"""사람용 API 끝에서 끝까지 — 멤버 토큰·`/api/v1` 업무 API·러너 새 경로 (phase 24 step 9, ADR-0032).

서버는 FastAPI `TestClient`, 러너는 연결 토큰(`wfc_…`)으로 `/api/v1/connector/claim`·이벤트·산출물을 직접 부르는 가짜다.
워커는 같은 DB 를 여는 `Worker` 를 테스트가 tick 한다. 실제 러너·n8n·GitHub·모델 호출 없음.

한 줄기:
1. 관리자 A·멤버 B 가 `/me` 화면 폼으로 토큰을 만든다(A full, B read) — 원문은 그 응답에서만 읽는다.
2. 쿠키 없이 Bearer 만으로 — B 의 읽기 전용 토큰으로 내 차례 목록·상세(담당 후보·행동), 쓰기는 403 `token_read_only`.
3. A 의 토큰으로 RUN-1 을 에이전트에게 맡김 → 가짜 러너가 새 경로로 받아 결과를 올림 → 워커 판정 → 웹 목록과 API 목록의 상태가 같다.
4. A 의 토큰으로 사람 요청 응답. B 비활성화 → B 토큰 401(다시 활성화해도 401). A 가 자기 토큰 폐기 → 401.
5. 옛 JSON 경로 404, 화면 폼 경로·`/healthz` 그대로. 마지막에 DB·로그·응답 어디에도 토큰 원문이 없다.

`TestClient` 만 쓰므로 `WORKFLOW_E2E` 없이 기본 실행에 든다. 테스트 함수는 번호 순으로 이어지며 앞 단계의 상태(`world`)를 쓴다.
"""

import dataclasses
import hashlib
import json
import logging
import re

import pytest
from fastapi.testclient import TestClient

from tests.workflow.server.conftest import _settings, bearer, event, exchange, log_in, log_in_member, meta_for
from tests.workflow.server.test_app import flat_routes
from tests.workflow.server.test_github_sync import config
from tests.workflow.server.test_task_cycle import (
    ALL_KINDS,
    BASE,
    C1,
    FIX,
    FIX_SHOP,
    REG_FIX,
    REG_REVIEW,
    REG_SHOP,
    REVIEW,
    NoCallbacks,
    _agent,
    import_issue,
    seen_at,
)
from workflow.adapters import repo
from workflow.adapters.artifact_store import ArtifactStore
from workflow.adapters.db import connect
from workflow.domain import team
from workflow.server.app import create_app
from workflow.server.auth import SELFHOST_SESSION_ID, ensure_workspace, utc_now
from workflow.server.worker import Worker

pytestmark = [pytest.mark.e2e]

SESSION = SELFHOST_SESSION_ID
NOW = "2026-10-06T12:00:00Z"
API = "/api/v1/work-items"
ISSUED = re.compile(r'<code id="issued-member-token">(wfm_[^<]+)</code>')


@dataclasses.dataclass
class World:
    app: object
    conn: object
    worker: Worker
    admin: TestClient  # A — 로그인 쿠키
    member: TestClient  # B — 로그인 쿠키
    connector_id: str
    runner: dict  # 연결 토큰 Bearer 헤더
    responses: list = dataclasses.field(default_factory=list)  # (방법, 경로, 상태, 본문) — 발급 응답 표시 포함
    logs: list = dataclasses.field(default_factory=list)
    tokens: dict = dataclasses.field(default_factory=dict)  # "a"·"b" → 원문
    ctx: dict = dataclasses.field(default_factory=dict)


class _Capture(logging.Handler):
    def __init__(self, sink: list):
        super().__init__(logging.DEBUG)
        self.sink = sink

    def emit(self, record: logging.LogRecord) -> None:
        self.sink.append(self.format(record))


def _recording(client: TestClient, world: World) -> TestClient:
    """client 의 모든 응답을 world.responses 에 남긴다 — 마지막 비밀값 단정의 재료."""
    def hook(response):
        response.read()
        world.responses.append((response.request.method, response.request.url.path, response.status_code,
                                response.text))
    client.event_hooks = {"request": [], "response": [hook]}
    return client


def bearer_only(world: World, token: str) -> TestClient:
    """쿠키·Origin 없이 `Authorization: Bearer` 만 싣는 클라이언트 — CLI·스크립트와 같은 모양."""
    client = TestClient(world.app)
    del client.headers["origin"]
    client.headers.update(bearer(token))
    return _recording(client, world)


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("human-api")
    settings = dataclasses.replace(_settings(tmp), github_repos=("acme/billing",))
    sink: list = []
    handler = _Capture(sink)
    handler.setFormatter(logging.Formatter("%(name)s %(levelname)s %(message)s"))
    root = logging.getLogger()
    old_level = root.level
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(team, "SCRYPT_N", 2**4)  # server conftest 의 _cheap_scrypt 와 같다
        app = create_app(settings)
        conn = connect(settings.db_path)
        try:
            # test_task_cycle.cycle 과 같은 워크스페이스 — 러너 billing(수정·검토)·shop, 소스는 지시 전 이슈(all_open)
            ensure_workspace(conn, NOW)
            for agent in (_agent(FIX, REG_FIX, "code.fix", "billing"),
                          _agent(REVIEW, REG_REVIEW, "code.review", "billing"),
                          _agent(FIX_SHOP, REG_SHOP, "code.fix", "shop")):
                repo.upsert_agent(conn, agent)
                repo.register_session_agent(conn, SESSION, agent["agent_id"], NOW)
            plain = TestClient(app)
            billing, runner_token = exchange(plain, conn)
            shop, _ = exchange(plain, conn)
            for registration, connector, repository, profiles in (
                (REG_FIX, billing, "billing", ["vp-pytest"]),
                (REG_REVIEW, billing, "billing", []),
                (REG_SHOP, shop, "shop", ["vp-shop"]),
            ):
                repo.update_registration(conn, registration, connector_id=connector, repository_id=repository,
                                         base_commit=BASE, verification_profile_ids=profiles, discovered={}, now=NOW)
            seen_at(conn, max(NOW, utc_now()))
            for connector in (billing, shop):
                repo.record_supported_kinds(conn, connector, ALL_KINDS)
            repo.save_github_source(conn, SESSION, config(review_agent_id=REVIEW, intake="all_open", label_filter=[],
                                                          trigger_label="runloom"), NOW)
            tasks = (import_issue(conn, 1, labels=[]), import_issue(conn, 2, labels=[]))  # RUN-1 · RUN-2

            world = World(app=app, conn=conn,
                          worker=Worker(lambda: connect(settings.db_path), ArtifactStore(settings.artifact_dir),
                                        NoCallbacks(), settings, utc_now),
                          admin=None, member=None, connector_id=billing, runner=bearer(runner_token), logs=sink)
            world.admin = _recording(log_in(TestClient(app)), world)
            world.member = _recording(log_in_member(TestClient(app), display_name="김멤버"), world)
            world.ctx.update(tasks=tasks, runner_token=runner_token,
                             a_id=repo.find_member_by_email(conn, SESSION, "admin@example.com")["member_id"],
                             b_id=next(m["member_id"] for m in repo.list_members(conn, SESSION)
                                       if m["display_name"] == "김멤버"))
            yield world
        finally:
            conn.close()
            root.removeHandler(handler)
            root.setLevel(old_level)


def issue_on_me(world: World, client: TestClient, name: str, scope: str) -> str:
    response = client.post("/me/tokens", data={"name": name, "scope": scope}, follow_redirects=False)
    assert response.status_code == 200, response.text
    world.ctx.setdefault("issued_bodies", []).append(response.text)
    (token,) = ISSUED.findall(response.text)
    return token


def status_of(response, status: int) -> dict:
    assert response.status_code == status, response.text
    return response.json()


def web_statuses(client: TestClient) -> dict[str, str]:
    """웹 업무 목록(`/tasks?closed=all`)의 행별 상태 배지."""
    html = client.get("/tasks?closed=all").text
    return dict(re.findall(r'<tr class="work-row" data-work-key="(RUN-\d+)">.*?data-status="([^"]+)"', html, re.S))


def api_statuses(client: TestClient) -> dict[str, str]:
    items = status_of(client.get(f"{API}?closed=all"), 200)["items"]
    return {item["key"]: item["status"] for item in items}


def run_fake_runner(world: World) -> str:
    """가짜 러너 — 새 경로로 받아 수정 결과(CONTRACT 13.5)를 올린다. 반환은 실행 ID."""
    claim = {"contract_version": 1, "connector_id": world.connector_id, "supported_kinds": ALL_KINDS}
    runner = TestClient(world.app)
    del runner.headers["origin"]
    runner = _recording(runner, world)
    request = status_of(runner.post("/api/v1/connector/claim", json=claim, headers=world.runner), 200)
    execution_id = request["execution_id"]

    def post_event(seq: int, type_: str, data: dict) -> None:
        response = runner.post(f"/api/v1/executions/{execution_id}/events",
                               json=event(execution_id, seq, type_, data), headers=world.runner)
        assert response.status_code == 200, response.text

    def upload(kind: str, data: bytes, content_type: str = "text/plain") -> str:
        meta = meta_for(data, kind=kind, name=f"{kind}.txt", content_type=content_type)
        response = runner.post(f"/api/v1/executions/{execution_id}/artifacts", data={"meta": json.dumps(meta)},
                               files={"file": (meta["name"], data, content_type)}, headers=world.runner)
        assert response.status_code in (200, 201), response.text
        return response.json()["artifact_id"]

    post_event(1, "accepted", {})
    post_event(2, "started", {"runtime_ref": "pid:1"})
    ids = {kind: upload(kind, data) for kind, data in (
        ("diff", b"--- a/coupon.py\n+++ b/coupon.py\n"),
        ("test_log_before", b"exit_code=1\nFAILED tests/test_coupon.py\n"),
        ("test_log_after", b"exit_code=0\n1 passed\n"),
        ("verification_log", b"exit_code=0\n12 passed\n"),
    )}
    body = {
        "contract_version": 1, "execution_id": execution_id, "task_id": request["task_id"],
        "outcome": "ready_for_review", "summary": "쿠폰 중복 적용 수정", "base_commit": request["target"]["base_commit"],
        "result_commit": C1, "artifact_ids": list(ids.values()),
        "verification": {"profile_id": request["target"]["verification_profile_id"], "result_commit": C1,
                         "exit_code": 0, "log_artifact_id": ids["verification_log"]},
    }
    result_id = upload("code_change_result", json.dumps(body).encode(), "application/json")
    post_event(3, "result_ready", {"result_artifact_id": result_id})
    return execution_id


# --- 1. 토큰 발급 ------------------------------------------------------------------------------------------


def test_01_admin_and_member_issue_tokens_on_the_me_screen(world):
    world.tokens["a"] = issue_on_me(world, world.admin, "A 노트북 CLI", "full")
    world.tokens["b"] = issue_on_me(world, world.member, "B 대시보드", "read")
    assert all(t.startswith("wfm_") for t in world.tokens.values())

    rows = {r["member_id"]: r for r in world.conn.execute("SELECT * FROM member_tokens")}
    assert {(r["member_id"], r["scope"]) for r in rows.values()} == {
        (world.ctx["a_id"], "full"), (world.ctx["b_id"], "read")}
    for token in world.tokens.values():  # 저장은 sha256 만
        assert hashlib.sha256(token.encode()).hexdigest() in {r["token_sha256"] for r in rows.values()}

    # 발급 뒤 /me 다시 보기에는 원문이 없다 — 이름·권한만
    again = world.member.get("/me").text
    assert world.tokens["b"] not in again and "B 대시보드" in again


# --- 2. 읽기 전용 토큰 ---------------------------------------------------------------------------------------


def test_02_read_token_lists_my_turn_and_reads_the_detail_without_a_cookie(world):
    a = bearer_only(world, world.tokens["a"])
    b = bearer_only(world, world.tokens["b"])
    assert "wf_login" not in b.cookies

    # A 가 RUN-2 를 B 에게 맡기고(토큰), 수정 단계가 정보를 묻는다 → B 의 내 차례
    status_of(a.put(f"{API}/RUN-2/assignee", json={"type": "member", "id": world.ctx["b_id"]}), 200)
    request_id, _ = repo.create_human_request_once(world.conn, world.ctx["tasks"][1], "fix_needs_information",
                                                   "재현 금액이 필요합니다", "fix_needs_information:e2e", NOW)
    world.ctx["request_id"] = request_id

    mine = status_of(b.get(f"{API}?filter=my_turn"), 200)
    assert [item["key"] for item in mine["items"]] == ["RUN-2"]
    assert mine["items"][0]["my_turn"] is True and mine["counts"]["my_turn"] == 1

    detail = status_of(b.get(f"{API}/RUN-2"), 200)
    assert detail == status_of(world.member.get(f"{API}/RUN-2"), 200)  # 토큰과 쿠키가 같은 상세
    candidates = {(c["type"], c["id"]) for c in detail["assignee_candidates"]}
    assert {("member", world.ctx["a_id"]), ("member", world.ctx["b_id"]), ("agent", FIX)} <= candidates
    fix = next(c for c in detail["assignee_candidates"] if c["id"] == FIX)
    assert (fix["online"], fix["needs_owner_approval"]) == (True, False)
    assert {"assign", "set_priority"} <= set(detail["actions"])
    assert [r["request_id"] for r in detail["open_requests"]] == [request_id]


def test_03_read_token_cannot_change_anything(world):
    b = bearer_only(world, world.tokens["b"])
    before = repo.work_item_of_task(world.conn, world.ctx["tasks"][0])
    body = status_of(b.put(f"{API}/RUN-1/assignee", json={"type": "agent", "id": FIX}), 403)
    assert body["code"] == "token_read_only"
    assert repo.work_item_of_task(world.conn, world.ctx["tasks"][0]) == before
    assert repo.list_executions(world.conn, world.ctx["tasks"][0]) == []


# --- 3. 에이전트에게 맡김 → 러너 새 경로 → 결과 -------------------------------------------------------------


def test_04_full_token_hands_over_to_an_agent_and_the_runner_claims_on_the_new_path(world):
    a = bearer_only(world, world.tokens["a"])
    detail = status_of(a.put(f"{API}/RUN-1/assignee", json={"type": "agent", "id": FIX, "note": "쿠폰만"}), 200)
    assert detail["work"]["assignee"]["id"] == FIX and detail["work"]["status"] == "에이전트 작업 중"

    execution_id = run_fake_runner(world)
    assert repo.list_executions(world.conn, world.ctx["tasks"][0])[0]["execution_id"] == execution_id
    world.worker.tick()  # 판정 → 검토 단계

    task = repo.get_task(world.conn, world.ctx["tasks"][0])
    assert (task["status"], task["status_reason"]) == ("확인 필요", "검토 대기")  # 수정 단계 — 검증 통과, 검토 단계 생김
    work = repo.work_item_of_task(world.conn, world.ctx["tasks"][0])
    assert (work["status"], work["status_reason"]) == ("내 차례", "검토 대기")  # 업무 상태(work_status)
    web, api = web_statuses(world.admin), api_statuses(a)
    assert web == api  # 웹 목록과 API 목록이 같은 상태
    assert api["RUN-1"] == "내 차례"
    assert api_statuses(world.admin) == api  # 쿠키로 부른 API 도 같다
    item = next(i for i in status_of(a.get(API), 200)["items"] if i["key"] == "RUN-1")
    assert item["status_reason"] == "검토 대기"


# --- 4. 사람 요청 응답 · 비활성화 · 폐기 -------------------------------------------------------------------------


def test_05_full_token_answers_a_human_request(world):
    a = bearer_only(world, world.tokens["a"])
    (open_request,) = status_of(a.get("/api/v1/human-requests"), 200)["requests"]
    assert open_request["request_id"] == world.ctx["request_id"]
    answered = status_of(a.post(f"/api/v1/human-requests/{world.ctx['request_id']}/responses", json={
        "response_id": "resp-e2e-1", "expected_revision": open_request["revision"], "action": "resume",
        "text": "10,000원"}), 200)
    assert answered["created"] is True
    assert status_of(a.get("/api/v1/human-requests"), 200)["requests"] == []
    b = bearer_only(world, world.tokens["b"])
    assert status_of(b.get(f"{API}?filter=my_turn"), 200)["items"] == []


def test_06_disabling_the_member_and_revoking_the_token_end_api_access(world):
    b = bearer_only(world, world.tokens["b"])
    unauthorized = {"code": "unauthenticated", "message": "유효한 멤버 토큰이 필요합니다."}

    assert world.admin.post(f"/team/members/{world.ctx['b_id']}/disable", follow_redirects=False).status_code == 303
    body = status_of(b.get(API), 401)
    assert {k: body[k] for k in unauthorized} == unauthorized
    assert world.admin.post(f"/team/members/{world.ctx['b_id']}/enable", follow_redirects=False).status_code == 303
    status_of(b.get(API), 401)  # 비활성화가 폐기한 토큰은 되살아나지 않는다

    a = bearer_only(world, world.tokens["a"])
    status_of(a.get(API), 200)
    (mine,) = status_of(world.admin.get("/api/v1/me/tokens"), 200)["tokens"]
    revoke = world.admin.post(f"/me/tokens/{mine['token_id']}/revoke", follow_redirects=False)
    assert revoke.status_code == 303
    status_of(a.get(API), 401)
    status_of(a.put(f"{API}/RUN-2/assignee", json={"type": "agent", "id": FIX}), 401)


# --- 5. 옛 경로 · 화면 경로 ------------------------------------------------------------------------------------


def test_07_old_json_paths_are_gone_and_screen_paths_stay(world):
    runner = bearer_only(world, world.ctx["runner_token"])
    claim = {"contract_version": 1, "connector_id": world.connector_id}
    assert world.admin.get("/human-requests").status_code == 404
    assert world.admin.get("/metrics.json").status_code == 404
    assert runner.post("/connector/claim", json=claim).status_code == 404  # Bearer — Origin 검사 통과 뒤 라우팅
    assert world.admin.post("/connector/claim", json=claim).status_code == 404  # 쿠키 + Origin
    inbound = bearer_only(world, "wfs_e2e")
    assert inbound.post("/sources/n8n/chains", json={}).status_code == 404
    # Origin·Bearer 둘 다 없으면 라우팅 전에 origin_guard 가 막는다
    bare = TestClient(world.app)
    del bare.headers["origin"]
    assert bare.post("/connector/claim", json=claim).json()["code"] == "forbidden_origin"

    # 화면·화면 폼 경로는 그대로 — 같은 접두어의 JSON 이 옮겨 가도 폼은 남는다
    forms = {(m, path) for r, path in flat_routes(world.app) if getattr(r, "endpoint", None) is not None
             and r.endpoint.__module__ == "workflow.server.web" for m in getattr(r, "methods", ())}
    assert {("POST", "/responsibilities/add"), ("POST", "/responsibilities/remove"), ("POST", "/me/tokens"),
            ("POST", "/work/{key}/assignee"), ("POST", "/sources/tokens"), ("GET", "/tasks"),
            ("GET", "/me")} <= forms
    # 빈 폼은 라우트까지 가서 폼 검증(422)에 걸린다 — 404 가 아니다
    assert world.admin.post("/responsibilities/add", data={}, follow_redirects=False).status_code == 422
    for path, status in (("/tasks", 200), ("/me", 200), ("/work/RUN-1", 303)):  # /work/{key} → /tasks?open=
        assert world.admin.get(path, follow_redirects=False).status_code == status, path
    health = world.admin.get("/healthz")
    assert health.status_code == 200 and health.json()["status"] == "ok"


# --- 6. 비밀값 ---------------------------------------------------------------------------------------------


def test_08_no_raw_token_in_the_db_logs_or_responses(world):
    raw = list(world.tokens.values())
    assert len(raw) == 2

    # DB 모든 표·모든 칸
    tables = [name for (name,) in world.conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")]
    for table in tables:
        for row in world.conn.execute(f'SELECT * FROM "{table}"'):  # noqa: S608 — sqlite_master 의 표 이름
            for value in tuple(row):
                for token in raw:
                    assert token not in str(value), table

    # 로그 캡처 — 앱·워커·인증의 모든 기록
    assert world.logs
    for line in world.logs:
        for token in raw:
            assert token not in line

    # 모든 응답(JSON·HTML) — 발급 화면 응답 둘만 원문을 싣는다
    issued = world.ctx["issued_bodies"]
    assert len(issued) == 2
    checked = 0
    for method, path, status, text in world.responses:
        if (method, path) == ("POST", "/me/tokens") and text in issued:
            continue
        checked += 1
        for token in raw:
            assert token not in text, (method, path, status)
    assert checked > 20
