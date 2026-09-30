"""시작하기 `/start` (phase 16 step 7, ARCHITECTURE "업무 화면 — phase 16" 시작하기).

항목 4개의 완료는 DB 에서 계산한다(`repo.start_facts`). 필수 셋이 끝나면 사이드바에서 "시작하기" 를 숨기지만 주소는 열린다.
그 항목의 동작이 없는 멤버에게는 버튼 대신 "관리자에게 요청".
"""

import re

from fastapi.testclient import TestClient

from workflow.adapters import repo

from .conftest import EXEC_FIX, NOW, SESSION, TASK_A, log_in, log_in_member, seed_agents, seed_execution, task_row
from .test_web_work_list import sidebar_of

ASK_ADMIN = "관리자에게 요청"


def items_of(html: str) -> dict[str, tuple[str, str]]:
    """항목 키 → (상태, 항목 HTML)."""
    found = re.findall(r'<li class="start-item" data-start-item="([a-z]+)" data-state="([a-z]+)">(.*?)</li>',
                       html, re.S)
    return {key: (state, body) for key, state, body in found}


def states_of(html: str) -> dict[str, str]:
    return {key: state for key, (state, _) in items_of(html).items()}


def page(client: TestClient) -> str:
    response = client.get("/start")
    assert response.status_code == 200, response.text
    return response.text


def test_start_needs_login(client):
    response = client.get("/start", follow_redirects=False)
    assert (response.status_code, response.headers["location"]) == (303, "/login")


def test_empty_workspace_first_item_is_next(client):
    admin = log_in(client)
    html = page(admin)
    assert list(items_of(html)) == ["source", "runner", "invite", "delegate"]
    assert states_of(html) == {"source": "next", "runner": "todo", "invite": "optional", "delegate": "todo"}
    items = items_of(html)
    assert 'href="/connect?tab=sources"' in items["source"][1]
    assert 'href="/connect?tab=sources"' in items["runner"][1]
    assert 'href="/connect?tab=team"' in items["invite"][1]
    assert 'href="/tasks?q=unassigned"' in items["delegate"][1]
    assert ASK_ADMIN not in html  # 관리자는 모두 할 수 있다
    for label in ("가져올 곳 연결", "러너 붙이기", "팀원 초대", "첫 업무 맡기기"):
        assert label in html
    for word in ("다음", "선택"):
        assert word in html


def test_items_complete_step_by_step_and_sidebar_hides_when_required_done(client, conn):
    admin = log_in(client)
    assert 'href="/start"' in sidebar_of(admin.get("/tasks").text)

    repo.issue_source_token(conn, SESSION, "n8n", "", NOW)
    assert states_of(page(admin)) == {"source": "done", "runner": "next", "invite": "optional", "delegate": "todo"}

    repo.exchange_connect_code(conn, repo.issue_connect_code(conn, NOW), NOW)
    assert states_of(page(admin)) == {"source": "done", "runner": "done", "invite": "optional", "delegate": "next"}
    assert 'href="/start"' in sidebar_of(admin.get("/tasks").text)

    seed_agents(conn)
    repo.insert_work_item_task(conn, task_row(TASK_A), NOW)
    seed_execution(conn, EXEC_FIX, TASK_A)
    html = page(admin)  # 주소는 필수 완료 뒤에도 열린다
    assert states_of(html) == {"source": "done", "runner": "done", "invite": "optional", "delegate": "done"}
    assert 'href="/start"' not in sidebar_of(html)  # 선택 항목(초대)이 남아도 숨긴다
    assert "시작하기" not in sidebar_of(admin.get("/tasks").text)


def test_optional_invite_does_not_complete_required(client, conn):
    admin = log_in(client)
    admin_id = repo.find_member_by_email(conn, SESSION, "admin@example.com")["member_id"]
    repo.issue_invite(conn, SESSION, role="member", created_by_member_id=admin_id, now=NOW)
    html = page(admin)
    assert states_of(html) == {"source": "next", "runner": "todo", "invite": "done", "delegate": "todo"}
    assert 'href="/start"' in sidebar_of(html)


def test_sidebar_orders_start_between_connect_and_settings_and_marks_active(client):
    admin = log_in(client)
    sidebar = sidebar_of(page(admin))
    nav = re.findall(r'<a href="([^"]+)"[^>]*>([^<]+)', sidebar[sidebar.index('class="nav"'):])
    assert [href for href, _ in nav[:5]] == ["/tasks", "/monitor", "/connect", "/start", "/me"]
    assert [label.strip() for _, label in nav[:5]] == ["업무", "모니터링", "연결", "시작하기", "내 설정"]
    assert '<a href="/start" class="active">' in sidebar


def test_member_gets_ask_admin_for_admin_only_items(app, client):
    log_in(client)
    member = log_in_member(TestClient(app))  # 초대로 가입 — 초대 항목은 완료
    items = items_of(page(member))
    assert items["source"][0] == "next"
    assert ASK_ADMIN in items["source"][1] and 'href="/connect?tab=sources"' not in items["source"][1]
    assert items["invite"][0] == "done"
    assert ASK_ADMIN in items["invite"][1] and 'href="/connect?tab=team"' not in items["invite"][1]
    # 러너 붙이기(attach_runner)·맡기기(delegate)는 멤버도 할 수 있다
    assert ASK_ADMIN not in items["runner"][1] and 'href="/connect?tab=sources"' in items["runner"][1]
    assert ASK_ADMIN not in items["delegate"][1] and 'href="/tasks?q=unassigned"' in items["delegate"][1]
