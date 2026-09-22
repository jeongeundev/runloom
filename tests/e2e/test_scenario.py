"""전체 시나리오 — 심사자 흐름을 HTTP 로 재현한다. 두 경로가 한 스택(`conftest.stack`, 대본 에이전트) 위에서 이어진다.

- test_01 ~ test_11: **직접 등록 경로** — 카탈로그 등록 → 시연 업무 A·B 를 폼으로 등록 → A 실행 → B 자동 착수 → 검토 승인 →
  저장소 검사 → 연결 프로그램 오프라인. PRD "시연 흐름" 1~7 의 원래 순서.
- test_12 ~ test_21: **주 경로** (phase 5, UI_GUIDE "심사자 첫 방문 흐름") — 랜딩 → 에이전트 등록(카탈로그 3개) → 업무 가져오기
  (GitHub fixture) → 워크플로우 확인(순서·담당·이유) → 시작 → A 완료 → B 자동 착수 → 검토 승인 → 저장소 검사 → Jira·다른 세션
  → Claude 먼저 등록한 세션(대본 Claude 경로, `slow`).
- test_22 ~ test_28: **세 번째 종류** (phase 6 step 8, ADR-0009) — 별도 세션이 `/kinds`·`/rules` 폼으로 종류 `review` 와 규칙
  `code_change --[ready_for_review]--> review` 를 등록하면 진단 → 수정 → 검토가 사람 조작 없이 이어진다 (B 승인 전에 C 착수).
  `composition.py`·`worker.py` 는 이 절을 위해 바뀌지 않았다 — 등록만으로 붙는다는 증명. 규칙을 지우면 C' 는 착수하지 않는다.
- test_29 ~ test_35: **n8n 입구·출구** (phase 7 step 8, ADR-0010) — 별도 세션이 `/sources` 에서 입구 토큰을 발급하고, 테스트 안
  HTTP 수신기(n8n Wait 노드 역할, 127.0.0.1 임시 포트)를 `callback_url` 로 하여 쿠키 없이 `POST /sources/n8n/chains`. 접수 즉시
  A 가 시작되고 대본 A → B 뒤 사람 차례가 되면 `ChainCallback` 이 정확히 1건 온다 — 승인 뒤에도 두 번째는 없다. 거부 경로·세션 격리.

`WORKFLOW_E2E=1` 일 때만 돈다 (verify.sh 가 매 턴 도는 pytest 에서 제외). 실제 Codex·Claude·OpenAI 는 호출하지 않는다:
진단은 `DIAG_MODEL=fake`(fixture 대본), 코드 수정은 `workflow.scripted.codex`/`.claude` 래퍼(`LocalStack(scripted=True)`).
5개 프로세스는 `scripts/local_stack.py` 가 띄운다.

테스트 함수는 번호 순으로 이어지며 앞 단계의 결과(`ctx`·`chain_ctx`)를 쓴다 — `-x` 로 첫 실패에서 멈춘다.
"""

import json
import os
import re
import socketserver
import sqlite3
import subprocess
import sys
import threading
import time
from html import unescape as html_unescape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from local_stack import LocalStack  # noqa: E402
from seed_demo import REVIEW_CAPABILITY_CODE  # noqa: E402

from workflow.contracts.v1 import ChainCallback, InboundChainResponse  # noqa: E402

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(os.environ.get("WORKFLOW_E2E") != "1", reason="WORKFLOW_E2E=1 일 때만"),
]

CATALOG = ("agent-ops-demo", "agent-codex-mac", "agent-claude-mac")  # seed_demo 의 3개, 등록 순서
GITHUB_KEYS = ("#41", "#42", "#43", "#44")  # adapters/task_source_fixtures/github.json — 가져오기 화면의 기본 체크 4개
JIRA_KEYS = ("OPS-41", "OPS-42", "OPS-43", "OPS-44")
CHAIN_TITLE = "일일 보고서 생성 실패 (09-20 09:00) → 집계 API 응답 형식 변경 대응"

# web.EXAMPLES 와 같은 값 — 심사자가 미리 채워진 폼을 그대로 제출하는 상황
FORM_A = {
    "title": "일일 보고서 실패 진단",
    "request": "일일 보고서 생성 실패를 조사하고, 로컬 개발 에이전트가 재현·수정할 수 있도록 근거와 기대 동작을 정리해 주세요.",
    "capability_code": "operations.diagnose",
    "scope_value": "daily-report",
    "selection_mode": "auto",
    "run_mode": "manual",
    "completion_mode": "auto",
    "run_id": "daily-0920-0900",
}
FORM_B = {
    "title": "보고서 변환기 수정",
    "request": "인계된 진단 근거로 보고서 변환 실패를 재현하는 테스트를 먼저 작성하고, 실패를 확인한 뒤 두 응답 형식을 모두 처리하도록 최소 수정하세요.",
    "capability_code": "code.modify",
    "scope_value": "demo-report-repo",
    "selection_mode": "auto",
    "run_mode": "auto",
    "completion_mode": "review",
}

_LIVE_STATUS = re.compile(r'<div class="live"[^>]*\bdata-status="([^"]+)"')
_REASON = re.compile(r'<div class="status-line">.*?<span class="reason">(.*?)</span>', re.S)
_CHIP = re.compile(r'<a class="chip" href="/tasks/[^/]+/artifacts/([^"]+)"[^>]*>(?:<svg.*?</svg>)?([^<]+)</a>', re.S)
_EVENT_TYPE = re.compile(r'<span class="t-type">([a-z_]+)</span>')
_AGENT_CARD = re.compile(r'<div class="card agent-card">(.*?)</div>\s*</div>', re.S)


# --- fixture ------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def client(stack) -> httpx.Client:
    """심사자 세션 하나. 첫 `GET /` 의 Set-Cookie 를 이후 요청에 붙인다."""
    with httpx.Client(base_url=stack.central_url, follow_redirects=False, timeout=10.0) as session:
        yield session


@pytest.fixture(scope="module")
def ctx() -> dict:
    return {}


# --- 도우미 --------------------------------------------------------------------------------


def _live(client: httpx.Client, task_id: str) -> str:
    response = client.get(f"/tasks/{task_id}/live")
    assert response.status_code == 200, response.text[:300]
    return response.text


def _status(html: str) -> tuple[str, str]:
    label = _LIVE_STATUS.search(html)
    reason = _REASON.search(html)
    assert label and reason, html[:500]
    return label.group(1), reason.group(1).strip()


def _chips(html: str) -> dict[str, str]:
    """산출물 칩 라벨 → artifact_id (같은 라벨이면 마지막 것)."""
    return {label.strip(): artifact_id for artifact_id, label in _CHIP.findall(html)}


def _watch(client: httpx.Client, task_id: str, *, until, timeout: float, interval: float = 0.3) -> list[tuple[str, str]]:
    """`until(label, reason)` 이 참이 될 때까지 /live 를 폴링하고, 관측한 (상태, 이유) 변화를 순서대로 돌려준다."""
    seen: list[tuple[str, str]] = []
    deadline = time.monotonic() + timeout
    while True:
        current = _status(_live(client, task_id))
        if not seen or seen[-1] != current:
            seen.append(current)
        if until(*current):
            return seen
        assert time.monotonic() < deadline, f"{timeout}초 안에 도달하지 못함. 관측: {seen}"
        time.sleep(interval)


def _labels(seen: list[tuple[str, str]]) -> list[str]:
    labels: list[str] = []
    for label, _ in seen:
        if not labels or labels[-1] != label:
            labels.append(label)
    return labels


def _assert_in_order(labels: list[str], chain: list[str]) -> None:
    """관측한 상태가 기대 순서의 부분열이어야 한다 (뒤로 가거나 다른 상태가 끼면 실패)."""
    positions = [chain.index(label) for label in labels if label in chain]
    assert len(positions) == len(labels), f"기대 밖 상태: {labels} ⊄ {chain}"
    assert positions == sorted(positions), f"순서 역전: {labels}"


def _create_task(client: httpx.Client, form: dict[str, str]) -> str:
    response = client.post("/tasks", data=form)
    assert response.status_code == 303, response.text[:500]
    location = response.headers["location"]
    assert location.startswith("/tasks/task-"), location
    return location.rsplit("/", 1)[1]


def _select(client: httpx.Client, task_id: str, agent_id: str) -> None:
    response = client.post(f"/tasks/{task_id}/select", data={"agent_id": agent_id})
    assert response.status_code == 303, response.text[:500]


def _raw(client: httpx.Client, task_id: str, artifact_id: str) -> str:
    response = client.get(f"/tasks/{task_id}/artifacts/{artifact_id}", params={"raw": 1})
    assert response.status_code == 200, response.text[:300]
    return response.text


def _agent_states(html: str) -> dict[str, str]:
    states = {}
    for card in _AGENT_CARD.findall(html):
        agent_id = re.search(r'<a href="/agents/([^"]+)"', card).group(1)
        states[agent_id] = re.search(r'data-status="(연결됨|연결 끊김)"', card).group(1)
    return states


def _wait_agent(client: httpx.Client, agent_id: str, state: str, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while True:
        states = _agent_states(client.get("/tasks").text)
        if states.get(agent_id) == state:
            return
        assert time.monotonic() < deadline, f"{agent_id} 가 {timeout}초 안에 {state} 가 되지 않음: {states}"
        time.sleep(1.0)


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()


# --- 시나리오 ------------------------------------------------------------------------------


def test_01_first_visit_issues_session_and_prompts_agent_registration(stack, client):
    response = client.get("/tasks")
    assert response.status_code == 200
    assert "wf_session" in response.headers.get("set-cookie", "")
    assert "아직 업무가 없습니다." in response.text
    # 등록 전에는 이 세션에 Agent 가 없다 — 카탈로그에서 고르라는 안내만
    assert _agent_states(response.text) == {}
    assert "먼저 에이전트를 등록하세요." in response.text and 'href="/agents/register"' in response.text


def test_01b_register_three_catalog_agents_and_see_them_connected(client):
    """심사자 흐름 1단계 — 운영자 카탈로그 3개(진단 API·개인 Codex·Claude Code)를 ops → codex → claude 순으로 등록한다."""
    catalog = client.get("/agents/register")
    assert catalog.status_code == 200
    assert "운영 진단 데모" in catalog.text and "개인 Codex" in catalog.text and "Claude Code" in catalog.text
    assert catalog.text.count("시연용 · 대본 재생") == 3  # LocalStack(scripted=True) 는 seed 도 --scripted 로 돌린다
    for agent_id in CATALOG:
        response = client.post("/agents/register", data={"agent_id": agent_id})
        assert response.status_code == 303, response.text[:300]
    assert set(_agent_states(client.get("/tasks").text)) == set(CATALOG)
    # connector run 하나가 두 로컬 등록(codex·claude)을 대신하므로 heartbeat 뒤 둘 다 연결됨
    _wait_agent(client, "agent-codex-mac", "연결됨", timeout=20)
    _wait_agent(client, "agent-claude-mac", "연결됨", timeout=20)


def test_02_register_diagnosis_task_a_is_runnable(client, ctx):
    ctx["A"] = _create_task(client, FORM_A)
    label, reason = _status(_live(client, ctx["A"]))
    assert label == "실행 가능"
    assert reason == "agent-ops-demo 선택됨"
    assert "자동 선택 · agent-ops-demo" in _live(client, ctx["A"])


def test_03_register_fix_task_b_waits_for_predecessor(client, ctx):
    ctx["B"] = _create_task(client, {**FORM_B, "predecessor_task_id": ctx["A"]})
    # 직접 등록은 동률 기본 선택이 없다 (phase 5 step 5) — Codex·Claude 둘 다 맡을 수 있어 선택 필요 (상태는 선행 대기가 먼저)
    html = _live(client, ctx["B"])
    assert _status(html) == ("대기", "선행 대기")
    assert "자동 선택 · 미선택 · 후보 2개 — 선택 필요" in html
    _select(client, ctx["B"], "agent-codex-mac")
    html = _live(client, ctx["B"])
    assert _status(html) == ("대기", "선행 대기")
    assert "직접 선택 · agent-codex-mac" in html


def test_04_run_a_completes_by_verifier_verdict(client, ctx):
    response = client.post(f"/tasks/{ctx['A']}/run")
    assert response.status_code == 303, response.text[:300]
    assert _status(_live(client, ctx["A"]))[0] == "실행 요청됨"

    seen = _watch(client, ctx["A"], until=lambda label, _: label in ("완료", "실패", "확인 필요"), timeout=60)
    label, reason = seen[-1]
    assert label == "완료", seen
    assert reason.startswith("판정 근거: "), reason
    _assert_in_order(_labels(seen), ["실행 요청됨", "실행 중", "완료"])

    # 가짜 진단은 한 폴링 간격 안에 끝나 `실행 중` 이 화면에 안 잡힐 수 있다. 실행이 running 을 거쳤다는 증거는
    # 실행 블록의 이벤트 타임라인(started → progress → result_ready)으로 확인한다
    html = _live(client, ctx["A"])
    types = _EVENT_TYPE.findall(html)
    assert types[:2] == ["accepted", "started"] and "progress" in types and types[-1] == "result_ready", types
    assert 'data-outcome="ready_for_handoff"' in html and "인계 가능" in html
    assert "response_path_changed" in html
    chips = _chips(html)
    assert "진단 결과" in chips
    ctx["A_result"] = _raw(client, ctx["A"], chips["진단 결과"])
    assert re.search(r'"model_id":\s*"fake-fixture-script"', ctx["A_result"])  # 실제 모델이 아니라 fixture 대본


def test_05_b_starts_by_worker_scan_and_waits_for_review(client, ctx):
    seen = _watch(
        client, ctx["B"],
        until=lambda label, reason: (label, reason) == ("확인 필요", "검토 대기") or label in ("실패", "완료"),
        timeout=120,
    )
    assert seen[-1] == ("확인 필요", "검토 대기"), seen
    labels = _labels(seen)
    _assert_in_order(labels, ["대기", "실행 요청됨", "실행 중", "확인 필요"])
    assert "실행 중" in labels, seen

    html = _live(client, ctx["B"])
    assert 'data-outcome="ready_for_review"' in html and "검토 가능" in html
    chips = _chips(html)
    assert {"diff", "테스트 전", "테스트 후", "보고서"} <= set(chips), chips
    ctx["B_chips"] = chips
    types = _EVENT_TYPE.findall(html)
    assert types[:2] == ["accepted", "started"] and types[-1] == "result_ready", types


def test_06_b_artifacts_prove_repro_fix_and_report(client, ctx):
    chips = ctx["B_chips"]
    before = _raw(client, ctx["B"], chips["테스트 전"])
    assert before.startswith("exit_code=1\n"), before[:200]
    after = _raw(client, ctx["B"], chips["테스트 후"])
    assert after.startswith("exit_code=0\n"), after[:200]
    report = _raw(client, ctx["B"], chips["보고서"])
    assert "일일 업무 보고서 — 2026-09-19" in report and "합계    20    5" in report, report
    diff = _raw(client, ctx["B"], chips["diff"])
    assert "daily_report/transformer.py" in diff and "tests/test_repro_records.py" in diff
    jsonl = _raw(client, ctx["B"], chips["Codex JSONL"])
    assert "scripted-codex" in jsonl  # 실제 Codex 가 아니라 대본(workflow.scripted.codex)이 돌았다
    ctx["B_result"] = _raw(client, ctx["B"], chips["수정 결과"])
    assert re.search(r'"outcome":\s*"ready_for_review"', ctx["B_result"])


def test_07_reviewer_approves_b_and_merge_waits_for_operator(client, ctx):
    response = client.post(f"/tasks/{ctx['B']}/review", data={"decision": "approve", "comment": ""})
    assert response.status_code == 303, response.text[:300]
    html = _live(client, ctx["B"])
    assert _status(html) == ("완료", "검토 승인 · 병합: 운영자 확인 대기")
    assert "병합: 운영자 확인 대기" in html
    detail = client.get(f"/tasks/{ctx['B']}")
    assert detail.status_code == 200 and 'data-status="완료"' in detail.text


def test_08_no_duplicate_execution_after_more_worker_ticks(stack, client, ctx):
    time.sleep(10)  # 중앙 워커가 완료된 A 를 몇 번 더 스캔한 뒤
    conn = sqlite3.connect(stack.central_db)
    try:
        rows = conn.execute(
            "SELECT execution_id, status FROM executions WHERE task_id = ?", (ctx["B"],)
        ).fetchall()
        a_rows = conn.execute("SELECT COUNT(*) FROM executions WHERE task_id = ?", (ctx["A"],)).fetchone()[0]
    finally:
        conn.close()
    assert len(rows) == 1 and rows[0][1] == "result_ready", rows
    assert a_rows == 1


def test_09_other_session_cannot_see_the_tasks(stack, ctx):
    with httpx.Client(base_url=stack.central_url, timeout=10.0) as other:
        assert other.get(f"/tasks/{ctx['A']}").status_code == 404
        assert other.get(f"/tasks/{ctx['B']}").status_code == 404
        assert "아직 업무가 없습니다." in other.get("/tasks").text


def test_10_demo_repo_main_untouched_and_result_on_task_branch(stack, ctx):
    repo = stack.repo_path
    assert _git(repo, "rev-parse", "main") == stack.base_commit
    assert _git(repo, "rev-parse", "report-base^{commit}") == stack.base_commit
    assert _git(repo, "status", "--porcelain") == ""
    branch = f"task/{ctx['B']}"
    result_commit = _git(repo, "rev-parse", branch)
    assert result_commit != stack.base_commit
    assert _git(repo, "rev-parse", f"{branch}^") == stack.base_commit
    changed = _git(repo, "diff", "--name-only", stack.base_commit, branch).splitlines()
    assert sorted(changed) == ["daily_report/transformer.py", "tests/test_repro_records.py"]
    assert re.search(rf'"result_commit":\s*"{result_commit}"', ctx["B_result"]), ctx["B_result"]
    # 결과 업로드 뒤 worktree·인계 디렉터리는 지워지고 브랜치만 남는다 (연결 프로그램 기본 동작, --keep-workdirs 없음)
    worktrees = repo.parent / f"{repo.name}-worktrees"
    assert not (worktrees / ctx["B"]).exists() and not (worktrees / f"{ctx['B']}.handoff").exists()
    assert ctx["B"] not in _git(repo, "worktree", "list")


def test_11_offline_connector_leaves_new_fix_task_waiting(stack, client, ctx):
    stack.stop_service("connector")
    assert not stack.running("connector")
    # heartbeat 10초 규칙 (LocalStack 의 WORKFLOW_LIMIT_HEARTBEAT_OFFLINE_SECONDS) — 워커·화면 모두 같은 값을 읽는다.
    # 오프라인이 되기 전에 등록하면 워커가 (아직 online 으로 보이는) 에이전트에 실행을 만들 수 있으므로 먼저 기다린다
    _wait_agent(client, "agent-codex-mac", "연결 끊김", timeout=100)

    ctx["C"] = _create_task(client, {**FORM_B, "predecessor_task_id": ctx["A"]})
    _select(client, ctx["C"], "agent-codex-mac")  # test_03 과 같은 이유 — 후보 2개
    label, reason = _status(_live(client, ctx["C"]))
    assert label == "대기" and reason.startswith("연결 끊김, 마지막 확인 "), (label, reason)
    time.sleep(7)  # 워커 두 바퀴 — 오프라인 동안 실행을 만들지 않는다
    label, reason = _status(_live(client, ctx["C"]))
    assert label == "대기" and reason.startswith("연결 끊김, 마지막 확인 "), (label, reason)
    conn = sqlite3.connect(stack.central_db)
    try:
        count = conn.execute("SELECT COUNT(*) FROM executions WHERE task_id = ?", (ctx["C"],)).fetchone()[0]
    finally:
        conn.close()
    assert count == 0


# --- 주 경로: 등록 → 가져오기 → 워크플로우 (phase 5, UI_GUIDE "심사자 첫 방문 흐름") --------------------------

_NODE_OPEN = re.compile(r'<li class="chain-node" data-task-id="([^"]+)">')
_GATE_OPEN = '<li class="chain-node chain-node-human">'
_NODE_STATUS = re.compile(r'data-status="([^"]+)"')
_NODE_REASON = re.compile(r'<span class="reason">(.*?)</span>', re.S)
_CHECKBOX = re.compile(r'<input type="checkbox" name="issue_keys" value="([^"]+)" checked')


@pytest.fixture(scope="module")
def chain_stack(stack) -> LocalStack:
    """주 경로는 연결 프로그램이 살아 있어야 한다 — 직접 등록 경로의 마지막(test_11)이 내렸으면 같은 계획으로 다시 띄운다.
    등록·연결 토큰은 connector 홈에 남아 있어 재접속만 한다."""
    stack.start_service("connector")
    return stack


@pytest.fixture(scope="module")
def judge(chain_stack) -> httpx.Client:
    """심사자 세션 — 직접 등록 경로(`client`)와 다른 세션. 랜딩(`/`)은 쿠키를 주지 않고 `/tasks` 가 준다."""
    with httpx.Client(base_url=chain_stack.central_url, follow_redirects=False, timeout=10.0) as session:
        yield session


@pytest.fixture(scope="module")
def chain_ctx() -> dict:
    return {}


def _chain_page(client: httpx.Client, chain_id: str, *, live: bool = False) -> str:
    response = client.get(f"/chains/{chain_id}/live" if live else f"/chains/{chain_id}")
    assert response.status_code == 200, response.text[:300]
    return response.text


def _node_bodies(html: str) -> dict[str, str]:
    """워크플로우 노드 li 의 본문 — task_id → 다음 노드(또는 사람 단계) 직전까지의 HTML. 순서는 화면 순서."""
    parts = re.split(r'(?=<li class="chain-node)', html)
    bodies: dict[str, str] = {}
    for part in parts:
        match = _NODE_OPEN.match(part)
        if match:
            bodies[match.group(1)] = part
    return bodies


def _node_status(body: str) -> tuple[str, str]:
    """노드 본문의 상태 배지 텍스트와 이유 — 첫 `data-status` 는 status-line 의 배지다 (node-index 의 점에는 없다)."""
    label = _NODE_STATUS.search(body)
    reason = _NODE_REASON.search(body)
    assert label and reason, body[:500]
    return label.group(1), reason.group(1).strip()


def _nodes(html: str) -> dict[str, tuple[str, str]]:
    return {task_id: _node_status(body) for task_id, body in _node_bodies(html).items()}


def _gate(html: str) -> tuple[str, str]:
    assert _GATE_OPEN in html, html[:500]
    return _node_status(html[html.index(_GATE_OPEN):])


def _watch_chain(
    client: httpx.Client, chain_id: str, task_id: str, *, until, timeout: float, interval: float = 0.3,
) -> list[tuple[str, str]]:
    """체인 라이브 조각(`/chains/{id}/live`)에서 한 노드의 (상태, 이유) 변화를 `until` 이 참이 될 때까지 기록한다."""
    seen: list[tuple[str, str]] = []
    deadline = time.monotonic() + timeout
    while True:
        current = _nodes(_chain_page(client, chain_id, live=True))[task_id]
        if not seen or seen[-1] != current:
            seen.append(current)
        if until(*current):
            return seen
        assert time.monotonic() < deadline, f"{timeout}초 안에 도달하지 못함. 관측: {seen}"
        time.sleep(interval)


def _register_all(client: httpx.Client, order: tuple[str, ...]) -> None:
    for agent_id in order:
        response = client.post("/agents/register", data={"agent_id": agent_id})
        assert response.status_code == 303, response.text[:300]


def _import(client: httpx.Client, source: str, *keys: str) -> str:
    response = client.post("/tasks/import", data={"source": source, "issue_keys": list(keys)})
    assert response.status_code == 303, response.text[:500]
    location = response.headers["location"]
    assert location.startswith("/chains/chain-"), location
    return location.removeprefix("/chains/")


def _assert_fix_artifacts(client: httpx.Client, task_id: str, chips: dict[str, str]) -> str:
    """코드 수정 결과의 산출물 4종 + 결과 봉투. 재현(수정 전 실패) → 수정(후 통과) → 보고서를 원문으로 확인하고 결과 봉투를 돌려준다."""
    assert {"diff", "테스트 전", "테스트 후", "보고서", "수정 결과"} <= set(chips), chips
    assert _raw(client, task_id, chips["테스트 전"]).startswith("exit_code=1\n")
    assert _raw(client, task_id, chips["테스트 후"]).startswith("exit_code=0\n")
    report = _raw(client, task_id, chips["보고서"])
    assert "일일 업무 보고서 — 2026-09-19" in report and "합계    20    5" in report, report
    diff = _raw(client, task_id, chips["diff"])
    assert "daily_report/transformer.py" in diff and "tests/test_repro_records.py" in diff
    result = _raw(client, task_id, chips["수정 결과"])
    assert re.search(r'"outcome":\s*"ready_for_review"', result)
    return result


def test_12_chain_first_visit_landing_has_no_session_and_home_prompts_registration(judge):
    """1. 첫 방문 — 랜딩은 세션을 만들지 않는다. `서비스 바로 가기` → `/tasks` 가 쿠키를 주고 등록부터 안내한다."""
    landing = judge.get("/")
    assert landing.status_code == 200
    assert "set-cookie" not in landing.headers
    assert "서비스 바로 가기" in landing.text and 'href="/tasks"' in landing.text

    home = judge.get("/tasks")
    assert home.status_code == 200
    assert "wf_session" in home.headers.get("set-cookie", "")
    assert _agent_states(home.text) == {}
    assert "먼저 에이전트를 등록하세요." in home.text and 'href="/agents/register"' in home.text
    assert '<button class="btn" type="button" disabled>업무 가져오기</button>' in home.text
    assert "아직 업무가 없습니다." in home.text and "워크플로우</h2>" not in home.text


def test_13_chain_register_catalog_three_agents_ops_codex_claude(judge):
    """2. 카탈로그 3개를 ops → codex → claude 순으로 등록한다. 홈 카드 3개, 셋 다 대본 라벨, 로컬 2개는 연결됨."""
    catalog = judge.get("/agents/register")
    assert catalog.status_code == 200
    for agent_id in CATALOG:
        assert f'href="/agents/{agent_id}"' in catalog.text, agent_id
    assert catalog.text.count("시연용 · 대본 재생") == 3
    _register_all(judge, CATALOG)

    home = judge.get("/tasks").text
    assert list(_agent_states(home)) == list(CATALOG)  # 등록 순서대로
    assert home.count("시연용 · 대본 재생") == 3
    assert 'href="/tasks/import">업무 가져오기</a>' in home
    _wait_agent(judge, "agent-codex-mac", "연결됨", timeout=30)
    _wait_agent(judge, "agent-claude-mac", "연결됨", timeout=30)


def test_14_chain_import_github_issues_all_checked(judge, chain_ctx):
    """3. 업무 가져오기 — GitHub 탭 4개가 전부 체크돼 있고, 그대로 가져오면 워크플로우 화면으로 간다."""
    page = judge.get("/tasks/import", params={"source": "github"})
    assert page.status_code == 200
    assert "시연 데이터입니다 — 실제 GitHub·Jira 에 연결하지 않습니다." in page.text
    assert tuple(_CHECKBOX.findall(page.text)) == GITHUB_KEYS
    assert "operations.diagnose · daily-report" in page.text and "code.modify · demo-report-repo" in page.text
    assert page.text.count("맡을 에이전트 없음") == 2  # #43 enhancement · #44 docs
    assert "blocked by #41" in page.text
    assert '<button class="btn" type="submit">가져와서 워크플로우 구성</button>' in page.text

    chain_ctx["chain"] = _import(judge, "github", *GITHUB_KEYS)


def test_15_chain_page_shows_order_assignment_reasons_gate_and_skipped(judge, chain_ctx):
    """4. 워크플로우 화면 — #41(진단, 실행 가능) → #42(개인 Codex 기본 선택, 선행 대기) → 사람 단계. #43·#44 는 넣지 않음."""
    html = _chain_page(judge, chain_ctx["chain"])
    assert f"워크플로우<span class=\"sep\">/</span>{CHAIN_TITLE}" in html
    assert "GitHub Issues" in html and "시연 데이터" in html

    bodies = _node_bodies(html)
    assert len(bodies) == 2
    chain_ctx["A"], chain_ctx["B"] = bodies
    node_a, node_b = bodies.values()
    assert "#41" in node_a and "진단</span>" in node_a and "운영 진단 데모" in node_a
    assert "실행 직접" in node_a and "자동 완료" in node_a
    assert _node_status(node_a) == ("실행 가능", "agent-ops-demo 선택됨")
    assert "체인의 첫 업무 — 선행 없음" in node_a

    assert "#42" in node_b and "코드 수정</span>" in node_b and "개인 Codex" in node_b
    assert "시연용 · 대본 재생" in node_b
    assert "선행 완료 시 자동" in node_b and "검토 후 완료" in node_b
    assert _node_status(node_b) == ("대기", "선행 대기")
    assert "선행 #41 (operations.diagnose) → code.modify 인계" in node_b
    assert "일치 후보 2개" in node_b and "먼저 등록한 agent-codex-mac" in node_b  # 등록 순서 동률 규칙
    assert "담당 변경" in node_b  # 시작 전에는 바꿀 수 있다

    assert "검토 승인 (사람) · 병합은 운영자 확인" in html
    assert _gate(html) == ("대기", "선행 대기")
    assert "워크플로우에 넣지 않은 이슈" in html
    assert "#43" in html and "변경 응답 형식 모니터링 알림 추가" in html
    assert "#44" in html and "README 오타 수정" in html
    assert f'action="/chains/{chain_ctx["chain"]}/start"' in html
    assert f'action="/tasks/{chain_ctx["B"]}/run"' not in html  # 뒤 노드는 워커가 잇는다
    home = judge.get("/tasks").text
    assert f'href="/chains/{chain_ctx["chain"]}"' in home and "0/2 완료 · 시작 전" in home


def test_16_chain_start_runs_diagnosis_a_to_completion(judge, chain_ctx):
    """5. `워크플로우 시작` → #41 실행 요청됨 → 실행 중 → 완료 · 판정 근거 n/n. 실제 모델이 아니라 fixture 대본."""
    chain_id, task_a = chain_ctx["chain"], chain_ctx["A"]
    response = judge.post(f"/chains/{chain_id}/start")
    assert response.status_code == 303 and response.headers["location"] == f"/chains/{chain_id}"
    html = _chain_page(judge, chain_id, live=True)
    assert _nodes(html)[task_a][0] == "실행 요청됨"
    assert "2단계 중 1단계 실행 요청됨" in html and 'data-poll="1"' in html

    seen = _watch_chain(
        judge, chain_id, task_a, until=lambda label, _: label in ("완료", "실패", "확인 필요"), timeout=60,
    )
    label, reason = seen[-1]
    assert label == "완료" and reason.startswith("판정 근거: "), seen
    _assert_in_order(_labels(seen), ["실행 요청됨", "실행 중", "완료"])

    detail = _live(judge, task_a)
    assert 'data-outcome="ready_for_handoff"' in detail and "인계 가능" in detail
    assert "대본 재생 (실제 모델 호출 없음)" in detail
    assert f'href="/chains/{chain_id}"' in detail  # 브레드크럼의 워크플로우 칩
    types = _EVENT_TYPE.findall(detail)
    assert types[:2] == ["accepted", "started"] and types[-1] == "result_ready", types
    chips = _chips(detail)
    assert "진단 결과" in chips
    assert re.search(r'"model_id":\s*"fake-fixture-script"', _raw(judge, task_a, chips["진단 결과"]))


def test_17_chain_b_starts_without_human_action_and_waits_for_review(judge, chain_ctx):
    """6. #42 는 사람 조작 없이 실행 요청됨 → 실행 중 → 확인 필요 · 검토 대기. 결과 카드 검토 가능 + 대본 재생, 산출물 4종."""
    chain_id, task_b = chain_ctx["chain"], chain_ctx["B"]
    seen = _watch_chain(
        judge, chain_id, task_b,
        until=lambda label, reason: (label, reason) == ("확인 필요", "검토 대기") or label in ("실패", "완료"),
        timeout=120,
    )
    assert seen[-1] == ("확인 필요", "검토 대기"), seen
    labels = _labels(seen)
    _assert_in_order(labels, ["대기", "실행 요청됨", "실행 중", "확인 필요"])
    assert "실행 중" in labels, seen

    html = _chain_page(judge, chain_id, live=True)
    assert _gate(html) == ("확인 필요", "검토 대기")
    assert f'href="/tasks/{task_b}">검토하기</a>' in html
    assert "2단계 중 2단계 확인 필요" in html and 'data-poll="0"' in html  # 사람 차례

    detail = _live(judge, task_b)
    assert 'data-outcome="ready_for_review"' in detail and "검토 가능" in detail
    assert "대본 재생 (실제 모델 호출 없음)" in detail
    chips = _chips(detail)
    chain_ctx["B_result"] = _assert_fix_artifacts(judge, task_b, chips)
    assert "scripted-codex" in _raw(judge, task_b, chips["Codex JSONL"])  # 실제 Codex 가 아니라 대본
    assert "Claude JSONL" not in chips


def test_18_chain_review_approve_completes_gate_and_home_shows_two_of_two(judge, chain_ctx):
    """7. 검토 승인 → 사람 단계 `완료 · 병합: 운영자 확인 대기`. 홈 워크플로우 구역 2/2 완료."""
    chain_id, task_b = chain_ctx["chain"], chain_ctx["B"]
    response = judge.post(f"/tasks/{task_b}/review", data={"decision": "approve", "comment": ""})
    assert response.status_code == 303, response.text[:300]

    html = _chain_page(judge, chain_id)
    assert _nodes(html)[task_b] == ("완료", "검토 승인 · 병합: 운영자 확인 대기")
    assert _gate(html) == ("완료", "병합: 운영자 확인 대기")
    assert "병합 확인됨" not in html and "2단계 모두 완료" in html
    home = judge.get("/tasks").text
    assert "2/2 완료 · 2단계 모두 완료" in home


def test_19_chain_demo_repo_main_untouched_branch_exists_no_worktree(chain_stack, chain_ctx):
    """8. 데모 저장소 — main 은 report-base 그대로, task/{#42} 브랜치에 결과 커밋, worktree·인계 디렉터리는 지워짐."""
    repo, task_b = chain_stack.repo_path, chain_ctx["B"]
    assert _git(repo, "rev-parse", "main") == chain_stack.base_commit
    assert _git(repo, "rev-parse", "report-base^{commit}") == chain_stack.base_commit
    assert _git(repo, "status", "--porcelain") == ""
    branch = f"task/{task_b}"
    result_commit = _git(repo, "rev-parse", branch)
    assert result_commit != chain_stack.base_commit
    assert _git(repo, "rev-parse", f"{branch}^") == chain_stack.base_commit
    changed = _git(repo, "diff", "--name-only", chain_stack.base_commit, branch).splitlines()
    assert sorted(changed) == ["daily_report/transformer.py", "tests/test_repro_records.py"]
    assert re.search(rf'"result_commit":\s*"{result_commit}"', chain_ctx["B_result"])
    worktrees = repo.parent / f"{repo.name}-worktrees"
    assert not (worktrees / task_b).exists() and not (worktrees / f"{task_b}.handoff").exists()
    assert task_b not in _git(repo, "worktree", "list")


def test_20_chain_jira_import_in_other_session_has_same_shape_and_is_isolated(chain_stack, judge, chain_ctx):
    """9. 다른 세션이 Jira 탭으로 가져오면 같은 구조(OPS-41 → OPS-42, OPS-43·OPS-44 제외). 세션끼리 워크플로우가 보이지 않는다."""
    with httpx.Client(base_url=chain_stack.central_url, follow_redirects=False, timeout=10.0) as other:
        _register_all(other, CATALOG)
        page = other.get("/tasks/import", params={"source": "jira"})
        assert tuple(_CHECKBOX.findall(page.text)) == JIRA_KEYS
        assert 'aria-current="page">Jira</a>' in page.text
        other_chain = _import(other, "jira", *JIRA_KEYS)

        html = _chain_page(other, other_chain)
        assert "Jira" in html and CHAIN_TITLE in html
        bodies = _node_bodies(html)
        assert len(bodies) == 2
        node_a, node_b = bodies.values()
        assert "OPS-41" in node_a and _node_status(node_a) == ("실행 가능", "agent-ops-demo 선택됨")
        assert "OPS-42" in node_b and _node_status(node_b) == ("대기", "선행 대기")
        assert "먼저 등록한 agent-codex-mac" in node_b
        assert _gate(html) == ("대기", "선행 대기")
        assert "OPS-43" in html and "OPS-44" in html and "워크플로우에 넣지 않은 이슈" in html
        assert f'action="/chains/{other_chain}/start"' in html  # 시작하지 않는다 — 구조만 확인

        # 세션 격리 — 서로의 워크플로우·업무가 404, 홈 목록에도 없다
        assert other.get(f"/chains/{chain_ctx['chain']}").status_code == 404
        assert other.get(f"/tasks/{chain_ctx['B']}").status_code == 404
        assert f'href="/chains/{chain_ctx["chain"]}"' not in other.get("/tasks").text
        assert judge.get(f"/chains/{other_chain}").status_code == 404
        assert f'href="/chains/{other_chain}"' not in judge.get("/tasks").text


@pytest.mark.slow
def test_21_chain_claude_first_session_runs_fix_with_scripted_claude(chain_stack):
    """10. Claude 를 먼저 등록한 세션 — #42 는 Claude Code 기본 선택, 실행 산출물에 Claude JSONL (대본 Claude 경로)."""
    with httpx.Client(base_url=chain_stack.central_url, follow_redirects=False, timeout=10.0) as session:
        _register_all(session, ("agent-claude-mac", "agent-codex-mac", "agent-ops-demo"))
        chain_id = _import(session, "github", "#41", "#42")
        html = _chain_page(session, chain_id)
        task_a, task_b = _node_bodies(html)
        node_b = _node_bodies(html)[task_b]
        assert "Claude Code" in node_b and "시연용 · 대본 재생" in node_b
        assert "일치 후보 2개" in node_b and "먼저 등록한 agent-claude-mac" in node_b

        assert session.post(f"/chains/{chain_id}/start").status_code == 303
        seen = _watch_chain(
            session, chain_id, task_a, until=lambda label, _: label in ("완료", "실패", "확인 필요"), timeout=60,
        )
        assert seen[-1][0] == "완료", seen
        seen = _watch_chain(
            session, chain_id, task_b,
            until=lambda label, reason: (label, reason) == ("확인 필요", "검토 대기") or label in ("실패", "완료"),
            timeout=120,
        )
        assert seen[-1] == ("확인 필요", "검토 대기"), seen
        _assert_in_order(_labels(seen), ["대기", "실행 요청됨", "실행 중", "확인 필요"])

        detail = _live(session, task_b)
        assert 'data-outcome="ready_for_review"' in detail and "대본 재생 (실제 모델 호출 없음)" in detail
        chips = _chips(detail)
        result = _assert_fix_artifacts(session, task_b, chips)
        assert "Claude JSONL" in chips and "Codex JSONL" not in chips, chips
        jsonl = _raw(session, task_b, chips["Claude JSONL"])
        assert "scripted-claude" in jsonl and "scripted-demo-agent" in jsonl  # 실제 Claude 가 아니라 대본
        result_commit = re.search(r'"result_commit":\s*"([0-9a-f]{40})"', result).group(1)
        repo = chain_stack.repo_path
        assert _git(repo, "rev-parse", f"task/{task_b}") == result_commit
        assert _git(repo, "rev-parse", "main") == chain_stack.base_commit
        assert task_b not in _git(repo, "worktree", "list")


# --- 세 번째 종류: review — 화면으로 등록한 종류·규칙만으로 진단 → 수정 → 검토 (phase 6 step 8, ADR-0009) ---------------

# /kinds 폼 — 세 번째 종류. seed·코드 상수가 아니라 화면으로 등록한다 (이 절의 증명 조건)
REVIEW_KIND_FORM = {
    "kind": "review",
    "label": "검토",
    "capability_code": REVIEW_CAPABILITY_CODE,  # seed_demo 의 Claude 능력 코드와 같은 값
    "scope_key": "repository_id",
    "input_kinds": ["diff", "code_change_result"],
    "outcomes": "approved, changes_requested, needs_information",
    "instructions": "인계 디렉터리의 diff 와 code_change_result 를 읽고 변경이 진단의 수정 요청을 충족하는지 검토하세요. "
                    "저장소를 수정하지 마세요.",
}
# /rules 폼 — 코드 수정 결과가 ready_for_review 면 diff·수정 결과·수정 후 테스트 기록을 넘겨 검토를 시작한다
REVIEW_RULE_FORM = {
    "from_kind": "code_change",
    "on_outcomes": ["ready_for_review"],
    "to_kind": "review",
    "handoff_kinds": ["diff", "code_change_result", "test_log_after"],
}
REVIEW_RULE_TEXT = "코드 수정 --[ready_for_review]--> 검토"
BUILTIN_RULE_TEXT = "진단 --[ready_for_handoff]--> 코드 수정"
FORM_C = {
    "title": "변경 검토",
    "request": "인계된 diff 와 코드 수정 결과를 검토하고 승인 여부를 판단해 주세요.",
    "capability_code": REVIEW_CAPABILITY_CODE,
    "scope_value": "demo-report-repo",
    "selection_mode": "auto",
    "run_mode": "auto",
    "completion_mode": "review",
}
_RULE_ROW = re.compile(r'<td class="rule-text">(.*?)</td>.*?action="/rules/([^"]+)/delete"', re.S)
_REVIEW_DONE = ("확인 필요", "검토 대기")


@pytest.fixture(scope="module")
def reviewer(chain_stack) -> httpx.Client:
    """세 번째 종류 절의 세션 — 종류·규칙은 세션별이라 앞 절의 세션들(`client`·`judge`)의 등록부를 건드리지 않는다."""
    with httpx.Client(base_url=chain_stack.central_url, follow_redirects=False, timeout=10.0) as session:
        yield session


@pytest.fixture(scope="module")
def review_ctx() -> dict:
    return {}


def _rules(html: str) -> dict[str, str]:
    """규칙 표의 한 줄 텍스트(`-->` 는 HTML 이스케이프를 되돌려) → rule_id."""
    return {html_unescape(text.strip()): rule_id for text, rule_id in _RULE_ROW.findall(html)}


def _executions(stack: LocalStack, task_id: str) -> list[tuple[str, str | None]]:
    """(status, failed_code) — 중앙 DB 의 executions 행. 실행 수·실패 코드 확인용."""
    conn = sqlite3.connect(stack.central_db)
    try:
        return conn.execute(
            "SELECT status, failed_code FROM executions WHERE task_id = ? ORDER BY attempt_no", (task_id,)
        ).fetchall()
    finally:
        conn.close()


def _wait_verdict(stack: LocalStack, task_id: str, timeout: float) -> dict:
    """중앙 워커가 그 업무의 활성 실행에 남긴 판정(`task_verdicts`). 화면에는 진단 판정만 보이므로 DB 로 읽는다."""
    deadline = time.monotonic() + timeout
    while True:
        conn = sqlite3.connect(stack.central_db)
        try:
            row = conn.execute(
                "SELECT v.verdict_json FROM task_verdicts v JOIN executions e ON e.execution_id = v.execution_id "
                "WHERE e.task_id = ? AND e.released_at IS NULL", (task_id,),
            ).fetchone()
        finally:
            conn.close()
        if row is not None:
            return json.loads(row[0])
        assert time.monotonic() < deadline, f"{task_id} 의 판정이 {timeout}초 안에 기록되지 않음"
        time.sleep(0.5)


def _stored_status(stack: LocalStack, task_id: str) -> tuple[str, str]:
    """워커가 남긴 저장 상태·이유 (`tasks.status`·`status_reason`). 화면은 마감 전엔 실시간 판정이라 여기서 읽는다."""
    conn = sqlite3.connect(stack.central_db)
    try:
        return tuple(conn.execute("SELECT status, status_reason FROM tasks WHERE task_id = ?", (task_id,)).fetchone())
    finally:
        conn.close()


def _run_a_then_wait_b_review(client: httpx.Client, task_a: str, task_b: str) -> None:
    """A 실행 → A `완료`(검증기) → B 가 내장 규칙으로 자동 착수 → `확인 필요 · 검토 대기` (test_04·05 와 같은 흐름)."""
    assert client.post(f"/tasks/{task_a}/run").status_code == 303
    seen = _watch(client, task_a, until=lambda label, _: label in ("완료", "실패", "확인 필요"), timeout=60)
    assert seen[-1][0] == "완료", seen
    seen = _watch(
        client, task_b, until=lambda label, reason: (label, reason) == _REVIEW_DONE or label in ("실패", "완료"),
        timeout=120,
    )
    assert seen[-1] == _REVIEW_DONE, seen
    _assert_in_order(_labels(seen), ["대기", "실행 요청됨", "실행 중", "확인 필요"])


def test_22_review_register_kind_and_rule_via_pages(reviewer, review_ctx):
    """종류 `review` 와 규칙 `code_change → review` 를 /kinds·/rules 폼으로 등록한다. 등록 전엔 내장 2종·내장 규칙 1개뿐."""
    _register_all(reviewer, CATALOG)
    _wait_agent(reviewer, "agent-claude-mac", "연결됨", timeout=30)
    page = reviewer.get("/kinds")
    assert page.status_code == 200
    assert page.text.count('class="tag-builtin"') == 2 and "review · review · repository_id" not in page.text
    assert set(_rules(page.text)) == {BUILTIN_RULE_TEXT}

    response = reviewer.post("/kinds", data=REVIEW_KIND_FORM)
    assert response.status_code == 303 and response.headers["location"] == "/kinds", response.text[:500]
    response = reviewer.post("/rules", data=REVIEW_RULE_FORM)
    assert response.status_code == 303 and response.headers["location"] == "/kinds", response.text[:500]

    page = reviewer.get("/kinds").text
    assert "review · review · repository_id" in page and 'action="/kinds/review/delete"' in page  # 사용자 정의 — 삭제 가능
    assert page.count('class="tag-builtin"') == 2
    assert REVIEW_KIND_FORM["instructions"] in page
    rules = _rules(page)
    assert set(rules) == {BUILTIN_RULE_TEXT, REVIEW_RULE_TEXT}, rules
    review_ctx["rule_id"] = rules[REVIEW_RULE_TEXT]
    # 업무 등록 폼의 종류 목록도 등록부에서 온다 — 세 번째 종류가 바로 보인다
    form = reviewer.get("/tasks/new").text
    assert "검토 (review) · review · repository_id" in form
    # 카탈로그의 Claude 능력 review 가 이 세션에서는 종류 라벨을 얻는다
    assert "review · repository_id=demo-report-repo</span> <span class=\"small\">(검토)</span>" in reviewer.get("/agents/agent-claude-mac").text


def test_23_review_register_a_b_then_review_task_c_after_b(reviewer, review_ctx):
    """A(진단)·B(코드 수정, 선행 A) 는 test_02·03 방식. C(검토, 선행 B) 는 능력 review 라 Claude 만 후보 — 자동 선택."""
    review_ctx["A"] = _create_task(reviewer, FORM_A)
    assert _status(_live(reviewer, review_ctx["A"])) == ("실행 가능", "agent-ops-demo 선택됨")
    review_ctx["B"] = _create_task(reviewer, {**FORM_B, "predecessor_task_id": review_ctx["A"]})
    _select(reviewer, review_ctx["B"], "agent-codex-mac")  # code.modify 는 후보 2개 — 수정은 Codex, 검토는 Claude 가 맡게
    assert _status(_live(reviewer, review_ctx["B"])) == ("대기", "선행 대기")

    review_ctx["C"] = _create_task(reviewer, {**FORM_C, "predecessor_task_id": review_ctx["B"]})
    html = _live(reviewer, review_ctx["C"])
    assert _status(html) == ("대기", "선행 대기")  # 선행 결과 대기
    assert "자동 선택 · agent-claude-mac" in html and "review · repository_id=demo-report-repo 일치 후보 1개" in html
    assert '<span class="chip kind-chip">검토</span>' in html
    assert f'href="/tasks/{review_ctx["B"]}"' in html  # 선행 칩


def test_24_review_a_then_b_then_c_start_without_human_click(reviewer, review_ctx):
    """A 실행 한 번 → A 완료 → B 자동 착수 → B 검토 대기 → **B 를 승인하기 전에** C 가 실행 요청됨 → 실행 중 → 확인 필요."""
    task_b, task_c = review_ctx["B"], review_ctx["C"]
    _run_a_then_wait_b_review(reviewer, review_ctx["A"], task_b)

    seen = _watch(
        reviewer, task_c, until=lambda label, reason: (label, reason) == _REVIEW_DONE or label in ("실패", "완료"),
        timeout=120,
    )
    assert seen[-1] == _REVIEW_DONE, seen
    _assert_in_order(_labels(seen), ["대기", "실행 요청됨", "실행 중", "확인 필요"])
    assert _status(_live(reviewer, task_b)) == _REVIEW_DONE  # B 는 여전히 사람 검토 전 — 승인이 후속 착수를 막지 않는다

    html = _live(reviewer, task_c)
    types = _EVENT_TYPE.findall(html)
    assert types[:2] == ["accepted", "started"] and types[-1] == "result_ready", types
    assert 'data-outcome="approved"' in html and "결과 봉투 · review" in html
    assert "대본 재생 (실제 모델 호출 없음)" in html
    assert "병합" not in html  # 사용자 정의 종류 — 검토 승인만, 병합 단계 없음


def test_25_review_c_inputs_and_result(chain_stack, reviewer, review_ctx):
    """C 산출물은 결과 봉투 + Claude 원시 로그뿐(코드 수정 산출물 없음). 인계 묶음은 규칙 handoff_kinds 대로, 선행은 code_change.
    중앙은 결과 봉투의 `outcome ∈ KindSpec.outcomes` 만 판정한다 — 내용은 보지 않는다 (ADR-0009 (5))."""
    task_b, task_c = review_ctx["B"], review_ctx["C"]
    chips = _chips(_live(reviewer, task_c))
    assert {"결과 봉투", "Claude JSONL", "Claude stderr"} <= set(chips), chips
    assert not ({"diff", "테스트 전", "테스트 후", "보고서", "수정 결과", "Codex JSONL"} & set(chips)), chips
    result = json.loads(_raw(reviewer, task_c, chips["결과 봉투"]))
    assert (result["kind"], result["outcome"], result["task_id"]) == ("review", "approved", task_c)
    assert "diff.patch" in result["summary"] and "code_change_result.json" in result["summary"], result["summary"]
    assert set(result["artifact_ids"]) == {chips["Claude JSONL"], chips["Claude stderr"]}  # 원시 로그만 — 파일 산출물 없음
    jsonl = _raw(reviewer, task_c, chips["Claude JSONL"])
    assert "scripted-claude" in jsonl and "scripted-demo-agent" in jsonl  # 실제 Claude 가 아니라 대본
    verdict = _wait_verdict(chain_stack, task_c, timeout=15)
    assert verdict["outcome"] == "passed"
    assert [(c["code"], c["passed"]) for c in verdict["checks"]] == [
        ("envelope_valid", True), ("ids_match", True), ("outcome_in_spec", True),
    ], verdict

    b_chips = _chips(_live(reviewer, task_b))
    assert "인계 묶음" in b_chips, b_chips
    bundle = json.loads(_raw(reviewer, task_b, b_chips["인계 묶음"]))
    assert bundle["source_kind"] == "code_change"
    assert {i["kind"] for i in bundle["inputs"]} == set(REVIEW_RULE_FORM["handoff_kinds"])
    assert bundle["source_result_artifact_id"] == b_chips["수정 결과"] and bundle["attachments"] == []
    review_ctx["B_result"] = _raw(reviewer, task_b, b_chips["수정 결과"])


def test_26_review_approve_b_then_c_and_no_duplicate(chain_stack, reviewer, review_ctx):
    """B 승인 → 완료(병합 대기), C 승인 → 완료(병합 없음). 워커가 더 돌아도 A·B·C 실행은 각각 하나."""
    task_a, task_b, task_c = review_ctx["A"], review_ctx["B"], review_ctx["C"]
    assert reviewer.post(f"/tasks/{task_b}/review", data={"decision": "approve", "comment": ""}).status_code == 303
    assert _status(_live(reviewer, task_b)) == ("완료", "검토 승인 · 병합: 운영자 확인 대기")
    assert reviewer.post(f"/tasks/{task_c}/review", data={"decision": "approve", "comment": ""}).status_code == 303
    assert _status(_live(reviewer, task_c)) == ("완료", "검토 승인")

    time.sleep(10)  # 중앙 워커 몇 바퀴 — 마감된 선행·후속에 실행을 다시 만들지 않는다
    assert {t: _executions(chain_stack, t) for t in (task_a, task_b, task_c)} == {
        task_a: [("result_ready", None)], task_b: [("result_ready", None)], task_c: [("result_ready", None)],
    }


def test_27_review_rule_removed_stops_new_succession(chain_stack, reviewer, review_ctx):
    """규칙을 지우면 A' → B' 는 내장 규칙으로 여전히 잇지만 C' 는 착수하지 않는다 — 이유 `후속 규칙 없음: code_change → review`."""
    assert reviewer.post(f"/rules/{review_ctx['rule_id']}/delete").status_code == 303
    assert set(_rules(reviewer.get("/kinds").text)) == {BUILTIN_RULE_TEXT}

    task_a = _create_task(reviewer, FORM_A)
    task_b = _create_task(reviewer, {**FORM_B, "predecessor_task_id": task_a})
    _select(reviewer, task_b, "agent-codex-mac")
    task_c = _create_task(reviewer, {**FORM_C, "predecessor_task_id": task_b})
    _run_a_then_wait_b_review(reviewer, task_a, task_b)

    time.sleep(7)  # 워커 두 바퀴 — B' 결과가 판정됐어도 규칙이 없으니 C' 실행을 만들지 않는다
    assert _status(_live(reviewer, task_c))[0] == "대기"
    assert _executions(chain_stack, task_c) == []
    status, reason = _stored_status(chain_stack, task_c)
    assert status == "대기" and reason.startswith("후속 규칙 없음: code_change → review"), (status, reason)
    assert "인계 묶음" not in _chips(_live(reviewer, task_b))  # 규칙이 없으면 묶음도 조립하지 않는다
    response = reviewer.post(f"/tasks/{task_c}/run")  # 직접 실행도 규칙 없이는 인계 자료가 없다
    assert response.status_code == 409 and "후속 규칙이 없어 인계 자료가 없습니다" in response.text
    review_ctx["C2"] = task_c


def test_28_review_demo_repo_untouched_by_review(chain_stack, review_ctx):
    """검토(C)는 읽기 전용 — main·task/{B} 는 B 의 결과 커밋 그대로, C 의 브랜치·worktree·인계 디렉터리 없음."""
    repo, task_b, task_c = chain_stack.repo_path, review_ctx["B"], review_ctx["C"]
    assert _git(repo, "rev-parse", "main") == chain_stack.base_commit
    assert _git(repo, "status", "--porcelain") == ""
    result_commit = re.search(r'"result_commit":\s*"([0-9a-f]{40})"', review_ctx["B_result"]).group(1)
    assert _git(repo, "rev-parse", f"task/{task_b}") == result_commit  # C 실행 뒤에도 B 의 결과 커밋 그대로
    assert _git(repo, "branch", "--list", f"task/{task_c}") == "" and task_c not in _git(repo, "worktree", "list")
    worktrees = repo.parent / f"{repo.name}-worktrees"
    assert not (worktrees / task_c).exists() and not (worktrees / f"{task_c}.handoff").exists()
    # 인계 디렉터리에 새 파일이 생겼다면 연결 프로그램이 `readonly_violation` 으로 실패시켰을 것이다 — C 는 result_ready 였다
    assert _executions(chain_stack, task_c) == [("result_ready", None)]


# --- n8n 입구·출구 (phase 7 step 8, ADR-0010) — 입구 토큰 → 쿠키 없는 POST → 대본 A → B → callback 1회 ------------------

# CONTRACT 12절 (a) 의 항목 2개. 라벨은 GitHub 시연 데이터 #41·#42 와 같다 — map_issue·compose 를 그대로 타므로 구성 결과도 같다
N8N_ITEMS = [
    {
        "key": "run-daily-0920",
        "title": "일일 보고서 2026-09-20 09:00 실행 실패",
        "body": "daily-report 의 daily-0920-0900 실행이 변환 단계에서 실패했습니다. 실패 원인과 수정에 필요한 근거를 조사해 주세요.",
        "labels": ["incident", "workflow:daily-report", "run:daily-0920-0900"],
        "blocked_by": [],
    },
    {
        "key": "fix-format",
        "title": "응답 형식 변경에 맞춰 보고서 변환 수정",
        "body": "진단 결과와 근거를 바탕으로 demo-report-repo 의 변환 코드를 수정하고 재현 테스트를 추가해 주세요.",
        "labels": ["bug", "repo:demo-report-repo"],
        "blocked_by": ["run-daily-0920"],
    },
]
N8N_CHAIN_TITLE = "일일 보고서 2026-09-20 09:00 실행 실패 → 응답 형식 변경에 맞춰 보고서 변환 수정"
_ISSUED_TOKEN = re.compile(
    r'<code id="issued-token">(wfs_[A-Za-z0-9_-]+)</code> · <span class="mono">(src-[0-9a-f]{8})</span>'
)
_CHAIN_LINK = re.compile(r'href="/chains/(chain-[0-9a-f]+)"')
CALLBACK_WAIT_SECONDS = 30.0  # 사람 차례가 된 뒤 워커 tick(3초) 몇 바퀴 여유


class _LoopbackServer(ThreadingHTTPServer):
    def server_bind(self):
        # HTTPServer.server_bind 는 socket.getfqdn(host) 로 역방향 DNS 를 조회한다 — macOS 에서 30초 넘게 걸릴 수 있다. 루프백이라 필요 없다
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[0], self.server_address[1]


class _CallbackReceiver:
    """n8n Wait 노드 역할 — 127.0.0.1 의 임시 포트에서 POST 를 받아 (path, headers, body) 를 쌓고 200 {"ok": true} 로 답한다.
    실패 모드는 두지 않는다 (재시도·중단은 워커 단위 테스트가 했다)."""

    def __init__(self):
        received: list[tuple[str, dict[str, str], dict]] = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802 — http.server 의 메서드 이름
                length = int(self.headers.get("content-length") or 0)
                body = json.loads(self.rfile.read(length) or b"null")
                received.append((self.path, {k.lower(): v for k, v in self.headers.items()}, body))
                payload = b'{"ok": true}'
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args):  # pytest 출력을 어지럽히지 않는다
                return

        self.received = received
        self._server = _LoopbackServer(("127.0.0.1", 0), Handler)  # 0.0.0.0 이 아니다 — 외부 접속을 열 이유가 없다
        self.port = self._server.server_address[1]
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    def url(self, path: str) -> str:
        return f"http://127.0.0.1:{self.port}{path}"

    def wait_for(self, count: int, timeout: float, interval: float = 0.3) -> None:
        deadline = time.monotonic() + timeout
        while len(self.received) < count:
            assert time.monotonic() < deadline, (
                f"{timeout}초 안에 callback {count}건이 오지 않음 (받은 것 {len(self.received)}건)"
            )
            time.sleep(interval)

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()


@pytest.fixture(scope="module")
def callback_receiver():
    receiver = _CallbackReceiver()
    yield receiver
    receiver.close()


@pytest.fixture(scope="module")
def n8n_client(chain_stack) -> httpx.Client:
    """n8n 절의 워크스페이스(세션) — 입구 토큰을 발급하고 체인 화면을 본다. 입구 API 호출은 쿠키 없이 `_inbound` 로 따로 한다."""
    with httpx.Client(base_url=chain_stack.central_url, follow_redirects=False, timeout=10.0) as session:
        yield session


@pytest.fixture(scope="module")
def n8n_ctx() -> dict:
    return {}


def _inbound(stack: LocalStack, token: str | None, body: dict) -> httpx.Response:
    """쿠키 없는 입구 API 호출 — 발신자는 Bearer 입구 토큰으로만 정해진다 (n8n HTTP Request 노드와 같은 모양)."""
    headers = {"Authorization": f"Bearer {token}"} if token is not None else {}
    return httpx.post(f"{stack.central_url}/sources/n8n/chains", headers=headers, json=body, timeout=10.0)


def _chain_row(stack: LocalStack, chain_id: str) -> dict:
    """중앙 DB 의 chains 행 — callback 열(`callback_sent_at`·`callback_attempts`·…)은 화면에 값 그대로 나오지 않으므로 DB 로 읽는다."""
    conn = sqlite3.connect(stack.central_db)
    conn.row_factory = sqlite3.Row
    try:
        return dict(conn.execute("SELECT * FROM chains WHERE chain_id = ?", (chain_id,)).fetchone())
    finally:
        conn.close()


def _chain_ids(client: httpx.Client) -> set[str]:
    return set(_CHAIN_LINK.findall(client.get("/tasks").text))


def test_29_n8n_sources_page_and_token_issue(chain_stack, n8n_client, n8n_ctx):
    """1. 입구 화면 — 입구 주소는 공개 주소(LocalStack 기본 = 중앙 웹 주소) + /sources/n8n/chains. 발급 응답에 원문 한 번, 다시 열면 없다."""
    _register_all(n8n_client, CATALOG)  # ops → codex → claude: prefer(등록 순) 규칙으로 코드 수정은 codex 가 기본 담당
    _wait_agent(n8n_client, "agent-codex-mac", "연결됨", timeout=30)

    page = n8n_client.get("/sources")
    assert page.status_code == 200
    assert f'<code id="inbound-url">{chain_stack.central_url}/sources/n8n/chains</code>' in page.text
    assert "아직 발급한 토큰이 없습니다." in page.text
    assert "허용된 host: <code>127.0.0.1</code>" in page.text  # LocalStack 기본 callback_hosts — 그 호스트의 모든 포트
    assert 'href="/sources" class="active">입구</a>' in page.text  # 사이드바

    issued = n8n_client.post("/sources/tokens", data={"label": "e2e"})
    assert issued.status_code == 200, issued.text[:500]
    match = _ISSUED_TOKEN.search(issued.text)
    assert match, issued.text[:800]
    n8n_ctx["token"], n8n_ctx["token_id"] = match.group(1), match.group(2)
    assert "이 값은 다시 볼 수 없습니다" in issued.text

    again = n8n_client.get("/sources").text
    assert n8n_ctx["token"] not in again  # 원문은 발급 응답 한 번뿐 — 서버에는 sha256 만
    assert f'data-token-id="{n8n_ctx["token_id"]}"' in again and "<td>e2e</td>" in again and "<td>없음</td>" in again


def test_30_n8n_post_creates_chain_and_starts_first_task(chain_stack, callback_receiver, n8n_client, n8n_ctx):
    """2. n8n HTTP Request 노드 역할 — 쿠키 없이 Bearer 만으로 POST. 201, 진단은 접수 즉시 실행 요청됨, 수정은 대기. 체인 화면은 callback 대기."""
    body = {"contract_version": 1, "items": N8N_ITEMS, "callback_url": callback_receiver.url("/webhook-waiting/e2e")}
    n8n_ctx["body"] = body
    response = _inbound(chain_stack, n8n_ctx["token"], body)
    assert response.status_code == 201, response.text
    data = InboundChainResponse.model_validate(response.json())  # 계약 왕복
    assert data.started is True and data.start_error is None and data.skipped == []
    assert data.chain_url == f"{chain_stack.central_url}/chains/{data.chain_id}"
    assert [(t.key, t.kind, t.status) for t in data.tasks] == [
        ("run-daily-0920", "diagnosis", "실행 요청됨"), ("fix-format", "code_change", "대기"),
    ]
    assert n8n_ctx["token"] not in response.text
    n8n_ctx["chain"] = data.chain_id
    n8n_ctx["A"], n8n_ctx["B"] = (t.task_id for t in data.tasks)

    html = _chain_page(n8n_client, data.chain_id)
    assert f"워크플로우<span class=\"sep\">/</span>{N8N_CHAIN_TITLE}" in html
    assert '<span class="chip">n8n</span>' in html and "시연 데이터" not in html  # 입구 항목은 fixture 가 아니다
    assert f'data-callback-state="대기">callback · 127.0.0.1:{callback_receiver.port} · 대기(사람 차례가 되면 보냄)' in html
    assert "webhook-waiting" not in html  # URL 전체는 찍지 않는다
    node_b = _node_bodies(html)[n8n_ctx["B"]]
    assert "일치 후보 2개" in node_b and "먼저 등록한 agent-codex-mac" in node_b  # items_json 으로 구성 이유 재계산
    assert f'action="/tasks/{n8n_ctx["B"]}/run"' not in html  # 뒤 노드는 워커가 잇는다
    assert "n8n · 0/2 완료" in re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", n8n_client.get("/tasks").text))
    assert callback_receiver.received == []
    _wait_agent(n8n_client, "agent-codex-mac", "연결됨", timeout=30)


def test_31_n8n_a_then_b_run_without_human_action_and_b_waits_for_review(n8n_client, n8n_ctx):
    """3. 사람 조작 없이 진단 완료 → 코드 수정 자동 착수 → 확인 필요 · 검토 대기 (test_16·17 과 같은 시간 한도). 실제 모델·Codex 아님."""
    chain_id, task_a, task_b = n8n_ctx["chain"], n8n_ctx["A"], n8n_ctx["B"]
    seen = _watch_chain(
        n8n_client, chain_id, task_a, until=lambda label, _: label in ("완료", "실패", "확인 필요"), timeout=60,
    )
    label, reason = seen[-1]
    assert label == "완료" and reason.startswith("판정 근거: "), seen
    assert re.search(r'"model_id":\s*"fake-fixture-script"', _raw(n8n_client, task_a, _chips(_live(n8n_client, task_a))["진단 결과"]))

    seen = _watch_chain(
        n8n_client, chain_id, task_b,
        until=lambda label, reason: (label, reason) == ("확인 필요", "검토 대기") or label in ("실패", "완료"),
        timeout=120,
    )
    assert seen[-1] == ("확인 필요", "검토 대기"), seen
    _assert_in_order(_labels(seen), ["대기", "실행 요청됨", "실행 중", "확인 필요"])
    html = _chain_page(n8n_client, chain_id, live=True)
    assert _gate(html) == ("확인 필요", "검토 대기") and 'data-poll="0"' in html  # 사람 차례
    detail = _live(n8n_client, task_b)
    assert 'data-outcome="ready_for_review"' in detail and "대본 재생 (실제 모델 호출 없음)" in detail
    chips = _chips(detail)
    n8n_ctx["B_result"] = _assert_fix_artifacts(n8n_client, task_b, chips)
    assert "scripted-codex" in _raw(n8n_client, task_b, chips["Codex JSONL"])  # 실제 Codex 가 아니라 대본


def test_32_n8n_callback_arrives_once_with_contract_body(chain_stack, callback_receiver, n8n_client, n8n_ctx):
    """4. 사람 차례가 된 tick 의 마지막에 ChainCallback 1건 — 계약 왕복, 값, JSON 헤더. 체인 화면은 `전송됨`, DB 는 attempts 0."""
    chain_id, task_a_id, task_b_id = n8n_ctx["chain"], n8n_ctx["A"], n8n_ctx["B"]
    callback_receiver.wait_for(1, timeout=CALLBACK_WAIT_SECONDS)
    path, headers, body = callback_receiver.received[0]
    assert path == "/webhook-waiting/e2e"
    assert headers["content-type"] == "application/json"
    callback = ChainCallback.model_validate(body)
    assert (callback.contract_version, callback.chain_id, callback.source) == (1, chain_id, "n8n")
    assert callback.title == N8N_CHAIN_TITLE
    assert callback.chain_url == f"{chain_stack.central_url}/chains/{chain_id}"
    assert (callback.human_gate.label, callback.human_gate.status_label, callback.human_gate.reason) == (
        "검토 승인 (사람) · 병합은 운영자 확인", "확인 필요", "검토 대기",
    )
    task_a, task_b = callback.tasks
    assert (task_a.task_id, task_a.key, task_a.kind, task_a.title) == (task_a_id, "run-daily-0920", "diagnosis", N8N_ITEMS[0]["title"])
    assert task_a.status == "완료" and task_a.status_reason.startswith("판정 근거: ")
    assert task_a.outcome == "ready_for_handoff" and task_a.summary
    assert (task_b.task_id, task_b.key, task_b.kind, task_b.title) == (task_b_id, "fix-format", "code_change", N8N_ITEMS[1]["title"])
    assert (task_b.status, task_b.status_reason) == ("확인 필요", "검토 대기")
    assert task_b.outcome == "ready_for_review" and task_b.summary
    assert re.search(rf'"summary":\s*"{re.escape(task_b.summary)}"', n8n_ctx["B_result"])  # 수정 결과 봉투의 summary 그대로
    for task in callback.tasks:
        assert task.task_url == f"{chain_stack.central_url}/tasks/{task.task_id}"
    assert n8n_ctx["token"] not in json.dumps(body)

    html = _chain_page(n8n_client, chain_id)
    assert f'data-callback-state="전송됨">callback · 127.0.0.1:{callback_receiver.port} · 전송됨' in html
    row = _chain_row(chain_stack, chain_id)
    assert row["callback_sent_at"] is not None
    assert (row["callback_attempts"], row["callback_next_at"], row["callback_last_error"]) == (0, None, None)
    n8n_ctx["callback_sent_at"] = row["callback_sent_at"]


def test_33_n8n_no_second_callback_after_approve(chain_stack, callback_receiver, n8n_client, n8n_ctx):
    """5. 승인 뒤 워커가 세 바퀴 넘게 더 돌아도 callback 은 1건 그대로 — 체인당 1회 (n8n Wait 노드는 한 번만 깨어난다)."""
    chain_id, task_b = n8n_ctx["chain"], n8n_ctx["B"]
    assert n8n_client.post(f"/tasks/{task_b}/review", data={"decision": "approve", "comment": ""}).status_code == 303
    assert _status(_live(n8n_client, task_b)) == ("완료", "검토 승인 · 병합: 운영자 확인 대기")
    assert _gate(_chain_page(n8n_client, chain_id)) == ("완료", "병합: 운영자 확인 대기")

    time.sleep(10)  # 중앙 워커 3바퀴 이상 (3초 간격) — 승인으로 바뀐 상태를 다시 판정해도 보내지 않는다
    assert len(callback_receiver.received) == 1
    row = _chain_row(chain_stack, chain_id)
    assert row["callback_sent_at"] == n8n_ctx["callback_sent_at"] and row["callback_attempts"] == 0
    assert _executions(chain_stack, task_b) == [("result_ready", None)]


def test_34_n8n_rejections_and_chain_without_callback(chain_stack, callback_receiver, n8n_client, n8n_ctx):
    """6. 거부 경로 — (a) 허용 밖 host 422·체인 안 생김, (c) Bearer 없음 401, (d) callback_url 없이 201·callback 없이 진행,
    (b) 토큰 취소 뒤 같은 POST 401. (d) 가 토큰을 쓰므로 취소는 마지막에 한다. 앞 체인은 test_33 에서 마감돼 활성 업무는 2개다."""
    token, body = n8n_ctx["token"], n8n_ctx["body"]
    chains_before = _chain_ids(n8n_client)
    assert chains_before == {n8n_ctx["chain"]}

    # (a) 허용 목록(127.0.0.1) 밖 — 접수 전에 거부, 체인 없음
    response = _inbound(chain_stack, token, {**body, "callback_url": "http://example.com/x"})
    assert response.status_code == 422, response.text
    error = response.json()
    assert (error["code"], error["field"], error["details"]) == (
        "callback_host_not_allowed", "callback_url", {"allowed": ["127.0.0.1"]},
    )
    assert "example.com" in error["message"]
    assert _chain_ids(n8n_client) == chains_before

    # (c) Bearer 없음 — 세션 쿠키가 있어도 통과하지 않는다 (브라우저 CSRF 경로 없음)
    response = n8n_client.post("/sources/n8n/chains", json=body)
    assert response.status_code == 401 and response.json()["code"] == "unauthenticated", response.text
    assert _inbound(chain_stack, None, body).status_code == 401
    assert _chain_ids(n8n_client) == chains_before

    # (d) callback_url 없이 — 접수·즉시 시작은 같고 callback 만 없다
    without = {key: value for key, value in body.items() if key != "callback_url"}
    response = _inbound(chain_stack, token, without)
    assert response.status_code == 201, response.text
    data = InboundChainResponse.model_validate(response.json())
    assert data.started is True and [t.status for t in data.tasks] == ["실행 요청됨", "대기"]
    n8n_ctx["chain2"] = data.chain_id
    _, n8n_ctx["B2"] = (t.task_id for t in data.tasks)
    html = _chain_page(n8n_client, data.chain_id)
    assert '<span class="chip">n8n</span>' in html and "callback" not in html
    assert _chain_row(chain_stack, data.chain_id)["callback_url"] is None
    assert _chain_ids(n8n_client) == chains_before | {data.chain_id}

    # (b) 토큰 취소 → 다음 요청부터 401
    response = n8n_client.post(f"/sources/tokens/{n8n_ctx['token_id']}/revoke")
    assert response.status_code == 303 and response.headers["location"] == "/sources"
    sources = n8n_client.get("/sources").text
    assert "취소됨" in sources and "<td>e2e</td>" in sources
    response = _inbound(chain_stack, token, body)
    assert response.status_code == 401 and response.json()["code"] == "unauthenticated", response.text
    assert _chain_ids(n8n_client) == chains_before | {data.chain_id}

    # (d) 의 체인은 callback 없이 A → B 가 그대로 이어지고, 사람 차례가 돼도 수신기에는 아무것도 오지 않는다
    seen = _watch_chain(
        n8n_client, data.chain_id, n8n_ctx["B2"],
        until=lambda label, reason: (label, reason) == ("확인 필요", "검토 대기") or label in ("실패", "완료"),
        timeout=120,
    )
    assert seen[-1] == ("확인 필요", "검토 대기"), seen
    time.sleep(7)  # 워커 두 바퀴 — callback_url 이 없는 체인은 chains_awaiting_callback 에 들지 않는다
    assert len(callback_receiver.received) == 1
    row = _chain_row(chain_stack, data.chain_id)
    assert (row["callback_sent_at"], row["callback_attempts"]) == (None, 0)


def test_35_n8n_other_session_cannot_see_the_chain_or_token(chain_stack, n8n_client, n8n_ctx):
    """7. 다른 세션은 이 체인·업무·토큰을 보지 못한다 (test_09·20 방식). 발급한 세션의 홈에는 두 체인이 보인다."""
    with httpx.Client(base_url=chain_stack.central_url, follow_redirects=False, timeout=10.0) as other:
        assert other.get(f"/chains/{n8n_ctx['chain']}").status_code == 404
        assert other.get(f"/chains/{n8n_ctx['chain2']}").status_code == 404
        assert other.get(f"/tasks/{n8n_ctx['B']}").status_code == 404
        home = other.get("/tasks").text
        assert "아직 업무가 없습니다." in home and f'href="/chains/{n8n_ctx["chain"]}"' not in home
        sources = other.get("/sources").text
        assert n8n_ctx["token_id"] not in sources and "아직 발급한 토큰이 없습니다." in sources
        assert other.post(f"/sources/tokens/{n8n_ctx['token_id']}/revoke").status_code == 404  # 존재를 알리지 않는다
    assert _chain_ids(n8n_client) == {n8n_ctx["chain"], n8n_ctx["chain2"]}
