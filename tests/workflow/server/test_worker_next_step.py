# ruff: noqa: F811 — test_task_cycle·test_triage_runs 픽스처(cycle·judge·worker)를 가져와 인자로 쓴다
"""워커의 결과 뒤 판단 — phase 22 step 6 (ADR-0027, ARCHITECTURE "결과 뒤 판단 — phase 22" 흐름·원인과 시작 지점·처분).

① 규칙 없는 결과(업무 순환 `no_rule`·사용자 정의 종류의 후속 없는 결과)와 ② `needs_information` 에서 판단 Agent 에
결과 뒤 판단을 건다. ② 는 시작할 수 없거나 판단이 실패·무시되면 원래 사람 요청(같은 원인 키)을 연다. 워크스페이스에
도는 판단 1건·러너 빔 규칙은 접수 판단과 같고, 결과 뒤 판단이 먼저 고른다. 연결 프로그램 대신 테스트가 이벤트와
산출물을 올린다 — 실제 러너·GitHub·모델을 부르지 않는다.
"""

import json

import pytest

from workflow.adapters import repo
from workflow.contracts.v1 import ExecutionRequest, KindSpec, LocalTarget
from workflow.domain.next_step import NEXT_STEP_WAIT_SECONDS
from workflow.server.worker import _plus_seconds

from .test_task_cycle import (  # noqa: F401 — 픽스처
    BASE,
    FIX,
    NOW,
    REG_FIX,
    SESSION,
    _append,
    _store,
    clock,
    config,
    cycle,
    executions,
    finish_fix,
    import_issue,
    make_worker,
    settings,
    worker,
)
from .test_triage_dispatch import touch, triage_logs
from .test_triage_judge import fail, submit
from .test_triage_runs import judge, new_issue  # noqa: F401 — 픽스처

HUMAN = {"type": "human", "question": "재현 금액을 알려 주세요"}


@pytest.fixture
def capable(conn, judge) -> dict:
    """판단 Agent(FIX) 러너가 결과 뒤 판단 능력을 보고했다."""
    repo.record_runner_capabilities(conn, judge["billing"], ["verify_only", "after_result_triage"])
    conn.commit()
    return judge


def fix_result(conn, store, worker, number: int = 1, **finish) -> tuple[str, str]:
    """GitHub 이슈의 수정 단계를 착수하고 결과를 올린다(판정은 다음 tick). 반환은 (수정 Task, 실행)."""
    task_id = import_issue(conn, number)
    worker.tick()
    execution_id = executions(conn, task_id)[0]["execution_id"]
    finish_fix(conn, store, execution_id, **finish)
    return task_id, execution_id


def work_of(conn, task_id: str):
    return repo.work_item_of_task(conn, task_id)


def requests_of(conn, task_id: str) -> list[tuple[str, str]]:
    return [(r["code"], r["cause_key"]) for r in repo.list_human_requests(conn, task_id)]


def next_body(log, action: dict | None = None, **overrides) -> dict:
    body = {
        "contract_version": 1, "execution_id": log["execution_id"], "task_id": log["task_id"],
        "inspected_commit": BASE, "proceed": "ready", "confidence": 0.82, "proposed_kind": None, "assignee": None,
        "predecessors": [], "reasons": [{"criterion": "clarity", "note": "재현 금액이 필요"}],
        "missing_information": [], "next_action": action or HUMAN,
    }
    return {**body, **overrides}


def only_log(conn, work_item_id: str):
    (log,) = triage_logs(conn, work_item_id)
    return log


# --- ② needs_information ---------------------------------------------------------------


def test_needs_information_starts_a_next_step_instead_of_a_human_request(conn, store, worker, capable):
    task_id, execution_id = fix_result(conn, store, worker, outcome="needs_information")

    report = worker.tick()

    assert (report.next_step_started, report.triage_started, report.human_requests) == (1, 0, 0)
    assert requests_of(conn, task_id) == []
    wid = work_of(conn, task_id)["work_item_id"]
    log = only_log(conn, wid)
    assert (log["cause"], log["cause_execution_id"], log["state"], log["trigger"]) == (
        "after_result", execution_id, "running", "auto")
    request = ExecutionRequest.model_validate_json(repo.get_execution(conn, log["execution_id"])["request_json"])
    assert request.target.mode == "next_step"
    work = repo.get_work_item(conn, SESSION, wid)
    assert (work["status"], work["status_reason"]) == ("에이전트 작업 중", "다음 단계 판단 중")
    # 원인 Task 는 그대로 — 판단은 제안만
    assert repo.get_task(conn, task_id)["finished_at"] is None
    assert repo.get_execution(conn, execution_id)["released_at"] is None


def test_proposed_next_step_is_judged_with_the_next_step_rules(conn, store, worker, capable):
    task_id, _ = fix_result(conn, store, worker, outcome="needs_information")
    worker.tick()
    wid = work_of(conn, task_id)["work_item_id"]
    log = only_log(conn, wid)
    submit(conn, store, log, next_body(log))

    report = worker.tick()

    assert report.triage_judged == 1
    row = only_log(conn, wid)
    assert (row["state"], row["proceed"], row["confidence"], row["proposed_kind"]) == ("proposed", "ready", 0.82, None)
    assert json.loads(row["result_json"])["next_action"] == HUMAN
    stage = repo.get_task(conn, log["task_id"])
    assert (stage["status"], stage["status_reason"]) == ("완료", "다음 단계 제안 · 사람 확인")
    work = repo.get_work_item(conn, SESSION, wid)
    assert (work["status"], work["status_reason"]) == ("내 차례", "다음 단계 제안 · 사람 확인")
    # 제안이 떠 있는 동안 원래 사람 요청은 열지 않는다
    worker.tick()
    assert requests_of(conn, task_id) == []
    assert report.next_step_started == 0 and len(triage_logs(conn, wid)) == 1


def test_intake_rules_are_not_used_for_a_next_step(conn, store, worker, capable):
    """접수 판단 모양(제안 종류·담당, next_action 없음)은 결과 뒤 판단에서 후보 밖 제안이다."""
    task_id, execution_id = fix_result(conn, store, worker, outcome="needs_information")
    worker.tick()
    wid = work_of(conn, task_id)["work_item_id"]
    log = only_log(conn, wid)
    submit(conn, store, log, next_body(log, next_action=None, proposed_kind="bug_fix",
                                       assignee={"type": "agent", "id": FIX}))

    worker.tick()

    row = only_log(conn, wid)
    assert (row["state"], row["failed_code"]) == ("failed", "triage_invalid")
    assert row["failed_message"].startswith("next_action_missing — ")
    assert requests_of(conn, task_id) == [("fix_needs_information", f"fix_needs_information:{execution_id}")]


def test_invalid_next_step_opens_the_original_request_once(conn, store, worker, make_worker, capable):
    task_id, execution_id = fix_result(conn, store, worker, outcome="needs_information")
    worker.tick()
    wid = work_of(conn, task_id)["work_item_id"]
    log = only_log(conn, wid)
    submit(conn, store, log, next_body(log, {"type": "stage", "kind": "nope", "rework": False,
                                             "assignee": {"type": "agent", "id": FIX}}))

    report = worker.tick()

    row = only_log(conn, wid)
    assert (row["state"], row["failed_code"]) == ("failed", "triage_invalid")
    assert row["failed_message"].startswith("kind_not_candidate — ")
    assert (report.human_requests, report.next_step_fallbacks) == (1, 1)
    assert requests_of(conn, task_id) == [("fix_needs_information", f"fix_needs_information:{execution_id}")]
    assert repo.get_task(conn, task_id)["status"] == "확인 필요"
    # 다시 돌거나 재시작해도 판단을 다시 걸지 않고, 요청은 하나
    worker.tick()
    make_worker().tick()
    assert len(requests_of(conn, task_id)) == 1
    assert len(triage_logs(conn, wid)) == 1


def test_failed_next_step_execution_opens_the_original_request(conn, store, worker, capable):
    task_id, execution_id = fix_result(conn, store, worker, outcome="needs_information")
    worker.tick()
    log = only_log(conn, work_of(conn, task_id)["work_item_id"])
    fail(conn, log, "tool_error")

    report = worker.tick()

    assert report.triage_judged == 1
    assert requests_of(conn, task_id) == [("fix_needs_information", f"fix_needs_information:{execution_id}")]


def test_next_step_past_the_deadline_opens_the_original_request(conn, store, worker, clock, capable):
    task_id, execution_id = fix_result(conn, store, worker, outcome="needs_information")
    worker.tick()
    later = _plus_seconds(NOW, 3601)
    clock.now = later
    touch(conn, capable, later)

    worker.tick()

    log = only_log(conn, work_of(conn, task_id)["work_item_id"])
    assert (log["state"], log["failed_code"]) == ("failed", "triage_deadline")
    assert requests_of(conn, task_id) == [("fix_needs_information", f"fix_needs_information:{execution_id}")]


def test_dismissed_next_step_opens_the_original_request(conn, store, worker, capable):
    task_id, execution_id = fix_result(conn, store, worker, outcome="needs_information")
    worker.tick()
    wid = work_of(conn, task_id)["work_item_id"]
    log = only_log(conn, wid)
    submit(conn, store, log, next_body(log))
    worker.tick()
    admin = repo.ensure_first_admin(conn, SESSION, now=NOW)
    assert repo.dismiss_next_step(conn, SESSION, wid, log["triage_id"], member_id=admin, now=NOW)

    worker.tick()

    assert requests_of(conn, task_id) == [("fix_needs_information", f"fix_needs_information:{execution_id}")]


@pytest.mark.parametrize("capabilities", [None, ["verify_only"]])
def test_runner_without_after_result_triage_asks_a_human_as_before(conn, store, worker, capable, capabilities):
    repo.record_runner_capabilities(conn, capable["billing"], capabilities)
    conn.commit()
    task_id, execution_id = fix_result(conn, store, worker, outcome="needs_information")

    report = worker.tick()

    assert (report.next_step_started, report.human_requests) == (0, 1)
    assert requests_of(conn, task_id) == [("fix_needs_information", f"fix_needs_information:{execution_id}")]
    assert triage_logs(conn) == []


def test_work_without_a_triage_agent_asks_a_human_as_before(conn, store, worker, cycle):
    task_id, execution_id = fix_result(conn, store, worker, outcome="needs_information")

    report = worker.tick()

    assert (report.next_step_started, report.human_requests) == (0, 1)
    assert requests_of(conn, task_id) == [("fix_needs_information", f"fix_needs_information:{execution_id}")]
    assert triage_logs(conn) == []


def test_busy_runner_keeps_the_cause_waiting_until_the_limit(conn, store, worker, clock, capable):
    """러너가 비지 않으면 사람 요청 없이 기다리고, 원인 나이가 상한을 넘으면 원래 사람 요청."""
    task_id, execution_id = fix_result(conn, store, worker, outcome="needs_information")
    busy = import_issue(conn, 2)  # 같은 러너의 다른 수정 — 이번 tick 에 착수해 러너를 차지한다

    report = worker.tick()

    assert executions(conn, busy) and not repo.runner_idle(conn, capable["billing"])
    assert (report.next_step_started, report.human_requests) == (0, 0)
    assert triage_logs(conn) == [] and requests_of(conn, task_id) == []
    later = _plus_seconds(NOW, NEXT_STEP_WAIT_SECONDS + 1)
    clock.now = later
    touch(conn, capable, later)

    worker.tick()

    assert requests_of(conn, task_id) == [("fix_needs_information", f"fix_needs_information:{execution_id}")]
    assert triage_logs(conn) == []


# --- ① 규칙 없는 결과 ------------------------------------------------------------------


def _drop_rules(conn) -> None:
    for rule_id, _ in repo.list_rules(conn, SESSION):
        repo.delete_rule(conn, SESSION, rule_id, now=NOW)


def test_fix_result_without_a_rule_starts_a_next_step(conn, store, worker, capable):
    _drop_rules(conn)
    task_id, execution_id = fix_result(conn, store, worker)

    report = worker.tick()

    assert report.next_step_started == 1
    log = only_log(conn, work_of(conn, task_id)["work_item_id"])
    assert (log["cause"], log["cause_execution_id"]) == ("after_result", execution_id)
    assert requests_of(conn, task_id) == []


def test_failed_next_step_on_a_result_without_a_rule_stays_quiet(conn, store, worker, capable):
    """① 의 대체 경로는 없다 — 지금처럼 `확인 필요`, 사람 요청·새 판단 없음."""
    _drop_rules(conn)
    task_id, _ = fix_result(conn, store, worker)
    worker.tick()
    before = repo.get_task(conn, task_id)["status"]
    log = only_log(conn, work_of(conn, task_id)["work_item_id"])
    fail(conn, log, "tool_error")

    report = worker.tick()
    worker.tick()

    assert report.human_requests == 0 and requests_of(conn, task_id) == []
    assert repo.get_task(conn, task_id)["status"] == before == "확인 필요"
    assert len(triage_logs(conn)) == 1


def test_fix_result_with_a_rule_follows_the_rule(conn, store, worker, capable):
    task_id, _ = fix_result(conn, store, worker)
    report = worker.tick()
    assert report.followup_tasks_created == 1 and report.next_step_started == 0
    assert triage_logs(conn) == []


def test_failed_verdict_and_failed_execution_do_not_start_a_next_step(conn, store, worker, capable):
    task_id, execution_id = fix_result(conn, store, worker, before_exit=0)  # 판정 실패
    worker.tick()
    assert requests_of(conn, task_id) == [("fix_verification_failed", f"fix_verification_failed:{execution_id}")]
    other = import_issue(conn, 2)
    worker.tick()
    (run,) = executions(conn, other)
    fail(conn, run, "tool_error")  # 실행 실패
    worker.tick()
    worker.tick()
    assert triage_logs(conn) == []


DOCS = KindSpec(kind="docs", label="문서 정리", capability_code="code.docs", scope_key="repository_id",
                input_kinds=[], output_kind="generic_result", outcomes=["done"], instructions="", builtin=False)


def generic_stage(conn, store, *, outcome: str = "done") -> tuple[str, str]:
    """GitHub 업무의 수정 단계를 끝내고 같은 업무에 사용자 정의 종류 단계 하나 — 결과까지 올린다(판정은 다음 tick).
    반환은 (단계, 실행)."""
    fix_task = import_issue(conn, 1)
    wid = work_of(conn, fix_task)["work_item_id"]
    conn.execute("UPDATE tasks SET status = '완료', finished_at = ? WHERE task_id = ?", (NOW, fix_task))
    conn.commit()
    repo.insert_kind(conn, SESSION, DOCS, NOW)
    capability = {"code": "code.docs", "scope": {"repository_id": "billing"}}
    task_id, execution_id = "task-docs-1", "exec-docs-1"
    repo.insert_task(conn, {
        "task_id": task_id, "session_id": SESSION, "title": "문서 정리", "request": "README 를 고치세요.",
        "kind": "docs", "required_capability": capability, "selection_mode": "manual", "chosen_agent_id": FIX,
        "run_mode": "auto", "completion_mode": "review", "criteria": [], "predecessor_task_id": None,
        "revision": 1, "target": {}, "status": "실행 요청됨", "status_reason": "접수 대기",
    }, NOW, work_item_id=wid)
    request = ExecutionRequest(
        contract_version=1, execution_id=execution_id, task_id=task_id, kind="docs", agent_id=FIX, task_revision=1,
        request="README 를 고치세요.", input_artifact_ids=[], target=LocalTarget(local_registration_id=REG_FIX),
        kind_spec=DOCS,
    )
    repo.create_execution(conn, execution_id=execution_id, task_id=task_id, attempt_no=1, start_key="req:docs-1",
                          agent_id=FIX, kind="docs", request=request, assigned_connector_id=None,
                          predecessor_execution_id=None, now=NOW)
    _append(conn, execution_id, 1, "accepted", {})
    _append(conn, execution_id, 2, "started", {"runtime_ref": "pid:3"})
    body = {"contract_version": 1, "execution_id": execution_id, "task_id": task_id, "kind": "docs",
            "outcome": outcome, "summary": "README 정리함", "artifact_ids": []}
    result_id = _store(conn, store, execution_id, "generic_result", json.dumps(body).encode(), "application/json")
    _append(conn, execution_id, 3, "result_ready", {"result_artifact_id": result_id})
    return task_id, execution_id


def test_generic_result_without_a_successor_starts_a_next_step(conn, store, worker, capable):
    task_id, execution_id = generic_stage(conn, store)

    report = worker.tick()

    assert (report.generic_checked, report.next_step_started) == (1, 1)
    log = only_log(conn, work_of(conn, task_id)["work_item_id"])
    assert (log["cause"], log["cause_execution_id"]) == ("after_result", execution_id)
    assert requests_of(conn, task_id) == []


def test_generic_result_failing_its_verdict_does_not_start_a_next_step(conn, store, worker, capable):
    generic_stage(conn, store, outcome="unknown_outcome")
    report = worker.tick()
    assert (report.generic_checked, report.next_step_started) == (1, 0)
    assert triage_logs(conn) == []


def test_generic_result_without_a_capable_runner_stays_as_before(conn, store, worker, capable):
    repo.record_runner_capabilities(conn, capable["billing"], None)
    conn.commit()
    task_id, _ = generic_stage(conn, store)
    report = worker.tick()
    assert report.next_step_started == 0 and triage_logs(conn) == [] and requests_of(conn, task_id) == []


# --- 접수 판단과 우선순위 · 1건 ----------------------------------------------------------


def test_next_step_goes_before_intake_and_takes_the_single_slot(conn, store, worker, capable):
    task_id, execution_id = fix_result(conn, store, worker, outcome="needs_information")
    fresh = new_issue(conn, 2)  # 담당 없는 새 업무 — 접수 판단 대상

    report = worker.tick()

    assert (report.next_step_started, report.triage_started) == (1, 0)
    assert triage_logs(conn, fresh) == []
    assert [r["cause"] for r in triage_logs(conn)] == ["after_result"]
    assert repo.next_step_for_cause(conn, execution_id=execution_id) is not None


def test_running_intake_triage_holds_the_next_step(conn, store, worker, capable):
    """워크스페이스에 도는 판단(접수)이 있으면 결과 뒤 판단은 다음 tick 으로 — 사람 요청도 열지 않는다."""
    fresh = new_issue(conn, 2)
    worker.tick()  # 새 업무에 접수 판단(러너가 비어 있다)
    (intake,) = triage_logs(conn, fresh)
    task_id, _ = fix_result(conn, store, worker, number=1, outcome="needs_information")

    report = worker.tick()

    assert report.next_step_started == 0 and requests_of(conn, task_id) == []
    assert [r["cause"] for r in triage_logs(conn)] == ["intake"]
    submit(conn, store, intake, {
        "contract_version": 1, "execution_id": intake["execution_id"], "task_id": intake["task_id"],
        "inspected_commit": BASE, "proceed": "ready", "confidence": 0.9, "proposed_kind": "bug_fix",
        "assignee": {"type": "agent", "id": FIX}, "predecessors": [],
        "reasons": [{"criterion": "clarity", "note": "분명함"}], "missing_information": [],
    })
    assert worker.tick().next_step_started == 1


def test_restart_does_not_start_the_same_cause_twice(conn, store, worker, make_worker, capable):
    task_id, _ = fix_result(conn, store, worker, outcome="needs_information")
    worker.tick()
    again = make_worker()
    assert again.tick().next_step_started == 0
    assert len(triage_logs(conn)) == 1
