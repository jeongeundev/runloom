-- phase 8 (service, 9-measure step 3 직전) 의 SCHEMA_VERSION 5 스키마 원문. 마이그레이션 테스트가 실제 v5 DB 를 만들 때 쓴다 — 고치지 않는다.
-- sessions.config_revision·executions 측정 칸·task_events·baseline_* 가 없다(phase 9 가 추가).

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
  discovered_json               TEXT NOT NULL DEFAULT '{}',
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
  skipped_json        TEXT NOT NULL DEFAULT '[]',  -- 체인에 못 들어간 이슈 [{key, title, reason}]
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
  status                   TEXT NOT NULL CHECK (status IN ('대기', '실행 가능', '실행 요청됨', '실행 중', '확인 필요', '완료', '실패')),
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
  status                   TEXT NOT NULL CHECK (status IN ('queued', 'accepted', 'running', 'result_ready', 'failed', 'unknown')),
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
  kind         TEXT NOT NULL CHECK (kind IN ('unknown_no_start', 'heartbeat_lost', 'timeout')),
  detail       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS artifacts (
  artifact_id  TEXT PRIMARY KEY,
  execution_id TEXT NOT NULL REFERENCES executions(execution_id),
  session_id   TEXT NOT NULL REFERENCES sessions(session_id),
  kind         TEXT NOT NULL CHECK (kind IN ('handoff_bundle', 'diagnosis_result', 'tool_trace', 'evidence', 'codex_jsonl', 'codex_stderr', 'diff', 'test_log_before', 'test_log_after', 'verification_log', 'report_output', 'code_change_result', 'review_comment', 'claude_jsonl', 'claude_stderr', 'generic_result', 'code_review_result')),
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
