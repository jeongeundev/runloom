"""업무 종류 등록부 조회·검사 — ARCHITECTURE "업무 종류와 후속 규칙", ADR-0009.

등록부(`Sequence[KindSpec]`)는 항상 인자로 받는다. DB 는 `adapters/` 가, 세션별 조회는 `server/` 가 한다.
검사는 등록된 값의 명시적 비교뿐이며 사유는 사람이 읽는 문장이다 (ADR-0004).
"""

from collections.abc import Sequence

from workflow.contracts.v1 import Capability, KindSpec, SuccessorRule


def kind_for_capability(kinds: Sequence[KindSpec], code: str) -> KindSpec | None:
    """`capability_code` 가 `code` 인 종류. 여럿이면 앞의 것 (등록부가 정렬 책임)."""
    return next((spec for spec in kinds if spec.capability_code == code), None)


def get_kind(kinds: Sequence[KindSpec], kind: str) -> KindSpec | None:
    return next((spec for spec in kinds if spec.kind == kind), None)


def can_auto_complete(spec: KindSpec) -> bool:
    """자동 완료 검증기가 있는 종류만 — 내장 진단의 `response_path_changed` 판정뿐이다."""
    return spec.builtin and spec.output_kind == "diagnosis_result"


def validate_capability(kinds: Sequence[KindSpec], capability: Capability) -> str | None:
    """None 이면 OK. 코드가 어느 종류의 `capability_code` 이고 scope 키가 그 종류의 `scope_key` 인지 본다."""
    spec = kind_for_capability(kinds, capability.code)
    if spec is None:
        return f"모르는 능력 코드 {capability.code}"
    if set(capability.scope) != {spec.scope_key}:
        return f"{capability.code} 의 scope 키는 {spec.scope_key} 여야 합니다"
    return None


def validate_rule(kinds: Sequence[KindSpec], rule: SuccessorRule) -> str | None:
    """None 이면 OK. from·to 가 등록돼 있고 `on_outcomes ⊆ from.outcomes`, `to.input_kinds ⊆ handoff_kinds` 인지 본다."""
    from_spec = get_kind(kinds, rule.from_kind)
    if from_spec is None:
        return f"등록되지 않은 종류 {rule.from_kind}"
    to_spec = get_kind(kinds, rule.to_kind)
    if to_spec is None:
        return f"등록되지 않은 종류 {rule.to_kind}"
    extra = [o for o in rule.on_outcomes if o not in from_spec.outcomes]
    if extra:
        return f"on_outcomes 에 {from_spec.kind} 의 outcome 이 아닌 값이 있습니다: {', '.join(extra)}"
    missing = [k for k in to_spec.input_kinds if k not in rule.handoff_kinds]
    if missing:
        return f"handoff_kinds 에 {to_spec.kind} 의 input_kinds 가 빠졌습니다: {', '.join(missing)}"
    return None
