"""사람이 쓰는 웹 라우트 — 심사자 익명 세션과 운영자 (ADR-0005, UI_GUIDE "화면 목록", PRD 2·4절).

- 모든 라우트는 `require_session` 을 건다. 세션은 자기 Task 만 보고, 남의 것은 404 로 존재를 알리지 않는다.
- 화면은 문자열(HTML) 을 돌려준다. 그래야 `require_session` 이 sub-response 에 심은 Set-Cookie 가
  FastAPI 에서 합쳐진다. 303 은 `_redirect` 가 그 헤더를 옮긴다.
- 오류는 `PageError` 로 `error.html` 에 렌더한다. 본문 규칙(code·message·details) 은 ApiError 와 같다.
- 실행 요청(`ExecutionRequest`) 은 여기서 조립해 `request_json` 에 고정한다. 셸 명령·경로는 폼에서 받지 않는다.
  대상 정보는 선택된 Agent 등록값에서, 인계 자료는 선행 Task 의 `handoff_bundle` 산출물에서 온다.
- 진단 API·연결 프로그램에 직접 보내지 않는다. `queued` 로 남기면 워커(Step 8)·claim(Step 5) 이 가져간다.
- 화면은 UI_GUIDE 의 3열 셸이다. `GET /tasks/{id}/live` 는 상세의 라이브 조각(`_live.html`)만 돌려주고
  `base.html` 의 스크립트가 3초(마감 후 10초)마다 교체한다.
"""

import hashlib
import hmac
import json
import re
import secrets
from collections.abc import Sequence
from pathlib import Path
from sqlite3 import Connection, Row
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, FastAPI, Form, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from jinja2 import Environment, FileSystemLoader
from pydantic import ValidationError

from workflow.adapters import repo
from workflow.adapters.errors import ActiveExecutionExists, NotFound
from workflow.contracts.v1 import (
    ArtifactMeta,
    Capability,
    CodeChangeResult,
    ExecutionRequest,
    ReviewComment,
)
from workflow.domain.completion import can_auto_complete, criteria_template, merge_criteria
from workflow.domain.defaults import default_run_mode, kind_for_capability
from workflow.domain.selection import Candidate, select_agent
from workflow.domain.start_key import request_start_key
from workflow.domain.status import user_status
from workflow.server import views
from workflow.server.auth import get_conn, require_operator, require_session, utc_now
from workflow.server.errors import ApiError
from workflow.server.filters import ago, duration, kind_label, kst, outcome_label
from workflow.server.settings import Settings

TEMPLATE_DIR = Path(__file__).parent / "templates"

# 요구 능력 코드 → scope 키 (ARCHITECTURE "능력 필드 — 첫 구현"). 등록 폼은 값 하나만 받는다.
SCOPE_KEYS = {"operations.diagnose": "workflow_id", "code.modify": "repository_id"}
CAPABILITY_CODES = tuple(SCOPE_KEYS)
OWNER_SCOPES = ("personal", "team", "company")
CONNECTION_TYPES = ("local", "api")
REVIEW_DECISIONS = ("approve", "request_changes", "close")

# 등록 폼 미리 채움 (UI_GUIDE "심사자 첫 방문 흐름"). `example` 은 미리 채움 키일 뿐 목표 자동 분해가 아니다.
EXAMPLES: dict[str, dict[str, str]] = {
    "diagnose": {
        "title": "일일 보고서 실패 진단",
        "request": (
            "일일 보고서 생성 실패를 조사하고, 로컬 개발 에이전트가 재현·수정할 수 있도록 "
            "근거와 기대 동작을 정리해 주세요."
        ),
        "capability_code": "operations.diagnose",
        "scope_value": "daily-report",
        "selection_mode": "auto",
        "run_mode": "manual",
        # A 만 기본값(review)과 다르다. 자동 판정기가 있는 유일한 업무 종류라 인계가 사람 조작 없이 보인다.
        "completion_mode": "auto",
        "completion_note": "자동 판정기가 있는 진단 업무라 자동 완료로 미리 채움",
        "run_id": "daily-0920-0900",
    },
    "fix": {
        "title": "보고서 변환기 수정",
        "request": (
            "인계된 진단 근거로 보고서 변환 실패를 재현하는 테스트를 먼저 작성하고, "
            "실패를 확인한 뒤 두 응답 형식을 모두 처리하도록 최소 수정하세요."
        ),
        "capability_code": "code.modify",
        "scope_value": "demo-report-repo",
        "selection_mode": "auto",
        "run_mode": "auto",
        "completion_mode": "review",
        "completion_note": "",
        "run_id": "",
    },
}

_EMPTY_FORM: dict[str, str] = {
    "title": "",
    "request": "",
    "capability_code": "operations.diagnose",
    "scope_value": "",
    "selection_mode": "auto",
    "chosen_agent_id": "",
    "run_mode": "manual",
    "completion_mode": "review",
    "criteria_extra": "",
    "predecessor_task_id": "",
    "run_id": "",
    "completion_note": "",
}

_SAFE_FILENAME = re.compile(r"[^A-Za-z0-9._-]+")

router = APIRouter()

_env = Environment(loader=FileSystemLoader(TEMPLATE_DIR), autoescape=True)
_env.filters.update({
    "kst": kst, "ago": ago, "duration": duration,
    "outcome_label": outcome_label, "kind_label": kind_label,
})


class PageError(ApiError):
    """HTML 로 보여 줄 오류. `install` 이 `error.html` 로 렌더한다."""


def install(app: FastAPI) -> None:
    app.include_router(router)

    @app.exception_handler(PageError)
    async def _page_error(request: Request, exc: PageError) -> HTMLResponse:
        return HTMLResponse(_render("error.html", request=request, error=exc), status_code=exc.status)


# --- 렌더·리다이렉트 ----------------------------------------------------------------


def _render(name: str, **context: Any) -> str:
    return _env.get_template(name).render(**context)


def _redirect(url: str, response: Response) -> RedirectResponse:
    """303. `require_session` 이 sub-response 에 심은 Set-Cookie 를 옮긴다 (직접 반환한 Response 에는 합쳐지지 않는다)."""
    redirect = RedirectResponse(url, status_code=303)
    redirect.headers.raw.extend(response.headers.raw)
    return redirect


def _settings(request: Request) -> Settings:
    return request.app.state.settings


def _base(request: Request, conn: Connection, session_id: str, now: str) -> dict[str, Any]:
    """모든 화면에 들어가는 공통 컨텍스트 — 탐색·왼쪽 목록용."""
    session = repo.get_session(conn, session_id)
    settings = _settings(request)
    return {
        "request": request,
        "now": now,
        "session_id": session_id,
        "is_operator": bool(session["is_operator"]) if session is not None else False,
        "my_tasks": [
            views.task_summary(conn, t, now=now, settings=settings)
            for t in repo.list_tasks(conn, session_id)
        ],
    }


def _shared_agents(conn: Connection) -> list[Row]:
    return [a for a in repo.list_agents(conn) if a["shared_to_all_sessions"]]


def _candidates(conn: Connection) -> list[Candidate]:
    return [
        Candidate(
            agent_id=a["agent_id"],
            capabilities=tuple(
                Capability.model_validate(c) for c in json.loads(a["capabilities_json"])
            ),
        )
        for a in _shared_agents(conn)
    ]


def _own_task(conn: Connection, session_id: str, task_id: str) -> Row:
    row = repo.get_task(conn, task_id)
    if row is None or row["session_id"] != session_id:
        raise PageError(404, "not_found", f"업무 {task_id}을 찾을 수 없습니다.", field="task_id")
    return row


def _refresh_status(
    conn: Connection, task_id: str, now: str, settings: Settings, *, review_decision: str | None = None
) -> None:
    """실행·선택·선행 상태를 보고 Task 의 사용자 상태를 다시 저장한다 (PRD 3절 대응표)."""
    row = repo.get_task(conn, task_id)
    status = user_status(views.build_task_view(conn, row, now=now, settings=settings))
    repo.update_task_status(conn, task_id, status.label, status.reason, review_decision=review_decision)


def _target_for(kind: str, run_id: str, agent: Row | None) -> dict[str, Any]:
    """Task 의 target. 코드 수정은 선택된 Agent 의 등록값(연결 프로그램이 보고한 값)에서 온다. 폼에서 받지 않는다."""
    if kind == "diagnosis":
        return {"run_id": run_id}
    if agent is None:
        return {}
    profiles = json.loads(agent["verification_profile_ids_json"])
    return {
        "local_registration_id": agent["local_registration_id"],
        "base_commit": agent["base_commit"],
        "verification_profile_id": profiles[0] if profiles else None,
    }


# --- 실행 생성 -----------------------------------------------------------------------


def _check_diagnosis_limits(conn: Connection, session_id: str, now: str, settings: Settings) -> None:
    """CONTRACT 10절. 세션 일일 → 전체 일일 순으로 검사한다. 총액 상한은 진단 서비스가 판단한다."""
    since, resets_at = views.kst_day_bounds(now)
    limits = settings.limits
    if repo.count_diagnosis_started(conn, session_id=session_id, since=since) >= limits.per_session_daily:
        raise PageError(
            429,
            "daily_limit_reached",
            f"오늘 이 세션의 진단 실행 한도({limits.per_session_daily}회)에 도달했습니다.",
            details={"limit": limits.per_session_daily, "resets_at": resets_at},
        )
    if repo.count_diagnosis_started(conn, session_id=None, since=since) >= limits.global_daily:
        raise PageError(
            429,
            "daily_limit_reached",
            f"오늘 전체 진단 실행 한도({limits.global_daily}회)에 도달했습니다.",
            details={"limit": limits.global_daily, "resets_at": resets_at},
        )


def _handoff_inputs(conn: Connection, task: Row) -> tuple[list[str], str | None]:
    """선행 Task 의 가장 최근 `handoff_bundle` 산출물과 그것을 만든 실행 ID. 없으면 ([], None)."""
    if task["predecessor_task_id"] is None:
        return [], None
    for execution in reversed(repo.list_executions(conn, task["predecessor_task_id"])):
        for artifact in reversed(repo.artifacts_of(conn, execution["execution_id"])):
            if artifact["kind"] == "handoff_bundle":
                return [artifact["artifact_id"]], execution["execution_id"]
    return [], None


def _start_execution(
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
    """새 시도를 `queued` 로 만든다. 요청은 여기서 고정되고 이후 바뀌지 않는다. 반환은 execution_id."""
    kind = task["kind"]
    if kind == "diagnosis":
        _check_diagnosis_limits(conn, session_id, now, settings)
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
        })
    except ValidationError:
        raise PageError(
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
        raise PageError(409, "execution_conflict", "이미 활성 실행이 있습니다.") from None
    if kind == "diagnosis":
        repo.record_diagnosis_start(conn, session_id, execution_id, now)
    return execution_id


# --- 홈·업무 ----------------------------------------------------------------------


@router.get("/", response_class=HTMLResponse)
def landing(request: Request) -> str:
    """랜딩 — 세션을 만들지 않는다. `서비스 바로 가기` 가 `/tasks` 로 보낸다."""
    return _render("landing.html", request=request)


@router.get("/tasks", response_class=HTMLResponse)
def home(
    request: Request,
    session_id: str = Depends(require_session),
    conn: Connection = Depends(get_conn),
) -> str:
    now = utc_now()
    settings = _settings(request)
    agents = [views.agent_public(a, now=now, settings=settings) for a in _shared_agents(conn)]
    return _render("home.html", **_base(request, conn, session_id, now), agents=agents)


def _form_context(
    request: Request, conn: Connection, session_id: str, now: str, form: dict[str, str]
) -> dict[str, Any]:
    settings = _settings(request)
    return {
        **_base(request, conn, session_id, now),
        "form": form,
        "agents": [views.agent_public(a, now=now, settings=settings) for a in _shared_agents(conn)],
        "capability_codes": CAPABILITY_CODES,
        "scope_keys": SCOPE_KEYS,
        "criteria_templates": {
            kind: [c.text for c in criteria_template(kind)] for kind in ("diagnosis", "code_change")
        },
        "auto_completion_kinds": [k for k in ("diagnosis", "code_change") if can_auto_complete(k)],
        "form_kind": kind_for_capability(form["capability_code"]),
    }


@router.get("/tasks/new", response_class=HTMLResponse)
def task_new(
    request: Request,
    example: str | None = None,
    predecessor: str | None = None,
    session_id: str = Depends(require_session),
    conn: Connection = Depends(get_conn),
) -> str:
    now = utc_now()
    form = dict(_EMPTY_FORM)
    if example in EXAMPLES:
        form.update(EXAMPLES[example])
    else:
        form["run_mode"] = default_run_mode(bool(predecessor))
    if predecessor:
        _own_task(conn, session_id, predecessor)
        form["predecessor_task_id"] = predecessor
    # 시연 A 폼에서만 후속 B 동시 등록을 제안한다 — 심사자가 A 실행만으로 A → B 자동 착수를 보게 하기 위해
    form["offer_successor"] = "1" if example == "diagnose" else ""
    return _render("task_new.html", **_form_context(request, conn, session_id, now, form))


@router.post("/tasks")
def task_create(
    request: Request,
    response: Response,
    title: str = Form(""),
    request_text: str = Form("", alias="request"),
    capability_code: str = Form(""),
    scope_value: str = Form(""),
    selection_mode: str = Form("auto"),
    chosen_agent_id: str = Form(""),
    run_mode: str = Form("manual"),
    completion_mode: str = Form("review"),
    criteria_extra: str = Form(""),
    predecessor_task_id: str = Form(""),
    run_id: str = Form(""),
    with_successor: str = Form(""),
    session_id: str = Depends(require_session),
    conn: Connection = Depends(get_conn),
) -> RedirectResponse:
    now = utc_now()
    settings = _settings(request)
    title, request_text = title.strip(), request_text.strip()
    scope_value, run_id, chosen_agent_id = scope_value.strip(), run_id.strip(), chosen_agent_id.strip()
    predecessor_task_id = predecessor_task_id.strip()

    if not title or not request_text:
        raise PageError(422, "invalid_field", "제목과 요청 내용을 입력하세요.", field="title")
    if capability_code not in SCOPE_KEYS:
        raise PageError(422, "invalid_field", "지원하지 않는 능력 코드입니다.", field="capability_code")
    if not scope_value:
        raise PageError(422, "invalid_field", "능력 범위 값을 입력하세요.", field="scope_value")
    if selection_mode not in ("auto", "manual"):
        raise PageError(422, "invalid_field", "선택 방식이 올바르지 않습니다.", field="selection_mode")
    if run_mode not in ("manual", "auto"):
        raise PageError(422, "invalid_field", "실행 방식이 올바르지 않습니다.", field="run_mode")
    if completion_mode not in ("auto", "review"):
        raise PageError(422, "invalid_field", "완료 방식이 올바르지 않습니다.", field="completion_mode")
    if selection_mode == "manual" and not chosen_agent_id:
        raise PageError(422, "invalid_field", "직접 선택은 에이전트를 지정해야 합니다.", field="chosen_agent_id")

    kind = kind_for_capability(capability_code)
    if completion_mode == "auto" and not can_auto_complete(kind):
        raise PageError(
            422, "invalid_field",
            "이 업무 종류는 자동 완료를 지원하지 않습니다. 검토 후 완료를 선택하세요.",
            field="completion_mode",
        )
    if kind == "diagnosis" and not run_id:
        raise PageError(422, "invalid_field", "조사할 run_id 를 입력하세요.", field="run_id")
    if predecessor_task_id:
        _own_task(conn, session_id, predecessor_task_id)

    successor = bool(with_successor) and kind == "diagnosis"
    active = [t for t in repo.list_tasks(conn, session_id) if t["finished_at"] is None]
    limit = settings.limits.active_tasks_per_session
    if len(active) + (2 if successor else 1) > limit:
        raise PageError(
            429, "active_task_limit_reached",
            f"세션당 활성 업무 한도({limit}개)에 도달했습니다.", details={"limit": limit},
        )

    task_id = _insert_new_task(
        conn, session_id, now, settings,
        title=title, request_text=request_text, kind=kind, capability_code=capability_code,
        scope_value=scope_value, selection_mode=selection_mode, chosen_agent_id=chosen_agent_id,
        run_mode=run_mode, completion_mode=completion_mode, criteria_extra=criteria_extra,
        predecessor_task_id=predecessor_task_id, run_id=run_id,
    )
    if successor:
        # 시연 후속 B — fix 예시 그대로, A 를 선행으로. A 가 완료되면 워커가 별도 조작 없이 착수한다.
        fix = EXAMPLES["fix"]
        _insert_new_task(
            conn, session_id, now, settings,
            title=fix["title"], request_text=fix["request"], kind=kind_for_capability(fix["capability_code"]),
            capability_code=fix["capability_code"], scope_value=fix["scope_value"],
            selection_mode=fix["selection_mode"], chosen_agent_id="", run_mode=fix["run_mode"],
            completion_mode=fix["completion_mode"], criteria_extra="", predecessor_task_id=task_id, run_id="",
        )
    return _redirect(f"/tasks/{task_id}", response)


def _insert_new_task(
    conn: Connection, session_id: str, now: str, settings: Settings, *,
    title: str, request_text: str, kind: str, capability_code: str, scope_value: str,
    selection_mode: str, chosen_agent_id: str, run_mode: str, completion_mode: str,
    criteria_extra: str, predecessor_task_id: str, run_id: str,
) -> str:
    """검증이 끝난 값으로 Task 1개를 만들고 선택 기록·상태를 확정한다. 한도 검사는 호출자가 한다."""
    task_id = f"task-{secrets.token_hex(6)}"
    capability = Capability(code=capability_code, scope={SCOPE_KEYS[capability_code]: scope_value})
    selection = select_agent(
        task_id, capability, _candidates(conn), mode=selection_mode,
        chosen_agent_id=chosen_agent_id or None,
    )
    agent = repo.get_agent(conn, selection.selected_agent_id) if selection.selected_agent_id else None
    criteria = merge_criteria(criteria_template(kind), criteria_extra.splitlines())
    repo.insert_task(
        conn,
        {
            "task_id": task_id,
            "session_id": session_id,
            "title": title,
            "request": request_text,
            "kind": kind,
            "required_capability": capability.model_dump(),
            "selection_mode": selection_mode,
            "chosen_agent_id": chosen_agent_id or None,
            "run_mode": run_mode,
            "completion_mode": completion_mode,
            "criteria": [c.__dict__ for c in criteria],
            "predecessor_task_id": predecessor_task_id or None,
            "revision": 1,
            "target": _target_for(kind, run_id, agent),
            "status": "대기",
            "status_reason": "등록 중",
        },
        now,
    )
    repo.save_selection(conn, selection)
    _refresh_status(conn, task_id, now, settings)
    return task_id


@router.get("/tasks/{task_id}", response_class=HTMLResponse)
def task_detail(
    request: Request,
    task_id: str,
    session_id: str = Depends(require_session),
    conn: Connection = Depends(get_conn),
) -> str:
    now = utc_now()
    row = _own_task(conn, session_id, task_id)
    store = request.app.state.store
    context = views.task_context(conn, store, row, now=now, settings=_settings(request))
    viewer = views.viewer_context(conn, store, context["result"], session_id=session_id)
    return _render("task_detail.html", **_base(request, conn, session_id, now), **context, viewer=viewer)


@router.get("/tasks/{task_id}/live", response_class=HTMLResponse)
def task_live(
    request: Request,
    response: Response,
    task_id: str,
    session_id: str = Depends(require_session),
    conn: Connection = Depends(get_conn),
) -> str:
    """상세의 라이브 조각만 (상태 줄·실행 블록·결과 카드·산출물 칩·동작 영역·연결 업무 칩). 세션 소유 확인은 같다."""
    now = utc_now()
    row = _own_task(conn, session_id, task_id)
    store = request.app.state.store
    context = views.task_context(conn, store, row, now=now, settings=_settings(request))
    viewer = views.viewer_context(conn, store, context["result"], session_id=session_id)
    response.headers["Cache-Control"] = "no-store"
    return _render("_live.html", request=request, now=now, **context, viewer=viewer)


@router.post("/tasks/{task_id}/run")
def task_run(
    request: Request,
    response: Response,
    task_id: str,
    session_id: str = Depends(require_session),
    conn: Connection = Depends(get_conn),
) -> RedirectResponse:
    """직접 실행. 활성 실행 없음 · 선택됨 · 선행 완료가 조건이다. run_mode 와 무관하게 사용자 조작으로 시작할 수 있다."""
    now = utc_now()
    settings = _settings(request)
    task = _own_task(conn, session_id, task_id)
    if task["finished_at"] is not None:
        raise PageError(409, "invalid_transition", "마감된 업무는 실행할 수 없습니다.")
    if repo.active_execution(conn, task_id) is not None:
        raise PageError(409, "execution_conflict", "이미 활성 실행이 있습니다.")
    selection = repo.get_selection(conn, task_id)
    if selection is None or selection.status != "selected":
        raise PageError(409, "invalid_transition", "에이전트가 아직 선택되지 않았습니다.")
    if task["predecessor_task_id"] is not None:
        predecessor = repo.get_task(conn, task["predecessor_task_id"])
        if predecessor is None or predecessor["status"] != "완료":
            raise PageError(409, "invalid_transition", "선행 업무가 완료되지 않았습니다.")
    agent = repo.get_agent(conn, selection.selected_agent_id)
    if agent is None:
        raise PageError(409, "invalid_transition", "선택된 에이전트가 더 이상 등록돼 있지 않습니다.")

    inputs, predecessor_execution_id = (
        _handoff_inputs(conn, task) if task["kind"] == "code_change" else ([], None)
    )
    _start_execution(
        conn, task, agent, session_id=session_id, now=now, settings=settings,
        input_artifact_ids=inputs, predecessor_execution_id=predecessor_execution_id,
        target=json.loads(task["target_json"]),
    )
    _refresh_status(conn, task_id, now, settings)
    return _redirect(f"/tasks/{task_id}", response)


@router.post("/tasks/{task_id}/select")
def task_select(
    request: Request,
    response: Response,
    task_id: str,
    agent_id: str = Form(""),
    session_id: str = Depends(require_session),
    conn: Connection = Depends(get_conn),
) -> RedirectResponse:
    """`needs_selection` 인 업무에 에이전트를 직접 지정한다. 판단은 `select_agent(mode="manual")` 이 한다."""
    now = utc_now()
    settings = _settings(request)
    task = _own_task(conn, session_id, task_id)
    selection = repo.get_selection(conn, task_id)
    if task["finished_at"] is not None or repo.active_execution(conn, task_id) is not None:
        raise PageError(409, "invalid_transition", "실행 중이거나 마감된 업무는 선택을 바꿀 수 없습니다.")
    if selection is not None and selection.status == "selected":
        raise PageError(409, "invalid_transition", "이미 에이전트가 선택된 업무입니다.")
    if not agent_id.strip():
        raise PageError(422, "invalid_field", "에이전트를 지정하세요.", field="agent_id")

    capability = Capability.model_validate(json.loads(task["required_capability_json"]))
    record = select_agent(
        task_id, capability, _candidates(conn), mode="manual", chosen_agent_id=agent_id.strip()
    )
    repo.save_selection(conn, record)
    agent = repo.get_agent(conn, record.selected_agent_id) if record.selected_agent_id else None
    run_id = json.loads(task["target_json"]).get("run_id", "")
    repo.update_task_choice(
        conn, task_id, chosen_agent_id=agent_id.strip(),
        target=_target_for(task["kind"], run_id, agent),
    )
    _refresh_status(conn, task_id, now, settings)
    return _redirect(f"/tasks/{task_id}", response)


@router.post("/tasks/{task_id}/review")
def task_review(
    request: Request,
    response: Response,
    task_id: str,
    decision: str = Form(""),
    comment: str = Form(""),
    session_id: str = Depends(require_session),
    conn: Connection = Depends(get_conn),
) -> RedirectResponse:
    """PRD 4절 검토 동작. 결과가 `result_ready` 이고 사용자 상태가 `확인 필요` 일 때만 받는다."""
    now = utc_now()
    settings = _settings(request)
    store = request.app.state.store
    task = _own_task(conn, session_id, task_id)
    if decision not in REVIEW_DECISIONS:
        raise PageError(422, "invalid_field", "검토 동작이 올바르지 않습니다.", field="decision")
    execution = repo.active_execution(conn, task_id)
    status = views.status_of(task, views.build_task_view(conn, task, now=now, settings=settings))
    if (
        task["finished_at"] is not None
        or execution is None
        or execution["status"] != "result_ready"
        or status.label != "확인 필요"
    ):
        raise PageError(409, "invalid_transition", "검토할 결과가 없습니다.")
    execution_id = execution["execution_id"]

    if decision == "approve":
        reason = "검토 승인 · 병합: 운영자 확인 대기" if task["kind"] == "code_change" else "검토 승인"
        repo.update_task_status(
            conn, task_id, "완료", reason, finished_at=now, review_decision="approve"
        )
        repo.release_execution(conn, execution_id, now)
        return _redirect(f"/tasks/{task_id}", response)

    if decision == "close":
        repo.update_task_status(
            conn, task_id, "실패", "검토 거절", finished_at=now, review_decision="close"
        )
        repo.release_execution(conn, execution_id, now)
        return _redirect(f"/tasks/{task_id}", response)

    # request_changes — 같은 업무의 새 시도. 이전 결과와 검토 의견이 입력에 더해진다 (CONTRACT 8절).
    agent = repo.get_agent(conn, execution["agent_id"])
    if agent is None:
        raise PageError(409, "invalid_transition", "실행했던 에이전트가 더 이상 등록돼 있지 않습니다.")
    if task["kind"] == "diagnosis":
        _check_diagnosis_limits(conn, session_id, now, settings)  # 이전 시도를 해제하기 전에 확인
    review = ReviewComment(
        contract_version=1,
        task_id=task_id,
        reviewed_execution_id=execution_id,
        decision="request_changes",
        comment=comment,
        created_at=now,
    )
    data = review.model_dump_json().encode()
    created, _ = repo.store_artifact(
        conn, store,
        execution_id=execution_id,
        session_id=session_id,
        meta=ArtifactMeta(
            contract_version=1, kind="review_comment", name="review.json",
            content_type="application/json", sha256=hashlib.sha256(data).hexdigest(), size=len(data),
        ),
        data=data,
        now=now,
    )
    previous = ExecutionRequest.model_validate_json(execution["request_json"])
    inputs = list(
        dict.fromkeys([*previous.input_artifact_ids, execution["result_artifact_id"], created.artifact_id])
    )
    # 대상은 이전 시도의 요청을 잇는다. 코드 수정은 base_commit 만 이전 시도가 보존한 result_commit 으로.
    target = previous.target.model_dump()
    if task["kind"] == "code_change":
        result_commit = _result_commit(conn, store, execution["result_artifact_id"])
        if result_commit is not None:
            target["base_commit"] = result_commit
    repo.release_execution(conn, execution_id, now)
    _start_execution(
        conn, task, agent, session_id=session_id, now=now, settings=settings,
        input_artifact_ids=inputs,
        predecessor_execution_id=execution["predecessor_execution_id"],
        target=target,
    )
    _refresh_status(conn, task_id, now, settings, review_decision="request_changes")
    return _redirect(f"/tasks/{task_id}", response)


def _result_commit(conn: Connection, store: Any, artifact_id: str) -> str | None:
    """이전 시도의 `code_change_result` 에서 보존된 result_commit. 보류 제출(null)·깨진 결과면 None → 원래 기준 커밋."""
    try:
        result = CodeChangeResult.model_validate_json(repo.read_artifact(conn, store, artifact_id))
    except (ValidationError, ValueError, NotFound):
        return None
    return result.result_commit


@router.get("/tasks/{task_id}/artifacts/{artifact_id}", response_class=HTMLResponse)
def artifact_view(
    request: Request,
    task_id: str,
    artifact_id: str,
    raw: int = 0,
    session_id: str = Depends(require_session),
    conn: Connection = Depends(get_conn),
) -> Any:
    """세션 소유 + 이 업무의 실행이 만든 산출물만. `?raw=1` 은 저장된 바이트 그대로."""
    now = utc_now()
    _own_task(conn, session_id, task_id)
    artifact = repo.get_artifact(conn, artifact_id)
    execution = repo.get_execution(conn, artifact["execution_id"]) if artifact is not None else None
    if (
        artifact is None
        or artifact["session_id"] != session_id
        or execution is None
        or execution["task_id"] != task_id
    ):
        raise PageError(404, "not_found", f"산출물 {artifact_id}을 찾을 수 없습니다.", field="artifact_id")
    data = repo.read_artifact(conn, request.app.state.store, artifact_id)
    if raw:
        filename = _SAFE_FILENAME.sub("_", artifact["name"]) or artifact_id
        return Response(
            content=data,
            media_type=artifact["content_type"],
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    return _render(
        "artifact.html",
        **_base(request, conn, session_id, now),
        task_id=task_id,
        artifact=dict(artifact),
        text=data.decode("utf-8", errors="replace"),
        render=views.artifact_render(artifact, data),
    )


# --- 에이전트 (읽기 전용) ------------------------------------------------------------


@router.get("/agents", response_class=HTMLResponse)
def agents_list(
    request: Request,
    session_id: str = Depends(require_session),
    conn: Connection = Depends(get_conn),
) -> str:
    now = utc_now()
    settings = _settings(request)
    agents = [views.agent_public(a, now=now, settings=settings) for a in _shared_agents(conn)]
    return _render("agents.html", **_base(request, conn, session_id, now), agents=agents)


@router.get("/agents/{agent_id}", response_class=HTMLResponse)
def agent_detail(
    request: Request,
    agent_id: str,
    session_id: str = Depends(require_session),
    conn: Connection = Depends(get_conn),
) -> str:
    now = utc_now()
    row = repo.get_agent(conn, agent_id)
    if row is None or not row["shared_to_all_sessions"]:
        raise PageError(404, "not_found", f"에이전트 {agent_id}을 찾을 수 없습니다.", field="agent_id")
    agent = views.agent_public(row, now=now, settings=_settings(request))
    return _render("agent_detail.html", **_base(request, conn, session_id, now), agent=agent)


# --- 운영자 (ADR-0005) --------------------------------------------------------------


def _operator_context(
    request: Request, conn: Connection, session_id: str, now: str, issued: dict[str, str] | None = None
) -> dict[str, Any]:
    settings = _settings(request)
    tasks = [views.task_summary(conn, t, now=now, settings=settings) for t in repo.list_tasks(conn, None)]
    merge_queue = [
        t for t in repo.list_tasks(conn, None)
        if t["status"] == "완료" and t["kind"] == "code_change" and t["merge_confirmed_at"] is None
    ]
    since, _ = views.kst_day_bounds(now)
    return {
        **_base(request, conn, session_id, now),
        "agents": [views.agent_public(a, now=now, settings=settings) for a in repo.list_agents(conn)],
        "all_tasks": tasks,
        "merge_queue": [dict(t) for t in merge_queue],
        "connect_codes": [dict(c) for c in repo.list_connect_codes(conn)],
        "issued": issued,
        "diagnosis_today": repo.count_diagnosis_started(conn, session_id=None, since=since),
        "capability_codes": CAPABILITY_CODES,
        "scope_keys": SCOPE_KEYS,
        "owner_scopes": OWNER_SCOPES,
        "connection_types": CONNECTION_TYPES,
    }


@router.get("/operator", response_class=HTMLResponse)
def operator_page(
    request: Request,
    session_id: str = Depends(require_session),
    conn: Connection = Depends(get_conn),
) -> str:
    """운영자가 아니면 토큰 입력 폼만. 토큰 값은 세션 행의 `is_operator` 표시로만 남고 쿠키·HTML 에 넣지 않는다."""
    now = utc_now()
    base = _base(request, conn, session_id, now)
    if not base["is_operator"]:
        return _render("operator.html", **base)
    return _render("operator.html", **_operator_context(request, conn, session_id, now))


@router.post("/operator/login")
def operator_login(
    request: Request,
    response: Response,
    token: str = Form(""),
    session_id: str = Depends(require_session),
    conn: Connection = Depends(get_conn),
) -> RedirectResponse:
    expected = _settings(request).operator_token
    if not hmac.compare_digest(token.encode(), expected.encode()):
        raise PageError(403, "forbidden", "운영자 토큰이 올바르지 않습니다.")
    repo.mark_operator(conn, session_id)
    return _redirect("/operator", response)


@router.post("/operator/agents")
def operator_register_agent(
    request: Request,
    response: Response,
    agent_id: str = Form(""),
    name: str = Form(""),
    owner_scope: str = Form(""),
    connection_type: str = Form(""),
    capability_code: str = Form(""),
    scope_value: str = Form(""),
    local_registration_id: str = Form(""),
    api_url: str = Form(""),
    credential_ref: str = Form(""),
    session_id: str = Depends(require_operator),
    conn: Connection = Depends(get_conn),
) -> RedirectResponse:
    """운영자 등록. 자동 파악값은 연결 프로그램의 registrations 가 채우므로 기존 보고값은 유지한다."""
    agent_id, name = agent_id.strip(), name.strip()
    scope_value, local_registration_id = scope_value.strip(), local_registration_id.strip()
    api_url, credential_ref = api_url.strip(), credential_ref.strip()
    if not agent_id or not name:
        raise PageError(422, "invalid_field", "에이전트 ID 와 이름을 입력하세요.", field="agent_id")
    if owner_scope not in OWNER_SCOPES:
        raise PageError(422, "invalid_field", "소유 구분이 올바르지 않습니다.", field="owner_scope")
    if connection_type not in CONNECTION_TYPES:
        raise PageError(422, "invalid_field", "연결 유형이 올바르지 않습니다.", field="connection_type")
    if capability_code not in SCOPE_KEYS or not scope_value:
        raise PageError(422, "invalid_field", "능력 코드와 범위 값을 입력하세요.", field="capability_code")
    if connection_type == "local" and not local_registration_id:
        raise PageError(
            422, "invalid_field", "로컬 에이전트는 local_registration_id 가 필요합니다.",
            field="local_registration_id",
        )
    if connection_type == "api" and (not api_url or not credential_ref.startswith("env:")):
        raise PageError(
            422, "invalid_field",
            "API 에이전트는 주소와 `env:` 접두사의 자격 증명 참조명이 필요합니다. 값 자체는 넣지 않습니다.",
            field="credential_ref",
        )

    existing = repo.get_agent(conn, agent_id)
    reported = (
        {
            "connector_id": existing["connector_id"],
            "repository_id": existing["repository_id"],
            "base_commit": existing["base_commit"],
            "verification_profile_ids": json.loads(existing["verification_profile_ids_json"]),
            "discovered": json.loads(existing["discovered_json"]),
            "connection_state": existing["connection_state"],
            "last_seen_at": existing["last_seen_at"],
        }
        if existing is not None
        else {}
    )
    repo.upsert_agent(conn, {
        **reported,
        "agent_id": agent_id,
        "name": name,
        "owner_scope": owner_scope,
        "connection_type": connection_type,
        "capabilities": [{"code": capability_code, "scope": {SCOPE_KEYS[capability_code]: scope_value}}],
        "local_registration_id": local_registration_id or None,
        "api_url": api_url or None,
        "credential_ref": credential_ref or None,
        "shared_to_all_sessions": True,
    })
    return _redirect("/operator", response)


@router.post("/operator/agents/{agent_id}/delete")
def operator_delete_agent(
    response: Response,
    agent_id: str,
    session_id: str = Depends(require_operator),
    conn: Connection = Depends(get_conn),
) -> RedirectResponse:
    try:
        repo.delete_agent(conn, agent_id)
    except NotFound:
        raise PageError(404, "not_found", f"에이전트 {agent_id}을 찾을 수 없습니다.", field="agent_id") from None
    return _redirect("/operator", response)


@router.post("/operator/connect-codes", response_class=HTMLResponse)
def operator_issue_connect_code(
    request: Request,
    session_id: str = Depends(require_operator),
    conn: Connection = Depends(get_conn),
) -> str:
    """발급한 코드는 이 응답 화면에만 보인다 (URL 에 넣지 않는다). 1회용·10분."""
    now = utc_now()
    code = repo.issue_connect_code(conn, now)
    row = next(c for c in repo.list_connect_codes(conn) if c["code"] == code)
    issued = {"code": code, "expires_at": row["expires_at"]}
    return _render("operator.html", **_operator_context(request, conn, session_id, now, issued))


@router.post("/operator/connect-codes/{code}/revoke")
def operator_revoke_connect_code(
    response: Response,
    code: str,
    session_id: str = Depends(require_operator),
    conn: Connection = Depends(get_conn),
) -> RedirectResponse:
    try:
        repo.revoke_connect_code(conn, code, utc_now())
    except NotFound:
        raise PageError(404, "not_found", "취소할 수 있는 연결 코드가 아닙니다.", field="code") from None
    return _redirect("/operator", response)


@router.post("/operator/merges/{task_id}/confirm")
def operator_confirm_merge(
    response: Response,
    task_id: str,
    session_id: str = Depends(require_operator),
    conn: Connection = Depends(get_conn),
) -> RedirectResponse:
    """병합 확인 기록. 실제 git 병합은 운영자가 Mac 의 데모 저장소에서 수동으로 한다."""
    task = repo.get_task(conn, task_id)
    if (
        task is None
        or task["status"] != "완료"
        or task["kind"] != "code_change"
        or task["merge_confirmed_at"] is not None
    ):
        raise PageError(409, "invalid_transition", "병합 확인 대기 상태의 코드 수정 업무가 아닙니다.")
    repo.confirm_merge(conn, task_id, utc_now())
    return _redirect("/operator", response)
