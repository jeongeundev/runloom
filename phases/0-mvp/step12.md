# Step 12: connector-codex

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/docs/ARCHITECTURE.md` — "Codex와 worktree" 절 전체, "코드 수정 결과"(필수 산출물, `verification` 은 연결 프로그램이 별도 실행), "인증·권한·비밀정보 규칙"(Codex 환경 허용 목록), "타임아웃과 폴링"(Codex 20분, 검증 5분)
- `/docs/adr/0001-first-local-agent-codex.md`
- `/docs/CONTRACT.md` — 2절(요청 target·handoff manifest), 7절(결과 정상·보류 예), 4절 kind 목록
- `/docs/PRD.md` — "기대 수정 결과와 보고서"(B 제출물), "Git 작업의 실행 환경"
- `/src/workflow/connector/` (Step 11) — `adapter.py` 의 `ExecutionAdapter`·`AdapterOutput`, `masking.codex_env`, `state.py`, `runner.py`, `cli.py`
- `/phases/0-mvp/step13.md` — 데모 저장소의 구조(`daily_report` 패키지, `python3 -m daily_report <response.json>`). Step 13 이 아직 실행되지 않았으므로 이 step 의 테스트는 임시 git 저장소를 직접 만든다

## 작업

실제 Codex 를 띄우는 어댑터와 Git worktree 관리, 검증 프로필 실행, 결과 보존을 만든다. 이 step 의 테스트는 **PATH 앞에 둔 가짜 `codex` 스크립트**로 돈다. 실제 Codex 실행은 Step 15.

### `src/workflow/connector/git_ops.py`

```python
def head_sha(repo: Path) -> str
def ensure_worktree(repo: Path, task_id: str, base_commit: str) -> Path
    # 경로 <repo>-worktrees/<task_id>/ (저장소 옆). 없으면 `git worktree add -b task/<task_id> <path> <base_commit>`. 있으면 브랜치가 task/<task_id> 인지 확인하고 그대로 사용(재시도는 같은 worktree). base_commit 이 저장소에 없으면 GitError
def is_dirty(worktree: Path) -> bool
def commit_all(worktree: Path, message: str, author: str = "workflow-connector <connector@localhost>") -> str      # 변경 없으면 GitError("nothing to commit")
def diff_text(repo: Path, base: str, head: str) -> str
def changed_test_files(repo: Path, base: str, head: str) -> list[str]     # diff 에서 추가·수정된 tests/ 아래 파일과 test_*.py
def export_checkout(repo: Path, commit: str, dest: Path) -> None          # `git worktree add --detach dest commit` 후 호출자가 정리. 검증용 깨끗한 체크아웃
def remove_worktree(repo: Path, path: Path) -> None
```

원본 저장소의 작업 트리와 기준 브랜치는 건드리지 않는다. 모든 명령은 `subprocess.run(["git", ...], cwd=..., check=True, capture_output=True)` 고정 인자 배열.

### `src/workflow/connector/codex.py`

```python
@dataclass(frozen=True)
class CodexRun: exit_code: int; jsonl: bytes; stderr: bytes; last_message: str | None; started_at: str; ended_at: str; pid: int; stopped: bool
class CodexAdapter(ExecutionAdapter):
    def __init__(self, codex_bin: str = "codex", timeout_seconds: int = 1200, sandbox: str = "workspace-write",
                 approval_policy: str = "never", env_base: Mapping[str, str] = os.environ, verification_timeout: int = 300)
    def build_argv(self, worktree: Path, schema_path: Path, last_message_path: Path) -> list[str]
        # [codex_bin, "exec", "--json", "-C", str(worktree), "--sandbox", sandbox, "-c", f"approval_policy=\"{approval_policy}\"", "--output-schema", str(schema_path), "--output-last-message", str(last_message_path), "-"]
        # 프롬프트는 stdin. 인자에 사용자·근거 문자열을 넣지 않는다
    def run(self, request: ExecutionRequest, handoff_dir: Path, progress) -> AdapterOutput
```

`run()` 순서 (모두 이 어댑터가 수행. Codex 의 말을 믿지 않는다):

1. `registration = state.get_registration(request.target.local_registration_id)` — 없으면 `failed("registration_missing")`. `repo = registration.repo_path`.
2. `worktree = ensure_worktree(repo, request.task_id, request.target.base_commit)`. 실패 → `failed("base_commit_missing", …)`.
3. 프롬프트 작성 (`prompt.py` 의 `build_prompt(request, handoff_dir, worktree) -> str`): 업무 `request` 원문, 인계 디렉터리의 파일 목록과 경로(진단 결과 JSON 경로, 응답 두 개, 로그, 문서, 기대 보고서), 규칙 — 이 worktree 안에서만 작업, 재현 테스트를 먼저 작성해 실패 확인 후 최소 수정, `python3 -m pytest -q` 로 확인, 커밋하지 말 것(연결 프로그램이 커밋), 마지막 메시지는 스키마(`codex_result_schema.json`: `{summary, outcome: ready_for_review|needs_information, files_changed: [..], notes}`)대로. 인계 자료의 `target_component` 는 단서일 뿐 실제 코드에서 확인하라고 적는다.
4. `subprocess.Popen(argv, cwd=worktree, stdin=PIPE, stdout=PIPE, stderr=PIPE, env=codex_env(env_base))` → 프롬프트 write → `communicate(timeout=timeout_seconds)`. 타임아웃이면 `terminate()` → 5초 후 `kill()` → `stopped` 확인. `progress("Codex 실행 시작 pid=…")`, 종료 시 `progress("Codex 종료 exit=…")`.
5. `runtime_ref = f"pid:{pid};start:{started_at}"` 를 `AdapterOutput.runtime_ref` 에 (Step 11 runner 가 `started` 이벤트에 쓴다 — runner 는 어댑터 시작 직후 이 값을 받아야 하므로 `run()` 은 `progress` 로 먼저 알리고 반환값에도 넣는다. runner 의 `started` 이벤트 시점을 "어댑터가 `runtime_ref` 를 progress 콜백 첫 호출로 넘긴 뒤" 로 조정한다: 콜백 시그니처를 `progress(message: str, *, runtime_ref: str | None = None)` 로 확장).
6. 산출물 수집: `codex_jsonl`(stdout), `codex_stderr`. 둘 다 `mask_secrets` 후 저장. `last_message` 를 스키마로 파싱(실패해도 계속, `notes` 에 기록).
7. `is_dirty(worktree)` 아니면 → `CodeChangeResult(outcome="needs_information", summary="변경 없음: " + last_message.summary, result_commit=None, verification=None)`.
8. 재현 실패 기록 `test_log_before`: `changed_test_files(repo, base_commit, worktree HEAD+작업트리)` 를 구해 `export_checkout(repo, base_commit, tmp)` 에 그 테스트 파일들만 복사하고 검증 프로필(`registration.verification_profiles[request.target.verification_profile_id]` 의 인자 배열)을 실행. 출력 첫 줄에 `exit_code={n}` 을 붙여 저장. 새 테스트가 없으면 `test_log_before` 는 `exit_code=0\n(재현 테스트 없음)` 으로 저장하고 결과 `needs_information` (중앙이 "exit 0 이 아님" 을 요구하므로 채택되지 않는다 — 정직하게 남긴다).
9. `test_log_after`: worktree 에서 검증 프로필 실행, `exit_code=` 첫 줄.
10. `result_commit = commit_all(worktree, f"fix({task_id}): {summary[:60]}")`.
11. `verification_log`: `export_checkout(repo, result_commit, tmp2)` 에서 검증 프로필 실행 → `Verification(profile_id, result_commit, exit_code, log_artifact_id)`. 검사한 코드와 커밋이 같음을 보장하는 절차다.
12. `report_output`: 같은 깨끗한 체크아웃에서 `[sys.executable, "-m", "daily_report", str(handoff_dir / "response-after@1.json")]` 실행 stdout (실패하면 stderr 와 `exit_code=`). 이 명령은 등록 정보의 `report_command` 프로필(`vp-report` 로 `register --verify vp-report="python3 -m daily_report {response}"`) 로 두어 하드코딩하지 않는다. `{response}` 자리만 어댑터가 채운다.
13. `diff` = `diff_text(repo, base_commit, result_commit)`.
14. `CodeChangeResult(outcome="ready_for_review", summary, base_commit, result_commit, artifact_ids=[…], verification)`. 산출물 목록: `diff`, `test_log_before`, `test_log_after`, `verification_log`, `report_output`, `codex_jsonl`, `codex_stderr`, `code_change_result`(runner 가 마지막에 업로드). `artifact_ids` 는 업로드 후 채워지므로 `AdapterOutput` 에는 kind 순서로 두고 runner 가 ID 를 채운다 (Step 11 runner 를 그에 맞게 수정).
15. 임시 체크아웃 정리. worktree 는 남긴다.

`register --verify` 는 여러 개를 받는다: `vp-pytest="python3 -m pytest -q"`, `vp-report="python3 -m daily_report {response}"`. Step 11 의 `cli.register` 에 반영한다.

### `src/workflow/connector/cli.py` 변경

`run` 의 기본 어댑터를 `CodexAdapter` 로. `--adapter echo` 는 유지. 새 하위 명령 `run-local --request request.json --handoff-dir DIR --out DIR`: 중앙 없이 어댑터 한 번 실행하고 산출물을 `--out` 에 파일로 저장 (Step 15 실연동 확인용).

### 테스트 — `tests/workflow/connector/test_git_ops.py`, `test_codex.py`, `test_prompt.py`

가짜 `codex`: 테스트가 `tmp_path/bin/codex` 에 실행 가능한 Python 스크립트를 쓰고 `PATH` 앞에 둔다. 스크립트는 stdin 프롬프트를 읽어 `-C` 디렉터리에서 (a) `tests/test_repro.py` 를 추가하고 (b) `daily_report/transformer.py` 를 두 경로 지원으로 고친 뒤 JSONL 몇 줄을 stdout 에, `--output-last-message` 파일에 스키마 JSON 을 쓴다. 변형: (b) 만 하는 버전(테스트 없음), 아무것도 안 하는 버전, 60초 sleep 버전(타임아웃), `wfc_abc…` 를 stdout 에 찍는 버전.

데모 저장소는 테스트 fixture 함수 `make_repo(tmp_path)` 가 만든다: `daily_report/transformer.py`(`items` 만 읽음), `daily_report/report.py`, `daily_report/__main__.py`, `tests/test_transformer.py`, git init + commit. Step 13 이 만들 것과 같은 최소 구조.

- `build_argv` 가 고정 인자만 갖고 프롬프트·근거 문자열이 argv 에 없다.
- 정상 가짜 codex → `ready_for_review`, 산출물 7종, `test_log_before` 첫 줄 `exit_code=1`, `test_log_after`·`verification_log` `exit_code=0`, `report_output` 에 `합계`, `result_commit` 이 `task/<task_id>` 브랜치의 HEAD, 원본 저장소 HEAD 와 작업 트리 불변, `diff` 에 `transformer.py`.
- 테스트 없이 고친 버전 → `needs_information`.
- 변경 없음 → `needs_information`, 커밋 없음.
- 타임아웃 → `failed("timeout", process_stopped=True)`.
- 환경: 가짜 codex 가 `env` 를 덤프하도록 해 `OPENAI_API_KEY`·`WORKFLOW_*` 가 없고 `PATH` 가 있음을 확인.
- 마스킹: stdout 의 `wfc_…` 가 `codex_jsonl` 산출물에서 `wfc_***`.
- 재시도: 같은 task_id 로 두 번째 `ensure_worktree` 가 같은 경로·같은 브랜치.
- `run-local` CLI 가 `--out` 에 파일을 만든다.

### GLOSSARY

`CodexRun`(Codex 프로세스 한 번의 원시 결과), `vp-report`(보고서 생성 검증 프로필. `{response}` 자리만 치환) 를 추가한다.

## Acceptance Criteria

```bash
python3 -m pytest tests/workflow/connector -q
python3 -m pytest -q
python3 -m ruff check .
```

## 검증 절차

1. 위 AC 커맨드를 실행한다. 실제 `codex` 가 호출되지 않았는지 확인한다 (PATH 조작 fixture 가 모든 테스트에 적용).
2. 아키텍처 체크리스트:
   - 셸 명령이 요청·근거·모델 출력에서 오지 않는가? 검증 프로필은 로컬 등록값에서만.
   - 종료 코드 0 을 테스트 통과로 쓰지 않는가? (`verification` 은 별도 실행)
   - Codex 환경에 토큰·키가 없는가?
   - 원본 저장소·기준 브랜치가 변경되지 않는가?
3. `phases/0-mvp/index.json` 의 step 12 를 업데이트한다 (summary 에 `run()` 의 산출물 순서, `run-local` 사용법, `register --verify` 형식).

## 금지사항

- `--dangerously-bypass-approvals-and-sandbox` 를 쓰지 마라. 이유: 자동 실행을 샌드박스 해제로 해석하지 않는다. `workspace-write` + `approval_policy=never`.
- `--worktree` (Codex 내장) 를 쓰지 마라. 이유: ARCHITECTURE — 결과 보존을 연결 프로그램이 통일 관리한다.
- Codex 가 커밋·푸시하게 하지 마라. 이유: 결과 커밋은 어댑터가 만든다. 푸시·병합은 범위 밖.
- Claude Code 어댑터를 미리 만들지 마라. 이유: ADR-0001 후속.
- 기존 테스트를 깨뜨리지 마라.
