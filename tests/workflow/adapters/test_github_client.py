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
    GitHubUnprocessable,
    HttpGitHubClient,
    IssueComment,
    IssueCursor,
    IssuePage,
    InstallationTokenProvider,
)
from workflow.contracts.github import GitHubIssueSnapshot, IssuePrLink, PullRequestRef

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


def test_repository_id_checks_access_to_an_allowed_repository():
    """PAT 연결(phase 11 step 7)의 확인 — `GET /repos/{o}/{r}` 한 번, 허용 목록 밖은 부르지 않는다."""
    rec = Recorder({})
    client = _client(rec)

    assert client.repository_id(REPO) == 9001
    assert [r.url.path for r in rec.calls] == [f"/repos/{REPO}"]
    with pytest.raises(GitHubRepositoryNotAllowed):
        client.repository_id("acme/other")
    assert len(rec.calls) == 1


def test_repository_id_without_access_is_classified():
    rec = Recorder({("GET", f"/repos/{REPO}"): httpx.Response(404, json={"message": "Not Found"})})

    with pytest.raises(GitHubNotFound):
        _client(rec).repository_id(REPO)


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


# ── 기준선: GraphQL 이슈 → 병합 PR (phase 9, step 7) ────────────────────────

OPENED_BEFORE = "2026-09-22T00:00:00Z"


def _pr(number: int, merged_at: str | None) -> dict:
    return {"number": number, "merged": merged_at is not None, "mergedAt": merged_at}


def _gql_issue(number: int, created_at: str, prs: list[dict]) -> dict:
    return {"number": number, "title": f"이슈 {number}", "createdAt": created_at,
            "closedByPullRequestsReferences": {"nodes": prs}}


def _gql_page(nodes: list[dict], end_cursor: str | None) -> dict:
    return {"data": {"repository": {"issues": {
        "pageInfo": {"hasNextPage": end_cursor is not None, "endCursor": end_cursor}, "nodes": nodes,
    }}}}


def _graphql(*pages: dict, status: int = 200, headers=None) -> Recorder:
    """부를 때마다 다음 페이지를 준다."""
    queue = list(pages)
    return Recorder({("POST", "/graphql"): lambda request: httpx.Response(status, json=queue.pop(0), headers=headers)})


def _body(request: httpx.Request) -> dict:
    return json.loads(request.content)


def test_issue_pr_links_follow_pages_with_end_cursor():
    rec = _graphql(
        _gql_page([_gql_issue(1, "2026-08-01T00:00:00Z", [_pr(10, "2026-08-02T00:00:00Z")])], "c1"),
        _gql_page([_gql_issue(2, "2026-08-03T00:00:00Z", [_pr(11, "2026-08-04T00:00:00Z")])], None),
    )

    links = _client(rec).list_issue_pr_links(REPO, opened_before=OPENED_BEFORE)

    assert links == [
        IssuePrLink(issue_number=1, issue_title="이슈 1", issue_opened_at="2026-08-01T00:00:00Z",
                    pr_number=10, pr_merged_at="2026-08-02T00:00:00Z"),
        IssuePrLink(issue_number=2, issue_title="이슈 2", issue_opened_at="2026-08-03T00:00:00Z",
                    pr_number=11, pr_merged_at="2026-08-04T00:00:00Z"),
    ]
    first, second = rec.calls
    assert first.url.host == "api.github.com" and first.url.path == "/graphql"
    assert first.headers["authorization"] == f"Bearer {TOKEN}"
    assert _body(first)["variables"] == {"owner": "acme", "name": "app", "cursor": None}
    assert _body(second)["variables"] == {"owner": "acme", "name": "app", "cursor": "c1"}
    query = _body(first)["query"]
    assert "states: CLOSED" in query and "closedByPullRequestsReferences" in query and "includeClosedPrs: true" in query


def test_issue_pr_links_skip_unmerged_and_pick_earliest_merge():
    rec = _graphql(_gql_page([
        _gql_issue(1, "2026-08-01T00:00:00Z", [_pr(20, None)]),  # 병합 안 된 PR 만 — 제외
        _gql_issue(2, "2026-08-01T00:00:00Z", []),  # PR 없음 — 제외
        _gql_issue(3, "2026-08-01T00:00:00Z", [
            _pr(30, "2026-08-09T00:00:00Z"), _pr(31, None), _pr(32, "2026-08-05T00:00:00Z"),
        ]),
    ], None))

    links = _client(rec).list_issue_pr_links(REPO, opened_before=OPENED_BEFORE)

    assert [(link.issue_number, link.pr_number, link.pr_merged_at) for link in links] == [
        (3, 32, "2026-08-05T00:00:00Z"),
    ]


def test_issue_pr_links_keep_only_issues_opened_before_boundary():
    merged = [_pr(40, "2026-09-25T00:00:00Z")]
    rec = _graphql(_gql_page([
        _gql_issue(1, "2026-09-21T23:59:59Z", merged),
        _gql_issue(2, "2026-09-22T00:00:00Z", merged),  # 경계와 같음 — 도입 뒤
        _gql_issue(3, "2026-09-23T00:00:00Z", merged),
        _gql_issue(4, "2026-09-22T08:30:00+09:00", merged),  # UTC 로 21일 23:30 — 도입 전
    ], None))

    links = _client(rec).list_issue_pr_links(REPO, opened_before=OPENED_BEFORE)

    assert [link.issue_number for link in links] == [1, 4]


@pytest.mark.parametrize(
    ("error_type", "expected"),
    [("NOT_FOUND", GitHubNotFound), ("FORBIDDEN", GitHubForbidden), ("SOMETHING_ELSE", GitHubError)],
)
def test_graphql_errors_are_classified_without_body(error_type, expected):
    rec = _graphql({"data": None, "errors": [{"type": error_type, "message": f"secret detail {TOKEN}"}]})

    with pytest.raises(expected) as info:
        _client(rec).list_issue_pr_links(REPO, opened_before=OPENED_BEFORE)

    assert type(info.value) is expected
    assert "secret detail" not in str(info.value) and TOKEN not in str(info.value)


def test_graphql_rate_limit_error_and_http_limit_are_rate_limited():
    rec = _graphql({"errors": [{"type": "RATE_LIMITED", "message": "API rate limit exceeded"}]},
                   headers={"x-ratelimit-reset": "1790000000"})
    with pytest.raises(GitHubRateLimited) as info:
        _client(rec).list_issue_pr_links(REPO, opened_before=OPENED_BEFORE)
    assert info.value.reset_epoch == 1790000000

    rec = _graphql({"message": "limit"}, status=403, headers={"x-ratelimit-remaining": "0", "retry-after": "60"})
    with pytest.raises(GitHubRateLimited) as info:
        _client(rec).list_issue_pr_links(REPO, opened_before=OPENED_BEFORE)
    assert info.value.retry_after_seconds == 60


@pytest.mark.parametrize(
    "payload",
    [
        {"data": {"repository": None}},
        {"data": {"repository": {"issues": {"pageInfo": {"hasNextPage": True, "endCursor": None}, "nodes": []}}}},
        _gql_page([{"number": 1}], None),
        _gql_page([_gql_issue(1, "어제", [_pr(2, "2026-08-02T00:00:00Z")])], None),
    ],
)
def test_graphql_bad_shape_is_plain_github_error(payload):
    with pytest.raises(GitHubError) as info:
        _client(_graphql(payload)).list_issue_pr_links(REPO, opened_before=OPENED_BEFORE)
    assert type(info.value) is GitHubError


@pytest.mark.parametrize("repo", ["acme/other", "../etc", "https://evil.example/acme/app"])
def test_issue_pr_links_refuse_repository_outside_allowed_list(repo):
    rec = Recorder({})

    with pytest.raises(GitHubRepositoryNotAllowed):
        _client(rec).list_issue_pr_links(repo, opened_before=OPENED_BEFORE)
    assert rec.calls == []


def test_issue_pr_links_rejects_bad_opened_before_before_request():
    rec = Recorder({})

    with pytest.raises(ValueError):
        _client(rec).list_issue_pr_links(REPO, opened_before="2026-09-22")
    assert rec.calls == []


def test_graphql_request_body_and_logs_have_no_token(caplog):
    caplog.set_level(logging.DEBUG)
    rec = _graphql({"errors": [{"type": "FORBIDDEN", "message": "no"}]})

    with pytest.raises(GitHubForbidden) as info:
        _client(rec).list_issue_pr_links(REPO, opened_before=OPENED_BEFORE)

    assert TOKEN not in rec.calls[0].content.decode()
    for text in (str(info.value), repr(info.value), caplog.text):
        assert TOKEN not in text



# ── 이슈 하나의 병합 PR (phase 9, step 11) ─────────────────────────────────


def _gql_one(state: str | None, prs: list[dict], number: int = 7) -> dict:
    issue = None if state is None else {**_gql_issue(number, "2026-10-01T00:00:00Z", prs), "state": state}
    return {"data": {"repository": {"issue": issue}}}


def test_issue_pr_link_picks_earliest_merged_pr_of_closed_issue():
    rec = _graphql(_gql_one("CLOSED", [
        _pr(30, "2026-10-09T00:00:00Z"), _pr(31, None), _pr(32, "2026-10-05T00:00:00Z"),
    ]))

    link = _client(rec).get_issue_pr_link(REPO, 7)

    assert link == IssuePrLink(issue_number=7, issue_title="이슈 7", issue_opened_at="2026-10-01T00:00:00Z",
                               pr_number=32, pr_merged_at="2026-10-05T00:00:00Z")
    (call,) = rec.calls
    assert call.url.host == "api.github.com" and call.url.path == "/graphql"
    assert _body(call)["variables"] == {"owner": "acme", "name": "app", "number": 7}
    query = _body(call)["query"]
    assert "issue(number: $number)" in query and "includeClosedPrs: true" in query


def test_issue_pr_link_single_merged_pr():
    rec = _graphql(_gql_one("CLOSED", [_pr(12, "2026-10-02T03:00:00Z")]))
    link = _client(rec).get_issue_pr_link(REPO, 7)
    assert (link.pr_number, link.pr_merged_at) == (12, "2026-10-02T03:00:00Z")


@pytest.mark.parametrize(
    ("state", "prs"),
    [
        ("CLOSED", [_pr(20, None)]),  # 병합 안 된 PR 만
        ("CLOSED", []),  # PR 없이 닫힘
        ("OPEN", [_pr(21, "2026-10-02T00:00:00Z")]),  # 아직 열려 있음 — 병합됐어도 완료가 아니다
    ],
)
def test_issue_pr_link_is_none_without_merge_or_while_open(state, prs):
    assert _client(_graphql(_gql_one(state, prs))).get_issue_pr_link(REPO, 7) is None


@pytest.mark.parametrize(
    ("error_type", "expected"),
    [("NOT_FOUND", GitHubNotFound), ("FORBIDDEN", GitHubForbidden), ("RATE_LIMITED", GitHubRateLimited),
     ("SOMETHING_ELSE", GitHubError)],
)
def test_issue_pr_link_graphql_errors_are_classified_without_body(error_type, expected):
    rec = _graphql({"data": None, "errors": [{"type": error_type, "message": f"secret detail {TOKEN}"}]})

    with pytest.raises(expected) as info:
        _client(rec).get_issue_pr_link(REPO, 7)

    assert type(info.value) is expected
    assert "secret detail" not in str(info.value) and TOKEN not in str(info.value)


@pytest.mark.parametrize(
    "payload",
    [
        {"data": {"repository": None}},
        _gql_one(None, []),  # 이슈 없음(오류 없이 null)
        {"data": {"repository": {"issue": {"number": 7, "state": "CLOSED"}}}},
        _gql_one("CLOSED", [_pr(2, "어제")]),
    ],
)
def test_issue_pr_link_bad_shape_is_github_error(payload):
    with pytest.raises(GitHubError):
        _client(_graphql(payload)).get_issue_pr_link(REPO, 7)


@pytest.mark.parametrize("repo", ["acme/other", "../etc", "https://evil.example/acme/app"])
def test_issue_pr_link_refuses_repository_outside_allowed_list(repo):
    rec = Recorder({})

    with pytest.raises(GitHubRepositoryNotAllowed):
        _client(rec).get_issue_pr_link(repo, 7)
    assert rec.calls == []


def test_issue_pr_link_request_body_and_errors_have_no_token(caplog):
    caplog.set_level(logging.DEBUG)
    rec = _graphql({"errors": [{"type": "FORBIDDEN", "message": "no"}]})

    with pytest.raises(GitHubForbidden) as info:
        _client(rec).get_issue_pr_link(REPO, 7)

    assert TOKEN not in rec.calls[0].content.decode()
    for text in (str(info.value), repr(info.value), caplog.text):
        assert TOKEN not in text

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


# ── 토큰 공급자 (phase 11 step 2) ─────────────────────────────────────────


class FakeProvider:
    def __init__(self, tokens):
        self.tokens = list(tokens)
        self.invalidated = 0

    def token(self) -> str:
        return self.tokens[0]

    def invalidate(self) -> None:
        self.invalidated += 1
        self.tokens.pop(0)


def test_token_provider_is_asked_on_every_request():
    rec = Recorder({("GET", f"/repos/{REPO}/issues/1"): httpx.Response(200, json=_issue(1))})
    provider = FakeProvider(["ghs_ONE"])
    client = HttpGitHubClient(provider, [REPO], transport=httpx.MockTransport(rec))

    client.get_issue(REPO, 1)
    provider.tokens[0] = "ghs_TWO"
    client.get_issue(REPO, 1)

    assert [r.headers["authorization"] for r in rec.calls if r.url.path.endswith("/issues/1")] == [
        "Bearer ghs_ONE", "Bearer ghs_TWO",
    ]


def test_token_provider_401_invalidates_and_retries_once():
    def handler(request):
        if request.headers["authorization"] == "Bearer ghs_OLD":
            return httpx.Response(401, json={})
        return httpx.Response(200, json=_issue(1))

    rec = Recorder({("GET", f"/repos/{REPO}/issues/1"): handler, ("GET", f"/repos/{REPO}"): lambda r: (
        httpx.Response(401, json={}) if r.headers["authorization"] == "Bearer ghs_OLD"
        else httpx.Response(200, json=REPO_JSON))})
    provider = FakeProvider(["ghs_OLD", "ghs_NEW"])

    assert HttpGitHubClient(provider, [REPO], transport=httpx.MockTransport(rec)).get_issue(REPO, 1).number == 1
    assert provider.invalidated == 1


def test_token_provider_401_twice_is_forbidden():
    rec = _failing(401)
    provider = FakeProvider(["ghs_OLD", "ghs_NEW"])

    with pytest.raises(GitHubForbidden):
        HttpGitHubClient(provider, [REPO], transport=httpx.MockTransport(rec)).get_issue(REPO, 1)
    assert len(rec.calls) == 2 and provider.invalidated == 1


def test_fixed_token_401_is_not_retried():
    rec = _failing(401)
    with pytest.raises(GitHubForbidden):
        _client(rec).get_issue(REPO, 1)
    assert len(rec.calls) == 1


def test_provider_token_not_in_repr_or_errors(caplog):
    caplog.set_level(logging.DEBUG)
    client = HttpGitHubClient(FakeProvider(["ghs_SECRET_PROVIDED", "ghs_SECRET_AGAIN"]), [REPO],
                              transport=httpx.MockTransport(_failing(401)))
    with pytest.raises(GitHubForbidden) as info:
        client.get_issue(REPO, 1)
    for text in (str(info.value), repr(info.value), caplog.text, repr(client)):
        assert "ghs_SECRET" not in text


def test_installation_token_provider_delegates_to_app_auth():
    class Auth:
        def __init__(self):
            self.calls = []

        def installation_token(self, installation_id):
            self.calls.append(("token", installation_id))
            return f"ghs_{installation_id}"

        def invalidate(self, installation_id):
            self.calls.append(("invalidate", installation_id))

    auth = Auth()
    provider = InstallationTokenProvider(auth, 77)

    assert provider.token() == "ghs_77"
    provider.invalidate()
    assert auth.calls == [("token", 77), ("invalidate", 77)]
    assert "ghs_" not in repr(provider)


def test_allowed_repos_can_be_installation_repositories():
    installed = ["acme/app", "acme/lib"]  # 호출자가 설치 저장소 목록을 허용 목록으로 넘긴다
    rec = Recorder({("GET", f"/repos/{REPO}/issues/1"): httpx.Response(200, json=_issue(1))})
    client = HttpGitHubClient(FakeProvider(["ghs_X"]), installed, transport=httpx.MockTransport(rec))

    assert client.get_issue(REPO, 1).number == 1
    with pytest.raises(GitHubRepositoryNotAllowed, match="허용 저장소"):
        client.get_issue("acme/other", 1)


# ── 초안 PR (phase 12 step 6, ADR-0018 결정 4) ─────────────────────────────

PULLS = f"/repos/{REPO}/pulls"


def _pull(number: int = 31, **overrides) -> dict:
    item = {
        "number": number, "html_url": f"https://github.com/{REPO}/pull/{number}", "state": "open",
        "draft": True, "merged_at": None, "head": {"ref": "task/task-1"}, "base": {"ref": "main"},
    }
    item.update(overrides)
    return item


def _unprocessable(message: str, *errors: str) -> httpx.Response:
    return httpx.Response(422, json={"message": message, "errors": [{"message": e} for e in errors]})


def _create_args(**overrides) -> dict:
    return {"head": "task/task-1", "base": "main", "title": "쿠폰 중복", "body": "Fixes #7\n요약", "draft": True,
            **overrides}


def test_default_branch_reads_the_repository():
    rec = Recorder({("GET", f"/repos/{REPO}"): httpx.Response(200, json={**REPO_JSON, "default_branch": "trunk"})})
    assert _client(rec).default_branch(REPO) == "trunk"


def test_default_branch_missing_is_a_format_error():
    rec = Recorder({})
    with pytest.raises(GitHubError, match="응답 형식 오류"):
        _client(rec).default_branch(REPO)


def test_create_pull_request_posts_head_base_draft_and_body():
    rec = Recorder({("POST", PULLS): httpx.Response(201, json=_pull())})
    pr = _client(rec).create_pull_request(REPO, **_create_args())
    assert pr == PullRequestRef(number=31, html_url=f"https://github.com/{REPO}/pull/31", state="open", draft=True,
                                merged_at=None)
    (call,) = rec.calls
    body = json.loads(call.content)
    assert body == {"head": "task/task-1", "base": "main", "title": "쿠폰 중복", "body": "Fixes #7\n요약",
                    "draft": True}
    assert body["body"].startswith("Fixes #7")


def test_create_pull_request_existing_head_returns_the_existing_pr():
    def pulls(request):
        assert _query(request) == {"head": "acme:task/task-1", "state": "all", "per_page": "100"}
        return httpx.Response(200, json=[_pull(40, draft=False)])

    rec = Recorder({
        ("POST", PULLS): _unprocessable("Validation Failed", "A pull request already exists for acme:task/task-1."),
        ("GET", PULLS): pulls,
    })
    pr = _client(rec).create_pull_request(REPO, **_create_args())
    assert (pr.number, pr.draft) == (40, False)
    assert [c.method for c in rec.calls] == ["POST", "GET"]


def test_create_pull_request_retries_without_draft_when_drafts_are_unsupported():
    answers = iter([
        _unprocessable("Validation Failed", "Draft pull requests are not supported in this repository."),
        httpx.Response(201, json=_pull(32, draft=False)),
    ])
    rec = Recorder({("POST", PULLS): lambda request: next(answers)})
    pr = _client(rec).create_pull_request(REPO, **_create_args())
    assert (pr.number, pr.draft) == (32, False)
    assert [json.loads(c.content)["draft"] for c in rec.calls] == [True, False]


def test_create_pull_request_other_422_without_existing_pr_is_unprocessable():
    rec = Recorder({
        ("POST", PULLS): _unprocessable("Validation Failed", "No commits between main and task/task-1"),
        ("GET", PULLS): httpx.Response(200, json=[]),
    })
    with pytest.raises(GitHubUnprocessable) as info:
        _client(rec).create_pull_request(REPO, **_create_args())
    assert str(info.value) == f"POST {PULLS}: HTTP 422"  # 본문은 메시지에 싣지 않는다
    assert "No commits" in info.value.message


def test_create_pull_request_403_is_forbidden():
    rec = Recorder({("POST", PULLS): httpx.Response(403, json={"message": "Resource not accessible by integration"})})
    with pytest.raises(GitHubForbidden) as info:
        _client(rec).create_pull_request(REPO, **_create_args())
    assert str(info.value) == f"POST {PULLS}: HTTP 403"


def test_find_pull_request_takes_the_newest_or_none():
    rec = Recorder({("GET", PULLS): httpx.Response(200, json=[_pull(41, state="closed"), _pull(40)])})
    assert _client(rec).find_pull_request(REPO, "task/task-1").number == 41
    rec = Recorder({("GET", PULLS): httpx.Response(200, json=[])})
    assert _client(rec).find_pull_request(REPO, "task/task-1") is None


def test_get_pull_request_reports_merge():
    merged = _pull(31, state="closed", draft=False, merged_at="2026-10-06T12:00:00Z")
    rec = Recorder({("GET", f"{PULLS}/31"): httpx.Response(200, json=merged)})
    pr = _client(rec).get_pull_request(REPO, 31)
    assert (pr.state, pr.merged_at) == ("closed", "2026-10-06T12:00:00Z")


def test_pull_request_bad_shape_is_a_format_error():
    rec = Recorder({("GET", f"{PULLS}/31"): httpx.Response(200, json={"number": 31})})
    with pytest.raises(GitHubError, match="응답 형식 오류"):
        _client(rec).get_pull_request(REPO, 31)


def test_pull_request_calls_stay_inside_allowed_repositories():
    rec = Recorder({})
    with pytest.raises(GitHubRepositoryNotAllowed):
        _client(rec).create_pull_request("acme/other", **_create_args())
    assert rec.calls == []
