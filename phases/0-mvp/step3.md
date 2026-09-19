# Step 3: diagnosis-verifier

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/docs/PRD.md` — "데모 데이터와 검증 상세", "완료 판정과 실제 인계", "도구·인계 수용 기준"
- `/docs/ARCHITECTURE.md` — "진단 결과와 근거"(location 문법, 검증기가 확인하는 것), "진단 완료 검증"
- `/docs/CONTRACT.md` — 5절(`ready_for_handoff` 전체), 6절(`needs_information`). 5절 끝의 "검증기가 확인하는 것" 문단
- `/src/workflow/contracts/v1.py` (Step 1) — `DiagnosisResult`, `EvidenceRef`, `AttachmentRef`
- `/src/workflow/domain/` (Step 2) — 같은 규약(순수 함수, I/O 없음)을 따른다

## 작업

A 의 진단 결과를 자동 완료해도 되는지 판정하는 검증기를 만든다. 제품 원칙: 모델의 "완료했다"가 아니라 이 검증 결과로 A 완료를 결정한다. 이 검증기가 실패하면 A 는 `확인 필요`에 머물고 B 는 시작하지 않는다.

### `src/workflow/domain/evidence_location.py`

```python
@dataclass(frozen=True)
class ObjectPath:  keys: tuple[str, ...]        # "$.data.records" → ("data", "records")
@dataclass(frozen=True)
class LineRange:   start: int; end: int         # "lines:2-3" → 1-based, 양끝 포함

def parse_location(location: str) -> ObjectPath | LineRange      # 문법 오류는 ValueError
def resolve_location(content: bytes, content_type: str, location: str) -> Resolved | None
```

- `application/json` + `ObjectPath`: JSON 을 파싱해 키를 차례로 따라간다. 키가 없으면 `None`. 값이 `null` 이어도 키가 있으면 `Resolved(value=None, found=True)`. 배열 인덱스·와일드카드는 지원하지 않는다.
- `text/plain` + `LineRange`: 줄을 `\n` 으로 나누고 `start..end` 가 범위 안이면 `Resolved(value=[줄...])`. 범위를 넘으면 `None`.
- JSON 에 `LineRange`, 텍스트에 `ObjectPath` 는 `None` (문법은 맞지만 자료형에 맞지 않음).

### `src/workflow/domain/verification.py`

```python
@dataclass(frozen=True)
class LoadedEvidence:
    evidence_id: str; version: str; content_type: str; sha256: str; content: bytes

@dataclass(frozen=True)
class TraceEntry:
    call_id: str; tool: str; input: dict; ok: bool
    returned: tuple[tuple[str, str], ...]     # 실제로 반환한 (evidence_id, version)

@dataclass(frozen=True)
class Check:
    code: str; passed: bool; detail: str

@dataclass(frozen=True)
class Verdict:
    outcome: Literal["passed", "failed", "undecidable"]
    checks: tuple[Check, ...]

def verify_diagnosis(result: DiagnosisResult,
                     attachments: Mapping[tuple[str, str], LoadedEvidence],
                     trace: Sequence[TraceEntry]) -> Verdict
```

`attachments` 의 키는 `(evidence_id, version)` 이고 중앙 워커(Step 8)가 진단 API 에서 내려받아 채운다. `trace` 는 진단 서비스의 조회 이력 산출물(`tool_trace`)을 푼 것이다.

공통 검사 (모든 결과에 적용, 하나라도 실패하면 `failed`):

| code | 확인 |
|---|---|
| `attachments_in_trace` | `result.attachments` 의 모든 `(evidence_id, version)` 이 어떤 `TraceEntry.returned` 에 있다 (읽지 않은 자료 첨부 금지) |
| `refs_in_attachments` | 모든 `findings[].evidence_refs` 와 `diagnosis.change_document`·`report_contract` 의 `(evidence_id, version)` 이 `result.attachments` 에 있다 |
| `attachments_loaded` | `result.attachments` 의 모든 항목이 `attachments` 인자에 있고 `sha256` 이 `LoadedEvidence.sha256` 및 실제 `sha256(content)` 와 같다 |
| `locations_resolve` | 모든 `evidence_refs[].location` 이 해당 첨부 원문에서 `resolve_location` 으로 찾아진다 |
| `outcome_shape` | `ready_for_handoff` 이면 diagnosis 가 있고, `needs_information` 이면 검증을 여기서 끝내고 `outcome="failed"` 가 아니라 `"undecidable"` 로 돌려준다 (정보 부족은 오류가 아니라 보류) |

데모 검사 (`diagnosis.code == "response_path_changed"` 일 때만. 필요한 첨부가 없으면 해당 검사는 `passed=False`, 최종 `undecidable`):

| code | 확인 (첨부 원문에서 직접 읽는다) |
|---|---|
| `runs_same_workflow_and_version` | `run-{baseline_run_id}@1`·`run-{failed_run_id}@1` 의 `workflow_id` 와 `code_version` 이 같고, baseline `status == "succeeded"`, failed `status == "failed"` |
| `failed_run_http_ok_then_transform_failed` | failed 실행 기록의 `http_status == 200` 이고 `stages` 에 `{"stage": "transform", "status": "failed"}` 가 있으며 `render` 는 `skipped`. 실행 기록이 가리키는 `log_ref` 첨부에 `stage=transform` 과 `ERROR` 가 같은 줄에 있다 |
| `paths_differ_as_claimed` | baseline 실행 기록의 `response_ref` 첨부에서 `old_path` 가 list 로 찾아지고 `new_path` 는 없다. failed 의 `response_ref` 첨부에서 `new_path` 가 list 로 찾아지고 `old_path` 는 없다 |
| `change_document_matches` | `change_document` 첨부의 `$.machine` 에서 `workflow_id`, `old_path`, `new_path` 가 실행 기록·diagnosis 와 같다 |
| `change_effective_before_failure` | `$.machine.effective_at` 이 failed 실행의 `started_at` 이하이고 baseline 의 `started_at` 보다 뒤다. 아니면 `passed=False`, detail `"evidence_conflict"` |
| `report_contract_supports_both` | `report_contract` 첨부의 `$.machine.supported_paths` 에 `old_path` 와 `new_path` 가 모두 있고 `workflow_id` 가 같다 |
| `run_ids_consistent` | `result.run_id == diagnosis.failed_run_id`, 실행 기록의 `run_id` 필드가 각각 일치 |

시각 비교는 RFC 3339 문자열을 `datetime.fromisoformat` 으로 파싱해 비교한다 (입력에서 받은 값만 파싱하고 현재 시각은 쓰지 않는다).

### 실행 기록·문서 첨부의 형식 (Step 9 가 이 형식으로 fixture 를 만든다)

실행 기록 `run-daily-0920-0900@1` (`application/json`):

```json
{
  "run_id": "daily-0920-0900", "workflow_id": "daily-report",
  "started_at": "2026-09-20T09:00:00+09:00", "finished_at": "2026-09-20T09:00:01+09:00",
  "status": "failed", "code_version": "report-base", "http_status": 200,
  "stages": [
    {"stage": "fetch", "status": "succeeded"},
    {"stage": "transform", "status": "failed", "error_code": "MISSING_RECORDS_FIELD"},
    {"stage": "render", "status": "skipped"}
  ],
  "response_ref": {"evidence_id": "response-after", "version": "1"},
  "log_ref": {"evidence_id": "log-daily-0920", "version": "1"},
  "report_ref": null
}
```

정상 실행 `run-daily-0919-0900@1` 은 `status: "succeeded"`, 세 stage 모두 `succeeded`, `response_ref: response-before@1`, `log_ref: log-daily-0919@1`, `report_ref: {"evidence_id": "report-0918", "version": "1"}`.

문서 첨부는 `{"markdown": "...", "machine": {...}}`:

- `upstream-response-change@1` machine: `{"workflow_id": "daily-report", "effective_at": "2026-09-20T00:00:00+09:00", "old_path": "$.items", "new_path": "$.data.records", "preserved_fields": ["report_date", "team", "completed", "pending"]}`
- `daily-report-contract@1` machine: `{"workflow_id": "daily-report", "supported_paths": ["$.items", "$.data.records"], "required_row_fields": ["team", "completed", "pending"], "empty_list_policy": {"explicit_empty": "zero_rows", "missing": "error", "ambiguous": "error"}}`
- `daily-report-runbook@1` machine: `{"workflow_id": "daily-report", "schedule": "09:00 Asia/Seoul", "stages": ["fetch", "transform", "render"], "transformer_component": "report_transformer", "reads_path": "$.items", "on_failure": "no_report_for_date"}`

응답·로그는 PRD "데모 데이터와 검증 상세" 절의 JSON·텍스트 그대로.

### 테스트 — `tests/workflow/domain/test_evidence_location.py`, `test_verification.py`

테스트 안에 위 형식의 fixture 를 만드는 헬퍼(`make_demo_attachments()`)를 두고, CONTRACT 5절의 `DiagnosisResult` JSON 을 그대로 파싱해 사용한다 (해시는 fixture 내용으로 다시 계산해 넣는다). 조회 이력은 첨부 8개를 모두 반환한 것으로 만든다.

- 정상 → `passed`, 모든 check 통과.
- `trace` 에서 `upstream-response-change` 를 빼면 → `attachments_in_trace` 실패, `failed`.
- 첨부 해시를 바꾸면 → `failed`.
- `location: "$.data.record"` (오타) → `locations_resolve` 실패.
- `effective_at` 을 `2026-09-21T00:00:00+09:00` 으로 바꾸면 → `change_effective_before_failure` 실패, detail 에 `evidence_conflict`.
- CONTRACT 6절 결과(`needs_information`) → `undecidable`, 공통 검사는 통과.
- 정상 자료인데 `old_path`·`new_path` 를 바꿔 넣은 결과 → `paths_differ_as_claimed` 실패.
- 응답의 건수를 바꿔도 (12 → 13) 검증은 여전히 통과한다 — 검증기는 고정 숫자를 판정에 쓰지 않는다.
- location: JSON 경로 존재/부재/null 값, 텍스트 줄 범위 안/밖, 자료형 불일치, 문법 오류.

### GLOSSARY

`Verdict`(검증기의 판정. `passed`/`failed`/`undecidable`. Task 상태·outcome 과 구분), `Check`(판정 항목 하나), `LoadedEvidence`(중앙이 내려받아 해시를 확인한 첨부 원문) 를 추가한다.

## Acceptance Criteria

```bash
python3 -m pytest tests/workflow/domain -q
python3 -m pytest -q
python3 -m ruff check .
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트:
   - I/O 없음. `open()`, `httpx`, `sqlite3` 호출이 없는가?
   - "숨겨 둔 정답 문장과의 일치 판정"이 없는가? 20·5 같은 고정 수치를 판정에 쓰지 않는가?
   - `response_path_changed` 외의 code 는 검증하지 않고 `undecidable` 로 두는가?
3. `phases/0-mvp/index.json` 의 step 3 을 업데이트한다 (summary 에 check code 목록과 fixture 형식이 정의된 위치).

## 금지사항

- 자연어 `summary`·`claim` 의 내용을 판정에 쓰지 마라. 이유: ARCHITECTURE "자연어 인용이 존재한다는 것만으로 의미가 맞다고 판정하지 않는다". 판정은 구조화 필드와 첨부 원문 값으로만 한다.
- 검증 실패를 `undecidable` 로 완화하지 마라. 이유: 읽지 않은 근거 인용·해시 불일치는 보류가 아니라 차단이다.
- 첨부가 없을 때 fixture 를 직접 읽어오지 마라. 이유: 검증기는 인자로 받은 첨부만 본다. 부족하면 `undecidable`.
- 기존 테스트를 깨뜨리지 마라.
