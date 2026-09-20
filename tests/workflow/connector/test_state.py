"""state — 연결 프로그램의 로컬 sqlite 기록: 등록, 실행 phase, seq 발급, 미전송 이벤트, 산출물 보존."""

import pytest

from workflow.connector import state
from workflow.contracts.v1 import ArtifactMeta, ExecutionEvent

from .conftest import NOW, make_request


def _event(execution_id: str, seq: int, type_: str = "progress", data: dict | None = None) -> ExecutionEvent:
    if data is None:
        data = {"message": f"진행 {seq}"} if type_ == "progress" else {}
    return ExecutionEvent.model_validate({
        "contract_version": 1, "execution_id": execution_id, "seq": seq,
        "occurred_at": NOW, "type": type_, "data": data,
    })


def test_init_schema_is_idempotent(state_conn):
    state.init_schema(state_conn)
    state.init_schema(state_conn)

    tables = {r[0] for r in state_conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"registrations", "executions", "pending_events", "outputs"} <= tables


# --- 등록 ----------------------------------------------------------------------------


def test_registration_roundtrip(state_conn, tmp_path):
    reg = {
        "local_registration_id": "local-demo-report",
        "repo_path": str(tmp_path / "repo"),
        "tool": "codex",
        "repository_id": "demo-report-repo",
        "base_commit": "3f9c2e1a7b0d4c6e8f1a2b3c4d5e6f7a8b9c0d1e",
        "verification_profiles": {"vp-pytest": ["python3", "-m", "pytest", "-q"]},
    }

    state.save_registration(state_conn, reg)

    assert state.get_registration(state_conn, "local-demo-report") == reg
    assert state.get_registration(state_conn, "없음") is None


def test_save_registration_overwrites_same_id(state_conn, tmp_path):
    base = {
        "local_registration_id": "local-demo-report", "repo_path": str(tmp_path), "tool": "codex",
        "repository_id": "demo-report-repo", "base_commit": "a" * 40, "verification_profiles": {},
    }
    state.save_registration(state_conn, base)
    state.save_registration(state_conn, {**base, "base_commit": "b" * 40})

    assert state.get_registration(state_conn, "local-demo-report")["base_commit"] == "b" * 40


# --- 실행 기록 -----------------------------------------------------------------------


def test_record_claim_starts_at_accepted_with_next_seq_1(state_conn):
    request = make_request()

    state.record_claim(state_conn, request, NOW)

    row = state.get_execution(state_conn, request.execution_id)
    assert (row["phase"], row["next_seq"], row["claimed_at"]) == ("accepted", 1, NOW)
    assert row["request_json"] == request.model_dump_json()
    assert state.active_execution(state_conn)["execution_id"] == request.execution_id


def test_record_claim_twice_keeps_existing_record(state_conn):
    request = make_request()
    state.record_claim(state_conn, request, NOW)
    state.next_seq(state_conn, request.execution_id)

    state.record_claim(state_conn, request, "2026-09-20T02:00:00Z")  # 재시작 후 같은 배정을 다시 받음

    row = state.get_execution(state_conn, request.execution_id)
    assert (row["next_seq"], row["claimed_at"]) == (2, NOW)


def test_set_phase_updates_phase_and_fields(state_conn):
    request = make_request()
    state.record_claim(state_conn, request, NOW)

    state.set_phase(state_conn, request.execution_id, "running", runtime_ref="pid:1;start:x", pid=1)

    row = state.get_execution(state_conn, request.execution_id)
    assert (row["phase"], row["runtime_ref"], row["pid"]) == ("running", "pid:1;start:x", 1)


def test_set_phase_rejects_unknown_phase_and_field(state_conn):
    request = make_request()
    state.record_claim(state_conn, request, NOW)

    with pytest.raises(Exception):
        state.set_phase(state_conn, request.execution_id, "done")
    with pytest.raises(ValueError):
        state.set_phase(state_conn, request.execution_id, "running", bogus=1)


def test_next_seq_continues_after_reconnect(paths, state_conn):
    request = make_request()
    state.record_claim(state_conn, request, NOW)
    assert [state.next_seq(state_conn, request.execution_id) for _ in range(3)] == [1, 2, 3]
    state_conn.close()

    reopened = state.connect(paths.state_db)  # 새 connect (프로그램 재시작·재연결)
    try:
        state.init_schema(reopened)
        assert state.next_seq(reopened, request.execution_id) == 4
    finally:
        reopened.close()


# --- 미전송 이벤트 -------------------------------------------------------------------


def test_pending_events_keep_seq_order_and_ack_removes(state_conn):
    request = make_request()
    state.record_claim(state_conn, request, NOW)
    for seq in (3, 1, 2):
        state.queue_event(state_conn, _event(request.execution_id, seq))

    pending = state.pop_pending(state_conn, request.execution_id)
    assert [e.seq for e in pending] == [1, 2, 3]

    state.ack_event(state_conn, request.execution_id, 1)
    state.ack_event(state_conn, request.execution_id, 2)
    assert [e.seq for e in state.pop_pending(state_conn, request.execution_id)] == [3]
    assert state.pending_execution_ids(state_conn) == [request.execution_id]


def test_unack_from_restores_acked_events_for_resend(state_conn):
    request = make_request()
    state.record_claim(state_conn, request, NOW)
    for seq in (1, 2, 3):
        state.queue_event(state_conn, _event(request.execution_id, seq))
        state.ack_event(state_conn, request.execution_id, seq)
    assert state.pop_pending(state_conn, request.execution_id) == []

    assert state.unack_from(state_conn, request.execution_id, 2) == 2

    assert [e.seq for e in state.pop_pending(state_conn, request.execution_id)] == [2, 3]
    assert state.unack_from(state_conn, request.execution_id, 9) == 0


def test_queue_event_same_seq_twice_is_rejected(state_conn):
    request = make_request()
    state.record_claim(state_conn, request, NOW)
    state.queue_event(state_conn, _event(request.execution_id, 1))

    with pytest.raises(Exception):
        state.queue_event(state_conn, _event(request.execution_id, 1))


# --- 활성 실행 -----------------------------------------------------------------------


def test_active_execution_is_none_when_finished_and_all_acked(state_conn):
    request = make_request()
    state.record_claim(state_conn, request, NOW)
    state.queue_event(state_conn, _event(request.execution_id, 1, "accepted", {}))
    state.set_phase(state_conn, request.execution_id, "finished", finished_at=NOW)

    assert state.active_execution(state_conn)["execution_id"] == request.execution_id  # 미전송 남음

    state.ack_event(state_conn, request.execution_id, 1)
    assert state.active_execution(state_conn) is None


# --- 산출물 보존 ---------------------------------------------------------------------


def test_outputs_are_saved_and_marked_uploaded(state_conn):
    request = make_request()
    state.record_claim(state_conn, request, NOW)
    meta = ArtifactMeta(contract_version=1, kind="diff", name="a.diff", content_type="text/plain",
                        sha256="0" * 64, size=3)

    state.save_outputs(state_conn, request.execution_id, [(meta, b"abc")])
    outputs = state.list_outputs(state_conn, request.execution_id)
    assert len(outputs) == 1
    assert (outputs[0]["idx"], outputs[0]["meta"], outputs[0]["data"], outputs[0]["artifact_id"]) == (
        0, meta, b"abc", None,
    )

    state.set_output_artifact(state_conn, request.execution_id, 0, "art-1")
    assert state.list_outputs(state_conn, request.execution_id)[0]["artifact_id"] == "art-1"
