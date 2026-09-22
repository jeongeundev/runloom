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
    EventConflict,
    HashMismatch,
    InvalidTransition,
    KindInUse,
    KindProtected,
    NotFound,
    SequenceGap,
)
from workflow.contracts.v1 import (
    BUILTIN_KINDS,
    BUILTIN_RULES,
    ArtifactMeta,
    ExecutionEvent,
    ExecutionRequest,
    KindSpec,
    SelectionRecord,
    SuccessorRule,
)

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


@pytest.fixture
def seeded(conn):
    repo.create_session(conn, SESSION, NOW)
    repo.create_session(conn, OTHER_SESSION, NOW)
    repo.insert_task(conn, _task(TASK_A), NOW)
    return conn


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


# --- 업무·선택 ---------------------------------------------------------------


def test_task_insert_get_list_and_status(seeded):
    conn = seeded
    row = repo.get_task(conn, TASK_A)
    assert row["status"] == "실행 가능" and row["revision"] == 1
    assert json.loads(row["required_capability_json"])["code"] == "operations.diagnose"
    repo.insert_task(conn, _task("other-task", session_id=OTHER_SESSION), LATER)
    assert [r["task_id"] for r in repo.list_tasks(conn, SESSION)] == [TASK_A]
    assert len(repo.list_tasks(conn, None)) == 2
    repo.update_task_status(conn, TASK_A, "완료", "검토 승인", finished_at=LATER, review_decision="approve")
    row = repo.get_task(conn, TASK_A)
    assert (row["status"], row["status_reason"], row["finished_at"], row["review_decision"]) == (
        "완료", "검토 승인", LATER, "approve")
    with pytest.raises(NotFound):
        repo.update_task_status(conn, "nope", "완료", "x")


def test_task_predecessor_must_be_same_session(seeded):
    conn = seeded
    repo.insert_task(conn, _task(TASK_B, kind="code_change", predecessor=TASK_A), NOW)
    assert [r["task_id"] for r in repo.successors_of(conn, TASK_A)] == [TASK_B]
    with pytest.raises(NotFound):
        repo.insert_task(conn, _task("fix-2", session_id=OTHER_SESSION, kind="code_change",
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


def test_confirm_merge(seeded):
    repo.confirm_merge(seeded, TASK_A, LATER)
    assert repo.get_task(seeded, TASK_A)["merge_confirmed_at"] == LATER
    with pytest.raises(NotFound):
        repo.confirm_merge(seeded, "nope", LATER)


# --- 실행 생성·잠금 -----------------------------------------------------------


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

    repo.insert_task(conn, _task("other-task"), NOW)
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
    repo.insert_task(conn, _task(TASK_B, kind="code_change", predecessor=TASK_A), NOW)
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
    repo.insert_task(conn, _task(TASK_B, kind="code_change", predecessor=TASK_A), NOW)
    repo.insert_task(conn, _task("fix-2", kind="code_change", predecessor=TASK_A), NOW)
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
    repo.insert_task(conn, _task("other-task", session_id=OTHER_SESSION), NOW)
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
    repo.insert_task(conn, _task(TASK_B, kind="code_change", predecessor=TASK_A), NOW)
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


# --- 상한 --------------------------------------------------------------------


def test_diagnosis_usage_counts_per_session_and_total(seeded):
    conn = seeded
    repo.record_diagnosis_start(conn, SESSION, "exec-1", "2026-09-19T23:00:00Z")
    repo.record_diagnosis_start(conn, SESSION, "exec-2", "2026-09-20T01:00:00Z")
    repo.record_diagnosis_start(conn, OTHER_SESSION, "exec-3", "2026-09-20T02:00:00Z")
    since = "2026-09-20T00:00:00Z"
    assert repo.count_diagnosis_started(conn, session_id=SESSION, since=since) == 1
    assert repo.count_diagnosis_started(conn, session_id=OTHER_SESSION, since=since) == 1
    assert repo.count_diagnosis_started(conn, session_id=None, since=since) == 2
    assert repo.count_diagnosis_started(conn, session_id=None, since="2026-09-19T00:00:00Z") == 3


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
    repo.insert_task(conn, _task(TASK_B, kind="code_change", predecessor=TASK_A), NOW)
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
    repo.insert_task(conn, _task(TASK_B, kind="code_change", predecessor=TASK_A), NOW)
    assert repo.tasks_with_ready_predecessor(conn) == []
    repo.update_task_status(conn, TASK_A, "완료", "판정 근거: 12/12")
    assert repo.tasks_with_ready_predecessor(conn) == []  # finished_at 이 없으면 완료로 보지 않는다
    repo.update_task_status(conn, TASK_A, "완료", "판정 근거: 12/12", finished_at=LATER)
    assert [r["task_id"] for r in repo.tasks_with_ready_predecessor(conn)] == [TASK_B]
    repo.update_task_status(conn, TASK_B, "실패", "검토 거절", finished_at=LATER)
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
    repo.insert_task(conn, task, NOW)
    row = repo.get_task(conn, "t-imported")
    assert (row["chain_id"], row["source_ref"]) == ("chain-1", "#42")
    with pytest.raises(sqlite3.IntegrityError):
        repo.insert_task(conn, {**_task("t-bad"), "chain_id": "no-such-chain"}, NOW)
    assert repo.get_task(conn, "t-bad") is None  # 롤백됨


def test_tasks_of_chain_follows_predecessor_order(seeded):
    """created_at·task_id 정렬이 모두 어긋나도 선행 없는 것부터 predecessor 체인 순서로 돌려준다."""
    conn = seeded
    repo.insert_chain(conn, _chain(), NOW)
    repo.insert_chain(conn, _chain("chain-2"), NOW)
    # A → B → C. 삽입은 선행이 먼저 있어야 하므로 A·B·C 순이지만 created_at 은 거꾸로, ID 는 역순 알파벳.
    repo.insert_task(conn, {**_task("t-root"), "chain_id": "chain-1"}, "2026-09-20T00:00:09Z")
    repo.insert_task(conn, {**_task("t-mid", kind="code_change", predecessor="t-root"),
                            "chain_id": "chain-1"}, "2026-09-20T00:00:05Z")
    repo.insert_task(conn, {**_task("t-last", kind="code_change", predecessor="t-mid"),
                            "chain_id": "chain-1"}, "2026-09-20T00:00:01Z")
    # 다른 체인·체인 없는 Task 는 섞이지 않는다
    repo.insert_task(conn, {**_task("t-other"), "chain_id": "chain-2"}, NOW)
    assert [r["task_id"] for r in repo.tasks_of_chain(conn, "chain-1")] == ["t-root", "t-mid", "t-last"]
    assert [r["task_id"] for r in repo.tasks_of_chain(conn, "chain-2")] == ["t-other"]
    assert repo.tasks_of_chain(conn, "nope") == []


def test_tasks_of_chain_treats_predecessor_outside_chain_as_root(seeded):
    conn = seeded
    repo.insert_chain(conn, _chain(), NOW)
    # TASK_A(체인 없음) 를 선행으로 갖는 B 가 체인의 첫 Task 다
    repo.insert_task(conn, {**_task("t-b", kind="code_change", predecessor=TASK_A),
                            "chain_id": "chain-1"}, LATER)
    repo.insert_task(conn, {**_task("t-c", kind="code_change", predecessor="t-b"),
                            "chain_id": "chain-1"}, NOW)
    assert [r["task_id"] for r in repo.tasks_of_chain(conn, "chain-1")] == ["t-b", "t-c"]


def test_list_tasks_is_unchanged_by_chain_columns(seeded):
    conn = seeded
    repo.insert_chain(conn, _chain(), NOW)
    repo.insert_task(conn, {**_task("t-imported"), "chain_id": "chain-1", "source_ref": "OPS-42"}, LATER)
    assert [r["task_id"] for r in repo.list_tasks(conn, SESSION)] == [TASK_A, "t-imported"]


# --- phase 6: 업무 종류·후속 규칙 (ADR-0009) -------------------------------------


REVIEW = KindSpec(
    kind="review", label="검토", capability_code="review", scope_key="repository_id",
    input_kinds=["diff", "code_change_result"], output_kind="generic_result",
    outcomes=["approved", "changes_requested", "needs_information"],
    instructions="diff 를 읽고 검토하세요.", builtin=False,
)
FIX_TO_REVIEW = SuccessorRule(
    from_kind="code_change", on_outcomes=["ready_for_review"], to_kind="review",
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
    repo.create_session(conn, SESSION, NOW)
    assert repo.list_kinds(conn, SESSION) == list(BUILTIN_KINDS)
    rules = repo.list_rules(conn, SESSION)
    assert [rule for _, rule in rules] == list(BUILTIN_RULES)
    assert all(rule_id.startswith("rule-") for rule_id, _ in rules)
    assert repo.list_kinds(conn, OTHER_SESSION) == [] and repo.list_rules(conn, OTHER_SESSION) == []
    repo.create_session(conn, OTHER_SESSION, LATER)
    assert repo.list_kinds(conn, OTHER_SESSION) == list(BUILTIN_KINDS)
    assert len(repo.list_rules(conn, OTHER_SESSION)) == 1
    assert repo.get_kind(conn, SESSION, "diagnosis") == BUILTIN_KINDS[0]
    assert repo.get_kind(conn, SESSION, "review") is None


def test_create_session_is_atomic_with_seed(conn):
    repo.create_session(conn, SESSION, NOW)
    with pytest.raises(sqlite3.IntegrityError):
        repo.create_session(conn, SESSION, LATER)
    assert len(repo.list_kinds(conn, SESSION)) == 2 and len(repo.list_rules(conn, SESSION)) == 1


def test_insert_kind_roundtrip_ordering_and_duplicate(seeded):
    conn = seeded
    zebra = REVIEW.model_copy(update={"kind": "zebra", "capability_code": "zebra"})
    repo.insert_kind(conn, SESSION, zebra, NOW)
    repo.insert_kind(conn, SESSION, REVIEW, LATER)
    apple = REVIEW.model_copy(update={"kind": "apple", "capability_code": "apple"})
    repo.insert_kind(conn, SESSION, apple, NOW)
    assert repo.get_kind(conn, SESSION, "review") == REVIEW
    assert repo.get_kind(conn, OTHER_SESSION, "review") is None  # 세션 격리
    # 내장 먼저(BUILTIN_KINDS 순), 그 다음 created_at·kind 순
    assert [k.kind for k in repo.list_kinds(conn, SESSION)] == [
        "diagnosis", "code_change", "apple", "zebra", "review"]
    with pytest.raises(DuplicateKind):
        repo.insert_kind(conn, SESSION, REVIEW, LATER)
    with pytest.raises(DuplicateKind):  # 내장 이름 재등록도 중복
        repo.insert_kind(conn, SESSION, BUILTIN_KINDS[0], LATER)
    with pytest.raises(sqlite3.IntegrityError):  # 없는 세션
        repo.insert_kind(conn, "no-such-session", REVIEW, NOW)


def test_delete_kind_protects_builtin_and_in_use(seeded):
    conn = seeded
    with pytest.raises(KindProtected):
        repo.delete_kind(conn, SESSION, "diagnosis")
    with pytest.raises(NotFound):
        repo.delete_kind(conn, SESSION, "review")
    repo.insert_kind(conn, SESSION, REVIEW, NOW)
    repo.insert_kind(conn, OTHER_SESSION, REVIEW, NOW)
    rule_id = repo.insert_rule(conn, SESSION, FIX_TO_REVIEW, NOW)
    with pytest.raises(KindInUse):  # 규칙이 참조
        repo.delete_kind(conn, SESSION, "review")
    repo.delete_rule(conn, SESSION, rule_id)
    repo.insert_task(conn, _task("review-1", kind="review", predecessor=TASK_A), NOW)
    with pytest.raises(KindInUse):  # Task 가 사용
        repo.delete_kind(conn, SESSION, "review")
    repo.delete_kind(conn, OTHER_SESSION, "review")  # 다른 세션의 같은 이름은 무관
    assert repo.get_kind(conn, OTHER_SESSION, "review") is None
    assert repo.get_kind(conn, SESSION, "review") == REVIEW


def test_delete_kind_success(seeded):
    conn = seeded
    repo.insert_kind(conn, SESSION, REVIEW, NOW)
    repo.delete_kind(conn, SESSION, "review")
    assert repo.get_kind(conn, SESSION, "review") is None
    assert [k.kind for k in repo.list_kinds(conn, SESSION)] == ["diagnosis", "code_change"]


def test_rule_insert_get_list_duplicate_and_delete(seeded):
    conn = seeded
    with pytest.raises(NotFound):  # to_kind 미등록
        repo.insert_rule(conn, SESSION, FIX_TO_REVIEW, NOW)
    repo.insert_kind(conn, SESSION, REVIEW, NOW)
    rule_id = repo.insert_rule(conn, SESSION, FIX_TO_REVIEW, LATER)
    assert rule_id.startswith("rule-")
    assert repo.get_rule(conn, SESSION, "code_change", "review") == FIX_TO_REVIEW
    assert repo.get_rule(conn, SESSION, "diagnosis", "code_change") == BUILTIN_RULES[0]
    assert repo.get_rule(conn, SESSION, "review", "code_change") is None
    assert repo.get_rule(conn, OTHER_SESSION, "code_change", "review") is None
    rules = repo.list_rules(conn, SESSION)
    assert [(rid == rule_id, rule) for rid, rule in rules] == [(False, BUILTIN_RULES[0]), (True, FIX_TO_REVIEW)]
    with pytest.raises(DuplicateRule):  # 같은 (from, to) — on_outcomes 가 달라도
        repo.insert_rule(conn, SESSION, FIX_TO_REVIEW.model_copy(update={"on_outcomes": ["needs_information"]}), LATER)
    with pytest.raises(NotFound):  # from_kind 미등록
        repo.insert_rule(conn, SESSION, SuccessorRule(from_kind="nope", on_outcomes=["x"], to_kind="review",
                                                      handoff_kinds=["diff"]), NOW)
    repo.delete_rule(conn, SESSION, rule_id)
    assert repo.get_rule(conn, SESSION, "code_change", "review") is None
    with pytest.raises(NotFound):
        repo.delete_rule(conn, SESSION, rule_id)
    with pytest.raises(NotFound):  # 다른 세션의 rule_id 로는 지울 수 없다
        [(builtin_id, _)] = repo.list_rules(conn, SESSION)
        repo.delete_rule(conn, OTHER_SESSION, builtin_id)


def test_builtin_rule_can_be_deleted(seeded):
    conn = seeded
    [(rule_id, _)] = repo.list_rules(conn, SESSION)
    repo.delete_rule(conn, SESSION, rule_id)
    assert repo.list_rules(conn, SESSION) == []
    assert len(repo.list_rules(conn, OTHER_SESSION)) == 1
    assert repo.get_rule(conn, SESSION, "diagnosis", "code_change") is None


def test_list_rules_orders_by_created_at_then_rule_id(seeded):
    conn = seeded
    repo.insert_kind(conn, SESSION, REVIEW, NOW)
    later_rule = repo.insert_rule(conn, SESSION, FIX_TO_REVIEW, LATER)
    earlier = SuccessorRule(from_kind="review", on_outcomes=["changes_requested"], to_kind="code_change",
                            handoff_kinds=["diagnosis_result", "evidence", "generic_result"])
    earlier_rule = repo.insert_rule(conn, SESSION, earlier, NOW)
    ids = [rid for rid, _ in repo.list_rules(conn, SESSION)]
    assert ids[-1] == later_rule and earlier_rule in ids[:-1]


def test_insert_task_requires_registered_kind(seeded):
    conn = seeded
    with pytest.raises(NotFound) as info:
        repo.insert_task(conn, _task("review-1", kind="review"), NOW)
    assert "review" in str(info.value) and "등록되지 않음" in str(info.value)
    assert repo.get_task(conn, "review-1") is None
    repo.insert_kind(conn, SESSION, REVIEW, NOW)
    repo.insert_task(conn, _task("review-1", kind="review"), NOW)
    assert repo.get_task(conn, "review-1")["kind"] == "review"
    with pytest.raises(NotFound):  # 다른 세션의 등록은 세지 않는다
        repo.insert_task(conn, _task("review-2", session_id=OTHER_SESSION, kind="review"), NOW)


def test_tasks_with_ready_predecessor_by_result_ready_with_verdict(seeded, store):
    conn = seeded
    repo.insert_task(conn, _task(TASK_B, kind="code_change", predecessor=TASK_A), NOW)
    _create_execution(conn, "exec-1")
    _to_result_ready(conn, store, "exec-1")
    assert repo.tasks_with_ready_predecessor(conn) == []  # 판정 없음 → 제외
    repo.record_verdict(conn, task_id=TASK_A, execution_id="exec-1", verdict={"outcome": "passed", "checks": []},
                        status="확인 필요", reason="검토 대기", finish=False, now=LATER)
    assert [r["task_id"] for r in repo.tasks_with_ready_predecessor(conn)] == [TASK_B]  # 사람 승인 전
    repo.update_task_status(conn, TASK_A, "완료", "검토 승인", finished_at=LATER, review_decision="approve")
    repo.release_execution(conn, "exec-1", LATER)
    assert [r["task_id"] for r in repo.tasks_with_ready_predecessor(conn)] == [TASK_B]
    repo.update_task_status(conn, TASK_B, "완료", "검토 승인", finished_at=LATER)
    assert repo.tasks_with_ready_predecessor(conn) == []  # 마감된 후속은 제외


def test_tasks_with_ready_predecessor_excludes_failed_predecessor(seeded, store):
    conn = seeded
    repo.insert_task(conn, _task(TASK_B, kind="code_change", predecessor=TASK_A), NOW)
    _create_execution(conn, "exec-1")
    _to_result_ready(conn, store, "exec-1")
    _verdict_row(conn, TASK_A, "exec-1")
    assert [r["task_id"] for r in repo.tasks_with_ready_predecessor(conn)] == [TASK_B]
    repo.update_task_status(conn, TASK_A, "실패", "검토 거절", finished_at=LATER, review_decision="close")
    assert repo.tasks_with_ready_predecessor(conn) == []  # 실행이 아직 활성이어도 선행 실패면 제외


def test_tasks_with_ready_predecessor_ignores_released_result_without_completion(seeded, store):
    conn = seeded
    repo.insert_task(conn, _task(TASK_B, kind="code_change", predecessor=TASK_A), NOW)
    _create_execution(conn, "exec-1")
    _to_result_ready(conn, store, "exec-1")
    _verdict_row(conn, TASK_A, "exec-1")
    repo.release_execution(conn, "exec-1", LATER)  # 활성 실행이 아니고 선행도 완료가 아님
    assert repo.tasks_with_ready_predecessor(conn) == []


def test_tasks_with_ready_predecessor_orders_by_created_at_then_task_id(seeded, store):
    conn = seeded
    repo.insert_task(conn, _task("fix-z", kind="code_change", predecessor=TASK_A), NOW)
    repo.insert_task(conn, _task("fix-a", kind="code_change", predecessor=TASK_A), LATER)
    repo.insert_task(conn, _task("fix-m", kind="code_change", predecessor=TASK_A), NOW)
    repo.update_task_status(conn, TASK_A, "완료", "판정 근거: 12/12", finished_at=LATER)
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
    repo.insert_task(conn, _task(TASK_B, kind="code_change", predecessor=TASK_A), NOW)
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
    repo.insert_task(conn, _task("review-1", kind="review", predecessor=TASK_B), NOW)
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
