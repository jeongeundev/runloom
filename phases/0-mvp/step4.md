# Step 4: central-db

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/docs/ARCHITECTURE.md` — "최소 데이터 모델과 영속성", "DB 제약과 실행 잠금", "계약 수용 기준", "인증·권한·비밀정보 규칙"(연결 코드·토큰 해시), "배포와 실행 예산"(SQLite busy_timeout 5초)
- `/docs/adr/0002-server-stack-python-fastapi-sqlite.md`, `/docs/adr/0005-access-model-anonymous-session-operator-token.md`
- `/docs/CONTRACT.md` — 3절 이벤트 오류표, 4절 산출물 업로드 규칙
- `/src/workflow/contracts/v1.py` (Step 1), `/src/workflow/domain/status.py`·`start_key.py` (Step 2)

## 작업

중앙 서비스의 저장 계층을 `src/workflow/adapters/` 에 만든다. 표준 `sqlite3` + 명시적 SQL 만 쓴다 (ORM 없음). 상태 전이 판단은 `workflow.domain.status.next_execution_status` 를 호출하고 여기서 다시 구현하지 않는다.

### `src/workflow/adapters/db.py`

```python
def connect(path: str | Path) -> sqlite3.Connection
    # PRAGMA foreign_keys=ON, journal_mode=WAL, busy_timeout=5000, row_factory=sqlite3.Row, isolation_level=None(명시적 BEGIN)
def init_schema(conn) -> None      # 멱등. CREATE TABLE IF NOT EXISTS
SCHEMA_VERSION = 1
```

테이블과 제약 (ARCHITECTURE "DB 제약과 실행 잠금" 표를 SQL 로 옮긴다):

| 테이블 | 핵심 컬럼 | 제약 |
|---|---|---|
| `sessions` | `session_id PK`, `created_at`, `is_operator INTEGER` | |
| `agents` | `agent_id PK`, `name`, `owner_scope CHECK IN ('personal','team','company')`, `connection_type CHECK IN ('local','api')`, `connector_id NULL`, `local_registration_id NULL`, `repository_id NULL`, `base_commit NULL`, `verification_profile_ids_json`, `api_url NULL`, `credential_ref NULL`, `capabilities_json`, `discovered_json`, `connection_state CHECK IN ('online','offline','unknown')`, `last_seen_at NULL`, `shared_to_all_sessions INTEGER` | |
| `connect_codes` | `code PK`, `issued_at`, `expires_at`, `used_at NULL`, `revoked_at NULL` | |
| `connectors` | `connector_id PK`, `token_sha256 UNIQUE`, `created_at`, `revoked_at NULL`, `last_seen_at NULL`, `current_execution_id NULL` | |
| `tasks` | `task_id PK`, `session_id FK`, `title`, `request`, `kind`, `required_capability_json`, `selection_mode`, `chosen_agent_id NULL`, `run_mode`, `completion_mode`, `criteria_json`, `predecessor_task_id NULL FK`, `revision CHECK(revision>=1)`, `target_json`, `status`(사용자 상태 한글), `status_reason`, `finished_at NULL`, `review_decision NULL`, `merge_confirmed_at NULL`, `created_at` | `CHECK(predecessor_task_id IS NULL OR predecessor_task_id != task_id)` |
| `selection_records` | `task_id PK FK`, `record_json` | |
| `executions` | `execution_id PK`, `task_id FK`, `attempt_no`, `start_key`, `agent_id`, `kind`, `request_json`(고정된 ExecutionRequest), `status CHECK IN (EXECUTION_STATUSES)`, `last_event_seq DEFAULT 0`, `assigned_connector_id NULL`, `result_artifact_id NULL`, `failed_code NULL`, `failed_message NULL`, `process_stopped NULL`, `created_at`, `accepted_at NULL`, `started_at NULL`, `finished_at NULL`, `released_at NULL`, `predecessor_execution_id NULL` | `UNIQUE(task_id, attempt_no)`, `UNIQUE(task_id, start_key)`, `CHECK(attempt_no>=1)`, 부분 유일 인덱스 `CREATE UNIQUE INDEX ux_executions_active ON executions(task_id) WHERE released_at IS NULL` |
| `execution_events` | `execution_id FK`, `seq`, `type`, `occurred_at`, `received_at`, `data_json`, `actor` | `PRIMARY KEY(execution_id, seq)`, `CHECK(seq>=1)` |
| `execution_observations` | `id PK`, `execution_id FK`, `observed_at`, `kind`(`unknown_no_start`, `heartbeat_lost`, `timeout`), `detail` | 서버 관찰. 실행 주체의 seq 를 쓰지 않는다 |
| `artifacts` | `artifact_id PK`, `execution_id FK`, `session_id FK`, `kind`, `name`, `content_type`, `sha256`, `size`, `store_ref`, `created_at` | `UNIQUE(execution_id, kind, sha256)` |
| `task_verdicts` | `task_id FK`, `execution_id FK`, `verdict_json`, `decided_at` | |
| `diagnosis_usage` | `id PK`, `session_id`, `execution_id`, `started_at` | 세션·일일 상한 계산용 |

### `src/workflow/adapters/artifact_store.py`

```python
class ArtifactStore:
    def __init__(self, root: Path): ...
    def write(self, data: bytes) -> tuple[str, str]      # (sha256, store_ref). 임시 파일에 쓰고 fsync 후 최종 경로로 rename. 최종 경로 root/ab/cd/<sha256>
    def read(self, store_ref: str) -> bytes
    def exists(self, sha256: str) -> bool
```

내용 주소 저장이라 같은 바이트는 한 번만 저장된다. DB 의 `artifacts` 행이 소유 범위를 결정하며, 해시가 같아도 다른 세션의 행을 합치지 않는다 (ARCHITECTURE: "해시가 같아도 소유 권한을 합치지 않음").

### `src/workflow/adapters/errors.py`

`ActiveExecutionExists`, `DuplicateStartKey`, `EventConflict`, `SequenceGap(expected_seq)`, `InvalidTransition(current_status)`, `NotFound`, `Forbidden`, `HashMismatch`, `ArtifactMissing`. 모두 `AdapterError` 를 상속. HTTP 상태로 옮기는 것은 Step 5.

### `src/workflow/adapters/repo.py`

함수형 저장소. 모든 함수는 `conn` 을 첫 인자로 받고, 여러 문장을 바꾸는 함수는 `BEGIN IMMEDIATE` … `COMMIT` 을 함수 안에서 연다. 시각은 인자 `now: str`(RFC 3339 UTC) 로 받는다 — 테스트가 시각을 고정할 수 있어야 한다.

세션·운영자·에이전트:

```python
def create_session(conn, session_id: str, now: str) -> None
def get_session(conn, session_id: str) -> Row | None
def mark_operator(conn, session_id: str) -> None
def upsert_agent(conn, agent: dict) -> None          # dict 키는 agents 컬럼. capabilities 는 Capability 로 검증한 뒤 json 저장
def list_agents(conn) -> list[Row]
def get_agent(conn, agent_id: str) -> Row | None
def delete_agent(conn, agent_id: str) -> None
def set_agent_connection(conn, agent_id: str, state: str, last_seen_at: str | None) -> None
def agents_for_connector(conn, connector_id: str) -> list[Row]
```

연결 코드·연결 프로그램 (ARCHITECTURE 인증 절):

```python
def issue_connect_code(conn, now: str, ttl_seconds: int = 600) -> str            # 무작위 코드. 발급·만료 시각 저장
def revoke_connect_code(conn, code: str, now: str) -> None
def exchange_connect_code(conn, code: str, now: str) -> tuple[str, str]           # (connector_id, token_plain). 만료·사용·취소된 코드는 NotFound. 사용 표시와 connector 생성을 한 트랜잭션. 토큰은 secrets.token_urlsafe(32) 에 접두사 "wfc_", DB 에는 sha256 만
def authenticate_connector(conn, token_plain: str) -> str | None                   # revoked 는 None
def revoke_connector(conn, connector_id: str, now: str) -> None
def touch_connector(conn, connector_id: str, now: str, current_execution_id: str | None) -> None
```

업무·선택:

```python
def insert_task(conn, task: dict, now: str) -> None
def get_task(conn, task_id: str) -> Row | None
def list_tasks(conn, session_id: str | None) -> list[Row]        # None 이면 전체(운영자)
def update_task_status(conn, task_id: str, status: str, reason: str, *, finished_at: str | None = None, review_decision: str | None = None) -> None
def save_selection(conn, record: SelectionRecord) -> None
def get_selection(conn, task_id: str) -> SelectionRecord | None
def successors_of(conn, task_id: str) -> list[Row]
def confirm_merge(conn, task_id: str, now: str) -> None
```

실행·이벤트 (핵심):

```python
def create_execution(conn, *, execution_id, task_id, attempt_no, start_key, agent_id, kind,
                     request: ExecutionRequest, assigned_connector_id: str | None,
                     predecessor_execution_id: str | None, now: str) -> None
    # 활성 잠금 위반 → ActiveExecutionExists, start_key 중복 → DuplicateStartKey (IntegrityError 를 메시지로 구분)
def get_execution(conn, execution_id: str) -> Row | None
def active_execution(conn, task_id: str) -> Row | None
def claim_execution(conn, connector_id: str, now: str) -> Row | None
    # BEGIN IMMEDIATE 안에서: 이 connector 에 배정됐고 status='queued' 인 가장 오래된 실행 하나, 또는 이미 배정됐지만 accepted 전(=queued 유지)인 같은 실행. 없으면 None. 배정 표시만 하고 상태는 바꾸지 않는다 (accepted 이벤트가 바꾼다)
def append_event(conn, execution_id: str, event: ExecutionEvent, actor: str, now: str) -> EventAck
    # 한 트랜잭션: 같은 seq·같은 내용 → 저장하지 않고 현재 상태 반환. 같은 seq·다른 내용 → EventConflict.
    # seq != last_event_seq + 1 → SequenceGap(expected). 전이 불가 → InvalidTransition(current).
    # result_ready 는 data.result_artifact_id 가 이 실행의 artifacts 에 있어야 한다. 없으면 InvalidTransition(reason="result_artifact_missing")
    # 상태 갱신과 accepted_at/started_at/finished_at/failed_* 기록을 함께 한다
def list_events(conn, execution_id: str, after_seq: int = 0) -> list[Row]
def mark_unknown(conn, execution_id: str, kind: str, detail: str, now: str) -> None    # status='unknown' + observation 행
def release_execution(conn, execution_id: str, now: str) -> None
def executions_needing_attention(conn) -> list[Row]           # 워커 스캔용: 활성이면서 최종 상태가 아닌 것
```

산출물:

```python
def store_artifact(conn, store: ArtifactStore, *, execution_id, session_id, meta: ArtifactMeta, data: bytes, now: str) -> tuple[ArtifactCreated, bool]
    # sha256(data) != meta.sha256 또는 len != size → HashMismatch. 같은 (execution_id, kind, sha256) 이 있으면 (기존, False). 새로 저장하면 (생성, True)
def get_artifact(conn, artifact_id: str) -> Row | None
def read_artifact(conn, store: ArtifactStore, artifact_id: str) -> bytes
def artifacts_of(conn, execution_id: str) -> list[Row]
def download_allowed(conn, execution_id: str, artifact_id: str) -> bool
    # 실행의 request_json.input_artifact_ids 에 있거나, 그 입력 중 kind=handoff_bundle 의 manifest attachments 에 나열된 artifact_id 이거나, 이 실행이 만든 산출물이면 True
```

상한:

```python
def count_diagnosis_started(conn, *, session_id: str | None, since: str) -> int
def record_diagnosis_start(conn, session_id: str, execution_id: str, now: str) -> None
```

### 테스트 — `tests/workflow/adapters/test_db.py`, `test_artifact_store.py`, `test_repo.py`

`tmp_path` 의 DB 파일로 실제 sqlite 를 쓴다 (`:memory:` 는 WAL·연결 분리 검증을 못 한다).

- 스키마: `init_schema` 두 번 호출해도 오류 없음. 외래키 위반 시 `IntegrityError`.
- 활성 잠금: 같은 task 에 두 번째 `create_execution` → `ActiveExecutionExists`. `release_execution` 뒤에는 가능. 같은 `start_key` 재사용 → `DuplicateStartKey` (해제 뒤에도).
- append_event: CONTRACT 3절 오류표의 모든 행 — 같은 seq 같은 내용 200(재적용 없음, 이벤트 수 불변), 같은 seq 다른 내용 `EventConflict`, 순번 누락 `SequenceGap(expected_seq=4)`, 최종 상태 뒤 `started` → `InvalidTransition`, running 아닌데 progress → `InvalidTransition`, `result_ready` 인데 산출물 없음 → `InvalidTransition`. 정상 5종 순서대로 넣으면 상태가 `queued→accepted→running→result_ready` 로 바뀌고 시각이 기록된다.
- claim: 배정 없으면 None. queued 실행이 있으면 같은 것을 두 번 claim 해도 같은 행. accepted 이벤트 뒤에는 claim 이 None. 다른 connector 는 받지 못한다. 두 연결(`connect` 두 번)에서 동시에 claim 해도 하나만 받는다 (스레드 2개로 확인).
- 연결 코드: 발급 → 교환 성공 → 재교환 `NotFound`. 만료(now 를 11분 뒤로) `NotFound`. 취소 `NotFound`. 토큰 평문이 DB 어디에도 없다 (`SELECT * FROM connectors` 문자열에 `wfc_` 없음). `authenticate_connector` 는 취소 후 None.
- 산출물: 해시 불일치 `HashMismatch`. 같은 kind·sha256 재업로드는 기존 반환·파일 1개. 다른 세션의 같은 바이트는 별도 행. `download_allowed` 세 경로 각각과 거부.
- 상한: `count_diagnosis_started` 가 세션별·전체를 센다.

### GLOSSARY

`ExecutionObservation`(서버가 기록한 관찰. `unknown` 판정 근거. 실행 주체의 `ExecutionEvent` 와 구분) 을 추가한다.

## Acceptance Criteria

```bash
python3 -m pytest tests/workflow/adapters -q
python3 -m pytest -q
python3 -m ruff check .
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트:
   - `src/workflow/adapters/` 만 `sqlite3` 를 import 하는가? domain 이 여전히 깨끗한가 (`tests/test_packages.py`)?
   - 상태 전이가 `workflow.domain.status.next_execution_status` 를 호출하는가 (중복 구현 없음)?
   - 토큰 평문·`Authorization` 값이 DB 에 저장되지 않는가?
   - 트랜잭션 안에서 네트워크·파일 해시 계산 같은 긴 작업을 하지 않는가? (해시는 트랜잭션 밖에서 계산하고 안에서는 비교만)
3. `phases/0-mvp/index.json` 의 step 4 를 업데이트한다 (summary 에 테이블 목록과 repo 함수 파일 경로).

## 금지사항

- ORM·마이그레이션 도구를 추가하지 마라. 이유: ADR-0002.
- 잠금 해제를 시간 경과로 하지 마라. 이유: ARCHITECTURE "기한 만료에 따른 잠금 해제는 하지 않는다".
- `append_event` 에서 발신자 신원을 이벤트 본문으로 판단하지 마라. 이유: 신원은 `actor` 인자(인증 계층이 준다)로만 온다.
- FastAPI·HTTP 코드를 이 계층에 넣지 마라. 이유: 예외 → HTTP 변환은 Step 5.
- 기존 테스트를 깨뜨리지 마라.
