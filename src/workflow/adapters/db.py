"""중앙 DB 연결과 스키마 (ADR-0002: 표준 sqlite3 + 명시적 SQL, ORM 없음).

제약의 원본은 docs/ARCHITECTURE.md "DB 제약과 실행 잠금". 허용 상태 CHECK 는 domain·contracts 의
상수에서 만들어 문자열을 두 곳에 적지 않는다.
"""

import sqlite3
from pathlib import Path

from workflow.contracts.v1 import ARTIFACT_KINDS
from workflow.domain.status import EXECUTION_STATUSES, USER_STATUS_LABELS

SCHEMA_VERSION = 1

OBSERVATION_KINDS = ("unknown_no_start", "heartbeat_lost", "timeout")


def connect(path: str | Path) -> sqlite3.Connection:
    """외래키 ON, WAL, busy_timeout 5초(ARCHITECTURE 배포 절), Row, 명시적 BEGIN."""
    conn = sqlite3.connect(str(path), isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def _in(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in values)


_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS schema_version (
  version INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
  session_id  TEXT PRIMARY KEY,
  created_at  TEXT NOT NULL,
  is_operator INTEGER NOT NULL DEFAULT 0 CHECK (is_operator IN (0, 1))
);

CREATE TABLE IF NOT EXISTS agents (
  agent_id                      TEXT PRIMARY KEY,
  name                          TEXT NOT NULL,
  owner_scope                   TEXT NOT NULL CHECK (owner_scope IN ('personal', 'team', 'company')),
  connection_type               TEXT NOT NULL CHECK (connection_type IN ('local', 'api')),
  connector_id                  TEXT,
  local_registration_id         TEXT,
  repository_id                 TEXT,
  base_commit                   TEXT,
  verification_profile_ids_json TEXT NOT NULL DEFAULT '[]',
  api_url                       TEXT,
  credential_ref                TEXT,
  capabilities_json             TEXT NOT NULL,
  discovered_json               TEXT NOT NULL DEFAULT '{{}}',
  connection_state              TEXT NOT NULL CHECK (connection_state IN ('online', 'offline', 'unknown')),
  last_seen_at                  TEXT,
  shared_to_all_sessions        INTEGER NOT NULL DEFAULT 0 CHECK (shared_to_all_sessions IN (0, 1))
);

CREATE TABLE IF NOT EXISTS connect_codes (
  code       TEXT PRIMARY KEY,
  issued_at  TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  used_at    TEXT,
  revoked_at TEXT
);

CREATE TABLE IF NOT EXISTS connectors (
  connector_id         TEXT PRIMARY KEY,
  token_sha256         TEXT NOT NULL UNIQUE,
  created_at           TEXT NOT NULL,
  revoked_at           TEXT,
  last_seen_at         TEXT,
  current_execution_id TEXT
);

CREATE TABLE IF NOT EXISTS tasks (
  task_id                  TEXT PRIMARY KEY,
  session_id               TEXT NOT NULL REFERENCES sessions(session_id),
  title                    TEXT NOT NULL,
  request                  TEXT NOT NULL,
  kind                     TEXT NOT NULL CHECK (kind IN ('diagnosis', 'code_change')),
  required_capability_json TEXT NOT NULL,
  selection_mode           TEXT NOT NULL CHECK (selection_mode IN ('auto', 'manual')),
  chosen_agent_id          TEXT,
  run_mode                 TEXT NOT NULL CHECK (run_mode IN ('auto', 'manual')),
  completion_mode          TEXT NOT NULL CHECK (completion_mode IN ('auto', 'review')),
  criteria_json            TEXT NOT NULL,
  predecessor_task_id      TEXT REFERENCES tasks(task_id),
  revision                 INTEGER NOT NULL CHECK (revision >= 1),
  target_json              TEXT NOT NULL,
  status                   TEXT NOT NULL CHECK (status IN ({_in(USER_STATUS_LABELS)})),
  status_reason            TEXT NOT NULL,
  finished_at              TEXT,
  review_decision          TEXT CHECK (review_decision IN ('approve', 'request_changes', 'close')),
  merge_confirmed_at       TEXT,
  created_at               TEXT NOT NULL,
  CHECK (predecessor_task_id IS NULL OR predecessor_task_id != task_id)
);

CREATE TABLE IF NOT EXISTS selection_records (
  task_id     TEXT PRIMARY KEY REFERENCES tasks(task_id),
  record_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS executions (
  execution_id             TEXT PRIMARY KEY,
  task_id                  TEXT NOT NULL REFERENCES tasks(task_id),
  attempt_no               INTEGER NOT NULL CHECK (attempt_no >= 1),
  start_key                TEXT NOT NULL,
  agent_id                 TEXT NOT NULL,
  kind                     TEXT NOT NULL CHECK (kind IN ('diagnosis', 'code_change')),
  request_json             TEXT NOT NULL,
  status                   TEXT NOT NULL CHECK (status IN ({_in(EXECUTION_STATUSES)})),
  last_event_seq           INTEGER NOT NULL DEFAULT 0,
  assigned_connector_id    TEXT,
  result_artifact_id       TEXT,
  failed_code              TEXT,
  failed_message           TEXT,
  process_stopped          INTEGER CHECK (process_stopped IS NULL OR process_stopped IN (0, 1)),
  created_at               TEXT NOT NULL,
  accepted_at              TEXT,
  started_at               TEXT,
  finished_at              TEXT,
  released_at              TEXT,
  predecessor_execution_id TEXT,
  UNIQUE (task_id, attempt_no),
  UNIQUE (task_id, start_key)
);

-- 활성 잠금: released_at 이 NULL 인 행은 task 당 하나. 시간 경과로 해제하지 않는다.
CREATE UNIQUE INDEX IF NOT EXISTS ux_executions_active
  ON executions(task_id) WHERE released_at IS NULL;

CREATE TABLE IF NOT EXISTS execution_events (
  execution_id TEXT NOT NULL REFERENCES executions(execution_id),
  seq          INTEGER NOT NULL CHECK (seq >= 1),
  type         TEXT NOT NULL
               CHECK (type IN ('accepted', 'started', 'progress', 'result_ready', 'failed')),
  occurred_at  TEXT NOT NULL,
  received_at  TEXT NOT NULL,
  data_json    TEXT NOT NULL,
  actor        TEXT NOT NULL,
  PRIMARY KEY (execution_id, seq)
);

-- 서버 관찰. unknown 판정 근거이며 실행 주체의 seq 를 소비하지 않는다.
CREATE TABLE IF NOT EXISTS execution_observations (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  execution_id TEXT NOT NULL REFERENCES executions(execution_id),
  observed_at  TEXT NOT NULL,
  kind         TEXT NOT NULL CHECK (kind IN ({_in(OBSERVATION_KINDS)})),
  detail       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS artifacts (
  artifact_id  TEXT PRIMARY KEY,
  execution_id TEXT NOT NULL REFERENCES executions(execution_id),
  session_id   TEXT NOT NULL REFERENCES sessions(session_id),
  kind         TEXT NOT NULL CHECK (kind IN ({_in(ARTIFACT_KINDS)})),
  name         TEXT NOT NULL,
  content_type TEXT NOT NULL,
  sha256       TEXT NOT NULL,
  size         INTEGER NOT NULL CHECK (size >= 0),
  store_ref    TEXT NOT NULL,
  created_at   TEXT NOT NULL,
  UNIQUE (execution_id, kind, sha256)
);

CREATE TABLE IF NOT EXISTS task_verdicts (
  task_id      TEXT NOT NULL REFERENCES tasks(task_id),
  execution_id TEXT NOT NULL REFERENCES executions(execution_id),
  verdict_json TEXT NOT NULL,
  decided_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS diagnosis_usage (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id   TEXT NOT NULL,
  execution_id TEXT NOT NULL,
  started_at   TEXT NOT NULL
);
"""


def init_schema(conn: sqlite3.Connection) -> None:
    """멱등. 다른 버전이 기록돼 있으면 마이그레이션 도구가 없으므로 실패시킨다."""
    conn.executescript(_SCHEMA)
    row = conn.execute("SELECT version FROM schema_version").fetchone()
    if row is None:
        conn.execute("INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,))
    elif row[0] != SCHEMA_VERSION:
        raise RuntimeError(f"schema_version {row[0]} 은 지원하지 않습니다 (기대 {SCHEMA_VERSION})")
