"""완료 기준 템플릿과 자동 완료 가능 여부 — PRD 4절, ADR-0004.

템플릿은 업무 종류별 고정 문구다. 사용자가 추가한 자유 텍스트는 자동 판정에
쓰지 않고 검토자에게만 표시한다 (`structured=False`).
"""

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class Criterion:
    code: str  # 예: "diagnosis.verified", "user.1"
    text: str  # 화면 문구
    structured: bool  # True 면 자동 판정에 사용, False 면 검토자에게만 표시


_TEMPLATES: dict[str, tuple[Criterion, ...]] = {
    "diagnosis": (
        Criterion("diagnosis.ready_for_handoff", "결과가 ready_for_handoff임", True),
        Criterion("diagnosis.verified", "근거 검증을 통과함", True),
    ),
    "code_change": (
        Criterion("code_change.verification_passed", "등록된 검증 프로필이 결과 커밋에서 통과함", True),
        Criterion("code_change.result_preserved", "결과가 보존됨", True),
    ),
}


def criteria_template(kind: Literal["diagnosis", "code_change"]) -> list[Criterion]:
    return list(_TEMPLATES[kind])


def merge_criteria(template: list[Criterion], user_items: list[str]) -> list[Criterion]:
    texts = [item.strip() for item in user_items if item.strip()]
    return [*template, *(Criterion(f"user.{n}", text, False) for n, text in enumerate(texts, 1))]


def can_auto_complete(kind: str) -> bool:
    # 자동 판정기가 있는 종류만. 첫 구현은 진단의 response_path_changed 판정뿐이다.
    return kind == "diagnosis"
