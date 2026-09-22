"""GitHub 이슈 접수 — 접수 범위·Task 매핑·준비 판정 입력 (ADR-0014 결정 2·9, ARCHITECTURE "GitHub 업무 순환").

DB·HTTP 를 보지 않는다. 수집(`server/github_sync`)이 스냅샷과 설정을 넘기고 저장은 repo 가 한다.

- 새 이슈의 자동 접수 범위: 설정 저장소의 open Issue 중 `label_filter` 라벨을 모두 가지고(대소문자 무시)
  `created_at >= start_at` 인 것. `selected_issue_numbers` 로 고른 이슈는 명시적 선택이라 라벨·시작 시각·닫힘과
  무관하게 받는다(닫혀 있으면 준비 판정이 `source_closed` 로 막는다). Pull request 와 다른 저장소는 어떤 경우에도 받지 않는다.
- 이슈 제목·본문은 Task 의 제목·요청 재료일 뿐 명령·경로·URL 로 해석하지 않는다. 실행 대상(target)은 이슈에서 받지 않고
  실행 생성 때 등록값으로 고정한다.
- 준비 판정 입력(`intake_facts`)은 가져온 업무와 직접 등록 업무가 같은 함수를 쓴다 — 차이는 원본 스냅샷 유무뿐이다.
"""

from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Literal

from workflow.contracts.github import GitHubIssueSnapshot, GitHubSourceConfig
from workflow.contracts.v1 import BUILTIN_KINDS
from workflow.domain.completion import criteria_template, merge_criteria

# GitHub 이슈가 되는 업무 종류(ADR-0014 결정 3). 후속·재작업은 규칙 표가 정한다.
ISSUE_KIND = next(spec for spec in BUILTIN_KINDS if spec.kind == "bug_fix")

SkipReason = Literal["other_repository", "pull_request", "closed", "not_selected", "label_mismatch", "before_start"]


@dataclass(frozen=True)
class IntakeScope:
    accept: bool
    reason: SkipReason | None  # 받지 않는 이유. 받으면 None
    explicit: bool  # `selected_issue_numbers` 로 고른 이슈


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def intake_scope(config: GitHubSourceConfig, snapshot: GitHubIssueSnapshot) -> IntakeScope:
    """아직 Task 가 없는 이슈를 받을지. 이미 받은 이슈의 변경은 범위와 무관하게 수집기가 반영한다."""
    explicit = snapshot.number in config.selected_issue_numbers
    if snapshot.repository_full_name.lower() != config.repository_full_name.lower():
        return IntakeScope(False, "other_repository", explicit)
    if snapshot.is_pull_request:
        return IntakeScope(False, "pull_request", explicit)
    if explicit:
        return IntakeScope(True, None, True)
    if snapshot.state == "closed":
        return IntakeScope(False, "closed", False)
    if not config.label_filter:
        return IntakeScope(False, "not_selected", False)
    labels = {label.casefold() for label in snapshot.labels}
    if not {label.casefold() for label in config.label_filter} <= labels:
        return IntakeScope(False, "label_mismatch", False)
    if _parse(snapshot.created_at) < _parse(config.start_at):
        return IntakeScope(False, "before_start", False)
    return IntakeScope(True, None, False)


def task_input(snapshot: GitHubIssueSnapshot) -> tuple[str, str]:
    """Task 의 (제목, 요청). 이 둘이 바뀌면 새 Task revision 이다 — 담당·라벨·상태·시각만 바뀐 것은 입력 변경이 아니다."""
    return snapshot.title, snapshot.body.strip()


def snapshot_to_task_spec(
    config: GitHubSourceConfig, snapshot: GitHubIssueSnapshot, *, session_id: str, task_id: str
) -> dict:
    """`repo.insert_task` 와 같은 dict. 실행 방식은 이 시점 설정의 `run_mode`, 완료는 사람 검토."""
    title, request = task_input(snapshot)
    criteria = merge_criteria(criteria_template(ISSUE_KIND), [])
    return {
        "task_id": task_id,
        "session_id": session_id,
        "title": title,
        "request": request,
        "kind": ISSUE_KIND.kind,
        "required_capability": {
            "code": ISSUE_KIND.capability_code,
            "scope": {ISSUE_KIND.scope_key: config.workflow_repository_id},
        },
        "selection_mode": "auto",
        "chosen_agent_id": None,
        "run_mode": config.run_mode,
        "completion_mode": "review",
        "criteria": [c.__dict__ for c in criteria],
        "predecessor_task_id": None,
        "revision": 1,
        "target": {},
        "status": "대기",
        "status_reason": "준비 판정 대기",
        "chain_id": None,
        "source_ref": f"{config.repository_full_name}#{snapshot.number}",
    }


@dataclass(frozen=True)
class IntakeFacts:
    """`TaskFacts` 의 담당·입력·원본 부분. 이름이 `TaskFacts` 필드와 같다 — `TaskFacts(..., **facts.as_kwargs())`."""

    assignee_ids: tuple[int, ...] | None
    bindings: Mapping[int, str]
    request_text: str
    request_required: bool
    run_mode: Literal["auto", "manual"]
    max_rework_rounds: int | None
    source_state: Literal["open", "closed"] | None

    def as_kwargs(self) -> dict:
        return asdict(self)


def intake_facts(
    *,
    request: str,
    run_mode: Literal["auto", "manual"],
    snapshot: GitHubIssueSnapshot | None,
    bindings: Mapping[int, str],
    max_rework_rounds: int | None,
) -> IntakeFacts:
    """원본이 없으면(직접 등록) 담당 개념이 없다 — 직접 선택·자동 선택으로 Agent 를 정한다. 요청은 언제나 필수다."""
    return IntakeFacts(
        assignee_ids=tuple(snapshot.assignee_ids) if snapshot is not None else None,
        bindings=dict(bindings),
        request_text=request,
        request_required=True,
        run_mode=run_mode,
        max_rework_rounds=max_rework_rounds,
        source_state=snapshot.state if snapshot is not None else None,
    )
