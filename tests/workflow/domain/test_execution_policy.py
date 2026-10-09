"""execution_policy — 종류 이름 분기 대신 조회하는 실행 정책 표 (ADR-0014 결정 3, phase 8 step 10)."""

import ast
import inspect

from workflow.contracts.v1 import BUILTIN_KIND_NAMES, BUILTIN_KINDS, KindSpec
from workflow.domain import execution_policy
from workflow.domain.execution_policy import (
    BUILTIN_POLICIES,
    GENERIC_POLICY,
    TRIAGE_OUTPUT_KIND,
    is_triage_kind,
    policy_for,
)


def test_every_builtin_kind_has_a_policy_matching_its_kind_spec():
    assert set(BUILTIN_POLICIES) == set(BUILTIN_KIND_NAMES)
    for spec in BUILTIN_KINDS:
        policy = BUILTIN_POLICIES[spec.kind]
        assert policy.kind == spec.kind
        assert policy.result_kind == spec.output_kind


def test_builtin_policies_required_artifacts_and_verifiers():
    assert policy_for("bug_fix").required_artifacts == ("diff", "test_log_before", "test_log_after", "verification_log")
    assert policy_for("bug_fix").verifier == "code_change"
    assert policy_for("code_review").required_artifacts == ()
    assert policy_for("code_review").verifier == "commit_review"
    # 판단 — 읽기 전용 체크아웃, 결과 봉투 triage_result, 업무 순환 밖 (ARCHITECTURE "판단 단계 가르기")
    triage = policy_for("triage")
    assert (triage.target, triage.result_kind, triage.required_artifacts, triage.verifier, triage.cycle) == (
        "triage", "triage_result", (), "triage", False)


def test_only_the_github_cycle_kinds_are_driven_by_readiness_and_followup_decisions():
    assert {k for k, p in BUILTIN_POLICIES.items() if p.cycle} == {"bug_fix", "code_review"}
    # 검토는 자기 요청이 아니라 수정 결과에서 시작한다
    assert policy_for("code_review").starts_from_result
    assert not policy_for("bug_fix").starts_from_result


def test_user_defined_kinds_get_the_generic_policy():
    assert policy_for("review") is GENERIC_POLICY
    # 진단 데모의 옛 내장 이름은 공개 데모 전용(종료) — 지금은 사용자 정의 이름일 뿐이다 (ADR-0019)
    assert policy_for("diagnosis") is GENERIC_POLICY
    assert policy_for("code_change") is GENERIC_POLICY
    assert (GENERIC_POLICY.target, GENERIC_POLICY.verifier, GENERIC_POLICY.cycle) == ("local", "generic", False)


def test_triage_stage_is_decided_by_output_kind_not_by_name():
    """판단 단계는 결과 형태(`output_kind == "triage_result"`)로 가른다 — 이름을 바꿔도 같은 판정 (ADR-0025)."""
    bug_fix, code_review, triage = BUILTIN_KINDS
    assert TRIAGE_OUTPUT_KIND == "triage_result"
    assert is_triage_kind(triage)
    assert not is_triage_kind(bug_fix) and not is_triage_kind(code_review)
    # 검증 없이 이름만 바꾼 봉투 — 판정은 이름을 보지 않는다
    assert is_triage_kind(triage.model_copy(update={"kind": "classify_work"}))
    assert not is_triage_kind(bug_fix.model_copy(update={"kind": "triage"}))
    user = KindSpec(kind="classify", label="분류", capability_code="ops.classify", scope_key="workflow_id",
                    input_kinds=[], output_kind="generic_result", outcomes=["done"], instructions="", builtin=False)
    assert not is_triage_kind(user)


def test_policy_module_is_pure():
    tree = ast.parse(inspect.getsource(execution_policy))
    imported = {
        (node.module if isinstance(node, ast.ImportFrom) else alias.name).split(".")[0]
        for node in ast.walk(tree) if isinstance(node, ast.Import | ast.ImportFrom)
        for alias in node.names
    }
    assert imported <= {"dataclasses", "typing", "types", "workflow"}
