"""server 테스트 공용 fixture — tmp_path 의 실제 DB·산출물 디렉터리로 `create_app(settings)` 를 만든다.

시드는 Step 4 repo 로 직접 넣고, 연결 토큰은 연결 코드 발급·교환 API 로 얻는다.
"""

import hashlib
import json

import pytest
from fastapi.testclient import TestClient

from workflow.adapters import repo
from workflow.adapters.artifact_store import ArtifactStore
from workflow.adapters.db import connect
from workflow.contracts.v1 import ExecutionRequest
from workflow.server.app import create_app
from workflow.server.auth import utc_now
from workflow.server.settings import Settings

NOW = "2026-09-20T00:00:00Z"
SESSION = "sess-1"
TASK_A = "diagnose-daily-0920"
TASK_B = "fix-daily-0920"
EXEC_FIX = "exec-fix-001"
BASE_COMMIT = "3f9c2e1a7b0d4c6e8f1a2b3c4d5e6f7a8b9c0d1e"
LOCAL_REGISTRATION = "local-demo-report"


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        db_path=tmp_path / "central.sqlite",
        artifact_dir=tmp_path / "artifacts",
        session_secret="test-session-secret",
        operator_token="test-operator-token",
        diag_api_url="http://127.0.0.1:8100",
        diag_api_token="test-diag-token",
    )


@pytest.fixture
def app(settings):
    return create_app(settings)


@pytest.fixture
def client(app):
    return TestClient(app)


@pytest.fixture
def conn(app, settings):
    """앱이 스키마를 만든 뒤 여는 시드·검사용 연결. 앱은 요청마다 자기 연결을 연다."""
    c = connect(settings.db_path)
    yield c
    c.close()


@pytest.fixture
def store(settings) -> ArtifactStore:
    return ArtifactStore(settings.artifact_dir)


# --- 빌더 ------------------------------------------------------------------


def request_body(execution_id: str, task_id: str, kind: str = "code_change", inputs=("art-handoff-001",)):
    if kind == "diagnosis":
        target = {"run_id": "daily-0920-0900"}
    else:
        target = {
            "local_registration_id": LOCAL_REGISTRATION,
            "base_commit": BASE_COMMIT,
            "verification_profile_id": "vp-pytest",
        }
    return {
        "contract_version": 1,
        "execution_id": execution_id,
        "task_id": task_id,
        "kind": kind,
        "agent_id": "agent-ops-demo" if kind == "diagnosis" else "agent-codex-mac",
        "task_revision": 1,
        "request": "인계된 진단 근거로 보고서 변환 실패를 재현하는 테스트를 먼저 작성하세요.",
        "input_artifact_ids": list(inputs),
        "target": target,
    }


def event(execution_id: str, seq: int, type_: str, data: dict, occurred_at: str = "2026-09-20T01:00:00+09:00"):
    return {
        "contract_version": 1,
        "execution_id": execution_id,
        "seq": seq,
        "occurred_at": occurred_at,
        "type": type_,
        "data": data,
    }


def meta_for(data: bytes, kind: str = "diff", name: str = "change.diff", content_type: str = "text/plain"):
    return {
        "contract_version": 1,
        "kind": kind,
        "name": name,
        "content_type": content_type,
        "sha256": hashlib.sha256(data).hexdigest(),
        "size": len(data),
    }


def task_row(task_id: str, kind: str = "diagnosis", predecessor=None) -> dict:
    if kind == "diagnosis":
        capability = {"code": "operations.diagnose", "scope": {"workflow_id": "daily-report"}}
    else:
        capability = {"code": "code.modify", "scope": {"repository_id": "demo-report-repo"}}
    return {
        "task_id": task_id,
        "session_id": SESSION,
        "title": "일일 보고서 실패 진단" if kind == "diagnosis" else "보고서 변환 수정",
        "request": "실패 원인을 조사해 주세요.",
        "kind": kind,
        "required_capability": capability,
        "selection_mode": "auto",
        "chosen_agent_id": None,
        "run_mode": "manual" if predecessor is None else "auto",
        "completion_mode": "review",
        "criteria": [{"code": "handoff_verified", "text": "근거 검증 통과", "structured": True}],
        "predecessor_task_id": predecessor,
        "revision": 1,
        "target": {"run_id": "daily-0920-0900"},
        "status": "실행 가능",
        "status_reason": "agent-ops-demo 선택됨",
    }


def seed_execution(conn, execution_id: str, task_id: str, *, kind: str = "code_change",
                   connector_id: str | None = None, inputs=("art-handoff-001",),
                   predecessor: str | None = None, start_key: str | None = None) -> None:
    repo.create_execution(
        conn,
        execution_id=execution_id,
        task_id=task_id,
        attempt_no=1,
        start_key=start_key or f"auto:{task_id}:r1",
        agent_id="agent-ops-demo" if kind == "diagnosis" else "agent-codex-mac",
        kind=kind,
        request=ExecutionRequest.model_validate(request_body(execution_id, task_id, kind, inputs)),
        assigned_connector_id=connector_id,
        predecessor_execution_id=predecessor,
        now=NOW,
    )


def exchange(client, conn) -> tuple[str, str]:
    """연결 코드를 발급·교환해 (connector_id, token) 을 얻는다. 만료 검사가 서버 시각을 쓰므로 실제 시각으로 발급한다."""
    code = repo.issue_connect_code(conn, utc_now())
    response = client.post("/connector/exchange", json={"contract_version": 1, "connect_code": code})
    assert response.status_code == 200, response.text
    body = response.json()
    return body["connector_id"], body["token"]


def bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# --- 시드 ------------------------------------------------------------------


def seed_agents(conn) -> None:
    """운영자 등록 에이전트 2개(진단 API·로컬 Codex). 둘 다 모든 세션에 사용 허용."""
    repo.upsert_agent(conn, {
        "agent_id": "agent-ops-demo",
        "name": "운영 진단 데모",
        "owner_scope": "company",
        "connection_type": "api",
        "api_url": "http://127.0.0.1:8100",
        "credential_ref": "env:DIAG_API_TOKEN",
        "capabilities": [{"code": "operations.diagnose", "scope": {"workflow_id": "daily-report"}}],
        "connection_state": "online",
        "shared_to_all_sessions": True,
    })
    repo.upsert_agent(conn, {
        "agent_id": "agent-codex-mac",
        "name": "개인 Codex",
        "owner_scope": "personal",
        "connection_type": "local",
        "local_registration_id": LOCAL_REGISTRATION,
        "capabilities": [{"code": "code.modify", "scope": {"repository_id": "demo-report-repo"}}],
        "connection_state": "unknown",
        "shared_to_all_sessions": True,
    })


def seed_claude_agent(conn) -> None:
    """카탈로그 세 번째 — 같은 저장소를 맡는 개인 Claude Code. `seed_agents` 뒤에 부른다 (phase 5 step 5 동률 규칙)."""
    repo.upsert_agent(conn, {
        "agent_id": "agent-claude-mac",
        "name": "개인 Claude Code",
        "owner_scope": "personal",
        "connection_type": "local",
        "local_registration_id": "local-demo-report-claude",
        "repository_id": "demo-report-repo",
        "base_commit": BASE_COMMIT,
        "capabilities": [{"code": "code.modify", "scope": {"repository_id": "demo-report-repo"}}],
        "connection_state": "unknown",
        "shared_to_all_sessions": True,
    })


def register_catalog(conn, session_id: str, *agent_ids: str, now: str = NOW) -> None:
    """세션이 카탈로그 Agent 를 등록한 상태 (phase 5 step 2). 기본은 둘 다, codex → ops 순."""
    for agent_id in agent_ids or ("agent-codex-mac", "agent-ops-demo"):
        repo.register_session_agent(conn, session_id, agent_id, now)


@pytest.fixture
def seeded(conn):
    """세션 1개, 운영자 등록 에이전트 2개(세션이 둘 다 등록), 업무 A → B."""
    repo.create_session(conn, SESSION, NOW)
    seed_agents(conn)
    register_catalog(conn, SESSION)
    repo.insert_task(conn, task_row(TASK_A), NOW)
    repo.insert_task(conn, task_row(TASK_B, kind="code_change", predecessor=TASK_A), NOW)
    return conn


@pytest.fixture
def connector(client, seeded) -> tuple[str, str]:
    return exchange(client, seeded)


@pytest.fixture
def headers(connector) -> dict:
    return bearer(connector[1])


@pytest.fixture
def exec_fix(seeded, connector) -> str:
    """이 connector 에 배정된 queued 실행 exec-fix-001 (업무 B, code_change)."""
    seed_execution(seeded, EXEC_FIX, TASK_B, connector_id=connector[0])
    return EXEC_FIX


@pytest.fixture
def running(client, headers, exec_fix) -> str:
    """accepted(1) → started(2) → progress(3) 까지 반영된 exec-fix-001. 상태 running."""
    for body in (
        event(exec_fix, 1, "accepted", {}),
        event(exec_fix, 2, "started", {"runtime_ref": "pid:48213;start:2026-09-20T01:00:03+09:00"}),
        event(exec_fix, 3, "progress", {"message": "재현 테스트 작성, 수정 전 실행 실패 확인"}),
    ):
        response = client.post(f"/executions/{exec_fix}/events", json=body, headers=headers)
        assert response.status_code == 200, response.text
    return exec_fix


# --- 결과 시드 (Step 6 웹·뷰 테스트) -------------------------------------------

RESULT_COMMIT = "9b7e4d2c1a0f8e6d5c3b2a1f0e9d8c7b6a5f4e3d"


def code_change_result(execution_id: str, task_id: str) -> dict:
    """CONTRACT 7절 `ready_for_review` 결과. 산출물 ID 는 화면이 파싱만 하므로 예시 값을 그대로 둔다."""
    return {
        "contract_version": 1,
        "execution_id": execution_id,
        "task_id": task_id,
        "outcome": "ready_for_review",
        "summary": "report_transformer가 items 또는 data.records 중 정확히 하나의 목록을 읽도록 수정했습니다.",
        "base_commit": BASE_COMMIT,
        "result_commit": RESULT_COMMIT,
        "artifact_ids": ["art-diff-001", "art-test-before-001", "art-test-after-001", "art-report-001"],
        "verification": {
            "profile_id": "vp-pytest",
            "result_commit": RESULT_COMMIT,
            "exit_code": 0,
            "log_artifact_id": "art-verify-001",
        },
    }


def seed_result_ready(conn, store, execution_id: str, *, kind: str, body: dict,
                      session_id: str = SESSION, actor: str = "connector:conn-1") -> str:
    """queued 실행을 accepted → started → (결과 산출물 저장) → result_ready 까지 진행시킨다. 반환은 산출물 ID."""
    from workflow.contracts.v1 import ArtifactMeta, ExecutionEvent

    def _apply(seq: int, type_: str, data: dict) -> None:
        repo.append_event(
            conn, execution_id,
            ExecutionEvent.model_validate(event(execution_id, seq, type_, data)),
            actor=actor, now=NOW,
        )

    _apply(1, "accepted", {})
    _apply(2, "started", {"runtime_ref": "pid:1"})
    data = json.dumps(body, ensure_ascii=False).encode()
    created, _ = repo.store_artifact(
        conn, store, execution_id=execution_id, session_id=session_id,
        meta=ArtifactMeta.model_validate(meta_for(data, kind=kind, name=f"{kind}.json",
                                                   content_type="application/json")),
        data=data, now=NOW,
    )
    _apply(3, "result_ready", {"result_artifact_id": created.artifact_id})
    return created.artifact_id
