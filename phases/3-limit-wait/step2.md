# Step 2: agent-limit-policy

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/docs/ARCHITECTURE.md` — "등록·선택·권한", "최소 데이터 모델과 영속성", "DB 제약과 실행 잠금", "재시작·백업·보존"
- `/docs/adr/` (하위 파일 전부) — 특히 `0007-usage-limit-wait-policy.md`
- `/docs/GLOSSARY.md`
- `/docs/UI_GUIDE.md` — Agent 상세 화면 항목
- `/src/workflow/adapters/db.py` — `_SCHEMA`(`agents` 테이블), `SCHEMA_VERSION = 1`, `init_schema`("다른 버전이 기록돼 있으면 마이그레이션 도구가 없으므로 실패시킨다")
- `/src/workflow/adapters/repo.py` — `upsert_agent`, `get_agent`
- `/src/workflow/server/machine_api.py` — `RegistrationRequest`, `register` 핸들러 (등록 요청 → `upsert_agent`)
- `/src/workflow/server/views.py` — `agent_public`
- `/src/workflow/server/templates/agent_detail.html`, `agents.html`
- `/tests/workflow/adapters/test_db.py`, `test_repo.py`, `/tests/workflow/server/test_machine_api.py`, `test_views.py`, `test_web.py`

이전 step에서 만들어진 코드를 꼼꼼히 읽고, 설계 의도를 이해한 뒤 작업하라.

## 배경

ADR-0007 의 정책 `on_usage_limit` 은 Agent 에 속한다(구독 한도는 Agent 소유). 서버가 이 값을 저장·표시해야 워커(Step 6)가 대기·마감을 결정한다. 배포된 중앙 DB 는 심사 기간 데이터를 보존하므로(ARCHITECTURE "재시작·백업·보존") 컬럼 추가에는 마이그레이션이 필요하다. 지금 `init_schema` 는 버전이 다르면 실패만 한다.

## 작업

### `src/workflow/adapters/db.py` — 마이그레이션 도입 + 컬럼

- `_SCHEMA` 의 `agents` 에 `on_usage_limit TEXT NOT NULL DEFAULT 'wait' CHECK (on_usage_limit IN ('wait', 'fail'))` 를 추가한다 (새 DB 용).
- `SCHEMA_VERSION = 2`. 버전 단계 마이그레이션을 도입한다. 예:
  ```python
  _MIGRATIONS: dict[int, tuple[str, ...]] = {
      2: ("ALTER TABLE agents ADD COLUMN on_usage_limit TEXT NOT NULL DEFAULT 'wait' CHECK (on_usage_limit IN ('wait', 'fail'))",),
  }
  ```
  `init_schema`: 기록된 버전이 현재보다 **낮으면** 그 다음 버전부터 순서대로 적용하고 버전을 갱신한다(한 트랜잭션). **높으면** 지금처럼 실패한다. 멱등 — 두 번 불러도 같은 결과.
- `fallback` 값은 예약만 한다 — CHECK 에 넣지 마라. 이유: 구현이 없는 값을 저장 가능하게 두면 화면·워커가 모르는 상태를 만든다.

### `src/workflow/adapters/repo.py`

- `upsert_agent` 가 `on_usage_limit` 을 저장한다. 키가 없으면 `'wait'`. 기존 행을 갱신할 때 값이 주어지지 않으면 기존 값을 유지한다(등록 재보고로 정책이 초기화되지 않게).

### `src/workflow/server/machine_api.py`

- `RegistrationRequest.on_usage_limit: Literal["wait", "fail"] = "wait"` (선택). 그 외 값은 422.
- `register` 가 이 값을 `upsert_agent` 로 넘긴다.

### `src/workflow/server/views.py`, 템플릿

- `agent_public` 에 `on_usage_limit` 노출. Agent 상세 화면에 "사용량 한도 시: 리셋까지 대기 / 실패 처리" 한 줄. API Agent(`connection_type == "api"`) 에는 표시하지 않는다.
- `UI_GUIDE.md` 에 항목 한 줄 추가.

### 테스트 (먼저 작성)

- `tests/workflow/adapters/test_db.py`: (1) 새 DB 는 version 2 이고 `agents.on_usage_limit` 기본 `'wait'`; (2) version 1 스키마로 만든 DB(컬럼 없음, 행 1개) 에 `init_schema` 를 부르면 컬럼이 생기고 기존 행은 `'wait'`, version 2; (3) 두 번 불러도 같음; (4) version 3 이 기록된 DB 는 여전히 `RuntimeError`.
- `tests/workflow/adapters/test_repo.py`: `upsert_agent` 저장·기본값·갱신 시 유지.
- `tests/workflow/server/test_machine_api.py`: 필드 없는 등록 → `'wait'`; `"fail"` → 저장; `"fallback"` → 422.
- `tests/workflow/server/test_views.py` 또는 `test_web.py`: 로컬 Agent 상세에 문구 표시, API Agent 에는 없음.

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
   - ADR 기술 스택을 벗어나지 않았는가? (표준 `sqlite3` + 명시적 SQL, 마이그레이션 라이브러리 없음)
   - AGENTS.md CRITICAL 규칙을 위반하지 않았는가?
   - GLOSSARY.md 용어를 그대로 썼는가? 금지 표현을 쓰지 않았는가?
3. 결과에 따라 `phases/3-limit-wait/index.json`의 해당 step을 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"` (마이그레이션 방식·버전·컬럼·요청 필드)
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- 웹 화면에서 정책을 바꾸는 폼·엔드포인트를 만들지 마라. 이유: 이번 범위는 등록 시 설정(Step 3 의 연결 프로그램 플래그)뿐이다.
- `executions` 테이블을 건드리지 마라. 이유: `failed_retry_after` 컬럼은 Step 4 가 version 3 으로 추가한다.
- 마이그레이션 라이브러리(alembic 등)를 추가하지 마라. 이유: ADR-0002 — 표준 `sqlite3` + 명시적 SQL.
- 연결 프로그램 코드를 고치지 마라. 이유: Step 3 이다.
- 기존 테스트를 깨뜨리지 마라
