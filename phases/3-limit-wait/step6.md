# Step 6: wait-retry-worker

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/docs/ARCHITECTURE.md` — "상태·재접속·완료", "DB 제약과 실행 잠금", "연결 끊김과 Mac 오프라인", "타임아웃과 폴링"
- `/docs/adr/` (하위 파일 전부) — 특히 `0007-usage-limit-wait-policy.md` 와 `0000-principles.md` ("같은 이벤트가 두 번 와도 실행은 한 번")
- `/docs/GLOSSARY.md` — `Execution`, `start_key`, `TickReport`
- `/src/workflow/server/worker.py` — 모듈 docstring("새 실행을 만드는 곳은 B 생성 스캔 하나"), `tick` 의 단계 순서, `_spawn_successors`(`ExecutionRequest` 조립 → `repo.create_execution` → `DuplicateStartKey`/`ActiveExecutionExists` 처리), `_reflect_failures`, `_refresh_task`, `_write_status`, `TickReport`
- `/src/workflow/adapters/repo.py` — `create_execution`(잠금·start_key 유일성), `executions_by`, `list_executions`, `get_execution`, `finish_task`, `get_agent`, `get_task`
- `/src/workflow/domain/status.py`, `/src/workflow/domain/start_key.py` — Step 5 의 `TaskView` 새 필드·`retry_start_key`
- `/src/workflow/server/settings.py` — Step 5 의 `Limits.usage_limit_*`
- `/src/workflow/server/views.py` — `agent_online`, `build_task_view`
- `/tests/workflow/server/test_worker.py` — `flow`·`worker`·`clock` fixture 와 `test_restart_after_a_completion_creates_b_once`, `test_b_unknown_keeps_lock_and_recovers_on_resend` 의 검증 방식

이전 step에서 만들어진 코드를 꼼꼼히 읽고, 설계 의도를 이해한 뒤 작업하라.

## 배경

지금 `_reflect_failures` 는 `failed` + `process_stopped` 실행을 모두 `실패` 로 마감한다. ADR-0007 에 따라 `failed_code == "usage_limit"` 이고 Agent 정책이 `wait` 이면 마감하지 않고 `대기` 로 두었다가, 리셋 시각이 지나면 새 Execution 을 만든다. 워커 docstring 의 "새 실행을 만드는 곳은 B 생성 스캔 하나" 가 둘이 되므로 docstring 도 고친다.

## 작업

### `src/workflow/server/worker.py`

- `_reflect_failures` 의 `failed and process_stopped` 분기를 나눈다:
  1. `failed_code != "usage_limit"` 또는 Agent 정책 `fail` (또는 Agent 없음) → 기존대로 `실패` 마감.
  2. `usage_limit` + `wait`:
     - `used` = 이 Task 의 `failed_code == "usage_limit"` Execution 수. `used >= limits.usage_limit_max_retries` → `finish_task(status="실패", reason="사용량 한도 · 재시도 {N}회 소진")`.
     - `due` 계산: `failed_retry_after` 가 있으면 그 시각 + 60초, 없으면 `finished_at + usage_limit_default_wait_seconds`.
     - `failed_retry_after - finished_at > usage_limit_max_wait_seconds` → `finish_task(status="실패", reason="사용량 한도 · 리셋 {시각} 까지 {n}시간 — 기다리지 않음")`.
     - `now < due` → `_refresh_task` (Step 5 판정으로 `대기 · 사용량 한도 …`). 마감하지 않고 잠금(활성 실행 없음)도 그대로.
     - `now >= due` → `_retry_after_usage_limit(conn, task, failed_execution, report)`.
- `_retry_after_usage_limit`:
  - 선행 조건: `repo.active_execution(task_id) is None`, Task 미마감, 로컬 Agent 면 `views.agent_online(...)` (오프라인이면 만들지 않고 `_refresh_task` — "대기 · 연결 끊김" 이 우선).
  - 실패한 Execution 의 `request_json` 을 `ExecutionRequest` 로 읽어 `execution_id` 만 새 값(`exec-` + `secrets.token_hex(8)`)으로 바꾼다. `task_revision`·`request`·`input_artifact_ids`·`target`(같은 `base_commit`) 은 그대로 — Task 가 그사이 수정됐으면(`task["revision"] != request.task_revision`) 재시도하지 않고 `실패` 마감 + 이유 "업무가 수정됨".
  - `repo.create_execution(..., attempt_no=마지막+1, start_key=retry_start_key(failed_execution_id), agent_id·assigned_connector_id·predecessor_execution_id 는 실패한 실행과 같게)`.
  - `DuplicateStartKey` → 이미 재시도했음 (같은 실패에서 두 번 만들지 않는다) → 로그만. `ActiveExecutionExists` → 로그만.
  - `report.usage_limit_retries += 1` (`TickReport` 에 카운터 추가), `_refresh_task` → `실행 요청됨 · 접수 대기`.
- 실행 순서: `_reflect_failures` 는 `tick` 의 마지막 단계다. 재시도 생성이 같은 tick 의 앞 단계에 영향을 주지 않도록 그 자리에 둔다.
- 모듈 docstring 을 고친다: 새 실행을 만드는 곳은 "B 생성 스캔"과 "사용량 한도 재시도" 둘이며, 둘 다 `start_key` 유일성으로 한 번만 만든다.

### 테스트 (먼저 작성) — `tests/workflow/server/test_worker.py`

시계(`clock`) 를 움직이며 `worker.tick()` 을 반복하는 기존 방식으로:

1. B 가 `failed(code="usage_limit", retry_after=T, process_stopped=true)` → tick: Task `대기 · 사용량 한도 · T 이후 재시도 (1/6)`, `finished_at` 없음, 새 Execution 없음.
2. 시계를 T+30초로 → tick: 여전히 대기, 새 Execution 없음. T+61초 → tick: Execution 2 (`attempt_no 2`, `start_key == "retry:<exec1>"`, `request_json` 이 `execution_id` 외 동일), Task `실행 요청됨`. 한 번 더 tick → Execution 은 여전히 2개.
3. `retry_after` 없음 → `finished_at + default_wait` 전엔 대기, 지나면 재시도.
4. 재시도도 한도로 실패하는 것을 `max_retries` 번 반복 → 마지막에 `실패 · 사용량 한도 · 재시도 6회 소진`, `finished_at` 기록.
5. `retry_after` 가 `max_wait` 보다 멀면 → 즉시 `실패`, 이유에 리셋 시각.
6. Agent 정책 `fail` → 기존대로 즉시 `실패`.
7. 리셋 시각이 지났지만 연결 프로그램 오프라인 → 재시도 만들지 않고 `대기 · 연결 끊김 …`; 온라인 되면 다음 tick 에 생성.
8. 워커 재시작(새 `Worker` 인스턴스) 뒤에도 같은 실패에서 재시도가 두 번 생기지 않는다 (`start_key`).
9. 재시도 전에 Task revision 이 바뀌면 재시도 없이 `실패 · 업무가 수정됨`.
10. `process_stopped=false` 인 `usage_limit` 은 기존 `확인 필요 · 종료 미확인 — 재실행하지 않음`.

## Acceptance Criteria

```bash
python3 -m pytest tests/workflow/server/test_worker.py -q
python3 -m pytest -q
python3 -m ruff check .
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - ARCHITECTURE.md 디렉토리 구조를 따르는가?
   - ARCHITECTURE.md 외부 의존 표에 없는 외부 서비스를 끌어들이지 않았는가?
   - ADR 기술 스택을 벗어나지 않았는가?
   - AGENTS.md CRITICAL 규칙을 위반하지 않았는가?
   - GLOSSARY.md 용어를 그대로 썼는가? 금지 표현을 쓰지 않았는가?
3. 결과에 따라 `phases/3-limit-wait/index.json`의 해당 step을 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"` (재시도 생성 조건·마감 이유 문구·TickReport 카운터)
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- 실패한 Execution 의 상태를 되돌리거나 같은 `execution_id` 를 다시 claim 시키지 마라. 이유: `failed` 는 최종 상태(ARCHITECTURE "실행 이벤트"). 재시도는 항상 새 Execution 이다.
- `process_stopped` 가 false 이거나 `unknown` 인 실행에서 재시도를 만들지 마라. 이유: 이전 프로세스가 살아 있을 수 있어 중복 실행이 된다(ADR-0000 "시작 여부를 모르면 재실행하지 않는다").
- 다른 Agent 를 골라 재시도하지 마라. 이유: ADR-0007 — `fallback` 은 이번 범위 밖. 재시도 Agent 는 실패한 실행과 같다.
- `time.sleep` 으로 기다리지 마라. 이유: 워커는 tick 마다 DB 를 보고 판단한다. 대기는 상태이지 블로킹이 아니다.
- 진단(A, `kind == "diagnosis"`) 실행에 이 규칙을 적용하지 마라. 이유: ADR-0007 범위 밖. `kind == "code_change"` 만.
- 기존 테스트를 깨뜨리지 마라
