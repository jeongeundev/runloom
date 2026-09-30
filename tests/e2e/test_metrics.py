"""측정 e2e — 이슈 1건의 수정 → 검토(수정 요청) → 재작업 → 검토 승인 → 운영자 승인 → GitHub 병합·이슈 닫힘을 대역으로
돌린 뒤 지표·기준선을 본다(phase 9 step 10·12).

`test_github_cycle` 의 가짜 GitHub·임시 Git 저장소·가짜 codex 를 다시 쓰고, 이 모듈이 더하는 것:
- **가짜 claude** — 수정 담당 등록의 도구. 가짜 codex 와 같은 시나리오 파일을 쓰고 결과 JSON 에 `total_cost_usd`·`usage` 를
  싣는다(첫 수정 0.25, 재작업 0.35). 검토는 가짜 codex 라 비용·토큰을 보고하지 않는다(= 모름).
- **GraphQL 기준선** — 가짜 GitHub 가 `POST /graphql` 의 `repository.issues` 를 두 건씩 커서로 나눠 준다.
- **GraphQL 병합 조회** — 같은 가짜가 `repository.issue(number)` 에 그 이슈의 상태와 닫은 PR(`merged_prs`)을 준다.
- **중앙 API 는 이 프로세스 안의 uvicorn 스레드** — 기준선 가져오기의 `HttpGitHubClient` 는 그대로(`api.github.com` 고정) 두고
  `app.state.github_client` 의 transport 만 가짜 GitHub 로 넘기기 위해서다. 연결 프로그램은 하위 프로세스, 워커는 테스트가
  tick 을 돌린다.

bug_fix 의 "병합·이슈 종료는 사람" 단계는 운영자의 검토 승인(`/tasks/{id}/review` approve → `완료`)이지만, GitHub 이슈
묶음의 접수 → 완료는 승인이 아니라 그 이슈를 닫은 병합 PR 의 병합 시각으로 잰다(ADR-0015 결정 9). 승인 직후에는 미완료이고,
가짜 GitHub 에서 병합·닫힘 뒤 워커 수집이 병합 시각을 저장하면 완료가 된다. 승인 시각은 접수 → 승인 으로 따로 본다.

`WORKFLOW_E2E=1` 일 때만 돈다. 실제 GitHub·모델 호출 없음 — 네트워크는 127.0.0.1 뿐이다.
"""

import json
import os
import re
import sys
import threading
import time
from pathlib import Path

import httpx
import pytest
import uvicorn

from tests.e2e.test_github_cycle import (
    BILLING_FILES,
    DROP_PREFIXES,
    FIXES,
    KIM,
    START_AT,
    TOKEN,
    FakeGitHub,
    ToFakeGitHub,
    World,
    _free_port,
    drive,
    execs,
    gh_issue,
    install_fake_codex,
    make_repo,
    operator_agent,
    q,
    reviews_of,
    serve,
    task,
)
from workflow.adapters.github_client import HttpGitHubClient
from workflow.domain.metrics import BASELINE_NOTE
from workflow.server import worker as worker_module
from workflow.server.app import create_app
from workflow.server.settings import load_settings

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(os.environ.get("WORKFLOW_E2E") != "1", reason="WORKFLOW_E2E=1 일 때만"),
]

REPO = "acme/billing"
FIX, REVIEW = "agent-fix-kim", "agent-review-billing"
COSTS = {"first": 0.25, "rework": 0.35}

_FAKE_CLAUDE = r'''#!/usr/bin/env python3
"""e2e 가짜 claude. 모델 없이 요청 속 `[scenario:이름]` 과 재작업 여부로 시나리오 파일을 cwd(worktree)에 쓰고,
`--output-format json` 결과 한 덩어리에 비용·토큰을 싣는다."""
import json
import os
import re
import sys

TOKEN = "__TOKEN__"
FIXES = __FIXES__
COSTS = __COSTS__

prompt = sys.stdin.read()
leaked = [k for k, v in os.environ.items() if TOKEN in v or k in ("WORKFLOW_GITHUB_TOKEN", "GITHUB_TOKEN", "GH_TOKEN")]
if leaked:
    print(f"GitHub 토큰이 도구 환경에 있다: {leaked}", file=sys.stderr)
    sys.exit(3)
scenario = re.search(r"\[scenario:([a-z]+)\]", prompt).group(1)
attempt = "rework" if "# 이전 검토 지적" in prompt else "first"
files = FIXES[f"{scenario}:{attempt}"]
for rel, text in files.items():
    os.makedirs(os.path.dirname(os.path.join(os.getcwd(), rel)), exist_ok=True)
    with open(rel, "w", encoding="utf-8") as f:
        f.write(text)
print(json.dumps({
    "type": "result", "subtype": "success", "is_error": False, "result": "",
    "structured_output": {"summary": f"{scenario} {attempt}", "outcome": "ready_for_review",
                          "files_changed": sorted(files), "notes": ""},
    "total_cost_usd": COSTS[attempt], "usage": {"input_tokens": 1000, "output_tokens": 200},
}, ensure_ascii=False))
'''

# 도입 전 이력 — 소스 연결(`github_sources.created_at`, 실제 현재 시각) 이전에 열려 병합 PR 로 닫힌 이슈만 기준선이 된다
BASELINE_ISSUES = [
    {"number": 11, "title": "합계 오류", "createdAt": "2026-08-01T00:00:00Z",
     "prs": [{"number": 12, "merged": True, "mergedAt": "2026-08-01T02:00:00Z"}]},
    {"number": 13, "title": "PR 없이 닫힘", "createdAt": "2026-08-02T00:00:00Z", "prs": []},
    {"number": 14, "title": "병합 안 된 PR", "createdAt": "2026-08-03T00:00:00Z",
     "prs": [{"number": 15, "merged": False, "mergedAt": None}]},
    {"number": 16, "title": "두 PR 중 이른 병합", "createdAt": "2026-08-04T00:00:00Z",
     "prs": [{"number": 18, "merged": True, "mergedAt": "2026-08-04T09:00:00Z"},
             {"number": 17, "merged": True, "mergedAt": "2026-08-04T06:00:00Z"}]},
    {"number": 19, "title": "연결 이후 이슈", "createdAt": "2999-01-01T00:00:00Z",
     "prs": [{"number": 20, "merged": True, "mergedAt": "2999-01-01T01:00:00Z"}]},
]
GRAPHQL_PAGE = 2
ISSUE_OPENED_AT = "2026-09-10T00:00:00Z"  # gh_issue 의 기본 created_at
MERGED_AT = "2026-09-12T00:00:00Z"  # 이슈 #1 을 닫은 PR 의 병합 시각 — 열림에서 2일


class FakeGitHubWithGraphQL(FakeGitHub):
    """`POST /graphql` 의 기준선 질의(`repository.issues`)와 병합 조회(`repository.issue`)만 더한다. 나머지는 `FakeGitHub`
    그대로. `merged_prs` = 이슈 번호 → 그 이슈를 닫은 PR 노드 목록."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.merged_prs: dict[int, list[dict]] = {}

    def handle(self, method, path, query, headers, body):
        if (method, path) != ("POST", "/graphql"):
            return super().handle(method, path, query, headers, body)
        authorized = headers.get("Authorization") == f"Bearer {self.token}"
        with self.lock:
            self.requests.append((method, path, authorized))
        if not authorized:
            return 401, {}, {"message": "Bad credentials"}
        variables = json.loads(body)["variables"]
        if f"{variables['owner']}/{variables['name']}" != REPO:
            return 200, {}, {"data": None, "errors": [{"type": "NOT_FOUND"}]}
        if "number" in variables:
            item = self.issues[REPO].get(variables["number"])
            if item is None:
                return 200, {}, {"data": {"repository": {"issue": None}}, "errors": [{"type": "NOT_FOUND"}]}
            node = {"number": item["number"], "title": item["title"], "createdAt": item["created_at"],
                    "state": item["state"].upper(),
                    "closedByPullRequestsReferences": {"nodes": self.merged_prs.get(item["number"], [])}}
            return 200, {}, {"data": {"repository": {"issue": node}}}
        start = int(variables["cursor"] or 0)
        chunk = BASELINE_ISSUES[start:start + GRAPHQL_PAGE]
        end = start + len(chunk)
        nodes = [{"number": i["number"], "title": i["title"], "createdAt": i["createdAt"],
                  "closedByPullRequestsReferences": {"nodes": i["prs"]}} for i in chunk]
        page = {"hasNextPage": end < len(BASELINE_ISSUES), "endCursor": str(end)}
        return 200, {}, {"data": {"repository": {"issues": {"nodes": nodes, "pageInfo": page}}}}


def install_fake_claude(bin_dir: Path) -> None:
    text = (_FAKE_CLAUDE.replace("__TOKEN__", TOKEN).replace("__FIXES__", repr(FIXES))
            .replace("__COSTS__", repr(COSTS)))
    script = bin_dir / "claude"
    script.write_text(f"#!{sys.executable}\n" + text.split("\n", 1)[1])
    script.chmod(0o755)


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    workdir = tmp_path_factory.mktemp("metrics")
    (workdir / "logs").mkdir()
    billing = workdir / "repos" / "billing"
    base = {"billing": make_repo(billing, BILLING_FILES)}

    fake = FakeGitHubWithGraphQL(TOKEN)
    fake.add(REPO, gh_issue(REPO, 1, "쿠폰이 두 번 차감됩니다", assignees=[KIM], updated_at="2026-09-10T01:00:00Z",
                            body="쿠폰 1장이 두 번 빠집니다. [scenario:coupon]"))
    github_server = serve(fake)
    fake_port = github_server.server_address[1]

    port = _free_port()
    inherited = {k: v for k, v in os.environ.items() if not k.startswith(DROP_PREFIXES)}
    central_env = {
        **inherited,
        "WORKFLOW_DB_PATH": str(workdir / "central" / "db.sqlite"),
        "WORKFLOW_ARTIFACT_DIR": str(workdir / "central" / "artifacts"),
        "SESSION_SECRET": "e2e-session-secret-" + "s" * 20,
        "OPERATOR_TOKEN": "e2e-operator-token-" + "o" * 20,
        "WORKFLOW_PUBLIC_URL": f"http://127.0.0.1:{port}",
        "WORKFLOW_GITHUB_TOKEN": TOKEN,
        "WORKFLOW_GITHUB_REPOS": REPO,
    }
    fake_bin = workdir / "bin"
    install_fake_codex(fake_bin)
    install_fake_claude(fake_bin)
    connector_env = {
        **inherited,
        "WORKFLOW_CONNECTOR_HOME": str(workdir / "connector"),
        "PATH": f"{fake_bin}{os.pathsep}{inherited.get('PATH', '')}",
    }
    world = World(workdir=workdir, central_url=f"http://127.0.0.1:{port}", central_env=central_env,
                  connector_env=connector_env, fake=fake, fake_port=fake_port, billing=billing, shop=None, base=base)

    settings = load_settings(central_env)
    app = create_app(settings)
    app.state.github_client = HttpGitHubClient(settings.github_token, settings.github_repos,
                                               transport=ToFakeGitHub(fake_port))
    api = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    api_thread = threading.Thread(target=api.run, daemon=True)
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


def metrics(world: World, **params) -> dict:
    response = world.http.get("/metrics.json", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def test_01_setup_connects_fix_by_claude_and_review_by_codex(world):
    http = world.http
    login = http.post("/login", data={"token": world.central_env["OPERATOR_TOKEN"]})  # 워크스페이스 = 운영자
    assert login.status_code == 303, login.text[:300]
    world.session_id = q(world, "SELECT session_id FROM sessions WHERE is_operator = 1")[0]["session_id"]

    registrations = ((FIX, "code.fix", "local-billing-kim", "claude", True),
                     (REVIEW, "code.review", "local-billing-review", "codex", False))
    for agent_id, code, registration, _, _ in registrations:
        operator_agent(world, agent_id, code, "billing", registration)
    issued = http.post("/operator/connect-codes")
    connect_code = re.search(r'<code id="issued-code">([^<]+)</code>', issued.text).group(1)
    py = sys.executable
    world.once("connector-connect", [py, "-m", "workflow.connector", "connect", "--server", world.central_url,
                                     "--code", connect_code])
    for agent_id, _, registration, tool, verify in registrations:
        argv = [py, "-m", "workflow.connector", "register", "--id", registration, "--tool", tool,
                "--repo", str(world.billing), "--repository-id", "billing"]
        if verify:
            argv += ["--verify", f"vp-pytest={py} -m pytest -q -p no:cacheprovider"]
        assert f"agent {agent_id}" in world.once(f"connector-register-{registration}", argv)
    world.spawn("connector", [py, "-m", "workflow.connector", "run", "--claim-interval", "0.5",
                              "--heartbeat-interval", "1"], world.connector_env)  # --adapter auto: 등록의 tool 로 고른다
    deadline = time.monotonic() + 30
    while not q(world, "SELECT 1 FROM connectors WHERE supported_kinds_json LIKE '%bug_fix%'"):
        assert time.monotonic() < deadline, world.log_tails()
        time.sleep(0.3)

    response = http.post("/github/sources", json={
        "repository_full_name": REPO, "workflow_repository_id": "billing", "label_filter": ["bug"],
        "selected_issue_numbers": [], "start_at": START_AT, "fix_verification_profile_id": "vp-pytest",
        "review_agent_id": REVIEW, "run_mode": "auto", "max_rework_rounds": 1, "enabled": True,
    })
    assert response.status_code == 201, response.text
    world.sources[REPO] = response.json()["source"]["source_id"]
    user_id, login_name = KIM
    response = http.put(f"/github/sources/{world.sources[REPO]}/assignees/{user_id}",
                        json={"github_login": login_name, "agent_id": FIX})
    assert response.status_code == 200, response.text

    # 가져온 기록이 없으면 지표는 비어 있고 기준선은 '모름'
    empty = metrics(world)
    assert [g["key"] for g in empty["groups"]] == ["all"] and empty["groups"][0]["bundles"] == 0
    assert empty["baselines"][0]["imported"] is False and empty["baselines"][0]["intake_to_merge"] is None


def test_02_issue_runs_fix_review_rework_review_and_operator_approval(world):
    world.worker = world.make_worker()
    report = world.worker.tick()
    assert report.sync_errors == 0 and report.issues_created == 1
    world.tasks["A"] = q(world, "SELECT task_id FROM source_issues")[0]["task_id"]
    a_id = world.tasks["A"]

    drive(world, lambda: reviews_of(world, a_id) and task(world, reviews_of(world, a_id)[0]["task_id"])["finished_at"],
          "재작업 뒤 검토 승인")
    (review,) = reviews_of(world, a_id)
    world.tasks["F"] = review["task_id"]
    fixes, reviews = execs(world, a_id), execs(world, review["task_id"])
    assert [e["start_key"] for e in fixes] == [f"auto:{a_id}:r1", f"rework:{reviews[0]['execution_id']}"]
    assert [e["status"] for e in fixes + reviews] == ["result_ready"] * 4
    assert task(world, a_id)["status"] == "확인 필요"

    # 병합·이슈 종료는 사람 — 운영자 승인이 A 를 `완료` 로 바꾼다
    response = world.http.post(f"/tasks/{a_id}/review", data={"decision": "approve", "comment": ""})
    assert response.status_code == 303, response.text[:500]
    assert task(world, a_id)["status"] == "완료"


def test_03_metrics_json_reflects_rework_handoff_cost_and_versions(world):
    a_id, f_id = world.tasks["A"], world.tasks["F"]
    (group,) = metrics(world)["groups"]
    assert group["key"] == "all" and group["bundles"] == 1
    assert (group["rework"]["n"], group["rework"]["total"]) == (1, 1)
    assert (group["first_pass"]["numerator"], group["first_pass"]["denominator"]) == (0, 1)  # 첫 검토가 수정 요청
    assert group["handoff_wait"]["n"] >= 1  # 후속 검토 Task 생성 → 첫 시작
    # GitHub 이슈 묶음 — 운영자 승인만으로는 완료가 아니다(병합 전 = 미완료). 승인은 접수 → 승인 으로 따로
    assert (group["intake_to_done"]["n"], group["intake_to_done"]["incomplete"]) == (0, 1)
    assert (group["intake_to_merge"]["n"], group["intake_to_merge"]["incomplete"]) == (0, 1)
    assert group["done_by_finished_at"] == 0
    assert group["intake_to_approval"]["n"] == 1
    # 개입 = 사람 요청 + 운영자 검토 결정(승인 1). 재작업 1회로 승인까지 가서 사람 요청은 없다
    assert q(world, "SELECT COUNT(*) FROM human_requests WHERE task_id IN (?, ?)", a_id, f_id)[0][0] == 0
    assert group["interventions"]["total"] == 1
    cost = group["cost_usd"]
    assert cost["total"] == pytest.approx(COSTS["first"] + COSTS["rework"])  # 가짜 claude 가 보고한 값 합
    assert (cost["n"], cost["unknown"], cost["incomplete"]) == (2, 2, 0)  # 검토(codex)는 비용 모름 — 0 아님
    assert (group["input_tokens"]["total"], group["input_tokens"]["unknown"]) == (2000, 2)
    assert (group["reruns"]["numerator"], group["reruns"]["denominator"]) == (2, 4)  # 재작업·재검토 attempt 2

    # 설정 번호 — 실행·후속 링크에 현재 세션 번호가 찍혔고 그 번호로 묶인다
    revision = q(world, "SELECT config_revision FROM sessions WHERE session_id = ?", world.session_id)[0][0]
    assert revision > 1  # 소스 생성이 올렸다
    runs = q(world, "SELECT config_revision, folder_commit, folder_dirty FROM executions WHERE task_id IN (?, ?)",
             a_id, f_id)
    assert {r["config_revision"] for r in runs} == {revision}
    assert {r["rules_revision"] for r in q(world, "SELECT rules_revision FROM followup_links")} == {revision}
    by_revision = metrics(world, group_by="config_revision")
    assert [g["key"] for g in by_revision["groups"]] == [str(revision)]

    # 러너 폴더 커밋 — 등록 폴더(worktree 아님)의 HEAD. 기준 브랜치는 움직이지 않았다
    assert {r["folder_commit"] for r in runs} == {world.base["billing"]}
    by_folder = metrics(world, group_by="folder_commit")
    assert [g["key"] for g in by_folder["groups"]] == [world.base["billing"]]
    assert by_folder["groups"][0]["rework"]["total"] == 1

    events = {r["type"] for r in q(world, "SELECT type FROM task_events WHERE task_id IN (?, ?)", a_id, f_id)}
    assert {"status_changed", "ready"} <= events


def test_04_github_merge_and_close_completes_the_bundle_at_merge_time(world):
    a_id = world.tasks["A"]
    world.fake.merged_prs[1] = [{"number": 7, "merged": False, "mergedAt": None},
                                {"number": 8, "merged": True, "mergedAt": MERGED_AT}]
    item = gh_issue(REPO, 1, "쿠폰이 두 번 차감됩니다", assignees=[KIM], state="closed",
                    updated_at="2026-09-12T00:05:00Z", body="쿠폰 1장이 두 번 빠집니다. [scenario:coupon]")
    world.fake.add(REPO, item)

    report = world.worker.tick()
    assert report.sync_errors == 0
    (row,) = q(world, "SELECT task_id, state, merged_pr_number, pr_merged_at, merge_checked_at FROM source_issues")
    assert (row["task_id"], row["state"], row["merged_pr_number"], row["pr_merged_at"]) == (a_id, "closed", 8, MERGED_AT)
    assert row["merge_checked_at"] is not None

    (group,) = metrics(world)["groups"]
    expected = 2 * 24 * 3600  # 이슈 열림 → 병합 — 승인 시각이 아니다
    assert (group["intake_to_done"]["median"], group["intake_to_done"]["n"]) == (expected, 1)
    assert (group["intake_to_merge"]["median"], group["intake_to_merge"]["n"]) == (expected, 1)
    assert group["intake_to_done"]["incomplete"] == 0 and group["closed_unmerged"] == 0
    approval = group["intake_to_approval"]
    assert approval["n"] == 1 and approval["median"] != expected  # 승인은 실제 시계, 병합은 GitHub 시각

    lookups = len([r for r in world.fake.requests if r[1] == "/graphql"])
    world.worker.tick()  # 병합을 안 뒤에는 다시 조회하지 않는다
    assert len([r for r in world.fake.requests if r[1] == "/graphql"]) == lookups


def test_05_baseline_import_shows_before_and_after_on_the_page(world):
    source_id = world.sources[REPO]
    before = world.http.get("/metrics")
    assert before.status_code == 200 and "가져온 적 없음" in before.text
    earlier = len([r for r in world.fake.requests if r[1] == "/graphql"])  # 병합 조회

    for _ in range(2):  # 멱등 — 다시 가져와도 같은 건수
        response = world.http.post(f"/operator/github/sources/{source_id}/baseline")
        assert response.status_code == 200, response.text
        assert response.json()["item_count"] == 2
    graphql = [r for r in world.fake.requests if r[1] == "/graphql"][earlier:]
    assert len(graphql) == 2 * 3 and all(authorized for _, _, authorized in graphql)  # 두 번 × 3 페이지

    (baseline,) = metrics(world)["baselines"]
    assert baseline["imported"] is True and baseline["note"] == BASELINE_NOTE
    assert baseline["intake_to_merge"]["n"] == 2  # #11(2시간)·#16(이른 병합 6시간). PR 없음·미병합·연결 이후는 제외
    assert baseline["intake_to_merge"]["median"] == 4 * 3600

    page = world.http.get("/metrics")
    assert page.status_code == 200
    top = page.text.split('id="compare"', 1)[1].split("</section>", 1)[0]
    assert REPO in top and "n 2" in top and "4시간 0분" in top and BASELINE_NOTE in top
    assert "48시간 0분" in top and "승인" not in top  # 도입 후도 이슈 열림 → 병합. 승인 지표는 아래 속도 표에
    assert "접수 → 승인" in page.text
    csv_text = world.http.get("/metrics.csv").text
    assert f"baseline:{source_id},speed,intake_to_merge,seconds,14400.0,2" in csv_text

    needle = TOKEN
    assert needle not in page.text and needle not in csv_text and needle not in response.text
