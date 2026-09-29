"""중앙 DB 연결과 스키마 (ADR-0002: 표준 sqlite3 + 명시적 SQL, ORM 없음).

제약의 원본은 docs/ARCHITECTURE.md "DB 제약과 실행 잠금". 허용 상태 CHECK 는 domain·contracts 의
상수에서 만들어 문자열을 두 곳에 적지 않는다.
"""

import secrets
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from workflow.contracts.v1 import ARTIFACT_KINDS, BUILTIN_KINDS, BUILTIN_RULES
from workflow.domain.status import EXECUTION_STATUSES, USER_STATUS_LABELS

SCHEMA_VERSION = 9

# phase 8 이 기존 세션에 더하는 내장 종류 (ADR-0014). 같은 이름의 사용자 정의 종류가 있으면 마이그레이션을 되돌린다.
PHASE8_KIND_NAMES = ("bug_fix", "code_review")
# phase 13 이 모든 세션에서 지우는 진단 데모 내장 종류 (ADR-0019 결정 4). 쓰는 Task·사용자 규칙이 있으면 되돌린다.
PHASE13_REMOVED_KIND_NAMES = ("diagnosis", "code_change")

OBSERVATION_KINDS = ("unknown_no_start", "heartbeat_lost", "timeout")

# phase 9 (ADR-0015): Task 이벤트 종류. ARCHITECTURE "측정 — phase 9" 이벤트 기록 규칙.
TASK_EVENT_TYPES = ("status_changed", "blocked", "ready")


def connect(path: str | Path) -> sqlite3.Connection:
    """외래키 ON, WAL, busy_timeout 5초(ARCHITECTURE 배포 절), Row, 명시적 BEGIN.

    `check_same_thread=False`: FastAPI 는 요청 하나의 의존성 준비·핸들러·정리를 서로 다른
    threadpool 스레드에서 돌린다. 연결은 요청마다 새로 열어 순차적으로만 쓰고 스레드 간에
    동시에 공유하지 않는다."""
    conn = sqlite3.connect(str(path), isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def _in(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in values)


def _artifacts_table(name: str) -> str:
    """v4 → v5 가 kind CHECK 를 넓히려고 같은 정의로 다시 만든다(SQLite 는 CHECK 를 바꾸지 못한다)."""
    return f"""CREATE TABLE IF NOT EXISTS {name} (
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
);"""


# v5 (phase 8, ADR-0014): GitHub 업무 순환. 중복 키는 ARCHITECTURE "GitHub 업무 순환 — 중복 키" 표.
# 세션 소유는 source → session, Task → session 으로 따라가며 repo 가 같은 트랜잭션에서 검사한다.
_V5_TABLES = """
-- 운영자가 연결한 저장소. config_json 은 GitHubSourceConfig JSON(토큰 필드 없음 — 값은 WORKFLOW_GITHUB_TOKEN 에만).
CREATE TABLE IF NOT EXISTS github_sources (
  source_id            TEXT PRIMARY KEY,                    -- 'ghs-' + 8 hex
  session_id           TEXT NOT NULL REFERENCES sessions(session_id),
  repository_full_name TEXT NOT NULL,                       -- config_json 과 같다
  config_json          TEXT NOT NULL,
  cursor               TEXT,                                -- 수집 커서. 실패 페이지는 넘기지 않는다
  cursor_updated_at    TEXT,
  created_at           TEXT NOT NULL,
  updated_at           TEXT NOT NULL,
  UNIQUE (session_id, repository_full_name)
);

CREATE TABLE IF NOT EXISTS github_assignee_bindings (
  source_id      TEXT NOT NULL REFERENCES github_sources(source_id),
  github_user_id INTEGER NOT NULL CHECK (github_user_id >= 1),
  github_login   TEXT NOT NULL,                             -- 표시용 (바뀔 수 있다)
  agent_id       TEXT NOT NULL REFERENCES agents(agent_id),
  updated_at     TEXT NOT NULL,
  PRIMARY KEY (source_id, github_user_id)
);

-- 원본 이슈 → Task. 같은 digest 는 무시, 다른 digest 는 source_revision + 1, 오래된 updated_at 은 버린다.
CREATE TABLE IF NOT EXISTS source_issues (
  source_id        TEXT NOT NULL REFERENCES github_sources(source_id),
  github_issue_id  INTEGER NOT NULL CHECK (github_issue_id >= 1),
  issue_number     INTEGER NOT NULL CHECK (issue_number >= 1),
  task_id          TEXT NOT NULL UNIQUE REFERENCES tasks(task_id),
  source_revision  INTEGER NOT NULL CHECK (source_revision >= 1),
  snapshot_json    TEXT NOT NULL,                           -- GitHubIssueSnapshot JSON
  snapshot_digest  TEXT NOT NULL,
  issue_updated_at TEXT NOT NULL,
  state            TEXT NOT NULL CHECK (state IN ('open', 'closed')),
  created_at       TEXT NOT NULL,
  updated_at       TEXT NOT NULL,
  PRIMARY KEY (source_id, github_issue_id)
);

-- 결과가 만든 후속 Task. 같은 원인 실행·같은 종류는 하나.
CREATE TABLE IF NOT EXISTS followup_links (
  session_id         TEXT NOT NULL REFERENCES sessions(session_id),
  cause_execution_id TEXT NOT NULL REFERENCES executions(execution_id),
  to_kind            TEXT NOT NULL,
  task_id            TEXT NOT NULL UNIQUE REFERENCES tasks(task_id),
  rules_revision     INTEGER NOT NULL,
  created_at         TEXT NOT NULL,
  PRIMARY KEY (session_id, cause_execution_id, to_kind),
  FOREIGN KEY (session_id, to_kind) REFERENCES kinds(session_id, kind)
);

CREATE TABLE IF NOT EXISTS human_requests (
  request_id    TEXT PRIMARY KEY,                           -- 'hr-' + 8 hex
  task_id       TEXT NOT NULL REFERENCES tasks(task_id),
  code          TEXT NOT NULL,
  question      TEXT NOT NULL,
  cause_key     TEXT NOT NULL,
  task_revision INTEGER NOT NULL CHECK (task_revision >= 1), -- 요청을 만든 때의 Task revision
  revision      INTEGER NOT NULL CHECK (revision >= 1),      -- 낙관적 잠금 (expected_revision)
  state         TEXT NOT NULL CHECK (state IN ('open', 'answered')),
  created_at    TEXT NOT NULL,
  answered_at   TEXT,
  UNIQUE (task_id, cause_key)
);

CREATE TABLE IF NOT EXISTS human_responses (
  request_id        TEXT NOT NULL REFERENCES human_requests(request_id),
  response_id       TEXT NOT NULL,
  action            TEXT NOT NULL,
  text              TEXT NOT NULL,
  agent_id          TEXT,                                     -- choose_agent 응답이 지정한 Agent
  expected_revision INTEGER NOT NULL,
  task_revision     INTEGER NOT NULL CHECK (task_revision >= 1), -- 이 응답으로 생긴 Task revision
  created_at        TEXT NOT NULL,
  PRIMARY KEY (request_id, response_id)
);

-- 원본 이슈 댓글 outbox (SourceDelivery). 워커 트랜잭션 밖에서 보낸다.
CREATE TABLE IF NOT EXISTS source_deliveries (
  delivery_id   TEXT PRIMARY KEY,                           -- 'dlv-' + 8 hex
  source_id     TEXT NOT NULL REFERENCES github_sources(source_id),
  task_id       TEXT NOT NULL REFERENCES tasks(task_id),
  issue_number  INTEGER NOT NULL CHECK (issue_number >= 1),
  body_revision INTEGER NOT NULL CHECK (body_revision >= 1),
  body_digest   TEXT NOT NULL,
  body          TEXT NOT NULL,
  state         TEXT NOT NULL CHECK (state IN ('pending', 'sending', 'delivered', 'unknown', 'failed')),
  comment_id    INTEGER,
  attempts      INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
  next_at       TEXT,
  last_error    TEXT,
  created_at    TEXT NOT NULL,
  updated_at    TEXT NOT NULL,
  UNIQUE (task_id, body_revision),
  CHECK (state != 'delivered' OR comment_id IS NOT NULL)
);
"""


# v6 (phase 9, ADR-0015): 측정. 새 칸은 CREATE TABLE 과 v5 → v6 ALTER 가 같은 정의를 쓴다.
# 실행 칸은 모두 NULL 허용 — NULL = 모름(0 과 다름).
_SESSION_CONFIG_REVISION = "config_revision INTEGER NOT NULL DEFAULT 1 CHECK (config_revision >= 1)"
_EXECUTION_MEASURE_COLUMNS = (
    "config_revision INTEGER",
    "folder_commit TEXT",
    "folder_dirty INTEGER CHECK (folder_dirty IS NULL OR folder_dirty IN (0, 1))",
    "cost_usd REAL CHECK (cost_usd IS NULL OR cost_usd >= 0)",
    "input_tokens INTEGER CHECK (input_tokens IS NULL OR input_tokens >= 0)",
    "output_tokens INTEGER CHECK (output_tokens IS NULL OR output_tokens >= 0)",
)
# 원본 이슈를 닫은 병합 PR (step 11 — 도입 후 완료 시각). NULL = 아직 모름/병합 없음, merge_checked_at = 마지막 조회.
_SOURCE_ISSUE_MERGE_COLUMNS = (
    "merged_pr_number INTEGER",
    "pr_merged_at TEXT",
    "merge_checked_at TEXT",
)

_V6_TABLES = f"""
-- Task 상태·준비 이력. 추가 전용 — UPDATE·DELETE 경로 없음. revision 들은 기록 시점의 Task·세션 값.
CREATE TABLE IF NOT EXISTS task_events (
  id              INTEGER PRIMARY KEY,
  task_id         TEXT NOT NULL REFERENCES tasks(task_id),
  session_id      TEXT NOT NULL REFERENCES sessions(session_id),
  type            TEXT NOT NULL CHECK (type IN ({_in(TASK_EVENT_TYPES)})),
  task_revision   INTEGER NOT NULL,
  config_revision INTEGER NOT NULL,
  occurred_at     TEXT NOT NULL,                            -- 서버 시계
  data_json       TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_task_events_task ON task_events(task_id, id);
CREATE INDEX IF NOT EXISTS ix_task_events_session ON task_events(session_id, occurred_at);

-- 기준선: 도입 전 이슈 → 병합 PR. 이슈 하나에 병합 PR 이 여럿이면 행도 여럿.
CREATE TABLE IF NOT EXISTS baseline_items (
  source_id       TEXT NOT NULL REFERENCES github_sources(source_id),
  issue_number    INTEGER NOT NULL,
  issue_title     TEXT NOT NULL,
  issue_opened_at TEXT NOT NULL,
  pr_number       INTEGER NOT NULL,
  pr_merged_at    TEXT NOT NULL,
  fetched_at      TEXT NOT NULL,
  PRIMARY KEY (source_id, issue_number, pr_number)
);

-- 소스별 마지막 기준선 가져오기. opened_before = 그 소스의 github_sources.created_at.
CREATE TABLE IF NOT EXISTS baseline_imports (
  source_id     TEXT PRIMARY KEY REFERENCES github_sources(source_id),
  opened_before TEXT NOT NULL,
  fetched_at    TEXT NOT NULL,
  item_count    INTEGER NOT NULL
);
"""

# source_issues 는 v5 정의(_V5_TABLES)를 4 → 5 가 그대로 쓰므로, v6 칸은 빈 DB·5 → 6 모두 ALTER 로 더한다.
_V6_SOURCE_ISSUE_ALTERS = "".join(
    f"ALTER TABLE source_issues ADD COLUMN {column};\n" for column in _SOURCE_ISSUE_MERGE_COLUMNS
)

# v7 (phase 11, ADR-0017): `all_open` 소스 Task 의 실행 지시. NULL = 지시 전. 빈 DB·6 → 7 모두 ALTER 로 더한다.
_SOURCE_ISSUE_DELEGATION_COLUMNS = (
    "delegated_at TEXT",
    "delegated_by TEXT CHECK (delegated_by IS NULL OR delegated_by IN ('operator', 'label'))",
)
_V7_SOURCE_ISSUE_ALTERS = "".join(
    f"ALTER TABLE source_issues ADD COLUMN {column};\n" for column in _SOURCE_ISSUE_DELEGATION_COLUMNS
)

# v8 (phase 12, ADR-0018): 결과 브랜치 push 보고·초안 PR 대기열·알림 대기열. 새 칸은 빈 DB·7 → 8 모두 ALTER 로 더한다.
_EXECUTION_PUSH_COLUMN = "branch_pushed INTEGER CHECK (branch_pushed IS NULL OR branch_pushed IN (0, 1))"
_V8_EXECUTION_ALTERS = f"ALTER TABLE executions ADD COLUMN {_EXECUTION_PUSH_COLUMN};\n"
PULL_REQUEST_STATES = ("pending", "open", "merged", "closed", "failed")
NOTIFICATION_EVENTS = ("human_request", "pr_opened", "task_failed")
NOTIFICATION_STATES = ("pending", "sent", "failed", "skipped")

_V8_TABLES = f"""
-- 수정 Task 당 초안 PR 하나(재작업은 같은 브랜치라 같은 PR). last_error 는 분류 문구만 — 응답 본문·토큰 없음.
CREATE TABLE IF NOT EXISTS task_pull_requests (
  task_id              TEXT PRIMARY KEY REFERENCES tasks(task_id),
  session_id           TEXT NOT NULL,
  source_id            TEXT NOT NULL REFERENCES github_sources(source_id),
  repository_full_name TEXT NOT NULL,
  issue_number         INTEGER NOT NULL,
  head_branch          TEXT NOT NULL,                       -- task/<task_id>
  fix_execution_id     TEXT NOT NULL,
  review_execution_id  TEXT NOT NULL,
  state                TEXT NOT NULL CHECK (state IN ({_in(PULL_REQUEST_STATES)})),
  pr_number            INTEGER,
  pr_url               TEXT,
  draft                INTEGER CHECK (draft IS NULL OR draft IN (0, 1)),
  attempts             INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
  next_at              TEXT,
  last_error           TEXT,
  created_at           TEXT NOT NULL,
  updated_at           TEXT NOT NULL,
  merged_at            TEXT,
  closed_at            TEXT,
  CHECK (state NOT IN ('open', 'merged', 'closed') OR pr_number IS NOT NULL)
);

-- 알림 대기열(step 7 이 쓰고 보낸다). URL 칸은 없다 — 보낼 때 비밀 파일에서 읽는다.
CREATE TABLE IF NOT EXISTS notifications (
  notification_id TEXT PRIMARY KEY,                         -- 'ntf-' + 8 hex
  session_id      TEXT NOT NULL,
  event           TEXT NOT NULL CHECK (event IN ({_in(NOTIFICATION_EVENTS)})),
  task_id         TEXT REFERENCES tasks(task_id),
  dedupe_key      TEXT NOT NULL UNIQUE,
  content         TEXT NOT NULL,
  payload_json    TEXT NOT NULL,
  state           TEXT NOT NULL CHECK (state IN ({_in(NOTIFICATION_STATES)})),
  attempts        INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
  next_at         TEXT,
  last_error      TEXT,
  created_at      TEXT NOT NULL,
  sent_at         TEXT
);
"""


_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS schema_version (
  version INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
  session_id  TEXT PRIMARY KEY,
  created_at  TEXT NOT NULL,
  is_operator INTEGER NOT NULL DEFAULT 0 CHECK (is_operator IN (0, 1)),
  {_SESSION_CONFIG_REVISION}                               -- 워크스페이스 설정 번호 (v6)
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
  shared_to_all_sessions        INTEGER NOT NULL DEFAULT 0 CHECK (shared_to_all_sessions IN (0, 1)),
  demo_scripted                 INTEGER NOT NULL DEFAULT 0 CHECK (demo_scripted IN (0, 1))
);

-- 심사자 세션이 카탈로그(shared_to_all_sessions=1) 에서 등록한 Agent. 세션별로 격리된다.
CREATE TABLE IF NOT EXISTS session_agents (
  session_id    TEXT NOT NULL REFERENCES sessions(session_id),
  agent_id      TEXT NOT NULL REFERENCES agents(agent_id),
  registered_at TEXT NOT NULL,
  PRIMARY KEY (session_id, agent_id)
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
  current_execution_id TEXT,
  supported_kinds_json TEXT                -- 마지막 claim 의 ClaimRequest.supported_kinds. NULL = 구버전 (v5)
);

-- 입구 토큰: 워크스페이스(세션)가 발급해 외부(n8n)가 업무를 넣을 때 쓴다 (ADR-0010). 원문은 저장하지 않는다.
CREATE TABLE IF NOT EXISTS source_tokens (
  token_id      TEXT PRIMARY KEY,                       -- 'src-' + 8 hex
  session_id    TEXT NOT NULL REFERENCES sessions(session_id),
  source        TEXT NOT NULL CHECK (source IN ('n8n')),
  token_sha256  TEXT NOT NULL UNIQUE,
  label         TEXT NOT NULL,                          -- 사람이 붙인 이름 (빈 문자열 허용)
  created_at    TEXT NOT NULL,
  last_used_at  TEXT,
  revoked_at    TEXT
);

-- Chain: 세션이 "업무 가져오기" 또는 입구 API(n8n) 로 만든 Task 묶음 (화면 라벨 "워크플로우"). 순서는 Task 의
-- predecessor_task_id 체인으로만 표현한다. `workflow_id` 는 진단 대상 자동화 ID 라 여기 쓰지 않는다.
-- callback 은 체인당 1회 — `callback_sent_at` 이 차면 끝. 실패는 attempts·next_at 으로 재시도한다 (ADR-0010).
CREATE TABLE IF NOT EXISTS chains (
  chain_id            TEXT PRIMARY KEY,
  session_id          TEXT NOT NULL REFERENCES sessions(session_id),
  title               TEXT NOT NULL,
  source              TEXT NOT NULL CHECK (source IN ('github', 'jira', 'manual', 'n8n')),
  skipped_json        TEXT NOT NULL DEFAULT '[]',  -- 체인에 못 들어간 이슈 [{{key, title, reason}}]
  created_at          TEXT NOT NULL,
  started_at          TEXT,
  items_json          TEXT,                        -- n8n 이 보낸 항목 원문 배열 (다른 출처는 NULL)
  callback_url        TEXT,                        -- 없으면 아무것도 보내지 않는다
  callback_sent_at    TEXT,
  callback_attempts   INTEGER NOT NULL DEFAULT 0 CHECK (callback_attempts >= 0),
  callback_next_at    TEXT,
  callback_last_error TEXT
);

-- 업무 종류·후속 규칙은 워크스페이스(세션)별 등록이다 (ADR-0009). 내장은 세션 생성 시 seed 된다.
CREATE TABLE IF NOT EXISTS kinds (
  session_id  TEXT NOT NULL REFERENCES sessions(session_id),
  kind        TEXT NOT NULL,
  spec_json   TEXT NOT NULL,      -- KindSpec JSON. kind 필드는 컬럼과 같다
  created_at  TEXT NOT NULL,
  PRIMARY KEY (session_id, kind)
);

CREATE TABLE IF NOT EXISTS succession_rules (
  rule_id     TEXT PRIMARY KEY,
  session_id  TEXT NOT NULL REFERENCES sessions(session_id),
  from_kind   TEXT NOT NULL,
  to_kind     TEXT NOT NULL,
  rule_json   TEXT NOT NULL,      -- SuccessorRule JSON. from_kind·to_kind 는 컬럼과 같다
  created_at  TEXT NOT NULL,
  UNIQUE (session_id, from_kind, to_kind),
  FOREIGN KEY (session_id, from_kind) REFERENCES kinds(session_id, kind),
  FOREIGN KEY (session_id, to_kind)   REFERENCES kinds(session_id, kind)
);

CREATE TABLE IF NOT EXISTS tasks (
  task_id                  TEXT PRIMARY KEY,
  session_id               TEXT NOT NULL REFERENCES sessions(session_id),
  title                    TEXT NOT NULL,
  request                  TEXT NOT NULL,
  kind                     TEXT NOT NULL,
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
  chain_id                 TEXT REFERENCES chains(chain_id),  -- 직접 등록 Task 는 NULL
  source_ref               TEXT,                              -- 가져온 이슈 키 (#42, OPS-42)
  CHECK (predecessor_task_id IS NULL OR predecessor_task_id != task_id),
  FOREIGN KEY (session_id, kind) REFERENCES kinds(session_id, kind)
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
  kind                     TEXT NOT NULL,                     -- Task 에서 복사된다
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
  {",\n  ".join(_EXECUTION_MEASURE_COLUMNS)},               -- 측정 (v6)
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

{_artifacts_table("artifacts")}
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
""" + _V5_TABLES + _V6_TABLES + _V6_SOURCE_ISSUE_ALTERS + _V7_SOURCE_ISSUE_ALTERS + _V8_EXECUTION_ALTERS + _V8_TABLES


def _statements(script: str) -> list[str]:
    """스크립트를 문장 단위로 — `executescript` 는 먼저 COMMIT 하므로 트랜잭션 안에서 쓸 수 없다."""
    out, buf = [], ""
    for line in script.splitlines(keepends=True):
        buf += line
        if sqlite3.complete_statement(buf):
            out.append(buf.strip())
            buf = ""
    return out


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _seed_phase8_kinds(conn: sqlite3.Connection, now: str) -> None:
    """기존 세션마다 phase 8 내장 종류와 그 둘을 잇는 내장 규칙을 넣는다. 이미 지운 기존 내장 규칙은 되살리지 않는다."""
    specs = [spec for spec in BUILTIN_KINDS if spec.kind in PHASE8_KIND_NAMES]
    rules = [rule for rule in BUILTIN_RULES if {rule.from_kind, rule.to_kind} <= set(PHASE8_KIND_NAMES)]
    for (session_id,) in conn.execute("SELECT session_id FROM sessions ORDER BY session_id").fetchall():
        for spec in specs:
            conn.execute(
                "INSERT INTO kinds (session_id, kind, spec_json, created_at) VALUES (?, ?, ?, ?)",
                (session_id, spec.kind, spec.model_dump_json(), now),
            )
        for rule in rules:
            conn.execute(
                "INSERT INTO succession_rules (rule_id, session_id, from_kind, to_kind, rule_json, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (f"rule-{secrets.token_hex(6)}", session_id, rule.from_kind, rule.to_kind,
                 rule.model_dump_json(), now),
            )


def _migrate_4_to_5(conn: sqlite3.Connection) -> None:
    """호출자가 연 트랜잭션 안에서 실행한다. 실패하면 호출자가 전체를 되돌린다."""
    placeholders = ", ".join("?" * len(PHASE8_KIND_NAMES))
    conflicts = conn.execute(
        f"SELECT session_id, kind FROM kinds WHERE kind IN ({placeholders}) ORDER BY session_id, kind",
        PHASE8_KIND_NAMES,
    ).fetchall()
    if conflicts:
        listed = ", ".join(f"{r[0]}:{r[1]}" for r in conflicts)
        raise RuntimeError(
            f"schema_version 4 → {SCHEMA_VERSION} 마이그레이션 중단 — 내장 종류와 이름이 같은 사용자 정의 종류: {listed}"
        )
    conn.execute("ALTER TABLE connectors ADD COLUMN supported_kinds_json TEXT")
    # artifacts.kind CHECK 에 code_review_result 추가 — 새 테이블로 옮긴다. artifacts 를 참조하는 FK 는 없다.
    conn.execute(_artifacts_table("artifacts_v5"))
    conn.execute("INSERT INTO artifacts_v5 SELECT * FROM artifacts")
    conn.execute("DROP TABLE artifacts")
    conn.execute("ALTER TABLE artifacts_v5 RENAME TO artifacts")
    for statement in _statements(_V5_TABLES):
        conn.execute(statement)
    _seed_phase8_kinds(conn, _now())
    if conn.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise RuntimeError("마이그레이션 뒤 외래키 검사 실패")
    conn.execute("UPDATE schema_version SET version = 5")


def _migrate_5_to_6(conn: sqlite3.Connection) -> None:
    """호출자가 연 트랜잭션 안에서 실행한다. 기존 행은 건드리지 않고 칸·테이블만 더한다 —
    세션 config_revision 은 1, 실행 측정 칸·원본 이슈 병합 칸은 NULL(모름). 과거 이벤트를 추정해 채우지 않는다."""
    conn.execute(f"ALTER TABLE sessions ADD COLUMN {_SESSION_CONFIG_REVISION}")
    for column in _EXECUTION_MEASURE_COLUMNS:
        conn.execute(f"ALTER TABLE executions ADD COLUMN {column}")
    for statement in _statements(_V6_SOURCE_ISSUE_ALTERS):
        conn.execute(statement)
    for statement in _statements(_V6_TABLES):
        conn.execute(statement)
    if conn.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise RuntimeError("마이그레이션 뒤 외래키 검사 실패")
    conn.execute("UPDATE schema_version SET version = 6")


def _migrate_6_to_7(conn: sqlite3.Connection) -> None:
    """호출자가 연 트랜잭션 안에서 실행한다. 지시 칸만 더한다 — 기존 원본 이슈는 NULL(옛 소스는 `filtered` 라 보지 않는다)."""
    for statement in _statements(_V7_SOURCE_ISSUE_ALTERS):
        conn.execute(statement)
    conn.execute("UPDATE schema_version SET version = 7")


def _migrate_7_to_8(conn: sqlite3.Connection) -> None:
    """호출자가 연 트랜잭션 안에서 실행한다. push 칸·두 대기열을 더하고, 이미 받은 `result_ready` 이벤트에 남은
    `branch_pushed` 만 칸으로 옮긴다(보고 없으면 NULL — 추정하지 않는다)."""
    for statement in _statements(_V8_EXECUTION_ALTERS + _V8_TABLES):
        conn.execute(statement)
    conn.execute(
        "UPDATE executions SET branch_pushed = ("
        " SELECT json_extract(ev.data_json, '$.branch_pushed') FROM execution_events ev"
        " WHERE ev.execution_id = executions.execution_id AND ev.type = 'result_ready'"
        " AND json_type(ev.data_json, '$.branch_pushed') IN ('true', 'false'))"
    )
    if conn.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise RuntimeError("마이그레이션 뒤 외래키 검사 실패")
    conn.execute("UPDATE schema_version SET version = 8")


def _migrate_8_to_9(conn: sqlite3.Connection) -> None:
    """호출자가 연 트랜잭션 안에서 실행한다. 모든 세션에서 `diagnosis`·`code_change` 종류와 그 둘의 내장 규칙
    (`diagnosis → code_change`)을 지운다. 그 종류의 Task·실행이 있거나 그 쌍 밖의 규칙(사용자 등록)이 그 종류를
    가리키면 지우지 않고 중단한다 — 칸·표는 그대로 둔다(ADR-0019 결정 5)."""
    names = PHASE13_REMOVED_KIND_NAMES
    placeholders = ", ".join("?" * len(names))
    in_use = conn.execute(
        f"SELECT session_id, kind, COUNT(*) FROM tasks WHERE kind IN ({placeholders})"
        " GROUP BY session_id, kind ORDER BY session_id, kind",
        names,
    ).fetchall()
    executions = conn.execute(
        f"SELECT t.session_id, e.kind, COUNT(*) FROM executions e JOIN tasks t ON t.task_id = e.task_id"
        f" WHERE e.kind IN ({placeholders}) GROUP BY t.session_id, e.kind ORDER BY t.session_id, e.kind",
        names,
    ).fetchall()
    rules = conn.execute(
        f"SELECT session_id, from_kind, to_kind FROM succession_rules"
        f" WHERE (from_kind IN ({placeholders}) OR to_kind IN ({placeholders}))"
        " AND NOT (from_kind = 'diagnosis' AND to_kind = 'code_change') ORDER BY session_id, from_kind, to_kind",
        names + names,
    ).fetchall()
    if in_use or executions or rules:
        listed = [f"{r[0]}:{r[1]} 업무 {r[2]}건" for r in in_use]
        listed += [f"{r[0]}:{r[1]} 실행 {r[2]}건" for r in executions]
        listed += [f"{r[0]}:{r[1]}→{r[2]} 규칙" for r in rules]
        raise RuntimeError(
            f"schema_version 8 → 9 마이그레이션 중단 — 지울 종류(diagnosis·code_change)를 쓰는 행: {', '.join(listed)}"
        )
    conn.execute("DELETE FROM succession_rules WHERE from_kind = 'diagnosis' AND to_kind = 'code_change'")
    conn.execute(f"DELETE FROM kinds WHERE kind IN ({placeholders})", names)
    if conn.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise RuntimeError("마이그레이션 뒤 외래키 검사 실패")
    conn.execute("UPDATE schema_version SET version = 9")


def init_schema(conn: sqlite3.Connection) -> None:
    """멱등. 빈 DB 는 새로 만들고, 4~8 은 9 까지 차례로(4 → 5 → 6 → 7 → 8 → 9) 한 트랜잭션으로 올린다
    (데이터 보존, 실패하면 원래 버전 그대로).
    그 밖의 버전은 지원하지 않는다 — 3 이하는 `WORKFLOW_RESET_DB=1` 재생성 대상이다."""
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'schema_version'"
    ).fetchone()
    if exists is None:
        conn.executescript(_SCHEMA)
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute("SELECT version FROM schema_version").fetchone()
        if row is None:
            conn.execute("INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,))
        elif row[0] in (4, 5, 6, 7, 8):
            steps = (_migrate_4_to_5, _migrate_5_to_6, _migrate_6_to_7, _migrate_7_to_8, _migrate_8_to_9)
            for step in steps[row[0] - 4:]:
                step(conn)
        elif row[0] != SCHEMA_VERSION:
            raise RuntimeError(f"schema_version {row[0]} 은 지원하지 않습니다 (기대 {SCHEMA_VERSION})")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")
