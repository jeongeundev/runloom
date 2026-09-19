# Step 2: domain-rules

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/docs/PRD.md` — "2. 업무 등록과 연결"(자동 선택 판단 규칙, 기본값), "3. 실행과 관찰"(사용자 상태 대응표), "4. 완료 처리와 후속 업무 시작"(완료 기준 템플릿, 검토 동작)
- `/docs/ARCHITECTURE.md` — "실행 이벤트"(정상 전이), "DB 제약과 실행 잠금"(`start_key` 규칙)
- `/docs/adr/0004-central-service-rule-based-no-llm.md`
- `/docs/GLOSSARY.md` — 사용자 상태 7개의 한글 문구
- `/src/workflow/contracts/v1.py` (Step 1) — `Capability`, `SelectionRecord`, `ExecutionEvent` 를 그대로 사용한다

## 작업

중앙 서비스의 판단 규칙을 `src/workflow/domain/` 에 순수 함수로 만든다. I/O 가 없다: DB·HTTP·시각 조회·프로세스를 호출하지 않고 인자로 받은 값만 본다. 이 모듈들은 Step 4~8 이 호출한다.

### `src/workflow/domain/selection.py`

```python
@dataclass(frozen=True)
class Candidate:
    agent_id: str
    capabilities: tuple[Capability, ...]
    allowed: bool = True          # 세션에 사용 허용된 에이전트인지. False 면 후보에서 제외

def select_agent(task_id: str, required: Capability, candidates: Sequence[Candidate],
                 mode: Literal["auto", "manual"] = "auto",
                 chosen_agent_id: str | None = None) -> SelectionRecord
```

규칙 (PRD 2절 "첫 구현의 판단 규칙" 그대로):

- 일치 = `code` 가 같고 `scope` 의 모든 키·값이 같다. 부분 일치 없음.
- `auto`: 허용된 후보 중 일치 후보를 센다. 1개 → `status="selected"`, `matched` 에 일치한 capability, `reason` 은 CONTRACT 9절 형식 `"{code} · {scope_key}={scope_value} 일치 후보 1개"`. 0개 → `needs_selection`, reason `"후보 없음"`. 2개 이상 → `needs_selection`, reason `"후보 {N}개 — 선택 필요"`.
- `manual`: `chosen_agent_id` 가 허용 후보이고 일치하면 `selected`, reason `"직접 선택"`. 허용되지 않았거나 일치하지 않으면 `needs_selection` 과 이유(`"선택한 에이전트는 사용 허용되지 않음"` / `"선택한 에이전트에 {code} 능력 없음"`).
- 우선순위·최근 사용·이름으로 후보를 줄이지 않는다.

### `src/workflow/domain/status.py`

```python
EXECUTION_STATUSES = ("queued", "accepted", "running", "result_ready", "failed", "unknown")
TERMINAL_STATUSES = ("result_ready", "failed")

class InvalidTransition(Exception):
    def __init__(self, current: str, event_type: str): ...

def next_execution_status(current: str, event_type: str) -> str
```

전이 (ARCHITECTURE "실행 이벤트"): `queued --accepted--> accepted --started--> running --result_ready--> result_ready`. `accepted --failed--> failed`, `running --failed--> failed`. `progress` 는 `running` 에서만 허용하고 상태를 바꾸지 않는다. 최종 상태 뒤 어떤 이벤트도 `InvalidTransition`. `unknown` 은 이벤트로 들어가지 않는다 (서버 관찰로만 설정). `unknown` 에서 `started`/`result_ready`/`failed` 는 허용한다 — 기존 실행 주체의 누락 없는 재전송으로 복원하는 경로다.

```python
USER_STATUS_LABELS = ("대기", "실행 가능", "실행 요청됨", "실행 중", "확인 필요", "완료", "실패")

@dataclass(frozen=True)
class TaskView:
    kind: Literal["diagnosis", "code_change"]
    run_mode: Literal["manual", "auto"]
    completion_mode: Literal["auto", "review"]
    selection_status: Literal["selected", "needs_selection"]
    selection_reason: str
    predecessor_status: str | None          # 선행 Task 의 사용자 상태. 없으면 None
    connector_online: bool | None           # code_change 만 의미. 진단은 None
    connector_last_seen: str | None
    execution_status: str | None            # 활성 Execution 상태. 없으면 None
    last_progress: str | None
    failed_code: str | None
    failed_message: str | None
    process_stopped: bool | None
    verdict: Literal["passed", "failed", "undecidable"] | None   # 자동 완료 판정
    verdict_detail: str | None
    review_decision: Literal["approve", "request_changes", "close"] | None
    finished: bool                          # Task 가 완료 또는 실패로 마감됐는지

@dataclass(frozen=True)
class UserStatus:
    label: str      # USER_STATUS_LABELS 중 하나
    reason: str     # 화면에 그대로 보일 이유 문구

def user_status(view: TaskView) -> UserStatus
```

PRD 3절 대응표를 행 순서대로 구현한다. 이유 문구는 표의 "표시할 이유·동작" 열을 따르고 UI_GUIDE 의 예시 문구와 맞춘다: `"선행 대기"`, `"연결 끊김, 마지막 확인 {시각}"`, `"후보 없음"`, `"후보 N개 — 선택 필요"`, `"{agent} 선택됨"`… 정확한 문자열은 테스트에서 고정한다. `finished` 이고 review_decision 이 approve 면 `완료`/`"검토 승인"`, close 면 `실패`/`"검토 거절"`.

### `src/workflow/domain/completion.py`

```python
@dataclass(frozen=True)
class Criterion:
    code: str                 # 예: "diagnosis.ready_for_handoff", "diagnosis.verified", "code_change.verification_passed", "code_change.result_preserved"
    text: str                 # 화면 문구
    structured: bool          # True 면 자동 판정에 사용, False 면 검토자에게만 표시

def criteria_template(kind: Literal["diagnosis", "code_change"]) -> list[Criterion]
def merge_criteria(template: list[Criterion], user_items: list[str]) -> list[Criterion]   # 사용자 자유 텍스트는 structured=False, code="user.{n}"
def can_auto_complete(kind: str) -> bool     # diagnosis 만 True (PRD: 자동 판정기가 있는 종류만)
```

템플릿 문구는 PRD 4절 그대로: 진단 "결과가 `ready_for_handoff`이고 근거 검증을 통과함", 코드 수정 "등록된 검증 프로필이 결과 커밋에서 통과하고 결과가 보존됨".

### `src/workflow/domain/defaults.py`

```python
def default_run_mode(has_predecessor: bool) -> Literal["manual", "auto"]      # 선행 있으면 auto
def default_selection_mode() -> Literal["auto"]
def default_completion_mode(kind: str) -> Literal["review"]                   # 항상 review. 예시 A 의 auto 는 폼 미리 채움(Step 6)에서만
def kind_for_capability(code: str) -> Literal["diagnosis", "code_change"]     # operations.diagnose → diagnosis, code.modify → code_change
```

### `src/workflow/domain/start_key.py`

```python
def auto_start_key(task_id: str, task_revision: int) -> str      # "auto:{task_id}:r{revision}" — 결정적. 같은 revision 이면 항상 같은 값
def request_start_key(request_id: str) -> str                    # "req:{request_id}"
```

ARCHITECTURE: 최초 자동 실행은 revision 에 연결된 서버 생성 키, 직접 실행·명시적 재시도는 요청 ID. 실패 후 자동 평가가 반복돼도 새 키를 만들지 않는다 — 그래서 `auto_start_key` 는 결정적이어야 한다.

### 테스트

`tests/workflow/domain/test_selection.py`, `test_status.py`, `test_completion.py`, `test_defaults.py`, `test_start_key.py`. 먼저 작성해 실패를 확인한 뒤 구현한다.

- selection: 후보 0/1/2개, 허용 안 된 후보는 제외, scope 부분 일치는 불일치, manual 의 세 경우, reason 문자열이 CONTRACT 9절과 같음.
- status: 정상 전이 전부, 최종 상태 뒤 `started` 거부, `queued` 에서 `progress` 거부, `unknown` 복원 경로. `user_status` 는 PRD 3절 표의 10행을 각각 하나의 테스트로.
- completion: 템플릿 문구, 사용자 항목은 structured=False, `can_auto_complete("code_change") is False`.
- start_key: 결정적, 두 함수의 접두사가 다름.
- `tests/test_packages.py` 의 import 금지 검사가 여전히 통과해야 한다.

### GLOSSARY

`docs/GLOSSARY.md` 용어 표에 `Candidate`(선택 후보. Agent 의 ID·능력·허용 여부만 가진 도메인 값), `TaskView`(사용자 상태 판정에 필요한 Task·Execution·연결 스냅샷), `Criterion`(완료 기준 항목 하나) 세 줄을 추가한다. 금지 표현 열도 채운다 (`Option`, `Snapshot`, `Rule`).

## Acceptance Criteria

```bash
python3 -m pytest tests/workflow/domain -q
python3 -m pytest -q
python3 -m ruff check .
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트:
   - `src/workflow/domain/` 이 `fastapi`, `sqlite3`, `httpx`, `subprocess`, `datetime.now` 를 호출하지 않는가?
   - 자동 선택에 모델 호출·우선순위·최근 사용이 없는가? (ADR-0004)
   - 사용자 상태 문구가 GLOSSARY 의 7개 그대로인가? `대기 중`, `pending` 같은 금지 표현이 없는가?
3. `phases/0-mvp/index.json` 의 step 2 를 업데이트한다 (summary 에 모듈 5개 이름과 공개 함수 이름).

## 금지사항

- 범용 선택 알고리즘(점수, 가중치, 유사도)을 만들지 마라. 이유: ADR-0004 는 능력 코드·범위의 명시적 비교만 허용한다.
- 시각을 도메인 안에서 조회하지 마라. 이유: 순수 함수여야 테스트가 결정적이다. 시각은 문자열 인자로 받는다.
- `TaskView` 에 DB 행이나 Pydantic 모델을 그대로 넣지 마라. 이유: 도메인은 저장 형식을 모른다.
- 기존 테스트를 깨뜨리지 마라.
