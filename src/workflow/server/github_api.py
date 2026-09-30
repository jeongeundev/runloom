"""운영자 GitHub 소스 설정·담당 연결 API — ADR-0014 결정 1·5, CONTRACT 13.6·13.9.

- 목록·상세는 로그인한 멤버(`require_member_api`), 쓰기는 `manage_connections`(ADR-0021). 소스는 만든 세션 소유이고,
  다른 세션에는 404 다.
  셀프호스트 1개 워크스페이스: 이미 다른 세션이 소스를 가지고 있으면 새 소스를 만들 수 없다(409 `github_workspace_taken`).
- 토큰은 요청으로 받지 않고(알 수 없는 필드 422) 응답에는 `token_configured` 만 넣는다. 값은 `Settings.github_token` 에만.
- 저장소는 `Settings.github_repos`(`WORKFLOW_GITHUB_REPOS`) 안에서만 연결한다(대소문자 무시, 저장은 목록의 표기).
- Agent 는 이 세션에 등록된 것만. 검토 Agent 는 `code.review {repository_id}`, 담당 Agent 는 `code.fix {repository_id}` 와
  로컬 등록이 보고한 검증 프로필 `fix_verification_profile_id` 가 있어야 한다. 프로필은 ID 일 뿐 명령이 아니다.
- 설정 변경은 `config_revision` 을 올릴 뿐 이미 만든 Task·Execution 입력을 바꾸지 않는다. GitHub 호출은 하지 않는다(수집은 step 7).
- phase 11(ADR-0017): `intake: all_open` 소스는 범위·세 ID 가 비어도 된다(None 은 자동 매칭 몫이라 검사하지 않는다).
  `start_at` 생략은 서버 수신 시각, `all_open` 의 `trigger_label` 생략은 `runloom`. `installation_id` 는 본문으로 받지 않고
  (App 설치 흐름이 정한다) 변경 때 유지하며, 설치 소스의 변경은 허용 목록 검사를 하지 않는다.
"""

import json
import secrets
import sqlite3
from sqlite3 import Connection
from typing import Literal

from fastapi import APIRouter, Depends, Path, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from workflow.adapters import repo
from workflow.adapters.errors import StaleConfig
from workflow.contracts.github import (
    FILTERED_REQUIRED,
    AssigneeBinding,
    GitHubSourceConfig,
    PositiveInt,
    RepositoryFullName,
)
from workflow.contracts.v1 import NonEmptyStr, Rfc3339
from workflow.domain import team
from workflow.server.auth import LoggedIn, get_conn, require_action, require_member_api, utc_now
from workflow.server.errors import ApiError
from workflow.server.settings import Settings

router = APIRouter(prefix="/github")

DEFAULT_TRIGGER_LABEL = "runloom"


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class SourceSettingsRequest(_Body):
    """`GitHubSourceConfig` 에서 서버가 정하는 `source_id`·`config_revision`·`installation_id` 를 뺀 것. 토큰 필드는 없다."""

    repository_full_name: RepositoryFullName
    intake: Literal["filtered", "all_open"] = "filtered"
    workflow_repository_id: NonEmptyStr | None = None  # filtered 필수 여부는 _config 가 검사한다
    label_filter: list[NonEmptyStr] = []
    selected_issue_numbers: list[PositiveInt] = []
    start_at: Rfc3339 | None = None  # 생략하면 서버 수신 시각
    fix_verification_profile_id: NonEmptyStr | None = None
    review_agent_id: NonEmptyStr | None = None
    run_mode: Literal["auto", "manual"]
    max_rework_rounds: int = 1  # 0~3 범위는 GitHubSourceConfig 가 검사한다
    trigger_label: NonEmptyStr | None = None  # 생략하면 all_open 은 DEFAULT_TRIGGER_LABEL, 명시한 null 은 그대로
    default_fix_agent_id: NonEmptyStr | None = None
    enabled: bool = True


class SourceUpdateRequest(SourceSettingsRequest):
    expected_revision: PositiveInt


class AssigneeRequest(_Body):
    github_login: NonEmptyStr
    agent_id: NonEmptyStr


def _settings(request: Request) -> Settings:
    return request.app.state.settings


def _config(
    body: SourceSettingsRequest, source_id: str, revision: int, repository: str, installation_id: int | None = None
) -> GitHubSourceConfig:
    if body.intake == "filtered":  # phase 8 요청에서 빠졌을 때와 같은 오류 — 그 필드의 invalid_field
        for name in FILTERED_REQUIRED:
            if getattr(body, name) is None:
                raise ApiError(422, "invalid_field", f"필드 {name}: intake filtered 에는 필요합니다", field=name)
    data = body.model_dump(exclude={"expected_revision"})
    if data["start_at"] is None:
        data["start_at"] = utc_now()
    if body.intake == "all_open" and "trigger_label" not in body.model_fields_set:
        data["trigger_label"] = DEFAULT_TRIGGER_LABEL
    return GitHubSourceConfig(**{**data, "repository_full_name": repository}, source_id=source_id,
                              config_revision=revision, installation_id=installation_id)


def _source_view(config: GitHubSourceConfig, settings: Settings) -> dict:
    return {"source": config.model_dump(mode="json"), "token_configured": bool(settings.github_token)}


def _not_found(source_id: str) -> ApiError:
    return ApiError(404, "not_found", f"source {source_id}을 찾을 수 없습니다.", field="source_id")


def _existing(conn: Connection, session_id: str, source_id: str) -> GitHubSourceConfig:
    config = repo.get_github_source(conn, session_id, source_id)
    if config is None:
        raise _not_found(source_id)
    return config


# --- 검사 -----------------------------------------------------------------------------------


def _allowed_name(name: str, settings: Settings) -> str | None:
    """허용 목록의 표기. 목록 밖이면 None."""
    return next((allowed for allowed in settings.github_repos if allowed.lower() == name.lower()), None)


def _has_capability(agent, code: str, repository_id: str) -> bool:
    return any(
        cap["code"] == code and cap["scope"] == {"repository_id": repository_id}
        for cap in json.loads(agent["capabilities_json"])
    )


def _session_agent(conn: Connection, session_id: str, agent_id: str, field: str) -> tuple[object, ApiError | None]:
    agent = repo.get_agent(conn, agent_id)
    if agent is None or not repo.is_session_agent(conn, session_id, agent_id):
        return None, ApiError(422, "agent_not_registered",
                              f"에이전트 {agent_id} 는 이 워크스페이스에 등록되지 않았습니다.", field=field)
    return agent, None


def _capability_error(agent_id: str, code: str, repository_id: str, field: str) -> ApiError:
    return ApiError(422, "agent_capability_mismatch",
                    f"에이전트 {agent_id} 에 {code} · repository_id={repository_id} 능력이 없습니다.", field=field)


def _agent_problem(
    conn: Connection, session_id: str, agent_id: str | None, code: str, repository_id: str | None, field: str
) -> ApiError | None:
    """None 이면 자동 매칭 몫이라 검사하지 않는다. 저장소가 None 이면 등록 여부만 본다."""
    if agent_id is None:
        return None
    agent, problem = _session_agent(conn, session_id, agent_id, field)
    if problem is None and repository_id is not None and not _has_capability(agent, code, repository_id):
        problem = _capability_error(agent_id, code, repository_id, field)
    return problem


def _source_problems(
    conn: Connection, session_id: str, body: SourceSettingsRequest, settings: Settings, *, installed: bool = False
) -> list[ApiError]:
    """모든 문제를 모은다 — 미리보기는 목록을 그대로, 저장은 첫 문제를 422 로. `installed` 는 App 설치 소스 변경."""
    problems: list[ApiError] = []
    name = body.repository_full_name
    if not installed and _allowed_name(name, settings) is None:
        problems.append(ApiError(422, "repository_not_allowed", f"저장소 {name} 는 WORKFLOW_GITHUB_REPOS 에 없습니다.",
                                 field="repository_full_name"))
    repository_id = body.workflow_repository_id
    problem = _agent_problem(conn, session_id, body.review_agent_id, "code.review", repository_id, "review_agent_id")
    if problem is not None:
        problems.append(problem)
    profile = body.fix_verification_profile_id
    if profile is not None and repository_id is not None and not any(
        _has_capability(a, "code.fix", repository_id) and profile in json.loads(a["verification_profile_ids_json"])
        for a in repo.list_session_agents(conn, session_id)
    ):
        problems.append(ApiError(
            422, "verification_profile_unknown",
            f"검증 프로필 {profile} 을 보고한 code.fix · repository_id={repository_id} 에이전트가 이 워크스페이스에 없습니다.",
            field="fix_verification_profile_id",
        ))
    problem = _agent_problem(conn, session_id, body.default_fix_agent_id, "code.fix", repository_id,
                             "default_fix_agent_id")
    if problem is not None:
        problems.append(problem)
    return problems


def _raise_first(problems: list[ApiError]) -> None:
    if problems:
        raise problems[0]


def _save(conn: Connection, session_id: str, config: GitHubSourceConfig, *, expected_revision: int | None) -> None:
    try:
        repo.save_github_source(conn, session_id, config, utc_now(), expected_revision=expected_revision)
    except StaleConfig as exc:
        raise ApiError(409, "stale_config", f"source {config.source_id} 설정이 이미 revision {exc.current_revision} 입니다.",
                       field="expected_revision", details={"current_revision": exc.current_revision}) from None
    except sqlite3.IntegrityError:
        raise ApiError(409, "source_exists", f"저장소 {config.repository_full_name} 는 이미 연결되어 있습니다.",
                       field="repository_full_name") from None


# --- 소스 ------------------------------------------------------------------------------------


@router.get("/sources")
def list_sources(
    request: Request,
    member: LoggedIn = Depends(require_member_api),
    conn: Connection = Depends(get_conn),
) -> JSONResponse:
    session_id = member.session_id
    settings = _settings(request)
    return JSONResponse({
        "token_configured": bool(settings.github_token),
        "allowed_repositories": list(settings.github_repos),
        "sources": [c.model_dump(mode="json") for c in repo.list_github_sources(conn, session_id)],
    })


@router.post("/sources/preview")
def preview_source(
    request: Request, body: SourceSettingsRequest,
    member: LoggedIn = Depends(require_action(team.MANAGE_CONNECTIONS, api=True)),
    conn: Connection = Depends(get_conn),
) -> JSONResponse:
    """저장하지 않고 검사만 한다. 형식 오류(범위 없음 등)는 422, 저장소·Agent·프로필 문제는 200 의 `problems` 목록."""
    session_id = member.session_id
    settings = _settings(request)
    _config(body, "ghs-00000000", 1, body.repository_full_name)
    return JSONResponse({
        "token_configured": bool(settings.github_token),
        "problems": [p.body() for p in _source_problems(conn, session_id, body, settings)],
    })


@router.post("/sources", status_code=201)
def create_source(
    request: Request, body: SourceSettingsRequest,
    member: LoggedIn = Depends(require_action(team.MANAGE_CONNECTIONS, api=True)),
    conn: Connection = Depends(get_conn),
) -> JSONResponse:
    session_id = member.session_id
    settings = _settings(request)
    if any(owner != session_id for owner in repo.github_source_sessions(conn)):
        raise ApiError(409, "github_workspace_taken", "GitHub 연결은 이미 다른 운영자 워크스페이스가 쓰고 있습니다.")
    config = _config(body, f"ghs-{secrets.token_hex(4)}", 1, body.repository_full_name)
    _raise_first(_source_problems(conn, session_id, body, settings))
    config = config.model_copy(update={"repository_full_name": _allowed_name(body.repository_full_name, settings)})
    _save(conn, session_id, config, expected_revision=None)
    return JSONResponse(_source_view(config, settings), status_code=201)


@router.get("/sources/{source_id}")
def get_source(
    request: Request, source_id: str,
    member: LoggedIn = Depends(require_member_api), conn: Connection = Depends(get_conn),
) -> JSONResponse:
    session_id = member.session_id
    config = _existing(conn, session_id, source_id)
    bindings = repo.list_assignee_bindings(conn, session_id, source_id)
    return JSONResponse({
        **_source_view(config, _settings(request)),
        "assignees": [b.model_dump(mode="json") for b in bindings],
    })


@router.put("/sources/{source_id}")
def update_source(
    request: Request, source_id: str, body: SourceUpdateRequest,
    member: LoggedIn = Depends(require_action(team.MANAGE_CONNECTIONS, api=True)),
    conn: Connection = Depends(get_conn),
) -> JSONResponse:
    """전체 교체 + `expected_revision` 잠금. 저장소는 바꿀 수 없다(커서·원본 매핑이 그 저장소 것이다)."""
    session_id = member.session_id
    settings = _settings(request)
    current = _existing(conn, session_id, source_id)
    if body.repository_full_name.lower() != current.repository_full_name.lower():
        raise ApiError(422, "invalid_field", "repository_full_name 은 바꿀 수 없습니다 — 새 소스로 연결하세요.",
                       field="repository_full_name")
    config = _config(body, source_id, body.expected_revision + 1, current.repository_full_name,
                     current.installation_id)
    _raise_first(_source_problems(conn, session_id, body, settings, installed=current.installation_id is not None))
    _save(conn, session_id, config, expected_revision=body.expected_revision)
    return JSONResponse(_source_view(config, settings))


@router.post("/sources/{source_id}/stop")
def stop_source(
    request: Request, source_id: str,
    member: LoggedIn = Depends(require_action(team.MANAGE_CONNECTIONS, api=True)),
    conn: Connection = Depends(get_conn),
) -> JSONResponse:
    """`enabled=false`. 이미 멈췄으면 그대로(revision 유지). 진행 중 실행은 건드리지 않는다."""
    session_id = member.session_id
    current = _existing(conn, session_id, source_id)
    if current.enabled:
        stopped = current.model_copy(update={"enabled": False, "config_revision": current.config_revision + 1})
        _save(conn, session_id, stopped, expected_revision=current.config_revision)
        current = stopped
    return JSONResponse(_source_view(current, _settings(request)))


# --- 담당 연결 -------------------------------------------------------------------------------


@router.put("/sources/{source_id}/assignees/{github_user_id}")
def bind_assignee(
    source_id: str, body: AssigneeRequest,
    github_user_id: int = Path(ge=1),
    member: LoggedIn = Depends(require_action(team.MANAGE_CONNECTIONS, api=True)),
    conn: Connection = Depends(get_conn),
) -> JSONResponse:
    """GitHub 사용자 숫자 ID → 이 세션의 수정 Agent. 같은 ID 는 한 행(다시 부르면 교체)."""
    session_id = member.session_id
    config = _existing(conn, session_id, source_id)
    agent, problem = _session_agent(conn, session_id, body.agent_id, "agent_id")
    if problem is not None:
        raise problem
    # all_open 소스에서 아직 정해지지 않은 칸(None)은 검사하지 않는다 — 자동 매칭 몫
    repository_id = config.workflow_repository_id
    if repository_id is not None and not _has_capability(agent, "code.fix", repository_id):
        raise _capability_error(body.agent_id, "code.fix", repository_id, "agent_id")
    profile = config.fix_verification_profile_id
    if profile is not None and profile not in json.loads(agent["verification_profile_ids_json"]):
        raise ApiError(422, "verification_profile_unknown",
                       f"에이전트 {body.agent_id} 의 로컬 등록에 검증 프로필 {config.fix_verification_profile_id} 이 없습니다.",
                       field="agent_id")
    binding = AssigneeBinding(source_id=source_id, github_user_id=github_user_id,
                              github_login=body.github_login, agent_id=body.agent_id)
    repo.bind_assignee(conn, session_id, binding, utc_now())
    return JSONResponse({"assignee": binding.model_dump(mode="json")})
