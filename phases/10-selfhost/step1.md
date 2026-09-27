# Step 1: 설정 — 셀프호스트 모드

## 읽어야 할 파일

- AGENTS.md
- phases/10-selfhost/README.md (계획 기본값 — 이 phase 의 기준)
- phases/10-selfhost/index.json (완료 step 의 summary)
- docs/adr/0016-selfhost-docker-fixed-workspace.md (step 0 산출물. step 0 에서는 새로 만든다)
- docs/ARCHITECTURE.md ("셀프호스트 — phase 10" 절. step 0 에서는 새로 만든다)
- docs/GLOSSARY.md
- src/workflow/server/settings.py (`ENV_KEYS`, `SECRET_KEYS`, `load_settings`)
- src/workflow/server/app.py, src/workflow/server/worker.py (설정을 쓰는 곳, 진단 클라이언트 생성)
- tests/workflow/server/ (settings 테스트), tests/test_deploy_files.py (env 예시 키 일치 검사)

먼저 실제 파일과 이전 step 산출물을 읽는다. 이름·환경변수·경로는 step 0 의 ADR-0016·ARCHITECTURE 가 고정한 것을 따르고, 바꿔야 하면 같은 이름의 문서·테스트를 함께 갱신한다. 대화 이력을 전제로 판단하지 않는다.

## 작업

1. `Settings.mode: Literal["demo", "selfhost"]` — `WORKFLOW_MODE` 에서 읽는다. 비면 `demo`, 다른 값은 ValueError. `ENV_KEYS` 에 추가하고, 기존 `deploy/env/central.env.example` 은 키 일치 검사를 통과하도록 **빈 값 줄 하나만** 추가한다(값은 비워 demo 유지).
2. selfhost 에서는 `DIAG_API_TOKEN` 이 선택이다(비면 진단 기능 꺼짐). demo 에서는 지금처럼 필수. `SESSION_SECRET`·`OPERATOR_TOKEN` 은 두 모드 모두 필수.
3. 진단 토큰이 없을 때 서버·워커가 진단 클라이언트를 만들지 않고, 진단 실행 요청은 명확한 오류(예: "진단 기능이 꺼져 있음")로 거부되게 한다 — 기존 진단 경로에서 토큰을 쓰는 곳을 찾아 최소한으로 막는다.

## 테스트 먼저

settings 테스트: 모드 기본값 demo, selfhost, 잘못된 값 거부, selfhost 에서 진단 토큰 없이 로드 성공, demo 에서 진단 토큰 없으면 기존처럼 실패. 서버 테스트: selfhost·진단 토큰 없음에서 앱 시작과 진단 실행 거부. 배포 파일 검사 통과.

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
3. 성공이면 `phases/10-selfhost/index.json` 의 step 1 만 `completed` 로 바꾸고 `summary` 에 생성·수정 파일, 결정, 검증 결과(테스트 수), 다음 step 이 주의할 점을 한 줄로 남긴다.
4. 3회 수정 후에도 실패하면 `error` + `error_message`, 사용자 결정·외부 자원이 필요하면 `blocked` + `blocked_reason` 을 기록한다. 실행하지 않은 검증을 `completed` 로 표시하지 않는다.

## 금지사항

- `main`·공개 데모 VM·`deploy/install-vm.sh`·`deploy/systemd/` 를 바꾸지 않는다. 이유: 공개 데모는 기존 경로 그대로 유지한다(ADR-0006·0008).
- 기본 모드(`WORKFLOW_MODE` 미설정 = demo)의 동작을 바꾸지 않는다. 이유: 기존 테스트와 공개 데모가 익명 세션을 전제로 한다.
- 실제 GitHub·유료 모델을 호출하지 않는다.
- step 8 이 아니면 `docker compose up`·`docker run` 으로 컨테이너를 띄우지 않는다(`docker compose config` 같은 정적 검사는 가능). 이유: 실제 기동 검증은 step 8 한 곳에서 격리된 프로젝트 이름·포트로 한다.
- 사용자 변경을 삭제하거나 stash·reset 하지 않는다. `data/` 아래 기존 DB 를 건드리지 않는다. 커밋은 하네스가 한다.
- 이 step 범위 밖 모듈을 "개선"하지 않는다.
