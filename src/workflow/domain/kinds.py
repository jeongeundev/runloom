"""업무 종류 등록부 조회·검사 — ARCHITECTURE "업무 종류와 후속 규칙", ADR-0009.

등록부(`Sequence[KindSpec]`)는 항상 인자로 받는다. DB 는 `adapters/` 가, 세션별 조회는 `server/` 가 한다.
검사는 등록된 값의 명시적 비교뿐이며 사유는 사람이 읽는 문장이다 (ADR-0004).
"""

from collections.abc import Sequence

from workflow.contracts.v1 import BUILTIN_KINDS, Capability, KindSpec, SuccessorRule
from workflow.domain.execution_policy import is_triage_kind

BUILTIN_CAPABILITY_CODES: frozenset[str] = frozenset(spec.capability_code for spec in BUILTIN_KINDS)
SCOPE_NO_REPOSITORY = "no_repository"
SCOPE_MANY_REPOSITORIES = "many_repositories"


def kind_for_capability(kinds: Sequence[KindSpec], code: str) -> KindSpec | None:
    """`capability_code` 가 `code` 인 종류. 여럿이면 앞의 것 (등록부가 정렬 책임)."""
    return next((spec for spec in kinds if spec.capability_code == code), None)


def get_kind(kinds: Sequence[KindSpec], kind: str) -> KindSpec | None:
    return next((spec for spec in kinds if spec.kind == kind), None)


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
    if is_triage_kind(from_spec) or is_triage_kind(to_spec):
        return "판단 종류는 후속 규칙에 쓸 수 없습니다"
    extra = [o for o in rule.on_outcomes if o not in from_spec.outcomes]
    if extra:
        return f"on_outcomes 에 {from_spec.kind} 의 outcome 이 아닌 값이 있습니다: {', '.join(extra)}"
    missing = [k for k in to_spec.input_kinds if k not in rule.handoff_kinds]
    if missing:
        return f"handoff_kinds 에 {to_spec.kind} 의 input_kinds 가 빠졌습니다: {', '.join(missing)}"
    return None


def agent_repository_scope(capabilities: Sequence[Capability]) -> tuple[str | None, str | None]:
    """에이전트의 범위 값 — 내장 능력 scope 의 `repository_id`. `(값, None)` 또는 `(None, 이유 코드)`.
    사용자 정의 능력의 scope 는 보지 않는다(붙인 능력이 범위를 바꾸지 않게)."""
    values = {c.scope["repository_id"] for c in capabilities
              if c.code in BUILTIN_CAPABILITY_CODES and "repository_id" in c.scope}
    if not values:
        return None, SCOPE_NO_REPOSITORY
    if len(values) > 1:
        return None, SCOPE_MANY_REPOSITORIES
    return next(iter(values)), None
