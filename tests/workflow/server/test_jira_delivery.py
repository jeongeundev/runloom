# ruff: noqa: F811 — test_task_cycle·test_jira_sync 픽스처(cycle·settings·worker·project)를 가져와 인자로 쓴다
"""jira_delivery — 세 순간 → outbox(`jira_deliveries`) → 전환 전송 (phase 18 step 7, ADR-0024 결정 11·12,
ARCHITECTURE "Jira 소스 — phase 18" 상태 옮기기·오류 분류·화면).

실제 Jira 를 부르지 않는다 — `FakeJiraWriter` 가 `JiraClient` 의 `issue_status`·`transitions`·`transition` 을 흉내 낸다.
"""

import inspect
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
from workflow.contracts.jira import JiraTransition
from workflow.domain.work_status import WorkStatus
from workflow.server.jira_delivery import MAX_ATTEMPTS, deliver_jira_updates
from workflow.server.worker import Worker

from .conftest import log_in
from .test_jira_sync import FACTS, FakeJira, _imported, add_project, jissue
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
    deliver(conn, jira, now="2026-10-06T12:00:30Z")
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

    html = log_in(client).get("/connect?tab=sources").text

    assert "Jira 반영 실패 1건" in html
    # 다시 연결하면 그 뒤의 실패만 센다
    repo.save_jira_connection(conn, FACTS, session_id=SESSION, now=LATER)
    assert "Jira 반영 실패" not in log_in(client).get("/connect?tab=sources").text
    assert repo.jira_delivery_failures(conn, SESSION) == {}
