"""Jira 업무 순환 e2e — 가짜 Jira Cloud·가짜 GitHub App·가짜 러너로 연결부터 병합·후속 이슈까지 (phase 18 step 9, ADR-0024).

`test_work_ui` 의 구성(가짜 GitHub App + PR 경로, bare origin, uvicorn 스레드의 중앙 API, 테스트가 돌리는 워커, 하위
프로세스 러너·PATH 앞 가짜 `codex`)을 그대로 쓰고, 이 모듈이 더하는 것:
- **가짜 Jira Cloud** — httpx `MockTransport` 처리기(`FakeJiraCloud`). 사이트 `shopco.atlassian.net`(tenant_info)과
  게이트웨이 `api.atlassian.com/ex/jira/{cloudId}` 의 `rest/api/3` 경로만 흉내 낸다 — myself·프로젝트 검색·조회·상태 목록·
  `search/jql`(커서·statusCategory·라벨 JQL)·이슈 상태·전환 조회/실행·이슈 생성·링크 유형·링크. 상태가 있다: 전환·생성이
  이슈의 상태·`updated` 를 바꾼다. 웹(`app.state.jira_transport`)과 워커(`JiraClients(transport=)`)가 같은 가짜를 본다.

흐름:
1. 관리자 로그인 → GitHub 연결(가짜) → 러너 붙이기.
2. Jira 연결(사이트·이메일·토큰) → 프로젝트 찾기·추가(연결 저장소 acme/billing, 지금부터) → 설정(세 상태·후속 유형).
3. Jira 에 SHOP-12 → 동기화 → RUN-1 `새로 들어옴`(맡기기 전 착수 없음) → 에이전트에게 맡김 → Jira "진행 중".
4. 수정·검토 → 초안 PR(연결 저장소, `Fixes` 없음·원본 줄) → `PR · 검토` → Jira "리뷰중" → 병합 → `완료` → Jira "종료".
5. 후속 규칙을 새 업무(`new_work`)로 → SHOP-13 맡김 → Jira 에서 완료 범주로 옮김 → 수정 결과가 나와도 다음 단계 대기 →
   다시 열면 검토가 새 업무 RUN-3 으로 생김 → Jira 에 후속 이슈 1건(라벨·링크) → 다음 동기화 뒤 업무 수 그대로.
6. 비밀(Jira 토큰)이 DB·로그·화면에 없다.
마지막 함수는 따로: v13 사본(업무·GitHub 소스·매핑 행) → 앱 시작이 v14 로 올림 → 같은 가짜 Jira 로 연결·가져오기.

`WORKFLOW_E2E=1` 일 때만 돈다. 실제 Jira·GitHub·Discord·모델 호출 없음 — 네트워크는 127.0.0.1 과 MockTransport 뿐이다.
테스트 함수는 번호 순으로 이어지며 앞 단계의 상태(`world`)를 쓴다.
"""

import base64
import json
import logging
import os
import re
import sys
import threading
import time
from datetime import datetime, timezone
from html import unescape
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
import uvicorn

from tests.e2e.test_github_app import INSTALLATION_ID, MANIFEST_CODE, REPO
from tests.e2e.test_github_cycle import (
    BILLING_FILES,
    DROP_PREFIXES,
    ToFakeGitHub,
    World,
    _free_port,
    _git,
    drive,
    execs,
    log_in,
    make_repo,
    q,
    serve,
)
from tests.e2e.test_real_repo import (
    PR_NUMBER,
    FakeGitHubPulls,
    install_fake_codex,
    source_card,
    status_flow,
)
from tests.e2e.test_work_ui import page, panel_of
from workflow.adapters.callback_client import HttpCallbackClient
from workflow.adapters.db import SCHEMA_VERSION, connect
from workflow.adapters.secret_store import JIRA_API_TOKEN, SecretStore
from workflow.server import worker as worker_module
from workflow.server.app import create_app
from workflow.server.auth import utc_now
from workflow.server.github_clients import SourceClients
from workflow.server.jira_sync import JiraClients
from workflow.server.settings import load_settings
from workflow.server.worker import Worker

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(os.environ.get("WORKFLOW_E2E") != "1", reason="WORKFLOW_E2E=1 일 때만"),
]

SITE = "https://shopco.atlassian.net"
CLOUD = "7d0c5a3e-2b4f-4c1d-9e8a-0f1e2d3c4b5a"
GATEWAY = f"https://api.atlassian.com/ex/jira/{CLOUD}"
EMAIL = "dev@shopco.example"
JIRA_TOKEN = "ATATT3xFfGF0e2eJiraTOKEN" + "J7" * 12  # 가짜 Jira 만 받는 값 — DB·로그·화면에 나오면 안 된다
PROJECT_ID, PROJECT_KEY = "10000", "SHOP"
STATUSES = {"대기": "new", "진행 중": "indeterminate", "리뷰중": "indeterminate", "종료": "done"}
ISSUE_TYPES = {"10001": "버그", "10002": "작업"}
REGISTRATION = "billing"


class FakeJiraCloud:
    """상태 있는 가짜 Jira Cloud — `httpx.MockTransport(fake)` 로 쓴다. 요청은 모두 (메서드, 경로, 인증 맞음) 로 남긴다."""

    def __init__(self):
        self.lock = threading.Lock()
        self.issues: dict[str, dict] = {}  # issue id → 칸
        self.links: list[tuple[str, str, str]] = []
        self.requests: list[tuple[str, str, bool]] = []
        self._next = 12
        self._last_ms = 0
        self._auth = "Basic " + base64.b64encode(f"{EMAIL}:{JIRA_TOKEN}".encode()).decode()

    # ── 테스트가 Jira 화면에서 하는 일 ──

    def _stamp(self) -> str:
        """엄격히 늘어나는 Jira 형식 시각(`…000+0000`) — 커서(포함 경계)가 바뀐 이슈를 다시 받게 한다."""
        ms = max(int(time.time() * 1000), self._last_ms + 1)
        self._last_ms = ms
        moment = datetime.fromtimestamp(ms / 1000, timezone.utc)
        return moment.strftime("%Y-%m-%dT%H:%M:%S.") + f"{ms % 1000:03d}+0000"

    def add(self, summary: str, body: str, *, issue_type: str = "10001", labels=(), status: str = "대기") -> dict:
        with self.lock:
            number = self._next
            self._next += 1
            stamp = self._stamp()
            issue = {
                "id": str(10000 + number), "key": f"{PROJECT_KEY}-{number}", "summary": summary,
                "description": {"type": "doc", "version": 1, "content": [
                    {"type": "paragraph", "content": [{"type": "text", "text": body}]}]},
                "status": status, "issuetype": issue_type, "labels": list(labels), "created": stamp, "updated": stamp,
            }
            self.issues[issue["id"]] = issue
            return issue

    def move(self, key: str, status: str) -> None:
        with self.lock:
            issue = self.by_key(key)
            issue.update(status=status, updated=self._stamp())

    def by_key(self, key: str) -> dict:
        return next(i for i in self.issues.values() if i["key"] == key)

    def status_of(self, key: str) -> str:
        with self.lock:
            return self.by_key(key)["status"]

    def posts(self, path_end: str) -> list[str]:
        with self.lock:
            return [p for m, p, _ in self.requests if m == "POST" and p.endswith(path_end)]

    # ── HTTP ──

    def _json(self, issue: dict) -> dict:
        return {"id": issue["id"], "key": issue["key"], "fields": {
            "project": {"id": PROJECT_ID}, "summary": issue["summary"], "description": issue["description"],
            "status": self._status(issue["status"]), "issuetype": {"name": ISSUE_TYPES[issue["issuetype"]]},
            "priority": {"name": "Medium"}, "labels": issue["labels"],
            "created": issue["created"], "updated": issue["updated"],
        }}

    @staticmethod
    def _status(name: str) -> dict:
        return {"name": name, "statusCategory": {"key": STATUSES[name]}}

    def _transitions(self, issue: dict) -> list[dict]:
        return [{"id": str(11 + i), "name": f"{name}(으)로", "to": self._status(name)}
                for i, name in enumerate(STATUSES) if name != issue["status"]]

    def _search(self, jql: str) -> dict:
        assert jql.startswith(f"project = {PROJECT_ID} AND "), jql
        if label := re.search(r'labels = "([^"]+)"', jql):
            found = [i for i in self.issues.values() if label.group(1) in i["labels"]]
            return {"issues": [{"id": i["id"], "key": i["key"]} for i in found], "isLast": True}
        cursor = re.search(r"updated >= (\d+)", jql)

        def ms(issue: dict) -> int:
            return int(datetime.strptime(issue["updated"], "%Y-%m-%dT%H:%M:%S.%f%z").timestamp() * 1000)

        found = sorted(
            (i for i in self.issues.values()
             if (cursor is None or ms(i) >= int(cursor.group(1)))
             and ("statusCategory != Done" not in jql or STATUSES[i["status"]] != "done")),
            key=lambda i: (i["updated"], i["key"]),
        )
        return {"issues": [self._json(i) for i in found], "isLast": True}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        host, path = request.url.host, request.url.path
        authorized = request.headers.get("Authorization") == self._auth
        with self.lock:
            self.requests.append((request.method, path, authorized))
            if (host, path) == ("shopco.atlassian.net", "/_edge/tenant_info"):
                return httpx.Response(200, json={"cloudId": CLOUD})
            if host == "api.atlassian.com" and path.startswith(f"/ex/jira/{CLOUD}/rest/api/3/"):
                rest = path[len(f"/ex/jira/{CLOUD}/rest/api/3"):]
            elif host == "shopco.atlassian.net" and path.startswith("/rest/api/3/"):
                rest = path[len("/rest/api/3"):]
            else:
                raise AssertionError(f"가짜 Jira 밖 주소: {request.url}")
            if not authorized:
                return httpx.Response(401, json={"errorMessages": ["인증 실패"]})
            body = json.loads(request.content) if request.content else None
            return self._route(request.method, rest, request.url.params, body)

    def _route(self, method: str, rest: str, params, body) -> httpx.Response:
        project = {"id": PROJECT_ID, "key": PROJECT_KEY, "name": "쇼핑몰"}
        if (method, rest) == ("GET", "/myself"):
            return httpx.Response(200, json={"accountId": "5b10ac8d82e05b22cc7d4ef5", "displayName": "김개발"})
        if (method, rest) == ("GET", "/project/search"):
            return httpx.Response(200, json={"values": [project], "isLast": True})
        if (method, rest) == ("GET", f"/project/{PROJECT_ID}"):
            return httpx.Response(200, json=project)
        if (method, rest) == ("GET", f"/project/{PROJECT_KEY}/statuses"):
            return httpx.Response(200, json=[
                {"id": type_id, "name": name, "subtask": False, "statuses": [{"name": s} for s in STATUSES]}
                for type_id, name in ISSUE_TYPES.items()])
        if (method, rest) == ("GET", "/search/jql"):
            return httpx.Response(200, json=self._search(params["jql"]))
        if (method, rest) == ("GET", "/issueLinkType"):
            return httpx.Response(200, json={"issueLinkTypes": [{"name": "Blocks"}, {"name": "Relates"}]})
        if (method, rest) == ("POST", "/issueLink"):
            self.links.append((body["type"]["name"], body["inwardIssue"]["id"], body["outwardIssue"]["id"]))
            return httpx.Response(201)
        if (method, rest) == ("POST", "/issue"):
            fields = body["fields"]
            assert fields["project"] == {"id": PROJECT_ID} and fields["description"]["type"] == "doc", fields
            number = self._next
            self._next += 1
            stamp = self._stamp()
            issue = {"id": str(10000 + number), "key": f"{PROJECT_KEY}-{number}", "summary": fields["summary"],
                     "description": fields["description"], "status": "대기",
                     "issuetype": fields["issuetype"]["id"], "labels": fields["labels"],
                     "created": stamp, "updated": stamp}
            self.issues[issue["id"]] = issue
            return httpx.Response(201, json={"id": issue["id"], "key": issue["key"]})
        if m := re.fullmatch(r"/issue/(\d+)(/transitions)?", rest):
            issue = self.issues.get(m.group(1))
            if issue is None:
                return httpx.Response(404, json={"errorMessages": ["이슈 없음"]})
            if method == "GET" and m.group(2) is None:
                assert params["fields"] == "status"
                return httpx.Response(200, json={"id": issue["id"], "fields": {"status": self._status(issue["status"])}})
            if method == "GET":
                return httpx.Response(200, json={"transitions": self._transitions(issue)})
            target = next(t for t in self._transitions(issue) if t["id"] == body["transition"]["id"])
            issue.update(status=target["to"]["name"], updated=self._stamp())
            return httpx.Response(204)
        return httpx.Response(404, json={"errorMessages": ["없음"]})


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    workdir = tmp_path_factory.mktemp("jira-cycle")
    (workdir / "logs").mkdir()

    bare = workdir / "remote" / "billing.git"
    bare.parent.mkdir(parents=True)
    _git(bare.parent, "init", "-q", "--bare", "-b", "main", str(bare))
    original = workdir / "repos" / "billing"
    base = make_repo(original, BILLING_FILES)
    _git(original, "remote", "add", "origin", "git@github.com:acme/billing.git")
    _git(original, "config", f"url.{bare}.insteadOf", "git@github.com:acme/billing.git")
    _git(original, "push", "-q", "origin", "main")

    fake = FakeGitHubPulls(bare)  # GitHub 이슈는 없다 — 업무는 모두 Jira 에서 온다
    github_server = serve(fake)
    jira = FakeJiraCloud()

    port = _free_port()
    inherited = {k: v for k, v in os.environ.items() if not k.startswith(DROP_PREFIXES)}
    central_env = {
        **inherited,
        "WORKFLOW_MODE": "selfhost",
        "WORKFLOW_DB_PATH": str(workdir / "central" / "db.sqlite"),
        "WORKFLOW_ARTIFACT_DIR": str(workdir / "central" / "artifacts"),
        "WORKFLOW_SECRET_DIR": str(workdir / "central" / "secrets"),
        "SESSION_SECRET": "e2e-session-secret-" + "j" * 20,
        "OPERATOR_TOKEN": "e2e-operator-token-" + "j" * 20,
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
    world.ctx.update(bare=bare, jira=jira)

    app = create_app(load_settings(central_env))
    app.state.github_transport = ToFakeGitHub(world.fake_port)
    app.state.jira_transport = httpx.MockTransport(jira)
    api = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="info"))
    api_thread = threading.Thread(target=api.run, daemon=True)

    log_handler = logging.FileHandler(workdir / "logs" / "central.log", encoding="utf-8")
    log_handler.setFormatter(logging.Formatter("%(name)s %(levelname)s %(message)s"))
    root = logging.getLogger()
    old_level = root.level
    root.addHandler(log_handler)
    root.setLevel(logging.DEBUG)  # httpx 요청 줄까지 남겨 토큰이 없는지 본다
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(worker_module, "GITHUB_SYNC_INTERVAL_SECONDS", 0)
        patch.setattr(worker_module, "JIRA_SYNC_INTERVAL_SECONDS", 0)
        try:
            api_thread.start()
            deadline = time.monotonic() + 30
            while not api.started:
                assert time.monotonic() < deadline and api_thread.is_alive(), "중앙 API 가 뜨지 않음"
                time.sleep(0.1)
            world.http = httpx.Client(base_url=world.central_url, follow_redirects=False, timeout=10.0,
                                      headers={"Origin": world.central_url})
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
    """`worker.main` 과 같은 연결 + Jira — 소스별 설치 토큰 클라이언트, 비밀 파일 토큰의 Jira 클라이언트."""
    settings = load_settings(world.central_env)
    store = SecretStore(settings.secret_dir)
    return Worker(lambda: connect(settings.db_path), world.store, HttpCallbackClient(), settings, utc_now,
                  github_for=SourceClients(settings, store, transport=ToFakeGitHub(world.fake_port)),
                  secrets=store, jira_for=JiraClients(store, transport=httpx.MockTransport(world.ctx["jira"])))


def work(world: World, key_number: int):
    (row,) = q(world, "SELECT * FROM work_items WHERE key_number = ?", key_number)
    return row


def tasks_of(world: World, key_number: int) -> list:
    return q(world, "SELECT t.* FROM tasks t JOIN work_items w ON w.work_item_id = t.work_item_id"
                    " WHERE w.key_number = ? ORDER BY t.created_at", key_number)


def deliveries(world: World, key_number: int) -> list[tuple]:
    rows = q(world, "SELECT d.action, d.moment, d.target, d.state FROM jira_deliveries d JOIN work_items w"
                    " ON w.work_item_id = d.work_item_id WHERE w.key_number = ? ORDER BY d.created_at, d.rowid",
             key_number)
    return [tuple(r) for r in rows]


def issue_state(world: World, key: str) -> str:
    """스냅숏의 열림/닫힘 — 완료 범주(done)면 closed. 업무의 `source_state` 는 Jira 상태 이름이다."""
    return q(world, "SELECT state FROM jira_issues WHERE issue_key = ?", key)[0]["state"]


def assign(world: World, key: str) -> None:
    response = world.http.post(f"/work/{key}/assignee", data={"assignee": f"agent:{world.ctx['agent_id']}"})
    assert response.status_code == 303, response.text[:500]


def jira_section(html: str) -> str:
    return unescape(html.split("data-jira", 1)[1]) if "data-jira" in html else ""


# --- 시나리오 ------------------------------------------------------------------------------


def test_01_admin_connects_github_and_attaches_the_runner(world):
    http = world.http
    assert log_in(http, world.central_env["OPERATOR_TOKEN"]).status_code == 303
    new = http.get("/operator/github/app/new")
    action = unescape(re.search(r'<form id="gh-manifest-form" method="post" action="([^"]+)"', new.text).group(1))
    callback = http.get("/operator/github/app/callback", params={
        "code": MANIFEST_CODE, "state": parse_qs(urlsplit(action).query)["state"][0]})
    state = parse_qs(urlsplit(callback.headers["location"]).query)["state"][0]
    setup = http.get("/operator/github/app/setup", params={
        "installation_id": INSTALLATION_ID, "setup_action": "install", "state": state})
    assert setup.status_code == 303, setup.text[:500]
    (source,) = http.get("/github/sources").json()["sources"]
    world.sources[REPO] = source["source_id"]

    issued = http.post(f"/operator/github/sources/{world.sources[REPO]}/runner")
    command = unescape(source_card(world, issued.text).split("data-runner-command", 1)[1])
    server, code = re.search(r"install-runner\.sh --server (\S+) --code (\S+) --repo <", command).groups()
    py = sys.executable
    world.once("connector-setup", [
        py, "-m", "workflow.connector", "setup", "--server", server, "--code", code, "--repo", str(world.billing),
        "--tool", "codex", "--verify", f"check={py} -m pytest -q -p no:cacheprovider",
    ])
    (agent,) = q(world, "SELECT agent_id FROM agents WHERE local_registration_id = ?", REGISTRATION)
    world.ctx["agent_id"] = agent["agent_id"]
    world.spawn("connector", [py, "-m", "workflow.connector", "run", "--adapter", "codex",
                              "--claim-interval", "0.5", "--heartbeat-interval", "1"], world.connector_env)
    world.worker = make_worker(world)
    deadline = time.monotonic() + 30
    while "data-runner-missing" in source_card(world) or not q(
            world, "SELECT 1 FROM connectors WHERE supported_kinds_json IS NOT NULL"):
        assert time.monotonic() < deadline, world.log_tails()
        time.sleep(0.3)


def test_02_jira_connect_project_and_settings(world):
    http, jira = world.http, world.ctx["jira"]
    sources = page(world, "/repos")
    assert 'action="/operator/jira/connect"' in sources

    connected = http.post("/operator/jira/connect", data={"site_url": SITE, "email": EMAIL, "token": JIRA_TOKEN})
    assert (connected.status_code, connected.headers["location"]) == (303, "/repos"), connected.text
    (row,) = q(world, "SELECT site_url, cloud_id, api_base, email FROM jira_connections")
    assert tuple(row) == (SITE, CLOUD, "gateway", EMAIL)
    secrets = SecretStore(load_settings(world.central_env).secret_dir)
    assert secrets.read(JIRA_API_TOKEN) == JIRA_TOKEN

    found = http.get("/operator/jira/projects", params={"q": "SHOP"})
    assert found.status_code == 200 and "쇼핑몰" in found.text
    added = http.post("/operator/jira/projects", data={
        "project_id": PROJECT_ID, "github_source_id": world.sources[REPO], "start_mode": "from_now"})
    assert added.status_code == 303, added.text[:500]
    (project,) = q(world, "SELECT source_id, project_key, cursor_ms FROM jira_projects")
    assert project["project_key"] == PROJECT_KEY and project["cursor_ms"] is not None
    world.ctx["jira_source"] = project["source_id"]

    saved = http.post(f"/operator/jira/projects/{project['source_id']}", data={
        "github_source_id": world.sources[REPO], "issue_types": [], "status_on_start": "진행 중",
        "status_on_review": "리뷰중", "status_on_done": "종료", "followup_issue_type": "작업", "enabled": "on"})
    assert saved.status_code == 303, saved.text[:500]
    (settings,) = q(world, "SELECT status_on_start, status_on_review, status_on_done, followup_issue_type, enabled"
                           " FROM jira_projects")
    assert tuple(settings) == ("진행 중", "리뷰중", "종료", "작업", 1)
    screen = page(world, "/repos")
    assert "shopco.atlassian.net" in screen and "김개발" in screen
    # 연결 화면·확인은 게이트웨이로만 부른다 — 사이트에는 tenant_info 한 번
    assert [p for _, p, _ in jira.requests if not p.startswith("/ex/jira/")] == ["/_edge/tenant_info"]


def test_03_synced_issue_waits_until_it_is_delegated_then_jira_moves_to_in_progress(world):
    jira = world.ctx["jira"]
    jira.add("쿠폰 할인 후 청구서 번호 자릿수", "번호가 5자리로 채워지지 않습니다. [scenario:invoice]")
    report = world.worker.tick()
    assert report.jira_projects_synced == 1 and report.issues_created == 1 and report.tasks_started == 0
    one = work(world, 1)
    assert (one["source_type"], one["source_key"], one["source_url"], one["source_state"]) == (
        "jira", "SHOP-12", f"{SITE}/browse/SHOP-12", "대기")
    assert one["status"] == "새로 들어옴" and one["kind"] == "bug_fix"
    for _ in range(2):  # 맡기기 전에는 착수하지 않는다
        world.worker.tick()
    (fix,) = tasks_of(world, 1)
    assert execs(world, fix["task_id"]) == []
    panel = panel_of(page(world, "/tasks", open="RUN-1"), "RUN-1")
    assert f'href="{SITE}/browse/SHOP-12"' in panel
    assert '<span class="source-badge" data-source="jira">Jira</span>' in page(world, "/tasks")

    assign(world, "RUN-1")
    world.tasks["SHOP-12"] = fix["task_id"]
    assert len(execs(world, fix["task_id"])) == 1
    drive(world, lambda: jira.status_of("SHOP-12") == "진행 중", "Jira 진행 중")
    assert deliveries(world, 1)[0] == ("transition", "start", "진행 중", "delivered")
    (execution,) = execs(world, fix["task_id"])
    request = json.loads(execution["request_json"])["request"]
    assert request.splitlines()[0].startswith("# RUN-1 ") and "원본: SHOP-12" in request


def test_04_fix_review_draft_pr_then_merge_moves_jira_to_review_and_done(world):
    jira, fake = world.ctx["jira"], world.fake
    drive(world, lambda: fake.pulls, "초안 PR")
    (pr,) = fake.pulls.values()
    assert (pr["number"], pr["draft"], pr["head"]["ref"]) == (PR_NUMBER, True, "runloom/RUN-1")
    assert pr["title"].startswith("RUN-1 ") and "SHOP-12" not in pr["title"]  # PR 제목에 Jira 키 없음
    assert pr["body"].splitlines()[0] == f"원본: SHOP-12 — {SITE}/browse/SHOP-12" and "Fixes" not in pr["body"]
    (row,) = q(world, "SELECT repository_full_name, issue_number FROM task_pull_requests")
    assert tuple(row) == (REPO, None)

    drive(world, lambda: work(world, 1)["status"] == "PR · 검토", "PR · 검토")
    drive(world, lambda: jira.status_of("SHOP-12") == "리뷰중", "Jira 리뷰중")

    fake.merge(PR_NUMBER)
    one = drive(world, lambda: (w := work(world, 1))["status"] == "완료" and w, "PR 병합 → 완료")
    assert one["closed_at"] is not None
    drive(world, lambda: jira.status_of("SHOP-12") == "종료", "Jira 종료")
    assert deliveries(world, 1) == [("transition", "start", "진행 중", "delivered"),
                                    ("transition", "review", "리뷰중", "delivered"),
                                    ("transition", "done", "종료", "delivered")]
    flow = status_flow(world, one["work_item_id"])
    assert flow[0] == "새로 들어옴" and flow[-1] == "완료" and "PR · 검토" in flow
    # 세 순간은 한 번씩만 — 다시 돌려도 전환 POST 가 늘지 않는다
    posted = len(jira.posts("/transitions"))
    for _ in range(3):
        world.worker.tick()
    assert len(jira.posts("/transitions")) == posted == 3
    # 끝난 뒤 Jira 가 종료(done)를 돌려줘도 업무는 그대로 완료
    assert work(world, 1)["status"] == "완료" and work(world, 1)["source_state"] == "종료"
    assert issue_state(world, "SHOP-12") == "closed"
    panel = panel_of(page(world, "/tasks", open="RUN-1"), "RUN-1")
    assert "Jira 상태 → 종료" in unescape(panel)


def test_05_jira_done_holds_the_next_stage_and_reopen_resumes_with_a_followup_issue(world):
    http, jira = world.http, world.ctx["jira"]
    # 후속 규칙을 새 업무(new_work)로 — 등록 데이터만 바꾼다(종류 이름 분기 없음)
    rules = q(world, "SELECT rule_id, rule_json FROM succession_rules")
    (builtin,) = [r for r in rules if json.loads(r["rule_json"])["to_kind"] == "code_review"]
    rule = json.loads(builtin["rule_json"])
    assert http.post(f"/rules/{builtin['rule_id']}/delete").status_code == 303
    created = http.post("/rules", data={**{k: rule[k] for k in ("from_kind", "to_kind")},
                                        "on_outcomes": rule["on_outcomes"], "handoff_kinds": rule["handoff_kinds"],
                                        "placement": "new_work"})
    assert created.status_code == 303, created.text[:500]

    jira.add("환불 수수료 계산", "수수료는 3% 입니다. [scenario:refund]", issue_type="10002")
    world.worker.tick()
    two = work(world, 2)
    assert (two["source_key"], two["status"]) == ("SHOP-13", "새로 들어옴")
    assign(world, "RUN-2")
    (fix,) = tasks_of(world, 2)
    # 사람이 Jira 에서 바로 종료로 옮긴다 — 다음 바퀴 가져오기가 원본 닫힘으로 본다
    jira.move("SHOP-13", "종료")
    drive(world, lambda: issue_state(world, "SHOP-13") == "closed", "원본 닫힘")
    (execution,) = execs(world, fix["task_id"])

    def held():
        verdict = q(world, "SELECT 1 FROM task_verdicts WHERE execution_id = ?", execution["execution_id"])
        task = q(world, "SELECT status_reason FROM tasks WHERE task_id = ?", fix["task_id"])[0]
        return verdict and task["status_reason"] == "원본 이슈 닫힘 — 재오픈 시 재평가"

    drive(world, held, "수정 결과 뒤 원본 닫힘 대기")
    for _ in range(2):
        world.worker.tick()
    assert q(world, "SELECT COUNT(*) FROM work_items")[0][0] == 2  # 검토(새 업무)가 생기지 않았다
    assert len(tasks_of(world, 2)) == 1 and len(execs(world, fix["task_id"])) == 1  # 도는 실행은 끊지 않았다
    # 진행 중 순간은 Jira 가 이미 완료 범주라 건너뛰었다(옮기지 않음)
    assert deliveries(world, 2) == [("transition", "start", "진행 중", "skipped")]
    assert jira.status_of("SHOP-13") == "종료"

    # 다시 열면 이어간다 — 검토가 새 업무 RUN-3 으로 생기고, Jira 에 후속 이슈 1건
    jira.move("SHOP-13", "대기")
    three = drive(world, lambda: (rows := q(world, "SELECT * FROM work_items WHERE key_number = 3")) and rows[0],
                  "검토 새 업무")
    assert (three["source_type"], three["kind"]) == ("jira", "code_review")
    followup = drive(world, lambda: (w := work(world, 3))["source_key"] and w, "후속 이슈 생성")
    created_issue = jira.by_key(followup["source_key"])
    assert followup["source_key"] == "SHOP-14" and followup["source_url"] == f"{SITE}/browse/SHOP-14"
    assert created_issue["labels"] == ["runloom", "runloom-RUN-3"] and created_issue["issuetype"] == "10002"
    assert created_issue["summary"] == followup["title"]
    assert jira.links == [("Relates", jira.by_key("SHOP-13")["id"], created_issue["id"])]
    assert len(jira.posts("/rest/api/3/issue")) == 1
    # 다음 동기화가 그 이슈를 받아도 같은 업무 — 업무 수 그대로
    for _ in range(3):
        world.worker.tick()
    assert q(world, "SELECT COUNT(*) FROM work_items")[0][0] == 3
    (snapshot,) = q(world, "SELECT issue_key, task_id FROM jira_issues WHERE issue_id = ?", created_issue["id"])
    assert snapshot["issue_key"] == "SHOP-14" and snapshot["task_id"] in {t["task_id"] for t in tasks_of(world, 3)}
    assert len(jira.posts("/rest/api/3/issue")) == 1
    assert "후속 이슈 만들기 → SHOP-14" in unescape(panel_of(page(world, "/tasks", open="RUN-3"), "RUN-3"))


def test_06_the_jira_token_stays_in_the_secret_file(world):
    jira = world.ctx["jira"]
    assert all(ok for method, path, ok in jira.requests if path != "/_edge/tenant_info")
    conn = world.conn()
    try:
        dump = "\n".join(conn.iterdump())
    finally:
        conn.close()
    assert JIRA_TOKEN not in dump and EMAIL in dump
    for path in (world.workdir / "logs").glob("*.log"):
        assert JIRA_TOKEN not in path.read_text(encoding="utf-8", errors="replace"), path.name
    for path in ("/repos", "/tasks", "/tasks?open=RUN-1"):
        assert JIRA_TOKEN not in page(world, path)
    (secret,) = (world.workdir / "central" / "secrets").glob(JIRA_API_TOKEN)
    assert secret.stat().st_mode & 0o777 == 0o600


def test_v13_copy_upgrades_to_v14_and_takes_jira_issues(tmp_path):
    """v13 사본(업무·GitHub 소스·매핑 행) → 앱 시작이 v14 로 올림 → 그 DB 에서 Jira 연결·프로젝트·가져오기."""
    from fastapi.testclient import TestClient

    from tests.workflow.adapters.test_db import _v13_db
    from tests.workflow.adapters.test_repo import _source
    from workflow.adapters import repo
    from workflow.adapters.jira_client import HttpJiraClient
    from workflow.server import jira_connect
    from workflow.server.jira_sync import sync_project

    db_path = tmp_path / "central" / "db.sqlite"
    db_path.parent.mkdir()
    old = _v13_db(db_path)
    # fixture 의 소스 설정은 빈 칸이다 — phase 17 서버가 저장하는 모양으로 채운다(Jira 실행 설정의 사본 원본)
    old.execute("UPDATE github_sources SET config_json = ? WHERE source_id = 'ghs-00000001'",
                (_source(source_id="ghs-00000001", intake="all_open").model_dump_json(),))
    old.commit()
    before = {t: old.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
              for t in ("work_items", "github_sources", "field_mappings", "task_pull_requests", "tasks")}
    old.close()

    env = {"WORKFLOW_MODE": "selfhost", "WORKFLOW_DB_PATH": str(db_path),
           "WORKFLOW_ARTIFACT_DIR": str(tmp_path / "artifacts"), "WORKFLOW_SECRET_DIR": str(tmp_path / "secrets"),
           "SESSION_SECRET": "e2e-session-secret-" + "m" * 20, "OPERATOR_TOKEN": "e2e-operator-token-" + "m" * 20}
    with TestClient(create_app(load_settings(env))) as client:
        assert client.get("/healthz").status_code == 200

    conn = connect(db_path)
    try:
        assert conn.execute("SELECT version FROM schema_version").fetchone()[0] == SCHEMA_VERSION == 25  # 결과 뒤 판단 마이그레이션까지
        after = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in before}
        assert after == {**before, "field_mappings": before["field_mappings"] + 1}  # jira 기본 매핑 한 행
        assert [tuple(r) for r in conn.execute(
            "SELECT source_type, field, source_value, runloom_value FROM field_mappings"
            " WHERE session_id = 's1' AND source_type = 'jira'")] == [("jira", "kind", "*", "bug_fix")]
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []

        jira = FakeJiraCloud()
        transport = httpx.MockTransport(jira)
        facts = jira_connect.verify(SITE, EMAIL, JIRA_TOKEN, transport=transport)
        repo.save_jira_connection(conn, facts, session_id="s1", now=utc_now())
        client = HttpJiraClient(jira_connect.api_base_url(repo.get_jira_connection(conn, "s1")), EMAIL, JIRA_TOKEN,
                                transport=transport)
        source_id = repo.add_jira_project(conn, session_id="s1", ref=client.get_project(PROJECT_ID),
                                          github_source_id="ghs-00000001", start_mode="all_open",
                                          choices=client.project_choices(PROJECT_KEY), now=utc_now())
        jira.add("결제 버튼 오류", "결제가 두 번 됩니다.")
        result = sync_project(conn, client, source_id, utc_now())
        assert result.error is None and len(result.created) == 1
        (new,) = conn.execute("SELECT key_number, source_type, source_key, status FROM work_items"
                              " WHERE source_type = 'jira'").fetchall()
        assert tuple(new) == (8, "jira", "SHOP-12", "새로 들어옴")  # 키 번호는 옛 업무(3·7) 뒤로 이어진다
        assert conn.execute("SELECT COUNT(*) FROM work_items WHERE source_type = 'github'").fetchone()[0] == 1
    finally:
        conn.close()
