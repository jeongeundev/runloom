# Step 6: 자동 매칭 — 담당 에이전트·로컬 저장소·검증 프로필

## 읽어야 할 파일

- AGENTS.md
- phases/11-github-app/README.md (계획 기본값 — 이 phase 의 기준)
- phases/11-github-app/index.json (완료 step 의 summary)
- docs/adr/0017-github-app-connection.md (step 0 산출물. step 0 에서는 새로 만든다)
- docs/ARCHITECTURE.md ("GitHub App 연결 — phase 11" 절. step 0 에서는 새로 만든다)
- docs/GLOSSARY.md
- src/workflow/domain/task_readiness.py (담당 판정 `assignee_*` 코드), src/workflow/domain/selection.py
- src/workflow/server/task_cycle.py (`_executor`, `evaluate`, `origin_source`), src/workflow/server/worker.py (`_create_cycle_execution` target 조립)
- src/workflow/adapters/repo.py (agents 의 `discovered_json`, `repository_id`, `verification_profile_ids_json`)
- tests/workflow/domain/test_task_readiness.py, tests/workflow/server/ (task_cycle·worker 테스트)

먼저 실제 파일과 이전 step 산출물을 읽는다. 이름·경로·API 필드는 step 0 의 ADR-0017·ARCHITECTURE 가 고정한 것을 따르고, 바꿔야 하면 같은 이름의 문서·테스트를 함께 갱신한다. 대화 이력을 전제로 판단하지 않는다.

## 작업

1. 소스의 비어 있는 칸을 실행 시점에 자동 결정한다(저장하지 않고 매번 계산하거나, 결정값을 표시용으로만 보여준다 — ARCHITECTURE 대로):
   - 로컬 저장소: `discovered.github_repository` 가 소스 저장소와 같은(대소문자 무시) 등록 에이전트들의 `repository_id`.
   - 수정 담당: 설정된 담당자 매핑 → `default_fix_agent_id` → 위 후보 중 `code.fix` 능력 에이전트가 **정확히 하나**면 그것. 둘 이상이면 기존 `assignee_multiple` 처럼 사람 선택 대기, 없으면 새 대기 코드(예: `runner_unmatched` "이 저장소를 등록한 러너 없음", actor operator).
   - 검토 담당: `review_agent_id` → 같은 저장소의 `code.review` 에이전트가 하나면 그것(수정 담당과 같은 에이전트도 허용 여부는 ARCHITECTURE 대로).
   - 검증 프로필: `fix_verification_profile_id` → 그 에이전트 등록의 첫 프로필 → 없으면 대기 코드(예: `verification_profile_missing`).
2. GitHub 이슈에 담당자가 없어도(1인 사용) 위 규칙으로 정해지면 막지 않는다. 담당자가 있고 매핑이 있으면 매핑이 우선.
3. 새 대기 코드는 ARCHITECTURE "준비 판정 — 대기 코드" 표와 화면 문구에 추가한다.

## 테스트 먼저

readiness 테스트: 매핑 없음 + 같은 저장소 에이전트 1개 → 통과, 2개 → 선택 대기, 0개 → runner_unmatched, 기본 담당 우선, 매핑 우선, 프로필 자동·없음 대기, 기존 대기 코드 회귀. worker/task_cycle 테스트: 자동 결정값으로 실행 target 이 만들어짐.

소스·배포 파일 변경 전에 `tests/` 미러 경로에 실패 테스트를 작성하고 실패 원인이 의도한 것인지 확인한다(`tdd-guard.sh` 가 테스트 없는 소스 작성을 막는다). 구현 후 해당 테스트와 전체 회귀를 통과시킨다.

## Acceptance Criteria

```bash
python3 -m pytest -q
python3 -m ruff check .
```

위 커맨드와 이 step 의 테스트 항목을 모두 확인한다.

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 규칙을 확인한다: `domain/` 은 FastAPI·sqlite3·HTTPX·subprocess·Git 을 import 하지 않는다 / `server/` 와 `connector/` 는 서로 import 하지 않는다 / 비밀값(`OPERATOR_TOKEN`, `SESSION_SECRET`, `WORKFLOW_GITHUB_TOKEN`, 연결 토큰 등)은 환경변수·0600 파일에서만 읽고 DB·로그·응답·템플릿·이미지 레이어에 넣지 않는다 / 기본 모드(demo)의 기존 동작과 기존 GitHub 소스 설정(라벨·고른 이슈·환경변수 토큰)이 그대로 동작한다.
3. 성공이면 `phases/11-github-app/index.json` 의 step 6 만 `completed` 로 바꾸고 `summary` 에 생성·수정 파일, 결정, 검증 결과(테스트 수), 다음 step 이 주의할 점을 한 줄로 남긴다.
4. 3회 수정 후에도 실패하면 `error` + `error_message`, 사용자 결정·외부 자원이 필요하면 `blocked` + `blocked_reason` 을 기록한다. 실행하지 않은 검증을 `completed` 로 표시하지 않는다.

## 금지사항

- 실제 GitHub API(api.github.com)·github.com 앱 흐름을 호출하지 않는다(docs.github.com 문서 읽기는 허용). 이유: 모든 step 은 httpx `MockTransport`·가짜 GitHub 로 검증한다. 실제 App 생성·설치는 phase 뒤 사용자 브라우저에서 한다.
- 사용자가 띄워 둔 셀프호스트(compose 프로젝트 `runloom`, 포트 8000, 볼륨 `runloom_workflow-data`)와 `deploy/selfhost/.env` 를 건드리지 않는다. 컨테이너를 띄우거나 멈추지 않는다.
- `main`·공개 데모 VM·demo 모드 기본 동작을 바꾸지 않는다.
- 비밀값(App 개인 키·client secret·webhook secret·설치 토큰·PAT)을 DB·로그·응답·템플릿·예외 문구·백업에 넣지 않는다. 테스트 fixture 의 개인 키는 테스트 안에서 생성한다(저장소에 키 파일을 커밋하지 않는다).
- 사용자 변경을 삭제하거나 stash·reset 하지 않는다. 커밋은 하네스가 한다.
- 이 step 범위 밖 모듈을 "개선"하지 않는다.
