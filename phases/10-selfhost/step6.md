# Step 6: 설치 스크립트 — 한 명령 설치와 러너 설정

## 읽어야 할 파일

- AGENTS.md
- phases/10-selfhost/README.md (계획 기본값 — 이 phase 의 기준)
- phases/10-selfhost/index.json (완료 step 의 summary)
- docs/adr/0016-selfhost-docker-fixed-workspace.md (step 0 산출물. step 0 에서는 새로 만든다)
- docs/ARCHITECTURE.md ("셀프호스트 — phase 10" 절. step 0 에서는 새로 만든다)
- docs/GLOSSARY.md
- deploy/selfhost/ (step 5 산출물), deploy/launchd/com.workflow.connector.plist (참고)
- src/workflow/connector/config.py (러너 경로), src/workflow/connector/cli.py (`connect`·`register`)
- scripts/test_*.py (셸 스크립트를 가짜 바이너리로 테스트하는 방식이 있으면 따른다)

먼저 실제 파일과 이전 step 산출물을 읽는다. 이름·환경변수·경로는 step 0 의 ADR-0016·ARCHITECTURE 가 고정한 것을 따르고, 바꿔야 하면 같은 이름의 문서·테스트를 함께 갱신한다. 대화 이력을 전제로 판단하지 않는다.

## 작업

1. `deploy/selfhost/install.sh`(멱등, `set -euo pipefail`):
   - `docker`·`docker compose` 확인, 없으면 설치 안내 후 종료.
   - `deploy/selfhost/.env` 가 없으면 `.env.example` 을 복사하고 `SESSION_SECRET`·`OPERATOR_TOKEN` 을 무작위 생성(`openssl rand -hex 32` 또는 python secrets), 0600. 있으면 절대 덮어쓰지 않는다.
   - `docker compose -p ${RUNLOOM_PROJECT:-runloom} up -d --build`, `/healthz` 가 ok 가 될 때까지 대기(시간 초과 시 로그 안내 후 실패).
   - 접속 주소, 운영자 토큰 **파일 위치**(값은 출력하지 않는다), 다음 할 일(로그인 → 연결 코드 발급 → 러너 설정)을 출력.
   - 다시 실행하면 이미지 재빌드·재기동(업그레이드). 데이터 볼륨은 유지.
2. `deploy/selfhost/install-runner.sh`(macOS): `python3 -m pip install -e .`(또는 안내), 현재 사용자의 홈·python·PATH(claude/codex 위치 포함)를 채운 launchd plist 를 `~/Library/LaunchAgents/` 에 쓰고 `launchctl` 로 적재. 러너 서버 주소는 `http://127.0.0.1:${RUNLOOM_PORT:-8000}`. 연결 코드(`connect`)와 저장소 등록(`register`)은 사용자가 할 명령으로 출력만 한다(토큰을 스크립트가 다루지 않는다).
3. 두 스크립트 모두 `--help` 와 `DRY_RUN=1`(실행 대신 할 일 출력)을 둔다 — 테스트용.

## 테스트 먼저

`tests/test_selfhost_files.py` 또는 scripts 테스트: 가짜 `docker`·`launchctl`·`curl` 을 PATH 앞에 둔 임시 디렉터리에서 install.sh 실행 → `.env` 생성·0600·비밀값 채움, 재실행 시 `.env` 불변, compose 인자(프로젝트 이름·파일) 확인, 헬스 대기 실패 시 비정상 종료; 출력에 토큰 값 없음. install-runner.sh `DRY_RUN=1` 이 plist 경로·내용(실제 홈 치환, 토큰 없음)을 낸다.

소스·배포 파일 변경 전에 `tests/` 미러 경로(배포 파일은 `tests/test_deploy_files.py` 또는 새 `tests/test_selfhost_files.py`)에 실패 테스트를 작성하고 실패 원인이 의도한 것인지 확인한다(`tdd-guard.sh` 가 테스트 없는 소스 작성을 막는다). 구현 후 해당 테스트와 전체 회귀를 통과시킨다.

## Acceptance Criteria

```bash
python3 -m pytest -q
python3 -m ruff check .
```

위 커맨드와 이 step 의 테스트 항목을 모두 확인한다.

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 규칙을 확인한다: `domain/` 은 FastAPI·sqlite3·HTTPX·subprocess·Git 을 import 하지 않는다 / `server/` 와 `connector/` 는 서로 import 하지 않는다 / 비밀값(`OPERATOR_TOKEN`, `SESSION_SECRET`, `WORKFLOW_GITHUB_TOKEN`, 연결 토큰 등)은 환경변수·0600 파일에서만 읽고 DB·로그·응답·템플릿·이미지 레이어에 넣지 않는다 / 기본 모드(demo)의 기존 동작과 테스트가 그대로다.
3. 성공이면 `phases/10-selfhost/index.json` 의 step 6 만 `completed` 로 바꾸고 `summary` 에 생성·수정 파일, 결정, 검증 결과(테스트 수), 다음 step 이 주의할 점을 한 줄로 남긴다.
4. 3회 수정 후에도 실패하면 `error` + `error_message`, 사용자 결정·외부 자원이 필요하면 `blocked` + `blocked_reason` 을 기록한다. 실행하지 않은 검증을 `completed` 로 표시하지 않는다.

## 금지사항

- `main`·공개 데모 VM·`deploy/install-vm.sh`·`deploy/systemd/` 를 바꾸지 않는다. 이유: 공개 데모는 기존 경로 그대로 유지한다(ADR-0006·0008).
- 기본 모드(`WORKFLOW_MODE` 미설정 = demo)의 동작을 바꾸지 않는다. 이유: 기존 테스트와 공개 데모가 익명 세션을 전제로 한다.
- 실제 GitHub·유료 모델을 호출하지 않는다.
- step 8 이 아니면 `docker compose up`·`docker run` 으로 컨테이너를 띄우지 않는다(`docker compose config` 같은 정적 검사는 가능). 이유: 실제 기동 검증은 step 8 한 곳에서 격리된 프로젝트 이름·포트로 한다.
- 사용자 변경을 삭제하거나 stash·reset 하지 않는다. `data/` 아래 기존 DB 를 건드리지 않는다. 커밋은 하네스가 한다.
- 이 step 범위 밖 모듈을 "개선"하지 않는다.
