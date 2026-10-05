"""팀 화면(`/team`)·내 설정(`/me`) — phase 15 step 7 (ADR-0021, ARCHITECTURE "팀 — phase 15" 새 경로).

초대·재설정 링크 원문은 발급 응답 화면에 한 번만 보이고 목록·DB 에는 없다. 개인 웹훅 URL 은 비밀 파일
`notify_webhook_url.<member_id>`(0600)에만 있고 화면에는 "저장됨"·호스트만. 실제 외부 웹훅은 부르지 않는다(MockTransport).
"""

import re
import stat

import httpx
import pytest
from fastapi.testclient import TestClient

from workflow.adapters import repo, secret_store
from workflow.adapters.secret_store import SecretStore
from workflow.server.app import create_app

from .conftest import ADMIN_EMAIL, ADMIN_PASSWORD, MEMBER_PASSWORD, SESSION, log_in, log_in_member

URL = "https://discord.com/api/webhooks/654321/PERSONALwebhookTOKEN_xyz"
TOKEN = "PERSONALwebhookTOKEN_xyz"
NEW_PASSWORD = "brand-new-password-0930"


class FakeReceiver:
    def __init__(self):
        self.calls: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        return httpx.Response(204)


@pytest.fixture
def receiver() -> FakeReceiver:
    return FakeReceiver()


@pytest.fixture
def app(settings, receiver):
    app = create_app(settings)
    app.state.notify_transport = httpx.MockTransport(receiver)
    return app


@pytest.fixture
def secrets(settings) -> SecretStore:
    return SecretStore(settings.secret_dir)


@pytest.fixture
def admin(app) -> TestClient:
    return log_in(TestClient(app))


@pytest.fixture
def member(app, admin) -> TestClient:
    return log_in_member(TestClient(app), email="dev@example.com", display_name="김개발")


def error(response, status: int, code: str) -> None:
    assert response.status_code == status, response.text
    assert f"<code>{code}</code>" in response.text, response.text


def member_id(conn, email: str) -> str:
    return repo.find_member_by_email(conn, SESSION, email)["member_id"]


def dump(conn) -> str:
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")]
    return "\n".join(repr(tuple(row)) for t in tables for row in conn.execute(f"SELECT * FROM {t}"))


def link_token(html: str, kind: str) -> str:
    found = re.findall(rf"/{kind}/([A-Za-z0-9_-]{{20,}})", html)
    assert len(set(found)) == 1, html
    return found[0]


def invite_error(response, code: str, message: str) -> None:
    """초대 폼 오류는 오류 화면이 아니라 팀 화면 폼 위 인라인(422) — phase 23 step 3."""
    assert response.status_code == 422, response.text
    assert f'data-invite-error="{code}"' in response.text and message in response.text, response.text
    assert 'action="/team/invites"' in response.text  # 같은 팀 화면


def visible_text(html: str) -> str:
    """ARCHITECTURE "노출 단정 규칙" — 스크립트·스타일·"자세히" 를 뺀 화면 글자."""
    html = re.sub(r"<(script|style)\b.*?</\1>", " ", html, flags=re.S)
    html = re.sub(r'<details class="detail"[^>]*>.*?</details>', " ", html, flags=re.S)
    return re.sub(r"<[^>]+>", " ", html)


LINK_NOTE = "이 링크는 이 컴퓨터 주소 기준입니다 — 다른 컴퓨터에서 열려면 SELFHOST 문서의 WORKFLOW_PUBLIC_URL 을 설정하세요."
INVITE_NOTICE = ("이 링크는 지금만 보입니다. 놓치면 아래 목록에서 [링크 다시 만들기] 를 누르세요 — 옛 링크는 더 쓸 수 없습니다."
                 " 7일 동안 한 번 쓸 수 있습니다.")
RESET_NOTICE = "이 링크는 지금만 보입니다. 24시간 동안 한 번 쓸 수 있습니다."


def logged_in(client: TestClient) -> bool:
    return client.get("/tasks", follow_redirects=False).status_code == 200


# --- 권한 ----------------------------------------------------------------------------------------

TEAM_POSTS = [
    ("/team/invites", {"role": "member", "invitee_email": "x@example.com"}),
    ("/team/invites/inv-000000000000/revoke", {}),
    ("/team/invites/inv-000000000000/reissue", {}),
    ("/team/members/mem-00000000/role", {"role": "admin"}),
    ("/team/members/mem-00000000/disable", {}),
    ("/team/members/mem-00000000/enable", {}),
    ("/team/members/mem-00000000/reset-link", {}),
]
ME_POSTS = [
    ("/me/profile", {"display_name": "x"}),
    ("/me/password", {"current_password": "x", "new_password": "y"}),
    ("/me/webhook", {"url": URL}),
    ("/me/webhook/delete", {}),
    ("/me/webhook/test", {}),
]


def test_member_gets_403_on_team_and_changes_nothing(member, conn):
    page = member.get("/team")  # phase 23: 팀 화면은 열리지만 멤버·초대 절은 `manage_team` 만
    assert page.status_code == 200 and 'action="/team/invites"' not in page.text and "data-member-id=" not in page.text
    invites = len(repo.list_open_invites(conn, SESSION, now="2026-09-30T00:00:00Z"))
    for path, data in TEAM_POSTS:
        error(member.post(path, data=data), 403, "forbidden")
    assert len(repo.list_open_invites(conn, SESSION, now="2026-09-30T00:00:00Z")) == invites


def test_anonymous_is_sent_to_login(app, admin):
    anonymous = TestClient(app)
    for path in ("/team", "/me"):
        response = anonymous.get(path, follow_redirects=False)
        assert (response.status_code, response.headers["location"]) == (303, "/login")
    for path, data in TEAM_POSTS + ME_POSTS:
        response = anonymous.post(path, data=data, follow_redirects=False)
        assert (response.status_code, response.headers["location"]) == (303, "/login"), path


def test_sidebar_links_team_and_me_for_everyone(admin, member):
    """phase 23: 사이드바에 팀 화면(`/team`)과 내 설정이 모두에게 있다(옛 `연결` 메뉴는 없다)."""
    for client in (admin, member):
        page = client.get("/tasks").text
        sidebar = page[page.index('class="sidebar'):page.index('class="main')]
        assert 'href="/team"' in sidebar and 'href="/me"' in sidebar and 'href="/connect"' not in sidebar
        assert client.get("/team", follow_redirects=False).status_code == 200


# --- 팀 목록 (팀 화면 — phase 23 step 1) ------------------------------------------------------------------------------------


def test_team_lists_members_with_email_role_state_and_dates(admin, member, conn):
    page = admin.get("/team")
    assert page.status_code == 200
    dev = member_id(conn, "dev@example.com")
    assert f'data-member-id="{dev}"' in page.text
    assert "김개발" in page.text and "dev@example.com" in page.text and ADMIN_EMAIL in page.text
    assert "관리자" in page.text and "멤버" in page.text and "활성" in page.text
    assert "가입" in page.text and "마지막 접속" in page.text
    assert "scrypt$" not in page.text  # 비밀번호 해시는 화면에 없다


def test_team_has_no_red_public_url_warning_and_no_link_note_without_link(admin):
    """phase 23: 팀 화면 맨 위 빨간 `WORKFLOW_PUBLIC_URL` 경고는 없다 — 회색 안내는 링크가 보일 때만."""
    page = admin.get("/team").text
    assert "WORKFLOW_PUBLIC_URL" not in page and "data-public-url-note" not in page
    assert 'data-team-section="members"' in page and 'data-team-section="invites"' in page
    assert page.index('data-team-section="members"') < page.index('data-team-section="invites"')


# --- 초대 ------------------------------------------------------------------------------------------


def test_invite_link_shown_once_then_only_listed_without_token(app, admin, conn):
    response = admin.post("/team/invites", data={"role": "member", "invitee_email": " New@Example.com ",
                                                 "invitee_name": "새 멤버"})
    assert response.status_code == 200, response.text
    token = link_token(response.text, "invite")
    assert f"http://testserver/invite/{token}" in response.text
    assert 'data-issued="invite"' in response.text and "new@example.com 초대 링크(멤버):" in response.text
    assert 'data-copy-target="issued-link"' in response.text  # 복사 버튼
    assert INVITE_NOTICE in response.text
    assert 'class="link-note" data-public-url-note' in response.text and LINK_NOTE in response.text
    assert '<div class="alert">' not in response.text  # 빨간 경고 없음

    listed = admin.get("/team").text
    assert token not in listed and "data-issued" not in listed and "data-public-url-note" not in listed
    invite = repo.list_open_invites(conn, SESSION, now="2026-09-30T00:00:00Z")[-1]
    row = re.search(rf'<tr data-invite-id="{invite["invite_id"]}">.*?</tr>', listed, flags=re.S).group(0)
    assert "new@example.com" in row and "새 멤버" in row and "멤버" in row
    assert f'action="/team/invites/{invite["invite_id"]}/reissue"' in row and "링크 다시 만들기" in row
    assert f'action="/team/invites/{invite["invite_id"]}/revoke"' in row
    assert invite["invite_id"] in row and invite["invite_id"] not in visible_text(row)  # ID 는 "자세히" 안에만
    assert (invite["invitee_email"], invite["invitee_name"]) == ("new@example.com", "새 멤버")
    assert token not in dump(conn)

    joined = TestClient(app).post(f"/invite/{token}", data={
        "email": "new@example.com", "display_name": "새 멤버", "password": MEMBER_PASSWORD}, follow_redirects=False)
    assert joined.status_code == 303
    assert repo.find_member_by_email(conn, SESSION, "new@example.com")["role"] == "member"


def test_invite_role_admin_and_invalid_role(app, admin, conn):
    token = link_token(admin.post("/team/invites", data={"role": "admin", "invitee_email": "boss@example.com"}).text,
                       "invite")
    TestClient(app).post(f"/invite/{token}", data={
        "email": "boss@example.com", "display_name": "새 관리자", "password": MEMBER_PASSWORD})
    assert repo.find_member_by_email(conn, SESSION, "boss@example.com")["role"] == "admin"

    invite_error(admin.post("/team/invites", data={"role": "owner", "invitee_email": "x@example.com"}),
                 "invalid_field", "역할은 관리자 또는 멤버입니다.")


def test_invite_needs_valid_email_name_and_no_overlap(admin, conn):
    before = len(repo.list_open_invites(conn, SESSION, now="2026-09-30T00:00:00Z"))
    for data, code, message in (
        ({"role": "member"}, "invalid_field", "이메일 형식이 올바르지 않습니다."),
        ({"role": "member", "invitee_email": "not-an-email"}, "invalid_field", "이메일 형식이 올바르지 않습니다."),
        ({"role": "member", "invitee_email": "n@example.com", "invitee_name": "가" * 41}, "invalid_field",
         "이름은 1~40자로 입력하세요."),
        ({"role": "member", "invitee_email": ADMIN_EMAIL.upper()}, "invite_email_taken",
         "이미 팀에 있는 이메일입니다(비활성 멤버 포함)."),
    ):
        response = admin.post("/team/invites", data=data)
        invite_error(response, code, message)
        assert "data-issued" not in response.text
    assert admin.post("/team/invites", data={"role": "member", "invitee_email": "dup@example.com"}).status_code == 200
    again = admin.post("/team/invites", data={"role": "admin", "invitee_email": "DUP@example.com", "invitee_name": "중복"})
    invite_error(again, "invite_email_taken",
                 "이 이메일로 보낸 초대가 아직 열려 있습니다 — 아래 목록에서 [링크 다시 만들기] 를 누르세요.")
    assert 'value="DUP@example.com"' in again.text and 'value="중복"' in again.text  # 입력값을 다시 채운다
    assert '<option value="admin" selected>' in again.text
    assert len(repo.list_open_invites(conn, SESSION, now="2026-09-30T00:00:00Z")) == before + 1


def test_invite_accept_with_another_email_is_422_and_keeps_the_link(app, admin, conn):
    token = link_token(admin.post("/team/invites", data={"role": "member", "invitee_email": "fix@example.com"}).text,
                       "invite")
    client = TestClient(app)
    wrong = client.post(f"/invite/{token}", data={"email": "other@example.com", "display_name": "다른",
                                                  "password": MEMBER_PASSWORD}, follow_redirects=False)
    assert wrong.status_code == 422 and "초대받은 이메일로만 가입할 수 있습니다." in wrong.text
    assert 'data-error-code="invite_email_mismatch"' in wrong.text
    assert 'value="fix@example.com" readonly' in wrong.text  # 고정 이메일은 다시 초대 이메일로
    assert repo.find_member_by_email(conn, SESSION, "other@example.com") is None
    joined = client.post(f"/invite/{token}", data={"email": "fix@example.com", "display_name": "고정",
                                                   "password": MEMBER_PASSWORD}, follow_redirects=False)
    assert joined.status_code == 303


def test_invite_form_asks_invitee_email_and_name(admin):
    page = admin.get("/team").text
    assert 'name="invitee_email"' in page and 'type="email"' in page and 'name="invitee_name"' in page
    assert "받는 사람 이메일" in page and "링크를 복사해 직접 전하세요 — 이메일은 보내지 않습니다. 받는 사람은 이 이메일로만 가입합니다." in page
    assert "쓰지 않은 초대가 없습니다." in page


def test_signup_page_fixes_invite_email_and_defaults_name(app, admin, conn):
    token = link_token(admin.post("/team/invites", data={"role": "admin", "invitee_email": "Fixed@Example.com",
                                                         "invitee_name": "고정 이름"}).text, "invite")
    page = TestClient(app).get(f"/invite/{token}")
    assert page.status_code == 200
    assert '<input type="email" id="email" name="email" value="fixed@example.com" readonly' in page.text
    assert 'value="고정 이름"' in page.text
    assert "관리자 이 관리자 로 초대했습니다." in page.text


def test_signup_page_for_old_invite_without_email_is_unchanged(app, admin, conn):
    invite_id, token = repo.issue_invite(conn, SESSION, role="member", invitee_email="old@example.com",
                                         created_by_member_id=None, now="2026-09-30T00:00:00Z")
    conn.execute("UPDATE member_invites SET invitee_email = NULL, expires_at = '2099-01-01T00:00:00Z'"
                 " WHERE invite_id = ?", (invite_id,))
    page = TestClient(app).get(f"/invite/{token}").text
    assert "readonly" not in page and 'value=""' in page
    assert "관리자 이 멤버 로 초대했습니다." in page  # 초대한 사람이 없으면 `관리자`

    listed = admin.get("/team").text
    row = re.search(rf'<tr data-invite-id="{invite_id}">.*?</tr>', listed, flags=re.S).group(0)
    assert "이메일 없음(옛 초대)" in row and "—" in row
    joined = TestClient(app).post(f"/invite/{token}", data={"email": "any@example.com", "display_name": "옛 초대",
                                                            "password": MEMBER_PASSWORD}, follow_redirects=False)
    assert joined.status_code == 303


def test_invite_link_uses_public_url(settings, receiver, conn):
    from dataclasses import replace

    app = create_app(replace(settings, public_url="https://runloom.example.com"))
    admin = log_in(TestClient(app, base_url="https://testserver"))
    response = admin.post("/team/invites", data={"role": "member", "invitee_email": "pub@example.com"})
    token = link_token(response.text, "invite")
    assert f"https://runloom.example.com/invite/{token}" in response.text
    assert "data-public-url-note" not in response.text and "WORKFLOW_PUBLIC_URL" not in response.text
    assert "WORKFLOW_PUBLIC_URL" not in admin.get("/team").text
    invite_id = repo.list_open_invites(conn, SESSION, now="2026-09-30T00:00:00Z")[-1]["invite_id"]
    again = admin.post(f"/team/invites/{invite_id}/reissue")
    assert "https://runloom.example.com/invite/" in again.text and "data-public-url-note" not in again.text


def test_reissue_invite_shows_new_link_once_and_old_link_dies(app, admin, conn):
    old = link_token(admin.post("/team/invites", data={"role": "member", "invitee_email": "re@example.com",
                                                       "invitee_name": "다시"}).text, "invite")
    invite_id = repo.list_open_invites(conn, SESSION, now="2026-09-30T00:00:00Z")[-1]["invite_id"]
    response = admin.post(f"/team/invites/{invite_id}/reissue", follow_redirects=False)
    assert response.status_code == 200, response.text
    new = link_token(response.text, "invite")
    assert new != old and old not in response.text
    assert 'data-issued="invite"' in response.text and "re@example.com 초대 링크(멤버):" in response.text
    assert INVITE_NOTICE in response.text and LINK_NOTE in response.text
    assert new not in admin.get("/team").text and new not in dump(conn)
    assert len([r for r in repo.list_open_invites(conn, SESSION, now="2026-09-30T00:00:00Z")
                if r["invitee_email"] == "re@example.com"]) == 1  # 같은 줄

    guest = TestClient(app)
    assert guest.get(f"/invite/{old}").status_code == 404
    stale = guest.post(f"/invite/{old}", data={"email": "re@example.com", "display_name": "다시",
                                               "password": MEMBER_PASSWORD}, follow_redirects=False)
    assert stale.status_code == 404 and repo.find_member_by_email(conn, SESSION, "re@example.com") is None
    joined = guest.post(f"/invite/{new}", data={"email": "re@example.com", "display_name": "다시",
                                                "password": MEMBER_PASSWORD}, follow_redirects=False)
    assert joined.status_code == 303
    # 쓴 초대·없는 초대는 다시 만들 수 없다
    used = admin.post(f"/team/invites/{invite_id}/reissue")
    error(used, 404, "not_found")
    assert f"초대 {invite_id}을 찾을 수 없습니다." in used.text
    error(admin.post("/team/invites/inv-000000000000/reissue"), 404, "not_found")


def test_revoke_invite(app, admin, conn):
    token = link_token(admin.post("/team/invites", data={"role": "member", "invitee_email": "rv@example.com"}).text,
                       "invite")
    invite_id = repo.list_open_invites(conn, SESSION, now="2026-09-30T00:00:00Z")[-1]["invite_id"]
    response = admin.post(f"/team/invites/{invite_id}/revoke", follow_redirects=False)
    assert (response.status_code, response.headers["location"]) == (303, "/team")
    assert f'data-invite-id="{invite_id}"' not in admin.get("/team").text
    assert TestClient(app).get(f"/invite/{token}").status_code == 404
    error(admin.post(f"/team/invites/{invite_id}/revoke"), 404, "not_found")


# --- 역할·비활성화 ------------------------------------------------------------------------------------


def test_role_change_applies_immediately(admin, member, conn):
    dev = member_id(conn, "dev@example.com")
    response = admin.post(f"/team/members/{dev}/role", data={"role": "admin"}, follow_redirects=False)
    assert (response.status_code, response.headers["location"]) == (303, "/team")
    assert 'action="/team/invites"' in member.get("/team").text
    error(admin.post(f"/team/members/{dev}/role", data={"role": "owner"}), 422, "invalid_field")


def test_last_admin_cannot_demote_or_disable_self(admin, member, conn):
    me = member_id(conn, ADMIN_EMAIL)
    for response in (admin.post(f"/team/members/{me}/role", data={"role": "member"}),
                     admin.post(f"/team/members/{me}/disable")):
        error(response, 409, "last_admin")
        assert "활성 관리자가 한 명은 있어야 합니다" in response.text
    assert repo.get_member(conn, SESSION, me)["role"] == "admin"
    assert logged_in(admin)


def test_disable_logs_member_out_and_blocks_login_then_enable(app, admin, member, conn):
    dev = member_id(conn, "dev@example.com")
    response = admin.post(f"/team/members/{dev}/disable", follow_redirects=False)
    assert (response.status_code, response.headers["location"]) == (303, "/team")
    assert not logged_in(member)
    assert "비활성" in admin.get("/team").text
    login = TestClient(app).post("/login", data={"email": "dev@example.com", "password": MEMBER_PASSWORD},
                                 follow_redirects=False)
    assert login.status_code == 403

    assert admin.post(f"/team/members/{dev}/enable", follow_redirects=False).status_code == 303
    login = TestClient(app).post("/login", data={"email": "dev@example.com", "password": MEMBER_PASSWORD},
                                 follow_redirects=False)
    assert login.status_code == 303


def test_unknown_member_is_404(admin):
    for path, data in TEAM_POSTS[2:]:
        error(admin.post(path, data=data), 404, "not_found")


# --- 재설정 링크 -----------------------------------------------------------------------------------


def test_reset_link_shown_once_and_sets_new_password(app, admin, member, conn):
    dev = member_id(conn, "dev@example.com")
    response = admin.post(f"/team/members/{dev}/reset-link")
    assert response.status_code == 200, response.text
    token = link_token(response.text, "reset")
    assert f"http://testserver/reset/{token}" in response.text
    assert 'data-issued="reset"' in response.text and RESET_NOTICE in response.text
    assert LINK_NOTE in response.text and '<div class="alert">' not in response.text
    issued = response.text.index('data-issued="reset"')
    assert issued < response.text.index('data-team-section="members"')  # 멤버 절 위
    assert token not in admin.get("/team").text and token not in dump(conn)

    used = TestClient(app).post(f"/reset/{token}", data={"password": NEW_PASSWORD}, follow_redirects=False)
    assert used.status_code == 303
    assert not logged_in(member)  # 재설정은 그 멤버의 세션을 전부 폐기한다
    login = TestClient(app).post("/login", data={"email": "dev@example.com", "password": NEW_PASSWORD},
                                 follow_redirects=False)
    assert login.status_code == 303


# --- /me: 표시 이름·비밀번호 -------------------------------------------------------------------------


def test_me_page_shows_own_settings(member):
    page = member.get("/me")
    assert page.status_code == 200
    assert "dev@example.com" in page.text and "김개발" in page.text
    for action in ("/me/profile", "/me/password", "/me/webhook"):
        assert f'action="{action}"' in page.text
    assert 'data-personal-webhook="false"' in page.text


def test_change_display_name(member, conn):
    response = member.post("/me/profile", data={"display_name": "  박개발 "}, follow_redirects=False)
    assert (response.status_code, response.headers["location"]) == (303, "/me")
    assert repo.get_member(conn, SESSION, member_id(conn, "dev@example.com"))["display_name"] == "박개발"
    assert "박개발" in member.get("/tasks").text
    for bad in ("", "   ", "가" * 41):
        error(member.post("/me/profile", data={"display_name": bad}), 422, "invalid_field")


def test_change_password_revokes_other_devices_and_keeps_this_one(app, member):
    other_device = TestClient(app)
    other_device.post("/login", data={"email": "dev@example.com", "password": MEMBER_PASSWORD})
    assert logged_in(other_device)

    response = member.post("/me/password", data={"current_password": MEMBER_PASSWORD, "new_password": NEW_PASSWORD},
                           follow_redirects=False)
    assert (response.status_code, response.headers["location"]) == (303, "/me")
    assert "wf_login=" in response.headers["set-cookie"]
    assert logged_in(member)
    assert not logged_in(other_device)
    old = TestClient(app).post("/login", data={"email": "dev@example.com", "password": MEMBER_PASSWORD},
                               follow_redirects=False)
    assert old.status_code == 403
    new = TestClient(app).post("/login", data={"email": "dev@example.com", "password": NEW_PASSWORD},
                               follow_redirects=False)
    assert new.status_code == 303


def test_change_password_wrong_current_new_rules_and_throttle(member):
    wrong = member.post("/me/password", data={"current_password": "not-my-password", "new_password": NEW_PASSWORD})
    assert wrong.status_code == 403
    assert "not-my-password" not in wrong.text and NEW_PASSWORD not in wrong.text
    short = member.post("/me/password", data={"current_password": MEMBER_PASSWORD, "new_password": "short"})
    assert short.status_code == 422
    for _ in range(4):
        member.post("/me/password", data={"current_password": "not-my-password", "new_password": NEW_PASSWORD})
    blocked = member.post("/me/password", data={"current_password": MEMBER_PASSWORD, "new_password": NEW_PASSWORD})
    assert blocked.status_code == 429
    assert logged_in(member)


def test_admin_can_use_me_too(admin):
    response = admin.post("/me/password", data={"current_password": ADMIN_PASSWORD, "new_password": NEW_PASSWORD},
                          follow_redirects=False)
    assert response.status_code == 303


# --- /me: 개인 웹훅 ------------------------------------------------------------------------------------


def test_personal_webhook_save_is_0600_and_never_shown(member, conn, secrets, settings):
    dev = member_id(conn, "dev@example.com")
    response = member.post("/me/webhook", data={"url": f" {URL} "}, follow_redirects=False)
    assert (response.status_code, response.headers["location"]) == (303, "/me")

    name = secret_store.personal_webhook_name(dev)
    assert secrets.read(name) == URL
    assert stat.S_IMODE((settings.secret_dir / name).stat().st_mode) == 0o600
    assert not secrets.exists(secret_store.NOTIFY_WEBHOOK_URL)  # 공용 웹훅과 다른 파일
    page = member.get("/me").text
    assert 'data-personal-webhook="true"' in page and "저장됨" in page
    assert TOKEN not in page and "webhooks/654321" not in page
    assert TOKEN not in dump(conn)


def test_personal_webhook_invalid_is_422_and_not_saved(member, conn, secrets):
    name = secret_store.personal_webhook_name(member_id(conn, "dev@example.com"))
    for bad in ("", "http://example.com/hook", "https://user:pass@example.com/hook"):
        response = member.post("/me/webhook", data={"url": bad})
        error(response, 422, "invalid_field")
        assert not secrets.exists(name)
        if bad:
            assert bad not in response.text


def test_personal_webhook_is_per_member(admin, member, conn, secrets):
    member.post("/me/webhook", data={"url": URL})
    assert not secrets.exists(secret_store.personal_webhook_name(member_id(conn, ADMIN_EMAIL)))
    assert 'data-personal-webhook="false"' in admin.get("/me").text


def test_personal_webhook_delete(member, conn, secrets):
    name = secret_store.personal_webhook_name(member_id(conn, "dev@example.com"))
    member.post("/me/webhook", data={"url": URL})
    response = member.post("/me/webhook/delete", follow_redirects=False)
    assert (response.status_code, response.headers["location"]) == (303, "/me")
    assert not secrets.exists(name)
    assert 'data-personal-webhook="false"' in member.get("/me").text


def test_personal_webhook_test_sends_once(member, receiver):
    error(member.post("/me/webhook/test"), 409, "notify_not_configured")
    assert receiver.calls == []

    member.post("/me/webhook", data={"url": URL})
    response = member.post("/me/webhook/test")
    assert response.status_code == 200
    assert len(receiver.calls) == 1 and str(receiver.calls[0].url) == URL
    assert 'data-notify-test="sent"' in response.text
    assert TOKEN not in response.text


def test_me_lists_only_notifications_i_receive(admin, member, conn):
    """`/me` 최근 알림 = 내 개인 행 + recipient_member_ids 에 내가 든 공용 행(ARCHITECTURE "알림 — 받는 사람별"). 본문·URL 없음."""
    dev, boss = member_id(conn, "dev@example.com"), member_id(conn, ADMIN_EMAIL)
    base = {"session_id": SESSION, "task_id": None, "now": "2026-10-06T12:00:00Z"}
    payload = {"title": "버그", "task_url": None, "pr_url": None}
    repo.enqueue_notification(conn, event="human_request", dedupe_key="human_request:hr-1:shared",
                              content="본문-공용", payload={**payload, "recipient_member_ids": [dev]}, **base)
    repo.enqueue_notification(conn, event="pr_opened", dedupe_key=f"pr_opened:t:1:personal:{dev}", content="본문-개인",
                              payload=payload, channel="personal", recipient_member_id=dev, **base)
    repo.enqueue_notification(conn, event="task_failed", dedupe_key="task_failed:e:shared", content="본문-남",
                              payload={**payload, "recipient_member_ids": [boss]}, **base)

    page = member.get("/me").text
    assert re.findall(r'data-notification-event="(\w+)"', page) == ["pr_opened", "human_request"]
    assert "본문-" not in page
    assert re.findall(r'data-notification-event="(\w+)"', admin.get("/me").text) == ["task_failed"]
