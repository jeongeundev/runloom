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
    assert agent["online"] is True  # API 에이전트는 heartbeat 가 없으므로 connection_state 만 본다
    assert "credential_ref" not in agent
    assert not any("wfc_" in str(v) for v in agent.values())


def test_agent_online_rule_by_connection_type(seeded, settings):
    """로컬은 online + heartbeat 이내, API 는 connection_state 만 (heartbeat 를 보내지 않는다)."""
    ops = repo.get_agent(seeded, "agent-ops-demo")
    assert ops["connection_type"] == "api" and ops["last_seen_at"] is None
    assert views.agent_online(ops, now=NOW, settings=settings) is True
    repo.set_agent_connection(seeded, "agent-ops-demo", "offline", None)
    assert views.agent_online(repo.get_agent(seeded, "agent-ops-demo"), now=NOW, settings=settings) is False

    codex = repo.get_agent(seeded, "agent-codex-mac")
    assert codex["connection_type"] == "local"
    repo.set_agent_connection(seeded, "agent-codex-mac", "online", None)
    assert views.agent_online(repo.get_agent(seeded, "agent-codex-mac"), now=NOW, settings=settings) is False
    repo.set_agent_connection(seeded, "agent-codex-mac", "online", NOW)
    assert views.agent_online(repo.get_agent(seeded, "agent-codex-mac"), now=NOW, settings=settings) is True


def test_kst_day_bounds():
    since, resets_at = views.kst_day_bounds("2026-09-20T00:00:00Z")  # 09:00 KST
    assert since == "2026-09-19T15:00:00.000000Z"
    assert resets_at == "2026-09-21T00:00:00+09:00"
    since, resets_at = views.kst_day_bounds("2026-09-20T14:59:59.999999Z")  # 23:59:59 KST
    assert since == "2026-09-19T15:00:00.000000Z"
    since, resets_at = views.kst_day_bounds("2026-09-20T15:00:00Z")  # 다음 날 00:00 KST
    assert since == "2026-09-20T15:00:00.000000Z"
    assert resets_at == "2026-09-22T00:00:00+09:00"


# --- Step 7: 화면 헬퍼 -----------------------------------------------------------

DIFF_TEXT = (
    "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1,2 +1,3 @@\n context\n-old\n+new\n+added\n"
    "diff --git a/b.py b/b.py\n--- a/b.py\n+++ b/b.py\n@@ -0,0 +1,1 @@\n+only\n"
)


def test_diff_stats_counts_files_and_changed_lines():
    assert views.diff_stats(DIFF_TEXT) == (2, 3, 1)
    assert views.diff_stats("") == (0, 0, 0)
    assert views.diff_stats("--- a\n+++ b\n+x\n-y\n") == (1, 1, 1)  # 헤더 줄은 세지 않는다


def test_diff_lines_classifies_each_line():
    kinds = [kind for kind, _ in views.diff_lines(DIFF_TEXT)]
    assert kinds[:8] == ["meta", "meta", "meta", "hunk", "ctx", "del", "add", "add"]
    assert views.diff_lines("+x\n")[0] == ("add", "+x")
    assert views.diff_lines("") == []


def test_tail_lines_keeps_last_n():
    assert views.tail_lines("a\nb\nc\n", 2) == ["b", "c"]
    assert views.tail_lines("", 5) == []


def _store_evidence(conn, store, execution_id: str, sources: dict) -> dict:
    from workflow.contracts.v1 import ArtifactMeta

    from .conftest import meta_for

    ids = {}
    for (evidence_id, version), (content_type, text) in sources.items():
        data = text.encode()
        created, _ = repo.store_artifact(
            conn, store, execution_id=execution_id, session_id="sess-1",
            meta=ArtifactMeta.model_validate(meta_for(data, kind="evidence", name=evidence_id,
                                                       content_type=content_type)),
            data=data, now=NOW,
        )
        ids[(evidence_id, version)] = created.artifact_id
    return ids


def test_evidence_excerpts_resolve_locations_from_stored_attachments(seeded, settings, store):
    _select(seeded, TASK_A, "agent-ops-demo", CAP_A)
    seed_execution(seeded, "exec-1", TASK_A, kind="diagnosis", inputs=())
    ids = _store_evidence(seeded, store, "exec-1", {
        ("response-after", "1"): ("application/json", '{"report_date": "2026-09-19", "data": {"records": [{"team": "운영"}]}}'),
        ("log-daily-0920", "1"): ("text/plain", "line one\nline two\nline three\n"),
    })
    result = {
        "findings": [
            {"claim": "a", "evidence_refs": [
                {"evidence_id": "response-after", "version": "1", "location": "$.data.records"},
                {"evidence_id": "log-daily-0920", "version": "1", "location": "lines:2-3"},
                {"evidence_id": "log-daily-0920", "version": "1", "location": "lines:9-9"},
                {"evidence_id": "missing-doc", "version": "1", "location": "$.x"},
            ]},
        ],
        "attachments": [
            {"evidence_id": "response-after", "version": "1", "content_type": "application/json",
             "artifact_id": ids[("response-after", "1")], "sha256": "0" * 64},
            {"evidence_id": "log-daily-0920", "version": "1", "content_type": "text/plain",
             "artifact_id": ids[("log-daily-0920", "1")], "sha256": "0" * 64},
            {"evidence_id": "other-session", "version": "1", "content_type": "text/plain",
             "artifact_id": ids[("log-daily-0920", "1")], "sha256": "0" * 64},
        ],
    }
    excerpts = views.evidence_excerpts(seeded, store, result, session_id="sess-1")
    assert excerpts["response-after@1 · $.data.records"] == {"found": True, "text": '[\n  {\n    "team": "운영"\n  }\n]'}
    assert excerpts["log-daily-0920@1 · lines:2-3"] == {"found": True, "text": "line two\nline three"}
    assert excerpts["log-daily-0920@1 · lines:9-9"] == {"found": False, "text": "원문에 없음"}
    assert excerpts["missing-doc@1 · $.x"] == {"found": False, "text": "첨부 없음"}
    # 다른 세션의 산출물은 읽지 않는다
    result["findings"][0]["evidence_refs"] = [{"evidence_id": "log-daily-0920", "version": "1", "location": "lines:1-1"}]
    assert views.evidence_excerpts(seeded, store, result, session_id="sess-other") == {
        "log-daily-0920@1 · lines:1-1": {"found": False, "text": "첨부 없음"},
    }


def test_viewer_context_for_diagnosis_reads_verdict_and_response_pair(seeded, settings, store):
    _select(seeded, TASK_A, "agent-ops-demo", CAP_A)
    seed_execution(seeded, "exec-1", TASK_A, kind="diagnosis", inputs=())
    ids = _store_evidence(seeded, store, "exec-1", {
        ("run-daily-0919-0900", "1"): ("application/json", '{"response_ref": {"evidence_id": "response-before", "version": "1"}}'),
        ("run-daily-0920-0900", "1"): ("application/json", '{"response_ref": {"evidence_id": "response-after", "version": "1"}}'),
        ("response-before", "1"): ("application/json", '{"items": []}'),
        ("response-after", "1"): ("application/json", '{"data": {"records": []}}'),
    })
    body = {
        "outcome": "ready_for_handoff", "summary": "s", "findings": [], "missing_information": [],
        "diagnosis": {"code": "response_path_changed", "baseline_run_id": "daily-0919-0900",
                      "failed_run_id": "daily-0920-0900", "old_path": "$.items", "new_path": "$.data.records"},
        "attachments": [
            {"evidence_id": e, "version": v, "content_type": "application/json", "artifact_id": a, "sha256": "0" * 64}
            for (e, v), a in ids.items()
        ],
    }
    artifact_id = seed_result_ready(seeded, store, "exec-1", kind="diagnosis_result", body=body)
    seeded.execute(
        "INSERT INTO task_verdicts (task_id, execution_id, verdict_json, decided_at) VALUES (?, ?, ?, ?)",
        (TASK_A, "exec-1", json.dumps({"outcome": "passed", "checks": [{"code": "c1", "passed": True, "detail": "ok"}]}), NOW),
    )
    ctx = views.task_context(seeded, store, repo.get_task(seeded, TASK_A), now=NOW, settings=settings)
    viewer = views.viewer_context(seeded, store, ctx["result"], session_id="sess-1")
    assert viewer["kind"] == "diagnosis_result"
    assert viewer["artifact_id"] == artifact_id
    assert viewer["verdict"]["outcome"] == "passed"
    assert (viewer["verdict_passed"], viewer["verdict_total"]) == (1, 1)
    assert viewer["compare"]["before"]["path"] == "$.items"
    assert '"items": []' in viewer["compare"]["before"]["text"]
    assert viewer["compare"]["after"]["path"] == "$.data.records"
    assert '"records": []' in viewer["compare"]["after"]["text"]
    assert viewer["raw_text"].startswith("{")
    assert views.viewer_context(seeded, store, None, session_id="sess-1") is None


def test_viewer_context_for_code_change_reads_logs_diff_and_report(seeded, settings, store):
    from workflow.contracts.v1 import ArtifactMeta

    from .conftest import meta_for

    _select(seeded, TASK_B, "agent-codex-mac", CAP_B)
    seed_execution(seeded, "exec-fix-001", TASK_B)
    for kind, text in (("diff", DIFF_TEXT), ("test_log_before", "\n".join(str(n) for n in range(30))),
                       ("test_log_after", "ok\n"), ("report_output", "보고서\n")):
        data = text.encode()
        repo.store_artifact(
            seeded, store, execution_id="exec-fix-001", session_id="sess-1",
            meta=ArtifactMeta.model_validate(meta_for(data, kind=kind, name=kind)), data=data, now=NOW,
        )
    seed_result_ready(seeded, store, "exec-fix-001", kind="code_change_result",
                      body=code_change_result("exec-fix-001", TASK_B))
    ctx = views.task_context(seeded, store, repo.get_task(seeded, TASK_B), now=NOW, settings=settings)
    viewer = views.viewer_context(seeded, store, ctx["result"], session_id="sess-1")
    assert viewer["kind"] == "code_change_result"
    assert viewer["diff"]["stats"] == (2, 3, 1)
    assert viewer["diff"]["lines"][5] == ("del", "-old")
    assert viewer["test_before"]["lines"] == [str(n) for n in range(10, 30)]
    assert viewer["test_after"]["lines"] == ["ok"]
    assert viewer["report"]["text"] == "보고서\n"
    assert viewer["verdict"] is None
    empty = views.viewer_context(seeded, store, {"kind": "code_change_result", "artifact_id": "x",
                                                  "execution_id": "exec-none", "data": None}, session_id="sess-1")
    assert empty["diff"] is None and empty["test_before"] is None and empty["report"] is None


def test_execution_context_has_duration_and_progress_count(seeded, settings, store):
    _select(seeded, TASK_A, "agent-ops-demo", CAP_A)
    seed_execution(seeded, "exec-1", TASK_A, kind="diagnosis", inputs=())
    for body in (
        event("exec-1", 1, "accepted", {}, occurred_at="2026-09-20T09:00:00+09:00"),
        event("exec-1", 2, "started", {"runtime_ref": "r"}, occurred_at="2026-09-20T09:00:05+09:00"),
        event("exec-1", 3, "progress", {"message": "하나"}, occurred_at="2026-09-20T09:01:00+09:00"),
        event("exec-1", 4, "progress", {"message": "둘"}, occurred_at="2026-09-20T09:02:00+09:00"),
    ):
        repo.append_event(seeded, "exec-1", ExecutionEvent.model_validate(body), actor="diag", now=NOW)
    ctx = views.task_context(seeded, store, repo.get_task(seeded, TASK_A), now="2026-09-20T00:04:18Z", settings=settings)
    [execution] = ctx["executions"]
    assert execution["duration_seconds"] == 253  # started 09:00:05 KST → now 09:04:18 KST
    assert execution["progress_count"] == 2
    assert execution["last_progress"] == "둘"

    repo.append_event(
        seeded, "exec-1",
        ExecutionEvent.model_validate(event("exec-1", 5, "failed", {"code": "timeout", "message": "x", "process_stopped": True},
                                            occurred_at="2026-09-20T09:03:00+09:00")),
        actor="diag", now=NOW,
    )
    ctx = views.task_context(seeded, store, repo.get_task(seeded, TASK_A), now="2026-09-20T01:00:00Z", settings=settings)
    assert ctx["executions"][0]["duration_seconds"] == 175  # 종료 이벤트까지


def test_artifact_render_context_by_kind():
    diff = views.artifact_render({"kind": "diff", "content_type": "text/plain"}, DIFF_TEXT.encode())
    assert diff["mode"] == "diff" and diff["diff_lines"][0][0] == "meta"
    log = views.artifact_render({"kind": "test_log_after", "content_type": "text/plain"}, b"a\nb\n")
    assert log["mode"] == "log" and log["lines"] == ["a", "b"]
    for kind in ("codex_jsonl", "codex_stderr", "claude_jsonl", "claude_stderr"):
        assert views.artifact_render({"kind": kind, "content_type": "text/plain"}, b"x\n")["mode"] == "log"
    js = views.artifact_render({"kind": "evidence", "content_type": "application/json"}, b'{"a":1}')
    assert js["mode"] == "json" and js["text"] == '{\n  "a": 1\n}'
    broken = views.artifact_render({"kind": "evidence", "content_type": "application/json"}, b"{oops")
    assert broken["mode"] == "text" and broken["text"] == "{oops"
