# Step 2: 러너 — `setup` 한 명령과 로컬 등록 새 칸

## 읽어야 할 파일

- AGENTS.md
- phases/12-real-repo/README.md (계획 기본값 — 이 phase 의 기준)
- phases/12-real-repo/index.json (완료 step 의 summary)
- docs/adr/0018-real-repo-cycle.md (step 0 산출물. step 0 에서는 새로 만든다)
- docs/ARCHITECTURE.md ("실제 저장소 순환 — phase 12" 절. step 0 에서는 새로 만든다)
- docs/GLOSSARY.md
- src/workflow/connector/cli.py (`connect`·`register`·`_register`), src/workflow/connector/config.py, src/workflow/connector/state.py (registrations 테이블), src/workflow/connector/discovery.py (`found.github_repository`)
- tests/workflow/connector/test_cli.py, tests/workflow/connector/test_state.py (없으면 미러 경로에 만든다)

먼저 실제 파일과 이전 step 산출물을 읽는다. 이름·경로·API 필드는 step 0 의 ADR-0018·ARCHITECTURE 가 고정한 것을 따르고, 바꿔야 하면 같은 이름의 문서·테스트를 함께 갱신한다. 대화 이력을 전제로 판단하지 않는다.

## 작업

러너만 바꾼다.

- `python3 -m workflow.connector setup --server URL --code CODE --repo 폴더 [--id 이름] [--tool claude|codex] [--verify 이름=명령 ...] [--link 경로 ...] [--env 이름=값 ...]`: connect(토큰 교환·`token.json`)와 register 를 한 번에. `--id` 생략 = 폴더 이름(소문자, 허용 문자 외는 `-`), `--repository-id` 생략 = discovery 가 찾은 GitHub owner/name, 없으면 폴더 이름. `--tool` 생략 = PATH 에 있는 `claude` 우선 그다음 `codex`. 이미 `token.json` 이 같은 서버로 있으면 connect 를 건너뛴다(코드는 1회용이므로).
- `register` 에도 `--link`·`--env` 를 추가하고 `--id`·`--repository-id` 를 선택으로 만든다(기본값은 setup 과 같은 규칙). 기존 인자 조합은 그대로 동작.
- 로컬 `state.sqlite` registrations 에 `links_json`·`env_json` 칸 추가. `CREATE TABLE IF NOT EXISTS` 만 쓰는 현재 구조에서 기존 DB 도 열리도록 칸이 없으면 `ALTER TABLE` 로 더한다(ADR-0018 이 정한 방식).
- `--link` 는 등록 폴더 기준 상대 경로만(절대 경로·`..`·`.git` 거부). `--env` 이름은 `[A-Z_][A-Z0-9_]*`, AGENTS.md 비밀값 이름·`WORKFLOW_*`·`PATH`·`HOME` 거부. 값은 로컬 DB 에만 저장하고 중앙 요청·로그·출력에 넣지 않는다(이름도 중앙에 보내지 않는다).
- 끝에 한 줄 요약: 등록 이름, GitHub 저장소, 검증 프로필 이름들, 링크 수, 환경변수 이름들(값 없음), 다음 할 일(`run` 또는 install-runner).
- 링크·환경변수를 실제로 쓰는 것은 step 4 다. 이 step 은 저장·검증·기본값까지.

## 테스트 먼저

- setup: 가짜 서버(httpx MockTransport 또는 기존 테스트 방식)로 교환 → register 요청 본문 확인(id·repository_id 기본값, env 값·이름 없음).
- `token.json` 있을 때 connect 생략.
- `--link ../x`·`/abs`·`.git/hooks` 거부, `--env OPENAI_API_KEY=…`·`PATH=…` 거부.
- 칸 없는 옛 `state.sqlite` 를 열면 칸이 더해지고 기존 등록이 남는다.
- 출력·로그 문자열에 env 값이 없다.

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
3. 성공이면 `phases/12-real-repo/index.json` 의 step 2 만 `completed` 로 바꾸고 `summary` 에 생성·수정 파일, 결정, 검증 결과(테스트 수), 다음 step 이 주의할 점을 한 줄로 남긴다.
4. 3회 수정 후에도 실패하면 `error` + `error_message`, 사용자 결정·외부 자원이 필요하면 `blocked` + `blocked_reason` 을 기록한다. 실행하지 않은 검증을 `completed` 로 표시하지 않는다.

## 금지사항

- 실제 GitHub(api.github.com·github.com)와 실제 Discord 를 호출하지 않는다(docs.github.com·discord.com/developers 문서 읽기는 허용). 이유: 모든 step 은 httpx `MockTransport`·가짜 GitHub·로컬 bare 저장소로 검증한다. 실연동은 phase 뒤 사용자와 함께 한다.
- `/Users/kje/demo/OpenArchive` 와 그 worktree 를 읽거나 바꾸지 않는다. 이유: 사용자가 개발 중인 실제 저장소다. 테스트는 임시 디렉터리의 가짜 저장소로 한다.
- 사용자가 띄워 둔 셀프호스트(compose 프로젝트 `runloom`, 포트 8000, 볼륨 `runloom_workflow-data`)·`deploy/selfhost/.env`·러너 홈(`~/Library/Application Support/workflow-connector/`)·launchd 를 건드리지 않는다. 컨테이너를 띄우거나 멈추지 않는다.
- 러너가 기본 브랜치에 push 하거나 force push 하지 않는다. 병합·이슈 닫기는 하지 않는다 — 사람만 한다.
- `main`·공개 데모 VM·demo 모드 기본 동작을 바꾸지 않는다.
- 사용자 변경을 삭제하거나 stash·reset 하지 않는다. 커밋은 하네스가 한다.
- 이 step 범위 밖 모듈을 "개선"하지 않는다.
