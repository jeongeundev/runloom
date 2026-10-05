"""결과 뒤 판단 e2e — 가짜 GitHub App·PATH 앞 가짜 `claude` 러너로 RUN-26 장면을 끝까지 (phase 22 step 10, ADR-0027,
ARCHITECTURE "결과 뒤 판단 — phase 22").

`test_triage_cycle` 의 구성(가짜 GitHub App + PR 경로, bare origin, uvicorn 스레드의 중앙 API, 테스트가 돌리는 워커)에
가짜 알림 수신(`test_real_repo.Receiver`)과 두 번째 멤버(박조사 — 쿠키 병이 따로)를 더한다. 가짜 `claude` 는 `--json-schema`
의 모양으로 할 일을 고른다: `next_action` 칸이면 결과 뒤 판단(반환된 사내 요청이 있으면 재작업, 아니면 본문 표식
`[next:internal|human|new_work]`), `proceed` 면 접수 판단, `findings` 면 검토, 요청문 첫 줄 `# 업무 종류:` 면 사용자 정의
종류(조사), 그 밖은 수정(`[ask]` 이고 인계 파일 `input-…` 이 없는 첫 시도는 `needs_information`).

흐름:
1. 관리자 → GitHub 연결(가짜) → 박조사 초대 → 알림 웹훅(공용·박조사 개인) → claude 러너(능력 `after_result_triage`) →
   판단 Agent 칸 → 조사 종류 `incident_investigation` 등록 → 담당 범위 `kube_proxy/investigation → 박조사`.
2. RUN-1 접수 판단 → [제안대로 맡기기] → 수정 `needs_information` → 사람 요청 대신 결과 뒤 판단 → 사내 요청 제안.
3. [제안대로](요청자 = 관리자) → 사내 요청(`created_by_triage_id`) → 박조사에게 알림.
4. 박조사 수락 → 조사(사용자 정의 종류, 가짜 claude) → 검토 승인 → 반환.
5. 반환 → `request_returned` 판단 → 재작업 제안 → [제안대로] → 반환 요약을 지적으로 새 실행 → 검토 승인 → PR 병합 → 완료.
6. [무시] → 원래 사람 요청(`fix_needs_information`) 하나.
7. 규칙 없는 결과(수정 → 검토 규칙 삭제) → 새 업무 제안 → [제안대로] → `spawned_from` 새 업무.
8. 옛 러너(결과 뒤 판단 능력 없음) → 판단 없이 원래 사람 요청.
마지막 함수는 따로: v23 사본 → 앱 시작이 v24 로 올림.

`WORKFLOW_E2E=1` 일 때만 돈다. 실제 Claude·GitHub 호출 없음 — 네트워크는 127.0.0.1 과 MockTransport 뿐이다.
테스트 함수는 번호 순으로 이어지며 앞 단계의 상태(`world`)를 쓴다.
"""

import json
import logging
import os
import re
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
    FIXES,
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
    serve,
)
from tests.e2e.test_real_repo import FakeGitHubPulls, Receiver, make_worker, serve_receiver, source_card
from tests.e2e.test_work_ui import page
from workflow.adapters import repo
from workflow.adapters.db import SCHEMA_VERSION, connect
from workflow.server import worker as worker_module
from workflow.server.app import create_app
from workflow.server.settings import load_settings

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(os.environ.get("WORKFLOW_E2E") != "1", reason="WORKFLOW_E2E=1 일 때만"),
]

REGISTRATION = "billing"
MEMBER = {"email": "park@example.com", "display_name": "박조사", "password": "e2e-member-password"}
SHARED_PATH, PERSONAL_PATH = "/hooks/shared", "/hooks/park"
INVESTIGATION = "incident_investigation"
RETURNED_SUMMARY = "호스트 nf_conntrack_max 는 1310720 — LXC 안에서는 바꿀 수 없음"
PURPOSE = "LXC 호스트의 nf_conntrack_max 설정값을 확인해 주세요"
QUESTION = "청구서 번호 자릿수를 5자리로 고정해도 되나요?"
NEW_TITLE = "쿠폰 차감 회귀 테스트 보강"

_FAKE_CLAUDE = r'''#!/usr/bin/env python3
"""e2e 가짜 claude (결과 뒤 판단). 모델 없이 `--json-schema` 모양으로 할 일을 고른다. stdout 에 결과 JSON 한 덩어리."""
import json
import os
import re
import sys

FIXES = __FIXES__
PURPOSE = __PURPOSE__
QUESTION = __QUESTION__
NEW_TITLE = __NEW_TITLE__
RETURNED_SUMMARY = __RETURNED_SUMMARY__

args = sys.argv[1:]
schema = json.loads(args[args.index("--json-schema") + 1])
props = schema.get("properties", {})
prompt = sys.stdin.read()
cwd = os.getcwd()


def reply(structured):
    print(json.dumps({"type": "result", "subtype": "success", "is_error": False, "api_error_status": None,
                      "usage": {"input_tokens": 40, "output_tokens": 12}, "total_cost_usd": 0.01,
                      "result": json.dumps(structured, ensure_ascii=False), "structured_output": structured},
                     ensure_ascii=False))
    sys.exit(0)


if "next_action" in props:  # 결과 뒤 판단 — 기본 브랜치 체크아웃을 읽기만 한다
    current = re.search(r"^지금 단계: (\S+) ", prompt, re.M).group(1)
    if "## 반환된 사내 요청" in prompt:
        agent = re.search(r"지금 단계의 에이전트\(agent:([^)]+)\)", prompt).group(1)
        action = {"type": "stage", "kind": current, "assignee": {"type": "agent", "id": agent}, "rework": True}
    elif "[next:internal]" in prompt:
        system_id, request_kind, recipient = re.search(r"^- (\S+)/(\S+) → member:(\S+) ", prompt, re.M).groups()
        action = {"type": "internal_request", "system_id": system_id, "request_kind": request_kind,
                  "recipient_member_id": recipient, "purpose": PURPOSE}
    elif "[next:new_work]" in prompt:
        member = re.search(r"^- member:(\S+) ", prompt, re.M).group(1)
        action = {"type": "new_work", "kind": current, "title": NEW_TITLE,
                  "assignee": {"type": "member", "id": member}}
    else:
        action = {"type": "human", "question": QUESTION}
    reply({"proceed": "ready", "confidence": 0.82, "next_action": action,
           "reasons": [{"criterion": "clarity", "note": "이전 결과가 다음 행동을 정한다"}], "missing_information": []})

if "proceed" in props:  # 접수 판단 — 후보 줄의 첫 에이전트
    agents = re.findall(r"^- agent:(\S+) ", prompt, re.M)
    reply({"proceed": "ready", "confidence": 0.9, "proposed_kind": "bug_fix",
           "assignee": {"type": "agent", "id": agents[0]}, "predecessors": [],
           "reasons": [{"criterion": "scope", "note": "모듈 하나의 변경"}], "missing_information": []})

if "findings" in props:  # 검토 — 결과 커밋 체크아웃의 TODO(review):
    findings = []
    for root, dirs, files in os.walk(cwd):
        dirs[:] = sorted(d for d in dirs if d != ".git")
        for name in sorted(files):
            if name.endswith(".py"):
                path = os.path.join(root, name)
                with open(path, encoding="utf-8") as f:
                    for no, line in enumerate(f, 1):
                        if "TODO(review):" in line:
                            findings.append({"severity": "blocking", "path": os.path.relpath(path, cwd), "line": no,
                                             "message": line.split("TODO(review):", 1)[1].strip()})
    reply({"outcome": "changes_requested" if findings else "approved",
           "summary": f"결과 커밋 검토: 차단 지적 {len(findings)}건", "findings": findings, "missing_information": []})

if prompt.startswith("# 업무 종류:"):  # 사용자 정의 종류(조사) — 등록 outcome 첫 값
    reply({"outcome": props["outcome"]["enum"][0], "summary": RETURNED_SUMMARY})

if "[ask]" in prompt and "/input-" not in prompt:  # 첫 시도(인계된 이전 결과 없음) — 정보가 모자라다
    reply({"summary": "호스트 sysctl 값을 알아야 합니다", "outcome": "needs_information", "files_changed": [],
           "notes": "LXC 호스트의 nf_conntrack_max 값이 필요합니다"})

scenario = re.search(r"\[scenario:([a-z]+)\]", prompt).group(1)
files = FIXES[f"{scenario}:first"]
for rel, text in files.items():
    path = os.path.join(cwd, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
reply({"summary": f"{scenario} 수정", "outcome": "ready_for_review", "files_changed": sorted(files), "notes": ""})
'''

# 옛 러너 — 결과 뒤 판단을 모르는 버전(phase 21)이 claim 에 싣는 능력
_OLD_RUNNER = (
    "import sys\n"
    "import workflow.connector.client as client\n"
    "client.CentralClient.claim.__kwdefaults__['capabilities'] = ('verify_only',)\n"
    "from workflow.connector.cli import main\n"
    "sys.exit(main(sys.argv[1:]))\n"
)


def install_fake_claude(bin_dir) -> None:
    bin_dir.mkdir(parents=True)
    text = _FAKE_CLAUDE
    for name, value in (("FIXES", FIXES), ("PURPOSE", PURPOSE), ("QUESTION", QUESTION), ("NEW_TITLE", NEW_TITLE),
                        ("RETURNED_SUMMARY", RETURNED_SUMMARY)):
        text = text.replace(f"__{name}__", repr(value))
    script = bin_dir / "claude"
    script.write_text(f"#!{sys.executable}\n" + text.split("\n", 1)[1])
    script.chmod(0o755)


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    workdir = tmp_path_factory.mktemp("next-step-cycle")
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
        "SESSION_SECRET": "e2e-session-secret-" + "n" * 20,
        "OPERATOR_TOKEN": "e2e-operator-token-" + "n" * 20,
        "WORKFLOW_PUBLIC_URL": f"http://127.0.0.1:{port}",
    }
    fake_bin = workdir / "bin"
    install_fake_claude(fake_bin)
    connector_env = {
        **inherited,
        "WORKFLOW_CONNECTOR_HOME": str(workdir / "connector"),
        "PATH": f"{fake_bin}{os.pathsep}{inherited.get('PATH', '')}",
    }
    world = World(workdir=workdir, central_url=f"http://127.0.0.1:{port}", central_env=central_env,
                  connector_env=connector_env, fake=fake, fake_port=github_server.server_address[1],
                  billing=original, shop=None, base={"billing": base})
    world.ctx.update(bare=bare, receiver=receiver,
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
            for _ in range(2):  # 관리자·박조사 — 쿠키 병이 따로다
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


def add_issue(world: World, number: int, title: str, body: str) -> int:
    """이슈 하나 → 업무가 생길 때까지. 반환은 업무 키 번호(사이에 새 업무가 생기므로 이슈 번호와 다를 수 있다)."""
    world.fake.add(REPO, gh_issue(REPO, number, title, labels=(), body=body,
                                  updated_at=f"2026-09-10T{number:02d}:00:00Z"))
    (row,) = drive(world, lambda: q(world, "SELECT key_number FROM work_items WHERE title = ?", title), f"{title} 업무")
    return row["key_number"]


def work(world: World, key_number: int):
    (row,) = q(world, "SELECT * FROM work_items WHERE key_number = ?", key_number)
    return row


def logs_of(world: World, key_number: int, *, cause: str | None = None) -> list:
    """그 업무의 판단 로그 — 오래된 순. `cause` 를 주면 그 원인만."""
    rows = q(world, "SELECT l.* FROM triage_logs l JOIN work_items w ON w.work_item_id = l.work_item_id"
                    " WHERE w.key_number = ? ORDER BY l.created_at, l.rowid", key_number)
    return [r for r in rows if cause is None or r["cause"] == cause]


def latest(world: World, key_number: int, cause: str, state: str):
    rows = logs_of(world, key_number, cause=cause)
    return rows[-1] if rows and rows[-1]["state"] == state else None


def stages(world: World, key_number: int) -> list:
    return q(world, "SELECT t.* FROM tasks t JOIN work_items w ON w.work_item_id = t.work_item_id"
                    " WHERE w.key_number = ? AND t.kind != 'triage' ORDER BY t.created_at", key_number)


def human_requests(world: World, task_id: str) -> list:
    return q(world, "SELECT * FROM human_requests WHERE task_id = ? ORDER BY created_at", task_id)


def section(html: str, name: str) -> str:
    marker = f'data-panel-section="{name}"'
    if marker not in html:
        return ""
    return unescape(html.split(marker, 1)[1].split("</section>", 1)[0])


def next_step_panel(world: World, key_number: int) -> str:
    return section(page(world, "/tasks", open=f"RUN-{key_number}"), "next_step")


def bodies_at(world: World, path: str, event: str) -> list[dict]:
    with world.ctx["receiver"].lock:
        return [body for p, body in world.ctx["receiver"].received if p == path and body.get("event") == event]


def notes(world: World, event: str) -> list[tuple[str, str]]:
    """알림 행 — (경로, 받는 사람)."""
    return [(r["channel"], r["recipient_member_id"]) for r in
            q(world, "SELECT channel, recipient_member_id FROM notifications WHERE event = ? ORDER BY rowid", event)]


def start_runner(world: World, *, old: bool = False) -> None:
    py = sys.executable
    head = [py, "-c", _OLD_RUNNER] if old else [py, "-m", "workflow.connector"]
    world.spawn("connector-old" if old else "connector",
                [*head, "run", "--adapter", "auto", "--claim-interval", "0.5", "--heartbeat-interval", "1"],
                world.connector_env)


def runner_capabilities(world: World) -> list[str] | None:
    (row,) = q(world, "SELECT capabilities_json FROM connectors")
    return json.loads(row[0]) if row[0] else None


def accept_intake(world: World, key_number: int) -> None:
    """접수 판단 제안 → [제안대로 맡기기] → 수정 착수."""
    log = drive(world, lambda: latest(world, key_number, "intake", "proposed"), f"RUN-{key_number} 접수 판단 제안")
    accepted = world.http.post(f"/work/RUN-{key_number}/triage/accept", data={"triage_id": log["triage_id"]})
    assert accepted.status_code == 303, accepted.text[:500]


def fix_needs_information(world: World, key_number: int):
    """수정 단계가 `needs_information` 결과를 낼 때까지. 반환은 (수정 Task, 그 실행)."""
    (fix,) = stages(world, key_number)
    execution = drive(world, lambda: next((e for e in execs(world, fix["task_id"]) if e["status"] == "result_ready"),
                                          None), f"RUN-{key_number} 수정 결과")
    envelope = artifact_json(world, execution["result_artifact_id"])
    assert envelope["outcome"] == "needs_information"
    return fix, execution


def artifact_json(world: World, artifact_id: str) -> dict:
    conn = world.conn()
    try:
        return json.loads(repo.read_artifact(conn, world.store, artifact_id))
    finally:
        conn.close()


def request_row(world: World):
    (row,) = q(world, "SELECT * FROM internal_requests")
    return row


def merge_to_done(world: World, key_number: int) -> None:
    key = f"RUN-{key_number}"
    pr = drive(world, lambda: next((p for p in world.fake.pulls.values() if p["head"]["ref"] == f"runloom/{key}"),
                                   None), f"{key} 초안 PR")
    drive(world, lambda: work(world, key_number)["status"] == "PR · 검토", f"{key} PR · 검토")
    world.fake.merge(pr["number"])
    drive(world, lambda: work(world, key_number)["status"] == "완료", f"{key} 병합 → 완료")


# --- 시나리오 ------------------------------------------------------------------------------


def test_01_admin_sets_up_github_a_member_webhooks_a_runner_the_triage_agent_and_the_directory(world):
    http, member = world.http, world.ctx["member"]
    assert log_in(http, world.central_env["OPERATOR_TOKEN"]).status_code == 303
    (admin,) = q(world, "SELECT member_id, session_id FROM members WHERE email = ?", E2E_ADMIN["email"])
    world.ctx.update(admin_id=admin["member_id"], session_id=admin["session_id"])
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

    issued = http.post("/team/invites", data={"role": "member", "invitee_email": MEMBER["email"]})
    link = re.search(rf"{re.escape(world.central_url)}/invite/([A-Za-z0-9_-]+)", issued.text)
    assert link, issued.text[:1000]
    assert member.post(f"/invite/{link.group(1)}", data=MEMBER).status_code == 303
    (row,) = q(world, "SELECT member_id FROM members WHERE email = ?", MEMBER["email"])
    world.ctx["member_id"] = member_id = row["member_id"]
    assert http.post("/operator/notifications/webhook", data={"url": world.ctx["shared_url"]}).status_code in (200, 303)
    assert member.post("/me/webhook", data={"url": world.ctx["personal_url"]}).status_code in (200, 303)

    issued = http.post(f"/operator/github/sources/{world.sources[REPO]}/runner")
    command = unescape(source_card(world, issued.text).split("data-runner-command", 1)[1])
    server, code = re.search(r"install-runner\.sh --server (\S+) --code (\S+) --repo <", command).groups()
    py = sys.executable
    world.once("connector-setup", [
        py, "-m", "workflow.connector", "setup", "--server", server, "--code", code, "--repo", str(world.billing),
        "--tool", "claude", "--verify", f"check={py} -m pytest -q -p no:cacheprovider",
    ])
    (agent,) = q(world, "SELECT agent_id, capabilities_json FROM agents WHERE local_registration_id = ?",
                 REGISTRATION)
    world.ctx["agent_id"] = agent_id = agent["agent_id"]
    (scope,) = [c["scope"]["repository_id"] for c in json.loads(agent["capabilities_json"]) if c["code"] == "code.review"]
    world.ctx["repository_id"] = scope
    start_runner(world)
    world.worker = make_worker(world)
    deadline = time.monotonic() + 30
    while "data-runner-missing" in source_card(world) or "after_result_triage" not in (
            runner_capabilities(world) or []) or not q(
            world, "SELECT 1 FROM agents WHERE agent_id = ? AND base_commit IS NOT NULL", agent_id):
        assert time.monotonic() < deadline, world.log_tails()
        time.sleep(0.3)
    assert runner_capabilities(world) == ["after_result_triage", "verify_only"]

    (source,) = http.get("/github/sources").json()["sources"]
    fields = ("repository_full_name", "intake", "workflow_repository_id", "label_filter", "selected_issue_numbers",
              "start_at", "fix_verification_profile_id", "review_agent_id", "run_mode", "max_rework_rounds",
              "trigger_label", "default_fix_agent_id", "enabled")
    updated = http.put(f"/github/sources/{source['source_id']}", json={
        **{k: source[k] for k in fields}, "triage_agent_id": agent_id, "expected_revision": source["config_revision"]})
    assert updated.status_code == 200, updated.text[:500]

    # 조사 종류(능력은 러너 에이전트가 가진 code.review 로 — 실연동 1회차 우회와 같다)와 담당 범위 표
    kind = http.post("/kinds", data={"kind": INVESTIGATION, "label": "장애 조사", "capability_code": "code.review",
                                     "scope_key": "repository_id", "outcomes": "cause_found, unresolved",
                                     "instructions": "요청 본문만 근거로 원인 후보를 정리한다."})
    assert kind.status_code == 303, kind.text[:500]
    conn = world.conn()
    try:
        revision = repo.get_config_revision(conn, world.ctx["session_id"])
    finally:
        conn.close()
    added = http.post("/responsibilities/add", data={
        "system_id": "kube_proxy", "request_kind": "investigation", "recipient_member_id": member_id,
        "judgment_member_id": member_id, "agent_id": agent_id, "expected_revision": revision})
    assert added.status_code == 303, added.text[:500]


def test_02_a_fix_needing_information_gets_an_internal_request_proposal_instead_of_a_human_request(world):
    admin_id, agent_id = world.ctx["admin_id"], world.ctx["agent_id"]
    key = add_issue(world, 1, "kube-proxy 가 LXC 에서 conntrack 설정 실패",
                    "업그레이드 뒤 kube-proxy 가 시작되지 않습니다. [scenario:invoice] [ask] [next:internal]")
    world.ctx["main"] = key
    accept_intake(world, key)
    fix, execution = fix_needs_information(world, key)
    world.ctx.update(fix_task=fix["task_id"], fix_execution=execution["execution_id"])

    log = drive(world, lambda: latest(world, key, "after_result", "proposed"), "결과 뒤 판단 제안")
    assert (log["trigger"], log["agent_id"], log["cause_execution_id"], log["cause_request_id"], log["handling"]) == (
        "auto", agent_id, execution["execution_id"], None, None)
    (judge,) = execs(world, log["task_id"])
    request = json.loads(judge["request_json"])
    assert request["target"]["mode"] == "next_step"
    assert request["request"].splitlines()[0] == f"# 다음 단계 판단: RUN-{key} kube-proxy 가 LXC 에서 conntrack 설정 실패"
    assert "### 사내 요청 담당 범위" in request["request"] and "결과 needs_information" in request["request"]
    action = json.loads(log["result_json"])["next_action"]
    assert action == {"type": "internal_request", "system_id": "kube_proxy", "request_kind": "investigation",
                      "recipient_member_id": world.ctx["member_id"], "purpose": PURPOSE}
    assert human_requests(world, fix["task_id"]) == []  # 사람 요청 대신 판단
    assert (work(world, key)["status"], work(world, key)["status_reason"]) == ("내 차례", "다음 단계 제안 · 사내 요청")
    assert ("shared", admin_id) in notes(world, "next_step_proposed")
    panel = next_step_panel(world, key)
    assert "사내 요청 · kube_proxy/investigation → 박조사" in panel and "제안대로" in panel and PURPOSE in panel
    assert 'data-next-step="다음 단계 제안"' in page(world, "/tasks")
    assert _git(world.billing, "status", "--porcelain") == ""  # 판단은 임시 체크아웃에서 읽기만


def test_03_accepting_creates_the_request_and_notifies_the_recipient(world):
    key, admin_id, member_id = world.ctx["main"], world.ctx["admin_id"], world.ctx["member_id"]
    log = latest(world, key, "after_result", "proposed")
    accepted = world.http.post(f"/work/RUN-{key}/next-step/accept", data={"triage_id": log["triage_id"]})
    assert accepted.status_code == 303, accepted.text[:500]
    row = request_row(world)
    assert (row["created_by_triage_id"], row["requester_member_id"], row["recipient_member_id"], row["state"],
            row["system_id"], row["purpose"]) == (
        log["triage_id"], admin_id, member_id, "pending", "kube_proxy", PURPOSE)
    world.ctx["request_id"] = row["request_id"]
    (log,) = logs_of(world, key, cause="after_result")
    assert (log["handling"], log["handled_by_member_id"]) == ("accepted", admin_id)
    w = work(world, key)
    assert (w["status"], w["status_reason"]) == ("대기", "사내 요청 대기 · 박조사")
    fix = q(world, "SELECT status, finished_at FROM tasks WHERE task_id = ?", world.ctx["fix_task"])[0]
    assert fix["finished_at"] is None  # 원인 Task 는 그대로

    assert sorted(notes(world, "internal_request_received")) == [("personal", member_id), ("shared", member_id)]
    (body,) = drive(world, lambda: bodies_at(world, PERSONAL_PATH, "internal_request_received"), "박조사에게 알림")
    assert body["event"] == "internal_request_received"
    assert "판단 제안으로 생성" in unescape(world.ctx["member"].get("/requests").text)
    again = world.http.post(f"/work/RUN-{key}/next-step/accept", data={"triage_id": log["triage_id"]})
    assert again.status_code == 409  # 두 번 눌러도 요청 하나
    assert len(q(world, "SELECT 1 FROM internal_requests")) == 1


def test_04_the_recipient_accepts_investigates_reviews_and_returns(world):
    member, request_id = world.ctx["member"], world.ctx["request_id"]
    accepted = member.post(f"/requests/{request_id}/accept", data={"expected_revision": request_row(world)["revision"]})
    assert accepted.status_code == 303, accepted.text[:500]
    started = member.post(f"/requests/{request_id}/investigation", data={
        "investigation_selection": json.dumps({"kind": INVESTIGATION, "scope_value": world.ctx["repository_id"]}),
        "expected_revision": request_row(world)["revision"]})
    assert started.status_code == 303, started.text[:500]
    (link,) = q(world, "SELECT task_id FROM internal_request_investigations WHERE request_id = ?", request_id)
    task_id = link["task_id"]
    execution = drive(world, lambda: next((e for e in execs(world, task_id) if e["status"] == "result_ready"), None)
                      and q(world, "SELECT 1 FROM tasks WHERE task_id = ? AND status = '확인 필요'", task_id)
                      and execs(world, task_id)[-1], "조사 결과")
    reviewed = member.post(f"/tasks/{task_id}/review", data={"decision": "approve"})
    assert reviewed.status_code == 303, reviewed.text[:500]
    returned = member.post(f"/requests/{request_id}/return", data={
        "execution_id": execution["execution_id"], "expected_revision": request_row(world)["revision"]})
    assert returned.status_code == 303, returned.text[:500]
    (row,) = q(world, "SELECT returned_summary, returned_at FROM internal_request_investigations WHERE request_id = ?",
               request_id)
    assert row["returned_summary"] == RETURNED_SUMMARY and row["returned_at"] is not None


def test_05_the_return_gets_a_rework_proposal_and_the_rework_runs_to_a_merged_pr(world):
    key, agent_id, admin_id = world.ctx["main"], world.ctx["agent_id"], world.ctx["admin_id"]
    log = drive(world, lambda: latest(world, key, "request_returned", "proposed"), "반환 뒤 판단 제안")
    assert (log["cause_request_id"], log["cause_execution_id"]) == (world.ctx["request_id"],
                                                                    world.ctx["fix_execution"])
    (judge,) = execs(world, log["task_id"])
    text = json.loads(judge["request_json"])["request"]
    assert "## 반환된 사내 요청" in text and RETURNED_SUMMARY in text
    assert json.loads(log["result_json"])["next_action"] == {
        "type": "stage", "kind": "bug_fix", "assignee": {"type": "agent", "id": agent_id}, "rework": True}
    assert work(world, key)["status_reason"] == "다음 단계 제안 · 재작업"
    assert "재작업 → " in next_step_panel(world, key)

    accepted = world.http.post(f"/work/RUN-{key}/next-step/accept", data={"triage_id": log["triage_id"]})
    assert accepted.status_code == 303, accepted.text[:500]
    first, rework = execs(world, world.ctx["fix_task"])
    assert first["execution_id"] == world.ctx["fix_execution"] and first["released_at"] is not None
    comment = q(world, "SELECT artifact_id FROM artifacts WHERE execution_id = ? AND kind = 'review_comment'",
                first["execution_id"])
    (artifact,) = comment
    note = artifact_json(world, artifact["artifact_id"])["comment"]
    assert note.startswith("다음 단계 판단: 재작업") and RETURNED_SUMMARY in note
    assert artifact["artifact_id"] in json.loads(rework["request_json"])["input_artifact_ids"]

    merge_to_done(world, key)
    assert [t["kind"] for t in stages(world, key)] == ["bug_fix", "code_review"]
    assert [(r["cause"], r["state"], r["handling"]) for r in logs_of(world, key)] == [
        ("intake", "proposed", "accepted"), ("after_result", "proposed", "accepted"),
        ("request_returned", "proposed", "accepted")]
    assert all(r["handled_by_member_id"] == admin_id for r in logs_of(world, key))
    assert human_requests(world, world.ctx["fix_task"]) == []  # 사람 요청 없이 사이클이 닫혔다


def test_06_dismissing_a_proposal_opens_the_original_human_request(world):
    key = add_issue(world, 2, "청구서 번호 자릿수", "번호가 5자리로 채워지지 않습니다. [scenario:invoice] [ask] [next:human]")
    accept_intake(world, key)
    fix, _ = fix_needs_information(world, key)
    log = drive(world, lambda: latest(world, key, "after_result", "proposed"), "사람 확인 제안")
    assert json.loads(log["result_json"])["next_action"] == {"type": "human", "question": QUESTION}
    assert human_requests(world, fix["task_id"]) == []

    dismissed = world.http.post(f"/work/RUN-{key}/next-step/dismiss", data={"triage_id": log["triage_id"]})
    assert dismissed.status_code == 303, dismissed.text[:500]
    (opened,) = drive(world, lambda: human_requests(world, fix["task_id"]), "원래 사람 요청")
    assert opened["code"] == "fix_needs_information"
    for _ in range(3):  # 다시 돌아도 하나
        world.worker.tick()
    assert len(human_requests(world, fix["task_id"])) == 1
    (log,) = logs_of(world, key, cause="after_result")
    assert log["handling"] == "dismissed"
    assert work(world, key)["status"] == "내 차례"
    assert "다음 단계 제안 무시함" in next_step_panel(world, key)


def test_07_a_result_without_a_rule_gets_a_new_work_proposal(world):
    http, admin_id = world.http, world.ctx["admin_id"]
    conn = world.conn()
    try:
        rule_ids = [rule_id for rule_id, rule in repo.list_rules(conn, world.ctx["session_id"])
                    if rule.from_kind == "bug_fix"]
    finally:
        conn.close()
    assert rule_ids
    for rule_id in rule_ids:
        assert http.post(f"/rules/{rule_id}/delete").status_code == 303

    key = add_issue(world, 3, "쿠폰이 두 번 차감됩니다", "쿠폰 1장이 두 번 빠집니다. [scenario:coupon] [next:new_work]")
    accept_intake(world, key)
    (fix,) = stages(world, key)
    log = drive(world, lambda: latest(world, key, "after_result", "proposed"), "새 업무 제안")
    (execution,) = execs(world, fix["task_id"])
    assert log["cause_execution_id"] == execution["execution_id"]
    assert json.loads(log["result_json"])["next_action"] == {
        "type": "new_work", "kind": "bug_fix", "title": NEW_TITLE, "assignee": {"type": "member", "id": admin_id}}

    accepted = http.post(f"/work/RUN-{key}/next-step/accept", data={"triage_id": log["triage_id"]})
    assert accepted.status_code == 303, accepted.text[:500]
    (spawned,) = q(world, "SELECT * FROM work_items WHERE title = ?", NEW_TITLE)
    (link,) = q(world, "SELECT from_work_item_id FROM work_item_links WHERE to_work_item_id = ? AND type = 'spawned_from'",
                spawned["work_item_id"])
    assert link["from_work_item_id"] == work(world, key)["work_item_id"]
    assert (spawned["assignee_type"], spawned["assignee_id"]) == ("member", admin_id)
    done = q(world, "SELECT status, status_reason FROM tasks WHERE task_id = ?", fix["task_id"])[0]
    assert tuple(done) == ("완료", "새 업무로 넘김")
    assert [t["kind"] for t in stages(world, spawned["key_number"])] == ["bug_fix"]
    assert execs(world, stages(world, spawned["key_number"])[0]["task_id"]) == []  # 멤버는 배정만


def test_08_an_old_runner_gets_no_next_step_and_the_human_request_opens_as_before(world):
    proc, log = world.procs.pop("connector")
    proc.terminate()
    proc.wait(10)
    log.close()
    start_runner(world, old=True)
    deadline = time.monotonic() + 30
    while runner_capabilities(world) != ["verify_only"]:
        assert time.monotonic() < deadline, world.log_tails()
        time.sleep(0.3)

    key = add_issue(world, 4, "환불 수수료 계산", "수수료는 3% 입니다. [scenario:refund] [ask] [next:human]")
    accept_intake(world, key)  # 접수 판단은 옛 러너도 한다
    fix, _ = fix_needs_information(world, key)
    (opened,) = drive(world, lambda: human_requests(world, fix["task_id"]), "원래 사람 요청")
    assert opened["code"] == "fix_needs_information"
    for _ in range(3):
        report = world.worker.tick()
        assert report.next_step_started == 0
    assert logs_of(world, key, cause="after_result") == []
    assert next_step_panel(world, key) == ""


def test_v23_copy_upgrades_to_v24(tmp_path):
    """v23 사본(접수 판단 셋·알림 둘·사내 요청 하나) → 앱 시작이 v24 로 올림 → 행 수 그대로·외래키 검사 통과·원인 intake."""
    from fastapi.testclient import TestClient

    from tests.workflow.adapters.test_db import _v23_db

    db_path = tmp_path / "central" / "db.sqlite"
    db_path.parent.mkdir()
    old = _v23_db(db_path)
    old.commit()
    tables = ("work_items", "tasks", "executions", "triage_logs", "notifications", "internal_requests", "members")
    before = {t: old.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables}
    old.close()

    env = {"WORKFLOW_MODE": "selfhost", "WORKFLOW_DB_PATH": str(db_path),
           "WORKFLOW_ARTIFACT_DIR": str(tmp_path / "artifacts"), "WORKFLOW_SECRET_DIR": str(tmp_path / "secrets"),
           "SESSION_SECRET": "e2e-session-secret-" + "v" * 20, "OPERATOR_TOKEN": "e2e-operator-token-" + "v" * 20}
    with TestClient(create_app(load_settings(env))) as client:
        assert client.get("/healthz").status_code == 200

    conn = connect(db_path)
    try:
        assert conn.execute("SELECT version FROM schema_version").fetchone()[0] == SCHEMA_VERSION == 25
        assert {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables} == before
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert {r[0] for r in conn.execute("SELECT cause FROM triage_logs")} == {"intake"}
        assert [r[0] for r in conn.execute("SELECT created_by_triage_id FROM internal_requests")] == [None]
    finally:
        conn.close()
