# Step 7: branch-key — 실행 요청 `work_key`, 결과 브랜치 `runloom/<키>`, PR 제목

## 읽어야 할 파일

- AGENTS.md
- phases/14-task-model/README.md, phases/14-task-model/index.json (step 0~6 summary)
- docs/adr/0020-work-items-and-stages.md, docs/adr/0018-real-repo-cycle.md, docs/ARCHITECTURE.md ("업무와 단계 — phase 14" 의 브랜치 규칙, "실제 저장소 순환 — phase 12" 의 브랜치 push·초안 PR), docs/CONTRACT.md (`work_key` 예시)
- src/workflow/contracts/v1.py (`ExecutionRequest`), src/workflow/server/worker.py (실행 요청 조립, `_queue_pull_request`, `_deliver_pull_requests`), src/workflow/domain/pull_request.py (`head_branch`, `pr_body`), src/workflow/server/github_delivery.py (브랜치 문구)
- src/workflow/connector/git_ops.py (`_task_branch`, `ensure_worktree`, `push_task_branch`, `_safe`), src/workflow/connector/runner.py, src/workflow/connector/local_tool.py (브랜치 이름을 쓰는 곳)
- tests: tests/workflow/contracts/test_v1.py, tests/workflow/connector/test_git_ops.py, test_runner.py, test_local_tool.py, tests/workflow/domain/test_pull_request.py, tests/workflow/server/test_worker.py, tests/e2e/test_real_repo.py

## 작업

1. 계약: `ExecutionRequest.work_key: str | None = None`, 패턴 `^[A-Z][A-Z0-9]{1,9}-[1-9][0-9]{0,8}$`(ARCHITECTURE 값). 워커가 업무 키를 넣는다.
2. 브랜치 이름 함수는 **contracts 에 한 번만** 둔다(예: `contracts/v1.result_branch(task_id, work_key, branch_seq) -> str`) — 러너(`git_ops`)와 서버(`domain/pull_request.head_branch`)가 같은 함수를 쓴다(`server`·`connector` 는 `contracts` 만 공유). 규칙: `work_key` 가 있으면 `runloom/<work_key>`, 없으면 `task/<safe task_id>`.
3. 같은 단계의 재작업은 같은 브랜치(지금과 같음). **다시 맡기기**(step 6 `retry` — 새 단계 Task)는 기준 커밋에서 새로 시작하고 브랜치에 순번을 붙인다: 실행 요청 선택 칸 `branch_seq: int >= 1`(기본 1, 서버가 "그 업무에서 같은 종류 단계의 순번" 으로 계산) → 1 이면 `runloom/<키>`, 2 이상이면 `runloom/<키>-<순번>`. 옛 브랜치는 그대로 두고 force push 하지 않는다. 새 단계는 PR 대기열 행도 새로(PK = 단계 task_id).
4. 서버: 새 PR 대기열 행의 `head_branch` 는 새 규칙, 이미 있는 행은 그대로. PR 제목 `"<업무 키> <원본 제목>"`, 본문에 업무 키 줄 추가(`Fixes #N` 유지). 원본 댓글 문구의 브랜치·업무 표시도 새 규칙.
5. 외부 입력에서 브랜치 이름을 받지 않는다 — 키는 서버가 만든 값이고 러너는 패턴 검사 뒤에만 쓴다. 패턴 불일치면 러너가 실행을 `failed`(코드 ARCHITECTURE 값)로 보고.

## 테스트 먼저

- contracts: `work_key` 패턴(허용·거부), `branch_seq` 범위, `result_branch` 세 경우(키 없음·순번 1·순번 2).
- git_ops: bare 저장소로 `runloom/RUN-3` worktree·push, 옛 요청(`work_key` 없음) → `task/<id>`, 잘못된 키 → 실행 전 거부.
- worker·pull_request: PR 제목·본문·`head_branch`, 기존 대기열 행 불변.
- e2e `tests/e2e/test_real_repo.py`: 브랜치 이름 기대값 갱신(`WORKFLOW_E2E=1` 로 실행).

소스·배포 파일 변경 전에 `tests/` 미러 경로에 실패 테스트를 작성하고 실패 원인이 의도한 것인지 확인한다(`tdd-guard.sh` 가 테스트 없는 소스 작성을 막는다). 구현 후 해당 테스트와 전체 회귀를 통과시킨다.

## Acceptance Criteria

```bash
python3 -m pytest -q
python3 -m ruff check .
WORKFLOW_E2E=1 python3 -m pytest tests/e2e -q   # e2e 를 건드린 step
```

위 커맨드와 이 step 의 테스트 항목을 모두 확인한다.

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 규칙을 확인한다: `domain/` 은 FastAPI·sqlite3·HTTPX·subprocess·Git 을 import 하지 않는다 / `server/` 와 `connector/` 는 서로 import 하지 않고 `contracts/` 만 공유한다 / 외부 입력(요청 본문·이슈 본문·모델 응답)에서 명령·경로·브랜치 이름을 받아 실행하지 않는다 / 비밀값(연결 토큰, `OPERATOR_TOKEN`, `SESSION_SECRET`, GitHub App 비밀·설치 토큰, PAT, 알림 웹훅 URL, 러너 로컬 등록의 환경변수 값)은 DB·로그·응답·템플릿·백업·중앙에 넣지 않는다 / GLOSSARY 이름을 그대로 쓴다 / phase 8·11·12 의 GitHub 순환(수집 → 수정 → 검토 → 초안 PR → 병합 추적)이 그대로 동작한다.
3. 성공이면 `phases/14-task-model/index.json` 의 step 7 만 `completed` 로 바꾸고 `summary` 에 생성·수정·삭제 파일, 결정, 검증 결과(테스트 수), 다음 step 이 주의할 점을 한 줄로 남긴다.
4. 3회 수정 후에도 실패하면 `error` + `error_message`, 사용자 결정·외부 자원이 필요하면 `blocked` + `blocked_reason` 을 기록한다. 실행하지 않은 검증을 `completed` 로 표시하지 않는다.

## 금지사항

- 실제 GitHub(api.github.com·github.com)·Discord·Jira 를 호출하지 않는다(공식 문서 읽기는 허용). 이유: 모든 step 은 httpx `MockTransport`·가짜 GitHub·임시 bare 저장소로 검증한다. 실연동은 phase 뒤 사용자와 함께 한다.
- 사용자가 띄워 둔 셀프호스트(compose 프로젝트 `runloom`, 포트 8000, 볼륨 `runloom_workflow-data`)·`deploy/selfhost/.env`·러너 홈(`~/Library/Application Support/workflow-connector/`)·launchd·`/Users/kje/demo/*`(OpenArchive·runloom-sandbox 클론)를 읽거나 바꾸지 않는다. 컨테이너를 띄우거나 멈추지 않는다. 이유: 실사용 중인 환경이다.
- 종류 이름(`bug_fix`·`code_review`)으로 새 분기를 만들지 않는다. 이유: 업무 종류·후속 규칙은 워크스페이스 등록 데이터다(ADR-0009, AGENTS.md).
- `tasks` 테이블을 재생성하거나 이름을 바꾸지 않는다. 이유: ADR-0020 결정 — 업무 상태는 `work_items` 에 두고 단계 상태는 그대로 둔다.
- 새 화면 구성(담당자별 묶음·보드·상세 패널·연결 화면)을 만들지 않는다. 이유: 16-work-ui 범위. 이 phase 의 화면 변경은 step 9 최소 변경뿐이다.
- 사용자 변경을 삭제하거나 stash·reset 하지 않는다. 커밋은 하네스가 한다.
- 이 step 범위 밖 모듈을 "개선"하지 않는다. 기존 테스트의 단정을 약하게 만들어 통과시키지 않는다.
