"""중앙 워커 — ARCHITECTURE "계약 수용 기준" 표를 한 행씩. 진단 API 는 `FakeDiagServer` + MockTransport 로 흉내 낸다.

시계는 고정 문자열을 돌려주는 `Clock`. 첨부·결과·조회 이력은 Step 3 테스트의 fixture(`make_demo_attachments`)를
재사용하고, 진단 API 쪽 산출물 ID(`diag-…`)가 중앙 ID(`art-…`)로 치환되는지도 여기서 확인한다.
"""

import dataclasses
import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from tests.workflow.domain.test_verification import (
    contract_results,
    demo_sources,
    make_demo_attachments,
    make_trace,
    with_hashes,
)
from workflow.adapters import repo
from workflow.adapters.db import connect
from workflow.adapters.diag_client import HttpDiagClient
from workflow.contracts.v1 import (
    ArtifactMeta,
    ExecutionEvent,
    ExecutionRequest,
    HandoffBundle,
    SelectionRecord,
)
from workflow.server.auth import SESSION_COOKIE, sign_session
from workflow.server.worker import TickReport, Worker, assemble_handoff

from .conftest import (
    BASE_COMMIT,
    LOCAL_REGISTRATION,
    SESSION,
    TASK_A,
    TASK_B,
    bearer,
    code_change_result,
    event,
    exchange,
    meta_for,
    seed_agents,
    seed_execution,
    task_row,
)

EXEC_A = "exec-diagnose-001"
TOKEN = "test-diag-token"
CAP_A = {"code": "operations.diagnose", "scope": {"workflow_id": "daily-report"}}
CAP_B = {"code": "code.modify", "scope": {"repository_id": "demo-report-repo"}}
REQUIRED_KINDS = ("diff", "test_log_before", "test_log_after", "report_output", "verification_log")
REPORT_TEXT = """일일 업무 보고서 — 2026-09-19

팀      완료  미완료
운영    12    3
개발    8     2
합계    20    5
"""


# --- 시계·진단 API 흉내 -------------------------------------------------------------------


class Clock:
    def __init__(self, now: str = "2026-09-20T00:00:00Z"):
        self.now = now

    def __call__(self) -> str:
        return self.now

    def advance(self, seconds: int) -> None:
        moved = datetime.fromisoformat(self.now) + timedelta(seconds=seconds)
        self.now = moved.astimezone(UTC).isoformat().replace("+00:00", "Z")


def diag_data(sources=None, *, result_raw=None, drop_from_trace=(), execution_id=EXEC_A) -> dict:
    """진단 API 가 내줄 결과·조회 이력·첨부. 결과의 산출물 ID 는 진단 API 쪽 ID(`diag-…`)다."""
    attachments = make_demo_attachments(sources)
    raw = with_hashes(contract_results()[0] if result_raw is None else result_raw, attachments)
    raw["execution_id"] = execution_id
    raw["task_id"] = TASK_A
    for ref in raw["attachments"]:
        ref["artifact_id"] = f"diag-ev-{ref['evidence_id']}"
    raw["provenance"]["tool_trace_artifact_id"] = "diag-art-trace"
    entries = [
        {
            "call_id": t.call_id, "tool": t.tool, "input": t.input, "ok": t.ok, "error": None,
            "returned": [
                {"evidence_id": k[0], "version": k[1], "sha256": attachments[k].sha256}
                for k in t.returned
            ],
        }
        for t in make_trace(attachments)
        if t.returned[0] not in drop_from_trace
    ]
    trace = {"contract_version": 1, "tool_contract_version": "tools-v1", "entries": entries}
    artifacts = {
        "diag-art-result": (json.dumps(raw, ensure_ascii=False).encode(), "application/json"),
        "diag-art-trace": (json.dumps(trace, ensure_ascii=False).encode(), "application/json"),
        **{f"diag-ev-{k[0]}": (le.content, le.content_type) for k, le in attachments.items()},
    }
    return {"result": raw, "artifacts": artifacts}


def _diag_event(execution_id: str, seq: int, type_: str, data: dict) -> dict:
    return {
        "contract_version": 1, "execution_id": execution_id, "seq": seq,
        "occurred_at": f"2026-09-20T00:10:0{seq}Z", "type": type_, "data": data,
    }


class FakeDiagServer:
    """CONTRACT 1절. `visible` 까지의 이벤트만 내준다. 접수는 202, 재접수는 200."""

    def __init__(self, data: dict, execution_id: str = EXEC_A):
        self.artifacts: dict[str, tuple[bytes, str]] = dict(data["artifacts"])
        self.events = [
            _diag_event(execution_id, 1, "accepted", {}),
            _diag_event(execution_id, 2, "started", {"runtime_ref": "diag-run-7f3a"}),
            _diag_event(execution_id, 3, "progress", {"message": "get_run daily-0920-0900 조회 완료"}),
            _diag_event(execution_id, 4, "result_ready", {"result_artifact_id": "diag-art-result"}),
        ]
        self.visible = 1
        self.submit_mode = "ok"  # ok | limit | conflict
        self.down = False
        self.calls: list[tuple[str, str]] = []
        self.submitted: dict[str, dict] = {}

    def posts(self) -> list[tuple[str, str]]:
        return [c for c in self.calls if c[0] == "POST"]

    def _status(self, execution_id: str, after_seq: int) -> dict:
        visible = self.events[: self.visible]
        last = visible[-1]["type"]
        status = {"accepted": "accepted", "started": "running", "progress": "running"}.get(last, last)
        return {
            "execution_id": execution_id,
            "status": status,
            "last_event_seq": len(visible),
            "result_artifact_id": "diag-art-result" if status == "result_ready" else None,
            "error": (
                {"code": visible[-1]["data"]["code"], "message": visible[-1]["data"]["message"],
                 "field": None, "details": None}
                if status == "failed" else None
            ),
            "events": [e for e in visible if e["seq"] > after_seq],
        }

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.calls.append((request.method, request.url.path))
        if self.down:
            raise httpx.ConnectError("down", request=request)
        if request.headers.get("authorization") != f"Bearer {TOKEN}":
            return httpx.Response(401, json={"code": "unauthenticated", "message": "토큰", "field": None, "details": None})
        parts = request.url.path.strip("/").split("/")
        if request.method == "POST" and parts == ["runs"]:
            if self.submit_mode == "limit":
                return httpx.Response(429, json={
                    "code": "daily_limit_reached",
                    "message": "오늘 이 세션의 진단 실행 한도(10회)에 도달했습니다.",
                    "field": None, "details": {"limit": 10, "resets_at": "2026-09-21T00:00:00+09:00"},
                })
            if self.submit_mode == "conflict":
                return httpx.Response(409, json={
                    "code": "execution_conflict", "message": "다른 내용으로 이미 접수되었습니다.",
                    "field": "target.run_id", "details": None,
                })
            body = json.loads(request.content)
            execution_id = body["execution_id"]
            new = execution_id not in self.submitted
            self.submitted[execution_id] = body
            return httpx.Response(202 if new else 200, json=self._status(execution_id, after_seq=10**9))
        if request.method == "GET" and len(parts) == 2 and parts[0] == "runs":
            execution_id = parts[1]
            if execution_id not in self.submitted:
                return httpx.Response(404, json={"code": "not_found", "message": "없음", "field": "execution_id", "details": None})
            after_seq = int(request.url.params.get("after_seq", "0"))
            return httpx.Response(200, json=self._status(execution_id, after_seq))
        if request.method == "GET" and len(parts) == 4 and parts[2] == "artifacts":
            found = self.artifacts.get(parts[3])
            if found is None:
                return httpx.Response(404, json={"code": "not_found", "message": "없음", "field": "artifact_id", "details": None})
            content, content_type = found
            return httpx.Response(200, content=content, headers={"content-type": content_type})
        return httpx.Response(404, json={"code": "not_found", "message": request.url.path, "field": None, "details": None})


# --- fixture ------------------------------------------------------------------------------


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def server() -> FakeDiagServer:
    return FakeDiagServer(diag_data())


@pytest.fixture
def make_worker(app, settings, store, clock):
    """`app` 이 스키마를 만든 뒤에 워커를 연다. 워커는 자기 conn_factory 로 tick 마다 연결을 연다."""

    def _make(diag_server: FakeDiagServer, overrides=None) -> Worker:
        used = settings if overrides is None else overrides
        client = HttpDiagClient(
            used.diag_api_url, used.diag_api_token,
            transport=httpx.MockTransport(diag_server.handle),
        )
        return Worker(lambda: connect(used.db_path), store, client, used, clock)

    return _make


@pytest.fixture
def worker(make_worker, server) -> Worker:
    return make_worker(server)


def _selection(task_id: str, agent_id: str, capability: dict) -> SelectionRecord:
    return SelectionRecord.model_validate({
        "task_id": task_id, "mode": "auto", "required_capability": capability, "candidate_count": 1,
        "selected_agent_id": agent_id, "matched": capability, "status": "selected",
        "reason": f"{capability['code']} 일치 후보 1개",
    })


def seed_flow(conn, client, clock, *, a_completion="auto", b_run_mode="auto") -> tuple[str, str]:
    """세션·에이전트 2개·A(진단, 자동 완료)→B(코드 수정)·선택 기록·A queued 실행. 연결 프로그램은 등록·온라인.
    반환은 (connector_id, token)."""
    now = clock()
    repo.create_session(conn, SESSION, now)
    seed_agents(conn)
    repo.insert_task(conn, {**task_row(TASK_A), "completion_mode": a_completion}, now)
    repo.insert_task(conn, {
        **task_row(TASK_B, kind="code_change", predecessor=TASK_A),
        "run_mode": b_run_mode,
        "target": {
            "local_registration_id": LOCAL_REGISTRATION,
            "base_commit": BASE_COMMIT,
            "verification_profile_id": "vp-pytest",
        },
        "status": "대기",
        "status_reason": "선행 대기",
    }, now)
    repo.save_selection(conn, _selection(TASK_A, "agent-ops-demo", CAP_A))
    repo.save_selection(conn, _selection(TASK_B, "agent-codex-mac", CAP_B))
    seed_execution(conn, EXEC_A, TASK_A, kind="diagnosis", inputs=())
    connector_id, token = exchange(client, conn)
    repo.update_registration(
        conn, LOCAL_REGISTRATION, connector_id=connector_id, repository_id="demo-report-repo",
        base_commit=BASE_COMMIT, verification_profile_ids=["vp-pytest"], discovered={}, now=now,
    )
    repo.touch_connector(conn, connector_id, now, None)
    return connector_id, token


@pytest.fixture
def flow(conn, client, clock) -> tuple[str, str]:
    return seed_flow(conn, client, clock)


def run_to_result(worker: Worker, server: FakeDiagServer) -> TickReport:
    """A: queued → 접수 → 결과까지. 마지막 tick 의 보고를 돌려준다 (A 판정 + B 스캔 포함)."""
    worker.tick()
    server.visible = 4
    return worker.tick()


def _task(conn, task_id: str):
    return repo.get_task(conn, task_id)


def _status(conn, task_id: str) -> tuple[str, str]:
    row = _task(conn, task_id)
    return row["status"], row["status_reason"]


def _executions(conn, task_id: str):
    return repo.list_executions(conn, task_id)


def _verdict(conn, execution_id: str) -> dict | None:
    row = repo.get_verdict(conn, execution_id)
    return json.loads(row["verdict_json"]) if row else None


def _append(conn, execution_id: str, seq: int, type_: str, data: dict, now: str, actor="connector:x"):
    repo.append_event(
        conn, execution_id, ExecutionEvent.model_validate(event(execution_id, seq, type_, data)),
        actor=actor, now=now,
    )


def _store(conn, store, execution_id: str, kind: str, data: bytes, now: str, content_type="text/plain") -> str:
    created, _ = repo.store_artifact(
        conn, store, execution_id=execution_id, session_id=SESSION,
        meta=ArtifactMeta.model_validate(meta_for(data, kind=kind, name=f"{kind}.txt", content_type=content_type)),
        data=data, now=now,
    )
    return created.artifact_id


def seed_code_result(
    conn, store, execution_id: str, now: str, *, kinds=REQUIRED_KINDS, report_text=REPORT_TEXT,
    before_exit=1, verification_exit=0,
) -> None:
    """B 실행을 accepted → started → (산출물) → result_ready 로. CONTRACT 7절 정상 제출 형태."""
    _append(conn, execution_id, 1, "accepted", {}, now)
    _append(conn, execution_id, 2, "started", {"runtime_ref": "pid:1"}, now)
    contents = {
        "diff": "--- a/report.py\n+++ b/report.py\n@@ -1 +1,2 @@\n-items\n+items\n+records\n",
        "test_log_before": f"exit_code={before_exit}\nFAILED tests/test_transform.py::test_records\n",
        "test_log_after": "exit_code=0\n3 passed\n",
        "report_output": report_text,
        "verification_log": f"exit_code={verification_exit}\n3 passed\n",
    }
    for kind in kinds:
        _store(conn, store, execution_id, kind, contents[kind].encode(), now)
    body = code_change_result(execution_id, TASK_B)
    body["verification"]["exit_code"] = verification_exit
    result_id = _store(
        conn, store, execution_id, "code_change_result",
        json.dumps(body, ensure_ascii=False).encode(), now, content_type="application/json",
    )
    _append(conn, execution_id, 3, "result_ready", {"result_artifact_id": result_id}, now)


# --- 정상 흐름: A queued → 접수 → 실행 중 → 결과 → 완료 → B queued ---------------------------------


def test_diagnosis_flow_completes_a_and_spawns_b(flow, worker, server, conn, store, client, clock):
    connector_id, token = flow

    report = worker.tick()

    assert report.submitted == 1 and server.posts() == [("POST", "/runs")]
    a = repo.get_execution(conn, EXEC_A)
    assert (a["status"], a["last_event_seq"]) == ("accepted", 1)
    assert _status(conn, TASK_A) == ("실행 요청됨", "접수 확인")

    server.visible = 3
    report = worker.tick()

    assert report.events_applied == 2 and server.posts() == [("POST", "/runs")]
    a = repo.get_execution(conn, EXEC_A)
    assert (a["status"], a["last_event_seq"]) == ("running", 3)
    assert _status(conn, TASK_A) == ("실행 중", "get_run daily-0920-0900 조회 완료")

    server.visible = 4
    report = worker.tick()

    # 결과·이력·첨부가 중앙 산출물로 저장되고 결과 안의 ID 가 중앙 ID 로 치환됐다
    a = repo.get_execution(conn, EXEC_A)
    assert a["status"] == "result_ready"
    result_row = repo.get_artifact(conn, a["result_artifact_id"])
    assert result_row["kind"] == "diagnosis_result" and result_row["execution_id"] == EXEC_A
    result = json.loads(repo.read_artifact(conn, store, a["result_artifact_id"]))
    kinds = {}
    for ref in result["attachments"]:
        row = repo.get_artifact(conn, ref["artifact_id"])
        assert row is not None and row["execution_id"] == EXEC_A, ref
        kinds[ref["evidence_id"]] = row["kind"]
        assert row["sha256"] == ref["sha256"]
    assert set(kinds.values()) == {"evidence"} and len(kinds) == 8
    trace_row = repo.get_artifact(conn, result["provenance"]["tool_trace_artifact_id"])
    assert trace_row["kind"] == "tool_trace" and trace_row["execution_id"] == EXEC_A
    assert not any(v.startswith("diag-") for v in [a["result_artifact_id"], *(r["artifact_id"] for r in result["attachments"])])

    # A 판정: verify_diagnosis 의 passed 로만 완료
    verdict = _verdict(conn, EXEC_A)
    assert verdict["outcome"] == "passed" and len(verdict["checks"]) == 14  # 워커 사전 검사 2 + 검증기 12
    task_a = _task(conn, TASK_A)
    assert (task_a["status"], task_a["status_reason"]) == ("완료", "판정 근거: 14/14")
    assert task_a["finished_at"] == clock() and a["released_at"] == clock()
    assert report.verdicts == 1 and report.successors_created == 1

    # B: 인계 묶음을 입력으로 고정한 queued 실행 하나
    executions = _executions(conn, TASK_B)
    assert len(executions) == 1
    b = executions[0]
    assert (b["status"], b["kind"], b["attempt_no"]) == ("queued", "code_change", 1)
    assert b["assigned_connector_id"] == connector_id
    assert b["predecessor_execution_id"] == EXEC_A
    assert b["start_key"] == f"auto:{TASK_B}:r1"
    request = ExecutionRequest.model_validate_json(b["request_json"])
    assert request.target.base_commit == BASE_COMMIT and request.agent_id == "agent-codex-mac"
    assert len(request.input_artifact_ids) == 1
    bundle_row = repo.get_artifact(conn, request.input_artifact_ids[0])
    assert (bundle_row["kind"], bundle_row["execution_id"], bundle_row["session_id"]) == ("handoff_bundle", EXEC_A, SESSION)
    bundle = HandoffBundle.model_validate_json(repo.read_artifact(conn, store, bundle_row["artifact_id"]))
    assert bundle.source_execution_id == EXEC_A
    assert bundle.source_result_artifact_id == a["result_artifact_id"]
    assert [(x.evidence_id, x.artifact_id) for x in bundle.attachments[:8]] == [
        (r["evidence_id"], r["artifact_id"]) for r in result["attachments"]
    ]
    expected_ref = bundle.attachments[8]
    assert (expected_ref.evidence_id, expected_ref.version) == ("expected-report", "1")
    expected = json.loads(repo.read_artifact(conn, store, expected_ref.artifact_id))
    assert expected == {
        "report_date": "2026-09-19",
        "rows": [
            {"team": "운영", "completed": 12, "pending": 3},
            {"team": "개발", "completed": 8, "pending": 2},
        ],
        "total_completed": 20,
        "total_pending": 5,
    }
    assert _status(conn, TASK_B) == ("실행 요청됨", "접수 대기")

    # 연결 프로그램이 Step 5 API 로 그 실행을 claim 할 수 있다
    response = client.post(
        "/connector/claim", json={"contract_version": 1, "connector_id": connector_id},
        headers=bearer(token),
    )
    assert response.status_code == 200, response.text
    assert response.json()["execution_id"] == b["execution_id"]
    assert response.json()["input_artifact_ids"] == request.input_artifact_ids


def test_bundle_is_assembled_once_and_reused(flow, worker, server, conn, store, clock):
    run_to_result(worker, server)
    a = repo.get_execution(conn, EXEC_A)
    task_b = _task(conn, TASK_B)

    first = assemble_handoff(conn, store, a, task_b, clock())
    second = assemble_handoff(conn, store, a, task_b, clock())

    assert first == second
    bundles = [x for x in repo.artifacts_of(conn, EXEC_A) if x["kind"] == "handoff_bundle"]
    assert len(bundles) == 1


# --- 같은 요청·ID 두 번, 연결 불가 재시도 -------------------------------------------------------


def test_unavailable_diag_api_keeps_state_and_resubmits_same_id(flow, worker, server, conn):
    server.down = True

    report = worker.tick()

    assert report.retries == 1 and report.submitted == 0
    a = repo.get_execution(conn, EXEC_A)
    assert (a["status"], a["last_event_seq"]) == ("queued", 0)
    assert repo.list_events(conn, EXEC_A) == []

    server.down = False
    worker.tick()
    worker.tick()

    # 끊긴 동안의 시도 1 + 복구 후 접수 1. 접수된 뒤에는 다시 보내지 않는다 (같은 ID 한 번)
    assert server.posts() == [("POST", "/runs"), ("POST", "/runs")]
    assert list(server.submitted) == [EXEC_A]
    assert repo.get_execution(conn, EXEC_A)["status"] == "accepted"


def test_resubmission_answered_with_200_still_records_acceptance(flow, worker, server, conn):
    """중앙이 접수 이벤트를 남기기 전에 죽었다가 재시작한 경우: 같은 ID 재전송에 200 이 와도 접수를 기록하고 이어 간다."""
    server.submitted[EXEC_A] = {}
    server.visible = 2

    report = worker.tick()

    assert report.submitted == 1 and server.posts() == [("POST", "/runs")]
    a = repo.get_execution(conn, EXEC_A)
    assert (a["status"], a["last_event_seq"]) == ("running", 2)
    assert [e["type"] for e in repo.list_events(conn, EXEC_A)] == ["accepted", "started"]
    worker.tick()
    assert server.posts() == [("POST", "/runs")]


def test_unavailable_during_polling_leaves_execution_unchanged(flow, worker, server, conn):
    worker.tick()
    server.visible = 4
    server.down = True

    report = worker.tick()

    assert report.retries == 1
    a = repo.get_execution(conn, EXEC_A)
    assert (a["status"], a["last_event_seq"]) == ("accepted", 1)
    assert repo.artifacts_of(conn, EXEC_A) == []

    server.down = False
    worker.tick()

    assert repo.get_execution(conn, EXEC_A)["status"] == "result_ready"
    assert _status(conn, TASK_A)[0] == "완료"


# --- A 완료 직후 재시작, B 종료 뒤 재처리, unknown·검토 대기 잠금 ------------------------------------


def test_restart_after_a_completion_creates_b_once(flow, make_worker, server, conn, clock):
    repo.set_agent_connection(conn, "agent-codex-mac", "offline", clock())
    report = run_to_result(make_worker(server), server)

    assert _status(conn, TASK_A)[0] == "완료"
    assert report.successors_created == 0 and _executions(conn, TASK_B) == []
    status, reason = _status(conn, TASK_B)
    assert status == "대기" and reason.startswith("연결 끊김, 마지막 확인 ")

    repo.set_agent_connection(conn, "agent-codex-mac", "online", clock())
    fresh = make_worker(server)  # 재시작: 새 인스턴스의 첫 tick 이 후속 스캔으로 이어 간다
    assert fresh.tick().successors_created == 1
    assert make_worker(server).tick().successors_created == 0
    make_worker(server).tick()

    assert len(_executions(conn, TASK_B)) == 1
    assert _status(conn, TASK_B) == ("실행 요청됨", "접수 대기")


def test_replayed_a_completion_after_b_finished_adds_no_execution(flow, worker, server, conn, clock):
    run_to_result(worker, server)
    b = _executions(conn, TASK_B)[0]["execution_id"]
    _append(conn, b, 1, "accepted", {}, clock())
    _append(conn, b, 2, "failed", {"code": "timeout", "message": "Codex 실행이 20분을 초과해 종료했습니다.", "process_stopped": True}, clock())

    report = worker.tick()

    assert report.failures_reflected == 1
    task_b = _task(conn, TASK_B)
    assert (task_b["status"], task_b["status_reason"]) == ("실패", "timeout · Codex 실행이 20분을 초과해 종료했습니다.")
    assert task_b["finished_at"] == clock()
    assert repo.get_execution(conn, b)["released_at"] == clock()

    for _ in range(3):
        report = worker.tick()  # A 완료는 그대로 남아 있다 — start_key 로 B 추가 실행 없음
        assert report.successors_created == 0 and report.failures_reflected == 0

    assert len(_executions(conn, TASK_B)) == 1


def test_b_unknown_keeps_lock_and_recovers_on_resend(flow, worker, server, conn, clock, settings):
    run_to_result(worker, server)
    b = _executions(conn, TASK_B)[0]["execution_id"]
    _append(conn, b, 1, "accepted", {}, clock())
    clock.advance(settings.limits.unknown_after_seconds + 1)

    report = worker.tick()

    assert report.observations == 1
    assert repo.get_execution(conn, b)["status"] == "unknown"
    observations = conn.execute("SELECT kind FROM execution_observations WHERE execution_id = ?", (b,)).fetchall()
    assert [o["kind"] for o in observations] == ["unknown_no_start"]
    assert _status(conn, TASK_B) == ("확인 필요", "시작 여부 불명 — 재실행하지 않음")
    assert _task(conn, TASK_B)["finished_at"] is None

    for _ in range(2):
        worker.tick()
    assert len(_executions(conn, TASK_B)) == 1

    # 기존 실행 주체의 재전송(started)으로 복원. 새 프로세스를 만들지 않는다
    _append(conn, b, 2, "started", {"runtime_ref": "pid:9"}, clock())
    worker.tick()
    assert repo.get_execution(conn, b)["status"] == "running"
    assert len(_executions(conn, TASK_B)) == 1


def test_b_review_wait_keeps_lock(flow, worker, server, conn, store, clock):
    run_to_result(worker, server)
    b = _executions(conn, TASK_B)[0]["execution_id"]
    seed_code_result(conn, store, b, clock())

    report = worker.tick()

    assert report.results_checked == 1
    assert _status(conn, TASK_B) == ("확인 필요", "검토 대기")
    task_b = _task(conn, TASK_B)
    assert task_b["finished_at"] is None and repo.get_execution(conn, b)["released_at"] is None
    verdict = _verdict(conn, b)
    assert verdict["outcome"] == "passed" and all(c["passed"] for c in verdict["checks"])

    for _ in range(2):
        report = worker.tick()
        assert report.results_checked == 0 and report.successors_created == 0
    assert len(_executions(conn, TASK_B)) == 1


# --- B 결과 확인: 필수 산출물·수정 전 실패·검증 프로필·보고서 수치 -------------------------------------


def test_b_result_missing_artifacts_needs_attention(flow, worker, server, conn, store, clock):
    run_to_result(worker, server)
    b = _executions(conn, TASK_B)[0]["execution_id"]
    seed_code_result(conn, store, b, clock(), kinds=("diff", "report_output"))

    worker.tick()

    status, reason = _status(conn, TASK_B)
    assert status == "확인 필요"
    assert reason == "필수 산출물 누락: test_log_before, test_log_after, verification_log"
    verdict = _verdict(conn, b)
    assert verdict["outcome"] == "failed"
    assert not next(c for c in verdict["checks"] if c["code"] == "required_artifacts")["passed"]
    assert _task(conn, TASK_B)["finished_at"] is None


def test_b_result_with_passing_before_test_or_failed_verification_needs_attention(
    flow, worker, server, conn, store, clock
):
    run_to_result(worker, server)
    b = _executions(conn, TASK_B)[0]["execution_id"]
    seed_code_result(conn, store, b, clock(), before_exit=0, verification_exit=2)

    worker.tick()

    status, reason = _status(conn, TASK_B)
    assert status == "확인 필요"
    assert "수정 전 테스트가 실패하지 않음" in reason and "vp-pytest exit 2" in reason


def test_report_totals_follow_changed_input_rows(flow, make_worker, conn, store, clock):
    sources = demo_sources()
    sources[("response-after", "1")][1]["data"]["records"][0]["completed"] = 13
    sources[("response-before", "1")][1]["items"][0]["completed"] = 13
    server = FakeDiagServer(diag_data(sources))
    worker = make_worker(server)
    run_to_result(worker, server)

    b_row = _executions(conn, TASK_B)[0]
    request = ExecutionRequest.model_validate_json(b_row["request_json"])
    bundle = HandoffBundle.model_validate_json(repo.read_artifact(conn, store, request.input_artifact_ids[0]))
    expected_ref = next(x for x in bundle.attachments if x.evidence_id == "expected-report")
    expected = json.loads(repo.read_artifact(conn, store, expected_ref.artifact_id))
    assert (expected["total_completed"], expected["total_pending"]) == (21, 5)
    assert expected["rows"][0] == {"team": "운영", "completed": 13, "pending": 3}

    # B 가 고정 20 보고서를 내면 거부
    seed_code_result(conn, store, b_row["execution_id"], clock(), report_text=REPORT_TEXT)
    worker.tick()

    assert _status(conn, TASK_B) == ("확인 필요", "보고서 수치 불일치")


# --- A 판정 실패·보류 → 확인 필요, B 없음 --------------------------------------------------------


def test_needs_information_result_keeps_a_waiting_and_no_b(flow, make_worker, conn):
    server = FakeDiagServer(diag_data(result_raw=contract_results()[1]))
    report = run_to_result(make_worker(server), server)

    assert report.verdicts == 1 and report.successors_created == 0
    assert _verdict(conn, EXEC_A)["outcome"] == "undecidable"
    task_a = _task(conn, TASK_A)
    assert (task_a["status"], task_a["status_reason"]) == ("확인 필요", "판정 불가")
    assert task_a["finished_at"] is None
    assert repo.get_execution(conn, EXEC_A)["released_at"] is None
    assert _executions(conn, TASK_B) == []
    assert _status(conn, TASK_B) == ("대기", "선행 대기")


def test_attachment_missing_from_trace_fails_verdict_and_no_b(flow, make_worker, conn):
    server = FakeDiagServer(diag_data(drop_from_trace=[("upstream-response-change", "1")]))
    run_to_result(make_worker(server), server)

    assert _verdict(conn, EXEC_A)["outcome"] == "failed"
    assert _status(conn, TASK_A) == ("확인 필요", "미충족: attachments_in_trace")
    assert _executions(conn, TASK_B) == []


def test_review_mode_a_waits_for_human_even_when_verdict_passes(conn, client, clock, make_worker, server):
    seed_flow(conn, client, clock, a_completion="review")
    run_to_result(make_worker(server), server)

    assert _verdict(conn, EXEC_A)["outcome"] == "passed"
    assert _status(conn, TASK_A) == ("확인 필요", "검토 대기")
    assert _task(conn, TASK_A)["finished_at"] is None
    assert _executions(conn, TASK_B) == []


def test_broken_result_is_preserved_and_needs_attention(flow, worker, server, conn, store):
    server.artifacts["diag-art-result"] = (b"not json {", "application/json")
    run_to_result(worker, server)

    a = repo.get_execution(conn, EXEC_A)
    assert a["status"] == "result_ready"
    assert repo.read_artifact(conn, store, a["result_artifact_id"]) == b"not json {"
    verdict = _verdict(conn, EXEC_A)
    assert verdict["outcome"] == "failed" and verdict["checks"][0]["code"] == "result_parsed"
    assert _status(conn, TASK_A) == ("확인 필요", "미충족: result_parsed")
    assert _executions(conn, TASK_B) == []


def test_result_ids_must_match_the_execution(flow, make_worker, conn):
    server = FakeDiagServer(diag_data(execution_id="exec-other"))
    run_to_result(make_worker(server), server)

    verdict = _verdict(conn, EXEC_A)
    assert verdict["outcome"] == "failed"
    assert [c["code"] for c in verdict["checks"] if not c["passed"]] == ["result_ids_match"]
    assert _status(conn, TASK_A)[0] == "확인 필요"


# --- 연결 끊김·오프라인, 직접 실행 후속 --------------------------------------------------------


def test_offline_connector_holds_b_until_online(flow, worker, server, conn, clock, settings):
    worker.tick()
    server.visible = 4
    clock.advance(settings.limits.heartbeat_offline_seconds + 1)  # heartbeat 끊김

    report = worker.tick()

    assert report.agents_offline == 1
    assert repo.get_agent(conn, "agent-codex-mac")["connection_state"] == "offline"
    assert repo.get_agent(conn, "agent-ops-demo")["connection_state"] == "online"
    assert _status(conn, TASK_A)[0] == "완료"
    assert _executions(conn, TASK_B) == []
    status, reason = _status(conn, TASK_B)
    assert (status, reason) == ("대기", "연결 끊김, 마지막 확인 2026-09-20 09:00:00 KST")

    repo.set_agent_connection(conn, "agent-codex-mac", "online", clock())
    assert worker.tick().successors_created == 1
    assert _status(conn, TASK_B) == ("실행 요청됨", "접수 대기")


def test_manual_successor_gets_bundle_and_is_runnable(conn, client, clock, make_worker, server, store, settings):
    seed_flow(conn, client, clock, b_run_mode="manual")
    worker = make_worker(server)

    report = run_to_result(worker, server)

    assert report.inputs_prepared == 1 and report.successors_created == 0
    assert _executions(conn, TASK_B) == []
    assert _status(conn, TASK_B) == ("실행 가능", "agent-codex-mac 선택됨")
    bundles = [x for x in repo.artifacts_of(conn, EXEC_A) if x["kind"] == "handoff_bundle"]
    assert len(bundles) == 1
    assert worker.tick().inputs_prepared == 0

    # 사용자의 직접 실행(Step 6)이 그 인계 묶음을 입력으로 쓴다
    client.cookies.set(SESSION_COOKIE, sign_session(SESSION, settings.session_secret))
    response = client.post(f"/tasks/{TASK_B}/run", follow_redirects=False)
    assert response.status_code == 303, response.text
    b = _executions(conn, TASK_B)[0]
    assert ExecutionRequest.model_validate_json(b["request_json"]).input_artifact_ids == [bundles[0]["artifact_id"]]
    assert b["predecessor_execution_id"] == EXEC_A


# --- 상한·실패 반영·관찰 ------------------------------------------------------------------


def test_diag_limit_fails_execution_without_resubmit(flow, worker, server, conn, clock):
    server.submit_mode = "limit"

    report = worker.tick()

    assert report.failures_reflected == 1
    a = repo.get_execution(conn, EXEC_A)
    assert (a["status"], a["failed_code"], a["process_stopped"]) == ("failed", "daily_limit_reached", 1)
    assert repo.list_events(conn, EXEC_A) == []
    task_a = _task(conn, TASK_A)
    assert task_a["status"] == "실패"
    assert task_a["status_reason"] == "daily_limit_reached · 오늘 이 세션의 진단 실행 한도(10회)에 도달했습니다."
    assert task_a["finished_at"] == clock() and a["released_at"] == clock()

    worker.tick()
    assert server.posts() == [("POST", "/runs")]
    assert _executions(conn, TASK_B) == []


def test_diag_conflict_fails_execution(flow, worker, server, conn):
    server.submit_mode = "conflict"

    worker.tick()

    a = repo.get_execution(conn, EXEC_A)
    assert (a["status"], a["failed_code"]) == ("failed", "execution_conflict")
    assert _status(conn, TASK_A)[0] == "실패"


def test_attachments_over_limit_fail_execution(flow, make_worker, server, conn, settings):
    small = dataclasses.replace(settings, limits=dataclasses.replace(settings.limits, attachments_max_bytes=100))
    worker = make_worker(server, small)

    run_to_result(worker, server)

    a = repo.get_execution(conn, EXEC_A)
    assert (a["status"], a["failed_code"], a["process_stopped"]) == ("failed", "attachments_too_large", 1)
    assert a["last_event_seq"] == 3  # result_ready 는 반영하지 않았다
    assert repo.artifacts_of(conn, EXEC_A) == []
    assert _status(conn, TASK_A)[0] == "실패" and "100" in _status(conn, TASK_A)[1]
    assert _executions(conn, TASK_B) == []


def test_diag_failed_event_marks_task_failed(flow, worker, server, conn):
    server.events[3] = _diag_event(EXEC_A, 4, "failed", {
        "code": "budget_exceeded", "message": "모델 호출 15회를 넘었습니다.", "process_stopped": True,
    })

    run_to_result(worker, server)

    a = repo.get_execution(conn, EXEC_A)
    assert (a["status"], a["failed_code"], a["last_event_seq"]) == ("failed", "budget_exceeded", 4)
    assert _status(conn, TASK_A) == ("실패", "budget_exceeded · 모델 호출 15회를 넘었습니다.")
    assert _executions(conn, TASK_B) == []


def test_failed_without_process_confirmation_needs_attention(flow, worker, server, conn, clock):
    run_to_result(worker, server)
    b = _executions(conn, TASK_B)[0]["execution_id"]
    _append(conn, b, 1, "accepted", {}, clock())
    _append(conn, b, 2, "failed", {"code": "timeout", "message": "종료 확인 실패", "process_stopped": False}, clock())

    worker.tick()

    task_b = _task(conn, TASK_B)
    assert (task_b["status"], task_b["status_reason"]) == ("확인 필요", "종료 미확인 — 재실행하지 않음")
    assert task_b["finished_at"] is None and repo.get_execution(conn, b)["released_at"] is None


def test_accepted_without_start_becomes_unknown_then_recovers(flow, worker, server, conn, clock, settings):
    worker.tick()
    clock.advance(settings.limits.unknown_after_seconds + 1)

    report = worker.tick()

    assert report.observations == 1
    assert repo.get_execution(conn, EXEC_A)["status"] == "unknown"
    assert _status(conn, TASK_A) == ("확인 필요", "시작 여부 불명 — 재실행하지 않음")
    assert worker.tick().observations == 0  # 한 번만 기록

    server.visible = 2  # 진단 API 가 뒤늦게 started 를 냈다 — 같은 실행으로 복원
    worker.tick()

    assert repo.get_execution(conn, EXEC_A)["status"] == "running"
    assert _status(conn, TASK_A)[0] == "실행 중"
    assert server.posts() == [("POST", "/runs")]


def test_heartbeat_loss_is_observed_once_and_never_restarts(flow, worker, server, conn, clock, settings):
    run_to_result(worker, server)
    b = _executions(conn, TASK_B)[0]["execution_id"]
    _append(conn, b, 1, "accepted", {}, clock())
    _append(conn, b, 2, "started", {"runtime_ref": "pid:1"}, clock())
    clock.advance(settings.limits.heartbeat_offline_seconds + 1)

    report = worker.tick()

    assert report.observations == 1
    assert repo.get_execution(conn, b)["status"] == "running"
    worker.tick()
    rows = conn.execute("SELECT kind FROM execution_observations WHERE execution_id = ?", (b,)).fetchall()
    assert [r["kind"] for r in rows] == ["heartbeat_lost"]
    assert len(_executions(conn, TASK_B)) == 1


# --- TickReport·run_forever -------------------------------------------------------------


def test_tick_report_defaults_to_zero():
    assert all(v == 0 for v in dataclasses.asdict(TickReport()).values())


def test_empty_db_tick_is_noop(worker):
    assert worker.tick() == TickReport()


def test_run_forever_sleeps_between_ticks(worker, monkeypatch):
    slept = []

    def fake_sleep(seconds):
        slept.append(seconds)
        raise KeyboardInterrupt

    monkeypatch.setattr("workflow.server.worker.time.sleep", fake_sleep)
    with pytest.raises(KeyboardInterrupt):
        worker.run_forever(0.5)
    assert slept == [0.5]
