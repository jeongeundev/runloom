"""워크플로우 자동 구성 — 가져온 이슈의 순서 배치와 Agent 배정 (심사자 흐름 3단계).

규칙 기반이며 LLM 을 쓰지 않는다 (ADR-0004). 그래서 모든 결정에 사람이 읽는 이유 문장이
붙는다. 순서는 `blocked_by` 위상 정렬, 체인은 `operations.diagnose → code.modify` 인접 쌍만
(인계 자료가 정의된 유일한 쌍), 방식은 `defaults`·`completion` 의 기본값, 배정은 `select_agent`.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from workflow.contracts.v1 import Capability, SelectionRecord
from workflow.domain.completion import Criterion, can_auto_complete, criteria_template
from workflow.domain.defaults import default_run_mode, kind_for_capability
from workflow.domain.selection import Candidate, select_agent
from workflow.domain.task_sources import Issue, IssueMapping, map_issue

Kind = Literal["diagnosis", "code_change"]

# 인계 자료가 정의된 선행 → 후속 능력 쌍. 이 phase 는 진단 → 코드 수정 하나뿐이다.
_HANDOFF_PAIRS: frozenset[tuple[str, str]] = frozenset({("operations.diagnose", "code.modify")})
_UNSUPPORTED_PAIR_REASON = "진단 → 코드 수정 인계만 지원"


@dataclass(frozen=True)
class PlanNode:
    issue: Issue
    capability: Capability
    kind: Kind
    run_id: str | None
    predecessor_key: str | None  # 체인 안의 바로 앞 노드 key
    run_mode: Literal["manual", "auto"]
    completion_mode: Literal["auto", "review"]
    criteria: tuple[Criterion, ...]
    # task_id 는 아직 없으므로 issue.key 를 임시 task_id 로 넣는다 (server 가 삽입 시 다시 계산한다)
    selection: SelectionRecord
    reasons: tuple[str, ...]  # 매핑·순서·체인·방식·배정의 이유 문장들 (규칙 순서대로)


@dataclass(frozen=True)
class Standalone:
    issue: Issue
    mapping: IssueMapping  # capability None 이거나, 있어도 체인에 못 들어간 경우
    reason: str


@dataclass(frozen=True)
class ChainPlan:
    nodes: tuple[PlanNode, ...]  # 실행 순서
    standalone: tuple[Standalone, ...]  # 입력 순서
    human_gate: str  # 마지막 노드 뒤 사람 단계 문구
    title: str  # 첫·끝 노드 제목에서


def _order(capable: Sequence[Issue]) -> list[Issue]:
    """`blocked_by` 위상 정렬. 같은 층위는 입력 순서를 유지한다. 순환이면 ValueError."""
    keys = {issue.key for issue in capable}
    remaining = list(capable)
    placed: list[Issue] = []
    placed_keys: set[str] = set()
    while remaining:
        ready = next(
            (i for i in remaining if all(k in placed_keys for k in i.blocked_by if k in keys)),
            None,
        )
        if ready is None:
            raise ValueError("의존 순환: " + ", ".join(i.key for i in remaining))
        remaining.remove(ready)
        placed.append(ready)
        placed_keys.add(ready.key)
    return placed


def _order_reasons(issue: Issue, capable_keys: set[str], all_keys: set[str]) -> list[str]:
    reasons: list[str] = []
    kept = [k for k in issue.blocked_by if k in capable_keys]
    if kept:
        reasons.append(f"blocked_by {', '.join(kept)} — {', '.join(kept)} 뒤에 배치")
    else:
        reasons.append("blocked_by 없음 — 가져온 순서대로 배치")
    for key in issue.blocked_by:
        if key in capable_keys:
            continue
        if key in all_keys:
            reasons.append(f"선행 {key} 은 이 제품의 에이전트가 맡지 않아 건너뜀")
        else:
            reasons.append(f"선행 {key} 은 가져온 이슈에 없어 건너뜀")
    return reasons


def _mode_reasons(has_predecessor: bool, completion_mode: str) -> list[str]:
    run = (
        "자동 실행 — 선행 완료 후 별도 조작 없이 착수"
        if has_predecessor
        else "직접 실행 — 흐름의 첫 업무는 사람이 시작"
    )
    completion = "자동 완료 — 진단 자동 판정기 있음" if completion_mode == "auto" else "검토 후 완료 — 기본값"
    return [run, completion]


def human_gate_label(completion_mode: str) -> str:
    """마지막 노드 뒤 사람 단계 문구. 검토 후 완료면 승인 + 병합은 운영자 확인(ADR-0005), 자동 완료면 완료 확인.
    체인 화면(`server/views.chain_summary`)도 이 문구를 쓴다."""
    return "검토 승인 (사람) · 병합은 운영자 확인" if completion_mode == "review" else "완료 확인 (사람)"


def _assignment_reason(selection: SelectionRecord) -> str:
    if selection.status == "needs_selection" and selection.candidate_count == 0:
        return "후보 없음 — 에이전트를 등록하거나 직접 지정"
    return selection.reason


def compose(issues: Sequence[Issue], candidates: Sequence[Candidate], prefer: Sequence[str]) -> ChainPlan:
    """이슈 목록을 체인 하나(+ Standalone)로 구성한다. 규칙 1~7 은 모듈 docstring·step 정의 순서다."""
    # 1. 매핑
    mappings = {issue.key: map_issue(issue) for issue in issues}
    standalone: dict[str, Standalone] = {}
    capable: list[Issue] = []
    capabilities: dict[str, Capability] = {}
    for issue in issues:
        mapping = mappings[issue.key]
        if mapping.capability is None:
            standalone[issue.key] = Standalone(issue=issue, mapping=mapping, reason=mapping.reason)
        else:
            capable.append(issue)
            capabilities[issue.key] = mapping.capability

    # 2. 순서
    all_keys = {issue.key for issue in issues}
    capable_keys = {issue.key for issue in capable}
    ordered = _order(capable)

    # 3~5. 체인 구성 · 방식 · 배정
    nodes: list[PlanNode] = []
    for issue in ordered:
        mapping = mappings[issue.key]
        capability = capabilities[issue.key]
        prev = nodes[-1] if nodes else None
        if prev is not None and (prev.capability.code, capability.code) not in _HANDOFF_PAIRS:
            standalone[issue.key] = Standalone(issue=issue, mapping=mapping, reason=_UNSUPPORTED_PAIR_REASON)
            continue

        reasons = [mapping.reason, *_order_reasons(issue, capable_keys, all_keys)]
        if prev is None:
            reasons.append("체인의 첫 업무 — 선행 없음")
        else:
            reasons.append(f"선행 {prev.issue.key} ({prev.capability.code}) → {capability.code} 인계")

        kind = kind_for_capability(capability.code)
        has_predecessor = prev is not None
        run_mode = default_run_mode(has_predecessor)
        completion_mode: Literal["auto", "review"] = (
            "auto" if kind == "diagnosis" and can_auto_complete(kind) else "review"
        )
        reasons.extend(_mode_reasons(has_predecessor, completion_mode))

        selection = select_agent(issue.key, capability, candidates, prefer=prefer)
        reasons.append(_assignment_reason(selection))

        nodes.append(
            PlanNode(
                issue=issue,
                capability=capability,
                kind=kind,
                run_id=mapping.run_id,
                predecessor_key=prev.issue.key if prev is not None else None,
                run_mode=run_mode,
                completion_mode=completion_mode,
                criteria=tuple(criteria_template(kind)),
                selection=selection,
                reasons=tuple(reasons),
            )
        )

    # 6. 사람 단계  7. 제목
    if nodes:
        human_gate = human_gate_label(nodes[-1].completion_mode)
        title = nodes[0].issue.title if len(nodes) == 1 else f"{nodes[0].issue.title} → {nodes[-1].issue.title}"
    else:
        human_gate = ""
        title = ""

    return ChainPlan(
        nodes=tuple(nodes),
        standalone=tuple(standalone[issue.key] for issue in issues if issue.key in standalone),
        human_gate=human_gate,
        title=title,
    )
