"""jira_client.py — Jira Cloud REST 경계 (ARCHITECTURE "Jira 소스 — phase 18" 계약·클라이언트·오류 분류).

실제 Jira 를 부르지 않는다 — 모두 httpx `MockTransport`.
"""

import base64
import json

import httpx
import pytest

from workflow.adapters import jira_client
from workflow.adapters.jira_client import (
    HttpJiraClient,
    JiraBadRequest,
    JiraError,
    JiraForbidden,
    JiraNotFound,
    JiraRateLimited,
    JiraUnauthorized,
    JiraUnavailable,
    tenant_info,
)
from workflow.contracts.jira import JIRA_FIELDS, JiraIssueRef, JiraTransition

SITE = "https://acme.atlassian.net"
CLOUD_ID = "0f1e2d3c-4b5a-6978-8a9b-0c1d2e3f4a5b"
GATEWAY_BASE = f"https://api.atlassian.com/ex/jira/{CLOUD_ID}"
EMAIL = "dev@example.com"
TOKEN = "ATATT3xFfGF0-super-secret-token"
BASIC = "Basic " + base64.b64encode(f"{EMAIL}:{TOKEN}".encode()).decode()


class Recorder:
    def __init__(self, handler):
        self.handler = handler
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.handler(request)


def make(handler, base_url: str = GATEWAY_BASE) -> tuple[HttpJiraClient, Recorder]:
    rec = Recorder(handler)
    return HttpJiraClient(base_url, EMAIL, TOKEN, transport=httpx.MockTransport(rec)), rec


def ok(payload, status: int = 200) -> httpx.Response:
    return httpx.Response(status, json=payload)


def issue_item(issue_id: str = "10001", key: str = "PROJ-1", **fields) -> dict:
    base = {
        "summary": "로그인 버튼이 안 눌림",
        "description": {
            "type": "doc", "version": 1,
            "content": [{"type": "paragraph", "content": [{"type": "text", "text": "재현: 버튼 클릭"}]}],
        },
        "status": {"name": "할 일", "statusCategory": {"key": "new"}},
        "issuetype": {"name": "Bug"},
        "priority": {"name": "High"},
        "labels": ["frontend"],
        "created": "2026-09-29T10:00:00.000+0900",
        "updated": "2026-09-29T11:30:15.123+0900",
        "project": {"id": "10000"},
    }
    base.update(fields)
    return {"id": issue_id, "key": key, "fields": base}


# ── 생성자: 기준 주소 ──


@pytest.mark.parametrize("base", [GATEWAY_BASE, SITE, SITE + "/"])
def test_accepts_gateway_or_site_base(base):
    client, rec = make(lambda r: ok({"accountId": "a-1", "displayName": "Dev"}), base_url=base)
    client.myself()
    url = rec.requests[0].url
    assert str(url).startswith(base.rstrip("/") + "/rest/api/3/myself")


@pytest.mark.parametrize("base", [
    "https://evil.example.com",
    "http://acme.atlassian.net",
    "https://acme.atlassian.net:8443",
    "https://acme.atlassian.net/jira",
    "https://api.atlassian.com/ex/jira/not-a-uuid",
    "https://api.atlassian.com/ex/jira/",
    f"https://api.atlassian.com/ex/jira/{CLOUD_ID}/x",
    "https://acme.atlassian.net.evil.com",
])
def test_rejects_other_bases(base):
    with pytest.raises(ValueError):
        HttpJiraClient(base, EMAIL, TOKEN)


def test_rejects_bad_email_or_token():
    with pytest.raises(ValueError):
        HttpJiraClient(SITE, "not-an-email", TOKEN)
    with pytest.raises(ValueError):
        HttpJiraClient(SITE, EMAIL, "has space")


def test_repr_has_no_token_or_email():
    client, _ = make(lambda r: ok({}))
    assert TOKEN not in repr(client)
    assert "Basic" not in repr(client)


# ── tenant_info ──


def test_tenant_info_returns_cloud_id_without_auth():
    rec = Recorder(lambda r: ok({"cloudId": CLOUD_ID.upper()}))
    assert tenant_info(SITE + "/", transport=httpx.MockTransport(rec)) == CLOUD_ID
    request = rec.requests[0]
    assert str(request.url) == f"{SITE}/_edge/tenant_info"
    assert "authorization" not in request.headers


def test_tenant_info_rejects_other_hosts_before_sending():
    rec = Recorder(lambda r: ok({"cloudId": CLOUD_ID}))
    with pytest.raises(ValueError):
        tenant_info("https://evil.example.com", transport=httpx.MockTransport(rec))
    assert rec.requests == []


def test_tenant_info_rejects_bad_cloud_id_and_classifies_errors():
    bad = Recorder(lambda r: ok({"cloudId": "../../etc"}))
    with pytest.raises(JiraError):
        tenant_info(SITE, transport=httpx.MockTransport(bad))
    missing = Recorder(lambda r: httpx.Response(404))
    with pytest.raises(JiraNotFound):
        tenant_info(SITE, transport=httpx.MockTransport(missing))


def test_tenant_info_does_not_follow_redirects():
    rec = Recorder(lambda r: httpx.Response(302, headers={"Location": "https://evil.example.com/"}))
    with pytest.raises(JiraError):
        tenant_info(SITE, transport=httpx.MockTransport(rec))
    assert len(rec.requests) == 1


# ── 요청 모양 ──


def test_myself_uses_basic_auth_and_returns_account():
    client, rec = make(lambda r: ok({"accountId": "5b10ac8d82e05b22cc7d4ef5", "displayName": "개발자"}))
    assert client.myself() == ("5b10ac8d82e05b22cc7d4ef5", "개발자")
    request = rec.requests[0]
    assert request.method == "GET"
    assert request.url.path == f"/ex/jira/{CLOUD_ID}/rest/api/3/myself"
    assert request.headers["authorization"] == BASIC
    assert request.headers["accept"] == "application/json"


def test_search_projects_query_and_shape():
    client, rec = make(lambda r: ok({"values": [
        {"id": "10000", "key": "PROJ", "name": "프로젝트"},
        {"id": "10001", "key": "OPS", "name": "운영"},
    ]}))
    projects = client.search_projects("pro j")
    assert [(p.project_id, p.key, p.name) for p in projects] == [("10000", "PROJ", "프로젝트"), ("10001", "OPS", "운영")]
    request = rec.requests[0]
    assert request.url.path.endswith("/rest/api/3/project/search")
    assert request.url.params["query"] == "pro j"
    assert request.url.params["maxResults"] == "50"


def test_get_project():
    client, rec = make(lambda r: ok({"id": "10000", "key": "PROJ", "name": "프로젝트"}))
    project = client.get_project("10000")
    assert (project.project_id, project.key, project.name) == ("10000", "PROJ", "프로젝트")
    assert rec.requests[0].url.path.endswith("/rest/api/3/project/10000")


def test_project_choices_skips_subtasks_and_keeps_first_status_order():
    payload = [
        {"id": "10001", "name": "Bug", "subtask": False, "statuses": [
            {"name": "할 일"}, {"name": "진행 중"}, {"name": "완료"},
        ]},
        {"id": "10002", "name": "Sub-task", "subtask": True, "statuses": [{"name": "하위 전용"}]},
        {"id": "10003", "name": "Task", "subtask": False, "statuses": [
            {"name": "진행 중"}, {"name": "검토"}, {"name": "할 일"},
        ]},
    ]
    client, rec = make(lambda r: ok(payload))
    choices = client.project_choices("PROJ")
    assert [(t.id, t.name) for t in choices.issue_types] == [("10001", "Bug"), ("10003", "Task")]
    assert choices.statuses == ["할 일", "진행 중", "완료", "검토"]
    assert rec.requests[0].url.path.endswith("/rest/api/3/project/PROJ/statuses")


def test_search_issues_two_pages():
    pages = {
        None: {"issues": [issue_item("10001", "PROJ-1")], "nextPageToken": "tok-2", "isLast": False},
        "tok-2": {"issues": [issue_item("10002", "PROJ-2", priority=None, description=None)], "isLast": True},
    }
    client, rec = make(lambda r: ok(pages[r.url.params.get("nextPageToken")]))
    jql = "project = 10000 ORDER BY updated ASC, key ASC"

    first, token = client.search_issues(jql, next_page_token=None, site_url=SITE)
    assert token == "tok-2"
    second, token2 = client.search_issues(jql, next_page_token=token, site_url=SITE)
    assert token2 is None

    one = first[0]
    assert (one.issue_id, one.key, one.project_id) == ("10001", "PROJ-1", "10000")
    assert one.summary == "로그인 버튼이 안 눌림"
    assert one.description_text == "재현: 버튼 클릭"
    assert (one.status_name, one.status_category, one.issue_type) == ("할 일", "new", "Bug")
    assert one.priority == "High" and one.labels == ["frontend"]
    assert one.created == "2026-09-29T10:00:00.000+09:00"
    assert one.updated == "2026-09-29T11:30:15.123+09:00"
    two = second[0]
    assert two.priority is None and two.description_text == ""

    for request in rec.requests:
        assert request.method == "GET"
        assert request.url.path.endswith("/rest/api/3/search/jql")
        assert request.url.params["jql"] == jql
        assert request.url.params["fields"] == ",".join(JIRA_FIELDS)
        assert request.url.params["maxResults"] == "100"
    assert "nextPageToken" not in rec.requests[0].url.params
    assert rec.requests[1].url.params["nextPageToken"] == "tok-2"


def test_search_issues_token_without_islast_still_continues():
    client, _ = make(lambda r: ok({"issues": [], "nextPageToken": "t"}))
    assert client.search_issues("project = 1", next_page_token=None, site_url=SITE) == ([], "t")


def test_search_issues_rejects_bad_site_url():
    client, rec = make(lambda r: ok({"issues": []}))
    with pytest.raises(ValueError):
        client.search_issues("project = 1", next_page_token=None, site_url="https://evil.example.com")
    assert rec.requests == []


def test_search_issues_bad_item_is_format_error():
    client, _ = make(lambda r: ok({"issues": [{"id": "x", "key": "PROJ-1", "fields": {}}]}))
    with pytest.raises(JiraError) as info:
        client.search_issues("project = 1", next_page_token=None, site_url=SITE)
    assert "응답 형식" in str(info.value)


def test_issue_status():
    client, rec = make(lambda r: ok({"id": "10001", "fields": {"status": {"name": "완료", "statusCategory": {"key": "done"}}}}))
    assert client.issue_status("10001") == ("완료", "done")
    request = rec.requests[0]
    assert request.url.path.endswith("/rest/api/3/issue/10001")
    assert request.url.params["fields"] == "status"


def test_transitions_and_transition():
    def handler(request):
        if request.method == "GET":
            return ok({"transitions": [
                {"id": "21", "name": "시작", "to": {"name": "진행 중", "statusCategory": {"key": "indeterminate"}}},
                {"id": "31", "name": "끝", "to": {"name": "완료", "statusCategory": {"key": "done"}}},
            ]})
        return httpx.Response(204)

    client, rec = make(handler)
    assert client.transitions("10001") == [
        JiraTransition(transition_id="21", name="시작", to_name="진행 중", to_category="indeterminate"),
        JiraTransition(transition_id="31", name="끝", to_name="완료", to_category="done"),
    ]
    client.transition("10001", "21")
    get, post = rec.requests
    assert get.url.path.endswith("/rest/api/3/issue/10001/transitions")
    assert post.method == "POST" and post.url.path.endswith("/rest/api/3/issue/10001/transitions")
    assert json.loads(post.content) == {"transition": {"id": "21"}}
    assert post.headers["content-type"] == "application/json"


def test_create_issue_body():
    client, rec = make(lambda r: ok({"id": "10050", "key": "PROJ-50", "self": "https://x"}, status=201))
    description = {"type": "doc", "version": 1, "content": []}
    ref = client.create_issue("10000", "10001", "후속 작업", description, ["runloom-RUN-7"])
    assert ref == JiraIssueRef(issue_id="10050", key="PROJ-50")
    request = rec.requests[0]
    assert request.method == "POST" and request.url.path.endswith("/rest/api/3/issue")
    assert json.loads(request.content) == {"fields": {
        "project": {"id": "10000"},
        "issuetype": {"id": "10001"},
        "summary": "후속 작업",
        "description": description,
        "labels": ["runloom-RUN-7"],
    }}


def test_find_issues_by_label_escapes_label():
    client, rec = make(lambda r: ok({"issues": [{"id": "10050", "key": "PROJ-50"}], "isLast": True}))
    assert client.find_issues_by_label("10000", "runloom-RUN-7") == [JiraIssueRef(issue_id="10050", key="PROJ-50")]
    request = rec.requests[0]
    assert request.url.path.endswith("/rest/api/3/search/jql")
    assert request.url.params["jql"] == 'project = 10000 AND labels = "runloom-RUN-7"'
    client.find_issues_by_label("10000", 'a" OR project = 1')
    assert rec.requests[1].url.params["jql"] == 'project = 10000 AND labels = "a\\" OR project = 1"'


def test_issue_link_types_and_link_issues():
    def handler(request):
        if request.method == "GET":
            return ok({"issueLinkTypes": [{"id": "1", "name": "Blocks"}, {"id": "2", "name": "Relates"}]})
        return httpx.Response(201)

    client, rec = make(handler)
    assert client.issue_link_types() == ["Blocks", "Relates"]
    client.link_issues("Relates", inward_issue_id="10001", outward_issue_id="10050")
    get, post = rec.requests
    assert get.url.path.endswith("/rest/api/3/issueLinkType")
    assert post.url.path.endswith("/rest/api/3/issueLink")
    assert json.loads(post.content) == {
        "type": {"name": "Relates"}, "inwardIssue": {"id": "10001"}, "outwardIssue": {"id": "10050"},
    }


# ── 경로에 넣는 값 검사 ──


@pytest.mark.parametrize("call", [
    lambda c: c.get_project("../myself"),
    lambda c: c.get_project("PROJ"),
    lambda c: c.project_choices("proj/../x"),
    lambda c: c.project_choices("10000"),
    lambda c: c.issue_status("PROJ-1"),
    lambda c: c.issue_status("1/../../x"),
    lambda c: c.transitions("0"),
    lambda c: c.transition("10001", "21?x=1"),
    lambda c: c.transition("abc", "21"),
    lambda c: c.create_issue("x", "10001", "s", {}, []),
    lambda c: c.create_issue("10000", "type", "s", {}, []),
    lambda c: c.find_issues_by_label("1 OR 1=1", "l"),
    lambda c: c.link_issues("Relates", inward_issue_id="a", outward_issue_id="10050"),
])
def test_bad_ids_and_keys_are_rejected_before_sending(call):
    client, rec = make(lambda r: ok({}))
    with pytest.raises(ValueError):
        call(client)
    assert rec.requests == []


# ── 오류 분류 ──


@pytest.mark.parametrize(("status", "error"), [
    (401, JiraUnauthorized),
    (403, JiraForbidden),
    (404, JiraNotFound),
    (400, JiraBadRequest),
    (429, JiraRateLimited),
    (500, JiraUnavailable),
    (502, JiraUnavailable),
    (503, JiraUnavailable),
    (409, JiraError),
    (302, JiraError),
])
def test_error_classification(status, error):
    client, _ = make(lambda r: httpx.Response(status, json={}))
    with pytest.raises(error) as info:
        client.myself()
    assert type(info.value) is error
    assert info.value.status == status
    assert str(info.value) == f"GET /rest/api/3/myself: HTTP {status}"


def test_all_errors_share_the_base():
    for cls in (JiraUnauthorized, JiraForbidden, JiraNotFound, JiraBadRequest, JiraRateLimited, JiraUnavailable):
        assert issubclass(cls, JiraError)


def test_bad_request_keeps_only_first_message_summary():
    long = "필수 칸이 비었습니다 " * 30
    client, _ = make(lambda r: httpx.Response(400, json={"errorMessages": [long, "둘째"], "errors": {"x": "y"}}))
    with pytest.raises(JiraBadRequest) as info:
        client.create_issue("10000", "10001", "s", {}, [])
    assert info.value.message == long[:200]
    assert long not in str(info.value)


def test_bad_request_falls_back_to_errors_dict():
    client, _ = make(lambda r: httpx.Response(400, json={"errorMessages": [], "errors": {"summary": "요약이 필요합니다"}}))
    with pytest.raises(JiraBadRequest) as info:
        client.create_issue("10000", "10001", "s", {}, [])
    assert info.value.message == "요약이 필요합니다"


def test_bad_request_non_json_has_empty_message():
    client, _ = make(lambda r: httpx.Response(400, text="<html>"))
    with pytest.raises(JiraBadRequest) as info:
        client.myself()
    assert info.value.message == ""


@pytest.mark.parametrize(("headers", "expected"), [
    ({"Retry-After": "17"}, 17),
    ({}, 60),
    ({"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}, 60),
    ({"Retry-After": "-5"}, 60),
])
def test_rate_limited_retry_after(headers, expected):
    client, _ = make(lambda r: httpx.Response(429, headers=headers))
    with pytest.raises(JiraRateLimited) as info:
        client.myself()
    assert info.value.retry_after == expected


def test_503_with_retry_after_is_rate_limited():
    client, _ = make(lambda r: httpx.Response(503, headers={"Retry-After": "30"}))
    with pytest.raises(JiraRateLimited) as info:
        client.myself()
    assert info.value.retry_after == 30
    assert info.value.status == 503


@pytest.mark.parametrize("exc", [httpx.ConnectError("boom"), httpx.ReadTimeout("slow")])
def test_network_errors_are_unavailable(exc):
    def handler(request):
        raise exc

    client, _ = make(handler)
    with pytest.raises(JiraUnavailable) as info:
        client.myself()
    assert info.value.status is None


def test_redirect_is_not_followed():
    client, rec = make(lambda r: httpx.Response(302, headers={"Location": "https://evil.example.com/steal"}))
    with pytest.raises(JiraError):
        client.myself()
    assert len(rec.requests) == 1


def test_401_is_not_retried():
    client, rec = make(lambda r: httpx.Response(401))
    with pytest.raises(JiraUnauthorized):
        client.myself()
    assert len(rec.requests) == 1


def test_non_json_success_is_format_error():
    client, _ = make(lambda r: httpx.Response(200, text="not json"))
    with pytest.raises(JiraError) as info:
        client.myself()
    assert "응답 형식" in str(info.value)


def test_oversized_response_is_rejected(monkeypatch):
    monkeypatch.setattr(jira_client, "MAX_RESPONSE_BYTES", 64)
    client, _ = make(lambda r: ok({"accountId": "a" * 200, "displayName": "x"}))
    with pytest.raises(JiraError) as info:
        client.myself()
    assert "크기" in str(info.value)


def test_errors_never_contain_token_or_authorization():
    def handler(request):
        return httpx.Response(400, json={"errorMessages": [f"echo {request.headers['authorization']}"]})

    client, _ = make(handler)
    with pytest.raises(JiraBadRequest) as info:
        client.myself()
    for text in (str(info.value), repr(info.value), info.value.message):
        assert TOKEN not in text
        assert BASIC.split()[1] not in text

    for status in (401, 403, 404, 429, 500):
        client, _ = make(lambda r, s=status: httpx.Response(s, text=TOKEN))
        with pytest.raises(JiraError) as info:
            client.myself()
        assert TOKEN not in str(info.value) and TOKEN not in repr(info.value)

    def broken(request):
        raise httpx.ConnectError(f"failed {request.headers['authorization']}")

    client, _ = make(broken)
    with pytest.raises(JiraUnavailable) as info:
        client.myself()
    assert TOKEN not in str(info.value) and BASIC.split()[1] not in str(info.value)
    assert info.value.__cause__ is None and info.value.__suppress_context__
