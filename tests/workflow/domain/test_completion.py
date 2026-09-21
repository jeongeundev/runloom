"""완료 기준 템플릿·병합 — PRD 4절. 사용자 정의 종류는 사람 검토 항목 하나다 (ADR-0009)."""

from workflow.contracts.v1 import BUILTIN_KINDS, KindSpec
from workflow.domain.completion import Criterion, criteria_template, merge_criteria

DIAGNOSIS, CODE_CHANGE = BUILTIN_KINDS
REVIEW = KindSpec(
    kind="review", label="검토", capability_code="review", scope_key="repository_id",
    input_kinds=["diff", "code_change_result"], output_kind="generic_result",
    outcomes=["approved", "changes_requested", "needs_information"], instructions="", builtin=False,
)


def test_diagnosis_template_matches_prd_wording():
    template = criteria_template(DIAGNOSIS)

    assert [c.code for c in template] == ["diagnosis.ready_for_handoff", "diagnosis.verified"]
    assert [c.text for c in template] == ["결과가 ready_for_handoff임", "근거 검증을 통과함"]
    assert all(c.structured for c in template)


def test_code_change_template_matches_prd_wording():
    template = criteria_template(CODE_CHANGE)

    assert [c.code for c in template] == [
        "code_change.verification_passed",
        "code_change.result_preserved",
    ]
    assert [c.text for c in template] == ["등록된 검증 프로필이 결과 커밋에서 통과함", "결과가 보존됨"]
    assert all(c.structured for c in template)


def test_template_returns_fresh_list_each_call():
    first = criteria_template(DIAGNOSIS)
    first.append(Criterion(code="x", text="x", structured=False))

    assert len(criteria_template(DIAGNOSIS)) == 2


def test_merge_appends_user_items_as_unstructured():
    template = criteria_template(CODE_CHANGE)

    merged = merge_criteria(template, ["보고서 합계가 입력 행에서 계산한 값과 같음", "테스트 무력화 없음"])

    assert merged[: len(template)] == template
    assert [c.code for c in merged[len(template) :]] == ["user.1", "user.2"]
    assert [c.text for c in merged[len(template) :]] == [
        "보고서 합계가 입력 행에서 계산한 값과 같음",
        "테스트 무력화 없음",
    ]
    assert all(c.structured is False for c in merged[len(template) :])


def test_merge_skips_blank_user_items_and_keeps_template_unchanged():
    template = criteria_template(DIAGNOSIS)

    merged = merge_criteria(template, ["  ", "", "검토자 확인"])

    assert len(template) == 2
    assert [c.code for c in merged] == ["diagnosis.ready_for_handoff", "diagnosis.verified", "user.1"]
    assert merged[-1].text == "검토자 확인"


def test_user_defined_kind_template_is_single_review_item():
    template = criteria_template(REVIEW)

    assert template == [Criterion(code="outcome_in_spec", text="결과 outcome 이 허용 목록 안 · 사람 검토 승인", structured=False)]
