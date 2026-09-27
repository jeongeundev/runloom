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
)
from workflow.contracts.github import AssigneeBinding, GitHubIssueSnapshot, GitHubSourceConfig, IssuePrLink
from workflow.contracts.v1 import Capability, ExecutionRequest
from workflow.domain.selection import Candidate
from workflow.domain.task_readiness import ExecutorFacts, TaskFacts, evaluate_readiness
from workflow.server.auth import SESSION_COOKIE, verify_session
from workflow.server.github_sync import MAX_MERGE_CHECKS_PER_SYNC, sync_source, task_intake_facts

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
def session_id(client, conn) -> str:
    """웹 세션(직접 등록에도 쓴다) + 수정 Agent 등록 + 소스 + 담당 연결."""
    assert client.get("/tasks").status_code == 200
    sid = verify_session(client.cookies[SESSION_COOKIE], "test-session-secret")
    repo.upsert_agent(conn, {
        "agent_id": FIX_AGENT, "name": "수정", "owner_scope": "personal", "connection_type": "local",
        "local_registration_id": "local-billing",
        "capabilities": [{"code": "code.fix", "scope": {"repository_id": "billing"}}],
        "verification_profile_ids": ["vp-pytest"], "shared_to_all_sessions": True,
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
