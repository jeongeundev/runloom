# Step 4: worker-succession

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/AGENTS.md`
- `/docs/adr/0009-registered-kinds-and-succession-rules.md`, `/docs/adr/0004-central-service-rule-based-no-llm.md`
- `/docs/ARCHITECTURE.md` "업무 종류와 후속 규칙", "DB 제약과 실행 잠금", "상태·재접속·완료", "진단 완료 검증"
- `/docs/CONTRACT.md` 2절(인계 묶음 예시)·7절·11절, `/docs/GLOSSARY.md`
- `/src/workflow/contracts/v1.py` — `HandoffBundle`·`InputRef`·`GenericResult`·`LocalTarget`·`ExecutionRequest.kind_spec`·`BUILTIN_KIND_NAMES`
- `/src/workflow/domain/succession.py` (`rule_for`, `may_continue`, `continue_reason`), `/src/workflow/domain/kinds.py`
- `/src/workflow/adapters/repo.py` — step 3 이 만든 `get_kind`·`get_rule`·`tasks_with_ready_predecessor`·`predecessor_ready_execution`·`results_awaiting_verdict(kind=None)`·`artifacts_of_kinds`·`record_verdict`·`store_artifact`·`read_artifact`
- `/src/workflow/server/worker.py` 전부 — 특히 `assemble_handoff`, `_expected_from_result`, `_store`, `_read_owned`, `Worker.tick`, `_spawn_successors`, `_completed_execution`, `_judge_diagnoses`, `_check_code_results`, `_reflect_failures`
- `/src/workflow/server/views.py` 의 `agent_online`, `/src/workflow/server/web.py` 의 `_handoff_inputs`·`_start_execution`(워커와 같은 요청 구성 — 이 step 은 워커만)
- `/tests/workflow/server/test_worker.py`, `/tests/workflow/server/conftest.py`

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

`src/workflow/server/worker.py` 만 바꾼다. 웹 경로(`web.py`)의 직접 실행·검토는 step 7.

### 1. 인계 조립을 규칙 기반으로 — `assemble_handoff`

```python
def assemble_handoff(conn, store, source_execution: Row, successor_task: Row, rule: SuccessorRule, now: str) -> str
    """규칙 handoff_kinds 로 선행 실행의 산출물을 모아 handoff_bundle 을 successor 세션 소유로 저장. 이미 있으면 그 ID."""
```

- `source_kind = source_execution["kind"]`, `source_result_artifact_id = source_execution["result_artifact_id"]`.
- `attachments`: `source_kind == "diagnosis"` 일 때만 — 기존 논리(`DiagnosisResult.attachments` + `expected_report.json` 을 `evidence` 산출물로 저장해 추가)를 `_diagnosis_attachments(conn, store, source_execution, successor_task, now) -> list[AttachmentRef]` 로 분리해 호출. 그 외 종류는 `[]`.
- `inputs`: `repo.artifacts_of_kinds(conn, source_execution_id, rule.handoff_kinds)` 의 각 행을 `InputRef(kind, artifact_id, sha256, content_type)` 로 — 단 `attachments` 에 이미 있는 `artifact_id` 와 `handoff_bundle` kind 는 제외. 같은 kind 가 여럿이면 전부 넣는다(순서는 저장 순).
- 멱등: 같은 선행 실행에 `handoff_bundle` 이 이미 있으면 새로 만들지 않는다(기존과 같음).

### 2. 후속 착수 조건 — `_spawn_successors`

기존 `task["kind"] != "code_change"` 가드와 `_completed_execution` 을 지우고 아래 순서로:

1. `for task in repo.tasks_with_ready_predecessor(conn)` — 활성 실행 있으면 skip(기존).
2. `selection` 이 `selected` 가 아니면 skip(기존).
3. `source = repo.predecessor_ready_execution(conn, task["predecessor_task_id"])`; None 이면 `_refresh_task` 후 continue.
4. `rule = repo.get_rule(conn, task["session_id"], source["kind"], task["kind"])`; None 이면 `_write_status(conn, task, "대기", f"후속 규칙 없음: {source['kind']} → {task['kind']} — 규칙을 등록하거나 직접 실행")` 후 continue. (`_write_status` 는 상태가 바뀔 때만 기록한다 — 기존 헬퍼.)
5. `verdict = repo.get_verdict(conn, source["execution_id"])`; 없거나 `outcome != "passed"` 면 `_refresh_task` 후 continue (판정 실패·보류인 선행은 사람이 본다).
6. `outcome = _result_outcome(conn, store, source)` — 결과 산출물 JSON 의 최상위 `"outcome"` 문자열(세 결과 봉투 모두 가진다). 읽지 못하면 None → `_refresh_task`, continue.
7. `may_continue(rule, outcome)` 이 False 면 `_write_status(conn, task, "확인 필요", continue_reason(rule, outcome))` 후 continue.
8. 에이전트: `repo.get_agent`; `connection_type == "api"` 면 기존처럼 자동 착수하지 않고 `_refresh_task` (진단 API 는 이번에도 웹 경로에서만 시작 — 상한·usage 기록이 거기 있다). 로컬이면 `agent_online` 확인(기존).
9. `run_mode == "manual"` 이면 `assemble_handoff` 만 하고 `_refresh_task` (실행 가능 — 기존).
10. 자동이면 `bundle_id = assemble_handoff(...)` 후 `ExecutionRequest` 구성:
    - `kind = task["kind"]`, `kind_spec = repo.get_kind(conn, task["session_id"], kind)` (None 이면 warning + `_refresh_task`, continue),
    - `target = json.loads(task["target_json"])` (code_change 는 기존 3 필드, 그 외는 `{"local_registration_id": …}` — Task 등록이 채운다, step 7),
    - `input_artifact_ids = [bundle_id]`, `predecessor_execution_id = source["execution_id"]`, `start_key = auto_start_key(task_id, revision)` (중복 방지 기존 그대로).
11. `DuplicateStartKey`·`ActiveExecutionExists` 처리 기존 유지. `report.successors_created += 1`.

`_refresh_task` 가 만드는 사용자 상태는 `domain/status.py` 가 정한다 — 후속 대기 문구가 "선행 완료 대기" 였다면 "선행 결과 대기" 로 바꾸되 상태 라벨 집합은 바꾸지 않는다.

### 3. 사용자 정의 종류의 결과 판정 — `_check_generic_results`

```python
def _check_generic_results(self, conn, report) -> None
    """result_ready 이고 판정 없는 실행 중 kind ∉ BUILTIN_KIND_NAMES. GenericResult 봉투를 검사해 판정을 기록한다.
    checks: envelope_valid(파싱), ids_match(execution_id·task_id·kind 일치), outcome_in_spec(kind_spec.outcomes 안).
    모두 통과 → verdict passed, 상태 '확인 필요', 이유 '검토 대기' (완료는 사람). 하나라도 실패 → verdict failed, 상태 '확인 필요', 이유는 실패 항목 detail."""
```

`Worker.tick` 순서: `… _judge_diagnoses → _check_code_results → _check_generic_results → _spawn_successors → _reflect_failures`. **`_spawn_successors` 를 판정 두 개 뒤로 옮긴다** — 같은 tick 에 판정이 나면 바로 이을 수 있다. `TickReport` 에 `generic_checked: int` 추가.

### 4. 정리

- `_expected_from_result`·`_store`·`_read_owned` 는 유지(진단 첨부 분리 함수가 쓴다).
- `worker.py` 안에 `"code_change"`·`"diagnosis"` 문자열 비교가 남는 곳은 (a) `_submit_diagnoses`·`_poll_diagnoses`·`_judge_diagnoses` 의 `kind="diagnosis"` 조회, (b) `_check_code_results` 의 `kind="code_change"`, (c) `_diagnosis_attachments` 의 분기 — 세 곳뿐이어야 한다. 그 외의 종류 이름 비교를 없앤다.

### 테스트 (먼저 작성) — `tests/workflow/server/test_worker.py`

기존 진단→수정 케이스는 그대로 통과해야 한다(내장 규칙이 seed 되므로). 추가:

- `assemble_handoff`: 규칙 `code_change → review`(handoff `diff`, `code_change_result`, `test_log_after`) 로 B 실행의 산출물 3개가 `inputs` 에 들어가고 `attachments == []`, `source_kind == "code_change"`; 진단 규칙에서는 `inputs` 에 `diagnosis_result` 가 있고 `attachments` 에 근거 + `expected_report.json` 이 있다(기존 동작); 두 번 불러도 같은 ID.
- `_spawn_successors`: (a) 선행 `result_ready` + 판정 passed + outcome 일치 → 후속 실행 생성(선행 Task 는 `확인 필요` 그대로 — 사람 승인 전 착수 확인); (b) 판정 없음 → 생성 안 함; (c) outcome 이 규칙 밖(`needs_information`) → 상태 `확인 필요` 이유 문구; (d) 규칙 없음 → `대기` 이유 "후속 규칙 없음"; (e) 선행 `실패` 마감 → 생성 안 함; (f) 두 tick 에 실행 하나(start_key); (g) 사용자 정의 종류 후속 요청에 `kind_spec` 와 `LocalTarget` 이 들어감.
- `_check_generic_results`: 정상 봉투 → passed/확인 필요/검토 대기; `outcome` 이 spec 밖 → failed + detail; `kind` 불일치 → failed; 내장 종류 실행은 이 함수가 건드리지 않음.
- `tick` 순서: 같은 tick 에서 B 판정 → C 착수가 일어남(`successors_created == 1`).

## Acceptance Criteria

```bash
python3 -m pytest tests/workflow/server/test_worker.py -q
python3 -m pytest -q
python3 -m ruff check .
grep -n '"code_change"\|"diagnosis"' src/workflow/server/worker.py     # 위 "정리" 의 세 곳(조회 kind 인자·첨부 분기)만
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - 모델의 "완료했다" 응답·프로세스 종료 코드만으로 완료 처리하지 않는가? (사용자 정의 종류는 항상 사람 검토)
   - 중복 착수가 `start_key`·활성 잠금으로 막히는가? (ARCHITECTURE "DB 제약과 실행 잠금")
   - 워커가 LLM 을 부르지 않는가? (ADR-0004)
   - 후속 착수 조건이 ADR-0009 (3) 과 같은가?
   - GLOSSARY 용어 그대로인가? (`outcome` ≠ 상태, `Verdict`, `SuccessorRule`)
3. 결과에 따라 `phases/6-typed-handoff/index.json` 의 해당 step 을 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"` (바뀐 함수·tick 순서·새 이유 문구)
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- `web.py`·`views.py`·`connector/` 를 바꾸지 마라. 이유: 이 step 은 워커만. 직접 실행 경로는 step 7, 연결 프로그램은 step 5.
- 진단 API 에이전트(`connection_type == "api"`)의 후속 자동 착수를 이 step 에서 열지 마라. 이유: 상한·usage 기록이 웹 경로에만 있다(ADR-0009 한계).
- 사람이 선행을 `close` 한 뒤에도 이미 시작한 후속을 취소하지 마라. 이유: ADR-0009 트레이드오프 — 취소 정책은 별도 결정.
- 규칙이 없을 때 "기본 규칙"을 추측하지 마라. 이유: 규칙에 없는 결과는 사람에게 간다.
- 기존 테스트를 깨뜨리지 마라.
