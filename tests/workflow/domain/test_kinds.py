"""업무 종류 등록부 조회·검사 — 등록부는 인자로 받고 DB 를 모른다 (ADR-0009, ADR-0004)."""

from workflow.contracts.v1 import BUILTIN_KINDS, Capability, KindSpec, SuccessorRule
from workflow.domain.kinds import (
    BUILTIN_CAPABILITY_CODES,
    SCOPE_MANY_REPOSITORIES,
    SCOPE_NO_REPOSITORY,
    agent_repository_scope,
    editable_kinds,
    get_kind,
    kind_for_capability,
    validate_capability,
    validate_rule,
)

BUG_FIX, CODE_REVIEW = BUILTIN_KINDS[:2]

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
    assert kind_for_capability(BUILTIN_KINDS, "code.fix") is BUG_FIX
    assert kind_for_capability(BUILTIN_KINDS, "code.review") is CODE_REVIEW
    assert kind_for_capability(BUILTIN_KINDS, "operations.diagnose") is None  # 진단 데모는 main 전용 (ADR-0019)


def test_kind_for_capability_finds_registered_kind():
    assert kind_for_capability(KINDS, "review") is REVIEW


def test_kind_for_unknown_capability_is_none():
    assert kind_for_capability(BUILTIN_KINDS, "ops.unknown") is None
    assert kind_for_capability([], "code.fix") is None


def test_kind_for_capability_takes_first_when_duplicated():
    other = REVIEW.model_copy(update={"kind": "second_review"})

    assert kind_for_capability([REVIEW, other], "review") is REVIEW
    assert kind_for_capability([other, REVIEW], "review") is other


def test_get_kind_by_name():
    assert get_kind(KINDS, "bug_fix") is BUG_FIX
    assert get_kind(KINDS, "diagnosis") is None
    assert get_kind(KINDS, "review") is REVIEW
    assert get_kind(BUILTIN_KINDS, "review") is None


# --- validate_capability -----------------------------------------------------------------


def test_validate_capability_ok_for_builtin_and_registered():
    assert validate_capability(KINDS, Capability(code="code.fix", scope={"repository_id": "demo-report-repo"})) is None
    assert validate_capability(KINDS, Capability(code="review", scope={"repository_id": "demo-report-repo"})) is None


def test_validate_capability_rejects_unknown_code():
    reason = validate_capability(BUILTIN_KINDS, Capability(code="review", scope={"repository_id": "x"}))

    assert reason == "모르는 능력 코드 review"


def test_validate_capability_rejects_wrong_scope_key():
    reason = validate_capability(BUILTIN_KINDS, Capability(code="code.fix", scope={"workflow_id": "daily-report"}))

    assert reason == "code.fix 의 scope 키는 repository_id 여야 합니다"


# --- validate_rule -----------------------------------------------------------------------


def _rule(**changes) -> SuccessorRule:
    base = {
        "from_kind": "bug_fix", "on_outcomes": ["ready_for_review"],
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

    assert reason == "on_outcomes 에 bug_fix 의 outcome 이 아닌 값이 있습니다: approved"


def test_validate_rule_rejects_missing_input_kinds():
    reason = validate_rule(KINDS, _rule(handoff_kinds=["diff"]))

    assert reason == "handoff_kinds 에 review 의 input_kinds 가 빠졌습니다: code_change_result"


def test_validate_rule_rejects_triage_kinds_on_either_side():
    """판단 단계는 후속 규칙을 만들지도 받지도 않는다 — 결과 형태로 가른다 (ADR-0025)."""
    triage = BUILTIN_KINDS[2]
    renamed = triage.model_copy(update={"kind": "sorter"})  # 이름이 아니라 결과 형태
    kinds = [*KINDS, renamed]
    reason = "판단 종류는 후속 규칙에 쓸 수 없습니다"
    assert validate_rule(kinds, _rule(from_kind="triage", on_outcomes=["ready"])) == reason
    assert validate_rule(kinds, _rule(from_kind="sorter", on_outcomes=["ready"])) == reason
    assert validate_rule(kinds, _rule(to_kind="triage", handoff_kinds=["diff"])) == reason


# --- agent_repository_scope (phase 23 step 4) -------------------------------------------


def _caps(*pairs: tuple[str, dict]) -> list[Capability]:
    return [Capability(code=code, scope=scope) for code, scope in pairs]


def test_builtin_capability_codes_are_the_builtin_kinds_codes():
    assert BUILTIN_CAPABILITY_CODES == frozenset({"code.fix", "code.review", "code.triage"})
    assert BUILTIN_CAPABILITY_CODES == frozenset(spec.capability_code for spec in BUILTIN_KINDS)


def test_runner_agent_scope_is_its_repository():
    caps = _caps(("code.fix", {"repository_id": "billing"}), ("code.review", {"repository_id": "billing"}),
                 ("code.triage", {"repository_id": "billing"}), ("review", {"repository_id": "other"}))
    assert agent_repository_scope(caps) == ("billing", None)  # 사용자 정의 능력의 scope 는 보지 않는다


def test_agent_without_builtin_repository_capability_has_no_scope():
    assert agent_repository_scope([]) == (None, SCOPE_NO_REPOSITORY)
    manual = _caps(("operations.diagnose", {"workflow_id": "daily-report"}), ("review", {"repository_id": "x"}))
    assert agent_repository_scope(manual) == (None, "no_repository")
    other_key = _caps(("code.fix", {"workflow_id": "billing"}))
    assert agent_repository_scope(other_key) == (None, "no_repository")


def test_agent_with_many_repositories_has_no_scope():
    caps = _caps(("code.fix", {"repository_id": "billing"}), ("code.review", {"repository_id": "shop"}))
    assert agent_repository_scope(caps) == (None, SCOPE_MANY_REPOSITORIES)
    assert SCOPE_MANY_REPOSITORIES == "many_repositories"


# --- editable_kinds (phase 23 step 6) -----------------------------------------------------


def test_editable_kinds_are_user_kinds_with_own_code_and_repository_scope():
    fix_alias = REVIEW.model_copy(update={"kind": "fix_alias", "capability_code": "code.fix"})
    workflow_scoped = REVIEW.model_copy(update={"kind": "ops", "capability_code": "ops", "scope_key": "workflow_id"})
    audit = REVIEW.model_copy(update={"kind": "audit", "capability_code": "audit"})
    kinds = [*BUILTIN_KINDS, REVIEW, fix_alias, workflow_scoped, audit]
    assert [s.kind for s in editable_kinds(kinds)] == ["review", "audit"]  # 등록부 순서 그대로
    assert editable_kinds(BUILTIN_KINDS) == []
