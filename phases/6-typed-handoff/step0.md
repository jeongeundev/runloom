# Step 0: adr-kind-rules

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/AGENTS.md`
- `/docs/ARCHITECTURE.md` 전부 — 특히 "등록·선택·권한", "최소 데이터 모델과 영속성", "진단 결과와 근거", "코드 수정 결과", "DB 제약과 실행 잠금", "상태·재접속·완료"
- `/docs/adr/` 전부 — 특히 `0000-principles.md`, `0004-central-service-rule-based-no-llm.md`, `0008-public-demo-scripted-agents.md` (형식과 어조를 따른다)
- `/docs/GLOSSARY.md` 전부 (표 형식·"금지 표현" 열)
- `/docs/CONTRACT.md` 전부 (절 번호·JSON 예시 형식)
- `/docs/PRD.md` 2절(기본값)·3절(상태)·4절(검토)
- `/src/workflow/domain/composition.py` (`_HANDOFF_PAIRS`, `compose`), `/src/workflow/domain/defaults.py`, `/src/workflow/server/worker.py` 의 `assemble_handoff`·`_spawn_successors`, `/src/workflow/contracts/v1.py` 의 `HandoffBundle`·`ExecutionRequest`·`Capability` — 지금 무엇이 박혀 있는지 확인용. **이 step 은 코드를 고치지 않는다.**

이전 step 은 없다. 이 step 이 이 phase 의 첫 문서를 만들고, 뒤의 모든 step 이 이 문서를 읽는다.

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

## 작업

문서만 쓴다. 코드·테스트를 만들지 않는다. 위 "이 phase 의 개념" 절이 정본이며, 아래 문서는 그것을 각 문서의 형식으로 옮긴 것이다. 개념 절과 다르게 쓰지 마라.

### 1. `docs/adr/0009-registered-kinds-and-succession-rules.md` (신규)

제목: `ADR-0009: 업무 종류와 후속 규칙은 워크스페이스가 등록한다 — 흐름을 그리지 않는다`. 결정일 2026-09-21, 사용자 확정. 기존 ADR 형식(**결정** / **이유** / **트레이드오프**)을 따르고 아래를 담는다:

- **결정**: (1) 업무 종류는 `KindSpec` 봉투(위 필드 목록)로 정의하고 워크스페이스별 DB 에 등록한다. 내장 2종(`diagnosis`·`code_change`)은 검증기·실행 흐름이 코드에 있고, 사용자 정의 종류는 결과가 `generic_result` 이며 완료는 사람 검토다. (2) 종류 사이 연결은 `SuccessorRule`(`from_kind`·`on_outcomes`·`to_kind`·`handoff_kinds`) 행으로 등록한다. 그래프·DAG·pipeline 개념을 도입하지 않는다 — 순서는 Task 의 `predecessor_task_id` 뿐이고, 규칙은 "이 결과 다음에 무엇을 넘겨 무엇을 시작하는가"만 말한다. (3) 후속 착수 조건은 선행 Task 의 `완료` 가 아니라 **선행 결과 + 판정 통과 + outcome 일치**다. 사람 승인은 선행을 마감할 뿐 후속 착수를 막지 않는다. 사람이 선행을 종료하면 후속을 새로 착수하지 않는다. (4) 규칙에 없는 결과는 착수하지 않고 확인 필요로 남긴다. 중앙은 여전히 LLM 을 부르지 않는다(ADR-0004 유지). (5) 종류의 내용은 정의하지 않는다 — 봉투만 고정하고 내용은 에이전트가 쓴다(받는 쪽이 LLM 이라 읽으면 된다).
- **이유**: 원래 문제는 착수 대기(A 가 끝나 B 를 시작할 수 있어도 사람이 전달·실행할 때까지 후속이 밀림)다. 흐름은 진행돼 봐야 아는 경우가 많아 미리 그리는 방식(n8n 식 노드·엣지, LLM 이 흐름 JSON 생성)은 예측이 틀리고 분기가 폭발한다. 입출력 봉투를 정형화하면 매 단계 결정이 "방금 나온 출력이 이 형태면 이 형태를 받는 다음 작업을 만든다" 하나로 줄고, 흐름은 그 반복의 결과가 된다. 규칙이라 같은 출력엔 항상 같은 다음 작업이고 이유가 남고 형태가 안 맞으면 멈춘다. 병목은 개인보다 팀에서 크므로 종류·규칙을 코드가 아니라 등록 데이터로 둔다. 착수 조건을 결과로 바꾸는 이유: 코드 수정의 `완료` 는 사람 승인이라 "에이전트 검토가 사람 승인보다 먼저" 장면이 성립하지 않았다.
- **트레이드오프**: 사용자 정의 종류는 자동 완료 검증기가 없어 항상 사람 검토다. API 에이전트(진단 API)는 이번에도 `diagnosis` 만 받는다(범용 API 계약은 다음 ADR). 완료 시 새 업무를 **생성**하는 규칙(대상·범위 파생 필요)은 이 phase 밖이며 미리 등록된 업무 사이를 잇는 것만 한다. 사람이 선행을 종료해도 이미 시작한 후속은 계속된다. `HandoffBundle.diagnosis_result_artifact_id` 가 `source_result_artifact_id` 로 바뀌므로 계약 v1 안에서 필드명이 바뀐다(공개 배포 전이라 버전을 올리지 않는다 — DB 는 스키마 버전으로 재생성).
- 참고: Astera(parsingk/Astera)·n8n 과의 차이 한 줄 — "A 끝나면 B 자동"만으로는 차이가 없고, "안 그려도 흐른다 + 넘어갈 때 검증한다"가 차이다. n8n 은 업무가 들어오는 입구·나가는 출구로만 쓴다(다음 phase).

### 2. `docs/GLOSSARY.md`

"용어" 표에 아래 행을 추가한다(코드 식별자 그대로, 금지 표현 포함). 갱신일을 2026-09-21 로 유지하고 표 위 문장은 손대지 않는다.

| 용어 | 정의 요지 | 금지 표현 |
|---|---|---|
| `KindSpec` / `kind` | 업무 종류의 봉투(위 필드). 워크스페이스별 등록. `Task.kind`·`Execution.kind` 의 값 | `TaskType`, `Template`, `NodeType`, `Category` |
| `BUILTIN_KINDS` / 내장 종류 | `diagnosis`·`code_change`. 검증기·실행 흐름이 코드에 있고 삭제 불가 | `system kind`, `default kind` |
| `SuccessorRule` / 후속 규칙 | `from_kind`·`on_outcomes`·`to_kind`·`handoff_kinds`. 워크스페이스별 등록. 착수 조건은 선행 결과 + 판정 통과 + outcome 일치 | `edge`, `transition`, `trigger`, `pipeline step`, `workflow`(체인 화면 라벨과 혼동) |
| `BUILTIN_RULES` / 내장 규칙 | `diagnosis` --[`ready_for_handoff`]--> `code_change`, handoff [`diagnosis_result`, `evidence`] | — |
| `GenericResult` / `generic_result` | 사용자 정의 종류의 결과 봉투와 그 산출물 kind. 중앙은 `outcome ∈ outcomes` 만 판정 | `Result`(내장 결과와 혼동), `Output`, `Answer` |
| `LocalTarget` | 사용자 정의 종류를 로컬 도구가 읽기 전용으로 수행할 때의 target(`local_registration_id`) | `GenericTarget`, `workspace` |
| `InputRef` / `inputs` | 인계 묶음의 입력 항목(`kind`·`artifact_id`·`sha256`·`content_type`). `attachments`(근거 원문)와 구분 | `payload`, `files` |
| `source_result_artifact_id` | `HandoffBundle` 의 선행 결과 산출물 ID. 이전 이름 `diagnosis_result_artifact_id` 는 쓰지 않는다 | `diagnosis_result_artifact_id` |
| `outcome` (기존 행 갱신) | 사용자 정의 종류는 `KindSpec.outcomes` 의 값. 예시 `review`: `approved`/`changes_requested`/`needs_information` | (기존 유지) |

"경계가 헷갈리는 개념" 절에 두 항목을 추가한다: (a) `SuccessorRule` 과 `Chain`: 규칙은 종류 사이의 일반 규칙(워크스페이스에 한 번), 체인은 가져오기로 만든 구체 Task 묶음. 체인의 순서는 규칙이 아니라 `predecessor_task_id` 다. (b) `KindSpec.capability_code` 와 `Capability`: 종류가 요구하는 능력 코드 vs 에이전트가 등록한 능력(코드 + scope). 사용자 정의 종류의 `capability_code` 기본값은 종류 이름과 같다.

### 3. `docs/ARCHITECTURE.md`

"등록·선택·권한" 절 다음에 새 절 `## 업무 종류와 후속 규칙 — 2026-09-21 확정` 을 추가한다. 내용: (1) `KindSpec`·`SuccessorRule` 봉투와 내장 값(개념 절 그대로), (2) 저장 — `kinds(session_id, kind, spec_json, created_at)`, `succession_rules(rule_id, session_id, from_kind, to_kind, rule_json, created_at)`, `tasks.kind` 는 `(session_id, kind)` 로 `kinds` 를 참조, 세션 생성 시 내장 seed, (3) 워커 후속 스캔의 새 조건(결과 + 판정 `passed` + `outcome ∈ on_outcomes`)과 인계 조립(규칙 `handoff_kinds` 로 선행 실행의 산출물을 `inputs` 로, 진단 결과의 근거 원문은 `attachments` 로), (4) 사용자 정의 종류의 실행 — 로컬 도구 읽기 전용 흐름(작업 위치 = 인계 디렉터리, 지시문 + 요청 + 입력 목록, 마지막 메시지 `{outcome, summary}`), (5) 화면 — "업무 종류·규칙" 페이지(목록 + 등록 폼), 업무 등록 폼의 종류 목록은 등록부에서. (6) 한계 — API 에이전트는 `diagnosis` 만, 완료 시 새 업무 생성은 다음.

"최소 데이터 모델과 영속성" 표에 `KindSpec`·`SuccessorRule` 행을 추가하고, "상태·재접속·완료" 절 4번("A 완료 후 B 의 입력을 고정하고 실행을 생성한다")을 새 조건으로 고친다. "DB 제약과 실행 잠금" 표의 "결과 인계" 행에 "규칙 `handoff_kinds` 로 고정" 을 덧붙인다. 다른 절은 손대지 않는다.

### 4. `docs/CONTRACT.md`

`## 11. 업무 종류·후속 규칙·범용 결과` 절을 추가한다. JSON 예시: (a) `KindSpec` — 내장 `code_change` 하나와 사용자 정의 `review` 하나, (b) `SuccessorRule` — 내장 규칙과 `code_change → review` 규칙(`on_outcomes: ["ready_for_review"]`, `handoff_kinds: ["diff", "code_change_result", "test_log_after"]`), (c) `GenericResult` — `review` 의 `approved` 예시, (d) 일반화된 `HandoffBundle` — `source_kind: "code_change"`, `inputs` 3개, `attachments: []`, (e) `ExecutionRequest` — `kind: "review"`, `target: {"local_registration_id": "local-demo-report-claude"}`, `kind_spec` 포함. 4절의 산출물 kind 목록에 `generic_result` 를 추가하고 표에 행(생산자: 연결 프로그램, 내용: 사용자 정의 종류의 결과 봉투)을 넣는다. 2절의 `handoff_bundle` 예시에서 `diagnosis_result_artifact_id` 를 `source_result_artifact_id` 로 바꾸고 `source_kind: "diagnosis"`, `inputs: [{kind: "diagnosis_result", …}]` 를 추가한다.

## Acceptance Criteria

```bash
ls docs/adr/0009-registered-kinds-and-succession-rules.md
grep -n "KindSpec\|SuccessorRule\|GenericResult\|LocalTarget\|InputRef\|source_result_artifact_id" docs/GLOSSARY.md docs/ARCHITECTURE.md docs/CONTRACT.md | wc -l   # 0 이 아니어야 한다
grep -n "diagnosis_result_artifact_id" docs/CONTRACT.md docs/ARCHITECTURE.md   # GLOSSARY 의 금지 표현 행 외에는 남지 않아야 한다 (이 두 파일은 0건)
python3 -m pytest -q          # 문서만 바꿨으므로 그대로 통과
python3 -m ruff check .
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - 문서 사이에 모순이 없는가? (ADR·GLOSSARY·ARCHITECTURE·CONTRACT 가 같은 필드명·같은 내장 값을 쓴다)
   - GLOSSARY 의 금지 표현(`edge`, `transition`, `pipeline`, `DAG`, `graph`)을 새 문장에 쓰지 않았는가?
   - AGENTS.md CRITICAL 규칙과 충돌하는 문장이 없는가? (중앙 LLM 미사용, 셸 명령 미수신)
3. 결과에 따라 `phases/6-typed-handoff/index.json` 의 해당 step 을 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"` (만든 파일·절 이름을 적어 뒤 step 이 찾게 한다)
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- 코드·테스트·템플릿을 만들거나 고치지 마라. 이유: 이 step 은 문서 정본을 만드는 단계이고, 코드는 step 1 부터 이 문서를 읽고 만든다.
- 개념 절과 다른 필드명·값을 문서에 쓰지 마라. 이유: 뒤 step 9개가 이 문서를 시그니처처럼 읽는다.
- `docs/PRD.md`·`docs/UI_GUIDE.md`·`docs/DEPLOY.md` 를 고치지 마라. 이유: step 9 가 구현 결과에 맞춰 한꺼번에 맞춘다.
- 그래프·DAG·pipeline·workflow(제품 흐름 뜻으로) 단어를 설계 설명에 쓰지 마라. 이유: 이 phase 의 결정이 "그리지 않는다"이며 GLOSSARY 금지 표현이다.
- 기존 테스트를 깨뜨리지 마라.
