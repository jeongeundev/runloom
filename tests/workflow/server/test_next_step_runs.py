# ruff: noqa: F811 — test_task_cycle·test_triage_runs 픽스처(cycle·judge·settings)를 가져와 인자로 쓴다
"""결과 뒤 판단 시작·기록·대상 조회 — phase 22 step 5 (ADR-0027, ARCHITECTURE "결과 뒤 판단 — phase 22" 시작 조건·
원인 거르기·결과 뒤 판단 repo).

원인은 수정 Agent 의 `needs_information` 결과(②) — 워커 한 바퀴로 판정까지 만든 뒤 `request_next_step` 을 직접 부른다
(워커가 언제 부르는지는 step 6). 실제 연결 프로그램·GitHub·모델을 부르지 않는다.
"""

import json

import pytest

from workflow.adapters import repo, responsibility_store
from workflow.adapters.errors import NextStepExists
from workflow.contracts.responsibility import Responsibility
from workflow.contracts.v1 import (
    ExecutionRequest,
    TriageCandidates,
    TriageResult,
    TriageTarget,
)
from workflow.domain.next_step import PriorResult
from workflow.server import next_step_runs, triage_runs
from workflow.server.next_step_runs import NextStepCause
from workflow.server.triage_runs import REASONS

from .test_task_cycle import (  # noqa: F401 — 픽스처
    BASE,
    FIX,
    NOW,
    REG_FIX,
    SESSION,
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
from .test_triage_runs import LATER, OFFLINE, judge, logs, new_issue, work  # noqa: F401

SHA = "0" * 64
RESULT_BASE = {"contract_version": 1, "confidence": 0.8, "missing_information": [], "inspected_commit": BASE,
               "proposed_kind": None, "assignee": None, "predecessors": []}


@pytest.fixture
def admin(conn, cycle) -> str:
    return repo.ensure_first_admin(conn, SESSION, now=NOW)


@pytest.fixture
def capable(conn, judge) -> dict:
    """판단 Agent(FIX) 러너가 결과 뒤 판단 능력을 보고했다."""
    repo.record_runner_capabilities(conn, judge["billing"], ["verify_only", "after_result_triage"])
    conn.commit()
    return judge


@pytest.fixture
def needs_info(conn, store, worker, capable) -> NextStepCause:
    """RUN-1 수정 결과가 `needs_information`(판정까지) — 원래 사람 요청은 답한 것으로 치워 업무 상태가 판단을 보인다."""
    fix_task = import_issue(conn, 1)
    worker.tick()
    fix_exec = executions(conn, fix_task)[0]["execution_id"]
    finish_fix(conn, store, fix_exec, outcome="needs_information")
    worker.tick()
    conn.execute("UPDATE human_requests SET state = 'answered', answered_at = ?", (NOW,))
    conn.commit()
    wid = repo.work_item_of_task(conn, fix_task)["work_item_id"]
    return NextStepCause(cause="after_result", session_id=SESSION, work_item_id=wid, task_id=fix_task,
                         execution_id=fix_exec, request_id=None, at=repo.get_verdict(conn, fix_exec)["decided_at"],
                         fallback="original_request")


def start(conn, settings, cause: NextStepCause, *, now: str = LATER):
    return next_step_runs.request_next_step(conn, settings, cause=cause, now=now)


def proposed_result(log, action: dict, *, proceed: str = "ready") -> TriageResult:
    return TriageResult.model_validate({
        **RESULT_BASE, "execution_id": log["execution_id"], "task_id": log["task_id"], "proceed": proceed,
        "reasons": [{"criterion": "clarity", "note": "호스트 설정값이 필요"}], "next_action": action,
    })


INTERNAL = {"type": "internal_request", "system_id": "kube_proxy", "request_kind": "investigation",
            "recipient_member_id": "mem-x", "purpose": "호스트 sysctl 값 확인"}


def finish_log(conn, log, *, state: str = "failed") -> None:
    """판단 실행을 끝낸 것으로 — 원인 그대로, 판단 단계 잠금만 푼다."""
    if state == "failed":
        conn.execute("UPDATE triage_logs SET state = 'failed', failed_code = 'timeout' WHERE triage_id = ?",
                     (log["triage_id"],))
    else:
        conn.execute("UPDATE triage_logs SET state = 'proposed', result_json = ?, proceed = 'ready', confidence = 0.8"
                     " WHERE triage_id = ?", (proposed_result(log, INTERNAL).model_dump_json(), log["triage_id"]))
    conn.execute("UPDATE executions SET released_at = ? WHERE execution_id = ?", (LATER, log["execution_id"]))
    conn.commit()


def spare_stage(conn, cause: NextStepCause) -> tuple[str, str]:
    """판단 로그 행이 가리킬 별도 단계·실행(판단 로그의 `task_id`·`execution_id` 는 UNIQUE) — 원인 Task·실행을 베껴
    끝난 단계·해제된 시도로 둔다. 반환은 (task_id, execution_id)."""
    n = conn.execute("SELECT COUNT(*) FROM executions").fetchone()[0]
    task_id, execution_id = f"task-spare{n:04d}", f"exec-spare{n:04d}"
    row = dict(conn.execute("SELECT * FROM tasks WHERE task_id = ?", (cause.task_id,)).fetchone())
    row.update(task_id=task_id, status="완료", finished_at=NOW)
    conn.execute(f"INSERT INTO tasks ({', '.join(row)}) VALUES ({', '.join('?' for _ in row)})", tuple(row.values()))
    conn.execute(
        "INSERT INTO executions (execution_id, task_id, attempt_no, start_key, agent_id, kind, request_json, status,"
        " created_at, released_at) SELECT ?, ?, 1, 'req:spare', agent_id, kind, request_json, 'failed', created_at,"
        " created_at FROM executions WHERE execution_id = ?", (execution_id, task_id, cause.execution_id))
    return task_id, execution_id


def insert_intake_log(conn, wid: str, cause: NextStepCause, *, state="proposed", handling=None,
                      proceed="ready", kind="bug_fix", created_at=NOW) -> str:
    triage_id = f"trg-in{abs(hash((wid, state, handling, created_at))) % 10**6:06d}"
    conn.execute(
        "INSERT INTO triage_logs (triage_id, session_id, work_item_id, work_revision, task_id, execution_id, agent_id,"
        " trigger, criteria_version, input_sha256, candidates_json, state, result_json, proceed, confidence,"
        " proposed_kind, failed_code, handling, created_at, updated_at)"
        " VALUES (?, ?, ?, 1, ?, ?, ?, 'auto', 1, ?, '{}', ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (triage_id, SESSION, wid, *spare_stage(conn, cause), FIX, SHA, state,
         "{}" if state == "proposed" else None, proceed if state == "proposed" else None,
         0.9 if state == "proposed" else None, kind if state == "proposed" else None,
         "timeout" if state == "failed" else None, handling, created_at, created_at),
    )
    conn.commit()
    return triage_id


# --- 경로 ---------------------------------------------------------------------------


def test_next_step_route_does_not_require_a_new_unassigned_work(conn, settings, needs_info):
    found = next_step_runs.next_step_route(conn, needs_info, now=LATER, settings=settings)
    assert found.reason is None
    assert (found.agent_id, found.repository_id, found.stage["task_id"]) == (FIX, "billing", needs_info.task_id)
    assert triage_runs.triage_route(conn, work(conn, needs_info.work_item_id), now=LATER,
                                    settings=settings).reason == REASONS["not_new"]  # 접수 경로는 그대로


@pytest.mark.parametrize("capabilities", [None, ["verify_only"]])
def test_runner_without_after_result_triage_is_refused(conn, settings, needs_info, capable, capabilities):
    repo.record_runner_capabilities(conn, capable["billing"], capabilities)
    conn.commit()
    found = next_step_runs.next_step_route(conn, needs_info, now=LATER, settings=settings)
    assert found.reason == REASONS["runner_no_next_step"] == "러너 업데이트 필요 — 결과 뒤 판단 미지원"
    result = start(conn, settings, needs_info)
    assert (result.started, result.reason) == (False, REASONS["runner_no_next_step"])
    assert logs(conn, needs_info.work_item_id) == []


def test_intake_route_ignores_the_after_result_capability(conn, settings, judge):
    """접수 판단은 결과 뒤 판단 능력이 없어도 그대로 시작 가능."""
    wid = new_issue(conn, 7)
    assert triage_runs.triage_route(conn, work(conn, wid), now=LATER, settings=settings).reason is None


def test_next_step_route_shares_the_agent_checks(conn, settings, needs_info):
    assert next_step_runs.next_step_route(conn, needs_info, now=OFFLINE, settings=settings).reason == REASONS["offline"]
    repo.save_github_source(conn, SESSION, config(triage_agent_id=None), NOW)
    assert next_step_runs.next_step_route(conn, needs_info, now=LATER, settings=settings).reason == REASONS["no_agent"]


# --- 시작 ---------------------------------------------------------------------------


def test_request_next_step_opens_stage_execution_and_log_with_the_cause(conn, settings, needs_info):
    result = start(conn, settings, needs_info)

    assert result.started and result.triage_id.startswith("trg-")
    (log,) = logs(conn, needs_info.work_item_id)
    assert (log["cause"], log["cause_execution_id"], log["cause_request_id"]) == (
        "after_result", needs_info.execution_id, None)
    assert (log["state"], log["trigger"], log["requested_by_member_id"], log["agent_id"]) == (
        "running", "auto", None, FIX)
    stage = repo.get_task(conn, log["task_id"])
    assert (stage["kind"], stage["status"], stage["status_reason"]) == ("triage", "실행 요청됨", "다음 단계 판단 접수 대기")
    req = ExecutionRequest.model_validate_json(repo.get_execution(conn, log["execution_id"])["request_json"])
    assert req.target == TriageTarget(local_registration_id=REG_FIX, base_commit=BASE, mode="next_step")
    assert req.request.startswith("# 다음 단계 판단: RUN-1 ")
    assert "## 이전 결과" in req.request and "결과 needs_information" in req.request
    candidates = TriageCandidates.model_validate_json(log["candidates_json"])
    assert candidates.cause.model_dump() == {
        "cause": "after_result", "execution_id": needs_info.execution_id, "task_id": needs_info.task_id,
        "kind": "bug_fix", "agent_id": FIX, "outcome": "needs_information", "request_id": None}
    assert candidates.current_kind == "bug_fix" and candidates.predecessors == []
    # 원인 Task 는 그대로 — 판단은 제안만
    assert repo.get_task(conn, needs_info.task_id)["finished_at"] is None
    assert repo.get_execution(conn, needs_info.execution_id)["released_at"] is None
    assert repo.has_running_triage(conn, SESSION)  # 워크스페이스 1건 규칙은 두 원인을 합쳐 센다


def test_work_status_shows_the_next_step_while_running(conn, settings, needs_info):
    start(conn, settings, needs_info)
    row = work(conn, needs_info.work_item_id)
    assert (row["status"], row["status_reason"]) == ("에이전트 작업 중", "다음 단계 판단 중")


def test_same_cause_is_started_once(conn, settings, needs_info):
    first = start(conn, settings, needs_info)
    again = start(conn, settings, needs_info)  # 도는 중
    assert (again.started, again.triage_id, again.reason) == (False, None, "판단 중")
    (log,) = logs(conn, needs_info.work_item_id)
    finish_log(conn, log)
    after = start(conn, settings, needs_info)  # 끝난 뒤에도 같은 원인은 다시 시작하지 않는다
    assert (after.started, after.reason) == (False, "판단 중")
    assert [r["triage_id"] for r in logs(conn, needs_info.work_item_id)] == [first.triage_id]
    assert [r["state"] for r in logs(conn, needs_info.work_item_id)] == ["failed"]


def test_start_triage_raises_next_step_exists_for_the_same_cause(conn, settings, needs_info, monkeypatch):
    start(conn, settings, needs_info)
    (log,) = logs(conn, needs_info.work_item_id)
    finish_log(conn, log)
    calls = []
    real = repo.start_triage

    def spy(*args, **kwargs):
        try:
            return real(*args, **kwargs)
        except NextStepExists as exc:
            calls.append(exc.cause_key)
            raise

    monkeypatch.setattr(repo, "start_triage", spy)
    start(conn, settings, needs_info)
    assert calls == [f"after_result:{needs_info.execution_id}"]
    assert len(repo.list_work_item_tasks(conn, needs_info.work_item_id)) == 2  # 수정 단계 + 첫 판단 단계


def test_next_step_supersedes_only_the_same_family(conn, settings, needs_info):
    intake = insert_intake_log(conn, needs_info.work_item_id, needs_info)
    first = start(conn, settings, needs_info).triage_id
    assert repo.next_step_for_cause(conn, execution_id=needs_info.execution_id)["triage_id"] == first
    (_, log) = logs(conn, needs_info.work_item_id)
    finish_log(conn, log)
    # 다른 원인인 것처럼 — 앞 행의 원인 참조를 판단 실행으로 옮긴다(CHECK 그대로)
    conn.execute("UPDATE triage_logs SET cause_execution_id = execution_id WHERE triage_id = ?", (first,))
    conn.commit()
    second = start(conn, settings, needs_info).triage_id

    states = {r["triage_id"]: (r["cause"], r["state"]) for r in logs(conn, needs_info.work_item_id)}
    assert states == {intake: ("intake", "proposed"), first: ("after_result", "superseded"),
                      second: ("after_result", "running")}


def test_intake_triage_does_not_supersede_a_next_step(conn, settings, needs_info):
    first = start(conn, settings, needs_info).triage_id
    (log,) = logs(conn, needs_info.work_item_id)
    finish_log(conn, log, state="proposed")
    insert_intake_log(conn, needs_info.work_item_id, needs_info, state="failed")
    # 접수 판단 시작은 같은 계열(intake)만 대체한다
    conn.execute("UPDATE work_items SET status = '새로 들어옴', assignee_type = NULL, assignee_id = NULL")
    conn.execute("UPDATE tasks SET chosen_agent_id = NULL")
    conn.commit()
    started = triage_runs.request_triage(conn, settings, session_id=SESSION, work_item_id=needs_info.work_item_id,
                                         trigger="auto", member_id=None, now=LATER)
    assert started.started
    states = {r["triage_id"]: (r["cause"], r["state"]) for r in logs(conn, needs_info.work_item_id)}
    assert states[first] == ("after_result", "proposed")
    assert sorted(v for k, v in states.items() if k != first) == [("intake", "running"), ("intake", "superseded")]


# --- 기록·처리 ---------------------------------------------------------------------


def test_proposed_next_step_closes_the_stage_with_the_action_and_shows_on_the_work(conn, settings, needs_info):
    start(conn, settings, needs_info)
    (log,) = logs(conn, needs_info.work_item_id)
    assert repo.record_triage_proposed(conn, log["triage_id"], execution_id=log["execution_id"],
                                       result=proposed_result(log, INTERNAL), verdict={"outcome": "passed"}, now=LATER)
    stage = repo.get_task(conn, log["task_id"])
    assert (stage["status"], stage["status_reason"]) == ("완료", "다음 단계 제안 · 사내 요청")
    row = work(conn, needs_info.work_item_id)
    assert (row["status"], row["status_reason"]) == ("내 차례", "다음 단계 제안 · 사내 요청")
    assert repo.latest_next_step(conn, needs_info.work_item_id)["triage_id"] == log["triage_id"]
    assert repo.latest_triage(conn, needs_info.work_item_id) is None  # 접수 판단의 최신 행만 본다
    assert repo.next_step_cause_current(conn, repo.latest_next_step(conn, needs_info.work_item_id))


def test_failed_next_step_closes_the_stage_with_the_failure_name(conn, settings, needs_info):
    start(conn, settings, needs_info)
    (log,) = logs(conn, needs_info.work_item_id)
    assert repo.record_triage_failed(conn, log["triage_id"], execution_id=log["execution_id"], code="triage_invalid",
                                     message="kind_not_candidate — 후보 밖 종류", verdict=None, now=LATER)
    stage = repo.get_task(conn, log["task_id"])
    assert (stage["status"], stage["status_reason"]) == ("실패", "다음 단계 판단 실패 · 후보 밖 제안")
    row = work(conn, needs_info.work_item_id)
    assert row["status_reason"] != "다음 단계 판단 중"


def test_cause_is_no_longer_current_after_release(conn, settings, needs_info):
    start(conn, settings, needs_info)
    (log,) = logs(conn, needs_info.work_item_id)
    repo.record_triage_proposed(conn, log["triage_id"], execution_id=log["execution_id"],
                                result=proposed_result(log, INTERNAL), verdict={"outcome": "passed"}, now=LATER)
    conn.execute("UPDATE executions SET released_at = ? WHERE execution_id = ?", (LATER, needs_info.execution_id))
    conn.commit()
    latest = repo.latest_next_step(conn, needs_info.work_item_id)
    assert not repo.next_step_cause_current(conn, latest)
    repo.refresh_work_status(conn, needs_info.work_item_id, now=LATER)
    conn.commit()
    assert work(conn, needs_info.work_item_id)["status_reason"] != "다음 단계 제안 · 사내 요청"


def test_record_next_step_handling_on_an_assigned_work(conn, settings, needs_info, admin):
    assert work(conn, needs_info.work_item_id)["assignee_type"] == "agent"  # 담당이 이미 있다
    start(conn, settings, needs_info)
    (log,) = logs(conn, needs_info.work_item_id)
    human = {"type": "human", "question": "끝난 것 같습니다 — 닫을까요?"}
    repo.record_triage_proposed(conn, log["triage_id"], execution_id=log["execution_id"],
                                result=proposed_result(log, human), verdict={"outcome": "passed"}, now=LATER)

    with repo._tx(conn):
        assert repo.record_next_step_handling(conn, log["triage_id"], handling="accepted", member_id=admin, now=LATER)
    with repo._tx(conn):
        assert not repo.record_next_step_handling(conn, log["triage_id"], handling="dismissed", member_id=admin,
                                                  now=LATER)
    row = repo.latest_next_step(conn, needs_info.work_item_id)
    assert (row["handling"], row["handled_by_member_id"], row["handled_at"]) == ("accepted", admin, LATER)
    assert (row["final_assignee_type"], row["final_kind"]) == (None, None)
    assert work(conn, needs_info.work_item_id)["assignee_id"] == FIX  # 원래 업무 담당은 그대로


def test_record_next_step_handling_does_not_touch_intake_rows(conn, needs_info, admin):
    intake = insert_intake_log(conn, needs_info.work_item_id, needs_info)
    with repo._tx(conn):
        assert not repo.record_next_step_handling(conn, intake, handling="accepted", member_id=admin, now=LATER)
    assert repo.latest_triage(conn, needs_info.work_item_id)["handling"] is None


def test_dismiss_next_step(conn, settings, needs_info, admin):
    start(conn, settings, needs_info)
    (log,) = logs(conn, needs_info.work_item_id)
    assert not repo.dismiss_next_step(conn, SESSION, needs_info.work_item_id, log["triage_id"], member_id=admin,
                                      now=LATER)  # 도는 중
    repo.record_triage_proposed(conn, log["triage_id"], execution_id=log["execution_id"],
                                result=proposed_result(log, INTERNAL), verdict={"outcome": "passed"}, now=LATER)
    assert not repo.dismiss_next_step(conn, SESSION, needs_info.work_item_id, "trg-other", member_id=admin, now=LATER)
    assert repo.dismiss_next_step(conn, SESSION, needs_info.work_item_id, log["triage_id"], member_id=admin, now=LATER)
    assert not repo.dismiss_next_step(conn, SESSION, needs_info.work_item_id, log["triage_id"], member_id=admin,
                                      now=LATER)
    row = repo.latest_next_step(conn, needs_info.work_item_id)
    assert (row["handling"], row["handled_by_member_id"]) == ("dismissed", admin)
    assert work(conn, needs_info.work_item_id)["status_reason"] != "다음 단계 제안 · 사내 요청"


# --- 접수 판단 조회는 결과 뒤 판단에 영향받지 않는다 ---------------------------------------


def test_intake_queries_ignore_next_step_rows(conn, settings, judge, needs_info, admin):
    fresh = new_issue(conn, 9)
    other = NextStepCause(cause="after_result", session_id=SESSION, work_item_id=fresh, task_id=needs_info.task_id,
                          execution_id=needs_info.execution_id, request_id=None, at=NOW, fallback="none")
    assert [w["work_item_id"] for w in repo.auto_triage_works(conn, SESSION)] == [fresh]
    # 새 업무에 결과 뒤 판단 행이 있어도 접수 판단 대상
    conn.execute(
        "INSERT INTO triage_logs (triage_id, session_id, work_item_id, work_revision, task_id, execution_id, agent_id,"
        " trigger, criteria_version, input_sha256, candidates_json, state, result_json, proceed, confidence,"
        " proposed_kind, handling, handled_by_member_id, handled_at, cause, cause_execution_id, created_at, updated_at)"
        " VALUES ('trg-ns000001', ?, ?, 1, ?, ?, ?, 'auto', 1, '0000000000000000000000000000000000000000000000000000000000000000', '{}', 'proposed', '{}', 'ready', 0.9,"
        " 'bug_fix', 'accepted', ?, ?, 'after_result', ?, ?, ?)",
        (SESSION, fresh, *spare_stage(conn, other), FIX, admin, LATER, other.execution_id, LATER,
         LATER),
    )
    conn.commit()
    assert [w["work_item_id"] for w in repo.auto_triage_works(conn, SESSION)] == [fresh]
    assert repo.triage_handled_counts(conn, SESSION) == {}
    assert repo.latest_triage(conn, fresh) is None
    facts, outcomes = repo.list_triage_facts(conn, SESSION)
    assert facts == [] and outcomes == []
    (row,) = [r for r in repo.list_work_rows(conn, SESSION, closed_since=None) if r.work_item_id == fresh]
    assert row.triage is None


def test_autostart_candidates_keep_the_intake_proposal_under_a_later_next_step(conn, needs_info):
    conn.execute("UPDATE work_items SET status = '새로 들어옴', assignee_type = NULL, assignee_id = NULL")
    conn.commit()
    intake = insert_intake_log(conn, needs_info.work_item_id, needs_info)
    assert [r["triage_id"] for r in repo.autostart_candidates(conn)] == [intake]
    conn.execute(
        "INSERT INTO triage_logs (triage_id, session_id, work_item_id, work_revision, task_id, execution_id, agent_id,"
        " trigger, criteria_version, input_sha256, candidates_json, state, result_json, proceed, confidence, cause,"
        " cause_execution_id, created_at, updated_at) VALUES ('trg-ns000002', ?, ?, 1, ?, ?, ?, 'auto', 1, '0000000000000000000000000000000000000000000000000000000000000000',"
        " '{}', 'proposed', '{}', 'ready', 0.9, 'after_result', ?, ?, ?)",
        (SESSION, needs_info.work_item_id, *spare_stage(conn, needs_info), FIX,
         needs_info.execution_id, LATER, LATER),
    )
    conn.commit()
    assert [r["triage_id"] for r in repo.autostart_candidates(conn)] == [intake]


# --- 대상 조회 ---------------------------------------------------------------------


def test_cause_prior_reads_the_result_envelope(conn, store, needs_info):
    prior = repo.cause_prior(conn, store, needs_info.execution_id)
    assert prior == PriorResult(kind="bug_fix", kind_label=prior.kind_label, outcome="needs_information",
                                verdict="passed", summary="쿠폰 중복 적용 수정", missing_information=())
    assert prior.kind_label == "버그 수정"


def test_cause_prior_without_a_readable_result(conn, store, needs_info):
    conn.execute("UPDATE executions SET result_artifact_id = NULL WHERE execution_id = ?", (needs_info.execution_id,))
    conn.commit()
    prior = repo.cause_prior(conn, store, needs_info.execution_id)
    assert prior.summary == "(결과를 읽을 수 없음)"


def _generic_kind(conn) -> None:
    from workflow.contracts.v1 import KindSpec

    repo.insert_kind(conn, SESSION, KindSpec.model_validate({
        "kind": "docs", "label": "문서 정리", "builtin": False, "capability_code": "code.docs",
        "scope_key": "repository_id", "input_kinds": [], "output_kind": "generic_result", "outcomes": ["done"],
        "instructions": "",
    }), NOW)


def test_generic_results_awaiting_next_step(conn, needs_info):
    _generic_kind(conn)
    conn.execute("UPDATE tasks SET kind = 'docs' WHERE task_id = ?", (needs_info.task_id,))
    conn.execute("UPDATE task_verdicts SET verdict_json = ?, decided_at = ? WHERE execution_id = ?",
                 (json.dumps({"outcome": "passed"}), NOW, needs_info.execution_id))
    conn.commit()
    rows = repo.generic_results_awaiting_next_step(conn, since="2026-10-06T11:00:00Z")
    assert [(r["execution_id"], r["task_id"], r["work_item_id"], r["session_id"], r["decided_at"]) for r in rows] == [
        (needs_info.execution_id, needs_info.task_id, needs_info.work_item_id, SESSION, NOW)]
    assert repo.generic_results_awaiting_next_step(conn, since="2026-10-06T12:00:01Z") == []  # 상한 밖
    insert_intake_log(conn, needs_info.work_item_id, needs_info)  # 접수 판단 행은 원인을 막지 않는다
    assert len(repo.generic_results_awaiting_next_step(conn, since=NOW)) == 1
    conn.execute(
        "INSERT INTO triage_logs (triage_id, session_id, work_item_id, work_revision, task_id, execution_id, agent_id,"
        " trigger, criteria_version, input_sha256, candidates_json, state, cause, cause_execution_id, created_at,"
        " updated_at) VALUES ('trg-ns000003', ?, ?, 1, ?, ?, ?, 'auto', 1, '0000000000000000000000000000000000000000000000000000000000000000', '{}', 'running', 'after_result', ?,"
        " ?, ?)",
        (SESSION, needs_info.work_item_id, *spare_stage(conn, needs_info), FIX,
         needs_info.execution_id, LATER, LATER),
    )
    conn.commit()
    assert repo.generic_results_awaiting_next_step(conn, since=NOW) == []


def test_generic_results_skip_builtin_failed_and_successor_tasks(conn, needs_info):
    assert repo.generic_results_awaiting_next_step(conn, since=NOW) == []  # bug_fix 는 업무 순환 — 워커가 본다
    _generic_kind(conn)
    conn.execute("UPDATE tasks SET kind = 'docs' WHERE task_id = ?", (needs_info.task_id,))
    conn.execute("UPDATE task_verdicts SET verdict_json = ? WHERE execution_id = ?",
                 (json.dumps({"outcome": "failed"}), needs_info.execution_id))
    conn.commit()
    assert repo.generic_results_awaiting_next_step(conn, since=NOW) == []


def _returned_request(conn, needs_info, admin, *, request_id="ir-0001", returned_at=LATER, triage_id=None,
                      task_id=None) -> str:
    conn.execute(
        "INSERT INTO internal_requests (request_id, session_id, work_item_id, requester_member_id, recipient_member_id,"
        " judgment_member_id, system_id, request_kind, directory_revision, submission_key, purpose, state, revision,"
        " created_at, accepted_at, created_by_triage_id) VALUES (?, ?, ?, ?, ?, ?, 'kube_proxy', 'investigation', 1,"
        " ?, '호스트 sysctl 값', 'accepted', 1, ?, ?, ?)",
        (request_id, SESSION, needs_info.work_item_id, admin, admin, admin, request_id, NOW, NOW, triage_id),
    )
    artifact = repo.get_execution(conn, needs_info.execution_id)["result_artifact_id"]
    conn.execute(
        "INSERT INTO internal_request_investigations (request_id, task_id, created_at, returned_execution_id,"
        " returned_artifact_id, returned_summary, returned_at, returned_by_member_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (request_id, task_id or needs_info.task_id, NOW, needs_info.execution_id, artifact, "net.netfilter 값은 131072",
         returned_at, admin),
    )
    conn.commit()
    return request_id


def test_returned_requests_awaiting_next_step(conn, settings, needs_info, admin):
    rid = _returned_request(conn, needs_info, admin)
    (row,) = repo.returned_requests_awaiting_next_step(conn, since=NOW)
    assert (row["request_id"], row["session_id"], row["work_item_id"], row["returned_at"],
            row["created_by_triage_id"], row["cause_execution_id"]) == (
        rid, SESSION, needs_info.work_item_id, LATER, None, None)
    assert repo.returned_requests_awaiting_next_step(conn, since="2026-10-07T00:00:00Z") == []  # 사람이 만든 요청은 상한


def test_returned_request_made_by_a_next_step_carries_its_cause(conn, settings, needs_info, admin):
    first = start(conn, settings, needs_info).triage_id
    rid = _returned_request(conn, needs_info, admin, triage_id=first)
    (row,) = repo.returned_requests_awaiting_next_step(conn, since="2026-10-07T00:00:00Z")  # 판단이 만든 요청은 상한 밖도
    assert (row["request_id"], row["created_by_triage_id"], row["cause_execution_id"]) == (
        rid, first, needs_info.execution_id)


def test_returned_request_with_a_rejection_or_a_judgment_is_not_awaiting(conn, settings, needs_info, admin):
    rid = _returned_request(conn, needs_info, admin)
    log = logs(conn, needs_info.work_item_id)
    assert log == []
    conn.execute(
        "INSERT INTO triage_logs (triage_id, session_id, work_item_id, work_revision, task_id, execution_id, agent_id,"
        " trigger, criteria_version, input_sha256, candidates_json, state, cause, cause_execution_id, cause_request_id,"
        " failed_code, created_at, updated_at) VALUES ('trg-ns000004', ?, ?, 1, ?, ?, ?, 'auto', 1, '0000000000000000000000000000000000000000000000000000000000000000', '{}',"
        " 'failed', 'request_returned', ?, ?, 'timeout', ?, ?)",
        (SESSION, needs_info.work_item_id, *spare_stage(conn, needs_info), FIX,
         needs_info.execution_id, rid, LATER, LATER),
    )
    conn.commit()
    assert repo.returned_requests_awaiting_next_step(conn, since=NOW) == []
    assert repo.next_step_for_cause(conn, request_id=rid)["triage_id"] == "trg-ns000004"
    rid2 = _returned_request(conn, needs_info, admin, request_id="ir-0002", task_id=import_issue(conn, 2))
    conn.execute("INSERT INTO internal_request_rejections (request_id, reason, rejected_at) VALUES (?, '범위 밖', ?)",
                 (rid2, LATER))
    conn.commit()
    assert repo.returned_requests_awaiting_next_step(conn, since=NOW) == []


def test_request_returned_cause_is_current_only_while_returned_and_not_rejected(conn, settings, needs_info, admin):
    rid = _returned_request(conn, needs_info, admin)
    returned = NextStepCause(cause="request_returned", session_id=SESSION, work_item_id=needs_info.work_item_id,
                             task_id=needs_info.task_id, execution_id=needs_info.execution_id, request_id=rid,
                             at=LATER, fallback="none")
    result = start(conn, settings, returned)
    assert result.started
    log = repo.next_step_for_cause(conn, request_id=rid)
    assert (log["cause"], log["cause_request_id"], log["cause_execution_id"]) == (
        "request_returned", rid, needs_info.execution_id)
    req = ExecutionRequest.model_validate_json(repo.get_execution(conn, log["execution_id"])["request_json"])
    assert "## 반환된 사내 요청" in req.request and "net.netfilter 값은 131072" in req.request
    candidates = TriageCandidates.model_validate_json(log["candidates_json"])
    assert candidates.cause.request_id == rid
    assert repo.next_step_cause_current(conn, log)
    conn.execute("INSERT INTO internal_request_rejections (request_id, reason, rejected_at) VALUES (?, '범위 밖', ?)",
                 (rid, LATER))
    conn.commit()
    assert not repo.next_step_cause_current(conn, log)


# --- 후보 ---------------------------------------------------------------------------


def test_candidates_include_active_responsibilities_only(conn, settings, needs_info, admin):
    gone = repo.add_member(conn, SESSION, display_name="박담당", now=NOW)
    responsibility_store.replace_entries(conn, SESSION, [
        Responsibility(system_id="kube_proxy", request_kind="investigation", recipient_member_id=admin,
                       judgment_member_id=admin, agent_id=None),
        Responsibility(system_id="kubelet", request_kind="investigation", recipient_member_id=gone,
                       judgment_member_id=admin, agent_id=FIX),
    ], expected_revision=repo.get_config_revision(conn, SESSION), member_id=admin, now=NOW)
    conn.execute("UPDATE members SET disabled_at = ? WHERE member_id = ?", (NOW, gone))
    conn.commit()
    found = next_step_runs.next_step_route(conn, needs_info, now=LATER, settings=settings)
    candidates = next_step_runs.build_next_step_candidates(conn, needs_info, found, now=LATER, settings=settings)
    admin_name = repo.get_member(conn, SESSION, admin)["display_name"]
    assert [r.model_dump() for r in candidates.responsibilities] == [{
        "system_id": "kube_proxy", "request_kind": "investigation", "recipient_member_id": admin,
        "recipient_name": admin_name, "judgment_member_id": admin, "agent_id": None}]
    assert [m.member_id for m in candidates.members] == [admin]
    assert [a.agent_id for a in candidates.agents] == [FIX]
    assert [(k.kind, k.startable) for k in candidates.kinds][0] == ("bug_fix", True)
