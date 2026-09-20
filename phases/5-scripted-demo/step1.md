# Step 1: chain-schema

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/AGENTS.md`, `/docs/ARCHITECTURE.md`(특히 "저장" 절과 상태 규칙), `/docs/adr/` 전부, `/docs/GLOSSARY.md`
- `/src/workflow/adapters/db.py` — `_SCHEMA`, `SCHEMA_VERSION`, `init_schema`(버전이 다르면 RuntimeError — 마이그레이션 없음)
- `/src/workflow/adapters/repo.py` — `upsert_agent`, `list_agents`, `insert_task`, `list_tasks`, `successors_of`, `tasks_with_completed_predecessor`
- `/tests/workflow/adapters/test_db.py`, `/tests/workflow/adapters/test_repo.py`, `/tests/workflow/adapters/conftest.py`
- `/phases/5-scripted-demo/step0.md` 의 "배경" 절 — 이 phase 의 목적

이전 step 에서 만들어진 코드를 꼼꼼히 읽고, 설계 의도를 이해한 뒤 작업하라.

## 이 step 이 만드는 개념 (뒤 step 이 그대로 쓴다)

- **세션 등록(session agent)**: 운영자가 미리 넣어 둔 카탈로그 Agent(`agents.shared_to_all_sessions=1`) 중 심사자 세션이 "등록"한 것. 세션은 등록한 Agent 만 홈·후보·선택에 쓴다. 다른 세션과 격리된다.
- **Chain**: 세션이 "업무 가져오기"로 만든 Task 묶음. 코드 식별자는 `Chain`/`chain_id`(화면 라벨은 "워크플로우"). `workflow_id` 라는 이름은 이미 진단 대상 자동화의 ID(`daily-report`, GLOSSARY)로 쓰이므로 여기서 쓰지 않는다. 순서는 Task 의 `predecessor_task_id` 체인으로만 표현한다(별도 순서 컬럼 없음).
- **demo_scripted**: Agent 가 대본 에이전트임을 나타내는 플래그. 화면에 "시연용 · 대본 재생"을 붙이는 근거.

## 작업

### `src/workflow/adapters/db.py`

- `SCHEMA_VERSION` 을 1 올린다. 마이그레이션은 만들지 않는다(기존 방침). 배포 시 DB 를 새로 만든다(step 10 이 문서화).
- `agents` 에 `demo_scripted INTEGER NOT NULL DEFAULT 0 CHECK (demo_scripted IN (0, 1))`.
- 신규 테이블:
  ```sql
  CREATE TABLE IF NOT EXISTS session_agents (
    session_id    TEXT NOT NULL REFERENCES sessions(session_id),
    agent_id      TEXT NOT NULL REFERENCES agents(agent_id),
    registered_at TEXT NOT NULL,
    PRIMARY KEY (session_id, agent_id)
  );
  CREATE TABLE IF NOT EXISTS chains (
    chain_id   TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(session_id),
    title      TEXT NOT NULL,
    source     TEXT NOT NULL CHECK (source IN ('github', 'jira', 'manual')),
    skipped_json TEXT NOT NULL DEFAULT '[]',   -- 체인에 못 들어간 이슈 [{key, title, reason}] (Task 를 만들지 않은 것)
    created_at TEXT NOT NULL,
    started_at TEXT
  );
  ```
- `tasks` 에 `chain_id TEXT REFERENCES chains(chain_id)`(NULL 허용 — 직접 등록 Task 는 체인이 없다) 와 `source_ref TEXT`(가져온 이슈 키, 예 `#42` / `OPS-42`, NULL 허용).

### `src/workflow/adapters/repo.py`

시그니처만 정한다. 구현·SQL 은 기존 함수 스타일(`_tx`, `_one`, `Row`)을 따른다.

```python
def upsert_agent(conn, agent: dict) -> None      # 기존. agent.get("demo_scripted", False) 를 컬럼에 반영
def register_session_agent(conn, session_id: str, agent_id: str, now: str) -> None   # 멱등 (INSERT OR IGNORE)
def unregister_session_agent(conn, session_id: str, agent_id: str) -> None
def list_session_agents(conn, session_id: str) -> list[Row]     # agents 행, registered_at 오름차순 (먼저 등록한 순서가 뒤 step 의 동률 규칙에 쓰인다)
def is_session_agent(conn, session_id: str, agent_id: str) -> bool
def insert_chain(conn, chain: dict, now: str) -> None            # keys: chain_id, session_id, title, source, skipped(list[dict], 기본 [])
def get_chain(conn, chain_id: str) -> Row | None
def list_chains(conn, session_id: str) -> list[Row]              # created_at 오름차순
def mark_chain_started(conn, chain_id: str, now: str) -> None    # started_at 이 NULL 일 때만
def tasks_of_chain(conn, chain_id: str) -> list[Row]             # predecessor 체인 순서로 정렬: 선행 없는 것부터, 그 다음 그것을 선행으로 갖는 것 … (파이썬에서 정렬)
def insert_task(conn, task: dict, now: str) -> None              # 기존. task.get("chain_id"), task.get("source_ref") 반영
```

- `delete_agent` 는 `session_agents` 의 해당 행도 지운다.
- `list_tasks(conn, session_id)` 의 정렬·반환은 바꾸지 않는다.

### 테스트 (먼저 작성)

- `tests/workflow/adapters/test_db.py`: 새 테이블·컬럼 존재, `SCHEMA_VERSION` 불일치 시 RuntimeError(기존 테스트 유지).
- `tests/workflow/adapters/test_repo.py`: 세션 등록 멱등·해제·목록 순서(registered_at)·`is_session_agent`; 다른 세션과 격리; `delete_agent` 가 세션 등록도 지움; chain 삽입·조회(`skipped` 왕복)·`mark_chain_started` 1회만; `tasks_of_chain` 이 A→B→C 순서로 나옴(삽입 순서를 섞어도); `demo_scripted` 왕복.

## Acceptance Criteria

```bash
python3 -m pytest tests/workflow/adapters -q     # 어댑터 테스트 통과
python3 -m pytest -q                              # 전체 통과
python3 -m ruff check .
grep -n "session_agents\|chains\|demo_scripted\|chain_id\|source_ref" src/workflow/adapters/db.py
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - ARCHITECTURE.md 디렉토리 구조를 따르는가? (DB 는 `adapters/` 에서만)
   - ADR 기술 스택을 벗어나지 않았는가? (표준 sqlite3 + 명시적 SQL)
   - AGENTS.md CRITICAL 규칙을 위반하지 않았는가?
   - GLOSSARY.md 용어를 그대로 썼는가? (`Chain` 은 새 용어 — step 11 이 GLOSSARY 에 넣는다. `workflow_id` 를 체인 ID 로 쓰지 마라)
3. 결과에 따라 `phases/5-scripted-demo/index.json` 의 해당 step 을 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"`
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- `tasks.kind`·`executions.kind` 의 CHECK(`diagnosis`, `code_change`)를 늘리지 마라. 이유: 이 phase 는 새 업무 종류를 만들지 않는다.
- 마이그레이션 코드를 만들지 마라. 이유: 기존 방침(버전 불일치는 오류, DB 재생성).
- `server/`·`domain/` 를 건드리지 마라. 이유: 이 step 은 저장 계층만.
- 기존 테스트를 깨뜨리지 마라.
