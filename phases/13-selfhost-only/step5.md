# Step 5: selfhost-only-verify — 전체 회귀·셀프호스트 e2e·v8 사본 마이그레이션, 문서

## 읽어야 할 파일

- AGENTS.md
- phases/13-selfhost-only/README.md, phases/13-selfhost-only/index.json (step 0~4 summary)
- docs/adr/0019-service-selfhost-only.md, docs/ARCHITECTURE.md ("셀프호스트 전용 — phase 13" 절)
- tests/e2e/ (conftest.py, test_github_cycle.py, test_github_app.py, test_real_repo.py, test_metrics.py, test_selfhost.py)
- docs/VERIFICATION_LOG.md, docs/CURRENT_HANDOFF.md, docs/SELFHOST.md, docs/product/REDESIGN_PLAN.md (13절 phase 표)
- src/workflow/server/backup.py

## 작업

1. 전체 회귀: `python3 -m pytest -q`, `python3 -m ruff check .`, `WORKFLOW_E2E=1 python3 -m pytest tests/e2e -q`. e2e 가 demo 모드 설정(`WORKFLOW_MODE`, 카탈로그, 대본 에이전트)을 쓰던 곳이 남았으면 셀프호스트 설정으로 고친다.
2. 셀프호스트 Docker e2e: `WORKFLOW_DOCKER=1 python3 -m pytest tests/e2e/test_selfhost.py -q`. **이 테스트가 사용자의 compose 프로젝트 `runloom`·포트 8000·볼륨 `runloom_workflow-data` 와 겹치는지 먼저 테스트 코드를 읽어 확인한다** — 겹치면 실행하지 말고 blocked 가 아니라 "미실행(이유)" 로 summary 에 적는다. Docker 데몬이 없어도 같다.
3. v8 → v9 사본 확인: 저장소 테스트 안에서 v8 스키마 DB 를 만들어(현재 셀프호스트와 같은 모양: 워크스페이스 1개, 내장 4종류, `bug_fix`·`code_review` Task 여러 개, `github_sources`·`source_issues`·`baseline_items`·`task_pull_requests`·`notifications` 행) `init_schema` 로 올리고, 행 수 보존·두 종류 삭제·백업 CLI(`backup create`/`restore`) 왕복을 단정하는 테스트를 `tests/workflow/adapters/test_db.py` 또는 `tests/workflow/server/test_backup.py` 에 둔다. **사용자의 실제 셀프호스트 볼륨·백업 파일을 읽지 않는다.**
4. 문서: `docs/VERIFICATION_LOG.md` 에 "2026-09-29 phase 13 셀프호스트 전용" 절(명령·결과 수·미실행 항목), `docs/SELFHOST.md` 업그레이드 절에 "v9 — 진단 종류 삭제, 백업 먼저" 한 줄, `docs/product/REDESIGN_PLAN.md` 13절 표를 새 번호(13-selfhost-only, 14-task-model, 15-team, 16-work-ui, 17-jira, 18-triage, 19-monitor)로 고치고 본문의 phase 번호 참조를 맞춘다, `docs/CURRENT_HANDOFF.md` "다음 작업" 을 "13 완료 → service 병합 → 14-task-model(phases/14-task-model)" 로.

## 테스트 먼저

3 의 마이그레이션·백업 왕복 테스트를 먼저 작성해 실패(또는 이미 통과)를 확인한 뒤 진행한다. 이 step 은 새 제품 기능이 없다 — 제품 코드를 고쳐야 하면 그 결함을 재현하는 테스트를 먼저 쓴다.

소스·배포 파일 변경 전에 `tests/` 미러 경로에 실패 테스트를 작성하고 실패 원인이 의도한 것인지 확인한다(`tdd-guard.sh` 가 테스트 없는 소스 작성을 막는다). 구현 후 해당 테스트와 전체 회귀를 통과시킨다.

## Acceptance Criteria

```bash
python3 -m pytest -q
python3 -m ruff check .
WORKFLOW_E2E=1 python3 -m pytest tests/e2e -q   # e2e 를 건드린 step
```

위 커맨드와 이 step 의 테스트 항목을 모두 확인한다.

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 규칙을 확인한다: `domain/` 은 FastAPI·sqlite3·HTTPX·subprocess·Git 을 import 하지 않는다 / `server/` 와 `connector/` 는 서로 import 하지 않고 `contracts/` 만 공유한다 / 외부 입력(요청 본문·이슈 본문·모델 응답)에서 명령·경로·브랜치 이름을 받아 실행하지 않는다 / 비밀값(연결 토큰, `OPERATOR_TOKEN`, `SESSION_SECRET`, GitHub App 비밀·설치 토큰, PAT, 알림 웹훅 URL, 러너 로컬 등록의 환경변수 값)은 DB·로그·응답·템플릿·백업·중앙에 넣지 않는다 / GLOSSARY 이름을 그대로 쓴다 / phase 8·11·12 의 GitHub 순환(수집 → 수정 → 검토 → 초안 PR → 병합 추적)이 그대로 동작한다.
3. 성공이면 `phases/13-selfhost-only/index.json` 의 step 5 만 `completed` 로 바꾸고 `summary` 에 생성·수정·삭제 파일, 결정, 검증 결과(테스트 수), 다음 step 이 주의할 점을 한 줄로 남긴다.
4. 3회 수정 후에도 실패하면 `error` + `error_message`, 사용자 결정·외부 자원이 필요하면 `blocked` + `blocked_reason` 을 기록한다. 실행하지 않은 검증을 `completed` 로 표시하지 않는다.

## 금지사항

- 실제 GitHub(api.github.com·github.com)·Discord·Jira 를 호출하지 않는다(공식 문서 읽기는 허용). 이유: 모든 step 은 httpx `MockTransport`·가짜 GitHub·임시 bare 저장소로 검증한다. 실연동은 phase 뒤 사용자와 함께 한다.
- 사용자가 띄워 둔 셀프호스트(compose 프로젝트 `runloom`, 포트 8000, 볼륨 `runloom_workflow-data`)·`deploy/selfhost/.env`·러너 홈(`~/Library/Application Support/workflow-connector/`)·launchd·`/Users/kje/demo/*`(OpenArchive·runloom-sandbox 클론)를 읽거나 바꾸지 않는다. 컨테이너를 띄우거나 멈추지 않는다. 이유: 실사용 중인 환경이다.
- `main` 브랜치·공개 데모 VM 을 바꾸지 않는다. 이유: 공개 데모 원본(심사 ~2026-10-05 동결)이며 demo 코드의 원본이 거기 남아야 한다.
- 셀프호스트가 쓰는 것(`session_agents`, `sessions.is_operator`, n8n 입구·`chains`, `code_change_result` 산출물 종류, `deploy/selfhost/*`)을 지우지 않는다. 이유: README "남기는 것" — 지우면 실사용이 깨진다.
- 사용자 변경을 삭제하거나 stash·reset 하지 않는다. 커밋은 하네스가 한다.
- 이 step 범위 밖 모듈을 "개선"하지 않는다. 기존 테스트의 단정을 약하게 만들어 통과시키지 않는다.
