"""알림 설정 화면 — URL 저장·삭제·[테스트 보내기] (phase 12 step 8, ADR-0018 결정 5, ARCHITECTURE "새 경로 (step 8·9)").

실제 Discord 는 부르지 않는다 — `app.state.notify_transport` 에 가짜 수신(MockTransport)을 넣는다. URL 은 토큰을 담으므로
비밀 파일(0600)에만 있고 응답 HTML·로그·DB 에는 호스트만 나온다.
"""

import json
import logging
import stat

import httpx
import pytest
from fastapi.testclient import TestClient

from workflow.adapters import repo, secret_store
from workflow.adapters.secret_store import SecretStore
from workflow.server.app import create_app

from .conftest import log_in, log_in_other_workspace

BASE = "http://127.0.0.1:8000"
URL = "https://discord.com/api/webhooks/123456/SECRETwebhookTOKEN_abcdef"
TOKEN = "SECRETwebhookTOKEN_abcdef"


class FakeReceiver:
    def __init__(self):
        self.status = 204
        self.raise_exc: Exception | None = None
        self.calls: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        if self.raise_exc is not None:
            raise self.raise_exc
        return httpx.Response(self.status)


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
def op(app) -> TestClient:
    return log_in(TestClient(app, base_url=BASE))


def error(response, status: int, code: str) -> None:
    assert response.status_code == status, response.text
    assert f"<code>{code}</code>" in response.text, response.text


def save(op: TestClient, url: str = URL):
    return op.post("/operator/notifications/webhook", data={"url": url}, follow_redirects=False)


# --- 권한 ----------------------------------------------------------------------------------------


def test_every_path_is_operator_only(app, conn, secrets, receiver):
    # 로그인 전, 그리고 고정 워크스페이스가 아닌 워크스페이스의 로그인 쿠키 — 셀프호스트에서는 둘 다 로그인 안 된 것
    stranger = log_in_other_workspace(TestClient(app, base_url=BASE))
    for anonymous in (TestClient(app, base_url=BASE), stranger):
        response = anonymous.get("/operator/notifications", follow_redirects=False)
        assert (response.status_code, response.headers["location"]) == (303, "/login")
        for path, data in (("/operator/notifications/webhook", {"url": URL}),
                           ("/operator/notifications/webhook/delete", {}),
                           ("/operator/notifications/test", {})):
            response = anonymous.post(path, data=data, follow_redirects=False)
            assert (response.status_code, response.headers["location"]) == (303, "/login")
    assert not secrets.exists(secret_store.NOTIFY_WEBHOOK_URL)
    assert receiver.calls == []


def test_selfhost_without_login_redirects_to_login(app):
    client = TestClient(app, base_url=BASE)
    response = client.get("/operator/notifications", follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == "/login"


# --- 저장·삭제 -------------------------------------------------------------------------------------


def test_empty_page_shows_form_and_not_configured(op):
    response = op.get("/operator/notifications")

    assert response.status_code == 200
    assert 'action="/operator/notifications/webhook"' in response.text
    assert 'type="password"' in response.text
    assert "data-notify-configured=\"false\"" in response.text
    assert "/operator/notifications/test" not in response.text  # URL 이 없으면 테스트 버튼 없음


def test_save_writes_0600_secret_and_page_shows_host_only(op, secrets, settings):
    response = save(op, f"  {URL} ")

    assert response.status_code == 303 and response.headers["location"] == "/operator/notifications"
    assert secrets.read(secret_store.NOTIFY_WEBHOOK_URL) == URL
    path = settings.secret_dir / secret_store.NOTIFY_WEBHOOK_URL
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    page = op.get("/operator/notifications").text
    assert "data-notify-configured=\"true\"" in page
    assert "설정됨 · 호스트 discord.com" in page
    assert TOKEN not in page and "webhooks/123456" not in page


def test_delete_removes_secret_file(op, secrets, settings):
    save(op)
    response = op.post("/operator/notifications/webhook/delete", follow_redirects=False)

    assert response.status_code == 303 and response.headers["location"] == "/operator/notifications"
    assert not (settings.secret_dir / secret_store.NOTIFY_WEBHOOK_URL).exists()
    assert "data-notify-configured=\"false\"" in op.get("/operator/notifications").text


@pytest.mark.parametrize("bad", [
    "",
    "not a url",
    "ftp://example.com/hook",
    "http://example.com/hook",  # 루프백 밖 http 는 거부
    "https://user:pass@example.com/hook",
    "https://example.com/" + "a" * 2100,
])
def test_invalid_url_is_422_and_not_saved(op, secrets, bad):
    response = save(op, bad)

    error(response, 422, "invalid_field")
    assert not secrets.exists(secret_store.NOTIFY_WEBHOOK_URL)
    if bad:
        assert bad not in response.text  # 값을 되돌려 보이지 않는다


@pytest.mark.parametrize("ok", ["http://127.0.0.1:5678/webhook/x", "http://localhost/hook", "https://hooks.example.com/x"])
def test_https_or_loopback_http_is_accepted(op, secrets, ok):
    assert save(op, ok).status_code == 303
    assert secrets.read(secret_store.NOTIFY_WEBHOOK_URL) == ok


def test_recent_notifications_listed_without_url_or_body(op, conn, secrets):
    save(op)
    session_id = conn.execute("SELECT session_id FROM sessions WHERE is_operator = 1").fetchone()[0]
    repo.enqueue_notification(
        conn, session_id=session_id, event="human_request", task_id=None, dedupe_key="human_request:hr-1",
        content="[Runloom] 사람 차례 — 비밀 본문", payload={"title": "t", "task_url": None, "pr_url": None},
        now="2026-09-28T00:00:00Z",
    )
    conn.commit()

    page = op.get("/operator/notifications").text
    assert 'data-notification-event="human_request"' in page
    assert "비밀 본문" not in page


# --- 테스트 보내기 --------------------------------------------------------------------------------


def test_send_test_posts_once_and_shows_success(op, receiver):
    save(op)
    response = op.post("/operator/notifications/test")

    assert response.status_code == 200
    assert len(receiver.calls) == 1
    call = receiver.calls[0]
    assert str(call.url) == URL
    assert set(json.loads(call.content)) == {"content"}  # Discord 형식
    assert 'data-notify-test="sent"' in response.text
    assert TOKEN not in response.text


def test_send_test_shows_http_status(op, receiver):
    save(op)
    receiver.status = 404
    response = op.post("/operator/notifications/test")

    assert response.status_code == 200
    assert 'data-notify-test="failed"' in response.text and "HTTP 404" in response.text
    assert TOKEN not in response.text


def test_send_test_shows_timeout(op, receiver):
    save(op)
    receiver.raise_exc = httpx.ReadTimeout("timed out")
    response = op.post("/operator/notifications/test")

    assert response.status_code == 200
    assert 'data-notify-test="failed"' in response.text and "시간 초과" in response.text


def test_send_test_without_url_is_409(op, receiver):
    error(op.post("/operator/notifications/test"), 409, "notify_not_configured")
    assert receiver.calls == []


def test_url_never_in_logs(op, receiver, caplog):
    caplog.set_level(logging.DEBUG)
    save(op)
    op.get("/operator/notifications")
    op.post("/operator/notifications/test")
    receiver.status = 500
    op.post("/operator/notifications/test")

    assert TOKEN not in caplog.text
