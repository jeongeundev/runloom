# Step 6: task-source-schema

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/AGENTS.md` — DB 는 `adapters/` 에서만, 명시적 SQL
- `/docs/ARCHITECTURE.md` — "저장", "중앙 DB"
- `/docs/GLOSSARY.md` — Task 정의 (여기에 `source` 를 추가하게 된다)
- `/src/workflow/adapters/db.py` — `_SCHEMA`, `SCHEMA_VERSION = 1`, `init_schema` ("다른 버전이면 실패")
- `/src/workflow/adapters/repo.py` — `insert_task`, `get_task`, `list_tasks`
- `/src/workflow/server/views.py` — `task_summary`, `task_context` (source 를 화면에 내보낼 자리)
- `/tests/workflow/adapters/test_db.py`, `test_repo.py`
- `/deploy/backup.sh`, `/docs/DEPLOY.md` 8절 — 배포 DB 는 이미 version 1 로 존재한다

이전 step에서 만들어진 코드를 꼼꼼히 읽고, 설계 의도를 이해한 뒤 작업하라. 새 기능은 테스트를 먼저 작성한다 (AGENTS.md TDD, `tests/` 는 `src/` 미러 배치).

## 배경

두 가지가 DB 에 필요하다. (1) GitHub Issues 에서 가져온 업무의 출처(저장소·이슈 번호·URL) — 카드 링크와 나중의 상태 되쓰기. (2) **세션별 에이전트 등록** — 심사자(실서비스의 사용자)가 빈 워크스페이스에서 시작해 연결돼 있는 에이전트를 자기 워크스페이스에 등록하는 흐름(step 9)의 저장소. 배포된 DB 가 version 1 이고 `init_schema` 가 다른 버전이면 실패하므로, **첫 마이그레이션 경로**를 함께 만들고 두 변경을 v2 하나에 담는다.

## 작업

### `src/workflow/adapters/db.py`

- `_SCHEMA` 의 `tasks` 에 `source_json TEXT` (NULL 허용) 추가. 새 테이블:
  ```sql
  CREATE TABLE IF NOT EXISTS session_agents (
    session_id    TEXT NOT NULL REFERENCES sessions(session_id),
    agent_id      TEXT NOT NULL REFERENCES agents(agent_id),
    display_name  TEXT NOT NULL,          -- 세션 사용자가 정한 이름 (기본은 agents.name)
    registered_at TEXT NOT NULL,
    PRIMARY KEY (session_id, agent_id)
  );
  ```
  `SCHEMA_VERSION = 2`.
- `init_schema`: 기록된 version 이 1 이면 마이그레이션을 적용하고 version 을 2 로 올린다. 그 외 다른 버전은 지금처럼 실패. 마이그레이션 목록은 `_MIGRATIONS: dict[int, list[str]] = {1: ["ALTER TABLE tasks ADD COLUMN source_json TEXT", "CREATE TABLE IF NOT EXISTS session_agents (…)"]}` 처럼 버전 → SQL 목록으로 두고 순서대로 적용한다 (다음에 또 쓸 수 있게, 단 도구를 만들지는 않는다). 새 테이블 SQL 은 `_SCHEMA` 와 한 상수를 공유한다.
- 마이그레이션은 트랜잭션 하나로 한다. 실패하면 version 은 그대로다.

### `src/workflow/adapters/repo.py`

- `insert_task` 가 `task["source"]` (dict | None) 를 `source_json` 에 JSON 으로 저장. 키 없으면 NULL.
- `get_task`·`list_tasks` 행은 그대로 `source_json` 을 포함한다 (Row).
- `source` 형식 (contracts 에 넣지 않는다 — 서버 내부 값): `{"kind": "github_issue", "repo": "owner/name", "number": 12, "url": "https://github.com/owner/name/issues/12"}`.
- `session_agents` 접근 함수: `register_session_agent(conn, session_id, agent_id, display_name, now)`(멱등 — 있으면 이름만 갱신), `list_session_agents(conn, session_id) -> list[Row]`(agents 와 JOIN, `display_name` 포함), `unregister_session_agent(conn, session_id, agent_id)`. 화면·선택 로직 연결은 step 9.

### `src/workflow/server/views.py`

- `task_summary`·`task_context` 에 `source: dict | None` 을 넣는다 (JSON 파싱). 화면 표시는 step 8.

### `docs/GLOSSARY.md`

- Task 행에 "source: 업무의 외부 출처(예: GitHub 이슈). 없으면 서비스 안에서 등록한 업무" 를 추가.

### 테스트 (먼저 작성)

- `test_db.py`: 새 DB 는 version 2 이고 `tasks.source_json`·`session_agents` 가 있다; version 1 로 만든 DB(구 스키마를 테스트 안에서 직접 만든다) 에 `init_schema` 를 돌리면 컬럼·테이블이 생기고 version 2, 기존 행은 `source_json IS NULL`; version 3 은 실패; 두 번 돌려도 멱등.
- `test_repo.py`: `source` 있는/없는 insert·get 왕복; `register_session_agent` 멱등·이름 갱신, `list_session_agents` 가 다른 세션 것을 섞지 않음, 없는 agent_id 는 FK 로 실패.
- `test_views.py`: `task_summary` 의 `source`.

## Acceptance Criteria

```bash
python3 -m pytest tests/workflow/adapters tests/workflow/server -q
python3 -m pytest -q
python3 -m ruff check .
python3 - <<'EOF'
import sqlite3, tempfile, pathlib
from workflow.adapters.db import connect, init_schema, SCHEMA_VERSION
p = pathlib.Path(tempfile.mkdtemp()) / "t.sqlite"
c = connect(p); init_schema(c); init_schema(c)
assert c.execute("select version from schema_version").fetchone()[0] == SCHEMA_VERSION == 2
assert "source_json" in [r[1] for r in c.execute("pragma table_info(tasks)")]
assert c.execute("select name from sqlite_master where name='session_agents'").fetchone()
print("schema ok")
EOF
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - ARCHITECTURE.md 디렉토리 구조를 따르는가?
   - ARCHITECTURE.md 외부 의존 표에 없는 외부 서비스를 끌어들이지 않았는가?
   - ADR 기술 스택을 벗어나지 않았는가?
   - AGENTS.md CRITICAL 규칙을 위반하지 않았는가? (도메인이 I/O 를 import 하지 않는가, server↔connector 가 서로 import 하지 않는가, 외부 입력에서 명령·경로를 실행하지 않는가, 비밀값이 DB·로그·응답에 없는가)
   - GLOSSARY.md 용어를 그대로 썼는가? 금지 표현을 쓰지 않았는가?
3. 결과에 따라 `phases/4-claude-issues/index.json`의 해당 step을 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"` (만든 파일·함수·결정 사항)
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- `contracts/v1.py` 에 `source` 를 넣지 마라. 이유: 출처는 중앙 서비스 내부 정보이며 연결 프로그램·진단 API 계약과 무관하다.
- `init_schema` 의 "알 수 없는 버전이면 실패" 를 없애지 마라. 이유: 잘못된 DB 에 조용히 쓰는 것을 막는 안전장치다.
- 배포 VM 의 DB 를 이 step 에서 건드리지 마라. 이유: 배포는 step 10 뒤 `update-vm.sh` 로 하며 서비스 시작 시 `init_schema` 가 마이그레이션한다.
- 기존 테스트를 깨뜨리지 마라
