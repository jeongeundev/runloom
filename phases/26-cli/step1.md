# Step 1: me-api — `GET /api/v1/me` 와 리포트 보기의 요청자 id

## 읽어야 할 파일

- AGENTS.md, `phases/26-cli/README.md`("결정" D3·D28), `phases/26-cli/index.json`(step 0 summary)
- `docs/adr/0034-cli.md`, `docs/ARCHITECTURE.md` "CLI — phase 26"(**README 와 다르면 ARCHITECTURE 가 기준**)·"사람용 API — phase 24" 의 "### 주소 표", `docs/CONTRACT.md` 20절
- `src/workflow/server/member_token_api.py`, `src/workflow/server/auth.py`(`LoggedIn`·`require_member_api`), `src/workflow/server/app.py`(라우터 등록), `src/workflow/server/handoff_reports.py`(`report_view`)
- `tests/workflow/server/test_member_token_api.py`(있으면), `tests/workflow/server/test_app.py`(주소 표 대조·`len(rows) == …` 단정), `tests/workflow/server/test_report_api.py`·`test_handoff_reports.py`

## 작업

1. `GET /api/v1/me` — 응답 `{"member_id", "display_name", "role", "token_scope"}`(`token_scope` = `LoggedIn.token_scope`, 쿠키 로그인은 `null`). 의존성은 `require_member_api`(쿠키·`wfm_` 모두, 읽기 전용 토큰 허용, 미인증 401). 둘 곳은 ARCHITECTURE 가 정한 모듈(정하지 않았으면 `member_token_api.py` 에 `/me` 접두어 라우터를 하나 더 두거나 같은 파일의 새 라우터 — 기존 `/me/tokens` 경로와 동작은 바꾸지 않는다). `app.py` 에 등록.
2. `handoff_reports.report_view` 반환에 `requested_by_member_id`(원본 = `None`, 맞춤 풀이 = 요청 멤버 id). 기존 `requested_by`(표시 이름)는 그대로. 웹 템플릿은 바꾸지 않는다.
3. ARCHITECTURE "사람용 API — phase 24" "### 주소 표" 의 "새로 여는 라우트" 표에 `/api/v1/me` 행을 더하고, `tests/workflow/server/test_app.py` 의 행 수 단정을 `+ 1` 로 고친다(주석에 phase 26).

## 테스트

- `/api/v1/me`: 쿠키 로그인 200 + `token_scope` null, `wfm_` read 토큰 200 + `read`, full 토큰 200 + `full`, 토큰 없음 401, 폐기된 토큰 401, 응답에 토큰 원문·해시·이메일·비밀번호 해시 없음.
- `report_view`: 원본 `requested_by_member_id` None, 맞춤 풀이는 요청 멤버 id. 리포트 API JSON 에도 칸이 있다.
- 주소 표 대조 테스트 통과.
## 테스트 먼저

소스를 바꾸기 전에 `tests/` 미러 경로(`src/workflow/x/y.py` → `tests/workflow/x/test_y.py`)에 실패하는 테스트를 먼저 작성하고, 실패 원인이 의도한 것인지 확인한다(`tdd-guard.sh` 가 테스트 없는 소스 작성을 막는다). 구현한 뒤 해당 테스트와 전체 회귀를 통과시킨다. 이 step 의 변경으로 기존 테스트(개수·이름 집합·문구 단정)가 깨지면 새 동작 기준으로 고치되 단정을 약하게 만들지 않는다. 무엇을 왜 바꿨는지는 summary 에 남긴다.

## Acceptance Criteria

```bash
python3 -m pytest -q
python3 -m ruff check .
```

위 커맨드와 이 step 의 "테스트" 항목을 모두 확인한다.

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트: ARCHITECTURE.md 디렉토리 구조·외부 의존 표를 벗어나지 않았는가, ADR 기술 스택 안인가, AGENTS.md CRITICAL 규칙(도메인 순수성, `server`↔`connector`(↔`cli`) 상호 import 금지, 외부 입력으로 명령·경로 실행 금지, 비밀값)을 지켰는가, GLOSSARY 용어를 그대로 썼는가.
3. `phases/26-cli/index.json` 의 이 step 을 갱신한다: 성공 → `"status": "completed"`, `"summary"`(다음 step 에 필요한 파일·이름·결정), `"verification"`(실행한 AC 명령과 종료 코드 목록 `[{"command": "...", "exit_code": 0}]`), 걸리는 점이 있으면 `"concerns"`. 3회 수정해도 실패 → `"error"` + `"error_message"`. 사용자 개입 필요 → `"blocked"` + `"blocked_reason"` 후 즉시 중단.

## 금지사항

- `/api/v1/me` 에서 다른 멤버 정보·토큰 목록을 돌려주지 마라. 이유: 자기 정보 확인용이다(D28). 토큰 목록은 기존 `/api/v1/me/tokens`.
- 웹 템플릿을 바꾸지 마라. 이유: D28 — 화면은 표시 이름만 쓴다.
- 기존 테스트를 깨뜨리지 마라.
