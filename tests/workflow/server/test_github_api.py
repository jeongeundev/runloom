"""github_api.py — 운영자 GitHub 소스 설정·담당 연결 API (phase 8 step 6, ADR-0014 결정 1·5, CONTRACT 13.6·13.9).

운영자 세션(OPERATOR_TOKEN 로그인)만 쓰고, 소스는 만든 세션 소유다. 토큰은 받지도 돌려주지도 않는다 —
`token_configured` 만 응답한다. 저장소는 `WORKFLOW_GITHUB_REPOS`(Settings.github_repos) 안에서만 연결한다.
"""

import dataclasses

import pytest
from fastapi.testclient import TestClient

from workflow.adapters import repo
from workflow.contracts.v1 import ExecutionRequest
from workflow.server.auth import SESSION_COOKIE, verify_session

from .conftest import BASE_COMMIT, NOW

TOKEN = "github_pat_" + "S3cr3t" * 10
FIX_AGENT = "agent-codex-mac"
REVIEW_AGENT = "agent-claude-mac"
OTHER_AGENT = "agent-other"


@pytest.fixture
def settings(settings):
    return dataclasses.replace(settings, github_token=TOKEN, github_repos=("acme/billing", "acme/lib"))


def _agent(agent_id: str, *capabilities: tuple[str, str], profiles=("vp-pytest",)) -> dict:
    return {
        "agent_id": agent_id,
        "name": agent_id,
        "owner_scope": "personal",
        "connection_type": "local",
        "local_registration_id": f"local-{agent_id}",
        "capabilities": [{"code": code, "scope": {"repository_id": repo_id}} for code, repo_id in capabilities],
        "verification_profile_ids": list(profiles),
        "shared_to_all_sessions": True,
    }


def login(client: TestClient) -> str:
    """운영자 로그인 → 세션 ID. 쿠키는 client 가 보관한다."""
    response = client.post("/operator/login", data={"token": "test-operator-token"}, follow_redirects=False)
    assert response.status_code == 303, response.text
    return verify_session(client.cookies[SESSION_COOKIE], "test-session-secret")


@pytest.fixture
def operator(app, conn) -> tuple[TestClient, str]:
    """운영자 세션 + 수정 Agent(code.fix billing, vp-pytest)·검토 Agent(code.review billing) 등록."""
    client = TestClient(app)
    session_id = login(client)
    repo.upsert_agent(conn, _agent(FIX_AGENT, ("code.fix", "billing")))
    repo.upsert_agent(conn, _agent(REVIEW_AGENT, ("code.review", "billing"), profiles=()))
    repo.upsert_agent(conn, _agent(OTHER_AGENT, ("code.modify", "billing")))
    for agent_id in (FIX_AGENT, REVIEW_AGENT, OTHER_AGENT):
        repo.register_session_agent(conn, session_id, agent_id, NOW)
    return client, session_id


@pytest.fixture
def op(operator) -> TestClient:
    return operator[0]


def body(**overrides) -> dict:
    data = {
        "repository_full_name": "acme/billing",
        "workflow_repository_id": "billing",
        "label_filter": ["bug"],
        "selected_issue_numbers": [],
        "start_at": "2026-10-06T09:00:00+09:00",
        "fix_verification_profile_id": "vp-pytest",
        "review_agent_id": REVIEW_AGENT,
        "run_mode": "auto",
        "max_rework_rounds": 1,
        "enabled": True,
    }
    return {**data, **overrides}


def create(client: TestClient, **overrides) -> dict:
    response = client.post("/github/sources", json=body(**overrides))
    assert response.status_code == 201, response.text
    return response.json()["source"]


def error(response, status: int, code: str, field: str | None = "__any__") -> dict:
    assert response.status_code == status, response.text
    data = response.json()
    assert data["code"] == code, data
    if field != "__any__":
        assert data["field"] == field, data
    return data


# --- 인증·소유 범위 -----------------------------------------------------------------------


def test_every_endpoint_requires_operator_session(client, conn):
    client.get("/")  # 공개 세션 쿠키만 받는다
    calls = [
        ("GET", "/github/sources", None),
        ("POST", "/github/sources/preview", body()),
        ("POST", "/github/sources", body()),
        ("GET", "/github/sources/ghs-1a2b3c4d", None),
        ("PUT", "/github/sources/ghs-1a2b3c4d", {**body(), "expected_revision": 1}),
        ("POST", "/github/sources/ghs-1a2b3c4d/stop", None),
        ("PUT", "/github/sources/ghs-1a2b3c4d/assignees/5812345", {"github_login": "kim", "agent_id": FIX_AGENT}),
    ]
    for anonymous in (TestClient(client.app), client):
        for method, url, payload in calls:
            response = anonymous.request(method, url, json=payload)
            error(response, 403, "forbidden")
    assert repo.github_source_sessions(conn) == []


def test_other_operator_session_cannot_see_or_change_a_source(app, op, conn):
    source = create(op)
    other = TestClient(app)
    login(other)
    sid = source["source_id"]
    error(other.get(f"/github/sources/{sid}"), 404, "not_found")
    error(other.put(f"/github/sources/{sid}", json={**body(), "expected_revision": 1}), 404, "not_found")
    error(other.post(f"/github/sources/{sid}/stop"), 404, "not_found")
    error(other.put(f"/github/sources/{sid}/assignees/5812345", json={"github_login": "kim", "agent_id": FIX_AGENT}),
          404, "not_found")
    assert other.get("/github/sources").json()["sources"] == []
    # 셀프호스트 1개 워크스페이스 — 다른 세션은 전역 토큰으로 새 연결을 만들 수 없다
    error(other.post("/github/sources", json=body(repository_full_name="acme/lib")), 409, "github_workspace_taken")
    assert op.get(f"/github/sources/{sid}").json()["source"] == source


# --- 저장·조회 ------------------------------------------------------------------------------


def test_create_assigns_source_id_and_first_revision(op, operator, conn):
    source = create(op)
    assert source["source_id"].startswith("ghs-") and len(source["source_id"]) == 12
    assert source["config_revision"] == 1
    assert {k: source[k] for k in body()} == body()
    assert repo.get_github_source(conn, operator[1], source["source_id"]).model_dump() == source

    detail = op.get(f"/github/sources/{source['source_id']}").json()
    assert detail == {"source": source, "assignees": [], "token_configured": True}
    listing = op.get("/github/sources").json()
    assert listing == {"token_configured": True, "allowed_repositories": ["acme/billing", "acme/lib"],
                       "sources": [source]}


def test_selected_issues_without_labels_is_a_valid_scope(op):
    source = create(op, label_filter=[], selected_issue_numbers=[41, 42])
    assert source["selected_issue_numbers"] == [41, 42]


def test_token_is_never_accepted_in_the_body(op):
    for field in ("token", "github_token", "WORKFLOW_GITHUB_TOKEN"):
        error(op.post("/github/sources", json={**body(), field: "ghp_x"}), 422, "unknown_field", field)
    error(op.post("/github/sources", json={**body(), "source_id": "ghs-00000001"}), 422, "unknown_field", "source_id")


def test_repository_outside_allowlist_is_rejected(op, conn):
    data = error(op.post("/github/sources", json=body(repository_full_name="acme/other")),
                 422, "repository_not_allowed", "repository_full_name")
    assert data == {"code": "repository_not_allowed", "message": "저장소 acme/other 는 WORKFLOW_GITHUB_REPOS 에 없습니다.",
                    "field": "repository_full_name", "details": None}
    error(op.post("/github/sources", json=body(repository_full_name="https://github.com/acme/billing")),
          422, "invalid_field", "repository_full_name")
    assert repo.github_source_sessions(conn) == []
    # 허용 목록 비교는 대소문자 무시, 저장은 허용 목록의 표기
    assert create(op, repository_full_name="ACME/Billing")["repository_full_name"] == "acme/billing"


def test_empty_allowlist_rejects_every_repository(app, conn, settings):
    client = TestClient(app)
    app.state.settings = dataclasses.replace(settings, github_repos=(), github_token="")
    login(client)
    error(client.post("/github/sources", json=body()), 422, "repository_not_allowed")
    listing = client.get("/github/sources").json()
    assert listing == {"token_configured": False, "allowed_repositories": [], "sources": []}


def test_whole_backlog_scope_is_rejected(op):
    error(op.post("/github/sources", json=body(label_filter=[], selected_issue_numbers=[])), 422, "invalid_field")
    error(op.post("/github/sources", json=body(max_rework_rounds=4)), 422, "invalid_field", "max_rework_rounds")


def test_review_agent_must_be_in_this_session_with_review_capability(op, conn):
    repo.upsert_agent(conn, _agent("agent-elsewhere", ("code.review", "billing")))  # 이 세션에 등록 안 함
    error(op.post("/github/sources", json=body(review_agent_id="agent-elsewhere")),
          422, "agent_not_registered", "review_agent_id")
    error(op.post("/github/sources", json=body(review_agent_id="agent-nope")),
          422, "agent_not_registered", "review_agent_id")
    error(op.post("/github/sources", json=body(review_agent_id=FIX_AGENT)),
          422, "agent_capability_mismatch", "review_agent_id")
    error(op.post("/github/sources", json=body(workflow_repository_id="lib")),  # 검토 능력은 billing 범위뿐
          422, "agent_capability_mismatch", "review_agent_id")


def test_unregistered_verification_profile_is_rejected(op):
    error(op.post("/github/sources", json=body(fix_verification_profile_id="vp-report")),
          422, "verification_profile_unknown", "fix_verification_profile_id")


def test_same_repository_twice_in_a_workspace_conflicts(op):
    create(op)
    error(op.post("/github/sources", json=body(label_filter=["p1"])), 409, "source_exists", "repository_full_name")


# --- 미리보기 ------------------------------------------------------------------------------


def test_preview_lists_every_problem_and_saves_nothing(op, conn):
    response = op.post("/github/sources/preview", json=body(
        repository_full_name="acme/other", review_agent_id=FIX_AGENT, fix_verification_profile_id="vp-x"))
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["token_configured"] is True
    assert [p["code"] for p in data["problems"]] == [
        "repository_not_allowed", "agent_capability_mismatch", "verification_profile_unknown"]
    assert repo.github_source_sessions(conn) == []

    ok = op.post("/github/sources/preview", json=body()).json()
    assert ok == {"token_configured": True, "problems": []}
    assert repo.github_source_sessions(conn) == []


# --- 변경·중지 ------------------------------------------------------------------------------


def test_update_needs_the_current_revision(op):
    source = create(op)
    url = f"/github/sources/{source['source_id']}"
    error(op.put(url, json=body(label_filter=["p1"])), 422, "invalid_field", "expected_revision")
    updated = op.put(url, json={**body(label_filter=["p1"]), "expected_revision": 1})
    assert updated.status_code == 200, updated.text
    assert updated.json()["source"]["config_revision"] == 2
    assert updated.json()["source"]["label_filter"] == ["p1"]
    stale = error(op.put(url, json={**body(), "expected_revision": 1}), 409, "stale_config", "expected_revision")
    assert stale["details"] == {"current_revision": 2}
    assert op.get(url).json()["source"]["label_filter"] == ["p1"]
    # 저장소는 바꿀 수 없다 — 커서·원본 매핑이 그 저장소 것이다
    error(op.put(url, json={**body(repository_full_name="acme/lib"), "expected_revision": 2}),
          422, "invalid_field", "repository_full_name")


def test_stop_disables_and_bumps_revision_once(op):
    source = create(op)
    url = f"/github/sources/{source['source_id']}/stop"
    first = op.post(url)
    assert first.status_code == 200, first.text
    assert (first.json()["source"]["enabled"], first.json()["source"]["config_revision"]) == (False, 2)
    again = op.post(url).json()["source"]
    assert (again["enabled"], again["config_revision"]) == (False, 2)
    error(op.post("/github/sources/ghs-00000000/stop"), 404, "not_found")


def test_config_change_does_not_touch_active_execution_input(op, operator, conn):
    session_id = operator[1]
    repo.upsert_agent(conn, _agent(FIX_AGENT, ("code.fix", "billing"), profiles=("vp-pytest", "vp-fast")))
    source = create(op)
    task = {
        "task_id": "task-gh-41", "session_id": session_id, "title": "할인 쿠폰이 두 번 적용됨",
        "request": "GitHub acme/billing#41", "kind": "bug_fix",
        "required_capability": {"code": "code.fix", "scope": {"repository_id": "billing"}},
        "selection_mode": "auto", "chosen_agent_id": None, "run_mode": "auto", "completion_mode": "review",
        "criteria": [], "predecessor_task_id": None, "revision": 1,
        "target": {"local_registration_id": f"local-{FIX_AGENT}", "base_commit": BASE_COMMIT,
                   "verification_profile_id": "vp-pytest"},
        "status": "실행 중", "status_reason": "",
    }
    repo.insert_task(conn, task, NOW)
    request = ExecutionRequest.model_validate({
        "contract_version": 1, "execution_id": "exec-gh-41", "task_id": "task-gh-41", "kind": "bug_fix",
        "agent_id": FIX_AGENT, "task_revision": 1, "request": "GitHub acme/billing#41",
        "input_artifact_ids": [], "target": task["target"], "kind_spec": None,
    })
    repo.create_execution(conn, execution_id="exec-gh-41", task_id="task-gh-41", attempt_no=1,
                          start_key="auto:task-gh-41:r1", agent_id=FIX_AGENT, kind="bug_fix", request=request,
                          assigned_connector_id=None, predecessor_execution_id=None, now=NOW)
    before_exec = dict(repo.get_execution(conn, "exec-gh-41"))
    before_task = dict(repo.get_task(conn, "task-gh-41"))

    url = f"/github/sources/{source['source_id']}"
    response = op.put(url, json={**body(fix_verification_profile_id="vp-fast", run_mode="manual",
                                        max_rework_rounds=0), "expected_revision": 1})
    assert response.status_code == 200, response.text
    op.post(f"{url}/stop")

    assert dict(repo.get_execution(conn, "exec-gh-41")) == before_exec
    assert dict(repo.get_task(conn, "task-gh-41")) == before_task


# --- 담당 연결 ------------------------------------------------------------------------------


def test_bind_assignee_to_session_fix_agent(op):
    source = create(op)
    url = f"/github/sources/{source['source_id']}/assignees/5812345"
    response = op.put(url, json={"github_login": "kim-dev", "agent_id": FIX_AGENT})
    assert response.status_code == 200, response.text
    binding = {"source_id": source["source_id"], "github_user_id": 5812345, "github_login": "kim-dev",
               "agent_id": FIX_AGENT}
    assert response.json() == {"assignee": binding}
    renamed = op.put(url, json={"github_login": "kim-renamed", "agent_id": FIX_AGENT}).json()["assignee"]
    assert op.get(f"/github/sources/{source['source_id']}").json()["assignees"] == [renamed]


def test_bind_assignee_checks_agent_ownership_capability_and_profile(op, conn):
    source = create(op)
    url = f"/github/sources/{source['source_id']}/assignees/5812345"
    repo.upsert_agent(conn, _agent("agent-elsewhere", ("code.fix", "billing")))  # 다른 세션 소유(미등록)
    error(op.put(url, json={"github_login": "kim", "agent_id": "agent-elsewhere"}), 422, "agent_not_registered", "agent_id")
    error(op.put(url, json={"github_login": "kim", "agent_id": "agent-nope"}), 422, "agent_not_registered", "agent_id")
    error(op.put(url, json={"github_login": "kim", "agent_id": OTHER_AGENT}), 422, "agent_capability_mismatch", "agent_id")
    repo.upsert_agent(conn, _agent(FIX_AGENT, ("code.fix", "billing"), profiles=("vp-other",)))
    error(op.put(url, json={"github_login": "kim", "agent_id": FIX_AGENT}),
          422, "verification_profile_unknown", "agent_id")
    error(op.put(url, json={"github_login": "kim", "agent_id": FIX_AGENT, "token": "x"}), 422, "unknown_field")
    error(op.put(f"/github/sources/{source['source_id']}/assignees/0", json={"github_login": "kim", "agent_id": FIX_AGENT}),
          422, "invalid_field")
    error(op.put("/github/sources/ghs-00000000/assignees/1", json={"github_login": "kim", "agent_id": FIX_AGENT}),
          404, "not_found")
    assert op.get(f"/github/sources/{source['source_id']}").json()["assignees"] == []


# --- 비밀값 --------------------------------------------------------------------------------


def test_token_value_never_leaves_the_server(op, settings):
    source = create(op)
    sid = source["source_id"]
    responses = [
        op.get("/github/sources"),
        op.post("/github/sources/preview", json=body()),
        op.post("/github/sources/preview", json=body(repository_full_name="acme/other")),
        op.get(f"/github/sources/{sid}"),
        op.put(f"/github/sources/{sid}", json={**body(), "expected_revision": 1}),
        op.put(f"/github/sources/{sid}", json={**body(), "expected_revision": 1}),
        op.put(f"/github/sources/{sid}/assignees/5812345", json={"github_login": "kim", "agent_id": FIX_AGENT}),
        op.post(f"/github/sources/{sid}/stop"),
        op.post("/github/sources", json=body(repository_full_name="acme/other")),
    ]
    for response in responses:
        assert TOKEN not in response.text
        assert "github_pat_" not in response.text
    db_bytes = b"".join(p.read_bytes() for p in settings.db_path.parent.glob("central.sqlite*"))
    assert TOKEN.encode() not in db_bytes
