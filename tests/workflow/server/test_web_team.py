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


def logged_in(client: TestClient) -> bool:
    return client.get("/tasks", follow_redirects=False).status_code == 200


# --- 권한 ----------------------------------------------------------------------------------------

TEAM_POSTS = [
    ("/team/invites", {"role": "member"}),
    ("/team/invites/inv-000000000000/revoke", {}),
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
    error(member.get("/team"), 403, "forbidden")
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


def test_sidebar_links_team_for_admin_and_me_for_everyone(admin, member):
    admin_page = admin.get("/tasks").text
    assert 'href="/team"' in admin_page and 'href="/me"' in admin_page
    member_page = member.get("/tasks").text
    assert 'href="/team"' not in member_page and 'href="/me"' in member_page


# --- /team 목록 ------------------------------------------------------------------------------------


def test_team_lists_members_with_email_role_state_and_dates(admin, member, conn):
    page = admin.get("/team")
    assert page.status_code == 200
    dev = member_id(conn, "dev@example.com")
    assert f'data-member-id="{dev}"' in page.text
    assert "김개발" in page.text and "dev@example.com" in page.text and ADMIN_EMAIL in page.text
    assert "관리자" in page.text and "멤버" in page.text and "활성" in page.text
    assert "가입" in page.text and "마지막 접속" in page.text
    assert "scrypt$" not in page.text  # 비밀번호 해시는 화면에 없다


def test_team_without_public_url_shows_setting_hint(admin):
    page = admin.get("/team").text
    assert "WORKFLOW_PUBLIC_URL" in page and "SELFHOST" in page


# --- 초대 ------------------------------------------------------------------------------------------


def test_invite_link_shown_once_then_only_listed_without_token(app, admin, conn):
    response = admin.post("/team/invites", data={"role": "member"})
    assert response.status_code == 200, response.text
    token = link_token(response.text, "invite")
    assert f"http://testserver/invite/{token}" in response.text
    assert "data-copy-target" in response.text  # 복사 버튼

    listed = admin.get("/team").text
    assert token not in listed
    invite = repo.list_open_invites(conn, SESSION, now="2026-09-30T00:00:00Z")[-1]
    assert f'data-invite-id="{invite["invite_id"]}"' in listed
    assert token not in dump(conn)

    joined = TestClient(app).post(f"/invite/{token}", data={
        "email": "new@example.com", "display_name": "새 멤버", "password": MEMBER_PASSWORD}, follow_redirects=False)
    assert joined.status_code == 303
    assert repo.find_member_by_email(conn, SESSION, "new@example.com")["role"] == "member"


def test_invite_role_admin_and_invalid_role(app, admin, conn):
    token = link_token(admin.post("/team/invites", data={"role": "admin"}).text, "invite")
    TestClient(app).post(f"/invite/{token}", data={
        "email": "boss@example.com", "display_name": "새 관리자", "password": MEMBER_PASSWORD})
    assert repo.find_member_by_email(conn, SESSION, "boss@example.com")["role"] == "admin"

    error(admin.post("/team/invites", data={"role": "owner"}), 422, "invalid_field")


def test_invite_link_uses_public_url(settings, receiver):
    from dataclasses import replace

    app = create_app(replace(settings, public_url="https://runloom.example.com"))
    admin = log_in(TestClient(app, base_url="https://testserver"))
    response = admin.post("/team/invites", data={"role": "member"})
    token = link_token(response.text, "invite")
    assert f"https://runloom.example.com/invite/{token}" in response.text
    assert "WORKFLOW_PUBLIC_URL" not in admin.get("/team").text


def test_revoke_invite(app, admin, conn):
    token = link_token(admin.post("/team/invites", data={"role": "member"}).text, "invite")
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
    assert member.get("/team").status_code == 200
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
