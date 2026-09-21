# Step 8: e2e-third-kind

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/AGENTS.md` (`src/workflow/scripted/` 는 공개 데모 전용 대본 — `connector/` 에서 import 하지 않는다)
- `/docs/adr/0009-registered-kinds-and-succession-rules.md`, `/docs/adr/0008-public-demo-scripted-agents.md`
- `/docs/ARCHITECTURE.md` "업무 종류와 후속 규칙", "공개 데모 구성"; `/docs/GLOSSARY.md` (`scripted agent`, `LocalStack`, `demo-report-repo`); `/docs/VERIFICATION_LOG.md` (형식)
- `/src/workflow/scripted/codex.py`·`claude.py`·`_common.py` (인자 형식·`read_handoff_response`·결과 봉투), `/tests/workflow/scripted/`
- `/src/workflow/connector/prompt.py` 의 `build_generic_prompt` 첫 줄 형식(`# 업무 종류: {kind} ({label})`), `/src/workflow/connector/codex.py`·`claude.py` 의 `launch_readonly` argv(`--output-schema <파일>` / `--json-schema <JSON>`)
- `/scripts/seed_demo.py` (`LOCAL_AGENTS`, `capabilities`), `/scripts/local_stack.py`, `/scripts/test_seed_demo.py`
- `/tests/e2e/conftest.py`, `/tests/e2e/test_scenario.py` 전부 (`_create_task`, `_select`, `_watch`, `_status`, `_chips`, `_raw`, test_01~11 의 흐름과 `ctx`)
- `/src/workflow/server/web.py` 의 `/kinds`·`/rules`·`/tasks`·`/operator/agents` 라우트 폼 필드(step 6·7)

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

## 시작 상태 (이전 시도의 결과)

이 step 의 첫 시도가 아래를 이미 만들고 커밋했다(`de06ffc`, 2026-09-21 23:24). 그 뒤 세 번의 재시도는 Claude 세션 한도(429)로 시작하지 못했다 — 코드 문제가 아니다. **다시 만들지 말고 읽고 이어서 하라**:

- `src/workflow/scripted/_common.py`·`codex.py`·`claude.py` — 사용자 정의 종류 응답(`generic_kind_of`·`generic_outcomes`·`generic_result` 류), `tests/workflow/scripted/` 갱신 (60 passed 확인됨)
- `scripts/seed_demo.py` — Claude 에이전트에 `review` 능력 추가, `scripts/test_seed_demo.py` 갱신
- `tests/e2e/test_scenario.py` — test_12~18 추가(252줄). **아직 실행·통과가 확인되지 않았다.** `python3 -m pytest tests/e2e -q` 를 돌려 실패를 고치는 것이 이번 시도의 첫 일이다.
- `docs/VERIFICATION_LOG.md` 절은 아직 없다.

남은 일: e2e 통과 → VERIFICATION_LOG 절 → 전체 pytest·ruff → `index.json` 상태 갱신.

## 작업

이 phase 의 증명이다: **e2e 가 화면으로 종류·규칙을 등록해 세 번째 단계를 자동 착수시킨다.** `composition.py`·`worker.py` 는 이 step 에서 한 줄도 바꾸지 않는다.

### 1. 대본 에이전트가 사용자 정의 종류에 답한다 — `src/workflow/scripted/`

- `_common.py`: `generic_kind_of(prompt: str) -> str | None` — 프롬프트 첫 줄이 `# 업무 종류: {kind} (` 형식이면 `kind` 를 돌려준다. `generic_outcomes(schema: dict) -> list[str]` — `properties.outcome.enum`. `generic_result(kind, outcomes, handoff_listing) -> dict` — `{"outcome": outcomes[0], "summary": f"대본 {kind}: 인계 자료 {n}개 확인 — {파일명 나열}"}`. 파일을 만들거나 고치지 않는다.
- `codex.py`: `--output-schema <path>` 를 읽어 스키마를 얻고, `generic_kind_of(prompt)` 가 있으면 인계 응답 찾기·수정 적용을 건너뛰고 `generic_result` 를 마지막 메시지 파일과 JSONL 로 낸다(기존 3줄 형식과 같은 봉투, `structured` 자리에 `{outcome, summary}`).
- `claude.py`: `--json-schema <json>` 을 읽어 같은 처리. `structured_output = {outcome, summary}`.
- `-C`/cwd 가 인계 디렉터리라는 점 외에 인자 형식은 바뀌지 않는다(connector `build_readonly_argv` 가 낸 인자를 그대로 받는다).
- 테스트 `tests/workflow/scripted/`: 사용자 정의 종류 프롬프트에 두 대본이 `outcome == enum[0]` 을 내고 cwd 파일을 바꾸지 않음; 기존 코드 수정 대본 회귀 없음.

### 2. seed — `scripts/seed_demo.py`

`LOCAL_AGENTS` 의 Claude 에이전트(`agent-claude-mac`) 능력을 `[code.modify(repository_id), review(repository_id)]` 두 개로 한다(Codex 는 그대로). `review` 는 e2e 가 등록할 종류의 `capability_code` 와 같아야 한다 — 상수 `REVIEW_CAPABILITY_CODE = "review"` 로 두고 e2e 도 그 값을 쓴다. 공개 데모에는 `review` 종류가 등록되지 않으므로 이 능력은 매칭되지 않고 카탈로그 카드에 코드만 보인다(허용). `scripts/test_seed_demo.py` 갱신.

### 3. e2e — `tests/e2e/test_scenario.py`

기존 test_01~11 뒤에 **세 번째 종류** 절을 추가한다(같은 `stack`·`client`·`ctx` 를 잇거나, 필요하면 별도 세션 fixture — 기존 체인 e2e 의 `chain_stack`·`judge` 방식을 참고). 순서:

- `test_12_register_review_kind_and_rule_via_pages`: `POST /kinds`(`review`, 라벨 "검토", capability `review`, scope_key `repository_id`, input_kinds `diff`·`code_change_result`, outcomes `approved`·`changes_requested`·`needs_information`, 지시문 한 문단) → 302; `POST /rules`(`code_change` → `review`, on `ready_for_review`, handoff `diff`·`code_change_result`·`test_log_after`) → 302; `GET /kinds` 에 둘 다 보임.
- `test_13_register_review_task_c_after_b`: A(진단, `with_successor` 로 B)·B 가 이미 있으면 재사용, 없으면 test_02·03 방식으로 만든다. C 를 `/tasks` 폼으로: capability `review`, scope `demo-report-repo`, `predecessor_task_id = B`, `run_mode=auto`, `selection_mode=auto` → 상태 `대기`(이유에 "선행 결과 대기").
- `test_14_a_then_b_then_c_start_without_human_click`: A 실행 → A `완료`(검증기) → B 자동 착수 → B `확인 필요`(검토 대기) → **B 를 승인하기 전에** C 가 `실행 요청됨`→`실행 중`→`확인 필요` 가 됨(`_watch`). C 의 이유가 "검토 대기".
- `test_15_c_inputs_and_result`: C 실행의 산출물 칩에 `generic_result`·`claude_jsonl` 이 있고, `generic_result` 원문(`_raw`)의 `kind == "review"`, `outcome == "approved"`, `summary` 에 `diff`·`code_change_result` 파일명이 들어 있음(대본이 인계 목록을 적는다); 인계 묶음(`handoff_bundle`)의 `inputs` kind 집합이 규칙과 같고 `source_kind == "code_change"`.
- `test_16_approve_b_then_c_and_no_duplicate`: B 승인 → `완료`; C 승인 → `완료`; 워커 tick 몇 번 뒤에도 실행 수가 각각 1(중복 없음).
- `test_17_rule_removed_stops_new_succession`: 규칙 삭제 후 새 A'→B'→C' 를 만들고 A' 실행 → B' 는 내장 규칙으로 착수하지만 C' 는 `대기` 이유 "후속 규칙 없음: code_change → review".
- `test_18_demo_repo_untouched_by_review`: 데모 저장소 `main`·`task/{B}` 브랜치가 C 실행 전후로 같고, C 의 인계 디렉터리에 새 파일이 없다(대본이 읽기만 했다).

`_create_task` 등 헬퍼는 재사용하고, 새 폼 필드(`predecessor_task_id`, `capability_code=review`)만 추가한다. 타임아웃은 기존 e2e 값을 따른다.

### 4. 기록 — `docs/VERIFICATION_LOG.md`

`## 2026-09-21 — 세 번째 종류 review 자동 착수 (phase 6 step 8)` 절: 실행 명령, 통과 건수, **`git diff --stat feat-6-typed-handoff~N -- src/workflow/domain/composition.py src/workflow/server/worker.py` 가 이 step 의 커밋에서 비어 있음**을 적는다(증명의 핵심). 대본이라 실제 모델은 돌지 않았음을 명시.

## Acceptance Criteria

```bash
python3 -m pytest tests/workflow/scripted scripts/test_seed_demo.py -q
python3 -m pytest tests/e2e -q                     # 대본 스택 — 실제 codex·claude·OpenAI 호출 없음
python3 -m pytest -q
python3 -m ruff check .
git diff --stat HEAD -- src/workflow/domain/composition.py src/workflow/server/worker.py | wc -l   # 0 (이 step 의 작업 트리에서)
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - `connector/`·`server/` 가 `scripted/` 를 import 하지 않는가? (PATH 래퍼로만 앞에 둔다)
   - 대본이 인계 디렉터리·저장소를 바꾸지 않는가?
   - e2e 가 실제 `codex`·`claude`·OpenAI 를 부르지 않는가?
   - 세 번째 단계가 `composition.py`·`worker.py` 변경 없이 붙었는가?
   - GLOSSARY 용어 그대로인가?
3. 결과에 따라 `phases/6-typed-handoff/index.json` 의 해당 step 을 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"` (e2e 건수·대본 변경·seed 변경·VERIFICATION_LOG 절)
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- `src/workflow/domain/composition.py`·`src/workflow/server/worker.py` 를 바꾸지 마라. 이유: 이 step 의 존재 이유가 "규칙 등록만으로 붙는다"의 증명이다. 그 둘을 고쳐야만 통과한다면 `error` 로 남기고 무엇이 막혔는지 `error_message` 에 적어라.
- e2e 에서 `review` 종류를 seed 나 코드 상수로 넣지 마라 — `/kinds`·`/rules` 폼으로 등록하라. 이유: 같은 증명.
- 대본 에이전트가 파일을 쓰거나 저장소를 건드리게 하지 마라. 이유: `LocalTarget` 은 읽기 전용(ADR-0009), connector 가 `readonly_violation` 으로 실패시킨다.
- 실제 `codex`·`claude`·OpenAI 를 테스트에서 부르지 마라. 이유: 사용량·비용.
- 공개 데모 fixture(`task_source_fixtures/*.json`)에 `review` 이슈를 넣지 마라. 이유: 심사 중 공개 데모 화면이 바뀐다.
- 기존 테스트를 깨뜨리지 마라.
