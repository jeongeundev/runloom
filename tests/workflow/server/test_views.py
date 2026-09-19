"""views.py — DB 행에서 TaskView·템플릿 컨텍스트를 조립한다. 판정은 domain.status 가 하고 여기서는 재료만 모은다."""

import json

from workflow.adapters import repo
from workflow.contracts.v1 import ExecutionEvent, SelectionRecord
from workflow.server import views

from .conftest import (
    NOW,
    RESULT_COMMIT,
    TASK_A,
    TASK_B,
    code_change_result,
    event,
    seed_execution,
    seed_result_ready,
)

CAP_A = {"code": "operations.diagnose", "scope": {"workflow_id": "daily-report"}}
CAP_B = {"code": "code.modify", "scope": {"repository_id": "demo-report-repo"}}


def _select(conn, task_id: str, agent_id: str, capability: dict) -> None:
    repo.save_selection(conn, SelectionRecord.model_validate({
        "task_id": task_id,
        "mode": "auto",
        "required_capability": capability,
        "candidate_count": 1,
        "selected_agent_id": agent_id,
        "matched": capability,
        "status": "selected",
        "reason": f"{capability['code']} 일치 후보 1개",
    }))


def _view(conn, settings, task_id: str, now: str = NOW):
    return views.build_task_view(conn, repo.get_task(conn, task_id), now=now, settings=settings)


# --- build_task_view ----------------------------------------------------------


def test_view_without_selection_record_is_needs_selection(seeded, settings):
    view = _view(seeded, settings, TASK_A)
    assert view.selection_status == "needs_selection"
    assert view.selection_reason == "후보 없음"
    assert view.selected_agent_id is None
    assert view.execution_status is None
    assert view.connector_online is None  # 진단은 연결 프로그램과 무관
    assert view.finished is False


def test_view_diagnosis_selected_and_runnable(seeded, settings):
    _select(seeded, TASK_A, "agent-ops-demo", CAP_A)
    view = _view(seeded, settings, TASK_A)
    assert (view.kind, view.run_mode, view.completion_mode) == ("diagnosis", "manual", "review")
    assert view.selection_status == "selected"
    assert view.selected_agent_id == "agent-ops-demo"
    assert view.predecessor_status is None
    assert views.status_of(repo.get_task(seeded, TASK_A), view).label == "실행 가능"


def test_view_code_change_reads_predecessor_status_and_connector_heartbeat(seeded, settings):
    _select(seeded, TASK_B, "agent-codex-mac", CAP_B)
    view = _view(seeded, settings, TASK_B)
    assert view.predecessor_status == "실행 가능"  # A 는 아직 완료가 아님
    assert view.connector_online is False  # last_seen_at 없음
    assert view.connector_last_seen == "없음"

    repo.update_task_status(seeded, TASK_A, "완료", "검토 승인", finished_at=NOW, review_decision="approve")
    repo.set_agent_connection(seeded, "agent-codex-mac", "online", "2026-09-20T00:00:00Z")
    fresh = _view(seeded, settings, TASK_B, now="2026-09-20T00:01:00Z")  # 60초 뒤: 90초 이내
    assert fresh.predecessor_status == "완료"
    assert fresh.connector_online is True
    assert fresh.connector_last_seen == "2026-09-20 09:00:00 KST"

    stale = _view(seeded, settings, TASK_B, now="2026-09-20T00:02:00Z")  # 120초 뒤: 90초 초과
    assert stale.connector_online is False
    assert views.status_of(repo.get_task(seeded, TASK_B), stale).reason == "연결 끊김, 마지막 확인 2026-09-20 09:00:00 KST"


def test_view_reads_active_execution_progress_and_failure(seeded, settings):
    _select(seeded, TASK_A, "agent-ops-demo", CAP_A)
    seed_execution(seeded, "exec-1", TASK_A, kind="diagnosis", inputs=())
    for body in (
        event("exec-1", 1, "accepted", {}),
        event("exec-1", 2, "started", {"runtime_ref": "diag-run-1"}),
        event("exec-1", 3, "progress", {"message": "get_run 조회 완료"}),
    ):
        repo.append_event(seeded, "exec-1", ExecutionEvent.model_validate(body), actor="diag", now=NOW)
    view = _view(seeded, settings, TASK_A)
    assert view.execution_status == "running"
    assert view.last_progress == "get_run 조회 완료"
    assert view.verdict is None

    repo.append_event(
        seeded, "exec-1",
        ExecutionEvent.model_validate(event("exec-1", 4, "failed", {"code": "timeout", "message": "5분 초과", "process_stopped": True})),
        actor="diag", now=NOW,
    )
    view = _view(seeded, settings, TASK_A)
    assert (view.execution_status, view.failed_code, view.failed_message, view.process_stopped) == (
        "failed", "timeout", "5분 초과", True)


def test_view_summarises_verdict_from_task_verdicts(seeded, settings, store):
    _select(seeded, TASK_A, "agent-ops-demo", CAP_A)
    seed_execution(seeded, "exec-1", TASK_A, kind="diagnosis", inputs=())
    seed_result_ready(seeded, store, "exec-1", kind="diagnosis_result", body={"outcome": "ready_for_handoff"})
    verdict = {"outcome": "passed", "checks": [
        {"code": "attachments_in_trace", "passed": True, "detail": "8건"},
        {"code": "paths_differ_as_claimed", "passed": True, "detail": "확인"},
    ]}
    seeded.execute(
        "INSERT INTO task_verdicts (task_id, execution_id, verdict_json, decided_at) VALUES (?, ?, ?, ?)",
        (TASK_A, "exec-1", json.dumps(verdict), NOW),
    )
    view = _view(seeded, settings, TASK_A)
    assert (view.verdict, view.verdict_detail) == ("passed", "판정 근거: 2/2")

    verdict["outcome"] = "failed"
    verdict["checks"][1] = {"code": "paths_differ_as_claimed", "passed": False, "detail": "old_path 존재"}
    seeded.execute(
        "INSERT INTO task_verdicts (task_id, execution_id, verdict_json, decided_at) VALUES (?, ?, ?, ?)",
        (TASK_A, "exec-1", json.dumps(verdict), "2026-09-20T00:00:01Z"),
    )
    view = _view(seeded, settings, TASK_A)
    assert (view.verdict, view.verdict_detail) == ("failed", "미충족: paths_differ_as_claimed")


# --- status_of: 마감된 Task 는 DB 값 고정 ------------------------------------------


def test_status_of_uses_stored_status_once_finished(seeded, settings):
    repo.update_task_status(seeded, TASK_A, "완료", "판정 근거: 12/12", finished_at=NOW)
    row = repo.get_task(seeded, TASK_A)
    status = views.status_of(row, _view(seeded, settings, TASK_A))
    assert (status.label, status.reason) == ("완료", "판정 근거: 12/12")


# --- task_context ----------------------------------------------------------------


def test_task_context_collects_executions_result_and_actions(seeded, settings, store):
    _select(seeded, TASK_B, "agent-codex-mac", CAP_B)
    seed_execution(seeded, "exec-fix-001", TASK_B)
    artifact_id = seed_result_ready(
        seeded, store, "exec-fix-001", kind="code_change_result",
        body=code_change_result("exec-fix-001", TASK_B),
    )
    ctx = views.task_context(seeded, store, repo.get_task(seeded, TASK_B), now=NOW, settings=settings)

    assert ctx["task"]["task_id"] == TASK_B
    assert ctx["task"]["required_capability"] == CAP_B
    assert ctx["task"]["criteria"][0]["text"] == "근거 검증 통과"
    assert ctx["selection"].selected_agent_id == "agent-codex-mac"
    assert ctx["agent"]["agent_id"] == "agent-codex-mac"
    assert "credential_ref" not in ctx["agent"]
    assert (ctx["status"].label, ctx["status"].reason) == ("확인 필요", "검토 대기")
    assert ctx["predecessor"]["task_id"] == TASK_A
    assert ctx["successors"] == []

    [execution] = ctx["executions"]
    assert execution["attempt_no"] == 1 and execution["status"] == "result_ready"
    assert [e["type"] for e in execution["events"]] == ["accepted", "started", "result_ready"]
    assert execution["events"][2]["data"] == {"result_artifact_id": artifact_id}
    assert [a["artifact_id"] for a in execution["artifacts"]] == [artifact_id]

    assert ctx["result"]["kind"] == "code_change_result"
    assert ctx["result"]["artifact_id"] == artifact_id
    assert ctx["result"]["data"]["result_commit"] == RESULT_COMMIT
    assert ctx["can_review"] is True
    assert ctx["can_run"] is False
    assert ctx["needs_selection"] is False


def test_task_context_flags_run_and_selection(seeded, settings, store):
    ctx = views.task_context(seeded, store, repo.get_task(seeded, TASK_A), now=NOW, settings=settings)
    assert ctx["needs_selection"] is True
    assert [c["agent_id"] for c in ctx["candidates"]] == ["agent-codex-mac", "agent-ops-demo"]
    assert ctx["can_run"] is False
    assert ctx["result"] is None
    assert ctx["executions"] == []

    _select(seeded, TASK_A, "agent-ops-demo", CAP_A)
    ctx = views.task_context(seeded, store, repo.get_task(seeded, TASK_A), now=NOW, settings=settings)
    assert ctx["needs_selection"] is False
    assert ctx["can_run"] is True
    assert ctx["successors"][0]["task_id"] == TASK_B


# --- 보조 ------------------------------------------------------------------------


def test_task_summary_and_agent_public(seeded, settings):
    _select(seeded, TASK_A, "agent-ops-demo", CAP_A)
    summary = views.task_summary(seeded, repo.get_task(seeded, TASK_A), now=NOW, settings=settings)
    assert summary["task_id"] == TASK_A
    assert summary["title"] == "일일 보고서 실패 진단"
    assert summary["status"].label == "실행 가능"
    assert summary["created_at"] == NOW

    agent = views.agent_public(repo.get_agent(seeded, "agent-ops-demo"), now=NOW, settings=settings)
    assert agent["agent_id"] == "agent-ops-demo"
    assert agent["capabilities"][0]["code"] == "operations.diagnose"
    assert agent["discovered"] == {}
    assert agent["online"] is False  # online 이지만 last_seen_at 이 없다
    assert "credential_ref" not in agent
    assert not any("wfc_" in str(v) for v in agent.values())


def test_kst_day_bounds():
    since, resets_at = views.kst_day_bounds("2026-09-20T00:00:00Z")  # 09:00 KST
    assert since == "2026-09-19T15:00:00.000000Z"
    assert resets_at == "2026-09-21T00:00:00+09:00"
    since, resets_at = views.kst_day_bounds("2026-09-20T14:59:59.999999Z")  # 23:59:59 KST
    assert since == "2026-09-19T15:00:00.000000Z"
    since, resets_at = views.kst_day_bounds("2026-09-20T15:00:00Z")  # 다음 날 00:00 KST
    assert since == "2026-09-20T15:00:00.000000Z"
    assert resets_at == "2026-09-22T00:00:00+09:00"
