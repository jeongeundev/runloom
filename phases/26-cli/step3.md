# Step 3: cli-core — 자격 파일·서버 주소 규칙·HTTP 클라이언트·import 경계 테스트

## 읽어야 할 파일

- AGENTS.md, `phases/26-cli/README.md`("결정" D5·D6·D7·D9·D10·D11·D24), `phases/26-cli/index.json`(이전 step summary)
- `docs/adr/0034-cli.md`, `docs/ARCHITECTURE.md` "CLI — phase 26"(**README 와 다르면 ARCHITECTURE 가 기준** — 모듈·시그니처·문구)
- `src/workflow/contracts/v1.py`(`API_PREFIX`), `src/workflow/connector/config.py`(`write_token` — 0600·원자 교체 선례), `src/workflow/connector/client.py`(HTTPX 클라이언트·오류 분류 선례), `src/workflow/server/errors.py`(오류 본문 `code`·`message`·`field`·`details`)
- step 1 결과(`GET /api/v1/me`)

## 작업

1. 패키지 `src/workflow/cli/__init__.py`(빈 모듈 설명), `config.py`, `client.py` — ARCHITECTURE 시그니처 그대로.
2. `config.py`
   - 경로: `RUNLOOM_HOME` 이 있으면 `$RUNLOOM_HOME/credentials.json`, 없으면 `$HOME/.config/runloom/credentials.json`. `XDG_CONFIG_HOME` 은 보지 않는다.
   - 저장: 디렉터리 0700, 임시 파일(0600) 쓰고 `os.replace`. 내용 `{"server": …, "token": …}`.
   - 읽기: `RUNLOOM_TOKEN`·`RUNLOOM_SERVER` 가 있으면 각각 파일 값보다 먼저. 파일을 읽어야 할 때 파일 모드에 그룹·다른 사용자 비트(`0o077`)가 있으면 `ConfigError`(문구에 `chmod 600 <경로>`). 둘 중 하나라도 없으면 `ConfigError`("runloom login --server URL 로 먼저 로그인하세요.").
   - `normalize_server`: 스킴 `http`·`https` 만, `http` 는 호스트가 `localhost`·`127.0.0.1`·`::1` 일 때만, 끝의 `/` 제거, 경로·쿼리가 있으면 거절. 환경변수로 받은 주소에도 적용.
   - `Credentials.__repr__` 에 토큰을 넣지 않는다.
3. `client.py`
   - `ApiClient` 는 `http` 가 주어지면 그 `httpx.Client` 를 쓰고(테스트는 `TestClient` 를 넣는다 — 이때 기준 주소는 그 클라이언트의 것), 없으면 `httpx.Client(base_url=server, timeout=timeout)` 를 만든다.
   - 모든 요청 경로 = `API_PREFIX + path`, 헤더 `Authorization: Bearer <token>`. JSON 본문만 보낸다.
   - 2xx 면 JSON(204 는 None). 아니면 본문의 `code`·`message` 로 `ApiError(status, code, message)`(본문이 JSON 이 아니면 `code="http_error"`, `message` = 상태 문구). `httpx.TransportError`·시간 초과 → `Unreachable`.
   - 토큰을 로그·예외 문구·`repr` 에 넣지 않는다.
4. **테스트 패키지**: `tests/workflow/cli/__init__.py` 와 `tests/workflow/cli/conftest.py` 를 만든다. 서버 픽스처(`settings`·`app`·`client`·`conn`·자동 적용 `_cheap_scrypt` 등)는 `tests/workflow/server/conftest.py` 에 있어 다른 디렉터리에서 보이지 않는다 — conftest 에서 `from tests.workflow.server.conftest import …` 로 다시 내보낸다(이름은 그 파일을 읽고 맞춘다. 다른 디렉터리의 conftest 재사용 선례가 있으면 그것을 따른다). 멤버·`wfm_` 토큰을 만드는 도우미도 여기에 둔다.
5. **import 경계 테스트**(D5): `tests/workflow/cli/test_boundary.py` — `src/workflow/cli/*.py` 를 `ast` 로 읽어 `workflow.server`·`workflow.connector`·`workflow.domain`·`workflow.adapters` import 가 없음을 단정. 같은 파일에서 `src/workflow/server`·`src/workflow/connector` 가 `workflow.cli` 를 import 하지 않음도 단정.

## 테스트

`tests/workflow/cli/test_config.py`·`test_client.py`(+ 위 경계 테스트). `tmp_path` 와 env dict 주입으로 실제 홈을 건드리지 않는다.
- 저장 뒤 파일 0600·디렉터리 0700·내용, 다시 저장하면 교체, `delete` 뒤 없음.
- 환경변수 우선(토큰만·서버만·둘 다), 파일 없음 → `ConfigError`, 0644 파일 → `ConfigError`(chmod 문구).
- `normalize_server`: `http://localhost:8000/` → 끝 `/` 제거, `http://127.0.0.1`·`http://[::1]:8000` 허용, `http://example.com`·`http://192.168.0.5:8000`·`ftp://…`·`https://x/path` 거절, `https://runloom.example.com` 허용. 환경변수 `RUNLOOM_SERVER=http://example.com` 도 거절.
- `ApiClient`: 실제 앱(`TestClient` — 기존 서버 테스트의 앱·멤버·토큰 만드는 픽스처를 찾아 쓴다)으로 `get("/me")` 200, 틀린 토큰 → `ApiError(401, "unauthenticated", …)`, 없는 업무 → 404 `not_found`, 연결 안 되는 주소 → `Unreachable`(httpx `MockTransport` 로 `ConnectError` 를 던져도 된다).
- `repr(Credentials(...))`·예외 문자열에 토큰 원문 없음.
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

- `workflow.server`·`workflow.connector`·`workflow.domain`·`workflow.adapters` 를 `src/workflow/cli/` 에서 import 하지 마라. 이유: D5 — CLI 는 API 클라이언트다.
- 토큰을 명령 인자·로그·예외 문구에 넣지 마라. 이유: AGENTS.md 비밀값 규칙.
- 자동 재시도를 넣지 마라. 이유: D24 — 행동 API 는 멱등 키가 없는 것이 있다.
- 실제 홈 디렉터리(`~/.config/runloom`)를 테스트에서 쓰지 마라. 이유: 개발자 자격 파일을 덮는다.
- 기존 테스트를 깨뜨리지 마라.
