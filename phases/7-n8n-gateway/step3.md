# Step 3: db-source-tokens

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/AGENTS.md`
- `/docs/adr/0010-n8n-inbox-and-callback.md`, `/docs/adr/0005-access-model-anonymous-session-operator-token.md`
- `/docs/ARCHITECTURE.md` "n8n 입구와 출구" 절 (저장), "최소 데이터 모델과 영속성", "DB 제약과 실행 잠금", "인증·권한·비밀정보 규칙"
- `/docs/GLOSSARY.md` (`source token`·`Chain` 행)
- `/src/workflow/adapters/db.py` 전부 — `SCHEMA_VERSION`, `connect_codes`·`connectors`·`chains` 테이블, `init_schema` 의 버전 검사(마이그레이션 없음)
- `/src/workflow/adapters/repo.py` 전부 — `_tx`·`_one`·`_sha256`·`_require_rowcount`, `exchange_connect_code`·`authenticate_connector`·`revoke_connector`·`touch_connector`(토큰 처리 방식), `insert_chain`·`get_chain`·`list_chains`·`mark_chain_started`·`tasks_of_chain`, `TOKEN_PREFIX`
- `/src/workflow/adapters/errors.py` (`NotFound`·`Forbidden`)
- `/tests/workflow/adapters/test_db.py`, `/tests/workflow/adapters/test_repo.py`, `/tests/workflow/adapters/conftest.py` — 테스트 작성 방식과 fixture
- step 1·2 산출물: `/src/workflow/contracts/v1.py` 의 새 모델, `/src/workflow/domain/settlement.py`(이 step 은 쓰지 않는다)

## 이 phase 의 개념 (모든 step 공통 — 이 절은 step 파일마다 같다)

이 phase 는 n8n 을 업무가 **들어오는 입구**와 결과가 **나가는 출구**로 붙인다. 판단(종류·규칙·판정·인계)은 그대로 Runloom 이 한다
(ADR-0009 참고 절 "n8n 은 업무가 들어오는 입구·나가는 출구로만 쓴다"). n8n 쪽은 노드 4개 — Webhook(또는 Error Trigger) → HTTP Request(Runloom 에 POST) →
Wait(On Webhook Call — `$execution.resumeUrl` 로 깨어남) → Slack. ADR-0010(step 0 이 쓴다)이 정본이다. 사용자는 n8n 을 써 본 적이 없고,
이 제품을 "n8n 옆의 에이전트 인계 계층"으로 소개한다 — 문서·화면·주석에 n8n 을 비판하는 문구를 쓰지 않는다.

- **`n8n` 은 `TaskSource` 하나**: `chains.source` 값 `'n8n'`, `domain/task_sources.Source` 에 `"n8n"`. 본문 항목은 GitHub·Jira fixture 의 `Issue` 와
  같은 모양(`key`·`title`·`body`·`labels`·`blocked_by`, `url` 은 None)이고 라벨 규칙(`incident`+`workflow:<id>`+`run:<run_id>`, `bug`+`repo:<id>`,
  `kind:<kind>`+`<scope_key>:<value>`)도 그대로다 — `map_issue`·`compose` 를 재사용하며 **매핑·구성·워커 후속 코드에 n8n 분기를 넣지 않는다**.
  fixture 출처 목록 `adapters/task_sources.SOURCES`(`github`·`jira`)는 그대로다 — n8n 은 파일이 없으므로 가져오기 화면(`/tasks/import`)에 나오지 않는다.
- **입구 토큰 (`source token`)**: 워크스페이스(세션)가 `/sources` 화면에서 발급한다. 원문은 `wfs_` + `secrets.token_urlsafe(32)`, 발급 응답에서 한 번만 보이고
  서버에는 sha256 만 남는다(연결 토큰 `wfc_` 와 같은 방식, `repo.exchange_connect_code` 참고). 취소하면 다음 요청부터 401. `Authorization: Bearer wfs_…` → `session_id` + `source`.
- **입구 API** `POST /sources/n8n/chains` (JSON 본문 `InboundChainRequest`, 응답 201 `InboundChainResponse`): 항목으로 체인 + Task 들을 가져오기(`/tasks/import`)와
  같은 규칙으로 만들고, **접수 즉시 첫 업무 시작을 시도한다**(n8n 트리거가 곧 사람의 "워크플로우 시작"). 첫 업무가 후보 없음(409 `selection_required`)·상한(429)·
  조건 미충족(409)이면 체인은 남기고 `started=false` + `start_error`(오류 본문)로 201 을 돌려준다. 세션에 등록된 에이전트가 없으면 422 `agent_not_registered`.
  `callback_url` 은 선택이며 http/https 만, 허용 목록 밖이면 422 `callback_host_not_allowed`. 토큰 없음·취소 401 `unauthenticated`, 토큰의 `source` 가 경로와 다르면 403.
- **callback (출구)**: 체인이 **사람 차례**(`chain_settled`)가 되면 워커가 `callback_url` 로 `ChainCallback` 을 **체인당 1회** POST 한다
  (n8n Wait 노드는 한 번만 깨어난다). `chain_settled(nodes)` 는 도메인 순수 함수(`domain/settlement.py`): (a) 어떤 업무도 `실행 요청됨`·`실행 중` 이 아니고,
  (b) `대기` 인 업무는 모두 선행 업무의 상태가 `확인 필요` 또는 `실패` 이면(= 사람에게 막힘) 참. 그 밖의 `대기`(자동 실행 대기·연결 끊김·선행 진행 중)와
  실행 중은 아직 워커 몫이라 거짓. 빈 목록은 거짓. 남는 상태(`확인 필요`·`완료`·`실패`·`실행 가능`)는 사람 조작 전엔 바뀌지 않는다 — 화면 폴링 규칙(`views._LIVE_LABELS`)과 같은 관찰이다.
  워커 tick 의 **마지막 단계**(후속 스캔·실패 반영 뒤)에서 판정하므로, A 판정 → B 착수가 같은 tick 에 일어나면 그 사이에 보내지 않는다.
  전송은 트랜잭션 밖 HTTPX POST(10초). 2xx 면 `callback_sent_at`, 아니면 `callback_attempts`+1 과 `callback_next_at = now + 30·2^(n-1)초`(30·60·120·240초), 5회 실패 후 중단(`callback_last_error` 를 화면에).
  사람이 그 뒤 승인·종료해도 다시 보내지 않는다. 직접 등록·가져오기 화면으로 만든 체인은 `callback_url` 이 없으므로 아무것도 보내지 않는다.
- **허용 목록** `WORKFLOW_CALLBACK_HOSTS`: 콤마 구분 `host` 또는 `host:port`(예 `localhost:5678,127.0.0.1`). `host` 만 쓰면 그 호스트의 모든 포트. 비어 있으면 callback 을 받지 않는다(접수 시 422).
  이유: 공개 데모 VM 은 누구나 세션을 만들 수 있어, 외부가 준 주소로 서버가 POST 하게 두면 내부 주소(`127.0.0.1:8100` 등)를 찌를 수 있다. 셀프호스트는 `localhost:5678` 한 줄.
  판정 함수는 `domain/callback_policy.py` 의 `host_allowed(url, allowed)`·`parse_hosts(raw)` 다. `WORKFLOW_PUBLIC_URL`(선택, 예 `http://127.0.0.1:8000`, 끝 `/` 없음)은
  응답·callback 의 `chain_url`·`task_url` 앞에 붙는다. 비면 두 필드는 null. 둘 다 비밀값이 아니다.
- **계약** (`contracts/v1.py`, step 1):
  `InboundItem(key, title, body, labels: list[str], blocked_by: list[str])` ·
  `InboundChainRequest(contract_version, items: list[InboundItem] 1~10개, key 유일, blocked_by 는 같은 요청의 key 만, callback_url: str | None)` ·
  `InboundChainResponse(contract_version, chain_id, chain_url: str | None, started: bool, start_error: ErrorBody | None, tasks: list[InboundTaskRef(task_id, key, kind, status)], skipped: list[InboundSkipped(key, reason)])` ·
  `ChainCallback(contract_version, chain_id, title, source: Literal["n8n"], chain_url: str | None, settled_at, human_gate: CallbackGate(label, status_label, reason), tasks: list[CallbackTask(task_id, key, kind, title, status, status_reason, outcome: str | None, summary: str | None, task_url: str | None)])`.
  `outcome`·`summary` 는 그 Task 의 최신 결과 봉투(`diagnosis_result`·`code_change_result`·`generic_result` 산출물)에서 읽고, 결과가 없으면 null. `status` 는 `USER_STATUS_LABELS` 의 문구다.
- **저장** (step 3): `SCHEMA_VERSION` 3 → 4, 마이그레이션 없음(`WORKFLOW_RESET_DB=1` 재생성 — 공개 데모 VM 은 손대지 않는다).
  신규 `source_tokens(token_id PK 'src-'+8hex, session_id, source, token_sha256 UNIQUE, label, created_at, last_used_at, revoked_at)`.
  `chains` 에 `items_json`(n8n 이 보낸 항목 원문 — 체인 화면의 구성 이유 재계산용)·`callback_url`·`callback_sent_at`·`callback_attempts`(기본 0)·`callback_next_at`·`callback_last_error` 추가, `source` CHECK 에 `'n8n'`.
- **화면** (step 6): `/sources`(입구 URL·토큰 목록·발급·취소·curl 예시), 사이드바 "입구" 링크, 체인 화면에 출처 `n8n` 과 callback 상태 한 줄.
- **증명** (step 8·10): e2e 는 테스트 안 HTTP 수신기가 n8n 역할 — 토큰 발급 → POST → 대본 A→B → 수신기가 `ChainCallback` 1건(B `확인 필요 · 검토 대기`) 받고 두 번째는 안 온다.
  step 10 은 Docker 의 실제 n8n 으로 같은 흐름을 1회 돌리고 VERIFICATION_LOG 에 남긴다.
- **하지 않는 것**: 등록부 → n8n JSON 컴파일, LLM 워크플로우 생성, 업무마다 callback, 완료 시 새 업무 생성 규칙(ADR-0009 트레이드오프 — 다음), n8n 비판 문구, `graph`·`DAG`·`pipeline`·`workflow`(제품 흐름 뜻) 단어.

## 작업

TDD: `tests/workflow/adapters/test_db.py`·`test_repo.py` 에 테스트를 먼저 쓰고 실패를 확인한 뒤 구현한다. 저장 계층만 바꾼다.

### 1. `src/workflow/adapters/db.py`

- `SCHEMA_VERSION = 4`. 마이그레이션은 만들지 않는다(기존 버전이면 `init_schema` 가 지금처럼 `RuntimeError`).
- 신규 테이블 (주석은 기존 표의 어조로):

```sql
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
```

- `chains` 테이블: `source` CHECK 를 `('github', 'jira', 'manual', 'n8n')` 로. 컬럼 추가:
  `items_json TEXT`(n8n 이 보낸 항목 원문 배열, 다른 출처는 NULL) · `callback_url TEXT` · `callback_sent_at TEXT` · `callback_attempts INTEGER NOT NULL DEFAULT 0 CHECK (callback_attempts >= 0)` · `callback_next_at TEXT` · `callback_last_error TEXT`. 주석에 "callback 은 체인당 1회 — `callback_sent_at` 이 차면 끝" 을 적는다.

### 2. `src/workflow/adapters/repo.py`

```python
SOURCE_TOKEN_PREFIX = "wfs_"

def issue_source_token(conn, session_id: str, source: str, label: str, now: str) -> tuple[str, str]:
    """(token_id, token_plain). 원문은 여기서만 만들어 돌려주고 DB 에는 sha256 만 남는다. 세션이 없으면 NotFound."""

def authenticate_source_token(conn, token_plain: str) -> Row | None:
    """취소되지 않은 토큰이면 그 행(token_id·session_id·source). 없으면 None. 원문을 로그·예외에 넣지 않는다."""

def touch_source_token(conn, token_id: str, now: str) -> None          # last_used_at

def revoke_source_token(conn, session_id: str, token_id: str, now: str) -> None:
    """같은 세션의 토큰만. 다른 세션이거나 없으면 NotFound. 이미 취소됐으면 그대로(멱등)."""

def list_source_tokens(conn, session_id: str) -> list[Row]              # 발급 순, 취소된 것 포함

def insert_chain(conn, chain: dict, now: str) -> None
    # 기존 키(chain_id·session_id·title·source·skipped)에 선택 키 `callback_url`(str|None)·`items`(list|None → items_json) 를 더한다. 없으면 NULL.

def chains_awaiting_callback(conn, now: str, *, max_attempts: int) -> list[Row]:
    """callback_url 이 있고 callback_sent_at 이 NULL 이고 attempts < max_attempts 이고 (next_at IS NULL OR next_at <= now) 인 체인. created_at 순."""

def record_callback_attempt(conn, chain_id: str, *, ok: bool, error: str | None, now: str, next_at: str | None) -> None:
    """ok 면 callback_sent_at = now, last_error = NULL. 아니면 attempts + 1, last_error = error, next_at = next_at. 없는 체인은 NotFound."""
```

`chains` 의 새 컬럼은 `get_chain`·`list_chains` 의 `SELECT *` 로 그대로 나온다. `items_json` 은 JSON 문자열 그대로 두고 해석은 호출자가 한다(`views` 가 step 6 에서 읽는다).

### 3. 테스트

`test_db.py`: `SCHEMA_VERSION == 4`, `source_tokens` 테이블 존재, `chains` 의 새 컬럼 기본값(`callback_attempts` 0, 나머지 NULL), `source='n8n'` 삽입 가능, `source='slack'` 은 CHECK 위반, 기존 버전 DB 에서 `init_schema` 가 `RuntimeError`.
`test_repo.py`: 발급 → 원문이 `wfs_` 로 시작하고 DB 에 원문이 없음(`SELECT token_sha256` 이 원문과 다름, 파일 어디에도 원문 없음) → 인증 성공(session_id·source) → `touch` 후 `last_used_at` → 취소 후 인증 None → 다른 세션이 취소하면 NotFound → 두 번 취소는 조용히 통과; `list_source_tokens` 순서·취소 표시; `insert_chain` 에 `callback_url`·`items` 저장·`get_chain` 으로 읽힘; `chains_awaiting_callback` — callback_url 없는 체인 제외, sent 된 체인 제외, attempts ≥ max 제외, next_at 미래 제외·과거 포함; `record_callback_attempt` ok/실패 각각의 컬럼 변화, 없는 체인 NotFound.

## Acceptance Criteria

```bash
python3 -m pytest tests/workflow/adapters -q
python3 -m pytest -q
python3 -m ruff check .
grep -rn "wfs_" src/workflow/adapters/repo.py | grep -v "SOURCE_TOKEN_PREFIX" | wc -l    # 0 — 접두사는 상수 한 곳에만
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - 토큰 원문을 DB·로그·예외 메시지에 넣지 않는가? (AGENTS.md CRITICAL 비밀값 규칙 — `wfs_` 는 `wfc_` 와 같은 취급)
   - `adapters/` 가 `server/`·`connector/` 를 import 하지 않는가?
   - 스키마 변경이 재생성 정책(마이그레이션 없음)을 따르는가? — `init_schema` 의 버전 검사가 그대로인가?
   - 함수 이름이 GLOSSARY·ARCHITECTURE 와 같은가?
3. 결과에 따라 `phases/7-n8n-gateway/index.json` 의 해당 step 을 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"` (테이블·컬럼·repo 함수 시그니처를 적는다)
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- 마이그레이션 코드(ALTER TABLE 분기)를 만들지 마라. 이유: 이 프로젝트는 스키마 버전으로 재생성한다(ADR-0009 트레이드오프, DEPLOY 7b `WORKFLOW_RESET_DB=1`).
- `chains.source` 에 `'n8n'` 을 넣으면서 `adapters/task_sources.SOURCES` 에도 넣지 마라. 이유: `SOURCES` 는 fixture 파일이 있는 출처 목록이며 가져오기 화면·`load_issues` 가 그것으로 파일을 찾는다.
- 토큰 인증에 세션 쿠키 검사를 섞지 마라. 이유: 입구 API 는 Bearer 만 본다(ARCHITECTURE 인증 표).
- `server/`·`domain/` 코드를 건드리지 마라. 이유: 각각 step 4~6 의 범위다.
- 기존 테스트를 깨뜨리지 마라. `test_db.py` 의 버전 단언(3)은 4 로 고친다 — 그것은 이 step 의 변경이다.
