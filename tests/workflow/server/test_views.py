"""views.py — DB 행에서 TaskView·템플릿 컨텍스트를 조립한다. 판정은 domain.status 가 하고 여기서는 재료만 모은다."""

import json

from workflow.adapters import repo
from workflow.contracts.v1 import (
    BUILTIN_KINDS,
    BUILTIN_RULES,
    ExecutionEvent,
    ExecutionRequest,
    KindSpec,
    SelectionRecord,
    SuccessorRule,
)
from workflow.domain.kinds import get_kind
from workflow.domain.work_list import parse_list_query
from workflow.domain.work_status import WorkStatus
from workflow.server import views

from .conftest import (
    NOW,
    REPOSITORY,
    RESULT_COMMIT,
    SESSION,
    TASK_A,
    TASK_B,
    code_change_result,
    event,
    seed_execution,
    seed_result_ready,
    task_row,
)

CAP_FIX = {"code": "code.fix", "scope": {"repository_id": REPOSITORY}}  # TASK_A (bug_fix)
CAP_REVIEW = {"code": "code.review", "scope": {"repository_id": REPOSITORY}}  # TASK_B (code_review)


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


# --- 사용자 정의 종류 classify → patch ---------------------------------------------------
# 내장 `bug_fix`·`code_review` 는 업무 순환 종류라 `status_of` 가 저장된 상태를 쓴다. 선택·선행·연결·실행으로
# 상태를 다시 판정하는 경로는 사용자 정의 종류(GENERIC_POLICY)로 본다.

TRIAGE = KindSpec(
    kind="classify", label="분류", capability_code="ops.triage", scope_key="workflow_id",
    input_kinds=[], output_kind="generic_result", outcomes=["ready_for_handoff", "needs_information"],
    instructions="실패 원인을 분류하세요.", builtin=False,
)
PATCH = KindSpec(
    kind="patch", label="패치", capability_code="code.patch", scope_key="repository_id",
    input_kinds=["generic_result"], output_kind="generic_result", outcomes=["done", "needs_information"],
    instructions="인계된 분류 결과로 고치세요.", builtin=False,
)
TRIAGE_RULE = SuccessorRule(
    from_kind="classify", on_outcomes=["ready_for_handoff"], to_kind="patch", handoff_kinds=["generic_result"],
)
CAP_TRIAGE = {"code": "ops.triage", "scope": {"workflow_id": "daily-report"}}
CAP_PATCH = {"code": "code.patch", "scope": {"repository_id": REPOSITORY}}
API_AGENT = "agent-api-ops"  # API 연결 — heartbeat 를 보내지 않는다
TRIAGE_AGENT = "agent-triage-mac"
PATCH_AGENT = "agent-patch-mac"
TASK_T = "triage-daily-0920"  # triage
TASK_P = "patch-daily-0920"  # patch ← TASK_T
# kind → (종류, 능력, 제목, 로컬 등록 ID)
USER_KINDS = {
    "classify": (TRIAGE, CAP_TRIAGE, "일일 보고서 실패 분류", "local-triage"),
    "patch": (PATCH, CAP_PATCH, "보고서 변환 패치", "local-patch"),
}


def seed_user_kinds(conn) -> None:
    """세션에 종류 classify·patch 와 규칙 classify → patch 를 등록하고 분류 API(API)·분류 Codex·패치 Codex(로컬)를
    워크스페이스에 붙인다. 워크스페이스 행이 먼저 있어야 한다."""
    repo.insert_kind(conn, SESSION, TRIAGE, NOW)
    repo.insert_kind(conn, SESSION, PATCH, NOW)
    repo.insert_rule(conn, SESSION, TRIAGE_RULE, NOW)
    for agent in (
        {"agent_id": API_AGENT, "name": "운영 분류 API", "owner_scope": "company", "connection_type": "api",
         "api_url": "http://127.0.0.1:8100", "credential_ref": "env:OPS_API_TOKEN", "capabilities": [CAP_TRIAGE],
         "connection_state": "online"},
        {"agent_id": TRIAGE_AGENT, "name": "분류 Codex", "owner_scope": "personal", "connection_type": "local",
         "local_registration_id": "local-triage", "capabilities": [CAP_TRIAGE], "connection_state": "unknown"},
        {"agent_id": PATCH_AGENT, "name": "패치 Codex", "owner_scope": "personal", "connection_type": "local",
         "local_registration_id": "local-patch", "capabilities": [CAP_PATCH], "connection_state": "unknown"},
    ):
        repo.upsert_agent(conn, agent)
        repo.register_session_agent(conn, SESSION, agent["agent_id"], NOW)


def user_task(task_id: str, kind: str, predecessor: str | None = None) -> dict:
    """사용자 정의 종류 Task 행 (conftest `task_row` 와 같은 모양, target 은 로컬 등록 하나)."""
    _, capability, title, registration = USER_KINDS[kind]
    return {
        "task_id": task_id,
        "session_id": SESSION,
        "title": title,
        "request": "실패 원인을 분류해 주세요." if kind == "classify" else "보고서 변환을 고쳐 주세요.",
        "kind": kind,
        "required_capability": capability,
        "selection_mode": "auto",
        "chosen_agent_id": None,
        "run_mode": "manual" if predecessor is None else "auto",
        "completion_mode": "review",
        "criteria": [{"code": "outcome_allowed", "text": "결과 봉투 outcome 이 종류의 outcome 목록에 있음",
                      "structured": True}],
        "predecessor_task_id": predecessor,
        "revision": 1,
        "target": {"local_registration_id": registration},
        "status": "실행 가능",
        "status_reason": "선택됨",
    }


def seed_user_tasks(conn) -> None:
    """종류·Agent(`seed_user_kinds`)와 Task T(classify) → P(patch)."""
    seed_user_kinds(conn)
    repo.insert_work_item_task(conn, user_task(TASK_T, "classify"), NOW)
    repo.insert_work_item_task(conn, user_task(TASK_P, "patch", predecessor=TASK_T), NOW)


def seed_user_execution(conn, execution_id: str, task_id: str, kind: str) -> None:
    """사용자 정의 종류의 queued 실행 (연결 프로그램 배정 없음)."""
    spec, _, _, registration = USER_KINDS[kind]
    agent_id = TRIAGE_AGENT if kind == "classify" else PATCH_AGENT
    repo.create_execution(
        conn, execution_id=execution_id, task_id=task_id, attempt_no=1, start_key=f"auto:{task_id}:r1",
        agent_id=agent_id, kind=kind,
        request=ExecutionRequest.model_validate({
            "contract_version": 1, "execution_id": execution_id, "task_id": task_id, "kind": kind,
            "agent_id": agent_id, "task_revision": 1, "request": "진행", "input_artifact_ids": [],
            "target": {"local_registration_id": registration}, "kind_spec": spec.model_dump(),
        }),
        assigned_connector_id=None, predecessor_execution_id=None, now=NOW,
    )


def user_result(execution_id: str, task_id: str, kind: str, outcome: str) -> dict:
    return {
        "contract_version": 1, "execution_id": execution_id, "task_id": task_id, "kind": kind,
        "outcome": outcome, "summary": f"{kind} 결과", "artifact_ids": [],
    }


# --- build_task_view ----------------------------------------------------------


def test_view_without_selection_record_is_needs_selection(seeded, settings):
    view = _view(seeded, settings, TASK_A)
    assert view.selection_status == "needs_selection"
    assert view.selection_reason == "후보 없음"
    assert view.selected_agent_id is None
    assert view.execution_status is None
    assert view.connector_online is None  # 선택된 Agent 가 없으면 연결 상태를 보지 않는다
    assert view.finished is False


def test_view_user_kind_selected_and_runnable(seeded, settings):
    seed_user_tasks(seeded)
    _select(seeded, TASK_T, API_AGENT, CAP_TRIAGE)
    view = _view(seeded, settings, TASK_T)
    assert (view.kind, view.run_mode, view.completion_mode) == ("classify", "manual", "review")
    assert view.selection_status == "selected"
    assert view.selected_agent_id == API_AGENT
    assert view.predecessor_status is None
    assert views.status_of(repo.get_task(seeded, TASK_T), view).label == "실행 가능"


def test_view_user_kind_reads_predecessor_status_and_connector_heartbeat(seeded, settings):
    seed_user_tasks(seeded)
    _select(seeded, TASK_P, PATCH_AGENT, CAP_PATCH)
    view = _view(seeded, settings, TASK_P)
    assert view.predecessor_status == "실행 가능"  # T 는 아직 완료가 아님
    assert view.connector_online is False  # last_seen_at 없음
    assert view.connector_last_seen == "없음"

    repo.update_task_status(seeded, TASK_T, "완료", "검토 승인", finished_at=NOW, review_decision="approve", now=NOW)
    repo.set_agent_connection(seeded, PATCH_AGENT, "online", "2026-09-20T00:00:00Z")
    fresh = _view(seeded, settings, TASK_P, now="2026-09-20T00:01:00Z")  # 60초 뒤: 90초 이내
    assert fresh.predecessor_status == "완료"
    assert fresh.connector_online is True
    assert fresh.connector_last_seen == "2026-09-20 09:00:00 KST"

    stale = _view(seeded, settings, TASK_P, now="2026-09-20T00:02:00Z")  # 120초 뒤: 90초 초과
    assert stale.connector_online is False
    # phase 17 — 꺼진 러너는 소유자 문구(이 러너는 소유자 없음 = 공용)
    assert views.status_of(repo.get_task(seeded, TASK_P), stale).reason == "공용 러너 꺼짐 · 켜지면 시작"


def test_status_of_builtin_cycle_kind_uses_stored_status(seeded, settings):
    """`bug_fix`·`code_review` 는 업무 순환 종류 — 선택·연결로 다시 판정하지 않고 워커가 저장한 상태를 쓴다."""
    _select(seeded, TASK_B, "agent-codex-mac", CAP_REVIEW)
    view = _view(seeded, settings, TASK_B)
    assert view.predecessor_status == "실행 가능" and view.connector_online is False
    status = views.status_of(repo.get_task(seeded, TASK_B), view)
    assert (status.label, status.reason) == ("실행 가능", "agent-codex-mac 선택됨")  # task_row 의 저장값


def test_view_reads_active_execution_progress_and_failure(seeded, settings):
    _select(seeded, TASK_A, "agent-codex-mac", CAP_FIX)
    seed_execution(seeded, "exec-1", TASK_A)
    for body in (
        event("exec-1", 1, "accepted", {}),
        event("exec-1", 2, "started", {"runtime_ref": "pid:1"}),
        event("exec-1", 3, "progress", {"message": "재현 테스트 실행"}),
    ):
        repo.append_event(seeded, "exec-1", ExecutionEvent.model_validate(body), actor="connector:conn-1", now=NOW)
    view = _view(seeded, settings, TASK_A)
    assert view.execution_status == "running"
    assert view.last_progress == "재현 테스트 실행"
    assert view.verdict is None

    repo.append_event(
        seeded, "exec-1",
        ExecutionEvent.model_validate(event("exec-1", 4, "failed", {"code": "timeout", "message": "5분 초과", "process_stopped": True})),
        actor="connector:conn-1", now=NOW,
    )
    view = _view(seeded, settings, TASK_A)
    assert (view.execution_status, view.failed_code, view.failed_message, view.process_stopped) == (
        "failed", "timeout", "5분 초과", True)


def test_view_summarises_verdict_from_task_verdicts(seeded, settings, store):
    _select(seeded, TASK_A, "agent-codex-mac", CAP_FIX)
    seed_execution(seeded, "exec-1", TASK_A)
    seed_result_ready(seeded, store, "exec-1", kind="code_change_result", body=code_change_result("exec-1", TASK_A))
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
    repo.update_task_status(seeded, TASK_A, "완료", "판정 근거: 12/12", finished_at=NOW, now=NOW)
    row = repo.get_task(seeded, TASK_A)
    status = views.status_of(row, _view(seeded, settings, TASK_A))
    assert (status.label, status.reason) == ("완료", "판정 근거: 12/12")


# --- task_context ----------------------------------------------------------------


def test_task_context_collects_executions_result_and_actions(seeded, settings, store):
    seed_user_tasks(seeded)
    _select(seeded, TASK_P, PATCH_AGENT, CAP_PATCH)
    seed_user_execution(seeded, "exec-patch-001", TASK_P, "patch")
    artifact_id = seed_result_ready(
        seeded, store, "exec-patch-001", kind="generic_result",
        body=user_result("exec-patch-001", TASK_P, "patch", "done"),
    )
    ctx = views.task_context(seeded, store, repo.get_task(seeded, TASK_P), now=NOW, settings=settings)

    assert ctx["task"]["task_id"] == TASK_P
    assert ctx["task"]["required_capability"] == CAP_PATCH
    assert ctx["task"]["criteria"][0]["text"] == "결과 봉투 outcome 이 종류의 outcome 목록에 있음"
    assert ctx["selection"].selected_agent_id == PATCH_AGENT
    assert ctx["agent"]["agent_id"] == PATCH_AGENT
    assert "credential_ref" not in ctx["agent"]
    assert (ctx["status"].label, ctx["status"].reason) == ("확인 필요", "검토 대기")
    assert ctx["predecessor"]["task_id"] == TASK_T
    assert ctx["successors"] == []

    [execution] = ctx["executions"]
    assert execution["attempt_no"] == 1 and execution["status"] == "result_ready"
    assert [e["type"] for e in execution["events"]] == ["accepted", "started", "result_ready"]
    assert execution["events"][2]["data"] == {"result_artifact_id": artifact_id}
    assert [a["artifact_id"] for a in execution["artifacts"]] == [artifact_id]

    assert ctx["result"]["kind"] == "generic_result"
    assert ctx["result"]["artifact_id"] == artifact_id
    assert ctx["result"]["data"]["outcome"] == "done"
    assert ctx["can_review"] is True
    assert ctx["can_run"] is False
    assert ctx["needs_selection"] is False


def test_task_context_flags_run_and_selection(seeded, settings, store):
    seed_user_tasks(seeded)
    ctx = views.task_context(seeded, store, repo.get_task(seeded, TASK_T), now=NOW, settings=settings)
    assert ctx["needs_selection"] is True
    assert [c["agent_id"] for c in ctx["candidates"]] == [  # 세션 등록 순
        "agent-codex-mac", API_AGENT, TRIAGE_AGENT, PATCH_AGENT,
    ]
    assert ctx["can_run"] is False
    assert ctx["result"] is None
    assert ctx["executions"] == []

    # 후보는 세션이 등록한 Agent 만 — 해제하면 카탈로그에 있어도 후보에서 빠진다
    repo.unregister_session_agent(seeded, SESSION, API_AGENT)
    ctx = views.task_context(seeded, store, repo.get_task(seeded, TASK_T), now=NOW, settings=settings)
    assert [c["agent_id"] for c in ctx["candidates"]] == ["agent-codex-mac", TRIAGE_AGENT, PATCH_AGENT]
    repo.register_session_agent(seeded, SESSION, API_AGENT, NOW)

    _select(seeded, TASK_T, API_AGENT, CAP_TRIAGE)
    ctx = views.task_context(seeded, store, repo.get_task(seeded, TASK_T), now=NOW, settings=settings)
    assert ctx["needs_selection"] is False
    assert ctx["can_run"] is True
    assert ctx["successors"][0]["task_id"] == TASK_P
    assert ctx["chain"] is None  # 직접 등록 — 워크플로우 칩 없음


# --- 보조 ------------------------------------------------------------------------


def test_task_summary_and_agent_public(seeded, settings):
    seed_user_tasks(seeded)
    _select(seeded, TASK_T, API_AGENT, CAP_TRIAGE)
    summary = views.task_summary(seeded, repo.get_task(seeded, TASK_T), now=NOW, settings=settings)
    assert summary["task_id"] == TASK_T
    assert summary["title"] == "일일 보고서 실패 분류"
    assert summary["status"].label == "실행 가능"
    assert summary["created_at"] == NOW

    agent = views.agent_public(repo.get_agent(seeded, API_AGENT), now=NOW, settings=settings)
    assert agent["agent_id"] == API_AGENT
    assert agent["capabilities"][0]["code"] == "ops.triage"
    assert agent["discovered"] == {}
    assert agent["online"] is True  # API 에이전트는 heartbeat 가 없으므로 connection_state 만 본다
    assert agent["discovered_summary"] == ["ops.triage · workflow_id=daily-report"]  # API: 역할·자료 범위
    assert "credential_ref" not in agent
    assert not any("wfc_" in str(v) for v in agent.values())


def test_agent_public_discovered_summary_lists_found_keys_and_profiles_only(seeded, settings):
    """로컬 Agent 의 요약은 `discovered.found` 의 truthy 키(중첩은 `git.head`)와 검증 프로필. 값·URL·비밀 참조는 없다."""
    codex = views.agent_public(repo.get_agent(seeded, "agent-codex-mac"), now=NOW, settings=settings)
    assert codex["discovered_summary"] == []

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


def test_agent_online_rule_by_connection_type(seeded, settings):
    """로컬은 online + heartbeat 이내, API 는 connection_state 만 (heartbeat 를 보내지 않는다)."""
    seed_user_kinds(seeded)
    ops = repo.get_agent(seeded, API_AGENT)
    assert ops["connection_type"] == "api" and ops["last_seen_at"] is None
    assert views.agent_online(ops, now=NOW, settings=settings) is True
    repo.set_agent_connection(seeded, API_AGENT, "offline", None)
    assert views.agent_online(repo.get_agent(seeded, API_AGENT), now=NOW, settings=settings) is False

    codex = repo.get_agent(seeded, "agent-codex-mac")
    assert codex["connection_type"] == "local"
    repo.set_agent_connection(seeded, "agent-codex-mac", "online", None)
    assert views.agent_online(repo.get_agent(seeded, "agent-codex-mac"), now=NOW, settings=settings) is False
    repo.set_agent_connection(seeded, "agent-codex-mac", "online", NOW)
    assert views.agent_online(repo.get_agent(seeded, "agent-codex-mac"), now=NOW, settings=settings) is True


# --- chain_summary — 워크플로우 화면 (phase 5 step 6) ---------------------------------

CHAIN = "chain-abc123"


def _seed_chain(conn) -> tuple[str, str]:
    """github #41(bug_fix) → #42(code_review) 체인. Task 는 conftest 의 task_row 에 chain_id·source_ref 만 얹는다."""
    repo.insert_chain(conn, {"chain_id": CHAIN, "session_id": SESSION, "source": "github",
                             "title": "보고서 변환 수정 → 보고서 변환 수정 검토"}, NOW)
    from .conftest import task_row
    repo.insert_work_item_task(conn, {**task_row("task-c41"), "chain_id": CHAIN, "source_ref": "#41"}, NOW)
    repo.insert_work_item_task(conn, {**task_row("task-c42", kind="code_review", predecessor="task-c41"),
                            "chain_id": CHAIN, "source_ref": "#42"}, NOW)
    return "task-c41", "task-c42"


CHAIN_ITEMS = [
    {"key": "#41", "title": "일일 보고서 생성 실패 (09-20 09:00)", "body": "실패 원인을 분류해 주세요.",
     "labels": ["kind:classify", "workflow_id:daily-report"], "blocked_by": []},
    {"key": "#42", "title": "집계 API 응답 형식 변경 대응", "body": "보고서 변환을 고쳐 주세요.",
     "labels": ["kind:patch", f"repository_id:{REPOSITORY}"], "blocked_by": ["#41"]},
    {"key": "#43", "title": "변경 응답 형식 모니터링 알림 추가", "body": "알림을 추가해 주세요.",
     "labels": ["enhancement", f"repository_id:{REPOSITORY}"], "blocked_by": ["#42"]},
]


def _seed_user_chain(conn, *, skipped=None) -> tuple[str, str]:
    """n8n #41 → #42 체인(classify → patch). 접수 항목 원문(`items`)을 함께 저장해 구성 이유를 다시 만든다.
    Task 는 `user_task` 에 chain_id·source_ref 만 얹는다."""
    seed_user_kinds(conn)
    repo.insert_chain(conn, {
        "chain_id": CHAIN, "session_id": SESSION, "source": "n8n", "items": CHAIN_ITEMS,
        "title": "일일 보고서 생성 실패 (09-20 09:00) → 집계 API 응답 형식 변경 대응",
        "skipped": skipped if skipped is not None else [
            {"key": "#43", "title": "변경 응답 형식 모니터링 알림 추가",
             "reason": f"맞는 능력 코드 없음 (라벨: enhancement, repository_id:{REPOSITORY})"},
        ],
    }, NOW)
    repo.insert_work_item_task(conn, {**user_task("task-c41", "classify"), "chain_id": CHAIN, "source_ref": "#41"}, NOW)
    repo.insert_work_item_task(conn, {**user_task("task-c42", "patch", predecessor="task-c41"),
                            "chain_id": CHAIN, "source_ref": "#42"}, NOW)
    return "task-c41", "task-c42"


def _chain(conn, settings, now: str = NOW) -> dict:
    return views.chain_summary(conn, repo.get_chain(conn, CHAIN), now=now, settings=settings)


def test_chain_summary_orders_nodes_and_recomposes_reasons(seeded, settings):
    task_a, task_b = _seed_user_chain(seeded)
    _select(seeded, task_a, API_AGENT, CAP_TRIAGE)
    _select(seeded, task_b, PATCH_AGENT, CAP_PATCH)
    summary = _chain(seeded, settings)

    assert summary["chain_id"] == CHAIN
    assert summary["title"] == "일일 보고서 생성 실패 (09-20 09:00) → 집계 API 응답 형식 변경 대응"
    assert (summary["source"], summary["source_label"]) == ("n8n", "n8n")
    assert [t["task_id"] for t in summary["tasks"]] == [task_a, task_b]
    assert [t["source_ref"] for t in summary["tasks"]] == ["#41", "#42"]
    assert (summary["done_count"], summary["total"], summary["started"]) == (0, 2, False)
    assert summary["all_selected"] is True and summary["can_start"] is True
    assert summary["skipped"][0]["key"] == "#43"
    assert summary["progress"] == "시작 전"
    assert summary["polling"] is True  # #42 가 대기

    first, second = summary["tasks"]
    assert first["status"].label == "실행 가능" and first["kind"] == "classify"
    assert first["agent"]["name"] == "운영 분류 API"
    assert "credential_ref" not in first["agent"]
    assert (first["run_mode"], first["completion_mode"]) == ("manual", "review")
    assert first["selection"].status == "selected"
    # 구성 이유는 가져오기 때와 같은 규칙으로 다시 만들고, 배정 이유는 저장된 선택 기록이 기준
    assert first["reasons"] == [
        "라벨 kind:classify + workflow_id:daily-report → classify",
        "blocked_by 없음 — 가져온 순서대로 배치",
        "체인의 첫 업무 — 선행 없음",
        "직접 실행 — 흐름의 첫 업무는 사람이 시작",
        "검토 후 완료 — 기본값",
        "ops.triage 일치 후보 1개",
    ]
    assert (second["status"].label, second["status"].reason) == ("대기", "선행 대기")
    assert second["reasons"][1:3] == ["blocked_by #41 — #41 뒤에 배치", "선행 #41 (ops.triage) → code.patch 인계"]
    assert second["reasons"][-1] == "code.patch 일치 후보 1개"

    gate = summary["human_gate"]
    assert gate["label"] == "검토 승인 (사람) · 병합은 운영자 확인"
    assert (gate["status_label"], gate["reason"], gate["task_id"]) == ("대기", "선행 대기", task_b)


def test_chain_summary_without_selection_cannot_start(seeded, settings):
    task_a, _ = _seed_user_chain(seeded)
    summary = _chain(seeded, settings)
    assert summary["all_selected"] is False and summary["can_start"] is False
    assert summary["tasks"][0]["agent"] is None
    assert summary["tasks"][0]["status"].label == "확인 필요"
    assert summary["tasks"][0]["reasons"][-1] == "후보 없음"


def test_chain_summary_progress_and_human_gate_follow_last_task(seeded, settings, store):
    task_a, task_b = _seed_user_chain(seeded)
    _select(seeded, task_a, API_AGENT, CAP_TRIAGE)
    _select(seeded, task_b, PATCH_AGENT, CAP_PATCH)
    repo.mark_chain_started(seeded, CHAIN, NOW)
    seed_user_execution(seeded, "exec-a", task_a, "classify")
    summary = _chain(seeded, settings)
    assert summary["started"] is True and summary["can_start"] is False
    assert summary["progress"] == "2단계 중 1단계 실행 요청됨"

    repo.update_task_status(seeded, task_a, "완료", "검토 승인", finished_at=NOW, review_decision="approve", now=NOW)
    repo.release_execution(seeded, "exec-a", NOW)
    seed_user_execution(seeded, "exec-b", task_b, "patch")
    seed_result_ready(seeded, store, "exec-b", kind="generic_result", body=user_result("exec-b", task_b, "patch", "done"))
    summary = _chain(seeded, settings)
    assert (summary["done_count"], summary["total"]) == (1, 2)
    assert summary["progress"] == "2단계 중 2단계 확인 필요"
    assert summary["polling"] is False
    gate = summary["human_gate"]
    assert (gate["status_label"], gate["reason"]) == ("확인 필요", "검토 대기")

    repo.update_task_status(seeded, task_b, "완료", "검토 승인", finished_at=NOW, review_decision="approve", now=NOW)
    summary = _chain(seeded, settings)
    assert summary["done_count"] == 2 and summary["progress"] == "2단계 모두 완료"
    gate = summary["human_gate"]
    assert (gate["status_label"], gate["reason"]) == ("완료", "검토 승인")  # 완료 사유는 저장된 상태 사유 그대로


def test_chain_summary_single_auto_node_gate_and_empty_chain(seeded, settings):
    seed_user_kinds(seeded)
    repo.insert_chain(seeded, {"chain_id": CHAIN, "session_id": SESSION, "source": "github",
                               "title": "일일 보고서 생성 실패 (09-20 09:00)"}, NOW)
    repo.insert_work_item_task(seeded, {**user_task("task-c41", "classify"), "chain_id": CHAIN, "source_ref": "#41",
                              "completion_mode": "auto"}, NOW)
    _select(seeded, "task-c41", API_AGENT, CAP_TRIAGE)
    summary = _chain(seeded, settings)
    assert summary["human_gate"]["label"] == "완료 확인 (사람)"
    assert summary["polling"] is False  # 실행 가능뿐 — 사용자 조작 전에는 갱신할 게 없다

    # 검토 거절로 마감되면 사람 단계도 실패
    repo.update_task_status(seeded, "task-c41", "실패", "검토 거절", finished_at=NOW, review_decision="close", now=NOW)
    gate = _chain(seeded, settings)["human_gate"]
    assert (gate["status_label"], gate["reason"]) == ("실패", "검토 거절")

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


def test_viewer_context_for_code_change_reads_logs_and_diff(seeded, settings, store):
    from workflow.contracts.v1 import ArtifactMeta

    from .conftest import meta_for

    _select(seeded, TASK_A, "agent-codex-mac", CAP_FIX)
    seed_execution(seeded, "exec-fix-001", TASK_A)
    for kind, text in (("diff", DIFF_TEXT), ("test_log_before", "\n".join(str(n) for n in range(30))),
                       ("test_log_after", "ok\n")):
        data = text.encode()
        repo.store_artifact(
            seeded, store, execution_id="exec-fix-001", session_id=SESSION,
            meta=ArtifactMeta.model_validate(meta_for(data, kind=kind, name=kind)), data=data, now=NOW,
        )
    artifact_id = seed_result_ready(seeded, store, "exec-fix-001", kind="code_change_result",
                                    body=code_change_result("exec-fix-001", TASK_A))
    ctx = views.task_context(seeded, store, repo.get_task(seeded, TASK_A), now=NOW, settings=settings)
    viewer = views.viewer_context(seeded, store, ctx["result"], session_id=SESSION)
    assert viewer["kind"] == "code_change_result"
    assert viewer["artifact_id"] == artifact_id
    assert viewer["data"]["result_commit"] == RESULT_COMMIT
    assert viewer["raw_text"].startswith("{")
    assert viewer["diff"]["stats"] == (2, 3, 1)
    assert viewer["diff"]["lines"][5] == ("del", "-old")
    assert viewer["test_before"]["lines"] == [str(n) for n in range(10, 30)]
    assert viewer["test_after"]["lines"] == ["ok"]
    assert "report" not in viewer and "compare" not in viewer and "excerpts" not in viewer  # 진단·보고서 데모 뷰어 없음
    assert viewer["verdict"] is None
    assert (viewer["verdict_passed"], viewer["verdict_total"]) == (0, 0)

    seeded.execute(
        "INSERT INTO task_verdicts (task_id, execution_id, verdict_json, decided_at) VALUES (?, ?, ?, ?)",
        (TASK_A, "exec-fix-001", json.dumps({"outcome": "failed", "checks": [
            {"code": "verification_passed", "passed": True, "detail": "exit 0"},
            {"code": "base_commit_matches", "passed": False, "detail": "다른 기준 커밋"},
        ]}), NOW),
    )
    viewer = views.viewer_context(seeded, store, ctx["result"], session_id=SESSION)
    assert viewer["verdict"]["outcome"] == "failed"
    assert (viewer["verdict_passed"], viewer["verdict_total"]) == (1, 2)

    empty = views.viewer_context(seeded, store, {"kind": "code_change_result", "artifact_id": "x",
                                                  "execution_id": "exec-none", "data": None}, session_id=SESSION)
    assert empty["diff"] is None and empty["test_before"] is None and empty["test_after"] is None
    assert views.viewer_context(seeded, store, None, session_id=SESSION) is None


def test_execution_context_has_duration_and_progress_count(seeded, settings, store):
    _select(seeded, TASK_A, "agent-codex-mac", CAP_FIX)
    seed_execution(seeded, "exec-1", TASK_A)
    for body in (
        event("exec-1", 1, "accepted", {}, occurred_at="2026-09-20T09:00:00+09:00"),
        event("exec-1", 2, "started", {"runtime_ref": "r"}, occurred_at="2026-09-20T09:00:05+09:00"),
        event("exec-1", 3, "progress", {"message": "하나"}, occurred_at="2026-09-20T09:01:00+09:00"),
        event("exec-1", 4, "progress", {"message": "둘"}, occurred_at="2026-09-20T09:02:00+09:00"),
    ):
        repo.append_event(seeded, "exec-1", ExecutionEvent.model_validate(body), actor="connector:conn-1", now=NOW)
    ctx = views.task_context(seeded, store, repo.get_task(seeded, TASK_A), now="2026-09-20T00:04:18Z", settings=settings)
    [execution] = ctx["executions"]
    assert execution["duration_seconds"] == 253  # started 09:00:05 KST → now 09:04:18 KST
    assert execution["progress_count"] == 2
    assert execution["last_progress"] == "둘"

    repo.append_event(
        seeded, "exec-1",
        ExecutionEvent.model_validate(event("exec-1", 5, "failed", {"code": "timeout", "message": "x", "process_stopped": True},
                                            occurred_at="2026-09-20T09:03:00+09:00")),
        actor="connector:conn-1", now=NOW,
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
    code_review = views.kind_public(get_kind(BUILTIN_KINDS, "code_review"))
    assert (code_review["kind"], code_review["label"]) == ("code_review", "커밋 검토")
    assert (code_review["capability_code"], code_review["scope_key"]) == ("code.review", "repository_id")
    assert code_review["input_kinds"] == ["code_change_result"]
    assert code_review["input_labels"] == ["수정 결과"]
    assert (code_review["output_kind"], code_review["output_label"]) == ("code_review_result", "code_review_result")  # 칩 라벨 없음 → 코드
    assert code_review["outcomes"] == ["approved", "changes_requested", "needs_information"]
    assert code_review["builtin"] is True and code_review["instructions"] == ""

    review = views.kind_public(REVIEW_SPEC)
    assert review["input_labels"] == ["diff", "수정 결과"]
    assert (review["output_kind"], review["output_label"]) == ("generic_result", "결과 봉투")
    assert review["builtin"] is False and review["instructions"] == "diff 를 읽고 검토하세요."


def test_rule_public_one_line_text_with_labels():
    builtin = next(r for r in BUILTIN_RULES if r.from_kind == "bug_fix")
    rule = views.rule_public("rule-1", builtin, BUILTIN_KINDS)
    assert rule["rule_id"] == "rule-1"
    assert (rule["from_kind"], rule["to_kind"]) == ("bug_fix", "code_review")
    assert rule["text"] == "버그 수정 --[ready_for_review]--> 커밋 검토"
    assert rule["handoff_kinds"] == ["code_change_result", "diff", "test_log_after", "verification_log"]
    assert rule["handoff_labels"] == ["수정 결과", "diff", "테스트 후", "검증 로그"]
    assert (rule["placement"], rule["placement_label"]) == ("same_work", "같은 업무의 다음 단계")
    spawned = views.rule_public("rule-4", builtin.model_copy(update={"placement": "new_work"}), BUILTIN_KINDS)
    assert (spawned["placement"], spawned["placement_label"]) == ("new_work", "새 업무로 등록")

    custom = views.rule_public(
        "rule-2",
        builtin.model_copy(update={"on_outcomes": ["ready_for_review", "needs_information"]}),
        [*BUILTIN_KINDS, REVIEW_SPEC],
    )
    assert custom["text"] == "버그 수정 --[ready_for_review, needs_information]--> 커밋 검토"
    # 등록부에 없는 종류는 라벨 대신 코드 그대로
    assert views.rule_public("rule-3", builtin, ())["text"] == "bug_fix --[ready_for_review]--> code_review"


# --- 등록부를 보는 화면 컨텍스트 (phase 6 step 7) --------------------------------------

CAP_C = {"code": "review", "scope": {"repository_id": "demo-report-repo"}}
LOCAL_REVIEW = "local-demo-report-claude"
TASK_C = "custom-review-0920"  # 사용자 정의 종류 review


def _seed_review_task(conn, *, predecessor: str | None = TASK_B) -> None:
    """세션에 종류 review 를 등록하고 검토 Claude(로컬, 능력 review)와 Task C 를 만든다."""
    repo.insert_kind(conn, SESSION, REVIEW_SPEC, NOW)
    repo.upsert_agent(conn, {
        "agent_id": "agent-review-mac", "name": "검토 Claude", "owner_scope": "personal", "connection_type": "local",
        "local_registration_id": LOCAL_REVIEW, "capabilities": [CAP_C], "connection_state": "unknown",
    })
    repo.register_session_agent(conn, SESSION, "agent-review-mac", NOW)
    from .conftest import task_row
    repo.insert_work_item_task(conn, {
        **task_row(TASK_C, predecessor=predecessor), "kind": "review", "title": "보고서 수정 검토",
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
    seed_user_tasks(seeded)
    _select(seeded, TASK_T, API_AGENT, CAP_TRIAGE)
    assert _view(seeded, settings, TASK_T).connector_online is None


def test_predecessor_handoff_needs_judged_result_and_bundle(seeded, settings, store):
    """ADR-0009 (3): 선행 결과가 판정되고 인계 묶음이 있으면 (선행 `완료` 전이라도) 선행 조건이 풀린다."""
    seed_user_tasks(seeded)
    _select(seeded, TASK_T, API_AGENT, CAP_TRIAGE)
    _select(seeded, TASK_P, PATCH_AGENT, CAP_PATCH)
    repo.set_agent_connection(seeded, PATCH_AGENT, "online", NOW)
    task_p = repo.get_task(seeded, TASK_P)
    assert views.predecessor_handoff(seeded, task_p) == ([], None)
    assert views.predecessor_handoff(seeded, repo.get_task(seeded, TASK_T)) == ([], None)  # 선행 없음
    assert _view(seeded, settings, TASK_P).predecessor_status == "실행 가능"

    seed_user_execution(seeded, "exec-a", TASK_T, "classify")
    seed_result_ready(seeded, store, "exec-a", kind="generic_result",
                      body=user_result("exec-a", TASK_T, "classify", "ready_for_handoff"))
    assert views.predecessor_handoff(seeded, task_p) == ([], None)  # 판정 전
    _judge(seeded, TASK_T, "exec-a")
    assert views.predecessor_handoff(seeded, task_p) == ([], None)  # 판정됐지만 묶음 없음
    view = _view(seeded, settings, TASK_P)
    assert view.predecessor_status == "확인 필요"
    assert views.status_of(task_p, view).reason == "선행 대기"

    bundle_id = _store_bundle(seeded, store, "exec-a", "classify")
    assert views.predecessor_handoff(seeded, task_p) == ([bundle_id], "exec-a")
    view = _view(seeded, settings, TASK_P)
    assert view.predecessor_status is None  # 선행 조건 충족 — 선행 `완료` 를 기다리지 않는다
    status = views.status_of(task_p, view)
    assert (status.label, status.reason) == ("대기", "자동 실행 대기")  # P 는 run_mode auto — 워커가 잇는다
    ctx = views.task_context(seeded, store, task_p, now=NOW, settings=settings)
    assert ctx["can_run"] is True  # 직접 실행도 열려 있다

    # 선행이 검토 거절(실패)로 마감되면 새로 착수하지 않는다
    repo.update_task_status(seeded, TASK_T, "실패", "검토 거절", finished_at=NOW, review_decision="close", now=NOW)
    assert views.predecessor_handoff(seeded, task_p) == ([], None)
    assert _view(seeded, settings, TASK_P).predecessor_status == "실패"


def test_task_context_kind_label_from_registry(seeded, settings, store):
    _seed_review_task(seeded)
    assert views.task_context(seeded, store, repo.get_task(seeded, TASK_A), now=NOW, settings=settings)["kind_label"] == "버그 수정"
    assert views.task_context(seeded, store, repo.get_task(seeded, TASK_B), now=NOW, settings=settings)["kind_label"] == "커밋 검토"
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
    task_a, task_b = _seed_user_chain(seeded)
    _select(seeded, task_a, API_AGENT, CAP_TRIAGE)
    _select(seeded, task_b, PATCH_AGENT, CAP_PATCH)
    first, second = _chain(seeded, settings)["tasks"]
    assert (first["kind_label"], second["kind_label"]) == ("분류", "패치")
    assert "선행 #41 (ops.triage) → code.patch 인계" in second["reasons"]

    (rule_id, _), = [(rid, r) for rid, r in repo.list_rules(seeded, SESSION) if r.from_kind == "classify"]
    repo.delete_rule(seeded, SESSION, rule_id, now=NOW)
    second = _chain(seeded, settings)["tasks"][1]
    assert second["reasons"][0] == "후속 규칙 없음: classify → patch"


def test_agent_public_annotates_capabilities_with_kind_labels(seeded, settings):
    codex = views.agent_public(repo.get_agent(seeded, "agent-codex-mac"), now=NOW, settings=settings, kinds=BUILTIN_KINDS)
    assert [c["kind_label"] for c in codex["capabilities"]] == ["버그 수정", "커밋 검토"]
    unknown = views.agent_public(repo.get_agent(seeded, "agent-codex-mac"), now=NOW, settings=settings, kinds=())
    assert unknown["capabilities"][0]["kind_label"] is None
    default = views.agent_public(repo.get_agent(seeded, "agent-codex-mac"), now=NOW, settings=settings)
    assert default["capabilities"][0] == {"code": "code.fix", "scope": {"repository_id": REPOSITORY}, "kind_label": None}


# --- n8n 체인 — callback 상태·items_json 으로 구성 이유 재계산 (phase 7 step 6, ADR-0010) --------------------

CALLBACK_URL = "http://localhost:5678/webhook-waiting/1234"
N8N_ITEMS = [
    {
        "key": "fix-format",
        "title": "응답 형식 변경에 맞춰 보고서 변환 수정",
        "body": "변환 코드를 수정하고 재현 테스트를 추가해 주세요.",
        "labels": ["kind:bug_fix", f"repository_id:{REPOSITORY}"],
        "blocked_by": [],
    },
    {
        "key": "review-format",
        "title": "보고서 변환 수정 검토",
        "body": "수정 커밋을 검토해 주세요.",
        "labels": ["kind:code_review", f"repository_id:{REPOSITORY}"],
        "blocked_by": ["fix-format"],
    },
]


def _seed_n8n_chain(conn, *, callback_url: str | None = CALLBACK_URL, items=N8N_ITEMS) -> tuple[str, str]:
    """입구 API 가 만든 것과 같은 모양의 n8n 체인(bug_fix → code_review) — source_ref 는 항목 key. (task_a, task_b)."""
    repo.insert_chain(conn, {
        "chain_id": CHAIN, "session_id": SESSION, "source": "n8n",
        "title": "응답 형식 변경에 맞춰 보고서 변환 수정 → 보고서 변환 수정 검토",
        "callback_url": callback_url, "items": items,
    }, NOW)
    from .conftest import task_row
    repo.insert_work_item_task(conn, {**task_row("task-n1"), "chain_id": CHAIN, "source_ref": "fix-format"}, NOW)
    repo.insert_work_item_task(conn, {**task_row("task-n2", kind="code_review", predecessor="task-n1"),
                            "chain_id": CHAIN, "source_ref": "review-format"}, NOW)
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
    _select(seeded, task_a, "agent-codex-mac", CAP_FIX)
    _select(seeded, task_b, "agent-codex-mac", CAP_REVIEW)
    first, second = _chain(seeded, settings)["tasks"]
    assert first["reasons"] == [
        f"라벨 kind:bug_fix + repository_id:{REPOSITORY} → bug_fix",
        "blocked_by 없음 — 가져온 순서대로 배치",
        "체인의 첫 업무 — 선행 없음",
        "직접 실행 — 흐름의 첫 업무는 사람이 시작",
        "검토 후 완료 — 기본값",
        "code.fix 일치 후보 1개",
    ]
    assert second["reasons"][0] == f"라벨 kind:code_review + repository_id:{REPOSITORY} → code_review"
    assert second["reasons"][1:3] == [
        "blocked_by fix-format — fix-format 뒤에 배치",
        "선행 fix-format (code.fix) → code.review 인계",
    ]
    assert second["reasons"][-1] == "code.review 일치 후보 1개"


def test_n8n_chain_without_items_json_keeps_only_selection_reason(seeded, settings):
    task_a, _ = _seed_n8n_chain(seeded, items=None)
    _select(seeded, task_a, "agent-codex-mac", CAP_FIX)
    assert repo.get_chain(seeded, CHAIN)["items_json"] is None
    first, second = _chain(seeded, settings)["tasks"]
    assert first["reasons"] == ["code.fix 일치 후보 1개"]
    assert second["reasons"] == ["후보 없음"]


# --- phase 16 step 2: 목록 화면 문맥 ------------------------------------------------------------------------


def _list_context(conn, member_id: str, **query):
    return views.work_list_context(conn, SESSION, member_id=member_id, query=parse_list_query(**query), now=NOW)


def test_work_list_context_counts_filters_over_the_closed_scope(seeded):
    conn = seeded
    admin = repo.ensure_first_admin(conn, SESSION, now=NOW)
    repo.create_human_request_once(conn, TASK_A, "decision", "어느 쪽으로 고칠까요?", "decision:1", NOW)  # RUN-1 내 차례
    old = repo.insert_work_item_task(conn, task_row("task-old"), NOW)
    repo.set_work_status(conn, old, WorkStatus("종료", "닫힘"), now="2026-09-01T00:00:00Z")  # 19일 전

    ctx = _list_context(conn, admin, q="my_turn")
    assert ctx["query"] == parse_list_query(q="my_turn")
    assert ctx["counts"] == {"all": 2, "my_turn": 1, "unassigned": 2, "agent_working": 0}
    assert [r.work_key for r in ctx["rows"]] == ["RUN-1"]
    assert [(g.key, [r.work_key for r in g.rows]) for g in ctx["groups"]] == [("none", ["RUN-1"])]
    assert [len(c.rows) for c in ctx["columns"]] == [0, 0, 0, 1, 0, 0]
    assert ctx["open_missing"] is False

    ctx = _list_context(conn, admin, closed="all", group="status")
    assert ctx["counts"] == {"all": 3, "my_turn": 1, "unassigned": 2, "agent_working": 0}
    assert [r.work_key for r in ctx["rows"]] == ["RUN-3", "RUN-2", "RUN-1"]
    assert [g.key for g in ctx["groups"]] == ["status:새로 들어옴", "status:내 차례", "status:종료"]
    assert _list_context(conn, "mem-someone-else", q="my_turn")["counts"]["my_turn"] == 0


def test_work_list_context_normalizes_unknown_values_and_flags_a_missing_open_key(seeded):
    conn = seeded
    admin = repo.ensure_first_admin(conn, SESSION, now=NOW)
    ctx = _list_context(conn, admin, q="nope", group="x", view="grid", closed="old", open="RUN-99")
    assert ctx["query"] == parse_list_query(open="RUN-99")
    assert (ctx["query"].q, ctx["query"].group, ctx["query"].view, ctx["query"].closed) == (
        "all", "assignee", "list", "recent")
    assert ctx["open_missing"] is True
    assert _list_context(conn, admin, open="RUN-2")["open_missing"] is False
    assert _list_context(conn, admin, open="garbage")["open_missing"] is False  # 키 형식이 아니면 열 것이 없다


def test_list_query_params_leave_out_defaults():
    assert views.list_query_params(parse_list_query()) == ""
    assert views.list_query_params(parse_list_query(q="my_turn", group="status", view="board", closed="all"),
                                   open_key=12) == "q=my_turn&group=status&view=board&closed=all&open=RUN-12"
    assert views.list_query_params(parse_list_query(view="board", open="RUN-3")) == "view=board"  # open 은 인자로만
    assert views.list_query_params(parse_list_query(), open_key=4) == "open=RUN-4"


def test_list_href_changes_one_value_and_keeps_the_rest():
    query = parse_list_query(q="unassigned", group="status")
    assert views.list_href(query) == "/tasks?q=unassigned&group=status"
    assert views.list_href(query, q="all") == "/tasks?group=status"
    assert views.list_href(query, view="board", open_key=7) == "/tasks?q=unassigned&group=status&view=board&open=RUN-7"
    assert views.list_href(parse_list_query(), q="all") == "/tasks"


def test_response_buttons_name_what_each_action_does(seeded):
    """[답하고 다시 맡기기] = 에이전트를 다시 돌림, [검증만 다시] = 결과는 그대로 두고 검증만 (phase 17)."""
    request_id, _ = repo.create_human_request_once(seeded, TASK_A, "fix_verification_failed", "결과 판정 실패",
                                                   "fix_verification_failed:exec-1", NOW)
    row = repo.get_human_request(seeded, SESSION, request_id)
    data = views._request_public(seeded, row, can_respond=True, agent_choices=[])
    assert data["actions"] == [("resume", "답하고 다시 맡기기"), ("reverify", "검증만 다시"), ("close", "업무 종료")]

    other, _ = repo.create_human_request_once(seeded, TASK_A, "fix_needs_information", "재현 금액?",
                                              "fix_needs_information:exec-1", NOW)
    data = views._request_public(seeded, repo.get_human_request(seeded, SESSION, other), can_respond=True,
                                 agent_choices=[])
    assert data["actions"] == [("resume", "답하고 다시 맡기기"), ("close", "업무 종료")]


# --- 모니터링 탭 (phase 20 step 7) ---------------------------------------------------------------------------


def test_config_change_heads_name_the_change_or_say_no_record():
    change = {"revision": 4, "area": "triage_criteria", "action": "change", "subject": "v2",
              "by_member_name": "김OO", "occurred_at": "2026-10-02T16:00:00Z"}  # KST 10/3 01:00
    system = {**change, "revision": 5, "area": "source", "action": "add", "subject": "acme/billing",
              "by_member_name": None}
    heads = views.config_change_heads({4: [change], 5: [system]}, ["all", "3", "4", "5", "unknown"])
    assert heads == {
        "3": "설정 3 — 기록 없음",
        "4": "설정 4 — 판단 기준 변경 v2 · 김OO · 10/3",
        "5": "설정 5 — 저장소 연결 추가 acme/billing · 시스템 · 10/3",
    }


# --- 저장소 화면 담당 연결 — 수집한 이슈에서 본 GitHub 사용자 (phase 23 step 7) ---------------------------


def _issue_row(updated_at: str, ids: list[int], logins: list[str]) -> dict:
    return {"issue_updated_at": updated_at,
            "snapshot_json": json.dumps({"assignee_ids": ids, "assignee_logins": logins})}


def test_seen_github_users_pairs_ids_with_the_latest_login_sorted_by_login():
    rows = [
        _issue_row("2026-10-06T02:00:00Z", [7, 3], ["zed", "old-kim"]),
        _issue_row("2026-10-06T03:00:00Z", [3], ["kim-dev"]),  # 같은 id 는 가장 최근 이슈의 login
        _issue_row("2026-10-06T01:00:00Z", [3, 9], ["older-kim", "amy"]),
        _issue_row("2026-10-06T04:00:00Z", [], []),
    ]
    assert views.seen_github_users(rows) == [
        {"github_user_id": 9, "github_login": "amy"},
        {"github_user_id": 3, "github_login": "kim-dev"},
        {"github_user_id": 7, "github_login": "zed"},
    ]


def test_seen_github_users_is_empty_without_assignees():
    assert views.seen_github_users([]) == []
    assert views.seen_github_users([_issue_row("2026-10-06T01:00:00Z", [], [])]) == []
