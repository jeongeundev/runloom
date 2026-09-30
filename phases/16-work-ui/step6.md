# Step 6: connect-tabs — 연결 화면 — 탭 5개로 모으고 옛 주소 넘기기

## 읽어야 할 파일

- AGENTS.md
- phases/16-work-ui/README.md (조사 결과·계획 기본값 9가지 — 이 phase 의 기준), phases/16-work-ui/index.json (이전 step summary)
- docs/ARCHITECTURE.md ("업무 화면 — phase 16" 연결 탭 대응 표·바꾸지 않는 경로·권한)
- src/workflow/server/web.py (`/sources`·`/operator`·`/operator/github`·`/operator/notifications`·`/team`·`/agents`·`/kinds` GET 과 그 POST 들의 303 대상), src/workflow/server/views.py (`github_context`·`notifications_context`·`team_context`·`agent_public`·`kind_public`·`rule_public`)
- src/workflow/server/templates/sources.html·operator.html·operator_github.html·operator_notifications.html·team.html·agents.html·kinds.html·_sidebar.html
- tests/workflow/server/test_web.py·test_web_roles.py·test_web_team.py·test_web_github_connect.py·test_web_runner.py·test_web_runner_owner.py·test_web_notifications.py·test_github_api.py, tests/e2e/*.py(옛 주소 GET 을 쓰는 곳)

먼저 실제 파일을 읽는다. 대화 이력을 전제로 판단하지 않는다. 이전 step 이 정한 이름·주소는 `docs/ARCHITECTURE.md` "업무 화면 — phase 16" 을 따른다(README 와 다르면 ARCHITECTURE 가 기준).

## 작업

1. `GET /connect?tab=...`: 탭 머리 + 탭 본문. 본문은 기존 템플릿 내용을 부분 템플릿으로 옮겨 재사용한다(새로 설계하지 않는다 — 문구·폼·`data-*` 표시 유지): 가져올 곳 = GitHub(App 연결·저장소 카드·러너 붙이기·기준선) + n8n 입구, 팀·담당자 = 멤버·초대 + 에이전트 목록 + 러너(연결 프로그램·소유자), 업무 종류·규칙 = `/kinds` 내용, 알림 = 공용 알림, 고급 = 운영자 화면의 나머지(연결 코드·입구 토큰 등). 탭별 권한은 기존 화면의 권한 그대로 — 권한 없는 탭은 머리에서 숨기고 직접 열면 403. 모르는 `tab` 은 첫 번째 볼 수 있는 탭.
2. 옛 GET(`/sources`·`/operator`·`/operator/github`·`/operator/notifications`·`/team`·`/agents`·`/kinds`) → 303 새 탭. POST 경로는 그대로, 성공 뒤 303 대상은 새 탭 주소로 바꾼다(오류 화면 재렌더가 옛 템플릿을 쓰면 탭 안에서 같은 오류가 보이게). `/operator/github/app/*`·콜백·setup, `/sources/{id}`, `/agents/{id}` 는 주소 유지(연결 탭에서 링크).
3. 사이드바의 연결 링크를 `/connect` 로. 문서(`docs/SELFHOST.md`·`docs/github/*`·`deploy/selfhost/install*.sh` 출력 문구)에 옛 화면 주소가 있으면 새 주소로 고친다(옛 주소도 넘어가므로 찾은 것만).

## 테스트 먼저

`tests/workflow/server/test_web_connect.py`(미러 새 파일): 탭 5개와 역할별 보임·403, 각 탭에 옮긴 핵심 요소(`data-*`·폼 action)가 있음, 옛 GET 7개 → 303 대상, 대표 POST(소스 저장·초대 발급·알림 저장·종류 등록)의 303 대상이 새 탭, App 콜백 경로가 그대로. 기존 화면 테스트는 옛 GET 대신 새 탭 주소로 옮긴다(단정 내용은 그대로).

소스·템플릿 변경 전에 `tests/` 미러 경로에 실패 테스트를 작성하고 실패 원인이 의도한 것인지 확인한다(`tdd-guard.sh` 가 테스트 없는 소스 작성을 막는다). 구현 후 해당 테스트와 전체 회귀를 통과시킨다. 이 step 의 변경으로 기존 테스트가 깨지면 새 동작 기준으로 고치되 단정을 약하게 만들지 않는다(무엇을 왜 바꿨는지 summary 에 남긴다).

## Acceptance Criteria

```bash
python3 -m pytest -q
python3 -m ruff check .
```

위 커맨드와 이 step 의 테스트 항목을 모두 확인한다.

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 규칙을 확인한다: `domain/` 은 FastAPI·sqlite3·HTTPX·subprocess·Git 을 import 하지 않는다 / `server/` 와 `connector/` 는 서로 import 하지 않고 `contracts/` 만 공유한다 / 외부 입력(요청 본문·폼·쿼리 문자열·PR 제목·브랜치 이름·이슈 본문·모델 응답)에서 명령·경로를 받아 실행하지 않는다 — 주소 쿼리 값은 열거형으로만 받고 되돌아갈 URL 을 받지 않는다 / 비밀값(연결 토큰, `OPERATOR_TOKEN`, `SESSION_SECRET`, 로그인 세션·초대·재설정 토큰 원문, 비밀번호, GitHub App 비밀·설치 토큰, PAT, 알림 웹훅 URL)은 DB·로그·응답·템플릿·백업에 넣지 않는다 / 권한은 `domain/team.py` 역할 × 동작 표로만 판정한다 / 템플릿은 PR 제목·이슈 제목 같은 외부 문자열을 자동 이스케이프로만 출력한다(`|safe` 금지) / GLOSSARY 이름을 그대로 쓴다 / phase 8·11·12·14·15 의 GitHub 순환(수집 → 맡기기 → 수정 → 검토 → 초안 PR → 병합 추적 → 업무 완료)과 사람별 내 차례가 그대로 동작한다.
3. 성공이면 `phases/16-work-ui/index.json` 의 step 6 만 `completed` 로 바꾸고 `summary` 에 생성·수정·삭제 파일, 결정, 검증 결과(테스트 수), 다음 step 이 주의할 점을 한 줄로 남긴다.
4. 3회 수정 후에도 실패하면 `error` + `error_message`, 사용자 결정·외부 자원이 필요하면 `blocked` + `blocked_reason` 을 기록한다. 실행하지 않은 검증을 `completed` 로 표시하지 않는다.

## 금지사항

- 실제 GitHub(api.github.com·github.com)·Discord·외부 웹훅을 호출하지 않는다(공식 문서 읽기는 허용). 이유: 모든 step 은 httpx `MockTransport`·가짜 GitHub·가짜 알림 수신으로 검증한다. 실연동은 phase 뒤 사용자와 함께 한다.
- 사용자가 띄워 둔 셀프호스트(compose 프로젝트 `runloom`, 포트 8000, 볼륨 `runloom_workflow-data`)·`deploy/selfhost/.env`·러너 홈(`~/Library/Application Support/workflow-connector/`)·launchd·`~/.claude/`·`/Users/kje/demo/*` 를 읽거나 바꾸지 않는다. 컨테이너를 띄우거나 멈추지 않는다. 이유: 실사용 중인 환경이다.
- 새 의존성(Python 패키지, JS 프레임워크·번들러, 외부 CDN·웹폰트·아이콘 라이브러리)을 추가하지 않는다. 이유: ADR-0002·UI_GUIDE — Jinja2 서버 렌더 + CSS + 소량 인라인 JS.
- 종류 이름(`bug_fix`·`code_review`)으로 새 분기를 만들지 않는다. 이유: 업무 종류·후속 규칙은 워크스페이스 등록 데이터다(ADR-0009, AGENTS.md).
- `tasks` 표를 재생성하거나 `tasks.status` CHECK 를 바꾸지 않는다. 이유: ADR-0020 — 업무 상태는 `work_items` 에, 단계 상태는 그대로.
- 판단 제안·확신도·자동 시작(18-triage), Jira(17-jira), 모니터링 지표 확장(19-monitor), Claude Code 훅(다음 phase), 보드 끌어 옮기기, 브랜치 push 감지, 여러 행 일괄 변경을 만들지 않는다. 이유: 사용자 결정 2026-09-30 범위 밖.
- 직접 작업을 Runloom 결과 판정·완료 판정으로 처리하지 않는다. 이유: 완료는 PR 병합 같은 원본 신호로만(REDESIGN_PLAN 10절, 계약 v1).
- 사용자 변경을 삭제하거나 stash·reset 하지 않는다. 커밋은 하네스가 한다.
- 이 step 범위 밖 모듈을 "개선"하지 않는다. 기존 테스트의 단정을 약하게 만들어 통과시키지 않는다.
