# Step 2: domain-succession

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/AGENTS.md` (CRITICAL: `domain/` 은 FastAPI·sqlite3·HTTPX·subprocess·Git 을 import 하지 않는다)
- `/docs/adr/0009-registered-kinds-and-succession-rules.md`, `/docs/adr/0004-central-service-rule-based-no-llm.md`
- `/docs/GLOSSARY.md`, `/docs/ARCHITECTURE.md` "업무 종류와 후속 규칙", "등록·선택·권한"
- `/src/workflow/contracts/v1.py` — step 1 이 만든 `KindSpec`·`BUILTIN_KINDS`·`BUILTIN_KIND_NAMES`·`SuccessorRule`·`BUILTIN_RULES`·`Capability`
- `/src/workflow/domain/composition.py` (`_HANDOFF_PAIRS`, `compose`, `PlanNode`, `_mode_reasons`), `/src/workflow/domain/defaults.py` (`_KIND_BY_CODE`, `kind_for_capability`), `/src/workflow/domain/completion.py` (`_TEMPLATES`, `criteria_template`, `can_auto_complete`), `/src/workflow/domain/task_sources.py` (`map_issue`, `_label_value`), `/src/workflow/domain/status.py` (`TaskView.kind`), `/src/workflow/domain/selection.py`
- `/tests/workflow/domain/` 전부 (특히 `test_composition.py`, `test_defaults.py`, `test_completion.py`, `test_task_sources.py`)
- 도메인 함수를 부르는 곳(시그니처가 바뀌면 호출부를 최소로 맞춘다): `grep -rn "kind_for_capability\|can_auto_complete\|criteria_template\|map_issue\|compose(" src tests --include=*.py`

이전 step 에서 만들어진 코드를 꼼꼼히 읽고, 설계 의도를 이해한 뒤 작업하라.

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

도메인 층만 바꾼다. 도메인은 **등록부를 인자로 받는다** — DB 를 모른다. 호출부(`server/web.py`, `server/worker.py`)는 이 step 에서는 `BUILTIN_KINDS`·`BUILTIN_RULES` 를 그대로 넘겨 기존 동작을 유지한다(등록부 조회는 step 7).

### `src/workflow/domain/kinds.py` (신규)

```python
def kind_for_capability(kinds: Sequence[KindSpec], code: str) -> KindSpec | None
    """capability_code 가 code 인 종류. 여럿이면 앞의 것 (등록부가 정렬 책임)."""
def get_kind(kinds: Sequence[KindSpec], kind: str) -> KindSpec | None
def can_auto_complete(spec: KindSpec) -> bool
    """자동 완료 검증기가 있는 종류만 — builtin 이고 output_kind == "diagnosis_result"."""
def validate_capability(kinds: Sequence[KindSpec], capability: Capability) -> str | None
    """None 이면 OK. 아니면 사람이 읽는 사유: '모르는 능력 코드 …' / '{code} 의 scope 키는 {scope_key} 여야 합니다'."""
def validate_rule(kinds: Sequence[KindSpec], rule: SuccessorRule) -> str | None
    """from_kind·to_kind 가 등록돼 있고, on_outcomes ⊆ from_kind.outcomes, to_kind.input_kinds ⊆ handoff_kinds 인가."""
```

### `src/workflow/domain/succession.py` (신규)

```python
def rule_for(rules: Sequence[SuccessorRule], from_kind: str, to_kind: str) -> SuccessorRule | None
def may_continue(rule: SuccessorRule, outcome: str) -> bool          # outcome in rule.on_outcomes
def continue_reason(rule: SuccessorRule, outcome: str) -> str        # "선행 outcome ready_for_handoff — 규칙 diagnosis → code_change 로 착수" / "선행 outcome needs_information 은 규칙 대상 아님 — 확인 필요"
```

### 기존 모듈 정리

- `defaults.py`: `_KIND_BY_CODE`·`kind_for_capability` 를 지운다(→ `kinds.py`). `default_run_mode`·`default_selection_mode`·`default_completion_mode` 는 유지.
- `completion.py`: `criteria_template(spec: KindSpec) -> list[Criterion]` — 내장 두 종류는 기존 `_TEMPLATES` 그대로, 사용자 정의는 항목 하나 `Criterion(code="outcome_in_spec", text="결과 outcome 이 허용 목록 안 · 사람 검토 승인", structured=None)` (기존 `Criterion` 필드 구조를 따른다). `can_auto_complete` 는 `kinds.py` 로 옮기고 여기서는 re-export 하지 않는다.
- `status.py`: `TaskView.kind: str` (Literal 해제). `user_status` 가 `kind` 로 분기하는 곳이 있으면 `"code_change"` 비교는 유지한다(연결 상태 판단은 step 7 에서 에이전트 연결 유형으로 바꾼다).
- `task_sources.py`: `map_issue(issue: Issue, kinds: Sequence[KindSpec]) -> IssueMapping`. 기존 두 라벨 규칙(`incident`+`workflow:<id>`+`run:<run_id>` → `operations.diagnose`, `bug`+`repo:<id>` → `code.modify`)은 그대로 두고(내장 종류가 등록돼 있을 때만 적용), 일반 규칙을 추가한다: 라벨 `kind:<kind>` 가 있고 그 종류가 `kinds` 에 있으며 라벨 `<scope_key>:<value>` 가 있으면 `Capability(code=spec.capability_code, scope={scope_key: value})`, 이유 "라벨 kind:review + repository_id:demo-report-repo → review". `kind:` 라벨의 종류가 없으면 이유 "등록되지 않은 종류 kind:…". 우선순위: `kind:` 라벨이 있으면 일반 규칙, 없으면 기존 두 규칙.
- `composition.py`: `compose(issues, candidates, prefer, *, kinds: Sequence[KindSpec], rules: Sequence[SuccessorRule]) -> ChainPlan`. `_HANDOFF_PAIRS`·`_UNSUPPORTED_PAIR_REASON` 을 지우고 인접 쌍은 `rule_for(rules, prev.kind, kind)` 로 판단(없으면 Standalone, 이유 "후속 규칙 없음: {prev.kind} → {kind}"). `PlanNode.kind: str`. `kind = kind_for_capability(kinds, capability.code)` (None 이면 Standalone "등록되지 않은 능력 코드"). `completion_mode` 는 `can_auto_complete(spec)` 로. `_mode_reasons` 의 문구 "자동 실행 — 선행 완료 후 별도 조작 없이 착수" → "자동 실행 — 선행 결과가 규칙에 맞으면 별도 조작 없이 착수". `criteria_template(spec)`.

### 호출부 최소 수정

`server/web.py`·`server/worker.py` 에서 위 함수를 부르는 곳은 시그니처만 맞춘다: `kinds=BUILTIN_KINDS`, `rules=BUILTIN_RULES` 를 넘기고 `kind_for_capability(BUILTIN_KINDS, code)` 처럼 바꾼다. 반환이 `KindSpec | None` 이 된 곳은 `.kind` 를 꺼내 쓰되 None 처리는 기존 422 경로를 재사용한다. 그 이상은 바꾸지 않는다.

### 테스트 (먼저 작성)

- `tests/workflow/domain/test_kinds.py`: `kind_for_capability`(내장 둘·모르는 코드 None), `can_auto_complete`(diagnosis True, code_change False, 사용자 정의 False), `validate_capability`(OK·모르는 코드·scope 키 불일치 사유 문구), `validate_rule`(OK·없는 종류·`on_outcomes` 초과·`input_kinds` 미포함).
- `tests/workflow/domain/test_succession.py`: `rule_for` 내장 규칙, 없는 쌍 None, `may_continue`·`continue_reason` 두 문구.
- `test_composition.py`: 기존 케이스를 `kinds=BUILTIN_KINDS, rules=BUILTIN_RULES` 로 유지 + **규칙 한 줄 추가 케이스**: `review` 종류(개념 절 값)와 규칙 `code_change → review` 를 넘기면 이슈 3개(incident → bug → `kind:review`+`repository_id:…`)가 노드 3개 체인이 되고, 규칙을 빼면 세 번째가 Standalone("후속 규칙 없음") 이 된다. 이유 문장 확인.
- `test_task_sources.py`: 일반 라벨 규칙 3케이스(성공·종류 없음·scope 라벨 없음), 기존 규칙 유지.
- `test_completion.py`·`test_defaults.py`·`test_status.py`: 시그니처 변경 반영.

## Acceptance Criteria

```bash
python3 -m pytest tests/workflow/domain -q
python3 -m pytest -q
python3 -m ruff check .
grep -n "_HANDOFF_PAIRS\|_KIND_BY_CODE" src/workflow/domain/*.py | wc -l     # 0
grep -n "^import\|^from" src/workflow/domain/kinds.py src/workflow/domain/succession.py   # contracts·표준 라이브러리만
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - `domain/` 이 FastAPI·sqlite3·HTTPX·subprocess·Git·`adapters`·`server` 를 import 하지 않는가?
   - 모든 자동 결정에 사람이 읽는 이유 문장이 붙는가? (ADR-0004)
   - GLOSSARY 용어 그대로인가? (`SuccessorRule`, `KindSpec`, `Standalone`, `PlanNode`)
   - 호출부 수정이 시그니처 맞춤을 넘지 않았는가? (`git diff src/workflow/server` 가 짧아야 한다)
3. 결과에 따라 `phases/6-typed-handoff/index.json` 의 해당 step 을 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"` (새 모듈·함수 시그니처·바뀐 호출부)
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- 도메인에 DB·HTTP 를 넣지 마라 — 등록부는 항상 인자로 받는다. 이유: AGENTS.md CRITICAL, 테스트 가능성.
- 후속 착수 조건(결과 + 판정 + outcome)을 이 step 에서 워커에 구현하지 마라. 이유: step 4 의 몫. 여기서는 판단 함수(`may_continue`)만 만든다.
- 규칙 표를 코드 상수로 늘리지 마라(`BUILTIN_RULES` 는 내장 1개뿐). 이유: 규칙은 등록 데이터다 — 테스트에서만 `review` 규칙을 만든다.
- `compose` 에 LLM 호출이나 자유 문장 추론을 넣지 마라. 이유: ADR-0004.
- 기존 테스트를 깨뜨리지 마라(시그니처 변경 반영만).
