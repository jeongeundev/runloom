"""execution_policy — 종류 이름 분기 대신 조회하는 실행 정책 표 (ADR-0014 결정 3, phase 8 step 10)."""

import ast
import inspect

from workflow.contracts.v1 import BUILTIN_KIND_NAMES, BUILTIN_KINDS
from workflow.domain import execution_policy
from workflow.domain.execution_policy import BUILTIN_POLICIES, GENERIC_POLICY, policy_for


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


def test_only_the_github_cycle_kinds_are_driven_by_readiness_and_followup_decisions():
    assert {k for k, p in BUILTIN_POLICIES.items() if p.cycle} == {"bug_fix", "code_review"}
    # 검토는 자기 요청이 아니라 수정 결과에서 시작한다
    assert policy_for("code_review").starts_from_result
    assert not policy_for("bug_fix").starts_from_result


def test_user_defined_kinds_get_the_generic_policy():
    assert policy_for("review") is GENERIC_POLICY
    # 진단 데모의 옛 내장 이름은 main 전용 — service 에서는 사용자 정의 이름일 뿐이다 (ADR-0019)
    assert policy_for("diagnosis") is GENERIC_POLICY
    assert policy_for("code_change") is GENERIC_POLICY
    assert (GENERIC_POLICY.target, GENERIC_POLICY.verifier, GENERIC_POLICY.cycle) == ("local", "generic", False)


def test_policy_module_is_pure():
    tree = ast.parse(inspect.getsource(execution_policy))
    imported = {
        (node.module if isinstance(node, ast.ImportFrom) else alias.name).split(".")[0]
        for node in ast.walk(tree) if isinstance(node, ast.Import | ast.ImportFrom)
        for alias in node.names
    }
    assert imported <= {"dataclasses", "typing", "types", "workflow"}
