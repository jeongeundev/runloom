"""Step 7 화면 — UI_GUIDE 를 테스트로 고정한다. 실제 페이지를 렌더해 셸·배지·결과 카드·뷰어·라이브 조각·금지 사항을 본다."""

import hashlib
import html as html_lib
import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.workflow.domain.test_verification import contract_results, demo_sources
from workflow.adapters import repo
from workflow.contracts.v1 import ArtifactMeta, ExecutionRequest
from workflow.server.auth import SESSION_COOKIE, verify_session

from .conftest import BASE_COMMIT, NOW, code_change_result, meta_for, seed_agents, seed_result_ready
from .test_web import create_task, diagnose_form, fix_form, import_chain, login_operator, register_agents

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
    seed_agents(conn)
    return conn


@pytest.fixture
def web(client, agents):
    """세션 쿠키를 받고 카탈로그 2개를 등록한 클라이언트 (test_web 과 같다)."""
    assert client.get("/tasks").status_code == 200
    register_agents(client)
    return client


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


def store_evidence(conn, store, execution_id: str, session_id: str) -> dict[tuple[str, str], tuple[str, str]]:
    """demo_sources() 의 근거 8개를 `evidence` 산출물로 저장한다. (evidence_id, version) → (artifact_id, sha256)."""
    stored = {}
    for (evidence_id, version), (content_type, source) in demo_sources().items():
        data = source.encode() if isinstance(source, str) else json.dumps(source, ensure_ascii=False, indent=2).encode()
        created, _ = repo.store_artifact(
            conn, store, execution_id=execution_id, session_id=session_id,
            meta=ArtifactMeta.model_validate(meta_for(data, kind="evidence", name=f"{evidence_id}.txt",
                                                       content_type=content_type)),
            data=data, now=NOW,
        )
        stored[(evidence_id, version)] = (created.artifact_id, hashlib.sha256(data).hexdigest())
    return stored


def seed_diagnosis_result(client, conn, store, settings, *, needs_information: bool = False) -> tuple[str, str]:
    """진단 업무 A 에 CONTRACT 5절(또는 6절) 결과를 result_ready 로 넣는다. 첨부는 실제 저장한 근거 산출물을 가리킨다."""
    task_id = create_task(client, diagnose_form())
    session_id = session_id_of(client, settings)
    execution_id = "exec-diagnose-001"
    repo.create_execution(
        conn,
        execution_id=execution_id,
        task_id=task_id,
        attempt_no=1,
        start_key=f"auto:{task_id}:r1",
        agent_id="agent-ops-demo",
        kind="diagnosis",
        request=ExecutionRequest.model_validate({
            "contract_version": 1, "execution_id": execution_id, "task_id": task_id, "kind": "diagnosis",
            "agent_id": "agent-ops-demo", "task_revision": 1, "request": "조사", "input_artifact_ids": [],
            "target": {"run_id": "daily-0920-0900"},
        }),
        assigned_connector_id=None,
        predecessor_execution_id=None,
        now=NOW,
    )
    stored = store_evidence(conn, store, execution_id, session_id)
    body = json.loads(json.dumps(contract_results()[1 if needs_information else 0]))
    body["execution_id"], body["task_id"] = execution_id, task_id
    for ref in body["attachments"]:
        ref["artifact_id"], ref["sha256"] = stored[(ref["evidence_id"], ref["version"])]
    seed_result_ready(conn, store, execution_id, kind="diagnosis_result", body=body, session_id=session_id)
    return task_id, execution_id


def seed_code_change_result(client, conn, store, settings) -> tuple[str, str]:
    """A → B 등록 뒤 B 에 CONTRACT 7절 결과와 diff·테스트 로그·보고서 산출물을 넣는다."""
    task_a = create_task(client, diagnose_form())
    task_b = create_task(client, fix_form(task_a))
    session_id = session_id_of(client, settings)
    execution_id = "exec-fix-001"
    repo.create_execution(
        conn,
        execution_id=execution_id,
        task_id=task_b,
        attempt_no=1,
        start_key=f"auto:{task_b}:r1",
        agent_id="agent-codex-mac",
        kind="code_change",
        request=ExecutionRequest.model_validate({
            "contract_version": 1, "execution_id": execution_id, "task_id": task_b, "kind": "code_change",
            "agent_id": "agent-codex-mac", "task_revision": 1, "request": "수정", "input_artifact_ids": ["art-handoff-001"],
            "target": {"local_registration_id": "local-demo-report", "base_commit": BASE_COMMIT,
                       "verification_profile_id": "vp-pytest"},
        }),
        assigned_connector_id=None,
        predecessor_execution_id=None,
        now=NOW,
    )
    for kind, name, text in (
        ("diff", "change.diff", DIFF),
        ("test_log_before", "pytest-before.txt", "\n".join(f"before line {n}" for n in range(1, 31)) + "\nFAILED 1\n"),
        ("test_log_after", "pytest-after.txt", "collected 3 items\n3 passed\n"),
        ("report_output", "report.txt", "일일 업무 보고서 — 2026-09-19\n합계    20    5\n"),
    ):
        data = text.encode()
        repo.store_artifact(
            conn, store, execution_id=execution_id, session_id=session_id,
            meta=ArtifactMeta.model_validate(meta_for(data, kind=kind, name=name)), data=data, now=NOW,
        )
    seed_result_ready(
        conn, store, execution_id, kind="code_change_result",
        body=code_change_result(execution_id, task_b), session_id=session_id,
    )
    return task_b, execution_id


def status_line(html: str) -> str:
    match = re.search(r'<[^>]+class="[^"]*status-line[^"]*"[^>]*>(.*?)</(?:div|p)>', html, re.DOTALL)
    assert match, "status-line 요소 없음"
    return match.group(1)


# --- 셸 -------------------------------------------------------------------------


def test_pages_render_three_column_shell(web, conn, store, settings):
    task_id, _ = seed_diagnosis_result(web, conn, store, settings)
    chain_id, _ = import_chain(web, conn, "#41", "#42")
    login_operator(web)
    for path in (
        "/tasks", f"/tasks/{task_id}", "/agents", "/agents/register", "/agents/agent-ops-demo", "/operator",
        "/tasks/new", "/tasks/import", f"/chains/{chain_id}", "/kinds",
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


def test_sidebar_lists_my_tasks_with_status_dot_and_relative_time(web):
    task_id = create_task(web, diagnose_form())
    html = web.get("/tasks").text
    sidebar = html[html.index('class="sidebar'):html.index('class="main')]
    assert f'href="/tasks/{task_id}"' in sidebar
    assert "일일 보고서 실패 진단" in sidebar
    assert 'data-status="실행 가능"' in sidebar
    assert "전" in sidebar  # 상대 시각 "n분 전"
    assert 'href="/tasks/import"' in sidebar  # `+` 는 업무 가져오기
    assert "/tasks/new?example=diagnose" not in sidebar
    assert "운영자" not in sidebar  # 운영자 쿠키 없음
    login_operator(web)
    assert 'href="/operator"' in web.get("/tasks").text


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


def test_detail_status_line_has_label_and_reason_together(web):
    task_id = create_task(web, diagnose_form())
    html = web.get(f"/tasks/{task_id}").text
    line = status_line(html)
    assert 'data-status="실행 가능"' in line
    assert "실행 가능" in visible_text(line)
    assert "agent-ops-demo 선택됨" in visible_text(line)
    assert f'action="/tasks/{task_id}/run"' in html


def test_badge_dot_fill_follows_ui_guide(web, conn, store, settings):
    """빈 점: 대기·실행 가능·실행 요청됨. 채운 점: 실행 중·확인 필요·완료·실패."""
    task_a = create_task(web, diagnose_form())
    task_b = create_task(web, fix_form(task_a))
    waiting = status_line(web.get(f"/tasks/{task_b}").text)
    assert 'data-status="대기"' in waiting and "dot-filled" not in waiting

    web.post(f"/tasks/{task_a}/run", follow_redirects=False)
    requested = status_line(web.get(f"/tasks/{task_a}").text)
    assert 'data-status="실행 요청됨"' in requested and "dot-filled" not in requested

    task_c, _ = seed_code_change_result(web, conn, store, settings)
    attention = status_line(web.get(f"/tasks/{task_c}").text)
    assert 'data-status="확인 필요"' in attention and "dot-filled" in attention
    web.post(f"/tasks/{task_c}/review", data={"decision": "approve"}, follow_redirects=False)
    done = status_line(web.get(f"/tasks/{task_c}").text)
    assert 'data-status="완료"' in done and "dot-filled" in done
    assert "병합: 운영자 확인 대기" in visible_text(web.get(f"/tasks/{task_c}").text)


def test_chain_nodes_show_status_as_badge_text_with_reason(web, conn):
    """워크플로우 노드는 색만이 아니라 배지 텍스트 + 한글 이유로 상태를 보인다 (UI_GUIDE "하지 마라")."""
    chain_id, (task_a, task_b) = import_chain(web, conn, "#41", "#42")
    html = web.get(f"/chains/{chain_id}").text
    nodes = re.findall(r'<li class="chain-node"[^>]*>(.*?)</li>', html, re.DOTALL)
    assert len(nodes) == 2
    for node, label, reason in ((nodes[0], "실행 가능", "agent-ops-demo 선택됨"), (nodes[1], "대기", "선행 대기")):
        line = status_line(node)
        assert f'data-status="{label}"' in line
        assert label in visible_text(line) and reason in visible_text(line)
    assert "dot-filled" not in status_line(nodes[0])  # 실행 가능은 빈 점
    human = html[html.index('class="chain-node chain-node-human"'):]
    assert "검토 승인 (사람) · 병합은 운영자 확인" in visible_text(human)
    assert 'data-status="대기"' in status_line(human)
    for label in ("진단", "코드 수정", "직접", "선행 완료 시 자동", "자동 완료", "검토 후 완료"):
        assert label in visible_text(html), label


def test_api_agent_card_shows_connected_without_last_seen(web):
    """API 에이전트는 heartbeat 가 없어도 '연결됨' 이고 '마지막 확인' 을 보이지 않는다. 로컬은 heartbeat 규칙."""
    cards = re.findall(r'<div class="card agent-card">(.*?)</div>\s*</div>', web.get("/tasks").text, re.S)
    by_id = {re.search(r"agent-[a-z-]+", c).group(0): c for c in cards}
    ops, codex = by_id["agent-ops-demo"], by_id["agent-codex-mac"]
    assert 'data-status="연결됨"' in ops and "마지막 확인" not in ops
    assert 'data-status="연결 끊김"' in codex and "마지막 확인 없음" in codex


# --- 결과 카드·뷰어 — 진단 -----------------------------------------------------------


def test_diagnosis_result_card_and_viewer(web, conn, store, settings):
    task_id, execution_id = seed_diagnosis_result(web, conn, store, settings)
    conn.execute(
        "INSERT INTO task_verdicts (task_id, execution_id, verdict_json, decided_at) VALUES (?, ?, ?, ?)",
        (task_id, execution_id, json.dumps({"outcome": "passed", "checks": [
            {"code": "attachments_in_trace", "passed": True, "detail": "8건"},
            {"code": "paths_differ_as_claimed", "passed": True, "detail": "확인"},
        ]}), NOW),
    )
    html = web.get(f"/tasks/{task_id}").text

    card = visible_text(html[html.index('class="result-card'):html.index('class="viewer')])
    assert "인계 가능" in card
    assert "daily-report · daily-0920-0900" in card
    assert "response_path_changed · 근거 5 · 첨부 8 · 검증 2/2 통과" in card

    viewer = visible_text(viewer_of(html))
    assert "인계 가능" in viewer
    chip = "response-after@1 · $.data.records"
    assert chip in viewer
    excerpt = viewer[viewer.index(chip):viewer.index("log-daily-0920@1 · lines:1-2")]
    assert "records" in excerpt and '"team": "운영"' in excerpt
    # 순서: 검증 결과 → summary → findings → diagnosis → repair_request
    positions = [viewer.index(s) for s in ("attachments_in_trace", "조회는 성공했으나", "실패 실행과 직전 정상 실행은",
                                            "baseline_run_id", "target_component")]
    assert positions == sorted(positions)
    assert "변경 전 응답" in viewer and "변경 후 응답" in viewer
    assert "$.items" in viewer and "$.data.records" in viewer
    assert "첨부 없음" not in viewer and "원문에 없음" not in viewer
    assert f"/tasks/{task_id}/artifacts/" in html  # 산출물 칩


def test_needs_information_puts_missing_information_first(web, conn, store, settings):
    task_id, _ = seed_diagnosis_result(web, conn, store, settings, needs_information=True)
    viewer = visible_text(viewer_of(web.get(f"/tasks/{task_id}").text))
    assert "정보 필요" in viewer
    assert viewer.index("evidence_unavailable") < viewer.index("응답의 목록 위치 차이")


# --- 결과 카드·뷰어 — 코드 수정 -------------------------------------------------------


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
    assert "일일 업무 보고서 — 2026-09-19" in viewer
    assert 'class="diff-add"' in html and 'class="diff-del"' in html
    assert "검토 의견" in text and 'value="request_changes"' in html


# --- 라이브 조각 -----------------------------------------------------------------


def test_live_fragment_is_partial_and_session_scoped(app, web, conn, store, settings, agents):
    task_id, _ = seed_code_change_result(web, conn, store, settings)
    response = web.get(f"/tasks/{task_id}/live")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert "<html" not in response.text and "<script" not in response.text
    assert "data-live" in response.text
    assert 'data-status="확인 필요"' in response.text
    assert 'class="result-card' in response.text
    assert f'action="/tasks/{task_id}/review"' in response.text
    assert f'data-live="/tasks/{task_id}/live"' in web.get(f"/tasks/{task_id}").text

    other = TestClient(app)
    assert other.get(f"/tasks/{task_id}/live").status_code == 404
    assert web.get("/tasks/nope/live").status_code == 404


def test_live_fragment_shows_successor_chip(web):
    task_a = create_task(web, diagnose_form())
    task_b = create_task(web, fix_form(task_a))
    fragment = web.get(f"/tasks/{task_a}/live").text
    assert f'href="/tasks/{task_b}"' in fragment and "후속" in fragment
    assert 'data-status="대기"' in fragment


# --- 금지 사항 ------------------------------------------------------------------


def test_visible_text_has_no_forbidden_phrases(web, conn, store, settings):
    task_id, _ = seed_code_change_result(web, conn, store, settings)
    chain_id, _ = import_chain(web, conn, "#41", "#42")
    login_operator(web)
    for path in (
        "/tasks", f"/tasks/{task_id}", "/tasks/new", "/tasks/import", "/agents", "/agents/register",
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
