# Step 4: remove-demo-deploy — 대본 에이전트·VM 배포 파일·데모 스크립트 삭제

## 읽어야 할 파일

- AGENTS.md
- phases/13-selfhost-only/README.md, phases/13-selfhost-only/index.json (step 0~3 summary)
- docs/adr/0019-service-selfhost-only.md
- src/workflow/scripted/, src/workflow/connector/masking.py (`WORKFLOW_SCRIPT_PACE_SECONDS`)
- deploy/ 전체 목록, deploy/selfhost/ (남길 것), docs/DEPLOY.md, docs/SELFHOST.md
- scripts/ 목록 (`execute.py`·`hooks/`·`test_execute.py`·`test_hooks.py` 는 하네스 — 남긴다), pyproject.toml (`testpaths`)
- tests/test_deploy_files.py, tests/test_selfhost_files.py, tests/test_packages.py, tests/test_n8n_example.py

## 작업

1. `src/workflow/scripted/`·`tests/workflow/scripted/` 삭제. `masking.py` 의 `WORKFLOW_SCRIPT_PACE_SECONDS` 통과 규칙 삭제(테스트 갱신).
2. VM 배포 파일 삭제: `deploy/bin/`, `deploy/env/`, `deploy/systemd/`, `deploy/launchd/`, `deploy/install-vm.sh`, `deploy/update-vm.sh`, `deploy/backup.sh`, `deploy/Caddyfile`. `deploy/selfhost/` 는 건드리지 않는다. 먼저 `deploy/selfhost/*`·`tests/test_selfhost_files.py`·docs 가 지울 파일을 참조하는지 grep 으로 확인하고, 참조가 있으면 셀프호스트가 실제로 쓰는지 판단해 쓰면 남기고 summary 에 이유를 적는다.
3. 데모 스크립트 삭제: `scripts/seed_demo.py`, `local_stack.py`, `scaffold_demo_repo.py`, `diag_eval.py`, `make_handoff_dir.py` 와 각 `scripts/test_*`. 하네스(`execute.py`·`hooks/`)는 남긴다.
4. `tests/test_deploy_files.py`: VM 파일 검사를 지운다. 남는 검사가 없으면 파일째 지우고, `settings.ENV_KEYS` 와 셀프호스트 `.env.example` 일치 검사가 `test_selfhost_files.py` 에 있는지 확인해 없으면 그쪽으로 옮긴다.
5. `docs/DEPLOY.md` 는 머리에 "공개 데모 VM 배포 — `main` 브랜치 전용(ADR-0019). `service` 에는 해당 파일이 없다" 를 적고 본문은 둔다(기록). `docs/SELFHOST.md` 에서 데모 러너(`com.workflow.connector`)와의 충돌 주의 문단은 "`main` 데모 러너를 같은 Mac 에 둔 경우" 로 줄인다.
6. AGENTS.md: "`src/workflow/scripted/` 는 공개 데모 전용…" 문장 삭제, 명령어 절에 셀프호스트 명령만 남았는지 확인.

## 테스트 먼저

- `tests/test_packages.py`: `workflow.scripted`·`diagnostic_demo` import 불가, `workflow` 패키지 import 가능.
- masking: 대본 속도 변수가 더는 통과하지 않음.
- 삭제 뒤 `python3 -m pytest -q` 가 수집 오류 없이 통과(지운 모듈을 import 하는 테스트가 남지 않음).

소스·배포 파일 변경 전에 `tests/` 미러 경로에 실패 테스트를 작성하고 실패 원인이 의도한 것인지 확인한다(`tdd-guard.sh` 가 테스트 없는 소스 작성을 막는다). 구현 후 해당 테스트와 전체 회귀를 통과시킨다.

## Acceptance Criteria

```bash
python3 -m pytest -q
python3 -m ruff check .
```

위 커맨드와 이 step 의 테스트 항목을 모두 확인한다.

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 규칙을 확인한다: `domain/` 은 FastAPI·sqlite3·HTTPX·subprocess·Git 을 import 하지 않는다 / `server/` 와 `connector/` 는 서로 import 하지 않고 `contracts/` 만 공유한다 / 외부 입력(요청 본문·이슈 본문·모델 응답)에서 명령·경로·브랜치 이름을 받아 실행하지 않는다 / 비밀값(연결 토큰, `OPERATOR_TOKEN`, `SESSION_SECRET`, GitHub App 비밀·설치 토큰, PAT, 알림 웹훅 URL, 러너 로컬 등록의 환경변수 값)은 DB·로그·응답·템플릿·백업·중앙에 넣지 않는다 / GLOSSARY 이름을 그대로 쓴다 / phase 8·11·12 의 GitHub 순환(수집 → 수정 → 검토 → 초안 PR → 병합 추적)이 그대로 동작한다.
3. 성공이면 `phases/13-selfhost-only/index.json` 의 step 4 만 `completed` 로 바꾸고 `summary` 에 생성·수정·삭제 파일, 결정, 검증 결과(테스트 수), 다음 step 이 주의할 점을 한 줄로 남긴다.
4. 3회 수정 후에도 실패하면 `error` + `error_message`, 사용자 결정·외부 자원이 필요하면 `blocked` + `blocked_reason` 을 기록한다. 실행하지 않은 검증을 `completed` 로 표시하지 않는다.

## 금지사항

- 실제 GitHub(api.github.com·github.com)·Discord·Jira 를 호출하지 않는다(공식 문서 읽기는 허용). 이유: 모든 step 은 httpx `MockTransport`·가짜 GitHub·임시 bare 저장소로 검증한다. 실연동은 phase 뒤 사용자와 함께 한다.
- 사용자가 띄워 둔 셀프호스트(compose 프로젝트 `runloom`, 포트 8000, 볼륨 `runloom_workflow-data`)·`deploy/selfhost/.env`·러너 홈(`~/Library/Application Support/workflow-connector/`)·launchd·`/Users/kje/demo/*`(OpenArchive·runloom-sandbox 클론)를 읽거나 바꾸지 않는다. 컨테이너를 띄우거나 멈추지 않는다. 이유: 실사용 중인 환경이다.
- `main` 브랜치·공개 데모 VM 을 바꾸지 않는다. 이유: 공개 데모 원본(심사 ~2026-10-05 동결)이며 demo 코드의 원본이 거기 남아야 한다.
- 셀프호스트가 쓰는 것(`session_agents`, `sessions.is_operator`, n8n 입구·`chains`, `code_change_result` 산출물 종류, `deploy/selfhost/*`)을 지우지 않는다. 이유: README "남기는 것" — 지우면 실사용이 깨진다.
- 사용자 변경을 삭제하거나 stash·reset 하지 않는다. 커밋은 하네스가 한다.
- 이 step 범위 밖 모듈을 "개선"하지 않는다. 기존 테스트의 단정을 약하게 만들어 통과시키지 않는다.
