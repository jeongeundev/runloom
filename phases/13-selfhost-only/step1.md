# Step 1: selfhost-fixtures — 테스트 기반을 셀프호스트·`bug_fix` 기준으로

## 읽어야 할 파일

- AGENTS.md
- phases/13-selfhost-only/README.md, phases/13-selfhost-only/index.json (step 0 summary)
- docs/adr/0019-service-selfhost-only.md (step 0 산출물)
- docs/ARCHITECTURE.md ("셀프호스트 전용 — phase 13" 절)
- tests/conftest.py, tests/workflow/server/conftest.py (`Settings` 기본값, `seed_agents`, `register_catalog`, `task_row`, `request_body`), tests/workflow/adapters/conftest.py, tests/workflow/connector/conftest.py, tests/e2e/conftest.py
- src/workflow/server/auth.py (`ensure_workspace`, `workspace_session`, `require_session`), src/workflow/adapters/repo.py (`create_session`, `register_local_agent`, `session_agents` 관련 함수)
- 셀프호스트로 이미 도는 테스트 예: tests/workflow/server/test_web_runner.py, test_web_notifications.py, test_web_github_connect.py

## 작업

코드 삭제 전에 **테스트 쪽 기반만** 옮긴다. 제품 코드(`src/`)는 바꾸지 않는다. 이 step 이 끝났을 때 전체 테스트가 지금 코드 그대로 통과해야 한다 — 그래야 step 2·3 이 코드를 지울 때 "무엇이 깨졌는지" 가 분명하다.

1. `tests/workflow/server/conftest.py`: 기본 `Settings` 를 `mode="selfhost"` + `OPERATOR_TOKEN` 설정값으로 바꾸고, 테스트 클라이언트가 워크스페이스 로그인(`/login` 폼 또는 `ensure_workspace` + 세션 쿠키 발급 도우미)을 거친 상태를 기본으로 준다. 도우미 이름을 하나로 고정한다(예: `logged_in_client`). demo 익명 세션을 전제로 한 fixture 는 이름 뒤에 `_demo` 를 붙여 남긴다 — step 2 가 그것을 쓰는 테스트와 함께 지운다.
2. `seed_agents`: 카탈로그(`shared_to_all_sessions=True` + `register_catalog`) 대신 셀프호스트 경로(러너 register 가 Agent 를 만들고 `session_agents` 로 워크스페이스에 붙이는 것과 같은 repo 함수)로 만든다. 능력은 `code.fix`·`code.review`.
3. `task_row`·`request_body` 의 기본 종류를 `bug_fix`(검토는 `code_review`)로. 진단·`code_change` 를 명시적으로 원하는 테스트는 인자로 넘기게 한다.
4. 기존 테스트 중 기본값 변경으로 깨지는 것을 고친다. 판단 기준: **셀프호스트에서도 의미 있는 동작을 검사하는 테스트는 새 기본값으로 고치고**, demo 전용 동작(랜딩·익명 세션·카탈로그·fixture 가져오기·진단·대본 에이전트)을 검사하는 테스트는 `_demo` fixture 를 명시적으로 쓰게 해 그대로 둔다(삭제는 step 2·3·4). 고친 테스트의 단정(assert)을 약하게 만들지 않는다.
5. 옮기지 못한 테스트가 있으면 파일:함수 목록을 summary 에 남긴다.

## 테스트 먼저

이 step 은 테스트 기반 자체가 산출물이다. 바꾼 뒤 `python3 -m pytest -q` 전체가 통과해야 하고, 통과 수·skip 수를 바꾸기 전과 비교해 summary 에 적는다(줄었다면 이유).

## Acceptance Criteria

```bash
python3 -m pytest -q
python3 -m ruff check .
```

위 커맨드와 이 step 의 테스트 항목을 모두 확인한다.

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 규칙을 확인한다: `domain/` 은 FastAPI·sqlite3·HTTPX·subprocess·Git 을 import 하지 않는다 / `server/` 와 `connector/` 는 서로 import 하지 않고 `contracts/` 만 공유한다 / 외부 입력(요청 본문·이슈 본문·모델 응답)에서 명령·경로·브랜치 이름을 받아 실행하지 않는다 / 비밀값(연결 토큰, `OPERATOR_TOKEN`, `SESSION_SECRET`, GitHub App 비밀·설치 토큰, PAT, 알림 웹훅 URL, 러너 로컬 등록의 환경변수 값)은 DB·로그·응답·템플릿·백업·중앙에 넣지 않는다 / GLOSSARY 이름을 그대로 쓴다 / phase 8·11·12 의 GitHub 순환(수집 → 수정 → 검토 → 초안 PR → 병합 추적)이 그대로 동작한다.
3. 성공이면 `phases/13-selfhost-only/index.json` 의 step 1 만 `completed` 로 바꾸고 `summary` 에 생성·수정·삭제 파일, 결정, 검증 결과(테스트 수), 다음 step 이 주의할 점을 한 줄로 남긴다.
4. 3회 수정 후에도 실패하면 `error` + `error_message`, 사용자 결정·외부 자원이 필요하면 `blocked` + `blocked_reason` 을 기록한다. 실행하지 않은 검증을 `completed` 로 표시하지 않는다.

## 금지사항

- 실제 GitHub(api.github.com·github.com)·Discord·Jira 를 호출하지 않는다(공식 문서 읽기는 허용). 이유: 모든 step 은 httpx `MockTransport`·가짜 GitHub·임시 bare 저장소로 검증한다. 실연동은 phase 뒤 사용자와 함께 한다.
- 사용자가 띄워 둔 셀프호스트(compose 프로젝트 `runloom`, 포트 8000, 볼륨 `runloom_workflow-data`)·`deploy/selfhost/.env`·러너 홈(`~/Library/Application Support/workflow-connector/`)·launchd·`/Users/kje/demo/*`(OpenArchive·runloom-sandbox 클론)를 읽거나 바꾸지 않는다. 컨테이너를 띄우거나 멈추지 않는다. 이유: 실사용 중인 환경이다.
- `main` 브랜치·공개 데모 VM 을 바꾸지 않는다. 이유: 공개 데모 원본(심사 ~2026-10-05 동결)이며 demo 코드의 원본이 거기 남아야 한다.
- 셀프호스트가 쓰는 것(`session_agents`, `sessions.is_operator`, n8n 입구·`chains`, `code_change_result` 산출물 종류, `deploy/selfhost/*`)을 지우지 않는다. 이유: README "남기는 것" — 지우면 실사용이 깨진다.
- 사용자 변경을 삭제하거나 stash·reset 하지 않는다. 커밋은 하네스가 한다.
- 이 step 범위 밖 모듈을 "개선"하지 않는다. 기존 테스트의 단정을 약하게 만들어 통과시키지 않는다.
