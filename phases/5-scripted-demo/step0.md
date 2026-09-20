# Step 0: scripted-agents

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/AGENTS.md` — CRITICAL 규칙. 특히 "외부 입력에서 셸 명령·경로를 받아 실행하지 않는다", "진단 fixture 는 실제 운영 데이터로 표시하지 않는다"
- `/docs/ARCHITECTURE.md` — "Codex와 worktree", "상태·재접속·완료" 절
- `/docs/adr/` (하위 파일 전부) — 특히 ADR-0001(Codex), ADR-0003(진단 모델·fake), ADR-0004(중앙 규칙 기반)
- `/docs/GLOSSARY.md` — `fake codex`, `LocalToolAdapter`, `ToolRun`, `ToolResult`, `vp-report`, `LocalStack`
- `/tests/e2e/fake_codex.py` — 지금의 e2e 전용 가짜 Codex (이 step 에서 제품 코드로 옮긴다)
- `/src/workflow/connector/local_tool.py` — `LocalToolAdapter.launch/run`, `communicate_or_stop`
- `/src/workflow/connector/codex.py` — `CodexAdapter.build_argv`(`codex exec --json -C <worktree> … --output-last-message <file> -`)와 `parse_last_message`
- `/src/workflow/connector/claude.py` — `ClaudeAdapter.build_argv`(`claude -p --output-format json --no-session-persistence --permission-mode acceptEdits --allowedTools … --json-schema <schema>`), `parse_last_message`, `_result_envelope`(stdout 에서 `type=result` 객체를 찾는다), `classify_failure`
- `/src/diagnostic_demo/worker/model.py` — `FakeModelClient`, `/src/diagnostic_demo/worker/fake_script.py` — `fixture_script`, `/src/diagnostic_demo/settings.py` — `DIAG_MODEL=fake`, `ENV_KEYS`
- `/scripts/local_stack.py` — `--fake-codex` 가 `codex` 이름의 래퍼를 PATH 앞에 두는 방식(`_install_fake_codex`)
- `/tests/workflow/connector/test_claude.py`, `/tests/workflow/connector/test_codex.py`, `/scripts/test_local_stack.py`, `/tests/diagnostic_demo/worker/test_fake_script.py`

이전 step 에서 만들어진 코드를 꼼꼼히 읽고, 설계 의도를 이해한 뒤 작업하라.

## 배경 (이 phase 전체에 해당)

2026-09-21 사용자 확정: 공개 데모(runloom.duckdns.org)는 심사위원 외에 투표하는 불특정 방문자도 쓰므로 **실제 에이전트를 돌리지 않는다** — OpenAI 진단 호출도, 운영자 Mac 의 Codex·Claude 도 마찬가지. 대신 "실제 에이전트라면 이렇게 돌아갈 것"이라는 **대본(scripted) 에이전트**가 같은 계약·같은 검증기·같은 worktree·같은 pytest 를 거쳐 결과를 낸다. 모델 호출만 없다. 화면에는 "시연용 · 대본 재생" 을 표시한다(뒤 step). 실제 Codex/Claude 어댑터(`connector/codex.py`, `connector/claude.py`)는 셀프호스트 실사용을 위해 그대로 남기며 이 step 에서 고치지 않는다.

## 작업

### `src/workflow/scripted/` (신규 패키지) — 대본 에이전트 2종

`tests/e2e/fake_codex.py` 의 구현을 제품 코드로 옮기고 Claude 대본을 추가한다. 두 스크립트는 `python3 -m workflow.scripted.codex …` / `python3 -m workflow.scripted.claude …` 로 실행되며, 배포·로컬 스택은 이들을 `codex`/`claude` 이름의 래퍼로 PATH 앞에 둔다(어댑터의 `build_argv` 는 바뀌지 않는다).

- `src/workflow/scripted/__init__.py` — 모듈 docstring 에 위 배경 4줄. `SCRIPT_MODEL_ID = "scripted-demo-agent"` 상수(대본임을 나타내는 식별자. 결과·로그에 실제 모델이 돈 것처럼 적지 않는다).
- `src/workflow/scripted/_common.py`
  - `pace_seconds() -> float` — 환경변수 `WORKFLOW_SCRIPT_PACE_SECONDS`(기본 `0`) 를 읽는다. 음수·비숫자면 0.
  - `paced_sleep(fraction: float) -> None` — `pace_seconds() * fraction` 만큼 `time.sleep`. 배포에서는 25 로 두어 B 실행이 눈에 보이게 한다(대본 실행 20~30초, 그 뒤 실제 테스트·검증 10~20초).
  - `find_handoff_response(prompt: str) -> Path | None` — 지금 `fake_codex._find_response` 그대로(프롬프트의 인계 목록에서 `response-after@1.json` 경로).
  - `apply_fix(worktree: Path, response: dict) -> list[str]` — 재현 테스트 `tests/test_repro_records.py` 추가 + `daily_report/transformer.py` 를 두 경로 지원으로 교체(지금 `fake_codex` 의 `REPRO_TEST`·`FIXED_TRANSFORMER` 그대로 옮긴다). 변경한 상대 경로 목록을 돌려준다.
- `src/workflow/scripted/codex.py` — `main(argv) -> int`. `codex exec … --output-last-message <file> -` 인자 형식을 그대로 받는다(`fake_codex.main` 을 옮긴다). 흐름: 프롬프트(stdin) → `paced_sleep(0.4)` → 응답 파일 찾기 → `paced_sleep(0.6)` → `apply_fix` → JSONL 3줄(stdout) + 마지막 메시지 파일. 응답을 못 찾으면 고치지 않고 `needs_information`. `thread_id` 는 `"scripted-codex"`.
- `src/workflow/scripted/claude.py` — `main(argv) -> int`. `claude -p --output-format json … --json-schema <schema>` 인자 형식을 받고 프롬프트는 stdin. 같은 흐름으로 `apply_fix` 를 한 뒤 stdout 에 **`type=result` 봉투 한 덩어리**를 쓴다: `{"type":"result","subtype":"success","is_error":false,"result":"<summary>","structured_output":{"summary":…,"outcome":"ready_for_review","files_changed":[…],"notes":"scripted"},"session_id":"scripted-claude","usage":{"input_tokens":0,"output_tokens":0}}`. `ClaudeAdapter.parse_last_message` 가 이 봉투를 읽어 `ToolResult(outcome="ready_for_review")` 를 내야 한다 — 실제 키 이름은 `connector/claude.py` 의 `ClaudeResult` 를 보고 맞춘다. 응답을 못 찾으면 `structured_output.outcome="needs_information"`.
- 두 스크립트 모두 `__main__` 가드로 `sys.exit(main(sys.argv))`. 네트워크·모델 호출 없음. 인자에서 받은 경로는 `--output-last-message` 파일 하나뿐이며 그 외 경로는 프롬프트에서 읽은 인계 파일(읽기 전용)이다.
- `tests/e2e/fake_codex.py` 는 5줄 shim 으로 줄인다: docstring 1줄("`workflow.scripted.codex` 의 shim — 이전 경로 호환") + `from workflow.scripted.codex import main` + `sys.exit(main(sys.argv))`. 기존 호출처(`scripts/test_local_stack.py`, e2e)가 그대로 돈다.

### `scripts/local_stack.py`

- `--fake-codex PATH` 는 유지하되, 새 옵션 `--scripted`(flag) 를 추가한다: `codex` → `python3 -m workflow.scripted.codex "$@"`, `claude` → `python3 -m workflow.scripted.claude "$@"` 두 래퍼를 한 디렉터리에 만들어 connector 의 PATH 앞에 둔다. 래퍼는 `#!/usr/bin/env bash` 2줄 셸 스크립트이며 `PYTHONPATH` 는 부모의 값을 물려준다(현재 `_install_fake_codex` 방식 그대로). `--scripted` 와 `--fake-codex` 를 같이 주면 `--scripted` 가 우선.
- `WORKFLOW_SCRIPT_PACE_SECONDS` 는 connector 자식 환경에 그대로 넘긴다(기본 0 이라 e2e 속도는 그대로).

### `src/diagnostic_demo/worker/model.py` — 진단 대본 속도

- `FakeModelClient` 에 `turn_seconds: float = 0.0` 필드를 추가하고 `_next()` 가 대본을 꺼내기 전에 `time.sleep(turn_seconds)`(0 이면 sleep 하지 않음).
- `src/diagnostic_demo/settings.py`: `ENV_KEYS` 에 `DIAG_FAKE_TURN_SECONDS` 추가, `Settings.fake_turn_seconds: float`(기본 0.0, `DIAG_MODEL=openai` 면 무시). `worker/__main__.py` 가 fake 클라이언트를 만들 때 넘긴다. 배포에서는 2.5 로 두어 진단이 약 25초 걸린다(도구 호출 9~10회).

### 테스트 (먼저 작성)

- `tests/workflow/scripted/test_codex.py`, `tests/workflow/scripted/test_claude.py`: tmp worktree(데모 저장소는 `scripts/scaffold_demo_repo.py` 로 tmp 에 생성하거나 최소 파일만 만든다)에 인계 파일을 두고 프롬프트를 stdin 으로 넣어 실행 → 변경 파일 2개·JSONL/봉투 형식·마지막 메시지 파일·exit 0. `WORKFLOW_SCRIPT_PACE_SECONDS=0` 에서 1초 안에 끝난다. 잘못된 값(`abc`, `-3`)이면 0 으로 취급. Claude 봉투를 `ClaudeAdapter.parse_last_message` 에 넣으면 `ready_for_review`.
- `tests/workflow/scripted/test_common.py`: `pace_seconds` 파싱, `find_handoff_response`.
- `tests/diagnostic_demo/worker/test_model.py`(있으면 거기에): `turn_seconds=0.01` 로 대본 2턴이 최소 0.02초 걸린다.
- `scripts/test_local_stack.py`: `--scripted` 가 래퍼 2개를 만들고 PATH 앞에 두는지(프로세스를 실제로 띄우지 않는 단위 수준).
- 기존 `tests/e2e/*`·`tests/workflow/connector/*` 는 그대로 통과해야 한다.

## Acceptance Criteria

```bash
python3 -m pytest -q                                   # 전체 테스트 통과 (e2e 포함)
python3 -m ruff check .                                # 린트 통과
python3 -m workflow.scripted.codex --help 2>&1 | head -1 || true   # 모듈로 실행 가능 (인자 없으면 비정상 종료해도 됨)
grep -n "scripted-demo-agent" src/workflow/scripted/__init__.py
wc -l tests/e2e/fake_codex.py                          # 10줄 이하 (shim)
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - ARCHITECTURE.md 디렉토리 구조를 따르는가? (`src/workflow/scripted/` 는 제품 안의 시연 전용 패키지다 — `domain/`·`server/`·`connector/` 를 import 하지 않는다)
   - 외부 의존 표에 없는 외부 서비스를 끌어들이지 않았는가?
   - ADR 기술 스택을 벗어나지 않았는가?
   - AGENTS.md CRITICAL 규칙을 위반하지 않았는가? (스크립트가 프롬프트의 경로를 "읽기"만 하고 실행하지 않는다)
   - GLOSSARY.md 용어를 그대로 썼는가? (`fake codex` 항목은 step 11 에서 `scripted agent` 로 바꾼다 — 이 step 에서는 문서를 고치지 않는다)
3. 결과에 따라 `phases/5-scripted-demo/index.json` 의 해당 step 을 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"`
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- `connector/codex.py`·`connector/claude.py` 의 `build_argv` 를 바꾸지 마라. 이유: 실제 도구 어댑터는 셀프호스트 실사용 코드이며 대본은 PATH 래퍼로만 바꿔 끼운다.
- 대본 결과에 실제 모델 ID(`gpt-4.1…`, `claude-…`)를 적지 마라. 이유: AGENTS.md — 가상 실행을 실제로 표시하지 않는다.
- 실제 `codex`·`claude`·OpenAI 를 호출하는 테스트를 쓰지 마라. 이유: 비용·한도·비결정성.
- `WORKFLOW_SCRIPT_PACE_SECONDS` 를 0 이 아닌 값으로 테스트에 넣지 마라. 이유: 테스트 시간.
- 기존 테스트를 깨뜨리지 마라.
