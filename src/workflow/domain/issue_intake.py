"""GitHub 이슈 접수 — 접수 범위·Task 매핑·준비 판정 입력 (ADR-0014 결정 2·9, ARCHITECTURE "GitHub 업무 순환").

DB·HTTP 를 보지 않는다. 수집(`server/github_sync`)이 스냅샷과 설정을 넘기고 저장은 repo 가 한다.

- 종류·우선순위는 워크스페이스 매핑 표(`field_mappings`)가 라벨로 정한다(ADR-0020). 종류 이름으로 분기하지 않는다.
- 새 이슈의 자동 접수 범위: 설정 저장소의 open Issue 중 `label_filter` 라벨을 모두 가지고(대소문자 무시)
  `created_at >= start_at` 인 것. `selected_issue_numbers` 로 고른 이슈는 명시적 선택이라 라벨·시작 시각·닫힘과
  무관하게 받는다(닫혀 있으면 준비 판정이 `source_closed` 로 막는다). Pull request 와 다른 저장소는 어떤 경우에도 받지 않는다.
- `intake: all_open` 소스(ADR-0017)는 열린 Issue 를 전부 받는다 — `label_filter`·`start_at` 은 보지 않는다. 대신 실행은
  지시된 것만: `trigger_label` 이 붙은 이슈(`label_delegated`) 또는 운영자 [맡기기]. 지시 전 Task 는 `not_delegated` 대기다.
- 이슈 제목·본문은 Task 의 제목·요청 재료일 뿐 명령·경로·URL 로 해석하지 않는다. 실행 대상(target)은 이슈에서 받지 않고
  실행 생성 때 등록값으로 고정한다.
- 준비 판정 입력(`intake_facts`)은 가져온 업무와 직접 등록 업무가 같은 함수를 쓴다 — 차이는 원본 스냅샷 유무뿐이다.
"""

from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Literal

from collections.abc import Sequence

from workflow.contracts.github import GitHubIssueSnapshot, GitHubSourceConfig
from workflow.contracts.v1 import KindSpec
from workflow.domain.completion import criteria_template, merge_criteria
from workflow.domain.field_mapping import MappingRow, map_value

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
    if config.intake == "all_open":
        return IntakeScope(True, None, False)
    if not config.label_filter:
        return IntakeScope(False, "not_selected", False)
    labels = {label.casefold() for label in snapshot.labels}
    if not {label.casefold() for label in config.label_filter} <= labels:
        return IntakeScope(False, "label_mismatch", False)
    if _parse(snapshot.created_at) < _parse(config.start_at):
        return IntakeScope(False, "before_start", False)
    return IntakeScope(True, None, False)


def label_delegated(config: GitHubSourceConfig, snapshot: GitHubIssueSnapshot) -> bool:
    """`all_open` 소스에서 이슈에 트리거 라벨이 있는지(대소문자 무시). `filtered` 소스는 수집이 곧 지시라 보지 않는다."""
    if config.intake != "all_open" or config.trigger_label is None:
        return False
    return config.trigger_label.casefold() in {label.casefold() for label in snapshot.labels}


def task_input(snapshot: GitHubIssueSnapshot) -> tuple[str, str]:
    """Task 의 (제목, 요청). 이 둘이 바뀌면 새 Task revision 이다 — 담당·라벨·상태·시각만 바뀐 것은 입력 변경이 아니다."""
    return snapshot.title, snapshot.body.strip()


def issue_kind(rows: Sequence[MappingRow], snapshot: GitHubIssueSnapshot) -> str | None:
    """이슈 라벨로 매핑 표(`github · kind`)를 읽은 종류(ADR-0020). None 이면 그 이슈를 가져오지 않는다 — 기본 종류는
    매핑 행(`*`)으로만 둔다."""
    return map_value(rows, "github", "kind", snapshot.labels)


def issue_priority(rows: Sequence[MappingRow], snapshot: GitHubIssueSnapshot) -> str:
    return map_value(rows, "github", "priority", snapshot.labels) or "normal"


def snapshot_to_task_spec(
    config: GitHubSourceConfig, snapshot: GitHubIssueSnapshot, *, kind: KindSpec, session_id: str, task_id: str
) -> dict:
    """`repo.insert_task` 와 같은 dict. 종류는 매핑 결과(`issue_kind`)의 등록 봉투, 실행 방식은 이 시점 설정의
    `run_mode`, 완료는 사람 검토."""
    title, request = task_input(snapshot)
    criteria = merge_criteria(criteria_template(kind), [])
    return {
        "task_id": task_id,
        "session_id": session_id,
        "title": title,
        "request": request,
        "kind": kind.kind,
        "required_capability": {
            "code": kind.capability_code,
            # 로컬 저장소를 자동 매칭하는 소스는 GitHub 저장소 이름을 둔다 — 준비 판정이 매칭 값으로 바꿔 본다(github_match)
            "scope": {kind.scope_key: config.workflow_repository_id or config.repository_full_name},
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
    delegated: bool  # 실행 지시가 있다(또는 지시 단계가 없는 소스·직접 등록)

    def as_kwargs(self) -> dict:
        return asdict(self)


def intake_facts(
    *,
    request: str,
    run_mode: Literal["auto", "manual"],
    snapshot: GitHubIssueSnapshot | None,
    bindings: Mapping[int, str],
    max_rework_rounds: int | None,
    needs_delegation: bool = False,
    delegated_by: Literal["operator", "label"] | None = None,
) -> IntakeFacts:
    """원본이 없으면(직접 등록) 담당 개념이 없다 — 직접 선택·자동 선택으로 Agent 를 정한다. 요청은 언제나 필수다.
    `needs_delegation`(`all_open` 소스)이면 `delegated_by` 가 있어야 착수한다. 운영자 지시는 직접 지시라 `manual` 이어도
    자동 착수처럼 보고, 라벨 지시는 소스의 `run_mode` 를 따른다."""
    return IntakeFacts(
        assignee_ids=tuple(snapshot.assignee_ids) if snapshot is not None else None,
        bindings=dict(bindings),
        request_text=request,
        request_required=True,
        run_mode="auto" if delegated_by == "operator" else run_mode,
        max_rework_rounds=max_rework_rounds,
        source_state=snapshot.state if snapshot is not None else None,
        delegated=not needs_delegation or delegated_by is not None,
    )
