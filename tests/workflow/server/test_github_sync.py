"""github_sync — GitHub 이슈 수집과 직접 등록의 같은 준비 판정 (phase 8 step 7, ADR-0014 결정 2·9).

실제 GitHub 을 부르지 않는다 — `FakeGitHub` 가 `GitHubClient` Protocol 을 흉내 내고(updated 오름차순·페이지·ETag),
댓글 API 는 부르면 실패한다(댓글은 업무를 만들지 않는다).
"""

import hashlib
import json

import pytest
from fastapi.testclient import TestClient

from workflow.adapters import repo
from workflow.adapters.github_client import (
    GitHubNotFound,
    GitHubRateLimited,
    GitHubUnavailable,
    IssueCursor,
    IssuePage,
    PullSummary,
)
from workflow.contracts.github import AssigneeBinding, GitHubIssueSnapshot, GitHubSourceConfig, IssuePrLink
from workflow.contracts.v1 import Capability, ExecutionRequest, KindSpec
from workflow.domain.field_mapping import MappingRow
from workflow.domain.selection import Candidate
from workflow.domain.task_readiness import ExecutorFacts, TaskFacts, evaluate_readiness
from workflow.server.auth import SELFHOST_SESSION_ID
from workflow.server.github_sync import MAX_MERGE_CHECKS_PER_SYNC, sync_source, task_intake_facts

from .conftest import session_of

SOURCE = "ghs-1a2b3c4d"
FIX_AGENT = "agent-fix"
START = "2026-10-06T00:00:00Z"
NOW = "2026-10-06T12:00:00Z"
ASSIGNEE = 5812345


class FakeGitHub:
    """updated_at 오름차순·`since` 이상·페이지 번호·목록 ETag 를 흉내 낸다. `failures` 는 list_issues 호출마다 하나씩 꺼낸다."""

    def __init__(self, per_page: int = 100):
        self.issues: dict[int, GitHubIssueSnapshot] = {}
        self.per_page = per_page
        self.failures: list[Exception | None] = []
        self.list_calls: list[IssueCursor | None] = []
        self.get_calls: list[int] = []
        self.links: dict[int, IssuePrLink] = {}  # 이슈 번호 → 그 이슈를 닫은 병합 PR. 없으면 병합 없음
        self.link_failures: dict[int, Exception] = {}
        self.link_calls: list[int] = []
        self.pulls: dict[int, PullSummary] = {}  # 소스 저장소 PR — 번호 → 요약
        self.pull_failures: list[Exception] = []
        self.pull_calls: list[str | None] = []

    def put(self, snapshot: GitHubIssueSnapshot) -> None:
        self.issues[snapshot.issue_id] = snapshot

    def list_issues(self, repo_name: str, cursor: IssueCursor | None) -> IssuePage:
        self.list_calls.append(cursor)
        if self.failures:
            failure = self.failures.pop(0)
            if failure is not None:
                raise failure
        cursor = cursor or IssueCursor()
        items = sorted(
            (i for i in self.issues.values() if cursor.since is None or i.updated_at >= cursor.since),
            key=lambda i: (i.updated_at, i.issue_id),
        )
        etag = '"' + hashlib.sha256(json.dumps([i.model_dump() for i in items]).encode()).hexdigest()[:12] + '"'
        if cursor.page == 1 and cursor.etag == etag:
            return IssuePage((), None, etag, True, 0)
        start = (cursor.page - 1) * self.per_page
        chunk = items[start:start + self.per_page]
        more = start + self.per_page < len(items)
        issues = tuple(i for i in chunk if not i.is_pull_request)
        return IssuePage(
            issues=issues,
            next_cursor=IssueCursor(since=cursor.since, page=cursor.page + 1) if more else None,
            etag=etag,
            not_modified=False,
            skipped_pull_requests=len(chunk) - len(issues),
        )

    def get_issue(self, repo_name: str, number: int) -> GitHubIssueSnapshot:
        self.get_calls.append(number)
        for snapshot in self.issues.values():
            if snapshot.number == number:
                return snapshot
        raise GitHubNotFound(f"GET /repos/{repo_name}/issues/{number}: HTTP 404")

    def get_issue_pr_link(self, repo_name: str, number: int) -> IssuePrLink | None:
        self.link_calls.append(number)
        if number in self.link_failures:
            raise self.link_failures[number]
        return self.links.get(number)

    def list_pulls(self, repo_name: str, cursor: str | None) -> list[PullSummary]:
        """갱신 내림차순, `updated_at > cursor` 만(상한은 흉내 내지 않는다)."""
        self.pull_calls.append(cursor)
        if self.pull_failures:
            raise self.pull_failures.pop(0)
        return sorted((p for p in self.pulls.values() if cursor is None or p.updated_at > cursor),
                      key=lambda p: (p.updated_at, p.number), reverse=True)

    def list_comments(self, *args, **kwargs):
        raise AssertionError("수집은 댓글을 읽지 않는다")

    def create_comment(self, *args, **kwargs):
        raise AssertionError("수집은 GitHub 에 쓰지 않는다")

    def update_comment(self, *args, **kwargs):
        raise AssertionError("수집은 GitHub 에 쓰지 않는다")


def issue(number: int, **overrides) -> GitHubIssueSnapshot:
    data = {
        "repository_id": 700112233,
        "repository_full_name": "acme/billing",
        "issue_id": 1000 + number,
        "number": number,
        "title": f"버그 {number}",
        "body": f"재현 절차 {number}",
        "state": "open",
        "labels": ["bug"],
        "assignee_ids": [ASSIGNEE],
        "assignee_logins": ["kim-dev"],
        "html_url": f"https://github.com/acme/billing/issues/{number}",
        "created_at": "2026-10-06T01:00:00Z",
        "updated_at": f"2026-10-06T02:{number:02d}:00Z",
        "is_pull_request": False,
    }
    return GitHubIssueSnapshot.model_validate({**data, **overrides})


def config(**overrides) -> GitHubSourceConfig:
    data = {
        "source_id": SOURCE,
        "repository_full_name": "acme/billing",
        "workflow_repository_id": "billing",
        "label_filter": ["bug"],
        "selected_issue_numbers": [],
        "start_at": START,
        "fix_verification_profile_id": "vp-pytest",
        "review_agent_id": FIX_AGENT,
        "run_mode": "auto",
        "max_rework_rounds": 1,
        "enabled": True,
        "config_revision": 1,
    }
    return GitHubSourceConfig.model_validate({**data, **overrides})


@pytest.fixture
def session_id(logged_in_client, conn) -> str:
    """로그인한 워크스페이스(직접 등록에도 쓴다) + 수정 Agent 등록 + 소스 + 담당 연결."""
    assert logged_in_client.get("/tasks").status_code == 200
    sid = session_of(logged_in_client)
    assert sid == SELFHOST_SESSION_ID
    repo.upsert_agent(conn, {
        "agent_id": FIX_AGENT, "name": "수정", "owner_scope": "personal", "connection_type": "local",
        "local_registration_id": "local-billing",
        "capabilities": [{"code": "code.fix", "scope": {"repository_id": "billing"}}],
        "verification_profile_ids": ["vp-pytest"],
    })
    repo.register_session_agent(conn, sid, FIX_AGENT, NOW)
    repo.save_github_source(conn, sid, config(), NOW)
    repo.bind_assignee(conn, sid, AssigneeBinding(source_id=SOURCE, github_user_id=ASSIGNEE,
                                                  github_login="kim-dev", agent_id=FIX_AGENT), NOW)
    return sid


def reconfigure(conn, sid: str, **overrides) -> None:
    repo.save_github_source(conn, sid, config(**overrides), NOW)


def tasks_by_ref(conn, sid: str) -> dict[str, dict]:
    return {t["source_ref"]: dict(t) for t in repo.list_tasks(conn, sid) if t["source_ref"]}


def cursor(conn, sid: str) -> IssueCursor | None:
    raw = repo.get_source_cursor(conn, sid, SOURCE)
    return IssueCursor.parse(raw) if raw else None


# --- 범위 ---


def test_first_sync_starts_at_cutoff_and_accepts_only_new_labelled_open_issues(conn, session_id):
    gh = FakeGitHub()
    gh.put(issue(1))
    gh.put(issue(2, labels=[]))
    gh.put(issue(3, created_at="2026-10-01T00:00:00Z"))  # 시작 전부터 있던 백로그 — 최근 수정돼도 받지 않음
    gh.put(issue(4, is_pull_request=True))
    gh.put(issue(5, state="closed"))

    report = sync_source(conn, gh, SOURCE, NOW)

    assert gh.list_calls[0] == IssueCursor(since=START, page=1, etag=None)  # 최초 cutoff = start_at
    assert report.error is None and report.pages == 1
    assert report.skipped == {"label_mismatch": 1, "before_start": 1, "pull_request": 1, "closed": 1}
    tasks = tasks_by_ref(conn, session_id)
    assert list(tasks) == ["acme/billing#1"]
    task = tasks["acme/billing#1"]
    assert report.created == [task["task_id"]]
    assert (task["kind"], task["title"], task["request"], task["run_mode"]) == ("bug_fix", "버그 1", "재현 절차 1", "auto")
    assert task["finished_at"] is None
    # 다음 폴링은 본 이슈 중 가장 늦은 updated_at 부터
    assert cursor(conn, session_id).since == "2026-10-06T02:05:00Z"


def test_disabled_source_is_not_polled(conn, session_id):
    reconfigure(conn, session_id, enabled=False)
    gh = FakeGitHub()
    gh.put(issue(1))
    report = sync_source(conn, gh, SOURCE, NOW)
    assert report.disabled and gh.list_calls == [] and tasks_by_ref(conn, session_id) == {}


def test_explicitly_selected_backlog_issue_is_imported_but_pull_request_is_not(conn, session_id):
    reconfigure(conn, session_id, label_filter=[], selected_issue_numbers=[7, 8, 9])
    gh = FakeGitHub()
    gh.put(issue(7, labels=[], created_at="2025-01-01T00:00:00Z", updated_at="2025-02-01T00:00:00Z"))  # since 밖
    gh.put(issue(8, is_pull_request=True, updated_at="2025-02-01T00:00:00Z"))
    gh.put(issue(1))  # 라벨 필터가 없으면 고르지 않은 이슈는 받지 않는다

    report = sync_source(conn, gh, SOURCE, NOW)

    assert sorted(gh.get_calls) == [7, 8, 9]
    assert report.skipped == {"not_selected": 1, "pull_request": 1, "not_found": 1}
    assert list(tasks_by_ref(conn, session_id)) == ["acme/billing#7"]

    gh.get_calls.clear()
    sync_source(conn, gh, SOURCE, NOW)
    assert 7 not in gh.get_calls  # 이미 받은 선택 이슈는 목록 폴링으로만 갱신한다
    assert list(tasks_by_ref(conn, session_id)) == ["acme/billing#7"]


# --- 재접수·페이지 ---


def test_resync_does_not_duplicate_and_uses_etag(conn, session_id):
    gh = FakeGitHub()
    gh.put(issue(1))
    sync_source(conn, gh, SOURCE, NOW)

    again = sync_source(conn, gh, SOURCE, NOW)  # since 는 포함 경계 — 같은 이슈가 다시 와도 새 Task 없음
    assert (again.unchanged, again.created, again.not_modified) == (1, [], False)
    third = sync_source(conn, gh, SOURCE, NOW)  # 같은 요청이 반복되면 ETag 로 304
    assert third.not_modified and third.created == []
    assert gh.list_calls[-1].etag is not None

    gh.put(issue(2))  # 새 이슈가 생기면 목록이 바뀐다 — 경계의 #1 은 그대로 흡수
    fourth = sync_source(conn, gh, SOURCE, NOW)
    assert fourth.unchanged == 1 and len(fourth.created) == 1
    assert sorted(tasks_by_ref(conn, session_id)) == ["acme/billing#1", "acme/billing#2"]


def test_page_failure_keeps_cursor_and_retry_resumes_without_duplicates(conn, session_id):
    gh = FakeGitHub(per_page=2)
    for n in range(1, 6):
        gh.put(issue(n))
    gh.failures = [None, GitHubUnavailable("GET /repos/acme/billing/issues: HTTP 502")]

    first = sync_source(conn, gh, SOURCE, NOW)
    assert first.error == "GET /repos/acme/billing/issues: HTTP 502"
    assert len(first.created) == 2 and first.pages == 1
    assert cursor(conn, session_id) == IssueCursor(since=START, page=2, etag=None)  # 실패한 페이지를 가리킨다

    gh.failures = [GitHubRateLimited("GET /repos/acme/billing/issues: HTTP 429", 30, None)]
    limited = sync_source(conn, gh, SOURCE, NOW)
    assert (limited.retry_after_seconds, limited.created) == (30, [])
    assert cursor(conn, session_id).page == 2

    retry = sync_source(conn, gh, SOURCE, NOW)
    assert retry.error is None and len(retry.created) == 3
    assert [c.page for c in gh.list_calls[-2:]] == [2, 3]  # 실패한 페이지부터 다시
    assert len(tasks_by_ref(conn, session_id)) == 5
    assert cursor(conn, session_id) == IssueCursor(since="2026-10-06T02:05:00Z", page=1, etag=None)


def test_crash_between_page_and_cursor_reprocesses_page_without_duplicates(conn, session_id, monkeypatch):
    gh = FakeGitHub(per_page=2)
    for n in range(1, 4):
        gh.put(issue(n))
    monkeypatch.setattr(repo, "save_source_cursor", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("crash")))
    with pytest.raises(RuntimeError):
        sync_source(conn, gh, SOURCE, NOW)
    monkeypatch.undo()
    assert cursor(conn, session_id) is None
    report = sync_source(conn, gh, SOURCE, NOW)
    assert report.unchanged == 2 and len(report.created) == 1
    assert len(tasks_by_ref(conn, session_id)) == 3


# --- 원본 변경 ---


def test_issue_edit_raises_task_revision_but_assignee_change_does_not(conn, session_id):
    gh = FakeGitHub()
    gh.put(issue(1))
    sync_source(conn, gh, SOURCE, NOW)
    task_id = tasks_by_ref(conn, session_id)["acme/billing#1"]["task_id"]

    gh.put(issue(1, body="재현 절차 보강", updated_at="2026-10-06T03:00:00Z"))
    edited = sync_source(conn, gh, SOURCE, NOW)
    assert (edited.updated, edited.input_changed) == ([task_id], [task_id])
    task = repo.get_task(conn, task_id)
    assert (task["request"], task["revision"]) == ("재현 절차 보강", 2)

    gh.put(issue(1, body="재현 절차 보강", assignee_ids=[ASSIGNEE, 42], assignee_logins=["kim-dev", "lee"],
                 labels=["bug", "p1"], updated_at="2026-10-06T03:10:00Z"))
    reassigned = sync_source(conn, gh, SOURCE, NOW)
    assert (reassigned.updated, reassigned.input_changed) == ([task_id], [])
    assert repo.get_task(conn, task_id)["revision"] == 2
    facts = task_intake_facts(conn, session_id, task_id)
    assert facts.assignee_ids == (ASSIGNEE, 42)  # 준비 판정은 최신 담당을 본다


def test_tracked_issue_keeps_updating_after_leaving_label_scope(conn, session_id):
    gh = FakeGitHub()
    gh.put(issue(1))
    sync_source(conn, gh, SOURCE, NOW)
    gh.put(issue(1, labels=[], title="라벨 제거", updated_at="2026-10-06T03:00:00Z"))
    report = sync_source(conn, gh, SOURCE, NOW)
    assert len(report.input_changed) == 1 and report.skipped == {}
    assert tasks_by_ref(conn, session_id)["acme/billing#1"]["title"] == "라벨 제거"


def test_close_and_reopen_only_change_source_state(conn, session_id):
    gh = FakeGitHub()
    gh.put(issue(1))
    sync_source(conn, gh, SOURCE, NOW)
    task_id = tasks_by_ref(conn, session_id)["acme/billing#1"]["task_id"]

    gh.put(issue(1, state="closed", updated_at="2026-10-06T03:00:00Z"))
    sync_source(conn, gh, SOURCE, NOW)
    assert task_intake_facts(conn, session_id, task_id).source_state == "closed"
    task = repo.get_task(conn, task_id)
    assert (task["finished_at"], task["revision"]) == (None, 1)  # 닫힘은 대기 사유일 뿐 마감이 아니다

    gh.put(issue(1, state="open", updated_at="2026-10-06T04:00:00Z"))
    sync_source(conn, gh, SOURCE, NOW)
    assert task_intake_facts(conn, session_id, task_id).source_state == "open"
    assert len(tasks_by_ref(conn, session_id)) == 1  # 재오픈이 새 Task 를 만들지 않는다


def test_edit_during_execution_keeps_fixed_input_and_records_new_revision(conn, session_id):
    gh = FakeGitHub()
    gh.put(issue(1))
    sync_source(conn, gh, SOURCE, NOW)
    task_id = tasks_by_ref(conn, session_id)["acme/billing#1"]["task_id"]
    request = ExecutionRequest.model_validate({
        "contract_version": 1, "execution_id": "exec-1", "task_id": task_id, "kind": "bug_fix",
        "agent_id": FIX_AGENT, "task_revision": 1, "request": "재현 절차 1", "input_artifact_ids": [],
        "target": {"local_registration_id": "local-billing", "base_commit": "a" * 40,
                   "verification_profile_id": "vp-pytest"},
    })
    repo.create_execution(conn, execution_id="exec-1", task_id=task_id, attempt_no=1, start_key="auto:x:r1",
                          agent_id=FIX_AGENT, kind="bug_fix", request=request, assigned_connector_id=None,
                          predecessor_execution_id=None, now=NOW)

    gh.put(issue(1, body="실행 중 편집", updated_at="2026-10-06T03:00:00Z"))
    report = sync_source(conn, gh, SOURCE, NOW)

    assert report.input_changed == [task_id]
    execution = repo.get_execution(conn, "exec-1")
    assert ExecutionRequest.model_validate_json(execution["request_json"]) == request  # 실행 입력은 고정
    # 재평가 필요: Task revision 이 실행의 task_revision 보다 크다
    assert repo.get_task(conn, task_id)["revision"] == 2 > request.task_revision


def test_own_comment_bumping_updated_at_creates_no_work(conn, session_id):
    """원본 댓글은 스냅샷에 없다 — 댓글로 updated_at 만 바뀐 이슈는 입력 변경도 새 업무도 아니다."""
    gh = FakeGitHub()
    gh.put(issue(1))
    sync_source(conn, gh, SOURCE, NOW)
    gh.put(issue(1, updated_at="2026-10-06T03:00:00Z"))
    report = sync_source(conn, gh, SOURCE, NOW)
    assert report.created == [] and report.input_changed == []
    assert len(repo.list_tasks(conn, session_id)) == 1


# --- 직접 등록과 같은 준비 판정 ---


def _readiness(conn, sid: str, task_id: str):
    required = Capability(code="code.fix", scope={"repository_id": "billing"})
    executor = ExecutorFacts(agent_id=FIX_AGENT, connector_id="con-1", repository_id="billing",
                             connection_type="local", connection_state="online", last_seen_at=NOW,
                             supported_kinds=("bug_fix", "code_review"))
    return evaluate_readiness(TaskFacts(
        task_id=task_id, kind="bug_fix", now=NOW, offline_after_seconds=45, required=required,
        candidates=[Candidate(agent_id=FIX_AGENT, capabilities=(required,))], executors={FIX_AGENT: executor},
        **task_intake_facts(conn, sid, task_id).as_kwargs(),
    ))


def test_direct_registration_matches_imported_task_and_readiness(client: TestClient, conn, session_id):
    gh = FakeGitHub()
    gh.put(issue(1))
    sync_source(conn, gh, SOURCE, NOW)
    imported = tasks_by_ref(conn, session_id)["acme/billing#1"]

    response = client.post("/tasks", data={
        "title": "버그 1", "request": "재현 절차 1", "capability_code": "code.fix", "scope_value": "billing",
        "selection_mode": "auto", "run_mode": "auto", "completion_mode": "review",
    }, follow_redirects=False)
    assert response.status_code == 303, response.text
    direct = dict(repo.get_task(conn, response.headers["location"].rsplit("/", 1)[-1]))

    keys = ("kind", "required_capability_json", "run_mode", "completion_mode", "criteria_json", "title", "request")
    assert {k: imported[k] for k in keys} == {k: direct[k] for k in keys}

    a, b = _readiness(conn, session_id, imported["task_id"]), _readiness(conn, session_id, direct["task_id"])
    assert (a.ready, a.agent_id, a.blockers) == (b.ready, b.agent_id, b.blockers) == (True, FIX_AGENT, ())
    assert task_intake_facts(conn, session_id, direct["task_id"]).assignee_ids is None


def test_imported_task_without_bound_assignee_waits(conn, session_id):
    gh = FakeGitHub()
    gh.put(issue(1, assignee_ids=[], assignee_logins=[]))
    gh.put(issue(2, assignee_ids=[77], assignee_logins=["someone"]))
    sync_source(conn, gh, SOURCE, NOW)
    tasks = tasks_by_ref(conn, session_id)
    codes = {ref: [x.code for x in _readiness(conn, session_id, t["task_id"]).blockers] for ref, t in tasks.items()}
    assert codes == {"acme/billing#1": ["assignee_missing"], "acme/billing#2": ["assignee_unbound"]}


def test_task_intake_facts_hides_other_session_tasks(conn, session_id):
    gh = FakeGitHub()
    gh.put(issue(1))
    sync_source(conn, gh, SOURCE, NOW)
    task_id = tasks_by_ref(conn, session_id)["acme/billing#1"]["task_id"]
    repo.create_session(conn, "sess-other", NOW)
    with pytest.raises(repo.NotFound):
        task_intake_facts(conn, "sess-other", task_id)


# --- 병합 PR 조회 (phase 9 step 12, ADR-0015 결정 9) ---


def merged(number: int, pr: int = 90, at: str = "2026-10-06T05:00:00Z") -> IssuePrLink:
    return IssuePrLink(issue_number=number, issue_title=f"버그 {number}", issue_opened_at="2026-10-06T01:00:00Z",
                       pr_number=pr, pr_merged_at=at)


def source_issue(conn, sid: str, number: int) -> dict:
    return next(dict(r) for r in repo.list_source_issues(conn, sid, SOURCE) if r["issue_number"] == number)


def test_closed_issue_merge_is_looked_up_and_stored(conn, session_id):
    gh = FakeGitHub()
    gh.put(issue(1))
    gh.put(issue(2))
    sync_source(conn, gh, SOURCE, NOW)
    assert gh.link_calls == []  # 열린 이슈는 조회하지 않는다

    gh.put(issue(1, state="closed", updated_at="2026-10-06T05:00:00Z"))
    gh.links[1] = merged(1, pr=91)
    report = sync_source(conn, gh, SOURCE, "2026-10-06T13:00:00Z")
    assert gh.link_calls == [1]
    row = source_issue(conn, session_id, 1)
    assert (row["merged_pr_number"], row["pr_merged_at"], row["merge_checked_at"]) == \
        (91, "2026-10-06T05:00:00Z", "2026-10-06T13:00:00Z")
    assert report.merged == [row["task_id"]] and report.merge_error is None

    sync_source(conn, gh, SOURCE, "2026-10-06T14:00:00Z")
    assert gh.link_calls == [1]  # 병합을 안 뒤에는 다시 조회하지 않는다


def test_merge_lookups_are_capped_per_sync(conn, session_id):
    gh = FakeGitHub()
    count = MAX_MERGE_CHECKS_PER_SYNC + 3
    for n in range(1, count + 1):
        gh.put(issue(n, state="open", updated_at=f"2026-10-06T02:{n:02d}:00Z"))
    sync_source(conn, gh, SOURCE, NOW)
    for n in range(1, count + 1):
        gh.put(issue(n, state="closed", updated_at=f"2026-10-06T03:{n:02d}:00Z"))
    sync_source(conn, gh, SOURCE, "2026-10-06T13:00:00Z")
    assert gh.link_calls == list(range(1, MAX_MERGE_CHECKS_PER_SYNC + 1))

    sync_source(conn, gh, SOURCE, "2026-10-06T14:00:00Z")
    assert gh.link_calls[MAX_MERGE_CHECKS_PER_SYNC:] == list(range(MAX_MERGE_CHECKS_PER_SYNC + 1, count + 1))


def test_merge_lookup_failure_keeps_intake_and_is_reported(conn, session_id):
    gh = FakeGitHub()
    gh.put(issue(1, state="closed"))
    gh.put(issue(2, state="closed"))
    gh.put(issue(3, state="closed"))
    reconfigure(conn, session_id, selected_issue_numbers=[1, 2, 3])
    gh.link_failures[1] = GitHubUnavailable("GitHub 연결 실패")
    report = sync_source(conn, gh, SOURCE, NOW)
    assert len(report.created) == 3  # 수집은 그대로 남는다
    assert len(tasks_by_ref(conn, session_id)) == 3
    assert report.error is None and report.merge_error is not None
    assert gh.link_calls == [1]  # 연결 실패는 이번 조회를 멈춘다
    assert source_issue(conn, session_id, 1)["merge_checked_at"] is None

    gh.link_failures = {1: GitHubNotFound("이슈 없음")}
    gh.links[3] = merged(3)
    report = sync_source(conn, gh, SOURCE, "2026-10-06T13:00:00Z")
    assert gh.link_calls[1:] == [1, 2, 3]  # 한 이슈의 오류는 나머지 조회를 막지 않는다
    assert report.merge_error is not None
    assert source_issue(conn, session_id, 3)["pr_merged_at"] == "2026-10-06T05:00:00Z"


def test_merge_lookup_rate_limit_backs_off(conn, session_id):
    gh = FakeGitHub()
    gh.put(issue(1, state="closed"))
    reconfigure(conn, session_id, selected_issue_numbers=[1])
    gh.link_failures[1] = GitHubRateLimited("한도", retry_after_seconds=120, reset_epoch=None)
    report = sync_source(conn, gh, SOURCE, NOW)
    assert report.retry_after_seconds == 120 and report.merge_error is not None
    assert len(report.created) == 1


def test_no_merge_is_not_rechecked_until_issue_changes(conn, session_id):
    gh = FakeGitHub()
    gh.put(issue(1))
    sync_source(conn, gh, SOURCE, NOW)
    gh.put(issue(1, state="closed", updated_at="2026-10-06T05:00:00Z"))
    sync_source(conn, gh, SOURCE, "2026-10-06T13:00:00Z")
    assert gh.link_calls == [1]
    row = source_issue(conn, session_id, 1)
    assert (row["pr_merged_at"], row["merge_checked_at"]) == (None, "2026-10-06T13:00:00Z")

    sync_source(conn, gh, SOURCE, "2026-10-06T14:00:00Z")
    assert gh.link_calls == [1]  # 병합 없음을 확인했고 이슈가 그대로 — 다시 조회하지 않는다

    gh.put(issue(1, state="closed", title="고침", updated_at="2026-10-06T15:00:00Z"))
    gh.links[1] = merged(1, at="2026-10-06T14:30:00Z")
    sync_source(conn, gh, SOURCE, "2026-10-06T16:00:00Z")
    assert gh.link_calls == [1, 1]  # 확인 뒤 이슈가 바뀌었다 — 다시 조회
    assert source_issue(conn, session_id, 1)["pr_merged_at"] == "2026-10-06T14:30:00Z"


# --- all_open 소스: 열린 이슈 전부 + 트리거 라벨 (phase 11 step 5, ADR-0017) ---


def open_all(conn, sid: str, **overrides) -> None:
    reconfigure(conn, sid, **{"intake": "all_open", "label_filter": [], "trigger_label": "runloom", **overrides})


def codes_by_ref(conn, sid: str) -> dict[str, list[str]]:
    return {ref: [b.code for b in _readiness(conn, sid, t["task_id"]).blockers]
            for ref, t in tasks_by_ref(conn, sid).items()}


def test_all_open_imports_every_open_issue_from_the_whole_history(conn, session_id):
    open_all(conn, session_id)
    gh = FakeGitHub()
    gh.put(issue(1, labels=[]))
    gh.put(issue(2, labels=["ui"], created_at="2024-01-01T00:00:00Z", updated_at="2024-02-01T00:00:00Z"))  # 오래된 백로그
    gh.put(issue(3, is_pull_request=True))
    gh.put(issue(4, state="closed"))

    report = sync_source(conn, gh, SOURCE, NOW)

    assert gh.list_calls[0] == IssueCursor(since=None, page=1, etag=None)  # start_at 이 아니라 처음부터
    assert report.error is None
    assert report.skipped == {"pull_request": 1, "closed": 1}
    assert sorted(tasks_by_ref(conn, session_id)) == ["acme/billing#1", "acme/billing#2"]

    again = sync_source(conn, gh, SOURCE, NOW)
    assert again.created == [] and sorted(tasks_by_ref(conn, session_id)) == ["acme/billing#1", "acme/billing#2"]


def test_all_open_closing_a_tracked_issue_only_updates_source_state(conn, session_id):
    open_all(conn, session_id)
    gh = FakeGitHub()
    gh.put(issue(1, labels=[]))
    sync_source(conn, gh, SOURCE, NOW)
    gh.put(issue(1, labels=[], state="closed", updated_at="2026-10-06T03:00:00Z"))
    report = sync_source(conn, gh, SOURCE, NOW)
    assert report.created == [] and len(report.updated) == 1
    assert source_issue(conn, session_id, 1)["state"] == "closed"


def test_trigger_label_delegates_and_unlabelled_issue_waits(conn, session_id):
    open_all(conn, session_id)
    gh = FakeGitHub()
    gh.put(issue(1, labels=["bug", "RunLoom"]))
    gh.put(issue(2, labels=["bug"]))
    sync_source(conn, gh, SOURCE, NOW)

    assert codes_by_ref(conn, session_id) == {"acme/billing#1": [], "acme/billing#2": ["not_delegated"]}
    assert (source_issue(conn, session_id, 1)["delegated_by"], source_issue(conn, session_id, 1)["delegated_at"]) == (
        "label", NOW)
    assert source_issue(conn, session_id, 2)["delegated_at"] is None


def test_label_added_later_switches_to_auto_and_removing_it_does_not_undo(conn, session_id):
    open_all(conn, session_id)
    gh = FakeGitHub()
    gh.put(issue(1, labels=[]))
    sync_source(conn, gh, SOURCE, NOW)
    assert codes_by_ref(conn, session_id) == {"acme/billing#1": ["not_delegated"]}

    gh.put(issue(1, labels=["runloom"], updated_at="2026-10-06T03:00:00Z"))
    sync_source(conn, gh, SOURCE, "2026-10-06T13:00:00Z")
    assert codes_by_ref(conn, session_id) == {"acme/billing#1": []}

    gh.put(issue(1, labels=[], updated_at="2026-10-06T04:00:00Z"))
    sync_source(conn, gh, SOURCE, "2026-10-06T14:00:00Z")
    assert codes_by_ref(conn, session_id) == {"acme/billing#1": []}
    assert source_issue(conn, session_id, 1)["delegated_at"] == "2026-10-06T13:00:00Z"


def test_label_delegation_still_honours_manual_run_mode(conn, session_id):
    open_all(conn, session_id, run_mode="manual")
    gh = FakeGitHub()
    gh.put(issue(1, labels=["runloom"]))
    sync_source(conn, gh, SOURCE, NOW)
    assert codes_by_ref(conn, session_id) == {"acme/billing#1": ["manual_mode"]}


def test_filtered_source_ignores_trigger_label_and_needs_no_delegation(conn, session_id):
    reconfigure(conn, session_id, trigger_label="runloom")
    gh = FakeGitHub()
    gh.put(issue(1))
    sync_source(conn, gh, SOURCE, NOW)
    assert gh.list_calls[0] == IssueCursor(since=START, page=1, etag=None)
    assert codes_by_ref(conn, session_id) == {"acme/billing#1": []}
    assert source_issue(conn, session_id, 1)["delegated_at"] is None


# --- 업무 + 첫 단계, 매핑 표, 양식 칸 (phase 14 step 5, ADR-0020) ---

DOCS = KindSpec(kind="write_docs", label="문서 작성", capability_code="docs.write", scope_key="repository_id",
                input_kinds=[], output_kind="generic_result", outcomes=["done"], instructions="", builtin=False)
FORM_BODY = "### 목표\n\n쿠폰은 한 번만\n\n### 기대 동작\n\n_No response_\n"


def mappings(conn, sid: str, *rows: tuple[str, str, str]) -> None:
    repo.replace_field_mappings(conn, sid, [MappingRow("github", field, value, result, position)
                                            for position, (field, value, result) in enumerate(rows, start=1)], now=NOW)


def test_new_issue_becomes_work_item_with_first_stage_form_and_priority(conn, session_id):
    mappings(conn, session_id, ("kind", "*", "bug_fix"), ("priority", "P1", "high"))
    gh = FakeGitHub()
    gh.put(issue(1, body=FORM_BODY, labels=["bug", "p1"]))
    sync_source(conn, gh, SOURCE, NOW)

    (work,) = repo.list_work_items(conn, session_id)
    (stage,) = repo.list_work_item_tasks(conn, work["work_item_id"])
    assert (stage["kind"], stage["source_ref"]) == ("bug_fix", "acme/billing#1")
    assert (work["kind"], work["priority"], work["source_type"], work["source_key"]) == (
        "bug_fix", "high", "github", "acme/billing#1")
    assert (work["assignee_type"], work["status"]) == (None, "새로 들어옴")
    assert json.loads(work["form_json"]) == {"goal": {"value": "쿠폰은 한 번만", "source": "github_body:### 목표"}}
    assert stage["request"] == FORM_BODY.strip()  # 요청은 본문 그대로


def test_label_mapping_picks_another_registered_kind(conn, session_id):
    repo.insert_kind(conn, session_id, DOCS, NOW)
    mappings(conn, session_id, ("kind", "docs", "write_docs"), ("kind", "*", "bug_fix"))
    gh = FakeGitHub()
    gh.put(issue(1, labels=["bug", "Docs"]))
    gh.put(issue(2))
    sync_source(conn, gh, SOURCE, NOW)

    tasks = tasks_by_ref(conn, session_id)
    assert tasks["acme/billing#1"]["kind"] == "write_docs"
    assert json.loads(tasks["acme/billing#1"]["required_capability_json"]) == {
        "code": "docs.write", "scope": {"repository_id": "billing"}}
    assert tasks["acme/billing#2"]["kind"] == "bug_fix"
    assert {w["kind"] for w in repo.list_work_items(conn, session_id)} == {"write_docs", "bug_fix"}


def test_unmapped_issue_is_not_imported_until_it_maps(conn, session_id):
    """종류 매핑이 없으면 업무도 단계도 만들지 않는다(`work_items.kind` 는 등록 종류 필수, 기본 종류 없음) — 이슈가
    바뀌어 매핑되면 다음 수집이 가져온다."""
    repo.insert_kind(conn, session_id, DOCS, NOW)
    mappings(conn, session_id, ("kind", "docs", "write_docs"))
    gh = FakeGitHub()
    gh.put(issue(1))
    report = sync_source(conn, gh, SOURCE, NOW)
    assert (report.unmapped, report.created) == (1, [])
    assert repo.list_work_items(conn, session_id) == [] and tasks_by_ref(conn, session_id) == {}

    gh.put(issue(1, labels=["bug", "docs"], updated_at="2026-10-06T03:00:00Z"))
    report = sync_source(conn, gh, SOURCE, NOW)
    assert (report.unmapped, len(report.created)) == (0, 1)
    assert tasks_by_ref(conn, session_id)["acme/billing#1"]["kind"] == "write_docs"


def test_body_edit_updates_work_item_form_and_revision(conn, session_id):
    gh = FakeGitHub()
    gh.put(issue(1))
    sync_source(conn, gh, SOURCE, NOW)
    (work,) = repo.list_work_items(conn, session_id)
    assert (work["revision"], work["form_json"]) == (1, "{}")

    gh.put(issue(1, title="새 제목", body=FORM_BODY, updated_at="2026-10-06T03:00:00Z"))
    sync_source(conn, gh, SOURCE, NOW)
    work = repo.get_work_item(conn, session_id, work["work_item_id"])
    assert (work["title"], work["request"], work["revision"]) == ("새 제목", FORM_BODY.strip(), 2)
    assert set(json.loads(work["form_json"])) == {"goal"}

    gh.put(issue(1, title="새 제목", body=FORM_BODY, state="closed", updated_at="2026-10-06T03:10:00Z"))
    sync_source(conn, gh, SOURCE, NOW)
    work = repo.get_work_item(conn, session_id, work["work_item_id"])
    assert (work["source_state"], work["revision"]) == ("closed", 2)


# --- PR 신호 (phase 16 step 9 — ARCHITECTURE "업무 화면 — phase 16" PR 신호) ---


def pull(number: int, *, head: str = "feature/x", title: str = "고침", state: str = "open",
         merged_at: str | None = None, at: str = "2026-10-06T03:00:00Z") -> PullSummary:
    return PullSummary(number=number, title=title, head_ref=head, state=state, draft=False, merged_at=merged_at,
                       author_login="kim-dev", updated_at=at)


def imported_work(conn, sid: str, gh: FakeGitHub, number: int = 1) -> str:
    """이슈를 받아 업무(RUN-n)를 만든다. PR 대역은 비워 둔다."""
    gh.put(issue(number))
    sync_source(conn, gh, SOURCE, NOW)
    return repo.work_item_of_task(conn, tasks_by_ref(conn, sid)[f"acme/billing#{number}"]["task_id"])["work_item_id"]


def detected(conn, work_item_id: str) -> list[dict]:
    return [dict(r) for r in repo.list_work_pull_requests(conn, work_item_id)]


def work_events(conn, work_item_id: str, type: str) -> list[dict]:
    return [json.loads(r["data_json"]) for r in conn.execute(
        "SELECT data_json FROM work_item_events WHERE work_item_id = ? AND type = ? ORDER BY id", (work_item_id, type))]


def work_status_of(conn, sid: str, work_item_id: str) -> tuple[str, str]:
    row = repo.get_work_item(conn, sid, work_item_id)
    return row["status"], row["status_reason"]


def test_pr_with_key_in_head_branch_is_linked_to_work(conn, session_id):
    gh = FakeGitHub()
    work = imported_work(conn, session_id, gh)
    gh.pulls[7] = pull(7, head="RUN-1-url-filter", title="URL 필터 <b>고침</b>")

    report = sync_source(conn, gh, SOURCE, NOW)

    assert report.pulls_linked == [work] and report.pull_error is None
    (row,) = detected(conn, work)
    assert (row["pr_number"], row["title"], row["head_branch"], row["state"], row["pr_url"]) == (
        7, "URL 필터 <b>고침</b>", "RUN-1-url-filter", "open", "https://github.com/acme/billing/pull/7")
    assert work_events(conn, work, "pull_request_linked") == [
        {"repository_full_name": "acme/billing", "pr_number": 7, "head_branch": "RUN-1-url-filter",
         "matched_in": "head"}]
    assert work_status_of(conn, session_id, work) == ("PR · 검토", "PR 확인 — #7")


def test_pr_with_key_only_in_title_is_linked_and_head_wins_over_title(conn, session_id):
    gh = FakeGitHub()
    first = imported_work(conn, session_id, gh, 1)
    second = imported_work(conn, session_id, gh, 2)
    gh.pulls[7] = pull(7, head="fix/coupon", title="fix: run-2 쿠폰")
    gh.pulls[8] = pull(8, head="RUN-1-a", title="RUN-2 도 같이", at="2026-10-06T03:01:00Z")
    gh.pulls[9] = pull(9, head="RUN-99-gone", title="RUN-2 제목", at="2026-10-06T03:02:00Z")  # head 키가 없는 업무면 제목

    sync_source(conn, gh, SOURCE, NOW)

    assert [r["pr_number"] for r in detected(conn, first)] == [8]
    assert sorted(r["pr_number"] for r in detected(conn, second)) == [7, 9]
    assert [e["matched_in"] for e in work_events(conn, second, "pull_request_linked")] == ["title", "title"]


def test_pr_without_known_key_is_ignored(conn, session_id):
    gh = FakeGitHub()
    work = imported_work(conn, session_id, gh)
    gh.pulls[7] = pull(7, head="feature/x", title="키 없음")
    gh.pulls[8] = pull(8, head="RUN-77-x", title="없는 업무")
    gh.pulls[9] = pull(9, head="XRUN-1", title="prefixRUN-1")

    report = sync_source(conn, gh, SOURCE, NOW)

    assert report.pulls_linked == [] and detected(conn, work) == []
    assert conn.execute("SELECT COUNT(*) FROM work_pull_requests").fetchone()[0] == 0


def test_key_of_another_workspace_is_ignored(conn, session_id):
    gh = FakeGitHub()
    imported_work(conn, session_id, gh)
    repo.create_session(conn, "sess-other", NOW)
    conn.execute("BEGIN IMMEDIATE")
    other, _ = repo.create_work_item(conn, "sess-other", title="남의 업무", request="-", kind="bug_fix",
                                     source_type="manual", now=NOW)
    repo.create_work_item(conn, "sess-other", title="남의 업무 2", request="-", kind="bug_fix", source_type="manual",
                          now=NOW)
    conn.execute("COMMIT")
    gh.pulls[7] = pull(7, head="RUN-2-x")  # 이 워크스페이스에는 RUN-2 가 없다

    report = sync_source(conn, gh, SOURCE, NOW)

    assert report.pulls_linked == [] and detected(conn, other) == []


def test_runloom_pull_request_is_not_stored_again(conn, session_id):
    gh = FakeGitHub()
    work = imported_work(conn, session_id, gh)
    task_id = tasks_by_ref(conn, session_id)["acme/billing#1"]["task_id"]
    conn.execute(
        "INSERT INTO task_pull_requests (task_id, session_id, source_id, repository_full_name, issue_number,"
        " head_branch, fix_execution_id, review_execution_id, state, pr_number, created_at, updated_at)"
        " VALUES (?, ?, ?, 'acme/billing', 1, 'RUN-1-2', 'exec-f', 'exec-r', 'open', 31, ?, ?)",
        (task_id, session_id, SOURCE, NOW, NOW),
    )
    gh.pulls[31] = pull(31, head="RUN-1-2", title="RUN-1 초안")

    report = sync_source(conn, gh, SOURCE, NOW)

    assert report.pulls_linked == [] and detected(conn, work) == []


def test_resync_is_idempotent_and_cursor_advances(conn, session_id):
    gh = FakeGitHub()
    work = imported_work(conn, session_id, gh)
    assert gh.pull_calls == [None] and repo.get_pull_cursor(conn, SOURCE) is None  # PR 이 없으면 커서 그대로
    gh.pulls[7] = pull(7, head="RUN-1-a", at="2026-10-06T03:00:00Z")
    gh.pulls[8] = pull(8, head="nothing", at="2026-10-06T04:00:00Z")  # 붙지 않은 PR 도 커서를 옮긴다

    sync_source(conn, gh, SOURCE, NOW)
    assert repo.get_pull_cursor(conn, SOURCE) == "2026-10-06T04:00:00Z"

    again = sync_source(conn, gh, SOURCE, NOW)
    assert gh.pull_calls[-1] == "2026-10-06T04:00:00Z"
    assert again.pulls_linked == [] and len(detected(conn, work)) == 1

    gh.pulls[7] = pull(7, head="RUN-1-a", title="제목 바뀜", at="2026-10-06T05:00:00Z")
    third = sync_source(conn, gh, SOURCE, NOW)
    assert third.pulls_linked == []
    (row,) = detected(conn, work)
    assert row["title"] == "제목 바뀜"
    assert len(work_events(conn, work, "pull_request_linked")) == 1
    assert repo.get_pull_cursor(conn, SOURCE) == "2026-10-06T05:00:00Z"


def test_open_then_merged_pr_completes_work_once(conn, session_id):
    gh = FakeGitHub()
    work = imported_work(conn, session_id, gh)
    gh.pulls[7] = pull(7, head="RUN-1-a")
    sync_source(conn, gh, SOURCE, NOW)
    assert work_status_of(conn, session_id, work) == ("PR · 검토", "PR 확인 — #7")

    gh.pulls[7] = pull(7, head="RUN-1-a", state="closed", merged_at="2026-10-06T06:00:00Z", at="2026-10-06T06:00:00Z")
    sync_source(conn, gh, SOURCE, NOW)
    assert work_status_of(conn, session_id, work) == ("완료", "PR 병합 — #7")
    assert repo.get_work_item(conn, session_id, work)["closed_at"] == NOW

    gh.put(issue(1, state="closed", updated_at="2026-10-06T06:01:00Z"))  # 원본 이슈 닫힘이 겹쳐도
    gh.pulls[7] = pull(7, head="RUN-1-a", state="closed", merged_at="2026-10-06T06:00:00Z", at="2026-10-06T06:02:00Z")
    sync_source(conn, gh, SOURCE, NOW)
    assert [e["to"] for e in work_events(conn, work, "status_changed")].count("완료") == 1
    assert work_status_of(conn, session_id, work) == ("완료", "PR 병합 — #7")


def test_closed_pr_without_merge_returns_to_direct_work(conn, session_id):
    gh = FakeGitHub()
    work = imported_work(conn, session_id, gh)
    member = repo.list_members(conn, session_id)[0]
    repo.start_direct_work(conn, session_id, work, member_id=member["member_id"], branch="RUN-1-a", now=NOW)
    gh.pulls[7] = pull(7, head="RUN-1-a")
    sync_source(conn, gh, SOURCE, NOW)
    assert work_status_of(conn, session_id, work) == ("PR · 검토", "PR 확인 — #7")

    gh.pulls[7] = pull(7, head="RUN-1-a", state="closed", at="2026-10-06T06:00:00Z")
    sync_source(conn, gh, SOURCE, NOW)
    assert detected(conn, work)[0]["state"] == "closed"
    assert work_status_of(conn, session_id, work) == ("직접 작업 중", member["display_name"])


def test_pr_read_failure_keeps_issue_intake_and_cursor(conn, session_id):
    gh = FakeGitHub()
    gh.put(issue(1))
    gh.pulls[7] = pull(7, head="RUN-1-a")
    gh.pull_failures = [GitHubUnavailable("GET /repos/acme/billing/pulls: HTTP 502")]

    report = sync_source(conn, gh, SOURCE, NOW)

    assert report.error is None and len(report.created) == 1
    assert report.pull_error == "GET /repos/acme/billing/pulls: HTTP 502" and report.pulls_linked == []
    assert repo.get_pull_cursor(conn, SOURCE) is None
    assert conn.execute("SELECT COUNT(*) FROM work_pull_requests").fetchone()[0] == 0

    gh.pull_failures = [GitHubRateLimited("GET /repos/acme/billing/pulls: HTTP 429", 45, None)]
    limited = sync_source(conn, gh, SOURCE, NOW)
    assert (limited.pull_error, limited.retry_after_seconds) == ("GET /repos/acme/billing/pulls: HTTP 429", 45)

    retried = sync_source(conn, gh, SOURCE, NOW)  # 다음 호출이 처음부터 다시 읽어 붙인다
    assert len(retried.pulls_linked) == 1 and repo.get_pull_cursor(conn, SOURCE) == "2026-10-06T03:00:00Z"


def test_pr_is_not_read_when_issue_polling_failed(conn, session_id):
    gh = FakeGitHub()
    gh.failures = [GitHubUnavailable("GET /repos/acme/billing/issues: HTTP 502")]
    report = sync_source(conn, gh, SOURCE, NOW)
    assert report.error is not None and gh.pull_calls == []
