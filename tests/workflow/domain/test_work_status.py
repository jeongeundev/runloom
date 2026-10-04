"""업무 상태 판정(ARCHITECTURE "업무와 단계 — phase 14" 업무 상태 표, ADR-0020 결정 4·5)."""

import dataclasses

import pytest

from workflow.domain.work_status import (
    STAGE_FAILED,
    TERMINAL_WORK_STATUSES,
    WORK_STATUSES,
    PullRequestFact,
    RequestFact,
    StageFact,
    WorkItemFacts,
    WorkStatus,
    work_status,
)
from workflow.domain.next_step import NextStepFact
from workflow.domain.triage import TriageFact


def stage(status, reason="", *, task_id="t1", kind="bug_fix", label="버그 수정", at="2026-09-30T00:00:00Z", executed=True):
    return StageFact(
        task_id=task_id,
        kind=kind,
        kind_label=label,
        status=status,
        status_reason=reason,
        created_at=at,
        executed=executed,
    )


def facts(
    stages=(),
    *,
    stored=("새로 들어옴", "담당 없음"),
    assigned=True,
    delegated=True,
    requests=(),
    pr=None,
    direct=None,
    detected=(),
):
    return WorkItemFacts(
        stored_status=stored[0],
        stored_reason=stored[1],
        assigned=assigned,
        delegated=delegated,
        stages=tuple(stages),
        open_requests=tuple(requests),
        pull_request=pr,
        direct_member_name=direct,
        detected_pull_requests=tuple(detected),
    )


FAILED = stage("실패", "PROCESS_EXIT · 종료 코드 1")
RUNNING = stage("실행 중", "시작 확인")
REVIEW_RUNNING = stage("실행 중", "시작 확인", task_id="t2", kind="code_review", label="커밋 검토", at="2026-09-30T01:00:00Z")


def test_constants():
    assert WORK_STATUSES == (
        "새로 들어옴",
        "대기",
        "에이전트 작업 중",
        "직접 작업 중",
        "내 차례",
        "PR · 검토",
        "완료",
        "종료",
    )
    assert TERMINAL_WORK_STATUSES == ("완료", "종료")
    assert STAGE_FAILED == "stage_failed"


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        # 새로 들어옴 — 담당 없음 / 지시 전
        pytest.param(
            facts(assigned=False),
            WorkStatus("새로 들어옴", "담당 없음"),
            id="new-unassigned-no-stage",
        ),
        pytest.param(
            facts([stage("대기", "자동 실행 대기", executed=False)], assigned=False),
            WorkStatus("새로 들어옴", "담당 없음"),
            id="new-unassigned",
        ),
        pytest.param(
            facts([stage("대기", "자동 실행 대기", executed=False)], delegated=False),
            WorkStatus("새로 들어옴", "지시 전 — [에이전트에게 맡기기]"),
            id="new-not-delegated",
        ),
        # 대기 — 맡겼지만 시작 전
        pytest.param(
            facts([stage("대기", "연결 끊김, 마지막 확인 10:00", executed=False)]),
            WorkStatus("대기", "연결 끊김, 마지막 확인 10:00"),
            id="waiting-disconnected",
        ),
        pytest.param(
            facts([stage("실행 가능", "agent-1 선택됨", executed=False)]),
            WorkStatus("대기", "agent-1 선택됨"),
            id="waiting-runnable",
        ),
        pytest.param(
            facts([stage("완료", "판정 통과"), stage("대기", "선행 대기", task_id="t2", at="2026-09-30T01:00:00Z")]),
            WorkStatus("대기", "선행 대기"),
            id="waiting-next-stage",
        ),
        # 에이전트 작업 중
        pytest.param(facts([RUNNING]), WorkStatus("에이전트 작업 중", "버그 수정 실행 중"), id="agent-running"),
        pytest.param(
            facts([stage("실행 요청됨", "접수 대기", executed=False)], assigned=False),
            WorkStatus("에이전트 작업 중", "버그 수정 실행 중"),
            id="agent-requested",
        ),
        pytest.param(
            facts([stage("완료", "판정 통과"), REVIEW_RUNNING]),
            WorkStatus("에이전트 작업 중", "커밋 검토 실행 중"),
            id="agent-review-running",
        ),
        # 내 차례
        pytest.param(
            facts([RUNNING], requests=[RequestFact("rework_limit", "재작업 한도 도달\n자세한 내용")]),
            WorkStatus("내 차례", "사람 요청 — 재작업 한도 도달"),
            id="mine-request",
        ),
        pytest.param(
            facts([FAILED], requests=[RequestFact(STAGE_FAILED, "실행 실패 — PROCESS_EXIT: 종료 코드 1")]),
            WorkStatus("내 차례", "실패 — PROCESS_EXIT · 종료 코드 1"),
            id="mine-failed",
        ),
        pytest.param(
            facts([stage("확인 필요", "검토 승인 — 병합·이슈 종료는 사람")]),
            WorkStatus("내 차례", "검토 승인 — 병합·이슈 종료는 사람"),
            id="mine-check-needed",
        ),
        # PR · 검토
        pytest.param(
            facts([stage("완료", "판정 통과")], pr=PullRequestFact("pending", None)),
            WorkStatus("PR · 검토", "PR 여는 중"),
            id="pr-pending",
        ),
        pytest.param(
            facts([stage("완료", "판정 통과")], pr=PullRequestFact("open", 12)),
            WorkStatus("PR · 검토", "PR 확인 — #12"),
            id="pr-open",
        ),
        # 완료
        pytest.param(
            facts([stage("완료", "판정 통과")], pr=PullRequestFact("merged", 12)),
            WorkStatus("완료", "PR 병합 — #12"),
            id="done-merged",
        ),
        pytest.param(
            facts([stage("완료", "판정 통과"), stage("완료", "검토 승인", task_id="t2", label="커밋 검토", at="2026-09-30T01:00:00Z")]),
            WorkStatus("완료", "커밋 검토 완료"),
            id="done-all-stages",
        ),
        # 종료
        pytest.param(
            facts([stage("완료", "판정 통과")], pr=PullRequestFact("closed", 12)),
            WorkStatus("종료", "PR 이 병합 없이 닫힘 — #12"),
            id="closed-pr-closed",
        ),
        pytest.param(
            facts([stage("실패", "운영자 종료 — 사람 요청 응답")]),
            WorkStatus("종료", "운영자 종료 — 사람 요청 응답"),
            id="closed-last-stage-failed",
        ),
        pytest.param(
            facts([RUNNING], stored=("종료", "닫음 — 실행 실패")),
            WorkStatus("종료", "닫음 — 실행 실패"),
            id="closed-stored",
        ),
    ],
)
def test_work_status_table(given, expected):
    assert work_status(given) == expected


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        pytest.param(
            facts([RUNNING], requests=[RequestFact("question", "어느 브랜치?")]),
            WorkStatus("내 차례", "사람 요청 — 어느 브랜치?"),
            id="request-beats-running",
        ),
        pytest.param(
            facts([stage("완료", "판정 통과")], requests=[RequestFact("question", "병합할까요?")], pr=PullRequestFact("open", 3)),
            WorkStatus("내 차례", "사람 요청 — 병합할까요?"),
            id="request-beats-open-pr",
        ),
        pytest.param(
            facts(
                [RUNNING],
                stored=("완료", "PR 병합 — #3"),
                requests=[RequestFact("question", "?")],
                pr=PullRequestFact("closed", 3),
            ),
            WorkStatus("완료", "PR 병합 — #3"),
            id="terminal-ignores-everything",
        ),
        pytest.param(
            facts([RUNNING], requests=[RequestFact("question", "?")], pr=PullRequestFact("merged", 3)),
            WorkStatus("완료", "PR 병합 — #3"),
            id="merged-beats-request",
        ),
        pytest.param(
            facts([RUNNING], pr=PullRequestFact("open", 3)),
            WorkStatus("PR · 검토", "PR 확인 — #3"),
            id="open-pr-beats-running",
        ),
        pytest.param(
            facts([FAILED, stage("실행 중", "시작 확인", task_id="t2", at="2026-09-30T01:00:00Z")]),
            WorkStatus("에이전트 작업 중", "버그 수정 실행 중"),
            id="retry-after-failure-running",
        ),
        pytest.param(
            facts([FAILED, stage("대기", "자동 실행 대기", task_id="t2", at="2026-09-30T01:00:00Z", executed=False)]),
            WorkStatus("대기", "자동 실행 대기"),
            id="retry-after-failure-waiting",
        ),
        pytest.param(
            facts([stage("완료", "판정 통과")], pr=PullRequestFact("failed", None)),
            WorkStatus("완료", "버그 수정 완료"),
            id="failed-pr-falls-through",
        ),
        pytest.param(
            facts([stage("실행 중", "시작 확인", executed=True)], assigned=False, delegated=False),
            WorkStatus("에이전트 작업 중", "버그 수정 실행 중"),
            id="running-beats-unassigned",
        ),
    ],
)
def test_priority(given, expected):
    assert work_status(given) == expected


def test_request_reason_first_line_80_chars():
    question = "가" * 100 + "\n둘째 줄"
    got = work_status(facts([RUNNING], requests=[RequestFact("question", question)]))
    assert got == WorkStatus("내 차례", "사람 요청 — " + "가" * 80)


def test_stage_failed_request_wins_over_other_requests():
    got = work_status(
        facts(
            [FAILED],
            requests=[RequestFact("question", "다른 질문"), RequestFact(STAGE_FAILED, "실행 실패 — X: y")],
        )
    )
    assert got == WorkStatus("내 차례", "실패 — PROCESS_EXIT · 종료 코드 1")


def test_latest_stage_decides_by_created_at_not_tuple_order():
    later_done = stage("완료", "검토 승인", task_id="t2", label="커밋 검토", at="2026-09-30T02:00:00Z")
    earlier_failed = stage("실패", "검토 거절", task_id="t1", at="2026-09-30T01:00:00Z")
    assert work_status(facts([later_done, earlier_failed])) == WorkStatus("완료", "커밋 검토 완료")


def test_every_result_is_a_known_status():
    samples = [
        facts(),
        facts(assigned=False),
        facts([RUNNING]),
        facts([FAILED], requests=[RequestFact(STAGE_FAILED, "q")]),
    ]
    for sample in samples:
        assert work_status(sample).status in WORK_STATUSES


# --- 직접 작업 (phase 16 step 8, ARCHITECTURE "업무 상태 표 갱신" 8번) ---------------------------------


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        pytest.param(facts(direct="김개발"), WorkStatus("직접 작업 중", "김개발"), id="direct-no-stage"),
        pytest.param(
            facts([stage("대기", "자동 실행 대기", executed=False)], assigned=False, delegated=False, direct="김개발"),
            WorkStatus("직접 작업 중", "김개발"),
            id="direct-beats-new",
        ),
        pytest.param(
            facts([FAILED, stage("대기", "선행 대기", task_id="t2", at="2026-09-30T01:00:00Z")], direct="김개발"),
            WorkStatus("직접 작업 중", "김개발"),
            id="direct-beats-waiting",
        ),
        pytest.param(
            facts([stage("확인 필요", "검토 승인")], direct="김개발"),
            WorkStatus("직접 작업 중", "김개발"),
            id="direct-beats-check-needed",
        ),
        pytest.param(
            facts([RUNNING], requests=[RequestFact("question", "어느 브랜치?")], direct="김개발"),
            WorkStatus("내 차례", "사람 요청 — 어느 브랜치?"),
            id="request-beats-direct",
        ),
        pytest.param(
            facts(pr=PullRequestFact("open", 4), direct="김개발"),
            WorkStatus("PR · 검토", "PR 확인 — #4"),
            id="open-pr-beats-direct",
        ),
        pytest.param(
            facts(pr=PullRequestFact("merged", 4), direct="김개발"),
            WorkStatus("완료", "PR 병합 — #4"),
            id="merged-pr-beats-direct",
        ),
        pytest.param(
            facts(stored=("종료", "원본 이슈 닫힘"), direct="김개발"),
            WorkStatus("종료", "원본 이슈 닫힘"),
            id="terminal-beats-direct",
        ),
    ],
)
def test_direct_work(given, expected):
    assert work_status(given) == expected


def test_direct_work_defaults_to_none_so_old_facts_are_unchanged():
    old = WorkItemFacts(
        stored_status="새로 들어옴", stored_reason="", assigned=False, delegated=True, stages=(), open_requests=(),
        pull_request=None,
    )
    assert old.direct_member_name is None
    assert work_status(old) == WorkStatus("새로 들어옴", "담당 없음")


# --- 감지 PR (phase 16 step 9 — 규칙 4·7) ---

D_OPEN = PullRequestFact("open", 21)
D_MERGED = PullRequestFact("merged", 22)
D_CLOSED = PullRequestFact("closed", 23)


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        pytest.param(facts(detected=[D_OPEN]), WorkStatus("PR · 검토", "PR 확인 — #21"), id="open"),
        pytest.param(facts(detected=[D_MERGED]), WorkStatus("완료", "PR 병합 — #22"), id="merged"),
        pytest.param(facts(detected=[D_OPEN, D_MERGED]), WorkStatus("완료", "PR 병합 — #22"), id="merged-beats-open"),
        pytest.param(facts(detected=[PullRequestFact("open", 30), D_OPEN]), WorkStatus("PR · 검토", "PR 확인 — #30"),
                     id="latest-open-first"),
        pytest.param(facts(detected=[D_CLOSED], direct="김개발"), WorkStatus("직접 작업 중", "김개발"),
                     id="closed-ignored-direct"),
        pytest.param(facts([RUNNING], detected=[D_CLOSED]), WorkStatus("에이전트 작업 중", "버그 수정 실행 중"),
                     id="closed-ignored"),
        pytest.param(facts(detected=[D_OPEN], direct="김개발"), WorkStatus("PR · 검토", "PR 확인 — #21"),
                     id="open-beats-direct"),
        pytest.param(facts(requests=[RequestFact("question", "어느 브랜치?")], detected=[D_OPEN]),
                     WorkStatus("내 차례", "사람 요청 — 어느 브랜치?"), id="request-beats-open"),
        pytest.param(facts(requests=[RequestFact("question", "어느 브랜치?")], detected=[D_MERGED]),
                     WorkStatus("완료", "PR 병합 — #22"), id="merged-beats-request"),
        pytest.param(facts(pr=PullRequestFact("closed", 4), detected=[D_MERGED]),
                     WorkStatus("종료", "PR 이 병합 없이 닫힘 — #4"), id="runloom-closed-first"),
        pytest.param(facts(pr=PullRequestFact("open", 4), detected=[D_OPEN]), WorkStatus("PR · 검토", "PR 확인 — #4"),
                     id="runloom-open-first"),
        pytest.param(facts(stored=("종료", "원본 이슈 닫힘"), detected=[D_MERGED]), WorkStatus("종료", "원본 이슈 닫힘"),
                     id="terminal-stays"),
        pytest.param(facts(assigned=False, detected=[D_OPEN]), WorkStatus("PR · 검토", "PR 확인 — #21"),
                     id="unassigned-too"),
    ],
)
def test_detected_pull_requests(given, expected):
    assert work_status(given) == expected


def test_detected_pull_requests_default_empty():
    assert facts().detected_pull_requests == ()


# --- 사람 사이 인계 (phase 17) ---


def test_owner_approval_request_reason_is_question_first_line_without_prefix():
    question = "김OO 가 맡김 · 이OO 승인 대기\n에이전트 opensql — [승인]하면 곧 시작합니다."

    assert work_status(facts([stage("대기", "김OO 가 맡김 · 이OO 승인 대기", executed=False)],
                             requests=[RequestFact("owner_approval", question)])) == WorkStatus(
        "내 차례", "김OO 가 맡김 · 이OO 승인 대기")


def test_runner_offline_stage_waits_with_owner_reason():
    assert work_status(facts([stage("대기", "이OO의 러너 꺼짐 · 켜지면 시작", executed=False)])) == WorkStatus(
        "대기", "이OO의 러너 꺼짐 · 켜지면 시작")


# --- 판단 (phase 19 step 5 — 담당 없는 새 업무의 이유만 바뀐다) ---

def test_triage_fact_defaults_to_none_and_keeps_the_unassigned_reason():
    assert facts(assigned=False).triage is None
    assert work_status(facts(assigned=False)) == WorkStatus("새로 들어옴", "담당 없음")


@pytest.mark.parametrize(
    ("fact", "reason"),
    [
        (TriageFact("running", None, None, None), "판단 중"),
        (TriageFact("proposed", "ready", 0.86, None), "판단 제안 · 맡겨도 됨 0.86"),
        (TriageFact("failed", None, None, "usage_limit"), "판단 실패 · 사용량 한도"),
    ],
)
def test_unassigned_new_work_shows_the_triage_reason(fact, reason):
    given = dataclasses.replace(facts(assigned=False), triage=fact)
    assert work_status(given) == WorkStatus("새로 들어옴", reason)


def test_triage_does_not_change_other_statuses():
    running = TriageFact("running", None, None, None)
    # 담당이 정해졌으면(판단이 떠 있어도) 그대로 — 판단은 제안일 뿐
    assert work_status(dataclasses.replace(facts(assigned=True, delegated=False), triage=running)) == WorkStatus(
        "새로 들어옴", "지시 전 — [에이전트에게 맡기기]")
    assert work_status(dataclasses.replace(facts([RUNNING], assigned=False), triage=running)) == WorkStatus(
        "에이전트 작업 중", "버그 수정 실행 중")


# --- 결과 뒤 판단 (phase 22 step 5 — 직접 작업 다음, 확인 필요 단계 앞) ---

CHECK = stage("확인 필요", "결과 확인 — needs_information")


def test_next_step_fact_defaults_to_none():
    assert facts([CHECK]).next_step is None
    assert work_status(facts([CHECK])) == WorkStatus("내 차례", "결과 확인 — needs_information")


@pytest.mark.parametrize(
    ("fact", "expected"),
    [
        (NextStepFact("running", None, None), WorkStatus("에이전트 작업 중", "다음 단계 판단 중")),
        (NextStepFact("proposed", "사내 요청", None), WorkStatus("내 차례", "다음 단계 제안 · 사내 요청")),
        (NextStepFact("request_waiting", None, "박OO"), WorkStatus("대기", "사내 요청 대기 · 박OO")),
    ],
)
def test_next_step_comes_before_the_check_stage(fact, expected):
    assert work_status(dataclasses.replace(facts([CHECK]), next_step=fact)) == expected


def test_open_requests_and_direct_work_come_before_the_next_step():
    running = NextStepFact("running", None, None)
    asked = dataclasses.replace(facts([CHECK], requests=[RequestFact("fix_needs_information", "값이 필요")]),
                                next_step=running)
    assert work_status(asked) == WorkStatus("내 차례", "사람 요청 — 값이 필요")
    direct = dataclasses.replace(facts([CHECK], direct="김지은"), next_step=running)
    assert work_status(direct) == WorkStatus("직접 작업 중", "김지은")
