# ruff: noqa: F811 — test_task_cycle 픽스처(cycle)를 가져와 인자로 쓴다
"""GitHub 연결 경로 — App 만들기·callback·setup·PAT·[에이전트에게 맡기기] (phase 11 step 7, ADR-0017, ARCHITECTURE "경로")
와 연결 화면·[에이전트에게 맡기기] 버튼 (step 8).

실제 GitHub 는 부르지 않는다 — `app.state.github_transport` 에 가짜 GitHub(MockTransport)를 넣는다. 개인 키는 테스트 안에서
만든다. state 는 쿠키 `wf_gh_state`(서명·발급 시각 포함)와 쿼리를 비교한다. 비밀값은 응답·로그·DB 어디에도 없다.
"""

import dataclasses
import html as html_lib
import json
import logging
import re
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from workflow.adapters import repo, secret_store
from workflow.adapters.github_app import AppCredentials, save_credentials
from workflow.adapters.secret_store import SecretStore
from workflow.contracts.github import AssigneeBinding, IssuePrLink
from workflow.server import web
from workflow.server.app import create_app
from workflow.server.auth import LOGIN_COOKIE

from .conftest import log_in, log_in_member, log_in_other_workspace, session_of, task_row
from .test_task_cycle import SESSION as CYCLE_SESSION
from .test_task_cycle import (  # noqa: F401 — 픽스처
    SOURCE,
    auto_source,
    config,
    cycle,
    direct_shop_task,
    executions,
    import_issue,
)

BASE = "http://127.0.0.1:8000"
STATE_COOKIE = "wf_gh_state"
CLIENT_SECRET = "cs_SECRETclientSECRET0123"
WEBHOOK_SECRET = "wh_SECRETwebhookSECRET0123"
INSTALL_TOKEN = "ghs_SECRETinstallTOKEN0123"
PAT = "github_pat_SECRETpastedTOKEN0123456789"
CODE = "manifestCODE_0123-abc"
SLUG = "runloom-a1b2c3"


@pytest.fixture(scope="module")
def pem() -> str:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL, serialization.NoEncryption()
    ).decode()


class FakeGitHub:
    """경로별 응답. `repositories` 는 설치 저장소, `status` 로 특정 경로의 오류를 흉내 낸다."""

    def __init__(self, pem: str):
        self.pem = pem
        self.repositories = ["acme/billing", "acme/shop"]
        self.status: dict[str, int] = {}
        self.calls: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        path = request.url.path
        for prefix, status in self.status.items():
            if path.startswith(prefix):
                return httpx.Response(status, json={"message": f"no {CLIENT_SECRET}"})
        if request.method == "POST" and path == f"/app-manifests/{CODE}/conversions":
            return httpx.Response(201, json={
                "id": 12345, "client_id": "Iv23liTEST", "slug": SLUG, "name": SLUG,
                "owner": {"login": "kim-dev"}, "html_url": f"https://github.com/apps/{SLUG}",
                "client_secret": CLIENT_SECRET, "webhook_secret": WEBHOOK_SECRET, "pem": self.pem,
            })
        if request.method == "GET" and path == "/app/installations/42":
            return httpx.Response(200, json={"id": 42, "account": {"login": "acme"}, "repository_selection": "all"})
        if request.method == "POST" and path == "/app/installations/42/access_tokens":
            return httpx.Response(201, json={"token": INSTALL_TOKEN, "expires_at": "2099-01-01T00:00:00Z"})
        if request.method == "GET" and path == "/installation/repositories":
            items = [{"id": 700 + i, "full_name": name} for i, name in enumerate(self.repositories)]
            return httpx.Response(200, json={"total_count": len(items), "repositories": items})
        if request.method == "GET" and path == "/repos/acme/lib":
            return httpx.Response(200, json={"id": 900, "full_name": "acme/lib"})
        return httpx.Response(404, json={"message": "Not Found"})

    def paths(self) -> list[str]:
        return [f"{r.method} {r.url.path}" for r in self.calls]


@pytest.fixture
def settings(settings):
    return dataclasses.replace(settings, github_repos=("acme/billing",))


@pytest.fixture
def github(pem) -> FakeGitHub:
    return FakeGitHub(pem)


@pytest.fixture
def app(settings, github):
    app = create_app(settings)
    app.state.github_transport = httpx.MockTransport(github)
    return app


@pytest.fixture
def secrets(settings) -> SecretStore:
    return SecretStore(settings.secret_dir)


@pytest.fixture
def op(app) -> TestClient:
    """고정 워크스페이스에 `/login` 으로 로그인한 클라이언트(127.0.0.1 요청 주소) — 셀프호스트는 로그인 = 운영자."""
    return log_in(TestClient(app, base_url=BASE))


def op_session(client: TestClient) -> str:
    return session_of(client)


def error(response, status: int, code: str) -> None:
    assert response.status_code == status, response.text
    assert f"<code>{code}</code>" in response.text, response.text


def state_of(url: str) -> str:
    return parse_qs(urlsplit(url).query)["state"][0]


def start(client: TestClient, **params) -> tuple[str, dict]:
    """[GitHub 연결] → (폼 state, manifest)."""
    response = client.get("/operator/github/app/new", params=params)
    assert response.status_code == 200, response.text
    action = html_lib.unescape(re.search(r'<form id="gh-manifest-form"[^>]*action="([^"]+)"', response.text).group(1))
    manifest = json.loads(html_lib.unescape(re.search(r'name="manifest" value="([^"]*)"', response.text).group(1)))
    return state_of(action), manifest


def save_app(secrets: SecretStore, pem: str) -> None:
    save_credentials(secrets, AppCredentials(
        app_id=12345, client_id="Iv23liTEST", slug=SLUG, name=SLUG, owner_login="kim-dev",
        html_url=f"https://github.com/apps/{SLUG}", client_secret=CLIENT_SECRET, webhook_secret=WEBHOOK_SECRET,
        pem=pem,
    ), "2026-09-27T10:00:00Z")


def names(conn, session_id: str) -> dict:
    return {s.repository_full_name: s for s in repo.list_github_sources(conn, session_id)}


# --- 권한 ----------------------------------------------------------------------------------------


def test_every_path_is_operator_only(app, conn, github):
    # 로그인 전, 그리고 고정 워크스페이스가 아닌 워크스페이스의 로그인 쿠키 — 셀프호스트에서는 둘 다 로그인 안 된 것
    stranger = log_in_other_workspace(TestClient(app, base_url=BASE))
    for anonymous in (TestClient(app, base_url=BASE), stranger):
        for url in ("/operator/github/app/new", f"/operator/github/app/callback?code={CODE}&state=x",
                    "/operator/github/app/setup?installation_id=42"):
            response = anonymous.get(url, follow_redirects=False)
            assert (response.status_code, response.headers["location"]) == (303, "/login"), url
        response = anonymous.post("/operator/github/token", data={"token": PAT, "repository_full_name": "acme/lib"},
                                  follow_redirects=False)
        assert (response.status_code, response.headers["location"]) == (303, "/login")
    assert github.calls == []
    assert repo.github_source_sessions(conn) == []


def test_selfhost_without_login_redirects_to_login(app, github):
    client = TestClient(app, base_url=BASE)
    for url in ("/operator/github/app/new", "/operator/github/app/setup?installation_id=42"):
        response = client.get(url, follow_redirects=False)
        assert response.status_code == 303 and response.headers["location"] == "/login"
    assert github.calls == []


# --- App 만들기 ------------------------------------------------------------------------------------


def test_new_renders_manifest_form_with_state_cookie(op):
    response = op.get("/operator/github/app/new")

    assert response.status_code == 200
    state, manifest = start(op)
    action = re.search(r'<form id="gh-manifest-form"[^>]*action="([^"]+)"', op.get("/operator/github/app/new").text).group(1)
    assert html_lib.unescape(action).startswith("https://github.com/settings/apps/new?state=")
    assert manifest["redirect_url"] == f"{BASE}/operator/github/app/callback"
    assert manifest["setup_url"] == f"{BASE}/operator/github/app/setup"
    assert "hook_attributes" not in manifest and manifest["public"] is False
    assert re.fullmatch(r"runloom-[a-z0-9]{6}", manifest["name"])
    cookie = response.headers["set-cookie"]
    assert cookie.startswith(f"{STATE_COOKIE}=")
    lowered = cookie.lower()
    for part in ("httponly", "samesite=lax", "path=/operator/github/app", "max-age=3600"):
        assert part in lowered, cookie
    assert len(state) >= 40


def test_new_for_an_organization_uses_the_org_url(op):
    response = op.get("/operator/github/app/new", params={"org": "acme-inc"})
    action = html_lib.unescape(re.search(r'<form id="gh-manifest-form"[^>]*action="([^"]+)"', response.text).group(1))
    assert action.startswith("https://github.com/organizations/acme-inc/settings/apps/new?state=")
    error(op.get("/operator/github/app/new", params={"org": "../evil"}), 422, "invalid_field")


def test_public_url_is_the_base_when_set(settings, github):
    app = create_app(dataclasses.replace(settings, public_url="https://runloom.example"))
    app.state.github_transport = httpx.MockTransport(github)
    # 요청 주소 testserver 는 쓰지 않는다. 공개 주소가 https 면 로그인 쿠키가 Secure — 브라우저처럼 https 로 연다
    client = log_in(TestClient(app, base_url="https://testserver"))
    _, manifest = start(client)
    assert manifest["redirect_url"] == "https://runloom.example/operator/github/app/callback"


def test_non_loopback_request_host_needs_public_url(app):
    client = log_in(TestClient(app))  # http://testserver
    error(client.get("/operator/github/app/new"), 400, "public_url_required")
    assert STATE_COOKIE not in client.cookies


def test_saved_app_skips_to_the_installation_screen(op, secrets, pem):
    save_app(secrets, pem)

    response = op.get("/operator/github/app/new", follow_redirects=False)

    assert response.status_code == 303
    location = response.headers["location"]
    assert location.startswith(f"https://github.com/apps/{SLUG}/installations/new?state=")
    assert STATE_COOKIE in response.headers["set-cookie"]


# --- callback -------------------------------------------------------------------------------------


def test_callback_saves_the_app_and_redirects_to_install_with_a_new_state(op, github, secrets, pem):
    state, _ = start(op)

    response = op.get("/operator/github/app/callback", params={"code": CODE, "state": state}, follow_redirects=False)

    assert response.status_code == 303
    location = response.headers["location"]
    assert location.startswith(f"https://github.com/apps/{SLUG}/installations/new?state=")
    assert state_of(location) != state
    assert secrets.read(secret_store.GITHUB_APP_PRIVATE_KEY) == pem
    assert secrets.read(secret_store.GITHUB_APP_CLIENT_SECRET) == CLIENT_SECRET
    assert secrets.read(secret_store.GITHUB_APP_WEBHOOK_SECRET) == WEBHOOK_SECRET
    assert json.loads(secrets.read(secret_store.GITHUB_APP_INFO))["slug"] == SLUG
    assert github.paths() == [f"POST /app-manifests/{CODE}/conversions"]
    # 한 번 쓴 state 는 다시 통하지 않는다
    again = op.get("/operator/github/app/callback", params={"code": CODE, "state": state}, follow_redirects=False)
    error(again, 403, "github_state_invalid")
    assert len(github.calls) == 1


def test_callback_rejects_a_wrong_or_missing_state(op, app, github, secrets):
    start(op)
    error(op.get("/operator/github/app/callback", params={"code": CODE, "state": "forged"}), 403,
          "github_state_invalid")
    fresh = TestClient(app, base_url=BASE)
    fresh.cookies.set(LOGIN_COOKIE, op.cookies[LOGIN_COOKIE])  # 같은 운영자, state 쿠키 없음
    error(fresh.get("/operator/github/app/callback", params={"code": CODE, "state": "forged"}), 403,
          "github_state_invalid")
    assert github.calls == []
    assert not secrets.exists(secret_store.GITHUB_APP_PRIVATE_KEY)


def test_callback_rejects_an_expired_state(op, github, monkeypatch):
    state, _ = start(op)
    issued = web._epoch()
    monkeypatch.setattr(web, "_epoch", lambda: issued + 3601)

    error(op.get("/operator/github/app/callback", params={"code": CODE, "state": state}), 403, "github_state_invalid")
    assert github.calls == []


@pytest.mark.parametrize("status", [404, 422])
def test_failed_exchange_shows_an_error_without_code_or_body(op, github, secrets, status):
    github.status["/app-manifests/"] = status
    state, _ = start(op)

    response = op.get("/operator/github/app/callback", params={"code": CODE, "state": state})

    error(response, 400, "github_manifest_failed")
    assert "GitHub 연결" in response.text
    assert CODE not in response.text and CLIENT_SECRET not in response.text
    assert not secrets.exists(secret_store.GITHUB_APP_INFO)


def test_malformed_code_is_rejected_without_calling_github(op, github):
    state, _ = start(op)
    error(op.get("/operator/github/app/callback", params={"code": "../x", "state": state}), 400,
          "github_manifest_failed")
    assert github.calls == []


# --- setup ----------------------------------------------------------------------------------------


def test_setup_creates_a_source_per_installed_repository(op, conn, github, secrets, pem):
    save_app(secrets, pem)
    install = op.get("/operator/github/app/new", follow_redirects=False).headers["location"]

    response = op.get("/operator/github/app/setup",
                      params={"installation_id": 42, "setup_action": "install", "state": state_of(install)},
                      follow_redirects=False)

    assert response.status_code == 303 and response.headers["location"] == "/repos"
    sources = names(conn, op_session(op))
    assert sorted(sources) == ["acme/billing", "acme/shop"]
    assert all(s.installation_id == 42 and s.intake == "all_open" and s.trigger_label == "runloom"
               and s.run_mode == "auto" for s in sources.values())
    assert github.paths()[0] == "GET /app/installations/42"  # 설치 확인이 먼저
    assert "GET /installation/repositories" in github.paths()


def test_setup_again_is_idempotent_and_stops_removed_repositories(op, conn, github, secrets, pem):
    save_app(secrets, pem)
    assert op.get("/operator/github/app/setup", params={"installation_id": 42}, follow_redirects=False).status_code == 303
    before = names(conn, op_session(op))

    assert op.get("/operator/github/app/setup", params={"installation_id": 42}, follow_redirects=False).status_code == 303
    assert names(conn, op_session(op)) == before

    github.repositories = ["acme/billing"]
    assert op.get("/operator/github/app/setup", params={"installation_id": 42}, follow_redirects=False).status_code == 303
    after = names(conn, op_session(op))
    assert after["acme/shop"].enabled is False
    assert after["acme/billing"] == before["acme/billing"]


def test_setup_with_a_wrong_state_is_rejected(op, conn, github, secrets, pem):
    save_app(secrets, pem)
    op.get("/operator/github/app/new", follow_redirects=False)
    error(op.get("/operator/github/app/setup", params={"installation_id": 42, "state": "forged"}), 403,
          "github_state_invalid")
    assert github.calls == [] and repo.github_source_sessions(conn) == []


def test_setup_without_a_saved_app(op, github):
    error(op.get("/operator/github/app/setup", params={"installation_id": 42}), 409, "github_app_missing")
    assert github.calls == []


def test_setup_for_another_apps_installation(op, conn, github, secrets, pem):
    save_app(secrets, pem)
    error(op.get("/operator/github/app/setup", params={"installation_id": 777}), 400, "github_installation_invalid")
    assert repo.github_source_sessions(conn) == []


def test_setup_when_github_is_down(op, conn, github, secrets, pem):
    save_app(secrets, pem)
    github.status["/installation/repositories"] = 502
    error(op.get("/operator/github/app/setup", params={"installation_id": 42}), 502, "github_unavailable")
    assert repo.github_source_sessions(conn) == []


def test_setup_when_another_workspace_owns_github(op, conn, secrets, pem):
    save_app(secrets, pem)
    # 다른 워크스페이스(운영자 세션)가 이미 GitHub 연결(소스)을 가짐 — DB 에 직접 둔다
    repo.create_session(conn, "sess-other", "2026-10-06T12:00:00Z")
    repo.mark_operator(conn, "sess-other")
    repo.save_github_source(conn, "sess-other", config(), "2026-10-06T12:00:00Z")
    error(op.get("/operator/github/app/setup", params={"installation_id": 42}), 409, "github_workspace_taken")
    assert repo.github_source_sessions(conn) == ["sess-other"]


# --- PAT (고급) -----------------------------------------------------------------------------------


def test_pasted_token_is_checked_saved_and_makes_a_source(op, conn, github, secrets):
    response = op.post("/operator/github/token", data={"token": f"  {PAT} ", "repository_full_name": "acme/lib"},
                       follow_redirects=False)

    assert response.status_code == 303 and response.headers["location"] == "/repos"
    assert github.calls[0].headers["authorization"] == f"Bearer {PAT}"
    assert secrets.read(secret_store.GITHUB_TOKEN) == PAT
    lib = names(conn, op_session(op))["acme/lib"]
    assert (lib.intake, lib.trigger_label, lib.installation_id) == ("all_open", "runloom", None)
    assert PAT not in response.text and PAT not in str(response.headers)


def test_token_without_access_is_not_saved(op, conn, github, secrets, caplog):
    caplog.set_level(logging.DEBUG)
    response = op.post("/operator/github/token", data={"token": PAT, "repository_full_name": "acme/private"})

    error(response, 400, "github_token_invalid")
    assert PAT not in response.text and PAT not in caplog.text
    assert not secrets.exists(secret_store.GITHUB_TOKEN)
    assert repo.github_source_sessions(conn) == []


def test_token_form_rejects_a_bad_repository_name_or_empty_token(op, github, secrets):
    error(op.post("/operator/github/token", data={"token": PAT, "repository_full_name": "not a repo"}), 422,
          "invalid_field")
    error(op.post("/operator/github/token", data={"token": "  ", "repository_full_name": "acme/lib"}), 400,
          "github_token_invalid")
    assert github.calls == [] and not secrets.exists(secret_store.GITHUB_TOKEN)


# --- 비밀이 새지 않음 --------------------------------------------------------------------------------


def test_whole_flow_leaks_no_secret(op, conn, github, secrets, pem, caplog):
    caplog.set_level(logging.DEBUG)
    seen: list[str] = []

    def keep(response):
        seen.append(response.text)
        seen.append(str(response.headers))
        return response

    state, _ = start(op)
    install = keep(op.get("/operator/github/app/callback", params={"code": CODE, "state": state},
                          follow_redirects=False)).headers["location"]
    keep(op.get("/operator/github/app/setup", params={"installation_id": 42, "state": state_of(install)},
                follow_redirects=False))
    keep(op.post("/operator/github/token", data={"token": PAT, "repository_full_name": "acme/lib"},
                 follow_redirects=False))
    keep(op.get("/repos"))

    dump = "\n".join(conn.iterdump())
    key_body = pem.splitlines()[1]
    for secret in (CLIENT_SECRET, WEBHOOK_SECRET, INSTALL_TOKEN, PAT, key_body):
        for text in (*seen, caplog.text, dump):
            assert secret not in text, secret
    # manifest code 는 한 번 쓰는 값이라 비밀값 목록에는 없다 — GitHub 가 준 callback URL(접근 로그)에는 남지만 응답·DB 에는 싣지 않는다
    for text in (*seen, dump):
        assert CODE not in text


# --- [에이전트에게 맡기기] ------------------------------------------------------------------------------


@pytest.fixture
def cycle_op(client, cycle, conn):
    """이 클라이언트를 고정 워크스페이스(cycle 의 `SESSION`)에 로그인 — all_open 소스(담당 연결 FIX)의 주인."""
    repo.save_github_source(conn, CYCLE_SESSION, config(review_agent_id="agent-review", intake="all_open",
                                                         label_filter=[], trigger_label="runloom"), "2026-10-06T12:00:00Z")
    log_in(client)
    assert op_session(client) == CYCLE_SESSION
    return client


def delegated(conn, task_id: str):
    row = repo.get_source_issue_by_task(conn, CYCLE_SESSION, task_id)
    return row["delegated_by"], row["delegated_at"]


def test_delegate_records_the_instruction_once_and_starts(cycle_op, conn):
    task_id = import_issue(conn, 1, labels=[])

    response = cycle_op.post(f"/tasks/{task_id}/delegate", follow_redirects=False)

    assert response.status_code == 303 and response.headers["location"] == f"/tasks/{task_id}"
    by, at = delegated(conn, task_id)
    assert by == "operator" and at is not None
    assert len(executions(conn, task_id)) == 1

    again = cycle_op.post(f"/tasks/{task_id}/delegate", follow_redirects=False)
    assert again.status_code == 303
    assert delegated(conn, task_id) == (by, at)
    assert len(executions(conn, task_id)) == 1


def test_delegate_keeps_a_label_instruction(cycle_op, conn):
    task_id = import_issue(conn, 1, labels=["runloom"])
    issue_id = repo.get_source_issue_by_task(conn, CYCLE_SESSION, task_id)["github_issue_id"]
    repo.mark_issue_delegated(conn, session_id=CYCLE_SESSION, source_id=SOURCE, github_issue_id=issue_id,
                              by="label", now="2026-10-06T12:00:00Z")

    assert cycle_op.post(f"/tasks/{task_id}/delegate", follow_redirects=False).status_code == 303
    assert delegated(conn, task_id)[0] == "label"


def test_delegate_refuses_a_task_without_a_github_origin(cycle_op, conn):
    error(cycle_op.post(f"/tasks/{direct_shop_task(conn)}/delegate"), 409, "not_delegatable")


def test_delegate_refuses_a_closed_task(cycle_op, conn):
    task_id = import_issue(conn, 1, labels=[])
    conn.execute("UPDATE tasks SET finished_at = ? WHERE task_id = ?", ("2026-10-06T12:00:00Z", task_id))
    conn.commit()

    error(cycle_op.post(f"/tasks/{task_id}/delegate"), 409, "task_closed")
    assert delegated(conn, task_id) == (None, None)


def test_delegate_needs_a_login_to_the_owning_workspace(cycle_op, app, conn):
    task_id = import_issue(conn, 1, labels=[])
    stranger = log_in_other_workspace(TestClient(app))  # 다른 워크스페이스의 로그인 쿠키 — 로그인 안 된 것
    response = stranger.post(f"/tasks/{task_id}/delegate", follow_redirects=False)
    assert (response.status_code, response.headers["location"]) == (303, "/login")
    assert delegated(conn, task_id) == (None, None)
    # 다른 워크스페이스(운영자 세션)의 업무는 로그인 워크스페이스에서 404
    repo.mark_operator(conn, "sess-other")
    repo.insert_work_item_task(conn, {**task_row("task-other"), "session_id": "sess-other"}, "2026-10-06T12:00:00Z")
    assert cycle_op.post("/tasks/task-other/delegate").status_code == 404
    # 맡기기는 `delegate` 동작 — 멤버도 한다. 워크스페이스의 `is_operator` 는 권한 판정에 읽지 않는다 (ADR-0021)
    conn.execute("UPDATE sessions SET is_operator = 0 WHERE session_id = ?", (CYCLE_SESSION,))
    conn.commit()
    member = log_in_member(TestClient(app))
    assert member.post(f"/tasks/{task_id}/delegate", follow_redirects=False).status_code == 303
    assert delegated(conn, task_id)[0] == "operator"


# --- 연결 화면 (phase 11 step 8) ------------------------------------------------------------------------
# 기본 화면(접힌 <details> 밖)에는 내부 ID·토큰 입력 칸이 없다. 저장소 카드는 `data-source-card`, 고급 설정은 카드 안에 접혀 있다.

# phase 23: 수정·검토·판단 에이전트 select 는 카드 본문 칸(옵션은 이름만 — test_card_selects_show_agent_names_only)
ID_FIELDS = ("workflow_repository_id", "fix_verification_profile_id", "github_user_id", "start_at", "token")


def visible(text: str) -> str:
    """본문에서 접힌 영역(<details>)을 뺀 것 — 템플릿은 details 를 겹치지 않는다."""
    return re.sub(r"<details\b.*?</details>", "", text.split("<main", 1)[1], flags=re.S)


def folded(text: str, summary: str) -> str:
    for block in re.findall(r"<details\b.*?</details>", text, flags=re.S):
        if summary in block.split("</summary>", 1)[0]:
            return block
    raise AssertionError(f"접힌 영역 없음: {summary}")


def card_of(text: str, source_id: str) -> str:
    return text.split(f'data-source-card="{source_id}"', 1)[1].split("</section>", 1)[0]


def connect_app(op, secrets, pem) -> None:
    save_app(secrets, pem)
    response = op.get("/operator/github/app/setup", params={"installation_id": 42}, follow_redirects=False)
    assert response.status_code == 303, response.text


def test_page_before_connecting_is_one_button_and_a_folded_token_form(op):
    text = op.get("/repos").text

    shown = visible(text)
    assert 'href="/operator/github/app/new"' in shown and "GitHub 연결" in shown
    for name in ID_FIELDS:
        assert f'name="{name}"' not in shown, name
    token = folded(text, "고급 — 토큰으로 연결")
    assert 'action="/operator/github/token"' in token and 'type="password"' in token
    assert "저장소 추가/변경" not in text and "data-source-card" not in text


def test_page_after_app_setup_shows_a_card_per_repository(op, conn, secrets, pem):
    connect_app(op, secrets, pem)

    text = op.get("/repos").text

    shown = visible(text)
    for name in ID_FIELDS:
        assert f'name="{name}"' not in shown, name
    assert f'href="https://github.com/apps/{SLUG}/installations/new"' in shown and "저장소 추가/변경" in shown
    assert "GitHub App 연결됨 (kim-dev)" in shown  # phase 23: slug 는 "자세히"(설치 주소 속성에만)
    assert SLUG not in re.sub(r"<[^>]+>", " ", shown)
    assert SLUG in folded(text, "자세히")
    # ADR-0018: 결과 브랜치 push·초안 PR 은 하고, 병합·이슈 종료는 사람
    assert "PR·푸시" not in shown and "초안 PR" in shown and "병합·이슈 종료" in shown
    sources = names(conn, op_session(op))
    assert sorted(sources) == ["acme/billing", "acme/shop"]
    for full_name, source in sources.items():
        card = card_of(text, source.source_id)
        assert full_name in card
        assert "runloom" in card  # 트리거 라벨
        assert "수집 켜짐" in card and "아직 동기화 전" in card  # phase 23: 가져온 이슈 수 대신 [업무 N건 보기]
        assert f'href="/tasks?repo={full_name}">업무 0건 보기' in card
        assert "GitHub App 설치" in card  # 수집 자격 — 종류만
        assert "이 저장소를 등록한 러너 없음" in card and "러너 붙이기" in card
        tools = folded(card, "러너·담당 연결·기준선")  # phase 23: 기준선·담당 연결은 카드 도구 접힘
        assert f'data-json-action="/operator/github/sources/{source.source_id}/baseline"' in tools
        advanced = folded(card, "고급 설정")
        assert 'name="workflow_repository_id"' in advanced and "비워 두면 자동" in advanced
        assert 'name="intake" value="all_open"' in advanced
        assert f'data-json-action="/github/sources/{source.source_id}/assignees"' in tools


def test_card_explains_the_baseline_before_it_is_imported(op, conn, secrets, pem):
    connect_app(op, secrets, pem)
    text = op.get("/repos").text
    for source in names(conn, op_session(op)).values():
        card = folded(card_of(text, source.source_id), "러너·담당 연결·기준선")  # phase 23: 카드 도구 접힘
        assert "Runloom 도입 전 GitHub 이력으로 비교 기준을 만듭니다" in card
        assert "중앙값" not in card


def test_card_shows_the_imported_baseline_summary(op, conn, secrets, pem):
    """[기준선 가져오기] 뒤 새로고침된 카드에 결과가 보인다 — 2026-09-27 실제 사용에서 '뭐가 나오는지 모르겠다'."""
    connect_app(op, secrets, pem)
    session_id = op_session(op)
    source = names(conn, session_id)["acme/billing"]
    links = [
        IssuePrLink(issue_number=1, issue_title="a", issue_opened_at="2026-08-01T00:00:00Z", pr_number=10,
                    pr_merged_at="2026-08-01T02:00:00Z"),
        IssuePrLink(issue_number=2, issue_title="b", issue_opened_at="2026-08-02T00:00:00Z", pr_number=20,
                    pr_merged_at="2026-08-02T06:00:00Z"),
    ]
    repo.replace_baseline(conn, session_id, source.source_id, links, opened_before="2026-09-27T00:00:00Z",
                          now="2026-09-27T11:20:00Z")

    card = folded(card_of(op.get("/repos").text, source.source_id), "러너·담당 연결·기준선")  # phase 23: 카드 도구 접힘

    assert "기준선 2건" in card and "이슈 열림 → 병합 중앙값 4시간 0분" in card
    assert "2026-09-27 20:20" in card  # 가져온 시각(KST)
    assert 'href="/monitor"' in card


def test_card_title_hides_internal_ids(op, conn, secrets, pem):
    connect_app(op, secrets, pem)
    text = op.get("/repos").text
    for source in names(conn, op_session(op)).values():
        shown = re.sub(r"<[^>]+>", " ", card_of(visible(text), source.source_id))  # 보이는 글자만(요청 주소 속성 제외)
        assert source.source_id not in shown and "revision" not in shown


def test_card_shows_the_automatically_matched_agents_and_profile(client, auto_source, conn):
    log_in(client)  # auto_source 의 주인 = 고정 워크스페이스

    card = card_of(client.get("/repos").text, SOURCE)

    for value in ("billing", "vp-pytest"):
        assert f'<span class="mono">{value}</span> (자동)' in card, value
    for name in ("agent-fix", "agent-review"):  # phase 23: 에이전트 줄은 화면 이름(이 픽스처는 이름 = ID)
        assert f"<span>{name} (자동)</span>" in card, name
    assert "러너 없음" not in card


def test_card_says_when_no_credential_can_collect(op, conn, app):
    op.post("/operator/github/token", data={"token": PAT, "repository_full_name": "acme/lib"})
    (source,) = names(conn, op_session(op)).values()
    app.state.secrets.delete(secret_store.GITHUB_TOKEN)

    card = card_of(op.get("/repos").text, source.source_id)
    assert "GitHub 자격 없음" in card


def test_secret_status_is_only_connected_or_missing(op, conn, secrets, pem):
    connect_app(op, secrets, pem)
    op.post("/operator/github/token", data={"token": PAT, "repository_full_name": "acme/lib"})

    text = op.get("/repos").text

    assert "붙여 넣은 토큰 연결됨" in text and "서버 환경변수 토큰(WORKFLOW_GITHUB_TOKEN) 없음" in text
    for secret in (PAT, CLIENT_SECRET, WEBHOOK_SECRET, INSTALL_TOKEN, pem.splitlines()[1]):
        assert secret not in text


# --- [에이전트에게 맡기기] 버튼 (step 8) ---------------------------------------------------------------


def delegate_form(task_id: str) -> str:
    return f'action="/tasks/{task_id}/delegate"'


def test_delegate_button_only_for_open_tasks_without_an_instruction(cycle_op, conn):
    waiting = import_issue(conn, 1, labels=[])
    labelled = import_issue(conn, 2, labels=["runloom"])
    issue_id = repo.get_source_issue_by_task(conn, CYCLE_SESSION, labelled)["github_issue_id"]
    repo.mark_issue_delegated(conn, session_id=CYCLE_SESSION, source_id=SOURCE, github_issue_id=issue_id,
                              by="label", now="2026-10-06T12:00:00Z")
    closed = import_issue(conn, 3, labels=[])
    conn.execute("UPDATE tasks SET finished_at = ? WHERE task_id = ?", ("2026-10-06T12:00:00Z", closed))
    conn.commit()

    # phase 16: 업무 화면 표에는 행 동작 버튼이 없다(에이전트 맡기기는 패널의 담당 선택 — step 5)
    assert "/delegate" not in cycle_op.get("/tasks").text
    assert "/delegate" not in cycle_op.get("/repos").text  # phase 23: 저장소 카드에 이슈 목록·맡기기 없음

    detail = cycle_op.get(f"/tasks/{waiting}").text
    assert delegate_form(waiting) in detail
    assert 'data-blocker="not_delegated"' in detail and "실행 지시 전" in detail
    assert delegate_form(labelled) not in cycle_op.get(f"/tasks/{labelled}").text


def test_list_groups_undelegated_tasks_as_waiting_for_an_instruction(cycle_op, conn):
    waiting = import_issue(conn, 1, labels=[], assignee_ids=[], assignee_logins=[])
    conn.execute("UPDATE tasks SET status = ?, status_reason = ? WHERE task_id = ?",
                 ("대기", "실행 지시 전 — [에이전트에게 맡기기] 또는 `runloom` 라벨 · 다른 사유", waiting))
    conn.commit()

    repo.refresh_open_work_statuses(conn, now="2026-10-06T12:00:00Z")  # 워커 tick 끝과 같은 업무 상태 계산

    home = cycle_op.get("/tasks").text
    main = home[home.index('class="main'):]
    card = re.search(r'<tr class="work-row" data-work-key="RUN-1".*?</tr>', main, re.S).group(0)  # 목록 한 줄 = 업무
    # 업무 상태는 `새로 들어옴 · 담당 없음`(담당 없음이 지시 전보다 먼저 — domain/work_status). 담당 없음 행의 "다음 할 일"
    # `담당 없음` 은 담당 칸과 겹쳐 `—` 로 보인다(phase 23 step 8)
    assert 'data-status="새로 들어옴"' in card and '<td class="next-action">—' in card and "다른 사유" not in card
    assert '<span class="muted">없음</span>' in card
    detail = cycle_op.get(f"/tasks/{waiting}").text
    assert delegate_form(waiting) in detail  # 지시 전은 단계 상세의 맡기기 버튼으로(phase 16 — 패널은 step 5)
    assert "다른 사유" in detail  # 다른 사유는 상세에서


def test_no_delegate_button_for_filtered_sources_or_non_operators(client, cycle, conn):
    task_id = import_issue(conn, 1)  # filtered 소스 — 수집 = 지시
    log_in(client)
    home, detail = client.get("/tasks"), client.get(f"/tasks/{task_id}")
    assert home.status_code == detail.status_code == 200
    assert "/delegate" not in home.text
    assert "/delegate" not in detail.text


def test_member_sees_the_delegate_button_regardless_of_is_operator(cycle_op, app, conn):
    # 버튼은 `delegate` 동작(관리자·멤버)을 본다 — 워크스페이스의 `is_operator` 는 읽지 않는다 (ADR-0021)
    task_id = import_issue(conn, 1, labels=[])
    conn.execute("UPDATE sessions SET is_operator = 0 WHERE session_id = ?", (CYCLE_SESSION,))
    conn.commit()
    for client in (cycle_op, log_in_member(TestClient(app))):
        assert delegate_form(task_id) in client.get(f"/tasks/{task_id}").text
        assert delegate_form(task_id) not in client.get("/repos").text  # phase 23: 저장소 카드에도 없음(단계 상세만)


def test_home_without_github_is_unchanged(logged_in_client):
    response = logged_in_client.get("/tasks")
    assert response.status_code == 200
    text = response.text
    assert "/delegate" not in text and "지시 전" not in text


# --- phase 20: 설정 변경 기록 -------------------------------------------------------------------


def _changes(conn, session_id: str) -> list[tuple]:
    return [(r["area"], r["action"], r["subject"], r["by_member_id"])
            for r in repo.list_config_changes(conn, session_id)]


def _admin(conn, session_id: str) -> str:
    return conn.execute("SELECT member_id FROM members WHERE session_id = ?", (session_id,)).fetchone()[0]


def test_setup_records_source_changes_by_the_logged_in_member(op, conn, github, secrets, pem):
    save_app(secrets, pem)
    assert op.get("/operator/github/app/setup", params={"installation_id": 42}, follow_redirects=False).status_code == 303
    github.repositories = ["acme/billing"]
    assert op.get("/operator/github/app/setup", params={"installation_id": 42}, follow_redirects=False).status_code == 303
    session_id = op_session(op)
    admin = _admin(conn, session_id)
    assert _changes(conn, session_id) == [
        ("source", "add", "acme/billing", admin),
        ("source", "add", "acme/shop", admin),
        ("source", "change", "acme/shop · enabled", admin),
    ]


def test_pasted_token_source_is_recorded_without_the_token(op, conn, github, secrets):
    assert op.post("/operator/github/token", data={"token": PAT, "repository_full_name": "acme/lib"},
                   follow_redirects=False).status_code == 303
    session_id = op_session(op)
    assert _changes(conn, session_id) == [("source", "add", "acme/lib", _admin(conn, session_id))]
    assert PAT not in json.dumps([dict(r) for r in repo.list_config_changes(conn, session_id)])


# --- phase 23 step 7: 저장소 카드 정리 (ARCHITECTURE "설정 UX — phase 23" 저장소 화면) --------------------------

INTERNAL_ID = re.compile(r"agt-[0-9a-f]|conn-[0-9a-f]|inv-[0-9a-f]|code\.(fix|review|triage)")
JUDGE = "agt-0a1b2c3d"


def visible_text(html: str) -> str:
    """ARCHITECTURE 노출 단정 규칙 — script·style·`<details class="detail">` 를 빼고 태그를 지운 화면 글자."""
    html = re.sub(r"<(script|style)\b.*?</\1>", " ", html, flags=re.S)
    html = re.sub(r'<details class="detail"[^>]*>.*?</details>', " ", html, flags=re.S)
    return re.sub(r"<[^>]+>", " ", html)


def block(html: str, marker: str) -> str:
    """`marker` 속성을 가진 <details> 하나(겹치지 않는다)."""
    found = re.search(rf"<details[^>]*{re.escape(marker)}[^>]*>.*?</details>", html, re.S)
    assert found is not None, marker
    return found.group(0)


def settings_form(card: str) -> str:
    found = re.search(rf'<form data-json-action="/github/sources/{SOURCE}" data-json-method="PUT">.*?</form>', card, re.S)
    assert found is not None
    return found.group(0)


def option_texts(html: str, name: str) -> list[str]:
    select = re.search(rf'<select[^>]*name="{name}".*?</select>', html, re.S)
    assert select is not None, name
    return [html_lib.unescape(t) for t in re.findall(r"<option[^>]*>(.*?)</option>", select.group(0))]


@pytest.fixture
def named(cycle_op, conn):
    """화면 이름이 ID 와 다른 에이전트 — `agt-…` 판단 에이전트(code.triage·code.fix billing, 정책 run)."""
    conn.execute("UPDATE agents SET name = '수정봇' WHERE agent_id = 'agent-fix'")
    conn.execute("UPDATE agents SET name = '검토봇' WHERE agent_id = 'agent-review'")
    conn.commit()
    repo.upsert_agent(conn, {
        "agent_id": JUDGE, "name": "판단봇", "owner_scope": "personal", "connection_type": "local",
        "capabilities": [{"code": "code.triage", "scope": {"repository_id": "billing"}},
                         {"code": "code.fix", "scope": {"repository_id": "billing"}}],
    })
    repo.register_session_agent(conn, CYCLE_SESSION, JUDGE, "2026-10-06T12:00:00Z")
    repo.set_delegation_policy(conn, CYCLE_SESSION, JUDGE, "run", member_id=None, now="2026-10-06T12:00:00Z")
    return cycle_op


def test_card_body_has_triage_fix_review_and_run_mode_and_the_rest_is_advanced(named):
    card = card_of(named.get("/repos").text, SOURCE)
    form = settings_form(card)
    advanced = block(form, "data-source-advanced")
    assert advanced.startswith('<details class="row" data-source-advanced>') and "고급 설정" in advanced
    body = form.replace(advanced, "")
    for name in ("triage_agent_id", "default_fix_agent_id", "review_agent_id", "run_mode"):
        assert f'name="{name}"' in body and f'name="{name}"' not in advanced, name
    for name in ("repository_full_name", "workflow_repository_id", "fix_verification_profile_id", "trigger_label",
                 "max_rework_rounds", "enabled"):
        assert f'name="{name}"' in advanced and f'name="{name}"' not in body, name
    assert 'name="expected_revision"' in body  # 잠금 그대로
    assert "미리보기" in body and ">저장</button>" in body  # 버튼은 고급 접힘 밖
    assert body.index('name="triage_agent_id"') < body.index('name="default_fix_agent_id"')
    assert ("새로 들어온 업무의 담당·종류를 제안하고, 결과가 규칙 밖이거나 정보가 모자라거나 사내 요청이 돌아오면 "
            "다음 단계를 제안합니다. 이 저장소를 등록했고 맡기기 정책이 '바로 실행'인 에이전트만 고를 수 있습니다.") in body


def test_card_selects_show_agent_names_only(named, conn):
    form = settings_form(card_of(named.get("/repos").text, SOURCE))
    assert option_texts(form, "triage_agent_id") == ["없음 — 판단하지 않음", "판단봇"]
    fix = option_texts(form, "default_fix_agent_id")
    assert fix[0] == "비워 두면 자동" and {"수정봇", "판단봇"} <= set(fix)
    assert not any("(" in t for t in fix)
    assert option_texts(form, "review_agent_id") == ["비워 두면 자동", "검토봇"]
    assert '<option value="agent-review" selected>검토봇</option>' in form
    assert f'<option value="{JUDGE}">판단봇</option>' in form


def test_card_match_rows_show_names_and_ids_only_in_detail(named, conn):
    repo.save_github_source(conn, CYCLE_SESSION, repo.get_github_source(conn, CYCLE_SESSION, SOURCE).model_copy(
        update={"triage_agent_id": JUDGE}), "2026-10-06T12:00:00Z")
    card = card_of(named.get("/repos").text, SOURCE)
    rows = re.search(r'<ul class="agent-list">.*?</ul>', card, re.S).group(0)
    assert "<span>판단 에이전트</span><span>판단봇 (설정)</span>" in rows
    assert "<span>검토 에이전트</span><span>검토봇 (설정)</span>" in rows
    assert '<span>로컬 저장소</span><span><span class="mono">billing</span> (설정)</span>' in rows
    detail = block(card, 'class="detail"')
    for value in (SOURCE, JUDGE, "agent-review"):
        assert value in detail, value


def test_empty_candidates_use_plain_sentences(op, conn, secrets, pem):
    connect_app(op, secrets, pem)  # 러너·에이전트 없는 저장소
    text = op.get("/repos").text
    assert "능력의 등록 Agent 없음" not in text
    source = names(conn, op_session(op))["acme/billing"]
    card = card_of(text, source.source_id)
    assert "고를 수 있는 판단 에이전트가 없습니다 — 이 저장소를 등록한 러너 에이전트의 맡기기 정책을 '바로 실행'으로 두세요" in card


def test_empty_fix_and_review_candidates_in_a_filtered_source(op, conn, app):
    app.state.settings = dataclasses.replace(app.state.settings, github_token="ghp_env", github_repos=("acme/billing",))
    text = op.get("/repos").text
    new = folded(text, "고급 — 토큰으로 연결")
    assert option_texts(new, "review_agent_id") == ["검토할 수 있는 에이전트가 없습니다 — 러너를 붙이면 생깁니다"]
    assert option_texts(new, "default_fix_agent_id") == ["없음 — 담당 연결로"]


def test_card_has_no_issue_list_and_links_to_open_work(named, conn):
    first = import_issue(conn, 1, labels=[])
    import_issue(conn, 2, labels=[])
    closed = import_issue(conn, 3, labels=[])
    conn.execute("UPDATE work_items SET status = '완료', closed_at = ? WHERE work_item_id = ?",
                 ("2026-10-06T12:00:00Z", repo.work_item_of_task(conn, closed)["work_item_id"]))
    conn.commit()
    direct_shop_task(conn)  # 다른 저장소 업무는 세지 않는다

    card = card_of(named.get("/repos").text, SOURCE)

    assert '<a data-source-work href="/tasks?repo=acme/billing">업무 2건 보기</a>' in card
    assert "실제 GitHub 이슈" not in card and "/delegate" not in card and "에이전트에게 맡기기" not in card
    assert f'href="/tasks/{first}"' not in card and "issues/1" not in card
    assert "수집 켜짐" in card and "아직 동기화 전" in card


def test_paused_source_says_so(named, conn):
    repo.save_github_source(conn, CYCLE_SESSION, repo.get_github_source(conn, CYCLE_SESSION, SOURCE).model_copy(
        update={"enabled": False}), "2026-10-06T12:00:00Z")
    card = card_of(named.get("/repos").text, SOURCE)
    assert "수집 멈춤" in card and "수집 켜짐" not in card
    assert f'data-json-action="/github/sources/{SOURCE}/stop"' not in card


def test_card_tools_hold_runner_assignees_baseline_and_stop(named):
    card = card_of(named.get("/repos").text, SOURCE)
    tools = block(card, "data-source-tools")
    assert "러너·담당 연결·기준선" in tools
    assert f'action="/operator/github/sources/{SOURCE}/runner"' in tools and "러너 다시 붙이기" in tools
    assert f'data-json-action="/github/sources/{SOURCE}/assignees"' in tools
    assert f'data-json-action="/operator/github/sources/{SOURCE}/baseline"' in tools
    assert f'data-json-action="/github/sources/{SOURCE}/stop"' in tools
    outside = card.replace(tools, "")
    for action in ("/runner", "/assignees", "/baseline", "/stop"):
        assert action not in outside, action


def test_assignee_binding_picks_users_seen_in_collected_issues(named, conn):
    import_issue(conn, 1, assignee_ids=[777], assignee_logins=["park-old"])
    import_issue(conn, 2, assignee_ids=[777, 5812345], assignee_logins=["park", "kim-dev"])
    tools = block(card_of(named.get("/repos").text, SOURCE), "data-source-tools")

    bound = re.search(r"<table>.*?</table>", tools, re.S).group(0)  # 연결된 줄 = login · 에이전트 이름
    assert "kim-dev" in bound and "수정봇" in bound
    assert "agent-fix" not in re.sub(r'<details class="detail".*?</details>', "", bound, flags=re.S)
    forms = re.findall(r'<form data-json-action="/github/sources/[^"]+/assignees"[^>]*>.*?</form>', tools, re.S)
    seen = [f for f in forms if 'type="hidden" name="github_user_id"' in f]
    assert len(seen) == 2
    assert 'value="5812345"' in seen[0] and 'name="github_login" value="kim-dev"' in seen[0]
    assert 'value="777"' in seen[1] and 'name="github_login" value="park"' in seen[1]  # 가장 최근 이슈의 login
    for form in seen:
        assert 'data-json-method="PUT"' in form and 'data-json-path-field="github_user_id"' in form
        agents = option_texts(form, "agent_id")
        assert {"수정봇", "판단봇"} <= set(agents) and not any("(" in t for t in agents)
    manual = [f for f in forms if f not in seen]
    assert len(manual) == 1 and "목록에 없는 사용자" in tools
    assert "GitHub 사용자 번호" in manual[0] and "GitHub 사용자 숫자 ID" not in tools
    assert "같은 번호를 다시 연결하면 교체합니다." in manual[0]


def test_seen_user_binding_put_is_the_same_api(named, conn):
    """화면의 숨은 값으로 보내는 PUT — 경로·`AssigneeBinding` 계약 그대로."""
    import_issue(conn, 1, assignee_ids=[777], assignee_logins=["park"])
    response = named.put(f"/github/sources/{SOURCE}/assignees/777", json={"github_login": "park", "agent_id": "agent-fix"})
    assert response.status_code == 200, response.text
    bindings = {b.github_user_id: b.agent_id for b in repo.list_assignee_bindings(conn, CYCLE_SESSION, SOURCE)}
    assert bindings[777] == "agent-fix"


def test_no_seen_users_says_so(named, conn):
    tools = block(card_of(named.get("/repos").text, SOURCE), "data-source-tools")
    assert "수집한 이슈에 GitHub 담당자가 없습니다" in tools


def test_repos_page_shows_no_internal_ids_outside_detail(named, conn, secrets, pem):
    import_issue(conn, 1)
    repo.bind_assignee(conn, CYCLE_SESSION, AssigneeBinding(source_id=SOURCE, github_user_id=42,
                                                             github_login="lee", agent_id=JUDGE), "2026-10-06T12:00:00Z")
    repo.save_github_source(conn, CYCLE_SESSION, repo.get_github_source(conn, CYCLE_SESSION, SOURCE).model_copy(
        update={"triage_agent_id": JUDGE}), "2026-10-06T12:00:00Z")
    save_app(secrets, pem)
    for client in (named,):
        text = client.get("/repos").text
        assert INTERNAL_ID.search(visible_text(text)) is None, INTERNAL_ID.search(visible_text(text))
        assert SLUG not in visible_text(text) and SOURCE not in visible_text(text)
        assert "<table>" not in re.sub(r'<div class="table-wrap">\s*<table>', "", text)  # 표는 가로 스크롤 안


def test_member_sees_names_as_text_without_forms(named, app, conn):
    repo.save_github_source(conn, CYCLE_SESSION, repo.get_github_source(conn, CYCLE_SESSION, SOURCE).model_copy(
        update={"triage_agent_id": JUDGE}), "2026-10-06T12:00:00Z")
    member = log_in_member(TestClient(app))
    text = member.get("/repos").text
    card = card_of(text, SOURCE)
    assert 'data-json-method="PUT"' not in card and "/baseline" not in card
    summary = re.search(r"<p[^>]*data-source-summary[^>]*>.*?</p>", card, re.S).group(0)
    for value in ("판단 에이전트 판단봇", "검토 에이전트 검토봇", "자동 실행"):
        assert value in summary, value
    assert INTERNAL_ID.search(visible_text(text)) is None


def test_page_order_is_connection_jira_cards_then_token(named, conn):
    text = named.get("/repos").text
    order = [text.index(marker) for marker in ("저장소 1개", "data-jira", "data-source-card", "고급 — 토큰으로 연결")]
    assert order == sorted(order)
