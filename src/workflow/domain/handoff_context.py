"""인계 맥락 요청문 — 업무 키·제목·양식 칸·맡긴 사람 지시를 원래 요청문 앞에 붙인다 (ARCHITECTURE "인계 맥락 요청문").

순수 함수다. 메모·양식·제목은 외부 입력이지만 문자열로만 이어 붙인다 — 명령·경로로 해석하지 않는다.
"""

from collections.abc import Sequence

from workflow.domain.form_sections import NO_RESPONSE

HANDOFF_NOTE_MAX = 2000  # 지시 메모 최대 글자 수(넘으면 폼이 422)
FORM_VALUE_MAX = 4000  # 양식 칸 값 하나의 최대 글자 수(넘으면 자른다)
_CUT = "…(생략)"


def compose_request(*, work_key: str | None, title: str, form_fields: Sequence[tuple[str, str]], note: str | None,
                    note_by: str | None, body: str, origin_key: str | None = None) -> str:
    """`# <키> <제목>` → `## 업무 양식` → `## 맡긴 사람 지시 (<이름>)` → 원래 요청문. 빈 절은 통째로 빼고 절 사이는 빈 줄 하나.
    `origin_key`(Jira 원본 키)가 있으면 머리 바로 아래 줄 `원본: <키>`. `work_key` 가 None(업무 없는 단계)이면 `body` 그대로."""
    if work_key is None:
        return body
    sections = [f"# {work_key} {title}" + (f"\n원본: {origin_key}" if origin_key else "")]
    fields = [(label, value.strip()) for label, value in form_fields]
    fields = [(label, value) for label, value in fields if value and value != NO_RESPONSE]
    if fields:
        blocks = [f"### {label}\n{value[:FORM_VALUE_MAX] + _CUT if len(value) > FORM_VALUE_MAX else value}"
                  for label, value in fields]
        sections.append("## 업무 양식\n\n" + "\n\n".join(blocks))
    if note:
        sections.append(f"## 맡긴 사람 지시 ({note_by or '이름 없음'})\n{note}")
    if body.strip():
        sections.append(body.strip())
    return "\n\n".join(sections)
