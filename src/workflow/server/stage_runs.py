"""단계 착수 — 실행 만들기(`start_execution`)와 업무 순환이 아닌 단계의 착수(`run_task`) (ARCHITECTURE "사람 사이 인계 —
phase 17" 착수 코드 이동). `work_actions` 에서 옮겼다 — 워커도 쓰는데 `work_actions` 가 `worker` 를 import 해 순환이
생기기 때문이다. `work_actions` 는 같은 이름을 import 해 둔다. 오류는 `WorkActionError`(웹이 `PageError` 로 바꾼다).
"""

import json
import secrets
from collections.abc import Sequence
from sqlite3 import Connection, Row
from typing import Any
from uuid import uuid4

from pydantic import ValidationError

from workflow.adapters import repo
from workflow.adapters.errors import ActiveExecutionExists
from workflow.adapters.secret_store import SecretStore
from workflow.contracts.v1 import ExecutionRequest
from workflow.domain.delegation import offline_reason
from workflow.domain.start_key import request_start_key
from workflow.domain.status import user_status
from workflow.server import owner_approval, views
from workflow.server.settings import Settings


class WorkActionError(Exception):
    def __init__(self, status: int, code: str, message: str, field: str | None = None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.field = field


def refresh_task_status(
    conn: Connection, task_id: str, now: str, settings: Settings, *, review_decision: str | None = None
) -> None:
    """실행·선택·선행 상태를 보고 Task 의 사용자 상태를 다시 저장한다 (PRD 3절 대응표)."""
    row = repo.get_task(conn, task_id)
    status = user_status(views.build_task_view(conn, row, now=now, settings=settings))
    repo.update_task_status(
        conn, task_id, status.label, status.reason, review_decision=review_decision, now=now
    )


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


def run_task(conn: Connection, task: Row, *, session_id: str, now: str, settings: Settings, explicit: bool = True,
             secrets: SecretStore | None = None) -> None:
    """업무 순환이 아닌 Task 의 실행 — 조건 검사, 실행 생성, 상태 갱신. 사람이 누른 착수(`explicit`)와 워커의
    `_start_waiting_stages` 가 같이 쓴다. 소유자 승인 전(`owner_approval.gate`)이거나 선택 Agent 의 러너가 꺼져 있으면
    실행을 만들지 않고 409 — 조건이 풀리면 워커가 `start_pending_at` 으로 다시 시작한다(phase 17)."""
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

    state = owner_approval.gate(conn, task, agent, now=now, explicit=explicit, settings=settings, secrets=secrets)
    if state not in owner_approval.STARTABLE:
        refresh_task_status(conn, task_id, now, settings)
        reason = owner_approval.approval_fact(conn, repo.get_task(conn, task_id), agent).reason
        raise WorkActionError(409, "owner_approval_pending", reason)
    # 러너에 붙은 에이전트만 — 러너 보고 전(연결 프로그램 없음)이면 아래 요청 검사가 `request_incomplete` 로 답한다
    if agent["connector_id"] is not None and not views.agent_online(agent, now=now, settings=settings):
        raise WorkActionError(409, "runner_offline",
                              offline_reason(owner_approval.owner_name(conn, session_id, agent["agent_id"])))

    start_execution(
        conn, task, agent, session_id=session_id, now=now, settings=settings,
        input_artifact_ids=inputs, predecessor_execution_id=predecessor_execution_id,
        target=json.loads(task["target_json"]),
    )
    refresh_task_status(conn, task_id, now, settings)
