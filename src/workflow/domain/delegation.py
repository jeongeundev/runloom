"""맡기기 — 에이전트 맡기기 정책·소유자 승인 요청의 원인 키. ARCHITECTURE "사람 사이 인계 — phase 17", ADR-0023.

DB·시각을 보지 않는다.
"""

DELEGATION_POLICIES = ("run", "owner_approval")
OWNER_APPROVAL_CODE = "owner_approval"
OWNER_APPROVAL_PREFIX = "owner_approval:"
_NO_REQUESTER = "none"


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
