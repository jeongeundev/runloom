"""지표 순수 계산 — ARCHITECTURE "측정 — phase 9" 지표 정의 표, ADR-0015 8항.

DB 행이 아닌 값 객체를 받아 중앙값·n·미완료·모름을 낸다. 현재 시각·DB·HTTP 를 보지 않는다. 모르는 값
(NULL)은 0 으로 채우지 않고 `unknown` 으로 따로 센다. 시각은 RFC 3339 문자열.

업무 묶음 = `predecessor_task_id` 를 따라 올라간 선행 없는 시작 Task 와 그 후속들.
"""

from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from statistics import median
from typing import Any, Literal

GroupBy = Literal["config_revision", "folder_commit"]

ALL_GROUP = "all"  # group_by 가 없을 때 그룹 하나의 키
UNKNOWN = "unknown"  # 그룹 값·실패 코드가 NULL 인 것
ACTORS = ("operator", "assignee", "system")
BASELINE_NOTE = "하네스·Claude 사용 시기 이력 — 순수 수작업 기준 아님"

_TERMINAL = ("result_ready", "failed")
_HUMAN_TURN = "확인 필요"
_DONE = "완료"
_CLOSED = "closed"
_FAILED = "실패"
_REVIEW_KIND = "code_review"
_REWORK_PREFIX = "rework:"


# ── 입력 ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class TaskFact:
    task_id: str
    kind: str
    created_at: str
    status: str  # USER_STATUS_LABELS
    predecessor_task_id: str | None = None
    issue_opened_at: str | None = None  # 원본 이슈 스냅샷의 created_at. 없으면 None
    issue_state: str | None = None  # 원본 이슈 상태(open·closed). None = 직접 등록 Task
    pr_merged_at: str | None = None  # 원본 이슈를 닫은 병합 PR 의 병합 시각(source_issues). None = 모름/병합 없음
    merge_checked_at: str | None = None  # 병합 PR 을 마지막으로 조회한 시각. None = 조회 전
    finished_at: str | None = None
    review_decision: str | None = None  # tasks.review_decision(마지막 값)


@dataclass(frozen=True)
class ExecutionFact:
    execution_id: str
    task_id: str
    kind: str
    attempt_no: int
    status: str
    start_key: str
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None
    failed_code: str | None = None
    outcome: str | None = None  # 판정·검토 결과 outcome. 없으면 None
    config_revision: int | None = None
    folder_commit: str | None = None
    cost_usd: float | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None


@dataclass(frozen=True)
class TaskEventFact:
    task_id: str
    type: str  # status_changed | blocked | ready
    occurred_at: str
    data: Mapping[str, Any]


@dataclass(frozen=True)
class HumanRequestFact:
    request_id: str
    task_id: str
    created_at: str
    answered_at: str | None = None


@dataclass(frozen=True)
class MetricFacts:
    tasks: Sequence[TaskFact] = ()
    executions: Sequence[ExecutionFact] = ()
    events: Sequence[TaskEventFact] = ()  # task_events.id 순
    human_requests: Sequence[HumanRequestFact] = ()


@dataclass(frozen=True)
class BaselineItemFact:
    issue_number: int
    issue_opened_at: str
    pr_number: int
    pr_merged_at: str


# ── 결과 ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Stat:
    median: float | None  # 초·달러·토큰·건수. 표본 0 이면 None
    n: int
    incomplete: int = 0  # 끝나지 않아 뺀 건수
    unknown: int = 0  # 값을 몰라 뺀 건수
    total: float | None = None  # 합계를 내는 지표(비용·토큰·건수·대기 구간)만. 표본 0 이면 None


@dataclass(frozen=True)
class Ratio:
    numerator: int
    denominator: int
    incomplete: int = 0
    unknown: int = 0

    @property
    def rate(self) -> float | None:
        return self.numerator / self.denominator if self.denominator else None


@dataclass(frozen=True)
class MetricsGroup:
    key: str  # ALL_GROUP, 그룹 값(문자열), 또는 UNKNOWN
    bundles: int
    handoff_wait: Stat
    handoff_blocked: Mapping[str, Stat]  # actor → 후속 Task 당 대기 구간 합
    intake_to_human: Stat
    intake_to_done: Stat
    intake_to_merge: Stat  # GitHub 이슈 묶음만 — 기준선과 같은 구간(이슈 열림 → 병합)
    intake_to_approval: Stat
    done_by_finished_at: int  # 완료 시각을 v6 이전 finished_at 으로 대신한 묶음
    closed_failed: int  # 실패로 마감된 묶음(intake_to_done 미완료에 포함)
    closed_unmerged: int  # 원본 이슈가 병합 PR 없이 닫힌 묶음(조회로 확인, intake_to_done 미완료에 포함)
    interventions: Stat
    response_time: Stat
    first_pass: Ratio
    rework: Stat
    human_rejection: Ratio
    execution_time: Stat
    cost_usd: Stat
    input_tokens: Stat
    output_tokens: Stat
    failure: Ratio
    failed_codes: Mapping[str, int]
    reruns: Ratio


@dataclass(frozen=True)
class MetricsReport:
    since: str | None
    until: str | None
    group_by: GroupBy | None
    groups: tuple[MetricsGroup, ...]


@dataclass(frozen=True)
class BaselineSummary:
    intake_to_merge: Stat  # n = 이슈 수
    opened_before: str
    fetched_at: str
    note: str = BASELINE_NOTE


# ── 계산 ─────────────────────────────────────────────────────────────


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _seconds(start: str, end: str) -> float:
    return (_parse(end) - _parse(start)).total_seconds()


def _stat(values: Sequence[float], *, incomplete: int = 0, unknown: int = 0, with_total: bool = False) -> Stat:
    return Stat(
        median=median(values) if values else None,
        n=len(values),
        incomplete=incomplete,
        unknown=unknown,
        total=sum(values) if with_total and values else None,
    )


def _earliest(values: Iterable[str | None]) -> str | None:
    known = [v for v in values if v is not None]
    return min(known, key=_parse) if known else None


@dataclass
class _Bundle:
    root: TaskFact
    tasks: list[TaskFact] = field(default_factory=list)

    @property
    def intake_at(self) -> str:
        return self.root.issue_opened_at or self.root.created_at


class _Index:
    def __init__(self, facts: MetricFacts) -> None:
        self.tasks = {t.task_id: t for t in facts.tasks}
        self.executions: dict[str, list[ExecutionFact]] = {}
        for e in facts.executions:
            self.executions.setdefault(e.task_id, []).append(e)
        self.events: dict[str, list[TaskEventFact]] = {}
        for ev in facts.events:
            self.events.setdefault(ev.task_id, []).append(ev)
        self.requests: dict[str, list[HumanRequestFact]] = {}
        for r in facts.human_requests:
            self.requests.setdefault(r.task_id, []).append(r)

    def root_of(self, task: TaskFact) -> TaskFact:
        seen = {task.task_id}
        while task.predecessor_task_id in self.tasks and task.predecessor_task_id not in seen:
            task = self.tasks[task.predecessor_task_id]
            seen.add(task.task_id)
        return task

    def bundles(self) -> list[_Bundle]:
        by_root: dict[str, _Bundle] = {}
        for task in self.tasks.values():
            root = self.root_of(task)
            by_root.setdefault(root.task_id, _Bundle(root)).tasks.append(task)
        return list(by_root.values())

    def first_start(self, task_id: str) -> str | None:
        return _earliest(e.started_at for e in self.executions.get(task_id, ()))


def _in_window(at: str, since: str | None, until: str | None) -> bool:
    moment = _parse(at)
    return (since is None or moment >= _parse(since)) and (until is None or moment < _parse(until))


def _group_key(value: int | str | None, group_by: GroupBy | None) -> str:
    if group_by is None:
        return ALL_GROUP
    return UNKNOWN if value is None else str(value)


def _execution_key(e: ExecutionFact, group_by: GroupBy | None) -> str:
    if group_by is None:
        return ALL_GROUP
    return _group_key(getattr(e, group_by), group_by)


def _bundle_key(bundle: _Bundle, index: _Index, group_by: GroupBy | None) -> str:
    if group_by is None:
        return ALL_GROUP
    executions = [e for t in bundle.tasks for e in index.executions.get(t.task_id, ())]
    if not executions:
        return UNKNOWN
    first = min(executions, key=lambda e: _parse(e.created_at))
    return _group_key(getattr(first, group_by), group_by)


def _sort_keys(keys: Iterable[str], group_by: GroupBy | None) -> list[str]:
    known = sorted((k for k in keys if k != UNKNOWN), key=(int if group_by == "config_revision" else str))
    return known + ([UNKNOWN] if UNKNOWN in keys else [])


def _blocked_seconds(events: Sequence[TaskEventFact], end: str) -> dict[str, float]:
    """`blocked` 행이 연 구간을 다음 `blocked`·`ready` 또는 첫 시작(`end`)까지 actor 별로 더한다."""
    totals = dict.fromkeys(ACTORS, 0.0)
    marks = [ev for ev in events if ev.type in ("blocked", "ready") and _parse(ev.occurred_at) < _parse(end)]
    if not marks:  # 만든 그 초에 시작 — 시작 전 대기 표시가 없다
        return totals
    for current, following in zip(marks, [*marks[1:], None], strict=True):
        if current.type != "blocked":
            continue
        stop = following.occurred_at if following is not None else end
        span = _seconds(current.occurred_at, stop)
        for actor in {b["actor"] for b in current.data.get("blockers", ())}:
            totals[actor] = totals.get(actor, 0.0) + span
    return totals


def _bundle_metrics(bundles: Sequence[_Bundle], index: _Index) -> dict[str, Any]:
    handoff: list[float] = []
    handoff_incomplete = 0
    blocked: dict[str, list[float]] = {actor: [] for actor in ACTORS}
    unsplit = 0
    to_human: list[float] = []
    to_human_incomplete = 0
    to_done: list[float] = []
    done_by_finished_at = 0
    closed_failed = 0
    closed_unmerged = 0
    to_done_incomplete = 0
    to_merge: list[float] = []
    to_merge_incomplete = 0
    to_approval: list[float] = []
    to_approval_incomplete = 0
    interventions: list[float] = []
    response: list[float] = []
    response_incomplete = 0
    first_pass_yes = first_pass_total = first_pass_missing = 0
    rework: list[float] = []
    decisions = rejected = decisions_unknown = 0

    for bundle in bundles:
        events = [ev for t in bundle.tasks for ev in index.events.get(t.task_id, ())]
        requests = [r for t in bundle.tasks for r in index.requests.get(t.task_id, ())]
        executions = [e for t in bundle.tasks for e in index.executions.get(t.task_id, ())]
        intake = bundle.intake_at

        # 인계 대기 — 후속 Task 생성 → 첫 시작
        for task in bundle.tasks:
            if task.predecessor_task_id is None:
                continue
            start = index.first_start(task.task_id)
            if start is None:
                handoff_incomplete += 1
                continue
            handoff.append(_seconds(task.created_at, start))
            task_events = index.events.get(task.task_id, ())
            if not task_events:
                unsplit += 1
                continue
            for actor, seconds in _blocked_seconds(task_events, start).items():
                blocked.setdefault(actor, []).append(seconds)

        # 접수 → 사람 차례
        human_at = _earliest([
            *(r.created_at for r in requests),
            *(ev.occurred_at for ev in events if ev.type == "status_changed" and ev.data.get("to") == _HUMAN_TURN),
        ])
        if human_at is None:
            to_human_incomplete += 1
        else:
            to_human.append(_seconds(intake, human_at))

        # 접수 → 완료 — GitHub 이슈 묶음은 원본 이슈를 닫은 PR 의 병합 시각만(승인 시각은 쓰지 않는다).
        # 직접 등록은 시작 Task 가 `완료` 로 바뀐 시각, v6 이전은 finished_at (진단 데모의 병합 확인은 `main` 전용 — ADR-0019)
        root = bundle.root
        if root.issue_state is not None:
            done_at = root.pr_merged_at
            if done_at is None:
                to_merge_incomplete += 1
                closed_unmerged += root.issue_state == _CLOSED and root.merge_checked_at is not None
            else:
                to_merge.append(_seconds(intake, done_at))
        else:
            done_at = _earliest(
                ev.occurred_at for ev in index.events.get(root.task_id, ())
                if ev.type == "status_changed" and ev.data.get("to") == _DONE
            )
            if done_at is None and root.status == _DONE and root.finished_at is not None:
                done_at = root.finished_at
                done_by_finished_at += 1
        if done_at is None:
            to_done_incomplete += 1
            closed_failed += root.status == _FAILED
        else:
            to_done.append(_seconds(intake, done_at))

        # 접수 → 승인 — 묶음에서 처음 운영자 승인 또는 `완료` 로 바뀐 시각
        approved_at = _earliest(
            ev.occurred_at for ev in events
            if ev.type == "status_changed" and (ev.data.get("review_decision") == "approve" or ev.data.get("to") == _DONE)
        )
        if approved_at is None:
            to_approval_incomplete += 1
        else:
            to_approval.append(_seconds(intake, approved_at))

        # 사람 부담
        decision_events = [ev for ev in events if ev.type == "status_changed" and ev.data.get("review_decision")]
        interventions.append(len(requests) + len(decision_events))
        for r in requests:
            if r.answered_at is None:
                response_incomplete += 1
            else:
                response.append(_seconds(r.created_at, r.answered_at))

        # 품질
        reviews = [e for e in executions if e.kind == _REVIEW_KIND and e.outcome is not None]
        if reviews:
            first_pass_total += 1
            first_pass_yes += min(reviews, key=lambda e: _parse(e.created_at)).outcome == "approved"
        else:
            first_pass_missing += 1
        rework.append(sum(1 for e in executions if e.start_key.startswith(_REWORK_PREFIX)))
        decisions += len(decision_events)
        rejected += sum(1 for ev in decision_events if ev.data["review_decision"] in ("request_changes", "close"))
        decided_tasks = {ev.task_id for ev in decision_events}
        decisions_unknown += sum(1 for t in bundle.tasks if t.review_decision and t.task_id not in decided_tasks)

    return {
        "bundles": len(bundles),
        "handoff_wait": _stat(handoff, incomplete=handoff_incomplete),
        "handoff_blocked": {a: _stat(v, unknown=unsplit, with_total=True) for a, v in blocked.items()},
        "intake_to_human": _stat(to_human, incomplete=to_human_incomplete),
        "intake_to_done": _stat(to_done, incomplete=to_done_incomplete),
        "intake_to_merge": _stat(to_merge, incomplete=to_merge_incomplete),
        "intake_to_approval": _stat(to_approval, incomplete=to_approval_incomplete),
        "done_by_finished_at": done_by_finished_at,
        "closed_failed": closed_failed,
        "closed_unmerged": closed_unmerged,
        "interventions": _stat(interventions, with_total=True),
        "response_time": _stat(response, incomplete=response_incomplete),
        "first_pass": Ratio(first_pass_yes, first_pass_total, incomplete=first_pass_missing),
        "rework": _stat(rework, with_total=True),
        "human_rejection": Ratio(rejected, decisions, unknown=decisions_unknown),
    }


def _amount(executions: Sequence[ExecutionFact], name: str) -> Stat:
    finished = [e for e in executions if e.status in _TERMINAL]
    values = [getattr(e, name) for e in finished if getattr(e, name) is not None]
    return _stat(values, incomplete=len(executions) - len(finished), unknown=len(finished) - len(values),
                 with_total=True)


def _execution_metrics(executions: Sequence[ExecutionFact]) -> dict[str, Any]:
    finished = [e for e in executions if e.status in _TERMINAL]
    unfinished = len(executions) - len(finished)
    durations = [_seconds(e.started_at, e.finished_at) for e in finished if e.started_at and e.finished_at]
    failed = [e for e in finished if e.status == "failed"]
    return {
        "execution_time": _stat(durations, incomplete=unfinished, unknown=len(finished) - len(durations)),
        "cost_usd": _amount(executions, "cost_usd"),
        "input_tokens": _amount(executions, "input_tokens"),
        "output_tokens": _amount(executions, "output_tokens"),
        "failure": Ratio(len(failed), len(finished), incomplete=unfinished),
        "failed_codes": dict(Counter(e.failed_code or UNKNOWN for e in failed)),
        "reruns": Ratio(sum(1 for e in executions if e.attempt_no > 1), len(executions)),
    }


def compute_metrics(
    facts: MetricFacts, *, since: str | None, until: str | None, group_by: GroupBy | None
) -> MetricsReport:
    """기간(`since` 이상 `until` 미만)은 묶음 지표는 접수 시각, 실행 지표는 `created_at` 으로 거른다.

    `group_by` 가 있으면 실행 지표는 그 실행의 값, 묶음 지표는 묶음 첫 실행의 값으로 나누고 NULL 은
    `UNKNOWN` 그룹 하나로 모은다. 없으면 `ALL_GROUP` 하나.
    """
    if group_by not in (None, "config_revision", "folder_commit"):
        raise ValueError(f"group_by 는 config_revision 또는 folder_commit: {group_by!r}")
    index = _Index(facts)
    bundles: dict[str, list[_Bundle]] = {}
    for bundle in index.bundles():
        if _in_window(bundle.intake_at, since, until):
            bundles.setdefault(_bundle_key(bundle, index, group_by), []).append(bundle)
    executions: dict[str, list[ExecutionFact]] = {}
    for e in facts.executions:
        if _in_window(e.created_at, since, until):
            executions.setdefault(_execution_key(e, group_by), []).append(e)

    keys = set(bundles) | set(executions)
    if group_by is None:
        keys = {ALL_GROUP}
    groups = tuple(
        MetricsGroup(
            key=key,
            **_bundle_metrics(bundles.get(key, []), index),
            **_execution_metrics(executions.get(key, [])),
        )
        for key in _sort_keys(keys, group_by)
    )
    return MetricsReport(since=since, until=until, group_by=group_by, groups=groups)


def summarize_baseline(items: Sequence[BaselineItemFact], *, opened_before: str, fetched_at: str) -> BaselineSummary:
    """이슈별 가장 이른 `pr_merged_at` − `issue_opened_at` 의 중앙값. n = 이슈 수."""
    earliest: dict[int, float] = {}
    for item in items:
        seconds = _seconds(item.issue_opened_at, item.pr_merged_at)
        earliest[item.issue_number] = min(seconds, earliest.get(item.issue_number, seconds))
    return BaselineSummary(
        intake_to_merge=_stat(list(earliest.values())), opened_before=opened_before, fetched_at=fetched_at
    )
