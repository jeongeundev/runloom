"""inbound_api.py — n8n 입구 `POST /sources/n8n/chains`. CONTRACT 12절(요청·응답·오류표), ADR-0010 결정 1·2·3·5.

인증은 세션이 발급한 입구 토큰(wfs_)만이다 — 세션 쿠키·연결 토큰으로는 통과하지 않는다.
허용 목록·공개 주소는 conftest 의 `settings` 를 `dataclasses.replace` 로 바꿔 쓴다(기존 fixture 는 그대로).
"""

import dataclasses
import json

import pytest
from fastapi.testclient import TestClient

from workflow.adapters import repo
from workflow.contracts.v1 import InboundChainResponse
from workflow.server.app import create_app
from workflow.server.auth import SESSION_COOKIE, utc_now

from .conftest import NOW, SESSION, bearer, exchange, register_catalog, seed_agents, task_row

PUBLIC_URL = "http://127.0.0.1:8000"
CALLBACK = "http://localhost:5678/webhook-waiting/1234"

DIAGNOSE_ITEM = {
    "key": "run-daily-0920",
    "title": "일일 보고서 2026-09-20 09:00 실행 실패",
    "body": "daily-report 의 daily-0920-0900 실행이 변환 단계에서 실패했습니다. 실패 원인과 수정에 필요한 근거를 조사해 주세요.",
    "labels": ["incident", "workflow:daily-report", "run:daily-0920-0900"],
    "blocked_by": [],
}
FIX_ITEM = {
    "key": "fix-format",
    "title": "응답 형식 변경에 맞춰 보고서 변환 수정",
    "body": "진단 결과와 근거를 바탕으로 demo-report-repo 의 변환 코드를 수정하고 재현 테스트를 추가해 주세요.",
    "labels": ["bug", "repo:demo-report-repo"],
    "blocked_by": ["run-daily-0920"],
}
DOCS_ITEM = {
    "key": "readme",
    "title": "README 오타 수정",
    "body": "오타를 고쳐 주세요.",
    "labels": ["docs"],
    "blocked_by": [],
}

UNAUTHENTICATED = {
    "code": "unauthenticated", "message": "유효한 입구 토큰이 필요합니다.", "field": None, "details": None,
}


@pytest.fixture
def settings(settings):
    """conftest 의 settings 에 허용 목록(localhost:5678)과 공개 주소를 더한다 — app·client·conn 이 이것을 쓴다."""
    return dataclasses.replace(settings, callback_hosts=("localhost:5678",), public_url=PUBLIC_URL)


@pytest.fixture
def workspace(conn) -> str:
    """세션 1개 + 카탈로그 2개 등록(codex → ops 순, conftest 기본). 업무는 없다."""
    repo.create_session(conn, SESSION, NOW)
    seed_agents(conn)
    register_catalog(conn, SESSION)
    return SESSION


def _issue(conn, session_id: str) -> str:
    """입구 토큰 원문 — 발급 응답에서만 나온다."""
    return repo.issue_source_token(conn, session_id, "n8n", "n8n 테스트", NOW)[1]


@pytest.fixture
def token(conn, workspace) -> str:
    return _issue(conn, workspace)


def post(client, token: str | None, *, source: str = "n8n", body: dict | None = None, **overrides):
    """기본 본문은 진단 + 수정 2항목 + 허용된 callback_url. `body` 를 주면 그대로 보낸다."""
    if body is None:
        body = {"contract_version": 1, "items": [DIAGNOSE_ITEM, FIX_ITEM], "callback_url": CALLBACK, **overrides}
    headers = bearer(token) if token is not None else {}
    return client.post(f"/sources/{source}/chains", json=body, headers=headers)


# --- 인증 (a) ------------------------------------------------------------------------------------


def test_without_bearer_is_401_even_with_session_cookie(client, conn, workspace):
    assert post(client, None).status_code == 401
    assert post(client, None).json() == UNAUTHENTICATED
    # 세션 쿠키가 있어도 입구는 Bearer 만 받는다 (브라우저 CSRF 경로를 만들지 않는다)
    assert client.get("/tasks").status_code == 200 and client.cookies.get(SESSION_COOKIE)
    assert post(client, None).status_code == 401
    # 연결 토큰(wfc_)도 아니다
    connector_token = exchange(client, conn)[1]
    assert post(client, connector_token).status_code == 401
    assert repo.list_chains(conn, workspace) == []


def test_unknown_or_revoked_token_is_401_without_echoing_the_token(client, conn, workspace):
    response = post(client, "wfs_not-a-real-token")
    assert response.status_code == 401 and response.json() == UNAUTHENTICATED
    token_id, token = repo.issue_source_token(conn, workspace, "n8n", "취소될 토큰", NOW)
    assert post(client, token).status_code == 201
    repo.revoke_source_token(conn, workspace, token_id, "2026-09-22T00:00:00Z")
    response = post(client, token)
    assert response.status_code == 401 and response.json() == UNAUTHENTICATED
    assert token not in response.text


def test_source_other_than_n8n_is_404(client, conn, token, workspace):
    response = post(client, token, source="github")
    assert response.status_code == 404
    assert response.json()["code"] == "not_found"
    assert repo.list_chains(conn, workspace) == []


def test_token_bound_to_another_source_is_403(client, conn, token, workspace):
    """토큰은 발급 때의 source 하나에 묶인다. 지금은 n8n 뿐이라 CHECK 를 잠시 끄고 행을 바꿔 만든다(테스트 전용)."""
    conn.execute("PRAGMA ignore_check_constraints=ON")
    conn.execute("UPDATE source_tokens SET source = 'github'")
    conn.execute("PRAGMA ignore_check_constraints=OFF")
    response = post(client, token)
    assert response.status_code == 403
    assert response.json() == {
        "code": "forbidden", "message": "이 토큰은 n8n 입구에 쓸 수 없습니다.", "field": None, "details": None,
    }
    assert repo.list_chains(conn, workspace) == []


# --- 정상 접수 (b) ------------------------------------------------------------------------------


def test_two_items_build_chain_and_start_first_task(client, conn, token, workspace):
    response = post(client, token)
    assert response.status_code == 201, response.text
    body = response.json()
    assert list(body) == ["contract_version", "chain_id", "chain_url", "started", "start_error", "tasks", "skipped"]
    InboundChainResponse.model_validate(body)  # 계약 왕복
    chain_id = body["chain_id"]
    assert chain_id.startswith("chain-")
    assert body["chain_url"] == f"{PUBLIC_URL}/chains/{chain_id}"
    assert body["started"] is True and body["start_error"] is None
    assert body["skipped"] == []
    assert [t["key"] for t in body["tasks"]] == ["run-daily-0920", "fix-format"]
    assert [t["kind"] for t in body["tasks"]] == ["diagnosis", "code_change"]
    assert [t["status"] for t in body["tasks"]] == ["실행 요청됨", "대기"]

    chain = repo.get_chain(conn, chain_id)
    assert chain["session_id"] == workspace and chain["source"] == "n8n"
    assert chain["title"] == "일일 보고서 2026-09-20 09:00 실행 실패 → 응답 형식 변경에 맞춰 보고서 변환 수정"
    assert chain["callback_url"] == CALLBACK
    assert json.loads(chain["items_json"]) == [DIAGNOSE_ITEM, FIX_ITEM]
    assert chain["started_at"] is not None
    assert chain["callback_sent_at"] is None and chain["callback_attempts"] == 0

    task_a, task_b = repo.tasks_of_chain(conn, chain_id)
    assert [t["task_id"] for t in (task_a, task_b)] == [t["task_id"] for t in body["tasks"]]
    assert task_a["source_ref"] == "run-daily-0920" and task_a["request"] == DIAGNOSE_ITEM["body"]
    assert task_a["kind"] == "diagnosis" and task_a["completion_mode"] == "auto"
    assert json.loads(task_a["target_json"]) == {"run_id": "daily-0920-0900"}
    assert task_b["predecessor_task_id"] == task_a["task_id"] and task_b["run_mode"] == "auto"
    assert task_b["status"] == "대기" and task_b["status_reason"] == "선행 대기"
    assert repo.get_selection(conn, task_a["task_id"]).selected_agent_id == "agent-ops-demo"
    assert repo.get_selection(conn, task_b["task_id"]).selected_agent_id == "agent-codex-mac"
    execution = repo.active_execution(conn, task_a["task_id"])
    assert execution is not None and execution["status"] == "queued"
    assert repo.active_execution(conn, task_b["task_id"]) is None  # 후속은 워커가 잇는다
    (row,) = repo.list_source_tokens(conn, workspace)
    assert row["last_used_at"] is not None
    assert token not in response.text  # 토큰 원문은 응답에 없다


def test_item_without_capability_is_skipped_with_reason(client, conn, token):
    response = post(client, token, items=[DIAGNOSE_ITEM, FIX_ITEM, DOCS_ITEM])
    assert response.status_code == 201, response.text
    body = response.json()
    assert [t["key"] for t in body["tasks"]] == ["run-daily-0920", "fix-format"]
    assert body["skipped"] == [{"key": "readme", "reason": "맞는 능력 코드 없음 (라벨: docs)"}]
    chain = repo.get_chain(conn, body["chain_id"])
    assert [s["key"] for s in json.loads(chain["skipped_json"])] == ["readme"]
    assert len(json.loads(chain["items_json"])) == 3  # 원문은 skipped 까지 전부


def test_chain_url_is_null_without_public_url(settings, conn, workspace):
    app = create_app(dataclasses.replace(settings, public_url=""))
    response = post(TestClient(app), _issue(conn, workspace))
    assert response.status_code == 201, response.text
    assert response.json()["chain_url"] is None


def test_without_callback_url_chain_has_null_callback(client, conn, token):
    response = post(client, token, body={"contract_version": 1, "items": [DIAGNOSE_ITEM, FIX_ITEM]})
    assert response.status_code == 201, response.text
    chain = repo.get_chain(conn, response.json()["chain_id"])
    assert chain["callback_url"] is None
    assert json.loads(chain["items_json"]) == [DIAGNOSE_ITEM, FIX_ITEM]


def test_same_request_twice_makes_two_chains(client, conn, token, workspace):
    first, second = post(client, token), post(client, token)
    assert first.status_code == 201 and second.status_code == 201
    assert first.json()["chain_id"] != second.json()["chain_id"]
    assert len(repo.list_chains(conn, workspace)) == 2  # 멱등 키는 없다 (문서화된 동작)
    assert len(repo.list_tasks(conn, workspace)) == 4


# --- 첫 업무 시작 거부 — 체인은 남고 started=false (c) -------------------------------------------


def test_no_registered_agent_is_422_without_chain(client, conn):
    repo.create_session(conn, SESSION, NOW)
    seed_agents(conn)  # 카탈로그만 있고 세션은 아무것도 등록하지 않았다
    response = post(client, _issue(conn, SESSION))
    assert response.status_code == 422
    assert response.json() == {
        "code": "agent_not_registered", "message": "에이전트를 먼저 등록하세요.", "field": "agent_id", "details": None,
    }
    assert repo.list_chains(conn, SESSION) == [] and repo.list_tasks(conn, SESSION) == []


def test_first_task_without_candidate_returns_201_started_false_selection_required(client, conn):
    """진단 Agent 미등록 → 첫 업무 후보 없음. 체인·Task 는 남고 사람이 chain_url 에서 확정한다."""
    repo.create_session(conn, SESSION, NOW)
    seed_agents(conn)
    register_catalog(conn, SESSION, "agent-codex-mac")
    response = post(client, _issue(conn, SESSION))
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["started"] is False
    assert body["start_error"] == {
        "code": "selection_required", "message": "담당 에이전트를 먼저 확정하세요.", "field": None, "details": None,
    }
    assert [t["status"] for t in body["tasks"]] == ["확인 필요", "대기"]
    chain = repo.get_chain(conn, body["chain_id"])
    assert chain["started_at"] is None and chain["callback_url"] == CALLBACK
    task_a, task_b = repo.tasks_of_chain(conn, body["chain_id"])
    assert repo.get_selection(conn, task_a["task_id"]).status == "needs_selection"
    assert repo.active_execution(conn, task_a["task_id"]) is None


def test_diagnosis_daily_limit_returns_201_started_false_with_429_body(client, conn, token, workspace):
    for n in range(10):
        repo.record_diagnosis_start(conn, workspace, f"exec-seed-{n}", utc_now())
    response = post(client, token)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["started"] is False
    assert body["start_error"]["code"] == "daily_limit_reached"
    assert body["start_error"]["message"] == "오늘 이 세션의 진단 실행 한도(10회)에 도달했습니다."
    assert body["start_error"]["details"]["limit"] == 10
    assert [t["status"] for t in body["tasks"]] == ["실행 가능", "대기"]
    assert repo.get_chain(conn, body["chain_id"])["started_at"] is None
    assert repo.active_execution(conn, body["tasks"][0]["task_id"]) is None


# --- callback_url 허용 목록 (d)·(e) ------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "http://evil.example/hook",
        "http://127.0.0.1:8100/runs",  # 내부 진단 API — 허용 목록 밖
        "http://localhost:9999/hook",  # 같은 호스트, 다른 포트
        "http://user@localhost:5678/hook",  # userinfo
    ],
)
def test_callback_host_outside_allow_list_is_422_without_chain(client, conn, token, workspace, url):
    response = post(client, token, callback_url=url)
    assert response.status_code == 422, response.text
    body = response.json()
    assert body["code"] == "callback_host_not_allowed" and body["field"] == "callback_url"
    assert body["details"] == {"allowed": ["localhost:5678"]}
    assert "허용 목록에 없습니다" in body["message"]
    assert repo.list_chains(conn, workspace) == [] and repo.list_tasks(conn, workspace) == []


def test_empty_allow_list_rejects_any_callback_url(settings, conn, workspace):
    """공개 데모 구성 — WORKFLOW_CALLBACK_HOSTS 가 비면 callback 을 받지 않는다. callback_url 없는 접수는 된다."""
    client = TestClient(create_app(dataclasses.replace(settings, callback_hosts=())))
    token = _issue(conn, workspace)
    response = post(client, token, callback_url=CALLBACK)
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "callback_host_not_allowed" and body["details"] == {"allowed": []}
    assert "비어" in body["message"]
    assert repo.list_chains(conn, workspace) == []
    ok = post(client, token, body={"contract_version": 1, "items": [DIAGNOSE_ITEM, FIX_ITEM]})
    assert ok.status_code == 201


# --- 본문 검증 (f) -------------------------------------------------------------------------------


def test_unknown_field_and_bad_contract_version_are_422(client, conn, token, workspace):
    response = post(client, token, extra="x")
    assert response.status_code == 422
    assert response.json()["code"] == "unknown_field" and response.json()["field"] == "extra"
    response = post(client, token, contract_version=2)
    assert response.status_code == 422
    assert response.json()["code"] == "unsupported_contract_version"
    response = post(client, token, body={"contract_version": 1, "items": []})
    assert response.status_code == 422 and response.json()["code"] == "invalid_field"
    response = post(client, token, callback_url="ftp://localhost:5678/hook")
    assert response.status_code == 422 and response.json()["code"] == "invalid_field"
    assert response.json()["field"] == "callback_url"
    assert repo.list_chains(conn, workspace) == []


def test_blocked_by_cycle_is_422_dependency_cycle(client, conn, token, workspace):
    a = {**DIAGNOSE_ITEM, "blocked_by": ["fix-format"]}
    response = post(client, token, items=[a, FIX_ITEM])
    assert response.status_code == 422, response.text
    body = response.json()
    assert body["code"] == "dependency_cycle" and body["field"] == "items"
    assert "run-daily-0920" in body["message"] and "fix-format" in body["message"]
    assert repo.list_chains(conn, workspace) == []


# --- 활성 업무 상한 (g) — 체인을 만들기 전에 검사한다 ------------------------------------------


def test_active_task_limit_is_429_before_chain(app, client, conn, token, workspace):
    limit = app.state.settings.limits.active_tasks_per_session
    for n in range(limit - 1):
        repo.insert_task(conn, task_row(f"seed-{n}"), NOW)
    response = post(client, token)
    assert response.status_code == 429
    assert response.json() == {
        "code": "active_task_limit_reached",
        "message": f"세션당 활성 업무 한도({limit}개)에 도달했습니다.",
        "field": None,
        "details": {"limit": limit},
    }
    assert repo.list_chains(conn, workspace) == []
    assert len(repo.list_tasks(conn, workspace)) == limit - 1
    one = post(client, token, items=[DIAGNOSE_ITEM])  # 노드 1개는 들어간다
    assert one.status_code == 201, one.text
