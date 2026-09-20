# Step 0: tool-contract

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/AGENTS.md` — CRITICAL 규칙, 명령어
- `/docs/ARCHITECTURE.md` — "계약 v1의 공통 규칙", "산출물", "연결 프로그램"
- `/docs/CONTRACT.md` — 4절 산출물 kind 표, 등록 요청 예시
- `/docs/GLOSSARY.md`
- `/docs/adr/0001-first-local-agent-codex.md` — "Claude Code 는 후속 어댑터" (이 phase 가 그 후속이다)
- `/src/workflow/contracts/v1.py` — `ARTIFACT_KINDS`, `ArtifactMeta`
- `/src/workflow/server/machine_api.py` — `RegistrationRequest.tool: Literal["codex"]`
- `/src/workflow/server/filters.py` — `KIND_LABELS`
- `/src/workflow/server/views.py` — `LOG_KINDS`
- `/tests/workflow/contracts/test_v1.py`, `/tests/workflow/server/test_machine_api.py`, `/tests/workflow/server/test_filters.py`

이전 step에서 만들어진 코드를 꼼꼼히 읽고, 설계 의도를 이해한 뒤 작업하라. 새 기능은 테스트를 먼저 작성한다 (AGENTS.md TDD, `tests/` 는 `src/` 미러 배치).

## 배경

phase 4 는 두 번째 로컬 도구 어댑터(Claude Code CLI)를 추가한다. 서버와 연결 프로그램은 `src/workflow/contracts/` 만 공유하므로, 어댑터 코드를 만들기 전에 계약과 기계 API 가 `claude` 도구와 그 원시 로그 산출물을 받아들이게 한다. 이 step 은 계약·API·화면 라벨만 바꾸고 어댑터는 만들지 않는다.

## 작업

### `src/workflow/contracts/v1.py`

- `ARTIFACT_KINDS` 에 `"claude_jsonl"`, `"claude_stderr"` 두 종류를 추가한다 (기존 13종 뒤에). 주석의 "13종" 을 "15종" 으로 고친다.
- 다른 모델·검증기는 바꾸지 않는다. `contract_version` 은 1 그대로 — 종류 추가는 하위 호환이다.

### `src/workflow/server/machine_api.py`

- `RegistrationRequest.tool: Literal["codex", "claude"]`.
- 등록 처리 코드에서 `tool` 값을 그대로 저장하는지 확인한다 (`repo.update_registration` 경로). 값에 따라 분기하는 곳이 있으면 `claude` 도 같은 경로를 타게 한다.

### `src/workflow/server/filters.py`·`views.py`

- `KIND_LABELS` 에 `"claude_jsonl": "Claude JSONL"`, `"claude_stderr": "Claude stderr"`.
- `views.LOG_KINDS` 에 두 종류를 더한다 (뷰어가 로그로 다루는 종류 목록).

### `docs/CONTRACT.md`

- 4절 산출물 kind 표에 두 행을 추가한다 (생산자: 연결 프로그램 Claude 어댑터, 내용: `claude -p --output-format json` 의 원문 stdout / stderr, 마스킹 적용).
- 등록 요청 예시 옆에 `"tool": "claude"` 예시 한 건을 추가한다 (`local_registration_id: "local-demo-report-claude"`).

### 테스트 (먼저 작성)

- `tests/workflow/contracts/test_v1.py`: `ARTIFACT_KINDS` 가 15종이고 두 이름을 포함, `ArtifactMeta(kind="claude_jsonl", …)` 통과, CONTRACT.md 4절 표의 kind 목록과 상수가 일치(기존 표-상수 대조 테스트가 있으면 그것이 자동으로 검사한다 — 확인).
- `tests/workflow/server/test_machine_api.py`: `tool: "claude"` 등록 요청이 200 이고 DB 에 `claude` 로 저장, `tool: "gemini"` 는 422.
- `tests/workflow/server/test_filters.py`: 두 라벨.

## Acceptance Criteria

```bash
python3 -m pytest tests/workflow/contracts tests/workflow/server -q
python3 -m pytest -q
python3 -m ruff check .
grep -c "claude_jsonl" docs/CONTRACT.md          # 1 이상
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

- `contract_version` 을 올리지 마라. 이유: 종류·리터럴 추가는 하위 호환이며, 올리면 배포된 연결 프로그램이 422 를 받는다.
- `codex_jsonl`·`codex_stderr` 를 `agent_jsonl` 같은 이름으로 통합하지 마라. 이유: 배포된 DB 의 산출물 행이 그 이름으로 저장돼 있다.
- 연결 프로그램(`src/workflow/connector/`)을 고치지 마라. 이유: step 1~3 이다.
- 기존 테스트를 깨뜨리지 마라
