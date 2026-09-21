"""web.py — 심사자 세션·운영자 웹 라우트. 마크업이 아니라 렌더된 텍스트·리다이렉트·상태 코드를 본다 (Step 7 이 화면을 꾸민다)."""

import html as html_lib
import json
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


def register_agents(client, *agent_ids: str) -> None:
    """세션이 카탈로그에서 Agent 를 등록한다 (`POST /agents/register`). 기본은 운영 진단·개인 Codex 둘 다."""
    for agent_id in agent_ids or ("agent-ops-demo", "agent-codex-mac"):
        response = client.post("/agents/register", data={"agent_id": agent_id}, follow_redirects=False)
        assert response.status_code == 303, response.text


@pytest.fixture
def web(client, agents):
    """홈을 한 번 열어 세션 쿠키를 받고, 카탈로그 2개를 등록한 클라이언트."""
    assert client.get("/tasks").status_code == 200
    register_agents(client)
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
    """새 세션 홈: 등록 Agent 0개 → 안내 문장과 `에이전트 등록` 링크, `업무 가져오기` 는 비활성."""
    first = client.get("/tasks")
    assert first.status_code == 200
    assert SESSION_COOKIE in first.cookies
    assert "아직 업무가 없습니다." in first.text
    assert "GitHub·Jira 이슈를 가져오면 순서와 담당 에이전트가 자동으로 구성됩니다." in first.text
    assert "먼저 에이전트를 등록하세요." in first.text
    assert 'href="/agents/register"' in first.text
    assert "운영 진단 데모" not in first.text and "개인 Codex" not in first.text  # 등록 전에는 카드 없음
    button = re.search(r"<button[^>]*>업무 가져오기</button>", first.text)
    assert button and "disabled" in button.group(0), first.text
    assert "에이전트를 먼저 등록하세요" in first.text
    assert 'class="btn" href="/tasks/import"' not in first.text
    assert "시연 업무 만들기" not in first.text

    second = client.get("/tasks")
    assert "set-cookie" not in second.headers
    assert second.status_code == 200


def test_home_after_registration_shows_cards_and_enables_import_button(web):
    text = web.get("/tasks").text
    assert "운영 진단 데모" in text and "개인 Codex" in text
    assert "먼저 에이전트를 등록하세요." not in text
    assert 'class="btn" href="/tasks/import"' in text and "에이전트를 먼저 등록하세요" not in text
    assert 'href="/tasks/new"' in text  # 직접 등록은 보조 버튼
    assert "시연 업무 만들기" not in text
    assert 'href="/agents/register"' in text  # 더 등록할 수 있다


def test_landing_is_public_and_links_to_app(client, agents):
    page = client.get("/")
    assert page.status_code == 200
    assert SESSION_COOKIE not in page.cookies  # 랜딩은 세션을 만들지 않는다
    text = page.text
    assert '/static/hero.jpg' in text
    assert "Work flows. Agents continue." in text
    assert "앞 업무가 끝나는 순간 다음 에이전트가 이어서 일합니다" in text
    assert "서비스 바로 가기" in text and 'href="/tasks"' in text
    assert "에이전트 등록" in text and "업무 가져오기" in text and "워크플로우 자동 구성" in text and "자동 실행" in text
    assert 'class="shell' not in text  # 앱 셸(사이드바·뷰어) 없이 단독 페이지
    assert client.get("/static/hero.jpg").status_code == 200


def test_app_home_has_agent_and_task_sections_with_direct_register(client, agents):
    text = client.get("/tasks").text
    assert 'href="/tasks/new"' in text  # 시연 예시 없이 직접 등록
    assert 'href="/agents"' in text
    assert 'href="/"' in text  # 사이드바 브랜드 → 랜딩
    assert '/static/logo.jpg' not in text  # 로고는 랜딩에만


def test_home_lists_my_tasks_with_status(web):
    task_id = create_task(web, diagnose_form())
    text = web.get("/tasks").text
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


def test_diagnose_example_form_offers_successor_checked(web):
    """시연 폼은 후속 B 동시 등록 체크박스를 기본 체크로 보여 준다. 빈 폼·fix 폼에는 없다."""
    text = web.get("/tasks/new?example=diagnose").text
    assert 'name="with_successor"' in text and "checked" in text.split('name="with_successor"')[1][:80]
    assert "A 완료 시 자동 착수" in text
    assert 'name="with_successor"' not in web.get("/tasks/new").text
    task_a = create_task(web, diagnose_form())
    assert 'name="with_successor"' not in web.get(f"/tasks/new?example=fix&predecessor={task_a}").text


def test_create_diagnose_with_successor_registers_b_waiting_on_a(web, conn):
    """with_successor 면 A 와 fix 예시 B 가 한 번에 만들어지고 B 는 A 를 선행으로 자동 실행 대기한다."""
    task_a = create_task(web, diagnose_form(with_successor="1"))
    tasks = repo.list_tasks(conn, repo.get_task(conn, task_a)["session_id"])
    assert len(tasks) == 2
    task_b = next(t for t in tasks if t["task_id"] != task_a)
    assert task_b["title"] == EXAMPLES["fix"]["title"]
    assert task_b["predecessor_task_id"] == task_a
    assert task_b["kind"] == "code_change" and task_b["run_mode"] == "auto" and task_b["completion_mode"] == "review"
    assert task_b["status"] == "대기" and task_b["status_reason"] == "선행 대기"
    text = detail(web, task_a)
    assert "실행 가능" in text and "보고서 변환기 수정" in text  # A 화면에 후속 링크
    # 체크 안 하면 A 만
    task_c = create_task(web, diagnose_form())
    assert len(repo.list_tasks(conn, repo.get_task(conn, task_c)["session_id"])) == 3


def test_create_with_successor_counts_both_against_active_limit(app, web, conn):
    settings = app.state.settings
    for _ in range(settings.limits.active_tasks_per_session - 1):
        create_task(web, diagnose_form())
    response = web.post("/tasks", data=diagnose_form(with_successor="1"), follow_redirects=False)
    assert response.status_code == 429
    assert len(repo.list_tasks(conn, repo.get_task(conn, create_task(web, diagnose_form()))["session_id"])) == settings.limits.active_tasks_per_session


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
    register_agents(web, "agent-ops-second")
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
        "kind_spec": None,
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
    register_agents(web, "agent-ops-second")
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
        body={"contract_version": 1, "source_execution_id": exec_a, "source_kind": "diagnosis",
              "source_result_artifact_id": "art-diag-result-001", "inputs": [], "attachments": []},
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


# --- 에이전트 등록 — 카탈로그에서 세션이 고른다 (phase 5 step 2, ADR-0005) --------------------


DISCOVERED = {
    "found": {
        "AGENTS.md": "# demo-report-repo 지침 본문 …",
        "codex_config": False,
        "claude_config": False,
        "pyproject": {"name": "demo-report", "pytest_configured": True},
        "tests_dir": True,
        "git": {"remotes": [], "head": "0c1ddcf6ecd35d20c49dc9b0868f3cabf1f2afa0"},
    },
    "not_read": ["CLAUDE.md"],
    "verification_level": "설정 발견",
}


def test_register_page_lists_catalog_with_discovered_summary_and_no_secrets(client, conn, agents):
    client.get("/tasks")
    repo.update_registration(
        conn, "local-demo-report", connector_id="conn-mac-01", repository_id="demo-report-repo",
        base_commit=BASE_COMMIT, verification_profile_ids=["vp-pytest", "vp-report"],
        discovered=DISCOVERED, now=NOW,
    )
    page = client.get("/agents/register")
    assert page.status_code == 200
    text = page.text
    assert "이미 쓰는 에이전트를 골라 등록합니다." in text
    assert "운영 진단 데모" in text and "개인 Codex" in text
    assert text.count('action="/agents/register"') == 2  # 카드마다 등록 버튼, 아직 등록됨 없음
    assert "등록됨" not in text and "/unregister" not in text
    # 발견된 정보 요약 — 키만. 값(파일 본문·커밋)은 넣지 않는다
    assert "AGENTS.md" in text and "git.head" in text and "tests_dir" in text and "pyproject.name" in text
    assert "codex_config" not in text and "git.remotes" not in text  # False·빈 목록은 발견이 아니다
    assert "지침 본문" not in text and "0c1ddcf6" not in text
    assert "vp-pytest" in text and "vp-report" in text
    assert "operations.diagnose · workflow_id=daily-report" in text  # API 에이전트는 능력 역할·범위
    for secret in ("api_url", "credential_ref", "env:DIAG_API_TOKEN", "http://127.0.0.1:8100", "wfc_"):
        assert secret not in text, secret


def test_register_agent_appears_on_home_and_list_and_is_idempotent(client, conn, agents, settings):
    client.get("/tasks")
    listing = client.get("/agents").text
    assert "등록한 에이전트가 없습니다." in listing and 'href="/agents/register"' in listing
    assert "agent-ops-demo" not in listing

    response = client.post("/agents/register", data={"agent_id": "agent-ops-demo"}, follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == "/tasks"
    home = client.get("/tasks").text
    assert "운영 진단 데모" in home and "개인 Codex" not in home
    listing = client.get("/agents").text
    assert "agent-ops-demo" in listing and "agent-codex-mac" not in listing
    assert "등록한 에이전트가 없습니다." not in listing

    register = client.get("/agents/register").text
    assert "등록됨" in register and 'action="/agents/agent-ops-demo/unregister"' in register
    assert 'action="/agents/register"' in register  # 아직 안 한 Codex 는 등록 버튼

    client.post("/agents/register", data={"agent_id": "agent-ops-demo"}, follow_redirects=False)
    assert len(repo.list_session_agents(conn, session_id_of(client, settings))) == 1
    assert client.post("/agents/register", data={"agent_id": "agent-none"}, follow_redirects=False).status_code == 404
    assert client.post("/agents/register", data={"agent_id": ""}, follow_redirects=False).status_code == 404


def test_agent_detail_opens_for_catalog_and_registered_only(client, conn, agents):
    client.get("/tasks")
    assert client.get("/agents/agent-ops-demo").status_code == 200  # 카탈로그 — 등록 전에도 열린다
    repo.upsert_agent(conn, {
        "agent_id": "agent-private", "name": "비공개", "owner_scope": "personal", "connection_type": "local",
        "local_registration_id": "local-private",
        "capabilities": [{"code": "code.modify", "scope": {"repository_id": "other"}}],
        "shared_to_all_sessions": False,
    })
    assert client.get("/agents/agent-private").status_code == 404
    assert client.post("/agents/register", data={"agent_id": "agent-private"}, follow_redirects=False).status_code == 404


def test_manual_choice_of_unregistered_agent_is_422(client, agents):
    client.get("/tasks")
    register_agents(client, "agent-ops-demo")
    response = client.post(
        "/tasks", data=diagnose_form(selection_mode="manual", chosen_agent_id="agent-codex-mac"),
        follow_redirects=False,
    )
    assert response.status_code == 422
    assert "agent_not_registered" in response.text and "등록하지 않은 에이전트입니다" in response.text
    # 등록 폼의 직접 선택 목록에도 등록한 것만
    form = client.get("/tasks/new").text
    assert 'value="agent-ops-demo"' in form and 'value="agent-codex-mac"' not in form


def test_auto_selection_counts_only_registered_agents(client, conn, agents):
    """운영 진단을 등록하지 않으면 진단 업무는 카탈로그에 있어도 `확인 필요 · 후보 없음`. 직접 선택도 등록한 것만."""
    client.get("/tasks")
    register_agents(client, "agent-codex-mac")
    task_id = create_task(client, diagnose_form())
    text = detail(client, task_id)
    assert "확인 필요" in text and "후보 없음" in text
    assert f'action="/tasks/{task_id}/select"' in text
    assert 'value="agent-ops-demo"' not in text and 'value="agent-codex-mac"' in text  # 선택 목록도 세션 등록만
    blocked = client.post(f"/tasks/{task_id}/select", data={"agent_id": "agent-ops-demo"}, follow_redirects=False)
    assert blocked.status_code == 422 and "agent_not_registered" in blocked.text
    assert repo.get_selection(conn, task_id).status == "needs_selection"

    register_agents(client, "agent-ops-demo")
    chosen = client.post(f"/tasks/{task_id}/select", data={"agent_id": "agent-ops-demo"}, follow_redirects=False)
    assert chosen.status_code == 303
    assert "실행 가능" in detail(client, task_id)


def test_unregister_agent_and_409_when_task_in_progress(web, conn, settings):
    task_id = create_task(web, diagnose_form())  # agent-ops-demo 선택됨, 미완료
    blocked = web.post("/agents/agent-ops-demo/unregister", follow_redirects=False)
    assert blocked.status_code == 409
    assert "agent_in_use" in blocked.text and "진행 중인 업무가 있어 해제할 수 없습니다" in blocked.text
    assert repo.is_session_agent(conn, session_id_of(web, settings), "agent-ops-demo")

    response = web.post("/agents/agent-codex-mac/unregister", follow_redirects=False)  # 선택된 업무 없음
    assert response.status_code == 303
    assert not repo.is_session_agent(conn, session_id_of(web, settings), "agent-codex-mac")
    assert "개인 Codex" not in web.get("/tasks").text
    assert web.post("/agents/agent-codex-mac/unregister", follow_redirects=False).status_code == 303  # 멱등
    assert web.post("/agents/agent-none/unregister", follow_redirects=False).status_code == 404

    repo.update_task_status(conn, task_id, "실패", "검토 거절", finished_at=NOW)
    assert web.post("/agents/agent-ops-demo/unregister", follow_redirects=False).status_code == 303
    assert "먼저 에이전트를 등록하세요." in web.get("/tasks").text


def test_registration_is_isolated_per_session(app, web, conn, settings):
    other = TestClient(app)
    home = other.get("/tasks").text
    assert "먼저 에이전트를 등록하세요." in home
    assert "운영 진단 데모" not in home and "개인 Codex" not in home
    assert "등록됨" not in other.get("/agents/register").text
    assert len(repo.list_session_agents(conn, session_id_of(web, settings))) == 2
    # 다른 세션의 해제는 이 세션에 영향 없음
    other.post("/agents/register", data={"agent_id": "agent-ops-demo"}, follow_redirects=False)
    assert other.post("/agents/agent-ops-demo/unregister", follow_redirects=False).status_code == 303
    assert repo.is_session_agent(conn, session_id_of(web, settings), "agent-ops-demo")


def test_scripted_agent_is_labelled_on_cards_and_result_card(web, conn, store, settings):
    codex = repo.get_agent(conn, "agent-codex-mac")
    repo.upsert_agent(conn, {
        "agent_id": "agent-codex-mac", "name": codex["name"], "owner_scope": codex["owner_scope"],
        "connection_type": "local", "local_registration_id": codex["local_registration_id"],
        "capabilities": [{"code": "code.modify", "scope": {"repository_id": "demo-report-repo"}}],
        "connection_state": codex["connection_state"], "shared_to_all_sessions": True, "demo_scripted": True,
    })
    for path in ("/tasks", "/agents/register", "/agents/agent-codex-mac"):
        text = web.get(path).text
        assert "시연용 · 대본 재생" in text, path
        assert text.count("시연용 · 대본 재생") == 1, path  # 운영 진단은 대본이 아니다
    assert "시연용 · 대본 재생" not in web.get("/agents/agent-ops-demo").text

    task_b, _ = seed_reviewable_fix(web, conn, store, settings)
    text = detail(web, task_b)
    card = text[text.index('class="result-card'):text.index('class="viewer')]
    assert "대본 재생 (실제 모델 호출 없음)" in card
    assert "대본 재생" not in detail(web, create_task(web, diagnose_form()))  # 결과 없음


# --- 업무 가져오기 — GitHub·Jira fixture → 체인 (phase 5 step 5) -------------------------


def import_issues(client, source: str, *keys: str):
    return client.post(
        "/tasks/import", data={"source": source, "issue_keys": list(keys)}, follow_redirects=False,
    )


def test_import_page_defaults_to_github_tab_with_mapping_preview(web):
    text = web.get("/tasks/import").text
    assert "시연 데이터입니다 — 실제 GitHub·Jira 에 연결하지 않습니다." in text
    assert 'href="/tasks/import?source=github"' in text and 'href="/tasks/import?source=jira"' in text
    assert "GitHub Issues" in text and "Jira" in text
    for key in ("#41", "#42", "#43", "#44"):
        assert f'value="{key}" checked' in text, key
    assert "OPS-41" not in text
    assert "일일 보고서 생성 실패 (09-20 09:00)" in text and "README 오타 수정" in text
    assert "operations.diagnose · daily-report" in text
    assert "code.modify · demo-report-repo" in text
    assert "맡을 에이전트 없음 · 맞는 능력 코드 없음 (라벨: enhancement, repo:demo-report-repo)" in text
    assert "맡을 에이전트 없음 · 맞는 능력 코드 없음 (라벨: docs)" in text
    assert "blocked by #41" in text and "blocked by #42" in text
    assert "workflow:daily-report" in text and "run:daily-0920-0900" in text  # 라벨 칩
    button = re.search(r"<button[^>]*>가져와서 워크플로우 구성</button>", text)
    assert button and "disabled" not in button.group(0)
    assert web.get("/tasks/import?source=svn").status_code == 422


def test_import_page_jira_tab_lists_ops_keys(web):
    text = web.get("/tasks/import?source=jira").text
    for key in ("OPS-41", "OPS-42", "OPS-43", "OPS-44"):
        assert f'value="{key}" checked' in text, key
    assert 'value="#41"' not in text
    assert "blocked by OPS-41" in text
    assert "operations.diagnose · daily-report" in text


def test_import_page_disables_button_without_registered_agent(client, agents):
    client.get("/tasks")
    text = client.get("/tasks/import").text
    button = re.search(r"<button[^>]*>가져와서 워크플로우 구성</button>", text)
    assert button and "disabled" in button.group(0), text
    assert "에이전트를 먼저 등록하세요" in text and 'href="/agents/register"' in text
    assert import_issues(client, "github", "#41", "#42").status_code == 422


def test_import_fixture_builds_chain_of_two_tasks_and_skips_the_rest(web, conn, settings):
    response = import_issues(web, "github", "#41", "#42", "#43", "#44")
    assert response.status_code == 303, response.text
    location = response.headers["location"]
    assert location.startswith("/chains/chain-")
    chain_id = location.removeprefix("/chains/")
    session_id = session_id_of(web, settings)

    chain = repo.get_chain(conn, chain_id)
    assert chain["session_id"] == session_id and chain["source"] == "github"
    assert chain["title"] == "일일 보고서 생성 실패 (09-20 09:00) → 집계 API 응답 형식 변경 대응"
    assert chain["started_at"] is None
    skipped = json.loads(chain["skipped_json"])
    assert [s["key"] for s in skipped] == ["#43", "#44"]
    assert skipped[0]["title"] == "변경 응답 형식 모니터링 알림 추가"
    assert "맞는 능력 코드 없음" in skipped[0]["reason"] and "docs" in skipped[1]["reason"]

    tasks = repo.tasks_of_chain(conn, chain_id)
    assert [t["source_ref"] for t in tasks] == ["#41", "#42"]
    assert {t["chain_id"] for t in tasks} == {chain_id}
    task_a, task_b = tasks
    assert task_a["title"] == "일일 보고서 생성 실패 (09-20 09:00)" and task_a["request"] == DIAGNOSE_REQUEST
    assert task_a["kind"] == "diagnosis" and task_a["run_mode"] == "manual"
    assert task_a["completion_mode"] == "auto" and task_a["selection_mode"] == "auto"
    assert task_a["predecessor_task_id"] is None and task_a["status"] == "실행 가능"
    assert json.loads(task_a["target_json"]) == {"run_id": "daily-0920-0900"}
    assert task_b["title"] == "집계 API 응답 형식 변경 대응" and task_b["request"] == FIX_REQUEST
    assert task_b["kind"] == "code_change" and task_b["run_mode"] == "auto"
    assert task_b["completion_mode"] == "review"
    assert task_b["predecessor_task_id"] == task_a["task_id"]
    assert task_b["status"] == "대기" and task_b["status_reason"] == "선행 대기"
    assert repo.get_selection(conn, task_a["task_id"]).selected_agent_id == "agent-ops-demo"
    assert repo.get_selection(conn, task_b["task_id"]).selected_agent_id == "agent-codex-mac"
    assert len(repo.list_tasks(conn, session_id)) == 2  # #43·#44 는 Task 가 아니다
    assert repo.active_execution(conn, task_a["task_id"]) is None  # 실행은 체인 화면(step 6)에서만

    home = web.get("/tasks").text
    assert "일일 보고서 생성 실패 (09-20 09:00)" in home and "집계 API 응답 형식 변경 대응" in home
    assert "README 오타 수정" not in home


def test_import_with_codex_and_claude_picks_first_registered_agent(web, conn):
    seed_agents(conn, with_claude=True)
    register_agents(web, "agent-claude-mac")  # 등록 순서: ops → codex → claude
    response = import_issues(web, "github", "#41", "#42")
    assert response.status_code == 303, response.text
    chain_id = response.headers["location"].removeprefix("/chains/")
    task_b = repo.tasks_of_chain(conn, chain_id)[1]
    selection = repo.get_selection(conn, task_b["task_id"])
    assert selection.task_id == task_b["task_id"]  # PlanNode 의 임시 task_id(issue.key) 가 아니다
    assert selection.status == "selected" and selection.selected_agent_id == "agent-codex-mac"
    assert selection.candidate_count == 1
    assert "먼저 등록한 agent-codex-mac" in selection.reason
    assert json.loads(task_b["target_json"])["local_registration_id"] == "local-demo-report"
    assert "먼저 등록한 agent-codex-mac" in detail(web, task_b["task_id"])
    # 직접 등록 경로는 그대로 — 동률이면 확인 필요
    task_c = create_task(web, fix_form(""))
    assert repo.get_selection(conn, task_c).status == "needs_selection"


def test_import_single_fix_issue_is_standalone_manual_task(web, conn):
    response = import_issues(web, "github", "#42")
    assert response.status_code == 303, response.text
    chain_id = response.headers["location"].removeprefix("/chains/")
    chain = repo.get_chain(conn, chain_id)
    assert chain["title"] == "집계 API 응답 형식 변경 대응" and json.loads(chain["skipped_json"]) == []
    (task,) = repo.tasks_of_chain(conn, chain_id)
    assert task["kind"] == "code_change" and task["predecessor_task_id"] is None
    assert task["run_mode"] == "manual" and task["completion_mode"] == "review"
    assert task["source_ref"] == "#42"
    # 선행 대기가 아니라 연결 프로그램 오프라인(conftest 의 Codex 는 unknown) 때문에 대기 — PRD 3절
    assert task["status"] == "대기" and task["status_reason"].startswith("연결 끊김")
    assert repo.get_selection(conn, task["task_id"]).selected_agent_id == "agent-codex-mac"


@pytest.mark.parametrize("keys", [(), ("#99",), ("OPS-41",)])
def test_import_without_known_issue_is_422(web, conn, settings, keys):
    response = import_issues(web, "github", *keys)
    assert response.status_code == 422, keys
    assert "no_issues" in response.text
    assert repo.list_chains(conn, session_id_of(web, settings)) == []
    assert import_issues(web, "svn", "#41").status_code == 422


def test_import_counts_all_new_tasks_against_active_limit(app, web, conn, settings):
    limit = app.state.settings.limits.active_tasks_per_session
    for _ in range(limit - 1):
        create_task(web, diagnose_form())
    response = import_issues(web, "github", "#41", "#42", "#43", "#44")
    assert response.status_code == 429
    assert f"세션당 활성 업무 한도({limit}개)에 도달했습니다." in response.text
    session_id = session_id_of(web, settings)
    assert len(repo.list_tasks(conn, session_id)) == limit - 1
    assert repo.list_chains(conn, session_id) == []
    assert import_issues(web, "github", "#41").status_code == 303  # 노드 1개는 들어간다


def test_import_chain_is_isolated_per_session(app, web, conn, agents):
    chain_id = import_issues(web, "github", "#41", "#42").headers["location"].removeprefix("/chains/")
    task_id = repo.tasks_of_chain(conn, chain_id)[0]["task_id"]
    assert web.get(f"/chains/{chain_id}").status_code == 200
    other = TestClient(app)
    assert other.get(f"/chains/{chain_id}").status_code == 404
    assert other.get(f"/chains/{chain_id}/live").status_code == 404
    assert other.post(f"/chains/{chain_id}/start", follow_redirects=False).status_code == 404
    assert other.get(f"/tasks/{task_id}").status_code == 404
    assert "일일 보고서 생성 실패" not in other.get("/tasks").text
    assert web.get("/chains/chain-none").status_code == 404
    assert web.get("/chains/chain-none/live").status_code == 404


# --- 워크플로우 화면 — 체인 상세·시작·라이브 (phase 5 step 6) --------------------------------


def import_chain(client, conn, *keys: str) -> tuple[str, list[str]]:
    """가져오기 뒤 (chain_id, 체인 순서의 task_id 목록)."""
    response = import_issues(client, "github", *(keys or ("#41", "#42", "#43", "#44")))
    assert response.status_code == 303, response.text
    chain_id = response.headers["location"].removeprefix("/chains/")
    return chain_id, [t["task_id"] for t in repo.tasks_of_chain(conn, chain_id)]


def chain_page(client, chain_id: str) -> str:
    response = client.get(f"/chains/{chain_id}")
    assert response.status_code == 200, response.text
    return response.text


def start_button(text: str):
    return re.search(r"<button[^>]*>워크플로우 시작</button>", text)


def test_chain_page_shows_nodes_in_order_with_assignment_reasons_and_start_button(web, conn):
    chain_id, (task_a, task_b) = import_chain(web, conn)
    text = chain_page(web, chain_id)

    assert "워크플로우" in text and "일일 보고서 생성 실패 (09-20 09:00) → 집계 API 응답 형식 변경 대응" in text
    assert "GitHub Issues" in text and "시연 데이터" in text
    assert text.index("#41") < text.index("#42")
    assert f'href="/tasks/{task_a}"' in text and f'href="/tasks/{task_b}"' in text
    assert "운영 진단 데모" in text and "개인 Codex" in text
    assert "진단" in text and "코드 수정" in text
    assert "직접" in text and "선행 완료 시 자동" in text
    assert "자동 완료" in text and "검토 후 완료" in text
    assert 'data-status="실행 가능"' in text and "agent-ops-demo 선택됨" in text
    assert 'data-status="대기"' in text and "선행 대기" in text
    # 접이식 이유 — 구성 이유 문장 + SelectionRecord.reason
    assert "체인의 첫 업무 — 선행 없음" in text
    assert "선행 #41 (operations.diagnose) → code.modify 인계" in text
    assert "operations.diagnose · workflow_id=daily-report 일치 후보 1개" in text
    # 사람 단계
    assert "검토 승인 (사람) · 병합은 운영자 확인" in text
    # 넣지 않은 이슈
    assert "워크플로우에 넣지 않은 이슈" in text
    assert "#43" in text and "변경 응답 형식 모니터링 알림 추가" in text
    assert "맞는 능력 코드 없음 (라벨: enhancement, repo:demo-report-repo)" in text
    assert "#44" in text and "README 오타 수정" in text
    # 동작 영역 — 시작 버튼만. 두 번째 노드를 직접 실행하는 버튼은 없다 (워커가 잇는다)
    button = start_button(text)
    assert button and "disabled" not in button.group(0)
    assert f'action="/chains/{chain_id}/start"' in text
    assert f'action="/tasks/{task_b}/run"' not in text and f'action="/tasks/{task_a}/run"' not in text
    assert f'data-live="/chains/{chain_id}/live"' in text


def test_chain_start_runs_first_task_once_and_marks_started(web, conn):
    chain_id, (task_a, task_b) = import_chain(web, conn, "#41", "#42")
    response = web.post(f"/chains/{chain_id}/start", follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == f"/chains/{chain_id}"

    execution = repo.active_execution(conn, task_a)
    assert execution is not None and execution["status"] == "queued"
    assert repo.active_execution(conn, task_b) is None  # 후속은 워커가 선행 완료를 보고 잇는다
    assert repo.get_chain(conn, chain_id)["started_at"] is not None
    text = chain_page(web, chain_id)
    assert 'data-status="실행 요청됨"' in text and "접수 대기" in text
    assert start_button(text) is None
    assert "2단계 중 1단계 실행 요청됨" in text
    assert "담당 변경" not in text

    again = web.post(f"/chains/{chain_id}/start", follow_redirects=False)
    assert again.status_code == 303  # 멱등
    assert len(repo.list_executions(conn, task_a)) == 1


def test_chain_start_diagnosis_limit_429(web, conn, settings):
    chain_id, _ = import_chain(web, conn, "#41", "#42")
    from workflow.server.auth import utc_now
    for n in range(10):
        repo.record_diagnosis_start(conn, session_id_of(web, settings), f"exec-seed-{n}", utc_now())
    response = web.post(f"/chains/{chain_id}/start", follow_redirects=False)
    assert response.status_code == 429
    assert "오늘 이 세션의 진단 실행 한도(10회)에 도달했습니다." in response.text
    assert repo.get_chain(conn, chain_id)["started_at"] is None


def test_chain_start_requires_first_node_selection(client, conn, agents):
    client.get("/tasks")
    register_agents(client, "agent-codex-mac")  # 진단 Agent 미등록 → #41 후보 없음
    chain_id, (task_a, task_b) = import_chain(client, conn, "#41", "#42")
    text = chain_page(client, chain_id)
    assert 'data-status="확인 필요"' in text and "후보 없음" in text
    button = start_button(text)
    assert button and "disabled" in button.group(0)
    assert "담당 에이전트를 먼저 확정하세요" in text
    assert f'action="/tasks/{task_a}/select"' in text

    blocked = client.post(f"/chains/{chain_id}/start", follow_redirects=False)
    assert blocked.status_code == 409 and "selection_required" in blocked.text
    assert repo.get_chain(conn, chain_id)["started_at"] is None

    # 진단 Agent 를 등록하고 체인 화면의 인라인 폼으로 확정하면 체인 화면으로 돌아오고 시작할 수 있다
    register_agents(client, "agent-ops-demo")
    chosen = client.post(
        f"/tasks/{task_a}/select", data={"agent_id": "agent-ops-demo", "return_to": "chain"}, follow_redirects=False,
    )
    assert chosen.status_code == 303 and chosen.headers["location"] == f"/chains/{chain_id}"
    text = chain_page(client, chain_id)
    assert "disabled" not in start_button(text).group(0)
    assert client.post(f"/chains/{chain_id}/start", follow_redirects=False).status_code == 303
    assert repo.active_execution(conn, task_a) is not None


def test_chain_reassigns_tied_node_before_start_only(web, conn):
    seed_agents(conn, with_claude=True)
    register_agents(web, "agent-claude-mac")
    chain_id, (task_a, task_b) = import_chain(web, conn, "#41", "#42")
    text = chain_page(web, chain_id)
    assert "먼저 등록한 agent-codex-mac 를 기본 선택" in text
    assert "담당 변경" in text and f'action="/tasks/{task_b}/select"' in text
    assert "disabled" not in start_button(text).group(0)  # 동률이라도 기본 선택돼 시작 가능

    response = web.post(
        f"/tasks/{task_b}/select", data={"agent_id": "agent-claude-mac", "return_to": "chain"}, follow_redirects=False,
    )
    assert response.status_code == 303 and response.headers["location"] == f"/chains/{chain_id}"
    selection = repo.get_selection(conn, task_b)
    assert selection.mode == "manual" and selection.selected_agent_id == "agent-claude-mac"
    assert repo.get_task(conn, task_b)["selection_mode"] == "manual"
    assert json.loads(repo.get_task(conn, task_b)["target_json"])["local_registration_id"] == "local-demo-report-claude"
    assert "Claude Code" in chain_page(web, chain_id)

    assert web.post(f"/chains/{chain_id}/start", follow_redirects=False).status_code == 303
    assert "담당 변경" not in chain_page(web, chain_id)
    after = web.post(f"/tasks/{task_b}/select", data={"agent_id": "agent-codex-mac", "return_to": "chain"}, follow_redirects=False)
    assert after.status_code == 409
    assert repo.get_selection(conn, task_b).selected_agent_id == "agent-claude-mac"


def test_chain_live_fragment_carries_node_status(web, conn):
    chain_id, (task_a, task_b) = import_chain(web, conn, "#41", "#42")
    response = web.get(f"/chains/{chain_id}/live")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert "<html" not in response.text and "<script" not in response.text
    assert f'data-live="/chains/{chain_id}/live"' in response.text
    assert 'data-status="실행 가능"' in response.text and 'data-status="대기"' in response.text
    assert 'data-poll="1"' in response.text  # 대기 노드가 있는 동안 폴링
    assert f'action="/chains/{chain_id}/start"' in response.text

    web.post(f"/chains/{chain_id}/start", follow_redirects=False)
    assert 'data-status="실행 요청됨"' in web.get(f"/chains/{chain_id}/live").text


def test_chain_human_gate_follows_last_task_review(web, conn, store, settings):
    chain_id, (task_a, task_b) = import_chain(web, conn, "#41", "#42")
    web.post(f"/chains/{chain_id}/start", follow_redirects=False)
    exec_a = repo.active_execution(conn, task_a)["execution_id"]
    repo.update_task_status(conn, task_a, "완료", "판정 근거: 12/12", finished_at=NOW)
    repo.release_execution(conn, exec_a, NOW)
    repo.create_execution(
        conn, execution_id="exec-fix-001", task_id=task_b, attempt_no=1, start_key=f"auto:{task_b}:r1",
        agent_id="agent-codex-mac", kind="code_change",
        request=ExecutionRequest.model_validate({
            "contract_version": 1, "execution_id": "exec-fix-001", "task_id": task_b, "kind": "code_change",
            "agent_id": "agent-codex-mac", "task_revision": 1, "request": FIX_REQUEST,
            "input_artifact_ids": ["art-handoff-001"],
            "target": {"local_registration_id": "local-demo-report", "base_commit": BASE_COMMIT,
                       "verification_profile_id": "vp-pytest"},
        }),
        assigned_connector_id=None, predecessor_execution_id=exec_a, now=NOW,
    )
    seed_result_ready(
        conn, store, "exec-fix-001", kind="code_change_result",
        body=code_change_result("exec-fix-001", task_b), session_id=session_id_of(web, settings),
    )
    text = chain_page(web, chain_id)
    gate = text[text.index("검토 승인 (사람)"):]
    assert 'data-status="확인 필요"' in gate and "검토 대기" in gate
    assert f'href="/tasks/{task_b}"' in gate  # Task 상세의 검토 폼으로
    assert "2단계 중 2단계 확인 필요" in text
    assert 'data-poll="0"' in text  # 사람 차례 — 폴링 없음

    web.post(f"/tasks/{task_b}/review", data={"decision": "approve", "comment": ""}, follow_redirects=False)
    text = chain_page(web, chain_id)
    gate = text[text.index("검토 승인 (사람)"):]
    assert 'data-status="완료"' in gate and "병합: 운영자 확인 대기" in gate
    assert "병합 확인됨" not in gate
    assert "2단계 모두 완료" in text

    login_operator(web)
    web.post(f"/operator/merges/{task_b}/confirm", follow_redirects=False)
    gate = chain_page(web, chain_id)
    assert "병합 확인됨" in gate[gate.index("검토 승인 (사람)"):]


def test_home_lists_chains_with_progress(web, conn):
    chain_id, (task_a, task_b) = import_chain(web, conn)
    home = web.get("/tasks").text
    assert "워크플로우" in home and f'href="/chains/{chain_id}"' in home
    assert "0/2 완료" in home and "시작 전" in home
    main = home[home.index('class="main'):]
    assert main.index("<h2>워크플로우</h2>") < main.index("<h2>업무</h2>")  # 업무 구역 위에

    repo.update_task_status(conn, task_a, "완료", "판정 근거: 12/12", finished_at=NOW)
    repo.mark_chain_started(conn, chain_id, NOW)
    home = web.get("/tasks").text
    assert "1/2 완료" in home and "2단계 중 2단계 대기" in home
    # 왼쪽 목록에는 Task 그대로
    sidebar = home[home.index('class="sidebar'):home.index('class="main')]
    assert f'href="/tasks/{task_a}"' in sidebar and f'href="/tasks/{task_b}"' in sidebar
    assert "/chains/" not in sidebar


def test_task_detail_links_to_its_chain(web, conn):
    chain_id, (task_a, task_b) = import_chain(web, conn, "#41", "#42")
    text = detail(web, task_b)
    assert f'href="/chains/{chain_id}"' in text
    assert "워크플로우 일일 보고서 생성 실패 (09-20 09:00) → 집계 API 응답 형식 변경 대응" in text
    assert f'href="/chains/{chain_id}"' in web.get(f"/tasks/{task_b}/live").text
    assert "/chains/" not in detail(web, create_task(web, diagnose_form()))


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
    assert "agent-claude-mac" in web.get("/agents/register").text  # 카탈로그에 바로 보인다 (세션 등록 전)
    assert "agent-claude-mac" not in web.get("/agents").text  # 세션 목록은 등록한 것만

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
    other.get("/tasks")
    register_agents(other, "agent-ops-demo")  # 다른 세션도 카탈로그에서 등록해야 후보가 생긴다
    other_task = create_task(other, diagnose_form())
    assert other.post(f"/tasks/{other_task}/run", follow_redirects=False).status_code == 303

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
    for path in ("/tasks", "/operator", "/agents", "/tasks/new"):
        assert "test-operator-token" not in web.get(path).text


# --- 업무 종류·후속 규칙 (phase 6 step 6, ADR-0009) — 워크스페이스(세션)가 등록한다 ----------------


REVIEW_INSTRUCTIONS = (
    "인계 디렉터리의 diff 와 code_change_result 를 읽고 변경이 진단의 repair_request 를 충족하는지 검토하세요."
)


def kind_form(**overrides) -> dict:
    """CONTRACT 11.1 의 사용자 정의 `review`. output_kind·builtin 은 서버가 채우므로 폼에 없다."""
    form = {
        "kind": "review",
        "label": "검토",
        "capability_code": "review",
        "scope_key": "repository_id",
        "input_kinds": ["diff", "code_change_result"],
        "outcomes": "approved, changes_requested needs_information",
        "instructions": REVIEW_INSTRUCTIONS,
    }
    form.update(overrides)
    return form


def rule_form(**overrides) -> dict:
    """CONTRACT 11.2 의 `code_change --[ready_for_review]--> review`."""
    form = {
        "from_kind": "code_change",
        "on_outcomes": ["ready_for_review"],
        "to_kind": "review",
        "handoff_kinds": ["diff", "code_change_result"],
    }
    form.update(overrides)
    return form


def register_kind(client, **overrides) -> None:
    response = client.post("/kinds", data=kind_form(**overrides), follow_redirects=False)
    assert response.status_code == 303, response.text
    assert response.headers["location"] == "/kinds"


def register_rule(client, **overrides) -> None:
    response = client.post("/rules", data=rule_form(**overrides), follow_redirects=False)
    assert response.status_code == 303, response.text
    assert response.headers["location"] == "/kinds"


def kinds_page(client) -> str:
    """엔티티를 복원한 페이지 텍스트 — 규칙 한 줄의 `-->` 가 `--&gt;` 로 이스케이프되므로."""
    response = client.get("/kinds")
    assert response.status_code == 200, response.text
    return html_lib.unescape(response.text)


def alert_of(response) -> str:
    """오류 화면의 알림 블록만 (사이드바의 `업무` 같은 탐색 문구를 제외하고 본다)."""
    text = response.text
    return text[text.index('class="alert"'):text.index('class="actions"')]


def test_kinds_page_shows_builtin_kinds_and_rule_without_delete_button(web):
    text = kinds_page(web)
    assert "업무 종류" in text and "후속 규칙" in text and "받는 산출물" in text and "내는 산출물" in text
    assert "결과값" in text
    for value in ("진단", "코드 수정", "diagnosis", "code_change", "operations.diagnose", "code.modify",
                  "workflow_id", "repository_id", "ready_for_handoff", "ready_for_review", "needs_information"):
        assert value in text, value
    assert text.count(">내장<") == 2
    assert 'action="/kinds/diagnosis/delete"' not in text and 'action="/kinds/code_change/delete"' not in text
    # 내장 규칙 한 줄 텍스트 — 그래프·화살표 그림 없음, 삭제 가능
    assert "진단 --[ready_for_handoff]--> 코드 수정" in text
    assert text.count('action="/rules/') == 1 and "/delete" in text
    assert "규칙이 없으면 그 결과 뒤 후속은 사람이 시작합니다" in text
    assert 'action="/kinds"' in text and 'action="/rules"' in text
    assert 'name="input_kinds"' in text and 'value="handoff_bundle"' not in text
    assert 'data-outcomes="ready_for_handoff needs_information"' in text
    assert "<svg" not in text[text.index('class="main'):text.index('class="viewer')]


def test_register_kind_appears_on_page_and_is_isolated_per_session(app, web, conn, settings):
    register_kind(web)
    text = kinds_page(web)
    assert 'action="/kinds/review/delete"' in text
    for value in ("검토", "review", "repository_id", "approved", "changes_requested", "결과 봉투", "수정 결과",
                  REVIEW_INSTRUCTIONS):
        assert value in text, value
    spec = repo.get_kind(conn, session_id_of(web, settings), "review")
    assert spec is not None
    assert (spec.label, spec.capability_code, spec.scope_key) == ("검토", "review", "repository_id")
    assert spec.input_kinds == ["diff", "code_change_result"]
    assert spec.outcomes == ["approved", "changes_requested", "needs_information"]
    assert (spec.output_kind, spec.builtin) == ("generic_result", False)
    # 규칙 폼의 선행·후속 select 에도 나타난다
    assert 'value="review" data-outcomes="approved changes_requested needs_information"' in text

    other = TestClient(app)
    assert 'action="/kinds/review/delete"' not in other.get("/kinds").text
    assert [s.kind for s in repo.list_kinds(conn, session_id_of(other, settings))] == ["diagnosis", "code_change"]


def test_register_kind_defaults_capability_code_to_kind(web, conn, settings):
    register_kind(web, capability_code="")
    assert repo.get_kind(conn, session_id_of(web, settings), "review").capability_code == "review"


@pytest.mark.parametrize(
    ("overrides", "field"),
    [
        ({"kind": "Review"}, "kind"),
        ({"kind": "r"}, "kind"),
        ({"label": ""}, "label"),
        ({"capability_code": "Code.Modify"}, "capability_code"),
        ({"scope_key": "Repo-ID"}, "scope_key"),
        ({"outcomes": ""}, "outcomes"),
        ({"outcomes": "Approved"}, "outcomes"),
        ({"input_kinds": ["nope"]}, "input_kinds"),
    ],
)
def test_register_kind_rejects_bad_form_with_422(web, overrides, field):
    response = web.post("/kinds", data=kind_form(**overrides), follow_redirects=False)
    assert response.status_code == 422, response.text
    assert "invalid_field" in response.text and f"<code>{field}</code>" in response.text


def test_register_kind_duplicate_outcomes_422_with_model_message(web):
    response = web.post("/kinds", data=kind_form(outcomes="approved approved"), follow_redirects=False)
    assert response.status_code == 422
    assert "outcomes 에 중복이 있습니다" in response.text and "Value error" not in response.text


def test_register_kind_duplicate_and_builtin_name_409(web):
    register_kind(web)
    again = web.post("/kinds", data=kind_form(), follow_redirects=False)
    assert again.status_code == 409 and "kind_exists" in again.text
    builtin = web.post("/kinds", data=kind_form(kind="diagnosis", input_kinds=[]), follow_redirects=False)
    assert builtin.status_code == 409 and "kind_exists" in builtin.text


def test_register_rule_appears_as_one_line(web, conn, settings):
    register_kind(web)
    register_rule(web)
    text = kinds_page(web)
    assert "코드 수정 --[ready_for_review]--> 검토" in text
    assert text.count('action="/rules/') == 2
    rules = repo.list_rules(conn, session_id_of(web, settings))
    assert [(r.from_kind, r.to_kind) for _, r in rules] == [("diagnosis", "code_change"), ("code_change", "review")]
    assert rules[1][1].on_outcomes == ["ready_for_review"]
    assert rules[1][1].handoff_kinds == ["diff", "code_change_result"]


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"on_outcomes": ["approved"]}, "on_outcomes 에 code_change 의 outcome 이 아닌 값이 있습니다: approved"),
        ({"handoff_kinds": ["diff"]}, "handoff_kinds 에 review 의 input_kinds 가 빠졌습니다: code_change_result"),
        ({"to_kind": "nope"}, "등록되지 않은 종류 nope"),
        ({"from_kind": "nope"}, "등록되지 않은 종류 nope"),
        ({"on_outcomes": []}, "on_outcomes"),
        ({"from_kind": "review", "on_outcomes": ["approved"]}, "from_kind 와 to_kind 가 같습니다"),
        ({"handoff_kinds": ["diff", "code_change_result", "handoff_bundle"]}, "handoff_bundle"),
    ],
)
def test_register_rule_rejects_invalid_422(web, conn, settings, overrides, message):
    register_kind(web)
    response = web.post("/rules", data=rule_form(**overrides), follow_redirects=False)
    assert response.status_code == 422, response.text
    assert "invalid_field" in response.text and message in response.text
    assert len(repo.list_rules(conn, session_id_of(web, settings))) == 1


def test_register_rule_duplicate_409(web):
    register_kind(web)
    register_rule(web)
    again = web.post("/rules", data=rule_form(), follow_redirects=False)
    assert again.status_code == 409 and "rule_exists" in again.text
    builtin = web.post(
        "/rules",
        data=rule_form(from_kind="diagnosis", on_outcomes=["ready_for_handoff"], to_kind="code_change",
                       handoff_kinds=["diagnosis_result", "evidence"]),
        follow_redirects=False,
    )
    assert builtin.status_code == 409 and "rule_exists" in builtin.text


def test_delete_kind_protected_in_use_then_success(web, conn, settings):
    session_id = session_id_of(web, settings)
    protected = web.post("/kinds/diagnosis/delete", follow_redirects=False)
    assert protected.status_code == 409
    assert "kind_protected" in protected.text and "내장 종류는 삭제할 수 없습니다" in protected.text
    assert repo.get_kind(conn, session_id, "diagnosis") is not None

    # Task 가 쓰는 종류
    register_kind(web)
    row = {
        "task_id": "review-daily-0920", "session_id": session_id, "title": "수정 검토", "request": "검토해 주세요.",
        "kind": "review", "required_capability": {"code": "review", "scope": {"repository_id": "demo-report-repo"}},
        "selection_mode": "auto", "chosen_agent_id": None, "run_mode": "manual", "completion_mode": "review",
        "criteria": [], "predecessor_task_id": None, "revision": 1,
        "target": {"local_registration_id": "local-demo-report"}, "status": "확인 필요", "status_reason": "후보 없음",
    }
    repo.insert_task(conn, row, NOW)
    by_task = web.post("/kinds/review/delete", follow_redirects=False)
    assert by_task.status_code == 409
    alert = alert_of(by_task)
    assert "kind_in_use" in alert and "업무" in alert and "후속 규칙" not in alert

    # 규칙이 참조하는 종류 → 규칙 삭제 뒤 종류 삭제 성공
    register_kind(web, kind="audit", label="감사", capability_code="", input_kinds=[], outcomes="ok, not_ok")
    register_rule(web, to_kind="audit")
    by_rule = web.post("/kinds/audit/delete", follow_redirects=False)
    assert by_rule.status_code == 409
    alert = alert_of(by_rule)
    assert "kind_in_use" in alert and "후속 규칙" in alert and "업무" not in alert
    rule_id = next(rid for rid, r in repo.list_rules(conn, session_id) if r.to_kind == "audit")
    assert web.post(f"/rules/{rule_id}/delete", follow_redirects=False).status_code == 303
    deleted = web.post("/kinds/audit/delete", follow_redirects=False)
    assert deleted.status_code == 303 and deleted.headers["location"] == "/kinds"
    assert repo.get_kind(conn, session_id, "audit") is None
    assert 'action="/kinds/audit/delete"' not in kinds_page(web)
    assert web.post("/kinds/audit/delete", follow_redirects=False).status_code == 404


def test_delete_rule_then_404(web, conn, settings):
    session_id = session_id_of(web, settings)
    (rule_id, _), = repo.list_rules(conn, session_id)
    response = web.post(f"/rules/{rule_id}/delete", follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == "/kinds"
    assert repo.list_rules(conn, session_id) == []
    assert "진단 --[ready_for_handoff]--> 코드 수정" not in kinds_page(web)
    assert web.post(f"/rules/{rule_id}/delete", follow_redirects=False).status_code == 404
    assert web.post("/rules/rule-none/delete", follow_redirects=False).status_code == 404


def test_other_session_cannot_delete_my_kind_or_rule(app, web, conn, settings):
    register_kind(web)
    session_id = session_id_of(web, settings)
    (rule_id, _), = repo.list_rules(conn, session_id)
    other = TestClient(app)
    other.get("/tasks")
    assert other.post("/kinds/review/delete", follow_redirects=False).status_code == 404
    assert other.post(f"/rules/{rule_id}/delete", follow_redirects=False).status_code == 404
    assert repo.get_kind(conn, session_id, "review") is not None
    assert len(repo.list_rules(conn, session_id)) == 1
