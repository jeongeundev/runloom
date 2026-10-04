"""운영자 사람 요청 목록·응답 API — ADR-0014 결정 7, CONTRACT 13.9·13.11.

- 목록은 로그인한 멤버, 응답은 `respond` 동작(ADR-0021). GitHub 담당자를 웹 인증 사용자로 보지 않고, GitHub 댓글을
  응답·승인 명령으로 읽지 않는다. 다른 세션의 요청은 404.
- 응답은 `response_id` 로 멱등, `expected_revision` 으로 경쟁을 막는다. 요청을 `answered` 로, Task revision 을 +1 할 뿐
  실행을 만들지 않는다 — 워커가 다음 revision 을 준비 판정으로 다시 본다. 응답 내용은 다음 실행 요청에 붙고
  Task 요청 원문·원본 스냅샷은 그대로다(`task_cycle.request_text`).
- `choose_agent` 는 이 세션에 등록된 Agent 만 받는다. 담당자 연결·능력·위임 범위는 워커의 재평가가 다시 검사한다 —
  응답이 권한이나 설정을 바꾸지 않는다(위임 밖은 설정 API 로 따로 고친다).
- `close` 는 Task 를 `실패` 로 마감하고 활성 실행을 해제한다(운영자 종료). 같은 트랜잭션이라 착수와 겹쳐도 실행이 붙지 않는다.
- 실행 실패 요청(`stage_failed`, ADR-0020 결정 5)은 `retry`(다시 맡기기 — 같은 업무에 실패한 단계를 복사한 새 단계)와
  `close`(닫기 — 업무 `종료`)만 받는다. 단계는 이미 `실패` 로 마감이라 `task_closed` 검사를 하지 않는다.
- 소유자 승인 요청(`owner_approval`, ADR-0023 결정 1)은 `approve`·`decline` 만 받고, 에이전트 소유자와 관리자만 답한다
  (`delegation.can_decide_approval` — 아니면 403). 승인은 요청만 닫고 착수는 워커가, 거절은 같은 트랜잭션에서 단계
  선택·업무 담당을 비우고 트랜잭션 뒤 맡긴 사람에게 `delegation_declined` 알림.
- 수정 결과 판정 실패(`fix_verification_failed`)는 `reverify`(검증만 다시, ADR-0023)도 받는다. 그 단계의 활성 실행이
  `result_ready` 이고 결과 봉투에 `result_commit` 이 있어야 한다(아니면 409 `nothing_to_reverify`). 실행은 워커가
  같은 결과 커밋으로 만든다(`Worker._start_verify_only`).
"""

import secrets
from sqlite3 import Connection
from typing import Literal

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, ValidationError

from workflow.adapters import repo
from workflow.adapters.artifact_store import ArtifactStore
from workflow.adapters.errors import ArtifactMissing, NotFound, ResponseConflict, StaleRequest, TaskClosed
from workflow.adapters.secret_store import SecretStore
from workflow.contracts.v1 import CodeChangeResult, NonEmptyStr
from workflow.domain.delegation import OWNER_APPROVAL_CODE, can_decide_approval, parse_approval_cause_key
from workflow.domain.next_step import NEXT_STEP_HUMAN_CODE
from workflow.domain.work_status import STAGE_FAILED
from workflow.domain import team
from workflow.server import owner_approval
from workflow.server.auth import LoggedIn, get_conn, require_action, require_member_api, utc_now
from workflow.server.errors import ApiError
from workflow.server.settings import Settings

router = APIRouter(prefix="/human-requests")

Action = Literal["resume", "reverify", "choose_agent", "retry", "close", "approve", "decline"]
CLOSE_REASON = "운영자 종료 — 사람 요청 응답"
CLOSE_WORK_REASON = "닫음 — 실행 실패"  # 실행 실패 요청을 닫으면 업무 `종료` 의 이유
DECLINE_NOTE_MAX = 2000  # 거절 메모 최대 글자 수
# 요청 code → 허용 응답. 없으면 `resume`·`close`
_ACTIONS: dict[str, frozenset[str]] = {
    "assignee_multiple": frozenset({"choose_agent", "close"}),
    STAGE_FAILED: frozenset({"retry", "close"}),
    OWNER_APPROVAL_CODE: frozenset({"approve", "decline"}),
    "fix_verification_failed": frozenset({"resume", "reverify", "close"}),
}
# 추가 정보를 묻는 요청 — `resume` 에 빈 답은 받지 않는다
_INFORMATION_CODES = frozenset({"input_missing", "fix_needs_information", "review_needs_information",
                                NEXT_STEP_HUMAN_CODE})


class ResponseBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    response_id: NonEmptyStr
    expected_revision: int
    action: Action
    text: str = ""
    agent_id: NonEmptyStr | None = None


def _request_view(row) -> dict:
    return {k: row[k] for k in ("request_id", "task_id", "code", "question", "revision", "state", "created_at")}


def allowed_actions(code: str) -> frozenset[str]:
    """요청 code 에 허용되는 응답 — 검사와 운영자 화면의 응답 폼이 같이 쓴다."""
    return _ACTIONS.get(code, frozenset({"resume", "close"}))


def asks_information(code: str) -> bool:
    """`resume` 응답에 글(요청한 정보)이 있어야 하는 요청인가."""
    return code in _INFORMATION_CODES


def reverify_commit(conn: Connection, store: ArtifactStore | None, task_id: str) -> str | None:
    """검증만 다시 할 결과 커밋 — 단계의 활성 실행이 `result_ready` 이고 결과 봉투(`CodeChangeResult`)에 있는 값. 없으면 None."""
    active = repo.active_execution(conn, task_id)
    if store is None or active is None or active["status"] != "result_ready" or not active["result_artifact_id"]:
        return None
    try:
        return CodeChangeResult.model_validate_json(
            repo.read_artifact(conn, store, active["result_artifact_id"])
        ).result_commit
    except (ValidationError, ValueError, NotFound, ArtifactMissing):
        return None


def _check(conn: Connection, session_id: str, request, body: ResponseBody, member_id: str | None,
           store: ArtifactStore | None) -> None:
    allowed = allowed_actions(request["code"])
    if body.action not in allowed:
        raise ApiError(422, "invalid_field", f"요청 {request['code']} 에는 {body.action} 로 응답할 수 없습니다.",
                       field="action")
    if body.action in ("approve", "decline"):
        scope = parse_approval_cause_key(request["cause_key"])
        member = repo.get_member(conn, session_id, member_id) if member_id is not None else None
        owner_id = repo.agent_owner_id(conn, scope[0]) if scope is not None else None
        if member is None or not can_decide_approval(member_id=member_id, role=member["role"], owner_id=owner_id):
            raise ApiError(403, "forbidden", "에이전트 소유자나 관리자만 승인·거절할 수 있습니다.")
        if body.action == "decline" and len(body.text) > DECLINE_NOTE_MAX:
            raise ApiError(422, "invalid_field", f"거절 메모는 {DECLINE_NOTE_MAX}자까지입니다.", field="text")
    # 이미 답한 요청은 재전송·경쟁 판정(`record_human_response_once`)에 맡긴다 — 착수 뒤 재전송도 처음 결과
    if body.action == "reverify" and request["state"] == "open" and reverify_commit(conn, store, request["task_id"]) is None:
        raise ApiError(409, "nothing_to_reverify", "다시 검증할 결과 커밋이 없습니다.")
    if body.action == "resume" and asks_information(request["code"]) and not body.text.strip():
        raise ApiError(422, "invalid_field", "요청한 정보를 text 에 적어야 합니다.", field="text")
    if body.action == "choose_agent":
        if body.agent_id is None:
            raise ApiError(422, "invalid_field", "지정할 agent_id 가 필요합니다.", field="agent_id")
        if repo.get_agent(conn, body.agent_id) is None or not repo.is_session_agent(conn, session_id, body.agent_id):
            raise ApiError(422, "agent_not_registered",
                           f"에이전트 {body.agent_id} 는 이 워크스페이스에 등록되지 않았습니다.", field="agent_id")


def respond_to_request(
    conn: Connection, session_id: str, request_id: str, body: ResponseBody, now: str, *, member_id: str | None = None,
    settings: Settings | None = None, secret_store: SecretStore | None = None, store: ArtifactStore | None = None,
) -> dict:
    """응답 기록 — 재전송은 처음 결과. `member_id` 는 응답자(다시 맡기기면 맡긴 사람도). 검사는 처음 응답에만 의미가 있지만 같은 본문이면 같은 결과라 먼저 한다.
    `settings`·`secret_store` 는 거절 알림용 — 없으면 알림을 쌓지 않는다. `store` 는 `reverify` 의 결과 커밋 확인용 —
    없으면 결과 커밋을 읽지 못하므로 `reverify` 는 409."""
    request = repo.get_human_request(conn, session_id, request_id)
    if request is None:
        raise ApiError(404, "not_found", f"사람 요청 {request_id}을 찾을 수 없습니다.", field="request_id")
    _check(conn, session_id, request, body, member_id, store)
    failed_stage = request["code"] == STAGE_FAILED
    try:
        task_revision, created = repo.record_human_response_once(
            conn, session_id, request_id, response_id=body.response_id, expected_revision=body.expected_revision,
            action=body.action, text=body.text, now=now,
            agent_id=body.agent_id if body.action == "choose_agent" else None,
            close_reason=CLOSE_REASON if body.action == "close" and not failed_stage else None,
            retry_task_id=f"task-{secrets.token_hex(6)}" if body.action == "retry" else None,
            close_work_reason=CLOSE_WORK_REASON if body.action == "close" and failed_stage else None,
            member_id=member_id, clear_delegation=body.action == "decline",
        )
    except StaleRequest as exc:
        raise ApiError(409, "stale_request", f"사람 요청 {request_id} 가 이미 revision {exc.current_revision} 입니다.",
                       field="expected_revision", details={"current_revision": exc.current_revision}) from None
    except ResponseConflict:
        raise ApiError(409, "response_conflict", f"응답 {body.response_id} 는 다른 내용으로 이미 저장되었습니다.",
                       field="response_id") from None
    except TaskClosed:
        raise ApiError(409, "task_closed", f"업무 {request['task_id']} 는 이미 마감되었습니다.") from None
    if created and body.action == "decline" and settings is not None:
        owner_approval.notify_declined(conn, request, note=body.text, member_id=member_id, now=now, settings=settings,
                                       secrets=secret_store)
    return {"request_id": request_id, "task_id": request["task_id"], "response_id": body.response_id,
            "task_revision": task_revision, "created": created}


@router.get("")
def list_requests(member: LoggedIn = Depends(require_member_api), conn: Connection = Depends(get_conn)) -> JSONResponse:
    session_id = member.session_id
    return JSONResponse({"requests": [_request_view(r) for r in repo.list_open_human_requests(conn, session_id)]})


@router.post("/{request_id}/responses")
def post_response(
    request: Request, request_id: str, body: ResponseBody,
    member: LoggedIn = Depends(require_action(team.RESPOND, api=True)),
    conn: Connection = Depends(get_conn),
) -> JSONResponse:
    session_id = member.session_id
    return JSONResponse(respond_to_request(conn, session_id, request_id, body, utc_now(), member_id=member.member_id,
                                           settings=request.app.state.settings,
                                           secret_store=request.app.state.secrets, store=request.app.state.store))
