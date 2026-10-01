"""판단 e2e — 가짜 GitHub App·가짜 Jira Cloud·PATH 앞 가짜 `claude` 러너로 자동 판단부터 자동 시작까지
(phase 19 step 10, ADR-0025).

`test_jira_cycle` 의 구성(가짜 GitHub App + PR 경로, bare origin, uvicorn 스레드의 중앙 API, 테스트가 돌리는 워커,
가짜 Jira `MockTransport`)을 그대로 쓰고, 러너 도구만 가짜 `claude` 다. 가짜는 `--json-schema` 의 모양으로 할 일을 고른다:
판단(`proceed` 칸)은 요청문의 후보 줄에서 첫 에이전트를 골라 제안하고(`[triage:outside]` 면 후보 밖 담당), 검토(`findings`)는
체크아웃의 `TODO(review):` 를 찾고, 그 밖(수정)은 `test_github_cycle.FIXES` 대본대로 worktree 에 쓴다.

흐름:
1. 관리자 → GitHub 연결(가짜) → claude 러너 붙이기 → 저장소 카드 판단 Agent 칸.
2. 이슈 2건 → 자동 판단이 한 번에 1건씩 → 둘 다 제안(`맡겨도 됨`) → 목록 배지.
3. RUN-1 [제안대로 맡기기] → 수정·검토·초안 PR·병합 → 완료, 판단 로그 `accepted`.
4. RUN-2 를 다른 담당(멤버)으로 → `changed`.
5. 후보 밖 제안 → 판단 실패(`triage_invalid`) — 내 차례·사람 요청 없음, [다시 판단] → 이전 행 `superseded`.
6. Jira 업무 → 연결 저장소의 판단 Agent 가 판단 → [무시] → `dismissed`.
7. 자동 시작: 20건 미만이면 잠김 → 처리 20건 시드 → 켬 → 다음 업무 자동 맡김(`auto_started`·맡긴 사람 없음) → 초안 PR.
8. 옛 러너(triage 미지원) → 판단 없음·"러너 업데이트 필요", 수정 순환은 그대로 병합까지.
마지막 함수는 따로: v14 사본(업무·단계·실행·Agent·Jira 행) → 앱 시작이 v15 로 올림.

`WORKFLOW_E2E=1` 일 때만 돈다. 실제 Claude·GitHub·Jira 호출 없음 — 네트워크는 127.0.0.1 과 MockTransport 뿐이다.
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
from tests.e2e.test_jira_cycle import (
    EMAIL,
    JIRA_TOKEN,
    PROJECT_ID,
    SITE,
    FakeJiraCloud,
    make_worker,
    tasks_of,
    work,
)
from tests.e2e.test_real_repo import PR_NUMBER, FakeGitHubPulls, source_card, status_flow
from tests.e2e.test_team_handoff import my_turn_keys
from tests.e2e.test_work_ui import page, panel_of
from workflow.adapters.db import SCHEMA_VERSION, connect
from workflow.server import worker as worker_module
from workflow.server.app import create_app
from workflow.server.settings import load_settings

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(os.environ.get("WORKFLOW_E2E") != "1", reason="WORKFLOW_E2E=1 일 때만"),
]

REGISTRATION = "billing"
CONFIDENCE = 0.9  # 가짜 판단의 확신도 — 자동 시작 기준값 0.8 이상

_FAKE_CLAUDE = r'''#!/usr/bin/env python3
"""e2e 가짜 claude. 모델 없이 `--json-schema` 모양으로 판단·검토·수정을 고른다. stdout 에 결과 JSON 한 덩어리."""
import json
import os
import re
import sys

FIXES = __FIXES__
CONFIDENCE = __CONFIDENCE__

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


if "proceed" in props:  # 판단 — 기본 브랜치 체크아웃을 읽기만 한다
    agents = re.findall(r"^- agent:(\S+) ", prompt, re.M)
    assignee = "agt-outside" if "[triage:outside]" in prompt else agents[0]
    reply({"proceed": "ready", "confidence": CONFIDENCE, "proposed_kind": "bug_fix",
           "assignee": {"type": "agent", "id": assignee}, "predecessors": [],
           "reasons": [{"criterion": "scope", "note": "모듈 하나의 변경"},
                       {"criterion": "verifiability", "note": "재현 테스트로 확인 가능"}],
           "missing_information": []})

if "findings" in props:  # 검토 — 결과 커밋 체크아웃
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

scenario = re.search(r"\[scenario:([a-z]+)\]", prompt).group(1)
files = FIXES[f"{scenario}:first"]
for rel, text in files.items():
    path = os.path.join(cwd, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
reply({"summary": f"{scenario} 재현 테스트 추가 후 수정", "outcome": "ready_for_review",
       "files_changed": sorted(files), "notes": ""})
'''

# 옛 러너 — 판단을 모르는 버전이 claim 에 싣는 지원 종류(phase 18 까지)
_OLD_RUNNER = (
    "import sys\n"
    "import workflow.connector.client as client\n"
    "client.SUPPORTED_BUILTIN_KINDS = ('bug_fix', 'code_review')\n"
    "from workflow.connector.cli import main\n"
    "sys.exit(main(sys.argv[1:]))\n"
)


def install_fake_claude(bin_dir) -> None:
    bin_dir.mkdir(parents=True)
    text = _FAKE_CLAUDE.replace("__FIXES__", repr(FIXES)).replace("__CONFIDENCE__", repr(CONFIDENCE))
    script = bin_dir / "claude"
    script.write_text(f"#!{sys.executable}\n" + text.split("\n", 1)[1])
    script.chmod(0o755)


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    workdir = tmp_path_factory.mktemp("triage-cycle")
    (workdir / "logs").mkdir()

    bare = workdir / "remote" / "billing.git"
    bare.parent.mkdir(parents=True)
    _git(bare.parent, "init", "-q", "--bare", "-b", "main", str(bare))
    original = workdir / "repos" / "billing"
    base = make_repo(original, BILLING_FILES)
    _git(original, "remote", "add", "origin", "git@github.com:acme/billing.git")
    _git(original, "config", f"url.{bare}.insteadOf", "git@github.com:acme/billing.git")
    _git(original, "push", "-q", "origin", "main")

    fake = FakeGitHubPulls(bare)  # 이슈는 러너·판단 Agent 를 정한 뒤 테스트가 올린다
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
        "SESSION_SECRET": "e2e-session-secret-" + "t" * 20,
        "OPERATOR_TOKEN": "e2e-operator-token-" + "t" * 20,
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
    root.setLevel(logging.INFO)
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


# --- 도우미 --------------------------------------------------------------------------------


def logs_of(world: World, key_number: int) -> list:
    """그 업무의 판단 로그 — 오래된 순."""
    return q(world, "SELECT l.* FROM triage_logs l JOIN work_items w ON w.work_item_id = l.work_item_id"
                    " WHERE w.key_number = ? ORDER BY l.created_at, l.rowid", key_number)


def latest_log(world: World, key_number: int):
    rows = logs_of(world, key_number)
    return rows[-1] if rows else None


def proposed(world: World, key_number: int):
    log = latest_log(world, key_number)
    return log if log is not None and log["state"] == "proposed" else None


def stages(world: World, key_number: int) -> list:
    """판단이 아닌 단계."""
    return [t for t in tasks_of(world, key_number) if t["kind"] != "triage"]


def add_issue(world: World, number: int, title: str, body: str) -> None:
    world.fake.add(REPO, gh_issue(REPO, number, title, labels=(), body=body,
                                  updated_at=f"2026-09-10T{number:02d}:00:00Z"))


def triage_section(html: str) -> str:
    if 'data-panel-section="triage"' not in html:
        return ""
    return unescape(html.split('data-panel-section="triage"', 1)[1].split("</section>", 1)[0])


def merge_to_done(world: World, key_number: int, what: str) -> None:
    key = f"RUN-{key_number}"
    pr = drive(world, lambda: next((p for p in world.fake.pulls.values() if p["head"]["ref"] == f"runloom/{key}"),
                                   None), f"{what} 초안 PR")
    drive(world, lambda: work(world, key_number)["status"] == "PR · 검토", f"{what} PR · 검토")
    world.fake.merge(pr["number"])
    drive(world, lambda: work(world, key_number)["status"] == "완료", f"{what} 병합 → 완료")


def start_runner(world: World, *, old: bool = False) -> None:
    py = sys.executable
    head = [py, "-c", _OLD_RUNNER] if old else [py, "-m", "workflow.connector"]
    world.spawn("connector-old" if old else "connector",
                [*head, "run", "--adapter", "auto", "--claim-interval", "0.5", "--heartbeat-interval", "1"],
                world.connector_env)


def supported_kinds(world: World) -> list[str] | None:
    (row,) = q(world, "SELECT supported_kinds_json FROM connectors")
    return json.loads(row[0]) if row[0] else None


# --- 시나리오 ------------------------------------------------------------------------------


def test_01_admin_connects_github_attaches_a_claude_runner_and_picks_the_triage_agent(world):
    http = world.http
    assert log_in(http, world.central_env["OPERATOR_TOKEN"]).status_code == 303
    (admin,) = q(world, "SELECT member_id FROM members WHERE email = ?", E2E_ADMIN["email"])
    world.ctx["admin_id"] = admin["member_id"]
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
    assert source["triage_agent_id"] is None

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
    assert [c["code"] for c in json.loads(agent["capabilities_json"])] == ["code.fix", "code.review", "code.triage"]
    start_runner(world)
    world.worker = make_worker(world)
    deadline = time.monotonic() + 30
    while "data-runner-missing" in source_card(world) or "triage" not in (supported_kinds(world) or []) or not q(
            world, "SELECT 1 FROM agents WHERE agent_id = ? AND base_commit IS NOT NULL", agent_id):
        assert time.monotonic() < deadline, world.log_tails()
        time.sleep(0.3)

    # 저장소 카드의 판단 Agent 칸 — 후보는 code.triage 가 있고 정책이 '바로 실행'인 에이전트
    card = source_card(world)
    assert 'name="triage_agent_id"' in card and f'<option value="{agent_id}"' in card
    (source,) = http.get("/github/sources").json()["sources"]
    fields = ("repository_full_name", "intake", "workflow_repository_id", "label_filter", "selected_issue_numbers",
              "start_at", "fix_verification_profile_id", "review_agent_id", "run_mode", "max_rework_rounds",
              "trigger_label", "default_fix_agent_id", "enabled")
    updated = http.put(f"/github/sources/{source['source_id']}", json={
        **{k: source[k] for k in fields}, "triage_agent_id": agent_id, "expected_revision": source["config_revision"]})
    assert updated.status_code == 200, updated.text[:500]
    assert http.get("/github/sources").json()["sources"][0]["triage_agent_id"] == agent_id


def test_02_two_new_issues_are_triaged_one_at_a_time(world):
    agent_id = world.ctx["agent_id"]
    add_issue(world, 1, "청구서 번호 자릿수", "번호가 5자리로 채워지지 않습니다. [scenario:invoice]")
    add_issue(world, 2, "환불 수수료 계산", "수수료는 3% 입니다. [scenario:refund]")
    report = world.worker.tick()
    assert report.issues_created == 2 and report.triage_started == 1 and report.tasks_started == 0
    # 워크스페이스에서 한 번에 1건 — 오래된 업무부터
    assert [r["state"] for r in logs_of(world, 1)] == ["running"] and logs_of(world, 2) == []
    one = work(world, 1)
    assert (one["status"], one["status_reason"], one["assignee_type"]) == ("새로 들어옴", "판단 중", None)

    def running_at_most_one():
        assert len(q(world, "SELECT 1 FROM triage_logs WHERE state = 'running'")) <= 1
        return proposed(world, 1) and proposed(world, 2)

    drive(world, running_at_most_one, "두 업무 판단 제안")
    for key_number in (1, 2):
        (log,) = logs_of(world, key_number)
        assert (log["trigger"], log["agent_id"], log["criteria_version"], log["requested_by_member_id"]) == (
            "auto", agent_id, 1, None)
        assert (log["proceed"], log["confidence"], log["proposed_kind"], log["handling"]) == (
            "ready", CONFIDENCE, "bug_fix", None)
        result = json.loads(log["result_json"])
        assert result["assignee"] == {"type": "agent", "id": agent_id}
        assert result["inspected_commit"] == world.base["billing"]  # 중앙이 고정한 기본 브랜치 끝
        (execution,) = execs(world, log["task_id"])
        request = json.loads(execution["request_json"])
        assert request["kind"] == "triage" and request["target"]["base_commit"] == world.base["billing"]
        assert request["request"].splitlines()[0].startswith(f"# 판단: RUN-{key_number} ")
        w = work(world, key_number)
        assert (w["status"], w["status_reason"], w["assignee_type"]) == (
            "새로 들어옴", "판단 제안 · 맡겨도 됨 0.90", None)
        assert [t["kind"] for t in stages(world, key_number)] == ["bug_fix"]  # 판단은 맡길 단계가 아니다
        assert execs(world, stages(world, key_number)[0]["task_id"]) == []  # 판단은 착수하지 않는다
    # 원본 폴더는 그대로 — 판단은 임시 체크아웃에서 읽기만
    assert _git(world.billing, "status", "--porcelain") == ""
    listed = page(world, "/tasks")
    assert listed.count('data-triage="판단 제안"') == 2
    section = triage_section(page(world, "/tasks", open="RUN-1"))
    assert "판단 제안 · 맡겨도 됨 · 확신도" in section and "제안대로 맡기기" in section and "무시" in section


def test_03_accepting_the_proposal_runs_the_fix_cycle_to_done(world):
    agent_id, admin_id = world.ctx["agent_id"], world.ctx["admin_id"]
    log = proposed(world, 1)
    accepted = world.http.post("/work/RUN-1/triage/accept", data={"triage_id": log["triage_id"]})
    assert accepted.status_code == 303, accepted.text[:500]
    one = work(world, 1)
    assert (one["assignee_type"], one["assignee_id"], one["requested_by_member_id"]) == ("agent", agent_id, admin_id)
    (fix,) = stages(world, 1)
    assert len(execs(world, fix["task_id"])) == 1
    (log,) = logs_of(world, 1)
    assert (log["handling"], log["handled_by_member_id"], log["final_assignee_type"], log["final_assignee_id"],
            log["final_kind"]) == ("accepted", admin_id, "agent", agent_id, "bug_fix")

    merge_to_done(world, 1, "RUN-1")
    flow = status_flow(world, one["work_item_id"])
    assert flow[-1] == "완료" and "에이전트 작업 중" in flow and "PR · 검토" in flow
    assert [t["kind"] for t in stages(world, 1)] == ["bug_fix", "code_review"]
    (log,) = logs_of(world, 1)
    assert (log["state"], log["handling"]) == ("proposed", "accepted")  # 판단은 완료 판정에 끼지 않는다
    assert "제안대로 맡김" in triage_section(page(world, "/tasks", open="RUN-1"))


def test_04_choosing_a_different_assignee_records_changed(world):
    admin_id = world.ctx["admin_id"]
    assigned = world.http.post("/work/RUN-2/assignee", data={"assignee": f"member:{admin_id}"})
    assert assigned.status_code == 303, assigned.text[:500]
    (log,) = logs_of(world, 2)
    assert (log["handling"], log["handled_by_member_id"], log["final_assignee_type"], log["final_assignee_id"],
            log["final_kind"]) == ("changed", admin_id, "member", admin_id, "bug_fix")
    (fix,) = stages(world, 2)
    assert execs(world, fix["task_id"]) == []  # 사람은 배정만


def test_05_a_failed_triage_makes_no_turn_and_can_be_asked_again(world):
    add_issue(world, 3, "운영 서버 로그 확인", "운영 DB 접근이 필요합니다. [triage:outside] [scenario:invoice]")
    log = drive(world, lambda: (r := latest_log(world, 3)) is not None and r["state"] == "failed" and r,
                "후보 밖 제안 → 판단 실패")
    assert log["failed_code"] == "triage_invalid"
    assert log["failed_message"].startswith("assignee_not_candidate — 후보 밖 담당 agent:agt-outside")
    task = q(world, "SELECT status, finished_at FROM tasks WHERE task_id = ?", log["task_id"])[0]
    assert task["status"] == "실패" and task["finished_at"] is not None
    three = work(world, 3)
    assert (three["status"], three["status_reason"], three["assignee_type"]) == (
        "새로 들어옴", "판단 실패 · 후보 밖 제안", None)
    assert q(world, "SELECT 1 FROM human_requests WHERE task_id IN (SELECT task_id FROM tasks WHERE work_item_id = ?)",
             three["work_item_id"]) == []
    assert "RUN-3" not in my_turn_keys(world.http)
    for _ in range(3):  # 자동 판단은 실패한 업무에 다시 걸지 않는다
        world.worker.tick()
    assert len(logs_of(world, 3)) == 1
    section = triage_section(page(world, "/tasks", open="RUN-3"))
    assert "판단 실패 · 후보 밖 제안" in section and "다시 판단" in section

    again = world.http.post("/work/RUN-3/triage")
    assert again.status_code == 303, again.text[:500]
    first, second = logs_of(world, 3)
    assert (first["state"], second["state"], second["trigger"], second["requested_by_member_id"]) == (
        "superseded", "running", "manual", world.ctx["admin_id"])
    drive(world, lambda: latest_log(world, 3)["state"] == "failed", "다시 판단 끝")
    assert "RUN-3" not in my_turn_keys(world.http)


def test_06_a_jira_issue_is_triaged_by_the_linked_repository_agent(world):
    http, jira = world.http, world.ctx["jira"]
    connected = http.post("/operator/jira/connect", data={"site_url": SITE, "email": EMAIL, "token": JIRA_TOKEN})
    assert connected.status_code == 303, connected.text[:500]
    added = http.post("/operator/jira/projects", data={
        "project_id": PROJECT_ID, "github_source_id": world.sources[REPO], "start_mode": "from_now"})
    assert added.status_code == 303, added.text[:500]
    jira.add("쿠폰 할인 후 청구서 번호", "번호가 5자리로 채워지지 않습니다. [scenario:invoice]")
    (four,) = drive(world, lambda: q(world, "SELECT * FROM work_items WHERE source_type = 'jira'"), "Jira 업무")
    assert four["key_number"] == 4
    log = drive(world, lambda: proposed(world, 4), "Jira 업무 판단 제안")
    assert (log["agent_id"], log["trigger"]) == (world.ctx["agent_id"], "auto")
    (execution,) = execs(world, log["task_id"])
    request = json.loads(execution["request_json"])
    assert request["target"]["local_registration_id"] == REGISTRATION
    assert "원본: SHOP-12" in request["request"]

    dismissed = http.post("/work/RUN-4/triage/dismiss", data={"triage_id": log["triage_id"]})
    assert dismissed.status_code == 303, dismissed.text[:500]
    log = latest_log(world, 4)
    assert (log["handling"], log["final_assignee_type"], log["final_kind"]) == ("dismissed", None, None)
    assert work(world, 4)["status_reason"] == "담당 없음"
    assert "판단 제안(무시함)" in triage_section(page(world, "/tasks", open="RUN-4"))


def _seed_handled(world: World, count: int) -> None:
    """사람이 처리한 bug_fix 판단을 `count` 건 더 — RUN-1 의 판단 단계·실행·로그 행을 새 ID 로 베낀다."""
    (log,) = logs_of(world, 1)
    conn = world.conn()
    try:
        task = dict(conn.execute("SELECT * FROM tasks WHERE task_id = ?", (log["task_id"],)).fetchone())
        execution = dict(conn.execute("SELECT * FROM executions WHERE execution_id = ?",
                                      (log["execution_id"],)).fetchone())
        row = dict(log)
        for i in range(count):
            suffix = f"{i:04x}"
            values = [(task, {"task_id": f"task-seed0000{suffix}"}),
                      (execution, {"execution_id": f"exec-seed0000{suffix}", "task_id": f"task-seed0000{suffix}",
                                   "start_key": f"seed-{suffix}"}),
                      (row, {"triage_id": f"trg-s{suffix}", "task_id": f"task-seed0000{suffix}",
                             "execution_id": f"exec-seed0000{suffix}"})]
            for (source, changes), table in zip(values, ("tasks", "executions", "triage_logs"), strict=True):
                data = {**source, **changes}
                conn.execute(f"INSERT INTO {table} ({', '.join(data)}) VALUES ({', '.join('?' * len(data))})",
                             tuple(data.values()))
        conn.commit()
    finally:
        conn.close()


def test_07_autostart_unlocks_at_20_handled_triages_and_hands_the_next_work_over(world):
    http, agent_id = world.http, world.ctx["agent_id"]
    locked = http.post("/operator/triage/autostart/bug_fix", data={"enabled": "on", "threshold": "0.80"})
    assert locked.status_code == 409 and "판단 기록 2/20" in unescape(locked.text)
    assert q(world, "SELECT 1 FROM triage_autostart") == []

    _seed_handled(world, 18)
    assert "판단 기록 20/20" in unescape(page(world, "/connect", tab="triage"))
    enabled = http.post("/operator/triage/autostart/bug_fix", data={"enabled": "on", "threshold": "0.80"})
    assert enabled.status_code == 303, enabled.text[:500]
    (setting,) = q(world, "SELECT kind, version, enabled, threshold FROM triage_autostart")
    assert tuple(setting) == ("bug_fix", 1, 1, 0.8)

    add_issue(world, 5, "환불 수수료 다시", "수수료는 3% 입니다. [scenario:refund]")
    log = drive(world, lambda: (r := latest_log(world, 5)) is not None and r["handling"] == "auto_started" and r,
                "판단 → 자동 시작")
    assert (log["handled_by_member_id"], log["final_assignee_type"], log["final_assignee_id"], log["final_kind"]) == (
        None, "agent", agent_id, "bug_fix")
    five = work(world, 5)
    assert (five["assignee_type"], five["assignee_id"], five["requested_by_member_id"]) == ("agent", agent_id, None)
    (fix,) = stages(world, 5)
    assert len(execs(world, fix["task_id"])) == 1
    (event,) = q(world, "SELECT data_json FROM work_item_events WHERE work_item_id = ? AND type = 'assigned'",
                 five["work_item_id"])
    assert json.loads(event["data_json"])["triage"] == {"triage_id": log["triage_id"], "criteria_version": 1}
    panel = unescape(panel_of(page(world, "/tasks", open="RUN-5"), "RUN-5"))
    assert "자동 시작 · 판단 v1" in panel
    merge_to_done(world, 5, "RUN-5")


def test_08_an_old_runner_gets_no_triage_and_the_fix_cycle_still_runs(world):
    proc, log = world.procs.pop("connector")
    proc.terminate()
    proc.wait(10)
    log.close()
    start_runner(world, old=True)
    deadline = time.monotonic() + 30
    while supported_kinds(world) != ["bug_fix", "code_review"]:
        assert time.monotonic() < deadline, world.log_tails()
        time.sleep(0.3)

    add_issue(world, 6, "청구서 번호 다시", "번호가 5자리로 채워지지 않습니다. [scenario:invoice]")
    drive(world, lambda: q(world, "SELECT 1 FROM work_items WHERE key_number = 6"), "RUN-6")
    for _ in range(3):
        report = world.worker.tick()
        assert report.triage_started == 0
    assert logs_of(world, 6) == []
    six = work(world, 6)
    assert (six["status"], six["status_reason"]) == ("새로 들어옴", "담당 없음")
    section = triage_section(page(world, "/tasks", open="RUN-6"))
    assert "러너 업데이트 필요 — 판단 미지원" in section and "disabled" in section

    assigned = world.http.post("/work/RUN-6/assignee", data={"assignee": f"agent:{world.ctx['agent_id']}"})
    assert assigned.status_code == 303, assigned.text[:500]
    merge_to_done(world, 6, "RUN-6")
    assert [t["kind"] for t in stages(world, 6)] == ["bug_fix", "code_review"]
    assert len(world.fake.pulls) == 3 and PR_NUMBER in world.fake.pulls


def test_v14_copy_upgrades_to_v15_with_triage_seeds(tmp_path):
    """v14 사본(업무·단계·실행·Agent·Jira 연결·프로젝트·이슈·전달 행) → 앱 시작이 v15 로 올림 → 행 수 그대로·외래키
    검사 통과·워크스페이스마다 판단 기준 v1·내장 triage 종류·code.fix 로컬 Agent 에 code.triage."""
    from fastapi.testclient import TestClient

    from tests.workflow.adapters.test_db import _insert_task, _v14_db
    from workflow.domain.triage_criteria import TRIAGE_CRITERIA_V1

    db_path = tmp_path / "central" / "db.sqlite"
    db_path.parent.mkdir()
    old = _v14_db(db_path)
    now = "2026-10-01T00:00:00Z"
    old.execute("INSERT INTO jira_connections (session_id, site_url, cloud_id, api_base, email, account_id,"
                " display_name, connected_at, updated_at) VALUES ('s1', ?, 'c', 'gateway', ?, 'acc', '김개발', ?, ?)",
                (SITE, EMAIL, now, now))
    old.execute("INSERT INTO jira_projects (source_id, session_id, project_id, project_key, project_name,"
                " github_source_id, start_mode, start_at, created_at, updated_at) VALUES ('jps-00000001', 's1', ?,"
                " 'SHOP', '쇼핑몰', 'ghs-00000001', 'from_now', ?, ?, ?)", (PROJECT_ID, now, now, now))
    old.execute("INSERT INTO work_items (work_item_id, session_id, key_number, title, request, kind, status,"
                " status_reason, source_type, source_id, source_item_id, created_at, updated_at)"
                " VALUES ('wi-000000000004', 's1', 4, '결제 버튼', 'r', 'bug_fix', '새로 들어옴', '담당 없음', 'jira',"
                " 'jps-00000001', '10012', ?, ?)", (now, now))
    _insert_task(old, "t-jira", "s1", "bug_fix")
    old.execute("UPDATE tasks SET work_item_id = 'wi-000000000004' WHERE task_id = 't-jira'")
    old.execute("INSERT INTO jira_issues (source_id, issue_id, issue_key, task_id, source_revision, snapshot_json,"
                " snapshot_digest, issue_updated_at, state, status_name, created_at, updated_at) VALUES"
                " ('jps-00000001', '10012', 'SHOP-12', 't-jira', 1, '{}', 'd', ?, 'open', '대기', ?, ?)",
                (now, now, now))
    old.execute("INSERT INTO jira_deliveries (delivery_id, session_id, source_id, work_item_id, action, moment,"
                " target, dedupe_key, state, attempts, created_at, updated_at) VALUES ('jdl-00000001', 's1',"
                " 'jps-00000001', 'wi-000000000004', 'transition', 'start', '진행 중',"
                " 'transition:wi-000000000004:start', 'delivered', 1, ?, ?)", (now, now))
    old.commit()
    tables = ("work_items", "tasks", "executions", "agents", "github_sources", "jira_connections", "jira_projects",
              "jira_issues", "jira_deliveries", "members", "field_mappings")
    before = {t: old.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables}
    kinds_before = old.execute("SELECT COUNT(*) FROM kinds").fetchone()[0]
    old.close()

    env = {"WORKFLOW_MODE": "selfhost", "WORKFLOW_DB_PATH": str(db_path),
           "WORKFLOW_ARTIFACT_DIR": str(tmp_path / "artifacts"), "WORKFLOW_SECRET_DIR": str(tmp_path / "secrets"),
           "SESSION_SECRET": "e2e-session-secret-" + "v" * 20, "OPERATOR_TOKEN": "e2e-operator-token-" + "v" * 20}
    with TestClient(create_app(load_settings(env))) as client:
        assert client.get("/healthz").status_code == 200

    conn = connect(db_path)
    try:
        assert conn.execute("SELECT version FROM schema_version").fetchone()[0] == SCHEMA_VERSION == 15
        assert {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables} == before
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert conn.execute("SELECT COUNT(*) FROM kinds").fetchone()[0] == kinds_before + 2  # s1·s2 에 triage
        assert [tuple(r) for r in conn.execute(
            "SELECT session_id, version, body FROM triage_criteria ORDER BY session_id")] == [
            ("s1", 1, TRIAGE_CRITERIA_V1), ("s2", 1, TRIAGE_CRITERIA_V1)]
        caps = {r[0]: [c["code"] for c in json.loads(r[1])]
                for r in conn.execute("SELECT agent_id, capabilities_json FROM agents")}
        assert caps == {"agt-00000001": ["code.fix", "code.review", "code.triage"],
                        "agt-00000002": ["code.fix", "code.triage"], "agt-00000003": ["ops.classify"],
                        "agt-00000004": ["code.fix"]}
        (jira_work,) = conn.execute("SELECT status, status_reason FROM work_items WHERE source_type = 'jira'")
        assert tuple(jira_work) == ("새로 들어옴", "담당 없음")
        assert conn.execute("SELECT COUNT(*) FROM triage_logs").fetchone()[0] == 0
    finally:
        conn.close()
