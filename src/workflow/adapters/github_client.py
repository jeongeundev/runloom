"""GitHub REST 클라이언트 — 중앙 서버 → `api.github.com` (ADR-0014, ARCHITECTURE "GitHub REST 경계").

- host 는 `https://api.github.com` 고정. 리다이렉트를 따라가지 않고(`follow_redirects=False`), Link 헤더의 다음 페이지는
  같은 host 일 때 그 `page` 값만 꺼내 쓴다. 저장소는 생성자로 받은 허용 목록(`WORKFLOW_GITHUB_REPOS`) 안에서만 부른다.
- 토큰은 `from_env` 가 환경변수 `WORKFLOW_GITHUB_TOKEN` 에서만 읽거나, 생성자에 `TokenProvider`(설치 토큰 — ADR-0017)를
  넘긴다. 공급자는 요청마다 부르고, 401 이면 `invalidate()` 뒤 한 번만 다시 보낸다. 오류 메시지는 `메서드 경로: HTTP 상태` 뿐 —
  토큰·헤더·응답 본문을 넣지 않는다. 프로세스를 띄우지 않으므로 도구 프로세스 환경으로 넘어갈 경로도 없다.
- 오류: `GitHubRateLimited`(429, 또는 403 + `x-ratelimit-remaining: 0`/`retry-after`)·`GitHubForbidden`(401·403)·
  `GitHubNotFound`(404·410)·`GitHubUnavailable`(5xx·연결 오류·timeout)·그 밖(3xx·422·응답 형식)은 `GitHubError`.
  재시도 여부·간격은 호출자(step 7 수집·step 12 전달)가 정한다.
- 기준선과 이슈별 병합 PR 조회(ADR-0015)만 GraphQL(`POST /graphql`)을 쓴다. 같은 헤더·허용 저장소·오류 분류이고, 200 응답의 `errors` 는
  `type` 으로 분류한다(`RATE_LIMITED`·`FORBIDDEN`·`NOT_FOUND`). 메시지는 여기도 `POST /graphql: …` 뿐이다.
"""

import json
import os
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Protocol
from urllib.parse import parse_qs, urlsplit

import httpx
from pydantic import TypeAdapter, ValidationError

from workflow.contracts.github import GitHubIssueSnapshot, IssuePrLink, RepositoryFullName
from workflow.contracts.v1 import parse_rfc3339_aware

API_HOST = "api.github.com"
API_VERSION = "2022-11-28"
PER_PAGE = 100

_REPO_NAME = TypeAdapter(RepositoryFullName)

# 닫힌 이슈를 생성 순으로. 이슈당 PR 은 첫 25개만 본다(이슈 하나를 닫은 PR 이 그보다 많은 경우는 다루지 않는다).
# `includeClosedPrs: true` 는 닫힌(병합 포함) PR 도 돌려받기 위해서다 — 병합 여부는 `merged` 로 거른다.
_ISSUE_PR_QUERY = """
query($owner: String!, $name: String!, $cursor: String) {
  repository(owner: $owner, name: $name) {
    issues(first: 100, after: $cursor, states: CLOSED, orderBy: {field: CREATED_AT, direction: ASC}) {
      pageInfo { hasNextPage endCursor }
      nodes {
        number
        title
        createdAt
        closedByPullRequestsReferences(first: 25, includeClosedPrs: true) {
          nodes { number merged mergedAt }
        }
      }
    }
  }
}
"""

# 이슈 하나(phase 9 step 11 — 도입 후 완료 시각). PR 노드 모양은 위 질의와 같다 — `_earliest_merge` 가 함께 해석한다.
_ONE_ISSUE_PR_QUERY = """
query($owner: String!, $name: String!, $number: Int!) {
  repository(owner: $owner, name: $name) {
    issue(number: $number) {
      number
      title
      createdAt
      state
      closedByPullRequestsReferences(first: 25, includeClosedPrs: true) {
        nodes { number merged mergedAt }
      }
    }
  }
}
"""


class GitHubError(Exception):
    """GitHub 호출 실패. 메시지에 토큰·헤더·응답 본문을 넣지 않는다."""


class GitHubRepositoryNotAllowed(GitHubError):
    """허용 목록 밖이거나 `owner/name` 모양이 아닌 저장소 — 요청을 보내지 않는다."""


class GitHubRateLimited(GitHubError):
    """primary·secondary rate limit. 둘 다 None 이면 최소 1분 기다린다(GitHub 권고)."""

    def __init__(self, message: str, retry_after_seconds: int | None, reset_epoch: int | None):
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds
        self.reset_epoch = reset_epoch


class GitHubForbidden(GitHubError):
    """401·403 — 토큰이 없거나 저장소 권한(Issues read/write, Metadata read)이 모자란다."""


class GitHubNotFound(GitHubError):
    """404·410 — 저장소·이슈가 없거나(권한 없는 비공개 저장소도 404) 삭제됐다."""


class GitHubUnavailable(GitHubError):
    """5xx·연결 오류·timeout. 다음 주기에 같은 커서로 다시 부른다."""


@dataclass(frozen=True)
class IssueCursor:
    """이슈 목록 위치. `since` 는 GitHub `since`(updated_at >= since), `etag` 는 같은 요청의 이전 ETag(304 용).
    DB(`github_sources.cursor`)에는 `to_str()` 로 저장한다. URL 은 담지 않는다."""

    since: str | None = None
    page: int = 1
    etag: str | None = None

    def to_str(self) -> str:
        return json.dumps(asdict(self), sort_keys=True)

    @classmethod
    def parse(cls, raw: str) -> "IssueCursor":
        try:
            data = json.loads(raw)
        except ValueError:
            raise ValueError("커서가 JSON 이 아닙니다") from None
        if not isinstance(data, dict) or set(data) != {"since", "page", "etag"}:
            raise ValueError("커서 키는 since·page·etag 여야 합니다")
        since, page, etag = data["since"], data["page"], data["etag"]
        if since is not None:
            if not isinstance(since, str):
                raise ValueError("커서 since 는 문자열이어야 합니다")
            parse_rfc3339_aware(since)
        if not isinstance(page, int) or isinstance(page, bool) or page < 1:
            raise ValueError("커서 page 는 1 이상 정수여야 합니다")
        if etag is not None and not isinstance(etag, str):
            raise ValueError("커서 etag 는 문자열이어야 합니다")
        return cls(since=since, page=page, etag=etag)


@dataclass(frozen=True)
class IssuePage:
    """Pull request 항목은 빼고 개수만 센다. `next_cursor` None 이면 마지막 페이지. 304 면 `not_modified` 이고 비어 있다."""

    issues: tuple[GitHubIssueSnapshot, ...]
    next_cursor: IssueCursor | None
    etag: str | None
    not_modified: bool
    skipped_pull_requests: int


@dataclass(frozen=True)
class IssueComment:
    comment_id: int
    body: str
    author_id: int | None
    author_login: str | None
    updated_at: str


@dataclass(frozen=True)
class CommentPage:
    """댓글은 id 오름차순. `next_cursor` 는 다음 페이지 번호, None 이면 마지막."""

    comments: tuple[IssueComment, ...]
    next_cursor: int | None


class TokenProvider(Protocol):
    """요청마다 부르는 토큰 공급자. `invalidate()` 는 401 을 받았을 때 캐시를 버리게 한다."""

    def token(self) -> str: ...

    def invalidate(self) -> None: ...


class _FixedToken:
    """문자열 토큰(PAT·환경변수). repr 에 값이 없고, 401 에 다시 보내지 않는다."""

    def __init__(self, value: str):
        self._value = value

    def __repr__(self) -> str:
        return "_FixedToken(***)"

    def token(self) -> str:
        return self._value

    def invalidate(self) -> None:
        pass


class InstallationTokenProvider:
    """`GitHubAppAuth` 의 설치 토큰(설치별 메모리 캐시)을 공급한다. repr 에 토큰이 없다."""

    def __init__(self, auth: Any, installation_id: int):
        self._auth = auth
        self.installation_id = installation_id

    def __repr__(self) -> str:
        return f"InstallationTokenProvider(installation_id={self.installation_id})"

    def token(self) -> str:
        return self._auth.installation_token(self.installation_id)

    def invalidate(self) -> None:
        self._auth.invalidate(self.installation_id)


class GitHubClient(Protocol):
    def list_issues(self, repo: str, cursor: IssueCursor | None) -> IssuePage: ...

    def get_issue(self, repo: str, number: int) -> GitHubIssueSnapshot: ...

    def list_comments(self, repo: str, number: int, cursor: int | None) -> CommentPage: ...

    def create_comment(self, repo: str, number: int, body: str) -> int: ...

    def update_comment(self, repo: str, comment_id: int, body: str) -> None: ...

    def list_issue_pr_links(self, repo: str, *, opened_before: str) -> list[IssuePrLink]: ...

    def get_issue_pr_link(self, repo: str, number: int) -> IssuePrLink | None: ...


def _header_int(response: httpx.Response, name: str) -> int | None:
    try:
        return int(response.headers[name])
    except (KeyError, ValueError):
        return None


def check_response(method: str, path: str, response: httpx.Response) -> httpx.Response:
    """상태 코드 → 오류 분류. 메시지는 `메서드 경로: HTTP 상태` 뿐이다(github_app 도 같이 쓴다)."""
    status = response.status_code
    if 200 <= status < 300 or status == 304:
        return response
    message = f"{method} {path}: HTTP {status}"
    retry_after = _header_int(response, "retry-after")
    if status == 429 or (status == 403 and (retry_after is not None or response.headers.get("x-ratelimit-remaining") == "0")):
        raise GitHubRateLimited(message, retry_after, _header_int(response, "x-ratelimit-reset"))
    if status in (401, 403):
        raise GitHubForbidden(message)
    if status in (404, 410):
        raise GitHubNotFound(message)
    if status >= 500:
        raise GitHubUnavailable(message)
    raise GitHubError(message)  # 3xx(리다이렉트는 따라가지 않는다)·422 등


def next_page(response: httpx.Response, path: str) -> int | None:
    """Link 헤더의 rel="next" 에서 page 만 꺼내 같은 경로로 부른다(URL 자체는 쓰지 않음). 다른 host 는 거부한다."""
    link = response.links.get("next")
    if link is None:
        return None
    url = urlsplit(link["url"])
    if url.scheme != "https" or url.hostname != API_HOST:
        raise GitHubError(f"GET {path}: 다음 페이지가 {API_HOST} 밖을 가리킵니다")
    try:
        page = int(parse_qs(url.query)["page"][0])
    except (KeyError, ValueError):
        raise GitHubError(f"GET {path}: 다음 페이지 번호가 없습니다") from None
    if page < 1:
        raise GitHubError(f"GET {path}: 다음 페이지 번호가 없습니다")
    return page


def _earliest_merge(node: Any) -> IssuePrLink | None:
    """이슈 노드 → 그 이슈를 닫은 병합 PR 중 가장 이른 병합 하나. 병합 PR 이 없으면 None.
    형식 오류는 KeyError·TypeError·ValueError·ValidationError 로 올린다 — 호출자가 GitHubError 로 바꾼다."""
    merged = [pr for pr in node["closedByPullRequestsReferences"]["nodes"] if pr["merged"]]
    if not merged:
        return None
    first = min(merged, key=lambda pr: datetime.fromisoformat(parse_rfc3339_aware(pr["mergedAt"])))
    return IssuePrLink(
        issue_number=node["number"], issue_title=node["title"], issue_opened_at=node["createdAt"],
        pr_number=first["number"], pr_merged_at=first["mergedAt"],
    )


class HttpGitHubClient:
    def __init__(
        self,
        token: str | TokenProvider,
        allowed_repos: Iterable[str],
        *,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 10.0,
    ):
        # 허용 목록은 호출자가 고른다 — 환경변수 `WORKFLOW_GITHUB_REPOS` 또는 설치 저장소 목록
        self._retry_on_401 = not isinstance(token, str)
        self._token: TokenProvider = _FixedToken(token) if isinstance(token, str) else token
        self._allowed = frozenset(_REPO_NAME.validate_python(repo).lower() for repo in allowed_repos)
        self._repository_ids: dict[str, int] = {}
        self._client = httpx.Client(
            base_url=f"https://{API_HOST}",
            headers={
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": API_VERSION,
                "User-Agent": "runloom",
            },
            transport=transport,
            timeout=timeout,
            follow_redirects=False,
        )

    def __repr__(self) -> str:
        return f"HttpGitHubClient(repos={sorted(self._allowed)})"

    @classmethod
    def from_env(
        cls, env: Mapping[str, str] = os.environ, *, transport: httpx.BaseTransport | None = None
    ) -> "HttpGitHubClient":
        token = env.get("WORKFLOW_GITHUB_TOKEN", "")
        if not token:
            raise ValueError("WORKFLOW_GITHUB_TOKEN 이 비어 있습니다")
        repos = [repo.strip() for repo in env.get("WORKFLOW_GITHUB_REPOS", "").split(",") if repo.strip()]
        return cls(token, repos, transport=transport)

    # ── 공개 동작 ──

    def list_issues(self, repo: str, cursor: IssueCursor | None) -> IssuePage:
        cursor = cursor or IssueCursor()
        path = f"/repos/{self._repo(repo)}/issues"
        params: dict[str, Any] = {
            "state": "all", "sort": "updated", "direction": "asc", "per_page": PER_PAGE, "page": cursor.page,
        }
        if cursor.since is not None:
            params["since"] = cursor.since
        headers = {"If-None-Match": cursor.etag} if cursor.etag else {}
        response = self._call("GET", path, params=params, headers=headers)
        if response.status_code == 304:
            return IssuePage(issues=(), next_cursor=None, etag=cursor.etag, not_modified=True, skipped_pull_requests=0)
        items = self._json_list(response, path)
        issues = tuple(self._snapshot(repo, item, path) for item in items if "pull_request" not in item)
        next_page = self._next_page(response, path)
        return IssuePage(
            issues=issues,
            next_cursor=None if next_page is None else IssueCursor(since=cursor.since, page=next_page),
            etag=response.headers.get("etag"),
            not_modified=False,
            skipped_pull_requests=len(items) - len(issues),
        )

    def get_issue(self, repo: str, number: int) -> GitHubIssueSnapshot:
        path = f"/repos/{self._repo(repo)}/issues/{int(number)}"
        response = self._call("GET", path)
        try:
            item = response.json()
        except ValueError:
            raise GitHubError(f"GET {path}: 응답 형식 오류") from None
        return self._snapshot(repo, item, path)

    def list_comments(self, repo: str, number: int, cursor: int | None) -> CommentPage:
        path = f"/repos/{self._repo(repo)}/issues/{int(number)}/comments"
        response = self._call("GET", path, params={"per_page": PER_PAGE, "page": cursor or 1})
        try:
            comments = tuple(
                IssueComment(
                    comment_id=int(item["id"]),
                    body=item.get("body") or "",
                    author_id=(item.get("user") or {}).get("id"),
                    author_login=(item.get("user") or {}).get("login"),
                    updated_at=item["updated_at"],
                )
                for item in self._json_list(response, path)
            )
        except (KeyError, TypeError, ValueError):
            raise GitHubError(f"GET {path}: 응답 형식 오류") from None
        return CommentPage(comments=comments, next_cursor=self._next_page(response, path))

    def create_comment(self, repo: str, number: int, body: str) -> int:
        path = f"/repos/{self._repo(repo)}/issues/{int(number)}/comments"
        response = self._call("POST", path, json={"body": body})
        try:
            return int(response.json()["id"])
        except (KeyError, TypeError, ValueError):
            raise GitHubError(f"POST {path}: 응답 형식 오류") from None

    def update_comment(self, repo: str, comment_id: int, body: str) -> None:
        self._call("PATCH", f"/repos/{self._repo(repo)}/issues/comments/{int(comment_id)}", json={"body": body})

    def list_issue_pr_links(self, repo: str, *, opened_before: str) -> list[IssuePrLink]:
        """`opened_before` 이전에 열린 닫힌 이슈 중 병합 PR 로 닫힌 것. 이슈마다 가장 이른 병합 PR 하나, 이슈 번호순."""
        name = self._repo(repo)
        boundary = datetime.fromisoformat(parse_rfc3339_aware(opened_before))
        owner, short = name.split("/")
        links: list[IssuePrLink] = []
        cursor: str | None = None
        while True:
            issues = self._graphql(_ISSUE_PR_QUERY, {"owner": owner, "name": short, "cursor": cursor}, "issues")
            try:
                for node in issues["nodes"]:
                    link = _earliest_merge(node)
                    if link is None or datetime.fromisoformat(parse_rfc3339_aware(node["createdAt"])) >= boundary:
                        continue
                    links.append(link)
                page = issues["pageInfo"]
                has_next, cursor = page["hasNextPage"], page["endCursor"]
            except (KeyError, TypeError, ValueError, ValidationError):
                raise GitHubError("POST /graphql: 응답 형식 오류") from None
            if not has_next:
                return sorted(links, key=lambda link: link.issue_number)
            if not isinstance(cursor, str) or not cursor:
                raise GitHubError("POST /graphql: 다음 페이지 커서가 없습니다")

    def get_issue_pr_link(self, repo: str, number: int) -> IssuePrLink | None:
        """이슈 하나를 닫은 병합 PR 중 가장 이른 병합. 이슈가 열려 있거나 병합 PR 이 없으면 None."""
        owner, short = self._repo(repo).split("/")
        node = self._graphql(_ONE_ISSUE_PR_QUERY, {"owner": owner, "name": short, "number": int(number)}, "issue")
        try:
            return None if node["state"] != "CLOSED" else _earliest_merge(node)
        except (KeyError, TypeError, ValueError, ValidationError):
            raise GitHubError("POST /graphql: 응답 형식 오류") from None

    # ── 내부 ──

    def _repo(self, repo: str) -> str:
        try:
            name = _REPO_NAME.validate_python(repo)
        except ValidationError:
            raise GitHubRepositoryNotAllowed("저장소 이름은 owner/name 이어야 합니다") from None
        if name.lower() not in self._allowed:
            raise GitHubRepositoryNotAllowed(f"저장소 {name} 는 허용 저장소 목록에 없습니다")
        return name

    def _call(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        response = self._send(method, path, kwargs)
        if response.status_code == 401 and self._retry_on_401:
            self._token.invalidate()
            response = self._send(method, path, kwargs)
        return check_response(method, path, response)

    def _send(self, method: str, path: str, kwargs: dict[str, Any]) -> httpx.Response:
        headers = {**kwargs.get("headers", {}), "Authorization": f"Bearer {self._token.token()}"}
        try:
            return self._client.request(method, path, **{**kwargs, "headers": headers})
        except httpx.HTTPError as exc:
            raise GitHubUnavailable(f"{method} {path}: {type(exc).__name__}") from None

    def _graphql(self, query: str, variables: dict[str, Any], field: str) -> dict:
        """질의 한 번 → `repository.{field}` 객체. `errors` 는 type 으로만 분류하고 message 는 버린다."""
        response = self._call("POST", "/graphql", json={"query": query, "variables": variables})
        try:
            payload = response.json()
        except ValueError:
            payload = None
        if not isinstance(payload, dict):
            raise GitHubError("POST /graphql: 응답 형식 오류")
        errors = payload.get("errors")
        if errors:
            types = {e.get("type") for e in errors if isinstance(e, dict)} if isinstance(errors, list) else set()
            if "RATE_LIMITED" in types:
                raise GitHubRateLimited(
                    "POST /graphql: RATE_LIMITED", _header_int(response, "retry-after"),
                    _header_int(response, "x-ratelimit-reset"),
                )
            if "FORBIDDEN" in types:
                raise GitHubForbidden("POST /graphql: FORBIDDEN")
            if "NOT_FOUND" in types:
                raise GitHubNotFound("POST /graphql: NOT_FOUND")
            raise GitHubError("POST /graphql: GraphQL 오류")
        try:
            value = payload["data"]["repository"][field]
        except (KeyError, TypeError):
            raise GitHubError("POST /graphql: 응답 형식 오류") from None
        if not isinstance(value, dict):  # 이슈가 null 이어도 — 없는 이슈는 GitHub 가 NOT_FOUND 오류로 준다
            raise GitHubError("POST /graphql: 응답 형식 오류")
        return value

    def _json_list(self, response: httpx.Response, path: str) -> list:
        try:
            data = response.json()
        except ValueError:
            data = None
        if not isinstance(data, list) or not all(isinstance(item, dict) for item in data):
            raise GitHubError(f"GET {path}: 응답 형식 오류")
        return data

    def _next_page(self, response: httpx.Response, path: str) -> int | None:
        return next_page(response, path)

    def _repository_id(self, repo: str) -> int:
        key = repo.lower()
        if key not in self._repository_ids:
            path = f"/repos/{repo}"
            try:
                self._repository_ids[key] = int(self._call("GET", path).json()["id"])
            except (KeyError, TypeError, ValueError):
                raise GitHubError(f"GET {path}: 응답 형식 오류") from None
        return self._repository_ids[key]

    def _snapshot(self, repo: str, item: Any, path: str) -> GitHubIssueSnapshot:
        try:
            assignees = item.get("assignees") or []
            return GitHubIssueSnapshot(
                repository_id=self._repository_id(repo),
                repository_full_name=repo,
                issue_id=item["id"],
                number=item["number"],
                title=item["title"],
                body=item.get("body") or "",
                state=item["state"],
                labels=[label["name"] for label in item.get("labels") or []],
                assignee_ids=[a["id"] for a in assignees],
                assignee_logins=[a["login"] for a in assignees],
                html_url=item["html_url"],
                created_at=item["created_at"],
                updated_at=item["updated_at"],
                is_pull_request="pull_request" in item,
            )
        except (KeyError, TypeError, AttributeError, ValidationError):
            raise GitHubError(f"GET {path}: 응답 형식 오류") from None
