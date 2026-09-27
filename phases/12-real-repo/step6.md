# Step 6: 중앙 — 검토 승인 뒤 초안 PR, 병합 추적

## 읽어야 할 파일

- AGENTS.md
- phases/12-real-repo/README.md (계획 기본값 — 이 phase 의 기준)
- phases/12-real-repo/index.json (완료 step 의 summary)
- docs/adr/0018-real-repo-cycle.md (step 0 산출물. step 0 에서는 새로 만든다)
- docs/ARCHITECTURE.md ("실제 저장소 순환 — phase 12" 절. step 0 에서는 새로 만든다)
- docs/GLOSSARY.md
- src/workflow/adapters/github_client.py (`HttpGitHubClient`, `check_response`, 허용 저장소 검사), src/workflow/adapters/github_app.py (manifest `default_permissions`)
- src/workflow/server/worker.py (검토 `approved` 처리), src/workflow/server/github_sync.py, src/workflow/server/github_clients.py (`client_for`)
- src/workflow/domain/task_followup.py, src/workflow/domain/task_readiness.py, src/workflow/domain/metrics.py (`pr_merged_at`)
- src/workflow/adapters/db.py (스키마 v7 → v8), src/workflow/adapters/repo.py, src/workflow/server/backup.py
- src/workflow/server/views.py·templates (업무 상태 문구, PR 링크)
- tests/e2e/test_github_cycle.py 의 FakeGitHub (가짜 GitHub 확장 지점)

먼저 실제 파일과 이전 step 산출물을 읽는다. 이름·경로·API 필드는 step 0 의 ADR-0018·ARCHITECTURE 가 고정한 것을 따르고, 바꿔야 하면 같은 이름의 문서·테스트를 함께 갱신한다. 대화 이력을 전제로 판단하지 않는다.

## 작업

- `HttpGitHubClient` 에 `default_branch(repo) -> str`, `create_pull_request(repo, *, head, base, title, body, draft) -> PullRequestRef(number, url, state)`, `find_pull_request(repo, head) -> PullRequestRef | None`, `get_pull_request(repo, number) -> PullRequestState(state, merged_at)` (이름은 ADR-0018 표를 따른다). 422 "이미 있음" → `find_pull_request` 로 기존 PR. 초안 미지원 422 → `draft=False` 로 한 번 더.
- 스키마 v8: ADR-0018 이 정한 PR 기록(수정 Task 당 하나, 저장소·번호·URL·상태·병합 시각, 유일 제약) + `_migrate_7_to_8`, 4~7 에서 8 까지 이어지는 경로. 백업·복원 테스트가 새 스키마로 통과해야 한다.
- 검토 `approved` 처리에서: 수정 결과 브랜치가 push 됐으면(`branch_pushed` True) PR 을 연다 — 제목 = 원본 이슈 제목, 본문 = `Fixes #N` + 검토 요약(길이 제한) + Runloom 업무 링크(`WORKFLOW_PUBLIC_URL` 없으면 링크 생략). 이미 PR 기록이 있으면 다시 열지 않는다(멱등, tick 재평가 안전). push 안 됐거나 GitHub 실패면 사람 요청(ADR-0018 코드, 예 `pr_unavailable`, 사유에 push 명령 안내 한 줄)을 만들고 업무는 지금처럼 사람 차례.
- GitHub 이슈에서 온 수정 Task 만 대상. 원본 이슈가 없는 Task 는 기존 동작.
- 동기화: 열린 PR 기록마다 상태 조회 → 병합되면 수정 Task 완료(outcome·사유 ADR-0018), 원본 이슈 `pr_merged_at` 이 비어 있으면 병합 시각으로 채움(지표가 쓰는 칸), 병합 없이 닫히면 종료. 이미 마감된 Task 는 건드리지 않는다.
- manifest `default_permissions` 를 ADR-0018 값으로 올린다(예 `pull_requests: write`). 권한 부족 403 은 사람 요청 사유에 "GitHub App 권한(Pull requests 쓰기) 승인 필요"로 보인다.
- 화면: 수정 Task 상태가 PR 열림이면 "사람 차례 · PR 확인" + PR 링크, 병합 뒤 완료.

## 테스트 먼저

- github_client: MockTransport 로 PR 생성 본문(head·base·draft·`Fixes #N`), 422 이미 있음 → 기존 PR, 초안 미지원 → 일반 PR, 403 분류.
- 마이그레이션 v7→v8(기존 데이터 보존), 새 DB v8, 백업·복원.
- worker: approved + pushed → PR 1개, 같은 검토 재평가 → PR 그대로 1개, pushed False → 사람 요청 1개·PR 없음, GitHub 실패 → 사람 요청.
- sync: 병합 → Task 완료·`pr_merged_at` 채움, 닫힘 → 종료, 마감된 Task 불변.
- metrics: 병합으로 끝난 업무가 도입 후 "이슈 → 병합"에 잡힘.
- 화면: 상태 문구·PR 링크.

소스·배포 파일 변경 전에 `tests/` 미러 경로에 실패 테스트를 작성하고 실패 원인이 의도한 것인지 확인한다(`tdd-guard.sh` 가 테스트 없는 소스 작성을 막는다). 구현 후 해당 테스트와 전체 회귀를 통과시킨다.

## Acceptance Criteria

```bash
python3 -m pytest -q
python3 -m ruff check .
```

위 커맨드와 이 step 의 테스트 항목을 모두 확인한다.

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 규칙을 확인한다: `domain/` 은 FastAPI·sqlite3·HTTPX·subprocess·Git 을 import 하지 않는다 / `server/` 와 `connector/` 는 서로 import 하지 않고 `contracts/` 만 공유한다 / 외부 입력(요청 본문·이슈 본문·모델 응답)에서 명령·경로·브랜치 이름을 받아 실행하지 않는다 — git 인자 배열은 러너 어댑터가 고정한다 / 비밀값(연결 토큰, GitHub App 비밀·설치 토큰, PAT, 알림 웹훅 URL, 러너 로컬 등록의 환경변수 값)은 DB·로그·응답·템플릿·백업·중앙에 넣지 않는다 / 기본 모드(demo)와 phase 8·11 의 기존 GitHub 순환이 그대로 동작한다.
3. 성공이면 `phases/12-real-repo/index.json` 의 step 6 만 `completed` 로 바꾸고 `summary` 에 생성·수정 파일, 결정, 검증 결과(테스트 수), 다음 step 이 주의할 점을 한 줄로 남긴다.
4. 3회 수정 후에도 실패하면 `error` + `error_message`, 사용자 결정·외부 자원이 필요하면 `blocked` + `blocked_reason` 을 기록한다. 실행하지 않은 검증을 `completed` 로 표시하지 않는다.

## 금지사항

- 실제 GitHub(api.github.com·github.com)와 실제 Discord 를 호출하지 않는다(docs.github.com·discord.com/developers 문서 읽기는 허용). 이유: 모든 step 은 httpx `MockTransport`·가짜 GitHub·로컬 bare 저장소로 검증한다. 실연동은 phase 뒤 사용자와 함께 한다.
- `/Users/kje/demo/OpenArchive` 와 그 worktree 를 읽거나 바꾸지 않는다. 이유: 사용자가 개발 중인 실제 저장소다. 테스트는 임시 디렉터리의 가짜 저장소로 한다.
- 사용자가 띄워 둔 셀프호스트(compose 프로젝트 `runloom`, 포트 8000, 볼륨 `runloom_workflow-data`)·`deploy/selfhost/.env`·러너 홈(`~/Library/Application Support/workflow-connector/`)·launchd 를 건드리지 않는다. 컨테이너를 띄우거나 멈추지 않는다.
- 러너가 기본 브랜치에 push 하거나 force push 하지 않는다. 병합·이슈 닫기는 하지 않는다 — 사람만 한다.
- `main`·공개 데모 VM·demo 모드 기본 동작을 바꾸지 않는다.
- 사용자 변경을 삭제하거나 stash·reset 하지 않는다. 커밋은 하네스가 한다.
- 이 step 범위 밖 모듈을 "개선"하지 않는다.
