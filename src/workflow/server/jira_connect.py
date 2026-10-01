"""Jira 연결 — 확인·호출 기준 주소·설정 이름 대조·연결 화면 컨텍스트 (ADR-0024 결정 1·2·3·18, ARCHITECTURE "Jira 소스 — phase 18").

- 확인 순서: `tenant_info`(인증 없음) → 게이트웨이 `myself` → 401 이면 사이트 `myself`. 된 쪽이 `api_base`.
  사이트 주소는 `https://<이름>.atlassian.net` 만(형식 밖이면 요청 없이 ValueError — SSRF 방지).
- 토큰은 다루기만 하고 들고 있지 않는다 — 확인 결과(`JiraConnectionFacts`)에 토큰 칸이 없다. 저장은 호출자(web)가
  비밀 저장소에 한다.
- 설정 이름(이슈 유형·상태)은 Jira 에서 받은 후보(`choices_json`)와 대소문자 무시로 같아야 하고 후보 표기로 저장한다.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from sqlite3 import Connection, Row
from typing import Any, Literal

import httpx

from workflow.adapters import repo
from workflow.adapters.jira_client import HttpJiraClient, JiraUnauthorized, tenant_info
from workflow.contracts.jira import JIRA_GATEWAY, JiraChoices, JiraConnection, normalize_site_url

TOKEN_PAGE = "https://id.atlassian.com/manage-profile/security/api-tokens"  # 화면의 "토큰 만들기" 링크(새 창)


@dataclass(frozen=True)
class JiraConnectionFacts:
    """확인이 끝난 연결의 공개 정보 — `jira_connections` 한 행의 재료. 토큰 없음."""

    site_url: str
    cloud_id: str
    api_base: Literal["gateway", "site"]
    email: str
    account_id: str
    display_name: str


class InvalidSetting(ValueError):
    """설정 칸 값이 Jira 후보에 없다. `field` = 폼 칸 이름."""

    def __init__(self, field: str, message: str):
        super().__init__(message)
        self.field = field


def normalize_site(value: str) -> str:
    """`https://<이름>.atlassian.net` 으로 맞춘 값. 형식 밖이면 ValueError."""
    return normalize_site_url(value)


def api_base_url(connection: Row | Mapping[str, Any] | JiraConnection) -> str:
    """저장한 연결의 호출 기준 주소 — 게이트웨이 + cloudId 또는 사이트. 다른 URL 은 만들지 않는다."""
    if isinstance(connection, JiraConnection):
        api_base, cloud_id, site_url = connection.api_base, connection.cloud_id, connection.site_url
    else:
        api_base, cloud_id, site_url = connection["api_base"], connection["cloud_id"], connection["site_url"]
    return JIRA_GATEWAY + cloud_id if api_base == "gateway" else site_url


def verify(
    site_url: str, email: str, token: str, *, transport: httpx.BaseTransport | None = None
) -> JiraConnectionFacts:
    """연결 확인. 형식 오류는 ValueError(요청 없음), Jira 오류는 `jira_client` 오류 계층 그대로."""
    site = normalize_site(site_url)
    cloud_id = tenant_info(site, transport=transport)
    try:
        account_id, display_name = HttpJiraClient(JIRA_GATEWAY + cloud_id, email, token, transport=transport).myself()
        api_base: Literal["gateway", "site"] = "gateway"
    except JiraUnauthorized:
        account_id, display_name = HttpJiraClient(site, email, token, transport=transport).myself()
        api_base = "site"
    return JiraConnectionFacts(site_url=site, cloud_id=cloud_id, api_base=api_base, email=email,
                               account_id=account_id, display_name=display_name)


def _pick(names: Sequence[str], value: str, field: str) -> str | None:
    value = value.strip()
    if not value:
        return None
    for name in names:
        if name.casefold() == value.casefold():
            return name
    raise InvalidSetting(field, f"Jira 목록에 없는 이름입니다: {value}")


def project_settings(
    choices: JiraChoices, *, issue_types: Sequence[str], status_on_start: str, status_on_review: str,
    status_on_done: str, followup_issue_type: str,
) -> dict[str, Any]:
    """폼 값을 후보와 대조한다 — 빈 값은 None, 같은 이름은 후보 표기로, 모르는 이름은 InvalidSetting."""
    type_names = [t.name for t in choices.issue_types]
    picked: list[str] = []
    for value in issue_types:
        name = _pick(type_names, value, "issue_types")
        if name is not None and name not in picked:
            picked.append(name)
    return {
        "issue_types": picked,
        "status_on_start": _pick(choices.statuses, status_on_start, "status_on_start"),
        "status_on_review": _pick(choices.statuses, status_on_review, "status_on_review"),
        "status_on_done": _pick(choices.statuses, status_on_done, "status_on_done"),
        "followup_issue_type": _pick(type_names, followup_issue_type, "followup_issue_type"),
    }


def connect_context(conn: Connection, session_id: str, *, token_saved: bool) -> dict[str, Any]:
    """연결 화면 가져올 곳 탭의 Jira 절. `state` = `none`(입력 칸)·`connected`·`auth_failed`(경고 + 입력 칸).
    끊겼거나 토큰 파일이 없으면 `none`. 화면을 그릴 때 Jira 를 부르지 않는다(후보는 저장한 `choices_json`)."""
    row = repo.get_jira_connection(conn, session_id)
    if row is None or row["disconnected_at"] is not None or not token_saved:
        state = "none"
    else:
        state = "auth_failed" if row["auth_failed_at"] is not None else "connected"
    sources = repo.list_github_sources(conn, session_id)
    names = {s.source_id: s.repository_full_name for s in sources}
    failures = repo.jira_delivery_failures(conn, session_id)
    projects = [
        {
            "config": repo.jira_project_config(r).model_dump(mode="json"),
            "choices": JiraChoices.model_validate_json(r["choices_json"]).model_dump(mode="json"),
            "repository_full_name": names.get(r["github_source_id"]),
            "cursor_updated_at": r["cursor_updated_at"],
            "delivery_failures": failures.get(r["source_id"], 0),  # 지금 연결 이후 Jira 반영 실패 수
        }
        for r in repo.list_jira_project_rows(conn, session_id)
    ]
    return {"jira": {
        "state": state,
        "token_page": TOKEN_PAGE,
        "connection": None if state == "none" else {k: row[k] for k in ("site_url", "email", "display_name")},
        "projects": projects,
        "github_sources": [{"source_id": s.source_id, "repository_full_name": s.repository_full_name} for s in sources],
    }}
