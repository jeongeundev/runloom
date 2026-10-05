"""Jira 연결 경로·화면 — 붙여 넣기 → 확인 → 프로젝트 찾기·추가 → 설정 → 끊기 (phase 18 step 4, ARCHITECTURE "경로 (step 4)"·"화면").

실제 Jira 는 부르지 않는다 — `app.state.jira_transport` 에 가짜 Jira(`FakeJira`, MockTransport)를 넣는다(GitHub 연결의
`github_transport` 와 같은 관례). 토큰은 비밀 저장소에만 있고 DB·응답·로그에는 없다.
"""

import logging
import re

import httpx
import pytest
from fastapi.testclient import TestClient

from workflow.adapters import repo, secret_store
from workflow.adapters.secret_store import SecretStore
from workflow.server.app import create_app

from .conftest import log_in, log_in_member, log_in_other_workspace, session_of, task_row
from .test_jira_connect import ACCOUNT, CLOUD, EMAIL, GATEWAY, JIRA_TOKEN, SITE, FakeJira
from .test_web_github_connect import error

BASE = "http://127.0.0.1:8000"
GH_SOURCE = "ghs-1a2b3c4d"
NOW = "2026-10-01T00:00:00Z"


@pytest.fixture
def jira() -> FakeJira:
    return FakeJira()


@pytest.fixture
def app(settings, jira):
    app = create_app(settings)
    app.state.jira_transport = httpx.MockTransport(jira)
    return app


@pytest.fixture
def secrets(settings) -> SecretStore:
    return SecretStore(settings.secret_dir)


@pytest.fixture
def op(app) -> TestClient:
    return log_in(TestClient(app, base_url=BASE))


@pytest.fixture
def session(op) -> str:
    return session_of(op)


@pytest.fixture
def github_source(conn, session) -> str:
    from ..adapters.test_repo import _source

    repo.save_github_source(conn, session, _source(source_id=GH_SOURCE), NOW)
    return GH_SOURCE


def connect(client: TestClient, **overrides):
    form = {"site_url": SITE, "email": EMAIL, "token": JIRA_TOKEN} | overrides
    return client.post("/operator/jira/connect", data=form, follow_redirects=False)


@pytest.fixture
def connected(op, jira) -> TestClient:
    response = connect(op)
    assert response.status_code == 303, response.text
    jira.calls.clear()
    return op


def add_project(client: TestClient, **overrides):
    form = {"project_id": "10000", "github_source_id": GH_SOURCE, "start_mode": "from_now"} | overrides
    return client.post("/operator/jira/projects", data=form, follow_redirects=False)


@pytest.fixture
def project(connected, conn, session, github_source, jira) -> str:
    assert add_project(connected).status_code == 303
    jira.calls.clear()
    [p] = repo.list_jira_projects(conn, session)
    return p.source_id


def save(client: TestClient, source_id: str, **overrides):
    form = {"github_source_id": GH_SOURCE, "issue_types": [], "status_on_start": "", "status_on_review": "",
            "status_on_done": "", "followup_issue_type": "", "enabled": "on"} | overrides
    return client.post(f"/operator/jira/projects/{source_id}", data=form, follow_redirects=False)


# --- 연결 -------------------------------------------------------------------------------------------


def test_connect_checks_then_saves_token_in_secret_store_and_public_facts_in_db(op, conn, session, jira, secrets,
                                                                              caplog):
    caplog.set_level(logging.DEBUG)
    response = connect(op, site_url=" https://ACME.atlassian.net/ ", token=f"  {JIRA_TOKEN} ")

    assert (response.status_code, response.headers["location"]) == (303, "/repos")
    assert jira.urls() == [f"GET {SITE}/_edge/tenant_info", f"GET {GATEWAY}/rest/api/3/myself"]
    assert secrets.read(secret_store.JIRA_API_TOKEN) == JIRA_TOKEN
    row = repo.get_jira_connection(conn, session)
    assert (row["site_url"], row["cloud_id"], row["api_base"], row["account_id"], row["display_name"]) == (
        SITE, CLOUD, "gateway", ACCOUNT, "김개발")
    page = op.get("/repos").text
    dump = "\n".join(conn.iterdump())
    for text in (response.text, str(response.headers), page, dump, caplog.text):
        assert JIRA_TOKEN not in text


def test_connect_falls_back_to_site_address(op, conn, session, jira):
    jira.status[f"{GATEWAY}/"] = 401

    assert connect(op).status_code == 303
    assert repo.get_jira_connection(conn, session)["api_base"] == "site"


def test_connect_401_is_auth_failed_and_saves_nothing(op, conn, session, jira, secrets, caplog):
    caplog.set_level(logging.DEBUG)
    jira.status[f"{GATEWAY}/"] = 401
    jira.status[f"{SITE}/rest/"] = 401

    response = connect(op)

    error(response, 400, "jira_auth_failed")
    assert "이메일·토큰이 맞지 않습니다." in response.text
    assert not secrets.exists(secret_store.JIRA_API_TOKEN) and repo.get_jira_connection(conn, session) is None
    assert JIRA_TOKEN not in response.text and JIRA_TOKEN not in caplog.text


def test_connect_403_is_forbidden_with_scope_hint(op, conn, session, jira, secrets):
    jira.status[f"{GATEWAY}/"] = 403

    response = connect(op)

    error(response, 400, "jira_forbidden")
    assert "read:jira-work" in response.text
    assert not secrets.exists(secret_store.JIRA_API_TOKEN) and repo.get_jira_connection(conn, session) is None


def test_connect_unreachable_site_is_502(op, jira, secrets):
    jira.status[f"{SITE}/_edge"] = 500

    error(connect(op), 502, "jira_unavailable")
    assert not secrets.exists(secret_store.JIRA_API_TOKEN)


@pytest.mark.parametrize("site", [
    "http://acme.atlassian.net", "https://acme.atlassian.net:8443", "https://acme.atlassian.net/wiki",
    "https://evil.example.com", "https://user@acme.atlassian.net", "https://127.0.0.1",
])
def test_connect_rejects_addresses_outside_atlassian_net_without_calling(op, jira, secrets, site):
    response = connect(op, site_url=site)

    error(response, 422, "invalid_field")
    assert "https://&lt;이름&gt;.atlassian.net 형식만 받습니다." in response.text
    assert jira.calls == [] and not secrets.exists(secret_store.JIRA_API_TOKEN)


def test_connect_rejects_bad_email_or_token_without_calling(op, jira):
    error(connect(op, email="not-an-email"), 422, "invalid_field")
    error(connect(op, token="  "), 422, "invalid_field")
    error(connect(op, token="has space"), 422, "invalid_field")
    assert jira.calls == []


def test_reconnect_to_another_site_with_projects_is_409(project, connected, conn, session, jira, secrets):
    jira.cloud = "99999999-2222-4333-8444-555555555555"

    error(connect(connected, token="ATATT3xOTHERtoken"), 409, "jira_site_mismatch")

    assert secrets.read(secret_store.JIRA_API_TOKEN) == JIRA_TOKEN
    assert repo.get_jira_connection(conn, session)["cloud_id"] == CLOUD


def test_reconnect_same_site_clears_auth_failure(connected, conn, session):
    repo.mark_jira_auth_failed(conn, session, now=NOW)
    assert "Jira 토큰 확인 필요 — 다시 연결하세요" in connected.get("/repos").text

    assert connect(connected).status_code == 303
    assert repo.get_jira_connection(conn, session)["auth_failed_at"] is None


# --- 프로젝트 찾기·추가 ------------------------------------------------------------------------------------


def test_project_search_lists_results_with_escaping(connected, jira, github_source):
    jira.projects = [{"id": "10000", "key": "SHOP", "name": "<script>alert(1)</script>"}]

    response = connected.get("/operator/jira/projects", params={"q": "sh"})

    assert response.status_code == 200
    assert jira.calls[0].url.params["query"] == "sh"
    assert "<script>alert(1)</script>" not in response.text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in response.text
    assert 'name="project_id" value="10000"' in response.text
    assert f'<option value="{GH_SOURCE}">acme/billing</option>' in response.text


def test_project_search_needs_connection_and_short_query(op, connected, jira):
    error(connected.get("/operator/jira/projects", params={"q": "x" * 101}), 422, "invalid_field")
    jira.status[f"{GATEWAY}/rest/api/3/project"] = 503
    error(connected.get("/operator/jira/projects"), 502, "jira_unavailable")
    connected.post("/operator/jira/disconnect")
    error(connected.get("/operator/jira/projects"), 409, "jira_not_connected")


def test_project_search_401_marks_auth_failed(connected, conn, session, jira):
    jira.status[f"{GATEWAY}/"] = 401

    error(connected.get("/operator/jira/projects"), 400, "jira_auth_failed")

    assert repo.get_jira_connection(conn, session)["auth_failed_at"] is not None


def test_add_project_reads_project_and_candidates(connected, conn, session, github_source, jira):
    response = add_project(connected, start_mode="all_open")

    assert (response.status_code, response.headers["location"]) == (303, "/repos")
    assert jira.urls() == [f"GET {GATEWAY}/rest/api/3/project/10000", f"GET {GATEWAY}/rest/api/3/project/SHOP/statuses"]
    [p] = repo.list_jira_projects(conn, session)
    assert (p.project_key, p.project_name, p.github_source_id, p.start_mode) == ("SHOP", "쇼핑몰", GH_SOURCE, "all_open")
    choices = repo.get_jira_choices(conn, session, p.source_id)
    assert [t.name for t in choices.issue_types] == ["버그", "작업"]
    assert choices.statuses == ["대기", "진행 중", "리뷰중", "종료"]


def test_add_project_requires_a_github_source_first(connected, jira):
    response = add_project(connected)

    error(response, 409, "github_source_required")
    assert "먼저 GitHub 저장소를 연결하세요." in response.text
    assert jira.calls == []


def test_add_project_rejects_bad_fields_duplicates_and_missing(connected, github_source, jira):
    error(add_project(connected, github_source_id="ghs-00000000"), 422, "invalid_field")
    error(add_project(connected, start_mode="later"), 422, "invalid_field")
    error(add_project(connected, project_id="SHOP"), 422, "invalid_field")
    assert jira.calls == []
    error(add_project(connected, project_id="10099"), 404, "not_found")
    assert add_project(connected).status_code == 303
    error(add_project(connected), 409, "jira_project_exists")


def test_add_project_needs_connection(op, github_source, jira):
    error(add_project(op), 409, "jira_not_connected")
    assert jira.calls == []


# --- 프로젝트 설정 ---------------------------------------------------------------------------------------


def test_save_settings_keeps_candidate_spelling(connected, conn, session, project, jira):
    response = save(connected, project, issue_types=["버그", "작업"], status_on_start="진행 중",
                    status_on_review="리뷰중", status_on_done="종료", followup_issue_type="작업")

    assert (response.status_code, response.headers["location"]) == (303, "/repos")
    p = repo.get_jira_project(conn, session, project)
    assert (p.issue_types, p.status_on_start, p.status_on_review, p.status_on_done, p.followup_issue_type,
            p.enabled) == (["버그", "작업"], "진행 중", "리뷰중", "종료", "작업", True)
    assert jira.calls == []  # 대조는 저장한 후보로 — Jira 를 부르지 않는다

    assert save(connected, project, enabled="").status_code == 303  # 체크 해제 = 꺼짐, 빈 값 = 옮기지 않음
    p = repo.get_jira_project(conn, session, project)
    assert (p.issue_types, p.status_on_start, p.followup_issue_type, p.enabled) == ([], None, None, False)


@pytest.mark.parametrize(("field", "value"), [
    ("status_on_start", "없는 상태"), ("status_on_done", "<script>"), ("issue_types", ["에픽"]),
    ("followup_issue_type", "하위 작업"), ("github_source_id", "ghs-00000000"),
])
def test_save_settings_rejects_unknown_values(connected, conn, session, project, field, value):
    response = save(connected, project, **{field: value})

    error(response, 422, "invalid_field")
    assert f"필드 <code>{field}</code>" in response.text
    assert repo.get_jira_project(conn, session, project).status_on_start is None


def test_save_settings_of_unknown_project_is_404(connected, github_source):
    error(save(connected, "jps-00000000"), 404, "not_found")


def test_refresh_replaces_candidates(connected, conn, session, project, jira):
    jira.statuses = [{"id": "10005", "name": "스토리", "subtask": False, "statuses": [{"name": "할 일"}]}]

    response = connected.post(f"/operator/jira/projects/{project}/refresh", follow_redirects=False)

    assert response.status_code == 303
    choices = repo.get_jira_choices(conn, session, project)
    assert ([t.name for t in choices.issue_types], choices.statuses) == (["스토리"], ["할 일"])
    error(connected.post("/operator/jira/projects/jps-00000000/refresh"), 404, "not_found")


# --- 끊기 -----------------------------------------------------------------------------------------------


def test_disconnect_deletes_token_keeps_projects_and_work(connected, conn, session, project, secrets):
    repo.insert_work_item_task(conn, task_row("task-keep-0001"), NOW)
    works = conn.execute("SELECT COUNT(*) FROM work_items").fetchone()[0]

    response = connected.post("/operator/jira/disconnect", follow_redirects=False)

    assert (response.status_code, response.headers["location"]) == (303, "/repos")
    assert not secrets.exists(secret_store.JIRA_API_TOKEN)
    assert repo.get_jira_connection(conn, session)["disconnected_at"] is not None
    assert [p.source_id for p in repo.list_jira_projects(conn, session)] == [project]
    assert conn.execute("SELECT COUNT(*) FROM work_items").fetchone()[0] == works
    page = connected.get("/repos").text
    assert 'action="/operator/jira/connect"' in page and "연결됨 · 김개발" not in page
    error(connected.post(f"/operator/jira/projects/{project}/refresh"), 409, "jira_not_connected")


# --- 화면 -----------------------------------------------------------------------------------------------


def _jira_section(text: str) -> str:
    match = re.search(r"<section[^>]*data-jira>.*?</section>\s*<!-- /jira -->", text, re.S)
    assert match, text
    return match.group(0)


def test_page_before_connecting_shows_inputs_and_token_link(op):
    section = _jira_section(op.get("/repos").text)

    assert 'action="/operator/jira/connect"' in section
    for name in ("site_url", "email", "token"):
        assert f'name="{name}"' in section
    assert 'type="password"' in section
    assert ('href="https://id.atlassian.com/manage-profile/security/api-tokens" rel="noopener noreferrer" '
            'target="_blank"') in section
    assert "read:jira-work·write:jira-work·read:jira-user" in section


def test_page_after_connecting_shows_account_and_project_form(connected, conn, session, project, jira):
    jira.calls.clear()
    repo.update_jira_project(conn, session, project, now=NOW, github_source_id=GH_SOURCE, issue_types=["버그"],
                             status_on_start="진행 중", status_on_review=None, status_on_done=None,
                             followup_issue_type=None, enabled=True)

    section = _jira_section(connected.get("/repos").text)

    assert jira.calls == []  # 화면은 Jira 를 부르지 않는다
    assert f"연결됨 · 김개발 · {SITE}" in section and 'action="/operator/jira/disconnect"' in section
    assert 'name="token"' not in section
    assert 'method="get" action="/operator/jira/projects"' in section
    assert f'action="/operator/jira/projects/{project}"' in section
    assert f'action="/operator/jira/projects/{project}/refresh"' in section
    assert "SHOP · 쇼핑몰" in section
    assert 'name="issue_types" value="버그" checked' in section and 'name="issue_types" value="작업">' in section
    assert '<option value="진행 중" selected>진행 중</option>' in section
    assert '<option value="">옮기지 않음</option>' in section and '<option value="">만들지 않음</option>' in section
    assert f'<option value="{GH_SOURCE}" selected>acme/billing</option>' in section


def test_page_escapes_jira_strings(connected, conn, session, github_source, jira):
    jira.projects = [{"id": "10000", "key": "SHOP", "name": "<script>x</script>"}]
    jira.statuses = [{"id": "10001", "name": "<b>버그</b>", "subtask": False, "statuses": [{"name": "<i>대기</i>"}]}]
    assert add_project(connected).status_code == 303

    text = connected.get("/repos").text

    for raw in ("<script>x</script>", "<b>버그</b>", "<i>대기</i>"):
        assert raw not in text
    assert "&lt;script&gt;x&lt;/script&gt;" in text and "&lt;i&gt;대기&lt;/i&gt;" in text


def test_page_without_github_source_says_connect_github_first(connected):
    section = _jira_section(connected.get("/operator/jira/projects").text)

    assert "GitHub 저장소 먼저 연결" in section


# --- 권한·출처 ---------------------------------------------------------------------------------------------

ROUTES = [
    ("post", "/operator/jira/connect"), ("get", "/operator/jira/projects"), ("post", "/operator/jira/projects"),
    ("post", "/operator/jira/projects/jps-00000000"), ("post", "/operator/jira/projects/jps-00000000/refresh"),
    ("post", "/operator/jira/disconnect"),
]


@pytest.mark.parametrize(("method", "url"), ROUTES)
def test_member_gets_403(app, jira, method, url):
    member = log_in_member(TestClient(app, base_url=BASE))

    error(getattr(member, method)(url), 403, "forbidden")
    assert jira.calls == []


def test_member_page_has_no_jira_section(app):
    member = log_in_member(TestClient(app, base_url=BASE))

    assert "data-jira" not in member.get("/repos").text


@pytest.mark.parametrize(("method", "url"), ROUTES)
def test_not_logged_in_redirects_to_login(app, jira, method, url):
    client = log_in_other_workspace(TestClient(app, base_url=BASE))

    response = getattr(client, method)(url, follow_redirects=False)

    assert (response.status_code, response.headers["location"]) == (303, "/login")
    assert jira.calls == []


def test_foreign_origin_is_rejected(op, jira, secrets):
    response = op.post("/operator/jira/connect", data={"site_url": SITE, "email": EMAIL, "token": JIRA_TOKEN},
                       headers={"origin": "https://evil.example.com"})

    assert response.status_code == 403 and "forbidden_origin" in response.text
    assert jira.calls == [] and not secrets.exists(secret_store.JIRA_API_TOKEN)
    response = op.post("/operator/jira/disconnect", headers={"origin": "https://evil.example.com"})
    assert response.status_code == 403
