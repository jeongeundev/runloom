# Step 4: work-list-ui — 업무 화면 — 한 줄 표·묶음·필터·목록/보드·새 사이드바

## 읽어야 할 파일

- AGENTS.md
- phases/16-work-ui/README.md (조사 결과·계획 기본값 9가지 — 이 phase 의 기준), phases/16-work-ui/index.json (이전 step summary)
- docs/ARCHITECTURE.md ("업무 화면 — phase 16" 주소 표·화면 배치), docs/UI_GUIDE.md (step 0 이 고친 셸·업무 화면)
- src/workflow/server/web.py (`home`·`_base`·`_render`), src/workflow/server/views.py (step 2 문맥 함수)
- src/workflow/server/templates/base.html·_sidebar.html·home.html·_status.html, src/workflow/server/static/style.css
- tests/workflow/server/test_ui.py·test_web.py·test_web_my_turn.py·test_web_roles.py

먼저 실제 파일을 읽는다. 대화 이력을 전제로 판단하지 않는다. 이전 step 이 정한 이름·주소는 `docs/ARCHITECTURE.md` "업무 화면 — phase 16" 을 따른다(README 와 다르면 ARCHITECTURE 가 기준).

## 작업

1. `GET /tasks`: step 2 문맥으로 `home.html` 을 새로 쓴다. 도구 막대(빠른 필터 4개 + 건수, 묶기, 보기, [업무 등록] → `/tasks/new`), 목록 보기 = 표(`<table>`, 묶음마다 머리 행 — 담당 이름·건수·접기 버튼, 칸 8개: 키·제목·담당·우선순위·종류·상태·다음 할 일·업데이트), 보드 보기 = 6칸 카드(키·우선순위·제목·담당·다음 할 일). 행·카드는 `<a href="/tasks?...&open=<key>">` 로 JS 없이도 동작한다. 원본 표시(GitHub·n8n·직접)는 글자 배지(아이콘 라이브러리 없이), 우선순위는 텍스트 + 기호, 상태는 기존 `_status.html` 배지(색 + 한글). 빈 목록·빈 필터는 빈 상태 문장과 다음 행동 버튼.
2. 에이전트 카드·워크플로우 카드는 홈에서 뺀다(에이전트는 연결 화면 — step 6, 체인은 업무 패널의 "들어온 곳" — step 5). 에이전트가 0 이면 도구 막대 아래에 "러너를 붙이면 에이전트가 생깁니다" 한 줄과 러너 붙이는 화면 링크.
3. `_sidebar.html`: 업무(내 차례 수 배지) · 모니터링 · 연결 · 시작하기 · 내 설정, 아래 로그인 멤버·로그아웃. "최근" 목록과 `+` 제거, `_base()["my_work"]` 가 더 쓰이지 않으면 제거. 아직 없는 주소(`/connect`·`/monitor`·`/start`)는 이 step 에서 기존 화면(`/operator/github`·`/metrics`)으로 가는 링크로 두고 step 6·7 이 바꾼다(시작하기는 step 7 전까지 숨김). 권한 없는 항목은 지금처럼 숨긴다.
4. 소량 JS(`base.html` 인라인 또는 `static/` 의 새 `.js` 하나): 묶음 접기(접힌 묶음은 `localStorage` 에 — 읽기·쓰기를 try/catch, 실패해도 동작). 필터·묶기·보기는 서버 이동 링크(JS 없이). 행 키보드 접근(Tab·Enter).
5. CSS: 업무 화면만 본문 폭 제한 해제, 표는 가로 스크롤 컨테이너(390px 에서 페이지 가로 스크롤 없음), 기존 토큰 색 재사용(보라·그라데이션 금지 — UI_GUIDE).

## 테스트 먼저

`tests/workflow/server/test_ui.py`(또는 미러 새 파일 `test_web_work_list.py`): 표 칸 머리 8개, 담당자 묶음 순서·건수, 상태 묶음, 빠른 필터 4개 결과·건수, 보드 6칸과 카드 배치, 행 링크가 `open=<key>` 이고 다른 쿼리 값을 유지, 모르는 쿼리 값 → 기본값, 외부 제목의 `<script>` 가 이스케이프됨, 사이드바 새 항목·"최근" 없음·권한별 숨김, 내 차례 수 배지, 빈 상태 문장. 기존 홈 단정(에이전트 카드·체인 카드·`data-view`)은 새 화면 기준으로 옮긴다.

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
3. 성공이면 `phases/16-work-ui/index.json` 의 step 4 만 `completed` 로 바꾸고 `summary` 에 생성·수정·삭제 파일, 결정, 검증 결과(테스트 수), 다음 step 이 주의할 점을 한 줄로 남긴다.
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
