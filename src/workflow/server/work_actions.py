"""업무 동작 — 담당·우선순위 바꾸기와 단계 착수 (ADR-0022, ARCHITECTURE "업무 화면 — phase 16" 담당 바꾸기).

- 담당을 에이전트로 = 맡기기: 열린 단계에 그 에이전트를 직접 선택으로 지정하고(`select_agent(mode="manual")`) 지시 전
  GitHub 원본이면 운영자 지시를 남긴 뒤 착수한다. 지금 못 시작하면 오류가 아니다 — 대기 사유는 단계 상태에 남는다.
- 멤버 = 배정만, `none` = 담당 해제. 끝난 업무·활성 실행이 있는 업무는 409.
- 직접 작업: 시작 = 담당을 누른 멤버로 + 브랜치 이름(`branch_name`), 그만두기 = 칸만 비움(담당 그대로). 에이전트나 다른
  멤버를 담당으로 고르면 직접 작업이 먼저 끝난다(repo 가 같은 트랜잭션에서). 결과 판정·완료 처리는 하지 않는다.
- 착수 코드(`run_task`·`run_cycle_task`)는 웹의 `/tasks/{id}/run`·`/select`·체인 시작·검토 재요청과 함께 쓴다.
  `run_task`·`start_execution`·`WorkActionError` 는 워커도 쓰므로 `stage_runs` 에 있다(phase 17).
- 오류는 `WorkActionError` — 웹이 `PageError` 로 바꾼다. 이 모듈은 `web` 을 import 하지 않는다.
"""

import json
import re
from sqlite3 import Connection, Row
from typing import Any

from workflow.adapters import repo
from workflow.adapters.secret_store import SecretStore
from workflow.contracts.v1 import Capability, KindSpec, format_work_key
from workflow.domain.execution_policy import policy_for
from workflow.domain.field_mapping import PRIORITIES
from workflow.domain.handoff_context import HANDOFF_NOTE_MAX
from workflow.domain.selection import Candidate, select_agent
from workflow.domain.work_keys import branch_name
from workflow.domain.work_status import TERMINAL_WORK_STATUSES
from workflow.server import owner_approval, task_cycle
from workflow.server.settings import Settings
from workflow.server.stage_runs import (  # noqa: F401 — 옮긴 이름을 그대로 둔다(웹·테스트가 work_actions 로 부른다)
    WorkActionError,
    refresh_task_status,
    run_task,
    start_execution,
)
from workflow.server.worker import Worker

_ASSIGNEE = re.compile(r"(member|agent):([A-Za-z0-9._-]+)")


def _invalid_assignee(message: str) -> WorkActionError:
    return WorkActionError(422, "invalid_field", message, field="assignee")


def parse_assignee(value: str) -> tuple[str, str] | None:
    """폼 값 `member:<id>`·`agent:<id>`·`none` → `("member", id)`·`("agent", id)`·None. 형식이 틀리면 422."""
    if value == "none":
        return None
    match = _ASSIGNEE.fullmatch(value)
    if match is None:
        raise _invalid_assignee("담당 값이 올바르지 않습니다.")
    return match.group(1), match.group(2)


# --- 후보·대상·상태 ----------------------------------------------------------------


def candidates(conn: Connection, session_id: str) -> list[Candidate]:
    """워크스페이스에 붙은 Agent(`session_agents`) 전부 — 선택 판단의 후보."""
    return [
        Candidate(
            agent_id=a["agent_id"],
            capabilities=tuple(Capability.model_validate(c) for c in json.loads(a["capabilities_json"])),
        )
        for a in repo.list_session_agents(conn, session_id)
    ]


def target_for(spec: KindSpec, agent: Row | None) -> dict[str, Any]:
    """Task 의 target. 선택된 Agent 의 등록값(연결 프로그램이 보고한 값)에서 온다 — 폼에서 받지 않는다.
    코드 수정 대상(`bug_fix`, 실행 정책 target `code_change`)은 등록·기준 커밋·첫 검증 프로필, 그 밖은 등록 ID 하나.
    Agent 가 아직 없으면 비워 두고 선택 뒤에 채운다."""
    if agent is None:
        return {}
    if policy_for(spec.kind).target == "code_change":
        profiles = json.loads(agent["verification_profile_ids_json"])
        return {
            "local_registration_id": agent["local_registration_id"],
            "base_commit": agent["base_commit"],
            "verification_profile_id": profiles[0] if profiles else None,
        }
    return {"local_registration_id": agent["local_registration_id"]}


# --- 착수 -------------------------------------------------------------------------


def run_cycle_task(conn: Connection, store: Any, task: Row, *, now: str, settings: Settings) -> None:
    """업무 순환 Task 의 직접 실행 — 워커와 같은 준비 판정·후속 결정·start_key 로 착수하고(`Worker.start_manually`)
    `manual_mode` 만 뺀다. 대상·입력은 워커가 Agent 등록값·원인 결과로 고정한다. 새 실행이 없으면 지금 대기 사유로 409."""
    if task["finished_at"] is not None:
        raise WorkActionError(409, "invalid_transition", "마감된 업무는 실행할 수 없습니다.")
    worker = Worker(lambda: conn, store, None, settings, lambda: now)  # 착수 단계만 쓴다 — callback 없음
    if worker.start_manually(conn, task["task_id"]):
        return
    readiness = task_cycle.evaluate(conn, repo.get_task(conn, task["task_id"]), now=now, settings=settings,
                                    run_mode="auto")
    reasons = " · ".join(b.reason for b in readiness.blockers) or "이미 실행이 있거나 이어서 시작할 결과가 없습니다"
    raise WorkActionError(409, "invalid_transition", f"지금 시작할 수 없습니다 — {reasons}.")


def start_stage(conn: Connection, store: Any, settings: Settings, task: Row, *, session_id: str, now: str,
                strict: bool, secrets: SecretStore | None = None) -> bool:
    """사람이 맡긴 착수 — 업무 순환 종류는 `Worker.start_manually`, 그 밖은 `run_task`. `strict=True` 는 `/run` 처럼 못
    시작하면 `WorkActionError`, `False` 는 조용히 False(대기 사유는 단계 상태에 남는다). 먼저 착수 대기
    (`start_pending_at`)를 표시하고 소유자 승인을 본다 — 승인이 필요하면 요청을 열고(`human_request` 알림이 소유자에게),
    필요 없으면 맡긴 사람 ≠ 소유자일 때 `delegated_to_you`. 조건이 풀리면 워커가 시작한다(phase 17)."""
    repo.mark_start_pending(conn, task["task_id"], now=now)
    selection = repo.get_selection(conn, task["task_id"])
    agent_id = task["chosen_agent_id"] or (selection.selected_agent_id if selection is not None else None)
    if agent_id is not None:
        state = owner_approval.ensure_request(conn, task, agent_id, now=now, explicit=True, settings=settings,
                                              secrets=secrets)
        if state in owner_approval.STARTABLE:
            owner_approval.notify_delegated(conn, task, agent_id, now=now, settings=settings, secrets=secrets)
    try:
        if policy_for(task["kind"]).cycle:
            run_cycle_task(conn, store, task, now=now, settings=settings)
        else:
            run_task(conn, task, session_id=session_id, now=now, settings=settings, secrets=secrets)
    except WorkActionError:
        if strict:
            raise
        return False
    return True


# --- 담당·우선순위 ------------------------------------------------------------------


def open_stage(conn: Connection, work_item_id: str) -> Row | None:
    """맡길 단계 = 업무의 마감 전 단계 중 가장 최근(`created_at`, `rowid` 순 마지막)."""
    return conn.execute(
        "SELECT * FROM tasks WHERE work_item_id = ? AND finished_at IS NULL ORDER BY created_at DESC, rowid DESC"
        " LIMIT 1", (work_item_id,)
    ).fetchone()


def _accepts(stage: Row, pool: list[Candidate], agent_id: str) -> bool:
    capability = Capability.model_validate_json(stage["required_capability_json"])
    record = select_agent(stage["task_id"], capability, pool, mode="manual", chosen_agent_id=agent_id)
    return record.status == "selected"


def agent_candidates(conn: Connection, session_id: str, work_item_id: str) -> list[Row]:
    """담당 후보 에이전트 — 열린 단계를 맡을 수 있는 워크스페이스 Agent(`assign_work` 와 같은 판정). 열린 단계가 없으면 빈 목록."""
    stage = open_stage(conn, work_item_id)
    if stage is None:
        return []
    pool = candidates(conn, session_id)
    return [a for a in repo.list_session_agents(conn, session_id) if _accepts(stage, pool, a["agent_id"])]


def _own_open_work(conn: Connection, session_id: str, work_item_id: str) -> Row:
    work = repo.get_work_item(conn, session_id, work_item_id)
    if work is None:
        raise WorkActionError(404, "not_found", "업무를 찾을 수 없습니다.")
    if work["status"] in TERMINAL_WORK_STATUSES:
        raise WorkActionError(409, "work_closed", "끝난 업무는 바꿀 수 없습니다.")
    return work


def _running(conn: Connection, work_item_id: str) -> bool:
    return any(repo.active_execution(conn, t["task_id"]) is not None
               for t in repo.list_work_item_tasks(conn, work_item_id))


DIRECT_WORK_ACTIVE = "직접 작업 중인 업무입니다. 먼저 직접 작업을 그만두세요."


def assign_work(conn: Connection, store: Any, settings: Settings, *, session_id: str, work_item_id: str, value: str,
                member_id: str, now: str, secrets: SecretStore | None = None, note: str = "") -> None:
    """담당 바꾸기 — 에이전트 = 맡기기(쓰기 한 트랜잭션 뒤 착수), 멤버 = 배정만(활성 멤버), `none` = 해제.
    직접 작업 중이면 에이전트·다른 멤버는 그 직접 작업을 끝내고(repo), `none` 은 409 `direct_work_active`.
    지시 메모 `note` 는 에이전트일 때만 쓴다(앞뒤 공백 제거, 빈 값 = 메모 없음, `HANDOFF_NOTE_MAX` 자 넘으면 422)."""
    assignee = parse_assignee(value)
    work = _own_open_work(conn, session_id, work_item_id)
    if _running(conn, work_item_id):
        raise WorkActionError(409, "execution_conflict", "실행 중인 업무는 담당을 바꿀 수 없습니다.")
    if assignee is None and work["direct_member_id"] is not None:
        raise WorkActionError(409, "direct_work_active", DIRECT_WORK_ACTIVE)
    if assignee is None:
        repo.assign_work_item(conn, session_id, work_item_id, assignee_type=None, assignee_id=None,
                              by_member_id=member_id, now=now)
        return
    kind, assignee_id = assignee
    note = note.strip()
    if kind == "agent" and len(note) > HANDOFF_NOTE_MAX:
        raise WorkActionError(422, "invalid_field", f"지시 메모는 {HANDOFF_NOTE_MAX}자까지 쓸 수 있습니다.", field="note")
    if kind == "member":
        member = repo.get_member(conn, session_id, assignee_id)
        if member is None or member["disabled_at"] is not None:
            raise _invalid_assignee("활성 멤버만 담당으로 지정할 수 있습니다.")
        repo.assign_work_item(conn, session_id, work_item_id, assignee_type="member", assignee_id=assignee_id,
                              by_member_id=member_id, now=now)
        return
    stage = open_stage(conn, work_item_id)
    if stage is None:
        raise WorkActionError(409, "no_open_stage", "맡길 단계가 없습니다.")
    capability = Capability.model_validate_json(stage["required_capability_json"])
    record = select_agent(stage["task_id"], capability, candidates(conn, session_id), mode="manual",
                          chosen_agent_id=assignee_id)
    if record.status != "selected":
        raise _invalid_assignee("이 단계를 맡을 수 없는 에이전트입니다.")
    target = target_for(repo.get_kind(conn, session_id, stage["kind"]), repo.get_agent(conn, assignee_id))
    repo.hand_work_to_agent(conn, session_id, work_item_id, record=record, target=target, member_id=member_id,
                            now=now, note=note or None)
    refresh_task_status(conn, stage["task_id"], now, settings)
    start_stage(conn, store, settings, repo.get_task(conn, stage["task_id"]), session_id=session_id, now=now,
                strict=False, secrets=secrets)


def set_priority(conn: Connection, *, session_id: str, work_item_id: str, priority: str, member_id: str,
                 now: str) -> None:
    """우선순위 `high`·`normal`·`low`(아니면 422). 끝난 업무면 409. 바뀌면 `priority_changed` 이벤트."""
    if priority not in PRIORITIES:
        raise WorkActionError(422, "invalid_field", "우선순위 값이 올바르지 않습니다.", field="priority")
    _own_open_work(conn, session_id, work_item_id)
    repo.set_work_priority(conn, session_id, work_item_id, priority, member_id=member_id, now=now)


# --- 직접 작업 ----------------------------------------------------------------------


def start_direct(conn: Connection, *, session_id: str, work_item_id: str, member_id: str, now: str) -> str:
    """[내 세션에서 작업] — 담당 = 누른 멤버(다른 멤버 담당이어도), 직접 작업 칸 채움, 상태 `직접 작업 중`. 끝난 업무·
    에이전트 실행 중이면 409. 반환은 브랜치 이름(같은 멤버가 다시 누르면 처음 값). 서버는 이 이름으로 아무것도 실행하지 않는다."""
    work = _own_open_work(conn, session_id, work_item_id)
    if _running(conn, work_item_id):
        raise WorkActionError(409, "execution_conflict", "에이전트가 실행 중인 업무는 직접 작업을 시작할 수 없습니다.")
    if work["direct_member_id"] == member_id:
        return work["direct_branch"]
    branch = branch_name(format_work_key(work["key_number"]), work["title"])
    repo.start_direct_work(conn, session_id, work_item_id, member_id=member_id, branch=branch, now=now)
    return branch


def stop_direct(conn: Connection, *, session_id: str, work_item_id: str, now: str) -> None:
    """[직접 작업 그만두기] — 칸만 비우고 담당은 그대로. 직접 작업 중이 아니면 변화 없음."""
    if repo.get_work_item(conn, session_id, work_item_id) is None:
        raise WorkActionError(404, "not_found", "업무를 찾을 수 없습니다.")
    repo.stop_direct_work(conn, work_item_id, reason="stopped", now=now)
