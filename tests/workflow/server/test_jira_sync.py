# ruff: noqa: F811 — test_task_cycle 픽스처(cycle·settings·worker)를 가져와 인자로 쓴다
"""jira_sync — Jira 이슈 가져오기·업무 upsert·원본 닫힘·맡기기 전 대기 (phase 18 step 5, ADR-0024 결정 4~9,
ARCHITECTURE "Jira 소스 — phase 18" 가져오기·원본 조회·지시·매칭).

실제 Jira 를 부르지 않는다 — `FakeJira` 가 `JiraClient` 의 `search_issues` 를 흉내 낸다(JQL 의 커서·statusCategory,
updated·key 오름차순, `nextPageToken` 페이지). 연결 저장소는 test_task_cycle 의 acme/billing 소스다.
"""

import inspect
import re
from datetime import datetime
from types import SimpleNamespace

import pytest

from workflow.adapters import repo
from workflow.adapters.db import connect
from workflow.adapters.jira_client import JiraError, JiraRateLimited, JiraUnauthorized, JiraUnavailable
from workflow.adapters.secret_store import JIRA_API_TOKEN, SecretStore
from workflow.contracts.jira import JiraChoices, JiraIssueSnapshot, JiraIssueType, JiraProjectRef
from workflow.domain.field_mapping import MappingRow
from workflow.server import jira_sync, task_cycle
from workflow.server.jira_sync import JiraClients, sync_project, task_intake_facts
from workflow.server.worker import JIRA_SYNC_INTERVAL_SECONDS, Worker

from .conftest import log_in
from .test_task_cycle import (  # noqa: F401 — 픽스처
    FIX,
    NOW,
    REVIEW,
    SESSION,
    SOURCE,
    Clock,
    NoCallbacks,
    clock,
    config,
    cycle,
    executions,
    finish_fix,
    import_issue,
    make_worker,
    review_tasks,
    settings,
    status,
    worker,
)

START = "2026-10-06T00:00:00Z"
SITE = "https://acme.atlassian.net"
CLOUD = "11111111-2222-4333-8444-555555555555"
FACTS = SimpleNamespace(site_url=SITE, cloud_id=CLOUD, api_base="gateway", email="dev@acme.com",
                        account_id="5b10ac8d82e05b22cc7d4ef5", display_name="김개발")
REF = JiraProjectRef(project_id="10000", key="SHOP", name="쇼핑몰")
CHOICES = JiraChoices(issue_types=[JiraIssueType(id="10001", name="Bug"), JiraIssueType(id="10002", name="Task")],
                      statuses=["대기", "진행 중", "리뷰중", "종료"])


def _ms(value: str) -> int:
    return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1000)


class FakeJira:
    """`search_issues` 만 — JQL 의 `updated >= <ms>`·`statusCategory != Done` 을 흉내 내고 updated·key 오름차순으로
    `per_page` 씩 자른다. 토큰은 다음 위치. `failures` 는 호출마다 하나씩 꺼낸다(None 이면 정상)."""

    def __init__(self, per_page: int = 100):
        self.issues: dict[str, JiraIssueSnapshot] = {}
        self.per_page = per_page
        self.failures: list[Exception | None] = []
        self.calls: list[tuple[str, str | None]] = []

    def put(self, snapshot: JiraIssueSnapshot) -> None:
        self.issues[snapshot.issue_id] = snapshot

    def search_issues(self, jql: str, *, next_page_token: str | None, site_url: str):
        assert site_url == SITE
        self.calls.append((jql, next_page_token))
        if self.failures:
            failure = self.failures.pop(0)
            if failure is not None:
                raise failure
        cursor = re.search(r"updated >= (\d+)", jql)
        items = sorted(
            (i for i in self.issues.values()
             if (cursor is None or _ms(i.updated) >= int(cursor.group(1)))
             and ("statusCategory != Done" not in jql or i.status_category != "done")),
            key=lambda i: (i.updated, i.key),
        )
        start = int(next_page_token or 0)
        end = start + self.per_page
        return items[start:end], (str(end) if end < len(items) else None)

    def __getattr__(self, name):  # 가져오기는 다른 Jira API 를 부르지 않는다
        raise AssertionError(f"가져오기가 {name} 를 불렀다")


def jissue(n: int, **overrides) -> JiraIssueSnapshot:
    data = {
        "issue_id": str(10000 + n), "key": f"SHOP-{n}", "project_id": "10000",
        "summary": f"쿠폰 오류 {n}", "description_text": f"재현 절차 {n}", "status_name": "대기",
        "status_category": "new", "issue_type": "Bug", "priority": "Medium", "labels": [],
        "created": "2026-10-06T03:00:00Z", "updated": f"2026-10-06T03:{n:02d}:00Z",
    }
    return JiraIssueSnapshot.model_validate({**data, **overrides})


def add_project(conn, *, start_mode: str = "from_now", **settings) -> str:
    repo.save_jira_connection(conn, FACTS, session_id=SESSION, now=START)
    source_id = repo.add_jira_project(conn, session_id=SESSION, ref=REF, github_source_id=SOURCE,
                                      start_mode=start_mode, choices=CHOICES, now=START)
    if settings:
        values = {"issue_types": [], "status_on_start": None, "status_on_review": None, "status_on_done": None,
                  "followup_issue_type": None, "enabled": True} | settings
        repo.update_jira_project(conn, SESSION, source_id, now=START, github_source_id=SOURCE, **values)
    return source_id


@pytest.fixture
def project(conn, cycle) -> str:
    return add_project(conn)


def jira_works(conn) -> list:
    return conn.execute("SELECT * FROM work_items WHERE source_type = 'jira' ORDER BY key_number").fetchall()


def first_task(conn, work_item_id: str):
    return repo.list_work_item_tasks(conn, work_item_id)[0]


def jira_row(conn, source_id: str, issue_id: str):
    return repo.get_jira_issue(conn, SESSION, source_id, issue_id)


def project_row(conn, source_id: str):
    return conn.execute("SELECT * FROM jira_projects WHERE source_id = ?", (source_id,)).fetchone()


# --- 가져오기·커서 ---------------------------------------------------------------------------------


def test_two_pages_become_work_items_and_the_cursor_moves_after_the_last_page(conn, project):
    jira = FakeJira(per_page=2)
    for n in (1, 2, 3):
        jira.put(jissue(n))

    result = sync_project(conn, jira, project, NOW)

    assert result.error is None and result.updated == 0 and not result.auth_failed
    assert [token for _, token in jira.calls] == [None, "2"]
    assert jira.calls[0][0] == f"project = 10000 AND updated >= {_ms(START)} ORDER BY updated ASC, key ASC"
    works = jira_works(conn)
    assert [w["work_item_id"] for w in works] == result.created
    work = works[0]
    assert (work["source_id"], work["source_item_id"], work["source_key"], work["source_url"], work["source_state"]) == (
        project, "10001", "SHOP-1", f"{SITE}/browse/SHOP-1", "대기")
    assert (work["title"], work["request"], work["kind"], work["priority"]) == ("쿠폰 오류 1", "재현 절차 1", "bug_fix",
                                                                              "normal")
    task = first_task(conn, work["work_item_id"])
    assert (task["kind"], task["source_ref"], task["run_mode"]) == ("bug_fix", "SHOP-1", "auto")
    assert task["required_capability_json"] == '{"code": "code.fix", "scope": {"repository_id": "billing"}}'
    row = jira_row(conn, project, "10001")
    assert (row["task_id"], row["issue_key"], row["state"], row["status_name"], row["source_revision"]) == (
        task["task_id"], "SHOP-1", "open", "대기", 1)
    assert row["delegated_by"] is None and row["delegated_at"] is None
    stored = project_row(conn, project)
    assert (stored["cursor_ms"], stored["cursor_updated_at"]) == (_ms("2026-10-06T03:03:00Z"), NOW)
    # 다음 바퀴는 본 이슈의 가장 늦은 updated 부터(포함 경계)
    sync_project(conn, jira, project, NOW)
    assert jira.calls[-1][0] == f"project = 10000 AND updated >= {_ms('2026-10-06T03:03:00Z')} ORDER BY updated ASC, key ASC"


def test_failed_page_keeps_the_cursor_and_saved_issues_are_absorbed_next_time(conn, project):
    jira = FakeJira(per_page=1)
    jira.put(jissue(1))
    jira.put(jissue(2))
    jira.failures = [None, JiraUnavailable("GET /rest/api/3/search/jql: HTTP 503", 503)]

    result = sync_project(conn, jira, project, NOW)

    assert result.error is not None and len(result.created) == 1
    assert project_row(conn, project)["cursor_ms"] == _ms(START)  # 커서 그대로
    again = sync_project(conn, jira, project, NOW)
    assert again.error is None and len(again.created) == 1  # 앞 이슈는 digest 로 흡수
    assert len(jira_works(conn)) == 2


def test_same_issue_again_is_idempotent(conn, project):
    jira = FakeJira()
    jira.put(jissue(1))
    sync_project(conn, jira, project, NOW)
    before = dict(jira_works(conn)[0])

    result = sync_project(conn, jira, project, NOW)

    assert result.created == [] and result.updated == 0
    assert len(jira_works(conn)) == 1 and dict(jira_works(conn)[0]) == before
    assert jira_row(conn, project, "10001")["source_revision"] == 1


def test_title_change_raises_revisions_and_status_change_follows(conn, project):
    jira = FakeJira()
    jira.put(jissue(1))
    sync_project(conn, jira, project, NOW)
    jira.put(jissue(1, summary="쿠폰 두 번 적용", status_name="진행 중", status_category="indeterminate",
                    key="SHOP-11", updated="2026-10-06T04:00:00Z"))

    result = sync_project(conn, jira, project, NOW)

    assert result.updated == 1 and result.created == []
    (work,) = jira_works(conn)
    assert (work["title"], work["revision"], work["source_state"], work["source_key"], work["source_url"]) == (
        "쿠폰 두 번 적용", 2, "진행 중", "SHOP-11", f"{SITE}/browse/SHOP-11")
    task = first_task(conn, work["work_item_id"])
    assert (task["title"], task["revision"]) == ("쿠폰 두 번 적용", 2)
    row = jira_row(conn, project, "10001")
    assert (row["source_revision"], row["issue_key"], row["status_name"], row["state"]) == (2, "SHOP-11", "진행 중",
                                                                                           "open")
    # 상태만 바뀌면 원본 revision 만 오른다
    jira.put(jissue(1, summary="쿠폰 두 번 적용", status_name="리뷰중", key="SHOP-11", updated="2026-10-06T05:00:00Z"))
    sync_project(conn, jira, project, NOW)
    assert first_task(conn, work["work_item_id"])["revision"] == 2
    assert jira_row(conn, project, "10001")["source_revision"] == 3
    # 저장값보다 오래된 스냅숏은 버린다
    jira.put(jissue(1, summary="옛 제목", updated="2026-10-06T04:30:00Z"))
    sync_project(conn, jira, project, NOW)
    assert jira_works(conn)[0]["title"] == "쿠폰 두 번 적용"


def test_issue_type_filter_applies_only_to_new_issues(conn, cycle):
    source_id = add_project(conn, issue_types=["Bug"])
    jira = FakeJira()
    jira.put(jissue(1))
    jira.put(jissue(2, issue_type="Task"))
    jira.put(jissue(3, issue_type="bug"))  # 대소문자 무시

    sync_project(conn, jira, source_id, NOW)

    assert [w["source_key"] for w in jira_works(conn)] == ["SHOP-1", "SHOP-3"]
    # 이미 받은 이슈는 유형이 바뀌어도 갱신한다
    jira.put(jissue(1, issue_type="Task", summary="바뀐 제목", updated="2026-10-06T04:00:00Z"))
    sync_project(conn, jira, source_id, NOW)
    assert jira_works(conn)[0]["title"] == "바뀐 제목"


def test_from_now_skips_backlog_and_all_open_takes_open_backlog(conn, cycle):
    backlog = {"created": "2026-09-01T00:00:00Z"}
    jira = FakeJira()
    jira.put(jissue(1, **backlog))
    jira.put(jissue(2))
    jira.put(jissue(3, **backlog, status_name="종료", status_category="done"))
    from_now = add_project(conn)

    sync_project(conn, jira, from_now, NOW)
    assert [w["source_key"] for w in jira_works(conn)] == ["SHOP-2"]

    other = repo.add_jira_project(conn, session_id=SESSION, ref=JiraProjectRef(project_id="20000", key="OPS", name="운영"),
                                  github_source_id=SOURCE, start_mode="all_open", choices=CHOICES, now=START)
    assert project_row(conn, other)["cursor_ms"] is None
    ops = FakeJira()
    for n in (1, 2, 3):
        snapshot = jissue(n, project_id="20000", key=f"OPS-{n}", issue_id=str(20000 + n), **backlog)
        ops.put(snapshot.model_copy(update={"status_category": "done", "status_name": "종료"}) if n == 3 else snapshot)

    sync_project(conn, ops, other, NOW)

    assert ops.calls[0][0] == "project = 20000 AND statusCategory != Done ORDER BY updated ASC, key ASC"
    assert [w["source_key"] for w in jira_works(conn) if w["source_id"] == other] == ["OPS-1", "OPS-2"]
    assert project_row(conn, other)["cursor_ms"] == _ms("2026-10-06T03:02:00Z")


def test_mapping_table_decides_kind_and_priority(conn, project):
    github_rows = repo.list_field_mappings(conn, SESSION, "github")
    repo.replace_field_mappings(conn, SESSION, [
        *github_rows,
        MappingRow("jira", "kind", "Bug", "bug_fix", 1),
        MappingRow("jira", "priority", "Highest", "high", 1),
        MappingRow("jira", "priority", "urgent", "high", 2),
    ], now=NOW)
    jira = FakeJira()
    jira.put(jissue(1, priority="Highest"))
    jira.put(jissue(2, issue_type="Task"))  # 종류 매핑 없음 — 받지 않는다
    jira.put(jissue(3, labels=["urgent"], priority=None))

    sync_project(conn, jira, project, NOW)

    assert [(w["source_key"], w["kind"], w["priority"]) for w in jira_works(conn)] == [
        ("SHOP-1", "bug_fix", "high"), ("SHOP-3", "bug_fix", "high")]


def test_disabled_project_is_not_polled(conn, cycle):
    source_id = add_project(conn, enabled=False)
    jira = FakeJira()
    jira.put(jissue(1))
    result = sync_project(conn, jira, source_id, NOW)
    assert jira.calls == [] and result.created == [] and result.error is None


def test_rate_limit_and_unauthorized_keep_the_cursor(conn, project):
    jira = FakeJira()
    jira.put(jissue(1))
    jira.failures = [JiraRateLimited("GET /rest/api/3/search/jql: HTTP 429", 429, 120)]

    limited = sync_project(conn, jira, project, NOW)

    assert limited.retry_after_seconds == 120 and limited.error is not None and not limited.auth_failed
    assert project_row(conn, project)["cursor_updated_at"] is None and jira_works(conn) == []

    jira.failures = [JiraUnauthorized("GET /rest/api/3/search/jql: HTTP 401", 401)]
    denied = sync_project(conn, jira, project, NOW)
    assert denied.auth_failed and denied.retry_after_seconds is None
    assert repo.get_jira_connection(conn, SESSION)["auth_failed_at"] == NOW
    assert project_row(conn, project)["cursor_updated_at"] is None


def test_issue_with_known_source_item_id_joins_that_work_item(conn, project):
    """후속 이슈 등록(step 8)이 원본 칸을 채운 업무 — 동기화가 같은 이슈를 받으면 새 업무를 만들지 않고 붙는다."""
    work_item_id = repo.insert_work_item_task(conn, {
        "task_id": "task-followup", "session_id": SESSION, "title": "후속 검토", "request": "Runloom 이 만든 요청",
        "kind": "bug_fix", "required_capability": {"code": "code.fix", "scope": {"repository_id": "billing"}},
        "selection_mode": "auto", "chosen_agent_id": None, "run_mode": "auto", "completion_mode": "review",
        "criteria": [], "predecessor_task_id": None, "revision": 1, "target": {},
        "status": "대기", "status_reason": "준비 판정 대기",
    }, NOW, source_type="jira", source_id=project, source_item_id="10005", source_key="SHOP-5")
    jira = FakeJira()
    jira.put(jissue(5, summary="Jira 쪽 제목", labels=["runloom"]))

    result = sync_project(conn, jira, project, NOW)

    assert result.created == [] and result.updated == 1
    (work,) = jira_works(conn)
    assert work["work_item_id"] == work_item_id and work["source_state"] == "대기"
    row = jira_row(conn, project, "10005")
    assert (row["task_id"], row["delegated_by"], row["delegated_at"]) == ("task-followup", "followup", NOW)
    # 붙인 업무는 Jira 제목·본문을 업무 입력으로 받지 않는다
    jira.put(jissue(5, summary="또 바뀐 제목", updated="2026-10-06T06:00:00Z"))
    sync_project(conn, jira, project, NOW)
    assert (jira_works(conn)[0]["title"], repo.get_task(conn, "task-followup")["revision"]) == ("후속 검토", 1)
    assert jira_row(conn, project, "10005")["source_revision"] == 2


# --- 원본 조회·준비 판정 ------------------------------------------------------------------------


def _imported(conn, source_id: str, jira: FakeJira) -> str:
    sync_project(conn, jira, source_id, NOW)
    return first_task(conn, jira_works(conn)[-1]["work_item_id"])["task_id"]


def test_origin_for_jira_work_uses_the_linked_repository_as_all_open(conn, project):
    jira = FakeJira()
    jira.put(jissue(1))
    task_id = _imported(conn, project, jira)

    origin = task_cycle.origin(conn, repo.get_task(conn, task_id))

    assert origin.issue is None and origin.own and origin.state == "open" and origin.origin_key == "SHOP-1"
    assert origin.config.source_id == SOURCE and origin.config.intake == "all_open"
    assert origin.config.trigger_label is None and origin.config.review_agent_id == REVIEW
    facts = task_intake_facts(conn, SESSION, task_id)
    assert (facts.assignee_ids, facts.delegated, facts.source_state, facts.request_required) == ((), False, "open",
                                                                                                True)
    assert task_cycle.task_intake(conn, repo.get_task(conn, task_id)) == facts
    # GitHub·직접 등록 Task 에는 Jira 사실이 없다
    gh_task = import_issue(conn, 1)
    assert task_intake_facts(conn, SESSION, gh_task) is None
    github = task_cycle.origin(conn, repo.get_task(conn, gh_task))
    assert github.issue is not None and github.own and github.origin_key is None and github.config.intake == "filtered"


def test_jira_work_waits_for_delegation_then_starts_with_the_fix_agent(conn, project, worker, client):
    jira = FakeJira()
    jira.put(jissue(1))
    task_id = _imported(conn, project, jira)

    worker.tick()

    assert executions(conn, task_id) == []
    assert status(conn, task_id) == ("대기", "실행 지시 전 — [에이전트에게 맡기기] 또는 `runloom` 라벨")
    work = repo.work_item_of_task(conn, task_id)
    assert (work["status"], work["status_reason"]) == ("새로 들어옴", "담당 없음")
    assert not repo.work_item_facts(conn, work["work_item_id"]).delegated

    admin = log_in(client)
    response = admin.post("/work/RUN-1/assignee", data={"assignee": f"agent:{FIX}"}, follow_redirects=False)

    assert response.status_code == 303, response.text
    row = jira_row(conn, project, "10001")
    assert row["delegated_by"] == "operator" and row["delegated_at"] is not None
    assert repo.work_item_facts(conn, work["work_item_id"]).delegated
    worker.tick()
    (execution,) = executions(conn, task_id)
    assert execution["agent_id"] == FIX
    assert repo.work_item_of_task(conn, task_id)["status"] != "새로 들어옴"


def test_done_category_holds_the_next_stage_and_reopen_resumes(conn, project, worker, client, store):
    jira = FakeJira()
    jira.put(jissue(1))
    task_id = _imported(conn, project, jira)
    assert log_in(client).post("/work/RUN-1/assignee", data={"assignee": f"agent:{FIX}"},
                               follow_redirects=False).status_code == 303
    worker.tick()
    (execution,) = executions(conn, task_id)

    # Jira 에서 완료 범주로 — 도는 실행은 끊지 않는다
    jira.put(jissue(1, status_name="종료", status_category="done", updated="2026-10-06T04:00:00Z"))
    sync_project(conn, jira, project, NOW)
    assert jira_row(conn, project, "10001")["state"] == "closed"
    assert task_cycle.origin(conn, repo.get_task(conn, task_id)).state == "closed"
    assert repo.get_execution(conn, execution["execution_id"])["released_at"] is None
    finish_fix(conn, store, execution["execution_id"])
    worker.tick()
    assert review_tasks(conn, task_id) == []  # source_closed — 다음 단계를 시작하지 않는다
    work = repo.work_item_of_task(conn, task_id)
    assert work["status"] not in ("완료", "종료")  # Runloom 업무를 닫지 않는다

    jira.put(jissue(1, status_name="대기", status_category="new", updated="2026-10-06T05:00:00Z"))
    sync_project(conn, jira, project, NOW)
    worker.tick()
    assert len(review_tasks(conn, task_id)) == 1  # 다시 열리면 이어간다


def test_closed_before_delegation_blocks_start(conn, project, worker, client):
    jira = FakeJira()
    jira.put(jissue(1))
    task_id = _imported(conn, project, jira)
    jira.put(jissue(1, status_name="종료", status_category="done", updated="2026-10-06T04:00:00Z"))
    sync_project(conn, jira, project, NOW)

    log_in(client).post("/work/RUN-1/assignee", data={"assignee": f"agent:{FIX}"}, follow_redirects=False)
    worker.tick()

    assert executions(conn, task_id) == []
    assert status(conn, task_id)[1] == "원본 이슈 닫힘 — 재오픈 시 재평가"


def test_jira_work_panel_and_task_page_render(conn, project, client):
    jira = FakeJira()
    jira.put(jissue(1, summary="<script>쿠폰</script>"))
    task_id = _imported(conn, project, jira)
    admin = log_in(client)

    for path in ("/work/RUN-1/panel", "/tasks?open=RUN-1", f"/tasks/{task_id}"):
        response = admin.get(path)
        assert response.status_code == 200, (path, response.text)
        assert "<script>쿠폰" not in response.text


# --- 워커 ------------------------------------------------------------------------------------------


def jira_worker(settings, store, clock, jira_for) -> Worker:
    return Worker(lambda: connect(settings.db_path), store, NoCallbacks(), settings, clock, jira_for=jira_for)


def test_tick_syncs_jira_right_after_github():
    calls = re.findall(r"self\.(_\w+)\(conn, report\)", inspect.getsource(Worker.tick))
    assert calls[:3] == ["_sync_github", "_sync_jira", "_mark_offline"]


def test_worker_syncs_on_interval_and_waits_out_rate_limits(conn, project, settings, store, clock):
    jira = FakeJira()
    jira.put(jissue(1))
    rows = []
    worker = jira_worker(settings, store, clock, lambda row: rows.append(row["session_id"]) or jira)

    assert worker.tick().issues_created == 1
    assert len(jira_works(conn)) == 1 and rows == [SESSION]
    worker.tick()
    assert len(jira.calls) == 1  # 간격 전

    clock.now = "2026-10-06T12:01:00Z"
    jira.failures = [JiraRateLimited("GET /rest/api/3/search/jql: HTTP 429", 429, 300)]
    report = worker.tick()
    assert report.sync_errors == 1 and len(jira.calls) == 2

    clock.now = "2026-10-06T12:02:00Z"  # 간격은 지났지만 Retry-After 300초 안
    worker.tick()
    assert len(jira.calls) == 2
    clock.now = "2026-10-06T12:06:00Z"
    worker.tick()
    assert len(jira.calls) == 3
    assert JIRA_SYNC_INTERVAL_SECONDS == 60


def test_worker_skips_disconnected_auth_failed_and_clientless_workspaces(conn, project, settings, store, clock):
    jira = FakeJira()
    jira.put(jissue(1))
    worker = jira_worker(settings, store, clock, lambda row: None)
    worker.tick()
    assert jira.calls == []  # 토큰 없음

    worker = jira_worker(settings, store, clock, lambda row: jira)
    repo.mark_jira_auth_failed(conn, SESSION, now=NOW)
    worker.tick()
    repo.save_jira_connection(conn, FACTS, session_id=SESSION, now=NOW)
    repo.disconnect_jira(conn, SESSION, now=NOW)
    worker.tick()
    assert jira.calls == [] and jira_works(conn) == []

    repo.save_jira_connection(conn, FACTS, session_id=SESSION, now=NOW)  # 다시 연결하면 이어간다
    jira.failures = [JiraUnauthorized("GET /rest/api/3/search/jql: HTTP 401", 401)]
    worker.tick()
    assert repo.get_jira_connection(conn, SESSION)["auth_failed_at"] is not None
    clock.now = "2026-10-06T13:00:00Z"
    worker.tick()
    assert len(jira.calls) == 1  # 토큰 오류 뒤에는 다시 연결할 때까지 부르지 않는다


def test_worker_logs_sync_errors_and_keeps_ticking(conn, project, settings, store, clock, caplog):
    class Broken(FakeJira):
        def search_issues(self, *args, **kwargs):
            raise RuntimeError("뜻밖의 오류")

    worker = jira_worker(settings, store, clock, lambda row: Broken())
    report = worker.tick()
    assert report.sync_errors == 1
    assert "Jira 가져오기 실패" in caplog.text

    jira = FakeJira()
    jira.failures = [JiraError("GET /rest/api/3/search/jql: HTTP 409", 409)]
    worker = jira_worker(settings, store, clock, lambda row: jira)
    assert worker.tick().sync_errors == 1


def test_jira_clients_needs_a_live_connection_and_a_token_file(conn, project, settings):
    secrets = SecretStore(settings.secret_dir)
    clients = JiraClients(secrets)
    row = repo.get_jira_connection(conn, SESSION)
    assert clients(row) is None  # 토큰 파일 없음

    secrets.write(JIRA_API_TOKEN, "ATATT-secret-token")
    client = clients(row)
    assert client is not None and "ATATT-secret-token" not in repr(client)
    repo.mark_jira_auth_failed(conn, SESSION, now=NOW)
    assert clients(repo.get_jira_connection(conn, SESSION)) is None
    repo.save_jira_connection(conn, FACTS, session_id=SESSION, now=NOW)
    repo.disconnect_jira(conn, SESSION, now=NOW)
    assert clients(repo.get_jira_connection(conn, SESSION)) is None
    assert jira_sync.JIRA_SYNC_INTERVAL_SECONDS == JIRA_SYNC_INTERVAL_SECONDS


def test_github_sync_is_unchanged_by_a_jira_project(conn, project, worker):
    """GitHub 원본 업무는 Jira 프로젝트가 있어도 그대로 — 원본 조회·담당 매칭·착수."""
    task_id = import_issue(conn, 1)
    origin = task_cycle.origin(conn, repo.get_task(conn, task_id))
    assert origin.state == "open" and origin.issue["github_issue_id"] == 1001
    worker.tick()
    assert len(executions(conn, task_id)) == 1
