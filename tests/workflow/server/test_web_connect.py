"""연결 화면 `/connect?tab=…` (phase 16 step 6, ADR-0022, ARCHITECTURE "업무 화면 — phase 16" 연결 화면 탭).

탭 5개(가져올 곳·팀·담당자·업무 종류·규칙·알림·고급)는 옛 설정 화면 7개의 본문을 그대로 옮긴 것이다 — 문구·폼·`data-*` 유지.
권한 없는 탭은 머리에서 숨기고 직접 열면 403. 옛 GET 은 새 탭으로 303, POST 경로는 그대로이고 성공 뒤 303 대상만 새 탭.
"""

import re

import pytest
from fastapi.testclient import TestClient

from workflow.adapters import repo

from .conftest import NOW, SESSION, log_in, log_in_member, seed_agents
from .test_github_sync import config

SOURCE = config().source_id
TABS = ["sources", "team", "kinds", "triage", "notify", "advanced"]  # 판단 탭은 phase 19 step 8
TAB_LABELS = ["가져올 곳", "팀·담당자", "업무 종류·규칙", "판단", "알림", "고급"]
OLD_GETS = [
    ("/sources", "/connect?tab=sources"),
    ("/operator/github", "/connect?tab=sources"),
    ("/operator", "/connect?tab=advanced"),
    ("/operator/notifications", "/connect?tab=notify"),
    ("/team", "/connect?tab=team"),
    ("/agents", "/connect?tab=team"),
    ("/kinds", "/connect?tab=kinds"),
]


@pytest.fixture
def admin(app, conn):
    client = log_in(TestClient(app))
    repo.save_github_source(conn, SESSION, config(), NOW)
    seed_agents(conn)
    return client


@pytest.fixture
def member(app, admin):
    return log_in_member(TestClient(app))


def tabs_of(html: str) -> list[tuple[str, str]]:
    """탭 머리의 (tab 키, 이름) — 나온 순서대로."""
    head = html[html.index("data-connect-tabs"):]
    head = head[:head.index("</nav>")]
    return re.findall(r'<a href="/connect\?tab=[a-z]+" data-tab="([a-z]+)"[^>]*>([^<]+)</a>', head)


def active_tab(html: str) -> str:
    (key,) = re.findall(r'data-tab="([a-z]+)" class="active"', html)
    return key


def body_of(html: str) -> str:
    return html[html.index("data-tab-body"):html.index("</main>")] if "</main>" in html else \
        html[html.index("data-tab-body"):]


# --- 탭 머리·권한 -----------------------------------------------------------------------------------


def test_admin_sees_six_tabs_in_order(admin):
    html = admin.get("/connect").text
    assert tabs_of(html) == list(zip(TABS, TAB_LABELS, strict=True))
    assert active_tab(html) == "sources"  # 기본 탭


def test_member_does_not_see_notify_tab_and_gets_403_directly(member):
    html = member.get("/connect").text
    assert [key for key, _ in tabs_of(html)] == ["sources", "team", "kinds", "advanced"]

    response = member.get("/connect?tab=notify", follow_redirects=False)
    assert response.status_code == 403 and "<code>forbidden</code>" in response.text
    assert "관리자 권한이 필요합니다." in response.text


@pytest.mark.parametrize("tab", TABS)
def test_each_tab_opens_as_active(admin, tab):
    response = admin.get(f"/connect?tab={tab}", follow_redirects=False)
    assert response.status_code == 200
    assert active_tab(response.text) == tab
    assert f'data-tab-body="{tab}"' in response.text


def test_unknown_tab_is_the_first_visible_tab(admin, member):
    assert active_tab(admin.get("/connect?tab=nope").text) == "sources"
    assert active_tab(member.get("/connect?tab=").text) == "sources"


def test_anonymous_goes_to_login(app, admin):
    response = TestClient(app).get("/connect?tab=team", follow_redirects=False)
    assert (response.status_code, response.headers["location"]) == (303, "/login")


def test_sidebar_links_connect_and_marks_it_active(admin):
    html = admin.get("/connect?tab=kinds").text
    nav = html[html.index('class="nav"'):html.index("</nav>")]
    assert '<a href="/connect" class="active">연결</a>' in nav
    assert '<a href="/connect">연결</a>' in admin.get("/tasks").text
    assert "/operator/github" not in admin.get("/tasks").text  # 에이전트 0 안내도 연결 탭으로(여기선 에이전트가 있다)


# --- 탭 본문 — 옮긴 핵심 요소 -------------------------------------------------------------------------


def test_sources_tab_has_github_and_n8n_for_admin(admin):
    body = body_of(admin.get("/connect?tab=sources").text)
    for marker in (f'data-source-card="{SOURCE}"', f'action="/operator/github/sources/{SOURCE}/runner"',
                   'href="/operator/github/app/new"', 'action="/operator/github/token"',
                   f'data-json-action="/operator/github/sources/{SOURCE}/baseline"',
                   'id="inbound-url"', 'action="/sources/tokens"'):
        assert marker in body, marker
    assert "사람 응답 대기" not in body  # 업무 화면(내 차례)이 대신한다


def test_sources_tab_for_member_hides_connection_forms_and_n8n(member):
    body = body_of(member.get("/connect?tab=sources").text)
    assert f'action="/operator/github/sources/{SOURCE}/runner"' in body
    for marker in ("/operator/github/app/new", "/operator/github/token", 'id="inbound-url"', "/sources/tokens"):
        assert marker not in body, marker


def test_team_tab_has_members_agents_and_runners(admin):
    body = body_of(admin.get("/connect?tab=team").text)
    for marker in ('action="/team/invites"', "data-member-id=", 'href="/agents/agent-codex-mac"', "연결된 러너 없음"):
        assert marker in body, marker


def test_team_tab_for_member_hides_members_and_invites(member):
    body = body_of(member.get("/connect?tab=team").text)
    assert 'href="/agents/agent-codex-mac"' in body and "연결된 러너 없음" in body
    assert "/team/invites" not in body and "data-member-id=" not in body


def test_kinds_tab_forms_follow_manage_rules(admin, member):
    admin_body = body_of(admin.get("/connect?tab=kinds").text)
    assert 'action="/kinds"' in admin_body and 'action="/rules"' in admin_body
    body = body_of(member.get("/connect?tab=kinds").text)
    assert "업무 종류" in body and 'action="/kinds"' not in body and 'action="/rules"' not in body


def test_notify_tab_has_webhook_form(admin):
    body = body_of(admin.get("/connect?tab=notify").text)
    assert 'data-notify-configured="false"' in body and 'action="/operator/notifications/webhook"' in body


def test_advanced_tab_has_connect_codes_and_agent_forms(admin, member):
    admin_body = body_of(admin.get("/connect?tab=advanced").text)
    assert 'action="/operator/connect-codes"' in admin_body and 'action="/operator/agents"' in admin_body
    assert "모든 세션 업무" not in admin_body  # 업무 화면(전체)이 대신한다
    body = body_of(member.get("/connect?tab=advanced").text)
    assert 'action="/operator/connect-codes"' in body and 'action="/operator/agents"' not in body


# --- 옛 GET → 새 탭 ------------------------------------------------------------------------------------


@pytest.mark.parametrize(("old", "new"), OLD_GETS)
def test_old_get_redirects_to_new_tab(admin, old, new):
    response = admin.get(f"{old}?x=1", follow_redirects=False)
    assert (response.status_code, response.headers["location"]) == (303, new)  # 쿼리는 버린다


def test_unchanged_paths_stay(admin):
    assert admin.get("/agents/agent-codex-mac", follow_redirects=False).status_code == 200
    forged = admin.get("/operator/github/app/callback?code=x&state=forged", follow_redirects=False)
    assert forged.status_code == 403 and "github_state_invalid" in forged.text
    setup = admin.get("/operator/github/app/setup?installation_id=42&state=forged", follow_redirects=False)
    assert setup.status_code == 403 and "github_state_invalid" in setup.text
    new = admin.get("/operator/github/app/new", follow_redirects=False)  # 테스트 주소는 루프백이 아니라 공개 주소 요구
    assert new.status_code == 400 and "public_url_required" in new.text


# --- POST 경로 그대로 · 성공 뒤 새 탭 --------------------------------------------------------------------


def post(client: TestClient, path: str, data: dict | None = None):
    return client.post(path, data=data or {}, follow_redirects=False)


def test_kind_and_rule_posts_redirect_to_kinds_tab(admin):
    created = post(admin, "/kinds", {"kind": "triage_note", "label": "분류 메모", "scope_key": "repository_id",
                                     "outcomes": "done"})
    assert (created.status_code, created.headers["location"]) == (303, "/connect?tab=kinds")
    deleted = post(admin, "/kinds/triage_note/delete")
    assert (deleted.status_code, deleted.headers["location"]) == (303, "/connect?tab=kinds")


def test_source_token_issue_renders_sources_tab_and_revoke_redirects(admin, conn):
    issued = post(admin, "/sources/tokens", {"label": "n8n"})
    assert issued.status_code == 200 and active_tab(issued.text) == "sources"
    assert 'id="issued-token"' in issued.text
    (token,) = repo.list_source_tokens(conn, SESSION)
    revoked = post(admin, f"/sources/tokens/{token['token_id']}/revoke")
    assert (revoked.status_code, revoked.headers["location"]) == (303, "/connect?tab=sources")


def test_attach_runner_renders_sources_tab(admin):
    response = post(admin, f"/operator/github/sources/{SOURCE}/runner")
    assert response.status_code == 200 and active_tab(response.text) == "sources"
    assert "data-runner-command" in response.text


def test_invite_renders_team_tab_and_revoke_redirects(admin, conn):
    issued = post(admin, "/team/invites", {"role": "member"})
    assert issued.status_code == 200 and active_tab(issued.text) == "team"
    assert 'data-issued="invite"' in issued.text
    invite_id = re.search(r'data-invite-id="([^"]+)"', issued.text).group(1)
    revoked = post(admin, f"/team/invites/{invite_id}/revoke")
    assert (revoked.status_code, revoked.headers["location"]) == (303, "/connect?tab=team")


def test_notification_posts_go_to_notify_tab(admin):
    saved = post(admin, "/operator/notifications/webhook", {"url": "https://hooks.example.com/x"})
    assert (saved.status_code, saved.headers["location"]) == (303, "/connect?tab=notify")
    deleted = post(admin, "/operator/notifications/webhook/delete")
    assert (deleted.status_code, deleted.headers["location"]) == (303, "/connect?tab=notify")


def test_connect_code_issue_renders_advanced_tab_and_revoke_redirects(admin, conn):
    issued = post(admin, "/operator/connect-codes")
    assert issued.status_code == 200 and active_tab(issued.text) == "advanced"
    (row,) = repo.list_connect_codes(conn)
    assert f'<code id="issued-code">{row["code"]}</code>' in issued.text
    revoked = post(admin, f"/operator/connect-codes/{row['code']}/revoke")
    assert (revoked.status_code, revoked.headers["location"]) == (303, "/connect?tab=advanced")


def test_agent_register_redirects_to_advanced_tab(admin):
    response = post(admin, "/operator/agents", {
        "agent_id": "agent-manual", "name": "수동", "owner_scope": "personal", "connection_type": "local",
        "capability_code": "code.fix", "scope_value": "demo-report-repo", "local_registration_id": "local-x",
    })
    assert (response.status_code, response.headers["location"]) == (303, "/connect?tab=advanced")
    deleted = post(admin, "/operator/agents/agent-manual/delete")
    assert (deleted.status_code, deleted.headers["location"]) == (303, "/connect?tab=advanced")
