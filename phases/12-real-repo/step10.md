# Step 10: e2e 한 줄기와 문서·인계

## 읽어야 할 파일

- AGENTS.md
- phases/12-real-repo/README.md (계획 기본값 — 이 phase 의 기준)
- phases/12-real-repo/index.json (완료 step 의 summary)
- docs/adr/0018-real-repo-cycle.md (step 0 산출물. step 0 에서는 새로 만든다)
- docs/ARCHITECTURE.md ("실제 저장소 순환 — phase 12" 절. step 0 에서는 새로 만든다)
- docs/GLOSSARY.md
- tests/e2e/test_github_app.py, tests/e2e/test_github_cycle.py (FakeGitHub·FakeGitHubApp), tests/e2e/ 의 러너 구동 도우미
- 이 phase 의 모든 step summary
- docs/SELFHOST.md, docs/github/README.md, docs/CURRENT_HANDOFF.md, docs/VERIFICATION_LOG.md

먼저 실제 파일과 이전 step 산출물을 읽는다. 이름·경로·API 필드는 step 0 의 ADR-0018·ARCHITECTURE 가 고정한 것을 따르고, 바꿔야 하면 같은 이름의 문서·테스트를 함께 갱신한다. 대화 이력을 전제로 판단하지 않는다.

## 작업

- 신설 `tests/e2e/test_real_repo.py`(`WORKFLOW_E2E=1`): 가짜 GitHub App + 임시 bare 저장소를 origin 으로 둔 "원본 폴더"(원본에만 있는 `deps/` 디렉터리) + 로컬 가짜 알림 수신 서버.
  흐름: 운영자 로그인 → GitHub 연결(가짜) → 카드 [러너 붙이기] → 화면의 명령에서 코드를 읽어 `connector setup --repo 원본 --verify check=… --link deps --env CHECK_DB=…` → 카드 매칭 (자동) → bare 에 새 커밋 push(사용자가 다른 곳에서 개발한 상황) → 이슈 [맡기기] → 수정 실행의 기준 커밋이 새 커밋 → 검증 명령이 링크된 `deps/` 와 `CHECK_DB` 를 봄 → bare 에 `task/<id>` → 검토 승인 → 가짜 GitHub 에 초안 PR(`Fixes #N`) → 알림 수신 서버에 "PR 확인" 1건 → 가짜 GitHub 에서 PR 병합 → 동기화 뒤 Task 완료·지표에 도입 후 1건 → 두 번째 이슈는 실패하도록 해 실패 알림 1건.
  비밀 검사: 연결 코드·env 값·알림 URL 경로·설치 토큰이 중앙 DB 덤프·로그 파일·화면·PR 본문·알림 본문에 없음.
- 문서: SELFHOST(러너 붙이기, 알림, App 권한 올리기 절차), docs/github/README.md(초안 PR·병합 추적), CURRENT_HANDOFF(12 완료, 실연동 준비 목록 — 에이전트 전용 pgvector 5434, OpenArchive `scripts/check.sh` 의 pytest 를 `python -m pytest` 로, 원본 폴더 `npm install`, App 권한 승인, Discord 웹훅 URL, 이슈 3건), VERIFICATION_LOG 에 실연동 기록 틀(날짜·이슈·기준 커밋·검증 결과·PR·병합·알림·실패/재작업).
- 제품 코드 변경은 e2e 가 드러낸 결함 수정만(재현 테스트 먼저).

## 테스트 먼저

- `WORKFLOW_E2E=1 python3 -m pytest tests/e2e/test_real_repo.py -q` 통과, 기존 e2e(`WORKFLOW_E2E=1 python3 -m pytest tests/e2e -q`) 회귀 없음.

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
3. 성공이면 `phases/12-real-repo/index.json` 의 step 10 만 `completed` 로 바꾸고 `summary` 에 생성·수정 파일, 결정, 검증 결과(테스트 수), 다음 step 이 주의할 점을 한 줄로 남긴다.
4. 3회 수정 후에도 실패하면 `error` + `error_message`, 사용자 결정·외부 자원이 필요하면 `blocked` + `blocked_reason` 을 기록한다. 실행하지 않은 검증을 `completed` 로 표시하지 않는다.

## 금지사항

- 실제 GitHub(api.github.com·github.com)와 실제 Discord 를 호출하지 않는다(docs.github.com·discord.com/developers 문서 읽기는 허용). 이유: 모든 step 은 httpx `MockTransport`·가짜 GitHub·로컬 bare 저장소로 검증한다. 실연동은 phase 뒤 사용자와 함께 한다.
- `/Users/kje/demo/OpenArchive` 와 그 worktree 를 읽거나 바꾸지 않는다. 이유: 사용자가 개발 중인 실제 저장소다. 테스트는 임시 디렉터리의 가짜 저장소로 한다.
- 사용자가 띄워 둔 셀프호스트(compose 프로젝트 `runloom`, 포트 8000, 볼륨 `runloom_workflow-data`)·`deploy/selfhost/.env`·러너 홈(`~/Library/Application Support/workflow-connector/`)·launchd 를 건드리지 않는다. 컨테이너를 띄우거나 멈추지 않는다.
- 러너가 기본 브랜치에 push 하거나 force push 하지 않는다. 병합·이슈 닫기는 하지 않는다 — 사람만 한다.
- `main`·공개 데모 VM·demo 모드 기본 동작을 바꾸지 않는다.
- 사용자 변경을 삭제하거나 stash·reset 하지 않는다. 커밋은 하네스가 한다.
- 이 step 범위 밖 모듈을 "개선"하지 않는다.
