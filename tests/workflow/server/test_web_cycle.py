# ruff: noqa: F811 — test_task_cycle 픽스처(cycle·worker)를 가져와 인자로 쓴다
"""GitHub 업무 순환 화면 — 운영자 연결 화면·업무 상세의 원본·대기 사유·사람 응답·결과·반영 상태 (phase 8 step 13).

서버 렌더만 본다(브라우저 스크립트는 돌리지 않는다). 쓰기는 화면이 부르는 JSON API(`/github/sources…`·
`/human-requests/…/responses`)를 그대로 부른다 — 화면 전용 쓰기 경로를 따로 두지 않는다. 워커·연결 프로그램 흉내는
test_task_cycle 의 시드를 그대로 쓴다(고정 워크스페이스에 `/login` 으로 로그인한 클라이언트가 소스·업무의 주인).
"""

import dataclasses
import html as html_lib
import re

import pytest
from fastapi.testclient import TestClient

from workflow.adapters import repo
from workflow.domain import team
from workflow.server import task_cycle, views

from .conftest import log_in, log_in_member, log_in_other_workspace, session_of

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
    fail_fix,
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
    """이 클라이언트를 고정 워크스페이스(cycle 의 `SESSION`)에 로그인 — 소스·업무의 주인. 셀프호스트는 로그인 = 운영자."""
    log_in(client)
    assert session_of(client) == SESSION
    return client


def _stranger(app, conn):
    """고정 워크스페이스가 아닌 워크스페이스의 로그인 쿠키를 가진 클라이언트 — 셀프호스트에서는 로그인 안 된 것으로 본다."""
    from fastapi.testclient import TestClient

    return log_in_other_workspace(TestClient(app))


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


def test_github_page_is_operator_only(client, cycle, conn, app):
    for anonymous in (client, _stranger(app, conn)):
        response = anonymous.get("/repos", follow_redirects=False)
        assert (response.status_code, response.headers["location"]) == (303, "/login")
        assert SOURCE not in response.text
        tasks = anonymous.get("/tasks", follow_redirects=False)  # 사이드바 링크를 볼 화면도 없다
        assert (tasks.status_code, tasks.headers["location"]) == (303, "/login")


def test_github_page_shows_settings_assignees_and_the_real_issue_list(operator, conn, worker):
    task_id = import_issue(conn, 1)
    import_issue(conn, 2, assignee_ids=[], assignee_logins=[])
    worker.tick()

    text = page(operator, "/repos")

    assert "서버 환경변수 토큰(WORKFLOW_GITHUB_TOKEN) 연결됨" in text
    assert "acme/billing" in text and SOURCE in text
    # 설정·미리보기·담당 연결 폼은 저장소 카드의 접힌 고급 설정에서 JSON API 로 보낸다 — 토큰 입력칸은 접힌 고급 연결 폼 하나뿐,
    # 값을 채우지 않는다(phase 11 step 8). Jira 칸(phase 18 step 4)도 접힌 연결 폼에 빈 토큰 칸 하나를 둔다
    assert 'data-json-action="/github/sources/preview"' in text
    assert f'data-json-action="/github/sources/{SOURCE}"' in text and 'data-json-method="PUT"' in text
    assert f'data-json-action="/github/sources/{SOURCE}/stop"' in text
    assert f'data-json-action="/github/sources/{SOURCE}/assignees"' in text
    assert re.findall(r'<input[^>]*name="token"[^>]*>', text) == [
        '<input type="password" id="gh-token" name="token" autocomplete="off" required>',
        '<input type="password" id="jira-token" name="token" autocomplete="off" required>',
    ]
    assert "kim-dev" in text and FIX in text  # 담당 연결
    # 실제 업무 목록 — 원본 링크는 저장소 이름·번호로 만든다, Task 상태·대기 사유와 함께
    assert 'href="https://github.com/acme/billing/issues/1"' in text
    assert f'href="/tasks/{task_id}"' in text
    assert "실제 GitHub 이슈" in text
    assert "GitHub 담당자 없음" in text


def test_github_page_without_token_says_so(operator, settings, app):
    app.state.settings = dataclasses.replace(settings, github_token="")
    text = page(operator, "/repos")
    assert "서버 환경변수 토큰(WORKFLOW_GITHUB_TOKEN) 없음" in text


def test_sidebar_links_github_page_for_operator(operator):
    assert 'href="/repos"' in page(operator, "/tasks")  # phase 23: 저장소 화면


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
    for url in (f"/tasks/{task_id}", "/repos"):
        text = page(operator, url)
        assert "<script>alert(1)</script>" not in text
        assert "<img src=x" not in text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page(operator, f"/tasks/{task_id}")


def test_views_blockers_are_the_readiness_blockers(operator, conn, worker, settings, store):
    task_id = import_issue(conn, 1, assignee_ids=[], assignee_logins=[])
    worker.tick()
    row = repo.get_task(conn, task_id)
    context = views.cycle_context(conn, store, row, now=NOW, settings=settings,
                                 allowed=team.allowed_actions("admin"))
    readiness = task_cycle.evaluate(conn, row, now=NOW, settings=settings)
    assert [(b["code"], b["reason"], b["actor"]) for b in context["blockers"]] == [
        (b.code, b.reason, b.actor) for b in readiness.blockers
    ]


def test_plain_tasks_have_no_cycle_block(client, conn, store, settings):
    """업무 순환 종류(bug_fix·code_review)가 아니고 원본 이슈도 없는 업무 — 워크스페이스가 등록한 일반 종류."""
    from workflow.contracts.v1 import KindSpec
    from workflow.server.auth import ensure_workspace

    from .conftest import SESSION as PLAIN_SESSION
    from .conftest import seed_agents, task_row

    ensure_workspace(conn, NOW)
    seed_agents(conn)
    repo.insert_kind(conn, PLAIN_SESSION, KindSpec(
        kind="doc_update", label="문서 갱신", capability_code="docs.write", scope_key="repository_id",
        input_kinds=[], output_kind="generic_result", outcomes=["done"], instructions="문서를 고치세요.",
        builtin=False,
    ), NOW)
    repo.insert_work_item_task(conn, {**task_row("task-plain"), "kind": "doc_update",
                            "required_capability": {"code": "docs.write", "scope": {"repository_id": "docs"}},
                            "criteria": []}, NOW)
    assert views.cycle_context(conn, store, repo.get_task(conn, "task-plain"), now=NOW, settings=settings,
                               allowed=frozenset()) is None


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

    stranger = _stranger(app, conn)
    assert stranger.post(url, json={"response_id": "x", "expected_revision": 1, "action": "close"}).status_code == 401
    assert stranger.get(f"/tasks/{task_id}", follow_redirects=False).status_code == 303
    assert repo.list_human_responses(conn, task_id) == []

    # 다른 워크스페이스(운영자 세션)의 업무·사람 요청은 로그인 워크스페이스에서 보이지도 응답되지도 않는다
    from .conftest import task_row

    repo.mark_operator(conn, "sess-other")
    repo.insert_work_item_task(conn, {**task_row("task-other"), "session_id": "sess-other"}, NOW)
    other_request, _ = repo.create_human_request_once(conn, "task-other", "decision", "q", "decision:other", NOW)
    assert operator.get("/tasks/task-other").status_code == 404
    other = operator.post(f"/human-requests/{other_request}/responses",
                          json={"response_id": "x", "expected_revision": 1, "action": "close"})
    assert other.status_code == 404
    assert repo.get_human_request(conn, "sess-other", other_request)["state"] == "open"


def test_member_sees_the_response_form_regardless_of_is_operator(operator, conn, worker, app):
    # 응답은 `respond` 동작(관리자·멤버) — 워크스페이스의 `is_operator` 는 읽지 않는다 (ADR-0021)
    task_id = _two_assignees(conn)
    worker.tick()
    conn.execute("UPDATE sessions SET is_operator = 0 WHERE session_id = ?", (SESSION,))
    conn.commit()
    (request,) = repo.list_human_requests(conn, task_id)
    for client in (operator, log_in_member(TestClient(app))):
        text = page(client, f"/tasks/{task_id}")
        assert request["question"] in text
        assert f'data-request-id="{request["request_id"]}"' in text
        assert "응답 권한이 없습니다" not in text


def test_views_hide_response_and_delegate_without_the_actions(operator, conn, worker, settings, store):
    task_id = _two_assignees(conn)
    worker.tick()
    context = views.cycle_context(conn, store, repo.get_task(conn, task_id), now=NOW, settings=settings,
                                  allowed=frozenset())
    assert context["can_delegate"] is False
    assert [r["response_id"] for r in context["open_requests"]] == [None]


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
    assert "반영 실패" in page(operator, "/repos")


# --- 초안 PR (phase 12 step 6) ----------------------------------------------------------------------


def test_open_pr_shows_human_turn_and_the_pr_link_then_done_after_merge(operator, conn, store, pr_worker, pr_github,
                                                                        clock):
    fix_task, _ = _approved(conn, store, pr_worker)

    text = page(operator, f"/tasks/{fix_task}")
    assert "사람 차례 · PR 확인" in text
    assert 'href="https://github.com/acme/billing/pull/31"' in text
    assert 'data-pull-request="open"' in text
    assert 'data-status="PR · 검토"' in page(operator, "/tasks")  # 목록은 업무 상태

    pr_github.merge(31)
    clock.now = "2026-10-06T13:00:00Z"
    pr_worker.tick()
    text = page(operator, f"/tasks/{fix_task}")
    assert 'data-pull-request="merged"' in text and "PR 병합" in text


def test_task_without_pr_has_no_pr_line(operator, conn, worker):
    task_id = import_issue(conn, 1)
    worker.tick()
    assert "data-pull-request" not in page(operator, f"/tasks/{task_id}")


# --- 업무 목록·상세 (phase 14 step 9) -----------------------------------------------------------------


def main_of(text: str) -> str:
    return text[text.index('class="main'):]


def sidebar_of(text: str) -> str:
    return text[text.index('class="sidebar'):text.index('class="main')]


def fix_then_review(conn, store, worker, clock) -> tuple[str, str]:
    """(fix_task, review_task) — 수정 결과가 판정을 통과해 검토 단계가 한 시간 뒤에 생긴 GitHub 업무 하나."""
    fix_task = import_issue(conn, 1)
    worker.tick()
    finish_fix(conn, store, executions(conn, fix_task)[0]["execution_id"])
    clock.now = "2026-10-06T13:00:00Z"
    worker.tick()
    (review,) = review_tasks(conn, fix_task)
    return fix_task, review["task_id"]


def test_one_issue_with_fix_and_review_is_one_row_and_one_work_detail(operator, conn, store, worker, clock):
    fix_task, review_task = fix_then_review(conn, store, worker, clock)

    home = page(operator, "/tasks")
    main = main_of(home)
    # 한 줄 = 업무 — 단계(Task) 링크가 아니라 업무를 여는 링크 하나(phase 16: `/tasks?open=<key>`)
    assert main.count('href="/tasks?open=RUN-1"') == 1
    assert f'href="/tasks/{fix_task}"' not in main and f'href="/tasks/{review_task}"' not in main
    row = re.search(r'<tr class="work-row" data-work-key="RUN-1".*?</tr>', main, re.S).group(0)
    # phase 17: 키 칸 = RUN-n 먼저, 원본 키는 옆에 짧게(`acme/billing#1` → `billing#1`)
    assert row.index("RUN-1") < row.index("billing#1") and "acme/billing#1" not in row
    assert "버그 1" in row
    assert FIX in row  # 담당 = 에이전트 이름
    assert 'data-status="내 차례"' in row and "검토 대기" in row  # 업무 상태 — 수정 단계가 검토를 기다림
    assert "RUN-1" not in sidebar_of(home)  # phase 16: 사이드바 "최근" 목록 없음

    detail = page(operator, "/work/RUN-1")
    assert "RUN-1" in detail and 'href="https://github.com/acme/billing/issues/1"' in detail
    stages = detail.split("data-stages", 1)[1].split("</ol>", 1)[0]
    assert stages.index(f'href="/tasks/{fix_task}"') < stages.index(f'href="/tasks/{review_task}"')
    assert "버그 수정" in stages and "커밋 검토" in stages
    assert "실행 1회" in stages


def test_task_detail_links_its_work_item_and_names_stages_by_position(operator, conn, store, worker, clock):
    fix_task, review_task = fix_then_review(conn, store, worker, clock)

    fix = page(operator, f"/tasks/{fix_task}")
    crumbs = fix.split('class="crumbs"', 1)[1].split('class="bubble"', 1)[0]
    assert 'href="/tasks?open=RUN-1"' in crumbs  # 업무 패널(phase 16)
    assert f'href="/tasks/{review_task}"' in crumbs and "단계 2/2" in crumbs
    review = page(operator, f"/tasks/{review_task}")
    crumbs = review.split('class="crumbs"', 1)[1].split('class="bubble"', 1)[0]
    assert f'href="/tasks/{fix_task}"' in crumbs and "단계 1/2" in crumbs


def test_unknown_and_foreign_work_keys_are_not_shown(operator, conn, app):
    import_issue(conn, 1)
    # phase 16: 키 형식이면 `/tasks?open=` 로 넘기고, 없는 키는 패널 없이 목록 + 안내. 키 형식이 아니면 404
    assert "RUN-99 업무를 찾을 수 없습니다." in page(operator, "/work/RUN-99")
    assert operator.get("/work/RUN-99/panel").status_code == 404
    assert operator.get("/work/task-gh-1").status_code == 404
    assert operator.get("/work/RUN-0").status_code == 404
    # 다른 워크스페이스의 업무 — 키 번호가 달라도 이 워크스페이스에서는 없는 업무
    _stranger(app, conn)
    conn.execute("BEGIN IMMEDIATE")
    repo.create_work_item(conn, "sess-other", title="남의 업무", request="-", kind="bug_fix", source_type="manual",
                          now=NOW)
    conn.execute("UPDATE work_items SET key_number = 7 WHERE session_id = 'sess-other'")
    conn.commit()
    response = operator.get("/work/RUN-7")
    assert "RUN-7 업무를 찾을 수 없습니다." in response.text and "남의 업무" not in response.text
    assert operator.get("/work/RUN-7/panel").status_code == 404
    assert "남의 업무" not in page(operator, "/tasks")


def test_failed_work_offers_retry_and_close_and_retry_adds_a_stage(operator, conn, store, worker):
    fix_task, _ = fail_fix(conn, store, worker)
    (request,) = [r for r in repo.list_human_requests(conn, fix_task) if r["state"] == "open"]
    request_id = request["request_id"]

    assert 'data-status="내 차례"' in main_of(page(operator, "/tasks"))
    detail = page(operator, "/work/RUN-1")
    assert 'data-status="내 차례"' in detail and "실패 — timeout · 20분 초과" in detail
    block = detail.split(f'data-request-id="{request_id}"', 1)[1].split("</form>", 1)[0]
    assert re.search(r'value="retry">다시 맡기기<', block) and re.search(r'value="close">닫기<', block)
    assert 'value="resume"' not in block
    # 내부 코드(stage_failed)는 "자세히" 안에만
    assert "stage_failed" not in detail.split("<details", 1)[0]

    body = {"response_id": form_value(detail, request_id, "response_id"),
            "expected_revision": request["revision"], "action": "retry", "text": ""}
    assert operator.post(f"/human-requests/{request_id}/responses", json=body).status_code == 200

    detail = page(operator, "/work/RUN-1")
    stages = detail.split("data-stages", 1)[1].split("</ol>", 1)[0]
    assert stages.count('href="/tasks/') == 2
    assert f'data-request-id="{request_id}"' not in detail


def test_closing_a_failed_work_ends_it(operator, conn, store, worker):
    fix_task, _ = fail_fix(conn, store, worker)
    (request,) = [r for r in repo.list_human_requests(conn, fix_task) if r["state"] == "open"]
    detail = page(operator, "/work/RUN-1")
    body = {"response_id": form_value(detail, request["request_id"], "response_id"),
            "expected_revision": request["revision"], "action": "close", "text": ""}
    assert operator.post(f"/human-requests/{request['request_id']}/responses", json=body).status_code == 200
    detail = page(operator, "/work/RUN-1")
    assert 'data-status="종료"' in detail and "닫음 — 실행 실패" in detail
