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

- **연결 프로그램은 도구가 도는 동안 heartbeat 를 보내지 않는다** (제품 결함, 미수정). `runner.tick` 이 단일 스레드로 `_claim_and_start` → 어댑터 → `communicate_or_stop`(`proc.communicate(timeout=1200)`) 를 동기로 돌리므로 `_heartbeat_if_due` 가 실행 중엔 호출되지 않는다. 이번 실행: 마지막 heartbeat 09:42:41.775, claim 43.788, 다음 heartbeat **09:44:10.862** (Claude 가 끝난 뒤). 중앙 워커 tick 09:42:54.751 `agents_offline 2 · observations 1` — 로컬 Agent 둘(같은 연결 프로그램)이 `offline` 이 되고 C 실행에 `execution_observations` `heartbeat_lost`("연결 프로그램 heartbeat 미수신 (마지막 확인 …43.785Z)") 가 남았다. 결과 자체는 정상 수신·판정됐다(재실행 없음, 재접속 후 online). 로컬 스택은 offline 판정 10초·heartbeat 3초라 바로 드러났고, 운영 기본값(30초/90초)에서는 **90초 넘는 실제 실행**(Step 15 의 Codex B 2분 35초가 이미 그렇다)마다 같은 일이 난다. 대본 e2e(실행 1초 미만)와 `run-local`(중앙 없음)로는 보이지 않던 것. ARCHITECTURE 의 "연결 생존과 모델 진행 구분" 은 실행 중엔 성립하지 않는다. 고치려면 도구 실행 중 heartbeat 스레드(또는 `communicate` 를 폴링 루프로) — 사용자 결정 후.
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
