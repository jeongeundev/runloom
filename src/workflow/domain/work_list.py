"""업무 목록 모델 — ARCHITECTURE "업무 화면 — phase 16" 끝난 업무·빠른 필터·묶기 순서·보드 칸·다음 할 일.

시각·DB·HTTP 를 보지 않는다. 행(`WorkRow`)은 repo `list_work_rows` 가 모으고, 끝난 업무 범위의 경계 시각은
서버(`views`)가 계산한다.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass

from workflow.contracts.v1 import WORK_KEY_PATTERN, WORK_KEY_PREFIX
from workflow.domain.work_status import TERMINAL_WORK_STATUSES, WORK_STATUSES

QUICK_FILTERS = ("all", "my_turn", "unassigned", "agent_working")
GROUP_BYS = ("assignee", "status", "repo")
VIEWS = ("list", "board")
CLOSED_SCOPES = ("recent", "all")
CLOSED_RECENT_DAYS = 14
PRIORITY_ORDER = ("high", "normal", "low")

# (칸 키, 칸 이름, 드는 업무 상태) — `종료` 는 보드에 없다
BOARD_COLUMNS = (
    ("waiting", "대기", ("대기", "새로 들어옴")),
    ("agent_working", "에이전트 작업 중", ("에이전트 작업 중",)),
    ("direct_working", "직접 작업 중", ("직접 작업 중",)),
    ("my_turn", "내 차례", ("내 차례",)),
    ("pr_review", "PR · 검토", ("PR · 검토",)),
    ("done", "완료", ("완료",)),
)

_QUESTION_MAX = 80


@dataclass(frozen=True)
class ListQuery:
    q: str
    group: str
    view: str
    closed: str
    open_key: int | None
    repo: str | None = None  # 워크스페이스 저장소 목록의 값만


@dataclass(frozen=True)
class WorkRow:
    work_item_id: str
    key_number: int
    work_key: str
    source_type: str
    source_key: str | None
    source_url: str | None
    title: str
    assignee_type: str | None
    assignee_id: str | None
    assignee_name: str | None
    assignee_active: bool
    priority: str
    kind: str
    kind_label: str
    status: str
    status_reason: str
    next_action: str
    recipients: tuple[str, ...]  # `내 차례` 일 때 받는 사람 member_id 들
    updated_at: str
    closed_at: str | None
    repository: str | None = None  # GitHub 원본의 `owner/name`, 그 밖은 None
    source_key_short: str | None = None  # `work_keys.short_source_key(source_key)`
    triage: str | None = None  # 최신 판단 로그 — `판단 중`·`판단 제안`(처리 전)·`판단 실패`, 그 밖 None (phase 19)
    # 최신 결과 뒤 판단 — `다음 단계 판단 중`·`다음 단계 제안`(원인 그대로·처리 전)·`사내 요청 대기`, 그 밖 None (phase 22).
    # `next_action`(지금 할 일 문구)과 다르다
    next_step: str | None = None


@dataclass(frozen=True)
class RowGroup:
    key: str
    label: str
    rows: tuple[WorkRow, ...]


@dataclass(frozen=True)
class BoardColumn:
    key: str
    label: str
    rows: tuple[WorkRow, ...]


def _pick(value: str, allowed: tuple[str, ...]) -> str:
    return value if value in allowed else allowed[0]


def parse_list_query(*, q: str = "", group: str = "", view: str = "", closed: str = "", open: str = "",
                     repo: str = "", repos: Sequence[str] = ()) -> ListQuery:
    """주소 쿼리 값 → 열거형. 모르는 값·빈 값은 기본값(첫 값). 15 의 `view=my_turn` 은 `q=my_turn` 으로 읽는다.
    `repo` 는 `repos`(워크스페이스 저장소 목록)와 대소문자 무시로 같을 때만 목록 표기로, 아니면 None."""
    if view == "my_turn":
        q = "my_turn"
    open_key = None
    if re.fullmatch(WORK_KEY_PATTERN, open) and open.startswith(f"{WORK_KEY_PREFIX}-"):
        open_key = int(open.removeprefix(f"{WORK_KEY_PREFIX}-"))
    picked_repo = next((name for name in repos if name.lower() == repo.lower()), None) if repo else None
    return ListQuery(q=_pick(q, QUICK_FILTERS), group=_pick(group, GROUP_BYS), view=_pick(view, VIEWS),
                     closed=_pick(closed, CLOSED_SCOPES), open_key=open_key, repo=picked_repo)


def next_action(*, request_question: str | None, direct_member_name: str | None, pr_label: str | None,
                status_reason: str) -> str:
    """"다음 할 일" 칸 — 열린 사람 요청 질문 첫 줄 → 직접 작업 → 업무 PR → 상태 이유 순의 첫 일치."""
    if request_question is not None:
        return request_question.split("\n", 1)[0][:_QUESTION_MAX]
    if direct_member_name is not None:
        return f"직접 작업 중 · {direct_member_name}"
    if pr_label is not None:
        return pr_label
    return status_reason


def _open(row: WorkRow) -> bool:
    return row.status not in TERMINAL_WORK_STATUSES


def _matches(row: WorkRow, q: str, member_id: str) -> bool:
    if q == "my_turn":
        return row.status == "내 차례" and member_id in row.recipients
    if q == "unassigned":
        return row.assignee_type is None and _open(row)
    if q == "agent_working":
        return row.assignee_type == "agent" and _open(row)
    return True


def filter_rows(rows, q: str, *, member_id: str, repo: str | None = None) -> list[WorkRow]:
    """저장소 필터(`repo` 가 있으면 그 저장소만) → 빠른 필터."""
    q = _pick(q, QUICK_FILTERS)
    return [r for r in rows if (repo is None or r.repository == repo) and _matches(r, q, member_id)]


def filter_counts(rows, *, member_id: str, repo: str | None = None) -> dict[str, int]:
    return {q: len(filter_rows(rows, q, member_id=member_id, repo=repo)) for q in QUICK_FILTERS}


def _in_group_order(rows) -> tuple[WorkRow, ...]:
    """우선순위(high → low) → `updated_at` 최근순 → 키 번호 내림차순."""
    rows = sorted(rows, key=lambda r: (r.updated_at, r.key_number), reverse=True)
    return tuple(sorted(rows, key=lambda r: PRIORITY_ORDER.index(r.priority)))


def _assignee_group(row: WorkRow, member_id: str) -> tuple[tuple, str, str]:
    """(정렬 키, 묶음 키, 표시 이름)."""
    if row.assignee_type is None:
        return (0,), "none", "담당 없음"
    name = row.assignee_name or row.assignee_id
    key = f"{row.assignee_type}:{row.assignee_id}"
    if row.assignee_type == "agent":
        return (3, name, row.assignee_id), key, name
    if row.assignee_id == member_id:
        return (1,), key, f"{name} (나)"
    if row.assignee_active:
        return (2, name, row.assignee_id), key, name
    return (4, name, row.assignee_id), key, f"{name} (비활성)"


def _repo_group(row: WorkRow) -> tuple[tuple, str, str]:
    """저장소 이름 대소문자 무시 순, `저장소 없음` 은 마지막."""
    if row.repository is None:
        return (1,), "repo:none", "저장소 없음"
    return (0, row.repository.lower(), row.repository), f"repo:{row.repository}", row.repository


def group_rows(rows, by: str, *, member_id: str) -> list[RowGroup]:
    """묶음 목록. 행이 있는 묶음만 만든다."""
    by = _pick(by, GROUP_BYS)
    buckets: dict[str, tuple[tuple, str, list[WorkRow]]] = {}
    for row in rows:
        if by == "status":
            order, key, label = (WORK_STATUSES.index(row.status),), f"status:{row.status}", row.status
        elif by == "repo":
            order, key, label = _repo_group(row)
        else:
            order, key, label = _assignee_group(row, member_id)
        buckets.setdefault(key, (order, label, []))[2].append(row)
    return [RowGroup(key=key, label=label, rows=_in_group_order(members))
            for key, (_, label, members) in sorted(buckets.items(), key=lambda item: item[1][0])]


def board_columns(rows) -> list[BoardColumn]:
    """보드 6칸(빈 칸 포함, 묶기와 무관). 칸 안 순서는 묶음 안 순서와 같다."""
    return [BoardColumn(key=key, label=label, rows=_in_group_order(r for r in rows if r.status in statuses))
            for key, label, statuses in BOARD_COLUMNS]
