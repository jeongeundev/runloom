# Step 3: 스키마 v6 — 데이터 보존 마이그레이션

## 읽어야 할 파일

- AGENTS.md
- phases/9-measure/README.md (계획 기본값·지표 정의 — 이 phase 의 기준)
- phases/9-measure/index.json (완료 step 의 summary)
- docs/adr/0015-measurement-events-and-baseline.md (step 0 산출물. step 0 에서는 새로 만든다)
- docs/ARCHITECTURE.md ("측정 — phase 9" 절. step 0 에서는 새로 만든다)
- docs/GLOSSARY.md
- src/workflow/adapters/db.py (`SCHEMA_VERSION`, `_migrate_4_to_5`, `init_schema`)
- tests/workflow/adapters/test_db.py

먼저 실제 파일과 이전 step 산출물을 읽는다. 이름·칸·함수 시그니처는 step 0 의 ADR-0015·ARCHITECTURE 가 고정한 것을 따르고, 바꿔야 하면 같은 이름의 문서·계약·테스트를 함께 갱신한다. 대화 이력을 전제로 판단하지 않는다.

## 작업

`src/workflow/adapters/db.py` 만 바꾼다. ARCHITECTURE "측정 — phase 9" 의 v6 스키마 표를 그대로 구현한다.

- `SCHEMA_VERSION = 6`. 빈 DB 는 v6 로 바로 만든다.
- `_migrate_5_to_6(conn)`: `sessions.config_revision INTEGER NOT NULL DEFAULT 1`, `executions` 새 칸 6개(NULL 허용), `task_events`, `baseline_items`, `baseline_imports` 추가. 인덱스: `task_events(task_id, id)`, `task_events(session_id, occurred_at)`.
- `init_schema`: 4 는 4→5→6 을 한 트랜잭션으로, 5 는 5→6 으로. 실패하면 전부 되돌려 원래 버전 그대로 남는다. 그 밖의 버전은 기존처럼 거부.
- `task_events.type` CHECK 는 상수 튜플(예: `TASK_EVENT_TYPES`)에서 만든다(문자열을 두 곳에 쓰지 않는 기존 규칙).

핵심 규칙: 기존 행을 지우거나 다시 만들지 않는다(ALTER TABLE ADD COLUMN 으로 충분한 곳은 그것만 쓴다). `WORKFLOW_RESET_DB` 를 요구하지 않는다.

## 테스트 먼저

`tests/workflow/adapters/test_db.py`: 빈 DB → v6, 데이터가 있는 v5 DB → v6(기존 Task·Execution·Artifact·kinds 보존, 새 칸 NULL, `config_revision` 1), v4 → v6 한 번에, 마이그레이션 도중 실패 시 v5 그대로(되돌림), 재실행 멱등, `task_events.type` CHECK 거부, 지원 밖 버전 거부.

소스 변경 전에 `tests/` 미러 경로에 실패 테스트를 작성하고 실패 원인이 의도한 것인지 확인한다(`tdd-guard.sh` 가 테스트 없는 소스 작성을 막는다). 구현 후 해당 테스트와 전체 회귀를 통과시킨다.

## Acceptance Criteria

```bash
python3 -m pytest -q
python3 -m ruff check .
```

위 커맨드와 이 step 의 테스트 항목을 모두 확인한다.

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 규칙을 확인한다: `domain/` 은 FastAPI·sqlite3·HTTPX·subprocess·Git 을 import 하지 않는다 / `server/` 와 `connector/` 는 서로 import 하지 않고 `contracts/` 만 공유한다 / 비밀값(`WORKFLOW_GITHUB_TOKEN`, 연결 토큰 등)은 환경변수에서만 읽고 DB·로그·응답·템플릿에 넣지 않는다 / 이름은 GLOSSARY 식별자를 쓴다.
3. 성공이면 `phases/9-measure/index.json` 의 step 3 만 `completed` 로 바꾸고 `summary` 에 생성·수정 파일, 결정, 검증 결과(테스트 수), 다음 step 이 주의할 점을 한 줄로 남긴다.
4. 3회 수정 후에도 실패하면 `error` + `error_message`, 사용자 결정·외부 자격이 필요하면 `blocked` + `blocked_reason` 을 기록한다. 실행하지 않은 검증을 `completed` 로 표시하지 않는다.

## 금지사항

- `main`·공개 데모 VM 을 변경·배포하지 않는다. 이유: 이 phase 는 `service` 기반 실서비스 작업이다.
- 실제 GitHub·유료 모델을 호출하지 않는다. 이유: 모든 step 은 대역(httpx `MockTransport`, fake 도구)으로 검증한다. 실제 기준선 가져오기는 phase 뒤 사용자 지시로 한다.
- 사용자 변경을 삭제하거나 stash·reset 하지 않는다. 커밋은 하네스가 한다.
- 이 step 범위 밖 모듈을 "개선"하지 않는다. 이유: step 마다 한 레이어만 바꿔 회귀 원인을 좁힌다.
- 모르는 비용·시각을 0 이나 현재 시각으로 채우지 않는다. 이유: "모름"과 0 은 다른 사실이다.
