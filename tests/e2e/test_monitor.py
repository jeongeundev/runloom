"""모니터링 e2e — 가짜 GitHub App·PATH 앞 가짜 `claude` 러너로 판단·인계를 쌓은 뒤 세 탭·`/metrics.json`·`/metrics.csv`
의 숫자를 본다 (phase 20 step 9, ADR-0026, ARCHITECTURE "모니터링 — phase 20").

`test_triage_cycle` 의 구성(가짜 GitHub App + PR 경로, bare origin, uvicorn 스레드의 중앙 API, 테스트가 돌리는 워커)을
그대로 쓴다. 가짜 `claude` 는 판단 확신도를 이슈 본문의 `[confidence:x]` 로, 수정은 `[scenario:이름]` 과 재작업 여부
('# 이전 검토 지적' 절)로 고르고, `[ask]` 이면 인계 파일(`input-…`)이 없는 첫 시도에 `needs_information` 을 낸다.

흐름:
1. 관리자(운영자) → GitHub 연결(가짜) → claude 러너 → 판단 Agent 칸. 멤버 김멤버 초대.
2. 이슈 3건 → 자동 판단(기준 v1) → RUN-1 은 김멤버가 [제안대로 맡기기] → 정보 요청 → 김멤버 응답 → 수정·검토·초안 PR 병합.
   RUN-2 는 다르게(김멤버 직접), RUN-3 은 [무시].
3. 판단 기준 v2 저장 → 이슈 4 → 판단(v2) → 제안대로 → 검토 수정 요청 → 재작업 1회 → 병합.
4. 자동 시작 기준값 0.70 저장(끈 채) → 연결 판단 탭 미리보기.
5. `/monitor` 세 탭·`/metrics.json`·`/metrics.csv` 숫자.
마지막 함수는 따로: v15 사본(업무·판단 로그·실행) → 앱 시작이 v16 으로 올림 → 세 탭 200, 설정 번호 머리 `기록 없음`.

`WORKFLOW_E2E=1` 일 때만 돈다. 실제 Claude·GitHub 호출 없음 — 네트워크는 127.0.0.1 과 MockTransport 뿐이다.
테스트 함수는 번호 순으로 이어지며 앞 단계의 상태(`world`)를 쓴다.
"""

import csv
import io
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
from tests.e2e.test_jira_cycle import FakeJiraCloud, make_worker, tasks_of, work
from tests.e2e.test_real_repo import FakeGitHubPulls, source_card
from tests.e2e.test_team_handoff import MEMBER, respond
from tests.e2e.test_work_ui import page
from workflow.adapters.db import SCHEMA_VERSION, connect
from workflow.server import worker as worker_module
from workflow.server.app import create_app
from workflow.server.settings import load_settings

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(os.environ.get("WORKFLOW_E2E") != "1", reason="WORKFLOW_E2E=1 일 때만"),
]

REGISTRATION = "billing"
ISSUES = {  # 번호 → (제목, 본문). 확신도는 구간마다 하나씩
    1: ("청구서 번호 자릿수", "번호가 5자리로 채워지지 않습니다. [scenario:invoice] [confidence:0.95] [ask]"),
    2: ("환불 수수료 계산", "수수료는 3% 입니다. [scenario:refund] [confidence:0.85]"),
    3: ("주문 합계 반올림", "999.6원이 999원이 됩니다. [scenario:rounding] [confidence:0.6]"),
    4: ("쿠폰이 두 번 차감됩니다", "쿠폰 1장이 두 번 빠집니다. [scenario:coupon] [confidence:0.75]"),
}
CRITERIA_V2 = "범위가 모듈 하나이고 재현 테스트로 확인할 수 있으면 맡겨도 됨.\n운영 접근이 필요하면 부적합."

_FAKE_CLAUDE = r'''#!/usr/bin/env python3
"""e2e 가짜 claude (모니터링). 모델 없이 `--json-schema` 모양으로 판단·검토·수정을 고른다. stdout 에 결과 JSON 한 덩어리."""
import json
import os
import re
import sys

FIXES = __FIXES__

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


if "proceed" in props:  # 판단 — 확신도는 본문 표식
    agents = re.findall(r"^- agent:(\S+) ", prompt, re.M)
    confidence = float(re.search(r"\[confidence:([0-9.]+)\]", prompt).group(1))
    reply({"proceed": "ready", "confidence": confidence, "proposed_kind": "bug_fix",
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

if "[ask]" in prompt and "/input-" not in prompt:  # 첫 시도(인계된 이전 결과 없음) — 사람에게 묻는다
    reply({"summary": "기준 자릿수를 확인해야 합니다", "outcome": "needs_information", "files_changed": [],
           "notes": "청구서 번호 자릿수를 5자리로 고정해도 되는지 알려 주세요"})

scenario = re.search(r"\[scenario:([a-z]+)\]", prompt).group(1)
rework = "# 이전 검토 지적" in prompt
files = FIXES[f"{scenario}:{'rework' if rework else 'first'}"]
for rel, text in files.items():
    path = os.path.join(cwd, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
reply({"summary": f"{scenario} 수정", "outcome": "ready_for_review", "files_changed": sorted(files), "notes": ""})
'''


def install_fake_claude(bin_dir) -> None:
    bin_dir.mkdir(parents=True)
    script = bin_dir / "claude"
    script.write_text(f"#!{sys.executable}\n" + _FAKE_CLAUDE.replace("__FIXES__", repr(FIXES)).split("\n", 1)[1])
    script.chmod(0o755)


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    workdir = tmp_path_factory.mktemp("monitor")
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
    jira = FakeJiraCloud()  # make_worker 가 Jira 클라이언트 자리에 쓴다 — 연결하지 않는다

    port = _free_port()
    inherited = {k: v for k, v in os.environ.items() if not k.startswith(DROP_PREFIXES)}
    central_env = {
        **inherited,
        "WORKFLOW_MODE": "selfhost",
        "WORKFLOW_DB_PATH": str(workdir / "central" / "db.sqlite"),
        "WORKFLOW_ARTIFACT_DIR": str(workdir / "central" / "artifacts"),
        "WORKFLOW_SECRET_DIR": str(workdir / "central" / "secrets"),
        "SESSION_SECRET": "e2e-session-secret-" + "m" * 20,
        "OPERATOR_TOKEN": "e2e-operator-token-" + "m" * 20,
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
            for _ in range(2):  # 관리자·멤버 — 쿠키 병이 따로다
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
            root.removeHandler(log_handler)
            root.setLevel(old_level)
            log_handler.close()


# --- 도우미 --------------------------------------------------------------------------------


def logs_of(world: World, key_number: int) -> list:
    return q(world, "SELECT l.* FROM triage_logs l JOIN work_items w ON w.work_item_id = l.work_item_id"
                    " WHERE w.key_number = ? ORDER BY l.created_at, l.rowid", key_number)


def proposed(world: World, key_number: int):
    rows = logs_of(world, key_number)
    return rows[-1] if rows and rows[-1]["state"] == "proposed" else None


def stages(world: World, key_number: int) -> list:
    return [t for t in tasks_of(world, key_number) if t["kind"] != "triage"]


def add_issue(world: World, number: int) -> None:
    title, body = ISSUES[number]
    world.fake.add(REPO, gh_issue(REPO, number, title, labels=(), body=body,
                                  updated_at=f"2026-09-10T{number:02d}:00:00Z"))


def merge_to_done(world: World, key_number: int) -> None:
    key = f"RUN-{key_number}"
    pr = drive(world, lambda: next((p for p in world.fake.pulls.values() if p["head"]["ref"] == f"runloom/{key}"),
                                   None), f"{key} 초안 PR")
    drive(world, lambda: work(world, key_number)["status"] == "PR · 검토", f"{key} PR · 검토")
    world.fake.merge(pr["number"])
    drive(world, lambda: work(world, key_number)["status"] == "완료", f"{key} 병합 → 완료")


def ratio(value: dict) -> tuple[int, int]:
    return value["numerator"], value["denominator"]


def csv_rows(world: World) -> dict[tuple[str, str], dict]:
    response = world.http.get("/metrics.csv")
    assert response.status_code == 200, response.text[:500]
    return {(r["group"], r["metric"]): r for r in csv.DictReader(io.StringIO(response.text))}


# --- 시나리오 ------------------------------------------------------------------------------


def test_01_admin_connects_github_attaches_a_claude_runner_and_invites_a_member(world):
    http, member = world.http, world.ctx["member"]
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

    issued = http.post("/team/invites", data={"role": "member", "invitee_email": MEMBER["email"]})
    link = re.search(rf"{re.escape(world.central_url)}/invite/([A-Za-z0-9_-]+)", issued.text)
    assert link, issued.text[:1000]
    assert member.post(f"/invite/{link.group(1)}", data=MEMBER).status_code == 303
    (row,) = q(world, "SELECT member_id FROM members WHERE email = ?", MEMBER["email"])
    world.ctx["member_id"] = row["member_id"]

    issued = http.post(f"/operator/github/sources/{world.sources[REPO]}/runner")
    command = unescape(source_card(world, issued.text).split("data-runner-command", 1)[1])
    server, code = re.search(r"install-runner\.sh --server (\S+) --code (\S+) --repo <", command).groups()
    py = sys.executable
    world.once("connector-setup", [
        py, "-m", "workflow.connector", "setup", "--server", server, "--code", code, "--repo", str(world.billing),
        "--tool", "claude", "--verify", f"check={py} -m pytest -q -p no:cacheprovider",
    ])
    (agent,) = q(world, "SELECT agent_id FROM agents WHERE local_registration_id = ?", REGISTRATION)
    world.ctx["agent_id"] = agent_id = agent["agent_id"]
    world.spawn("connector", [py, "-m", "workflow.connector", "run", "--adapter", "auto", "--claim-interval", "0.5",
                              "--heartbeat-interval", "1"], world.connector_env)
    world.worker = make_worker(world)
    deadline = time.monotonic() + 30
    while "data-runner-missing" in source_card(world) or not q(
            world, "SELECT 1 FROM connectors WHERE supported_kinds_json LIKE '%triage%'") or not q(
            world, "SELECT 1 FROM agents WHERE agent_id = ? AND base_commit IS NOT NULL", agent_id):
        assert time.monotonic() < deadline, world.log_tails()
        time.sleep(0.3)

    (source,) = http.get("/github/sources").json()["sources"]
    fields = ("repository_full_name", "intake", "workflow_repository_id", "label_filter", "selected_issue_numbers",
              "start_at", "fix_verification_profile_id", "review_agent_id", "run_mode", "max_rework_rounds",
              "trigger_label", "default_fix_agent_id", "enabled")
    assert source["max_rework_rounds"] == 1
    updated = http.put(f"/github/sources/{source['source_id']}", json={
        **{k: source[k] for k in fields}, "triage_agent_id": agent_id, "expected_revision": source["config_revision"]})
    assert updated.status_code == 200, updated.text[:500]


def test_02_three_issues_are_triaged_under_criteria_v1(world):
    for number in (1, 2, 3):
        add_issue(world, number)
    drive(world, lambda: all(proposed(world, n) for n in (1, 2, 3)), "이슈 셋 판단 제안")
    for number, confidence in ((1, 0.95), (2, 0.85), (3, 0.6)):
        (log,) = logs_of(world, number)
        assert (log["criteria_version"], log["proceed"], log["confidence"], log["handling"]) == (
            1, "ready", confidence, None)


def test_03_member_accepts_run1_answers_the_question_and_the_pr_is_merged(world):
    member, agent_id, member_id = world.ctx["member"], world.ctx["agent_id"], world.ctx["member_id"]
    accepted = member.post("/work/RUN-1/triage/accept", data={"triage_id": proposed(world, 1)["triage_id"]})
    assert accepted.status_code == 303, accepted.text[:500]
    one = work(world, 1)
    assert (one["assignee_type"], one["assignee_id"], one["requested_by_member_id"]) == ("agent", agent_id, member_id)
    (fix,) = stages(world, 1)
    request = drive(world, lambda: next((r for r in member.get("/human-requests").json()["requests"]
                                         if r["task_id"] == fix["task_id"]), None), "RUN-1 정보 요청")
    answered = respond(member, request, "answer-run1", "resume", "5자리로 고정합니다")
    assert answered.status_code == 200, answered.text[:500]
    merge_to_done(world, 1)
    assert len(execs(world, fix["task_id"])) == 2  # 정보 요청 → 응답 뒤 다시 수정
    (response,) = q(world, "SELECT member_id FROM human_responses")
    assert response["member_id"] == member_id
    assert (logs_of(world, 1)[0]["handling"], logs_of(world, 1)[0]["handled_by_member_id"]) == ("accepted", member_id)


def test_04_run2_goes_to_the_member_directly_and_run3_is_dismissed(world):
    http, member_id = world.http, world.ctx["member_id"]
    assigned = http.post("/work/RUN-2/assignee", data={"assignee": f"member:{member_id}"})
    assert assigned.status_code == 303, assigned.text[:500]
    dismissed = http.post("/work/RUN-3/triage/dismiss", data={"triage_id": proposed(world, 3)["triage_id"]})
    assert dismissed.status_code == 303, dismissed.text[:500]
    assert [logs_of(world, n)[0]["handling"] for n in (2, 3)] == ["changed", "dismissed"]


def test_05_criteria_v2_then_run4_is_accepted_reworked_once_and_merged(world):
    http = world.http
    saved = http.post("/operator/triage/criteria", data={"body": CRITERIA_V2, "expected_version": "1"})
    assert saved.status_code == 303, saved.text[:500]
    add_issue(world, 4)
    log = drive(world, lambda: proposed(world, 4), "RUN-4 판단 제안")
    assert (log["criteria_version"], log["confidence"]) == (2, 0.75)
    accepted = http.post("/work/RUN-4/triage/accept", data={"triage_id": log["triage_id"]})
    assert accepted.status_code == 303, accepted.text[:500]
    merge_to_done(world, 4)
    (fix,) = [t for t in stages(world, 4) if t["kind"] == "bug_fix"]
    keys = [e["start_key"] for e in execs(world, fix["task_id"])]
    assert len(keys) == 2 and keys[1].startswith("rework:")


def test_06_autostart_threshold_change_shows_the_preview_line(world):
    saved = world.http.post("/operator/triage/autostart/bug_fix", data={"enabled": "", "threshold": "0.70"})
    assert saved.status_code == 303, saved.text[:500]
    html = unescape(page(world, "/settings", tab="triage"))
    # 0.70 이상 bug_fix 제안 = RUN-1(0.95 제안대로)·RUN-2(0.85 다르게)·RUN-4(0.75 제안대로), 병합 둘 다
    assert "지금 기준값 0.70 이상 판단 3건 — 사람 일치 2/3 · 병합 2/2" in html


def test_07_metrics_json_counts_triage_quality_assignees_and_config_changes(world):
    data = world.http.get("/metrics.json").json()
    agent_id, admin_id, member_id = world.ctx["agent_id"], world.ctx["admin_id"], world.ctx["member_id"]
    triage = data["triage"]
    overall = triage["overall"]
    assert (overall["proposed"], overall["failed"], overall["superseded"], overall["running"]) == (4, 0, 0, 0)
    assert overall["handling"] == {"accepted": 2, "changed": 1, "dismissed": 1, "auto_started": 0, "unhandled": 0}
    assert overall["proceed"] == {"ready": 4, "needs_check": 0, "unsuitable": 0}
    assert ratio(overall["agreement"]) == (2, 3)
    assert ratio(overall["merged"]) == (2, 2) and overall["merged"]["incomplete"] == 0
    assert ratio(overall["merged_without_rework"]) == (1, 2)  # RUN-4 는 재작업 1회
    assert (overall["cost_usd"]["n"], overall["cost_usd"]["total"]) == (4, pytest.approx(0.04))
    assert overall["triage_time"]["n"] == 4
    by_criteria = {q["key"]: q for q in triage["by_criteria"]}
    assert list(by_criteria) == ["v1", "v2"]
    assert (by_criteria["v1"]["proposed"], ratio(by_criteria["v1"]["agreement"])) == (3, (1, 2))
    assert (by_criteria["v2"]["proposed"], ratio(by_criteria["v2"]["agreement"])) == (1, (1, 1))
    assert ratio(by_criteria["v1"]["merged_without_rework"]) == (1, 1)
    assert ratio(by_criteria["v2"]["merged_without_rework"]) == (0, 1)
    assert [q["key"] for q in triage["by_kind"]] == ["bug_fix"]
    buckets = {b["key"]: (b["proposed"], ratio(b["agreement"]), ratio(b["merged"]), ratio(b["merged_without_rework"]))
               for b in triage["confidence"]}
    assert buckets == {"0-0.5": (0, (0, 0), (0, 0), (0, 0)), "0.5-0.7": (1, (0, 0), (0, 0), (0, 0)),
                       "0.7-0.8": (1, (1, 1), (1, 1), (0, 1)), "0.8-0.9": (1, (0, 1), (0, 0), (0, 0)),
                       "0.9-1": (1, (1, 1), (1, 1), (1, 1))}

    assignees = data["assignees"]
    members = {m["member_id"]: m for m in assignees["members"]}
    assert list(members) == [admin_id, member_id]
    kim = members[member_id]
    assert (kim["display_name"], kim["done"], kim["open"]) == (MEMBER["display_name"], 0, 1)  # RUN-2
    assert kim["response_time"]["n"] == 1 and members[admin_id]["response_time"]["n"] == 0
    (agent,) = assignees["agents"]
    assert (agent["agent_id"], agent["done"], agent["open"], agent["rework"]) == (agent_id, 2, 0, 1)
    # RUN-1 수정 2·검토 1, RUN-4 수정 2·검토 2 — 판단 실행은 빼고
    assert agent["runs"] == 7 and ratio(agent["failure"]) == (0, 7)
    assert ratio(agent["first_pass"]) == (1, 2)
    assert (assignees["unassigned_open"], assignees["responses_unknown_member"]) == (1, 0)  # RUN-3

    changes = [(c["area"], c["action"], c["subject"], c["by_member_id"]) for c in data["config_changes"]]
    assert changes == [  # 설정 번호 1 은 워크스페이스 첫 값 — 바꾼 것이 아니라 기록이 없다
        ("source", "add", REPO, admin_id),
        ("source", "change", f"{REPO} · triage_agent_id", admin_id),
        ("triage_criteria", "change", "v2", admin_id),
        ("triage_autostart", "change", "bug_fix 끔 · 기준값 0.70", admin_id),
    ]
    assert [c["revision"] for c in data["config_changes"]] == [2, 3, 4, 5]
    assert {c["by_member_name"] for c in data["config_changes"]} == {E2E_ADMIN["display_name"]}
    assert CRITERIA_V2 not in json.dumps(data, ensure_ascii=False)  # 기록은 이름만 — 본문은 넣지 않는다


def test_08_metrics_csv_has_the_same_numbers_in_new_rows(world):
    rows = csv_rows(world)
    agent_id, member_id = world.ctx["agent_id"], world.ctx["member_id"]

    def cells(group, metric, *names):
        return tuple(rows[(group, metric)][n] for n in names)

    assert cells("triage:all", "agreement", "numerator", "denominator") == ("2", "3")
    assert cells("triage:criteria:v1", "agreement", "numerator", "denominator") == ("1", "2")
    assert cells("triage:all", "merged_without_rework", "numerator", "denominator") == ("1", "2")
    assert cells("triage:all", "handling:dismissed", "total") == ("1",)
    assert cells("triage:confidence:0.7-0.8", "merged", "numerator", "denominator") == ("1", "1")
    assert cells(f"agent:{agent_id}", "runs", "total") == ("7",)
    assert cells(f"agent:{agent_id}", "first_pass", "numerator", "denominator") == ("1", "2")
    assert cells(f"member:{member_id}", "response_time", "n") == ("1",)
    assert cells("unassigned", "open", "total") == ("1",)
    assert not any(MEMBER["display_name"] in str(r.values()) for r in rows.values())  # CSV 에는 id 만


def test_09_monitor_tabs_show_the_same_numbers(world):
    data = world.http.get("/metrics.json").json()
    triage = unescape(page(world, "/monitor", tab="triage"))
    assert "제안 4 · 사람 일치 2/3 · 병합 완료 2/2(진행 중 0)" in triage
    assert all(f"<th>{label}</th>" in triage for label in ("v1", "v2"))

    assignees = unescape(page(world, "/monitor", tab="assignees"))
    assert f"<th>{MEMBER['display_name']}</th>" in assignees and f"<th>{E2E_ADMIN['display_name']}</th>" in assignees
    assert "담당 없는 진행 중 업무 1" in assignees

    before_after = unescape(page(world, "/monitor", tab="before_after", group_by="config_revision"))
    heads = re.findall(r"data-config-head[^>]*>([^<]*)<", before_after)
    assert heads and all(h.startswith("설정 ") for h in heads)
    by_revision = {c["revision"]: c for c in data["config_changes"]}
    for head in heads:
        number = int(re.match(r"설정 (\d+) — ", head).group(1))
        if number in by_revision:
            assert by_revision[number]["subject"] in head and E2E_ADMIN["display_name"] in head
        else:
            assert head == f"설정 {number} — 기록 없음"


def test_v15_copy_upgrades_to_v16_and_the_monitor_tabs_open(tmp_path):
    """v15 사본(고정 워크스페이스로 옮긴 업무·판단 로그·판단 실행 + 설정 번호 3 의 수정 실행) → 앱 시작이 v16 으로 올림 →
    행 수 그대로·빈 설정 변경 기록 → 세 탭 200, 설정 번호 머리는 `설정 3 — 기록 없음`."""
    from fastapi.testclient import TestClient

    from tests.workflow.adapters.test_db import _insert_task, _v15_db
    from workflow.adapters import repo
    from workflow.domain import team
    from workflow.server.auth import LOGIN_COOKIE, SELFHOST_SESSION_ID, utc_now

    db_path = tmp_path / "central" / "db.sqlite"
    db_path.parent.mkdir()
    old = _v15_db(db_path)
    old.execute("PRAGMA foreign_keys = OFF")  # s1 을 셀프호스트 고정 워크스페이스로 옮긴다
    for (table,) in old.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall():
        if any(c[1] == "session_id" for c in old.execute(f"PRAGMA table_info({table})")):
            old.execute(f"UPDATE {table} SET session_id = ? WHERE session_id = 's1'", (SELFHOST_SESSION_ID,))
    _insert_task(old, "t2", SELFHOST_SESSION_ID, "bug_fix")
    old.execute("UPDATE tasks SET work_item_id = 'wi-000000000001' WHERE task_id = 't2'")
    old.execute("INSERT INTO executions (execution_id, task_id, attempt_no, start_key, agent_id, kind, request_json,"
                " status, config_revision, created_at) VALUES ('e2', 't2', 1, 'k2', 'agt-00000001', 'bug_fix', '{}',"
                " 'queued', 3, '2026-09-30T00:00:00Z')")
    old.commit()
    old.execute("PRAGMA foreign_keys = ON")
    assert old.execute("PRAGMA foreign_key_check").fetchall() == []
    tables = ("work_items", "tasks", "executions", "triage_logs", "triage_criteria", "triage_autostart", "members")
    before = {t: old.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables}
    old.close()

    env = {"WORKFLOW_MODE": "selfhost", "WORKFLOW_DB_PATH": str(db_path),
           "WORKFLOW_ARTIFACT_DIR": str(tmp_path / "artifacts"), "WORKFLOW_SECRET_DIR": str(tmp_path / "secrets"),
           "SESSION_SECRET": "e2e-session-secret-" + "w" * 20, "OPERATOR_TOKEN": "e2e-operator-token-" + "w" * 20}
    with TestClient(create_app(load_settings(env))) as client:
        conn = connect(db_path)
        try:
            assert conn.execute("SELECT version FROM schema_version").fetchone()[0] == SCHEMA_VERSION
            assert {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables} == before
            assert conn.execute("SELECT COUNT(*) FROM config_changes").fetchone()[0] == 0
            assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
            repo.set_member_credentials(conn, SELFHOST_SESSION_ID, "mem-00000001", email="admin@example.com",
                                        password_hash=team.hash_password("v15-admin-password"), now=utc_now())
            token = repo.create_login_session(conn, SELFHOST_SESSION_ID, "mem-00000001", now=utc_now(), days=1)
        finally:
            conn.close()
        client.cookies.set(LOGIN_COOKIE, token)
        pages = {tab: client.get("/monitor", params={"tab": tab, "group_by": "config_revision"})
                 for tab in ("before_after", "triage", "assignees")}
        assert {tab: r.status_code for tab, r in pages.items()} == {
            "before_after": 200, "triage": 200, "assignees": 200}
        assert "설정 3 — 기록 없음" in unescape(pages["before_after"].text)
        assert "아직 판단 기록이 없습니다" not in pages["triage"].text  # 옮긴 판단 로그(도는 중 1)가 보인다
        data = client.get("/metrics.json").json()
        assert data["config_changes"] == [] and data["triage"]["overall"]["running"] == 1
