# Step 2: schema-v10 — 업무·링크·멤버·매핑 표와 v9 → v10 마이그레이션

## 읽어야 할 파일

- AGENTS.md
- phases/14-task-model/README.md, phases/14-task-model/index.json (step 0·1 summary)
- docs/adr/0020-work-items-and-stages.md, docs/ARCHITECTURE.md ("업무와 단계 — phase 14" 의 스키마 v10 표·마이그레이션 규칙)
- src/workflow/adapters/db.py (`_SCHEMA`, `init_schema`, `_migrate_*` — 특히 phase 13 의 `_migrate_8_to_9` 형식), tests/workflow/adapters/test_db.py
- src/workflow/adapters/repo.py (`create_session`, 워크스페이스 생성 경로 `server/auth.py` `ensure_workspace`)
- src/workflow/domain/work_status.py (step 1), src/workflow/domain/status.py
- src/workflow/server/backup.py, tests/workflow/server/test_backup.py (스키마 버전 확인 방식)

## 작업

1. `db.py`: `SCHEMA_VERSION = 10`. ARCHITECTURE 표대로 `work_items`·`work_item_links`·`members`·`field_mappings`(와 step 0 이 정한 업무 이벤트 표)를 CREATE, `tasks.work_item_id` 는 ALTER 로 더한다(빈 DB·9 → 10 같은 정의 공유 — v6~v8 방식). 업무 상태 CHECK 는 `domain/work_status.WORK_STATUSES` 상수에서 만든다(문자열을 두 곳에 적지 않는다). 인덱스: `tasks(work_item_id)`, `work_items(session_id, status)`.
2. `_migrate_9_to_10` (호출자 트랜잭션 안):
   - 업무 묶기: `followup_links` 로 생긴 Task → 원인 Execution 의 Task 와 같은 업무(사슬을 끝까지 따라감). 그 밖 Task → 각자 새 업무.
   - 업무 칸 채우기: 제목·요청·종류 = 업무의 첫 단계 Task 값. 원본: 첫 단계에 `source_issues` 행이 있으면 `github`·소스 id·`owner/repo#N`·`snapshot_json` 의 URL·원본 상태, 체인 소속(n8n)이면 `n8n`, 그 밖 `manual`. 담당: 첫 단계 `chosen_agent_id` 가 있으면 `agent`, 없으면 없음. 양식 칸은 비워 둔다(추출은 step 5 — 마이그레이션에서 본문을 다시 해석하지 않는다).
   - 키: 워크스페이스별로 업무의 가장 이른 Task `created_at` 순(같으면 task_id 순)으로 1, 2, 3 …
   - 업무 상태: DB 사실로 `work_status` 를 계산해 넣는다. 사실을 모으는 SQL 은 이 함수 안에서만 쓰고, step 3 repo 가 같은 사실 조립 함수를 공유할 수 있게 `adapters/` 쪽 한 곳에 둔다.
   - 링크: 서로 다른 업무에 속한 Task 사이의 `predecessor_task_id` → `work_item_links(type='blocks')`.
   - 멤버: 워크스페이스마다 `admin` 1명(`display_name` "관리자").
   - 매핑: 워크스페이스마다 기본 행 `github · kind · * → bug_fix`.
   - 끝에 `PRAGMA foreign_key_check`, 그리고 `tasks.work_item_id IS NULL` 행이 0 인지 검사 — 아니면 `RuntimeError`.
3. 새 워크스페이스 생성 경로(`create_session`)가 첫 관리자·기본 매핑 행을 같은 트랜잭션에서 만든다.

## 테스트 먼저

`tests/workflow/adapters/test_db.py`:
- 빈 DB → v10, 표·칸·CHECK 존재.
- v9 DB 픽스처(워크스페이스 1개, GitHub 이슈 2건 — 하나는 fix+review+재작업, 하나는 검토 마감 뒤 두 번째 검토 Task 까지; n8n 체인 2업무(선행 있음); 직접 등록 1건) → v10: 업무 4+개·키 순서·모든 Task 에 업무·검토 Task 들이 fix 업무에 붙음·체인 선행이 `blocks` 링크·업무 상태 값·관리자 1명·매핑 1행·기존 행 수 보존.
- 마이그레이션 중 실패(예: 강제로 NULL 남김) → 버전 9 그대로·데이터 불변.
- v4~v8 DB 가 v10 까지 오른다(기존 테스트 기대 버전 갱신). 백업·복원 테스트의 버전 기대 갱신.

소스·배포 파일 변경 전에 `tests/` 미러 경로에 실패 테스트를 작성하고 실패 원인이 의도한 것인지 확인한다(`tdd-guard.sh` 가 테스트 없는 소스 작성을 막는다). 구현 후 해당 테스트와 전체 회귀를 통과시킨다.

## Acceptance Criteria

```bash
python3 -m pytest -q
python3 -m ruff check .
```

위 커맨드와 이 step 의 테스트 항목을 모두 확인한다.

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 규칙을 확인한다: `domain/` 은 FastAPI·sqlite3·HTTPX·subprocess·Git 을 import 하지 않는다 / `server/` 와 `connector/` 는 서로 import 하지 않고 `contracts/` 만 공유한다 / 외부 입력(요청 본문·이슈 본문·모델 응답)에서 명령·경로·브랜치 이름을 받아 실행하지 않는다 / 비밀값(연결 토큰, `OPERATOR_TOKEN`, `SESSION_SECRET`, GitHub App 비밀·설치 토큰, PAT, 알림 웹훅 URL, 러너 로컬 등록의 환경변수 값)은 DB·로그·응답·템플릿·백업·중앙에 넣지 않는다 / GLOSSARY 이름을 그대로 쓴다 / phase 8·11·12 의 GitHub 순환(수집 → 수정 → 검토 → 초안 PR → 병합 추적)이 그대로 동작한다.
3. 성공이면 `phases/14-task-model/index.json` 의 step 2 만 `completed` 로 바꾸고 `summary` 에 생성·수정·삭제 파일, 결정, 검증 결과(테스트 수), 다음 step 이 주의할 점을 한 줄로 남긴다.
4. 3회 수정 후에도 실패하면 `error` + `error_message`, 사용자 결정·외부 자원이 필요하면 `blocked` + `blocked_reason` 을 기록한다. 실행하지 않은 검증을 `completed` 로 표시하지 않는다.

## 금지사항

- 실제 GitHub(api.github.com·github.com)·Discord·Jira 를 호출하지 않는다(공식 문서 읽기는 허용). 이유: 모든 step 은 httpx `MockTransport`·가짜 GitHub·임시 bare 저장소로 검증한다. 실연동은 phase 뒤 사용자와 함께 한다.
- 사용자가 띄워 둔 셀프호스트(compose 프로젝트 `runloom`, 포트 8000, 볼륨 `runloom_workflow-data`)·`deploy/selfhost/.env`·러너 홈(`~/Library/Application Support/workflow-connector/`)·launchd·`/Users/kje/demo/*`(OpenArchive·runloom-sandbox 클론)를 읽거나 바꾸지 않는다. 컨테이너를 띄우거나 멈추지 않는다. 이유: 실사용 중인 환경이다.
- 종류 이름(`bug_fix`·`code_review`)으로 새 분기를 만들지 않는다. 이유: 업무 종류·후속 규칙은 워크스페이스 등록 데이터다(ADR-0009, AGENTS.md).
- `tasks` 테이블을 재생성하거나 이름을 바꾸지 않는다. 이유: ADR-0020 결정 — 업무 상태는 `work_items` 에 두고 단계 상태는 그대로 둔다.
- 새 화면 구성(담당자별 묶음·보드·상세 패널·연결 화면)을 만들지 않는다. 이유: 16-work-ui 범위. 이 phase 의 화면 변경은 step 9 최소 변경뿐이다.
- 사용자 변경을 삭제하거나 stash·reset 하지 않는다. 커밋은 하네스가 한다.
- 이 step 범위 밖 모듈을 "개선"하지 않는다. 기존 테스트의 단정을 약하게 만들어 통과시키지 않는다.
