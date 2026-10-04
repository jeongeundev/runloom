# ruff: noqa: F811 — test_task_cycle·test_triage_runs·test_worker_next_step·test_work_actions_next_step 픽스처를 인자로 쓴다
"""다음 단계 제안 절·업무 이유·목록 배지·`/requests` 표시 — phase 22 step 9 (ADR-0027, ARCHITECTURE "결과 뒤 판단 — phase 22"
업무 상태·화면).

패널 절 `data-panel-section="next_step"`(판단 중·제안·지난 결과·처리됨·무시함·실패), [제안대로]·[무시] 버튼(`delegate`),
목록 배지 `data-next-step`, `/requests` 의 `판단 제안으로 생성`. 원인은 수정 Agent 의 `needs_information` 결과(②) —
워커가 결과 뒤 판단을 걸고 테스트가 러너 대신 판단 결과를 올린다. 실제 러너·GitHub·모델을 부르지 않는다.
"""

import re

import pytest
from fastapi.testclient import TestClient

from workflow.adapters import repo, responsibility_store
from workflow.contracts.responsibility import Responsibility
from workflow.domain import team
from workflow.server import views
from workflow.server.filters import kst

from .conftest import ADMIN_NAME, log_in
from .test_task_cycle import (  # noqa: F401 — 픽스처
    FIX,
    NOW,
    SESSION,
    clock,
    config,
    cycle,
    executions,
    make_worker,
    settings,
    worker,
)
from .test_triage_dispatch import triage_logs
from .test_triage_judge import fail, log_row, submit
from .test_triage_runs import judge, new_issue  # noqa: F401 — 픽스처
from .test_work_actions_next_step import internal, propose
from .test_worker_next_step import capable, fix_result, next_body, work_of  # noqa: F401


@pytest.fixture
def web(client, capable) -> TestClient:
    """관리자 로그인 — 판단 후보(멤버)에 들도록 판단 시작 전에."""
    return log_in(client)


@pytest.fixture
def boss(web, conn) -> str:
    return repo.ensure_first_admin(conn, SESSION, now=NOW)


@pytest.fixture
def recipient(conn, boss) -> str:
    """담당 범위 표 — kube_proxy/investigation → 박조사(판단 담당 관리자). 반환은 받는 사람 member_id."""
    member = repo.add_member(conn, SESSION, display_name="박조사", now=NOW)
    responsibility_store.replace_entries(conn, SESSION, [
        Responsibility(system_id="kube_proxy", request_kind="investigation", recipient_member_id=member,
                       judgment_member_id=boss, agent_id=None),
    ], expected_revision=repo.get_config_revision(conn, SESSION), member_id=boss, now=NOW)
    return member


def panel(client, key: str = "RUN-1") -> str:
    response = client.get(f"/work/{key}/panel")
    assert response.status_code == 200, response.text
    return response.text


def section(text: str, name: str = "next_step") -> str | None:
    found = re.search(rf'data-panel-section="{name}".*?</section>', text, re.S)
    return found.group(0) if found else None


def list_row(client, key: str = "RUN-1") -> str:
    text = client.get("/tasks").text
    found = re.search(rf'<tr class="work-row" data-work-key="{key}">.*?</tr>', text, re.S)
    assert found, text
    return found.group(0)


def started(conn, store, worker) -> tuple[str, object]:
    """수정 결과 `needs_information` → 결과 뒤 판단 시작(running). 반환은 (업무, 판단 로그 행)."""
    task_id, _ = fix_result(conn, store, worker, outcome="needs_information")
    worker.tick()
    wid = work_of(conn, task_id)["work_item_id"]
    (log,) = triage_logs(conn, wid)
    assert log["state"] == "running"
    return wid, log


HUMAN = {"type": "human", "question": "재현 금액은 얼마인가요?\n둘째 줄"}


# --- 절 그리기 -----------------------------------------------------------------------


def test_running_next_step_shows_agent_version_and_cause(web, conn, store, worker):
    started(conn, store, worker)

    text = panel(web)
    found = section(text)
    assert found is not None and 'data-next-step-state="running"' in found
    assert "<h3>다음 단계 제안</h3>" in found
    assert f"다음 단계 판단 중 · {FIX} · 기준 v1 · 원인 버그 수정 결과 needs_information" in found
    assert "<button" not in found
    # 절 순서 — 속성 다음, 진행 앞 (ARCHITECTURE 화면: 판단 절 뒤·지금 할 일 앞)
    assert text.index('data-panel-section="props"') < text.index('data-panel-section="next_step"') < text.index(
        'data-panel-section="timeline"')


def test_human_proposal_shows_head_cause_line_question_reasons_and_buttons(web, conn, store, worker, boss):
    _, _, _, log = propose(conn, store, worker, HUMAN)

    text = panel(web)
    found = section(text)
    assert 'data-next-step-state="proposed"' in found
    assert "사람 확인 · 맡겨도 됨 · 확신도 0.82 · 기준 v1" in found
    assert "원인 버그 수정 결과 needs_information" in found
    assert "사람 확인 — 재현 금액은 얼마인가요?" in found
    assert "재현 금액은 얼마인가요?\n둘째 줄" in found  # 질문 전문
    assert "명확성 — 호스트 설정값이 필요" in found
    assert 'action="/work/RUN-1/next-step/accept"' in found and 'action="/work/RUN-1/next-step/dismiss"' in found
    assert f'name="triage_id" value="{log["triage_id"]}"' in found
    assert "제안대로" in found and "무시" in found
    assert "next_step_human" not in found and "ready" not in found  # 내부 코드는 보이지 않는다
    # 업무 이유 — 열린 사람 요청이 없으니 제안이 이유다
    assert "다음 단계 제안 · 사람 확인" in text


def test_internal_request_proposal_names_the_recipient_and_shows_the_purpose(web, conn, store, worker, recipient):
    propose(conn, store, worker, internal(recipient))

    found = section(panel(web))
    assert "사내 요청 · 맡겨도 됨 · 확신도 0.82" in found
    assert "사내 요청 · kube_proxy/investigation → 박조사" in found
    assert re.search(r'<p class="[^"]*pre-wrap[^"]*"[^>]*>호스트 sysctl 값 확인</p>', found)


def test_rework_proposal_names_the_agent(web, conn, store, worker, boss):
    propose(conn, store, worker, {"type": "stage", "kind": "bug_fix", "assignee": {"type": "agent", "id": FIX},
                                  "rework": True})

    found = section(panel(web))
    assert "재작업 · 맡겨도 됨 · 확신도 0.82" in found and f"재작업 → {FIX}" in found


def test_buttons_need_the_delegate_action(web, conn, store, worker, settings, boss):
    _, _, wid, _ = propose(conn, store, worker, HUMAN)
    work = repo.get_work_item(conn, SESSION, wid)

    allowed = views.next_step_panel(conn, SESSION, work, allowed=frozenset({team.DELEGATE}), now=NOW,
                                    settings=settings)
    denied = views.next_step_panel(conn, SESSION, work, allowed=frozenset({team.RESPOND}), now=NOW, settings=settings)

    assert (allowed["state"], allowed["can_act"]) == ("proposed", True)
    assert (denied["state"], denied["can_act"]) == ("proposed", False)


def test_accepted_proposal_shows_who_and_when_without_buttons(web, conn, store, worker, boss):
    _, _, _, log = propose(conn, store, worker, HUMAN)
    web.post("/work/RUN-1/next-step/accept", data={"triage_id": log["triage_id"]}, follow_redirects=False)

    at = log_row(conn, log["triage_id"])["handled_at"]

    text = panel(web)
    found = section(text)
    assert f"제안대로 처리 · 사람 확인 · {ADMIN_NAME} · {kst(at)}" in found
    assert "<button" not in found
    assert "사람 요청 — 재현 금액은 얼마인가요?" in text  # 업무 이유는 열린 사람 요청이 먼저


def test_dismissed_proposal_is_folded_with_the_same_content(web, conn, store, worker, boss):
    _, _, _, log = propose(conn, store, worker, HUMAN)
    web.post("/work/RUN-1/next-step/dismiss", data={"triage_id": log["triage_id"]}, follow_redirects=False)

    at = log_row(conn, log["triage_id"])["handled_at"]

    found = section(panel(web))
    assert f"<summary>다음 단계 제안 무시함 · {ADMIN_NAME} · {kst(at)}</summary>" in found
    assert "사람 확인 — 재현 금액은 얼마인가요?" in found and "명확성 — 호스트 설정값이 필요" in found
    assert "<button" not in found


def test_proposal_of_a_past_result_is_folded_without_buttons(web, conn, store, worker, boss):
    _, execution_id, _, _ = propose(conn, store, worker, HUMAN)
    conn.execute("UPDATE executions SET released_at = ? WHERE execution_id = ?", (NOW, execution_id))
    conn.commit()

    found = section(panel(web))
    assert 'data-next-step-state="stale"' in found
    assert "<summary>다음 단계 제안(지난 결과)</summary>" in found
    assert "사람 확인 — 재현 금액은 얼마인가요?" in found
    assert "<button" not in found


def test_failed_next_step_shows_the_failure_name_and_message(web, conn, store, worker):
    _, log = started(conn, store, worker)
    fail(conn, log, "timeout", "30분 안에 끝나지 않음")
    worker.tick()

    found = section(panel(web))
    assert 'data-next-step-state="failed"' in found
    assert "다음 단계 판단 실패 · 시간 초과 · 30분 안에 끝나지 않음" in found
    assert "<button" not in found


def test_external_strings_are_escaped(web, conn, store, worker, recipient):
    wid, log = started(conn, store, worker)
    action = {**internal(recipient), "purpose": "<script>alert(1)</script>"}
    submit(conn, store, log, next_body(log, action, reasons=[{"criterion": "risk", "note": "<img src=x onerror=a()>"}],
                                      proceed="needs_check", missing_information=["<b>금액</b>"]))
    worker.tick()
    assert log_row(conn, log["triage_id"])["state"] == "proposed", log_row(conn, log["triage_id"])["failed_message"]
    conn.execute("UPDATE members SET display_name = '<i>박</i>' WHERE member_id = ?", (recipient,))
    conn.commit()

    found = section(panel(web))
    assert "<script>" not in found and "&lt;script&gt;alert(1)&lt;/script&gt;" in found
    assert "<img src=x" not in found and "&lt;img src=x onerror=a()&gt;" in found
    assert "<b>금액" not in found and "&lt;b&gt;금액&lt;/b&gt;" in found


def test_no_next_step_draws_no_section_and_keeps_the_intake_section(web, conn):
    new_issue(conn, 1)

    text = panel(web)
    assert section(text) is None
    assert section(text, "triage") is not None and "판단 받기" in section(text, "triage")


# --- 목록 배지 ------------------------------------------------------------------------


def badges(conn) -> dict[str, str | None]:
    return {r.work_key: r.next_step for r in repo.list_work_rows(conn, SESSION, closed_since=None)}


def test_list_badge_follows_the_latest_next_step(web, conn, store, worker, boss, recipient, settings):
    wid, log = started(conn, store, worker)
    assert badges(conn) == {"RUN-1": "다음 단계 판단 중"}
    assert 'data-next-step="다음 단계 판단 중"' in list_row(web)

    submit(conn, store, log, next_body(log, internal(recipient)))
    worker.tick()
    assert badges(conn) == {"RUN-1": "다음 단계 제안"}
    row = list_row(web)
    assert 'data-next-step="다음 단계 제안"' in row and "data-triage=" not in row

    web.post("/work/RUN-1/next-step/accept", data={"triage_id": log["triage_id"]}, follow_redirects=False)
    assert badges(conn) == {"RUN-1": "사내 요청 대기"}
    assert 'data-next-step="사내 요청 대기"' in list_row(web)


def test_list_badge_is_gone_when_the_cause_moves_on(web, conn, store, worker, boss):
    _, execution_id, _, _ = propose(conn, store, worker, HUMAN)
    conn.execute("UPDATE executions SET released_at = ? WHERE execution_id = ?", (NOW, execution_id))
    conn.commit()
    assert badges(conn) == {"RUN-1": None}
    assert "data-next-step" not in list_row(web)


def test_list_badge_is_gone_after_dismiss(web, conn, store, worker, boss):
    _, _, _, log = propose(conn, store, worker, HUMAN)
    web.post("/work/RUN-1/next-step/dismiss", data={"triage_id": log["triage_id"]}, follow_redirects=False)
    assert badges(conn) == {"RUN-1": None}


def test_list_has_no_next_step_badge_without_a_next_step(web, conn):
    new_issue(conn, 1)
    assert badges(conn) == {"RUN-1": None}
    assert "data-next-step" not in list_row(web)


# --- /requests ------------------------------------------------------------------------


def test_requests_page_marks_a_request_made_by_a_next_step(web, conn, store, worker, recipient):
    _, _, _, log = propose(conn, store, worker, internal(recipient))
    web.post("/work/RUN-1/next-step/accept", data={"triage_id": log["triage_id"]}, follow_redirects=False)

    text = web.get("/requests").text
    assert text.count("data-created-by-triage") == 1 and "판단 제안으로 생성" in text


def test_requests_page_has_no_mark_for_a_human_request(web, conn, recipient):
    new_issue(conn, 1)
    response = web.post("/work/RUN-1/internal-requests", data={
        "selection": '{"system_id": "kube_proxy", "request_kind": "investigation", "recipient_member_id": "'
                     + recipient + '"}',
        "purpose": "직접 확인", "submission_key": "k-1",
        "expected_directory_revision": str(repo.get_config_revision(conn, SESSION))}, follow_redirects=False)
    assert response.status_code == 303, response.text

    text = web.get("/requests").text
    assert "직접 확인" in text and "data-created-by-triage" not in text and "판단 제안으로 생성" not in text
