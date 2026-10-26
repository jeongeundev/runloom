# ruff: noqa: F811 — 서버 테스트 픽스처(app·conn·store·clock·worker·judge)를 가져와 인자로 쓴다
"""인계 리포트 끝에서 끝까지 — phase 25 step 11 (ADR-0033, ARCHITECTURE "인계 리포트 — phase 25").

서버는 FastAPI `TestClient`, 러너는 연결 토큰(`wfc_…`)으로 claim·이벤트·산출물·리포트 제출 경로를 직접 부르는 가짜(`Runner`)다.
워커는 같은 DB 를 여는 `Worker` 를 테스트가 tick 한다. 실제 러너·GitHub·모델 호출 없음 — 실제 `connector` 러너 코드
(handoff dir 의 `report.json`·`decisions.json`)는 step 7 단위 테스트가 본다.

워크스페이스는 `test_task_cycle.cycle` 과 같고 러너 토큰만 더 남긴다. 수정 Agent(FIX)가 러너 billing 에서 원본 리포트를
쓰고, 다음 단계(검토 Agent REVIEW)도 같은 billing 이 claim 한다 — 검토는 수정과 같은 러너·저장소에서만 착수한다
(`review_repository_mismatch`). 맞춤 풀이(5)만 검토 Agent 를 받는 사람의 러너 shop 으로 옮긴다.

1. bug_fix passed → 원본 리포트 → 그동안 검토 실행은 claim 되지 않음 → done 뒤 claim 에 리포트·결정 답, 내려받기 200.
2. 리포트 실패·시간 초과 → 다음 단계는 리포트 없이 착수, 패널 `리포트 없음(사유)` → 다시 만들기 → done.
3. 사용자 정의 종류·접수 판단 결과에는 리포트, 결과 뒤 판단 결과에는 없음, 직접 작업만 있는 업무는 없음.
4. 옛 러너(능력 미선언) → `unavailable`, 다음 단계 대기 없음, claim 응답 바이트가 기존과 같다.
5. 맞춤 풀이 — 받는 사람이 자기 Agent 로 요청 → 그 러너가 tailored 제출 → API·페이지에서 보인다.
6. 근거 없는 id → done + 그 주장에 `근거 확인 안 됨`.

`TestClient` 만 쓰므로 `WORKFLOW_E2E` 없이 기본 실행에 든다.
"""

import hashlib
import json
import re

import pytest
from fastapi.testclient import TestClient

from tests.workflow.server.conftest import (  # noqa: F401 — 픽스처
    _cheap_scrypt,
    _settings,
    app,
    bearer,
    conn,
    event,
    exchange,
    log_in,
    log_in_member,
    meta_for,
    store,
)
from tests.workflow.server.test_github_sync import config
from tests.workflow.server.test_task_cycle import (  # noqa: F401 — 픽스처
    ALL_KINDS,
    ASSIGNEE,
    BASE,
    C1,
    FIX,
    FIX_SHOP,
    NOW,
    REG_FIX,
    REG_REVIEW,
    REG_SHOP,
    REVIEW,
    SESSION,
    SOURCE,
    _agent,
    clock,
    import_issue,
    make_worker,
    review_tasks,
    seen_at,
    worker,
)
from tests.workflow.server.test_triage_dispatch import triage_logs
from tests.workflow.server.test_triage_judge import result_body
from tests.workflow.server.test_triage_runs import TRIAGE_KINDS, judge  # noqa: F401 — 픽스처
from tests.workflow.server.test_worker import EXEC_T, TASK_R, TASK_T, seed_user_flow
from tests.workflow.server.test_worker_next_step import next_body
from workflow.adapters import repo
from workflow.contracts.github import AssigneeBinding
from workflow.contracts.v1 import (
    RUNNER_CAPABILITY_AFTER_RESULT_TRIAGE,
    RUNNER_CAPABILITY_HANDOFF_REPORT,
    ExecutionRequest,
    format_work_key,
)
from workflow.server.auth import ensure_workspace, utc_now
from workflow.server.worker import _plus_seconds

pytestmark = [pytest.mark.e2e]

CAPABLE = (RUNNER_CAPABILITY_HANDOFF_REPORT,)


@pytest.fixture
def settings(tmp_path):
    """acme/billing 만 허용 저장소 — test_task_cycle.settings 와 같다."""
    import dataclasses

    return dataclasses.replace(_settings(tmp_path), github_repos=("acme/billing",))


@pytest.fixture
def client(app):
    return TestClient(app)


class Runner:
    """연결 토큰으로 `/api/v1` 러너 경로만 부르는 가짜 러너."""

    def __init__(self, app, connector_id: str, token: str, *, kinds=ALL_KINDS, capabilities=CAPABLE):
        self.connector_id, self.kinds, self.capabilities = connector_id, kinds, capabilities
        self.http = TestClient(app)
        del self.http.headers["origin"]
        self.http.headers.update(bearer(token))

    def claim(self):
        body = {"contract_version": 1, "connector_id": self.connector_id}
        if self.kinds is not None:
            body["supported_kinds"] = list(self.kinds)
        if self.capabilities is not None:  # None = 옛 러너 — 칸 자체가 없다
            body["capabilities"] = list(self.capabilities)
        return self.http.post("/api/v1/connector/claim", json=body)

    def take(self) -> dict:
        response = self.claim()
        assert response.status_code == 200, response.text
        return response.json()

    def idle(self) -> None:
        response = self.claim()
        assert response.status_code == 204, response.text

    def _event(self, execution_id: str, seq: int, type_: str, data: dict) -> None:
        response = self.http.post(f"/api/v1/executions/{execution_id}/events",
                                  json=event(execution_id, seq, type_, data))
        assert response.status_code == 200, response.text

    def upload(self, execution_id: str, kind: str, data: bytes, content_type: str = "text/plain") -> str:
        meta = meta_for(data, kind=kind, name=f"{kind}.txt", content_type=content_type)
        response = self.http.post(f"/api/v1/executions/{execution_id}/artifacts", data={"meta": json.dumps(meta)},
                                  files={"file": (meta["name"], data, content_type)})
        assert response.status_code in (200, 201), response.text
        return response.json()["artifact_id"]

    def finish(self, execution_id: str, result_kind: str, body_of) -> None:
        """accepted → started → (산출물) → 결과 봉투 → result_ready. `body_of(upload)` 가 봉투를 만든다."""
        self._event(execution_id, 1, "accepted", {})
        self._event(execution_id, 2, "started", {"runtime_ref": "pid:1"})
        body = body_of(lambda kind, data: self.upload(execution_id, kind, data))
        result_id = self.upload(execution_id, result_kind, json.dumps(body).encode(), "application/json")
        self._event(execution_id, 3, "result_ready", {"result_artifact_id": result_id})
        # 실제 러너처럼 실행을 내려놓은 뒤의 heartbeat — 러너 빔 판정(결과 뒤 판단 착수)이 이것을 본다
        response = self.http.post("/api/v1/connector/heartbeat", json={
            "contract_version": 1, "connector_id": self.connector_id, "current_execution_id": None})
        assert response.status_code == 200, response.text

    def fix(self, request: dict, outcome: str = "ready_for_review") -> None:
        """bug_fix 결과(CONTRACT 13.5)."""
        def body_of(upload) -> dict:
            ids = {kind: upload(kind, data) for kind, data in (
                ("diff", b"--- a/coupon.py\n+++ b/coupon.py\n"),
                ("test_log_before", b"exit_code=1\nFAILED tests/test_coupon.py\n"),
                ("test_log_after", b"exit_code=0\n1 passed\n"),
                ("verification_log", b"exit_code=0\n12 passed\n"),
            )}
            ready = outcome == "ready_for_review"
            return {
                "contract_version": 1, "execution_id": request["execution_id"], "task_id": request["task_id"],
                "outcome": outcome, "summary": "쿠폰 중복 적용 수정", "base_commit": request["target"]["base_commit"],
                "result_commit": C1 if ready else None, "artifact_ids": list(ids.values()),
                "verification": {"profile_id": request["target"]["verification_profile_id"], "result_commit": C1,
                                 "exit_code": 0, "log_artifact_id": ids["verification_log"]} if ready else None,
            }
        self.finish(request["execution_id"], "code_change_result", body_of)

    def submit_report(self, report_id: str, body: dict):
        return self.http.post(f"/api/v1/report-jobs/{report_id}/result", content=json.dumps(body).encode(),
                              headers={"content-type": "application/json"})

    def fail_report(self, report_id: str, message: str):
        return self.http.post(f"/api/v1/report-jobs/{report_id}/failure",
                              json={"contract_version": 1, "failure_code": "agent_failed", "message": message})

    def download(self, execution_id: str, artifact_id: str):
        return self.http.get(f"/api/v1/executions/{execution_id}/artifacts/{artifact_id}")


def original_body(report_id: str, **sections) -> dict:
    """원본 리포트 — 기본은 모든 근거가 확인되는 주장 둘(바뀐 것·확인할 것)."""
    changes = [{"id": "c1", "text": "쿠폰 중복 적용을 막음", "why": "재현 테스트가 실패했다",
                "evidence": [f"commit:{C1[:7]}", "verification"]}]
    to_confirm = [{"id": "q1", "text": "할인 상한 기준 확인", "why": None, "evidence": ["source:work"]}]
    return {"contract_version": 1, "report_id": report_id, "job_type": "original",
            "sections": {"changes": changes, "decisions": [], "open_questions": [], "to_confirm": to_confirm,
                         "constraints": [], "remaining_work": [], **sections}, "diagram": None}


def tailored_body(report_id: str) -> dict:
    changes = [{"id": "t1", "text": "검토자는 쿠폰 조건 한 줄만 보면 됨", "why": None, "evidence": ["claim:c1"]}]
    return {"contract_version": 1, "report_id": report_id, "job_type": "tailored",
            "sections": {"changes": changes, "decisions": [], "open_questions": [], "to_confirm": [],
                         "constraints": [], "remaining_work": []}, "diagram": None}


def reports_of(conn, execution_id: str) -> list:
    return conn.execute("SELECT * FROM handoff_reports WHERE source_execution_id = ? ORDER BY created_at, rowid",
                        (execution_id,)).fetchall()


def stage_list(client: TestClient, key: str) -> str:
    response = client.get(f"/work/{key}/panel")
    assert response.status_code == 200, response.text
    return re.search(r'<ol class="stage-list" data-stages>.*?</ol>', response.text, re.S).group(0)


@pytest.fixture
def cycle(conn, client) -> dict:
    """`test_task_cycle.cycle` 과 같은 워크스페이스(acme/billing 소스, 담당자 → FIX) — 러너 토큰을 남긴다. 반환은 러너 이름 → connector_id(`judge` 가 그대로 쓴다)와 `tokens`(러너 이름 → 연결 토큰)."""
    ensure_workspace(conn, NOW)
    for agent in (_agent(FIX, REG_FIX, "code.fix", "billing"), _agent(REVIEW, REG_REVIEW, "code.review", "billing"),
                  _agent(FIX_SHOP, REG_SHOP, "code.fix", "shop")):
        repo.upsert_agent(conn, agent)
        repo.register_session_agent(conn, SESSION, agent["agent_id"], NOW)
    billing = exchange(client, conn)
    shop = exchange(client, conn)
    for registration, connector, repository, profiles in (
        (REG_FIX, billing[0], "billing", ["vp-pytest"]),
        (REG_REVIEW, billing[0], "billing", []),
        (REG_SHOP, shop[0], "shop", ["vp-shop"]),
    ):
        repo.update_registration(conn, registration, connector_id=connector, repository_id=repository,
                                 base_commit=BASE, verification_profile_ids=profiles, discovered={}, now=NOW)
    seen_at(conn, max(NOW, utc_now()))
    for connector, _ in (billing, shop):
        repo.record_supported_kinds(conn, connector, ALL_KINDS)
    repo.save_github_source(conn, SESSION, config(review_agent_id=REVIEW), NOW)
    repo.bind_assignee(conn, SESSION, AssigneeBinding(source_id=SOURCE, github_user_id=ASSIGNEE,
                                                      github_login="kim-dev", agent_id=FIX), NOW)
    conn.commit()
    return {"billing": billing[0], "shop": shop[0], "tokens": {"billing": billing[1], "shop": shop[1]}}


def runner_of(app, cycle: dict, name: str, **kwargs) -> Runner:
    return Runner(app, cycle[name], cycle["tokens"][name], **kwargs)


@pytest.fixture
def runners(app, cycle) -> dict:
    return {name: runner_of(app, cycle, name) for name in ("billing", "shop")}


@pytest.fixture
def admin(app) -> TestClient:
    return log_in(TestClient(app))


def fix_passed(conn, worker, runners) -> dict:
    """RUN-1 수정 단계 → billing 이 능력을 선언하고 claim·결과 제출 → 판정 tick(원본 리포트 queued·검토 착수)."""
    task_id = import_issue(conn, 1)
    worker.tick()
    request = runners["billing"].take()
    assert request["task_id"] == task_id and "job_type" not in request
    runners["billing"].fix(request)
    worker.tick()
    (review,) = review_tasks(conn, task_id)
    (review_exec,) = repo.list_executions(conn, review["task_id"])
    (report,) = reports_of(conn, request["execution_id"])
    assert (report["status"], report["connector_id"]) == ("queued", runners["billing"].connector_id)
    work = repo.work_item_of_task(conn, task_id)
    return {"fix_task": task_id, "fix_exec": request["execution_id"], "review_exec": review_exec["execution_id"],
            "report_id": report["report_id"], "key": format_work_key(work["key_number"]),
            "work_item_id": work["work_item_id"]}


def write_report(runner: Runner, flow: dict, body_of=original_body) -> dict:
    """보내는 러너가 리포트 작업을 claim 해 제출 — 반환은 받은 `ReportJob`."""
    job = runner.take()
    assert (job["report_id"], job["job_type"]) == (flow["report_id"], "original")
    response = runner.submit_report(job["report_id"], body_of(job["report_id"]))
    assert response.status_code == 200, response.text
    assert response.json() == {"report_id": job["report_id"], "status": "done"}
    return job


# --- 1. bug_fix → 원본 리포트 → 다음 단계 대기 → 리포트·결정 답 전달 ---------------------------------------------


def test_01_fix_report_holds_the_review_claim_until_done_then_rides_on_it(conn, worker, runners, admin):
    flow = fix_passed(conn, worker, runners)
    billing = runners["billing"]  # 보내는 러너이자 검토를 받는 러너

    job = billing.take()  # queued 검토 실행보다 리포트 작업을 먼저 받는다(D11)
    assert (job["report_id"], job["job_type"], job["agent_id"]) == (flow["report_id"], "original", FIX)
    assert (job["local_registration_id"], job["checkout_commit"], job["base_commit"]) == (REG_FIX, C1, BASE)
    assert job["request"].startswith("# 인계 리포트: RUN-")
    billing.idle()  # leased — 검토 실행은 나가지 않는다(D9)
    assert repo.get_execution(conn, flow["review_exec"])["status"] == "queued"

    body = original_body(job["report_id"])
    response = billing.submit_report(job["report_id"], body)
    assert response.status_code == 200, response.text
    row = repo.get_handoff_report(conn, flow["report_id"])
    assert row["status"] == "done"
    assert json.loads(row["evidence_check_json"])["claims"] == {
        "c1": {"verified": True, "unverified": []}, "q1": {"verified": True, "unverified": []}}

    # 받는 사람(여기서는 관리자)이 결정 항목에 답한다 — claim 순간의 최신 답이 실린다(D14)
    answered = admin.put(f"/api/v1/reports/{flow['report_id']}/decisions/q1",
                         json={"answer": "changed", "note": "상한은 1만 원"})
    assert answered.status_code == 200, answered.text

    request = billing.take()
    assert request["execution_id"] == flow["review_exec"] and "job_type" not in request
    ref = request["handoff_report"]
    assert (ref["report_id"], ref["artifact_id"]) == (flow["report_id"], row["artifact_id"])
    (decision,) = ref["decisions"]
    assert (decision["item_id"], decision["answer"], decision["note"], decision["answered_by"]) == (
        "q1", "changed", "상한은 1만 원", "관리자")
    assert ExecutionRequest.model_validate(request).handoff_report.decisions[0].note == "상한은 1만 원"
    # 저장된 요청은 그대로 — 참조는 claim 응답에만
    assert "handoff_report" not in json.loads(repo.get_execution(conn, flow["review_exec"])["request_json"])

    download = billing.download(flow["review_exec"], ref["artifact_id"])
    assert download.status_code == 200
    assert hashlib.sha256(download.content).hexdigest() == ref["sha256"]
    assert json.loads(download.content) == body


# --- 2. 실패·시간 초과 → 리포트 없이 착수 → 다시 만들기 ----------------------------------------------------------


def _regenerate_and_finish(conn, admin, runners, flow, old_report_id: str) -> str:
    response = admin.post(f"/api/v1/reports/{old_report_id}/regenerate")
    assert response.status_code == 202, response.text
    new_id = response.json()["report_id"]
    assert new_id != old_report_id and repo.get_handoff_report(conn, new_id)["status"] == "queued"
    job = runners["billing"].take()  # 같은 Agent(같은 러너)가 다시 쓴다
    assert (job["report_id"], job["agent_id"]) == (new_id, FIX)
    assert runners["billing"].submit_report(new_id, original_body(new_id)).status_code == 200
    assert repo.get_handoff_report(conn, new_id)["status"] == "done"
    line = stage_list(admin, flow["key"])
    assert f'href="/work/{flow["key"]}/reports/{new_id}"' in line and "리포트 보기" in line
    return new_id


def test_02_failed_report_lets_the_review_start_without_it_then_regenerate(conn, worker, runners, admin):
    flow = fix_passed(conn, worker, runners)
    job = runners["billing"].take()
    failed = runners["billing"].fail_report(job["report_id"], "모델이 멈춤")
    assert failed.status_code == 200 and failed.json() == {"report_id": job["report_id"], "status": "failed"}

    request = runners["billing"].take()  # 리포트 없이 착수
    assert request["execution_id"] == flow["review_exec"] and "handoff_report" not in request
    line = stage_list(admin, flow["key"])
    assert 'data-stage-report="failed"' in line and "리포트 없음(리포트 작성 실패 — 모델이 멈춤)" in line
    assert f'action="/work/{flow["key"]}/reports/{job["report_id"]}/regenerate"' in line

    _regenerate_and_finish(conn, admin, runners, flow, job["report_id"])
    # 이미 착수한 다음 단계에는 사람용으로만 — 실행 요청은 그대로
    assert "handoff_report" not in json.loads(repo.get_execution(conn, flow["review_exec"])["request_json"])


def test_02b_timed_out_report_lets_the_review_start_without_it_then_regenerate(conn, worker, runners, admin, clock):
    clock.now = _plus_seconds(NOW, 0)  # 워커 시각 표기(utc_now 모양) — 시간 초과 경계를 문자열로 비교한다
    flow = fix_passed(conn, worker, runners)
    assert runners["billing"].take()["report_id"] == flow["report_id"]  # 받아 놓고 제출하지 않는다

    clock.now = _plus_seconds(NOW, 1799)
    worker.tick()
    runners["billing"].idle()  # 아직 30분 전 — leased, 검토는 대기
    clock.now = _plus_seconds(NOW, 1800)
    assert worker.tick().handoff_reports_expired == 1

    request = runners["billing"].take()
    assert request["execution_id"] == flow["review_exec"] and "handoff_report" not in request
    line = stage_list(admin, flow["key"])
    assert 'data-stage-report="timed_out"' in line and "리포트 없음(30분 안에 리포트가 오지 않음)" in line
    # 늦게 온 제출은 받지 않는다 — 리포트 작업은 이미 끝났다
    late = runners["billing"].submit_report(flow["report_id"], original_body(flow["report_id"]))
    assert late.status_code == 409 and late.json()["code"] == "report_not_leased"

    _regenerate_and_finish(conn, admin, runners, flow, flow["report_id"])


# --- 3. 대상 판정 — 사용자 정의 종류·접수 판단·결과 뒤 판단·직접 작업 ---------------------------------------------


def test_03_custom_kind_result_gets_a_report_and_its_successor_waits(conn, app, client, store, worker, clock):
    connector_id, token = seed_user_flow(conn, client, clock)  # classify(T) → review(R) 규칙, 한 러너
    runner = Runner(app, connector_id, token, kinds=None)  # 사용자 정의 종류만 받는 러너 모양
    request = runner.take()
    assert request["execution_id"] == EXEC_T

    def body_of(upload) -> dict:
        ids = [upload("claude_jsonl", b'{"type":"result"}\n')]
        return {"contract_version": 1, "execution_id": EXEC_T, "task_id": TASK_T, "kind": "classify",
                "outcome": "ready_for_handoff", "summary": "변환 실패 원인 분류", "artifact_ids": ids}
    runner.finish(EXEC_T, "generic_result", body_of)
    worker.tick()

    (report,) = reports_of(conn, EXEC_T)
    (successor,) = repo.list_executions(conn, TASK_R)
    job = runner.take()  # 리포트 작업이 후속 실행보다 먼저
    assert (job["report_id"], job["job_type"], job["checkout_commit"]) == (report["report_id"], "original", None)
    assert "outcome: ready_for_handoff" in job["request"]
    runner.idle()  # 후속(R)은 원인 리포트를 기다린다
    changes = [{"id": "c1", "text": "분류 결과", "why": None, "evidence": ["verification"]}]
    response = runner.submit_report(job["report_id"], {
        "contract_version": 1, "report_id": job["report_id"], "job_type": "original", "diagram": None,
        "sections": {"changes": changes, "decisions": [], "open_questions": [], "to_confirm": [],
                     "constraints": [], "remaining_work": []}})
    assert response.status_code == 200, response.text

    after = runner.take()
    assert after["execution_id"] == successor["execution_id"]
    assert after["handoff_report"]["report_id"] == job["report_id"] and after["handoff_report"]["decisions"] == []


def test_03b_intake_triage_result_gets_a_report(conn, app, worker, cycle, judge):
    task_id = import_issue(conn, 1, assignee_ids=[], assignee_logins=[])
    work_item_id = repo.work_item_of_task(conn, task_id)["work_item_id"]
    assert worker.tick().triage_started == 1
    billing = runner_of(app, cycle, "billing", kinds=TRIAGE_KINDS)
    request = billing.take()
    (log,) = triage_logs(conn, work_item_id)
    assert (request["execution_id"], log["cause"]) == (log["execution_id"], "intake")
    billing.finish(log["execution_id"], "triage_result", lambda upload: result_body(log))

    worker.tick()

    assert triage_logs(conn, work_item_id)[0]["state"] == "proposed"
    (report,) = reports_of(conn, log["execution_id"])
    job = billing.take()
    assert (job["report_id"], job["agent_id"], job["checkout_commit"], job["base_commit"]) == (
        report["report_id"], FIX, BASE, None)


def test_03c_after_result_triage_result_gets_no_report_and_does_not_wait(conn, app, worker, cycle, judge):
    billing = runner_of(app, cycle, "billing", kinds=TRIAGE_KINDS,
                        capabilities=(RUNNER_CAPABILITY_AFTER_RESULT_TRIAGE, RUNNER_CAPABILITY_HANDOFF_REPORT))
    task_id = import_issue(conn, 1)
    worker.tick()
    fix = billing.take()
    billing.fix(fix, outcome="needs_information")
    worker.tick()  # 판정 → 수정 결과의 원본 리포트 + 결과 뒤 판단 착수

    work_item_id = repo.work_item_of_task(conn, task_id)["work_item_id"]
    (log,) = triage_logs(conn, work_item_id)
    assert log["cause"] == "after_result"
    (fix_report,) = reports_of(conn, fix["execution_id"])
    assert billing.take()["report_id"] == fix_report["report_id"]  # 리포트 작업 먼저
    triage = billing.take()  # 판단은 원인 리포트(leased)를 기다리지 않는다(D15)
    assert triage["execution_id"] == log["execution_id"] and "handoff_report" not in triage
    billing.finish(log["execution_id"], "triage_result", lambda upload: next_body(log))

    worker.tick()

    assert triage_logs(conn, work_item_id)[0]["state"] == "proposed"
    assert reports_of(conn, log["execution_id"]) == []  # 결과 뒤 판단 결과에는 리포트 없음(D4)
    assert [r["source_execution_id"] for r in conn.execute("SELECT * FROM handoff_reports")] == [fix["execution_id"]]
    billing.idle()


def test_03d_direct_work_only_has_no_report(conn, worker, runners, admin):
    task_id = import_issue(conn, 1, assignee_ids=[], assignee_logins=[])
    work = repo.work_item_of_task(conn, task_id)
    member_id = repo.find_member_by_email(conn, SESSION, "admin@example.com")["member_id"]
    assert repo.start_direct_work(conn, SESSION, work["work_item_id"], member_id=member_id, branch="RUN-1", now=NOW)

    worker.tick()

    assert repo.list_executions(conn, task_id) == []
    assert conn.execute("SELECT COUNT(*) FROM handoff_reports").fetchone()[0] == 0
    runners["billing"].idle()
    assert "data-stage-report" not in stage_list(admin, format_work_key(work["key_number"]))


# --- 4. 옛 러너 -----------------------------------------------------------------------------------------------


def test_04_old_runner_gets_unavailable_no_wait_and_the_same_claim_bytes(conn, app, worker, cycle, admin):
    old = {name: runner_of(app, cycle, name, capabilities=None) for name in ("billing", "shop")}
    task_id = import_issue(conn, 1)
    worker.tick()
    fix = old["billing"].take()
    old["billing"].fix(fix)
    worker.tick()

    (report,) = reports_of(conn, fix["execution_id"])
    assert (report["status"], report["reason"]) == ("unavailable", "러너가 리포트를 지원하지 않음 — 러너 재설치 필요")
    (review,) = review_tasks(conn, task_id)
    (review_exec,) = repo.list_executions(conn, review["task_id"])
    expected = ExecutionRequest.model_validate_json(review_exec["request_json"])

    response = old["billing"].claim()  # 대기 없이 바로
    assert response.status_code == 200
    assert response.content == json.dumps(expected.model_dump(mode="json"), ensure_ascii=False,
                                          separators=(",", ":")).encode()
    assert "handoff_report" not in response.json() and "job_type" not in response.json()
    line = stage_list(admin, format_work_key(repo.work_item_of_task(conn, task_id)["key_number"]))
    assert "리포트 없음(러너가 리포트를 지원하지 않음 — 러너 재설치 필요)" in line


# --- 5. 맞춤 풀이 -----------------------------------------------------------------------------------------------


def test_05_recipient_tailors_with_their_own_agent_and_sees_it(conn, app, worker, runners, cycle, admin):
    flow = fix_passed(conn, worker, runners)
    recipient = log_in_member(TestClient(app), display_name="받는이")
    recipient_id = next(m["member_id"] for m in repo.list_members(conn, SESSION) if m["display_name"] == "받는이")
    # 받는 사람 = 다음 단계 Agent(REVIEW)의 러너 주인(D7). 검토 실행은 billing 에 배정된 뒤라 Agent 만 받는 사람의 러너
    # shop 으로 옮긴다 — 받는 사람의 Agent 가 맞춤 풀이를 쓰는 모양(test_report_api 의 world 와 같다)
    conn.execute("UPDATE connectors SET owner_member_id = ? WHERE connector_id = ?", (recipient_id, cycle["shop"]))
    conn.execute("UPDATE agents SET connector_id = ? WHERE agent_id = ?", (cycle["shop"], REVIEW))
    conn.commit()
    write_report(runners["billing"], flow)
    runners["shop"].idle()  # 받는 사람의 러너도 리포트 능력을 선언했다 — 그래야 맞춤 풀이 후보다(D8)

    detail = recipient.get(f"/api/v1/reports/{flow['report_id']}").json()
    assert "tailor" in detail["actions"] and detail["parties"]["recipient"] == "받는이"
    assert {c["agent_id"] for c in detail["tailor_choices"]} == {REVIEW, FIX_SHOP}
    requested = recipient.post(f"/api/v1/reports/{flow['report_id']}/tailor", json={"agent_id": REVIEW})
    assert requested.status_code == 202, requested.text
    tailored_id = requested.json()["report_id"]

    job = runners["shop"].take()  # 받는 사람 Agent 의 러너가 맞춤 풀이 작업을 받는다
    assert (job["report_id"], job["job_type"], job["agent_id"]) == (tailored_id, "tailored", REVIEW)
    assert (job["checkout_commit"], job["base_commit"]) == (None, None)
    assert "claim:c1" in job["request"]
    response = runners["shop"].submit_report(tailored_id, tailored_body(tailored_id))
    assert response.status_code == 200, response.text
    runners["shop"].idle()
    # 다음 단계 실행에 실리는 것은 원본이다 — 맞춤 풀이는 사람용(D8)
    assert runners["billing"].take()["handoff_report"]["report_id"] == flow["report_id"]

    view = admin.get(f"/api/v1/reports/{tailored_id}").json()
    assert (view["status"], view["job_type"], view["base_report_id"]) == ("done", "tailored", flow["report_id"])
    assert view["report"] == tailored_body(tailored_id)
    assert view["evidence_check"]["claims"]["t1"] == {"verified": True, "unverified": []}
    listing = admin.get(f"/api/v1/work-items/{flow['key']}/reports").json()
    assert [t["report_id"] for t in listing["tailored"]] == [tailored_id]

    page = admin.get(f"/work/{flow['key']}/reports/{tailored_id}")
    assert page.status_code == 200 and f"맞춤 풀이 · 받는이 · {REVIEW}" in page.text
    assert "검토자는 쿠폰 조건 한 줄만 보면 됨" in page.text
    original = admin.get(f"/work/{flow['key']}/reports/{flow['report_id']}").text
    listing_html = re.search(r"data-tailored-list.*?</ul>", original, re.S).group(0)
    assert f"/work/{flow['key']}/reports/{tailored_id}" in listing_html


# --- 6. 근거 없는 id -------------------------------------------------------------------------------------------


def test_06_unknown_evidence_ids_are_marked_not_rejected(conn, worker, runners, admin):
    flow = fix_passed(conn, worker, runners)
    bogus = [
        {"id": "c1", "text": "커밋은 확인됨", "why": None, "evidence": [f"commit:{C1[:7]}"]},
        {"id": "c2", "text": "없는 산출물", "why": None, "evidence": ["artifact:art-unknown"]},
        {"id": "c3", "text": "연결 안 된 PR", "why": None, "evidence": ["pr:99", f"commit:{C1}"]},
        {"id": "c4", "text": "원본에서 쓸 수 없는 인용", "why": None, "evidence": ["claim:c1"]},
        {"id": "c5", "text": "근거 없음", "why": None, "evidence": []},
    ]
    write_report(runners["billing"], flow, lambda rid: original_body(rid, changes=bogus))

    row = repo.get_handoff_report(conn, flow["report_id"])
    assert row["status"] == "done"
    claims = json.loads(row["evidence_check_json"])["claims"]
    assert claims["c1"] == {"verified": True, "unverified": []}
    assert claims["c2"] == {"verified": False, "unverified": ["artifact:art-unknown"]}
    assert claims["c3"] == {"verified": False, "unverified": ["pr:99"]}
    assert claims["c4"] == {"verified": False, "unverified": ["claim:c1"]}
    assert claims["c5"] == {"verified": False, "unverified": []}

    text = admin.get(f"/work/{flow['key']}/reports/{flow['report_id']}").text
    for claim_id, verified in (("c1", True), ("c2", False), ("c3", False), ("c4", False), ("c5", False)):
        block = re.search(rf'<li id="claim-{claim_id}".*?</li>', text, re.S).group(0)
        assert ("근거 확인 안 됨" in block) is not verified, claim_id
    # 다음 단계는 그대로 리포트를 받는다
    assert runners["billing"].take()["handoff_report"]["report_id"] == flow["report_id"]
