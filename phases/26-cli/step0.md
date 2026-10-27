# Step 0: cli-design — ADR-0034·ARCHITECTURE·CONTRACT·GLOSSARY·AGENTS.md 로 이름·인자·출력 형태 고정

## 읽어야 할 파일

- AGENTS.md, `phases/26-cli/README.md`("결정" 절 D1~D31 전부 — 이 step 이 구현 기준으로 옮긴다)
- `docs/adr/0031-handoff-understanding-and-clients.md`, `docs/adr/0032-human-api-and-member-tokens.md`, `docs/adr/0033-handoff-report.md`
- `docs/ARCHITECTURE.md` "사람용 API — phase 24"(특히 "### 주소 표" 와 그 "새로 여는 라우트" 표), "인계 리포트 — phase 25"(사람용 API·화면 문구 — 리포트 절 제목·상태 문구)
- `docs/CONTRACT.md` 18절·19절, `docs/GLOSSARY.md` 마지막 "계획 용어" 절들
- 코드: `src/workflow/server/work_api.py`(`_item`·`_detail`·라우트), `src/workflow/server/report_api.py`, `src/workflow/server/handoff_reports.py`(`report_view`·`stage_reports`), `src/workflow/server/human_api.py`(`ResponseBody`·`list_requests`·`post_response`), `src/workflow/server/member_token_api.py`, `src/workflow/server/auth.py`(`LoggedIn`·`require_member_api`), `src/workflow/connector/masking.py`, `src/workflow/connector/cli.py`(기존 러너 CLI 의 argparse·출력 관례), `src/workflow/server/templates/` 의 리포트 템플릿(절 제목 한국어 문구를 찾는다)
- 테스트: `tests/workflow/server/test_app.py`(주소 표 대조·행 수 단정), `tests/workflow/contracts/test_v1.py`(CONTRACT 예시 대조 — 키 서명 규칙)

## 작업

문서만 바꾼다. 코드는 바꾸지 않는다. README 결정을 코드 이름으로 옮기며 확인하고, 코드 사실이 README 와 어긋나면 ADR "코드 조사로 README 와 달라진 사실" 절에 적고 [사용자] 결정은 바꾸지 않는다(바꿔야 하면 `blocked`).

1. **`docs/adr/0034-cli.md`** — ADR-0032·0033 과 같은 꼴(배경·코드 조사로 달라진 사실·결정·하지 않는 것·결과). 결정 원본은 `phases/26-cli/README.md` D1~D31 이고, 이름·인자·출력의 기준은 ARCHITECTURE "CLI — phase 26" 이라고 적는다. ADR-0031 "결과" 또는 순서 6 근처에 ③ → ADR-0034 링크 한 줄.
2. **`docs/ARCHITECTURE.md` 새 절 "## CLI — phase 26"**(마지막 phase 절 뒤). 담을 것:
   - 모듈 표: `src/workflow/cli/__init__.py`·`__main__.py`(→ `main()`)·`config.py`(자격 파일·환경변수·주소 규칙)·`client.py`(HTTP)·`main.py`(argparse·명령)·`render.py`(텍스트 렌더). 시그니처 수준으로 고정한다. 최소한:
     - `config.Credentials(server: str, token: str)`(frozen, `repr` 에 토큰 없음), `config.credentials_path(env: Mapping[str, str]) -> Path`, `config.load(env) -> Credentials`(환경변수 우선·각각 따로·파일 권한 검사·없으면 `ConfigError`), `config.save(env, creds) -> Path`, `config.delete(env) -> bool`, `config.normalize_server(url: str) -> str`(D11 규칙, 위반이면 `ConfigError`), `config.ConfigError`
     - `client.ApiClient(server: str, token: str, *, http: httpx.Client | None = None, timeout: float = 30.0)` 와 메서드 `get(path, params=None) -> Any`·`put(path, json=None)`·`post(path, json=None)`·`delete(path)` — 경로는 `contracts.v1.API_PREFIX` 뒤 부분, `Authorization: Bearer`, 2xx 가 아니면 `client.ApiError(status, code, message)`, 네트워크 실패·시간 초과는 `client.Unreachable`
     - `main.main(argv: list[str] | None = None, *, env: Mapping[str, str] | None = None, stdin=None, stdout=None, stderr=None, http: httpx.Client | None = None) -> int` — 테스트가 env·입출력·HTTP 를 주입한다
     - console script 진입 함수 `main.main_entry() -> NoReturn`(`sys.exit(main())`) — pyproject `[project.scripts] runloom = "workflow.cli.main:main_entry"`, `__main__.py` 도 이것을 부른다
   - argparse 와 `main()`: `main` 은 `SystemExit` 을 밖으로 던지지 않고 int 를 돌려준다 — 사용법 오류는 주입된 `stderr` 에 쓰고 2, `--help` 는 주입된 `stdout` 에 쓰고 0(argparse 하위 클래스로 `print_message`·`exit`·`error` 를 덮는 등). 테스트는 반환값으로 단정한다.
   - `login` 성공 문구의 역할은 API 의 `role` 값 그대로(번역 없음 — `cli` 는 `domain` 을 import 하지 않는다).
   - 명령 표: D7·D8·D14~D22 의 명령마다 인자, 부르는 API(메서드·경로), 텍스트 출력 꼴(예시 블록), `--json` 출력(D23 의 합친 키 `detail`·`reports`·`report`·`tailored`·`more_my_turn` — 업무 있음·없음 두 경우의 예시 JSON 을 적어 고정한다), exit code(D24).
   - 리포트 텍스트 렌더 규칙(D16~D18): 펼칠 단계 고르는 규칙과 그 단계의 내 최신 맞춤 풀이 고르는 규칙(D16 — 기준 원본이 그 단계의 원본이면 최신이 아니어도, `stale` 이면 `옛 리포트 기준`), 절 제목(웹 리포트 페이지와 같은 문구 — 템플릿에서 찾아 그대로 적는다), `근거 확인 안 됨`·`기록 없음`·`작성 중`·`리포트 없음(사유)`·`옛 리포트 기준` 문구.
   - 자격 파일 형식·경로·권한, 서버 주소 규칙(D11 — localhost 만 http, 끝 `/` 제거, 경로·쿼리 거절), 환경변수 `RUNLOOM_TOKEN`·`RUNLOOM_SERVER`·`RUNLOOM_HOME`, http 허용 호스트.
   - 오류 출력 꼴 `오류 <status> <code>: <message>` 와 401 안내 문구, 네트워크 실패 문구.
   - 서버 변경: `GET /api/v1/me` 응답 칸(D28)과 `report_view` 의 `requested_by_member_id`. **"사람용 API — phase 24" 의 "### 주소 표" "새로 여는 라우트" 표에 넣을 행은 이 step 에서 적지 않는다** — `test_app.py` 가 그 표 행 수와 실제 라우트를 대조하므로 step 1 이 라우트와 함께 행을 더한다. 이 절에는 step 1 이 할 일로만 적는다.
   - 러너 변경(D12): `RESERVED_ENV_NAMES` 에 `RUNLOOM_TOKEN`·`RUNLOOM_SERVER`, `SECRET_PATTERNS` 에 `wfm_` 와 대체 문자열 `wfm_***`.
   - step 경계 표(README "step" 표).
3. **`docs/CONTRACT.md` 새 절 "## 20. CLI — phase 26"**: `GET /api/v1/me` 응답 예시(토큰·쿠키 두 경우)와 리포트 보기의 `requested_by_member_id` 가 들어간 조각. `tests/workflow/contracts/test_v1.py` 가 CONTRACT 의 json 블록을 계약 모델과 대조하는 방식을 읽고, 새 예시가 기존 모델 키 서명에 잘못 걸리지 않게 블록 언어·키를 고른다(19절이 쓴 방식을 따른다). 대조 대상이 아니어야 하면 그 테스트가 건너뛰는 표기를 쓴다.
4. **`docs/GLOSSARY.md`** 새 절 "## 계획 용어 — phase 26 CLI (미구현)": `runloom` CLI, 자격 파일, `내 차례`(CLI `next` 후보 = 웹 `내 차례` 필터와 같은 뜻), 금지 표현(예: CLI 를 "러너"·"연결 프로그램" 이라 부르지 않는다 — 러너 CLI 는 `python3 -m workflow.connector`).
5. **`AGENTS.md`**:
   - 아키텍처 규칙의 CRITICAL `server`↔`connector` 줄을 `src/workflow/server/`·`src/workflow/connector/`·`src/workflow/cli/` 는 서로 import 하지 않고 `contracts/` 만 공유한다로 넓히고, `cli` 는 `domain`·`adapters` 도 import 하지 않는다고 적는다.
   - 비밀값 줄의 멤버 토큰 설명에 CLI 자격 파일(`~/.config/runloom/credentials.json`, 0600)과 `RUNLOOM_TOKEN` 을 허용 위치로 더한다.
   - 명령어 블록에 `runloom --help   # 사람용 CLI (docs/CLI.md)` 한 줄.

## 테스트

문서 step 이다. 새 테스트는 없지만 CONTRACT·ARCHITECTURE 를 읽는 기존 테스트(`test_app.py`·`tests/workflow/contracts/test_v1.py`·`tests/test_n8n_example.py`)가 통과해야 한다.

## Acceptance Criteria

```bash
python3 -m pytest -q
python3 -m ruff check .
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. README D1~D31 이 ADR-0034 또는 ARCHITECTURE 의 어딘가에 대응하는지 하나씩 확인한다. 빠진 것이 있으면 채운다.
3. `phases/26-cli/index.json` 의 이 step 을 갱신한다: 성공 → `"status": "completed"`, `"summary"`(다음 step 이 쓸 이름·경로·문구), `"verification"`(실행한 AC 명령과 종료 코드 목록), 걸리는 점은 `"concerns"`. 3회 수정해도 실패 → `"error"` + `"error_message"`. 사용자 개입 필요 → `"blocked"` + `"blocked_reason"` 후 즉시 중단.

## 금지사항

- 코드(`src/`)·테스트를 바꾸지 마라. 이유: 이 step 은 이름 고정만 하고, 구현은 step 1~6 이 테스트 먼저로 한다.
- ARCHITECTURE "사람용 API — phase 24" 의 "### 주소 표" 에 `/api/v1/me` 행을 넣지 마라. 이유: 라우트가 아직 없어 `test_app.py` 대조가 깨진다 — step 1 이 함께 넣는다.
- [사용자] 결정을 바꾸지 마라. 이유: 사용자가 정한 범위다. 코드 사실과 충돌하면 `blocked`.
- 기존 테스트를 깨뜨리지 마라.
