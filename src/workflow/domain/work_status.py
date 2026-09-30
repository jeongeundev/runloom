"""업무 상태 판정 — ARCHITECTURE "업무와 단계 — phase 14" 업무 상태 표(+ "업무 화면 — phase 16" 갱신), ADR-0020 결정 4·5.

시각·DB·HTTP 를 보지 않는다. 호출자가 단계·사람 요청·PR 을 `WorkItemFacts` 로 모아 넘긴다.
단계 상태(`domain/status.py` 사용자 상태 7개)는 그대로 읽기만 한다.
"""

from dataclasses import dataclass
from typing import Literal

WORK_STATUSES = (
    "새로 들어옴",
    "대기",
    "에이전트 작업 중",
    "직접 작업 중",
    "내 차례",
    "PR · 검토",
    "완료",
    "종료",
)
TERMINAL_WORK_STATUSES = ("완료", "종료")

STAGE_FAILED = "stage_failed"  # 실행 실패 뒤 사람에게 다시 맡기기·닫기를 묻는 요청 코드

_STAGE_CLOSED = ("완료", "실패")
_STAGE_RUNNING = ("실행 요청됨", "실행 중")
_REASON_MAX = 80


@dataclass(frozen=True)
class WorkStatus:
    status: str  # WORK_STATUSES 중 하나
    reason: str


@dataclass(frozen=True)
class StageFact:
    task_id: str
    kind: str
    kind_label: str
    status: str  # 단계 사용자 상태 (USER_STATUS_LABELS)
    status_reason: str
    created_at: str
    executed: bool  # 실행이 한 번이라도 있었는지


@dataclass(frozen=True)
class RequestFact:
    code: str
    question: str


@dataclass(frozen=True)
class PullRequestFact:
    state: Literal["pending", "open", "merged", "closed", "failed"]
    number: int | None


@dataclass(frozen=True)
class WorkItemFacts:
    stored_status: str
    stored_reason: str
    assigned: bool  # 업무 담당 또는 어느 단계의 선택 Agent 가 있음
    delegated: bool  # 원본이 all_open 이고 지시 전이면 False
    stages: tuple[StageFact, ...]
    open_requests: tuple[RequestFact, ...]
    pull_request: PullRequestFact | None
    direct_member_name: str | None = None  # 직접 작업 중이면 그 멤버 표시 이름 (phase 16)
    # 감지 PR(`work_pull_requests`) — open·merged·closed, 최근순 (phase 16 step 9)
    detected_pull_requests: tuple[PullRequestFact, ...] = ()


def _pr_suffix(pr: PullRequestFact) -> str:
    return f" — #{pr.number}" if pr.number is not None else ""


def _latest(stages) -> StageFact | None:
    return max(stages, key=lambda s: (s.created_at, s.task_id), default=None)


def work_status(facts: WorkItemFacts) -> WorkStatus:
    """ARCHITECTURE 업무 상태 표를 위에서부터 적용한다. 첫 일치가 답이다."""
    if facts.stored_status in TERMINAL_WORK_STATUSES:
        return WorkStatus(facts.stored_status, facts.stored_reason)

    pr = facts.pull_request
    if pr is not None and pr.state == "merged":
        return WorkStatus("완료", "PR 병합" + _pr_suffix(pr))
    if pr is not None and pr.state == "closed":
        return WorkStatus("종료", "PR 이 병합 없이 닫힘" + _pr_suffix(pr))
    detected_merged = next((d for d in facts.detected_pull_requests if d.state == "merged"), None)
    if detected_merged is not None:
        return WorkStatus("완료", "PR 병합" + _pr_suffix(detected_merged))

    if facts.open_requests:
        failed = next((r for r in facts.open_requests if r.code == STAGE_FAILED), None)
        if failed is not None:
            stage = _latest(s for s in facts.stages if s.status == "실패")
            return WorkStatus("내 차례", "실패 — " + (stage.status_reason if stage else failed.question))
        first_line = facts.open_requests[0].question.split("\n", 1)[0][:_REASON_MAX]
        return WorkStatus("내 차례", "사람 요청 — " + first_line)

    if pr is not None and pr.state == "pending":
        return WorkStatus("PR · 검토", "PR 여는 중")
    if pr is not None and pr.state == "open":
        return WorkStatus("PR · 검토", "PR 확인" + _pr_suffix(pr))
    detected_open = next((d for d in facts.detected_pull_requests if d.state == "open"), None)
    if detected_open is not None:  # 병합 없이 닫힌 감지 PR 은 보지 않는다
        return WorkStatus("PR · 검토", "PR 확인" + _pr_suffix(detected_open))

    if facts.direct_member_name is not None:
        return WorkStatus("직접 작업 중", facts.direct_member_name)

    check = _latest(s for s in facts.stages if s.status == "확인 필요")
    if check is not None:
        return WorkStatus("내 차례", check.status_reason)

    latest = _latest(facts.stages)
    if latest is not None and all(s.status in _STAGE_CLOSED for s in facts.stages):
        if latest.status == "완료":
            return WorkStatus("완료", f"{latest.kind_label} 완료")
        return WorkStatus("종료", latest.status_reason)

    running = _latest(s for s in facts.stages if s.status in _STAGE_RUNNING)
    if running is not None:
        return WorkStatus("에이전트 작업 중", f"{running.kind_label} 실행 중")

    if not any(s.executed for s in facts.stages):
        if not facts.assigned:
            return WorkStatus("새로 들어옴", "담당 없음")
        if not facts.delegated:
            return WorkStatus("새로 들어옴", "지시 전 — [에이전트에게 맡기기]")

    waiting = _latest(s for s in facts.stages if s.status not in _STAGE_CLOSED)
    return WorkStatus("대기", waiting.status_reason if waiting else "대기")
