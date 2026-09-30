# ruff: noqa: F811 — test_task_cycle 픽스처(cycle·settings·worker)를 가져와 인자로 쓴다
"""업무 상세 패널 — phase 16 step 5 (ARCHITECTURE "업무 화면 — phase 16" 주소 표·화면 배치 "상세 패널").

`GET /work/{key}/panel` 은 `base.html` 없는 조각, `GET /tasks?open=<key>` 는 목록과 함께 같은 조각을 서버 렌더,
`GET /work/{key}` 는 303 `/tasks?open=<key>`. 절은 있는 것만 보인다. 서버 렌더만 본다(브라우저 스크립트는 돌리지 않는다).
"""

import re

import pytest
from fastapi.testclient import TestClient

from workflow.adapters import repo
from workflow.server import views, web

from .conftest import ADMIN_EMAIL, log_in, log_in_member
from .test_task_cycle import (  # noqa: F401 — 픽스처
    FIX,
    NOW,
    REVIEW,
    SESSION,
    _approved,
    clock,
    config,
    cycle,
    executions,
    fail_fix,
    import_issue,
    make_worker,
    pr_github,
    pr_worker,
    settings,
    worker,
)


@pytest.fixture
def all_open(conn, cycle):
    """지시 전 이슈가 생기는 all_open 소스 — 가져와도 실행이 시작되지 않는다."""
    repo.save_github_source(conn, SESSION, config(review_agent_id=REVIEW, intake="all_open", label_filter=[],
                                                  trigger_label="runloom"), NOW)


@pytest.fixture
def issue_task(conn, all_open) -> str:
    return import_issue(conn, 1, labels=[])


@pytest.fixture
def admin(client, cycle) -> TestClient:
    return log_in(client)


def panel(client, key: str = "RUN-1", query: str = "") -> str:
    response = client.get(f"/work/{key}/panel{query}")
    assert response.status_code == 200, response.text
    return response.text


def section(text: str, name: str) -> str | None:
    found = re.search(rf'data-panel-section="{name}".*?</section>', text, re.S)
    return found.group(0) if found else None


def work_id(conn, task_id: str) -> str:
    return repo.work_item_of_task(conn, task_id)["work_item_id"]


def admin_id(conn) -> str:
    return repo.find_member_by_email(conn, SESSION, ADMIN_EMAIL)["member_id"]


# --- 조각 · 머리 · 속성 ------------------------------------------------------------------------


def test_panel_is_a_fragment_with_head_and_properties(admin, conn, issue_task):
    text = panel(admin)
    assert "<html" not in text and 'class="sidebar' not in text  # base.html 없이
    assert 'data-work-panel="RUN-1"' in text
    assert 'href="https://github.com/acme/billing/issues/1"' in text  # 원본 링크
    assert 'data-status="새로 들어옴"' in text or 'data-status="대기"' in text
    assert re.search(r'<a[^>]*data-panel-close[^>]*href="/tasks"', text)  # 닫기 = open 을 뺀 목록 주소

    props = section(text, "props")
    assert 'action="/work/RUN-1/assignee"' in props and 'action="/work/RUN-1/priority"' in props
    assert f'value="member:{admin_id(conn)}"' in props and 'value="none"' in props
    assert "버그 수정" in props  # 종류 라벨


def test_assignee_candidates_are_only_agents_that_can_take_the_open_stage(admin, conn, issue_task):
    props = section(panel(admin), "props")
    assert f'value="agent:{FIX}"' in props
    assert f'value="agent:{REVIEW}"' not in props and 'value="agent:agent-fix-shop"' not in props


def test_disabled_members_are_not_assignee_choices(app, admin, conn, issue_task):
    other = repo.add_member(conn, SESSION, display_name="떠난사람", now=NOW)
    repo.disable_member(conn, SESSION, other, now=NOW)
    assert f'value="member:{other}"' not in section(panel(admin), "props")


def test_forms_carry_the_list_state_and_close_goes_back_to_it(admin, issue_task):
    text = panel(admin, query="?q=unassigned&view=board&group=nope")
    assert re.search(r'<a[^>]*data-panel-close[^>]*href="/tasks\?q=unassigned&amp;view=board"', text)
    props = section(text, "props")
    assert 'name="q" value="unassigned"' in props and 'name="view" value="board"' in props
    assert 'name="group"' not in props  # 기본값(모르는 값)은 싣지 않는다


def test_sections_without_content_are_hidden(admin, issue_task):
    text = panel(admin)
    for name in ("now", "left", "links", "origin", "form"):
        assert section(text, name) is None, name
    assert "data-request-id" not in text
    assert section(text, "timeline") is not None and section(text, "detail") is not None


# --- 지금 할 일 · 진행 타임라인 -----------------------------------------------------------------


def test_open_request_shows_the_response_form_and_failure_offers_retry_and_close(admin, conn, store, cycle, worker):
    fix_task, _ = fail_fix(conn, store, worker)
    (request,) = [r for r in repo.list_human_requests(conn, fix_task) if r["state"] == "open"]
    now = section(panel(admin), "now")
    assert request["question"] in now
    block = now.split(f'data-request-id="{request["request_id"]}"', 1)[1].split("</form>", 1)[0]
    assert re.search(r'value="retry">다시 맡기기<', block) and re.search(r'value="close">닫기<', block)
    assert 'data-json-action="/human-requests/' in now


def test_timeline_lists_stages_with_kind_status_and_attempts_then_responses(admin, conn, store, cycle, worker):
    fix_task, _ = fail_fix(conn, store, worker)
    (request,) = [r for r in repo.list_human_requests(conn, fix_task) if r["state"] == "open"]
    body = {"response_id": "resp-panel-1", "expected_revision": request["revision"], "action": "retry", "text": ""}
    assert admin.post(f"/human-requests/{request['request_id']}/responses", json=body).status_code == 200

    timeline = section(panel(admin), "timeline")
    stages = timeline.split("data-stages", 1)[1].split("</ol>", 1)[0]
    assert stages.count('href="/tasks/') == 2 and f'href="/tasks/{fix_task}"' in stages
    assert "버그 수정" in stages and "실행 1회" in stages
    assert re.search(r"data-responder[^>]*>\s*관리자", timeline)  # 응답 기록과 응답자


def test_member_without_respond_sees_a_note_instead_of_the_form(conn, store, cycle, worker, settings):
    fix_task, _ = fail_fix(conn, store, worker)
    context = views.work_panel_context(conn, SESSION, work_id(conn, fix_task), member_id="mem-x",
                                       allowed=frozenset(), now=NOW, settings=settings)
    assert [r["response_id"] for r in context["open_requests"]] == [None]
    assert context["can_edit"] is False  # delegate 없으면 담당·우선순위 폼도 없다


def test_panel_without_respond_renders_the_note(conn, store, cycle, worker, settings):
    fix_task, _ = fail_fix(conn, store, worker)
    context = views.work_panel_context(conn, SESSION, work_id(conn, fix_task), member_id="mem-x",
                                       allowed=frozenset(), now=NOW, settings=settings)
    text = web._render("_work_panel.html", panel={**context, "close_href": "/tasks", "list_state": []}, now=NOW)
    now = section(text, "now")
    assert "응답 권한이 없습니다" in now and "data-request-id" not in now
    assert 'action="/work/RUN-1/assignee"' not in text


# --- PR · 원본에 남긴 것 ---------------------------------------------------------------------------


def test_pr_is_linked_in_properties_timeline_and_left_on_source(admin, conn, store, cycle, pr_worker, pr_github, clock):
    _approved(conn, store, pr_worker)
    text = panel(admin)
    assert 'href="https://github.com/acme/billing/pull/31"' in section(text, "props")
    assert 'data-pull-request="open"' in section(text, "timeline")
    assert 'href="https://github.com/acme/billing/pull/31"' in section(text, "left")


def test_delivered_comment_is_left_on_source(admin, conn, cycle, worker):
    task_id = import_issue(conn, 1)
    worker.tick()
    delivery, _ = repo.enqueue_source_delivery_once(conn, task_id, "<!-- runloom:task=x -->\n본문", NOW)
    fence = repo.claim_source_delivery(conn, delivery.delivery_id, expected_state="pending", now=NOW,
                                       claim_until="2026-10-06T12:02:00Z", comment_id=None, for_send=True)
    repo.record_source_delivery(conn, delivery.delivery_id, attempts=fence, state="delivered", comment_id=555,
                                next_at=None, last_error=None, now=NOW)
    conn.commit()
    left = section(panel(admin), "left")
    assert 'href="https://github.com/acme/billing/issues/1#issuecomment-555"' in left
    assert "반영됨" in left


# --- 이어서 생긴 업무 · 들어온 곳 · 양식 -------------------------------------------------------------


def test_linked_work_chain_and_form_fields(admin, conn, issue_task):
    first = work_id(conn, issue_task)
    conn.execute("BEGIN IMMEDIATE")
    second, _ = repo.create_work_item(conn, SESSION, title="이어진 업무", request="-", kind="bug_fix",
                                      source_type="manual", form={"goal": {"value": "합계 맞추기", "source": "manual"}},
                                      now=NOW)
    repo.link_work_items(conn, from_work_item_id=first, to_work_item_id=second, type="spawned_from", now=NOW)
    conn.commit()
    repo.insert_chain(conn, {"chain_id": "chain-p", "session_id": SESSION, "source": "n8n", "title": "들어온 흐름",
                             "callback_url": None, "items": []}, NOW)
    conn.execute("UPDATE tasks SET chain_id = 'chain-p' WHERE task_id = ?", (issue_task,))
    conn.commit()

    text = panel(admin)
    links = section(text, "links")
    assert "이어서 생긴 업무" in links and 'href="/tasks?open=RUN-2"' in links and "이어진 업무" in links
    origin = section(text, "origin")
    assert 'href="/chains/chain-p"' in origin and "들어온 흐름" in origin

    second_text = panel(admin, "RUN-2")
    assert "원인 업무" in section(second_text, "links")
    form = section(second_text, "form")
    assert "목표" in form and "합계 맞추기" in form


# --- 주소 · 권한 · 이스케이프 ------------------------------------------------------------------------


def test_open_query_renders_the_panel_with_the_list(admin, issue_task):
    text = admin.get("/tasks?open=RUN-1").text
    assert 'data-work-panel="RUN-1"' in text and 'class="work-table"' in text
    assert "업무를 찾을 수 없습니다" not in text
    assert 'data-work-panel' not in admin.get("/tasks").text


def test_unknown_open_key_shows_the_list_and_a_short_note(admin, issue_task):
    text = admin.get("/tasks?open=RUN-99").text
    assert "data-work-panel" not in text and "RUN-99 업무를 찾을 수 없습니다." in text
    assert 'data-work-key="RUN-1"' in text


def test_work_address_redirects_to_the_open_panel(admin, issue_task):
    response = admin.get("/work/RUN-1", follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == "/tasks?open=RUN-1"
    assert admin.get("/work/task-gh-1", follow_redirects=False).status_code == 404
    assert admin.get("/work/RUN-0", follow_redirects=False).status_code == 404


def test_panel_of_unknown_or_foreign_key_is_404(app, admin, conn, issue_task):
    assert admin.get("/work/RUN-99/panel").status_code == 404
    assert admin.get("/work/task-gh-1/panel").status_code == 404
    repo.create_session(conn, "sess-other", NOW)
    conn.execute("BEGIN IMMEDIATE")
    repo.create_work_item(conn, "sess-other", title="남의 업무", request="-", kind="bug_fix", source_type="manual",
                          now=NOW)
    conn.execute("UPDATE work_items SET key_number = 7 WHERE session_id = 'sess-other'")
    conn.commit()
    response = admin.get("/work/RUN-7/panel")
    assert response.status_code == 404 and "남의 업무" not in response.text
    assert "남의 업무" not in admin.get("/tasks?open=RUN-7").text


def test_login_is_required(client, cycle, issue_task):
    response = client.get("/work/RUN-1/panel", follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == "/login"


def test_member_sees_the_same_panel(app, admin, issue_task):
    member = log_in_member(TestClient(app))
    assert 'action="/work/RUN-1/assignee"' in panel(member)  # delegate 는 멤버도


def test_external_strings_are_escaped(admin, conn, issue_task):
    conn.execute("UPDATE work_items SET title = '<script>alert(1)</script>', request = '<b>본문</b>'")
    conn.commit()
    text = panel(admin)
    assert "<script>alert(1)</script>" not in text and "&lt;script&gt;alert(1)&lt;/script&gt;" in text
    assert "<b>본문</b>" not in text
