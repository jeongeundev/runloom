-- phase 14 (service, 15-team step 2 직전) 의 SCHEMA_VERSION 10 스키마 원문(빈 DB 의 sqlite_master). 마이그레이션 테스트가
-- 실제 v10 DB 를 만들 때 쓴다 — 고치지 않는다. login_sessions·member_invites·members 계정 칸·
-- work_items.requested_by_member_id·human_responses.member_id·notifications 받는 사람 칸·connect_codes 발급자·
-- connectors 소유자가 없다(phase 15 가 추가).

CREATE TABLE schema_version (
  version INTEGER NOT NULL
);

CREATE TABLE sessions (
  session_id  TEXT PRIMARY KEY,
  created_at  TEXT NOT NULL,
  is_operator INTEGER NOT NULL DEFAULT 0 CHECK (is_operator IN (0, 1)),
  config_revision INTEGER NOT NULL DEFAULT 1 CHECK (config_revision >= 1)                               -- 워크스페이스 설정 번호 (v6)
);

CREATE TABLE agents (
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

CREATE TABLE session_agents (
  session_id    TEXT NOT NULL REFERENCES sessions(session_id),
  agent_id      TEXT NOT NULL REFERENCES agents(agent_id),
  registered_at TEXT NOT NULL,
  PRIMARY KEY (session_id, agent_id)
);

CREATE TABLE connect_codes (
  code       TEXT PRIMARY KEY,
  issued_at  TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  used_at    TEXT,
  revoked_at TEXT
);

CREATE TABLE connectors (
  connector_id         TEXT PRIMARY KEY,
  token_sha256         TEXT NOT NULL UNIQUE,
  created_at           TEXT NOT NULL,
  revoked_at           TEXT,
  last_seen_at         TEXT,
  current_execution_id TEXT,
  supported_kinds_json TEXT                -- 마지막 claim 의 ClaimRequest.supported_kinds. NULL = 구버전 (v5)
);

CREATE TABLE source_tokens (
  token_id      TEXT PRIMARY KEY,                       -- 'src-' + 8 hex
  session_id    TEXT NOT NULL REFERENCES sessions(session_id),
  source        TEXT NOT NULL CHECK (source IN ('n8n')),
  token_sha256  TEXT NOT NULL UNIQUE,
  label         TEXT NOT NULL,                          -- 사람이 붙인 이름 (빈 문자열 허용)
  created_at    TEXT NOT NULL,
  last_used_at  TEXT,
  revoked_at    TEXT
);

CREATE TABLE chains (
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

CREATE TABLE kinds (
  session_id  TEXT NOT NULL REFERENCES sessions(session_id),
  kind        TEXT NOT NULL,
  spec_json   TEXT NOT NULL,      -- KindSpec JSON. kind 필드는 컬럼과 같다
  created_at  TEXT NOT NULL,
  PRIMARY KEY (session_id, kind)
);

CREATE TABLE succession_rules (
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

CREATE TABLE tasks (
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
  source_ref               TEXT, work_item_id TEXT REFERENCES work_items(work_item_id),                              -- 가져온 이슈 키 (#42, OPS-42)
  CHECK (predecessor_task_id IS NULL OR predecessor_task_id != task_id),
  FOREIGN KEY (session_id, kind) REFERENCES kinds(session_id, kind)
);

CREATE TABLE selection_records (
  task_id     TEXT PRIMARY KEY REFERENCES tasks(task_id),
  record_json TEXT NOT NULL
);

CREATE TABLE executions (
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
  config_revision INTEGER,
  folder_commit TEXT,
  folder_dirty INTEGER CHECK (folder_dirty IS NULL OR folder_dirty IN (0, 1)),
  cost_usd REAL CHECK (cost_usd IS NULL OR cost_usd >= 0),
  input_tokens INTEGER CHECK (input_tokens IS NULL OR input_tokens >= 0),
  output_tokens INTEGER CHECK (output_tokens IS NULL OR output_tokens >= 0), branch_pushed INTEGER CHECK (branch_pushed IS NULL OR branch_pushed IN (0, 1)),               -- 측정 (v6)
  UNIQUE (task_id, attempt_no),
  UNIQUE (task_id, start_key)
);

CREATE UNIQUE INDEX ux_executions_active
  ON executions(task_id) WHERE released_at IS NULL;

CREATE TABLE execution_events (
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

CREATE TABLE execution_observations (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  execution_id TEXT NOT NULL REFERENCES executions(execution_id),
  observed_at  TEXT NOT NULL,
  kind         TEXT NOT NULL CHECK (kind IN ('unknown_no_start', 'heartbeat_lost', 'timeout')),
  detail       TEXT NOT NULL
);

CREATE TABLE artifacts (
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

CREATE TABLE task_verdicts (
  task_id      TEXT NOT NULL REFERENCES tasks(task_id),
  execution_id TEXT NOT NULL REFERENCES executions(execution_id),
  verdict_json TEXT NOT NULL,
  decided_at   TEXT NOT NULL
);

CREATE TABLE diagnosis_usage (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id   TEXT NOT NULL,
  execution_id TEXT NOT NULL,
  started_at   TEXT NOT NULL
);

CREATE TABLE github_sources (
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

CREATE TABLE github_assignee_bindings (
  source_id      TEXT NOT NULL REFERENCES github_sources(source_id),
  github_user_id INTEGER NOT NULL CHECK (github_user_id >= 1),
  github_login   TEXT NOT NULL,                             -- 표시용 (바뀔 수 있다)
  agent_id       TEXT NOT NULL REFERENCES agents(agent_id),
  updated_at     TEXT NOT NULL,
  PRIMARY KEY (source_id, github_user_id)
);

CREATE TABLE source_issues (
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
  updated_at       TEXT NOT NULL, merged_pr_number INTEGER, pr_merged_at TEXT, merge_checked_at TEXT, delegated_at TEXT, delegated_by TEXT CHECK (delegated_by IS NULL OR delegated_by IN ('operator', 'label')),
  PRIMARY KEY (source_id, github_issue_id)
);

CREATE TABLE followup_links (
  session_id         TEXT NOT NULL REFERENCES sessions(session_id),
  cause_execution_id TEXT NOT NULL REFERENCES executions(execution_id),
  to_kind            TEXT NOT NULL,
  task_id            TEXT NOT NULL UNIQUE REFERENCES tasks(task_id),
  rules_revision     INTEGER NOT NULL,
  created_at         TEXT NOT NULL,
  PRIMARY KEY (session_id, cause_execution_id, to_kind),
  FOREIGN KEY (session_id, to_kind) REFERENCES kinds(session_id, kind)
);

CREATE TABLE human_requests (
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

CREATE TABLE human_responses (
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

CREATE TABLE source_deliveries (
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

CREATE TABLE task_events (
  id              INTEGER PRIMARY KEY,
  task_id         TEXT NOT NULL REFERENCES tasks(task_id),
  session_id      TEXT NOT NULL REFERENCES sessions(session_id),
  type            TEXT NOT NULL CHECK (type IN ('status_changed', 'blocked', 'ready')),
  task_revision   INTEGER NOT NULL,
  config_revision INTEGER NOT NULL,
  occurred_at     TEXT NOT NULL,                            -- 서버 시계
  data_json       TEXT NOT NULL
);

CREATE INDEX ix_task_events_task ON task_events(task_id, id);

CREATE INDEX ix_task_events_session ON task_events(session_id, occurred_at);

CREATE TABLE baseline_items (
  source_id       TEXT NOT NULL REFERENCES github_sources(source_id),
  issue_number    INTEGER NOT NULL,
  issue_title     TEXT NOT NULL,
  issue_opened_at TEXT NOT NULL,
  pr_number       INTEGER NOT NULL,
  pr_merged_at    TEXT NOT NULL,
  fetched_at      TEXT NOT NULL,
  PRIMARY KEY (source_id, issue_number, pr_number)
);

CREATE TABLE baseline_imports (
  source_id     TEXT PRIMARY KEY REFERENCES github_sources(source_id),
  opened_before TEXT NOT NULL,
  fetched_at    TEXT NOT NULL,
  item_count    INTEGER NOT NULL
);

CREATE TABLE task_pull_requests (
  task_id              TEXT PRIMARY KEY REFERENCES tasks(task_id),
  session_id           TEXT NOT NULL,
  source_id            TEXT NOT NULL REFERENCES github_sources(source_id),
  repository_full_name TEXT NOT NULL,
  issue_number         INTEGER NOT NULL,
  head_branch          TEXT NOT NULL,                       -- task/<task_id>
  fix_execution_id     TEXT NOT NULL,
  review_execution_id  TEXT NOT NULL,
  state                TEXT NOT NULL CHECK (state IN ('pending', 'open', 'merged', 'closed', 'failed')),
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

CREATE TABLE notifications (
  notification_id TEXT PRIMARY KEY,                         -- 'ntf-' + 8 hex
  session_id      TEXT NOT NULL,
  event           TEXT NOT NULL CHECK (event IN ('human_request', 'pr_opened', 'task_failed')),
  task_id         TEXT REFERENCES tasks(task_id),
  dedupe_key      TEXT NOT NULL UNIQUE,
  content         TEXT NOT NULL,
  payload_json    TEXT NOT NULL,
  state           TEXT NOT NULL CHECK (state IN ('pending', 'sent', 'failed', 'skipped')),
  attempts        INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
  next_at         TEXT,
  last_error      TEXT,
  created_at      TEXT NOT NULL,
  sent_at         TEXT
);

CREATE TABLE work_items (
  work_item_id   TEXT PRIMARY KEY,                          -- 'wi-' + 12 hex
  session_id     TEXT NOT NULL REFERENCES sessions(session_id),
  key_number     INTEGER NOT NULL CHECK (key_number >= 1),
  title          TEXT NOT NULL,
  request        TEXT NOT NULL,
  kind           TEXT NOT NULL,                             -- 대표 종류 = 첫 단계 종류
  priority       TEXT NOT NULL DEFAULT 'normal' CHECK (priority IN ('high', 'normal', 'low')),
  assignee_type  TEXT CHECK (assignee_type IS NULL OR assignee_type IN ('member', 'agent')),
  assignee_id    TEXT,
  status         TEXT NOT NULL CHECK (status IN ('새로 들어옴', '대기', '에이전트 작업 중', '직접 작업 중', '내 차례', 'PR · 검토', '완료', '종료')),
  status_reason  TEXT NOT NULL,
  source_type    TEXT NOT NULL CHECK (source_type IN ('github', 'n8n', 'manual')),
  source_id      TEXT,
  source_item_id TEXT,
  source_key     TEXT,
  source_url     TEXT,
  source_state   TEXT,
  form_json      TEXT NOT NULL DEFAULT '{}',
  revision       INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1),
  created_at     TEXT NOT NULL,
  updated_at     TEXT NOT NULL,
  closed_at      TEXT,
  UNIQUE (session_id, key_number),
  FOREIGN KEY (session_id, kind) REFERENCES kinds(session_id, kind),
  CHECK ((assignee_type IS NULL) = (assignee_id IS NULL)),
  CHECK ((status IN ('완료', '종료')) = (closed_at IS NOT NULL))
);

CREATE INDEX ix_work_items_status ON work_items(session_id, status);

CREATE TABLE work_item_links (
  from_work_item_id  TEXT NOT NULL REFERENCES work_items(work_item_id),
  to_work_item_id    TEXT NOT NULL REFERENCES work_items(work_item_id),
  type               TEXT NOT NULL CHECK (type IN ('blocks', 'spawned_from')),
  cause_execution_id TEXT REFERENCES executions(execution_id),
  created_at         TEXT NOT NULL,
  PRIMARY KEY (from_work_item_id, to_work_item_id, type),
  CHECK (from_work_item_id != to_work_item_id)
);

CREATE TABLE members (
  member_id    TEXT PRIMARY KEY,                            -- 'mem-' + 8 hex
  session_id   TEXT NOT NULL REFERENCES sessions(session_id),
  display_name TEXT NOT NULL,
  role         TEXT NOT NULL CHECK (role IN ('admin', 'member')),
  created_at   TEXT NOT NULL
);

CREATE TABLE field_mappings (
  mapping_id    TEXT PRIMARY KEY,                           -- 'map-' + 8 hex
  session_id    TEXT NOT NULL REFERENCES sessions(session_id),
  source_type   TEXT NOT NULL CHECK (source_type IN ('github', 'n8n')),
  field         TEXT NOT NULL CHECK (field IN ('kind', 'priority')),
  source_value  TEXT NOT NULL,
  runloom_value TEXT NOT NULL,
  position      INTEGER NOT NULL CHECK (position >= 1),
  created_at    TEXT NOT NULL,
  UNIQUE (session_id, source_type, field, source_value)
);

CREATE TABLE work_item_events (
  id              INTEGER PRIMARY KEY,
  work_item_id    TEXT NOT NULL REFERENCES work_items(work_item_id),
  session_id      TEXT NOT NULL REFERENCES sessions(session_id),
  type            TEXT NOT NULL CHECK (type IN ('status_changed', 'assigned')),
  config_revision INTEGER NOT NULL,
  occurred_at     TEXT NOT NULL,
  data_json       TEXT NOT NULL
);

CREATE INDEX ix_work_item_events_item ON work_item_events(work_item_id, id);

CREATE INDEX ix_work_item_events_session ON work_item_events(session_id, occurred_at);

CREATE INDEX ix_tasks_work_item ON tasks(work_item_id, created_at);
