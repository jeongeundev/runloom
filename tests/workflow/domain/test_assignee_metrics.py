"""담당자별 순수 계산 — ARCHITECTURE "모니터링 — phase 20" 담당자별 지표 정의."""

from dataclasses import replace
from itertools import count

from workflow.domain.assignee_metrics import (
    AgentLabel,
    AgentRunFact,
    AssigneeFacts,
    AssigneeWorkFact,
    MemberLabel,
    ResponseFact,
    compute_assignee_metrics,
)
from workflow.domain.metrics import UNKNOWN, Ratio, Stat

_ids = count(1)
NOW = "2026-10-02T00:00:00Z"


def work(**overrides) -> AssigneeWorkFact:
    n = next(_ids)
    base = AssigneeWorkFact(
        work_item_id=f"w-{n}",
        assignee_type=None,
        assignee_id=None,
        status="대기",
        closed_at=None,
        turn_since=None,
        recipients=(),
    )
    return replace(base, **overrides)


def run(**overrides) -> AgentRunFact:
    n = next(_ids)
    base = AgentRunFact(
        execution_id=f"e-{n}",
        agent_id="a1",
        work_item_id="w-x",
        kind="bug_fix",
        status="result_ready",
        start_key=f"manual:{n}",
        created_at="2026-10-01T00:00:00Z",
        started_at="2026-10-01T00:00:00Z",
        finished_at="2026-10-01T00:10:00Z",
        failed_code=None,
        outcome=None,
        cost_usd=0.5,
    )
    return replace(base, **overrides)


def response(member_id, requested_at, responded_at) -> ResponseFact:
    return ResponseFact(request_id=f"r-{next(_ids)}", member_id=member_id, requested_at=requested_at,
                        responded_at=responded_at)


MEMBERS = (MemberLabel("m1", "김", True), MemberLabel("m2", "이", True))
AGENTS = (AgentLabel("a1", "고치는 에이전트"),)


def compute(*, works=(), responses=(), runs=(), members=MEMBERS, agents=AGENTS, since=None, until=None, now=NOW):
    facts = AssigneeFacts(works=tuple(works), responses=tuple(responses), runs=tuple(runs), members=tuple(members),
                          agents=tuple(agents))
    return compute_assignee_metrics(facts, since=since, until=until, now=now)


def member(report, member_id):
    return next(m for m in report.members if m.member_id == member_id)


def agent(report, agent_id):
    return next(a for a in report.agents if a.agent_id == agent_id)


def test_empty_input():
    r = compute(members=(), agents=())
    assert r.since is None and r.until is None
    assert r.members == () and r.agents == ()
    assert r.unassigned_open == 0
    assert r.responses_unknown_member == 0


def test_rows_without_records_are_zero():
    r = compute()
    assert [m.member_id for m in r.members] == ["m1", "m2"]
    m = member(r, "m1")
    assert (m.display_name, m.active, m.done, m.open, m.turn_waiting, m.longest_wait) == ("김", True, 0, 0, 0, None)
    assert m.wait == Stat(None, 0)
    assert m.response_time == Stat(None, 0)
    a = agent(r, "a1")
    assert (a.name, a.done, a.open, a.runs, a.rework) == ("고치는 에이전트", 0, 0, 0, 0)
    assert a.failure == Ratio(0, 0)
    assert a.failed_codes == {}
    assert a.first_pass == Ratio(0, 0)
    assert a.execution_time == Stat(None, 0)
    assert a.cost_usd == Stat(None, 0)


def test_done_and_open_go_to_current_assignee():
    r = compute(works=[
        work(assignee_type="member", assignee_id="m1", status="완료", closed_at="2026-10-01T00:00:00Z"),
        work(assignee_type="member", assignee_id="m1", status="종료", closed_at="2026-10-01T00:00:00Z"),
        work(assignee_type="member", assignee_id="m1", status="에이전트 작업 중"),
        work(assignee_type="member", assignee_id="m2", status="내 차례"),
        work(assignee_type="agent", assignee_id="a1", status="완료", closed_at="2026-10-01T00:00:00Z"),
        work(assignee_type="agent", assignee_id="a1", status="PR · 검토"),
        work(assignee_type="agent", assignee_id="a1", status="대기"),
    ])
    assert (member(r, "m1").done, member(r, "m1").open) == (1, 1)
    assert (member(r, "m2").done, member(r, "m2").open) == (0, 1)
    assert (agent(r, "a1").done, agent(r, "a1").open) == (1, 2)


def test_done_filtered_by_closed_at_open_ignores_window():
    r = compute(works=[
        work(assignee_type="member", assignee_id="m1", status="완료", closed_at="2026-09-01T00:00:00Z"),
        work(assignee_type="member", assignee_id="m1", status="완료", closed_at="2026-10-01T00:00:00Z"),
        work(assignee_type="member", assignee_id="m1", status="완료", closed_at="2026-10-05T00:00:00Z"),
        work(assignee_type="member", assignee_id="m1", status="대기"),
    ], since="2026-09-30T00:00:00Z", until="2026-10-05T00:00:00Z")
    assert r.since == "2026-09-30T00:00:00Z" and r.until == "2026-10-05T00:00:00Z"
    assert (member(r, "m1").done, member(r, "m1").open) == (1, 1)


def test_turn_waiting_counts_every_recipient_and_uses_now():
    r = compute(works=[
        work(status="내 차례", turn_since="2026-10-01T00:00:00Z", recipients=("m1", "m2")),
        work(status="내 차례", turn_since="2026-10-01T22:00:00Z", recipients=("m1",)),
        work(status="내 차례", turn_since=None, recipients=("m1",)),
        work(status="대기", turn_since="2026-09-01T00:00:00Z", recipients=("m1",)),  # 지금 내 차례 아님
    ])
    m1 = member(r, "m1")
    assert m1.turn_waiting == 3
    assert m1.longest_wait == 86400.0
    assert m1.wait == Stat(median=(86400.0 + 7200.0) / 2, n=2, unknown=1)
    m2 = member(r, "m2")
    assert m2.turn_waiting == 1
    assert m2.longest_wait == 86400.0
    assert m2.wait == Stat(median=86400.0, n=1)


def test_response_time_goes_to_responder_and_filters_by_response_time():
    r = compute(responses=[
        response("m1", "2026-10-01T00:00:00Z", "2026-10-01T01:00:00Z"),
        response("m1", "2026-10-01T00:00:00Z", "2026-10-01T03:00:00Z"),
        response("m1", "2026-09-01T00:00:00Z", "2026-09-29T00:00:00Z"),  # 응답이 기간 밖
        response("m2", "2026-09-29T00:00:00Z", "2026-10-01T00:00:00Z"),  # 요청은 기간 밖, 응답은 안
        response(None, "2026-10-01T00:00:00Z", "2026-10-01T00:30:00Z"),
        response(None, "2026-09-01T00:00:00Z", "2026-09-01T00:30:00Z"),
    ], since="2026-09-30T00:00:00Z", until="2026-10-02T00:00:00Z")
    assert member(r, "m1").response_time == Stat(median=7200.0, n=2)
    assert member(r, "m2").response_time == Stat(median=172800.0, n=1)
    assert r.responses_unknown_member == 1


def test_agent_runs_failure_rework_time_and_cost():
    r = compute(runs=[
        run(status="result_ready", cost_usd=1.0),
        run(status="failed", failed_code="timeout", cost_usd=None,
            started_at="2026-10-01T00:00:00Z", finished_at="2026-10-01T00:20:00Z"),
        run(status="failed", failed_code=None, started_at=None, finished_at="2026-10-01T00:01:00Z", cost_usd=0.2),
        run(status="running", start_key="rework:t1:1", finished_at=None, cost_usd=None),
        run(status="result_ready", start_key="rework:t1:2", cost_usd=0.3),
        run(created_at="2026-09-01T00:00:00Z", start_key="rework:old"),  # 기간 밖
    ], since="2026-09-30T00:00:00Z")
    a = agent(r, "a1")
    assert a.runs == 5
    assert a.failure == Ratio(2, 4, incomplete=1)
    assert a.failed_codes == {"timeout": 1, UNKNOWN: 1}
    assert a.rework == 2
    assert a.execution_time == Stat(median=600.0, n=3, incomplete=1, unknown=1)
    assert a.cost_usd == Stat(median=0.3, n=3, incomplete=1, unknown=1, total=1.5)


def test_runs_go_to_running_agent_not_current_assignee():
    r = compute(
        works=[work(work_item_id="w1", assignee_type="agent", assignee_id="a1", status="대기")],
        runs=[run(agent_id="a2", work_item_id="w1")],
        agents=(AgentLabel("a1", "하나"), AgentLabel("a2", "둘")),
    )
    assert agent(r, "a1").runs == 0
    assert agent(r, "a2").runs == 1


def test_first_pass_over_works_currently_assigned_to_agent():
    works = [
        work(work_item_id="w1", assignee_type="agent", assignee_id="a1", status="완료",
             closed_at="2026-10-01T00:00:00Z"),
        work(work_item_id="w2", assignee_type="agent", assignee_id="a1", status="PR · 검토"),
        work(work_item_id="w3", assignee_type="agent", assignee_id="a1", status="대기"),
        work(work_item_id="w4", assignee_type="member", assignee_id="m1", status="완료",
             closed_at="2026-10-01T00:00:00Z"),
    ]
    runs = [
        # w1: 첫 검토 approved — 검토는 다른 에이전트가 돌려도 그 업무의 결과다
        run(agent_id="a2", work_item_id="w1", kind="code_review", outcome="approved",
            created_at="2026-10-01T01:00:00Z"),
        # w2: 첫 검토 changes_requested, 두 번째 approved
        run(work_item_id="w2", kind="code_review", outcome="approved", created_at="2026-10-01T03:00:00Z"),
        run(work_item_id="w2", kind="code_review", outcome="changes_requested", created_at="2026-10-01T02:00:00Z"),
        # w3: 검토 결과 없음(실패한 검토는 outcome 없음)
        run(work_item_id="w3", kind="code_review", status="failed", outcome=None),
        # w4: 멤버 담당 — 에이전트 1회 통과에 들지 않음
        run(work_item_id="w4", kind="code_review", outcome="approved"),
    ]
    r = compute(works=works, runs=runs, agents=(AgentLabel("a1", "하나"), AgentLabel("a2", "둘")))
    assert agent(r, "a1").first_pass == Ratio(1, 2, incomplete=1)
    assert agent(r, "a2").first_pass == Ratio(0, 0)


def test_inactive_member_only_with_records():
    members = (*MEMBERS, MemberLabel("m3", "박", False), MemberLabel("m4", "최", False))
    r = compute(members=members, works=[work(assignee_type="member", assignee_id="m3", status="대기")])
    assert [m.member_id for m in r.members] == ["m1", "m2", "m3"]
    assert member(r, "m3").active is False


def test_inactive_member_with_response_or_turn_only():
    members = (MemberLabel("m3", "박", False), MemberLabel("m4", "최", False), MemberLabel("m5", "정", False))
    r = compute(members=members,
                works=[work(status="내 차례", turn_since="2026-10-01T00:00:00Z", recipients=("m4",))],
                responses=[response("m3", "2026-10-01T00:00:00Z", "2026-10-01T01:00:00Z")])
    assert [m.member_id for m in r.members] == ["m3", "m4"]


def test_unlabeled_ids_in_records_get_rows():
    r = compute(
        works=[work(assignee_type="member", assignee_id="m9", status="대기"),
               work(assignee_type="agent", assignee_id="a9", status="대기")],
        runs=[run(agent_id="a8")],
    )
    assert [m.member_id for m in r.members] == ["m1", "m2", "m9"]
    m9 = member(r, "m9")
    assert (m9.display_name, m9.active, m9.open) == ("m9", False, 1)
    assert [a.agent_id for a in r.agents] == ["a1", "a8", "a9"]
    assert agent(r, "a9").name == "a9"


def test_unassigned_open():
    r = compute(works=[
        work(status="새로 들어옴"),
        work(status="내 차례", recipients=("m1",)),
        work(status="완료", closed_at="2026-10-01T00:00:00Z"),
        work(status="종료", closed_at="2026-10-01T00:00:00Z"),
        work(assignee_type="member", assignee_id="m1", status="대기"),
    ])
    assert r.unassigned_open == 2
