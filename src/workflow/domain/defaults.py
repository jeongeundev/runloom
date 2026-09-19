"""업무 등록 기본값 — PRD 2절 "기본값 — 2026-09-20 사용자 확정"."""

from typing import Literal

_KIND_BY_CODE: dict[str, Literal["diagnosis", "code_change"]] = {
    "operations.diagnose": "diagnosis",
    "code.modify": "code_change",
}


def default_run_mode(has_predecessor: bool) -> Literal["manual", "auto"]:
    # 착수 대기는 선행 완료 뒤 후속 착수에서 생기므로 후속 업무만 자동이다.
    return "auto" if has_predecessor else "manual"


def default_selection_mode() -> Literal["auto"]:
    return "auto"


def default_completion_mode(kind: str) -> Literal["review"]:
    # 항상 검토 후 완료. 시연 예시 A 의 자동 완료는 폼 미리 채움에서만 바꾼다.
    return "review"


def kind_for_capability(code: str) -> Literal["diagnosis", "code_change"]:
    return _KIND_BY_CODE[code]
