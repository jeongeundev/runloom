"""사람이 쓰는 웹 라우트 — 로그인한 고정 워크스페이스(= 운영자) (ADR-0016 결정 3, ADR-0019, UI_GUIDE "화면 목록", PRD 2·4절).

- `/`·`/login`·`/logout` 밖의 라우트는 `require_session` 을 건다. 워크스페이스는 자기 Task 만 보고, 남의 것은 404 로
  존재를 알리지 않는다.
- 화면은 문자열(HTML) 을 돌려준다. 303 은 `_redirect` 가 sub-response 헤더를 옮긴다.
- 오류는 `PageError` 로 `error.html` 에 렌더한다. 본문 규칙(code·message·details) 은 ApiError 와 같다.
- 실행 요청(`ExecutionRequest`) 은 여기서 조립해 `request_json` 에 고정한다. 셸 명령·경로는 폼에서 받지 않는다.
  대상 정보는 선택된 Agent 등록값에서, 인계 자료는 선행 Task 의 `handoff_bundle` 산출물에서 온다.
- 진단 API·연결 프로그램에 직접 보내지 않는다. `queued` 로 남기면 워커(Step 8)·claim(Step 5) 이 가져간다.
- 화면은 UI_GUIDE 의 3열 셸이다. `GET /tasks/{id}/live` 는 상세의 라이브 조각(`_live.html`)만 돌려주고
  `base.html` 의 스크립트가 3초(마감 후 10초)마다 교체한다. 워크플로우 화면(`/chains/{id}`, phase 5 step 6)도
  같은 방식으로 `_chain_live.html` 을 교체하되, 실행 중·실행 요청됨·대기 노드가 없으면 폴링을 멈춘다(`data-poll`).
"""

import hashlib
import hmac
import json
import logging
import re
import secrets
import string
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from sqlite3 import Connection, Row
from typing import Any
from urllib.parse import quote, urlencode, urlsplit
from uuid import uuid4

from fastapi import APIRouter, Depends, FastAPI, Form, Query, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from jinja2 import Environment, FileSystemLoader
from pydantic import TypeAdapter, ValidationError

from workflow.adapters import repo, secret_store
from workflow.adapters.errors import (
    ActiveExecutionExists,
    DuplicateKind,
    DuplicateRule,
    KindInUse,
    KindProtected,
    NotFound,
)
from workflow.adapters.github_app import build_manifest, exchange_manifest_code, load_app, save_credentials
from workflow.adapters.github_client import (
    GitHubError,
    GitHubNotFound,
    GitHubRateLimited,
    GitHubUnavailable,
    HttpGitHubClient,
)
from workflow.adapters.notify_sender import NotifyFailed, NotifySender
from workflow.contracts.github import RepositoryFullName
from workflow.contracts.v1 import (
    ARTIFACT_KINDS,
    BUILTIN_KIND_NAMES,
    BUILTIN_KINDS,
    ArtifactMeta,
    Capability,
    CodeChangeResult,
    CodeChangeTarget,
    ExecutionRequest,
    KindSpec,
    ReviewComment,
    SuccessorRule,
    parse_rfc3339_aware,
)
from workflow.domain.completion import criteria_template, merge_criteria
from workflow.domain.composition import compose
from workflow.domain.defaults import default_run_mode
from workflow.domain.execution_policy import policy_for
from workflow.domain import notification
from workflow.domain.kinds import (
    get_kind,
    kind_for_capability,
    validate_capability,
    validate_rule,
)
from workflow.domain.selection import Candidate, select_agent
from workflow.domain.start_key import request_start_key
from workflow.domain.status import user_status
from workflow.domain.task_sources import Issue
from workflow.server import github_connect, metrics_api, task_cycle, views
from workflow.server.auth import (
    SELFHOST_SESSION_ID,
    SESSION_COOKIE,
    ensure_workspace,
    get_conn,
    require_operator,
    require_session,
    set_session_cookie,
    utc_now,
    workspace_session,
)
from workflow.server.errors import ApiError
from workflow.server.filters import ago, duration, kind_label, kst, outcome_label
from workflow.server.settings import Settings
from workflow.server.worker import Worker

TEMPLATE_DIR = Path(__file__).parent / "templates"

OWNER_SCOPES = ("personal", "team", "company")
CONNECTION_TYPES = ("local", "api")
REVIEW_DECISIONS = ("approve", "request_changes", "close")
# 종류·규칙 폼의 산출물 kind 선택지 — 묶음 자체(`handoff_bundle`)는 받는 산출물도 넘기는 산출물도 아니다
INPUT_KIND_CHOICES = tuple(k for k in ARTIFACT_KINDS if k != "handoff_bundle")
# 입구 (ADR-0010) — 토큰은 지금 n8n 하나에 묶이고, 경로는 inbound_api 의 것을 화면에 그대로 적는다
INBOUND_SOURCE = "n8n"
INBOUND_PATH = "/sources/n8n/chains"
SOURCE_TOKEN_LIMIT = 5  # 세션당 활성 입구 토큰 상한

# 직접 등록 폼 기본값. 기본 종류는 내장 `bug_fix`(능력 `code.fix`) — 셀프호스트 실사용 종류 (ADR-0019)
_EMPTY_FORM: dict[str, str] = {
    "title": "",
    "request": "",
    "capability_code": "code.fix",
    "scope_value": "",
    "selection_mode": "auto",
    "chosen_agent_id": "",
    "run_mode": "manual",
    "completion_mode": "review",
    "criteria_extra": "",
    "predecessor_task_id": "",
    "completion_note": "",
}

_SAFE_FILENAME = re.compile(r"[^A-Za-z0-9._-]+")

router = APIRouter()
logger = logging.getLogger(__name__)

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


def _session_agents(conn: Connection, session_id: str) -> list[Row]:
    """워크스페이스에 붙은 Agent 만(`session_agents` — 러너 등록이 붙인다). 홈·등록 폼·선택 목록·후보는 전부 이 목록을 쓴다."""
    return repo.list_session_agents(conn, session_id)


def _candidates(conn: Connection, session_id: str) -> list[Candidate]:
    return [
        Candidate(
            agent_id=a["agent_id"],
            capabilities=tuple(
                Capability.model_validate(c) for c in json.loads(a["capabilities_json"])
            ),
        )
        for a in _session_agents(conn, session_id)
    ]


def _require_registered(conn: Connection, session_id: str, agent_id: str) -> None:
    """직접 선택 대상은 워크스페이스에 붙은 Agent 여야 한다. 아니면 422."""
    if not repo.is_session_agent(conn, session_id, agent_id):
        raise PageError(422, "agent_not_registered", "등록하지 않은 에이전트입니다.", field="agent_id")


def _own_task(conn: Connection, session_id: str, task_id: str) -> Row:
    row = repo.get_task(conn, task_id)
    if row is None or row["session_id"] != session_id:
        raise PageError(404, "not_found", f"업무 {task_id}을 찾을 수 없습니다.", field="task_id")
    return row


def _own_chain(conn: Connection, session_id: str, chain_id: str) -> Row:
    row = repo.get_chain(conn, chain_id)
    if row is None or row["session_id"] != session_id:
        raise PageError(404, "not_found", f"워크플로우 {chain_id}을 찾을 수 없습니다.", field="chain_id")
    return row


def _refresh_status(
    conn: Connection, task_id: str, now: str, settings: Settings, *, review_decision: str | None = None
) -> None:
    """실행·선택·선행 상태를 보고 Task 의 사용자 상태를 다시 저장한다 (PRD 3절 대응표)."""
    row = repo.get_task(conn, task_id)
    status = user_status(views.build_task_view(conn, row, now=now, settings=settings))
    repo.update_task_status(
        conn, task_id, status.label, status.reason, review_decision=review_decision, now=now
    )


# --- 업무 종류 등록부 (ADR-0009) — 요구 능력·종류 판단은 세션 등록부 + domain.kinds 로만 한다 ---------------


def _kinds(conn: Connection, session_id: str) -> list[KindSpec]:
    return repo.list_kinds(conn, session_id)


def _unknown_kind() -> PageError:
    return PageError(422, "invalid_field", "등록되지 않은 업무 종류입니다.", field="capability_code")


def _kind_of(conn: Connection, session_id: str, kind: str) -> KindSpec:
    spec = repo.get_kind(conn, session_id, kind)
    if spec is None:
        raise _unknown_kind()
    return spec


def _kind_for_code(conn: Connection, session_id: str, code: str) -> KindSpec:
    """능력 코드가 어느 종류의 `capability_code` 인가 — 등록부에 없으면 422."""
    spec = kind_for_capability(_kinds(conn, session_id), code)
    if spec is None:
        raise _unknown_kind()
    return spec


def _target_for(spec: KindSpec, agent: Row | None) -> dict[str, Any]:
    """Task 의 target. 선택된 Agent 의 등록값(연결 프로그램이 보고한 값)에서 온다 — 폼에서 받지 않는다.
    코드 수정 대상(`bug_fix`, 실행 정책 target `code_change`)은 등록·기준 커밋·첫 검증 프로필, 그 밖은 등록 ID 하나.
    Agent 가 아직 없으면 비워 두고 선택 뒤에 채운다."""
    if agent is None:
        return {}
    if policy_for(spec.kind).target == "code_change":
        profiles = json.loads(agent["verification_profile_ids_json"])
        return {
            "local_registration_id": agent["local_registration_id"],
            "base_commit": agent["base_commit"],
            "verification_profile_id": profiles[0] if profiles else None,
        }
    return {"local_registration_id": agent["local_registration_id"]}


# --- 실행 생성 -----------------------------------------------------------------------


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
    """새 시도를 `queued` 로 만든다. 요청은 여기서 고정되고 이후 바뀌지 않는다 — 종류 봉투(`kind_spec`)도 등록부에서
    이때 채운다. 반환은 execution_id."""
    kind = task["kind"]
    spec = repo.get_kind(conn, session_id, kind)
    if spec is None:
        raise PageError(409, "request_incomplete", "실행 요청을 만들 수 없습니다. 업무 종류가 등록돼 있지 않습니다.")
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
            "kind_spec": spec.model_dump(),
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
    return execution_id


# --- 홈·업무 ----------------------------------------------------------------------


@router.get("/")
def root(request: Request, conn: Connection = Depends(get_conn)) -> RedirectResponse:
    """랜딩 없음 (ADR-0019) — 로그인 상태면 업무 목록 `/tasks`, 아니면 `/login` 으로 303. 세션을 만들지 않는다."""
    return RedirectResponse("/tasks" if workspace_session(request, conn) else "/login", status_code=303)


# --- 로그인 (ADR-0016 결정 3) ---------------------------------------------------------------


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request) -> str:
    return _render("login.html", request=request, error=None)


def _workspace_login(request: Request, conn: Connection, token: str) -> HTMLResponse | RedirectResponse:
    """`OPERATOR_TOKEN` 과 비교해 맞으면 고정 워크스페이스 쿠키. 입력한 토큰 값은 응답·로그에 넣지 않는다."""
    throttle = request.app.state.login_throttle
    if throttle.blocked():
        logger.warning("로그인 거부: 연속 실패 제한")
        return HTMLResponse(
            _render("login.html", request=request, error="로그인 시도가 너무 많습니다. 잠시 후 다시 시도하세요."),
            status_code=429,
        )
    settings = _settings(request)
    if not hmac.compare_digest(token.encode(), settings.operator_token.encode()):
        throttle.fail()
        logger.warning("로그인 실패")
        return HTMLResponse(
            _render("login.html", request=request, error="토큰이 올바르지 않습니다."), status_code=403,
        )
    throttle.reset()
    ensure_workspace(conn, utc_now())
    redirect = RedirectResponse("/", status_code=303)
    set_session_cookie(redirect, SELFHOST_SESSION_ID, settings)
    return redirect


@router.post("/login", response_model=None)
def login(
    request: Request, token: str = Form(""), conn: Connection = Depends(get_conn),
) -> HTMLResponse | RedirectResponse:
    return _workspace_login(request, conn, token)


@router.post("/logout")
def logout() -> RedirectResponse:
    """쿠키만 지운다. 워크스페이스 행은 그대로."""
    redirect = RedirectResponse("/login", status_code=303)
    redirect.delete_cookie(SESSION_COOKIE, httponly=True, samesite="lax")
    return redirect


@router.get("/tasks", response_class=HTMLResponse)
def home(
    request: Request,
    session_id: str = Depends(require_session),
    conn: Connection = Depends(get_conn),
) -> str:
    now = utc_now()
    settings = _settings(request)
    agents = [views.agent_public(a, now=now, settings=settings) for a in _session_agents(conn, session_id)]
    chains = [
        views.chain_summary(conn, c, now=now, settings=settings) for c in repo.list_chains(conn, session_id)
    ]
    return _render("home.html", **_base(request, conn, session_id, now), agents=agents, chains=chains)


def _form_context(
    request: Request, conn: Connection, session_id: str, now: str, form: dict[str, str]
) -> dict[str, Any]:
    settings = _settings(request)
    kinds = _kinds(conn, session_id)
    form_spec = kind_for_capability(kinds, form["capability_code"])
    if form_spec is None:
        raise _unknown_kind()
    return {
        **_base(request, conn, session_id, now),
        "form": form,
        "agents": [views.agent_public(a, now=now, settings=settings) for a in _session_agents(conn, session_id)],
        # 요구 능력 select 는 세션 등록부에서 — 종류를 고르면 capability_code·scope_key 가 정해진다 (ARCHITECTURE "화면")
        "kind_options": [views.kind_public(spec) for spec in kinds],
        "criteria_templates": {spec.kind: [c.text for c in criteria_template(spec)] for spec in kinds},
        "form_kind": form_spec.kind,
        "form_scope_key": form_spec.scope_key,
    }


@router.get("/tasks/new", response_class=HTMLResponse)
def task_new(
    request: Request,
    predecessor: str | None = None,
    session_id: str = Depends(require_session),
    conn: Connection = Depends(get_conn),
) -> str:
    now = utc_now()
    form = dict(_EMPTY_FORM)
    form["run_mode"] = default_run_mode(bool(predecessor))
    if predecessor:
        _own_task(conn, session_id, predecessor)
        form["predecessor_task_id"] = predecessor
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
    session_id: str = Depends(require_session),
    conn: Connection = Depends(get_conn),
) -> RedirectResponse:
    now = utc_now()
    settings = _settings(request)
    title, request_text = title.strip(), request_text.strip()
    scope_value, chosen_agent_id = scope_value.strip(), chosen_agent_id.strip()
    predecessor_task_id = predecessor_task_id.strip()

    if not title or not request_text:
        raise PageError(422, "invalid_field", "제목과 요청 내용을 입력하세요.", field="title")
    spec = _kind_for_code(conn, session_id, capability_code)
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
    if selection_mode == "manual":
        _require_registered(conn, session_id, chosen_agent_id)

    if completion_mode == "auto":  # 자동 완료 검증기가 있는 종류가 없다 — 진단 데모는 `main` 전용 (ADR-0019)
        raise PageError(
            422, "invalid_field",
            "이 업무 종류는 자동 완료를 지원하지 않습니다. 검토 후 완료를 선택하세요.",
            field="completion_mode",
        )
    if predecessor_task_id:
        _own_task(conn, session_id, predecessor_task_id)

    active = [t for t in repo.list_tasks(conn, session_id) if t["finished_at"] is None]
    limit = settings.limits.active_tasks_per_session
    if len(active) + 1 > limit:
        raise PageError(
            429, "active_task_limit_reached",
            f"세션당 활성 업무 한도({limit}개)에 도달했습니다.", details={"limit": limit},
        )

    task_id = _insert_new_task(
        conn, session_id, now, settings,
        title=title, request_text=request_text, spec=spec, scope_value=scope_value,
        selection_mode=selection_mode, chosen_agent_id=chosen_agent_id,
        run_mode=run_mode, completion_mode=completion_mode, criteria_extra=criteria_extra,
        predecessor_task_id=predecessor_task_id,
    )
    return _redirect(f"/tasks/{task_id}", response)


def _insert_new_task(
    conn: Connection, session_id: str, now: str, settings: Settings, *,
    title: str, request_text: str, spec: KindSpec, scope_value: str,
    selection_mode: str, chosen_agent_id: str, run_mode: str, completion_mode: str,
    criteria_extra: str, predecessor_task_id: str,
    chain_id: str | None = None, source_ref: str | None = None, prefer: Sequence[str] | None = None,
    source_type: str = "manual",
) -> str:
    """검증이 끝난 값으로 업무 1개와 그 첫 단계 Task 를 만들고 선택 기록·상태를 확정한다. 한도 검사는 호출자가 한다.
    `source_type="n8n"` 이면 업무 원본 칸이 체인·항목 키(v9 → v10 마이그레이션과 같다).
    요구 능력은 종류 봉투(`spec.capability_code` + `spec.scope_key`)에서 만들고 등록부로 한 번 더 검사한다.

    `prefer` 는 세션 등록 순서(동률 기본 선택, step 4). 직접 등록은 넘기지 않아 동률이면 확인 필요다."""
    task_id = f"task-{secrets.token_hex(6)}"
    capability = Capability(code=spec.capability_code, scope={spec.scope_key: scope_value})
    reason = validate_capability(_kinds(conn, session_id), capability)
    if reason is not None:
        raise PageError(422, "invalid_field", reason, field="capability_code")
    selection = select_agent(
        task_id, capability, _candidates(conn, session_id), mode=selection_mode,
        chosen_agent_id=chosen_agent_id or None, prefer=prefer,
    )
    agent = repo.get_agent(conn, selection.selected_agent_id) if selection.selected_agent_id else None
    criteria = merge_criteria(criteria_template(spec), criteria_extra.splitlines())
    n8n = source_type == "n8n"
    repo.insert_work_item_task(
        conn,
        {
            "task_id": task_id,
            "session_id": session_id,
            "title": title,
            "request": request_text,
            "kind": spec.kind,
            "required_capability": capability.model_dump(),
            "selection_mode": selection_mode,
            "chosen_agent_id": chosen_agent_id or None,
            "run_mode": run_mode,
            "completion_mode": completion_mode,
            "criteria": [c.__dict__ for c in criteria],
            "predecessor_task_id": predecessor_task_id or None,
            "revision": 1,
            "target": _target_for(spec, agent),
            "status": "대기",
            "status_reason": "등록 중",
            "chain_id": chain_id,
            "source_ref": source_ref,
        },
        now,
        source_type=source_type, source_id=chain_id if n8n else None,
        source_item_id=source_ref if n8n else None, source_key=source_ref if n8n else None,
    )
    repo.save_selection(conn, selection)
    _refresh_status(conn, task_id, now, settings)
    return task_id


@dataclass(frozen=True)
class ChainCreated:
    chain_id: str
    task_ids: dict[str, str]  # issue key → task_id (체인 노드 + 능력 있는 단독 Task)
    skipped: list[dict[str, str]]  # {key, title, reason}


def create_chain(
    conn: Connection, session_id: str, issues: Sequence[Issue], *, source: str, now: str, settings: Settings,
    callback_url: str | None = None, items: list[dict] | None = None, error: type[ApiError] = ApiError,
    items_field: str = "issue_keys",
) -> ChainCreated:
    """이슈들로 체인 하나와 Task 들을 만든다 — 입구 API(`inbound_api`, ADR-0010)의 본체.
    등록 에이전트 확인 → `compose` → 활성 한도 → `insert_chain` → Task 삽입. 실행은 만들지 않는다.

    오류는 `error(...)` 로 던진다 — 웹은 `PageError`(error.html), 입구 API 는 기본값 `ApiError`(JSON).
    `items_field` 는 `dependency_cycle` 의 field 이름(폼은 `issue_keys`, 입구 본문은 `items`).
    `callback_url`·`items` 는 입구 API 만 넘긴다(허용 목록 검사는 호출자가 끝낸 값)."""
    registered = _session_agents(conn, session_id)
    if not registered:
        raise error(422, "agent_not_registered", "에이전트를 먼저 등록하세요.", field="agent_id")
    prefer = [a["agent_id"] for a in registered]
    kinds = _kinds(conn, session_id)
    rules = [rule for _, rule in repo.list_rules(conn, session_id)]
    try:
        plan = compose(issues, _candidates(conn, session_id), prefer=prefer, kinds=kinds, rules=rules)
    except ValueError as exc:
        raise error(422, "dependency_cycle", str(exc), field=items_field) from None

    # Standalone 중 능력이 있는 것(지원 안 되는 인계 쌍)은 선행 없는 단독 Task, 없는 것은 체인의 skipped 로만
    capable_standalone = [item for item in plan.standalone if item.mapping.capability is not None]
    skipped = [
        {"key": item.issue.key, "title": item.issue.title, "reason": item.reason}
        for item in plan.standalone if item.mapping.capability is None
    ]
    active = [t for t in repo.list_tasks(conn, session_id) if t["finished_at"] is None]
    limit = settings.limits.active_tasks_per_session
    if len(active) + len(plan.nodes) + len(capable_standalone) > limit:
        raise error(
            429, "active_task_limit_reached",
            f"세션당 활성 업무 한도({limit}개)에 도달했습니다.", details={"limit": limit},
        )

    chain_id = f"chain-{secrets.token_hex(6)}"
    repo.insert_chain(
        conn,
        {"chain_id": chain_id, "session_id": session_id, "title": plan.title, "source": source,
         "skipped": skipped, "callback_url": callback_url, "items": items},
        now,
    )
    work_source = "n8n" if source == INBOUND_SOURCE else "manual"
    # PlanNode.selection 은 임시 task_id(issue.key) 라 저장하지 않는다 — 실제 task_id 로 다시 계산한다
    task_ids: dict[str, str] = {}
    for node in plan.nodes:
        spec = get_kind(kinds, node.kind)
        task_ids[node.issue.key] = _insert_new_task(
            conn, session_id, now, settings,
            title=node.issue.title, request_text=node.issue.body, spec=spec,
            scope_value=node.capability.scope[spec.scope_key],
            selection_mode="auto", chosen_agent_id="", run_mode=node.run_mode,
            completion_mode=node.completion_mode, criteria_extra="",
            predecessor_task_id=task_ids[node.predecessor_key] if node.predecessor_key else "",
            chain_id=chain_id, source_ref=node.issue.key, prefer=prefer, source_type=work_source,
        )
    for item in capable_standalone:
        capability = item.mapping.capability
        spec = _kind_for_code(conn, session_id, capability.code)
        task_ids[item.issue.key] = _insert_new_task(
            conn, session_id, now, settings,
            title=item.issue.title, request_text=item.issue.body, spec=spec,
            scope_value=capability.scope[spec.scope_key],
            selection_mode="auto", chosen_agent_id="", run_mode="manual",
            completion_mode="review", criteria_extra="",
            predecessor_task_id="",
            chain_id=chain_id, source_ref=item.issue.key, prefer=prefer, source_type=work_source,
        )
    return ChainCreated(chain_id=chain_id, task_ids=task_ids, skipped=skipped)


# --- 워크플로우 화면 — 체인 상세·시작·라이브 (phase 5 step 6). 화면 라벨은 "워크플로우", 경로·코드는 chain ------


def _chain_context(request: Request, conn: Connection, session_id: str, chain: Row, now: str) -> dict[str, Any]:
    """체인 화면·라이브 조각 공통. 인라인 담당 선택 폼의 후보는 세션 등록 Agent (등록 순)."""
    settings = _settings(request)
    return {
        "chain": views.chain_summary(conn, chain, now=now, settings=settings),
        "candidates": [
            views.agent_public(a, now=now, settings=settings) for a in _session_agents(conn, session_id)
        ],
    }


@router.get("/chains/{chain_id}", response_class=HTMLResponse)
def chain_page(
    request: Request,
    chain_id: str,
    session_id: str = Depends(require_session),
    conn: Connection = Depends(get_conn),
) -> str:
    """구성 결과(순서·담당·이유)와 `워크플로우 시작`. 두 번째 노드부터는 워커가 선행 완료를 보고 자동으로 잇는다."""
    now = utc_now()
    chain = _own_chain(conn, session_id, chain_id)
    return _render(
        "chain_detail.html", **_base(request, conn, session_id, now),
        **_chain_context(request, conn, session_id, chain, now),
    )


@router.get("/chains/{chain_id}/live", response_class=HTMLResponse)
def chain_live(
    request: Request,
    response: Response,
    chain_id: str,
    session_id: str = Depends(require_session),
    conn: Connection = Depends(get_conn),
) -> str:
    """노드 목록 + 사람 단계 + 동작 영역 조각만 (`_chain_live.html`)."""
    now = utc_now()
    chain = _own_chain(conn, session_id, chain_id)
    response.headers["Cache-Control"] = "no-store"
    return _render(
        "_chain_live.html", request=request, now=now, **_chain_context(request, conn, session_id, chain, now)
    )


@router.post("/chains/{chain_id}/start")
def chain_start(
    request: Request,
    response: Response,
    chain_id: str,
    session_id: str = Depends(require_session),
    conn: Connection = Depends(get_conn),
) -> RedirectResponse:
    """첫 노드를 `task_run` 과 같은 경로로 실행하고 `started_at` 을 기록한다. 이미 시작했으면 그대로 303 (멱등)."""
    now = utc_now()
    chain = _own_chain(conn, session_id, chain_id)
    start_chain(conn, chain, session_id=session_id, now=now, settings=_settings(request), error=PageError)
    return _redirect(f"/chains/{chain_id}", response)


def start_chain(
    conn: Connection, chain: Row, *, session_id: str, now: str, settings: Settings,
    error: type[ApiError] = ApiError,
) -> None:
    """체인의 첫 노드를 `task_run` 과 같은 경로로 실행하고 `started_at` 을 기록한다 — `chain_start` 와 입구 API 의 공통 본체.
    이미 시작했으면 새 실행 없이 시작 기록만 맞춘다(멱등). 오류는 `error(...)` 로 던진다(`_run_task` 의 것은 PageError 그대로 —
    ApiError 의 하위라 입구 API 는 함께 잡는다)."""
    chain_id = chain["chain_id"]
    tasks = repo.tasks_of_chain(conn, chain_id)
    if not tasks:
        raise error(409, "invalid_transition", "시작할 업무가 없습니다.")
    first = tasks[0]
    # 첫 노드가 이미 실행된 적 있으면(업무 상세의 `실행` 등) 새 실행 없이 시작 기록만 맞춘다
    if chain["started_at"] is None and not repo.list_executions(conn, first["task_id"]):
        selection = repo.get_selection(conn, first["task_id"])
        if selection is None or selection.status != "selected":
            raise error(409, "selection_required", "담당 에이전트를 먼저 확정하세요.")
        _run_task(conn, first, session_id=session_id, now=now, settings=settings)
    repo.mark_chain_started(conn, chain_id, now)


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
    base = _base(request, conn, session_id, now)
    context = views.task_context(conn, store, row, now=now, settings=_settings(request), is_operator=base["is_operator"])
    viewer = views.viewer_context(conn, store, context["result"], session_id=session_id)
    return _render("task_detail.html", **base, **context, viewer=viewer)


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
    session = repo.get_session(conn, session_id)
    context = views.task_context(
        conn, store, row, now=now, settings=_settings(request), is_operator=bool(session and session["is_operator"]),
    )
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
    """직접 실행. 활성 실행 없음 · 선택됨 · (선행이 있으면) 선행 결과 + 판정 + 인계 묶음이 조건이다 (ADR-0009 (3)).
    run_mode 와 무관하게 사용자 조작으로 시작할 수 있다."""
    now = utc_now()
    task = _own_task(conn, session_id, task_id)
    if policy_for(task["kind"]).cycle:
        _run_cycle_task(conn, request.app.state.store, task, now=now, settings=_settings(request))
    else:
        _run_task(conn, task, session_id=session_id, now=now, settings=_settings(request))
    return _redirect(f"/tasks/{task_id}", response)


@router.post("/tasks/{task_id}/delegate")
def task_delegate(
    request: Request,
    response: Response,
    task_id: str,
    session_id: str = Depends(require_session),
    conn: Connection = Depends(get_conn),
) -> RedirectResponse:
    """[에이전트에게 맡기기] (ADR-0017) — GitHub 원본 Task 에 운영자 실행 지시를 한 번 기록하고(멱등) `/run` 과 같은
    착수(`Worker.start_manually`)를 시도한다. 지금 못 시작하면 대기 사유는 상세에 남고 워커가 풀리는 대로 착수한다."""
    now = utc_now()
    task = _own_task(conn, session_id, task_id)
    _require_operator_page(conn, session_id)
    issue = repo.get_source_issue_by_task(conn, session_id, task_id)
    if issue is None:
        raise PageError(409, "not_delegatable", "GitHub 이슈에서 온 업무만 맡길 수 있습니다.")
    if task["finished_at"] is not None:
        raise PageError(409, "task_closed", "마감된 업무는 맡길 수 없습니다.")
    repo.mark_issue_delegated(conn, session_id=session_id, source_id=issue["source_id"],
                              github_issue_id=issue["github_issue_id"], by="operator", now=now)
    worker = Worker(lambda: conn, request.app.state.store, None, _settings(request), lambda: now)
    worker.start_manually(conn, task_id)
    return _redirect(f"/tasks/{task_id}", response)


def _run_cycle_task(conn: Connection, store: Any, task: Row, *, now: str, settings: Settings) -> None:
    """업무 순환 Task 의 직접 실행 — 워커와 같은 준비 판정·후속 결정·start_key 로 착수하고(`Worker.start_manually`)
    `manual_mode` 만 뺀다. 대상·입력은 워커가 Agent 등록값·원인 결과로 고정한다. 새 실행이 없으면 지금 대기 사유로 409."""
    if task["finished_at"] is not None:
        raise PageError(409, "invalid_transition", "마감된 업무는 실행할 수 없습니다.")
    worker = Worker(lambda: conn, store, None, settings, lambda: now)  # 착수 단계만 쓴다 — callback 없음
    if worker.start_manually(conn, task["task_id"]):
        return
    readiness = task_cycle.evaluate(conn, repo.get_task(conn, task["task_id"]), now=now, settings=settings,
                                    run_mode="auto")
    reasons = " · ".join(b.reason for b in readiness.blockers) or "이미 실행이 있거나 이어서 시작할 결과가 없습니다"
    raise PageError(409, "invalid_transition", f"지금 시작할 수 없습니다 — {reasons}.")


def _run_task(conn: Connection, task: Row, *, session_id: str, now: str, settings: Settings) -> None:
    """`task_run` 과 `chain_start` 의 공통 경로 — 조건 검사, 실행 생성, 상태 갱신."""
    task_id = task["task_id"]
    if task["finished_at"] is not None:
        raise PageError(409, "invalid_transition", "마감된 업무는 실행할 수 없습니다.")
    if repo.active_execution(conn, task_id) is not None:
        raise PageError(409, "execution_conflict", "이미 활성 실행이 있습니다.")
    selection = repo.get_selection(conn, task_id)
    if selection is None or selection.status != "selected":
        raise PageError(409, "invalid_transition", "에이전트가 아직 선택되지 않았습니다.")
    # 선행이 있으면 선행 결과가 판정되고 인계 묶음이 있어야 한다 — 선행 `완료`(사람 승인)를 기다리지 않는다 (ADR-0009 (3)).
    # 묶음은 워커가 규칙 `handoff_kinds` 로 조립하므로 규칙이 없으면 생기지 않는다.
    inputs, predecessor_execution_id = views.predecessor_handoff(conn, task)
    if task["predecessor_task_id"] is not None and not inputs:
        predecessor = repo.get_task(conn, task["predecessor_task_id"])
        if predecessor is not None and repo.get_rule(conn, session_id, predecessor["kind"], task["kind"]) is None:
            raise PageError(
                409, "invalid_transition", "후속 규칙이 없어 인계 자료가 없습니다. /kinds 에서 규칙을 등록하세요.",
            )
        raise PageError(
            409, "invalid_transition",
            "선행 업무의 결과와 인계 자료가 아직 준비되지 않았습니다. 선행 결과가 판정을 통과하고 규칙에 맞으면 워커가 조립합니다.",
        )
    agent = repo.get_agent(conn, selection.selected_agent_id)
    if agent is None:
        raise PageError(409, "invalid_transition", "선택된 에이전트가 더 이상 등록돼 있지 않습니다.")

    _start_execution(
        conn, task, agent, session_id=session_id, now=now, settings=settings,
        input_artifact_ids=inputs, predecessor_execution_id=predecessor_execution_id,
        target=json.loads(task["target_json"]),
    )
    _refresh_status(conn, task_id, now, settings)


def _in_unstarted_chain(conn: Connection, task: Row) -> bool:
    """워크플로우 시작 전의 노드인가 — 이때만 이미 선택된 담당을 바꿀 수 있다 (시작 후에는 워커가 선택 기록을 보고 착수)."""
    if task["chain_id"] is None:
        return False
    chain = repo.get_chain(conn, task["chain_id"])
    return chain is not None and chain["started_at"] is None


@router.post("/tasks/{task_id}/select")
def task_select(
    request: Request,
    response: Response,
    task_id: str,
    agent_id: str = Form(""),
    return_to: str = Form(""),
    session_id: str = Depends(require_session),
    conn: Connection = Depends(get_conn),
) -> RedirectResponse:
    """`needs_selection` 인 업무(또는 시작 전 워크플로우 노드)에 에이전트를 직접 지정한다. 판단은 `select_agent(mode="manual")`.
    `return_to=chain` 이면 워크플로우 화면으로 돌아간다 — 값은 열거형이며 URL 을 받지 않는다."""
    now = utc_now()
    settings = _settings(request)
    task = _own_task(conn, session_id, task_id)
    selection = repo.get_selection(conn, task_id)
    if task["finished_at"] is not None or repo.active_execution(conn, task_id) is not None:
        raise PageError(409, "invalid_transition", "실행 중이거나 마감된 업무는 선택을 바꿀 수 없습니다.")
    if selection is not None and selection.status == "selected" and not _in_unstarted_chain(conn, task):
        raise PageError(409, "invalid_transition", "이미 에이전트가 선택된 업무입니다.")
    if not agent_id.strip():
        raise PageError(422, "invalid_field", "에이전트를 지정하세요.", field="agent_id")
    _require_registered(conn, session_id, agent_id.strip())

    capability = Capability.model_validate(json.loads(task["required_capability_json"]))
    record = select_agent(
        task_id, capability, _candidates(conn, session_id), mode="manual", chosen_agent_id=agent_id.strip()
    )
    repo.save_selection(conn, record)
    agent = repo.get_agent(conn, record.selected_agent_id) if record.selected_agent_id else None
    repo.update_task_choice(
        conn, task_id, chosen_agent_id=agent_id.strip(),
        target=_target_for(_kind_of(conn, session_id, task["kind"]), agent),
    )
    _refresh_status(conn, task_id, now, settings)
    if return_to == "chain" and task["chain_id"] is not None:
        return _redirect(f"/chains/{task['chain_id']}", response)
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
        reason = "검토 승인"
        repo.update_task_status(
            conn, task_id, "완료", reason, finished_at=now, review_decision="approve", now=now
        )
        repo.release_execution(conn, execution_id, now)
        return _redirect(f"/tasks/{task_id}", response)

    if decision == "close":
        repo.update_task_status(
            conn, task_id, "실패", "검토 거절", finished_at=now, review_decision="close", now=now
        )
        repo.release_execution(conn, execution_id, now)
        return _redirect(f"/tasks/{task_id}", response)

    # request_changes — 같은 업무의 새 시도. 이전 결과와 검토 의견이 입력에 더해진다 (CONTRACT 8절).
    agent = repo.get_agent(conn, execution["agent_id"])
    if agent is None:
        raise PageError(409, "invalid_transition", "실행했던 에이전트가 더 이상 등록돼 있지 않습니다.")
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
    # 대상은 이전 시도의 요청을 잇는다 (종류 무관). 코드 수정 대상은 base_commit 만 이전 시도가 보존한 result_commit 으로.
    target = previous.target.model_dump()
    if isinstance(previous.target, CodeChangeTarget):
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


# --- 에이전트 — 러너 등록이 워크스페이스에 붙인다 (ADR-0018 결정 1) --------------


@router.get("/agents", response_class=HTMLResponse)
def agents_list(
    request: Request,
    session_id: str = Depends(require_session),
    conn: Connection = Depends(get_conn),
) -> str:
    """워크스페이스에 붙은 Agent 만. 0개면 빈 상태와 러너 연결 안내."""
    now = utc_now()
    settings = _settings(request)
    agents = [views.agent_public(a, now=now, settings=settings) for a in _session_agents(conn, session_id)]
    return _render("agents.html", **_base(request, conn, session_id, now), agents=agents)


@router.get("/agents/{agent_id}", response_class=HTMLResponse)
def agent_detail(
    request: Request,
    agent_id: str,
    session_id: str = Depends(require_session),
    conn: Connection = Depends(get_conn),
) -> str:
    """워크스페이스에 붙은 Agent 만 열린다. 그 외는 404 로 존재를 알리지 않는다."""
    now = utc_now()
    row = repo.get_agent(conn, agent_id)
    if row is None or not repo.is_session_agent(conn, session_id, agent_id):
        raise PageError(404, "not_found", f"에이전트 {agent_id}을 찾을 수 없습니다.", field="agent_id")
    agent = views.agent_public(row, now=now, settings=_settings(request), kinds=_kinds(conn, session_id))
    return _render("agent_detail.html", **_base(request, conn, session_id, now), agent=agent)


# --- 업무 종류·후속 규칙 (ADR-0009) — 워크스페이스(세션) 구성원이 등록한다. 운영자 전용이 아니다 ------


def _split_identifiers(text: str) -> list[str]:
    """쉼표·공백 구분 텍스트 → 목록. 값의 형식은 계약(`Outcome`)이 검사한다."""
    return [item for item in re.split(r"[,\s]+", text.strip()) if item]


def _validation_page_error(exc: ValidationError) -> PageError:
    """계약 모델의 첫 오류 → 422 `invalid_field`. 필드는 폼 입력 이름(최상위)이고 문구는 사람이 읽는 한 줄.
    모델 검사(`model_validator`)의 ValueError 문구는 그대로 쓴다."""
    err = exc.errors()[0]
    loc = err["loc"]
    field = str(loc[0]) if loc else None
    kind = err["type"]
    if kind == "value_error":
        message = err["msg"].removeprefix("Value error, ")
    elif kind == "string_pattern_mismatch":
        message = f"{field} 형식이 올바르지 않습니다 (허용: {err['ctx']['pattern']})."
    elif kind in ("string_too_short", "too_short"):
        message = f"{field} 을(를) 비울 수 없습니다."
    elif kind == "literal_error":
        message = f"{field} 에 허용되지 않은 값이 있습니다."
    else:
        message = f"필드 {field}: {err['msg']}"
    return PageError(422, "invalid_field", message, field=field)


def _kind_in_use_message(conn: Connection, session_id: str, kind: str) -> str:
    by_task = any(t["kind"] == kind for t in repo.list_tasks(conn, session_id))
    by_rule = any(kind in (r.from_kind, r.to_kind) for _, r in repo.list_rules(conn, session_id))
    if by_task and by_rule:
        return "이 종류를 쓰는 업무와 참조하는 후속 규칙이 있어 삭제할 수 없습니다."
    if by_rule:
        return "이 종류를 참조하는 후속 규칙이 있어 삭제할 수 없습니다. 규칙을 먼저 삭제하세요."
    return "이 종류를 쓰는 업무가 있어 삭제할 수 없습니다."


@router.get("/kinds", response_class=HTMLResponse)
def kinds_page(
    request: Request,
    session_id: str = Depends(require_session),
    conn: Connection = Depends(get_conn),
) -> str:
    """종류 목록 + 종류 등록 폼 + 규칙 목록 + 규칙 등록 폼. 규칙은 한 줄 텍스트다 — 그래프를 그리지 않는다."""
    now = utc_now()
    kinds = repo.list_kinds(conn, session_id)
    return _render(
        "kinds.html", **_base(request, conn, session_id, now),
        kinds=[views.kind_public(spec) for spec in kinds],
        rules=[views.rule_public(rule_id, rule, kinds) for rule_id, rule in repo.list_rules(conn, session_id)],
        input_kind_choices=INPUT_KIND_CHOICES,
        placement_choices=list(views.PLACEMENT_LABELS.items()),
    )


@router.post("/kinds")
def kinds_create(
    response: Response,
    kind: str = Form(""),
    label: str = Form(""),
    capability_code: str = Form(""),
    scope_key: str = Form(""),
    input_kinds: list[str] = Form([]),
    outcomes: str = Form(""),
    instructions: str = Form(""),
    session_id: str = Depends(require_session),
    conn: Connection = Depends(get_conn),
) -> RedirectResponse:
    """사용자 정의 종류. `output_kind` 는 항상 `generic_result`(내장 결과 봉투는 검증기가 딸려 있다), `builtin` 은 False.
    능력 코드를 비우면 종류 이름과 같다 (ARCHITECTURE "봉투와 내장 값")."""
    kind = kind.strip()
    if kind in BUILTIN_KIND_NAMES:  # 세션에 아직 seed 되지 않은 내장 이름도 예약어다
        raise PageError(409, "kind_exists", f"종류 {kind} 은 이미 등록돼 있습니다.", field="kind")
    try:
        spec = KindSpec.model_validate({
            "kind": kind,
            "label": label.strip(),
            "capability_code": capability_code.strip() or kind,
            "scope_key": scope_key.strip(),
            "input_kinds": input_kinds,
            "output_kind": "generic_result",
            "outcomes": _split_identifiers(outcomes),
            "instructions": instructions.strip(),
            "builtin": False,
        })
    except ValidationError as exc:
        raise _validation_page_error(exc) from None
    try:
        repo.insert_kind(conn, session_id, spec, utc_now())
    except DuplicateKind:
        raise PageError(409, "kind_exists", f"종류 {spec.kind} 은 이미 등록돼 있습니다.", field="kind") from None
    return _redirect("/kinds", response)


@router.post("/kinds/{kind}/delete")
def kinds_delete(
    response: Response,
    kind: str,
    session_id: str = Depends(require_session),
    conn: Connection = Depends(get_conn),
) -> RedirectResponse:
    try:
        repo.delete_kind(conn, session_id, kind)
    except KindProtected:
        raise PageError(409, "kind_protected", "내장 종류는 삭제할 수 없습니다.", field="kind") from None
    except KindInUse:
        raise PageError(409, "kind_in_use", _kind_in_use_message(conn, session_id, kind), field="kind") from None
    except NotFound:
        raise PageError(404, "not_found", f"종류 {kind}을 찾을 수 없습니다.", field="kind") from None
    return _redirect("/kinds", response)


@router.post("/rules")
def rules_create(
    response: Response,
    from_kind: str = Form(""),
    on_outcomes: list[str] = Form([]),
    to_kind: str = Form(""),
    handoff_kinds: list[str] = Form([]),
    placement: str = Form("same_work"),
    session_id: str = Depends(require_session),
    conn: Connection = Depends(get_conn),
) -> RedirectResponse:
    """형식은 계약(`SuccessorRule`), 등록부와의 정합(`on_outcomes ⊆ from.outcomes`·`handoff_kinds ⊇ to.input_kinds`)은
    `domain.kinds.validate_rule` 이 본다. 여기서 규칙 논리를 다시 쓰지 않는다."""
    try:
        rule = SuccessorRule.model_validate({
            "from_kind": from_kind.strip(),
            "on_outcomes": on_outcomes,
            "to_kind": to_kind.strip(),
            "handoff_kinds": handoff_kinds,
            "placement": placement,
        })
    except ValidationError as exc:
        raise _validation_page_error(exc) from None
    reason = validate_rule(repo.list_kinds(conn, session_id), rule)
    if reason is not None:
        raise PageError(422, "invalid_field", reason)
    try:
        repo.insert_rule(conn, session_id, rule, utc_now())
    except DuplicateRule:
        raise PageError(
            409, "rule_exists", f"규칙 {rule.from_kind} → {rule.to_kind} 은 이미 등록돼 있습니다.",
        ) from None
    return _redirect("/kinds", response)


@router.post("/rules/{rule_id}/delete")
def rules_delete(
    response: Response,
    rule_id: str,
    session_id: str = Depends(require_session),
    conn: Connection = Depends(get_conn),
) -> RedirectResponse:
    """내장 규칙도 삭제할 수 있다 — 규칙이 없으면 그 결과 뒤 후속은 사람이 시작한다."""
    try:
        repo.delete_rule(conn, session_id, rule_id)
    except NotFound:
        raise PageError(404, "not_found", f"규칙 {rule_id}을 찾을 수 없습니다.", field="rule_id") from None
    return _redirect("/kinds", response)


# --- 입구 (ADR-0010) — 워크스페이스(세션)가 입구 토큰을 발급·취소한다. 운영자 화면이 아니다 ------------------


def _sources_context(
    request: Request, conn: Connection, session_id: str, now: str, issued: dict[str, str] | None = None
) -> dict[str, Any]:
    """`/sources` 화면. 토큰 행에서 화면에 필요한 열만 고른다 — 해시는 넘기지 않는다. `issued` 는 발급 응답 한 번뿐."""
    settings = _settings(request)
    base_url = settings.public_url or str(request.base_url).rstrip("/")
    return {
        **_base(request, conn, session_id, now),
        "inbound_url": f"{base_url}{INBOUND_PATH}",
        "tokens": [
            {
                "token_id": t["token_id"],
                "label": t["label"],
                "created_at": t["created_at"],
                "last_used_at": t["last_used_at"],
                "revoked_at": t["revoked_at"],
            }
            for t in repo.list_source_tokens(conn, session_id)
        ],
        "issued": issued,
        "token_limit": SOURCE_TOKEN_LIMIT,
        "callback_hosts": settings.callback_hosts,
    }


@router.get("/sources", response_class=HTMLResponse)
def sources_page(
    request: Request,
    session_id: str = Depends(require_session),
    conn: Connection = Depends(get_conn),
) -> str:
    """입구 주소 · 토큰 목록 · 발급 폼 · 요청 예시 · callback 허용 목록 상태."""
    return _render("sources.html", **_sources_context(request, conn, session_id, utc_now()))


@router.post("/sources/tokens", response_class=HTMLResponse)
def sources_issue_token(
    request: Request,
    label: str = Form(""),
    session_id: str = Depends(require_session),
    conn: Connection = Depends(get_conn),
) -> str:
    """발급한 원문은 이 응답 화면에만 보인다 — 303 으로 넘기지 않는다(쿠키·쿼리·flash 에 원문을 두지 않는다).
    활성 토큰은 세션당 `SOURCE_TOKEN_LIMIT` 개까지."""
    now = utc_now()
    active = [t for t in repo.list_source_tokens(conn, session_id) if t["revoked_at"] is None]
    if len(active) >= SOURCE_TOKEN_LIMIT:
        raise PageError(
            422, "invalid_field", f"활성 토큰은 {SOURCE_TOKEN_LIMIT}개까지입니다. 하나를 취소하세요.", field="label",
        )
    token_id, token_plain = repo.issue_source_token(conn, session_id, INBOUND_SOURCE, label.strip(), now)
    issued = {"token_id": token_id, "token": token_plain}
    return _render("sources.html", **_sources_context(request, conn, session_id, now, issued))


@router.post("/sources/tokens/{token_id}/revoke")
def sources_revoke_token(
    response: Response,
    token_id: str,
    session_id: str = Depends(require_session),
    conn: Connection = Depends(get_conn),
) -> RedirectResponse:
    """같은 세션의 토큰만. 다른 세션·없음은 404 로 존재를 알리지 않는다. 재취소는 멱등."""
    try:
        repo.revoke_source_token(conn, session_id, token_id, utc_now())
    except NotFound:
        raise PageError(404, "not_found", f"토큰 {token_id}을 찾을 수 없습니다.", field="token_id") from None
    return _redirect("/sources", response)


# --- 운영자 (ADR-0005) --------------------------------------------------------------


def _operator_context(
    request: Request, conn: Connection, session_id: str, now: str, issued: dict[str, str] | None = None
) -> dict[str, Any]:
    settings = _settings(request)
    tasks = [views.task_summary(conn, t, now=now, settings=settings) for t in repo.list_tasks(conn, None)]
    kinds = _kinds(conn, session_id)
    return {
        **_base(request, conn, session_id, now),
        "agents": [views.agent_public(a, now=now, settings=settings, kinds=kinds) for a in repo.list_agents(conn)],
        "all_tasks": tasks,
        "connect_codes": [dict(c) for c in repo.list_connect_codes(conn)],
        "issued": issued,
        # 에이전트 등록 폼의 안내 — 운영자는 세션 무관이라 내장 종류만 보인다. 사용자 정의 코드는 scope 키를 직접 적는다
        "builtin_kinds": [views.kind_public(spec) for spec in BUILTIN_KINDS],
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


@router.get("/operator/github", response_class=HTMLResponse)
def operator_github_page(
    request: Request,
    session_id: str = Depends(require_session),
    conn: Connection = Depends(get_conn),
) -> str:
    """GitHub 연결(ADR-0014·0017) — 연결 전엔 [GitHub 연결] 버튼, 연결 뒤엔 저장소 카드(동기화·러너 매칭·트리거 라벨)와
    접힌 고급 설정·실제 업무 목록·열린 사람 요청. 비밀은 연결됨/없음만. 설정 쓰기는 화면 스크립트가 운영자 JSON API
    (`/github/sources…`·`/human-requests…`)로, 연결은 `/operator/github/app/new`·`/operator/github/token` 으로 한다."""
    now = utc_now()
    base = _base(request, conn, session_id, now)
    if not base["is_operator"]:
        raise PageError(403, "forbidden", "운영자 권한이 필요합니다. /operator 에서 운영자 토큰으로 여세요.")
    return _render(
        "operator_github.html", **base,
        **views.github_context(conn, session_id, now=now, settings=_settings(request), secrets=request.app.state.secrets),
    )


@router.post("/operator/github/sources/{source_id}/runner", response_class=HTMLResponse)
def operator_attach_runner(
    request: Request,
    source_id: str,
    session_id: str = Depends(require_session),
    conn: Connection = Depends(get_conn),
) -> str:
    """[러너 붙이기](ADR-0018 결정 6) — 연결 코드(1회용·10분)를 발급해 그 카드에 명령 한 줄을 넣어 그대로 렌더한다.
    리다이렉트하지 않는다 — 코드가 URL·기록에 남지 않게. 서버 주소는 `WORKFLOW_PUBLIC_URL` 또는 요청 base URL."""
    _require_operator_page(conn, session_id)
    if repo.get_github_source(conn, session_id, source_id) is None:
        raise PageError(404, "not_found", "이 워크스페이스의 저장소 연결이 아닙니다.", field="source_id")
    now = utc_now()
    code = repo.issue_connect_code(conn, now)
    row = next(c for c in repo.list_connect_codes(conn) if c["code"] == code)
    settings = _settings(request)
    server = settings.public_url or str(request.base_url).rstrip("/")
    return _render(
        "operator_github.html", **_base(request, conn, session_id, now),
        **views.github_context(conn, session_id, now=now, settings=settings, secrets=request.app.state.secrets),
        runner_issued={
            "source_id": source_id,
            "command": f"deploy/selfhost/install-runner.sh --server {server} --code {code} --repo <이 저장소를 클론한 폴더>",
            "expires_at": row["expires_at"],
        },
    )


# --- GitHub 연결 경로 (phase 11 step 7, ADR-0017, ARCHITECTURE "경로") ---------------------------------------
# state(CSRF): 쿠키 `wf_gh_state` = `<state>.<발급 epoch>.<HMAC>` — 서버 메모리·DB 에 두지 않고, 쿼리 state 와 비교하며
# 발급 뒤 1시간(manifest code 유효 시간)이 지나면 거부한다. 비밀값(개인 키·client secret·webhook secret·PAT)은
# SecretStore 에만 쓰고 응답·로그·예외 문구에 넣지 않는다.

GH_STATE_COOKIE = "wf_gh_state"
GH_STATE_PATH = "/operator/github/app"
GH_STATE_MAX_AGE = 3600
_LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")
_GITHUB_LOGIN = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})")
_TOKEN_CHARS = re.compile(r"[\x21-\x7e]{1,500}")  # PAT 는 공백 없는 ASCII — 헤더에 그대로 싣는다
_REPOSITORY_NAME = TypeAdapter(RepositoryFullName)
_APP_NAME_CHARS = string.ascii_lowercase + string.digits


def _epoch() -> float:
    return time.time()


def _state_mac(value: str, secret: str) -> str:
    return hmac.new(secret.encode(), f"gh-state:{value}".encode(), hashlib.sha256).hexdigest()


def _issue_gh_state(request: Request, response: Response) -> str:
    state = secrets.token_urlsafe(32)
    value = f"{state}.{int(_epoch())}"
    response.set_cookie(
        GH_STATE_COOKIE, f"{value}.{_state_mac(value, _settings(request).session_secret)}",
        max_age=GH_STATE_MAX_AGE, path=GH_STATE_PATH, httponly=True, samesite="lax",
    )
    return state


def _gh_state_valid(request: Request, state: str | None) -> bool:
    state_part, dot, rest = (request.cookies.get(GH_STATE_COOKIE) or "").partition(".")
    issued, dot2, mac = rest.partition(".")
    if not (state and dot and dot2 and issued.isdigit()):
        return False
    value = f"{state_part}.{issued}"
    return (
        hmac.compare_digest(mac, _state_mac(value, _settings(request).session_secret))
        and 0 <= _epoch() - int(issued) <= GH_STATE_MAX_AGE
        and hmac.compare_digest(state_part.encode(), state.encode())
    )


def _invalid_gh_state() -> PageError:
    return PageError(403, "github_state_invalid",
                     "GitHub 연결 요청을 확인할 수 없습니다(만료 또는 다른 요청). /operator/github 에서 다시 [GitHub 연결] 을 누르세요.")


def _require_operator_page(conn: Connection, session_id: str) -> None:
    session = repo.get_session(conn, session_id)
    if session is None or not session["is_operator"]:
        raise PageError(403, "forbidden", "운영자 권한이 필요합니다. /operator 에서 운영자 토큰으로 여세요.")


def _require_github_workspace(conn: Connection, session_id: str) -> None:
    if any(owner != session_id for owner in repo.github_source_sessions(conn)):
        raise PageError(409, "github_workspace_taken", "GitHub 연결은 이미 다른 운영자 워크스페이스가 쓰고 있습니다.")


def _public_base(request: Request) -> str:
    """App 이 돌아올 주소. `WORKFLOW_PUBLIC_URL` 이 없으면 요청 주소 — Host 헤더를 믿지 않도록 루프백만 받는다."""
    settings = _settings(request)
    if settings.public_url:
        return settings.public_url
    if request.url.hostname not in _LOOPBACK_HOSTS:
        raise PageError(400, "public_url_required",
                        "127.0.0.1·localhost 가 아닌 주소로 열었습니다. WORKFLOW_PUBLIC_URL 을 설정하세요.")
    return str(request.base_url).rstrip("/")


def _saved_app_slug(request: Request) -> str | None:
    """저장된 App 의 slug — 개인 키와 정보가 모두 있을 때만."""
    store = request.app.state.secrets
    if not store.exists(secret_store.GITHUB_APP_PRIVATE_KEY):
        return None
    try:
        slug = json.loads(store.read(secret_store.GITHUB_APP_INFO) or "")["slug"]
    except (ValueError, KeyError, TypeError):
        return None
    return slug if isinstance(slug, str) and slug else None


def _install_redirect(request: Request, response: Response, slug: str) -> RedirectResponse:
    state = _issue_gh_state(request, response)
    return _redirect(f"https://github.com/apps/{quote(slug, safe='')}/installations/new?state={state}", response)


@router.get("/operator/github/app/new", response_class=HTMLResponse, response_model=None)
def github_app_new(
    request: Request,
    response: Response,
    org: str | None = Query(None),
    session_id: str = Depends(require_session),
    conn: Connection = Depends(get_conn),
) -> str | RedirectResponse:
    """[GitHub 연결] — manifest 를 담은 자동 제출 폼. App 이 이미 있으면 설치 화면으로 바로 보낸다."""
    _require_operator_page(conn, session_id)
    if org is not None and not _GITHUB_LOGIN.fullmatch(org):
        raise PageError(422, "invalid_field", "GitHub 조직 이름 형식이 아닙니다.", field="org")
    slug = _saved_app_slug(request)
    if slug is not None:
        return _install_redirect(request, response, slug)
    base = _public_base(request)
    manifest = build_manifest(base, "runloom-" + "".join(secrets.choice(_APP_NAME_CHARS) for _ in range(6)))
    state = _issue_gh_state(request, response)
    owner = f"organizations/{org}/" if org else ""
    return _render(
        "operator_github_app_new.html", **_base(request, conn, session_id, utc_now()),
        action=f"https://github.com/{owner}settings/apps/new?state={state}",
        manifest_json=json.dumps(manifest, ensure_ascii=False),
    )


@router.get("/operator/github/app/callback")
def github_app_callback(
    request: Request,
    response: Response,
    code: str = Query(""),
    state: str = Query(""),
    session_id: str = Depends(require_session),
    conn: Connection = Depends(get_conn),
) -> RedirectResponse:
    """GitHub 가 App 을 만들고 돌아온 곳 — code 교환 → 비밀 저장 → 설치 화면. code·응답 본문은 싣지 않는다."""
    _require_operator_page(conn, session_id)
    if not _gh_state_valid(request, state):
        raise _invalid_gh_state()
    try:
        creds = exchange_manifest_code(code, transport=request.app.state.github_transport)
    except (ValueError, GitHubError) as exc:
        logger.warning("GitHub App manifest 교환 실패: %s", type(exc).__name__)
        raise PageError(400, "github_manifest_failed",
                        "GitHub App 을 만들지 못했습니다. /operator/github 에서 다시 [GitHub 연결] 을 누르세요.") from None
    save_credentials(request.app.state.secrets, creds, utc_now())
    return _install_redirect(request, response, creds.slug)


@router.get("/operator/github/app/setup")
def github_app_setup(
    request: Request,
    response: Response,
    installation_id: int = Query(ge=1),
    setup_action: str | None = Query(None),  # install·update — 값으로 분기하지 않는다
    state: str | None = Query(None),
    session_id: str = Depends(require_session),
    conn: Connection = Depends(get_conn),
) -> RedirectResponse:
    """설치 뒤 돌아온 곳 — App JWT 로 우리 App 의 설치인지 확인한 뒤 설치 저장소마다 소스를 맞춘다.
    state 가 없으면(GitHub 설정 화면에서 설치를 바꾸고 온 경우) 운영자 세션만으로 받는다."""
    _require_operator_page(conn, session_id)
    if state is not None and not _gh_state_valid(request, state):
        raise _invalid_gh_state()
    _require_github_workspace(conn, session_id)
    auth = load_app(request.app.state.secrets, transport=request.app.state.github_transport)
    if auth is None:
        raise PageError(409, "github_app_missing", "저장된 GitHub App 이 없습니다. /operator/github 에서 [GitHub 연결] 을 누르세요.")
    try:
        auth.get_installation(installation_id)
    except GitHubNotFound:
        raise PageError(400, "github_installation_invalid", "이 App 의 설치가 아닙니다.") from None
    except GitHubError as exc:
        logger.warning("GitHub 설치 확인 실패: %s", exc)
        raise PageError(502, "github_unavailable", "GitHub 에서 설치를 확인하지 못했습니다. 잠시 뒤 다시 여세요.") from None
    try:
        repositories = auth.list_installation_repositories(installation_id)
    except GitHubError as exc:
        logger.warning("GitHub 설치 저장소 조회 실패: %s", exc)
        raise PageError(502, "github_unavailable", "GitHub 에서 설치 저장소를 읽지 못했습니다. 잠시 뒤 다시 여세요.") from None
    github_connect.sync_installation_sources(conn, session_id, installation_id, repositories, utc_now())
    response.delete_cookie(GH_STATE_COOKIE, path=GH_STATE_PATH)
    return _redirect("/operator/github", response)


@router.post("/operator/github/token")
def github_token_connect(
    request: Request,
    response: Response,
    token: str = Form(""),
    repository_full_name: str = Form(""),
    session_id: str = Depends(require_session),
    conn: Connection = Depends(get_conn),
) -> RedirectResponse:
    """고급: 붙여 넣은 PAT 로 저장소를 볼 수 있는지 확인한 뒤 비밀 파일에 쓰고 그 저장소 소스를 만든다.
    토큰 값·길이는 응답·로그에 싣지 않는다."""
    _require_operator_page(conn, session_id)
    try:
        name = _REPOSITORY_NAME.validate_python(repository_full_name.strip())
    except ValidationError:
        raise PageError(422, "invalid_field", "저장소는 owner/name 형식이어야 합니다.",
                        field="repository_full_name") from None
    token = token.strip()
    if not _TOKEN_CHARS.fullmatch(token):
        raise PageError(400, "github_token_invalid", "GitHub 토큰을 붙여 넣으세요.", field="token")
    _require_github_workspace(conn, session_id)
    try:
        HttpGitHubClient(token, [name], transport=request.app.state.github_transport).repository_id(name)
    except (GitHubRateLimited, GitHubUnavailable) as exc:
        logger.warning("GitHub 토큰 확인 실패: %s", exc)
        raise PageError(502, "github_unavailable", "GitHub 에 연결하지 못했습니다. 잠시 뒤 다시 시도하세요.") from None
    except GitHubError:
        raise PageError(400, "github_token_invalid", f"이 토큰으로 저장소 {name} 를 볼 수 없습니다.",
                        field="token") from None
    request.app.state.secrets.write(secret_store.GITHUB_TOKEN, token)
    github_connect.ensure_token_source(conn, session_id, name, utc_now())
    return _redirect("/operator/github", response)


# --- 알림 설정 (phase 12 step 8, ADR-0018 결정 5, ARCHITECTURE "새 경로 (step 8·9)") ------------------------------
# URL 은 토큰을 담는다(Discord 웹훅 URL 자체가 비밀). 비밀 파일 `notify_webhook_url` 에만 쓰고 화면·로그·오류 문구에는 호스트만.

_TEST_MESSAGE = "[Runloom] 테스트 알림 — 이 주소로 사람 차례·PR 확인·실패 알림을 보냅니다."


def _webhook_url_savable(url: str) -> bool:
    """저장 검사 — 형식(`webhook_url_valid`) + `https`, 또는 루프백 호스트의 `http`(같은 Mac 의 n8n 등)."""
    if not notification.webhook_url_valid(url):
        return False
    parts = urlsplit(url)
    return parts.scheme == "https" or parts.hostname in _LOOPBACK_HOSTS


def _notifications_page(request: Request, conn: Connection, session_id: str,
                        test_result: dict[str, str] | None = None) -> str:
    now = utc_now()
    return _render(
        "operator_notifications.html", **_base(request, conn, session_id, now),
        **views.notifications_context(conn, session_id, secrets=request.app.state.secrets), test_result=test_result,
    )


@router.get("/operator/notifications", response_class=HTMLResponse)
def operator_notifications_page(
    request: Request,
    session_id: str = Depends(require_session),
    conn: Connection = Depends(get_conn),
) -> str:
    """알림 웹훅 설정 — 설정됨/없음·호스트, 최근 알림 20건, URL 폼·[테스트 보내기]·[삭제]."""
    _require_operator_page(conn, session_id)
    return _notifications_page(request, conn, session_id)


@router.post("/operator/notifications/webhook")
def operator_notifications_save(
    request: Request,
    response: Response,
    url: str = Form(""),
    session_id: str = Depends(require_session),
    conn: Connection = Depends(get_conn),
) -> RedirectResponse:
    """검사 뒤 비밀 파일에 저장. 형식 오류 문구에 값을 되돌려 싣지 않는다."""
    _require_operator_page(conn, session_id)
    url = url.strip()
    if not _webhook_url_savable(url):
        raise PageError(422, "invalid_field",
                        f"https:// 주소(같은 컴퓨터는 http:// 도 가능)를 {notification.WEBHOOK_URL_MAX_LENGTH}자 이하로 "
                        "입력하세요. 사용자 이름·비밀번호가 든 주소는 받지 않습니다.", field="url")
    request.app.state.secrets.write(secret_store.NOTIFY_WEBHOOK_URL, url)
    logger.info("알림 웹훅 저장: %s", notification.webhook_host(url))
    return _redirect("/operator/notifications", response)


@router.post("/operator/notifications/webhook/delete")
def operator_notifications_delete(
    request: Request,
    response: Response,
    session_id: str = Depends(require_session),
    conn: Connection = Depends(get_conn),
) -> RedirectResponse:
    _require_operator_page(conn, session_id)
    request.app.state.secrets.delete(secret_store.NOTIFY_WEBHOOK_URL)
    return _redirect("/operator/notifications", response)


@router.post("/operator/notifications/test", response_class=HTMLResponse)
def operator_notifications_test(
    request: Request,
    session_id: str = Depends(require_session),
    conn: Connection = Depends(get_conn),
) -> str:
    """저장된 URL 로 대기열 없이 한 번 보내고 결과(보냄·HTTP 상태·시간 초과·연결 오류)만 보인다."""
    _require_operator_page(conn, session_id)
    url = request.app.state.secrets.read(secret_store.NOTIFY_WEBHOOK_URL)
    if url is None:
        raise PageError(409, "notify_not_configured", "저장된 알림 주소가 없습니다. 먼저 웹훅 URL 을 저장하세요.")
    message = notification.NotificationMessage(
        event="test", task_id=None, title="테스트 알림", content=_TEST_MESSAGE, task_url=None, pr_url=None,
    )
    try:
        NotifySender(transport=request.app.state.notify_transport).post(url, notification.notification_body(url, message))
    except NotifyFailed as exc:
        reason = "시간 초과" if "Timeout" in str(exc) else str(exc)
        logger.info("알림 테스트 실패 → %s: %s", notification.webhook_host(url), reason)
        result = {"state": "failed", "text": f"보내지 못했습니다 — {reason}"}
    else:
        result = {"state": "sent", "text": "보냈습니다 — 받는 쪽에서 메시지를 확인하세요."}
    return _notifications_page(request, conn, session_id, test_result=result)


@router.get("/metrics", response_class=HTMLResponse)
def metrics_page(
    request: Request,
    since: str = Query("", alias="from"),
    until: str = Query("", alias="to"),
    group_by: str = Query(""),
    session_id: str = Depends(require_session),
    conn: Connection = Depends(get_conn),
) -> str:
    """지표 화면 — `metrics_api` 와 같은 계산. 폼은 GET 이라 빈 칸은 "지정 안 함" 이다. 기준선 가져오기 버튼은
    운영자 JSON API(`POST /operator/github/sources/{id}/baseline`)로 보낸다."""
    now = utc_now()
    base = _base(request, conn, session_id, now)
    if not base["is_operator"]:
        raise PageError(403, "forbidden", "운영자 권한이 필요합니다. /operator 에서 운영자 토큰으로 여세요.")
    for field, value in (("from", since), ("to", until)):
        if not value:
            continue
        try:
            parse_rfc3339_aware(value)
        except ValueError:
            raise PageError(422, "invalid_field", "기간은 시간대가 있는 RFC 3339 시각이어야 합니다.", field=field) from None
    if group_by not in ("", "config_revision", "folder_commit"):
        raise PageError(422, "invalid_field", "그룹은 설정 번호 또는 러너 폴더 커밋입니다.", field="group_by")
    params = {"from": since, "to": until, "group_by": group_by}
    try:
        report = metrics_api._report(conn, request, session_id, since or None, until or None, group_by or None)
    except ApiError as exc:
        raise PageError(exc.status, exc.code, exc.message, field=exc.field) from None
    query = urlencode({k: v for k, v in params.items() if v})
    return _render(
        "metrics.html", **base, **views.metrics_context(report, metrics_api._baselines(conn, session_id)),
        params=params, query=f"?{query}" if query else "",
        token_configured=bool(_settings(request).github_token),
    )


@router.post("/operator/agents")
def operator_register_agent(
    request: Request,
    response: Response,
    agent_id: str = Form(""),
    name: str = Form(""),
    owner_scope: str = Form(""),
    connection_type: str = Form(""),
    capability_code: str = Form(""),
    scope_key: str = Form(""),
    scope_value: str = Form(""),
    local_registration_id: str = Form(""),
    api_url: str = Form(""),
    credential_ref: str = Form(""),
    session_id: str = Depends(require_operator),
    conn: Connection = Depends(get_conn),
) -> RedirectResponse:
    """운영자 등록. 운영자 = 워크스페이스라 등록한 Agent 를 워크스페이스에 붙인다(ADR-0019 — 카탈로그 등록 단계 없음).
    자동 파악값은 연결 프로그램의 registrations 가 채우므로 기존 보고값은 유지한다.
    능력은 계약 패턴으로만 검사한다 — 운영자는 세션 무관이라 어느 세션의 종류와 맞는지는 그 세션의 선택 시점에 정해진다.
    `scope_key` 를 비우면 내장 종류의 코드일 때만 그 종류의 scope 키를 쓴다."""
    agent_id, name = agent_id.strip(), name.strip()
    capability_code, scope_key, scope_value = capability_code.strip(), scope_key.strip(), scope_value.strip()
    local_registration_id = local_registration_id.strip()
    api_url, credential_ref = api_url.strip(), credential_ref.strip()
    if not agent_id or not name:
        raise PageError(422, "invalid_field", "에이전트 ID 와 이름을 입력하세요.", field="agent_id")
    if owner_scope not in OWNER_SCOPES:
        raise PageError(422, "invalid_field", "소유 구분이 올바르지 않습니다.", field="owner_scope")
    if connection_type not in CONNECTION_TYPES:
        raise PageError(422, "invalid_field", "연결 유형이 올바르지 않습니다.", field="connection_type")
    if not capability_code or not scope_value:
        raise PageError(422, "invalid_field", "능력 코드와 범위 값을 입력하세요.", field="capability_code")
    if not scope_key:
        builtin = kind_for_capability(BUILTIN_KINDS, capability_code)
        if builtin is None:
            raise PageError(422, "invalid_field", "scope 키를 입력하세요.", field="scope_key")
        scope_key = builtin.scope_key
    try:
        capability = Capability.model_validate({"code": capability_code, "scope": {scope_key: scope_value}})
    except ValidationError as exc:
        raise _validation_page_error(exc) from None
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
        "capabilities": [capability.model_dump()],
        "local_registration_id": local_registration_id or None,
        "api_url": api_url or None,
        "credential_ref": credential_ref or None,
    })
    repo.register_session_agent(conn, session_id, agent_id, utc_now())  # 카탈로그 없음 — 워크스페이스에 바로 붙인다
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
