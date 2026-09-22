"""n8n 입구 API — 워크스페이스(세션)가 발급한 입구 토큰(`wfs_`)으로 체인을 접수한다. CONTRACT 12절, ADR-0010.

항목은 `Issue` 와 같은 모양이라 매핑·구성은 가져오기와 같은 경로 하나(`web.create_chain`)다 — 여기서 Task 를 직접 만들지
않는다. 접수 즉시 첫 업무 시작을 시도하고(n8n 트리거가 곧 사람의 "워크플로우 시작"), 시작만 거부되면 체인은 남기고
`started=false` + `start_error` 로 알린다. 요청 본문의 `body`·`title`·`labels` 는 문자열로만 저장하고 경로·명령으로
해석하지 않는다. 발신자 신원은 토큰으로만 정한다 — 세션 쿠키로는 통과하지 않는다(브라우저 CSRF 경로를 만들지 않는다).
"""

from sqlite3 import Connection, Row
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from workflow.adapters import repo
from workflow.contracts.v1 import (
    ErrorBody,
    InboundChainRequest,
    InboundChainResponse,
    InboundSkipped,
    InboundTaskRef,
)
from workflow.domain.callback_policy import host_allowed
from workflow.domain.task_sources import Issue
from workflow.server.auth import get_conn, require_source_token, utc_now
from workflow.server.errors import ApiError
from workflow.server.settings import Settings
from workflow.server.web import create_chain, start_chain

router = APIRouter()


def _json(model: BaseModel, status: int = 200) -> JSONResponse:
    return JSONResponse(status_code=status, content=model.model_dump(mode="json"))


def _host_of(url: str) -> str:
    try:
        return urlsplit(url).hostname or "(없음)"
    except ValueError:
        return "(없음)"


def _check_callback_url(url: str, settings: Settings) -> None:
    """허용 목록 밖이면 422. 목록 값은 `details.allowed` 에만 두고 메시지에는 비었는지 여부만 적는다."""
    if host_allowed(url, settings.callback_hosts):
        return
    message = f"callback_url 의 호스트 {_host_of(url)} 은 허용 목록에 없습니다."
    if not settings.callback_hosts:
        message += " 허용 목록(WORKFLOW_CALLBACK_HOSTS)이 비어 있어 callback 을 받지 않습니다."
    raise ApiError(
        422, "callback_host_not_allowed", message,
        field="callback_url", details={"allowed": list(settings.callback_hosts)},
    )


@router.post("/sources/{source}/chains", status_code=201)
def inbound_chain(
    request: Request,
    source: str,
    body: InboundChainRequest,
    token: Row = Depends(require_source_token),
    conn: Connection = Depends(get_conn),
) -> JSONResponse:
    if source != "n8n":
        raise ApiError(404, "not_found", f"입구 {source}을 찾을 수 없습니다.", field="source")
    if token["source"] != source:
        raise ApiError(403, "forbidden", "이 토큰은 n8n 입구에 쓸 수 없습니다.")
    settings: Settings = request.app.state.settings
    now = utc_now()
    session_id = token["session_id"]
    if body.callback_url is not None:
        _check_callback_url(body.callback_url, settings)

    issues = [
        Issue(
            source="n8n", key=item.key, title=item.title, body=item.body,
            labels=tuple(item.labels), blocked_by=tuple(item.blocked_by), url=None,
        )
        for item in body.items
    ]
    created = create_chain(
        conn, session_id, issues, source="n8n", now=now, settings=settings,
        callback_url=body.callback_url, items=[item.model_dump() for item in body.items], items_field="items",
    )
    # 체인·Task 는 이미 커밋됐다. 시작만 거부되면(후보 없음·상한·조건 미충족) 오류 본문을 start_error 에 담아 201
    start_error: ErrorBody | None = None
    try:
        start_chain(conn, repo.get_chain(conn, created.chain_id), session_id=session_id, now=now, settings=settings)
    except ApiError as exc:
        start_error = ErrorBody(code=exc.code, message=exc.message, field=exc.field, details=exc.details)

    tasks = repo.tasks_of_chain(conn, created.chain_id)
    response = InboundChainResponse(
        contract_version=1,
        chain_id=created.chain_id,
        chain_url=f"{settings.public_url}/chains/{created.chain_id}" if settings.public_url else None,
        started=start_error is None,
        start_error=start_error,
        tasks=[
            InboundTaskRef(task_id=t["task_id"], key=t["source_ref"], kind=t["kind"], status=t["status"])
            for t in tasks
        ],
        skipped=[InboundSkipped(key=s["key"], reason=s["reason"]) for s in created.skipped],
    )
    return _json(response, 201)
