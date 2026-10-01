"""담당자별 순수 계산 — ARCHITECTURE "모니터링 — phase 20" 담당자별 지표 정의, ADR-0026 결정 6.

귀속: 완료·진행 = 업무의 지금 담당, 응답 시간 = 응답한 멤버, 내 차례 대기 = 지금 받는 사람, 실행 = 그 실행의
Agent. 과거 담당 이력으로 나누지 않는다. 판단 단계 실행은 입력에 오지 않는다(호출자가 뺀다). 현재 시각·DB 를
보지 않는다 — 대기 시간은 `now` 인자로 잰다. `Stat`·`Ratio` 와 1회 통과·실행 시간·비용 정의는 phase 9 것.
"""

from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from workflow.domain.metrics import UNKNOWN, Ratio, Stat, _in_window, _parse, _seconds, _stat

_MEMBER = "member"
_AGENT = "agent"
_DONE = "완료"
_FINISHED = ("완료", "종료")
_HUMAN_TURN = "내 차례"
_TERMINAL = ("result_ready", "failed")
_REVIEW_KIND = "code_review"
_REWORK_PREFIX = "rework:"


# ── 입력 ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class AssigneeWorkFact:
    work_item_id: str
    assignee_type: str | None  # member | agent. None = 담당 없음
    assignee_id: str | None
    status: str  # WORK_STATUSES
    closed_at: str | None
    turn_since: str | None  # 지금 내 차례면 내 차례가 된 시각. 모르면 None
    recipients: tuple[str, ...]  # 지금 내 차례 받는 사람 member_id


@dataclass(frozen=True)
class ResponseFact:
    request_id: str
    member_id: str | None  # 응답한 멤버. v11 이전 응답은 None
    requested_at: str
    responded_at: str


@dataclass(frozen=True)
class AgentRunFact:
    execution_id: str
    agent_id: str
    work_item_id: str
    kind: str
    status: str
    start_key: str
    created_at: str
    started_at: str | None
    finished_at: str | None
    failed_code: str | None
    outcome: str | None  # 검토 결과 outcome. 없으면 None
    cost_usd: float | None


@dataclass(frozen=True)
class MemberLabel:
    member_id: str
    display_name: str
    active: bool


@dataclass(frozen=True)
class AgentLabel:
    agent_id: str
    name: str


@dataclass(frozen=True)
class AssigneeFacts:
    works: tuple[AssigneeWorkFact, ...] = ()
    responses: tuple[ResponseFact, ...] = ()
    runs: tuple[AgentRunFact, ...] = ()
    members: tuple[MemberLabel, ...] = ()  # 표시 순
    agents: tuple[AgentLabel, ...] = ()  # 표시 순


# ── 결과 ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class MemberRow:
    member_id: str
    display_name: str
    active: bool
    done: int
    open: int
    turn_waiting: int
    longest_wait: float | None
    wait: Stat
    response_time: Stat


@dataclass(frozen=True)
class AgentRow:
    agent_id: str
    name: str
    done: int
    open: int
    runs: int
    failure: Ratio
    failed_codes: dict[str, int]
    first_pass: Ratio
    rework: int
    execution_time: Stat
    cost_usd: Stat


@dataclass(frozen=True)
class AssigneeReport:
    since: str | None
    until: str | None
    members: tuple[MemberRow, ...]
    agents: tuple[AgentRow, ...]
    unassigned_open: int
    responses_unknown_member: int


# ── 계산 ─────────────────────────────────────────────────────────────


def _assigned(works: Sequence[AssigneeWorkFact], type_: str, id_: str) -> list[AssigneeWorkFact]:
    return [w for w in works if w.assignee_type == type_ and w.assignee_id == id_]


def _done(works: Sequence[AssigneeWorkFact], since: str | None, until: str | None) -> int:
    return sum(1 for w in works if w.status == _DONE and w.closed_at and _in_window(w.closed_at, since, until))


def _open(works: Sequence[AssigneeWorkFact]) -> int:
    return sum(1 for w in works if w.status not in _FINISHED)


def _ordered_ids(labelled: Sequence[str], recorded: Iterable[str]) -> list[str]:
    """라벨 순서 뒤에 라벨 없는 기록 id 를 이름순으로."""
    return [*labelled, *sorted(set(recorded) - set(labelled))]


def _member_row(
    label: MemberLabel, facts: AssigneeFacts, responses: Sequence[ResponseFact],
    since: str | None, until: str | None, now: str,
) -> MemberRow:
    works = _assigned(facts.works, _MEMBER, label.member_id)
    turns = [w for w in facts.works if w.status == _HUMAN_TURN and label.member_id in w.recipients]
    waits = [_seconds(w.turn_since, now) for w in turns if w.turn_since is not None]
    answered = [_seconds(r.requested_at, r.responded_at) for r in responses if r.member_id == label.member_id]
    return MemberRow(
        member_id=label.member_id,
        display_name=label.display_name,
        active=label.active,
        done=_done(works, since, until),
        open=_open(works),
        turn_waiting=len(turns),
        longest_wait=max(waits) if waits else None,
        wait=_stat(waits, unknown=len(turns) - len(waits)),
        response_time=_stat(answered),
    )


def _first_pass(works: Sequence[AssigneeWorkFact], runs: Sequence[AgentRunFact]) -> Ratio:
    """phase 9 정의 — 업무의 첫 `code_review` 결과가 `approved`. 검토 결과 없는 업무는 미완료."""
    reviews: dict[str, list[AgentRunFact]] = {}
    for r in runs:
        if r.kind == _REVIEW_KIND and r.outcome is not None:
            reviews.setdefault(r.work_item_id, []).append(r)
    yes = total = missing = 0
    for w in works:
        found = reviews.get(w.work_item_id)
        if not found:
            missing += 1
            continue
        total += 1
        yes += min(found, key=lambda r: _parse(r.created_at)).outcome == "approved"
    return Ratio(yes, total, incomplete=missing)


def _agent_row(label: AgentLabel, facts: AssigneeFacts, since: str | None, until: str | None) -> AgentRow:
    works = _assigned(facts.works, _AGENT, label.agent_id)
    runs = [r for r in facts.runs if r.agent_id == label.agent_id and _in_window(r.created_at, since, until)]
    finished = [r for r in runs if r.status in _TERMINAL]
    unfinished = len(runs) - len(finished)
    failed = [r for r in finished if r.status == "failed"]
    durations = [_seconds(r.started_at, r.finished_at) for r in finished if r.started_at and r.finished_at]
    costs = [r.cost_usd for r in finished if r.cost_usd is not None]
    return AgentRow(
        agent_id=label.agent_id,
        name=label.name,
        done=_done(works, since, until),
        open=_open(works),
        runs=len(runs),
        failure=Ratio(len(failed), len(finished), incomplete=unfinished),
        failed_codes=dict(Counter(r.failed_code or UNKNOWN for r in failed)),
        first_pass=_first_pass(works, facts.runs),
        rework=sum(1 for r in runs if r.start_key.startswith(_REWORK_PREFIX)),
        execution_time=_stat(durations, incomplete=unfinished, unknown=len(finished) - len(durations)),
        cost_usd=_stat(costs, incomplete=unfinished, unknown=len(finished) - len(costs), with_total=True),
    )


def _has_record(row: MemberRow) -> bool:
    return bool(row.done or row.open or row.turn_waiting or row.response_time.n)


def compute_assignee_metrics(
    facts: AssigneeFacts, *, since: str | None, until: str | None, now: str
) -> AssigneeReport:
    """기간(`since` 이상 `until` 미만): 완료 = `closed_at`, 응답 = 응답 시각, 실행 = `created_at`.
    진행 중·내 차례 대기·담당 없음·1회 통과는 기간과 무관한 지금 값.

    멤버 행 = 활성 멤버 전부 + 기록이 있는 비활성 멤버, 에이전트 행 = 받은 Agent 전부. 라벨에 없는 id 가 기록에
    있으면 id 를 이름으로 한 행을 뒤에 붙인다(멤버는 비활성으로).
    """
    responses = [r for r in facts.responses if _in_window(r.responded_at, since, until)]

    member_labels = {m.member_id: m for m in facts.members}
    member_ids = _ordered_ids(list(member_labels), [
        *(w.assignee_id for w in facts.works if w.assignee_type == _MEMBER and w.assignee_id),
        *(m for w in facts.works if w.status == _HUMAN_TURN for m in w.recipients),
        *(r.member_id for r in responses if r.member_id),
    ])
    members = []
    for member_id in member_ids:
        label = member_labels.get(member_id) or MemberLabel(member_id, member_id, False)
        row = _member_row(label, facts, responses, since, until, now)
        if row.active or _has_record(row):
            members.append(row)

    agent_labels = {a.agent_id: a for a in facts.agents}
    agent_ids = _ordered_ids(list(agent_labels), [
        *(w.assignee_id for w in facts.works if w.assignee_type == _AGENT and w.assignee_id),
        *(r.agent_id for r in facts.runs if _in_window(r.created_at, since, until)),
    ])
    agents = tuple(
        _agent_row(agent_labels.get(agent_id) or AgentLabel(agent_id, agent_id), facts, since, until)
        for agent_id in agent_ids
    )

    return AssigneeReport(
        since=since,
        until=until,
        members=tuple(members),
        agents=agents,
        unassigned_open=sum(1 for w in facts.works if w.assignee_type is None and w.status not in _FINISHED),
        responses_unknown_member=sum(1 for r in responses if r.member_id is None),
    )
