"""요청문 조립 — phase 17 step 8 (ARCHITECTURE "인계 맥락 요청문")."""

from workflow.domain.form_sections import NO_RESPONSE
from workflow.domain.handoff_context import FORM_VALUE_MAX, HANDOFF_NOTE_MAX, compose_request


def compose(**overrides) -> str:
    args = {"work_key": "RUN-3", "title": "주문 합계 반올림", "form_fields": (), "note": None, "note_by": None,
            "body": "합계가 1원 틀립니다."}
    return compose_request(**{**args, **overrides})


def test_constants():
    assert (HANDOFF_NOTE_MAX, FORM_VALUE_MAX) == (2000, 4000)


def test_all_sections_in_order():
    text = compose(
        form_fields=(("목표", "합계를 맞춘다"), ("재현 절차", "1. 담기\n2. 결제")),
        note="결제 모듈만 보세요", note_by="김맡김",
        body="합계가 1원 틀립니다.\n\n## 사람 응답 (운영자)\n- 질문: 어디?\n  답: 장바구니",
    )
    assert text == (
        "# RUN-3 주문 합계 반올림\n\n"
        "## 업무 양식\n\n### 목표\n합계를 맞춘다\n\n### 재현 절차\n1. 담기\n2. 결제\n\n"
        "## 맡긴 사람 지시 (김맡김)\n결제 모듈만 보세요\n\n"
        "합계가 1원 틀립니다.\n\n## 사람 응답 (운영자)\n- 질문: 어디?\n  답: 장바구니"
    )


def test_empty_sections_are_left_out():
    assert compose() == "# RUN-3 주문 합계 반올림\n\n합계가 1원 틀립니다."
    assert compose(body="  ") == "# RUN-3 주문 합계 반올림"
    assert compose(form_fields=(("목표", ""), ("기대 동작", NO_RESPONSE), ("인수 조건", "  "))) == (
        "# RUN-3 주문 합계 반올림\n\n합계가 1원 틀립니다.")
    assert compose(note="") == compose(note=None)


def test_note_without_a_name():
    assert "## 맡긴 사람 지시 (이름 없음)\n먼저 테스트" in compose(note="먼저 테스트")


def test_without_a_work_key_only_the_body():
    body = "원문 그대로\n"
    assert compose(work_key=None, form_fields=(("목표", "x"),), note="메모", note_by="김", body=body) == body


def test_long_form_value_is_cut():
    text = compose(form_fields=(("목표", "가" * (FORM_VALUE_MAX + 10)),))
    assert "### 목표\n" + "가" * FORM_VALUE_MAX + "…(생략)\n" in text
    assert "가" * (FORM_VALUE_MAX + 1) not in text
    exact = compose(form_fields=(("목표", "가" * FORM_VALUE_MAX),))
    assert "…(생략)" not in exact
