# Step 1: 중앙 — register 가 에이전트를 만든다

## 읽어야 할 파일

- AGENTS.md
- phases/12-real-repo/README.md (계획 기본값 — 이 phase 의 기준)
- phases/12-real-repo/index.json (완료 step 의 summary)
- docs/adr/0018-real-repo-cycle.md (step 0 산출물. step 0 에서는 새로 만든다)
- docs/ARCHITECTURE.md ("실제 저장소 순환 — phase 12" 절. step 0 에서는 새로 만든다)
- docs/GLOSSARY.md
- src/workflow/server/machine_api.py (`register`), src/workflow/adapters/repo.py (`update_registration`, 에이전트 생성 함수), src/workflow/contracts/v1.py (`RegistrationRequest`)
- src/workflow/domain/github_match.py (자동 매칭 — 같은 에이전트가 수정·검토를 모두 맡을 수 있는지 확인)
- tests/workflow/server/test_machine_api.py (또는 register 테스트가 있는 파일), tests/workflow/domain/test_github_match.py

먼저 실제 파일과 이전 step 산출물을 읽는다. 이름·경로·API 필드는 step 0 의 ADR-0018·ARCHITECTURE 가 고정한 것을 따르고, 바꿔야 하면 같은 이름의 문서·테스트를 함께 갱신한다. 대화 이력을 전제로 판단하지 않는다.

## 작업

중앙만 바꾼다(러너 CLI 는 step 2).

- `POST /connector/registrations` 에서 `local_registration_id` 에 맞는 에이전트가 없으면 404 대신 **그 연결 프로그램의 워크스페이스에 에이전트를 만든다**: 이름 = `local_registration_id`, 도구 = 요청의 `tool`, 능력 = 내장 `code.fix`·`code.review`(범위 = 요청의 `repository_id`), 연결 정보 = 기존 `update_registration` 과 같다. 응답은 기존 `{"agent_id"}` 에 ADR-0018 이 정한 "새로 만듦" 표시를 더한다.
- 같은 `local_registration_id` 로 다시 register 하면 새로 만들지 않고 갱신한다(멱등). 다른 연결 프로그램이 이미 쓰는 이름이면 409 로 거부한다(기존 규칙이 있으면 따른다).
- `repository_id` 는 요청값을 그대로 쓴다(러너가 GitHub owner/name 을 넣는다 — step 2). 서버가 이름 규칙을 추측하지 않는다.
- 자동 매칭(`github_match`)에서 한 에이전트가 `code.fix`·`code.review` 를 모두 가질 때 수정·검토 모두 그 에이전트로 정해지는지 확인하고, 막는 규칙(서버 검사 포함)이 있으면 ADR-0018 에 따라 허용으로 고친다(수정과 검토는 별도 실행·별도 worktree 다).
- 운영자 화면에 새로 만들어진 에이전트가 기존 에이전트처럼 보인다(템플릿 변경이 필요 없으면 바꾸지 않는다).

## 테스트 먼저

- 모르는 이름으로 register → 에이전트 생성, 능력 두 개, 범위 = repository_id, 연결 프로그램·기준 커밋·검증 프로필·discovered 저장.
- 같은 이름 두 번 → 에이전트 하나(멱등), 두 번째는 갱신.
- 미리 만든 에이전트 이름으로 register → 기존 동작 그대로(새로 만들지 않음).
- 다른 연결 프로그램이 쓰는 이름 → 거부.
- 자동 매칭: 두 능력을 가진 에이전트 하나뿐인 저장소 → 수정·검토 모두 그 에이전트, 대기 코드 없음.

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
3. 성공이면 `phases/12-real-repo/index.json` 의 step 1 만 `completed` 로 바꾸고 `summary` 에 생성·수정 파일, 결정, 검증 결과(테스트 수), 다음 step 이 주의할 점을 한 줄로 남긴다.
4. 3회 수정 후에도 실패하면 `error` + `error_message`, 사용자 결정·외부 자원이 필요하면 `blocked` + `blocked_reason` 을 기록한다. 실행하지 않은 검증을 `completed` 로 표시하지 않는다.

## 금지사항

- 실제 GitHub(api.github.com·github.com)와 실제 Discord 를 호출하지 않는다(docs.github.com·discord.com/developers 문서 읽기는 허용). 이유: 모든 step 은 httpx `MockTransport`·가짜 GitHub·로컬 bare 저장소로 검증한다. 실연동은 phase 뒤 사용자와 함께 한다.
- `/Users/kje/demo/OpenArchive` 와 그 worktree 를 읽거나 바꾸지 않는다. 이유: 사용자가 개발 중인 실제 저장소다. 테스트는 임시 디렉터리의 가짜 저장소로 한다.
- 사용자가 띄워 둔 셀프호스트(compose 프로젝트 `runloom`, 포트 8000, 볼륨 `runloom_workflow-data`)·`deploy/selfhost/.env`·러너 홈(`~/Library/Application Support/workflow-connector/`)·launchd 를 건드리지 않는다. 컨테이너를 띄우거나 멈추지 않는다.
- 러너가 기본 브랜치에 push 하거나 force push 하지 않는다. 병합·이슈 닫기는 하지 않는다 — 사람만 한다.
- `main`·공개 데모 VM·demo 모드 기본 동작을 바꾸지 않는다.
- 사용자 변경을 삭제하거나 stash·reset 하지 않는다. 커밋은 하네스가 한다.
- 이 step 범위 밖 모듈을 "개선"하지 않는다.
