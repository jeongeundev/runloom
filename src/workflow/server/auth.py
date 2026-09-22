"""인증 — 심사자 세션 서명 쿠키, 연결 프로그램 Bearer 토큰, 운영자 확인 (ADR-0005, ARCHITECTURE 인증 절),
입구 토큰 (ADR-0010 — 세션이 발급해 n8n 이 쓴다).

요청 범위 의존성(`get_conn`, `utc_now`) 도 여기 둔다. 인증이 가장 먼저 DB 와 시각을 쓴다.
sqlite 연결은 요청마다 새로 열고 응답 뒤 닫는다 (`get_conn`). 앱 전역 연결을 두지 않는다.
"""

import hashlib
import hmac
import secrets
from collections.abc import Iterator
from datetime import UTC, datetime
from sqlite3 import Connection, Row

from fastapi import Depends, Request, Response

from workflow.adapters import repo
from workflow.server.errors import ApiError

SESSION_COOKIE = "wf_session"


def utc_now() -> str:
    """서버 수신 시각. RFC 3339 UTC, `Z` 표기 — repo 의 `now` 인자 형식."""
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def get_conn(request: Request) -> Iterator[Connection]:
    conn = request.app.state.conn_factory()
    try:
        yield conn
    finally:
        conn.close()


# --- 세션 서명 ---------------------------------------------------------------


def _mac(session_id: str, secret: str) -> str:
    return hmac.new(secret.encode(), session_id.encode(), hashlib.sha256).hexdigest()


def sign_session(session_id: str, secret: str) -> str:
    return f"{session_id}.{_mac(session_id, secret)}"


def verify_session(cookie: str, secret: str) -> str | None:
    session_id, dot, mac = cookie.rpartition(".")
    if not dot or not session_id:
        return None
    return session_id if hmac.compare_digest(mac, _mac(session_id, secret)) else None


# --- 의존성 ------------------------------------------------------------------


def _bearer_token(request: Request) -> str | None:
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    token = token.strip()
    return token if scheme.lower() == "bearer" and token else None


def require_connector(request: Request, conn: Connection = Depends(get_conn)) -> str:
    """`Authorization: Bearer wfc_…` → connector_id. 없거나 무효·취소면 401. 토큰은 메시지에 넣지 않는다."""
    token = _bearer_token(request)
    connector_id = repo.authenticate_connector(conn, token) if token else None
    if connector_id is None:
        raise ApiError(401, "unauthenticated", "유효한 연결 토큰이 필요합니다.")
    return connector_id


def require_source_token(request: Request, conn: Connection = Depends(get_conn)) -> Row:
    """`Authorization: Bearer wfs_…` → source_tokens 행(token_id·session_id·source). 없거나 취소면 401 unauthenticated.
    성공 시 touch_source_token(last_used_at). 토큰 원문을 메시지에 넣지 않는다. 세션 쿠키로는 통과하지 않는다(CSRF)."""
    token = _bearer_token(request)
    row = repo.authenticate_source_token(conn, token) if token else None
    if row is None:
        raise ApiError(401, "unauthenticated", "유효한 입구 토큰이 필요합니다.")
    repo.touch_source_token(conn, row["token_id"], utc_now())
    return row


def _session_from_cookie(request: Request, conn: Connection) -> str | None:
    cookie = request.cookies.get(SESSION_COOKIE)
    if not cookie:
        return None
    session_id = verify_session(cookie, request.app.state.settings.session_secret)
    if session_id is None or repo.get_session(conn, session_id) is None:
        return None
    return session_id


def require_session(
    request: Request, response: Response, conn: Connection = Depends(get_conn)
) -> str:
    """심사자 세션. 유효한 쿠키가 없으면 새 세션을 만들고 Set-Cookie(HttpOnly, SameSite=Lax, 14일)."""
    session_id = _session_from_cookie(request, conn)
    if session_id is None:
        settings = request.app.state.settings
        session_id = f"sess-{secrets.token_hex(16)}"
        repo.create_session(conn, session_id, utc_now())
        response.set_cookie(
            SESSION_COOKIE,
            sign_session(session_id, settings.session_secret),
            max_age=settings.session_cookie_days * 86400,
            httponly=True,
            samesite="lax",
        )
    return session_id


def require_operator(request: Request, conn: Connection = Depends(get_conn)) -> str:
    session_id = _session_from_cookie(request, conn)
    row = repo.get_session(conn, session_id) if session_id else None
    if row is None or not row["is_operator"]:
        raise ApiError(403, "forbidden", "운영자 권한이 필요합니다.")
    return session_id
