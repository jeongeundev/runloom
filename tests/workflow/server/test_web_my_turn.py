# ruff: noqa: F811 — test_task_cycle 픽스처(cycle·worker)를 가져와 인자로 쓴다
"""맡긴 사람·응답자·사람별 "내 차례" — phase 15 step 8 (ADR-0021, ARCHITECTURE "맡긴 사람과 '내 차례' 받는 사람").

받는 사람 = 담당 활성 멤버 → 맡긴 사람(활성) → 활성 관리자 전원. 저장하지 않고 계산한다. 홈 `GET /tasks?view=my_turn` 은
로그인한 멤버가 받는 사람인 `내 차례` 업무만. 응답은 누구나(`respond`) 하고 응답자가 남는다.
"""

import re

import pytest
from fastapi.testclient import TestClient

from workflow.adapters import repo
from workflow.server.auth import LOGIN_COOKIE, utc_now

from .conftest import log_in, log_in_member
from .test_task_cycle import (  # noqa: F401 — 픽스처
    NOW,
    SESSION,
    SOURCE,
    _append,
    clock,
    config,
    cycle,
    executions,
    fail_fix,
    import_issue,
    make_worker,
    settings,
    worker,
)


@pytest.fixture
def admin(client, cycle):
    """관리자 B — 고정 워크스페이스의 첫 관리자."""
    return log_in(client)


@pytest.fixture
def requester(app, admin):
    return log_in_member(TestClient(app), email="a@example.com", display_name="김맡김")


@pytest.fixture
def other(app, admin):
    return log_in_member(TestClient(app), email="c@example.com", display_name="이담당")


def member_id(conn, client) -> str:
    return repo.member_for_login_token(conn, client.cookies[LOGIN_COOKIE], now=utc_now())["member_id"]


def work_id(conn, task_id: str) -> str:
    return repo.work_item_of_task(conn, task_id)["work_item_id"]


def my_turn(client) -> str:
    response = client.get("/tasks?view=my_turn")
    assert response.status_code == 200, response.text
    return response.text.split('class="main', 1)[1]


def my_turn_count(client) -> int:
    return int(re.search(r'data-view="my_turn"[^>]*>내 차례 <span[^>]*>(\d+)</span>', client.get("/tasks").text).group(1))


def delegate_and_fail(conn, worker, client, number: int = 1) -> str:
    """all_open 소스의 이슈를 `client` 가 맡기고, 실행이 실패해 `내 차례` 가 된 상태. 원본 이슈 Task id."""
    repo.save_github_source(conn, SESSION, config(intake="all_open", label_filter=[], trigger_label="runloom"), NOW)
    task_id = import_issue(conn, number, labels=[])
    assert client.post(f"/tasks/{task_id}/delegate", follow_redirects=False).status_code == 303
    execution_id = executions(conn, task_id)[0]["execution_id"]
    _append(conn, execution_id, 1, "accepted", {})
    _append(conn, execution_id, 2, "failed", {"code": "timeout", "message": "20분 초과", "process_stopped": True})
    worker.tick()
    assert repo.work_item_of_task(conn, task_id)["status"] == "내 차례"
    return task_id


def respond(client, conn, task_id: str, action: str = "retry"):
    (request,) = [r for r in repo.list_human_requests(conn, task_id) if r["state"] == "open"]
    body = {"response_id": f"resp-{action}-{member_id(conn, client)}", "expected_revision": request["revision"],
            "action": action, "text": ""}
    return client.post(f"/human-requests/{request['request_id']}/responses", json=body)


def test_delegated_work_is_on_the_requesters_turn_only(admin, requester, conn, worker):
    delegate_and_fail(conn, worker, requester)
    assert 'data-work-key="RUN-1"' in my_turn(requester)
    assert 'data-work-key="RUN-1"' not in my_turn(admin)  # 관리자 B 에게는 안 보인다
    assert (my_turn_count(requester), my_turn_count(admin)) == (1, 0)
    assert 'data-work-key="RUN-1"' in admin.get("/tasks").text.split('class="main', 1)[1]  # 전체에는 있다
    detail = admin.get("/work/RUN-1").text
    assert re.search(r"받는 사람:?\s*김맡김", detail)


def test_member_assignee_takes_the_turn(admin, requester, other, conn, worker):
    task_id = delegate_and_fail(conn, worker, requester)
    repo.assign_work_item(conn, SESSION, work_id(conn, task_id), assignee_type="member",
                          assignee_id=member_id(conn, other), now=NOW)
    assert 'data-work-key="RUN-1"' in my_turn(other)
    assert 'data-work-key="RUN-1"' not in my_turn(requester)


def test_disabled_requester_falls_back_to_every_admin(app, admin, requester, conn, worker):
    delegate_and_fail(conn, worker, requester)
    second = log_in_member(TestClient(app), "admin", email="b2@example.com", display_name="박관리")
    repo.disable_member(conn, SESSION, member_id(conn, requester), now=NOW)
    assert 'data-work-key="RUN-1"' in my_turn(admin)
    assert 'data-work-key="RUN-1"' in my_turn(second)


def test_collected_only_failure_goes_to_every_admin(admin, requester, conn, store, worker):
    fail_fix(conn, store, worker)  # 수집·자동 시작만 — 맡긴 사람 없음
    assert repo.work_item_of_task(conn, "task-gh-1")["requested_by_member_id"] is None
    assert 'data-work-key="RUN-1"' in my_turn(admin)
    assert 'data-work-key="RUN-1"' not in my_turn(requester)
    assert "받는 사람" in admin.get("/work/RUN-1").text


def test_retry_by_anyone_records_the_responder_and_becomes_the_requester(admin, requester, other, conn, worker):
    task_id = delegate_and_fail(conn, worker, requester)
    response = respond(other, conn, task_id)  # 받는 사람이 아니어도 응답한다
    assert response.status_code == 200, response.text
    (answer,) = repo.list_human_responses(conn, task_id)
    assert answer["member_id"] == member_id(conn, other)
    assert repo.work_item_of_task(conn, task_id)["requested_by_member_id"] == member_id(conn, other)
    detail = admin.get("/work/RUN-1").text
    assert re.search(r"data-responder[^>]*>\s*이담당", detail)
    assert "이담당" in admin.get(f"/tasks/{task_id}").text  # 단계 상세의 응답 기록에도


def test_close_by_admin_records_the_responder_and_keeps_the_requester(admin, requester, conn, worker):
    task_id = delegate_and_fail(conn, worker, requester)
    assert respond(admin, conn, task_id, "close").status_code == 200
    assert repo.list_human_responses(conn, task_id)[0]["member_id"] == member_id(conn, admin)
    assert repo.work_item_of_task(conn, task_id)["requested_by_member_id"] == member_id(conn, requester)
    assert 'data-work-key="RUN-1"' not in my_turn(requester)  # 종료 — 더는 내 차례가 아니다


def test_pressing_delegate_again_makes_the_latest_member_the_requester(admin, requester, conn):
    repo.save_github_source(conn, SESSION, config(intake="all_open", label_filter=[], trigger_label="runloom"), NOW)
    task_id = import_issue(conn, 1, labels=[])
    assert requester.post(f"/tasks/{task_id}/delegate", follow_redirects=False).status_code == 303
    assert admin.post(f"/tasks/{task_id}/delegate", follow_redirects=False).status_code == 303
    assert repo.work_item_of_task(conn, task_id)["requested_by_member_id"] == member_id(conn, admin)


def test_unknown_view_is_the_whole_list(admin, requester, conn, worker):
    delegate_and_fail(conn, worker, requester)
    assert 'data-work-key="RUN-1"' in admin.get("/tasks?view=nope").text.split('class="main', 1)[1]
