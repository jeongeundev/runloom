"""인증 — 워크스페이스 서명 쿠키, 연결 프로그램 Bearer 토큰, 운영자 확인 (ARCHITECTURE 인증 절),
입구 토큰 (ADR-0010 — 워크스페이스가 발급해 n8n 이 쓴다).
셀프호스트 전용(ADR-0016 결정 3, ADR-0019) — 익명 세션을 만들지 않고 고정 워크스페이스 `SELFHOST_SESSION_ID` 쿠키만 받는다.

팀(phase 15, ADR-0021): 로그인 세션 쿠키 `wf_login` → 현재 멤버(`current_member`)·역할 × 동작(`require_action`),
쿠키 인증 변경 요청의 Origin 검사(`check_origin`), 키별 로그인 실패 제한. 옛 `require_session`·`require_operator`·
`wf_session` 은 라우트가 옮겨 갈 때까지(step 5·6) 그대로 둔다 — 새 의존성은 `wf_session` 을 읽지 않는다.

요청 범위 의존성(`get_conn`, `utc_now`) 도 여기 둔다. 인증이 가장 먼저 DB 와 시각을 쓴다.
sqlite 연결은 요청마다 새로 열고 응답 뒤 닫는다 (`get_conn`). 앱 전역 연결을 두지 않는다.
"""

import hashlib
import hmac
import re
import time
from collections import deque
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from sqlite3 import Connection, Row
from urllib.parse import urlsplit

from fastapi import Depends, HTTPException, Request, Response

from workflow.adapters import repo
from workflow.domain import team
from workflow.server.errors import ApiError, PageError
from workflow.server.settings import Settings

SESSION_COOKIE = "wf_session"
LOGIN_COOKIE = "wf_login"
SELFHOST_SESSION_ID = "sess-selfhost"
LOGIN_MAX_FAILURES = 5  # 이 창 안에서 연속 실패가 이만큼이면 창이 지날 때까지 로그인 429
LOGIN_FAILURE_WINDOW_SECONDS = 60


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


def workspace_session(request: Request, conn: Connection) -> str | None:
    """로그인 상태 — 쿠키가 고정 워크스페이스를 가리키고 그 행이 있을 때만 그 id."""
    session_id = _session_from_cookie(request, conn)
    return session_id if session_id == SELFHOST_SESSION_ID else None


def ensure_workspace(conn: Connection, now: str) -> None:
    """고정 워크스페이스 행을 한 번 만든다(내장 종류·규칙 seed + 운영자). 이미 있으면 그대로."""
    if repo.get_session(conn, SELFHOST_SESSION_ID) is None:
        repo.create_session(conn, SELFHOST_SESSION_ID, now)
        repo.mark_operator(conn, SELFHOST_SESSION_ID)


def set_session_cookie(response: Response, session_id: str, settings: Settings) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        sign_session(session_id, settings.session_secret),
        max_age=settings.session_cookie_days * 86400,
        httponly=True,
        samesite="lax",
    )


class LoginThrottle:
    """프로세스 메모리의 키별 로그인 실패 시각. 창 안 실패가 한도에 닿으면 그 키를 막고, 성공하면 그 키만 비운다.
    키: 로그인·비밀번호 변경 = `login_email_key(입력)`, 첫 설정 = `setup`, 복구 = `recover`. 시계는 테스트가 주입한다."""

    def __init__(self, max_failures: int = LOGIN_MAX_FAILURES, window: float = LOGIN_FAILURE_WINDOW_SECONDS,
                 clock: Callable[[], float] = time.monotonic):
        self.max_failures = max_failures
        self.window = window
        self._clock = clock
        self._failures: dict[str, deque[float]] = {}

    def _prune(self, key: str) -> None:
        failures = self._failures[key]
        cutoff = self._clock() - self.window
        while failures and failures[0] <= cutoff:
            failures.popleft()
        if not failures:
            del self._failures[key]

    def blocked(self, key: str) -> bool:
        if key in self._failures:
            self._prune(key)
        return len(self._failures.get(key, ())) >= self.max_failures

    def fail(self, key: str) -> None:
        for other in list(self._failures):  # 창이 지난 키를 정리한다 — 키 수가 창 안 실패 수를 넘지 않는다
            self._prune(other)
        self._failures.setdefault(key, deque()).append(self._clock())

    def reset(self, key: str) -> None:
        self._failures.pop(key, None)


def login_email_key(raw: str) -> str:
    """이메일별 실패 제한 키 — 정규화한 이메일, 형식이 틀리면 입력을 소문자로 254자까지."""
    return "email:" + (team.normalize_email(raw) or raw.lower()[:254])


def require_session(request: Request, conn: Connection = Depends(get_conn)) -> str:
    """로그인한 고정 워크스페이스. 세션을 만들지 않는다 — 로그인 전이면 `/login` 으로 303."""
    session_id = workspace_session(request, conn)
    if session_id is None:
        raise HTTPException(303, "로그인이 필요합니다.", headers={"Location": "/login"})
    return session_id


def require_operator(request: Request, conn: Connection = Depends(get_conn)) -> str:
    """로그인 = 운영자(고정 워크스페이스는 `ensure_workspace` 가 운영자로 만든다). 로그인 전이면 401 unauthenticated."""
    session_id = workspace_session(request, conn)
    if session_id is None:
        raise ApiError(401, "unauthenticated", "로그인이 필요합니다.")
    row = repo.get_session(conn, session_id)
    if row is None or not row["is_operator"]:
        raise ApiError(403, "forbidden", "운영자 권한이 필요합니다.")
    return session_id


# --- 로그인 세션·현재 멤버 (phase 15) --------------------------------------------------------


@dataclass(frozen=True)
class LoggedIn:
    """로그인한 활성 멤버. 비밀번호 해시·이메일은 싣지 않는다."""

    session_id: str
    member_id: str
    role: str
    display_name: str
    login_id: str


_UNREAD = object()


def current_member(request: Request, conn: Connection) -> LoggedIn | None:
    """쿠키 `wf_login` → 유효한 로그인 세션 + 활성 멤버 + 고정 워크스페이스. 요청마다 한 번만 읽는다(`request.state`)."""
    cached = getattr(request.state, "logged_in", _UNREAD)
    if cached is not _UNREAD:
        return cached
    member = None
    token = request.cookies.get(LOGIN_COOKIE)
    if token:
        row = repo.member_for_login_token(conn, token, now=utc_now())
        if row is not None and row["session_id"] == SELFHOST_SESSION_ID:
            member = LoggedIn(session_id=row["session_id"], member_id=row["member_id"], role=row["role"],
                              display_name=row["display_name"], login_id=row["login_id"])
    request.state.logged_in = member
    return member


def require_member(request: Request, conn: Connection = Depends(get_conn)) -> LoggedIn:
    """화면 — 로그인 전이면 `/login` 으로 303."""
    member = current_member(request, conn)
    if member is None:
        raise HTTPException(303, "로그인이 필요합니다.", headers={"Location": "/login"})
    return member


def require_member_api(request: Request, conn: Connection = Depends(get_conn)) -> LoggedIn:
    """API — 로그인 전이면 401 unauthenticated."""
    member = current_member(request, conn)
    if member is None:
        raise ApiError(401, "unauthenticated", "로그인이 필요합니다.")
    return member


def require_action(action: str, *, api: bool = False) -> Callable[..., LoggedIn]:
    """의존성 팩토리 — 역할이 `team.can(role, action)` 을 통과하지 못하면 403 forbidden(화면 HTML·API JSON).
    모르는 동작은 라우트를 만들 때 ValueError."""
    if action not in team.ACTIONS:
        raise ValueError(f"모르는 동작: {action}")
    error = ApiError if api else PageError

    def dependency(member: LoggedIn = Depends(require_member_api if api else require_member)) -> LoggedIn:
        if not team.can(member.role, action):
            raise error(403, "forbidden", "관리자 권한이 필요합니다.")
        return member

    return dependency


def set_login_cookie(response: Response, token: str, settings: Settings) -> None:
    """로그인 세션 원문 토큰. 공개 주소가 https 일 때만 Secure."""
    response.set_cookie(
        LOGIN_COOKIE,
        token,
        max_age=settings.session_cookie_days * 86400,
        path="/",
        httponly=True,
        samesite="lax",
        secure=settings.public_url.lower().startswith("https://"),
    )


def clear_login_cookies(response: Response) -> None:
    """로그아웃 — 새 로그인 쿠키와 옛 `wf_session` 을 모두 지운다."""
    response.delete_cookie(LOGIN_COOKIE, path="/")
    response.delete_cookie(SESSION_COOKIE, path="/")


# --- Origin 검사 (phase 15) ---------------------------------------------------------------------

# Bearer 인증 경로 — 쿠키를 보지 않으므로 검사하지 않는다. `/sources/tokens…` 는 쿠키 경로라 검사 대상
_BEARER_PREFIXES = ("/connector/", "/executions/")
_CHAINS_PATH = re.compile(r"/sources/[^/]+/chains")


def _origin_of(url: str) -> str | None:
    """scheme://host:port (소문자, 기본 포트 채움). http(s) 가 아니거나 호스트가 없으면 None."""
    parts = urlsplit(url.strip())
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return None
    try:
        port = parts.port or (443 if parts.scheme == "https" else 80)
    except ValueError:
        return None
    return f"{parts.scheme}://{parts.hostname}:{port}"


def check_origin(request: Request, settings: Settings) -> bool:
    """쿠키 인증 변경 요청의 출처가 이 서버인가. `Origin`, 없으면 `Referer` 의 출처를 요청 base URL·공개 주소와 비교한다.
    둘 다 없거나 `Origin: null` 이면 거짓. 메서드는 보지 않는다 — 앱 미들웨어가 GET·HEAD·OPTIONS 밖에서만 부른다."""
    path = request.url.path
    if path.startswith(_BEARER_PREFIXES) or _CHAINS_PATH.fullmatch(path):
        return True
    origin = request.headers.get("origin")
    source = _origin_of(origin) if origin else _origin_of(request.headers.get("referer", ""))
    if source is None:
        return False
    allowed = {_origin_of(str(request.base_url))}
    if settings.public_url:
        allowed.add(_origin_of(settings.public_url))
    return source in allowed
