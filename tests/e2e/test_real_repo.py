"""실제 저장소 순환 e2e — [러너 붙이기]부터 초안 PR 병합·알림까지 가짜 GitHub·로컬 bare 저장소로 돌린다 (phase 12 step 10, ADR-0018).

`test_github_app` 의 가짜 GitHub App·도우미를 다시 쓰고, 이 모듈이 더하는 것:
- **origin = 임시 bare 저장소** — "원본 폴더"(러너가 등록하는 사용자 클론)의 `remote.origin.url` 은
  `git@github.com:acme/billing.git`(러너가 owner/name 을 읽는다)이고, 저장소 로컬 설정 `url.<bare>.insteadOf` 가
  fetch·push 를 bare 로 보낸다. 원본 폴더에만 있는 `deps/`(git 무시)는 `--link deps` 로 worktree 에 걸린다.
- **가짜 GitHub 의 PR 경로** — `GET /repos/{r}`(default_branch), `GET·POST /repos/{r}/pulls`, `GET /repos/{r}/pulls/{n}`.
  PR 은 head 브랜치가 bare 에 있을 때만 만든다(실제 GitHub 처럼 422). 병합은 테스트가 가짜 객체에서 한다(사람 몫).
- **가짜 알림 수신** — 127.0.0.1 임시 포트. 알림 URL 의 경로에 비밀 조각을 넣어 어디에도 새지 않는지 본다.
- **중앙은 selfhost 모드** — 러너 register 가 Agent 를 만드는 모드다. API 는 이 프로세스의 uvicorn 스레드, 워커는
  테스트가 tick 을 돌린다(`secrets`·`notifier` 연결 — `worker.main` 과 같다).
- **러너 기준 커밋 간격** — 러너 프로세스를 `BASE_FETCH_INTERVAL_SECONDS` 만 줄인 런처로 띄운다(제품 기본 60초).

흐름: 로그인 → GitHub 연결(가짜) → 알림 URL 저장 → 카드 [러너 붙이기] → 화면 명령의 서버·코드로 `connector setup`
→ 카드 자동 매칭 → bare 에 새 커밋(다른 곳에서 개발) → 기준 커밋 보고 → #1 [맡기기] → 수정(기준 = 새 커밋, 검증이
링크된 deps/·CHECK_DB 를 봄) → bare 에 runloom/<업무 키> → 검토 승인 → 초안 PR(Fixes #1) → "PR 확인" 알림 → PR 병합 →
Task 완료·지표 → #2 는 도구가 직접 커밋(실행 실패) → 실패 알림. 끝으로 비밀값(연결 코드·러너 토큰·env 값·알림 URL 경로·설치 토큰)이
중앙 DB 덤프·로그·화면·PR 본문·알림 본문에 없다.

`WORKFLOW_E2E=1` 일 때만 돈다. 실제 GitHub·Discord·모델 호출 없음 — 네트워크는 127.0.0.1 뿐이다.
"""

import hashlib
import json
import logging
import os
import re
import subprocess
import sys
import threading
import time
from html import unescape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
import uvicorn

from tests.e2e.test_github_app import (
    CLIENT_SECRET,
    INSTALL_TOKEN,
    INSTALLATION_ID,
    MANIFEST_CODE,
    PEM_BODY,
    REPO,
    FakeGitHubApp,
)
from tests.e2e.test_github_cycle import (
    log_in,
    _FAKE_CODEX,
    BILLING_FILES,
    DROP_PREFIXES,
    FIXES,
    ToFakeGitHub,
    World,
    _free_port,
    _git,
    artifact,
    drive,
    execs,
    fix_result,
    gh_issue,
    make_repo,
    q,
    request_of,
    result_branch_of,
    reviews_of,
    serve,
    task,
)
from workflow.adapters.callback_client import HttpCallbackClient
from workflow.adapters.db import connect
from workflow.adapters.notify_sender import NotifySender
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

CHECK_DB = "postgresql://agent:e2e-db-secret-7f3a@127.0.0.1:5434/check"  # 러너 로컬 등록에만 — 중앙에 없어야 한다
HOOK_SECRET = "e2eHookPath" + "W4" * 12  # 알림 URL 경로의 비밀 조각
DEPS_MARKER = "원본 폴더에만 있는 설치물\n"
PR_NUMBER = 101
REGISTRATION = "billing"  # 폴더 이름 = 로컬 등록 기본값

# 원본 폴더의 검증이 링크된 deps/ 와 CHECK_DB 를 본다 — 값은 해시로만 비교해 저장소 파일에도 남기지 않는다
PREPARED_TEST = (
    "import hashlib\nimport os\nfrom pathlib import Path\n\n\n"
    "def test_prepared_folder_and_env():\n"
    f"    assert (Path(__file__).parent.parent / 'deps' / 'marker.txt').read_text(encoding='utf-8') == {DEPS_MARKER!r}\n"
    "    value = os.environ.get('CHECK_DB', '').encode()\n"
    f"    assert hashlib.sha256(value).hexdigest() == {hashlib.sha256(CHECK_DB.encode()).hexdigest()!r}\n"
)
REPO_FILES = {
    **BILLING_FILES,
    ".gitignore": "__pycache__/\n.pytest_cache/\ndeps/\n",
    "tests/test_prepared.py": PREPARED_TEST,
}
UPSTREAM_FILE = "billing/tax.py"  # 사용자가 다른 곳에서 개발해 push 한 커밋의 파일


class FakeGitHubPulls(FakeGitHubApp):
    """PR 경로만 더한다 — 저장소 조회에 default_branch, PR 목록·생성·조회. 인증은 설치 토큰."""

    def __init__(self, bare: Path):
        super().__init__()
        self.bare = bare
        self.pulls: dict[int, dict] = {}

    def merge(self, number: int) -> None:
        with self.lock:
            self.pulls[number].update(state="closed", merged_at=utc_now())

    def handle(self, method, path, query, headers, body):
        prefix = f"/repos/{REPO}"
        if not ((method == "GET" and path == prefix) or path.startswith(prefix + "/pulls")):
            return super().handle(method, path, query, headers, body)
        authorized = headers.get("Authorization") == f"Bearer {self.token}"
        with self.lock:
            self.requests.append((method, path, authorized))
            if not authorized:
                return 401, {}, {"message": "Bad credentials"}
            rest = path[len(prefix):]
            if rest == "":
                return 200, {}, {"id": 5001, "full_name": REPO, "default_branch": "main"}
            if method == "GET" and rest == "/pulls":
                head = query["head"][0]
                return 200, {}, [pr for _, pr in sorted(self.pulls.items(), reverse=True)
                                 if f"acme:{pr['head']['ref']}" == head]
            if method == "POST" and rest == "/pulls":
                data = json.loads(body)
                exists = subprocess.run(["git", "--git-dir", str(self.bare), "rev-parse", "--verify", "--quiet",
                                         f"refs/heads/{data['head']}"], capture_output=True).returncode == 0
                if not exists or any(pr["head"]["ref"] == data["head"] for pr in self.pulls.values()):
                    return 422, {}, {"message": "Validation Failed"}
                number = PR_NUMBER + len(self.pulls)
                self.pulls[number] = {
                    "number": number, "html_url": f"https://github.com/{REPO}/pull/{number}", "state": "open",
                    "draft": data["draft"], "merged_at": None, "title": data["title"], "body": data["body"],
                    "head": {"ref": data["head"]}, "base": {"ref": data["base"]},
                }
                return 201, {}, self.pulls[number]
            if method == "GET" and (m := re.fullmatch(r"/pulls/(\d+)", rest)):
                pr = self.pulls.get(int(m.group(1)))
                return (200, {}, pr) if pr else (404, {}, {"message": "Not Found"})
            return 404, {}, {"message": "Not Found"}


class Receiver:
    """가짜 알림 수신 — 받은 (경로, JSON 본문) 을 모두 기록하고 204."""

    def __init__(self):
        self.received: list[tuple[str, dict]] = []
        self.lock = threading.Lock()

    def bodies(self) -> list[dict]:
        with self.lock:
            return [body for _, body in self.received]


def serve_receiver(receiver: Receiver) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"null")
            with receiver.lock:
                receiver.received.append((self.path, body))
            self.send_response(204)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def install_fake_codex(bin_dir: Path) -> None:
    """`test_github_cycle` 의 가짜 codex + `[scenario:selfcommit]` 면 도구가 직접 커밋한다 — 러너가 `commit_mismatch` 로
    실행을 실패(프로세스 종료 확인)시키는 경로(실패 알림용). 설치 토큰이 환경에 있으면 실패."""
    bin_dir.mkdir(parents=True)
    selfcommit = (
        'if "[scenario:selfcommit]" in prompt:\n'
        '    import subprocess\n'
        '    with open(os.path.join(cwd, "SELF.md"), "w") as f:\n'
        '        f.write("도구가 직접 커밋\\n")\n'
        '    subprocess.run(["git", "-C", cwd, "add", "-A"], check=True)\n'
        '    subprocess.run(["git", "-C", cwd, "-c", "user.name=tool", "-c", "user.email=tool@example.invalid",\n'
        '                    "commit", "-q", "-m", "self"], check=True)\n'
        '    sys.exit(0)\n'
    )
    text = _FAKE_CODEX.replace("__TOKEN__", INSTALL_TOKEN).replace("__FIXES__", repr(FIXES)).replace(
        "scenario = re.search", selfcommit + "scenario = re.search", 1,
    )
    script = bin_dir / "codex"
    script.write_text(f"#!{sys.executable}\n" + text.split("\n", 1)[1])
    script.chmod(0o755)


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    workdir = tmp_path_factory.mktemp("real-repo")
    (workdir / "logs").mkdir()

    # origin(bare) ← 원본 폴더(사용자 클론). 원본에만 deps/
    bare = workdir / "remote" / "billing.git"
    bare.parent.mkdir(parents=True)
    _git(bare.parent, "init", "-q", "--bare", "-b", "main", str(bare))
    original = workdir / "repos" / "billing"
    base = make_repo(original, REPO_FILES)
    _git(original, "remote", "add", "origin", "git@github.com:acme/billing.git")
    _git(original, "config", f"url.{bare}.insteadOf", "git@github.com:acme/billing.git")
    _git(original, "push", "-q", "origin", "main")
    (original / "deps").mkdir()
    (original / "deps" / "marker.txt").write_text(DEPS_MARKER, encoding="utf-8")

    fake = FakeGitHubPulls(bare)
    fake.add(REPO, gh_issue(REPO, 1, "청구서 번호 자릿수", labels=(), updated_at="2026-09-10T01:00:00Z",
                            body="번호가 5자리로 채워지지 않습니다. [scenario:invoice]"))
    fake.add(REPO, gh_issue(REPO, 2, "도구가 직접 커밋하는 요청", labels=(), updated_at="2026-09-10T02:00:00Z",
                            body="이 요청은 도구가 실패한다. [scenario:selfcommit]"))
    github_server = serve(fake)
    receiver = Receiver()
    receiver_server = serve_receiver(receiver)

    port = _free_port()
    inherited = {k: v for k, v in os.environ.items() if not k.startswith(DROP_PREFIXES)}
    central_env = {
        **inherited,
        "WORKFLOW_MODE": "selfhost",
        "WORKFLOW_DB_PATH": str(workdir / "central" / "db.sqlite"),
        "WORKFLOW_ARTIFACT_DIR": str(workdir / "central" / "artifacts"),
        "WORKFLOW_SECRET_DIR": str(workdir / "central" / "secrets"),
        "SESSION_SECRET": "e2e-session-secret-" + "s" * 20,
        "OPERATOR_TOKEN": "e2e-operator-token-" + "o" * 20,
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
                  connector_env=connector_env, fake=fake, fake_port=github_server.server_address[1],
                  billing=original, shop=None, base={"billing": base})
    world.ctx.update(bare=bare, receiver=receiver,
                     hook_url=f"http://127.0.0.1:{receiver_server.server_address[1]}/hooks/{HOOK_SECRET}")

    app = create_app(load_settings(central_env))
    app.state.github_transport = ToFakeGitHub(world.fake_port)
    api = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="info"))
    api_thread = threading.Thread(target=api.run, daemon=True)

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
            world.http = httpx.Client(base_url=world.central_url, follow_redirects=False, timeout=10.0,
                                      headers={"Origin": world.central_url})  # Origin 검사 (phase 15)
            yield world
        finally:
            if world.http is not None:
                world.http.close()
            world.stop()
            api.should_exit = True
            api_thread.join(10)
            github_server.shutdown()
            receiver_server.shutdown()
            root.removeHandler(log_handler)
            root.setLevel(old_level)
            log_handler.close()


def make_worker(world: World) -> Worker:
    """`worker.main` 과 같은 연결 — 소스별 설치 토큰 클라이언트, 비밀 저장소의 알림 URL, 알림 전송기. 진단은 없음(selfhost)."""
    settings = load_settings(world.central_env)
    store = SecretStore(settings.secret_dir)
    return Worker(lambda: connect(settings.db_path), world.store, HttpCallbackClient(), settings, utc_now,
                  github_for=SourceClients(settings, store, transport=ToFakeGitHub(world.fake_port)),
                  secrets=store, notifier=NotifySender())


def issue_task(world: World, number: int) -> str:
    return q(world, "SELECT task_id FROM source_issues WHERE issue_number = ?", number)[0]["task_id"]


def source_card(world: World, html: str | None = None) -> str:
    if html is None:
        page = world.http.get("/operator/github")
        assert page.status_code == 200, page.text[:500]
        html = page.text
    return html.split(f'data-source-card="{world.sources[REPO]}"', 1)[1].split("</section>", 1)[0]


def bare_git(world: World, *args: str) -> str:
    return _git(world.ctx["bare"], *args)


def received(world: World, event: str) -> list[dict]:
    return [b for b in world.ctx["receiver"].bodies() if b.get("event") == event]


def work_of(world: World, task_id: str):
    """단계(Task)가 속한 업무 행 (phase 14)."""
    (row,) = q(world, "SELECT w.* FROM work_items w JOIN tasks t ON t.work_item_id = w.work_item_id"
                      " WHERE t.task_id = ?", task_id)
    return row


def status_flow(world: World, work_item_id: str) -> list[str]:
    """업무 상태가 거쳐 온 값 — 처음 값 + `status_changed` 이벤트의 `to` 들."""
    rows = q(world, "SELECT data_json FROM work_item_events WHERE work_item_id = ? AND type = 'status_changed'"
                    " ORDER BY id", work_item_id)
    data = [json.loads(r["data_json"]) for r in rows]
    return [data[0]["from"], *(d["to"] for d in data)] if data else []


# --- 시나리오 ------------------------------------------------------------------------------


def test_01_login_connect_github_and_set_the_notification_url(world):
    http = world.http
    login = log_in(http, world.central_env["OPERATOR_TOKEN"])
    assert login.status_code == 303, login.text[:300]

    new = http.get("/operator/github/app/new")
    assert new.status_code == 200, new.text[:500]
    action = unescape(re.search(r'<form id="gh-manifest-form" method="post" action="([^"]+)"', new.text).group(1))
    manifest = json.loads(unescape(re.search(r'name="manifest" value="([^"]+)"', new.text).group(1)))
    assert manifest["default_permissions"]["pull_requests"] == "write"  # 초안 PR 을 연다
    callback = http.get("/operator/github/app/callback", params={
        "code": MANIFEST_CODE, "state": parse_qs(urlsplit(action).query)["state"][0]})
    assert callback.status_code == 303, callback.text[:500]
    state = parse_qs(urlsplit(callback.headers["location"]).query)["state"][0]
    setup = http.get("/operator/github/app/setup", params={
        "installation_id": INSTALLATION_ID, "setup_action": "install", "state": state})
    assert setup.status_code == 303, setup.text[:500]
    (source,) = http.get("/github/sources").json()["sources"]
    world.sources[REPO] = source["source_id"]

    saved = http.post("/operator/notifications/webhook", data={"url": world.ctx["hook_url"]})
    assert saved.status_code in (200, 303), saved.text[:500]
    page = http.get("/operator/notifications").text
    assert "설정됨" in page and HOOK_SECRET not in page

    world.worker = make_worker(world)
    report = world.worker.tick()
    assert report.sync_errors == 0 and report.issues_created == 2 and report.tasks_started == 0
    assert "data-runner-missing" in source_card(world)
    # 이슈 하나 = 업무 하나 — 가져온 순서대로 키, 지시 전이라 새로 들어옴
    works = q(world, "SELECT key_number, status, source_key FROM work_items ORDER BY key_number")
    assert [(w["key_number"], w["status"], w["source_key"]) for w in works] == [
        (1, "새로 들어옴", f"{REPO}#1"), (2, "새로 들어옴", f"{REPO}#2")]


def test_02_attach_runner_button_gives_one_command_and_setup_registers_the_folder(world):
    issued = world.http.post(f"/operator/github/sources/{world.sources[REPO]}/runner")
    assert issued.status_code == 200, issued.text[:500]
    command = unescape(source_card(world, issued.text).split("data-runner-command", 1)[1])
    match = re.search(r"install-runner\.sh --server (\S+) --code (\S+) --repo <", command)
    assert match, command[:500]
    server, code = match.groups()
    assert server == world.central_url
    world.ctx["connect_code"] = code

    py = sys.executable
    out = world.once("connector-setup", [
        py, "-m", "workflow.connector", "setup", "--server", server, "--code", code, "--repo", str(world.billing),
        "--tool", "codex", "--verify", f"check={py} -m pytest -q -p no:cacheprovider",
        "--link", "deps", "--env", f"CHECK_DB={CHECK_DB}",
    ])
    assert "GitHub acme/billing" in out and "링크 1개" in out and "환경변수 CHECK_DB" in out
    assert CHECK_DB not in out
    world.ctx["runner_token"] = json.loads(
        (Path(world.connector_env["WORKFLOW_CONNECTOR_HOME"]) / "token.json").read_text())["token"]

    (agent,) = q(world, "SELECT * FROM agents WHERE local_registration_id = ?", REGISTRATION)
    world.ctx["agent_id"] = agent["agent_id"]
    assert agent["name"] == "billing" and agent["base_commit"] == world.base["billing"]

    # 러너 — 기준 커밋 fetch 간격만 줄인 런처(제품 기본 60초)
    launcher = ("import sys; import workflow.connector.runner as r; r.BASE_FETCH_INTERVAL_SECONDS = 0.5; "
                "from workflow.connector.cli import main; sys.exit(main(sys.argv[1:]))")
    world.spawn("connector", [py, "-c", launcher, "run", "--adapter", "codex", "--claim-interval", "0.5",
                              "--heartbeat-interval", "1"], world.connector_env)
    deadline = time.monotonic() + 30
    while "data-runner-missing" in source_card(world):
        assert time.monotonic() < deadline, world.log_tails()
        time.sleep(0.3)
    card = source_card(world)
    for value in ("acme/billing", world.ctx["agent_id"], "check"):
        assert f'<span class="mono">{value}</span> (자동)' in card, card
    assert world.ctx["connect_code"] not in card


def test_03_new_upstream_commit_becomes_the_base_of_the_delegated_fix(world):
    elsewhere = world.workdir / "elsewhere"
    _git(world.workdir, "clone", "-q", str(world.ctx["bare"]), str(elsewhere))
    (elsewhere / UPSTREAM_FILE).write_text("def tax(amount: int) -> int:\n    return amount // 10\n")
    _git(elsewhere, "add", "-A")
    _git(elsewhere, "-c", "user.name=dev", "-c", "user.email=dev@example.invalid", "commit", "-q", "-m", "tax")
    _git(elsewhere, "push", "-q", "origin", "main")
    upstream = _git(elsewhere, "rev-parse", "HEAD")
    world.ctx["upstream"] = upstream

    deadline = time.monotonic() + 30
    while q(world, "SELECT base_commit FROM agents WHERE agent_id = ?", world.ctx["agent_id"])[0][0] != upstream:
        assert time.monotonic() < deadline, world.log_tails()
        time.sleep(0.3)
    assert _git(world.billing, "rev-parse", "HEAD") == world.base["billing"]  # 원본 폴더는 그대로 뒤처져 있다

    a_id = issue_task(world, 1)
    world.tasks["A"] = a_id
    assert world.http.post(f"/tasks/{a_id}/delegate").status_code == 303
    drive(world, lambda: reviews_of(world, a_id) and task(world, reviews_of(world, a_id)[0]["task_id"])["finished_at"],
          "#1 검토 끝")

    (fix,) = execs(world, a_id)
    assert request_of(fix).target.base_commit == upstream
    assert request_of(fix).target.verification_profile_id == "check"
    result = fix_result(world, fix)
    assert result.outcome == "ready_for_review" and result.verification.exit_code == 0
    verification = artifact(world, fix["execution_id"], "verification_log").decode()
    assert "passed" in verification  # 링크된 deps/ 와 CHECK_DB 를 본 검증(test_prepared)이 통과
    before = artifact(world, fix["execution_id"], "test_log_before").decode()
    assert "1 failed" in before and "test_invoice_format" in before  # 기준 코드에선 재현 테스트만 실패

    # 결과 브랜치가 origin(bare) 에 — 부모는 새 커밋, 기본 브랜치는 그대로
    assert fix["branch_pushed"] == 1
    assert bare_git(world, "rev-parse", f"refs/heads/{result_branch_of(world, a_id)}") == result.result_commit
    assert bare_git(world, "rev-parse", f"{result.result_commit}^") == upstream
    assert bare_git(world, "rev-parse", "refs/heads/main") == upstream
    assert _git(world.billing, "rev-parse", "main") == world.base["billing"]  # 원본 폴더의 main 은 건드리지 않는다


def test_04_review_approval_opens_a_draft_pr_and_notifies_once(world):
    a_id = world.tasks["A"]
    (review,) = reviews_of(world, a_id)
    assert task(world, review["task_id"])["status"] == "완료"

    drive(world, lambda: received(world, "pr_opened"), "PR 확인 알림")
    (pr,) = world.fake.pulls.values()
    branch = result_branch_of(world, a_id)
    key = branch.removeprefix("runloom/")
    assert (pr["number"], pr["draft"], pr["head"]["ref"], pr["base"]["ref"]) == (PR_NUMBER, True, branch, "main")
    assert pr["body"].splitlines()[0] == "Fixes #1" and f"<!-- runloom:task={a_id} -->" in pr["body"]
    assert f"업무 키: {key}" in pr["body"].splitlines()
    assert pr["title"] == f"{key} 청구서 번호 자릿수"
    (row,) = q(world, "SELECT state, pr_number FROM task_pull_requests WHERE task_id = ?", a_id)
    assert (row["state"], row["pr_number"]) == ("open", PR_NUMBER)
    fix_task = task(world, a_id)
    assert fix_task["status"] == "확인 필요" and f"PR 확인 — #{PR_NUMBER}" in fix_task["status_reason"]

    (note,) = received(world, "pr_opened")
    assert note["content"].startswith("[Runloom] PR 확인 — 청구서 번호 자릿수")
    assert note["pr_url"] == f"https://github.com/{REPO}/pull/{PR_NUMBER}"
    assert note["task_url"] == f"{world.central_url}/tasks?open={key}"  # 업무 주소(phase 16)
    assert all(path == f"/hooks/{HOOK_SECRET}" for path, _ in world.ctx["receiver"].received)

    (comment,) = world.fake.issue_comments(REPO, 1)  # 원본 이슈 댓글이 올린 브랜치를 가리킨다
    assert f"`{branch}`" in comment["body"] and "푸시하지 않" not in comment["body"]

    detail = world.http.get(f"/tasks/{a_id}").text
    assert "data-pull-request" in detail and f"/pull/{PR_NUMBER}" in detail
    for _ in range(2):  # 다시 돌아도 PR·알림이 늘지 않는다
        world.worker.tick()
    assert len(world.fake.pulls) == 1 and len(received(world, "pr_opened")) == 1


def test_05_merging_the_pr_completes_the_task_and_counts_in_metrics(world):
    a_id = world.tasks["A"]
    world.fake.merge(PR_NUMBER)
    drive(world, lambda: task(world, a_id)["finished_at"], "PR 병합 → 완료")
    fix_task = task(world, a_id)
    assert (fix_task["status"], fix_task["status_reason"]) == ("완료", "PR 병합")
    (issue,) = q(world, "SELECT merged_pr_number, pr_merged_at FROM source_issues WHERE issue_number = 1")
    assert issue["merged_pr_number"] == PR_NUMBER and issue["pr_merged_at"] is not None

    metrics = world.http.get("/metrics.json")
    assert metrics.status_code == 200, metrics.text[:500]
    assert sum(g["intake_to_merge"]["n"] for g in metrics.json()["groups"]) == 1  # 도입 후 1건
    assert bare_git(world, "rev-parse", "refs/heads/main") == world.ctx["upstream"]  # 병합은 사람(가짜 GitHub) 몫

    # 업무 기준 (phase 14) — 이슈 #1 = 업무 RUN-1 하나, 단계 = 수정 + 같은 업무의 검토
    work = work_of(world, a_id)
    (review,) = reviews_of(world, a_id)
    stages = q(world, "SELECT task_id, kind FROM tasks WHERE work_item_id = ? ORDER BY created_at, task_id",
               work["work_item_id"])
    assert [(s["task_id"], s["kind"]) for s in stages] == [(a_id, "bug_fix"), (review["task_id"], "code_review")]
    assert (work["key_number"], work["status"]) == (1, "완료")
    assert work["closed_at"] is not None
    flow = status_flow(world, work["work_item_id"])
    assert flow[0] == "새로 들어옴" and flow[-1] == "완료"
    wanted = iter(flow)  # 새로 들어옴 → (지시) → 에이전트 작업 중 → PR · 검토 → 완료 순서로 거쳤다
    assert all(status in wanted for status in ("새로 들어옴", "에이전트 작업 중", "PR · 검토", "완료")), flow
    assert result_branch_of(world, a_id) == "runloom/RUN-1"
    assert world.fake.pulls[PR_NUMBER]["title"].startswith("RUN-1 ")
    home = world.http.get("/tasks").text
    assert home.count('href="/tasks?open=RUN-1"') >= 1 and f'href="/tasks/{review["task_id"]}"' not in home


def test_06_a_failed_fix_sends_one_failure_notification(world):
    b_id = issue_task(world, 2)
    world.tasks["B"] = b_id
    assert world.http.post(f"/tasks/{b_id}/delegate").status_code == 303
    drive(world, lambda: [n for n in received(world, "task_failed") if n["task_id"] == b_id], "실패 알림")
    b_task = task(world, b_id)
    assert b_task["status"] == "실패" and b_task["status_reason"].startswith("commit_mismatch")
    for _ in range(2):
        world.worker.tick()
    (note,) = received(world, "task_failed")
    assert note["content"].startswith("[Runloom] 실패 — 도구가 직접 커밋하는 요청")
    events = sorted(b["event"] for b in world.ctx["receiver"].bodies())
    assert events == ["pr_opened", "task_failed"]
    rows = q(world, "SELECT event, state FROM notifications ORDER BY created_at")
    assert [(r["event"], r["state"]) for r in rows] == [("pr_opened", "sent"), ("task_failed", "sent")]


def test_07_failed_work_is_my_turn_and_retry_adds_a_new_stage_on_a_new_branch(world):
    """실패한 업무는 내 차례(이유 "실패 — …") — [다시 맡기기] 가 같은 업무에 새 단계를 만들고 `runloom/<키>-2` 로 돈다."""
    b_id = world.tasks["B"]
    work = work_of(world, b_id)
    assert work["key_number"] == 2
    assert (work["status"], work["status_reason"].startswith("실패 — ")) == ("내 차례", True), dict(work)
    (request,) = [r for r in world.http.get("/human-requests").json()["requests"] if r["task_id"] == b_id]
    assert request["code"] == "stage_failed"
    assert world.http.get("/work/RUN-2").headers["location"] == "/tasks?open=RUN-2"  # 업무 주소(phase 16)
    detail = world.http.get("/tasks?open=RUN-2").text
    assert 'value="retry">다시 맡기기<' in detail and 'value="close">닫기<' in detail

    response = world.http.post(f"/human-requests/{request['request_id']}/responses", json={
        "response_id": "resp-retry-b", "expected_revision": request["revision"], "action": "retry", "text": ""})
    assert response.status_code == 200, response.text[:500]
    stages = q(world, "SELECT * FROM tasks WHERE work_item_id = ? ORDER BY created_at, task_id", work["work_item_id"])
    assert [s["task_id"] for s in stages][0] == b_id and len(stages) == 2
    retry = stages[1]
    assert (retry["kind"], retry["status"] != "실패") == ("bug_fix", True)
    world.tasks["B2"] = retry["task_id"]

    (run,) = drive(world, lambda: execs(world, retry["task_id"]), "다시 맡긴 단계 착수")
    sent = request_of(run)
    assert (sent.work_key, sent.branch_seq) == ("RUN-2", 2)
    # 같은 도구 실패가 다시 난다 — 새 단계도 실패로 마감되고 업무는 다시 내 차례
    drive(world, lambda: task(world, retry["task_id"])["finished_at"], "다시 맡긴 단계 마감")
    world.worker.tick()
    assert work_of(world, b_id)["status"] == "내 차례"
    assert len(q(world, "SELECT 1 FROM work_items")) == 2  # 업무는 늘지 않는다


def test_08_secrets_stay_out_of_the_central_side(world):
    """연결 코드·러너 토큰·env 값·알림 URL 경로·설치 토큰 — 중앙 DB 덤프·산출물·로그·화면·PR 본문·알림 본문에 없다.
    연결 코드만은 `connect_codes` 에 1회용 기록(사용 처리)으로 남는다 — 기존 설계(운영자 화면 목록)다."""
    code = world.ctx["connect_code"]
    needles = {"connect_code": code, "runner_token": world.ctx["runner_token"], "env": CHECK_DB,
               "hook": HOOK_SECRET, "install_token": INSTALL_TOKEN, "pem": PEM_BODY, "client_secret": CLIENT_SECRET}

    conn = world.conn()
    try:
        (used,) = conn.execute("SELECT used_at FROM connect_codes WHERE code = ?", (code,)).fetchone()
        dump = "\n".join(line for line in conn.iterdump() if not line.startswith('INSERT INTO "connect_codes"'))
    finally:
        conn.close()
    assert used is not None
    leaked = [name for name, value in needles.items() if value in dump]

    artifacts = Path(world.central_env["WORKFLOW_ARTIFACT_DIR"])
    logs = [*(world.workdir / "logs").glob("*.log"),
            *(Path(world.connector_env["WORKFLOW_CONNECTOR_HOME"]) / "logs").glob("*.log")]
    for path in [*artifacts.rglob("*"), *logs]:
        if path.is_file():
            data = path.read_text(encoding="utf-8", errors="replace")
            leaked += [f"{name}@{path.name}" for name, value in needles.items() if value in data]
    assert (world.workdir / "logs" / "central.log").stat().st_size > 0

    pages = ["/tasks", f"/tasks/{world.tasks['A']}", f"/tasks/{world.tasks['B']}", "/operator/github",
             "/operator/notifications", "/metrics", "/github/sources"]
    for page in pages:
        body = world.http.get(page).text
        leaked += [f"{name}@{page}" for name, value in needles.items() if value in body]
    for pr in world.fake.pulls.values():
        leaked += [f"{name}@pr" for name, value in needles.items() if value in pr["body"] + pr["title"]]
    for note in world.ctx["receiver"].bodies():
        text = json.dumps(note, ensure_ascii=False)
        leaked += [f"{name}@notification" for name, value in needles.items() if value in text]
    assert leaked == []
