# Step 1: contracts-kinds

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/AGENTS.md`
- `/docs/adr/0009-registered-kinds-and-succession-rules.md` (step 0 이 만든 정본), `/docs/adr/0004-central-service-rule-based-no-llm.md`
- `/docs/GLOSSARY.md` (`KindSpec`·`SuccessorRule`·`GenericResult`·`LocalTarget`·`InputRef`·`source_result_artifact_id` 행)
- `/docs/CONTRACT.md` 2절·4절·11절, `/docs/ARCHITECTURE.md` "업무 종류와 후속 규칙", "계약 v1의 공통 규칙", "실행 요청과 접수"
- `/src/workflow/contracts/v1.py` 전부 — `_Contract`(`extra="forbid", strict=True`), `ARTIFACT_KINDS`, `DiagnosisTarget`·`CodeChangeTarget`·`ExecutionRequest`(`_check_kind_target`), `HandoffBundle`, `Capability`(`_CAPABILITY_SCOPE_KEYS`), `DiagnosisResult`·`CodeChangeResult`
- `/tests/workflow/contracts/test_v1.py`
- 필드명 변경이 닿는 곳(기계적 치환 대상): `grep -rn "diagnosis_result_artifact_id\|_CAPABILITY_SCOPE_KEYS" src tests scripts --include=*.py`

이전 step 에서 만들어진 문서를 꼼꼼히 읽고, 설계 의도를 이해한 뒤 작업하라.

## 이 phase 의 개념 (모든 step 공통 — 이 절은 step 파일마다 같다)

이 phase 는 "업무 종류 2개(`diagnosis`·`code_change`)와 인계 쌍 1개(진단 → 코드 수정)가 코드에 박힌 상태"를
"종류·후속 규칙을 워크스페이스(세션)가 **등록**하는 상태"로 바꾼다. 흐름을 그리지 않는다 — 규칙 표를 반복 적용한
결과가 흐름이다 (ADR-0009, step 0 이 쓴다). 팀 사용을 전제하므로 종류·규칙은 코드가 아니라 DB + 화면이다.

- `KindSpec` (계약, `contracts/v1.py`): 업무 종류의 **봉투**. 필드:
  `kind`(식별자, `^[a-z][a-z0-9_]{1,39}$`) · `label`(화면 표시) · `capability_code`(에이전트 능력 코드, `^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)?$`) ·
  `scope_key`(능력 scope 의 키 하나, 식별자) · `input_kinds`(시작할 때 받아야 하는 산출물 kind 목록, `ARTIFACT_KINDS` 부분집합, 빈 목록 허용) ·
  `output_kind`(`diagnosis_result` | `code_change_result` | `generic_result`) · `outcomes`(허용 outcome 식별자 목록, 1개 이상, 중복 없음) ·
  `instructions`(에이전트 지시문 — 내장은 빈 문자열) · `builtin`(내장 여부).
  종류의 **내용**(검토 의견이 어떻게 생겼는지 등)은 정의하지 않는다. 중앙은 봉투만 본다.
- 내장 종류 2개 `BUILTIN_KINDS` (계약 상수):
  `diagnosis` = 진단 · `operations.diagnose` · `workflow_id` · input 없음 · `diagnosis_result` · [`ready_for_handoff`, `needs_information`];
  `code_change` = 코드 수정 · `code.modify` · `repository_id` · input [`diagnosis_result`, `evidence`] · `code_change_result` · [`ready_for_review`, `needs_information`].
  내장은 검증기·실행 흐름이 코드에 있고 삭제할 수 없다. 사용자 정의 종류는 `output_kind` 가 항상 `generic_result` 이고 완료는 항상 사람 검토다.
- `SuccessorRule` (계약): `from_kind` · `on_outcomes`(선행 결과의 outcome 이 이 중 하나면 잇는다, `from_kind.outcomes` 부분집합) · `to_kind` ·
  `handoff_kinds`(선행 실행의 산출물 중 넘길 kind 목록; `to_kind.input_kinds` 를 모두 포함해야 한다).
  내장 규칙 1개 `BUILTIN_RULES`: `diagnosis` --[`ready_for_handoff`]--> `code_change`, handoff [`diagnosis_result`, `evidence`].
- `GenericResult` (계약): 내장이 아닌 종류의 결과 봉투 — `contract_version` · `execution_id` · `task_id` · `kind` · `outcome` · `summary` · `artifact_ids`.
  산출물 kind 는 `generic_result` (`ARTIFACT_KINDS` 에 추가). 중앙은 `outcome ∈ KindSpec.outcomes` 만 판정하고 완료는 사람이 한다.
- `LocalTarget` (계약): 내장이 아닌 종류를 로컬 도구(Codex·Claude)가 수행할 때의 target — `local_registration_id` 하나.
  worktree·커밋·검증 프로필 없음 — **읽기 전용 실행**이며 작업 위치는 인계 디렉터리다.
- `InputRef` (계약): 인계 묶음의 입력 항목 — `kind` · `artifact_id` · `sha256` · `content_type`.
- `HandoffBundle` (계약, 일반화): `source_execution_id` · `source_kind` · `source_result_artifact_id`(이전 이름 `diagnosis_result_artifact_id`) ·
  `inputs: list[InputRef]`(규칙 `handoff_kinds` 로 모은 산출물) · `attachments`(근거 원문 `evidence_id@version` — 기존과 같음, 진단 결과에서만 채워진다).
- `ExecutionRequest.kind` 는 식별자 문자열이고 `kind_spec: KindSpec | None` 을 갖는다(서버가 채운다). target 은 `diagnosis` → `DiagnosisTarget`,
  `code_change` → `CodeChangeTarget`, 그 외 → `LocalTarget`(이때 `kind_spec` 필수, `kind_spec.kind == kind`, `builtin=False`).
- 종류·규칙은 **워크스페이스별**: `kinds(session_id, kind)` · `succession_rules(session_id, from_kind, to_kind)`. 세션이 생길 때(`repo.create_session`)
  내장 종류 2개 + 내장 규칙 1개를 seed 한다. `Capability.code` 는 패턴만 계약이 검사하고, "코드가 어느 종류의 `capability_code` 인가 · scope 키가 그 종류의
  `scope_key` 인가"는 서버가 등록부로 검사한다(422).
- **후속 착수 조건이 바뀐다**: 이전엔 선행 Task 가 `완료` 여야 했다. 이제는 선행 실행이 `result_ready` 이고 판정(`task_verdicts`)이 `passed` 이며
  결과 봉투의 `outcome` 이 규칙 `on_outcomes` 에 있으면 착수한다. 사람 승인은 선행 Task 를 마감할 뿐 후속 착수를 막지 않는다
  (그래서 "에이전트 검토가 사람 승인보다 먼저" 가 가능하다). 사람이 선행을 종료(close)하면 후속을 새로 착수하지 않는다(이미 시작한 것은 계속).
  규칙에 없는 결과·outcome 은 착수하지 않고 이유를 남긴다(확인 필요). 중앙은 LLM 을 부르지 않는다(ADR-0004).
- 증명 기준(step 8): e2e 가 화면으로 종류 `review`(diff·code_change_result 를 받아 `approved`/`changes_requested`/`needs_information` 을 냄)와
  규칙 `code_change --[ready_for_review]--> review` 를 등록하면, 진단 → 수정 → 검토가 `composition.py`·`worker.py` 를 고치지 않고 자동 착수한다.

## 시작 상태 (step 0 의 결과)

step 0 이 `docs/CONTRACT.md` 에 11절(새 모델 JSON 예시 7개)을 넣고 2절 `handoff_bundle` 예시를 `source_kind`·`source_result_artifact_id`·`inputs` 로 바꿨다. `tests/workflow/contracts/test_v1.py` 는 CONTRACT 의 ```json 블록을 **fixture 로 읽어** 블록 수와 모델 대응을 검사하므로, 이 step 을 시작하는 시점에 `test_v1.py` 14건이 red 다(그 외 전부 green). 이건 예상된 상태이며 **이 step 이 새 모델을 추가하고 그 fixture 테스트(블록 수·블록→모델 대응표)를 갱신해 전부 green 으로 만든다.** CONTRACT 의 JSON 을 고쳐서 맞추지 말고 모델을 CONTRACT 에 맞춰라(문서가 정본). 11절 예시가 모델 검증을 통과하지 못하는 부분이 있으면 그때만 CONTRACT 예시를 최소로 고치고 summary 에 적어라.

## 작업

계약 층(`src/workflow/contracts/v1.py`)만 바꾼다. 다른 층은 **필드명 변경에 따른 기계적 치환**만 허용한다(논리 변경 금지 — 그건 step 2~7).

### `src/workflow/contracts/v1.py`

```python
KIND_PATTERN = r"^[a-z][a-z0-9_]{1,39}$"
CAPABILITY_CODE_PATTERN = r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)?$"
IDENTIFIER_PATTERN = r"^[a-z][a-z0-9_]{0,39}$"        # scope_key · outcome
KindId = Annotated[str, Field(pattern=KIND_PATTERN)]
Outcome = Annotated[str, Field(pattern=IDENTIFIER_PATTERN)]

ARTIFACT_KINDS = (..., "generic_result")                # 16종. 순서는 기존 + 끝에 추가

class KindSpec(_Contract):
    kind: KindId
    label: NonEmptyStr
    capability_code: Annotated[str, Field(pattern=CAPABILITY_CODE_PATTERN)]
    scope_key: Annotated[str, Field(pattern=IDENTIFIER_PATTERN)]
    input_kinds: list[ArtifactKind]                     # 중복 없음
    output_kind: Literal["diagnosis_result", "code_change_result", "generic_result"]
    outcomes: list[Outcome]                             # 1개 이상, 중복 없음
    instructions: str
    builtin: bool
    # validator: builtin=False 면 output_kind == "generic_result"; builtin=True 면 kind 가 BUILTIN_KINDS 의 이름 중 하나

BUILTIN_KINDS: tuple[KindSpec, ...]                     # diagnosis, code_change — 개념 절의 값 그대로
BUILTIN_KIND_NAMES: tuple[str, ...] = ("diagnosis", "code_change")

class SuccessorRule(_Contract):
    from_kind: KindId
    on_outcomes: list[Outcome]                          # 1개 이상, 중복 없음
    to_kind: KindId                                     # from_kind 와 달라야 한다
    handoff_kinds: list[ArtifactKind]                   # 중복 없음, "handoff_bundle" 은 넣을 수 없다(묶음 자체다)

BUILTIN_RULES: tuple[SuccessorRule, ...]                # 개념 절의 내장 규칙 1개

class LocalTarget(_Contract):
    local_registration_id: NonEmptyStr

class ExecutionRequest(_Contract):
    ...
    kind: KindId                                        # Literal 둘 → 식별자
    target: DiagnosisTarget | CodeChangeTarget | LocalTarget
    kind_spec: KindSpec | None = None
    # _check_kind_target: diagnosis → DiagnosisTarget, code_change → CodeChangeTarget(입력 필수 유지),
    # 그 외 → LocalTarget 이고 kind_spec 이 있어야 하며 kind_spec.kind == kind, kind_spec.builtin is False.
    # kind_spec 이 있으면 어느 종류든 kind_spec.kind == kind.

class InputRef(_Contract):
    kind: ArtifactKind
    artifact_id: NonEmptyStr
    sha256: Sha256
    content_type: NonEmptyStr

class HandoffBundle(_Contract):
    contract_version: ContractVersion
    source_execution_id: NonEmptyStr
    source_kind: KindId
    source_result_artifact_id: NonEmptyStr              # 이전 diagnosis_result_artifact_id
    inputs: list[InputRef]                              # artifact_id 중복 없음
    attachments: list[AttachmentRef]                    # 기존 검증(evidence_id@version 중복 없음) 유지

class GenericResult(_Contract):
    contract_version: ContractVersion
    execution_id: NonEmptyStr
    task_id: NonEmptyStr
    kind: KindId
    outcome: Outcome
    summary: str
    artifact_ids: list[NonEmptyStr]                     # 중복 없음

class Capability(_Contract):
    code: Annotated[str, Field(pattern=CAPABILITY_CODE_PATTERN)]
    scope: dict[str, str]                               # 키 정확히 1개, 키는 IDENTIFIER_PATTERN, 값은 비어 있지 않음
    # _CAPABILITY_SCOPE_KEYS 는 삭제한다. "코드 ↔ scope 키" 대응은 KindSpec 이 말하고 서버가 검사한다 (step 7).
```

- `SelectionRecord`·`DiagnosisResult`·`CodeChangeResult`·`ReviewComment`·이벤트 모델은 바꾸지 않는다.
- Pydantic 의 union 판별: `_Contract` 가 `extra="forbid"` 이므로 `LocalTarget` 데이터는 `CodeChangeTarget` 으로 읽히지 않고 그 반대도 마찬가지다. 테스트로 세 target 의 왕복(`model_validate` → `model_dump` → 같은 클래스)을 확인하라.
- `ExecutionRequest.model_validate({... "target": {"local_registration_id": "x"}})` 가 `kind="review"`, `kind_spec` 있음일 때 통과하고, `kind_spec` 없으면 실패해야 한다.

### 기계적 치환 (논리 변경 없이)

`diagnosis_result_artifact_id` → `source_result_artifact_id` 로 바꾸고, `HandoffBundle(...)` 생성 지점에 `source_kind="diagnosis"`, `inputs=[]` 를 넣어 기존 테스트가 그대로 통과하게 한다. 대상(사전 grep 결과): `src/workflow/server/worker.py`, `src/workflow/connector/runner.py`, `src/workflow/adapters/repo.py`(참조만), `scripts/make_handoff_dir.py`·`scripts/test_make_handoff_dir.py`, `tests/workflow/connector/conftest.py`, `tests/workflow/server/test_worker.py`·`test_machine_api.py`·`test_web.py`, `tests/workflow/adapters/test_repo.py`. `worker.assemble_handoff` 의 **일반화는 step 4** 다 — 여기서는 필드명만.

`SCOPE_KEYS`/`_CAPABILITY_SCOPE_KEYS` 를 쓰던 곳(`server/web.py` 의 `SCOPE_KEYS` 는 자체 사전이라 그대로 두고, 계약의 `_CAPABILITY_SCOPE_KEYS` 를 import 하던 곳만) 은 임시로 `{"operations.diagnose": "workflow_id", "code.modify": "repository_id"}` 지역 사전으로 대체해 통과시킨다. step 7 이 등록부로 바꾼다.

### 테스트 (먼저 작성) — `tests/workflow/contracts/test_v1.py`

- `KindSpec`: 패턴 위반(대문자·공백·40자 초과) 거부, `outcomes` 빈 목록·중복 거부, `builtin=False` + `output_kind != generic_result` 거부, `builtin=True` + 모르는 이름 거부, `BUILTIN_KINDS` 2개의 값이 개념 절과 같음(각 필드 명시 비교).
- `SuccessorRule`: `from_kind == to_kind` 거부, `handoff_kinds` 에 `handoff_bundle` 거부, `on_outcomes` 빈 목록 거부, `BUILTIN_RULES` 값 비교.
- `ExecutionRequest`: 세 target 판별 왕복; `kind="review"` + `LocalTarget` + `kind_spec` 통과; `kind_spec` 없음 실패; `kind_spec.kind != kind` 실패; `kind_spec.builtin=True` + `LocalTarget` 실패; `kind="diagnosis"` + `LocalTarget` 실패(기존 규칙 유지).
- `HandoffBundle`: `inputs` artifact_id 중복 거부, 기존 attachments 중복 거부 유지, `source_kind` 패턴.
- `GenericResult`: 왕복·`artifact_ids` 중복 거부·`outcome` 패턴.
- `Capability`: scope 키 0개·2개 거부, 값 빈 문자열 거부, 코드 패턴(`review`, `code.modify` 통과 / `Code.Modify`, `a.b.c` 거부).
- `ARTIFACT_KINDS` 에 `generic_result` 포함, 길이 16.

## Acceptance Criteria

```bash
python3 -m pytest tests/workflow/contracts -q
python3 -m pytest -q                    # 기계적 치환 뒤 전체 통과
python3 -m ruff check .
grep -rn "diagnosis_result_artifact_id" src tests scripts --include=*.py | wc -l    # 0
grep -n "_CAPABILITY_SCOPE_KEYS" src/workflow/contracts/v1.py | wc -l               # 0
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - `contracts/` 가 `domain/`·`adapters/`·`server/`·`connector/` 를 import 하지 않는가? (계약은 모두가 공유하는 잎이다)
   - ADR-0009·CONTRACT 11절의 필드명과 정확히 같은가?
   - GLOSSARY 용어를 그대로 썼는가? (`kind`, `outcome`, `inputs`, `attachments`)
   - 기계적 치환 외에 다른 층의 논리를 바꾸지 않았는가? (`git diff --stat` 으로 확인)
3. 결과에 따라 `phases/6-typed-handoff/index.json` 의 해당 step 을 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"` (새 모델·상수 이름과 치환한 파일 목록)
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- `domain/`·`adapters/`·`server/`·`connector/`·`scripted/` 의 논리를 바꾸지 마라(필드명 치환·지역 사전 대체만). 이유: 한 step 은 한 층. 일반화는 step 2~7.
- `ExecutionRequest.kind` 를 `str` 로만 두지 마라 — `KIND_PATTERN` 을 강제하라. 이유: 식별자가 경로·파일명·SQL 로 흘러간다.
- `contract_version` 을 2 로 올리지 마라. 이유: ADR-0009 트레이드오프 — 공개 배포 전이며 DB 는 스키마 버전으로 재생성한다.
- `DiagnosisResult`·`CodeChangeResult` 를 `GenericResult` 로 합치지 마라. 이유: 내장 검증기가 그 필드를 읽는다.
- 기존 테스트를 깨뜨리지 마라(기계적 치환에 맞춰 고치는 것만).
