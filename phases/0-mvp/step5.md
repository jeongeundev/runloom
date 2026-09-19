# Step 5: machine-api

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/docs/ARCHITECTURE.md` — "실행 인터페이스 초안", "인증·권한·비밀정보 규칙", "등록·선택·권한"(연결 프로그램의 능력 설명 제안)
- `/docs/CONTRACT.md` — 2절(claim), 3절(이벤트와 오류표 전부), 4절(산출물 업로드·다운로드)
- `/docs/adr/0005-access-model-anonymous-session-operator-token.md`
- `/src/workflow/contracts/v1.py` (Step 1)
- `/src/workflow/adapters/` (Step 4) — `repo`, `errors`, `artifact_store`, `db`

## 작업

기계가 호출하는 중앙 API 를 만든다: 연결 프로그램(운영자 Mac)이 쓰는 엔드포인트다. 사람 화면은 Step 6. FastAPI 앱 팩토리와 설정·인증·오류 변환도 이 step 에서 만든다.

### `src/workflow/server/settings.py`

```python
@dataclass(frozen=True)
class Settings:
    db_path: Path; artifact_dir: Path
    session_secret: str; operator_token: str
    diag_api_url: str; diag_api_token: str
    session_cookie_days: int = 14
    limits: Limits   # per_session_daily=10, global_daily=60, active_tasks_per_session=5, attachments_max_bytes=1_048_576, unknown_after_seconds=120, heartbeat_offline_seconds=90

def load_settings(env: Mapping[str, str] = os.environ) -> Settings
```

환경변수: `WORKFLOW_DB_PATH`(기본 `data/central.sqlite`), `WORKFLOW_ARTIFACT_DIR`(기본 `data/artifacts`), `SESSION_SECRET`, `OPERATOR_TOKEN`, `DIAG_API_URL`(기본 `http://127.0.0.1:8100`), `DIAG_API_TOKEN`, 상한은 `WORKFLOW_LIMIT_*`. 비밀값이 비어 있으면 `load_settings` 가 `ValueError` — 단, 테스트와 로컬 기동을 위해 `WORKFLOW_DEV=1` 이면 무작위 값을 생성하고 stderr 에 경고 한 줄을 남긴다.

### `src/workflow/server/errors.py`

```python
class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, field: str | None = None, details: dict | None = None)
def install_error_handlers(app: FastAPI) -> None
def adapter_error_to_api(exc: AdapterError) -> ApiError
```

- `ApiError` → `ErrorBody` JSON 과 status.
- `RequestValidationError` → 422. `extra_forbidden` 이면 `code="unknown_field"`, `field` 는 허용되지 않은 필드명(CONTRACT 3절 예 `"필드 extra는 허용되지 않습니다."`). `contract_version` 오류면 `code="unsupported_contract_version"`, message `"contract_version {값}는 지원하지 않습니다."`. 그 외 `code="invalid_field"`, `field` 는 `loc` 를 `.` 로 이은 값(body 접두사 제외).
- 어댑터 예외 대응: `NotFound`→404 `not_found`, `Forbidden`→403 `forbidden`, `EventConflict`→409 `event_conflict`, `SequenceGap`→409 `sequence_gap`(details `expected_seq`), `InvalidTransition`→409 `invalid_transition`(details `current_status` 또는 `reason`), `HashMismatch`→422 `hash_mismatch`, `ActiveExecutionExists`/`DuplicateStartKey`→409 `execution_conflict`. 메시지 문구는 CONTRACT 3절 표를 그대로 쓴다.

### `src/workflow/server/auth.py`

```python
def sign_session(session_id: str, secret: str) -> str          # "{session_id}.{hmac_sha256_hex}"
def verify_session(cookie: str, secret: str) -> str | None
def require_connector(request) -> str                          # FastAPI 의존성. Authorization: Bearer wfc_... → repo.authenticate_connector → connector_id. 없거나 무효 → 401 unauthenticated "유효한 연결 토큰이 필요합니다."
def require_session(request, response) -> str                  # Step 6 이 쓴다. 쿠키 없으면 새 세션을 만들고 Set-Cookie(HttpOnly, SameSite=Lax, 14일). 여기서는 함수만 두고 라우트에 쓰지 않는다
def require_operator(request) -> str                           # 세션이 is_operator 가 아니면 403 forbidden
```

### `src/workflow/server/app.py`

```python
def create_app(settings: Settings | None = None) -> FastAPI
    # settings None 이면 load_settings(). connect + init_schema, ArtifactStore 생성. app.state.settings/conn_factory/store 에 둔다.
    # conn 은 요청마다 새로 열고 닫는다 (sqlite3 연결을 스레드 간 공유하지 않는다).
app = create_app()    # AGENTS.md 의 `python3 -m uvicorn workflow.server.app:app` 용. import 시 환경변수를 읽으므로 테스트는 create_app(settings) 를 쓴다
```

`app` 모듈 변수는 `WORKFLOW_DEV` 없이 비밀값도 없으면 import 가 실패한다. 이것이 의도다 — 운영에서 비밀값 없이 뜨지 않는다. 테스트가 `workflow.server.app` 을 import 할 때 실패하지 않도록, 모듈 로드 시점의 `create_app()` 호출은 `WORKFLOW_SKIP_APP=1` 이면 건너뛴다 (`app = None`). `tests/conftest.py` 에서 이 환경변수를 설정한다.

### `src/workflow/server/machine_api.py` — `router`

| 경로 | 인증 | 동작 |
|---|---|---|
| `POST /connector/exchange` | 없음 (코드 자체가 자격) | 본문 `{contract_version, connect_code}` → `repo.exchange_connect_code` → 200 `{connector_id, token}`. 실패 404 `not_found`. 토큰은 이 응답에만 한 번 나온다 |
| `POST /connector/claim` | connector | `ClaimRequest.connector_id` 가 인증된 ID 와 다르면 403. `repo.claim_execution` → 200 `ExecutionRequest`(저장된 `request_json`) 또는 204 |
| `POST /connector/heartbeat` | connector | `HeartbeatRequest` → `touch_connector` + 이 connector 의 agents 를 `online` 으로. 200 `{}` |
| `POST /connector/registrations` | connector | 본문 `{contract_version, connector_id, local_registration_id, tool: "codex", repository_id, base_commit, verification_profile_ids: [...], discovered: {...}}` → `local_registration_id` 가 같은 agent(운영자가 미리 등록)를 찾아 `connector_id`·`repository_id`·`base_commit`·`verification_profile_ids`·`discovered_json`·`connection_state=online` 갱신. 없으면 404. `discovered` 는 임의 JSON 이지만 크기 64KB 상한 |
| `POST /executions/{execution_id}/events` | connector | 실행이 이 connector 에 배정돼 있어야 한다(아니면 403 forbidden, 메시지 CONTRACT 3절). URL 의 ID 와 본문 `execution_id` 불일치 → 422 `invalid_field`. `repo.append_event(actor=f"connector:{id}")` → 200 `EventAck` |
| `POST /executions/{execution_id}/artifacts` | connector | multipart `meta`(JSON 문자열 → `ArtifactMeta`) + `file`. 배정 확인 → `store_artifact` → 201(신규)/200(기존) `ArtifactCreated`. 해시 불일치 422 |
| `GET /executions/{execution_id}/artifacts/{artifact_id}` | connector | 배정 확인 + `download_allowed` 아니면 403. 200 바이트, `Content-Type` 은 저장된 값 |

모든 응답 본문은 계약 모델의 `model_dump(mode="json")`. 로그에 `Authorization` 을 남기지 않는다 (uvicorn access log 는 헤더를 찍지 않지만, 예외 메시지에 헤더를 넣지 않도록 `ApiError` 메시지에 요청 객체를 넣지 않는다).

### 테스트 — `tests/workflow/server/test_machine_api.py`, `test_auth.py`, `test_errors.py`, `test_settings.py`

`fastapi.testclient.TestClient` 와 `tmp_path` 의 설정으로 `create_app(settings)`. fixture 에서 DB 에 직접 agent·task·execution 을 넣고(Step 4 repo 사용), 연결 코드 발급·교환으로 토큰을 얻는다.

- CONTRACT 3절 오류표의 모든 행을 각각 한 테스트로: 200 중복, 409 `event_conflict`, 409 `sequence_gap`(`details.expected_seq`), 409 `invalid_transition` 두 경우, 401(토큰 없음/취소), 403(다른 connector 의 실행), 404, 422 `unsupported_contract_version`, 422 `unknown_field`. 응답 본문의 `code`·`message`·`field`·`details` 가 표와 같다.
- claim: 204 → 실행 생성 후 200 과 CONTRACT 2절 형식 → accepted 이벤트 뒤 204. 다른 connector 토큰으로 claim 하면 자기 것만.
- exchange: 성공 후 같은 코드 재사용 404. 응답 토큰으로 heartbeat 200.
- 산출물: 업로드 201 → 같은 것 200 → `result_ready` 이벤트 200. 산출물 없이 `result_ready` → 409 `invalid_transition` `details.reason == "result_artifact_missing"`. 해시 불일치 422. 다운로드 허용/거부.
- registrations: 미리 등록된 agent 갱신, 없는 ID 404.
- settings: 비밀값 없으면 `ValueError`, `WORKFLOW_DEV=1` 이면 통과.
- auth: 서명 위조 쿠키 거부.

### GLOSSARY

`connect code`(운영자가 발급하는 1회용 10분 연결 코드. 교환하면 `connector_id` 와 연결 토큰 `wfc_…` 이 된다) 를 추가한다.

## Acceptance Criteria

```bash
python3 -m pytest tests/workflow/server -q
python3 -m pytest -q
python3 -m ruff check .
WORKFLOW_DEV=1 python3 -c "from workflow.server.app import app; print(type(app).__name__)"   # FastAPI
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트:
   - `workflow.server` 가 `workflow.connector` 를 import 하지 않는가?
   - 비밀값이 환경변수에서만 오는가? 응답·로그·예외 메시지에 토큰이 없는가?
   - 요청 본문에서 파일 경로·셸 명령을 받아 실행하는 곳이 없는가?
   - 오류 문구가 CONTRACT 3절과 글자 단위로 같은가?
3. `phases/0-mvp/index.json` 의 step 5 를 업데이트한다 (summary 에 라우트 목록과 `create_app` 시그니처, `WORKFLOW_SKIP_APP` 규약).

## 금지사항

- 사람 화면 라우트(`/`, `/tasks`)를 만들지 마라. 이유: Step 6.
- `BackgroundTasks` 로 실행을 시작하지 마라. 이유: ARCHITECTURE — 실행은 워커(Step 8)가 DB 기록으로 시작한다.
- sqlite 연결을 앱 전역에 하나 두고 공유하지 마라. 이유: 스레드 안전하지 않다. 요청마다 연다.
- 진단 API 를 여기서 호출하지 마라. 이유: 중앙 워커의 책임이다.
- 기존 테스트를 깨뜨리지 마라.
