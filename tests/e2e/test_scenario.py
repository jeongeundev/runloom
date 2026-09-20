"""전체 시나리오 — PRD "시연 흐름" 1~7 과 UI_GUIDE "심사자 첫 방문 흐름" 을 HTTP 로 재현한다.

`WORKFLOW_E2E=1` 일 때만 돈다 (verify.sh 가 매 턴 도는 pytest 에서 제외). 실제 Codex·OpenAI 는 호출하지 않는다:
진단은 `DIAG_MODEL=fake`(fixture 대본), B 는 `tests/e2e/fake_codex.py`(`workflow.scripted.codex` shim) —
`WORKFLOW_E2E_SCRIPTED=1` 이면 `LocalStack(scripted=True)` 로 `codex`·`claude` 래퍼 경로를 대신 검증한다.
5개 프로세스는 `scripts/local_stack.py` 가 띄운다.

테스트 함수는 번호 순으로 이어지며 앞 단계의 결과(`ctx`)를 쓴다 — `-x` 로 첫 실패에서 멈춘다.
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

FAKE_CODEX = Path(__file__).with_name("fake_codex.py")

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
def stack(tmp_path_factory) -> LocalStack:
    scripted = os.environ.get("WORKFLOW_E2E_SCRIPTED") == "1"
    with LocalStack(tmp_path_factory.mktemp("stack"), fake_codex=FAKE_CODEX, scripted=scripted) as running:
        yield running


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


def test_01b_register_two_catalog_agents_and_see_them_connected(client):
    """심사자 흐름 1단계 — 운영자 카탈로그(진단 API·개인 Codex)에서 둘을 등록한다."""
    catalog = client.get("/agents/register")
    assert catalog.status_code == 200
    assert "운영 진단 데모" in catalog.text and "개인 Codex" in catalog.text
    for agent_id in ("agent-ops-demo", "agent-codex-mac"):
        response = client.post("/agents/register", data={"agent_id": agent_id})
        assert response.status_code == 303, response.text[:300]
    assert set(_agent_states(client.get("/tasks").text)) == {"agent-ops-demo", "agent-codex-mac"}
    # connector run 이 heartbeat 를 보낸 뒤 연결됨 (등록 보고 직후에도 online 이지만 heartbeat 로 유지된다)
    _wait_agent(client, "agent-codex-mac", "연결됨", timeout=20)


def test_02_register_diagnosis_task_a_is_runnable(client, ctx):
    ctx["A"] = _create_task(client, FORM_A)
    label, reason = _status(_live(client, ctx["A"]))
    assert label == "실행 가능"
    assert reason == "agent-ops-demo 선택됨"
    assert "자동 선택 · agent-ops-demo" in _live(client, ctx["A"])


def test_03_register_fix_task_b_waits_for_predecessor(client, ctx):
    ctx["B"] = _create_task(client, {**FORM_B, "predecessor_task_id": ctx["A"]})
    assert _status(_live(client, ctx["B"])) == ("대기", "선행 대기")


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


def test_11_offline_connector_leaves_new_fix_task_waiting(stack, client, ctx):
    stack.stop_service("connector")
    assert not stack.running("connector")
    # heartbeat 10초 규칙 (LocalStack 의 WORKFLOW_LIMIT_HEARTBEAT_OFFLINE_SECONDS) — 워커·화면 모두 같은 값을 읽는다.
    # 오프라인이 되기 전에 등록하면 워커가 (아직 online 으로 보이는) 에이전트에 실행을 만들 수 있으므로 먼저 기다린다
    _wait_agent(client, "agent-codex-mac", "연결 끊김", timeout=100)

    ctx["C"] = _create_task(client, {**FORM_B, "predecessor_task_id": ctx["A"]})
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
