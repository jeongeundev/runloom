"""준비 판정 — ARCHITECTURE "준비 판정 — 대기 코드", ADR-0014.

가져온 업무 하나가 지금 착수 가능한지 조건마다 따로 평가하고 미해결 항목을 모두 돌려준다(첫 사유에서
멈추지 않음). 업무끼리 직렬 체인으로 묶지 않는다 — 다른 Task 에 관한 사실은 호출자가 이 Task 의 값
(`busy_execution_ids`·`result_dependency_ready` 등)으로 넘긴다. 시각·DB·HTTP 를 보지 않고, 현재 시각도
`TaskFacts.now` 로 받는다. Agent 의 능력·범위 확인은 `selection.select_agent` 를 그대로 쓴다.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Literal

from workflow.contracts.v1 import BUILTIN_KIND_NAMES, Capability
from workflow.domain.delegation import ApprovalFact, offline_reason
from workflow.domain.selection import Candidate, select_agent

Actor = Literal["operator", "assignee", "system"]
# 러너 능력 → 대기 이유의 이름
_RUNNER_CAPABILITY_LABELS = {"verify_only": "검증만 다시"}


@dataclass(frozen=True)
class Blocker:
    code: str  # ARCHITECTURE "준비 판정 — 대기 코드" 표의 code
    reason: str  # 화면에 그대로 보일 이유 문구
    actor: Actor  # 해소를 위해 행동할 주체


@dataclass(frozen=True)
class TaskReadiness:
    ready: bool
    blockers: tuple[Blocker, ...]
    agent_id: str | None = None  # 담당·능력 검사를 통과한 실행 Agent. 없으면 None


@dataclass(frozen=True)
class ExecutorFacts:
    agent_id: str
    connector_id: str | None
    repository_id: str | None  # 로컬 등록의 scope 값
    connection_type: Literal["local", "api"]
    connection_state: str
    last_seen_at: str | None  # RFC 3339
    supported_kinds: tuple[str, ...] | None  # 마지막 claim 의 선언. None 이면 구버전
    owner_name: str | None = None  # 러너 소유자 표시 이름. None 이면 공용 (phase 17)
    runner_capabilities: tuple[str, ...] | None = None  # 마지막 claim 의 러너 능력. None 이면 보고 없음 (phase 17)


@dataclass(frozen=True)
class TaskFacts:
    task_id: str
    kind: str
    now: str  # RFC 3339
    offline_after_seconds: int
    required: Capability
    candidates: Sequence[Candidate]
    executors: Mapping[str, ExecutorFacts]  # agent_id → 실행 환경
    # 담당: None 이면 GitHub 담당 개념 없음(검토 등) — `chosen_agent_id` 또는 기존 자동 선택
    assignee_ids: tuple[int, ...] | None = None
    bindings: Mapping[int, str] = field(default_factory=dict)  # GitHub 사용자 ID → agent_id
    chosen_agent_id: str | None = None  # 사람 응답으로 지정한 Agent, 또는 설정의 검토 Agent
    # 소스 자동 매칭(`github_match.match_source`, phase 11 step 6): 담당 연결·기본 수정 Agent·자동 선택 결과와 그 사유.
    # `auto_match` 면 Agent 는 이 결과로만 정한다(담당 대기·기존 자동 선택 없음). 아니면 담당 대기 전의 기본값일 뿐이다
    matched_agent_id: str | None = None
    match_blockers: tuple[Blocker, ...] = ()
    auto_match: bool = False
    repository_allowed: bool = True  # 저장소가 WORKFLOW_GITHUB_REPOS 안인지
    # 입력
    request_text: str = ""
    request_required: bool = False
    task_revision: int = 1
    information_requested_at_revision: int | None = None  # 마지막 needs_information 이 나온 revision
    result_dependency_task_id: str | None = None  # 결과가 필요한 선행 Task (검토의 수정 Task 등)
    result_dependency_ready: bool = False
    # 조율·환경
    open_request_ids: tuple[str, ...] = ()  # 열린 HumanRequest
    busy_execution_ids: tuple[str, ...] = ()  # 같은 로컬 등록에서 활성인 다른 수정 Execution
    pair_agent_id: str | None = None  # 검토 Task 의 수정 Agent — 같은 연결 프로그램·저장소여야 함
    run_mode: Literal["auto", "manual"] = "auto"
    rework_requested: bool = False
    rework_rounds_used: int = 0
    max_rework_rounds: int | None = None
    source_state: Literal["open", "closed"] | None = None  # 원본 이슈 상태. 원본 없으면 None
    delegated: bool = True  # `all_open` 소스 Task 의 실행 지시(맡기기·트리거 라벨). 지시 단계가 없으면 True
    closed: bool = False  # 운영자 종료
    direct_work: bool = False  # 업무가 직접 작업 중 — 사람이 자기 세션에서 하므로 에이전트를 착수하지 않는다 (phase 16)
    # agent_id → 소유자 승인 상태. 없는 Agent 는 `not_needed` (phase 17)
    owner_approvals: Mapping[str, ApprovalFact] = field(default_factory=dict)
    required_runner_capability: str | None = None  # 이 실행에 필요한 러너 능력(검증만 다시 = `verify_only`, phase 17)


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _online(executor: ExecutorFacts, now: str, offline_after_seconds: int) -> bool:
    # server/views.agent_online 과 같은 규칙: API 는 heartbeat 가 없다
    if executor.connection_state != "online":
        return False
    if executor.connection_type == "api":
        return True
    if not executor.last_seen_at:
        return False
    return _parse(now) - _parse(executor.last_seen_at) <= timedelta(seconds=offline_after_seconds)


def _supports(executor: ExecutorFacts, kind: str) -> bool:
    if kind not in BUILTIN_KIND_NAMES or executor.connection_type == "api":
        return True
    # `supported_kinds` 를 보내지 않는 구버전 연결 프로그램은 내장 종류를 실행하지 못한다(ADR-0014 4항 — 옛 내장
    # `code_change` 는 ADR-0019 로 없어졌다)
    return executor.supported_kinds is not None and kind in executor.supported_kinds


def _resolve_agent(facts: TaskFacts, blockers: list[Blocker]) -> str | None:
    """담당자 → 후보 Agent. 자동 추정은 하지 않는다 — 담당 개념이 없을 때만 기존 자동 선택. 자동 매칭 소스는
    매칭 결과(사유는 `match_blockers`)를, 담당이 풀리지 않은 filtered 소스는 기본 수정 Agent 를 쓴다."""
    blockers.extend(facts.match_blockers)
    if facts.auto_match:
        return facts.matched_agent_id
    if facts.assignee_ids is None:
        if facts.chosen_agent_id is not None:
            return facts.chosen_agent_id
        record = select_agent(facts.task_id, facts.required, facts.candidates)
        if record.selected_agent_id is None:
            blockers.append(Blocker("delegation_denied", record.reason, "operator"))
        return record.selected_agent_id
    if not facts.assignee_ids:
        if facts.matched_agent_id is not None:
            return facts.matched_agent_id
        blockers.append(Blocker("assignee_missing", "GitHub 담당자 없음", "operator"))
        return None
    if len(facts.assignee_ids) >= 2:
        bound = {facts.bindings[i] for i in facts.assignee_ids if i in facts.bindings}
        if facts.chosen_agent_id is not None and facts.chosen_agent_id in bound:
            return facts.chosen_agent_id
        if facts.matched_agent_id is not None:
            return facts.matched_agent_id
        count = len(facts.assignee_ids)
        blockers.append(Blocker("assignee_multiple", f"GitHub 담당자 {count}명 — Agent 지정 필요", "operator"))
        return None
    (assignee,) = facts.assignee_ids
    agent_id = facts.bindings.get(assignee, facts.matched_agent_id)
    if agent_id is None:
        blockers.append(Blocker("assignee_unbound", f"GitHub 담당자 {assignee} 에 연결된 Agent 없음", "operator"))
    return agent_id


def _check_delegation(facts: TaskFacts, agent_id: str | None, blockers: list[Blocker]) -> str | None:
    if not facts.repository_allowed:
        blockers.append(Blocker("delegation_denied", "허용 저장소 밖", "operator"))
    if agent_id is None:
        return None
    # 사람이 고른 Agent 도 같은 능력·범위 검사를 거친다
    record = select_agent(facts.task_id, facts.required, facts.candidates, mode="manual", chosen_agent_id=agent_id)
    if record.selected_agent_id is None:
        blockers.append(Blocker("delegation_denied", record.reason, "operator"))
    return record.selected_agent_id


def _check_input(facts: TaskFacts, blockers: list[Blocker]) -> None:
    if facts.request_required and not facts.request_text.strip():
        blockers.append(Blocker("input_missing", "필수 입력(재현 정보) 없음", "assignee"))
    asked = facts.information_requested_at_revision
    if asked is not None and asked >= facts.task_revision:
        blockers.append(Blocker("input_missing", "요청한 추가 정보 응답 대기", "assignee"))
    if facts.result_dependency_task_id is not None and not facts.result_dependency_ready:
        blockers.append(Blocker("awaiting_result", f"선행 결과 대기 — {facts.result_dependency_task_id}", "system"))


def _check_executor(facts: TaskFacts, agent_id: str, blockers: list[Blocker]) -> None:
    executor = facts.executors.get(agent_id)
    if executor is None:
        blockers.append(Blocker("executor_offline", f"{agent_id} 연결 정보 없음", "system"))
        return
    if not _online(executor, facts.now, facts.offline_after_seconds):
        blockers.append(Blocker("executor_offline", offline_reason(executor.owner_name), "system"))
    if not _supports(executor, facts.kind):
        blockers.append(
            Blocker("executor_outdated", f"연결 프로그램 업데이트 필요 — {facts.kind} 미지원", "operator")
        )
    required = facts.required_runner_capability
    if required is not None and required not in (executor.runner_capabilities or ()):
        label = _RUNNER_CAPABILITY_LABELS.get(required, required)
        blockers.append(Blocker("executor_outdated", f"연결 프로그램 업데이트 필요 — {label} 미지원", "operator"))
    if facts.pair_agent_id is not None:
        pair = facts.executors.get(facts.pair_agent_id)
        if pair is None or (pair.connector_id, pair.repository_id) != (executor.connector_id, executor.repository_id):
            blockers.append(
                Blocker(
                    "review_repository_mismatch",
                    f"검토 Agent {agent_id} 가 수정 Agent {facts.pair_agent_id} 와 다른 연결 프로그램·저장소",
                    "operator",
                )
            )


def _check_owner_approval(facts: TaskFacts, agent_id: str, blockers: list[Blocker]) -> None:
    approval = facts.owner_approvals.get(agent_id)
    if approval is None:
        return
    if approval.state in ("missing", "pending"):
        blockers.append(Blocker("owner_approval_pending", approval.reason, "operator"))
    elif approval.state == "declined":
        blockers.append(Blocker("owner_approval_declined", approval.reason, "operator"))


def evaluate_readiness(facts: TaskFacts) -> TaskReadiness:
    """대기 코드 표의 조건을 모두 평가한다. 운영자 종료는 마감이라 `task_closed` 하나만 돌려준다.

    `manual_mode` 는 다른 조건이 모두 풀렸어도 자동 착수만 막는 사유다 — 직접 실행은 호출자가
    이 사유를 제외하고 판단한다.
    """
    if facts.closed:
        return TaskReadiness(ready=False, blockers=(Blocker("task_closed", "운영자 종료", "operator"),))

    blockers: list[Blocker] = []
    if facts.source_state == "closed":
        blockers.append(Blocker("source_closed", "원본 이슈 닫힘 — 재오픈 시 재평가", "operator"))

    agent_id = _check_delegation(facts, _resolve_agent(facts, blockers), blockers)
    _check_input(facts, blockers)
    if facts.open_request_ids:
        blockers.append(Blocker("decision_pending", f"사람 응답 대기 {len(facts.open_request_ids)}건", "operator"))
    if agent_id is not None:
        _check_owner_approval(facts, agent_id, blockers)
        _check_executor(facts, agent_id, blockers)
    if facts.busy_execution_ids:
        blockers.append(Blocker("repository_busy", "같은 저장소에서 다른 수정 실행 중", "system"))
    if (
        facts.rework_requested
        and facts.max_rework_rounds is not None
        and facts.rework_rounds_used >= facts.max_rework_rounds
    ):
        blockers.append(
            Blocker("rework_limit_reached", f"자동 재작업 상한 {facts.max_rework_rounds}회 도달", "operator")
        )
    if not facts.delegated:
        blockers.append(
            Blocker("not_delegated", "실행 지시 전 — [에이전트에게 맡기기] 또는 `runloom` 라벨", "operator")
        )
    if facts.direct_work:
        blockers.append(Blocker("direct_work", "직접 작업 중 — 에이전트에게 넘기면 시작", "operator"))
    if facts.run_mode == "manual":
        blockers.append(Blocker("manual_mode", "직접 실행 모드", "operator"))

    return TaskReadiness(ready=not blockers, blockers=tuple(blockers), agent_id=agent_id)
