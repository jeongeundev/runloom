"""업무 목록·상세·행동 API — phase 24 step 6·7 (ADR-0032, ARCHITECTURE "업무 목록 응답"·"업무 상세 응답"·"업무 행동",
CONTRACT 18.1~18.3).

- 목록은 웹 업무 화면과 같은 `views.work_list_context`, 상세는 웹 패널과 같은 `views.work_panel_context` 로 만들고 칸만
  JSON 으로 옮긴다. 필터·건수·담당 후보·가능 행동을 여기서 따로 판정하지 않는다(README D4·D28).
- 업무는 업무 키(`RUN-12`)로 가리킨다. 쿼리 값은 웹과 같은 집합이지만 모르는 값은 기본값 대신 422(D27).
- 인증은 쿠키·멤버 토큰 모두(`require_member_api`).
- 행동은 웹 폼 라우트(`web.work_assignee` 등)와 같은 `team.DELEGATE` 권한·같은 `work_actions`·`triage_runs` 함수·같은 인자.
  본문은 JSON 만(`extra="forbid"`), 성공 응답은 바뀐 업무 상세(D29). 규칙을 여기서 따로 두지 않는다(D4·D30).
"""

from collections.abc import Iterator
from contextlib import contextmanager
from sqlite3 import Connection, Row
from typing import Any, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict

from workflow.adapters import internal_request_store, repo
from workflow.contracts.v1 import NonEmptyStr
from workflow.domain import team
from workflow.domain.status import UserStatus
from workflow.domain.work_list import CLOSED_SCOPES, GROUP_BYS, QUICK_FILTERS, VIEWS, ListQuery, WorkRow
from workflow.server import triage_runs, views, work_actions
from workflow.server.auth import LoggedIn, get_conn, require_action, require_member_api, utc_now
from workflow.server.errors import ApiError
from workflow.server.work_lookup import work_by_key

router = APIRouter(prefix="/work-items")


def _own_work(conn: Connection, session_id: str, key: str) -> Row:
    work = work_by_key(conn, session_id, key)
    if work is None:
        raise ApiError(404, "not_found", f"업무 {key}을 찾을 수 없습니다.", field="key")
    return work


def _status(status: UserStatus) -> dict[str, str]:
    return {"label": status.label, "reason": status.reason}


def _pick(values: dict[str, Any], *names: str) -> dict[str, Any]:
    """패널 dict 에서 칸 고르기 — 없는 칸은 None."""
    return {name: values.get(name) for name in names}


def _item(row: WorkRow, *, member_id: str) -> dict[str, Any]:
    """목록 항목 — `WorkRow` 의 칸(내부 id·화면 축약 제외)."""
    assignee = None
    if row.assignee_type is not None:
        assignee = {"type": row.assignee_type, "id": row.assignee_id, "name": row.assignee_name or row.assignee_id,
                    "active": row.assignee_active}
    return {
        "key": row.work_key, "title": row.title, "kind": row.kind, "kind_label": row.kind_label,
        "priority": row.priority, "assignee": assignee, "status": row.status, "status_reason": row.status_reason,
        "my_turn": row.status == "내 차례" and member_id in row.recipients,
        "next_action": row.next_action, "triage": row.triage, "next_step": row.next_step,
        "repository": row.repository,
        "source": {"type": row.source_type, "key": row.source_key, "url": row.source_url},
        "updated_at": row.updated_at, "closed_at": row.closed_at,
    }


def _triage(panel: dict[str, Any] | None) -> dict[str, Any] | None:
    if panel is None:
        return None
    proposal = panel.get("proposal")
    button = panel.get("button")
    return {
        **_pick(panel, "state", "triage_id", "criteria_version", "agent_name", "failed_label", "failed_message"),
        "proposal": _pick(proposal, "proceed", "confidence", "kind_line", "assignee", "predecessors", "reasons",
                          "missing", "acceptable") if proposal is not None else None,
        **_pick(panel, "handled_by", "handled_at", "handled_text"),
        "unavailable_reason": button["reason"] if button is not None else panel.get("note"),
    }


def _next_step(panel: dict[str, Any] | None) -> dict[str, Any] | None:
    if panel is None:
        return None
    proposal = panel.get("proposal")
    return {
        **_pick(panel, "state", "triage_id", "criteria_version", "agent_name", "cause_line", "failed_label",
                "failed_message"),
        "proposal": _pick(proposal, "action_label", "proceed", "confidence", "line", "purpose", "question", "reasons",
                          "missing") if proposal is not None else None,
        **_pick(panel, "handled_by", "handled_at"),
    }


def _detail(conn: Connection, request: Request, member: LoggedIn, work: Row) -> dict[str, Any]:
    """업무 상세 — 웹 패널과 같은 재료에서 화면 전용 값(폼 숨은 입력·제출 키·선택 라벨)을 뺀 것. step 7 행동 응답도 쓴다."""
    session_id, work_item_id = member.session_id, work["work_item_id"]
    allowed = team.allowed_actions(member.role)
    panel = views.work_panel_context(conn, session_id, work_item_id, member_id=member.member_id, allowed=allowed,
                                     now=utc_now(), settings=request.app.state.settings)
    row = next(r for r in repo.list_work_rows(conn, session_id, closed_since=None) if r.work_item_id == work_item_id)
    internal = [views.internal_request_view(conn, session_id, member.member_id, allowed, r, request.app.state.store)
                for r in internal_request_store.list_for_work(conn, session_id, work_item_id)]
    return {
        "work": {
            **_item(row, member_id=member.member_id),
            "request": panel["work"]["request"], "created_at": panel["work"]["created_at"],
            "turn_recipients": panel["work"]["recipients"],
            "form_fields": [_pick(f, "key", "label", "value", "source") for f in panel["form_fields"]],
        },
        "stages": [{**_pick(s, "title", "kind", "kind_label"), "status": _status(s["status"]),
                    **_pick(s, "delegatable", "attempts", "created_at")} for s in panel["stages"]],
        "links": [{**_pick(link, "type", "label", "key", "title"), "status": _status(link["status"])}
                  for link in panel["links"]],
        "direct": _pick(panel["direct"], "member_name", "started_at", "branch") if panel["direct"] else None,
        "open_requests": [
            {**_pick(r, "request_id", "code", "question", "state", "revision", "created_at", "answered_at"),
             "actions": [value for value, _ in r["actions"]], "asks_information": r["asks_information"],
             "agent_choices": [_pick(c, "agent_id", "name") for c in r["agent_choices"]]}
            for r in panel["open_requests"]
        ],
        "responses": [_pick(r, "question", "action", "text", "responder", "created_at", "task_revision")
                      for r in panel["responses"]],
        "triage": _triage(panel["triage"]),
        "next_step": _next_step(panel["next_step"]),
        "pull_requests": [_pick(p, "state", "label", "number", "url", "draft", "title", "runloom", "at")
                          for p in panel["pulls"]],
        "internal_requests": [_pick(r, "request_id", "system_id", "request_kind", "purpose", "phase_label",
                                    "requester_name", "recipient_name", "problem", "created_at", "returned_at")
                              for r in internal],
        "events": [_pick(e, "at", "text") for e in panel["events"]],
        "assignee_candidates": [
            *({"type": "member", "id": m["member_id"], "name": m["display_name"]} for m in panel["members"]),
            *({"type": "agent", "id": a["agent_id"], "name": a["name"], "online": a["online"],
               "needs_owner_approval": a["needs_owner_approval"]} for a in panel["agents"]),
        ],
        "actions": views.panel_actions(panel),
    }


@router.get("")
def list_work(
    filter: str = "all",
    repository: str = "",
    closed: str = "recent",
    member: LoggedIn = Depends(require_member_api),
    conn: Connection = Depends(get_conn),
) -> dict[str, Any]:
    if filter not in QUICK_FILTERS:
        raise ApiError(422, "invalid_field", f"filter 는 {'·'.join(QUICK_FILTERS)} 중 하나입니다.", field="filter")
    if closed not in CLOSED_SCOPES:
        raise ApiError(422, "invalid_field", f"closed 는 {'·'.join(CLOSED_SCOPES)} 중 하나입니다.", field="closed")
    picked = None
    if repository:
        picked = next((name for name in repo.list_work_repositories(conn, member.session_id)
                       if name.lower() == repository.lower()), None)
        if picked is None:
            raise ApiError(422, "invalid_field", f"저장소 {repository} 는 이 워크스페이스 업무 저장소가 아닙니다.",
                           field="repository")
    query = ListQuery(q=filter, group=GROUP_BYS[0], view=VIEWS[0], closed=closed, open_key=None, repo=picked)
    context = views.work_list_context(conn, member.session_id, member_id=member.member_id, query=query, now=utc_now())
    return {"items": [_item(row, member_id=member.member_id) for row in context["rows"]], "counts": context["counts"]}


@router.get("/{key}")
def get_work(
    key: str,
    request: Request,
    member: LoggedIn = Depends(require_member_api),
    conn: Connection = Depends(get_conn),
) -> dict[str, Any]:
    return _detail(conn, request, member, _own_work(conn, member.session_id, key))


# --- 행동 (step 7) ------------------------------------------------------------------------------------------

_delegate = require_action(team.DELEGATE, api=True)


class WorkPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    priority: Literal["high", "normal", "low"]


class AssigneeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["agent", "member"]
    id: NonEmptyStr
    note: str = ""


@contextmanager
def _api_errors() -> Iterator[None]:
    """`work_actions` 의 오류를 API 오류로 — 웹 `_page_errors` 와 같은 옮김."""
    try:
        yield
    except work_actions.WorkActionError as exc:
        raise ApiError(exc.status, exc.code, exc.message, field=exc.field) from None


def _assign(request: Request, conn: Connection, member: LoggedIn, key: str, value: str, note: str = "") -> dict:
    work = _own_work(conn, member.session_id, key)
    with _api_errors():
        work_actions.assign_work(conn, request.app.state.store, request.app.state.settings,
                                 session_id=member.session_id, work_item_id=work["work_item_id"], value=value,
                                 member_id=member.member_id, now=utc_now(), secrets=request.app.state.secrets,
                                 note=note)
    return _detail(conn, request, member, work)


@router.patch("/{key}")
def patch_work(
    key: str, body: WorkPatch, request: Request, member: LoggedIn = Depends(_delegate),
    conn: Connection = Depends(get_conn),
) -> dict[str, Any]:
    work = _own_work(conn, member.session_id, key)
    with _api_errors():
        work_actions.set_priority(conn, session_id=member.session_id, work_item_id=work["work_item_id"],
                                  priority=body.priority, member_id=member.member_id, now=utc_now())
    return _detail(conn, request, member, work)


@router.put("/{key}/assignee")
def put_assignee(
    key: str, body: AssigneeBody, request: Request, member: LoggedIn = Depends(_delegate),
    conn: Connection = Depends(get_conn),
) -> dict[str, Any]:
    return _assign(request, conn, member, key, f"{body.type}:{body.id}", body.note)


@router.delete("/{key}/assignee")
def delete_assignee(
    key: str, request: Request, member: LoggedIn = Depends(_delegate), conn: Connection = Depends(get_conn),
) -> dict[str, Any]:
    return _assign(request, conn, member, key, "none")


@router.put("/{key}/direct-work")
def put_direct_work(
    key: str, request: Request, member: LoggedIn = Depends(_delegate), conn: Connection = Depends(get_conn),
) -> dict[str, Any]:
    work = _own_work(conn, member.session_id, key)
    with _api_errors():
        work_actions.start_direct(conn, session_id=member.session_id, work_item_id=work["work_item_id"],
                                  member_id=member.member_id, now=utc_now())
    return _detail(conn, request, member, work)


@router.delete("/{key}/direct-work")
def delete_direct_work(
    key: str, request: Request, member: LoggedIn = Depends(_delegate), conn: Connection = Depends(get_conn),
) -> dict[str, Any]:
    work = _own_work(conn, member.session_id, key)
    with _api_errors():
        work_actions.stop_direct(conn, session_id=member.session_id, work_item_id=work["work_item_id"], now=utc_now())
    return _detail(conn, request, member, work)


@router.post("/{key}/triage-runs", status_code=202)
def post_triage_run(
    key: str, request: Request, member: LoggedIn = Depends(_delegate), conn: Connection = Depends(get_conn),
) -> dict[str, Any]:
    work = _own_work(conn, member.session_id, key)
    started = triage_runs.request_triage(conn, request.app.state.settings, session_id=member.session_id,
                                         work_item_id=work["work_item_id"], trigger="manual",
                                         member_id=member.member_id, now=utc_now())
    if not started.started:
        raise ApiError(409, "triage_unavailable", f"{started.reason}.")
    return _detail(conn, request, member, work)


@router.post("/{key}/triage-proposals/{triage_id}/accept")
def accept_triage(
    key: str, triage_id: str, request: Request, member: LoggedIn = Depends(_delegate),
    conn: Connection = Depends(get_conn),
) -> dict[str, Any]:
    work = _own_work(conn, member.session_id, key)
    with _api_errors():
        work_actions.accept_triage(conn, request.app.state.store, request.app.state.settings,
                                   session_id=member.session_id, work_item_id=work["work_item_id"],
                                   triage_id=triage_id, member_id=member.member_id, now=utc_now(),
                                   secrets=request.app.state.secrets)
    return _detail(conn, request, member, work)


@router.post("/{key}/triage-proposals/{triage_id}/dismiss")
def dismiss_triage(
    key: str, triage_id: str, request: Request, member: LoggedIn = Depends(_delegate),
    conn: Connection = Depends(get_conn),
) -> dict[str, Any]:
    work = _own_work(conn, member.session_id, key)
    with _api_errors():
        work_actions.dismiss_triage(conn, session_id=member.session_id, work_item_id=work["work_item_id"],
                                    triage_id=triage_id, member_id=member.member_id, now=utc_now())
    return _detail(conn, request, member, work)


@router.post("/{key}/next-step-proposals/{triage_id}/accept")
def accept_next_step(
    key: str, triage_id: str, request: Request, member: LoggedIn = Depends(_delegate),
    conn: Connection = Depends(get_conn),
) -> dict[str, Any]:
    work = _own_work(conn, member.session_id, key)
    with _api_errors():
        work_actions.accept_next_step(conn, request.app.state.store, request.app.state.settings,
                                      session_id=member.session_id, work_item_id=work["work_item_id"],
                                      triage_id=triage_id, member_id=member.member_id, now=utc_now(),
                                      secrets=request.app.state.secrets)
    return _detail(conn, request, member, work)


@router.post("/{key}/next-step-proposals/{triage_id}/dismiss")
def dismiss_next_step(
    key: str, triage_id: str, request: Request, member: LoggedIn = Depends(_delegate),
    conn: Connection = Depends(get_conn),
) -> dict[str, Any]:
    work = _own_work(conn, member.session_id, key)
    with _api_errors():
        work_actions.dismiss_next_step(conn, session_id=member.session_id, work_item_id=work["work_item_id"],
                                       triage_id=triage_id, member_id=member.member_id, now=utc_now())
    return _detail(conn, request, member, work)
