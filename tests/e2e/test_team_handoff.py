"""사람 사이 인계 e2e — 멤버 두 명·러너 두 대로 다른 멤버의 에이전트에게 맡기기 (phase 17 step 11, ADR-0023).

`test_team` 의 가짜 GitHub App(PR 경로 포함)·가짜 알림 수신·워커 연결을 다시 쓰고, 러너를 둘로 나눈다:
- **관리자 A(운영자)** — 첫 설정·GitHub 연결(가짜)·공용 알림 URL·재작업 상한 0. 자기 러너 `billing-a`.
- **멤버 B(김멤버)** — 초대로 가입, 개인 웹훅, B 가 발급한 연결 코드로 자기 러너 `billing-b`(소유자 = B).
두 러너는 각자 `WORKFLOW_CONNECTOR_HOME` 임시 폴더와 같은 bare origin 의 클론 하나씩을 쓴다(launchd 아님).

한 줄기:
1. A 가 RUN-1 을 B 의 에이전트(정책 `run`)에게 지시 메모와 함께 맡김 → B 러너가 수정·검토(수정 요청) →
   재작업 상한 0 이라 사람 요청 → A 의 내 차례(B 에게는 없음), B 에게 `delegated_to_you`.
2. B 가 정책을 `owner_approval` 로 → A 가 RUN-2 를 맡김 → 실행 없음·B 의 내 차례 → B 승인 → 실행.
   RUN-3 을 맡기면 B 가 거절 → 담당 없음 + A 에게 `delegation_declined`.
3. B 러너를 멈춤 → A 가 RUN-4 를 맡김 → `대기 · 김멤버의 러너 꺼짐` + B 알림 1회 → B 러너 재기동 → 실행.
4. RUN-4 는 검증 명령이 (플래그 파일로) 한 번 실패 → 검증 실패 요청 → [검증만 다시] → 가짜 도구가 다시 불리지 않고
   검증만 돌아 통과 → 검토 단계.
5. 목록 `?group=repo`·키 칸 `RUN-n` 먼저, 요청문에 업무 제목·지시 메모(가짜 도구가 받은 프롬프트를 파일로 남김).

v12 → v13 마이그레이션(행 보존·기본값)은 `tests/workflow/adapters/test_db.py` 가 fixture 로 본다 — 여기서는 생략.
`WORKFLOW_E2E=1` 일 때만 돈다. 실제 GitHub·Discord·모델 호출 없음 — 네트워크는 127.0.0.1 뿐이다.
테스트 함수는 번호 순으로 이어지며 앞 단계의 상태(`world`)를 쓴다.
"""

import json
import logging
import os
import re
import subprocess
import sys
import threading
import time
from html import unescape
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
import uvicorn

from tests.e2e.test_github_app import INSTALLATION_ID, MANIFEST_CODE, REPO
from tests.e2e.test_github_cycle import (
    BILLING_FILES,
    DROP_PREFIXES,
    E2E_ADMIN,
    ToFakeGitHub,
    World,
    _free_port,
    _git,
    drive,
    execs,
    gh_issue,
    log_in,
    make_repo,
    q,
    request_of,
    serve,
    verdict_checks,
)
from tests.e2e.test_real_repo import (
    FakeGitHubPulls,
    Receiver,
    fix_result,
    install_fake_codex,
    issue_task,
    make_worker,
    serve_receiver,
    source_card,
)
from tests.e2e.test_work_ui import groups, page, panel_of
from workflow.server import worker as worker_module
from workflow.server.app import create_app
from workflow.server.settings import load_settings

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(os.environ.get("WORKFLOW_E2E") != "1", reason="WORKFLOW_E2E=1 일 때만"),
]

MEMBER = {"email": "kim@example.com", "display_name": "김멤버", "password": "e2e-member-password"}
SHARED_PATH, PERSONAL_PATH = "/hooks/shared", "/hooks/kim"
REG_A, REG_B = "billing-a", "billing-b"  # 로컬 등록 이름 — 등록은 러너마다 달라야 한다
AGENT_NAME = "billing"  # 에이전트 이름 = 폴더 이름 — 두 러너 모두 같아 후보 줄의 소유자로 구분한다
NOTE = "쿠폰 계산만 고치고 다른 파일은 건드리지 마세요."
DECLINE_NOTE = "오늘은 Mac 을 못 씁니다"
OFFLINE_SECONDS = 4  # 러너 heartbeat 1초 — 멈춘 뒤 4초면 꺼짐

# 검증 프로필 — 플래그 파일이 있으면 실패, 없으면 pytest. 러너 로컬 등록에만 있고 중앙은 이 명령을 모른다
VERIFY_SCRIPT = (
    "import os\nimport subprocess\nimport sys\n\n"
    "if os.path.exists(__FLAG__):\n"
    "    print('검증 환경 고장 (e2e 플래그)')\n"
    "    sys.exit(1)\n"
    "sys.exit(subprocess.run([sys.executable, '-m', 'pytest', '-q', '-p', 'no:cacheprovider']).returncode)\n"
)
# 가짜 codex 가 받은 프롬프트를 한 파일씩 남긴다 — 요청문 확인·"도구가 다시 불리지 않음" 확인
PROMPT_LOG = (
    "prompt = sys.stdin.read()\n"
    "import time as _time\n"
    "with open(os.path.join(__PROMPTS__, f'{_time.time_ns()}.json'), 'w', encoding='utf-8') as _f:\n"
    "    json.dump({'sandbox': sandbox, 'prompt': prompt}, _f, ensure_ascii=False)\n"
)


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    workdir = tmp_path_factory.mktemp("team-handoff")
    (workdir / "logs").mkdir()

    bare = workdir / "remote" / "billing.git"
    bare.parent.mkdir(parents=True)
    _git(bare.parent, "init", "-q", "--bare", "-b", "main", str(bare))
    clones = {}
    for who in ("a", "b"):  # 두 멤버가 각자 Mac 에 둔 클론 — origin 은 같은 bare
        clone = workdir / "repos" / who / "billing"
        make_repo(clone, BILLING_FILES)
        _git(clone, "remote", "add", "origin", "git@github.com:acme/billing.git")
        _git(clone, "config", f"url.{bare}.insteadOf", "git@github.com:acme/billing.git")
        clones[who] = clone
    _git(clones["a"], "push", "-q", "origin", "main")
    _git(clones["b"], "fetch", "-q", "origin")
    _git(clones["b"], "reset", "-q", "--hard", "origin/main")  # 같은 기준 커밋

    fake = FakeGitHubPulls(bare)
    issues = ((1, "쿠폰 중복 차감", "coupon"), (2, "청구서 번호 자릿수", "invoice"),
              (3, "환불 수수료 계산", "refund"), (4, "환불 수수료 3%", "refund"))
    for number, title, scenario in issues:
        fake.add(REPO, gh_issue(REPO, number, title, labels=(), updated_at=f"2026-09-10T0{number}:00:00Z",
                                body=f"{title} 문제입니다. [scenario:{scenario}]"))
    github_server = serve(fake)
    receiver = Receiver()
    receiver_server = serve_receiver(receiver)
    hook_base = f"http://127.0.0.1:{receiver_server.server_address[1]}"

    port = _free_port()
    inherited = {k: v for k, v in os.environ.items() if not k.startswith(DROP_PREFIXES)}
    central_env = {
        **inherited,
        "WORKFLOW_MODE": "selfhost",
        "WORKFLOW_DB_PATH": str(workdir / "central" / "db.sqlite"),
        "WORKFLOW_ARTIFACT_DIR": str(workdir / "central" / "artifacts"),
        "WORKFLOW_SECRET_DIR": str(workdir / "central" / "secrets"),
        "SESSION_SECRET": "e2e-session-secret-" + "h" * 20,
        "OPERATOR_TOKEN": "e2e-operator-token-" + "h" * 20,
        "WORKFLOW_PUBLIC_URL": f"http://127.0.0.1:{port}",
        "WORKFLOW_LIMIT_HEARTBEAT_OFFLINE_SECONDS": str(OFFLINE_SECONDS),
    }
    fake_bin = workdir / "bin"
    install_fake_codex(fake_bin)
    prompts = workdir / "prompts"
    prompts.mkdir()
    codex = fake_bin / "codex"
    codex.write_text(codex.read_text().replace("prompt = sys.stdin.read()\n",
                                               PROMPT_LOG.replace("__PROMPTS__", repr(str(prompts))), 1))
    flag = workdir / "verify-broken"
    verify = workdir / "verify.py"
    verify.write_text(VERIFY_SCRIPT.replace("__FLAG__", repr(str(flag))), encoding="utf-8")

    def runner_env(who: str) -> dict:
        return {**inherited, "WORKFLOW_CONNECTOR_HOME": str(workdir / f"connector-{who}"),
                "PATH": f"{fake_bin}{os.pathsep}{inherited.get('PATH', '')}"}

    world = World(workdir=workdir, central_url=f"http://127.0.0.1:{port}", central_env=central_env,
                  connector_env=runner_env("a"), fake=fake, fake_port=github_server.server_address[1],
                  billing=clones["a"], shop=None, base={})
    world.ctx.update(bare=bare, receiver=receiver, clones=clones, prompts=prompts, flag=flag, verify=verify,
                     env={"a": runner_env("a"), "b": runner_env("b")},
                     shared_url=hook_base + SHARED_PATH, personal_url=hook_base + PERSONAL_PATH)

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
            for _ in range(2):  # A·B — 쿠키 병이 따로다
                clients.append(httpx.Client(base_url=world.central_url, follow_redirects=False, timeout=10.0,
                                            headers={"Origin": world.central_url}))
            world.http, world.ctx["member"] = clients
            yield world
        finally:
            for client in clients:
                client.close()
            world.stop()
            api.should_exit = True
            api_thread.join(10)
            github_server.shutdown()
            receiver_server.shutdown()
            root.removeHandler(log_handler)
            root.setLevel(old_level)
            log_handler.close()


# --- 도우미 --------------------------------------------------------------------------------


def member_http(world: World) -> httpx.Client:
    return world.ctx["member"]


def work(world: World, key_number: int):
    (row,) = q(world, "SELECT * FROM work_items WHERE key_number = ?", key_number)
    return row


def my_turn_keys(http: httpx.Client) -> set[str]:
    response = http.get("/tasks", params={"q": "my_turn"})
    assert response.status_code == 200, response.text[:500]
    return set(re.findall(r'<tr class="work-row" data-work-key="([^"]+)"', response.text))


def agent_choices(html: str, work_key: str) -> dict[str, str]:
    """패널 담당 후보(에이전트) — agent_id → 한 줄."""
    panel = panel_of(html, work_key)
    return {m.group(1): unescape(m.group(2)) for m in
            re.finditer(r'<option value="agent:([^"]+)"(?: selected)?>([^<]*)</option>', panel)}


def notes(world: World, event: str) -> list[tuple[str, str, str]]:
    """알림 행 — (경로, 받는 사람, 사건 키). 저장된 중복 키에서 경로 꼬리(`:shared`·`:personal:<멤버>`)를 뗀다."""
    rows = q(world, "SELECT channel, recipient_member_id, dedupe_key FROM notifications WHERE event = ?"
                    " ORDER BY channel, notification_id", event)
    return [(r["channel"], r["recipient_member_id"], re.sub(r":(shared|personal:[^:]+)$", "", r["dedupe_key"]))
            for r in rows]


def bodies_at(world: World, path: str, event: str) -> list[dict]:
    with world.ctx["receiver"].lock:
        return [body for p, body in world.ctx["receiver"].received if p == path and body.get("event") == event]


def prompts(world: World, work_key: str, sandbox: str = "workspace-write") -> list[str]:
    """가짜 codex 가 받은 프롬프트 중 이 업무의 것(시간순). 수정 = workspace-write, 검토 = read-only."""
    found = []
    for path in sorted(world.ctx["prompts"].glob("*.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        if record["sandbox"] == sandbox and f"# {work_key} " in record["prompt"]:
            found.append(record["prompt"])
    return found


def assign(http: httpx.Client, key: str, agent_id: str, note: str = "") -> None:
    response = http.post(f"/work/{key}/assignee", data={"assignee": f"agent:{agent_id}", "note": note})
    assert response.status_code == 303, response.text[:500]


def set_policy(http: httpx.Client, agent_id: str, policy: str) -> None:
    response = http.post(f"/agents/{agent_id}/delegation-policy", data={"policy": policy})
    assert (response.status_code, response.headers.get("location")) == (303, "/team"), response.text[:500]


def open_request(world: World, http: httpx.Client, task_id: str, code: str) -> dict | None:
    listed = http.get("/human-requests")
    assert listed.status_code == 200, listed.text[:500]
    return next((r for r in listed.json()["requests"] if r["task_id"] == task_id and r["code"] == code), None)


def respond(http: httpx.Client, request: dict, response_id: str, action: str, text: str = "") -> httpx.Response:
    return http.post(f"/human-requests/{request['request_id']}/responses", json={
        "response_id": response_id, "expected_revision": request["revision"], "action": action, "text": text})


def connector_of(world: World, who: str) -> str:
    return world.ctx[f"connector_{who}"]


def start_runner(world: World, who: str, name: str) -> None:
    world.spawn(name, [sys.executable, "-m", "workflow.connector", "run", "--adapter", "codex",
                       "--claim-interval", "0.5", "--heartbeat-interval", "1"], world.ctx["env"][who])


def setup_runner(world: World, http: httpx.Client, who: str, registration: str) -> str:
    """카드 [러너 붙이기] → 화면 명령의 서버·코드로 `connector setup`(그 사람의 러너 홈·클론). 반환은 Agent id."""
    issued = http.post(f"/operator/github/sources/{world.sources[REPO]}/runner")
    assert issued.status_code == 200, issued.text[:500]
    command = unescape(source_card(world, issued.text).split("data-runner-command", 1)[1])
    server, code = re.search(r"install-runner\.sh --server (\S+) --code (\S+) --repo <", command).groups()
    assert server == world.central_url
    py = sys.executable
    done = subprocess.run([
        py, "-m", "workflow.connector", "setup", "--server", server, "--code", code,
        "--repo", str(world.ctx["clones"][who]), "--id", registration, "--tool", "codex",
        "--verify", f"check={py} {world.ctx['verify']}",
    ], env=world.ctx["env"][who], cwd=world.workdir, capture_output=True, text=True)
    (world.workdir / "logs" / f"setup-{who}.log").write_text(done.stdout + done.stderr)
    assert done.returncode == 0, done.stdout + done.stderr
    (agent,) = q(world, "SELECT agent_id, connector_id FROM agents WHERE local_registration_id = ?", registration)
    world.ctx[f"connector_{who}"] = agent["connector_id"]
    return agent["agent_id"]


def wait_until(world: World, until, what: str, timeout: float = 30.0):
    deadline = time.monotonic() + timeout
    while not (result := until()):
        assert time.monotonic() < deadline, f"{what}\n{world.log_tails()}"
        time.sleep(0.3)
    return result


# --- 시나리오 ------------------------------------------------------------------------------


def test_01_admin_connects_github_and_invites_a_member(world):
    http = world.http
    login = log_in(http, world.central_env["OPERATOR_TOKEN"])
    assert login.status_code == 303, login.text[:300]
    (admin,) = q(world, "SELECT member_id FROM members WHERE email = ?", E2E_ADMIN["email"])
    world.ctx["admin_id"] = admin["member_id"]

    new = http.get("/operator/github/app/new")
    action = unescape(re.search(r'<form id="gh-manifest-form" method="post" action="([^"]+)"', new.text).group(1))
    callback = http.get("/operator/github/app/callback", params={
        "code": MANIFEST_CODE, "state": parse_qs(urlsplit(action).query)["state"][0]})
    assert callback.status_code == 303, callback.text[:500]
    state = parse_qs(urlsplit(callback.headers["location"]).query)["state"][0]
    setup = http.get("/operator/github/app/setup", params={
        "installation_id": INSTALLATION_ID, "setup_action": "install", "state": state})
    assert setup.status_code == 303, setup.text[:500]
    (source,) = http.get("/github/sources").json()["sources"]
    world.sources[REPO] = source["source_id"]
    saved = http.post("/operator/notifications/webhook", data={"url": world.ctx["shared_url"]})
    assert saved.status_code in (200, 303), saved.text[:500]

    world.worker = make_worker(world)
    report = world.worker.tick()
    assert report.sync_errors == 0 and report.issues_created == 4 and report.tasks_started == 0

    issued = http.post("/team/invites", data={"role": "member"})
    link = re.search(rf"{re.escape(world.central_url)}/invite/([A-Za-z0-9_-]+)", issued.text)
    assert link, issued.text[:1000]
    member = member_http(world)
    assert member.post(f"/invite/{link.group(1)}", data=MEMBER).status_code == 303
    (row,) = q(world, "SELECT member_id FROM members WHERE email = ?", MEMBER["email"])
    world.ctx["member_id"] = row["member_id"]
    saved = member.post("/me/webhook", data={"url": world.ctx["personal_url"]})
    assert saved.status_code in (200, 303), saved.text[:500]


def test_02_each_person_attaches_their_own_runner(world):
    agent_a = setup_runner(world, world.http, "a", REG_A)
    agent_b = setup_runner(world, member_http(world), "b", REG_B)
    world.ctx.update(agent_a=agent_a, agent_b=agent_b)
    owners = {r["connector_id"]: r["owner_member_id"] for r in q(world, "SELECT * FROM connectors")}
    assert owners == {connector_of(world, "a"): world.ctx["admin_id"], connector_of(world, "b"): world.ctx["member_id"]}

    # 재작업 상한 0. 수정·검토 에이전트는 빈 칸 그대로 — 사람이 맡긴 에이전트가 수정하고, 그 러너가 검토한다
    (source,) = world.http.get("/github/sources").json()["sources"]
    assert (source["default_fix_agent_id"], source["review_agent_id"]) == (None, None)
    fields = ("repository_full_name", "intake", "workflow_repository_id", "label_filter", "selected_issue_numbers",
              "start_at", "fix_verification_profile_id", "review_agent_id", "run_mode", "trigger_label",
              "default_fix_agent_id", "enabled")
    updated = world.http.put(f"/github/sources/{source['source_id']}", json={
        **{k: source[k] for k in fields}, "max_rework_rounds": 0, "expected_revision": source["config_revision"]})
    assert updated.status_code == 200, updated.text[:500]

    start_runner(world, "a", "connector-a")
    start_runner(world, "b", "connector-b")
    wait_until(world, lambda: len(q(world, "SELECT 1 FROM connectors WHERE supported_kinds_json IS NOT NULL"
                                           " AND capabilities_json = '[\"after_result_triage\", \"verify_only\"]'")) == 2,
               "두 러너가 claim 으로 지원 종류·능력을 보고")


def test_03_admin_hands_run1_to_the_members_agent_and_the_turn_comes_back_to_the_admin(world):
    http, member = world.http, member_http(world)
    agent_a, agent_b = world.ctx["agent_a"], world.ctx["agent_b"]
    choices = agent_choices(page(world, "/tasks", open="RUN-1"), "RUN-1")
    assert choices == {agent_a: f"{AGENT_NAME} · {E2E_ADMIN['display_name']}의 Mac · 켜짐",
                       agent_b: f"{AGENT_NAME} · {MEMBER['display_name']}의 Mac · 켜짐"}

    assign(http, "RUN-1", agent_b, NOTE)
    task_id = issue_task(world, 1)
    world.tasks["1"] = task_id
    one = work(world, 1)
    assert (one["assignee_id"], one["requested_by_member_id"], one["handoff_note"]) == (
        agent_b, world.ctx["admin_id"], NOTE)

    request = drive(world, lambda: open_request(world, http, task_id, "rework_limit_reached"),
                    "B 러너 수정 → 같은 러너 검토 수정 요청 → 사람 요청")
    world.ctx["request_1"] = request
    (fix,) = execs(world, task_id)
    assert (fix["agent_id"], fix["assigned_connector_id"]) == (agent_b, connector_of(world, "b"))
    (review,) = q(world, "SELECT e.agent_id, e.assigned_connector_id FROM executions e JOIN tasks t"
                         " ON t.task_id = e.task_id WHERE t.predecessor_task_id = ?", task_id)
    assert tuple(review) == (agent_b, connector_of(world, "b"))  # 검토는 수정 결과 커밋이 있는 B 의 러너

    world.worker.tick()
    assert work(world, 1)["status"] == "내 차례"
    assert "RUN-1" in my_turn_keys(http) and "RUN-1" not in my_turn_keys(member)  # 맡긴 사람의 차례

    # B 에게는 "맡김" 정보 알림 — 공용·개인 한 번씩, 다시 돌아도 늘지 않는다
    expected = [("personal", world.ctx["member_id"], f"delegated_to_you:{task_id}:{agent_b}"),
                ("shared", world.ctx["member_id"], f"delegated_to_you:{task_id}:{agent_b}")]
    drive(world, lambda: bodies_at(world, PERSONAL_PATH, "delegated_to_you"), "B 개인 알림")
    world.worker.tick()
    assert notes(world, "delegated_to_you") == expected
    (personal,) = bodies_at(world, PERSONAL_PATH, "delegated_to_you")
    assert "맡김" in personal["content"] and E2E_ADMIN["display_name"] in personal["content"]


def test_04_owner_approval_waits_for_the_member_then_runs(world):
    http, member = world.http, member_http(world)
    agent_b = world.ctx["agent_b"]
    assert member.post(f"/agents/{agent_b}/delegation-policy", data={"policy": "nope"}).status_code == 422
    set_policy(member, agent_b, "owner_approval")  # B 가 자기 러너의 에이전트 정책을 바꾼다
    assert q(world, "SELECT delegation_policy FROM agents WHERE agent_id = ?", agent_b)[0][0] == "owner_approval"
    choices = agent_choices(page(world, "/tasks", open="RUN-2"), "RUN-2")
    assert choices[agent_b] == f"{AGENT_NAME} · {MEMBER['display_name']}의 Mac · 켜짐 · 승인 필요"

    assign(http, "RUN-2", agent_b)
    task_id = issue_task(world, 2)
    world.tasks["2"] = task_id
    for _ in range(2):
        world.worker.tick()
    assert execs(world, task_id) == []  # 승인 전에는 실행이 없다
    two = work(world, 2)
    assert (two["status"], two["status_reason"]) == (
        "내 차례", f"{E2E_ADMIN['display_name']} 가 맡김 · {MEMBER['display_name']} 승인 대기")
    assert "RUN-2" in my_turn_keys(member) and "RUN-2" not in my_turn_keys(http)  # 받는 사람 = 소유자
    assert notes(world, "delegated_to_you")[-1][2] != f"delegated_to_you:{task_id}:{agent_b}"  # 승인 요청 알림이 대신
    drive(world, lambda: [b for b in bodies_at(world, PERSONAL_PATH, "human_request")
                          if "승인 대기" in b["content"]], "B 에게 승인 요청 알림")

    request = open_request(world, member, task_id, "owner_approval")
    approved = respond(member, request, "approve-run2", "approve")
    assert approved.status_code == 200, approved.text[:500]
    (fix,) = drive(world, lambda: execs(world, task_id), "승인 뒤 워커가 착수")
    assert (fix["agent_id"], fix["assigned_connector_id"]) == (agent_b, connector_of(world, "b"))
    drive(world, lambda: verdict_checks(world, fix["execution_id"]) is not None, "B 러너 결과 판정")
    # 후속 검토는 같은 업무의 다음 단계 — 승인 범위가 업무라서 다시 묻지 않고 같은 러너가 검토한다(ADR-0023 결정 1)
    (review_task,) = drive(world, lambda: q(world, "SELECT task_id FROM tasks WHERE predecessor_task_id = ?", task_id),
                           "검토 단계")
    two = drive(world, lambda: (w := work(world, 2))["status"] == "PR · 검토" and w, "검토 → 초안 PR")
    assert two["status_reason"].startswith("PR 확인")
    assert open_request(world, member, review_task["task_id"], "owner_approval") is None
    assert q(world, "SELECT count(*) FROM human_requests WHERE task_id = ? AND code = 'owner_approval'",
             review_task["task_id"])[0][0] == 0


def test_05_owner_declines_run3_and_the_work_goes_back_unassigned(world):
    http, member = world.http, member_http(world)
    agent_b = world.ctx["agent_b"]
    assign(http, "RUN-3", agent_b)
    task_id = issue_task(world, 3)
    request = drive(world, lambda: open_request(world, member, task_id, "owner_approval"), "RUN-3 승인 요청")
    assert http.post(f"/human-requests/{request['request_id']}/responses", json={
        "response_id": "decline-long", "expected_revision": request["revision"], "action": "decline",
        "text": "가" * 2001}).status_code == 422

    declined = respond(member, request, "decline-run3", "decline", DECLINE_NOTE)
    assert declined.status_code == 200, declined.text[:500]
    world.worker.tick()
    three = work(world, 3)
    assert (three["assignee_type"], three["assignee_id"]) == (None, None)
    assert "RUN-3" in groups(page(world, "/tasks"))["none"][2]  # "담당 없음" 묶음
    assert execs(world, task_id) == [] and three["closed_at"] is None  # 업무는 닫지 않는다

    assert notes(world, "delegation_declined") == [
        ("shared", world.ctx["admin_id"], f"delegation_declined:{request['request_id']}")]
    shared = drive(world, lambda: bodies_at(world, SHARED_PATH, "delegation_declined"), "A 에게 거절 알림")
    assert f"{MEMBER['display_name']} 가 거절 — {DECLINE_NOTE}" in shared[0]["content"]
    for _ in range(2):  # 워커는 거절된 범위를 다시 묻지 않는다
        world.worker.tick()
    assert open_request(world, member, task_id, "owner_approval") is None and execs(world, task_id) == []


def test_06_offline_runner_waits_and_starts_when_it_comes_back(world):
    http = world.http
    agent_b = world.ctx["agent_b"]
    set_policy(http, agent_b, "run")  # 관리자도 바꿀 수 있다
    proc, log = world.procs.pop("connector-b")
    proc.terminate()
    proc.wait(10)
    log.close()
    wait_until(world, lambda: agent_choices(page(world, "/tasks", open="RUN-4"), "RUN-4")[agent_b].endswith("꺼짐"),
               "B 러너가 꺼짐으로 보임", timeout=OFFLINE_SECONDS * 5)

    world.ctx["flag"].write_text("고장", encoding="utf-8")  # 다음 줄기 — 첫 검증은 실패한다
    assign(http, "RUN-4", agent_b)
    task_id = issue_task(world, 4)
    world.tasks["4"] = task_id
    for _ in range(3):
        world.worker.tick()
    assert execs(world, task_id) == []
    four = work(world, 4)
    assert (four["status"], four["status_reason"]) == ("대기", f"{MEMBER['display_name']}의 러너 꺼짐 · 켜지면 시작")
    key = f"runner_offline:{four['work_item_id']}:{agent_b}"
    assert notes(world, "runner_offline_waiting") == [("personal", world.ctx["member_id"], key),
                                                      ("shared", world.ctx["member_id"], key)]  # 한 번만

    start_runner(world, "b", "connector-b-again")
    (fix,) = drive(world, lambda: execs(world, task_id), "러너가 다시 붙으면 워커가 착수")
    assert fix["assigned_connector_id"] == connector_of(world, "b")
    request = drive(world, lambda: open_request(world, http, task_id, "fix_verification_failed"), "검증 실패 요청")
    world.ctx["verify_failed"] = request
    assert verdict_checks(world, fix["execution_id"]) is not None
    assert len(notes(world, "runner_offline_waiting")) == 2  # 다시 켜져도 다시 보내지 않는다


def test_07_verify_only_reruns_the_check_without_calling_the_agent(world):
    http = world.http
    task_id, request = world.tasks["4"], world.ctx["verify_failed"]
    (first,) = execs(world, task_id)
    assert len(prompts(world, "RUN-4")) == 1
    world.ctx["flag"].unlink()  # 검증 환경을 고쳤다 — 결과 커밋은 그대로

    answered = respond(http, request, "reverify-run4", "reverify")
    assert answered.status_code == 200, answered.text[:500]
    runs = drive(world, lambda: len(r := execs(world, task_id)) == 2 and r, "검증만 다시 실행")
    again = runs[1]
    assert again["verify_only"] == 1 and again["start_key"] == f"reverify:{request['request_id']}"
    commit = fix_result(world, first).result_commit
    assert request_of(again).verify_only_commit == commit

    review = drive(world, lambda: q(world, "SELECT * FROM tasks WHERE predecessor_task_id = ?", task_id),
                   "검증 통과 → 검토 단계")
    assert verdict_checks(world, again["execution_id"]) and all(verdict_checks(world, again["execution_id"]).values())
    assert fix_result(world, again).result_commit == commit
    assert fix_result(world, again).summary.startswith("검증만 다시 — ")
    drive(world, lambda: q(world, "SELECT 1 FROM executions WHERE task_id = ?", review[0]["task_id"]), "검토 착수")
    assert len(prompts(world, "RUN-4")) == 1  # 수정 도구는 다시 불리지 않았다


def test_08_list_groups_by_repository_and_the_prompt_carries_the_handoff(world):
    listed = groups(page(world, "/tasks", group="repo"))
    assert list(listed) == ["repo:acme/billing"]
    label, count, keys = listed["repo:acme/billing"]
    assert (label, count, sorted(keys)) == ("acme/billing", 4, ["RUN-1", "RUN-2", "RUN-3", "RUN-4"])
    html = page(world, "/tasks")
    assert re.search(r'<span class="mono">RUN-1</span> <span class="muted small mono">billing#1</span>', html)
    assert "acme/billing#1" not in html
    filtered = groups(page(world, "/tasks", group="repo", repo="ACME/billing"))  # 대소문자 무시, 목록 표기로
    assert filtered["repo:acme/billing"][1] == 4

    (prompt,) = prompts(world, "RUN-1")
    assert prompt.count("# RUN-1 쿠폰 중복 차감") == 1
    assert f"## 맡긴 사람 지시 ({E2E_ADMIN['display_name']})\n{NOTE}" in prompt
    assert prompt.index("# RUN-1 쿠폰 중복 차감") < prompt.index(NOTE) < prompt.index("[scenario:coupon]")
    timeline = panel_of(page(world, "/tasks", open="RUN-1"), "RUN-1")
    assert f"지시 메모 · {E2E_ADMIN['display_name']} — {NOTE}" in unescape(timeline)
