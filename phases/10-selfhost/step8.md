# Step 8: 실제 Docker 로 설치·보존·백업 검증

## 읽어야 할 파일

- AGENTS.md
- phases/10-selfhost/README.md (계획 기본값 — 이 phase 의 기준)
- phases/10-selfhost/index.json (완료 step 의 summary)
- docs/adr/0016-selfhost-docker-fixed-workspace.md (step 0 산출물. step 0 에서는 새로 만든다)
- docs/ARCHITECTURE.md ("셀프호스트 — phase 10" 절. step 0 에서는 새로 만든다)
- docs/GLOSSARY.md
- docs/SELFHOST.md (step 7), deploy/selfhost/ 전부
- tests/e2e/ (게이트 방식: `WORKFLOW_E2E`)
- docs/VERIFICATION_LOG.md, docs/CURRENT_HANDOFF.md, docs/product/MVP_PLAN.md (7절 배포 행)

먼저 실제 파일과 이전 step 산출물을 읽는다. 이름·환경변수·경로는 step 0 의 ADR-0016·ARCHITECTURE 가 고정한 것을 따르고, 바꿔야 하면 같은 이름의 문서·테스트를 함께 갱신한다. 대화 이력을 전제로 판단하지 않는다.

## 작업

이 step 만 이 Mac 의 Docker 로 실제 컨테이너를 띄운다. 외부 호출·비용 없음.

1. `tests/e2e/test_selfhost.py`(`WORKFLOW_DOCKER=1` 게이트, 없으면 skip): 임시 디렉터리에 현재 작업 트리를 `git clone`(또는 `git worktree`/복사 — 커밋된 내용 기준) → `RUNLOOM_PROJECT=runloom-e2e-<랜덤> RUNLOOM_PORT=<빈 포트>` 로 install.sh → `/healthz` ok → `.env` 의 운영자 토큰으로 로그인(httpx) → 데이터 생성(예: 종류 하나 등록 또는 업무 하나) → `docker compose down`(볼륨 유지) → install.sh 재실행 → 같은 데이터가 보임 → 컨테이너 안에서 backup create·list → 데이터 하나 더 만든 뒤 서비스 정지·restore → 백업 시점 데이터로 돌아옴. 끝나면 `down -v` 로 **이 테스트가 만든 프로젝트만** 정리(finally). 다른 compose 프로젝트·볼륨을 건드리지 않는다.
2. 실행해 통과시킨다: `WORKFLOW_DOCKER=1 python3 -m pytest tests/e2e/test_selfhost.py -q`.
3. 문서: VERIFICATION_LOG 에 실행 기록(날짜, Docker 버전, 소요 시간, 결과), CURRENT_HANDOFF 에 phase 10 상태·다음(`11-real-repo`), MVP_PLAN 7절 배포 행 갱신.

## 테스트 먼저

e2e 자체가 이 step 의 테스트다. 기존 전체 테스트와 `WORKFLOW_E2E=1` e2e 도 통과해야 한다.

소스·배포 파일 변경 전에 `tests/` 미러 경로(배포 파일은 `tests/test_deploy_files.py` 또는 새 `tests/test_selfhost_files.py`)에 실패 테스트를 작성하고 실패 원인이 의도한 것인지 확인한다(`tdd-guard.sh` 가 테스트 없는 소스 작성을 막는다). 구현 후 해당 테스트와 전체 회귀를 통과시킨다.

## Acceptance Criteria

```bash
python3 -m pytest -q
python3 -m ruff check .
WORKFLOW_E2E=1 python3 -m pytest tests/e2e -q
WORKFLOW_DOCKER=1 python3 -m pytest tests/e2e/test_selfhost.py -q
```

위 커맨드와 이 step 의 테스트 항목을 모두 확인한다.

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 규칙을 확인한다: `domain/` 은 FastAPI·sqlite3·HTTPX·subprocess·Git 을 import 하지 않는다 / `server/` 와 `connector/` 는 서로 import 하지 않는다 / 비밀값(`OPERATOR_TOKEN`, `SESSION_SECRET`, `WORKFLOW_GITHUB_TOKEN`, 연결 토큰 등)은 환경변수·0600 파일에서만 읽고 DB·로그·응답·템플릿·이미지 레이어에 넣지 않는다 / 기본 모드(demo)의 기존 동작과 테스트가 그대로다.
3. 성공이면 `phases/10-selfhost/index.json` 의 step 8 만 `completed` 로 바꾸고 `summary` 에 생성·수정 파일, 결정, 검증 결과(테스트 수), 다음 step 이 주의할 점을 한 줄로 남긴다.
4. 3회 수정 후에도 실패하면 `error` + `error_message`, 사용자 결정·외부 자원이 필요하면 `blocked` + `blocked_reason` 을 기록한다. 실행하지 않은 검증을 `completed` 로 표시하지 않는다.

## 금지사항

- `main`·공개 데모 VM·`deploy/install-vm.sh`·`deploy/systemd/` 를 바꾸지 않는다. 이유: 공개 데모는 기존 경로 그대로 유지한다(ADR-0006·0008).
- 기본 모드(`WORKFLOW_MODE` 미설정 = demo)의 동작을 바꾸지 않는다. 이유: 기존 테스트와 공개 데모가 익명 세션을 전제로 한다.
- 실제 GitHub·유료 모델을 호출하지 않는다.
- step 8 이 아니면 `docker compose up`·`docker run` 으로 컨테이너를 띄우지 않는다(`docker compose config` 같은 정적 검사는 가능). 이유: 실제 기동 검증은 step 8 한 곳에서 격리된 프로젝트 이름·포트로 한다.
- 사용자 변경을 삭제하거나 stash·reset 하지 않는다. `data/` 아래 기존 DB 를 건드리지 않는다. 커밋은 하네스가 한다.
- 이 step 범위 밖 모듈을 "개선"하지 않는다.
- 이 테스트가 만든 compose 프로젝트 외의 컨테이너·볼륨·이미지를 지우지 않는다. `docker system prune` 류를 쓰지 않는다.
