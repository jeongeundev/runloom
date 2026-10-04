"""담당표 조회·등록. 업무 배정·실행·승인을 발생시키지 않는다."""
from sqlite3 import Connection
from fastapi import APIRouter, Depends
from workflow.adapters import repo, responsibility_store as store
from workflow.contracts.responsibility import ResponsibilitiesRequest, ResponsibilitySelection
from workflow.domain import team
from workflow.server.auth import LoggedIn, get_conn, require_action, require_member_api, utc_now
from workflow.server.errors import ApiError

router = APIRouter(prefix='/responsibilities')


def _view(conn: Connection, session_id: str) -> dict:
    return {'config_revision': repo.get_config_revision(conn, session_id),
            'entries': [e.model_dump() for e in store.list_entries(conn, session_id)]}


@router.get('')
def directory(member: LoggedIn = Depends(require_member_api), conn: Connection = Depends(get_conn)) -> dict:
    return _view(conn, member.session_id)


@router.put('')
def replace(body: ResponsibilitiesRequest, member: LoggedIn = Depends(require_action(team.MANAGE_RULES, api=True)),
            conn: Connection = Depends(get_conn)) -> dict:
    try:
        store.replace_entries(conn, member.session_id, body.entries, expected_revision=body.expected_revision,
                              member_id=member.member_id, now=utc_now())
    except store.StaleDirectory:
        raise ApiError(409, 'stale_directory', '담당 범위 표가 변경되었습니다. 다시 조회하세요.') from None
    except ValueError as exc:
        raise ApiError(422, 'invalid_field', str(exc), field='entries') from None
    return _view(conn, member.session_id)


@router.get('/candidates')
def candidates(system_id: str, request_kind: str, member: LoggedIn = Depends(require_member_api),
               conn: Connection = Depends(get_conn)) -> dict:
    entries = [e for e in store.list_entries(conn, member.session_id)
               if e.system_id == system_id and e.request_kind == request_kind]
    result = []
    for entry in entries:
        problem = store.entry_problem(conn, member.session_id, entry)
        result.append({**entry.model_dump(), 'selectable': problem is None, 'reason': problem})
    return {'config_revision': repo.get_config_revision(conn, member.session_id), 'selection_required': True,
            'candidates': result}


@router.post('/select')
def select(body: ResponsibilitySelection, member: LoggedIn = Depends(require_action(team.DELEGATE, api=True)),
           conn: Connection = Depends(get_conn)) -> dict:
    # 확인 전용이다. 실제 요청 생성 시 같은 revision과 활성 여부를 다시 검사해야 한다.
    conn.execute('BEGIN')
    try:
        revision = repo.get_config_revision(conn, member.session_id)
        if revision != body.expected_revision:
            raise ApiError(409, 'stale_directory', '담당 범위 표가 변경되었습니다. 다시 조회하세요.')
        entry = next((e for e in store.list_entries(conn, member.session_id)
                      if (e.system_id, e.request_kind, e.recipient_member_id)
                      == (body.system_id, body.request_kind, body.recipient_member_id)), None)
        if entry is None:
            raise ApiError(422, 'invalid_field', '담당 후보를 명시적으로 선택하세요.', field='recipient_member_id')
        problem = store.entry_problem(conn, member.session_id, entry)
        if problem:
            raise ApiError(422, 'invalid_field', problem, field='recipient_member_id')
        return {'config_revision': revision, 'entry': entry.model_dump(), 'execution_started': False}
    finally:
        conn.execute('ROLLBACK')
