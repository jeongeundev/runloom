"""web.py — 워크스페이스·운영자 웹 라우트. 마크업이 아니라 렌더된 텍스트·리다이렉트·상태 코드를 본다 (Step 7 이 화면을 꾸민다).

셀프호스트 전용(ADR-0019) — 로그인한 고정 워크스페이스, 러너 모양 Agent(`seed_agents`), 종류 `bug_fix`·`code_review`.
후속 인계(`/run` 의 인계 묶음)는 순환 종류가 아닌 사용자 정의 종류 `review` 로 본다. 다른 워크스페이스 격리는
`sess-other` 행을 DB 에 직접 넣어 본다."""

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
from workflow.server.web import create_chain

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
    seed_execution,
    seed_result_ready,
)

DIAGNOSE_REQUEST = (
    "일일 보고서 생성 실패를 조사하고, 로컬 개발 에이전트가 재현·수정할 수 있도록 근거와 기대 동작을 정리해 주세요."
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


# --- 러너 등록 보고·결과 시드 도우미 --------------------------------------------------------


def report_registration(conn, local_registration_id: str = LOCAL_REGISTRATION, *,
                        verification_profile_ids=("vp-pytest",)) -> None:
    """러너의 등록 보고 — Agent 에 연결 프로그램·기준 커밋·검증 프로필을 채운다. `bug_fix` 요청(CodeChangeTarget)은
    이 값이 있어야 만들어진다. 온라인 판정이 서버 시각 기준 heartbeat_offline_seconds 이내인지 보므로 실제 시각으로 둔다."""
    from workflow.server.auth import utc_now

    repo.update_registration(
        conn, local_registration_id, connector_id="conn-mac-01", repository_id=REPOSITORY,
        base_commit=BASE_COMMIT, verification_profile_ids=list(verification_profile_ids), discovered={}, now=utc_now(),
    )


def seed_reviewable_fix(client, conn, store, settings) -> tuple[str, str]:
    """직접 등록한 `bug_fix` 에 result_ready 실행·CONTRACT 7절 결과·워커 판정(`passed`)을 넣는다 — 사람 검토 대기.
    (task_id, execution_id)."""
    report_registration(conn)
    task_id = create_task(client, fix_form())
    execution_id, _ = seed_judged_fix(client, conn, store, settings, task_id, bundle=False)
    return task_id, execution_id


def seed_judged_fix(client, conn, store, settings, task_a: str, *, bundle: bool = True) -> tuple[str, str | None]:
    """`bug_fix` A 에 결과(result_ready)와 판정 `passed` 를 넣는다 — 사람 승인 전 `확인 필요` 상태. `bundle` 이면 워커가
    규칙으로 조립했을 handoff_bundle 산출물도 넣는다. (exec_a, bundle_id)."""
    session_id = session_id_of(client, settings)
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
    if not bundle:
        return "exec-fix-001", None
    data = json.dumps({
        "contract_version": 1, "source_execution_id": "exec-fix-001", "source_kind": "bug_fix",
        "source_result_artifact_id": "art-fix-result-001", "inputs": [], "attachments": [],
    }).encode()
    created, _ = repo.store_artifact(
        conn, store, execution_id="exec-fix-001", session_id=session_id,
        meta=ArtifactMeta.model_validate(meta_for(data, kind="handoff_bundle", name="manifest.json",
                                                   content_type="application/json")),
        data=data, now=NOW,
    )
    return "exec-fix-001", created.artifact_id


# --- 세션·홈 ---------------------------------------------------------------------


def test_app_home_has_agent_and_task_sections_with_direct_register(web):
    text = web.get("/tasks").text
    assert 'href="/tasks/new"' in text  # 시연 예시 없이 직접 등록
    assert 'href="/agents"' in text
    assert 'href="/"' in text  # 사이드바 브랜드 → `/` (로그인 상태면 /tasks)
    assert '/static/logo.jpg' not in text  # 로고는 랜딩에만


def test_home_lists_my_work_with_status(web):
    task_id = create_task(web, fix_form())
    text = web.get("/tasks").text
    assert "아직 업무가 없습니다." not in text
    main = text[text.index('class="main'):]
    assert 'href="/work/RUN-1"' in main and f"/tasks/{task_id}" not in main  # 한 줄 = 업무
    assert BUG_FIX_TITLE in main
    assert 'data-status="새로 들어옴"' in main and "담당 없음" in main  # 자동 선택만으로는 담당이 아니다
    # 단계 상태는 업무 상세의 단계 목록에서
    stages = web.get("/work/RUN-1").text.split("data-stages", 1)[1].split("</ol>", 1)[0]
    assert f'href="/tasks/{task_id}"' in stages
    assert 'data-status="대기"' in stages and "연결 끊김, 마지막 확인 없음" in stages  # conftest 의 Codex 는 아직 보고 전


# --- 등록 폼 ---------------------------------------------------------------------


def other_workspace_task(conn, task_id: str = "task-other") -> str:
    """다른 워크스페이스(`sess-other`)의 업무 — 셀프호스트에서 두 번째 워크스페이스는 DB 에만 만들 수 있다."""
    from .conftest import task_row

    repo.create_session(conn, "sess-other", NOW)
    repo.insert_work_item_task(conn, {**task_row(task_id), "session_id": "sess-other"}, NOW)
    return task_id


def test_new_task_form_rejects_predecessor_of_other_workspace(web, conn):
    other_task = other_workspace_task(conn)
    assert web.get(f"/tasks/new?predecessor={other_task}").status_code == 404


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
    # 코드 수정 대상 — 러너가 아직 기준 커밋·검증 프로필을 보고하지 않아 비어 있다 (보고 전에는 요청을 만들 수 없다)
    assert json.loads(row["target_json"]) == {
        "local_registration_id": LOCAL_REGISTRATION, "base_commit": None, "verification_profile_id": None,
    }
    selection = repo.get_selection(conn, task_id)
    assert selection.selected_agent_id == "agent-codex-mac" and selection.mode == "auto"
    assert selection.reason == "code.fix · repository_id=demo-report-repo 일치 후보 1개"
    # 직접 등록 = 업무 하나(원본 manual, 양식 없음) + 그 첫 단계 (ADR-0020)
    (work,) = repo.list_work_items(conn, row["session_id"])
    assert [t["task_id"] for t in repo.list_work_item_tasks(conn, work["work_item_id"])] == [task_id]
    assert (work["source_type"], work["source_id"], work["source_key"], work["priority"], work["form_json"]) == (
        "manual", None, None, "normal", "{}")


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
    # 폼의 선행은 다른 업무 — 업무 사이 `blocks` 링크로도 남는다
    work_a, work_b = (repo.work_item_of_task(conn, t)["work_item_id"] for t in (task_a, task_b))
    assert [(link["from_work_item_id"], link["to_work_item_id"], link["type"])
            for link in repo.list_work_item_links(conn, work_b)] == [(work_a, work_b, "blocks")]


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
    assert json.loads(row["target_json"]) == {
        "local_registration_id": LOCAL_REGISTRATION, "base_commit": None, "verification_profile_id": None,
    }
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


def test_cannot_see_task_of_other_workspace(web, conn):
    task_id = other_workspace_task(conn)
    assert web.get(f"/tasks/{task_id}").status_code == 404
    assert web.post(f"/tasks/{task_id}/run", follow_redirects=False).status_code == 404
    assert web.post(f"/tasks/{task_id}/review", data={"decision": "approve"}, follow_redirects=False).status_code == 404
    assert web.get("/tasks/nope").status_code == 404


# --- 직접 실행 -------------------------------------------------------------------


def test_run_creates_queued_execution_with_frozen_request(review_web, conn, settings):
    """직접 실행 — 순환 종류가 아닌 `review` 는 이 요청으로 바로 queued 실행을 만든다. 요청은 여기서 고정된다."""
    report_registration(conn, LOCAL_REVIEW, verification_profile_ids=())
    task_id = create_task(review_web, review_form())
    response = review_web.post(f"/tasks/{task_id}/run", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == f"/tasks/{task_id}"

    text = detail(review_web, task_id)
    assert "실행 요청됨" in text and "접수 대기" in text
    assert f'action="/tasks/{task_id}/run"' not in text

    execution = repo.active_execution(conn, task_id)
    assert execution["status"] == "queued"
    assert execution["attempt_no"] == 1
    assert execution["start_key"].startswith("req:")
    assert execution["assigned_connector_id"] == "conn-mac-01"  # 러너가 보고한 연결 프로그램
    request = ExecutionRequest.model_validate_json(execution["request_json"])
    assert request.model_dump() == {
        "contract_version": 1,
        "execution_id": execution["execution_id"],
        "task_id": task_id,
        "kind": "review",
        "agent_id": REVIEW_AGENT,
        "task_revision": 1,
        "request": REVIEW_REQUEST,
        "input_artifact_ids": [],
        "target": {"local_registration_id": LOCAL_REVIEW},
        "kind_spec": repo.get_kind(conn, session_id_of(review_web, settings), "review").model_dump(),  # 서버가 등록부에서 채운다 (ADR-0009)
        "work_key": f"RUN-{repo.work_item_of_task(conn, task_id)['key_number']}",  # 업무 키 (phase 14 step 7)
        "branch_seq": 1,
    }
    assert repo.get_task(conn, task_id)["status"] == "실행 요청됨"

    again = review_web.post(f"/tasks/{task_id}/run", follow_redirects=False)
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


def test_run_successor_uses_predecessor_handoff_bundle(review_web, conn, store, settings):
    """선행 A(bug_fix) 의 결과가 판정되고(사람 승인 전) handoff_bundle 산출물이 있으면, 후속 C(review) 직접 실행 요청의
    입력에 그 ID 가 고정된다 (ADR-0009 (3) — 선행 `완료` 를 기다리지 않는다)."""
    task_a = create_task(review_web, fix_form())
    exec_a, bundle_id = seed_judged_fix(review_web, conn, store, settings, task_a)
    assert repo.get_task(conn, task_a)["finished_at"] is None
    report_registration(conn, LOCAL_REVIEW, verification_profile_ids=())
    task_c = create_task(review_web, review_form(task_a, run_mode="manual"))
    text = detail(review_web, task_c)
    assert "실행 가능" in text and "선행 대기" not in text and f'action="/tasks/{task_c}/run"' in text

    response = review_web.post(f"/tasks/{task_c}/run", follow_redirects=False)
    assert response.status_code == 303, response.text
    execution = repo.active_execution(conn, task_c)
    assert execution["assigned_connector_id"] == "conn-mac-01"
    assert execution["predecessor_execution_id"] == exec_a
    request = ExecutionRequest.model_validate_json(execution["request_json"])
    assert request.input_artifact_ids == [bundle_id]
    assert request.kind_spec == repo.get_kind(conn, session_id_of(review_web, settings), "review")
    assert request.target.model_dump() == {"local_registration_id": LOCAL_REVIEW}
    assert repo.get_task(conn, task_a)["status"] == "확인 필요"  # A 는 여전히 사람 검토 전


def test_run_successor_without_handoff_409(review_web, conn):
    task_a = create_task(review_web, fix_form())
    repo.update_task_status(conn, task_a, "완료", "판정 근거: 3/3", finished_at=NOW, now=NOW)
    task_c = create_task(review_web, review_form(task_a, run_mode="manual"))
    response = review_web.post(f"/tasks/{task_c}/run", follow_redirects=False)
    assert response.status_code == 409
    assert "인계 자료" in html_lib.unescape(response.text)
    assert repo.active_execution(conn, task_c) is None


def test_run_successor_waits_for_bundle_and_says_so_without_rule(review_web, conn, store, settings):
    """선행 결과가 판정됐어도 인계 묶음이 없으면 409. 규칙이 없으면 /kinds 로 안내한다."""
    task_a = create_task(review_web, fix_form())
    seed_judged_fix(review_web, conn, store, settings, task_a, bundle=False)
    task_c = create_task(review_web, review_form(task_a, run_mode="manual"))
    text = detail(review_web, task_c)
    assert "선행 대기" in text and f'action="/tasks/{task_c}/run"' not in text
    waiting = review_web.post(f"/tasks/{task_c}/run", follow_redirects=False)
    assert waiting.status_code == 409 and "인계 자료" in alert_of(waiting) and "/kinds" not in alert_of(waiting)

    session_id = session_id_of(review_web, settings)
    (rule_id, _), = [(rid, r) for rid, r in repo.list_rules(conn, session_id) if r.to_kind == "review"]
    repo.delete_rule(conn, session_id, rule_id)
    without_rule = review_web.post(f"/tasks/{task_c}/run", follow_redirects=False)
    assert without_rule.status_code == 409
    assert "후속 규칙이 없어 인계 자료가 없습니다. /kinds 에서 규칙을 등록하세요." in html_lib.unescape(without_rule.text)
    assert repo.active_execution(conn, task_c) is None


# --- 검토 ------------------------------------------------------------------------


def test_review_approve_completes_without_merge_queue(web, conn, store, settings):
    task_id, execution_id = seed_reviewable_fix(web, conn, store, settings)
    text = detail(web, task_id)
    assert "확인 필요" in text and "검토 대기" in text
    assert f'action="/tasks/{task_id}/review"' in text
    assert RESULT_COMMIT[:7] in text
    assert "ready_for_review" in text

    response = web.post(f"/tasks/{task_id}/review", data={"decision": "approve", "comment": ""}, follow_redirects=False)
    assert response.status_code == 303
    text = detail(web, task_id)
    assert "완료" in text and "검토 승인" in text
    assert "병합: 운영자 확인 대기" not in text  # 병합 확인 대기열 없음 (ADR-0019)
    row = repo.get_task(conn, task_id)
    assert (row["status"], row["status_reason"], row["review_decision"]) == ("완료", "검토 승인", "approve")
    assert row["finished_at"] is not None
    assert repo.get_execution(conn, execution_id)["released_at"] is not None
    assert web.post(f"/tasks/{task_id}/review", data={"decision": "approve"}, follow_redirects=False).status_code == 409


def test_review_request_changes_creates_second_attempt(web, conn, store, settings):
    task_id, execution_id = seed_reviewable_fix(web, conn, store, settings)
    response = web.post(
        f"/tasks/{task_id}/review",
        data={"decision": "request_changes", "comment": "두 경로가 동시에 있는 응답 테스트가 없습니다."},
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text
    assert "실행 요청됨" in detail(web, task_id)

    executions = repo.list_executions(conn, task_id)
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
    assert request.input_artifact_ids == [previous["result_artifact_id"], review[0]["artifact_id"]]
    assert request.target.base_commit == RESULT_COMMIT  # 이전 시도의 result_commit 위에서 다시
    assert request.target.local_registration_id == LOCAL_REGISTRATION
    assert request.target.verification_profile_id == "vp-pytest"
    assert request.task_revision == 1
    assert repo.get_task(conn, task_id)["review_decision"] == "request_changes"
    assert repo.get_task(conn, task_id)["finished_at"] is None


def test_review_close_fails_task(web, conn, store, settings):
    task_id, execution_id = seed_reviewable_fix(web, conn, store, settings)
    response = web.post(f"/tasks/{task_id}/review", data={"decision": "close", "comment": "중단"}, follow_redirects=False)
    assert response.status_code == 303
    text = detail(web, task_id)
    assert "실패" in text and "검토 거절" in text
    row = repo.get_task(conn, task_id)
    assert (row["status"], row["status_reason"], row["review_decision"]) == ("실패", "검토 거절", "close")
    assert repo.get_execution(conn, execution_id)["released_at"] is not None


def test_review_rejects_when_nothing_to_review(web, conn, store, settings):
    task_id = create_task(web, fix_form())
    assert web.post(f"/tasks/{task_id}/review", data={"decision": "approve"}, follow_redirects=False).status_code == 409
    task_b, _ = seed_reviewable_fix(web, conn, store, settings)
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


def test_agents_pages_hide_credentials(web, conn):
    repo.upsert_agent(conn, {
        "agent_id": "agent-api-review", "name": "사내 검토 API", "owner_scope": "company", "connection_type": "api",
        "api_url": "http://127.0.0.1:8101", "credential_ref": "env:REVIEW_API_TOKEN",
        "capabilities": [{"code": "code.review", "scope": {"repository_id": REPOSITORY}}],
    })
    repo.register_session_agent(conn, SESSION, "agent-api-review", NOW)
    listing = web.get("/agents")
    assert listing.status_code == 200
    assert "agent-api-review" in listing.text and "agent-codex-mac" in listing.text
    assert "env:REVIEW_API_TOKEN" not in listing.text

    repo.update_registration(
        conn, LOCAL_REGISTRATION, connector_id="conn-mac-01", repository_id=REPOSITORY,
        base_commit=BASE_COMMIT, verification_profile_ids=["vp-pytest"],
        discovered={"instructions": ["AGENTS.md"], "confirmed": False}, now=NOW,
    )
    page = web.get("/agents/agent-codex-mac")
    assert page.status_code == 200
    assert "AGENTS.md" in page.text and "vp-pytest" in page.text and BASE_COMMIT in page.text
    assert "wfc_" not in page.text

    api = web.get("/agents/agent-api-review")
    assert api.status_code == 200
    assert "http://127.0.0.1:8101" in api.text
    assert "env:REVIEW_API_TOKEN" not in api.text and "credential_ref" not in api.text
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
    assert json.loads(repo.get_task(conn, task_id)["target_json"]) == {
        "local_registration_id": "local-other", "base_commit": None, "verification_profile_id": None,
    }
    assert "확인 필요" not in status_of_detail(detail(web, task_id))


def status_of_detail(html: str) -> str:
    """상세의 상태 줄(`status-line`) 마크업."""
    match = re.search(r'<[^>]+class="[^"]*status-line[^"]*"[^>]*>(.*?)</(?:div|p)>', html, re.DOTALL)
    assert match, "status-line 요소 없음"
    return match.group(1)


# --- 업무 가져오기 — GitHub·Jira fixture → 체인 (phase 5 step 5) -------------------------


# --- 워크플로우 화면 — 체인 상세·시작·라이브 (phase 5 step 6) --------------------------------


CHAIN_ITEMS = [
    {"key": "#41", "title": "일일 보고서 생성 실패 (09-20 09:00)", "body": BUG_FIX_REQUEST,
     "labels": ["kind:bug_fix", "repository_id:demo-report-repo"], "blocked_by": []},
    {"key": "#42", "title": "집계 API 응답 형식 변경 대응", "body": CODE_REVIEW_REQUEST,
     "labels": ["kind:code_review", "repository_id:demo-report-repo"], "blocked_by": ["#41"]},
    {"key": "#43", "title": "변경 응답 형식 모니터링 알림 추가",
     "body": "집계 응답의 목록 위치(items 또는 data.records)를 실행마다 기록하고, 둘 다 없거나 둘 다 있으면 알림을 보내도록 해 주세요.",
     "labels": ["enhancement", "repo:demo-report-repo"], "blocked_by": ["#42"]},
    {"key": "#44", "title": "README 오타 수정", "body": "README 의 실행 명령 예시에 오타가 있습니다. 고쳐 주세요.",
     "labels": ["docs"], "blocked_by": []},
]


def import_chain(client, conn, *keys: str) -> tuple[str, list[str]]:
    """입구(n8n)와 같은 본체(`web.create_chain`)로 체인을 만든다. (chain_id, 체인 순서의 task_id 목록)."""
    from workflow.domain.task_sources import Issue

    wanted = keys or ("#41", "#42", "#43", "#44")
    items = [i for i in CHAIN_ITEMS if i["key"] in wanted]
    issues = [
        Issue(source="n8n", key=i["key"], title=i["title"], body=i["body"], labels=tuple(i["labels"]),
              blocked_by=tuple(k for k in i["blocked_by"] if k in wanted), url=None)
        for i in items
    ]
    settings = client.app.state.settings
    created = create_chain(conn, SESSION, issues, source="n8n", now=NOW, settings=settings,
                           items=[{**i, "blocked_by": [k for k in i["blocked_by"] if k in wanted]} for i in items])
    return created.chain_id, [t["task_id"] for t in repo.tasks_of_chain(conn, created.chain_id)]


def chain_page(client, chain_id: str) -> str:
    response = client.get(f"/chains/{chain_id}")
    assert response.status_code == 200, response.text
    return response.text


def start_button(text: str):
    return re.search(r"<button[^>]*>워크플로우 시작</button>", text)


def test_chain_page_shows_nodes_in_order_with_assignment_reasons_and_start_button(web, conn):
    report_registration(conn)
    chain_id, (task_a, task_b) = import_chain(web, conn)
    text = chain_page(web, chain_id)

    assert "워크플로우" in text and "일일 보고서 생성 실패 (09-20 09:00) → 집계 API 응답 형식 변경 대응" in text
    assert "n8n" in text and "시연 데이터" not in text
    main = text[text.index('class="main'):]  # 왼쪽 목록은 업무 키(원본 키 #41·#42)를 새 업무부터 보인다
    assert main.index("#41") < main.index("#42")
    assert f'href="/tasks/{task_a}"' in text and f'href="/tasks/{task_b}"' in text
    assert "개인 Codex" in text
    assert "버그 수정" in text and "커밋 검토" in text
    assert "직접" in text and "선행 완료 시 자동" in text
    assert "검토 후 완료" in text
    assert 'data-status="실행 가능"' in text and "agent-codex-mac 선택됨" in text
    assert 'data-status="대기"' in text and "선행 대기" in text
    # 접이식 이유 — 구성 이유 문장 + SelectionRecord.reason
    assert "체인의 첫 업무 — 선행 없음" in text
    assert "선행 #41 (code.fix) → code.review 인계" in text
    assert "code.fix · repository_id=demo-report-repo 일치 후보 1개" in text
    # 사람 단계
    assert "검토 승인 (사람)" in text
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
    report_registration(conn)
    chain_id, (task_a, task_b) = import_chain(web, conn, "#41", "#42")
    response = web.post(f"/chains/{chain_id}/start", follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == f"/chains/{chain_id}"

    execution = repo.active_execution(conn, task_a)
    assert execution is not None and execution["status"] == "queued"
    assert execution["assigned_connector_id"] == "conn-mac-01"
    request = ExecutionRequest.model_validate_json(execution["request_json"])
    assert request.target.model_dump() == {
        "local_registration_id": LOCAL_REGISTRATION, "base_commit": BASE_COMMIT, "verification_profile_id": "vp-pytest",
    }
    assert repo.active_execution(conn, task_b) is None  # 후속은 워커가 선행 결과를 보고 잇는다
    assert repo.get_chain(conn, chain_id)["started_at"] is not None
    text = chain_page(web, chain_id)
    assert 'data-status="실행 요청됨"' in text and "접수 대기" in text
    assert start_button(text) is None
    assert "2단계 중 1단계 실행 요청됨" in text
    assert "담당 변경" not in text

    again = web.post(f"/chains/{chain_id}/start", follow_redirects=False)
    assert again.status_code == 303  # 멱등
    assert len(repo.list_executions(conn, task_a)) == 1


def test_chain_start_without_registration_report_409(web, conn):
    """`bug_fix` 요청은 러너가 보고한 기준 커밋·검증 프로필이 있어야 만들어진다 — 보고 전이면 시작하지 않는다."""
    chain_id, (task_a, _) = import_chain(web, conn, "#41", "#42")
    response = web.post(f"/chains/{chain_id}/start", follow_redirects=False)
    assert response.status_code == 409
    assert "request_incomplete" in response.text and "등록 정보" in html_lib.unescape(response.text)
    assert repo.active_execution(conn, task_a) is None
    assert repo.get_chain(conn, chain_id)["started_at"] is None


def test_chain_start_requires_first_node_selection(logged_in_client, conn):
    client = logged_in_client
    ensure_workspace(conn, NOW)
    repo.upsert_agent(conn, {  # 검토만 하는 Agent 만 붙음 → #41(bug_fix) 후보 없음
        "agent_id": "agent-review-only", "name": "검토 전용", "owner_scope": "personal", "connection_type": "local",
        "local_registration_id": "local-review-only",
        "capabilities": [{"code": "code.review", "scope": {"repository_id": REPOSITORY}}],
    })
    repo.register_session_agent(conn, SESSION, "agent-review-only", NOW)
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

    # 수정 Agent 가 러너로 붙고(보고 포함) 체인 화면의 인라인 폼으로 확정하면 체인 화면으로 돌아오고 시작할 수 있다
    seed_agents(conn)
    report_registration(conn)
    chosen = client.post(
        f"/tasks/{task_a}/select", data={"agent_id": "agent-codex-mac", "return_to": "chain"}, follow_redirects=False,
    )
    assert chosen.status_code == 303 and chosen.headers["location"] == f"/chains/{chain_id}"
    text = chain_page(client, chain_id)
    assert "disabled" not in start_button(text).group(0)
    assert client.post(f"/chains/{chain_id}/start", follow_redirects=False).status_code == 303
    assert repo.active_execution(conn, task_a) is not None


def test_chain_reassigns_tied_node_before_start_only(web, conn):
    seed_agents(conn, with_claude=True)  # 같은 저장소를 맡는 Claude Code 까지 — 두 노드 모두 동률
    report_registration(conn)
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
    report_registration(conn)
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


def code_review_result(execution_id: str, task_id: str, source_execution_id: str) -> dict:
    """`code_review` 의 `approved` 결과 (CONTRACT 13절)."""
    return {
        "contract_version": 1, "execution_id": execution_id, "task_id": task_id,
        "source_execution_id": source_execution_id, "reviewed_commit": RESULT_COMMIT, "outcome": "approved",
        "summary": "두 응답 형식을 모두 처리하고 재현 테스트가 있습니다.", "findings": [], "missing_information": [],
        "artifact_ids": [],
    }


def test_chain_human_gate_follows_last_task_review(web, conn, store, settings):
    report_registration(conn)
    chain_id, (task_a, task_b) = import_chain(web, conn, "#41", "#42")
    web.post(f"/chains/{chain_id}/start", follow_redirects=False)
    exec_a = repo.active_execution(conn, task_a)["execution_id"]
    repo.update_task_status(conn, task_a, "완료", "판정 근거: 3/3", finished_at=NOW, now=NOW)
    repo.release_execution(conn, exec_a, NOW)
    repo.create_execution(
        conn, execution_id="exec-review-001", task_id=task_b, attempt_no=1, start_key=f"auto:{task_b}:r1",
        agent_id="agent-codex-mac", kind="code_review",
        request=ExecutionRequest.model_validate({
            "contract_version": 1, "execution_id": "exec-review-001", "task_id": task_b, "kind": "code_review",
            "agent_id": "agent-codex-mac", "task_revision": 1, "request": CODE_REVIEW_REQUEST,
            "input_artifact_ids": ["art-fix-result-001"],
            "target": {"local_registration_id": LOCAL_REGISTRATION, "source_execution_id": exec_a,
                       "base_commit": BASE_COMMIT, "result_commit": RESULT_COMMIT},
        }),
        assigned_connector_id=None, predecessor_execution_id=exec_a, now=NOW,
    )
    seed_result_ready(
        conn, store, "exec-review-001", kind="code_review_result",
        body=code_review_result("exec-review-001", task_b, exec_a), session_id=session_id_of(web, settings),
    )
    repo.record_verdict(  # 워커의 판정 — 검토한 커밋이 최신 수정 결과와 같음
        conn, task_id=task_b, execution_id="exec-review-001",
        verdict={"outcome": "passed", "checks": [{"code": "commit_matches", "passed": True, "detail": "일치"}]},
        status="확인 필요", reason="검토 대기", finish=False, now=NOW,
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
    assert 'data-status="완료"' in gate and "검토 승인" in gate
    assert "병합: 운영자 확인 대기" not in gate and "병합 확인됨" not in gate  # 병합 확인 대기열 없음 (ADR-0019)
    assert "2단계 모두 완료" in text
    assert web.post(f"/operator/merges/{task_b}/confirm", follow_redirects=False).status_code in (404, 405)


def test_home_lists_chains_with_progress(web, conn):
    chain_id, (task_a, task_b) = import_chain(web, conn)
    home = web.get("/tasks").text
    assert "워크플로우" in home and f'href="/chains/{chain_id}"' in home
    assert "0/2 완료" in home and "시작 전" in home
    main = home[home.index('class="main'):]
    assert main.index("<h2>워크플로우</h2>") < main.index("<h2>업무</h2>")  # 업무 구역 위에

    repo.update_task_status(conn, task_a, "완료", "판정 근거: 3/3", finished_at=NOW, now=NOW)
    repo.mark_chain_started(conn, chain_id, NOW)
    home = web.get("/tasks").text
    assert "1/2 완료" in home and "2단계 중 2단계 대기" in home
    # 왼쪽 목록에는 업무 한 줄씩 — 체인 노드는 각자 업무
    sidebar = home[home.index('class="sidebar'):home.index('class="main')]
    assert 'href="/work/RUN-1"' in sidebar and 'href="/work/RUN-2"' in sidebar
    assert f'href="/tasks/{task_a}"' not in sidebar and f'href="/tasks/{task_b}"' not in sidebar
    assert "/chains/" not in sidebar


def test_task_detail_links_to_its_chain(web, conn):
    chain_id, (task_a, task_b) = import_chain(web, conn, "#41", "#42")
    text = detail(web, task_b)
    assert f'href="/chains/{chain_id}"' in text
    assert "워크플로우 일일 보고서 생성 실패 (09-20 09:00) → 집계 API 응답 형식 변경 대응" in text
    assert f'href="/chains/{chain_id}"' in web.get(f"/tasks/{task_b}/live").text
    assert "/chains/" not in detail(web, create_task(web, fix_form()))


# --- 운영자 ----------------------------------------------------------------------


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


def test_operator_registers_and_deletes_agent(web, conn):
    response = web.post("/operator/agents", data={
        "agent_id": "agent-claude-mac",
        "name": "개인 Claude",
        "owner_scope": "personal",
        "connection_type": "local",
        "capability_code": "code.fix",
        "scope_value": "other-repo",
        "local_registration_id": "local-other",
        "api_url": "",
        "credential_ref": "",
    }, follow_redirects=False)
    assert response.status_code == 303, response.text
    row = repo.get_agent(conn, "agent-claude-mac")
    assert row["shared_to_all_sessions"] == 0 and row["connection_state"] == "unknown"  # 카탈로그 없음 (ADR-0019)
    assert row["local_registration_id"] == "local-other"
    # 운영자 = 워크스페이스 — 등록하면 바로 워크스페이스에 붙어 목록·후보에 들어간다 (카탈로그 등록 단계 대신)
    assert repo.is_session_agent(conn, SESSION, "agent-claude-mac")
    assert "agent-claude-mac" in web.get("/agents").text

    bad = web.post("/operator/agents", data={
        "agent_id": "agent-api-2", "name": "x", "owner_scope": "company", "connection_type": "api",
        "capability_code": "code.review", "scope_value": REPOSITORY,
        "local_registration_id": "", "api_url": "http://127.0.0.1:8101", "credential_ref": "wfc_plain",
    }, follow_redirects=False)
    assert bad.status_code == 422
    assert repo.get_agent(conn, "agent-api-2") is None

    deleted = web.post("/operator/agents/agent-claude-mac/delete", follow_redirects=False)
    assert deleted.status_code == 303
    assert repo.get_agent(conn, "agent-claude-mac") is None
    assert web.post("/operator/agents/agent-claude-mac/delete", follow_redirects=False).status_code == 404


def test_operator_reregistration_keeps_connector_report(web, conn):
    repo.update_registration(
        conn, LOCAL_REGISTRATION, connector_id="conn-mac-01", repository_id=REPOSITORY,
        base_commit=BASE_COMMIT, verification_profile_ids=["vp-pytest"], discovered={"k": 1}, now=NOW,
    )
    response = web.post("/operator/agents", data={
        "agent_id": "agent-codex-mac", "name": "개인 Codex (이름 수정)", "owner_scope": "personal",
        "connection_type": "local", "capability_code": "code.fix", "scope_value": REPOSITORY,
        "local_registration_id": LOCAL_REGISTRATION, "api_url": "", "credential_ref": "",
    }, follow_redirects=False)
    assert response.status_code == 303
    row = repo.get_agent(conn, "agent-codex-mac")
    assert row["name"] == "개인 Codex (이름 수정)"
    assert row["connector_id"] == "conn-mac-01" and row["base_commit"] == BASE_COMMIT
    assert row["connection_state"] == "online"


def test_operator_sees_all_workspaces_tasks_without_merge_queue_or_usage(web, conn, store, settings):
    task_id, _ = seed_reviewable_fix(web, conn, store, settings)
    web.post(f"/tasks/{task_id}/review", data={"decision": "approve"}, follow_redirects=False)
    other_task = other_workspace_task(conn)

    page = web.get("/operator").text
    assert task_id in page and other_task in page
    # 병합 확인 대기열·진단 사용량은 없다 (ADR-0019)
    assert "병합 확인 대기" not in page and "진단 사용량" not in page and "오늘 진단 실행" not in page
    assert "/operator/merges/" not in page
    confirm = web.post(f"/operator/merges/{task_id}/confirm", follow_redirects=False)
    assert confirm.status_code in (404, 405)
    assert repo.get_task(conn, task_id)["merge_confirmed_at"] is None


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
    (`bug_fix` 도 `code_change_result` 를 내고 outcome 이 같다)."""
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
    assert text.count(">내장<") == 2  # 내장 전부 — bug_fix·code_review (ADR-0019)
    assert 'action="/kinds/bug_fix/delete"' not in text and 'action="/kinds/code_review/delete"' not in text
    # 내장 규칙 한 줄 텍스트 — 그래프·화살표 그림 없음, 삭제 가능
    assert "버그 수정 --[ready_for_review]--> 커밋 검토" in text
    assert text.count('action="/rules/') == 1 and "/delete" in text  # 내장 규칙 전부 (bug_fix → code_review 하나)
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
    assert text.count('action="/rules/') == 2
    rules = repo.list_rules(conn, session_id_of(web, settings))
    assert [(r.from_kind, r.to_kind) for _, r in rules] == [("bug_fix", "code_review"), ("bug_fix", "review")]
    assert rules[1][1].on_outcomes == ["ready_for_review"]
    assert rules[1][1].handoff_kinds == ["diff", "code_change_result"]
    assert rules[1][1].placement == "same_work"  # 폼에 칸이 없으면 같은 업무의 다음 단계
    assert "같은 업무의 다음 단계" in text


def test_register_rule_with_new_work_placement(web, conn, settings):
    register_kind(web)
    register_rule(web, placement="new_work")
    (_, rule), = [r for r in repo.list_rules(conn, session_id_of(web, settings)) if r[1].to_kind == "review"]
    assert rule.placement == "new_work"
    text = kinds_page(web)
    assert "새 업무로 등록" in text
    assert 'name="placement"' in text


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
        ({"placement": "elsewhere"}, "placement"),
    ],
)
def test_register_rule_rejects_invalid_422(web, conn, settings, overrides, message):
    register_kind(web)
    response = web.post("/rules", data=rule_form(**overrides), follow_redirects=False)
    assert response.status_code == 422, response.text
    assert "invalid_field" in response.text and message in response.text
    assert len(repo.list_rules(conn, session_id_of(web, settings))) == 1  # 내장 규칙만


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
    repo.insert_work_item_task(conn, row, NOW)
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
    assert repo.list_rules(conn, session_id) == []
    assert "버그 수정 --[ready_for_review]--> 커밋 검토" not in kinds_page(web)
    assert web.post(f"/rules/{rule_id}/delete", follow_redirects=False).status_code == 404
    assert web.post("/rules/rule-none/delete", follow_redirects=False).status_code == 404


def test_cannot_delete_kind_or_rule_of_other_workspace(web, conn):
    from workflow.contracts.v1 import KindSpec

    repo.create_session(conn, "sess-other", NOW)
    repo.insert_kind(conn, "sess-other", KindSpec.model_validate({
        "kind": "review", "label": "검토", "capability_code": "review", "scope_key": "repository_id",
        "input_kinds": ["diff"], "output_kind": "generic_result", "outcomes": ["approved"], "instructions": "검토",
        "builtin": False,
    }), NOW)
    rules = repo.list_rules(conn, "sess-other")
    rule_id = rules[0][0]
    assert web.post("/kinds/review/delete", follow_redirects=False).status_code == 404
    assert web.post(f"/rules/{rule_id}/delete", follow_redirects=False).status_code == 404
    assert repo.get_kind(conn, "sess-other", "review") is not None
    assert len(repo.list_rules(conn, "sess-other")) == len(rules)


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


def test_new_task_form_lists_session_kinds_with_scope_key(review_web, conn, settings):
    text = html_lib.unescape(review_web.get("/tasks/new").text)
    select = text[text.index('id="capability_code"'):text.index("</select>", text.index('id="capability_code"'))]
    assert 'value="code.fix" data-scope-key="repository_id"' in select and "버그 수정 (bug_fix)" in select
    assert 'value="code.review" data-scope-key="repository_id"' in select and "커밋 검토 (code_review)" in select
    assert 'value="review" data-scope-key="repository_id"' in select and "검토 (review) · review · repository_id" in select
    assert "operations.diagnose" not in select and "code.modify" not in select  # 진단 데모 종류 없음 (ADR-0019)
    assert 'value="code.fix" data-scope-key="repository_id" selected' in select  # 빈 폼의 기본값 bug_fix
    assert 'id="scope-key">repository_id<' in text  # 선택한 종류의 scope 키가 범위 값 라벨에
    assert "결과 outcome 이 허용 목록 안 · 사람 검토 승인" in text  # review 의 완료 기준 템플릿
    session_id = session_id_of(review_web, settings)
    assert repo.get_kind(conn, session_id, "review") is not None


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


def test_agent_pages_show_kind_label_next_to_capability_code(review_web):
    detail_text = html_lib.unescape(review_web.get(f"/agents/{REVIEW_AGENT}").text)
    assert "review · repository_id=demo-report-repo" in detail_text and "검토" in detail_text
    codex = html_lib.unescape(review_web.get("/agents/agent-codex-mac").text)
    assert "code.fix · repository_id=demo-report-repo" in codex and "(버그 수정)" in codex
    assert "code.review · repository_id=demo-report-repo" in codex and "(커밋 검토)" in codex
    operator = html_lib.unescape(review_web.get("/operator").text)
    assert "(버그 수정)" in operator and "(검토)" in operator


def test_operator_register_agent_scope_key_defaults_for_builtin_and_required_otherwise(web, conn):
    page = html_lib.unescape(web.get("/operator").text)
    assert 'name="scope_key"' in page and 'name="capability_code"' in page
    assert "code.fix</span> (repository_id)" in page and "code.review</span> (repository_id)" in page  # 내장 코드 안내
    assert "operations.diagnose" not in page and "workflow_id" not in page

    builtin = web.post("/operator/agents", data={
        "agent_id": "agent-fix-2", "name": "둘째 수정", "owner_scope": "personal", "connection_type": "local",
        "capability_code": "code.fix", "scope_key": "", "scope_value": REPOSITORY,
        "local_registration_id": "local-fix-2", "api_url": "", "credential_ref": "",
    }, follow_redirects=False)
    assert builtin.status_code == 303, builtin.text
    assert json.loads(repo.get_agent(conn, "agent-fix-2")["capabilities_json"]) == [
        {"code": "code.fix", "scope": {"repository_id": REPOSITORY}}
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
def test_operator_register_agent_rejects_bad_capability_422(web, conn, overrides):
    data = {
        "agent_id": "agent-x", "name": "x", "owner_scope": "personal", "connection_type": "local",
        "capability_code": "code.fix", "scope_key": "", "scope_value": "other-repo",
        "local_registration_id": "local-x", "api_url": "", "credential_ref": "",
    }
    data.update(overrides)
    response = web.post("/operator/agents", data=data, follow_redirects=False)
    assert response.status_code == 422, overrides
    assert "invalid_field" in response.text
    assert repo.get_agent(conn, "agent-x") is None


# --- 입구 (phase 7 step 6, ADR-0010) — 워크스페이스(세션)가 입구 토큰을 발급·취소한다. 운영자 화면이 아니다 ----------


INBOUND_ITEM = {
    "key": "fix-format",
    "title": "응답 형식 변경에 맞춰 보고서 변환 수정",
    "body": "보고서 변환 실패를 고쳐 주세요.",
    "labels": ["kind:bug_fix", "repository_id:demo-report-repo"],
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


def test_cannot_see_or_revoke_token_of_other_workspace(web, conn):
    repo.create_session(conn, "sess-other", NOW)
    token_id, _ = repo.issue_source_token(conn, "sess-other", "n8n", "다른 워크스페이스", NOW)
    assert web.post(f"/sources/tokens/{token_id}/revoke", follow_redirects=False).status_code == 404
    assert repo.list_source_tokens(conn, "sess-other")[0]["revoked_at"] is None
    assert token_id not in sources_page(web)
    assert "아직 발급한 토큰이 없습니다." in sources_page(web)
    assert web.post("/sources/tokens/src-nope/revoke", follow_redirects=False).status_code == 404


def test_token_issue_is_not_on_operator_page(web):
    text = web.get("/operator").text
    assert 'action="/sources/tokens"' not in text and "입구 토큰" not in text


# --- selfhost 로그인 (phase 10 step 2, ADR-0016 결정 3) ------------------------------------

OPERATOR_TOKEN = "test-operator-token"


@pytest.fixture
def selfhost(settings):
    return create_app(settings)


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


def test_operator_login_route_is_gone(selfhost, conn):
    """ADR-0019 — 로그인은 `/login` 하나. 예전 `/operator/login` 은 없다."""
    client = TestClient(selfhost)
    assert login(client, path="/operator/login").status_code == 404
    assert session_count(conn) == 0


# --- 셀프호스트 전용 (phase 13 step 2, ADR-0019) — 랜딩·카탈로그·fixture 가져오기 삭제 ------------------


def test_root_redirects_to_login_or_task_list(selfhost, conn):
    client = TestClient(selfhost)
    for path in ("/", "/tasks"):
        response = client.get(path, follow_redirects=False)
        assert response.status_code == 303 and response.headers["location"] == "/login", path
    assert session_count(conn) == 0
    login(client)
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == "/tasks"
    assert client.get("/").url.path == "/tasks"


def test_demo_routes_and_landing_assets_are_gone(web, conn):
    for method, path in (
        ("get", "/agents/register"),
        ("post", "/agents/register"),
        ("post", "/agents/agent-codex-mac/unregister"),
        ("get", "/tasks/import"),
        ("post", "/tasks/import"),
        ("get", "/static/hero.jpg"),
    ):
        response = getattr(web, method)(path, follow_redirects=False)
        # POST 는 남은 GET 경로(`/agents/{agent_id}`·`/tasks/{task_id}`)와 모양이 겹쳐 405 일 수 있다 — 라우트는 없다
        allowed = (404, 405) if method == "post" else (404,)
        assert response.status_code in allowed, (method, path, response.status_code)
    assert repo.is_session_agent(conn, SESSION, "agent-codex-mac")  # 해제 경로가 없으니 그대로
    for path in ("/tasks", "/agents", "/tasks/new"):
        text = web.get(path).text
        assert "/agents/register" not in text and "/tasks/import" not in text, path


def test_new_task_form_defaults_to_bug_fix(web):
    text = web.get("/tasks/new").text
    selected = re.search(r'<option value="([^"]+)"[^>]*selected', text)
    assert selected is not None and selected.group(1) == "code.fix"
    assert 'name="with_successor"' not in text
    assert web.get("/tasks/new?example=diagnose").text == text  # 시연 미리 채움 없음


# --- 화면 — 로그인·내비게이션·데모 요소 없음 (phase 10 step 3, ADR-0019) ----------------------------

# 공개 데모(main)에만 있는 문구·링크. 진단 조각(run_id·진단 사용량)은 service 에 없다 (ADR-0019).
DEMO_ONLY_TASKS = ('href="/tasks/import"', "업무 가져오기", "세션 · 익명")
DEMO_ONLY_TASK_NEW = ('name="run_id"', "demo-report-repo", "daily-report")
DEMO_ONLY_OPERATOR = ("진단 사용량", "데모 저장소")
DEMO_ONLY_SOURCES = ("demo-report-repo", "daily-0920-0900", "진단 항목")
DEMO_ONLY_DETAIL = ("후속 업무 B 등록",)


def assert_no_secrets(text: str, settings) -> None:
    for secret in (settings.operator_token, settings.session_secret):
        assert not secret or secret not in text


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


def test_selfhost_hides_demo_only_elements(settings):
    client = TestClient(create_app(settings))
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


