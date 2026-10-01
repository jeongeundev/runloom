"""매핑 표 API — 워크스페이스 `field_mappings` 조회·교체 (ADR-0020, ARCHITECTURE "매핑 표"). 화면은 16-work-ui.

- 조회는 로그인한 멤버, 교체는 `manage_rules`(ADR-0021). 그 워크스페이스 행만 보인다.
- PUT 은 행 전부를 본문 순서(= `position`)로 바꾸고 설정 번호를 올린다. 이미 만든 업무는 바꾸지 않는다.
- 값은 문자열로만 저장한다 — 명령·경로로 해석하지 않는다.
"""

from sqlite3 import Connection
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict

from workflow.adapters import repo
from workflow.contracts.v1 import NonEmptyStr
from workflow.domain.field_mapping import MappingRow
from workflow.domain import team
from workflow.server.auth import LoggedIn, get_conn, require_action, require_member_api, utc_now
from workflow.server.errors import ApiError

router = APIRouter(prefix="/field-mappings")


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class FieldMapping(_Body):
    source_type: Literal["github", "n8n", "jira"]
    field: Literal["kind", "priority"]
    source_value: NonEmptyStr  # `*` = 나머지 전부
    runloom_value: NonEmptyStr


class FieldMappingsRequest(_Body):
    mappings: list[FieldMapping]


def _view(conn: Connection, session_id: str) -> dict:
    return {
        "config_revision": repo.get_config_revision(conn, session_id),
        "mappings": [
            FieldMapping(source_type=r.source_type, field=r.field, source_value=r.source_value,
                         runloom_value=r.runloom_value).model_dump()
            for r in repo.list_field_mappings(conn, session_id)
        ],
    }


@router.get("")
def list_mappings(member: LoggedIn = Depends(require_member_api), conn: Connection = Depends(get_conn)) -> dict:
    session_id = member.session_id
    return _view(conn, session_id)


@router.put("")
def replace_mappings(
    body: FieldMappingsRequest,
    member: LoggedIn = Depends(require_action(team.MANAGE_RULES, api=True)),
    conn: Connection = Depends(get_conn),
) -> dict:
    session_id = member.session_id
    rows = [MappingRow(m.source_type, m.field, m.source_value, m.runloom_value, position)
            for position, m in enumerate(body.mappings, start=1)]
    try:
        repo.replace_field_mappings(conn, session_id, rows, now=utc_now(), member_id=member.member_id)
    except ValueError as exc:
        raise ApiError(422, "invalid_field", str(exc), field="mappings") from None
    return _view(conn, session_id)
