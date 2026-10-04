"""사람이 쓰는 웹 라우트 — 로그인한 멤버의 고정 워크스페이스 (ADR-0016 결정 3, ADR-0019, ADR-0021, UI_GUIDE "화면 목록", PRD 2·4절).

- 로그인은 멤버 이메일·비밀번호(쿠키 `wf_login`). 운영자 토큰은 첫 설정(`/login/setup`)·복구(`/login/recover`)만.
- `/`·`/login*`·`/invite/*`·`/reset/*`·`/logout` 밖의 라우트는 `require_member`, 역할이 필요한 곳은 `require_action(동작)` 을
  건다(ARCHITECTURE "팀 — phase 15" 라우트별 필요 동작). 워크스페이스는 자기 Task 만 보고, 남의 것은 404 로 존재를 알리지 않는다.
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
import sqlite3
import string
import time
from collections.abc import Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from sqlite3 import Connection, Row
from typing import Any
from urllib.parse import quote, urlencode, urlsplit

from fastapi import APIRouter, Depends, FastAPI, Form, Query, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from jinja2 import Environment, FileSystemLoader
from pydantic import TypeAdapter, ValidationError

from workflow.adapters import repo, secret_store, responsibility_store, internal_request_store
from workflow.adapters.errors import (
    AutostartLocked,
    DuplicateKind,
    DuplicateRule,
    EmailTaken,
    KindInUse,
    KindProtected,
    LastAdmin,
    NotFound,
    StaleCriteria,
)
from workflow.adapters.github_app import build_manifest, exchange_manifest_code, load_app, save_credentials
from workflow.adapters.github_client import (
    GitHubError,
    GitHubNotFound,
    GitHubRateLimited,
    GitHubUnavailable,
    HttpGitHubClient,
)
from workflow.adapters.jira_client import (
    HttpJiraClient,
    JiraBadRequest,
    JiraError,
    JiraForbidden,
    JiraNotFound,
    JiraUnauthorized,
)
from workflow.adapters.notify_sender import NotifyFailed, NotifySender
from workflow.contracts.internal_request import InternalRequestCreate, InternalInvestigationStart, InternalResultReturn, InternalRequestReject, InternalRequestReroute, InternalInformationQuestion, InternalInformationAnswer, InternalJudgmentRequest, InternalJudgmentResponse
from workflow.contracts.responsibility import Responsibility
from workflow.contracts.github import RepositoryFullName
from workflow.contracts.jira import JiraProjectConfig, valid_email, valid_token
from workflow.contracts.v1 import (
    ARTIFACT_KINDS,
    BUILTIN_KIND_NAMES,
    BUILTIN_KINDS,
    WORK_KEY_PREFIX,
    ArtifactMeta,
    Capability,
    CodeChangeResult,
    CodeChangeTarget,
    ExecutionRequest,
    KindSpec,
    ReviewComment,
    SuccessorRule,
    format_work_key,
    parse_rfc3339_aware,
)
from workflow.domain.completion import criteria_template, merge_criteria
from workflow.domain.composition import compose
from workflow.domain.defaults import default_run_mode
from workflow.domain import notification, start_checklist, team
from workflow.domain.delegation import DELEGATION_POLICIES, can_set_policy
from workflow.domain.execution_policy import is_triage_kind
from workflow.domain.kinds import (
    get_kind,
    kind_for_capability,
    validate_capability,
    validate_rule,
)
from workflow.domain.selection import Candidate, select_agent
from workflow.domain.task_sources import Issue
from workflow.domain.triage import AUTOSTART_MIN_HANDLED, parse_threshold
from workflow.domain.triage_criteria import CRITERIA_BODY_MAX
from workflow.domain.work_keys import work_path
from workflow.domain.work_list import ListQuery, parse_list_query
from workflow.server import github_connect, jira_connect, metrics_api, triage_runs, views, work_actions
from workflow.server.auth import (
    LOGIN_COOKIE,
    SELFHOST_SESSION_ID,
    check_origin,
    clear_login_cookies,
    current_member,
    LoggedIn,
    ensure_workspace,
    get_conn,
    login_email_key,
    require_action,
    require_member,
    set_login_cookie,
    utc_now,
)
from workflow.server.errors import ApiError, PageError
from workflow.server import internal_investigation
from workflow.server.filters import ago, duration, kind_label, kst, outcome_label
from workflow.server.settings import Settings

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


def install(app: FastAPI) -> None:
    app.include_router(router)
    _install_access_log_filter()

    @app.exception_handler(PageError)
    async def _page_error(request: Request, exc: PageError) -> HTMLResponse:
        return HTMLResponse(_render("error.html", request=request, error=exc), status_code=exc.status)


async def origin_guard(request: Request, call_next):
    """앱 미들웨어 — GET·HEAD·OPTIONS 밖의 요청이 `check_origin` 을 통과하지 못하면 403 forbidden_origin.
    폼 제출(Accept 에 text/html)은 `error.html`, 그 밖은 JSON 오류 본문. 경로는 로그에 넣지 않는다(초대·재설정 토큰)."""
    if request.method in ("GET", "HEAD", "OPTIONS") or check_origin(request, _settings(request)):
        return await call_next(request)
    logger.warning("요청 출처 거부: %s", request.method)
    error = ApiError(403, "forbidden_origin", "요청 출처를 확인할 수 없습니다.")
    if "text/html" in request.headers.get("accept", ""):
        return HTMLResponse(_render("error.html", request=request, error=error), status_code=403)
    return error.response()


# --- 렌더·리다이렉트 ----------------------------------------------------------------


def _render(name: str, **context: Any) -> str:
    return _env.get_template(name).render(**context)


def _redirect(url: str, response: Response) -> RedirectResponse:
    """303. 의존성이 sub-response 에 심은 Set-Cookie 를 옮긴다 (직접 반환한 Response 에는 합쳐지지 않는다)."""
    redirect = RedirectResponse(url, status_code=303)
    redirect.headers.raw.extend(response.headers.raw)
    return redirect


def _settings(request: Request) -> Settings:
    return request.app.state.settings


def _base(request: Request, conn: Connection, session_id: str, now: str) -> dict[str, Any]:
    """모든 화면에 들어가는 공통 컨텍스트 — 탐색·왼쪽 목록용. `allowed` = 로그인한 멤버의 역할이 할 수 있는 동작
    (템플릿은 `'manage_rules' in allowed` 처럼 본다)."""
    member = current_member(request, conn)
    return {
        "request": request,
        "now": now,
        "session_id": session_id,
        "member": member,  # 왼쪽 목록 아래 — 로그인한 멤버 표시 이름·역할
        "allowed": team.allowed_actions(member.role) if member is not None else frozenset(),
        # 사이드바 "업무" 배지 = 로그인한 멤버가 받는 사람인 `내 차례` 업무 수(빠른 필터 `my_turn` 과 같은 계산)
        "turn_count": len(repo.list_work_items(conn, session_id, recipient_member_id=member.member_id))
        if member is not None else 0,
        # 사이드바 "시작하기" — 필수 항목이 모두 끝나면 숨긴다(주소는 열림)
        "show_start": not start_checklist.required_done(repo.start_facts(conn, session_id)),
    }


def _session_agents(conn: Connection, session_id: str) -> list[Row]:
    """워크스페이스에 붙은 Agent 만(`session_agents` — 러너 등록이 붙인다). 홈·등록 폼·선택 목록·후보는 전부 이 목록을 쓴다."""
    return repo.list_session_agents(conn, session_id)


def _candidates(conn: Connection, session_id: str) -> list[Candidate]:
    return work_actions.candidates(conn, session_id)


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
    work_actions.refresh_task_status(conn, task_id, now, settings, review_decision=review_decision)


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
    return work_actions.target_for(spec, agent)


# --- 실행 생성 -----------------------------------------------------------------------


@contextmanager
def _page_errors():
    """`work_actions` 의 오류를 화면 오류(`PageError`)로 — 본문 규칙은 같다."""
    try:
        yield
    except work_actions.WorkActionError as exc:
        raise PageError(exc.status, exc.code, exc.message, field=exc.field) from None


def _start_execution(conn: Connection, task: Row, agent: Row, **kwargs: Any) -> str:
    """새 시도를 `queued` 로 만든다 — `work_actions.start_execution`."""
    with _page_errors():
        return work_actions.start_execution(conn, task, agent, **kwargs)


# --- 홈·업무 ----------------------------------------------------------------------


@router.get("/")
def root(request: Request, conn: Connection = Depends(get_conn)) -> RedirectResponse:
    """랜딩 없음 (ADR-0019) — 로그인 상태면 업무 목록 `/tasks`, 아니면 `/login` 으로 303. 세션을 만들지 않는다."""
    return RedirectResponse("/tasks" if current_member(request, conn) else "/login", status_code=303)


# --- 로그인·첫 설정·복구·초대·재설정 (ADR-0021, ARCHITECTURE "팀 — phase 15") ------------------------------
# 비밀번호·토큰 원문은 응답·로그·템플릿에 넣지 않는다 — 되돌려 채우는 값은 이메일·표시 이름뿐이다.

_TOO_MANY = "로그인 시도가 너무 많습니다. 잠시 후 다시 시도하세요."
_BAD_LOGIN = "이메일 또는 비밀번호가 올바르지 않습니다."
_BAD_RECOVER = "토큰 또는 관리자 이메일이 올바르지 않습니다."
_BAD_INVITE = "초대 링크가 없거나 만료됐습니다."
_BAD_RESET = "재설정 링크가 없거나 만료됐습니다."
_LINK_PATH = re.compile(r"(/(?:invite|reset)/)[^\s?#\"]+")


class _LinkTokenFilter(logging.Filter):
    """uvicorn 접근 로그의 `/invite/<토큰>`·`/reset/<토큰>` 을 `***` 로 가린다."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple):
            record.args = tuple(_LINK_PATH.sub(r"\1***", a) if isinstance(a, str) else a for a in record.args)
        elif isinstance(record.msg, str):
            record.msg = _LINK_PATH.sub(r"\1***", record.msg)
        return True


def _install_access_log_filter() -> None:
    access = logging.getLogger("uvicorn.access")
    if not any(isinstance(f, _LinkTokenFilter) for f in access.filters):
        access.addFilter(_LinkTokenFilter())


def _login_page(request: Request, mode: str, *, status: int = 200, error: str | None = None,
                email: str = "", display_name: str = "") -> HTMLResponse:
    """`login.html` 한 장 — mode: setup·login·recover·invite·reset·invalid."""
    body = _render("login.html", request=request, mode=mode, error=error, email=email,
                   display_name=display_name)
    return HTMLResponse(body, status_code=status)


def _link_page(request: Request, mode: str, **kwargs: Any) -> HTMLResponse:
    """초대·재설정 화면 — 링크 토큰이 다른 곳으로 새지 않게 `Referrer-Policy: no-referrer`."""
    response = _login_page(request, mode, **kwargs)
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


def _logged_in_redirect(request: Request, conn: Connection, member_id: str, url: str = "/") -> RedirectResponse:
    """새 로그인 세션을 만들고 쿠키를 실어 303."""
    settings = _settings(request)
    token = repo.create_login_session(conn, SELFHOST_SESSION_ID, member_id, now=utc_now(),
                                      days=settings.session_cookie_days)
    redirect = RedirectResponse(url, status_code=303)
    set_login_cookie(redirect, token, settings)
    return redirect


def _token_matches(request: Request, token: str) -> bool:
    return hmac.compare_digest(token.encode(), _settings(request).operator_token.encode())


def _account_problem(email: str, display_name: str | None, password: str) -> str | None:
    """이메일·표시 이름(None 이면 보지 않음)·비밀번호 규칙. 문제 문구 또는 None."""
    if team.normalize_email(email) is None:
        return "이메일 형식이 올바르지 않습니다."
    if display_name is not None and team.clean_display_name(display_name) is None:
        return "표시 이름은 1~40자로 입력하세요."
    return team.password_problem(password)


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request, conn: Connection = Depends(get_conn)) -> HTMLResponse:
    """첫 설정 전이면 관리자 계정 만들기(운영자 토큰 칸), 아니면 이메일·비밀번호."""
    return _login_page(request, "setup" if repo.needs_first_setup(conn, SELFHOST_SESSION_ID) else "login")


@router.post("/login/setup", response_model=None)
def login_setup(
    request: Request, token: str = Form(""), email: str = Form(""), display_name: str = Form(""),
    password: str = Form(""), conn: Connection = Depends(get_conn),
) -> HTMLResponse | RedirectResponse:
    """운영자 토큰으로 첫 관리자 행에 계정을 채우고 로그인한다. 첫 설정이 끝났으면 409 already_set_up."""
    if not repo.needs_first_setup(conn, SELFHOST_SESSION_ID):
        raise PageError(409, "already_set_up", "이미 관리자 계정이 있습니다. 이메일로 로그인하세요.")
    refill = {"email": email.strip(), "display_name": display_name.strip()}
    throttle = request.app.state.login_throttle
    if throttle.blocked("setup"):
        logger.warning("첫 설정 거부: 연속 실패 제한")
        return _login_page(request, "setup", status=429, error=_TOO_MANY, **refill)
    if not _token_matches(request, token):
        throttle.fail("setup")
        logger.warning("첫 설정 실패: 토큰 불일치")
        return _login_page(request, "setup", status=403, error="토큰이 올바르지 않습니다.", **refill)
    problem = _account_problem(email, display_name, password)
    if problem:
        return _login_page(request, "setup", status=422, error=problem, **refill)
    throttle.reset("setup")
    now = utc_now()
    ensure_workspace(conn, now)
    member_id = repo.ensure_first_admin(conn, SELFHOST_SESSION_ID, now=now)
    try:
        repo.set_member_credentials(conn, SELFHOST_SESSION_ID, member_id, email=email,
                                    password_hash=team.hash_password(password),
                                    display_name=team.clean_display_name(display_name), now=now)
    except EmailTaken:
        return _login_page(request, "setup", status=422, error="이미 쓰는 이메일입니다.", **refill)
    logger.info("첫 설정: 관리자 계정 생성")
    return _logged_in_redirect(request, conn, member_id)


@router.post("/login", response_model=None)
def login(
    request: Request, email: str = Form(""), password: str = Form(""), conn: Connection = Depends(get_conn),
) -> HTMLResponse | RedirectResponse:
    """이메일·비밀번호. 틀림·없는 이메일·비활성은 같은 문구 403 — 없는 경우도 더미 해시로 한 번 검증해 시간을 맞춘다."""
    if repo.needs_first_setup(conn, SELFHOST_SESSION_ID):
        return RedirectResponse("/login", status_code=303)
    refill = {"email": email.strip()}
    key = login_email_key(email)
    throttle = request.app.state.login_throttle
    if throttle.blocked(key):
        logger.warning("로그인 거부: 연속 실패 제한")
        return _login_page(request, "login", status=429, error=_TOO_MANY, **refill)
    member = repo.find_member_by_email(conn, SELFHOST_SESSION_ID, email)
    usable = member is not None and member["disabled_at"] is None and member["password_hash"] is not None
    stored = member["password_hash"] if usable else team.DUMMY_PASSWORD_HASH
    matched = len(password) <= team.PASSWORD_MAX_LENGTH and team.verify_password(password, stored)
    if not (usable and matched):
        throttle.fail(key)
        logger.warning("로그인 실패")
        return _login_page(request, "login", status=403, error=_BAD_LOGIN, **refill)
    throttle.reset(key)
    return _logged_in_redirect(request, conn, member["member_id"])


@router.get("/login/recover", response_class=HTMLResponse, response_model=None)
def recover_page(request: Request, conn: Connection = Depends(get_conn)) -> HTMLResponse | RedirectResponse:
    if repo.needs_first_setup(conn, SELFHOST_SESSION_ID):
        return RedirectResponse("/login", status_code=303)
    return _login_page(request, "recover")


@router.post("/login/recover", response_model=None)
def recover(
    request: Request, token: str = Form(""), email: str = Form(""), password: str = Form(""),
    conn: Connection = Depends(get_conn),
) -> HTMLResponse | RedirectResponse:
    """운영자 토큰 + 활성 관리자 이메일 → 새 비밀번호. 그 관리자의 로그인 세션을 전부 폐기하고 새 세션으로 로그인."""
    if repo.needs_first_setup(conn, SELFHOST_SESSION_ID):
        return RedirectResponse("/login", status_code=303)
    refill = {"email": email.strip()}
    throttle = request.app.state.login_throttle
    if throttle.blocked("recover"):
        logger.warning("복구 거부: 연속 실패 제한")
        return _login_page(request, "recover", status=429, error=_TOO_MANY, **refill)
    member = repo.find_member_by_email(conn, SELFHOST_SESSION_ID, email)
    admin = member is not None and member["role"] == "admin" and member["disabled_at"] is None
    if not (_token_matches(request, token) and admin):
        throttle.fail("recover")
        logger.warning("복구 실패")
        return _login_page(request, "recover", status=403, error=_BAD_RECOVER, **refill)
    problem = team.password_problem(password)
    if problem:
        return _login_page(request, "recover", status=422, error=problem, **refill)
    throttle.reset("recover")
    repo.set_member_credentials(conn, SELFHOST_SESSION_ID, member["member_id"], email=member["email"],
                                password_hash=team.hash_password(password), now=utc_now())
    logger.info("복구: 관리자 비밀번호 변경")
    return _logged_in_redirect(request, conn, member["member_id"])


@router.get("/invite/{token}", response_class=HTMLResponse, response_model=None)
def invite_page(request: Request, token: str, conn: Connection = Depends(get_conn)) -> HTMLResponse | RedirectResponse:
    if repo.needs_first_setup(conn, SELFHOST_SESSION_ID):
        return RedirectResponse("/login", status_code=303)
    if repo.invite_for_token(conn, token, purpose="invite", now=utc_now()) is None:
        return _link_page(request, "invalid", status=404, error=_BAD_INVITE)
    return _link_page(request, "invite")


@router.post("/invite/{token}", response_model=None)
def invite_accept(
    request: Request, token: str, email: str = Form(""), display_name: str = Form(""), password: str = Form(""),
    conn: Connection = Depends(get_conn),
) -> HTMLResponse | RedirectResponse:
    """초대로 가입(초대의 역할) → 로그인. 무효·만료·사용·취소는 같은 404 안내."""
    if repo.needs_first_setup(conn, SELFHOST_SESSION_ID):
        return RedirectResponse("/login", status_code=303)
    if repo.invite_for_token(conn, token, purpose="invite", now=utc_now()) is None:
        return _link_page(request, "invalid", status=404, error=_BAD_INVITE)
    refill = {"email": email.strip(), "display_name": display_name.strip()}
    problem = _account_problem(email, display_name, password)
    if problem:
        return _link_page(request, "invite", status=422, error=problem, **refill)
    try:
        member_id = repo.accept_invite(conn, token, email=email, display_name=team.clean_display_name(display_name),
                                       password_hash=team.hash_password(password), now=utc_now())
    except NotFound:
        return _link_page(request, "invalid", status=404, error=_BAD_INVITE)
    except EmailTaken:
        return _link_page(request, "invite", status=422, error="이미 쓰는 이메일입니다.", **refill)
    logger.info("초대 가입")
    redirect = _logged_in_redirect(request, conn, member_id)
    redirect.headers["Referrer-Policy"] = "no-referrer"
    return redirect


@router.get("/reset/{token}", response_class=HTMLResponse, response_model=None)
def reset_page(request: Request, token: str, conn: Connection = Depends(get_conn)) -> HTMLResponse | RedirectResponse:
    if repo.needs_first_setup(conn, SELFHOST_SESSION_ID):
        return RedirectResponse("/login", status_code=303)
    if repo.invite_for_token(conn, token, purpose="reset", now=utc_now()) is None:
        return _link_page(request, "invalid", status=404, error=_BAD_RESET)
    return _link_page(request, "reset")


@router.post("/reset/{token}", response_model=None)
def reset_use(
    request: Request, token: str, password: str = Form(""), conn: Connection = Depends(get_conn),
) -> HTMLResponse | RedirectResponse:
    """재설정 링크로 새 비밀번호 → 그 멤버 세션 전부 폐기 → 새 세션으로 로그인."""
    if repo.needs_first_setup(conn, SELFHOST_SESSION_ID):
        return RedirectResponse("/login", status_code=303)
    if repo.invite_for_token(conn, token, purpose="reset", now=utc_now()) is None:
        return _link_page(request, "invalid", status=404, error=_BAD_RESET)
    problem = team.password_problem(password)
    if problem:
        return _link_page(request, "reset", status=422, error=problem)
    try:
        member_id = repo.use_reset_link(conn, token, password_hash=team.hash_password(password), now=utc_now())
    except NotFound:
        return _link_page(request, "invalid", status=404, error=_BAD_RESET)
    logger.info("재설정 링크 사용")
    redirect = _logged_in_redirect(request, conn, member_id)
    redirect.headers["Referrer-Policy"] = "no-referrer"
    return redirect


@router.post("/logout")
def logout(request: Request, conn: Connection = Depends(get_conn)) -> RedirectResponse:
    """이 쿠키의 로그인 세션만 폐기하고 쿠키를 지운다. 워크스페이스 행은 그대로."""
    token = request.cookies.get(LOGIN_COOKIE)
    if token:
        repo.revoke_login_session(conn, token, now=utc_now())
    redirect = RedirectResponse("/login", status_code=303)
    clear_login_cookies(redirect)
    return redirect


@router.get("/tasks", response_class=HTMLResponse)
def home(
    request: Request,
    q: str = "",
    group: str = "",
    view: str = "",
    closed: str = "",
    open: str = "",
    repo_name: str = Query("", alias="repo"),
    member: LoggedIn = Depends(require_member),
    conn: Connection = Depends(get_conn),
) -> str:
    """업무 화면 — 한 줄 표(묶기)·보드, 빠른 필터. 쿼리는 열거형만(`parse_list_query` — 모르는 값은 기본값), 저장소는
    워크스페이스 저장소 목록의 값만."""
    session_id = member.session_id
    now = utc_now()
    query = _list_query(conn, session_id, q=q, group=group, view=view, closed=closed, open=open, repo_name=repo_name)
    base = _base(request, conn, session_id, now)
    work = repo.get_work_item_by_key(conn, session_id, query.open_key) if query.open_key is not None else None
    panel = _panel(request, conn, member, work, query, allowed=base["allowed"], now=now) if work else None
    return _render("home.html", **base,
                   **views.work_list_context(conn, session_id, member_id=member.member_id, query=query, now=now),
                   list_href=views.list_href, has_agents=bool(_session_agents(conn, session_id)), panel=panel,
                   open_key=format_work_key(query.open_key) if query.open_key is not None else None)


def _list_query(conn: Connection, session_id: str, *, q: str, group: str, view: str, closed: str, repo_name: str,
                open: str = "") -> ListQuery:
    """목록 상태 — `repo` 는 이 워크스페이스 저장소 목록과 맞을 때만 남는다(`parse_list_query`)."""
    return parse_list_query(q=q, group=group, view=view, closed=closed, open=open, repo=repo_name,
                            repos=repo.list_work_repositories(conn, session_id))


def _panel(request: Request, conn: Connection, member: LoggedIn, work: Row, query: ListQuery, *,
           allowed: frozenset[str], now: str) -> dict[str, Any]:
    """패널 조각 컨텍스트 — `views.work_panel_context` + 닫기 주소(`open` 을 뺀 목록) + 폼 숨은 입력(기본값이 아닌 목록 상태)."""
    context = views.work_panel_context(conn, member.session_id, work["work_item_id"], member_id=member.member_id,
                                       allowed=allowed, now=now, settings=_settings(request))
    state = [pair.split("=", 1) for pair in views.list_query_params(query).split("&") if pair]
    conn.execute('BEGIN')
    try:
        directory_revision = repo.get_config_revision(conn, member.session_id)
        choices = []
        for entry in responsibility_store.list_entries(conn, member.session_id):
            recipient = repo.get_member(conn, member.session_id, entry.recipient_member_id)
            choices.append({
                "value": json.dumps({key: getattr(entry, key) for key in
                                     ('system_id', 'request_kind', 'recipient_member_id')}),
                "label": f"{entry.system_id} · {entry.request_kind} · "
                         f"{recipient['display_name'] if recipient else entry.recipient_member_id}",
                "problem": responsibility_store.entry_problem(conn, member.session_id, entry),
            })
    finally:
        conn.execute('ROLLBACK')
    return {**context, "close_href": views.list_href(query), "list_state": state,
            "internal_requests": [_request_display(conn, member, r, request.app.state.store) for r in
                                  internal_request_store.list_for_work(conn, member.session_id, work['work_item_id'])],
            "directory_choices": choices, "directory_revision": directory_revision,
            "submission_key": secrets.token_hex(16)}


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
        # 선행 업무 select — 단계(Task) 단위
        "my_tasks": [
            views.task_summary(conn, t, now=now, settings=settings) for t in repo.list_tasks(conn, session_id)
        ],
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
    member: LoggedIn = Depends(require_action(team.CREATE_WORK)),
    conn: Connection = Depends(get_conn),
) -> str:
    session_id = member.session_id
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
    member: LoggedIn = Depends(require_action(team.CREATE_WORK)),
    conn: Connection = Depends(get_conn),
) -> RedirectResponse:
    session_id = member.session_id
    now = utc_now()
    settings = _settings(request)
    title, request_text = title.strip(), request_text.strip()
    scope_value, chosen_agent_id = scope_value.strip(), chosen_agent_id.strip()
    predecessor_task_id = predecessor_task_id.strip()

    if not title or not request_text:
        raise PageError(422, "invalid_field", "제목과 요청 내용을 입력하세요.", field="title")
    spec = _kind_for_code(conn, session_id, capability_code)
    if is_triage_kind(spec):  # 판단 단계는 중앙만 붙인다 (ADR-0025)
        raise PageError(422, "invalid_field", "판단 종류로는 업무를 등록할 수 없습니다.", field="capability_code")
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
        predecessor_task_id=predecessor_task_id, requested_by_member_id=member.member_id,
    )
    return _redirect(f"/tasks/{task_id}", response)


def _insert_new_task(
    conn: Connection, session_id: str, now: str, settings: Settings, *,
    title: str, request_text: str, spec: KindSpec, scope_value: str,
    selection_mode: str, chosen_agent_id: str, run_mode: str, completion_mode: str,
    criteria_extra: str, predecessor_task_id: str,
    chain_id: str | None = None, source_ref: str | None = None, prefer: Sequence[str] | None = None,
    source_type: str = "manual", requested_by_member_id: str | None = None,
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
        requested_by_member_id=requested_by_member_id,
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
    member: LoggedIn = Depends(require_member),
    conn: Connection = Depends(get_conn),
) -> str:
    """구성 결과(순서·담당·이유)와 `워크플로우 시작`. 두 번째 노드부터는 워커가 선행 완료를 보고 자동으로 잇는다."""
    session_id = member.session_id
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
    member: LoggedIn = Depends(require_member),
    conn: Connection = Depends(get_conn),
) -> str:
    """노드 목록 + 사람 단계 + 동작 영역 조각만 (`_chain_live.html`)."""
    session_id = member.session_id
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
    member: LoggedIn = Depends(require_action(team.DELEGATE)),
    conn: Connection = Depends(get_conn),
) -> RedirectResponse:
    """첫 노드를 `task_run` 과 같은 경로로 실행하고 `started_at` 을 기록한다. 이미 시작했으면 그대로 303 (멱등)."""
    session_id = member.session_id
    now = utc_now()
    chain = _own_chain(conn, session_id, chain_id)
    start_chain(conn, chain, session_id=session_id, now=now, settings=_settings(request), error=PageError)
    for work_item_id in dict.fromkeys(t["work_item_id"] for t in repo.tasks_of_chain(conn, chain_id)):
        repo.set_work_requester(conn, work_item_id, member.member_id)  # 맡긴 사람 = 체인을 시작한 멤버
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
        repo.mark_start_pending(conn, first["task_id"], now=now)  # 승인·러너를 기다리면 워커가 이어 시작한다
        _run_task(conn, first, session_id=session_id, now=now, settings=settings)
    repo.mark_chain_started(conn, chain_id, now)


_WORK_KEY = re.compile(rf"{WORK_KEY_PREFIX}-([1-9][0-9]{{0,8}})")


@router.get("/work/{key}")
def work_detail(key: str, response: Response, member: LoggedIn = Depends(require_member)) -> RedirectResponse:
    """옛 업무 상세 주소 — 키 형식이면 `/tasks?open=<key>`(없는 키는 그 화면이 안내), 아니면 404."""
    if _WORK_KEY.fullmatch(key) is None:
        raise PageError(404, "not_found", f"업무 {key}을 찾을 수 없습니다.", field="key")
    return _redirect(work_path(key), response)


@router.get("/work/{key}/panel", response_class=HTMLResponse)
def work_panel(
    request: Request,
    response: Response,
    key: str,
    q: str = "",
    group: str = "",
    view: str = "",
    closed: str = "",
    repo_name: str = Query("", alias="repo"),
    member: LoggedIn = Depends(require_member),
    conn: Connection = Depends(get_conn),
) -> str:
    """상세 패널 조각(`base.html` 없이) — 업무 화면의 JS 가 끼운다. 목록 상태 쿼리는 닫기 주소·폼 숨은 입력에만 쓴다."""
    now = utc_now()
    work = _own_work(conn, member.session_id, key)
    query = _list_query(conn, member.session_id, q=q, group=group, view=view, closed=closed, repo_name=repo_name)
    response.headers["Cache-Control"] = "no-store"
    return _render("_work_panel.html", now=now, panel=_panel(
        request, conn, member, work, query, allowed=team.allowed_actions(member.role), now=now))


def _own_work(conn: Connection, session_id: str, key: str) -> Row:
    match = _WORK_KEY.fullmatch(key)
    work = repo.get_work_item_by_key(conn, session_id, int(match.group(1))) if match else None
    if work is None:
        raise PageError(404, "not_found", f"업무 {key}을 찾을 수 없습니다.", field="key")
    return work


def _work_redirect(conn: Connection, work: Row, response: Response, q: str, group: str, view: str, closed: str,
                   repo_name: str) -> RedirectResponse:
    """`/tasks?open=<key>` — 목록 상태(숨은 입력)는 `parse_list_query` 로 정규화한 값만 붙인다."""
    query = _list_query(conn, work["session_id"], q=q, group=group, view=view, closed=closed, repo_name=repo_name)
    return _redirect(f"/tasks?{views.list_query_params(query, open_key=work['key_number'])}", response)


def _request_display(conn: Connection, member: LoggedIn, row: dict, artifact_store: Any) -> dict:
    work = repo.get_work_item(conn, member.session_id, row['work_item_id'])
    names = {}
    for role in ('requester', 'recipient', 'judgment'):
        person = repo.get_member(conn, member.session_id, row[f'{role}_member_id'])
        names[f'{role}_name'] = person['display_name'] if person else row[f'{role}_member_id']
    entry = Responsibility(**{key: row[key] for key in
                              ('system_id', 'request_kind', 'recipient_member_id', 'judgment_member_id', 'agent_id')})
    problem = responsibility_store.entry_problem(conn, member.session_id, entry)
    options = []
    agent = repo.get_agent(conn, row['agent_id']) if row['agent_id'] else None
    if agent is not None and agent['connection_type'] == 'local':
        capabilities = json.loads(agent['capabilities_json'])
        for spec in repo.list_kinds(conn, member.session_id):
            if spec.output_kind != 'generic_result' or spec.input_kinds:
                continue
            for cap in capabilities:
                if cap['code'] == spec.capability_code and spec.scope_key in cap['scope']:
                    value = cap['scope'][spec.scope_key]
                    options.append({'value': json.dumps({'kind': spec.kind, 'scope_value': value}),
                                    'label': f"{spec.label} · {spec.scope_key}={value}"})
    task = repo.get_task(conn, row['investigation_task_id']) if row['investigation_task_id'] else None
    return_execution_id = None
    return_reason = None
    if task is not None and row['returned_at'] is None:
        problem = problem or internal_request_store.investigation_problem(conn, task)
        executions = repo.list_executions(conn, task['task_id'])
        if executions:
            try:
                internal_investigation.reviewed_result(conn, artifact_store, row, executions[-1]['execution_id'])
                return_execution_id = executions[-1]['execution_id']
            except internal_request_store.RequestProblem as exc:
                return_reason = str(exc)
    basis_execution_id = return_execution_id or row['returned_execution_id']
    judgment_reason = internal_request_store.judgment_problem(conn, member.session_id, row, basis_execution_id or '')
    versions = internal_request_store.information_versions(row)
    judgment_views = []
    for judgment in row['judgments']:
        current = judgment['execution_id'] == basis_execution_id and judgment['information_versions'] == versions
        judgment_views.append({**judgment, 'is_current': current,
            'can_respond': current and judgment['judgment_id'] == row['judgments'][-1]['judgment_id']
                and judgment['judgment_member_id'] == member.member_id and judgment['decision'] is None
                and row['returned_at'] is None and not row['waiting_for_information']
                and team.RESPOND in team.allowed_actions(member.role)})
    reroute_options = []
    for candidate in responsibility_store.list_entries(conn, member.session_id):
        if (candidate.system_id, candidate.request_kind) != (row['system_id'], row['request_kind']):
            continue
        if candidate.recipient_member_id == row['recipient_member_id'] or responsibility_store.entry_problem(conn, member.session_id, candidate):
            continue
        person = repo.get_member(conn, member.session_id, candidate.recipient_member_id)
        reroute_options.append({'member_id': candidate.recipient_member_id, 'name': person['display_name']})
    phase_label = '수락됨' if row['state'] == 'accepted' else '수락 대기'
    if task:
        phase_label = '조사 작업 연결됨'
    if row['returned_at']:
        phase_label = '결과 반환됨'
    if row['resumption']:
        phase_label = '업무 재개 기록됨'
    if row['rejected_at']:
        phase_label = '재전달됨' if row['new_request_id'] else '담당 아님'
    if row['judgments'] and row['returned_at'] is None:
        latest = row['judgments'][-1]
        changed = latest['execution_id'] != basis_execution_id or latest['information_versions'] != versions
        phase_label = '새 판단 필요' if changed else (
            '판단 승인' if latest['decision'] == 'approve' else ('판단 거절' if latest['decision'] == 'reject' else '판단 응답 대기'))
    if row['waiting_for_information']:
        phase_label = '정보 답변 대기'
    recipient = row['recipient_member_id'] == member.member_id
    return {**row, **names, "work_key": format_work_key(work['key_number']), "problem": problem,
            "phase_label": phase_label, "judgment_views": judgment_views, "judgment_reason": judgment_reason,
            "judgment_submission_key": secrets.token_hex(16),
            "can_request_judgment": recipient and return_execution_id is not None and row['returned_at'] is None
                and not row['waiting_for_information'] and team.RESPOND in team.allowed_actions(member.role)
                and not any(j['is_current'] for j in judgment_views),
            "question_submission_key": secrets.token_hex(16),
            "can_ask": recipient and row['state'] == 'accepted' and row['returned_at'] is None
                       and not row['waiting_for_information'] and team.RESPOND in team.allowed_actions(member.role),
            "can_answer": row['requester_member_id'] == member.member_id and team.RESPOND in team.allowed_actions(member.role),
            "investigation_options": options, "investigation_status": task['status'] if task else None,
            "investigation_reason": task['status_reason'] if task else None,
            "can_investigate": recipient and row['state'] == 'accepted' and task is None and problem is None and not row['waiting_for_information']
                               and team.DELEGATE in team.allowed_actions(member.role),
            "return_execution_id": return_execution_id, "return_reason": return_reason,
            "can_resume": row['returned_at'] is not None and row['resumption'] is None
                          and row['requester_member_id'] == member.member_id and team.RESPOND in team.allowed_actions(member.role),
            "can_return": recipient and return_execution_id is not None and not row['waiting_for_information'] and judgment_reason is None and team.RESPOND in team.allowed_actions(member.role),
            "reroute_options": reroute_options, "directory_revision": repo.get_config_revision(conn, member.session_id),
            "can_reroute": row['requester_member_id'] == member.member_id and row['rejected_at'] is not None
                           and row['new_request_id'] is None and team.DELEGATE in team.allowed_actions(member.role),
            "can_reject": recipient and row['state'] == 'pending' and row['rejected_at'] is None
                          and team.RESPOND in team.allowed_actions(member.role),
            "can_accept": row['rejected_at'] is None and row['state'] == 'pending' and row['recipient_member_id'] == member.member_id
                          and problem is None and team.RESPOND in team.allowed_actions(member.role)}


@router.get('/requests', response_class=HTMLResponse)
def requests_page(request: Request, member: LoggedIn = Depends(require_member),
                  conn: Connection = Depends(get_conn)) -> str:
    return _render('internal_requests.html', **_base(request, conn, member.session_id, utc_now()),
                   requests=[_request_display(conn, member, row, request.app.state.store) for row in
                             internal_request_store.list_for_member(conn, member.session_id, member.member_id)])


@router.post('/work/{key}/internal-requests')
def internal_request_send(
    key: str, response: Response, selection: str = Form(...), purpose: str = Form(...),
    submission_key: str = Form(...), expected_directory_revision: int = Form(...),
    member: LoggedIn = Depends(require_action(team.DELEGATE)), conn: Connection = Depends(get_conn),
) -> RedirectResponse:
    work = _own_work(conn, member.session_id, key)
    try:
        chosen = json.loads(selection)
        if not isinstance(chosen, dict):
            raise ValueError
        body = InternalRequestCreate.model_validate({**chosen, 'purpose': purpose, 'submission_key': submission_key,
                                                    'expected_directory_revision': expected_directory_revision})
    except (ValueError, ValidationError):
        raise PageError(422, 'invalid_field', '담당 후보와 조사 목적을 확인하세요.') from None
    try:
        internal_request_store.create(conn, member.session_id, member.member_id, work['work_item_id'], body, now=utc_now())
    except internal_request_store.RequestProblem as exc:
        raise PageError(exc.status, exc.code, str(exc)) from None
    return _redirect(work_path(key), response)


@router.post('/requests/{request_id}/accept')
def internal_request_accept(
    request_id: str, response: Response, expected_revision: int = Form(...),
    member: LoggedIn = Depends(require_action(team.RESPOND)), conn: Connection = Depends(get_conn),
) -> RedirectResponse:
    try:
        internal_request_store.accept(conn, member.session_id, member.member_id, request_id, expected_revision, now=utc_now())
    except internal_request_store.RequestProblem as exc:
        raise PageError(exc.status, exc.code, str(exc)) from None
    return _redirect('/requests', response)


@router.post('/requests/{request_id}/judgments')
def internal_request_judgment(
    request: Request, request_id: str, response: Response, expected_revision: int = Form(...),
    execution_id: str = Form(...), submission_key: str = Form(...), issue: str = Form(...),
    member: LoggedIn = Depends(require_action(team.RESPOND)), conn: Connection = Depends(get_conn),
) -> RedirectResponse:
    try:
        body = InternalJudgmentRequest(expected_revision=expected_revision, execution_id=execution_id,
                                       submission_key=submission_key, issue=issue)
    except ValidationError:
        raise PageError(422, 'invalid_field', '판단할 쟁점과 조사 결과를 확인하세요.') from None
    try:
        internal_investigation.request_judgment(conn, request.app.state.store, member, request_id, body, now=utc_now())
    except internal_request_store.RequestProblem as exc:
        raise PageError(exc.status, exc.code, str(exc)) from None
    return _redirect('/requests', response)


@router.post('/requests/{request_id}/judgments/{judgment_id}/respond')
def internal_request_judgment_response(
    request: Request, request_id: str, judgment_id: str, response: Response, expected_revision: int = Form(...),
    decision: str = Form(...), reason: str = Form(...), member: LoggedIn = Depends(require_action(team.RESPOND)),
    conn: Connection = Depends(get_conn),
) -> RedirectResponse:
    try:
        body = InternalJudgmentResponse(expected_revision=expected_revision, decision=decision, reason=reason)
    except ValidationError:
        raise PageError(422, 'invalid_field', '판단과 사유를 입력하세요.') from None
    try:
        internal_investigation.respond_judgment(conn, request.app.state.store, member, request_id, judgment_id, body, now=utc_now())
    except internal_request_store.RequestProblem as exc:
        raise PageError(exc.status, exc.code, str(exc)) from None
    return _redirect('/requests', response)


@router.post('/requests/{request_id}/questions')
def internal_request_ask(
    request_id: str, response: Response, expected_revision: int = Form(...), text: str = Form(...),
    submission_key: str = Form(...), member: LoggedIn = Depends(require_action(team.RESPOND)),
    conn: Connection = Depends(get_conn),
) -> RedirectResponse:
    try:
        body = InternalInformationQuestion(expected_revision=expected_revision, text=text, submission_key=submission_key)
    except ValidationError:
        raise PageError(422, 'invalid_field', '부족한 정보를 질문으로 입력하세요.') from None
    try:
        internal_request_store.ask(conn, member.session_id, member.member_id, request_id, body, now=utc_now())
    except internal_request_store.RequestProblem as exc:
        raise PageError(exc.status, exc.code, str(exc)) from None
    return _redirect('/requests', response)


@router.post('/requests/{request_id}/questions/{question_id}/answer')
def internal_request_answer(
    request_id: str, question_id: str, response: Response, expected_revision: int = Form(...), text: str = Form(...),
    member: LoggedIn = Depends(require_action(team.RESPOND)), conn: Connection = Depends(get_conn),
) -> RedirectResponse:
    try:
        body = InternalInformationAnswer(expected_revision=expected_revision, text=text)
    except ValidationError:
        raise PageError(422, 'invalid_field', '정보 질문에 대한 답변을 입력하세요.') from None
    try:
        internal_request_store.answer(conn, member.session_id, member.member_id, request_id, question_id, body, now=utc_now())
    except internal_request_store.RequestProblem as exc:
        raise PageError(exc.status, exc.code, str(exc)) from None
    return _redirect('/requests', response)


@router.post('/requests/{request_id}/not-responsible')
def internal_request_reject(
    request_id: str, response: Response, expected_revision: int = Form(...), reason: str = Form(...),
    member: LoggedIn = Depends(require_action(team.RESPOND)), conn: Connection = Depends(get_conn),
) -> RedirectResponse:
    try:
        body = InternalRequestReject(expected_revision=expected_revision, reason=reason)
    except ValidationError:
        raise PageError(422, 'invalid_field', '담당 아님 사유를 입력하세요.') from None
    try:
        internal_request_store.reject(conn, member.session_id, member.member_id, request_id, body, now=utc_now())
    except internal_request_store.RequestProblem as exc:
        raise PageError(exc.status, exc.code, str(exc)) from None
    return _redirect('/requests', response)


@router.post('/requests/{request_id}/reroute')
def internal_request_reroute(
    request_id: str, response: Response, expected_revision: int = Form(...),
    expected_directory_revision: int = Form(...), recipient_member_id: str = Form(...),
    member: LoggedIn = Depends(require_action(team.DELEGATE)), conn: Connection = Depends(get_conn),
) -> RedirectResponse:
    try:
        body = InternalRequestReroute(expected_revision=expected_revision,
            expected_directory_revision=expected_directory_revision, recipient_member_id=recipient_member_id)
    except ValidationError:
        raise PageError(422, 'invalid_field', '새 담당 후보를 선택하세요.') from None
    try:
        internal_request_store.reroute(conn, member.session_id, member.member_id, request_id, body, now=utc_now())
    except internal_request_store.RequestProblem as exc:
        raise PageError(exc.status, exc.code, str(exc)) from None
    return _redirect('/requests', response)


@router.post('/requests/{request_id}/investigation')
def internal_request_investigate(
    request: Request, request_id: str, response: Response, investigation_selection: str = Form(...),
    expected_revision: int = Form(...), member: LoggedIn = Depends(require_action(team.DELEGATE)),
    conn: Connection = Depends(get_conn),
) -> RedirectResponse:
    try:
        selected = json.loads(investigation_selection)
        if not isinstance(selected, dict):
            raise ValueError
        body = InternalInvestigationStart.model_validate({**selected, 'expected_revision': expected_revision})
    except ValueError:
        raise PageError(422, 'invalid_field', '등록된 조사 종류와 범위를 선택하세요.') from None
    try:
        internal_investigation.start(conn, request.app, member, request_id, body, now=utc_now())
    except internal_request_store.RequestProblem as exc:
        raise PageError(exc.status, exc.code, str(exc)) from None
    return _redirect('/requests', response)


@router.post('/requests/{request_id}/return')
def internal_request_return(
    request: Request, request_id: str, response: Response, execution_id: str = Form(...),
    expected_revision: int = Form(...), member: LoggedIn = Depends(require_action(team.RESPOND)),
    conn: Connection = Depends(get_conn),
) -> RedirectResponse:
    body = InternalResultReturn(expected_revision=expected_revision, execution_id=execution_id)
    try:
        internal_investigation.return_result(conn, request.app.state.store, member, request_id, body, now=utc_now())
    except internal_request_store.RequestProblem as exc:
        raise PageError(exc.status, exc.code, str(exc)) from None
    return _redirect('/requests', response)


@router.post("/work/{key}/assignee")
def work_assignee(
    request: Request,
    response: Response,
    key: str,
    assignee: str = Form(""),
    note: str = Form(""),
    q: str = Form(""),
    group: str = Form(""),
    view: str = Form(""),
    closed: str = Form(""),
    repo_name: str = Form("", alias="repo"),
    member: LoggedIn = Depends(require_action(team.DELEGATE)),
    conn: Connection = Depends(get_conn),
) -> RedirectResponse:
    """담당 바꾸기 — 에이전트 = 맡기기, 멤버 = 배정만, `none` = 해제 (`work_actions.assign_work`)."""
    work = _own_work(conn, member.session_id, key)
    with _page_errors():
        work_actions.assign_work(conn, request.app.state.store, _settings(request), session_id=member.session_id,
                                 work_item_id=work["work_item_id"], value=assignee, member_id=member.member_id,
                                 now=utc_now(), secrets=request.app.state.secrets, note=note)
    return _work_redirect(conn, work, response, q, group, view, closed, repo_name)


@router.post("/work/{key}/priority")
def work_priority(
    response: Response,
    key: str,
    priority: str = Form(""),
    q: str = Form(""),
    group: str = Form(""),
    view: str = Form(""),
    closed: str = Form(""),
    repo_name: str = Form("", alias="repo"),
    member: LoggedIn = Depends(require_action(team.DELEGATE)),
    conn: Connection = Depends(get_conn),
) -> RedirectResponse:
    """우선순위 바꾸기 (`work_actions.set_priority`)."""
    work = _own_work(conn, member.session_id, key)
    with _page_errors():
        work_actions.set_priority(conn, session_id=member.session_id, work_item_id=work["work_item_id"],
                                  priority=priority, member_id=member.member_id, now=utc_now())
    return _work_redirect(conn, work, response, q, group, view, closed, repo_name)


@router.post("/work/{key}/direct")
def work_direct(
    response: Response,
    key: str,
    q: str = Form(""),
    group: str = Form(""),
    view: str = Form(""),
    closed: str = Form(""),
    repo_name: str = Form("", alias="repo"),
    member: LoggedIn = Depends(require_action(team.DELEGATE)),
    conn: Connection = Depends(get_conn),
) -> RedirectResponse:
    """[내 세션에서 작업] — 담당 = 누른 멤버, 업무 상태 `직접 작업 중` (`work_actions.start_direct`)."""
    work = _own_work(conn, member.session_id, key)
    with _page_errors():
        work_actions.start_direct(conn, session_id=member.session_id, work_item_id=work["work_item_id"],
                                  member_id=member.member_id, now=utc_now())
    return _work_redirect(conn, work, response, q, group, view, closed, repo_name)


@router.post("/work/{key}/direct/stop")
def work_direct_stop(
    response: Response,
    key: str,
    q: str = Form(""),
    group: str = Form(""),
    view: str = Form(""),
    closed: str = Form(""),
    repo_name: str = Form("", alias="repo"),
    member: LoggedIn = Depends(require_action(team.DELEGATE)),
    conn: Connection = Depends(get_conn),
) -> RedirectResponse:
    """[직접 작업 그만두기] — 칸만 비우고 담당은 그대로 (`work_actions.stop_direct`)."""
    work = _own_work(conn, member.session_id, key)
    with _page_errors():
        work_actions.stop_direct(conn, session_id=member.session_id, work_item_id=work["work_item_id"], now=utc_now())
    return _work_redirect(conn, work, response, q, group, view, closed, repo_name)


@router.post("/work/{key}/triage")
def work_triage(
    request: Request,
    response: Response,
    key: str,
    q: str = Form(""),
    group: str = Form(""),
    view: str = Form(""),
    closed: str = Form(""),
    repo_name: str = Form("", alias="repo"),
    member: LoggedIn = Depends(require_action(team.DELEGATE)),
    conn: Connection = Depends(get_conn),
) -> RedirectResponse:
    """[판단 받기]·[다시 판단] — 판단할 수 없으면 409 `triage_unavailable`(문구 = 이유) (`triage_runs.request_triage`)."""
    work = _own_work(conn, member.session_id, key)
    started = triage_runs.request_triage(conn, _settings(request), session_id=member.session_id,
                                         work_item_id=work["work_item_id"], trigger="manual",
                                         member_id=member.member_id, now=utc_now())
    if not started.started:
        raise PageError(409, "triage_unavailable", f"{started.reason}.")
    return _work_redirect(conn, work, response, q, group, view, closed, repo_name)


@router.post("/work/{key}/triage/accept")
def work_triage_accept(
    request: Request,
    response: Response,
    key: str,
    triage_id: str = Form(""),
    q: str = Form(""),
    group: str = Form(""),
    view: str = Form(""),
    closed: str = Form(""),
    repo_name: str = Form("", alias="repo"),
    member: LoggedIn = Depends(require_action(team.DELEGATE)),
    conn: Connection = Depends(get_conn),
) -> RedirectResponse:
    """[제안대로 맡기기] (`work_actions.accept_triage`)."""
    work = _own_work(conn, member.session_id, key)
    with _page_errors():
        work_actions.accept_triage(conn, request.app.state.store, _settings(request), session_id=member.session_id,
                                   work_item_id=work["work_item_id"], triage_id=triage_id,
                                   member_id=member.member_id, now=utc_now(), secrets=request.app.state.secrets)
    return _work_redirect(conn, work, response, q, group, view, closed, repo_name)


@router.post("/work/{key}/triage/dismiss")
def work_triage_dismiss(
    response: Response,
    key: str,
    triage_id: str = Form(""),
    q: str = Form(""),
    group: str = Form(""),
    view: str = Form(""),
    closed: str = Form(""),
    repo_name: str = Form("", alias="repo"),
    member: LoggedIn = Depends(require_action(team.DELEGATE)),
    conn: Connection = Depends(get_conn),
) -> RedirectResponse:
    """[무시] — 판단 제안을 접는다 (`work_actions.dismiss_triage`)."""
    work = _own_work(conn, member.session_id, key)
    with _page_errors():
        work_actions.dismiss_triage(conn, session_id=member.session_id, work_item_id=work["work_item_id"],
                                    triage_id=triage_id, member_id=member.member_id, now=utc_now())
    return _work_redirect(conn, work, response, q, group, view, closed, repo_name)


def _refuse_direct_work(conn: Connection, task_id: str) -> None:
    """직접 작업 중인 업무의 단계는 에이전트로 착수하지 않는다 — 담당 폼에서 에이전트에게 넘기면 시작된다."""
    if repo.is_direct_working(conn, task_id):
        raise PageError(409, "direct_work_active", work_actions.DIRECT_WORK_ACTIVE)


@router.get("/tasks/{task_id}", response_class=HTMLResponse)
def task_detail(
    request: Request,
    task_id: str,
    member: LoggedIn = Depends(require_member),
    conn: Connection = Depends(get_conn),
) -> str:
    session_id = member.session_id
    now = utc_now()
    row = _own_task(conn, session_id, task_id)
    store = request.app.state.store
    base = _base(request, conn, session_id, now)
    context = views.task_context(conn, store, row, now=now, settings=_settings(request), allowed=base["allowed"])
    viewer = views.viewer_context(conn, store, context["result"], session_id=session_id)
    return _render("task_detail.html", **base, **context, viewer=viewer)


@router.get("/tasks/{task_id}/live", response_class=HTMLResponse)
def task_live(
    request: Request,
    response: Response,
    task_id: str,
    member: LoggedIn = Depends(require_member),
    conn: Connection = Depends(get_conn),
) -> str:
    """상세의 라이브 조각만 (상태 줄·실행 블록·결과 카드·산출물 칩·동작 영역·연결 업무 칩). 세션 소유 확인은 같다."""
    session_id = member.session_id
    now = utc_now()
    row = _own_task(conn, session_id, task_id)
    store = request.app.state.store
    context = views.task_context(
        conn, store, row, now=now, settings=_settings(request), allowed=team.allowed_actions(member.role),
    )
    viewer = views.viewer_context(conn, store, context["result"], session_id=session_id)
    response.headers["Cache-Control"] = "no-store"
    return _render("_live.html", request=request, now=now, **context, viewer=viewer)


@router.post("/tasks/{task_id}/run")
def task_run(
    request: Request,
    response: Response,
    task_id: str,
    member: LoggedIn = Depends(require_action(team.DELEGATE)),
    conn: Connection = Depends(get_conn),
) -> RedirectResponse:
    """직접 실행. 활성 실행 없음 · 선택됨 · (선행이 있으면) 선행 결과 + 판정 + 인계 묶음이 조건이다 (ADR-0009 (3)).
    run_mode 와 무관하게 사용자 조작으로 시작할 수 있다. 사람이 맡긴 착수(`work_actions.start_stage`)라 소유자 승인을 거친다."""
    session_id = member.session_id
    now = utc_now()
    task = _own_task(conn, session_id, task_id)
    _refuse_direct_work(conn, task_id)
    if task["finished_at"] is not None:
        raise PageError(409, "invalid_transition", "마감된 업무는 실행할 수 없습니다.")
    # 맡긴 사람 = 직접 실행한 멤버 — 소유자 승인이 이 멤버로 묻도록 착수 전에 쓴다. 못 시작했고 승인 요청도 없으면 되돌린다
    previous = repo.work_item_of_task(conn, task_id)["requested_by_member_id"]
    repo.set_work_requester(conn, task["work_item_id"], member.member_id)
    try:
        with _page_errors():
            work_actions.start_stage(conn, request.app.state.store, _settings(request), task, session_id=session_id,
                                     now=now, strict=True, secrets=request.app.state.secrets)
    except PageError:
        if not any(r["state"] == "open" for r in repo.list_owner_approvals(conn, task_id)) and previous is not None:
            repo.set_work_requester(conn, task["work_item_id"], previous)
        raise
    return _redirect(f"/tasks/{task_id}", response)


@router.post("/tasks/{task_id}/delegate")
def task_delegate(
    request: Request,
    response: Response,
    task_id: str,
    member: LoggedIn = Depends(require_action(team.DELEGATE)),
    conn: Connection = Depends(get_conn),
) -> RedirectResponse:
    """[에이전트에게 맡기기] (ADR-0017) — GitHub 원본 Task 에 운영자 실행 지시를 한 번 기록하고(멱등) `/run` 과 같은
    착수(`work_actions.start_stage`)를 시도한다. 지금 못 시작하면 대기 사유는 상세에 남고 워커가 풀리는 대로 착수한다."""
    session_id = member.session_id
    now = utc_now()
    task = _own_task(conn, session_id, task_id)
    issue = repo.get_source_issue_by_task(conn, session_id, task_id)
    if issue is None:
        raise PageError(409, "not_delegatable", "GitHub 이슈에서 온 업무만 맡길 수 있습니다.")
    if task["finished_at"] is not None:
        raise PageError(409, "task_closed", "마감된 업무는 맡길 수 없습니다.")
    _refuse_direct_work(conn, task_id)
    repo.mark_issue_delegated(conn, session_id=session_id, source_id=issue["source_id"],
                              github_issue_id=issue["github_issue_id"], by="operator", now=now,
                              member_id=member.member_id)
    work_actions.start_stage(conn, request.app.state.store, _settings(request), repo.get_task(conn, task_id),
                             session_id=session_id, now=now, strict=False, secrets=request.app.state.secrets)
    return _redirect(f"/tasks/{task_id}", response)


def _run_task(conn: Connection, task: Row, *, session_id: str, now: str, settings: Settings) -> None:
    """`task_run` 과 `chain_start` 의 공통 경로 — `work_actions.run_task`(조건 검사, 실행 생성, 상태 갱신)."""
    with _page_errors():
        work_actions.run_task(conn, task, session_id=session_id, now=now, settings=settings)


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
    member: LoggedIn = Depends(require_action(team.DELEGATE)),
    conn: Connection = Depends(get_conn),
) -> RedirectResponse:
    """`needs_selection` 인 업무(또는 시작 전 워크플로우 노드)에 에이전트를 직접 지정한다. 판단은 `select_agent(mode="manual")`.
    `return_to=chain` 이면 워크플로우 화면으로 돌아간다 — 값은 열거형이며 URL 을 받지 않는다."""
    session_id = member.session_id
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
    member: LoggedIn = Depends(require_action(team.RESPOND)),
    conn: Connection = Depends(get_conn),
) -> RedirectResponse:
    """PRD 4절 검토 동작. 결과가 `result_ready` 이고 사용자 상태가 `확인 필요` 일 때만 받는다."""
    session_id = member.session_id
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
    problem = internal_request_store.investigation_problem(conn, task)
    if problem:
        raise PageError(409, "investigation_blocked", problem)
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
    member: LoggedIn = Depends(require_member),
    conn: Connection = Depends(get_conn),
) -> Any:
    """세션 소유 + 이 업무의 실행이 만든 산출물만. `?raw=1` 은 저장된 바이트 그대로."""
    session_id = member.session_id
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


# --- 연결 화면 (phase 16 step 6, ARCHITECTURE "업무 화면 — phase 16" 연결 화면 탭) -----------------------------
# 옛 설정 화면 7개의 본문을 탭 5개로 모은다. 탭 머리는 볼 수 있는 탭만, 권한 없는 탭을 직접 열면 403. 절별 권한은 템플릿이
# `allowed` 로 가른다. 옛 GET 은 `_to_connect` 로 303(쿼리는 버림), POST 경로는 그대로이고 성공 뒤 303 대상만 새 탭.

# (tab, 이름, 탭을 보는 데 필요한 동작 — None 이면 로그인만)
CONNECT_TABS = (
    ("sources", "가져올 곳", None),
    ("team", "팀·담당자", None),
    ("kinds", "업무 종류·규칙", None),
    ("triage", "판단", team.MANAGE_CONNECTIONS),
    ("notify", "알림", team.MANAGE_SHARED_NOTIFY),
    ("advanced", "고급", None),
)


def _to_connect(tab: str) -> RedirectResponse:
    return RedirectResponse(f"/connect?tab={tab}", status_code=303)


def _visible_tabs(allowed: frozenset[str]) -> list[dict[str, str]]:
    return [{"key": key, "label": label} for key, label, action in CONNECT_TABS if action is None or action in allowed]


def _tab_context(request: Request, conn: Connection, member: LoggedIn, tab: str, now: str,
                 allowed: frozenset[str]) -> dict[str, Any]:
    session_id = member.session_id
    settings = _settings(request)
    if tab == "sources":
        context = views.github_context(conn, session_id, now=now, settings=settings, secrets=request.app.state.secrets)
        if team.MANAGE_CONNECTIONS in allowed:
            context |= _inbound_context(request, conn, session_id)
            context |= jira_connect.connect_context(
                conn, session_id, token_saved=request.app.state.secrets.exists(secret_store.JIRA_API_TOKEN))
        return context
    if tab == "team":
        context: dict[str, Any] = {
            "agents": [
                {**views.agent_public(a, now=now, settings=settings),
                 "owner": views.runner_owner(conn, session_id, owner_id),
                 "can_set_policy": can_set_policy(member_id=member.member_id, role=member.role, owner_id=owner_id)}
                for a in _session_agents(conn, session_id)
                for owner_id in (views.agent_owner_id(conn, a),)
            ],
            "public_url_set": bool(settings.public_url),
            "responsibilities": [
                {**e.model_dump(), "problem": responsibility_store.entry_problem(conn, session_id, e)}
                for e in responsibility_store.list_entries(conn, session_id)
            ],
            "directory_revision": repo.get_config_revision(conn, session_id),
            "directory_members": [{"member_id": m["member_id"], "display_name": m["display_name"]}
                                  for m in repo.list_members(conn, session_id) if not m["disabled_at"]],
        }
        if team.MANAGE_TEAM in allowed:
            context |= views.team_context(conn, session_id, now=now)
        if team.ATTACH_RUNNER in allowed:
            context["runners"] = _runners(conn, session_id, member)
        return context
    if tab == "kinds":
        return _kinds_context(conn, session_id)
    if tab == "triage":
        return views.triage_settings_context(conn, session_id)
    if tab == "notify":
        return views.notifications_context(conn, session_id, secrets=request.app.state.secrets)
    return _advanced_context(request, conn, session_id, now, member, allowed)


def _connect_page(request: Request, conn: Connection, member: LoggedIn, tab: str, **extra: Any) -> str:
    """연결 화면 한 탭. `extra` = 발급 응답 한 번뿐인 값(`issued`·`runner_issued`)이나 테스트 결과 — POST 응답이 같은 탭을 그린다."""
    now = utc_now()
    base = _base(request, conn, member.session_id, now)
    return _render("connect.html", **{
        **base, **_tab_context(request, conn, member, tab, now, base["allowed"]),
        "tabs": _visible_tabs(base["allowed"]), "tab": tab, **extra,
    })


def _save_directory(conn: Connection, member: LoggedIn, entries: list[Responsibility], revision: int) -> RedirectResponse:
    if len(entries) > 200:
        raise PageError(422, "invalid_field", "담당 범위는 최대 200행까지 등록할 수 있습니다.", field="entries")
    try:
        responsibility_store.replace_entries(conn, member.session_id, entries, expected_revision=revision,
                                             member_id=member.member_id, now=utc_now())
    except responsibility_store.StaleDirectory:
        raise PageError(409, "stale_directory", "담당 범위 표가 변경되었습니다. 팀·담당자 화면을 다시 열어 주세요.") from None
    except ValueError as exc:
        raise PageError(422, "invalid_field", str(exc), field="entries") from None
    return _to_connect("team")


@router.post("/responsibilities/add")
def responsibility_add(
    system_id: str = Form(...), request_kind: str = Form(...), recipient_member_id: str = Form(...),
    judgment_member_id: str = Form(...), agent_id: str = Form(""), expected_revision: int = Form(...),
    member: LoggedIn = Depends(require_action(team.MANAGE_RULES)), conn: Connection = Depends(get_conn),
) -> RedirectResponse:
    try:
        entry = Responsibility(system_id=system_id, request_kind=request_kind,
                               recipient_member_id=recipient_member_id, judgment_member_id=judgment_member_id,
                               agent_id=agent_id or None)
    except ValidationError:
        raise PageError(422, "invalid_field", "시스템과 요청 유형은 소문자 식별자로 입력하세요.") from None
    entries = responsibility_store.list_entries(conn, member.session_id)
    return _save_directory(conn, member, [*entries, entry], expected_revision)


@router.post("/responsibilities/remove")
def responsibility_remove(
    position: int = Form(...), expected_revision: int = Form(...),
    member: LoggedIn = Depends(require_action(team.MANAGE_RULES)), conn: Connection = Depends(get_conn),
) -> RedirectResponse:
    entries = responsibility_store.list_entries(conn, member.session_id)
    if position < 0 or position >= len(entries):
        raise PageError(422, "invalid_field", "삭제할 담당 범위를 찾을 수 없습니다.", field="position")
    return _save_directory(conn, member, entries[:position] + entries[position + 1:], expected_revision)


@router.get("/connect", response_class=HTMLResponse)
def connect_page(
    request: Request,
    tab: str = Query(""),
    version: int | None = Query(None),
    member: LoggedIn = Depends(require_member),
    conn: Connection = Depends(get_conn),
) -> str:
    """모르는 `tab` 은 볼 수 있는 첫 탭, 아는 탭인데 권한이 없으면 403 forbidden. 판단 탭의 `version` 은 그 기준 본문
    읽기 전용 보기(없는 버전 404)."""
    visible = [t["key"] for t in _visible_tabs(team.allowed_actions(member.role))]
    if tab not in visible:
        if any(tab == key for key, _, _ in CONNECT_TABS):
            raise PageError(403, "forbidden", "관리자 권한이 필요합니다.")
        tab = visible[0]
    extra: dict[str, Any] = {}
    if tab == "triage" and version is not None:
        row = repo.get_triage_criteria(conn, member.session_id, version)
        if row is None:
            raise PageError(404, "not_found", f"판단 기준 v{version} 이 없습니다.", field="version")
        extra["criteria_view"] = {"version": row["version"], "body": row["body"]}
    return _connect_page(request, conn, member, tab, **extra)


# --- 판단 설정 (phase 19 step 8, ADR-0025, ARCHITECTURE "판단 — phase 19" 경로) -------------------------------------
# 기준 본문은 사용자 입력 — 요청문에 글로만 들어가고 화면은 자동 이스케이프로만 그린다. 모두 `manage_connections`.


@router.post("/operator/triage/criteria")
def triage_criteria_save(
    response: Response,
    body: str = Form(""),
    expected_version: int = Form(...),
    member: LoggedIn = Depends(require_action(team.MANAGE_CONNECTIONS)),
    conn: Connection = Depends(get_conn),
) -> RedirectResponse:
    """저장 = 새 버전 행. 같은 본문이면 버전을 올리지 않는다(textarea 의 CRLF 는 LF 로 맞춘다)."""
    body = body.replace("\r\n", "\n")
    if not body.strip() or len(body) > CRITERIA_BODY_MAX:
        raise PageError(422, "invalid_field", f"판단 기준은 1~{CRITERIA_BODY_MAX}자입니다.", field="body")
    try:
        repo.save_triage_criteria(conn, member.session_id, body, expected_version=expected_version,
                                  member_id=member.member_id, now=utc_now())
    except StaleCriteria:
        raise PageError(409, "stale_criteria", "다른 사람이 먼저 고쳤습니다 — 새로 고친 뒤 다시",
                        field="expected_version") from None
    return _redirect("/connect?tab=triage", response)


@router.post("/operator/triage/autostart/{kind}")
def triage_autostart_save(
    response: Response,
    kind: str,
    enabled: str = Form(""),
    threshold: str = Form(""),
    member: LoggedIn = Depends(require_action(team.MANAGE_CONNECTIONS)),
    conn: Connection = Depends(get_conn),
) -> RedirectResponse:
    """종류별 자동 시작 켬·기준값. 켜기는 사람 처리 판단 20건부터 — 서버가 같은 트랜잭션에서 다시 센다."""
    session_id = member.session_id
    if kind not in {s.kind for s in views.autostart_kinds(_kinds(conn, session_id))}:
        raise PageError(404, "not_found", "자동 시작을 둘 수 있는 업무 종류가 아닙니다.", field="kind")
    try:
        value = parse_threshold(threshold.strip())
    except ValueError as exc:
        raise PageError(422, "invalid_field", str(exc), field="threshold") from None
    try:
        repo.save_triage_autostart(conn, session_id, kind, enabled=bool(enabled), threshold=value,
                                   member_id=member.member_id, now=utc_now())
    except AutostartLocked as exc:
        raise PageError(409, "triage_autostart_locked",
                        f"판단 기록 {exc.count}/{AUTOSTART_MIN_HANDLED} — {AUTOSTART_MIN_HANDLED}건이 되면 켤 수 있습니다",
                        field="enabled") from None
    return _redirect("/connect?tab=triage", response)


# --- 에이전트 — 러너 등록이 워크스페이스에 붙인다 (ADR-0018 결정 1) --------------


@router.get("/agents")
def agents_list() -> RedirectResponse:
    """옛 에이전트 목록 — 연결 화면 팀·담당자 탭(phase 16 step 6). 쿼리는 버린다."""
    return _to_connect("team")


@router.post("/agents/{agent_id}/delegation-policy")
def agent_delegation_policy(
    response: Response,
    agent_id: str,
    policy: str = Form(""),
    member: LoggedIn = Depends(require_member),
    conn: Connection = Depends(get_conn),
) -> RedirectResponse:
    """맡기기 정책(`run` 바로 실행 · `owner_approval` 내 승인 뒤 실행) — 러너 소유자 또는 관리자(`can_set_policy`).
    `run` 으로 바꾸면 그 에이전트의 열린 승인 요청을 닫는다(`repo.set_delegation_policy`). ARCHITECTURE "사람 사이 인계 — phase 17"."""
    if repo.get_agent(conn, agent_id) is None or not repo.is_session_agent(conn, member.session_id, agent_id):
        raise PageError(404, "not_found", "에이전트를 찾을 수 없습니다.", field="agent_id")
    if not can_set_policy(member_id=member.member_id, role=member.role, owner_id=repo.agent_owner_id(conn, agent_id)):
        raise PageError(403, "forbidden", "러너 소유자나 관리자만 바꿀 수 있습니다.")
    if policy not in DELEGATION_POLICIES:
        raise PageError(422, "invalid_field", "맡기기 정책이 올바르지 않습니다.", field="policy")
    try:
        repo.set_delegation_policy(conn, member.session_id, agent_id, policy, member_id=member.member_id,
                                   now=utc_now())
    except NotFound:
        raise PageError(404, "not_found", "에이전트를 찾을 수 없습니다.", field="agent_id") from None
    return _redirect("/connect?tab=team", response)


@router.get("/agents/{agent_id}", response_class=HTMLResponse)
def agent_detail(
    request: Request,
    agent_id: str,
    member: LoggedIn = Depends(require_member),
    conn: Connection = Depends(get_conn),
) -> str:
    """워크스페이스에 붙은 Agent 만 열린다. 그 외는 404 로 존재를 알리지 않는다."""
    session_id = member.session_id
    now = utc_now()
    row = repo.get_agent(conn, agent_id)
    if row is None or not repo.is_session_agent(conn, session_id, agent_id):
        raise PageError(404, "not_found", f"에이전트 {agent_id}을 찾을 수 없습니다.", field="agent_id")
    agent = views.agent_public(row, now=now, settings=_settings(request), kinds=_kinds(conn, session_id))
    agent["owner"] = views.runner_owner(conn, session_id, views.agent_owner_id(conn, row))
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


def _kinds_context(conn: Connection, session_id: str) -> dict[str, Any]:
    """종류 목록 + 종류 등록 폼 + 규칙 목록 + 규칙 등록 폼. 규칙은 한 줄 텍스트다 — 그래프를 그리지 않는다."""
    kinds = repo.list_kinds(conn, session_id)
    return {
        "kinds": [views.kind_public(spec) for spec in kinds],
        "rules": [views.rule_public(rule_id, rule, kinds) for rule_id, rule in repo.list_rules(conn, session_id)],
        "input_kind_choices": INPUT_KIND_CHOICES,
        "placement_choices": list(views.PLACEMENT_LABELS.items()),
    }


@router.get("/kinds")
def kinds_page() -> RedirectResponse:
    """옛 종류·규칙 화면 — 연결 화면 업무 종류·규칙 탭(phase 16 step 6)."""
    return _to_connect("kinds")


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
    member: LoggedIn = Depends(require_action(team.MANAGE_RULES)),
    conn: Connection = Depends(get_conn),
) -> RedirectResponse:
    """사용자 정의 종류. `output_kind` 는 항상 `generic_result`(내장 결과 봉투는 검증기가 딸려 있다), `builtin` 은 False.
    능력 코드를 비우면 종류 이름과 같다 (ARCHITECTURE "봉투와 내장 값")."""
    session_id = member.session_id
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
        repo.insert_kind(conn, session_id, spec, utc_now(), member_id=member.member_id)
    except DuplicateKind:
        raise PageError(409, "kind_exists", f"종류 {spec.kind} 은 이미 등록돼 있습니다.", field="kind") from None
    return _redirect("/connect?tab=kinds", response)


@router.post("/kinds/{kind}/delete")
def kinds_delete(
    response: Response,
    kind: str,
    member: LoggedIn = Depends(require_action(team.MANAGE_RULES)),
    conn: Connection = Depends(get_conn),
) -> RedirectResponse:
    session_id = member.session_id
    try:
        repo.delete_kind(conn, session_id, kind, now=utc_now(), member_id=member.member_id)
    except KindProtected:
        raise PageError(409, "kind_protected", "내장 종류는 삭제할 수 없습니다.", field="kind") from None
    except KindInUse:
        raise PageError(409, "kind_in_use", _kind_in_use_message(conn, session_id, kind), field="kind") from None
    except NotFound:
        raise PageError(404, "not_found", f"종류 {kind}을 찾을 수 없습니다.", field="kind") from None
    return _redirect("/connect?tab=kinds", response)


@router.post("/rules")
def rules_create(
    response: Response,
    from_kind: str = Form(""),
    on_outcomes: list[str] = Form([]),
    to_kind: str = Form(""),
    handoff_kinds: list[str] = Form([]),
    placement: str = Form("same_work"),
    member: LoggedIn = Depends(require_action(team.MANAGE_RULES)),
    conn: Connection = Depends(get_conn),
) -> RedirectResponse:
    """형식은 계약(`SuccessorRule`), 등록부와의 정합(`on_outcomes ⊆ from.outcomes`·`handoff_kinds ⊇ to.input_kinds`)은
    `domain.kinds.validate_rule` 이 본다. 여기서 규칙 논리를 다시 쓰지 않는다."""
    session_id = member.session_id
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
        repo.insert_rule(conn, session_id, rule, utc_now(), member_id=member.member_id)
    except DuplicateRule:
        raise PageError(
            409, "rule_exists", f"규칙 {rule.from_kind} → {rule.to_kind} 은 이미 등록돼 있습니다.",
        ) from None
    return _redirect("/connect?tab=kinds", response)


@router.post("/rules/{rule_id}/delete")
def rules_delete(
    response: Response,
    rule_id: str,
    member: LoggedIn = Depends(require_action(team.MANAGE_RULES)),
    conn: Connection = Depends(get_conn),
) -> RedirectResponse:
    """내장 규칙도 삭제할 수 있다 — 규칙이 없으면 그 결과 뒤 후속은 사람이 시작한다."""
    session_id = member.session_id
    try:
        repo.delete_rule(conn, session_id, rule_id, now=utc_now(), member_id=member.member_id)
    except NotFound:
        raise PageError(404, "not_found", f"규칙 {rule_id}을 찾을 수 없습니다.", field="rule_id") from None
    return _redirect("/connect?tab=kinds", response)


# --- 입구 (ADR-0010) — 워크스페이스(세션)가 입구 토큰을 발급·취소한다. 운영자 화면이 아니다 ------------------


def _inbound_context(request: Request, conn: Connection, session_id: str) -> dict[str, Any]:
    """가져올 곳 탭의 n8n 입구 절(옛 `/sources`). 토큰 행에서 화면에 필요한 열만 고른다 — 해시는 넘기지 않는다.
    발급 원문(`issued`)은 발급 응답 한 번뿐이라 호출한 쪽이 따로 싣는다."""
    settings = _settings(request)
    base_url = settings.public_url or str(request.base_url).rstrip("/")
    return {
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
        "token_limit": SOURCE_TOKEN_LIMIT,
        "callback_hosts": settings.callback_hosts,
    }


@router.get("/sources")
def sources_page() -> RedirectResponse:
    """옛 입구 화면 — 연결 화면 가져올 곳 탭(phase 16 step 6)."""
    return _to_connect("sources")


@router.post("/sources/tokens", response_class=HTMLResponse)
def sources_issue_token(
    request: Request,
    label: str = Form(""),
    member: LoggedIn = Depends(require_action(team.MANAGE_CONNECTIONS)),
    conn: Connection = Depends(get_conn),
) -> str:
    """발급한 원문은 이 응답 화면에만 보인다 — 303 으로 넘기지 않는다(쿠키·쿼리·flash 에 원문을 두지 않는다).
    활성 토큰은 세션당 `SOURCE_TOKEN_LIMIT` 개까지."""
    session_id = member.session_id
    now = utc_now()
    active = [t for t in repo.list_source_tokens(conn, session_id) if t["revoked_at"] is None]
    if len(active) >= SOURCE_TOKEN_LIMIT:
        raise PageError(
            422, "invalid_field", f"활성 토큰은 {SOURCE_TOKEN_LIMIT}개까지입니다. 하나를 취소하세요.", field="label",
        )
    token_id, token_plain = repo.issue_source_token(conn, session_id, INBOUND_SOURCE, label.strip(), now)
    return _connect_page(request, conn, member, "sources", issued={"token_id": token_id, "token": token_plain})


@router.post("/sources/tokens/{token_id}/revoke")
def sources_revoke_token(
    response: Response,
    token_id: str,
    member: LoggedIn = Depends(require_action(team.MANAGE_CONNECTIONS)),
    conn: Connection = Depends(get_conn),
) -> RedirectResponse:
    """같은 세션의 토큰만. 다른 세션·없음은 404 로 존재를 알리지 않는다. 재취소는 멱등."""
    session_id = member.session_id
    try:
        repo.revoke_source_token(conn, session_id, token_id, utc_now())
    except NotFound:
        raise PageError(404, "not_found", f"토큰 {token_id}을 찾을 수 없습니다.", field="token_id") from None
    return _redirect("/connect?tab=sources", response)


# --- 운영자 (ADR-0005) --------------------------------------------------------------


def _may_remove_runner(member: LoggedIn, owner_member_id: str | None) -> bool:
    """러너 해제·코드 취소·러너 에이전트 삭제 — 소유자(발급자) 본인(`attach_runner`) 또는 `remove_any_runner`.
    소유자 없음(v11 이전)은 관리자만."""
    if team.can(member.role, team.REMOVE_ANY_RUNNER):
        return True
    return owner_member_id is not None and owner_member_id == member.member_id \
        and team.can(member.role, team.ATTACH_RUNNER)


def _forbid_runner_removal(owner_member_id: str | None) -> PageError:
    if owner_member_id is None:
        return PageError(403, "forbidden", "관리자 권한이 필요합니다.")
    return PageError(403, "forbidden", "러너 소유자나 관리자만 할 수 있습니다.")


def _runners(conn: Connection, session_id: str, member: LoggedIn) -> list[dict[str, Any]]:
    """팀·담당자 탭의 러너 목록(옛 `/operator`) — 해제는 소유자 본인 또는 `remove_any_runner`."""
    return [
        {
            **{k: c[k] for k in ("connector_id", "created_at", "last_seen_at", "revoked_at")},
            "owner": views.runner_owner(conn, session_id, c["owner_member_id"]),
            "can_revoke": c["revoked_at"] is None and _may_remove_runner(member, c["owner_member_id"]),
        }
        for c in repo.list_connectors(conn)
    ]


def _advanced_context(
    request: Request, conn: Connection, session_id: str, now: str, member: LoggedIn, allowed: frozenset[str]
) -> dict[str, Any]:
    """고급 탭(옛 `/operator` 의 연결 코드·에이전트 등록). 발급한 코드(`issued`)는 호출한 쪽이 싣는다."""
    settings = _settings(request)
    kinds = _kinds(conn, session_id)
    # 연결 코드 목록은 관리자면 전부, 멤버면 자기가 발급한 것만(러너 소유자 — phase 15 step 10)
    codes = repo.list_connect_codes(
        conn, issued_by_member_id=None if team.REMOVE_ANY_RUNNER in allowed else member.member_id
    )
    agents = []
    for a in repo.list_agents(conn):
        owner_id = views.agent_owner_id(conn, a)
        agents.append({
            **views.agent_public(a, now=now, settings=settings, kinds=kinds),
            "owner": views.runner_owner(conn, session_id, owner_id),
            "can_delete": team.MANAGE_CONNECTIONS in allowed or _may_remove_runner(member, owner_id),
        })
    return {
        "agents": agents,
        "connect_codes": [
            {**dict(c), "can_revoke": _may_remove_runner(member, c["issued_by_member_id"])} for c in codes
        ],
        # 에이전트 등록 폼의 안내 — 운영자는 세션 무관이라 내장 종류만 보인다. 사용자 정의 코드는 scope 키를 직접 적는다
        "builtin_kinds": [views.kind_public(spec) for spec in BUILTIN_KINDS],
        "owner_scopes": OWNER_SCOPES,
        "connection_types": CONNECTION_TYPES,
    }


@router.get("/operator")
def operator_page() -> RedirectResponse:
    """옛 운영자 화면 — 연결 화면 고급 탭(phase 16 step 6). 러너 목록은 팀·담당자 탭으로 옮겼다."""
    return _to_connect("advanced")


@router.get("/operator/github")
def operator_github_page() -> RedirectResponse:
    """옛 GitHub 연결 화면 — 연결 화면 가져올 곳 탭(phase 16 step 6)."""
    return _to_connect("sources")


@router.post("/operator/github/sources/{source_id}/runner", response_class=HTMLResponse)
def operator_attach_runner(
    request: Request,
    source_id: str,
    member: LoggedIn = Depends(require_action(team.ATTACH_RUNNER)),
    conn: Connection = Depends(get_conn),
) -> str:
    """[러너 붙이기](ADR-0018 결정 6) — 연결 코드(1회용·10분)를 발급해 그 카드에 명령 한 줄을 넣어 그대로 렌더한다.
    리다이렉트하지 않는다 — 코드가 URL·기록에 남지 않게. 서버 주소는 `WORKFLOW_PUBLIC_URL` 또는 요청 base URL."""
    session_id = member.session_id
    if repo.get_github_source(conn, session_id, source_id) is None:
        raise PageError(404, "not_found", "이 워크스페이스의 저장소 연결이 아닙니다.", field="source_id")
    now = utc_now()
    code = repo.issue_connect_code(conn, now, issued_by_member_id=member.member_id)
    row = next(c for c in repo.list_connect_codes(conn) if c["code"] == code)
    server = _settings(request).public_url or str(request.base_url).rstrip("/")
    return _connect_page(
        request, conn, member, "sources",
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
    member: LoggedIn = Depends(require_action(team.MANAGE_CONNECTIONS)),
    conn: Connection = Depends(get_conn),
) -> str | RedirectResponse:
    """[GitHub 연결] — manifest 를 담은 자동 제출 폼. App 이 이미 있으면 설치 화면으로 바로 보낸다."""
    session_id = member.session_id
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
    member: LoggedIn = Depends(require_action(team.MANAGE_CONNECTIONS)),
    conn: Connection = Depends(get_conn),
) -> RedirectResponse:
    """GitHub 가 App 을 만들고 돌아온 곳 — code 교환 → 비밀 저장 → 설치 화면. code·응답 본문은 싣지 않는다."""
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
    member: LoggedIn = Depends(require_action(team.MANAGE_CONNECTIONS)),
    conn: Connection = Depends(get_conn),
) -> RedirectResponse:
    """설치 뒤 돌아온 곳 — App JWT 로 우리 App 의 설치인지 확인한 뒤 설치 저장소마다 소스를 맞춘다.
    state 가 없으면(GitHub 설정 화면에서 설치를 바꾸고 온 경우) 운영자 세션만으로 받는다."""
    session_id = member.session_id
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
    github_connect.sync_installation_sources(conn, session_id, installation_id, repositories, utc_now(),
                                             member_id=member.member_id)
    response.delete_cookie(GH_STATE_COOKIE, path=GH_STATE_PATH)
    return _redirect("/connect?tab=sources", response)


@router.post("/operator/github/token")
def github_token_connect(
    request: Request,
    response: Response,
    token: str = Form(""),
    repository_full_name: str = Form(""),
    member: LoggedIn = Depends(require_action(team.MANAGE_CONNECTIONS)),
    conn: Connection = Depends(get_conn),
) -> RedirectResponse:
    """고급: 붙여 넣은 PAT 로 저장소를 볼 수 있는지 확인한 뒤 비밀 파일에 쓰고 그 저장소 소스를 만든다.
    토큰 값·길이는 응답·로그에 싣지 않는다."""
    session_id = member.session_id
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
    github_connect.ensure_token_source(conn, session_id, name, utc_now(), member_id=member.member_id)
    return _redirect("/connect?tab=sources", response)


# --- Jira 연결 (phase 18 step 4, ADR-0024, ARCHITECTURE "Jira 소스 — phase 18" 경로) ----------------------------------
# 모두 `manage_connections`. 토큰은 비밀 파일 `jira_api_token` 에만 — 값·길이를 응답·로그·오류 문구에 싣지 않는다.
# 화면은 Jira 를 부르지 않는다(후보는 저장한 `choices_json`). 검색·추가·새로 고침만 사람이 누를 때 부른다.

JIRA_QUERY_MAX = 100
_JIRA_PROJECT_ID = re.compile(r"[1-9][0-9]*")
_JIRA_START_MODES = ("from_now", "all_open")


def _jira_page_error(exc: JiraError, conn: Connection | None = None, session_id: str = "") -> PageError:
    """Jira 오류 → 화면 오류(ARCHITECTURE 오류 분류 "연결 화면" 칸). 저장한 연결로 부른 401 이면 `auth_failed_at` 을 쓴다."""
    if isinstance(exc, JiraUnauthorized):
        if conn is not None:
            repo.mark_jira_auth_failed(conn, session_id, now=utc_now())
        return PageError(400, "jira_auth_failed", "이메일·토큰이 맞지 않습니다.", field="token")
    if isinstance(exc, JiraForbidden):
        return PageError(400, "jira_forbidden",
                         "권한(스코프)이 부족합니다 — read:jira-work·write:jira-work·read:jira-user")
    if isinstance(exc, JiraNotFound):
        return PageError(404, "not_found", "Jira 에서 찾을 수 없습니다.")
    if isinstance(exc, JiraBadRequest):
        return PageError(422, "invalid_field", f"Jira 가 요청을 거부했습니다 — {exc.message}")
    logger.warning("Jira 호출 실패: %s", exc)  # 메시지는 `메서드 경로: 상태` 뿐(토큰 없음)
    return PageError(502, "jira_unavailable", "Jira 사이트에 닿지 못했습니다. 잠시 뒤 다시 시도하세요.")


def _jira_client(request: Request, conn: Connection, session_id: str) -> HttpJiraClient:
    """저장한 연결 + 비밀 파일 토큰으로 만든 클라이언트. 끊겼거나 토큰 파일이 없으면 409 jira_not_connected."""
    row = repo.get_jira_connection(conn, session_id)
    token = request.app.state.secrets.read(secret_store.JIRA_API_TOKEN)
    if row is None or row["disconnected_at"] is not None or token is None:
        raise PageError(409, "jira_not_connected", "Jira 를 먼저 연결하세요.")
    return HttpJiraClient(jira_connect.api_base_url(row), row["email"], token,
                          transport=request.app.state.jira_transport)


def _jira_project_or_404(conn: Connection, session_id: str, source_id: str) -> JiraProjectConfig:
    project = repo.get_jira_project(conn, session_id, source_id)
    if project is None:
        raise PageError(404, "not_found", "Jira 프로젝트 설정을 찾을 수 없습니다.", field="source_id")
    return project


@router.post("/operator/jira/connect")
def jira_connect_route(
    request: Request,
    response: Response,
    site_url: str = Form(""),
    email: str = Form(""),
    token: str = Form(""),
    member: LoggedIn = Depends(require_action(team.MANAGE_CONNECTIONS)),
    conn: Connection = Depends(get_conn),
) -> RedirectResponse:
    """사이트·이메일·토큰 확인(tenant_info → myself, 게이트웨이 → 사이트) 뒤 토큰은 비밀 파일, 공개 정보는 DB.
    이미 프로젝트를 추가했는데 다른 사이트(cloudId)면 저장하지 않는다(409)."""
    session_id = member.session_id
    try:
        site = jira_connect.normalize_site(site_url)
    except ValueError:
        raise PageError(422, "invalid_field", "https://<이름>.atlassian.net 형식만 받습니다.", field="site_url") from None
    email = email.strip()
    if not valid_email(email):
        raise PageError(422, "invalid_field", "이메일 형식이 아닙니다.", field="email")
    token = token.strip()
    if not valid_token(token):
        raise PageError(422, "invalid_field", "Jira API 토큰을 붙여 넣으세요.", field="token")
    try:
        facts = jira_connect.verify(site, email, token, transport=request.app.state.jira_transport)
    except JiraError as exc:
        raise _jira_page_error(exc) from None
    current = repo.get_jira_connection(conn, session_id)
    if current is not None and current["cloud_id"] != facts.cloud_id and repo.list_jira_projects(conn, session_id):
        raise PageError(409, "jira_site_mismatch", "이미 설정한 프로젝트가 다른 사이트의 것입니다.", field="site_url")
    request.app.state.secrets.write(secret_store.JIRA_API_TOKEN, token)
    repo.save_jira_connection(conn, facts, session_id=session_id, now=utc_now())
    return _redirect("/connect?tab=sources", response)


@router.get("/operator/jira/projects", response_class=HTMLResponse)
def jira_project_search(
    request: Request,
    q: str = Query(""),
    member: LoggedIn = Depends(require_action(team.MANAGE_CONNECTIONS)),
    conn: Connection = Depends(get_conn),
) -> str:
    """프로젝트 찾기 — 결과 줄을 연결 화면에 그린다(줄마다 연결 저장소·시작점 고르기 + [추가])."""
    session_id = member.session_id
    q = q.strip()
    if len(q) > JIRA_QUERY_MAX:
        raise PageError(422, "invalid_field", f"검색어는 {JIRA_QUERY_MAX}자까지입니다.", field="q")
    client = _jira_client(request, conn, session_id)
    try:
        found = client.search_projects(q)
    except JiraError as exc:
        raise _jira_page_error(exc, conn, session_id) from None
    added = {p.project_id for p in repo.list_jira_projects(conn, session_id)}
    return _connect_page(request, conn, member, "sources", jira_query=q, jira_found=[
        {**ref.model_dump(mode="json"), "added": ref.project_id in added} for ref in found
    ])


@router.post("/operator/jira/projects")
def jira_project_add(
    request: Request,
    response: Response,
    project_id: str = Form(""),
    github_source_id: str = Form(""),
    start_mode: str = Form(""),
    member: LoggedIn = Depends(require_action(team.MANAGE_CONNECTIONS)),
    conn: Connection = Depends(get_conn),
) -> RedirectResponse:
    """프로젝트 추가 — Jira 에서 프로젝트·후보(이슈 유형·상태)를 받아 설정 행을 만든다. 시작점은 여기서만 고른다."""
    session_id = member.session_id
    client = _jira_client(request, conn, session_id)
    sources = {s.source_id for s in repo.list_github_sources(conn, session_id)}
    if not sources:
        raise PageError(409, "github_source_required", "먼저 GitHub 저장소를 연결하세요.", field="github_source_id")
    if github_source_id not in sources:
        raise PageError(422, "invalid_field", "이 워크스페이스에 연결한 GitHub 저장소를 고르세요.", field="github_source_id")
    if start_mode not in _JIRA_START_MODES:
        raise PageError(422, "invalid_field", "시작점이 올바르지 않습니다.", field="start_mode")
    if not _JIRA_PROJECT_ID.fullmatch(project_id):
        raise PageError(422, "invalid_field", "프로젝트를 목록에서 고르세요.", field="project_id")
    if any(p.project_id == project_id for p in repo.list_jira_projects(conn, session_id)):
        raise PageError(409, "jira_project_exists", "이미 추가한 프로젝트입니다.", field="project_id")
    try:
        ref = client.get_project(project_id)
        choices = client.project_choices(ref.key)
    except JiraError as exc:
        raise _jira_page_error(exc, conn, session_id) from None
    try:
        repo.add_jira_project(conn, session_id=session_id, ref=ref, github_source_id=github_source_id,
                              start_mode=start_mode, choices=choices, now=utc_now())
    except sqlite3.IntegrityError:
        raise PageError(409, "jira_project_exists", "이미 추가한 프로젝트입니다.", field="project_id") from None
    return _redirect("/connect?tab=sources", response)


@router.post("/operator/jira/projects/{source_id}")
def jira_project_save(
    response: Response,
    source_id: str,
    github_source_id: str = Form(""),
    issue_types: list[str] = Form([]),
    status_on_start: str = Form(""),
    status_on_review: str = Form(""),
    status_on_done: str = Form(""),
    followup_issue_type: str = Form(""),
    enabled: str = Form(""),
    member: LoggedIn = Depends(require_action(team.MANAGE_CONNECTIONS)),
    conn: Connection = Depends(get_conn),
) -> RedirectResponse:
    """프로젝트 설정 저장. 이름은 저장한 후보와 대조(대소문자 무시, 후보 표기로 저장), 빈 값 = 옮기지 않음/만들지 않음.
    Jira 를 부르지 않는다."""
    session_id = member.session_id
    _jira_project_or_404(conn, session_id, source_id)
    if github_source_id not in {s.source_id for s in repo.list_github_sources(conn, session_id)}:
        raise PageError(422, "invalid_field", "이 워크스페이스에 연결한 GitHub 저장소를 고르세요.", field="github_source_id")
    choices = repo.get_jira_choices(conn, session_id, source_id)
    try:
        settings = jira_connect.project_settings(
            choices, issue_types=issue_types, status_on_start=status_on_start, status_on_review=status_on_review,
            status_on_done=status_on_done, followup_issue_type=followup_issue_type,
        )
    except jira_connect.InvalidSetting as exc:
        raise PageError(422, "invalid_field", str(exc), field=exc.field) from None
    repo.update_jira_project(conn, session_id, source_id, now=utc_now(), github_source_id=github_source_id,
                             enabled=bool(enabled), **settings)
    return _redirect("/connect?tab=sources", response)


@router.post("/operator/jira/projects/{source_id}/refresh")
def jira_project_refresh(
    request: Request,
    response: Response,
    source_id: str,
    member: LoggedIn = Depends(require_action(team.MANAGE_CONNECTIONS)),
    conn: Connection = Depends(get_conn),
) -> RedirectResponse:
    """후보(이슈 유형·상태 이름) 다시 받기. 이미 고른 이름은 그대로 둔다."""
    session_id = member.session_id
    project = _jira_project_or_404(conn, session_id, source_id)
    client = _jira_client(request, conn, session_id)
    try:
        choices = client.project_choices(project.project_key)
    except JiraError as exc:
        raise _jira_page_error(exc, conn, session_id) from None
    repo.set_jira_choices(conn, session_id, source_id, choices, now=utc_now())
    return _redirect("/connect?tab=sources", response)


@router.post("/operator/jira/disconnect")
def jira_disconnect(
    request: Request,
    response: Response,
    member: LoggedIn = Depends(require_action(team.MANAGE_CONNECTIONS)),
    conn: Connection = Depends(get_conn),
) -> RedirectResponse:
    """끊기 — 토큰 파일 삭제 + `disconnected_at`. 프로젝트 설정·업무는 남는다(다시 연결하면 이어간다)."""
    request.app.state.secrets.delete(secret_store.JIRA_API_TOKEN)
    repo.disconnect_jira(conn, member.session_id, now=utc_now())
    return _redirect("/connect?tab=sources", response)


# --- 알림 설정 (phase 12 step 8, ADR-0018 결정 5, ARCHITECTURE "새 경로 (step 8·9)") ------------------------------
# URL 은 토큰을 담는다(Discord 웹훅 URL 자체가 비밀). 비밀 파일 `notify_webhook_url` 에만 쓰고 화면·로그·오류 문구에는 호스트만.

_TEST_MESSAGE = "[Runloom] 테스트 알림 — 이 주소로 사람 차례·PR 확인·실패 알림을 보냅니다."


def _webhook_url_savable(url: str) -> bool:
    """저장 검사 — 형식(`webhook_url_valid`) + `https`, 또는 루프백 호스트의 `http`(같은 Mac 의 n8n 등)."""
    if not notification.webhook_url_valid(url):
        return False
    parts = urlsplit(url)
    return parts.scheme == "https" or parts.hostname in _LOOPBACK_HOSTS


def _savable_webhook_url(url: str) -> str:
    """앞뒤 공백을 떼고 저장 검사. 틀리면 422 — 문구에 값을 되돌려 싣지 않는다."""
    url = url.strip()
    if not _webhook_url_savable(url):
        raise PageError(422, "invalid_field",
                        f"https:// 주소(같은 컴퓨터는 http:// 도 가능)를 {notification.WEBHOOK_URL_MAX_LENGTH}자 이하로 "
                        "입력하세요. 사용자 이름·비밀번호가 든 주소는 받지 않습니다.", field="url")
    return url


def _send_test_notification(request: Request, url: str | None) -> dict[str, str]:
    """저장된 URL 로 대기열 없이 한 번 보내고 결과(보냄·HTTP 상태·시간 초과·연결 오류)만 돌려준다. 없으면 409."""
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
        return {"state": "failed", "text": f"보내지 못했습니다 — {reason}"}
    return {"state": "sent", "text": "보냈습니다 — 받는 쪽에서 메시지를 확인하세요."}


@router.get("/operator/notifications")
def operator_notifications_page() -> RedirectResponse:
    """옛 알림 화면 — 연결 화면 알림 탭(phase 16 step 6)."""
    return _to_connect("notify")


@router.post("/operator/notifications/webhook")
def operator_notifications_save(
    request: Request,
    response: Response,
    url: str = Form(""),
    member: LoggedIn = Depends(require_action(team.MANAGE_SHARED_NOTIFY)),
    conn: Connection = Depends(get_conn),
) -> RedirectResponse:
    """검사 뒤 비밀 파일에 저장. 형식 오류 문구에 값을 되돌려 싣지 않는다."""
    url = _savable_webhook_url(url)
    request.app.state.secrets.write(secret_store.NOTIFY_WEBHOOK_URL, url)
    logger.info("알림 웹훅 저장: %s", notification.webhook_host(url))
    return _redirect("/connect?tab=notify", response)


@router.post("/operator/notifications/webhook/delete")
def operator_notifications_delete(
    request: Request,
    response: Response,
    member: LoggedIn = Depends(require_action(team.MANAGE_SHARED_NOTIFY)),
    conn: Connection = Depends(get_conn),
) -> RedirectResponse:
    request.app.state.secrets.delete(secret_store.NOTIFY_WEBHOOK_URL)
    return _redirect("/connect?tab=notify", response)


@router.post("/operator/notifications/test", response_class=HTMLResponse)
def operator_notifications_test(
    request: Request,
    member: LoggedIn = Depends(require_action(team.MANAGE_SHARED_NOTIFY)),
    conn: Connection = Depends(get_conn),
) -> str:
    """저장된 URL 로 대기열 없이 한 번 보내고 결과(보냄·HTTP 상태·시간 초과·연결 오류)만 보인다."""
    result = _send_test_notification(request, request.app.state.secrets.read(secret_store.NOTIFY_WEBHOOK_URL))
    return _connect_page(request, conn, member, "notify", test_result=result)


# --- 팀·내 설정 (phase 15 step 7, ADR-0021, ARCHITECTURE "팀 — phase 15" 새 경로) --------------------------------
# 초대·재설정 링크 원문은 발급 응답 화면에만 한 번(303 없이 렌더). 개인 웹훅 URL 은 비밀 파일
# `notify_webhook_url.<member_id>` 에만 두고 화면·로그에는 호스트만.

_LAST_ADMIN = "활성 관리자가 한 명은 있어야 합니다."
_BAD_CURRENT_PASSWORD = "현재 비밀번호가 올바르지 않습니다."


def _link_url(request: Request, kind: str, token: str) -> str:
    """`<base>/invite/<토큰>`·`<base>/reset/<토큰>` — base 는 공개 주소, 없으면 요청 주소."""
    base = _settings(request).public_url or str(request.base_url).rstrip("/")
    return f"{base}/{kind}/{token}"


def _checked_role(role: str) -> str:
    if role not in team.ROLES:
        raise PageError(422, "invalid_field", "역할은 관리자 또는 멤버입니다.", field="role")
    return role


def _team_member(conn: Connection, session_id: str, member_id: str) -> Row:
    row = repo.get_member(conn, session_id, member_id)
    if row is None:
        raise PageError(404, "not_found", f"멤버 {member_id}을 찾을 수 없습니다.", field="member_id")
    return row


@router.get("/team")
def team_page() -> RedirectResponse:
    """옛 팀 화면 — 연결 화면 팀·담당자 탭(phase 16 step 6)."""
    return _to_connect("team")


@router.post("/team/invites", response_class=HTMLResponse)
def team_invite(
    request: Request,
    role: str = Form(""),
    member: LoggedIn = Depends(require_action(team.MANAGE_TEAM)),
    conn: Connection = Depends(get_conn),
) -> str:
    """초대 링크 발급 — 같은 화면에 링크를 한 번만 보인다."""
    _, token = repo.issue_invite(conn, member.session_id, role=_checked_role(role),
                                 created_by_member_id=member.member_id, now=utc_now())
    logger.info("초대 링크 발급: %s", role)
    return _connect_page(request, conn, member, "team",
                         issued={"kind": "invite", "link": _link_url(request, "invite", token), "role": role})


@router.post("/team/invites/{invite_id}/revoke")
def team_invite_revoke(
    request: Request,
    response: Response,
    invite_id: str,
    member: LoggedIn = Depends(require_action(team.MANAGE_TEAM)),
    conn: Connection = Depends(get_conn),
) -> RedirectResponse:
    try:
        repo.revoke_invite(conn, member.session_id, invite_id, now=utc_now())
    except NotFound:
        raise PageError(404, "not_found", f"초대 {invite_id}을 찾을 수 없습니다.", field="invite_id") from None
    return _redirect("/connect?tab=team", response)


@router.post("/team/members/{member_id}/role")
def team_member_role(
    request: Request,
    response: Response,
    member_id: str,
    role: str = Form(""),
    member: LoggedIn = Depends(require_action(team.MANAGE_TEAM)),
    conn: Connection = Depends(get_conn),
) -> RedirectResponse:
    _team_member(conn, member.session_id, member_id)
    try:
        repo.set_member_role(conn, member.session_id, member_id, _checked_role(role), now=utc_now())
    except LastAdmin:
        raise PageError(409, "last_admin", _LAST_ADMIN) from None
    return _redirect("/connect?tab=team", response)


@router.post("/team/members/{member_id}/disable")
def team_member_disable(
    request: Request,
    response: Response,
    member_id: str,
    member: LoggedIn = Depends(require_action(team.MANAGE_TEAM)),
    conn: Connection = Depends(get_conn),
) -> RedirectResponse:
    """비활성화 — 그 멤버의 로그인 세션을 전부 폐기한다."""
    _team_member(conn, member.session_id, member_id)
    try:
        repo.disable_member(conn, member.session_id, member_id, now=utc_now())
    except LastAdmin:
        raise PageError(409, "last_admin", _LAST_ADMIN) from None
    return _redirect("/connect?tab=team", response)


@router.post("/team/members/{member_id}/enable")
def team_member_enable(
    request: Request,
    response: Response,
    member_id: str,
    member: LoggedIn = Depends(require_action(team.MANAGE_TEAM)),
    conn: Connection = Depends(get_conn),
) -> RedirectResponse:
    _team_member(conn, member.session_id, member_id)
    repo.enable_member(conn, member.session_id, member_id, now=utc_now())
    return _redirect("/connect?tab=team", response)


@router.post("/team/members/{member_id}/reset-link", response_class=HTMLResponse)
def team_member_reset_link(
    request: Request,
    member_id: str,
    member: LoggedIn = Depends(require_action(team.MANAGE_TEAM)),
    conn: Connection = Depends(get_conn),
) -> str:
    """재설정 링크 발급(그 멤버의 이전 미사용 링크는 취소) — 같은 화면에 링크를 한 번만 보인다."""
    target = _team_member(conn, member.session_id, member_id)
    _, token = repo.issue_reset_link(conn, member.session_id, member_id, created_by_member_id=member.member_id,
                                     now=utc_now())
    logger.info("재설정 링크 발급")
    return _connect_page(request, conn, member, "team", issued={
        "kind": "reset", "link": _link_url(request, "reset", token), "display_name": target["display_name"]})


def _me_page(request: Request, conn: Connection, member: LoggedIn, *, status: int = 200,
             error: str | None = None, test_result: dict[str, str] | None = None) -> HTMLResponse:
    now = utc_now()
    body = _render(
        "me.html", **_base(request, conn, member.session_id, now),
        **views.me_context(conn, member.session_id, member.member_id, secrets=request.app.state.secrets),
        error=error, test_result=test_result,
    )
    return HTMLResponse(body, status_code=status)


@router.get("/me", response_class=HTMLResponse)
def me_page(
    request: Request,
    member: LoggedIn = Depends(require_action(team.EDIT_OWN_SETTINGS)),
    conn: Connection = Depends(get_conn),
) -> HTMLResponse:
    """내 설정 — 표시 이름·이메일(읽기)·역할, 비밀번호 변경, 개인 웹훅(설정됨/없음·호스트만)."""
    return _me_page(request, conn, member)


@router.post("/me/profile")
def me_profile(
    request: Request,
    response: Response,
    display_name: str = Form(""),
    member: LoggedIn = Depends(require_action(team.EDIT_OWN_SETTINGS)),
    conn: Connection = Depends(get_conn),
) -> RedirectResponse:
    cleaned = team.clean_display_name(display_name)
    if cleaned is None:
        raise PageError(422, "invalid_field", "표시 이름은 1~40자로 입력하세요.", field="display_name")
    repo.set_member_display_name(conn, member.session_id, member.member_id, cleaned, now=utc_now())
    return _redirect("/me", response)


@router.post("/me/password", response_model=None)
def me_password(
    request: Request,
    current_password: str = Form(""),
    new_password: str = Form(""),
    member: LoggedIn = Depends(require_action(team.EDIT_OWN_SETTINGS)),
    conn: Connection = Depends(get_conn),
) -> HTMLResponse | RedirectResponse:
    """현재 비밀번호 확인(이 멤버 이메일 키로 실패 제한) → 새 비밀번호. 이 멤버의 로그인 세션을 전부 폐기하고
    이 브라우저에는 새 세션 쿠키를 준다."""
    row = repo.get_member(conn, member.session_id, member.member_id)
    key = login_email_key(row["email"])
    throttle = request.app.state.login_throttle
    if throttle.blocked(key):
        logger.warning("비밀번호 변경 거부: 연속 실패 제한")
        return _me_page(request, conn, member, status=429, error=_TOO_MANY)
    matched = len(current_password) <= team.PASSWORD_MAX_LENGTH and team.verify_password(current_password,
                                                                                         row["password_hash"])
    if not matched:
        throttle.fail(key)
        logger.warning("비밀번호 변경 실패: 현재 비밀번호 불일치")
        return _me_page(request, conn, member, status=403, error=_BAD_CURRENT_PASSWORD)
    problem = team.password_problem(new_password)
    if problem:
        return _me_page(request, conn, member, status=422, error=problem)
    throttle.reset(key)
    repo.set_member_credentials(conn, member.session_id, member.member_id, email=row["email"],
                                password_hash=team.hash_password(new_password), now=utc_now())
    logger.info("비밀번호 변경")
    return _logged_in_redirect(request, conn, member.member_id, "/me")


@router.post("/me/webhook")
def me_webhook_save(
    request: Request,
    response: Response,
    url: str = Form(""),
    member: LoggedIn = Depends(require_action(team.EDIT_OWN_SETTINGS)),
    conn: Connection = Depends(get_conn),
) -> RedirectResponse:
    """개인 웹훅 — 공용과 같은 검사, 비밀 파일만 개인 이름."""
    url = _savable_webhook_url(url)
    request.app.state.secrets.write(secret_store.personal_webhook_name(member.member_id), url)
    logger.info("개인 알림 웹훅 저장: %s", notification.webhook_host(url))
    return _redirect("/me", response)


@router.post("/me/webhook/delete")
def me_webhook_delete(
    request: Request,
    response: Response,
    member: LoggedIn = Depends(require_action(team.EDIT_OWN_SETTINGS)),
    conn: Connection = Depends(get_conn),
) -> RedirectResponse:
    request.app.state.secrets.delete(secret_store.personal_webhook_name(member.member_id))
    return _redirect("/me", response)


@router.post("/me/webhook/test", response_class=HTMLResponse)
def me_webhook_test(
    request: Request,
    member: LoggedIn = Depends(require_action(team.EDIT_OWN_SETTINGS)),
    conn: Connection = Depends(get_conn),
) -> HTMLResponse:
    url = request.app.state.secrets.read(secret_store.personal_webhook_name(member.member_id))
    return _me_page(request, conn, member, test_result=_send_test_notification(request, url))


@router.get("/metrics")
def metrics_redirect(request: Request) -> RedirectResponse:
    """옛 지표 주소 → `/monitor`(쿼리 그대로 — 값은 `/monitor` 의 파서가 검증)."""
    query = request.url.query
    return RedirectResponse(f"/monitor?{query}" if query else "/monitor", status_code=303)


@router.get("/start", response_class=HTMLResponse)
def start_page(
    request: Request,
    member: LoggedIn = Depends(require_member),
    conn: Connection = Depends(get_conn),
) -> str:
    """시작하기 체크리스트 — 항목·상태는 `domain/start_checklist.py`. 필수가 모두 끝나도 열린다."""
    facts = repo.start_facts(conn, member.session_id)
    return _render("start.html", **_base(request, conn, member.session_id, utc_now()),
                   items=start_checklist.start_items(facts), done=start_checklist.required_done(facts))


MONITOR_TABS = (("before_after", "전후"), ("triage", "판단"), ("assignees", "담당자별"))


@router.get("/monitor", response_class=HTMLResponse)
def metrics_page(
    request: Request,
    tab: str = Query("before_after"),
    since: str = Query("", alias="from"),
    until: str = Query("", alias="to"),
    group_by: str = Query(""),
    member: LoggedIn = Depends(require_action(team.VIEW_METRICS)),
    conn: Connection = Depends(get_conn),
) -> str:
    """모니터링 화면 탭 셋(phase 20) — `metrics_api` 와 같은 계산. 폼은 GET 이라 빈 칸은 "지정 안 함" 이다. 기준선 가져오기
    버튼은 운영자 JSON API(`POST /operator/github/sources/{id}/baseline`)로 보낸다. `group_by` 는 전후 탭만 쓰지만 늘 검증한다."""
    session_id = member.session_id
    now = utc_now()
    base = _base(request, conn, session_id, now)
    if tab not in dict(MONITOR_TABS):
        raise PageError(422, "invalid_field", "탭은 전후·판단·담당자별 중 하나입니다.", field="tab")
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
    context: dict[str, Any] = {}
    if tab == "before_after":
        context = views.metrics_context(report, metrics_api._baselines(conn, session_id))
        if group_by == "config_revision":
            heads = views.config_change_heads(repo.config_changes_by_revision(conn, session_id),
                                              [g["key"] for g in context["group_columns"]])
            context["group_columns"] = [{**g, "head": heads.get(g["key"])} for g in context["group_columns"]]
    elif tab == "triage":
        context = views.triage_quality_context(metrics_api._triage_report(conn, session_id, since or None, until or None))
    else:
        assignees = metrics_api._assignee_report(conn, request, session_id, since or None, until or None, now=now)
        context = views.assignee_context(assignees) | {"agent_owners": {
            a.agent_id: views.runner_owner(conn, session_id, repo.agent_owner_id(conn, a.agent_id))
            for a in assignees.agents
        }}
    return _render(
        "metrics.html", **base, **context,
        tab=tab, tabs=[{"key": key, "label": label, "href": "/monitor?" + urlencode({"tab": key, **{
            k: v for k, v in params.items() if v}})} for key, label in MONITOR_TABS],
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
    member: LoggedIn = Depends(require_action(team.MANAGE_CONNECTIONS)),
    conn: Connection = Depends(get_conn),
) -> RedirectResponse:
    """운영자 등록. 운영자 = 워크스페이스라 등록한 Agent 를 워크스페이스에 붙인다(ADR-0019 — 카탈로그 등록 단계 없음).
    자동 파악값은 연결 프로그램의 registrations 가 채우므로 기존 보고값은 유지한다.
    능력은 계약 패턴으로만 검사한다 — 운영자는 세션 무관이라 어느 세션의 종류와 맞는지는 그 세션의 선택 시점에 정해진다.
    `scope_key` 를 비우면 내장 종류의 코드일 때만 그 종류의 scope 키를 쓴다."""
    session_id = member.session_id
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
    return _redirect("/connect?tab=advanced", response)


@router.post("/operator/agents/{agent_id}/delete")
def operator_delete_agent(
    response: Response,
    agent_id: str,
    member: LoggedIn = Depends(require_member),
    conn: Connection = Depends(get_conn),
) -> RedirectResponse:
    """`manage_connections`(관리자) 또는 그 에이전트 러너의 소유자 본인. 없는 에이전트는 관리자에게만 404."""
    row = repo.get_agent(conn, agent_id)
    owner_id = views.agent_owner_id(conn, row) if row is not None else None
    if not (team.can(member.role, team.MANAGE_CONNECTIONS) or _may_remove_runner(member, owner_id)):
        raise _forbid_runner_removal(owner_id)
    try:
        repo.delete_agent(conn, agent_id)
    except NotFound:
        raise PageError(404, "not_found", f"에이전트 {agent_id}을 찾을 수 없습니다.", field="agent_id") from None
    return _redirect("/connect?tab=advanced", response)


@router.post("/operator/connect-codes", response_class=HTMLResponse)
def operator_issue_connect_code(
    request: Request,
    member: LoggedIn = Depends(require_action(team.ATTACH_RUNNER)),
    conn: Connection = Depends(get_conn),
) -> str:
    """발급한 코드는 이 응답 화면에만 보인다 (URL 에 넣지 않는다). 1회용·10분."""
    now = utc_now()
    code = repo.issue_connect_code(conn, now, issued_by_member_id=member.member_id)
    row = next(c for c in repo.list_connect_codes(conn) if c["code"] == code)
    return _connect_page(request, conn, member, "advanced", issued={"code": code, "expires_at": row["expires_at"]})


@router.post("/operator/connect-codes/{code}/revoke")
def operator_revoke_connect_code(
    response: Response,
    code: str,
    member: LoggedIn = Depends(require_action(team.ATTACH_RUNNER)),
    conn: Connection = Depends(get_conn),
) -> RedirectResponse:
    """발급자 본인 또는 `remove_any_runner`. 발급자 없는 코드는 관리자만."""
    row = next((c for c in repo.list_connect_codes(conn) if c["code"] == code), None)
    owner_id = row["issued_by_member_id"] if row is not None else None
    if not _may_remove_runner(member, owner_id):
        raise _forbid_runner_removal(owner_id)
    try:
        repo.revoke_connect_code(conn, code, utc_now())
    except NotFound:
        raise PageError(404, "not_found", "취소할 수 있는 연결 코드가 아닙니다.", field="code") from None
    return _redirect("/connect?tab=advanced", response)


@router.post("/operator/connectors/{connector_id}/revoke")
def operator_revoke_connector(
    response: Response,
    connector_id: str,
    member: LoggedIn = Depends(require_action(team.ATTACH_RUNNER)),
    conn: Connection = Depends(get_conn),
) -> RedirectResponse:
    """러너 해제 — 연결 토큰 무효(러너 다음 요청 401). 소유자 본인 또는 `remove_any_runner`, 소유자 없는 러너는 관리자만."""
    owner_id = repo.connector_owner(conn, connector_id)
    if not _may_remove_runner(member, owner_id):
        raise _forbid_runner_removal(owner_id)
    try:
        repo.revoke_connector(conn, connector_id, utc_now())
    except NotFound:
        raise PageError(404, "not_found", "해제할 수 있는 러너가 아닙니다.", field="connector_id") from None
    return _redirect("/connect?tab=team", response)


@router.post('/requests/{request_id}/resume')
def internal_request_resume(
    request_id: str, response: Response, expected_revision: int = Form(...), text: str = Form(...),
    member: LoggedIn = Depends(require_action(team.RESPOND)), conn: Connection = Depends(get_conn),
) -> RedirectResponse:
    try:
        body = InternalInformationAnswer(expected_revision=expected_revision, text=text)
    except ValidationError:
        raise PageError(422, 'invalid_field', '업무 재개 내용을 입력하세요.') from None
    try:
        internal_request_store.resume(conn, member.session_id, member.member_id, request_id, body, now=utc_now())
    except internal_request_store.RequestProblem as exc:
        raise PageError(exc.status, exc.code, str(exc)) from None
    return _redirect('/requests', response)
