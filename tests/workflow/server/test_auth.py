"""auth.py — 세션 서명 쿠키, 연결 토큰 인증, 운영자 확인 (ADR-0005), 입구 토큰 인증 (ADR-0010)."""

from fastapi import Depends
from fastapi.testclient import TestClient

from workflow.adapters import repo
from workflow.server.auth import (
    SELFHOST_SESSION_ID,
    SESSION_COOKIE,
    ensure_workspace,
    require_connector,
    require_operator,
    require_session,
    require_source_token,
    sign_session,
    verify_session,
)

from .conftest import NOW, bearer, exchange, log_in

SECRET = "test-session-secret"


# --- 서명 ------------------------------------------------------------------


def test_sign_and_verify_roundtrip():
    cookie = sign_session("sess-abc", SECRET)
    session_id, _, mac = cookie.rpartition(".")
    assert session_id == "sess-abc"
    assert len(mac) == 64
    assert verify_session(cookie, SECRET) == "sess-abc"


def test_forged_or_tampered_cookie_is_rejected():
    cookie = sign_session("sess-abc", SECRET)
    session_id, _, mac = cookie.rpartition(".")
    assert verify_session(f"sess-other.{mac}", SECRET) is None  # 다른 ID 에 같은 서명
    assert verify_session(f"{session_id}.{'0' * 64}", SECRET) is None  # 서명 위조
    assert verify_session(cookie, "another-secret") is None  # 다른 비밀값
    assert verify_session("no-dot-here", SECRET) is None
    assert verify_session(f".{mac}", SECRET) is None  # 빈 ID
    assert verify_session("", SECRET) is None


# --- 의존성 — Step 6 이 쓸 세션·운영자 의존성을 테스트 전용 라우트로 검증 ---------


def _install_probe_routes(app):
    @app.get("/_probe/session")
    def _session(session_id: str = Depends(require_session)):
        return {"session_id": session_id}

    @app.get("/_probe/operator")
    def _operator(session_id: str = Depends(require_operator)):
        return {"session_id": session_id}

    @app.get("/_probe/connector")
    def _connector(connector_id: str = Depends(require_connector)):
        return {"connector_id": connector_id}

    @app.get("/_probe/source")
    def _source(token=Depends(require_source_token)):
        return {"token_id": token["token_id"], "session_id": token["session_id"], "source": token["source"]}


def test_require_connector_accepts_only_bearer_wfc_token(app, conn):
    _install_probe_routes(app)
    client = TestClient(app)
    ensure_workspace(conn, NOW)
    connector_id, token = exchange(client, conn)
    assert client.get("/_probe/connector", headers=bearer(token)).json() == {"connector_id": connector_id}

    unauthenticated = {
        "code": "unauthenticated", "message": "유효한 연결 토큰이 필요합니다.", "field": None, "details": None,
    }
    for headers in (
        {},
        {"Authorization": f"Basic {token}"},
        {"Authorization": "Bearer "},
        {"Authorization": "Bearer wfc_not-a-real-token"},
        {"Authorization": f"Bearer {token}x"},
    ):
        response = client.get("/_probe/connector", headers=headers)
        assert response.status_code == 401, headers
        assert response.json() == unauthenticated


# --- 입구 토큰 (phase 7, ADR-0010 결정 2) — 세션이 발급한 wfs_ 토큰만. 세션 쿠키로는 통과하지 않는다 ------


def test_require_source_token_accepts_only_bearer_wfs_token_and_touches_last_used(app, conn):
    _install_probe_routes(app)
    client = TestClient(app)
    ensure_workspace(conn, NOW)
    token_id, token = repo.issue_source_token(conn, SELFHOST_SESSION_ID, "n8n", "n8n 테스트", NOW)
    (row,) = repo.list_source_tokens(conn, SELFHOST_SESSION_ID)
    assert row["last_used_at"] is None

    response = client.get("/_probe/source", headers=bearer(token))
    assert response.status_code == 200, response.text
    assert response.json() == {"token_id": token_id, "session_id": SELFHOST_SESSION_ID, "source": "n8n"}
    (row,) = repo.list_source_tokens(conn, SELFHOST_SESSION_ID)
    assert row["last_used_at"] is not None and row["last_used_at"] > NOW

    unauthenticated = {
        "code": "unauthenticated", "message": "유효한 입구 토큰이 필요합니다.", "field": None, "details": None,
    }
    log_in(client)  # 로그인한 워크스페이스 쿠키도 입구 인증이 아니다
    assert client.cookies.get(SESSION_COOKIE)
    connector_token = exchange(client, conn)[1]  # 연결 토큰(wfc_)도 아니다
    for headers in (
        {},
        {"Authorization": f"Basic {token}"},
        {"Authorization": "Bearer "},
        {"Authorization": "Bearer wfs_not-a-real-token"},
        {"Authorization": f"Bearer {token}x"},
        bearer(connector_token),
    ):
        response = client.get("/_probe/source", headers=headers)
        assert response.status_code == 401, headers
        assert response.json() == unauthenticated
        assert token not in response.text


def test_require_source_token_rejects_revoked_token(app, conn):
    _install_probe_routes(app)
    client = TestClient(app)
    ensure_workspace(conn, NOW)
    token_id, token = repo.issue_source_token(conn, SELFHOST_SESSION_ID, "n8n", "n8n 테스트", NOW)
    assert client.get("/_probe/source", headers=bearer(token)).status_code == 200
    repo.revoke_source_token(conn, SELFHOST_SESSION_ID, token_id, "2026-09-22T00:00:00Z")
    response = client.get("/_probe/source", headers=bearer(token))
    assert response.status_code == 401
    assert response.json()["code"] == "unauthenticated"
    assert token not in response.text


# --- 고정 워크스페이스 (ADR-0016 결정 3, ADR-0019) — 익명 세션 없음 ---------


def _selfhost_app(settings):
    from workflow.server.app import create_app

    app = create_app(settings)
    _install_probe_routes(app)
    return app


def _session_count(conn) -> int:
    return conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]


def test_selfhost_require_session_redirects_to_login_without_creating_session(settings, conn):
    client = TestClient(_selfhost_app(settings))
    response = client.get("/_probe/session", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"
    assert "set-cookie" not in response.headers
    assert _session_count(conn) == 0


def test_selfhost_require_operator_is_401_without_login(settings, conn):
    client = TestClient(_selfhost_app(settings))
    response = client.get("/_probe/operator")
    assert response.status_code == 401
    assert response.json()["code"] == "unauthenticated"
    assert _session_count(conn) == 0


def test_selfhost_accepts_only_the_fixed_workspace_cookie(settings, conn):
    """서명이 맞아도 다른 세션 id(예: demo 에서 쓰던 익명 세션)는 미인증. 고정 워크스페이스는 운영자다."""
    client = TestClient(_selfhost_app(settings))
    repo.create_session(conn, "sess-anon", NOW)
    repo.mark_operator(conn, "sess-anon")
    client.cookies.set(SESSION_COOKIE, sign_session("sess-anon", SECRET))
    assert client.get("/_probe/session", follow_redirects=False).status_code == 303
    assert client.get("/_probe/operator").status_code == 401

    ensure_workspace(conn, NOW)
    client.cookies.set(SESSION_COOKIE, sign_session(SELFHOST_SESSION_ID, SECRET))
    assert client.get("/_probe/session").json() == {"session_id": SELFHOST_SESSION_ID}
    assert client.get("/_probe/operator").json() == {"session_id": SELFHOST_SESSION_ID}


def test_selfhost_workspace_cookie_without_row_is_unauthenticated(settings, conn):
    client = TestClient(_selfhost_app(settings))
    client.cookies.set(SESSION_COOKIE, sign_session(SELFHOST_SESSION_ID, SECRET))
    assert client.get("/_probe/session", follow_redirects=False).status_code == 303
    assert _session_count(conn) == 0


def test_ensure_workspace_creates_operator_session_once(conn, app):
    ensure_workspace(conn, NOW)
    ensure_workspace(conn, "2026-09-21T00:00:00Z")
    row = repo.get_session(conn, SELFHOST_SESSION_ID)
    assert row["is_operator"] == 1
    assert row["created_at"] == NOW
    assert _session_count(conn) == 1
    assert repo.list_kinds(conn, SELFHOST_SESSION_ID)  # 내장 종류 seed — create_session 경로 그대로


def test_selfhost_connector_and_source_tokens_unchanged(settings, conn):
    client = TestClient(_selfhost_app(settings))
    ensure_workspace(conn, NOW)
    connector_id, connector_token = exchange(client, conn)
    assert client.get("/_probe/connector", headers=bearer(connector_token)).json() == {"connector_id": connector_id}
    token_id, token = repo.issue_source_token(conn, SELFHOST_SESSION_ID, "n8n", "n8n", NOW)
    assert client.get("/_probe/source", headers=bearer(token)).json() == {
        "token_id": token_id, "session_id": SELFHOST_SESSION_ID, "source": "n8n",
    }
    assert client.get("/_probe/connector").status_code == 401
