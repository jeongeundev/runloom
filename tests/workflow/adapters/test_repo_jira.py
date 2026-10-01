"""repo 의 Jira 연결·프로젝트 설정 함수 (phase 18 step 4, ARCHITECTURE "Jira 소스 — phase 18" 이름·시그니처 고정 repo(연결·설정)).

토큰은 DB 에 없다 — 연결 행은 공개 정보만. 프로젝트 행은 워크스페이스의 GitHub 소스를 연결 저장소로 가리킨다.
"""

import json
import sqlite3
from types import SimpleNamespace

import pytest

from workflow.adapters import repo
from workflow.adapters.errors import NotFound
from workflow.contracts.jira import JiraChoices, JiraIssueType, JiraProjectRef

from .test_repo import NOW, OTHER_SESSION, SESSION, SOURCE, _source

LATER = "2026-10-01T06:00:00Z"
CLOUD = "11111111-2222-4333-8444-555555555555"
FACTS = SimpleNamespace(site_url="https://acme.atlassian.net", cloud_id=CLOUD, api_base="gateway",
                        email="dev@acme.com", account_id="5b10ac8d82e05b22cc7d4ef5", display_name="김개발")
REF = JiraProjectRef(project_id="10000", key="SHOP", name="쇼핑몰")
CHOICES = JiraChoices(issue_types=[JiraIssueType(id="10001", name="버그"), JiraIssueType(id="10002", name="작업")],
                      statuses=["대기", "진행 중", "리뷰중", "종료"])


@pytest.fixture
def sessions(conn):
    repo.create_session(conn, SESSION, NOW)
    repo.create_session(conn, OTHER_SESSION, NOW)
    repo.save_github_source(conn, SESSION, _source(), NOW)
    repo.save_github_source(conn, OTHER_SESSION, _source(source_id="ghs-99999999"), NOW)
    return conn


def _add(conn, **overrides) -> str:
    args = {"session_id": SESSION, "ref": REF, "github_source_id": SOURCE, "start_mode": "from_now",
            "choices": CHOICES, "now": NOW} | overrides
    return repo.add_jira_project(conn, **args)


# --- 연결 -----------------------------------------------------------------------------------------


def test_save_connection_keeps_only_public_facts(sessions):
    repo.save_jira_connection(sessions, FACTS, session_id=SESSION, now=NOW)

    row = repo.get_jira_connection(sessions, SESSION)
    assert dict(row) == {
        "session_id": SESSION, "site_url": FACTS.site_url, "cloud_id": CLOUD, "api_base": "gateway",
        "email": "dev@acme.com", "account_id": FACTS.account_id, "display_name": "김개발", "connected_at": NOW,
        "disconnected_at": None, "auth_failed_at": None, "updated_at": NOW,
    }
    assert repo.get_jira_connection(sessions, OTHER_SESSION) is None


def test_reconnect_replaces_the_row_and_clears_disconnect_and_auth_failure(sessions):
    repo.save_jira_connection(sessions, FACTS, session_id=SESSION, now=NOW)
    repo.mark_jira_auth_failed(sessions, SESSION, now=NOW)
    repo.disconnect_jira(sessions, SESSION, now=NOW)

    again = SimpleNamespace(**{**vars(FACTS), "api_base": "site", "display_name": "새 이름"})
    repo.save_jira_connection(sessions, again, session_id=SESSION, now=LATER)

    row = repo.get_jira_connection(sessions, SESSION)
    assert (row["api_base"], row["display_name"], row["connected_at"], row["updated_at"]) == ("site", "새 이름", LATER, LATER)
    assert row["disconnected_at"] is None and row["auth_failed_at"] is None
    assert sessions.execute("SELECT COUNT(*) FROM jira_connections").fetchone()[0] == 1


def test_disconnect_and_auth_failure_only_stamp_the_row(sessions):
    repo.disconnect_jira(sessions, SESSION, now=NOW)  # 연결 없음 — 아무 일도 없다
    assert repo.get_jira_connection(sessions, SESSION) is None

    repo.save_jira_connection(sessions, FACTS, session_id=SESSION, now=NOW)
    repo.mark_jira_auth_failed(sessions, SESSION, now=LATER)
    repo.mark_jira_auth_failed(sessions, SESSION, now="2026-10-02T00:00:00Z")  # 첫 시각을 지킨다
    repo.disconnect_jira(sessions, SESSION, now=LATER)

    row = repo.get_jira_connection(sessions, SESSION)
    assert (row["auth_failed_at"], row["disconnected_at"]) == (LATER, LATER)


def test_disconnect_keeps_projects(sessions):
    repo.save_jira_connection(sessions, FACTS, session_id=SESSION, now=NOW)
    source_id = _add(sessions)

    repo.disconnect_jira(sessions, SESSION, now=LATER)

    assert [p.source_id for p in repo.list_jira_projects(sessions, SESSION)] == [source_id]


# --- 프로젝트 -------------------------------------------------------------------------------------


def test_add_project_from_now_starts_the_cursor_at_that_time(sessions):
    source_id = _add(sessions, now=LATER)

    project = repo.get_jira_project(sessions, SESSION, source_id)
    assert source_id.startswith("jps-") and len(source_id) == 12
    assert project.model_dump() == {
        "source_id": source_id, "session_id": SESSION, "project_id": "10000", "project_key": "SHOP",
        "project_name": "쇼핑몰", "github_source_id": SOURCE, "issue_types": [], "start_mode": "from_now",
        "start_at": LATER, "status_on_start": None, "status_on_review": None, "status_on_done": None,
        "followup_issue_type": None, "enabled": True,
    }
    row = sessions.execute("SELECT cursor_ms, cursor_updated_at, choices_json FROM jira_projects").fetchone()
    assert row["cursor_ms"] == 1790834400000 and row["cursor_updated_at"] is None
    assert JiraChoices.model_validate_json(row["choices_json"]) == CHOICES
    assert repo.get_jira_choices(sessions, SESSION, source_id) == CHOICES


def test_add_project_all_open_has_no_cursor(sessions):
    _add(sessions, start_mode="all_open")

    assert sessions.execute("SELECT cursor_ms FROM jira_projects").fetchone()[0] is None


def test_add_project_rejects_other_workspace_source_and_duplicates(sessions):
    with pytest.raises(NotFound):
        _add(sessions, github_source_id="ghs-99999999")
    _add(sessions)
    with pytest.raises(sqlite3.IntegrityError):
        _add(sessions)
    assert len(repo.list_jira_projects(sessions, SESSION)) == 1
    assert repo.list_jira_projects(sessions, OTHER_SESSION) == []


def test_update_project_settings(sessions):
    source_id = _add(sessions)

    repo.update_jira_project(
        sessions, SESSION, source_id, now=LATER, github_source_id=SOURCE, issue_types=["버그"],
        status_on_start="진행 중", status_on_review="리뷰중", status_on_done=None, followup_issue_type="작업",
        enabled=False,
    )

    project = repo.get_jira_project(sessions, SESSION, source_id)
    assert (project.issue_types, project.status_on_start, project.status_on_review, project.status_on_done,
            project.followup_issue_type, project.enabled) == (["버그"], "진행 중", "리뷰중", None, "작업", False)
    row = sessions.execute("SELECT issue_types_json, updated_at, start_mode FROM jira_projects").fetchone()
    assert (json.loads(row["issue_types_json"]), row["updated_at"], row["start_mode"]) == (["버그"], LATER, "from_now")


def test_update_project_rejects_other_workspace(sessions):
    source_id = _add(sessions)
    settings = {"now": LATER, "github_source_id": SOURCE, "issue_types": [], "status_on_start": None,
                "status_on_review": None, "status_on_done": None, "followup_issue_type": None, "enabled": True}

    with pytest.raises(NotFound):
        repo.update_jira_project(sessions, OTHER_SESSION, source_id, **settings)
    with pytest.raises(NotFound):
        repo.update_jira_project(sessions, SESSION, source_id, **{**settings, "github_source_id": "ghs-99999999"})
    with pytest.raises(NotFound):
        repo.update_jira_project(sessions, SESSION, "jps-00000000", **settings)
    assert repo.get_jira_project(sessions, OTHER_SESSION, source_id) is None


def test_set_choices_replaces_candidates(sessions):
    source_id = _add(sessions)
    fresh = JiraChoices(issue_types=[JiraIssueType(id="10003", name="스토리")], statuses=["할 일", "완료"])

    repo.set_jira_choices(sessions, SESSION, source_id, fresh, now=LATER)

    assert repo.get_jira_choices(sessions, SESSION, source_id) == fresh
    with pytest.raises(NotFound):
        repo.set_jira_choices(sessions, OTHER_SESSION, source_id, fresh, now=LATER)
    assert repo.get_jira_choices(sessions, OTHER_SESSION, source_id) is None


def test_project_rows_carry_choices_and_last_import(sessions):
    source_id = _add(sessions)
    sessions.execute("UPDATE jira_projects SET cursor_updated_at = ? WHERE source_id = ?", (LATER, source_id))

    [row] = repo.list_jira_project_rows(sessions, SESSION)

    assert (row["source_id"], row["cursor_updated_at"]) == (source_id, LATER)
    assert repo.list_jira_project_rows(sessions, OTHER_SESSION) == []
