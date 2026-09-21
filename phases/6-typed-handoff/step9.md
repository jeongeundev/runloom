# Step 9: docs-sync

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/AGENTS.md`
- `/docs/adr/0009-registered-kinds-and-succession-rules.md` (step 0), `/docs/GLOSSARY.md`, `/docs/ARCHITECTURE.md`, `/docs/CONTRACT.md`, `/docs/PRD.md`, `/docs/UI_GUIDE.md`, `/docs/DEPLOY.md`, `/docs/CURRENT_HANDOFF.md`, `/docs/VERIFICATION_LOG.md`, `/docs/README.md`
- `/phases/6-typed-handoff/index.json` — step 0~8 의 `summary` 전부 (무엇이 실제로 만들어졌는지의 정본)
- 실제 코드: `/src/workflow/contracts/v1.py`, `/src/workflow/domain/kinds.py`·`succession.py`·`composition.py`, `/src/workflow/adapters/db.py`(`_SCHEMA`)·`repo.py`, `/src/workflow/server/worker.py`(`tick`, `_spawn_successors`, `_check_generic_results`)·`web.py`(`/kinds`·`/rules`·`task_create`)·`views.py`·`filters.py`, `/src/workflow/connector/local_tool.py`(`_run_generic`)·`prompt.py`(`build_generic_prompt`)·`runner.py`, `/src/workflow/scripted/_common.py`, `/scripts/seed_demo.py`, `/tests/e2e/test_scenario.py` 의 test_12~18
- `/phases/5-scripted-demo/step11.md` (이전 phase 의 문서 동기화 step — 형식 참고)

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

문서를 **구현 결과**에 맞춘다. step 0 이 설계로 쓴 문장 중 구현이 달라진 것을 찾아 고치고, 빠진 것을 채운다. 코드는 바꾸지 않는다(문서와 코드가 다르면 문서를 고치고, 코드가 ADR 을 어긴 것이면 `error` 로 남긴다).

### 1. `docs/GLOSSARY.md`

step 0 이 넣은 행을 실제 식별자와 대조한다(`KindSpec` 필드명, `SuccessorRule`, `GenericResult`, `LocalTarget`, `InputRef`, `tasks_with_ready_predecessor`, `predecessor_ready_execution`, 실패 코드 `kind_spec_missing`·`result_invalid`·`readonly_violation`, 오류 코드 `kind_exists`·`rule_exists`·`kind_protected`·`kind_in_use`). 새로 생긴 이름은 행을 추가하고, 화면 문구("업무 종류", "후속 규칙", "받는 산출물", "내는 산출물", "결과값", "선행 결과 대기", "후속 규칙 없음")를 `사용자 상태`·`outcome 라벨` 행 옆에 정리한다. 갱신일을 이 step 의 날짜로.

### 2. `docs/ARCHITECTURE.md`

"업무 종류와 후속 규칙" 절을 구현대로 정정: 테이블 정의(`_SCHEMA` 그대로), `tick` 순서(판정 두 개 → 후속 스캔), 후속 착수 조건의 정확한 조회(`tasks_with_ready_predecessor` 의 (a)/(b)), 인계 조립(`inputs` 파일명 규칙 `{kind}.{ext}`·중복 규칙), 사용자 정의 종류의 실행(읽기 전용 인자 — Codex `--sandbox read-only`, Claude `READONLY_TOOLS`; `readonly_violation`), 화면. "검증 순서와 다음 결정" 표에 6번 행 "세 번째 종류 — 대본 e2e 통과(test_12~18), 실제 Claude 로는 미검증" 을 추가한다. "디렉터리와 의존 방향" 트리에 `domain/kinds.py`·`succession.py` 를 반영한다(트리가 파일 단위면).

### 3. `docs/CONTRACT.md`

11절의 JSON 예시를 실제 모델의 `model_dump_json()` 결과와 맞춘다(필드 순서·기본값). 2절 인계 묶음 예시의 `inputs` 를 실제 조립 결과 형식으로. 4절 표의 `generic_result` 행 확인.

### 4. `docs/PRD.md`

2절 "기본값" 에 "사용자 정의 종류의 완료 방식은 검토 후 완료만" 을, 3절 상태 대응표에 새 이유 문구("선행 결과 대기", "후속 규칙 없음: …", "선행 outcome … 은 규칙 대상 아님")를, 4절 검토에 "사람 승인 전에 후속(예: 에이전트 검토)이 먼저 돌 수 있다 — 승인 화면에서 후속 결과를 함께 본다" 를 추가한다. 공모전 시나리오 절은 손대지 않는다.

### 5. `docs/UI_GUIDE.md`

"화면 목록" 에 `/kinds` 추가(3열 셸, 카드·한 줄 규칙). "결과 카드" 에 `generic_result` 카드(outcome 배지 코드 그대로·summary). "상태 표시" 에 새 이유 문구.

### 6. `docs/DEPLOY.md`

"7b. 코드 갱신" 에 이 phase 가 스키마 버전 3 이라 **심사 이후** 배포 시 `WORKFLOW_RESET_DB=1` 이 필요하고(백업 절차 기존), 심사 기간(~10-05)에는 배포하지 않는다는 문장을 넣는다. "10. 알려진 한계" 에 "API 에이전트는 `diagnosis` 만, 완료 시 새 업무 생성 없음, 사람이 선행을 종료해도 이미 시작한 후속은 계속" 을 추가한다.

### 7. `docs/CURRENT_HANDOFF.md`

"지금 상태" 표에 phase 6 완료를, "다음 세션에서 할 일" 에 (1) 실제 Claude 로 `review` 종류 1회 실연동(사용량 확인 후, 사람이 지시할 때만), (2) n8n 입구 phase(업무 `callback_url` + `TaskSource` n8n, 3~4 노드 워크플로우 — 별도 ADR), (3) 완료 시 새 업무 생성 규칙(ADR-0009 트레이드오프), (4) 심사 후 VM 배포(`WORKFLOW_RESET_DB=1`) 를 적는다. "제품의 중심" 절에 ADR-0009 한 줄 요약을 덧붙인다: "흐름을 그리지 않는다 — 종류·규칙을 등록하면 흐른다." 실제 미리디 공고·n8n 관련 문장은 있는 그대로 두고 새 주장을 넣지 않는다.

### 8. `docs/README.md`·`AGENTS.md`

`docs/README.md` 의 문서 목록에 ADR-0009 를 넣는다. `AGENTS.md` 는 "아키텍처 규칙" 에 한 줄만 추가한다: "업무 종류·후속 규칙은 워크스페이스 등록 데이터다(ADR-0009). 새 단계를 붙일 때 `composition.py`·`worker.py` 에 종류 이름 분기를 늘리지 않는다 — 규칙 행으로 되는지가 설계 기준." (AGENTS.md 는 모든 step 프롬프트에 주입되므로 짧게.)

## Acceptance Criteria

```bash
python3 -m pytest -q          # 문서만 바꿨으므로 그대로 통과
python3 -m ruff check .
grep -n "0009" docs/README.md AGENTS.md | wc -l                     # 2 이상
grep -n "WORKFLOW_RESET_DB" docs/DEPLOY.md | wc -l                  # 1 이상
grep -rn "diagnosis_result_artifact_id\|_HANDOFF_PAIRS\|tasks_with_completed_predecessor" docs/*.md docs/adr/*.md | grep -v "이전 이름\|금지 표현\|지운다" | wc -l   # 0
git diff --stat HEAD -- src tests scripts | wc -l                   # 0 (코드 변경 없음)
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - 문서의 식별자·테이블·경로가 코드와 정확히 같은가? (grep 으로 하나씩 확인)
   - ADR-0009 와 구현이 어긋난 곳이 있는가? 있으면 문서를 고치지 말고 `error` 로 남겨라.
   - GLOSSARY 금지 표현(`edge`, `transition`, `pipeline`, `DAG`, `graph`)이 새 문장에 없는가?
   - AGENTS.md 추가가 한 줄인가?
3. 결과에 따라 `phases/6-typed-handoff/index.json` 의 해당 step 을 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"` (고친 문서·절 목록, 발견한 설계-구현 차이)
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- 코드·테스트를 고치지 마라. 이유: 이 step 은 문서 동기화. 코드가 ADR 을 어겼으면 `error` 로 사람이 판단한다.
- VM 에 배포하거나 `deploy/` 스크립트를 실행하지 마라. 이유: 심사 기간(~2026-10-05) 공개 데모 동결.
- 미리디·n8n 등 외부 조직에 대해 확인되지 않은 주장을 문서에 넣지 마라. 이유: CURRENT_HANDOFF 의 기존 방침.
- 공모전 시나리오·심사자 흐름 문서를 새 설계로 다시 쓰지 마라. 이유: 공개 데모는 그대로다.
- 기존 테스트를 깨뜨리지 마라.
