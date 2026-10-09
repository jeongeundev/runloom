"""후속 규칙 판단 — 등록된 값의 명시적 비교만 하고 이유 문장을 붙인다 (ADR-0009, ADR-0004).

착수 조건(결과 + 판정 + outcome)을 워커에 적용하는 것은 step 4 다. 여기서는 판단 함수만 본다.
"""

from workflow.contracts.v1 import BUILTIN_RULES, SuccessorRule
from workflow.domain.succession import continue_reason, may_continue, rule_for

BUG_FIX_TO_CODE_REVIEW = BUILTIN_RULES[0]
CODE_CHANGE_TO_REVIEW = SuccessorRule(
    from_kind="code_change", on_outcomes=["ready_for_review"], to_kind="review",
    handoff_kinds=["diff", "code_change_result"],
)


def test_rule_for_finds_builtin_rule():
    assert rule_for(BUILTIN_RULES, "bug_fix", "code_review") is BUG_FIX_TO_CODE_REVIEW


def test_rule_for_registered_rule():
    rules = [*BUILTIN_RULES, CODE_CHANGE_TO_REVIEW]

    assert rule_for(rules, "code_change", "review") is CODE_CHANGE_TO_REVIEW


def test_rule_for_missing_pair_is_none():
    assert rule_for(BUILTIN_RULES, "code_change", "review") is None
    assert rule_for(BUILTIN_RULES, "code_review", "bug_fix") is None
    assert rule_for(BUILTIN_RULES, "diagnosis", "code_change") is None  # 진단 데모 규칙은 공개 데모 전용(종료) (ADR-0019)
    assert rule_for([], "bug_fix", "code_review") is None


def test_may_continue_only_on_listed_outcomes():
    assert may_continue(BUG_FIX_TO_CODE_REVIEW, "ready_for_review") is True
    assert may_continue(BUG_FIX_TO_CODE_REVIEW, "needs_information") is False
    assert may_continue(BUG_FIX_TO_CODE_REVIEW, "approved") is False


def test_continue_reason_names_rule_and_outcome():
    assert continue_reason(BUG_FIX_TO_CODE_REVIEW, "ready_for_review") == (
        "선행 outcome ready_for_review — 규칙 bug_fix → code_review 로 착수"
    )
    assert continue_reason(BUG_FIX_TO_CODE_REVIEW, "needs_information") == (
        "선행 outcome needs_information 은 규칙 대상 아님 — 확인 필요"
    )
    assert continue_reason(CODE_CHANGE_TO_REVIEW, "ready_for_review") == (
        "선행 outcome ready_for_review — 규칙 code_change → review 로 착수"
    )
