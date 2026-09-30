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

## 2026-09-22 — 세 번째 종류 review 자동 착수 (phase 6 step 8)

목적: 종류 `review` 와 규칙 `code_change --[ready_for_review]--> review` 를 **화면(`/kinds`·`/rules` 폼)으로만** 등록하면 진단 → 코드 수정 → 검토 3단계가 사람 조작 없이 이어지는지(B 승인 **전에** C 착수), 규칙을 지우면 C' 가 멈추는지, 검토(`LocalTarget`, 읽기 전용)가 저장소를 건드리지 않는지 확인한다 ([ADR-0009](adr/0009-registered-kinds-and-succession-rules.md)). 증명의 핵심은 **`src/workflow/domain/composition.py`·`src/workflow/server/worker.py` 를 이 step 에서 한 줄도 바꾸지 않았다**는 것이다. 실제 모델·실제 Codex·실제 Claude 는 **돌지 않았다** — 진단은 `DIAG_MODEL=fake`, 코드 수정·검토는 `workflow.scripted.codex`/`.claude` 대본 래퍼. 코드는 첫 시도(`de06ffc`, 2026-09-21 23:24)가 커밋했고 그 뒤 세 번의 재시도는 Claude 세션 한도(429)로 시작하지 못했다 — 이번 시도는 실행·검증·기록이다.

| 항목 | 값 |
|---|---|
| 명령·통과 건수 | `WORKFLOW_E2E=1 python3 -m pytest tests/e2e -q -x` — **29 passed** 92.95초, 재실행 29 passed 90.42초 (기존 22 + 세 번째 종류 절 test_22~28 7건). 절만: `-k "test_2 and review" --durations=0` 7 passed 37.70초 (2026-09-22 02:18:21 ~ 02:18:58 KST, 아래 시각은 이 실행의 DB·로그) |
| 전체·린트 | `python3 -m pytest -q` 1545 passed·29 skipped(e2e 는 `WORKFLOW_E2E` 없이 skip), `python3 -m ruff check .` 통과, `python3 -m pytest tests/workflow/scripted scripts/test_seed_demo.py -q` 60 passed |
| 스택 | `LocalStack(scripted=True)` — phase 5 step 9 절과 같다. 대본 `codex`·`claude` 는 connector `build_readonly_argv` 가 낸 인자(`--output-schema <파일>` / `--json-schema <JSON>`, `-C`·cwd = 인계 디렉터리 `<repo>-worktrees/<task_id>.handoff/`)를 그대로 받고, 프롬프트 첫 줄 `# 업무 종류: review (검토)` 로 종류를 읽어 스키마 `outcome.enum` 첫 값과 인계 파일 이름만 적은 `{outcome, summary}` 를 낸다. 파일을 만들지 않는다 |
| 등록 (test_22) | `POST /kinds` — kind `review` · 라벨 `검토` · capability `review`(`seed_demo.REVIEW_CAPABILITY_CODE`) · scope_key `repository_id` · input `diff`,`code_change_result` · outcomes `approved, changes_requested, needs_information` · 지시문 → 303 `/kinds`. `POST /rules` — `code_change` --[`ready_for_review`]--> `review`, handoff `diff`·`code_change_result`·`test_log_after` → 303. `/kinds` 에 내장 2종(`tag-builtin` 2, 삭제 버튼 없음) + `review`(삭제 가능), 규칙 표 2줄. `/tasks/new` select 에 `검토 (review) · review · repository_id`, `/agents/agent-claude-mac` 능력 `review` 옆 `(검토)` — 종류·규칙은 세션별이라 앞 절의 세션들엔 보이지 않는다 |
| 업무 (test_23) | A(`FORM_A`, 진단) `실행 가능 · agent-ops-demo 선택됨`; B(`FORM_B`, 선행 A, Codex 직접 선택) `대기 · 선행 대기`; C(`FORM_C`: capability `review` · scope `demo-report-repo` · 선행 B · run_mode auto · selection auto) → `자동 선택 · agent-claude-mac`(`review · repository_id=demo-report-repo 일치 후보 1개`), 종류 칩 `검토`, `대기 · 선행 대기` |
| 자동 착수 (test_24) | 사람 클릭은 **A 실행 1회**. A exec 생성 17:18:21.843Z → `result_ready` 27.297Z(판정 14/14 → `완료`). 워커 tick 27.303 `verdicts 1 · successors_created 1` → B exec 27.299Z → 시작 28.088Z → `result_ready` 29.940Z. 워커 tick **30.321 `results_checked 1 · successors_created 1`** — B 의 코드 결과 판정(passed)과 C 착수가 **같은 tick**, 이때 B 는 `확인 필요 · 검토 대기`(사람 승인 전). C exec 30.316Z → 시작 31.994Z(`Claude 실행 시작 pid=…` — 대본 래퍼) → `result_ready` 32.323Z. tick 33.338 `generic_checked 1` → 판정 passed(`envelope_valid`·`ids_match`·`outcome_in_spec`) → C `확인 필요 · 검토 대기`. 화면: `data-outcome="approved"`, `결과 봉투 · review`, `대본 재생 (실제 모델 호출 없음)`, 병합 문구 없음 |
| 인계·결과 (test_25) | B 실행의 `handoff_bundle`(`handoff.json`, 831B): `source_kind` `code_change`, `source_result_artifact_id` = 수정 결과, `inputs` kind {`diff`, `test_log_after`, `code_change_result`} = 규칙 `handoff_kinds`, `attachments []`. C 산출물 3개만: `generic_result`(`generic_result.json`) · `claude_jsonl`(session_id `scripted-claude`, model `scripted-demo-agent`) · `claude_stderr` — 코드 수정 산출물 없음. 결과 봉투: kind `review` · outcome `approved` · summary `대본 review: 인계 자료 4개 확인 — code_change_result.json, diff.patch, manifest.json, test_log_after.txt` · `artifact_ids` = 원시 로그 2개 |
| 승인·중복 (test_26) | B 승인 → `완료 · 검토 승인 · 병합: 운영자 확인 대기`; C 승인 → `완료 · 검토 승인`(병합 없음). 10초 뒤 `executions` 는 A·B·C 각 1건(`result_ready`, `failed_code` NULL) |
| 규칙 삭제 (test_27) | `POST /rules/{rule_id}/delete` 303 → 규칙 표는 내장 1줄. A'·B'·C' 등록 후 A' 실행 → B' 는 내장 규칙으로 착수(exec 48.451Z → `result_ready` 50.088Z). tick 51.460 `results_checked 1 · successors_created 0`. C' 실행 0건, `tasks.status` `대기`, `status_reason` `후속 규칙 없음: code_change → review — 규칙을 등록하거나 직접 실행`, B' 에 인계 묶음 없음, `POST /tasks/{C'}/run` → 409 `후속 규칙이 없어 인계 자료가 없습니다` |
| 저장소 (test_28) | `main` == base_commit, 작업 트리 깨끗, `task/{B}` = B 결과 커밋 그대로(C 실행 전후 동일), `task/{C}` 브랜치 없음, `git worktree list` 는 main 하나, `demo-report-repo-worktrees/` 에 `{C}`·`{C}.handoff` 없음(빈 디렉터리만). C 실행이 `result_ready`(실패 코드 없음)이므로 인계 디렉터리에 새 파일이 생기지 않았다 — 생겼다면 connector 가 `readonly_violation` 으로 실패시킨다 |
| **변경 없음 증명** | `git diff --stat 1365f16 -- src/workflow/domain/composition.py src/workflow/server/worker.py` → **빈 출력** (`1365f16` = step 7 마지막 커밋, 이 step 첫 커밋 `de06ffc` 의 부모; 작업 트리 기준 `git diff --stat HEAD -- … \| wc -l` 도 0). `grep -rn "workflow.scripted" src/workflow/connector src/workflow/server` 에 import 없음 — 대본은 PATH 래퍼로만 앞에 둔다 (ADR-0008) |
| 근거 | pytest `--basetemp=/tmp/e2e-review-basetemp` 의 `stack0/central/db.sqlite`(`tasks`·`executions`·`artifacts`·`task_verdicts`·`kinds`·`succession_rules`)·`stack0/logs/central_worker.log`·`connector.log`·`demo-report-repo` — 임시 디렉터리라 재실행하면 바뀐다. 재현은 위 명령 |

### 발견한 결함과 고친 파일

- 제품 코드(`src/workflow/` 중 `scripted/` 제외) 결함: **없음**. 도메인·워커·연결 프로그램·웹 변경 없이 통과했다.
- 이 step 의 변경: `src/workflow/scripted/_common.py`(`generic_kind_of`·`generic_outcomes`·`handoff_listing`·`generic_result`)·`codex.py`(`--output-schema` 읽기, 사용자 정의 종류 분기 — 같은 JSONL 3줄 봉투)·`claude.py`(`--json-schema`, `structured_output = {outcome, summary}`), `scripts/seed_demo.py`(Claude 능력 `code.modify` + `review`, 상수 `REVIEW_CAPABILITY_CODE`), 테스트(`tests/workflow/scripted/` 60건, `scripts/test_seed_demo.py`, `tests/e2e/test_scenario.py` test_22~28 — 파일에 test_12~21 주 경로가 이미 있어 번호가 22 부터다).
- 메모(결함 아님): 결과 봉투 summary 에 `manifest.json` 이 들어간다 — connector 가 인계 디렉터리에 두는 목록 파일도 프롬프트의 인계 목록에 나열되고, 대본은 목록의 파일 이름만 적기 때문. 공개 데모에는 `review` 종류가 등록되지 않으므로 Claude 카드의 능력 `review` 는 매칭되지 않고 코드만 보인다(허용, seed 주석).

## 2026-09-22 — 실제 Claude 로 세 번째 종류 review 1회 (phase 6 실연동)

목적: phase 6 step 8 의 세 번째 종류 흐름을 대본이 아닌 **실제 `claude` CLI** 로 한 번 돌려, 연결 프로그램의 읽기 전용 실행(`LocalToolAdapter._run_generic` → `ClaudeAdapter.launch_readonly`)이 실제 도구·실제 모델과 맞물리는지 확인한다. 실행은 1회다. 진단은 `DIAG_MODEL=fake`, 코드 수정(B)은 대본 codex(`tests/e2e/fake_codex.py` shim)이고 **검토(C)만 실제 Claude** 다 — Codex 사용량이 없어 B 는 대본으로 뒀다.

| 항목 | 값 |
|---|---|
| 실행일 | 2026-09-22 09:42:31 ~ 09:44:19 KST (A 실행 클릭부터 C 검토 대기까지 98.0초, Claude 프로세스 85.0초) |
| Claude | `2.1.278 (Claude Code)`, `/opt/homebrew/bin/claude`, 운영자 로그인(구독) 재사용. 모델은 운영자 기본값 `claude-opus-5[1m]` (`modelUsage`) |
| 스택 | `LocalStack(workdir=../workflow-live-review-2026-09-22, fake_codex=tests/e2e/fake_codex.py, scripted=False)` — connector PATH 앞에 `codex` 래퍼만, `claude` 는 PATH 의 실제 바이너리. 작업 디렉터리를 이 저장소 밖에 둬 Claude 가 이 저장소의 CLAUDE.md·훅을 프로젝트 설정으로 읽지 않게 했다. 기동 세션(Claude Code)의 `CLAUDECODE`·`CLAUDE_CODE_*` 는 지우고 띄웠다 |
| 드라이버 | e2e `tests/e2e/test_scenario.py` 의 test_22~28 흐름과 폼(`REVIEW_KIND_FORM`·`REVIEW_RULE_FORM`·`FORM_A/B/C`)을 그대로 HTTP 로 재생한 1회성 스크립트(저장소 밖). 종류·규칙 등록 → A·B(Codex 직접 선택)·C 등록 → A 실행 클릭 1회 → 관찰 → 승인 |
| Claude argv (어댑터 고정) | `claude -p --output-format json --no-session-persistence --permission-mode acceptEdits --allowedTools Read Glob Grep --json-schema {"type":"object","properties":{"outcome":{"enum":[approved, changes_requested, needs_information]},…}}` — cwd = 인계 디렉터리 `<repo>-worktrees/task-4d11c1412fef.handoff/`, 프롬프트는 stdin (`ps` 로 확인) |
| Claude 결과 봉투 | `subtype=success`, `is_error=false`, `num_turns=7`, `duration_ms=81692`(api 79206), `permission_denials=[]`, `api_error_status=null`. 토큰: input 6 · cache_creation 36,255(1h) · cache_read 67,659 · output 6,160(thinking 3,745). `total_cost_usd=0.5504`(`costBasis: list` — 구독이라 청구 아님, 참고값). stderr 0 바이트 |
| 결과 (`generic_result.json`, 1,518B) | kind `review` · **outcome `changes_requested`** · execution/task id 일치 · `artifact_ids` = Claude JSONL·stderr 2개. summary 는 아래 "검토 내용" |
| 화면 | C 상태 순서 `대기·선행 대기` → `실행 요청됨·접수 대기` → `접수 확인` → `실행 중·시작 확인` → `확인 필요·검토 대기`. 이벤트 `accepted, started, progress("Claude 종료 exit=0"), result_ready`. `data-outcome="changes_requested"`, `결과 봉투 · review`, **`대본 재생` 문구 없음**, 병합 문구 없음. C 가 검토 대기가 됐을 때 B 는 아직 `확인 필요 · 검토 대기`(승인 전 착수) |
| 시각 (UTC) | A exec 00:42:31.267 → `result_ready` 36.690 → tick 36.697 `verdicts 1 · successors_created 1`(14/14 완료). B exec 36.693 → 시작 37.545(대본 codex pid 23121) → `result_ready` 39.746. tick 42.724 `results_checked 1 · successors_created 1` → C exec 42.718 → accepted 43.791 → started 43.813(**Claude pid 23454**) → progress `Claude 종료 exit=0` 44:08.840 → `result_ready` 44:08.853 |
| 인계 묶음 (B) | `source_kind=code_change`, `source_result_artifact_id` = B 수정 결과, `inputs` = {`diff`, `code_change_result`, `test_log_after`} (규칙 `handoff_kinds` 그대로), `attachments []`. 인계 디렉터리에는 이 3개 + `manifest.json` |
| 저장소 불변 | `main` == base_commit `0b05eb99…`, 작업 트리 깨끗, `task/{B}` == B 결과 커밋 `07ba467e…`(C 뒤에도 동일), `task/{C}` 브랜치 없음, `git worktree list` main 하나, `demo-report-repo-worktrees/` 비어 있음(C 인계 디렉터리 정리됨). C 실행 `result_ready`·`failed_code NULL` → `readonly_violation` 없음 |
| 승인·중복 | B 승인 → `완료 · 검토 승인 · 병합: 운영자 확인 대기`, C 승인 → `완료 · 검토 승인`. 10초 뒤 `executions` A·B·C 각 1건(`result_ready`, NULL) |
| 비밀값 | 증거 파일에서 `wfc_` 0건, `sk-` 는 `task-…` 식별자 안의 부분 문자열뿐 |
| 세션 저장 | `--no-session-persistence` — `~/.claude/projects/<인계 디렉터리 경로>/` 가 생겼으나 빈 `memory/` 뿐, 대화 기록 파일 없음 |
| 근거 | `../workflow-live-review-2026-09-22/` — `report.json`(드라이버 요약; 끝의 `error: SystemExit(0)` 은 정상 종료를 드라이버가 잘못 적은 것), `evidence/`(`C_결과_봉투.json`·`C_Claude_JSONL.jsonl`·`C_live.html`·`B_diff.patch`·`B_code_change_result.json`·`B_handoff_bundle.json`·`C_offline_generic_checks.json`), `central/db.sqlite`, `logs/{central_worker,connector}.log`. 커밋하지 않는다 |

### 검토 내용 — 실제 모델이 대본 수정에서 결함을 찾았다

Claude 의 summary(원문은 `evidence/C_결과_봉투.json`): 진단 원문이 인계 자료에 없어 `code_change_result.summary` 와 docstring 을 기준으로 대조했고, 핵심 요구(`$.items`/`$.data.records` 중 하나, 둘 다면 `AMBIGUOUS_RECORDS_FIELD`, 없으면 `MISSING_RECORDS_FIELD`, 테스트 3개 추가·14 passed)는 충족. **그러나** `transformer.py` 의 `(data or {}).get("records")` 는 `data` 가 truthy 비-dict(`"oops"`, `[1]`, `1`)일 때 `AttributeError` 로 크래시해, 수정 전엔 모든 비정상 형태를 `TransformError(MISSING_RECORDS_FIELD)` 로 바꾸던 계약을 깨뜨린다 → `changes_requested`. 바로 위의 `has_records` 를 재사용하면 한 줄. 비차단: `docs/contract.md` 가 diff 에 없어 계약 문구 일치는 미확인.

- `evidence/B_diff.patch` 로 확인: 지적이 맞다. 대본 codex(`workflow.scripted.codex`)의 고정 수정안이 가진 실제 결함이다 (공개 데모의 B 결과에도 같은 코드가 들어간다 — 데모 대본의 결함이지 제품 코드 결함은 아님).
- 대본 e2e 는 항상 `approved`(스키마 enum 첫 값)였다. 실제 모델은 `changes_requested` 를 냈고 중앙은 봉투·id·`outcome ∈ spec` 만 보므로 그대로 `확인 필요 · 검토 대기` 가 됐다 — ADR-0009 (5) 대로 내용은 보지 않는다.
- 규칙 `handoff_kinds` 에 진단 결과가 없어 검토자가 "진단 원문 없음" 을 명시했다. 규칙 등록 데이터의 문제이지 코드 문제가 아니다 — 검토 규칙에 `diagnosis_result` 를 넣을지는 운영자 선택.

### ARCHITECTURE "검증 순서" 6번

| 순서 | 검증 | 결과 | 근거 |
|---|---|---|---|
| 6 | 세 번째 종류 — 실제 Claude | **통과** (C 만 실제 Claude, A 는 fake 진단, B 는 대본 codex). 화면 등록만으로 A → B → C 자동 착수(B 승인 전), 실제 `claude -p` 가 `Read Glob Grep` 만으로 인계 자료를 읽고 `{outcome, summary}` 봉투를 냈으며(`permission_denials []`), 인계 디렉터리·저장소 불변, `readonly_violation`·`result_invalid`·`usage_limit` 없음. 규칙 삭제 시나리오(test_27)는 이번엔 돌리지 않았다(대본 e2e 로만) | 위 표 |

### 발견한 결함과 고친 파일

- **연결 프로그램은 도구가 도는 동안 heartbeat 를 보내지 않는다** (제품 결함 — 같은 날 `service` 에서 수정: `Runner._start` 가 `adapter.run` 동안 heartbeat 스레드를 돌린다, `tests/workflow/connector/test_runner.py` heartbeat 3건. 아래는 발견 당시 기록). `runner.tick` 이 단일 스레드로 `_claim_and_start` → 어댑터 → `communicate_or_stop`(`proc.communicate(timeout=1200)`) 를 동기로 돌리므로 `_heartbeat_if_due` 가 실행 중엔 호출되지 않는다. 이번 실행: 마지막 heartbeat 09:42:41.775, claim 43.788, 다음 heartbeat **09:44:10.862** (Claude 가 끝난 뒤). 중앙 워커 tick 09:42:54.751 `agents_offline 2 · observations 1` — 로컬 Agent 둘(같은 연결 프로그램)이 `offline` 이 되고 C 실행에 `execution_observations` `heartbeat_lost`("연결 프로그램 heartbeat 미수신 (마지막 확인 …43.785Z)") 가 남았다. 결과 자체는 정상 수신·판정됐다(재실행 없음, 재접속 후 online). 로컬 스택은 offline 판정 10초·heartbeat 3초라 바로 드러났고, 운영 기본값(30초/90초)에서는 **90초 넘는 실제 실행**(Step 15 의 Codex B 2분 35초가 이미 그렇다)마다 같은 일이 난다. 대본 e2e(실행 1초 미만)와 `run-local`(중앙 없음)로는 보이지 않던 것. ARCHITECTURE 의 "연결 생존과 모델 진행 구분" 은 실행 중엔 성립하지 않는다. 스레드 방식으로 고쳤다 — 어댑터는 그대로, `runner.py` 만.
- **사람 승인이 중앙 판정보다 먼저 오면 판정이 기록되지 않는다** (관찰, 결함 여부는 결정 필요). `/live` 는 `result_ready` 직후 `확인 필요 · 검토 대기` 를 실시간으로 보여주고 검토 폼을 열지만, 워커의 `_check_generic_results` 는 다음 tick(≤3초)에 돈다. 드라이버가 0.4초 만에 승인해 `released_at` 이 찍혔고 `results_awaiting_verdict`(`released_at IS NULL`)에서 빠져 C 의 `task_verdicts` 행이 **없다** (worker.log 에 `generic_checked` 0). 같은 검사를 보존된 DB·산출물에 오프라인으로 적용하면 `envelope_valid`·`ids_match`·`outcome_in_spec` 모두 통과 (`evidence/C_offline_generic_checks.json`). 대본 e2e(test_25)는 판정을 기다린 뒤 승인하므로 드러나지 않는다. 사람이 3초 안에 승인하는 일은 드물지만, 판정 전 승인을 막을지(폼 비활성) 또는 승인 시 판정을 같이 남길지는 사용자 결정.
- 어댑터·워커·웹 코드 변경: **없음**. 이 항목의 변경은 이 문서, [ARCHITECTURE](ARCHITECTURE.md) "검증 순서" 6번, [CURRENT_HANDOFF](CURRENT_HANDOFF.md) 뿐.

## 2026-09-22 — n8n 입구·출구 e2e (phase 7 step 8)

목적: [ADR-0010](adr/0010-n8n-inbox-and-callback.md)의 입구(입구 토큰 → `POST /sources/n8n/chains` → 접수 즉시 첫 업무 시작)와 출구(사람 차례 `chain_settled` 에 `ChainCallback` 체인당 1회)를 **로컬 5-프로세스 스택에서 HTTP 로** 끝까지 돌린다. 무엇이 대본이고 무엇이 실제였는지: 에이전트는 **대본**(진단 `DIAG_MODEL=fake`, 코드 수정 `workflow.scripted.codex` — 실제 모델·Codex·Claude 없음), **n8n 은 테스트 안 HTTP 수신기**(`_CallbackReceiver`, `127.0.0.1` 임시 포트, n8n Wait 노드 역할 — 실제 n8n·Docker 없음, 그것은 step 10). 입구 API·워커의 callback 전달·허용 목록·토큰 인증·화면은 **실제 제품 코드**이며 이 step 에서 `src/` 는 한 줄도 바꾸지 않았다.

| 항목 | 값 |
|---|---|
| 명령·통과 건수 | `WORKFLOW_E2E=1 python3 -m pytest tests/e2e -q -x --durations=0 --basetemp=/tmp/e2e-n8n-basetemp` — **36 passed** 130.31초 (2026-09-22 13:05:01 ~ 13:07:12 KST; 기존 29 + n8n 절 test_29~35 7건, 아래 시각은 이 실행의 DB·로그). 절만: `-k n8n` **7 passed** 36.18초(수신기 결함 수정 뒤 재실행; 첫 실행은 71.65초 — 아래 "발견한 결함") |
| 전체·린트 | `python3 -m pytest -q` 1714 passed·36 skipped(e2e 는 `WORKFLOW_E2E` 없이 skip), `python3 -m ruff check .` 통과, `python3 -m pytest scripts/test_local_stack.py -q` 28 passed(+5) |
| 스택 | `LocalStack(scripted=True)` — phase 5 step 9 절과 같고, 이 step 이 더한 인자 `callback_hosts="127.0.0.1"`(기본, 그 호스트의 모든 포트)·`public_url=None`(기본 = `central_url`)이 중앙 API·중앙 워커 env `WORKFLOW_CALLBACK_HOSTS=127.0.0.1`·`WORKFLOW_PUBLIC_URL=http://127.0.0.1:18000` 이 된다(진단·연결 프로그램 env 에는 없음). `tests/e2e/conftest.py` 는 기본값으로 충분해 무변경. CLI `--callback-hosts`·`--public-url` 은 [docs/n8n/README.md](n8n/README.md) 2절의 한 줄 명령과 같은 이름 |
| n8n 역할 | `tests/e2e/test_scenario.py` 의 `_CallbackReceiver` — `ThreadingHTTPServer(("127.0.0.1", 0))` 데몬 스레드, 이번 실행 포트 57570, POST 를 `(path, headers, body)` 로 쌓고 200 `{"ok": true}`. `0.0.0.0` 이 아니다. 실패 모드 없음(재시도·중단은 `tests/workflow/server/test_worker.py` 가 했다) |
| 입구 (test_29) | 세션이 카탈로그 3개를 ops → codex → claude 순으로 등록(prefer 규칙으로 코드 수정은 codex). `GET /sources` 200 — 입구 주소 `http://127.0.0.1:18000/sources/n8n/chains`, 허용 host `127.0.0.1`, 사이드바 `입구`. `POST /sources/tokens`(라벨 `e2e`) → 200 에 원문 `wfs_…` 1회 + `src-b47fbb94`(04:06:37.999Z). 다시 `GET /sources` 에는 원문 없음. DB 파일 `grep -c wfs_` 0, `token_sha256` 64자 |
| 접수 (test_30) | **쿠키 없이** `Authorization: Bearer wfs_…` 만으로 CONTRACT 12절 (a) 본문 + `callback_url=http://127.0.0.1:57570/webhook-waiting/e2e` POST → 04:06:38.017Z **201** `InboundChainResponse`(계약 왕복): `started=true`, `chain-0d9a7fb95b22`, `chain_url=http://127.0.0.1:18000/chains/…`, tasks `[run-daily-0920 diagnosis 실행 요청됨, fix-format code_change 대기]`, skipped `[]`. 진단 Execution 은 접수와 같은 시각에 생성(사람의 "워크플로우 시작" 없음). 체인 화면: 출처 칩 `n8n`, `시연 데이터` 없음, `callback · 127.0.0.1:57570 · 대기(사람 차례가 되면 보냄)`(URL 전체 없음), B 노드 이유 `일치 후보 2개 … 먼저 등록한 agent-codex-mac`(`items_json` 재계산), 뒤 노드 실행 버튼 없음, 홈 `n8n · 0/2 완료` |
| A → B (test_31) | A exec 04:06:38.017 → 시작 42.700 → `result_ready` 42.725(fixture 대본, `model_id=fake-fixture-script`). 워커 tick 42.730 `verdicts 1 · successors_created 1`(14/14 → `완료`) → B exec 42.727 → 시작 42.980(대본 codex) → `result_ready` 44.532. 화면 `대기 → 실행 요청됨 → 실행 중 → 확인 필요 · 검토 대기`, 사람 단계 `확인 필요 · 검토 대기`, `data-poll="0"`. 산출물 diff·테스트 전(exit 1)·후(exit 0)·보고서·수정 결과, `Codex JSONL` 에 `scripted-codex` |
| **callback 1건** (test_32) | 워커 tick **04:06:45.754 `results_checked 1 · callbacks_sent 1`** — B 결과 판정과 **같은 tick 의 마지막 단계**에서 전송. `callback_sent_at` 04:06:45.748Z(접수부터 7.7초, B 승인 전). 수신 1건: path `/webhook-waiting/e2e`, `content-type: application/json`, `ChainCallback.model_validate` 통과 — `source n8n`, `chain_id` 일치, title `일일 보고서 2026-09-20 09:00 실행 실패 → 응답 형식 변경에 맞춰 보고서 변환 수정`, `human_gate` (`검토 승인 (사람) · 병합은 운영자 확인`, `확인 필요`, `검토 대기`), tasks[0] `diagnosis · 완료 · 판정 근거: 14/14 · ready_for_handoff` + summary, tasks[1] `code_change · 확인 필요 · 검토 대기 · ready_for_review` + summary(= 수정 결과 봉투의 `summary` 그대로), `chain_url`·`task_url` 모두 `http://127.0.0.1:18000/…`. 체인 화면 `callback · 127.0.0.1:57570 · 전송됨`, DB `callback_attempts 0 · callback_next_at NULL · callback_last_error NULL` |
| 두 번째 없음 (test_33) | B 승인 04:06:45.931Z → `완료 · 검토 승인 · 병합: 운영자 확인 대기`, 사람 단계 `완료 · 병합: 운영자 확인 대기`. 10초(워커 3바퀴+) 뒤 수신 여전히 **1건**, `callback_sent_at` 그대로, attempts 0, B 실행 1건(`result_ready`) |
| 거부 경로 (test_34) | (a) `callback_url=http://example.com/x` → **422** `callback_host_not_allowed`(field `callback_url`, `details.allowed ["127.0.0.1"]`, 메시지에 `example.com`), 홈의 체인 수 그대로. (c) Bearer 없음 — 세션 쿠키가 있어도 **401** `unauthenticated`, 쿠키도 없으면 401. (d) `callback_url` 없이 → **201** `started=true` `chain-b1a37bdcb189`(04:06:56.040Z), 화면에 callback 줄 없음, DB `callback_url NULL`; 그 체인은 A → B 가 그대로 이어져 04:07:04.371Z `확인 필요 · 검토 대기` 가 됐고 7초 뒤에도 수신 1건·`callback_sent_at NULL`. (b) `POST /sources/tokens/src-b47fbb94/revoke` → 303 `/sources`, 화면 `취소됨`(04:06:56.051Z), 같은 POST → **401**. 실행 순서는 (a)(c)(d)(b) — (d) 가 토큰을 쓰므로 취소를 마지막에 |
| 세션 격리 (test_35) | 다른 세션에서 체인 2개·B 404, 홈 목록 없음, `/sources` 에 토큰 없음, 그 토큰 취소 404(존재를 알리지 않음). 발급한 세션의 홈에는 체인 2개 |
| 비밀값 | 입구 API 응답·callback 본문에 토큰 원문 없음, DB 파일에 `wfs_` 0건 |
| 근거 | `/tmp/e2e-n8n-basetemp/stack0/` 의 `central/db.sqlite`(`chains`·`source_tokens`·`tasks`·`executions`)·`logs/central_worker.log`(13:06:45,754 tick 줄)·`central_api.log`(`/sources/n8n/chains` 201·422·401·401·201·401)·`connector.log` — 임시 디렉터리라 재실행하면 바뀐다. 재현은 위 명령 |

### 발견한 결함과 고친 파일

- 제품 코드(`src/`) 결함: **없음**. `git diff --stat HEAD -- src` 빈 출력 — `chain_settled`·워커 단계 순서·허용 목록 검사·입구 API·화면 무변경으로 통과했다.
- 이 step 의 변경: `scripts/local_stack.py`(`LocalStack(callback_hosts=, public_url=)` → 중앙 env 두 키, `--callback-hosts`·`--public-url`, docstring 사용법), `scripts/test_local_stack.py`(5건), `tests/e2e/test_scenario.py`(test_29~35 + `_CallbackReceiver`·`_inbound`·`_chain_row`·`_chain_ids`, 모듈 docstring), 이 문서.
- **테스트 하네스 결함(고침)**: `http.server.HTTPServer.server_bind` 가 `socket.getfqdn("127.0.0.1")` 로 역방향 DNS 를 조회하는데 이 Mac 에서 **35.0초** 걸렸다(첫 `-k n8n` 실행의 test_30 setup 35.01초 — 따로 잰 `getfqdn` 도 35.01초). `_LoopbackServer.server_bind` 가 `TCPServer.server_bind` 만 부르고 `server_name` 을 주소 문자열로 둬 0초. 워커 쪽은 httpx 클라이언트라 무관하다.
- 메모(결함 아님): step 의 AC `python3 scripts/local_stack.py --help | grep -c "callback-hosts\|public-url"` 는 2 가 아니라 **4** — argparse 가 usage 블록(2줄)과 옵션 설명(2줄)에 각각 찍는다. 두 옵션 모두 있다.
- 메모(결함 아님): 화면이 B 를 `확인 필요 · 검토 대기` 로 보이는 시점(`result_ready` 직후, 실시간 판정)과 callback 시점(다음 tick 의 `_check_code_results` 뒤 마지막 단계) 사이는 최대 한 tick(3초)이다 — test_32 는 상태 폴링 30초 한도로 기다린다. 이번 실행은 B `result_ready` 44.532 → callback 45.748.
- [ARCHITECTURE](ARCHITECTURE.md) "검증 순서" 7번(실제 n8n)은 그대로 미검증 — step 10 의 몫이다.

## 2026-09-22 — 실제 n8n 으로 입구·출구 1회 (phase 7 step 10)

목적: [ADR-0010](adr/0010-n8n-inbox-and-callback.md)의 입구·출구를 테스트 안 수신기가 아닌 **실제 n8n(Docker)** 으로 1회 돌린다 — n8n 의 HTTP Request 노드가 Runloom 입구 API 를 부르고, Runloom 워커의 callback 이 n8n Wait 노드를 실제로 깨우는지. [docs/n8n/README.md](n8n/README.md) 의 CLI 경로를 그대로 따랐고, 틀린 곳은 절차서를 고쳤다(아래). 무엇이 실제이고 무엇이 대본인지: **실제** — n8n 2.39.10 컨테이너, n8n → Runloom HTTP(`host.docker.internal:18000`), Runloom 워커 → n8n HTTP(`localhost:5678`), Wait 노드 재개·Slack 노드(비활성 통과)까지의 n8n 실행. **대본** — 에이전트(진단 `DIAG_MODEL=fake`, 코드 수정 `workflow.scripted.codex`; 실제 모델·Codex·Claude 없음, `OPENAI_API_KEY` 없음). 제품 코드(`src/`)·테스트는 한 줄도 바꾸지 않았다(`git diff --stat HEAD -- src tests` 빈 출력). 공개 데모 VM 은 건드리지 않았다.

| 항목 | 값 |
|---|---|
| 실행일 | 2026-09-22 13:39 ~ 13:49 KST (이미지 pull·기동·import 포함 약 10분). 트리거 → callback 은 **13:44:50.844 → 13:44:57.356 KST, 6.5초** |
| Docker | Docker Desktop 4.21.1 (114176), Engine 24.0.2, 호스트 darwin/arm64 · 엔진 linux/arm64 |
| n8n | `docker.n8n.io/n8nio/n8n:latest` → `n8n --version` **2.39.10**. `docker run -d --name runloom-n8n -p 5678:5678 -v runloom_n8n_data:/home/node/.n8n …`(README 2절 그대로), `healthz` 200 은 기동 1초 뒤. 소유자 계정·화면 로그인 없이 CLI 만 썼다 |
| Runloom 스택 | `python3 scripts/local_stack.py --scripted --workdir ../workflow-live-n8n-2026-09-22 --callback-hosts localhost:5678 --public-url http://127.0.0.1:18000`(README 2절 (a)). 기동 출력 `callback 허용 목록: localhost:5678 · 공개 주소: http://127.0.0.1:18000`. 저장소 밖 작업 디렉터리 |
| 세션·등록·토큰 (curl, 쿠키 jar) | `GET /` → `POST /agents/register` ops → codex → claude(303 ×3, e2e 와 같은 순서) → `/agents` 에 셋 다 `연결됨` → `/sources` 입구 주소 `http://127.0.0.1:18000/sources/n8n/chains`, `허용된 host: localhost:5678` → `POST /sources/tokens`(라벨 `n8n 로컬`) 200, 원문 `wfs_` 47자 1회 + `이 값은 다시 볼 수 없습니다`, 재열람에 원문 없음. 토큰은 셸 변수에만 뒀다. 첫 발급(`src-abe162a2`)은 셸 변수를 자식에 넘기지 못해 자격 증명 파일을 못 만들었고 취소(303) 후 재발급 `src-b048e33a` 로 진행 — 제품과 무관한 조작 실수 |
| n8n 에 넣기 (CLI) | 워크플로우 사본은 `sed` 로 URL 포트 8000 → 18000. 자격 증명은 README decrypted 형식(`id: runloom-source-token`, `httpHeaderAuth`, `Authorization: Bearer wfs_…`) 0600 파일 → `docker cp` 둘 → **`import:credentials` EACCES**(절차서 결함 1, 아래) → `chown node:node` → `Successfully imported 1 credential.` → 컨테이너·호스트 자격 증명 파일 삭제 → **`import:workflow` `SQLITE_CONSTRAINT: NOT NULL constraint failed: workflow_entity.id`**(절차서 결함 2) → 사본에 `"id": "runloomHandoff001"` 추가 → `Successfully imported 1 workflow.` → `list:workflow` `runloomHandoff001\|Runloom handoff` → `publish:workflow --id=runloomHandoff001`("Please restart n8n") → `docker restart` → healthz 3초 → n8n 로그 `Finished building workflow dependency index. Processed 0 draft workflows, 1 published workflows.` · `Activated workflow "Runloom handoff" (ID: runloomHandoff001)`(04:44:40.072Z) |
| 트리거 | 13:44:50.844 KST `curl -X POST http://localhost:5678/webhook/runloom-demo -H 'content-type: application/json' -d '{"run_id":"daily-0920-0900"}'` → **200** `{"message":"Workflow was started"}` 50ms. n8n 이벤트 로그: `workflow.started`(exec 1, 04:44:50.902Z) → Webhook 50.903~50.904 → **HTTP Request 50.904 → 50.955(51ms)** → Wait 50.955 → 50.959 → `n8n.audit.workflow.waiting` |
| 입구 (Runloom) | `central_api.log` `POST /sources/n8n/chains` **201**. `chains` 행 `chain-c59759073459 · source n8n · created_at 04:44:50.943979Z · callback_url http://localhost:5678/webhook-waiting/1?signature=…`(n8n `$execution.resumeUrl` — host 가 허용 목록 안). n8n HTTP Request 노드 출력 = `InboundChainResponse`(계약 왕복 통과): `started true`, `start_error null`, tasks `[run-daily-0920-0900 diagnosis 실행 요청됨, fix-daily-0920-0900 code_change 대기]`, `chain_url http://127.0.0.1:18000/chains/chain-c59759073459`, skipped `[]`. 체인 제목 `일일 보고서 생성 실패 (daily-0920-0900) → 집계 API 응답 형식 변경 대응`(n8n 표현식이 만든 항목 — fixture 와 라벨만 같다). B 노드 이유 `일치 후보 2개 — 먼저 등록한 agent-codex-mac`(`items_json` 재계산) |
| A → B (대본) | 워커 tick 13:44:51.215 `submitted 1`(진단 `POST /runs` 202, 접수와 같은 tick — 사람의 "워크플로우 시작" 없음) → tick 13:44:54.270 `events_applied 10 · verdicts 1 · successors_created 1`(A `완료 · 판정 근거: 14/14`, B Execution 04:44:54.266Z 생성 → 54.945Z 대본 codex 시작) → B `result_ready` → 다음 tick |
| **callback → Wait 재개** | 워커 tick **13:44:57.358 `results_checked 1 · callbacks_sent 1`** — B 결과 판정과 같은 tick 의 마지막 단계. httpx `POST http://localhost:5678/webhook-waiting/1?signature=… "HTTP/1.1 200 OK"`(13:44:57.356). DB `callback_sent_at 2026-09-22T04:44:57.288077Z · callback_attempts 0 · callback_last_error NULL`. **접수부터 6.3초, 트리거부터 6.5초.** n8n 쪽: `n8n.audit.workflow.resumed`(04:44:57.333Z) → Wait 57.385~57.386 → Slack(disabled, 통과) 57.386 → **`n8n.workflow.success`**(57.386). `execution_entity` 1행: `status success · finished 1 · mode webhook · startedAt 04:44:50.886 · stoppedAt 04:44:57.386 · waitTill NULL`. Wait 노드 출력 `body` 는 **`ChainCallback` 계약 왕복 통과** — `source n8n`, `chain_id` 일치, `settled_at 04:44:57.288077Z`, `human_gate` (`검토 승인 (사람) · 병합은 운영자 확인`, `확인 필요`, `검토 대기`), tasks[0] `diagnosis · 완료 · 판정 근거: 14/14 · ready_for_handoff` + summary, tasks[1] `code_change · 확인 필요 · 검토 대기 · ready_for_review` + summary(수정 결과 봉투 그대로), `chain_url`·`task_url` 모두 `http://127.0.0.1:18000/…`. 요청 헤더 `user-agent python-httpx/0.28.1 · content-type application/json · content-length 1315`. Slack 노드(비활성) 출력은 같은 항목을 그대로 통과 — README 5절 4항과 일치 |
| 2xx 가 재개의 증거인지 | 끝난 실행의 같은 resume URL 에 다시 POST → n8n **409** `The execution "1 has finished already.`(step 지시의 "404" 는 이 버전에서 409 — 어느 쪽이든 비-2xx). 따라서 워커가 기록한 2xx 는 대기 중이던 실행을 실제로 깨운 것이다 |
| 화면 | `GET /chains/chain-c59759073459`: 출처 칩 `n8n`, `시연 데이터` 없음, `<p class="small callback-line" data-callback-state="전송됨">callback · localhost:5678 · 전송됨 1분 전</p>`, `webhook-waiting`·`signature` 미노출, 사람 단계 `확인 필요 · 검토 대기` + `검토하기` |
| 두 번째 없음 | 13:47:08 `POST /tasks/task-8a357d395b9c/review decision=approve` → 303 → B `완료`, 사람 단계 `완료 · 병합: 운영자 확인 대기 · 2단계 모두 완료`. 10초 뒤 `callback_sent_at`·`attempts 0`·`last_error NULL` 그대로, 워커 로그의 `webhook-waiting` POST **1회**, `callbacks_failed` 0, n8n `execution_entity` **1행** |
| 정리 | 13:48:27~13:49 스택 종료, `docker rm -f runloom-n8n`, `docker volume rm runloom_n8n_data` → `docker ps -a --filter name=runloom-n8n` 0건. 컨테이너 안 자격 증명 파일은 import 직후 삭제, 호스트 사본도 즉시 삭제. 스크래치에서 `wfs_` 원문(47자) 검색 0건(텍스트·바이너리). 사용자 본인의 연결 프로그램(9/20 부터 도는 `workflow.connector run`, `~/Library/Application Support/workflow-connector/`)은 이 스택과 무관해 건드리지 않았다 |
| 비밀값 | 토큰 원문은 셸 변수 → 자격 증명 파일(0600, 즉시 삭제) → n8n 자격 증명 저장소(n8n 이 `/home/node/.n8n/config` 의 키로 암호화 — 볼륨 삭제로 소멸, 스크래치의 n8n DB 사본에는 암호문만 있고 키 파일은 복사하지 않았다)뿐. 이 문서·커밋·로그 출력에 원문 없음. Runloom DB 는 `token_sha256` 만 |
| 근거 | `../workflow-live-n8n-2026-09-22/` — `central/db.sqlite`(`chains`·`source_tokens`·`tasks`·`executions`), `logs/central_worker.log`(13:44:57,358 tick 줄)·`central_api.log`(`/sources/n8n/chains` 201), `n8nEventLog.log`(노드 이벤트), `n8n-database.sqlite(+wal)`(`execution_entity`·`execution_data`), `evidence-n8n-node-outputs.json`(HTTP Request·Wait 노드 출력 = `InboundChainResponse`·`ChainCallback`), `evidence-chain-page.html`, `wf.json`(포트·id 고친 사본). 커밋하지 않는다 |

### ARCHITECTURE "검증 순서" 7번

| 순서 | 검증 | 결과 | 근거 |
|---|---|---|---|
| 7 | n8n 입구·출구 — 실제 n8n | **통과** (n8n 2.39.10 Docker 1회, 에이전트는 대본). n8n Webhook → HTTP Request 가 입구 API 를 불러 체인이 생기고 A → B 가 사람 조작 없이 돌았으며, B `확인 필요 · 검토 대기` 시점에 워커 callback(2xx)이 Wait 노드를 깨워 Slack 노드까지 `success` 로 끝났다. B 승인 뒤 두 번째 callback 없음 | 위 표 |

### 발견한 결함과 고친 파일

- 제품 코드(`src/`) 결함: **없음**. 입구 API·워커 callback·허용 목록·화면 모두 e2e(step 8)와 같은 동작으로 실제 n8n 을 통과했다.
- **절차서 결함 1 (고침)** — [docs/n8n/README.md](n8n/README.md) 4절 CLI: `docker cp` 는 호스트 사용자(uid 501)·권한(0600) 그대로 옮기므로 `docker exec -u node … import:credentials` 가 `EACCES: permission denied, open '/tmp/…'` 로 실패한다. 두 `docker cp` 뒤에 `docker exec -u root runloom-n8n chown node:node /tmp/…` 줄을 추가했다.
- **절차서 결함 2 (고침)** — n8n 2.39.10 의 `import:workflow` 는 JSON 최상위 `id` 가 없으면 `SQLITE_CONSTRAINT: NOT NULL constraint failed: workflow_entity.id` 로 거부한다(`--help` 에 자동 생성 옵션 없음). [docs/n8n/runloom-handoff.json](n8n/runloom-handoff.json) 에 `"id": "runloomHandoff001"` 을 추가하고 README 의 `list:workflow`·`publish:workflow --id=` 예를 그 값으로 고쳤다. `tests/test_n8n_example.py` 14건은 최상위 키를 고정하지 않아 무변경 통과.
- 절차서 보강 — README 2절: 소유자 계정은 화면 import 에만 필요하고 CLI 경로는 없이 된다(이번 실행이 그렇다). 4절 CLI: 로컬 스택이면 `sed` 로 포트를 고친 사본을 `docker cp` 한다(전엔 화면 방식에만 적혀 있었다). 4절 끝에 2.39.10 확인 문장.
- **관찰 (제품, 미수정 — 사용자 결정)**: 중앙 워커의 httpx INFO 로그가 callback 요청 줄에 URL 전체를 찍는다 — `POST http://localhost:5678/webhook-waiting/1?signature=<64 hex> "HTTP/1.1 200 OK"`. n8n 의 `$execution.resumeUrl` 은 `signature` 쿼리로 그 실행을 깨우는 권한을 담고 있다(1회성 — 끝난 뒤엔 409). `callback_url` 은 AGENTS.md 의 비밀값 목록에 없고 DB `chains.callback_url` 에도 원문이 있어야 보낼 수 있으므로 규칙 위반은 아니지만, 화면(`callback · localhost:5678`)이 host 만 보이는 것과 달리 로그에는 남는다. 고치려면 `httpx` 로거 레벨 조정 또는 `callback_client` 에서 host 만 로그 — 사용자 결정 후.
- 메모(결함 아님): n8n 은 끝난 실행의 resume URL 에 404 가 아니라 **409** 를 준다(step 지시의 전제와 코드만 다름). 워커는 비-2xx 를 모두 `CallbackFailed` 로 다루므로 동작 차이 없음.
- 메모(결함 아님): `n8nEventLog.log` 의 시각 표기가 실행 도중 `+00:00` 에서 `-04:00` 으로 바뀐다(같은 순간을 다른 오프셋으로 찍은 것 — 위 표는 모두 UTC 로 환산).
- 메모(하네스, 미수정): `scripts/local_stack.py` 를 비대화형 셸에서 `nohup … &` 로 띄우면 SIGINT 가 무시돼 `Ctrl-C` 경로(`KeyboardInterrupt` → `stack.stop()`)가 없고, SIGTERM 은 부모만 죽여 자식 5개가 남는다 — 이번엔 자식 PID 를 직접 종료했다. 대화형 터미널에서 `Ctrl-C` 로 쓰는 원래 용법에는 영향 없다.
- 변경 파일: [docs/n8n/README.md](n8n/README.md), [docs/n8n/runloom-handoff.json](n8n/runloom-handoff.json)(`id` 1줄), 이 문서, [ARCHITECTURE](ARCHITECTURE.md) "검증 순서" 7번, [CURRENT_HANDOFF](CURRENT_HANDOFF.md) "지금 상태" phase 7 행.

## 2026-09-23 — GitHub 업무 순환 대역 e2e (phase 8 step 14 작성, step 15 재실행)

목적: [ADR-0014](adr/0014-github-task-cycle.md)의 수집 → `bug_fix` → 판정 → `code_review` 연결·생성 → 재작업·사람 요청·응답 후 재개 → 원본 댓글 반영을 프로세스 경계를 넘어 끝까지 돌린다. **실제 외부 도구 호출은 없다** — 이 절은 대역 검증 기록이며 실연동(step 16)을 대신하지 않는다.

| 항목 | 값 |
|---|---|
| 명령·결과 | `WORKFLOW_E2E=1 python3 -m pytest tests/e2e -q` — **49 passed** 148.69초(2026-09-23 step 15 실행, `tests/e2e/test_github_cycle.py` 13 + 기존 대본·n8n 36). `python3 -m pytest -q` 2164 passed·49 skipped, `python3 -m ruff check .` 통과 |
| 실제 제품 코드 | uvicorn 중앙 API, `HttpGitHubClient`(transport 만 가짜 서버로), 테스트 프로세스 안 `Worker`(재시작 = 새 인스턴스), 하위 프로세스 `workflow.connector connect/register/run`, 임시 Git 저장소 2개, 검증 프로필의 실제 `pytest` |
| 대역 | GitHub = 127.0.0.1 `ThreadingHTTPServer`(이슈 목록 since·2개 단위 페이지·Link·ETag/304·PR 항목·댓글 생성/조회/수정, 5xx·POST 응답 유실·오래된 스냅샷 주입). 수정·검토 도구 = PATH 가짜 `codex`(시나리오 표대로 재현 테스트·수정 커밋, 검토는 결과 커밋 체크아웃의 `TODO(review)` 로 `changes_requested`/`approved`). 실제 GitHub·Codex·Claude·모델 호출 없음, 비용 0 |
| 확인한 것 | 범위 이슈 4건만 접수(백로그·PR·다른 라벨 제외) → A·B 서로 다른 등록에서 같은 tick 착수 → D 위임 밖·E 담당 2명 사람 요청 1건씩 → G 같은 등록 잠금 대기 → 웹 등록 기존 검토 C 에 A 결과 연결(워커 재시작 뒤에도 1회) → C `changes_requested` → A 재작업 1회(base = 이전 결과 커밋) → 재검토 `approved`, A `확인 필요 · 검토 승인 — 병합·이슈 종료는 사람`, 기준 브랜치 불변 → B 결과로 새 검토 F 생성·승인 → G 상한 0 `rework_limit_reached` → E `choose_agent` 응답(재전송 멱등·`response_conflict`·`stale_request`) 후 r2 착수 → D 는 응답만으로 미착수·Agent 범위 변경 후 착수 → 재시작 tick 에도 Task·실행 수 불변, 이슈마다 marker 댓글 1개, GitHub 쓰기는 댓글 POST/PATCH 뿐, 토큰이 DB·산출물·로그·댓글에 없음. B 의 POST 응답 유실은 marker 조회로 `delivered`(재POST 없음) |
| 확인하지 않은 것 | 실제 `api.github.com`·fine-grained PAT 권한·rate limit 헤더·댓글 목록 페이지네이션, 실제 Codex/Claude 의 `bug_fix`·`code_review` 동작, 브라우저의 `data-json-action` 스크립트 — [GitHub 런북](github/README.md) 10절 |

### 발견한 결함과 고친 파일

- step 15 재실행에서 새 결함 없음. step 14 에서 기존 `tests/e2e/test_scenario.py` test_22~28 의 기대값을 내장 4종·규칙 2개로 갱신했다(제품 소스 무변경).

## 2026-09-23 — 실제 GitHub·실제 Claude 로 업무 순환 1회 (phase 8 step 16)

목적: [ADR-0014](adr/0014-github-task-cycle.md) 의 GitHub 업무 순환을 대역이 아닌 **실제 `api.github.com` + 실제 `claude -p`** 로 1회 돌린다 — 지정한 이슈만 접수해 담당 Agent 가 재현 테스트를 먼저 쓰고 고치는지, 검토 Agent 가 결과 커밋을 읽고 스키마대로 답하는지, 원본 이슈에 댓글 하나가 만들어지고 갱신되는지. 절차는 [GitHub 런북](github/README.md) 의 실연동 체크리스트를 따랐다. 무엇이 실제이고 무엇이 대본인지: **실제** — GitHub REST(이슈 수집·댓글 POST/PATCH), 수정 2건·검토 2건 모두 실제 `claude -p`(대본 에이전트·가짜 실행 파일 없음), 검증 프로필의 실제 `pytest`, 실제 Git 커밋. **대본** — 없음. 진단 데모·n8n·공개 데모 VM 은 이 검증에 쓰지 않았고 `main` 은 건드리지 않았다. 제품 코드(`src/`)·테스트는 한 줄도 바꾸지 않았다.

| 항목 | 값 |
|---|---|
| 실행일 | 2026-09-23. 준비(저장소·이슈·스택·연결) 09:0x~09:35, **순환 본체 09:35:58 → 09:37:57 KST (약 2분)** |
| 도구 | `claude` **2.1.280 (Claude Code)** — 수정·검토 모두. Codex 는 쓰지 않았다(사용량 소진 — 수정·검토 모두 Claude 로 두는 것은 사용자 결정) |
| 테스트 저장소 | `jeongeundev/runloom-live-test` (**비공개**, 이 검증용으로 새로 만듦). 작은 `billing` 패키지 — `billing/invoice.py`·`billing/period.py` + `tests/` 9개, base 커밋 `c204e0db8005` 에서 `python3 -m pytest -q` **9 passed**. 버그 2개를 심고 그것을 드러내는 테스트는 넣지 않았다(재현 테스트 작성은 Agent 의 몫이므로) |
| 이슈 | `#1` `line_total` 이 할인 적용 후 수량을 곱하기 전에 버림(333×3×10% → 897, 899 여야 함), `#2` `billing_days` 가 종료일을 빠뜨림(1/1~1/31 → 30, 31 이어야 함). 둘 다 assignee `jeongeundev`. 라벨 대신 `selected_issue_numbers [1,2]` 로 명시 선택 |
| 토큰 | fine-grained PAT, Repository access = 그 저장소만, **Issues: Read and write · Metadata: Read-only** 만. 값은 `~/.runloom-live.env`(0600) → 서버·워커 프로세스 환경변수에만. 사전 확인: 저장소 조회 200(`private=true`), 이슈 목록 200, 댓글 POST 201 → 그 댓글 DELETE 204(권한 확인용 임시 댓글, 즉시 삭제해 검증 시작 시점 댓글 0개). `gh` CLI 의 OAuth 토큰(`repo` scope)은 권한이 넓어 제품에 주지 않았다 |
| 스택 | 저장소 밖 `../runloom-live-state/`(`live.env` 0600, `db.sqlite`, `artifacts/`, `connector/`). 중앙 `uvicorn workflow.server.app:app 127.0.0.1:8000` + `python3 -m workflow.server.worker` + `python3 -m workflow.connector run --adapter claude`. `WORKFLOW_GITHUB_REPOS=jeongeundev/runloom-live-test` 하나만, `WORKFLOW_PUBLIC_URL=http://127.0.0.1:8000`. **DB 는 새로 만들어져 `schema_version` = 5**(v4 데이터 보존 경로는 이번에 타지 않았다 — 기존 v4 DB 승격은 미검증) |
| 등록·설정 | Agent `fix-billing`(`code.fix billing`, 로컬 등록 `local-billing-fix`)·`review-billing`(`code.review billing`, `local-billing-review`) — 둘 다 같은 연결 프로그램·**같은 로컬 클론** `../runloom-live-test`, 도구 `claude`. 검증 프로필은 수정 등록에만 `vp-pytest=python3 -m pytest -q`. 첫 claim 이 `supported_kinds ["code_change","bug_fix","code_review"]` 를 선언(v5 신버전). 소스 `ghs-0e68cffe`(`run_mode auto`, `max_rework_rounds 1`), 담당 연결 `PUT …/assignees/284910647` `{jeongeundev, fix-billing}` 200 — 숫자 ID 는 공개 API `users/jeongeundev` 의 `id` |
| 수집 | 워커 첫 tick(복구 스캔) `sources_synced 1 · issues_created 2 · sync_errors 0 · tasks_started 1 · deliveries_queued 1 · deliveries_sent 1`. 이슈 `#1 → task-80e9ab2f3c59`, `#2 → task-3df94f548d78`(둘 다 `bug_fix`). **지정 범위 밖 접수 없음**(저장소에 다른 이슈·PR 이 없어 배제 경로는 관찰 대상이 아니었다) |
| 순차 착수 | 같은 로컬 등록이라 하나씩 — `#2` 착수, `#1` 은 `대기 · 같은 저장소에서 다른 수정 실행 중`(`repository_busy`). 예상된 동작이며 병렬 착수는 이번 구성에서 관찰할 수 없다 |
| 수정 `#2` | `exec-fde64c5764121884` try1, 09:35:58.39 → 09:36:23.31(**24.9초**). 결과 `ready_for_review`, 커밋 `aa9dd403bd93`(브랜치 `task/task-3df94f548d78`, base `c204e0db8005`). `(end - start).days` → `+ 1` 한 줄, 재현 테스트 2개 선작성. 판정 09:36:25.48 **passed** — `test_before_failed`(수정 전 exit_code=1: 새 테스트 2개만 실패, 기존 9개 통과), `verification_passed`(`vp-pytest` exit 0 @ `aa9dd40`, 11 passed), `required_artifacts`(diff·test_log_before·test_log_after·verification_log) |
| 검토 `#2` | 후속 `code_review` `task-b93926f57374` 자동 생성(`followup_links.cause_execution_id = exec-fde64c5764121884`) → `exec-1a1229ca82b2b148` 09:36:28.43 → 09:36:46.45(**18.0초**) → `code_review_result` **`approved`**, 차단 지적 0건. 판정 `source_matches`·`commit_matches aa9dd40` 통과. 검토 본문이 실제로 커밋을 읽은 내용 — `billing/period.py:10` 의 새 반환식, docstring 과의 일치, `end < start` 거절 유지, 기존 테스트 약화 없음, 저장소 안 호출자(테스트·`__init__` 재수출)뿐이라 영향 없음 |
| 수정 `#1` | `exec-67341614c35a0e23` try1, 09:36:51.55 → 09:37:30.65(**39.1초**). 커밋 `95b642dac4db`. `int(단가×할인)×수량` → `(단가 × 수량 × (100-할인) + 50) // 100` — 정수 연산으로 한 번만 반올림해 float 오차까지 피했다. 재현 테스트 3개 선작성(897/29900/13 → 899/29970/14). 판정 09:37:31.74 passed(`test_before_failed` exit 1: 새 3개만 실패, `verification_passed` exit 0 @ `95b642d`, 12 passed) |
| 검토 `#1` | `task-31ffedc675b4` 자동 생성 → `exec-e5b872f8d56057fd` 09:37:35.75 → 09:37:54.25(**18.5초**) → **`approved`**. 검토 본문에 새 공식의 기대값 재계산(`89910+50→899`, `2997000+50→29970`, `1350+50→14`)과 기존 5개 테스트가 새 공식에서도 통과함(3000·1600·0·ValueError·3000)을 직접 확인한 내용 |
| 원본 반영 | **이슈마다 댓글 정확히 1개.** `#2` id `5786944577` created 00:35:58Z → updated 00:36:48Z, `#1` id `5786949941` created 00:36:26Z → updated 00:37:56Z — `body_revision` 1 → 2 → 3 이 모두 같은 댓글 **PATCH** 로 반영(`source_deliveries` 6행 전부 `delivered`, `attempts 1`, `last_error` 없음). 첫 줄 marker `<!-- runloom:task=… -->`, 기준 커밋 → 결과 커밋 SHA, 후속 `code_review` 상태, 검토 결과 요약, `_Runloom 은 PR 생성·푸시·병합·이슈 종료를 자동으로 하지 않습니다._`, 운영자 전용 상세 링크. GitHub 쓰기는 댓글 POST 2회 + PATCH 4회뿐 |
| 끝 상태 | 수정 Task 둘 다 `확인 필요 · 검토 승인 — 병합·이슈 종료는 사람`, 검토 Task 둘 다 `완료 · 검토 승인`. **이슈 둘 다 `open`**(자동 종료 없음), 기준 브랜치 `main` = `c204e0db8005` 그대로, 결과 커밋은 로컬 `task/<id>` 브랜치 2개에만 — `git ls-remote --heads origin` 에 `main` 만(**push·PR·merge 없음**). 사람 조작은 0회(승인·재개·응답 없이 자동으로 여기까지) |
| 독립 검증 (제품 판정과 별개로 직접 확인) | 각 결과 커밋을 별도 worktree 에 체크아웃해 `python3 -m pytest -q` → **11 passed · 12 passed**. base 커밋에 Agent 가 쓴 테스트만 얹어 실행 → `#2` 의 2개·`#1` 의 3개가 **실패**하고 기존 9개는 통과 → 재현 테스트가 실제로 그 버그를 겨냥했음이 확인됨(제품의 `test_before_failed` 와 일치). 두 수정의 코드 변경 내용도 직접 읽어 버그가 실제로 고쳐졌음을 확인 |
| 비용 | `claude.jsonl` 의 `total_cost_usd` — 수정 `#2` **$0.3403**, 검토 `#2` **$0.3207**, 수정 `#1` **$0.3208**, 검토 `#1` **$0.2064** → **합계 약 $1.19**(CLI 보고값. 구독 사용량으로 실제 청구액과 다를 수 있어 "확인된 값" 은 CLI 보고치까지다). 캐시 읽기가 큰 비중(예 수정 `#1` `cache_read_input_tokens` 326,960) |
| 미관찰 (이번 실연동에서 확인하지 못한 것) | **재작업 경로** — 검토 둘 다 `approved` 라서 `changes_requested` → 재작업 → 재검토는 대역 e2e 에서만 확인됐다(`max_rework_rounds 1` 은 설정만 됨). 담당자 없음/복수, 위임 밖, 사람 요청·응답 후 재개, 이슈 편집·닫힘, rate limit·secondary rate limit, 댓글 목록 페이지네이션(댓글 1개뿐), POST 응답 유실 조정, v4 → v5 데이터 보존 마이그레이션, 비 Python 저장소의 재현 테스트 인식, 브라우저의 `data-json-action` 스크립트 |
| 화면 | 서버 렌더 확인만 — `/operator/github` 에 `토큰 설정됨`·저장소·`확인 필요`·`반영됨`, 업무 상세에 `검토 승인 — 병합·이슈 종료는 사람`·기준/결과 커밋 SHA·`반영됨`. 브라우저로 열어 보지는 않았다 |
| 비밀값 | PAT 원문은 `~/.runloom-live.env`(0600)와 프로세스 환경변수에만. 검사: 토큰 문자열이 `db.sqlite`·`artifacts/`(17파일)·`connector/`·테스트 저장소·중앙 서버 로그·워커 로그·연결 프로그램 로그에 **0건**(텍스트·바이너리). 산출물에 `GITHUB_TOKEN`·`OPERATOR_TOKEN`·`SESSION_SECRET` 이라는 **키 이름조차 없음**. GitHub 댓글 본문에도 없음. 이 문서·커밋에도 원문 없음 |
| 근거 | `../runloom-live-state/` — `db.sqlite`(`github_sources`·`github_assignee_bindings`·`source_issues`·`tasks`·`executions`·`task_verdicts`·`followup_links`·`source_deliveries`), `artifacts/`(diff·test_log_before/after·verification_log·claude.jsonl·code_change_result·code_review_result·handoff_bundle). `../runloom-live-test/` — `task/task-3df94f548d78`(`aa9dd40`)·`task/task-80e9ab2f3c59`(`95b642d`). GitHub 이슈 `#1`·`#2` 의 댓글 각 1개. 모두 커밋하지 않는다 |

### ARCHITECTURE "검증 순서" 8번

| 순서 | 검증 | 결과 | 근거 |
|---|---|---|---|
| 8 | GitHub 업무 순환 — 실제 GitHub·실제 Claude | **통과(단, 재작업 경로는 미관찰)**. 지정한 이슈 2건만 `bug_fix` 로 접수되어 담당 Agent 가 재현 테스트를 먼저 쓰고 고쳤고(base 에서만 실패함을 독립 확인), 판정 통과 결과마다 `code_review` 가 자동 생성되어 실제 Claude 가 결과 커밋을 읽고 `approved` 를 스키마대로 제출했으며, 이슈마다 댓글 하나가 만들어져 PATCH 로 갱신됐다. push·PR·merge·이슈 종료 없음, 사람 조작 0회, 토큰 비노출 | 위 표 |

### 발견한 결함과 고친 파일

- 제품 코드(`src/`) 결함: **없음**. 수집·준비 판정·착수·판정·후속 생성·검토·댓글 outbox·화면이 대역 e2e(step 14)와 같은 동작으로 실제 GitHub·실제 Claude 를 통과했다. 고친 파일도 없다.
- **검증 절차 실수 (내 쪽, 복원함)** — base 커밋에 재현 테스트만 얹어 실행하려고 `git --work-tree=<별도 경로> --git-dir=<등록 폴더>/.git checkout <task 브랜치> -- tests/` 를 썼는데, 이 조합은 **등록 폴더의 인덱스를 공유**해서 `tests/test_invoice.py` 가 `MM` 로 남았다. 등록 폴더는 깨끗해야 하므로(`worktree_dirty`) `git reset HEAD && git checkout -- .` 로 복원했다(이후 `main` = `c204e0db8005`, base 와 diff 없음, `task/*` 브랜치 보존). 제품과 무관한 조작 실수이며, 다음에는 `git worktree add` 한 경로에서 그 worktree 의 `git` 으로만 체크아웃한다.
- 메모(결함 아님): `test_log_after` 와 `verification_log` 의 sha256 이 같다(`cf0efb98…`, 111바이트). Agent 가 돌린 `pytest` 와 제품이 `vp-pytest` 로 돌린 `pytest` 의 출력이 같아서 내용 주소 저장이 한 파일을 가리키는 것이고, 검증이 생략된 것이 아니다(판정의 `verification_passed` 는 별도 실행 결과).
- 메모(결함 아님): 이슈가 2건뿐이고 둘 다 같은 로컬 등록이라 `repository_busy` 로 순차 실행됐다. 서로 다른 등록의 병렬 착수는 대역 e2e 에만 있다.

## 2026-09-27 — 측정·기준선 대역 e2e (phase 9 step 10)

목적: [ADR-0015](adr/0015-measurement-events-and-baseline.md)의 이벤트 보충·설정 번호·러너 폴더 커밋·CLI 보고 비용이 실제 순환에서 쌓이고, `/metrics.json`·`/metrics` 화면·기준선 가져오기가 그 기록으로 계산되는지 프로세스 경계를 넘어 확인한다. **실제 외부 도구 호출은 없다** — 이 절은 대역 검증 기록이며 실연동이 아니다. 실제 OpenArchive 기준선은 가져오지 않았다.

| 항목 | 값 |
|---|---|
| 명령·결과 | `WORKFLOW_E2E=1 python3 -m pytest tests/e2e -q` — **53 passed** 150.39초(`tests/e2e/test_metrics.py` 4 + 기존 49). `python3 -m pytest -q` 2337 passed·53 skipped, `python3 -m ruff check .` 통과 |
| 실제 제품 코드 | 중앙 API(`create_app`, 이 테스트 프로세스 안 uvicorn 스레드 — 기준선 클라이언트의 transport 만 가짜 GitHub 로), `HttpGitHubClient`(REST 수집·GraphQL 기준선), 테스트 프로세스 안 `Worker`, 하위 프로세스 `workflow.connector connect/register/run --adapter auto`(등록의 tool 로 claude·codex 선택), 임시 Git 저장소 1개, 검증 프로필의 실제 `pytest` |
| 대역 | GitHub = `test_github_cycle` 의 127.0.0.1 가짜 서버 + `POST /graphql`(`repository.issues`, 2건 단위 커서 페이지). 수정 도구 = PATH 가짜 `claude`(결과 JSON 에 `total_cost_usd` 0.25·재작업 0.35, 토큰 1000/200), 검토 도구 = 가짜 `codex`(비용·토큰 보고 없음). 비용 값은 가짜 도구가 만든 숫자다 — 실제 청구액 아님, 비용 0 |
| 확인한 것 | 이슈 1건 → `bug_fix`(claude) → 후속 `code_review`(codex) `changes_requested` → 재작업 1회 → 재검토 `approved` → 운영자 검토 승인으로 `완료`(bug_fix 는 `merge_confirmed_at` 경로가 없어 `status_changed → 완료` 로 잼). `/metrics.json`: 묶음 1, 재작업 합 1, 1회 통과 0/1, 인계 대기 n 1 이상, 접수→완료 n 1(`finished_at` 대체 0), 비용 합 0.60·n 2·모름 2(0 이 아님), 입력 토큰 합 2000·모름 2, 재실행 2/4, 개입 = 사람 요청 수 + 승인 1. 실행 4건 모두 `config_revision` = 세션 설정 번호(>1), 후속 링크 `rules_revision` 같음, `folder_commit` = 등록 폴더 HEAD(기준 커밋) — `group_by=config_revision`·`folder_commit` 그룹 키가 그 값 하나. 기준선 가져오기 2회(멱등) 각 2건: PR 없음·미병합 PR·연결 이후 이슈 제외, 두 PR 중 이른 병합 사용 → 화면 비교 절에 `n 2`·중앙값 4시간·기준선 주석, CSV `baseline:<source_id>` 행. GraphQL 요청은 모두 토큰 헤더, 토큰이 화면·CSV·응답에 없음 |
| 확인하지 않은 것 | 실제 `api.github.com` GraphQL(`closedByPullRequestsReferences` 실제 응답·권한·rate limit), 실제 Claude CLI 의 `total_cost_usd`·토큰 값, 실제 Codex 토큰 키, 브라우저의 기준선 가져오기 버튼 스크립트, 기간(from/to) 필터의 e2e |

### 발견한 결함과 고친 파일

- 고친 파일 없음(이 step 은 e2e·문서만). 발견: 재작업 상한 1 에서 재작업 착수 뒤 재작업 결과 판정 전 tick 이 첫 검토(`changes_requested`)를 다시 평가해 `rework_limit_reached` 사람 요청을 하나 더 만든다(`domain/task_followup.py` `_after_review` 가 `rework:{검토 실행}` 착수 여부를 보지 않음). e2e 는 요청 수를 DB 그대로 세어 지표와 맞춘다. 수정은 [CURRENT_HANDOFF](CURRENT_HANDOFF.md) 에 남겼다.

## 2026-09-27 — 셀프호스트 실제 Docker 설치·보존·백업 복원 (phase 10 step 8)

목적: [ADR-0016](adr/0016-selfhost-docker-fixed-workspace.md)의 한 명령 설치·재시작 뒤 데이터 보존·백업 복원이 실제 Docker 에서 되는지 확인한다. 외부 호출·비용 없음 — 네트워크는 127.0.0.1 뿐이다.

| 항목 | 값 |
|---|---|
| 실행일 | 2026-09-27 KST, 이 Mac(Darwin 25.6.0) |
| Docker | Docker Desktop 서버 24.0.2, Docker Compose v2.19.1 (Docker Desktop 이 꺼져 있어 `open -a Docker` 로 켠 뒤 실행) |
| 명령·결과 | `WORKFLOW_DOCKER=1 python3 -m pytest tests/e2e/test_selfhost.py -q` — **1 passed** 72.62초(이미지 캐시 있는 상태. 첫 실행은 로그인 호출 방식 테스트 오류로 실패 — 설치·healthz 는 36초 안에 통과, 제품 코드 수정 없음). `python3 -m pytest -q` 2464 passed·55 skipped, `ruff` 통과, `WORKFLOW_E2E=1 python3 -m pytest tests/e2e -q` 54 passed·1 skipped(셀프호스트 e2e 는 `WORKFLOW_DOCKER` 게이트) |
| 격리 | 커밋된 HEAD 를 임시 디렉터리에 `git clone --no-local` → 그 복사본의 `deploy/selfhost/install.sh` 를 `RUNLOOM_PROJECT=runloom-e2e-<랜덤 8자리>`·`WORKFLOW_PORT=<빈 포트>` 로 실행. 끝나면 finally 에서 그 프로젝트만 `down -v --remove-orphans`. 실행 뒤 `runloom-e2e` 이름의 컨테이너·볼륨 0개 확인. 이미지 `workflow-selfhost:local` 은 남긴다(지우지 않음) |
| 확인한 것 | 1) install.sh → `.env` 0600·`OPERATOR_TOKEN` 64자 생성, 출력에 토큰 값 없음, `/healthz` `{status: ok, mode: selfhost}`, `/login`(httpx 폼) 303 → `/` 가 `/tasks` 로, `/kinds` 에 종류 `selfhost_before_restart` 등록. 2) `docker compose down`(볼륨 유지) → install.sh 재실행 → 새 로그인에서 같은 종류가 보임. 3) `exec -T central … backup create`·`list`(첫 줄 이름 = create 출력의 이름) → 종류 `selfhost_after_backup` 추가 → `stop central worker` → `run --rm -T central … restore <이름> --force` → `up -d` → healthz ok → `selfhost_before_restart` 있음·`selfhost_after_backup` 없음, `list` 에 `pre-restore-…` 백업 |
| 확인하지 않은 것 | 러너 `install-runner.sh` 의 실제 launchd 적재·호스트 러너 연결, 브라우저 화면 로그인, 이미지 없는 첫 빌드 소요 시간(pip 설치 포함), `cp` 로 호스트 반출, 업그레이드 시 스키마 버전이 바뀌는 경우 |

### 발견한 결함과 고친 파일

- 제품·배포 파일 수정 없음. 추가 파일: `tests/e2e/test_selfhost.py`. 문서: [SELFHOST](SELFHOST.md) 상태 줄.

## 2026-09-27 — GitHub App 버튼 연결 대역 e2e (phase 11 step 9)

목적: [ADR-0017](adr/0017-github-app-connection.md)의 [GitHub 연결] → App 만들기 → 설치 → 소스 자동 생성 → 열린 이슈 전부 가져오기 → 러너 원격 자동 매칭 → 지시 실행([맡기기]·트리거 라벨)이 프로세스 경계를 넘어 이어지는지 확인한다. **실제 외부 호출은 없다** — 이 절은 대역 검증 기록이며 실연동이 아니다. github.com 의 App 만들기·설치 화면은 밟지 않았고 테스트가 그 뒤의 callback·setup 을 직접 불렀다.

| 항목 | 값 |
|---|---|
| 명령·결과 | `WORKFLOW_E2E=1 python3 -m pytest tests/e2e -q` — **60 passed·1 skipped** 164초(`tests/e2e/test_github_app.py` 6 + 기존 54, skip 은 `WORKFLOW_DOCKER` 게이트의 셀프호스트 e2e). `python3 -m pytest -q` 2685 passed·61 skipped, `python3 -m ruff check .` 통과 |
| 실제 제품 코드 | 중앙 API(`create_app`, 이 테스트 프로세스 안 uvicorn 스레드 — `app.state.github_transport` 만 가짜 GitHub 로), `exchange_manifest_code`·`save_credentials`·`GitHubAppAuth`(PyJWT RS256)·`SecretStore`, `github_connect.sync_installation_sources`, 테스트 프로세스 안 `Worker` + `SourceClients`(설치 토큰 공급자), 하위 프로세스 `workflow.connector connect/register/run --adapter codex`, 임시 Git 저장소 1개(`origin` = `git@github.com:acme/billing.git`), 검증 프로필의 실제 `pytest` |
| 대역 | GitHub = `test_github_cycle` 의 127.0.0.1 가짜 서버 + App 경로: `POST /app-manifests/{code}/conversions`(테스트 안에서 만든 RSA 2048 개인 키를 pem 으로), `GET /app/installations/{id}`·`POST …/access_tokens`(App JWT 를 공개 키로 검증, `iss` = client ID), `GET /installation/repositories`. 저장소·이슈·댓글 경로는 설치 토큰만 받는다. 도구 = PATH 가짜 `codex`(도구 환경에 설치 토큰이 있으면 실패). 키 파일은 저장소에 없다 |
| 확인한 것 | 1) 연결 전 화면은 [GitHub 연결] 링크뿐, 카드 없음. `/operator/github/app/new` → manifest 폼(`redirect_url`·`setup_url` = `WORKFLOW_PUBLIC_URL` 기준, 웹훅 비활성, 권한 issues write·pull_requests read·metadata read) → callback(state 일치) → 설치 URL 로 303 + 새 state, 같은 state 재사용 403 → setup(`setup_action=install`) → `/operator/github` 303. 설치 확인은 JWT, 저장소 목록은 설치 토큰. 소스 1개 `all_open`·`runloom`·`installation_id`·`auto`, 세 ID 는 `null`. 2) 비밀 디렉터리 0700, 파일 4개(App 정보·개인 키·client secret·webhook secret) 0600, PAT 파일 없음. 3) 첫 수집: 열린 이슈 3건(연결 전 2025년 백로그 포함) → Task, PR·닫힌 이슈 제외, 착수 0·사람 요청 0, 상태 사유 "실행 지시 전", `/tasks` 에 "지시 전"·[에이전트에게 맡기기]. 4) 러너 register 뒤 `discovered.found.github_repository` = `acme/billing`(URL 원문 없음), 카드에 로컬 저장소 `billing`·수정 Agent·`vp-pytest`·검토 Agent 모두 `(자동)`. 지시 전이라 tick 2회에도 착수 0. 5) #1 [맡기기] → `delegated_by=operator`(다시 눌러도 실행 1개), start_key `auto:{task}:r{revision}` → 수정(판정 통과) → 후속 `code_review` 자동 생성·자동 매칭된 검토 Agent 가 `approved` → 수정 Task "검토 승인 — 병합·이슈 종료는 사람" → 운영자 승인 `완료`, 이슈 댓글 1개(marker). 6) #2 에 `Runloom` 라벨(대소문자 다름) → 다음 수집에서 `delegated_by=label`·자동 착수 → 검토 승인. #3 은 끝까지 착수 없음. 기준 브랜치 그대로. 7) 개인 키 본문·client secret·webhook secret·설치 토큰이 비밀 디렉터리 밖 파일(DB·WAL·산출물·중앙/워커 로그 파일·연결 프로그램 상태·로그·저장소)·댓글·화면(`/operator/github`·`/tasks`·`/github/sources`)에 없음. 실패 실행 0 |
| 확인하지 않은 것 | 실제 github.com App 만들기 화면(manifest 폼 수락·이름 중복 처리·조직 경로), 실제 설치 화면과 `setup_action` 값·설치 URL `state` 복귀, 127.0.0.1 비활성 웹훅 URL 수락, 실제 `api.github.com` 의 JWT·설치 토큰 발급·만료 갱신·rate limit, 브라우저 자동 제출 스크립트, 붙여 넣은 PAT 경로·App 설치 변경(저장소 제거) 경로의 e2e(단위·서버 테스트에만 있음), 재작업 경로(이 e2e 의 두 이슈는 첫 검토 승인) |

### 발견한 결함과 고친 파일

- 제품 코드 수정 없음. 추가 파일: `tests/e2e/test_github_app.py`. 문서: [SELFHOST](SELFHOST.md) GitHub 절(버튼 흐름·토큰은 고급), [GitHub 런북](github/README.md) 0절·10절, [CURRENT_HANDOFF](CURRENT_HANDOFF.md).

## 2026-09-28 — 실제 저장소 순환 대역 e2e (phase 12 step 10)

목적: [ADR-0018](adr/0018-real-repo-cycle.md)의 [러너 붙이기] 한 명령 → 기본 브랜치 최신 기준 커밋 → worktree 링크·환경변수 → 결과 브랜치 push → 초안 PR → 병합 추적 → 알림이 프로세스 경계를 넘어 한 줄기로 이어지는지 확인한다. **실제 외부 호출은 없다** — 이 절은 대역 검증 기록이며 실연동이 아니다(github.com·api.github.com·Discord 호출 없음, 네트워크는 127.0.0.1 과 로컬 파일 경로 git 뿐).

| 항목 | 값 |
|---|---|
| 명령·결과 | `WORKFLOW_E2E=1 python3 -m pytest tests/e2e/test_real_repo.py -q` — **7 passed** 약 7초. `WORKFLOW_E2E=1 python3 -m pytest tests/e2e -q` — **67 passed·1 skipped** 약 164초(`test_real_repo.py` 7 + 기존 60, skip 은 `WORKFLOW_DOCKER` 게이트). `python3 -m pytest -q` 2918 passed·68 skipped, `python3 -m ruff check .` 통과 |
| 실제 제품 코드 | 중앙 API(`create_app`, selfhost 모드, 이 테스트 프로세스 안 uvicorn 스레드 — `app.state.github_transport` 만 가짜 GitHub 로), 테스트 프로세스 안 `Worker`(`SourceClients` 설치 토큰·`SecretStore` 알림 URL·`NotifySender` — `worker.main` 과 같은 연결), 하위 프로세스 `workflow.connector setup`·`run --adapter codex`, 실제 `git fetch`·`git push`, 검증 프로필의 실제 `pytest` |
| 대역 | GitHub = `test_github_app` 의 가짜 App + PR 경로(`GET /repos/{r}` 의 `default_branch`, `GET·POST /repos/{r}/pulls`, `GET /repos/{r}/pulls/{n}` — head 브랜치가 bare 에 없으면 422). origin = 임시 bare 저장소: 원본 폴더의 `remote.origin.url` 은 `git@github.com:acme/billing.git`(러너가 owner/name 을 읽음), 저장소 로컬 설정 `url.<bare>.insteadOf` 가 fetch·push 를 bare 로 보낸다. 알림 = 127.0.0.1 수신 서버(경로에 비밀 조각). 도구 = PATH 가짜 `codex`. 러너는 `BASE_FETCH_INTERVAL_SECONDS` 만 0.5초로 줄인 런처(`python -c`)로 띄웠다(제품 기본 60초). 병합은 테스트가 가짜 GitHub 객체에서 했다(사람 몫) |
| 확인한 것 | 1) `/login` → [GitHub 연결] callback·setup → 소스 1개, manifest 권한 `pull_requests: write`. 알림 URL 저장 → 화면 `설정됨`, URL 경로 없음. 첫 수집 이슈 2건 지시 전. 2) 카드 [러너 붙이기] → 응답 화면의 명령 `install-runner.sh --server <중앙> --code <코드> --repo <…>` 에서 서버·코드를 읽어 `connector setup --repo 원본 --tool codex --verify check=… --link deps --env CHECK_DB=…` → 요약 줄 `GitHub acme/billing · 링크 1개 · 환경변수 CHECK_DB`(값 없음), Agent 이름 `billing` 자동 생성, 러너 run 뒤 카드에 저장소·Agent·`check` 가 `(자동)`. 3) 다른 클론에서 bare `main` 에 새 커밋 push → 러너 fetch 보고로 `agents.base_commit` = 새 커밋(원본 폴더 HEAD 는 그대로 뒤처짐). 4) #1 [맡기기] → 수정 요청 `base_commit` = 새 커밋, 검증 `check` exit 0 — 저장소의 `tests/test_prepared.py` 가 링크된 `deps/marker.txt`(원본 폴더에만, git 무시)와 `CHECK_DB`(해시 비교) 를 보고 통과, 수정 전 로그는 재현 테스트만 실패. `executions.branch_pushed = 1`, bare 에 `task/<id>` = 결과 커밋(부모 = 새 커밋), bare·원본의 `main` 불변. 5) 검토 승인 → 초안 PR #101(`draft`, head `task/<id>`, base `main`, 제목 = 이슈 제목, 본문 첫 줄 `Fixes #1`·marker), `task_pull_requests.state = open`, 수정 Task `확인 필요 · … PR 확인 — #101`, 알림 `[Runloom] PR 확인 — …` 1건(`pr_url`·`task_url`), 원본 이슈 댓글이 `task/<id>` 를 가리킴, 업무 상세에 PR 줄. tick 을 더 돌려도 PR·알림 1건. 6) 가짜 GitHub 에서 병합 → 다음 tick 에 수정 Task `완료 · PR 병합`, 원본 이슈 `merged_pr_number = 101`, `/metrics.json` 의 이슈 열림 → 병합 n 합 1. 7) #2 는 가짜 도구가 직접 커밋 → 러너 `commit_mismatch` 실패 → Task `실패`, 알림 `[Runloom] 실패 — …` 1건. 수신한 알림은 `pr_opened`·`task_failed` 두 건뿐, `notifications` 두 행 모두 `sent`. 8) 연결 코드·러너 연결 토큰·`CHECK_DB` 값·알림 URL 경로 조각·설치 토큰·App 개인 키·client secret 이 중앙 DB 덤프(`connect_codes` 행 제외 — 아래)·중앙 산출물·로그 파일(중앙·러너)·화면 7개·PR 본문·알림 본문에 없음 |
| 확인하지 않은 것 | 실제 github.com 의 초안 PR 생성·초안 미지원 422·권한 올리기 화면·병합 뒤 이슈 자동 닫힘(`Fixes #N`), 실제 Discord 전송·429, launchd 로 띄운 러너의 git 자격(ssh·https), `install-runner.sh` 실제 실행(명령 문자열에서 서버·코드만 읽어 setup 을 직접 돌림), 실제 `claude`·`codex`, 재작업 경로(이 e2e 의 #1 은 첫 검토 승인), 비 Python 저장소(OpenArchive `scripts/check.sh`) |

### 발견한 결함과 고친 파일

- **결과 봉투의 `task_id` 가 `task-***` 로 가려짐 (step 4 회귀)** — 러너가 결과 봉투에 `mask_secrets` 를 적용하는데(step 4), `sk-[A-Za-z0-9_-]{8,}` 패턴이 서버 발급 `task-<12 hex>` 안의 `sk-<hex>` 에 걸려 `task_id` 가 바뀌었다. 판정 `result_ids_match` 가 실패해 **모든 수정 결과가 "결과 판정 실패"** 로 멈췄다(단위 테스트는 짧은 ID 를 써서 못 잡았다). 고침: `src/workflow/connector/masking.py` 의 `sk-` 앞에 낱말 경계(`(?<![A-Za-z0-9_-])`). 재현: `tests/workflow/connector/test_masking.py::test_ids_ending_in_sk_are_not_openai_keys`.
- **원본 이슈 댓글·`/operator/github` 문구가 push·PR 을 안 한다고 말함** — 결과 브랜치를 올린 실행이면 댓글이 `결과 브랜치 task/<id> 를 원격에 올렸습니다. 검토 승인 뒤 초안 PR 을 엽니다.` 를 적는다(`src/workflow/server/github_delivery.py`, 재현 `tests/workflow/server/test_task_cycle.py::test_comment_says_where_the_pushed_result_branch_is`). 화면 상단 문구는 "초안 PR 을 엽니다. 병합·이슈 종료는 사람"(`templates/operator_github.html`, `test_web_github_connect` 단정 추가).
- **`test_github_app` e2e 가 실제 github.com 에 fetch·push 를 시도** — 러너 폴더의 `origin` 이 `git@github.com:acme/billing.git` 라서 phase 12 러너가 실제 원격에 닿으려 했고(실패 → `branch_pushed false` → 사람 요청) 단정이 깨졌다. 원격 이름을 `upstream` 으로 바꿔 origin 없는 폴더(phase 11 동작)로 둔다 — push·PR 은 `test_real_repo` 가 본다. manifest 권한 단정도 `pull_requests: write` 로(step 6 변경).
- 메모(결함 아님): 연결 코드는 `connect_codes` 테이블에 평문으로 남는다(기본 키, 운영자 화면 목록 — phase 0 설계). 1회용·10분이고 교환 뒤 `used_at` 이 채워져 다시 쓸 수 없다. 비밀 검사는 이 행만 빼고 했고, 러너 연결 토큰(sha256 만 저장)은 DB 전체에 없다.
- 메모(결함 아님): 도구가 exit 1 로 끝나면(마지막 메시지 없음) 실패가 아니라 `needs_information`(사람 차례 알림)이다. 실패 알림은 실행이 `failed`·프로세스 종료 확인일 때만 — e2e 는 도구가 직접 커밋해 `commit_mismatch` 가 되는 경로를 썼다.

## 실연동 기록 틀 — 실제 저장소 순환 (phase 12 뒤, OpenArchive)

phase 뒤 사용자와 함께 채운다. 결과가 좋게 보이도록 편집하지 않는다. 이슈마다 아래 표 하나.

| 항목 | 값 |
|---|---|
| 날짜·시각 | (KST, 맡기기 → PR 병합) |
| 환경 | 셀프호스트 커밋·스키마 버전, 러너 도구·버전(`claude --version`), App 권한(Pull requests 쓰기 승인 시각), 테스트 DB(pgvector 5434), `setup_action` 값·설치 URL `state` 복귀 여부(phase 11 미확인) |
| 러너 등록 | [러너 붙이기] 명령(코드는 `***`), `--verify`·`--link`·`--env` 이름(값 없이), 카드 자동 매칭 결과 |
| 이슈 | `#번호` 제목, 지시 방법([맡기기]/`runloom` 라벨), 사용자가 손대지 않았음 확인 |
| 기준 커밋 | 수정 요청의 `base_commit` = 그 시각 `origin/main` 인지(원본 폴더 HEAD 와 비교) |
| 검증 결과 | 수정 전 로그(재현 테스트만 실패?), 수정 후·검증 프로필 exit, 링크(`backend/.venv`·`frontend/node_modules`)·`DATABASE_URL` 이 쓰였는지 |
| 결과 브랜치 | `branch_pushed`, 원격 `task/<id>` 커밋 |
| 검토 | 결과(`approved`/`changes_requested`/…), 재작업 횟수·새 커밋 |
| PR | 번호·초안 여부·본문 첫 줄 `Fixes #N`, 열린 시각, 사람 요청(`pr_unavailable`) 여부 |
| 병합 | 병합한 사람·시각, 업무 `완료` 반영 시각, 이슈 자동 닫힘 |
| 알림 | 받은 알림(사람 차례·PR 확인·실패)과 시각, Discord 표시 모양 |
| 실패·재작업 | 실패 코드·사유, 사람 조작(무엇을 왜) |
| 비용 | CLI 보고 비용(수정·검토) |
| 비밀값 | 토큰·URL·env 값이 DB·로그·화면·PR·알림에 없는지 확인 방법 |
| 근거 | 업무·실행 ID, PR URL, 로그 경로 |

## 2026-09-29 실연동 1 — runloom-sandbox #1 (재설계 전 phase 12 순환 확인)

[재설계 계획 16절](product/REDESIGN_PLAN.md#16-설계-검토-2026-09-29) 결정 3. 대상은 비공개 저장소 `jeongeundev/runloom-sandbox`(`service` 82d1bc1 복사). OpenArchive 에는 쓰지 않았다 — 시작 전에 남아 있던 OpenArchive #112 실행(`exec-8a1530fa723a2ff6`, 중앙 `running`)을 `실패 · 운영자 종료 — OpenArchive 쓰기 금지` 로 마감하고 OpenArchive 소스를 중지했다(백업 `20260929T081001Z` 뒤).

| 항목 | 값 |
|---|---|
| 날짜·시각 | 2026-09-29 17:26 KST 이슈 수집 → 17:30 맡기기 → 17:44 수정 결과 → 17:47 검토 승인 → 17:52 초안 PR(결함 2 로 5분 지연) → 18:06 병합 |
| 환경 | 셀프호스트 스키마 8, `service` 82d1bc1 이미지(2e06218 기준 설치), Claude Code 2.1.284, App `runloom-gwufov` — Pull requests 쓰기·Contents 읽기(결함 2 뒤 추가) 승인 |
| 러너 등록 | [러너 붙이기] 명령(코드 `***`) + `--repo /Users/kje/demo/runloom-sandbox --verify "check=sh -c 'python3 -m pytest -q && python3 -m ruff check .'" --env PYTHONPATH=src`. 카드 자동 매칭: 로컬 저장소·수정·검토 에이전트(`agt-b4929dbc`)·검증 프로필 `check` 모두 `(자동)`. `origin` 은 https + 클론 로컬 `credential.https://github.com.helper = !gh auth git-credential`(osxkeychain 에 GitHub 자격이 없었다) |
| 이슈 | #1 "워커 로그에 외부 요청 URL 전체가 남아 n8n callback 서명이 노출된다"(실제 결함, 재현·기대·인수 조건 포함), [에이전트에게 맡기기] |
| 기준 커밋 | `82d1bc16f766` = 그 시각 `origin/main` |
| 검증 결과 | 수정 실행 `exec-393f44f4a818380d` 14분. 재현 테스트 추가 → 2926 passed/68 skipped, ruff 통과 |
| 결과 브랜치 | `branch_pushed=1`, 원격 `task/task-b07ed4afa33d` = `79af657` — launchd 환경에서 push 성공(처음 실제 확인) |
| 검토 | `exec-36316de63142e6f3` 3분, `approved`, 재작업 0 |
| PR | #2 초안, 작성자 `app/runloom-gwufov`, 본문 `Fixes #1` + 검토 요약. 처음 3회 422(결함 2) |
| 병합 | 사용자 18:06:04 병합 → 이슈 18:06:05 자동 닫힘 → 업무 `완료 · PR 병합`, 원본 댓글 1개를 4번 고쳐 씀(마지막 "완료") |
| 알림 | `pr_opened` 1건 17:52:16 전송(Discord) |
| 실패·재작업 | 결함 1·2(아래). 사람 조작: 러너 로컬 상태의 끊긴 OpenArchive 실행 1건을 종료로 표시(백업 뒤), PR 재시도 소진을 막으려 워커를 권한 승인까지 정지 |
| 비용 | 수정 US$0.58(출력 6,541 토큰), 검토 US$0.41(2,175) — 구독 CLI 보고값 |
| 비밀값 | 러너 로그에 `signature`·`wfc_`·`gho_` 없음(grep 0). PR·댓글에 토큰·env 값 없음 |
| 근거 | 업무 `task-b07ed4afa33d`, https://github.com/jeongeundev/runloom-sandbox/pull/2, `~/Library/Logs/workflow-connector-selfhost/` |

발견한 결함 — 같은 날 `fix-live-1` 에서 수정(러너 `3664277`, App 권한·422 `6d8d8b7`):
1. **러너 재시작 뒤 끊긴 실행이 러너를 영구히 막는다.** 로컬 상태에 `running` 으로 남은 실행은 `unknown_local_at` 만 찍고 "사람 확인 필요" 로그를 남긴 채 활성 실행으로 남아 claim 을 하지 않는다(`connector/runner.py` `_continue`, `state.active_execution`). 중앙이 그 실행을 이미 마감(`failed`)했어도 러너는 모르고, 풀어 주는 명령도 없다. 방향: 중앙이 종료로 본 실행은 러너가 내려놓는다(heartbeat 응답 또는 claim 전 조회), 아니면 `connector release <실행>` 명령.
2. **App 권한에 Contents 읽기가 없어 초안 PR 이 422 `Validation Failed · not all refs are readable`.** `adapters/github_app.py` manifest `default_permissions` 가 issues·pull_requests·metadata 뿐. 가짜 GitHub e2e 로는 드러나지 않았다. 방향: manifest 에 `contents: read`, SELFHOST "App 권한 올리기" 에 Contents 추가, 권한 부족 422 를 `pr_unavailable` 사람 요청(권한 안내)으로 분류.
3. (개선) PR 생성 422 의 `message` 가 워커 로그에 남지 않아(`str(exc)` 는 `HTTP 422` 뿐) 원인을 컨테이너에서 재현해야 알았다. 로그에 요약(`GitHubUnprocessable.message`, 200자)을 붙인다.

sandbox 의 #1 수정(`79af657`, httpx 로그 억제)은 `service` 로 가져왔다(`d5c2568`, cherry-pick).

## 2026-09-29 phase 13 셀프호스트 전용 (step 5)

목적: [ADR-0019](adr/0019-service-selfhost-only.md)대로 `service` 에서 demo 모드·진단 데모·대본 에이전트·VM 배포 파일을 걷어낸 뒤 전체 회귀·대역 e2e·v8 → v9 마이그레이션을 확인한다. 외부 호출 없음 — GitHub 는 127.0.0.1 가짜 서버다.

| 항목 | 값 |
|---|---|
| 실행일 | 2026-09-29 KST, 이 Mac, 브랜치 `feat-13-selfhost-only` |
| 명령·결과 | `python3 -m pytest -q` — **2382 passed·32 skipped**(phase 13 전 2932 passed·68 skipped — 진단·대본·VM·데모 테스트 삭제). `python3 -m ruff check .` 통과. `WORKFLOW_E2E=1 python3 -m pytest tests/e2e -q` — **31 passed·1 skipped**(skip 은 `WORKFLOW_DOCKER` 게이트의 셀프호스트 e2e) |
| e2e 설정 | `tests/e2e` 에 demo 모드 설정(카탈로그 등록·대본 에이전트·`WORKFLOW_MODE=demo`)이 남지 않음. `test_real_repo.py` 의 `WORKFLOW_MODE=selfhost` 는 ADR-0019 가 허용하는 값(읽고 버림)이라 둠 |
| v8 → v9 사본 | `tests/workflow/server/test_backup.py::test_selfhost_v8_copy_upgrades_to_v9_and_backups_round_trip` — 임시 디렉터리에 셀프호스트 모양 v8 DB(워크스페이스 `sess-selfhost` 운영자, 옛 내장 4종류·규칙 2개, `bug_fix`·`code_review` Task 6, `github_sources`·`source_issues`·`baseline_items`·`task_pull_requests`·`notifications` 행)를 만들고 `backup create`(목록에 schema 8) → `init_schema` → 행 수는 `kinds` 4→2·`succession_rules` 2→1 말고 전부 그대로, 남은 종류 `bug_fix`·`code_review` → v9 백업을 다른 위치로 `restore`(행 수·산출물 같음) → v8 백업을 복원해도 복원이 v9 로 올려 같은 결과. 사용자 셀프호스트 볼륨·백업 파일은 읽지 않았다 |
| 미실행 | `WORKFLOW_DOCKER=1 python3 -m pytest tests/e2e/test_selfhost.py -q` — compose 프로젝트(`runloom-e2e-<랜덤>`)·포트(빈 포트)·볼륨은 격리되지만 이미지 태그 `workflow-selfhost:local` 을 사용자 셀프호스트(`runloom`)와 같이 쓴다. `install.sh` 의 `up -d --build` 가 그 태그를 이 브랜치(v9) 코드로 다시 빌드하므로, 사용자가 다음에 `compose up` 하면 백업 없이 DB 가 v9 로 올라갈 수 있다. 셀프호스트 재설치는 사용자 지시 뒤라 실행하지 않았다 |
| 아키텍처 | `domain/` 에 FastAPI·sqlite3·HTTPX·subprocess·Git import 없음, `server/`↔`connector/` 상호 import 없음(grep + `tests/test_packages.py`) |

### 발견한 결함과 고친 파일

- 제품 코드 수정 없음(마이그레이션은 step 3 에서 구현, 새 테스트는 처음부터 통과). 추가: 위 테스트. 문서: [SELFHOST](SELFHOST.md) 업그레이드 절 v9 한 줄, [REDESIGN_PLAN](product/REDESIGN_PLAN.md) 13절 phase 표 새 번호, [CURRENT_HANDOFF](CURRENT_HANDOFF.md) 다음 작업.

## 2026-09-30 phase 14 업무·단계 (step 10)

목적: [ADR-0020](adr/0020-work-items-and-stages.md)의 업무(`work_items`, 키 `RUN-n`)·단계(Task) 분리가 대역 e2e 한 줄기와 셀프호스트 모양 v9 사본 마이그레이션에서 그대로 도는지 확인한다. 외부 호출 없음 — GitHub 는 127.0.0.1 가짜 서버, origin 은 임시 bare 저장소다.

| 항목 | 값 |
|---|---|
| 실행일 | 2026-09-30 KST, 이 Mac, 브랜치 `feat-14-task-model` |
| 명령·결과 | `python3 -m pytest -q` — **2568 passed·33 skipped**. `python3 -m ruff check .` 통과. `WORKFLOW_E2E=1 python3 -m pytest tests/e2e -q` — **32 passed·1 skipped**(skip 은 `WORKFLOW_DOCKER` 게이트의 셀프호스트 e2e) |
| 성공 줄기 | `tests/e2e/test_real_repo.py` — 수집 직후 이슈 2건 = 업무 `RUN-1`·`RUN-2`(`새로 들어옴`, 원본 키 `acme/billing#n`), #1 끝에서 업무 1개·단계 2개(`bug_fix` + 같은 업무 `code_review`), 상태 흐름 `새로 들어옴` → … → `에이전트 작업 중` → … → `PR · 검토` → `완료`(`work_item_events` 순서), 결과 브랜치 `runloom/RUN-1`, PR 제목 `RUN-1 청구서 번호 자릿수`, 홈 목록은 `/work/RUN-1` 한 줄(검토 단계 링크 없음). `tests/e2e/test_github_cycle.py` — 업무 6개(이슈 5 + 웹 등록 검토 C), B 업무 = 단계 B·F, `내 차례`(승인 뒤 병합은 사람), 브랜치 `runloom/RUN-n`, A → C 는 `blocks` 연결 |
| 실패 줄기 | `test_real_repo.py::test_07_failed_work_is_my_turn_and_retry_adds_a_new_stage_on_a_new_branch` — #2 도구 직접 커밋(`commit_mismatch`) → 업무 `RUN-2` `내 차례`·이유 "실패 — …"·요청 `stage_failed`, `/work/RUN-2` 에 [다시 맡기기]·[닫기] → `retry` 응답 → 같은 업무에 새 `bug_fix` 단계, 실행 요청 `work_key=RUN-2`·`branch_seq=2` → 다시 실패해 다시 `내 차례`, 업무 수 그대로 |
| v9 → v10 사본 | `tests/workflow/server/test_backup.py::test_selfhost_v9_copy_upgrades_to_v10_work_items_and_backups_round_trip` — 임시 디렉터리에 셀프호스트 모양 v9 DB(운영자 워크스페이스, `all_open` 소스 둘. OpenArchive 류 이슈 20건 — 17건 지시 전, 3건 수정 + 검토 + PR 병합 — 기준선 24행·`baseline_imports`, sandbox 류 1건 병합·이슈 닫힘)를 만들고 `backup create`(schema 9) → `init_schema` → 업무 21개·키 1~21(생성 순)·지시 전 17건 `새로 들어옴`(담당 없음, 단계 1)·병합 4건 `완료`("PR 병합 — #n", 단계 2), 기존 표 행 수·기준선 그대로, 이미 기록된 `head_branch`(`task/…`) 불변, 멤버 1·매핑 1·이벤트 0 → v10 백업을 다른 위치로 복원(행 수·업무·산출물 같음) → v9 백업을 복원해도 v10 으로 올라 같은 업무. 사용자 셀프호스트 볼륨·백업 파일은 읽지 않았다 |
| 미실행 | `WORKFLOW_DOCKER=1 python3 -m pytest tests/e2e/test_selfhost.py -q` — compose 프로젝트(`runloom-e2e-<랜덤>`)·포트(빈 포트)·볼륨은 격리되지만 이미지 태그 `workflow-selfhost:local` 을 사용자 셀프호스트(`runloom`)와 같이 쓴다. `install.sh` 의 `up -d --build` 가 그 태그를 이 브랜치(v10) 코드로 다시 빌드하므로, 사용자가 다음에 `compose up` 하면 백업 없이 DB 가 v10 으로 올라갈 수 있다. 셀프호스트 재설치는 사용자 지시 뒤라 실행하지 않았다 |
| 아키텍처 | `domain/` 에 FastAPI·sqlite3·HTTPX·subprocess·Git import 없음, `server/`↔`connector/` 상호 import 없음(grep + `tests/test_packages.py`) |

### 발견한 결함과 고친 파일

- 제품 코드 수정 없음 — 추가한 단정·테스트는 처음부터 통과했다(업무 동작은 step 1~9 에서 구현). 추가: 위 e2e 단정·실패 줄기, v9 사본 테스트. 문서: [SELFHOST](SELFHOST.md) 업그레이드 절 v10 한 줄, [REDESIGN_PLAN](product/REDESIGN_PLAN.md) 16절 "14 확정" 표시, [CURRENT_HANDOFF](CURRENT_HANDOFF.md) 다음 작업.

## 2026-09-30 phase 15 팀 (step 11)

목적: [ADR-0021](adr/0021-team-accounts-and-roles.md)의 팀 계정·역할·사람별 "내 차례"·받는 사람별 알림·러너 소유자가 대역 e2e 한 줄기와 셀프호스트 모양 v10 사본 마이그레이션에서 그대로 도는지 확인한다. 외부 호출 없음 — GitHub 는 127.0.0.1 가짜 서버, 알림은 127.0.0.1 가짜 수신, origin 은 임시 bare 저장소다.

| 항목 | 값 |
|---|---|
| 실행일 | 2026-09-30 KST, 이 Mac, 브랜치 `feat-15-team` |
| 명령·결과 | `python3 -m pytest -q` — **2912 passed·40 skipped**. `python3 -m ruff check .` 통과. `WORKFLOW_E2E=1 python3 -m pytest tests/e2e -q` — **39 passed·1 skipped**(skip 은 `WORKFLOW_DOCKER` 게이트의 셀프호스트 e2e) |
| 팀 한 줄기 | `tests/e2e/test_team.py` 7개 — 첫 설정(운영자 토큰 → 관리자 계정, 설정 뒤 `/login` 은 이메일·비밀번호 폼) → 관리자 GitHub 연결(가짜)·공용 알림·재작업 상한 0 → 초대 링크 → 멤버 `김멤버` 가입·재로그인, `/team`·`/operator/notifications`·`POST /team/invites` 는 멤버 403, 개인 웹훅 저장 → 멤버 [러너 붙이기] → `connectors.owner_member_id` = 멤버, 운영자 화면 `소유자 김멤버` → 멤버 [맡기기] → `requested_by_member_id` = 멤버 → 검토 수정 요청이 사람 요청 → 멤버의 `내 차례` 에만(관리자 내 차례엔 없음, 전체엔 있음) → 공용 1(`→ 김멤버`)·개인 1(→ 없음), 재실행해도 알림 2건 그대로 → 멤버 응답(`human_responses.member_id`·`data-responder`) → 수정이 revision 2 로 이어 돈다. 다른 Origin POST → 403 `forbidden_origin`·변경 없음. DB 덤프·로그·화면에 비밀번호·초대 토큰·웹훅 경로·로그인 쿠키 없음 |
| v10 → v11 사본 | `tests/workflow/server/test_backup.py::test_selfhost_v10_copy_upgrades_to_v11_needs_first_setup_and_backups_round_trip` — 임시 디렉터리에 셀프호스트 모양 v10 DB(워크스페이스 하나, 첫 관리자 이메일·비밀번호 없음, 업무 22건 — 수집만 17·완료 4·사람 요청 열린 `내 차례` 1, 알림 3행, 연결 코드로 붙은 러너 1개·Agent)를 만들고 `backup create`(schema 10) → `init_schema` → 기존 표 행 수 그대로 + `login_sessions`·`member_invites` 0행, `needs_first_setup` 참, 관리자 행 불변(이메일·해시 없음), 러너 소유자·코드 발급자 없음(관리자 관리), 알림 전부 `shared`·받는 사람 NULL, 맡긴 사람 0, `내 차례` 업무의 받는 사람 = 관리자 → v11 백업 다른 위치 복원(같은 행·판정·산출물) → v10 백업 복원도 v11 로 올라 같은 결과. 사용자 셀프호스트 볼륨·백업 파일은 읽지 않았다 |
| 미실행 | `WORKFLOW_DOCKER=1 python3 -m pytest tests/e2e/test_selfhost.py -q` — compose 프로젝트(`runloom-e2e-<랜덤>`)·포트(빈 포트)·볼륨은 격리되지만 이미지 태그 `workflow-selfhost:local` 을 사용자 셀프호스트(`runloom`)와 같이 쓴다. 실행하면 그 태그가 이 브랜치(v11) 코드로 다시 빌드돼, 사용자가 다음에 `compose up` 할 때 백업 없이 v11 로 올라가고 옛 로그인이 풀릴 수 있다. 셀프호스트 재설치는 사용자 지시 뒤라 실행하지 않았다 |
| 아키텍처 | `domain/` 에 FastAPI·sqlite3·HTTPX·subprocess·Git import 없음, `server/`↔`connector/` 상호 import 없음(grep + `tests/test_packages.py`) |

### 발견한 결함과 고친 파일

- 제품 코드 수정 없음 — 추가한 e2e·사본 테스트는 처음부터 통과했다(팀 동작은 step 1~10 에서 구현). 배포 파일: `deploy/selfhost/install.sh` 설치 뒤 안내 "처음 접속 때 토큰으로 관리자 계정을 만든다"(`tests/test_selfhost_files.py` 단정). 문서: [SELFHOST](SELFHOST.md) 업그레이드 v11·팀(초대·비밀번호 분실·공개 주소와 원격 접속·개인 웹훅), [REDESIGN_PLAN](product/REDESIGN_PLAN.md) 13절 15-team 완료, [CURRENT_HANDOFF](CURRENT_HANDOFF.md) 다음 작업.

## 2026-09-30 phase 16 업무 화면 (step 10)

목적: [ADR-0022](adr/0022-work-screen-and-direct-work.md)의 업무 화면(담당자 묶음·빠른 필터·보드·상세 패널)·담당 = 맡기기·직접 작업·PR 신호·연결 탭·시작하기가 대역 e2e 한 줄기와 셀프호스트 모양 v11 사본 마이그레이션에서 그대로 도는지 확인한다. 외부 호출 없음 — GitHub 는 127.0.0.1 가짜 서버, 알림은 127.0.0.1 가짜 수신, origin 은 임시 bare 저장소다.

| 항목 | 값 |
|---|---|
| 실행일 | 2026-09-30 KST, 이 Mac, 브랜치 `feat-16-work-ui` |
| 명령·결과 | `python3 -m pytest -q` — **3191 passed·48 skipped**. `python3 -m ruff check .` 통과. `WORKFLOW_E2E=1 python3 -m pytest tests/e2e -q` — **47 passed·1 skipped**(skip 은 `WORKFLOW_DOCKER` 게이트의 셀프호스트 e2e). `WORKFLOW_E2E=1 python3 -m pytest tests/e2e/test_work_ui.py -q` — **8 passed**(2회 연속) |
| 에이전트 한 줄기 | `tests/e2e/test_work_ui.py` test_01~06 — 관리자 첫 설정 → GitHub 연결(가짜 App)·알림 URL → 이슈 2건 수집 → `/tasks` 담당자 묶음 `담당 없음` 2건·보드 `대기` 칸 2장·빠른 필터 `담당 없음` 2 → 시작하기 `가져올 곳` 완료·`러너` 다음(사이드바에 보임) → 러너 붙이기 → `/tasks?open=RUN-1` 패널에 담당 폼(`agent:<id>`)·조각 `/work/RUN-1/panel` 200 → 담당 = 에이전트 POST → 303 `/tasks?open=RUN-1`, 업무 담당·맡긴 사람 기록, 실행 1개, 상태 `에이전트 작업 중`, 목록은 에이전트 묶음으로·`담당 없음` 1건, 보드 `에이전트 작업 중` 칸, 빠른 필터 `에이전트 작업 중` → 시작하기 필수 3항목 완료·사이드바에서 사라짐 → 수정·검토 → 초안 PR #101(`runloom/RUN-1`) → `pr_opened` 알림 `task_url` = `{공개 주소}/tasks?open=RUN-1` → `PR · 검토`(보드 `PR · 검토` 칸, 패널 PR 은 `감지` 표시 없음, `work_pull_requests` 0행) → 병합 → `완료`(보드 `완료` 칸, 상태 묶음 `완료`) |
| 직접 작업 한 줄기 | test_07~08 — RUN-2 패널 [내 세션에서 작업] POST(목록 상태 `group=status&view=board` 유지) → 담당 = 관리자, `직접 작업 중`, 브랜치 `RUN-2-rounding-error`(패널 `data-branch`), 보드 `직접 작업 중` 칸, 목록 `운영자 (나)` 묶음, 워커 tick 2회에도 실행 0 → 가짜 GitHub 에 그 브랜치의 PR #201 → 동기화 → `work_pull_requests` 1행(open) → `PR · 검토`("PR 확인 — #201", 패널 `감지`) → 병합 → `완료`("PR 병합 — #201"), 직접 작업 칸 셋 비움, 이벤트 `direct_started`·`pull_request_linked` 1회·`direct_stopped`(`closed`), 감지 PR 은 알림을 보내지 않음(`pr_opened` 1건 그대로) |
| 연결·옛 주소 | test_02 — `/operator/github`·`/team`·`/kinds`·`/operator/notifications` → `/connect?tab=…`, `/metrics` → `/monitor`, `/work/RUN-1` → `/tasks?open=RUN-1`(모두 303), 연결 탭 5개 머리·가져올 곳 탭의 저장소 카드·알림 탭 `설정됨` |
| v11 → v12 사본 | `tests/workflow/server/test_backup.py::test_selfhost_v11_copy_upgrades_to_v12_with_unchanged_work_status_and_backups_round_trip` — 임시 디렉터리에 셀프호스트 모양 v11 DB(관리자·멤버, 러너·Agent, GitHub 소스, 업무 5건 — `새로 들어옴`·`에이전트 작업 중`·`PR · 검토`(Runloom PR open)·`완료`(merged)·`내 차례`(사람 요청), 실행 4·업무 이벤트 5)를 만들고 `backup create`(schema 11) → `init_schema` → 기존 표 행 수 그대로 + `work_pull_requests` 0행, 저장된 업무 상태·이유 = v12 규칙(직접 작업·감지 PR 포함)으로 다시 계산한 값, 직접 작업 칸 NULL, 이슈 커서 그대로·PR 커서 NULL, 이벤트 id 1~5 그대로 → v12 백업 다른 위치 복원(같은 행·상태·산출물) → v11 백업을 v12 코드로 복원해도 v12 로 올라 같은 결과. 사용자 셀프호스트 볼륨·백업 파일은 읽지 않았다 |
| 미확인 | 실제 github.com 의 PR 목록 읽기·키 매칭(`state=all`·`sort=updated` 응답, 사람이 연 PR 감지)은 가짜 GitHub 로만 확인했다. 브라우저 JS(행 클릭 → 패널 조각 끼우기·`pushState`·Esc·뒤로 가기, 묶음 접기 `localStorage`, 브랜치 `복사`)는 자동 테스트가 없다 — 서버가 `?open=` 으로 그린 패널과 폼 POST 만 확인했다. 사용자 확인 필요 |
| 미실행 | `WORKFLOW_DOCKER=1 python3 -m pytest tests/e2e/test_selfhost.py -q` — 이미지 태그 `workflow-selfhost:local` 을 사용자 셀프호스트(`runloom`)와 같이 써서, 실행하면 그 태그가 이 브랜치(v12) 코드로 다시 빌드돼 사용자가 다음에 `compose up` 할 때 백업 없이 v12 로 올라갈 수 있다. 셀프호스트 재설치는 사용자 지시 뒤라 실행하지 않았다 |
| 아키텍처 | `domain/` 에 FastAPI·sqlite3·HTTPX·subprocess·Git import 없음, `server/`↔`connector/` 상호 import 없음, 템플릿에 `\|safe` 없음(grep + `tests/test_packages.py`) |

### 발견한 결함과 고친 파일

- 제품 코드 수정 없음. e2e 를 쓰며 본 것: 러너가 카드에 매칭된(heartbeat) 직후 첫 claim 전이면 `connectors.supported_kinds_json` 이 비어 담당 = 에이전트가 곧바로 착수하지 못하고 `대기`("연결 프로그램 업데이트 필요 — bug_fix 미지원")로 남는다 — 지시는 기록되므로 다음 워커 tick 이 착수한다. 기존 동작(phase 5 claim 지원 종류)이고, e2e 는 첫 claim 을 기다린 뒤 맡긴다. 문서: [SELFHOST](SELFHOST.md) 업그레이드 v12·옛 주소 넘김, [UI_GUIDE](UI_GUIDE.md) 시작하기 상태에 `할 일`, [CURRENT_HANDOFF](CURRENT_HANDOFF.md) 다음 작업.
