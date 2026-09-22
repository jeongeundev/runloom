"""후속 결정 — ARCHITECTURE "후속 결정 표 (`decide_followup`)", ADR-0014 6항."""

from dataclasses import replace

from workflow.contracts.v1 import BUILTIN_RULES
from workflow.domain.task_followup import (
    FollowupContext,
    FollowupTaskSpec,
    ReviewFacts,
    decide_followup,
)

FIX_COMMIT = "a" * 40
NEW_FIX_COMMIT = "b" * 40


def _fix(**kw) -> FollowupContext:
    values = dict(
        session_id="ses-1",
        task_id="task-fix",
        kind="bug_fix",
        execution_id="exe-fix-1",
        outcome="ready_for_review",
        verdict="passed",
        result_commit=FIX_COMMIT,
        rules=BUILTIN_RULES,
        rules_revision=3,
    )
    values.update(kw)
    return FollowupContext(**values)


def _review(outcome: str = "changes_requested", *, rounds_used: int = 0, max_rework_rounds: int = 1, **kw):
    review = ReviewFacts(
        fix_task_id="task-fix",
        source_execution_id="exe-fix-1",
        reviewed_commit=FIX_COMMIT,
        latest_fix_execution_id="exe-fix-1",
        latest_fix_commit=FIX_COMMIT,
        rounds_used=rounds_used,
        max_rework_rounds=max_rework_rounds,
    )
    values = dict(
        task_id="task-review",
        kind="code_review",
        execution_id="exe-rev-1",
        outcome=outcome,
        result_commit=None,
        review=review,
    )
    values.update(kw)
    return _fix(**values)


# --- 수정 결과 → 검토 연결·생성 ---


def test_fix_result_without_review_task_creates_one():
    decision = decide_followup(_fix())
    assert decision.action == "create_task"
    assert decision.create == FollowupTaskSpec(
        session_id="ses-1",
        kind="code_review",
        cause_execution_id="exe-fix-1",
        predecessor_task_id="task-fix",
        rules_revision=3,
    )
    assert decision.cause_key == "review:exe-fix-1"
    assert decision.review_commit == FIX_COMMIT
    assert decision.target_task_id is None


def test_fix_result_links_existing_review_by_explicit_reference():
    # 이전 라운드에서 만든 검토, 또는 predecessor_task_id 로 미리 등록된 검토(업무 C)
    decision = decide_followup(_fix(existing_followup_task_id="task-review"))
    assert decision.action == "link_existing"
    assert decision.target_task_id == "task-review"
    assert decision.create is None
    assert decision.cause_key == "review:exe-fix-1"
    assert decision.review_commit == FIX_COMMIT


def test_each_new_fix_result_links_review_again():
    first = decide_followup(_fix(existing_followup_task_id="task-review"))
    second = decide_followup(
        _fix(existing_followup_task_id="task-review", execution_id="exe-fix-2", result_commit=NEW_FIX_COMMIT)
    )
    assert second.action == "link_existing"
    assert second.cause_key == "review:exe-fix-2" != first.cause_key
    assert second.review_commit == NEW_FIX_COMMIT


def test_same_fix_result_again_is_not_followed_twice():
    handled = frozenset({"review:exe-fix-1"})
    decision = decide_followup(_fix(handled_cause_keys=handled))
    assert decision.action == "none"
    assert decision.create is None


def test_already_handled_other_result_does_not_block_new_one():
    decision = decide_followup(_fix(execution_id="exe-fix-2", handled_cause_keys=frozenset({"review:exe-fix-1"})))
    assert decision.action == "create_task"


def test_fix_outcome_not_in_rule_has_no_followup():
    decision = decide_followup(_fix(rules=()))
    assert decision.action == "none"


def test_fix_needs_information_requests_human():
    decision = decide_followup(_fix(outcome="needs_information", result_commit=None))
    assert decision.action == "request_human"
    assert decision.request_code == "fix_needs_information"
    assert decision.target_task_id == "task-fix"
    assert decision.cause_key == "fix_needs_information:exe-fix-1"


def test_failed_verdict_requests_human_without_retry():
    decision = decide_followup(_fix(verdict="failed"))
    assert decision.action == "request_human"
    assert decision.request_code == "fix_verification_failed"
    assert decision.create is None


# --- 검토 결과 ---


def test_approved_closes_review_only_no_merge_or_issue_close():
    decision = decide_followup(_review("approved"))
    assert decision.action == "none"
    assert decision.target_task_id is None
    assert decision.create is None
    assert "병합·이슈 종료는 사람" in decision.reason


def test_changes_requested_reworks_same_fix_task():
    decision = decide_followup(_review("changes_requested"))
    assert decision.action == "rework"
    assert decision.target_task_id == "task-fix"
    assert decision.create is None
    assert decision.base_commit == FIX_COMMIT
    assert decision.input_execution_ids == ("exe-fix-1", "exe-rev-1")
    assert decision.cause_key == "rework:exe-rev-1"


def test_rework_limit_reached_requests_human():
    decision = decide_followup(_review("changes_requested", rounds_used=1, max_rework_rounds=1))
    assert decision.action == "request_human"
    assert decision.request_code == "rework_limit_reached"
    assert decision.target_task_id == "task-fix"
    assert decision.base_commit is None


def test_rework_disabled_goes_straight_to_human():
    decision = decide_followup(_review("changes_requested", rounds_used=0, max_rework_rounds=0))
    assert decision.action == "request_human"
    assert decision.request_code == "rework_limit_reached"


def test_rework_within_larger_budget():
    decision = decide_followup(_review("changes_requested", rounds_used=2, max_rework_rounds=3))
    assert decision.action == "rework"


def test_review_needs_information_requests_human():
    decision = decide_followup(_review("needs_information"))
    assert decision.action == "request_human"
    assert decision.request_code == "review_needs_information"
    assert decision.target_task_id == "task-review"


def test_review_of_previous_commit_is_stale():
    ctx = _review("changes_requested")
    stale = replace(ctx.review, latest_fix_execution_id="exe-fix-2", latest_fix_commit=NEW_FIX_COMMIT)
    decision = decide_followup(replace(ctx, review=stale))
    assert decision.action == "none"
    assert decision.hold_code == "stale_review"


def test_same_review_result_again_is_not_reworked_twice():
    decision = decide_followup(_review("changes_requested", handled_cause_keys=frozenset({"rework:exe-rev-1"})))
    assert decision.action == "none"


# --- 종료·닫힘 ---


def test_operator_closed_task_makes_no_followup():
    assert decide_followup(_fix(task_closed=True)).action == "none"
    decision = decide_followup(_review("changes_requested", task_closed=True))
    assert decision.action == "none"
    assert decision.hold_code == "task_closed"


def test_source_closed_holds_followup():
    decision = decide_followup(_fix(source_state="closed"))
    assert decision.action == "none"
    assert decision.hold_code == "source_closed"
    assert decision.create is None
