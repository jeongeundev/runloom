"""github_client — 중앙 서버 → api.github.com REST 경계 (ADR-0014, step 5). 실제 GitHub 없이 MockTransport 로 검사한다."""

import json
import logging
from urllib.parse import parse_qs

import httpx
import pytest

from workflow.adapters.github_client import (
    CommentPage,
    GitHubError,
    GitHubForbidden,
    GitHubNotFound,
    GitHubRateLimited,
    GitHubRepositoryNotAllowed,
    GitHubUnavailable,
    HttpGitHubClient,
    IssueComment,
    IssueCursor,
    IssuePage,
)
from workflow.contracts.github import GitHubIssueSnapshot

TOKEN = "github_pat_11TESTSECRETVALUE0123456789"
REPO = "acme/app"
REPO_JSON = {"id": 9001, "full_name": REPO}


def _issue(number: int, **overrides) -> dict:
    item = {
        "id": 5000 + number,
        "number": number,
        "title": f"버그 {number}",
        "body": "재현 방법",
        "state": "open",
        "labels": [{"id": 1, "name": "bug"}],
        "assignees": [{"id": 77, "login": "dev-a"}],
        "html_url": f"https://github.com/{REPO}/issues/{number}",
        "created_at": "2026-09-20T01:00:00Z",
        "updated_at": "2026-09-21T02:00:00Z",
    }
    item.update(overrides)
    return item


def _comment(comment_id: int, body: str) -> dict:
    return {
        "id": comment_id,
        "body": body,
        "user": {"id": 42, "login": "runloom-bot"},
        "created_at": "2026-09-21T03:00:00Z",
        "updated_at": "2026-09-21T03:05:00Z",
    }


class Recorder:
    """경로별 응답 표. 부른 요청을 모두 남긴다. `/repos/acme/app` 은 기본으로 저장소 ID 를 준다."""

    def __init__(self, routes: dict):
        self.routes = {("GET", f"/repos/{REPO}"): httpx.Response(200, json=REPO_JSON), **routes}
        self.calls: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        response = self.routes[(request.method, request.url.path)]
        return response(request) if callable(response) else response


def _client(handler, repos=(REPO,)) -> HttpGitHubClient:
    return HttpGitHubClient(TOKEN, repos, transport=httpx.MockTransport(handler))


def _query(request: httpx.Request) -> dict:
    return {k: v[0] for k, v in parse_qs(request.url.query.decode()).items()}


def _next_link(page: int, host: str = "api.github.com") -> dict:
    return {"link": f'<https://{host}/repositories/9001/issues?state=all&page={page}>; rel="next", '
                    f'<https://{host}/repositories/9001/issues?state=all&page=9>; rel="last"'}


# ── 요청 모양 ──────────────────────────────────────────────────────────────


def test_list_issues_sends_fixed_host_headers_and_query():
    rec = Recorder({("GET", f"/repos/{REPO}/issues"): httpx.Response(200, json=[_issue(1)], headers={"etag": 'W/"e1"'})})

    page = _client(rec).list_issues(REPO, IssueCursor(since="2026-09-01T00:00:00Z"))

    request = rec.calls[0]
    assert request.url.scheme == "https" and request.url.host == "api.github.com"
    assert request.headers["authorization"] == f"Bearer {TOKEN}"
    assert request.headers["accept"] == "application/vnd.github+json"
    assert request.headers["x-github-api-version"] == "2022-11-28"
    assert "if-none-match" not in request.headers
    assert _query(request) == {
        "state": "all", "sort": "updated", "direction": "asc", "per_page": "100", "page": "1",
        "since": "2026-09-01T00:00:00Z",
    }
    assert isinstance(page, IssuePage)
    assert page.etag == 'W/"e1"' and page.not_modified is False and page.next_cursor is None


def test_issue_json_becomes_snapshot():
    rec = Recorder({("GET", f"/repos/{REPO}/issues"): httpx.Response(200, json=[_issue(3, body=None)])})

    page = _client(rec).list_issues(REPO, None)

    assert page.issues == (
        GitHubIssueSnapshot(
            repository_id=9001, repository_full_name=REPO, issue_id=5003, number=3, title="버그 3", body="",
            state="open", labels=["bug"], assignee_ids=[77], assignee_logins=["dev-a"],
            html_url=f"https://github.com/{REPO}/issues/3", created_at="2026-09-20T01:00:00Z",
            updated_at="2026-09-21T02:00:00Z", is_pull_request=False,
        ),
    )
    assert "since" not in _query(rec.calls[-1])


def test_repository_id_is_fetched_once_per_client():
    rec = Recorder({("GET", f"/repos/{REPO}/issues"): httpx.Response(200, json=[_issue(1)])})
    client = _client(rec)

    client.list_issues(REPO, None)
    client.list_issues(REPO, None)

    assert [r.url.path for r in rec.calls].count(f"/repos/{REPO}") == 1


def test_pull_request_items_are_excluded_from_list():
    items = [_issue(1), _issue(2, pull_request={"url": "https://api.github.com/repos/acme/app/pulls/2"})]
    rec = Recorder({("GET", f"/repos/{REPO}/issues"): httpx.Response(200, json=items)})

    page = _client(rec).list_issues(REPO, None)

    assert [i.number for i in page.issues] == [1]
    assert page.skipped_pull_requests == 1


# ── 페이지네이션·ETag ──────────────────────────────────────────────────────


def test_multiple_pages_follow_link_header_page():
    def issues(request: httpx.Request) -> httpx.Response:
        page = int(_query(request)["page"])
        if page == 1:
            return httpx.Response(200, json=[_issue(1)], headers=_next_link(2))
        return httpx.Response(200, json=[_issue(2)])

    rec = Recorder({("GET", f"/repos/{REPO}/issues"): issues})
    client = _client(rec)

    first = client.list_issues(REPO, IssueCursor(since="2026-09-01T00:00:00Z"))
    assert first.next_cursor == IssueCursor(since="2026-09-01T00:00:00Z", page=2)
    second = client.list_issues(REPO, first.next_cursor)

    assert [i.number for i in first.issues + second.issues] == [1, 2]
    assert second.next_cursor is None
    assert _query(rec.calls[-1])["page"] == "2"


def test_not_modified_returns_empty_page_with_same_etag():
    rec = Recorder({("GET", f"/repos/{REPO}/issues"): httpx.Response(304)})

    page = _client(rec).list_issues(REPO, IssueCursor(since=None, page=1, etag='W/"e1"'))

    assert rec.calls[-1].headers["if-none-match"] == 'W/"e1"'
    assert page == IssuePage(issues=(), next_cursor=None, etag='W/"e1"', not_modified=True, skipped_pull_requests=0)
    assert [r.url.path for r in rec.calls] == [f"/repos/{REPO}/issues"]  # 304 에는 저장소 조회도 없다


def test_cursor_round_trips_as_string_and_rejects_garbage():
    cursor = IssueCursor(since="2026-09-01T00:00:00Z", page=3, etag='W/"x"')

    assert IssueCursor.parse(cursor.to_str()) == cursor
    for bad in ("", "not json", '{"since": null, "page": 0, "etag": null}', '{"since": "yesterday", "page": 1, "etag": null}',
                '{"since": null, "page": 1, "etag": null, "url": "https://evil.example"}'):
        with pytest.raises(ValueError):
            IssueCursor.parse(bad)


def test_list_comments_pages_and_parses():
    def comments(request: httpx.Request) -> httpx.Response:
        if _query(request)["page"] == "1":
            return httpx.Response(200, json=[_comment(11, "<!-- runloom:task=t-1 -->\n본문")], headers=_next_link(2))
        return httpx.Response(200, json=[_comment(12, "다른 댓글")])

    rec = Recorder({("GET", f"/repos/{REPO}/issues/7/comments"): comments})
    client = _client(rec)

    first = client.list_comments(REPO, 7, None)
    second = client.list_comments(REPO, 7, first.next_cursor)

    assert first == CommentPage(
        comments=(IssueComment(comment_id=11, body="<!-- runloom:task=t-1 -->\n본문", author_id=42,
                               author_login="runloom-bot", updated_at="2026-09-21T03:05:00Z"),),
        next_cursor=2,
    )
    assert [c.comment_id for c in second.comments] == [12] and second.next_cursor is None
    assert _query(rec.calls[0])["per_page"] == "100"


def test_get_issue_returns_snapshot_including_pull_request_flag():
    rec = Recorder({("GET", f"/repos/{REPO}/issues/2"): httpx.Response(200, json=_issue(2, pull_request={}))})

    snapshot = _client(rec).get_issue(REPO, 2)

    assert snapshot.number == 2 and snapshot.is_pull_request is True


def test_create_and_update_comment():
    rec = Recorder({
        ("POST", f"/repos/{REPO}/issues/7/comments"): httpx.Response(201, json=_comment(99, "b")),
        ("PATCH", f"/repos/{REPO}/issues/comments/99"): httpx.Response(200, json=_comment(99, "b2")),
    })
    client = _client(rec)

    assert client.create_comment(REPO, 7, "b") == 99
    assert client.update_comment(REPO, 99, "b2") is None

    assert [json.loads(r.content) for r in rec.calls] == [{"body": "b"}, {"body": "b2"}]


# ── 오류 분류 ─────────────────────────────────────────────────────────────


def _failing(status: int, headers=None, body=None):
    return Recorder({("GET", f"/repos/{REPO}/issues/1"): httpx.Response(status, headers=headers or {}, json=body)})


def test_429_is_rate_limited_with_retry_after():
    with pytest.raises(GitHubRateLimited) as info:
        _client(_failing(429, {"retry-after": "30"})).get_issue(REPO, 1)
    assert (info.value.retry_after_seconds, info.value.reset_epoch) == (30, None)


def test_403_with_remaining_zero_is_rate_limited_with_reset():
    headers = {"x-ratelimit-remaining": "0", "x-ratelimit-reset": "1790000000"}
    with pytest.raises(GitHubRateLimited) as info:
        _client(_failing(403, headers)).get_issue(REPO, 1)
    assert (info.value.retry_after_seconds, info.value.reset_epoch) == (None, 1790000000)


@pytest.mark.parametrize("status", [401, 403])
def test_403_without_rate_headers_is_forbidden(status):
    headers = {"x-ratelimit-remaining": "4999"}
    with pytest.raises(GitHubForbidden):
        _client(_failing(status, headers, {"message": "Resource not accessible by personal access token"})).get_issue(REPO, 1)


@pytest.mark.parametrize("status", [404, 410])
def test_404_and_410_are_not_found(status):
    with pytest.raises(GitHubNotFound):
        _client(_failing(status)).get_issue(REPO, 1)


@pytest.mark.parametrize("status", [500, 502, 503])
def test_5xx_is_unavailable(status):
    with pytest.raises(GitHubUnavailable):
        _client(_failing(status)).get_issue(REPO, 1)


def test_timeout_is_unavailable():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    with pytest.raises(GitHubUnavailable) as info:
        _client(handler).get_issue(REPO, 1)
    assert str(info.value) == f"GET /repos/{REPO}/issues/1: ReadTimeout"


def test_unexpected_4xx_and_bad_json_are_plain_github_error():
    with pytest.raises(GitHubError) as info:
        _client(_failing(422)).get_issue(REPO, 1)
    assert type(info.value) is GitHubError

    rec = Recorder({("GET", f"/repos/{REPO}/issues/1"): httpx.Response(200, json={"id": 1})})
    with pytest.raises(GitHubError) as info:
        _client(rec).get_issue(REPO, 1)
    assert type(info.value) is GitHubError


# ── 범위: 저장소·host·리다이렉트 ─────────────────────────────────────────


@pytest.mark.parametrize("repo", ["acme/other", "../etc", "acme/app/../x", "https://evil.example/acme/app"])
def test_repository_outside_allowed_list_is_refused_before_any_request(repo):
    rec = Recorder({})
    client = _client(rec)

    with pytest.raises(GitHubRepositoryNotAllowed):
        client.list_issues(repo, None)
    with pytest.raises(GitHubRepositoryNotAllowed):
        client.create_comment(repo, 1, "x")
    assert rec.calls == []


def test_allowed_repository_match_is_case_insensitive():
    rec = Recorder({("GET", "/repos/Acme/App/issues/1"): httpx.Response(200, json=_issue(1)),
                    ("GET", "/repos/Acme/App"): httpx.Response(200, json=REPO_JSON)})

    assert _client(rec).get_issue("Acme/App", 1).number == 1


@pytest.mark.parametrize("status", [301, 302, 307])
def test_redirect_is_not_followed(status):
    rec = _failing(status, {"location": "https://evil.example/repos/acme/app/issues/1"})

    with pytest.raises(GitHubError, match=f"HTTP {status}"):
        _client(rec).get_issue(REPO, 1)
    assert len(rec.calls) == 1


def test_next_link_to_other_host_is_refused():
    rec = Recorder({("GET", f"/repos/{REPO}/issues"): httpx.Response(200, json=[_issue(1)], headers=_next_link(2, "evil.example"))})

    with pytest.raises(GitHubError, match="api.github.com"):
        _client(rec).list_issues(REPO, None)


# ── 비밀값 ────────────────────────────────────────────────────────────────


def test_token_never_appears_in_errors_logs_or_repr(caplog):
    caplog.set_level(logging.DEBUG)
    client = _client(_failing(403, {"x-ratelimit-remaining": "1"}, {"message": f"bad token {TOKEN}"}))

    with pytest.raises(GitHubForbidden) as info:
        client.get_issue(REPO, 1)

    for text in (str(info.value), repr(info.value), caplog.text, repr(client), repr(vars(client))):
        assert TOKEN not in text
    assert str(info.value) == f"GET /repos/{REPO}/issues/1: HTTP 403"


def test_from_env_reads_token_and_repos_only_from_env():
    rec = Recorder({("GET", f"/repos/{REPO}/issues/1"): httpx.Response(200, json=_issue(1))})
    env = {"WORKFLOW_GITHUB_TOKEN": TOKEN, "WORKFLOW_GITHUB_REPOS": f" {REPO} , acme/lib "}

    client = HttpGitHubClient.from_env(env, transport=httpx.MockTransport(rec))

    assert client.get_issue(REPO, 1).number == 1
    assert rec.calls[0].headers["authorization"] == f"Bearer {TOKEN}"
    with pytest.raises(ValueError, match="WORKFLOW_GITHUB_TOKEN"):
        HttpGitHubClient.from_env({"WORKFLOW_GITHUB_REPOS": REPO})
