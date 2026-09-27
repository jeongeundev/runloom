# ruff: noqa: F811 — test_task_cycle 픽스처(cycle·worker)를 가져와 인자로 쓴다
"""GitHub 업무 순환 화면 — 운영자 연결 화면·업무 상세의 원본·대기 사유·사람 응답·결과·반영 상태 (phase 8 step 13).

서버 렌더만 본다(브라우저 스크립트는 돌리지 않는다). 쓰기는 화면이 부르는 JSON API(`/github/sources…`·
`/human-requests/…/responses`)를 그대로 부른다 — 화면 전용 쓰기 경로를 따로 두지 않는다. 워커·연결 프로그램 흉내는
test_task_cycle 의 시드를 그대로 쓴다(세션 `sess-cycle` 을 이 클라이언트의 운영자 세션으로 삼는다).
"""

import dataclasses
import html as html_lib
import re

import pytest

from workflow.adapters import repo
from workflow.server import task_cycle, views
from workflow.server.auth import SESSION_COOKIE, sign_session

from .test_task_cycle import (  # noqa: F401 — 픽스처
    FIX,
    NOW,
    REVIEW,
    SESSION,
    SOURCE,
    clock,
    config,
    cycle,
    executions,
    finish_fix,
    finish_review,
    import_issue,
    make_worker,
    pr_github,
    pr_worker,
    review_tasks,
    worker,
)
from .test_task_cycle import _approved

TOKEN = "ghp_uiTestSecretValue123"


@pytest.fixture
def settings(settings):
    """acme/billing 허용 + GitHub 토큰이 환경에 있는 서버."""
    return dataclasses.replace(settings, github_repos=("acme/billing",), github_token=TOKEN)


@pytest.fixture
def operator(client, cycle, conn):
    """이 클라이언트를 `sess-cycle` 운영자 세션으로 — 소스·업무의 주인."""
    repo.mark_operator(conn, SESSION)
    client.cookies.set(SESSION_COOKIE, sign_session(SESSION, "test-session-secret"))
    return client


def page(client, url: str) -> str:
    response = client.get(url)
    assert response.status_code == 200, response.text
    assert TOKEN not in response.text  # 토큰 값은 어떤 화면에도 없다
    return response.text


def form_value(text: str, request_id: str, name: str) -> str:
    """응답 폼(`data-request-id`) 안의 hidden 값."""
    block = text.split(f'data-request-id="{request_id}"', 1)[1].split("</form>", 1)[0]
    return re.search(rf'name="{name}" value="([^"]*)"', block).group(1)


# --- 운영자 GitHub 화면 ---------------------------------------------------------------------------


def test_github_page_is_operator_only(client, cycle):
    response = client.get("/operator/github")
    assert response.status_code == 403
    assert "운영자" in response.text
    assert 'href="/operator/github"' not in client.get("/tasks").text


def test_github_page_shows_settings_assignees_and_the_real_issue_list(operator, conn, worker):
    task_id = import_issue(conn, 1)
    import_issue(conn, 2, assignee_ids=[], assignee_logins=[])
    worker.tick()

    text = page(operator, "/operator/github")

    assert "서버 환경변수 토큰(WORKFLOW_GITHUB_TOKEN) 연결됨" in text
    assert "acme/billing" in text and SOURCE in text
    # 설정·미리보기·담당 연결 폼은 저장소 카드의 접힌 고급 설정에서 JSON API 로 보낸다 — 토큰 입력칸은 접힌 고급 연결 폼 하나뿐,
    # 값을 채우지 않는다(phase 11 step 8)
    assert 'data-json-action="/github/sources/preview"' in text
    assert f'data-json-action="/github/sources/{SOURCE}"' in text and 'data-json-method="PUT"' in text
    assert f'data-json-action="/github/sources/{SOURCE}/stop"' in text
    assert f'data-json-action="/github/sources/{SOURCE}/assignees"' in text
    assert re.findall(r'<input[^>]*name="token"[^>]*>', text) == ['<input type="password" id="gh-token" name="token" autocomplete="off" required>']
    assert "kim-dev" in text and FIX in text  # 담당 연결
    # 실제 업무 목록 — 원본 링크는 저장소 이름·번호로 만든다, Task 상태·대기 사유와 함께
    assert 'href="https://github.com/acme/billing/issues/1"' in text
    assert f'href="/tasks/{task_id}"' in text
    assert "실제 GitHub 이슈" in text
    assert "GitHub 담당자 없음" in text


def test_github_page_without_token_says_so(operator, settings, app):
    app.state.settings = dataclasses.replace(settings, github_token="")
    text = page(operator, "/operator/github")
    assert "서버 환경변수 토큰(WORKFLOW_GITHUB_TOKEN) 없음" in text


def test_sidebar_links_github_page_for_operator(operator):
    assert 'href="/operator/github"' in page(operator, "/tasks")


# --- 업무 상세: 원본·담당·대기 사유 --------------------------------------------------------------


def test_detail_shows_origin_assignee_and_the_same_blockers_as_the_worker(operator, conn, worker, settings):
    task_id = import_issue(conn, 1, assignee_ids=[], assignee_logins=[], state="closed")
    worker.tick()
    row = repo.get_task(conn, task_id)

    text = page(operator, f"/tasks/{task_id}")

    readiness = task_cycle.evaluate(conn, row, now=NOW, settings=settings)
    assert [b.reason for b in readiness.blockers] == ["원본 이슈 닫힘 — 재오픈 시 재평가", "GitHub 담당자 없음"]
    for blocker in readiness.blockers:
        assert f'data-blocker="{blocker.code}"' in text and blocker.reason in text
    assert row["status_reason"] in text  # 워커가 저장한 이유와 같은 문구
    assert "실제 GitHub 이슈" in text and 'href="https://github.com/acme/billing/issues/1"' in text
    assert "원본 닫힘" in text
    # 업무 순환 Task 는 웹 선택 폼으로 담당을 바꾸지 않는다 — 담당은 GitHub 담당 연결·사람 요청 응답으로
    assert f'action="/tasks/{task_id}/select"' not in text
    assert f'action="/tasks/{task_id}/run"' not in text


def test_detail_shows_bound_agent_for_the_github_assignee(operator, conn, worker):
    task_id = import_issue(conn, 1)
    worker.tick()
    text = page(operator, f"/tasks/{task_id}")
    assert "kim-dev" in text and FIX in text
    assert "실행 요청됨" in text


def test_issue_text_is_escaped(operator, conn, worker):
    task_id = import_issue(conn, 1, title="<script>alert(1)</script>", body="<img src=x onerror=alert(2)>")
    worker.tick()
    for url in (f"/tasks/{task_id}", "/operator/github"):
        text = page(operator, url)
        assert "<script>alert(1)</script>" not in text
        assert "<img src=x" not in text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page(operator, f"/tasks/{task_id}")


def test_views_blockers_are_the_readiness_blockers(operator, conn, worker, settings, store):
    task_id = import_issue(conn, 1, assignee_ids=[], assignee_logins=[])
    worker.tick()
    row = repo.get_task(conn, task_id)
    context = views.cycle_context(conn, store, row, now=NOW, settings=settings, is_operator=True)
    readiness = task_cycle.evaluate(conn, row, now=NOW, settings=settings)
    assert [(b["code"], b["reason"], b["actor"]) for b in context["blockers"]] == [
        (b.code, b.reason, b.actor) for b in readiness.blockers
    ]


def test_plain_tasks_have_no_cycle_block(client, conn, store, settings):
    from .conftest import SESSION as PLAIN_SESSION
    from .conftest import TASK_A, seed_agents, task_row

    repo.create_session(conn, PLAIN_SESSION, NOW)
    seed_agents(conn)
    repo.insert_task(conn, task_row(TASK_A), NOW)
    assert views.cycle_context(conn, store, repo.get_task(conn, TASK_A), now=NOW, settings=settings,
                               is_operator=False) is None


# --- 사람 요청 응답 ----------------------------------------------------------------------------


def _two_assignees(conn) -> str:
    from workflow.contracts.github import AssigneeBinding

    from .test_task_cycle import FIX_SHOP

    repo.bind_assignee(conn, SESSION, AssigneeBinding(source_id=SOURCE, github_user_id=777, github_login="lee-dev",
                                                      agent_id=FIX_SHOP), NOW)
    return import_issue(conn, 1, assignee_ids=[5812345, 777], assignee_logins=["kim-dev", "lee-dev"])


def test_open_request_renders_an_idempotent_response_form_and_resumes_after_answer(operator, conn, worker):
    task_id = _two_assignees(conn)
    worker.tick()
    (request,) = repo.list_human_requests(conn, task_id)
    request_id = request["request_id"]

    text = page(operator, f"/tasks/{task_id}")
    assert request["question"] in text and 'data-blocker="assignee_multiple"' in text
    assert f'data-json-action="/human-requests/{request_id}/responses"' in text
    response_id = form_value(text, request_id, "response_id")
    assert form_value(text, request_id, "expected_revision") == str(request["revision"])
    block = text.split(f'data-request-id="{request_id}"', 1)[1].split("</form>", 1)[0]
    assert 'value="choose_agent"' in block and 'value="close"' in block and 'value="resume"' not in block
    assert page(operator, f"/tasks/{task_id}").count(response_id) == 0  # 다시 열면 새 응답 ID

    body = {"response_id": response_id, "expected_revision": request["revision"], "action": "choose_agent",
            "text": "kim 이 맡습니다", "agent_id": FIX}
    first = operator.post(f"/human-requests/{request_id}/responses", json=body)
    again = operator.post(f"/human-requests/{request_id}/responses", json=body)  # 두 번 누름
    assert (first.status_code, first.json()["created"]) == (200, True)
    assert (again.status_code, again.json()["created"]) == (200, False)
    assert repo.get_task(conn, task_id)["revision"] == 2

    worker.tick()
    (execution,) = executions(conn, task_id)
    assert execution["start_key"] == f"auto:{task_id}:r2"
    text = page(operator, f"/tasks/{task_id}")
    assert "kim 이 맡습니다" in text  # 입력 보충
    assert f'data-request-id="{request_id}"' not in text  # 응답한 요청은 폼이 없다


def test_response_api_rejects_form_posts_and_other_sessions(operator, client, conn, worker, app):
    task_id = _two_assignees(conn)
    worker.tick()
    (request,) = repo.list_human_requests(conn, task_id)
    url = f"/human-requests/{request['request_id']}/responses"

    # 교차 사이트 폼(text/plain·urlencoded)은 JSON 본문이 아니라 거부된다 — 기록 없음
    plain = operator.post(url, content='{"response_id": "x"}', headers={"content-type": "text/plain"})
    form = operator.post(url, data={"response_id": "x", "expected_revision": "1", "action": "close"})
    assert plain.status_code == 422 and form.status_code == 422
    assert repo.list_human_responses(conn, task_id) == []

    from fastapi.testclient import TestClient

    stranger = TestClient(app)
    assert stranger.post(url, json={"response_id": "x", "expected_revision": 1, "action": "close"}).status_code == 403
    assert stranger.get(f"/tasks/{task_id}").status_code == 404


def test_non_operator_session_sees_no_response_form(operator, conn, worker, app):
    task_id = _two_assignees(conn)
    worker.tick()
    conn.execute("UPDATE sessions SET is_operator = 0 WHERE session_id = ?", (SESSION,))
    conn.commit()
    text = page(operator, f"/tasks/{task_id}")
    (request,) = repo.list_human_requests(conn, task_id)
    assert request["question"] in text
    assert f'data-request-id="{request["request_id"]}"' not in text
    assert "운영자만 응답" in text


# --- 직접 실행 모드 -------------------------------------------------------------------------


def test_manual_mode_offers_run_and_a_second_submit_does_not_duplicate(operator, conn, worker):
    repo.save_github_source(conn, SESSION, config(review_agent_id=REVIEW, run_mode="manual"), NOW)
    task_id = import_issue(conn, 1)
    worker.tick()
    assert executions(conn, task_id) == []

    text = page(operator, f"/tasks/{task_id}")
    assert 'data-blocker="manual_mode"' in text
    assert f'action="/tasks/{task_id}/run"' in text

    first = operator.post(f"/tasks/{task_id}/run", follow_redirects=False)
    second = operator.post(f"/tasks/{task_id}/run", follow_redirects=False)
    assert first.status_code == 303
    assert second.status_code == 409
    (execution,) = executions(conn, task_id)
    assert (execution["start_key"], execution["agent_id"]) == (f"auto:{task_id}:r1", FIX)
    assert f'action="/tasks/{task_id}/run"' not in page(operator, f"/tasks/{task_id}")


def test_manual_run_refuses_a_task_that_is_not_ready(operator, conn, worker):
    repo.save_github_source(conn, SESSION, config(review_agent_id=REVIEW, run_mode="manual"), NOW)
    task_id = import_issue(conn, 1, assignee_ids=[], assignee_logins=[])
    worker.tick()
    response = operator.post(f"/tasks/{task_id}/run", follow_redirects=False)
    assert response.status_code == 409
    assert "GitHub 담당자 없음" in html_lib.unescape(response.text)
    assert executions(conn, task_id) == []


def test_manual_mode_review_starts_from_the_verified_fix_result(operator, conn, store, worker):
    repo.save_github_source(conn, SESSION, config(review_agent_id=REVIEW, run_mode="manual"), NOW)
    fix_task = import_issue(conn, 1)
    worker.tick()
    assert operator.post(f"/tasks/{fix_task}/run", follow_redirects=False).status_code == 303
    fix_exec = executions(conn, fix_task)[0]["execution_id"]
    finish_fix(conn, store, fix_exec)
    worker.tick()
    (review,) = review_tasks(conn, fix_task)
    assert executions(conn, review["task_id"]) == []

    assert f'action="/tasks/{review["task_id"]}/run"' in page(operator, f"/tasks/{review['task_id']}")
    assert operator.post(f"/tasks/{review['task_id']}/run", follow_redirects=False).status_code == 303
    (review_exec,) = executions(conn, review["task_id"])
    assert (review_exec["start_key"], review_exec["agent_id"]) == (f"review:{fix_exec}", REVIEW)


# --- 결과·생성 근거·재시도·반영 상태 ------------------------------------------------------------------


def test_review_result_creation_basis_and_attempts_are_shown(operator, conn, store, worker):
    fix_task = import_issue(conn, 1)
    worker.tick()
    fix_exec = executions(conn, fix_task)[0]["execution_id"]
    finish_fix(conn, store, fix_exec)
    worker.tick()
    (review,) = review_tasks(conn, fix_task)
    review_exec = executions(conn, review["task_id"])[0]["execution_id"]
    finish_review(conn, store, review_exec, outcome="changes_requested")
    worker.tick()

    text = page(operator, f"/tasks/{review['task_id']}")
    assert "changes_requested" in text and "검토 의견" in text
    assert "중복 조건이 남음" in text and "coupon.py" in text  # 차단 지적
    assert "b" * 40 in text  # 검토한 커밋 전체 SHA
    assert fix_exec in text and "생성 근거" in text  # 어느 수정 결과가 이 검토 Task 를 만들었나
    assert f'href="/tasks/{fix_task}"' in text

    fix_text = page(operator, f"/tasks/{fix_task}")
    assert "자동 재작업 1/1회" in fix_text
    assert "실행 2회" in fix_text


def test_delivery_state_is_shown_apart_from_the_task_state(operator, conn, worker):
    task_id = import_issue(conn, 1)
    worker.tick()
    delivery, _ = repo.enqueue_source_delivery_once(conn, task_id, "<!-- runloom:task=x -->\n본문", NOW)
    assert "반영 대기" in page(operator, f"/tasks/{task_id}")

    fence = repo.claim_source_delivery(conn, delivery.delivery_id, expected_state="pending", now=NOW,
                                       claim_until="2026-10-06T12:02:00Z", comment_id=None, for_send=True)
    repo.record_source_delivery(conn, delivery.delivery_id, attempts=fence, state="unknown", comment_id=None,
                                next_at=NOW, last_error="POST: 연결 오류", now=NOW)
    text = page(operator, f"/tasks/{task_id}")
    assert "반영 불확실" in text and "POST: 연결 오류" in text
    assert 'data-delivery="unknown"' in text

    conn.execute("UPDATE source_deliveries SET state = 'failed', last_error = 'POST: HTTP 403'")
    conn.commit()
    text = page(operator, f"/tasks/{task_id}")
    assert "반영 실패" in text and "시도 1회" in text
    # 반영 실패는 Agent 작업 상태가 아니다
    assert 'data-status="실행 요청됨"' in text and 'data-status="실패"' not in text
    assert "반영 실패" in page(operator, "/operator/github")


def test_fixture_import_is_marked_as_demo_data_not_a_real_issue(client, conn, settings):
    from .conftest import seed_agents

    assert client.get("/tasks").status_code == 200
    seed_agents(conn)
    client.post("/agents/register", data={"agent_id": "agent-codex-mac"})
    client.post("/agents/register", data={"agent_id": "agent-ops-demo"})
    created = client.post("/tasks/import", data={"source": "github", "issue_keys": ["#41"]},
                          follow_redirects=False)
    assert created.status_code == 303, created.text
    chain_id = created.headers["location"].rsplit("/", 1)[1]
    (task,) = repo.tasks_of_chain(conn, chain_id)
    text = client.get(f"/tasks/{task['task_id']}").text
    assert "시연 데이터" in text
    assert "실제 GitHub 이슈" not in text



# --- 초안 PR (phase 12 step 6) ----------------------------------------------------------------------


def test_open_pr_shows_human_turn_and_the_pr_link_then_done_after_merge(operator, conn, store, pr_worker, pr_github,
                                                                        clock):
    fix_task, _ = _approved(conn, store, pr_worker)

    text = page(operator, f"/tasks/{fix_task}")
    assert "사람 차례 · PR 확인" in text
    assert 'href="https://github.com/acme/billing/pull/31"' in text
    assert 'data-pull-request="open"' in text
    assert "사람 차례 · PR 확인" in page(operator, "/tasks")

    pr_github.merge(31)
    clock.now = "2026-10-06T13:00:00Z"
    pr_worker.tick()
    text = page(operator, f"/tasks/{fix_task}")
    assert 'data-pull-request="merged"' in text and "PR 병합" in text


def test_task_without_pr_has_no_pr_line(operator, conn, worker):
    task_id = import_issue(conn, 1)
    worker.tick()
    assert "data-pull-request" not in page(operator, f"/tasks/{task_id}")
