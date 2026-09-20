"""진단 서비스 자체 DB — runs·run_events·artifacts·usage. 중앙 DB 와 별개이며 중앙 스키마를 쓰지 않는다.

이벤트는 진단 워커만 기록하므로 외부 발신자 검증(seq 재전송·중복)은 없고, 전이 규칙과 result_ready 의 산출물 선행만
ARCHITECTURE "실행 이벤트" 와 같게 지킨다.
"""

import json
import sqlite3
import threading

import pytest

from diagnostic_demo import db
from diagnostic_demo.db import InvalidTransition
from tests.diagnostic_demo.conftest import EXEC_A, diagnosis_request
from workflow.contracts.v1 import ExecutionRequest, RunStatus

NOW = "2026-09-20T00:10:00.000000Z"


def _request(execution_id: str = EXEC_A) -> ExecutionRequest:
    return ExecutionRequest.model_validate(diagnosis_request(execution_id))


@pytest.fixture
def accepted(diag_conn) -> str:
    db.insert_run(diag_conn, _request(), NOW)
    return EXEC_A


# --- 스키마 ---------------------------------------------------------------------


def test_init_schema_is_idempotent_and_enables_foreign_keys(diag_settings):
    conn = db.connect(diag_settings.db_path)
    db.init_schema(conn)
    db.init_schema(conn)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"runs", "run_events", "artifacts", "usage", "schema_version"} <= tables
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert conn.execute("SELECT version FROM schema_version").fetchone()[0] == db.SCHEMA_VERSION
    conn.close()


def test_utc_now_is_rfc3339_z():
    assert db.utc_now().endswith("Z") and "T" in db.utc_now()


# --- 접수 ---------------------------------------------------------------------


def test_insert_run_records_accepted_and_seq_1_event(diag_conn, accepted):
    row = db.get_run(diag_conn, accepted)
    assert row["status"] == "accepted" and row["last_event_seq"] == 1
    assert row["accepted_at"] == NOW and row["started_at"] is None
    assert row["request_hash"] == db.request_hash(_request())
    (event,) = db.events_after(diag_conn, accepted, 0)
    assert (event.seq, event.type, event.occurred_at) == (1, "accepted", NOW)


def test_request_hash_ignores_key_order_but_not_values():
    a = _request()
    reordered = ExecutionRequest.model_validate(dict(reversed(list(diagnosis_request().items()))))
    assert db.request_hash(a) == db.request_hash(reordered)
    assert db.request_hash(a) != db.request_hash(
        ExecutionRequest.model_validate(diagnosis_request(run_id="daily-0919-0900"))
    )


def test_insert_run_twice_is_an_integrity_error(diag_conn, accepted):
    with pytest.raises(sqlite3.IntegrityError):
        db.insert_run(diag_conn, _request(), NOW)


def test_get_run_missing_is_none(diag_conn):
    assert db.get_run(diag_conn, "exec-none") is None


# --- 이벤트와 전이 --------------------------------------------------------------


def test_started_progress_result_ready_transitions(diag_conn, accepted, artifact_store):
    assert db.append_event(diag_conn, accepted, "started", {"runtime_ref": "diag-run:1"}, NOW) == 2
    row = db.get_run(diag_conn, accepted)
    assert row["status"] == "running" and row["started_at"] == NOW
    assert db.append_event(diag_conn, accepted, "progress", {"message": "get_run 조회 완료"}, NOW) == 3
    artifact_id = db.store_artifact(
        diag_conn, artifact_store, accepted, kind="diagnosis_result",
        content_type="application/json", data=b"{}", now=NOW,
    )
    assert db.append_event(
        diag_conn, accepted, "result_ready", {"result_artifact_id": artifact_id}, NOW
    ) == 4
    row = db.get_run(diag_conn, accepted)
    assert row["status"] == "result_ready" and row["result_artifact_id"] == artifact_id
    assert row["finished_at"] == NOW and row["last_event_seq"] == 4


def test_result_ready_requires_stored_artifact_of_this_run(diag_conn, accepted, artifact_store):
    db.append_event(diag_conn, accepted, "started", {"runtime_ref": "r"}, NOW)
    with pytest.raises(InvalidTransition) as exc:
        db.append_event(diag_conn, accepted, "result_ready", {"result_artifact_id": "art-none"}, NOW)
    assert exc.value.reason == "result_artifact_missing"
    # 다른 실행의 산출물도 안 된다
    db.insert_run(diag_conn, _request("exec-diagnose-002"), NOW)
    other = db.store_artifact(
        diag_conn, artifact_store, "exec-diagnose-002", kind="diagnosis_result",
        content_type="application/json", data=b"{}", now=NOW,
    )
    with pytest.raises(InvalidTransition):
        db.append_event(diag_conn, accepted, "result_ready", {"result_artifact_id": other}, NOW)
    assert db.get_run(diag_conn, accepted)["status"] == "running"
    assert db.get_run(diag_conn, accepted)["last_event_seq"] == 2


def test_failed_from_accepted_or_running_records_error(diag_conn, accepted):
    db.append_event(
        diag_conn, accepted, "failed",
        {"code": "budget_exceeded", "message": "호출 15회 초과", "process_stopped": True}, NOW,
    )
    row = db.get_run(diag_conn, accepted)
    assert row["status"] == "failed" and row["finished_at"] == NOW
    assert json.loads(row["error_json"]) == {"code": "budget_exceeded", "message": "호출 15회 초과"}


def test_invalid_transitions_are_rejected_without_side_effects(diag_conn, accepted):
    with pytest.raises(InvalidTransition) as exc:
        db.append_event(diag_conn, accepted, "progress", {"message": "x"}, NOW)
    assert exc.value.current_status == "accepted"
    db.append_event(diag_conn, accepted, "failed", {"code": "c", "message": "m", "process_stopped": True}, NOW)
    for type_, data in (("started", {"runtime_ref": "r"}), ("failed", {"code": "c", "message": "m", "process_stopped": True})):
        with pytest.raises(InvalidTransition):
            db.append_event(diag_conn, accepted, type_, data, NOW)
    assert db.get_run(diag_conn, accepted)["last_event_seq"] == 2


def test_event_data_is_validated_by_contract(diag_conn, accepted):
    with pytest.raises(ValueError):
        db.append_event(diag_conn, accepted, "started", {"pid": 1}, NOW)
    with pytest.raises(ValueError):
        db.append_event(diag_conn, accepted, "started", {"runtime_ref": "r"}, "2026-09-20 00:00")
    assert db.get_run(diag_conn, accepted)["last_event_seq"] == 1


def test_events_after_filters_by_seq_in_order(diag_conn, accepted):
    db.append_event(diag_conn, accepted, "started", {"runtime_ref": "r"}, NOW)
    db.append_event(diag_conn, accepted, "progress", {"message": "a"}, NOW)
    db.append_event(diag_conn, accepted, "progress", {"message": "b"}, NOW)
    events = db.events_after(diag_conn, accepted, 1)
    assert [e.seq for e in events] == [2, 3, 4]
    assert [e.execution_id for e in events] == [accepted] * 3
    assert db.events_after(diag_conn, accepted, 10) == []


# --- 상태 봉투 ------------------------------------------------------------------


def test_run_status_builds_contract_envelope(diag_conn, accepted):
    status = db.run_status(diag_conn, accepted, after_seq=0)
    assert isinstance(status, RunStatus)
    assert status.model_dump(mode="json", exclude={"events"}) == {
        "execution_id": accepted, "status": "accepted", "last_event_seq": 1,
        "result_artifact_id": None, "error": None,
    }
    assert [e.type for e in status.events] == ["accepted"]
    db.append_event(diag_conn, accepted, "failed", {"code": "timeout", "message": "5분 초과", "process_stopped": True}, NOW)
    status = db.run_status(diag_conn, accepted, after_seq=1)
    assert status.status == "failed" and [e.seq for e in status.events] == [2]
    assert status.error.model_dump() == {"code": "timeout", "message": "5분 초과", "field": None, "details": None}
    assert db.run_status(diag_conn, "exec-none", after_seq=0) is None


# --- 워커 잠금 ------------------------------------------------------------------


def test_claim_accepted_locks_one_run_per_worker(diag_conn, diag_settings, accepted):
    db.insert_run(diag_conn, _request("exec-diagnose-002"), NOW)
    first = db.claim_accepted(diag_conn, NOW)
    assert first["execution_id"] == accepted and first["started_at"] == NOW
    second = db.claim_accepted(diag_conn, NOW)
    assert second["execution_id"] == "exec-diagnose-002"
    assert db.claim_accepted(diag_conn, NOW) is None


def test_concurrent_claims_do_not_share_a_run(diag_settings, diag_conn, accepted):
    results: list[str | None] = []

    def worker() -> None:
        conn = db.connect(diag_settings.db_path)
        try:
            row = db.claim_accepted(conn, NOW)
            results.append(row["execution_id"] if row is not None else None)
        finally:
            conn.close()

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results, key=str) == [None, accepted]


# --- 산출물·usage ------------------------------------------------------------------


def test_store_artifact_is_owned_by_run(diag_conn, accepted, artifact_store):
    artifact_id = db.store_artifact(
        diag_conn, artifact_store, accepted, kind="evidence", content_type="text/plain",
        data=b"line", now=NOW,
    )
    row = db.get_artifact(diag_conn, accepted, artifact_id)
    assert row["kind"] == "evidence" and row["size"] == 4 and row["content_type"] == "text/plain"
    assert artifact_store.read(row["store_ref"]) == b"line"
    assert db.get_artifact(diag_conn, "exec-other", artifact_id) is None
    assert db.get_artifact(diag_conn, accepted, "art-none") is None
    with pytest.raises(sqlite3.IntegrityError):
        db.store_artifact(diag_conn, artifact_store, accepted, kind="binary", content_type="x", data=b"", now=NOW)


def test_usage_and_daily_count(diag_conn, accepted):
    db.record_usage(diag_conn, accepted, "gpt-test", 1000, 100, 3, 0.0012, NOW)
    db.insert_run(diag_conn, _request("exec-diagnose-002"), "2026-09-21T00:00:00.000000Z")
    db.record_usage(diag_conn, "exec-diagnose-002", "gpt-test", 10, 1, 1, None, NOW)
    rows = diag_conn.execute("SELECT execution_id, estimated_usd FROM usage ORDER BY execution_id").fetchall()
    assert [tuple(r) for r in rows] == [(accepted, 0.0012), ("exec-diagnose-002", None)]
    assert db.count_runs_accepted_between(diag_conn, "2026-09-20T00:00:00.000000Z", "2026-09-21T00:00:00.000000Z") == 1
    assert db.count_runs_accepted_between(diag_conn, "2026-09-19T00:00:00.000000Z", "2026-09-22T00:00:00.000000Z") == 2
