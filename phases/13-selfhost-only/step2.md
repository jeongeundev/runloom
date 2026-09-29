# Step 2: remove-demo-web — 모드 분기·랜딩·익명 세션·카탈로그·fixture 가져오기 삭제

## 읽어야 할 파일

- AGENTS.md
- phases/13-selfhost-only/README.md, phases/13-selfhost-only/index.json (step 0·1 summary)
- docs/adr/0019-service-selfhost-only.md, docs/ARCHITECTURE.md ("셀프호스트 전용 — phase 13" 절)
- src/workflow/server/settings.py, auth.py, web.py, views.py, app.py, machine_api.py, filters.py
- src/workflow/adapters/repo.py (`register_local_agent`, 카탈로그·`shared_to_all_sessions`·`demo_scripted` 사용부), src/workflow/adapters/task_sources.py, src/workflow/adapters/task_source_fixtures/, src/workflow/domain/task_sources.py, src/workflow/domain/composition.py
- src/workflow/server/templates/ (landing.html, agents_register.html, tasks_import.html, home.html, _sidebar.html, _live.html, operator.html, task_new.html, sources.html, login.html, agents.html, agent_detail.html, _chain_live.html, _result_card.html, chain_detail.html), src/workflow/server/static/style.css, static/hero.jpg
- tests/workflow/server/conftest.py (step 1 의 `_demo` fixture), tests/test_deploy_files.py

## 작업

1. **모드**: `Settings.mode`·`MODES` 를 없앤다. `WORKFLOW_MODE` 가 `demo` 이면 설정 읽기에서 오류(ADR-0019 문구), 빈 값·`selfhost` 는 무시. `ENV_KEYS` 에서 빼지 말지 여부는 ADR-0019 를 따른다. `auth.py` 의 `_selfhost()` 분기를 없애고 셀프호스트 동작만 남긴다(`require_session` 은 로그인 없으면 `/login` 303, `require_operator` 는 로그인 = 운영자). `/login`·`/logout` 의 `_require_selfhost` 가드 제거(늘 열림). `/operator/login` 은 `/login` 으로 303 하거나 없앤다 — 어느 쪽인지 ADR 에 한 줄. `/healthz` 응답에서 `mode` 제거(테스트·compose healthcheck 가 그 값을 보는지 확인).
2. **랜딩**: `/` 는 로그인 여부에 따라 업무 목록 또는 `/login` 으로. `landing.html`·`hero.jpg`·전용 CSS 삭제.
3. **카탈로그**: `/agents/register`·`/agents/{id}/unregister`·`_catalog_agents`·`agents_register.html`·`/operator/agents` 의 `shared_to_all_sessions=True` 설정 삭제. repo 의 카탈로그 조회 함수 삭제. `session_agents` 는 남긴다. `register_local_agent` 의 `session_id=None` 분기 제거(늘 워크스페이스).
4. **fixture 업무 가져오기**: `/tasks/import`·`tasks_import.html`·`adapters/task_sources.py`·`task_source_fixtures/`·views 의 `demo_data` 표시 삭제. `domain/task_sources.py`·`composition.py` 는 n8n 입구(`inbound_api.py`)가 쓰는지 확인하고 **쓰는 부분은 남긴다** — 쓰지 않게 된 함수만 지운다.
5. **시연 폼**: `web.EXAMPLES`·`_offers_demo_successor`·`with_successor` 경로 삭제. `_EMPTY_FORM` 기본 종류를 워크스페이스에 등록된 첫 종류(내장 `bug_fix`)로.
6. **대본 표시**: `demo_scripted`·`tag-scripted` 화면 태그 삭제(칸은 DB 에 남긴다 — ADR-0019). 템플릿의 `mode` 분기는 셀프호스트 쪽만 남긴다. 진단 전용 화면 조각(`operator.html` 진단 사용량, `task_new.html` run_id 입력)은 step 3 이 지우므로 여기서는 `mode` 조건만 없애고 진단이 꺼져 있을 때 숨는지 확인한다.
7. 테스트: step 1 의 `_demo` fixture 와 그것을 쓰는 demo 전용 테스트(랜딩·익명 세션·카탈로그·fixture 가져오기·시연 폼)를 지운다. `tests/workflow/adapters/test_task_sources.py` 는 남는 함수만 검사하게 줄인다. `tests/e2e/test_scenario.py` 가 demo 흐름 전용이면 지우고, 셀프호스트에서도 의미 있는 단정은 `test_github_cycle.py`·`test_real_repo.py` 에 이미 있는지 확인한다.

## 테스트 먼저

- 설정: `WORKFLOW_MODE=demo` → 오류, 빈 값·`selfhost` → 정상.
- 인증: 로그인 없이 `/`·`/tasks` → `/login` 303, 로그인 뒤 `/` → 업무 목록.
- 삭제된 경로(`/agents/register`, `/tasks/import`, 랜딩 정적 파일)는 404.
- `/tasks/new` 기본 종류가 `bug_fix`.

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
3. 성공이면 `phases/13-selfhost-only/index.json` 의 step 2 만 `completed` 로 바꾸고 `summary` 에 생성·수정·삭제 파일, 결정, 검증 결과(테스트 수), 다음 step 이 주의할 점을 한 줄로 남긴다.
4. 3회 수정 후에도 실패하면 `error` + `error_message`, 사용자 결정·외부 자원이 필요하면 `blocked` + `blocked_reason` 을 기록한다. 실행하지 않은 검증을 `completed` 로 표시하지 않는다.

## 금지사항

- 실제 GitHub(api.github.com·github.com)·Discord·Jira 를 호출하지 않는다(공식 문서 읽기는 허용). 이유: 모든 step 은 httpx `MockTransport`·가짜 GitHub·임시 bare 저장소로 검증한다. 실연동은 phase 뒤 사용자와 함께 한다.
- 사용자가 띄워 둔 셀프호스트(compose 프로젝트 `runloom`, 포트 8000, 볼륨 `runloom_workflow-data`)·`deploy/selfhost/.env`·러너 홈(`~/Library/Application Support/workflow-connector/`)·launchd·`/Users/kje/demo/*`(OpenArchive·runloom-sandbox 클론)를 읽거나 바꾸지 않는다. 컨테이너를 띄우거나 멈추지 않는다. 이유: 실사용 중인 환경이다.
- `main` 브랜치·공개 데모 VM 을 바꾸지 않는다. 이유: 공개 데모 원본(심사 ~2026-10-05 동결)이며 demo 코드의 원본이 거기 남아야 한다.
- 셀프호스트가 쓰는 것(`session_agents`, `sessions.is_operator`, n8n 입구·`chains`, `code_change_result` 산출물 종류, `deploy/selfhost/*`)을 지우지 않는다. 이유: README "남기는 것" — 지우면 실사용이 깨진다.
- 사용자 변경을 삭제하거나 stash·reset 하지 않는다. 커밋은 하네스가 한다.
- 이 step 범위 밖 모듈을 "개선"하지 않는다. 기존 테스트의 단정을 약하게 만들어 통과시키지 않는다.
