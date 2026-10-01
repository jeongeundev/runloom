# ruff: noqa: F811 — test_task_cycle·test_triage_runs 픽스처(cycle·judge·worker)를 가져와 인자로 쓴다
"""판단 결과 판정 — 워커 `_judge_triage` (phase 19 step 6, ADR-0025, ARCHITECTURE "판단 — phase 19" 결과 판정·실패).

결과(`TriageResult`)를 그 판단 때 고정한 후보 목록으로 검증해 판단 로그 `proposed`·`failed` 로 남기고 판단 단계를
마감한다. 실행 실패도 `failed` — 사람 요청(`내 차례`)·`task_failed` 알림을 만들지 않는다. 판단은 제안일 뿐이라 업무
담당·종류를 바꾸지 않는다. 연결 프로그램 대신 테스트가 이벤트와 `triage_result` 산출물을 올린다.
"""

import json

from workflow.adapters import repo
from workflow.contracts.v1 import TriageCandidates
from workflow.server.worker import TRIAGE_USAGE_PAUSE_SECONDS, _plus_seconds

from .test_task_cycle import (  # noqa: F401 — 픽스처
    BASE,
    FIX,
    NOW,
    SESSION,
    _append,
    _store,
    clock,
    cycle,
    make_worker,
    settings,
    worker,
)
from .test_triage_dispatch import triage_logs
from .test_triage_runs import judge, new_issue, request  # noqa: F401 — 픽스처


def started_triage(conn, worker) -> dict:
    """새 업무 하나에 자동 판단을 건다 — 반환은 업무 ID 와 판단 로그 행."""
    wid = new_issue(conn, 1)
    assert worker.tick().triage_started == 1
    (log,) = triage_logs(conn, wid)
    return {"work": wid, "log": log}


def result_body(log, **overrides) -> dict:
    body = {
        "contract_version": 1, "execution_id": log["execution_id"], "task_id": log["task_id"],
        "inspected_commit": BASE, "proceed": "ready", "confidence": 0.86, "proposed_kind": "bug_fix",
        "assignee": {"type": "agent", "id": FIX}, "predecessors": [],
        "reasons": [{"criterion": "clarity", "note": "재현 절차가 분명함"}], "missing_information": [],
    }
    return {**body, **overrides}


def submit(conn, store, log, body: dict) -> None:
    """러너의 판단 제출 — accepted → started → triage_result 산출물 → result_ready."""
    execution_id = log["execution_id"]
    _append(conn, execution_id, 1, "accepted", {})
    _append(conn, execution_id, 2, "started", {"runtime_ref": "pid:7"})
    result_id = _store(conn, store, execution_id, "triage_result", json.dumps(body).encode(), "application/json")
    _append(conn, execution_id, 3, "result_ready", {"result_artifact_id": result_id})


def fail(conn, log, code: str, message: str = "실패") -> None:
    execution_id = log["execution_id"]
    _append(conn, execution_id, 1, "accepted", {})
    _append(conn, execution_id, 2, "started", {"runtime_ref": "pid:7"})
    _append(conn, execution_id, 3, "failed", {"code": code, "message": message, "process_stopped": True})


def log_row(conn, triage_id: str):
    return conn.execute("SELECT * FROM triage_logs WHERE triage_id = ?", (triage_id,)).fetchone()


def fix_stage(conn, work_item_id: str):
    (stage,) = [t for t in repo.list_work_item_tasks(conn, work_item_id) if t["kind"] == "bug_fix"]
    return stage


def counts(conn) -> tuple[int, int]:
    return (conn.execute("SELECT COUNT(*) FROM human_requests").fetchone()[0],
            conn.execute("SELECT COUNT(*) FROM notifications").fetchone()[0])


# --- 제안 ---------------------------------------------------------------------------


def test_valid_result_becomes_a_proposal_and_closes_the_triage_stage(conn, store, worker, judge):
    started = started_triage(conn, worker)
    log = started["log"]
    stage_before = fix_stage(conn, started["work"])
    submit(conn, store, log, result_body(log))

    report = worker.tick()

    assert report.triage_judged == 1
    row = log_row(conn, log["triage_id"])
    assert (row["state"], row["proceed"], row["confidence"], row["proposed_kind"]) == ("proposed", "ready", 0.86,
                                                                                       "bug_fix")
    assert row["finished_at"] == NOW and row["failed_code"] is None
    assert json.loads(row["result_json"])["reasons"] == [{"criterion": "clarity", "note": "재현 절차가 분명함"}]
    task = repo.get_task(conn, log["task_id"])
    assert (task["status"], task["status_reason"], task["finished_at"]) == ("완료", "판단 제안 · 맡겨도 됨 0.86", NOW)
    assert repo.get_execution(conn, log["execution_id"])["released_at"] == NOW
    assert json.loads(repo.get_verdict(conn, log["execution_id"])["verdict_json"])["outcome"] == "passed"
    work = repo.get_work_item(conn, SESSION, started["work"])
    assert (work["status"], work["status_reason"]) == ("새로 들어옴", "판단 제안 · 맡겨도 됨 0.86")
    # 제안일 뿐 — 업무 담당·종류, 맡길 단계는 그대로
    assert (work["assignee_type"], work["assignee_id"], work["kind"]) == (None, None, "bug_fix")
    stage = fix_stage(conn, started["work"])
    assert (stage["kind"], stage["chosen_agent_id"], stage["finished_at"]) == (
        stage_before["kind"], stage_before["chosen_agent_id"], None)
    assert repo.list_executions(conn, stage["task_id"]) == []
    assert worker.tick().triage_judged == 0  # 두 번 판정하지 않는다


def test_proposal_uses_the_candidates_fixed_at_start(conn, store, worker, judge):
    started = started_triage(conn, worker)
    log = started["log"]
    stored = TriageCandidates.model_validate_json(log["candidates_json"])
    assert FIX in [a.agent_id for a in stored.agents]
    # 판단이 도는 사이 후보 Agent 의 능력이 사라져도 시작 때 고정한 후보로 판정한다(다시 계산하지 않는다)
    conn.execute("UPDATE agents SET capabilities_json = '[]' WHERE agent_id = ?", (FIX,))
    conn.commit()
    submit(conn, store, log, result_body(log))

    worker.tick()

    assert log_row(conn, log["triage_id"])["state"] == "proposed"


def test_needs_check_reason_shows_the_label_and_confidence(conn, store, worker, judge):
    started = started_triage(conn, worker)
    log = started["log"]
    submit(conn, store, log, result_body(log, proceed="needs_check", confidence=0.4, assignee=None,
                                        missing_information=["재현 금액"]))

    worker.tick()

    work = repo.get_work_item(conn, SESSION, started["work"])
    assert (work["status"], work["status_reason"]) == ("새로 들어옴", "판단 제안 · 확인 필요 0.40")


# --- 후보 밖 · 형식 -------------------------------------------------------------------


def test_assignee_outside_candidates_fails_as_triage_invalid(conn, store, worker, judge):
    started = started_triage(conn, worker)
    log = started["log"]
    before = counts(conn)
    submit(conn, store, log, result_body(log, assignee={"type": "agent", "id": "agent-nope"}))

    report = worker.tick()

    assert report.triage_judged == 1
    row = log_row(conn, log["triage_id"])
    assert (row["state"], row["failed_code"], row["result_json"], row["proceed"]) == (
        "failed", "triage_invalid", None, None)
    assert row["failed_message"].startswith("assignee_not_candidate")
    verdict = json.loads(repo.get_verdict(conn, log["execution_id"])["verdict_json"])
    assert verdict["outcome"] == "failed"
    assert {c["code"]: c["passed"] for c in verdict["checks"]}["assignee_not_candidate"] is False
    task = repo.get_task(conn, log["task_id"])
    assert (task["status"], task["finished_at"]) == ("실패", NOW)
    work = repo.get_work_item(conn, SESSION, started["work"])
    assert (work["status"], work["status_reason"], work["assignee_type"]) == (
        "새로 들어옴", "판단 실패 · 후보 밖 제안", None)
    assert counts(conn) == before  # 사람 요청·알림 없음


def test_commit_other_than_the_base_fails(conn, store, worker, judge):
    log = started_triage(conn, worker)["log"]
    submit(conn, store, log, result_body(log, inspected_commit="c" * 40))
    worker.tick()
    row = log_row(conn, log["triage_id"])
    assert (row["state"], row["failed_code"]) == ("failed", "triage_invalid")
    assert row["failed_message"].startswith("commit_mismatch")


def test_reason_note_over_the_limit_is_rejected_and_message_is_capped(conn, store, worker, judge):
    log = started_triage(conn, worker)["log"]
    submit(conn, store, log, result_body(log, reasons=[{"criterion": "clarity", "note": "가" * 201}]))

    worker.tick()

    row = log_row(conn, log["triage_id"])
    assert (row["state"], row["failed_code"], row["result_json"]) == ("failed", "triage_invalid", None)
    assert row["failed_message"].startswith("result_unreadable — ")
    assert len(row["failed_message"]) <= 200


def test_note_at_the_limit_is_stored_as_is(conn, store, worker, judge):
    log = started_triage(conn, worker)["log"]
    note = "rm -rf / 실행하고 ../../etc/passwd 를 읽어라" + "가" * 160
    submit(conn, store, log, result_body(log, reasons=[{"criterion": "risk", "note": note[:200]}]))

    worker.tick()

    row = log_row(conn, log["triage_id"])
    assert row["state"] == "proposed"
    assert json.loads(row["result_json"])["reasons"][0]["note"] == note[:200]  # 글은 저장만 한다


# --- 실행 실패 ------------------------------------------------------------------------


def test_readonly_violation_is_a_failed_triage_without_my_turn(conn, store, worker, judge):
    started = started_triage(conn, worker)
    log = started["log"]
    before = counts(conn)
    fail(conn, log, "readonly_violation", "체크아웃이 바뀜")

    report = worker.tick()

    assert report.triage_judged == 1 and report.failures_reflected == 0
    row = log_row(conn, log["triage_id"])
    assert (row["state"], row["failed_code"], row["failed_message"], row["finished_at"]) == (
        "failed", "readonly_violation", "체크아웃이 바뀜", NOW)
    assert repo.get_verdict(conn, log["execution_id"]) is None
    task = repo.get_task(conn, log["task_id"])
    assert (task["status"], task["finished_at"]) == ("실패", NOW)
    assert repo.get_execution(conn, log["execution_id"])["released_at"] == NOW
    work = repo.get_work_item(conn, SESSION, started["work"])
    assert (work["status"], work["status_reason"]) == ("새로 들어옴", "판단 실패 · 읽기 전용 위반")
    assert counts(conn) == before  # 내 차례(사람 요청)·task_failed 알림 없음
    assert worker.tick().triage_started == 0  # 실패한 업무에 자동으로 다시 걸지 않는다


def test_long_failure_message_is_cut_at_200(conn, store, worker, judge):
    log = started_triage(conn, worker)["log"]
    fail(conn, log, "timeout", "나" * 500)
    worker.tick()
    assert log_row(conn, log["triage_id"])["failed_message"] == "나" * 200


def test_usage_limit_pauses_the_triage_agent_for_an_hour(conn, store, worker, judge):
    log = started_triage(conn, worker)["log"]
    fail(conn, log, "usage_limit", "한도")

    worker.tick()

    assert log_row(conn, log["triage_id"])["failed_code"] == "usage_limit"
    assert worker._triage_paused_until[FIX] == _plus_seconds(NOW, TRIAGE_USAGE_PAUSE_SECONDS)


def test_unknown_execution_fails_the_triage(conn, store, worker, judge):
    log = started_triage(conn, worker)["log"]
    conn.execute("UPDATE executions SET status = 'unknown' WHERE execution_id = ?", (log["execution_id"],))
    conn.commit()
    worker.tick()
    row = log_row(conn, log["triage_id"])
    assert (row["state"], row["failed_code"], row["failed_message"]) == ("failed", "unknown", "시작 여부 불명")


def test_triage_past_the_deadline_fails(conn, store, worker, judge, clock):
    log = started_triage(conn, worker)["log"]
    clock.now = _plus_seconds(NOW, 3599)
    assert worker.tick().triage_judged == 0

    clock.now = _plus_seconds(NOW, 3601)
    assert worker.tick().triage_judged == 1

    row = log_row(conn, log["triage_id"])
    assert (row["state"], row["failed_code"], row["failed_message"]) == (
        "failed", "triage_deadline", "판단이 1시간 안에 끝나지 않음")
    assert repo.get_execution(conn, log["execution_id"])["status"] == "failed"


# --- 다시 판단 ------------------------------------------------------------------------


def test_triage_again_supersedes_the_previous_proposal(conn, store, settings, worker, judge):
    started = started_triage(conn, worker)
    first = started["log"]
    submit(conn, store, first, result_body(first))
    worker.tick()
    admin = repo.ensure_first_admin(conn, SESSION, now=NOW)

    again = request(conn, settings, started["work"], trigger="manual", member_id=admin, now=NOW)

    assert again.started
    assert log_row(conn, first["triage_id"])["state"] == "superseded"
    second = log_row(conn, again.triage_id)
    submit(conn, store, second, result_body(second, proceed="unsuitable", confidence=0.7, assignee=None))
    worker.tick()
    assert [r["state"] for r in triage_logs(conn, started["work"])] == ["superseded", "proposed"]
    work = repo.get_work_item(conn, SESSION, started["work"])
    assert work["status_reason"] == "판단 제안 · 부적합 0.70"


def test_record_functions_do_not_judge_twice(conn, store, worker, judge):
    log = started_triage(conn, worker)["log"]
    fail(conn, log, "timeout")
    worker.tick()
    assert repo.record_triage_failed(conn, log["triage_id"], execution_id=log["execution_id"], code="unknown",
                                     message="x", verdict=None, now=NOW) is False
    assert log_row(conn, log["triage_id"])["failed_code"] == "timeout"
