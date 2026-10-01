"""준비 판정 — ARCHITECTURE "준비 판정 — 대기 코드", PRD phase 8 수용 기준 A~E."""

from dataclasses import replace

import pytest

from workflow.contracts.v1 import Capability
from workflow.domain.delegation import ApprovalFact
from workflow.domain.selection import Candidate
from workflow.domain.task_readiness import (
    Blocker,
    ExecutorFacts,
    TaskFacts,
    TaskReadiness,
    evaluate_readiness,
)

NOW = "2026-09-23T03:00:00Z"
FIX_A = Capability(code="code.fix", scope={"repository_id": "repo-a"})
FIX_B = Capability(code="code.fix", scope={"repository_id": "repo-b"})
REVIEW_A = Capability(code="code.review", scope={"repository_id": "repo-a"})

CANDIDATES = (
    Candidate(agent_id="agent-a", capabilities=(FIX_A,)),
    Candidate(agent_id="agent-b", capabilities=(FIX_B,)),
    Candidate(agent_id="agent-review", capabilities=(REVIEW_A,)),
)


def _executor(agent_id: str, connector_id: str = "conn-1", repository_id: str = "repo-a", **kw) -> ExecutorFacts:
    values = dict(
        agent_id=agent_id,
        connector_id=connector_id,
        repository_id=repository_id,
        connection_type="local",
        connection_state="online",
        last_seen_at="2026-09-23T02:59:30Z",
        supported_kinds=("bug_fix", "code_review"),
    )
    values.update(kw)
    return ExecutorFacts(**values)


EXECUTORS = {
    "agent-a": _executor("agent-a"),
    "agent-b": _executor("agent-b", connector_id="conn-2", repository_id="repo-b"),
    "agent-review": _executor("agent-review"),
}


def _fix(task_id: str = "task-a", **kw) -> TaskFacts:
    values = dict(
        task_id=task_id,
        kind="bug_fix",
        now=NOW,
        offline_after_seconds=90,
        required=FIX_A,
        candidates=CANDIDATES,
        executors=EXECUTORS,
        assignee_ids=(101,),
        bindings={101: "agent-a", 102: "agent-b"},
        request_text="재현: python -m report 실행 시 KeyError",
        request_required=True,
    )
    values.update(kw)
    return TaskFacts(**values)


def _review(**kw) -> TaskFacts:
    values = dict(
        task_id="task-c",
        kind="code_review",
        now=NOW,
        offline_after_seconds=90,
        required=REVIEW_A,
        candidates=CANDIDATES,
        executors=EXECUTORS,
        chosen_agent_id="agent-review",
        pair_agent_id="agent-a",
        result_dependency_task_id="task-a",
        result_dependency_ready=True,
    )
    values.update(kw)
    return TaskFacts(**values)


def _codes(readiness: TaskReadiness) -> list[str]:
    return [b.code for b in readiness.blockers]


# --- 업무 A~E -----------------------------------------------------------------


def test_a_bound_single_assignee_is_ready():
    readiness = evaluate_readiness(_fix())

    assert readiness == TaskReadiness(ready=True, blockers=(), agent_id="agent-a")


def test_b_is_ready_independently_of_a_blockers():
    a = _fix(executors={**EXECUTORS, "agent-a": _executor("agent-a", connection_state="offline")})
    b = _fix("task-b", required=FIX_B, assignee_ids=(102,))

    assert not evaluate_readiness(a).ready
    readiness = evaluate_readiness(b)
    assert readiness.ready
    assert readiness.agent_id == "agent-b"


def test_c_review_waits_for_fix_result_then_becomes_ready():
    waiting = evaluate_readiness(_review(result_dependency_ready=False))

    assert not waiting.ready
    assert waiting.blockers == (Blocker("awaiting_result", "선행 결과 대기 — task-a", "system"),)
    assert evaluate_readiness(_review()).ready


def test_input_missing_when_request_text_is_blank():
    readiness = evaluate_readiness(_fix(request_text="  \n"))

    assert _codes(readiness) == ["input_missing"]
    assert readiness.blockers[0].actor == "assignee"


def test_input_missing_while_needs_information_has_no_newer_revision():
    asked = _fix(task_revision=2, information_requested_at_revision=2)
    answered = _fix(task_revision=3, information_requested_at_revision=2)

    assert _codes(evaluate_readiness(asked)) == ["input_missing"]
    assert evaluate_readiness(answered).ready


def test_d_repository_not_allowed_is_delegation_denied():
    readiness = evaluate_readiness(_fix(repository_allowed=False))

    assert _codes(readiness) == ["delegation_denied"]
    assert readiness.blockers[0].actor == "operator"


def test_d_bound_agent_without_matching_capability_is_delegation_denied():
    readiness = evaluate_readiness(_fix(required=FIX_B))  # agent-a 는 repo-a 만

    assert _codes(readiness) == ["delegation_denied"]
    assert readiness.blockers[0].reason == "선택한 에이전트에 code.fix 능력 없음"
    assert readiness.agent_id is None


def test_d_agent_not_allowed_in_session_is_delegation_denied():
    candidates = (Candidate(agent_id="agent-a", capabilities=(FIX_A,), allowed=False),)

    readiness = evaluate_readiness(_fix(candidates=candidates))

    assert _codes(readiness) == ["delegation_denied"]


def test_e_multiple_assignees_wait_for_decision():
    readiness = evaluate_readiness(_fix(assignee_ids=(101, 102), open_request_ids=("hr-1",)))

    assert _codes(readiness) == ["assignee_multiple", "decision_pending"]
    assert readiness.agent_id is None


def test_e_response_choosing_bound_agent_is_reevaluated_and_ready():
    readiness = evaluate_readiness(_fix(assignee_ids=(101, 102), chosen_agent_id="agent-a"))

    assert readiness.ready
    assert readiness.agent_id == "agent-a"


def test_e_response_still_rechecks_delegation():
    # agent-b 는 담당자 연결이지만 repo-a 의 code.fix 능력이 없다
    readiness = evaluate_readiness(_fix(assignee_ids=(101, 102), chosen_agent_id="agent-b"))

    assert _codes(readiness) == ["delegation_denied"]


def test_e_response_choosing_agent_outside_assignees_keeps_multiple():
    readiness = evaluate_readiness(_fix(assignee_ids=(101, 102), chosen_agent_id="agent-review"))

    assert _codes(readiness) == ["assignee_multiple"]


def test_response_without_new_information_still_rechecks_input():
    readiness = evaluate_readiness(
        _fix(assignee_ids=(101, 102), chosen_agent_id="agent-a", request_text="")
    )

    assert _codes(readiness) == ["input_missing"]


# --- 담당자 ---------------------------------------------------------------------


def test_no_assignee_waits_without_guessing():
    readiness = evaluate_readiness(_fix(assignee_ids=()))

    assert _codes(readiness) == ["assignee_missing"]
    assert readiness.agent_id is None


def test_unbound_assignee_waits_without_guessing():
    readiness = evaluate_readiness(_fix(assignee_ids=(999,)))

    assert _codes(readiness) == ["assignee_unbound"]
    assert readiness.blockers[0].reason == "GitHub 담당자 999 에 연결된 Agent 없음"


def test_without_assignee_concept_uses_existing_auto_selection():
    readiness = evaluate_readiness(_review(chosen_agent_id=None))

    assert readiness.ready
    assert readiness.agent_id == "agent-review"


def test_without_assignee_concept_and_no_candidate_is_delegation_denied():
    readiness = evaluate_readiness(_review(chosen_agent_id=None, candidates=CANDIDATES[:2]))

    assert _codes(readiness) == ["delegation_denied"]
    assert readiness.blockers[0].reason == "후보 없음"


# --- 실행 환경 -------------------------------------------------------------------


def test_offline_connector_state_is_executor_offline():
    executors = {**EXECUTORS, "agent-a": _executor("agent-a", connection_state="offline")}

    readiness = evaluate_readiness(_fix(executors=executors))

    assert _codes(readiness) == ["executor_offline"]
    assert readiness.blockers[0].actor == "system"


def test_stale_heartbeat_uses_now_argument():
    executors = {**EXECUTORS, "agent-a": _executor("agent-a", last_seen_at="2026-09-23T02:58:00Z")}

    assert _codes(evaluate_readiness(_fix(executors=executors))) == ["executor_offline"]
    assert evaluate_readiness(_fix(executors=executors, now="2026-09-23T02:59:00Z")).ready


def test_missing_executor_facts_is_offline():
    readiness = evaluate_readiness(_fix(executors={}))

    assert _codes(readiness) == ["executor_offline"]


def test_legacy_connector_without_supported_kinds_is_outdated_for_new_kinds():
    executors = {**EXECUTORS, "agent-a": _executor("agent-a", supported_kinds=None)}

    readiness = evaluate_readiness(_fix(executors=executors))

    assert _codes(readiness) == ["executor_outdated"]
    assert readiness.blockers[0].reason == "연결 프로그램 업데이트 필요 — bug_fix 미지원"


def test_legacy_connector_runs_only_user_kinds():
    executors = {**EXECUTORS, "agent-a": _executor("agent-a", supported_kinds=None)}

    assert evaluate_readiness(_fix(executors=executors, kind="classify")).ready
    # 옛 내장 code_change 는 없어졌다(ADR-0019) — 같은 이름이면 사용자 정의 종류로 본다
    assert evaluate_readiness(_fix(executors=executors, kind="code_change")).ready
    assert _codes(evaluate_readiness(_fix(executors=executors))) == ["executor_outdated"]


def test_declared_kinds_without_this_builtin_is_outdated():
    executors = {**EXECUTORS, "agent-a": _executor("agent-a", supported_kinds=("code_review",))}

    assert _codes(evaluate_readiness(_fix(executors=executors))) == ["executor_outdated"]


def test_runner_without_the_required_capability_is_outdated():
    """검증만 다시(phase 17) — 러너가 `verify_only` 를 보고하지 않았으면(옛 러너 = None) 업데이트를 기다린다."""
    for reported in (None, ()):
        executors = {**EXECUTORS, "agent-a": _executor("agent-a", runner_capabilities=reported)}
        readiness = evaluate_readiness(_fix(executors=executors, required_runner_capability="verify_only"))
        assert _codes(readiness) == ["executor_outdated"]
        assert readiness.blockers[0].reason == "연결 프로그램 업데이트 필요 — 검증만 다시 미지원"
        assert readiness.blockers[0].actor == "operator"


def test_runner_reporting_the_required_capability_is_ready():
    executors = {**EXECUTORS, "agent-a": _executor("agent-a", runner_capabilities=("verify_only",))}
    assert evaluate_readiness(_fix(executors=executors, required_runner_capability="verify_only")).ready
    # 능력 조건이 없는 보통 실행은 보고가 없어도 그대로
    assert evaluate_readiness(_fix()).ready


def test_repository_busy_blocks_only_this_repository():
    readiness = evaluate_readiness(_fix(busy_execution_ids=("exec-other",)))

    assert _codes(readiness) == ["repository_busy"]
    assert readiness.blockers[0].actor == "system"


def test_review_agent_on_other_connector_is_mismatch():
    executors = {**EXECUTORS, "agent-review": _executor("agent-review", connector_id="conn-2")}

    readiness = evaluate_readiness(_review(executors=executors))

    assert _codes(readiness) == ["review_repository_mismatch"]


def test_review_agent_on_other_repository_is_mismatch():
    executors = {**EXECUTORS, "agent-review": _executor("agent-review", repository_id="repo-b")}

    assert _codes(evaluate_readiness(_review(executors=executors))) == ["review_repository_mismatch"]


# --- 모드·마감·재작업 ---------------------------------------------------------------


def test_manual_mode_is_listed_with_other_conditions_satisfied():
    readiness = evaluate_readiness(_fix(run_mode="manual"))

    assert _codes(readiness) == ["manual_mode"]
    assert readiness.agent_id == "agent-a"


def test_rework_limit_reached_blocks_rework():
    at_limit = _fix(rework_requested=True, rework_rounds_used=1, max_rework_rounds=1)
    under = _fix(rework_requested=True, rework_rounds_used=0, max_rework_rounds=1)

    assert _codes(evaluate_readiness(at_limit)) == ["rework_limit_reached"]
    assert evaluate_readiness(under).ready


def test_source_closed_blocks_new_start():
    readiness = evaluate_readiness(_fix(source_state="closed"))

    assert _codes(readiness) == ["source_closed"]
    assert evaluate_readiness(_fix(source_state="open")).ready


def test_operator_closed_task_reports_only_task_closed():
    readiness = evaluate_readiness(_fix(closed=True, assignee_ids=(), source_state="closed"))

    assert readiness.blockers == (Blocker("task_closed", "운영자 종료", "operator"),)
    assert not readiness.ready


def test_all_blockers_are_collected_at_once():
    executors = {**EXECUTORS, "agent-a": _executor("agent-a", connection_state="offline", supported_kinds=None)}
    facts = _fix(
        executors=executors,
        request_text="",
        open_request_ids=("hr-1",),
        run_mode="manual",
        source_state="closed",
        busy_execution_ids=("exec-x",),
    )

    readiness = evaluate_readiness(facts)

    assert set(_codes(readiness)) == {
        "input_missing",
        "decision_pending",
        "executor_offline",
        "executor_outdated",
        "repository_busy",
        "manual_mode",
        "source_closed",
    }
    assert not readiness.ready


def test_facts_are_values_not_rows():
    facts = _fix()

    with pytest.raises(AttributeError):
        facts.kind = "code_review"  # frozen
    assert replace(facts, kind="classify").kind == "classify"


# --- 자동 매칭 결과 (phase 11 step 6, ADR-0017) ----------------------------------------------


def _unmatched(code: str) -> Blocker:
    return Blocker(code, f"{code} 사유", "operator")


def test_auto_matched_agent_is_ready_without_assignee():
    for assignees in ((), (101, 102), (999,)):
        readiness = evaluate_readiness(_fix(assignee_ids=assignees, auto_match=True, matched_agent_id="agent-a"))
        assert readiness == TaskReadiness(ready=True, blockers=(), agent_id="agent-a")


def test_auto_match_blockers_replace_assignee_blockers():
    readiness = evaluate_readiness(
        _fix(assignee_ids=(), auto_match=True, match_blockers=(_unmatched("fix_agent_ambiguous"),))
    )

    assert _codes(readiness) == ["fix_agent_ambiguous"]
    assert readiness.agent_id is None


def test_auto_match_blockers_are_collected_with_other_blockers():
    readiness = evaluate_readiness(
        _fix(auto_match=True, match_blockers=(_unmatched("repository_unmatched"),), source_state="closed",
             delegated=False)
    )

    assert _codes(readiness) == ["source_closed", "repository_unmatched", "not_delegated"]


def test_auto_matched_agent_still_passes_capability_and_executor_checks():
    wrong = evaluate_readiness(_fix(auto_match=True, matched_agent_id="agent-b"))  # repo-b 만
    offline = evaluate_readiness(_fix(
        auto_match=True, matched_agent_id="agent-a",
        executors={**EXECUTORS, "agent-a": _executor("agent-a", connection_state="offline")},
    ))

    assert _codes(wrong) == ["delegation_denied"]
    assert _codes(offline) == ["executor_offline"]


def test_auto_matched_review_agent_skips_automatic_selection():
    review = _review(chosen_agent_id=None, auto_match=True, match_blockers=(_unmatched("review_agent_unmatched"),))

    assert _codes(evaluate_readiness(review)) == ["review_agent_unmatched"]
    ready = evaluate_readiness(_review(chosen_agent_id=None, auto_match=True, matched_agent_id="agent-review"))
    assert ready.agent_id == "agent-review"


def test_default_agent_covers_filtered_assignee_gaps_but_binding_wins():
    assert evaluate_readiness(_fix(assignee_ids=(), matched_agent_id="agent-a")).agent_id == "agent-a"
    assert evaluate_readiness(_fix(assignee_ids=(999,), matched_agent_id="agent-a")).agent_id == "agent-a"
    assert evaluate_readiness(_fix(assignee_ids=(101, 102), matched_agent_id="agent-a")).agent_id == "agent-a"
    bound = evaluate_readiness(_fix(required=FIX_B, assignee_ids=(102,), matched_agent_id="agent-a"))
    assert bound.agent_id == "agent-b"


def test_without_match_input_assignee_blockers_are_unchanged():
    assert _codes(evaluate_readiness(_fix(assignee_ids=()))) == ["assignee_missing"]
    assert _codes(evaluate_readiness(_fix(assignee_ids=(101, 102)))) == ["assignee_multiple"]
    assert _codes(evaluate_readiness(_fix(assignee_ids=(999,)))) == ["assignee_unbound"]


def test_review_by_the_fix_agent_itself_is_ready():
    """ADR-0018 결정 1: 수정과 검토를 같은 Agent 가 맡는다(실행·worktree 는 따로). 짝 검사는 자기 자신이라 통과한다."""
    both = Candidate(agent_id="agent-a", capabilities=(FIX_A, REVIEW_A))

    readiness = evaluate_readiness(_review(candidates=(both,), chosen_agent_id="agent-a", pair_agent_id="agent-a"))

    assert readiness == TaskReadiness(ready=True, blockers=(), agent_id="agent-a")


def test_direct_work_blocks_the_agent_start():
    readiness = evaluate_readiness(_fix(direct_work=True))
    assert not readiness.ready
    assert readiness.blockers == (Blocker("direct_work", "직접 작업 중 — 에이전트에게 넘기면 시작", "operator"),)
    assert evaluate_readiness(_fix(direct_work=False)).ready


# --- 소유자 승인·꺼진 러너 (phase 17) ---------------------------------------------


def test_offline_reason_names_runner_owner():
    executors = {**EXECUTORS, "agent-a": _executor("agent-a", connection_state="offline", owner_name="이OO")}

    readiness = evaluate_readiness(_fix(executors=executors))

    assert readiness.blockers == (Blocker("executor_offline", "이OO의 러너 꺼짐 · 켜지면 시작", "system"),)


def test_offline_reason_for_shared_runner():
    executors = {**EXECUTORS, "agent-a": _executor("agent-a", connection_state="offline")}

    assert evaluate_readiness(_fix(executors=executors)).blockers == (
        Blocker("executor_offline", "공용 러너 꺼짐 · 켜지면 시작", "system"),)


def test_missing_executor_reason_is_unchanged():
    assert evaluate_readiness(_fix(executors={})).blockers == (
        Blocker("executor_offline", "agent-a 연결 정보 없음", "system"),)


@pytest.mark.parametrize("state", ["missing", "pending"])
def test_owner_approval_pending_blocks_resolved_agent(state):
    approvals = {"agent-a": ApprovalFact(state, "김OO 가 맡김 · 이OO 승인 대기")}

    readiness = evaluate_readiness(_fix(owner_approvals=approvals))

    assert not readiness.ready and readiness.agent_id == "agent-a"
    assert readiness.blockers == (Blocker("owner_approval_pending", "김OO 가 맡김 · 이OO 승인 대기", "operator"),)


def test_owner_approval_declined_blocks_resolved_agent():
    approvals = {"agent-a": ApprovalFact("declined", "이OO 가 거절 — 다른 담당을 고르세요")}

    assert evaluate_readiness(_fix(owner_approvals=approvals)).blockers == (
        Blocker("owner_approval_declined", "이OO 가 거절 — 다른 담당을 고르세요", "operator"),)


@pytest.mark.parametrize("state", ["not_needed", "approved"])
def test_owner_approval_not_needed_or_approved_is_ready(state):
    assert evaluate_readiness(_fix(owner_approvals={"agent-a": ApprovalFact(state, "")})).ready


def test_owner_approval_of_other_agent_is_ignored():
    approvals = {"agent-b": ApprovalFact("pending", "김OO 가 맡김 · 박OO 승인 대기")}

    assert evaluate_readiness(_fix(owner_approvals=approvals)).ready


def test_owner_approval_default_is_not_needed():
    assert _fix().owner_approvals == {}
    assert _executor("agent-a").owner_name is None
