# Step 0: selfhost-only-design — `service` 는 셀프호스트만, 결정 고정

## 읽어야 할 파일

- AGENTS.md
- phases/13-selfhost-only/README.md (조사 결과·남기는 것·계획 기본값 — 이 phase 의 기준)
- phases/13-selfhost-only/index.json
- docs/product/REDESIGN_PLAN.md (16절)
- docs/adr/0008-public-demo-scripted-agents.md, docs/adr/0016-selfhost-docker-fixed-workspace.md, docs/adr/0009-registered-kinds-and-succession-rules.md
- docs/ARCHITECTURE.md ("셀프호스트 — phase 10" 의 "모드 — `WORKFLOW_MODE`"·"고정 워크스페이스와 워크스페이스 로그인" 절, "업무 종류와 후속 규칙" 절)
- docs/GLOSSARY.md, docs/README.md, docs/SELFHOST.md, docs/DEPLOY.md
- src/workflow/server/settings.py, src/workflow/server/auth.py, src/workflow/contracts/v1.py (`BUILTIN_KINDS`·`BUILTIN_RULES`)

먼저 실제 파일을 읽는다. 대화 이력을 전제로 판단하지 않는다.

## 작업

문서만 바꾼다(제품 코드·테스트 없음). README "계획 기본값" 네 가지를 결정 기록으로 옮기고 이후 step 이 쓸 이름을 고정한다.

1. `docs/adr/0019-service-selfhost-only.md` 신설. 결정: (1) 모드 개념 삭제 — 앱은 늘 고정 워크스페이스 `sess-selfhost` + 운영자 토큰 로그인, `WORKFLOW_MODE=demo` 가 들어오면 시작 때 설정 오류(`SettingsError` 계열, 메시지에 "service 브랜치는 셀프호스트 전용 — 공개 데모는 main" 취지), 빈 값·`selfhost` 는 허용하되 읽고 버린다; (2) `service` 에서 지울 것 목록(README "조사로 확인한 현재" 전부); (3) 남길 것 목록(README "남기는 것" 과 이유); (4) 내장 종류 `bug_fix`·`code_review` 두 개, 스키마 v9 가 `diagnosis`·`code_change` 종류 행과 그 둘 사이 내장 규칙을 지우며 그 종류를 쓰는 Task·후속 규칙(from/to)이 있으면 마이그레이션 중단; (5) 칸 삭제는 하지 않음(README 4). 대안·기각 이유: 모드 유지(코드 두 벌 유지 비용, 14-task-model 수정 범위), `main` 에서도 제거(공개 데모 심사 ~10/5 동결). ADR-0008 과 ADR-0016 끝에 "service 브랜치에서는 ADR-0019 가 대체" 한 줄을 더한다.
2. `docs/ARCHITECTURE.md`: "모드 — `WORKFLOW_MODE`" 절 머리에 "ADR-0019 로 `service` 에서 폐지 — 아래는 `main` 기록" 을 적고, 새 짧은 절 "셀프호스트 전용 — phase 13" 을 셀프호스트 절 뒤에 둔다(지우는 경로·칸·표 목록, 남기는 것, v9 마이그레이션 규칙). 진단 데모·대본 에이전트를 설명하는 기존 절은 지우지 말고 머리에 "`main` 전용(ADR-0019)" 표시만 한다.
3. `docs/GLOSSARY.md`: `session_id` 의 뜻을 "워크스페이스 키(셀프호스트는 `sess-selfhost` 하나)" 로 고정, 진단 전용 용어(`run`/`run_id`, `workflow_id`, `evidence` 등)에 "`main` 전용" 표시. 용어를 지우지 않는다(`main` 이 쓴다).
4. `docs/README.md` 문서 목록에 ADR-0019 추가, `docs/CURRENT_HANDOFF.md` "다음 작업" 에 "13-selfhost-only 진행 중(phases/13-selfhost-only), 그 뒤 14-task-model" 한 줄.

## 테스트 먼저

문서 전용 step 이라 제품 테스트를 새로 만들지 않는다. 기존 문서 검사(링크·CONTRACT 예시 파싱 등)가 통과해야 한다.

## Acceptance Criteria

```bash
python3 -m pytest -q
python3 -m ruff check .
```

위 커맨드와 이 step 의 테스트 항목을 모두 확인한다.

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 규칙을 확인한다: `domain/` 은 FastAPI·sqlite3·HTTPX·subprocess·Git 을 import 하지 않는다 / `server/` 와 `connector/` 는 서로 import 하지 않고 `contracts/` 만 공유한다 / 외부 입력(요청 본문·이슈 본문·모델 응답)에서 명령·경로·브랜치 이름을 받아 실행하지 않는다 / 비밀값(연결 토큰, `OPERATOR_TOKEN`, `SESSION_SECRET`, GitHub App 비밀·설치 토큰, PAT, 알림 웹훅 URL, 러너 로컬 등록의 환경변수 값)은 DB·로그·응답·템플릿·백업·중앙에 넣지 않는다 / GLOSSARY 이름을 그대로 쓴다 / phase 8·11·12 의 GitHub 순환(수집 → 수정 → 검토 → 초안 PR → 병합 추적)이 그대로 동작한다.
3. 성공이면 `phases/13-selfhost-only/index.json` 의 step 0 만 `completed` 로 바꾸고 `summary` 에 생성·수정·삭제 파일, 결정, 검증 결과(테스트 수), 다음 step 이 주의할 점을 한 줄로 남긴다.
4. 3회 수정 후에도 실패하면 `error` + `error_message`, 사용자 결정·외부 자원이 필요하면 `blocked` + `blocked_reason` 을 기록한다. 실행하지 않은 검증을 `completed` 로 표시하지 않는다.

## 금지사항

- 실제 GitHub(api.github.com·github.com)·Discord·Jira 를 호출하지 않는다(공식 문서 읽기는 허용). 이유: 모든 step 은 httpx `MockTransport`·가짜 GitHub·임시 bare 저장소로 검증한다. 실연동은 phase 뒤 사용자와 함께 한다.
- 사용자가 띄워 둔 셀프호스트(compose 프로젝트 `runloom`, 포트 8000, 볼륨 `runloom_workflow-data`)·`deploy/selfhost/.env`·러너 홈(`~/Library/Application Support/workflow-connector/`)·launchd·`/Users/kje/demo/*`(OpenArchive·runloom-sandbox 클론)를 읽거나 바꾸지 않는다. 컨테이너를 띄우거나 멈추지 않는다. 이유: 실사용 중인 환경이다.
- `main` 브랜치·공개 데모 VM 을 바꾸지 않는다. 이유: 공개 데모 원본(심사 ~2026-10-05 동결)이며 demo 코드의 원본이 거기 남아야 한다.
- 셀프호스트가 쓰는 것(`session_agents`, `sessions.is_operator`, n8n 입구·`chains`, `code_change_result` 산출물 종류, `deploy/selfhost/*`)을 지우지 않는다. 이유: README "남기는 것" — 지우면 실사용이 깨진다.
- 사용자 변경을 삭제하거나 stash·reset 하지 않는다. 커밋은 하네스가 한다.
- 이 step 범위 밖 모듈을 "개선"하지 않는다. 기존 테스트의 단정을 약하게 만들어 통과시키지 않는다.
