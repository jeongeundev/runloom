"""업무 종류 등록부 조회·검사 — 등록부는 인자로 받고 DB 를 모른다 (ADR-0009, ADR-0004)."""

from workflow.contracts.v1 import BUILTIN_KINDS, Capability, KindSpec, SuccessorRule
from workflow.domain.kinds import (
    can_auto_complete,
    get_kind,
    kind_for_capability,
    validate_capability,
    validate_rule,
)

DIAGNOSIS, CODE_CHANGE = BUILTIN_KINDS

# 개념 절의 사용자 정의 종류 `review` — diff·code_change_result 를 받아 검토 의견을 낸다
REVIEW = KindSpec(
    kind="review", label="검토", capability_code="review", scope_key="repository_id",
    input_kinds=["diff", "code_change_result"], output_kind="generic_result",
    outcomes=["approved", "changes_requested", "needs_information"],
    instructions="diff 를 읽고 검토 의견을 남기세요.", builtin=False,
)
KINDS = [*BUILTIN_KINDS, REVIEW]


# --- kind_for_capability · get_kind --------------------------------------------------------


def test_kind_for_capability_finds_builtin_kinds():
    assert kind_for_capability(BUILTIN_KINDS, "operations.diagnose") is DIAGNOSIS
    assert kind_for_capability(BUILTIN_KINDS, "code.modify") is CODE_CHANGE


def test_kind_for_capability_finds_registered_kind():
    assert kind_for_capability(KINDS, "review") is REVIEW


def test_kind_for_unknown_capability_is_none():
    assert kind_for_capability(BUILTIN_KINDS, "ops.unknown") is None
    assert kind_for_capability([], "operations.diagnose") is None


def test_kind_for_capability_takes_first_when_duplicated():
    other = REVIEW.model_copy(update={"kind": "second_review"})

    assert kind_for_capability([REVIEW, other], "review") is REVIEW
    assert kind_for_capability([other, REVIEW], "review") is other


def test_get_kind_by_name():
    assert get_kind(KINDS, "diagnosis") is DIAGNOSIS
    assert get_kind(KINDS, "review") is REVIEW
    assert get_kind(BUILTIN_KINDS, "review") is None


# --- can_auto_complete -------------------------------------------------------------------


def test_only_builtin_diagnosis_can_auto_complete():
    assert can_auto_complete(DIAGNOSIS) is True
    assert can_auto_complete(CODE_CHANGE) is False
    assert can_auto_complete(REVIEW) is False


# --- validate_capability -----------------------------------------------------------------


def test_validate_capability_ok_for_builtin_and_registered():
    assert validate_capability(KINDS, Capability(code="operations.diagnose", scope={"workflow_id": "daily-report"})) is None
    assert validate_capability(KINDS, Capability(code="code.modify", scope={"repository_id": "demo-report-repo"})) is None
    assert validate_capability(KINDS, Capability(code="review", scope={"repository_id": "demo-report-repo"})) is None


def test_validate_capability_rejects_unknown_code():
    reason = validate_capability(BUILTIN_KINDS, Capability(code="review", scope={"repository_id": "x"}))

    assert reason == "모르는 능력 코드 review"


def test_validate_capability_rejects_wrong_scope_key():
    reason = validate_capability(BUILTIN_KINDS, Capability(code="code.modify", scope={"workflow_id": "daily-report"}))

    assert reason == "code.modify 의 scope 키는 repository_id 여야 합니다"


# --- validate_rule -----------------------------------------------------------------------


def _rule(**changes) -> SuccessorRule:
    base = {
        "from_kind": "code_change", "on_outcomes": ["ready_for_review"],
        "to_kind": "review", "handoff_kinds": ["diff", "code_change_result"],
    }
    return SuccessorRule(**{**base, **changes})


def test_validate_rule_ok():
    assert validate_rule(KINDS, _rule()) is None
    # handoff_kinds 는 input_kinds 를 포함하기만 하면 된다 — 더 넘겨도 된다
    assert validate_rule(KINDS, _rule(handoff_kinds=["diff", "code_change_result", "test_log_after"])) is None


def test_validate_rule_rejects_unregistered_kinds():
    assert validate_rule(BUILTIN_KINDS, _rule()) == "등록되지 않은 종류 review"
    assert validate_rule(KINDS, _rule(from_kind="lint")) == "등록되지 않은 종류 lint"


def test_validate_rule_rejects_outcome_not_in_from_kind():
    reason = validate_rule(KINDS, _rule(on_outcomes=["ready_for_review", "approved"]))

    assert reason == "on_outcomes 에 code_change 의 outcome 이 아닌 값이 있습니다: approved"


def test_validate_rule_rejects_missing_input_kinds():
    reason = validate_rule(KINDS, _rule(handoff_kinds=["diff"]))

    assert reason == "handoff_kinds 에 review 의 input_kinds 가 빠졌습니다: code_change_result"
