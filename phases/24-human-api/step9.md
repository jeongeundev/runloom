# Step 9: human-api-verify — 끝에서 끝까지 확인

## 읽어야 할 파일

- AGENTS.md, phases/24-human-api/README.md("결정" 절 전체), phases/24-human-api/index.json(step 0~8 summary)
- docs/ARCHITECTURE.md "사람용 API — phase 24" 주소 표·응답 칸 표·오류 코드 표, docs/CONTRACT.md 18절
- step 1~7 이 만든 코드: `adapters/repo.py` 토큰 함수, `server/auth.py`, 토큰 라우터, `server/work_api.py`, 옮긴 라우터들, `connector/client.py`
- 테스트: `tests/e2e/` 의 기존 e2e 형식(서버 `TestClient`·가짜 러너), `tests/workflow/server/conftest.py`

## 작업

1. `tests/e2e/test_human_api.py`(e2e 는 미러 규칙 예외 — 기존 `tests/e2e/` 형식) 한 흐름:
   - 멤버 둘(관리자 A, member B). A 가 `/me` 화면 폼으로 full 토큰, B 가 read 토큰 발급(원문은 그 응답에서만 읽음).
   - 쿠키 없이 Bearer 만으로: B 토큰 `GET /api/v1/work-items?filter=my_turn` 200, `GET /api/v1/work-items/RUN-n` 상세에 담당 후보·`actions`.
   - B 토큰으로 `PUT …/assignee` → 403 `token_read_only`.
   - A 토큰으로 `PUT /api/v1/work-items/RUN-n/assignee` 에이전트 → 가짜 러너가 새 경로 `/api/v1/connector/claim` 으로 실행을 받는다 → 결과 → 업무 상태가 웹 목록과 API 목록에서 같다.
   - A 토큰으로 사람 요청 응답(`/api/v1/human-requests/{id}/responses`) 한 번(이미 있는 fixture 흐름이 있으면 그것).
   - A 가 B 를 비활성화 → B 토큰 401, 다시 활성화해도 401. A 가 자기 토큰 폐기 → 401.
   - 옛 경로(`/human-requests`, `/connector/claim`, `/metrics.json`, `/sources/n8n/chains`) 404(POST 는 Origin 또는 Bearer 를 실어야 라우팅까지 간다 — 없으면 `origin_guard` 가 먼저 403), 화면 폼 경로(`/responsibilities/add` 등)는 그대로, `/healthz` 200.
2. **문서 ↔ 라우트 대조 테스트**(`tests/workflow/server/test_app.py` 등 미러 위치): ARCHITECTURE 의 라우트 분류 규칙대로 — endpoint 모듈이 `workflow.server.web` 인 라우트(화면)와 `/healthz`·FastAPI 기본 문서 경로·`/static` 마운트를 뺀 모든 라우트가 `/api/v1/` 로 시작하는지, ARCHITECTURE 주소 표의 새 경로가 모두 실제 라우트에 있는지(문서 파싱이 불안정하면 표를 테스트 안 상수로 옮기고 문서와 같다고 summary 에 남긴다).
3. 비밀값 단정: 전체 흐름 뒤 DB 모든 표·접근 로그 캡처·모든 JSON 응답(발급 응답 제외)에 토큰 원문이 없다.
4. 실패가 나오면 원인 step 의 코드를 고치되, 고친 내용과 이유를 summary·concerns 에 남긴다.

## 테스트

- 위 e2e 와 대조 테스트, 전체 회귀.

## 금지사항 (이 step)

- 실제 러너·n8n·셀프호스트를 띄우지 마라. 이유: 가짜 러너·`TestClient` 로 검증한다.
- 실패하는 단정을 지우거나 약하게 바꿔 통과시키지 마라. 이유: 이 step 이 phase 의 마지막 검증이다.

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
