# Step 9: [러너 붙이기] 버튼과 install-runner 한 명령

## 읽어야 할 파일

- AGENTS.md
- phases/12-real-repo/README.md (계획 기본값 — 이 phase 의 기준)
- phases/12-real-repo/index.json (완료 step 의 summary)
- docs/adr/0018-real-repo-cycle.md (step 0 산출물. step 0 에서는 새로 만든다)
- docs/ARCHITECTURE.md ("실제 저장소 순환 — phase 12" 절. step 0 에서는 새로 만든다)
- docs/GLOSSARY.md
- src/workflow/server/templates/operator_github.html (`runner_missing` 안내), src/workflow/server/views.py (`runner_missing`), src/workflow/server/web.py (`/operator/connect-codes`)
- deploy/selfhost/install-runner.sh, tests/test_selfhost_files.py (또는 install-runner 테스트)
- docs/SELFHOST.md (러너 절)

먼저 실제 파일과 이전 step 산출물을 읽는다. 이름·경로·API 필드는 step 0 의 ADR-0018·ARCHITECTURE 가 고정한 것을 따르고, 바꿔야 하면 같은 이름의 문서·테스트를 함께 갱신한다. 대화 이력을 전제로 판단하지 않는다.

## 작업

- 저장소 카드의 "이 저장소를 등록한 러너 없음 — register …" 안내를 [러너 붙이기] 버튼으로 바꾼다. 누르면(운영자 POST) 연결 코드를 발급하고 같은 카드에 복사할 명령 한 줄을 보여 준다: `deploy/selfhost/install-runner.sh --server <이 서버 URL> --code <코드> --repo <이 저장소를 클론한 폴더>` — `<…폴더>` 만 사용자가 바꾼다. 코드 만료 시각(10분)과 [다시 발급]. 러너가 붙으면 카드가 매칭 값을 보여 준다(기존).
- 서버 URL 은 `WORKFLOW_PUBLIC_URL` 이 있으면 그것, 없으면 요청 호스트. 템플릿에 외부 URL 을 직접 쓰지 않는 기존 규칙을 지킨다.
- `install-runner.sh` 에 `--server`·`--code`·`--repo`(+ `--verify`·`--link`·`--env` 전달) 를 더한다: 설치(pip) → `python -m workflow.connector setup …` → launchd 등록·적재. 인자 없이 부르면 기존 동작(설치 + 안내). 연결 코드·env 값을 plist·로그에 남기지 않는다. launchd 환경에 git push·fetch 에 필요한 `HOME` 이 들어가는지 확인한다(`SSH_AUTH_SOCK` 이 없어 ssh 원격은 실패할 수 있음을 SELFHOST 에 적는다).
- `DRY_RUN=1` 로 실행 명령을 출력만 하는 기존 방식으로 테스트한다.
- SELFHOST 러너 절을 버튼 흐름으로 갱신(수동 connect/register 는 "고급").

## 테스트 먼저

- 화면: 러너 없는 카드에 버튼, POST 뒤 코드가 들어간 명령 한 줄, 러너 매칭 뒤 버튼 없음, 운영자 아니면 거부.
- install-runner: `DRY_RUN=1 --server … --code … --repo …` 가 setup 명령과 launchd 단계를 출력, plist 내용에 코드 없음, 인자 없는 기존 동작 불변.

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
3. 성공이면 `phases/12-real-repo/index.json` 의 step 9 만 `completed` 로 바꾸고 `summary` 에 생성·수정 파일, 결정, 검증 결과(테스트 수), 다음 step 이 주의할 점을 한 줄로 남긴다.
4. 3회 수정 후에도 실패하면 `error` + `error_message`, 사용자 결정·외부 자원이 필요하면 `blocked` + `blocked_reason` 을 기록한다. 실행하지 않은 검증을 `completed` 로 표시하지 않는다.

## 금지사항

- 실제 GitHub(api.github.com·github.com)와 실제 Discord 를 호출하지 않는다(docs.github.com·discord.com/developers 문서 읽기는 허용). 이유: 모든 step 은 httpx `MockTransport`·가짜 GitHub·로컬 bare 저장소로 검증한다. 실연동은 phase 뒤 사용자와 함께 한다.
- `/Users/kje/demo/OpenArchive` 와 그 worktree 를 읽거나 바꾸지 않는다. 이유: 사용자가 개발 중인 실제 저장소다. 테스트는 임시 디렉터리의 가짜 저장소로 한다.
- 사용자가 띄워 둔 셀프호스트(compose 프로젝트 `runloom`, 포트 8000, 볼륨 `runloom_workflow-data`)·`deploy/selfhost/.env`·러너 홈(`~/Library/Application Support/workflow-connector/`)·launchd 를 건드리지 않는다. 컨테이너를 띄우거나 멈추지 않는다.
- 러너가 기본 브랜치에 push 하거나 force push 하지 않는다. 병합·이슈 닫기는 하지 않는다 — 사람만 한다.
- `main`·공개 데모 VM·demo 모드 기본 동작을 바꾸지 않는다.
- 사용자 변경을 삭제하거나 stash·reset 하지 않는다. 커밋은 하네스가 한다.
- 이 step 범위 밖 모듈을 "개선"하지 않는다.
