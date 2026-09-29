"""실행 정책 표 — ADR-0014 결정 3, ARCHITECTURE "인터페이스 — 이름·소유·책임" 의 `ExecutionPolicy`.

워커가 종류 이름으로 `if` 를 늘리지 않고 이 표를 조회한다: 어떤 결과 봉투를 어떤 판정기로 볼지, 필수 산출물이 무엇인지,
GitHub 업무 순환(준비 판정으로 착수, `decide_followup` 으로 후속)을 따르는지. 내장 종류만 코드 판정기를 가지고
사용자 정의 종류는 모두 `GENERIC_POLICY` 다(ADR-0009).
"""

from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal

Target = Literal["code_change", "commit_review", "local"]
# code_change: 버그 수정 결과(요청 ID·기준 커밋 대조). 진단·보고서 데모 판정기는 `main` 전용 (ADR-0019)
Verifier = Literal["code_change", "commit_review", "generic"]


@dataclass(frozen=True)
class ExecutionPolicy:
    kind: str
    target: Target  # 실행 요청 target 모양
    result_kind: str  # 결과 봉투 산출물 kind
    required_artifacts: tuple[str, ...]  # 판정 통과에 필요한 산출물 kind
    verifier: Verifier
    cycle: bool  # 준비 판정으로 착수하고 결과 뒤 `decide_followup` 으로 후속을 정한다

    @property
    def starts_from_result(self) -> bool:
        """자기 요청이 아니라 다른 실행의 결과(검토할 커밋)에서 시작한다 — 준비 판정만으로 착수하지 않는다."""
        return self.target == "commit_review"


_CODE_ARTIFACTS = ("diff", "test_log_before", "test_log_after", "verification_log")

BUILTIN_POLICIES = MappingProxyType({
    "bug_fix": ExecutionPolicy("bug_fix", "code_change", "code_change_result", _CODE_ARTIFACTS, "code_change", cycle=True),
    "code_review": ExecutionPolicy(
        "code_review", "commit_review", "code_review_result", (), "commit_review", cycle=True,
    ),
})

GENERIC_POLICY = ExecutionPolicy("*", "local", "generic_result", (), "generic", cycle=False)


def policy_for(kind: str) -> ExecutionPolicy:
    return BUILTIN_POLICIES.get(kind, GENERIC_POLICY)
