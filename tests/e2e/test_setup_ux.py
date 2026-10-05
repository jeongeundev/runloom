"""설정 UX e2e — 처음 온 관리자가 화면이 주는 링크·폼만 따라 준비를 끝낸다 (phase 23 step 10, ADR-0028,
ARCHITECTURE "설정 UX — phase 23").

`test_next_step_cycle` 의 구성(가짜 GitHub App + bare origin, uvicorn 스레드의 중앙 API, 테스트가 돌리는 워커, PATH 앞 가짜
도구의 러너)을 다시 쓴다. 주소는 손으로 만들지 않는다 — 사이드바·버튼의 href 와 폼의 action·필드를 HTML 에서 읽어 따라간다
(GitHub 쪽 왕복과 러너 설치 명령만 밖에서 흉내 낸다). JSON 폼(`data-json-action`)은 `base.html` 스크립트가 만드는 본문을
그대로 만들어 보낸다.

흐름(관리자 R):
1. 첫 설정 → `저장소` → [GitHub 연결] → 카드 [러너 붙이기] → 러너 설치 명령 → 에이전트 `billing`.
2. `팀` → 이메일 초대 2건(N·J) → J 링크는 놓친 셈 치고 [링크 다시 만들기] → 옛 링크 거부, 새 링크 가입 → 둘 다 멤버.
3. `설정` → `업무 종류·규칙` → 종류 `장애 조사`(맡을 에이전트 = billing) → billing 이 새 종류 후보.
4. `팀` → 담당 범위(`kube_proxy` · 조사 · N · J · billing).
5. `저장소` → 카드의 판단 에이전트 = billing → 저장소 설정에 반영.
6. 팀·저장소·설정(모든 탭)·업무 목록 — 내부 ID 는 "자세히" 밖에 없고, 표는 가로 스크롤 컨테이너 안.
7. 옛 주소 → 새 주소 303.
마지막 함수는 따로: v24 사본 → 앱 시작이 v25 로 올림 → 열린 옛 초대가 "이메일 없음(옛 초대)" 로 보이고 다시 만들기·취소.

`WORKFLOW_E2E=1` 일 때만 돈다. 실제 GitHub·모델 호출 없음 — 네트워크는 127.0.0.1 뿐이다.
테스트 함수는 번호 순으로 이어지며 앞 단계의 상태(`world`)를 쓴다.
"""

import hashlib
import json
import logging
import os
import re
import sys
import threading
import time
from dataclasses import dataclass, field
from html import unescape
from html.parser import HTMLParser
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
    gh_issue,
    log_in,
    make_repo,
    q,
    serve,
)
from tests.e2e.test_real_repo import FakeGitHubPulls, install_fake_codex, make_worker
from tests.workflow.server.test_web_connect import INTERNAL_ID, visible_text
from workflow.adapters import repo
from workflow.adapters.db import SCHEMA_VERSION, connect
from workflow.contracts.v1 import Capability
from workflow.domain import team
from workflow.domain.selection import select_agent
from workflow.server import work_actions
from workflow.server import worker as worker_module
from workflow.server.app import create_app
from workflow.server.auth import SELFHOST_SESSION_ID
from workflow.server.settings import load_settings

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(os.environ.get("WORKFLOW_E2E") != "1", reason="WORKFLOW_E2E=1 일 때만"),
]

REGISTRATION = "billing"
N = {"email": "n@example.com", "name": "남조사", "password": "e2e-member-password-n"}
J = {"email": "j@example.com", "name": "정판단", "password": "e2e-member-password-j"}
KIND_LABEL = "장애 조사"
INSTRUCTIONS = "요청 본문과 추가 질문·답변만 근거로 원인 후보를 정리한다."
OUTCOMES = "cause_found, needs_information, unresolved"
SIDEBAR = ["업무", "받은·보낸 요청", "모니터링", "팀", "저장소", "설정"]


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    workdir = tmp_path_factory.mktemp("setup-ux")
    (workdir / "logs").mkdir()

    bare = workdir / "remote" / "billing.git"
    bare.parent.mkdir(parents=True)
    _git(bare.parent, "init", "-q", "--bare", "-b", "main", str(bare))
    original = workdir / "repos" / "billing"
    base = make_repo(original, BILLING_FILES)
    _git(original, "remote", "add", "origin", "git@github.com:acme/billing.git")
    _git(original, "config", f"url.{bare}.insteadOf", "git@github.com:acme/billing.git")
    _git(original, "push", "-q", "origin", "main")

    fake = FakeGitHubPulls(bare)
    fake.add(REPO, gh_issue(REPO, 1, "kube-proxy 가 LXC 에서 시작되지 않음", labels=(),
                            updated_at="2026-09-10T01:00:00Z", body="업그레이드 뒤 kube-proxy 가 멈춥니다."))
    github_server = serve(fake)

    port = _free_port()
    inherited = {k: v for k, v in os.environ.items() if not k.startswith(DROP_PREFIXES)}
    central_env = {
        **inherited,
        "WORKFLOW_MODE": "selfhost",
        "WORKFLOW_DB_PATH": str(workdir / "central" / "db.sqlite"),
        "WORKFLOW_ARTIFACT_DIR": str(workdir / "central" / "artifacts"),
        "WORKFLOW_SECRET_DIR": str(workdir / "central" / "secrets"),
        "SESSION_SECRET": "e2e-session-secret-" + "x" * 20,
        "OPERATOR_TOKEN": "e2e-operator-token-" + "x" * 20,
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
    clients: list[httpx.Client] = []
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(worker_module, "GITHUB_SYNC_INTERVAL_SECONDS", 0)
        try:
            api_thread.start()
            deadline = time.monotonic() + 30
            while not api.started:
                assert time.monotonic() < deadline and api_thread.is_alive(), "중앙 API 가 뜨지 않음"
                time.sleep(0.1)
            for _ in range(3):  # 관리자·N·J — 쿠키 병이 따로다
                clients.append(httpx.Client(base_url=world.central_url, follow_redirects=False, timeout=10.0,
                                            headers={"Origin": world.central_url}))
            world.http = clients[0]
            world.ctx.update(n=clients[1], j=clients[2])
            yield world
        finally:
            for client in clients:
                client.close()
            world.stop()
            api.should_exit = True
            api_thread.join(10)
            github_server.shutdown()
            root.removeHandler(log_handler)
            root.setLevel(old_level)
            log_handler.close()


# --- 화면 읽기 — 링크·폼 -------------------------------------------------------------------


_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}


@dataclass
class Form:
    """화면의 폼 하나 — 필드는 화면에 있는 순서대로(`name`·`type`·`value`·`checked`·`disabled`·`label`·`options`)."""
    attrs: dict
    fields: list = field(default_factory=list)
    text: list = field(default_factory=list)  # 폼 안 글자

    @property
    def action(self) -> str:
        return unescape(self.attrs.get("action") or self.attrs.get("data-json-action") or "")

    def field_named(self, name: str) -> dict:
        (found,) = [f for f in self.fields if f.get("name") == name]
        return found

    def option(self, name: str, label: str) -> str:
        """select `name` 에서 화면 글자가 `label` 인 옵션의 값."""
        (value,) = [o["value"] for o in self.field_named(name)["options"] if o["text"].strip() == label]
        return value

    def checkbox(self, name: str, label_prefix: str) -> dict:
        (found,) = [f for f in self.fields if f.get("name") == name and f.get("type") == "checkbox"
                    and f["label"].strip().startswith(label_prefix)]
        return found

    def defaults(self) -> list[tuple[str, str]]:
        """브라우저가 기본으로 보내는 값 — 막힌 칸·안 고른 체크박스는 빠진다."""
        out = []
        for f in self.fields:
            name = f.get("name")
            if not name or "disabled" in f:
                continue
            if f["tag"] == "select":
                chosen = [o for o in f["options"] if "selected" in o] or f["options"][:1]
                out += [(name, o.get("value", o["text"])) for o in chosen]
            elif f.get("type") in ("checkbox", "radio"):
                if "checked" in f:
                    out.append((name, f.get("value", "on")))
            elif f.get("type") not in ("submit", "button"):
                out.append((name, f.get("value", "")))
        return out

    def submit(self, http: httpx.Client, page_path: str, **values) -> httpx.Response:
        """일반 폼 POST — 기본값 위에 `values`(리스트면 같은 이름 여럿)를 덮는다. action 이 없으면 지금 주소."""
        assert self.attrs.get("method", "get").lower() == "post", self.attrs
        data: dict[str, list[str]] = {}
        for k, v in self.defaults():
            if k not in values:
                data.setdefault(k, []).append(v)
        for k, v in values.items():
            data[k] = v if isinstance(v, list) else [v]
        return http.post(self.action or page_path, data=data)

    def json_body(self, **values) -> dict:
        """`base.html` 의 JSON 폼 스크립트가 만드는 본문 — `data-json-type` 대로 바꾼다."""
        body = {}
        for f in self.fields:
            name = f.get("name")
            if not name or "disabled" in f:
                continue
            kind = f.get("data-json-type", "str")
            if f["tag"] == "select":
                chosen = [o for o in f["options"] if "selected" in o] or f["options"][:1]
                value = chosen[0].get("value", chosen[0]["text"]) if chosen else ""
            else:
                value = f.get("value", "")
            value = values.get(name, value)
            if kind == "bool":
                body[name] = "checked" in f
            elif kind == "int":
                if str(value).strip():
                    body[name] = int(value)
            elif kind in ("list", "int-list"):
                items = [s for s in re.split(r"[\s,]+", value) if s]
                body[name] = [int(s) for s in items] if kind == "int-list" else items
            elif kind == "optional" and not str(value).strip():
                continue
            else:
                body[name] = value
        return body


class _Page(HTMLParser):
    """폼·표 조상·링크를 모은다."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.forms: list[Form] = []
        self.links: list[tuple[str, str, list[str]]] = []  # (href, 글자, 조상 class 들)
        self.tables_outside_wrap = 0
        self.tables = 0
        self._stack: list[tuple[str, str]] = []  # (tag, class)
        self._form: Form | None = None
        self._select = self._option = self._textarea = None
        self._label: list | None = None
        self._label_text: list[str] = []
        self._link: list | None = None

    def handle_starttag(self, tag, attrs):
        a = {k: (v if v is not None else "") for k, v in attrs}
        if tag == "table":
            self.tables += 1
            if not any("table-wrap" in cls.split() for _, cls in self._stack):
                self.tables_outside_wrap += 1
        if tag == "a" and "href" in a:
            self._link = [a["href"], [], [cls for _, cls in self._stack]]
        if tag == "form":
            self._form = Form(a)
            self.forms.append(self._form)
        elif self._form is not None:
            if tag == "input":
                f = {"tag": "input", "type": a.get("type", "text"), "label": "", **a}
                self._form.fields.append(f)
                if self._label is not None:
                    self._label.append(f)
            elif tag == "select":
                self._select = {"tag": "select", "options": [], **a}
                self._form.fields.append(self._select)
            elif tag == "option" and self._select is not None:
                self._option = {"text": "", **a}
                self._select["options"].append(self._option)
            elif tag == "textarea":
                self._textarea = {"tag": "textarea", "value": "", **a}
                self._form.fields.append(self._textarea)
            elif tag == "label":
                self._label, self._label_text = [], []
        if tag not in _VOID:
            self._stack.append((tag, a.get("class", "")))

    def handle_endtag(self, tag):
        if tag == "form":
            self._form = None
        elif tag == "select":
            self._select = None
        elif tag == "option":
            self._option = None
        elif tag == "textarea":
            self._textarea = None
        elif tag == "label" and self._label is not None:
            for f in self._label:
                f["label"] = " ".join("".join(self._label_text).split())
            self._label = None
        elif tag == "a" and self._link is not None:
            href, text, classes = self._link
            self.links.append((href, " ".join("".join(text).split()), classes))
            self._link = None
        for i in range(len(self._stack) - 1, -1, -1):
            if self._stack[i][0] == tag:
                del self._stack[i:]
                break

    def handle_data(self, data):
        if self._option is not None:
            self._option["text"] += data
        if self._textarea is not None:
            self._textarea["value"] += data
        if self._label is not None:
            self._label_text.append(data)
        if self._link is not None:
            self._link[1].append(data)
        if self._form is not None:
            self._form.text.append(data)


def parse(html: str) -> _Page:
    page = _Page()
    page.feed(html)
    page.close()
    return page


def _sha256(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def get(http: httpx.Client, path: str) -> str:
    response = http.get(path)
    assert response.status_code == 200, (path, response.status_code, response.text[:500])
    return response.text


def sidebar(html: str) -> dict[str, str]:
    """사이드바 메뉴 — 글자(배지 숫자 뺌) → href, 화면 순서."""
    aside = html.split('<aside class="sidebar">', 1)[1].split("</aside>", 1)[0]
    nav = aside.split('<nav class="nav">', 1)[1].split("</nav>", 1)[0]
    return {re.sub(r"\d+$", "", text).strip(): href for href, text, _ in parse(nav).links}


def link(html: str, text: str) -> str:
    """본문에서 글자가 `text` 인 링크 하나의 href."""
    (href,) = {h for h, t, _ in parse(html).links if t == text}
    return unescape(href)


def form_with(html: str, action: str) -> Form:
    (found,) = [f for f in parse(html).forms if f.action == action]
    return found


def section(html: str, marker: str) -> str:
    return html.split(marker, 1)[1].split("</section>", 1)[0]


def row_of(html: str, needle: str) -> str:
    (found,) = [r for r in re.findall(r"<tr\b.*?</tr>", html, re.S) if needle in r]
    return found


def invite_link(world: World, html: str) -> str:
    issued = html.split('data-issued="invite"', 1)[1].split("</div>", 1)[0]
    match = re.search(rf"{re.escape(world.central_url)}(/invite/[A-Za-z0-9_-]+)", issued)
    assert match, issued
    return match.group(1)


def settings_tabs(world: World) -> dict[str, str]:
    html = get(world.http, sidebar(get(world.http, "/tasks"))["설정"])
    tabs = html.split("data-settings-tabs", 1)[1].split("</nav>", 1)[0]
    return {text: unescape(href) for href, text, _ in parse("<nav" + tabs + "</nav>").links}


def card_of(html: str) -> str:
    return html.split("data-source-card=", 1)[1].split("</section>", 1)[0]


# --- 시나리오 ------------------------------------------------------------------------------


def test_01_first_setup_then_github_and_a_runner_from_the_repos_screen(world):
    http = world.http
    login = log_in(http, world.central_env["OPERATOR_TOKEN"])
    assert login.status_code == 303, login.text[:300]
    home = http.get(login.headers["location"])  # / → 업무 목록
    assert home.status_code == 303, home.text[:300]
    menu = sidebar(get(http, home.headers["location"]))
    assert [k for k in menu if k in SIDEBAR] == SIDEBAR and "연결" not in menu
    world.ctx["menu"] = menu
    (admin,) = q(world, "SELECT member_id, session_id FROM members WHERE role = 'admin'")
    world.ctx["session_id"] = admin["session_id"]

    # 저장소 → [GitHub 연결] → (GitHub 왕복) → 저장소 화면으로 돌아온다
    repos = get(http, menu["저장소"])
    new = get(http, link(repos, "GitHub 연결"))
    action = unescape(re.search(r'<form id="gh-manifest-form" method="post" action="([^"]+)"', new).group(1))
    callback = http.get("/operator/github/app/callback", params={
        "code": MANIFEST_CODE, "state": parse_qs(urlsplit(action).query)["state"][0]})
    assert callback.status_code == 303, callback.text[:500]
    state = parse_qs(urlsplit(callback.headers["location"]).query)["state"][0]
    setup = http.get("/operator/github/app/setup", params={
        "installation_id": INSTALLATION_ID, "setup_action": "install", "state": state})
    assert setup.status_code == 303 and setup.headers["location"] == menu["저장소"], setup.headers
    world.worker = make_worker(world)
    assert world.worker.tick().issues_created == 1

    # 카드 [러너 붙이기] → 설치 명령 → 러너 설치·실행
    repos = get(http, menu["저장소"])
    attach = next(f for f in parse(card_of(repos)).forms if "러너 붙이기" in "".join(f.text))
    issued = attach.submit(http, menu["저장소"])
    assert issued.status_code == 200, issued.text[:500]
    command = unescape(card_of(issued.text).split("data-runner-command", 1)[1])
    server, code = re.search(r"install-runner\.sh --server (\S+) --code (\S+) --repo <", command).groups()
    py = sys.executable
    world.once("connector-setup", [
        py, "-m", "workflow.connector", "setup", "--server", server, "--code", code, "--repo", str(world.billing),
        "--tool", "codex", "--verify", f"check={py} -m pytest -q -p no:cacheprovider",
    ])
    world.spawn("connector", [py, "-m", "workflow.connector", "run", "--adapter", "codex", "--claim-interval", "0.5",
                              "--heartbeat-interval", "1"], world.connector_env)
    deadline = time.monotonic() + 30
    while "data-runner-missing" in card_of(get(http, menu["저장소"])):
        assert time.monotonic() < deadline, world.log_tails()
        time.sleep(0.3)
    (agent,) = q(world, "SELECT agent_id, name, capabilities_json FROM agents WHERE local_registration_id = ?",
                 REGISTRATION)
    world.ctx.update(agent_id=agent["agent_id"], agent_name=agent["name"])
    (scope,) = {c["scope"]["repository_id"] for c in json.loads(agent["capabilities_json"])}
    world.ctx["repository_id"] = scope


def test_02_two_email_invites_a_reissued_link_and_both_join(world):
    http, menu = world.http, world.ctx["menu"]
    team_path = menu["팀"]
    team_html = get(http, team_path)
    invite_form = form_with(section(team_html, 'data-team-section="invites"'), "/team/invites")  # POST 경로 그대로

    links = {}
    for who in (N, J):
        issued = invite_form.submit(http, team_path, invitee_email=who["email"], invitee_name=who["name"])
        assert issued.status_code == 200, issued.text[:500]
        links[who["email"]] = invite_link(world, issued.text)
        assert who["email"] in issued.text.split('data-issued="invite"', 1)[1].split("</div>", 1)[0]
        assert "data-public-url-note" not in issued.text  # WORKFLOW_PUBLIC_URL 이 있다
        invite_form = form_with(section(issued.text, 'data-team-section="invites"'), "/team/invites")

    # J 링크는 놓쳤다 — 그 줄의 [링크 다시 만들기]
    invites = section(get(http, team_path), 'data-team-section="invites"')
    j_row = row_of(invites, J["email"])
    assert J["name"] in j_row and "멤버" in j_row
    (reissue,) = [f for f in parse(j_row).forms if f.action.endswith("/reissue")]
    assert re.fullmatch(r"/team/invites/inv-[0-9a-f]+/reissue", reissue.action)
    again = reissue.submit(http, team_path)
    assert again.status_code == 200, again.text[:500]
    old, new = links[J["email"]], invite_link(world, again.text)
    assert old != new

    j = world.ctx["j"]
    assert j.get(old).status_code == 404
    assert j.post(old, data={"email": J["email"], "display_name": J["name"], "password": J["password"]}
                  ).status_code == 404
    for client, who, path in ((j, J, new), (world.ctx["n"], N, links[N["email"]])):
        signup = get(client, path)
        (form,) = parse(signup).forms
        email = form.field_named("email")
        assert email["value"] == who["email"] and "readonly" in email  # 초대 이메일로 고정
        assert form.field_named("display_name")["value"] == who["name"]
        joined = form.submit(client, path, password=who["password"])
        assert joined.status_code == 303, joined.text[:500]

    team_html = get(http, team_path)
    members = section(team_html, 'data-team-section="members"')
    for who in (N, J):
        assert who["name"] in row_of(members, who["email"])
    assert "쓰지 않은 초대가 없습니다." in section(team_html, 'data-team-section="invites"')
    rows = q(world, "SELECT member_id, email FROM members WHERE email IN (?, ?)", N["email"], J["email"])
    world.ctx["members"] = {r["email"]: r["member_id"] for r in rows}
    assert len(world.ctx["members"]) == 2


def test_03_a_kind_with_the_runner_agent_makes_it_a_candidate(world):
    http, agent_name = world.http, world.ctx["agent_name"]
    tabs = settings_tabs(world)
    assert list(tabs) == ["업무 종류·규칙", "판단", "알림", "n8n 입구", "고급"]
    kinds_path = tabs["업무 종류·규칙"]
    form = form_with(get(http, kinds_path), "/kinds")  # POST 경로 그대로
    box = form.checkbox("agent_ids", agent_name)
    assert box["label"].startswith(f"{agent_name} · {REPO} · 운영자의 Mac"), box["label"]
    assert form.field_named("outcomes")["value"] == "done, needs_information"
    created = form.submit(http, kinds_path, label=KIND_LABEL, instructions=INSTRUCTIONS, outcomes=OUTCOMES,
                          agent_ids=[box["value"]])
    assert created.status_code == 303 and created.headers["location"] == kinds_path, created.text[:500]

    kinds_html = get(http, created.headers["location"])
    (card,) = [c for c in kinds_html.split('<div class="card kind-card">')[1:]
               if f'<div class="name">{KIND_LABEL}' in c]
    assert f"<dd>{agent_name}</dd>" in card
    kind = re.search(r"<summary>자세히</summary>(k_[0-9a-f]{6}) · (k_[0-9a-f]{6}) · repository_id</details>", card)
    assert kind and kind.group(1) == kind.group(2), card

    conn = world.conn()
    try:
        session_id = world.ctx["session_id"]
        spec = next(k for k in repo.list_kinds(conn, session_id) if k.label == KIND_LABEL)
        required = Capability(code=spec.capability_code, scope={spec.scope_key: world.ctx["repository_id"]})
        record = select_agent("probe", required, work_actions.candidates(conn, session_id), mode="auto")
        assert record.selected_agent_id == world.ctx["agent_id"]
    finally:
        conn.close()
    agent_row = row_of(section(get(http, world.ctx["menu"]["팀"]), 'data-team-section="agents"'),
                       f'data-agent="{world.ctx["agent_id"]}"')
    assert KIND_LABEL in agent_row


def test_04_a_responsibility_row_with_names_only(world):
    http, team_path = world.http, world.ctx["menu"]["팀"]
    form = form_with(section(get(http, team_path), 'data-team-section="responsibilities"'), "/responsibilities/add")
    added = form.submit(http, team_path, system_id="kube_proxy", request_kind=form.option("request_kind", "조사"),
                        recipient_member_id=form.option("recipient_member_id", N["name"]),
                        judgment_member_id=form.option("judgment_member_id", J["name"]),
                        agent_id=form.option("agent_id", world.ctx["agent_name"]))
    assert added.status_code == 303 and added.headers["location"] == team_path, added.text[:500]
    table = section(get(http, team_path), 'data-team-section="responsibilities"')
    cells = [visible_text(c).strip() for c in re.findall(r"<td>(.*?)</td>", row_of(table, "kube_proxy"), re.S)]
    assert cells[:6] == ["kube_proxy", "조사", N["name"], J["name"], world.ctx["agent_name"], "선택 가능"], cells
    (saved,) = http.get("/responsibilities").json()["entries"]
    assert saved == {**saved, "system_id": "kube_proxy", "request_kind": "investigation",
                     "recipient_member_id": world.ctx["members"][N["email"]],
                     "judgment_member_id": world.ctx["members"][J["email"]], "agent_id": world.ctx["agent_id"]}


def test_05_the_triage_agent_from_the_repo_card(world):
    http, repos_path = world.http, world.ctx["menu"]["저장소"]
    card = card_of(get(http, repos_path))
    (form,) = [f for f in parse(card).forms if any(x.get("name") == "triage_agent_id" for x in f.fields)]
    body = form.json_body(triage_agent_id=form.option("triage_agent_id", world.ctx["agent_name"]))
    saved = http.put(form.action, json=body)
    assert saved.status_code == 200, saved.text[:500]

    card = card_of(get(http, repos_path))
    (form,) = [f for f in parse(card).forms if any(x.get("name") == "triage_agent_id" for x in f.fields)]
    (chosen,) = [o for o in form.field_named("triage_agent_id")["options"] if "selected" in o]
    assert chosen["text"] == world.ctx["agent_name"]
    assert f"<li><span>판단 에이전트</span><span>{world.ctx['agent_name']} (설정)</span></li>" in card
    (source,) = http.get("/github/sources").json()["sources"]
    assert source["triage_agent_id"] == world.ctx["agent_id"]


def screens(world: World) -> dict[str, str]:
    """팀·저장소·설정(보이는 탭 전부)·업무 목록 — 화면 링크로 연다."""
    menu, http = world.ctx["menu"], world.http
    pages = {name: get(http, menu[name]) for name in ("팀", "저장소", "업무")}
    for name, href in settings_tabs(world).items():
        pages[f"설정 › {name}"] = get(http, href)
    return pages


def test_06_internal_ids_only_inside_details_and_tables_scroll_inside_a_wrap(world):
    pages = screens(world)
    assert len(pages) == 8
    for name, html in pages.items():
        page = parse(html)
        assert page.tables_outside_wrap == 0, (name, page.tables)
        if name == "설정 › 고급":  # 수동 등록·연결 코드 — 노출 단정 예외(ARCHITECTURE 설정 화면 표)
            assert world.ctx["agent_id"] in visible_text(html)
            continue
        found = INTERNAL_ID.search(visible_text(html))
        assert found is None, (name, found and visible_text(html)[max(0, found.start() - 80):found.end() + 80])
    # 내부 값은 실제로 화면에 있다 — "자세히" 안에
    assert world.ctx["agent_id"] in pages["팀"] and world.ctx["agent_id"] not in visible_text(pages["팀"])
    assert pages["팀"].count("<table") >= 3 and pages["업무"].count("<table") >= 1


def test_07_old_addresses_redirect_to_the_new_screens(world):
    http = world.http
    redirects = {
        "/connect": "/repos", "/connect?tab=sources": "/repos", "/connect?tab=team": "/team",
        "/connect?tab=kinds": "/settings?tab=kinds", "/connect?tab=triage": "/settings?tab=triage",
        "/connect?tab=triage&version=1": "/settings?tab=triage&version=1",
        "/connect?tab=notify": "/settings?tab=notify", "/connect?tab=advanced": "/settings?tab=advanced",
        "/connect?tab=nope": "/repos",
        "/sources": "/settings?tab=inbound", "/operator/github": "/repos", "/operator": "/settings?tab=advanced",
        "/operator/notifications": "/settings?tab=notify", "/agents": "/team", "/kinds": "/settings?tab=kinds",
    }
    for old, new in redirects.items():
        moved = http.get(old)
        assert (moved.status_code, moved.headers.get("location")) == (303, new), old
        assert http.get(new).status_code == 200, new


# --- v24 → v25 업그레이드 -------------------------------------------------------------------


def test_v24_copy_upgrades_to_v25_and_an_open_old_invite_can_be_reissued_and_revoked(tmp_path):
    """phase 22 서버가 남긴 v24 사본(관리자·멤버·열린 옛 초대 둘·사용된 초대 하나) → 앱 시작이 v25 로 올림 →
    팀 화면에 "이메일 없음(옛 초대)" → [링크 다시 만들기] 로 새 링크(옛 링크 무효, 이메일 입력해 가입) → [취소]."""
    from fastapi.testclient import TestClient

    from tests.workflow.adapters.test_db import V24_SCHEMA

    db_path = tmp_path / "central" / "db.sqlite"
    db_path.parent.mkdir()
    now, later = "2026-10-01T00:00:00Z", "2099-01-01T00:00:00Z"
    old = connect(db_path)
    old.executescript(V24_SCHEMA)
    old.execute("INSERT INTO schema_version (version) VALUES (24)")
    old.execute("INSERT INTO sessions (session_id, created_at) VALUES (?, ?)", (SELFHOST_SESSION_ID, now))
    old.execute("INSERT INTO members (member_id, session_id, display_name, role, created_at, email, password_hash)"
                " VALUES ('mem-00000001', ?, '운영자', 'admin', ?, 'r@example.com', ?)",
                (SELFHOST_SESSION_ID, now, team.hash_password("e2e-admin-password")))
    old.execute("INSERT INTO members (member_id, session_id, display_name, role, created_at, email, password_hash)"
                " VALUES ('mem-00000002', ?, '김멤버', 'member', ?, 'kim@example.com', NULL)",
                (SELFHOST_SESSION_ID, now))
    invite = ("INSERT INTO member_invites (invite_id, session_id, purpose, role, member_id, token_sha256,"
              " created_by_member_id, created_at, expires_at, used_at, used_by_member_id) VALUES (?, ?, 'invite', ?,"
              " NULL, ?, 'mem-00000001', ?, ?, ?, ?)")
    old.execute(invite, ("inv-0000000000a1", SELFHOST_SESSION_ID, "member", _sha256("old-token-a1"),
                         now, later, None, None))
    old.execute(invite, ("inv-0000000000a2", SELFHOST_SESSION_ID, "admin", _sha256("old-token-a2"),
                         now, later, None, None))
    old.execute(invite, ("inv-0000000000a3", SELFHOST_SESSION_ID, "member", _sha256("old-token-a3"),
                         now, later, now, "mem-00000002"))
    old.commit()
    old.close()

    env = {"WORKFLOW_MODE": "selfhost", "WORKFLOW_DB_PATH": str(db_path),
           "WORKFLOW_ARTIFACT_DIR": str(tmp_path / "artifacts"), "WORKFLOW_SECRET_DIR": str(tmp_path / "secrets"),
           "SESSION_SECRET": "e2e-session-secret-" + "w" * 20, "OPERATOR_TOKEN": "e2e-operator-token-" + "w" * 20}
    with TestClient(create_app(load_settings(env)), base_url="http://testserver", follow_redirects=False,
                    headers={"Origin": "http://testserver"}) as client:
        assert client.post("/login", data={"email": "r@example.com", "password": "e2e-admin-password"}
                           ).status_code == 303
        invites = section(get(client, "/team"), 'data-team-section="invites"')
        rows = re.findall(r"<tr data-invite-id=.*?</tr>", invites, re.S)
        assert len(rows) == 2  # 사용된 초대는 목록에 없다
        for row in rows:
            assert "이메일 없음(옛 초대)" in row and "<td>—</td>" in row
            assert INTERNAL_ID.search(visible_text(row)) is None
        a1 = row_of(invites, 'data-invite-id="inv-0000000000a1"')
        (reissue,) = [f for f in parse(a1).forms if f.action.endswith("/reissue")]
        again = reissue.submit(client, "/team")
        assert again.status_code == 200, again.text[:500]
        issued = again.text.split('data-issued="invite"', 1)[1].split("</div>", 1)[0]
        new = re.search(r"http://testserver(/invite/[A-Za-z0-9_-]+)", issued).group(1)
        assert client.get("/invite/old-token-a1").status_code == 404

        a2 = row_of(section(again.text, 'data-team-section="invites"'), 'data-invite-id="inv-0000000000a2"')
        (revoke,) = [f for f in parse(a2).forms if f.action.endswith("/revoke")]
        assert revoke.submit(client, "/team").status_code == 303
        assert client.get("/invite/old-token-a2").status_code == 404

        signup = get(client, new)
        (form,) = parse(signup).forms
        assert form.field_named("email")["value"] == "" and "readonly" not in form.field_named("email")
        client.cookies.clear()
        joined = form.submit(client, new, email="old@example.com", display_name="옛초대", password="e2e-old-password")
        assert joined.status_code == 303, joined.text[:500]

    conn = connect(db_path)
    try:
        assert conn.execute("SELECT version FROM schema_version").fetchone()[0] == SCHEMA_VERSION == 25
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        rows = {r["invite_id"]: r for r in conn.execute("SELECT * FROM member_invites")}
        assert rows["inv-0000000000a1"]["used_at"] is not None and rows["inv-0000000000a1"]["invitee_email"] is None
        assert rows["inv-0000000000a2"]["revoked_at"] is not None
        assert rows["inv-0000000000a3"]["token_sha256"] == _sha256("old-token-a3")
        assert repo.find_member_by_email(conn, SELFHOST_SESSION_ID, "old@example.com")["role"] == "member"
    finally:
        conn.close()
