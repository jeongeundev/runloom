"""후속 결정 — ARCHITECTURE "후속 결정 표 (`decide_followup`)", ADR-0014 6항.

판정이 끝난 결과 하나에서 다음 할 일(기존 검토 연결·새 검토 생성·같은 수정 Task 재작업·사람 요청·없음)을
정한다. 연결할 후속 종류는 등록 규칙(`SuccessorRule`)에서 찾고 종류 이름으로 분기하지 않는다. 기존
업무 대응은 호출자가 명시적 원인 참조(원인 키·`predecessor_task_id`)로 찾아 넘긴다 — 제목 유사도로
짝짓지 않는다. 저장·착수·사람 요청 생성은 워커가 하고, 여기서는 DB·시각을 보지 않는다.

`approved` 는 검토 종결일 뿐 병합·이슈 종료·push 권한이 아니다.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from workflow.contracts.v1 import SuccessorRule

Action = Literal["link_existing", "create_task", "rework", "request_human", "none"]


@dataclass(frozen=True)
class ReviewFacts:
    """검토 결과일 때만 채운다. `rounds_used` 는 지금까지 돈 자동 재작업 횟수."""

    fix_task_id: str
    source_execution_id: str  # 검토한 수정 Execution
    reviewed_commit: str
    latest_fix_execution_id: str  # 수정 Task 의 가장 최근 판정 통과 결과
    latest_fix_commit: str
    rounds_used: int
    max_rework_rounds: int


@dataclass(frozen=True)
class FollowupContext:
    session_id: str
    task_id: str
    kind: str
    execution_id: str  # 결과를 낸 Execution
    outcome: str
    verdict: Literal["passed", "failed"]
    result_commit: str | None  # 수정 결과 커밋. 검토 결과면 None
    rules: Sequence[SuccessorRule]
    rules_revision: int
    review: ReviewFacts | None = None
    existing_followup_task_id: str | None = None  # 이 Task 를 원인으로 이미 있는 후속 Task
    handled_cause_keys: frozenset[str] = frozenset()  # 이미 처리한 원인 키(재전송·재시작)
    source_state: Literal["open", "closed"] | None = None
    task_closed: bool = False


@dataclass(frozen=True)
class FollowupTaskSpec:
    session_id: str
    kind: str
    cause_execution_id: str  # 유일 키 (session_id, cause_execution_id, kind)
    predecessor_task_id: str
    rules_revision: int


@dataclass(frozen=True)
class FollowupDecision:
    action: Action
    reason: str
    target_task_id: str | None = None  # link_existing·rework·request_human 의 대상 Task
    create: FollowupTaskSpec | None = None
    cause_key: str | None = None  # Execution start_key 또는 HumanRequest cause_key
    request_code: str | None = None
    hold_code: str | None = None  # action none 의 기록 사유
    review_commit: str | None = None  # link_existing·create_task 가 검토할 결과 커밋
    base_commit: str | None = None  # rework 의 시작 커밋
    input_execution_ids: tuple[str, ...] = ()  # rework 입력: 이전 수정 결과·검토 결과


def _request(ctx: FollowupContext, code: str, target_task_id: str, reason: str) -> FollowupDecision:
    return FollowupDecision(
        action="request_human",
        reason=reason,
        target_task_id=target_task_id,
        cause_key=f"{code}:{ctx.execution_id}",
        request_code=code,
    )


def _after_fix(ctx: FollowupContext) -> FollowupDecision:
    rule = next((r for r in ctx.rules if r.from_kind == ctx.kind and ctx.outcome in r.on_outcomes), None)
    if rule is None:
        return FollowupDecision("none", f"outcome {ctx.outcome} 에 맞는 후속 규칙 없음")
    cause_key = f"review:{ctx.execution_id}"
    if ctx.existing_followup_task_id is not None:
        return FollowupDecision(
            "link_existing",
            f"규칙 {rule.from_kind} → {rule.to_kind}: 기존 {ctx.existing_followup_task_id} 에 결과 연결",
            target_task_id=ctx.existing_followup_task_id,
            cause_key=cause_key,
            review_commit=ctx.result_commit,
        )
    spec = FollowupTaskSpec(
        session_id=ctx.session_id,
        kind=rule.to_kind,
        cause_execution_id=ctx.execution_id,
        predecessor_task_id=ctx.task_id,
        rules_revision=ctx.rules_revision,
    )
    return FollowupDecision(
        "create_task",
        f"규칙 {rule.from_kind} → {rule.to_kind}: 새 Task 생성",
        create=spec,
        cause_key=cause_key,
        review_commit=ctx.result_commit,
    )


def _after_review(ctx: FollowupContext, review: ReviewFacts) -> FollowupDecision:
    latest = (review.latest_fix_execution_id, review.latest_fix_commit)
    if (review.source_execution_id, review.reviewed_commit) != latest:
        return FollowupDecision(
            "none", f"이전 커밋 {review.reviewed_commit[:12]} 검토 — 최신 결과 아님", hold_code="stale_review"
        )
    if ctx.outcome == "approved":
        return FollowupDecision("none", "검토 승인 — 병합·이슈 종료는 사람")
    if ctx.outcome != "changes_requested":
        return FollowupDecision("none", f"검토 outcome {ctx.outcome} 은 후속 대상 아님")
    if f"rework:{ctx.execution_id}" in ctx.handled_cause_keys:
        # 그 재작업이 rounds_used 에 이미 들어 있다 — 상한 판단은 아직 재작업을 일으키지 않은 검토에만
        return FollowupDecision("none", "이미 재작업을 시작한 검토")
    if review.rounds_used >= review.max_rework_rounds:
        return _request(
            ctx, "rework_limit_reached", review.fix_task_id,
            f"자동 재작업 상한 {review.max_rework_rounds}회 도달 — 사람 결정 필요",
        )
    return FollowupDecision(
        "rework",
        f"수정 요청 — 재작업 {review.rounds_used + 1}/{review.max_rework_rounds}회",
        target_task_id=review.fix_task_id,
        cause_key=f"rework:{ctx.execution_id}",
        base_commit=review.reviewed_commit,
        input_execution_ids=(review.source_execution_id, ctx.execution_id),
    )


def _decide(ctx: FollowupContext) -> FollowupDecision:
    if ctx.task_closed:
        return FollowupDecision("none", "운영자 종료 — 새 후속 없음", hold_code="task_closed")
    if ctx.source_state == "closed":
        return FollowupDecision("none", "원본 이슈 닫힘 — 재오픈 시 재평가", hold_code="source_closed")
    role = "fix" if ctx.review is None else "review"
    if ctx.verdict == "failed":
        return _request(ctx, f"{role}_verification_failed", ctx.task_id, "결과 판정 실패 — 자동 재시도 없음")
    if ctx.outcome == "needs_information":
        return _request(ctx, f"{role}_needs_information", ctx.task_id, "에이전트가 추가 정보를 요청함")
    if ctx.review is None:
        return _after_fix(ctx)
    return _after_review(ctx, ctx.review)


def decide_followup(context: FollowupContext) -> FollowupDecision:
    decision = _decide(context)
    if decision.cause_key is not None and decision.cause_key in context.handled_cause_keys:
        return FollowupDecision("none", f"이미 처리한 후속 {decision.cause_key}")
    return decision
