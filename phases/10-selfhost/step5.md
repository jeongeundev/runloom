# Step 5: Docker 이미지·compose

## 읽어야 할 파일

- AGENTS.md
- phases/10-selfhost/README.md (계획 기본값 — 이 phase 의 기준)
- phases/10-selfhost/index.json (완료 step 의 summary)
- docs/adr/0016-selfhost-docker-fixed-workspace.md (step 0 산출물. step 0 에서는 새로 만든다)
- docs/ARCHITECTURE.md ("셀프호스트 — phase 10" 절. step 0 에서는 새로 만든다)
- docs/GLOSSARY.md
- pyproject.toml, src/workflow/server/app.py (앱 생성·시작 시 스키마 초기화), src/workflow/server/worker.py (`main`)
- tests/test_deploy_files.py (배포 파일 정적 검사 방식)

먼저 실제 파일과 이전 step 산출물을 읽는다. 이름·환경변수·경로는 step 0 의 ADR-0016·ARCHITECTURE 가 고정한 것을 따르고, 바꿔야 하면 같은 이름의 문서·테스트를 함께 갱신한다. 대화 이력을 전제로 판단하지 않는다.

## 작업

1. `GET /healthz` (인증 없음): DB 연결·스키마 버전 확인 후 `{"status": "ok", "schema_version": N}`. 비밀값·경로를 응답에 넣지 않는다. 서버 테스트를 먼저 쓴다.
2. `Dockerfile`(step 0 이 정한 위치): `python:3.13-slim` 계열, 비 root 사용자, `pip install .`(dev 의존성 제외), 소스만 복사(`.dockerignore` 로 `data/`·`.git`·`phases/`·테스트 산출물·env 파일 제외). 이미지에 비밀값을 넣지 않는다(ARG/ENV 로 토큰 금지).
3. `deploy/selfhost/compose.yaml`: 서비스 `central`(uvicorn, 컨테이너 0.0.0.0:8000 → 호스트 `127.0.0.1:${RUNLOOM_PORT:-8000}`), `worker`(`python -m workflow.server.worker`), 같은 named volume 을 `/data` 에, `env_file: .env`, `WORKFLOW_MODE=selfhost`·DB/산출물 경로 고정, `restart: unless-stopped`, central healthcheck(`/healthz`), worker 는 central healthy 이후 시작.
4. `deploy/selfhost/.env.example`: 키 목록과 설명(비밀값 칸은 비움). `tests/` 의 키 일치 검사에 추가.

## 테스트 먼저

- 서버 테스트: `/healthz` 정상·DB 없음/잘못된 버전 오류, 인증 없이 접근 가능, 응답에 비밀값·경로 없음.
- `tests/test_selfhost_files.py`(새): compose 가 127.0.0.1 에만 포트를 연다, named volume 사용(bind mount 아님), 두 서비스가 같은 볼륨, 모드 selfhost, `.env.example` 키 = 설정 키(비밀값 빈 칸), Dockerfile 비 root·비밀값 ARG/ENV 없음, `.dockerignore` 에 `data/`·`.env`. 가능하면 `docker compose -f deploy/selfhost/compose.yaml config` 가 통과하는지(도커가 없으면 skip).

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
3. 성공이면 `phases/10-selfhost/index.json` 의 step 5 만 `completed` 로 바꾸고 `summary` 에 생성·수정 파일, 결정, 검증 결과(테스트 수), 다음 step 이 주의할 점을 한 줄로 남긴다.
4. 3회 수정 후에도 실패하면 `error` + `error_message`, 사용자 결정·외부 자원이 필요하면 `blocked` + `blocked_reason` 을 기록한다. 실행하지 않은 검증을 `completed` 로 표시하지 않는다.

## 금지사항

- `main`·공개 데모 VM·`deploy/install-vm.sh`·`deploy/systemd/` 를 바꾸지 않는다. 이유: 공개 데모는 기존 경로 그대로 유지한다(ADR-0006·0008).
- 기본 모드(`WORKFLOW_MODE` 미설정 = demo)의 동작을 바꾸지 않는다. 이유: 기존 테스트와 공개 데모가 익명 세션을 전제로 한다.
- 실제 GitHub·유료 모델을 호출하지 않는다.
- step 8 이 아니면 `docker compose up`·`docker run` 으로 컨테이너를 띄우지 않는다(`docker compose config` 같은 정적 검사는 가능). 이유: 실제 기동 검증은 step 8 한 곳에서 격리된 프로젝트 이름·포트로 한다.
- 사용자 변경을 삭제하거나 stash·reset 하지 않는다. `data/` 아래 기존 DB 를 건드리지 않는다. 커밋은 하네스가 한다.
- 이 step 범위 밖 모듈을 "개선"하지 않는다.
