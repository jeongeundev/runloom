# Step 4: api-prefix — 사람용 JSON API 를 `/api/v1` 로 옮기기

## 읽어야 할 파일

- AGENTS.md, phases/24-human-api/README.md("결정" D5~D11·D31), phases/24-human-api/index.json(이전 step summary)
- docs/ARCHITECTURE.md "사람용 API — phase 24" 의 **주소 표**와 step 경계(**README 와 다르면 ARCHITECTURE 가 기준**), docs/CONTRACT.md 18절
- 이 step 이 옮기는 라우터: `src/workflow/server/github_api.py`·`human_api.py`·`metrics_api.py`·`responsibility_api.py`·`internal_request_api.py`·`mapping_api.py`, `app.py`
- 이 step 이 옮기지 않는 것: `machine_api.py`·`inbound_api.py`(step 5), `web.py` 의 화면·폼 라우트(D10 — 특히 `/responsibilities/add`·`/remove`, `/requests/…`, `/work/{key}/internal-requests` 화면 폼은 그대로)
- 템플릿의 `data-json-action="…"` 10곳(`grep -rn 'data-json-action=' src/workflow/server/templates` — `_repos_github.html` 8, `metrics.html` 1, `_cycle.html` 1), `metrics.html` 의 `/metrics.json` 링크, `base.html` 의 `data-json-action` 스크립트
- 테스트: 옛 경로를 부르는 테스트 전부 — `grep -rln "/human-requests\|/internal-requests\|/responsibilities\|/field-mappings\|/github/sources\|/metrics.json\|/metrics.csv\|/work-items/" tests`

## 작업

1. 위 6개 라우터의 경로를 ARCHITECTURE 주소 표대로 바꾼다. 앞에 `/api/v1`, 이름이 바뀌는 곳은 표의 세 곳뿐(`/api/v1/metrics`, `/api/v1/github/sources/{id}/baseline`, `/api/v1/work-items/{key}/internal-requests`).
2. 업무 키 조회 공용 함수(ARCHITECTURE 의 위치·이름 — 예외 없이 None 반환)를 만들고 웹 `_own_work` 가 그것을 쓰게 바꾼다(웹 동작 그대로 — `PageError` 404). 사내 요청 업무 경로(D31)는 경로 변수가 업무 키(`RUN-12`)다. 키 형식이 틀리거나 이 워크스페이스에 없으면 `ApiError` 404 `not_found`. 내부에서 `work_item_id` 로 바꿔 기존 함수를 부른다. 응답 본문 칸은 바꾸지 않는다. step 6·7 도 이 함수를 쓴다.
3. 옛 경로는 남기지 않는다(D9) — 별칭·303·410 없음, 옛 경로는 404(또는 같은 경로의 화면 폼 라우트가 있으면 그 라우트의 동작 그대로 — 예 `POST /responsibilities/add` 는 화면 폼).
4. 템플릿 `data-json-action` 10곳과 `metrics.html` 링크를 새 경로로. CSV 링크가 있으면 `/api/v1/metrics.csv`.
5. 테스트의 옛 경로를 새 경로로 고친다(단정은 그대로). 위 grep 은 같은 접두어의 **화면 폼 경로도 잡는다** — `/operator/github/sources/{id}/runner`(test_web_roles.py·test_web_github_connect.py), `/work/{key}/internal-requests`, `/responsibilities/add`·`/remove`, `/requests/{id}/…` 는 화면이므로 그대로 둔다. 파일마다 줄 단위로 판단하고 일괄 치환하지 마라. 옛 경로가 404 인지 단정하는 테스트를 라우터마다 하나 더한다 — POST 는 Origin 헤더를 실은 요청이어야 한다(Origin 없는 쿠키 POST 는 라우팅 전에 `web.origin_guard` 가 403 `forbidden_origin`. `tests/conftest.py` 의 클라이언트가 Origin 을 기본으로 싣는지 확인).

## 테스트

- 각 라우터 새 경로 200/201 (기존 테스트를 새 경로로), 옛 JSON 경로 404.
- `/api/v1/work-items/RUN-n/internal-requests` GET·POST 동작, 없는 키 404.
- 화면 폼 `/responsibilities/add`·`/remove`, `/work/{key}/internal-requests`, `/requests/{id}/…` 가 그대로 동작(기존 테스트 통과).
- 템플릿 렌더 결과에 옛 JSON 경로가 없다 — 부분 문자열이 아니라 앞 문맥을 포함해 단정한다(예 `'data-json-action="/github/sources' not in html`, `'href="/metrics.json' not in html`). `/api/v1/github/sources` 와 화면 경로 `/operator/github/sources/…/runner` 도 `/github/sources` 를 포함하기 때문이다.

## 금지사항 (이 step)

- `machine_api.py`·`inbound_api.py`·`connector/` 를 건드리지 마라. 이유: 러너·n8n 프로토콜은 서버·러너를 함께 바꿔야 해서 step 5 가 한다.
- 응답 본문 칸 이름을 바꾸지 마라. 이유: 이 step 은 주소만 옮긴다.

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
