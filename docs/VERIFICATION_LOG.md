# 실연동 검증 기록

상태: 실제 외부 도구를 호출한 검증의 원본 기록. 결과가 좋게 보이도록 편집하지 않는다. 항목마다 근거 파일 경로를 적는다.

## 2026-09-20 — Codex CLI 단독 실행 (Step 15, ADR-0001 미검증 항목)

목적: 연결 프로그램의 Codex 어댑터(`src/workflow/connector/codex.py`)를 **실제 `codex exec`** 로 한 번 돌려 인증 재사용·모델 실행·worktree·승인 정책을 확인한다. 실행은 1회다.

| 항목 | 값 |
|---|---|
| 실행일 | 2026-09-20 11:34:06 ~ 11:36:42 KST (전체 2분 36초, Codex 프로세스 약 2분 35초, 검증 프로필·체크아웃 약 1초) |
| Codex | `codex-cli 0.155.1`, `codex login status` → "Logged in using ChatGPT" (실행 전 확인) |
| 명령 | `python3 -m workflow.connector run-local --request request.json --handoff-dir …/demo-report-repo-worktrees/fix-daily-0920.handoff --out …/step15/out` |
| Codex argv (어댑터 고정) | `codex exec --json -C <worktree> --sandbox workspace-write -c approval_policy="never" --output-schema <tmp>/codex_result_schema.json --output-last-message <tmp>/last_message.json -` (프롬프트는 stdin) |
| Codex exit code | 0 |
| 데모 저장소 | `../demo-report-repo` (`scripts/scaffold_demo_repo.py`), `base_commit=0c1ddcf6ecd35d20c49dc9b0868f3cabf1f2afa0` (태그 `report-base`) |
| 인계 디렉터리 | `scripts/make_handoff_dir.py` 로 생성 (fixture 근거 8 + `expected-report@1.json` + `manifest.json` + `diagnosis_result.json`) |
| 요청 | CONTRACT 2절 `ExecutionRequest` 에서 `target.base_commit` 만 실제 값으로 교체 |
| 로컬 등록 | `run-local` 은 `state.sqlite` 의 등록을 읽는다. `register` 는 연결 토큰이 필요해 `state.save_registration` 으로 직접 넣었다 (`vp-pytest=python3 -m pytest -q`, `vp-report=python3 -m daily_report {response}`) |
| 환경 | 연결 프로그램에 `WORKFLOW_CONNECTOR_HOME` 만 줌. Codex 프로세스 환경은 `masking.codex_env` 허용 목록(`HOME PATH LANG LC_ALL TERM TMPDIR USER SHELL CODEX_HOME XDG_*`)뿐이다 |
| 토큰 사용 (JSONL `turn.completed`) | input 517,213 (cached 453,120), output 5,980 (reasoning 2,305) |
| 산출물 | `…/demo-report-repo-worktrees/step15/out/` — `code_change_result.json`, `diff.diff`, `test_log_before.txt`, `test_log_after.txt`, `verification_log.txt`, `report_output.txt`, `codex_jsonl.jsonl`(47줄), `codex_stderr.txt` |

### 결과 확인

| 확인 | 결과 | 근거 |
|---|---|---|
| `code_change_result.json` | `outcome=ready_for_review`, `result_commit=93a84ea99208920888b8cfae7089d1d8bad23f03`, `verification.exit_code=0` | out/code_change_result.json |
| 수정 전 재현 실패 | `test_log_before.txt` 첫 줄 `exit_code=1` — 결과 커밋의 `tests/test_transformer.py` 를 기준 커밋 위에 놓고 실행, 3 failed / 14 passed (`test_data_records_format_generates_expected_report`, `test_empty_record_list_is_zero_rows[response1]`, `test_both_record_paths_are_rejected`) | out/test_log_before.txt |
| 수정 후 통과 | `test_log_after.txt`·`verification_log.txt` 첫 줄 `exit_code=0`, 17 passed | out/test_log_after.txt, out/verification_log.txt |
| 보고서 | `report_output.txt` 마지막 줄 `합계    20    5`, 날짜 `2026-09-19`, 행 운영 12·3 / 개발 8·2 | out/report_output.txt |
| 변경 범위 | `daily_report/transformer.py` (+9 −3), `tests/test_transformer.py` (+37 −2). 다른 파일 없음 | out/diff.diff |
| 저장소 | `main` == `report-base` == `0c1ddcf6…` (불변). 브랜치 `task/fix-daily-0920` 이 생겼고 worktree `../demo-report-repo-worktrees/fix-daily-0920/` 는 커밋 뒤 깨끗함. 커밋 author `workflow-connector` | `git -C ../demo-report-repo branch -a`, `git worktree list` |
| 비밀값 | 산출물 7개에서 `wfc_`·`sk-` 0건 | `grep -c` |
| 세션 | `~/.codex/sessions` 파일 563 → 564 (Codex 가 세션을 저장함, `--ephemeral` 미사용) | `find ~/.codex/sessions` |

### ARCHITECTURE "검증 순서" 1~3행

| 순서 | 검증 | 결과 | 근거 |
|---|---|---|---|
| 1 | Codex 단독 연결 — 지정 폴더 새 실행에서 JSONL·최종 결과·실패 구분 | **통과**. `-C <worktree>` 새 실행, JSONL 47줄 수집, `--output-last-message` 파일을 어댑터가 파싱, exit 0 | out/codex_jsonl.jsonl, out/code_change_result.json |
| 2 | 설정과 worktree — 지침·테스트·기존 도구 사용 증거, 원래 폴더·기준 브랜치 미변경 | **통과**. 아래 JSONL 발췌: worktree 의 `AGENTS.md` 와 사용자 홈 `~/.agents/skills/tdd/SKILL.md` 를 읽고 `python3 -m pytest -q` 를 썼다. `main` 불변, 원래 폴더 작업 트리 깨끗함 | 아래 발췌, `git status` |
| 3 | 중복·재접속 — 같은 ID 두 번 전달 시 한 번 실행, 업로드 복구, 시작 불명 시 보류 | **이 실행으로는 확인 불가**. `run-local` 은 중앙 없이 어댑터 1회다. 이 항목은 Step 11 `tests/workflow/connector/test_runner.py`(claim 후 크래시 재개, gap 재전송, 업로드 중 끊김)와 Step 14 e2e 가 **가짜 codex** 로만 확인했다 | — |

### Codex 가 실제로 사용한 기존 설정 — JSONL 발췌 (command 필드, 비밀값 없음)

```text
line 19 item_9 exit=0: sed -n '1,280p' daily_report/transformer.py; sed -n '1,220p' daily_report/__main__.py; sed -n '1,220p' pyproject.toml; sed -n '1,260p' AGENTS.md
line 17 item_8 exit=0: for f in …/demo-report-repo-worktrees/fix-daily-0920.handoff/*.json; do jq -c '{file: input_filename, data: .}' "$f"; done
line 24 item_12 exit=1: python3 -m pytest -q        (→ line 43 item_23 exit=0: python3 -m pytest -q)
```

- item_9: worktree 에 체크아웃된 데모 저장소의 `AGENTS.md`·`pyproject.toml` 을 읽었다. Codex 의 AGENTS.md 자동 주입 여부는 JSONL 에 나타나지 않아 "명시적 읽기" 만 확인한 것이다.
- item_2·item_4(발췌 밖): `~/.agents/skills/tdd/SKILL.md`, `~/.agents/skills/codebase-design/SKILL.md` — 운영자 홈의 기존 스킬을 읽고 순서(RED → GREEN)를 따랐다. 사용자 설정 재사용의 증거이지 연결 프로그램이 복사한 것이 아니다.
- item_8: 인계 디렉터리는 worktree **밖**(`<repo>-worktrees/<task_id>.handoff/`)인데 `workspace-write` 에서 읽혔다 (Step 12 미검증 항목 해소).
- item_12 → item_23: 재현 테스트 추가 후 실패(exit 1), 수정 후 통과(exit 0). 어댑터는 이 주장을 쓰지 않고 별도 체크아웃에서 다시 실행했다 (위 표).

### 승인 정책·샌드박스

- `-c approval_policy="never"` + `--sandbox workspace-write`: 명령 13개·파일 변경 5개가 승인 요청 없이 끝났다 (JSONL 에 approval 관련 이벤트 0건, 모든 `command_execution` 이 `completed`).
- worktree 안 `git status`·`git diff` 는 exit 0. worktree 의 Git 메타데이터는 원래 저장소 `.git/worktrees/` 에 있으므로 쓰기 차단 여부는 이 실행에서 관찰하지 않았다.
- `--output-schema` 는 마지막 메시지뿐 아니라 **중간 `agent_message` 7개 전부**를 스키마 JSON 으로 만들었다 (중간 메시지의 `outcome` 은 의미 없음). 어댑터는 `--output-last-message` 파일만 읽으므로 영향 없다.
- 운영자 홈 `~/.codex/config.toml` 의 MCP 서버·플러그인이 그대로 로드됐다. `codex_stderr.txt` 에 `notion` MCP 서버의 OAuth 갱신 실패 1줄(`invalid_grant`, 비밀값 없음), JSONL 첫 항목에 "Skill descriptions were shortened to fit the skills context budget" 경고가 남았다. 실행에는 영향이 없었으나 입력 토큰 517k 의 상당 부분이 이 설정에서 온다. 심사 기간 B 실행의 사용량은 이 값을 기준으로 본다.

### 발견한 결함과 고친 파일

- 어댑터·연결 프로그램 결함: **없음**. `src/` 변경 없음.
- 추가한 파일: `scripts/make_handoff_dir.py`(인계 디렉터리 생성, `scripts/test_make_handoff_dir.py` 8건), 이 문서.
- 메모(결함 아님): `run-local` 만 쓰려 해도 로컬 등록이 필요하고 `register` 는 연결 토큰을 요구한다. 이번에는 `state.save_registration` 으로 넣었다. 커밋 제목은 Codex 요약의 첫 60자를 잘라 쓰므로 단어 중간에서 끊길 수 있다 (`fix(fix-daily-0920): … 확인한 뒤, item`).

## 2026-09-21 — 대본 데모 e2e (phase 5 step 9)

목적: 심사자 흐름 전체(랜딩 → 에이전트 등록 → 업무 가져오기 → 워크플로우 시작 → A 완료 → B 자동 착수 → 검토 승인)를 **로컬 5-프로세스 스택에서 대본 에이전트로** 끝까지 돌린다. 실제 모델·실제 Codex·실제 Claude 는 **돌지 않았다** — 진단은 `DIAG_MODEL=fake`(fixture 대본), 코드 수정은 `workflow.scripted.codex`·`.claude`(connector PATH 앞의 래퍼). 이 절은 실연동 기록이 아니라 대본 경로의 통합 기록이다.

| 항목 | 값 |
|---|---|
| 실행일 | 2026-09-21 13:08:08 ~ 13:09:04 KST (전체 55.6초, 22 passed; 직전 첫 실행도 55.9초 22 passed) |
| 명령 | `WORKFLOW_E2E=1 python3 -m pytest tests/e2e -q -x` (`--durations=0` 로 단계별 시간 채집) |
| 스택 | `LocalStack(scripted=True)` — `tests/e2e/conftest.py` 의 `stack` fixture. `WORKFLOW_SCRIPT_PACE_SECONDS=0`, `DIAG_MODEL=fake`, seed `--scripted`(카탈로그 3개 `시연용 · 대본 재생`), 부모 환경의 `WORKFLOW_*`·`DIAG_*`·`OPENAI_*` 미전달 |
| 에이전트 실행 파일 | `workdir/bin/codex`·`workdir/bin/claude` → `python3 -m workflow.scripted.{codex,claude}` (connector 의 PATH 앞에만). connector 로그 `adapter=codex,claude`, `Codex 실행 시작 pid=…`·`Claude 실행 시작 pid=…` 는 이 래퍼의 pid |
| 시나리오 | test_01~11 직접 등록 경로(기존) → test_12~21 주 경로(등록 → GitHub 가져오기 → 워크플로우) → Jira·다른 세션 → Claude 먼저 등록한 세션(`slow`) |
| 주 경로 A (`#41`, agent-ops-demo) | Execution 생성 04:08:47.48Z → `result_ready` 04:08:52.12Z — **약 4.6초**. 화면 `실행 요청됨 → 완료 · 판정 근거: 14/14` (test_16 4.75초) |
| 주 경로 B (`#42`, agent-codex-mac) | A 완료 직후 04:08:52.13Z 중앙 워커가 Execution 생성(사람 조작 없음) → 시작 확인 04:08:54.41Z → `result_ready` 04:08:55.96Z — **약 3.8초** (worktree 체크아웃, 대본 수정, 재현 pytest 2회, 보고서). 화면 `대기 → 실행 요청됨 → 실행 중 → 확인 필요 · 검토 대기` (test_17 3.78초) |
| Claude 세션 (`slow`) | A 약 5.1초, B(agent-claude-mac) 약 2.8초 — `Claude JSONL` 산출물, `Codex JSONL` 없음 (test_21 7.92초) |
| 산출물 — 진단 | `diagnosis_result`, `evidence` ×8, `handoff_bundle`, `tool_trace`. provenance `model_id=fake-fixture-script` |
| 산출물 — 코드 수정 (Codex 대본) | `code_change_result`, `diff`, `test_log_before`(exit_code=1), `test_log_after`(exit_code=0), `report_output`(합계 20 5), `verification_log`, `codex_jsonl`(thread_id `scripted-codex`), `codex_stderr` |
| 산출물 — 코드 수정 (Claude 대본) | 같은 6종 + `claude_jsonl`(session_id `scripted-claude`, model `scripted-demo-agent`), `claude_stderr` |
| 저장소 | `main` == `report-base` == base_commit (불변, 작업 트리 깨끗). `task/{task_id}` 브랜치 4개(직접 등록 B, test_11 의 C — 연결 복구 뒤 실행됨, 주 경로 #42, Claude 세션 #42), 각 결과 커밋의 부모는 base_commit, 변경 파일 `daily_report/transformer.py`·`tests/test_repro_records.py` 2개. `git worktree list` 는 main 하나, `demo-report-repo-worktrees/` 디렉터리 없음 (step 8 정리) |
| 세션 격리 | 다른 세션의 Jira 워크플로우(OPS-41 → OPS-42, OPS-43·OPS-44 제외)와 서로 404, 홈 목록에 안 보임 |
| 근거 | pytest basetemp `…/pytest-of-kje/pytest-449/stack0/` 의 `central/db.sqlite`(`executions`·`artifacts` 표)·`logs/connector.log`·`demo-report-repo` — 임시 디렉터리라 재실행하면 바뀐다. 재현은 위 명령 |

### 발견한 결함과 고친 파일

- 제품 코드(`src/`) 결함: **없음**. 상태 규칙·워커·연결 프로그램 변경 없이 통과했다.
- 추가한 것: `LocalStack.start_service(name)`(`scripts/local_stack.py`) — 직접 등록 경로의 마지막(test_11)이 연결 프로그램을 내리므로 주 경로가 같은 계획으로 다시 띄운다. 그 결과 test_11 이 `대기` 로 남긴 C 가 연결 복구 뒤 자동 실행됐다(정상 동작 — "재접속 시 claim").
- 메모(결함 아님): 가짜 진단은 폴링 간격 안에 끝나 A 의 `실행 중` 은 화면에 잡히지 않을 수 있다(기존 test_04 와 같은 이유로 관측을 강제하지 않고 순서만 확인). B 는 `실행 중` 관측을 요구한다.

## 2026-09-21 — 공개 VM 배포와 심사자 흐름 완주 (phase 5 배포)

| 항목 | 내용 |
|---|---|
| 대상 | `https://runloom.duckdns.org`, main `46175d0` (phase 5 병합). `install-vm.sh` 재실행(connector 유닛·`[dev]`·connector.env) → `WORKFLOW_RESET_DB=1 update-vm.sh`(스키마 1→2, 백업 `/var/backups/workflow/reset-2026-09-21-055158`) → 런북 5·6(scaffold base_commit `3e285f5`, seed `--scripted`, connect `conn-77453b93`, register codex·claude) |
| env 정정 | `diag.env`: `DIAG_MODEL` openai→fake, `OPENAI_API_KEY` 주석 처리, `DIAG_FAKE_TURN_SECONDS=2.5` 추가, `DIAG_PRICE_*` 2.00/8.00→비움, `DIAG_GLOBAL_DAILY` 36→5000. `central.env`: `WORKFLOW_LIMIT_PER_SESSION_DAILY` 10→200, `GLOBAL_DAILY` 36→5000. 원본은 `/etc/workflow/*.bak-*` |
| 실행 | 런북 7 을 익명 세션에서 HTTP 로 3회(등록 3 → GitHub #41~#44 가져오기 → 시작 → 승인). 진단 A `ready_for_handoff` 25~30초, 코드 수정 B `ready_for_review` 56~61초(사람 조작 없음), 승인 → 체인 `병합: 운영자 확인 대기`, 홈 `2/2 완료`, 결과 카드 `대본 재생 (실제 모델 호출 없음)`, #43·#44 제외 표시 |
| 확인 | 유닛 5개 active, `/agents/register` 연결됨 3, worker·connector ERROR 0, 진단 워커 `model=fake`, 데모 저장소 `main`==base_commit·`task/*` 브랜치만 증가·worktree 는 main 하나 |
| 발견 | 단가가 남아 있던 동안 대본 3회에 `estimated_usd` 0.33 이 쌓임 — 단가를 비운 뒤 4회째는 0 증가(runs_today 4, 0.33 유지). 예산 한도 `30×0.9` 와 일일 36회는 심사 중 429 를 냈을 값. 런북 3·9 절에 대조 항목을 적었다 |
