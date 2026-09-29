# Step 3: work-item-repo — 업무 생성·조회·담당·상태 기록

## 읽어야 할 파일

- AGENTS.md
- phases/14-task-model/README.md, phases/14-task-model/index.json (step 0~2 summary)
- docs/adr/0020-work-items-and-stages.md, docs/ARCHITECTURE.md ("업무와 단계 — phase 14" 이름·시그니처 표)
- src/workflow/adapters/db.py (v10), src/workflow/adapters/repo.py (`create_task`·`_write_status` 류·`task_events` 기록·`upsert_source_issue`·`create_followup_once`), tests/workflow/adapters/test_repo.py
- src/workflow/domain/work_status.py

## 작업

`adapters/repo.py` 에 업무 함수(이름은 ARCHITECTURE 표):

1. `create_work_item(conn, *, session_id, title, request, kind, source=..., assignee=None, priority="normal", now) -> WorkItem행` — 키 번호는 같은 트랜잭션에서 `MAX(key_number)+1`(워크스페이스별). 호출자가 `BEGIN IMMEDIATE` 트랜잭션 안에서 부른다는 전제를 docstring 에 적고, 트랜잭션 밖 호출이면 오류. 동시 생성 두 건이 같은 번호를 받지 않음을 테스트로 보인다(UNIQUE 위반이 나면 호출자가 재시도하지 않아도 되게 한 트랜잭션 안에서 번호 계산).
2. `create_task` 류(직접 Task INSERT 하는 모든 repo 함수)가 `work_item_id` 를 **필수 인자**로 받게 한다. 호출부 전체를 고친다 — 아직 업무를 만들지 않는 호출부(GitHub 수집·n8n·폼·후속)는 이 step 에서는 "업무를 먼저 만들고 그 id 로 Task" 를 같은 트랜잭션에서 하도록 최소 변경(규칙 반영은 step 4·5). 기존 동작(행 수·상태)은 바뀌지 않아야 한다 — 단, 이제 업무 행이 함께 생긴다.
3. `get_work_item`, `get_work_item_by_key(session_id, key)`, `work_item_of_task(task_id)`, `list_work_items(session_id, *, status=None, assignee=None)`(목록 정렬: 갱신 시각 내림차순), `stages_of(work_item_id)`(Task 들, 생성 순).
4. `assign_work_item(conn, work_item_id, assignee | None, now)` — 멤버·에이전트가 같은 워크스페이스인지 검사(아니면 `NotFound`).
5. `refresh_work_status(conn, work_item_id, now)` — step 2 의 사실 조립 함수로 사실을 모아 `work_status` 를 계산해 바뀌었을 때만 쓰고 업무 이벤트(추가 전용)를 남긴다. 멱등.
6. `add_member`(관리자 이외 — 15-team 이 쓸 것, 지금은 테스트용), `list_members`, `first_admin(session_id)`.

## 테스트 먼저

`tests/workflow/adapters/test_repo.py`: 키 1·2·3 발급과 워크스페이스별 독립, 트랜잭션 밖 호출 오류, 다른 워크스페이스 에이전트 담당 → `NotFound`, `refresh_work_status` 가 같은 상태면 이벤트 없음·바뀌면 1개, 목록 필터, 모든 Task 생성 경로가 업무를 가진다(기존 repo 테스트 전부 통과 + `tasks.work_item_id IS NULL` 0 단정 도우미).

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
3. 성공이면 `phases/14-task-model/index.json` 의 step 3 만 `completed` 로 바꾸고 `summary` 에 생성·수정·삭제 파일, 결정, 검증 결과(테스트 수), 다음 step 이 주의할 점을 한 줄로 남긴다.
4. 3회 수정 후에도 실패하면 `error` + `error_message`, 사용자 결정·외부 자원이 필요하면 `blocked` + `blocked_reason` 을 기록한다. 실행하지 않은 검증을 `completed` 로 표시하지 않는다.

## 금지사항

- 실제 GitHub(api.github.com·github.com)·Discord·Jira 를 호출하지 않는다(공식 문서 읽기는 허용). 이유: 모든 step 은 httpx `MockTransport`·가짜 GitHub·임시 bare 저장소로 검증한다. 실연동은 phase 뒤 사용자와 함께 한다.
- 사용자가 띄워 둔 셀프호스트(compose 프로젝트 `runloom`, 포트 8000, 볼륨 `runloom_workflow-data`)·`deploy/selfhost/.env`·러너 홈(`~/Library/Application Support/workflow-connector/`)·launchd·`/Users/kje/demo/*`(OpenArchive·runloom-sandbox 클론)를 읽거나 바꾸지 않는다. 컨테이너를 띄우거나 멈추지 않는다. 이유: 실사용 중인 환경이다.
- 종류 이름(`bug_fix`·`code_review`)으로 새 분기를 만들지 않는다. 이유: 업무 종류·후속 규칙은 워크스페이스 등록 데이터다(ADR-0009, AGENTS.md).
- `tasks` 테이블을 재생성하거나 이름을 바꾸지 않는다. 이유: ADR-0020 결정 — 업무 상태는 `work_items` 에 두고 단계 상태는 그대로 둔다.
- 새 화면 구성(담당자별 묶음·보드·상세 패널·연결 화면)을 만들지 않는다. 이유: 16-work-ui 범위. 이 phase 의 화면 변경은 step 9 최소 변경뿐이다.
- 사용자 변경을 삭제하거나 stash·reset 하지 않는다. 커밋은 하네스가 한다.
- 이 step 범위 밖 모듈을 "개선"하지 않는다. 기존 테스트의 단정을 약하게 만들어 통과시키지 않는다.
