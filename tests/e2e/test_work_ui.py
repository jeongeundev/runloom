"""업무 화면 한 줄기 e2e — 담당자 묶음·패널·에이전트 맡기기·직접 작업·PR 신호 (phase 16 step 10, ADR-0022).

`test_real_repo` 의 가짜 GitHub App(PR 경로 포함)·가짜 알림 수신·워커 연결과 `test_team` 의 bare 저장소 구성을 다시 쓴다.
- **#1 (RUN-1)** — 관리자 로그인 → 이슈 2건 수집 → `/tasks` 담당자 묶음 "담당 없음" 2건 → `/tasks?open=RUN-1` 패널 →
  담당을 에이전트로(= 맡기기) → 가짜 러너가 수정·검토 → 초안 PR → 병합 → 완료. 목록 묶음·보드 칸 이동을 HTML 로 본다.
- **#2 (RUN-2)** — [내 세션에서 작업] → 브랜치 이름 → 가짜 GitHub 에 그 브랜치의 PR 생성 → 동기화 → `PR · 검토` → 병합 → `완료`.
- 연결 탭·옛 주소 넘김·시작하기 완료 표시·알림 링크가 업무 주소임을 한 번씩.

`WORKFLOW_E2E=1` 일 때만 돈다. 실제 GitHub·Discord·모델 호출 없음 — 네트워크는 127.0.0.1 뿐이다.
테스트 함수는 번호 순으로 이어지며 앞 단계의 상태(`world`)를 쓴다. 브라우저 JS(행 클릭·패널 끼우기)는 돌리지 않는다 —
서버가 `?open=` 으로 그리는 패널과 폼 POST 만 본다.
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
from tests.e2e.test_real_repo import (
    PR_NUMBER,
    FakeGitHubPulls,
    Receiver,
    install_fake_codex,
    make_worker,
    received,
    serve_receiver,
    source_card,
)
from workflow.server import worker as worker_module
from workflow.server.app import create_app
from workflow.server.auth import utc_now
from workflow.server.settings import load_settings

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(os.environ.get("WORKFLOW_E2E") != "1", reason="WORKFLOW_E2E=1 일 때만"),
]

REGISTRATION = "billing"
DIRECT_TITLE = "Rounding error 반올림 오류"
DIRECT_BRANCH = "RUN-2-rounding-error"  # 키 + 제목의 ASCII 영숫자 요약
DIRECT_PR = 201  # 사람이 직접 연 PR — Runloom PR(101) 과 번호가 겹치지 않게


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    workdir = tmp_path_factory.mktemp("work-ui")
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
    fake.add(REPO, gh_issue(REPO, 1, "청구서 번호 자릿수", labels=(), updated_at="2026-09-10T01:00:00Z",
                            body="번호가 5자리로 채워지지 않습니다. [scenario:invoice]"))
    fake.add(REPO, gh_issue(REPO, 2, DIRECT_TITLE, labels=(), updated_at="2026-09-10T02:00:00Z",
                            body="999.6원이 999원이 됩니다. 사람이 직접 고친다."))
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
        "SESSION_SECRET": "e2e-session-secret-" + "u" * 20,
        "OPERATOR_TOKEN": "e2e-operator-token-" + "u" * 20,
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
                     hook_url=f"http://127.0.0.1:{receiver_server.server_address[1]}/hooks/work-ui")

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
                                      headers={"Origin": world.central_url})
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


# --- 화면 읽기 도우미 --------------------------------------------------------------------------


def page(world: World, path: str, **params) -> str:
    response = world.http.get(path, params=params)
    assert response.status_code == 200, (path, response.status_code, response.text[:500])
    return response.text


def groups(html: str) -> dict[str, tuple[str, int, list[str]]]:
    """목록 보기의 묶음 — 묶음 키 → (이름, 건수, 행의 업무 키 순서)."""
    found = {}
    for body in html.split('<tbody data-group-body="')[1:]:
        key, rest = body.split('"', 1)
        block = rest.split("</tbody>", 1)[0]
        label = unescape(re.search(r'<span class="group-label">([^<]*)</span>', block).group(1))
        count = int(re.search(r'<span class="count">(\d+)</span>', block).group(1))
        found[key] = (label, count, re.findall(r'<tr class="work-row" data-work-key="([^"]+)"', block))
    return found


def board_column(html: str, work_key: str) -> str | None:
    """보드 보기에서 그 업무 카드가 든 칸의 키."""
    for section in html.split('<section class="board-col" data-column="')[1:]:
        column, rest = section.split('"', 1)
        if f'class="board-card" data-work-key="{work_key}"' in rest.split("</section>", 1)[0]:
            return column
    return None


def panel_of(html: str, work_key: str) -> str:
    assert f'data-work-panel="{work_key}"' in html, html[:1000]
    return html.split(f'data-work-panel="{work_key}"', 1)[1]


def work(world: World, key_number: int):
    (row,) = q(world, "SELECT * FROM work_items WHERE key_number = ?", key_number)
    return row


def work_events(world: World, key_number: int) -> list[tuple[str, dict]]:
    rows = q(world, "SELECT e.type, e.data_json FROM work_item_events e JOIN work_items w"
                    " ON w.work_item_id = e.work_item_id WHERE w.key_number = ? ORDER BY e.id", key_number)
    return [(r["type"], json.loads(r["data_json"])) for r in rows]


def sidebar(html: str) -> str:
    return html.split('<nav', 1)[1].split("</nav>", 1)[0]


# --- 시나리오 ------------------------------------------------------------------------------


def test_01_admin_connects_github_and_two_issues_land_unassigned(world):
    http = world.http
    login = log_in(http, world.central_env["OPERATOR_TOKEN"])
    assert login.status_code == 303, login.text[:300]
    (admin,) = q(world, "SELECT member_id FROM members WHERE email = ?", E2E_ADMIN["email"])
    world.ctx["admin_id"] = admin["member_id"]

    new = http.get("/operator/github/app/new")
    assert new.status_code == 200, new.text[:500]
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
    saved = http.post("/operator/notifications/webhook", data={"url": world.ctx["hook_url"]})
    assert saved.status_code in (200, 303), saved.text[:500]

    world.worker = make_worker(world)
    report = world.worker.tick()
    assert report.sync_errors == 0 and report.issues_created == 2 and report.tasks_started == 0

    # 업무 화면 — 기본 묶기 = 담당자, 맨 위 "담당 없음" 에 2건
    listed = groups(page(world, "/tasks"))
    assert list(listed) == ["none"]
    label, count, keys = listed["none"]
    assert (label, count, sorted(keys)) == ("담당 없음", 2, ["RUN-1", "RUN-2"])
    board = page(world, "/tasks", view="board")
    assert (board_column(board, "RUN-1"), board_column(board, "RUN-2")) == ("waiting", "waiting")
    assert groups(page(world, "/tasks", q="unassigned"))["none"][1] == 2

    # 시작하기 — 가져올 곳은 끝, 러너가 다음. 필수가 남아 사이드바에 보인다
    start = page(world, "/start")
    assert 'data-start-item="source" data-state="done"' in start
    assert 'data-start-item="runner" data-state="next"' in start
    assert 'href="/start"' in sidebar(start)


def test_02_connect_tabs_and_old_addresses(world):
    http = world.http
    for old, new in (("/operator/github", "/repos"), ("/agents", "/team"), ("/connect?tab=team", "/team"),
                     ("/kinds", "/settings?tab=kinds"), ("/operator/notifications", "/settings?tab=notify"),
                     ("/metrics", "/monitor"), ("/work/RUN-1", "/tasks?open=RUN-1")):
        response = http.get(old)
        assert (response.status_code, response.headers["location"]) == (303, new), old
    assert http.get("/team").status_code == 200  # phase 23: 303 이 아니라 화면
    sources = page(world, "/repos")
    assert f'data-source-card="{world.sources[REPO]}"' in sources
    settings = page(world, "/settings")
    assert all(f'data-tab="{t}"' in settings for t in ("kinds", "triage", "notify", "inbound", "advanced"))
    assert "설정됨" in page(world, "/settings", tab="notify")
    assert "모니터링" in page(world, "/monitor")


def test_03_attach_the_runner(world):
    issued = world.http.post(f"/operator/github/sources/{world.sources[REPO]}/runner")
    assert issued.status_code == 200, issued.text[:500]
    command = unescape(source_card(world, issued.text).split("data-runner-command", 1)[1])
    server, code = re.search(r"install-runner\.sh --server (\S+) --code (\S+) --repo <", command).groups()
    assert server == world.central_url

    py = sys.executable
    world.once("connector-setup", [
        py, "-m", "workflow.connector", "setup", "--server", server, "--code", code, "--repo", str(world.billing),
        "--tool", "codex", "--verify", f"check={py} -m pytest -q -p no:cacheprovider",
    ])
    (agent,) = q(world, "SELECT agent_id, name FROM agents WHERE local_registration_id = ?", REGISTRATION)
    world.ctx["agent_id"], world.ctx["agent_name"] = agent["agent_id"], agent["name"]
    assert agent["agent_id"] in page(world, "/team")  # 에이전트·러너는 팀 화면

    world.spawn("connector", [py, "-m", "workflow.connector", "run", "--adapter", "codex",
                              "--claim-interval", "0.5", "--heartbeat-interval", "1"], world.connector_env)
    deadline = time.monotonic() + 30
    # 카드 매칭(heartbeat) 뒤 첫 claim 이 지원 종류를 알려야 곧 맡기기가 바로 착수한다
    while "data-runner-missing" in source_card(world) or not q(
            world, "SELECT 1 FROM connectors WHERE supported_kinds_json IS NOT NULL"):
        assert time.monotonic() < deadline, world.log_tails()
        time.sleep(0.3)
    assert 'data-start-item="runner" data-state="done"' in page(world, "/start")


def test_04_panel_assigning_the_agent_delegates_the_work(world):
    agent_id = world.ctx["agent_id"]
    panel = panel_of(page(world, "/tasks", open="RUN-1"), "RUN-1")
    assert 'action="/work/RUN-1/assignee"' in panel and f'value="agent:{agent_id}"' in panel
    assert world.http.get("/work/RUN-1/panel").status_code == 200  # JS 가 끼우는 조각도 같은 업무

    assigned = world.http.post("/work/RUN-1/assignee", data={"assignee": f"agent:{agent_id}", "group": "assignee"})
    assert (assigned.status_code, assigned.headers["location"]) == (303, "/tasks?open=RUN-1"), assigned.text[:500]
    one = work(world, 1)
    assert (one["assignee_type"], one["assignee_id"]) == ("agent", agent_id)
    assert one["requested_by_member_id"] == world.ctx["admin_id"]
    assert one["status"] == "에이전트 작업 중", (one["status"], one["status_reason"])
    (task_id,) = [r["task_id"] for r in q(world, "SELECT task_id FROM tasks WHERE work_item_id = ?",
                                          one["work_item_id"])]
    world.tasks["A"] = task_id
    assert len(execs(world, task_id)) == 1  # 담당 = 곧 맡기기 — 실행이 생겼다

    # 목록: RUN-1 은 에이전트 묶음으로, "담당 없음" 은 1건. 보드: 에이전트 작업 중 칸
    listed = groups(page(world, "/tasks"))
    assert listed["none"] == ("담당 없음", 1, ["RUN-2"])
    assert listed[f"agent:{agent_id}"][2] == ["RUN-1"]
    assert board_column(page(world, "/tasks", view="board"), "RUN-1") == "agent_working"
    assert groups(page(world, "/tasks", q="agent_working")) == {f"agent:{agent_id}": listed[f"agent:{agent_id}"]}

    # 시작하기 필수 항목이 모두 끝나 사이드바에서 사라진다(주소는 남는다)
    start = page(world, "/start")
    for item in ("source", "runner", "delegate"):
        assert f'data-start-item="{item}" data-state="done"' in start, item
    assert "필수 항목을 모두 마쳤습니다." in start
    assert 'href="/start"' not in sidebar(page(world, "/tasks"))


def test_05_fix_review_draft_pr_moves_to_pr_review_and_the_notification_links_the_work(world):
    drive(world, lambda: received(world, "pr_opened"), "초안 PR · PR 확인 알림")
    (pr,) = world.fake.pulls.values()
    assert (pr["number"], pr["draft"], pr["head"]["ref"]) == (PR_NUMBER, True, "runloom/RUN-1")
    (note,) = received(world, "pr_opened")
    assert note["task_url"] == f"{world.central_url}/tasks?open=RUN-1"  # 알림 링크 = 업무 주소

    one = drive(world, lambda: (w := work(world, 1))["status"] == "PR · 검토" and w, "PR · 검토")
    assert one["status_reason"] == f"PR 확인 — #{PR_NUMBER}"
    assert board_column(page(world, "/tasks", view="board"), "RUN-1") == "pr_review"
    panel = panel_of(page(world, "/tasks", open="RUN-1"), "RUN-1")
    assert 'data-pull-request="open"' in panel and f"/pull/{PR_NUMBER}" in panel
    assert "data-detected" not in panel  # Runloom 이 연 PR 은 감지 PR 이 아니다
    assert q(world, "SELECT COUNT(*) FROM work_pull_requests")[0][0] == 0


def test_06_merging_the_pr_completes_the_work(world):
    world.fake.merge(PR_NUMBER)
    one = drive(world, lambda: (w := work(world, 1))["status"] == "완료" and w, "PR 병합 → 완료")
    assert one["closed_at"] is not None
    board = page(world, "/tasks", view="board")
    assert board_column(board, "RUN-1") == "done"
    listed = groups(page(world, "/tasks", group="status"))
    # 끝난 업무는 상태 묶기에서도 `완료` 묶음 대신 맨 아래 "끝난 업무" 묶음 (phase 23 step 8)
    assert listed["closed"] == ("끝난 업무", 1, ["RUN-1"]) and listed["status:새로 들어옴"][2] == ["RUN-2"]
    assert list(listed)[-1] == "closed" and "status:완료" not in listed


def test_07_direct_work_gives_a_branch_name_and_moves_the_board(world):
    admin_id = world.ctx["admin_id"]
    panel = panel_of(page(world, "/tasks", open="RUN-2"), "RUN-2")
    assert "data-direct-start" in panel and 'action="/work/RUN-2/direct"' in panel

    started = world.http.post("/work/RUN-2/direct", data={"group": "status", "view": "board"})
    assert started.status_code == 303, started.text[:500]
    location = urlsplit(started.headers["location"])
    assert location.path == "/tasks"
    assert parse_qs(location.query) == {"group": ["status"], "view": ["board"], "open": ["RUN-2"]}

    two = work(world, 2)
    assert (two["status"], two["assignee_type"], two["assignee_id"]) == ("직접 작업 중", "member", admin_id)
    assert (two["direct_member_id"], two["direct_branch"]) == (admin_id, DIRECT_BRANCH)
    html = page(world, "/tasks", view="board", open="RUN-2")
    assert board_column(html, "RUN-2") == "direct_working"
    panel = panel_of(html, "RUN-2")
    assert f'<code class="mono" data-branch>{DIRECT_BRANCH}</code>' in panel
    assert 'action="/work/RUN-2/direct/stop"' in panel
    assert groups(page(world, "/tasks"))[f"member:{admin_id}"] == (f"{E2E_ADMIN['display_name']} (나)", 1, ["RUN-2"])

    for _ in range(2):  # 워커는 직접 작업 업무를 착수하지 않는다
        world.worker.tick()
    assert q(world, "SELECT COUNT(*) FROM executions e JOIN tasks t ON t.task_id = e.task_id"
                    " WHERE t.work_item_id = ?", two["work_item_id"])[0][0] == 0


def test_08_a_pr_on_the_branch_is_detected_then_its_merge_completes_the_work(world):
    fake = world.fake
    with fake.lock:
        fake.pulls[DIRECT_PR] = {
            "number": DIRECT_PR, "html_url": f"https://github.com/{REPO}/pull/{DIRECT_PR}", "state": "open",
            "draft": False, "merged_at": None, "title": "반올림 고침", "body": "", "user": {"login": "kje"},
            "head": {"ref": DIRECT_BRANCH}, "base": {"ref": "main"}, "updated_at": utc_now(),
        }
    two = drive(world, lambda: (w := work(world, 2))["status"] == "PR · 검토" and w, "감지 PR → PR · 검토")
    assert two["status_reason"] == f"PR 확인 — #{DIRECT_PR}"
    (linked,) = q(world, "SELECT work_item_id, pr_number, head_branch, state FROM work_pull_requests")
    assert tuple(linked) == (two["work_item_id"], DIRECT_PR, DIRECT_BRANCH, "open")
    html = page(world, "/tasks", view="board", open="RUN-2")
    assert board_column(html, "RUN-2") == "pr_review"
    panel = panel_of(html, "RUN-2")
    assert 'data-pull-request="open"' in panel and "data-detected" in panel and f"/pull/{DIRECT_PR}" in panel

    fake.merge(DIRECT_PR)
    two = drive(world, lambda: (w := work(world, 2))["status"] == "완료" and w, "감지 PR 병합 → 완료")
    assert two["status_reason"] == f"PR 병합 — #{DIRECT_PR}" and two["closed_at"] is not None
    assert (two["direct_member_id"], two["direct_started_at"], two["direct_branch"]) == (None, None, None)
    assert board_column(page(world, "/tasks", view="board"), "RUN-2") == "done"
    types = [t for t, _ in work_events(world, 2)]
    assert types.count("direct_started") == 1 and types.count("pull_request_linked") == 1
    assert [d.get("reason") for t, d in work_events(world, 2) if t == "direct_stopped"] == ["closed"]
    assert q(world, "SELECT state FROM work_pull_requests")[0][0] == "merged"

    # 끝난 뒤 — 빠른 필터 "담당 없음" 은 비고, 둘 다 끝난 업무 묶음
    assert groups(page(world, "/tasks", group="status"))["closed"][1] == 2
    assert groups(page(world, "/tasks", q="unassigned")) == {}
    assert len(received(world, "pr_opened")) == 1  # 감지 PR 은 알리지 않는다
