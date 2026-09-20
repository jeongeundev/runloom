# Step 2: claude-adapter

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/AGENTS.md` — CRITICAL: 외부 입력에서 명령·경로를 받아 실행하지 않는다, 비밀값을 프로세스 환경에 넣지 않는다
- `/docs/ARCHITECTURE.md` — "연결 프로그램", "코드 수정 실행 계약", 도구 확인 절(`Claude Code 2.1.278`: `--print`, `--output-format stream-json`, `--json-schema` 확인 기록)
- `/docs/adr/0001-first-local-agent-codex.md`
- `/src/workflow/connector/local_tool.py` — step 1 의 공통 흐름. `launch`·`parse_last_message` 만 구현하면 된다
- `/src/workflow/connector/codex.py` — 같은 모양의 참고 구현 (스키마 파일, stdin 프롬프트, 타임아웃·종료 처리)
- `/src/workflow/connector/prompt.py` — `build_prompt` (도구 무관, 그대로 쓴다)
- `/tests/workflow/connector/test_codex.py` — 가짜 실행 파일로 어댑터를 검증하는 방식을 그대로 따른다

이전 step에서 만들어진 코드를 꼼꼼히 읽고, 설계 의도를 이해한 뒤 작업하라. 새 기능은 테스트를 먼저 작성한다 (AGENTS.md TDD, `tests/` 는 `src/` 미러 배치).

## 배경

두 번째 로컬 도구 Claude Code CLI 어댑터. 설치본 `claude 2.1.278` 에서 확인한 비대화형 옵션: `-p/--print`, `--output-format json|stream-json`, `--json-schema <schema>`(구조화 출력), `--permission-mode acceptEdits|auto|bypassPermissions|manual|dontAsk|plan`, `--allowedTools <tools...>`, `--disallowedTools`, `--add-dir`, `--no-session-persistence`, `--max-budget-usd`. ARCHITECTURE 는 "승인·샌드박스 우회 정책을 제품 런타임에 복사하지 않는다" 고 하므로 `bypassPermissions` 는 쓰지 않는다.

**시작 전 1회 확인 (유료 아님, 구독 사용)**: 출력 형태를 추측하지 말고 아래를 한 번 실행해 최상위 JSON 키(예: `result`, `structured_output`, `is_error`, `usage`)와 구조화 출력이 어디에 오는지 기록한 뒤 fixture 를 그 형태로 만든다.

```bash
claude -p --output-format json --no-session-persistence \
  --json-schema '{"type":"object","properties":{"ok":{"type":"boolean"}},"required":["ok"],"additionalProperties":false}' \
  'ok 를 true 로 답하라' | head -c 2000
```

## 작업

### `src/workflow/connector/claude.py` (신규)

```python
class ClaudeAdapter(LocalToolAdapter):
    tool_name = "claude"
    raw_kinds = ("claude_jsonl", "claude_stderr")
    def __init__(self, state_conn, *, claude_bin: str = "claude", timeout_seconds: int = 1200,
                 permission_mode: str = "acceptEdits", env_base=os.environ, verification_timeout: int = 300): ...
    def build_argv(self, worktree: Path, schema_json: str) -> list[str]: ...
    def launch(self, worktree: Path, prompt_text: str, progress: Progress) -> ToolRun: ...
    def parse_last_message(self, raw: str | None) -> ToolResult: ...
```

- `build_argv` 는 **고정 인자만**:
  `[claude_bin, "-p", "--output-format", "json", "--no-session-persistence", "--permission-mode", permission_mode, "--allowedTools", <고정 목록>, "--json-schema", schema_json]`
  고정 목록은 `Read`, `Edit`, `Write`, `Glob`, `Grep`, `Bash(python3 -m pytest*)`, `Bash(python3 -m daily_report*)`, `Bash(git diff*)`, `Bash(git status*)` — 프롬프트·인계 자료·사용자 문자열은 인자에 넣지 않는다. 작업 디렉터리는 `cwd=worktree` 로 준다 (`-C` 없음). 프롬프트는 stdin.
- `schema_json` 은 Codex 와 같은 결과 스키마(`{summary, outcome}`, `additionalProperties: false`)를 문자열로 준다. Codex 는 파일 경로, Claude 는 문자열 — 스키마 내용은 한 곳(`local_tool.py` 의 상수)에 두고 둘이 공유한다.
- `launch`: `subprocess.Popen(argv, cwd=worktree, stdin=PIPE, stdout=PIPE, stderr=PIPE, env=<비밀값 제거된 env>)`, 프롬프트를 stdin 으로 쓰고 닫는다, `timeout_seconds` 초과 시 terminate → 5초 → kill, `ToolRun` 반환. `last_message` 는 stdout 전체(`--output-format json` 은 JSON 한 덩어리)에서 구조화 출력 필드를 꺼낸 문자열, 없으면 None.
- `parse_last_message`: 위 1회 확인에서 본 형태로 파싱. `is_error` 참이거나 구조화 출력이 없으면 `ToolResult(outcome="needs_information", summary=…, parse_note=…)`. 사용량 한도 문구(rate limit / usage limit / 429)가 stdout·stderr 에 있으면 공통 흐름이 `failed(code="usage_limit", …)` 로 끝내도록 `ToolRun` 에 표시한다 — 방법은 `LocalToolAdapter` 에 `classify_failure(run) -> tuple[str, str] | None` 훅을 추가해 하위 클래스가 판정하게 한다 (Codex 는 None 반환 유지. phase 3 이 `retry_after` 를 붙인다).
- 로그 산출물 이름: `claude.jsonl`(stdout 원문, kind `claude_jsonl`), `claude-stderr.txt`.
- 환경: `CLAUDE_*`·`ANTHROPIC_*` 는 **부모 환경의 것을 그대로 물려준다** (사용자 Mac 의 로그인 재사용 — ADR-0001 이 Codex 에 적용한 것과 같은 원칙). `OPENAI_*`·`WORKFLOW_*`·`DIAG_*`·연결 토큰은 제거한다 (codex.py 의 규칙 재사용).

### `src/workflow/connector/cli.py`

- `ADAPTERS["claude"] = lambda conn, env: ClaudeAdapter(conn, env_base=env)`. 등록·실행 선택은 step 3 이 바꾼다 — 여기서는 factory 만 추가한다.

### 테스트 (먼저 작성) — `tests/workflow/connector/test_claude.py`

test_codex.py 방식 그대로: 가짜 `claude` 실행 파일(PATH 앞)이 인자를 파일에 기록하고 대본 JSON 을 stdout 에 쓴다.
- argv 가 고정 목록과 정확히 일치하고 프롬프트·인계 경로가 인자에 없다, `cwd` 가 worktree 다.
- 정상 대본(변경 있음 + 재현 테스트 있음) → `ready_for_review`, 산출물에 `diff`·`test_log_before`·`test_log_after`·`verification_log`·`report_output`·`claude_jsonl`·`claude_stderr`.
- `is_error` 대본 / 구조화 출력 없음 → `needs_information` + parse_note.
- 사용량 한도 문구 대본 → `failed("usage_limit", …)`.
- 자식 env 에 `OPENAI_API_KEY`·`WORKFLOW_*`·연결 토큰이 없고 `ANTHROPIC_*` 는 남아 있다.
- stdout/stderr 산출물이 마스킹된다(토큰 모양 문자열).

## Acceptance Criteria

```bash
python3 -m pytest tests/workflow/connector -q
python3 -m pytest -q
python3 -m ruff check .
grep -n "bypassPermissions\|dangerously" src/workflow/connector/claude.py ; test $? -eq 1   # 없어야 한다
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - ARCHITECTURE.md 디렉토리 구조를 따르는가?
   - ARCHITECTURE.md 외부 의존 표에 없는 외부 서비스를 끌어들이지 않았는가?
   - ADR 기술 스택을 벗어나지 않았는가?
   - AGENTS.md CRITICAL 규칙을 위반하지 않았는가? (도메인이 I/O 를 import 하지 않는가, server↔connector 가 서로 import 하지 않는가, 외부 입력에서 명령·경로를 실행하지 않는가, 비밀값이 DB·로그·응답에 없는가)
   - GLOSSARY.md 용어를 그대로 썼는가? 금지 표현을 쓰지 않았는가?
3. 결과에 따라 `phases/4-claude-issues/index.json`의 해당 step을 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"` (만든 파일·함수·결정 사항)
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- `--permission-mode bypassPermissions` 나 `--dangerously-skip-permissions` 를 쓰지 마라. 이유: ARCHITECTURE "승인·샌드박스 우회 정책을 제품 런타임에 복사하지 않는다".
- 프롬프트·인계 자료·저장소 경로를 argv 에 넣지 마라. 이유: AGENTS.md CRITICAL — 실행 파일과 인자 배열은 어댑터가 고정한다.
- `local_tool.py` 의 공통 판정 규칙을 Claude 편의로 바꾸지 마라. 이유: step 1 의 리팩터링 전제.
- 실제 `claude` 를 테스트에서 호출하지 마라(위 1회 확인 제외). 이유: 구독 한도를 쓰고 비결정적이다. 실연동은 step 5.
- 기존 테스트를 깨뜨리지 마라
