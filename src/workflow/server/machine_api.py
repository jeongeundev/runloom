"""기계가 호출하는 중앙 API — 연결 프로그램(운영자 Mac) 전용. CONTRACT 2절(claim)·3절(이벤트)·4절(산출물).

사람 화면은 Step 6. 실행 생성·진단 API 호출은 워커(Step 8) 의 몫이라 여기 없다.
요청 본문에서 경로·명령을 받아 실행하는 곳은 없다. 발신자 신원은 토큰·배정으로만 정한다.
"""

import json
from sqlite3 import Connection, Row
from typing import Any, Literal

from fastapi import APIRouter, Depends, File, Form, Request, Response, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from workflow.adapters import repo
from workflow.adapters.errors import NotFound, RegistrationTaken
from workflow.contracts.v1 import (
    ArtifactMeta,
    ClaimRequest,
    CommitSha,
    ContractVersion,
    ExecutionEvent,
    ExecutionRequest,
    HeartbeatRequest,
    NonEmptyStr,
)
from workflow.server.auth import SELFHOST_SESSION_ID, ensure_workspace, get_conn, require_connector, utc_now
from workflow.server.errors import ApiError

DISCOVERED_MAX_BYTES = 64 * 1024

router = APIRouter()


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ExchangeRequest(_Body):
    contract_version: ContractVersion
    connect_code: NonEmptyStr


class RegistrationRequest(_Body):
    contract_version: ContractVersion
    connector_id: NonEmptyStr
    local_registration_id: NonEmptyStr
    tool: Literal["codex", "claude"]
    repository_id: NonEmptyStr
    base_commit: CommitSha
    verification_profile_ids: list[NonEmptyStr]
    discovered: dict[str, Any]
    agent_name: NonEmptyStr | None = Field(default=None, max_length=100)  # Agent 를 새로 만들 때의 이름(ADR-0018)


class RegistrationResponse(_Body):
    """CONTRACT 14.4 — `created` 는 이번 요청이 Agent 를 만들었는가. 구버전 러너는 `agent_id` 만 읽는다."""

    agent_id: NonEmptyStr
    created: bool


def _json(model: BaseModel, status: int = 200) -> JSONResponse:
    return JSONResponse(status_code=status, content=model.model_dump(mode="json"))


def _check_connector_id(claimed: str, connector_id: str) -> None:
    if claimed != connector_id:
        raise ApiError(
            403, "forbidden", f"connector_id {claimed}는 인증된 연결 프로그램과 다릅니다.",
            field="connector_id",
        )


def _assigned_execution(conn: Connection, execution_id: str, connector_id: str) -> Row:
    row = repo.get_execution(conn, execution_id)
    if row is None:
        raise ApiError(
            404, "not_found", f"execution {execution_id}을 찾을 수 없습니다.", field="execution_id"
        )
    if row["assigned_connector_id"] != connector_id:
        raise ApiError(403, "forbidden", f"{execution_id}은 {connector_id}에 배정되지 않았습니다.")
    return row


# --- 연결 프로그램 -------------------------------------------------------------


@router.post("/connector/exchange")
def exchange(body: ExchangeRequest, conn: Connection = Depends(get_conn)) -> dict[str, str]:
    """연결 코드 → `connector_id` 와 연결 토큰. 토큰은 이 응답에만 한 번 나온다."""
    try:
        connector_id, token = repo.exchange_connect_code(conn, body.connect_code, utc_now())
    except NotFound:
        raise ApiError(
            404, "not_found", "연결 코드를 찾을 수 없습니다.", field="connect_code"
        ) from None
    return {"connector_id": connector_id, "token": token}


@router.post("/connector/claim")
def claim(
    body: ClaimRequest,
    connector_id: str = Depends(require_connector),
    conn: Connection = Depends(get_conn),
) -> Response:
    _check_connector_id(body.connector_id, connector_id)
    repo.record_supported_kinds(conn, connector_id, body.supported_kinds)  # 준비 판정(executor_outdated)의 입력
    row = repo.claim_execution(conn, connector_id, utc_now())
    if row is None:
        return Response(status_code=204)
    return _json(ExecutionRequest.model_validate_json(row["request_json"]))


@router.post("/connector/heartbeat")
def heartbeat(
    body: HeartbeatRequest,
    connector_id: str = Depends(require_connector),
    conn: Connection = Depends(get_conn),
) -> dict:
    _check_connector_id(body.connector_id, connector_id)
    now = utc_now()
    repo.touch_connector(conn, connector_id, now, body.current_execution_id)
    for agent in repo.agents_for_connector(conn, connector_id):
        repo.set_agent_connection(conn, agent["agent_id"], "online", now)
    return {}


@router.post("/connector/registrations")
def register(
    body: RegistrationRequest,
    request: Request,
    connector_id: str = Depends(require_connector),
    conn: Connection = Depends(get_conn),
) -> JSONResponse:
    """`local_registration_id` 가 일치하는 Agent 의 연결 정보를 채운다. 없으면 selfhost 에서만 고정 워크스페이스에
    `code.fix`·`code.review` Agent 를 만든다(ADR-0018 결정 1). demo 는 지금처럼 404."""
    _check_connector_id(body.connector_id, connector_id)
    if len(json.dumps(body.discovered, ensure_ascii=False).encode()) > DISCOVERED_MAX_BYTES:
        raise ApiError(422, "invalid_field", "discovered는 64KB를 넘을 수 없습니다.", field="discovered")
    now = utc_now()
    session_id = None
    if request.app.state.settings.mode == "selfhost":
        ensure_workspace(conn, now)
        session_id = SELFHOST_SESSION_ID
    try:
        agent_id, created = repo.register_local_agent(
            conn,
            connector_id=connector_id,
            local_registration_id=body.local_registration_id,
            agent_name=body.agent_name,
            repository_id=body.repository_id,
            base_commit=body.base_commit,
            verification_profile_ids=body.verification_profile_ids,
            discovered=body.discovered,
            session_id=session_id,
            now=now,
        )
    except NotFound:
        raise ApiError(
            404,
            "not_found",
            f"local_registration_id {body.local_registration_id}에 해당하는 에이전트가 없습니다.",
            field="local_registration_id",
        ) from None
    except RegistrationTaken:
        raise ApiError(
            409,
            "registration_taken",
            f"local_registration_id {body.local_registration_id}는 다른 연결 프로그램이 쓰고 있습니다.",
            field="local_registration_id",
        ) from None
    return _json(RegistrationResponse(agent_id=agent_id, created=created))


# --- 실행 이벤트·산출물 ---------------------------------------------------------


@router.post("/executions/{execution_id}/events")
def post_event(
    execution_id: str,
    body: ExecutionEvent,
    connector_id: str = Depends(require_connector),
    conn: Connection = Depends(get_conn),
) -> JSONResponse:
    _assigned_execution(conn, execution_id, connector_id)
    if body.execution_id != execution_id:
        raise ApiError(
            422, "invalid_field", "execution_id가 URL의 실행 ID와 다릅니다.", field="execution_id"
        )
    ack = repo.append_event(
        conn, execution_id, body, actor=f"connector:{connector_id}", now=utc_now()
    )
    return _json(ack)


@router.post("/executions/{execution_id}/artifacts")
def upload_artifact(
    execution_id: str,
    request: Request,
    meta: str = Form(...),
    file: UploadFile = File(...),
    connector_id: str = Depends(require_connector),
    conn: Connection = Depends(get_conn),
) -> JSONResponse:
    execution = _assigned_execution(conn, execution_id, connector_id)
    parsed = ArtifactMeta.model_validate_json(meta)  # ValidationError → 422 (errors.py)
    task = repo.get_task(conn, execution["task_id"])
    created, is_new = repo.store_artifact(
        conn,
        request.app.state.store,
        execution_id=execution_id,
        session_id=task["session_id"],
        meta=parsed,
        data=file.file.read(),
        now=utc_now(),
    )
    return _json(created, 201 if is_new else 200)


@router.get("/executions/{execution_id}/artifacts/{artifact_id}")
def download_artifact(
    execution_id: str,
    artifact_id: str,
    request: Request,
    connector_id: str = Depends(require_connector),
    conn: Connection = Depends(get_conn),
) -> Response:
    _assigned_execution(conn, execution_id, connector_id)
    store = request.app.state.store
    if not repo.download_allowed(conn, store, execution_id, artifact_id):
        raise ApiError(
            403, "forbidden", f"{artifact_id}은 {execution_id}에서 내려받을 수 없습니다.",
            field="artifact_id",
        )
    row = repo.get_artifact(conn, artifact_id)
    return Response(content=repo.read_artifact(conn, store, artifact_id), media_type=row["content_type"])
