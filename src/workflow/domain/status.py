"""Execution 상태 전이(ARCHITECTURE "실행 이벤트")와 사용자 상태 대응(PRD 3절).

시각·DB·HTTP 를 보지 않는다. 호출자가 만든 `TaskView` 스냅샷만 판정한다.
"""

from dataclasses import dataclass
from typing import Literal

EXECUTION_STATUSES = ("queued", "accepted", "running", "result_ready", "failed", "unknown")
TERMINAL_STATUSES = ("result_ready", "failed")

# (현재 상태, 이벤트 type) → 다음 상태. 없는 조합은 InvalidTransition.
# `unknown` 은 서버 관찰로만 들어가며, 기존 실행 주체의 재전송으로만 빠져나온다.
_TRANSITIONS: dict[tuple[str, str], str] = {
    ("queued", "accepted"): "accepted",
    ("accepted", "started"): "running",
    ("accepted", "failed"): "failed",
    ("running", "progress"): "running",
    ("running", "result_ready"): "result_ready",
    ("running", "failed"): "failed",
    ("unknown", "started"): "running",
    ("unknown", "result_ready"): "result_ready",
    ("unknown", "failed"): "failed",
}


class InvalidTransition(Exception):
    def __init__(self, current: str, event_type: str):
        super().__init__(f"{current} 상태에서는 {event_type}를 받을 수 없습니다")
        self.current = current
        self.event_type = event_type


def next_execution_status(current: str, event_type: str) -> str:
    try:
        return _TRANSITIONS[(current, event_type)]
    except KeyError:
        raise InvalidTransition(current, event_type) from None


USER_STATUS_LABELS = ("대기", "실행 가능", "실행 요청됨", "실행 중", "확인 필요", "완료", "실패")


@dataclass(frozen=True)
class TaskView:
    kind: Literal["diagnosis", "code_change"]
    run_mode: Literal["manual", "auto"]
    completion_mode: Literal["auto", "review"]
    selection_status: Literal["selected", "needs_selection"]
    selection_reason: str
    selected_agent_id: str | None
    predecessor_status: str | None  # 선행 Task 의 사용자 상태. 없으면 None
    connector_online: bool | None  # code_change 만 의미. 진단은 None
    connector_last_seen: str | None
    execution_status: str | None  # 활성 Execution 상태. 없으면 None
    last_progress: str | None
    failed_code: str | None
    failed_message: str | None
    process_stopped: bool | None
    verdict: Literal["passed", "failed", "undecidable"] | None  # 자동 완료 판정
    verdict_detail: str | None
    review_decision: Literal["approve", "request_changes", "close"] | None
    finished: bool  # Task 가 완료 또는 실패로 마감됐는지


@dataclass(frozen=True)
class UserStatus:
    label: str  # USER_STATUS_LABELS 중 하나
    reason: str  # 화면에 그대로 보일 이유 문구


def user_status(view: TaskView) -> UserStatus:
    """PRD 3절 대응표를 행 순서대로 적용한다. 검토 마감이 실행 상태보다 우선한다."""
    if view.finished and view.review_decision == "approve":
        return UserStatus("완료", "검토 승인")
    if view.finished and view.review_decision == "close":
        return UserStatus("실패", "검토 거절")

    if view.execution_status is None:
        if view.predecessor_status is not None and view.predecessor_status != "완료":
            return UserStatus("대기", "선행 대기")
        if view.connector_online is False:
            return UserStatus("대기", f"연결 끊김, 마지막 확인 {view.connector_last_seen}")
        if view.selection_status == "needs_selection":
            return UserStatus("확인 필요", view.selection_reason)
        if view.run_mode == "auto":
            return UserStatus("대기", "자동 실행 대기")
        return UserStatus("실행 가능", f"{view.selected_agent_id} 선택됨")

    if view.execution_status == "queued":
        return UserStatus("실행 요청됨", "접수 대기")
    if view.execution_status == "accepted":
        return UserStatus("실행 요청됨", "접수 확인")
    if view.execution_status == "running":
        return UserStatus("실행 중", view.last_progress or "시작 확인")
    if view.execution_status == "result_ready":
        if view.completion_mode == "review":
            return UserStatus("확인 필요", "검토 대기")
        if view.verdict == "passed":
            return UserStatus("완료", view.verdict_detail or "판정 통과")
        if view.verdict is None:
            return UserStatus("확인 필요", "판정 대기")
        return UserStatus("확인 필요", view.verdict_detail or "판정 불가")
    if view.execution_status == "failed" and view.process_stopped:
        return UserStatus("실패", f"{view.failed_code} · {view.failed_message}")
    if view.execution_status == "failed":
        return UserStatus("확인 필요", "종료 미확인 — 재실행하지 않음")
    return UserStatus("확인 필요", "시작 여부 불명 — 재실행하지 않음")
