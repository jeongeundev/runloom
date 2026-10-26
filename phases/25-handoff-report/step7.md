# Step 7: report-connector — 러너의 리포트 작업과 받는 쪽 파일

## 읽어야 할 파일

- AGENTS.md, phases/25-handoff-report/README.md("결정" 절), phases/25-handoff-report/index.json(이전 step summary)
- docs/adr/0033-handoff-report.md, docs/ARCHITECTURE.md "인계 리포트 — phase 25"(**README 와 다르면 ARCHITECTURE 가 기준** — step 0 이 코드 조사로 고정한 이름·표·경로), docs/CONTRACT.md 19절, docs/GLOSSARY.md phase 25 용어
- `src/workflow/connector/runner.py` 전체(`tick`·`_claim_and_start`·`_start`·`_finalize`·`_download_handoff`·`_handoff_dir`·`_masked_text`), `client.py`(claim·산출물 경로), `adapter.py`(`ExecutionAdapter`·`EchoAdapter`), `claude.py`·`codex.py`(고정 인자 배열), `prompt.py`, `git_ops.py`, `state.py`, `config.py`
- `src/workflow/connector/local_tool.py`(`launch_readonly`·`_run_triage` — 읽기 전용 실행·깨끗한 체크아웃 선례), `TRIAGE_OUTPUT_SCHEMA`(Codex 엄격 출력 스키마 선례)
- step 1·6 결과: `ReportJob`·claim 응답·제출 경로

## 작업

1. claim 요청 `capabilities` 에 `handoff_report` 가 들어가는지 확인만 한다 — step 1 이 `RUNNER_CAPABILITIES` 와 그 테스트를 이미 고쳤다.
2. `connector/report_job.py`(ARCHITECTURE 이름): claim 응답에 `job_type` 이 있으면 리포트 작업으로 처리한다. original — 결과 커밋을 깨끗한 읽기 전용 체크아웃으로 열고(판단과 같은 방식, ARCHITECTURE), 로컬 근거(커밋 목록·메시지·diff stat)를 모아 요청문과 함께 작업의 Agent 어댑터를 ARCHITECTURE 가 정한 경계(읽기 전용 실행 함수·리포트 출력 스키마)로 실행해 JSON 을 받는다. 체크아웃할 등록·커밋은 `ReportJob` 의 ARCHITECTURE 칸에서 얻는다. tailored — 체크아웃 없이 원본 리포트 JSON 만 입력. 결과를 제출 경로로, 실패(어댑터 실패·JSON 파싱 실패)는 실패 보고로. 끝나면 체크아웃을 지운다.
3. 쓰기 금지(D18): 리포트 작업 뒤 체크아웃에 변경·새 커밋이 있으면 제출하지 않고 실패 보고(사유). 어댑터 인자 배열은 고정이며 작업 입력에서 명령·경로를 받지 않는다.
4. 받는 쪽(D23): `ExecutionRequest` 에 리포트 산출물 id 가 있으면 내려받아(해시 확인) handoff dir 에 `report.json`, 결정 답이 있으면 `decisions.json` 을 쓴다. 없으면 파일을 만들지 않는다. 실행 요청문(`prompt.py`)에 "handoff dir 의 report.json·decisions.json 을 먼저 읽어라" 한 줄을 파일이 있을 때만 넣는다.
5. 비밀값 마스킹: 리포트 JSON 을 제출하기 전 기존 `_masked_text` 규칙을 적용한다.
6. `EchoAdapter` 가 리포트 작업에 계약에 맞는 리포트 JSON(근거 id 포함)을 내게 해 이 step 의 단위 테스트에 쓴다.

## 테스트

- 가짜 클라이언트로: 리포트 작업 claim → 체크아웃·어댑터 호출·제출 본문, 어댑터 실패 → 실패 보고, 체크아웃 변경 → 실패 보고, 끝난 뒤 체크아웃 정리.
- 받는 쪽: 리포트 id 있음 → `report.json`·`decisions.json` 생성, 해시 불일치 → 기존 `HandoffHashMismatch` 동작, 없음 → 파일 없음·요청문 줄 없음.
- (step 1 이 단정함 — 새 테스트 불필요)

## 금지사항 (이 step)

- `connector/` 에서 `workflow.server` 를 import 하지 마라. 이유: AGENTS.md CRITICAL — `contracts/` 만 공유.
- 리포트 작업 입력(업무 원문·PR 본문)을 셸 명령·파일 경로로 쓰지 마라. 이유: AGENTS.md CRITICAL.

## 테스트 먼저

소스·템플릿을 바꾸기 전에 `tests/` 미러 경로(`src/workflow/x/y.py` → `tests/workflow/x/test_y.py`)에 실패하는 테스트를 먼저 작성하고, 실패 원인이 의도한 것인지 확인한다(`tdd-guard.sh` 가 테스트 없는 소스 작성을 막는다). 구현한 뒤 해당 테스트와 전체 회귀를 통과시킨다. 이 step 의 변경으로 기존 테스트(개수·이름 집합·스키마 버전·문구 단정)가 깨지면 새 동작 기준으로 고치되 단정을 약하게 만들지 않는다. 무엇을 왜 바꿨는지는 summary 에 남긴다.

## Acceptance Criteria

```bash
python3 -m pytest -q
python3 -m ruff check .
```

위 커맨드와 이 step 의 "테스트" 항목을 모두 확인한다.

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트: ARCHITECTURE.md 디렉토리 구조·외부 의존 표를 벗어나지 않았는가, ADR 기술 스택 안인가, AGENTS.md CRITICAL 규칙(도메인 순수성, `server`↔`connector` 상호 import 금지, 외부 입력으로 명령·경로 실행 금지, 비밀값)을 지켰는가, GLOSSARY 용어를 그대로 썼는가.
3. `phases/25-handoff-report/index.json` 의 이 step 을 갱신한다: 성공 → `"status": "completed"`, `"summary"`(다음 step 에 필요한 파일·이름·결정), `"verification"`(실행한 AC 명령과 종료 코드). 3회 수정해도 실패 → `"error"` + `"error_message"`. 사용자 개입 필요 → `"blocked"` + `"blocked_reason"` 후 즉시 중단.
