"""라우트·템플릿 권한 = 역할 × 동작 표 (phase 15 step 6, ADR-0021, ARCHITECTURE "팀 — phase 15" 라우트별 필요 동작).

관리자 전용 경로는 멤버에게 403 forbidden(화면 HTML·API JSON), 관리자는 통과, 미로그인은 화면 303 `/login`·API 401.
`sessions.is_operator` 는 권한 판정에 읽지 않는다. 관리자 메뉴·설정 폼은 멤버에게 숨긴다.
"""

import pytest
from fastapi.testclient import TestClient

from workflow.adapters import repo

from .conftest import SESSION, log_in, log_in_member
from .test_github_sync import config

NOW = "2026-10-06T12:00:00Z"
SOURCE = config().source_id

# (메서드, 경로, 본문 종류, 본문) — 본문 종류 form = 화면, json = API
ADMIN_PAGES = [
    ("POST", "/kinds", {"kind": "x"}),
    ("POST", "/kinds/custom_kind/delete", {}),
    ("POST", "/rules", {}),
    ("POST", "/rules/rule-x/delete", {}),
    ("POST", "/sources/tokens", {"label": "n8n"}),
    ("POST", "/sources/tokens/tok-x/revoke", {}),
    ("GET", "/operator/github/app/new", None),
    ("GET", "/operator/github/app/callback?code=x&state=forged", None),
    ("GET", "/operator/github/app/setup?installation_id=42&state=forged", None),
    ("POST", "/operator/github/token", {}),
    ("POST", "/operator/agents", {}),
    ("POST", "/operator/agents/agent-x/delete", {}),
    ("POST", "/operator/connect-codes/CODE-X/revoke", {}),
    ("GET", "/settings?tab=notify", None),
    ("GET", "/settings?tab=triage", None),
    ("GET", "/settings?tab=inbound", None),
    ("POST", "/operator/notifications/webhook", {"url": "https://hooks.example.com/x"}),
    ("POST", "/operator/notifications/webhook/delete", {}),
    ("POST", "/operator/notifications/test", {}),
]
ADMIN_APIS = [
    ("POST", "/github/sources/preview", {}),
    ("POST", "/github/sources", {}),
    ("PUT", f"/github/sources/{SOURCE}", {}),
    ("POST", f"/github/sources/{SOURCE}/stop", {}),
    ("PUT", f"/github/sources/{SOURCE}/assignees/1", {}),
    ("POST", f"/operator/github/sources/{SOURCE}/baseline", None),
    ("PUT", "/field-mappings", {"mappings": []}),
]
# phase 23: 팀·저장소·설정 화면 — 멤버가 여는 화면·탭(설정의 판단·알림·n8n 입구 탭만 관리자)
MEMBER_PAGES = ["/tasks", "/tasks/new", "/team", "/repos", "/settings?tab=kinds", "/settings?tab=advanced",
                "/monitor"]
MEMBER_APIS = ["/github/sources", "/human-requests", "/field-mappings", "/metrics.json", "/metrics.csv"]
# phase 23 사이드바: 업무 · 모니터링(`view_metrics`, /monitor) · 팀 · 저장소 · 설정 · 내 설정(`edit_own_settings`) — 관리자 전용
# 화면(입구·알림)은 사이드바가 아니라 설정 화면 탭으로 간다. 옛 `연결` 메뉴는 없다
ADMIN_LINKS = ('href="/sources"', 'href="/operator/notifications"', 'href="/connect"')
MEMBER_LINKS = ('href="/tasks"', 'href="/monitor"', 'href="/team"', 'href="/repos"', 'href="/settings"', 'href="/me"')


def sidebar_of(html: str) -> str:
    return html[html.index('class="sidebar'):html.index('class="main')]


def send(client: TestClient, method: str, path: str, body):
    """화면 — 폼 본문(GET 은 없음)."""
    return client.request(method, path, data=body, follow_redirects=False)


def send_json(client: TestClient, method: str, path: str, body):
    """API — JSON 본문(None 이면 본문 없음)."""
    return client.request(method, path, json=body, follow_redirects=False)


@pytest.fixture
def admin(app, conn):
    client = log_in(TestClient(app))
    repo.save_github_source(conn, SESSION, config(), NOW)
    return client


@pytest.fixture
def member(app, admin):
    return log_in_member(TestClient(app))


def forbidden_page(response) -> bool:
    return response.status_code == 403 and "<code>forbidden</code>" in response.text \
        and "관리자 권한이 필요합니다." in response.text


def not_blocked(response) -> bool:
    """역할 판정을 통과했다 — 로그인 요구·forbidden 이 아니다(뒤의 입력 검사·404 는 상관없다)."""
    if response.status_code in (303, 307) and response.headers.get("location") == "/login":
        return False
    if response.status_code == 401:
        return False
    if response.headers.get("content-type", "").startswith("application/json"):
        return response.json().get("code") != "forbidden"
    return "<code>forbidden</code>" not in response.text


# --- 관리자 전용 경로 --------------------------------------------------------------------------------


@pytest.mark.parametrize(("method", "path", "body"), ADMIN_PAGES)
def test_member_is_forbidden_on_admin_pages(member, method, path, body):
    assert forbidden_page(send(member, method, path, body)), (method, path)


@pytest.mark.parametrize(("method", "path", "body"), ADMIN_APIS)
def test_member_is_forbidden_on_admin_apis(member, method, path, body):
    response = send_json(member, method, path, body)
    assert response.status_code == 403, (method, path, response.text)
    assert response.json()["code"] == "forbidden"
    assert response.json()["message"] == "관리자 권한이 필요합니다."


@pytest.mark.parametrize(("method", "path", "body"), ADMIN_PAGES)
def test_admin_passes_admin_pages(admin, method, path, body):
    assert not_blocked(send(admin, method, path, body)), (method, path)


@pytest.mark.parametrize(("method", "path", "body"), ADMIN_APIS)
def test_admin_passes_admin_apis(admin, method, path, body):
    assert not_blocked(send_json(admin, method, path, body)), (method, path)


@pytest.mark.parametrize(("method", "path", "body"), ADMIN_PAGES)
def test_anonymous_page_redirects_to_login(app, admin, method, path, body):
    response = send(TestClient(app), method, path, body)
    assert (response.status_code, response.headers.get("location")) == (303, "/login"), (method, path)


@pytest.mark.parametrize(("method", "path", "body"), ADMIN_APIS)
def test_anonymous_api_is_401(app, admin, method, path, body):
    response = send_json(TestClient(app), method, path, body)
    assert response.status_code == 401 and response.json()["code"] == "unauthenticated", (method, path)


def test_member_changes_nothing_on_admin_routes(member, conn):
    kinds = [k.kind for k in repo.list_kinds(conn, SESSION)]
    send(member, "POST", "/kinds", {"kind": "member_kind", "label": "멤버 종류"})
    send(member, "POST", "/sources/tokens", {"label": "n8n"})
    send(member, "POST", "/operator/notifications/webhook", {"url": "https://hooks.example.com/x"})
    assert [k.kind for k in repo.list_kinds(conn, SESSION)] == kinds
    assert repo.list_source_tokens(conn, SESSION) == []
    assert not member.app.state.secrets.exists("notify_webhook_url")


def test_admin_role_gates_do_not_read_is_operator(admin, conn):
    conn.execute("UPDATE sessions SET is_operator = 0 WHERE session_id = ?", (SESSION,))
    conn.commit()
    for path in ("/settings?tab=notify", "/settings?tab=inbound", "/repos", "/monitor"):
        response = admin.get(path, follow_redirects=False)
        assert response.status_code == 200, path
    assert 'id="inbound-url"' in admin.get("/settings?tab=inbound").text  # 입구 탭 = `manage_connections`
    sidebar = sidebar_of(admin.get("/tasks").text)
    for link in MEMBER_LINKS:
        assert link in sidebar, link


# --- 멤버가 쓰는 경로 --------------------------------------------------------------------------------


@pytest.mark.parametrize("path", MEMBER_PAGES)
def test_member_opens_login_pages(member, path):
    response = member.get(path, follow_redirects=False)
    assert response.status_code == 200, (path, response.text)


@pytest.mark.parametrize("path", MEMBER_APIS)
def test_member_reads_login_apis(member, path):
    response = member.get(path, follow_redirects=False)
    assert response.status_code == 200, (path, response.text)


def test_member_gets_a_connect_code_from_attach_runner(member, conn):
    response = member.post(f"/operator/github/sources/{SOURCE}/runner", follow_redirects=False)

    assert response.status_code == 200, response.text
    (row,) = repo.list_connect_codes(conn)
    assert f"--code {row['code']}" in response.text


def test_member_issues_a_connect_code_on_operator_page(member, conn):
    response = member.post("/operator/connect-codes", follow_redirects=False)

    assert response.status_code == 200, response.text
    (row,) = repo.list_connect_codes(conn)
    assert row["code"] in response.text


# --- 템플릿 — 관리자 메뉴·폼 숨김 ------------------------------------------------------------------------


def test_member_sidebar_hides_admin_links(member):
    text = sidebar_of(member.get("/tasks").text)
    for link in ADMIN_LINKS:
        assert link not in text, link
    for link in MEMBER_LINKS:
        assert link in text, link


def test_admin_sidebar_shows_every_nav_item(admin):
    text = sidebar_of(admin.get("/tasks").text)
    for link in MEMBER_LINKS:
        assert link in text, link
    for link in ADMIN_LINKS:  # 관리자 화면은 설정 화면 탭에서 간다
        assert link not in text, link


def test_member_kinds_page_hides_setting_forms(member, admin):
    assert 'action="/kinds"' in admin.get("/settings?tab=kinds").text and 'action="/rules"' in admin.get("/settings?tab=kinds").text
    text = member.get("/settings?tab=kinds").text
    assert 'action="/kinds"' not in text and 'action="/rules"' not in text
    assert 'name="agent_ids"' not in text and "data-kind-advanced" not in text  # 종류 폼 전체가 없다
    assert "/delete" not in text


def test_member_operator_page_shows_runner_but_not_agent_forms(member, admin):
    admin.post("/operator/connect-codes")
    admin_text = admin.get("/settings?tab=advanced").text
    assert 'action="/operator/agents"' in admin_text and "/revoke" in admin_text

    text = member.get("/settings?tab=advanced").text
    assert 'action="/operator/connect-codes"' in text
    assert 'action="/operator/agents"' not in text and "/operator/agents/" not in text
    assert "/revoke" not in text  # 관리자가 발급한 코드는 멤버에게 보이지 않는다(자기 발급분만 — step 10)


def test_member_github_page_shows_runner_but_not_connection_forms(member, admin):
    admin_text = admin.get("/repos").text
    for marker in ("/operator/github/token", f'data-json-action="/github/sources/{SOURCE}"',
                   f"/operator/github/sources/{SOURCE}/baseline", f"/github/sources/{SOURCE}/stop"):
        assert marker in admin_text, marker

    text = member.get("/repos").text
    assert f'action="/operator/github/sources/{SOURCE}/runner"' in text
    for marker in ("/operator/github/app/new", "/operator/github/token", f'data-json-action="/github/sources/{SOURCE}"',
                   f"/operator/github/sources/{SOURCE}/baseline", f"/github/sources/{SOURCE}/stop",
                   f"/github/sources/{SOURCE}/assignees"):
        assert marker not in text, marker


def test_member_metrics_page_hides_baseline_import(member, admin):
    assert f"/operator/github/sources/{SOURCE}/baseline" in admin.get("/monitor").text
    assert f"/operator/github/sources/{SOURCE}/baseline" not in member.get("/monitor").text
