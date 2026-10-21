# Step 6: work-read-api — 업무 목록·상세 API

## 읽어야 할 파일

- AGENTS.md, phases/24-human-api/README.md("결정" D1·D4·D6·D24b·D26~D28), phases/24-human-api/index.json(이전 step summary)
- docs/ARCHITECTURE.md "사람용 API — phase 24" 의 업무 목록·상세 응답 칸 표(**README 와 다르면 ARCHITECTURE 가 기준**), "업무 화면 — phase 16"(목록·패널), "판단 — phase 19"·"결과 뒤 판단 — phase 22"(패널 제안 절), docs/CONTRACT.md 18절 목록·상세 예시
- `src/workflow/server/web.py` 의 `home`·`_list_query`·`_panel`·`work_panel`·`_own_work`
- `src/workflow/server/views.py` 의 `work_list_context`·`work_panel_context`·`_agent_choice_lines`·`triage_panel`·`next_step_panel`·`assignee_label`, `src/workflow/domain/work_list.py`(`WorkRow`·`QUICK_FILTERS`·`CLOSED_SCOPES`·`parse_list_query`·`filter_rows`·`filter_counts`), `src/workflow/contracts/v1.py`(`WORK_KEY_PATTERN`·`format_work_key`)
- 테스트: `tests/workflow/server/test_web_work_list.py`·`test_web_work_panel.py`·`test_views.py`(목록·패널 fixture 만드는 법)

## 작업

1. 새 라우터 `server/work_api.py`(ARCHITECTURE 이름) `APIRouter(prefix="/api/v1/work-items")`, `app.py` 에 등록. 의존성은 `require_member_api`(쿠키·토큰 모두, step 2).
2. `GET /api/v1/work-items` — 쿼리 `filter`·`repository`·`closed` 를 ARCHITECTURE 값 집합으로 엄격 검사(모르는 값 422 `invalid_field`, `field` = 쿼리 이름 — D27). 행·건수는 웹과 같은 함수(`repo.list_work_rows`·`filter_rows`·`filter_counts` 또는 `views.work_list_context`)로 만들고, 칸 이름은 ARCHITECTURE 표대로 JSON 으로 옮긴다. 묶기·보드 칸(`groups`·`columns`·`hidden_columns`)은 싣지 않는다.
3. `GET /api/v1/work-items/{key}` — 키 형식이 틀리거나 이 워크스페이스에 없으면 404 `not_found`. `views.work_panel_context`(웹 패널과 같은 함수)의 결과를 ARCHITECTURE 칸 표대로 JSON 으로 바꾼다: 객체(`UserStatus` 등)는 `{"label","reason"}` 같은 JSON 값으로, 화면 전용 값(폼 숨은 입력·HTML)은 빼고, `assignee_candidates`(패널 담당 선택 목록과 같은 함수 — 멤버 id·이름, 에이전트 id·이름·`online`·`needs_owner_approval`. 웹에 없는 '맡길 수 있음' 판정을 만들지 않는다 — D28)와 `actions`(패널 `can_*` 판정과 같은 함수)를 싣는다. 변환 함수는 한 곳(ARCHITECTURE 이름)에 두고 step 7 이 행동 응답에 다시 쓴다.
4. `views._agent_choice_lines` 에 `needs_owner_approval: bool` 칸을 더한다(지금은 label 글자로만 있다 — 문자열 파싱 금지, 웹 표시는 그대로). 웹 화면 동작은 바꾸지 않는다. 변환에 필요한 값이 `views` 안 지역 계산이나 `web.py` 비공개 함수에만 있으면 그 계산을 `views` 의 공용 함수로 꺼내 웹과 API 가 같이 쓰게 한다(동작 그대로 — 기존 웹 테스트 통과). 특히 패널의 사내 요청 묶음은 지금 `web._panel`(610 근처, `_request_display`·`internal_request_store.list_for_work`)이 만든다 — ARCHITECTURE 의 이름으로 `views` 에 꺼낸다. 업무 키 조회는 step 4 의 공용 함수를 쓴다.

## 테스트

- 목록: 필터별 결과가 웹 목록(`GET /tasks?q=…`)과 같은 업무 키 집합, `counts` 가 웹 빠른 필터 건수와 같음, 모르는 `filter`·`closed`·`repository` 422, 토큰(read)으로 200, 미인증 401.
- 상세: 판단 제안이 있는 업무에 `triage.triage_id`·제안값, 다음 단계 제안, 담당 후보(꺼진 러너 에이전트는 `online: false`, 남의 에이전트는 `needs_owner_approval: true` — 패널 표시와 같음), member 역할 멤버의 `actions` 가 웹 패널 버튼과 같음, 없는 키·형식 틀린 키 404.
- 응답 JSON 에 비밀값·`token_sha256`·연결 토큰이 없다.

## 금지사항 (이 step)

- 행동(쓰기) 라우트를 만들지 마라. 이유: step 7 범위.
- 목록·패널 판정 규칙을 API 용으로 따로 쓰지 마라(필터·건수·후보·가능 행동). 이유: README D4 — 웹과 같은 함수.

## 테스트 먼저

소스·템플릿을 바꾸기 전에 `tests/` 미러 경로(`src/workflow/x/y.py` → `tests/workflow/x/test_y.py`)에 실패하는 테스트를 먼저 작성하고, 실패 원인이 의도한 것인지 확인한다(`tdd-guard.sh` 가 테스트 없는 소스 작성을 막는다). 구현한 뒤 해당 테스트와 전체 회귀를 통과시킨다. 이 step 의 변경으로 기존 테스트(주소·문구 단정)가 깨지면 새 동작 기준으로 고치되 단정을 약하게 만들지 않는다. 무엇을 왜 바꿨는지는 summary 에 남긴다.

## Acceptance Criteria

```bash
python3 -m pytest -q
python3 -m ruff check .
```

위 커맨드와 이 step 의 "테스트" 항목을 모두 확인한다.

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 규칙을 확인한다.
   - `domain/` 은 FastAPI·sqlite3·HTTPX·subprocess·Git 을 import 하지 않는다.
   - `server/` 와 `connector/` 는 서로 import 하지 않고 `contracts/` 만 공유한다.
   - 외부 입력(요청 본문·쿼리·토큰 이름)에서 명령·경로를 받아 실행하지 않는다.
   - 비밀값(연결 토큰 `wfc_…`, **멤버 토큰 `wfm_…` 원문**, `OPERATOR_TOKEN`, `SESSION_SECRET`, GitHub App 비밀·설치 토큰, PAT, 로그인·초대 링크 원본)을 DB·로그·응답(발급 응답 1회 제외)·템플릿·백업에 넣지 않는다. DB 에는 `token_sha256` 만.
   - 웹 폼과 API 는 같은 도메인·`work_actions`·`views` 함수를 쓴다. API 전용 규칙(권한·상태 전이·중복 방지)을 따로 만들지 않는다.
   - GLOSSARY 이름을 그대로 쓴다(`WorkItem`·업무 키, `Execution` ≠ run, `Agent` ≠ connector, `outcome` ≠ 상태).
   - 종류 이름(`bug_fix`·`code_review`·`triage`)으로 새 분기를 만들지 않는다(ADR-0009).
3. 성공이면 `phases/24-human-api/index.json` 의 이 step 만 `completed` 로 바꾸고 `summary`(생성·수정 파일, 결정, 테스트 수, 다음 step 주의점 한 줄)와 `verification`(실제 실행한 AC 명령과 종료 코드 목록 `[{"command": "...", "exit_code": 0}]`)을 남긴다. 끝냈지만 걸리는 점(정하지 않고 넘어간 것, 확인 못 한 것)은 `concerns` 에.
4. 3회 수정 후에도 실패하면 `error` + `error_message`. 사용자 결정·외부 자원이 필요하면 `blocked` + `blocked_reason` 후 즉시 중단. 실행하지 않은 검증을 `completed` 로 표시하지 않는다.

## 금지사항 (공통)

- 실제 Claude/Codex·GitHub·Jira·Discord·외부 웹훅을 호출하지 않는다. 이유: 가짜 클라이언트·`MockTransport`·FastAPI `TestClient` 로 검증한다.
- 사용자가 띄워 둔 환경을 읽거나 바꾸지 않는다: 셀프호스트(compose 프로젝트 `runloom`, 포트 8000, 볼륨 `runloom_workflow-data`)·`deploy/selfhost/.env`·러너 홈(`~/Library/Application Support/workflow-connector*`)·launchd(`launchctl` 실행 금지)·`~/.claude/`·GitHub 저장소 `jeongeundev/*`. 컨테이너를 띄우거나 멈추지 않는다. 셀프호스트에 업그레이드를 적용하지 않는다. 이유: 실사용 환경 — 적용은 phase 뒤 사용자 지시.
- 새 의존성(Python 패키지, JS·CSS 프레임워크, CDN)을 추가하지 않는다. 이유: ADR-0002.
- 화면(HTML) 라우트·화면 폼 POST 경로(`/tasks…`, `/work/{key}/…`, `/me…`, `/responsibilities/add`·`/remove`, `/sources/tokens…`, `/operator/…`, `/operator/github/app/callback`, `/requests/…`, `/team…` 등)와 `/healthz` 를 옮기지 않는다. 이유: README 결정 D10 — JSON API 만 `/api/v1` 로 옮긴다. 경로 문자열 일괄 치환을 하지 마라 — 같은 접두어의 화면 폼이 깨진다.
- 사용자 변경을 삭제하거나 stash·reset 하지 않는다. 커밋은 하네스가 한다.
- 이 step 범위 밖 모듈·화면을 "개선"하지 않는다. 기존 테스트의 단정을 약하게 만들어 통과시키지 않는다.
- 기존 테스트를 깨뜨리지 마라.
