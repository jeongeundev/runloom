"""지표 순수 계산 — ARCHITECTURE "측정 — phase 9" 지표 정의 표, ADR-0015 8항."""

import pytest

from workflow.domain.metrics import (
    ALL_GROUP,
    BASELINE_NOTE,
    UNKNOWN,
    BaselineItemFact,
    ExecutionFact,
    HumanRequestFact,
    MetricFacts,
    Ratio,
    Stat,
    TaskEventFact,
    TaskFact,
    compute_metrics,
    summarize_baseline,
)

H = 3600


def t(hour: int, minute: int = 0) -> str:
    return f"2026-09-27T{hour:02d}:{minute:02d}:00Z"


def task(task_id, *, created=t(0), status="대기", pred=None, **kw) -> TaskFact:
    return TaskFact(task_id=task_id, kind=kw.pop("kind", "code_change"), created_at=created, status=status,
                    predecessor_task_id=pred, **kw)


def exe(execution_id, task_id, *, created=t(0), status="result_ready", attempt=1, start_key=None, **kw):
    return ExecutionFact(execution_id=execution_id, task_id=task_id, kind=kw.pop("kind", "code_change"),
                         attempt_no=attempt, status=status, start_key=start_key or f"start:{execution_id}",
                         created_at=created, **kw)


def ev(task_id, type_, at, **data) -> TaskEventFact:
    return TaskEventFact(task_id=task_id, type=type_, occurred_at=at, data=data)


def compute(facts, *, since=None, until=None, group_by=None):
    return compute_metrics(facts, since=since, until=until, group_by=group_by)


def only(report):
    assert len(report.groups) == 1
    return report.groups[0]


# ── 빈 입력 ──────────────────────────────────────────────────────────


def test_empty_input_gives_one_group_with_no_samples():
    report = compute(MetricFacts())
    group = only(report)
    assert group.key == ALL_GROUP
    assert group.bundles == 0
    assert group.intake_to_done == Stat(median=None, n=0)
    assert group.execution_time == Stat(median=None, n=0)
    assert group.first_pass == Ratio(numerator=0, denominator=0)
    assert group.first_pass.rate is None
    assert group.failure.rate is None
    assert group.failed_codes == {}
    assert set(group.handoff_blocked) == {"operator", "assignee", "system"}


def test_empty_input_grouped_has_no_groups():
    assert compute(MetricFacts(), group_by="config_revision").groups == ()


# ── 중앙값: 짝수·홀수, 미완료 제외 ────────────────────────────────────


def test_execution_time_median_odd_and_unfinished_counted_separately():
    facts = MetricFacts(
        tasks=(task("t1"),),
        executions=(
            exe("e1", "t1", started_at=t(1), finished_at=t(2)),  # 1h
            exe("e2", "t1", started_at=t(1), finished_at=t(4)),  # 3h
            exe("e3", "t1", started_at=t(1), finished_at=t(1, 30)),  # 0.5h
            exe("e4", "t1", status="running", started_at=t(1)),  # 미완료
            exe("e5", "t1", status="failed", finished_at=t(2)),  # 시작 시각 모름
        ),
    )
    stat = only(compute(facts)).execution_time
    assert stat == Stat(median=1 * H, n=3, incomplete=1, unknown=1)


def test_execution_time_median_even():
    facts = MetricFacts(
        tasks=(task("t1"),),
        executions=(
            exe("e1", "t1", started_at=t(1), finished_at=t(2)),
            exe("e2", "t1", started_at=t(1), finished_at=t(4)),
        ),
    )
    assert only(compute(facts)).execution_time.median == 2 * H


# ── 비용·토큰: 모름은 0 이 아니다 ─────────────────────────────────────


def test_cost_partially_unknown_is_not_summed_as_zero():
    facts = MetricFacts(
        tasks=(task("t1"),),
        executions=(
            exe("e1", "t1", finished_at=t(1), cost_usd=0.5, input_tokens=100, output_tokens=10),
            exe("e2", "t1", finished_at=t(1), cost_usd=0.25, input_tokens=300),
            exe("e3", "t1", status="failed", finished_at=t(1)),
            exe("e4", "t1", status="running"),
        ),
    )
    group = only(compute(facts))
    assert group.cost_usd == Stat(median=0.375, n=2, incomplete=1, unknown=1, total=0.75)
    assert group.input_tokens == Stat(median=200, n=2, incomplete=1, unknown=1, total=400)
    assert group.output_tokens == Stat(median=10, n=1, incomplete=1, unknown=2, total=10)


def test_cost_all_unknown_total_is_none():
    facts = MetricFacts(tasks=(task("t1"),), executions=(exe("e1", "t1", finished_at=t(1)),))
    assert only(compute(facts)).cost_usd == Stat(median=None, n=0, unknown=1, total=None)


# ── 신뢰성 ────────────────────────────────────────────────────────────


def test_failure_rate_codes_and_reruns():
    facts = MetricFacts(
        tasks=(task("t1"),),
        executions=(
            exe("e1", "t1", status="failed", failed_code="timeout"),
            exe("e2", "t1", status="failed", failed_code="timeout", attempt=2),
            exe("e3", "t1", status="failed", attempt=3),
            exe("e4", "t1", status="result_ready"),
            exe("e5", "t1", status="queued"),
        ),
    )
    group = only(compute(facts))
    assert group.failure == Ratio(numerator=3, denominator=4, incomplete=1)
    assert group.failure.rate == 0.75
    assert group.failed_codes == {"timeout": 2, UNKNOWN: 1}
    assert group.reruns == Ratio(numerator=2, denominator=5)


# ── 묶음·품질 ─────────────────────────────────────────────────────────


def test_rework_twice_bundle_and_first_pass():
    # 묶음 A: t1(수정) → t2(검토). 검토 changes_requested 두 번 뒤 approved, 재작업 2회
    # 묶음 B: t3(수정) → t4(검토). 첫 검토 approved
    # 묶음 C: t5 — 검토 없음
    facts = MetricFacts(
        tasks=(
            task("t1"), task("t2", pred="t1", kind="code_review"),
            task("t3"), task("t4", pred="t3", kind="code_review"),
            task("t5"),
        ),
        executions=(
            exe("a1", "t1", created=t(1)),
            exe("r1", "t2", created=t(2), kind="code_review", outcome="changes_requested"),
            exe("a2", "t1", created=t(3), start_key="rework:r1", attempt=2),
            exe("r2", "t2", created=t(4), kind="code_review", outcome="changes_requested", attempt=2),
            exe("a3", "t1", created=t(5), start_key="rework:r2", attempt=3),
            exe("r3", "t2", created=t(6), kind="code_review", outcome="approved", attempt=3),
            exe("b1", "t3", created=t(1)),
            exe("s1", "t4", created=t(2), kind="code_review", outcome="approved"),
        ),
    )
    group = only(compute(facts))
    assert group.bundles == 3
    assert group.first_pass == Ratio(numerator=1, denominator=2, incomplete=1)
    assert group.rework == Stat(median=0, n=3, total=2)


def test_human_rejection_ratio_from_decision_events():
    facts = MetricFacts(
        tasks=(task("t1", review_decision="approve"), task("t2", review_decision="close")),
        events=(
            ev("t1", "status_changed", t(1), to="확인 필요", review_decision="request_changes"),
            ev("t1", "status_changed", t(2), to="완료", review_decision="approve"),
            ev("t1", "status_changed", t(3), to="완료", review_decision=None),
        ),
    )
    group = only(compute(facts))
    # t2 는 v6 이전 결정(tasks.review_decision 뿐) — 제외하고 모름으로 센다
    assert group.human_rejection == Ratio(numerator=1, denominator=2, unknown=1)


def test_interventions_and_response_time():
    facts = MetricFacts(
        tasks=(task("t1"), task("t2", pred="t1"), task("t3")),
        human_requests=(
            HumanRequestFact("hr1", "t1", created_at=t(1), answered_at=t(3)),
            HumanRequestFact("hr2", "t2", created_at=t(4)),  # 열린 요청
        ),
        events=(ev("t2", "status_changed", t(5), to="완료", review_decision="approve"),),
    )
    group = only(compute(facts))
    # 묶음 t1 = 요청 2 + 결정 1, 묶음 t3 = 0
    assert group.interventions == Stat(median=1.5, n=2, total=3)
    assert group.response_time == Stat(median=2 * H, n=1, incomplete=1)


# ── 속도 ──────────────────────────────────────────────────────────────


def test_intake_to_human_uses_earliest_of_request_and_status():
    facts = MetricFacts(
        tasks=(
            task("t1", created=t(1), issue_opened_at=t(0)),
            task("t2", pred="t1", created=t(2)),
            task("t3", created=t(1)),  # 이슈 없음 → 생성 시각
            task("t4", created=t(1)),  # 아직 사람 차례 없음
        ),
        human_requests=(HumanRequestFact("hr1", "t2", created_at=t(5)),),
        events=(
            ev("t2", "status_changed", t(3), **{"from": "실행 중", "to": "확인 필요"}),
            ev("t3", "status_changed", t(2), to="확인 필요"),
        ),
    )
    stat = only(compute(facts)).intake_to_human
    assert stat == Stat(median=2 * H, n=2, incomplete=1)  # [3h, 1h]


def test_intake_to_done_merge_status_fallback_and_failed():
    facts = MetricFacts(
        tasks=(
            task("t1", issue_opened_at=t(0), merge_confirmed_at=t(4), status="완료"),
            task("t2", created=t(0), status="완료"),  # status_changed → 완료
            task("t3", created=t(0), status="완료", finished_at=t(6)),  # v6 이전: finished_at
            task("t4", created=t(0), status="실패", finished_at=t(1)),  # 실패 마감
            task("t5", created=t(0)),  # 진행 중
        ),
        events=(ev("t2", "status_changed", t(2), to="완료"),),
    )
    group = only(compute(facts))
    assert group.intake_to_done == Stat(median=4 * H, n=3, incomplete=2)
    assert group.done_by_finished_at == 1
    assert group.closed_failed == 1


# ── 병목: 인계 대기, actor 별 대기 구간 ─────────────────────────────


def test_handoff_wait_splits_blocked_segments_by_actor():
    blockers_op = [{"code": "assignee_missing", "actor": "operator"}]
    blockers_both = [{"code": "assignee_missing", "actor": "operator"},
                     {"code": "input_missing", "actor": "assignee"}]
    facts = MetricFacts(
        tasks=(
            task("t1"),
            task("t2", pred="t1", created=t(1)),  # 1→5: operator 1h, operator+assignee 2h, 준비 후 1h
            task("t3", pred="t1", created=t(1)),  # v6 이전: 이벤트 없음 → 분해 모름
            task("t4", pred="t1", created=t(1)),  # 아직 시작 안 함
        ),
        executions=(
            exe("e2", "t2", created=t(4), started_at=t(5)),
            exe("e3", "t3", created=t(1), started_at=t(3)),
            exe("e4", "t4", status="queued"),
        ),
        events=(
            ev("t2", "blocked", t(1), blockers=blockers_op),
            ev("t2", "blocked", t(2), blockers=blockers_both),
            ev("t2", "status_changed", t(3), to="대기"),  # 구간을 끊지 않는다
            ev("t2", "ready", t(4)),
            ev("t4", "blocked", t(1), blockers=blockers_op),
        ),
    )
    group = only(compute(facts))
    assert group.handoff_wait == Stat(median=3 * H, n=2, incomplete=1)  # [4h, 2h]
    assert group.handoff_blocked["operator"] == Stat(median=3 * H, n=1, unknown=1, total=3 * H)
    assert group.handoff_blocked["assignee"] == Stat(median=2 * H, n=1, unknown=1, total=2 * H)
    assert group.handoff_blocked["system"] == Stat(median=0, n=1, unknown=1, total=0)


def test_blocked_segment_is_clipped_at_first_start():
    facts = MetricFacts(
        tasks=(task("t1"), task("t2", pred="t1", created=t(1))),
        executions=(exe("e2", "t2", started_at=t(2)),),  # ready 없이 시작(웹 시작)
        events=(ev("t2", "blocked", t(1), blockers=[{"code": "run_mode_manual", "actor": "operator"}]),
                ev("t2", "blocked", t(3), blockers=[{"code": "x", "actor": "system"}])),
    )
    group = only(compute(facts))
    assert group.handoff_blocked["operator"].total == 1 * H
    assert group.handoff_blocked["system"].total == 0


def test_events_only_at_or_after_first_start_leave_no_blocked_time():
    """후속 Task 가 만들어진 초에 바로 시작하면 시작 전 blocked·ready 표시가 없다 — 대기 구간 0(실패하지 않는다)."""
    facts = MetricFacts(
        tasks=(task("t1"), task("t2", pred="t1", created=t(1))),
        executions=(exe("e2", "t2", started_at=t(1)),),
        events=(ev("t2", "ready", t(1)), ev("t2", "status_changed", t(2), to="완료")),
    )
    group = only(compute(facts))
    assert group.handoff_wait == Stat(median=0, n=1)
    assert group.handoff_blocked["operator"] == Stat(median=0, n=1, total=0)


# ── 기간 필터 경계 ────────────────────────────────────────────────────


def test_window_is_since_inclusive_until_exclusive():
    facts = MetricFacts(
        tasks=(
            task("a", created=t(1), issue_opened_at=t(0), merge_confirmed_at=t(2)),  # 접수 0시 — 제외
            task("b", created=t(1), merge_confirmed_at=t(2)),  # 접수 1시 — 포함(since)
            task("c", created=t(2), merge_confirmed_at=t(3)),  # 접수 2시 — 제외(until)
        ),
        executions=(
            exe("e0", "a", created=t(0), finished_at=t(5)),
            exe("e1", "b", created=t(1), finished_at=t(5)),
            exe("e2", "c", created=t(2), finished_at=t(5)),
        ),
    )
    group = only(compute(facts, since=t(1), until=t(2)))
    assert group.bundles == 1
    assert group.intake_to_done.n == 1
    assert group.failure.denominator == 1  # 실행은 created_at 으로 거른다


def test_window_accepts_offset_timestamps():
    facts = MetricFacts(tasks=(task("b", created=t(1)),))
    assert only(compute(facts, since="2026-09-27T10:00:00+09:00")).bundles == 1


# ── group_by ──────────────────────────────────────────────────────────


def test_group_by_config_revision():
    facts = MetricFacts(
        tasks=(task("t1"), task("t2"), task("t3")),
        executions=(
            exe("e1", "t1", created=t(1), config_revision=2, status="failed"),
            exe("e1b", "t1", created=t(2), config_revision=3),
            exe("e2", "t2", created=t(1), config_revision=10),
            exe("e3", "t2", created=t(2), status="running"),  # NULL — 모름
        ),
    )
    groups = {g.key: g for g in compute(facts, group_by="config_revision").groups}
    assert [g.key for g in compute(facts, group_by="config_revision").groups] == ["2", "3", "10", UNKNOWN]
    # 묶음 지표는 묶음 첫 실행 값: t1→2, t2→10, t3(실행 없음)→모름
    assert groups["2"].bundles == 1
    assert groups["3"].bundles == 0
    assert groups["10"].bundles == 1
    assert groups[UNKNOWN].bundles == 1
    # 실행 지표는 그 실행 값
    assert groups["2"].failure == Ratio(numerator=1, denominator=1)
    assert groups["3"].failure == Ratio(numerator=0, denominator=1)
    assert groups[UNKNOWN].failure == Ratio(numerator=0, denominator=0, incomplete=1)


def test_group_by_folder_commit():
    sha_a, sha_b = "a" * 40, "b" * 40
    facts = MetricFacts(
        tasks=(task("t1"), task("t2", pred="t1")),
        executions=(
            exe("e1", "t1", created=t(1), folder_commit=sha_b, cost_usd=1.0),
            exe("e2", "t2", created=t(2), folder_commit=sha_a, cost_usd=2.0),
        ),
    )
    report = compute(facts, group_by="folder_commit")
    assert report.group_by == "folder_commit"
    groups = {g.key: g for g in report.groups}
    assert [g.key for g in report.groups] == [sha_a, sha_b]
    assert groups[sha_b].bundles == 1  # 묶음 첫 실행 e1
    assert groups[sha_a].bundles == 0
    assert groups[sha_a].cost_usd.total == 2.0
    assert groups[sha_b].cost_usd.total == 1.0


def test_group_by_rejects_unknown_key():
    with pytest.raises(ValueError):
        compute(MetricFacts(), group_by="kind")


# ── 기준선 ────────────────────────────────────────────────────────────


def test_summarize_baseline_uses_earliest_merge_per_issue():
    items = (
        BaselineItemFact(issue_number=1, issue_opened_at=t(0), pr_number=10, pr_merged_at=t(5)),
        BaselineItemFact(issue_number=1, issue_opened_at=t(0), pr_number=11, pr_merged_at=t(2)),
        BaselineItemFact(issue_number=2, issue_opened_at=t(0), pr_number=12, pr_merged_at=t(4)),
        BaselineItemFact(issue_number=3, issue_opened_at=t(1), pr_number=13, pr_merged_at=t(9)),
    )
    summary = summarize_baseline(items, opened_before=t(12), fetched_at=t(13))
    assert summary.intake_to_merge == Stat(median=4 * H, n=3)  # [2h, 4h, 8h]
    assert summary.opened_before == t(12)
    assert summary.fetched_at == t(13)
    assert summary.note == BASELINE_NOTE == "하네스·Claude 사용 시기 이력 — 순수 수작업 기준 아님"


def test_summarize_baseline_even_and_empty():
    items = (
        BaselineItemFact(issue_number=1, issue_opened_at=t(0), pr_number=1, pr_merged_at=t(1)),
        BaselineItemFact(issue_number=2, issue_opened_at=t(0), pr_number=2, pr_merged_at=t(2)),
    )
    assert summarize_baseline(items, opened_before=t(9), fetched_at=t(9)).intake_to_merge.median == 1.5 * H
    assert summarize_baseline((), opened_before=t(9), fetched_at=t(9)).intake_to_merge == Stat(median=None, n=0)


# ── 완료 = GitHub 병합 시각 (step 12, ADR-0015 결정 9) ────────────────


def gh_task(task_id, *, opened=t(0), state="open", **kw) -> TaskFact:
    return task(task_id, issue_opened_at=opened, issue_state=state, **kw)


def test_github_bundle_is_not_done_by_approval_alone():
    facts = MetricFacts(
        tasks=(gh_task("t1", status="완료", finished_at=t(2)),),
        events=(ev("t1", "status_changed", t(2), to="완료", review_decision="approve"),),
    )
    group = only(compute(facts))
    assert group.intake_to_done == Stat(median=None, n=0, incomplete=1)
    assert group.intake_to_merge == Stat(median=None, n=0, incomplete=1)
    assert group.done_by_finished_at == 0
    assert group.intake_to_approval == Stat(median=2 * H, n=1)


def test_github_bundle_is_done_at_merge_time():
    facts = MetricFacts(
        tasks=(gh_task("t1", state="closed", status="완료", pr_merged_at=t(5), merge_checked_at=t(6)),),
        events=(ev("t1", "status_changed", t(2), to="완료", review_decision="approve"),),
    )
    group = only(compute(facts))
    assert group.intake_to_done == Stat(median=5 * H, n=1)
    assert group.intake_to_merge == Stat(median=5 * H, n=1)
    assert group.intake_to_approval == Stat(median=2 * H, n=1)
    assert group.closed_unmerged == 0


def test_github_issue_closed_without_merge_is_counted_apart():
    facts = MetricFacts(tasks=(
        gh_task("t1", state="closed", merge_checked_at=t(3)),  # 조회했지만 병합 없음
        gh_task("t2", state="closed"),  # 아직 조회 전 — 모름이라 따로 세지 않는다
        gh_task("t3", state="open"),
    ))
    group = only(compute(facts))
    assert group.closed_unmerged == 1
    assert group.intake_to_done == Stat(median=None, n=0, incomplete=3)
    assert group.intake_to_merge == Stat(median=None, n=0, incomplete=3)


def test_direct_task_keeps_previous_done_rule_and_is_not_in_merge_metric():
    facts = MetricFacts(
        tasks=(task("t1", created=t(0), status="완료"),),
        events=(ev("t1", "status_changed", t(3), to="완료"),),
    )
    group = only(compute(facts))
    assert group.intake_to_done == Stat(median=3 * H, n=1)
    assert group.intake_to_merge == Stat(median=None, n=0)  # 이슈 열림 → 병합 은 GitHub 묶음만
    assert group.closed_unmerged == 0


def test_intake_to_approval_uses_first_approval_or_done_in_bundle():
    facts = MetricFacts(
        tasks=(
            gh_task("t1", opened=t(0)),
            task("t2", created=t(1), pred="t1", kind="code_review"),
            task("t3", created=t(0)),  # 승인 없음
        ),
        events=(
            ev("t1", "status_changed", t(2), to="확인 필요", review_decision="request_changes"),
            ev("t2", "status_changed", t(4), to="완료"),
            ev("t1", "status_changed", t(6), to="완료", review_decision="approve"),
        ),
    )
    stat = only(compute(facts)).intake_to_approval
    assert stat == Stat(median=4 * H, n=1, incomplete=1)
