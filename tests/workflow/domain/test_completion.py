"""완료 기준 템플릿·병합·자동 완료 가능 여부 — PRD 4절."""

from workflow.domain.completion import (
    Criterion,
    can_auto_complete,
    criteria_template,
    merge_criteria,
)


def test_diagnosis_template_matches_prd_wording():
    template = criteria_template("diagnosis")

    assert [c.code for c in template] == ["diagnosis.ready_for_handoff", "diagnosis.verified"]
    assert [c.text for c in template] == ["결과가 ready_for_handoff임", "근거 검증을 통과함"]
    assert all(c.structured for c in template)


def test_code_change_template_matches_prd_wording():
    template = criteria_template("code_change")

    assert [c.code for c in template] == [
        "code_change.verification_passed",
        "code_change.result_preserved",
    ]
    assert [c.text for c in template] == ["등록된 검증 프로필이 결과 커밋에서 통과함", "결과가 보존됨"]
    assert all(c.structured for c in template)


def test_template_returns_fresh_list_each_call():
    first = criteria_template("diagnosis")
    first.append(Criterion(code="x", text="x", structured=False))

    assert len(criteria_template("diagnosis")) == 2


def test_merge_appends_user_items_as_unstructured():
    template = criteria_template("code_change")

    merged = merge_criteria(template, ["보고서 합계가 입력 행에서 계산한 값과 같음", "테스트 무력화 없음"])

    assert merged[: len(template)] == template
    assert [c.code for c in merged[len(template) :]] == ["user.1", "user.2"]
    assert [c.text for c in merged[len(template) :]] == [
        "보고서 합계가 입력 행에서 계산한 값과 같음",
        "테스트 무력화 없음",
    ]
    assert all(c.structured is False for c in merged[len(template) :])


def test_merge_skips_blank_user_items_and_keeps_template_unchanged():
    template = criteria_template("diagnosis")

    merged = merge_criteria(template, ["  ", "", "검토자 확인"])

    assert len(template) == 2
    assert [c.code for c in merged] == ["diagnosis.ready_for_handoff", "diagnosis.verified", "user.1"]
    assert merged[-1].text == "검토자 확인"


def test_only_diagnosis_can_auto_complete():
    assert can_auto_complete("diagnosis") is True
    assert can_auto_complete("code_change") is False
