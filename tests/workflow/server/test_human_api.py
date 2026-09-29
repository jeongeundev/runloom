"""human_api.py — 운영자 사람 요청 목록·응답 API (phase 8 step 11, ADR-0014 결정 7, CONTRACT 13.9·13.11).

응답 권한은 운영자 세션뿐이다 — GitHub 담당자·공개 세션은 응답하지 못한다. 응답은 요청을 닫고 Task revision 을 올릴 뿐
실행을 만들지 않는다(재평가·착수는 워커, test_task_cycle). 재전송은 `response_id` 로, 경쟁은 `expected_revision` 으로 막는다.
"""

import pytest
from fastapi.testclient import TestClient

from workflow.adapters import repo
from workflow.server.auth import SESSION_COOKIE, sign_session

from .conftest import task_row
from .test_github_api import login
from .test_task_cycle import FIX, FIX_SHOP, SESSION, cycle, import_issue, settings  # noqa: F401 — 픽스처

NOW = "2026-10-06T12:00:00Z"


@pytest.fixture
def op(app, conn, cycle) -> TestClient:  # noqa: F811
    """cycle 워크스페이스(고정 워크스페이스)에 로그인한 클라이언트 — 셀프호스트는 로그인 = 운영자."""
    client = TestClient(app)
    assert login(client) == SESSION
    return client


@pytest.fixture
def request_id(conn, cycle) -> str:  # noqa: F811
    task_id = import_issue(conn, 1)
    request_id, _ = repo.create_human_request_once(
        conn, task_id, "fix_needs_information", "재현 금액이 필요합니다", "fix_needs_information:exec-1", NOW,
    )
    return request_id


def respond(client: TestClient, request_id: str, **overrides):
    body = {"response_id": "resp-1", "expected_revision": 1, "action": "resume", "text": "10,000원", **overrides}
    return client.post(f"/human-requests/{request_id}/responses", json=body)


def test_operator_lists_open_requests_and_answers_once(op, conn, request_id):
    listed = op.get("/human-requests")
    assert listed.status_code == 200
    (item,) = listed.json()["requests"]
    assert (item["request_id"], item["task_id"], item["code"], item["revision"], item["state"]) == (
        request_id, "task-gh-1", "fix_needs_information", 1, "open",
    )

    response = respond(op, request_id)
    assert response.status_code == 200, response.text
    assert response.json() == {"request_id": request_id, "task_id": "task-gh-1", "response_id": "resp-1",
                               "task_revision": 2, "created": True}
    assert repo.list_executions(conn, "task-gh-1") == []  # 응답은 실행을 만들지 않는다
    assert op.get("/human-requests").json()["requests"] == []

    # 응답 유실 뒤 재전송은 같은 결과, revision 은 다시 오르지 않는다
    again = respond(op, request_id)
    assert again.status_code == 200
    assert (again.json()["task_revision"], again.json()["created"]) == (2, False)
    assert repo.get_task(conn, "task-gh-1")["revision"] == 2


def test_resend_with_other_content_conflicts(op, request_id):
    respond(op, request_id)
    conflict = respond(op, request_id, text="20,000원")
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "response_conflict"


def test_stale_revision_and_already_answered_requests_are_rejected(op, request_id):
    stale = respond(op, request_id, expected_revision=5)
    assert stale.status_code == 409
    assert stale.json() == {"code": "stale_request", "message": f"사람 요청 {request_id} 가 이미 revision 1 입니다.",
                            "field": "expected_revision", "details": {"current_revision": 1}}
    respond(op, request_id)
    past = respond(op, request_id, response_id="resp-2")  # 이미 응답된 과거 요청
    assert past.status_code == 409
    assert past.json()["details"] == {"current_revision": 2}


def test_only_the_operator_session_can_answer(app, conn, request_id):
    anonymous = TestClient(app)
    assert respond(anonymous, request_id).status_code == 401
    assert anonymous.get("/human-requests").status_code == 401
    stranger = TestClient(app)  # 워크스페이스가 아닌 세션 행을 서명한 쿠키 — 로그인 안 된 것으로 본다
    repo.create_session(conn, "sess-other", NOW)
    stranger.cookies.set(SESSION_COOKIE, sign_session("sess-other", "test-session-secret"))
    assert respond(stranger, request_id).status_code == 401
    assert stranger.get("/human-requests").status_code == 401
    assert repo.get_human_request(conn, SESSION, request_id)["state"] == "open"


def test_other_operator_session_cannot_see_or_answer(op, conn):
    # 다른 워크스페이스(운영자 세션)의 업무와 사람 요청 — DB 에 직접 둔다
    repo.create_session(conn, "sess-other", NOW)
    repo.mark_operator(conn, "sess-other")
    repo.insert_work_item_task(conn, {**task_row("task-other"), "session_id": "sess-other"}, NOW)
    other_request, _ = repo.create_human_request_once(
        conn, "task-other", "fix_needs_information", "재현 금액이 필요합니다", "fix_needs_information:exec-9", NOW,
    )
    assert op.get("/human-requests").json()["requests"] == []
    response = respond(op, other_request)
    assert response.status_code == 404
    assert response.json()["code"] == "not_found"
    assert repo.get_human_request(conn, "sess-other", other_request)["state"] == "open"


def test_information_request_needs_an_actual_answer(op, conn, request_id):
    blank = respond(op, request_id, text="   ")
    assert blank.status_code == 422
    assert (blank.json()["code"], blank.json()["field"]) == ("invalid_field", "text")
    assert repo.get_task(conn, "task-gh-1")["revision"] == 1


def test_action_must_fit_the_request(op, conn, request_id):
    wrong = respond(op, request_id, action="choose_agent", agent_id=FIX)
    assert wrong.status_code == 422
    assert (wrong.json()["code"], wrong.json()["field"]) == ("invalid_field", "action")
    unknown = respond(op, request_id, action="approve")
    assert unknown.status_code == 422


def test_choose_agent_needs_a_session_agent(op, conn, cycle):  # noqa: F811
    task_id = import_issue(conn, 2)
    request_id, _ = repo.create_human_request_once(conn, task_id, "assignee_multiple", "누구?",
                                                   "ready:assignee_multiple:r1", NOW)
    missing = respond(op, request_id, action="choose_agent", text="")
    assert (missing.status_code, missing.json()["field"]) == (422, "agent_id")
    stranger = respond(op, request_id, action="choose_agent", text="", agent_id="agent-nobody")
    assert (stranger.status_code, stranger.json()["code"]) == (422, "agent_not_registered")
    ok = respond(op, request_id, action="choose_agent", text="", agent_id=FIX_SHOP)
    assert ok.status_code == 200
    # 등록 Agent 면 기록한다 — 담당자 연결·능력 검사는 워커의 재평가가 한다(자동 권한 부여 없음)
    assert repo.get_task(conn, task_id)["chosen_agent_id"] == FIX_SHOP


def test_close_ends_the_task_and_later_answers_see_it_closed(op, conn, request_id):
    other, _ = repo.create_human_request_once(conn, "task-gh-1", "decision", "q", "decision:1", NOW)
    closed = respond(op, request_id, action="close", text="")
    assert closed.status_code == 200
    task = repo.get_task(conn, "task-gh-1")
    assert (task["status"], task["status_reason"]) == ("실패", "운영자 종료 — 사람 요청 응답")
    late = respond(op, other, text="늦은 답")
    assert (late.status_code, late.json()["code"]) == (409, "task_closed")


def test_allowed_actions_per_request_code_are_shared_with_the_response_form():
    from workflow.server.human_api import allowed_actions, asks_information

    assert allowed_actions("assignee_multiple") == {"choose_agent", "close"}
    assert allowed_actions("rework_limit_reached") == {"resume", "close"}
    assert asks_information("fix_needs_information") and not asks_information("delegation_denied")
