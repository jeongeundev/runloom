"""web.py — 심사자 세션·운영자 웹 라우트. 마크업이 아니라 렌더된 텍스트·리다이렉트·상태 코드를 본다 (Step 7 이 화면을 꾸민다)."""

import re

import pytest
from fastapi.testclient import TestClient

from workflow.adapters import repo
from workflow.contracts.v1 import ExecutionRequest
from workflow.server.auth import SESSION_COOKIE, verify_session
from workflow.server.web import EXAMPLES

from .conftest import (
    BASE_COMMIT,
    NOW,
    RESULT_COMMIT,
    code_change_result,
    seed_agents,
    seed_result_ready,
)

DIAGNOSE_REQUEST = (
    "일일 보고서 생성 실패를 조사하고, 로컬 개발 에이전트가 재현·수정할 수 있도록 근거와 기대 동작을 정리해 주세요."
)
FIX_REQUEST = (
    "인계된 진단 근거로 보고서 변환 실패를 재현하는 테스트를 먼저 작성하고, "
    "실패를 확인한 뒤 두 응답 형식을 모두 처리하도록 최소 수정하세요."
)


@pytest.fixture
def agents(conn):
    seed_agents(conn)
    return conn


@pytest.fixture
def web(client, agents):
    """홈을 한 번 열어 세션 쿠키를 받은 클라이언트."""
    assert client.get("/").status_code == 200
    return client


def session_id_of(client: TestClient, settings) -> str:
    return verify_session(client.cookies[SESSION_COOKIE], settings.session_secret)


def diagnose_form(**overrides) -> dict:
    form = {
        "title": EXAMPLES["diagnose"]["title"],
        "request": EXAMPLES["diagnose"]["request"],
        "capability_code": "operations.diagnose",
        "scope_value": "daily-report",
        "selection_mode": "auto",
        "chosen_agent_id": "",
        "run_mode": "manual",
        "completion_mode": "auto",
        "criteria_extra": "",
        "predecessor_task_id": "",
        "run_id": "daily-0920-0900",
    }
    form.update(overrides)
    return form


def fix_form(predecessor: str, **overrides) -> dict:
    form = {
        "title": EXAMPLES["fix"]["title"],
        "request": EXAMPLES["fix"]["request"],
        "capability_code": "code.modify",
        "scope_value": "demo-report-repo",
        "selection_mode": "auto",
        "chosen_agent_id": "",
        "run_mode": "auto",
        "completion_mode": "review",
        "criteria_extra": "",
        "predecessor_task_id": predecessor,
        "run_id": "",
    }
    form.update(overrides)
    return form


def create_task(client, form: dict) -> str:
    response = client.post("/tasks", data=form, follow_redirects=False)
    assert response.status_code == 303, response.text
    location = response.headers["location"]
    assert location.startswith("/tasks/")
    return location.removeprefix("/tasks/")


def detail(client, task_id: str) -> str:
    response = client.get(f"/tasks/{task_id}")
    assert response.status_code == 200, response.text
    return response.text


def seed_reviewable_fix(client, conn, store, settings) -> tuple[str, str]:
    """A → B 등록 뒤 B 에 result_ready 실행과 CONTRACT 7절 결과를 넣는다. (task_id, execution_id)."""
    task_a = create_task(client, diagnose_form())
    task_b = create_task(client, fix_form(task_a))
    repo.create_execution(
        conn,
        execution_id="exec-fix-001",
        task_id=task_b,
        attempt_no=1,
        start_key=f"auto:{task_b}:r1",
        agent_id="agent-codex-mac",
        kind="code_change",
        request=ExecutionRequest.model_validate({
            "contract_version": 1,
            "execution_id": "exec-fix-001",
            "task_id": task_b,
            "kind": "code_change",
            "agent_id": "agent-codex-mac",
            "task_revision": 1,
            "request": FIX_REQUEST,
            "input_artifact_ids": ["art-handoff-001"],
            "target": {
                "local_registration_id": "local-demo-report",
                "base_commit": BASE_COMMIT,
                "verification_profile_id": "vp-pytest",
            },
        }),
        assigned_connector_id=None,
        predecessor_execution_id=None,
        now=NOW,
    )
    seed_result_ready(
        conn, store, "exec-fix-001", kind="code_change_result",
        body=code_change_result("exec-fix-001", task_b), session_id=session_id_of(client, settings),
    )
    return task_b, "exec-fix-001"


# --- 세션·홈 ---------------------------------------------------------------------


def test_home_issues_session_cookie_once_and_shows_empty_state(client, agents):
    first = client.get("/")
    assert first.status_code == 200
    assert SESSION_COOKIE in first.cookies
    assert "아직 업무가 없습니다." in first.text
    assert "시연 업무 만들기" in first.text
    assert "/tasks/new?example=diagnose" in first.text
    assert "운영 진단 데모" in first.text and "개인 Codex" in first.text

    second = client.get("/")
    assert "set-cookie" not in second.headers
    assert second.status_code == 200


def test_home_lists_my_tasks_with_status(web):
    task_id = create_task(web, diagnose_form())
    text = web.get("/").text
    assert "아직 업무가 없습니다." not in text
    assert f"/tasks/{task_id}" in text
    assert "일일 보고서 실패 진단" in text
    assert "실행 가능" in text


# --- 등록 폼 ---------------------------------------------------------------------


def test_new_task_form_prefills_diagnose_example(web):
    text = web.get("/tasks/new?example=diagnose").text
    assert DIAGNOSE_REQUEST in text
    assert "일일 보고서 실패 진단" in text
    assert "결과가 ready_for_handoff임" in text
    assert "근거 검증을 통과함" in text
    assert "자동 판정기가 있는 진단 업무라 자동 완료로 미리 채움" in text
    assert "daily-0920-0900" in text


def test_new_task_form_prefills_fix_example_with_predecessor(web):
    task_a = create_task(web, diagnose_form())
    text = web.get(f"/tasks/new?example=fix&predecessor={task_a}").text
    assert FIX_REQUEST in text
    assert "보고서 변환기 수정" in text
    assert "등록된 검증 프로필이 결과 커밋에서 통과함" in text
    assert f'value="{task_a}" selected' in text


def test_new_task_form_rejects_predecessor_of_other_session(app, web, agents):
    task_a = create_task(web, diagnose_form())
    other = TestClient(app)
    assert other.get(f"/tasks/new?example=fix&predecessor={task_a}").status_code == 404


def test_new_task_form_without_example_is_blank(web):
    text = web.get("/tasks/new").text
    assert DIAGNOSE_REQUEST not in text
    assert "자동 판정기가 있는 진단 업무라" not in text


# --- 등록 ------------------------------------------------------------------------


def test_create_diagnose_task_selects_agent_and_is_runnable(web, conn):
    task_id = create_task(web, diagnose_form())
    text = detail(web, task_id)
    assert "실행 가능" in text
    assert "agent-ops-demo 선택됨" in text
    assert "operations.diagnose · workflow_id=daily-report 일치 후보 1개" in text
    assert f'action="/tasks/{task_id}/run"' in text
    assert "자동 완료" in text and "결과가 ready_for_handoff임" in text

    row = repo.get_task(conn, task_id)
    assert row["kind"] == "diagnosis"
    assert row["status"] == "실행 가능"
    assert row["completion_mode"] == "auto"
    selection = repo.get_selection(conn, task_id)
    assert selection.selected_agent_id == "agent-ops-demo" and selection.mode == "auto"


def test_create_fix_task_waits_for_predecessor(web, conn):
    task_a = create_task(web, diagnose_form())
    task_b = create_task(web, fix_form(task_a, criteria_extra="두 경로 동시 존재 테스트 포함\n\n"))
    text = detail(web, task_b)
    assert "대기" in text and "선행 대기" in text
    assert "일일 보고서 실패 진단" in text  # 선행 링크
    assert "두 경로 동시 존재 테스트 포함" in text
    assert f'action="/tasks/{task_b}/run"' not in text

    row = repo.get_task(conn, task_b)
    assert row["predecessor_task_id"] == task_a
    assert row["run_mode"] == "auto" and row["completion_mode"] == "review"
    criteria = repo.get_task(conn, task_b)["criteria_json"]
    assert '"user.1"' in criteria and '"structured": false' in criteria
    assert "보고서 변환기 수정" in detail(web, task_a)  # 후속 링크


def test_create_task_needs_selection_when_two_candidates_then_manual_select(web, conn):
    repo.upsert_agent(conn, {
        "agent_id": "agent-ops-second",
        "name": "두 번째 진단",
        "owner_scope": "company",
        "connection_type": "api",
        "api_url": "http://127.0.0.1:8101",
        "credential_ref": "env:DIAG_API_TOKEN",
        "capabilities": [{"code": "operations.diagnose", "scope": {"workflow_id": "daily-report"}}],
        "shared_to_all_sessions": True,
    })
    task_id = create_task(web, diagnose_form())
    text = detail(web, task_id)
    assert "확인 필요" in text and "후보 2개 — 선택 필요" in text
    assert f'action="/tasks/{task_id}/select"' in text
    assert "agent-ops-second" in text

    response = web.post(f"/tasks/{task_id}/select", data={"agent_id": "agent-ops-demo"}, follow_redirects=False)
    assert response.status_code == 303
    text = detail(web, task_id)
    assert "실행 가능" in text and "직접 선택" in text
    row = repo.get_task(conn, task_id)
    assert row["selection_mode"] == "manual" and row["chosen_agent_id"] == "agent-ops-demo"

    again = web.post(f"/tasks/{task_id}/select", data={"agent_id": "agent-ops-second"}, follow_redirects=False)
    assert again.status_code == 409


def test_create_task_manual_selection_records_reason(web, conn):
    task_id = create_task(web, diagnose_form(selection_mode="manual", chosen_agent_id="agent-codex-mac"))
    text = detail(web, task_id)
    assert "확인 필요" in text
    assert "선택한 에이전트에 operations.diagnose 능력 없음" in text


@pytest.mark.parametrize(
    "overrides",
    [
        {"title": " "},
        {"request": ""},
        {"capability_code": "ops.unknown"},
        {"scope_value": ""},
        {"run_id": ""},
        {"selection_mode": "manual", "chosen_agent_id": ""},
        {"completion_mode": "sometimes"},
    ],
)
def test_create_task_rejects_bad_form_with_422(web, overrides):
    response = web.post("/tasks", data=diagnose_form(**overrides), follow_redirects=False)
    assert response.status_code == 422, overrides


def test_create_code_change_task_cannot_be_auto_completed(web):
    task_a = create_task(web, diagnose_form())
    response = web.post("/tasks", data=fix_form(task_a, completion_mode="auto"), follow_redirects=False)
    assert response.status_code == 422
    assert "자동 완료" in response.text


def test_create_task_active_limit_429(web):
    for _ in range(5):
        create_task(web, diagnose_form())
    response = web.post("/tasks", data=diagnose_form(), follow_redirects=False)
    assert response.status_code == 429
    assert "세션당 활성 업무 한도(5개)에 도달했습니다." in response.text


def test_other_session_cannot_see_task(app, web, agents):
    task_id = create_task(web, diagnose_form())
    other = TestClient(app)
    assert other.get(f"/tasks/{task_id}").status_code == 404
    assert other.post(f"/tasks/{task_id}/run", follow_redirects=False).status_code == 404
    assert other.post(f"/tasks/{task_id}/review", data={"decision": "approve"}, follow_redirects=False).status_code == 404
    assert web.get("/tasks/nope").status_code == 404


# --- 직접 실행 -------------------------------------------------------------------


def test_run_creates_queued_execution_with_frozen_request(web, conn, settings):
    task_id = create_task(web, diagnose_form())
    response = web.post(f"/tasks/{task_id}/run", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == f"/tasks/{task_id}"

    text = detail(web, task_id)
    assert "실행 요청됨" in text and "접수 대기" in text
    assert f'action="/tasks/{task_id}/run"' not in text

    execution = repo.active_execution(conn, task_id)
    assert execution["status"] == "queued"
    assert execution["attempt_no"] == 1
    assert execution["start_key"].startswith("req:")
    assert execution["assigned_connector_id"] is None  # API 에이전트는 워커가 전달한다
    request = ExecutionRequest.model_validate_json(execution["request_json"])
    assert request.model_dump() == {
        "contract_version": 1,
        "execution_id": execution["execution_id"],
        "task_id": task_id,
        "kind": "diagnosis",
        "agent_id": "agent-ops-demo",
        "task_revision": 1,
        "request": DIAGNOSE_REQUEST,
        "input_artifact_ids": [],
        "target": {"run_id": "daily-0920-0900"},
    }
    since = "2026-01-01T00:00:00Z"
    assert repo.count_diagnosis_started(conn, session_id=session_id_of(web, settings), since=since) == 1
    assert repo.get_task(conn, task_id)["status"] == "실행 요청됨"

    again = web.post(f"/tasks/{task_id}/run", follow_redirects=False)
    assert again.status_code == 409
    assert len(repo.list_executions(conn, task_id)) == 1


def test_run_requires_selection_and_finished_predecessor(web, conn):
    task_a = create_task(web, diagnose_form())
    task_b = create_task(web, fix_form(task_a))
    blocked = web.post(f"/tasks/{task_b}/run", follow_redirects=False)
    assert blocked.status_code == 409
    assert "선행" in blocked.text

    repo.upsert_agent(conn, {
        "agent_id": "agent-ops-second", "name": "둘", "owner_scope": "company", "connection_type": "api",
        "api_url": "http://127.0.0.1:8101", "credential_ref": "env:DIAG_API_TOKEN",
        "capabilities": [{"code": "operations.diagnose", "scope": {"workflow_id": "daily-report"}}],
        "shared_to_all_sessions": True,
    })
    unselected = create_task(web, diagnose_form())
    assert web.post(f"/tasks/{unselected}/run", follow_redirects=False).status_code == 409


def test_run_diagnosis_session_daily_limit_429(web, conn, settings):
    task_id = create_task(web, diagnose_form())
    session_id = session_id_of(web, settings)
    from workflow.server.auth import utc_now
    for n in range(10):
        repo.record_diagnosis_start(conn, session_id, f"exec-seed-{n}", utc_now())
    response = web.post(f"/tasks/{task_id}/run", follow_redirects=False)
    assert response.status_code == 429
    assert "오늘 이 세션의 진단 실행 한도(10회)에 도달했습니다." in response.text
    assert "daily_limit_reached" in response.text
    assert "다시 가능:" in response.text
    assert repo.active_execution(conn, task_id) is None


def test_run_diagnosis_global_daily_limit_429(web, conn):
    task_id = create_task(web, diagnose_form())
    from workflow.server.auth import utc_now
    for n in range(60):
        repo.record_diagnosis_start(conn, f"sess-other-{n}", f"exec-seed-{n}", utc_now())
    response = web.post(f"/tasks/{task_id}/run", follow_redirects=False)
    assert response.status_code == 429
    assert "오늘 전체 진단 실행 한도(60회)에 도달했습니다." in response.text


def test_run_code_change_uses_predecessor_handoff_bundle(web, conn, store, settings):
    """선행 A 가 완료되고 handoff_bundle 산출물이 있으면, B 직접 실행 요청의 입력에 그 ID 가 고정된다."""
    task_a = create_task(web, diagnose_form())
    web.post(f"/tasks/{task_a}/run", follow_redirects=False)
    exec_a = repo.active_execution(conn, task_a)["execution_id"]
    bundle_id = seed_result_ready(
        conn, store, exec_a, kind="handoff_bundle",
        body={"contract_version": 1, "source_execution_id": exec_a,
              "diagnosis_result_artifact_id": "art-diag-result-001", "attachments": []},
        session_id=session_id_of(web, settings),
    )
    repo.update_task_status(conn, task_a, "완료", "판정 근거: 12/12", finished_at=NOW)
    repo.release_execution(conn, exec_a, NOW)
    # 온라인 판정이 서버 시각 기준 heartbeat_offline_seconds 이내인지 보므로 last_seen 은 실제 시각으로 둔다
    from workflow.server.auth import utc_now
    repo.update_registration(
        conn, "local-demo-report", connector_id="conn-mac-01", repository_id="demo-report-repo",
        base_commit=BASE_COMMIT, verification_profile_ids=["vp-pytest"], discovered={}, now=utc_now(),
    )
    task_b = create_task(web, fix_form(task_a, run_mode="manual"))
    assert "실행 가능" in detail(web, task_b)

    response = web.post(f"/tasks/{task_b}/run", follow_redirects=False)
    assert response.status_code == 303, response.text
    execution = repo.active_execution(conn, task_b)
    assert execution["assigned_connector_id"] == "conn-mac-01"
    assert execution["predecessor_execution_id"] == exec_a
    request = ExecutionRequest.model_validate_json(execution["request_json"])
    assert request.input_artifact_ids == [bundle_id]
    assert request.target.model_dump() == {
        "local_registration_id": "local-demo-report",
        "base_commit": BASE_COMMIT,
        "verification_profile_id": "vp-pytest",
    }


def test_run_code_change_without_registration_or_handoff_409(web, conn):
    task_a = create_task(web, diagnose_form())
    repo.update_task_status(conn, task_a, "완료", "판정 근거: 12/12", finished_at=NOW)
    task_b = create_task(web, fix_form(task_a, run_mode="manual"))
    response = web.post(f"/tasks/{task_b}/run", follow_redirects=False)
    assert response.status_code == 409
    assert "인계 자료" in response.text or "등록 정보" in response.text


# --- 검토 ------------------------------------------------------------------------


def test_review_approve_completes_and_marks_merge_pending(web, conn, store, settings):
    task_b, execution_id = seed_reviewable_fix(web, conn, store, settings)
    text = detail(web, task_b)
    assert "확인 필요" in text and "검토 대기" in text
    assert f'action="/tasks/{task_b}/review"' in text
    assert RESULT_COMMIT[:7] in text
    assert "ready_for_review" in text

    response = web.post(f"/tasks/{task_b}/review", data={"decision": "approve", "comment": ""}, follow_redirects=False)
    assert response.status_code == 303
    text = detail(web, task_b)
    assert "완료" in text and "검토 승인 · 병합: 운영자 확인 대기" in text
    row = repo.get_task(conn, task_b)
    assert (row["status"], row["review_decision"]) == ("완료", "approve")
    assert row["finished_at"] is not None
    assert repo.get_execution(conn, execution_id)["released_at"] is not None
    assert web.post(f"/tasks/{task_b}/review", data={"decision": "approve"}, follow_redirects=False).status_code == 409


def test_review_request_changes_creates_second_attempt(web, conn, store, settings):
    task_b, execution_id = seed_reviewable_fix(web, conn, store, settings)
    response = web.post(
        f"/tasks/{task_b}/review",
        data={"decision": "request_changes", "comment": "두 경로가 동시에 있는 응답 테스트가 없습니다."},
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text
    assert "실행 요청됨" in detail(web, task_b)

    executions = repo.list_executions(conn, task_b)
    assert [e["attempt_no"] for e in executions] == [1, 2]
    previous, current = executions
    assert previous["released_at"] is not None and current["released_at"] is None
    assert current["status"] == "queued"
    assert current["start_key"].startswith("req:")
    assert current["agent_id"] == "agent-codex-mac"

    review = [a for a in repo.artifacts_of(conn, execution_id) if a["kind"] == "review_comment"]
    assert len(review) == 1
    review_body = repo.read_artifact(conn, store, review[0]["artifact_id"]).decode()
    assert "두 경로가 동시에 있는 응답 테스트가 없습니다." in review_body
    assert f'"reviewed_execution_id":"{execution_id}"' in review_body

    request = ExecutionRequest.model_validate_json(current["request_json"])
    assert request.input_artifact_ids == ["art-handoff-001", previous["result_artifact_id"], review[0]["artifact_id"]]
    assert request.target.base_commit == RESULT_COMMIT
    assert request.task_revision == 1
    assert repo.get_task(conn, task_b)["review_decision"] == "request_changes"
    assert repo.get_task(conn, task_b)["finished_at"] is None


def test_review_close_fails_task(web, conn, store, settings):
    task_b, execution_id = seed_reviewable_fix(web, conn, store, settings)
    response = web.post(f"/tasks/{task_b}/review", data={"decision": "close", "comment": "중단"}, follow_redirects=False)
    assert response.status_code == 303
    text = detail(web, task_b)
    assert "실패" in text and "검토 거절" in text
    row = repo.get_task(conn, task_b)
    assert (row["status"], row["status_reason"], row["review_decision"]) == ("실패", "검토 거절", "close")
    assert repo.get_execution(conn, execution_id)["released_at"] is not None


def test_review_rejects_when_nothing_to_review(web, conn, store, settings):
    task_id = create_task(web, diagnose_form())
    assert web.post(f"/tasks/{task_id}/review", data={"decision": "approve"}, follow_redirects=False).status_code == 409
    task_b, _ = seed_reviewable_fix(web, conn, store, settings)
    assert web.post(f"/tasks/{task_b}/review", data={"decision": "maybe"}, follow_redirects=False).status_code == 422


# --- 산출물 ----------------------------------------------------------------------


def test_artifact_page_and_raw_download_are_session_scoped(app, web, conn, store, settings, agents):
    task_b, execution_id = seed_reviewable_fix(web, conn, store, settings)
    [artifact] = repo.artifacts_of(conn, execution_id)
    page = web.get(f"/tasks/{task_b}/artifacts/{artifact['artifact_id']}")
    assert page.status_code == 200
    assert "code_change_result" in page.text and RESULT_COMMIT in page.text
    assert "<pre" in page.text

    raw = web.get(f"/tasks/{task_b}/artifacts/{artifact['artifact_id']}?raw=1")
    assert raw.status_code == 200
    assert raw.headers["content-type"].startswith("application/json")
    assert raw.content == repo.read_artifact(conn, store, artifact["artifact_id"])

    other = TestClient(app)
    assert other.get(f"/tasks/{task_b}/artifacts/{artifact['artifact_id']}").status_code == 404
    assert web.get(f"/tasks/{task_b}/artifacts/art-none").status_code == 404
    task_a = create_task(web, diagnose_form())
    assert web.get(f"/tasks/{task_a}/artifacts/{artifact['artifact_id']}").status_code == 404  # 다른 업무의 산출물


# --- 에이전트 (읽기 전용) ----------------------------------------------------------


def test_agents_pages_hide_credentials(web, conn):
    listing = web.get("/agents")
    assert listing.status_code == 200
    assert "agent-ops-demo" in listing.text and "agent-codex-mac" in listing.text
    assert "env:DIAG_API_TOKEN" not in listing.text

    repo.update_registration(
        conn, "local-demo-report", connector_id="conn-mac-01", repository_id="demo-report-repo",
        base_commit=BASE_COMMIT, verification_profile_ids=["vp-pytest"],
        discovered={"instructions": ["AGENTS.md"], "confirmed": False}, now=NOW,
    )
    page = web.get("/agents/agent-codex-mac")
    assert page.status_code == 200
    assert "AGENTS.md" in page.text and "vp-pytest" in page.text and BASE_COMMIT in page.text
    assert "wfc_" not in page.text

    api = web.get("/agents/agent-ops-demo")
    assert api.status_code == 200
    assert "http://127.0.0.1:8100" in api.text
    assert "env:DIAG_API_TOKEN" not in api.text and "credential_ref" not in api.text
    assert web.get("/agents/nope").status_code == 404


# --- 운영자 ----------------------------------------------------------------------


def login_operator(client, token: str = "test-operator-token"):
    return client.post("/operator/login", data={"token": token}, follow_redirects=False)


def test_operator_login_rejects_wrong_token(web, conn, settings):
    assert login_operator(web, "wrong").status_code == 403
    assert repo.get_session(conn, session_id_of(web, settings))["is_operator"] == 0
    page = web.get("/operator")
    assert page.status_code == 200
    assert 'action="/operator/login"' in page.text
    assert 'action="/operator/agents"' not in page.text
    assert web.post("/operator/agents", data={"agent_id": "x"}, follow_redirects=False).status_code == 403
    assert web.post("/operator/connect-codes", follow_redirects=False).status_code == 403


def test_operator_login_then_issue_connect_code_and_exchange(app, web, conn, settings):
    response = login_operator(web)
    assert response.status_code == 303 and response.headers["location"] == "/operator"
    assert repo.get_session(conn, session_id_of(web, settings))["is_operator"] == 1
    assert "test-operator-token" not in response.headers.get("set-cookie", "")

    page = web.get("/operator")
    assert 'action="/operator/agents"' in page.text
    assert 'action="/operator/connect-codes"' in page.text
    assert "운영 진단 데모" in page.text
    assert "test-operator-token" not in page.text

    issued = web.post("/operator/connect-codes", follow_redirects=False)
    assert issued.status_code == 200
    match = re.search(r'id="issued-code">([^<]+)<', issued.text)
    assert match, issued.text
    code = match.group(1)
    assert "만료" in issued.text

    connector = TestClient(app)
    exchanged = connector.post("/connector/exchange", json={"contract_version": 1, "connect_code": code})
    assert exchanged.status_code == 200
    assert exchanged.json()["token"].startswith("wfc_")
    assert code in web.get("/operator").text  # 목록에 사용됨으로 남는다


def test_operator_revokes_unused_connect_code(web, conn):
    login_operator(web)
    issued = web.post("/operator/connect-codes")
    code = re.search(r'id="issued-code">([^<]+)<', issued.text).group(1)
    revoked = web.post(f"/operator/connect-codes/{code}/revoke", follow_redirects=False)
    assert revoked.status_code == 303
    [row] = repo.list_connect_codes(conn)
    assert row["revoked_at"] is not None
    assert web.post(f"/operator/connect-codes/{code}/revoke", follow_redirects=False).status_code == 404


def test_operator_registers_and_deletes_agent(web, conn):
    login_operator(web)
    response = web.post("/operator/agents", data={
        "agent_id": "agent-claude-mac",
        "name": "개인 Claude",
        "owner_scope": "personal",
        "connection_type": "local",
        "capability_code": "code.modify",
        "scope_value": "other-repo",
        "local_registration_id": "local-other",
        "api_url": "",
        "credential_ref": "",
    }, follow_redirects=False)
    assert response.status_code == 303, response.text
    row = repo.get_agent(conn, "agent-claude-mac")
    assert row["shared_to_all_sessions"] == 1 and row["connection_state"] == "unknown"
    assert row["local_registration_id"] == "local-other"
    assert "agent-claude-mac" in web.get("/agents").text

    bad = web.post("/operator/agents", data={
        "agent_id": "agent-api-2", "name": "x", "owner_scope": "company", "connection_type": "api",
        "capability_code": "operations.diagnose", "scope_value": "daily-report",
        "local_registration_id": "", "api_url": "http://127.0.0.1:8101", "credential_ref": "wfc_plain",
    }, follow_redirects=False)
    assert bad.status_code == 422
    assert repo.get_agent(conn, "agent-api-2") is None

    deleted = web.post("/operator/agents/agent-claude-mac/delete", follow_redirects=False)
    assert deleted.status_code == 303
    assert repo.get_agent(conn, "agent-claude-mac") is None
    assert web.post("/operator/agents/agent-claude-mac/delete", follow_redirects=False).status_code == 404


def test_operator_reregistration_keeps_connector_report(web, conn):
    login_operator(web)
    repo.update_registration(
        conn, "local-demo-report", connector_id="conn-mac-01", repository_id="demo-report-repo",
        base_commit=BASE_COMMIT, verification_profile_ids=["vp-pytest"], discovered={"k": 1}, now=NOW,
    )
    response = web.post("/operator/agents", data={
        "agent_id": "agent-codex-mac", "name": "개인 Codex (이름 수정)", "owner_scope": "personal",
        "connection_type": "local", "capability_code": "code.modify", "scope_value": "demo-report-repo",
        "local_registration_id": "local-demo-report", "api_url": "", "credential_ref": "",
    }, follow_redirects=False)
    assert response.status_code == 303
    row = repo.get_agent(conn, "agent-codex-mac")
    assert row["name"] == "개인 Codex (이름 수정)"
    assert row["connector_id"] == "conn-mac-01" and row["base_commit"] == BASE_COMMIT
    assert row["connection_state"] == "online"


def test_operator_sees_all_sessions_tasks_merge_queue_and_usage(app, web, conn, store, settings, agents):
    task_b, _ = seed_reviewable_fix(web, conn, store, settings)
    web.post(f"/tasks/{task_b}/review", data={"decision": "approve"}, follow_redirects=False)
    other = TestClient(app)
    other.get("/")
    other_task = create_task(other, diagnose_form())
    other.post(f"/tasks/{other_task}/run", follow_redirects=False)

    login_operator(web)
    page = web.get("/operator").text
    assert task_b in page and other_task in page
    assert f'action="/operator/merges/{task_b}/confirm"' in page
    assert "오늘 진단 실행" in page and ">1<" in page

    confirmed = web.post(f"/operator/merges/{task_b}/confirm", follow_redirects=False)
    assert confirmed.status_code == 303
    assert repo.get_task(conn, task_b)["merge_confirmed_at"] is not None
    assert f'action="/operator/merges/{task_b}/confirm"' not in web.get("/operator").text
    assert web.post(f"/operator/merges/{other_task}/confirm", follow_redirects=False).status_code == 409


def test_operator_token_never_appears_in_html(web):
    login_operator(web)
    for path in ("/", "/operator", "/agents", "/tasks/new"):
        assert "test-operator-token" not in web.get(path).text
