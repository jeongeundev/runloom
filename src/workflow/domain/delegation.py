"""맡기기 — 에이전트 맡기기 정책·소유자 승인·꺼진 러너 문구. ARCHITECTURE "사람 사이 인계 — phase 17", ADR-0023.

DB·시각을 보지 않는다. 소유자가 없으면(공용) 활성 관리자가 소유자 역할을 한다.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from workflow.domain.team import ATTACH_RUNNER, REMOVE_ANY_RUNNER, MemberFact, can

DELEGATION_POLICIES = ("run", "owner_approval")
OWNER_APPROVAL_CODE = "owner_approval"
OWNER_APPROVAL_PREFIX = "owner_approval:"
APPROVAL_STATES = ("not_needed", "approved", "pending", "declined", "missing")
_NO_REQUESTER = "none"
_ADMIN_NAME = "관리자"
_NOTE_MAX = 80


@dataclass(frozen=True)
class ApprovalFact:
    state: str  # APPROVAL_STATES 중 하나
    reason: str  # 대기 이유 문구


def needs_owner_approval(*, policy: str, requester_id: str | None, owner_id: str | None,
                         admin_ids: frozenset[str]) -> bool:
    """정책 `owner_approval` 이고 맡긴 사람이 소유자(공용이면 활성 관리자)가 아닐 때. 맡긴 사람 모름은 소유자가 아니다."""
    if policy != "owner_approval":
        return False
    if owner_id is not None:
        return requester_id != owner_id
    return requester_id not in admin_ids


def approval_deciders(owner_id: str | None, members: Sequence[MemberFact]) -> tuple[str, ...]:
    """소유자가 활성 멤버면 소유자, 아니면(공용·비활성 소유자) 활성 관리자 전원(`members` 순서)."""
    if any(m.member_id == owner_id and m.active for m in members):
        return (owner_id,)
    return tuple(m.member_id for m in members if m.active and m.role == "admin")


def can_decide_approval(*, member_id: str, role: str, owner_id: str | None) -> bool:
    """승인·거절 — 관리자 또는 소유자. `team.can(role, RESPOND)` 를 먼저 거친 뒤 쓴다."""
    return role == "admin" or member_id == owner_id


def can_set_policy(*, member_id: str, role: str, owner_id: str | None) -> bool:
    """러너 해제와 같은 규칙 — 모든 러너를 다루는 역할이거나 자기 러너."""
    return can(role, REMOVE_ANY_RUNNER) or (can(role, ATTACH_RUNNER) and member_id == owner_id)


def approval_state(*, needed: bool, latest_state: str | None, latest_action: str | None) -> str:
    """범위의 가장 최근 요청(상태·응답 동작)으로 승인 상태를 정한다. 철회(`withdraw`)는 요청이 없던 것과 같다."""
    if not needed:
        return "not_needed"
    if latest_state is None:
        return "missing"
    if latest_state == "open":
        return "pending"
    return {"approve": "approved", "decline": "declined"}.get(latest_action or "", "missing")


def approval_cause_key(agent_id: str, requester_id: str | None, seq: int) -> str:
    """승인 범위(단계·에이전트·맡긴 사람)의 seq 번째 요청 키. 맡긴 사람이 없으면 `none`."""
    if seq < 1:
        raise ValueError(f"seq {seq}")
    return f"{OWNER_APPROVAL_PREFIX}{agent_id}:{requester_id or _NO_REQUESTER}:{seq}"


def parse_approval_cause_key(key: str) -> tuple[str, str | None, int] | None:
    """(agent_id, requester_id, seq). 승인 요청 키가 아니면 None."""
    if not key.startswith(OWNER_APPROVAL_PREFIX):
        return None
    parts = key[len(OWNER_APPROVAL_PREFIX):].rsplit(":", 2)
    if len(parts) != 3 or not parts[0] or not parts[1] or not (parts[2].isascii() and parts[2].isdigit()) or int(parts[2]) < 1:
        return None
    agent_id, requester, seq = parts
    return agent_id, None if requester == _NO_REQUESTER else requester, int(seq)


def approval_question(requester_name: str | None, owner_name: str | None, agent_name: str) -> str:
    """첫 줄 = 업무 이유(`김OO 가 맡김 · 이OO 승인 대기`), 둘째 줄 = 안내."""
    who = f"{requester_name} 가 맡김" if requester_name else "자동으로 맡김"
    return f"{who} · {owner_name or _ADMIN_NAME} 승인 대기\n에이전트 {agent_name} — [승인]하면 곧 시작합니다."


def declined_reason(owner_name: str | None, note: str | None) -> str:
    """`이OO 가 거절`, 메모가 있으면 ` — <첫 줄 80자>`."""
    reason = f"{owner_name or _ADMIN_NAME} 가 거절"
    first = (note or "").strip().split("\n", 1)[0].strip()[:_NOTE_MAX]
    return f"{reason} — {first}" if first else reason


def offline_reason(owner_name: str | None) -> str:
    return f"{owner_name + '의' if owner_name else '공용'} 러너 꺼짐 · 켜지면 시작"


def candidate_label(name: str, owner_name: str | None, online: bool) -> str:
    """패널 담당 후보 한 줄 — `opensql · 이OO의 Mac · 켜짐` / `opensql · 공용 · 꺼짐`."""
    where = f"{owner_name}의 Mac" if owner_name else "공용"
    return f"{name} · {where} · {'켜짐' if online else '꺼짐'}"
