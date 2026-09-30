"""mapping_api — 워크스페이스 매핑 표 조회·교체 운영자 API (phase 14 step 5, ADR-0020, ARCHITECTURE "매핑 표").

화면은 16-work-ui. 교체는 설정 번호를 올리고, 이미 만든 업무는 바꾸지 않는다.
"""

from fastapi.testclient import TestClient

from workflow.adapters import repo
from workflow.server.auth import SELFHOST_SESSION_ID

from .conftest import log_in_other_workspace
from .test_github_api import error

DEFAULT = [{"source_type": "github", "field": "kind", "source_value": "*", "runloom_value": "bug_fix"}]
NEW = [
    {"source_type": "github", "field": "kind", "source_value": "review", "runloom_value": "code_review"},
    {"source_type": "github", "field": "kind", "source_value": "*", "runloom_value": "bug_fix"},
    {"source_type": "github", "field": "priority", "source_value": "P1", "runloom_value": "high"},
]


def test_get_lists_default_mapping_with_config_revision(logged_in_client, conn):
    response = logged_in_client.get("/field-mappings")
    assert response.status_code == 200, response.text
    assert response.json() == {"config_revision": repo.get_config_revision(conn, SELFHOST_SESSION_ID),
                               "mappings": DEFAULT}


def test_put_replaces_rows_in_order_and_bumps_config_revision(logged_in_client, conn):
    before = repo.get_config_revision(conn, SELFHOST_SESSION_ID)
    response = logged_in_client.put("/field-mappings", json={"mappings": NEW})
    assert response.status_code == 200, response.text
    assert response.json() == {"config_revision": before + 1, "mappings": NEW}
    assert [row.position for row in repo.list_field_mappings(conn, SELFHOST_SESSION_ID)] == [1, 2, 3]
    assert logged_in_client.get("/field-mappings").json()["mappings"] == NEW


def test_put_rejects_unknown_kind_or_bad_body_without_change(logged_in_client, conn):
    before = repo.get_config_revision(conn, SELFHOST_SESSION_ID)
    bad_kind = [{**DEFAULT[0], "runloom_value": "no_such_kind"}]
    error(logged_in_client.put("/field-mappings", json={"mappings": bad_kind}), 422, "invalid_field", "mappings")
    for body in ({"mappings": [{**DEFAULT[0], "source_type": "jira"}]},  # 원본 종류 밖
                 {"mappings": [{**DEFAULT[0], "field": "assignee"}]},  # 필드 밖
                 {"mappings": [{**DEFAULT[0], "source_value": ""}]},
                 {"mappings": DEFAULT, "extra": 1}):
        assert logged_in_client.put("/field-mappings", json=body).status_code == 422
    assert repo.get_config_revision(conn, SELFHOST_SESSION_ID) == before
    assert logged_in_client.get("/field-mappings").json()["mappings"] == DEFAULT


def test_every_endpoint_requires_operator_session(client, conn):
    signed = log_in_other_workspace(TestClient(client.app))  # 다른 워크스페이스의 로그인 — 로그인 안 된 것으로 본다
    for anonymous in (TestClient(client.app), signed):
        error(anonymous.get("/field-mappings"), 401, "unauthenticated")
        error(anonymous.put("/field-mappings", json={"mappings": NEW}), 401, "unauthenticated")
    assert len(repo.list_field_mappings(conn, SELFHOST_SESSION_ID)) <= 1
