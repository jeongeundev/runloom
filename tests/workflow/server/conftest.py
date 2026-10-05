"""server 테스트 공용 fixture — tmp_path 의 실제 DB·산출물 디렉터리로 `create_app(settings)` 를 만든다.

셀프호스트 전용(ADR-0019): 고정 워크스페이스 `SESSION`(= `SELFHOST_SESSION_ID`), 로그인은 `logged_in_client`,
Agent 는 러너 등록과 같은 모양(`session_agents` 로 워크스페이스에 붙음, `code.fix`·`code.review`), 기본 종류 `bug_fix`.

시드는 repo 로 직접 넣고, 연결 토큰은 연결 코드 발급·교환 API 로 얻는다.
"""

import hashlib
import json
import secrets

import pytest
from fastapi.testclient import TestClient

from workflow.adapters import repo
from workflow.adapters.artifact_store import ArtifactStore
from workflow.adapters.db import connect
from workflow.contracts.v1 import ExecutionRequest
from workflow.server.app import create_app
from workflow.domain import team
from workflow.server.auth import LOGIN_COOKIE, SELFHOST_SESSION_ID, ensure_workspace, utc_now
from workflow.server.settings import Settings

NOW = "2026-09-20T00:00:00Z"
SESSION = SELFHOST_SESSION_ID
OPERATOR_TOKEN = "test-operator-token"
# `log_in` 이 첫 설정에서 만드는 관리자 계정 — 첫 관리자 행(표시 이름 `관리자`)에 채운다
ADMIN_EMAIL = "admin@example.com"
ADMIN_PASSWORD = "admin-password-0920"
ADMIN_NAME = "관리자"
MEMBER_PASSWORD = "member-password-0920"
TASK_A = "fix-daily-0920"  # bug_fix
TASK_B = "review-daily-0920"  # code_review ← TASK_A
EXEC_FIX = "exec-fix-001"
BASE_COMMIT = "3f9c2e1a7b0d4c6e8f1a2b3c4d5e6f7a8b9c0d1e"
LOCAL_REGISTRATION = "local-demo-report"
REPOSITORY = "demo-report-repo"


def _settings(tmp_path) -> Settings:
    return Settings(
        db_path=tmp_path / "central.sqlite",
        artifact_dir=tmp_path / "artifacts",
        session_secret="test-session-secret",
        operator_token=OPERATOR_TOKEN,
        secret_dir=tmp_path / "secrets",  # 기본값(data/secrets)은 저장소 작업 폴더라 테스트가 읽지 않게 한다
    )


@pytest.fixture(autouse=True)
def _cheap_scrypt(monkeypatch):
    """테스트는 scrypt 비용만 낮춘다 — 저장 형식·검증 경로는 그대로(ARCHITECTURE "비밀번호")."""
    monkeypatch.setattr(team, "SCRYPT_N", 2**4)


@pytest.fixture
def settings(tmp_path) -> Settings:
    return _settings(tmp_path)


@pytest.fixture
def app(settings):
    return create_app(settings)


@pytest.fixture
def client(app):
    """로그인하지 않은 클라이언트 — 기계 API(Bearer)·로그인 전 동작용."""
    return TestClient(app)


def log_in(client: TestClient) -> TestClient:
    """관리자로 로그인한다 — 첫 설정 전이면 운영자 토큰으로 관리자 계정을 만들고(`/login/setup`, 고정 워크스페이스 행도
    만든다), 이미 있으면 이메일·비밀번호로(`/login`). 쿠키 `wf_login` 은 client 가 보관한다."""
    account = {"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
    response = client.post("/login/setup", data={"token": OPERATOR_TOKEN, "display_name": ADMIN_NAME, **account},
                           follow_redirects=False)
    if response.status_code == 409:  # already_set_up
        response = client.post("/login", data=account, follow_redirects=False)
    assert response.status_code == 303, response.text
    return client


def _app_conn(client: TestClient):
    return client.app.state.conn_factory()


def log_in_member(client: TestClient, role: str = "member", *, email: str | None = None,
                  display_name: str = "멤버") -> TestClient:
    """관리자가 발급한 초대 링크(`/invite/{token}`)로 가입해 로그인한다. 관리자 계정은 다른 클라이언트로 먼저 만든다."""
    log_in(TestClient(client.app))
    conn = _app_conn(client)
    try:
        admin_id = repo.find_member_by_email(conn, SESSION, ADMIN_EMAIL)["member_id"]
        email = email or f"{role}-{secrets.token_hex(3)}@example.com"
        _, token = repo.issue_invite(conn, SESSION, role=role, invitee_email=email, created_by_member_id=admin_id,
                                     now=utc_now())
    finally:
        conn.close()
    response = client.post(f"/invite/{token}", data={"email": email, "display_name": display_name,
                                                     "password": MEMBER_PASSWORD}, follow_redirects=False)
    assert response.status_code == 303, response.text
    return client


def log_in_other_workspace(client: TestClient) -> TestClient:
    """고정 워크스페이스가 아닌 워크스페이스(`sess-other`)의 유효한 로그인 세션 쿠키 — 셀프호스트에서는 로그인 안 된 것."""
    conn = _app_conn(client)
    try:
        if repo.get_session(conn, "sess-other") is None:
            repo.create_session(conn, "sess-other", NOW)
        other = repo.ensure_first_admin(conn, "sess-other", now=NOW)
        if repo.get_member(conn, "sess-other", other)["email"] is None:
            repo.set_member_credentials(conn, "sess-other", other, email="other@example.com",
                                        password_hash=team.hash_password(ADMIN_PASSWORD), now=NOW)
        token = repo.create_login_session(conn, "sess-other", other, now=utc_now(), days=14)
    finally:
        conn.close()
    client.cookies.set(LOGIN_COOKIE, token)
    return client


def session_of(client: TestClient) -> str:
    """client 의 로그인 쿠키가 가리키는 워크스페이스 id."""
    conn = _app_conn(client)
    try:
        row = repo.member_for_login_token(conn, client.cookies[LOGIN_COOKIE], now=utc_now())
    finally:
        conn.close()
    assert row is not None
    return row["session_id"]


@pytest.fixture
def logged_in_client(client) -> TestClient:
    """워크스페이스에 로그인한 `client` (같은 객체). 화면·사람 API 테스트의 기본."""
    return log_in(client)


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
    """기본은 `bug_fix`(첫 시도라 입력 없음)."""
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
        "agent_id": "agent-codex-mac",
        "task_revision": 1,
        "request": "보고서 변환 실패를 재현하는 테스트를 먼저 작성하고 고치세요.",
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
    """`bug_fix`(기본)·`code_review`. 사용자 정의 종류는 각 테스트가 종류를 등록하고 행을 직접 만든다."""
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


def seed_execution(conn, execution_id: str, task_id: str, *, kind: str = "bug_fix",
                   connector_id: str | None = None, inputs=(),
                   predecessor: str | None = None, start_key: str | None = None) -> None:
    repo.create_execution(
        conn,
        execution_id=execution_id,
        task_id=task_id,
        attempt_no=1,
        start_key=start_key or f"auto:{task_id}:r1",
        agent_id="agent-codex-mac",
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
    repo.insert_work_item_task(conn, task_row(TASK_A), NOW)
    repo.insert_work_item_task(conn, task_row(TASK_B, kind="code_review", predecessor=TASK_A), NOW)
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
