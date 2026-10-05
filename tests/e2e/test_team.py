"""팀 한 줄기 e2e — 첫 설정부터 멤버가 맡긴 업무의 사람 요청 응답까지 (phase 15 step 11, ADR-0021).

`test_real_repo` 의 가짜 GitHub App(PR 경로 포함)·가짜 알림 수신·워커 연결을 다시 쓰고, 사람을 둘로 나눈다:
- **관리자** — 운영자 토큰으로 첫 설정(`/login/setup`) → GitHub 연결(가짜)·공용 알림 URL·재작업 상한 0 → 멤버 초대 링크 발급.
- **멤버(김멤버)** — 초대 링크로 가입·로그인 → 개인 웹훅 저장 → 카드 [러너 붙이기](러너 소유자 = 멤버) → #1 [맡기기]
  → 검토 수정 요청이 상한 0 이라 사람 요청 → 멤버의 "내 차례" 에만 보이고, 공용(`→ 김멤버`)·개인 경로로 알림 → 멤버 응답
  (응답자 기록) → 수정이 이어 돈다.
관리자 전용 화면을 멤버가 못 여는 것과 다른 Origin 의 POST 가 403 인 것을 하나씩 본다.

`WORKFLOW_E2E=1` 일 때만 돈다. 실제 GitHub·Discord·모델 호출 없음 — 네트워크는 127.0.0.1 뿐이다.
테스트 함수는 번호 순으로 이어지며 앞 단계의 상태(`world`)를 쓴다.
"""

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
    FakeGitHubPulls,
    Receiver,
    install_fake_codex,
    issue_task,
    make_worker,
    received,
    serve_receiver,
    source_card,
)
from workflow.server import worker as worker_module
from workflow.server.app import create_app
from workflow.server.settings import load_settings

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(os.environ.get("WORKFLOW_E2E") != "1", reason="WORKFLOW_E2E=1 일 때만"),
]

MEMBER = {"email": "kim@example.com", "display_name": "김멤버", "password": "e2e-member-password"}
SHARED_PATH, PERSONAL_PATH = "/hooks/shared", "/hooks/kim"
REGISTRATION = "billing"


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    workdir = tmp_path_factory.mktemp("team")
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
    # 첫 수정이 TODO(review) 를 남긴다 → 검토 수정 요청 → 재작업 상한 0 이라 사람 요청
    fake.add(REPO, gh_issue(REPO, 1, "쿠폰 중복 차감", labels=(), updated_at="2026-09-10T01:00:00Z",
                            body="쿠폰이 두 번 빠집니다. [scenario:coupon]"))
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
        "SESSION_SECRET": "e2e-session-secret-" + "t" * 20,
        "OPERATOR_TOKEN": "e2e-operator-token-" + "t" * 20,
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
    world.ctx.update(bare=bare, receiver=receiver, shared_url=hook_base + SHARED_PATH,
                     personal_url=hook_base + PERSONAL_PATH)

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
            receiver_server.shutdown()
            root.removeHandler(log_handler)
            root.setLevel(old_level)
            log_handler.close()


def member_http(world: World) -> httpx.Client:
    return world.ctx["member"]


def bodies_at(world: World, path: str, event: str) -> list[dict]:
    with world.ctx["receiver"].lock:
        return [body for p, body in world.ctx["receiver"].received if p == path and body.get("event") == event]


# --- 시나리오 ------------------------------------------------------------------------------


def test_01_first_setup_with_the_token_then_admin_connects_github(world):
    http = world.http
    assert 'name="token"' in http.get("/login").text  # 첫 설정 전 — 토큰 칸
    login = log_in(http, world.central_env["OPERATOR_TOKEN"])
    assert login.status_code == 303, login.text[:300]
    assert 'name="token"' not in httpx.get(f"{world.central_url}/login").text  # 설정 뒤 — 이메일·비밀번호

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

    # 재작업 상한 0 — 검토 수정 요청이 바로 사람 요청이 된다
    fields = ("repository_full_name", "intake", "workflow_repository_id", "label_filter", "selected_issue_numbers",
              "start_at", "fix_verification_profile_id", "review_agent_id", "run_mode", "trigger_label",
              "default_fix_agent_id", "enabled")
    updated = http.put(f"/github/sources/{source['source_id']}", json={
        **{k: source[k] for k in fields}, "max_rework_rounds": 0, "expected_revision": source["config_revision"]})
    assert updated.status_code == 200, updated.text[:500]

    saved = http.post("/operator/notifications/webhook", data={"url": world.ctx["shared_url"]})
    assert saved.status_code in (200, 303), saved.text[:500]

    world.worker = make_worker(world)
    report = world.worker.tick()
    assert report.sync_errors == 0 and report.issues_created == 1 and report.tasks_started == 0


def test_02_admin_invites_a_member_who_joins_and_logs_in(world):
    issued = world.http.post("/team/invites", data={"role": "member"})
    assert issued.status_code == 200, issued.text[:500]
    link = re.search(rf"{re.escape(world.central_url)}/invite/([A-Za-z0-9_-]+)", issued.text)
    assert link, issued.text[:1000]
    world.ctx["invite_token"] = link.group(1)

    member = member_http(world)
    joined = member.post(f"/invite/{link.group(1)}", data=MEMBER)
    assert joined.status_code == 303, joined.text[:500]
    member.cookies.clear()  # 가입 세션을 버리고 이메일·비밀번호로 다시 로그인
    login = member.post("/login", data={"email": MEMBER["email"], "password": MEMBER["password"]})
    assert login.status_code == 303, login.text[:500]
    (row,) = q(world, "SELECT member_id, role, email, disabled_at FROM members WHERE email = ?", MEMBER["email"])
    assert (row["role"], row["disabled_at"]) == ("member", None)
    world.ctx["member_id"] = row["member_id"]
    assert MEMBER["display_name"] in member.get("/tasks").text  # 사이드바의 표시 이름

    # 관리자 전용 경로는 멤버에게 403 — 팀 탭은 열리지만 멤버·초대 절은 관리자만(phase 16)
    forbidden = member.get("/settings?tab=notify")
    assert forbidden.status_code == 403, forbidden.status_code
    team_tab = member.get("/team")
    assert team_tab.status_code == 200 and 'action="/team/invites"' not in team_tab.text
    assert member.post("/team/invites", data={"role": "admin"}).status_code == 403

    saved = member.post("/me/webhook", data={"url": world.ctx["personal_url"]})
    assert saved.status_code in (200, 303), saved.text[:500]
    assert 'data-personal-webhook="true"' in member.get("/me").text


def test_03_member_attaches_the_runner_and_owns_it(world):
    member = member_http(world)
    issued = member.post(f"/operator/github/sources/{world.sources[REPO]}/runner")
    assert issued.status_code == 200, issued.text[:500]
    command = unescape(source_card(world, issued.text).split("data-runner-command", 1)[1])
    server, code = re.search(r"install-runner\.sh --server (\S+) --code (\S+) --repo <", command).groups()
    assert server == world.central_url

    py = sys.executable
    world.once("connector-setup", [
        py, "-m", "workflow.connector", "setup", "--server", server, "--code", code, "--repo", str(world.billing),
        "--tool", "codex", "--verify", f"check={py} -m pytest -q -p no:cacheprovider",
    ])
    (connector,) = q(world, "SELECT connector_id, owner_member_id FROM connectors")
    assert connector["owner_member_id"] == world.ctx["member_id"]
    (agent,) = q(world, "SELECT agent_id FROM agents WHERE local_registration_id = ?", REGISTRATION)
    world.ctx["agent_id"] = agent["agent_id"]
    assert f"소유자 {MEMBER['display_name']}" in world.http.get("/team").text  # 관리자도 소유자를 본다

    world.spawn("connector", [py, "-m", "workflow.connector", "run", "--adapter", "codex",
                              "--claim-interval", "0.5", "--heartbeat-interval", "1"], world.connector_env)
    deadline = time.monotonic() + 30
    while "data-runner-missing" in source_card(world):
        assert time.monotonic() < deadline, world.log_tails()
        time.sleep(0.3)


def test_04_member_delegates_and_the_review_request_is_only_the_members_turn(world):
    member = member_http(world)
    a_id = issue_task(world, 1)
    world.tasks["A"] = a_id
    assert member.post(f"/tasks/{a_id}/delegate").status_code == 303
    (work,) = q(world, "SELECT * FROM work_items")
    assert work["requested_by_member_id"] == world.ctx["member_id"]

    requests = drive(world, lambda: [r for r in member.get("/human-requests").json()["requests"]
                                     if r["task_id"] == a_id and r["code"] == "rework_limit_reached"],
                     "검토 수정 요청 → 사람 요청")
    world.ctx["request"] = requests[0]
    assert len(execs(world, a_id)) == 1  # 자동 재작업 없음
    world.worker.tick()
    assert q(world, "SELECT status FROM work_items")[0]["status"] == "내 차례"

    link = f'class="work-row" data-work-key="RUN-{work["key_number"]}"'  # 업무 화면 표 한 줄 (phase 16)
    assert link in member.get("/tasks", params={"view": "my_turn"}).text
    assert link not in world.http.get("/tasks", params={"view": "my_turn"}).text  # 관리자의 내 차례엔 없다
    assert link in world.http.get("/tasks").text  # 전체에는 있다

    shared = drive(world, lambda: bodies_at(world, SHARED_PATH, "human_request"), "공용 알림")
    personal = drive(world, lambda: bodies_at(world, PERSONAL_PATH, "human_request"), "개인 알림")
    assert len(shared) == 1 and len(personal) == 1
    assert shared[0]["content"].splitlines()[0].endswith(f"→ {MEMBER['display_name']}")
    assert "→" not in personal[0]["content"]
    for _ in range(2):  # 다시 돌아도 알림이 늘지 않는다
        world.worker.tick()
    assert len(received(world, "human_request")) == 2
    rows = q(world, "SELECT channel, recipient_member_id, state FROM notifications WHERE event = 'human_request'"
                    " ORDER BY channel")
    assert [tuple(r) for r in rows] == [("personal", world.ctx["member_id"], "sent"),
                                        ("shared", world.ctx["member_id"], "sent")]
    listed = member.get("/me").text  # 내가 받는 최근 알림
    assert "human_request" in listed or "사람 요청" in listed


def test_05_member_answers_the_request_and_work_continues(world):
    member = member_http(world)
    a_id, request = world.tasks["A"], world.ctx["request"]
    response = member.post(f"/human-requests/{request['request_id']}/responses", json={
        "response_id": "resp-team-1", "expected_revision": request["revision"], "action": "resume",
        "text": "한 번 더 고쳐 주세요"})
    assert response.status_code == 200, response.text[:500]
    (answered,) = q(world, "SELECT member_id FROM human_responses WHERE response_id = 'resp-team-1'")
    assert answered["member_id"] == world.ctx["member_id"]
    assert f"<span data-responder>{MEMBER['display_name']}</span>" in member.get(f"/tasks/{a_id}").text

    # 응답이 새 revision 을 만들고, 결과를 기다리던 수정이 그 revision 으로 다시 착수해 러너가 돌린다
    first, second = drive(world, lambda: len(runs := execs(world, a_id)) >= 2 and runs,
                          "응답 뒤 수정이 이어 돈다")[:2]
    assert second["start_key"] == f"auto:{a_id}:r2" and second["execution_id"] != first["execution_id"]
    drive(world, lambda: q(world, "SELECT result_artifact_id FROM executions WHERE execution_id = ?",
                           second["execution_id"])[0][0], "이어 돈 수정의 결과")


def test_06_a_post_from_another_origin_is_rejected(world):
    member = member_http(world)
    before = q(world, "SELECT display_name FROM members WHERE member_id = ?", world.ctx["member_id"])[0][0]
    forged = member.post("/me/profile", data={"display_name": "바뀐 이름"},
                         headers={"Origin": "https://evil.example", "Accept": "application/json"})
    assert forged.status_code == 403 and forged.json()["code"] == "forbidden_origin", forged.text[:300]
    assert q(world, "SELECT display_name FROM members WHERE member_id = ?", world.ctx["member_id"])[0][0] == before


def test_07_team_secrets_stay_out_of_the_db_logs_and_pages(world):
    needles = {"member_password": MEMBER["password"], "invite": world.ctx["invite_token"],
               "personal_hook": PERSONAL_PATH, "shared_hook": SHARED_PATH,
               "login_cookie": member_http(world).cookies.get("wf_login")}
    conn = world.conn()
    try:
        dump = "\n".join(conn.iterdump())
    finally:
        conn.close()
    leaked = [name for name, value in needles.items() if value and value in dump]
    for path in (world.workdir / "logs").glob("*.log"):
        # 이 테스트의 httpx 클라이언트가 남긴 요청 줄은 뺀다 — 중앙(uvicorn.access)이 남긴 줄만 본다
        text = "\n".join(line for line in path.read_text(encoding="utf-8", errors="replace").splitlines()
                         if not line.startswith(f"httpx INFO HTTP Request: POST {world.central_url}/"))
        leaked += [f"{name}@{path.name}" for name, value in needles.items() if value and value in text]
    for page in ("/tasks", "/me", "/team", "/settings?tab=advanced", "/repos"):
        body = member_http(world).get(page).text
        leaked += [f"{name}@{page}" for name, value in needles.items() if value and value in body]
    assert leaked == []
