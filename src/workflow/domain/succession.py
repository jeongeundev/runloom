"""후속 규칙 판단 — ARCHITECTURE "워커 후속 스캔과 인계 조립", ADR-0009.

규칙 표(`Sequence[SuccessorRule]`)는 인자로 받는다. 착수 조건(선행 결과 + 판정 통과 + outcome 일치) 중
outcome 일치만 여기서 판단하고, 결과·판정 조회와 실제 착수는 워커가 한다. 규칙 일치는 등록된 값의
명시적 비교이며 모든 판단에 사람이 읽는 이유 문장이 붙는다 (ADR-0004).
"""

from collections.abc import Sequence

from workflow.contracts.v1 import SuccessorRule


def rule_for(rules: Sequence[SuccessorRule], from_kind: str, to_kind: str) -> SuccessorRule | None:
    return next((r for r in rules if r.from_kind == from_kind and r.to_kind == to_kind), None)


def may_continue(rule: SuccessorRule, outcome: str) -> bool:
    return outcome in rule.on_outcomes


def continue_reason(rule: SuccessorRule, outcome: str) -> str:
    if may_continue(rule, outcome):
        return f"선행 outcome {outcome} — 규칙 {rule.from_kind} → {rule.to_kind} 로 착수"
    return f"선행 outcome {outcome} 은 규칙 대상 아님 — 확인 필요"
