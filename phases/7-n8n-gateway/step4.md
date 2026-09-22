# Step 4: server-inbound-api

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/AGENTS.md`
- `/docs/adr/0010-n8n-inbox-and-callback.md`, `/docs/adr/0005-access-model-anonymous-session-operator-token.md`
- `/docs/ARCHITECTURE.md` "n8n 입구와 출구" 절 (입구), "인증·권한·비밀정보 규칙" 표, "배포와 실행 예산"(상한)
- `/docs/CONTRACT.md` 12절 (요청·응답·오류 예시), 3절 오류표
- `/docs/GLOSSARY.md` (`source token`·`InboundChainRequest`·`InboundChainResponse`·`prefer`·`Chain`)
- `/src/workflow/server/web.py` 전부 — 특히 `tasks_import`(체인 생성 본체), `chain_start`, `_run_task`, `_start_execution`, `_insert_new_task`, `_session_agents`, `_candidates`, `_kinds`, `_refresh_status`, `PageError`, `install`
- `/src/workflow/server/machine_api.py` — 기계 API 의 라우터·본문 모델·`_json`·오류 방식 (새 모듈은 이 형식을 따른다)
- `/src/workflow/server/auth.py` — `require_connector`·`_bearer_token`·`get_conn`·`utc_now`
- `/src/workflow/server/errors.py` — `ApiError`·`validation_error_to_api`·`install_error_handlers`
- `/src/workflow/server/settings.py` — `Settings`·`ENV_KEYS`·`SECRET_KEYS`·`load_settings`, `/deploy/env/central.env.example`, `/tests/test_deploy_files.py` 의 `test_env_example_keys_match_what_load_settings_reads`
- `/src/workflow/server/app.py` — 라우터 포함 순서
- `/src/workflow/domain/task_sources.py` (`Issue`), `/src/workflow/domain/callback_policy.py` (step 2), `/src/workflow/contracts/v1.py` 의 `InboundChainRequest`·`InboundChainResponse`·`ErrorBody` (step 1), `/src/workflow/adapters/repo.py` 의 `issue_source_token`·`authenticate_source_token`·`touch_source_token`·`insert_chain` (step 3)
- `/tests/workflow/server/conftest.py`(fixture `settings`·`app`·`client`·`conn`·`seed_agents`·`register_catalog`), `/tests/workflow/server/test_web.py` 의 가져오기 테스트, `/tests/workflow/server/test_machine_api.py`, `/tests/workflow/server/test_auth.py`, `/tests/workflow/server/test_settings.py`

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

TDD: `tests/workflow/server/test_inbound_api.py`(신규)·`test_auth.py`·`test_settings.py` 에 테스트를 먼저 쓰고 실패를 확인한 뒤 구현한다. 서버 층(web·auth·settings·신규 inbound_api)만 바꾼다. 워커·화면 템플릿은 건드리지 않는다.

### 1. `src/workflow/server/settings.py` + `deploy/env/central.env.example`

`Settings` 에 `callback_hosts: tuple[str, ...]`(기본 `()`)·`public_url: str`(기본 `""`, 끝 `/` 는 잘라 저장) 을 더한다. `load_settings` 는 `WORKFLOW_CALLBACK_HOSTS` 를 `domain.callback_policy.parse_hosts` 로, `WORKFLOW_PUBLIC_URL` 을 문자열로 읽는다. 둘 다 `ENV_KEYS` 에 추가한다(`SECRET_KEYS` 가 아니다). `central.env.example` 에 두 키를 주석과 함께 추가한다 — `WORKFLOW_CALLBACK_HOSTS=`(공개 데모는 비워 둔다: callback 없음), `WORKFLOW_PUBLIC_URL=https://runloom.duckdns.org`. `tests/test_deploy_files.py` 가 키 일치를 검사한다.

### 2. `src/workflow/server/web.py` — 체인 생성·시작을 공개 함수로

`tasks_import` 의 본체(등록 에이전트 확인 → `compose` → 한도 → `insert_chain` → Task 삽입)와 `chain_start` 의 본체를 아래 두 공개 함수로 뽑고, 두 핸들러는 그것을 부르게 한다. 동작·오류 코드·문구는 지금과 같아야 한다(`test_web.py` 무변경 통과가 기준).

```python
@dataclass(frozen=True)
class ChainCreated:
    chain_id: str
    task_ids: dict[str, str]      # issue key → task_id (체인 노드 + 능력 있는 단독 Task)
    skipped: list[dict[str, str]] # {key, title, reason}

def create_chain(
    conn, session_id: str, issues: Sequence[Issue], *, source: str, now: str, settings: Settings,
    callback_url: str | None = None, items: list[dict] | None = None, error: type[ApiError] = ApiError,
) -> ChainCreated

def start_chain(conn, chain: Row, *, session_id: str, now: str, settings: Settings, error: type[ApiError] = ApiError) -> None
```

- 오류는 `error(...)` 로 던진다. 웹 핸들러는 `error=PageError` 를 넘겨 지금처럼 `error.html` 로 렌더되고, 입구 API 는 기본값 `ApiError` 로 JSON 이 된다.
- `_run_task`·`_start_execution` 등 다른 헬퍼는 그대로 둔다. 이동·개명하지 않는다(외과적 변경).
- `prefer` 는 지금처럼 세션 등록 순서에서 만든다.

### 3. `src/workflow/server/auth.py`

```python
def require_source_token(request: Request, conn: Connection = Depends(get_conn)) -> Row:
    """`Authorization: Bearer wfs_…` → source_tokens 행(token_id·session_id·source). 없거나 취소면 401 unauthenticated.
    성공 시 touch_source_token(last_used_at). 토큰 원문을 메시지에 넣지 않는다."""
```

### 4. `src/workflow/server/inbound_api.py` (신규)

`APIRouter`, 모듈 docstring 은 machine_api 형식. 경로 하나:

```python
@router.post("/sources/{source}/chains", status_code=201)
def inbound_chain(request, source: str, body: InboundChainRequest, token: Row = Depends(require_source_token), conn = Depends(get_conn)) -> JSONResponse
```

순서:
1. `source != "n8n"` → 404 `not_found`. `token["source"] != source` → 403 `forbidden`.
2. `body.callback_url` 이 있으면 `host_allowed(url, settings.callback_hosts)` — 거짓이면 422 `callback_host_not_allowed`(`field="callback_url"`, 메시지에 허용 목록이 비었는지 여부를 적되 목록 값은 넣지 않는다).
3. 항목 → `Issue(source="n8n", key, title, body, labels=tuple, blocked_by=tuple, url=None)`.
4. `create_chain(..., source="n8n", callback_url=..., items=[item.model_dump() for item in body.items])` — 422 `agent_not_registered`·`dependency_cycle`, 429 `active_task_limit_reached` 는 그대로 JSON 으로 나간다.
5. `start_chain(...)` 을 `try` 로 감싼다 — `ApiError` 면 `started=False, start_error=ErrorBody(...)`, 아니면 `started=True`. 체인·Task 는 이미 커밋돼 있으므로 남는다.
6. 응답 `InboundChainResponse`: `tasks` 는 `repo.tasks_of_chain` 순서로 `{task_id, key=source_ref, kind, status=저장된 status}`, `skipped` 는 `ChainCreated.skipped` 의 `{key, reason}`, `chain_url` 은 `settings.public_url` 이 있으면 `f"{public_url}/chains/{chain_id}"` 아니면 None.

`app.py` 에 `app.include_router(inbound_api.router)` 를 `machine_api` 다음에 넣는다. 본문 검증 오류는 기존 `install_error_handlers` 가 422 로 바꾼다(확인만).

### 5. 테스트 `tests/workflow/server/test_inbound_api.py`

fixture 로 세션을 만들고(`repo.create_session`) 카탈로그 에이전트를 등록(`seed_agents`·`register_catalog`)한 뒤 `repo.issue_source_token` 으로 토큰을 얻는다. `settings` fixture 의 `callback_hosts` 를 `("localhost:5678",)` 로 두는 방법은 conftest 의 `settings` 를 그대로 쓰되 `dataclasses.replace` 로 바꾼 앱을 만든다(기존 fixture 를 고치지 않는다).

검증할 것: (a) Bearer 없음 401 / 취소된 토큰 401 / `source` 경로가 `github` 이면 404 / 토큰 source 불일치 403(테스트용으로 DB 행의 source 를 직접 바꿔 만든다). (b) 정상 — 진단+수정 2항목 → 201, `started=True`, `tasks[0].status == "실행 요청됨"`(진단 실행 생성 — `FakeDiagServer` 없이도 `_start_execution` 은 실행 행을 만든다; 안 되면 `test_web.py` 의 chain start 테스트가 쓰는 방식을 따른다), `tasks[1].status == "대기"`, DB `chains.source == "n8n"`, `callback_url`·`items_json` 저장, `chain_url` 은 `public_url` 유무에 따라 값/None. (c) 첫 업무 후보 없음(에이전트 미등록 세션) → 422 `agent_not_registered`; 후보 2개(codex+claude 등록, 수정 업무만) → 201 `started=False`, `start_error.code == "selection_required"`. (d) `callback_url` 이 허용 밖 → 422 `callback_host_not_allowed`, 체인이 만들어지지 않음. (e) `callback_url` 없음 → 201, `callback_url` NULL. (f) 알 수 없는 필드 → 422 `unknown_field`, `contract_version: 2` → 422 `unsupported_contract_version`. (g) 활성 업무 한도 초과 → 429. (h) 같은 요청 두 번 → 체인 두 개(멱등 키는 없다 — 문서화된 동작).
`test_auth.py`: `require_source_token` 이 `last_used_at` 을 갱신한다. `test_settings.py`: 두 환경변수 파싱(빈 값·콤마·끝 `/` 제거).

## Acceptance Criteria

```bash
python3 -m pytest tests/workflow/server tests/test_deploy_files.py -q
python3 -m pytest -q
python3 -m ruff check .
git diff --stat HEAD -- src/workflow/server/worker.py src/workflow/server/templates    # 빈 출력 — 워커·템플릿은 손대지 않는다
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - `server/` 가 `connector/`·`scripted/` 를 import 하지 않는가?
   - 요청 본문의 `body`·`title`·`labels` 를 문자열로만 저장하고 경로·명령으로 해석하지 않는가? (AGENTS.md CRITICAL)
   - `callback_url` 을 허용 목록 검사 없이 저장하지 않는가?
   - 토큰 원문이 응답·로그에 없는가? (401 메시지에 토큰을 넣지 않는다)
   - 오류 코드·문구가 CONTRACT 12절·3절과 같은가?
   - `test_web.py` 를 고치지 않고 통과하는가? (리팩터링이 동작을 바꾸지 않았다는 증거)
3. 결과에 따라 `phases/7-n8n-gateway/index.json` 의 해당 step 을 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"` (`create_chain`·`start_chain` 시그니처, 경로, 오류 코드, 새 설정 키를 적는다)
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- 입구 API 를 세션 쿠키로도 통과시키지 마라. 이유: 브라우저에서 POST 되는 CSRF 경로가 생긴다. Bearer 만.
- `create_chain` 을 새 모듈(`server/chains.py`)로 옮기면서 `_insert_new_task`·`_run_task` 까지 끌고 가지 마라. 이유: `web.py` 안에서 공개 함수로 두고 `inbound_api` 가 `web` 을 import 하면 순환이 없고 diff 가 작다.
- 입구에서 `compose`·`map_issue` 를 우회해 Task 를 직접 만들지 마라. 이유: 매핑 경로는 하나(ADR-0010 결정 1).
- 시작 실패(429·409)를 500 이나 요청 전체 실패로 바꾸지 마라. 이유: 체인은 남기고 `started=false` 로 알리는 것이 계약이다 — n8n 은 `chain_url` 로 사람에게 넘길 수 있다.
- `WORKFLOW_CALLBACK_HOSTS`·`WORKFLOW_PUBLIC_URL` 을 `SECRET_KEYS` 에 넣지 마라. 이유: 비밀값이 아니며 화면에 보여도 된다.
- 기존 테스트를 깨뜨리지 마라.
