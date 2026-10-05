# ruff: noqa: F811 — test_task_cycle·test_jira_sync 픽스처(cycle·settings·worker·project)를 가져와 인자로 쓴다
"""jira_delivery — 세 순간 → outbox(`jira_deliveries`) → 전환 전송 (phase 18 step 7, ADR-0024 결정 11·12,
ARCHITECTURE "Jira 소스 — phase 18" 상태 옮기기·오류 분류·화면)과 후속 이슈 등록(step 8, ADR-0024 결정 13).

실제 Jira 를 부르지 않는다 — `FakeJiraWriter` 가 `JiraClient` 의 `issue_status`·`transitions`·`transition` 을,
`FakeJiraCreator` 가 `create_issue`·`find_issues_by_label`·`issue_link_types`·`link_issues` 를 흉내 낸다.
"""

import dataclasses
import inspect
import json
import re

import pytest

from workflow.adapters import repo
from workflow.adapters.db import connect
from workflow.adapters.secret_store import JIRA_API_TOKEN
from workflow.adapters.jira_client import (
    JiraBadRequest,
    JiraForbidden,
    JiraNotFound,
    JiraRateLimited,
    JiraUnauthorized,
    JiraUnavailable,
)
from workflow.contracts.jira import JiraIssueRef, JiraTransition
from workflow.domain.adf import markdown_to_adf
from workflow.domain.jira_intake import followup_description
from workflow.domain.task_followup import FollowupTaskSpec
from workflow.domain.work_keys import work_path
from workflow.domain.work_status import WorkStatus
from workflow.server.jira_delivery import MAX_ATTEMPTS, deliver_jira_updates
from workflow.server.jira_sync import sync_project
from workflow.server.worker import Worker

from .conftest import log_in
from .test_jira_sync import CHOICES, FACTS, SITE, FakeJira, _imported, add_project, jira_works, jissue
from .test_task_cycle import (  # noqa: F401 — 픽스처
    FIX,
    NOW,
    SESSION,
    SOURCE,
    Clock,
    NoCallbacks,
    clock,
    cycle,
    executions,
    import_issue,
    make_worker,
    settings,
    worker,
)

LATER = "2026-10-06T12:10:00Z"
STATES = {"status_on_start": "진행 중", "status_on_review": "리뷰중", "status_on_done": "종료"}


class FakeJiraWriter:
    """이슈 상태와 전환 목록. `transition` 은 상태를 옮긴다. `failures[메서드]` 는 부를 때마다 하나씩 꺼낸다."""

    def __init__(self, status: str = "대기", category: str = "new"):
        self.status = (status, category)
        self.moves = {"진행 중": "indeterminate", "리뷰중": "indeterminate", "종료": "done"}
        self.failures: dict[str, list[Exception]] = {}
        self.calls: list[tuple] = []

    def _fail(self, method: str) -> None:
        queue = self.failures.get(method)
        if queue:
            raise queue.pop(0)

    def issue_status(self, issue_id: str) -> tuple[str, str]:
        self.calls.append(("issue_status", issue_id))
        self._fail("issue_status")
        return self.status

    def _available(self) -> list[JiraTransition]:
        return [JiraTransition(transition_id=str(21 + i), name=f"{name} 로", to_name=name, to_category=category)
                for i, (name, category) in enumerate(self.moves.items()) if name != self.status[0]]

    def transitions(self, issue_id: str) -> list[JiraTransition]:
        self.calls.append(("transitions", issue_id))
        self._fail("transitions")
        return self._available()

    def transition(self, issue_id: str, transition_id: str) -> None:
        self.calls.append(("transition", issue_id, transition_id))
        self._fail("transition")
        target = next(t for t in self._available() if t.transition_id == transition_id)
        self.status = (target.to_name, target.to_category)

    def posted(self) -> list[tuple]:
        return [c for c in self.calls if c[0] == "transition"]

    def __getattr__(self, name):  # 전환 전송은 다른 Jira API 를 부르지 않는다
        raise AssertionError(f"전환 전송이 {name} 를 불렀다")


def _work(conn, source_id: str, n: int = 1) -> str:
    jira = FakeJira()
    jira.put(jissue(n))
    task_id = _imported(conn, source_id, jira)
    return repo.work_item_of_task(conn, task_id)["work_item_id"]


@pytest.fixture
def work(conn, cycle) -> str:
    return _work(conn, add_project(conn, **STATES))


def move(conn, work_item_id: str, status: str, reason: str = "테스트", now: str = NOW) -> None:
    repo.set_work_status(conn, work_item_id, WorkStatus(status, reason), now=now)


def rows(conn, work_item_id: str) -> list:
    return repo.list_jira_deliveries(conn, work_item_id)


def deliver(conn, jira, now: str = NOW, client_for=None):
    return deliver_jira_updates(conn, client_for or (lambda _session: jira), now)


# --- 세 순간 → outbox ---------------------------------------------------------------------------


def test_each_moment_queues_one_row_once(conn, work):
    move(conn, work, "에이전트 작업 중")
    move(conn, work, "직접 작업 중")  # 같은 순간(start) — 다시 넣지 않는다
    move(conn, work, "PR · 검토")
    move(conn, work, "에이전트 작업 중", reason="재작업")  # 재작업으로 다시 들어가도 한 번
    move(conn, work, "PR · 검토", reason="PR 다시 열림")
    move(conn, work, "완료")

    queued = rows(conn, work)
    assert [(r["action"], r["moment"], r["target"], r["state"], r["attempts"]) for r in queued] == [
        ("transition", "start", "진행 중", "pending", 0),
        ("transition", "review", "리뷰중", "pending", 0),
        ("transition", "done", "종료", "pending", 0),
    ]
    assert [r["dedupe_key"] for r in queued] == [f"transition:{work}:{m}" for m in ("start", "review", "done")]
    assert all(r["delivery_id"].startswith("jdl-") and r["session_id"] == SESSION for r in queued)
    assert queued[0]["created_at"] == NOW and queued[0]["cause_issue_id"] is None


def test_other_statuses_queue_nothing(conn, work):
    for status in ("대기", "내 차례", "새로 들어옴", "종료"):
        move(conn, work, status)
    assert rows(conn, work) == []


def test_blank_status_name_or_disabled_project_queues_nothing(conn, cycle):
    source_id = add_project(conn, status_on_review="리뷰중")
    work = _work(conn, source_id)
    move(conn, work, "에이전트 작업 중")  # 작업 시작 → 옮기지 않음
    move(conn, work, "PR · 검토")
    assert [r["moment"] for r in rows(conn, work)] == ["review"]

    repo.update_jira_project(conn, SESSION, source_id, now=NOW, github_source_id=SOURCE, issue_types=[],
                             enabled=False, followup_issue_type=None, **STATES)
    move(conn, work, "완료")
    assert [r["moment"] for r in rows(conn, work)] == ["review"]


def test_target_name_is_kept_when_settings_change_later(conn, cycle):
    source_id = add_project(conn, **STATES)
    work = _work(conn, source_id)
    move(conn, work, "에이전트 작업 중")
    repo.update_jira_project(conn, SESSION, source_id, now=NOW, github_source_id=SOURCE, issue_types=[],
                             enabled=True, followup_issue_type=None, **(STATES | {"status_on_start": "대기"}))
    assert rows(conn, work)[0]["target"] == "진행 중"


def test_github_and_direct_work_queue_nothing(conn, cycle):
    add_project(conn, **STATES)
    github_work = repo.work_item_of_task(conn, import_issue(conn, 1))["work_item_id"]
    move(conn, github_work, "에이전트 작업 중")
    move(conn, github_work, "완료")
    assert conn.execute("SELECT COUNT(*) FROM jira_deliveries").fetchone()[0] == 0


def test_rows_are_written_in_the_status_transaction(conn, work):
    with pytest.raises(RuntimeError):
        with repo._tx(conn):
            move(conn, work, "에이전트 작업 중")
            raise RuntimeError("되돌림")
    assert rows(conn, work) == []
    assert repo.get_work_item(conn, SESSION, work)["status"] == "새로 들어옴"


# --- 전송 -----------------------------------------------------------------------------------------


def test_transition_is_posted_and_delivered(conn, work):
    move(conn, work, "에이전트 작업 중")
    jira = FakeJiraWriter()

    report = deliver(conn, jira)

    assert (report.delivered, report.failed, report.skipped, report.deferred) == (1, 0, 0, 0)
    assert jira.calls == [("issue_status", "10001"), ("transitions", "10001"), ("transition", "10001", "21")]
    (row,) = rows(conn, work)
    assert (row["state"], row["attempts"], row["delivered_at"], row["last_error"]) == ("delivered", 1, NOW, None)
    assert jira.status == ("진행 중", "indeterminate")
    # 한 번 보낸 행은 다시 보내지 않는다
    deliver(conn, jira)
    assert len(jira.posted()) == 1


def test_already_in_target_is_delivered_without_posting(conn, work):
    move(conn, work, "에이전트 작업 중")
    jira = FakeJiraWriter(status="진행 중 ", category="indeterminate")  # 대소문자·앞뒤 공백 무시

    report = deliver(conn, jira)

    assert report.delivered == 1 and jira.calls == [("issue_status", "10001")]
    (row,) = rows(conn, work)
    assert (row["state"], row["note"]) == ("delivered", "이미 그 상태")


def test_done_category_in_jira_skips_earlier_moments(conn, work):
    move(conn, work, "에이전트 작업 중")
    jira = FakeJiraWriter(status="종료", category="done")

    report = deliver(conn, jira)

    assert report.skipped == 1 and jira.posted() == []
    (row,) = rows(conn, work)
    assert (row["state"], row["note"]) == ("skipped", "Jira 에서 이미 완료 범주")


def test_later_moment_supersedes_an_unsent_earlier_one(conn, work):
    move(conn, work, "에이전트 작업 중")
    move(conn, work, "PR · 검토")
    jira = FakeJiraWriter()

    report = deliver(conn, jira)

    assert (report.skipped, report.delivered) == (1, 1)
    start, review = rows(conn, work)
    assert (start["state"], start["note"]) == ("skipped", "다음 순간으로 대체")
    assert review["state"] == "delivered" and jira.posted() == [("transition", "10001", "22")]


def test_no_transition_fails_without_retry(conn, work):
    move(conn, work, "에이전트 작업 중")
    jira = FakeJiraWriter()
    jira.moves = {"종료": "done"}

    report = deliver(conn, jira)

    assert report.failed == 1 and jira.posted() == []
    (row,) = rows(conn, work)
    assert (row["state"], row["last_error"]) == ("failed", "전환 없음 — 현재 상태 대기")
    jira.calls.clear()
    deliver(conn, jira, now=LATER)
    assert jira.calls == []


@pytest.mark.parametrize(("failure", "message"), [
    (JiraBadRequest("POST …: HTTP 400", 400, "Field 'resolution' is required"),
     "전환에 입력할 칸이 있음 — Jira 에서 옮기세요"),
    (JiraForbidden("POST …: HTTP 403", 403), "권한 없음 — 이 이슈를 옮길 수 없음"),
    (JiraNotFound("POST …: HTTP 404", 404), "이슈를 찾을 수 없음"),
])
def test_rejections_fail_with_a_reason(conn, work, failure, message):
    move(conn, work, "에이전트 작업 중")
    jira = FakeJiraWriter()
    jira.failures["transition"] = [failure]

    report = deliver(conn, jira)

    assert report.failed == 1
    (row,) = rows(conn, work)
    assert (row["state"], row["last_error"]) == ("failed", message)
    deliver(conn, jira, now=LATER)
    assert len(jira.posted()) == 1  # 다시 보내지 않는다


def test_unauthorized_keeps_pending_marks_the_connection_and_stops_the_workspace(conn, cycle):
    source_id = add_project(conn, **STATES)
    first, second = _work(conn, source_id, 1), _work(conn, source_id, 2)
    move(conn, first, "에이전트 작업 중")
    move(conn, second, "에이전트 작업 중")
    jira = FakeJiraWriter()
    jira.failures["issue_status"] = [JiraUnauthorized("GET …: HTTP 401", 401)]

    deliver(conn, jira)

    assert jira.calls == [("issue_status", "10001")]  # 같은 워크스페이스의 다음 행은 부르지 않는다
    one, two = rows(conn, first)[0], rows(conn, second)[0]
    assert (one["state"], one["attempts"], one["last_error"]) == ("pending", 1, "Jira 토큰 확인 필요 — 다시 연결")
    assert (two["state"], two["attempts"]) == ("pending", 0)
    assert repo.get_jira_connection(conn, SESSION)["auth_failed_at"] == NOW


def test_rate_limit_waits_retry_after_and_stops_the_round(conn, cycle):
    source_id = add_project(conn, **STATES)
    first, second = _work(conn, source_id, 1), _work(conn, source_id, 2)
    move(conn, first, "에이전트 작업 중")
    move(conn, second, "에이전트 작업 중")
    jira = FakeJiraWriter()
    jira.failures["issue_status"] = [JiraRateLimited("GET …: HTTP 429", 429, retry_after=120)]

    report = deliver(conn, jira)

    assert report.rate_limited and len(jira.calls) == 1
    row = rows(conn, first)[0]
    assert (row["state"], row["next_at"], row["last_error"]) == ("pending", "2026-10-06T12:02:00.000000Z",
                                                                 "Jira 요청 한도 — 잠시 뒤")
    jira.calls.clear()
    deliver(conn, jira, now="2026-10-06T12:01:00Z")  # 아직 기다린다 — 두 번째 행도 이번 바퀴에 보낸다
    assert jira.posted() == [("transition", "10002", "21")]
    deliver(conn, jira, now="2026-10-06T12:02:00Z")
    assert [r["state"] for r in rows(conn, first)] == ["delivered"]


def test_short_rate_limit_waits_at_least_a_minute(conn, work):
    move(conn, work, "에이전트 작업 중")
    jira = FakeJiraWriter()
    jira.failures["transitions"] = [JiraRateLimited("GET …: HTTP 429", 429, retry_after=5)]
    deliver(conn, jira)
    assert rows(conn, work)[0]["next_at"] == "2026-10-06T12:01:00.000000Z"


def test_lost_response_is_reconciled_by_reading_the_issue_status(conn, work):
    move(conn, work, "에이전트 작업 중")
    jira = FakeJiraWriter()

    def lost(issue_id, transition_id):  # Jira 는 옮겼는데 응답을 잃었다
        jira.status = ("진행 중", "indeterminate")
        raise JiraUnavailable("POST …: timeout")

    jira.transition = lost
    report = deliver(conn, jira)

    assert report.deferred == 1
    row = rows(conn, work)[0]
    assert (row["state"], row["next_at"], row["last_error"]) == ("pending", "2026-10-06T12:00:30.000000Z",
                                                                 "Jira 응답 없음 — 다시 시도")
    del jira.transition
    jira.calls.clear()
    deliver(conn, jira, now="2026-10-06T12:00:30.000000Z")
    assert jira.calls == [("issue_status", "10001")]  # 다시 보내지 않는다
    row = rows(conn, work)[0]
    assert (row["state"], row["note"], row["attempts"]) == ("delivered", "이미 그 상태", 2)


def test_unavailable_backs_off_and_gives_up_after_the_cap(conn, work):
    move(conn, work, "에이전트 작업 중")
    jira = FakeJiraWriter()
    jira.failures["issue_status"] = [JiraUnavailable("GET …: HTTP 503", 503) for _ in range(MAX_ATTEMPTS)]

    deliver(conn, jira)
    assert rows(conn, work)[0]["next_at"] == "2026-10-06T12:00:30.000000Z"
    conn.execute("UPDATE jira_deliveries SET next_at = NULL")
    deliver(conn, jira)
    assert rows(conn, work)[0]["next_at"] == "2026-10-06T12:01:00.000000Z"  # 30·2^(n-1)
    for _ in range(MAX_ATTEMPTS - 2):
        conn.execute("UPDATE jira_deliveries SET next_at = NULL")
        deliver(conn, jira)
    (row,) = rows(conn, work)
    assert (row["state"], row["attempts"]) == ("failed", MAX_ATTEMPTS)
    assert row["last_error"] == f"Jira 응답 없음 — {MAX_ATTEMPTS}회 시도 뒤 멈춤"


def test_rows_without_a_client_are_left_alone(conn, work):
    move(conn, work, "에이전트 작업 중")

    report = deliver(conn, None, client_for=lambda _session: None)

    assert not report.any()
    (row,) = rows(conn, work)
    assert (row["state"], row["attempts"]) == ("pending", 0)


def test_claim_and_record_are_fenced_by_attempts(conn, work):
    move(conn, work, "에이전트 작업 중")
    (row,) = rows(conn, work)
    until = "2026-10-06T12:02:00.000000Z"

    assert repo.claim_jira_delivery(conn, row["delivery_id"], 0, now=NOW, claim_until=until)
    assert not repo.claim_jira_delivery(conn, row["delivery_id"], 0, now=NOW, claim_until=until)  # 이미 가져감
    assert not repo.claim_jira_delivery(conn, row["delivery_id"], 1, now=NOW, claim_until=until)  # claim 이 살아 있음
    # claim 이 만료되면 다른 소비자가 가져가고, 늦게 끝난 앞 소비자의 기록은 버린다
    assert repo.claim_jira_delivery(conn, row["delivery_id"], 1, now=until, claim_until="2026-10-06T12:04:00Z")
    assert not repo.record_jira_delivery(conn, row["delivery_id"], 1, state="delivered", now=until)
    assert repo.record_jira_delivery(conn, row["delivery_id"], 2, state="delivered", now=until, note="이미 그 상태")
    (row,) = rows(conn, work)
    assert (row["state"], row["attempts"], row["delivered_at"], row["note"]) == ("delivered", 2, until, "이미 그 상태")
    assert [r["delivery_id"] for r in repo.jira_deliveries_due(conn, LATER)] == []


# --- 워커 ------------------------------------------------------------------------------------------


def test_tick_delivers_jira_right_after_github():
    calls = re.findall(r"self\.(_\w+)\(conn, report\)", inspect.getsource(Worker.tick))
    at = calls.index("_deliver_github")
    assert calls[at:at + 3] == ["_deliver_github", "_deliver_jira", "_deliver_notifications"]


def jira_worker(settings, store, clock, jira) -> Worker:
    return Worker(lambda: connect(settings.db_path), store, NoCallbacks(), settings, clock,
                  jira_for=lambda _connection: jira)


def test_worker_delivers_through_live_connections_only(conn, work, settings, store, clock):
    move(conn, work, "에이전트 작업 중")
    jira = FakeJiraWriter()
    worker = jira_worker(settings, store, clock, jira)
    worker._sync_jira = lambda conn, report: None  # 가져오기는 test_jira_sync 몫 — 여기선 전송만 본다

    repo.disconnect_jira(conn, SESSION, now=NOW)
    worker.tick()
    assert jira.posted() == [] and rows(conn, work)[0]["attempts"] == 0  # 끊긴 동안 시도 수가 늘지 않는다

    repo.save_jira_connection(conn, FACTS, session_id=SESSION, now=NOW)
    repo.mark_jira_auth_failed(conn, SESSION, now=NOW)
    worker.tick()
    assert jira.posted() == []

    repo.save_jira_connection(conn, FACTS, session_id=SESSION, now=NOW)  # 다시 연결하면 이어 보낸다
    report = worker.tick()
    assert report.jira_deliveries_sent == 1 and jira.posted() == [("transition", "10001", "21")]


def test_delegated_jira_work_moves_the_issue_when_the_agent_starts(conn, work, client, settings, store, clock):
    assert log_in(client).post("/work/RUN-1/assignee", data={"assignee": f"agent:{FIX}"},
                               follow_redirects=False).status_code == 303
    jira = FakeJiraWriter()
    worker = jira_worker(settings, store, clock, jira)
    worker._sync_jira = lambda conn, report: None

    worker.tick()

    assert repo.get_work_item(conn, SESSION, work)["status"] == "에이전트 작업 중"
    assert [(r["moment"], r["state"]) for r in rows(conn, work)] == [("start", "delivered")]
    assert jira.status == ("진행 중", "indeterminate")


# --- 화면 -----------------------------------------------------------------------------------------


def test_panel_shows_jira_delivery_lines_but_not_skipped(conn, cycle, client):
    source_id = add_project(conn, **(STATES | {"status_on_review": "<b>리뷰</b>"}))
    work = _work(conn, source_id)
    move(conn, work, "에이전트 작업 중")
    move(conn, work, "PR · 검토")
    jira = FakeJiraWriter()
    jira.moves = {"진행 중": "indeterminate"}
    deliver(conn, jira)  # start 는 대체(skipped), review 는 전환 없음(failed)
    move(conn, work, "완료")

    html = log_in(client).get("/work/RUN-1/panel").text

    assert "<b>리뷰</b>" not in html and "&lt;b&gt;리뷰&lt;/b&gt;" in html
    assert 'data-jira-delivery="failed"' in html and "반영 실패 · 전환 없음 — 현재 상태 대기" in html
    assert 'data-jira-delivery="pending"' in html and "Jira 상태 → 종료 · 반영 대기" in html
    assert 'data-jira-delivery="skipped"' not in html and "Jira 상태 → 진행 중" not in html


def test_connect_screen_counts_recent_failures(conn, work, client):
    move(conn, work, "에이전트 작업 중")
    jira = FakeJiraWriter()
    jira.moves = {}
    deliver(conn, jira)
    client.app.state.secrets.write(JIRA_API_TOKEN, "tok")

    html = log_in(client).get("/repos").text

    assert "Jira 반영 실패 1건" in html
    # 다시 연결하면 그 뒤의 실패만 센다
    repo.save_jira_connection(conn, FACTS, session_id=SESSION, now=LATER)
    assert "Jira 반영 실패" not in log_in(client).get("/repos").text
    assert repo.jira_delivery_failures(conn, SESSION) == {}


# --- 후속 이슈 등록 (step 8) -----------------------------------------------------------------------
# 후속 규칙이 새 업무를 만들면 원인 Jira 프로젝트에 이슈를 만들고, 새 업무의 원본 칸을 채우고, 원인 이슈와 잇는다.

PUBLIC = "https://runloom.example"


class FakeJiraCreator:
    """이슈 만들기·라벨 찾기·링크. `lose` 면 이슈는 만들고 응답을 잃는다(연결 오류). `failures[메서드]` 는 하나씩 꺼낸다."""

    def __init__(self, link_types=("Blocks", "relates")):
        self.created: list[dict] = []
        self.links: list[tuple[str, str, str]] = []
        self.link_types = list(link_types)
        self.failures: dict[str, list[Exception]] = {}
        self.calls: list[str] = []
        self.lose = False
        self._next = 100

    def _fail(self, method: str) -> None:
        self.calls.append(method)
        queue = self.failures.get(method)
        if queue:
            raise queue.pop(0)

    def create_issue(self, project_id, issue_type_id, summary, description, labels) -> JiraIssueRef:
        self._fail("create_issue")
        ref = JiraIssueRef(issue_id=str(10000 + self._next), key=f"SHOP-{self._next}")
        self._next += 1
        self.created.append({"project_id": project_id, "issue_type_id": issue_type_id, "summary": summary,
                             "description": description, "labels": list(labels), "ref": ref})
        if self.lose:
            self.lose = False
            raise JiraUnavailable("POST /rest/api/3/issue: ReadTimeout")
        return ref

    def find_issues_by_label(self, project_id, label) -> list[JiraIssueRef]:
        self._fail("find_issues_by_label")
        assert project_id == "10000"
        return [c["ref"] for c in self.created if label in c["labels"]]

    def issue_link_types(self) -> list[str]:
        self._fail("issue_link_types")
        return self.link_types

    def link_issues(self, type_name, *, inward_issue_id, outward_issue_id) -> None:
        self._fail("link_issues")
        self.links.append((type_name, inward_issue_id, outward_issue_id))

    def __getattr__(self, name):  # 후속 이슈 등록은 다른 Jira API 를 부르지 않는다
        raise AssertionError(f"후속 이슈 등록이 {name} 를 불렀다")


def _spawn(conn, client, worker, source_id, placement="new_work") -> tuple[str, str]:
    """Jira 이슈 SHOP-1 → 맡기기 → 수정 실행 → 그 실행이 원인인 후속(code_review). (원인 업무, 후속 단계의 업무)."""
    jira = FakeJira()
    jira.put(jissue(1))
    task_id = _imported(conn, source_id, jira)
    assert log_in(client).post("/work/RUN-1/assignee", data={"assignee": f"agent:{FIX}"},
                               follow_redirects=False).status_code == 303
    (execution,) = executions(conn, task_id)
    spec = FollowupTaskSpec(session_id=SESSION, kind="code_review", cause_execution_id=execution["execution_id"],
                            predecessor_task_id=task_id, rules_revision=1, placement=placement)
    followup_task, _ = worker._create_followup_task(conn, repo.get_task(conn, task_id), spec, NOW)
    return (repo.work_item_of_task(conn, task_id)["work_item_id"],
            repo.work_item_of_task(conn, followup_task)["work_item_id"])


@pytest.fixture
def spawned(conn, cycle, client, worker) -> tuple[str, str]:
    return _spawn(conn, client, worker, add_project(conn, followup_issue_type="Task"))


def creates(conn, work_item_id: str) -> list:
    return [r for r in rows(conn, work_item_id) if r["action"] == "create_issue"]


def all_creates(conn) -> list:
    return conn.execute("SELECT * FROM jira_deliveries WHERE action = 'create_issue'").fetchall()


def send(conn, jira, now: str = NOW):
    return deliver_jira_updates(conn, lambda _session: jira, now, public_url=PUBLIC)


def test_new_work_followup_of_jira_work_queues_one_create_row(conn, spawned, worker):
    cause, new = spawned
    assert new != cause
    (row,) = creates(conn, new)
    assert (row["moment"], row["target"], row["cause_issue_id"], row["state"], row["attempts"]) == (
        None, "Task", "10001", "pending", 0)
    assert row["dedupe_key"] == f"create_issue:{new}" and row["source_id"] == repo.get_work_item(
        conn, SESSION, cause)["source_id"]
    work = repo.get_work_item(conn, SESSION, new)
    # 원본 칸: 종류·프로젝트는 원인 것, 키·주소는 이슈를 만든 뒤에야 — 원인 SHOP-1 을 복사하지 않는다
    assert (work["source_type"], work["source_item_id"], work["source_key"], work["source_url"]) == (
        "jira", None, None, None)
    assert creates(conn, cause) == []


def test_same_work_followup_creates_no_issue(conn, cycle, client, worker):
    _, same = _spawn(conn, client, worker, add_project(conn, followup_issue_type="Task"), placement="same_work")
    assert all_creates(conn) == []
    assert repo.get_work_item(conn, SESSION, same)["source_key"] == "SHOP-1"  # 같은 업무 그대로


def test_blank_followup_type_or_disabled_project_creates_no_issue(conn, cycle, client, worker):
    _, new = _spawn(conn, client, worker, add_project(conn, followup_issue_type=None))
    assert all_creates(conn) == []
    assert repo.get_work_item(conn, SESSION, new)["source_key"] is None


def test_disabled_project_creates_no_issue(conn, cycle, client, worker):
    source_id = add_project(conn, followup_issue_type="Task")
    jira = FakeJira()
    jira.put(jissue(1))
    task_id = _imported(conn, source_id, jira)
    repo.update_jira_project(conn, SESSION, source_id, now=NOW, github_source_id=SOURCE, issue_types=[],
                             status_on_start=None, status_on_review=None, status_on_done=None,
                             followup_issue_type="Task", enabled=False)
    work = repo.work_item_of_task(conn, task_id)["work_item_id"]
    assert repo.queue_jira_issue_creation(conn, work, work, now=NOW) is False
    assert all_creates(conn) == []


def test_github_cause_creates_no_issue_and_keeps_copying_its_key(conn, cycle, worker):
    task_id = import_issue(conn, 1)
    worker.tick()
    (execution,) = executions(conn, task_id)
    spec = FollowupTaskSpec(session_id=SESSION, kind="code_review", cause_execution_id=execution["execution_id"],
                            predecessor_task_id=task_id, rules_revision=1, placement="new_work")
    followup_task, _ = worker._create_followup_task(conn, repo.get_task(conn, task_id), spec, NOW)
    work = repo.work_item_of_task(conn, followup_task)
    assert all_creates(conn) == []
    assert (work["source_type"], work["source_key"]) == ("github", "acme/billing#1")


def test_create_issue_fills_the_new_work_and_links_the_cause(conn, spawned):
    cause, new = spawned
    jira = FakeJiraCreator()

    report = send(conn, jira)

    assert report.delivered == 1
    (created,) = jira.created
    work = repo.get_work_item(conn, SESSION, new)
    assert (created["project_id"], created["issue_type_id"], created["summary"]) == ("10000", "10002", work["title"])
    assert created["labels"] == ["runloom", "runloom-RUN-2"]
    assert created["description"] == markdown_to_adf(followup_description(
        work_key="RUN-2", cause_key="SHOP-1", request=work["request"], work_url=f"{PUBLIC}{work_path('RUN-2')}"))
    assert created["description"]["type"] == "doc"
    assert (work["source_item_id"], work["source_key"], work["source_url"]) == (
        "10100", "SHOP-100", f"{SITE}/browse/SHOP-100")
    (row,) = creates(conn, new)
    assert (row["state"], row["result_issue_id"], row["result_issue_key"], row["attempts"]) == (
        "delivered", "10100", "SHOP-100", 1)
    assert row["delivered_at"] == NOW and row["note"] is None
    assert jira.links == [("relates", "10001", "10100")]  # Jira 가 준 이름 그대로, 원인 → 새 이슈
    # 다시 돌려도 만들지 않는다
    assert send(conn, jira).any() is False and len(jira.created) == 1


def test_created_issue_joins_the_same_work_on_the_next_sync(conn, spawned):
    cause, new = spawned
    send(conn, FakeJiraCreator())
    source_id = repo.get_work_item(conn, SESSION, cause)["source_id"]
    before = len(jira_works(conn))
    jira = FakeJira()
    jira.put(jissue(100, summary="Jira 쪽 제목", labels=["runloom", "runloom-RUN-2"], issue_type="Task",
                    updated="2026-10-06T05:00:00Z"))

    result = sync_project(conn, jira, source_id, NOW)

    assert result.created == [] and len(jira_works(conn)) == before
    row = repo.get_jira_issue(conn, SESSION, source_id, "10100")
    assert row["delegated_by"] == "followup"
    assert repo.work_item_of_task(conn, row["task_id"])["work_item_id"] == new


def test_without_a_relates_link_type_the_issue_still_counts(conn, spawned):
    _, new = spawned
    jira = FakeJiraCreator(link_types=("Blocks",))
    assert send(conn, jira).delivered == 1
    (row,) = creates(conn, new)
    assert (row["state"], row["note"]) == ("delivered", "링크 유형 Relates 없음 — 링크 생략")
    assert jira.links == [] and "link_issues" not in jira.calls
    assert repo.get_work_item(conn, SESSION, new)["source_key"] == "SHOP-100"


def test_link_failure_is_only_noted(conn, spawned):
    _, new = spawned
    jira = FakeJiraCreator()
    jira.failures["link_issues"] = [JiraForbidden("POST /rest/api/3/issueLink: HTTP 403", 403)]
    assert send(conn, jira).delivered == 1
    (row,) = creates(conn, new)
    assert (row["state"], row["note"]) == ("delivered", "링크 실패 — 권한 없음")
    assert len(jira.created) == 1


def test_lost_response_is_reconciled_by_label_and_created_once(conn, spawned):
    _, new = spawned
    jira = FakeJiraCreator()
    jira.lose = True

    report = send(conn, jira)

    assert report.uncertain == 1 and len(jira.created) == 1
    (row,) = creates(conn, new)
    assert (row["state"], row["attempts"], row["last_error"]) == ("unknown", 1, "Jira 응답 없음 — 다시 시도")
    assert row["next_at"] == "2026-10-06T12:00:30.000000Z"
    assert repo.get_work_item(conn, SESSION, new)["source_item_id"] is None
    assert send(conn, jira).any() is False  # 물러난 동안은 보지 않는다

    report = send(conn, jira, now=LATER)

    assert report.delivered == 1 and len(jira.created) == 1  # 라벨로 찾았다 — 두 번 만들지 않는다
    assert jira.calls.count("find_issues_by_label") == 1 and jira.calls.count("create_issue") == 1
    (row,) = creates(conn, new)
    assert (row["state"], row["result_issue_key"]) == ("delivered", "SHOP-100")
    assert repo.get_work_item(conn, SESSION, new)["source_key"] == "SHOP-100"
    assert jira.links == [("relates", "10001", "10100")]


def test_unknown_without_a_labelled_issue_posts_in_the_same_round(conn, spawned):
    _, new = spawned
    jira = FakeJiraCreator()
    jira.failures["create_issue"] = [JiraUnavailable("POST /rest/api/3/issue: HTTP 502", 502)]  # 닿지 않았다
    send(conn, jira)
    assert creates(conn, new)[0]["state"] == "unknown" and jira.created == []

    assert send(conn, jira, now=LATER).delivered == 1
    assert jira.calls == ["create_issue", "find_issues_by_label", "create_issue", "issue_link_types", "link_issues"]
    assert len(jira.created) == 1


def test_unknown_lookup_failure_stays_unknown(conn, spawned):
    _, new = spawned
    jira = FakeJiraCreator()
    jira.lose = True
    send(conn, jira)
    jira.failures["find_issues_by_label"] = [JiraUnavailable("GET /rest/api/3/search/jql: HTTP 503", 503)]
    assert send(conn, jira, now=LATER).uncertain == 1
    (row,) = creates(conn, new)
    assert (row["state"], row["attempts"]) == ("unknown", 2)
    assert jira.calls.count("create_issue") == 1


@pytest.mark.parametrize(("failure", "message"), [
    (JiraBadRequest("POST /rest/api/3/issue: HTTP 400", 400, "Components is required."),
     "필수 칸 — Components is required. — Jira 프로젝트 설정 확인"),
    (JiraForbidden("POST /rest/api/3/issue: HTTP 403", 403), "권한 없음 — 이슈를 만들 수 없음"),
    (JiraNotFound("POST /rest/api/3/issue: HTTP 404", 404), "프로젝트를 찾을 수 없음"),
])
def test_rejected_creation_fails_with_a_reason(conn, spawned, failure, message):
    _, new = spawned
    jira = FakeJiraCreator()
    jira.failures["create_issue"] = [failure]
    assert send(conn, jira).failed == 1
    (row,) = creates(conn, new)
    assert (row["state"], row["last_error"]) == ("failed", message)
    assert send(conn, jira, now=LATER).any() is False and jira.calls == ["create_issue"]  # 다시 보내지 않는다
    assert repo.get_work_item(conn, SESSION, new)["source_item_id"] is None


def test_unknown_issue_type_fails_without_calling_jira(conn, cycle, client, worker):
    source_id = add_project(conn, followup_issue_type="Task")
    _, new = _spawn(conn, client, worker, source_id)
    repo.set_jira_choices(conn, SESSION, source_id, CHOICES.model_copy(update={"issue_types": CHOICES.issue_types[:1]}),
                          now=NOW)
    jira = FakeJiraCreator()
    assert send(conn, jira).failed == 1
    assert creates(conn, new)[0]["last_error"] == "이슈 유형 Task 없음 — 목록 새로 고침" and jira.calls == []


def test_unauthorized_and_rate_limit_keep_the_create_pending(conn, spawned):
    _, new = spawned
    jira = FakeJiraCreator()
    jira.failures["create_issue"] = [JiraRateLimited("POST /rest/api/3/issue: HTTP 429", 429, 120)]
    report = send(conn, jira)
    (row,) = creates(conn, new)
    assert report.rate_limited and (row["state"], row["next_at"]) == ("pending", "2026-10-06T12:02:00.000000Z")

    jira.failures["create_issue"] = [JiraUnauthorized("POST /rest/api/3/issue: HTTP 401", 401)]
    send(conn, jira, now=LATER)
    (row,) = creates(conn, new)
    assert row["state"] == "pending" and repo.get_jira_connection(conn, SESSION)["auth_failed_at"] == LATER
    assert jira.created == []


def test_sync_reconciles_a_labelled_issue_before_the_delivery_does(conn, spawned):
    """POST 는 닿았는데 기록 전에 죽었다 — 가져오기가 라벨로 같은 업무에 붙이고 생성 행을 끝낸다(새 업무 없음)."""
    cause, new = spawned
    source_id = repo.get_work_item(conn, SESSION, cause)["source_id"]
    before = len(jira_works(conn))
    jira = FakeJira()
    jira.put(jissue(100, labels=["runloom", "runloom-RUN-2"], issue_type="Task", updated="2026-10-06T05:00:00Z"))
    jira.put(jissue(101, labels=["runloom-RUN-77"], updated="2026-10-06T05:01:00Z"))  # 그런 업무 없음 — 건너뜀

    result = sync_project(conn, jira, source_id, NOW)

    assert result.created == [] and len(jira_works(conn)) == before
    work = repo.get_work_item(conn, SESSION, new)
    assert (work["source_item_id"], work["source_key"], work["source_url"]) == (
        "10100", "SHOP-100", f"{SITE}/browse/SHOP-100")
    assert repo.get_jira_issue(conn, SESSION, source_id, "10100")["delegated_by"] == "followup"
    assert repo.get_jira_issue(conn, SESSION, source_id, "10101") is None
    (row,) = creates(conn, new)
    assert (row["state"], row["result_issue_id"], row["result_issue_key"], row["note"]) == (
        "delivered", "10100", "SHOP-100", "가져오기로 조정")
    creator = FakeJiraCreator()
    assert send(conn, creator).any() is False and creator.calls == []


def test_labelled_issue_for_a_filled_or_other_work_is_not_taken(conn, spawned):
    cause, new = spawned
    send(conn, FakeJiraCreator())  # RUN-2 는 이미 SHOP-100
    source_id = repo.get_work_item(conn, SESSION, cause)["source_id"]
    before = len(jira_works(conn))
    jira = FakeJira()
    jira.put(jissue(150, labels=["runloom-RUN-2"], updated="2026-10-06T05:00:00Z"))  # 사람이 라벨을 복사한 이슈
    jira.put(jissue(151, labels=["runloom-RUN-1"], updated="2026-10-06T05:01:00Z"))  # 원인 업무(이미 원본 있음)
    assert sync_project(conn, jira, source_id, NOW).created == []
    assert len(jira_works(conn)) == before
    assert repo.get_work_item(conn, SESSION, new)["source_item_id"] == "10100"


def test_panel_shows_the_followup_issue_line(conn, spawned, client):
    _, new = spawned
    send(conn, FakeJiraCreator())
    html = log_in(client).get("/work/RUN-2/panel").text
    assert 'data-jira-delivery="delivered"' in html and "Jira 후속 이슈 만들기 → SHOP-100 · 반영됨" in html


def test_worker_passes_the_public_url(conn, spawned, settings, store, clock):
    _, new = spawned
    jira = FakeJiraCreator()
    worker = jira_worker(dataclasses.replace(settings, public_url=PUBLIC), store, clock, jira)
    worker._sync_jira = lambda conn, report: None
    worker.tick()
    (created,) = jira.created
    assert f"{PUBLIC}{work_path('RUN-2')}" in json.dumps(created["description"], ensure_ascii=False)
