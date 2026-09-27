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
from workflow.contracts.github import IssuePrLink
from workflow.server import web
from workflow.server.app import create_app
from workflow.server.auth import SESSION_COOKIE, sign_session

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
    """demo 모드 운영자 세션(127.0.0.1 요청 주소)."""
    client = TestClient(app, base_url=BASE)
    assert client.post("/operator/login", data={"token": "test-operator-token"}, follow_redirects=False).status_code == 303
    return client


def op_session(client: TestClient) -> str:
    return client.cookies[SESSION_COOKIE].rpartition(".")[0]


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
    anonymous = TestClient(app, base_url=BASE)
    for url in ("/operator/github/app/new", f"/operator/github/app/callback?code={CODE}&state=x",
                "/operator/github/app/setup?installation_id=42"):
        error(anonymous.get(url, follow_redirects=False), 403, "forbidden")
    response = anonymous.post("/operator/github/token", data={"token": PAT, "repository_full_name": "acme/lib"},
                              follow_redirects=False)
    assert response.status_code == 403
    assert github.calls == []
    assert repo.github_source_sessions(conn) == []


def test_selfhost_without_login_redirects_to_login(settings, github):
    app = create_app(dataclasses.replace(settings, mode="selfhost"))
    app.state.github_transport = httpx.MockTransport(github)
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
    client = TestClient(app)  # 요청 주소 testserver 는 쓰지 않는다
    client.post("/operator/login", data={"token": "test-operator-token"})
    _, manifest = start(client)
    assert manifest["redirect_url"] == "https://runloom.example/operator/github/app/callback"


def test_non_loopback_request_host_needs_public_url(app):
    client = TestClient(app)  # http://testserver
    client.post("/operator/login", data={"token": "test-operator-token"})
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
    fresh.cookies.set(SESSION_COOKIE, op.cookies[SESSION_COOKIE])  # 같은 운영자, state 쿠키 없음
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

    assert response.status_code == 303 and response.headers["location"] == "/operator/github"
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


def test_setup_when_another_workspace_owns_github(op, app, conn, secrets, pem):
    save_app(secrets, pem)
    other = TestClient(app, base_url=BASE)
    other.post("/operator/login", data={"token": "test-operator-token"})
    assert other.get("/operator/github/app/setup", params={"installation_id": 42},
                     follow_redirects=False).status_code == 303
    error(op.get("/operator/github/app/setup", params={"installation_id": 42}), 409, "github_workspace_taken")


# --- PAT (고급) -----------------------------------------------------------------------------------


def test_pasted_token_is_checked_saved_and_makes_a_source(op, conn, github, secrets):
    response = op.post("/operator/github/token", data={"token": f"  {PAT} ", "repository_full_name": "acme/lib"},
                       follow_redirects=False)

    assert response.status_code == 303 and response.headers["location"] == "/operator/github"
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
    keep(op.get("/operator/github"))

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
    """이 클라이언트를 `sess-cycle` 운영자 세션으로 — all_open 소스(담당 연결 FIX)의 주인."""
    repo.save_github_source(conn, CYCLE_SESSION, config(review_agent_id="agent-review", intake="all_open",
                                                         label_filter=[], trigger_label="runloom"), "2026-10-06T12:00:00Z")
    repo.mark_operator(conn, CYCLE_SESSION)
    client.cookies.set(SESSION_COOKIE, sign_session(CYCLE_SESSION, "test-session-secret"))
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


def test_delegate_needs_the_owning_operator(cycle_op, app, conn):
    task_id = import_issue(conn, 1, labels=[])
    stranger = TestClient(app)
    stranger.get("/tasks")
    assert stranger.post(f"/tasks/{task_id}/delegate").status_code == 404
    conn.execute("UPDATE sessions SET is_operator = 0 WHERE session_id = ?", (CYCLE_SESSION,))
    conn.commit()
    error(cycle_op.post(f"/tasks/{task_id}/delegate"), 403, "forbidden")
    assert delegated(conn, task_id) == (None, None)


# --- 연결 화면 (phase 11 step 8) ------------------------------------------------------------------------
# 기본 화면(접힌 <details> 밖)에는 내부 ID·토큰 입력 칸이 없다. 저장소 카드는 `data-source-card`, 고급 설정은 카드 안에 접혀 있다.

ID_FIELDS = ("workflow_repository_id", "fix_verification_profile_id", "review_agent_id", "default_fix_agent_id",
             "github_user_id", "start_at", "token")


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
    text = op.get("/operator/github").text

    shown = visible(text)
    assert 'href="/operator/github/app/new"' in shown and "GitHub 연결" in shown
    for name in ID_FIELDS:
        assert f'name="{name}"' not in shown, name
    token = folded(text, "고급 — 토큰으로 연결")
    assert 'action="/operator/github/token"' in token and 'type="password"' in token
    assert "저장소 추가/변경" not in text and "data-source-card" not in text


def test_page_after_app_setup_shows_a_card_per_repository(op, conn, secrets, pem):
    connect_app(op, secrets, pem)

    text = op.get("/operator/github").text

    shown = visible(text)
    for name in ID_FIELDS:
        assert f'name="{name}"' not in shown, name
    assert f'href="https://github.com/apps/{SLUG}/installations/new"' in shown and "저장소 추가/변경" in shown
    assert f"GitHub App <span class=\"mono\">{SLUG}</span>" in shown and "kim-dev" in shown
    sources = names(conn, op_session(op))
    assert sorted(sources) == ["acme/billing", "acme/shop"]
    for full_name, source in sources.items():
        card = card_of(text, source.source_id)
        assert full_name in card
        assert "runloom" in card  # 트리거 라벨
        assert "가져온 이슈 0건" in card and "아직 동기화 전" in card
        assert "GitHub App 설치" in card  # 수집 자격 — 종류만
        assert "이 저장소를 등록한 러너 없음" in card and "러너 붙이기" in card
        assert f'data-json-action="/operator/github/sources/{source.source_id}/baseline"' in card
        advanced = folded(card, "고급 설정")
        assert 'name="workflow_repository_id"' in advanced and "비워 두면 자동" in advanced
        assert 'name="intake" value="all_open"' in advanced
        assert f'data-json-action="/github/sources/{source.source_id}/assignees"' in advanced


def test_card_explains_the_baseline_before_it_is_imported(op, conn, secrets, pem):
    connect_app(op, secrets, pem)
    text = op.get("/operator/github").text
    for source in names(conn, op_session(op)).values():
        card = card_of(visible(text), source.source_id)
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

    card = card_of(visible(op.get("/operator/github").text), source.source_id)

    assert "기준선 2건" in card and "이슈 열림 → 병합 중앙값 4시간 0분" in card
    assert "2026-09-27 20:20" in card  # 가져온 시각(KST)
    assert 'href="/metrics"' in card


def test_card_title_hides_internal_ids(op, conn, secrets, pem):
    connect_app(op, secrets, pem)
    text = op.get("/operator/github").text
    for source in names(conn, op_session(op)).values():
        shown = re.sub(r"<[^>]+>", " ", card_of(visible(text), source.source_id))  # 보이는 글자만(요청 주소 속성 제외)
        assert source.source_id not in shown and "revision" not in shown


def test_card_shows_the_automatically_matched_agents_and_profile(client, auto_source, conn):
    repo.mark_operator(conn, CYCLE_SESSION)
    client.cookies.set(SESSION_COOKIE, sign_session(CYCLE_SESSION, "test-session-secret"))

    card = card_of(client.get("/operator/github").text, SOURCE)

    for value in ("billing", "agent-fix", "agent-review", "vp-pytest"):
        assert f'<span class="mono">{value}</span> (자동)' in card, value
    assert "러너 없음" not in card


def test_card_says_when_no_credential_can_collect(op, conn, app):
    op.post("/operator/github/token", data={"token": PAT, "repository_full_name": "acme/lib"})
    (source,) = names(conn, op_session(op)).values()
    app.state.secrets.delete(secret_store.GITHUB_TOKEN)

    card = card_of(op.get("/operator/github").text, source.source_id)
    assert "GitHub 자격 없음" in card


def test_secret_status_is_only_connected_or_missing(op, conn, secrets, pem):
    connect_app(op, secrets, pem)
    op.post("/operator/github/token", data={"token": PAT, "repository_full_name": "acme/lib"})

    text = op.get("/operator/github").text

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

    for url in ("/tasks", "/operator/github"):
        text = cycle_op.get(url).text
        assert delegate_form(waiting) in text and "에이전트에게 맡기기" in text, url
        assert delegate_form(labelled) not in text and delegate_form(closed) not in text, url

    detail = cycle_op.get(f"/tasks/{waiting}").text
    assert delegate_form(waiting) in detail
    assert 'data-blocker="not_delegated"' in detail and "실행 지시 전" in detail
    assert delegate_form(labelled) not in cycle_op.get(f"/tasks/{labelled}").text


def test_list_groups_undelegated_tasks_as_waiting_for_an_instruction(cycle_op, conn):
    waiting = import_issue(conn, 1, labels=[], assignee_ids=[], assignee_logins=[])
    conn.execute("UPDATE tasks SET status = ?, status_reason = ? WHERE task_id = ?",
                 ("대기", "실행 지시 전 — [에이전트에게 맡기기] 또는 `runloom` 라벨 · 다른 사유", waiting))
    conn.commit()

    home = cycle_op.get("/tasks").text
    card = home.split(f'href="/tasks/{waiting}"', 2)[2].split("</div>\n  </div>", 1)[0]
    assert "지시 전" in card and "다른 사유" not in card
    assert "다른 사유" in cycle_op.get(f"/tasks/{waiting}").text  # 다른 사유는 상세에서


def test_no_delegate_button_for_filtered_sources_or_non_operators(client, cycle, conn):
    task_id = import_issue(conn, 1)  # filtered 소스 — 수집 = 지시
    repo.mark_operator(conn, CYCLE_SESSION)
    client.cookies.set(SESSION_COOKIE, sign_session(CYCLE_SESSION, "test-session-secret"))
    assert "/delegate" not in client.get("/tasks").text
    assert "/delegate" not in client.get(f"/tasks/{task_id}").text


def test_detail_hides_the_delegate_button_from_a_non_operator(cycle_op, conn):
    task_id = import_issue(conn, 1, labels=[])
    conn.execute("UPDATE sessions SET is_operator = 0 WHERE session_id = ?", (CYCLE_SESSION,))
    conn.commit()
    assert delegate_form(task_id) not in cycle_op.get(f"/tasks/{task_id}").text
    assert delegate_form(task_id) not in cycle_op.get("/tasks").text


def test_demo_home_without_github_is_unchanged(client):
    text = client.get("/tasks").text
    assert "/delegate" not in text and "지시 전" not in text
