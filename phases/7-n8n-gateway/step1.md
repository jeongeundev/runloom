# Step 1: contracts-inbound

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/AGENTS.md`
- `/docs/adr/0010-n8n-inbox-and-callback.md` (step 0 이 만든 정본), `/docs/adr/0009-registered-kinds-and-succession-rules.md`
- `/docs/CONTRACT.md` 12절 (step 0 이 쓴 JSON 예시 — 필드명은 여기와 같아야 한다), 공통 규칙 절(`contract_version`·`extra=forbid`·오류 본문)
- `/docs/GLOSSARY.md` (`InboundChainRequest`·`ChainCallback`·`TaskSource` 행)
- `/src/workflow/contracts/v1.py` 전부 — `_Contract` 기반 클래스(`extra="forbid"`, strict), `NonEmptyStr`·`ContractVersion`·`KindId`·`ErrorBody`, `HandoffBundle`·`GenericResult` 의 검증기 작성 방식(`@model_validator(mode="after")`)
- `/src/workflow/domain/task_sources.py` (`Source` Literal, `Issue`)
- `/src/workflow/adapters/task_sources.py` 의 `_check_references` — 같은 요청 안 `blocked_by` 검사의 기존 규칙(자기 참조 금지·모르는 key 금지)
- `/tests/workflow/contracts/test_v1.py`, `/tests/workflow/domain/test_task_sources.py` — 테스트 작성 방식

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

TDD: 테스트를 먼저 쓰고(`tests/workflow/contracts/test_v1.py`, `tests/workflow/domain/test_task_sources.py`) 실패를 확인한 뒤 구현한다. 계약 층과 도메인의 `Source` 타입만 바꾼다.

### 1. `src/workflow/contracts/v1.py` — 입구·callback 계약 추가

모두 `_Contract` 를 상속한다. 기존 모델은 고치지 않는다.

```python
class InboundItem(_Contract):
    key: NonEmptyStr
    title: NonEmptyStr
    body: str                      # Task.request 가 된다 — 명령·경로로 해석하지 않는다
    labels: list[str]
    blocked_by: list[str]

class InboundChainRequest(_Contract):
    contract_version: ContractVersion
    items: list[InboundItem]       # 1~10개
    callback_url: str | None = None
    # 검증기: items 비어 있음 → 오류, 10개 초과 → 오류, key 중복 → 오류,
    #         blocked_by 가 자기 자신이거나 같은 요청의 key 가 아니면 → 오류 (adapters/task_sources._check_references 와 같은 규칙),
    #         callback_url 은 http:// 또는 https:// 로 시작하고 공백 없음, 2048자 이하. 그 외 스킴(file:, ftp:, javascript: …) → 오류

class InboundTaskRef(_Contract):
    task_id: NonEmptyStr
    key: NonEmptyStr
    kind: KindId
    status: NonEmptyStr            # USER_STATUS_LABELS 의 문구

class InboundSkipped(_Contract):
    key: NonEmptyStr
    reason: NonEmptyStr

class InboundChainResponse(_Contract):
    contract_version: ContractVersion
    chain_id: NonEmptyStr
    chain_url: str | None
    started: bool
    start_error: ErrorBody | None
    tasks: list[InboundTaskRef]
    skipped: list[InboundSkipped]

class CallbackGate(_Contract):
    label: NonEmptyStr
    status_label: NonEmptyStr
    reason: str

class CallbackTask(_Contract):
    task_id: NonEmptyStr
    key: str                       # source_ref. 단독 Task 도 key 가 있다
    kind: KindId
    title: NonEmptyStr
    status: NonEmptyStr
    status_reason: str
    outcome: str | None
    summary: str | None
    task_url: str | None

class ChainCallback(_Contract):
    contract_version: ContractVersion
    chain_id: NonEmptyStr
    title: NonEmptyStr
    source: Literal["n8n"]
    chain_url: str | None
    settled_at: NonEmptyStr        # RFC 3339 UTC (utc_now 형식)
    human_gate: CallbackGate
    tasks: list[CallbackTask]      # 1개 이상
```

검증기의 오류 메시지는 한국어 한 문장으로, 무엇이 왜 안 되는지 적는다(예: `blocked_by 의 fix 가 같은 요청의 key 가 아닙니다`). 기존 `_Contract` 의 strict 설정 때문에 `labels` 에 문자열이 아닌 값이 오면 자동으로 거부된다 — 따로 검사하지 않는다.

### 2. `src/workflow/domain/task_sources.py`

`Source = Literal["github", "jira", "n8n"]` 로 바꾼다. `Issue`·`map_issue` 는 그대로다 — n8n 은 라벨 규칙을 그대로 쓴다. 모듈 docstring 첫 줄의 "외부 출처(GitHub·Jira)" 를 "외부 출처(GitHub·Jira·n8n)" 로만 고친다.

### 3. 테스트

`tests/workflow/contracts/test_v1.py` 에: 정상 요청 1건(항목 2개, `callback_url` 있음/없음), items 0개·11개·key 중복·blocked_by 자기 참조·모르는 key·`callback_url` 스킴 오류(`ftp://`, `javascript:`)·공백 포함·알 수 없는 필드(`extra=forbid`) 각각 `ValidationError`, `InboundChainResponse`·`ChainCallback` 의 round-trip(`model_validate(model_dump(mode="json"))`), `ChainCallback.tasks` 빈 목록 거부, `source` 가 `github` 이면 거부.
`tests/workflow/domain/test_task_sources.py` 에: `Issue(source="n8n", url=None, …)` 로 `map_issue` 가 github 과 같은 매핑을 낸다(`incident`+`workflow:`+`run:` → `operations.diagnose`).

## Acceptance Criteria

```bash
python3 -m pytest tests/workflow/contracts tests/workflow/domain -q
python3 -m pytest -q
python3 -m ruff check .
python3 -c "from workflow.contracts.v1 import InboundChainRequest, InboundChainResponse, ChainCallback, InboundItem; print('ok')"
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - `contracts/` 가 FastAPI·sqlite3·HTTPX·도메인 모듈을 import 하지 않는가?
   - `domain/` 이 FastAPI·sqlite3·HTTPX·subprocess·Git 을 import 하지 않는가? (AGENTS.md CRITICAL)
   - 필드명이 CONTRACT 12절·GLOSSARY 와 정확히 같은가?
   - 요청 본문에서 경로·명령을 받아 실행하는 필드가 없는가? (`body` 는 문자열로만 저장된다)
3. 결과에 따라 `phases/7-n8n-gateway/index.json` 의 해당 step 을 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"` (추가한 클래스 이름·검증 규칙을 적는다)
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- 기존 계약 모델(`ExecutionRequest`·`HandoffBundle`·결과 봉투 등)의 필드를 바꾸지 마라. 이유: 연결 프로그램·진단 데모가 같은 계약을 쓴다.
- `callback_url` 을 `pydantic.HttpUrl` 로 두지 마라. 이유: 정규화(끝 `/` 추가 등)가 일어나 n8n 이 준 resume URL 과 달라질 수 있다. 문자열 + 스킴 검사만 한다.
- `InboundItem` 에 `kind`·`scope`·`run_id` 같은 별도 필드를 추가하지 마라. 이유: 매핑 경로는 라벨 규칙 하나다(ADR-0010).
- 서버·DB·워커 코드를 건드리지 마라. 이유: 각각 step 3~5 의 범위다.
- 기존 테스트를 깨뜨리지 마라.
