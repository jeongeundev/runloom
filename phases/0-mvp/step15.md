# Step 15: codex-live-check

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/docs/ARCHITECTURE.md` — "Codex와 worktree"(미검증 항목: 인증 재사용, 모델 실행, worktree 설정 재사용, 승인 정책), "검증 순서와 다음 결정" 1~3행
- `/docs/adr/0001-first-local-agent-codex.md` — "구현 계획의 첫 로컬 step에서 확인한다"
- `/src/workflow/connector/codex.py`, `cli.py` (Step 12 — `run-local`), `/scripts/scaffold_demo_repo.py` (Step 13)
- `/src/diagnostic_demo/fixtures/` (Step 9 — 인계 디렉터리를 손으로 만들 때 원문)

## 작업

**실제 Codex CLI** 로 어댑터를 한 번 돌려 ADR-0001 의 미검증 항목을 확인하고 결과를 `docs/VERIFICATION_LOG.md` 에 기록한다. 코드 변경은 확인 결과로 드러난 결함 수정에 한정한다.

이 step 은 사용자의 Codex 구독 사용량을 쓴다. 실행 전 `codex --version` 과 `codex exec --help` 만 확인하고, 실제 실행은 **딱 한 번** 시도한다. 사용량 한도(`usage limit`)나 로그인 필요, 승인 프롬프트 대기가 발생하면 두 번째 시도 없이 `blocked` 로 기록하고 멈춘다.

### 절차

1. 데모 저장소: `python3 scripts/scaffold_demo_repo.py ../demo-report-repo` (이미 있으면 그대로. `base_commit` 을 기록).
2. 인계 디렉터리: `tests/e2e` 의 도구가 아니라 손으로 만든다 — `scripts/make_handoff_dir.py` 를 추가한다: fixture 의 evidence 8개를 `{evidence_id}@{version}.{ext}` 로 복사하고, CONTRACT 5절의 `DiagnosisResult` 를 `diagnosis_result.json` 으로, `manifest.json` 을 CONTRACT 2절 형식으로, `expected-report@1.json` 을 `workflow.domain.report_expectation.expected_report` 로 계산해 넣는다. (테스트 `scripts/test_make_handoff_dir.py`: 파일 9개, manifest 파싱, 해시 일치.)
3. 요청 파일: CONTRACT 2절의 `ExecutionRequest` 에 `base_commit` 만 실제 값으로 바꿔 `request.json`.
4. 실행: `python3 -m workflow.connector run-local --request request.json --handoff-dir <dir> --out <out>` — 환경변수 없이(허용 목록 검증). 20분 상한.
5. 결과 확인:
   - `out/code_change_result.json` 의 `outcome`, `result_commit`.
   - `out/test_log_before.txt` 첫 줄 `exit_code=` 가 0 이 아닌가.
   - `out/test_log_after.txt`·`verification_log.txt` 가 `exit_code=0` 인가.
   - `out/report_output.txt` 가 `합계    20    5` 인가.
   - `out/codex_jsonl.jsonl` 에서 Codex 가 `AGENTS.md` 를 읽었는지(기존 설정 활용 증거), 어떤 명령을 실행했는지, 승인 요청이 있었는지.
   - `../demo-report-repo` 의 `main` 이 불변인가, `task/fix-daily-0920` 브랜치가 생겼는가.
6. `docs/VERIFICATION_LOG.md` 작성 (새 문서. 짧게):
   - 실행일, Codex 버전, 명령 argv, 걸린 시간, exit code
   - ARCHITECTURE "검증 순서" 1~3행에 대한 결과: 통과 / 실패 / 확인 불가와 근거 파일
   - Codex 가 실제로 사용한 기존 설정(AGENTS.md 등)의 증거 줄 (JSONL 발췌 3줄 이내, 비밀값 없음)
   - 승인 정책·샌드박스 인자의 실제 동작 (`approval_policy=never` + `workspace-write` 로 멈춤 없이 끝났는가)
   - 발견한 결함과 고친 파일
7. `docs/ARCHITECTURE.md` "Codex와 worktree" 절의 "미검증" 문장을 결과에 맞게 한 줄씩 갱신한다 (검증한 것만 "확인" 으로. 안 된 것은 그대로).

### blocked 조건

- `codex` 실행 결과에 `usage limit`/`rate limit`/로그인 요구가 있으면 → `blocked`, `blocked_reason`: "Codex 사용량 한도 또는 인증. 심사 기간 B 실행에도 같은 한도가 적용되므로 사용자 확인 필요".
- 승인 프롬프트로 20분 대기 후 타임아웃 → `blocked`, 사유에 JSONL 의 마지막 요청을 발췌.

## Acceptance Criteria

```bash
python3 -m pytest scripts/test_make_handoff_dir.py -q
python3 -m pytest -q
python3 -m ruff check .
test -f docs/VERIFICATION_LOG.md && grep -c "exit_code" docs/VERIFICATION_LOG.md
```

## 검증 절차

1. 위 AC 를 실행한다. 실제 Codex 실행은 AC 가 아니라 이 step 의 작업이며, 결과가 실패여도 정직하게 기록하면 step 은 `completed` 다. 단, 어댑터 결함으로 실패했으면 고치고 (Codex 재실행 없이 가짜 codex 테스트로) 확인한다.
2. `phases/0-mvp/index.json` 의 step 15 를 업데이트한다 (summary 에 Codex 실행 결과 한 줄과 VERIFICATION_LOG 경로).

## 금지사항

- Codex 를 두 번 이상 실행하지 마라. 이유: 사용량이 거의 없다.
- `--dangerously-bypass-approvals-and-sandbox` 로 재시도하지 마라. 이유: 제품은 그 옵션을 쓰지 않는다. 승인 문제가 있으면 기록만.
- 결과를 좋게 보이도록 로그를 편집하지 마라. 이유: 이 문서는 근거다.
- 기존 테스트를 깨뜨리지 마라.
