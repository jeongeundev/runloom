"""자동·직접 선택 규칙 — PRD 2절 "첫 구현의 판단 규칙", ADR-0004.

능력 코드·범위의 명시적 비교만 한다. 점수·우선순위·최근 사용·모델 판단으로
후보를 줄이지 않는다. 결과는 CONTRACT 9절 형식의 `SelectionRecord` 다.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from workflow.contracts.v1 import Capability, SelectionRecord


@dataclass(frozen=True)
class Candidate:
    agent_id: str
    capabilities: tuple[Capability, ...]
    allowed: bool = True  # 세션에 사용 허용된 에이전트인지. False 면 후보에서 제외


def _matched_capability(required: Capability, candidate: Candidate) -> Capability | None:
    # 일치 = code 가 같고 scope 의 모든 키·값이 같다. 부분 일치 없음.
    for capability in candidate.capabilities:
        if capability.code == required.code and capability.scope == required.scope:
            return capability
    return None


def _match_reason(matched: Capability) -> str:
    scope = " · ".join(f"{key}={value}" for key, value in sorted(matched.scope.items()))
    return f"{matched.code} · {scope} 일치 후보 1개"


def select_agent(
    task_id: str,
    required: Capability,
    candidates: Sequence[Candidate],
    mode: Literal["auto", "manual"] = "auto",
    chosen_agent_id: str | None = None,
) -> SelectionRecord:
    """허용된 후보 중 요구 능력과 일치하는 Agent 를 고른다.

    직접 선택(`manual`)에서는 지정한 에이전트만 후보로 세므로 `candidate_count` 는
    선택되면 1, 아니면 0 이다.
    """
    allowed = [c for c in candidates if c.allowed]

    if mode == "auto":
        matches = [(c, m) for c in allowed if (m := _matched_capability(required, c)) is not None]
        if len(matches) == 1:
            candidate, matched = matches[0]
            return SelectionRecord(
                task_id=task_id,
                mode="auto",
                required_capability=required,
                candidate_count=1,
                selected_agent_id=candidate.agent_id,
                matched=matched,
                status="selected",
                reason=_match_reason(matched),
            )
        return SelectionRecord(
            task_id=task_id,
            mode="auto",
            required_capability=required,
            candidate_count=len(matches),
            selected_agent_id=None,
            matched=None,
            status="needs_selection",
            reason="후보 없음" if not matches else f"후보 {len(matches)}개 — 선택 필요",
        )

    if chosen_agent_id is None:
        raise ValueError("manual 선택은 chosen_agent_id 가 필요합니다")
    chosen = next((c for c in allowed if c.agent_id == chosen_agent_id), None)
    matched = _matched_capability(required, chosen) if chosen is not None else None
    if chosen is not None and matched is not None:
        return SelectionRecord(
            task_id=task_id,
            mode="manual",
            required_capability=required,
            candidate_count=1,
            selected_agent_id=chosen.agent_id,
            matched=matched,
            status="selected",
            reason="직접 선택",
        )
    return SelectionRecord(
        task_id=task_id,
        mode="manual",
        required_capability=required,
        candidate_count=0,
        selected_agent_id=None,
        matched=None,
        status="needs_selection",
        reason=(
            "선택한 에이전트는 사용 허용되지 않음"
            if chosen is None
            else f"선택한 에이전트에 {required.code} 능력 없음"
        ),
    )
