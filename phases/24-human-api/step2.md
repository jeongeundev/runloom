# Step 2: token-auth — Bearer 인증·읽기 전용·Origin 예외

## 읽어야 할 파일

- AGENTS.md, phases/24-human-api/README.md("결정" D14~D24), phases/24-human-api/index.json(이전 step summary)
- docs/ARCHITECTURE.md "사람용 API — phase 24" 의 auth 규칙·오류 코드 표(**README 와 다르면 ARCHITECTURE 가 기준**), "팀 — phase 15" 의 Origin 검사, docs/adr/0032-*.md
- `src/workflow/server/auth.py` 전체(`LoggedIn`·`current_member`·`require_member`·`require_member_api`·`require_action`·`_bearer_token`·`check_origin`·`_BEARER_PREFIXES`·`_CHAINS_PATH`), `src/workflow/server/web.py` 의 `origin_guard`(180 근처 — Origin 미들웨어 본체, `app.py` 는 등록만), `src/workflow/server/errors.py`
- step 1 이 만든 `adapters/repo.py` 의 `member_for_api_token`·`touch_member_token`
- 테스트: `tests/workflow/server/test_auth.py`, `tests/workflow/server/conftest.py`(로그인 fixture)

## 작업

1. `LoggedIn` 에 토큰 범위 칸(ARCHITECTURE 이름, 쿠키 로그인 = None)을 더한다.
2. API 의존성(`require_member_api`, `require_action(..., api=True)`)이 `Authorization: Bearer` 헤더를 먼저 본다:
   - 헤더가 있으면 `member_for_api_token` 으로만 판정하고 쿠키는 보지 않는다. 틀리면 401 `unauthenticated`(쿠키로 넘어가지 않음).
   - 맞아도 `row["session_id"] != SELFHOST_SESSION_ID` 면 401(`current_member` 와 같은 규칙). 맞으면 `touch_member_token` 후 `LoggedIn`(토큰 주인 멤버, 역할 그대로) — 권한은 `team.can(role, action)` 그대로(D16).
   - 토큰 범위가 `read` 이고 요청 메서드가 GET·HEAD 가 아니면 **역할 검사보다 먼저** 403 `token_read_only` "읽기 전용 토큰입니다"(D24·D36).
   - 화면 의존성 `require_member`·`current_member` 는 Bearer 를 받지 않는다(쿠키만).
3. 쿠키 전용 API 의존성(ARCHITECTURE 이름)을 만든다 — Bearer 헤더가 있으면 403 `cookie_required`(D36). step 3 의 토큰 발급이 쓴다(D21).
4. `check_origin`: Origin 검사 예외를 (a) `Authorization: Bearer` 헤더가 있는 요청, (b) ARCHITECTURE 의 러너·n8n 경로 목록으로 바꾼다. **이 step 에서는 러너·n8n 경로가 아직 옛 주소**이므로 (b) 는 지금 경로(`/connector/`·`/executions/`·`/sources/{s}/chains`)를 유지하고, 새 경로로 바꾸는 일은 step 5 가 한다. `/api/v1/` 접두어 전체를 예외로 만들지 마라.
5. 기존 JSON API 들(사람 요청·사내 요청·담당 범위·GitHub 소스·매핑·지표)은 의존성만으로 토큰을 받게 된다(D20) — 라우터 파일은 고치지 않는다.

## 테스트

- 쿠키 없이 full 토큰으로 기존 JSON API 하나(예 `GET /human-requests`) 200, 틀린 토큰 401, 폐기 토큰 401, 비활성 멤버 토큰 401.
- 쿠키 + 틀린 Bearer → 401(쿠키로 넘어가지 않음).
- read 토큰으로 GET 200, POST 403 `token_read_only`(member 역할 read 토큰으로 관리자 POST 도 `token_read_only` — 순서 단정). member 역할 full 토큰으로 관리자 API → 403 `forbidden`.
- Bearer 요청은 Origin 헤더 없이 POST 해도 통과, 쿠키 POST 는 Origin 없으면 기존처럼 거부.
- 화면 라우트(`GET /tasks`)는 Bearer 만으로는 `/login` 303.
- `last_used_at` 갱신.

## 금지사항 (이 step)

- 라우트 경로를 옮기지 마라. 이유: step 4·5 범위.
- 토큰 원문을 `request.state`·로그·오류 본문에 남기지 마라. 이유: 비밀값 규칙.

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
