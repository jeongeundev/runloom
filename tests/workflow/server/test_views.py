"""views.py — DB 행에서 TaskView·템플릿 컨텍스트를 조립한다. 판정은 domain.status 가 하고 여기서는 재료만 모은다."""

import json

from workflow.adapters import repo
from workflow.contracts.v1 import BUILTIN_KINDS, BUILTIN_RULES, ExecutionEvent, KindSpec, SelectionRecord
from workflow.server import views

from .conftest import (
    NOW,
    RESULT_COMMIT,
    SESSION,
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
    assert [c["agent_id"] for c in ctx["candidates"]] == ["agent-codex-mac", "agent-ops-demo"]  # 세션 등록 순
    assert ctx["can_run"] is False
    assert ctx["result"] is None
    assert ctx["executions"] == []
    assert ctx["agent_scripted"] is False

    # 후보는 세션이 등록한 Agent 만 — 해제하면 카탈로그에 있어도 후보에서 빠진다
    repo.unregister_session_agent(seeded, SESSION, "agent-ops-demo")
    ctx = views.task_context(seeded, store, repo.get_task(seeded, TASK_A), now=NOW, settings=settings)
    assert [c["agent_id"] for c in ctx["candidates"]] == ["agent-codex-mac"]
    repo.register_session_agent(seeded, SESSION, "agent-ops-demo", NOW)

    _select(seeded, TASK_A, "agent-ops-demo", CAP_A)
    ctx = views.task_context(seeded, store, repo.get_task(seeded, TASK_A), now=NOW, settings=settings)
    assert ctx["needs_selection"] is False
    assert ctx["can_run"] is True
    assert ctx["successors"][0]["task_id"] == TASK_B
    assert ctx["chain"] is None  # 직접 등록 — 워크플로우 칩 없음


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
    assert agent["demo_scripted"] is False
    assert agent["discovered_summary"] == ["operations.diagnose · workflow_id=daily-report"]  # API: 역할·자료 범위
    assert "credential_ref" not in agent
    assert not any("wfc_" in str(v) for v in agent.values())


def test_agent_public_discovered_summary_lists_found_keys_and_profiles_only(seeded, settings):
    """로컬 Agent 의 요약은 `discovered.found` 의 truthy 키(중첩은 `git.head`)와 검증 프로필. 값·URL·비밀 참조는 없다."""
    codex = views.agent_public(repo.get_agent(seeded, "agent-codex-mac"), now=NOW, settings=settings)
    assert codex["demo_scripted"] is False and codex["discovered_summary"] == []

    repo.update_registration(
        seeded, "local-demo-report", connector_id="conn-mac-01", repository_id="demo-report-repo",
        base_commit="3f9c2e1a7b0d4c6e8f1a2b3c4d5e6f7a8b9c0d1e", verification_profile_ids=["vp-pytest", "vp-report"],
        discovered={
            "found": {"AGENTS.md": "# 본문", "codex_config": False, "tests_dir": True,
                      "pyproject": {"name": "demo-report", "pytest_configured": False},
                      "git": {"remotes": [], "head": "0c1ddcf6"}},
            "not_read": ["CLAUDE.md"], "verification_level": "설정 발견",
        },
        now=NOW,
    )
    codex = views.agent_public(repo.get_agent(seeded, "agent-codex-mac"), now=NOW, settings=settings)
    assert codex["discovered_summary"] == [
        "AGENTS.md", "tests_dir", "pyproject.name", "git.head", "검증 프로필 vp-pytest", "검증 프로필 vp-report",
    ]

    # `found` 가 없는 임의 형태(이전 테스트 시드)도 빈 목록으로
    repo.update_registration(
        seeded, "local-demo-report", connector_id="conn-mac-01", repository_id="demo-report-repo",
        base_commit="3f9c2e1a7b0d4c6e8f1a2b3c4d5e6f7a8b9c0d1e", verification_profile_ids=[],
        discovered={"instructions": ["AGENTS.md"], "confirmed": False}, now=NOW,
    )
    assert views.agent_public(repo.get_agent(seeded, "agent-codex-mac"), now=NOW, settings=settings)["discovered_summary"] == []

    scripted = dict(repo.get_agent(seeded, "agent-codex-mac"))
    repo.upsert_agent(seeded, {
        "agent_id": "agent-codex-mac", "name": scripted["name"], "owner_scope": scripted["owner_scope"],
        "connection_type": "local", "local_registration_id": scripted["local_registration_id"],
        "capabilities": [CAP_B], "shared_to_all_sessions": True, "demo_scripted": True,
    })
    assert views.agent_public(repo.get_agent(seeded, "agent-codex-mac"), now=NOW, settings=settings)["demo_scripted"] is True


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


# --- chain_summary — 워크플로우 화면 (phase 5 step 6) ---------------------------------

CHAIN = "chain-abc123"


def _seed_chain(conn, *, skipped=None) -> tuple[str, str]:
    """github #41 → #42 체인. Task 는 conftest 의 task_row 에 chain_id·source_ref 만 얹는다. (task_a, task_b)."""
    repo.insert_chain(conn, {
        "chain_id": CHAIN, "session_id": SESSION, "source": "github",
        "title": "일일 보고서 생성 실패 (09-20 09:00) → 집계 API 응답 형식 변경 대응",
        "skipped": skipped if skipped is not None else [
            {"key": "#43", "title": "변경 응답 형식 모니터링 알림 추가",
             "reason": "맞는 능력 코드 없음 (라벨: enhancement, repo:demo-report-repo)"},
        ],
    }, NOW)
    from .conftest import task_row
    repo.insert_task(conn, {**task_row("task-c41"), "chain_id": CHAIN, "source_ref": "#41",
                            "completion_mode": "auto"}, NOW)
    repo.insert_task(conn, {**task_row("task-c42", kind="code_change", predecessor="task-c41"),
                            "chain_id": CHAIN, "source_ref": "#42"}, NOW)
    return "task-c41", "task-c42"


def _chain(conn, settings, now: str = NOW) -> dict:
    return views.chain_summary(conn, repo.get_chain(conn, CHAIN), now=now, settings=settings)


def test_chain_summary_orders_nodes_and_recomposes_reasons(seeded, settings):
    task_a, task_b = _seed_chain(seeded)
    _select(seeded, task_a, "agent-ops-demo", CAP_A)
    _select(seeded, task_b, "agent-codex-mac", CAP_B)
    summary = _chain(seeded, settings)

    assert summary["chain_id"] == CHAIN
    assert summary["title"] == "일일 보고서 생성 실패 (09-20 09:00) → 집계 API 응답 형식 변경 대응"
    assert (summary["source"], summary["source_label"]) == ("github", "GitHub Issues")
    assert [t["task_id"] for t in summary["tasks"]] == [task_a, task_b]
    assert [t["source_ref"] for t in summary["tasks"]] == ["#41", "#42"]
    assert (summary["done_count"], summary["total"], summary["started"]) == (0, 2, False)
    assert summary["all_selected"] is True and summary["can_start"] is True
    assert summary["skipped"][0]["key"] == "#43"
    assert summary["progress"] == "시작 전"
    assert summary["polling"] is True  # #42 가 대기

    first, second = summary["tasks"]
    assert first["status"].label == "실행 가능" and first["kind"] == "diagnosis"
    assert first["agent"]["name"] == "운영 진단 데모" and first["agent"]["demo_scripted"] is False
    assert "credential_ref" not in first["agent"]
    assert (first["run_mode"], first["completion_mode"]) == ("manual", "auto")
    assert first["selection"].status == "selected"
    # 구성 이유는 가져오기 때와 같은 규칙으로 다시 만들고, 배정 이유는 저장된 선택 기록이 기준
    assert first["reasons"] == [
        "라벨 incident·workflow:daily-report → operations.diagnose",
        "blocked_by 없음 — 가져온 순서대로 배치",
        "체인의 첫 업무 — 선행 없음",
        "직접 실행 — 흐름의 첫 업무는 사람이 시작",
        "자동 완료 — 진단 자동 판정기 있음",
        "operations.diagnose 일치 후보 1개",
    ]
    assert (second["status"].label, second["status"].reason) == ("대기", "선행 대기")
    assert second["reasons"][1:3] == ["blocked_by #41 — #41 뒤에 배치", "선행 #41 (operations.diagnose) → code.modify 인계"]
    assert second["reasons"][-1] == "code.modify 일치 후보 1개"

    gate = summary["human_gate"]
    assert gate["label"] == "검토 승인 (사람) · 병합은 운영자 확인"
    assert (gate["status_label"], gate["reason"], gate["task_id"]) == ("대기", "선행 대기", task_b)


def test_chain_summary_without_selection_cannot_start(seeded, settings):
    task_a, _ = _seed_chain(seeded)
    summary = _chain(seeded, settings)
    assert summary["all_selected"] is False and summary["can_start"] is False
    assert summary["tasks"][0]["agent"] is None
    assert summary["tasks"][0]["status"].label == "확인 필요"
    assert summary["tasks"][0]["reasons"][-1] == "후보 없음"


def test_chain_summary_progress_and_human_gate_follow_last_task(seeded, settings, store):
    task_a, task_b = _seed_chain(seeded)
    _select(seeded, task_a, "agent-ops-demo", CAP_A)
    _select(seeded, task_b, "agent-codex-mac", CAP_B)
    repo.mark_chain_started(seeded, CHAIN, NOW)
    seed_execution(seeded, "exec-a", task_a, kind="diagnosis", inputs=())
    summary = _chain(seeded, settings)
    assert summary["started"] is True and summary["can_start"] is False
    assert summary["progress"] == "2단계 중 1단계 실행 요청됨"

    repo.update_task_status(seeded, task_a, "완료", "판정 근거: 12/12", finished_at=NOW)
    repo.release_execution(seeded, "exec-a", NOW)
    seed_execution(seeded, "exec-b", task_b)
    seed_result_ready(seeded, store, "exec-b", kind="code_change_result", body=code_change_result("exec-b", task_b))
    summary = _chain(seeded, settings)
    assert (summary["done_count"], summary["total"]) == (1, 2)
    assert summary["progress"] == "2단계 중 2단계 확인 필요"
    assert summary["polling"] is False
    gate = summary["human_gate"]
    assert (gate["status_label"], gate["reason"]) == ("확인 필요", "검토 대기")

    repo.update_task_status(seeded, task_b, "완료", "검토 승인 · 병합: 운영자 확인 대기", finished_at=NOW, review_decision="approve")
    summary = _chain(seeded, settings)
    assert summary["done_count"] == 2 and summary["progress"] == "2단계 모두 완료"
    gate = summary["human_gate"]
    assert (gate["status_label"], gate["reason"]) == ("완료", "병합: 운영자 확인 대기")  # ADR-0005: 세션 화면에는 대기만

    repo.confirm_merge(seeded, task_b, NOW)
    gate = _chain(seeded, settings)["human_gate"]
    assert (gate["status_label"], gate["reason"]) == ("완료", "병합 확인됨")


def test_chain_summary_single_auto_node_gate_and_empty_chain(seeded, settings):
    repo.insert_chain(seeded, {"chain_id": CHAIN, "session_id": SESSION, "source": "github",
                               "title": "일일 보고서 생성 실패 (09-20 09:00)"}, NOW)
    from .conftest import task_row
    repo.insert_task(seeded, {**task_row("task-c41"), "chain_id": CHAIN, "source_ref": "#41",
                              "completion_mode": "auto"}, NOW)
    _select(seeded, "task-c41", "agent-ops-demo", CAP_A)
    summary = _chain(seeded, settings)
    assert summary["human_gate"]["label"] == "완료 확인 (사람)"
    assert summary["polling"] is False  # 실행 가능뿐 — 사용자 조작 전에는 갱신할 게 없다

    repo.insert_chain(seeded, {"chain_id": "chain-empty", "session_id": SESSION, "source": "github", "title": "",
                               "skipped": [{"key": "#44", "title": "README 오타 수정", "reason": "맞는 능력 코드 없음 (라벨: docs)"}]}, NOW)
    empty = views.chain_summary(seeded, repo.get_chain(seeded, "chain-empty"), now=NOW, settings=settings)
    assert empty["tasks"] == [] and empty["human_gate"] is None and empty["can_start"] is False
    assert empty["skipped"][0]["key"] == "#44"


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


# --- 업무 종류·후속 규칙 화면 컨텍스트 (phase 6 step 6) -------------------------------

REVIEW_SPEC = KindSpec(
    kind="review", label="검토", capability_code="review", scope_key="repository_id",
    input_kinds=["diff", "code_change_result"], output_kind="generic_result",
    outcomes=["approved", "changes_requested", "needs_information"], instructions="diff 를 읽고 검토하세요.",
    builtin=False,
)


def test_kind_public_labels_chips_and_builtin_flag():
    code_change = views.kind_public(BUILTIN_KINDS[1])
    assert (code_change["kind"], code_change["label"]) == ("code_change", "코드 수정")
    assert (code_change["capability_code"], code_change["scope_key"]) == ("code.modify", "repository_id")
    assert code_change["input_kinds"] == ["diagnosis_result", "evidence"]
    assert code_change["input_labels"] == ["진단 결과", "근거"]
    assert (code_change["output_kind"], code_change["output_label"]) == ("code_change_result", "수정 결과")
    assert code_change["outcomes"] == ["ready_for_review", "needs_information"]
    assert code_change["builtin"] is True and code_change["instructions"] == ""

    review = views.kind_public(REVIEW_SPEC)
    assert review["input_labels"] == ["diff", "수정 결과"]
    assert (review["output_kind"], review["output_label"]) == ("generic_result", "결과 봉투")
    assert review["builtin"] is False and review["instructions"] == "diff 를 읽고 검토하세요."


def test_rule_public_one_line_text_with_labels():
    rule = views.rule_public("rule-1", BUILTIN_RULES[0], BUILTIN_KINDS)
    assert rule["rule_id"] == "rule-1"
    assert (rule["from_kind"], rule["to_kind"]) == ("diagnosis", "code_change")
    assert rule["text"] == "진단 --[ready_for_handoff]--> 코드 수정"
    assert rule["handoff_kinds"] == ["diagnosis_result", "evidence"]
    assert rule["handoff_labels"] == ["진단 결과", "근거"]

    custom = views.rule_public(
        "rule-2",
        BUILTIN_RULES[0].model_copy(update={"on_outcomes": ["ready_for_handoff", "needs_information"]}),
        [*BUILTIN_KINDS, REVIEW_SPEC],
    )
    assert custom["text"] == "진단 --[ready_for_handoff, needs_information]--> 코드 수정"
    # 등록부에 없는 종류는 라벨 대신 코드 그대로
    assert views.rule_public("rule-3", BUILTIN_RULES[0], ())["text"] == "diagnosis --[ready_for_handoff]--> code_change"


# --- 등록부를 보는 화면 컨텍스트 (phase 6 step 7) --------------------------------------

CAP_C = {"code": "review", "scope": {"repository_id": "demo-report-repo"}}
LOCAL_REVIEW = "local-demo-report-claude"
TASK_C = "review-daily-0920"


def _seed_review_task(conn, *, predecessor: str | None = TASK_B) -> None:
    """세션에 종류 review 를 등록하고 검토 Claude(로컬, 능력 review)와 Task C 를 만든다."""
    repo.insert_kind(conn, SESSION, REVIEW_SPEC, NOW)
    repo.upsert_agent(conn, {
        "agent_id": "agent-review-mac", "name": "검토 Claude", "owner_scope": "personal", "connection_type": "local",
        "local_registration_id": LOCAL_REVIEW, "capabilities": [CAP_C], "connection_state": "unknown",
        "shared_to_all_sessions": True,
    })
    repo.register_session_agent(conn, SESSION, "agent-review-mac", NOW)
    from .conftest import task_row
    repo.insert_task(conn, {
        **task_row(TASK_C, kind="review", predecessor=predecessor), "title": "보고서 수정 검토",
        "required_capability": CAP_C, "run_mode": "manual", "criteria": [],
        "target": {"local_registration_id": LOCAL_REVIEW},
    }, NOW)


def _judge(conn, task_id: str, execution_id: str) -> None:
    repo.record_verdict(
        conn, task_id=task_id, execution_id=execution_id,
        verdict={"outcome": "passed", "checks": [{"code": "verification_passed", "passed": True, "detail": "exit 0"}]},
        status="확인 필요", reason="검토 대기", finish=False, now=NOW,
    )


def _store_bundle(conn, store, execution_id: str, source_kind: str) -> str:
    from workflow.contracts.v1 import ArtifactMeta

    from .conftest import meta_for
    data = json.dumps({
        "contract_version": 1, "source_execution_id": execution_id, "source_kind": source_kind,
        "source_result_artifact_id": "art-result-001", "inputs": [], "attachments": [],
    }).encode()
    created, _ = repo.store_artifact(
        conn, store, execution_id=execution_id, session_id=SESSION,
        meta=ArtifactMeta.model_validate(meta_for(data, kind="handoff_bundle", name="manifest.json",
                                                   content_type="application/json")),
        data=data, now=NOW,
    )
    return created.artifact_id


def test_connector_online_follows_selected_local_agent_regardless_of_kind(seeded, settings):
    """연결 상태는 종류 이름이 아니라 선택된 Agent 의 connection_type 으로 본다 — 사용자 정의 종류도 로컬이면 계산한다."""
    _seed_review_task(seeded)
    _select(seeded, TASK_C, "agent-review-mac", CAP_C)
    view = _view(seeded, settings, TASK_C)
    assert view.kind == "review" and view.connector_online is False and view.connector_last_seen == "없음"
    repo.set_agent_connection(seeded, "agent-review-mac", "online", NOW)
    assert _view(seeded, settings, TASK_C).connector_online is True
    # API 에이전트를 고른 업무는 종류와 무관하게 None
    _select(seeded, TASK_A, "agent-ops-demo", CAP_A)
    assert _view(seeded, settings, TASK_A).connector_online is None


def test_predecessor_handoff_needs_judged_result_and_bundle(seeded, settings, store):
    """ADR-0009 (3): 선행 결과가 판정되고 인계 묶음이 있으면 (선행 `완료` 전이라도) 선행 조건이 풀린다."""
    _select(seeded, TASK_A, "agent-ops-demo", CAP_A)
    _select(seeded, TASK_B, "agent-codex-mac", CAP_B)
    repo.set_agent_connection(seeded, "agent-codex-mac", "online", NOW)
    task_b = repo.get_task(seeded, TASK_B)
    assert views.predecessor_handoff(seeded, task_b) == ([], None)
    assert views.predecessor_handoff(seeded, repo.get_task(seeded, TASK_A)) == ([], None)  # 선행 없음
    assert _view(seeded, settings, TASK_B).predecessor_status == "실행 가능"

    seed_execution(seeded, "exec-a", TASK_A, kind="diagnosis", inputs=())
    seed_result_ready(seeded, store, "exec-a", kind="diagnosis_result", body={"outcome": "ready_for_handoff"})
    assert views.predecessor_handoff(seeded, task_b) == ([], None)  # 판정 전
    _judge(seeded, TASK_A, "exec-a")
    assert views.predecessor_handoff(seeded, task_b) == ([], None)  # 판정됐지만 묶음 없음
    view = _view(seeded, settings, TASK_B)
    assert view.predecessor_status == "확인 필요"
    assert views.status_of(task_b, view).reason == "선행 대기"

    bundle_id = _store_bundle(seeded, store, "exec-a", "diagnosis")
    assert views.predecessor_handoff(seeded, task_b) == ([bundle_id], "exec-a")
    view = _view(seeded, settings, TASK_B)
    assert view.predecessor_status is None  # 선행 조건 충족 — 선행 `완료` 를 기다리지 않는다
    status = views.status_of(task_b, view)
    assert (status.label, status.reason) == ("대기", "자동 실행 대기")  # B 는 run_mode auto — 워커가 잇는다
    ctx = views.task_context(seeded, store, task_b, now=NOW, settings=settings)
    assert ctx["can_run"] is True  # 직접 실행도 열려 있다

    # 선행이 검토 거절(실패)로 마감되면 새로 착수하지 않는다
    repo.update_task_status(seeded, TASK_A, "실패", "검토 거절", finished_at=NOW, review_decision="close")
    assert views.predecessor_handoff(seeded, task_b) == ([], None)
    assert _view(seeded, settings, TASK_B).predecessor_status == "실패"


def test_task_context_kind_label_from_registry(seeded, settings, store):
    _seed_review_task(seeded)
    assert views.task_context(seeded, store, repo.get_task(seeded, TASK_A), now=NOW, settings=settings)["kind_label"] == "진단"
    assert views.task_context(seeded, store, repo.get_task(seeded, TASK_B), now=NOW, settings=settings)["kind_label"] == "코드 수정"
    assert views.task_context(seeded, store, repo.get_task(seeded, TASK_C), now=NOW, settings=settings)["kind_label"] == "검토"


def test_generic_result_card_and_viewer_context(seeded, settings, store):
    """generic_result 는 결과 봉투로 파싱된다 — outcome 은 코드 그대로, summary, 산출물 ID. 검증 요약(diff·로그)은 만들지 않는다."""
    _seed_review_task(seeded, predecessor=None)
    _select(seeded, TASK_C, "agent-review-mac", CAP_C)
    from workflow.contracts.v1 import ExecutionRequest
    repo.create_execution(
        seeded, execution_id="exec-review-001", task_id=TASK_C, attempt_no=1, start_key=f"auto:{TASK_C}:r1",
        agent_id="agent-review-mac", kind="review",
        request=ExecutionRequest.model_validate({
            "contract_version": 1, "execution_id": "exec-review-001", "task_id": TASK_C, "kind": "review",
            "agent_id": "agent-review-mac", "task_revision": 1, "request": "검토", "input_artifact_ids": ["art-handoff-002"],
            "target": {"local_registration_id": LOCAL_REVIEW}, "kind_spec": REVIEW_SPEC.model_dump(),
        }),
        assigned_connector_id=None, predecessor_execution_id=None, now=NOW,
    )
    body = {
        "contract_version": 1, "execution_id": "exec-review-001", "task_id": TASK_C, "kind": "review",
        "outcome": "approved", "summary": "diff 는 최소 변경이며 재현 테스트가 무력화되지 않았습니다.",
        "artifact_ids": ["art-claude-jsonl-003"],
    }
    artifact_id = seed_result_ready(seeded, store, "exec-review-001", kind="generic_result", body=body)
    ctx = views.task_context(seeded, store, repo.get_task(seeded, TASK_C), now=NOW, settings=settings)
    assert ctx["result"]["kind"] == "generic_result" and ctx["result"]["artifact_id"] == artifact_id
    assert ctx["result"]["data"]["outcome"] == "approved"
    assert (ctx["status"].label, ctx["status"].reason) == ("확인 필요", "검토 대기")
    assert ctx["can_review"] is True

    viewer = views.viewer_context(seeded, store, ctx["result"], session_id=SESSION)
    assert viewer["kind"] == "generic_result" and viewer["data"]["summary"] == body["summary"]
    assert viewer["verdict"] is None
    assert "diff" not in viewer and "test_before" not in viewer and "excerpts" not in viewer


def test_chain_node_kind_label_and_reasons_use_session_registry(seeded, settings):
    """체인 노드의 종류 라벨과 구성 이유는 세션 등록부로 만든다 — 규칙을 지우면 이유가 바뀐다."""
    task_a, task_b = _seed_chain(seeded)
    _select(seeded, task_a, "agent-ops-demo", CAP_A)
    _select(seeded, task_b, "agent-codex-mac", CAP_B)
    first, second = _chain(seeded, settings)["tasks"]
    assert (first["kind_label"], second["kind_label"]) == ("진단", "코드 수정")
    assert "선행 #41 (operations.diagnose) → code.modify 인계" in second["reasons"]

    (rule_id, _), = repo.list_rules(seeded, SESSION)
    repo.delete_rule(seeded, SESSION, rule_id)
    second = _chain(seeded, settings)["tasks"][1]
    assert second["reasons"][0] == "후속 규칙 없음: diagnosis → code_change"


def test_agent_public_annotates_capabilities_with_kind_labels(seeded, settings):
    ops = views.agent_public(repo.get_agent(seeded, "agent-ops-demo"), now=NOW, settings=settings, kinds=BUILTIN_KINDS)
    assert ops["capabilities"][0]["kind_label"] == "진단"
    unknown = views.agent_public(repo.get_agent(seeded, "agent-ops-demo"), now=NOW, settings=settings, kinds=())
    assert unknown["capabilities"][0]["kind_label"] is None
    default = views.agent_public(repo.get_agent(seeded, "agent-codex-mac"), now=NOW, settings=settings)
    assert default["capabilities"][0] == {"code": "code.modify", "scope": {"repository_id": "demo-report-repo"}, "kind_label": None}


# --- n8n 체인 — callback 상태·items_json 으로 구성 이유 재계산 (phase 7 step 6, ADR-0010) --------------------

CALLBACK_URL = "http://localhost:5678/webhook-waiting/1234"
N8N_ITEMS = [
    {
        "key": "run-daily-0920",
        "title": "일일 보고서 2026-09-20 09:00 실행 실패",
        "body": "daily-report 의 daily-0920-0900 실행이 변환 단계에서 실패했습니다.",
        "labels": ["incident", "workflow:daily-report", "run:daily-0920-0900"],
        "blocked_by": [],
    },
    {
        "key": "fix-format",
        "title": "응답 형식 변경에 맞춰 보고서 변환 수정",
        "body": "진단 결과와 근거를 바탕으로 변환 코드를 수정해 주세요.",
        "labels": ["bug", "repo:demo-report-repo"],
        "blocked_by": ["run-daily-0920"],
    },
]


def _seed_n8n_chain(conn, *, callback_url: str | None = CALLBACK_URL, items=N8N_ITEMS) -> tuple[str, str]:
    """입구 API 가 만든 것과 같은 모양의 n8n 체인 — source_ref 는 항목 key. (task_a, task_b)."""
    repo.insert_chain(conn, {
        "chain_id": CHAIN, "session_id": SESSION, "source": "n8n",
        "title": "일일 보고서 2026-09-20 09:00 실행 실패 → 응답 형식 변경에 맞춰 보고서 변환 수정",
        "callback_url": callback_url, "items": items,
    }, NOW)
    from .conftest import task_row
    repo.insert_task(conn, {**task_row("task-n1"), "chain_id": CHAIN, "source_ref": "run-daily-0920",
                            "completion_mode": "auto"}, NOW)
    repo.insert_task(conn, {**task_row("task-n2", kind="code_change", predecessor="task-n1"),
                            "chain_id": CHAIN, "source_ref": "fix-format"}, NOW)
    return "task-n1", "task-n2"


def test_chain_summary_callback_is_none_without_url(seeded, settings):
    _seed_chain(seeded)
    assert _chain(seeded, settings)["callback"] is None
    repo.insert_chain(seeded, {"chain_id": "chain-n8n-plain", "session_id": SESSION, "source": "n8n",
                               "title": "", "items": []}, NOW)
    plain = views.chain_summary(seeded, repo.get_chain(seeded, "chain-n8n-plain"), now=NOW, settings=settings)
    assert plain["callback"] is None and (plain["source"], plain["source_label"]) == ("n8n", "n8n")


def test_chain_summary_callback_three_states(seeded, settings):
    _seed_n8n_chain(seeded)
    waiting = _chain(seeded, settings)["callback"]
    assert waiting == {
        "url": CALLBACK_URL, "host": "localhost:5678", "state": "대기",
        "sent_at": None, "attempts": 0, "last_error": None,
    }

    # 재시도 중 — 아직 대기, 횟수·마지막 오류가 남는다
    for n in range(4):
        repo.record_callback_attempt(seeded, CHAIN, ok=False, error="HTTP 503", now=NOW, next_at=NOW)
    retrying = _chain(seeded, settings)["callback"]
    assert (retrying["state"], retrying["attempts"], retrying["last_error"]) == ("대기", 4, "HTTP 503")

    # 5회 실패·미전송 → 실패
    repo.record_callback_attempt(seeded, CHAIN, ok=False, error="연결 오류: ConnectError", now=NOW, next_at=None)
    failed = _chain(seeded, settings)["callback"]
    assert (failed["state"], failed["attempts"], failed["last_error"]) == ("실패", 5, "연결 오류: ConnectError")
    assert failed["sent_at"] is None

    # 전송됨 — sent_at 이 있으면 횟수와 무관하게 전송됨, 오류는 지워진다
    repo.record_callback_attempt(seeded, CHAIN, ok=True, error=None, now="2026-09-20T00:05:00Z", next_at=None)
    sent = _chain(seeded, settings)["callback"]
    assert (sent["state"], sent["sent_at"], sent["last_error"]) == ("전송됨", "2026-09-20T00:05:00Z", None)


def test_n8n_chain_recomposes_reasons_from_items_json(seeded, settings):
    """n8n 은 fixture 파일이 없다 — 저장한 항목 원문(items_json)으로 같은 compose 를 돌려 이유를 다시 만든다."""
    task_a, task_b = _seed_n8n_chain(seeded)
    _select(seeded, task_a, "agent-ops-demo", CAP_A)
    _select(seeded, task_b, "agent-codex-mac", CAP_B)
    first, second = _chain(seeded, settings)["tasks"]
    assert first["reasons"] == [
        "라벨 incident·workflow:daily-report → operations.diagnose",
        "blocked_by 없음 — 가져온 순서대로 배치",
        "체인의 첫 업무 — 선행 없음",
        "직접 실행 — 흐름의 첫 업무는 사람이 시작",
        "자동 완료 — 진단 자동 판정기 있음",
        "operations.diagnose 일치 후보 1개",
    ]
    assert second["reasons"][0] == "라벨 bug·repo:demo-report-repo → code.modify"
    assert second["reasons"][1:3] == [
        "blocked_by run-daily-0920 — run-daily-0920 뒤에 배치",
        "선행 run-daily-0920 (operations.diagnose) → code.modify 인계",
    ]
    assert second["reasons"][-1] == "code.modify 일치 후보 1개"


def test_n8n_chain_without_items_json_keeps_only_selection_reason(seeded, settings):
    task_a, _ = _seed_n8n_chain(seeded, items=None)
    _select(seeded, task_a, "agent-ops-demo", CAP_A)
    assert repo.get_chain(seeded, CHAIN)["items_json"] is None
    first, second = _chain(seeded, settings)["tasks"]
    assert first["reasons"] == ["operations.diagnose 일치 후보 1개"]
    assert second["reasons"] == ["후보 없음"]
