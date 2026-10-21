# ADR-0032: 사람용 JSON API — `/api/v1` 주소 체계와 멤버 토큰

결정일: 2026-10-21 (phase 24 step 0). 근거: [ADR-0031](0031-handoff-understanding-and-clients.md) 결정 3(API 우선)·6(순서 ① 사람용 JSON API)·미결 "API 인증", [ADR-0021](0021-team-accounts-and-roles.md)(팀·역할·로그인 세션·Origin 검사), [ADR-0030](0030-pr-ci-review-flow.md)(main 은 PR 로만). 결정 원본은 [phase 24 README](../../phases/24-human-api/README.md) "결정" D1~D37 이고 이 문서가 구현 기준으로 고정한다. 이름·표·경로·시그니처·문구의 기준은 [ARCHITECTURE](../ARCHITECTURE.md) "사람용 API — phase 24" 다(README 와 다르면 ARCHITECTURE).

## 배경

- ADR-0031 은 순서를 ① 사람용 JSON API → ② 인계 리포트 → ③ CLI → ④ IDE 앱으로 정했다. 이 ADR 은 ① 이다.
- 지금 사람용 기능은 대부분 `server/web.py` 의 HTML 폼·303 응답이다(화면 라우트 105개). 업무 목록·업무 상태·업무 행동(맡기기·우선순위·직접 작업·판단 제안 처리)에는 JSON 이 없다.
- JSON 라우트는 8개 라우터 파일에 41개 있다 — 러너(`machine_api.py` 7)·n8n 입구(`inbound_api.py` 1)·GitHub 소스 설정(`github_api.py` 7)·사람 요청 응답(`human_api.py` 2)·지표·기준선(`metrics_api.py` 3)·담당 범위(`responsibility_api.py` 4)·사내 요청(`internal_request_api.py` 15)·매핑 표(`mapping_api.py` 2). 주소에 공통 접두어가 없고, 사람용은 로그인 쿠키(`wf_login`)만 받는다.
- ADR-0031 미결 "API 인증": 웹은 세션 쿠키, 앱·CLI·스크립트·n8n 이 "내 이름으로" 부를 멤버별 토큰이 없다.

## 코드 조사로 README 와 달라진 사실 (2026-10-21, `feat-24-human-api` = `main` `dfe65c3` + phase 파일)

README 결정과 충돌하는 코드 사실은 없다. 아래는 결정을 구현 이름으로 옮기며 확인한 것이다. [사용자] 결정은 하나도 바꾸지 않았다.

1. **JSON 라우트는 41개, 화면 라우트는 105개다.** 앱 라우트 = 화면(endpoint 모듈 `workflow.server.web`) 105 + JSON 41 + `/healthz`(endpoint 모듈 `workflow.server.app` — `add_api_route` 의 lambda) + FastAPI 기본 문서 4(`/openapi.json`·`/docs`·`/docs/oauth2-redirect`·`/redoc`) + `/static` 마운트(endpoint 없음). 라우트 분류 규칙(ARCHITECTURE)은 이 사실을 쓴다.
2. **`data-json-action` 은 정말 10곳이다.** `_cycle.html` 1(사람 응답), `_repos_github.html` 8(소스 설정 PUT·미리보기 2·담당 연결 2·기준선·중지·새 소스), `metrics.html` 1(기준선). 그 밖에 `metrics.html` 의 JSON·CSV 내려받기 링크 2개가 있다(D11).
3. **읽기 전용 검사는 `require_member_api` 안에 두면 역할 검사보다 먼저 돈다.** `require_action(action, api=True)` 의 내부 의존성이 `require_member_api` 위에 서 있다. 그래서 D36 의 "역할 검사보다 먼저" 는 순서 코드를 따로 두지 않아도 된다.
4. **`LoggedIn.login_id` 는 로그인 세션 id(`lgn-…`)이고 읽는 곳이 없다.** 토큰으로 들어온 요청에는 `login_id` 에 토큰 id(`mtk-…`)를 싣는다(ARCHITECTURE auth 규칙). 칸 이름은 바꾸지 않는다.
5. **`/connector/exchange` 는 Bearer 헤더가 없다**(본문의 연결 코드로 교환). 그래서 Origin 예외 (b) "러너·n8n 새 경로 정확 목록" 이 Bearer 헤더 예외 (a) 와 별도로 필요하다(D19).
6. **Origin 검사 구조**: 앱 미들웨어 `web.origin_guard`(web.py 179)가 `GET`·`HEAD`·`OPTIONS` 밖의 요청에 `auth.check_origin(request, settings)` 을 부르고, 거짓이면 403 `forbidden_origin`. 예외 경로는 `auth._BEARER_PREFIXES = ("/connector/", "/executions/")` 와 `auth._CHAINS_PATH`(`/sources/[^/]+/chains` fullmatch) 두 상수다.
7. **원문을 한 번 보이는 화면은 POST 응답에서 바로 렌더한다.** `/sources/tokens`(입구 토큰)·`/team/invites`(초대 링크)가 303 없이 그 화면을 200 으로 그린다(쿠키·쿼리·flash 에 원문을 두지 않는다). `/me` 토큰 발급 폼도 같은 방식이다.
8. **`/me` 는 `team.EDIT_OWN_SETTINGS` 동작을 건다** — 관리자·멤버 모두 가진 동작이다. 토큰 API 도 같은 동작을 건다(새 동작을 만들지 않는다).
9. **`views._agent_choice_lines` 는 `{agent_id, name, label, online}` 만 돌려주고 소유자 승인 필요는 `label` 끝의 " · 승인 필요" 글자로만 있다.** D28 "웹과 같은 값" 을 지키려고 이 함수에 `needs_owner_approval: bool` 칸을 더한다(같은 `needs_owner_approval(...)` 호출 결과 — label 문자열을 파싱하지 않는다).
10. **업무 키 조회는 웹 안에만 있다.** `web._WORK_KEY`(`RUN-([1-9][0-9]{0,8})` fullmatch)와 `web._own_work`(없으면 `PageError` 404). API·사내 요청도 같은 규칙이 필요해 예외 없는 공용 함수로 꺼낸다(ARCHITECTURE "업무 키 조회").
11. **패널의 사내 요청 묶음은 `web._panel`·`web._request_display` 가 만든다.** `views` 에 없으므로 상세 API 가 쓰려면 `views` 공용 함수로 옮겨야 한다(ARCHITECTURE "업무 상세 응답").
12. **`web.INBOUND_PATH` 는 설정 n8n 입구 탭이 보이는 주소다**(`inbound_url = base_url + INBOUND_PATH`). D11 대로 러너·n8n 경로 변경(step 5)과 함께 바뀐다.

## 결정

### 범위 (D1~D4)

1. 이번에는 업무 읽기(목록·한 건 상태)와 업무 행동(담당 정하기/해제 = 맡기기, 우선순위, 직접 작업 시작/그만, 판단 받기·다시 판단, 판단 제안 수락/무시, 다음 단계 제안 수락/무시)을 JSON API 로 연다. 새 업무 직접 등록(`/tasks/new`)·인계 리포트·CLI·앱은 범위 밖이다.
2. 웹 화면은 그대로 HTML 폼이고, API 와 같은 `work_actions`·`views`·`domain/work_list` 함수를 쓴다. API 전용 권한·상태 전이·중복 방지 규칙을 만들지 않는다.

### 주소 체계 (D5~D11)

3. **모든 JSON API 는 `/api/v1` 아래**다. 리소스는 복수 명사, 메서드는 GET 읽기 · PATCH 칸 바꾸기 · PUT/DELETE 붙이고 떼기 · POST 생성 · CRUD 밖 행동은 `POST …/{id}/accept` 꼴. 접두어는 상수 `workflow.contracts.v1.API_PREFIX = "/api/v1"` 하나다(서버·러너가 함께 쓴다 — `server/`·`connector/` 가 공유하는 곳은 `contracts/` 뿐).
4. **업무는 업무 키(`RUN-12`)로 가리킨다.** 내부 `work_item_id` 를 주소에 쓰지 않는다. 사내 요청 생성·조회 경로도 키로 바뀐다(D31).
5. **이름이 바뀌는 곳은 셋뿐이다.** `/metrics.json` → `/api/v1/metrics`, `/operator/github/sources/{source_id}/baseline` → `/api/v1/github/sources/{source_id}/baseline`(사용자 확정), `/work-items/{work_item_id}/internal-requests` → `/api/v1/work-items/{key}/internal-requests`(D31). 나머지 사람용 JSON 경로는 앞에 `/api/v1` 만 붙인다.
6. **러너(`/connector/*`, `/executions/*`)와 n8n 입구(`/sources/{source}/chains`)도 `/api/v1` 아래로 옮긴다**(사용자 "같이 옮김"). 뒷부분 이름은 그대로다.
7. **옛 주소는 바로 없앤다** — 별칭·리다이렉트 없음(사용자 "바로 없앰"). 옛 주소는 404 다.
8. **화면 주소·화면 폼 POST 경로·`/healthz` 는 옮기지 않는다.** `/tasks…`, `/work/{key}/…`, `/me…`, `/responsibilities/add`·`/remove`, `/sources/tokens…`, `/operator/…`(GitHub App callback 포함 — GitHub 에 등록된 주소), `/requests/…`, `/team…` 등. `/healthz` 는 compose healthcheck·`install.sh` 가 부른다. 접두어 일괄 치환을 하지 않는다 — JSON 라우터만 옮긴다.
9. 웹 스크립트(`data-json-action` 10곳)·`metrics.html` 내려받기 링크·설정 n8n 입구 탭 주소(`web.INBOUND_PATH`)·러너 클라이언트(`connector/client.py`)·n8n 문서/예시를 주소 변경과 같은 step 에서 고친다.

전체 옛 → 새 주소 표는 ARCHITECTURE "사람용 API — phase 24" 에 있다.

### 멤버 토큰 (D12~D23)

10. **멤버 토큰**: 한 멤버가 여러 개, 이름(1~40자)을 붙여 발급, 하나씩 폐기, 자동 만료 없음, 마지막 사용 시각 표시. 멤버당 개수 상한 없음(D37). 발급·목록·폐기 화면은 `/me` 의 "API 토큰" 절.
11. **원문 형식** `wfm_` + `secrets.token_urlsafe(32)`(연결 토큰 `wfc_`·입구 토큰 `wfs_` 와 구별). DB 에는 `token_sha256` 만 둔다. 원문은 발급 응답(API 201 본문 또는 `/me` 발급 POST 응답 화면)에 한 번만 — DB·로그·그 뒤 응답·템플릿·백업에 없다. AGENTS.md 비밀값 목록에 멤버 토큰 `wfm_…` 을 더한다.
12. **범위(scope)**: 발급 때 `read`(읽기 전용 — GET·HEAD 만) 또는 `full`(내 권한 전부)을 고른다. 항목별 세부 권한은 없다. 토큰 요청의 권한은 토큰 주인 멤버의 역할(`team.can`)로 판정한다 — 웹과 같다.
13. **비활성화하면 그 멤버 토큰은 모두 폐기**(`revoked_at`)된다 — `disable_member` 가 같은 트랜잭션에서 로그인 세션과 함께 폐기한다. 다시 활성화해도 되살아나지 않는다. 비밀번호 변경·재설정은 토큰을 건드리지 않는다. 관리자가 남의 토큰을 개별 폐기하는 화면은 없다.
14. **인증**: 사람용 JSON API 는 `Authorization: Bearer <멤버 토큰>` 또는 로그인 쿠키를 받는다. Bearer 헤더가 있으면 토큰만 보고 쿠키는 무시한다 — 토큰이 틀리면 401 이고 쿠키로 넘어가지 않는다. 화면(HTML) 라우트는 Bearer 를 받지 않는다(쿠키만).
15. **Origin 검사 예외는 둘뿐**: (a) `Authorization: Bearer` 헤더가 있는 요청, (b) 러너·n8n 입구의 정확한 새 경로(`/api/v1/connector/`·`/api/v1/executions/` 접두어, `/api/v1/sources/{source}/chains` fullmatch). `/api/v1/` 접두어 전체를 예외로 넓히지 않는다 — 쿠키로 부르는 API 가 CSRF 검사에서 빠진다. (a) 가 안전한 이유: 브라우저는 다른 출처 요청에 `Authorization` 헤더를 CORS 사전 요청 없이 붙이지 못하고, 이 앱은 CORS 를 열지 않는다.
16. 멤버 로그인을 요구하는 기존 JSON API(사람 요청·사내 요청·담당 범위)와 관리자 설정 API(GitHub 소스·매핑·지표·기준선)는 모두 같은 의존성(`require_member_api`·`require_action(…, api=True)`)이라 토큰도 받는다. 역할 검사는 그대로다.
17. **토큰 발급(`POST /api/v1/me/tokens`)은 로그인 쿠키로만** — Bearer 가 오면 403 `cookie_required`. 토큰으로 토큰을 만들지 않는다(새면 퍼지지 않게). 목록·폐기는 토큰으로도 된다(읽기 전용 토큰의 폐기는 `token_read_only`).
18. 마지막 사용 시각은 저장된 값과 60초 이상 차이 날 때만 쓴다(매 요청 쓰기 방지).
19. 토큰 실패 횟수 제한은 없다 — 원문이 무작위 32바이트라 추측할 수 없다. `LoginThrottle` 은 비밀번호용이다.

### 응답·오류 (D24~D31)

20. **읽기 전용 토큰의 GET·HEAD 밖 요청은 403 `token_read_only`("읽기 전용 토큰입니다")** — 역할 부족 403 `forbidden` 과 구별한다. 기준은 메서드라 POST 인 미리보기(`/api/v1/github/sources/preview`)도 막힌다. 이 검사는 역할 검사보다 먼저다(D36).
21. 오류 본문은 기존 `code`·`message`·`field`·`details`. 미인증 401 `unauthenticated`, 권한 403 `forbidden`, 없는 업무 404 `not_found`(키 형식이 틀려도 404), `WorkActionError` 는 같은 status·code·message·field 로 옮긴다.
22. 행동 요청은 JSON 본문만(폼 본문 422). 알 수 없는 칸은 422 `unknown_field`.
23. **목록 응답** = `WorkRow` 칸(키·제목·종류·우선순위·담당·상태·상태 이유·다음 할 일·판단·다음 단계·저장소·원본·갱신 시각) + 빠른 필터 건수. 상태 값은 화면과 같은 사용자 상태 라벨. 요청 멤버 기준 `my_turn: bool` 을 싣고 받는 사람 member_id 목록(`recipients`)은 싣지 않는다.
24. 목록 쿼리 `filter`·`repository`·`closed` 는 웹과 같은 값 집합이고 모르는 값은 422 `invalid_field`(웹은 기본값으로 바꾸지만 API 는 엄격). 묶기·보드(`group`·`view`)는 받지 않는다. 페이지 나누기 없음.
25. **상세 응답** = 업무 패널이 보여 주는 정보(업무 칸·단계·링크·열린 사람 요청과 응답·판단 제안·다음 단계 제안·PR·직접 작업·사내 요청 요약·이벤트·할 수 있는 행동·담당 후보)에서 화면 전용(폼 숨은 입력·제출 키·HTML 문구 조각)을 뺀 것. 담당 후보는 패널 선택 목록과 같은 함수·같은 값(에이전트 `online`·`needs_owner_approval`) — 새 "맡길 수 있음" 판정은 없다. 내부 ID 는 행동에 필요한 것(제안 `triage_id`, 사람 요청 `request_id`, 에이전트·멤버 id, 사내 요청 `request_id`)만.
26. 행동 성공 응답은 바뀐 업무 상세다(판단 받기는 202, 나머지 200).
27. 중복·동시 요청은 도메인 동작 그대로(판단 제안은 `triage_id` 로 409 `triage_stale` 등). API 전용 멱등 키는 없다.

### 저장 (D32·D37)

28. 새 표 `member_tokens`(스키마 v26) — 칸은 ARCHITECTURE. 마이그레이션 `_migrate_25_to_26` 은 표 하나를 더할 뿐 데이터를 바꾸지 않는다.

### 업그레이드 (D35)

29. 순서: 진행 중 실행 0건 확인 → 백업(`backup create`) → `install.sh`(스키마 26) → **러너 재설치(`install-runner.sh`)** → **n8n 워크플로우의 Runloom 주소를 `/api/v1/sources/n8n/chains` 로 수정**. 그 사이 옛 러너·옛 n8n 요청은 404 다(옛 주소 없음).

### 판정 순서 (D36)

30. 사람용 API 요청의 판정 순서: Origin 검사(미들웨어 — Bearer 헤더 있으면 건너뜀) → 쿠키 전용 API 면 Bearer 헤더가 있을 때 403 `cookie_required` → 인증(Bearer 있으면 토큰, 없으면 쿠키 — 실패 401) → 읽기 전용 토큰 + GET·HEAD 밖이면 403 `token_read_only` → 역할(`team.can`) 403 `forbidden` → 본문 검증 422 → 업무 키 404 → 도메인 오류.

## 하지 않는 것

새 업무 직접 등록 API, 인계 리포트·CLI·IDE 앱, 웹 화면을 API 클라이언트로 바꾸기, 옛 주소 별칭·리다이렉트, 관리자의 남의 토큰 개별 폐기 화면, 토큰 항목별 세부 권한, 토큰 만료, 토큰 실패 횟수 제한, 멤버당 토큰 개수 상한, API 전용 멱등 키, 페이지 나누기, 묶기·보드 쿼리, 러너·n8n 경로 뒷부분 이름 변경, FastAPI 기본 `/docs`·`/openapi.json` 변경(지금처럼 둔다), CORS 열기, 셀프호스트 업그레이드 적용(phase 뒤 사용자 지시).

## 결과

- **셀프호스트 업그레이드 영향(v26)**: 러너 프로토콜 주소가 바뀌므로 **러너 재설치가 필수**다 — 옛 러너는 옛 주소를 불러 404 를 받고 실행을 받지 못한다. **n8n 워크플로우의 HTTP 노드 주소도 고쳐야 한다** — 옛 입구 주소는 404. 북마크한 지표 JSON 주소(`/api/v1/metrics`)도 바뀐다. 화면 주소는 그대로다. 절차는 [SELFHOST](../SELFHOST.md) "업그레이드" v26(step 8).
- 멤버가 `/me` 에서 토큰을 만들어 `curl -H "Authorization: Bearer wfm_…" /api/v1/work-items?filter=my_turn` 으로 내 차례 업무를 보고 `PUT /api/v1/work-items/RUN-12/assignee` 로 맡길 수 있다. ③ CLI·④ 앱은 이 API 와 토큰 위에 선다.
- 비밀값 목록에 멤버 토큰이 더해진다. 백업(`workflow.server.backup`)은 DB 를 그대로 담으므로 `member_tokens.token_sha256` 만 들어간다.
- 문서의 옛 주소는 모두 새 주소로 고친다(D33 — `docs/archive/`·`phases/*/` 제외). 옛 주소가 남는 곳은 이 ADR, ARCHITECTURE "사람용 API — phase 24" 절, 한 줄에 `→` 와 새 `/api/v1` 경로가 함께 있는 대응 줄뿐이다.
