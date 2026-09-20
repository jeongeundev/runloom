# Step 1: local-tool-base

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/AGENTS.md`
- `/docs/ARCHITECTURE.md` — "연결 프로그램", "코드 수정 실행 계약", "worktree"
- `/docs/adr/0001-first-local-agent-codex.md` — 트레이드오프 절: "두 도구 공용 추상화를 미리 만들지 않는다". 이제 두 번째 도구가 실제로 생기므로 공통 흐름을 뽑아낼 때다
- `/docs/GLOSSARY.md`
- `/src/workflow/connector/codex.py` — `CodexAdapter.run` 전체. `_launch` 이후의 흐름(변경 여부 → 커밋 → 수정 전 재현 테스트 → 수정 후 테스트 → 깨끗한 체크아웃에서 검증 프로필·보고서 → diff → outcome 판정 → 산출물 조립)은 도구와 무관하다
- `/src/workflow/connector/adapter.py` — `ExecutionAdapter` Protocol, `AdapterOutput`, `make_meta`
- `/src/workflow/connector/git_ops.py`, `/src/workflow/connector/prompt.py`, `/src/workflow/connector/masking.py`, `/src/workflow/connector/state.py`
- `/tests/workflow/connector/test_codex.py` — 지금 Codex 어댑터를 어떻게 검증하는지 (가짜 실행 파일·대본)

이전 step에서 만들어진 코드를 꼼꼼히 읽고, 설계 의도를 이해한 뒤 작업하라. 새 기능은 테스트를 먼저 작성한다 (AGENTS.md TDD, `tests/` 는 `src/` 미러 배치).

## 배경

Claude 어댑터(step 2)는 "프로세스를 띄우고 마지막 메시지를 얻는" 부분만 다르고 나머지 흐름은 Codex 와 같다. 복사해서 두 벌을 두면 검증 규칙(재현 테스트 필수, 깨끗한 체크아웃에서 검증, 변경 없음 → `needs_information`)이 갈라진다. 이 step 은 **동작 변경 없는 리팩터링**으로 공통 흐름을 한 곳으로 옮긴다. 기존 Codex 테스트가 그대로 통과해야 한다.

## 작업

### `src/workflow/connector/local_tool.py` (신규)

```python
@dataclass(frozen=True)
class ToolRun:
    pid: int
    started_at: str
    exit_code: int | None
    stdout: bytes          # 원문 (마스킹 전)
    stderr: bytes
    timed_out: bool
    stopped: bool
    last_message: str | None   # 도구가 낸 마지막 구조화 메시지 원문(JSON 문자열) 또는 None

@dataclass(frozen=True)
class ToolResult:
    outcome: Literal["ready_for_review", "needs_information"]
    summary: str
    parse_note: str | None    # 마지막 메시지를 못 읽었을 때 사유

class LocalToolAdapter:
    """로컬 도구 공통 흐름. 하위 클래스는 `tool_name`·`raw_kinds`·`launch`·`parse_last_message` 만 구현한다."""
    tool_name: str                          # "codex" | "claude" — 실패 코드 `{tool}_unavailable` 와 로그 이름에 쓴다
    raw_kinds: tuple[str, str]              # ("codex_jsonl", "codex_stderr") 처럼 원시 stdout/stderr 산출물 kind
    def __init__(self, state_conn, *, timeout_seconds=1200, env_base=os.environ, verification_timeout=300): ...
    def run(self, request: ExecutionRequest, handoff_dir: Path, progress: Progress) -> AdapterOutput: ...
    def launch(self, worktree: Path, prompt_text: str, progress: Progress) -> ToolRun: ...        # 추상
    def parse_last_message(self, raw: str | None) -> ToolResult: ...                                # 추상
```

- `run` 은 지금 `CodexAdapter.run` 의 흐름을 그대로 옮긴다. 도구 이름이 들어가던 문자열(`"codex_unavailable"`, `"Codex 실행이 …초를 초과"`, 산출물 파일 이름 `codex.jsonl`)은 `tool_name`·`raw_kinds` 로 만든다.
- 마스킹(`masking.py`)은 원시 stdout/stderr 산출물을 만들 때 공통 흐름에서 적용한다.
- 부모 환경에서 자식으로 넘기는 env 규칙(비밀값 제거)은 지금 codex.py 에 있는 것을 그대로 옮긴다 — AGENTS.md CRITICAL "비밀값을 Codex 프로세스 환경에 넣지 않는다" 는 Claude 프로세스에도 똑같이 적용한다.

### `src/workflow/connector/codex.py`

- `CodexAdapter(LocalToolAdapter)` 로 바꾸고 `tool_name="codex"`, `raw_kinds=("codex_jsonl","codex_stderr")`, `build_argv`·`launch`(기존 `_launch`)·`parse_last_message`(기존 `_parse_last_message` + outcome 판정) 만 남긴다. 공개 시그니처(`CodexAdapter(conn, codex_bin=…, timeout_seconds=…, sandbox=…, approval_policy=…, env_base=…, verification_timeout=…)`, `build_argv`, `run`)는 유지한다.
- `cli.py` 의 `ADAPTERS["codex"]` factory 는 그대로 동작해야 한다.

### 테스트

- `tests/workflow/connector/test_codex.py` 는 **수정하지 않는다** (리팩터링의 안전망).
- `tests/workflow/connector/test_local_tool.py` (신규): 가짜 하위 클래스(launch 가 대본을 돌려주는)로 공통 흐름을 검사한다 — 변경 없음 → `needs_information`, 재현 테스트 없음 → `needs_information`, timeout → `failed("timeout", …, stopped)`, 실행 파일 없음(`OSError`) → `failed("{tool_name}_unavailable")`, 원시 산출물 kind 가 `raw_kinds` 를 따름, stdout/stderr 가 마스킹됨.

## Acceptance Criteria

```bash
python3 -m pytest tests/workflow/connector -q
python3 -m pytest -q
python3 -m ruff check .
git diff --stat tests/workflow/connector/test_codex.py | tail -1     # 출력 없음 (미변경)
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

- `test_codex.py` 를 고치지 마라. 이유: 동작이 안 바뀌었다는 증거다. 통과하지 않으면 리팩터링이 틀린 것이다.
- 공통 흐름의 판정 규칙(변경 없음·재현 테스트 없음 → `needs_information`, 검증은 깨끗한 체크아웃에서)을 바꾸지 마라. 이유: ARCHITECTURE "완료 판정" 계약이며 phase 0 e2e 가 이를 전제한다.
- Claude 어댑터를 이 step 에서 만들지 마라. 이유: step 2 다. 여기서는 Codex 한 종류만으로 리팩터링을 닫는다.
- 기존 테스트를 깨뜨리지 마라
