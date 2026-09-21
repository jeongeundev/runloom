# Step 3: db-kinds-rules

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/AGENTS.md`
- `/docs/adr/0009-registered-kinds-and-succession-rules.md`, `/docs/adr/0002-server-stack-python-fastapi-sqlite.md`
- `/docs/ARCHITECTURE.md` "업무 종류와 후속 규칙", "최소 데이터 모델과 영속성", "DB 제약과 실행 잠금"
- `/docs/GLOSSARY.md`
- `/src/workflow/contracts/v1.py` — `KindSpec`·`BUILTIN_KINDS`·`SuccessorRule`·`BUILTIN_RULES`·`HandoffBundle`(`inputs`·`source_result_artifact_id`)·`ARTIFACT_KINDS`
- `/src/workflow/adapters/db.py` — `SCHEMA_VERSION`, `_SCHEMA`(`tasks.kind`·`executions.kind` CHECK, `artifacts.kind` CHECK), `init_schema`
- `/src/workflow/adapters/repo.py` — `create_session`, `insert_task`, `tasks_with_completed_predecessor`, `results_awaiting_verdict`, `artifacts_of`, `download_allowed`, 예외 클래스(`AdapterError`, `NotFound`, `DuplicateStartKey` 등)의 정의 방식
- `/tests/workflow/adapters/test_db.py`, `/tests/workflow/adapters/test_repo.py`, `/tests/workflow/adapters/conftest.py`
- `/tests/workflow/server/test_app.py` 의 `SCHEMA_VERSION` 사용

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

저장 계층(`src/workflow/adapters/`)만 바꾼다. `server/`·`domain/`·`connector/` 는 건드리지 않는다 — 단, 이 step 에서 `create_session` 이 내장을 seed 하므로 기존 서버 테스트가 그대로 통과해야 한다.

### `src/workflow/adapters/db.py`

- `SCHEMA_VERSION` 2 → 3. 마이그레이션 없음(기존 방침: 불일치는 RuntimeError, DB 재생성).
- 신규 테이블:
  ```sql
  CREATE TABLE IF NOT EXISTS kinds (
    session_id  TEXT NOT NULL REFERENCES sessions(session_id),
    kind        TEXT NOT NULL,
    spec_json   TEXT NOT NULL,      -- KindSpec JSON. kind 필드는 컬럼과 같다
    created_at  TEXT NOT NULL,
    PRIMARY KEY (session_id, kind)
  );
  CREATE TABLE IF NOT EXISTS succession_rules (
    rule_id     TEXT PRIMARY KEY,
    session_id  TEXT NOT NULL REFERENCES sessions(session_id),
    from_kind   TEXT NOT NULL,
    to_kind     TEXT NOT NULL,
    rule_json   TEXT NOT NULL,      -- SuccessorRule JSON. from_kind·to_kind 는 컬럼과 같다
    created_at  TEXT NOT NULL,
    UNIQUE (session_id, from_kind, to_kind),
    FOREIGN KEY (session_id, from_kind) REFERENCES kinds(session_id, kind),
    FOREIGN KEY (session_id, to_kind)   REFERENCES kinds(session_id, kind)
  );
  ```
- `tasks.kind` 의 `CHECK (kind IN ('diagnosis','code_change'))` 를 지우고 `FOREIGN KEY (session_id, kind) REFERENCES kinds(session_id, kind)` 를 둔다(테이블 정의 순서: `kinds` 가 `tasks` 보다 앞).
- `executions.kind` 의 CHECK 를 지운다(`TEXT NOT NULL`; 값은 Task 에서 복사된다).
- `artifacts.kind` CHECK 는 `ARTIFACT_KINDS` 에서 생성되므로 `generic_result` 가 자동으로 들어간다 — 확인만.

### `src/workflow/adapters/repo.py`

시그니처만 정한다. 구현·SQL 은 기존 스타일(`_tx`, `_one`, `Row`, `_require_rowcount`)을 따른다.

```python
class KindProtected(AdapterError): ...     # 내장 종류 삭제 시도
class KindInUse(AdapterError): ...         # Task 또는 규칙이 참조하는 종류 삭제 시도
class DuplicateKind(AdapterError): ...
class DuplicateRule(AdapterError): ...

def create_session(conn, session_id, now) -> None
    # 기존 INSERT 뒤 같은 트랜잭션에서 BUILTIN_KINDS 2개와 BUILTIN_RULES 1개를 이 세션에 seed 한다
def list_kinds(conn, session_id) -> list[KindSpec]         # 내장 먼저(BUILTIN_KINDS 순), 그 다음 created_at·kind 순
def get_kind(conn, session_id, kind) -> KindSpec | None
def insert_kind(conn, session_id, spec: KindSpec, now) -> None     # 같은 kind 있으면 DuplicateKind. 검증(validate_*)은 서버 몫 — 여기서는 저장만
def delete_kind(conn, session_id, kind) -> None            # builtin → KindProtected; tasks 가 쓰거나 규칙이 참조 → KindInUse; 없으면 NotFound
def list_rules(conn, session_id) -> list[tuple[str, SuccessorRule]]   # (rule_id, rule), created_at·rule_id 순
def get_rule(conn, session_id, from_kind, to_kind) -> SuccessorRule | None
def insert_rule(conn, session_id, rule: SuccessorRule, now) -> str   # rule_id 반환(`rule-` + token_hex(6)); 같은 (from,to) → DuplicateRule; 종류 없음 → NotFound
def delete_rule(conn, session_id, rule_id) -> None         # 없으면 NotFound. 내장 규칙도 삭제 가능(팀이 진단→수정을 잇지 않을 수 있다)
def insert_task(conn, task, now) -> None                   # 기존. 종류가 세션에 없으면 NotFound("종류 … 이 등록되지 않음") — FK IntegrityError 를 기다리지 말고 먼저 get_kind 로 확인
def tasks_with_ready_predecessor(conn) -> list[Row]
    """워커 후속 스캔용. 마감되지 않은 Task 중 선행 Task 가 (a) `완료` 로 마감됐거나 (b) 활성 실행이 `result_ready` 이고
    task_verdicts 에 그 실행의 판정이 있는 것. 선행이 `실패` 로 마감된 Task 는 제외. ORDER BY t.created_at, t.task_id.
    `tasks_with_completed_predecessor` 는 지운다(호출부는 worker 뿐 — step 4 가 바꾼다. 이 step 에서는 이름만 바꾸고 worker 의 호출을 새 이름으로 맞춘다; 조건 (b) 추가로 기존 worker 테스트가 깨지면 안 된다 — 판정 있는 result_ready 선행은 지금도 다음 tick 에 완료되거나 검토 대기이므로 결과가 같아야 한다. 깨지면 이유를 summary 에 적고 worker 쪽 최소 수정을 한다)."""
def predecessor_ready_execution(conn, task_id) -> Row | None
    """선행 Task 를 '준비'시킨 실행 — result_ready 이고 result_artifact_id 가 있으며 판정이 기록된 가장 최근 시도. 없으면 None."""
def results_awaiting_verdict(conn, kind: str | None = None) -> list[Row]   # kind None 이면 모든 종류
def artifacts_of_kinds(conn, execution_id, kinds: Sequence[str]) -> list[Row]  # created_at·artifact_id 순, kind 필터
def download_allowed(conn, store, execution_id, artifact_id) -> bool
    # 기존 조건 + bundle.inputs 의 artifact_id + bundle.source_result_artifact_id 도 허용
```

### 테스트 (먼저 작성)

- `test_db.py`: 새 테이블·FK 존재, `tasks.kind` 에 세션에 없는 종류 삽입이 IntegrityError(외래키 켜짐 확인), `executions.kind` 임의 문자열 허용, `SCHEMA_VERSION == 3` 불일치 RuntimeError 유지.
- `test_repo.py`: `create_session` 이 내장 2종 + 규칙 1개를 seed(다른 세션과 격리); `list_kinds` 순서(내장 먼저); `insert_kind` 왕복(`KindSpec` 동등 비교)·중복; `delete_kind` 내장 보호·사용 중(Task 있음 / 규칙 참조)·성공; `insert_rule`·`get_rule`·`list_rules`·중복·종류 없음·삭제; `insert_task` 종류 없음 NotFound; `tasks_with_ready_predecessor` 세 케이스(선행 완료 / 선행 result_ready + 판정 있음 / 선행 result_ready 판정 없음 → 제외 / 선행 실패 → 제외); `predecessor_ready_execution`; `results_awaiting_verdict(None)`; `artifacts_of_kinds`; `download_allowed` 가 `inputs`·`source_result_artifact_id` 를 허용하고 무관한 산출물은 거부.
- `tests/workflow/server/test_app.py` 의 버전 상수 사용은 그대로 통과해야 한다.

## Acceptance Criteria

```bash
python3 -m pytest tests/workflow/adapters -q
python3 -m pytest -q
python3 -m ruff check .
grep -n "kinds\|succession_rules\|FOREIGN KEY (session_id, kind)" src/workflow/adapters/db.py
grep -n "tasks_with_completed_predecessor" src tests -r --include=*.py | wc -l    # 0
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - DB 는 `adapters/` 에서만 다루는가? 표준 `sqlite3` + 명시적 SQL 인가?
   - 외래키 검사가 켜진 연결에서 테스트하는가? (`PRAGMA foreign_keys`)
   - GLOSSARY 용어 그대로인가? (`KindSpec`, `SuccessorRule`, `kinds`, `succession_rules`)
   - `server/`·`domain/` 변경이 `tasks_with_ready_predecessor` 이름 맞춤 외에 없는가?
3. 결과에 따라 `phases/6-typed-handoff/index.json` 의 해당 step 을 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"` (테이블·함수·예외 이름, worker 호출부 변경 여부)
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- 마이그레이션 코드를 만들지 마라. 이유: 기존 방침(버전 불일치는 오류, DB 재생성). 배포 절차는 step 9 가 문서화한다.
- `kinds`·`succession_rules` 를 전역(세션 무관) 테이블로 만들지 마라. 이유: 팀 전제 — 워크스페이스마다 어휘가 다르다(ADR-0009).
- `insert_kind`·`insert_rule` 에서 `validate_capability`·`validate_rule` 를 부르지 마라. 이유: 저장 계층은 저장만, 검증은 서버가 도메인 함수로 한다(step 6).
- 워커의 후속 착수 논리를 여기서 바꾸지 마라(이름 맞춤만). 이유: step 4.
- 기존 테스트를 깨뜨리지 마라.
