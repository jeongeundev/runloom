# Step 7: web-task-kinds

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/AGENTS.md`
- `/docs/adr/0009-registered-kinds-and-succession-rules.md`, `/docs/adr/0004-central-service-rule-based-no-llm.md`, `/docs/adr/0005-access-model-anonymous-session-operator-token.md`
- `/docs/ARCHITECTURE.md` "업무 종류와 후속 규칙", "등록·선택·권한", "상태·재접속·완료"; `/docs/UI_GUIDE.md` "업무 상세", "결과 카드", "상태 표시"; `/docs/PRD.md` 2절·3절·4절; `/docs/GLOSSARY.md`
- `/src/workflow/contracts/v1.py` — `KindSpec`·`Capability`·`LocalTarget`·`GenericResult`·`ExecutionRequest.kind_spec`·`BUILTIN_KIND_NAMES`
- `/src/workflow/domain/kinds.py` (`kind_for_capability`, `get_kind`, `can_auto_complete`, `validate_capability`), `/src/workflow/domain/completion.py` (`criteria_template(spec)`), `/src/workflow/domain/task_sources.py` (`map_issue(issue, kinds)`), `/src/workflow/domain/composition.py` (`compose(..., kinds=, rules=)`), `/src/workflow/domain/status.py` (`TaskView`)
- `/src/workflow/adapters/repo.py` — `list_kinds`·`get_kind`·`list_rules`·`get_rule`·`predecessor_ready_execution`
- `/src/workflow/server/web.py` 전부 — 특히 `SCOPE_KEYS`·`CAPABILITY_CODES`·`EXAMPLES`, `_target_for`, `_handoff_inputs`, `_start_execution`, `_form_context`, `task_new`, `task_create`, `_insert_new_task`, `_issue_view`, `tasks_import_page`, `tasks_import`, `_run_task`, `task_select`, `task_review`, `operator_register_agent`
- `/src/workflow/server/views.py` (`build_task_view`, `task_context`, `RESULT_KINDS`, 결과 카드 조립), `/src/workflow/server/filters.py` (`OUTCOME_LABELS`, `KIND_LABELS`, `outcome_label`), `/src/workflow/server/machine_api.py`(연결 프로그램의 등록 보고가 능력을 만지는지 확인)
- 템플릿: `task_new.html`, `task_detail.html`, `_result_card.html`, `tasks_import.html`, `chain_detail.html`, `operator.html`, `agent_detail.html`, `agents_register.html`
- `/tests/workflow/server/test_web.py`, `test_views.py`, `test_ui.py`, `test_filters.py`, `test_machine_api.py`, `conftest.py`
- step 6 이 만든 `kinds.html`·`views.kind_public` (같은 라벨 함수를 재사용)

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

웹 계층의 업무 등록·가져오기·직접 실행·검토·화면이 **등록부(세션의 종류·규칙)** 를 보게 한다. `SCOPE_KEYS`·`CAPABILITY_CODES` 같은 하드코딩 사전을 없앤다.

### 1. 등록부 조회 헬퍼 — `web.py`

```python
def _kinds(conn, session_id) -> list[KindSpec]            # repo.list_kinds
def _kind_of(conn, session_id, kind) -> KindSpec           # 없으면 PageError(422, "invalid_field", "등록되지 않은 업무 종류입니다.", field="capability_code")
def _kind_for_code(conn, session_id, code) -> KindSpec     # kind_for_capability; 없으면 위와 같은 422
```

### 2. 업무 등록 — `task_new` / `task_create` / `_insert_new_task` / `task_new.html`

- 폼의 "요구 능력" select 는 세션 종류 목록(`label (kind) · capability_code · scope_key`)에서 온다. `scope_value` 라벨은 선택한 종류의 `scope_key` 를 보여 준다(소량 JS: `data-scope-key`).
- `task_create`: `capability_code` → `_kind_for_code` → `spec`; `Capability(code=spec.capability_code, scope={spec.scope_key: scope_value})`; `validate_capability(kinds, capability)` 사유가 있으면 422. `kind = spec.kind`. `completion_mode == "auto"` 는 `can_auto_complete(spec)` 일 때만(기존 422 문구). `run_id` 는 `spec.kind == "diagnosis"` 일 때만 필수.
- `_target_for(spec, run_id, agent)`: `diagnosis` → `{run_id}`; `code_change` → 기존 3필드(에이전트 등록값); 그 외 → `{"local_registration_id": agent["local_registration_id"]}` (agent None 이면 `{}` — 자동 선택 뒤 채운다: 기존에 코드 수정도 선택 후 채우는 경로가 있다면 그것을 따른다; 없다면 `task_select`·`_run_task` 에서 선택된 에이전트로 `target_json` 을 갱신하는 기존 방식대로).
- `criteria_template(spec)`.
- `with_successor`: 기존 시연 전용(진단 → `EXAMPLES["fix"]`)을 유지하되 `spec.kind == "diagnosis"` 이고 세션에 규칙 `diagnosis → code_change` 가 있을 때만 체크박스를 보인다. 일반화하지 않는다(요청 텍스트·범위를 추측할 수 없다 — ADR-0009 트레이드오프).
- `EXAMPLES` 는 유지(내장 종류만 가리킨다).

### 3. 가져오기 — `tasks_import_page` / `tasks_import` / `_issue_view`

`compose(issues, candidates, prefer, kinds=_kinds(...), rules=[r for _, r in repo.list_rules(...)])`. `_issue_view` 의 `SCOPE_KEYS[...]` 는 `spec.scope_key` 로. `map_issue(issue, kinds)`. 저장 시 `target_json` 은 `_target_for(spec, run_id, agent)`.

### 4. 직접 실행·선택·검토 — `_handoff_inputs` / `_run_task` / `task_select` / `_start_execution` / `task_review`

- `_handoff_inputs(conn, task)`: 선행의 **준비된 실행**(`repo.predecessor_ready_execution`)이 만든 가장 최근 `handoff_bundle`. 없으면 `([], None)` — 워커가 다음 tick 에 조립한다(run_mode manual 도 워커가 조립, step 4).
- `_start_execution`: `kind_spec = repo.get_kind(conn, session_id, task["kind"])` 를 요청에 넣는다(None 이면 409 `request_incomplete` 문구에 "업무 종류가 등록돼 있지 않습니다" 추가). `diagnosis` 상한 검사 유지. `code_change` 가 아닌 로컬 종류는 `target` 이 `{"local_registration_id": …}` 인지 확인(비면 409 `request_incomplete`).
- `_run_task`: 선행이 있는 Task 의 직접 실행은 선행의 결과가 준비돼 있고(`predecessor_ready_execution`) 인계 묶음이 있을 때만(기존은 선행 `완료` 조건 — ADR-0009 (3) 에 맞춘다). 규칙이 없으면 인계 묶음이 없으므로 409 문구 "후속 규칙이 없어 인계 자료가 없습니다. /kinds 에서 규칙을 등록하세요."
- `task_review`: `request_changes` 재시도에서 `target` 계승은 종류 무관(기존). `approve` 문구 분기(`code_change` 는 "병합: 운영자 확인 대기") 유지.

### 5. 화면 — `views.py` / `filters.py` / 템플릿

- `TaskView.kind: str`. `connector_online` 은 **선택된 에이전트의 `connection_type == "local"`** 로 판단(종류 이름 비교 제거).
- `RESULT_KINDS = ("diagnosis_result", "code_change_result", "generic_result")`. 결과 카드: `generic_result` 는 outcome 배지 + `summary` + 산출물 칩(원시 로그). `_result_card.html` 에 분기 추가. `OUTCOME_LABELS` 에 없는 outcome 은 코드 값 그대로 배지(기존 `outcome_label` 의 fallback 확인).
- 업무 상세의 종류 표시는 `spec.label` (없으면 `kind` 그대로). `task_context` 에 `kind_label` 추가.
- 체인 화면(`chain_detail.html`·`_chain_live.html`): 노드 3개 이상도 순서대로 렌더링됨을 확인(템플릿이 2개를 가정한 곳이 있으면 고친다). 사람 게이트 문구는 마지막 노드의 `completion_mode` 로(기존).
- `agent_detail`·`agents_register`·`operator.html` 의 능력 표시: 코드 값 그대로 + 세션에 그 코드의 종류가 있으면 라벨 병기.

### 6. 운영자 에이전트 등록 — `operator_register_agent`

폼에 `scope_key` 필드 추가. 비어 있으면 `BUILTIN_KINDS` 에서 `capability_code` 로 찾은 `scope_key`(내장이 아니면 422 "scope 키를 입력하세요"). `Capability` 는 계약 패턴으로 검증(422). 운영자는 세션 무관이므로 `validate_capability` 를 세션 등록부로 돌리지 않는다 — 카탈로그 에이전트의 능력 코드가 어느 세션의 종류와 맞는지는 그 세션의 선택 시점에 자연히 결정된다. `operator.html` 폼에 필드 추가.

### 7. 정리

`web.py` 에서 `SCOPE_KEYS`·`CAPABILITY_CODES` 와 `"diagnosis"`/`"code_change"` 문자열 비교를 검색해, 남는 곳이 (a) `run_id` 필수 검사, (b) 진단 상한 검사, (c) `with_successor` 시연 분기, (d) 검토 승인 문구, (e) `_target_for` 의 내장 두 분기 — 다섯 곳뿐이게 한다.

### 테스트 (먼저 작성)

- `test_web.py`: 종류 `review` + 규칙 등록 후 `/tasks/new` select 에 `review` 가 나오고 등록이 되며 `target_json == {"local_registration_id": …}`(자동 선택 후); `scope` 키 불일치 422 사유; 자동 완료는 진단만; 가져오기에서 `kind:review`+`repository_id:` 라벨 이슈가 체인 3번째 노드가 됨(규칙 있을 때)/Standalone(없을 때); `_start_execution` 요청에 `kind_spec` 포함; 선행 `확인 필요`(result_ready + 판정) 상태에서 후속 직접 실행이 가능; 규칙 없으면 409 문구; 운영자 폼 `scope_key`(내장 생략 가능·사용자 정의 필수·패턴 422).
- `test_views.py`·`test_filters.py`: `generic_result` 결과 카드(outcome 배지 코드 그대로·summary), `connector_online` 이 로컬 에이전트면 종류 무관하게 계산, `kind_label`.
- `test_ui.py`: 업무 상세에 종류 라벨, 체인 3노드 렌더링.
- `test_machine_api.py`: 회귀만(변경 없어야 함).

## Acceptance Criteria

```bash
python3 -m pytest tests/workflow/server -q
python3 -m pytest -q
python3 -m ruff check .
grep -n "SCOPE_KEYS\|CAPABILITY_CODES" src/workflow/server/*.py | wc -l     # 0
grep -n '"diagnosis"\|"code_change"' src/workflow/server/web.py            # 위 "정리" 의 다섯 곳만
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - 요구 능력·종류 판단이 등록부 + 도메인 함수로 이뤄지고 웹에 새 규칙 논리가 없는가? (ADR-0004)
   - 직접 실행 조건이 ADR-0009 (3)(선행 결과 + 판정)과 같은가?
   - 화면에 `outcome` 코드 값과 라벨을 구분해 쓰는가? (GLOSSARY `outcome 라벨`)
   - 비밀값·토큰이 템플릿·응답에 없는가?
   - `server/` 가 `connector/` 를 import 하지 않는가?
3. 결과에 따라 `phases/6-typed-handoff/index.json` 의 해당 step 을 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"` (바뀐 라우트·헬퍼·템플릿·남긴 문자열 비교 다섯 곳)
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- `with_successor` 를 일반화하지 마라(진단 시연 전용 유지). 이유: 후속 업무의 요청·범위는 추측할 수 없다 — 완료 시 생성은 다음 phase(ADR-0009 트레이드오프).
- 진단 API 종류를 직접 실행 외의 경로(워커 자동 착수)로 열지 마라. 이유: step 4 의 결정과 같다.
- 종류·규칙 등록 페이지(step 6)의 라우트·템플릿을 다시 쓰지 마라 — 라벨 함수만 재사용. 이유: 한 step 은 한 층.
- `worker.py`·`connector/`·`scripted/` 를 바꾸지 마라. 이유: 이 step 은 웹만.
- 기존 테스트를 깨뜨리지 마라.
