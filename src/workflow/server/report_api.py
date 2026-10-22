"""사람용 리포트 API — phase 25 step 8 (ADR-0033, ARCHITECTURE "인계 리포트 — phase 25" 의 "사람용 API", D29).

- 업무의 리포트 목록·리포트 보기 묶음·결정 답·맞춤 풀이 요청·다시 만들기. 보기 묶음과 행동 판정은
  `handoff_reports` 의 함수가 하고 라우트는 호출만 한다 — 웹 폼(step 9)이 같은 함수를 부른다(ADR-0031 결정 3).
- 인증은 쿠키·멤버 토큰(`require_member_api` — 읽기 전용 토큰은 GET 만). 그 위 권한은 역할이 아니라 도메인 판정
  (받는 사람·보낸 사람·관리자)이다. 리포트 HTML 은 step 9.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from sqlite3 import Connection
from typing import Any, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict

from workflow.contracts.v1 import NonEmptyStr
from workflow.server import github_clients, handoff_reports
from workflow.server.auth import LoggedIn, get_conn, require_member_api, utc_now
from workflow.server.errors import ApiError
from workflow.server.stage_runs import WorkActionError
from workflow.server.work_lookup import work_by_key

router = APIRouter()


class DecisionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answer: Literal["confirmed", "changed"]
    note: str | None = None


class TailorBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_id: NonEmptyStr


@contextmanager
def _api_errors() -> Iterator[None]:
    """행동 함수의 오류를 API 오류로 — `work_api._api_errors` 와 같은 옮김."""
    try:
        yield
    except WorkActionError as exc:
        raise ApiError(exc.status, exc.code, exc.message, field=exc.field) from None


def _view(request: Request, conn: Connection, member: LoggedIn, report_id: str) -> dict[str, Any]:
    with _api_errors():
        row = handoff_reports.own_report(conn, member.session_id, report_id)
    return handoff_reports.report_view(conn, request.app.state.store, report=row, member_id=member.member_id,
                                       role=member.role)


@router.get("/work-items/{key}/reports")
def list_reports(
    key: str, request: Request, member: LoggedIn = Depends(require_member_api), conn: Connection = Depends(get_conn),
) -> dict[str, Any]:
    work = work_by_key(conn, member.session_id, key)
    if work is None:
        raise ApiError(404, "not_found", f"업무 {key}을 찾을 수 없습니다.", field="key")
    return handoff_reports.stage_reports(conn, request.app.state.store, work_item_id=work["work_item_id"],
                                         member_id=member.member_id, role=member.role)


@router.get("/reports/{report_id}")
def get_report(
    report_id: str, request: Request, member: LoggedIn = Depends(require_member_api),
    conn: Connection = Depends(get_conn),
) -> dict[str, Any]:
    return _view(request, conn, member, report_id)


@router.put("/reports/{report_id}/decisions/{item_id}")
def put_decision(
    report_id: str, item_id: str, body: DecisionBody, request: Request,
    member: LoggedIn = Depends(require_member_api), conn: Connection = Depends(get_conn),
) -> dict[str, Any]:
    with _api_errors():
        handoff_reports.answer_decision(conn, request.app.state.store, session_id=member.session_id, member=member,
                                        report_id=report_id, item_id=item_id, answer=body.answer, note=body.note,
                                        now=utc_now())
    return _view(request, conn, member, report_id)


@router.post("/reports/{report_id}/tailor", status_code=202)
def post_tailor(
    report_id: str, body: TailorBody, request: Request, member: LoggedIn = Depends(require_member_api),
    conn: Connection = Depends(get_conn),
) -> dict[str, Any]:
    with _api_errors():
        row, _created = handoff_reports.request_tailored(conn, request.app.state.store, session_id=member.session_id,
                                                         member=member, report_id=report_id, agent_id=body.agent_id,
                                                         now=utc_now())
    return _view(request, conn, member, row["report_id"])


@router.post("/reports/{report_id}/regenerate", status_code=202)
def post_regenerate(
    report_id: str, request: Request, member: LoggedIn = Depends(require_member_api),
    conn: Connection = Depends(get_conn),
) -> dict[str, Any]:
    state = request.app.state

    def github_for(config):  # 수집과 같은 자격 — 테스트는 `github_client` 로 바꾼다
        return state.github_client or github_clients.client_for(config, state.settings, state.secrets,
                                                                transport=state.github_transport)

    with _api_errors():
        row = handoff_reports.regenerate(conn, state.store, state.settings, github_for, session_id=member.session_id,
                                         member=member, report_id=report_id, now=utc_now())
    return _view(request, conn, member, row["report_id"])
