# Step 5: 동기화 — 열린 이슈 전부 가져오기와 트리거 라벨

## 읽어야 할 파일

- AGENTS.md
- phases/11-github-app/README.md (계획 기본값 — 이 phase 의 기준)
- phases/11-github-app/index.json (완료 step 의 summary)
- docs/adr/0017-github-app-connection.md (step 0 산출물. step 0 에서는 새로 만든다)
- docs/ARCHITECTURE.md ("GitHub App 연결 — phase 11" 절. step 0 에서는 새로 만든다)
- docs/GLOSSARY.md
- src/workflow/server/github_sync.py (`_poll`, `_selected`, `_Intake.take`, `sync_source`), src/workflow/domain/issue_intake.py
- src/workflow/server/task_cycle.py (`task_intake_facts`, run_mode), src/workflow/server/worker.py (소스 동기화 호출·클라이언트 생성)
- src/workflow/adapters/github_app.py (step 2), src/workflow/adapters/secret_store.py (step 1)
- tests/workflow/server/test_github_sync.py, tests/workflow/domain/test_issue_intake.py

먼저 실제 파일과 이전 step 산출물을 읽는다. 이름·경로·API 필드는 step 0 의 ADR-0017·ARCHITECTURE 가 고정한 것을 따르고, 바꿔야 하면 같은 이름의 문서·테스트를 함께 갱신한다. 대화 이력을 전제로 판단하지 않는다.

## 작업

1. `intake=all_open` 소스: 필터 없이 **열린** 이슈를 전부 Task 로(PR 제외, 기존 중복·revision 규칙 그대로). 닫힌 이슈는 새로 만들지 않고, 이미 가져온 Task 의 원본 상태만 갱신(기존 동작).
2. 트리거: 이슈에 `trigger_label` 이 있으면 그 Task 는 자동 착수 대상, 없으면 직접 실행 대기(기존 `run_mode=manual` 의미를 Task 단위로). 라벨이 나중에 붙으면(다음 동기화) 자동 착수로 바뀐다. 라벨을 떼도 이미 시작한 실행은 멈추지 않는다.
3. 워커의 GitHub 클라이언트 생성: 소스에 `installation_id` 가 있고 비밀 저장소에 App 자격이 있으면 `GitHubAppAuth` 토큰 공급자 + 설치 저장소 허용 목록, 아니면 비밀 저장소 PAT, 아니면 기존 환경변수 토큰. 어느 것도 없으면 기존 "토큰 없음" 처리.
4. `filtered` 소스·환경변수 토큰 경로는 기존과 같게 동작한다.

## 테스트 먼저

동기화 테스트: all_open 에서 라벨 없는 열린 이슈 전부 Task, PR 제외, 닫힌 이슈 미생성, 재동기화 중복 없음; 트리거 라벨 유무에 따른 자동/대기, 나중에 라벨 추가 시 자동 전환; 클라이언트 선택 우선순위(App → PAT 비밀 → 환경변수); filtered 회귀.

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
3. 성공이면 `phases/11-github-app/index.json` 의 step 5 만 `completed` 로 바꾸고 `summary` 에 생성·수정 파일, 결정, 검증 결과(테스트 수), 다음 step 이 주의할 점을 한 줄로 남긴다.
4. 3회 수정 후에도 실패하면 `error` + `error_message`, 사용자 결정·외부 자원이 필요하면 `blocked` + `blocked_reason` 을 기록한다. 실행하지 않은 검증을 `completed` 로 표시하지 않는다.

## 금지사항

- 실제 GitHub API(api.github.com)·github.com 앱 흐름을 호출하지 않는다(docs.github.com 문서 읽기는 허용). 이유: 모든 step 은 httpx `MockTransport`·가짜 GitHub 로 검증한다. 실제 App 생성·설치는 phase 뒤 사용자 브라우저에서 한다.
- 사용자가 띄워 둔 셀프호스트(compose 프로젝트 `runloom`, 포트 8000, 볼륨 `runloom_workflow-data`)와 `deploy/selfhost/.env` 를 건드리지 않는다. 컨테이너를 띄우거나 멈추지 않는다.
- `main`·공개 데모 VM·demo 모드 기본 동작을 바꾸지 않는다.
- 비밀값(App 개인 키·client secret·webhook secret·설치 토큰·PAT)을 DB·로그·응답·템플릿·예외 문구·백업에 넣지 않는다. 테스트 fixture 의 개인 키는 테스트 안에서 생성한다(저장소에 키 파일을 커밋하지 않는다).
- 사용자 변경을 삭제하거나 stash·reset 하지 않는다. 커밋은 하네스가 한다.
- 이 step 범위 밖 모듈을 "개선"하지 않는다.
