"""완료 기준 템플릿 — PRD 4절, ADR-0004, ADR-0009.

템플릿은 업무 종류별 고정 문구다. 내장 종류는 검증기 항목, 사용자 정의 종류는 사람 검토 항목 하나다.
사용자가 추가한 자유 텍스트는 자동 판정에 쓰지 않고 검토자에게만 표시한다 (`structured=False`).
자동 완료 가능 여부는 `domain/kinds.can_auto_complete`.
"""

from dataclasses import dataclass

from workflow.contracts.v1 import KindSpec


@dataclass(frozen=True)
class Criterion:
    code: str  # 예: "diagnosis.verified", "user.1"
    text: str  # 화면 문구
    structured: bool  # True 면 자동 판정에 사용, False 면 검토자에게만 표시


# 내장 종류의 템플릿. 사용자 정의 종류는 검증기가 없어 사람 검토 항목 하나뿐이다.
_TEMPLATES: dict[str, tuple[Criterion, ...]] = {
    "diagnosis": (
        Criterion("diagnosis.ready_for_handoff", "결과가 ready_for_handoff임", True),
        Criterion("diagnosis.verified", "근거 검증을 통과함", True),
    ),
    "code_change": (
        Criterion("code_change.verification_passed", "등록된 검증 프로필이 결과 커밋에서 통과함", True),
        Criterion("code_change.result_preserved", "결과가 보존됨", True),
    ),
    "bug_fix": (
        Criterion("bug_fix.test_before_failed", "재현 테스트가 수정 전에 실패함", True),
        Criterion("bug_fix.verification_passed", "등록된 검증 프로필이 결과 커밋에서 통과함", True),
        Criterion("bug_fix.result_preserved", "결과가 보존됨", True),
    ),
    "code_review": (
        Criterion("code_review.commit_matches", "검토한 커밋이 최신 수정 결과 커밋과 같음", True),
        Criterion("code_review.result_preserved", "검토 결과가 보존됨", True),
    ),
}
_USER_DEFINED = Criterion("outcome_in_spec", "결과 outcome 이 허용 목록 안 · 사람 검토 승인", False)


def criteria_template(spec: KindSpec) -> list[Criterion]:
    return list(_TEMPLATES[spec.kind]) if spec.builtin else [_USER_DEFINED]


def merge_criteria(template: list[Criterion], user_items: list[str]) -> list[Criterion]:
    texts = [item.strip() for item in user_items if item.strip()]
    return [*template, *(Criterion(f"user.{n}", text, False) for n, text in enumerate(texts, 1))]
