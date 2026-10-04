"""같은 워크스페이스의 업무 요청 접수 API."""
from sqlite3 import Connection
from fastapi import APIRouter, Depends, Request
from workflow.adapters import repo, internal_request_store as store
from workflow.contracts.internal_request import InternalRequestCreate, InternalRequestAccept, InternalInvestigationStart, InternalResultReturn, InternalRequestReject, InternalRequestReroute, InternalInformationQuestion, InternalInformationAnswer, InternalJudgmentRequest, InternalJudgmentResponse
from workflow.domain import team
from workflow.server.auth import LoggedIn, get_conn, require_action, require_member_api, utc_now
from workflow.server.errors import ApiError
from workflow.server import internal_investigation
from workflow.server.worker import enqueue_request_notification

router = APIRouter()


def _notify(conn: Connection, request: Request, request_id: str) -> None:
    """사내 요청이 생긴 뒤(생성·재전달) 받는 사람에게 알림 — 같은 요청이면 `dedupe_key` 로 한 번."""
    enqueue_request_notification(conn, request.app.state.settings, request.app.state.secrets,
                                 request_id=request_id, now=utc_now())


@router.get('/internal-requests')
def inbox(member: LoggedIn = Depends(require_member_api), conn: Connection = Depends(get_conn)) -> dict:
    return {'requests': store.list_for_member(conn, member.session_id, member.member_id)}


@router.get('/work-items/{work_item_id}/internal-requests')
def for_work(work_item_id: str, member: LoggedIn = Depends(require_member_api),
             conn: Connection = Depends(get_conn)) -> dict:
    if repo.get_work_item(conn, member.session_id, work_item_id) is None:
        raise ApiError(404, 'not_found', '원래 업무를 찾을 수 없습니다.')
    return {'requests': store.list_for_work(conn, member.session_id, work_item_id)}


@router.post('/work-items/{work_item_id}/internal-requests')
def create(work_item_id: str, request: Request, body: InternalRequestCreate,
           member: LoggedIn = Depends(require_action(team.DELEGATE, api=True)),
           conn: Connection = Depends(get_conn)) -> dict:
    try:
        result = store.create(conn, member.session_id, member.member_id, work_item_id, body, now=utc_now())
    except store.RequestProblem as exc:
        raise ApiError(exc.status, exc.code, str(exc)) from None
    _notify(conn, request, result['request_id'])
    return result


@router.post('/internal-requests/{request_id}/accept')
def accept(request_id: str, body: InternalRequestAccept,
           member: LoggedIn = Depends(require_action(team.RESPOND, api=True)),
           conn: Connection = Depends(get_conn)) -> dict:
    try:
        return store.accept(conn, member.session_id, member.member_id, request_id, body.expected_revision, now=utc_now())
    except store.RequestProblem as exc:
        raise ApiError(exc.status, exc.code, str(exc)) from None


@router.post('/internal-requests/{request_id}/investigation')
def investigate(request_id: str, request: Request, body: InternalInvestigationStart,
                member: LoggedIn = Depends(require_action(team.DELEGATE, api=True)),
                conn: Connection = Depends(get_conn)) -> dict:
    try:
        return internal_investigation.start(conn, request.app, member, request_id, body, now=utc_now())
    except store.RequestProblem as exc:
        raise ApiError(exc.status, exc.code, str(exc)) from None


@router.post('/internal-requests/{request_id}/return')
def return_result(request_id: str, request: Request, body: InternalResultReturn,
                  member: LoggedIn = Depends(require_action(team.RESPOND, api=True)),
                  conn: Connection = Depends(get_conn)) -> dict:
    try:
        return internal_investigation.return_result(conn, request.app.state.store, member, request_id, body, now=utc_now())
    except store.RequestProblem as exc:
        raise ApiError(exc.status, exc.code, str(exc)) from None


@router.post('/internal-requests/{request_id}/not-responsible')
def reject(request_id: str, body: InternalRequestReject,
           member: LoggedIn = Depends(require_action(team.RESPOND, api=True)),
           conn: Connection = Depends(get_conn)) -> dict:
    try:
        return store.reject(conn, member.session_id, member.member_id, request_id, body, now=utc_now())
    except store.RequestProblem as exc:
        raise ApiError(exc.status, exc.code, str(exc)) from None


@router.post('/internal-requests/{request_id}/reroute')
def reroute(request_id: str, request: Request, body: InternalRequestReroute,
            member: LoggedIn = Depends(require_action(team.DELEGATE, api=True)),
            conn: Connection = Depends(get_conn)) -> dict:
    try:
        result = store.reroute(conn, member.session_id, member.member_id, request_id, body, now=utc_now())
    except store.RequestProblem as exc:
        raise ApiError(exc.status, exc.code, str(exc)) from None
    _notify(conn, request, result['request_id'])
    return result


@router.post('/internal-requests/{request_id}/questions')
def ask(request_id: str, body: InternalInformationQuestion,
        member: LoggedIn = Depends(require_action(team.RESPOND, api=True)),
        conn: Connection = Depends(get_conn)) -> dict:
    try:
        return store.ask(conn, member.session_id, member.member_id, request_id, body, now=utc_now())
    except store.RequestProblem as exc:
        raise ApiError(exc.status, exc.code, str(exc)) from None


@router.post('/internal-requests/{request_id}/questions/{question_id}/answer')
def answer(request_id: str, question_id: str, body: InternalInformationAnswer,
           member: LoggedIn = Depends(require_action(team.RESPOND, api=True)),
           conn: Connection = Depends(get_conn)) -> dict:
    try:
        return store.answer(conn, member.session_id, member.member_id, request_id, question_id, body, now=utc_now())
    except store.RequestProblem as exc:
        raise ApiError(exc.status, exc.code, str(exc)) from None


@router.post('/internal-requests/{request_id}/judgments')
def request_judgment(request_id: str, request: Request, body: InternalJudgmentRequest,
                     member: LoggedIn = Depends(require_action(team.RESPOND, api=True)),
                     conn: Connection = Depends(get_conn)) -> dict:
    try:
        return internal_investigation.request_judgment(conn, request.app.state.store, member, request_id, body, now=utc_now())
    except store.RequestProblem as exc:
        raise ApiError(exc.status, exc.code, str(exc)) from None


@router.post('/internal-requests/{request_id}/judgments/{judgment_id}/respond')
def respond_judgment(request_id: str, judgment_id: str, request: Request, body: InternalJudgmentResponse,
                     member: LoggedIn = Depends(require_action(team.RESPOND, api=True)),
                     conn: Connection = Depends(get_conn)) -> dict:
    try:
        return internal_investigation.respond_judgment(conn, request.app.state.store, member, request_id, judgment_id, body, now=utc_now())
    except store.RequestProblem as exc:
        raise ApiError(exc.status, exc.code, str(exc)) from None


@router.post('/internal-requests/{request_id}/resume')
def resume_work(request_id: str, body: InternalInformationAnswer,
                member: LoggedIn = Depends(require_action(team.RESPOND, api=True)),
                conn: Connection = Depends(get_conn)) -> dict:
    try:
        return store.resume(conn, member.session_id, member.member_id, request_id, body, now=utc_now())
    except store.RequestProblem as exc:
        raise ApiError(exc.status, exc.code, str(exc)) from None
