# Step 5: detail-panel — 상세 패널 — 조각·`?open=`·진행 타임라인·원본에 남긴 것·링크 정리

## 읽어야 할 파일

- AGENTS.md
- phases/16-work-ui/README.md (조사 결과·계획 기본값 9가지 — 이 phase 의 기준), phases/16-work-ui/index.json (이전 step summary)
- docs/ARCHITECTURE.md ("업무 화면 — phase 16" 주소 표·화면 배치)
- src/workflow/server/web.py (`work_detail`·`home`), src/workflow/server/views.py (`work_context`·`cycle_context`·`pull_request_public`·`delivery_public`·`_responses_public`)
- src/workflow/server/templates/work_detail.html·_cycle.html(`response_form`)·home.html·base.html, static/style.css
- src/workflow/server/worker.py (`task_url`)·github_delivery.py (원본 댓글 "상세" 링크), 관련 테스트(test_worker·test_github_delivery·test_web_notifications)

먼저 실제 파일을 읽는다. 대화 이력을 전제로 판단하지 않는다. 이전 step 이 정한 이름·주소는 `docs/ARCHITECTURE.md` "업무 화면 — phase 16" 을 따른다(README 와 다르면 ARCHITECTURE 가 기준).

## 작업

1. `GET /work/{key}/panel` → 패널 조각 템플릿(`_work_panel.html`): 머리(키·원본 링크·상태 배지·닫기) · 속성(담당 선택 폼 — 멤버·후보 에이전트·없음, 우선순위 선택 폼 → step 3 경로, 종류, PR, 업데이트) · 지금 할 일(열린 사람 요청과 기존 `response_form`, 실패면 [다시 맡기기]·[닫기]) · 진행 타임라인(단계마다 종류·상태·실행 횟수 → `/tasks/{task_id}` 링크, PR 열림·병합, 응답 기록과 응답자) · 원본에 남긴 것(GitHub 댓글·초안 PR — 기존 전달 기록에서) · 이어서 생긴 업무·선행 업무(`work_item_links`) · 들어온 곳(체인이면 `/chains/{id}`) · 업무 양식 · 자세히(내부 코드). 있는 절만 보인다. 담당 후보 에이전트는 step 3 규칙과 같은 함수로.
2. `GET /tasks?open=<key>`: 목록과 함께 패널을 서버 렌더(없는 키면 패널 없이 목록 + 짧은 안내). `GET /work/{key}` → 303 `/tasks?open=<key>`(`work_detail.html` 은 지우거나 조각으로 대체).
3. JS: 행·카드 클릭 → `fetch('/work/<key>/panel')` 로 조각을 끼우고 `history.pushState` 로 `open` 만 바꿈, 뒤로 가기·Esc·닫기 버튼, 패널 안 폼은 일반 POST(서버가 `/tasks?open=` 으로 돌려보냄). 조각 로드 실패면 그냥 링크 이동.
4. 알림(`worker.py` `task_url`)·원본 댓글(`github_delivery.py`)의 상세 링크를 업무 주소(`{public_url}/tasks?open=<key>`)로 바꾼다. 업무가 없는 옛 경로는 지금처럼 단계 주소.

## 테스트 먼저

`tests/workflow/server/test_web_work_panel.py`(미러 새 파일): 조각에 절이 조건별로만 나옴(사람 요청 있을 때 응답 폼, 실패일 때 두 버튼, PR 있을 때 링크, 링크 업무·체인·양식), 담당 후보가 능력 있는 에이전트만, `?open=` 서버 렌더·없는 키, `/work/{key}` 303, 다른 워크스페이스 키 404, 권한 없는 사람은 응답 폼 대신 안내, 외부 문자열 이스케이프. `test_worker`·`test_github_delivery`·`test_web_notifications`: 링크가 업무 주소.

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
3. 성공이면 `phases/16-work-ui/index.json` 의 step 5 만 `completed` 로 바꾸고 `summary` 에 생성·수정·삭제 파일, 결정, 검증 결과(테스트 수), 다음 step 이 주의할 점을 한 줄로 남긴다.
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
