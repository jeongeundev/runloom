# Step 9: diag-fixtures-tools

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/docs/PRD.md` — "데모 데이터와 검증 상세"(응답·이력·로그·문서 내용), "진단 조회 도구와 A → B 인계"(도구 4개의 입력·반환, 실패 구분, 조회 이력)
- `/docs/ARCHITECTURE.md` — "API 에이전트의 자료 범위"(범위 밖 `access_denied`), "진단 결과와 근거"(문서 첨부 `{markdown, machine}` 필드)
- `/docs/adr/0000-principles.md` — "가상 데이터를 실제 기업 사례로 소개하지 않는다"
- `/phases/0-mvp/step3.md` 의 "실행 기록·문서 첨부의 형식" 절 — Step 3 검증기가 기대하는 fixture 형식. 글자 단위로 맞춘다
- `/src/workflow/domain/verification.py` (Step 3), `/src/workflow/contracts/v1.py` (Step 1)

## 작업

진단 데모 서비스의 가상 자료와 읽기 전용 조회 도구 4개를 만든다. `src/diagnostic_demo/` 는 중앙 DB 에 접근하지 않고 `workflow.contracts` 만 import 한다.

### fixture — `src/diagnostic_demo/fixtures/`

```text
fixtures/README.md                      # "가상 데모 자료. 미리디 공고에서 착안한 시나리오이며 실제 기업의 데이터·장애가 아니다."
fixtures/index.json                     # 아래 형식
fixtures/evidence/<evidence_id>/<version>.<json|txt>
```

`index.json`:

```json
{
  "workflows": ["daily-report"],
  "runs": [
    {"run_id": "daily-0919-0900", "workflow_id": "daily-report", "started_at": "2026-09-19T09:00:00+09:00", "status": "succeeded", "code_version": "report-base", "evidence": {"evidence_id": "run-daily-0919-0900", "version": "1"}},
    {"run_id": "daily-0920-0900", "workflow_id": "daily-report", "started_at": "2026-09-20T09:00:00+09:00", "status": "failed", "code_version": "report-base", "evidence": {"evidence_id": "run-daily-0920-0900", "version": "1"}}
  ],
  "documents": [
    {"evidence_id": "daily-report-runbook", "version": "1", "workflow_id": "daily-report", "title": "일일 보고서 런북", "effective_at": "2026-09-01T00:00:00+09:00"},
    {"evidence_id": "upstream-response-change", "version": "1", "workflow_id": "daily-report", "title": "집계 API 응답 형식 변경 안내", "effective_at": "2026-09-20T00:00:00+09:00"},
    {"evidence_id": "daily-report-contract", "version": "1", "workflow_id": "daily-report", "title": "일일 보고서 입력·출력 계약", "effective_at": "2026-09-01T00:00:00+09:00"}
  ],
  "content_types": {"json": "application/json", "txt": "text/plain"}
}
```

evidence 파일 (버전 `1`):

| evidence_id | 형식 | 내용 |
|---|---|---|
| `run-daily-0919-0900` | json | Step 3 형식의 정상 실행 기록 |
| `run-daily-0920-0900` | json | Step 3 형식의 실패 실행 기록 (`stages[1].error_code = "MISSING_RECORDS_FIELD"`) |
| `response-before` | json | PRD 의 `response-before` 그대로 |
| `response-after` | json | PRD 의 `response-after` 그대로 |
| `log-daily-0919` | txt | 0919 정상 실행 로그 4줄 (fetch 200 → transform ok → render ok → run_finished status=succeeded report_created=true) |
| `log-daily-0920` | txt | PRD 의 `log-daily-0920` 4줄 그대로 |
| `report-0918` | txt | PRD 기대 보고서 형식으로 2026-09-18 보고서 (합계 20·5) |
| `daily-report-runbook` | json | `{"markdown": "...", "machine": {...}}` — machine 은 Step 3 형식. markdown 은 PRD 문서표의 내용을 한국어로 10줄 내외, 첫 줄에 "(가상 데모 자료)" |
| `upstream-response-change` | json | 같은 형식. machine 은 Step 3 형식 |
| `daily-report-contract` | json | 같은 형식. machine 은 Step 3 형식 |

### `src/diagnostic_demo/tools/store.py`

```python
class ToolError(str, Enum): not_found = "not_found"; access_denied = "access_denied"; unavailable = "unavailable"

@dataclass(frozen=True)
class ToolResult:
    ok: bool
    content: Any | None                     # JSON 은 dict/list, 텍스트는 str
    content_type: str | None
    error: ToolError | None
    returned: tuple[EvidenceVersion, ...]   # 이 호출이 실제로 반환한 근거. 목록 조회는 빈 튜플

class FixtureStore:
    def __init__(self, root: Path, allowed_workflow_ids: frozenset[str],
                 removed: frozenset[tuple[str, str]] = frozenset(),
                 replaced: Mapping[tuple[str, str], bytes] = {},
                 unavailable: frozenset[tuple[str, str]] = frozenset())
    def get_run(self, run_id: str) -> ToolResult              # 실행 기록 evidence 본문 반환. returned = (run-…@1,)
    def list_runs(self, workflow_id: str, before: str | None, status: str | None, limit: int = 10) -> ToolResult   # 최신순 요약. returned = ()
    def list_documents(self, workflow_id: str) -> ToolResult   # index.documents 항목. returned = ()
    def read_evidence(self, evidence_id: str, version: str) -> ToolResult
    def sha256_of(self, evidence_id: str, version: str) -> str
    def raw_bytes(self, evidence_id: str, version: str) -> bytes
```

- 범위: `workflow_id` 가 `allowed_workflow_ids` 밖이면 `access_denied` (빈 결과가 아니라 오류). `run_id` 의 workflow 가 범위 밖이어도 `access_denied`. 없는 ID 는 `not_found`.
- `removed`·`replaced`·`unavailable` 은 Step 17 평가와 테스트가 자료 누락·충돌·일시 오류 사례를 만들 때 쓴다. `unavailable` 에 있으면 `ToolError.unavailable`.
- 파일 I/O 는 이 클래스에만 있다.

### `src/diagnostic_demo/tools/trace.py`

```python
@dataclass
class TraceRecord: call_id: str; tool: str; input: dict; ok: bool; error: str | None; returned: list[dict]   # returned 항목 {evidence_id, version, sha256}
class ToolTraceRecorder:
    def record(self, tool: str, input: dict, result: ToolResult, store: FixtureStore) -> TraceRecord   # call_id 는 순번
    def entries(self) -> list[TraceRecord]
    def to_json(self) -> bytes                 # {"contract_version": 1, "tool_contract_version": "tools-v1", "entries": [...]}
    def returned_set(self) -> set[tuple[str, str]]
```

Step 3 의 `TraceEntry` 와 필드가 대응돼야 한다 (`call_id`, `tool`, `input`, `ok`, `returned`).

### `src/diagnostic_demo/tools/api.py`

```python
TOOL_CONTRACT_VERSION = "tools-v1"
TOOL_SCHEMAS: list[dict]        # OpenAI function calling 형식 4개. 입력 스키마는 PRD 표 그대로. `strict: true`, 추가 속성 금지
class Tools:
    def __init__(self, store: FixtureStore, recorder: ToolTraceRecorder)
    def call(self, name: str, arguments: dict) -> dict     # 인자 검증(알 수 없는 도구·키 → ValueError) → store 호출 → recorder.record → 모델에 돌려줄 dict {"ok", "content", "content_type", "error", "evidence_id", "version"}
```

### 테스트 — `tests/diagnostic_demo/tools/test_store.py`, `test_trace.py`, `test_api.py`

- fixture 무결성: `index.json` 의 모든 run·document 가 evidence 파일을 가진다. 각 evidence JSON 이 파싱된다. 문서는 `markdown`·`machine` 키를 가진다. 실행 기록·문서 machine 이 Step 3 형식의 필수 키를 가진다.
- Step 3 검증기와의 정합: fixture 8개를 `LoadedEvidence` 로 싣고 CONTRACT 5절 결과(해시를 fixture 로 재계산) + 전체 반환 trace → `verify_diagnosis(...).outcome == "passed"`. 이 테스트가 fixture 와 검증기의 계약이다.
- 도구: `get_run` 정상/`not_found`/범위 밖 `access_denied`, `list_runs` 최신순·`before` 필터·`status` 필터, `list_documents`, `read_evidence` 정상·`removed` → `not_found`·`unavailable` → `unavailable`·`replaced` 내용 반영.
- trace: 실제 반환한 근거만 `returned` 에 있다. 실패 호출은 `ok=False`, `returned=[]`.
- api: 알 수 없는 도구·인자 거부. `TOOL_SCHEMAS` 이름이 `Tools.call` 이 받는 이름과 같다.
- README 에 "실제 기업" 부인 문구가 있다.

### GLOSSARY

`FixtureStore`(진단 데모의 가상 자료 저장소. 조회 범위와 누락·교체·일시 오류 재현 옵션), `ToolResult`, `TraceRecord`(진단 서비스가 기록한 조회 한 건. 중앙의 `TraceEntry` 로 읽힌다) 를 추가한다.

## Acceptance Criteria

```bash
python3 -m pytest tests/diagnostic_demo/tools -q
python3 -m pytest -q
python3 -m ruff check .
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트:
   - `src/diagnostic_demo/` 가 `workflow.adapters`·`workflow.server` 를 import 하지 않는가? (테스트만 `workflow.domain.verification` 을 써도 된다)
   - 접근 거절·조회 오류를 빈 본문으로 바꾸는 곳이 없는가?
   - fixture 에 실제 기업명·실제 URL 이 없는가? (미리디 공고 링크는 docs 에만)
3. `phases/0-mvp/index.json` 의 step 9 를 업데이트한다 (summary 에 fixture 목록과 도구 클래스 위치).

## 금지사항

- 도구가 진단 결론이나 힌트("원인은 경로 변경")를 반환하지 마라. 이유: 도구는 사실 자료만 반환한다. 판단은 모델.
- 벡터 검색·임베딩·OpenArchive 를 넣지 마라. 이유: ADR-0000 첫 범위 밖.
- fixture 를 실제 운영 데이터처럼 표시하지 마라. 이유: 원칙. README 와 markdown 첫 줄에 가상 표시.
- 기존 테스트를 깨뜨리지 마라.
