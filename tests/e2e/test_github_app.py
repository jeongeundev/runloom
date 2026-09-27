"""GitHub App 연결 e2e — [GitHub 연결] 버튼부터 라벨 자동 착수까지 가짜 GitHub 로 돌린다 (phase 11 step 9, ADR-0017).

`test_github_cycle` 의 가짜 GitHub·임시 Git 저장소·가짜 codex·도우미를 다시 쓰고, 이 모듈이 더하는 것:
- **가짜 GitHub App** — `POST /app-manifests/{code}/conversions`(테스트 안에서 만든 RSA 키를 pem 으로 준다),
  `GET /app/installations/{id}`·`POST /app/installations/{id}/access_tokens`(App JWT 를 공개 키로 검증),
  `GET /installation/repositories`(설치 토큰). 저장소·이슈·댓글은 `FakeGitHub` 그대로 — 설치 토큰만 받는다.
- **GitHub 쪽 화면은 테스트가 대신한다** — 사용자가 GitHub 에서 [Create]·[Install] 을 누른 뒤 브라우저가 돌아오는
  callback·setup 을 테스트가 직접 부른다(state 는 central 이 준 값 그대로).
- **중앙 API 는 이 프로세스 안의 uvicorn 스레드** — 연결 경로의 `app.state.github_transport` 를 가짜 GitHub 로 넘기기 위해서다.
  워커는 `SourceClients`(설치 토큰)로 수집·반영한다. 환경변수 토큰·`WORKFLOW_GITHUB_REPOS` 는 없다.

흐름: 운영자 로그인 → [GitHub 연결] → callback → setup → 소스 자동 생성(`all_open`) → 수집: 열린 이슈 전부 "지시 전" 대기 →
러너 register(origin 이 github.com/acme/billing) → 자동 매칭 → #1 [에이전트에게 맡기기] → 수정 → 검토 승인 → 운영자 승인(완료)
→ #2 에 `runloom` 라벨 → 자동 착수·검토 승인. 비밀 파일 권한 0600/0700, DB·로그·화면·도구 환경에 비밀 없음.

`WORKFLOW_E2E=1` 일 때만 돈다. 실제 GitHub·모델 호출 없음 — 네트워크는 127.0.0.1 뿐이다.
"""

import json
import logging
import os
import re
import stat
import sys
import threading
import time
from html import unescape
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
import jwt
import pytest
import uvicorn
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from tests.e2e.test_github_cycle import (
    _FAKE_CODEX,
    BILLING_FILES,
    DROP_PREFIXES,
    FIXES,
    REPO_IDS,
    FakeGitHub,
    ToFakeGitHub,
    World,
    _free_port,
    _git,
    drive,
    execs,
    gh_issue,
    make_repo,
    operator_agent,
    q,
    reviews_of,
    serve,
    task,
)
from workflow.adapters import secret_store
from workflow.adapters.callback_client import HttpCallbackClient
from workflow.adapters.db import connect
from workflow.adapters.diag_client import HttpDiagClient
from workflow.adapters.secret_store import SecretStore
from workflow.server import worker as worker_module
from workflow.server.app import create_app
from workflow.server.auth import utc_now
from workflow.server.github_clients import SourceClients
from workflow.server.settings import load_settings
from workflow.server.worker import Worker

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(os.environ.get("WORKFLOW_E2E") != "1", reason="WORKFLOW_E2E=1 일 때만"),
]

REPO = "acme/billing"
INSTALLATION_ID = 7701
MANIFEST_CODE = "e2e-manifest-code-1"
CLIENT_ID = "Iv1.e2eclient0001"
SLUG = "runloom-e2eapp"
CLIENT_SECRET = "e2e-client-secret-" + "c" * 24
WEBHOOK_SECRET = "e2e-webhook-secret-" + "w" * 24
INSTALL_TOKEN = "ghs_e2eInstallToken" + "Q7" * 12
FIX, REVIEW = "agent-fix-billing", "agent-review-billing"


def _rsa_pem() -> tuple[str, object]:
    """테스트 안에서만 만드는 App 개인 키 — 저장소에 키 파일을 두지 않는다."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption()).decode()
    return pem, key.public_key()


PEM, PUBLIC_KEY = _rsa_pem()
PEM_BODY = PEM.splitlines()[5]  # 키 본문 한 줄 — 파일·로그 검사에 쓴다


class FakeGitHubApp(FakeGitHub):
    """App 경로만 더한다. 저장소 경로(`/repos/…`)는 `FakeGitHub` 가 설치 토큰(`self.token`)으로만 받는다."""

    def __init__(self):
        super().__init__(INSTALL_TOKEN)
        self.installed = [REPO]
        self.app_requests: list[tuple[str, str, str]] = []  # (method, path, 인증 종류 jwt|token|none|bad)

    def _jwt_ok(self, headers) -> bool:
        value = headers.get("Authorization") or ""
        if not value.startswith("Bearer "):
            return False
        try:
            claims = jwt.decode(value[7:], PUBLIC_KEY, algorithms=["RS256"])
        except jwt.PyJWTError:
            return False
        return claims.get("iss") == CLIENT_ID

    def handle(self, method, path, query, headers, body):
        if not path.startswith(("/app-manifests/", "/app/", "/installation/")):
            return super().handle(method, path, query, headers, body)
        with self.lock:
            if method == "POST" and path == f"/app-manifests/{MANIFEST_CODE}/conversions":
                self.app_requests.append((method, path, "none"))
                return 201, {}, {
                    "id": 424242, "client_id": CLIENT_ID, "slug": SLUG, "name": SLUG,
                    "owner": {"login": "acme"}, "html_url": f"https://github.com/apps/{SLUG}",
                    "client_secret": CLIENT_SECRET, "webhook_secret": WEBHOOK_SECRET, "pem": PEM,
                }
            if path.startswith("/app-manifests/"):
                self.app_requests.append((method, path, "none"))
                return 404, {}, {"message": "Not Found"}
            if path.startswith("/app/"):
                kind = "jwt" if self._jwt_ok(headers) else "bad"
                self.app_requests.append((method, path, kind))
                if kind == "bad":
                    return 401, {}, {"message": "A JSON web token could not be decoded"}
                if method == "GET" and path == f"/app/installations/{INSTALLATION_ID}":
                    return 200, {}, {"id": INSTALLATION_ID, "account": {"login": "acme"},
                                     "repository_selection": "selected"}
                if method == "POST" and path == f"/app/installations/{INSTALLATION_ID}/access_tokens":
                    return 201, {}, {"token": INSTALL_TOKEN, "expires_at": "2999-01-01T00:00:00Z"}
                return 404, {}, {"message": "Not Found"}
            authorized = headers.get("Authorization") == f"Bearer {INSTALL_TOKEN}"
            self.app_requests.append((method, path, "token" if authorized else "bad"))
            if not authorized:
                return 401, {}, {"message": "Bad credentials"}
            if method == "GET" and path == "/installation/repositories":
                repos = [{"id": REPO_IDS[name], "full_name": name} for name in self.installed]
                return 200, {}, {"total_count": len(repos), "repositories": repos}
            return 404, {}, {"message": "Not Found"}


def install_fake_codex(bin_dir: Path) -> None:
    """`test_github_cycle` 의 가짜 codex — 도구 환경에 설치 토큰이 있으면 실패하게 바꿔 끼운다."""
    bin_dir.mkdir(parents=True)
    script = bin_dir / "codex"
    text = _FAKE_CODEX.replace("__TOKEN__", INSTALL_TOKEN).replace("__FIXES__", repr(FIXES))
    script.write_text(f"#!{sys.executable}\n" + text.split("\n", 1)[1])
    script.chmod(0o755)


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    workdir = tmp_path_factory.mktemp("github-app")
    (workdir / "logs").mkdir()
    billing = workdir / "repos" / "billing"
    base = {"billing": make_repo(billing, BILLING_FILES)}
    _git(billing, "remote", "add", "origin", "git@github.com:acme/billing.git")  # 러너가 보고할 owner/name

    fake = FakeGitHubApp()
    fake.add(REPO, gh_issue(REPO, 1, "청구서 번호 자릿수", labels=(), updated_at="2026-09-10T01:00:00Z",
                            body="번호가 5자리로 채워지지 않습니다. [scenario:invoice]"))
    fake.add(REPO, gh_issue(REPO, 2, "환불 수수료 계산 오류", labels=("bug",), updated_at="2026-09-10T02:00:00Z",
                            body="수수료는 3% 입니다. [scenario:refund]"))
    # 연결 전에 만든 오래된 열린 이슈도 받는다(all_open) · PR·닫힌 이슈는 받지 않는다
    fake.add(REPO, gh_issue(REPO, 3, "오래된 백로그", labels=(), created_at="2025-01-01T00:00:00Z",
                            updated_at="2025-01-02T00:00:00Z", body="[scenario:coupon]"))
    fake.add(REPO, gh_issue(REPO, 4, "fix: PR", updated_at="2026-09-10T04:00:00Z", pull=True))
    fake.add(REPO, gh_issue(REPO, 5, "이미 닫힘", state="closed", updated_at="2026-09-10T05:00:00Z"))
    github_server = serve(fake)
    fake_port = github_server.server_address[1]

    port = _free_port()
    inherited = {k: v for k, v in os.environ.items() if not k.startswith(DROP_PREFIXES)}
    central_env = {
        **inherited,
        "WORKFLOW_DB_PATH": str(workdir / "central" / "db.sqlite"),
        "WORKFLOW_ARTIFACT_DIR": str(workdir / "central" / "artifacts"),
        "WORKFLOW_SECRET_DIR": str(workdir / "central" / "secrets"),
        "SESSION_SECRET": "e2e-session-secret-" + "s" * 20,
        "OPERATOR_TOKEN": "e2e-operator-token-" + "o" * 20,
        "DIAG_API_TOKEN": "e2e-diag-token-" + "d" * 20,
        "DIAG_API_URL": "http://127.0.0.1:9",
        "WORKFLOW_PUBLIC_URL": f"http://127.0.0.1:{port}",
    }
    fake_bin = workdir / "bin"
    install_fake_codex(fake_bin)
    connector_env = {
        **inherited,
        "WORKFLOW_CONNECTOR_HOME": str(workdir / "connector"),
        "PATH": f"{fake_bin}{os.pathsep}{inherited.get('PATH', '')}",
    }
    world = World(workdir=workdir, central_url=f"http://127.0.0.1:{port}", central_env=central_env,
                  connector_env=connector_env, fake=fake, fake_port=fake_port, billing=billing, shop=None, base=base)

    settings = load_settings(central_env)
    app = create_app(settings)
    app.state.github_transport = ToFakeGitHub(fake_port)
    api = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="info"))
    api_thread = threading.Thread(target=api.run, daemon=True)

    # 이 프로세스(중앙 API·워커)의 로그를 파일로 — 비밀이 로그에 없는지 본다
    log_handler = logging.FileHandler(workdir / "logs" / "central.log", encoding="utf-8")
    log_handler.setFormatter(logging.Formatter("%(name)s %(levelname)s %(message)s"))
    root = logging.getLogger()
    old_level = root.level
    root.addHandler(log_handler)
    root.setLevel(logging.INFO)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(worker_module, "GITHUB_SYNC_INTERVAL_SECONDS", 0)
        try:
            api_thread.start()
            deadline = time.monotonic() + 30
            while not api.started:
                assert time.monotonic() < deadline and api_thread.is_alive(), "중앙 API 가 뜨지 않음"
                time.sleep(0.1)
            world.http = httpx.Client(base_url=world.central_url, follow_redirects=False, timeout=10.0)
            yield world
        finally:
            if world.http is not None:
                world.http.close()
            world.stop()
            api.should_exit = True
            api_thread.join(10)
            github_server.shutdown()
            root.removeHandler(log_handler)
            root.setLevel(old_level)
            log_handler.close()


def make_worker(world: World) -> Worker:
    """워커 프로세스 한 번의 시작과 같다 — 소스별 클라이언트(설치 토큰)는 비밀 저장소에서 읽는다."""
    settings = load_settings(world.central_env)
    diag = HttpDiagClient(settings.diag_api_url, settings.diag_api_token,
                          transport=httpx.MockTransport(lambda request: httpx.Response(503)))
    clients = SourceClients(settings, SecretStore(settings.secret_dir), transport=ToFakeGitHub(world.fake_port))
    return Worker(lambda: connect(settings.db_path), world.store, diag, HttpCallbackClient(), settings, utc_now,
                  github_for=clients)


def secret_dir(world: World) -> Path:
    return Path(world.central_env["WORKFLOW_SECRET_DIR"])


def source_card(world: World) -> str:
    page = world.http.get("/operator/github")
    assert page.status_code == 200, page.text[:500]
    source_id = world.sources[REPO]
    return page.text.split(f'data-source-card="{source_id}"', 1)[1].split("</section>", 1)[0]


def issue_task(world: World, number: int) -> str:
    return q(world, "SELECT task_id FROM source_issues WHERE issue_number = ?", number)[0]["task_id"]


def open_blockers(world: World, task_id: str) -> str:
    return task(world, task_id)["status_reason"] or ""


# --- 시나리오 ------------------------------------------------------------------------------


def test_01_operator_clicks_connect_creates_the_app_and_installs_it(world):
    http = world.http
    assert http.get("/tasks").status_code == 200  # 세션 쿠키
    login = http.post("/operator/login", data={"token": world.central_env["OPERATOR_TOKEN"]})
    assert login.status_code == 303, login.text[:300]
    world.session_id = q(world, "SELECT session_id FROM sessions WHERE is_operator = 1")[0]["session_id"]

    # 연결 전 화면 — 버튼 하나, 기본 화면에 ID·토큰 칸 없음
    before = http.get("/operator/github")
    assert before.status_code == 200 and 'href="/operator/github/app/new"' in before.text
    assert "data-source-card" not in before.text

    # [GitHub 연결] — manifest 자동 제출 폼. GitHub 로 가는 주소와 돌아올 주소가 central 공개 주소다
    new = http.get("/operator/github/app/new")
    assert new.status_code == 200, new.text[:500]
    action = unescape(re.search(r'<form id="gh-manifest-form" method="post" action="([^"]+)"', new.text).group(1))
    assert action.startswith("https://github.com/settings/apps/new?state=")
    state1 = parse_qs(urlsplit(action).query)["state"][0]
    manifest = json.loads(unescape(re.search(r'name="manifest" value="([^"]+)"', new.text).group(1)))
    assert manifest["redirect_url"] == f"{world.central_url}/operator/github/app/callback"
    assert manifest["setup_url"] == f"{world.central_url}/operator/github/app/setup"
    assert manifest["hook_attributes"]["active"] is False and manifest["public"] is False
    assert manifest["default_permissions"] == {"issues": "write", "pull_requests": "read", "metadata": "read"}

    # (GitHub 에서 사용자가 [Create]) → callback — code 교환·비밀 저장 → 설치 화면으로
    callback = http.get("/operator/github/app/callback", params={"code": MANIFEST_CODE, "state": state1})
    assert callback.status_code == 303, callback.text[:500]
    install_url = callback.headers["location"]
    assert install_url.startswith(f"https://github.com/apps/{SLUG}/installations/new?state=")
    state2 = parse_qs(urlsplit(install_url).query)["state"][0]
    assert state2 != state1
    for body in (callback.text, new.text):
        assert MANIFEST_CODE not in body and CLIENT_SECRET not in body and PEM_BODY not in body
    replay = http.get("/operator/github/app/callback", params={"code": MANIFEST_CODE, "state": state1})
    assert replay.status_code == 403  # 한 번 쓴 state 는 교체됐다

    # (GitHub 에서 사용자가 저장소 고르고 [Install]) → setup — JWT 로 설치 확인 → 저장소마다 소스
    setup = http.get("/operator/github/app/setup", params={
        "installation_id": INSTALLATION_ID, "setup_action": "install", "state": state2})
    assert setup.status_code == 303 and setup.headers["location"] == "/operator/github", setup.text[:500]
    kinds = {(m, p): kind for m, p, kind in world.fake.app_requests}
    assert kinds[("GET", f"/app/installations/{INSTALLATION_ID}")] == "jwt"
    assert kinds[("GET", "/installation/repositories")] == "token"

    response = http.get("/github/sources")
    assert response.status_code == 200, response.text
    (source,) = response.json()["sources"]
    assert (source["repository_full_name"], source["intake"], source["trigger_label"], source["installation_id"],
            source["run_mode"]) == (REPO, "all_open", "runloom", INSTALLATION_ID, "auto")
    assert (source["workflow_repository_id"], source["review_agent_id"], source["fix_verification_profile_id"]) == (
        None, None, None)  # 내부 ID 입력 없음 — 자동 매칭
    world.sources[REPO] = source["source_id"]

    # 비밀 파일 — 디렉터리 0700·파일 0600, PAT 없음. DB 에는 비밀이 없다(아래 test_06 이 전체를 훑는다)
    root = secret_dir(world)
    assert stat.S_IMODE(root.stat().st_mode) == 0o700
    names = {p.name for p in root.iterdir()}
    assert names == {secret_store.GITHUB_APP_INFO, secret_store.GITHUB_APP_PRIVATE_KEY,
                     secret_store.GITHUB_APP_CLIENT_SECRET, secret_store.GITHUB_APP_WEBHOOK_SECRET}
    assert all(stat.S_IMODE((root / name).stat().st_mode) == 0o600 for name in names)
    assert (root / secret_store.GITHUB_APP_PRIVATE_KEY).read_text() == PEM

    card = source_card(world)
    assert "App 설치" in card and "data-runner-missing" in card  # 러너 아직 없음


def test_02_first_sync_imports_every_open_issue_as_waiting_for_delegation(world):
    world.worker = make_worker(world)
    report = world.worker.tick()
    assert report.sync_errors == 0 and report.issues_created == 3
    assert report.tasks_started == 0
    rows = q(world, "SELECT issue_number, delegated_at FROM source_issues ORDER BY issue_number")
    assert [(r["issue_number"], r["delegated_at"]) for r in rows] == [(1, None), (2, None), (3, None)]  # PR·닫힘 제외
    for number in (1, 2, 3):
        task_id = issue_task(world, number)
        assert execs(world, task_id) == []
        assert "실행 지시 전" in open_blockers(world, task_id)
    assert q(world, "SELECT COUNT(*) FROM human_requests")[0][0] == 0  # 지시·매칭 대기는 사람 요청이 아니다

    # 수집은 설치 토큰으로만 — 저장소 경로 요청이 모두 설치 토큰이었다
    assert world.fake.requests and all(authorized for _, _, authorized in world.fake.requests)

    listing = world.http.get("/tasks")
    assert listing.status_code == 200 and "지시 전" in listing.text
    assert listing.text.count("에이전트에게 맡기기") >= 3


def test_03_runner_registers_the_folder_and_matching_fills_everything(world):
    http = world.http
    for agent_id, code, registration in ((FIX, "code.fix", "local-billing-fix"),
                                         (REVIEW, "code.review", "local-billing-review")):
        operator_agent(world, agent_id, code, "billing", registration)
        assert http.post("/agents/register", data={"agent_id": agent_id}).status_code == 303
    issued = http.post("/operator/connect-codes")
    connect_code = re.search(r'<code id="issued-code">([^<]+)</code>', issued.text).group(1)
    py = sys.executable
    world.once("connector-connect", [py, "-m", "workflow.connector", "connect", "--server", world.central_url,
                                     "--code", connect_code])
    for agent_id, registration, verify in ((FIX, "local-billing-fix", True), (REVIEW, "local-billing-review", False)):
        argv = [py, "-m", "workflow.connector", "register", "--id", registration, "--tool", "codex",
                "--repo", str(world.billing), "--repository-id", "billing"]
        if verify:
            argv += ["--verify", f"vp-pytest={py} -m pytest -q -p no:cacheprovider"]
        assert f"agent {agent_id}" in world.once(f"connector-register-{registration}", argv)
    discovered = json.loads(q(world, "SELECT discovered_json FROM agents WHERE agent_id = ?", FIX)[0][0])
    assert discovered["found"]["github_repository"] == REPO  # 원격 URL 원문은 없다
    assert "git@github.com" not in json.dumps(discovered)

    world.spawn("connector", [py, "-m", "workflow.connector", "run", "--adapter", "codex",
                              "--claim-interval", "0.5", "--heartbeat-interval", "1"], world.connector_env)
    deadline = time.monotonic() + 30
    while not q(world, "SELECT 1 FROM connectors WHERE supported_kinds_json LIKE '%bug_fix%'"):
        assert time.monotonic() < deadline, world.log_tails()
        time.sleep(0.3)

    card = source_card(world)
    assert "data-runner-missing" not in card
    for value in ("billing", FIX, "vp-pytest", REVIEW):
        assert f'<span class="mono">{value}</span> (자동)' in card, card

    # 매칭이 풀려도 지시 전이면 착수하지 않는다
    for _ in range(2):
        world.worker.tick()
    assert all(execs(world, issue_task(world, n)) == [] for n in (1, 2, 3))


def test_04_delegate_button_runs_fix_then_review_then_operator_approves(world):
    a_id = issue_task(world, 1)
    response = world.http.post(f"/tasks/{a_id}/delegate")
    assert response.status_code == 303 and response.headers["location"] == f"/tasks/{a_id}", response.text[:500]
    (row,) = q(world, "SELECT delegated_by FROM source_issues WHERE issue_number = 1")
    assert row["delegated_by"] == "operator"
    again = world.http.post(f"/tasks/{a_id}/delegate")  # 멱등 — 실행이 늘지 않는다
    assert again.status_code == 303

    drive(world, lambda: reviews_of(world, a_id) and task(world, reviews_of(world, a_id)[0]["task_id"])["finished_at"],
          "#1 검토 승인")
    (fix,) = execs(world, a_id)
    (review,) = reviews_of(world, a_id)
    (review_run,) = execs(world, review["task_id"])
    assert (fix["agent_id"], review_run["agent_id"]) == (FIX, REVIEW)
    assert fix["start_key"] == f"auto:{a_id}:r{task(world, a_id)['revision']}"  # [맡기기] 도 자동 착수와 같은 키
    target = json.loads(fix["request_json"])["target"]
    assert (target["local_registration_id"], target["verification_profile_id"]) == ("local-billing-fix", "vp-pytest")
    assert task(world, review["task_id"])["status"] == "완료"
    assert task(world, a_id)["status_reason"] == "검토 승인 — 병합·이슈 종료는 사람"

    response = world.http.post(f"/tasks/{a_id}/review", data={"decision": "approve", "comment": ""})
    assert response.status_code == 303, response.text[:500]
    assert task(world, a_id)["status"] == "완료"

    # 다른 이슈는 여전히 지시 전
    assert execs(world, issue_task(world, 2)) == [] and execs(world, issue_task(world, 3)) == []
    (comment,) = world.fake.issue_comments(REPO, 1)
    assert comment["body"].splitlines()[0] == f"<!-- runloom:task={a_id} -->"


def test_05_trigger_label_starts_another_issue_without_the_button(world):
    b_id = issue_task(world, 2)
    world.fake.add(REPO, gh_issue(REPO, 2, "환불 수수료 계산 오류", labels=("bug", "Runloom"),
                                  updated_at="2026-09-11T00:00:00Z", body="수수료는 3% 입니다. [scenario:refund]"))
    drive(world, lambda: execs(world, b_id), "#2 라벨 자동 착수")
    (row,) = q(world, "SELECT delegated_by FROM source_issues WHERE issue_number = 2")
    assert row["delegated_by"] == "label"
    drive(world, lambda: reviews_of(world, b_id) and task(world, reviews_of(world, b_id)[0]["task_id"])["finished_at"],
          "#2 검토 승인")
    assert task(world, b_id)["status_reason"] == "검토 승인 — 병합·이슈 종료는 사람"

    # 라벨도 버튼도 없는 오래된 이슈는 끝까지 기다린다
    for _ in range(2):
        world.worker.tick()
    c_id = issue_task(world, 3)
    assert execs(world, c_id) == [] and "실행 지시 전" in open_blockers(world, c_id)
    assert _git(world.billing, "rev-parse", "main") == world.base["billing"]  # 자동 병합·푸시 없음


def test_06_secrets_stay_in_the_secret_files(world):
    """App 개인 키·client secret·webhook secret·설치 토큰은 비밀 파일(설치 토큰은 메모리)에만 — DB·산출물·로그·연결
    프로그램 상태·저장소·댓글·화면에 없다(도구 환경은 가짜 codex 가 설치 토큰을 검사)."""
    needles = [PEM_BODY, CLIENT_SECRET, WEBHOOK_SECRET, INSTALL_TOKEN]
    root = secret_dir(world)
    leaked = []
    for path in world.workdir.rglob("*"):
        if not path.is_file() or root in path.parents or path.name == "codex":
            continue
        data = path.read_bytes()
        leaked += [(str(path), n[:12]) for n in needles if n.encode() in data]
    assert leaked == []
    assert (world.workdir / "logs" / "central.log").stat().st_size > 0  # 로그를 실제로 남겼다
    assert all(n not in c["body"] for c in world.fake.comments.values() for n in needles)
    for path in ("/operator/github", "/tasks", "/github/sources"):
        body = world.http.get(path).text
        assert all(n not in body for n in needles), path
    assert q(world, "SELECT COUNT(*) FROM executions WHERE failed_code IS NOT NULL")[0][0] == 0
