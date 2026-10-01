"""server/jira_connect.py — 연결 확인·기준 주소·설정 이름 대조·화면 컨텍스트 (phase 18 step 4, ARCHITECTURE "연결과 호출 기준 주소").

실제 Jira 는 부르지 않는다 — `FakeJira`(httpx MockTransport 처리기)가 사이트(`acme.atlassian.net`)와 게이트웨이
(`api.atlassian.com/ex/jira/{cloudId}`)를 흉내 낸다. 웹 경로 테스트(`test_web_jira_connect`)도 이것을 쓴다.
"""

from types import SimpleNamespace

import httpx
import pytest

from workflow.adapters import repo
from workflow.adapters.jira_client import JiraForbidden, JiraUnauthorized, JiraUnavailable
from workflow.contracts.jira import JiraChoices, JiraConnection, JiraIssueType
from workflow.server import jira_connect
from workflow.server.jira_connect import InvalidSetting, JiraConnectionFacts

SITE = "https://acme.atlassian.net"
CLOUD = "11111111-2222-4333-8444-555555555555"
GATEWAY = f"https://api.atlassian.com/ex/jira/{CLOUD}"
EMAIL = "dev@acme.com"
JIRA_TOKEN = "ATATT3xFfGF0SECRETjiraTOKEN0123456789"
ACCOUNT = "5b10ac8d82e05b22cc7d4ef5"
STATUSES = [
    {"id": "10001", "name": "버그", "subtask": False,
     "statuses": [{"name": "대기"}, {"name": "진행 중"}, {"name": "리뷰중"}, {"name": "종료"}]},
    {"id": "10002", "name": "작업", "subtask": False, "statuses": [{"name": "대기"}, {"name": "진행 중"}]},
    {"id": "10003", "name": "하위 작업", "subtask": True, "statuses": [{"name": "대기"}]},
]


class FakeJira:
    """주소(호스트+경로)별 응답. `status` 는 주소 앞부분 → 오류 상태, `projects` 는 검색 결과."""

    def __init__(self):
        self.status: dict[str, int] = {}
        self.projects = [{"id": "10000", "key": "SHOP", "name": "쇼핑몰"}, {"id": "10010", "key": "OPS", "name": "운영"}]
        self.statuses = STATUSES
        self.cloud = CLOUD
        self.calls: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        url = f"{request.url.scheme}://{request.url.host}{request.url.path}"
        for prefix, status in self.status.items():
            if url.startswith(prefix):
                return httpx.Response(status, json={"errorMessages": ["안 됨"]})
        if url == f"{SITE}/_edge/tenant_info":
            return httpx.Response(200, json={"cloudId": self.cloud})
        for base in (f"https://api.atlassian.com/ex/jira/{self.cloud}", SITE):
            if not url.startswith(base + "/rest/api/3/"):
                continue
            path = url[len(base) + len("/rest/api/3"):]
            if path == "/myself":
                return httpx.Response(200, json={"accountId": ACCOUNT, "displayName": "김개발"})
            if path == "/project/search":
                return httpx.Response(200, json={"values": self.projects, "isLast": True})
            for p in self.projects:
                if path == f"/project/{p['id']}":
                    return httpx.Response(200, json=p)
                if path == f"/project/{p['key']}/statuses":
                    return httpx.Response(200, json=self.statuses)
        return httpx.Response(404, json={"errorMessages": ["없음"]})

    def urls(self) -> list[str]:
        return [f"{r.method} {r.url.scheme}://{r.url.host}{r.url.path}" for r in self.calls]


@pytest.fixture
def jira() -> FakeJira:
    return FakeJira()


def transport(jira: FakeJira) -> httpx.MockTransport:
    return httpx.MockTransport(jira)


# --- 확인 -----------------------------------------------------------------------------------------


def test_verify_uses_the_gateway_when_it_accepts_the_token(jira):
    facts = jira_connect.verify(" HTTPS://Acme.atlassian.net/ ", EMAIL, JIRA_TOKEN, transport=transport(jira))

    assert facts == JiraConnectionFacts(site_url=SITE, cloud_id=CLOUD, api_base="gateway", email=EMAIL,
                                        account_id=ACCOUNT, display_name="김개발")
    assert jira.urls() == [f"GET {SITE}/_edge/tenant_info", f"GET {GATEWAY}/rest/api/3/myself"]
    assert "authorization" not in jira.calls[0].headers  # tenant_info 는 인증 없음
    assert JIRA_TOKEN not in repr(facts)


def test_verify_falls_back_to_the_site_on_gateway_401(jira):
    jira.status[f"{GATEWAY}/"] = 401

    facts = jira_connect.verify(SITE, EMAIL, JIRA_TOKEN, transport=transport(jira))

    assert facts.api_base == "site"
    assert jira.urls()[-1] == f"GET {SITE}/rest/api/3/myself"


def test_verify_raises_jira_errors(jira):
    jira.status[f"{GATEWAY}/"] = 401
    jira.status[f"{SITE}/rest/"] = 401
    with pytest.raises(JiraUnauthorized):
        jira_connect.verify(SITE, EMAIL, JIRA_TOKEN, transport=transport(jira))

    jira.status = {f"{GATEWAY}/": 403}
    with pytest.raises(JiraForbidden):
        jira_connect.verify(SITE, EMAIL, JIRA_TOKEN, transport=transport(jira))

    jira.status = {f"{SITE}/_edge": 503}
    with pytest.raises(JiraUnavailable):
        jira_connect.verify(SITE, EMAIL, JIRA_TOKEN, transport=transport(jira))


@pytest.mark.parametrize("site", [
    "http://acme.atlassian.net", "https://acme.atlassian.net:8443", "https://acme.atlassian.net/jira",
    "https://evil.example.com", "https://user@acme.atlassian.net", "https://acme.atlassian.net?x=1", "",
])
def test_verify_and_normalize_reject_other_addresses_before_any_request(jira, site):
    with pytest.raises(ValueError):
        jira_connect.normalize_site(site)
    with pytest.raises(ValueError):
        jira_connect.verify(site, EMAIL, JIRA_TOKEN, transport=transport(jira))
    assert jira.calls == []


def test_api_base_url_is_gateway_plus_cloud_or_the_site():
    row = {"site_url": SITE, "cloud_id": CLOUD, "api_base": "gateway"}
    assert jira_connect.api_base_url(row) == GATEWAY
    assert jira_connect.api_base_url({**row, "api_base": "site"}) == SITE
    contract = JiraConnection(session_id="s", site_url=SITE, cloud_id=CLOUD, api_base="site", email=EMAIL,
                              account_id=ACCOUNT, display_name="김개발", connected_at="2026-10-01T00:00:00Z",
                              disconnected_at=None, auth_failed_at=None)
    assert jira_connect.api_base_url(contract) == SITE


# --- 설정 이름 대조 -----------------------------------------------------------------------------------

CHOICES = JiraChoices(issue_types=[JiraIssueType(id="10001", name="버그"), JiraIssueType(id="10002", name="작업")],
                      statuses=["대기", "진행 중", "리뷰중", "종료"])


def _settings(**overrides):
    form = {"issue_types": [], "status_on_start": "", "status_on_review": "", "status_on_done": "",
            "followup_issue_type": ""} | overrides
    return jira_connect.project_settings(CHOICES, **form)


def test_project_settings_store_candidate_spelling_and_blank_as_none():
    assert _settings(issue_types=[" 버그 "], status_on_start="진행 중", status_on_review=" 리뷰중",
                     followup_issue_type="작업") == {
        "issue_types": ["버그"], "status_on_start": "진행 중", "status_on_review": "리뷰중", "status_on_done": None,
        "followup_issue_type": "작업",
    }
    assert _settings() == {"issue_types": [], "status_on_start": None, "status_on_review": None,
                           "status_on_done": None, "followup_issue_type": None}


def test_project_settings_match_names_ignoring_case():
    choices = JiraChoices(issue_types=[JiraIssueType(id="1", name="Bug")], statuses=["In Progress"])
    assert jira_connect.project_settings(
        choices, issue_types=["bug", "BUG"], status_on_start="in progress", status_on_review="", status_on_done="",
        followup_issue_type="bug",
    ) == {"issue_types": ["Bug"], "status_on_start": "In Progress", "status_on_review": None, "status_on_done": None,
          "followup_issue_type": "Bug"}


@pytest.mark.parametrize(("field", "value"), [
    ("issue_types", ["스토리"]), ("status_on_start", "완료"), ("status_on_review", "<script>"),
    ("status_on_done", "x"), ("followup_issue_type", "에픽"),
])
def test_project_settings_reject_unknown_names(field, value):
    with pytest.raises(InvalidSetting) as raised:
        _settings(**{field: value})
    assert raised.value.field == field


# --- 화면 컨텍스트 -------------------------------------------------------------------------------------


@pytest.fixture
def db(tmp_path):
    from workflow.adapters.db import connect, init_schema

    from ..adapters.test_repo import _source

    c = connect(tmp_path / "c.sqlite")
    init_schema(c)
    repo.create_session(c, "s1", "2026-10-01T00:00:00Z")
    repo.save_github_source(c, "s1", _source(), "2026-10-01T00:00:00Z")
    yield c
    c.close()


FACTS = SimpleNamespace(site_url=SITE, cloud_id=CLOUD, api_base="gateway", email=EMAIL, account_id=ACCOUNT,
                        display_name="김개발")


def test_context_states(db):
    assert jira_connect.connect_context(db, "s1", token_saved=False)["jira"]["state"] == "none"

    repo.save_jira_connection(db, FACTS, session_id="s1", now="2026-10-01T00:00:00Z")
    jira = jira_connect.connect_context(db, "s1", token_saved=True)["jira"]
    assert jira["state"] == "connected"
    assert jira["token_page"] == "https://id.atlassian.com/manage-profile/security/api-tokens"
    assert jira["connection"] == {"site_url": SITE, "email": EMAIL, "display_name": "김개발"}
    assert jira["github_sources"] == [{"source_id": "ghs-1a2b3c4d", "repository_full_name": "acme/billing"}]
    # 토큰 파일이 없으면 다시 붙여 넣어야 한다
    assert jira_connect.connect_context(db, "s1", token_saved=False)["jira"]["state"] == "none"

    repo.mark_jira_auth_failed(db, "s1", now="2026-10-01T01:00:00Z")
    assert jira_connect.connect_context(db, "s1", token_saved=True)["jira"]["state"] == "auth_failed"

    repo.disconnect_jira(db, "s1", now="2026-10-01T02:00:00Z")
    assert jira_connect.connect_context(db, "s1", token_saved=False)["jira"]["state"] == "none"


def test_context_lists_projects_with_choices(db):
    from workflow.contracts.jira import JiraProjectRef

    repo.save_jira_connection(db, FACTS, session_id="s1", now="2026-10-01T00:00:00Z")
    source_id = repo.add_jira_project(db, session_id="s1", ref=JiraProjectRef(project_id="10000", key="SHOP", name="쇼핑몰"),
                                      github_source_id="ghs-1a2b3c4d", start_mode="all_open", choices=CHOICES,
                                      now="2026-10-01T00:00:00Z")

    [project] = jira_connect.connect_context(db, "s1", token_saved=True)["jira"]["projects"]

    assert project["config"]["source_id"] == source_id
    assert project["choices"] == CHOICES.model_dump(mode="json")
    assert (project["repository_full_name"], project["cursor_updated_at"]) == ("acme/billing", None)
