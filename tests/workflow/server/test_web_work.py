# ruff: noqa: F811 — test_task_cycle 픽스처(cycle·settings)를 가져와 인자로 쓴다
"""업무 담당·우선순위 라우트 — phase 16 step 3 (ARCHITECTURE "업무 화면 — phase 16" 주소 표).

`POST /work/{key}/assignee`·`POST /work/{key}/priority` 는 `delegate` 동작(관리자·멤버), 다른 Origin 은 403, 성공하면
`/tasks?open=<key>`(+ 기본값이 아닌 목록 상태)로 303. 폼 값은 열거형·id 로만 받는다.
"""

import pytest
from fastapi.testclient import TestClient

from workflow.adapters import repo

from .conftest import log_in, log_in_member, log_in_other_workspace
from .test_task_cycle import (  # noqa: F401 — 픽스처
    FIX,
    NOW,
    REVIEW,
    SESSION,
    config,
    cycle,
    executions,
    import_issue,
    settings,
)


@pytest.fixture
def issue_task(conn, cycle) -> str:
    """지시 전 이슈 하나(RUN-1) — all_open 소스."""
    repo.save_github_source(conn, SESSION, config(review_agent_id=REVIEW, intake="all_open", label_filter=[],
                                                  trigger_label="runloom"), NOW)
    return import_issue(conn, 1, labels=[])


@pytest.fixture
def admin(client, issue_task) -> TestClient:
    return log_in(client)


def work(conn, task_id: str):
    return repo.work_item_of_task(conn, task_id)


def error(response, status: int, code: str) -> None:
    assert response.status_code == status, response.text
    assert f"<code>{code}</code>" in response.text, response.text


def test_member_assigns_an_agent_and_is_sent_to_the_open_work(app, admin, conn, issue_task):
    member = log_in_member(TestClient(app))
    response = member.post("/work/RUN-1/assignee", data={"assignee": f"agent:{FIX}"}, follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == "/tasks?open=RUN-1"
    assert (work(conn, issue_task)["assignee_type"], work(conn, issue_task)["assignee_id"]) == ("agent", FIX)
    assert len(executions(conn, issue_task)) == 1


def test_priority_route(admin, conn, issue_task):
    response = admin.post("/work/RUN-1/priority", data={"priority": "high"}, follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == "/tasks?open=RUN-1"
    assert work(conn, issue_task)["priority"] == "high"


def test_redirect_keeps_normalized_list_state(admin, conn, issue_task):
    form = {"priority": "low", "q": "unassigned", "group": "status", "view": "board", "closed": "all"}
    response = admin.post("/work/RUN-1/priority", data=form, follow_redirects=False)
    assert response.headers["location"] == "/tasks?q=unassigned&group=status&view=board&closed=all&open=RUN-1"
    # 모르는 값은 기본값 — 주소에 붙지 않는다. 되돌아갈 URL 은 받지 않는다
    form = {"priority": "high", "q": "https://evil.example", "view": "nope", "return_to": "https://evil.example"}
    response = admin.post("/work/RUN-1/priority", data=form, follow_redirects=False)
    assert response.headers["location"] == "/tasks?open=RUN-1"


def test_login_is_required(client, issue_task, conn):
    for path, data in (("/work/RUN-1/assignee", {"assignee": "none"}), ("/work/RUN-1/priority", {"priority": "high"})):
        response = client.post(path, data=data, follow_redirects=False)
        assert response.status_code == 303 and response.headers["location"] == "/login"
    assert work(conn, issue_task)["priority"] == "normal"


def test_other_workspace_login_is_not_logged_in(app, admin, issue_task):
    stranger = log_in_other_workspace(TestClient(app))
    response = stranger.post("/work/RUN-1/priority", data={"priority": "high"}, follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == "/login"


def test_other_origin_is_refused(admin, conn, issue_task):
    for path, data in (("/work/RUN-1/assignee", {"assignee": f"agent:{FIX}"}),
                       ("/work/RUN-1/priority", {"priority": "high"})):
        response = admin.post(path, data=data, headers={"Origin": "http://evil.example", "Accept": "text/html"},
                              follow_redirects=False)
        assert response.status_code == 403 and "forbidden_origin" in response.text
    assert work(conn, issue_task)["priority"] == "normal"
    assert executions(conn, issue_task) == []


@pytest.mark.parametrize("path, data", [
    ("/work/RUN-1/assignee", {"assignee": "robot:x"}),
    ("/work/RUN-1/assignee", {}),
    ("/work/RUN-1/assignee", {"assignee": f"agent:{REVIEW}"}),
    ("/work/RUN-1/assignee", {"assignee": "member:mem-nope"}),
    ("/work/RUN-1/priority", {"priority": "urgent"}),
    ("/work/RUN-1/priority", {}),
])
def test_invalid_values_are_422(admin, path, data):
    error(admin.post(path, data=data), 422, "invalid_field")


@pytest.mark.parametrize("key", ["RUN-99", "RUN-0", "abc", "OPS-1"])
def test_unknown_key_is_404(admin, key):
    error(admin.post(f"/work/{key}/assignee", data={"assignee": "none"}), 404, "not_found")
    error(admin.post(f"/work/{key}/priority", data={"priority": "high"}), 404, "not_found")


def test_conflicts_are_409(admin, conn, issue_task):
    assert admin.post("/work/RUN-1/assignee", data={"assignee": f"agent:{FIX}"}).status_code == 200  # 따라감
    error(admin.post("/work/RUN-1/assignee", data={"assignee": "none"}), 409, "execution_conflict")
