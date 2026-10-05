"""팀·저장소·설정 화면과 옛 `/connect` 주소 (phase 23 step 1, ADR-0028, ARCHITECTURE "설정 UX — phase 23" 주소·사이드바).

phase 16 의 연결 화면 탭 6개를 `/team`(팀·담당자) · `/repos`(GitHub·Jira) · `/settings?tab=…`(종류·판단·알림·n8n 입구·고급)로
자리만 옮긴다 — 절의 문구·폼·`data-*` 는 그대로. 권한 없는 설정 탭은 머리에서 숨기고 직접 열면 403. `/connect`·옛 GET 은 새
주소로 303, POST 경로는 그대로이고 성공 뒤 303 대상만 새 주소.
"""

import re

import pytest
from fastapi.testclient import TestClient

from workflow.adapters import repo
from workflow.domain import team

from .conftest import NOW, SESSION, log_in, log_in_member, seed_agents
from .test_github_sync import config

SOURCE = config().source_id
TABS = ["kinds", "triage", "notify", "inbound", "advanced"]
TAB_LABELS = ["업무 종류·규칙", "판단", "알림", "n8n 입구", "고급"]
CONNECT_TARGETS = [
    ("/connect", "/repos"),
    ("/connect?tab=sources", "/repos"),
    ("/connect?tab=team", "/team"),
    ("/connect?tab=kinds", "/settings?tab=kinds"),
    ("/connect?tab=triage", "/settings?tab=triage"),
    ("/connect?tab=triage&version=2", "/settings?tab=triage&version=2"),
    ("/connect?tab=notify", "/settings?tab=notify"),
    ("/connect?tab=advanced", "/settings?tab=advanced"),
    ("/connect?tab=nope", "/repos"),
    ("/connect?tab=team&version=2&x=1", "/team"),
]
OLD_GETS = [
    ("/sources", "/settings?tab=inbound"),
    ("/operator/github", "/repos"),
    ("/operator", "/settings?tab=advanced"),
    ("/operator/notifications", "/settings?tab=notify"),
    ("/agents", "/team"),
    ("/kinds", "/settings?tab=kinds"),
]
NAV_ADMIN = [("/tasks", "업무"), ("/requests", "받은·보낸 요청"), ("/monitor", "모니터링"), ("/team", "팀"),
             ("/repos", "저장소"), ("/settings", "설정"), ("/start", "시작하기"), ("/me", "내 설정")]


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
    """설정 탭 머리의 (tab 키, 이름) — 나온 순서대로."""
    head = html[html.index("data-settings-tabs"):]
    head = head[:head.index("</nav>")]
    return re.findall(r'<a href="/settings\?tab=[a-z]+" data-tab="([a-z]+)"[^>]*>([^<]+)</a>', head)


def active_tab(html: str) -> str:
    (key,) = re.findall(r'data-tab="([a-z]+)" class="active"', html)
    return key


def nav_of(html: str) -> str:
    return html[html.index('class="nav"'):html.index("</nav>")]


def nav_items(html: str) -> list[tuple[str, str]]:
    return [(href, re.sub(r"<[^>]+>.*", "", label))
            for href, label in re.findall(r'<a href="([^"]+)"[^>]*>(.*?)</a>', nav_of(html))]


def active_nav(html: str) -> list[str]:
    return re.findall(r'<a href="([^"]+)" class="active"', nav_of(html))


def main_of(html: str) -> str:
    return html[html.index("<main"):html.index("</main>")]


def body_of(html: str) -> str:
    return html[html.index("data-tab-body"):html.index("</main>")]


# --- 사이드바 ---------------------------------------------------------------------------------------


def test_admin_sidebar_items_in_order(admin):
    assert nav_items(admin.get("/tasks").text) == NAV_ADMIN  # 시작하기는 필수 항목이 남아 보인다
    assert "/connect" not in nav_of(admin.get("/tasks").text)


def test_member_sidebar_has_team_repos_settings(member):
    assert nav_items(member.get("/tasks").text) == NAV_ADMIN


def test_role_without_view_metrics_has_no_monitor(member, monkeypatch):
    monkeypatch.setitem(team._ROLE_ACTIONS, "member", team._ROLE_ACTIONS["member"] - {team.VIEW_METRICS})
    hrefs = [href for href, _ in nav_items(member.get("/tasks").text)]
    assert hrefs == ["/tasks", "/requests", "/team", "/repos", "/settings", "/start", "/me"]


@pytest.mark.parametrize(("path", "active"), [
    ("/tasks", "/tasks"),
    ("/team", "/team"),
    ("/agents/agent-codex-mac", "/team"),
    ("/repos", "/repos"),
    ("/settings", "/settings"),
    ("/settings?tab=advanced", "/settings"),
    ("/requests", "/requests"),
    ("/monitor", "/monitor"),
    ("/start", "/start"),
    ("/me", "/me"),
])
def test_sidebar_active_by_path(admin, path, active):
    assert active_nav(admin.get(path).text) == [active]


def test_post_rendered_pages_mark_their_menu(admin):
    """POST 가 그린 화면도 맞는 메뉴가 활성이다(`nav` 문맥)."""
    assert active_nav(admin.post("/team/invites", data={"role": "member", "invitee_email": "n@example.com"}).text) == ["/team"]
    assert active_nav(admin.post("/sources/tokens", data={"label": "n8n"}).text) == ["/settings"]
    assert active_nav(admin.post(f"/operator/github/sources/{SOURCE}/runner").text) == ["/repos"]
    assert active_nav(admin.post("/operator/connect-codes").text) == ["/settings"]


# --- 설정 탭 머리·권한 --------------------------------------------------------------------------------


def test_admin_sees_five_settings_tabs_in_order(admin):
    html = admin.get("/settings").text
    assert tabs_of(html) == list(zip(TABS, TAB_LABELS, strict=True))
    assert active_tab(html) == "kinds"  # 기본 탭
    assert "data-connect-tabs" not in html


def test_member_sees_kinds_and_advanced_and_gets_403_directly(member):
    assert [key for key, _ in tabs_of(member.get("/settings").text)] == ["kinds", "advanced"]
    for tab in ("triage", "notify", "inbound"):
        response = member.get(f"/settings?tab={tab}", follow_redirects=False)
        assert response.status_code == 403 and "<code>forbidden</code>" in response.text
        assert "관리자 권한이 필요합니다." in response.text


@pytest.mark.parametrize("tab", TABS)
def test_each_settings_tab_opens_as_active(admin, tab):
    response = admin.get(f"/settings?tab={tab}", follow_redirects=False)
    assert response.status_code == 200
    assert active_tab(response.text) == tab
    assert f'data-tab-body="{tab}"' in response.text


def test_unknown_settings_tab_is_the_first_visible_tab(admin, member):
    assert active_tab(admin.get("/settings?tab=nope").text) == "kinds"
    assert active_tab(member.get("/settings?tab=").text) == "kinds"
    assert active_tab(admin.get("/settings?tab=sources").text) == "kinds"  # 옛 탭 이름도 모르는 탭


@pytest.mark.parametrize("path", ["/team", "/repos", "/settings?tab=kinds"])
def test_anonymous_goes_to_login(app, admin, path):
    response = TestClient(app).get(path, follow_redirects=False)
    assert (response.status_code, response.headers["location"]) == (303, "/login")


# --- 화면 본문 — 옮긴 핵심 요소 ---------------------------------------------------------------------------


def test_repos_has_github_and_jira_but_not_n8n_for_admin(admin):
    response = admin.get("/repos", follow_redirects=False)
    assert response.status_code == 200
    body = main_of(response.text)
    for marker in (f'data-source-card="{SOURCE}"', f'action="/operator/github/sources/{SOURCE}/runner"',
                   'href="/operator/github/app/new"', 'action="/operator/github/token"',
                   f'data-json-action="/operator/github/sources/{SOURCE}/baseline"',
                   'action="/operator/jira/connect"'):
        assert marker in body, marker
    assert 'id="inbound-url"' not in body and 'action="/sources/tokens"' not in body  # 설정 › n8n 입구로
    assert "data-settings-tabs" not in body and "data-tab-body" not in body


def test_repos_for_member_hides_connection_forms(member):
    body = main_of(member.get("/repos").text)
    assert f'action="/operator/github/sources/{SOURCE}/runner"' in body
    for marker in ("/operator/github/app/new", "/operator/github/token", "/operator/jira/connect"):
        assert marker not in body, marker


def test_inbound_tab_has_n8n_entry(admin):
    body = body_of(admin.get("/settings?tab=inbound").text)
    assert 'id="inbound-url"' in body and 'action="/sources/tokens"' in body
    assert "data-source-card" not in body


def test_team_page_has_members_agents_runners_and_directory(admin):
    response = admin.get("/team", follow_redirects=False)
    assert response.status_code == 200
    body = main_of(response.text)
    # phase 23 step 6: 러너는 에이전트 표에 합쳐졌다(옛 "연결된 러너 없음" 표 대신 에이전트 절)
    for marker in ('action="/team/invites"', "data-member-id=", 'href="/agents/agent-codex-mac"',
                   'data-team-section="agents"', 'action="/responsibilities/add"'):
        assert marker in body, marker
    assert "data-settings-tabs" not in body


def test_team_page_for_member_hides_members_and_invites(member):
    body = main_of(member.get("/team").text)
    assert 'href="/agents/agent-codex-mac"' in body and 'data-team-section="agents"' in body
    assert "/team/invites" not in body and "data-member-id=" not in body


def test_kinds_tab_forms_follow_manage_rules(admin, member):
    admin_body = body_of(admin.get("/settings?tab=kinds").text)
    assert 'action="/kinds"' in admin_body and 'action="/rules"' in admin_body
    body = body_of(member.get("/settings?tab=kinds").text)
    assert "업무 종류" in body and 'action="/kinds"' not in body and 'action="/rules"' not in body


def test_triage_version_link_points_to_settings(admin):
    body = body_of(admin.get("/settings?tab=triage").text)
    assert "/connect" not in body


def test_notify_tab_has_webhook_form(admin):
    body = body_of(admin.get("/settings?tab=notify").text)
    assert 'data-notify-configured="false"' in body and 'action="/operator/notifications/webhook"' in body


def test_advanced_tab_has_connect_codes_and_agent_forms(admin, member):
    admin_body = body_of(admin.get("/settings?tab=advanced").text)
    assert 'action="/operator/connect-codes"' in admin_body and 'action="/operator/agents"' in admin_body
    assert "모든 세션 업무" not in admin_body  # 업무 화면(전체)이 대신한다
    body = body_of(member.get("/settings?tab=advanced").text)
    assert 'action="/operator/connect-codes"' in body and 'action="/operator/agents"' not in body


def test_no_page_links_to_connect(admin):
    for path in ("/tasks", "/team", "/repos", "/start", "/requests", "/agents/agent-codex-mac", "/tasks/new",
                 *(f"/settings?tab={tab}" for tab in TABS)):
        assert 'href="/connect' not in admin.get(path).text, path


# --- /connect·옛 GET → 새 주소 ----------------------------------------------------------------------------


@pytest.mark.parametrize(("old", "new"), CONNECT_TARGETS)
def test_connect_redirects_to_new_address(admin, old, new):
    response = admin.get(old, follow_redirects=False)
    assert (response.status_code, response.headers["location"]) == (303, new)


def test_connect_redirects_without_login(app, admin):
    response = TestClient(app).get("/connect?tab=team", follow_redirects=False)
    assert (response.status_code, response.headers["location"]) == (303, "/team")


@pytest.mark.parametrize(("old", "new"), OLD_GETS)
def test_old_get_redirects_to_new_address(admin, old, new):
    response = admin.get(f"{old}?x=1", follow_redirects=False)
    assert (response.status_code, response.headers["location"]) == (303, new)  # 쿼리는 버린다


def test_team_is_a_page_not_a_redirect(admin):
    assert admin.get("/team?x=1", follow_redirects=False).status_code == 200


def test_unchanged_paths_stay(admin):
    assert admin.get("/agents/agent-codex-mac", follow_redirects=False).status_code == 200
    forged = admin.get("/operator/github/app/callback?code=x&state=forged", follow_redirects=False)
    assert forged.status_code == 403 and "github_state_invalid" in forged.text
    setup = admin.get("/operator/github/app/setup?installation_id=42&state=forged", follow_redirects=False)
    assert setup.status_code == 403 and "github_state_invalid" in setup.text
    new = admin.get("/operator/github/app/new", follow_redirects=False)  # 테스트 주소는 루프백이 아니라 공개 주소 요구
    assert new.status_code == 400 and "public_url_required" in new.text


# --- POST 경로 그대로 · 성공 뒤 새 주소 ---------------------------------------------------------------------


def post(client: TestClient, path: str, data: dict | None = None):
    return client.post(path, data=data or {}, follow_redirects=False)


def test_kind_and_rule_posts_redirect_to_kinds_tab(admin):
    created = post(admin, "/kinds", {"kind": "triage_note", "label": "분류 메모", "scope_key": "repository_id",
                                     "outcomes": "done"})
    assert (created.status_code, created.headers["location"]) == (303, "/settings?tab=kinds")
    deleted = post(admin, "/kinds/triage_note/delete")
    assert (deleted.status_code, deleted.headers["location"]) == (303, "/settings?tab=kinds")


def test_source_token_issue_renders_inbound_tab_and_revoke_redirects(admin, conn):
    issued = post(admin, "/sources/tokens", {"label": "n8n"})
    assert issued.status_code == 200 and active_tab(issued.text) == "inbound"
    assert 'id="issued-token"' in issued.text
    (token,) = repo.list_source_tokens(conn, SESSION)
    revoked = post(admin, f"/sources/tokens/{token['token_id']}/revoke")
    assert (revoked.status_code, revoked.headers["location"]) == (303, "/settings?tab=inbound")


def test_attach_runner_renders_repos(admin):
    response = post(admin, f"/operator/github/sources/{SOURCE}/runner")
    assert response.status_code == 200 and "data-runner-command" in response.text
    assert f'data-source-card="{SOURCE}"' in response.text and "data-settings-tabs" not in response.text


def test_invite_renders_team_and_revoke_redirects(admin, conn):
    issued = post(admin, "/team/invites", {"role": "member", "invitee_email": "n@example.com"})
    assert issued.status_code == 200 and 'data-issued="invite"' in issued.text
    assert 'action="/responsibilities/add"' in issued.text  # 팀 화면 그대로
    invite_id = re.search(r'data-invite-id="([^"]+)"', issued.text).group(1)
    revoked = post(admin, f"/team/invites/{invite_id}/revoke")
    assert (revoked.status_code, revoked.headers["location"]) == (303, "/team")


def test_reset_link_renders_team(admin, conn):
    member_id = repo.list_members(conn, SESSION)[0]["member_id"]
    issued = post(admin, f"/team/members/{member_id}/reset-link")
    assert issued.status_code == 200 and 'data-issued="reset"' in issued.text
    assert active_nav(issued.text) == ["/team"]


def test_responsibility_posts_redirect_to_team(admin, conn):
    member_id = repo.list_members(conn, SESSION)[0]["member_id"]
    added = post(admin, "/responsibilities/add", {
        "system_id": "kube_proxy", "request_kind": "investigation", "recipient_member_id": member_id,
        "judgment_member_id": member_id, "expected_revision": str(repo.get_config_revision(conn, SESSION))})
    assert (added.status_code, added.headers["location"]) == (303, "/team")
    removed = post(admin, "/responsibilities/remove", {
        "position": "0", "expected_revision": str(repo.get_config_revision(conn, SESSION))})
    assert (removed.status_code, removed.headers["location"]) == (303, "/team")


def test_delegation_policy_redirects_to_team(admin):
    response = post(admin, "/agents/agent-codex-mac/delegation-policy", {"policy": "run"})
    assert (response.status_code, response.headers["location"]) == (303, "/team")


def test_notification_posts_go_to_notify_tab(admin):
    saved = post(admin, "/operator/notifications/webhook", {"url": "https://hooks.example.com/x"})
    assert (saved.status_code, saved.headers["location"]) == (303, "/settings?tab=notify")
    deleted = post(admin, "/operator/notifications/webhook/delete")
    assert (deleted.status_code, deleted.headers["location"]) == (303, "/settings?tab=notify")


def test_connect_code_issue_renders_advanced_tab_and_revoke_redirects(admin, conn):
    issued = post(admin, "/operator/connect-codes")
    assert issued.status_code == 200 and active_tab(issued.text) == "advanced"
    (row,) = repo.list_connect_codes(conn)
    assert f'<code id="issued-code">{row["code"]}</code>' in issued.text
    revoked = post(admin, f"/operator/connect-codes/{row['code']}/revoke")
    assert (revoked.status_code, revoked.headers["location"]) == (303, "/settings?tab=advanced")


def test_agent_register_redirects_to_advanced_tab(admin):
    response = post(admin, "/operator/agents", {
        "agent_id": "agent-manual", "name": "수동", "owner_scope": "personal", "connection_type": "local",
        "capability_code": "code.fix", "scope_value": "demo-report-repo", "local_registration_id": "local-x",
    })
    assert (response.status_code, response.headers["location"]) == (303, "/settings?tab=advanced")
    deleted = post(admin, "/operator/agents/agent-manual/delete")
    assert (deleted.status_code, deleted.headers["location"]) == (303, "/settings?tab=advanced")
