"""github_api.py — 운영자 GitHub 소스 설정·담당 연결 API (phase 8 step 6, ADR-0014 결정 1·5, CONTRACT 13.6·13.9).

운영자 세션(OPERATOR_TOKEN 로그인)만 쓰고, 소스는 만든 세션 소유다. 토큰은 받지도 돌려주지도 않는다 —
`token_configured` 만 응답한다. 저장소는 `WORKFLOW_GITHUB_REPOS`(Settings.github_repos) 안에서만 연결한다.
"""

import dataclasses

import pytest
from fastapi.testclient import TestClient

from workflow.adapters import repo
from workflow.contracts.github import GitHubSourceConfig
from workflow.contracts.v1 import ExecutionRequest
from workflow.server.auth import SELFHOST_SESSION_ID, SESSION_COOKIE, sign_session, verify_session

from .conftest import BASE_COMMIT, NOW, log_in

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
    }


def login(client: TestClient) -> str:
    """워크스페이스 로그인(`/login`, 로그인 = 운영자) → 세션 ID(고정 워크스페이스). 쿠키는 client 가 보관한다."""
    log_in(client)
    session_id = verify_session(client.cookies[SESSION_COOKIE], "test-session-secret")
    assert session_id == SELFHOST_SESSION_ID
    return session_id


@pytest.fixture
def operator(app, conn) -> tuple[TestClient, str]:
    """로그인한 워크스페이스 + 수정 Agent(code.fix billing, vp-pytest)·검토 Agent(code.review billing) 등록."""
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
    # 워크스페이스가 아닌 세션 행을 서명한 쿠키 — 셀프호스트에서는 로그인 안 된 것으로 본다
    repo.create_session(conn, "sess-other", NOW)
    client.cookies.set(SESSION_COOKIE, sign_session("sess-other", "test-session-secret"))
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
            error(response, 401, "unauthenticated")
    assert repo.github_source_sessions(conn) == []


def test_other_operator_session_cannot_see_or_change_a_source(op, conn):
    # 다른 워크스페이스(운영자 세션)가 가진 소스 — DB 에 직접 둔다
    repo.create_session(conn, "sess-other", NOW)
    repo.mark_operator(conn, "sess-other")
    sid = "ghs-0000beef"
    repo.save_github_source(conn, "sess-other", GitHubSourceConfig.model_validate({
        **body(), "source_id": sid, "start_at": "2026-10-06T00:00:00Z", "config_revision": 1,
    }), NOW)
    saved = repo.get_github_source(conn, "sess-other", sid)
    error(op.get(f"/github/sources/{sid}"), 404, "not_found")
    error(op.put(f"/github/sources/{sid}", json={**body(), "expected_revision": 1}), 404, "not_found")
    error(op.post(f"/github/sources/{sid}/stop"), 404, "not_found")
    error(op.put(f"/github/sources/{sid}/assignees/5812345", json={"github_login": "kim", "agent_id": FIX_AGENT}),
          404, "not_found")
    assert op.get("/github/sources").json()["sources"] == []
    # 셀프호스트 1개 워크스페이스 — 다른 세션이 연결을 가지면 전역 토큰으로 새 연결을 만들 수 없다
    error(op.post("/github/sources", json=body(repository_full_name="acme/lib")), 409, "github_workspace_taken")
    assert repo.get_github_source(conn, "sess-other", sid) == saved
    assert repo.github_source_sessions(conn) == ["sess-other"]


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
    repo.insert_work_item_task(conn, task, NOW)
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


# --- 새 칸: intake·trigger_label·기본 수정 Agent·자동 결정 칸 (phase 11 step 3) ---------------------


FIXED_NOW = "2026-10-07T01:02:03.000000Z"


@pytest.fixture
def fixed_now(monkeypatch):
    monkeypatch.setattr("workflow.server.github_api.utc_now", lambda: FIXED_NOW)
    return FIXED_NOW


def test_all_open_source_needs_only_repository_and_run_mode(op, operator, conn, fixed_now):
    response = op.post("/github/sources", json={
        "repository_full_name": "acme/billing", "intake": "all_open", "run_mode": "auto"})
    assert response.status_code == 201, response.text
    source = response.json()["source"]
    assert {k: source[k] for k in (
        "intake", "label_filter", "selected_issue_numbers", "start_at", "trigger_label", "workflow_repository_id",
        "fix_verification_profile_id", "review_agent_id", "default_fix_agent_id", "installation_id",
        "max_rework_rounds", "enabled", "config_revision")} == {
        "intake": "all_open", "label_filter": [], "selected_issue_numbers": [], "start_at": fixed_now,
        "trigger_label": "runloom", "workflow_repository_id": None, "fix_verification_profile_id": None,
        "review_agent_id": None, "default_fix_agent_id": None, "installation_id": None,
        "max_rework_rounds": 1, "enabled": True, "config_revision": 1}
    assert repo.get_github_source(conn, operator[1], source["source_id"]).model_dump() == source


def test_start_at_defaults_to_now_for_filtered_too(op, fixed_now):
    data = body()
    del data["start_at"]
    response = op.post("/github/sources", json=data)
    assert response.status_code == 201, response.text
    assert response.json()["source"]["start_at"] == fixed_now


def test_trigger_label_can_be_chosen_or_cleared(op):
    minimal = {"repository_full_name": "acme/billing", "intake": "all_open", "run_mode": "auto"}
    error(op.post("/github/sources", json={**minimal, "trigger_label": ""}), 422, "invalid_field", "trigger_label")
    source = op.post("/github/sources", json={**minimal, "trigger_label": None}).json()["source"]
    assert source["trigger_label"] is None
    url = f"/github/sources/{source['source_id']}"
    updated = op.put(url, json={**minimal, "trigger_label": "go-runloom", "expected_revision": 1})
    assert updated.status_code == 200, updated.text
    assert updated.json()["source"]["trigger_label"] == "go-runloom"
    # filtered 는 기본 None 그대로(phase 8 동작)
    assert create(op, repository_full_name="acme/lib", workflow_repository_id="billing")["trigger_label"] is None


def test_filtered_still_requires_the_three_ids(op):
    for field in ("workflow_repository_id", "fix_verification_profile_id", "review_agent_id"):
        data = body()
        del data[field]
        error(op.post("/github/sources", json=data), 422, "invalid_field", field)
        error(op.post("/github/sources", json=body(**{field: None})), 422, "invalid_field", field)
    error(op.post("/github/sources", json=body(intake="filtered", label_filter=[])), 422, "invalid_field")
    error(op.post("/github/sources", json=body(intake="everything")), 422, "invalid_field", "intake")


def test_installation_id_is_not_accepted_from_the_body(op):
    error(op.post("/github/sources", json=body(installation_id=1)), 422, "unknown_field", "installation_id")


def test_all_open_checks_only_the_ids_it_is_given(op, conn):
    minimal = {"repository_full_name": "acme/billing", "intake": "all_open", "run_mode": "auto"}
    error(op.post("/github/sources", json={**minimal, "default_fix_agent_id": "agent-nope"}),
          422, "agent_not_registered", "default_fix_agent_id")
    error(op.post("/github/sources", json={**minimal, "workflow_repository_id": "billing",
                                           "default_fix_agent_id": REVIEW_AGENT}),
          422, "agent_capability_mismatch", "default_fix_agent_id")
    error(op.post("/github/sources", json={**minimal, "review_agent_id": "agent-nope"}),
          422, "agent_not_registered", "review_agent_id")
    error(op.post("/github/sources", json={**minimal, "workflow_repository_id": "billing",
                                           "fix_verification_profile_id": "vp-x"}),
          422, "verification_profile_unknown", "fix_verification_profile_id")
    preview = op.post("/github/sources/preview", json=minimal)
    assert preview.json() == {"token_configured": True, "problems": []}
    source = op.post("/github/sources", json={**minimal, "workflow_repository_id": "billing",
                                              "default_fix_agent_id": FIX_AGENT}).json()["source"]
    assert (source["workflow_repository_id"], source["default_fix_agent_id"]) == ("billing", FIX_AGENT)


def test_update_keeps_installation_id_and_skips_allowlist_for_installed_source(op, operator, conn):
    """App 설치가 만든 소스(installation_id)는 WORKFLOW_GITHUB_REPOS 밖이어도 설정을 바꿀 수 있고, 설치 ID 는 유지된다."""
    from workflow.contracts.github import GitHubSourceConfig

    installed = GitHubSourceConfig(
        source_id="ghs-0000abcd", repository_full_name="kim/notes", intake="all_open", label_filter=[],
        selected_issue_numbers=[], start_at=NOW, run_mode="auto", trigger_label="runloom", installation_id=777,
        enabled=True, config_revision=1)
    repo.save_github_source(conn, operator[1], installed, NOW, expected_revision=None)
    url = "/github/sources/ghs-0000abcd"
    response = op.put(url, json={"repository_full_name": "kim/notes", "intake": "all_open", "run_mode": "manual",
                                 "expected_revision": 1})
    assert response.status_code == 200, response.text
    source = response.json()["source"]
    assert (source["installation_id"], source["run_mode"], source["config_revision"]) == (777, "manual", 2)
    # 설치 소스가 아니면 허용 목록 검사 그대로
    error(op.post("/github/sources", json={"repository_full_name": "kim/other", "intake": "all_open",
                                           "run_mode": "auto"}), 422, "repository_not_allowed")


def test_bind_assignee_on_undecided_all_open_source(op):
    source = op.post("/github/sources", json={
        "repository_full_name": "acme/billing", "intake": "all_open", "run_mode": "auto"}).json()["source"]
    url = f"/github/sources/{source['source_id']}/assignees/5812345"
    response = op.put(url, json={"github_login": "kim", "agent_id": FIX_AGENT})
    assert response.status_code == 200, response.text
    error(op.put(url, json={"github_login": "kim", "agent_id": "agent-nope"}), 422, "agent_not_registered", "agent_id")


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
