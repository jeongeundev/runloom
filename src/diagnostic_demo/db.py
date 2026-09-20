"""진단 서비스 자체 DB — 중앙 DB 와 별개 (ARCHITECTURE: 다른 구성 요소의 DB 를 직접 열지 않는다).

표준 sqlite3 + 명시적 SQL (ADR-0002). 접수 기록은 execution_id 별로 영속 보존해 통신이 끊겨도 같은 ID 를
조회·재전송한다. 이벤트는 진단 워커만 기록하므로 발신자 검증은 없고, 전이 규칙과 "result_ready 는 참조한 결과가
영속 저장된 뒤에만" 규칙(ARCHITECTURE 실행 이벤트)을 지킨다. 트랜잭션 안에서 네트워크·모델을 기다리지 않는다.
"""

import hashlib
import json
import secrets
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from sqlite3 import Connection, Row

from diagnostic_demo.artifact_store import ArtifactStore
from workflow.contracts.v1 import (
    ARTIFACT_KINDS,
    CONTRACT_VERSION,
    ErrorBody,
    ExecutionEvent,
    ExecutionRequest,
    RunStatus,
)

SCHEMA_VERSION = 1

RUN_STATUSES = ("accepted", "running", "result_ready", "failed")
EVENT_TYPES = ("accepted", "started", "progress", "result_ready", "failed")

# (현재 상태, 이벤트) → 다음 상태. 없으면 InvalidTransition
_TRANSITIONS: dict[tuple[str, str], str] = {
    ("accepted", "started"): "running",
    ("running", "progress"): "running",
    ("running", "result_ready"): "result_ready",
    ("accepted", "failed"): "failed",
    ("running", "failed"): "failed",
}


class InvalidTransition(Exception):
    def __init__(self, current_status: str, event_type: str, reason: str | None = None):
        super().__init__(f"{current_status} 상태에서는 {event_type}를 받을 수 없습니다" + (f" ({reason})" if reason else ""))
        self.current_status = current_status
        self.event_type = event_type
        self.reason = reason


def utc_now() -> str:
    """RFC 3339 UTC, `Z` 표기. DB 의 모든 시각이 이 형식이라 문자열 비교가 시각 비교와 같다."""
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _in(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in values)


_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS schema_version (
  version INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
  execution_id       TEXT PRIMARY KEY,
  request_json       TEXT NOT NULL,
  request_hash       TEXT NOT NULL,
  status             TEXT NOT NULL CHECK (status IN ({_in(RUN_STATUSES)})),
  last_event_seq     INTEGER NOT NULL DEFAULT 0 CHECK (last_event_seq >= 0),
  result_artifact_id TEXT,
  error_json         TEXT,
  accepted_at        TEXT NOT NULL,
  started_at         TEXT,
  finished_at        TEXT
);

CREATE TABLE IF NOT EXISTS run_events (
  execution_id TEXT NOT NULL REFERENCES runs(execution_id),
  seq          INTEGER NOT NULL CHECK (seq >= 1),
  type         TEXT NOT NULL CHECK (type IN ({_in(EVENT_TYPES)})),
  occurred_at  TEXT NOT NULL,
  data_json    TEXT NOT NULL,
  PRIMARY KEY (execution_id, seq)
);

CREATE TABLE IF NOT EXISTS artifacts (
  artifact_id  TEXT PRIMARY KEY,
  execution_id TEXT NOT NULL REFERENCES runs(execution_id),
  kind         TEXT NOT NULL CHECK (kind IN ({_in(ARTIFACT_KINDS)})),
  content_type TEXT NOT NULL,
  sha256       TEXT NOT NULL,
  size         INTEGER NOT NULL CHECK (size >= 0),
  store_ref    TEXT NOT NULL,
  created_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS usage (
  execution_id  TEXT PRIMARY KEY REFERENCES runs(execution_id),
  model_id      TEXT NOT NULL,
  input_tokens  INTEGER NOT NULL CHECK (input_tokens >= 0),
  output_tokens INTEGER NOT NULL CHECK (output_tokens >= 0),
  calls         INTEGER NOT NULL CHECK (calls >= 0),
  estimated_usd REAL,
  recorded_at   TEXT NOT NULL
);
"""


def connect(path: str | Path) -> Connection:
    """외래키 ON, WAL, busy_timeout 5초, Row, 명시적 BEGIN. 연결은 요청·워커마다 열고 스레드 간 공유하지 않는다."""
    conn = sqlite3.connect(str(path), isolation_level=None, check_same_thread=False)
    conn.row_factory = Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def init_schema(conn: Connection) -> None:
    conn.executescript(_SCHEMA)
    if conn.execute("SELECT COUNT(*) FROM schema_version").fetchone()[0] == 0:
        conn.execute("INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,))


# --- 접수 -------------------------------------------------------------------------


def request_hash(request: ExecutionRequest) -> str:
    """의미 필드의 정규화 해시. 키 순서·공백은 무시하고 값이 다르면 다르다 (ARCHITECTURE 실행 요청과 접수)."""
    canonical = json.dumps(request.model_dump(mode="json"), sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def insert_run(conn: Connection, request: ExecutionRequest, now: str) -> Row:
    """새 접수: runs 에 accepted, seq 1 accepted 이벤트. 한 트랜잭션."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(
            "INSERT INTO runs (execution_id, request_json, request_hash, status, last_event_seq, accepted_at)"
            " VALUES (?, ?, ?, 'accepted', 1, ?)",
            (request.execution_id, request.model_dump_json(), request_hash(request), now),
        )
        conn.execute(
            "INSERT INTO run_events (execution_id, seq, type, occurred_at, data_json)"
            " VALUES (?, 1, 'accepted', ?, '{}')",
            (request.execution_id, now),
        )
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return get_run(conn, request.execution_id)


def get_run(conn: Connection, execution_id: str) -> Row | None:
    return conn.execute("SELECT * FROM runs WHERE execution_id = ?", (execution_id,)).fetchone()


# --- 이벤트 -------------------------------------------------------------------------


def append_event(conn: Connection, execution_id: str, type_: str, data: dict, occurred_at: str) -> int:
    """이벤트 저장·last_event_seq·상태 변경을 한 트랜잭션에서. 반환은 부여한 seq.

    data 는 계약 v1 `ExecutionEvent` 로 검증한다 (형식이 틀리면 ValueError, 저장 안 함)."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        run = get_run(conn, execution_id)
        if run is None:
            raise LookupError(f"execution {execution_id} 을 찾을 수 없습니다")
        seq = run["last_event_seq"] + 1
        event = ExecutionEvent.model_validate({
            "contract_version": CONTRACT_VERSION, "execution_id": execution_id, "seq": seq,
            "occurred_at": occurred_at, "type": type_, "data": data,
        })
        next_status = _TRANSITIONS.get((run["status"], type_))
        if next_status is None:
            raise InvalidTransition(run["status"], type_)
        if type_ == "result_ready" and get_artifact(conn, execution_id, event.data.result_artifact_id) is None:
            raise InvalidTransition(run["status"], type_, reason="result_artifact_missing")
        conn.execute(
            "INSERT INTO run_events (execution_id, seq, type, occurred_at, data_json) VALUES (?, ?, ?, ?, ?)",
            (execution_id, seq, type_, occurred_at, event.data.model_dump_json()),
        )
        conn.execute(
            "UPDATE runs SET status = ?, last_event_seq = ?,"
            " result_artifact_id = CASE WHEN ? = 'result_ready' THEN ? ELSE result_artifact_id END,"
            " error_json = CASE WHEN ? = 'failed' THEN ? ELSE error_json END,"
            " started_at = CASE WHEN ? = 'started' AND started_at IS NULL THEN ? ELSE started_at END,"
            " finished_at = CASE WHEN ? IN ('result_ready', 'failed') THEN ? ELSE finished_at END"
            " WHERE execution_id = ?",
            (
                next_status, seq,
                type_, getattr(event.data, "result_artifact_id", None),
                type_, json.dumps({"code": data.get("code"), "message": data.get("message")}, ensure_ascii=False)
                if type_ == "failed" else None,
                type_, occurred_at,
                type_, occurred_at,
                execution_id,
            ),
        )
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return seq


def events_after(conn: Connection, execution_id: str, after_seq: int) -> list[ExecutionEvent]:
    rows = conn.execute(
        "SELECT seq, type, occurred_at, data_json FROM run_events WHERE execution_id = ? AND seq > ? ORDER BY seq",
        (execution_id, after_seq),
    ).fetchall()
    return [
        ExecutionEvent.model_validate({
            "contract_version": CONTRACT_VERSION, "execution_id": execution_id, "seq": r["seq"],
            "occurred_at": r["occurred_at"], "type": r["type"], "data": json.loads(r["data_json"]),
        })
        for r in rows
    ]


def run_status(conn: Connection, execution_id: str, after_seq: int | None) -> RunStatus | None:
    """CONTRACT 1절 상태 봉투. `after_seq` 가 None 이면 이벤트를 싣지 않는다 (POST 응답)."""
    run = get_run(conn, execution_id)
    if run is None:
        return None
    error = None
    if run["error_json"] is not None:
        stored = json.loads(run["error_json"])
        error = ErrorBody(code=str(stored["code"]), message=str(stored["message"]), field=None, details=None)
    return RunStatus(
        execution_id=execution_id,
        status=run["status"],
        last_event_seq=run["last_event_seq"],
        result_artifact_id=run["result_artifact_id"],
        error=error,
        events=[] if after_seq is None else events_after(conn, execution_id, after_seq),
    )


# --- 워커 잠금 -------------------------------------------------------------------------


def claim_accepted(conn: Connection, now: str) -> Row | None:
    """접수됐지만 아직 잡지 않은 실행 하나를 `started_at` 기록으로 원자적으로 잠근다. 가장 오래된 접수부터."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute(
            "UPDATE runs SET started_at = ? WHERE execution_id = ("
            "  SELECT execution_id FROM runs WHERE status = 'accepted' AND started_at IS NULL"
            "  ORDER BY accepted_at, execution_id LIMIT 1"
            ") RETURNING *",
            (now,),
        ).fetchone()
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return row


# --- 산출물 -------------------------------------------------------------------------


def store_artifact(
    conn: Connection,
    store: ArtifactStore,
    execution_id: str,
    *,
    kind: str,
    content_type: str,
    data: bytes,
    now: str,
) -> str:
    """파일을 먼저 확정하고 DB 에 연결한다. 반환은 새 artifact_id."""
    sha256, store_ref = store.write(data)
    artifact_id = f"art-{secrets.token_hex(8)}"
    conn.execute(
        "INSERT INTO artifacts (artifact_id, execution_id, kind, content_type, sha256, size, store_ref, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (artifact_id, execution_id, kind, content_type, sha256, len(data), store_ref, now),
    )
    return artifact_id


def get_artifact(conn: Connection, execution_id: str, artifact_id: str) -> Row | None:
    """그 실행의 산출물만. 다른 실행의 것은 없는 것과 같다."""
    return conn.execute(
        "SELECT * FROM artifacts WHERE artifact_id = ? AND execution_id = ?", (artifact_id, execution_id)
    ).fetchone()


# --- usage·상한 -------------------------------------------------------------------------


def record_usage(
    conn: Connection,
    execution_id: str,
    model_id: str,
    input_tokens: int,
    output_tokens: int,
    calls: int,
    estimated_usd: float | None,
    now: str,
) -> None:
    """실행 하나의 토큰·호출 수·비용 추정. `estimated_usd` 는 단가 미설정이면 None (추정 불가)."""
    conn.execute(
        "INSERT OR REPLACE INTO usage (execution_id, model_id, input_tokens, output_tokens, calls, estimated_usd, recorded_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (execution_id, model_id, input_tokens, output_tokens, calls, estimated_usd, now),
    )


def count_runs_accepted_between(conn: Connection, start: str, end: str) -> int:
    """[start, end) 에 접수된 실행 수 — 전체 하루 상한."""
    return conn.execute(
        "SELECT COUNT(*) FROM runs WHERE accepted_at >= ? AND accepted_at < ?", (start, end)
    ).fetchone()[0]
