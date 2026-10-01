"""form_sections — 이슈 본문의 `##`·`###` 절에서 업무 양식 칸을 읽는다 (phase 14 step 5, ARCHITECTURE "양식 칸")."""

import pytest

from workflow.domain.form_sections import FORM_HEADINGS, FORM_LABELS, FormField, WorkForm, extract_form

ISSUE_FORM_BODY = """### 목표

쿠폰은 한 번만 적용된다

### 재현 절차

1. 쿠폰 적용
2. 새로고침

### 기대 동작

_No response_

### Acceptance criteria

- 중복 적용 테스트 통과
"""


def test_issue_form_sections_fill_fields_with_source_heading():
    form = extract_form(ISSUE_FORM_BODY)
    assert form.fields == {
        "goal": FormField("쿠폰은 한 번만 적용된다", "github_body:### 목표"),
        "steps_to_reproduce": FormField("1. 쿠폰 적용\n2. 새로고침", "github_body:### 재현 절차"),
        "acceptance_criteria": FormField("- 중복 적용 테스트 통과", "github_body:### Acceptance criteria"),
    }  # `_No response_`(issue forms 빈 답)는 없는 칸
    assert form.to_json() == {
        "goal": {"value": "쿠폰은 한 번만 적용된다", "source": "github_body:### 목표"},
        "steps_to_reproduce": {"value": "1. 쿠폰 적용\n2. 새로고침", "source": "github_body:### 재현 절차"},
        "acceptance_criteria": {"value": "- 중복 적용 테스트 통과", "source": "github_body:### Acceptance criteria"},
    }


@pytest.mark.parametrize("heading", ["## Steps to reproduce", "###   STEPS TO REPRODUCE:  ", "## 재현 방법:",
                                     "### To Reproduce"])
def test_heading_matching_ignores_case_spaces_and_trailing_colon(heading):
    form = extract_form(f"{heading}\n  클릭  \n")
    assert form.fields["steps_to_reproduce"] == FormField("클릭", f"github_body:{heading}")


def test_english_synonyms_map_to_each_field():
    body = "## Summary\n요약\n## Expected behaviour\n기대\n## Definition of done\n완료\n"
    assert {k: f.value for k, f in extract_form(body).fields.items()} == {
        "goal": "요약", "expected_behavior": "기대", "acceptance_criteria": "완료",
    }


def test_first_section_wins_and_next_heading_of_any_level_ends_a_section():
    body = "### 목표\n첫 목표\n# 다른 절\n본문\n### 목적\n두 번째 목표\n#### 소제목은 내용이다\n"
    form = extract_form(body)
    assert form.fields == {"goal": FormField("첫 목표", "github_body:### 목표")}


def test_level_four_heading_stays_inside_section():
    form = extract_form("### 기대 결과\n#### 세부\n값\n")
    assert form.fields["expected_behavior"].value == "#### 세부\n값"


@pytest.mark.parametrize("body", ["", "그냥 본문입니다", "### 모르는 제목\n내용", "### 목표\n\n   \n", "###목표\n붙은 제목"])
def test_unparsable_or_empty_body_gives_empty_form(body):
    form = extract_form(body)
    assert form == WorkForm(fields={}) and form.to_json() == {}


def test_every_field_has_korean_and_english_synonyms():
    assert set(FORM_HEADINGS) == {"goal", "steps_to_reproduce", "expected_behavior", "acceptance_criteria"}
    for synonyms in FORM_HEADINGS.values():
        assert any(s.isascii() for s in synonyms) and any(not s.isascii() for s in synonyms)


def test_every_form_field_has_a_label():
    assert list(FORM_LABELS) == list(FORM_HEADINGS)
