"""web.py — 워크스페이스·운영자 웹 라우트. 마크업이 아니라 렌더된 텍스트·리다이렉트·상태 코드를 본다 (Step 7 이 화면을 꾸민다).

기본은 셀프호스트(ADR-0019) — 로그인한 고정 워크스페이스, 러너 모양 Agent(`seed_agents`), 종류 `bug_fix`·`code_review`.
이름 끝이 `_demo` 인 fixture·도우미와 그것을 쓰는 테스트는 demo 모드(익명 세션·카탈로그·가져오기·진단 → 코드 수정)
전제이며 phase 13 step 2·3 에서 함께 지운다."""

import dataclasses
import html as html_lib
import json
import re

import pytest
from fastapi.testclient import TestClient

from workflow.adapters import repo
from workflow.contracts.v1 import BUILTIN_KINDS, ArtifactMeta, ExecutionRequest
from workflow.server.app import create_app
from workflow.server.auth import (
    LOGIN_MAX_FAILURES,
    SELFHOST_SESSION_ID,
    SESSION_COOKIE,
    ensure_workspace,
    verify_session,
)
from workflow.server.web import EXAMPLES

from .conftest import (
    BASE_COMMIT,
    LOCAL_REGISTRATION,
    NOW,
    REPOSITORY,
    RESULT_COMMIT,
    SESSION,
    code_change_result,
    log_in,
    meta_for,
    seed_agents,
    seed_agents_demo,
    seed_execution,
    seed_result_ready,
)

DIAGNOSE_REQUEST = (
    "일일 보고서 생성 실패를 조사하고, 로컬 개발 에이전트가 재현·수정할 수 있도록 근거와 기대 동작을 정리해 주세요."
)
FIX_REQUEST = (
    "인계된 진단 근거로 보고서 변환 실패를 재현하는 테스트를 먼저 작성하고, "
    "실패를 확인한 뒤 두 응답 형식을 모두 처리하도록 최소 수정하세요."
)
BUG_FIX_TITLE = "보고서 변환 실패 수정"
BUG_FIX_REQUEST = "보고서 변환 실패를 재현하는 테스트를 먼저 작성하고 두 응답 형식을 모두 처리하도록 고치세요."
CODE_REVIEW_TITLE = "보고서 변환 수정 검토"
CODE_REVIEW_REQUEST = "수정 결과 커밋을 검토해 주세요."


@pytest.fixture
def agents(conn):
    """고정 워크스페이스 + 러너 모양 Agent 1개(agent-codex-mac, `code.fix`·`code.review`)."""
    ensure_workspace(conn, NOW)
    seed_agents(conn)
    return conn


@pytest.fixture
def web(logged_in_client, agents):
    """워크스페이스에 로그인했고 Agent 가 붙어 있는 클라이언트."""
    return logged_in_client


def session_id_of(client: TestClient, settings) -> str:
    return verify_session(client.cookies[SESSION_COOKIE], settings.session_secret)


def fix_form(**overrides) -> dict:
    """직접 등록하는 `bug_fix` (선행 없음, 직접 실행, 검토 후 완료)."""
    form = {
        "title": BUG_FIX_TITLE,
        "request": BUG_FIX_REQUEST,
        "capability_code": "code.fix",
        "scope_value": REPOSITORY,
        "selection_mode": "auto",
        "chosen_agent_id": "",
        "run_mode": "manual",
        "completion_mode": "review",
        "criteria_extra": "",
        "predecessor_task_id": "",
        "run_id": "",
    }
    form.update(overrides)
    return form


def code_review_form(predecessor: str, **overrides) -> dict:
    """`bug_fix` 뒤에 붙는 `code_review` (선행 완료 시 자동)."""
    form = fix_form(
        title=CODE_REVIEW_TITLE, request=CODE_REVIEW_REQUEST, capability_code="code.review",
        run_mode="auto", predecessor_task_id=predecessor,
    )
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


# --- demo 도우미 (step 2·3 에서 삭제) --------------------------------------------------------


@pytest.fixture
def agents_demo(conn):
    seed_agents_demo(conn)
    return conn


def register_agents_demo(client, *agent_ids: str) -> None:
    """세션이 카탈로그에서 Agent 를 등록한다 (`POST /agents/register`). 기본은 운영 진단·개인 Codex 둘 다."""
    for agent_id in agent_ids or ("agent-ops-demo", "agent-codex-mac"):
        response = client.post("/agents/register", data={"agent_id": agent_id}, follow_redirects=False)
        assert response.status_code == 303, response.text


@pytest.fixture
def web_demo(client_demo, agents_demo):
    """홈을 한 번 열어 세션 쿠키를 받고, 카탈로그 2개를 등록한 클라이언트."""
    assert client_demo.get("/tasks").status_code == 200
    register_agents_demo(client_demo)
    return client_demo


def diagnose_form_demo(**overrides) -> dict:
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


def fix_form_demo(predecessor: str, **overrides) -> dict:
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


def seed_reviewable_fix_demo(client, conn, store, settings) -> tuple[str, str]:
    """A → B 등록 뒤 B 에 result_ready 실행과 CONTRACT 7절 결과를 넣는다. (task_id, execution_id)."""
    task_a = create_task(client, diagnose_form_demo())
    task_b = create_task(client, fix_form_demo(task_a))
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


def test_home_issues_session_cookie_once_and_shows_empty_state(client_demo, agents_demo):
    """새 세션 홈: 등록 Agent 0개 → 안내 문장과 `에이전트 등록` 링크, `업무 가져오기` 는 비활성."""
    client = client_demo
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


def test_home_after_registration_shows_cards_and_enables_import_button(web_demo):
    text = web_demo.get("/tasks").text
    assert "운영 진단 데모" in text and "개인 Codex" in text
    assert "먼저 에이전트를 등록하세요." not in text
    assert 'class="btn" href="/tasks/import"' in text and "에이전트를 먼저 등록하세요" not in text
    assert 'href="/tasks/new"' in text  # 직접 등록은 보조 버튼
    assert "시연 업무 만들기" not in text
    assert 'href="/agents/register"' in text  # 더 등록할 수 있다


def test_landing_is_public_and_links_to_app(client_demo, agents_demo):
    client = client_demo
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


def test_app_home_has_agent_and_task_sections_with_direct_register(web):
    text = web.get("/tasks").text
    assert 'href="/tasks/new"' in text  # 시연 예시 없이 직접 등록
    assert 'href="/agents"' in text
    assert 'href="/"' in text  # 사이드바 브랜드 → `/` (로그인 상태면 /tasks)
    assert '/static/logo.jpg' not in text  # 로고는 랜딩에만


def test_home_lists_my_tasks_with_status(web):
    task_id = create_task(web, fix_form())
    text = web.get("/tasks").text
    assert "아직 업무가 없습니다." not in text
    assert f"/tasks/{task_id}" in text
    assert BUG_FIX_TITLE in text
    assert "대기" in text and "연결 끊김, 마지막 확인 없음" in text  # conftest 의 Codex 는 아직 보고 전


# --- 등록 폼 ---------------------------------------------------------------------


def test_new_task_form_prefills_diagnose_example(web_demo):
    text = web_demo.get("/tasks/new?example=diagnose").text
    assert DIAGNOSE_REQUEST in text
    assert "일일 보고서 실패 진단" in text
    assert "결과가 ready_for_handoff임" in text
    assert "근거 검증을 통과함" in text
    assert "자동 판정기가 있는 진단 업무라 자동 완료로 미리 채움" in text
    assert "daily-0920-0900" in text


def test_diagnose_example_form_offers_successor_checked(web_demo):
    """시연 폼은 후속 B 동시 등록 체크박스를 기본 체크로 보여 준다. 빈 폼·fix 폼에는 없다."""
    web = web_demo
    text = web.get("/tasks/new?example=diagnose").text
    assert 'name="with_successor"' in text and "checked" in text.split('name="with_successor"')[1][:80]
    assert "A 완료 시 자동 착수" in text
    assert 'name="with_successor"' not in web.get("/tasks/new").text
    task_a = create_task(web, diagnose_form_demo())
    assert 'name="with_successor"' not in web.get(f"/tasks/new?example=fix&predecessor={task_a}").text


def test_diagnose_example_form_offers_successor_only_with_builtin_rule(web_demo, conn, settings):
    """세션이 규칙 diagnosis → code_change 를 지웠으면 체크박스를 보이지 않고, 보내도 B 를 만들지 않는다."""
    web = web_demo
    session_id = session_id_of(web, settings)
    (rule_id, _), = [(rid, r) for rid, r in repo.list_rules(conn, session_id) if r.from_kind == "diagnosis"]
    repo.delete_rule(conn, session_id, rule_id)
    assert 'name="with_successor"' not in web.get("/tasks/new?example=diagnose").text
    task_a = create_task(web, diagnose_form_demo(with_successor="1"))
    assert [t["task_id"] for t in repo.list_tasks(conn, session_id)] == [task_a]


def test_create_diagnose_with_successor_registers_b_waiting_on_a(web_demo, conn):
    """with_successor 면 A 와 fix 예시 B 가 한 번에 만들어지고 B 는 A 를 선행으로 자동 실행 대기한다."""
    web = web_demo
    task_a = create_task(web, diagnose_form_demo(with_successor="1"))
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
    task_c = create_task(web, diagnose_form_demo())
    assert len(repo.list_tasks(conn, repo.get_task(conn, task_c)["session_id"])) == 3


def test_create_with_successor_counts_both_against_active_limit(app_demo, web_demo, conn):
    web = web_demo
    settings = app_demo.state.settings
    for _ in range(settings.limits.active_tasks_per_session - 1):
        create_task(web, diagnose_form_demo())
    response = web.post("/tasks", data=diagnose_form_demo(with_successor="1"), follow_redirects=False)
    assert response.status_code == 429
    assert len(repo.list_tasks(conn, repo.get_task(conn, create_task(web, diagnose_form_demo()))["session_id"])) == settings.limits.active_tasks_per_session


def test_new_task_form_prefills_fix_example_with_predecessor(web_demo):
    web = web_demo
    task_a = create_task(web, diagnose_form_demo())
    text = web.get(f"/tasks/new?example=fix&predecessor={task_a}").text
    assert FIX_REQUEST in text
    assert "보고서 변환기 수정" in text
    assert "등록된 검증 프로필이 결과 커밋에서 통과함" in text
    assert f'value="{task_a}" selected' in text


def test_new_task_form_rejects_predecessor_of_other_session(app_demo, web_demo, agents_demo):
    task_a = create_task(web_demo, diagnose_form_demo())
    other = TestClient(app_demo)
    assert other.get(f"/tasks/new?example=fix&predecessor={task_a}").status_code == 404


def test_new_task_form_without_example_is_blank(web):
    text = web.get("/tasks/new").text
    assert DIAGNOSE_REQUEST not in text
    assert "자동 판정기가 있는 진단 업무라" not in text


# --- 등록 ------------------------------------------------------------------------


def test_create_fix_task_selects_agent_and_waits_for_connection(web, conn):
    """직접 등록한 `bug_fix` — 자동 선택으로 러너 Agent 를 고르고 대상은 그 등록값. 러너가 보고 전이라 `대기`."""
    task_id = create_task(web, fix_form())
    text = detail(web, task_id)
    assert "대기" in text and "연결 끊김, 마지막 확인 없음" in text
    assert "버그 수정" in text and "검토 후 완료" in text and "재현 테스트가 수정 전에 실패함" in text
    assert f'action="/tasks/{task_id}/run"' not in text

    row = repo.get_task(conn, task_id)
    assert row["kind"] == "bug_fix"
    assert (row["status"], row["status_reason"]) == ("대기", "연결 끊김, 마지막 확인 없음")
    assert row["completion_mode"] == "review" and row["run_mode"] == "manual"
    assert json.loads(row["target_json"]) == {"local_registration_id": LOCAL_REGISTRATION}
    selection = repo.get_selection(conn, task_id)
    assert selection.selected_agent_id == "agent-codex-mac" and selection.mode == "auto"
    assert selection.reason == "code.fix · repository_id=demo-report-repo 일치 후보 1개"


def test_create_review_task_waits_for_predecessor(web, conn):
    task_a = create_task(web, fix_form())
    task_b = create_task(web, code_review_form(task_a, criteria_extra="두 경로 동시 존재 테스트 포함\n\n"))
    text = detail(web, task_b)
    assert "대기" in text and "선행 대기" in text
    assert BUG_FIX_TITLE in text  # 선행 링크
    assert "두 경로 동시 존재 테스트 포함" in text
    assert f'action="/tasks/{task_b}/run"' not in text

    row = repo.get_task(conn, task_b)
    assert row["kind"] == "code_review" and row["predecessor_task_id"] == task_a
    assert row["run_mode"] == "auto" and row["completion_mode"] == "review"
    assert (row["status"], row["status_reason"]) == ("대기", "선행 대기")
    criteria = repo.get_task(conn, task_b)["criteria_json"]
    assert '"user.1"' in criteria and '"structured": false' in criteria
    assert CODE_REVIEW_TITLE in detail(web, task_a)  # 후속 링크


def test_create_task_needs_selection_when_two_candidates_then_manual_select(web, conn):
    seed_agents(conn, with_claude=True)  # 같은 저장소를 맡는 Claude Code 까지 2개
    task_id = create_task(web, fix_form())
    text = detail(web, task_id)
    assert "확인 필요" in text and "후보 2개 — 선택 필요" in text
    assert repo.get_selection(conn, task_id).status == "needs_selection"

    response = web.post(f"/tasks/{task_id}/select", data={"agent_id": "agent-codex-mac"}, follow_redirects=False)
    assert response.status_code == 303
    row = repo.get_task(conn, task_id)
    assert row["selection_mode"] == "manual" and row["chosen_agent_id"] == "agent-codex-mac"
    assert json.loads(row["target_json"]) == {"local_registration_id": LOCAL_REGISTRATION}
    selection = repo.get_selection(conn, task_id)
    assert selection.mode == "manual" and selection.selected_agent_id == "agent-codex-mac"
    assert "확인 필요" not in repo.get_task(conn, task_id)["status"]

    again = web.post(f"/tasks/{task_id}/select", data={"agent_id": "agent-claude-mac"}, follow_redirects=False)
    assert again.status_code == 409


def test_create_task_manual_selection_records_reason(web, conn):
    repo.upsert_agent(conn, {
        "agent_id": "agent-review-only", "name": "검토 전용", "owner_scope": "personal", "connection_type": "local",
        "local_registration_id": "local-review-only",
        "capabilities": [{"code": "code.review", "scope": {"repository_id": REPOSITORY}}],
    })
    repo.register_session_agent(conn, SESSION, "agent-review-only", NOW)
    task_id = create_task(web, fix_form(selection_mode="manual", chosen_agent_id="agent-review-only"))
    text = detail(web, task_id)
    assert "확인 필요" in text
    assert "선택한 에이전트에 code.fix 능력 없음" in text


@pytest.mark.parametrize(
    "overrides",
    [
        {"title": " "},
        {"request": ""},
        {"capability_code": "ops.unknown"},
        {"scope_value": ""},
        {"run_mode": "sometimes"},
        {"selection_mode": "manual", "chosen_agent_id": ""},
        {"completion_mode": "sometimes"},
    ],
)
def test_create_task_rejects_bad_form_with_422(web, overrides):
    response = web.post("/tasks", data=fix_form(**overrides), follow_redirects=False)
    assert response.status_code == 422, overrides
    if "capability_code" in overrides:
        assert "등록되지 않은 업무 종류입니다." in response.text


def test_create_code_change_task_cannot_be_auto_completed(web):
    for form in (fix_form(completion_mode="auto"), code_review_form(create_task(web, fix_form()), completion_mode="auto")):
        response = web.post("/tasks", data=form, follow_redirects=False)
        assert response.status_code == 422
        assert "자동 완료" in response.text


def test_create_task_active_limit_429(web):
    for _ in range(5):
        create_task(web, fix_form())
    response = web.post("/tasks", data=fix_form(), follow_redirects=False)
    assert response.status_code == 429
    assert "세션당 활성 업무 한도(5개)에 도달했습니다." in response.text


def test_other_session_cannot_see_task(app_demo, web_demo, agents_demo):
    web = web_demo
    task_id = create_task(web, diagnose_form_demo())
    other = TestClient(app_demo)
    assert other.get(f"/tasks/{task_id}").status_code == 404
    assert other.post(f"/tasks/{task_id}/run", follow_redirects=False).status_code == 404
    assert other.post(f"/tasks/{task_id}/review", data={"decision": "approve"}, follow_redirects=False).status_code == 404
    assert web.get("/tasks/nope").status_code == 404


# --- 직접 실행 -------------------------------------------------------------------


def test_run_creates_queued_execution_with_frozen_request(web_demo, conn, settings):
    web = web_demo
    task_id = create_task(web, diagnose_form_demo())
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
        "kind_spec": BUILTIN_KINDS[0].model_dump(),  # 서버가 등록부에서 채운다 (ADR-0009)
    }
    since = "2026-01-01T00:00:00Z"
    assert repo.count_diagnosis_started(conn, session_id=session_id_of(web, settings), since=since) == 1
    assert repo.get_task(conn, task_id)["status"] == "실행 요청됨"

    again = web.post(f"/tasks/{task_id}/run", follow_redirects=False)
    assert again.status_code == 409
    assert len(repo.list_executions(conn, task_id)) == 1


def test_run_requires_selection_and_finished_predecessor(web, conn):
    task_a = create_task(web, fix_form())
    task_b = create_task(web, code_review_form(task_a, run_mode="manual"))
    blocked = web.post(f"/tasks/{task_b}/run", follow_redirects=False)
    assert blocked.status_code == 409
    assert "지금 시작할 수 없습니다" in html_lib.unescape(blocked.text)
    assert repo.get_task(conn, task_b)["status_reason"] == "선행 대기"
    assert repo.active_execution(conn, task_b) is None

    seed_agents(conn, with_claude=True)
    unselected = create_task(web, fix_form())
    assert repo.get_selection(conn, unselected).status == "needs_selection"
    assert web.post(f"/tasks/{unselected}/run", follow_redirects=False).status_code == 409
    assert repo.active_execution(conn, unselected) is None


def test_run_diagnosis_session_daily_limit_429(web_demo, conn, settings):
    web = web_demo
    task_id = create_task(web, diagnose_form_demo())
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


def test_run_diagnosis_global_daily_limit_429(web_demo, conn):
    web = web_demo
    task_id = create_task(web, diagnose_form_demo())
    from workflow.server.auth import utc_now
    for n in range(60):
        repo.record_diagnosis_start(conn, f"sess-other-{n}", f"exec-seed-{n}", utc_now())
    response = web.post(f"/tasks/{task_id}/run", follow_redirects=False)
    assert response.status_code == 429
    assert "오늘 전체 진단 실행 한도(60회)에 도달했습니다." in response.text


def test_run_diagnosis_rejected_when_diagnosis_is_off(agents_demo, settings_demo, conn):
    """phase 10 — 진단 토큰이 비면(셀프호스트 선택) 진단 실행을 만들지 않고 명확히 거부한다. 분기는 모드가 아니라 토큰."""
    off = TestClient(create_app(dataclasses.replace(settings_demo, diag_api_token="")))
    assert off.get("/tasks").status_code == 200
    register_agents_demo(off)
    task_id = create_task(off, diagnose_form_demo())
    response = off.post(f"/tasks/{task_id}/run", follow_redirects=False)
    assert response.status_code == 409
    assert "diagnosis_disabled" in response.text
    assert "진단 기능이 꺼져 있습니다" in response.text
    assert repo.active_execution(conn, task_id) is None
    assert repo.count_diagnosis_started(conn, session_id=None, since="2000-01-01T00:00:00Z") == 0


def seed_judged_predecessor_demo(client, conn, store, settings, task_a: str, *, bundle: bool = True) -> tuple[str, str | None]:
    """A 를 실행해 결과(result_ready)와 판정 `passed` 를 넣는다 — 사람 승인 전 `확인 필요` 상태. `bundle` 이면 워커가
    조립했을 handoff_bundle 산출물도 넣는다. (exec_a, bundle_id)."""
    client.post(f"/tasks/{task_a}/run", follow_redirects=False)
    exec_a = repo.active_execution(conn, task_a)["execution_id"]
    session_id = session_id_of(client, settings)
    seed_result_ready(
        conn, store, exec_a, kind="diagnosis_result",
        body={"outcome": "ready_for_handoff", "summary": "응답 경로 변경"}, session_id=session_id,
    )
    repo.record_verdict(
        conn, task_id=task_a, execution_id=exec_a,
        verdict={"outcome": "passed", "checks": [{"code": "response_path_changed", "passed": True, "detail": "확인"}]},
        status="확인 필요", reason="검토 대기", finish=False, now=NOW,
    )
    if not bundle:
        return exec_a, None
    data = json.dumps({
        "contract_version": 1, "source_execution_id": exec_a, "source_kind": "diagnosis",
        "source_result_artifact_id": "art-diag-result-001", "inputs": [], "attachments": [],
    }).encode()
    created, _ = repo.store_artifact(
        conn, store, execution_id=exec_a, session_id=session_id,
        meta=ArtifactMeta.model_validate(meta_for(data, kind="handoff_bundle", name="manifest.json",
                                                   content_type="application/json")),
        data=data, now=NOW,
    )
    return exec_a, created.artifact_id


def test_run_code_change_uses_predecessor_handoff_bundle(web_demo, conn, store, settings):
    web = web_demo
    """선행 A 의 결과가 판정되고(사람 승인 전) handoff_bundle 산출물이 있으면, B 직접 실행 요청의 입력에 그 ID 가 고정된다
    (ADR-0009 (3) — 선행 `완료` 를 기다리지 않는다)."""
    task_a = create_task(web, diagnose_form_demo())
    exec_a, bundle_id = seed_judged_predecessor_demo(web, conn, store, settings, task_a)
    assert repo.get_task(conn, task_a)["finished_at"] is None
    # 온라인 판정이 서버 시각 기준 heartbeat_offline_seconds 이내인지 보므로 last_seen 은 실제 시각으로 둔다
    from workflow.server.auth import utc_now
    repo.update_registration(
        conn, "local-demo-report", connector_id="conn-mac-01", repository_id="demo-report-repo",
        base_commit=BASE_COMMIT, verification_profile_ids=["vp-pytest"], discovered={}, now=utc_now(),
    )
    task_b = create_task(web, fix_form_demo(task_a, run_mode="manual"))
    text = detail(web, task_b)
    assert "실행 가능" in text and "선행 대기" not in text and f'action="/tasks/{task_b}/run"' in text

    response = web.post(f"/tasks/{task_b}/run", follow_redirects=False)
    assert response.status_code == 303, response.text
    execution = repo.active_execution(conn, task_b)
    assert execution["assigned_connector_id"] == "conn-mac-01"
    assert execution["predecessor_execution_id"] == exec_a
    request = ExecutionRequest.model_validate_json(execution["request_json"])
    assert request.input_artifact_ids == [bundle_id]
    assert request.kind_spec == BUILTIN_KINDS[1]
    assert request.target.model_dump() == {
        "local_registration_id": "local-demo-report",
        "base_commit": BASE_COMMIT,
        "verification_profile_id": "vp-pytest",
    }
    assert repo.get_task(conn, task_a)["status"] == "확인 필요"  # A 는 여전히 사람 검토 전


def test_run_code_change_without_registration_or_handoff_409(web_demo, conn):
    web = web_demo
    task_a = create_task(web, diagnose_form_demo())
    repo.update_task_status(conn, task_a, "완료", "판정 근거: 12/12", finished_at=NOW, now=NOW)
    task_b = create_task(web, fix_form_demo(task_a, run_mode="manual"))
    response = web.post(f"/tasks/{task_b}/run", follow_redirects=False)
    assert response.status_code == 409
    assert "인계 자료" in response.text or "등록 정보" in response.text


def test_run_successor_waits_for_bundle_and_says_so_without_rule(web_demo, conn, store, settings):
    web = web_demo
    """선행 결과가 판정됐어도 인계 묶음이 없으면 409. 규칙이 없으면 /kinds 로 안내한다."""
    task_a = create_task(web, diagnose_form_demo())
    seed_judged_predecessor_demo(web, conn, store, settings, task_a, bundle=False)
    task_b = create_task(web, fix_form_demo(task_a, run_mode="manual"))
    text = detail(web, task_b)
    assert "선행 대기" in text and f'action="/tasks/{task_b}/run"' not in text
    waiting = web.post(f"/tasks/{task_b}/run", follow_redirects=False)
    assert waiting.status_code == 409 and "인계 자료" in alert_of(waiting) and "/kinds" not in alert_of(waiting)

    session_id = session_id_of(web, settings)
    (rule_id, _), = [(rid, r) for rid, r in repo.list_rules(conn, session_id) if r.from_kind == "diagnosis"]
    repo.delete_rule(conn, session_id, rule_id)
    without_rule = web.post(f"/tasks/{task_b}/run", follow_redirects=False)
    assert without_rule.status_code == 409
    assert "후속 규칙이 없어 인계 자료가 없습니다. /kinds 에서 규칙을 등록하세요." in html_lib.unescape(without_rule.text)
    assert repo.active_execution(conn, task_b) is None


# --- 검토 ------------------------------------------------------------------------


def test_review_approve_completes_and_marks_merge_pending(web_demo, conn, store, settings):
    web = web_demo
    task_b, execution_id = seed_reviewable_fix_demo(web, conn, store, settings)
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


def test_review_request_changes_creates_second_attempt(web_demo, conn, store, settings):
    web = web_demo
    task_b, execution_id = seed_reviewable_fix_demo(web, conn, store, settings)
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


def test_review_close_fails_task(web_demo, conn, store, settings):
    web = web_demo
    task_b, execution_id = seed_reviewable_fix_demo(web, conn, store, settings)
    response = web.post(f"/tasks/{task_b}/review", data={"decision": "close", "comment": "중단"}, follow_redirects=False)
    assert response.status_code == 303
    text = detail(web, task_b)
    assert "실패" in text and "검토 거절" in text
    row = repo.get_task(conn, task_b)
    assert (row["status"], row["status_reason"], row["review_decision"]) == ("실패", "검토 거절", "close")
    assert repo.get_execution(conn, execution_id)["released_at"] is not None


def test_review_rejects_when_nothing_to_review(web_demo, conn, store, settings):
    web = web_demo
    task_id = create_task(web, diagnose_form_demo())
    assert web.post(f"/tasks/{task_id}/review", data={"decision": "approve"}, follow_redirects=False).status_code == 409
    task_b, _ = seed_reviewable_fix_demo(web, conn, store, settings)
    assert web.post(f"/tasks/{task_b}/review", data={"decision": "maybe"}, follow_redirects=False).status_code == 422


# --- 산출물 ----------------------------------------------------------------------


def test_artifact_page_and_raw_download_are_session_scoped(client, web, seeded, store):
    seed_execution(seeded, "exec-fix-001", "fix-daily-0920")
    seed_result_ready(seeded, store, "exec-fix-001", kind="code_change_result",
                      body=code_change_result("exec-fix-001", "fix-daily-0920"))
    [artifact] = repo.artifacts_of(seeded, "exec-fix-001")
    page = web.get(f"/tasks/fix-daily-0920/artifacts/{artifact['artifact_id']}")
    assert page.status_code == 200
    assert "code_change_result" in page.text and RESULT_COMMIT in page.text
    assert "<pre" in page.text

    raw = web.get(f"/tasks/fix-daily-0920/artifacts/{artifact['artifact_id']}?raw=1")
    assert raw.status_code == 200
    assert raw.headers["content-type"].startswith("application/json")
    assert raw.content == repo.read_artifact(seeded, store, artifact["artifact_id"])

    web.post("/logout", follow_redirects=False)  # 로그인하지 않은 브라우저는 로그인 화면으로
    for path in (f"/tasks/fix-daily-0920/artifacts/{artifact['artifact_id']}",
                 f"/tasks/fix-daily-0920/artifacts/{artifact['artifact_id']}?raw=1"):
        response = client.get(path, follow_redirects=False)
        assert response.status_code == 303 and response.headers["location"] == "/login", path
    log_in(web)
    assert web.get("/tasks/fix-daily-0920/artifacts/art-none").status_code == 404
    assert web.get(f"/tasks/review-daily-0920/artifacts/{artifact['artifact_id']}").status_code == 404  # 다른 업무의 산출물


# --- 에이전트 (읽기 전용) ----------------------------------------------------------


def test_agents_pages_hide_credentials(web_demo, conn):
    web = web_demo
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


def test_register_page_lists_catalog_with_discovered_summary_and_no_secrets(client_demo, conn, agents_demo):
    client = client_demo
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


def test_register_agent_appears_on_home_and_list_and_is_idempotent(client_demo, conn, agents_demo, settings):
    client = client_demo
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


def test_agent_detail_opens_for_catalog_and_registered_only(client_demo, conn, agents_demo):
    client = client_demo
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


def test_manual_choice_of_unregistered_agent_is_422(web, conn):
    repo.upsert_agent(conn, {  # 워크스페이스(session_agents)에 붙지 않은 Agent
        "agent_id": "agent-private", "name": "비공개", "owner_scope": "personal", "connection_type": "local",
        "local_registration_id": "local-private",
        "capabilities": [{"code": "code.fix", "scope": {"repository_id": REPOSITORY}}],
    })
    response = web.post(
        "/tasks", data=fix_form(selection_mode="manual", chosen_agent_id="agent-private"),
        follow_redirects=False,
    )
    assert response.status_code == 422
    assert "agent_not_registered" in response.text and "등록하지 않은 에이전트입니다" in response.text
    # 등록 폼의 직접 선택 목록에도 워크스페이스에 붙은 것만
    form = web.get("/tasks/new").text
    assert 'value="agent-codex-mac"' in form and 'value="agent-private"' not in form


def test_auto_selection_counts_only_registered_agents(web, conn):
    """워크스페이스에 붙지 않은 Agent 는 능력이 맞아도 후보가 아니다 — `확인 필요 · 후보 없음`. 직접 선택도 붙은 것만."""
    repo.upsert_agent(conn, {
        "agent_id": "agent-other", "name": "다른 저장소 Codex", "owner_scope": "personal", "connection_type": "local",
        "local_registration_id": "local-other",
        "capabilities": [{"code": "code.fix", "scope": {"repository_id": "other-repo"}}],
    })
    task_id = create_task(web, fix_form(scope_value="other-repo"))
    text = detail(web, task_id)
    assert "확인 필요" in text and "후보 없음" in text
    assert 'value="agent-other"' not in text
    blocked = web.post(f"/tasks/{task_id}/select", data={"agent_id": "agent-other"}, follow_redirects=False)
    assert blocked.status_code == 422 and "agent_not_registered" in blocked.text
    assert repo.get_selection(conn, task_id).status == "needs_selection"

    repo.register_session_agent(conn, SESSION, "agent-other", NOW)  # 러너 등록이 워크스페이스에 붙인다
    chosen = web.post(f"/tasks/{task_id}/select", data={"agent_id": "agent-other"}, follow_redirects=False)
    assert chosen.status_code == 303
    assert repo.get_selection(conn, task_id).selected_agent_id == "agent-other"
    assert json.loads(repo.get_task(conn, task_id)["target_json"]) == {"local_registration_id": "local-other"}
    assert "확인 필요" not in status_of_detail(detail(web, task_id))


def status_of_detail(html: str) -> str:
    """상세의 상태 줄(`status-line`) 마크업."""
    match = re.search(r'<[^>]+class="[^"]*status-line[^"]*"[^>]*>(.*?)</(?:div|p)>', html, re.DOTALL)
    assert match, "status-line 요소 없음"
    return match.group(1)


def test_unregister_agent_and_409_when_task_in_progress(web_demo, conn, settings):
    web = web_demo
    task_id = create_task(web, diagnose_form_demo())  # agent-ops-demo 선택됨, 미완료
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

    repo.update_task_status(conn, task_id, "실패", "검토 거절", finished_at=NOW, now=NOW)
    assert web.post("/agents/agent-ops-demo/unregister", follow_redirects=False).status_code == 303
    assert "먼저 에이전트를 등록하세요." in web.get("/tasks").text


def test_registration_is_isolated_per_session(app_demo, web_demo, conn, settings):
    app = app_demo
    web = web_demo
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


def test_scripted_agent_is_labelled_on_cards_and_result_card(web_demo, conn, store, settings):
    web = web_demo
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

    task_b, _ = seed_reviewable_fix_demo(web, conn, store, settings)
    text = detail(web, task_b)
    card = text[text.index('class="result-card'):text.index('class="viewer')]
    assert "대본 재생 (실제 모델 호출 없음)" in card
    assert "대본 재생" not in detail(web, create_task(web, diagnose_form_demo()))  # 결과 없음


# --- 업무 가져오기 — GitHub·Jira fixture → 체인 (phase 5 step 5) -------------------------


def import_issues_demo(client, source: str, *keys: str):
    return client.post(
        "/tasks/import", data={"source": source, "issue_keys": list(keys)}, follow_redirects=False,
    )


def test_import_page_defaults_to_github_tab_with_mapping_preview(web_demo):
    web = web_demo
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


def test_import_page_jira_tab_lists_ops_keys(web_demo):
    web = web_demo
    text = web.get("/tasks/import?source=jira").text
    for key in ("OPS-41", "OPS-42", "OPS-43", "OPS-44"):
        assert f'value="{key}" checked' in text, key
    assert 'value="#41"' not in text
    assert "blocked by OPS-41" in text
    assert "operations.diagnose · daily-report" in text


def test_import_page_disables_button_without_registered_agent(client_demo, agents_demo):
    client = client_demo
    client.get("/tasks")
    text = client.get("/tasks/import").text
    button = re.search(r"<button[^>]*>가져와서 워크플로우 구성</button>", text)
    assert button and "disabled" in button.group(0), text
    assert "에이전트를 먼저 등록하세요" in text and 'href="/agents/register"' in text
    assert import_issues_demo(client, "github", "#41", "#42").status_code == 422


def test_import_fixture_builds_chain_of_two_tasks_and_skips_the_rest(web_demo, conn, settings):
    web = web_demo
    response = import_issues_demo(web, "github", "#41", "#42", "#43", "#44")
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


def test_import_with_codex_and_claude_picks_first_registered_agent(web_demo, conn):
    web = web_demo
    seed_agents_demo(conn, with_claude=True)
    register_agents_demo(web, "agent-claude-mac")  # 등록 순서: ops → codex → claude
    response = import_issues_demo(web, "github", "#41", "#42")
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
    task_c = create_task(web, fix_form_demo(""))
    assert repo.get_selection(conn, task_c).status == "needs_selection"


def test_import_single_fix_issue_is_standalone_manual_task(web_demo, conn):
    web = web_demo
    response = import_issues_demo(web, "github", "#42")
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
def test_import_without_known_issue_is_422(web_demo, conn, settings, keys):
    web = web_demo
    response = import_issues_demo(web, "github", *keys)
    assert response.status_code == 422, keys
    assert "no_issues" in response.text
    assert repo.list_chains(conn, session_id_of(web, settings)) == []
    assert import_issues_demo(web, "svn", "#41").status_code == 422


def test_import_counts_all_new_tasks_against_active_limit(app_demo, web_demo, conn, settings):
    app = app_demo
    web = web_demo
    limit = app.state.settings.limits.active_tasks_per_session
    for _ in range(limit - 1):
        create_task(web, diagnose_form_demo())
    response = import_issues_demo(web, "github", "#41", "#42", "#43", "#44")
    assert response.status_code == 429
    assert f"세션당 활성 업무 한도({limit}개)에 도달했습니다." in response.text
    session_id = session_id_of(web, settings)
    assert len(repo.list_tasks(conn, session_id)) == limit - 1
    assert repo.list_chains(conn, session_id) == []
    assert import_issues_demo(web, "github", "#41").status_code == 303  # 노드 1개는 들어간다


def test_import_chain_is_isolated_per_session(app_demo, web_demo, conn, agents_demo):
    app = app_demo
    web = web_demo
    chain_id = import_issues_demo(web, "github", "#41", "#42").headers["location"].removeprefix("/chains/")
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


def import_chain_demo(client, conn, *keys: str) -> tuple[str, list[str]]:
    """가져오기 뒤 (chain_id, 체인 순서의 task_id 목록)."""
    response = import_issues_demo(client, "github", *(keys or ("#41", "#42", "#43", "#44")))
    assert response.status_code == 303, response.text
    chain_id = response.headers["location"].removeprefix("/chains/")
    return chain_id, [t["task_id"] for t in repo.tasks_of_chain(conn, chain_id)]


def chain_page(client, chain_id: str) -> str:
    response = client.get(f"/chains/{chain_id}")
    assert response.status_code == 200, response.text
    return response.text


def start_button(text: str):
    return re.search(r"<button[^>]*>워크플로우 시작</button>", text)


def test_chain_page_shows_nodes_in_order_with_assignment_reasons_and_start_button(web_demo, conn):
    web = web_demo
    chain_id, (task_a, task_b) = import_chain_demo(web, conn)
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


def test_chain_start_runs_first_task_once_and_marks_started(web_demo, conn):
    web = web_demo
    chain_id, (task_a, task_b) = import_chain_demo(web, conn, "#41", "#42")
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


def test_chain_start_diagnosis_limit_429(web_demo, conn, settings):
    web = web_demo
    chain_id, _ = import_chain_demo(web, conn, "#41", "#42")
    from workflow.server.auth import utc_now
    for n in range(10):
        repo.record_diagnosis_start(conn, session_id_of(web, settings), f"exec-seed-{n}", utc_now())
    response = web.post(f"/chains/{chain_id}/start", follow_redirects=False)
    assert response.status_code == 429
    assert "오늘 이 세션의 진단 실행 한도(10회)에 도달했습니다." in response.text
    assert repo.get_chain(conn, chain_id)["started_at"] is None


def test_chain_start_requires_first_node_selection(client_demo, conn, agents_demo):
    client = client_demo
    client.get("/tasks")
    register_agents_demo(client, "agent-codex-mac")  # 진단 Agent 미등록 → #41 후보 없음
    chain_id, (task_a, task_b) = import_chain_demo(client, conn, "#41", "#42")
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
    register_agents_demo(client, "agent-ops-demo")
    chosen = client.post(
        f"/tasks/{task_a}/select", data={"agent_id": "agent-ops-demo", "return_to": "chain"}, follow_redirects=False,
    )
    assert chosen.status_code == 303 and chosen.headers["location"] == f"/chains/{chain_id}"
    text = chain_page(client, chain_id)
    assert "disabled" not in start_button(text).group(0)
    assert client.post(f"/chains/{chain_id}/start", follow_redirects=False).status_code == 303
    assert repo.active_execution(conn, task_a) is not None


def test_chain_reassigns_tied_node_before_start_only(web_demo, conn):
    web = web_demo
    seed_agents_demo(conn, with_claude=True)
    register_agents_demo(web, "agent-claude-mac")
    chain_id, (task_a, task_b) = import_chain_demo(web, conn, "#41", "#42")
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


def test_chain_live_fragment_carries_node_status(web_demo, conn):
    web = web_demo
    chain_id, (task_a, task_b) = import_chain_demo(web, conn, "#41", "#42")
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


def test_chain_human_gate_follows_last_task_review(web_demo, conn, store, settings):
    web = web_demo
    chain_id, (task_a, task_b) = import_chain_demo(web, conn, "#41", "#42")
    web.post(f"/chains/{chain_id}/start", follow_redirects=False)
    exec_a = repo.active_execution(conn, task_a)["execution_id"]
    repo.update_task_status(conn, task_a, "완료", "판정 근거: 12/12", finished_at=NOW, now=NOW)
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

    login_operator_demo(web)
    web.post(f"/operator/merges/{task_b}/confirm", follow_redirects=False)
    gate = chain_page(web, chain_id)
    assert "병합 확인됨" in gate[gate.index("검토 승인 (사람)"):]


def test_home_lists_chains_with_progress(web_demo, conn):
    web = web_demo
    chain_id, (task_a, task_b) = import_chain_demo(web, conn)
    home = web.get("/tasks").text
    assert "워크플로우" in home and f'href="/chains/{chain_id}"' in home
    assert "0/2 완료" in home and "시작 전" in home
    main = home[home.index('class="main'):]
    assert main.index("<h2>워크플로우</h2>") < main.index("<h2>업무</h2>")  # 업무 구역 위에

    repo.update_task_status(conn, task_a, "완료", "판정 근거: 12/12", finished_at=NOW, now=NOW)
    repo.mark_chain_started(conn, chain_id, NOW)
    home = web.get("/tasks").text
    assert "1/2 완료" in home and "2단계 중 2단계 대기" in home
    # 왼쪽 목록에는 Task 그대로
    sidebar = home[home.index('class="sidebar'):home.index('class="main')]
    assert f'href="/tasks/{task_a}"' in sidebar and f'href="/tasks/{task_b}"' in sidebar
    assert "/chains/" not in sidebar


def test_task_detail_links_to_its_chain(web_demo, conn):
    web = web_demo
    chain_id, (task_a, task_b) = import_chain_demo(web, conn, "#41", "#42")
    text = detail(web, task_b)
    assert f'href="/chains/{chain_id}"' in text
    assert "워크플로우 일일 보고서 생성 실패 (09-20 09:00) → 집계 API 응답 형식 변경 대응" in text
    assert f'href="/chains/{chain_id}"' in web.get(f"/tasks/{task_b}/live").text
    assert "/chains/" not in detail(web, create_task(web, diagnose_form_demo()))


# --- 운영자 ----------------------------------------------------------------------


def login_operator_demo(client, token: str = "test-operator-token"):
    return client.post("/operator/login", data={"token": token}, follow_redirects=False)


def test_operator_login_rejects_wrong_token(web_demo, conn, settings):
    web = web_demo
    assert login_operator_demo(web, "wrong").status_code == 403
    assert repo.get_session(conn, session_id_of(web, settings))["is_operator"] == 0
    page = web.get("/operator")
    assert page.status_code == 200
    assert 'action="/operator/login"' in page.text
    assert 'action="/operator/agents"' not in page.text
    assert web.post("/operator/agents", data={"agent_id": "x"}, follow_redirects=False).status_code == 403
    assert web.post("/operator/connect-codes", follow_redirects=False).status_code == 403


def test_operator_issues_connect_code_and_exchange(app, web, conn, settings):
    """셀프호스트 워크스페이스 로그인이 곧 운영자다 — 운영자 화면에서 연결 코드를 발급하고 러너가 교환한다."""
    assert repo.get_session(conn, session_id_of(web, settings))["is_operator"] == 1

    page = web.get("/operator")
    assert page.status_code == 200
    assert 'action="/operator/connect-codes"' in page.text
    assert "개인 Codex" in page.text
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
    issued = web.post("/operator/connect-codes")
    code = re.search(r'id="issued-code">([^<]+)<', issued.text).group(1)
    revoked = web.post(f"/operator/connect-codes/{code}/revoke", follow_redirects=False)
    assert revoked.status_code == 303
    [row] = repo.list_connect_codes(conn)
    assert row["revoked_at"] is not None
    assert web.post(f"/operator/connect-codes/{code}/revoke", follow_redirects=False).status_code == 404


def test_operator_registers_and_deletes_agent(web_demo, conn):
    web = web_demo
    login_operator_demo(web)
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


def test_operator_reregistration_keeps_connector_report(web_demo, conn):
    web = web_demo
    login_operator_demo(web)
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


def test_operator_sees_all_sessions_tasks_merge_queue_and_usage(app_demo, web_demo, conn, store, settings, agents_demo):
    app = app_demo
    web = web_demo
    task_b, _ = seed_reviewable_fix_demo(web, conn, store, settings)
    web.post(f"/tasks/{task_b}/review", data={"decision": "approve"}, follow_redirects=False)
    other = TestClient(app)
    other.get("/tasks")
    register_agents_demo(other, "agent-ops-demo")  # 다른 세션도 카탈로그에서 등록해야 후보가 생긴다
    other_task = create_task(other, diagnose_form_demo())
    assert other.post(f"/tasks/{other_task}/run", follow_redirects=False).status_code == 303

    login_operator_demo(web)
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
    """CONTRACT 11.2 의 `code_change --[ready_for_review]--> review` 를 셀프호스트 내장 `bug_fix` 에서 잇는다
    (`bug_fix` 도 `code_change_result` 를 내고 outcome 이 같다). demo 는 `from_kind="code_change"` 를 넘긴다."""
    form = {
        "from_kind": "bug_fix",
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
    for value in ("버그 수정", "커밋 검토", "bug_fix", "code_review", "code.fix", "code.review",
                  "repository_id", "ready_for_review", "approved", "changes_requested", "needs_information"):
        assert value in text, value
    assert text.count(">내장<") == 4  # 지금 등록부의 내장 전부 (diagnosis·code_change 는 step 3 에서 빠진다)
    assert 'action="/kinds/bug_fix/delete"' not in text and 'action="/kinds/code_review/delete"' not in text
    # 내장 규칙 한 줄 텍스트 — 그래프·화살표 그림 없음, 삭제 가능
    assert "버그 수정 --[ready_for_review]--> 커밋 검토" in text
    assert text.count('action="/rules/') == 2 and "/delete" in text  # 내장 규칙 전부
    assert "규칙이 없으면 그 결과 뒤 후속은 사람이 시작합니다" in text
    assert 'action="/kinds"' in text and 'action="/rules"' in text
    assert 'name="input_kinds"' in text and 'value="handoff_bundle"' not in text
    assert 'value="bug_fix" data-outcomes="ready_for_review needs_information"' in text
    assert "<svg" not in text[text.index('class="main'):text.index('class="viewer')]


def test_register_kind_appears_on_page_and_is_isolated_per_session(web, conn, settings):
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

    # 등록부는 워크스페이스 키(session_id)별이다 — 다른 워크스페이스 행에는 내장만
    repo.create_session(conn, "sess-other", NOW)
    assert repo.get_kind(conn, "sess-other", "review") is None
    assert [s.kind for s in repo.list_kinds(conn, "sess-other")] == [k.kind for k in BUILTIN_KINDS]


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
    builtin = web.post("/kinds", data=kind_form(kind="bug_fix", input_kinds=[]), follow_redirects=False)
    assert builtin.status_code == 409 and "kind_exists" in builtin.text


@pytest.mark.parametrize("kind", ["bug_fix", "code_review"])
def test_register_kind_with_phase8_builtin_name_409(web, kind):
    """phase 8 내장 이름은 예약어다 — 사용자 정의로 가로챌 수 없다."""
    response = web.post("/kinds", data=kind_form(kind=kind, input_kinds=[]), follow_redirects=False)
    assert response.status_code == 409 and "kind_exists" in response.text


def test_register_rule_appears_as_one_line(web, conn, settings):
    register_kind(web)
    register_rule(web)
    text = kinds_page(web)
    assert "버그 수정 --[ready_for_review]--> 검토" in text
    assert text.count('action="/rules/') == 3
    rules = repo.list_rules(conn, session_id_of(web, settings))
    assert {(r.from_kind, r.to_kind) for _, r in rules[:2]} == {("diagnosis", "code_change"), ("bug_fix", "code_review")}
    assert (rules[2][1].from_kind, rules[2][1].to_kind) == ("bug_fix", "review")
    assert rules[2][1].on_outcomes == ["ready_for_review"]
    assert rules[2][1].handoff_kinds == ["diff", "code_change_result"]


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"on_outcomes": ["approved"]}, "on_outcomes 에 bug_fix 의 outcome 이 아닌 값이 있습니다: approved"),
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
    assert len(repo.list_rules(conn, session_id_of(web, settings))) == 2  # 내장 규칙만


def test_register_rule_duplicate_409(web):
    register_kind(web)
    register_rule(web)
    again = web.post("/rules", data=rule_form(), follow_redirects=False)
    assert again.status_code == 409 and "rule_exists" in again.text
    builtin = web.post(
        "/rules",
        data=rule_form(from_kind="bug_fix", on_outcomes=["ready_for_review"], to_kind="code_review",
                       handoff_kinds=["code_change_result", "diff", "test_log_after", "verification_log"]),
        follow_redirects=False,
    )
    assert builtin.status_code == 409 and "rule_exists" in builtin.text


def test_delete_kind_protected_in_use_then_success(web, conn, settings):
    session_id = session_id_of(web, settings)
    protected = web.post("/kinds/bug_fix/delete", follow_redirects=False)
    assert protected.status_code == 409
    assert "kind_protected" in protected.text and "내장 종류는 삭제할 수 없습니다" in protected.text
    assert repo.get_kind(conn, session_id, "bug_fix") is not None

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
    (rule_id, _), = [(rid, r) for rid, r in repo.list_rules(conn, session_id) if r.from_kind == "bug_fix"]
    response = web.post(f"/rules/{rule_id}/delete", follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == "/kinds"
    assert [r.from_kind for _, r in repo.list_rules(conn, session_id)] == ["diagnosis"]  # step 3 에서 빈 목록
    assert "버그 수정 --[ready_for_review]--> 커밋 검토" not in kinds_page(web)
    assert web.post(f"/rules/{rule_id}/delete", follow_redirects=False).status_code == 404
    assert web.post("/rules/rule-none/delete", follow_redirects=False).status_code == 404


def test_other_session_cannot_delete_my_kind_or_rule(app_demo, web_demo, conn, settings):
    app = app_demo
    web = web_demo
    register_kind(web)
    session_id = session_id_of(web, settings)
    (rule_id, _), = [(rid, r) for rid, r in repo.list_rules(conn, session_id) if r.from_kind == "diagnosis"]
    other = TestClient(app)
    other.get("/tasks")
    assert other.post("/kinds/review/delete", follow_redirects=False).status_code == 404
    assert other.post(f"/rules/{rule_id}/delete", follow_redirects=False).status_code == 404
    assert repo.get_kind(conn, session_id, "review") is not None
    assert len(repo.list_rules(conn, session_id)) == 2


# --- 업무 등록·가져오기·실행이 등록부를 본다 (phase 6 step 7) ------------------------------------

REVIEW_AGENT = "agent-review-mac"
LOCAL_REVIEW = "local-demo-report-claude"
REVIEW_REQUEST = "인계된 diff 와 코드 수정 결과를 검토하고 승인 여부를 판단해 주세요."


def seed_review_agent(conn) -> None:
    """능력 `review`(repository_id=demo-report-repo) 만 가진 로컬 Claude — 사용자 정의 종류 `review` 의 유일한 후보.
    `code.fix`·`code.review` 를 갖지 않아 내장 종류의 자동 선택(개인 Codex 1개)을 흔들지 않는다. 워크스페이스에 붙인다
    (워크스페이스 행이 먼저 있어야 한다)."""
    repo.upsert_agent(conn, {
        "agent_id": REVIEW_AGENT, "name": "검토 Claude", "owner_scope": "personal", "connection_type": "local",
        "local_registration_id": LOCAL_REVIEW,
        "capabilities": [{"code": "review", "scope": {"repository_id": "demo-report-repo"}}],
        "connection_state": "unknown",
    })
    repo.register_session_agent(conn, SESSION, REVIEW_AGENT, NOW)


def seed_review_agent_demo(conn) -> None:
    """카탈로그의 검토 Claude — 세션이 `register_agents_demo` 로 등록해야 후보가 된다."""
    repo.upsert_agent(conn, {
        "agent_id": REVIEW_AGENT, "name": "검토 Claude", "owner_scope": "personal", "connection_type": "local",
        "local_registration_id": LOCAL_REVIEW,
        "capabilities": [{"code": "review", "scope": {"repository_id": "demo-report-repo"}}],
        "connection_state": "unknown", "shared_to_all_sessions": True,
    })


def review_form(predecessor: str = "", **overrides) -> dict:
    form = {
        "title": "보고서 수정 검토",
        "request": REVIEW_REQUEST,
        "capability_code": "review",
        "scope_value": "demo-report-repo",
        "selection_mode": "auto",
        "chosen_agent_id": "",
        "run_mode": "manual" if not predecessor else "auto",
        "completion_mode": "review",
        "criteria_extra": "",
        "predecessor_task_id": predecessor,
        "run_id": "",
    }
    form.update(overrides)
    return form


@pytest.fixture
def review_web(web, conn):
    """`web` + 종류 review·규칙 bug_fix → review 등록 + 검토 Claude 가 워크스페이스에 붙음."""
    seed_review_agent(conn)
    register_kind(web)
    register_rule(web)
    return web


@pytest.fixture
def review_web_demo(web_demo, conn):
    """`web_demo` + 종류 review·규칙 code_change → review 등록 + 검토 Claude 세션 등록."""
    seed_review_agent_demo(conn)
    register_agents_demo(web_demo, REVIEW_AGENT)
    register_kind(web_demo)
    register_rule(web_demo, from_kind="code_change")
    return web_demo


def test_new_task_form_lists_session_kinds_with_scope_key(review_web_demo, conn, settings):
    review_web = review_web_demo
    text = html_lib.unescape(review_web.get("/tasks/new").text)
    select = text[text.index('id="capability_code"'):text.index("</select>", text.index('id="capability_code"'))]
    assert 'value="operations.diagnose" data-scope-key="workflow_id"' in select and "진단 (diagnosis)" in select
    assert 'value="code.modify" data-scope-key="repository_id"' in select and "코드 수정 (code_change)" in select
    assert 'value="review" data-scope-key="repository_id"' in select and "검토 (review) · review · repository_id" in select
    assert 'value="operations.diagnose" data-scope-key="workflow_id" selected' in select  # 빈 폼의 기본값
    assert 'id="scope-key">workflow_id<' in text  # 선택한 종류의 scope 키가 범위 값 라벨에
    assert "결과 outcome 이 허용 목록 안 · 사람 검토 승인" in text  # review 의 완료 기준 템플릿
    # 다른 세션에는 review 가 없다
    session_id = session_id_of(review_web, settings)
    assert repo.get_kind(conn, session_id, "review") is not None
    other_form = review_web.get("/tasks/new?example=fix").text
    assert 'value="code.modify" data-scope-key="repository_id" selected' in other_form


def test_create_review_task_targets_local_registration_and_needs_no_run_id(review_web, conn, settings):
    task_id = create_task(review_web, review_form())
    row = repo.get_task(conn, task_id)
    assert row["kind"] == "review" and row["run_mode"] == "manual" and row["completion_mode"] == "review"
    assert json.loads(row["required_capability_json"]) == {"code": "review", "scope": {"repository_id": "demo-report-repo"}}
    assert json.loads(row["target_json"]) == {"local_registration_id": LOCAL_REVIEW}  # 자동 선택 뒤 등록값에서
    criteria = json.loads(row["criteria_json"])
    assert [c["code"] for c in criteria] == ["outcome_in_spec"] and criteria[0]["structured"] is False
    selection = repo.get_selection(conn, task_id)
    assert selection.status == "selected" and selection.selected_agent_id == REVIEW_AGENT
    text = detail(review_web, task_id)
    assert "검토" in text and "review · repository_id=demo-report-repo 일치 후보 1개" in text
    assert "대기" in text and "연결 끊김" in text  # 로컬 에이전트라 종류와 무관하게 연결 상태를 본다

    auto = review_web.post("/tasks", data=review_form(completion_mode="auto"), follow_redirects=False)
    assert auto.status_code == 422 and "자동 완료" in auto.text


def test_create_review_task_in_session_without_the_kind_is_422(logged_in_client, conn):
    """종류는 워크스페이스 등록부에서 온다 — review 를 등록하지 않았으면 폼에 없고 보내도 422."""
    ensure_workspace(conn, NOW)
    seed_review_agent(conn)
    web = logged_in_client
    assert 'value="review" data-scope-key' not in web.get("/tasks/new").text
    gone = web.post("/tasks", data=review_form(), follow_redirects=False)
    assert gone.status_code == 422 and "등록되지 않은 업무 종류입니다." in gone.text


def test_create_review_task_after_fix_waits_and_runs_with_kind_spec(review_web, conn, store, settings):
    """A(bug_fix) → C(review) 를 직접 등록. C 는 A 결과가 판정되고 인계 묶음이 생기면 실행 가능이고,
    실행 요청에는 등록부의 KindSpec 과 LocalTarget 이 들어간다."""
    task_a = create_task(review_web, fix_form())
    task_c = create_task(review_web, review_form(task_a, run_mode="manual"))
    assert repo.get_task(conn, task_c)["predecessor_task_id"] == task_a
    assert "선행 대기" in detail(review_web, task_c)

    session_id = session_id_of(review_web, settings)
    seed_execution(conn, "exec-fix-001", task_a)
    seed_result_ready(
        conn, store, "exec-fix-001", kind="code_change_result",
        body=code_change_result("exec-fix-001", task_a), session_id=session_id,
    )
    repo.record_verdict(
        conn, task_id=task_a, execution_id="exec-fix-001",
        verdict={"outcome": "passed", "checks": [{"code": "verification_passed", "passed": True, "detail": "exit 0"}]},
        status="확인 필요", reason="검토 대기", finish=False, now=NOW,
    )
    data = json.dumps({
        "contract_version": 1, "source_execution_id": "exec-fix-001", "source_kind": "bug_fix",
        "source_result_artifact_id": "art-fix-result-001", "inputs": [], "attachments": [],
    }).encode()
    bundle, _ = repo.store_artifact(
        conn, store, execution_id="exec-fix-001", session_id=session_id,
        meta=ArtifactMeta.model_validate(meta_for(data, kind="handoff_bundle", name="manifest.json",
                                                   content_type="application/json")),
        data=data, now=NOW,
    )
    from workflow.server.auth import utc_now
    repo.update_registration(
        conn, LOCAL_REVIEW, connector_id="conn-mac-01", repository_id="demo-report-repo",
        base_commit=BASE_COMMIT, verification_profile_ids=[], discovered={}, now=utc_now(),
    )
    text = detail(review_web, task_c)
    assert "실행 가능" in text and f"{REVIEW_AGENT} 선택됨" in text and f'action="/tasks/{task_c}/run"' in text

    response = review_web.post(f"/tasks/{task_c}/run", follow_redirects=False)
    assert response.status_code == 303, response.text
    execution = repo.active_execution(conn, task_c)
    assert execution["kind"] == "review" and execution["predecessor_execution_id"] == "exec-fix-001"
    request = ExecutionRequest.model_validate_json(execution["request_json"])
    assert request.input_artifact_ids == [bundle.artifact_id]
    assert request.target.model_dump() == {"local_registration_id": LOCAL_REVIEW}
    assert request.kind_spec == repo.get_kind(conn, session_id, "review")
    assert request.kind_spec.builtin is False and request.kind_spec.outcomes == ["approved", "changes_requested", "needs_information"]
    assert "실행 요청됨" in detail(review_web, task_c)


REVIEW_ISSUE = {
    "key": "#45", "title": "보고서 수정 검토", "body": REVIEW_REQUEST,
    "labels": ["kind:review", "repository_id:demo-report-repo"], "blocked_by": ["#42"], "url": None,
}


@pytest.fixture
def review_issue_demo(monkeypatch):
    """fixture 이슈에 `kind:review` 이슈 #45(#42 뒤)를 덧붙인다 — 파일은 고치지 않는다 (공개 데모 e2e 가 4개를 센다)."""
    from workflow.adapters.task_sources import load_issues as real_load
    from workflow.domain.task_sources import Issue
    from workflow.server import views, web

    def patched(source: str):
        issues = real_load(source)
        return [*issues, Issue(source=source, **REVIEW_ISSUE)] if source == "github" else issues

    monkeypatch.setattr(web, "load_issues", patched)
    monkeypatch.setattr(views, "load_issues", patched)


def test_import_kind_label_issue_becomes_third_node_with_rule(review_web_demo, review_issue_demo, conn):
    review_web = review_web_demo
    page = html_lib.unescape(review_web.get("/tasks/import").text)
    assert "review · demo-report-repo" in page and 'value="#45" checked' in page
    response = import_issues_demo(review_web, "github", "#41", "#42", "#45")
    assert response.status_code == 303, response.text
    chain_id = response.headers["location"].removeprefix("/chains/")
    tasks = repo.tasks_of_chain(conn, chain_id)
    assert [(t["source_ref"], t["kind"]) for t in tasks] == [("#41", "diagnosis"), ("#42", "code_change"), ("#45", "review")]
    task_b, task_c = tasks[1], tasks[2]
    assert task_c["predecessor_task_id"] == task_b["task_id"]
    assert task_c["run_mode"] == "auto" and task_c["completion_mode"] == "review"
    assert json.loads(task_c["target_json"]) == {"local_registration_id": LOCAL_REVIEW}
    assert repo.get_selection(conn, task_c["task_id"]).selected_agent_id == REVIEW_AGENT
    assert json.loads(repo.get_chain(conn, chain_id)["skipped_json"]) == []

    text = html_lib.unescape(chain_page(review_web, chain_id))
    nodes = text.split('<li class="chain-node" data-task-id=')[1:]  # 이유 목록의 </li> 때문에 정규식 대신 분할
    assert len(nodes) == 3 and "#45" in nodes[2] and "검토</span>" in nodes[2]
    assert "라벨 kind:review + repository_id:demo-report-repo → review" in nodes[2]
    assert "선행 #42 (code.modify) → review 인계" in nodes[2]
    assert "3단계 중" not in text and "검토 승인 (사람) · 병합은 운영자 확인" in text


def test_import_kind_label_issue_is_standalone_without_rule_or_kind(review_web_demo, review_issue_demo, conn, settings):
    review_web = review_web_demo
    session_id = session_id_of(review_web, settings)
    (rule_id, _), = [(rid, r) for rid, r in repo.list_rules(conn, session_id) if r.to_kind == "review"]
    repo.delete_rule(conn, session_id, rule_id)
    response = import_issues_demo(review_web, "github", "#41", "#42", "#45")
    assert response.status_code == 303, response.text
    chain_id = response.headers["location"].removeprefix("/chains/")
    tasks = repo.tasks_of_chain(conn, chain_id)
    assert sorted(t["source_ref"] for t in tasks) == ["#41", "#42", "#45"]  # #45 는 체인 안의 단독 노드 (순서는 동률)
    standalone = next(t for t in tasks if t["source_ref"] == "#45")
    assert standalone["kind"] == "review" and standalone["predecessor_task_id"] is None
    assert standalone["run_mode"] == "manual" and standalone["chain_id"] == chain_id
    assert "후속 규칙 없음: code_change → review" in html_lib.unescape(chain_page(review_web, chain_id))


def test_import_page_marks_unknown_kind_label_unassignable(web_demo, review_issue_demo):
    """종류 review 가 없는 세션에서는 kind:review 이슈를 맡을 수 없다고 미리 보인다."""
    web = web_demo
    page = html_lib.unescape(web.get("/tasks/import").text)
    assert "맡을 에이전트 없음 · 등록되지 않은 종류 kind:review" in page


def test_agent_pages_show_kind_label_next_to_capability_code(review_web):
    detail_text = html_lib.unescape(review_web.get(f"/agents/{REVIEW_AGENT}").text)
    assert "review · repository_id=demo-report-repo" in detail_text and "검토" in detail_text
    codex = html_lib.unescape(review_web.get("/agents/agent-codex-mac").text)
    assert "code.fix · repository_id=demo-report-repo" in codex and "(버그 수정)" in codex
    assert "code.review · repository_id=demo-report-repo" in codex and "(커밋 검토)" in codex
    operator = html_lib.unescape(review_web.get("/operator").text)
    assert "(버그 수정)" in operator and "(검토)" in operator


def test_operator_register_agent_scope_key_defaults_for_builtin_and_required_otherwise(web_demo, conn):
    web = web_demo
    login_operator_demo(web)
    page = web.get("/operator").text
    assert 'name="scope_key"' in page and 'name="capability_code"' in page
    assert "operations.diagnose" in page and "workflow_id" in page  # 내장 코드 안내

    builtin = web.post("/operator/agents", data={
        "agent_id": "agent-api-2", "name": "둘째 진단", "owner_scope": "company", "connection_type": "api",
        "capability_code": "operations.diagnose", "scope_key": "", "scope_value": "daily-report",
        "local_registration_id": "", "api_url": "http://127.0.0.1:8101", "credential_ref": "env:DIAG_API_TOKEN",
    }, follow_redirects=False)
    assert builtin.status_code == 303, builtin.text
    assert json.loads(repo.get_agent(conn, "agent-api-2")["capabilities_json"]) == [
        {"code": "operations.diagnose", "scope": {"workflow_id": "daily-report"}}
    ]

    custom = web.post("/operator/agents", data={
        "agent_id": REVIEW_AGENT, "name": "검토 Claude", "owner_scope": "personal", "connection_type": "local",
        "capability_code": "review", "scope_key": "", "scope_value": "demo-report-repo",
        "local_registration_id": LOCAL_REVIEW, "api_url": "", "credential_ref": "",
    }, follow_redirects=False)
    assert custom.status_code == 422 and "scope 키를 입력하세요" in custom.text
    assert repo.get_agent(conn, REVIEW_AGENT) is None

    ok = web.post("/operator/agents", data={
        "agent_id": REVIEW_AGENT, "name": "검토 Claude", "owner_scope": "personal", "connection_type": "local",
        "capability_code": "review", "scope_key": "repository_id", "scope_value": "demo-report-repo",
        "local_registration_id": LOCAL_REVIEW, "api_url": "", "credential_ref": "",
    }, follow_redirects=False)
    assert ok.status_code == 303, ok.text
    assert json.loads(repo.get_agent(conn, REVIEW_AGENT)["capabilities_json"]) == [
        {"code": "review", "scope": {"repository_id": "demo-report-repo"}}
    ]
    page = html_lib.unescape(web.get("/operator").text)
    assert "review · repository_id=demo-report-repo" in page  # 운영자 목록의 능력 표시


@pytest.mark.parametrize(
    "overrides",
    [
        {"capability_code": "Code.Modify"},
        {"capability_code": "review", "scope_key": "Repo-ID"},
        {"capability_code": "", "scope_key": ""},
        {"scope_value": ""},
    ],
)
def test_operator_register_agent_rejects_bad_capability_422(web_demo, conn, overrides):
    web = web_demo
    login_operator_demo(web)
    data = {
        "agent_id": "agent-x", "name": "x", "owner_scope": "personal", "connection_type": "local",
        "capability_code": "code.modify", "scope_key": "", "scope_value": "other-repo",
        "local_registration_id": "local-x", "api_url": "", "credential_ref": "",
    }
    data.update(overrides)
    response = web.post("/operator/agents", data=data, follow_redirects=False)
    assert response.status_code == 422, overrides
    assert "invalid_field" in response.text
    assert repo.get_agent(conn, "agent-x") is None


# --- 입구 (phase 7 step 6, ADR-0010) — 워크스페이스(세션)가 입구 토큰을 발급·취소한다. 운영자 화면이 아니다 ----------


INBOUND_ITEM = {
    "key": "run-daily-0920",
    "title": "일일 보고서 2026-09-20 09:00 실행 실패",
    "body": "daily-report 의 daily-0920-0900 실행이 변환 단계에서 실패했습니다.",
    "labels": ["incident", "workflow:daily-report", "run:daily-0920-0900"],
    "blocked_by": [],
}


def sources_page(client) -> str:
    """엔티티를 복원한 페이지 텍스트 — curl 예시의 따옴표가 `&#39;` 로 이스케이프되므로."""
    response = client.get("/sources")
    assert response.status_code == 200, response.text
    return html_lib.unescape(response.text)


def issue_token(client, label: str = "n8n 테스트"):
    return client.post("/sources/tokens", data={"label": label}, follow_redirects=False)


def issued_token_of(text: str) -> str:
    match = re.search(r'id="issued-token">([^<]+)<', text)
    assert match, text
    return match.group(1)


def nav_of(html: str) -> str:
    return html[html.index('class="nav"'):html.index('class="side-head"')]


def token_row_of(html: str, token_id: str) -> str:
    match = re.search(rf'<tr data-token-id="{token_id}">(.*?)</tr>', html, re.DOTALL)
    assert match, token_id
    return match.group(1)


def inbound_post(client, token: str):
    return client.post(
        "/sources/n8n/chains", json={"contract_version": 1, "items": [INBOUND_ITEM]},
        headers={"Authorization": f"Bearer {token}"},
    )


def test_sources_page_shows_inbound_url_empty_state_example_and_sidebar_link(web):
    text = sources_page(web)
    assert "입구" in text
    # 입구 주소 — public_url 이 없으면 요청의 스킴+호스트. 경로는 inbound_api 의 것 그대로
    assert "http://testserver/sources/n8n/chains" in text
    assert "아직 발급한 토큰이 없습니다." in text
    assert 'action="/sources/tokens"' in text and 'name="label"' in text
    # 요청 예시 — 토큰 자리는 플레이스홀더, 본문은 CONTRACT 12절 (a) 와 같은 항목
    assert "curl -X POST http://testserver/sources/n8n/chains" in text
    assert "Authorization: Bearer wfs_…" in text and "content-type: application/json" in text
    assert '"key": "issue-1"' in text and '"labels": ["bug", "repo:my-repo"]' in text and '"blocked_by": []' in text
    assert "docs/n8n/README.md" in text
    # 허용 목록이 비어 있으면 callback_url 은 거부된다는 안내
    assert "callback 허용 목록이 비어 있어" in text and "WORKFLOW_CALLBACK_HOSTS" in text
    # 사이드바 — 종류·규칙 다음, 활성 표시
    nav = nav_of(web.get("/sources").text)
    assert '<a href="/sources" class="active">입구</a>' in nav
    assert nav.index('href="/kinds"') < nav.index('href="/sources"')
    assert '<a href="/sources">입구</a>' in nav_of(web.get("/tasks").text)
    # GLOSSARY 금지 표현·n8n 비판 문구 없음
    lowered = text.lower()
    for phrase in ("webhook secret", "api key", "inbound token", "whitelist"):
        assert phrase not in lowered, phrase


def test_sources_page_uses_public_url_and_lists_callback_hosts(settings, agents):
    """conftest 의 settings 에 허용 목록·공개 주소를 더한 앱 — 입구 주소는 public_url 을 앞에 쓴다."""
    custom = dataclasses.replace(
        settings, callback_hosts=("localhost:5678", "127.0.0.1"), public_url="https://runloom.example",
    )
    client = log_in(TestClient(create_app(custom)))
    text = sources_page(client)
    assert "https://runloom.example/sources/n8n/chains" in text
    assert "http://testserver/sources" not in text
    assert "callback 허용 목록이 비어 있어" not in text
    assert "localhost:5678" in text and "127.0.0.1" in text


def test_issue_token_shows_plaintext_once_and_never_again(web, conn, settings):
    response = issue_token(web)
    assert response.status_code == 200, response.text
    text = html_lib.unescape(response.text)
    token = issued_token_of(text)
    assert token.startswith("wfs_") and len(token) > 20
    assert text.count(token) == 1
    assert "이 값은 다시 볼 수 없습니다" in text
    assert "set-cookie" not in {k.lower() for k in response.headers} or token not in response.headers["set-cookie"]

    session_id = session_id_of(web, settings)
    [row] = repo.list_source_tokens(conn, session_id)
    assert (row["source"], row["label"], row["revoked_at"]) == ("n8n", "n8n 테스트", None)
    assert token not in [str(v) for v in dict(row).values()]  # DB 엔 sha256 만

    # 다시 열면 원문은 없고 목록만 — 플레이스홀더 `wfs_…` 외에 wfs_ 문자열이 없다
    again = sources_page(web)
    assert token not in again
    assert "wfs_" not in again.replace("wfs_…", "")
    assert "이 값은 다시 볼 수 없습니다" not in again
    assert row["token_id"] in again and "활성" in again and "n8n 테스트" in again
    assert f'action="/sources/tokens/{row["token_id"]}/revoke"' in again
    assert "아직 발급한 토큰이 없습니다." not in again

    # 발급한 토큰으로 입구 API 를 쓸 수 있고, 마지막 사용 시각이 표에 보인다
    assert token_row_of(again, row["token_id"]).count("없음") == 1  # 아직 마지막 사용 없음
    assert inbound_post(web, token).status_code == 201
    assert repo.list_source_tokens(conn, session_id)[0]["last_used_at"] is not None
    assert "없음" not in token_row_of(sources_page(web), row["token_id"])


def test_issue_token_label_is_optional(web, conn, settings):
    assert web.post("/sources/tokens", follow_redirects=False).status_code == 200
    [row] = repo.list_source_tokens(conn, session_id_of(web, settings))
    assert row["label"] == ""


def test_sixth_active_token_is_422_until_one_is_revoked(web, conn, settings):
    for n in range(5):
        assert issue_token(web, f"토큰 {n}").status_code == 200
    response = issue_token(web, "여섯째")
    assert response.status_code == 422
    assert "활성 토큰은 5개까지입니다. 하나를 취소하세요." in response.text
    assert "invalid_field" in response.text
    session_id = session_id_of(web, settings)
    rows = repo.list_source_tokens(conn, session_id)
    assert len(rows) == 5
    # 하나 취소하면 다시 발급할 수 있다 — 취소된 것은 상한에 들지 않는다
    assert web.post(f"/sources/tokens/{rows[0]['token_id']}/revoke", follow_redirects=False).status_code == 303
    assert issue_token(web, "여섯째").status_code == 200
    assert len(repo.list_source_tokens(conn, session_id)) == 6


def test_revoke_token_marks_row_and_blocks_inbound_api(web, conn, settings):
    token = issued_token_of(issue_token(web).text)
    session_id = session_id_of(web, settings)
    [row] = repo.list_source_tokens(conn, session_id)
    token_id = row["token_id"]
    assert inbound_post(web, token).status_code == 201

    response = web.post(f"/sources/tokens/{token_id}/revoke", follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == "/sources"
    assert repo.list_source_tokens(conn, session_id)[0]["revoked_at"] is not None
    text = sources_page(web)
    assert "취소됨" in text
    assert f'action="/sources/tokens/{token_id}/revoke"' not in text  # 취소 버튼은 활성만
    # 취소된 토큰으로는 입구 API 가 401 (step 4 의 라우트)
    denied = inbound_post(web, token)
    assert denied.status_code == 401 and denied.json()["code"] == "unauthenticated"
    # 재취소는 멱등 — 303, 처음 시각 유지
    revoked_at = repo.list_source_tokens(conn, session_id)[0]["revoked_at"]
    assert web.post(f"/sources/tokens/{token_id}/revoke", follow_redirects=False).status_code == 303
    assert repo.list_source_tokens(conn, session_id)[0]["revoked_at"] == revoked_at


def test_other_session_cannot_see_or_revoke_my_token(app_demo, web_demo, conn, settings):
    app = app_demo
    web = web_demo
    issue_token(web)
    [row] = repo.list_source_tokens(conn, session_id_of(web, settings))
    other = TestClient(app)
    other.get("/tasks")
    assert other.post(f"/sources/tokens/{row['token_id']}/revoke", follow_redirects=False).status_code == 404
    assert repo.list_source_tokens(conn, session_id_of(web, settings))[0]["revoked_at"] is None
    assert row["token_id"] not in sources_page(other)
    assert "아직 발급한 토큰이 없습니다." in sources_page(other)
    assert web.post("/sources/tokens/src-nope/revoke", follow_redirects=False).status_code == 404


def test_token_issue_is_not_on_operator_page(web):
    text = web.get("/operator").text
    assert 'action="/sources/tokens"' not in text and "입구 토큰" not in text


# --- selfhost 로그인 (phase 10 step 2, ADR-0016 결정 3) ------------------------------------

OPERATOR_TOKEN = "test-operator-token"


@pytest.fixture
def selfhost(settings):
    return create_app(dataclasses.replace(settings, mode="selfhost"))


def session_count(conn) -> int:
    return conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]


def login(client, token: str = OPERATOR_TOKEN, path: str = "/login"):
    return client.post(path, data={"token": token}, follow_redirects=False)


def test_selfhost_without_login_redirects_screens_and_rejects_api(selfhost, conn):
    client = TestClient(selfhost)
    for path in ("/", "/tasks", "/tasks/new", "/agents", "/operator", "/metrics"):
        response = client.get(path, follow_redirects=False)
        assert response.status_code == 303, path
        assert response.headers["location"] == "/login", path
    for path in ("/metrics.json", "/github/sources", "/human-requests"):
        response = client.get(path)
        assert response.status_code == 401, path
        assert response.json()["code"] == "unauthenticated", path
    assert client.post("/tasks", data={}, follow_redirects=False).status_code == 303
    assert session_count(conn) == 0
    page = client.get("/login")
    assert page.status_code == 200
    assert 'name="token"' in page.text and 'action="/login"' in page.text


def test_selfhost_login_sets_workspace_cookie_and_shares_workspace_across_browsers(selfhost, conn, settings):
    first = TestClient(selfhost)
    response = login(first)
    assert response.status_code == 303 and response.headers["location"] == "/"
    set_cookie = response.headers["set-cookie"].lower()
    assert "httponly" in set_cookie and "samesite=lax" in set_cookie and "max-age=1209600" in set_cookie
    assert verify_session(response.cookies[SESSION_COOKIE], settings.session_secret) == SELFHOST_SESSION_ID
    assert repo.get_session(conn, SELFHOST_SESSION_ID)["is_operator"] == 1
    assert first.get("/", follow_redirects=False).headers["location"] == "/tasks"
    assert first.get("/tasks").status_code == 200
    assert first.get("/metrics.json").status_code == 200  # 로그인 = 운영자

    shared = kind_form(kind="shared_check", label="브라우저 공유 확인", capability_code="shared_check")
    assert first.post("/kinds", data=shared, follow_redirects=False).status_code == 303
    second = TestClient(selfhost)  # 다른 브라우저 — 쿠키 없음
    assert second.get("/kinds", follow_redirects=False).status_code == 303
    assert login(second).status_code == 303
    assert "브라우저 공유 확인" in second.get("/kinds").text
    assert session_count(conn) == 1


def test_selfhost_wrong_token_is_rejected_without_leaking(selfhost, conn, caplog):
    client = TestClient(selfhost)
    secret_guess = "guess-" + OPERATOR_TOKEN[::-1]
    with caplog.at_level("DEBUG"):
        response = login(client, secret_guess)
    assert response.status_code == 403
    assert "토큰이 올바르지 않습니다" in response.text
    assert secret_guess not in response.text and OPERATOR_TOKEN not in response.text
    assert SESSION_COOKIE not in response.cookies
    assert all(secret_guess not in r.getMessage() and OPERATOR_TOKEN not in r.getMessage() for r in caplog.records)
    assert session_count(conn) == 0
    assert client.get("/tasks", follow_redirects=False).status_code == 303


def test_selfhost_logout_clears_cookie(selfhost, conn):
    client = TestClient(selfhost)
    login(client)
    response = client.post("/logout", follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == "/login"
    assert SESSION_COOKIE in response.headers["set-cookie"] and "max-age=0" in response.headers["set-cookie"].lower()
    assert client.get("/tasks", follow_redirects=False).headers["location"] == "/login"
    assert repo.get_session(conn, SELFHOST_SESSION_ID) is not None  # DB 는 그대로


def test_selfhost_login_throttles_repeated_failures(selfhost):
    client = TestClient(selfhost)
    for _ in range(LOGIN_MAX_FAILURES):
        assert login(client, "wrong").status_code == 403
    blocked = login(client)  # 맞는 토큰이어도 창이 지날 때까지 거부
    assert blocked.status_code == 429
    assert SESSION_COOKIE not in blocked.cookies
    assert "잠시 후" in blocked.text


def test_selfhost_login_success_resets_failure_count(selfhost):
    client = TestClient(selfhost)
    for _ in range(LOGIN_MAX_FAILURES - 1):
        login(client, "wrong")
    assert login(client).status_code == 303
    for _ in range(LOGIN_MAX_FAILURES - 1):
        assert login(client, "wrong").status_code == 403


def test_selfhost_operator_login_acts_as_login(selfhost, conn):
    client = TestClient(selfhost)
    assert login(client, "wrong", "/operator/login").status_code == 403
    response = login(client, path="/operator/login")
    assert response.status_code == 303
    assert client.get("/operator").status_code == 200
    assert session_count(conn) == 1


def test_demo_mode_has_no_login_routes(client_demo, conn):
    client = client_demo
    assert client.get("/login").status_code == 404
    assert client.post("/login", data={"token": OPERATOR_TOKEN}, follow_redirects=False).status_code == 404
    assert client.post("/logout", follow_redirects=False).status_code == 404
    assert client.get("/", follow_redirects=False).status_code == 200  # 공개 랜딩 그대로


# --- selfhost 화면 — 로그인·내비게이션·데모 전용 요소 (phase 10 step 3) ----------------------------

# demo 에서만 보이는 문구·링크. selfhost 는 템플릿이 `mode` 하나로 숨긴다.
DEMO_ONLY_TASKS = ('href="/tasks/import"', "업무 가져오기", "세션 · 익명")
DEMO_ONLY_TASK_NEW = ('name="run_id"', "demo-report-repo", "daily-report")
DEMO_ONLY_OPERATOR = ("진단 사용량", "데모 저장소")
DEMO_ONLY_SOURCES = ("demo-report-repo", "daily-0920-0900", "진단 항목")
DEMO_ONLY_DETAIL = ("후속 업무 B 등록",)


def assert_no_secrets(text: str, settings) -> None:
    for secret in (settings.operator_token, settings.session_secret, settings.diag_api_token):
        assert secret not in text


def test_selfhost_login_page_is_one_token_field_with_notice(selfhost, settings):
    client = TestClient(selfhost)
    page = client.get("/login").text
    assert page.count("<input") == 1 and 'type="password"' in page and 'name="token"' in page
    assert "/static/style.css" in page
    assert "셀프호스트" in page and "OPERATOR_TOKEN" in page
    assert 'class="alert"' not in page
    failed = login(client, "wrong").text
    assert 'class="alert"' in failed and "토큰이 올바르지 않습니다" in failed
    assert_no_secrets(page + failed, settings)


def test_selfhost_navigation_after_login_has_logout_metrics_github(selfhost, settings):
    client = TestClient(selfhost)
    login(client)
    html = client.get("/tasks").text
    sidebar = html[html.index('class="sidebar'):html.index('class="main')]
    assert 'action="/logout"' in sidebar and "로그아웃" in sidebar
    assert 'href="/metrics"' in sidebar and 'href="/operator/github"' in sidebar
    assert 'href="/tasks/new"' in sidebar  # `+` 는 직접 등록
    assert_no_secrets(html, settings)


def test_selfhost_hides_demo_only_elements(selfhost, settings):
    client = TestClient(selfhost)
    login(client)
    task_id = create_task(client, fix_form(scope_value="my-repo"))
    pages = {
        "/tasks": DEMO_ONLY_TASKS,
        "/tasks/new": DEMO_ONLY_TASK_NEW,
        "/operator": DEMO_ONLY_OPERATOR,
        "/sources": DEMO_ONLY_SOURCES,
        f"/tasks/{task_id}": DEMO_ONLY_DETAIL,
        f"/tasks/{task_id}/live": DEMO_ONLY_DETAIL,
    }
    for path, needles in pages.items():
        response = client.get(path)
        assert response.status_code == 200, path
        for needle in needles:
            assert needle not in response.text, (path, needle)
        assert_no_secrets(response.text, settings)


def test_demo_mode_keeps_demo_only_elements(web_demo, settings):
    web = web_demo
    task_id = create_task(web, diagnose_form_demo())
    login_operator_demo(web)
    pages = {
        "/tasks": DEMO_ONLY_TASKS,
        "/tasks/new": DEMO_ONLY_TASK_NEW,
        "/operator": DEMO_ONLY_OPERATOR,
        "/sources": DEMO_ONLY_SOURCES,
        f"/tasks/{task_id}": DEMO_ONLY_DETAIL,
        f"/tasks/{task_id}/live": DEMO_ONLY_DETAIL,
    }
    for path, needles in pages.items():
        text = web.get(path).text
        for needle in needles:
            assert needle in text, (path, needle)
    assert 'action="/logout"' not in web.get("/tasks").text
