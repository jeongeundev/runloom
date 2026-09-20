# Step 5: wait-status

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/docs/ARCHITECTURE.md` — "상태·재접속·완료", "DB 제약과 실행 잠금", "타임아웃과 폴링"
- `/docs/PRD.md` — 3절 "실행과 관찰" 의 사용자 상태 표 (`대기`, `확인 필요`, `실패` 행)
- `/docs/adr/` (하위 파일 전부) — 특히 `0007-usage-limit-wait-policy.md`
- `/docs/GLOSSARY.md` — `TaskView`, `start_key`, `Execution`
- `/src/workflow/domain/status.py` — `TaskView`, `user_status`(PRD 3절 표를 행 순서대로 적용), `failed_code`·`process_stopped`·`finished` 분기
- `/src/workflow/domain/start_key.py` — `auto_start_key`, `request_start_key` 와 모듈 docstring
- `/src/workflow/server/settings.py` — `Limits`, `WORKFLOW_LIMIT_*` 환경변수 읽기
- `/src/workflow/server/views.py` — `build_task_view` (DB 행 → `TaskView`)
- `/tests/workflow/domain/test_status.py`, `test_start_key.py`, `/tests/workflow/server/test_settings.py`, `test_views.py`

이전 step에서 만들어진 코드를 꼼꼼히 읽고, 설계 의도를 이해한 뒤 작업하라.

## 배경

ADR-0007: 사용량 한도 실패는 정책이 `wait` 이고 재시도가 남아 있으면 `실패` 가 아니라 `대기` 다. 판정은 도메인(`status.py`)이 스냅샷만 보고 하고, 스냅샷은 `views.build_task_view` 가 만든다. 재시도 Execution 의 중복 방지 키와 상한 설정도 이 step 에서 정의한다. 워커의 실제 재시도 생성은 Step 6 이다.

## 작업

### `src/workflow/server/settings.py`

- `Limits` 에 추가 (환경변수 이름은 기존 규칙 `WORKFLOW_LIMIT_*`):
  ```python
  usage_limit_max_retries: int = 6            # WORKFLOW_LIMIT_USAGE_LIMIT_MAX_RETRIES
  usage_limit_default_wait_seconds: int = 3600  # WORKFLOW_LIMIT_USAGE_LIMIT_DEFAULT_WAIT_SECONDS — 리셋 시각을 모를 때
  usage_limit_max_wait_seconds: int = 21600    # WORKFLOW_LIMIT_USAGE_LIMIT_MAX_WAIT_SECONDS — 이보다 먼 리셋(7일 창)은 기다리지 않음
  ```

### `src/workflow/domain/start_key.py`

- `retry_start_key(failed_execution_id: str) -> str` → `f"retry:{failed_execution_id}"`. docstring 에 "사용량 한도 재시도 — 실패 1건당 1건" 을 적는다.

### `src/workflow/domain/status.py`

- `TaskView` 에 필드 추가 (모두 스냅샷 값, DB·시각 계산 없음):
  ```python
  on_usage_limit: Literal["wait", "fail"] | None  # 선택된 Agent 의 정책. Agent 없으면 None
  retry_after: str | None                          # 활성(마지막) Execution 의 failed_retry_after
  usage_limit_retries_used: int                    # 이 Task 에서 사용량 한도로 실패한 Execution 수
  usage_limit_max_retries: int
  ```
- `user_status` 에 분기 추가 — 위치는 `execution_status == "failed" and process_stopped` 판정 **앞**:
  `failed_code == "usage_limit"` 이고 `process_stopped` 이고 `on_usage_limit == "wait"` 이고 `usage_limit_retries_used < usage_limit_max_retries` 이고 `not finished` 이면
  `UserStatus("대기", f"사용량 한도 · {retry_after} 이후 재시도 ({usage_limit_retries_used}/{usage_limit_max_retries})")`,
  `retry_after` 가 없으면 `"사용량 한도 · 리셋 시각 미확인, 잠시 뒤 재시도 (n/N)"`.
  - 그 외(정책 `fail`, 재시도 소진, Task 마감) 는 기존대로 `실패 · usage_limit · {message}` 다. 마감 이유 문구(재시도 소진·리셋 너무 멂)는 워커가 `finish_task` 에 적으므로 여기서 만들지 않는다.
  - `process_stopped` 가 아니면 기존 "종료 미확인 — 재실행하지 않음" 그대로 — 한도라도 종료 미확인이면 재시도하지 않는다.

### `src/workflow/server/views.py`

- `build_task_view` 가 새 필드를 채운다: 선택된 Agent 행의 `on_usage_limit`, 마지막 Execution 의 `failed_retry_after`, `list_executions` 중 `failed_code == "usage_limit"` 인 행 수, `settings.limits.usage_limit_max_retries`.
- `retry_after` 는 화면 문구에 그대로 들어가므로 저장된 UTC 값을 기존 시각 표시 관례(있으면 KST 변환 필터)에 맞춰 넘긴다. 관례가 없으면 UTC 그대로 둔다.

### 테스트 (먼저 작성)

- `tests/workflow/domain/test_status.py`: (1) 한도+wait+재시도 남음 → `대기` 와 시각·횟수 문구; (2) 시각 없음 문구; (3) 정책 `fail` → `실패`; (4) 재시도 소진(6/6) → `실패`; (5) `process_stopped=False` → `확인 필요` 기존 문구; (6) `finished=True` → 대기가 아님; (7) 다른 `failed_code` 는 영향 없음.
- `tests/workflow/domain/test_start_key.py`: `retry_start_key` 형식, 실행 ID 가 다르면 키가 다름, `auto_start_key` 와 겹치지 않음.
- `tests/workflow/server/test_settings.py`: 기본값 세 개, 환경변수 덮어쓰기, 정수 아님 → `ValueError`.
- `tests/workflow/server/test_views.py`: DB 에 한도 실패 Execution 2건 + Agent 정책 `wait` 를 넣고 `build_task_view` 의 새 필드 값 확인.

## Acceptance Criteria

```bash
python3 -m pytest tests/workflow/domain tests/workflow/server -q
python3 -m pytest -q
python3 -m ruff check .
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - ARCHITECTURE.md 디렉토리 구조를 따르는가?
   - ARCHITECTURE.md 외부 의존 표에 없는 외부 서비스를 끌어들이지 않았는가?
   - ADR 기술 스택을 벗어나지 않았는가?
   - AGENTS.md CRITICAL 규칙을 위반하지 않았는가? (`domain/` 은 FastAPI·sqlite3·HTTPX·subprocess·Git 을 import 하지 않는다)
   - GLOSSARY.md 용어를 그대로 썼는가? 금지 표현을 쓰지 않았는가?
3. 결과에 따라 `phases/3-limit-wait/index.json`의 해당 step을 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"` (TaskView 새 필드·문구·설정 이름·키 형식)
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- `domain/status.py` 에서 현재 시각을 읽거나 "리셋 시각이 지났는지" 를 계산하지 마라. 이유: 도메인은 스냅샷만 판정한다(모듈 docstring). 시각 비교는 워커(Step 6)가 한다.
- `_TRANSITIONS`(Execution 상태 전이표)를 바꾸지 마라. 이유: `failed` 는 최종 상태이며 되살리지 않는다. 재시도는 새 Execution 이다.
- 워커(`server/worker.py`)를 고치지 마라. 이유: Step 6 이다.
- `USER_STATUS_LABELS` 에 새 라벨을 추가하지 마라. 이유: PRD 3절 표의 `대기` 를 이유 문구로 구분한다.
- 기존 테스트를 깨뜨리지 마라
