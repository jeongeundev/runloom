# Step 5: worker-callback

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/AGENTS.md`
- `/docs/adr/0010-n8n-inbox-and-callback.md`, `/docs/adr/0004-central-service-rule-based-no-llm.md`
- `/docs/ARCHITECTURE.md` "n8n 입구와 출구" 절 (출구), "구성과 책임"(실행 조정 워커), "타임아웃과 폴링 — 초기값"
- `/docs/CONTRACT.md` 12절 (`ChainCallback` 예시)
- `/docs/GLOSSARY.md` (`ChainCallback`·`chain_settled`·`TickReport`·`human gate`·`outcome`)
- `/src/workflow/server/worker.py` 전부 — 모듈 docstring(단계 순서·트랜잭션 밖 HTTP 규칙), `TickReport`, `Worker.__init__`·`tick`·`run_forever`, `_refresh_task`, `_result_outcome`, `_read_owned`, `_spawn_successors`, `_reflect_failures`, `main`
- `/src/workflow/server/views.py` — `build_task_view`, `task_summary`, `chain_summary`, `_human_gate`, `_LIVE_LABELS`
- `/src/workflow/adapters/diag_client.py` — HTTPX 클라이언트·예외 작성 방식 (`Protocol` + 구현, 헤더·본문을 메시지에 넣지 않음)
- `/src/workflow/domain/settlement.py`, `/src/workflow/domain/callback_policy.py` (step 2), `/src/workflow/domain/status.py` (`user_status`)
- `/src/workflow/adapters/repo.py` 의 `chains_awaiting_callback`·`record_callback_attempt`·`tasks_of_chain`·`list_executions`·`read_artifact` (step 3)
- `/src/workflow/server/settings.py` 의 `callback_hosts`·`public_url` (step 4)
- `/tests/workflow/server/test_worker.py` 전부 — `Clock`, `FakeDiagServer`, `make_worker`, `seed_flow`, `run_to_result`, `_status`; `/tests/workflow/adapters/test_diag_client.py` — HTTPX 클라이언트 테스트 방식(`httpx.MockTransport`)

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

TDD: `tests/workflow/adapters/test_callback_client.py`(신규)·`tests/workflow/server/test_worker.py` 에 테스트를 먼저 쓰고 실패를 확인한 뒤 구현한다. 어댑터 하나와 워커의 새 단계만 만든다.

### 1. `src/workflow/adapters/callback_client.py` (신규)

```python
"""callback HTTP 클라이언트 — 중앙 워커 → n8n Wait 노드 (ADR-0010). 본문·응답·헤더를 로그·예외 메시지에 넣지 않는다."""

class CallbackFailed(Exception):
    """연결 오류·시간 초과·2xx 아님. 메시지는 `HTTP 404` / `연결 오류: <예외 클래스 이름>` 처럼 짧게."""

class CallbackClient(Protocol):
    def post(self, url: str, payload: dict[str, Any]) -> None: ...

class HttpCallbackClient:
    def __init__(self, timeout_seconds: float = 10.0, transport: httpx.BaseTransport | None = None): ...
    def post(self, url, payload) -> None      # httpx POST json=payload, Content-Type application/json. 리다이렉트를 따라가지 않는다(follow_redirects=False — 허용 목록 우회 방지).
```

`transport` 인자는 테스트의 `httpx.MockTransport` 용이다(diag_client 와 같은 방식이 있으면 그것을 따른다).

### 2. `src/workflow/server/worker.py`

- `CALLBACK_MAX_ATTEMPTS = 5`, `CALLBACK_BACKOFF_SECONDS = 30` 상수. 다음 시도 시각은 `now + 30 · 2^(attempts-1)` 초(attempts 는 이번 실패를 더한 값: 1회 실패 → 30초 뒤, 2회 → 60초, 3회 → 120초, 4회 → 240초, 5회 → 중단).
- `TickReport` 에 `callbacks_sent: int = 0`, `callbacks_failed: int = 0` 추가(`any()` 에 포함).
- `Worker.__init__` 에 `callbacks: CallbackClient` 인자를 더한다(위치는 `diag` 다음). `main()` 은 `HttpCallbackClient()` 를 넘긴다.
- `tick` 의 마지막에 `self._deliver_callbacks(conn, report)` 를 추가한다 — `_reflect_failures` **뒤**. 모듈 docstring 의 단계 목록에 "→ callback 전달" 을 덧붙이고 "체인이 사람 차례가 되면 1회" 한 줄을 적는다.

```python
def _deliver_callbacks(self, conn, report) -> None:
    """callback_url 이 있고 아직 안 보낸 체인 중 `chain_settled` 인 것에 ChainCallback 을 1회 POST 한다 (ADR-0010).
    HTTP 는 트랜잭션 밖. 실패는 attempts·next_at 으로 물러나 재시도하고 CALLBACK_MAX_ATTEMPTS 뒤 멈춘다."""
```

흐름: `repo.chains_awaiting_callback(conn, now, max_attempts=CALLBACK_MAX_ATTEMPTS)` → 체인마다 `tasks = repo.tasks_of_chain` → 각 Task 의 사용자 상태(`user_status(views.build_task_view(...))`, 저장 상태가 아니라 지금 판정)와 선행 상태(선행은 같은 체인 안에 있으므로 task_id → 상태 사전으로 찾는다; 체인 밖이면 `repo.get_task` 로 한 번 더) → `NodeState` 목록 → `chain_settled` 거짓이면 다음 체인 → 참이면 `host_allowed(callback_url, settings.callback_hosts)` 재검사(거짓이면 `record_callback_attempt(ok=False, error="허용 목록 밖", next_at=None)` 로 기록하고 다음) → `_chain_callback(...)` 으로 본문 조립 → `self._callbacks.post(url, payload.model_dump(mode="json"))` → 성공 `record_callback_attempt(ok=True)`·`report.callbacks_sent += 1` / `CallbackFailed` → attempts+1·`next_at`·`error=str(exc)`·`report.callbacks_failed += 1`. 한 체인의 예외가 다른 체인을 막지 않게 체인 단위로 `try`.

```python
def _chain_callback(self, conn, chain: Row, tasks: list[Row], states: dict[str, UserStatus], now: str) -> ChainCallback
```
- `human_gate`: `views.chain_summary(...)["human_gate"]` 의 `label`·`status_label`·`reason` 을 쓴다(뷰 함수를 재사용하며 규칙을 복제하지 않는다). 노드가 없는 체인은 애초에 settled 가 거짓이다.
- `tasks[]`: `task_id`·`key=source_ref or ""`·`kind`·`title`·`status=states[...].label`·`status_reason=states[...].reason`·`outcome`·`summary`·`task_url`.
- `outcome`·`summary`: 그 Task 의 실행 중 `result_artifact_id` 가 있는 최신 시도의 결과 봉투를 `_read_owned` 로 읽어 JSON 의 `outcome`·`summary` 를 꺼낸다(없으면 둘 다 None). 기존 `_result_outcome` 은 그대로 두고 `_result_envelope(conn, store, execution) -> tuple[str | None, str | None]` 를 새로 두거나 `_result_outcome` 이 그것을 쓰게 한다 — 기존 호출부의 동작은 바뀌지 않아야 한다.
- `chain_url`·`task_url`: `settings.public_url` 이 비면 None, 아니면 `{public_url}/chains/{chain_id}`·`{public_url}/tasks/{task_id}`.
- `settled_at = now`, `source = "n8n"`.

### 3. 테스트

`test_callback_client.py`: `MockTransport` 로 200 → 정상, 404·500 → `CallbackFailed("HTTP 404")`, 연결 예외 → `CallbackFailed` 메시지에 URL·본문이 없음, 302 를 따라가지 않음, 요청 본문이 JSON 이고 `Content-Type: application/json`.

`test_worker.py`: 기록하는 가짜 클라이언트(`posts: list[tuple[url, payload]]`, `fail: bool`)를 `make_worker` 에 넣는 방식으로. `seed_flow` 로 A→B 체인을 만들되 `insert_chain` 에 `callback_url="http://localhost:5678/webhook-waiting/1"`, `source="n8n"` 을 넣고 `settings.callback_hosts=("localhost:5678",)` 로 둔 앱을 쓴다. 검증: (a) A 실행 중·결과 전 tick → 전송 0. (b) A 판정 통과 → B 착수 tick → 아직 0 (B 실행 요청됨). (c) B `result_ready` + 판정 → tick → 전송 1, 본문 `ChainCallback.model_validate` 통과, `tasks[1].status == "확인 필요"`, `tasks[1].outcome == "ready_for_review"`, `human_gate.status_label == "확인 필요"`, `tasks[0].outcome == "ready_for_handoff"`. (d) 이후 tick 여러 번·B 승인(`update_task_status`+finished)에도 전송은 1. (e) 실패 클라이언트 → attempts 1·`next_at = now+30s`·`last_error`, `clock` 을 29초 전진하면 재시도 없음, 31초면 재시도 → attempts 2·`next_at` 60초 뒤; 5회 실패 뒤 더 시도 없음. (f) A 가 `needs_information` 으로 완료되어 B 가 `확인 필요`(규칙 밖 outcome) → 전송 1, `tasks[1].status_reason` 에 규칙 이유. (g) `callback_url` 없는 체인 → 전송 0. (h) 허용 목록이 빈 설정 → 전송 없이 실패 기록. (i) `public_url` 있음/없음에 따른 `chain_url`. (j) `TickReport.callbacks_sent` 집계.

## Acceptance Criteria

```bash
python3 -m pytest tests/workflow/adapters/test_callback_client.py tests/workflow/server/test_worker.py -q
python3 -m pytest -q
python3 -m ruff check .
grep -n "_deliver_callbacks" src/workflow/server/worker.py     # tick 의 마지막 호출이어야 한다
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - HTTP 호출이 repo 트랜잭션 밖에서 일어나는가? (워커 모듈 docstring 규칙)
   - 워커가 모델을 부르지 않는가? (ADR-0004) — callback 본문은 저장된 상태·결과 봉투에서만 만든다.
   - callback 본문·응답·헤더가 로그·예외에 없는가? `callback_last_error` 는 짧은 코드 문구뿐인가?
   - `_deliver_callbacks` 가 `_spawn_successors`·`_reflect_failures` 뒤인가?
   - `chain_settled` 의 판정을 워커에서 복제하지 않고 도메인 함수를 부르는가?
3. 결과에 따라 `phases/7-n8n-gateway/index.json` 의 해당 step 을 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"` (클라이언트 클래스·워커 단계 이름·재시도 규칙·`Worker.__init__` 의 새 인자를 적는다)
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- callback 을 웹 핸들러(승인·시작 시점)에서 보내지 마라. 이유: HTTP 를 요청 처리 안에서 하지 않는다(워커가 DB 를 보고 진행). 시점 판정은 `chain_settled` 하나다.
- 업무마다 또는 상태가 바뀔 때마다 보내지 마라. 이유: n8n Wait 노드는 한 번만 깨어나고, 계약은 체인당 1회다.
- `record_callback_attempt(ok=True)` 를 POST 전에 쓰지 마라. 이유: 전송 실패 시 재시도가 사라진다. 대신 같은 tick 에 두 번 보내는 일은 `chains_awaiting_callback` 이 한 번만 돌려주므로 없다.
- `follow_redirects=True` 로 두지 마라. 이유: 허용 목록 검사가 리다이렉트로 우회된다.
- `_spawn_successors`·판정 단계·`_LIVE_LABELS` 를 고치지 마라. 이유: 후속 규칙·화면 규칙은 그대로이고 이 step 은 마지막 단계를 더할 뿐이다.
- 기존 테스트를 깨뜨리지 마라. `Worker(...)` 생성자에 인자가 늘어 깨지는 테스트는 `make_worker` fixture 한 곳만 고친다.
