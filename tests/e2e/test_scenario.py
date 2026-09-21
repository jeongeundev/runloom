"""전체 시나리오 — 심사자 흐름을 HTTP 로 재현한다. 두 경로가 한 스택(`conftest.stack`, 대본 에이전트) 위에서 이어진다.

- test_01 ~ test_11: **직접 등록 경로** — 카탈로그 등록 → 시연 업무 A·B 를 폼으로 등록 → A 실행 → B 자동 착수 → 검토 승인 →
  저장소 검사 → 연결 프로그램 오프라인. PRD "시연 흐름" 1~7 의 원래 순서.
- test_12 ~ test_21: **주 경로** (phase 5, UI_GUIDE "심사자 첫 방문 흐름") — 랜딩 → 에이전트 등록(카탈로그 3개) → 업무 가져오기
  (GitHub fixture) → 워크플로우 확인(순서·담당·이유) → 시작 → A 완료 → B 자동 착수 → 검토 승인 → 저장소 검사 → Jira·다른 세션
  → Claude 먼저 등록한 세션(대본 Claude 경로, `slow`).

`WORKFLOW_E2E=1` 일 때만 돈다 (verify.sh 가 매 턴 도는 pytest 에서 제외). 실제 Codex·Claude·OpenAI 는 호출하지 않는다:
진단은 `DIAG_MODEL=fake`(fixture 대본), 코드 수정은 `workflow.scripted.codex`/`.claude` 래퍼(`LocalStack(scripted=True)`).
5개 프로세스는 `scripts/local_stack.py` 가 띄운다.

테스트 함수는 번호 순으로 이어지며 앞 단계의 결과(`ctx`·`chain_ctx`)를 쓴다 — `-x` 로 첫 실패에서 멈춘다.
"""

import os
import re
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from local_stack import LocalStack  # noqa: E402

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
