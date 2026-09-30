"""업무 동작 — 담당·우선순위 바꾸기와 단계 착수 (ADR-0022, ARCHITECTURE "업무 화면 — phase 16" 담당 바꾸기).

- 담당을 에이전트로 = 맡기기: 열린 단계에 그 에이전트를 직접 선택으로 지정하고(`select_agent(mode="manual")`) 지시 전
  GitHub 원본이면 운영자 지시를 남긴 뒤 착수한다. 지금 못 시작하면 오류가 아니다 — 대기 사유는 단계 상태에 남는다.
- 멤버 = 배정만, `none` = 담당 해제. 끝난 업무·활성 실행이 있는 업무는 409.
- 착수 코드(`run_task`·`run_cycle_task`)는 웹의 `/tasks/{id}/run`·`/select`·체인 시작·검토 재요청과 함께 쓴다.
- 오류는 `WorkActionError` — 웹이 `PageError` 로 바꾼다. 이 모듈은 `web` 을 import 하지 않는다.
"""

import json
import re
import secrets
from collections.abc import Sequence
from sqlite3 import Connection, Row
from typing import Any
from uuid import uuid4

from pydantic import ValidationError

from workflow.adapters import repo
from workflow.adapters.errors import ActiveExecutionExists
from workflow.contracts.v1 import Capability, ExecutionRequest, KindSpec
from workflow.domain.execution_policy import policy_for
from workflow.domain.field_mapping import PRIORITIES
from workflow.domain.selection import Candidate, select_agent
from workflow.domain.start_key import request_start_key
from workflow.domain.status import user_status
from workflow.domain.work_status import TERMINAL_WORK_STATUSES
from workflow.server import task_cycle, views
from workflow.server.settings import Settings
from workflow.server.worker import Worker

_ASSIGNEE = re.compile(r"(member|agent):([A-Za-z0-9._-]+)")


class WorkActionError(Exception):
    def __init__(self, status: int, code: str, message: str, field: str | None = None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.field = field


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


def refresh_task_status(
    conn: Connection, task_id: str, now: str, settings: Settings, *, review_decision: str | None = None
) -> None:
    """실행·선택·선행 상태를 보고 Task 의 사용자 상태를 다시 저장한다 (PRD 3절 대응표)."""
    row = repo.get_task(conn, task_id)
    status = user_status(views.build_task_view(conn, row, now=now, settings=settings))
    repo.update_task_status(
        conn, task_id, status.label, status.reason, review_decision=review_decision, now=now
    )


# --- 착수 -------------------------------------------------------------------------


def start_execution(
    conn: Connection,
    task: Row,
    agent: Row,
    *,
    session_id: str,
    now: str,
    settings: Settings,
    input_artifact_ids: Sequence[str],
    predecessor_execution_id: str | None,
    target: dict[str, Any],
) -> str:
    """새 시도를 `queued` 로 만든다. 요청은 여기서 고정되고 이후 바뀌지 않는다 — 종류 봉투(`kind_spec`)도 등록부에서
    이때 채운다. 반환은 execution_id."""
    kind = task["kind"]
    spec = repo.get_kind(conn, session_id, kind)
    if spec is None:
        raise WorkActionError(409, "request_incomplete", "실행 요청을 만들 수 없습니다. 업무 종류가 등록돼 있지 않습니다.")
    attempts = repo.list_executions(conn, task["task_id"])
    attempt_no = attempts[-1]["attempt_no"] + 1 if attempts else 1
    execution_id = f"exec-{secrets.token_hex(8)}"
    try:
        request = ExecutionRequest.model_validate({
            "contract_version": 1,
            "execution_id": execution_id,
            "task_id": task["task_id"],
            "kind": kind,
            "agent_id": agent["agent_id"],
            "task_revision": task["revision"],
            "request": task["request"],
            "input_artifact_ids": list(input_artifact_ids),
            "target": target,
            "kind_spec": spec.model_dump(),
            **repo.execution_branch_fields(conn, task["task_id"]),
        })
    except ValidationError:
        raise WorkActionError(
            409,
            "request_incomplete",
            "실행 요청을 만들 수 없습니다. 대상 등록 정보(연결 프로그램의 등록 보고)나 선행 업무의 "
            "인계 자료가 아직 없습니다.",
        ) from None
    try:
        repo.create_execution(
            conn,
            execution_id=execution_id,
            task_id=task["task_id"],
            attempt_no=attempt_no,
            start_key=request_start_key(uuid4().hex),
            agent_id=agent["agent_id"],
            kind=kind,
            request=request,
            assigned_connector_id=agent["connector_id"],
            predecessor_execution_id=predecessor_execution_id,
            now=now,
        )
    except ActiveExecutionExists:
        raise WorkActionError(409, "execution_conflict", "이미 활성 실행이 있습니다.") from None
    return execution_id


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


def run_task(conn: Connection, task: Row, *, session_id: str, now: str, settings: Settings) -> None:
    """업무 순환이 아닌 Task 의 직접 실행 — 조건 검사, 실행 생성, 상태 갱신."""
    task_id = task["task_id"]
    if task["finished_at"] is not None:
        raise WorkActionError(409, "invalid_transition", "마감된 업무는 실행할 수 없습니다.")
    if repo.active_execution(conn, task_id) is not None:
        raise WorkActionError(409, "execution_conflict", "이미 활성 실행이 있습니다.")
    selection = repo.get_selection(conn, task_id)
    if selection is None or selection.status != "selected":
        raise WorkActionError(409, "invalid_transition", "에이전트가 아직 선택되지 않았습니다.")
    # 선행이 있으면 선행 결과가 판정되고 인계 묶음이 있어야 한다 — 선행 `완료`(사람 승인)를 기다리지 않는다 (ADR-0009 (3)).
    # 묶음은 워커가 규칙 `handoff_kinds` 로 조립하므로 규칙이 없으면 생기지 않는다.
    inputs, predecessor_execution_id = views.predecessor_handoff(conn, task)
    if task["predecessor_task_id"] is not None and not inputs:
        predecessor = repo.get_task(conn, task["predecessor_task_id"])
        if predecessor is not None and repo.get_rule(conn, session_id, predecessor["kind"], task["kind"]) is None:
            raise WorkActionError(
                409, "invalid_transition", "후속 규칙이 없어 인계 자료가 없습니다. /kinds 에서 규칙을 등록하세요.",
            )
        raise WorkActionError(
            409, "invalid_transition",
            "선행 업무의 결과와 인계 자료가 아직 준비되지 않았습니다. 선행 결과가 판정을 통과하고 규칙에 맞으면 워커가 조립합니다.",
        )
    agent = repo.get_agent(conn, selection.selected_agent_id)
    if agent is None:
        raise WorkActionError(409, "invalid_transition", "선택된 에이전트가 더 이상 등록돼 있지 않습니다.")

    start_execution(
        conn, task, agent, session_id=session_id, now=now, settings=settings,
        input_artifact_ids=inputs, predecessor_execution_id=predecessor_execution_id,
        target=json.loads(task["target_json"]),
    )
    refresh_task_status(conn, task_id, now, settings)


def start_stage(conn: Connection, store: Any, settings: Settings, task: Row, *, session_id: str, now: str,
                strict: bool) -> bool:
    """단계 착수 — 업무 순환 종류는 `Worker.start_manually`, 그 밖은 `run_task`. `strict=True` 는 `/run` 처럼 못 시작하면
    `WorkActionError`, `False` 는 조용히 False(대기 사유는 단계 상태에 남는다)."""
    try:
        if policy_for(task["kind"]).cycle:
            run_cycle_task(conn, store, task, now=now, settings=settings)
        else:
            run_task(conn, task, session_id=session_id, now=now, settings=settings)
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


def assign_work(conn: Connection, store: Any, settings: Settings, *, session_id: str, work_item_id: str, value: str,
                member_id: str, now: str) -> None:
    """담당 바꾸기 — 에이전트 = 맡기기(쓰기 한 트랜잭션 뒤 착수), 멤버 = 배정만(활성 멤버), `none` = 해제."""
    assignee = parse_assignee(value)
    _own_open_work(conn, session_id, work_item_id)
    if any(repo.active_execution(conn, t["task_id"]) is not None
           for t in repo.list_work_item_tasks(conn, work_item_id)):
        raise WorkActionError(409, "execution_conflict", "실행 중인 업무는 담당을 바꿀 수 없습니다.")
    if assignee is None:
        repo.assign_work_item(conn, session_id, work_item_id, assignee_type=None, assignee_id=None,
                              by_member_id=member_id, now=now)
        return
    kind, assignee_id = assignee
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
                            now=now)
    refresh_task_status(conn, stage["task_id"], now, settings)
    start_stage(conn, store, settings, repo.get_task(conn, stage["task_id"]), session_id=session_id, now=now,
                strict=False)


def set_priority(conn: Connection, *, session_id: str, work_item_id: str, priority: str, member_id: str,
                 now: str) -> None:
    """우선순위 `high`·`normal`·`low`(아니면 422). 끝난 업무면 409. 바뀌면 `priority_changed` 이벤트."""
    if priority not in PRIORITIES:
        raise WorkActionError(422, "invalid_field", "우선순위 값이 올바르지 않습니다.", field="priority")
    _own_open_work(conn, session_id, work_item_id)
    repo.set_work_priority(conn, session_id, work_item_id, priority, member_id=member_id, now=now)
