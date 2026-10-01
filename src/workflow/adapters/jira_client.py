"""Jira Cloud REST 클라이언트 — 중앙 서버 → 연결한 Jira 사이트 (ADR-0024, ARCHITECTURE "Jira 소스 — phase 18").

- 기준 주소는 `JIRA_GATEWAY` + cloudId 또는 `https://<이름>.atlassian.net` 둘 중 하나만 받는다(생성자에서 검사 — SSRF 방지).
  경로는 이 모듈이 고정 문자열로 만들고, 넣는 id·키는 보내기 전에 형식을 검사한다(밖이면 ValueError, 요청 없음).
  리다이렉트를 따라가지 않는다(`follow_redirects=False`). 응답은 `MAX_RESPONSE_BYTES` 까지만 읽는다.
- 인증은 Basic(`email:token`). 토큰은 호출자가 비밀 저장소(`secret_store.JIRA_API_TOKEN`)에서 읽어 넘긴다. 고정 토큰이라
  401 에 다시 보내지 않는다. 오류 메시지는 `메서드 경로: HTTP 상태` 뿐이고 400 요약(`JiraBadRequest.message`)에서도
  토큰을 지운다 — 토큰·Authorization 헤더·응답 본문을 싣지 않는다.
- 오류: 401 `JiraUnauthorized`, 403 `JiraForbidden`, 404 `JiraNotFound`, 400 `JiraBadRequest`, 429(그리고 `Retry-After`
  가 붙은 503) `JiraRateLimited`, 5xx·연결 오류·timeout `JiraUnavailable`, 그 밖(3xx·409·응답 형식) `JiraError`.
  재시도 여부·간격은 호출자(step 5 가져오기·step 7·8 전송)가 정한다.
"""

import base64
import json
import re
from collections.abc import Sequence
from typing import Any, Protocol

import httpx
from pydantic import ValidationError

from workflow.contracts.jira import (
    JIRA_FIELDS,
    JIRA_GATEWAY,
    JiraChoices,
    JiraIssueRef,
    JiraIssueSnapshot,
    JiraIssueType,
    JiraProjectRef,
    JiraTransition,
    normalize_cloud_id,
    normalize_site_url,
    valid_email,
    valid_token,
)
from workflow.domain.adf import adf_to_text
from workflow.domain.jira_intake import jql_string

API = "/rest/api/3"
DEFAULT_TIMEOUT = 20.0
MAX_RESPONSE_BYTES = 5 * 1024 * 1024
DEFAULT_RETRY_AFTER = 60
SEARCH_MAX_RESULTS = 100
PROJECT_SEARCH_MAX_RESULTS = 50
LABEL_SEARCH_MAX_RESULTS = 10
BAD_REQUEST_SUMMARY_MAX = 200

_NUMERIC_ID = re.compile(r"[1-9][0-9]*")
_PROJECT_KEY = re.compile(r"[A-Z][A-Z0-9_]*")
_TRANSITION_ID = re.compile(r"[0-9]+")
_OFFSET_WITHOUT_COLON = re.compile(r"(.*T.*\d)([+-]\d{2})(\d{2})")


class JiraError(Exception):
    """Jira 호출 실패. `status` 는 HTTP 상태(연결 오류면 None). 메시지에 토큰·헤더·응답 본문을 넣지 않는다."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class JiraUnauthorized(JiraError):
    """401 — 이메일·토큰이 맞지 않거나 토큰이 만료됐다."""


class JiraForbidden(JiraError):
    """403 — 권한(스코프) 부족."""


class JiraNotFound(JiraError):
    """404 — 프로젝트·이슈가 없거나 볼 권한이 없다."""


class JiraBadRequest(JiraError):
    """400. `str()` 은 `메서드 경로: HTTP 400` 뿐이고, `.message` 에 응답 `errorMessages`/`errors` 첫 문구(200자)를 둔다."""

    def __init__(self, text: str, status: int, message: str):
        super().__init__(text, status)
        self.message = message


class JiraRateLimited(JiraError):
    """429(또는 `Retry-After` 가 붙은 503). `retry_after` 는 초 — 헤더가 없거나 읽을 수 없으면 60."""

    def __init__(self, text: str, status: int, retry_after: int):
        super().__init__(text, status)
        self.retry_after = retry_after


class JiraUnavailable(JiraError):
    """5xx·연결 오류·timeout. 다음 바퀴에 다시 부른다."""


class JiraClient(Protocol):
    def myself(self) -> tuple[str, str]: ...

    def search_projects(self, query: str) -> list[JiraProjectRef]: ...

    def get_project(self, project_id: str) -> JiraProjectRef: ...

    def project_choices(self, project_key: str) -> JiraChoices: ...

    def search_issues(
        self, jql: str, *, next_page_token: str | None, site_url: str
    ) -> tuple[list[JiraIssueSnapshot], str | None]: ...

    def issue_status(self, issue_id: str) -> tuple[str, str]: ...

    def transitions(self, issue_id: str) -> list[JiraTransition]: ...

    def transition(self, issue_id: str, transition_id: str) -> None: ...

    def create_issue(
        self, project_id: str, issue_type_id: str, summary: str, description: dict, labels: Sequence[str]
    ) -> JiraIssueRef: ...

    def find_issues_by_label(self, project_id: str, label: str) -> list[JiraIssueRef]: ...

    def issue_link_types(self) -> list[str]: ...

    def link_issues(self, type_name: str, *, inward_issue_id: str, outward_issue_id: str) -> None: ...


def _checked(value: Any, pattern: re.Pattern, what: str) -> str:
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise ValueError(f"{what} 형식이 아닙니다")
    return value


def _retry_after(response: httpx.Response) -> int | None:
    try:
        value = int(response.headers["retry-after"])
    except (KeyError, ValueError):
        return None
    return value if value >= 0 else None


def _bad_request_summary(body: bytes, secrets: Sequence[str]) -> str:
    """400 본문의 `errorMessages` 첫 문구, 없으면 `errors` 첫 값. 비밀 문자열은 지우고 200자까지."""
    try:
        data = json.loads(body)
    except ValueError:
        return ""
    if not isinstance(data, dict):
        return ""
    candidates: list[Any] = []
    if isinstance(data.get("errorMessages"), list):
        candidates += data["errorMessages"]
    if isinstance(data.get("errors"), dict):
        candidates += list(data["errors"].values())
    text = next((c for c in candidates if isinstance(c, str) and c), "")
    for secret in secrets:
        text = text.replace(secret, "***")
    return text[:BAD_REQUEST_SUMMARY_MAX]


def _raise_for_status(method: str, path: str, response: httpx.Response, body: bytes, secrets: Sequence[str]) -> None:
    status = response.status_code
    if 200 <= status < 300:
        return
    text = f"{method} {path}: HTTP {status}"
    retry_after = _retry_after(response)
    if status == 429 or (status == 503 and retry_after is not None):
        raise JiraRateLimited(text, status, DEFAULT_RETRY_AFTER if retry_after is None else retry_after)
    if status == 400:
        raise JiraBadRequest(text, status, _bad_request_summary(body, secrets))
    if status == 401:
        raise JiraUnauthorized(text, status)
    if status == 403:
        raise JiraForbidden(text, status)
    if status == 404:
        raise JiraNotFound(text, status)
    if status >= 500:
        raise JiraUnavailable(text, status)
    raise JiraError(text, status)  # 3xx(리다이렉트는 따라가지 않는다)·409 등


def _request(
    client: httpx.Client, method: str, path: str, *, secrets: Sequence[str] = (), **kwargs: Any
) -> Any:
    """요청 한 번 → JSON(본문이 비면 None). 응답은 `MAX_RESPONSE_BYTES` 까지만 읽는다."""
    try:
        request = client.build_request(method, path, **kwargs)
        response = client.send(request, stream=True)
        try:
            declared = response.headers.get("content-length")
            if declared is not None and declared.isdigit() and int(declared) > MAX_RESPONSE_BYTES:
                raise JiraError(f"{method} {path}: 응답 크기 초과", response.status_code)
            body = bytearray()
            for chunk in response.iter_bytes():
                body += chunk
                if len(body) > MAX_RESPONSE_BYTES:
                    raise JiraError(f"{method} {path}: 응답 크기 초과", response.status_code)
        finally:
            response.close()
    except httpx.HTTPError as exc:
        raise JiraUnavailable(f"{method} {path}: {type(exc).__name__}") from None
    _raise_for_status(method, path, response, bytes(body), secrets)
    if not body.strip():
        return None
    try:
        return json.loads(body)
    except ValueError:
        raise JiraError(f"{method} {path}: 응답 형식 오류", response.status_code) from None


def tenant_info(site_url: str, *, transport: httpx.BaseTransport | None = None) -> str:
    """`GET {site}/_edge/tenant_info`(인증 없음) → cloudId(소문자 UUID). 사이트 형식 밖이면 ValueError(요청 없음)."""
    site = normalize_site_url(site_url)
    path = "/_edge/tenant_info"
    with httpx.Client(
        base_url=site, headers={"Accept": "application/json", "User-Agent": "runloom"},
        transport=transport, timeout=DEFAULT_TIMEOUT, follow_redirects=False,
    ) as client:
        data = _request(client, "GET", path)
    try:
        return normalize_cloud_id(data["cloudId"])
    except (KeyError, TypeError, AttributeError, ValueError):
        raise JiraError(f"GET {path}: 응답 형식 오류") from None


def _base_url(value: str) -> str:
    """게이트웨이 + cloudId, 또는 사이트 주소만. 아니면 ValueError."""
    if value.startswith(JIRA_GATEWAY):
        return JIRA_GATEWAY + normalize_cloud_id(value[len(JIRA_GATEWAY):])
    return normalize_site_url(value)


def _jira_time(value: Any) -> Any:
    """Jira `2026-09-29T10:00:00.000+0900` → RFC 3339 `+09:00`. 다른 모양은 그대로(스냅숏 검사가 거른다)."""
    if isinstance(value, str) and (match := _OFFSET_WITHOUT_COLON.fullmatch(value)):
        return f"{match[1]}{match[2]}:{match[3]}"
    return value


class HttpJiraClient:
    def __init__(
        self,
        base_url: str,
        email: str,
        token: str,
        *,
        transport: httpx.BaseTransport | None = None,
        timeout: float = DEFAULT_TIMEOUT,
    ):
        # 기준 주소는 `jira_connect.api_base_url(connection)` 이 만든 값 — 여기서도 두 모양만 받는다
        self._base = _base_url(base_url)
        if not valid_email(email) or not valid_token(token):
            raise ValueError("이메일·토큰 형식이 아닙니다")
        credential = base64.b64encode(f"{email}:{token}".encode()).decode()
        self._secrets = (token, credential)
        self._client = httpx.Client(
            base_url=self._base,
            headers={"Accept": "application/json", "User-Agent": "runloom", "Authorization": f"Basic {credential}"},
            transport=transport,
            timeout=timeout,
            follow_redirects=False,
        )

    def __repr__(self) -> str:
        return f"HttpJiraClient(base_url={self._base!r})"

    # ── 공개 동작 ──

    def myself(self) -> tuple[str, str]:
        path = f"{API}/myself"
        data = self._call("GET", path)
        return self._parse("GET", path, lambda: (self._text(data["accountId"]), self._text(data["displayName"])))

    def search_projects(self, query: str) -> list[JiraProjectRef]:
        path = f"{API}/project/search"
        data = self._call("GET", path, params={"query": query, "maxResults": PROJECT_SEARCH_MAX_RESULTS})
        return self._parse("GET", path, lambda: [self._project(item) for item in data["values"]])

    def get_project(self, project_id: str) -> JiraProjectRef:
        path = f"{API}/project/{_checked(project_id, _NUMERIC_ID, '프로젝트 id')}"
        data = self._call("GET", path)
        return self._parse("GET", path, lambda: self._project(data))

    def project_choices(self, project_key: str) -> JiraChoices:
        """`project/{key}/statuses` 한 번 — 이슈 유형(하위 작업 제외)과 상태 이름(처음 나온 순서, 중복 없음)."""
        path = f"{API}/project/{_checked(project_key, _PROJECT_KEY, '프로젝트 키')}/statuses"
        data = self._call("GET", path)

        def build() -> JiraChoices:
            types: list[JiraIssueType] = []
            statuses: list[str] = []
            for item in data:
                if item.get("subtask"):
                    continue
                types.append(JiraIssueType(id=item["id"], name=item["name"]))
                for status in item.get("statuses") or []:
                    if status["name"] not in statuses:
                        statuses.append(status["name"])
            return JiraChoices(issue_types=types, statuses=statuses)

        return self._parse("GET", path, build)

    def search_issues(
        self, jql: str, *, next_page_token: str | None, site_url: str
    ) -> tuple[list[JiraIssueSnapshot], str | None]:
        """`search/jql` 한 페이지. 마지막 페이지면 다음 토큰 None. `site_url` 은 형식만 검사한다(스냅숏에 주소 칸이 없다)."""
        normalize_site_url(site_url)
        path = f"{API}/search/jql"
        params: dict[str, Any] = {"jql": jql, "fields": ",".join(JIRA_FIELDS), "maxResults": SEARCH_MAX_RESULTS}
        if next_page_token is not None:
            params["nextPageToken"] = next_page_token
        data = self._call("GET", path, params=params)

        def build() -> tuple[list[JiraIssueSnapshot], str | None]:
            issues = [self._snapshot(item) for item in data["issues"]]
            token = data.get("nextPageToken")
            if data.get("isLast") is True or token is None:
                return issues, None
            return issues, self._text(token)

        return self._parse("GET", path, build)

    def issue_status(self, issue_id: str) -> tuple[str, str]:
        path = f"{API}/issue/{_checked(issue_id, _NUMERIC_ID, '이슈 id')}"
        data = self._call("GET", path, params={"fields": "status"})

        def build() -> tuple[str, str]:
            status = data["fields"]["status"]
            return self._text(status["name"]), self._text(status["statusCategory"]["key"])

        return self._parse("GET", path, build)

    def transitions(self, issue_id: str) -> list[JiraTransition]:
        path = f"{API}/issue/{_checked(issue_id, _NUMERIC_ID, '이슈 id')}/transitions"
        data = self._call("GET", path)
        return self._parse("GET", path, lambda: [
            JiraTransition(
                transition_id=item["id"], name=item["name"],
                to_name=item["to"]["name"], to_category=item["to"]["statusCategory"]["key"],
            )
            for item in data["transitions"]
        ])

    def transition(self, issue_id: str, transition_id: str) -> None:
        path = f"{API}/issue/{_checked(issue_id, _NUMERIC_ID, '이슈 id')}/transitions"
        body = {"transition": {"id": _checked(transition_id, _TRANSITION_ID, '전환 id')}}
        self._call("POST", path, json=body)

    def create_issue(
        self, project_id: str, issue_type_id: str, summary: str, description: dict, labels: Sequence[str]
    ) -> JiraIssueRef:
        path = f"{API}/issue"
        fields = {
            "project": {"id": _checked(project_id, _NUMERIC_ID, '프로젝트 id')},
            "issuetype": {"id": _checked(issue_type_id, _NUMERIC_ID, '이슈 유형 id')},
            "summary": summary,
            "description": description,
            "labels": list(labels),
        }
        data = self._call("POST", path, json={"fields": fields})
        return self._parse("POST", path, lambda: JiraIssueRef(issue_id=data["id"], key=data["key"]))

    def find_issues_by_label(self, project_id: str, label: str) -> list[JiraIssueRef]:
        """후속 조정용 — 라벨은 JQL 문자열 리터럴로 이스케이프한다. 한 페이지만 본다."""
        jql = f"project = {_checked(project_id, _NUMERIC_ID, '프로젝트 id')} AND labels = {jql_string(label)}"
        path = f"{API}/search/jql"
        data = self._call("GET", path, params={"jql": jql, "maxResults": LABEL_SEARCH_MAX_RESULTS})
        return self._parse("GET", path, lambda: [
            JiraIssueRef(issue_id=item["id"], key=item["key"]) for item in data["issues"]
        ])

    def issue_link_types(self) -> list[str]:
        path = f"{API}/issueLinkType"
        data = self._call("GET", path)
        return self._parse("GET", path, lambda: [self._text(item["name"]) for item in data["issueLinkTypes"]])

    def link_issues(self, type_name: str, *, inward_issue_id: str, outward_issue_id: str) -> None:
        body = {
            "type": {"name": type_name},
            "inwardIssue": {"id": _checked(inward_issue_id, _NUMERIC_ID, '이슈 id')},
            "outwardIssue": {"id": _checked(outward_issue_id, _NUMERIC_ID, '이슈 id')},
        }
        self._call("POST", f"{API}/issueLink", json=body)

    # ── 내부 ──

    def _call(self, method: str, path: str, **kwargs: Any) -> Any:
        return _request(self._client, method, path, secrets=self._secrets, **kwargs)

    @staticmethod
    def _parse(method: str, path: str, build):
        try:
            return build()
        except (KeyError, TypeError, AttributeError, ValueError, ValidationError):
            raise JiraError(f"{method} {path}: 응답 형식 오류") from None

    @staticmethod
    def _text(value: Any) -> str:
        if not isinstance(value, str) or not value:
            raise ValueError("빈 문자열")
        return value

    @staticmethod
    def _project(item: Any) -> JiraProjectRef:
        return JiraProjectRef(project_id=item["id"], key=item["key"], name=item["name"])

    @staticmethod
    def _snapshot(item: Any) -> JiraIssueSnapshot:
        fields = item["fields"]
        return JiraIssueSnapshot(
            issue_id=item["id"],
            key=item["key"],
            project_id=fields["project"]["id"],
            summary=fields["summary"],
            description_text=adf_to_text(fields.get("description")),
            status_name=fields["status"]["name"],
            status_category=fields["status"]["statusCategory"]["key"],
            issue_type=fields["issuetype"]["name"],
            priority=(fields.get("priority") or {}).get("name"),
            labels=list(fields.get("labels") or []),
            created=_jira_time(fields["created"]),
            updated=_jira_time(fields["updated"]),
        )
