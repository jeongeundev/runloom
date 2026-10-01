"""판단 품질 순수 계산 — ARCHITECTURE "모니터링 — phase 20" 판단 품질 지표 정의·확신도 구간·기준값 미리보기."""

from dataclasses import replace
from itertools import count

import pytest

from workflow.domain.metrics import UNKNOWN, Ratio, Stat
from workflow.domain.triage_metrics import (
    CONFIDENCE_BUCKETS,
    TriageLogFact,
    WorkOutcomeFact,
    autostart_preview,
    compute_triage_quality,
    confidence_buckets,
)

_ids = count(1)


def log(**overrides) -> TriageLogFact:
    n = next(_ids)
    base = TriageLogFact(
        triage_id=f"tr-{n}",
        work_item_id=f"w-{n}",
        kind="bug_fix",
        criteria_version=1,
        trigger="auto",
        state="proposed",
        proceed="ready",
        confidence=0.85,
        failed_code=None,
        handling=None,
        created_at="2026-10-01T00:00:00Z",
        work_revision=1,
        work_revision_now=1,
        execution_status="result_ready",
        started_at="2026-10-01T00:00:00Z",
        finished_at="2026-10-01T00:01:00Z",
        cost_usd=0.1,
    )
    return replace(base, **overrides)


def outcome(work_item_id: str, status: str, *, merged: bool = False, rework_runs: int = 0) -> WorkOutcomeFact:
    closed = "2026-10-02T00:00:00Z" if status in ("완료", "종료") else None
    return WorkOutcomeFact(work_item_id=work_item_id, status=status, closed_at=closed, merged=merged,
                           rework_runs=rework_runs)


def report(logs, outcomes=(), *, since=None, until=None):
    return compute_triage_quality(logs, outcomes, since=since, until=until)


def test_empty_input():
    r = report([])
    q = r.overall
    assert q.key == "all"
    assert (q.proposed, q.running, q.failed, q.superseded, q.revised_after) == (0, 0, 0, 0, 0)
    assert q.failed_codes == {}
    assert q.handling == {"accepted": 0, "changed": 0, "dismissed": 0, "auto_started": 0, "unhandled": 0}
    assert q.proceed == {"ready": 0, "needs_check": 0, "unsuitable": 0}
    assert q.agreement == Ratio(0, 0) and q.agreement.rate is None
    assert q.merged == Ratio(0, 0) and q.merged.rate is None
    assert q.merged_without_rework == Ratio(0, 0)
    assert q.triage_time == Stat(median=None, n=0)
    assert q.cost_usd == Stat(median=None, n=0)
    assert r.by_kind == () and r.by_criteria == ()
    assert [b.key for b in r.buckets] == [key for key, _, _ in CONFIDENCE_BUCKETS]
    assert all(b.proposed == 0 and b.agreement.rate is None for b in r.buckets)


def test_states_and_failed_codes():
    logs = [
        log(),
        log(state="running", proceed=None, confidence=None, execution_status="running", finished_at=None),
        log(state="failed", proceed=None, confidence=None, failed_code="timeout", execution_status="failed"),
        log(state="failed", proceed=None, confidence=None, failed_code="timeout", execution_status="failed"),
        log(state="failed", proceed=None, confidence=None, failed_code="invalid_result", execution_status="failed"),
        log(state="superseded"),
    ]
    q = report(logs).overall
    assert (q.proposed, q.running, q.failed, q.superseded) == (1, 1, 3, 1)
    assert q.failed_codes == {"timeout": 2, "invalid_result": 1}


def test_agreement_excludes_dismissed_from_denominator():
    logs = (
        [log(handling="accepted") for _ in range(3)]
        + [log(handling="changed")]
        + [log(handling="dismissed") for _ in range(2)]
        + [log(handling="auto_started"), log(handling=None)]
    )
    q = report(logs).overall
    assert q.handling == {"accepted": 3, "changed": 1, "dismissed": 2, "auto_started": 1, "unhandled": 1}
    assert q.agreement.numerator == 3 and q.agreement.denominator == 4
    assert q.agreement.incomplete == 1  # 미처리 제안
    assert q.agreement.rate == pytest.approx(0.75)


def test_handling_and_proceed_count_proposed_only():
    logs = [
        log(proceed="ready"),
        log(proceed="needs_check"),
        log(proceed="unsuitable"),
        log(state="superseded", proceed="ready", handling="accepted"),
    ]
    q = report(logs).overall
    assert q.proceed == {"ready": 1, "needs_check": 1, "unsuitable": 1}
    assert q.handling["accepted"] == 0 and q.handling["unhandled"] == 3


def test_actual_result_mixed():
    merged = log(handling="accepted")
    reworked = log(handling="auto_started")
    closed = log(handling="accepted")
    done_without_pr = log(handling="accepted")
    running = log(handling="accepted")
    outcomes = [
        outcome(merged.work_item_id, "완료", merged=True),
        outcome(reworked.work_item_id, "완료", merged=True, rework_runs=1),
        outcome(closed.work_item_id, "종료"),
        outcome(done_without_pr.work_item_id, "완료"),
        outcome(running.work_item_id, "진행 중"),
    ]
    q = report([merged, reworked, closed, done_without_pr, running], outcomes).overall
    assert q.merged == Ratio(2, 4, incomplete=1)
    assert q.merged_without_rework == Ratio(1, 4, incomplete=1)


def test_needs_check_and_changed_are_outside_actual_result():
    logs = [
        log(proceed="needs_check", handling="accepted"),
        log(proceed="ready", handling="changed"),
        log(proceed="ready", handling="dismissed"),
        log(proceed="ready", handling=None),
    ]
    outcomes = [outcome(x.work_item_id, "완료", merged=True) for x in logs]
    q = report(logs, outcomes).overall
    assert q.merged == Ratio(0, 0)
    assert q.merged_without_rework == Ratio(0, 0)


def test_merged_flag_without_done_status_is_not_merge_complete():
    target = log(handling="accepted")
    q = report([target], [outcome(target.work_item_id, "종료", merged=True)]).overall
    assert q.merged == Ratio(0, 1)


def test_missing_outcome_is_unknown():
    q = report([log(handling="accepted")]).overall
    assert q.merged == Ratio(0, 0, unknown=1)


def test_confidence_bucket_boundaries():
    values = [0.0, 0.49, 0.5, 0.7, 0.8, 0.9, 1.0]
    buckets = confidence_buckets([log(confidence=c) for c in values], [])
    assert {b.key: b.proposed for b in buckets} == {
        "0-0.5": 2, "0.5-0.7": 1, "0.7-0.8": 1, "0.8-0.9": 1, "0.9-1": 2,
    }
    assert [(b.low, b.high) for b in buckets] == [(0.0, 0.5), (0.5, 0.7), (0.7, 0.8), (0.8, 0.9), (0.9, 1.0)]


def test_confidence_bucket_ratios_and_proposed_only():
    hit = log(confidence=0.95, handling="accepted")
    miss = log(confidence=0.92, handling="changed")
    running = log(state="running", confidence=None, proceed=None)
    superseded = log(state="superseded", confidence=0.95, handling="accepted")
    outcomes = [outcome(hit.work_item_id, "완료", merged=True, rework_runs=2)]
    top = confidence_buckets([hit, miss, running, superseded], outcomes)[-1]
    assert top.proposed == 2
    assert top.agreement == Ratio(1, 2)
    assert top.merged == Ratio(1, 1)
    assert top.merged_without_rework == Ratio(0, 1)


def test_by_kind_and_by_criteria():
    logs = [
        log(kind="bug_fix", criteria_version=1, handling="accepted"),
        log(kind="bug_fix", criteria_version=2, handling="changed"),
        log(kind="docs", criteria_version=2, handling="accepted"),
        log(kind=None, state="failed", proceed=None, confidence=None, failed_code="timeout",
            criteria_version=10),
    ]
    r = report(logs)
    assert [q.key for q in r.by_kind] == ["bug_fix", "docs", UNKNOWN]
    by_kind = {q.key: q for q in r.by_kind}
    assert by_kind["bug_fix"].proposed == 2 and by_kind["bug_fix"].agreement == Ratio(1, 2)
    assert by_kind[UNKNOWN].failed == 1
    assert [q.key for q in r.by_criteria] == ["v1", "v2", "v10"]
    by_criteria = {q.key: q for q in r.by_criteria}
    assert by_criteria["v1"].agreement == Ratio(1, 1)
    assert by_criteria["v2"].agreement == Ratio(1, 2)
    assert by_criteria["v2"].proposed == 2
    assert r.overall.proposed == 3


def test_window_filters_by_created_at_inclusive_start_exclusive_end():
    logs = [
        log(created_at="2026-09-30T23:59:59Z"),
        log(created_at="2026-10-01T00:00:00Z"),
        log(created_at="2026-10-01T12:00:00+09:00"),
        log(created_at="2026-10-02T00:00:00Z"),
    ]
    r = report(logs, since="2026-10-01T00:00:00Z", until="2026-10-02T00:00:00Z")
    assert r.overall.proposed == 2
    assert (r.since, r.until) == ("2026-10-01T00:00:00Z", "2026-10-02T00:00:00Z")
    assert sum(b.proposed for b in r.buckets) == 2


def test_triage_time():
    logs = [
        log(started_at="2026-10-01T00:00:00Z", finished_at="2026-10-01T00:01:00Z"),
        log(started_at="2026-10-01T00:00:00Z", finished_at="2026-10-01T00:03:00Z"),
        log(state="running", execution_status="running", finished_at=None, proceed=None, confidence=None),
        log(state="failed", execution_status="failed", started_at=None, failed_code="timeout",
            proceed=None, confidence=None),
    ]
    assert report(logs).overall.triage_time == Stat(median=120.0, n=2, incomplete=1, unknown=1)


def test_cost_null_is_unknown():
    logs = [
        log(cost_usd=0.1),
        log(cost_usd=0.3),
        log(cost_usd=None),
        log(state="running", execution_status="running", cost_usd=None, proceed=None, confidence=None),
    ]
    cost = report(logs).overall.cost_usd
    assert cost.n == 2 and cost.unknown == 1 and cost.incomplete == 1
    assert cost.median == pytest.approx(0.2)
    assert cost.total == pytest.approx(0.4)


def test_revised_after_counts_distinct_work_items():
    a = log(work_revision=1, work_revision_now=3)
    a_again = log(work_item_id=a.work_item_id, work_revision=2, work_revision_now=3)
    b = log(work_revision=2, work_revision_now=2)
    c = log(state="superseded", work_revision=1, work_revision_now=2)
    assert report([a, a_again, b, c]).overall.revised_after == 1


def test_autostart_preview_threshold_inclusive_and_kind_only():
    at = log(kind="bug_fix", confidence=0.8, handling="accepted")
    above = log(kind="bug_fix", confidence=0.9, handling="changed")
    auto = log(kind="bug_fix", confidence=1.0, handling="auto_started")
    below = log(kind="bug_fix", confidence=0.79, handling="accepted")
    other = log(kind="docs", confidence=0.95, handling="accepted")
    failed = log(kind="bug_fix", state="failed", confidence=None, proceed=None, failed_code="timeout")
    old = log(kind="bug_fix", confidence=0.85, criteria_version=1, created_at="2020-01-01T00:00:00Z")
    outcomes = [
        outcome(at.work_item_id, "완료", merged=True),
        outcome(auto.work_item_id, "진행 중"),
        outcome(below.work_item_id, "완료", merged=True),
        outcome(other.work_item_id, "완료", merged=True),
    ]
    p = autostart_preview([at, above, auto, below, other, failed, old], outcomes, kind="bug_fix", threshold=0.8)
    assert (p.kind, p.threshold) == ("bug_fix", 0.8)
    assert p.proposed == 4  # 기간·기준 버전으로 거르지 않는다
    assert p.agreement == Ratio(1, 2, incomplete=1)
    assert p.merged == Ratio(1, 1, incomplete=1)


def test_autostart_preview_empty():
    p = autostart_preview([], [], kind="bug_fix", threshold=0.9)
    assert p.proposed == 0 and p.agreement.rate is None and p.merged.rate is None
