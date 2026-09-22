"""auth.py — 세션 서명 쿠키, 연결 토큰 인증, 운영자 확인 (ADR-0005), 입구 토큰 인증 (ADR-0010)."""

from fastapi import Depends
from fastapi.testclient import TestClient

from workflow.adapters import repo
from workflow.server.auth import (
    SESSION_COOKIE,
    require_connector,
    require_operator,
    require_session,
    require_source_token,
    sign_session,
    verify_session,
)

from .conftest import NOW, bearer, exchange

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


def test_require_session_issues_signed_cookie_and_reuses_it(app, conn):
    _install_probe_routes(app)
    client = TestClient(app)
    first = client.get("/_probe/session")
    assert first.status_code == 200
    session_id = first.json()["session_id"]
    assert session_id.startswith("sess-")
    assert repo.get_session(conn, session_id) is not None

    cookie = first.cookies[SESSION_COOKIE]
    assert verify_session(cookie, "test-session-secret") == session_id
    set_cookie = first.headers["set-cookie"].lower()
    assert "httponly" in set_cookie
    assert "samesite=lax" in set_cookie
    assert "max-age=1209600" in set_cookie  # 14일

    second = client.get("/_probe/session")  # TestClient 가 쿠키를 보관한다
    assert second.json()["session_id"] == session_id
    assert "set-cookie" not in second.headers


def test_require_session_replaces_forged_cookie_with_new_session(app, conn):
    _install_probe_routes(app)
    client = TestClient(app)
    client.cookies.set(SESSION_COOKIE, sign_session("sess-forged", "wrong-secret"))
    response = client.get("/_probe/session")
    assert response.status_code == 200
    assert response.json()["session_id"] != "sess-forged"
    assert repo.get_session(conn, "sess-forged") is None


def test_require_session_ignores_cookie_for_unknown_session(app, conn):
    """서명은 맞지만 DB 에 없는 세션(예: DB 초기화 후) 은 새 세션으로 바꾼다."""
    _install_probe_routes(app)
    client = TestClient(app)
    client.cookies.set(SESSION_COOKIE, sign_session("sess-ghost", "test-session-secret"))
    response = client.get("/_probe/session")
    assert response.json()["session_id"] != "sess-ghost"


def test_require_operator_needs_operator_flag(app, conn):
    _install_probe_routes(app)
    client = TestClient(app)
    assert client.get("/_probe/operator").status_code == 403  # 세션 없음
    session_id = client.get("/_probe/session").json()["session_id"]
    denied = client.get("/_probe/operator")
    assert denied.status_code == 403
    assert denied.json() == {
        "code": "forbidden", "message": "운영자 권한이 필요합니다.", "field": None, "details": None,
    }
    repo.mark_operator(conn, session_id)
    assert client.get("/_probe/operator").json() == {"session_id": session_id}


def test_require_connector_accepts_only_bearer_wfc_token(app, conn):
    _install_probe_routes(app)
    client = TestClient(app)
    repo.create_session(conn, "sess-1", NOW)
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
    repo.create_session(conn, "sess-1", NOW)
    token_id, token = repo.issue_source_token(conn, "sess-1", "n8n", "n8n 테스트", NOW)
    (row,) = repo.list_source_tokens(conn, "sess-1")
    assert row["last_used_at"] is None

    response = client.get("/_probe/source", headers=bearer(token))
    assert response.status_code == 200, response.text
    assert response.json() == {"token_id": token_id, "session_id": "sess-1", "source": "n8n"}
    (row,) = repo.list_source_tokens(conn, "sess-1")
    assert row["last_used_at"] is not None and row["last_used_at"] > NOW

    unauthenticated = {
        "code": "unauthenticated", "message": "유효한 입구 토큰이 필요합니다.", "field": None, "details": None,
    }
    session_cookie = client.get("/_probe/session").cookies[SESSION_COOKIE]  # 세션 쿠키는 입구 인증이 아니다
    client.cookies.set(SESSION_COOKIE, session_cookie)
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
    repo.create_session(conn, "sess-1", NOW)
    token_id, token = repo.issue_source_token(conn, "sess-1", "n8n", "n8n 테스트", NOW)
    assert client.get("/_probe/source", headers=bearer(token)).status_code == 200
    repo.revoke_source_token(conn, "sess-1", token_id, "2026-09-22T00:00:00Z")
    response = client.get("/_probe/source", headers=bearer(token))
    assert response.status_code == 401
    assert response.json()["code"] == "unauthenticated"
    assert token not in response.text
