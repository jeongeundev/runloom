# Step 3: me-tokens — 토큰 API 와 `/me` 화면 절

## 읽어야 할 파일

- AGENTS.md, phases/24-human-api/README.md("결정" D12~D15·D17·D21), phases/24-human-api/index.json(이전 step summary)
- docs/ARCHITECTURE.md "사람용 API — phase 24" 의 토큰 API·화면 항목(**README 와 다르면 ARCHITECTURE 가 기준**), docs/CONTRACT.md 18절 토큰 발급 예시, docs/UI_GUIDE.md
- `src/workflow/server/web.py` 의 `/me` 라우트(`me_page`·`/me/profile`·`/me/webhook…`)와 `/team/invites`(원문을 한 번만 보여 주는 방식), `views.me_context`, 템플릿 `me.html`·`team.html`(초대 링크 1회 표시 마크업)
- step 1·2 의 `repo` 토큰 함수와 `auth` 의 쿠키 전용 의존성
- 테스트: `tests/workflow/server/test_web_team.py`(`/me` 화면 단정과 초대 링크 1회 표시 단정이 모두 여기 있다)

## 작업

1. 새 라우터 파일(ARCHITECTURE 이름, 예 `server/me_api.py`, `APIRouter(prefix="/api/v1/me/tokens")`)을 만들고 `app.py` 에 등록:
   - `GET /api/v1/me/tokens` — 내 토큰 목록(이름·범위·만든 시각·마지막 사용·폐기 여부). Bearer·쿠키 모두.
   - `POST /api/v1/me/tokens` — 본문 `{"name","scope"}`, **쿠키 전용**(D21). 201 + 원문은 이 응답에만.
   - `DELETE /api/v1/me/tokens/{token_id}` — 내 토큰 폐기. 남의·없는 토큰 404 `not_found`. 성공 204.
2. `/me` 화면에 "API 토큰" 절 — 화면 폼 라우트는 `web.py` 에 둔다(step 9 대조 테스트가 endpoint 모듈 `workflow.server.web` 을 화면으로 분류한다)(ARCHITECTURE 의 문구·폼 경로): 이름 입력·범위 선택(읽기 전용 / 내 권한 전부, 기본 읽기 전용) + [토큰 만들기], 만든 직후 원문 + [복사] + "이 토큰은 지금만 보입니다" 안내(초대 링크와 같은 방식), 목록(이름·범위·만든 날·마지막 사용 또는 "사용 기록 없음")·줄마다 [폐기]. 화면 폼은 쿠키·Origin 검사를 받는 기존 화면 POST 방식.
3. 화면과 API 는 같은 repo 함수를 쓴다.

## 테스트

- API: 발급 201·원문 1회·목록에 원문·해시 없음, Bearer 로 발급 시도 → 403 `cookie_required`, 폐기 204 → 그 토큰으로 GET 401, 남의 토큰 폐기 404, read 토큰으로 DELETE → 403 `token_read_only`.
- 화면: `/me` 에 절 표시, 만들기 POST 응답에 원문이 한 번 보이고 다시 `GET /me` 하면 원문이 없다, 폐기 버튼 동작, 템플릿 자동 이스케이프(`|safe` 없음).

## 금지사항 (이 step)

- 원문을 쿼리 문자열·리다이렉트 주소·쿠키·플래시 저장소에 담아 넘기지 마라. 이유: 접근 로그·브라우저 기록에 남는다.
- 관리자용 남의 토큰 화면을 만들지 마라. 이유: README D14.

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
