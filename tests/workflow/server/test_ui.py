"""Step 7 화면 — UI_GUIDE 를 테스트로 고정한다. 실제 페이지를 렌더해 셸·배지·결과 카드·뷰어·라이브 조각·금지 사항을 본다.

셀프호스트(ADR-0019) — 로그인한 고정 워크스페이스, 러너 모양 Agent, 종류 `bug_fix`·`code_review`·사용자 정의 `review`,
체인 상태 판정은 사용자 정의 `triage` → `patch`(test_views 와 같은 시드)."""

import html as html_lib
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from workflow.adapters import repo
from workflow.contracts.v1 import ArtifactMeta, ExecutionRequest, SelectionRecord
from workflow.server.auth import SESSION_COOKIE, ensure_workspace, utc_now, verify_session

from .conftest import (
    LOCAL_REGISTRATION,
    NOW,
    SESSION,
    code_change_result,
    meta_for,
    seed_agents,
    seed_execution,
    seed_result_ready,
    task_row,
)
from .test_views import API_AGENT, CAP_PATCH, CAP_TRIAGE, PATCH_AGENT, seed_user_kinds, user_task
from .test_web import (
    BUG_FIX_TITLE,
    LOCAL_REVIEW,
    REVIEW_AGENT,
    code_review_form,
    create_task,
    fix_form,
    register_kind,
    review_form,
    seed_review_agent,
)

SERVER_DIR = Path(__file__).resolve().parents[3] / "src" / "workflow" / "server"
STYLE = SERVER_DIR / "static" / "style.css"
TEMPLATES = sorted((SERVER_DIR / "templates").glob("*.html"))

# UI_GUIDE "하지 마라" — 보라 계열과 AI 템플릿 징후
FORBIDDEN_CSS = ("backdrop-filter", "gradient", "#7c3aed", "#6366f1", "#8b5cf6", "indigo", "violet", "purple")
ROOT_VARIABLES = (
    "--bg-page", "--bg-side", "--bg-bubble", "--border", "--text-muted", "--accent",
    "--state-wait", "--state-ready", "--state-inflight", "--state-attention", "--state-done", "--state-failed",
    "--bg-attention", "--bg-done", "--bg-failed", "--mono", "--sans",
)

DIFF = """diff --git a/report/transformer.py b/report/transformer.py
--- a/report/transformer.py
+++ b/report/transformer.py
@@ -1,4 +1,6 @@
 def rows(payload):
-    return payload["items"]
+    if "items" in payload:
+        return payload["items"]
+    return payload["data"]["records"]
diff --git a/tests/test_transformer.py b/tests/test_transformer.py
--- a/tests/test_transformer.py
+++ b/tests/test_transformer.py
@@ -0,0 +1,3 @@
+def test_records_path():
+    assert rows({"data": {"records": []}}) == []
+
"""


@pytest.fixture
def agents(conn):
    """고정 워크스페이스 + 러너 모양 Agent 1개 (test_web 과 같다)."""
    ensure_workspace(conn, NOW)
    seed_agents(conn)
    return conn


@pytest.fixture
def web(logged_in_client, agents):
    """워크스페이스에 로그인한 클라이언트 (test_web 과 같다)."""
    return logged_in_client


def session_id_of(client: TestClient, settings) -> str:
    return verify_session(client.cookies[SESSION_COOKIE], settings.session_secret)


def visible_text(html: str) -> str:
    """태그·스크립트를 벗긴 가시 텍스트 (공백 정규화, 엔티티 복원). 문구 검사용."""
    html = re.sub(r"<script.*?</script>", " ", html, flags=re.DOTALL)
    html = re.sub(r"<style.*?</style>", " ", html, flags=re.DOTALL)
    html = re.sub(r"<pre[^>]*\bhidden\b[^>]*>.*?</pre>", " ", html, flags=re.DOTALL)  # 원문 토글 전에는 안 보인다
    return html_lib.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))).strip()


def viewer_of(html: str) -> str:
    return html[html.index('class="viewer'):html.index("<script>")]


def select(conn, task_id: str, agent_id: str, capability: dict) -> None:
    repo.save_selection(conn, SelectionRecord.model_validate({
        "task_id": task_id, "mode": "auto", "required_capability": capability, "candidate_count": 1,
        "selected_agent_id": agent_id, "matched": capability, "status": "selected",
        "reason": f"{capability['code']} 일치 후보 1개",
    }))


def seed_code_change_result(client, conn, store, settings) -> tuple[str, str]:
    """`bug_fix` 업무를 등록하고 CONTRACT 7절 결과와 diff·테스트 로그 산출물을 넣는다."""
    task_id = create_task(client, fix_form())
    session_id = session_id_of(client, settings)
    execution_id = "exec-fix-001"
    seed_execution(conn, execution_id, task_id)
    for kind, name, text in (
        ("diff", "change.diff", DIFF),
        ("test_log_before", "pytest-before.txt", "\n".join(f"before line {n}" for n in range(1, 31)) + "\nFAILED 1\n"),
        ("test_log_after", "pytest-after.txt", "collected 3 items\n3 passed\n"),
    ):
        data = text.encode()
        repo.store_artifact(
            conn, store, execution_id=execution_id, session_id=session_id,
            meta=ArtifactMeta.model_validate(meta_for(data, kind=kind, name=name)), data=data, now=NOW,
        )
    seed_result_ready(
        conn, store, execution_id, kind="code_change_result",
        body=code_change_result(execution_id, task_id), session_id=session_id,
    )
    return task_id, execution_id


def status_line(html: str) -> str:
    match = re.search(r'<[^>]+class="[^"]*status-line[^"]*"[^>]*>(.*?)</(?:div|p)>', html, re.DOTALL)
    assert match, "status-line 요소 없음"
    return match.group(1)


# --- 셸 -------------------------------------------------------------------------


def test_pages_render_three_column_shell(web, conn, store, settings):
    task_id, _ = seed_code_change_result(web, conn, store, settings)
    chain_id = seed_n8n_chain(conn, session_id_of(web, settings), callback_url=None)
    for path in (
        "/tasks", f"/tasks/{task_id}", "/agents", "/agents/agent-codex-mac", "/operator",
        "/tasks/new", "/sources", f"/chains/{chain_id}", "/kinds",
    ):
        html = web.get(path).text
        assert 'class="shell' in html, path
        shell = html[html.index('class="shell'):]
        for column in ('class="sidebar', 'class="main', 'class="viewer'):
            assert column in shell, (path, column)
        assert '<link rel="stylesheet" href="/static/style.css">' in html


def test_static_stylesheet_is_served(client):
    response = client.get("/static/style.css")
    assert response.status_code == 200
    assert ":root" in response.text


def test_sidebar_lists_my_work_with_status_dot_and_relative_time(web):
    task_id = create_task(web, fix_form())
    html = web.get("/tasks").text
    sidebar = html[html.index('class="sidebar'):html.index('class="main')]
    assert 'href="/work/RUN-1"' in sidebar and f'href="/tasks/{task_id}"' not in sidebar  # 한 줄 = 업무
    assert BUG_FIX_TITLE in sidebar
    assert 'data-status="새로 들어옴"' in sidebar  # 업무 상태
    assert "전" in sidebar  # 상대 시각 "n분 전"
    assert 'href="/tasks/new"' in sidebar  # `+` 는 직접 등록
    assert 'href="/tasks/import"' not in sidebar and "/tasks/new?example=diagnose" not in sidebar
    assert 'href="/operator"' in sidebar  # 워크스페이스 로그인 = 운영자


def test_sidebar_links_kinds_page_after_agents_and_marks_active(web):
    """종류·규칙 링크는 에이전트 다음. 활성 표시 규칙은 다른 탐색 항목과 같다 (phase 6 step 6)."""
    html = web.get("/tasks").text
    nav = html[html.index('class="nav"'):html.index('class="side-head"')]
    assert '<a href="/kinds">종류·규칙</a>' in nav
    assert nav.index('href="/agents"') < nav.index('href="/kinds"')
    kinds = web.get("/kinds").text
    nav = kinds[kinds.index('class="nav"'):kinds.index('class="side-head"')]
    assert '<a href="/kinds" class="active">종류·규칙</a>' in nav
    assert 'href="/agents" class="active"' not in nav and 'href="/tasks" class="active"' not in nav


# --- 상태 배지 ----------------------------------------------------------------------


def test_detail_status_line_has_label_and_reason_together(web, conn):
    """상태 줄은 라벨과 이유를 함께 보인다 — 등록한 `bug_fix` 는 러너 보고 전이라 `대기 · 연결 끊김`."""
    task_id = create_task(web, fix_form())
    html = web.get(f"/tasks/{task_id}").text
    line = status_line(html)
    assert 'data-status="대기"' in line
    assert "대기" in visible_text(line)
    assert "연결 끊김, 마지막 확인 없음" in visible_text(line)
    assert f'action="/tasks/{task_id}/run"' not in html


def test_badge_dot_fill_follows_ui_guide(web, conn, store, settings):
    """빈 점: 대기·실행 가능·실행 요청됨. 채운 점: 실행 중·확인 필요·완료·실패."""
    task_a = create_task(web, fix_form())
    task_b = create_task(web, code_review_form(task_a))
    waiting = status_line(web.get(f"/tasks/{task_b}").text)
    assert 'data-status="대기"' in waiting and "dot-filled" not in waiting

    task_c, _ = seed_generic_result(web, conn, store, settings)  # 종류 review·검토 Claude 등록
    repo.update_registration(
        conn, LOCAL_REVIEW, connector_id="conn-mac-01", repository_id="demo-report-repo",
        base_commit=None, verification_profile_ids=[], discovered={}, now=utc_now(),
    )
    task_r = create_task(web, review_form())
    web.post(f"/tasks/{task_r}/run", follow_redirects=False)
    requested = status_line(web.get(f"/tasks/{task_r}").text)
    assert 'data-status="실행 요청됨"' in requested and "dot-filled" not in requested

    attention = status_line(web.get(f"/tasks/{task_c}").text)
    assert 'data-status="확인 필요"' in attention and "dot-filled" in attention
    web.post(f"/tasks/{task_c}/review", data={"decision": "approve"}, follow_redirects=False)
    done = status_line(web.get(f"/tasks/{task_c}").text)
    assert 'data-status="완료"' in done and "dot-filled" in done
    assert "검토 승인" in visible_text(done)


def seed_user_chain(conn) -> tuple[str, tuple[str, str]]:
    """사용자 정의 triage(분류 API 선택) → patch(패치 Codex 선택) 체인. 상태는 선택·선행으로 실시간 판정된다."""
    seed_user_kinds(conn)
    chain_id = "chain-user"
    repo.insert_chain(conn, {"chain_id": chain_id, "session_id": SESSION, "source": "github",
                             "title": "일일 보고서 실패 분류 → 보고서 변환 패치"}, NOW)
    repo.insert_work_item_task(conn, {**user_task("task-c41", "triage"), "chain_id": chain_id, "source_ref": "#41"}, NOW)
    repo.insert_work_item_task(conn, {**user_task("task-c42", "patch", predecessor="task-c41"),
                            "chain_id": chain_id, "source_ref": "#42"}, NOW)
    select(conn, "task-c41", API_AGENT, CAP_TRIAGE)
    select(conn, "task-c42", PATCH_AGENT, CAP_PATCH)
    return chain_id, ("task-c41", "task-c42")


def test_chain_nodes_show_status_as_badge_text_with_reason(web, conn):
    """워크플로우 노드는 색만이 아니라 배지 텍스트 + 한글 이유로 상태를 보인다 (UI_GUIDE "하지 마라")."""
    chain_id, _ = seed_user_chain(conn)
    html = web.get(f"/chains/{chain_id}").text
    nodes = re.findall(r'<li class="chain-node"[^>]*>(.*?)</li>', html, re.DOTALL)
    assert len(nodes) == 2
    for node, label, reason in ((nodes[0], "실행 가능", f"{API_AGENT} 선택됨"), (nodes[1], "대기", "선행 대기")):
        line = status_line(node)
        assert f'data-status="{label}"' in line
        assert label in visible_text(line) and reason in visible_text(line)
    assert "dot-filled" not in status_line(nodes[0])  # 실행 가능은 빈 점
    human = html[html.index('class="chain-node chain-node-human"'):]
    assert "검토 승인 (사람) · 병합은 운영자 확인" in visible_text(human)
    assert 'data-status="대기"' in status_line(human)
    for label in ("분류", "패치", "직접", "선행 완료 시 자동", "검토 후 완료"):
        assert label in visible_text(html), label


def test_api_agent_card_shows_connected_without_last_seen(web, conn):
    """API 에이전트는 heartbeat 가 없어도 '연결됨' 이고 '마지막 확인' 을 보이지 않는다. 로컬은 heartbeat 규칙."""
    seed_user_kinds(conn)  # 분류 API(API)를 워크스페이스에 붙인다
    cards = re.findall(r'<div class="card agent-card">(.*?)</div>\s*</div>', web.get("/tasks").text, re.S)
    by_id = {re.search(r"agent-[a-z-]+", c).group(0): c for c in cards}
    ops, codex = by_id[API_AGENT], by_id["agent-codex-mac"]
    assert 'data-status="연결됨"' in ops and "마지막 확인" not in ops
    assert 'data-status="연결 끊김"' in codex and "마지막 확인 없음" in codex


# --- 결과 카드·뷰어 — 수정 결과(`bug_fix`) -------------------------------------------------


def test_code_change_result_card_and_verification_summary(web, conn, store, settings):
    task_id, _ = seed_code_change_result(web, conn, store, settings)
    html = web.get(f"/tasks/{task_id}").text
    text = visible_text(html)
    card = visible_text(html[html.index('class="result-card'):html.index('class="viewer')])
    assert "검토 가능" in card
    assert "demo-report-repo" in card
    assert "9b7e4d2 ← 3f9c2e1 · 2 files · +6 -1 · vp-pytest exit 0" in card

    viewer = visible_text(viewer_of(html))
    assert "수정 전 테스트" in viewer and "수정 후 테스트" in viewer
    assert "before line 12" in viewer and "before line 11" not in viewer  # 마지막 20줄
    assert "FAILED 1" in viewer and "3 passed" in viewer
    assert "생성된 보고서" not in viewer and "진단 결과" not in viewer  # 진단·보고서 데모 뷰어 절 없음
    assert 'class="diff-add"' in html and 'class="diff-del"' in html
    # `bug_fix` 결과 뒤는 사람 검토 폼이 아니라 후속 규칙의 `code_review` 가 잇는다 (업무 순환)
    assert f'action="/tasks/{task_id}/review"' not in html and 'value="request_changes"' not in html
    assert "수정 결과" in text


# --- 결과 카드·뷰어 — 사용자 정의 종류(결과 봉투) · 종류 라벨 · 3노드 체인 (phase 6 step 7) -----------


def seed_generic_result(client, conn, store, settings) -> tuple[str, str]:
    """종류 review 를 등록하고 검토 Claude 로 선행 없는 검토 업무를 만든 뒤 CONTRACT 11.3 결과와 원시 로그를 넣는다."""
    seed_review_agent(conn)  # 워크스페이스에 붙는다
    register_kind(client)
    task_id = create_task(client, review_form())
    session_id = session_id_of(client, settings)
    execution_id = "exec-review-001"
    spec = repo.get_kind(conn, session_id, "review")
    repo.create_execution(
        conn, execution_id=execution_id, task_id=task_id, attempt_no=1, start_key=f"auto:{task_id}:r1",
        agent_id=REVIEW_AGENT, kind="review",
        request=ExecutionRequest.model_validate({
            "contract_version": 1, "execution_id": execution_id, "task_id": task_id, "kind": "review",
            "agent_id": REVIEW_AGENT, "task_revision": 1, "request": "검토", "input_artifact_ids": ["art-handoff-002"],
            "target": {"local_registration_id": LOCAL_REVIEW}, "kind_spec": spec.model_dump(),
        }),
        assigned_connector_id=None, predecessor_execution_id=None, now=NOW,
    )
    data = b'{"type":"result","subtype":"success"}\n'
    log, _ = repo.store_artifact(
        conn, store, execution_id=execution_id, session_id=session_id,
        meta=ArtifactMeta.model_validate(meta_for(data, kind="claude_jsonl", name="claude.jsonl")), data=data, now=NOW,
    )
    seed_result_ready(
        conn, store, execution_id, kind="generic_result",
        body={
            "contract_version": 1, "execution_id": execution_id, "task_id": task_id, "kind": "review",
            "outcome": "approved", "summary": "diff 는 최소 변경이며 재현 테스트가 무력화되지 않았습니다.",
            "artifact_ids": [log.artifact_id],
        },
        session_id=session_id,
    )
    return task_id, execution_id


def test_generic_result_card_shows_outcome_code_summary_and_raw_log_chip(web, conn, store, settings):
    task_id, _ = seed_generic_result(web, conn, store, settings)
    html = web.get(f"/tasks/{task_id}").text
    card_html = html[html.index('class="result-card'):html.index('class="viewer')]
    card = visible_text(card_html)
    assert 'data-outcome="approved"' in card_html and "approved" in card  # OUTCOME_LABELS 에 없는 outcome 은 코드 그대로
    assert "demo-report-repo" in card and task_id in card
    assert "diff 는 최소 변경이며 재현 테스트가 무력화되지 않았습니다." in card
    assert "결과 봉투" in card and "커밋 없음" not in card and "←" not in card
    chips = html[html.index('class="result-card'):html.index('class="status-line')]
    assert "Claude JSONL" in chips and "결과 봉투" in chips  # 원시 로그 · 결과 봉투 칩

    viewer = visible_text(viewer_of(html))
    assert "결과 봉투" in viewer and "approved" in viewer and "검증 요약" not in viewer and "수정 전 테스트" not in viewer
    assert "diff 는 최소 변경이며" in viewer
    line = status_line(html)
    assert 'data-status="확인 필요"' in line and "검토 대기" in visible_text(line)
    assert 'value="approve"' in html

    web.post(f"/tasks/{task_id}/review", data={"decision": "approve"}, follow_redirects=False)
    text = visible_text(web.get(f"/tasks/{task_id}").text)
    assert "검토 승인" in text and "병합" not in text  # 사용자 정의 종류에는 병합 단계가 없다


def test_task_detail_shows_kind_label_from_registry(web, conn, store, settings):
    task_a = create_task(web, fix_form())
    crumb = web.get(f"/tasks/{task_a}").text
    crumb = crumb[crumb.index('class="crumbs"'):crumb.index('class="bubble"')]
    assert 'class="chip kind-chip">버그 수정<' in crumb
    task_b = create_task(web, code_review_form(task_a))
    crumb = web.get(f"/tasks/{task_b}").text
    assert 'class="chip kind-chip">커밋 검토<' in crumb[crumb.index('class="crumbs"'):crumb.index('class="bubble"')]
    task_c, _ = seed_generic_result(web, conn, store, settings)
    crumb = web.get(f"/tasks/{task_c}").text
    assert 'class="chip kind-chip">검토<' in crumb[crumb.index('class="crumbs"'):crumb.index('class="bubble"')]


def test_chain_renders_three_nodes_in_order_with_kind_labels(web, conn, settings):
    """체인 템플릿이 노드 2개를 가정하지 않는다 — bug_fix → code_review 체인 뒤에 등록부의 사용자 정의 종류
    review Task 를 하나 더 붙여도 노드를 순서대로 그린다."""
    register_kind(web)
    chain_id = "chain-three"
    session_id = session_id_of(web, settings)
    repo.insert_chain(conn, {"chain_id": chain_id, "session_id": session_id, "source": "github",
                             "title": "보고서 변환 수정 → 보고서 수정 검토"}, NOW)
    repo.insert_work_item_task(conn, {**task_row("task-c41"), "chain_id": chain_id, "source_ref": "#41"}, NOW)
    repo.insert_work_item_task(conn, {**task_row("task-c42", kind="code_review", predecessor="task-c41"),
                            "chain_id": chain_id, "source_ref": "#42"}, NOW)
    repo.insert_work_item_task(conn, {
        "task_id": "task-c45", "session_id": session_id, "title": "보고서 수정 검토", "request": "검토",
        "kind": "review", "required_capability": {"code": "review", "scope": {"repository_id": "demo-report-repo"}},
        "selection_mode": "auto", "chosen_agent_id": None, "run_mode": "auto", "completion_mode": "review",
        "criteria": [], "predecessor_task_id": "task-c42", "revision": 1,
        "target": {"local_registration_id": LOCAL_REVIEW}, "status": "대기", "status_reason": "선행 대기",
        "chain_id": chain_id, "source_ref": "#45",
    }, NOW)
    html = web.get(f"/chains/{chain_id}").text
    nodes = re.findall(r'<li class="chain-node"[^>]*>(.*?)</li>', html, re.DOTALL)
    assert len(nodes) == 3
    assert [re.search(r'class="node-no">(\d)<', n).group(1) for n in nodes] == ["1", "2", "3"]
    labels = [re.search(r'<span class="chip">([^<]+)</span>', n).group(1) for n in nodes]
    assert labels == ["버그 수정", "커밋 검토", "검토"]
    assert "#45" in nodes[2] and "후보 없음" in visible_text(nodes[2])
    human = html[html.index('class="chain-node chain-node-human"'):]
    assert 'class="node-no">4<' in human and "검토 승인 (사람) · 병합은 운영자 확인" in visible_text(human)


# --- 라이브 조각 -----------------------------------------------------------------


def test_live_fragment_is_partial_and_session_scoped(app, web, conn, store, settings):
    task_id, _ = seed_generic_result(web, conn, store, settings)
    response = web.get(f"/tasks/{task_id}/live")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert "<html" not in response.text and "<script" not in response.text
    assert "data-live" in response.text
    assert 'data-status="확인 필요"' in response.text
    assert 'class="result-card' in response.text
    assert f'action="/tasks/{task_id}/review"' in response.text
    assert f'data-live="/tasks/{task_id}/live"' in web.get(f"/tasks/{task_id}").text

    other = TestClient(app)  # 로그인하지 않은 브라우저
    denied = other.get(f"/tasks/{task_id}/live", follow_redirects=False)
    assert denied.status_code == 303 and denied.headers["location"] == "/login"
    assert web.get("/tasks/nope/live").status_code == 404


def test_live_fragment_shows_successor_chip(web):
    task_a = create_task(web, fix_form())
    task_b = create_task(web, code_review_form(task_a))
    fragment = web.get(f"/tasks/{task_a}/live").text
    # 폼 선행으로 이은 Task 는 다른 업무 — 칩은 그 업무 키로 업무 상세에
    assert 'href="/work/RUN-2"' in fragment and "후속 RUN-2" in fragment
    assert 'href="/work/RUN-1"' in fragment  # 브레드크럼의 자기 업무
    back = web.get(f"/tasks/{task_b}/live").text
    assert 'href="/work/RUN-1"' in back and "선행 RUN-1" in back
    assert 'data-status="대기"' in fragment


# --- 금지 사항 ------------------------------------------------------------------


def test_visible_text_has_no_forbidden_phrases(web, conn, store, settings):
    task_id, _ = seed_code_change_result(web, conn, store, settings)
    chain_id = seed_n8n_chain(conn, session_id_of(web, settings), callback_url=None)
    for path in (
        "/tasks", f"/tasks/{task_id}", "/tasks/new", "/sources", "/agents",
        "/agents/agent-codex-mac", "/operator", f"/chains/{chain_id}", "/kinds",
    ):
        text = visible_text(web.get(path).text)
        for phrase in ("대기 중", "Powered by"):
            assert phrase not in text, (path, phrase)


def test_stylesheet_follows_ui_guide():
    css = STYLE.read_text(encoding="utf-8")
    lowered = css.lower()
    for forbidden in FORBIDDEN_CSS:
        assert forbidden not in lowered, forbidden
    assert "box-shadow" not in lowered
    assert "http://" not in lowered and "https://" not in lowered
    root = css[css.index(":root"):css.index("}", css.index(":root"))]
    for variable in ROOT_VARIABLES:
        assert f"{variable}:" in root, variable
    assert "prefers-reduced-motion" in css
    assert "max-width: 1199px" in css or "max-width: 1200px" in css
    assert "max-width: 799px" in css or "max-width: 800px" in css


def test_templates_have_no_external_assets():
    assert TEMPLATES, "템플릿 없음"
    for path in TEMPLATES:
        source = path.read_text(encoding="utf-8")
        assert '<script src="http' not in source, path.name
        assert '<link href="http' not in source, path.name
        assert 'href="http' not in source and 'src="http' not in source, path.name
        assert "Powered by" not in source, path.name
    base = (SERVER_DIR / "templates" / "base.html").read_text(encoding="utf-8")
    assert "<script>" in base and "data-live" in base and "3000" in base and "10000" in base
    assert "갱신 실패, 재시도 중" in base and "마지막 갱신" in base


# --- n8n 입구 화면·체인 화면의 출처 n8n + callback 한 줄 (phase 7 step 6, ADR-0010) -------------------------


N8N_ITEM = {"key": "issue-1", "title": "입력 형식 변경에 맞춰 변환 수정", "body": "재현 테스트를 먼저 추가하고 고쳐 주세요.",
            "labels": ["kind:bug_fix", "repository_id:demo-report-repo"], "blocked_by": []}


def seed_n8n_chain(conn, session_id: str, *, callback_url: str | None) -> str:
    """입구 API 가 만든 것과 같은 모양의 n8n 체인 하나 (`bug_fix` Task 1개, source_ref 는 항목 key)."""
    chain_id = "chain-n8n-ui"
    repo.insert_chain(conn, {
        "chain_id": chain_id, "session_id": session_id, "source": "n8n",
        "title": N8N_ITEM["title"], "callback_url": callback_url, "items": [N8N_ITEM],
    }, NOW)
    repo.insert_work_item_task(conn, {
        "task_id": "task-n8n-ui", "session_id": session_id, "title": N8N_ITEM["title"],
        "request": N8N_ITEM["body"], "kind": "bug_fix",
        "required_capability": {"code": "code.fix", "scope": {"repository_id": "demo-report-repo"}},
        "selection_mode": "auto", "chosen_agent_id": None, "run_mode": "manual", "completion_mode": "review",
        "criteria": [], "predecessor_task_id": None, "revision": 1,
        "target": {"local_registration_id": LOCAL_REGISTRATION},
        "status": "대기", "status_reason": "연결 끊김, 마지막 확인 없음", "chain_id": chain_id, "source_ref": N8N_ITEM["key"],
    }, NOW)
    return chain_id


def crumbs_of(html: str) -> str:
    return html[html.index('class="crumbs"'):html.index("data-live=")]


def test_chain_page_shows_n8n_source_and_callback_line(web, conn, settings):
    session_id = session_id_of(web, settings)
    callback_url = "http://localhost:5678/webhook-waiting/1234"
    chain_id = seed_n8n_chain(conn, session_id, callback_url=callback_url)
    html = web.get(f"/chains/{chain_id}").text
    head = crumbs_of(html)
    assert '<span class="chip">n8n</span>' in head
    assert "시연 데이터" not in head  # n8n 항목은 fixture 가 아니다
    assert "callback · localhost:5678 · 대기(사람 차례가 되면 보냄)" in visible_text(head)
    assert "webhook-waiting" not in html and callback_url not in html  # URL 전체·본문은 찍지 않는다
    assert "issue-1" in visible_text(html)  # 노드의 source_ref 는 항목 key
    assert "라벨 kind:bug_fix + repository_id:demo-report-repo → bug_fix" in visible_text(html)

    for _ in range(5):
        repo.record_callback_attempt(conn, chain_id, ok=False, error="HTTP 503", now=NOW, next_at=None)
    failed = visible_text(crumbs_of(web.get(f"/chains/{chain_id}").text))
    assert "callback · localhost:5678 · 실패 5회 · HTTP 503" in failed

    repo.record_callback_attempt(conn, chain_id, ok=True, error=None, now=NOW, next_at=None)
    sent = visible_text(crumbs_of(web.get(f"/chains/{chain_id}").text))
    assert "callback · localhost:5678 · 전송됨" in sent and "HTTP 503" not in sent
    # 홈의 워크플로우 카드도 같은 출처 라벨
    assert "n8n · 0/1 완료" in visible_text(web.get("/tasks").text)


def test_chain_page_without_callback_url_has_no_callback_line(web, conn, settings):
    chain_id = seed_n8n_chain(conn, session_id_of(web, settings), callback_url=None)
    html = web.get(f"/chains/{chain_id}").text
    assert '<span class="chip">n8n</span>' in crumbs_of(html)
    assert "callback" not in visible_text(html) and "시연 데이터" not in visible_text(html)


def test_sources_page_uses_app_shell(web):
    html = web.get("/sources").text
    shell = html[html.index('class="shell'):]
    for column in ('class="sidebar', 'class="main', 'class="viewer'):
        assert column in shell, column
    assert '<link rel="stylesheet" href="/static/style.css">' in html
    assert '<div class="crumb">입구</div>' in html
    nav = html[html.index('class="nav"'):html.index('class="side-head"')]
    assert '<a href="/sources" class="active">입구</a>' in nav
    main = html[html.index('class="main'):html.index('class="viewer')]
    assert "<svg" not in main and '<script src=' not in main
    text = visible_text(html)
    for phrase in ("대기 중", "Powered by", "webhook secret", "API key"):
        assert phrase not in text, phrase
    # 발급 응답도 같은 셸이다
    issued = web.post("/sources/tokens", data={"label": "n8n"}).text
    assert 'class="sidebar' in issued and 'id="issued-token"' in issued
