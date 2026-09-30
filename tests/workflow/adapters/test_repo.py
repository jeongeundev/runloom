"""repo.py — 함수형 저장소. CONTRACT 3절 오류표·4절 산출물 규칙·ARCHITECTURE 실행 잠금을 실제 sqlite 로 검증한다."""

import hashlib
import json
import sqlite3
import threading

import pytest
from pydantic import ValidationError

from workflow.adapters import repo
from workflow.adapters.db import connect
from workflow.adapters.errors import (
    ActiveExecutionExists,
    ArtifactMissing,
    DuplicateKind,
    DuplicateRule,
    DuplicateStartKey,
    EmailTaken,
    EventConflict,
    HashMismatch,
    InvalidTransition,
    KindInUse,
    KindProtected,
    LastAdmin,
    NotFound,
    RegistrationTaken,
    ResponseConflict,
    SequenceGap,
    StaleConfig,
    StaleRequest,
    TaskClosed,
)
from workflow.contracts.github import (
    AssigneeBinding,
    GitHubIssueSnapshot,
    GitHubSourceConfig,
    IssuePrLink,
    PullRequestRef,
    snapshot_digest,
)
from workflow.contracts.v1 import (
    BUILTIN_KINDS,
    BUILTIN_RULES,
    ArtifactMeta,
    Capability,
    ExecutionEvent,
    ExecutionRequest,
    KindSpec,
    SelectionRecord,
    SuccessorRule,
)
from workflow.domain.field_mapping import MappingRow
from workflow.domain.selection import Candidate, select_agent
from workflow.domain.start_checklist import StartFacts
from workflow.domain.task_followup import FollowupTaskSpec
from workflow.domain.work_status import WorkStatus, work_status

from .conftest import NOW

LATER = "2026-09-20T00:00:05Z"
SESSION = "sess-1"
OTHER_SESSION = "sess-2"
TASK_A = "diagnose-daily-0920"
TASK_B = "fix-daily-0920"
CONNECTOR = "conn-mac-01"


# --- 빌더 ------------------------------------------------------------------


def _request(execution_id: str, task_id: str, kind: str = "diagnosis", inputs=()) -> ExecutionRequest:
    if kind == "diagnosis":
        target = {"run_id": "daily-0920-0900"}
    else:
        target = {
            "local_registration_id": "local-demo-report",
            "base_commit": "3f9c2e1a7b0d4c6e8f1a2b3c4d5e6f7a8b9c0d1e",
            "verification_profile_id": "vp-pytest",
        }
    return ExecutionRequest.model_validate(
        {
            "contract_version": 1,
            "execution_id": execution_id,
            "task_id": task_id,
            "kind": kind,
            "agent_id": "agent-ops-demo" if kind == "diagnosis" else "agent-codex-mac",
            "task_revision": 1,
            "request": "실패 원인을 조사해 주세요.",
            "input_artifact_ids": list(inputs),
            "target": target,
        }
    )


def _event(execution_id: str, seq: int, type_: str, data: dict, occurred_at: str = NOW) -> ExecutionEvent:
    return ExecutionEvent.model_validate(
        {
            "contract_version": 1,
            "execution_id": execution_id,
            "seq": seq,
            "occurred_at": occurred_at,
            "type": type_,
            "data": data,
        }
    )


def _meta(data: bytes, kind: str = "evidence", name: str = "file.json") -> ArtifactMeta:
    return ArtifactMeta(
        contract_version=1,
        kind=kind,
        name=name,
        content_type="application/json",
        sha256=hashlib.sha256(data).hexdigest(),
        size=len(data),
    )


def _task(task_id: str, session_id: str = SESSION, kind: str = "diagnosis", predecessor=None) -> dict:
    if kind == "diagnosis":
        capability = {"code": "operations.diagnose", "scope": {"workflow_id": "daily-report"}}
    else:
        capability = {"code": "code.modify", "scope": {"repository_id": "demo-report-repo"}}
    return {
        "task_id": task_id,
        "session_id": session_id,
        "title": "일일 보고서 실패 진단",
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


def _agent(agent_id: str = "agent-ops-demo", **overrides) -> dict:
    agent = {
        "agent_id": agent_id,
        "name": "운영 진단 데모",
        "owner_scope": "company",
        "connection_type": "api",
        "api_url": "http://127.0.0.1:8100",
        "credential_ref": "env:DIAG_API_TOKEN",
        "capabilities": [{"code": "operations.diagnose", "scope": {"workflow_id": "daily-report"}}],
        "connection_state": "online",
        "shared_to_all_sessions": True,
    }
    agent.update(overrides)
    return agent


def _create_execution(conn, execution_id="exec-1", task_id=TASK_A, *, attempt_no=1, start_key=None,
                      kind="diagnosis", connector=None, inputs=(), predecessor=None, now=NOW):
    repo.create_execution(
        conn,
        execution_id=execution_id,
        task_id=task_id,
        attempt_no=attempt_no,
        start_key=start_key or f"auto:{task_id}:r1",
        agent_id="agent-ops-demo" if kind == "diagnosis" else "agent-codex-mac",
        kind=kind,
        request=_request(execution_id, task_id, kind, inputs),
        assigned_connector_id=connector,
        predecessor_execution_id=predecessor,
        now=now,
    )


# 진단 데모 제거(ADR-0019) 뒤 diagnosis·code_change 는 내장이 아니다. 저장소 계층은 종류 이름을 가리지 않으므로
# 기존 테스트 데이터(TASK_A = diagnosis, 후속 = code_change)는 사용자 정의 종류로 등록해 그대로 쓴다.
DIAGNOSIS_KIND = KindSpec(
    kind="diagnosis", label="운영 진단", capability_code="operations.diagnose", scope_key="workflow_id",
    input_kinds=[], output_kind="generic_result", outcomes=["ready_for_handoff", "needs_information"],
    instructions="", builtin=False,
)
CODE_CHANGE_KIND = KindSpec(
    kind="code_change", label="코드 수정", capability_code="code.modify", scope_key="repository_id",
    input_kinds=["diagnosis_result", "evidence"], output_kind="generic_result",
    outcomes=["ready_for_review", "needs_information"], instructions="", builtin=False,
)
# `seeded` 의 종류 등록 2건이 세션 설정 번호를 1 → 3 으로 올린다 (insert_kind 마다 +1)
SEEDED_REVISION = 3


@pytest.fixture(autouse=True)
def every_task_has_work_item(conn):
    """불변식(ADR-0020) — 어떤 repo 경로로 만든 Task 든 업무에 속한다."""
    yield
    assert conn.execute("SELECT COUNT(*) FROM tasks WHERE work_item_id IS NULL").fetchone()[0] == 0


@pytest.fixture
def sessions(conn):
    """내장 종류·규칙만 seed 된 두 세션."""
    repo.create_session(conn, SESSION, NOW)
    repo.create_session(conn, OTHER_SESSION, NOW)
    return conn


@pytest.fixture
def seeded(sessions):
    for session_id in (SESSION, OTHER_SESSION):
        repo.insert_kind(sessions, session_id, DIAGNOSIS_KIND, NOW)
        repo.insert_kind(sessions, session_id, CODE_CHANGE_KIND, NOW)
    repo.insert_work_item_task(sessions, _task(TASK_A), NOW)
    return sessions


@pytest.fixture
def running(seeded, store):
    """accepted → started 까지 진행된 exec-1. 상태는 running."""
    _create_execution(seeded, "exec-1")
    repo.append_event(seeded, "exec-1", _event("exec-1", 1, "accepted", {}), "conn", NOW)
    repo.append_event(seeded, "exec-1", _event("exec-1", 2, "started", {"runtime_ref": "pid:1"}), "conn", NOW)
    return seeded


# --- 세션·에이전트 ---------------------------------------------------------


def test_session_create_get_and_mark_operator(conn):
    repo.create_session(conn, SESSION, NOW)
    row = repo.get_session(conn, SESSION)
    assert row["session_id"] == SESSION and row["is_operator"] == 0
    repo.mark_operator(conn, SESSION)
    assert repo.get_session(conn, SESSION)["is_operator"] == 1
    assert repo.get_session(conn, "nope") is None


def test_agent_upsert_list_get_delete(conn):
    repo.upsert_agent(conn, _agent())
    repo.upsert_agent(conn, _agent(name="이름 변경"))
    rows = repo.list_agents(conn)
    assert len(rows) == 1 and rows[0]["name"] == "이름 변경"
    caps = json.loads(rows[0]["capabilities_json"])
    assert caps == [{"code": "operations.diagnose", "scope": {"workflow_id": "daily-report"}}]
    assert repo.get_agent(conn, "agent-ops-demo")["credential_ref"] == "env:DIAG_API_TOKEN"
    repo.delete_agent(conn, "agent-ops-demo")
    assert repo.get_agent(conn, "agent-ops-demo") is None
    with pytest.raises(NotFound):
        repo.delete_agent(conn, "agent-ops-demo")


def test_agent_upsert_rejects_bad_capability_and_unknown_key(conn):
    with pytest.raises(ValidationError):
        repo.upsert_agent(conn, _agent(capabilities=[{"code": "operations.diagnose", "scope": {}}]))
    with pytest.raises(ValueError):
        repo.upsert_agent(conn, _agent(token="wfc_leak"))


def test_agent_connection_state_and_connector_lookup(conn):
    repo.upsert_agent(conn, _agent("agent-codex-mac", connection_type="local", owner_scope="personal",
                                   connector_id=CONNECTOR, local_registration_id="local-demo-report",
                                   repository_id="demo-report-repo",
                                   capabilities=[{"code": "code.modify",
                                                  "scope": {"repository_id": "demo-report-repo"}}],
                                   connection_state="unknown"))
    repo.upsert_agent(conn, _agent())
    repo.set_agent_connection(conn, "agent-codex-mac", "offline", LATER)
    row = repo.get_agent(conn, "agent-codex-mac")
    assert row["connection_state"] == "offline" and row["last_seen_at"] == LATER
    assert [r["agent_id"] for r in repo.agents_for_connector(conn, CONNECTOR)] == ["agent-codex-mac"]
    with pytest.raises(NotFound):
        repo.set_agent_connection(conn, "nope", "online", None)


def test_update_registration_fills_connector_fields_and_keeps_capabilities(conn):
    repo.upsert_agent(conn, _agent("agent-codex-mac", connection_type="local", owner_scope="personal",
                                   local_registration_id="local-demo-report",
                                   capabilities=[{"code": "code.modify",
                                                  "scope": {"repository_id": "demo-report-repo"}}],
                                   connection_state="unknown"))
    agent_id = repo.update_registration(
        conn, "local-demo-report", connector_id=CONNECTOR, repository_id="demo-report-repo",
        base_commit="3f9c2e1a7b0d4c6e8f1a2b3c4d5e6f7a8b9c0d1e", verification_profile_ids=["vp-pytest"],
        discovered={"codex_version": "0.155.1"}, now=LATER,
    )
    assert agent_id == "agent-codex-mac"
    row = repo.get_agent(conn, "agent-codex-mac")
    assert row["connector_id"] == CONNECTOR
    assert row["repository_id"] == "demo-report-repo"
    assert row["base_commit"] == "3f9c2e1a7b0d4c6e8f1a2b3c4d5e6f7a8b9c0d1e"
    assert json.loads(row["verification_profile_ids_json"]) == ["vp-pytest"]
    assert json.loads(row["discovered_json"]) == {"codex_version": "0.155.1"}
    assert (row["connection_state"], row["last_seen_at"]) == ("online", LATER)
    assert json.loads(row["capabilities_json"])[0]["code"] == "code.modify"
    with pytest.raises(NotFound):
        repo.update_registration(conn, "local-none", connector_id=CONNECTOR, repository_id="r",
                                 base_commit="3f9c2e1a7b0d4c6e8f1a2b3c4d5e6f7a8b9c0d1e",
                                 verification_profile_ids=[], discovered={}, now=LATER)



def test_update_registration_heads_changes_only_that_connectors_agents(conn):
    repo.upsert_agent(conn, _agent("agent-a", connection_type="local", owner_scope="personal",
                                   local_registration_id="reg-a"))
    repo.upsert_agent(conn, _agent("agent-b", connection_type="local", owner_scope="personal",
                                   local_registration_id="reg-b"))
    for agent_id, reg, connector in (("agent-a", "reg-a", CONNECTOR), ("agent-b", "reg-b", "conn-other")):
        repo.update_registration(conn, reg, connector_id=connector, repository_id="r", base_commit="a" * 40,
                                 verification_profile_ids=[], discovered={}, now=LATER)

    changed = repo.update_registration_heads(conn, CONNECTOR, {"reg-a": "c" * 40, "reg-b": "d" * 40, "reg-x": "e" * 40})

    assert changed == 1
    assert repo.get_agent(conn, "agent-a")["base_commit"] == "c" * 40
    assert repo.get_agent(conn, "agent-b")["base_commit"] == "a" * 40
    assert repo.update_registration_heads(conn, CONNECTOR, {}) == 0


# --- 러너 등록이 Agent 를 만든다 (phase 12 step 1, ADR-0018 결정 1) ------------

REG_COMMIT = "3f9c2e1a7b0d4c6e8f1a2b3c4d5e6f7a8b9c0d1e"


def _register(conn, *, connector_id=CONNECTOR, local_registration_id="OpenArchive", agent_name="OpenArchive",
              repository_id="jeongeundev/OpenArchive", base_commit=REG_COMMIT, session_id=SESSION, now=LATER):
    return repo.register_local_agent(
        conn, connector_id=connector_id, local_registration_id=local_registration_id, agent_name=agent_name,
        repository_id=repository_id, base_commit=base_commit, verification_profile_ids=["vp-check"],
        discovered={"found": {"github_repository": "jeongeundev/OpenArchive"}}, session_id=session_id, now=now,
    )


def _issue_connector(conn, connector_id: str) -> None:
    conn.execute("INSERT INTO connectors (connector_id, token_sha256, created_at) VALUES (?, ?, ?)",
                 (connector_id, hashlib.sha256(connector_id.encode()).hexdigest(), NOW))


def test_register_local_agent_creates_agent_with_fix_and_review_in_workspace(conn):
    repo.create_session(conn, SESSION, NOW)
    _issue_connector(conn, CONNECTOR)

    agent_id, created = _register(conn)

    assert created is True
    assert agent_id.startswith("agt-") and len(agent_id) == len("agt-") + 8
    row = repo.get_agent(conn, agent_id)
    assert (row["name"], row["owner_scope"], row["connection_type"]) == ("OpenArchive", "personal", "local")
    assert row["local_registration_id"] == "OpenArchive"
    assert json.loads(row["capabilities_json"]) == [
        {"code": "code.fix", "scope": {"repository_id": "jeongeundev/OpenArchive"}},
        {"code": "code.review", "scope": {"repository_id": "jeongeundev/OpenArchive"}},
    ]
    assert (row["connector_id"], row["repository_id"], row["base_commit"]) == (
        CONNECTOR, "jeongeundev/OpenArchive", REG_COMMIT)
    assert json.loads(row["verification_profile_ids_json"]) == ["vp-check"]
    assert json.loads(row["discovered_json"])["found"]["github_repository"] == "jeongeundev/OpenArchive"
    assert (row["connection_state"], row["last_seen_at"]) == ("online", LATER)
    assert row["shared_to_all_sessions"] == 0
    assert repo.is_session_agent(conn, SESSION, agent_id)


def test_register_local_agent_without_name_uses_local_registration_id(conn):
    repo.create_session(conn, SESSION, NOW)
    _issue_connector(conn, CONNECTOR)

    agent_id, _ = _register(conn, agent_name=None)

    assert repo.get_agent(conn, agent_id)["name"] == "OpenArchive"


def test_register_local_agent_is_idempotent_and_updates(conn):
    repo.create_session(conn, SESSION, NOW)
    _issue_connector(conn, CONNECTOR)
    first, _ = _register(conn)

    again, created = _register(conn, agent_name="다른 이름", base_commit="a" * 40, repository_id="other/repo")

    assert (again, created) == (first, False)
    assert len([a for a in repo.list_agents(conn) if a["local_registration_id"] == "OpenArchive"]) == 1
    row = repo.get_agent(conn, first)
    assert (row["base_commit"], row["repository_id"]) == ("a" * 40, "other/repo")
    assert row["name"] == "OpenArchive"  # 이름·능력은 만들 때 값 그대로
    assert json.loads(row["capabilities_json"])[0]["scope"] == {"repository_id": "jeongeundev/OpenArchive"}


def test_register_local_agent_fills_preregistered_agent_without_creating(conn):
    repo.create_session(conn, SESSION, NOW)
    _issue_connector(conn, CONNECTOR)
    repo.upsert_agent(conn, _agent("agent-codex-mac", connection_type="local", owner_scope="personal",
                                   local_registration_id="OpenArchive",
                                   capabilities=[{"code": "code.modify", "scope": {"repository_id": "r"}}]))

    agent_id, created = _register(conn)

    assert (agent_id, created) == ("agent-codex-mac", False)
    assert len(repo.list_agents(conn)) == 1
    row = repo.get_agent(conn, agent_id)
    assert row["connector_id"] == CONNECTOR
    assert json.loads(row["capabilities_json"])[0]["code"] == "code.modify"


def test_register_local_agent_rejects_name_used_by_another_connector(conn):
    repo.create_session(conn, SESSION, NOW)
    _issue_connector(conn, CONNECTOR)
    _issue_connector(conn, "conn-other")
    agent_id, _ = _register(conn)

    with pytest.raises(RegistrationTaken):
        _register(conn, connector_id="conn-other", base_commit="b" * 40)

    row = repo.get_agent(conn, agent_id)
    assert (row["connector_id"], row["base_commit"]) == (CONNECTOR, REG_COMMIT)


def test_register_local_agent_takes_over_from_revoked_connector(conn):
    """취소된 연결 프로그램은 그 이름을 더 쓰지 않는다 — 새 연결 프로그램이 이어받는다."""
    repo.create_session(conn, SESSION, NOW)
    _issue_connector(conn, CONNECTOR)
    _issue_connector(conn, "conn-new")
    agent_id, _ = _register(conn)
    repo.revoke_connector(conn, CONNECTOR, LATER)

    again, created = _register(conn, connector_id="conn-new")

    assert (again, created) == (agent_id, False)
    assert repo.get_agent(conn, agent_id)["connector_id"] == "conn-new"


# --- 연결 코드·연결 프로그램 ------------------------------------------------


def test_connect_code_exchange_once(conn):
    code = repo.issue_connect_code(conn, NOW)
    connector_id, token = repo.exchange_connect_code(conn, code, NOW)
    assert token.startswith("wfc_") and connector_id
    assert repo.authenticate_connector(conn, token) == connector_id
    with pytest.raises(NotFound):
        repo.exchange_connect_code(conn, code, NOW)


def test_connect_code_expired_or_revoked_is_not_found(conn):
    code = repo.issue_connect_code(conn, NOW, ttl_seconds=600)
    with pytest.raises(NotFound):
        repo.exchange_connect_code(conn, code, "2026-09-20T00:11:00Z")
    code2 = repo.issue_connect_code(conn, NOW)
    repo.revoke_connect_code(conn, code2, NOW)
    with pytest.raises(NotFound):
        repo.exchange_connect_code(conn, code2, NOW)
    with pytest.raises(NotFound):
        repo.exchange_connect_code(conn, "no-such-code", NOW)


def test_token_plaintext_never_stored(conn):
    code = repo.issue_connect_code(conn, NOW)
    _, token = repo.exchange_connect_code(conn, code, NOW)
    dumped = ""
    for table in ("connectors", "connect_codes"):
        for row in conn.execute(f"SELECT * FROM {table}"):
            dumped += " ".join(str(v) for v in tuple(row))
    assert "wfc_" not in dumped
    assert token[4:] not in dumped


def test_connector_revoke_and_touch(conn):
    code = repo.issue_connect_code(conn, NOW)
    connector_id, token = repo.exchange_connect_code(conn, code, NOW)
    repo.touch_connector(conn, connector_id, LATER, "exec-1")
    row = conn.execute("SELECT * FROM connectors WHERE connector_id=?", (connector_id,)).fetchone()
    assert row["last_seen_at"] == LATER and row["current_execution_id"] == "exec-1"
    repo.revoke_connector(conn, connector_id, LATER)
    assert repo.authenticate_connector(conn, token) is None
    assert repo.authenticate_connector(conn, "wfc_bogus") is None


def test_connect_code_issuer_becomes_connector_owner(conn):
    """러너 소유자(phase 15 step 10) — 발급 멤버가 교환 때 같은 트랜잭션에서 연결 프로그램 소유자로 옮겨진다."""
    repo.create_session(conn, SESSION, NOW)
    kim = repo.add_member(conn, SESSION, display_name="김", now=NOW)
    code = repo.issue_connect_code(conn, NOW, issued_by_member_id=kim)
    legacy = repo.issue_connect_code(conn, NOW)

    assert [c["issued_by_member_id"] for c in repo.list_connect_codes(conn, issued_by_member_id=kim)] == [kim]
    assert {c["code"] for c in repo.list_connect_codes(conn)} == {code, legacy}

    owned, _ = repo.exchange_connect_code(conn, code, NOW)
    unowned, _ = repo.exchange_connect_code(conn, legacy, NOW)
    assert repo.connector_owner(conn, owned) == kim
    assert repo.connector_owner(conn, unowned) is None
    assert repo.connector_owner(conn, "conn-none") is None
    assert [r["connector_id"] for r in repo.list_connectors(conn)] == sorted([owned, unowned])


def test_record_supported_kinds_keeps_last_claim_declaration(conn):
    """마지막 claim 의 `supported_kinds` 선언을 JSON 으로 남긴다. 선언 없는 claim(구버전)은 NULL 로 되돌린다."""
    code = repo.issue_connect_code(conn, NOW)
    connector_id, _ = repo.exchange_connect_code(conn, code, NOW)

    def stored():
        return conn.execute(
            "SELECT supported_kinds_json FROM connectors WHERE connector_id=?", (connector_id,)
        ).fetchone()[0]

    assert stored() is None
    repo.record_supported_kinds(conn, connector_id, ["code_change", "bug_fix"])
    assert json.loads(stored()) == ["code_change", "bug_fix"]
    repo.record_supported_kinds(conn, connector_id, None)
    assert stored() is None
    with pytest.raises(NotFound):
        repo.record_supported_kinds(conn, "conn-missing", ["bug_fix"])


# --- 업무·선택 ---------------------------------------------------------------


def test_task_insert_get_list_and_status(seeded):
    conn = seeded
    row = repo.get_task(conn, TASK_A)
    assert row["status"] == "실행 가능" and row["revision"] == 1
    assert json.loads(row["required_capability_json"])["code"] == "operations.diagnose"
    repo.insert_work_item_task(conn, _task("other-task", session_id=OTHER_SESSION), LATER)
    assert [r["task_id"] for r in repo.list_tasks(conn, SESSION)] == [TASK_A]
    assert len(repo.list_tasks(conn, None)) == 2
    repo.update_task_status(conn, TASK_A, "완료", "검토 승인", finished_at=LATER, review_decision="approve", now=LATER)
    row = repo.get_task(conn, TASK_A)
    assert (row["status"], row["status_reason"], row["finished_at"], row["review_decision"]) == (
        "완료", "검토 승인", LATER, "approve")
    with pytest.raises(NotFound):
        repo.update_task_status(conn, "nope", "완료", "x", now=LATER)


def test_task_predecessor_must_be_same_session(seeded):
    conn = seeded
    repo.insert_work_item_task(conn, _task(TASK_B, kind="code_change", predecessor=TASK_A), NOW)
    assert [r["task_id"] for r in repo.successors_of(conn, TASK_A)] == [TASK_B]
    with pytest.raises(NotFound):
        repo.insert_work_item_task(conn, _task("fix-2", session_id=OTHER_SESSION, kind="code_change",
                                     predecessor=TASK_A), NOW)


def test_selection_record_roundtrip(seeded):
    conn = seeded
    record = SelectionRecord.model_validate({
        "task_id": TASK_A,
        "mode": "auto",
        "required_capability": {"code": "operations.diagnose", "scope": {"workflow_id": "daily-report"}},
        "candidate_count": 1,
        "selected_agent_id": "agent-ops-demo",
        "matched": {"code": "operations.diagnose", "scope": {"workflow_id": "daily-report"}},
        "status": "selected",
        "reason": "operations.diagnose · workflow_id=daily-report 일치 후보 1개",
    })
    repo.save_selection(conn, record)
    assert repo.get_selection(conn, TASK_A) == record
    repo.save_selection(conn, record.model_copy(update={"reason": "다시 저장"}))
    assert repo.get_selection(conn, TASK_A).reason == "다시 저장"
    assert repo.get_selection(conn, "nope") is None


def test_active_lock_blocks_second_execution_until_release(seeded):
    conn = seeded
    _create_execution(conn, "exec-1")
    with pytest.raises(ActiveExecutionExists):
        _create_execution(conn, "exec-2", attempt_no=2, start_key="req:retry-1")
    assert repo.active_execution(conn, TASK_A)["execution_id"] == "exec-1"
    repo.release_execution(conn, "exec-1", LATER)
    assert repo.active_execution(conn, TASK_A) is None
    _create_execution(conn, "exec-2", attempt_no=2, start_key="req:retry-1")
    assert repo.get_execution(conn, "exec-2")["attempt_no"] == 2
    assert repo.get_execution(conn, "exec-1")["released_at"] == LATER


def test_same_start_key_is_rejected_even_after_release(seeded):
    conn = seeded
    _create_execution(conn, "exec-1")
    repo.release_execution(conn, "exec-1", LATER)
    with pytest.raises(DuplicateStartKey):
        _create_execution(conn, "exec-2", attempt_no=2)
    assert repo.get_execution(conn, "exec-2") is None


def test_create_execution_stores_frozen_request(seeded):
    _create_execution(seeded, "exec-1")
    row = repo.get_execution(seeded, "exec-1")
    assert row["status"] == "queued" and row["last_event_seq"] == 0
    assert ExecutionRequest.model_validate_json(row["request_json"]) == _request("exec-1", TASK_A)
    with pytest.raises(NotFound):
        repo.release_execution(seeded, "nope", NOW)


def test_executions_needing_attention_excludes_terminal(seeded, store):
    conn = seeded
    _create_execution(conn, "exec-1")
    assert [r["execution_id"] for r in repo.executions_needing_attention(conn)] == ["exec-1"]
    repo.append_event(conn, "exec-1", _event("exec-1", 1, "accepted", {}), "conn", NOW)
    repo.append_event(conn, "exec-1", _event("exec-1", 2, "failed",
                                             {"code": "timeout", "message": "x", "process_stopped": True}),
                      "conn", NOW)
    assert repo.executions_needing_attention(conn) == []


# --- 이벤트 (CONTRACT 3절) ----------------------------------------------------


def test_events_normal_sequence_changes_status_and_timestamps(seeded, store):
    conn = seeded
    _create_execution(conn, "exec-1")
    ack = repo.append_event(conn, "exec-1", _event("exec-1", 1, "accepted", {}), "conn", NOW)
    assert (ack.status, ack.last_event_seq) == ("accepted", 1)
    assert repo.get_execution(conn, "exec-1")["accepted_at"] == NOW

    ack = repo.append_event(conn, "exec-1", _event("exec-1", 2, "started", {"runtime_ref": "pid:1"}),
                            "conn", LATER)
    assert ack.status == "running"
    assert repo.get_execution(conn, "exec-1")["started_at"] == LATER

    ack = repo.append_event(conn, "exec-1", _event("exec-1", 3, "progress", {"message": "조회 중"}),
                            "conn", LATER)
    assert ack.status == "running"

    data = b'{"outcome": "ready_for_handoff"}'
    created, _ = repo.store_artifact(conn, store, execution_id="exec-1", session_id=SESSION,
                                     meta=_meta(data, "diagnosis_result"), data=data, now=LATER)
    ack = repo.append_event(conn, "exec-1", _event("exec-1", 4, "result_ready",
                                                   {"result_artifact_id": created.artifact_id}),
                            "conn", LATER)
    assert (ack.status, ack.last_event_seq) == ("result_ready", 4)
    row = repo.get_execution(conn, "exec-1")
    assert row["result_artifact_id"] == created.artifact_id and row["finished_at"] == LATER

    events = repo.list_events(conn, "exec-1")
    assert [e["seq"] for e in events] == [1, 2, 3, 4]
    assert [e["seq"] for e in repo.list_events(conn, "exec-1", after_seq=2)] == [3, 4]
    assert events[0]["actor"] == "conn" and events[0]["received_at"] == NOW


def test_duplicate_same_seq_same_content_is_accepted_without_reapply(running):
    conn = running
    before = len(repo.list_events(conn, "exec-1"))
    ack = repo.append_event(conn, "exec-1", _event("exec-1", 2, "started", {"runtime_ref": "pid:1"}),
                            "conn", LATER)
    assert (ack.status, ack.last_event_seq) == ("running", 2)
    assert len(repo.list_events(conn, "exec-1")) == before
    assert repo.get_execution(conn, "exec-1")["started_at"] == NOW  # 다시 적용하지 않음


def test_same_seq_different_content_conflicts(running):
    with pytest.raises(EventConflict) as info:
        repo.append_event(running, "exec-1", _event("exec-1", 2, "started", {"runtime_ref": "pid:2"}),
                          "conn", LATER)
    assert info.value.seq == 2
    with pytest.raises(EventConflict):
        repo.append_event(running, "exec-1", _event("exec-1", 2, "progress", {"message": "x"}),
                          "conn", LATER)


def test_sequence_gap_reports_expected_seq(running):
    conn = running
    repo.append_event(conn, "exec-1", _event("exec-1", 3, "progress", {"message": "a"}), "conn", NOW)
    with pytest.raises(SequenceGap) as info:
        repo.append_event(conn, "exec-1", _event("exec-1", 5, "progress", {"message": "b"}), "conn", NOW)
    assert info.value.expected_seq == 4
    assert repo.get_execution(conn, "exec-1")["last_event_seq"] == 3


def test_invalid_transitions(seeded, store):
    conn = seeded
    _create_execution(conn, "exec-1")
    repo.append_event(conn, "exec-1", _event("exec-1", 1, "accepted", {}), "conn", NOW)
    with pytest.raises(InvalidTransition) as info:  # running 아닌데 progress
        repo.append_event(conn, "exec-1", _event("exec-1", 2, "progress", {"message": "x"}), "conn", NOW)
    assert (info.value.current_status, info.value.event_type) == ("accepted", "progress")

    repo.append_event(conn, "exec-1", _event("exec-1", 2, "failed",
                                             {"code": "timeout", "message": "x", "process_stopped": True}),
                      "conn", LATER)
    row = repo.get_execution(conn, "exec-1")
    assert (row["status"], row["failed_code"], row["process_stopped"], row["finished_at"]) == (
        "failed", "timeout", 1, LATER)
    with pytest.raises(InvalidTransition) as info:  # 최종 상태 뒤 새 started
        repo.append_event(conn, "exec-1", _event("exec-1", 3, "started", {"runtime_ref": "pid:9"}),
                          "conn", LATER)
    assert (info.value.current_status, info.value.event_type) == ("failed", "started")
    assert repo.get_execution(conn, "exec-1")["last_event_seq"] == 2


def test_result_ready_requires_artifact_of_this_execution(running, store):
    conn = running
    with pytest.raises(InvalidTransition) as info:
        repo.append_event(conn, "exec-1", _event("exec-1", 3, "result_ready",
                                                 {"result_artifact_id": "art-none"}), "conn", NOW)
    assert info.value.current_status == "running" and info.value.reason == "result_artifact_missing"
    assert info.value.event_type == "result_ready"

    repo.insert_work_item_task(conn, _task("other-task"), NOW)
    _create_execution(conn, "exec-other", "other-task")
    data = b"other"
    created, _ = repo.store_artifact(conn, store, execution_id="exec-other", session_id=SESSION,
                                     meta=_meta(data, "diagnosis_result"), data=data, now=NOW)
    with pytest.raises(InvalidTransition) as info:  # 다른 실행의 Artifact
        repo.append_event(conn, "exec-1", _event("exec-1", 3, "result_ready",
                                                 {"result_artifact_id": created.artifact_id}), "conn", NOW)
    assert info.value.reason == "result_artifact_missing"
    assert repo.get_execution(conn, "exec-1")["status"] == "running"


def test_append_event_unknown_execution_or_mismatched_id(seeded):
    with pytest.raises(NotFound):
        repo.append_event(seeded, "exec-none", _event("exec-none", 1, "accepted", {}), "conn", NOW)
    _create_execution(seeded, "exec-1")
    with pytest.raises(ValueError):
        repo.append_event(seeded, "exec-1", _event("exec-2", 1, "accepted", {}), "conn", NOW)


def test_mark_unknown_records_observation_and_allows_resume(seeded):
    conn = seeded
    _create_execution(conn, "exec-1")
    repo.append_event(conn, "exec-1", _event("exec-1", 1, "accepted", {}), "conn", NOW)
    repo.mark_unknown(conn, "exec-1", "unknown_no_start", "accepted 후 2분 동안 started 없음", LATER)
    row = repo.get_execution(conn, "exec-1")
    assert row["status"] == "unknown" and row["last_event_seq"] == 1  # 실행 주체의 seq 를 쓰지 않음
    obs = conn.execute("SELECT * FROM execution_observations WHERE execution_id='exec-1'").fetchall()
    assert len(obs) == 1 and obs[0]["kind"] == "unknown_no_start" and obs[0]["observed_at"] == LATER
    assert [r["execution_id"] for r in repo.executions_needing_attention(conn)] == ["exec-1"]
    # 기존 실행 주체의 이어지는 이벤트로 복원
    ack = repo.append_event(conn, "exec-1", _event("exec-1", 2, "started", {"runtime_ref": "pid:1"}),
                            "conn", LATER)
    assert ack.status == "running"
    with pytest.raises(NotFound):
        repo.mark_unknown(conn, "nope", "timeout", "x", LATER)


def test_mark_unknown_rejected_after_terminal(seeded):
    conn = seeded
    _create_execution(conn, "exec-1")
    repo.append_event(conn, "exec-1", _event("exec-1", 1, "accepted", {}), "conn", NOW)
    repo.append_event(conn, "exec-1", _event("exec-1", 2, "failed",
                                             {"code": "x", "message": "x", "process_stopped": True}),
                      "conn", NOW)
    with pytest.raises(InvalidTransition):
        repo.mark_unknown(conn, "exec-1", "heartbeat_lost", "x", LATER)


# --- claim -------------------------------------------------------------------


def test_claim_returns_none_without_assignment(seeded):
    assert repo.claim_execution(seeded, CONNECTOR, NOW) is None


def test_claim_same_execution_until_accepted(seeded):
    conn = seeded
    repo.insert_work_item_task(conn, _task(TASK_B, kind="code_change", predecessor=TASK_A), NOW)
    _create_execution(conn, "exec-fix-001", TASK_B, kind="code_change", connector=CONNECTOR,
                      inputs=["art-handoff-001"])
    first = repo.claim_execution(conn, CONNECTOR, NOW)
    second = repo.claim_execution(conn, CONNECTOR, LATER)
    assert first["execution_id"] == second["execution_id"] == "exec-fix-001"
    assert first["status"] == "queued"
    assert repo.claim_execution(conn, "conn-other", NOW) is None
    repo.append_event(conn, "exec-fix-001", _event("exec-fix-001", 1, "accepted", {}), CONNECTOR, LATER)
    assert repo.claim_execution(conn, CONNECTOR, LATER) is None


def test_concurrent_claims_hand_out_one_execution(seeded, db_path):
    conn = seeded
    repo.insert_work_item_task(conn, _task(TASK_B, kind="code_change", predecessor=TASK_A), NOW)
    repo.insert_work_item_task(conn, _task("fix-2", kind="code_change", predecessor=TASK_A), NOW)
    _create_execution(conn, "exec-fix-001", TASK_B, kind="code_change", connector=CONNECTOR,
                      inputs=["art-handoff-001"], now=NOW)
    _create_execution(conn, "exec-fix-002", "fix-2", kind="code_change", connector=CONNECTOR,
                      inputs=["art-handoff-001"], now=LATER)
    results = []
    barrier = threading.Barrier(2)

    def worker():
        c = connect(db_path)
        try:
            barrier.wait()
            row = repo.claim_execution(c, CONNECTOR, LATER)
            results.append(row["execution_id"] if row else None)
        finally:
            c.close()

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results == ["exec-fix-001", "exec-fix-001"]


# --- 산출물 (CONTRACT 4절) ----------------------------------------------------


def test_store_artifact_hash_mismatch(seeded, store):
    conn = seeded
    _create_execution(conn, "exec-1")
    data = b"hello"
    bad = _meta(data).model_copy(update={"sha256": "0" * 64})
    with pytest.raises(HashMismatch):
        repo.store_artifact(conn, store, execution_id="exec-1", session_id=SESSION, meta=bad,
                            data=data, now=NOW)
    bad_size = _meta(data).model_copy(update={"size": 4})
    with pytest.raises(HashMismatch):
        repo.store_artifact(conn, store, execution_id="exec-1", session_id=SESSION, meta=bad_size,
                            data=data, now=NOW)
    assert repo.artifacts_of(conn, "exec-1") == []


def test_store_artifact_reupload_returns_existing(seeded, store, tmp_path):
    conn = seeded
    _create_execution(conn, "exec-1")
    data = b'{"items": []}'
    created, is_new = repo.store_artifact(conn, store, execution_id="exec-1", session_id=SESSION,
                                          meta=_meta(data), data=data, now=NOW)
    again, is_new2 = repo.store_artifact(conn, store, execution_id="exec-1", session_id=SESSION,
                                         meta=_meta(data, name="renamed.json"), data=data, now=LATER)
    assert (is_new, is_new2) == (True, False)
    assert again == created
    assert created.sha256 == hashlib.sha256(data).hexdigest() and created.size == len(data)
    files = [p for p in (tmp_path / "artifacts").rglob("*") if p.is_file()]
    assert len(files) == 1
    assert repo.read_artifact(conn, store, created.artifact_id) == data
    assert repo.get_artifact(conn, created.artifact_id)["kind"] == "evidence"
    assert [r["artifact_id"] for r in repo.artifacts_of(conn, "exec-1")] == [created.artifact_id]


def test_same_bytes_other_session_is_separate_row_but_one_file(seeded, store, tmp_path):
    conn = seeded
    _create_execution(conn, "exec-1")
    repo.insert_work_item_task(conn, _task("other-task", session_id=OTHER_SESSION), NOW)
    _create_execution(conn, "exec-2", "other-task")
    data = b"shared bytes"
    a, _ = repo.store_artifact(conn, store, execution_id="exec-1", session_id=SESSION,
                               meta=_meta(data), data=data, now=NOW)
    b, is_new = repo.store_artifact(conn, store, execution_id="exec-2", session_id=OTHER_SESSION,
                                    meta=_meta(data), data=data, now=NOW)
    assert is_new and a.artifact_id != b.artifact_id
    assert len([p for p in (tmp_path / "artifacts").rglob("*") if p.is_file()]) == 1
    assert repo.get_artifact(conn, b.artifact_id)["session_id"] == OTHER_SESSION


def test_read_artifact_missing(seeded, store):
    conn = seeded
    with pytest.raises(NotFound):
        repo.read_artifact(conn, store, "art-none")
    _create_execution(conn, "exec-1")
    data = b"gone"
    created, _ = repo.store_artifact(conn, store, execution_id="exec-1", session_id=SESSION,
                                     meta=_meta(data), data=data, now=NOW)
    (store.root / repo.get_artifact(conn, created.artifact_id)["store_ref"]).unlink()
    with pytest.raises(ArtifactMissing):
        repo.read_artifact(conn, store, created.artifact_id)


def test_download_allowed_three_paths_and_denial(seeded, store):
    conn = seeded
    _create_execution(conn, "exec-diag")
    evidence = b'{"report_date": "2026-09-19"}'
    ev, _ = repo.store_artifact(conn, store, execution_id="exec-diag", session_id=SESSION,
                                meta=_meta(evidence), data=evidence, now=NOW)
    result = b'{"outcome": "ready_for_handoff"}'
    res, _ = repo.store_artifact(conn, store, execution_id="exec-diag", session_id=SESSION,
                                 meta=_meta(result, "diagnosis_result"), data=result, now=NOW)
    trace_bytes = b"[]"
    trace, _ = repo.store_artifact(conn, store, execution_id="exec-diag", session_id=SESSION,
                                   meta=_meta(trace_bytes, "tool_trace"), data=trace_bytes, now=NOW)
    manifest = json.dumps({
        "contract_version": 1,
        "source_execution_id": "exec-diag",
        "source_kind": "diagnosis",
        "source_result_artifact_id": res.artifact_id,
        "inputs": [],
        "attachments": [{"evidence_id": "response-after", "version": "1",
                         "content_type": "application/json", "artifact_id": ev.artifact_id,
                         "sha256": ev.sha256}],
    }).encode()
    bundle, _ = repo.store_artifact(conn, store, execution_id="exec-diag", session_id=SESSION,
                                    meta=_meta(manifest, "handoff_bundle", "manifest.json"),
                                    data=manifest, now=NOW)
    repo.insert_work_item_task(conn, _task(TASK_B, kind="code_change", predecessor=TASK_A), NOW)
    _create_execution(conn, "exec-fix", TASK_B, kind="code_change", connector=CONNECTOR,
                      inputs=[bundle.artifact_id], predecessor="exec-diag")
    own = b"diff"
    mine, _ = repo.store_artifact(conn, store, execution_id="exec-fix", session_id=SESSION,
                                  meta=_meta(own, "diff", "a.diff"), data=own, now=NOW)

    assert repo.download_allowed(conn, store, "exec-fix", bundle.artifact_id)  # 입력
    assert repo.download_allowed(conn, store, "exec-fix", ev.artifact_id)  # manifest 첨부
    assert repo.download_allowed(conn, store, "exec-fix", mine.artifact_id)  # 자기 산출물
    assert repo.download_allowed(conn, store, "exec-fix", res.artifact_id)  # source_result_artifact_id
    assert not repo.download_allowed(conn, store, "exec-fix", trace.artifact_id)  # 나열되지 않은 A 산출물
    assert not repo.download_allowed(conn, store, "exec-fix", "art-none")
    assert not repo.download_allowed(conn, store, "exec-none", mine.artifact_id)


# --- Step 6 웹 화면이 쓰는 읽기·갱신 보조 -------------------------------------


def test_list_executions_orders_by_attempt_no(seeded):
    conn = seeded
    assert repo.list_executions(conn, TASK_A) == []
    _create_execution(conn, "exec-1", attempt_no=1)
    repo.release_execution(conn, "exec-1", NOW)
    _create_execution(conn, "exec-2", attempt_no=2, start_key="req:2")
    assert [r["execution_id"] for r in repo.list_executions(conn, TASK_A)] == ["exec-1", "exec-2"]
    assert [r["attempt_no"] for r in repo.list_executions(conn, TASK_A)] == [1, 2]


def test_update_task_choice_sets_manual_selection_and_target(seeded):
    conn = seeded
    repo.update_task_choice(
        conn, TASK_A, chosen_agent_id="agent-ops-demo", target={"run_id": "daily-0921-0900"}
    )
    row = repo.get_task(conn, TASK_A)
    assert row["selection_mode"] == "manual"
    assert row["chosen_agent_id"] == "agent-ops-demo"
    assert json.loads(row["target_json"]) == {"run_id": "daily-0921-0900"}
    with pytest.raises(NotFound):
        repo.update_task_choice(conn, "nope", chosen_agent_id="a", target={})


def test_get_verdict_reads_latest_row_for_execution(seeded):
    conn = seeded
    _create_execution(conn, "exec-1")
    assert repo.get_verdict(conn, "exec-1") is None
    conn.execute(
        "INSERT INTO task_verdicts (task_id, execution_id, verdict_json, decided_at) VALUES (?, ?, ?, ?)",
        (TASK_A, "exec-1", json.dumps({"outcome": "passed", "checks": []}), NOW),
    )
    row = repo.get_verdict(conn, "exec-1")
    assert json.loads(row["verdict_json"])["outcome"] == "passed"
    assert row["decided_at"] == NOW


def test_list_connect_codes_newest_first_with_state_columns(conn):
    first = repo.issue_connect_code(conn, NOW)
    second = repo.issue_connect_code(conn, LATER)
    repo.revoke_connect_code(conn, first, LATER)
    rows = repo.list_connect_codes(conn)
    assert [r["code"] for r in rows] == [second, first]
    assert rows[1]["revoked_at"] == LATER and rows[0]["revoked_at"] is None
    assert rows[0]["used_at"] is None


# --- Step 8 워커가 쓰는 서버 확정·스캔 보조 --------------------------------------


def test_fail_execution_marks_failed_without_event(seeded):
    conn = seeded
    _create_execution(conn, "exec-1")
    repo.fail_execution(conn, "exec-1", code="daily_limit_reached", message="한도", now=LATER)
    row = repo.get_execution(conn, "exec-1")
    assert (row["status"], row["failed_code"], row["failed_message"]) == ("failed", "daily_limit_reached", "한도")
    assert row["process_stopped"] == 1 and row["finished_at"] == LATER and row["last_event_seq"] == 0
    assert repo.list_events(conn, "exec-1") == []
    with pytest.raises(InvalidTransition):
        repo.fail_execution(conn, "exec-1", code="x", message="x", now=LATER)
    with pytest.raises(NotFound):
        repo.fail_execution(conn, "nope", code="x", message="x", now=LATER)


def test_record_observation_keeps_status(running):
    conn = running
    repo.record_observation(conn, "exec-1", "heartbeat_lost", "90초 미수신", LATER)
    assert repo.get_execution(conn, "exec-1")["status"] == "running"
    rows = repo.observations_of(conn, "exec-1")
    assert [(r["kind"], r["observed_at"]) for r in rows] == [("heartbeat_lost", LATER)]
    with pytest.raises(NotFound):
        repo.record_observation(conn, "nope", "heartbeat_lost", "x", LATER)


def test_record_verdict_with_finish_completes_task_and_releases(seeded, store):
    conn = seeded
    _create_execution(conn, "exec-1")
    verdict = {"outcome": "passed", "checks": [{"code": "a", "passed": True, "detail": "ok"}]}
    repo.record_verdict(
        conn, task_id=TASK_A, execution_id="exec-1", verdict=verdict,
        status="완료", reason="판정 근거: 1/1", finish=True, now=LATER,
    )
    task = repo.get_task(conn, TASK_A)
    assert (task["status"], task["status_reason"], task["finished_at"]) == ("완료", "판정 근거: 1/1", LATER)
    assert repo.get_execution(conn, "exec-1")["released_at"] == LATER
    assert json.loads(repo.get_verdict(conn, "exec-1")["verdict_json"]) == verdict


def test_record_verdict_without_finish_keeps_lock(seeded):
    conn = seeded
    _create_execution(conn, "exec-1")
    repo.record_verdict(
        conn, task_id=TASK_A, execution_id="exec-1", verdict={"outcome": "failed", "checks": []},
        status="확인 필요", reason="미충족: a", finish=False, now=LATER,
    )
    task = repo.get_task(conn, TASK_A)
    assert (task["status"], task["finished_at"]) == ("확인 필요", None)
    assert repo.get_execution(conn, "exec-1")["released_at"] is None
    assert [r["execution_id"] for r in repo.results_awaiting_verdict(conn, "diagnosis")] == []


def test_finish_task_sets_finished_at_and_releases(seeded):
    conn = seeded
    _create_execution(conn, "exec-1")
    repo.finish_task(conn, task_id=TASK_A, execution_id="exec-1", status="실패", reason="timeout · x", now=LATER)
    task = repo.get_task(conn, TASK_A)
    assert (task["status"], task["status_reason"], task["finished_at"]) == ("실패", "timeout · x", LATER)
    assert repo.get_execution(conn, "exec-1")["released_at"] == LATER
    with pytest.raises(NotFound):
        repo.finish_task(conn, task_id="nope", execution_id="exec-1", status="실패", reason="x", now=LATER)


def test_executions_by_filters_active_status_and_kind(seeded, store):
    conn = seeded
    repo.insert_work_item_task(conn, _task(TASK_B, kind="code_change", predecessor=TASK_A), NOW)
    _create_execution(conn, "exec-1")
    _create_execution(conn, "exec-2", TASK_B, kind="code_change", inputs=("art-1",))
    repo.append_event(conn, "exec-2", _event("exec-2", 1, "accepted", {}), "conn", NOW)
    assert [r["execution_id"] for r in repo.executions_by(conn, statuses=("queued",))] == ["exec-1"]
    assert [r["execution_id"] for r in repo.executions_by(conn, statuses=("queued", "accepted"), kind="code_change")] == ["exec-2"]
    repo.release_execution(conn, "exec-1", LATER)
    assert repo.executions_by(conn, statuses=("queued",)) == []


def test_results_awaiting_verdict_lists_result_ready_without_verdict(seeded, store):
    conn = seeded
    _create_execution(conn, "exec-1")
    repo.append_event(conn, "exec-1", _event("exec-1", 1, "accepted", {}), "conn", NOW)
    repo.append_event(conn, "exec-1", _event("exec-1", 2, "started", {"runtime_ref": "pid:1"}), "conn", NOW)
    assert repo.results_awaiting_verdict(conn, "diagnosis") == []
    data = b'{"outcome": "ready_for_handoff"}'
    created, _ = repo.store_artifact(
        conn, store, execution_id="exec-1", session_id=SESSION,
        meta=_meta(data, kind="diagnosis_result"), data=data, now=NOW,
    )
    repo.append_event(
        conn, "exec-1", _event("exec-1", 3, "result_ready", {"result_artifact_id": created.artifact_id}),
        "conn", NOW,
    )
    assert [r["execution_id"] for r in repo.results_awaiting_verdict(conn, "diagnosis")] == ["exec-1"]
    assert repo.results_awaiting_verdict(conn, "code_change") == []
    assert [r["execution_id"] for r in repo.results_awaiting_verdict(conn)] == ["exec-1"]  # 모든 종류
    conn.execute(
        "INSERT INTO task_verdicts (task_id, execution_id, verdict_json, decided_at) VALUES (?, ?, ?, ?)",
        (TASK_A, "exec-1", json.dumps({"outcome": "passed", "checks": []}), NOW),
    )
    assert repo.results_awaiting_verdict(conn, "diagnosis") == []


def test_tasks_with_ready_predecessor_by_completed_predecessor(seeded):
    conn = seeded
    repo.insert_work_item_task(conn, _task(TASK_B, kind="code_change", predecessor=TASK_A), NOW)
    assert repo.tasks_with_ready_predecessor(conn) == []
    repo.update_task_status(conn, TASK_A, "완료", "판정 근거: 12/12", now=NOW)
    assert repo.tasks_with_ready_predecessor(conn) == []  # finished_at 이 없으면 완료로 보지 않는다
    repo.update_task_status(conn, TASK_A, "완료", "판정 근거: 12/12", finished_at=LATER, now=LATER)
    assert [r["task_id"] for r in repo.tasks_with_ready_predecessor(conn)] == [TASK_B]
    repo.update_task_status(conn, TASK_B, "실패", "검토 거절", finished_at=LATER, now=LATER)
    assert repo.tasks_with_ready_predecessor(conn) == []


def test_get_connector(conn):
    code = repo.issue_connect_code(conn, NOW)
    connector_id, _ = repo.exchange_connect_code(conn, code, NOW)
    repo.touch_connector(conn, connector_id, LATER, "exec-1")
    row = repo.get_connector(conn, connector_id)
    assert (row["last_seen_at"], row["current_execution_id"]) == (LATER, "exec-1")
    assert repo.get_connector(conn, "nope") is None


# --- phase 5: 세션 등록·Chain·대본 플래그 -----------------------------------


def _chain(chain_id: str = "chain-1", session_id: str = SESSION, **overrides) -> dict:
    chain = {
        "chain_id": chain_id,
        "session_id": session_id,
        "title": "일일 보고서 복구",
        "source": "github",
    }
    chain.update(overrides)
    return chain


def test_agent_demo_scripted_roundtrip(conn):
    repo.upsert_agent(conn, _agent())
    assert repo.get_agent(conn, "agent-ops-demo")["demo_scripted"] == 0
    repo.upsert_agent(conn, _agent(demo_scripted=True))
    assert repo.get_agent(conn, "agent-ops-demo")["demo_scripted"] == 1
    repo.upsert_agent(conn, _agent(demo_scripted=False))
    assert repo.get_agent(conn, "agent-ops-demo")["demo_scripted"] == 0


def test_session_agent_register_is_idempotent_and_ordered_by_registration(seeded):
    conn = seeded
    repo.upsert_agent(conn, _agent())
    repo.upsert_agent(conn, _agent("agent-codex-mac", connection_type="local", owner_scope="personal",
                                   capabilities=[{"code": "code.modify",
                                                  "scope": {"repository_id": "demo-report-repo"}}]))
    repo.register_session_agent(conn, SESSION, "agent-codex-mac", NOW)
    repo.register_session_agent(conn, SESSION, "agent-ops-demo", LATER)
    repo.register_session_agent(conn, SESSION, "agent-codex-mac", LATER)  # 멱등 — 처음 시각 유지
    rows = repo.list_session_agents(conn, SESSION)
    assert [r["agent_id"] for r in rows] == ["agent-codex-mac", "agent-ops-demo"]
    assert [r["registered_at"] for r in rows] == [NOW, LATER]
    assert rows[1]["name"] == "운영 진단 데모"  # agents 행이 그대로 온다
    assert repo.is_session_agent(conn, SESSION, "agent-ops-demo") is True
    assert repo.is_session_agent(conn, SESSION, "nope") is False


def test_session_agent_same_timestamp_keeps_insert_order(seeded):
    conn = seeded
    for agent_id in ("agent-z", "agent-a", "agent-m"):
        repo.upsert_agent(conn, _agent(agent_id))
        repo.register_session_agent(conn, SESSION, agent_id, NOW)
    assert [r["agent_id"] for r in repo.list_session_agents(conn, SESSION)] == [
        "agent-z", "agent-a", "agent-m"]


def test_session_agent_is_isolated_per_session_and_unregister(seeded):
    conn = seeded
    repo.upsert_agent(conn, _agent())
    repo.register_session_agent(conn, SESSION, "agent-ops-demo", NOW)
    assert repo.list_session_agents(conn, OTHER_SESSION) == []
    assert repo.is_session_agent(conn, OTHER_SESSION, "agent-ops-demo") is False
    repo.unregister_session_agent(conn, SESSION, "agent-ops-demo")
    assert repo.list_session_agents(conn, SESSION) == []
    assert repo.is_session_agent(conn, SESSION, "agent-ops-demo") is False
    repo.unregister_session_agent(conn, SESSION, "agent-ops-demo")  # 이미 없어도 오류 없음
    assert repo.get_agent(conn, "agent-ops-demo") is not None  # 카탈로그 Agent 는 남는다


def test_session_agent_requires_existing_agent_and_session(seeded):
    conn = seeded
    with pytest.raises(sqlite3.IntegrityError):
        repo.register_session_agent(conn, SESSION, "nope", NOW)
    repo.upsert_agent(conn, _agent())
    with pytest.raises(sqlite3.IntegrityError):
        repo.register_session_agent(conn, "no-such-session", "agent-ops-demo", NOW)


def test_delete_agent_removes_session_registrations(seeded):
    conn = seeded
    repo.upsert_agent(conn, _agent())
    repo.register_session_agent(conn, SESSION, "agent-ops-demo", NOW)
    repo.register_session_agent(conn, OTHER_SESSION, "agent-ops-demo", NOW)
    repo.delete_agent(conn, "agent-ops-demo")
    assert repo.get_agent(conn, "agent-ops-demo") is None
    assert repo.list_session_agents(conn, SESSION) == []
    assert conn.execute("SELECT COUNT(*) FROM session_agents").fetchone()[0] == 0
    with pytest.raises(NotFound):
        repo.delete_agent(conn, "agent-ops-demo")


def test_chain_insert_get_list_and_skipped_roundtrip(seeded):
    conn = seeded
    skipped = [{"key": "#43", "title": "문서 정리", "reason": "일치하는 능력 없음"}]
    repo.insert_chain(conn, _chain("chain-2", skipped=skipped), LATER)
    repo.insert_chain(conn, _chain("chain-1"), NOW)
    repo.insert_chain(conn, _chain("chain-other", session_id=OTHER_SESSION, source="jira"), NOW)
    row = repo.get_chain(conn, "chain-2")
    assert (row["session_id"], row["title"], row["source"], row["created_at"]) == (
        SESSION, "일일 보고서 복구", "github", LATER)
    assert row["started_at"] is None
    assert json.loads(row["skipped_json"]) == skipped
    assert json.loads(repo.get_chain(conn, "chain-1")["skipped_json"]) == []
    assert [r["chain_id"] for r in repo.list_chains(conn, SESSION)] == ["chain-1", "chain-2"]
    assert [r["chain_id"] for r in repo.list_chains(conn, OTHER_SESSION)] == ["chain-other"]
    assert repo.get_chain(conn, "nope") is None


def test_chain_insert_rejects_bad_source_and_unknown_session(seeded):
    conn = seeded
    with pytest.raises(sqlite3.IntegrityError):
        repo.insert_chain(conn, _chain(source="email"), NOW)
    with pytest.raises(sqlite3.IntegrityError):
        repo.insert_chain(conn, _chain(session_id="no-such-session"), NOW)


def test_mark_chain_started_only_once(seeded):
    conn = seeded
    repo.insert_chain(conn, _chain(), NOW)
    repo.mark_chain_started(conn, "chain-1", NOW)
    repo.mark_chain_started(conn, "chain-1", LATER)
    assert repo.get_chain(conn, "chain-1")["started_at"] == NOW
    with pytest.raises(NotFound):
        repo.mark_chain_started(conn, "nope", NOW)


def test_task_chain_id_and_source_ref_roundtrip(seeded):
    conn = seeded
    row = repo.get_task(conn, TASK_A)
    assert row["chain_id"] is None and row["source_ref"] is None  # 직접 등록 Task
    repo.insert_chain(conn, _chain(), NOW)
    task = {**_task("t-imported"), "chain_id": "chain-1", "source_ref": "#42"}
    repo.insert_work_item_task(conn, task, NOW)
    row = repo.get_task(conn, "t-imported")
    assert (row["chain_id"], row["source_ref"]) == ("chain-1", "#42")
    with pytest.raises(sqlite3.IntegrityError):
        repo.insert_work_item_task(conn, {**_task("t-bad"), "chain_id": "no-such-chain"}, NOW)
    assert repo.get_task(conn, "t-bad") is None  # 롤백됨


def test_tasks_of_chain_follows_predecessor_order(seeded):
    """created_at·task_id 정렬이 모두 어긋나도 선행 없는 것부터 predecessor 체인 순서로 돌려준다."""
    conn = seeded
    repo.insert_chain(conn, _chain(), NOW)
    repo.insert_chain(conn, _chain("chain-2"), NOW)
    # A → B → C. 삽입은 선행이 먼저 있어야 하므로 A·B·C 순이지만 created_at 은 거꾸로, ID 는 역순 알파벳.
    repo.insert_work_item_task(conn, {**_task("t-root"), "chain_id": "chain-1"}, "2026-09-20T00:00:09Z")
    repo.insert_work_item_task(conn, {**_task("t-mid", kind="code_change", predecessor="t-root"),
                            "chain_id": "chain-1"}, "2026-09-20T00:00:05Z")
    repo.insert_work_item_task(conn, {**_task("t-last", kind="code_change", predecessor="t-mid"),
                            "chain_id": "chain-1"}, "2026-09-20T00:00:01Z")
    # 다른 체인·체인 없는 Task 는 섞이지 않는다
    repo.insert_work_item_task(conn, {**_task("t-other"), "chain_id": "chain-2"}, NOW)
    assert [r["task_id"] for r in repo.tasks_of_chain(conn, "chain-1")] == ["t-root", "t-mid", "t-last"]
    assert [r["task_id"] for r in repo.tasks_of_chain(conn, "chain-2")] == ["t-other"]
    assert repo.tasks_of_chain(conn, "nope") == []


def test_tasks_of_chain_treats_predecessor_outside_chain_as_root(seeded):
    conn = seeded
    repo.insert_chain(conn, _chain(), NOW)
    # TASK_A(체인 없음) 를 선행으로 갖는 B 가 체인의 첫 Task 다
    repo.insert_work_item_task(conn, {**_task("t-b", kind="code_change", predecessor=TASK_A),
                            "chain_id": "chain-1"}, LATER)
    repo.insert_work_item_task(conn, {**_task("t-c", kind="code_change", predecessor="t-b"),
                            "chain_id": "chain-1"}, NOW)
    assert [r["task_id"] for r in repo.tasks_of_chain(conn, "chain-1")] == ["t-b", "t-c"]


def test_list_tasks_is_unchanged_by_chain_columns(seeded):
    conn = seeded
    repo.insert_chain(conn, _chain(), NOW)
    repo.insert_work_item_task(conn, {**_task("t-imported"), "chain_id": "chain-1", "source_ref": "OPS-42"}, LATER)
    assert [r["task_id"] for r in repo.list_tasks(conn, SESSION)] == [TASK_A, "t-imported"]


# --- phase 6: 업무 종류·후속 규칙 (ADR-0009) -------------------------------------


REVIEW = KindSpec(
    kind="review", label="검토", capability_code="review", scope_key="repository_id",
    input_kinds=["diff", "code_change_result"], output_kind="generic_result",
    outcomes=["approved", "changes_requested", "needs_information"],
    instructions="diff 를 읽고 검토하세요.", builtin=False,
)
FIX_TO_REVIEW = SuccessorRule(
    from_kind="bug_fix", on_outcomes=["ready_for_review"], to_kind="review",
    handoff_kinds=["diff", "code_change_result", "test_log_after"],
)


def _verdict_row(conn, task_id: str, execution_id: str, outcome: str = "passed") -> None:
    conn.execute(
        "INSERT INTO task_verdicts (task_id, execution_id, verdict_json, decided_at) VALUES (?, ?, ?, ?)",
        (task_id, execution_id, json.dumps({"outcome": outcome, "checks": []}), NOW),
    )


def _to_result_ready(conn, store, execution_id: str, kind: str = "diagnosis_result") -> str:
    """queued 실행을 accepted → started → result_ready 로. 결과 산출물 ID 를 돌려준다."""
    repo.append_event(conn, execution_id, _event(execution_id, 1, "accepted", {}), "conn", NOW)
    repo.append_event(conn, execution_id, _event(execution_id, 2, "started", {"runtime_ref": "pid:1"}), "conn", NOW)
    data = json.dumps({"outcome": "ready_for_handoff", "id": execution_id}).encode()
    created, _ = repo.store_artifact(conn, store, execution_id=execution_id, session_id=SESSION,
                                     meta=_meta(data, kind), data=data, now=NOW)
    repo.append_event(conn, execution_id, _event(execution_id, 3, "result_ready",
                                                 {"result_artifact_id": created.artifact_id}), "conn", NOW)
    return created.artifact_id


def test_create_session_seeds_builtin_kinds_and_rule_per_session(conn):
    """내장 종류 2개(bug_fix·code_review)와 내장 규칙 1개(bug_fix → code_review)를 세션마다 seed 한다."""
    repo.create_session(conn, SESSION, NOW)
    assert repo.list_kinds(conn, SESSION) == list(BUILTIN_KINDS)
    rules = repo.list_rules(conn, SESSION)
    assert [rule for _, rule in rules] == list(BUILTIN_RULES)
    assert all(rule_id.startswith("rule-") for rule_id, _ in rules)
    assert repo.list_kinds(conn, OTHER_SESSION) == [] and repo.list_rules(conn, OTHER_SESSION) == []
    repo.create_session(conn, OTHER_SESSION, LATER)
    assert repo.list_kinds(conn, OTHER_SESSION) == list(BUILTIN_KINDS)
    assert len(repo.list_rules(conn, OTHER_SESSION)) == 1
    assert repo.get_kind(conn, SESSION, "bug_fix") == BUILTIN_KINDS[0]
    assert repo.get_kind(conn, SESSION, "diagnosis") is None  # 진단 데모 종류는 더 이상 내장이 아니다
    assert repo.get_kind(conn, SESSION, "review") is None


def test_create_session_is_atomic_with_seed(conn):
    repo.create_session(conn, SESSION, NOW)
    with pytest.raises(sqlite3.IntegrityError):
        repo.create_session(conn, SESSION, LATER)
    assert len(repo.list_kinds(conn, SESSION)) == 2 and len(repo.list_rules(conn, SESSION)) == 1


def test_insert_kind_roundtrip_ordering_and_duplicate(sessions):
    conn = sessions
    zebra = REVIEW.model_copy(update={"kind": "zebra", "capability_code": "zebra"})
    repo.insert_kind(conn, SESSION, zebra, NOW)
    repo.insert_kind(conn, SESSION, REVIEW, LATER)
    apple = REVIEW.model_copy(update={"kind": "apple", "capability_code": "apple"})
    repo.insert_kind(conn, SESSION, apple, NOW)
    assert repo.get_kind(conn, SESSION, "review") == REVIEW
    assert repo.get_kind(conn, OTHER_SESSION, "review") is None  # 세션 격리
    # 내장 먼저(BUILTIN_KINDS 순), 그 다음 created_at·kind 순
    assert [k.kind for k in repo.list_kinds(conn, SESSION)] == ["bug_fix", "code_review", "apple", "zebra", "review"]
    with pytest.raises(DuplicateKind):
        repo.insert_kind(conn, SESSION, REVIEW, LATER)
    with pytest.raises(DuplicateKind):  # 내장 이름 재등록도 중복
        repo.insert_kind(conn, SESSION, BUILTIN_KINDS[0], LATER)
    with pytest.raises(sqlite3.IntegrityError):  # 없는 세션
        repo.insert_kind(conn, "no-such-session", REVIEW, NOW)


def test_delete_kind_protects_builtin_and_in_use(sessions):
    conn = sessions
    with pytest.raises(KindProtected):
        repo.delete_kind(conn, SESSION, "bug_fix")
    with pytest.raises(NotFound):
        repo.delete_kind(conn, SESSION, "review")
    repo.insert_kind(conn, SESSION, REVIEW, NOW)
    repo.insert_kind(conn, OTHER_SESSION, REVIEW, NOW)
    rule_id = repo.insert_rule(conn, SESSION, FIX_TO_REVIEW, NOW)
    with pytest.raises(KindInUse):  # 규칙이 참조
        repo.delete_kind(conn, SESSION, "review")
    repo.delete_rule(conn, SESSION, rule_id)
    repo.insert_work_item_task(conn, _task("review-1", kind="review"), NOW)
    with pytest.raises(KindInUse):  # Task 가 사용
        repo.delete_kind(conn, SESSION, "review")
    repo.delete_kind(conn, OTHER_SESSION, "review")  # 다른 세션의 같은 이름은 무관
    assert repo.get_kind(conn, OTHER_SESSION, "review") is None
    assert repo.get_kind(conn, SESSION, "review") == REVIEW


def test_delete_kind_success(sessions):
    conn = sessions
    repo.insert_kind(conn, SESSION, REVIEW, NOW)
    repo.delete_kind(conn, SESSION, "review")
    assert repo.get_kind(conn, SESSION, "review") is None
    assert [k.kind for k in repo.list_kinds(conn, SESSION)] == ["bug_fix", "code_review"]


def test_rule_insert_get_list_duplicate_and_delete(sessions):
    conn = sessions
    with pytest.raises(NotFound):  # to_kind 미등록
        repo.insert_rule(conn, SESSION, FIX_TO_REVIEW, NOW)
    repo.insert_kind(conn, SESSION, REVIEW, NOW)
    rule_id = repo.insert_rule(conn, SESSION, FIX_TO_REVIEW, LATER)
    assert rule_id.startswith("rule-")
    assert repo.get_rule(conn, SESSION, "bug_fix", "review") == FIX_TO_REVIEW
    assert repo.get_rule(conn, SESSION, "bug_fix", "code_review") == BUILTIN_RULES[0]
    assert repo.get_rule(conn, SESSION, "review", "bug_fix") is None
    assert repo.get_rule(conn, OTHER_SESSION, "bug_fix", "review") is None
    rules = repo.list_rules(conn, SESSION)
    # 내장 규칙이 먼저, 나중에 넣은 규칙이 끝
    assert [rule for _, rule in rules[:1]] == list(BUILTIN_RULES)
    assert rules[1] == (rule_id, FIX_TO_REVIEW)
    with pytest.raises(DuplicateRule):  # 같은 (from, to) — on_outcomes 가 달라도
        repo.insert_rule(conn, SESSION, FIX_TO_REVIEW.model_copy(update={"on_outcomes": ["needs_information"]}), LATER)
    with pytest.raises(NotFound):  # from_kind 미등록
        repo.insert_rule(conn, SESSION, SuccessorRule(from_kind="nope", on_outcomes=["x"], to_kind="review",
                                                      handoff_kinds=["diff"]), NOW)
    repo.delete_rule(conn, SESSION, rule_id)
    assert repo.get_rule(conn, SESSION, "bug_fix", "review") is None
    with pytest.raises(NotFound):
        repo.delete_rule(conn, SESSION, rule_id)
    with pytest.raises(NotFound):  # 다른 세션의 rule_id 로는 지울 수 없다
        builtin_id = repo.list_rules(conn, SESSION)[0][0]
        repo.delete_rule(conn, OTHER_SESSION, builtin_id)


def test_builtin_rule_can_be_deleted(sessions):
    conn = sessions
    [rule_id] = [rid for rid, rule in repo.list_rules(conn, SESSION) if rule.from_kind == "bug_fix"]
    repo.delete_rule(conn, SESSION, rule_id)
    assert repo.list_rules(conn, SESSION) == []
    assert len(repo.list_rules(conn, OTHER_SESSION)) == 1
    assert repo.get_rule(conn, SESSION, "bug_fix", "code_review") is None


def test_list_rules_orders_by_created_at_then_rule_id(sessions):
    conn = sessions
    repo.insert_kind(conn, SESSION, REVIEW, NOW)
    later_rule = repo.insert_rule(conn, SESSION, FIX_TO_REVIEW, LATER)
    earlier = SuccessorRule(from_kind="review", on_outcomes=["changes_requested"], to_kind="bug_fix",
                            handoff_kinds=["generic_result"])
    earlier_rule = repo.insert_rule(conn, SESSION, earlier, NOW)
    ids = [rid for rid, _ in repo.list_rules(conn, SESSION)]
    assert ids[-1] == later_rule and earlier_rule in ids[:-1]


def test_insert_task_requires_registered_kind(seeded):
    conn = seeded
    with pytest.raises(NotFound) as info:
        repo.insert_work_item_task(conn, _task("review-1", kind="review"), NOW)
    assert "review" in str(info.value) and "등록되지 않음" in str(info.value)
    assert repo.get_task(conn, "review-1") is None
    repo.insert_kind(conn, SESSION, REVIEW, NOW)
    repo.insert_work_item_task(conn, _task("review-1", kind="review"), NOW)
    assert repo.get_task(conn, "review-1")["kind"] == "review"
    with pytest.raises(NotFound):  # 다른 세션의 등록은 세지 않는다
        repo.insert_work_item_task(conn, _task("review-2", session_id=OTHER_SESSION, kind="review"), NOW)


def test_tasks_with_ready_predecessor_by_result_ready_with_verdict(seeded, store):
    conn = seeded
    repo.insert_work_item_task(conn, _task(TASK_B, kind="code_change", predecessor=TASK_A), NOW)
    _create_execution(conn, "exec-1")
    _to_result_ready(conn, store, "exec-1")
    assert repo.tasks_with_ready_predecessor(conn) == []  # 판정 없음 → 제외
    repo.record_verdict(conn, task_id=TASK_A, execution_id="exec-1", verdict={"outcome": "passed", "checks": []},
                        status="확인 필요", reason="검토 대기", finish=False, now=LATER)
    assert [r["task_id"] for r in repo.tasks_with_ready_predecessor(conn)] == [TASK_B]  # 사람 승인 전
    repo.update_task_status(conn, TASK_A, "완료", "검토 승인", finished_at=LATER, review_decision="approve", now=LATER)
    repo.release_execution(conn, "exec-1", LATER)
    assert [r["task_id"] for r in repo.tasks_with_ready_predecessor(conn)] == [TASK_B]
    repo.update_task_status(conn, TASK_B, "완료", "검토 승인", finished_at=LATER, now=LATER)
    assert repo.tasks_with_ready_predecessor(conn) == []  # 마감된 후속은 제외


def test_tasks_with_ready_predecessor_excludes_failed_predecessor(seeded, store):
    conn = seeded
    repo.insert_work_item_task(conn, _task(TASK_B, kind="code_change", predecessor=TASK_A), NOW)
    _create_execution(conn, "exec-1")
    _to_result_ready(conn, store, "exec-1")
    _verdict_row(conn, TASK_A, "exec-1")
    assert [r["task_id"] for r in repo.tasks_with_ready_predecessor(conn)] == [TASK_B]
    repo.update_task_status(conn, TASK_A, "실패", "검토 거절", finished_at=LATER, review_decision="close", now=LATER)
    assert repo.tasks_with_ready_predecessor(conn) == []  # 실행이 아직 활성이어도 선행 실패면 제외


def test_tasks_with_ready_predecessor_ignores_released_result_without_completion(seeded, store):
    conn = seeded
    repo.insert_work_item_task(conn, _task(TASK_B, kind="code_change", predecessor=TASK_A), NOW)
    _create_execution(conn, "exec-1")
    _to_result_ready(conn, store, "exec-1")
    _verdict_row(conn, TASK_A, "exec-1")
    repo.release_execution(conn, "exec-1", LATER)  # 활성 실행이 아니고 선행도 완료가 아님
    assert repo.tasks_with_ready_predecessor(conn) == []


def test_tasks_with_ready_predecessor_orders_by_created_at_then_task_id(seeded, store):
    conn = seeded
    repo.insert_work_item_task(conn, _task("fix-z", kind="code_change", predecessor=TASK_A), NOW)
    repo.insert_work_item_task(conn, _task("fix-a", kind="code_change", predecessor=TASK_A), LATER)
    repo.insert_work_item_task(conn, _task("fix-m", kind="code_change", predecessor=TASK_A), NOW)
    repo.update_task_status(conn, TASK_A, "완료", "판정 근거: 12/12", finished_at=LATER, now=LATER)
    assert [r["task_id"] for r in repo.tasks_with_ready_predecessor(conn)] == ["fix-m", "fix-z", "fix-a"]


def test_predecessor_ready_execution_picks_latest_attempt_with_verdict(seeded, store):
    conn = seeded
    assert repo.predecessor_ready_execution(conn, TASK_A) is None
    _create_execution(conn, "exec-1")
    _to_result_ready(conn, store, "exec-1")
    assert repo.predecessor_ready_execution(conn, TASK_A) is None  # 판정 없음
    _verdict_row(conn, TASK_A, "exec-1", "failed")
    assert repo.predecessor_ready_execution(conn, TASK_A)["execution_id"] == "exec-1"
    repo.release_execution(conn, "exec-1", LATER)
    assert repo.predecessor_ready_execution(conn, TASK_A)["execution_id"] == "exec-1"  # 해제돼도 결과는 남는다
    _create_execution(conn, "exec-2", attempt_no=2, start_key="req:retry-1")
    _to_result_ready(conn, store, "exec-2")
    assert repo.predecessor_ready_execution(conn, TASK_A)["execution_id"] == "exec-1"  # 2차는 아직 판정 없음
    _verdict_row(conn, TASK_A, "exec-2")
    assert repo.predecessor_ready_execution(conn, TASK_A)["execution_id"] == "exec-2"
    assert repo.predecessor_ready_execution(conn, "nope") is None


def test_predecessor_ready_execution_ignores_failed_attempt(seeded, store):
    conn = seeded
    _create_execution(conn, "exec-1")
    repo.append_event(conn, "exec-1", _event("exec-1", 1, "accepted", {}), "conn", NOW)
    repo.append_event(conn, "exec-1", _event("exec-1", 2, "failed",
                                             {"code": "timeout", "message": "x", "process_stopped": True}),
                      "conn", NOW)
    _verdict_row(conn, TASK_A, "exec-1")
    assert repo.predecessor_ready_execution(conn, TASK_A) is None


def test_artifacts_of_kinds_filters_and_orders(seeded, store):
    conn = seeded
    _create_execution(conn, "exec-1")
    ids = {}
    for kind, data, now in (("evidence", b"e1", LATER), ("diff", b"d", NOW), ("evidence", b"e0", NOW),
                            ("tool_trace", b"[]", NOW)):
        created, _ = repo.store_artifact(conn, store, execution_id="exec-1", session_id=SESSION,
                                         meta=_meta(data, kind), data=data, now=now)
        ids[data] = created.artifact_id
    rows = repo.artifacts_of_kinds(conn, "exec-1", ["diff", "evidence"])
    expected_first_two = sorted([ids[b"d"], ids[b"e0"]])  # 같은 created_at 은 artifact_id 순
    assert [r["artifact_id"] for r in rows] == expected_first_two + [ids[b"e1"]]
    assert repo.artifacts_of_kinds(conn, "exec-1", []) == []
    assert repo.artifacts_of_kinds(conn, "exec-1", ("generic_result",)) == []
    assert repo.artifacts_of_kinds(conn, "exec-none", ["diff"]) == []


def test_download_allowed_includes_bundle_inputs(seeded, store):
    conn = seeded
    repo.insert_kind(conn, SESSION, REVIEW, NOW)
    repo.insert_work_item_task(conn, _task(TASK_B, kind="code_change", predecessor=TASK_A), NOW)
    _create_execution(conn, "exec-fix", TASK_B, kind="code_change", inputs=["art-handoff-000"])
    stored = {}
    for kind, data in (("diff", b"--- a\n+++ b\n"), ("code_change_result", b'{"outcome": "ready_for_review"}'),
                       ("test_log_after", b"exit_code=0\n"), ("test_log_before", b"exit_code=1\n")):
        created, _ = repo.store_artifact(conn, store, execution_id="exec-fix", session_id=SESSION,
                                         meta=_meta(data, kind), data=data, now=NOW)
        stored[kind] = created
    manifest = json.dumps({
        "contract_version": 1,
        "source_execution_id": "exec-fix",
        "source_kind": "code_change",
        "source_result_artifact_id": stored["code_change_result"].artifact_id,
        "inputs": [
            {"kind": k, "artifact_id": stored[k].artifact_id, "sha256": stored[k].sha256,
             "content_type": "text/plain"}
            for k in ("diff", "test_log_after")
        ],
        "attachments": [],
    }).encode()
    bundle, _ = repo.store_artifact(conn, store, execution_id="exec-fix", session_id=SESSION,
                                    meta=_meta(manifest, "handoff_bundle", "manifest.json"),
                                    data=manifest, now=NOW)
    repo.insert_work_item_task(conn, _task("review-1", kind="review", predecessor=TASK_B), NOW)
    request = ExecutionRequest.model_validate({
        "contract_version": 1, "execution_id": "exec-review", "task_id": "review-1", "kind": "review",
        "agent_id": "agent-claude-mac", "task_revision": 1, "request": "검토해 주세요.",
        "input_artifact_ids": [bundle.artifact_id], "target": {"local_registration_id": "local-demo-report"},
        "kind_spec": REVIEW.model_dump(),
    })
    repo.create_execution(conn, execution_id="exec-review", task_id="review-1", attempt_no=1,
                          start_key="auto:review-1:r1", agent_id="agent-claude-mac", kind="review",
                          request=request, assigned_connector_id=CONNECTOR,
                          predecessor_execution_id="exec-fix", now=NOW)
    assert repo.download_allowed(conn, store, "exec-review", bundle.artifact_id)
    assert repo.download_allowed(conn, store, "exec-review", stored["diff"].artifact_id)  # inputs
    assert repo.download_allowed(conn, store, "exec-review", stored["test_log_after"].artifact_id)  # inputs
    assert repo.download_allowed(conn, store, "exec-review", stored["code_change_result"].artifact_id)  # source result
    assert not repo.download_allowed(conn, store, "exec-review", stored["test_log_before"].artifact_id)  # 무관


# --- phase 7: 입구 토큰·Chain callback (ADR-0010) --------------------------------


def _db_files_contain(db_path, needle: str) -> bool:
    """DB 본체와 WAL 어디에도 없어야 하는 문자열 검사."""
    return any(needle.encode() in f.read_bytes() for f in db_path.parent.glob(db_path.name + "*"))


def test_source_token_issue_authenticate_touch_revoke(seeded, db_path):
    conn = seeded
    token_id, token = repo.issue_source_token(conn, SESSION, "n8n", "n8n 운영", NOW)
    assert token.startswith("wfs_") and len(token) > 20
    assert token_id.startswith("src-") and len(token_id) == len("src-") + 8
    row = conn.execute("SELECT * FROM source_tokens WHERE token_id = ?", (token_id,)).fetchone()
    assert row["token_sha256"] != token
    assert row["token_sha256"] == hashlib.sha256(token.encode()).hexdigest()
    assert (row["session_id"], row["source"], row["label"], row["created_at"]) == (
        SESSION, "n8n", "n8n 운영", NOW)
    assert row["last_used_at"] is None and row["revoked_at"] is None
    dumped = " ".join(str(v) for v in tuple(row))
    assert "wfs_" not in dumped and token[4:] not in dumped
    assert not _db_files_contain(db_path, token[4:])

    auth = repo.authenticate_source_token(conn, token)
    assert (auth["token_id"], auth["session_id"], auth["source"]) == (token_id, SESSION, "n8n")

    repo.touch_source_token(conn, token_id, LATER)
    assert conn.execute(
        "SELECT last_used_at FROM source_tokens WHERE token_id = ?", (token_id,)
    ).fetchone()[0] == LATER

    repo.revoke_source_token(conn, SESSION, token_id, LATER)
    assert repo.authenticate_source_token(conn, token) is None
    repo.revoke_source_token(conn, SESSION, token_id, "2026-09-20T00:00:09Z")  # 멱등 — 처음 시각 유지
    assert conn.execute(
        "SELECT revoked_at FROM source_tokens WHERE token_id = ?", (token_id,)
    ).fetchone()[0] == LATER


def test_source_token_revoke_requires_same_session(seeded):
    conn = seeded
    token_id, token = repo.issue_source_token(conn, SESSION, "n8n", "", NOW)
    with pytest.raises(NotFound):
        repo.revoke_source_token(conn, OTHER_SESSION, token_id, NOW)
    with pytest.raises(NotFound):
        repo.revoke_source_token(conn, SESSION, "src-nope", NOW)
    assert repo.authenticate_source_token(conn, token) is not None  # 다른 세션의 취소는 영향 없음


def test_source_token_issue_requires_session_and_valid_source(conn):
    with pytest.raises(NotFound):
        repo.issue_source_token(conn, "no-such-session", "n8n", "", NOW)
    repo.create_session(conn, SESSION, NOW)
    with pytest.raises(sqlite3.IntegrityError):
        repo.issue_source_token(conn, SESSION, "slack", "", NOW)
    assert conn.execute("SELECT COUNT(*) FROM source_tokens").fetchone()[0] == 0


def test_authenticate_source_token_unknown_is_none(seeded):
    conn = seeded
    assert repo.authenticate_source_token(conn, "wfs_bogus") is None
    assert repo.authenticate_source_token(conn, "") is None
    with pytest.raises(NotFound):
        repo.touch_source_token(conn, "src-nope", NOW)


def test_list_source_tokens_in_issue_order_including_revoked(seeded):
    conn = seeded
    id_b, _ = repo.issue_source_token(conn, SESSION, "n8n", "b", LATER)
    id_a, _ = repo.issue_source_token(conn, SESSION, "n8n", "a", NOW)
    id_c, _ = repo.issue_source_token(conn, SESSION, "n8n", "c", LATER)  # 같은 시각은 발급 순
    repo.issue_source_token(conn, OTHER_SESSION, "n8n", "other", NOW)
    repo.revoke_source_token(conn, SESSION, id_b, LATER)
    rows = repo.list_source_tokens(conn, SESSION)
    assert [r["token_id"] for r in rows] == [id_a, id_b, id_c]
    assert [r["label"] for r in rows] == ["a", "b", "c"]
    assert [r["revoked_at"] for r in rows] == [None, LATER, None]
    assert [r["label"] for r in repo.list_source_tokens(conn, OTHER_SESSION)] == ["other"]
    assert "token_sha256" in rows[0].keys()  # 원문 컬럼은 없다


def test_chain_insert_stores_callback_url_and_items(seeded):
    conn = seeded
    items = [{"key": "n8n-1", "title": "일일 보고서 실패", "body": "", "labels": ["incident"],
              "blocked_by": []}]
    repo.insert_chain(conn, _chain("chain-n8n", source="n8n", callback_url="http://localhost:5678/x",
                                   items=items), NOW)
    row = repo.get_chain(conn, "chain-n8n")
    assert row["source"] == "n8n" and row["callback_url"] == "http://localhost:5678/x"
    assert json.loads(row["items_json"]) == items
    assert row["callback_attempts"] == 0
    assert row["callback_sent_at"] is None and row["callback_next_at"] is None
    assert row["callback_last_error"] is None
    repo.insert_chain(conn, _chain("chain-github"), NOW)  # 기존 호출 그대로
    row = repo.get_chain(conn, "chain-github")
    assert row["callback_url"] is None and row["items_json"] is None
    repo.insert_chain(conn, _chain("chain-none", source="n8n", callback_url=None, items=None), NOW)
    row = repo.get_chain(conn, "chain-none")
    assert row["callback_url"] is None and row["items_json"] is None
    assert [r["chain_id"] for r in repo.list_chains(conn, SESSION)] == [
        "chain-github", "chain-n8n", "chain-none"]


def test_record_callback_attempt_ok_and_failure(seeded):
    conn = seeded
    repo.insert_chain(conn, _chain("c1", source="n8n", callback_url="http://localhost:5678/x"), NOW)
    repo.record_callback_attempt(conn, "c1", ok=False, error="connect timeout", now=NOW,
                                 next_at="2026-09-20T00:00:30Z")
    row = repo.get_chain(conn, "c1")
    assert row["callback_attempts"] == 1 and row["callback_sent_at"] is None
    assert row["callback_last_error"] == "connect timeout"
    assert row["callback_next_at"] == "2026-09-20T00:00:30Z"
    repo.record_callback_attempt(conn, "c1", ok=False, error="HTTP 503", now=LATER,
                                 next_at="2026-09-20T00:01:30Z")
    row = repo.get_chain(conn, "c1")
    assert row["callback_attempts"] == 2 and row["callback_last_error"] == "HTTP 503"
    assert row["callback_next_at"] == "2026-09-20T00:01:30Z"
    repo.record_callback_attempt(conn, "c1", ok=True, error=None, now=LATER, next_at=None)
    row = repo.get_chain(conn, "c1")
    assert row["callback_sent_at"] == LATER and row["callback_last_error"] is None
    assert row["callback_attempts"] == 2  # 성공은 횟수를 늘리지 않는다
    with pytest.raises(NotFound):
        repo.record_callback_attempt(conn, "nope", ok=True, error=None, now=NOW, next_at=None)


def test_chains_awaiting_callback_filters_and_orders(seeded):
    conn = seeded
    url = "http://localhost:5678/x"
    repo.insert_chain(conn, _chain("c-fresh", source="n8n", callback_url=url), LATER)
    repo.insert_chain(conn, _chain("c-past", source="n8n", callback_url=url), NOW)
    repo.insert_chain(conn, _chain("c-future", source="n8n", callback_url=url), NOW)
    repo.insert_chain(conn, _chain("c-sent", source="n8n", callback_url=url), NOW)
    repo.insert_chain(conn, _chain("c-max", source="n8n", callback_url=url), NOW)
    repo.insert_chain(conn, _chain("c-no-url"), NOW)
    repo.insert_chain(conn, _chain("c-other", session_id=OTHER_SESSION, source="n8n",
                                   callback_url=url), NOW)  # 세션 무관 — 워커는 전체를 본다
    repo.record_callback_attempt(conn, "c-past", ok=False, error="e", now=NOW,
                                 next_at="2026-09-20T00:00:30Z")
    repo.record_callback_attempt(conn, "c-future", ok=False, error="e", now=NOW,
                                 next_at="2026-09-20T00:10:00Z")
    repo.record_callback_attempt(conn, "c-sent", ok=True, error=None, now=NOW, next_at=None)
    for _ in range(5):
        repo.record_callback_attempt(conn, "c-max", ok=False, error="e", now=NOW, next_at=NOW)

    due = "2026-09-20T00:01:00Z"
    assert [r["chain_id"] for r in repo.chains_awaiting_callback(conn, due, max_attempts=5)] == [
        "c-other", "c-past", "c-fresh"]
    assert [r["chain_id"] for r in repo.chains_awaiting_callback(conn, NOW, max_attempts=5)] == [
        "c-other", "c-fresh"]  # next_at 이 미래면 제외
    assert [r["chain_id"] for r in repo.chains_awaiting_callback(conn, due, max_attempts=6)] == [
        "c-max", "c-other", "c-past", "c-fresh"]  # 상한을 올리면 다시 대상
    assert repo.chains_awaiting_callback(conn, due, max_attempts=0) == []


# --- phase 8: GitHub 업무 순환 저장 (ADR-0014, ARCHITECTURE "GitHub 업무 순환" 중복 키) ------------

SOURCE = "ghs-1a2b3c4d"
FIX_AGENT = "agent-codex-mac"


def _source(**overrides) -> GitHubSourceConfig:
    data = {
        "source_id": SOURCE,
        "repository_full_name": "acme/billing",
        "workflow_repository_id": "billing",
        "label_filter": ["bug"],
        "selected_issue_numbers": [],
        "start_at": "2026-10-06T00:00:00Z",
        "fix_verification_profile_id": "vp-pytest",
        "review_agent_id": "agent-claude-mac",
        "run_mode": "auto",
        "max_rework_rounds": 1,
        "enabled": True,
        "config_revision": 1,
    }
    return GitHubSourceConfig.model_validate({**data, **overrides})


def _snapshot(**overrides) -> GitHubIssueSnapshot:
    data = {
        "repository_id": 700112233,
        "repository_full_name": "acme/billing",
        "issue_id": 2456789012,
        "number": 41,
        "title": "할인 쿠폰이 두 번 적용됨",
        "body": "재현: 같은 쿠폰으로 두 번 결제",
        "state": "open",
        "labels": ["bug"],
        "assignee_ids": [5812345],
        "assignee_logins": ["kim-dev"],
        "html_url": "https://github.com/acme/billing/issues/41",
        "created_at": "2026-10-06T10:12:00Z",
        "updated_at": "2026-10-06T10:15:30Z",
        "is_pull_request": False,
    }
    return GitHubIssueSnapshot.model_validate({**data, **overrides})


def _fix_task(task_id: str = "task-gh-41", session_id: str = SESSION, **overrides) -> dict:
    task = {
        **_task(task_id, session_id, kind="bug_fix"),
        "title": "할인 쿠폰이 두 번 적용됨",
        "request": "GitHub acme/billing#41",
        "required_capability": {"code": "code.fix", "scope": {"repository_id": "billing"}},
        "target": {"local_registration_id": "local-billing", "base_commit": "a" * 40,
                   "verification_profile_id": "vp-pytest"},
        "source_ref": "#41",
    }
    return {**task, **overrides}


@pytest.fixture
def cycle(seeded):
    """SESSION 이 acme/billing 을 연결하고 이슈 #41 이 Task 로 들어온 상태."""
    repo.upsert_agent(seeded, _agent(FIX_AGENT, connection_type="local", api_url=None, credential_ref=None,
                                     capabilities=[{"code": "code.fix", "scope": {"repository_id": "billing"}}]))
    repo.save_github_source(seeded, SESSION, _source(), NOW)
    result = repo.upsert_source_issue(seeded, SESSION, SOURCE, _snapshot(), task=_fix_task(), now=NOW)
    assert result.action == "created"
    return seeded


def test_hand_work_to_agent_writes_the_delegation_in_one_transaction(cycle):
    conn = cycle
    repo.register_session_agent(conn, SESSION, FIX_AGENT, NOW)
    work_item_id = repo.work_item_of_task(conn, "task-gh-41")["work_item_id"]
    admin = repo.ensure_first_admin(conn, SESSION, now=NOW)
    capability = Capability(code="code.fix", scope={"repository_id": "billing"})
    record = select_agent("task-gh-41", capability, [Candidate(FIX_AGENT, (capability,))], mode="manual",
                          chosen_agent_id=FIX_AGENT)
    target = {"local_registration_id": "local-billing"}
    with pytest.raises(NotFound):  # 다른 워크스페이스
        repo.hand_work_to_agent(conn, OTHER_SESSION, work_item_id, record=record, target=target, member_id=admin,
                                now=LATER)
    assert repo.get_selection(conn, "task-gh-41") is None

    repo.hand_work_to_agent(conn, SESSION, work_item_id, record=record, target=target, member_id=admin, now=LATER)

    assert repo.get_selection(conn, "task-gh-41") == record
    task = repo.get_task(conn, "task-gh-41")
    assert (task["selection_mode"], task["chosen_agent_id"], json.loads(task["target_json"])) == (
        "manual", FIX_AGENT, target)
    work = repo.get_work_item(conn, SESSION, work_item_id)
    assert (work["assignee_type"], work["assignee_id"], work["requested_by_member_id"]) == ("agent", FIX_AGENT, admin)
    issue = repo.get_source_issue_by_task(conn, SESSION, "task-gh-41")
    assert (issue["delegated_by"], issue["delegated_at"]) == ("operator", LATER)
    assert WorkStatus(work["status"], work["status_reason"]) == work_status(repo.work_item_facts(conn, work_item_id))

    repo.hand_work_to_agent(conn, SESSION, work_item_id, record=record, target=target, member_id=admin,
                            now="2026-09-20T00:00:09Z")  # 두 번째 — 지시·담당 이벤트는 처음 것 그대로
    assert repo.get_source_issue_by_task(conn, SESSION, "task-gh-41")["delegated_at"] == LATER
    assert len([e for e in repo.list_work_item_events(conn, work_item_id) if e["type"] == "assigned"]) == 1


def test_github_source_save_get_and_session_scope(seeded):
    repo.save_github_source(seeded, SESSION, _source(), NOW)
    assert repo.get_github_source(seeded, SESSION, SOURCE) == _source()
    assert repo.get_github_source(seeded, OTHER_SESSION, SOURCE) is None  # 다른 세션에는 없다
    repo.save_github_source(seeded, SESSION, _source(label_filter=["bug", "p1"], config_revision=2), LATER)
    assert repo.get_github_source(seeded, SESSION, SOURCE).label_filter == ["bug", "p1"]
    with pytest.raises(NotFound):  # 다른 세션이 같은 source_id 를 덮어쓰지 못한다
        repo.save_github_source(seeded, OTHER_SESSION, _source(), LATER)
    with pytest.raises(sqlite3.IntegrityError):  # 세션당 저장소 하나
        repo.save_github_source(seeded, SESSION, _source(source_id="ghs-00000002"), LATER)
    repo.save_github_source(seeded, OTHER_SESSION, _source(source_id="ghs-00000003"), LATER)  # 세션이 다르면 됨


def test_source_cursor_is_saved_per_source_and_scoped(seeded):
    repo.save_github_source(seeded, SESSION, _source(), NOW)
    assert repo.get_source_cursor(seeded, SESSION, SOURCE) is None
    repo.save_source_cursor(seeded, SESSION, SOURCE, "2026-10-06T10:15:30Z", LATER)
    assert repo.get_source_cursor(seeded, SESSION, SOURCE) == "2026-10-06T10:15:30Z"
    repo.save_github_source(seeded, SESSION, _source(config_revision=2), LATER)  # 설정 변경은 커서를 지우지 않는다
    assert repo.get_source_cursor(seeded, SESSION, SOURCE) == "2026-10-06T10:15:30Z"
    with pytest.raises(NotFound):
        repo.save_source_cursor(seeded, OTHER_SESSION, SOURCE, "x", LATER)
    with pytest.raises(NotFound):
        repo.get_source_cursor(seeded, OTHER_SESSION, SOURCE)


def test_source_synced_at_is_the_last_cursor_save(seeded):
    """phase 11 step 8 — 연결 화면의 "마지막 동기화". 커서를 저장한(수집이 끝난) 시각이고, 설정 변경은 바꾸지 않는다."""
    repo.save_github_source(seeded, SESSION, _source(), NOW)
    assert repo.get_source_synced_at(seeded, SESSION, SOURCE) is None
    repo.save_source_cursor(seeded, SESSION, SOURCE, "c", LATER)
    repo.save_github_source(seeded, SESSION, _source(config_revision=2), NOW)
    assert repo.get_source_synced_at(seeded, SESSION, SOURCE) == LATER
    with pytest.raises(NotFound):
        repo.get_source_synced_at(seeded, OTHER_SESSION, SOURCE)


def _link(issue: int, pr: int, merged_at: str = "2026-08-02T00:00:00Z") -> IssuePrLink:
    return IssuePrLink(issue_number=issue, issue_title=f"이슈 {issue}", issue_opened_at="2026-08-01T00:00:00Z",
                       pr_number=pr, pr_merged_at=merged_at)


def test_replace_baseline_replaces_items_and_records_import_idempotently(seeded):
    """step 7 — 소스 단위 전체 교체. 같은 결과로 다시 가져와도 행이 늘지 않고, 설정 번호는 그대로다."""
    repo.save_github_source(seeded, SESSION, _source(), NOW)
    revision = repo.get_config_revision(seeded, SESSION)
    assert repo.list_baseline(seeded, SESSION, SOURCE) == (None, [])

    links = [_link(3, 30), _link(1, 10)]
    assert repo.replace_baseline(seeded, SESSION, SOURCE, links, opened_before=NOW, now=NOW) == 2
    assert repo.replace_baseline(seeded, SESSION, SOURCE, links, opened_before=NOW, now=LATER) == 2

    record, items = repo.list_baseline(seeded, SESSION, SOURCE)
    assert (record["opened_before"], record["fetched_at"], record["item_count"]) == (NOW, LATER, 2)
    assert [(r["issue_number"], r["issue_title"], r["issue_opened_at"], r["pr_number"], r["pr_merged_at"],
             r["fetched_at"]) for r in items] == [
        (1, "이슈 1", "2026-08-01T00:00:00Z", 10, "2026-08-02T00:00:00Z", LATER),
        (3, "이슈 3", "2026-08-01T00:00:00Z", 30, "2026-08-02T00:00:00Z", LATER),
    ]

    assert repo.replace_baseline(seeded, SESSION, SOURCE, [_link(5, 50)], opened_before=NOW, now=LATER) == 1
    record, items = repo.list_baseline(seeded, SESSION, SOURCE)
    assert record["item_count"] == 1 and [r["issue_number"] for r in items] == [5]
    assert repo.replace_baseline(seeded, SESSION, SOURCE, [], opened_before=NOW, now=LATER) == 0
    assert repo.list_baseline(seeded, SESSION, SOURCE)[1] == []
    assert repo.get_config_revision(seeded, SESSION) == revision


def test_baseline_is_scoped_to_the_source_owner_session(seeded):
    repo.save_github_source(seeded, SESSION, _source(), NOW)
    repo.replace_baseline(seeded, SESSION, SOURCE, [_link(1, 10)], opened_before=NOW, now=NOW)

    with pytest.raises(NotFound):
        repo.replace_baseline(seeded, OTHER_SESSION, SOURCE, [], opened_before=NOW, now=LATER)
    with pytest.raises(NotFound):
        repo.list_baseline(seeded, OTHER_SESSION, SOURCE)
    with pytest.raises(NotFound):
        repo.list_baseline(seeded, SESSION, "ghs-00000009")
    assert [r["issue_number"] for r in repo.list_baseline(seeded, SESSION, SOURCE)[1]] == [1]


def test_replace_baseline_failure_keeps_previous_items(seeded):
    """한 트랜잭션 — 중간 INSERT 가 실패하면 이전 기준선이 그대로 남는다."""
    repo.save_github_source(seeded, SESSION, _source(), NOW)
    repo.replace_baseline(seeded, SESSION, SOURCE, [_link(1, 10)], opened_before=NOW, now=NOW)

    with pytest.raises(sqlite3.IntegrityError):
        repo.replace_baseline(seeded, SESSION, SOURCE, [_link(2, 20), _link(2, 20)], opened_before=NOW, now=LATER)

    record, items = repo.list_baseline(seeded, SESSION, SOURCE)
    assert record["fetched_at"] == NOW and [r["issue_number"] for r in items] == [1]


def test_list_github_sources_and_owner_sessions(seeded):
    """step 6 — 운영자 API 의 목록과 'GitHub 연결은 한 워크스페이스' 검사 재료."""
    assert repo.list_github_sources(seeded, SESSION) == []
    assert repo.github_source_sessions(seeded) == []
    repo.save_github_source(seeded, SESSION, _source(), NOW)
    repo.save_github_source(seeded, SESSION, _source(source_id="ghs-00000002", repository_full_name="acme/lib"), NOW)
    assert [s.source_id for s in repo.list_github_sources(seeded, SESSION)] == [SOURCE, "ghs-00000002"]
    assert repo.list_github_sources(seeded, OTHER_SESSION) == []
    assert repo.github_source_sessions(seeded) == [SESSION]


def test_save_github_source_expected_revision_is_checked_in_the_transaction(seeded):
    repo.save_github_source(seeded, SESSION, _source(), NOW)
    repo.save_github_source(seeded, SESSION, _source(config_revision=2), LATER, expected_revision=1)
    with pytest.raises(StaleConfig) as exc:
        repo.save_github_source(seeded, SESSION, _source(config_revision=2), LATER, expected_revision=1)
    assert exc.value.current_revision == 2
    assert repo.get_github_source(seeded, SESSION, SOURCE).config_revision == 2
    with pytest.raises(NotFound):  # 잠금 갱신은 기존 소스만
        repo.save_github_source(seeded, SESSION, _source(source_id="ghs-00000009"), LATER, expected_revision=1)


def test_bind_assignee_upserts_by_github_user_id(cycle):
    binding = AssigneeBinding(source_id=SOURCE, github_user_id=5812345, github_login="kim-dev", agent_id=FIX_AGENT)
    repo.bind_assignee(cycle, SESSION, binding, NOW)
    renamed = binding.model_copy(update={"github_login": "kim-renamed"})
    repo.bind_assignee(cycle, SESSION, renamed, LATER)  # login 은 표시용 — 같은 ID 는 한 행
    assert repo.list_assignee_bindings(cycle, SESSION, SOURCE) == [renamed]
    with pytest.raises(NotFound):
        repo.bind_assignee(cycle, OTHER_SESSION, binding, LATER)
    with pytest.raises(NotFound):
        repo.list_assignee_bindings(cycle, OTHER_SESSION, SOURCE)
    with pytest.raises(NotFound):  # 없는 Agent
        repo.bind_assignee(cycle, SESSION, binding.model_copy(update={"agent_id": "nope"}), LATER)


def test_upsert_source_issue_creates_one_task_per_remote_issue(cycle):
    row = repo.get_source_issue_by_task(cycle, SESSION, "task-gh-41")
    assert (row["source_id"], row["github_issue_id"], row["issue_number"], row["source_revision"]) == (
        SOURCE, 2456789012, 41, 1,
    )
    assert row["snapshot_digest"] == snapshot_digest(_snapshot())
    assert GitHubIssueSnapshot.model_validate_json(row["snapshot_json"]) == _snapshot()
    task = repo.get_task(cycle, "task-gh-41")
    assert task["kind"] == "bug_fix" and task["source_ref"] == "#41"

    again = repo.upsert_source_issue(cycle, SESSION, SOURCE, _snapshot(), task=_fix_task("task-other"), now=LATER)
    assert (again.action, again.task_id, again.source_revision) == ("unchanged", "task-gh-41", 1)
    assert repo.get_task(cycle, "task-other") is None  # 같은 이슈를 다시 받아도 Task 는 하나
    assert repo.get_source_issue_by_task(cycle, OTHER_SESSION, "task-gh-41") is None


def test_upsert_source_issue_revisions_and_out_of_order_snapshots(cycle):
    edited = _snapshot(body="재현 절차 보강", updated_at="2026-10-06T10:20:00Z")
    result = repo.upsert_source_issue(cycle, SESSION, SOURCE, edited, task=_fix_task(), now=LATER)
    assert (result.action, result.source_revision) == ("updated", 2)

    older = _snapshot(title="옛 제목", updated_at="2026-10-06T10:16:00Z")  # 늦게 도착한 이전 스냅샷
    result = repo.upsert_source_issue(cycle, SESSION, SOURCE, older, task=_fix_task(), now=LATER)
    assert (result.action, result.source_revision) == ("stale", 2)

    same_time = _snapshot(body="재현 절차 보강", labels=["bug", "p1"], updated_at="2026-10-06T19:20:00+09:00")
    result = repo.upsert_source_issue(cycle, SESSION, SOURCE, same_time, task=_fix_task(), now=LATER)
    assert (result.action, result.source_revision) == ("updated", 3)  # 같은 시각·다른 digest 는 새 revision

    result = repo.upsert_source_issue(cycle, SESSION, SOURCE, same_time, task=_fix_task(), now=LATER)
    assert result.action == "unchanged"
    row = repo.get_source_issue_by_task(cycle, SESSION, "task-gh-41")
    assert row["source_revision"] == 3 and row["snapshot_digest"] == snapshot_digest(same_time)

    closed = _snapshot(body="재현 절차 보강", labels=["bug", "p1"], state="closed",
                       updated_at="2026-10-06T11:00:00Z")
    repo.upsert_source_issue(cycle, SESSION, SOURCE, closed, task=_fix_task(), now=LATER)
    assert repo.get_source_issue_by_task(cycle, SESSION, "task-gh-41")["state"] == "closed"


def test_upsert_source_issue_rejects_other_session_and_wrong_repository(cycle):
    with pytest.raises(NotFound):
        repo.upsert_source_issue(cycle, OTHER_SESSION, SOURCE, _snapshot(issue_id=9, number=9),
                                 task=_fix_task("t-9", OTHER_SESSION), now=LATER)
    with pytest.raises(ValueError):  # Task 의 세션이 소스의 세션과 다르다
        repo.upsert_source_issue(cycle, SESSION, SOURCE, _snapshot(issue_id=9, number=9),
                                 task=_fix_task("t-9", OTHER_SESSION), now=LATER)
    with pytest.raises(ValueError):  # 소스에 연결된 저장소가 아니다
        repo.upsert_source_issue(cycle, SESSION, SOURCE,
                                 _snapshot(issue_id=9, number=9, repository_full_name="acme/other"),
                                 task=_fix_task("t-9"), now=LATER)
    assert repo.get_task(cycle, "t-9") is None


def test_upsert_source_issue_raises_task_revision_only_when_input_changes(cycle):
    """제목·요청이 바뀌면 Task revision+1(다음 실행 입력). 담당·라벨·시각만 바뀌면 원본 revision 만 오른다."""
    assigned = _snapshot(assignee_ids=[1, 2], assignee_logins=["a", "b"], updated_at="2026-10-06T10:20:00Z")
    result = repo.upsert_source_issue(cycle, SESSION, SOURCE, assigned, task=_fix_task(), now=LATER)
    assert (result.action, result.source_revision, result.input_changed) == ("updated", 2, False)
    assert repo.get_task(cycle, "task-gh-41")["revision"] == 1

    edited = _snapshot(body="재현 절차 보강", updated_at="2026-10-06T10:30:00Z")
    result = repo.upsert_source_issue(cycle, SESSION, SOURCE, edited,
                                      task=_fix_task(title="새 제목", request="재현 절차 보강"), now=LATER)
    assert (result.action, result.source_revision, result.input_changed) == ("updated", 3, True)
    task = repo.get_task(cycle, "task-gh-41")
    assert (task["title"], task["request"], task["revision"]) == ("새 제목", "재현 절차 보강", 2)

    stale = _snapshot(updated_at="2026-10-06T10:00:00Z")
    result = repo.upsert_source_issue(cycle, SESSION, SOURCE, stale, task=_fix_task(title="옛 제목"), now=LATER)
    assert (result.action, result.input_changed) == ("stale", False)
    assert repo.get_task(cycle, "task-gh-41")["title"] == "새 제목"


def test_upsert_source_issue_keeps_finished_task_input(cycle):
    repo.update_task_status(cycle, "task-gh-41", "실패", "운영자 종료", finished_at=LATER, now=LATER)
    edited = _snapshot(body="다시 편집", updated_at="2026-10-06T10:30:00Z")
    result = repo.upsert_source_issue(cycle, SESSION, SOURCE, edited,
                                      task=_fix_task(request="다시 편집"), now=LATER)
    assert (result.action, result.input_changed) == ("updated", False)
    task = repo.get_task(cycle, "task-gh-41")
    assert (task["request"], task["revision"], task["status"]) == ("GitHub acme/billing#41", 1, "실패")


def test_source_issue_lookup_and_source_owner(cycle):
    assert repo.github_source_session(cycle, SOURCE) == SESSION
    assert repo.github_source_session(cycle, "ghs-99999999") is None
    rows = repo.list_source_issues(cycle, SESSION, SOURCE)
    assert [(r["issue_number"], r["task_id"]) for r in rows] == [(41, "task-gh-41")]
    with pytest.raises(NotFound):
        repo.list_source_issues(cycle, OTHER_SESSION, SOURCE)



# --- phase 11 step 5: all_open 소스의 실행 지시 ---------------------------------------------------------


def _delegation(conn):
    return tuple(conn.execute(
        "SELECT delegated_at, delegated_by FROM source_issues WHERE github_issue_id = 2456789012").fetchone())


def test_mark_issue_delegated_records_first_instruction_only(cycle):
    assert _delegation(cycle) == (None, None)  # 지시 전
    record = dict(session_id=SESSION, source_id=SOURCE, github_issue_id=2456789012)
    assert repo.mark_issue_delegated(cycle, **record, by="label", now=NOW) is True
    assert _delegation(cycle) == (NOW, "label")
    assert repo.mark_issue_delegated(cycle, **record, by="operator", now=LATER) is False  # 멱등 — 처음 지시가 남는다
    assert _delegation(cycle) == (NOW, "label")
    # 재동기화(같은 스냅샷·새 revision)도 지시를 지우지 않는다
    repo.upsert_source_issue(cycle, SESSION, SOURCE, _snapshot(labels=[], updated_at="2026-10-06T11:00:00Z"),
                             task=_fix_task(), now=LATER)
    assert _delegation(cycle) == (NOW, "label")


def test_mark_issue_delegated_rejects_other_session_unknown_issue_and_bad_actor(cycle):
    with pytest.raises(NotFound):
        repo.mark_issue_delegated(cycle, session_id=OTHER_SESSION, source_id=SOURCE, github_issue_id=2456789012,
                                  by="label", now=NOW)
    with pytest.raises(NotFound):
        repo.mark_issue_delegated(cycle, session_id=SESSION, source_id=SOURCE, github_issue_id=1, by="label", now=NOW)
    with pytest.raises(ValueError):
        repo.mark_issue_delegated(cycle, session_id=SESSION, source_id=SOURCE, github_issue_id=2456789012,
                                  by="agent", now=NOW)
    assert _delegation(cycle) == (None, None)


# --- phase 9 step 11: 이슈를 닫은 병합 PR 의 병합 시각 --------------------------------------------------


def _merge_row(conn, issue_id: int = 2456789012):
    return tuple(conn.execute(
        "SELECT merged_pr_number, pr_merged_at, merge_checked_at FROM source_issues WHERE github_issue_id = ?",
        (issue_id,),
    ).fetchone())


def _merge_link(pr: int = 77, merged_at: str = "2026-10-07T09:00:00Z", issue: int = 41) -> IssuePrLink:
    return IssuePrLink(issue_number=issue, issue_title="할인 쿠폰이 두 번 적용됨",
                       issue_opened_at="2026-10-06T10:12:00Z", pr_number=pr, pr_merged_at=merged_at)


def test_record_issue_merge_stores_link_and_check_time_idempotently(cycle):
    assert _merge_row(cycle) == (None, None, None)  # 아직 모름

    repo.record_issue_merge(cycle, session_id=SESSION, source_id=SOURCE, github_issue_id=2456789012,
                            link=None, now=NOW)
    assert _merge_row(cycle) == (None, None, NOW)  # 조회했지만 병합 없음 — 시각을 지어내지 않는다

    repo.record_issue_merge(cycle, session_id=SESSION, source_id=SOURCE, github_issue_id=2456789012,
                            link=_merge_link(), now=LATER)
    assert _merge_row(cycle) == (77, "2026-10-07T09:00:00Z", LATER)

    repo.record_issue_merge(cycle, session_id=SESSION, source_id=SOURCE, github_issue_id=2456789012,
                            link=_merge_link(), now=LATER)  # 같은 값 재기록은 멱등
    assert _merge_row(cycle) == (77, "2026-10-07T09:00:00Z", LATER)


def test_record_issue_merge_never_overwrites_a_recorded_merge(cycle):
    record = dict(session_id=SESSION, source_id=SOURCE, github_issue_id=2456789012)
    repo.record_issue_merge(cycle, **record, link=_merge_link(), now=NOW)

    repo.record_issue_merge(cycle, **record, link=_merge_link(pr=78, merged_at="2026-10-08T00:00:00Z"), now=LATER)
    assert _merge_row(cycle)[:2] == (77, "2026-10-07T09:00:00Z")
    repo.record_issue_merge(cycle, **record, link=None, now=LATER)  # 나중 조회가 비어도 지우지 않는다
    assert _merge_row(cycle)[:2] == (77, "2026-10-07T09:00:00Z")


def test_record_issue_merge_rejects_other_session_unknown_issue_and_wrong_number(cycle):
    with pytest.raises(NotFound):
        repo.record_issue_merge(cycle, session_id=OTHER_SESSION, source_id=SOURCE, github_issue_id=2456789012,
                                link=_merge_link(), now=LATER)
    with pytest.raises(NotFound):
        repo.record_issue_merge(cycle, session_id=SESSION, source_id=SOURCE, github_issue_id=1,
                                link=_merge_link(), now=LATER)
    with pytest.raises(ValueError):  # 다른 이슈의 링크
        repo.record_issue_merge(cycle, session_id=SESSION, source_id=SOURCE, github_issue_id=2456789012,
                                link=_merge_link(issue=42), now=LATER)
    assert _merge_row(cycle) == (None, None, None)


def test_list_issues_needing_merge_check_is_closed_without_merge(cycle):
    closed = _snapshot(state="closed", updated_at="2026-10-06T11:00:00Z")
    repo.upsert_source_issue(cycle, SESSION, SOURCE, closed, task=_fix_task(), now=LATER)
    other = _snapshot(issue_id=9, number=9, state="closed")
    repo.upsert_source_issue(cycle, SESSION, SOURCE, other, task=_fix_task("task-gh-9"), now=LATER)
    repo.upsert_source_issue(cycle, SESSION, SOURCE, _snapshot(issue_id=10, number=10),
                             task=_fix_task("task-gh-10"), now=LATER)  # 열린 이슈 — 대상 아님

    rows = repo.list_issues_needing_merge_check(cycle, SESSION, SOURCE)
    assert [r["issue_number"] for r in rows] == [9, 41]

    repo.record_issue_merge(cycle, session_id=SESSION, source_id=SOURCE, github_issue_id=9, link=None, now=LATER)
    assert [r["issue_number"] for r in repo.list_issues_needing_merge_check(cycle, SESSION, SOURCE)] == [9, 41]
    repo.record_issue_merge(cycle, session_id=SESSION, source_id=SOURCE, github_issue_id=2456789012,
                            link=_merge_link(), now=LATER)
    assert [r["issue_number"] for r in repo.list_issues_needing_merge_check(cycle, SESSION, SOURCE)] == [9]
    with pytest.raises(NotFound):
        repo.list_issues_needing_merge_check(cycle, OTHER_SESSION, SOURCE)

def _wi41(conn) -> str:
    """이슈 #41 업무 — 후속 검토 단계가 들어갈 곳(same_work)."""
    return repo.work_item_of_task(conn, "task-gh-41")["work_item_id"]


def _review_spec(cause: str = "exec-fix-1", session_id: str = SESSION) -> FollowupTaskSpec:
    return FollowupTaskSpec(session_id=session_id, kind="code_review", cause_execution_id=cause,
                            predecessor_task_id="task-gh-41", rules_revision=1)


def _review_task(task_id: str = "task-gh-41-review") -> dict:
    return {
        **_fix_task(task_id), "kind": "code_review", "predecessor_task_id": "task-gh-41", "source_ref": None,
        "required_capability": {"code": "code.review", "scope": {"repository_id": "billing"}},
    }


def test_create_followup_once_is_unique_per_cause(cycle):
    _create_execution(cycle, "exec-fix-1", "task-gh-41", kind="code_change", inputs=("art-x",))
    task_id, created = repo.create_followup_once(cycle, _review_spec(), _review_task(), NOW, work_item_id=_wi41(cycle))
    assert (task_id, created) == ("task-gh-41-review", True)
    assert repo.get_task(cycle, task_id)["predecessor_task_id"] == "task-gh-41"
    # 같은 원인 재처리(재시작·중복 결과)는 기존 Task 를 돌려주고 새로 만들지 않는다
    task_id, created = repo.create_followup_once(cycle, _review_spec(), _review_task("task-dup"), LATER, work_item_id=_wi41(cycle))
    assert (task_id, created) == ("task-gh-41-review", False)
    assert repo.get_task(cycle, "task-dup") is None
    # 화면의 생성 근거 — 이 Task 를 만든 원인 실행
    link = repo.get_followup_link(cycle, "task-gh-41-review")
    assert (link["cause_execution_id"], link["to_kind"]) == ("exec-fix-1", "code_review")
    assert repo.get_followup_link(cycle, "task-gh-41") is None


def test_create_followup_once_checks_spec_and_ownership(cycle):
    _create_execution(cycle, "exec-fix-1", "task-gh-41", kind="code_change", inputs=("art-x",))
    with pytest.raises(ValueError):  # spec 과 Task 의 종류가 다르다
        repo.create_followup_once(cycle, _review_spec(), {**_review_task(), "kind": "bug_fix"}, NOW, work_item_id=_wi41(cycle))
    with pytest.raises(ValueError):  # spec 과 Task 의 선행이 다르다
        repo.create_followup_once(cycle, _review_spec(), {**_review_task(), "predecessor_task_id": TASK_A}, NOW, work_item_id=_wi41(cycle))
    with pytest.raises(NotFound):  # 원인 실행이 이 세션 것이 아니다
        repo.create_followup_once(cycle, _review_spec(session_id=OTHER_SESSION),
                                  {**_review_task(), "session_id": OTHER_SESSION}, NOW, work_item_id=_wi41(cycle))
    with pytest.raises(NotFound):  # 없는 원인 실행
        repo.create_followup_once(cycle, _review_spec("exec-none"), _review_task(), NOW, work_item_id=_wi41(cycle))
    assert repo.get_task(cycle, "task-gh-41-review") is None


def test_human_request_once_per_cause_key(cycle):
    request_id, created = repo.create_human_request_once(
        cycle, "task-gh-41", "fix_needs_information", "재현 절차가 필요합니다", "fix_needs_information:exec-1", NOW,
    )
    assert created and request_id.startswith("hr-")
    again, created = repo.create_human_request_once(
        cycle, "task-gh-41", "fix_needs_information", "다른 문구", "fix_needs_information:exec-1", LATER,
    )
    assert (again, created) == (request_id, False)
    row = repo.get_human_request(cycle, SESSION, request_id)
    assert (row["state"], row["revision"], row["task_revision"], row["question"]) == (
        "open", 1, 1, "재현 절차가 필요합니다",
    )
    assert repo.get_human_request(cycle, OTHER_SESSION, request_id) is None
    with pytest.raises(NotFound):
        repo.create_human_request_once(cycle, "no-task", "x", "q", "k", NOW)


def test_human_response_once_is_idempotent_and_locked_by_revision(cycle):
    request_id, _ = repo.create_human_request_once(
        cycle, "task-gh-41", "fix_needs_information", "재현 절차?", "fix_needs_information:exec-1", NOW,
    )
    kwargs = {"response_id": "resp-1", "expected_revision": 1, "action": "answer", "text": "두 번 클릭", "now": LATER}
    task_revision, created = repo.record_human_response_once(cycle, SESSION, request_id, **kwargs)
    assert (task_revision, created) == (2, True)  # 응답이 다음 실행 입력(새 task_revision)이 된다
    assert repo.get_task(cycle, "task-gh-41")["revision"] == 2
    row = repo.get_human_request(cycle, SESSION, request_id)
    assert (row["state"], row["revision"], row["answered_at"]) == ("answered", 2, LATER)

    # 같은 response_id 재전송(응답 유실 뒤 재시도)은 같은 결과, revision 은 다시 오르지 않는다
    assert repo.record_human_response_once(cycle, SESSION, request_id, **kwargs) == (2, False)
    assert repo.get_task(cycle, "task-gh-41")["revision"] == 2
    with pytest.raises(ResponseConflict):  # 같은 response_id 에 다른 내용
        repo.record_human_response_once(cycle, SESSION, request_id, **{**kwargs, "text": "세 번 클릭"})
    with pytest.raises(StaleRequest) as err:  # 이미 응답된 요청에 다른 응답
        repo.record_human_response_once(cycle, SESSION, request_id, **{**kwargs, "response_id": "resp-2"})
    assert err.value.current_revision == 2
    with pytest.raises(NotFound):  # 다른 세션
        repo.record_human_response_once(cycle, OTHER_SESSION, request_id, **{**kwargs, "response_id": "resp-3"})


def test_human_response_with_stale_expected_revision_is_rejected(cycle):
    request_id, _ = repo.create_human_request_once(cycle, "task-gh-41", "decision", "q", "decision:1", NOW)
    with pytest.raises(StaleRequest) as err:
        repo.record_human_response_once(cycle, SESSION, request_id, response_id="r", expected_revision=0,
                                        action="answer", text="t", now=LATER)
    assert err.value.current_revision == 1
    assert repo.get_task(cycle, "task-gh-41")["revision"] == 1
    assert repo.get_human_request(cycle, SESSION, request_id)["state"] == "open"


def test_enqueue_source_delivery_once_by_body_digest(cycle):
    delivery, created = repo.enqueue_source_delivery_once(cycle, "task-gh-41", "<!-- runloom:task=task-gh-41 -->\n착수", NOW)
    assert created
    assert (delivery.source_id, delivery.issue_number, delivery.body_revision, delivery.state) == (SOURCE, 41, 1, "pending")
    assert (delivery.comment_id, delivery.attempts, delivery.next_at, delivery.last_error) == (None, 0, None, None)
    assert delivery.delivery_id.startswith("dlv-")
    same, created = repo.enqueue_source_delivery_once(cycle, "task-gh-41", "<!-- runloom:task=task-gh-41 -->\n착수", LATER)
    assert (same, created) == (delivery, False)  # 같은 본문은 새 revision 을 만들지 않는다
    newer, created = repo.enqueue_source_delivery_once(cycle, "task-gh-41", "<!-- runloom:task=task-gh-41 -->\n완료", LATER)
    assert created and newer.body_revision == 2
    with pytest.raises(NotFound):  # 원본 이슈가 없는 Task 는 반영할 곳이 없다
        repo.enqueue_source_delivery_once(cycle, TASK_A, "본문", LATER)


def test_github_token_env_never_reaches_db_or_wal(cycle, db_path, monkeypatch):
    secret = "ghp_" + "S3cr3tT0kenValue" * 3
    monkeypatch.setenv("WORKFLOW_GITHUB_TOKEN", secret)
    _create_execution(cycle, "exec-fix-1", "task-gh-41", kind="code_change", inputs=("art-x",))
    repo.save_github_source(cycle, SESSION, _source(config_revision=2), LATER)
    repo.save_source_cursor(cycle, SESSION, SOURCE, "cursor-1", LATER)
    repo.bind_assignee(cycle, SESSION, AssigneeBinding(source_id=SOURCE, github_user_id=5812345,
                                                       github_login="kim-dev", agent_id=FIX_AGENT), LATER)
    repo.upsert_source_issue(cycle, SESSION, SOURCE, _snapshot(body="edit", updated_at="2026-10-07T00:00:00Z"),
                             task=_fix_task(), now=LATER)
    repo.create_followup_once(cycle, _review_spec(), _review_task(), LATER, work_item_id=_wi41(cycle))
    request_id, _ = repo.create_human_request_once(cycle, "task-gh-41", "decision", "q", "decision:1", LATER)
    repo.record_human_response_once(cycle, SESSION, request_id, response_id="r", expected_revision=1,
                                    action="answer", text="t", now=LATER)
    repo.enqueue_source_delivery_once(cycle, "task-gh-41", "본문", LATER)
    for path in (db_path, db_path.with_name(db_path.name + "-wal")):
        if path.exists():
            assert secret.encode() not in path.read_bytes(), path


# --- step 10: 업무 순환 워커가 쓰는 조회·원자적 재시도 --------------------------------------


def test_create_execution_releases_previous_attempt_in_the_same_transaction(seeded):
    conn = seeded
    _create_execution(conn, "exec-1")
    repo.create_execution(
        conn, execution_id="exec-2", task_id=TASK_A, attempt_no=2, start_key="rework:exec-r1",
        agent_id="agent-ops-demo", kind="diagnosis", request=_request("exec-2", TASK_A),
        assigned_connector_id=None, predecessor_execution_id=None, now=LATER, release_execution_id="exec-1",
    )
    assert repo.get_execution(conn, "exec-1")["released_at"] == LATER
    assert repo.active_execution(conn, TASK_A)["execution_id"] == "exec-2"
    # 새 시도가 거부되면(같은 start_key) 이전 시도의 해제도 되돌린다 — 활성 실행이 사라지지 않는다
    with pytest.raises(DuplicateStartKey):
        repo.create_execution(
            conn, execution_id="exec-3", task_id=TASK_A, attempt_no=3, start_key="rework:exec-r1",
            agent_id="agent-ops-demo", kind="diagnosis", request=_request("exec-3", TASK_A),
            assigned_connector_id=None, predecessor_execution_id=None, now=LATER, release_execution_id="exec-2",
        )
    assert repo.get_execution(conn, "exec-2")["released_at"] is None


def test_list_human_requests_of_task(cycle):
    assert repo.list_human_requests(cycle, "task-gh-41") == []
    first, _ = repo.create_human_request_once(cycle, "task-gh-41", "fix_needs_information", "q", "a:1", NOW)
    second, _ = repo.create_human_request_once(cycle, "task-gh-41", "rework_limit_reached", "q", "b:1", LATER)
    rows = repo.list_human_requests(cycle, "task-gh-41")
    assert [(r["request_id"], r["cause_key"], r["state"]) for r in rows] == [(first, "a:1", "open"), (second, "b:1", "open")]


def test_busy_executions_counts_only_in_flight_fix_runs_on_the_same_registration(cycle):
    conn = cycle
    repo.upsert_agent(conn, _agent("agent-codex-mac", connection_type="local", api_url=None, credential_ref=None,
                                   local_registration_id="local-billing",
                                   capabilities=[{"code": "code.fix", "scope": {"repository_id": "billing"}}]))
    repo.insert_work_item_task(conn, _fix_task("task-gh-42", source_ref="#42"), NOW)
    _create_execution(conn, "exec-41", "task-gh-41", kind="bug_fix", inputs=())
    kinds = ("bug_fix", "code_change")
    assert repo.busy_executions(conn, "local-billing", kinds, exclude_task_id="task-gh-42") == ["exec-41"]
    assert repo.busy_executions(conn, "local-billing", kinds, exclude_task_id="task-gh-41") == []  # 자기 Task 는 제외
    assert repo.busy_executions(conn, "local-other", kinds, exclude_task_id="task-gh-42") == []
    assert repo.busy_executions(conn, "local-billing", ("code_review",), exclude_task_id="task-gh-42") == []
    # 결과가 나와 검토·사람을 기다리는 실행은 저장소를 쓰지 않는다 — 다른 수정 업무를 막지 않는다
    repo.append_event(conn, "exec-41", _event("exec-41", 1, "accepted", {}), "conn", NOW)
    repo.append_event(conn, "exec-41", _event("exec-41", 2, "failed",
                                              {"code": "timeout", "message": "x", "process_stopped": True}), "conn", NOW)
    assert repo.busy_executions(conn, "local-billing", kinds, exclude_task_id="task-gh-42") == []


# --- step 11: 사람 응답 — Agent 지정·종료·재개 입력 -----------------------------------------


def _respond(conn, request_id, response_id="resp-1", session_id=SESSION, **overrides):
    kwargs = {"response_id": response_id, "expected_revision": 1, "action": "resume", "text": "", "now": LATER}
    return repo.record_human_response_once(conn, session_id, request_id, **{**kwargs, **overrides})


def test_response_choosing_an_agent_sets_it_in_the_same_transaction(cycle):
    request_id, _ = repo.create_human_request_once(cycle, "task-gh-41", "assignee_multiple", "누구?", "ready:x", NOW)
    assert _respond(cycle, request_id, action="choose_agent", agent_id=FIX_AGENT) == (2, True)
    task = repo.get_task(cycle, "task-gh-41")
    assert (task["chosen_agent_id"], task["revision"]) == (FIX_AGENT, 2)
    # 같은 response_id 는 지정한 Agent 까지 같아야 같은 응답이다
    assert _respond(cycle, request_id, action="choose_agent", agent_id=FIX_AGENT) == (2, False)
    with pytest.raises(ResponseConflict):
        _respond(cycle, request_id, action="choose_agent", agent_id="agent-other")


def test_close_response_finishes_the_task_and_releases_its_attempt(cycle):
    _create_execution(cycle, "exec-fix-1", "task-gh-41", kind="code_change", inputs=("art-x",))
    request_id, _ = repo.create_human_request_once(cycle, "task-gh-41", "rework_limit_reached", "q", "k:1", NOW)
    _respond(cycle, request_id, action="close", close_reason="운영자 종료 — 사람 요청 응답")
    task = repo.get_task(cycle, "task-gh-41")
    assert (task["status"], task["status_reason"], task["finished_at"]) == ("실패", "운영자 종료 — 사람 요청 응답", LATER)
    assert repo.active_execution(cycle, "task-gh-41") is None
    assert repo.get_human_request(cycle, SESSION, request_id)["state"] == "answered"


def test_response_to_a_closed_task_is_rejected_but_a_resend_still_answers(cycle):
    first, _ = repo.create_human_request_once(cycle, "task-gh-41", "decision", "q", "k:1", NOW)
    second, _ = repo.create_human_request_once(cycle, "task-gh-41", "decision", "q", "k:2", NOW)
    _respond(cycle, first, action="close", close_reason="운영자 종료")
    assert _respond(cycle, first, action="close", close_reason="운영자 종료") == (2, False)  # 재전송
    with pytest.raises(TaskClosed):  # 마감된 Task 의 다른 열린 요청은 응답해도 재개되지 않는다
        _respond(cycle, second, response_id="resp-2")
    assert repo.get_task(cycle, "task-gh-41")["revision"] == 2


def test_create_execution_on_a_closed_task_is_refused(cycle):
    """종료와 착수의 경쟁 — 워커가 종료 전에 읽은 Task 로 실행을 만들려 해도 같은 트랜잭션이 막는다."""
    repo.update_task_status(cycle, "task-gh-41", "실패", "운영자 종료", finished_at=LATER, now=LATER)
    with pytest.raises(TaskClosed):
        _create_execution(cycle, "exec-fix-1", "task-gh-41", kind="code_change", inputs=("art-x",))
    assert repo.list_executions(cycle, "task-gh-41") == []


def test_list_human_responses_and_open_requests_of_a_session(cycle):
    first, _ = repo.create_human_request_once(cycle, "task-gh-41", "fix_needs_information", "재현 금액?", "a:1", NOW)
    second, _ = repo.create_human_request_once(cycle, "task-gh-41", "decision", "q", "b:1", NOW)
    assert [r["request_id"] for r in repo.list_open_human_requests(cycle, SESSION)] == [first, second]
    assert repo.list_open_human_requests(cycle, OTHER_SESSION) == []
    _respond(cycle, first, text="10,000원")
    rows = repo.list_human_responses(cycle, "task-gh-41")
    assert [(r["code"], r["question"], r["text"], r["task_revision"]) for r in rows] == [
        ("fix_needs_information", "재현 금액?", "10,000원", 2),
    ]
    assert [r["request_id"] for r in repo.list_open_human_requests(cycle, SESSION)] == [second]
    # 원본 스냅샷·Task 요청 원문은 응답으로 바뀌지 않는다
    assert repo.get_task(cycle, "task-gh-41")["request"] == "GitHub acme/billing#41"


# --- 측정: 업무 이벤트·설정 번호·실행 사용량 (phase 9 step 4, ADR-0015) ---------------


def _events(conn, task_id=TASK_A):
    return [(r["type"], json.loads(r["data_json"])) for r in repo.list_task_events(conn, task_id)]


def _changed(frm, to, reason, review_decision=None):
    return ("status_changed", {"from": frm, "to": to, "reason": reason, "review_decision": review_decision})


def test_update_task_status_records_status_changed_only_when_status_changes(seeded):
    repo.update_task_status(seeded, TASK_A, "실행 중", "agent-ops-demo 실행 중", now=LATER)
    rows = repo.list_task_events(seeded, TASK_A)
    assert len(rows) == 1
    row = rows[0]
    assert (row["session_id"], row["type"], row["task_revision"], row["config_revision"], row["occurred_at"]) == (
        SESSION, "status_changed", 1, SEEDED_REVISION, LATER)
    assert json.loads(row["data_json"]) == {"from": "실행 가능", "to": "실행 중", "reason": "agent-ops-demo 실행 중",
                                            "review_decision": None}
    # 같은 상태로 다시 쓰면(사유 문구만 바뀌어도) 기록하지 않는다
    repo.update_task_status(seeded, TASK_A, "실행 중", "다른 문구", now=LATER)
    assert len(repo.list_task_events(seeded, TASK_A)) == 1
    assert repo.get_task(seeded, TASK_A)["status_reason"] == "다른 문구"


def test_update_task_status_with_review_decision_records_even_without_status_change(seeded):
    repo.update_task_status(seeded, TASK_A, "확인 필요", "결과 도착", now=NOW)
    repo.update_task_status(seeded, TASK_A, "확인 필요", "재작업 요청", review_decision="request_changes", now=LATER)
    repo.update_task_status(seeded, TASK_A, "완료", "검토 승인", finished_at=LATER, review_decision="approve", now=LATER)
    assert _events(seeded) == [
        _changed("실행 가능", "확인 필요", "결과 도착"),
        _changed("확인 필요", "확인 필요", "재작업 요청", "request_changes"),
        _changed("확인 필요", "완료", "검토 승인", "approve"),
    ]


def test_finish_task_and_record_verdict_record_status_changed(seeded):
    _create_execution(seeded, "exec-1")
    repo.record_verdict(seeded, task_id=TASK_A, execution_id="exec-1", verdict={"outcome": "passed"},
                        status="실행 가능", reason="같은 상태", finish=False, now=NOW)
    assert _events(seeded) == []  # 상태가 그대로면 기록 없음
    repo.record_verdict(seeded, task_id=TASK_A, execution_id="exec-1", verdict={"outcome": "passed"},
                        status="확인 필요", reason="판정 근거: 12/12", finish=False, now=NOW)
    repo.finish_task(seeded, task_id=TASK_A, execution_id="exec-1", status="완료", reason="자동 완료", now=LATER)
    assert _events(seeded) == [_changed("실행 가능", "확인 필요", "판정 근거: 12/12"),
                               _changed("확인 필요", "완료", "자동 완료")]
    assert repo.list_task_events(seeded, TASK_A)[-1]["occurred_at"] == LATER


def test_close_response_records_status_changed(cycle):
    request_id, _ = repo.create_human_request_once(cycle, "task-gh-41", "rework_limit_reached", "q", "k:1", NOW)
    _respond(cycle, request_id, action="resume")  # 상태를 바꾸지 않는 응답은 기록 없음
    assert _events(cycle, "task-gh-41") == []
    request_id, _ = repo.create_human_request_once(cycle, "task-gh-41", "rework_limit_reached", "q", "k:2", NOW)
    _respond(cycle, request_id, "resp-2", expected_revision=1, action="close", close_reason="운영자 종료")
    rows = repo.list_task_events(cycle, "task-gh-41")
    assert _events(cycle, "task-gh-41") == [_changed("실행 가능", "실패", "운영자 종료")]
    assert rows[0]["task_revision"] == 3  # 응답 두 번으로 오른 뒤의 revision


def test_failed_transaction_leaves_no_status_event(seeded):
    _create_execution(seeded, "exec-1")
    seeded.execute(
        "CREATE TEMP TRIGGER boom BEFORE UPDATE OF released_at ON executions BEGIN SELECT RAISE(ABORT, 'boom'); END"
    )
    with pytest.raises(sqlite3.IntegrityError):
        repo.finish_task(seeded, task_id=TASK_A, execution_id="exec-1", status="완료", reason="x", now=LATER)
    assert repo.get_task(seeded, TASK_A)["status"] == "실행 가능"
    assert repo.list_task_events(seeded, TASK_A) == []


def test_append_task_event_reads_session_and_revisions_and_dedupes(seeded):
    repo.insert_kind(seeded, SESSION, REVIEW, NOW)
    blockers = {"blockers": [{"code": "input_missing", "actor": "operator"}]}
    assert repo.append_task_event(seeded, task_id=TASK_A, type="blocked", data=blockers, now=NOW) is True
    row = repo.list_task_events(seeded, TASK_A)[0]
    assert (row["session_id"], row["task_revision"], row["config_revision"]) == (SESSION, 1, SEEDED_REVISION + 1)
    # 같은 대기 목록은 다시 쓰지 않는다, 다르면 쓴다
    assert repo.append_task_event(seeded, task_id=TASK_A, type="blocked", data=blockers, now=LATER) is False
    other = {"blockers": [{"code": "approval_needed", "actor": "operator"}]}
    assert repo.append_task_event(seeded, task_id=TASK_A, type="blocked", data=other, now=LATER) is True
    ready = {"execution_id": "exec-1", "agent_id": "agent-ops-demo", "start_key": "auto:x"}
    assert repo.append_task_event(seeded, task_id=TASK_A, type="ready", data=ready, now=LATER) is True
    assert repo.append_task_event(seeded, task_id=TASK_A, type="ready", data=ready, now=LATER) is False
    # ready 뒤에는 이전과 같은 목록의 blocked 도 새 구간이다
    assert repo.append_task_event(seeded, task_id=TASK_A, type="blocked", data=other, now=LATER) is True
    assert [t for t, _ in _events(seeded)] == ["blocked", "blocked", "ready", "blocked"]
    with pytest.raises(NotFound):
        repo.append_task_event(seeded, task_id="nope", type="ready", data=ready, now=LATER)


def test_create_execution_with_ready_records_the_event_in_the_same_transaction(seeded):
    _create_execution(seeded, "exec-0", start_key="plain")  # 기본값 ready=False — 기록 없음
    seeded.execute("UPDATE executions SET released_at = ? WHERE execution_id = 'exec-0'", (NOW,))
    assert _events(seeded) == []
    repo.create_execution(
        seeded, execution_id="exec-1", task_id=TASK_A, attempt_no=2, start_key="auto:x", agent_id="agent-ops-demo",
        kind="diagnosis", request=_request("exec-1", TASK_A, "diagnosis", ()), assigned_connector_id=None,
        predecessor_execution_id=None, now=LATER, ready=True,
    )
    assert _events(seeded) == [("ready", {"execution_id": "exec-1", "agent_id": "agent-ops-demo", "start_key": "auto:x"})]
    assert repo.list_task_events(seeded, TASK_A)[0]["occurred_at"] == LATER
    # 실행이 거부되면(활성 잠금) 이벤트도 없다
    with pytest.raises(ActiveExecutionExists):
        repo.create_execution(
            seeded, execution_id="exec-2", task_id=TASK_A, attempt_no=3, start_key="auto:y",
            agent_id="agent-ops-demo", kind="diagnosis", request=_request("exec-2", TASK_A, "diagnosis", ()),
            assigned_connector_id=None, predecessor_execution_id=None, now=LATER, ready=True,
        )
    assert len(_events(seeded)) == 1


def test_record_blocked_writes_in_its_own_transaction_and_dedupes(seeded):
    blockers = [{"code": "input_missing", "actor": "assignee"}]
    assert repo.record_blocked(seeded, TASK_A, blockers, now=NOW) is True
    assert repo.record_blocked(seeded, TASK_A, blockers, now=LATER) is False
    assert _events(seeded) == [("blocked", {"blockers": blockers})]
    assert not seeded.in_transaction


def _revision(conn, session_id=SESSION):
    return repo.get_config_revision(conn, session_id)


def test_config_revision_bumps_on_kind_rule_and_source_changes(sessions):
    seeded = sessions
    assert (_revision(seeded), _revision(seeded, OTHER_SESSION)) == (1, 1)
    repo.insert_kind(seeded, SESSION, REVIEW, NOW)
    assert _revision(seeded) == 2
    with pytest.raises(DuplicateKind):  # 실패한 저장은 올리지 않는다
        repo.insert_kind(seeded, SESSION, REVIEW, NOW)
    assert _revision(seeded) == 2
    rule_id = repo.insert_rule(seeded, SESSION, FIX_TO_REVIEW, NOW)
    assert _revision(seeded) == 3
    repo.delete_rule(seeded, SESSION, rule_id)
    assert _revision(seeded) == 4
    with pytest.raises(NotFound):
        repo.delete_rule(seeded, SESSION, rule_id)
    assert _revision(seeded) == 4
    repo.delete_kind(seeded, SESSION, "review")
    assert _revision(seeded) == 5
    repo.save_github_source(seeded, SESSION, _source(), NOW)
    assert _revision(seeded) == 6
    repo.save_github_source(seeded, SESSION, _source(enabled=False, config_revision=2), LATER)
    assert _revision(seeded) == 7
    with pytest.raises(StaleConfig):
        repo.save_github_source(seeded, SESSION, _source(config_revision=3), LATER, expected_revision=1)
    assert _revision(seeded) == 7
    assert _revision(seeded, OTHER_SESSION) == 1  # 다른 세션은 그대로


def test_config_revision_is_not_bumped_by_assignee_binding(cycle):
    before = _revision(cycle)
    repo.bind_assignee(cycle, SESSION, AssigneeBinding(source_id=SOURCE, github_user_id=5812345,
                                                       github_login="kim-dev", agent_id=FIX_AGENT), NOW)
    assert _revision(cycle) == before


def test_bump_config_revision_runs_inside_the_callers_transaction(sessions):
    seeded = sessions
    seeded.execute("BEGIN IMMEDIATE")
    assert repo.bump_config_revision(seeded, SESSION) == 2
    seeded.execute("ROLLBACK")
    assert _revision(seeded) == 1
    with pytest.raises(NotFound):
        repo.get_config_revision(seeded, "sess-none")


def test_create_execution_stamps_current_config_revision(seeded):
    _create_execution(seeded, "exec-1")
    repo.release_execution(seeded, "exec-1", NOW)
    repo.insert_kind(seeded, SESSION, REVIEW, NOW)
    _create_execution(seeded, "exec-2", attempt_no=2, start_key="rework:1")
    assert repo.get_execution(seeded, "exec-1")["config_revision"] == SEEDED_REVISION
    assert repo.get_execution(seeded, "exec-2")["config_revision"] == SEEDED_REVISION + 1


def test_followup_link_can_record_the_session_config_revision(cycle):
    _create_execution(cycle, "exec-fix-1", "task-gh-41", kind="code_change", inputs=("art-x",))
    revision = repo.get_config_revision(cycle, SESSION)
    assert revision == SEEDED_REVISION + 1  # cycle 이 소스를 저장했다
    spec = FollowupTaskSpec(session_id=SESSION, kind="code_review", cause_execution_id="exec-fix-1",
                            predecessor_task_id="task-gh-41", rules_revision=revision)
    repo.create_followup_once(cycle, spec, _review_task(), NOW, work_item_id=_wi41(cycle))
    assert repo.get_followup_link(cycle, "task-gh-41-review")["rules_revision"] == SEEDED_REVISION + 1


_MEASURE = ("folder_commit", "folder_dirty", "cost_usd", "input_tokens", "output_tokens")


def _measure(conn, execution_id="exec-1"):
    row = repo.get_execution(conn, execution_id)
    return tuple(row[c] for c in _MEASURE)


def test_append_event_stores_folder_commit_and_usage_idempotently(seeded, store):
    _create_execution(seeded, "exec-1")
    repo.append_event(seeded, "exec-1", _event("exec-1", 1, "accepted", {}), "conn", NOW)
    started = _event("exec-1", 2, "started", {"runtime_ref": "pid:1", "folder_commit": "c" * 40, "folder_dirty": False})
    repo.append_event(seeded, "exec-1", started, "conn", NOW)
    assert _measure(seeded) == ("c" * 40, 0, None, None, None)
    repo.append_event(seeded, "exec-1", started, "conn", LATER)  # 재전송
    assert _measure(seeded) == ("c" * 40, 0, None, None, None)
    data = b"{}"
    created, _ = repo.store_artifact(seeded, store, execution_id="exec-1", meta=_meta(data, kind="diagnosis_result"),
                                     data=data, session_id=SESSION, now=NOW)
    usage = {"cost_usd": 0, "input_tokens": 5, "output_tokens": 7}
    ready = _event("exec-1", 3, "result_ready", {"result_artifact_id": created.artifact_id, "usage": usage})
    repo.append_event(seeded, "exec-1", ready, "conn", NOW)
    assert _measure(seeded) == ("c" * 40, 0, 0.0, 5, 7)  # 0 은 0 으로(모름과 다르다)


def test_append_event_without_measure_fields_keeps_null(running):
    repo.append_event(running, "exec-1", _event("exec-1", 3, "failed", {
        "code": "timeout", "message": "x", "process_stopped": True}), "conn", NOW)
    assert _measure(running) == (None, None, None, None, None)


def test_failed_event_usage_is_stored(running):
    repo.append_event(running, "exec-1", _event("exec-1", 3, "failed", {
        "code": "timeout", "message": "x", "process_stopped": True, "usage": {"cost_usd": 0.5}}), "conn", NOW)
    assert _measure(running) == (None, None, 0.5, None, None)


# --- 지표 입력 (phase 9 step 8) ----------------------------------------------


def _store_result(conn, store, execution_id: str, body: dict, kind: str = "code_review_result") -> str:
    data = json.dumps(body).encode()
    created, _ = repo.store_artifact(conn, store, execution_id=execution_id, meta=_meta(data, kind=kind),
                                     data=data, session_id=SESSION, now=NOW)
    return created.artifact_id


def _finish(conn, store, execution_id: str, body: dict, *, usage=None, folder_commit=None, kind="code_review_result"):
    repo.append_event(conn, execution_id, _event(execution_id, 1, "accepted", {}), "conn", NOW)
    started = {"runtime_ref": "pid:1"}
    if folder_commit:
        started |= {"folder_commit": folder_commit, "folder_dirty": True}
    repo.append_event(conn, execution_id, _event(execution_id, 2, "started", started, "2026-10-06T10:20:00Z"),
                      "conn", "2026-10-06T10:20:00Z")
    ready = {"result_artifact_id": _store_result(conn, store, execution_id, body, kind)}
    if usage:
        ready["usage"] = usage
    repo.append_event(conn, execution_id, _event(execution_id, 3, "result_ready", ready, "2026-10-06T10:30:00Z"),
                      "conn", "2026-10-06T10:30:00Z")


def test_list_metric_facts_reads_the_session_rows_as_domain_values(cycle, store):
    """세션의 Task·실행·이벤트·사람 요청을 값 객체로. 검토 outcome 은 판정이 통과한 결과 산출물에서 읽는다."""
    from workflow.domain.metrics import ExecutionFact, HumanRequestFact, MetricFacts, TaskEventFact, TaskFact

    repo.insert_work_item_task(cycle, _task("task-other", OTHER_SESSION), NOW)  # 다른 세션은 보이지 않는다
    _create_execution(cycle, "exec-fix-1", "task-gh-41", kind="code_change", inputs=("art-x",))
    _finish(cycle, store, "exec-fix-1", {"outcome": "ready_for_review"}, kind="code_change_result",
            usage={"cost_usd": 0.25, "input_tokens": 10}, folder_commit="c" * 40)
    repo.record_verdict(cycle, task_id="task-gh-41", execution_id="exec-fix-1", verdict={"outcome": "passed"},
                        status="확인 필요", reason="판정 통과", finish=False, now="2026-10-06T10:31:00Z")
    spec = _review_spec()
    repo.create_followup_once(cycle, spec, _review_task(), "2026-10-06T10:32:00Z", work_item_id=_wi41(cycle))
    repo.create_execution(cycle, execution_id="exec-rev-1", task_id="task-gh-41-review", attempt_no=1,
                          start_key="auto:task-gh-41-review:r1", agent_id="agent-claude-mac", kind="code_review",
                          request=_request("exec-rev-1", "task-gh-41-review", "code_change", ("art-x",)),
                          assigned_connector_id=None, predecessor_execution_id=None, now="2026-10-06T10:33:00Z")
    _finish(cycle, store, "exec-rev-1", {"outcome": "approved"})
    repo.record_verdict(cycle, task_id="task-gh-41-review", execution_id="exec-rev-1",
                        verdict={"outcome": "passed"}, status="확인 필요", reason="판정 통과", finish=False,
                        now="2026-10-06T10:34:00Z")
    request_id, _ = repo.create_human_request_once(cycle, "task-gh-41", "decision", "q", "decision:1",
                                                   "2026-10-06T10:35:00Z")

    facts = repo.list_metric_facts(cycle, SESSION, store=store)

    assert isinstance(facts, MetricFacts)
    tasks = {t.task_id: t for t in facts.tasks}
    assert set(tasks) == {TASK_A, "task-gh-41", "task-gh-41-review"}
    assert tasks["task-gh-41"] == TaskFact(
        task_id="task-gh-41", work_item_id=_wi41(cycle), kind="bug_fix", created_at=NOW, status="확인 필요",
        issue_opened_at="2026-10-06T10:12:00Z", issue_state="open",
    )
    assert tasks["task-gh-41-review"].work_item_id == _wi41(cycle)  # 같은 업무의 다음 단계 = 같은 묶음
    assert tasks[TASK_A].work_item_id != _wi41(cycle)
    assert tasks["task-gh-41-review"].issue_opened_at is None
    assert tasks[TASK_A].issue_opened_at is None
    assert tasks[TASK_A].issue_state is None and tasks["task-gh-41-review"].issue_state is None

    executions = {e.execution_id: e for e in facts.executions}
    assert executions["exec-fix-1"] == ExecutionFact(
        execution_id="exec-fix-1", task_id="task-gh-41", kind="code_change", attempt_no=1, status="result_ready",
        start_key="auto:task-gh-41:r1", created_at=NOW, started_at="2026-10-06T10:20:00Z",
        finished_at="2026-10-06T10:30:00Z", outcome="passed", config_revision=SEEDED_REVISION + 1, folder_commit="c" * 40,
        cost_usd=0.25, input_tokens=10,
    )
    assert executions["exec-rev-1"].outcome == "approved"  # 판정(passed)이 아니라 검토 결과
    assert executions["exec-rev-1"].output_tokens is None  # 모름은 None 그대로

    assert [(e.task_id, e.type, e.data["to"]) for e in facts.events] == [
        ("task-gh-41", "status_changed", "확인 필요"),
        ("task-gh-41-review", "status_changed", "확인 필요"),
    ]
    assert isinstance(facts.events[0], TaskEventFact)
    assert facts.human_requests == (
        HumanRequestFact(request_id=request_id, task_id="task-gh-41", created_at="2026-10-06T10:35:00Z"),
    )
    assert repo.list_metric_facts(cycle, OTHER_SESSION, store=store).executions == ()


def test_list_metric_facts_carries_the_source_issue_merge(cycle, store):
    """묶음 시작 Task 의 완료 시각 원천 — 원본 이슈 상태·병합 시각·마지막 조회 시각(step 12)."""
    closed = _snapshot(state="closed", updated_at="2026-10-06T11:00:00Z")
    repo.upsert_source_issue(cycle, SESSION, SOURCE, closed, task=_fix_task(), now=LATER)
    repo.record_issue_merge(cycle, session_id=SESSION, source_id=SOURCE, github_issue_id=2456789012,
                            link=_merge_link(), now=LATER)
    fact = next(t for t in repo.list_metric_facts(cycle, SESSION, store=store).tasks if t.task_id == "task-gh-41")
    assert (fact.issue_state, fact.pr_merged_at, fact.merge_checked_at) == ("closed", "2026-10-07T09:00:00Z", LATER)


def test_list_metric_facts_review_outcome_needs_a_passed_verdict(cycle, store):
    """판정 전·판정 실패(결과가 요청과 안 맞음)인 검토는 outcome 을 모른다 — 1회 통과율에서 '검토 결과 없음'."""
    repo.create_execution(cycle, execution_id="exec-rev-1", task_id="task-gh-41", attempt_no=1,
                          start_key="auto:task-gh-41:r1", agent_id="agent-claude-mac", kind="code_review",
                          request=_request("exec-rev-1", "task-gh-41", "code_change", ("art-x",)),
                          assigned_connector_id=None, predecessor_execution_id=None, now=NOW)
    _finish(cycle, store, "exec-rev-1", {"outcome": "approved"})
    assert repo.list_metric_facts(cycle, SESSION, store=store).executions[0].outcome is None
    repo.record_verdict(cycle, task_id="task-gh-41", execution_id="exec-rev-1", verdict={"outcome": "failed"},
                        status="확인 필요", reason="x", finish=False, now=LATER)
    assert repo.list_metric_facts(cycle, SESSION, store=store).executions[0].outcome is None


def test_github_source_created_at_is_the_first_connection_time(seeded):
    repo.save_github_source(seeded, SESSION, _source(), NOW)
    repo.save_github_source(seeded, SESSION, _source(config_revision=2), LATER)  # 설정 변경은 연결 시각을 바꾸지 않는다
    assert repo.github_source_created_at(seeded, SESSION, SOURCE) == NOW
    with pytest.raises(NotFound):
        repo.github_source_created_at(seeded, OTHER_SESSION, SOURCE)



# --- phase 12 step 6: push 결과·초안 PR 대기열 (ADR-0018 결정 4, ARCHITECTURE "스키마 v8") ---------------------


@pytest.mark.parametrize(("data", "expected"), [({"branch_pushed": True}, 1), ({"branch_pushed": False}, 0), ({}, None)])
def test_result_ready_copies_branch_pushed(running, store, data, expected):
    conn = running
    payload = b'{"outcome": "ready_for_handoff"}'
    created, _ = repo.store_artifact(conn, store, execution_id="exec-1", session_id=SESSION,
                                     meta=_meta(payload, "diagnosis_result"), data=payload, now=LATER)
    repo.append_event(conn, "exec-1", _event("exec-1", 3, "result_ready",
                                             {"result_artifact_id": created.artifact_id, **data}), "conn", LATER)
    assert repo.get_execution(conn, "exec-1")["branch_pushed"] == expected


def _fix_execution(conn) -> None:
    repo.create_execution(conn, execution_id="exec-fix-1", task_id="task-gh-41", attempt_no=1,
                          start_key="auto:task-gh-41:r1", agent_id=FIX_AGENT, kind="bug_fix",
                          request=_request("exec-fix-1", "task-gh-41", "code_change", ("art-x",)),
                          assigned_connector_id=None, predecessor_execution_id=None, now=NOW)


def _enqueue(conn, now: str = NOW) -> bool:
    return repo.enqueue_pull_request(
        conn, task_id="task-gh-41", session_id=SESSION, source_id=SOURCE, repository_full_name="acme/billing",
        issue_number=41, fix_execution_id="exec-fix-1", review_execution_id="exec-rev-1", now=now,
    )


def _pr(number: int = 31, **overrides) -> PullRequestRef:
    return PullRequestRef.model_validate({
        "number": number, "html_url": f"https://github.com/acme/billing/pull/{number}", "state": "open",
        "draft": True, "merged_at": None, **overrides,
    })


def test_enqueue_pull_request_once_per_fix_task(cycle):
    _fix_execution(cycle)
    assert _enqueue(cycle) is True
    assert _enqueue(cycle, LATER) is False  # 재평가·재시작에도 한 행
    (row,) = repo.pull_requests_due(cycle, NOW, max_attempts=5)
    assert (row["task_id"], row["head_branch"], row["state"], row["attempts"]) == (
        "task-gh-41", "task/task-gh-41", "pending", 0,
    )
    assert repo.get_pull_request_row(cycle, "task-gh-41")["created_at"] == NOW


def test_enqueue_pull_request_head_branch_follows_the_fix_request_work_key(cycle):
    """phase 14 step 7 — head 는 검토한 수정 실행 요청의 두 칸으로 `result_branch`. 이미 있는 행은 그대로."""
    keyed = ExecutionRequest.model_validate({
        **_request("exec-fix-1", "task-gh-41", "code_change", ("art-x",)).model_dump(),
        "work_key": "RUN-1", "branch_seq": 2,
    })
    repo.create_execution(cycle, execution_id="exec-fix-1", task_id="task-gh-41", attempt_no=1,
                          start_key="auto:task-gh-41:r1", agent_id=FIX_AGENT, kind="bug_fix", request=keyed,
                          assigned_connector_id=None, predecessor_execution_id=None, now=NOW)
    assert _enqueue(cycle) is True
    assert repo.get_pull_request_row(cycle, "task-gh-41")["head_branch"] == "runloom/RUN-1-2"


def test_enqueue_pull_request_keeps_existing_row_head_branch(cycle):
    _fix_execution(cycle)
    cycle.execute(
        "INSERT INTO task_pull_requests (task_id, session_id, source_id, repository_full_name, issue_number,"
        " head_branch, fix_execution_id, review_execution_id, state, created_at, updated_at)"
        " VALUES ('task-gh-41', ?, ?, 'acme/billing', 41, 'task/task-gh-41', 'exec-fix-1', 'exec-rev-0',"
        " 'pending', ?, ?)", (SESSION, SOURCE, NOW, NOW),
    )
    assert _enqueue(cycle, LATER) is False
    assert repo.get_pull_request_row(cycle, "task-gh-41")["head_branch"] == "task/task-gh-41"


def test_execution_branch_fields_new_task_gets_work_key_and_same_kind_sequence(seeded):
    """첫 실행이면 업무 키 + 그 업무의 같은 종류 단계 중 순번(다시 맡긴 단계는 2, 3 …)."""
    conn = seeded
    work = repo.work_item_of_task(conn, TASK_A)
    key = f"RUN-{work['key_number']}"
    repo.insert_task(conn, _task(TASK_B, kind="code_change", predecessor=TASK_A), LATER,
                     work_item_id=work["work_item_id"])
    repo.insert_task(conn, _task("diagnose-again"), LATER, work_item_id=work["work_item_id"])
    assert repo.execution_branch_fields(conn, TASK_A) == {"work_key": key, "branch_seq": 1}
    assert repo.execution_branch_fields(conn, TASK_B) == {"work_key": key, "branch_seq": 1}  # 종류가 다르면 따로 센다
    assert repo.execution_branch_fields(conn, "diagnose-again") == {"work_key": key, "branch_seq": 2}


def test_execution_branch_fields_copy_the_first_request_of_the_task(seeded):
    """이미 실행이 있으면 첫 요청의 두 칸 그대로 — 재작업은 같은 브랜치, v10 이전 요청(칸 없음)은 끝까지 `task/<id>`."""
    conn = seeded
    _create_execution(conn, "exec-1", TASK_A)
    assert repo.execution_branch_fields(conn, TASK_A) == {"work_key": None, "branch_seq": 1}
    keyed = ExecutionRequest.model_validate({**_request("exec-2", TASK_B, "code_change", ("art-x",)).model_dump(),
                                             "work_key": "RUN-9", "branch_seq": 3})
    repo.insert_task(conn, _task(TASK_B, kind="code_change"), LATER,
                     work_item_id=repo.work_item_of_task(conn, TASK_A)["work_item_id"])
    repo.create_execution(conn, execution_id="exec-2", task_id=TASK_B, attempt_no=1, start_key="auto:b:r1",
                          agent_id="agent-codex-mac", kind="code_change", request=keyed, assigned_connector_id=None,
                          predecessor_execution_id=None, now=LATER)
    assert repo.execution_branch_fields(conn, TASK_B) == {"work_key": "RUN-9", "branch_seq": 3}


def test_failed_attempt_backs_off_and_stops_at_the_limit(cycle):
    _fix_execution(cycle)
    _enqueue(cycle)
    later = "2026-09-20T00:01:00Z"
    repo.record_pull_request(cycle, "task-gh-41", state="pending", now=NOW, error="GitHub 응답 없음", next_at=later)
    assert repo.pull_requests_due(cycle, NOW, max_attempts=5) == []
    (row,) = repo.pull_requests_due(cycle, later, max_attempts=5)
    assert (row["attempts"], row["last_error"]) == (1, "GitHub 응답 없음")
    assert repo.pull_requests_due(cycle, later, max_attempts=1) == []


def test_open_pr_is_recorded_then_merged(cycle):
    _fix_execution(cycle)
    _enqueue(cycle)
    repo.record_pull_request(cycle, "task-gh-41", state="open", now=NOW, pr=_pr())
    row = repo.get_pull_request_row(cycle, "task-gh-41")
    assert (row["state"], row["pr_number"], row["pr_url"], row["draft"], row["last_error"]) == (
        "open", 31, "https://github.com/acme/billing/pull/31", 1, None,
    )
    assert repo.pull_requests_due(cycle, LATER, max_attempts=5) == []  # 열린 PR 은 다시 열지 않는다
    assert [r["task_id"] for r in repo.open_pull_requests(cycle)] == ["task-gh-41"]

    merged = _pr(state="closed", draft=False, merged_at="2026-09-21T00:00:00Z")
    repo.record_pull_request(cycle, "task-gh-41", state="merged", now=LATER, pr=merged)
    row = repo.get_pull_request_row(cycle, "task-gh-41")
    assert (row["state"], row["merged_at"], row["updated_at"]) == ("merged", "2026-09-21T00:00:00Z", LATER)
    assert repo.open_pull_requests(cycle) == []


def test_closed_without_merge_records_closed_at(cycle):
    _fix_execution(cycle)
    _enqueue(cycle)
    repo.record_pull_request(cycle, "task-gh-41", state="open", now=NOW, pr=_pr())
    repo.record_pull_request(cycle, "task-gh-41", state="closed", now=LATER, pr=_pr(state="closed"))
    row = repo.get_pull_request_row(cycle, "task-gh-41")
    assert (row["state"], row["closed_at"], row["merged_at"]) == ("closed", LATER, None)


def test_record_pull_request_unknown_task_is_not_found(cycle):
    with pytest.raises(NotFound):
        repo.record_pull_request(cycle, "task-nope", state="failed", now=NOW, error="x")


# --- phase 12 step 7: 알림 대기열 (ADR-0018 결정 5, ARCHITECTURE "알림 (step 7·8)") -----------------------------


def _notify(conn, key: str = "human_request:hr-1", now: str = NOW, event: str = "human_request") -> bool:
    return repo.enqueue_notification(
        conn, session_id=SESSION, event=event, task_id="task-gh-41", dedupe_key=key,
        content="[Runloom] 사람 차례 — 버그: 질문", payload={"title": "버그", "task_url": None, "pr_url": None}, now=now,
    )


def test_enqueue_notification_once_per_dedupe_key(cycle):
    assert _notify(cycle) is True
    assert _notify(cycle, now=LATER) is False  # 재평가·재시작에도 한 번
    assert _notify(cycle, "task_failed:exec-1", event="task_failed") is True
    rows = repo.notifications_due(cycle, NOW, max_attempts=5)
    assert [(r["dedupe_key"], r["state"], r["attempts"]) for r in rows] == [
        ("human_request:hr-1", "pending", 0), ("task_failed:exec-1", "pending", 0),
    ]
    assert rows[0]["notification_id"].startswith("ntf-") and len(rows[0]["notification_id"]) == 12
    assert json.loads(rows[0]["payload_json"]) == {"title": "버그", "task_url": None, "pr_url": None}


def test_notification_attempts_back_off_and_finish(cycle):
    _notify(cycle)
    (row,) = repo.notifications_due(cycle, NOW, max_attempts=5)
    later = "2026-09-20T00:01:00Z"
    repo.record_notification_attempt(cycle, row["notification_id"], state="pending", error="HTTP 500", now=NOW,
                                     next_at=later)
    assert repo.notifications_due(cycle, NOW, max_attempts=5) == []
    (row,) = repo.notifications_due(cycle, later, max_attempts=5)
    assert (row["attempts"], row["last_error"], row["sent_at"]) == (1, "HTTP 500", None)
    assert repo.notifications_due(cycle, later, max_attempts=1) == []

    repo.record_notification_attempt(cycle, row["notification_id"], state="sent", error=None, now=later, next_at=None)
    assert repo.notifications_due(cycle, LATER, max_attempts=5) == []
    (row,) = repo.list_notifications(cycle, SESSION)
    assert (row["state"], row["attempts"], row["sent_at"], row["next_at"]) == ("sent", 2, later, None)


def test_skipped_notification_does_not_count_an_attempt(cycle):
    _notify(cycle)
    (row,) = repo.notifications_due(cycle, NOW, max_attempts=5)
    repo.record_notification_attempt(cycle, row["notification_id"], state="skipped", error=None, now=NOW, next_at=None)
    (row,) = repo.list_notifications(cycle, SESSION)
    assert (row["state"], row["attempts"], row["sent_at"]) == ("skipped", 0, None)
    assert repo.list_notifications(cycle, OTHER_SESSION) == []


def test_personal_notification_needs_a_recipient_and_member_list_filters(cycle):
    """개인 행은 받는 사람이 늘 있다(repo 가 지킴). `/me` 목록 = 그 멤버의 개인 행 + recipient_member_ids 에 든 공용 행."""
    kim = repo.add_member(cycle, SESSION, display_name="김", now=NOW)
    lee = repo.add_member(cycle, SESSION, display_name="이", now=NOW)
    payload = {"title": "버그", "task_url": None, "pr_url": None}
    with pytest.raises(ValueError):
        repo.enqueue_notification(cycle, session_id=SESSION, event="human_request", task_id="task-gh-41",
                                  dedupe_key="human_request:hr-1:personal:x", content="c", payload=payload, now=NOW,
                                  channel="personal")
    repo.enqueue_notification(cycle, session_id=SESSION, event="human_request", task_id="task-gh-41",
                              dedupe_key="human_request:hr-1:shared", content="c → 김, 이",
                              payload={**payload, "recipient_member_ids": [kim, lee]}, now=NOW)
    repo.enqueue_notification(cycle, session_id=SESSION, event="human_request", task_id="task-gh-41",
                              dedupe_key=f"human_request:hr-1:personal:{kim}", content="c", payload=payload, now=NOW,
                              channel="personal", recipient_member_id=kim)
    repo.enqueue_notification(cycle, session_id=SESSION, event="task_failed", task_id="task-gh-41",
                              dedupe_key="task_failed:exec-1:shared", content="c → 이", now=LATER,
                              payload={**payload, "recipient_member_ids": [lee]}, recipient_member_id=lee)
    _notify(cycle)  # v11 이전 모양 — 받는 사람 없음

    rows = repo.list_notifications(cycle, SESSION)
    assert len(rows) == 4
    shared = {r["dedupe_key"]: (r["channel"], r["recipient_member_id"]) for r in rows}
    assert shared["human_request:hr-1:shared"] == ("shared", None)
    assert shared[f"human_request:hr-1:personal:{kim}"] == ("personal", kim)
    assert shared["task_failed:exec-1:shared"] == ("shared", lee)
    assert shared["human_request:hr-1"] == ("shared", None)
    assert [r["dedupe_key"] for r in repo.list_notifications(cycle, SESSION, member_id=kim)] == [
        f"human_request:hr-1:personal:{kim}", "human_request:hr-1:shared",
    ]
    assert [r["dedupe_key"] for r in repo.list_notifications(cycle, SESSION, member_id=lee)] == [
        "task_failed:exec-1:shared", "human_request:hr-1:shared",
    ]
    assert repo.list_notifications(cycle, SESSION, member_id=kim, limit=1)[0]["channel"] == "personal"


def test_record_notification_unknown_id_is_not_found(cycle):
    with pytest.raises(NotFound):
        repo.record_notification_attempt(cycle, "ntf-nope", state="failed", error="x", now=NOW, next_at=None)


# --- 업무(WorkItem) — ADR-0020, ARCHITECTURE "업무와 단계 — phase 14" ---------------


def _new_work(conn, session_id: str = SESSION, title: str = "업무", **overrides) -> tuple[str, int]:
    conn.execute("BEGIN IMMEDIATE")
    try:
        created = repo.create_work_item(conn, session_id, title=title, request="요청", kind="bug_fix",
                                        **{"source_type": "manual", "now": NOW, **overrides})
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")
    return created


def test_create_work_item_issues_keys_per_workspace(sessions):
    assert [_new_work(sessions)[1] for _ in range(3)] == [1, 2, 3]
    assert _new_work(sessions, OTHER_SESSION)[1] == 1  # 워크스페이스마다 1 부터
    work_item_id, key = _new_work(sessions, priority="high", source_type="n8n", source_id="chain-1",
                                  source_item_id="OPS-1", source_key="OPS-1")
    row = repo.get_work_item(sessions, SESSION, work_item_id)
    assert work_item_id.startswith("wi-") and key == 4
    assert (row["priority"], row["source_type"], row["source_key"], row["status"], row["closed_at"]) == (
        "high", "n8n", "OPS-1", "새로 들어옴", None)
    assert repo.get_work_item(sessions, OTHER_SESSION, work_item_id) is None  # 다른 워크스페이스에서는 없다
    assert repo.get_work_item_by_key(sessions, SESSION, 4)["work_item_id"] == work_item_id
    assert repo.get_work_item_by_key(sessions, OTHER_SESSION, 4) is None


def test_create_work_item_outside_transaction_is_an_error(sessions):
    with pytest.raises(RuntimeError):
        repo.create_work_item(sessions, SESSION, title="t", request="r", kind="bug_fix", source_type="manual",
                              now=NOW)
    assert sessions.execute("SELECT COUNT(*) FROM work_items").fetchone()[0] == 0


def test_concurrent_work_item_creation_gets_distinct_keys(sessions, db_path):
    """두 연결이 동시에 만들어도 BEGIN IMMEDIATE 가 번호 계산을 직렬화한다 — 재시도 없이 1·2."""
    barrier = threading.Barrier(2)
    keys: list[int] = []
    errors: list[BaseException] = []

    def create() -> None:
        c = connect(db_path)
        try:
            barrier.wait()
            keys.append(_new_work(c)[1])
        except BaseException as exc:
            errors.append(exc)
        finally:
            c.close()

    threads = [threading.Thread(target=create) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == [] and sorted(keys) == [1, 2]


def test_insert_task_joins_existing_work_item_of_same_session(seeded):
    conn = seeded
    work_item_id = repo.work_item_of_task(conn, TASK_A)["work_item_id"]
    repo.insert_task(conn, _task(TASK_B, kind="code_change", predecessor=TASK_A), LATER, work_item_id=work_item_id)
    assert [t["task_id"] for t in repo.list_work_item_tasks(conn, work_item_id)] == [TASK_A, TASK_B]
    other_work, _ = _new_work(conn, OTHER_SESSION)
    with pytest.raises(NotFound):  # 다른 워크스페이스의 업무에 단계를 붙이지 못한다
        repo.insert_task(conn, _task("t-x"), NOW, work_item_id=other_work)
    with pytest.raises(NotFound):
        repo.insert_task(conn, _task("t-y"), NOW, work_item_id="wi-none")
    assert repo.get_task(conn, "t-x") is None and repo.get_task(conn, "t-y") is None


def test_insert_work_item_task_creates_work_item_with_first_stage(seeded):
    conn = seeded
    row = repo.work_item_of_task(conn, TASK_A)
    assert (row["key_number"], row["title"], row["request"], row["kind"], row["source_type"]) == (
        1, "일일 보고서 실패 진단", "실패 원인을 조사해 주세요.", "diagnosis", "manual")
    assert (row["assignee_type"], row["created_at"]) == (None, NOW)
    chosen = {**_task("t-chosen"), "selection_mode": "manual", "chosen_agent_id": "agent-ops-demo"}
    repo.upsert_agent(conn, _agent())
    work_item_id = repo.insert_work_item_task(conn, chosen, LATER)
    assert repo.get_work_item(conn, SESSION, work_item_id)["assignee_id"] == "agent-ops-demo"
    before = conn.execute("SELECT COUNT(*) FROM work_items").fetchone()[0]
    with pytest.raises(NotFound):  # 첫 단계가 실패하면 업무도 남지 않는다
        repo.insert_work_item_task(conn, _task("t-bad", kind="review"), NOW)
    assert conn.execute("SELECT COUNT(*) FROM work_items").fetchone()[0] == before


def test_upsert_source_issue_creates_github_work_item_once(cycle):
    row = repo.work_item_of_task(cycle, "task-gh-41")
    assert (row["source_type"], row["source_id"], row["source_item_id"], row["source_key"], row["source_url"],
            row["source_state"]) == ("github", SOURCE, "2456789012", "acme/billing#41",
                                     "https://github.com/acme/billing/issues/41", "open")
    before = cycle.execute("SELECT COUNT(*) FROM work_items").fetchone()[0]
    edited = _snapshot(body="edit", updated_at="2026-10-07T00:00:00Z")
    assert repo.upsert_source_issue(cycle, SESSION, SOURCE, edited, task=_fix_task("t-2"), now=LATER).action == "updated"
    assert cycle.execute("SELECT COUNT(*) FROM work_items").fetchone()[0] == before


def test_create_followup_once_adds_stage_to_given_work_item(cycle):
    _create_execution(cycle, "exec-fix-1", "task-gh-41", kind="code_change", inputs=("art-x",))
    other_work, _ = _new_work(cycle, OTHER_SESSION)
    with pytest.raises(NotFound):  # 다른 워크스페이스의 업무
        repo.create_followup_once(cycle, _review_spec(), _review_task(), NOW, work_item_id=other_work)
    task_id, _ = repo.create_followup_once(cycle, _review_spec(), _review_task(), NOW, work_item_id=_wi41(cycle))
    assert repo.work_item_of_task(cycle, task_id)["work_item_id"] == _wi41(cycle)
    assert [t["task_id"] for t in repo.list_work_item_tasks(cycle, _wi41(cycle))] == ["task-gh-41", task_id]


def test_list_work_items_filters_and_orders_newest_key_first(sessions):
    conn = sessions
    first, _ = _new_work(conn, title="하나")
    second, _ = _new_work(conn, title="둘")
    third, _ = _new_work(conn, title="셋")
    _new_work(conn, OTHER_SESSION)
    admin = repo.ensure_first_admin(conn, SESSION, now=NOW)
    repo.assign_work_item(conn, SESSION, second, assignee_type="member", assignee_id=admin, now=LATER)
    repo.set_work_status(conn, third, WorkStatus("완료", "PR 병합 — #3"), now=LATER)
    assert [r["work_item_id"] for r in repo.list_work_items(conn, SESSION)] == [third, second, first]
    assert [r["work_item_id"] for r in repo.list_work_items(conn, SESSION, include_closed=False)] == [second, first]
    assert [r["work_item_id"] for r in repo.list_work_items(conn, SESSION, status="완료")] == [third]
    assert [r["work_item_id"] for r in repo.list_work_items(conn, SESSION, assignee=("member", admin))] == [second]
    assert len(repo.list_work_items(conn, OTHER_SESSION)) == 1


def test_assign_work_item_checks_workspace_and_records_event(seeded):
    conn = seeded
    work_item_id = repo.work_item_of_task(conn, TASK_A)["work_item_id"]
    admin = repo.ensure_first_admin(conn, SESSION, now=NOW)
    other_admin = repo.ensure_first_admin(conn, OTHER_SESSION, now=NOW)
    repo.upsert_agent(conn, _agent())
    repo.register_session_agent(conn, OTHER_SESSION, "agent-ops-demo", NOW)
    with pytest.raises(NotFound):  # 다른 워크스페이스의 멤버
        repo.assign_work_item(conn, SESSION, work_item_id, assignee_type="member", assignee_id=other_admin, now=NOW)
    with pytest.raises(NotFound):  # 이 워크스페이스에 등록되지 않은 Agent
        repo.assign_work_item(conn, SESSION, work_item_id, assignee_type="agent", assignee_id="agent-ops-demo",
                              now=NOW)
    with pytest.raises(NotFound):  # 다른 워크스페이스의 업무
        repo.assign_work_item(conn, OTHER_SESSION, work_item_id, assignee_type="member", assignee_id=other_admin,
                              now=NOW)
    assert repo.assign_work_item(conn, SESSION, work_item_id, assignee_type="member", assignee_id=admin, now=LATER)
    assert not repo.assign_work_item(conn, SESSION, work_item_id, assignee_type="member", assignee_id=admin,
                                     now=LATER)  # 같은 담당이면 그대로
    repo.register_session_agent(conn, SESSION, "agent-ops-demo", NOW)
    assert repo.assign_work_item(conn, SESSION, work_item_id, assignee_type="agent", assignee_id="agent-ops-demo",
                                 now=LATER)
    assert repo.assign_work_item(conn, SESSION, work_item_id, assignee_type=None, assignee_id=None, now=LATER)
    row = repo.get_work_item(conn, SESSION, work_item_id)
    assert (row["assignee_type"], row["assignee_id"], row["updated_at"]) == (None, None, LATER)
    # 바뀔 때마다 `assigned` 한 행 + 업무 상태 재계산(phase 16 — 상태가 바뀌면 `status_changed` 도 남는다)
    events = [e for e in repo.list_work_item_events(conn, work_item_id) if e["type"] == "assigned"]
    assert [json.loads(e["data_json"]) for e in events] == [
        {"from": None, "to": {"type": "member", "id": admin}, "by": None},
        {"from": {"type": "member", "id": admin}, "to": {"type": "agent", "id": "agent-ops-demo"}, "by": None},
        {"from": {"type": "agent", "id": "agent-ops-demo"}, "to": None, "by": None},
    ]
    assert events[0]["session_id"] == SESSION and events[0]["config_revision"] == SEEDED_REVISION
    assert {e["type"] for e in repo.list_work_item_events(conn, work_item_id)} <= {"assigned", "status_changed"}


def test_assign_work_item_records_who_and_refreshes_the_work_status(seeded):
    conn = seeded
    work_item_id = repo.work_item_of_task(conn, TASK_A)["work_item_id"]
    admin = repo.ensure_first_admin(conn, SESSION, now=NOW)
    assert repo.get_work_item(conn, SESSION, work_item_id)["status_reason"] == ""  # 계산 전 저장값
    assert repo.assign_work_item(conn, SESSION, work_item_id, assignee_type="member", assignee_id=admin,
                                 by_member_id=admin, now=LATER)
    (event,) = [e for e in repo.list_work_item_events(conn, work_item_id) if e["type"] == "assigned"]
    assert json.loads(event["data_json"])["by"] == admin
    row = repo.get_work_item(conn, SESSION, work_item_id)
    assert WorkStatus(row["status"], row["status_reason"]) == work_status(repo.work_item_facts(conn, work_item_id))
    assert row["status_reason"] != ""


def _direct(conn, work_item_id: str) -> tuple:
    row = conn.execute("SELECT direct_member_id, direct_started_at, direct_branch FROM work_items WHERE work_item_id = ?",
                       (work_item_id,)).fetchone()
    return tuple(row)


def _events_of(conn, work_item_id: str, type_: str) -> list[dict]:
    return [json.loads(e["data_json"]) for e in repo.list_work_item_events(conn, work_item_id) if e["type"] == type_]


def test_start_and_stop_direct_work_fill_and_clear_three_columns(seeded):
    conn = seeded
    work_item_id = repo.work_item_of_task(conn, TASK_A)["work_item_id"]
    admin = repo.ensure_first_admin(conn, SESSION, now=NOW)
    repo.set_member_display_name(conn, SESSION, admin, "김개발", now=NOW)
    assert repo.start_direct_work(conn, SESSION, work_item_id, member_id=admin, branch="RUN-1-fix", now=LATER)
    assert _direct(conn, work_item_id) == (admin, LATER, "RUN-1-fix")
    row = repo.get_work_item(conn, SESSION, work_item_id)
    assert (row["assignee_type"], row["assignee_id"]) == ("member", admin)
    assert (row["status"], row["status_reason"]) == ("직접 작업 중", "김개발")
    assert repo.work_item_facts(conn, work_item_id).direct_member_name == "김개발"
    assert _events_of(conn, work_item_id, "direct_started") == [{"member_id": admin, "branch": "RUN-1-fix"}]
    assert _events_of(conn, work_item_id, "assigned")[-1]["by"] == admin
    # 같은 멤버가 다시 → 변화 없음(브랜치 이름은 처음 값)
    assert not repo.start_direct_work(conn, SESSION, work_item_id, member_id=admin, branch="RUN-1-other", now=LATER)
    assert _direct(conn, work_item_id) == (admin, LATER, "RUN-1-fix")

    assert repo.stop_direct_work(conn, work_item_id, reason="stopped", now=LATER)
    assert not repo.stop_direct_work(conn, work_item_id, reason="stopped", now=LATER)  # 이미 아님
    assert _direct(conn, work_item_id) == (None, None, None)
    row = repo.get_work_item(conn, SESSION, work_item_id)
    assert (row["assignee_type"], row["assignee_id"]) == ("member", admin)  # 담당은 그대로
    assert row["status"] != "직접 작업 중"
    assert _events_of(conn, work_item_id, "direct_stopped") == [{"member_id": admin, "reason": "stopped"}]
    with pytest.raises(ValueError):
        repo.stop_direct_work(conn, work_item_id, reason="bored", now=LATER)
    with pytest.raises(NotFound):  # 다른 워크스페이스 업무
        repo.start_direct_work(conn, OTHER_SESSION, work_item_id, member_id=admin, branch="x", now=LATER)


def test_start_direct_work_by_another_member_ends_the_first_and_takes_the_assignee(seeded):
    conn = seeded
    work_item_id = repo.work_item_of_task(conn, TASK_A)["work_item_id"]
    admin = repo.ensure_first_admin(conn, SESSION, now=NOW)
    other = repo.add_member(conn, SESSION, display_name="이담당", now=NOW)
    repo.start_direct_work(conn, SESSION, work_item_id, member_id=admin, branch="RUN-1", now=NOW)
    assert repo.start_direct_work(conn, SESSION, work_item_id, member_id=other, branch="RUN-1", now=LATER)
    assert _direct(conn, work_item_id) == (other, LATER, "RUN-1")
    assert repo.get_work_item(conn, SESSION, work_item_id)["assignee_id"] == other
    assert _events_of(conn, work_item_id, "direct_stopped") == [{"member_id": admin, "reason": "reassigned"}]


def test_reassigning_a_direct_work_ends_it(seeded):
    conn = seeded
    work_item_id = repo.work_item_of_task(conn, TASK_A)["work_item_id"]
    admin = repo.ensure_first_admin(conn, SESSION, now=NOW)
    other = repo.add_member(conn, SESSION, display_name="이담당", now=NOW)
    repo.start_direct_work(conn, SESSION, work_item_id, member_id=admin, branch="RUN-1", now=NOW)
    repo.assign_work_item(conn, SESSION, work_item_id, assignee_type="member", assignee_id=other, by_member_id=admin,
                          now=LATER)
    assert _direct(conn, work_item_id) == (None, None, None)
    assert _events_of(conn, work_item_id, "direct_stopped") == [{"member_id": admin, "reason": "reassigned"}]


def test_terminal_status_clears_direct_work_in_the_same_write(seeded):
    conn = seeded
    work_item_id = repo.work_item_of_task(conn, TASK_A)["work_item_id"]
    admin = repo.ensure_first_admin(conn, SESSION, now=NOW)
    repo.start_direct_work(conn, SESSION, work_item_id, member_id=admin, branch="RUN-1", now=NOW)
    assert repo.set_work_status(conn, work_item_id, WorkStatus("종료", "원본 이슈 닫힘"), now=LATER)
    assert _direct(conn, work_item_id) == (None, None, None)
    assert _events_of(conn, work_item_id, "direct_stopped") == [{"member_id": admin, "reason": "closed"}]
    assert repo.get_work_item(conn, SESSION, work_item_id)["closed_at"] == LATER


def test_set_work_priority_records_from_to_by(seeded):
    conn = seeded
    work_item_id = repo.work_item_of_task(conn, TASK_A)["work_item_id"]
    admin = repo.ensure_first_admin(conn, SESSION, now=NOW)
    with pytest.raises(ValueError):
        repo.set_work_priority(conn, SESSION, work_item_id, "urgent", member_id=admin, now=LATER)
    with pytest.raises(NotFound):
        repo.set_work_priority(conn, OTHER_SESSION, work_item_id, "high", member_id=admin, now=LATER)
    assert repo.set_work_priority(conn, SESSION, work_item_id, "high", member_id=admin, now=LATER)
    assert not repo.set_work_priority(conn, SESSION, work_item_id, "high", member_id=admin, now=LATER)
    row = repo.get_work_item(conn, SESSION, work_item_id)
    assert (row["priority"], row["updated_at"]) == ("high", LATER)
    (event,) = [e for e in repo.list_work_item_events(conn, work_item_id) if e["type"] == "priority_changed"]
    assert json.loads(event["data_json"]) == {"from": "normal", "to": "high", "by": admin}


def test_refresh_work_status_writes_only_on_change_and_is_idempotent(seeded):
    conn = seeded
    work_item_id = repo.work_item_of_task(conn, TASK_A)["work_item_id"]
    # TASK_A 는 '실행 가능' 한 단계, 실행 없음·담당 없음 → 새로 들어옴(담당 없음)
    assert repo.refresh_work_status(conn, work_item_id, now=LATER)
    assert not repo.refresh_work_status(conn, work_item_id, now=LATER)
    (event,) = repo.list_work_item_events(conn, work_item_id)
    assert event["type"] == "status_changed"
    assert json.loads(event["data_json"]) == {"from": "새로 들어옴", "to": "새로 들어옴", "reason": "담당 없음"}
    repo.finish_task(conn, task_id=TASK_A, execution_id="exec-none", status="완료", reason="판정 통과", now=LATER)
    assert not repo.refresh_work_status(conn, work_item_id, now=LATER)  # 단계 마감이 같은 트랜잭션에서 이미 기록했다
    row = repo.get_work_item(conn, SESSION, work_item_id)
    assert (row["status"], row["closed_at"], row["updated_at"]) == ("완료", LATER, LATER)
    assert not repo.refresh_work_status(conn, work_item_id, now="2026-09-21T00:00:00Z")  # 끝 상태는 그대로
    assert [json.loads(e["data_json"])["to"] for e in repo.list_work_item_events(conn, work_item_id)] == [
        "새로 들어옴", "완료"]
    with pytest.raises(NotFound):
        repo.refresh_work_status(conn, "wi-none", now=LATER)


def test_link_work_items_same_workspace_once(sessions):
    first, _ = _new_work(sessions)
    second, _ = _new_work(sessions)
    other, _ = _new_work(sessions, OTHER_SESSION)
    assert repo.link_work_items(sessions, from_work_item_id=first, to_work_item_id=second, type="blocks", now=NOW)
    assert not repo.link_work_items(sessions, from_work_item_id=first, to_work_item_id=second, type="blocks",
                                    now=LATER)
    with pytest.raises(NotFound):
        repo.link_work_items(sessions, from_work_item_id=first, to_work_item_id=other, type="blocks", now=NOW)
    (link,) = repo.list_work_item_links(sessions, second)
    assert (link["from_work_item_id"], link["to_work_item_id"], link["type"]) == (first, second, "blocks")
    assert repo.list_work_item_links(sessions, first) == repo.list_work_item_links(sessions, second)


def test_members_first_admin_and_added_member(sessions):
    admin = repo.ensure_first_admin(sessions, SESSION, now=NOW)
    assert repo.ensure_first_admin(sessions, SESSION, now=LATER) == admin  # 한 명만
    member = repo.add_member(sessions, SESSION, display_name="김개발", now=LATER)
    assert member.startswith("mem-")
    rows = repo.list_members(sessions, SESSION)
    assert [(r["member_id"], r["display_name"], r["role"]) for r in rows] == [
        (admin, "관리자", "admin"), (member, "김개발", "member")]
    assert [r["role"] for r in repo.list_members(sessions, OTHER_SESSION)] == ["admin"]


# --- phase 14 step 5: 접수 — 업무 양식·우선순위·원본 갱신, blocks 링크, 매핑 표 ---------------------------------

FORM_BODY = "### 목표\n쿠폰 한 번만\n\n### 재현 절차\n1. 쿠폰 적용"
FORM = {"goal": {"value": "쿠폰 한 번만", "source": "github_body:### 목표"}}


def test_upsert_source_issue_new_work_item_takes_form_and_priority(seeded):
    repo.save_github_source(seeded, SESSION, _source(), NOW)
    repo.upsert_source_issue(seeded, SESSION, SOURCE, _snapshot(body=FORM_BODY), task=_fix_task(),
                             form=FORM, priority="high", now=NOW)
    work = repo.work_item_of_task(seeded, "task-gh-41")
    assert json.loads(work["form_json"]) == FORM
    assert (work["priority"], work["assignee_type"], work["revision"]) == ("high", None, 1)


def test_upsert_source_issue_updates_work_item_input_like_the_first_stage(cycle):
    def work():
        return repo.work_item_of_task(cycle, "task-gh-41")

    assigned = _snapshot(assignee_ids=[1], assignee_logins=["a"], updated_at="2026-10-06T10:20:00Z")
    repo.upsert_source_issue(cycle, SESSION, SOURCE, assigned, task=_fix_task(), form=FORM, now=LATER)
    assert (work()["revision"], work()["form_json"]) == (1, "{}")  # 입력이 그대로면 업무도 그대로

    edited = _snapshot(body=FORM_BODY, updated_at="2026-10-06T10:30:00Z")
    repo.upsert_source_issue(cycle, SESSION, SOURCE, edited,
                             task=_fix_task(title="새 제목", request=FORM_BODY), form=FORM, now=LATER)
    row = work()
    assert (row["title"], row["request"], row["revision"], row["updated_at"]) == ("새 제목", FORM_BODY, 2, LATER)
    assert json.loads(row["form_json"]) == FORM

    closed = _snapshot(body=FORM_BODY, state="closed", updated_at="2026-10-06T10:40:00Z")
    repo.upsert_source_issue(cycle, SESSION, SOURCE, closed,
                             task=_fix_task(title="새 제목", request=FORM_BODY), form=FORM, now=LATER)
    assert (work()["source_state"], work()["revision"]) == ("closed", 2)  # 원본 상태만


def test_upsert_source_issue_keeps_work_item_input_after_first_stage_closes(cycle):
    repo.update_task_status(cycle, "task-gh-41", "실패", "운영자 종료", finished_at=LATER, now=LATER)
    edited = _snapshot(body="다시 편집", updated_at="2026-10-06T10:30:00Z")
    repo.upsert_source_issue(cycle, SESSION, SOURCE, edited, task=_fix_task(request="다시 편집"), form=FORM,
                             now=LATER)
    row = repo.work_item_of_task(cycle, "task-gh-41")
    assert (row["request"], row["revision"], row["form_json"]) == ("GitHub acme/billing#41", 1, "{}")


def test_mark_issue_delegated_refreshes_work_status(seeded):
    repo.upsert_agent(seeded, _agent(FIX_AGENT, connection_type="local", api_url=None, credential_ref=None,
                                     capabilities=[{"code": "code.fix", "scope": {"repository_id": "billing"}}]))
    repo.save_github_source(seeded, SESSION, _source(intake="all_open", label_filter=[]), NOW)
    task = _fix_task(selection_mode="manual", chosen_agent_id=FIX_AGENT)
    repo.upsert_source_issue(seeded, SESSION, SOURCE, _snapshot(), task=task, now=NOW)
    repo.refresh_open_work_statuses(seeded, now=NOW)
    assert repo.work_item_of_task(seeded, "task-gh-41")["status"] == "새로 들어옴"  # 지시 전
    repo.mark_issue_delegated(seeded, session_id=SESSION, source_id=SOURCE, github_issue_id=2456789012,
                              by="operator", now=LATER)
    work = repo.work_item_of_task(seeded, "task-gh-41")
    assert work["status"] == "대기"
    assert json.loads(repo.list_work_item_events(seeded, work["work_item_id"])[-1]["data_json"])["to"] == "대기"


def test_insert_work_item_task_with_predecessor_links_blocks(seeded):
    """다른 업무의 Task 를 선행으로 둔 새 업무(체인 `blocked_by`·폼 선행)는 `blocks` 링크(앞 업무 → 새 업무)로도 남는다."""
    later = repo.insert_work_item_task(seeded, _task(TASK_B, kind="code_change", predecessor=TASK_A), NOW)
    first = repo.work_item_of_task(seeded, TASK_A)["work_item_id"]
    (link,) = repo.list_work_item_links(seeded, later)
    assert (link["from_work_item_id"], link["to_work_item_id"], link["type"]) == (first, later, "blocks")
    assert repo.get_task(seeded, TASK_B)["predecessor_task_id"] == TASK_A
    alone = repo.insert_work_item_task(seeded, _task("t-alone"), NOW)
    assert repo.list_work_item_links(seeded, alone) == []


def _mapping(source_value: str, runloom_value: str, *, field: str = "kind", position: int = 1,
             source_type: str = "github") -> MappingRow:
    return MappingRow(source_type, field, source_value, runloom_value, position)


def test_field_mappings_start_with_default_and_replace_bumps_config_revision(sessions):
    assert repo.list_field_mappings(sessions, SESSION) == [_mapping("*", "bug_fix")]
    revision = repo.get_config_revision(sessions, SESSION)
    rows = [_mapping("docs", "code_review", position=1), _mapping("*", "bug_fix", position=2),
            _mapping("P1", "high", field="priority", position=3)]
    assert repo.replace_field_mappings(sessions, SESSION, rows, now=LATER) == revision + 1
    assert repo.get_config_revision(sessions, SESSION) == revision + 1
    assert repo.list_field_mappings(sessions, SESSION) == rows
    assert repo.list_field_mappings(sessions, SESSION, "github", "priority") == rows[2:]
    assert repo.list_field_mappings(sessions, OTHER_SESSION) == [_mapping("*", "bug_fix")]  # 다른 워크스페이스는 그대로


@pytest.mark.parametrize("rows", [
    [_mapping("docs", "no_such_kind")],  # 등록되지 않은 종류
    [_mapping("p1", "urgent", field="priority")],  # 우선순위 값 밖
    [_mapping("bug", "bug_fix"), _mapping("BUG", "code_review", position=2)],  # 같은 원본 값 두 번(대소문자 무시)
])
def test_replace_field_mappings_rejects_invalid_rows_and_keeps_old(sessions, rows):
    revision = repo.get_config_revision(sessions, SESSION)
    with pytest.raises(ValueError):
        repo.replace_field_mappings(sessions, SESSION, rows, now=LATER)
    assert repo.list_field_mappings(sessions, SESSION) == [_mapping("*", "bug_fix")]
    assert repo.get_config_revision(sessions, SESSION) == revision


# --- 단계 상태를 쓰면 업무 상태도 (phase 14 step 6) ----------------------------------------------


def _work(conn, task_id: str = TASK_A):
    return repo.work_item_of_task(conn, task_id)


def _status_events(conn, task_id: str = TASK_A) -> list[str]:
    events = repo.list_work_item_events(conn, _work(conn, task_id)["work_item_id"])
    return [json.loads(e["data_json"])["to"] for e in events if e["type"] == "status_changed"]


def test_every_stage_status_write_refreshes_the_work_status(seeded, store):
    conn = seeded
    repo.update_task_status(conn, TASK_A, "대기", "선행 대기", now=NOW)
    assert (_work(conn)["status"], _work(conn)["status_reason"]) == ("새로 들어옴", "담당 없음")
    _create_execution(conn, "exec-1")
    assert (_work(conn)["status"], _work(conn)["status_reason"]) == ("대기", "선행 대기")  # 실행이 생겼다
    repo.update_task_status(conn, TASK_A, "실행 요청됨", "접수 대기", now=NOW)
    assert _work(conn)["status"] == "에이전트 작업 중"
    repo.create_human_request_once(conn, TASK_A, "decision", "어느 쪽으로 고칠까요?", "decision:1", NOW)
    assert (_work(conn)["status"], _work(conn)["status_reason"]) == ("내 차례", "사람 요청 — 어느 쪽으로 고칠까요?")
    repo.record_verdict(conn, task_id=TASK_A, execution_id="exec-1", verdict={"outcome": "passed"}, status="완료",
                        reason="판정 통과", finish=True, now=LATER)
    assert _work(conn)["status"] == "내 차례"  # 열린 요청이 앞선다
    assert _status_events(conn) == ["새로 들어옴", "대기", "에이전트 작업 중", "내 차례"]
    repo.update_task_status(conn, TASK_A, "대기", "선행 대기", now=LATER)  # 같은 업무 상태 — 재기록 없음
    assert _status_events(conn) == ["새로 들어옴", "대기", "에이전트 작업 중", "내 차례"]


def test_the_first_agent_to_take_a_stage_becomes_the_work_assignee(seeded):
    conn = seeded
    assert (_work(conn)["assignee_type"], _work(conn)["assignee_id"]) == (None, None)
    _create_execution(conn, "exec-1")
    assert (_work(conn)["assignee_type"], _work(conn)["assignee_id"]) == ("agent", "agent-ops-demo")
    (event,) = [e for e in repo.list_work_item_events(conn, _work(conn)["work_item_id"]) if e["type"] == "assigned"]
    assert json.loads(event["data_json"]) == {"from": None, "to": {"type": "agent", "id": "agent-ops-demo"}}
    repo.release_execution(conn, "exec-1", NOW)
    repo.create_execution(conn, execution_id="exec-2", task_id=TASK_A, attempt_no=2, start_key="manual:2",
                          agent_id="agent-other", kind="diagnosis", request=_request("exec-2", TASK_A),
                          assigned_connector_id=None, predecessor_execution_id=None, now=LATER)
    assert _work(conn)["assignee_id"] == "agent-ops-demo"  # 이미 담당이 있으면 바꾸지 않는다


def test_refresh_open_work_statuses_skips_closed_work_items(seeded):
    conn = seeded
    assert repo.refresh_open_work_statuses(conn, now=NOW) == 1  # '' 이유로 시작한 업무에 계산값
    assert repo.refresh_open_work_statuses(conn, now=NOW) == 0
    repo.finish_task(conn, task_id=TASK_A, execution_id="exec-none", status="완료", reason="판정 통과", now=LATER)
    assert _work(conn)["status"] == "완료"
    assert repo.refresh_open_work_statuses(conn, now=LATER) == 0


def test_finish_failed_stage_closes_the_stage_and_asks_once(seeded):
    conn = seeded
    _create_execution(conn, "exec-1")
    request_id, fresh = repo.finish_failed_stage(
        conn, task_id=TASK_A, execution_id="exec-1", reason="timeout · 20분 초과",
        question="실행 실패 — timeout: 20분 초과", cause_key="stage_failed:exec-1", now=LATER,
    )
    assert fresh
    task = repo.get_task(conn, TASK_A)
    assert (task["status"], task["status_reason"], task["finished_at"]) == ("실패", "timeout · 20분 초과", LATER)
    assert repo.get_execution(conn, "exec-1")["released_at"] == LATER
    (request,) = repo.list_human_requests(conn, TASK_A)
    assert (request["request_id"], request["code"], request["state"]) == (request_id, "stage_failed", "open")
    assert (_work(conn)["status"], _work(conn)["status_reason"]) == ("내 차례", "실패 — timeout · 20분 초과")
    again = repo.finish_failed_stage(
        conn, task_id=TASK_A, execution_id="exec-1", reason="timeout · 20분 초과",
        question="실행 실패 — timeout: 20분 초과", cause_key="stage_failed:exec-1", now=LATER,
    )
    assert again == (request_id, False) and len(repo.list_human_requests(conn, TASK_A)) == 1


def _failed_stage(conn) -> str:
    _create_execution(conn, "exec-1")
    request_id, _ = repo.finish_failed_stage(
        conn, task_id=TASK_A, execution_id="exec-1", reason="timeout · 20분 초과",
        question="실행 실패 — timeout: 20분 초과", cause_key="stage_failed:exec-1", now=NOW,
    )
    return request_id


def test_retry_response_copies_the_failed_stage_into_the_same_work_item(seeded):
    conn = seeded
    request_id = _failed_stage(conn)
    repo.record_human_response_once(conn, SESSION, request_id, response_id="resp-1", expected_revision=1,
                                    action="retry", text="", now=LATER, retry_task_id="task-retry")
    retried = repo.get_task(conn, "task-retry")
    first = repo.get_task(conn, TASK_A)
    assert retried["work_item_id"] == first["work_item_id"]
    assert (retried["request"], retried["predecessor_task_id"], retried["status"]) == (
        first["request"], first["predecessor_task_id"], "대기")
    assert repo.get_human_request(conn, SESSION, request_id)["state"] == "answered"
    assert _work(conn)["status"] == "대기"
    with pytest.raises(StaleRequest):
        repo.record_human_response_once(conn, SESSION, request_id, response_id="resp-2", expected_revision=2,
                                        action="retry", text="", now=LATER, retry_task_id="task-retry-2")
    assert repo.get_task(conn, "task-retry-2") is None


def test_close_response_on_a_failed_stage_ends_the_work_item(seeded):
    conn = seeded
    request_id = _failed_stage(conn)
    repo.record_human_response_once(conn, SESSION, request_id, response_id="resp-1", expected_revision=1,
                                    action="close", text="", now=LATER, close_work_reason="닫음 — 실행 실패")
    row = _work(conn)
    assert (row["status"], row["status_reason"], row["closed_at"]) == ("종료", "닫음 — 실행 실패", LATER)


def test_response_to_a_non_stage_failed_request_on_a_closed_task_is_still_rejected(seeded):
    conn = seeded
    request_id, _ = repo.create_human_request_once(conn, TASK_A, "decision", "q", "decision:1", NOW)
    repo.finish_task(conn, task_id=TASK_A, execution_id="exec-none", status="실패", reason="운영자 종료", now=NOW)
    with pytest.raises(TaskClosed):
        repo.record_human_response_once(conn, SESSION, request_id, response_id="resp-1", expected_revision=1,
                                        action="resume", text="", now=LATER)


def test_retried_stage_reads_the_source_issue_of_its_work_item(cycle):
    """다시 맡긴 단계(같은 업무·원본 이슈 단계와 같은 종류)는 원본 이슈를 그대로 읽는다 — 담당·입력·PR 이 이어진다.
    같은 업무의 다른 종류 단계(검토)는 원본 이슈가 없다. `source_issues.task_id` 는 첫 단계 그대로."""
    conn = cycle
    work_item_id = repo.work_item_of_task(conn, "task-gh-41")["work_item_id"]
    repo.insert_task(conn, _fix_task("task-gh-41-retry"), LATER, work_item_id=work_item_id)
    review = {**_fix_task("task-gh-41-review"), "kind": "code_review",
              "required_capability": {"code": "code.review", "scope": {"repository_id": "billing"}}}
    repo.insert_task(conn, review, LATER, work_item_id=work_item_id)
    first = repo.get_source_issue_by_task(conn, SESSION, "task-gh-41")
    assert first["task_id"] == "task-gh-41"
    assert repo.get_source_issue_by_task(conn, SESSION, "task-gh-41-retry")["github_issue_id"] == first["github_issue_id"]
    assert repo.get_source_issue_by_task(conn, SESSION, "task-gh-41-review") is None
    assert repo.get_source_issue_by_task(conn, OTHER_SESSION, "task-gh-41-retry") is None


# --- phase 15 step 3: 계정·로그인 세션·초대·재설정·역할 ------------------------------------------------------

PW_HASH = "scrypt$16$8$1$c2FsdHNhbHRzYWx0c2FsdA==$aGFzaGhhc2hoYXNoaGFzaA=="
PW_HASH_2 = "scrypt$16$8$1$b3RoZXJvdGhlcm90aGVyMQ==$bmV3aGFzaG5ld2hhc2huZXc="


def _plus(seconds: int, base: str = NOW) -> str:
    return repo._plus_seconds(base, seconds)


def _admin_with_account(conn, email="admin@example.com") -> str:
    admin = repo.ensure_first_admin(conn, SESSION, now=NOW)
    repo.set_member_credentials(conn, SESSION, admin, email=email, password_hash=PW_HASH, display_name="김관리",
                                now=NOW)
    return admin


def _dump(conn) -> str:
    """모든 표의 모든 행을 한 문자열로 — 원문 토큰이 어디에도 없음을 확인한다."""
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")]
    return "\n".join(repr(tuple(row)) for t in tables for row in conn.execute(f"SELECT * FROM {t}"))


def test_needs_first_setup_until_an_active_member_has_email_and_password(conn):
    assert repo.needs_first_setup(conn, SESSION)  # 워크스페이스 행 없음
    repo.create_session(conn, SESSION, NOW)
    assert repo.needs_first_setup(conn, SESSION)  # 첫 관리자 행은 있으나 계정 없음
    admin = _admin_with_account(conn)
    assert not repo.needs_first_setup(conn, SESSION)
    assert repo.needs_first_setup(conn, OTHER_SESSION)
    row = repo.get_member(conn, SESSION, admin)
    assert (row["email"], row["password_hash"], row["display_name"]) == ("admin@example.com", PW_HASH, "김관리")
    repo.add_member(conn, SESSION, display_name="김개발", role="admin", now=NOW)
    repo.disable_member(conn, SESSION, admin, now=LATER)
    assert repo.needs_first_setup(conn, SESSION)  # 비밀번호 있는 활성 멤버 0


def test_set_member_credentials_normalizes_email_and_keeps_display_name_when_none(sessions):
    admin = repo.ensure_first_admin(sessions, SESSION, now=NOW)
    repo.set_member_credentials(sessions, SESSION, admin, email="  Admin@Example.COM ", password_hash=PW_HASH,
                                now=NOW)
    row = repo.get_member(sessions, SESSION, admin)
    assert (row["email"], row["display_name"]) == ("admin@example.com", "관리자")
    assert repo.find_member_by_email(sessions, SESSION, "ADMIN@example.com ")["member_id"] == admin
    assert repo.find_member_by_email(sessions, SESSION, "none@example.com") is None
    assert repo.find_member_by_email(sessions, SESSION, "not an email") is None
    assert repo.find_member_by_email(sessions, OTHER_SESSION, "admin@example.com") is None
    assert repo.get_member(sessions, OTHER_SESSION, admin) is None
    with pytest.raises(ValueError):
        repo.set_member_credentials(sessions, SESSION, admin, email="bad", password_hash=PW_HASH, now=NOW)
    with pytest.raises(NotFound):
        repo.set_member_credentials(sessions, OTHER_SESSION, admin, email="x@example.com", password_hash=PW_HASH,
                                    now=NOW)


def test_duplicate_email_raises_email_taken_and_keeps_rows(sessions):
    admin = _admin_with_account(sessions)
    other = repo.add_member(sessions, SESSION, display_name="김개발", now=NOW)
    with pytest.raises(EmailTaken):
        repo.set_member_credentials(sessions, SESSION, other, email="ADMIN@example.com", password_hash=PW_HASH_2,
                                    now=LATER)
    assert repo.get_member(sessions, SESSION, other)["email"] is None
    # 다른 워크스페이스는 같은 이메일을 쓸 수 있다
    other_admin = repo.ensure_first_admin(sessions, OTHER_SESSION, now=NOW)
    repo.set_member_credentials(sessions, OTHER_SESSION, other_admin, email="admin@example.com",
                                password_hash=PW_HASH, now=NOW)
    # 자기 이메일 그대로 비밀번호만 바꾸기는 된다
    repo.set_member_credentials(sessions, SESSION, admin, email="admin@example.com", password_hash=PW_HASH_2,
                                now=LATER)
    assert repo.get_member(sessions, SESSION, admin)["password_hash"] == PW_HASH_2


def test_list_members_includes_email_and_disabled(sessions):
    admin = _admin_with_account(sessions)
    member = repo.add_member(sessions, SESSION, display_name="김개발", now=LATER)
    repo.disable_member(sessions, SESSION, member, now=LATER)
    rows = repo.list_members(sessions, SESSION)
    assert [(r["member_id"], r["email"], r["disabled_at"]) for r in rows] == [
        (admin, "admin@example.com", None), (member, None, LATER)]


def test_login_session_create_lookup_and_token_only_as_sha256(sessions):
    admin = _admin_with_account(sessions)
    token = repo.create_login_session(sessions, SESSION, admin, now=NOW, days=14)
    assert len(token) >= 40
    row = repo.member_for_login_token(sessions, token, now=LATER)
    assert (row["member_id"], row["session_id"], row["role"], row["display_name"]) == (
        admin, SESSION, "admin", "김관리")
    assert row["login_id"].startswith("lgn-") and len(row["login_id"]) == 16
    assert "password_hash" not in row.keys()
    (stored,) = sessions.execute("SELECT * FROM login_sessions").fetchall()
    assert stored["token_sha256"] == hashlib.sha256(token.encode()).hexdigest()
    assert stored["expires_at"] == _plus(14 * 86400)
    assert token not in _dump(sessions)
    assert repo.member_for_login_token(sessions, "wrong-token", now=LATER) is None
    assert repo.member_for_login_token(sessions, token, now=_plus(14 * 86400)) is None  # 만료
    assert repo.member_for_login_token(sessions, token, now=_plus(14 * 86400 - 1)) is not None


def test_login_session_touch_only_after_five_minutes(sessions):
    admin = _admin_with_account(sessions)
    token = repo.create_login_session(sessions, SESSION, admin, now=NOW, days=14)

    def seen():
        return sessions.execute("SELECT last_seen_at FROM login_sessions").fetchone()[0]

    assert seen() == NOW
    repo.member_for_login_token(sessions, token, now=_plus(repo.LOGIN_TOUCH_SECONDS))
    assert seen() == NOW
    repo.member_for_login_token(sessions, token, now=_plus(repo.LOGIN_TOUCH_SECONDS + 1))
    assert seen() == _plus(repo.LOGIN_TOUCH_SECONDS + 1)


def test_login_session_revoke_one_and_all_and_disabled_member(sessions):
    admin = _admin_with_account(sessions)
    first = repo.create_login_session(sessions, SESSION, admin, now=NOW, days=14)
    second = repo.create_login_session(sessions, SESSION, admin, now=NOW, days=14)
    assert repo.revoke_login_session(sessions, first, now=LATER)
    assert not repo.revoke_login_session(sessions, first, now=LATER)  # 이미 폐기
    assert not repo.revoke_login_session(sessions, "nope", now=LATER)
    assert repo.member_for_login_token(sessions, first, now=LATER) is None
    assert repo.member_for_login_token(sessions, second, now=LATER) is not None
    third = repo.create_login_session(sessions, SESSION, admin, now=NOW, days=14)
    assert repo.revoke_login_sessions(sessions, admin, now=LATER) == 2
    assert repo.member_for_login_token(sessions, second, now=LATER) is None
    assert repo.member_for_login_token(sessions, third, now=LATER) is None

    member = repo.add_member(sessions, SESSION, display_name="김개발", now=NOW)
    repo.set_member_credentials(sessions, SESSION, member, email="dev@example.com", password_hash=PW_HASH, now=NOW)
    token = repo.create_login_session(sessions, SESSION, member, now=NOW, days=14)
    sessions.execute("UPDATE members SET disabled_at = ? WHERE member_id = ?", (LATER, member))
    assert repo.member_for_login_token(sessions, token, now=LATER) is None  # 비활성 멤버


def test_set_member_credentials_revokes_all_login_sessions(sessions):
    admin = _admin_with_account(sessions)
    token = repo.create_login_session(sessions, SESSION, admin, now=NOW, days=14)
    repo.set_member_credentials(sessions, SESSION, admin, email="admin@example.com", password_hash=PW_HASH_2,
                                now=LATER)
    assert repo.member_for_login_token(sessions, token, now=LATER) is None


def test_set_member_display_name(sessions):
    admin = _admin_with_account(sessions)
    repo.set_member_display_name(sessions, SESSION, admin, "이관리", now=LATER)
    assert repo.get_member(sessions, SESSION, admin)["display_name"] == "이관리"
    with pytest.raises(NotFound):
        repo.set_member_display_name(sessions, OTHER_SESSION, admin, "x", now=LATER)


def test_invite_accept_creates_member_with_role_once(sessions):
    admin = _admin_with_account(sessions)
    invite_id, token = repo.issue_invite(sessions, SESSION, role="member", created_by_member_id=admin, now=NOW)
    assert invite_id.startswith("inv-") and len(invite_id) == 16
    assert token not in _dump(sessions)
    shown = repo.invite_for_token(sessions, token, purpose="invite", now=LATER)
    assert (shown["invite_id"], shown["role"], shown["session_id"]) == (invite_id, "member", SESSION)
    assert repo.invite_for_token(sessions, token, purpose="reset", now=LATER) is None
    (listed,) = repo.list_open_invites(sessions, SESSION, now=LATER)
    assert (listed["invite_id"], listed["role"], listed["expires_at"]) == (
        invite_id, "member", _plus(repo.INVITE_TTL_DAYS * 86400))
    assert "token_sha256" not in listed.keys()

    member = repo.accept_invite(sessions, token, email="Dev@Example.com", display_name="김개발",
                                password_hash=PW_HASH, now=LATER)
    row = repo.get_member(sessions, SESSION, member)
    assert (row["email"], row["display_name"], row["role"], row["password_hash"], row["disabled_at"]) == (
        "dev@example.com", "김개발", "member", PW_HASH, None)
    used = sessions.execute("SELECT used_at, used_by_member_id FROM member_invites").fetchone()
    assert tuple(used) == (LATER, member)
    assert repo.list_open_invites(sessions, SESSION, now=LATER) == []
    assert repo.invite_for_token(sessions, token, purpose="invite", now=LATER) is None
    with pytest.raises(NotFound):  # 두 번째 수락
        repo.accept_invite(sessions, token, email="dev2@example.com", display_name="박개발",
                           password_hash=PW_HASH, now=LATER)
    assert len(repo.list_members(sessions, SESSION)) == 2


def test_invite_admin_role_is_applied(sessions):
    admin = _admin_with_account(sessions)
    _, token = repo.issue_invite(sessions, SESSION, role="admin", created_by_member_id=admin, now=NOW)
    member = repo.accept_invite(sessions, token, email="boss@example.com", display_name="박관리",
                                password_hash=PW_HASH, now=LATER)
    assert repo.get_member(sessions, SESSION, member)["role"] == "admin"


def test_invite_expired_revoked_or_unknown_is_not_found(sessions):
    admin = _admin_with_account(sessions)
    _, expired = repo.issue_invite(sessions, SESSION, role="member", created_by_member_id=admin, now=NOW)
    end = _plus(repo.INVITE_TTL_DAYS * 86400)
    assert repo.invite_for_token(sessions, expired, purpose="invite", now=end) is None
    assert repo.list_open_invites(sessions, SESSION, now=end) == []
    with pytest.raises(NotFound):
        repo.accept_invite(sessions, expired, email="a@example.com", display_name="가", password_hash=PW_HASH,
                           now=end)
    revoked_id, revoked = repo.issue_invite(sessions, SESSION, role="member", created_by_member_id=admin, now=NOW)
    repo.revoke_invite(sessions, SESSION, revoked_id, now=LATER)
    with pytest.raises(NotFound):
        repo.revoke_invite(sessions, SESSION, revoked_id, now=LATER)  # 이미 취소
    with pytest.raises(NotFound):
        repo.accept_invite(sessions, revoked, email="b@example.com", display_name="나", password_hash=PW_HASH,
                           now=LATER)
    with pytest.raises(NotFound):
        repo.accept_invite(sessions, "unknown", email="c@example.com", display_name="다", password_hash=PW_HASH,
                           now=LATER)
    other_id, _ = repo.issue_invite(sessions, SESSION, role="member", created_by_member_id=admin, now=NOW)
    with pytest.raises(NotFound):
        repo.revoke_invite(sessions, OTHER_SESSION, other_id, now=LATER)  # 다른 워크스페이스
    assert len(repo.list_members(sessions, SESSION)) == 1


def test_invite_with_taken_email_raises_and_stays_usable(sessions):
    admin = _admin_with_account(sessions)
    _, token = repo.issue_invite(sessions, SESSION, role="member", created_by_member_id=admin, now=NOW)
    with pytest.raises(EmailTaken):
        repo.accept_invite(sessions, token, email="admin@example.com", display_name="김개발",
                           password_hash=PW_HASH, now=LATER)
    assert len(repo.list_members(sessions, SESSION)) == 1
    assert repo.invite_for_token(sessions, token, purpose="invite", now=LATER) is not None
    repo.accept_invite(sessions, token, email="dev@example.com", display_name="김개발", password_hash=PW_HASH,
                       now=LATER)


def test_invite_second_accept_from_another_connection_fails(sessions, db_path):
    """두 연결이 같은 토큰으로 수락 — 한 번만 성공한다."""
    admin = _admin_with_account(sessions)
    _, token = repo.issue_invite(sessions, SESSION, role="member", created_by_member_id=admin, now=NOW)
    results = []

    def accept(i):
        c = connect(db_path)
        try:
            results.append(repo.accept_invite(c, token, email=f"dev{i}@example.com", display_name=f"개발{i}",
                                              password_hash=PW_HASH, now=LATER))
        except NotFound:
            results.append(None)
        finally:
            c.close()

    threads = [threading.Thread(target=accept, args=(i,)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len([r for r in results if r]) == 1
    assert len(repo.list_members(sessions, SESSION)) == 2


def test_reset_link_sets_password_revokes_sessions_and_earlier_links(sessions):
    admin = _admin_with_account(sessions)
    member = repo.add_member(sessions, SESSION, display_name="김개발", now=NOW)
    repo.set_member_credentials(sessions, SESSION, member, email="dev@example.com", password_hash=PW_HASH, now=NOW)
    login = repo.create_login_session(sessions, SESSION, member, now=NOW, days=14)
    _, first = repo.issue_reset_link(sessions, SESSION, member, created_by_member_id=admin, now=NOW)
    reset_id, second = repo.issue_reset_link(sessions, SESSION, member, created_by_member_id=admin, now=LATER)
    assert reset_id.startswith("inv-")
    dump = _dump(sessions)
    assert first not in dump and second not in dump
    assert repo.invite_for_token(sessions, first, purpose="reset", now=LATER) is None  # 이전 링크 취소
    assert repo.invite_for_token(sessions, second, purpose="reset", now=LATER)["member_id"] == member
    assert repo.invite_for_token(sessions, second, purpose="invite", now=LATER) is None
    assert repo.list_open_invites(sessions, SESSION, now=LATER) == []  # 재설정 링크는 초대 목록에 없다
    with pytest.raises(NotFound):
        repo.use_reset_link(sessions, first, password_hash=PW_HASH_2, now=LATER)

    assert repo.use_reset_link(sessions, second, password_hash=PW_HASH_2, now=LATER) == member
    assert repo.get_member(sessions, SESSION, member)["password_hash"] == PW_HASH_2
    assert repo.member_for_login_token(sessions, login, now=LATER) is None
    with pytest.raises(NotFound):  # 1회용
        repo.use_reset_link(sessions, second, password_hash=PW_HASH, now=LATER)
    assert repo.get_member(sessions, SESSION, member)["password_hash"] == PW_HASH_2


def test_reset_link_expiry_disabled_member_and_other_session(sessions):
    admin = _admin_with_account(sessions)
    member = repo.add_member(sessions, SESSION, display_name="김개발", now=NOW)
    repo.set_member_credentials(sessions, SESSION, member, email="dev@example.com", password_hash=PW_HASH, now=NOW)
    _, token = repo.issue_reset_link(sessions, SESSION, member, created_by_member_id=admin, now=NOW)
    with pytest.raises(NotFound):
        repo.use_reset_link(sessions, token, password_hash=PW_HASH_2, now=_plus(repo.RESET_TTL_HOURS * 3600))
    _, token = repo.issue_reset_link(sessions, SESSION, member, created_by_member_id=admin, now=NOW)
    repo.disable_member(sessions, SESSION, member, now=LATER)
    assert repo.invite_for_token(sessions, token, purpose="reset", now=LATER) is None
    with pytest.raises(NotFound):
        repo.use_reset_link(sessions, token, password_hash=PW_HASH_2, now=LATER)
    with pytest.raises(NotFound):
        repo.issue_reset_link(sessions, OTHER_SESSION, member, created_by_member_id=None, now=NOW)


def test_last_active_admin_cannot_be_demoted_or_disabled(sessions):
    admin = _admin_with_account(sessions)
    with pytest.raises(LastAdmin):
        repo.set_member_role(sessions, SESSION, admin, "member", now=LATER)
    with pytest.raises(LastAdmin):
        repo.disable_member(sessions, SESSION, admin, now=LATER)
    row = repo.get_member(sessions, SESSION, admin)
    assert (row["role"], row["disabled_at"]) == ("admin", None)

    second = repo.add_member(sessions, SESSION, display_name="박관리", now=NOW)
    repo.set_member_role(sessions, SESSION, second, "admin", now=LATER)
    repo.disable_member(sessions, SESSION, second, now=LATER)
    with pytest.raises(LastAdmin):  # 비활성 관리자는 세지 않는다
        repo.set_member_role(sessions, SESSION, admin, "member", now=LATER)
    repo.enable_member(sessions, SESSION, second, now=LATER)
    repo.enable_member(sessions, SESSION, second, now=LATER)  # 멱등
    repo.set_member_role(sessions, SESSION, admin, "member", now=LATER)  # 자기 자신도 같은 규칙
    assert repo.get_member(sessions, SESSION, admin)["role"] == "member"
    with pytest.raises(LastAdmin):
        repo.disable_member(sessions, SESSION, second, now=LATER)
    with pytest.raises(ValueError):
        repo.set_member_role(sessions, SESSION, admin, "owner", now=LATER)
    with pytest.raises(NotFound):
        repo.set_member_role(sessions, OTHER_SESSION, admin, "admin", now=LATER)


def test_disable_member_revokes_sessions_and_is_idempotent(sessions):
    _admin_with_account(sessions)
    member = repo.add_member(sessions, SESSION, display_name="김개발", now=NOW)
    repo.set_member_credentials(sessions, SESSION, member, email="dev@example.com", password_hash=PW_HASH, now=NOW)
    token = repo.create_login_session(sessions, SESSION, member, now=NOW, days=14)
    repo.disable_member(sessions, SESSION, member, now=LATER)
    repo.disable_member(sessions, SESSION, member, now=_plus(60))
    assert repo.get_member(sessions, SESSION, member)["disabled_at"] == LATER
    assert repo.member_for_login_token(sessions, token, now=LATER) is None
    revoked = sessions.execute("SELECT revoked_at FROM login_sessions").fetchone()[0]
    assert revoked == LATER
    repo.enable_member(sessions, SESSION, member, now=_plus(60))
    assert repo.get_member(sessions, SESSION, member)["disabled_at"] is None
    assert repo.member_for_login_token(sessions, token, now=_plus(60)) is None  # 폐기된 세션은 되살리지 않는다
    with pytest.raises(NotFound):
        repo.disable_member(sessions, OTHER_SESSION, member, now=LATER)
    with pytest.raises(NotFound):
        repo.enable_member(sessions, OTHER_SESSION, member, now=LATER)


def test_member_last_seen_is_latest_login_session_per_member(sessions):
    admin = _admin_with_account(sessions)
    member = repo.add_member(sessions, SESSION, display_name="김개발", now=NOW)
    repo.create_login_session(sessions, SESSION, admin, now=NOW, days=14)
    token = repo.create_login_session(sessions, SESSION, admin, now=_plus(60), days=14)
    repo.revoke_login_session(sessions, token, now=_plus(120))  # 폐기된 세션도 접속 기록이다
    assert repo.member_last_seen(sessions, SESSION) == {admin: _plus(60)}
    assert member not in repo.member_last_seen(sessions, SESSION)


# --- phase 15 step 8: 맡긴 사람·응답자·사람별 내 차례 ------------------------------------------------------


def _people(conn) -> tuple[str, str, str]:
    """(첫 관리자 B, 멤버 A, 멤버 C) — B 는 세션을 만들 때 생긴 관리자."""
    admin = repo.ensure_first_admin(conn, SESSION, now=NOW)
    a = repo.add_member(conn, SESSION, display_name="김맡김", now=NOW)
    c = repo.add_member(conn, SESSION, display_name="이담당", now=NOW)
    return admin, a, c


def test_turn_recipients_of_is_assignee_then_requester_then_admins(seeded):
    conn = seeded
    admin, a, c = _people(conn)
    second_admin = repo.add_member(conn, SESSION, display_name="박관리", role="admin", now=LATER)
    work = _work(conn)["work_item_id"]
    assert repo.turn_recipients_of(conn, work) == (admin, second_admin)  # 맡긴 사람 없음 → 활성 관리자 전원
    repo.set_work_requester(conn, work, a)
    assert _work(conn)["requested_by_member_id"] == a
    assert repo.turn_recipients_of(conn, work) == (a,)
    repo.assign_work_item(conn, SESSION, work, assignee_type="member", assignee_id=c, now=LATER)
    assert repo.turn_recipients_of(conn, work) == (c,)  # 담당 멤버가 먼저
    repo.disable_member(conn, SESSION, c, now=LATER)
    assert repo.turn_recipients_of(conn, work) == (a,)
    repo.disable_member(conn, SESSION, a, now=LATER)
    assert repo.turn_recipients_of(conn, work) == (admin, second_admin)  # 맡긴 사람이 비활성 → 관리자 전원
    with pytest.raises(NotFound):
        repo.turn_recipients_of(conn, "wi-nope")


def test_list_work_items_my_turn_is_computed_per_member(seeded):
    conn = seeded
    admin, a, _ = _people(conn)
    other = repo.insert_work_item_task(conn, _task("task-other"), NOW)  # 내 차례 아님
    repo.set_work_requester(conn, other, a)
    _failed_stage(conn)  # TASK_A 업무 → 내 차례, 맡긴 사람 없음
    work = _work(conn)["work_item_id"]
    assert [r["work_item_id"] for r in repo.list_work_items(conn, SESSION, recipient_member_id=admin)] == [work]
    assert repo.list_work_items(conn, SESSION, recipient_member_id=a) == []
    repo.set_work_requester(conn, work, a)
    assert [r["work_item_id"] for r in repo.list_work_items(conn, SESSION, recipient_member_id=a)] == [work]
    assert repo.list_work_items(conn, SESSION, recipient_member_id=admin) == []  # 관리자에게는 안 보인다
    assert conn.execute("SELECT COUNT(*) FROM work_items WHERE requested_by_member_id IS NOT NULL").fetchone()[0] == 2


def test_insert_work_item_task_records_requester(seeded):
    conn = seeded
    _, a, _ = _people(conn)
    work = repo.insert_work_item_task(conn, _task("task-direct"), NOW, requested_by_member_id=a)
    assert repo.get_work_item(conn, SESSION, work)["requested_by_member_id"] == a
    assert _work(conn)["requested_by_member_id"] is None  # 넘기지 않으면 기록 없음


def test_mark_issue_delegated_records_the_latest_member_but_not_the_label(cycle):
    conn = cycle
    admin, a, _ = _people(conn)
    issue = repo.get_source_issue_by_task(conn, SESSION, "task-gh-41")
    kwargs = {"session_id": SESSION, "source_id": SOURCE, "github_issue_id": issue["github_issue_id"]}
    repo.mark_issue_delegated(conn, **kwargs, by="label", now=NOW)
    assert _work(conn, "task-gh-41")["requested_by_member_id"] is None
    assert repo.mark_issue_delegated(conn, **kwargs, by="operator", now=LATER, member_id=a) is False
    assert _work(conn, "task-gh-41")["requested_by_member_id"] == a  # 이미 지시됐어도 누른 사람은 남는다
    repo.mark_issue_delegated(conn, **kwargs, by="operator", now=LATER, member_id=admin)
    assert _work(conn, "task-gh-41")["requested_by_member_id"] == admin  # 가장 최근이 이긴다


def test_response_records_the_responder_and_retry_sets_the_requester(seeded):
    conn = seeded
    admin, a, _ = _people(conn)
    request_id = _failed_stage(conn)
    kwargs = {"response_id": "resp-1", "expected_revision": 1, "action": "retry", "text": "", "now": LATER,
              "retry_task_id": "task-retry"}
    repo.record_human_response_once(conn, SESSION, request_id, **kwargs, member_id=a)
    (response,) = repo.list_human_responses(conn, TASK_A)
    assert response["member_id"] == a
    assert _work(conn)["requested_by_member_id"] == a  # 다시 맡기기 = 맡긴 사람
    # 같은 response_id 재전송은 처음 결과 — 다른 멤버가 보내도 응답자·맡긴 사람은 그대로
    assert repo.record_human_response_once(conn, SESSION, request_id, **kwargs, member_id=admin) == (2, False)
    assert repo.list_human_responses(conn, TASK_A)[0]["member_id"] == a
    assert _work(conn)["requested_by_member_id"] == a


def test_non_retry_response_keeps_the_requester(cycle):
    conn = cycle
    admin, a, _ = _people(conn)
    repo.set_work_requester(conn, _wi41(conn), a)
    request_id, _ = repo.create_human_request_once(conn, "task-gh-41", "decision", "q", "decision:1", NOW)
    repo.record_human_response_once(conn, SESSION, request_id, response_id="resp-1", expected_revision=1,
                                    action="resume", text="", now=LATER, member_id=admin)
    assert repo.list_human_responses(conn, "task-gh-41")[0]["member_id"] == admin
    assert _work(conn, "task-gh-41")["requested_by_member_id"] == a


def test_new_work_followup_copies_the_requester(cycle):
    conn = cycle
    _, a, _ = _people(conn)
    repo.set_work_requester(conn, _wi41(conn), a)
    _create_execution(conn, "exec-fix-1", "task-gh-41", kind="code_change", inputs=("art-x",))
    spec = FollowupTaskSpec(session_id=SESSION, kind="code_review", cause_execution_id="exec-fix-1",
                            predecessor_task_id=None, rules_revision=1)
    task_id, _ = repo.create_followup_once(conn, spec, {**_review_task(), "predecessor_task_id": None}, NOW,
                                           work_item_id=_wi41(conn), placement="new_work")
    spawned = repo.work_item_of_task(conn, task_id)
    assert spawned["work_item_id"] != _wi41(conn) and spawned["requested_by_member_id"] == a


# --- phase 16 step 2: 목록 행 조회 ----------------------------------------------------------------------------


def _row_of(rows, work_item_id):
    return next(r for r in rows if r.work_item_id == work_item_id)


def test_list_work_rows_keeps_recent_closed_work_unless_all(sessions):
    conn = sessions
    open_work, _ = _new_work(conn, title="열림")
    recent, _ = _new_work(conn, title="최근 끝남")
    old, _ = _new_work(conn, title="오래전 끝남")
    _new_work(conn, OTHER_SESSION, title="다른 워크스페이스")
    repo.set_work_status(conn, recent, WorkStatus("완료", "PR 병합 — #3"), now="2026-09-25T00:00:00Z")
    repo.set_work_status(conn, old, WorkStatus("종료", "닫힘"), now="2026-09-01T00:00:00Z")
    since = "2026-09-16T00:00:00Z"
    assert [r.work_item_id for r in repo.list_work_rows(conn, SESSION, closed_since=since)] == [recent, open_work]
    assert [r.work_item_id for r in repo.list_work_rows(conn, SESSION, closed_since=None)] == [old, recent, open_work]
    assert [r.title for r in repo.list_work_rows(conn, OTHER_SESSION, closed_since=None)] == ["다른 워크스페이스"]


def test_list_work_rows_fills_names_recipients_and_next_action(seeded):
    conn = seeded
    admin, a, c = _people(conn)
    _failed_stage(conn)  # TASK_A 업무 → 내 차례, 에이전트 담당
    failed = _work(conn)["work_item_id"]
    member_work, _ = _new_work(conn, title="멤버 담당", priority="high", source_key="OPS-7",
                               source_url="https://example.com/OPS-7")
    gone_work, _ = _new_work(conn, title="비활성 담당")
    direct_work, direct_key = _new_work(conn, title="직접 작업")
    repo.assign_work_item(conn, SESSION, member_work, assignee_type="member", assignee_id=a, now=LATER)
    repo.assign_work_item(conn, SESSION, gone_work, assignee_type="member", assignee_id=c, now=LATER)
    repo.disable_member(conn, SESSION, c, now=LATER)
    conn.execute("UPDATE work_items SET direct_member_id = ?, direct_started_at = ?, direct_branch = ?"
                 " WHERE work_item_id = ?", (admin, LATER, f"RUN-{direct_key}", direct_work))
    rows = repo.list_work_rows(conn, SESSION, closed_since=None)

    row = _row_of(rows, failed)
    assert (row.work_key, row.kind, row.kind_label, row.status) == ("RUN-1", "diagnosis", "운영 진단", "내 차례")
    assert (row.assignee_type, row.assignee_id, row.assignee_name, row.assignee_active) == (
        "agent", "agent-ops-demo", "agent-ops-demo", True)  # 등록되지 않은 Agent 는 id
    assert row.recipients == (admin,)
    assert row.next_action == "실행 실패 — timeout: 20분 초과"

    row = _row_of(rows, member_work)
    assert (row.assignee_name, row.assignee_active, row.priority, row.source_key, row.source_url) == (
        "김맡김", True, "high", "OPS-7", "https://example.com/OPS-7")
    assert row.recipients == ()  # 내 차례가 아니면 받는 사람을 싣지 않는다
    assert row.next_action == row.status_reason
    assert (_row_of(rows, gone_work).assignee_name, _row_of(rows, gone_work).assignee_active) == ("이담당", False)
    assert _row_of(rows, direct_work).next_action == "직접 작업 중 · 관리자"


def test_list_work_rows_uses_the_names_of_registered_agents(seeded):
    conn = seeded
    repo.upsert_agent(conn, _agent(name="운영 에이전트"))
    _create_execution(conn, "exec-1")
    assert _row_of(repo.list_work_rows(conn, SESSION, closed_since=None), _work(conn)["work_item_id"]).assignee_name \
        == "운영 에이전트"


def test_list_work_rows_shows_runloom_and_detected_pull_requests(cycle):
    conn = cycle
    work = _wi41(conn)
    _fix_execution(conn)
    _enqueue(conn)
    assert _row_of(repo.list_work_rows(conn, SESSION, closed_since=None), work).next_action == "PR 여는 중"

    detected, _ = _new_work(conn, title="사람이 연 PR")

    def detected_pr(number: int, state: str, updated: str) -> None:
        conn.execute(
            "INSERT INTO work_pull_requests (session_id, work_item_id, source_id, repository_full_name, pr_number,"
            " title, pr_url, head_branch, state, draft, merged_at, pr_updated_at, created_at, updated_at)"
            " VALUES (?, ?, ?, 'acme/billing', ?, 't', 'u', 'RUN-2-x', ?, 0, ?, ?, ?, ?)",
            (SESSION, detected, SOURCE, number, state, updated if state == "merged" else None, updated, NOW, NOW),
        )

    detected_pr(5, "closed", "2026-09-22T00:00:00Z")
    assert _row_of(repo.list_work_rows(conn, SESSION, closed_since=None), detected).next_action == ""  # 닫힘은 무시
    detected_pr(6, "merged", "2026-09-21T00:00:00Z")
    assert _row_of(repo.list_work_rows(conn, SESSION, closed_since=None), detected).next_action == "PR #6"
    detected_pr(7, "open", "2026-09-20T00:00:00Z")
    detected_pr(8, "open", "2026-09-20T10:00:00Z")
    assert _row_of(repo.list_work_rows(conn, SESSION, closed_since=None), detected).next_action == "PR #8"  # 열린 것 먼저


def test_list_work_pull_requests_is_recent_first_per_work(cycle):
    conn = cycle
    work, _ = _new_work(conn, title="사람이 연 PR")
    other, _ = _new_work(conn, title="다른 업무")
    for work_item_id, number, updated in ((work, 3, "2026-09-20T00:00:00Z"), (work, 4, "2026-09-21T00:00:00Z"),
                                          (other, 5, "2026-09-22T00:00:00Z")):
        conn.execute(
            "INSERT INTO work_pull_requests (session_id, work_item_id, source_id, repository_full_name, pr_number,"
            " title, pr_url, head_branch, state, draft, merged_at, pr_updated_at, created_at, updated_at)"
            " VALUES (?, ?, ?, 'acme/billing', ?, 't', 'u', 'RUN-2-x', 'open', 0, NULL, ?, ?, ?)",
            (SESSION, work_item_id, SOURCE, number, updated, NOW, NOW),
        )
    assert [r["pr_number"] for r in repo.list_work_pull_requests(conn, work)] == [4, 3]
    assert repo.list_work_pull_requests(conn, "wi-none") == []


def _detected(conn, work_item_id: str, *, number: int = 7, state: str = "open", title: str = "RUN-1 고침",
              merged_at: str | None = None, updated: str = "2026-10-06T03:00:00Z", now: str = NOW) -> bool:
    return repo.upsert_work_pull_request(
        conn, session_id=SESSION, work_item_id=work_item_id, source_id=SOURCE, repository_full_name="acme/billing",
        pr_number=number, title=title, head_branch="RUN-1-fix", state=state, draft=False, author_login="kim-dev",
        merged_at=merged_at, pr_updated_at=updated, matched_in="head", now=now,
    )


def test_upsert_work_pull_request_links_once_updates_and_refreshes_work_status(cycle):
    conn = cycle
    work = repo.work_item_of_task(conn, "task-gh-41")["work_item_id"]
    other, _ = _new_work(conn, title="다른 업무")

    assert _detected(conn, work) is True
    (row,) = repo.list_work_pull_requests(conn, work)
    assert (row["pr_url"], row["state"], row["head_branch"], row["author_login"]) == (
        "https://github.com/acme/billing/pull/7", "open", "RUN-1-fix", "kim-dev")
    assert _events_of(conn, work, "pull_request_linked") == [
        {"repository_full_name": "acme/billing", "pr_number": 7, "head_branch": "RUN-1-fix", "matched_in": "head"}]
    status = repo.get_work_item(conn, SESSION, work)
    assert (status["status"], status["status_reason"]) == ("PR · 검토", "PR 확인 — #7")
    assert repo.get_work_pull_request(conn, SESSION, "acme/billing", 7)["work_item_id"] == work
    assert repo.get_work_pull_request(conn, SESSION, "acme/billing", 8) is None

    # 다시 부르면 갱신만 — 다른 업무를 넘겨도 처음 붙은 업무 그대로, 이벤트 없음
    assert _detected(conn, other, title="새 제목", state="merged", merged_at="2026-10-06T04:00:00Z",
                     updated="2026-10-06T04:00:00Z", now=LATER) is False
    assert repo.list_work_pull_requests(conn, other) == []
    (row,) = repo.list_work_pull_requests(conn, work)
    assert (row["title"], row["state"], row["merged_at"], row["pr_updated_at"]) == (
        "새 제목", "merged", "2026-10-06T04:00:00Z", "2026-10-06T04:00:00Z")
    assert len(_events_of(conn, work, "pull_request_linked")) == 1
    done = repo.get_work_item(conn, SESSION, work)
    assert (done["status"], done["status_reason"], done["closed_at"]) == ("완료", "PR 병합 — #7", LATER)

    # 병합은 되돌아가지 않고, 끝난 업무의 상태 기록은 한 번뿐
    assert _detected(conn, work, state="closed", updated="2026-10-06T05:00:00Z") is False
    (row,) = repo.list_work_pull_requests(conn, work)
    assert (row["state"], row["merged_at"], row["pr_updated_at"]) == ("merged", "2026-10-06T04:00:00Z",
                                                                      "2026-10-06T05:00:00Z")
    assert [e["to"] for e in _events_of(conn, work, "status_changed")].count("완료") == 1


def test_is_runloom_pull_request_by_number_or_queued_head(cycle):
    conn = cycle
    _fix_execution(conn)
    _enqueue(conn)  # 번호 기록 전 대기열 행 — head 로 알아본다
    assert repo.is_runloom_pull_request(conn, SESSION, "acme/billing", pr_number=31, head_branch="task/task-gh-41")
    assert not repo.is_runloom_pull_request(conn, SESSION, "acme/billing", pr_number=31, head_branch="RUN-1-fix")
    repo.record_pull_request(conn, "task-gh-41", state="open", pr=_pr(31), now=NOW)
    assert repo.is_runloom_pull_request(conn, SESSION, "acme/billing", pr_number=31, head_branch="RUN-1-fix")
    assert not repo.is_runloom_pull_request(conn, SESSION, "acme/other", pr_number=31, head_branch="task/task-gh-41")
    assert not repo.is_runloom_pull_request(conn, OTHER_SESSION, "acme/billing", pr_number=31,
                                            head_branch="task/task-gh-41")


def test_pull_cursor_is_separate_from_issue_cursor(cycle):
    conn = cycle
    assert repo.get_pull_cursor(conn, SOURCE) is None
    repo.set_pull_cursor(conn, SOURCE, "2026-10-06T04:00:00Z", now=NOW)
    assert repo.get_pull_cursor(conn, SOURCE) == "2026-10-06T04:00:00Z"
    assert repo.get_source_cursor(conn, SESSION, SOURCE) is None
    with pytest.raises(NotFound):
        repo.set_pull_cursor(conn, "ghs-none", "2026-10-06T04:00:00Z", now=NOW)


def test_list_work_rows_query_count_does_not_grow_with_work_items(seeded):
    conn = seeded
    admin, a, _ = _people(conn)
    repo.upsert_agent(conn, _agent(name="운영 에이전트"))

    def count() -> int:
        statements: list[str] = []
        conn.set_trace_callback(statements.append)
        try:
            repo.list_work_rows(conn, SESSION, closed_since=None)
        finally:
            conn.set_trace_callback(None)
        return len(statements)

    def add(n: int) -> None:
        for i in range(n):
            work, _ = _new_work(conn, title=f"업무 {i}")
            repo.assign_work_item(conn, SESSION, work, assignee_type="member", assignee_id=(admin, a)[i % 2],
                                  now=LATER)

    _failed_stage(conn)
    add(2)
    few = count()
    add(28)
    assert len(repo.list_work_rows(conn, SESSION, closed_since=None)) == 31
    assert count() == few


# --- 시작하기 (phase 16 step 7) ---


def test_start_facts_empty_workspace_is_all_false(sessions):
    assert repo.start_facts(sessions, SESSION) == StartFacts(
        has_source=False, has_runner=False, invited=False, delegated=False)


def test_start_facts_source_is_enabled_github_source_or_live_inbound_token(sessions):
    conn = sessions
    repo.save_github_source(conn, OTHER_SESSION, _source(source_id="ghs-99999999"), NOW)  # 다른 워크스페이스는 세지 않는다
    token_id, _ = repo.issue_source_token(conn, SESSION, "n8n", "", NOW)
    assert repo.start_facts(conn, SESSION).has_source
    repo.revoke_source_token(conn, SESSION, token_id, LATER)
    assert not repo.start_facts(conn, SESSION).has_source
    repo.save_github_source(conn, SESSION, _source(enabled=False), NOW)
    assert not repo.start_facts(conn, SESSION).has_source
    repo.save_github_source(conn, SESSION, _source(), LATER)
    assert repo.start_facts(conn, SESSION).has_source


def test_start_facts_runner_is_unrevoked_connector(sessions):
    conn = sessions
    connector_id, _ = repo.exchange_connect_code(conn, repo.issue_connect_code(conn, NOW), NOW)
    assert repo.start_facts(conn, SESSION).has_runner
    repo.revoke_connector(conn, connector_id, LATER)
    assert not repo.start_facts(conn, SESSION).has_runner


def test_start_facts_invited_is_two_active_members_or_issued_invite(sessions):
    conn = sessions
    admin = repo.ensure_first_admin(conn, SESSION, now=NOW)  # 세션을 만들 때 생긴 첫 관리자 1명
    repo.add_member(conn, OTHER_SESSION, display_name="남", now=NOW)
    repo.issue_invite(conn, OTHER_SESSION, role="member", created_by_member_id=None, now=NOW)
    repo.issue_reset_link(conn, SESSION, admin, created_by_member_id=None, now=NOW)  # 재설정 링크는 초대가 아니다
    assert not repo.start_facts(conn, SESSION).invited
    other = repo.add_member(conn, SESSION, display_name="멤버", now=NOW)
    assert repo.start_facts(conn, SESSION).invited
    repo.disable_member(conn, SESSION, other, now=LATER)
    assert not repo.start_facts(conn, SESSION).invited
    repo.issue_invite(conn, SESSION, role="member", created_by_member_id=admin, now=NOW)
    assert repo.start_facts(conn, SESSION).invited


def test_start_facts_delegated_is_work_with_any_execution(seeded):
    conn = seeded
    assert not repo.start_facts(conn, SESSION).delegated
    _create_execution(conn, "exec-1")
    assert repo.start_facts(conn, SESSION).delegated
    assert not repo.start_facts(conn, OTHER_SESSION).delegated


# --- phase 17 step 1: 맡기기 정책·승인 요청·지시 메모·검증만 다시 표시 (ADR-0023, 스키마 v13) ---------------------


@pytest.fixture
def handoff(cycle):
    """cycle + FIX_AGENT 가 SESSION 에 붙어 있고, 맡긴 사람(멤버)·관리자가 있다. (conn, work_item_id, admin, member)."""
    conn = cycle
    repo.register_session_agent(conn, SESSION, FIX_AGENT, NOW)
    admin = repo.ensure_first_admin(conn, SESSION, now=NOW)
    member = repo.add_member(conn, SESSION, display_name="김OO", now=NOW)
    return conn, _wi41(conn), admin, member


def _open_approval(conn, *, requester=None, agent_id=FIX_AGENT, task_id="task-gh-41", now=NOW):
    return repo.open_owner_approval(conn, task_id, agent_id=agent_id, requester_id=requester,
                                    question="김OO 가 맡김 · 이OO 승인 대기", now=now)


def test_delegation_policy_defaults_to_run_and_is_kept_by_upsert(handoff):
    conn, _, admin, _ = handoff
    assert repo.get_agent(conn, FIX_AGENT)["delegation_policy"] == "run"
    assert repo.set_delegation_policy(conn, SESSION, FIX_AGENT, "owner_approval", member_id=admin, now=LATER) is True
    assert repo.set_delegation_policy(conn, SESSION, FIX_AGENT, "owner_approval", member_id=admin, now=LATER) is False
    assert repo.get_agent(conn, FIX_AGENT)["delegation_policy"] == "owner_approval"
    # 러너 재등록(upsert)은 정책을 건드리지 않는다
    repo.upsert_agent(conn, _agent(FIX_AGENT, connection_type="local", api_url=None, credential_ref=None,
                                   capabilities=[{"code": "code.fix", "scope": {"repository_id": "billing"}}]))
    assert repo.get_agent(conn, FIX_AGENT)["delegation_policy"] == "owner_approval"


def test_set_delegation_policy_rejects_unknown_value_agent_and_workspace(handoff):
    conn, _, admin, _ = handoff
    with pytest.raises(ValueError):
        repo.set_delegation_policy(conn, SESSION, FIX_AGENT, "ask", member_id=admin, now=LATER)
    with pytest.raises(NotFound):
        repo.set_delegation_policy(conn, SESSION, "agent-nope", "owner_approval", member_id=admin, now=LATER)
    with pytest.raises(NotFound):  # 다른 워크스페이스에 붙지 않은 에이전트
        repo.set_delegation_policy(conn, OTHER_SESSION, FIX_AGENT, "owner_approval", member_id=admin, now=LATER)
    assert repo.get_agent(conn, FIX_AGENT)["delegation_policy"] == "run"
    assert not conn.in_transaction


def test_open_owner_approval_keeps_one_open_request_per_scope(handoff):
    conn, work_item_id, _, member = handoff
    first, created = _open_approval(conn, requester=member)
    assert created is True
    again, created = _open_approval(conn, requester=member, now=LATER)  # 같은 단계·에이전트·맡긴 사람
    assert (again, created) == (first, False)
    rows = repo.list_owner_approvals(conn, "task-gh-41")
    assert [(r["request_id"], r["code"], r["cause_key"], r["state"], r["action"]) for r in rows] == [
        (first, "owner_approval", f"owner_approval:{FIX_AGENT}:{member}:1", "open", None)]
    assert rows[0]["question"] == "김OO 가 맡김 · 이OO 승인 대기"
    # 다른 사람 요청은 승인 요청 목록에 없다
    repo.create_human_request_once(conn, "task-gh-41", "decision", "q", "decision:1", NOW)
    assert [r["request_id"] for r in repo.list_owner_approvals(conn, "task-gh-41")] == [first]
    assert repo.get_work_item(conn, SESSION, work_item_id)["status"] == "내 차례"  # 업무 상태 재계산


def test_owner_approval_seq_grows_after_the_previous_request_is_answered(handoff):
    conn, _, admin, member = handoff
    first, _ = _open_approval(conn, requester=member)
    _respond(conn, first, action="decline", text="오늘은 Mac 을 못 씁니다", member_id=admin)
    second, created = _open_approval(conn, requester=member, now=LATER)
    assert created is True and second != first
    rows = repo.list_owner_approvals(conn, "task-gh-41")
    assert [(r["cause_key"], r["state"], r["action"], r["responder_id"]) for r in rows] == [
        (f"owner_approval:{FIX_AGENT}:{member}:1", "answered", "decline", admin),
        (f"owner_approval:{FIX_AGENT}:{member}:2", "open", None, None),
    ]
    # 맡긴 사람이 없는 범위(자동 착수)는 따로 센다
    auto, created = _open_approval(conn, now=LATER)
    assert created is True
    assert repo.list_owner_approvals(conn, "task-gh-41")[-1]["cause_key"] == f"owner_approval:{FIX_AGENT}:none:1"
    assert auto not in (first, second)


def test_withdraw_owner_approvals_closes_open_requests_without_bumping_revision(handoff):
    conn, work_item_id, admin, member = handoff
    request_id, _ = _open_approval(conn, requester=member)
    other, _ = repo.create_human_request_once(conn, "task-gh-41", "decision", "q", "decision:1", NOW)
    revision = repo.get_task(conn, "task-gh-41")["revision"]
    with pytest.raises(ValueError):  # 범위 없이 전부 닫지 않는다
        repo.withdraw_owner_approvals(conn, reason="x", member_id=admin, now=LATER)
    conn.execute("BEGIN IMMEDIATE")
    assert repo.withdraw_owner_approvals(conn, work_item_id=work_item_id, reason="담당 바뀜", member_id=admin,
                                         now=LATER) == 1
    assert repo.withdraw_owner_approvals(conn, work_item_id=work_item_id, reason="담당 바뀜", member_id=admin,
                                         now=LATER) == 0  # 이미 닫힘
    conn.execute("COMMIT")
    assert repo.get_task(conn, "task-gh-41")["revision"] == revision  # 올리지 않는다
    row = repo.list_owner_approvals(conn, "task-gh-41")[0]
    assert (row["request_id"], row["state"], row["action"], row["responder_id"]) == (
        request_id, "answered", "withdraw", admin)
    response = conn.execute("SELECT * FROM human_responses WHERE request_id = ?", (request_id,)).fetchone()
    assert (response["response_id"], response["text"], response["task_revision"]) == (
        f"withdraw-{request_id}", "담당 바뀜", revision)
    assert repo.get_human_request(conn, SESSION, other)["state"] == "open"  # 다른 요청은 그대로


def test_setting_policy_to_run_withdraws_open_approvals_of_that_agent(handoff):
    conn, work_item_id, admin, member = handoff
    repo.set_delegation_policy(conn, SESSION, FIX_AGENT, "owner_approval", member_id=admin, now=NOW)
    _open_approval(conn, requester=member)
    assert repo.get_work_item(conn, SESSION, work_item_id)["status_reason"].endswith("승인 대기")
    assert repo.set_delegation_policy(conn, SESSION, FIX_AGENT, "run", member_id=admin, now=LATER) is True
    rows = repo.list_owner_approvals(conn, "task-gh-41")
    assert [(r["state"], r["action"]) for r in rows] == [("answered", "withdraw")]
    assert not repo.get_work_item(conn, SESSION, work_item_id)["status_reason"].endswith("승인 대기")  # 재계산


def test_handoff_note_is_recorded_on_the_work_item_with_an_event(handoff):
    conn, work_item_id, _, member = handoff
    conn.execute("BEGIN IMMEDIATE")
    repo._set_handoff_note(conn, work_item_id, agent_id=FIX_AGENT, note="결제 모듈만 보세요", member_id=member,
                           now=LATER)
    conn.execute("COMMIT")
    row = repo.get_work_item(conn, SESSION, work_item_id)
    assert (row["handoff_note"], row["handoff_note_by_member_id"]) == ("결제 모듈만 보세요", member)
    assert _events_of(conn, work_item_id, "handoff_note") == [
        {"agent_id": FIX_AGENT, "note": "결제 모듈만 보세요", "by": member}]
    # 메모 없이 다시 맡기면 지난 메모는 지운다(이벤트 없음)
    conn.execute("BEGIN IMMEDIATE")
    repo._set_handoff_note(conn, work_item_id, agent_id=FIX_AGENT, note=None, member_id=member, now=LATER)
    conn.execute("COMMIT")
    row = repo.get_work_item(conn, SESSION, work_item_id)
    assert (row["handoff_note"], row["handoff_note_by_member_id"]) == (None, None)
    assert len(_events_of(conn, work_item_id, "handoff_note")) == 1


def test_execution_verify_only_flag_defaults_to_zero(cycle):
    _create_execution(cycle, "exec-fix-1", "task-gh-41", kind="code_change", inputs=("art-x",))
    assert repo.get_execution(cycle, "exec-fix-1")["verify_only"] == 0
    assert repo.get_task(cycle, "task-gh-41")["start_pending_at"] is None


@pytest.mark.parametrize("event", ["delegated_to_you", "runner_offline_waiting", "delegation_declined"])
def test_enqueue_notification_accepts_phase17_events(cycle, event):
    assert _notify(cycle, f"{event}:x", event=event) is True
    assert [r["event"] for r in repo.notifications_due(cycle, NOW, max_attempts=5)] == [event]
