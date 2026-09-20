# Step 3: runner-dispatch

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/AGENTS.md`
- `/docs/ARCHITECTURE.md` — "연결 프로그램" (등록 단위: 실행 도구 + 작업 폴더 하나)
- `/docs/GLOSSARY.md` — 등록(registration)·Agent·connector 구분
- `/src/workflow/connector/cli.py` — `register --tool` (choices 가 `["codex"]`), `run --adapter`, `run-local --adapter`, `ADAPTERS`
- `/src/workflow/connector/runner.py` — `Runner(adapter=…)` 가 어댑터 하나를 받아 모든 claim 에 쓴다
- `/src/workflow/connector/state.py` — `registrations.tool` 컬럼, `get_registration`
- `/src/workflow/connector/discovery.py` — `discover(repo_path)` 가 `.codex` 존재 등을 요약
- `/src/workflow/contracts/v1.py` — `CodeChangeTarget.local_registration_id`
- `/tests/workflow/connector/test_cli.py`, `test_runner.py`, `test_discovery.py`

이전 step에서 만들어진 코드를 꼼꼼히 읽고, 설계 의도를 이해한 뒤 작업하라. 새 기능은 테스트를 먼저 작성한다 (AGENTS.md TDD, `tests/` 는 `src/` 미러 배치).

## 배경

한 연결 프로그램이 같은 Mac 의 Codex 등록과 Claude 등록을 함께 맡는다. 지금 `Runner` 는 어댑터 하나를 받으므로, 실행 요청의 `target.local_registration_id` 로 등록을 찾아 그 `tool` 에 맞는 어댑터를 고르게 바꾼다.

## 작업

### `src/workflow/connector/runner.py`

- `Runner.__init__(…, adapters: Mapping[str, ExecutionAdapter], …)` — 도구 이름 → 어댑터. 기존 `adapter=` 단일 인자는 제거한다 (호출자는 cli.py 와 테스트뿐).
- claim 한 요청을 실행하기 전에: `target` 이 `CodeChangeTarget` 이면 `state.get_registration(conn, target.local_registration_id)` 의 `tool` 로 어댑터를 고른다. 등록이 없거나 그 도구의 어댑터가 없으면 `failed("registration_missing" | "adapter_missing", …, process_stopped=True)` 이벤트로 끝낸다 (기존 `adapter_error` 경로와 같은 방식).
- 진단 종류 등 로컬 등록이 없는 요청은 지금처럼 처리한다(연결 프로그램은 코드 수정만 받는다 — 바뀌지 않는다).

### `src/workflow/connector/cli.py`

- `register --tool` choices 를 `["codex", "claude"]` 로.
- `run --adapter` 의 기본값을 `"auto"` 로: `auto` 면 `ADAPTERS` 의 `codex`·`claude` 둘을 만들어 `Runner(adapters={…})` 에 넘긴다. 특정 이름을 주면 그 하나만 (e2e 의 `echo` 유지). `run-local --adapter` 도 같은 규칙.
- 시작 로그 `adapter=%s` 는 실제로 만든 어댑터 이름 목록을 찍는다.

### `src/workflow/connector/discovery.py`

- `discover` 결과에 `claude_config: bool` (`repo_path/.claude` 디렉터리 또는 `CLAUDE.md` 존재) 을 추가한다. 읽기 전용·존재 여부만. 인증 파일(`~/.claude/…`)은 읽지 않는다.

### 테스트 (먼저 작성)

- `test_runner.py`: 등록 두 개(codex·claude) 상태에서 `local_registration_id` 에 따라 다른 가짜 어댑터가 호출된다, 등록 없음 → `registration_missing`, 어댑터 없음 → `adapter_missing`.
- `test_cli.py`: `register --tool claude` 가 상태 DB 에 `tool=claude` 로 저장되고 서버에 `tool: "claude"` 로 보고한다, `run` 기본이 두 어댑터를 만든다(로그 문자열), `--adapter echo` 면 하나.
- `test_discovery.py`: `.claude/` 있으면 `claude_config` True.

## Acceptance Criteria

```bash
python3 -m pytest tests/workflow/connector -q
python3 -m pytest -q
python3 -m ruff check .
python3 -m workflow.connector register --help | grep -E "codex.*claude|claude.*codex"
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

- 서버(`src/workflow/server/`)를 고치지 마라. 이유: server 와 connector 는 서로 import 하지 않으며 등록 계약은 step 0 에서 이미 열렸다.
- 등록의 `tool` 을 실행 요청 본문에서 받지 마라. 이유: 어댑터 선택은 연결 프로그램의 로컬 상태(등록)로만 정한다 — 외부 입력으로 실행 대상을 고르지 않는다.
- 기존 테스트를 깨뜨리지 마라 (Runner 시그니처를 바꾸므로 기존 테스트의 호출부는 새 시그니처로 옮긴다 — 검증 내용은 유지)
