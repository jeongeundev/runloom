# Step 10: diag-service

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/docs/ARCHITECTURE.md` — "진단 모델과 평가 기준"(처리 순서, 첨부는 서비스가 조립), "실행 이벤트"(진단 API 도 같은 논리 이벤트), "진단 결과와 근거", "모델 호출 예산 — 총액 US$30" 표, "진단 API" 행
- `/docs/CONTRACT.md` — 1절(`POST /runs` 202/200/409, `GET /runs` 이벤트), 5·6절(결과), 10절(429)
- `/docs/adr/0003-diagnosis-model-openai-gpt41-mini.md` — 작업 가정. 유료 호출 금지
- `/docs/PRD.md` — "A에 전달할 조사 요청", "진단 결과 형식", "예상 조사 순서"
- `/src/diagnostic_demo/tools/` (Step 9), `/src/workflow/contracts/v1.py` (Step 1), `/src/workflow/domain/verification.py` (Step 3, 테스트에서만)

## 작업

진단 데모 서비스를 만든다: 접수·상태 API, 모델 호출 루프, 결과 조립. **이 step 은 OpenAI 를 실제로 호출하지 않는다.** `OpenAIModelClient` 는 구현하되 테스트는 `FakeModelClient` 로만 돈다. 실호출은 Step 17.

### `src/diagnostic_demo/settings.py`

환경변수: `DIAG_DB_PATH`(기본 `data/diag.sqlite`), `DIAG_ARTIFACT_DIR`, `DIAG_FIXTURES_DIR`(기본 패키지의 `fixtures/`), `DIAG_API_TOKEN`, `OPENAI_API_KEY`, `DIAG_MODEL_ID`(기본 `gpt-4.1-mini-2025-04-14`), `DIAG_MODEL=openai|fake`(기본 openai), `DIAG_ALLOWED_WORKFLOWS`(기본 `daily-report`), 예산 `DIAG_BUDGET_USD=30`, `DIAG_BUDGET_STOP_RATIO=0.9`, `DIAG_PRICE_INPUT_PER_M`, `DIAG_PRICE_OUTPUT_PER_M`(단가. 기본 0 이면 비용 추정 불가로 기록), 상한 `DIAG_MAX_CALLS=15`, `DIAG_MAX_INPUT_TOKENS=80000`, `DIAG_MAX_OUTPUT_TOKENS=8000`, `DIAG_TIMEOUT_SECONDS=300`, `DIAG_GLOBAL_DAILY=60`. `DIAG_DEV=1` 이면 토큰 자동 생성.

### `src/diagnostic_demo/db.py`

자체 SQLite (중앙 DB 와 별개). 테이블 `runs`(execution_id PK, request_json, request_hash, status, last_event_seq, result_artifact_id, error_json, accepted_at, started_at, finished_at), `run_events`(PK(execution_id, seq), type, occurred_at, data_json), `artifacts`(artifact_id PK, execution_id, kind, content_type, sha256, size, store_ref), `usage`(execution_id, model_id, input_tokens, output_tokens, calls, estimated_usd, recorded_at). `ArtifactStore` 는 중앙 것과 같은 구현을 쓰고 싶지만 `workflow.adapters` 를 import 할 수 없으므로 `diagnostic_demo/artifact_store.py` 에 같은 규약(내용 주소, 임시 → rename)으로 작게 다시 만든다.

### `src/diagnostic_demo/api/app.py` — `create_app(settings=None)`, `app` (`DIAG_SKIP_APP=1` 이면 None)

| 경로 | 동작 |
|---|---|
| `POST /runs` | Bearer `DIAG_API_TOKEN` 아니면 401. 본문 `ExecutionRequest`(kind 는 diagnosis 만, 아니면 422). 같은 ID·같은 내용(의미 필드 비교, 키 순서 무시) → 200 현재 상태. 같은 ID·다른 내용 → 409 `execution_conflict`(CONTRACT 1절 문구). 하루 전체 건수 ≥ `DIAG_GLOBAL_DAILY` → 429 `daily_limit_reached`. 누적 비용 ≥ 예산×비율 → 429 `budget_exhausted`(details `estimated_usd`, `limit_usd`). 새 접수 → `runs` 에 `accepted`, seq 1 `accepted` 이벤트, 202 `RunStatus` |
| `GET /runs/{id}?after_seq=` | 200 `RunStatus`(events 는 after_seq 이후). 없으면 404 |
| `GET /runs/{id}/artifacts/{artifact_id}` | 그 실행의 산출물만. 아니면 403 |
| `GET /capabilities` | `{"contract_version": 1, "role": "operations.diagnose", "workflow_ids": [...], "tool_contract_version": "tools-v1", "model_id": ..., "prompt_version": ...}` |
| `GET /budget` | `{"estimated_usd", "limit_usd", "stop_ratio", "runs_today"}` |

### `src/diagnostic_demo/worker/prompt.py`

`PROMPT_VERSION = "diag-prompt-v1"`, `SYSTEM_PROMPT`(한국어): 역할, 도구 4개 사용 규칙, 예상 조사 순서(실패 실행 → 직전 정상 실행 → 응답·로그 비교 → 문서 목록·본문), 인용 규칙(읽은 근거만, `location` 문법: JSON `$.a.b`, 텍스트 `lines:N-M`, 문서는 `$.machine.*` 만), 결론 규칙(변경 안내를 읽지 못하면 `needs_information`, 원인이 `response_path_changed` 가 아니면 `unsupported_diagnosis`), 출력 형식(`DiagnosisDraft` 스키마). 고정 답변·정답 문장을 프롬프트에 넣지 않는다.

### `src/diagnostic_demo/worker/model.py`

```python
class DiagnosisDraft(BaseModel):    # 모델이 쓰는 부분. attachments·provenance·contract_version·execution_id 는 서비스가 채운다
    outcome, summary, findings, diagnosis, repair_request, missing_information   # 타입은 v1 과 같은 하위 모델 재사용
@dataclass class ToolCall: call_id: str; name: str; arguments: dict
@dataclass class ModelTurn: tool_calls: list[ToolCall]; draft: DiagnosisDraft | None; input_tokens: int; output_tokens: int; raw_id: str | None
class ModelClient(Protocol):
    def start(self, system: str, user: str, tools: list[dict], schema: dict) -> ModelTurn
    def continue_with_tool_results(self, results: list[tuple[str, dict]]) -> ModelTurn     # (call_id, output)
class OpenAIModelClient(ModelClient):
    # openai.OpenAI(api_key).responses.create(model=, instructions=system, input=[...], tools=TOOL_SCHEMAS, text={"format": {"type": "json_schema", "name": "diagnosis_draft", "schema": ..., "strict": True}}, previous_response_id=...)
    # 도구 호출 출력(type=function_call)을 ToolCall 로, 최종 텍스트를 DiagnosisDraft 로. usage 를 토큰 수로
class FakeModelClient(ModelClient):
    def __init__(self, script: list[ModelTurn])     # 순서대로 돌려준다. 테스트·로컬 e2e 용
```

OpenAI SDK 는 설치된 버전(`openai` 3.x, `client.responses.create` 존재 확인됨)의 인터페이스를 쓴다. 이 step 에서 네트워크 호출을 하는 테스트를 만들지 않는다. `OpenAIModelClient` 는 `openai.OpenAI` 를 생성자 인자로 주입받아 테스트에서 가짜 객체로 대체할 수 있게 한다 — 응답 파싱 테스트는 가짜 응답 객체로 한다.

### `src/diagnostic_demo/worker/loop.py`

```python
@dataclass class Budget: max_calls: int; max_input_tokens: int; max_output_tokens: int; timeout_seconds: float
class BudgetExceeded(Exception): code: str   # "budget_exceeded" | "timeout"
def run_diagnosis(execution_id: str, request: ExecutionRequest, tools: Tools, model: ModelClient,
                  budget: Budget, clock: Callable[[], float], emit: Callable[[str, dict], None]) -> tuple[DiagnosisDraft, ToolTraceRecorder, Usage]
```

- `emit("started", {"runtime_ref": ...})` 후 루프: 모델 턴 → 도구 호출이 있으면 각각 `tools.call`(인자·권한 검사는 Tools 안) → `emit("progress", {"message": f"{tool} {요약} 조회 완료|실패"})` → 결과 반환 → 반복. draft 가 오면 종료.
- 호출 수·토큰·경과 시간이 상한을 넘으면 `BudgetExceeded`.
- `DiagnosisDraft` 의 `evidence_refs` 중 trace 에 없는 `(evidence_id, version)` 이 있으면 **모델에 한 번 더 기회**를 주지 않고 그대로 반환한다. 판정은 중앙 검증기가 한다 (서비스는 사실만 기록).

### 결과 조립 — `src/diagnostic_demo/worker/assemble.py`

```python
def assemble_result(draft, request, recorder, store: FixtureStore, artifact_store, db_conn, provenance_base: dict) -> tuple[DiagnosisResult, str]   # (결과, result_artifact_id)
```

- `attachments` = `recorder.returned_set()` 의 각 근거를 `store.raw_bytes` 로 읽어 산출물(kind `evidence`) 저장, `sha256` 기록. 모델이 인용했든 안 했든 **읽은 것은 전부** 첨부한다 (검증기의 `attachments_in_trace` 는 첨부 ⊆ 읽음이므로 안전).
- `tool_trace` 산출물 저장 → `provenance.tool_trace_artifact_id`.
- `provenance.model_id`, `prompt_version`, `tool_contract_version`.
- `DiagnosisResult` 로 검증(계약 위반이면 실행 `failed` code `result_schema_invalid`, 초안은 산출물로 보존) → kind `diagnosis_result` 저장 → `result_ready` 이벤트.

### `src/diagnostic_demo/worker/__main__.py` + `runner.py`

`runner.process_one(conn, settings, model_factory) -> bool`: `accepted` 실행 하나를 잡아(`started_at` 기록으로 잠금) `run_diagnosis` → `assemble_result` → 이벤트·usage 기록. 실패는 `failed` 이벤트(`process_stopped: true`). `__main__` 은 3초 간격 루프, `DIAG_MODEL=fake` 면 `FakeModelClient` 에 **fixture 기반 대본**(`worker/fake_script.py`: get_run → list_runs → read_evidence ×6 → CONTRACT 5절과 같은 draft) 을 쓴다. openai 인데 키가 없으면 시작하지 않고 stderr 에 이유를 적고 exit 2.

### 비용 — `src/diagnostic_demo/pricing.py`

`estimate_usd(input_tokens, output_tokens, price_in_per_m, price_out_per_m) -> float`, `total_estimated_usd(conn) -> float`.

### 테스트 — `tests/diagnostic_demo/api/test_app.py`, `tests/diagnostic_demo/worker/test_loop.py`, `test_assemble.py`, `test_model.py`, `test_runner.py`, `tests/diagnostic_demo/test_pricing.py`

- API: CONTRACT 1절 202/200/409 세 경우 본문 그대로, 401, `kind=code_change` 422, `after_seq` 이벤트 필터, 산출물 403, 429 두 종류(usage 를 DB 에 넣어 예산 초과 상태를 만든다).
- loop: Fake 대본 정상 → draft + trace 6건 + progress 이벤트 수. 호출 16회 대본 → `BudgetExceeded("budget_exceeded")`. clock 을 조작해 `timeout`. 도구 인자 오류는 모델에 오류 dict 로 돌려주고 루프는 계속.
- assemble: 정상 → `DiagnosisResult` 검증 통과, 첨부 = 읽은 근거 전부, 해시가 fixture 와 일치, trace 산출물 저장. 그리고 **중앙 검증기 통합**: `workflow.domain.verification.verify_diagnosis` 에 넣으면 `passed`. 대본에서 `upstream-response-change` 를 읽지 않고 `needs_information` 초안 → 결과 검증 통과, 검증기 `undecidable`. 초안이 읽지 않은 근거를 인용 → 결과는 조립되고 검증기 `failed`.
- model: 가짜 OpenAI 응답 객체(function_call 출력 / 최종 JSON) 파싱. `previous_response_id` 가 이어진다.
- runner: `process_one` 이 `accepted` → `running` → `result_ready` 로 이벤트를 남기고 usage 를 기록. 예외 시 `failed`.
- pricing: 단가 0 이면 0, 계산 예.

### GLOSSARY

`DiagnosisDraft`(모델이 작성하는 결과 초안. 첨부·provenance 가 없다. 서비스가 `DiagnosisResult` 로 완성), `ModelClient`, `Budget` 을 추가한다.

## Acceptance Criteria

```bash
python3 -m pytest tests/diagnostic_demo -q
python3 -m pytest -q
python3 -m ruff check .
DIAG_DEV=1 DIAG_SKIP_APP= python3 -c "from diagnostic_demo.api.app import app; print(type(app).__name__)"
```

## 검증 절차

1. 위 AC 커맨드를 실행한다. 네트워크 호출이 없었는지 확인한다 (테스트에 `OPENAI_API_KEY` 를 쓰는 곳이 없다).
2. 아키텍처 체크리스트:
   - `diagnostic_demo` 가 중앙 DB·`workflow.adapters`·`workflow.server` 를 import 하지 않는가?
   - 모델이 DB·파일 경로를 직접 받지 않는가? 도구 인자가 `Tools.call` 의 검증을 거치는가?
   - 첨부 본문·해시를 모델 출력이 아니라 `FixtureStore` 원문에서 조립하는가?
   - `OPENAI_API_KEY` 가 로그·응답·산출물에 없는가?
3. `phases/0-mvp/index.json` 의 step 10 을 업데이트한다 (summary 에 API 경로, `run_diagnosis` 시그니처, fake 대본 위치).

## 금지사항

- OpenAI 를 실제 호출하는 테스트를 만들지 마라. 이유: ADR-0003 확정 조건 전 유료 호출 금지.
- 정답 문장을 프롬프트나 코드에 박아 결과를 만들지 마라. 이유: 원칙 "고정 답변 재생 금지". fake 대본은 테스트·로컬 e2e 전용이며 `DIAG_MODEL=fake` 를 명시해야만 쓰인다.
- 첫 시도 실패 시 다른 모델로 조용히 대체하지 마라. 이유: ARCHITECTURE. 실패로 기록한다.
- 중앙의 검증 로직을 진단 서비스에 복사하지 마라. 이유: 판정은 중앙(Step 3·8). 서비스는 사실 기록.
- 기존 테스트를 깨뜨리지 마라.
