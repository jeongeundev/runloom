"""멤버 토큰 API — phase 24 step 3 (ADR-0032, ARCHITECTURE "토큰 API·화면", CONTRACT 18.4·18.5).

- 발급은 로그인 쿠키로만(`require_member_cookie_api` — Bearer 헤더면 403 cookie_required). 원문은 201 응답에만 한 번,
  서버에는 sha256 만. 목록·폐기는 쿠키·`wfm_` 모두, 읽기 전용 토큰의 폐기는 `require_member_api` 가 403 token_read_only.
- `GET /me`(phase 26, `me_router`)는 로그인한 멤버 자신의 id·표시 이름·역할·토큰 권한만. 읽기 전용 토큰도 된다.
- 자기 토큰만 본다·폐기한다. 관리자의 남의 토큰 화면은 없다(README D14). `/me` 화면 폼(`web.py`)과 같은 repo 함수를 쓴다.
"""

from sqlite3 import Connection

from fastapi import APIRouter, Depends, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from workflow.adapters import repo
from workflow.domain import team
from workflow.server.auth import (LoggedIn, get_conn, require_action, require_member_api, require_member_cookie_api,
                                  utc_now)
from workflow.server.errors import ApiError

router = APIRouter(prefix="/me/tokens")
me_router = APIRouter(prefix="/me")  # 자기 정보 — phase 26 step 1 (ADR-0034 D28, CONTRACT 20.1)

# `issue_member_token` 의 ValueError(칸 이름) → 화면·API 공용 문구
INVALID_MESSAGES = {
    "name": "토큰 이름은 1~40자로 입력하세요.",
    "scope": "권한은 read(읽기 전용) 또는 full(내 권한 전부)입니다.",
}


class MemberTokenCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    scope: str


def issue(conn: Connection, member: LoggedIn, name: str, scope: str, error: type[ApiError] = ApiError) -> dict:
    """발급 — `token_id`·`name`(공백 지운 값)·`scope`·`token`(원문)·`created_at`. 값 오류는 `error` 422 invalid_field."""
    now = utc_now()
    try:
        token_id, token = repo.issue_member_token(conn, session_id=member.session_id, member_id=member.member_id,
                                                  name=name, scope=scope, now=now)
    except ValueError as exc:
        field = str(exc)
        raise error(422, "invalid_field", INVALID_MESSAGES[field], field=field) from None
    return {"token_id": token_id, "name": name.strip(), "scope": scope, "token": token, "created_at": now}


def revoke(conn: Connection, member: LoggedIn, token_id: str, error: type[ApiError] = ApiError) -> None:
    """내 활성 토큰 폐기. 남의 토큰·없는 토큰·이미 폐기한 토큰은 `error` 404 not_found."""
    if not repo.revoke_member_token(conn, member_id=member.member_id, token_id=token_id, now=utc_now()):
        raise error(404, "not_found", f"토큰 {token_id}을 찾을 수 없습니다.", field="token_id")


def _can_edit_own_settings(member: LoggedIn) -> LoggedIn:
    if not team.can(member.role, team.EDIT_OWN_SETTINGS):
        raise ApiError(403, "forbidden", "관리자 권한이 필요합니다.")
    return member


@router.post("", status_code=201)
def create_token(
    body: MemberTokenCreate,
    member: LoggedIn = Depends(require_member_cookie_api),
    conn: Connection = Depends(get_conn),
) -> JSONResponse:
    _can_edit_own_settings(member)
    return JSONResponse(issue(conn, member, body.name, body.scope), status_code=201)


@router.get("")
def list_tokens(
    member: LoggedIn = Depends(require_action(team.EDIT_OWN_SETTINGS, api=True)),
    conn: Connection = Depends(get_conn),
) -> JSONResponse:
    return JSONResponse({"tokens": [dict(r) for r in repo.list_member_tokens(conn, member.member_id)]})


@router.delete("/{token_id}", status_code=204)
def delete_token(
    token_id: str,
    member: LoggedIn = Depends(require_action(team.EDIT_OWN_SETTINGS, api=True)),
    conn: Connection = Depends(get_conn),
) -> Response:
    revoke(conn, member, token_id)
    return Response(status_code=204)


@me_router.get("")
def read_me(member: LoggedIn = Depends(require_member_api)) -> JSONResponse:
    return JSONResponse({"member_id": member.member_id, "display_name": member.display_name, "role": member.role,
                         "token_scope": member.token_scope})
