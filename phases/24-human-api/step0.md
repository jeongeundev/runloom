# Step 0: api-design — 사람용 API 설계 고정 (문서만)

## 읽어야 할 파일

- AGENTS.md
- phases/24-human-api/README.md — **"결정" 절 D1~D37 이 이 phase 의 기준이다.** [사용자] 표기 결정은 바꾸지 않는다.
- docs/adr/0031-handoff-understanding-and-clients.md (결정 3·6, 미결 "API 인증"), docs/adr/0030-pr-ci-review-flow.md, docs/adr/0021-team-accounts-and-roles.md, docs/adr/0028-setup-ux.md (ADR 형식)
- docs/ARCHITECTURE.md — "팀 — phase 15"(로그인·권한·Origin 검사), "업무 화면 — phase 16"(목록·패널), "판단 — phase 19", "결과 뒤 판단 — phase 22", "설정 UX — phase 23"
- docs/CONTRACT.md 공통 오류 규칙과 13.10·13.11, docs/GLOSSARY.md, docs/SELFHOST.md "업그레이드"
- 코드: `src/workflow/server/app.py`(라우터 등록), `auth.py`(`current_member`·`require_member_api`·`require_action`·`check_origin`·`_BEARER_PREFIXES`·`_CHAINS_PATH`), `errors.py`, 모든 JSON 라우터 `machine_api.py`·`inbound_api.py`·`github_api.py`·`human_api.py`·`metrics_api.py`·`responsibility_api.py`·`internal_request_api.py`·`mapping_api.py`, `web.py` 의 `/tasks`·`/work/{key}/…` 라우트(`work_assignee`·`work_priority`·`work_direct`·`work_direct_stop`·`work_triage`·`work_triage_accept`·`work_triage_dismiss`·`work_next_step_accept`·`work_next_step_dismiss`·`_panel`·`_list_query`), `work_actions.py`, `triage_runs.request_triage`, `views.py`(`work_list_context`·`work_panel_context`·`_agent_choice_lines`·`triage_panel`·`next_step_panel`), `domain/work_list.py`(`WorkRow`·`QUICK_FILTERS`·`CLOSED_SCOPES`·`parse_list_query`·`filter_rows`·`filter_counts`), `adapters/repo.py`(`create_login_session`·`member_for_login_token`·`revoke_login_sessions`·`disable_member`·`enable_member`), `adapters/db.py`(`SCHEMA_VERSION`·마이그레이션 형식), `connector/client.py`, 템플릿의 `data-json-action`·`metrics.html` 링크, `docs/n8n/`

먼저 실제 파일을 읽는다. 대화 이력을 전제로 판단하지 않는다.

## 작업

이 step 은 **문서만** 쓴다. 코드·테스트는 바꾸지 않는다. 다음 step 들이 이 문서를 기준으로 구현한다.

1. 코드를 읽어 README 결정이 코드 사실과 맞는지 확인한다. 다르면(예: 함수가 없거나 이름이 다름) ADR 에 "코드 조사로 README 와 달라진 사실" 로 적고 [근거]·[조정값] 결정만 고친다. [사용자] 결정과 충돌하면 고치지 말고 `blocked` 로 멈춘다.
2. `docs/adr/0032-human-api-and-member-tokens.md` — 배경(ADR-0031 순서 ①·미결 API 인증), 결정(주소 체계 D5~D11, 멤버 토큰 D12~D23, 응답·오류 D24~D31, 업그레이드 D35, 판정 순서·상한 없음 D36·D37), 하지 않는 것, 결과(셀프호스트 업그레이드 영향: 러너 재설치·n8n 주소 수정 필수). ADR-0031 "미결 — API 인증" 에 "→ ADR-0032 에서 정함" 한 줄을 단다.
3. `docs/ARCHITECTURE.md` 끝에 `## 사람용 API — phase 24` 절. 최소한 아래를 **이름까지** 고정한다(다음 step 이 그대로 쓴다):
   - **주소 표**: 지금 있는 모든 JSON 라우트(위 8개 라우터 파일의 모든 `@router` — 빠짐없이) 옛 경로 → 새 경로, 메서드, 필요한 동작(`team.*`), 쿠키/Bearer 허용 여부. 새로 여는 업무 API·토큰 API 도 같은 표에. 철자 규칙: 앞에 `/api/v1`, 이름 바꾸는 것은 README D7 의 두 곳(`/metrics.json` → `/api/v1/metrics`, `/operator/github/sources/{id}/baseline` → `/api/v1/github/sources/{id}/baseline`)과 사내 요청 업무 경로(`/work-items/{work_item_id}/internal-requests` → `/api/v1/work-items/{key}/internal-requests`, D31)뿐. 러너·n8n 경로는 앞에 `/api/v1` 만(D8). 옮기지 않는 화면 경로 목록(D10)도 표로.
   - 옛 경로를 문서에 남기는 곳은 이 절(주소 표), ADR-0032, 그리고 한 줄에 `→` 와 새 `/api/v1` 경로가 함께 있는 대응 줄뿐이다 — step 8 의 옛 경로 검사 스크립트가 이 셋만 건너뛴다. CURRENT_HANDOFF·GLOSSARY 등 다른 문서에 옛 경로를 새로 쓰지 않는다.
   - **step 경계**: step 4 = 사람용 JSON 라우터(github_api·human_api·metrics_api·responsibility_api·internal_request_api·mapping_api) + 웹 스크립트·링크, step 5 = 러너·n8n 프로토콜(machine_api·inbound_api 서버 쪽과 `connector/client.py` 를 함께 — 한쪽만 옮기면 e2e 가 깨진다).
   - **`member_tokens` 표(스키마 v26)**: 칸·타입·제약·인덱스(`token_id` 형식 예 `mtk-<hex>`, `member_id` FK, `name`(길이 1~40, 앞뒤 공백 제거, 같은 멤버 안 중복 허용 여부), `scope CHECK IN ('read','full')`, `token_sha256 UNIQUE`, `created_at`, `last_used_at`, `revoked_at`). 원문 형식 `wfm_` + `secrets.token_urlsafe(32)`. 멤버당 개수 상한 없음(README D37).
   - **repo 함수 시그니처**(step 1): `issue_member_token(conn, *, session_id, member_id, name, scope, now) -> tuple[str, str]`(token_id, 원문), `list_member_tokens(conn, member_id) -> list[Row]`(원문·해시 없이), `revoke_member_token(conn, *, member_id, token_id, now) -> bool`(남의 토큰이면 False), `member_for_api_token(conn, token, *, now) -> Row | None`(활성 멤버·폐기 안 됨, `scope`·`session_id` 포함 — 워크스페이스 일치 검사는 auth 가 `SELFHOST_SESSION_ID` 로 한다, `current_member` 와 같은 방식), `touch_member_token(conn, token_id, *, now) -> None`(D22 — 60초 이상 차이일 때만), `revoke_member_tokens(conn, member_id, *, now) -> int`. `disable_member` 가 같은 트랜잭션에서 `revoke_member_tokens` 를 부른다(D14).
   - **auth 규칙**(step 2): `LoggedIn` 에 `token_scope: str | None`(쿠키 = None) 추가 여부와 이름, `current_member` 는 쿠키만(화면용 — 화면은 Bearer 를 받지 않는다), API 의존성 `require_member_api`·`require_action(api=True)` 이 Bearer 를 먼저 본다(D19). 읽기 전용 검사 위치(의존성 안, 메서드가 GET·HEAD 가 아니면 **역할 검사보다 먼저** 403 `token_read_only` "읽기 전용 토큰입니다" — D24·D36). `check_origin` 예외 = Bearer 헤더 있음 + 러너·n8n 새 경로 정확 목록(D19). 쿠키 전용 의존성 이름(토큰 발급용, D21 — 예 `require_member_cookie_api`)과 Bearer 가 왔을 때 403 `cookie_required`(D36). Origin 검사는 `web.origin_guard`(web.py 180 근처)가 `auth.check_origin` 을 부르는 구조 — 확인해 적는다.
   - **토큰 API·화면**(step 3): 요청·응답 본문(발급 `{"name","scope"}` → 201 `{"token_id","name","scope","token","created_at"}` — `token` 은 이 응답에만), 목록 칸, 폐기 204/404. `/me` 화면 절 이름·문구(원문 한 번 표시 안내, 읽기 전용/내 권한 전부 라벨, 마지막 사용 "사용 기록 없음"), 화면 폼 경로(쿠키 폼 — 예 `POST /me/tokens`·`POST /me/tokens/{id}/revoke`, 303 뒤 원문 표시 방식: 리다이렉트로는 원문을 못 넘기므로 POST 응답에서 바로 화면을 렌더하는 방식 — 기존 `/team/invites` 와 같은 방식인지 코드로 확인).
   - **업무 목록 응답**(step 6): `GET /api/v1/work-items` 쿼리 `filter`(값 집합 = `QUICK_FILTERS`, 기본값), `repository`(워크스페이스 저장소 목록 값, 대소문자 무시), `closed`(값 집합 = `CLOSED_SCOPES`). 모르는 값 422 `invalid_field`(`field` = 쿼리 이름). 응답 `{"items": [...], "counts": {...}}` — 항목 칸 이름 표(`key`·`title`·`kind`·`kind_label`·`priority`·`assignee{type,id,name,active}|null`·`status`·`status_reason`·`next_action`·`triage`·`next_step`·`repository`·`source{type,key,url}`·`updated_at`·`closed_at` 등 — `WorkRow` 에서). `recipients` 는 `my_turn: bool`(요청 멤버 기준)로 바꿔 싣는지 결정.
   - **업무 상세 응답**(step 6): `GET /api/v1/work-items/{key}` 의 칸 표 — `work`·`stages`·`links`·`direct`·`open_requests`·`responses`·`triage`(제안이면 `triage_id`·제안값·근거·확신도)·`next_step`(`triage_id`·행동)·`pull_requests`·`internal_requests` 요약·`events`·`assignee_candidates`(멤버: `type`·`id`·`name`, 에이전트: `type`·`id`·`name`·`online: bool`·`needs_owner_approval: bool` — `views._agent_choice_lines` 와 패널 멤버 목록에서, 웹과 같은 값. 지금 `_agent_choice_lines` 는 `{agent_id, name, label, online}` 만 돌려주고 승인 필요는 `label` 의 " · 승인 필요" 글자로만 있다 — 이 함수에 `needs_owner_approval: bool` 칸을 더하는 것으로 정한다(label 문자열 파싱 금지). '맡길 수 있음' 같은 새 판정은 만들지 않는다 — D28)·`actions`(지금 이 멤버가 할 수 있는 행동 이름 목록: `assign`·`set_priority`·`start_direct`·`stop_direct`·`request_triage`·`accept_triage`… — 패널의 `can_*` 판정과 같은 함수). `UserStatus` 등 객체는 `{"label","reason"}` 처럼 JSON 으로. 화면 전용 값(폼 숨은 입력, HTML 문구 조각)은 넣지 않는다. 키 형식이 틀리거나 없는 업무 404 `not_found`. 패널의 사내 요청 묶음은 지금 `web._panel`(web.py 610 근처, `_request_display`·`internal_request_store.list_for_work`)이 만든다 — step 6 이 이를 `views` 의 공용 함수로 꺼낼 이름을 정한다.
   - **업무 키 조회 공용 함수**(step 4 가 만들고 4·6·7 과 웹 `_own_work` 가 쓴다): 위치·이름(예 `server/work_lookup.py` `work_by_key(conn, session_id, key: str) -> Row | None` — 예외 없이 None, 웹은 `PageError` 404, API 는 `ApiError` 404 로 각자 바꾼다). 지금 `web._WORK_KEY`·`_own_work`(web.py 970~1010) 를 확인해 같은 규칙으로.
   - **업무 행동**(step 7): 메서드·경로·본문·성공 상태·오류 표. 담당 `PUT …/assignee` `{"type":"agent"|"member","id":"…","note":"…"?}`(→ `work_actions.parse_assignee` 값 `agent:<id>`/`member:<id>`), `DELETE …/assignee`(→ `none`), 우선순위 `PATCH /api/v1/work-items/{key}` `{"priority":"high|normal|low"}`(그 밖 칸은 422), 직접 작업 `PUT`/`DELETE …/direct-work`, 판단 받기 `POST …/triage-runs`(시작 못 하면 409 `triage_unavailable` — 메시지 = 이유, 성공 202), 제안 `POST …/triage-proposals/{triage_id}/accept|dismiss`, `POST …/next-step-proposals/{triage_id}/accept|dismiss`. 권한 = 웹 라우트와 같은 동작(`team.DELEGATE` 등 — web.py 에서 확인). 성공 응답 = 바뀐 업무 상세(D29). `WorkActionError` → 같은 status·code·message·field.
   - 오류 코드 표: 기존 + `token_read_only`·`cookie_required`.
   - **라우트 분류 규칙**(step 9 대조 테스트가 쓴다): 화면 라우트 = endpoint 함수 모듈이 `workflow.server.web` 인 것, 그 밖 예외 = `/healthz`·FastAPI 기본 문서 경로·`/static` 마운트(`app.py` 의 `app.mount` — endpoint 없음)(실제 경로를 app 에서 확인). 따라서 **화면 폼 라우트는 모두 `web.py` 에 둔다** — step 3 의 `/me` 토큰 화면 폼도 `web.py`, JSON 토큰 API 만 새 라우터 파일. 나머지 모든 라우트는 `/api/v1/` 로 시작해야 한다. 화면 경로 105개를 표로 다 적지 않는다.
4. `docs/CONTRACT.md` 에 `## 18. 사람용 API — phase 24` 절: 목록·상세·담당 PUT·토큰 발급 요청/응답 예시와 오류 본문 예시(`token_read_only`). **예시 펜스는 ` ```jsonc ` 로 쓴다(` ```json ` 금지)** — `tests/workflow/contracts/test_v1.py` 가 ` ```json ` 블록 개수(73)를 박아 두고 모든 `json` 블록을 계약 모델(`_SIGNATURES`)에 대응시키는데 이 응답들은 모델이 없다. 같은 이유로 CONTRACT 의 표·본문에 인라인 `` `{…}` `` JSON 을 쓰지 않는다(`_INLINE` 정규식이 집어 `json.loads` 한다). 쓴 뒤 `python3 -m pytest tests/workflow/contracts -q` 로 확인. 기존 절의 옛 경로 고치기는 step 8 이 한다(D33).
5. `docs/GLOSSARY.md` 에 phase 24 용어: 멤버 토큰(`member_tokens`, `wfm_`, 금지 표현: API 키·PAT·연결 토큰과 혼동), 읽기 전용 토큰(`scope=read`), `/api/v1`(사람용 API·러너·n8n 공통 접두어).
6. `AGENTS.md` CRITICAL 비밀값 목록에 "멤버 토큰 `wfm_…`" 을 더한다(그 줄만).
7. `docs/CURRENT_HANDOFF.md` 맨 위 "방향 갱신 — 2026-10-21" 아래에 "24-human-api 진행 중" 한 단락.

## 테스트

- 문서만이므로 테스트를 새로 쓰지 않는다. 기존 테스트 전체가 그대로 통과하는지 확인한다.

## 금지사항 (이 step)

- `src/`·`tests/` 를 바꾸지 마라. 이유: 문서만 쓰는 step 이다.
- README [사용자] 결정을 문서에서 다르게 적지 마라. 이유: 사용자가 정한 것이다 — 코드와 충돌하면 `blocked`.

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
