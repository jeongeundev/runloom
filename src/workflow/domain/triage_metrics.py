"""판단 품질 순수 계산 — ARCHITECTURE "모니터링 — phase 20" 판단 품질 지표 정의·확신도 구간·기준값 미리보기.

판단 로그 사실과 업무 결과 사실을 받아 묶음(전체·제안 종류별·기준 버전별)마다 n·비율·모름을 낸다. 현재 시각·DB 를
보지 않는다. 결과 업무의 사실(상태·병합·재작업)은 기간과 무관하게 지금 값이다. `Stat`·`Ratio` 는 phase 9 것.
"""

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from workflow.domain.metrics import ALL_GROUP, UNKNOWN, Ratio, Stat, _in_window, _seconds, _stat

# (키, 아래 끝, 위 끝) — 마지막 구간만 위 끝을 포함한다
CONFIDENCE_BUCKETS: tuple[tuple[str, float, float], ...] = (
    ("0-0.5", 0.0, 0.5),
    ("0.5-0.7", 0.5, 0.7),
    ("0.7-0.8", 0.7, 0.8),
    ("0.8-0.9", 0.8, 0.9),
    ("0.9-1", 0.9, 1.0),
)
HANDLINGS = ("accepted", "changed", "dismissed", "auto_started")
UNHANDLED = "unhandled"
PROCEEDS = ("ready", "needs_check", "unsuitable")

_PROPOSED = "proposed"
_TERMINAL = ("result_ready", "failed")
_TARGET_HANDLINGS = ("accepted", "auto_started")
_DONE = "완료"
_FINISHED = ("완료", "종료")


# ── 입력 ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class TriageLogFact:
    triage_id: str
    work_item_id: str
    kind: str | None  # proposed_kind. 실패·도는 중이면 None
    criteria_version: int
    trigger: str
    state: str  # running | proposed | failed | superseded
    proceed: str | None
    confidence: float | None
    failed_code: str | None
    handling: str | None  # accepted | changed | dismissed | auto_started. None = 미처리
    created_at: str
    work_revision: int  # 판단 시작 때 업무 revision
    work_revision_now: int
    execution_status: str  # 판단 실행 상태
    started_at: str | None  # 판단 실행 시작·끝
    finished_at: str | None
    cost_usd: float | None


@dataclass(frozen=True)
class WorkOutcomeFact:
    work_item_id: str
    status: str  # USER_STATUS_LABELS
    closed_at: str | None
    merged: bool  # 판단 단계가 아닌 단계의 PR 또는 감지 PR 이 병합됨
    rework_runs: int  # 판단 단계가 아닌 실행 중 start_key `rework:`


# ── 결과 ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class TriageQuality:
    key: str  # ALL_GROUP, 종류(없으면 UNKNOWN), 또는 `v<n>`
    proposed: int
    running: int
    failed: int
    failed_codes: Mapping[str, int]
    superseded: int
    handling: Mapping[str, int]  # HANDLINGS + UNHANDLED
    agreement: Ratio  # accepted / (accepted + changed), incomplete = 미처리
    proceed: Mapping[str, int]
    merged: Ratio  # 끝난 실제 결과 대상 중 병합 완료
    merged_without_rework: Ratio
    triage_time: Stat
    cost_usd: Stat
    revised_after: int  # 판단 뒤 내용이 바뀐 업무 수


@dataclass(frozen=True)
class ConfidenceBucket:
    key: str
    low: float
    high: float
    proposed: int
    agreement: Ratio
    merged: Ratio
    merged_without_rework: Ratio


@dataclass(frozen=True)
class TriageQualityReport:
    since: str | None
    until: str | None
    overall: TriageQuality
    by_kind: tuple[TriageQuality, ...]
    by_criteria: tuple[TriageQuality, ...]  # 키 `v<n>`, 버전 순
    buckets: tuple[ConfidenceBucket, ...]


@dataclass(frozen=True)
class AutostartPreview:
    kind: str
    threshold: float
    proposed: int
    agreement: Ratio
    merged: Ratio


# ── 계산 ─────────────────────────────────────────────────────────────


def _proposed(logs: Sequence[TriageLogFact]) -> list[TriageLogFact]:
    return [x for x in logs if x.state == _PROPOSED]


def _agreement(proposed: Sequence[TriageLogFact]) -> Ratio:
    handled = Counter(x.handling for x in proposed)
    return Ratio(handled["accepted"], handled["accepted"] + handled["changed"], incomplete=handled[None])


def _results(proposed: Sequence[TriageLogFact], outcomes: Mapping[str, WorkOutcomeFact]) -> tuple[Ratio, Ratio]:
    """실제 결과 ①·② — 대상 = `ready` 이고 제안대로·자동 시작으로 맡긴 제안. 끝난 업무가 분모."""
    finished = merged = clean = incomplete = unknown = 0
    for x in proposed:
        if x.proceed != "ready" or x.handling not in _TARGET_HANDLINGS:
            continue
        work = outcomes.get(x.work_item_id)
        if work is None:
            unknown += 1
        elif work.status not in _FINISHED:
            incomplete += 1
        else:
            finished += 1
            if work.status == _DONE and work.merged:
                merged += 1
                clean += work.rework_runs == 0
    return (Ratio(merged, finished, incomplete=incomplete, unknown=unknown),
            Ratio(clean, finished, incomplete=incomplete, unknown=unknown))


def _quality(key: str, logs: Sequence[TriageLogFact], outcomes: Mapping[str, WorkOutcomeFact]) -> TriageQuality:
    states = Counter(x.state for x in logs)
    proposed = _proposed(logs)
    handled = Counter(x.handling for x in proposed)
    proceeds = Counter(x.proceed for x in proposed)
    merged, merged_without_rework = _results(proposed, outcomes)
    finished = [x for x in logs if x.execution_status in _TERMINAL]
    durations = [_seconds(x.started_at, x.finished_at) for x in finished if x.started_at and x.finished_at]
    costs = [x.cost_usd for x in finished if x.cost_usd is not None]
    return TriageQuality(
        key=key,
        proposed=states[_PROPOSED],
        running=states["running"],
        failed=states["failed"],
        failed_codes=dict(Counter(x.failed_code or UNKNOWN for x in logs if x.state == "failed")),
        superseded=states["superseded"],
        handling={**{h: handled[h] for h in HANDLINGS}, UNHANDLED: handled[None]},
        agreement=_agreement(proposed),
        proceed={p: proceeds[p] for p in PROCEEDS},
        merged=merged,
        merged_without_rework=merged_without_rework,
        triage_time=_stat(durations, incomplete=len(logs) - len(finished), unknown=len(finished) - len(durations)),
        cost_usd=_stat(costs, incomplete=len(logs) - len(finished), unknown=len(finished) - len(costs),
                       with_total=True),
        revised_after=len({x.work_item_id for x in proposed if x.work_revision < x.work_revision_now}),
    )


def _bucket_of(confidence: float) -> str:
    for key, low, high in CONFIDENCE_BUCKETS[:-1]:
        if low <= confidence < high:
            return key
    return CONFIDENCE_BUCKETS[-1][0]


def _outcome_index(outcomes: Sequence[WorkOutcomeFact]) -> dict[str, WorkOutcomeFact]:
    return {o.work_item_id: o for o in outcomes}


def confidence_buckets(
    logs: Sequence[TriageLogFact], outcomes: Sequence[WorkOutcomeFact]
) -> tuple[ConfidenceBucket, ...]:
    """확신도가 있는 제안 행만 구간에 넣는다. 받은 행을 거르지 않는다(기간은 호출자가)."""
    index = _outcome_index(outcomes)
    grouped: dict[str, list[TriageLogFact]] = {key: [] for key, _, _ in CONFIDENCE_BUCKETS}
    for x in _proposed(logs):
        if x.confidence is not None:
            grouped[_bucket_of(x.confidence)].append(x)
    buckets = []
    for key, low, high in CONFIDENCE_BUCKETS:
        rows = grouped[key]
        merged, merged_without_rework = _results(rows, index)
        buckets.append(ConfidenceBucket(key=key, low=low, high=high, proposed=len(rows),
                                        agreement=_agreement(rows), merged=merged,
                                        merged_without_rework=merged_without_rework))
    return tuple(buckets)


def compute_triage_quality(
    logs: Sequence[TriageLogFact], outcomes: Sequence[WorkOutcomeFact], *, since: str | None, until: str | None
) -> TriageQualityReport:
    """기간(`since` 이상 `until` 미만)은 판단 로그 `created_at` 으로 거른다. 종류 NULL 은 `UNKNOWN` 묶음."""
    index = _outcome_index(outcomes)
    window = [x for x in logs if _in_window(x.created_at, since, until)]
    by_kind: dict[str, list[TriageLogFact]] = {}
    by_criteria: dict[int, list[TriageLogFact]] = {}
    for x in window:
        by_kind.setdefault(x.kind or UNKNOWN, []).append(x)
        by_criteria.setdefault(x.criteria_version, []).append(x)
    kinds = sorted(by_kind, key=lambda k: (k == UNKNOWN, k))
    return TriageQualityReport(
        since=since,
        until=until,
        overall=_quality(ALL_GROUP, window, index),
        by_kind=tuple(_quality(k, by_kind[k], index) for k in kinds),
        by_criteria=tuple(_quality(f"v{v}", by_criteria[v], index) for v in sorted(by_criteria)),
        buckets=confidence_buckets(window, outcomes),
    )


def autostart_preview(
    logs: Sequence[TriageLogFact], outcomes: Sequence[WorkOutcomeFact], *, kind: str, threshold: float
) -> AutostartPreview:
    """그 종류의 `confidence >= threshold` 제안 전부(기간·기준 버전 무관). 병합 = 실제 결과 ①."""
    rows = [x for x in _proposed(logs) if x.kind == kind and x.confidence is not None and x.confidence >= threshold]
    merged, _ = _results(rows, _outcome_index(outcomes))
    return AutostartPreview(kind=kind, threshold=threshold, proposed=len(rows), agreement=_agreement(rows),
                            merged=merged)
