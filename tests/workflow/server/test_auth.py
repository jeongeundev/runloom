"""auth.py — 연결 토큰 인증, 운영자 확인 (ADR-0005), 입구 토큰 인증 (ADR-0010),
로그인 세션·현재 멤버·역할·Origin 검사·키별 실패 제한 (phase 15 step 4), 옛 세션 의존성의 로그인 래퍼 (step 5)."""

import dataclasses
import hashlib
import hmac

import pytest
from fastapi import Depends, Request, Response
from fastapi.testclient import TestClient

from workflow.adapters import repo
from workflow.domain import team
from workflow.server.auth import (
    LOGIN_COOKIE,
    SELFHOST_SESSION_ID,
    LoggedIn,
    LoginThrottle,
    check_origin,
    clear_login_cookies,
    current_member,
    ensure_workspace,
    get_conn,
    login_email_key,
    require_action,
    require_connector,
    require_member,
    require_member_api,
    require_operator,
    require_session,
    require_source_token,
    set_login_cookie,
)

from .conftest import NOW, bearer, exchange, log_in, log_in_other_workspace

SECRET = "test-session-secret"
OLD_SESSION_COOKIE = "wf_session"  # phase 15 이전 로그인 쿠키 — 이제 읽지 않는다


def old_workspace_cookie(session_id: str) -> str:
    """phase 15 이전 `wf_session` 값 모양(`<id>.<SESSION_SECRET HMAC>`) — 서명이 맞아도 로그인이 아니어야 한다."""
    return f"{session_id}.{hmac.new(SECRET.encode(), session_id.encode(), hashlib.sha256).hexdigest()}"


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
    assert client.cookies.get(LOGIN_COOKIE)
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


def test_selfhost_accepts_only_a_login_to_the_fixed_workspace(settings, conn):
    """다른 워크스페이스(예: demo 에서 쓰던 익명 세션)의 유효한 로그인도 미인증. 고정 워크스페이스 로그인은 운영자다."""
    client = TestClient(_selfhost_app(settings))
    repo.create_session(conn, "sess-other", NOW)
    repo.mark_operator(conn, "sess-other")
    log_in_other_workspace(client)
    assert client.get("/_probe/session", follow_redirects=False).status_code == 303
    assert client.get("/_probe/operator").status_code == 401

    log_in(client)
    assert client.get("/_probe/session").json() == {"session_id": SELFHOST_SESSION_ID}
    assert client.get("/_probe/operator").json() == {"session_id": SELFHOST_SESSION_ID}


def test_selfhost_old_workspace_cookie_is_unauthenticated(settings, conn):
    """서명이 맞는 옛 `wf_session`(고정 워크스페이스)은 워크스페이스 행이 있어도 로그인이 아니다."""
    client = TestClient(_selfhost_app(settings))
    ensure_workspace(conn, NOW)
    client.cookies.set(OLD_SESSION_COOKIE, old_workspace_cookie(SELFHOST_SESSION_ID))
    assert client.get("/_probe/session", follow_redirects=False).status_code == 303
    assert client.get("/_probe/operator").status_code == 401


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


# --- phase 15 step 4 — 로그인 세션 쿠키·현재 멤버·역할·Origin 검사·키별 실패 제한 (ARCHITECTURE "팀 — phase 15") ---

def _member_app(settings):
    from workflow.server.app import create_app

    app = create_app(settings)

    @app.get("/_probe/member")
    def _member(member: LoggedIn = Depends(require_member)):
        return dataclasses.asdict(member)

    @app.get("/_probe/member-api")
    def _member_api(member: LoggedIn = Depends(require_member_api)):
        return {"member_id": member.member_id}

    @app.get("/_probe/team")
    def _team(member: LoggedIn = Depends(require_action(team.MANAGE_TEAM))):
        return {"member_id": member.member_id}

    @app.get("/_probe/team-api")
    def _team_api(member: LoggedIn = Depends(require_action(team.MANAGE_TEAM, api=True))):
        return {"member_id": member.member_id}

    @app.get("/_probe/work-api")
    def _work_api(member: LoggedIn = Depends(require_action(team.CREATE_WORK, api=True))):
        return {"member_id": member.member_id}

    @app.get("/_probe/twice")
    def _twice(request: Request, conn=Depends(get_conn), member: LoggedIn = Depends(require_member)):
        again = current_member(request, conn)
        return {"same": again is member}

    @app.post("/_probe/post")
    def _post():
        return {"ok": True}

    @app.post("/sources/tokens/_probe")
    def _cookie_path():
        return {"ok": True}

    return app


def _account(conn, *, role: str = "admin", email: str = "admin@example.com") -> str:
    """고정 워크스페이스의 계정 있는 멤버. admin 은 첫 관리자 행에 계정을 채우고, member 는 새로 더한다."""
    ensure_workspace(conn, NOW)
    if role == "admin":
        member_id = repo.ensure_first_admin(conn, SELFHOST_SESSION_ID, now=NOW)
    else:
        member_id = repo.add_member(conn, SELFHOST_SESSION_ID, display_name="멤버", role=role, now=NOW)
    repo.set_member_credentials(conn, SELFHOST_SESSION_ID, member_id, email=email,
                                password_hash=team.DUMMY_PASSWORD_HASH, now=NOW)
    return member_id


def _logged_in(settings, conn, *, role: str = "admin", email: str = "admin@example.com", now: str = NOW):
    client = TestClient(_member_app(settings))
    member_id = _account(conn, role=role, email=email)
    token = repo.create_login_session(conn, SELFHOST_SESSION_ID, member_id, now=now, days=14)
    client.cookies.set(LOGIN_COOKIE, token)
    return client, member_id, token


def _set_cookie_header(response: Response) -> str:
    (header,) = [v for k, v in response.raw_headers if k == b"set-cookie"]
    return header.decode().lower()


# 쿠키 ------------------------------------------------------------------------------------------


def test_login_cookie_is_httponly_lax_14_days_and_not_secure_without_https(settings):
    response = Response()
    set_login_cookie(response, "tok-raw", settings)
    header = _set_cookie_header(response)
    assert LOGIN_COOKIE == "wf_login"
    assert header.startswith("wf_login=tok-raw;")
    assert "httponly" in header and "samesite=lax" in header and "path=/" in header
    assert f"max-age={14 * 86400}" in header
    assert "secure" not in header

    response = Response()
    set_login_cookie(response, "tok-raw", dataclasses.replace(settings, public_url="http://127.0.0.1:8000"))
    assert "secure" not in _set_cookie_header(response)


def test_login_cookie_is_secure_when_public_url_is_https(settings):
    response = Response()
    set_login_cookie(response, "tok-raw", dataclasses.replace(settings, public_url="https://runloom.example"))
    assert "secure" in _set_cookie_header(response)


def test_clear_login_cookies_deletes_login_and_old_session_cookie():
    response = Response()
    clear_login_cookies(response)
    headers = [v.decode() for k, v in response.raw_headers if k == b"set-cookie"]
    assert {h.split("=", 1)[0] for h in headers} == {LOGIN_COOKIE, OLD_SESSION_COOKIE}
    assert all("Max-Age=0" in h for h in headers)


# 현재 멤버 --------------------------------------------------------------------------------------


def test_current_member_from_login_cookie(settings, conn):
    client, member_id, _ = _logged_in(settings, conn)
    body = client.get("/_probe/member").json()
    assert body["member_id"] == member_id
    assert body["session_id"] == SELFHOST_SESSION_ID
    assert body["role"] == "admin"
    assert body["display_name"] == "관리자"
    assert body["login_id"].startswith("lgn-")
    assert "password_hash" not in body and "email" not in body


def test_no_cookie_redirects_page_and_401s_api(settings, conn):
    client = TestClient(_member_app(settings))
    response = client.get("/_probe/member", follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == "/login"
    response = client.get("/_probe/member-api")
    assert response.status_code == 401 and response.json()["code"] == "unauthenticated"
    assert client.get("/_probe/team-api").status_code == 401


def test_revoked_login_session_is_rejected(settings, conn):
    client, _, token = _logged_in(settings, conn)
    assert client.get("/_probe/member-api").status_code == 200
    repo.revoke_login_session(conn, token, now=NOW)
    assert client.get("/_probe/member-api").status_code == 401
    assert client.get("/_probe/member", follow_redirects=False).status_code == 303


def test_expired_login_session_is_rejected(settings, conn):
    client, _, _ = _logged_in(settings, conn, now="2000-01-01T00:00:00Z")  # 14일 뒤 만료 — 이미 지남
    assert client.get("/_probe/member-api").status_code == 401


def test_disabled_member_cookie_is_rejected(settings, conn):
    _account(conn)  # 관리자가 남아 있어야 멤버를 끌 수 있다
    client, member_id, token = _logged_in(settings, conn, role="member", email="m@example.com")
    assert client.get("/_probe/member-api").status_code == 200
    conn.execute("UPDATE members SET disabled_at = ? WHERE member_id = ?", (NOW, member_id))
    assert client.get("/_probe/member-api").status_code == 401


def test_member_of_another_workspace_is_rejected(settings, conn):
    client = TestClient(_member_app(settings))
    repo.create_session(conn, "sess-other", NOW)
    other = repo.ensure_first_admin(conn, "sess-other", now=NOW)
    repo.set_member_credentials(conn, "sess-other", other, email="o@example.com",
                                password_hash=team.DUMMY_PASSWORD_HASH, now=NOW)
    client.cookies.set(LOGIN_COOKIE, repo.create_login_session(conn, "sess-other", other, now=NOW, days=14))
    assert client.get("/_probe/member-api").status_code == 401


def test_old_workspace_session_cookie_is_ignored(settings, conn):
    """옛 `wf_session`(서명된 고정 워크스페이스)은 새 의존성에서 로그인이 아니다."""
    client = TestClient(_member_app(settings))
    _account(conn)
    client.cookies.set(OLD_SESSION_COOKIE, old_workspace_cookie(SELFHOST_SESSION_ID))
    assert client.get("/_probe/member", follow_redirects=False).status_code == 303
    assert client.get("/_probe/member-api").status_code == 401


def test_garbage_login_cookie_is_rejected(settings, conn):
    client = TestClient(_member_app(settings))
    _account(conn)
    client.cookies.set(LOGIN_COOKIE, "not-a-token")
    assert client.get("/_probe/member-api").status_code == 401


def test_current_member_reads_db_once_per_request(settings, conn, monkeypatch):
    client, _, _ = _logged_in(settings, conn)
    calls = []
    original = repo.member_for_login_token

    def counting(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(repo, "member_for_login_token", counting)
    assert client.get("/_probe/twice").json() == {"same": True}
    assert len(calls) == 1


# 역할 × 동작 -------------------------------------------------------------------------------------


def test_require_action_follows_role_table(settings, conn):
    admin, admin_id, _ = _logged_in(settings, conn)
    assert admin.get("/_probe/team").json() == {"member_id": admin_id}
    assert admin.get("/_probe/team-api").json() == {"member_id": admin_id}

    member = TestClient(admin.app)
    member_id = _account(conn, role="member", email="m@example.com")
    member.cookies.set(LOGIN_COOKIE, repo.create_login_session(conn, SELFHOST_SESSION_ID, member_id, now=NOW, days=14))
    assert team.can("member", team.CREATE_WORK) and not team.can("member", team.MANAGE_TEAM)
    assert member.get("/_probe/work-api").json() == {"member_id": member_id}

    page = member.get("/_probe/team")
    assert page.status_code == 403
    assert page.headers["content-type"].startswith("text/html")
    assert "forbidden" in page.text and "관리자 권한이 필요합니다." in page.text

    api = member.get("/_probe/team-api")
    assert api.status_code == 403
    assert api.json() == {"code": "forbidden", "message": "관리자 권한이 필요합니다.", "field": None, "details": None}


def test_role_change_applies_on_next_request(settings, conn):
    """권한은 요청마다 DB 의 역할로 판정한다 — 세션을 폐기하지 않아도 바로 반영."""
    _account(conn)
    client, member_id, _ = _logged_in(settings, conn, role="member", email="m@example.com")
    assert client.get("/_probe/team-api").status_code == 403
    repo.set_member_role(conn, SELFHOST_SESSION_ID, member_id, "admin", now=NOW)
    assert client.get("/_probe/team-api").status_code == 200


def test_require_action_rejects_unknown_action():
    with pytest.raises(ValueError):
        require_action("manage_everything")


# Origin 검사 -----------------------------------------------------------------------------------


def test_same_origin_post_passes_and_get_is_not_checked(settings):
    client = TestClient(_member_app(settings))
    assert client.headers["origin"] == "http://testserver"  # tests/conftest.py 기본 헤더
    assert client.post("/_probe/post").json() == {"ok": True}
    assert client.post("/_probe/post", headers={"Origin": "HTTP://TestServer:80"}).status_code == 200
    del client.headers["origin"]
    assert client.get("/_probe/member", follow_redirects=False).status_code == 303  # GET 은 검사하지 않는다


@pytest.mark.parametrize("origin", ["http://evil.example", "https://testserver", "http://testserver:8080", "null"])
def test_cross_origin_post_is_403(settings, origin):
    client = TestClient(_member_app(settings))
    response = client.post("/_probe/post", headers={"Origin": origin})
    assert response.status_code == 403
    assert response.json() == {
        "code": "forbidden_origin", "message": "요청 출처를 확인할 수 없습니다.", "field": None, "details": None,
    }


def test_cross_origin_form_post_gets_html_error(settings):
    client = TestClient(_member_app(settings))
    response = client.post("/_probe/post", headers={"Origin": "http://evil.example", "Accept": "text/html"})
    assert response.status_code == 403
    assert response.headers["content-type"].startswith("text/html")
    assert "forbidden_origin" in response.text and "요청 출처를 확인할 수 없습니다." in response.text


def test_missing_origin_falls_back_to_referer(settings):
    client = TestClient(_member_app(settings))
    del client.headers["origin"]
    assert client.post("/_probe/post").status_code == 403  # 둘 다 없음
    assert client.post("/_probe/post", headers={"Referer": "http://testserver/tasks?x=1"}).status_code == 200
    assert client.post("/_probe/post", headers={"Referer": "http://evil.example/tasks"}).status_code == 403
    assert client.post("/_probe/post", headers={"Referer": "not a url"}).status_code == 403
    assert client.post("/sources/tokens/_probe").status_code == 403  # /sources/tokens 는 쿠키 경로


def test_public_url_origin_passes(settings):
    client = TestClient(_member_app(dataclasses.replace(settings, public_url="https://runloom.example")))
    assert client.post("/_probe/post", headers={"Origin": "https://runloom.example"}).status_code == 200
    assert client.post("/_probe/post", headers={"Origin": "https://RUNLOOM.example:443"}).status_code == 200
    assert client.post("/_probe/post", headers={"Origin": "http://runloom.example"}).status_code == 403
    assert client.post("/_probe/post").status_code == 200  # 요청 base URL 도 그대로 허용


def test_bearer_paths_are_not_origin_checked(settings, conn):
    client = TestClient(_member_app(settings))
    client.headers["origin"] = "http://evil.example"
    ensure_workspace(conn, NOW)
    connector_id, token = exchange(client, conn)  # POST /connector/exchange
    assert connector_id
    response = client.post("/executions/exec-none/events", json={}, headers=bearer(token))
    assert response.json()["code"] != "forbidden_origin"
    response = client.post("/sources/n8n/chains", json={})
    assert response.status_code == 401 and response.json()["code"] == "unauthenticated"
    del client.headers["origin"]
    assert client.post("/connector/exchange", json={}).status_code != 403


def test_check_origin_function_rules(settings):
    def request(method: str, path: str, headers: dict[str, str], host: str = "testserver") -> Request:
        return Request({
            "type": "http", "method": method, "path": path, "root_path": "", "scheme": "http", "query_string": b"",
            "server": (host, 80), "headers": [(b"host", host.encode())] + [
                (k.lower().encode(), v.encode()) for k, v in headers.items()
            ],
        })

    assert check_origin(request("POST", "/tasks", {"Origin": "http://testserver"}), settings)
    assert not check_origin(request("POST", "/tasks", {}), settings)
    assert not check_origin(request("POST", "/tasks", {"Origin": "null"}), settings)
    assert check_origin(request("POST", "/connector/heartbeat", {}), settings)
    assert check_origin(request("PUT", "/executions/x/result", {}), settings)
    assert check_origin(request("POST", "/sources/n8n/chains", {}), settings)
    assert not check_origin(request("POST", "/sources/tokens", {}), settings)
    assert not check_origin(request("POST", "/sources/tokens/tok-1/revoke", {}), settings)
    assert not check_origin(request("POST", "/sources/n8n/chains/extra", {}), settings)


# 키별 실패 제한 ------------------------------------------------------------------------------------


def test_login_throttle_is_per_key_with_injected_clock():
    now = [1000.0]
    throttle = LoginThrottle(clock=lambda: now[0])
    for _ in range(4):
        throttle.fail("email:a@example.com")
    assert not throttle.blocked("email:a@example.com")
    throttle.fail("email:a@example.com")
    assert throttle.blocked("email:a@example.com")
    assert not throttle.blocked("email:b@example.com")  # 다른 이메일은 통과
    assert not throttle.blocked("recover")

    now[0] += 59
    assert throttle.blocked("email:a@example.com")
    now[0] += 1  # 창 60초가 지남
    assert not throttle.blocked("email:a@example.com")
    assert "email:a@example.com" not in throttle._failures  # 빈 키는 정리


def test_login_throttle_reset_clears_only_that_key():
    throttle = LoginThrottle(clock=lambda: 0.0)
    for _ in range(5):
        throttle.fail("email:a@example.com")
        throttle.fail("setup")
    throttle.reset("email:a@example.com")
    assert not throttle.blocked("email:a@example.com")
    assert throttle.blocked("setup")


def test_login_email_key_normalizes_or_falls_back_to_lowercase():
    assert login_email_key("  Admin@Example.COM ") == "email:admin@example.com"
    assert login_email_key("Not An Email") == "email:not an email"
    assert login_email_key("X" * 300) == "email:" + "x" * 254
