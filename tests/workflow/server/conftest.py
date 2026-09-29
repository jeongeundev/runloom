"""server 테스트 공용 fixture — tmp_path 의 실제 DB·산출물 디렉터리로 `create_app(settings)` 를 만든다.

기본은 셀프호스트(ADR-0019): 고정 워크스페이스 `SESSION`(= `SELFHOST_SESSION_ID`), 로그인은 `logged_in_client`,
Agent 는 러너 등록과 같은 모양(`session_agents` 로 워크스페이스에 붙음, `code.fix`·`code.review`), 기본 종류 `bug_fix`.
이름 끝이 `_demo` 인 fixture·도우미는 demo 모드(익명 세션·카탈로그·진단 → code_change)를 전제로 하며,
그것을 쓰는 테스트와 함께 phase 13 step 2·3 에서 지운다.

시드는 repo 로 직접 넣고, 연결 토큰은 연결 코드 발급·교환 API 로 얻는다.
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
from workflow.server.auth import SELFHOST_SESSION_ID, ensure_workspace, utc_now
from workflow.server.settings import Settings

NOW = "2026-09-20T00:00:00Z"
SESSION = SELFHOST_SESSION_ID
OPERATOR_TOKEN = "test-operator-token"
TASK_A = "fix-daily-0920"  # bug_fix
TASK_B = "review-daily-0920"  # code_review ← TASK_A
TASK_A_DEMO = "diagnose-daily-0920"  # diagnosis
TASK_B_DEMO = "fix-daily-0920"  # code_change ← TASK_A_DEMO
EXEC_FIX = "exec-fix-001"
BASE_COMMIT = "3f9c2e1a7b0d4c6e8f1a2b3c4d5e6f7a8b9c0d1e"
LOCAL_REGISTRATION = "local-demo-report"
REPOSITORY = "demo-report-repo"


def _settings(tmp_path, mode: str) -> Settings:
    return Settings(
        db_path=tmp_path / "central.sqlite",
        artifact_dir=tmp_path / "artifacts",
        session_secret="test-session-secret",
        operator_token=OPERATOR_TOKEN,
        diag_api_url="http://127.0.0.1:8100",
        diag_api_token="test-diag-token",
        mode=mode,
        secret_dir=tmp_path / "secrets",  # 기본값(data/secrets)은 저장소 작업 폴더라 테스트가 읽지 않게 한다
    )


@pytest.fixture
def settings(tmp_path) -> Settings:
    return _settings(tmp_path, "selfhost")


@pytest.fixture
def app(settings):
    return create_app(settings)


@pytest.fixture
def client(app):
    """로그인하지 않은 클라이언트 — 기계 API(Bearer)·로그인 전 동작용."""
    return TestClient(app)


def log_in(client: TestClient) -> TestClient:
    """`/login` 폼으로 워크스페이스에 로그인한다(고정 워크스페이스 행을 만들고 쿠키를 받는다)."""
    response = client.post("/login", data={"token": OPERATOR_TOKEN}, follow_redirects=False)
    assert response.status_code == 303, response.text
    return client


@pytest.fixture
def logged_in_client(client) -> TestClient:
    """워크스페이스에 로그인한 `client` (같은 객체). 화면·사람 API 테스트의 기본."""
    return log_in(client)


# demo 모드 — 익명 세션·랜딩·카탈로그·fixture 가져오기·진단 (step 2·3 에서 테스트와 함께 삭제)


@pytest.fixture
def settings_demo(tmp_path) -> Settings:
    return _settings(tmp_path, "demo")


@pytest.fixture
def app_demo(settings_demo):
    return create_app(settings_demo)


@pytest.fixture
def client_demo(app_demo):
    return TestClient(app_demo)


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


def request_body(execution_id: str, task_id: str, kind: str = "bug_fix", inputs=()):
    """기본은 `bug_fix`(첫 시도라 입력 없음). 진단·`code_change` 는 `kind` 와 `inputs` 를 넘긴다(demo)."""
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
        "request": (
            "인계된 진단 근거로 보고서 변환 실패를 재현하는 테스트를 먼저 작성하세요." if kind in ("diagnosis", "code_change")
            else "보고서 변환 실패를 재현하는 테스트를 먼저 작성하고 고치세요."
        ),
        "input_artifact_ids": list(inputs),
        "target": target,
        "kind_spec": None,
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


def task_row(task_id: str, kind: str = "bug_fix", predecessor=None) -> dict:
    """기본은 `bug_fix`(검토는 `code_review`). 진단·`code_change`(demo)·사용자 정의 종류는 예전 모양
    (`code.modify` 능력·`run_id` 대상)으로 만든다."""
    if kind not in ("bug_fix", "code_review"):
        return _task_row_demo(task_id, kind, predecessor)
    capability_code = "code.review" if kind == "code_review" else "code.fix"
    criteria = {
        "bug_fix": [{"code": "bug_fix.verification_passed", "text": "등록된 검증 프로필이 결과 커밋에서 통과함",
                     "structured": True}],
        "code_review": [{"code": "code_review.commit_matches", "text": "검토한 커밋이 최신 수정 결과 커밋과 같음",
                         "structured": True}],
    }[kind]
    return {
        "task_id": task_id,
        "session_id": SESSION,
        "title": "보고서 변환 수정 검토" if kind == "code_review" else "보고서 변환 수정",
        "request": "보고서 변환 실패를 고쳐 주세요.",
        "kind": kind,
        "required_capability": {"code": capability_code, "scope": {"repository_id": REPOSITORY}},
        "selection_mode": "auto",
        "chosen_agent_id": None,
        "run_mode": "manual" if predecessor is None else "auto",
        "completion_mode": "review",
        "criteria": criteria,
        "predecessor_task_id": predecessor,
        "revision": 1,
        "target": {},
        "status": "실행 가능",
        "status_reason": "agent-codex-mac 선택됨",
    }


def _task_row_demo(task_id: str, kind: str, predecessor) -> dict:
    if kind == "diagnosis":
        capability = {"code": "operations.diagnose", "scope": {"workflow_id": "daily-report"}}
    else:
        capability = {"code": "code.modify", "scope": {"repository_id": REPOSITORY}}
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


def seed_execution(conn, execution_id: str, task_id: str, *, kind: str = "bug_fix",
                   connector_id: str | None = None, inputs=(),
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


def seed_agents(conn, *, with_claude: bool = False) -> None:
    """러너 등록과 같은 모양(`repo.register_local_agent` 가 만드는 것) — 로컬 Codex 하나가 `code.fix`·`code.review` 를
    맡고 워크스페이스(`session_agents`)에 붙는다. `with_claude` 면 같은 저장소의 Claude Code 까지 2개.
    워크스페이스 행(`ensure_workspace`)이 먼저 있어야 한다."""
    agents = [("agent-codex-mac", "개인 Codex", LOCAL_REGISTRATION)]
    if with_claude:
        agents.append(("agent-claude-mac", "Claude Code", "local-demo-report-claude"))
    for agent_id, name, registration in agents:
        repo.upsert_agent(conn, {
            "agent_id": agent_id,
            "name": name,
            "owner_scope": "personal",
            "connection_type": "local",
            "local_registration_id": registration,
            "capabilities": [
                {"code": "code.fix", "scope": {"repository_id": REPOSITORY}},
                {"code": "code.review", "scope": {"repository_id": REPOSITORY}},
            ],
            "connection_state": "unknown",
        })
        repo.register_session_agent(conn, SESSION, agent_id, NOW)


@pytest.fixture
def seeded(conn):
    """고정 워크스페이스, 러너 모양 Agent 1개, 업무 A(bug_fix) → B(code_review)."""
    ensure_workspace(conn, NOW)
    seed_agents(conn)
    repo.insert_task(conn, task_row(TASK_A), NOW)
    repo.insert_task(conn, task_row(TASK_B, kind="code_review", predecessor=TASK_A), NOW)
    return conn


@pytest.fixture
def connector(client, conn) -> tuple[str, str]:
    return exchange(client, conn)


@pytest.fixture
def headers(connector) -> dict:
    return bearer(connector[1])


@pytest.fixture
def exec_fix(seeded, connector) -> str:
    """이 connector 에 배정된 queued 실행 exec-fix-001 (업무 A, bug_fix)."""
    seed_execution(seeded, EXEC_FIX, TASK_A, connector_id=connector[0])
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


# --- demo 시드 (step 2·3 에서 삭제) ---------------------------------------------


def seed_agents_demo(conn, *, with_claude: bool = False) -> None:
    """운영자 카탈로그 — 진단 API·로컬 Codex, `with_claude` 면 같은 저장소를 맡는 Claude Code 까지 3개
    (scripts/seed_demo.py 와 같은 구성). 전부 모든 세션에 사용 허용. 기본 2개는 기존 카드 수 테스트를 위해 유지한다."""
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
        "capabilities": [{"code": "code.modify", "scope": {"repository_id": REPOSITORY}}],
        "connection_state": "unknown",
        "shared_to_all_sessions": True,
    })
    if not with_claude:
        return
    repo.upsert_agent(conn, {
        "agent_id": "agent-claude-mac",
        "name": "Claude Code",
        "owner_scope": "personal",
        "connection_type": "local",
        "local_registration_id": "local-demo-report-claude",
        "repository_id": REPOSITORY,
        "base_commit": BASE_COMMIT,
        "capabilities": [{"code": "code.modify", "scope": {"repository_id": REPOSITORY}}],
        "connection_state": "unknown",
        "shared_to_all_sessions": True,
    })


def register_catalog_demo(conn, session_id: str, *agent_ids: str, now: str = NOW) -> None:
    """세션이 카탈로그 Agent 를 등록한 상태 (phase 5 step 2). 기본은 둘 다, codex → ops 순."""
    for agent_id in agent_ids or ("agent-codex-mac", "agent-ops-demo"):
        repo.register_session_agent(conn, session_id, agent_id, now)


@pytest.fixture
def seeded_demo(conn):
    """demo 세션 1개(`SESSION` 이름 그대로, 운영자 아님), 카탈로그 에이전트 2개(세션이 둘 다 등록), 업무 A(진단) → B(code_change)."""
    repo.create_session(conn, SESSION, NOW)
    seed_agents_demo(conn)
    register_catalog_demo(conn, SESSION)
    repo.insert_task(conn, task_row(TASK_A_DEMO, kind="diagnosis"), NOW)
    repo.insert_task(conn, task_row(TASK_B_DEMO, kind="code_change", predecessor=TASK_A_DEMO), NOW)
    return conn


@pytest.fixture
def exec_fix_demo(seeded_demo, connector) -> str:
    """이 connector 에 배정된 queued 실행 exec-fix-001 (업무 B, code_change)."""
    seed_execution(seeded_demo, EXEC_FIX, TASK_B_DEMO, kind="code_change", connector_id=connector[0],
                   inputs=("art-handoff-001",))
    return EXEC_FIX


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
