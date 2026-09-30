"""GitHub 이슈 수집 — 워커가 소스마다 부르는 목록 폴링 (ADR-0014 결정 2·9, ARCHITECTURE "GitHub 업무 순환").

- webhook 없이 `GitHubClient.list_issues`(updated 오름차순) 만 쓴다. 최초 커서는 `since = start_at` 이다 — `intake: all_open`
  소스(ADR-0017)는 처음부터(`since` 없음) 받아 오래된 열린 이슈도 들어온다.
- 한 페이지의 이슈를 모두 저장(이슈마다 repo 트랜잭션)한 뒤에 커서를 다음 페이지로 넘긴다. 호출이 실패하거나 저장 도중
  멈추면 커서는 그 페이지에 남고, 다음 호출이 같은 페이지를 다시 받는다 — 다시 받은 이슈는 digest 가 같아 `unchanged` 다.
- 마지막 페이지 뒤 새 `since` 는 이번에 본 이슈의 가장 늦은 `updated_at`(포함 경계라 그 이슈는 다음에 한 번 더 온다).
  같은 요청을 다시 보낼 때만(since 그대로·1페이지) ETag 를 남겨 304 로 받는다.
- 새 이슈는 `domain.issue_intake.intake_scope` 범위 안만 업무 + 첫 단계 Task 로 만든다(ADR-0020). 종류·우선순위는
  워크스페이스 매핑 표가 라벨로 정하고, 종류 매핑이 없거나 등록되지 않은 종류면 가져오지 않는다(`unmapped`) — 이슈가
  바뀌면 다시 본다. 양식 칸은 본문의 절에서 읽는다(`domain.form_sections`).
  이미 받은 이슈는 범위·매핑과 무관하게 갱신한다 — 닫힘·재오픈·담당 변경은 원본 스냅샷·업무 원본 상태에, 제목·본문
  변경은 Task·업무 revision 에 반영된다(진행 중 실행의 입력은 그대로).
- `all_open` 소스에서 트리거 라벨이 붙은 이슈는 볼 때마다 실행 지시(`delegated_by=label`)를 기록한다 — 처음 한 번만
  남고 라벨을 떼도 지우지 않는다. 지시 전 Task 는 준비 판정의 `not_delegated` 로 기다린다.
- `selected_issue_numbers` 중 아직 받지 않은 이슈는 `get_issue` 로 따로 받는다(`since` 밖의 오래된 이슈도 명시적 선택이면).
- 댓글은 읽지 않는다 — 원본 댓글이 업무를 만들거나 명령이 되는 경로는 없다. GitHub 에 쓰지 않는다.
- 수집 뒤 닫혔지만 병합 시각을 모르는 이슈마다 그 이슈를 닫은 병합 PR 을 조회해 저장한다(ADR-0015 결정 9 — 도입 후 완료
  시각). 한 번에 `MAX_MERGE_CHECKS_PER_SYNC` 건까지. 병합 없음으로 확인한 이슈는 그 뒤 이슈가 바뀔 때만 다시 조회한다.
  조회 실패는 `merge_error` 에 남기고 이미 저장한 수집 결과는 그대로 둔다.
- 그 뒤 소스 저장소의 PR 을 읽어(`list_pulls`, 커서 = 본 PR 의 가장 늦은 `updated_at`) head 브랜치 → 제목 순으로 업무 키를
  찾아 감지 PR 로 붙인다(phase 16 PR 신호). Runloom 이 연 PR 은 붙이지 않는다. 실패는 `pull_error` 에 남기고 커서는 그대로다.
"""

import secrets
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from sqlite3 import Connection

from workflow.adapters import repo
from workflow.adapters.errors import NotFound
from workflow.adapters.github_client import (
    GitHubClient,
    GitHubError,
    GitHubNotFound,
    GitHubRateLimited,
    GitHubUnavailable,
    IssueCursor,
    PullSummary,
)
from workflow.contracts.github import GitHubIssueSnapshot, GitHubSourceConfig
from workflow.domain.form_sections import extract_form
from workflow.domain.issue_intake import (
    IntakeFacts,
    intake_facts,
    intake_scope,
    issue_kind,
    issue_priority,
    label_delegated,
    snapshot_to_task_spec,
)
from workflow.domain.work_keys import keys_in

# 한 번의 호출에서 넘길 최대 페이지 수. 남은 페이지는 저장된 커서로 다음 호출이 잇는다.
MAX_PAGES_PER_SYNC = 10
# rate limit 응답에 대기 시간이 없을 때 최소 대기(GitHub 권고)
DEFAULT_RETRY_AFTER_SECONDS = 60
# 한 번의 호출에서 병합 PR 을 조회할 최대 이슈 수. 남은 이슈는 다음 호출이 잇는다.
MAX_MERGE_CHECKS_PER_SYNC = 20


@dataclass
class SyncReport:
    """한 소스의 수집 결과. 로그·워커용이며 상태가 아니다(상태는 DB 의 커서·원본 매핑)."""

    source_id: str
    disabled: bool = False
    pages: int = 0
    not_modified: bool = False
    created: list[str] = field(default_factory=list)  # 새 Task
    updated: list[str] = field(default_factory=list)  # 원본 revision 이 오른 Task
    input_changed: list[str] = field(default_factory=list)  # 제목·요청이 바뀌어 Task revision 이 오른 Task(재평가 필요)
    unchanged: int = 0
    stale: int = 0
    skipped: dict[str, int] = field(default_factory=dict)  # 받지 않은 이슈 — 사유별 개수
    unmapped: int = 0  # 범위 안이지만 종류 매핑이 없어 받지 않은 이슈
    error: str | None = None
    retry_after_seconds: int | None = None
    merged: list[str] = field(default_factory=list)  # 병합 PR 을 새로 기록한 Task
    merge_error: str | None = None  # 병합 PR 조회 실패(마지막 것). 수집 결과는 그대로
    pulls_linked: list[str] = field(default_factory=list)  # 감지 PR 이 새로 붙은 업무
    pull_error: str | None = None  # PR 목록 읽기 실패. 수집 결과는 그대로


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _later(a: str | None, b: str) -> str:
    return b if a is None or _parse(b) > _parse(a) else a


class _Intake:
    def __init__(self, conn: Connection, session_id: str, config: GitHubSourceConfig, now: str, report: SyncReport):
        self.conn, self.session_id, self.config, self.now, self.report = conn, session_id, config, now, report
        self.tracked = {row["github_issue_id"]: row["task_id"]
                        for row in repo.list_source_issues(conn, session_id, config.source_id)}
        self.skipped: Counter[str] = Counter()
        self.mappings = repo.list_field_mappings(conn, session_id, "github")
        self.kinds = {spec.kind: spec for spec in repo.list_kinds(conn, session_id)}

    def take(self, snapshot: GitHubIssueSnapshot) -> None:
        tracked = self.tracked.get(snapshot.issue_id)
        if tracked is None:
            scope = intake_scope(self.config, snapshot)
            if not scope.accept:
                self.skipped[scope.reason] += 1
                return
            kind = self.kinds.get(issue_kind(self.mappings, snapshot))
            if kind is None:
                self.report.unmapped += 1
                return
        else:  # 이미 받은 이슈의 갱신은 제목·요청만 본다 — 종류는 첫 단계 그대로
            kind = self.kinds[repo.get_task(self.conn, tracked)["kind"]]
        task = snapshot_to_task_spec(self.config, snapshot, kind=kind, session_id=self.session_id,
                                     task_id=f"task-{secrets.token_hex(6)}")
        result = repo.upsert_source_issue(self.conn, self.session_id, self.config.source_id, snapshot, task=task,
                                          form=extract_form(snapshot.body).to_json(),
                                          priority=issue_priority(self.mappings, snapshot), now=self.now)
        self.tracked[snapshot.issue_id] = result.task_id
        if label_delegated(self.config, snapshot):
            repo.mark_issue_delegated(self.conn, session_id=self.session_id, source_id=self.config.source_id,
                                      github_issue_id=snapshot.issue_id, by="label", now=self.now)
        if result.action == "created":
            self.report.created.append(result.task_id)
        elif result.action == "updated":
            self.report.updated.append(result.task_id)
            if result.input_changed:
                self.report.input_changed.append(result.task_id)
        elif result.action == "unchanged":
            self.report.unchanged += 1
        else:
            self.report.stale += 1


def _fail(report: SyncReport, exc: GitHubError, now: str) -> None:
    report.error = str(exc)
    _back_off(report, exc, now)


def _back_off(report: SyncReport, exc: GitHubError, now: str) -> None:
    if isinstance(exc, GitHubRateLimited):
        wait = exc.retry_after_seconds
        if wait is None and exc.reset_epoch is not None:
            wait = max(0, exc.reset_epoch - int(_parse(now).timestamp()))
        report.retry_after_seconds = wait if wait is not None else DEFAULT_RETRY_AFTER_SECONDS


def _poll(client: GitHubClient, intake: _Intake, report: SyncReport) -> None:
    """목록 페이지를 따라가며 저장·커서 전진. 실패하면 `report.error` 만 남긴다(커서는 실패한 페이지에 남는다)."""
    config, conn, session_id, now = intake.config, intake.conn, intake.session_id, intake.now
    raw = repo.get_source_cursor(conn, session_id, config.source_id)
    cursor = IssueCursor.parse(raw) if raw else IssueCursor(since=None if config.intake == "all_open" else config.start_at)
    latest: str | None = None
    for _ in range(MAX_PAGES_PER_SYNC):
        try:
            page = client.list_issues(config.repository_full_name, cursor)
        except GitHubError as exc:
            _fail(report, exc, now)
            return
        if page.not_modified:
            report.not_modified = True
            return
        report.pages += 1
        intake.skipped["pull_request"] += page.skipped_pull_requests
        for snapshot in page.issues:
            intake.take(snapshot)
            latest = _later(latest, snapshot.updated_at)
        if page.next_cursor is not None:
            cursor = page.next_cursor
            repo.save_source_cursor(conn, session_id, config.source_id, cursor.to_str(), now)
            continue
        since = cursor.since if latest is None else _later(cursor.since, latest)
        same_request = since == cursor.since and cursor.page == 1
        done = IssueCursor(since=since, page=1, etag=page.etag if same_request else None)
        repo.save_source_cursor(conn, session_id, config.source_id, done.to_str(), now)
        return


def _selected(client: GitHubClient, intake: _Intake, report: SyncReport) -> None:
    tracked_numbers = {
        row["issue_number"] for row in repo.list_source_issues(intake.conn, intake.session_id, intake.config.source_id)
    }
    for number in intake.config.selected_issue_numbers:
        if number in tracked_numbers:
            continue
        try:
            snapshot = client.get_issue(intake.config.repository_full_name, number)
        except GitHubNotFound:
            intake.skipped["not_found"] += 1
            continue
        except GitHubError as exc:
            _fail(report, exc, intake.now)
            return
        intake.take(snapshot)


def _needs_merge_check(row) -> bool:
    checked = row["merge_checked_at"]
    return checked is None or _parse(row["issue_updated_at"]) > _parse(checked)


def _check_merges(client: GitHubClient, intake: _Intake, report: SyncReport) -> None:
    """닫힌 이슈의 병합 PR 을 조회·저장. rate limit·연결 실패는 이번 조회를 멈추고, 이슈 하나의 오류는 건너뛴다."""
    conn, session_id, config, now = intake.conn, intake.session_id, intake.config, intake.now
    rows = [r for r in repo.list_issues_needing_merge_check(conn, session_id, config.source_id) if _needs_merge_check(r)]
    for row in rows[:MAX_MERGE_CHECKS_PER_SYNC]:
        try:
            link = client.get_issue_pr_link(config.repository_full_name, row["issue_number"])
        except GitHubError as exc:
            report.merge_error = str(exc)
            if isinstance(exc, GitHubRateLimited | GitHubUnavailable):
                _back_off(report, exc, now)
                return
            continue
        repo.record_issue_merge(conn, session_id=session_id, source_id=config.source_id,
                                github_issue_id=row["github_issue_id"], link=link, now=now)
        if link is not None:
            report.merged.append(row["task_id"])


def _pull_target(intake: _Intake, pull: PullSummary) -> tuple[str, str] | None:
    """(업무 id, matched_in) — head 브랜치에서 이 워크스페이스에 있는 키를 먼저, 없으면 제목에서. 처음 것 하나."""
    for matched_in, text in (("head", pull.head_ref), ("title", pull.title)):
        for key_number in keys_in(text):
            work = repo.get_work_item_by_key(intake.conn, intake.session_id, key_number)
            if work is not None:
                return work["work_item_id"], matched_in
    return None


def _link_pulls(client: GitHubClient, intake: _Intake, report: SyncReport) -> None:
    """소스 저장소 PR → 업무 키 매칭 → 감지 PR 저장·업무 상태 재계산. 커서는 본 PR 을 모두 반영한 뒤에 옮긴다."""
    conn, session_id, config, now = intake.conn, intake.session_id, intake.config, intake.now
    repository = config.repository_full_name
    cursor = repo.get_pull_cursor(conn, config.source_id)
    try:
        pulls = client.list_pulls(repository, cursor)
    except GitHubError as exc:
        report.pull_error = str(exc)
        _back_off(report, exc, now)
        return
    latest = cursor
    for pull in pulls:
        latest = _later(latest, pull.updated_at)
        if repo.is_runloom_pull_request(conn, session_id, repository, pr_number=pull.number, head_branch=pull.head_ref):
            continue
        existing = repo.get_work_pull_request(conn, session_id, repository, pull.number)
        target = (existing["work_item_id"], "head") if existing is not None else _pull_target(intake, pull)
        if target is None:
            continue
        work_item_id, matched_in = target
        linked = repo.upsert_work_pull_request(
            conn, session_id=session_id, work_item_id=work_item_id, source_id=config.source_id,
            repository_full_name=repository, pr_number=pull.number, title=pull.title, head_branch=pull.head_ref,
            state="merged" if pull.merged_at is not None else pull.state, draft=pull.draft,
            author_login=pull.author_login, merged_at=pull.merged_at, pr_updated_at=pull.updated_at,
            matched_in=matched_in, now=now,
        )
        if linked:
            report.pulls_linked.append(work_item_id)
    if latest is not None and latest != cursor:
        repo.set_pull_cursor(conn, config.source_id, latest, now=now)


def sync_source(conn: Connection, client: GitHubClient, source_id: str, now: str) -> SyncReport:
    """소스 하나를 한 번 수집한다. GitHub 오류는 예외 대신 `report.error`(rate limit 이면 `retry_after_seconds`)로
    돌려주고, DB 오류는 그대로 올린다(커서가 넘어가지 않았으므로 다음 호출이 같은 페이지부터 다시 한다)."""
    report = SyncReport(source_id=source_id)
    session_id = repo.github_source_session(conn, source_id)
    if session_id is None:
        raise NotFound(f"source {source_id}")
    config = repo.get_github_source(conn, session_id, source_id)
    if not config.enabled:
        report.disabled = True
        return report
    intake = _Intake(conn, session_id, config, now, report)
    _poll(client, intake, report)
    if report.error is None:
        _selected(client, intake, report)
    if report.error is None:
        _check_merges(client, intake, report)
    if report.error is None and report.retry_after_seconds is None:
        _link_pulls(client, intake, report)
    report.skipped = {reason: count for reason, count in intake.skipped.items() if count}
    return report


def task_intake_facts(conn: Connection, session_id: str, task_id: str) -> IntakeFacts:
    """준비 판정의 담당·입력·원본 부분. 가져온 Task 와 직접 등록 Task 가 같은 함수를 거친다 — 원본 매핑이 있을 때만
    GitHub 담당·연결·원본 상태를 넣는다. 다른 세션의 Task 는 NotFound."""
    task = repo.get_task(conn, task_id)
    if task is None or task["session_id"] != session_id:
        raise NotFound(f"task {task_id}")
    source_issue = repo.get_source_issue_by_task(conn, session_id, task_id)
    if source_issue is None:
        return intake_facts(request=task["request"], run_mode=task["run_mode"], snapshot=None, bindings={},
                            max_rework_rounds=None)
    config = repo.get_github_source(conn, session_id, source_issue["source_id"])
    bindings = repo.list_assignee_bindings(conn, session_id, config.source_id)
    return intake_facts(
        request=task["request"],
        run_mode=task["run_mode"],
        snapshot=GitHubIssueSnapshot.model_validate_json(source_issue["snapshot_json"]),
        bindings={b.github_user_id: b.agent_id for b in bindings},
        max_rework_rounds=config.max_rework_rounds,
        needs_delegation=config.intake == "all_open",
        delegated_by=source_issue["delegated_by"],
    )
