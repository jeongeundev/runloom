# Step 1: contracts

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/docs/ARCHITECTURE.md` — "계약 v1의 공통 규칙", "실행 요청과 접수", "실행 이벤트", "진단 결과와 근거", "코드 수정 결과" 절. 필드·타입·조건의 원본이다
- `/docs/CONTRACT.md` — 모든 요청·이벤트·결과·오류의 완전한 예시. 이 파일의 JSON 이 계약 테스트의 fixture 다
- `/docs/GLOSSARY.md`
- `/docs/adr/` (하위 파일 전부)
- `/src/workflow/contracts/__init__.py` (Step 0 에서 생성, 비어 있음)
- `/tests/workflow/contracts/__init__.py`

## 작업

계약 v1 의 Pydantic v2 모델을 `src/workflow/contracts/v1.py` 하나에 만든다. 이 모델은 중앙 API·진단 API·연결 프로그램이 모두 공유하는 유일한 계약이다. 도메인 규칙·DB·HTTP 는 넣지 않는다.

### 공통 규칙 (ARCHITECTURE "계약 v1의 공통 규칙" 그대로)

- 모든 모델은 `model_config = ConfigDict(extra="forbid", strict=True)`. 알 수 없는 필드는 거부하고 `"1"` → `1` 같은 자동 변환을 허용하지 않는다.
- 시각 필드(`occurred_at`, `created_at`)는 `str` 로 받고 공용 validator 로 "시간대가 있는 RFC 3339" 를 강제한다. `datetime` 타입을 쓰지 마라 — FastAPI 는 JSON 을 dict 로 푼 뒤 python 모드로 검증하므로 strict 모드에서 문자열 → datetime 이 거부된다.
- ID 는 비어 있지 않은 `str`. 경로로 해석하지 않는다.
- `sha256` 은 `^[0-9a-f]{64}$`, `base_commit`·`result_commit` 은 `^[0-9a-f]{40}$`.
- `contract_version: Literal[1]`.
- `location` 문법: `^\$(\.[A-Za-z_][A-Za-z0-9_]*)+$` 또는 `^lines:[1-9][0-9]*-[1-9][0-9]*$` (끝 줄 ≥ 시작 줄). 와일드카드·필터 없음. 해석(값 찾기)은 Step 3 이며 여기서는 문법만 검증한다.

### 모델 목록

CONTRACT.md 절 번호와 대응한다. 이름은 코드 식별자로 그대로 쓴다.

| 모델 | 필드 요지 | 추가 검증 |
|---|---|---|
| `ErrorBody` | `code`, `message`, `field: str \| None`, `details: dict \| None` | |
| `DiagnosisTarget` | `run_id` | |
| `CodeChangeTarget` | `local_registration_id`, `base_commit`, `verification_profile_id` | |
| `ExecutionRequest` (1·2·8절) | `contract_version`, `execution_id`, `task_id`, `kind: Literal["diagnosis","code_change"]`, `agent_id`, `task_revision: int ≥ 1`, `request`(비어 있지 않음), `input_artifact_ids: list[str]`(중복 없음), `target: DiagnosisTarget \| CodeChangeTarget` | `kind` 와 `target` 종류 일치. `code_change` 는 `input_artifact_ids` 가 비어 있으면 안 됨 |
| `RunStatus` (1절 응답) | `execution_id`, `status: Literal["accepted","running","result_ready","failed"]`, `last_event_seq: int ≥ 0`, `result_artifact_id: str \| None`, `error: ErrorBody \| None`, `events: list[ExecutionEvent] = []` | |
| `ClaimRequest` (2절) | `contract_version`, `connector_id` | |
| `HeartbeatRequest` | `contract_version`, `connector_id`, `current_execution_id: str \| None` | |
| `AttachmentRef` | `evidence_id`, `version`, `content_type`, `artifact_id`, `sha256` | |
| `HandoffBundle` (2절 manifest) | `contract_version`, `source_execution_id`, `diagnosis_result_artifact_id`, `attachments: list[AttachmentRef]` | `(evidence_id, version)` 중복 없음 |
| `ExecutionEvent` (3절) | `contract_version`, `execution_id`, `seq: int ≥ 1`, `occurred_at`, `type: Literal["accepted","started","progress","result_ready","failed"]`, `data` | `data` 는 type 별로 정확히: accepted `{}` / started `{runtime_ref: str}` / progress `{message: str}` / result_ready `{result_artifact_id: str}` / failed `{code: str, message: str, process_stopped: bool}`. 다른 키가 있으면 거부. `type` 을 discriminator 로 하는 Union 이나 model_validator 중 하나로 구현 |
| `EventAck` | `execution_id`, `last_event_seq`, `status` | |
| `ArtifactMeta` (4절) | `contract_version`, `kind: Literal[ARTIFACT_KINDS]`, `name`, `content_type`, `sha256`, `size: int ≥ 0` | |
| `ArtifactCreated` | `artifact_id`, `kind`, `sha256`, `size` | |
| `EvidenceVersion` | `evidence_id`, `version` | |
| `EvidenceRef` | `evidence_id`, `version`, `location` | location 문법 |
| `Finding` | `claim`, `evidence_refs: list[EvidenceRef]`(비어 있지 않음) | |
| `Diagnosis` | `code: Literal["response_path_changed"]`, `baseline_run_id`, `failed_run_id`, `old_path`, `new_path`, `change_document: EvidenceVersion`, `report_contract: EvidenceVersion` | `old_path != new_path`, 둘 다 `$.` 로 시작 |
| `RepairRequest` | `target_component`, `change`, `preserve`, `checks: list[str]`(비어 있지 않음) | |
| `MissingInformation` | `code: Literal["evidence_unavailable","evidence_conflict","unsupported_diagnosis"]`, `description`, `evidence_id: str \| None` | |
| `Provenance` | `model_id`, `prompt_version`, `tool_contract_version`, `tool_trace_artifact_id` | |
| `DiagnosisResult` (5·6절) | `contract_version`, `execution_id`, `task_id`, `run_id`, `outcome: Literal["ready_for_handoff","needs_information"]`, `summary`, `findings`, `diagnosis: Diagnosis \| None`, `repair_request: RepairRequest \| None`, `missing_information: list[MissingInformation]`, `attachments: list[AttachmentRef]`, `provenance` | `ready_for_handoff` ⇒ diagnosis·repair_request 가 None 이 아니고 findings 비어 있지 않고 missing_information 빈 배열. `needs_information` ⇒ diagnosis·repair_request 가 None 이고 missing_information 1개 이상. `diagnosis.failed_run_id == run_id` |
| `Verification` (7절) | `profile_id`, `result_commit`, `exit_code: int`, `log_artifact_id` | |
| `CodeChangeResult` (7절) | `contract_version`, `execution_id`, `task_id`, `outcome: Literal["ready_for_review","needs_information"]`, `summary`, `base_commit`, `result_commit: str \| None`, `artifact_ids: list[str]`, `verification: Verification \| None` | `ready_for_review` ⇒ result_commit·verification 이 None 아님, `verification.result_commit == result_commit` |
| `ReviewComment` (8절) | `contract_version`, `task_id`, `reviewed_execution_id`, `decision: Literal["request_changes"]`, `comment`, `created_at` | |
| `Capability` | `code: Literal["operations.diagnose","code.modify"]`, `scope: dict[str, str]` | `operations.diagnose` 의 scope 키는 정확히 `{"workflow_id"}`, `code.modify` 는 `{"repository_id"}` |
| `SelectionRecord` (9절) | `task_id`, `mode: Literal["auto","manual"]`, `required_capability: Capability`, `candidate_count: int ≥ 0`, `selected_agent_id: str \| None`, `matched: Capability \| None`, `status: Literal["selected","needs_selection"]`, `reason` | `selected` ⇒ selected_agent_id·matched 있음, candidate_count == 1 |

상수: `ARTIFACT_KINDS: tuple[str, ...]` = CONTRACT 4절의 13종 그대로. `CONTRACT_VERSION = 1`.

헬퍼: `parse_rfc3339_aware(value: str) -> str` (검증만 하고 원문 반환. 시간대 없으면 `ValueError`).

### 테스트 — `tests/workflow/contracts/test_v1.py`

CONTRACT.md 를 fixture 로 쓴다. 테스트 파일에서 `docs/CONTRACT.md` 를 읽어 ```json 펜스 블록 22개를 순서대로 추출하고, 각 블록을 키 서명으로 모델에 대응시켜 검증한다:

- `kind` 와 `target` 키 → `ExecutionRequest`
- `status` 와 `last_event_seq` 키 → `RunStatus`
- `connector_id` 만 → `ClaimRequest`
- `source_execution_id` → `HandoffBundle`
- `seq` 와 `type` → `ExecutionEvent`
- `kind` 와 `sha256` 와 `size` 와 `contract_version` → `ArtifactMeta`
- `artifact_id` 와 `sha256` 와 `contract_version` 없음 → `ArtifactCreated`
- `outcome` 와 `findings` → `DiagnosisResult`
- `outcome` 와 `base_commit` → `CodeChangeResult`
- `decision` → `ReviewComment`
- `required_capability` → `SelectionRecord`
- `code` 와 `message` → `ErrorBody`

모든 블록이 정확히 하나의 모델에 대응돼야 하고(대응 안 되는 블록이 있으면 테스트 실패), 각 블록은 해당 모델로 검증에 성공해야 한다. 표 안의 인라인 JSON(`| ... | 409 | \`{ "code": ... }\` |`) 8개도 추출해 `ErrorBody` 로 검증한다.

거부 사례 테스트 (각각 `ValidationError`):

- 알 수 없는 필드 추가 (`extra`)
- `contract_version: 2`
- `task_revision: "1"` (문자열)
- `occurred_at: "2026-09-20T01:00:00"` (시간대 없음)
- `type: "started"` 인데 `data: {}`
- `ready_for_handoff` 인데 `diagnosis: null`
- `needs_information` 인데 `missing_information: []`
- `location: "$.items[*]"`, `location: "lines:3-2"`
- `Capability(code="operations.diagnose", scope={"repository_id": "x"})`
- `code_change` 요청의 `input_artifact_ids: []`
- `kind: "diagnosis"` 에 `CodeChangeTarget`

`model_dump(mode="json")` 후 다시 검증하면 같은 값이 되는 왕복 테스트를 `DiagnosisResult`·`ExecutionRequest` 에 둔다.

### GLOSSARY

새 용어 없음. 모델 이름은 GLOSSARY 의 `Execution`, `ExecutionEvent`, `Artifact`, `capability`, `outcome` 과 대응한다.

## Acceptance Criteria

```bash
python3 -m pytest tests/workflow/contracts -q   # 계약 테스트 통과
python3 -m pytest -q                            # 전체 통과
python3 -m ruff check .
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - `src/workflow/contracts/` 가 `fastapi`, `sqlite3`, `httpx` 를 import 하지 않는가?
   - 모델 이름·필드 이름이 CONTRACT.md 예시의 키와 글자 단위로 같은가?
   - GLOSSARY 금지 표현(`Run` 을 Execution 대신, `status` 를 outcome 대신)을 쓰지 않았는가?
3. 결과에 따라 `phases/0-mvp/index.json` 의 step 1 을 업데이트한다 (summary 에 모델 목록 파일 경로와 헬퍼 이름).

## 금지사항

- `datetime` 필드를 쓰지 마라. 이유: strict 모드 + FastAPI python 모드 검증에서 문자열이 거부된다.
- 기본값으로 필수 필드를 채우지 마라 (`events` 만 예외). 이유: 계약은 알 수 없는 필드뿐 아니라 누락도 거부한다.
- 도메인 판단(자동 선택, 상태 전이)을 모델에 넣지 마라. 이유: Step 2 의 순수 함수가 담당한다.
- CONTRACT.md 의 예시를 고쳐서 테스트를 통과시키지 마라. 이유: 문서가 fixture 다. 문서에 실제 모순이 있으면 `error_message` 에 적고 멈춘다.
- 기존 테스트를 깨뜨리지 마라.
