# Step 0: 셀프호스트 설계 고정 — ADR·ARCHITECTURE·GLOSSARY

## 읽어야 할 파일

- AGENTS.md
- phases/10-selfhost/README.md (계획 기본값 — 이 phase 의 기준)
- phases/10-selfhost/index.json (완료 step 의 summary)
- docs/adr/0016-selfhost-docker-fixed-workspace.md (step 0 산출물. step 0 에서는 새로 만든다)
- docs/ARCHITECTURE.md ("셀프호스트 — phase 10" 절. step 0 에서는 새로 만든다)
- docs/GLOSSARY.md
- docs/product/MVP_PLAN.md (8절 배포 경로, 10절 셀프호스트 범위, 11절 phase 계획)
- docs/adr/0005-access-model-anonymous-session-operator-token.md, docs/adr/0006-*.md, docs/adr/0008-public-demo-scripted-agents.md
- docs/DEPLOY.md (공개 데모 VM 런북 — 바꾸지 않는다, 참고만)
- src/workflow/server/settings.py, src/workflow/server/auth.py, src/workflow/server/web.py (세션·운영자 흐름)
- src/workflow/server/app.py, src/workflow/server/worker.py (`main`), src/workflow/adapters/db.py (`connect`·WAL)
- deploy/ (env 예시, backup.sh, launchd plist), tests/test_deploy_files.py

먼저 실제 파일과 이전 step 산출물을 읽는다. 이름·환경변수·경로는 step 0 의 ADR-0016·ARCHITECTURE 가 고정한 것을 따르고, 바꿔야 하면 같은 이름의 문서·테스트를 함께 갱신한다. 대화 이력을 전제로 판단하지 않는다.

## 작업

문서만 바꾼다. README "계획 기본값"을 결정 기록으로 옮기고 이후 step 이 쓸 이름을 고정한다.

1. `docs/adr/0016-selfhost-docker-fixed-workspace.md` 신설(기존 ADR 형식). 결정: 셀프호스트는 Docker compose(central·worker, named volume, 127.0.0.1 공개) + 러너는 호스트 네이티브(launchd) / `WORKFLOW_MODE=selfhost` 에서 고정 워크스페이스 하나 + `OPERATOR_TOKEN` 로그인, 익명 세션 없음 / 기본 모드 demo 는 기존 그대로 / 진단 데모 제외, `DIAG_API_TOKEN` 선택 / 백업·복원 CLI / 새 설치는 빈 DB. ADR-0005·0006 은 "공개 데모에 한정"으로 범위를 좁힌다고 적고, 두 ADR 파일에 한 줄 "범위: 공개 데모. 셀프호스트는 ADR-0016" 을 덧붙인다. 대안과 기각 이유(macOS 네이티브 전용, 별도 상시 서버, 로그인 없음, bind mount)를 적는다.
2. `docs/ARCHITECTURE.md` 에 "셀프호스트 — phase 10" 절:
   - 구성도(호스트 Mac: compose central·worker ↔ 볼륨 / 네이티브 러너 → 127.0.0.1:포트).
   - 모드 표: demo vs selfhost 에서 세션 생성·로그인·운영자 판정·진단·데모 전용 화면이 어떻게 다른가.
   - 고정 워크스페이스 규칙: 워크스페이스 세션 id 는 DB 에 하나(예: 첫 로그인 때 `is_operator=1` 로 만들고 이후 재사용 — 식별 방식을 정해 적는다). 로그인 쿠키는 그 id 를 `SESSION_SECRET` 으로 서명. 로그인 실패는 토큰 값을 로그에 남기지 않는다. 로그아웃은 쿠키 삭제.
   - 이름 고정: 환경변수 `WORKFLOW_MODE`(`demo`|`selfhost`), 경로(`deploy/selfhost/compose.yaml`, `deploy/selfhost/Dockerfile` 또는 저장소 루트 `Dockerfile` — 하나로 정한다, `deploy/selfhost/install.sh`, `deploy/selfhost/install-runner.sh`, `deploy/selfhost/.env.example`), 볼륨 이름, 컨테이너 안 데이터 경로(`/data/central.sqlite`, `/data/artifacts`, `/data/backups`), 공개 포트 기본 8000(환경변수로 바꿈), `GET /healthz` 응답 형식, 백업 CLI `python3 -m workflow.server.backup create|restore|list` 인자.
   - SQLite 두 프로세스(central·worker 컨테이너)가 같은 named volume 의 WAL DB 를 쓰는 조건과, Mac bind mount 를 쓰지 않는 이유.
   - 러너가 compose 밖에서 붙는 방법(연결 코드 발급 → `connect` → `register` → launchd).
3. `docs/GLOSSARY.md`: 셀프호스트 모드, 고정 워크스페이스, 워크스페이스 로그인 — 화면 라벨과 코드 식별자.

## 테스트 먼저

문서 전용 step 이라 제품 테스트를 새로 만들지 않는다. 기존 문서·배포 파일 검사(`tests/test_deploy_files.py`, 문서 링크 검사)가 그대로 통과해야 한다.

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
3. 성공이면 `phases/10-selfhost/index.json` 의 step 0 만 `completed` 로 바꾸고 `summary` 에 생성·수정 파일, 결정, 검증 결과(테스트 수), 다음 step 이 주의할 점을 한 줄로 남긴다.
4. 3회 수정 후에도 실패하면 `error` + `error_message`, 사용자 결정·외부 자원이 필요하면 `blocked` + `blocked_reason` 을 기록한다. 실행하지 않은 검증을 `completed` 로 표시하지 않는다.

## 금지사항

- `main`·공개 데모 VM·`deploy/install-vm.sh`·`deploy/systemd/` 를 바꾸지 않는다. 이유: 공개 데모는 기존 경로 그대로 유지한다(ADR-0006·0008).
- 기본 모드(`WORKFLOW_MODE` 미설정 = demo)의 동작을 바꾸지 않는다. 이유: 기존 테스트와 공개 데모가 익명 세션을 전제로 한다.
- 실제 GitHub·유료 모델을 호출하지 않는다.
- step 8 이 아니면 `docker compose up`·`docker run` 으로 컨테이너를 띄우지 않는다(`docker compose config` 같은 정적 검사는 가능). 이유: 실제 기동 검증은 step 8 한 곳에서 격리된 프로젝트 이름·포트로 한다.
- 사용자 변경을 삭제하거나 stash·reset 하지 않는다. `data/` 아래 기존 DB 를 건드리지 않는다. 커밋은 하네스가 한다.
- 이 step 범위 밖 모듈을 "개선"하지 않는다.
