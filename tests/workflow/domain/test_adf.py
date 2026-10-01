"""ADF ↔ 텍스트 — ARCHITECTURE "Jira 소스 — phase 18" 가져오기·후속 이슈 등록, ADR-0024 결정 5·14."""

from workflow.domain.adf import adf_to_text, markdown_to_adf


def _doc(*content) -> dict:
    return {"type": "doc", "version": 1, "content": list(content)}


def _p(*inline) -> dict:
    return {"type": "paragraph", "content": list(inline)}


def _t(text: str, *marks) -> dict:
    node = {"type": "text", "text": text}
    if marks:
        node["marks"] = list(marks)
    return node


def _link(href: str) -> dict:
    return {"type": "link", "attrs": {"href": href}}


def _li(*content) -> dict:
    return {"type": "listItem", "content": list(content)}


# --- ADF → 텍스트 ---

def test_research_shape_to_text():
    """조사 문서의 모양(루트 doc·블록·인라인 text + marks)."""
    doc = _doc(
        {"type": "heading", "attrs": {"level": 2}, "content": [_t("재현 절차")]},
        {"type": "orderedList", "content": [_li(_p(_t("쿠폰 입력"))), _li(_p(_t("결제")))]},
        {"type": "heading", "attrs": {"level": 2}, "content": [_t("기대 동작")]},
        _p(_t("할인이 "), _t("적용", {"type": "strong"}), _t("된다."), {"type": "hardBreak"}, _t("둘째 줄")),
        {"type": "bulletList", "content": [_li(_p(_t("문서", _link("https://example.com/doc"))))]},
        {"type": "codeBlock", "attrs": {"language": "python"}, "content": [_t("print(1)\nprint(2)")]},
    )
    assert adf_to_text(doc) == (
        "## 재현 절차\n\n"
        "1. 쿠폰 입력\n2. 결제\n\n"
        "## 기대 동작\n\n"
        "할인이 적용된다.\n둘째 줄\n\n"
        "- 문서 (https://example.com/doc)\n\n"
        "```python\nprint(1)\nprint(2)\n```"
    )


def test_heading_levels_and_form_sections_read_it():
    from workflow.domain.form_sections import extract_form

    doc = _doc(
        {"type": "heading", "attrs": {"level": 1}, "content": [_t("제목")]},
        {"type": "heading", "attrs": {"level": 3}, "content": [_t("인수 조건")]},
        _p(_t("결제 성공")),
    )
    text = adf_to_text(doc)
    assert text == "# 제목\n\n### 인수 조건\n\n결제 성공"
    assert extract_form(text).fields["acceptance_criteria"].value == "결제 성공"


def test_link_same_as_text_is_not_repeated():
    assert adf_to_text(_doc(_p(_t("https://a.io", _link("https://a.io"))))) == "https://a.io"


def test_mention_and_ordered_start():
    doc = _doc(
        _p({"type": "mention", "attrs": {"id": "1", "text": "@민지"}}, _t(" 님"),
           {"type": "mention", "attrs": {"id": "2", "text": "철수"}}),
        {"type": "orderedList", "attrs": {"order": 3}, "content": [_li(_p(_t("셋"))), _li(_p(_t("넷")))]},
    )
    assert adf_to_text(doc) == "@민지 님@철수\n\n3. 셋\n4. 넷"


def test_nested_list_is_indented():
    doc = _doc({"type": "bulletList", "content": [
        _li(_p(_t("위")), {"type": "bulletList", "content": [_li(_p(_t("아래")))]}),
    ]})
    assert adf_to_text(doc) == "- 위\n  - 아래"


def test_unknown_nodes_keep_inner_text():
    doc = _doc(
        {"type": "panel", "attrs": {"panelType": "info"}, "content": [_p(_t("안내")), _p(_t("둘"))]},
        _p({"type": "emoji", "attrs": {"shortName": ":smile:"}}, _t("끝")),
        {"type": "table", "content": [{"type": "tableRow", "content": [
            {"type": "tableCell", "content": [_p(_t("칸"))]},
        ]}]},
    )
    assert adf_to_text(doc) == "안내\n\n둘\n\n:smile:끝\n\n칸"


def test_bad_input_gives_empty_text():
    assert adf_to_text(None) == ""
    assert adf_to_text({}) == ""
    assert adf_to_text("문자열") == ""  # type: ignore[arg-type]
    assert adf_to_text({"type": "doc", "content": "x"}) == ""
    assert adf_to_text({"type": "doc", "content": [None, 3, {"type": "text", "text": 5}]}) == ""
    assert adf_to_text(_doc(_p(_t("ok")), {"type": "heading", "attrs": {"level": "x"}, "content": [_t("h")]})) == \
        "ok\n\n# h"


def test_depth_is_limited():
    node: dict = _t("깊은 글")
    for _ in range(500):
        node = {"type": "blockquote", "content": [node]}
    assert adf_to_text(_doc(node)) == ""  # 예외 없이 잘림
    shallow = _doc({"type": "blockquote", "content": [_p(_t("얕은 글"))]})
    assert adf_to_text(shallow) == "얕은 글"


# --- Markdown → ADF ---

def test_markdown_paragraphs_list_code_link():
    text = (
        "Runloom 후속 업무 RUN-13 — SHOP-12 의 결과로 생겼습니다.\n\n"
        "- 하나\n- [문서](https://example.com/d)\n\n"
        "```\nx = 1\n```\n\n"
        "Runloom 업무: https://runloom.example/work/RUN-13"
    )
    assert markdown_to_adf(text) == _doc(
        _p(_t("Runloom 후속 업무 RUN-13 — SHOP-12 의 결과로 생겼습니다.")),
        {"type": "bulletList", "content": [_li(_p(_t("하나"))), _li(_p(_t("문서", _link("https://example.com/d"))))]},
        {"type": "codeBlock", "content": [_t("x = 1")]},
        _p(_t("Runloom 업무: "), _t("https://runloom.example/work/RUN-13", _link("https://runloom.example/work/RUN-13"))),
    )


def test_markdown_lines_in_paragraph_become_hard_breaks():
    assert markdown_to_adf("첫 줄\n둘째 줄") == _doc(_p(_t("첫 줄"), {"type": "hardBreak"}, _t("둘째 줄")))


def test_markdown_other_schemes_stay_text():
    assert markdown_to_adf("[x](javascript:alert(1)) ftp://a") == _doc(_p(_t("[x](javascript:alert(1)) ftp://a")))


def test_markdown_bare_url_trailing_punctuation():
    assert markdown_to_adf("보기: https://a.io/x.") == _doc(
        _p(_t("보기: "), _t("https://a.io/x", _link("https://a.io/x")), _t(".")))


def test_markdown_code_language_and_unclosed_fence():
    assert markdown_to_adf("```python\na\n\nb") == _doc(
        {"type": "codeBlock", "attrs": {"language": "python"}, "content": [_t("a\n\nb")]})
    assert markdown_to_adf("```\n```") == _doc({"type": "codeBlock", "content": []})


def test_markdown_bad_input_gives_empty_doc():
    assert markdown_to_adf("") == _doc()
    assert markdown_to_adf("   \n\n") == _doc()
    assert markdown_to_adf(None) == _doc()  # type: ignore[arg-type]


def test_round_trip_minimal():
    for text in (
        "문단 하나",
        "문단 하나\n\n문단 둘",
        "- 가\n- 나",
        "```\ncode\n```",
        "링크 https://a.io/x 끝",
        "첫 줄\n둘째 줄",
    ):
        assert adf_to_text(markdown_to_adf(text)) == text
