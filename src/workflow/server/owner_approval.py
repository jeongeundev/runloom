"""소유자 승인 — 승인 상태 계산·승인 요청 열기·맡기기 알림 (ADR-0023 결정 1·2, ARCHITECTURE "사람 사이 인계 — phase 17").

승인 범위 = (단계, 에이전트, 맡긴 사람 `work_items.requested_by_member_id`). 범위의 가장 최근 요청과
`delegation.needs_owner_approval` 로 상태를 정한다(`delegation.approval_state`). 요청은 사람 요청 `owner_approval` 이고
응답은 `human_api`(approve·decline)가 받는다. 실행을 만들지 않는다 — 착수는 `stage_runs`·워커가 이 상태를 보고 한다.
알림 행은 `worker.enqueue_event_notification`(웹·워커 공용)으로 쌓는다. `secrets` 가 없으면 쌓지 않는다.
"""

from sqlite3 import Connection, Row

from workflow.adapters import repo
from workflow.adapters.secret_store import SecretStore
from workflow.contracts.v1 import format_work_key
from workflow.domain import delegation
from workflow.domain.delegation import ApprovalFact
from workflow.domain.team import MemberFact
from workflow.server import worker as worker_module  # 순환 import — 속성은 호출 때 읽는다(워커가 이 모듈을 쓴다)
from workflow.server.settings import Settings

STARTABLE = ("not_needed", "approved")  # 실행을 만들어도 되는 승인 상태


class _Team:
    """워크스페이스 멤버 사실과 표시 이름 — 한 번 읽어 여러 에이전트 판정에 쓴다."""

    def __init__(self, conn: Connection, session_id: str):
        rows = repo.list_members(conn, session_id)
        self.members = [MemberFact(member_id=m["member_id"], role=m["role"], active=m["disabled_at"] is None)
                        for m in rows]
        self.names = {m["member_id"]: m["display_name"] for m in rows}
        self.admin_ids = frozenset(m.member_id for m in self.members if m.active and m.role == "admin")

    def active_name(self, member_id: str | None) -> str | None:
        """활성 멤버의 표시 이름. 없거나 비활성이면 None(공용·관리자 문구)."""
        if any(m.member_id == member_id and m.active for m in self.members):
            return self.names[member_id]
        return None


def _requester(conn: Connection, task: Row) -> str | None:
    work = repo.work_item_of_task(conn, task["task_id"]) if task["work_item_id"] else None
    return work["requested_by_member_id"] if work is not None else None


def _fact(conn: Connection, task: Row, agent: Row, team: _Team, requester: str | None,
          requests: list[Row]) -> ApprovalFact:
    owner_id = repo.agent_owner_id(conn, agent["agent_id"])
    needed = delegation.needs_owner_approval(policy=agent["delegation_policy"], requester_id=requester,
                                             owner_id=owner_id, admin_ids=team.admin_ids)
    scope = [r for r in requests
             if (delegation.parse_approval_cause_key(r["cause_key"]) or (None, None))[:2] == (agent["agent_id"],
                                                                                            requester)]
    latest = scope[-1] if scope else None
    state = delegation.approval_state(needed=needed, latest_state=latest["state"] if latest else None,
                                      latest_action=latest["action"] if latest else None)
    if state == "declined":
        reason = f"{delegation.declined_reason(team.names.get(latest['responder_id']), None)} — 다른 담당을 고르세요"
    else:
        reason = _question(team, requester, owner_id, agent).split("\n", 1)[0]
    return ApprovalFact(state=state, reason=reason)


def _question(team: _Team, requester: str | None, owner_id: str | None, agent: Row) -> str:
    return delegation.approval_question(team.names.get(requester) if requester else None,
                                        team.active_name(owner_id), agent["name"])


def approval_fact(conn: Connection, task: Row, agent: Row) -> ApprovalFact:
    """단계 × 에이전트 하나의 승인 상태와 대기 이유."""
    return _fact(conn, task, agent, _Team(conn, task["session_id"]), _requester(conn, task),
                 repo.list_owner_approvals(conn, task["task_id"]))


def approval_facts(conn: Connection, task: Row) -> dict[str, ApprovalFact]:
    """워크스페이스 Agent 마다의 승인 상태 — 준비 판정(`TaskFacts.owner_approvals`) 재료."""
    team = _Team(conn, task["session_id"])
    requester = _requester(conn, task)
    requests = repo.list_owner_approvals(conn, task["task_id"])
    return {a["agent_id"]: _fact(conn, task, a, team, requester, requests)
            for a in repo.list_session_agents(conn, task["session_id"])}


def ensure_request(conn: Connection, task: Row, agent_id: str, *, now: str, explicit: bool, settings: Settings,
                   secrets: SecretStore | None) -> str:
    """승인 상태를 돌려주고, `missing`(또는 사람이 다시 맡긴 `declined`)이면 승인 요청을 연다 → `pending`. 새 요청이면
    `human_request` 알림(받는 사람 = 승인자). 워커(`explicit=False`)는 거절된 범위를 다시 묻지 않는다."""
    agent = repo.get_agent(conn, agent_id)
    if agent is None:
        return "not_needed"
    team = _Team(conn, task["session_id"])
    requester = _requester(conn, task)
    state = _fact(conn, task, agent, team, requester, repo.list_owner_approvals(conn, task["task_id"])).state
    if state != "missing" and not (state == "declined" and explicit):
        return state
    question = _question(team, requester, repo.agent_owner_id(conn, agent_id), agent)
    request_id, created = repo.open_owner_approval(conn, task["task_id"], agent_id=agent_id, requester_id=requester,
                                                   question=question, now=now)
    if created and secrets is not None:
        worker_module.enqueue_event_notification(
            conn, settings, secrets, event="human_request", task_id=task["task_id"],
            dedupe_key=f"human_request:{request_id}", detail=question, now=now,
        )
    return "pending"


def gate(conn: Connection, task: Row, agent: Row, *, now: str, explicit: bool, settings: Settings,
         secrets: SecretStore | None) -> str:
    """실행을 만들기 직전의 검사 — `ensure_request` 결과. `STARTABLE` 밖이면 호출자는 실행을 만들지 않는다."""
    return ensure_request(conn, task, agent["agent_id"], now=now, explicit=explicit, settings=settings,
                          secrets=secrets)


def owner_name(conn: Connection, session_id: str, agent_id: str) -> str | None:
    """에이전트 소유자의 표시 이름 — 공용·비활성 소유자면 None(꺼진 러너 문구가 `공용`)."""
    return _Team(conn, session_id).active_name(repo.agent_owner_id(conn, agent_id))


def notify_delegated(conn: Connection, task: Row, agent_id: str, *, now: str, settings: Settings,
                     secrets: SecretStore | None) -> None:
    """`delegated_to_you` — 맡긴 사람이 소유자(공용이면 활성 관리자)가 아니면 승인자에게 정보 알림 한 번."""
    agent = repo.get_agent(conn, agent_id)
    requester = _requester(conn, task)
    if secrets is None or agent is None or requester is None:
        return
    team = _Team(conn, task["session_id"])
    owner_id = repo.agent_owner_id(conn, agent_id)
    if requester == owner_id or (owner_id is None and requester in team.admin_ids):
        return
    worker_module.enqueue_event_notification(
        conn, settings, secrets, event="delegated_to_you", task_id=task["task_id"],
        dedupe_key=f"delegated_to_you:{task['task_id']}:{agent_id}",
        recipients=delegation.approval_deciders(owner_id, team.members),
        detail=f"{team.names.get(requester, requester)} 가 {agent['name']} 에게 맡김", now=now,
    )


def notify_offline(conn: Connection, task: Row, agent_id: str, *, now: str, settings: Settings,
                   secrets: SecretStore | None) -> None:
    """`runner_offline_waiting` — 업무 × 에이전트마다 한 번(러너가 켜졌다 다시 꺼져도 다시 보내지 않는다)."""
    work = repo.work_item_of_task(conn, task["task_id"]) if task["work_item_id"] else None
    if secrets is None or work is None:
        return
    team = _Team(conn, task["session_id"])
    worker_module.enqueue_event_notification(
        conn, settings, secrets, event="runner_offline_waiting", task_id=task["task_id"],
        dedupe_key=f"runner_offline:{work['work_item_id']}:{agent_id}",
        recipients=delegation.approval_deciders(repo.agent_owner_id(conn, agent_id), team.members),
        detail=f"러너가 꺼져 있어 {format_work_key(work['key_number'])} 이 기다림", now=now,
    )


def notify_declined(conn: Connection, request: Row, *, note: str, member_id: str | None, now: str,
                    settings: Settings, secrets: SecretStore | None) -> None:
    """`delegation_declined` — 맡긴 사람(요청 원인 키의 멤버, 활성일 때만)에게 `이OO 가 거절 — 메모 첫 줄`."""
    scope = delegation.parse_approval_cause_key(request["cause_key"])
    task = repo.get_task(conn, request["task_id"])
    if secrets is None or scope is None or scope[1] is None:
        return
    team = _Team(conn, task["session_id"])
    if team.active_name(scope[1]) is None:
        return
    worker_module.enqueue_event_notification(
        conn, settings, secrets, event="delegation_declined", task_id=task["task_id"],
        dedupe_key=f"delegation_declined:{request['request_id']}", recipients=(scope[1],),
        detail=delegation.declined_reason(team.names.get(member_id) if member_id else None, note), now=now,
    )
