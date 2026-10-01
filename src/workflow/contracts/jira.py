"""Jira 소스 계약 — 연결 공개 정보·프로젝트 설정·이슈 스냅숏 (ARCHITECTURE "Jira 소스 — phase 18", ADR-0024).

중앙 서버 안에서만 쓰는 모델이다(연결 프로그램은 모른다). 규칙은 v1 과 같다 — 알 수 없는 필드 거부, 자동 변환 없음.
토큰 칸은 없다: 값은 비밀 저장소 `jira_api_token` 에만 있다(ADR-0024 결정 2). 사이트 주소는 `https://<이름>.atlassian.net`
만 받는다(다른 호스트·경로·포트 거부 — SSRF 방지). 이슈 제목·본문·상태 이름은 표시·요청 재료일 뿐 명령·경로로 해석하지 않는다.
"""

import hashlib
import json
import re
from typing import Annotated, Literal

from pydantic import AfterValidator, Field, model_validator

from workflow.contracts.github import SourceId
from workflow.contracts.v1 import NonEmptyStr, Rfc3339, _Contract

JIRA_SITE_PATTERN = r"^https://[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.atlassian\.net$"
JIRA_EMAIL_PATTERN = r"^[^@\s]+@[^@\s]+$"
JIRA_TOKEN_PATTERN = r"^[\x21-\x7e]{1,2000}$"  # 공백 없는 ASCII — 헤더에 그대로 싣는다
JIRA_CLOUD_ID_PATTERN = r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
JIRA_EMAIL_MAX = 254
JIRA_GATEWAY = "https://api.atlassian.com/ex/jira/"
JIRA_MOMENTS = ("start", "review", "done")
JIRA_DELIVERY_STATES = ("pending", "sending", "delivered", "unknown", "failed", "skipped")
JIRA_FIELDS = ("summary", "description", "status", "issuetype", "priority", "labels", "created", "updated", "project")

_SITE = re.compile(JIRA_SITE_PATTERN)
_EMAIL = re.compile(JIRA_EMAIL_PATTERN)
_TOKEN = re.compile(JIRA_TOKEN_PATTERN)
_CLOUD_ID = re.compile(JIRA_CLOUD_ID_PATTERN)


def normalize_site_url(value: str) -> str:
    """앞뒤 공백 제거·소문자·끝 `/` 하나 제거 뒤 `JIRA_SITE_PATTERN` 전체 일치만. 아니면 ValueError."""
    site = value.strip().lower().removesuffix("/")
    if not _SITE.fullmatch(site):
        raise ValueError("https://<이름>.atlassian.net 형식만 받습니다")
    return site


def normalize_cloud_id(value: str) -> str:
    cloud_id = value.lower()
    if not _CLOUD_ID.fullmatch(cloud_id):
        raise ValueError("cloudId 는 UUID 형식이어야 합니다")
    return cloud_id


def valid_email(value: str) -> bool:
    return len(value) <= JIRA_EMAIL_MAX and _EMAIL.fullmatch(value) is not None


def valid_token(value: str) -> bool:
    return _TOKEN.fullmatch(value) is not None


def _email(value: str) -> str:
    if not valid_email(value):
        raise ValueError("이메일 형식이 아닙니다")
    return value


JiraSiteUrl = Annotated[str, AfterValidator(normalize_site_url)]
JiraCloudId = Annotated[str, AfterValidator(normalize_cloud_id)]
JiraEmail = Annotated[str, AfterValidator(_email)]
JiraSourceId = Annotated[str, Field(pattern=r"^jps-[0-9a-f]{8}$")]
JiraIssueId = Annotated[str, Field(pattern=r"^[1-9][0-9]*$")]  # 프로젝트 id 도 같은 형식
JiraIssueKey = Annotated[str, Field(pattern=r"^[A-Z][A-Z0-9_]*-[1-9][0-9]*$")]
JiraProjectKey = Annotated[str, Field(pattern=r"^[A-Z][A-Z0-9_]*$")]
JiraCategory = Literal["new", "indeterminate", "done"]


class JiraConnection(_Contract):
    """워크스페이스의 Jira Cloud 연결 공개 정보(`jira_connections` 한 행). 토큰은 없다."""

    session_id: NonEmptyStr
    site_url: JiraSiteUrl
    cloud_id: JiraCloudId
    api_base: Literal["gateway", "site"]
    email: JiraEmail
    account_id: NonEmptyStr
    display_name: NonEmptyStr
    connected_at: Rfc3339
    disconnected_at: Rfc3339 | None
    auth_failed_at: Rfc3339 | None


class JiraProjectConfig(_Contract):
    """Jira 프로젝트 하나의 설정(`jira_projects` 한 행). 상태·이슈 유형 이름은 Jira 후보(`choices_json`) 표기다."""

    source_id: JiraSourceId
    session_id: NonEmptyStr
    project_id: JiraIssueId
    project_key: JiraProjectKey
    project_name: NonEmptyStr
    github_source_id: SourceId  # 연결 저장소 — 실행·PR 은 이 저장소에서
    issue_types: list[NonEmptyStr]  # 비면 전부
    start_mode: Literal["from_now", "all_open"]
    start_at: Rfc3339
    status_on_start: NonEmptyStr | None
    status_on_review: NonEmptyStr | None
    status_on_done: NonEmptyStr | None
    followup_issue_type: NonEmptyStr | None  # 비면 후속을 Jira 에 만들지 않는다
    enabled: bool

    @model_validator(mode="after")
    def _check_issue_types(self) -> "JiraProjectConfig":
        folded = [name.casefold() for name in self.issue_types]
        if len(set(folded)) != len(folded):
            raise ValueError("issue_types 에 중복이 있습니다")
        return self


class JiraIssueSnapshot(_Contract):
    """Jira 이슈 응답에서 필요한 값만. 본문은 ADF 를 텍스트로 바꾼 것(`domain/adf.adf_to_text`), 시각은 RFC 3339 —
    둘 다 클라이언트가 바꿔 넣는다."""

    issue_id: JiraIssueId
    key: JiraIssueKey
    project_id: JiraIssueId
    summary: NonEmptyStr
    description_text: str
    status_name: NonEmptyStr
    status_category: JiraCategory
    issue_type: NonEmptyStr
    priority: str | None
    labels: list[str]
    created: Rfc3339
    updated: Rfc3339


class JiraProjectRef(_Contract):
    project_id: JiraIssueId
    key: JiraProjectKey
    name: NonEmptyStr


class JiraIssueType(_Contract):
    id: JiraIssueId
    name: NonEmptyStr


class JiraChoices(_Contract):
    """프로젝트의 후보 — 이슈 유형(하위 작업 제외)과 상태 이름(처음 나온 순서). `jira_projects.choices_json` 모양."""

    issue_types: list[JiraIssueType]
    statuses: list[NonEmptyStr]


class JiraIssueRef(_Contract):
    issue_id: JiraIssueId
    key: JiraIssueKey


class JiraTransition(_Contract):
    transition_id: NonEmptyStr
    name: NonEmptyStr
    to_name: NonEmptyStr
    to_category: JiraCategory


def snapshot_digest(snapshot: JiraIssueSnapshot) -> str:
    """같은 내용 판정용 sha256 — 키 정렬된 JSON (GitHub `snapshot_digest` 와 같다)."""
    data = json.dumps(snapshot.model_dump(mode="json"), sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(data.encode()).hexdigest()
