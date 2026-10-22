# Step 5: report-worker — 판정 통과 시 리포트 작업 만들기·시간 초과

## 읽어야 할 파일

- AGENTS.md, phases/25-handoff-report/README.md("결정" 절), phases/25-handoff-report/index.json(이전 step summary)
- docs/adr/0033-handoff-report.md, docs/ARCHITECTURE.md "인계 리포트 — phase 25"(**README 와 다르면 ARCHITECTURE 가 기준** — step 0 이 코드 조사로 고정한 이름·표·경로), docs/CONTRACT.md 19절, docs/GLOSSARY.md phase 25 용어
- `src/workflow/server/worker.py` 전체(특히 `record_verdict` 를 `passed` 로 부르는 모든 곳·접수 판단 통과를 기록하는 `repo.record_triage_proposed` 호출(worker 1437행 근처)·tick 순서·`_queue_after_result`·`_spawn_successors`·`_cycle_followups`), `src/workflow/server/internal_investigation.py`·`next_step_runs.py`·`triage_runs.py`(판단·사내 요청 결과 판정 경로), `src/workflow/server/settings.py`
- step 2·3·4 결과: repo 함수, `domain/handoff_report.py`, `server/handoff_reports.py` 의 PR 근거 수집

## 작업

1. `server/handoff_reports.py` 에 "판정 통과한 실행 → 리포트 작업" 함수를 둔다: 도메인 대상·불가 판정 → `queued`(입력 자료: 원인 결과·산출물 id·업무 원문·PR 근거 — ARCHITECTURE 가 정한 시점에 수집) 또는 `unavailable`(사유) 또는 아무것도 안 함. 같은 실행에 두 번 불려도 하나(멱등).
2. ARCHITECTURE 의 판정 지점 표에 있는 모든 `passed` 판정에서 이 함수를 부른다(같은 트랜잭션 또는 직후 — ARCHITECTURE 대로). 판정·후속 착수·결과 뒤 판단의 기존 동작은 바꾸지 않는다(결과 뒤 판단은 리포트를 기다리지 않는다 — D15). 결과 뒤 판단 단계(`TRIAGE_MODE_NEXT_STEP`) 자체의 결과에는 리포트를 만들지 않는다(D4).
3. tick 에 시간 초과 처리: `queued`·`leased` 가 설정값(기본 1800초, 환경변수 `WORKFLOW_HANDOFF_REPORT_WAIT_SECONDS`, `Settings` 칸 이름은 ARCHITECTURE — D39)을 넘으면 `timed_out`(사유).
4. 워커 순환 import 주의: `handoff_reports` 가 `worker` 를 import 하지 않는다(PR #9 순환 import 사고 — `owner_approval → worker` 고리).

## 테스트

- 코드 수정 결과 passed → original `queued`, failed → 없음. 범용 종류·검토·접수 판단·사내 요청 조사 passed 각각 → `queued`, 결과 뒤 판단 결과 → 없음. needs_information passed → `queued`.
- 원인 Agent 가 API 연결형·삭제됨·러너가 능력 미선언 → `unavailable` + 사유.
- 같은 판정 재처리(tick 두 번) → 행 하나.
- 30분 경계 시간 초과, `done` 은 건드리지 않음.
- 결과 뒤 판단·후속 Task 생성이 리포트 유무와 상관없이 기존과 같다(기존 worker 테스트 회귀).
- `python3 -c "import workflow.server.worker"` 와 `python3 -m workflow.server.worker --help`(또는 import 만) 로 순환 import 없음 — 기존 순환 import 테스트가 있으면 그것.

## 금지사항 (이 step)

- 후속 착수 경로마다 리포트 대기 분기를 넣지 마라. 이유: 대기는 claim 한 곳에서 한다(D9, step 6).
- 종류 이름으로 분기하지 마라. 이유: AGENTS.md — 결과 형태·판정 지점으로 고른다(ADR-0009).

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
