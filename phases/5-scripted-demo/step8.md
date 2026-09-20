# Step 8: connector-cleanup

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/AGENTS.md`, `/docs/ARCHITECTURE.md`("Codex와 worktree", "상태·재접속·완료" 절 — 결과 커밋은 `task/{task_id}` 브랜치에 보존, worktree 는 자동 삭제하지 않는다는 현재 문장), `/docs/adr/` 전부, `/docs/GLOSSARY.md`(`handoff dir`, `base_commit`/`result_commit`, `LocalToolAdapter`), `/docs/DEPLOY.md` 10절 "알려진 한계"(worktree 미삭제)
- `/src/workflow/connector/runner.py` — `_start`, `_finalize`(결과 업로드·`result_ready` 이벤트), `_finish_failed`, `_handoff_dir`, `_download_handoff`
- `/src/workflow/connector/git_ops.py` — `worktree_path`, `ensure_worktree`, `commit_all`, `export_checkout`, `remove_worktree`
- `/src/workflow/connector/local_tool.py` — 결과 커밋을 브랜치 `task/{task_id}` 로 남기는 부분, `export_checkout` 정리
- `/src/workflow/connector/state.py` — 실행 단계(phase) 기록
- `/tests/workflow/connector/test_runner.py`, `test_git_ops.py`, `test_local_tool.py`, `/tests/e2e/test_scenario.py`(데모 저장소 `main` 불변·`task/` 브랜치 확인)

이전 step 에서 만들어진 코드를 꼼꼼히 읽고, 설계 의도를 이해한 뒤 작업하라.

## 작업

공개 데모는 불특정 방문자가 워크플로우를 계속 만들므로 실행마다 남는 worktree·인계 디렉터리가 디스크를 채운다. 결과는 이미 `task/{task_id}` 브랜치의 커밋으로 보존되므로(GLOSSARY `result_commit`) **결과 업로드가 끝난 뒤** 작업 디렉터리를 지운다. 브랜치·커밋은 지우지 않는다.

### `src/workflow/connector/runner.py`

- `_finalize` 가 `result_ready` 이벤트를 **보낸 뒤**(서버가 받았음이 확인된 뒤 — `_emit`/`_flush` 의 확인 경로를 읽고 그 시점에) `_cleanup_workdirs(row)` 를 부른다. 실패 종료(`_finish_failed`) 뒤에도 부른다 — 단 `process_stopped=False`(프로세스 종료 미확인) 로 실패한 경우는 지우지 않는다(살아 있는 프로세스의 cwd 를 지우지 않는다).
- `_cleanup_workdirs(row)`: 등록의 `repo` 와 `task_id` 로 `git_ops.worktree_path` 를 구해 존재하면 `remove_worktree`, `_handoff_dir(request)` 가 존재하면 `shutil.rmtree`. 실패는 경고 로그만 남기고 실행 결과에 영향을 주지 않는다. 정리 여부를 `state` 에 `cleaned_at` 으로 기록한다(재시작 뒤 중복 정리 방지 — 이미 없는 디렉터리는 조용히 통과).
- 설정: `Runner(..., keep_workdirs: bool = False)`. `cli.py run --keep-workdirs` 플래그(디버깅용)를 추가한다. 기본은 정리.

### `src/workflow/connector/git_ops.py`

- `remove_worktree` 는 그대로. 추가로 `prune_worktrees(repo)`(`git worktree prune`) 를 두고 `_cleanup_workdirs` 끝에 부른다.

### 문서 (이 step 범위)

- `docs/ARCHITECTURE.md` "상태·재접속·완료" 절의 worktree 문장을 "결과 업로드 뒤 worktree·인계 디렉터리는 지우고 `task/{task_id}` 브랜치만 남긴다(`--keep-workdirs` 로 보존)" 로 고친다. `docs/DEPLOY.md` 10절의 "worktree 는 자동 삭제하지 않는다" 문장을 같은 내용으로 고친다.

### 테스트 (먼저 작성)

- `tests/workflow/connector/test_runner.py`: 정상 종료 → worktree·handoff dir 삭제, `task/{task_id}` 브랜치와 커밋은 남음, `cleaned_at` 기록; `keep_workdirs=True` 면 남음; `process_stopped=False` 실패면 남음; 삭제 실패(권한)를 흉내 내도 `result_ready` 는 이미 전송됨.
- `tests/workflow/connector/test_git_ops.py`: `prune_worktrees`.
- `tests/e2e/test_scenario.py`: B 완료 뒤 worktree 디렉터리가 없고 브랜치는 있다(기존 "main 불변" 검사 유지).

## Acceptance Criteria

```bash
python3 -m pytest tests/workflow/connector -q
python3 -m pytest -q
python3 -m ruff check .
grep -n "_cleanup_workdirs\|keep_workdirs\|cleaned_at" src/workflow/connector/runner.py | head
grep -n "keep-workdirs" src/workflow/connector/cli.py
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - ARCHITECTURE.md "Codex와 worktree" 의 보존 규칙(결과 커밋·브랜치)을 지키는가?
   - ADR 기술 스택을 벗어나지 않았는가? (Git 은 `connector/git_ops.py` 경계에서만)
   - AGENTS.md CRITICAL: 외부 입력에서 경로를 받아 지우지 않는다 — 지우는 경로는 등록의 `repo` 와 `task_id` 로 어댑터가 계산한 것뿐.
   - GLOSSARY.md 용어.
3. 결과에 따라 `phases/5-scripted-demo/index.json` 의 해당 step 을 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"`
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- 브랜치 `task/{task_id}` 나 결과 커밋을 지우지 마라. 이유: 결과 보존 규칙(ARCHITECTURE) — 병합은 운영자 확인 뒤 사람이 한다.
- 요청 본문·프롬프트에서 받은 경로를 지우지 마라. 이유: AGENTS.md CRITICAL.
- 업로드 전에 지우지 마라. 이유: 산출물(diff·로그·보고서)이 worktree 밖 산출 디렉터리에 있더라도 순서를 뒤집으면 유실 위험.
- 기존 테스트를 깨뜨리지 마라.
