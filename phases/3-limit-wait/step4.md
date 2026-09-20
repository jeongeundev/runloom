# Step 4: retry-after-persist

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/docs/ARCHITECTURE.md` — "실행 이벤트", "최소 데이터 모델과 영속성", "DB 제약과 실행 잠금"
- `/docs/adr/` (하위 파일 전부) — 특히 `0007-usage-limit-wait-policy.md`
- `/docs/GLOSSARY.md`
- `/src/workflow/contracts/v1.py` — `FailedData.retry_after` (Step 0)
- `/src/workflow/adapters/db.py` — Step 2 가 도입한 `_MIGRATIONS`·`SCHEMA_VERSION = 2`, `executions` 테이블(`failed_code`, `failed_message`, `process_stopped`, `finished_at`)
- `/src/workflow/adapters/repo.py` — `append_event` 의 `failed` 분기(`updates["failed_code"] = …`), `get_execution`, `list_executions`, `executions_by`
- `/src/workflow/server/machine_api.py` — 연결 프로그램 이벤트 수신 경로 (`append_event` 호출)
- `/tests/workflow/adapters/test_db.py`, `test_repo.py`, `/tests/workflow/server/test_machine_api.py`

이전 step에서 만들어진 코드를 꼼꼼히 읽고, 설계 의도를 이해한 뒤 작업하라.

## 배경

연결 프로그램이 `failed` 이벤트에 `retry_after` 를 실어 보낸다(Step 1). 서버가 이를 Execution 행에 보존해야 Step 5(상태 판정)·Step 6(재시도 생성) 이 읽는다. 시각은 계약 규칙대로 시간대 있는 RFC 3339 로 받아 DB 에는 UTC 로 정규화한다.

## 작업

### `src/workflow/adapters/db.py`

- `executions` 에 `failed_retry_after TEXT` (NULL 허용, UTC RFC 3339) 를 추가한다. 새 DB 용 `_SCHEMA` 와 `_MIGRATIONS[3]` 둘 다. `SCHEMA_VERSION = 3`.

### `src/workflow/adapters/repo.py`

- `append_event` 의 `failed` 분기가 `event.data.retry_after` 를 UTC 로 정규화해 `failed_retry_after` 에 저장한다. `None` 이면 NULL. 정규화는 이미 다른 시각 필드에 쓰는 방식이 있으면 그대로 쓴다.
- 조회 함수는 `SELECT *` 라 행에 자동 포함된다. 별도 getter 를 만들지 마라.

### 테스트 (먼저 작성)

- `tests/workflow/adapters/test_db.py`: version 2 DB → `init_schema` 후 컬럼 존재·version 3; 새 DB 도 version 3; version 1 → 3 두 단계가 한 번에 적용된다.
- `tests/workflow/adapters/test_repo.py`: `failed` 이벤트에 `retry_after="2026-09-20T21:16:34+09:00"` → 행의 `failed_retry_after == "2026-09-20T12:16:34+00:00"`(또는 프로젝트의 UTC 표기 관례); 없으면 NULL; `failed_code`·`failed_message`·`process_stopped` 는 기존과 같음.
- `tests/workflow/server/test_machine_api.py`: 연결 프로그램 경로로 `failed` 이벤트를 보내면 행에 저장된다 (기존 이벤트 테스트에 사례 1개 추가).

## Acceptance Criteria

```bash
python3 -m pytest tests/workflow/adapters tests/workflow/server -q
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
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"` (컬럼·버전·정규화 규칙)
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- 상태 전이표(`domain/status.py` `_TRANSITIONS`)·워커를 고치지 마라. 이유: Step 5·6 이다. 이 step 은 저장만 한다.
- `failed` 이벤트를 받았을 때 Task 상태를 바꾸지 마라. 이유: Task 사용자 상태는 워커의 `_reflect_failures` 가 판정한다(Step 6).
- 기존 테스트를 깨뜨리지 마라
