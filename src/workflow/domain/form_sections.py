"""업무 양식 칸 — 이슈 본문의 `##`·`###` 절(GitHub issue forms 결과)에서 목표·재현 절차·기대 동작·인수 조건을 읽는다
(ADR-0020, ARCHITECTURE "양식 칸").

양식은 참고 정보다 — 실행 요청은 본문 그대로다. 해석하지 못하면 빈 양식이고 예외를 내지 않는다.
제목 줄은 `#` 1~3 개 + 공백으로 시작하는 줄이다. `#` 은 절을 끝내기만 하고, `##`·`###` 은 새 절을 연다.
코드 블록 안의 `#` 줄도 제목으로 본다(단순함 우선 — 알려진 한계).
"""

import re
from dataclasses import dataclass

# 칸 키 → 제목 동의어. 비교는 앞뒤 공백·끝 `:` 를 빼고 casefold 한 값끼리 정확히.
FORM_HEADINGS: dict[str, tuple[str, ...]] = {
    "goal": ("목표", "목적", "요약", "Goal", "Objective", "Summary"),
    "steps_to_reproduce": ("재현 절차", "재현 방법", "재현 단계", "Steps to reproduce", "Reproduction steps",
                           "To reproduce"),
    "expected_behavior": ("기대 동작", "기대 결과", "예상 동작", "Expected behavior", "Expected behaviour",
                          "Expected result"),
    "acceptance_criteria": ("인수 조건", "완료 조건", "수용 기준", "Acceptance criteria", "Definition of done"),
}
NO_RESPONSE = "_No response_"  # GitHub issue forms 의 빈 답

_HEADING = re.compile(r"^(#{1,3})\s+(.*)$")


def _normalize(text: str) -> str:
    return " ".join(text.strip().removesuffix(":").split()).casefold()


_FIELD_OF = {_normalize(s): key for key, synonyms in FORM_HEADINGS.items() for s in synonyms}


@dataclass(frozen=True)
class FormField:
    value: str
    source: str  # `github_body:<원래 제목 줄>`


@dataclass(frozen=True)
class WorkForm:
    fields: dict[str, FormField]

    def to_json(self) -> dict:
        """`work_items.form_json` 모양 — 찾은 칸만."""
        return {key: {"value": f.value, "source": f.source} for key, f in self.fields.items()}


def extract_form(body: str) -> WorkForm:
    fields: dict[str, FormField] = {}
    current: tuple[str, str] | None = None  # (칸 키, 제목 줄)
    lines: list[str] = []

    def close() -> None:
        if current is None:
            return
        value = "\n".join(lines).strip()
        if value and value != NO_RESPONSE and current[0] not in fields:
            fields[current[0]] = FormField(value, f"github_body:{current[1]}")

    for line in body.splitlines():
        match = _HEADING.match(line)
        if match is None:
            lines.append(line)
            continue
        close()
        key = _FIELD_OF.get(_normalize(match.group(2))) if len(match.group(1)) >= 2 else None
        current = (key, line) if key is not None else None
        lines = []
    close()
    return WorkForm(fields=fields)
