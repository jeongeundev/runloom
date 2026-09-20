"""연결 프로그램의 로컬 상태 — 등록, 실행 기록, 미전송 이벤트, 산출물 보존 (ARCHITECTURE "상태·재접속·완료").

중앙 DB 와 별개의 sqlite 파일이다. 여기 적는 규칙:
- `phase` 는 로컬 관찰이지 중앙의 Execution 상태가 아니다.
  accepted(접수 기록) → launching(어댑터 호출 직전) → running(시작 확인) → finished(어댑터 반환).
  `finished_at` 은 종료 이벤트(result_ready·failed)를 큐에 넣은 시각이다.
- seq 는 여기서만 발급하고 재시작해도 1 로 되돌리지 않는다.
- 이벤트는 ack 뒤에도 남겨 두어 중앙이 `sequence_gap` 을 돌려주면 그 순번부터 다시 보낼 수 있다.
- 어댑터 산출물은 업로드 전에 `outputs` 에 넣어 업로드 중 끊겨도 어댑터를 다시 돌리지 않는다.
"""

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from workflow.contracts.v1 import ArtifactMeta, ExecutionEvent, ExecutionRequest

PHASES = ("accepted", "launching", "running", "finished")

_EXECUTION_FIELDS = frozenset({
    "pid", "process_start", "runtime_ref", "result_json", "failed_json", "worktree_path",
    "handoff_dir", "finished_at", "unknown_local_at",
})

_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS registrations (
  local_registration_id      TEXT PRIMARY KEY,
  repo_path                  TEXT NOT NULL,
  tool                       TEXT NOT NULL,
  repository_id              TEXT NOT NULL,
  base_commit                TEXT NOT NULL,
  verification_profiles_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS executions (
  execution_id     TEXT PRIMARY KEY,
  phase            TEXT NOT NULL CHECK (phase IN ({", ".join(f"'{p}'" for p in PHASES)})),
  request_json     TEXT NOT NULL,
  pid              INTEGER,
  process_start    TEXT,
  runtime_ref      TEXT,
  next_seq         INTEGER NOT NULL DEFAULT 1 CHECK (next_seq >= 1),
  result_json      TEXT,
  failed_json      TEXT,
  worktree_path    TEXT,
  handoff_dir      TEXT,
  claimed_at       TEXT NOT NULL,
  finished_at      TEXT,
  unknown_local_at TEXT
);

CREATE TABLE IF NOT EXISTS pending_events (
  execution_id TEXT NOT NULL REFERENCES executions(execution_id),
  seq          INTEGER NOT NULL CHECK (seq >= 1),
  event_json   TEXT NOT NULL,
  acked_at     TEXT,
  PRIMARY KEY (execution_id, seq)
);

CREATE TABLE IF NOT EXISTS outputs (
  execution_id TEXT NOT NULL REFERENCES executions(execution_id),
  idx          INTEGER NOT NULL,
  meta_json    TEXT NOT NULL,
  data         BLOB NOT NULL,
  artifact_id  TEXT,
  PRIMARY KEY (execution_id, idx)
);
"""


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def connect(path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(_SCHEMA)


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[None]:
    if conn.in_transaction:  # 바깥 트랜잭션에 합류
        yield
        return
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        conn.rollback()
        raise
    conn.commit()


# --- 등록 ------------------------------------------------------------------------------


def save_registration(conn: sqlite3.Connection, reg: dict) -> None:
    with transaction(conn):
        conn.execute(
            """
            INSERT INTO registrations (local_registration_id, repo_path, tool, repository_id, base_commit,
              verification_profiles_json) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(local_registration_id) DO UPDATE SET repo_path = excluded.repo_path,
              tool = excluded.tool, repository_id = excluded.repository_id, base_commit = excluded.base_commit,
              verification_profiles_json = excluded.verification_profiles_json
            """,
            (
                reg["local_registration_id"], reg["repo_path"], reg["tool"], reg["repository_id"],
                reg["base_commit"], json.dumps(reg["verification_profiles"]),
            ),
        )


def get_registration(conn: sqlite3.Connection, local_registration_id: str) -> dict | None:
    row = conn.execute(
        "SELECT * FROM registrations WHERE local_registration_id = ?", (local_registration_id,)
    ).fetchone()
    if row is None:
        return None
    return {
        "local_registration_id": row["local_registration_id"],
        "repo_path": row["repo_path"],
        "tool": row["tool"],
        "repository_id": row["repository_id"],
        "base_commit": row["base_commit"],
        "verification_profiles": json.loads(row["verification_profiles_json"]),
    }


# --- 실행 기록 ---------------------------------------------------------------------------


def record_claim(conn: sqlite3.Connection, request: ExecutionRequest, now: str) -> None:
    """phase accepted, next_seq 1. 이미 있으면(재시작 후 같은 배정) 기존 기록을 유지한다."""
    with transaction(conn):
        conn.execute(
            "INSERT OR IGNORE INTO executions (execution_id, phase, request_json, claimed_at) VALUES (?, 'accepted', ?, ?)",
            (request.execution_id, request.model_dump_json(), now),
        )


def get_execution(conn: sqlite3.Connection, execution_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM executions WHERE execution_id = ?", (execution_id,)).fetchone()
    return None if row is None else dict(row)


def update_execution(conn: sqlite3.Connection, execution_id: str, **fields) -> None:
    unknown = set(fields) - _EXECUTION_FIELDS
    if unknown:
        raise ValueError(f"executions 에 없는 필드: {sorted(unknown)}")
    if not fields:
        return
    assignments = ", ".join(f"{name} = ?" for name in fields)
    with transaction(conn):
        conn.execute(
            f"UPDATE executions SET {assignments} WHERE execution_id = ?",
            (*fields.values(), execution_id),
        )


def set_phase(conn: sqlite3.Connection, execution_id: str, phase: str, **fields) -> None:
    unknown = set(fields) - _EXECUTION_FIELDS
    if unknown:
        raise ValueError(f"executions 에 없는 필드: {sorted(unknown)}")
    assignments = ", ".join(["phase = ?", *(f"{name} = ?" for name in fields)])
    with transaction(conn):
        conn.execute(
            f"UPDATE executions SET {assignments} WHERE execution_id = ?",
            (phase, *fields.values(), execution_id),
        )


def next_seq(conn: sqlite3.Connection, execution_id: str) -> int:
    """발급 후 +1 저장. 발급한 seq 는 반드시 같은 트랜잭션에서 `queue_event` 로 써야 빈 순번이 생기지 않는다."""
    with transaction(conn):
        row = conn.execute(
            "UPDATE executions SET next_seq = next_seq + 1 WHERE execution_id = ? RETURNING next_seq - 1",
            (execution_id,),
        ).fetchone()
    if row is None:
        raise KeyError(execution_id)
    return row[0]


def active_execution(conn: sqlite3.Connection) -> dict | None:
    """종료 이벤트를 아직 큐에 넣지 않았거나, 넣었지만 미전송 이벤트가 남은 실행. 연결 프로그램당 하나."""
    row = conn.execute(
        "SELECT * FROM executions WHERE finished_at IS NULL ORDER BY claimed_at, execution_id LIMIT 1"
    ).fetchone()
    if row is None:
        row = conn.execute(
            """
            SELECT e.* FROM executions e
            WHERE EXISTS (SELECT 1 FROM pending_events p WHERE p.execution_id = e.execution_id AND p.acked_at IS NULL)
            ORDER BY e.claimed_at, e.execution_id LIMIT 1
            """
        ).fetchone()
    return None if row is None else dict(row)


# --- 미전송 이벤트 -------------------------------------------------------------------------


def queue_event(conn: sqlite3.Connection, event: ExecutionEvent) -> None:
    with transaction(conn):
        conn.execute(
            "INSERT INTO pending_events (execution_id, seq, event_json) VALUES (?, ?, ?)",
            (event.execution_id, event.seq, event.model_dump_json()),
        )


def pop_pending(conn: sqlite3.Connection, execution_id: str) -> list[ExecutionEvent]:
    """아직 ack 되지 않은 이벤트를 seq 순으로. 지우지 않는다 — 전송 성공 후 `ack_event`."""
    rows = conn.execute(
        "SELECT event_json FROM pending_events WHERE execution_id = ? AND acked_at IS NULL ORDER BY seq",
        (execution_id,),
    ).fetchall()
    return [ExecutionEvent.model_validate_json(row["event_json"]) for row in rows]


def ack_event(conn: sqlite3.Connection, execution_id: str, seq: int, now: str | None = None) -> None:
    with transaction(conn):
        conn.execute(
            "UPDATE pending_events SET acked_at = ? WHERE execution_id = ? AND seq = ?",
            (now or utc_now(), execution_id, seq),
        )


def unack_from(conn: sqlite3.Connection, execution_id: str, seq: int) -> int:
    """중앙이 `sequence_gap` 으로 요구한 순번부터 다시 미전송으로 돌린다. 되돌린 건수 반환."""
    with transaction(conn):
        cursor = conn.execute(
            "UPDATE pending_events SET acked_at = NULL WHERE execution_id = ? AND seq >= ?",
            (execution_id, seq),
        )
    return cursor.rowcount


def pending_execution_ids(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        """
        SELECT DISTINCT p.execution_id FROM pending_events p JOIN executions e USING (execution_id)
        WHERE p.acked_at IS NULL ORDER BY e.claimed_at, p.execution_id
        """
    ).fetchall()
    return [row[0] for row in rows]


# --- 산출물 보존 ---------------------------------------------------------------------------


def save_outputs(
    conn: sqlite3.Connection, execution_id: str, outputs: list[tuple[ArtifactMeta, bytes]]
) -> None:
    with transaction(conn):
        conn.execute("DELETE FROM outputs WHERE execution_id = ?", (execution_id,))
        conn.executemany(
            "INSERT INTO outputs (execution_id, idx, meta_json, data) VALUES (?, ?, ?, ?)",
            [(execution_id, idx, meta.model_dump_json(), data) for idx, (meta, data) in enumerate(outputs)],
        )


def list_outputs(conn: sqlite3.Connection, execution_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT idx, meta_json, data, artifact_id FROM outputs WHERE execution_id = ? ORDER BY idx",
        (execution_id,),
    ).fetchall()
    return [
        {
            "idx": row["idx"], "meta": ArtifactMeta.model_validate_json(row["meta_json"]),
            "data": bytes(row["data"]), "artifact_id": row["artifact_id"],
        }
        for row in rows
    ]


def set_output_artifact(conn: sqlite3.Connection, execution_id: str, idx: int, artifact_id: str) -> None:
    with transaction(conn):
        conn.execute(
            "UPDATE outputs SET artifact_id = ? WHERE execution_id = ? AND idx = ?",
            (artifact_id, execution_id, idx),
        )
