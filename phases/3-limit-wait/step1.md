# Step 1: codex-limit-signal

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/docs/ARCHITECTURE.md` — "Codex와 worktree", "상태·재접속·완료", "타임아웃과 폴링"
- `/docs/adr/` (하위 파일 전부) — 특히 `0007-usage-limit-wait-policy.md`
- `/docs/GLOSSARY.md`
- `/src/workflow/contracts/v1.py` — Step 0 이 추가한 `FailedData.retry_after`
- `/src/workflow/connector/adapter.py` — `AdapterOutput.failed: tuple[str, str, bool] | None`
- `/src/workflow/connector/codex.py` — `CodexAdapter.run`(worktree 준비 → `_launch` → 시간 초과 판정 → `is_dirty` 이면 커밋), `_launch`(stdout JSONL·stderr·last_message 수집), `_failed`
- `/src/workflow/connector/runner.py` — `_finish_failed`(failed 이벤트 발신), 실패 정보를 디스크(`failed_json`)에 보존했다가 재접속 뒤 보내는 흐름
- `/src/workflow/connector/state.py` — `failed_json` 저장·조회
- `/src/workflow/connector/git_ops.py` — `ensure_worktree`(있으면 브랜치만 확인하고 그대로 씀), `is_dirty`, `commit_all`
- `/src/workflow/connector/masking.py`
- `/scripts/execute.py` — `QUOTA_RE = re.compile(r"usage[ _]limit", re.IGNORECASE)` 와 그 주석 (Codex 한도 문구 `usage_limit_reached` / "You've hit your usage limit")
- `/tests/workflow/connector/test_codex.py` — `write_fake_codex`·`_FAKE_CODEX` 의 모드(`full`, `forbidden`, 시간 초과 등), `test_retry_reuses_same_worktree_and_branch`, `test_no_change_is_needs_information_without_commit`
- `/tests/workflow/connector/test_runner.py`

이전 step에서 만들어진 코드를 꼼꼼히 읽고, 설계 의도를 이해한 뒤 작업하라.

## 배경

지금 `CodexAdapter.run` 은 Codex 가 한도에 걸려 끝나도 이를 구분하지 않는다. worktree 가 깨끗하면 `needs_information` "변경 없음", 한도 도중 파일을 반쯤 고쳤으면 그 부분 수정을 결과로 커밋·테스트한다. ADR-0007 대로 서버가 대기·재시도하려면 연결 프로그램이 (1) 한도 도달을 `failed(code="usage_limit")` 로 보고하고, (2) 알 수 있으면 리셋 시각을 `retry_after` 에 싣고, (3) 재시도가 `base_commit` 부터 시작하도록 worktree 의 미커밋 변경을 버려야 한다.

알려진 사실 (2026-09-20 관찰):
- Codex 는 한도에 걸리면 출력에 `usage_limit` 또는 "usage limit" 문구를 남긴다 (하네스 `QUOTA_RE` 가 이걸로 감지해 왔다).
- Codex 세션 rollout 파일(`$CODEX_HOME/sessions/YYYY/MM/DD/rollout-<시각>-<thread_id>.jsonl`, 기본 `~/.codex`) 의 `token_count` 이벤트에 `"rate_limits": {"primary": {"used_percent", "window_minutes": 300, "resets_at": <unix 초>}, "secondary": {... "window_minutes": 10080 ...}, "rate_limit_reached_type": null | "..."}` 가 있다. `codex exec --json` 의 stdout JSONL 에도 같은 값이 나오는지는 **미확인** — 이 step 에서 `codex exec --json` 도움말·문서로 확인하되 실제 한도를 소모해 재현하지 마라.

## 작업

### `src/workflow/connector/adapter.py`

- `AdapterOutput` 이 재시도 가능 시각을 실을 수 있게 한다. 예: `retry_after: str | None = None` 필드 추가, 또는 `failed` 튜플에 4번째 원소. 어느 쪽이든 `EchoAdapter`·기존 `_failed(...)` 호출이 바뀌지 않거나 최소로 바뀌게 하라.

### `src/workflow/connector/codex.py`

- 한도 판정 함수를 분리한다. 시그니처 예:
  ```python
  def detect_usage_limit(stdout_jsonl: bytes, stderr: bytes) -> UsageLimit | None
  # UsageLimit(message: str, resets_at: str | None)  — resets_at 은 시간대 있는 RFC 3339
  ```
  - 판정 근거: stdout JSONL 의 `error` 류 이벤트 메시지 또는 stderr 에 `usage[ _]limit` (대소문자 무시). 하네스 `QUOTA_RE` 와 같은 정규식을 쓴다.
  - `resets_at`: 1순위 stdout JSONL 에서 `rate_limits` → `primary` → `resets_at`(unix 초) 를 찾아 시간대 있는 RFC 3339(UTC 또는 로컬, 시간대 표기 필수) 로 바꾼다. 2순위 stdout 의 `thread.started` 이벤트 `thread_id` 로 `$CODEX_HOME/sessions/**/rollout-*-{thread_id}.jsonl` 파일 **하나만** 찾아 마지막 `rate_limits.primary.resets_at` 을 읽는다 (`CODEX_HOME` 이 없으면 `~/.codex`). 둘 다 없으면 `None`.
  - 이 파일 읽기는 읽기 전용이고 정수 하나만 꺼낸다. 파일 내용·경로를 로그·산출물·메시지에 넣지 마라.
- `CodexAdapter.run`:
  - `_launch` 뒤, 시간 초과 판정 다음에 `detect_usage_limit` 를 적용한다. 한도면 `AdapterOutput(result=None, artifacts=codex_artifacts, failed=("usage_limit", <마스킹된 한 줄 메시지>, run.stopped_or_exited), retry_after=…)` 를 돌려주고 **커밋·테스트로 진행하지 않는다**. `process_stopped` 는 프로세스 종료를 실제로 확인한 값이어야 한다 (정상 종료했으면 True).
  - worktree 준비 직후, Codex 를 띄우기 **전에** 기존 worktree 의 미커밋 변경을 버린다 (추적 파일 되돌리기 + 미추적 파일 제거). 새 helper 는 `git_ops` 에 둔다. 예: `git_ops.discard_uncommitted(worktree: Path) -> None`. 이유: 한도·시간 초과 뒤 남은 부분 수정이 다음 시도의 결과에 섞이지 않게 한다(ADR-0007 "재시도는 base_commit 부터"). `ensure_worktree` 의 "있으면 브랜치 확인 후 그대로" 규칙은 유지한다.

### `src/workflow/connector/runner.py`, `state.py`

- `_finish_failed` 가 보내는 `failed` 이벤트 데이터에 `retry_after` 를 넣는다 (없으면 생략하거나 `null`).
- 실패 정보가 디스크(`failed_json`)를 거쳐 재접속 뒤에 전송되는 경로에서도 `retry_after` 가 살아남아야 한다. 형식을 바꾸면 기존 저장 행(원소 3개)도 읽히게 하라.

### 테스트 (먼저 작성)

- `tests/workflow/connector/test_codex.py`:
  - 가짜 codex 에 한도 모드를 추가한다: (a) stdout JSONL 에 `usage_limit` 문구 + `rate_limits.primary.resets_at` 포함, (b) 문구만 있고 `resets_at` 없음(stderr 에 "You've hit your usage limit"), (c) 정상 `full`.
  - (a): `failed[0] == "usage_limit"`, `retry_after` 가 RFC 3339 이고 unix 초와 일치, `result is None`, worktree 에 커밋이 생기지 않음, 산출물에 `codex_jsonl`·`codex_stderr` 는 있음.
  - (b): `failed[0] == "usage_limit"`, `retry_after is None`.
  - (a) 에서 가짜 codex 가 파일을 하나 고쳐 놓아도 커밋되지 않고, 다음 `run` 이 그 worktree 를 깨끗한 `base_commit` 상태로 되돌려 시작한다 (미추적 파일도 사라짐).
  - rollout 파일 경로 테스트는 `CODEX_HOME` 을 `tmp_path` 로 두고 가짜 rollout 파일을 만들어 한다. 운영자 홈을 읽지 마라.
  - 메시지에 비밀값 패턴이 있으면 마스킹된다 (기존 `test_secrets_in_codex_stdout_are_masked` 방식).
- `tests/workflow/connector/test_runner.py`: `usage_limit` 실패가 `failed` 이벤트로 나가고 `data.retry_after` 가 실린다. 재접속(디스크 경유) 뒤 전송돼도 같은 값이다.

## Acceptance Criteria

```bash
python3 -m pytest tests/workflow/connector -q
python3 -m pytest -q
python3 -m ruff check .
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - ARCHITECTURE.md 디렉토리 구조를 따르는가?
   - ARCHITECTURE.md 외부 의존 표에 없는 외부 서비스를 끌어들이지 않았는가?
   - ADR 기술 스택을 벗어나지 않았는가?
   - AGENTS.md CRITICAL 규칙을 위반하지 않았는가? (실행 파일·인자 배열 고정, 비밀값 미노출)
   - GLOSSARY.md 용어를 그대로 썼는가? 금지 표현을 쓰지 않았는가?
3. 결과에 따라 `phases/3-limit-wait/index.json`의 해당 step을 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"` (판정 근거·`resets_at` 출처·worktree 정리 방식·`AdapterOutput` 변경)
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- 실제 `codex` 를 실행하지 마라. 이유: 사용량을 소모하고 한도 재현은 통제할 수 없다. 모든 테스트는 가짜 codex 로 한다.
- 한도에 걸린 실행을 어댑터 안에서 기다리거나 다시 띄우지 마라. 이유: 대기·재시도는 서버 워커의 새 Execution 이다(ADR-0007). 어댑터는 보고만 한다.
- Codex 홈의 `auth.json`·`config.toml` 등 rollout 이외의 파일을 읽지 마라. 이유: 비밀값·설정을 산출물에 실을 위험. rollout 도 `resets_at` 정수 하나만 꺼낸다.
- `build_argv` 의 실행 파일·인자 배열을 바꾸지 마라. 이유: AGENTS.md CRITICAL — 외부 입력으로 명령을 구성하지 않는다.
- 계약(`contracts/v1.py`)·서버 코드를 고치지 마라. 이유: 계약은 Step 0 에서 끝났고 서버는 Step 4 다.
- 기존 테스트를 깨뜨리지 마라
