# Step 6: report-claim — claim 배정·대기와 리포트 제출

## 읽어야 할 파일

- AGENTS.md, phases/25-handoff-report/README.md("결정" 절), phases/25-handoff-report/index.json(이전 step summary)
- docs/adr/0033-handoff-report.md, docs/ARCHITECTURE.md "인계 리포트 — phase 25"(**README 와 다르면 ARCHITECTURE 가 기준** — step 0 이 코드 조사로 고정한 이름·표·경로), docs/CONTRACT.md 19절, docs/GLOSSARY.md phase 25 용어
- `src/workflow/adapters/repo.py` 의 `claim_execution`(판단 단계 `supported_kinds`·검증만 다시 `capabilities` 거르기 선례), `src/workflow/server/machine_api.py` 전체(claim·산출물 업로드·내려받기·`require_connector`), `src/workflow/adapters/artifact_store.py`, `tests/workflow/server/test_machine_api.py`
- step 1~5 결과

## 작업

1. claim(D9·D11·D35·D36): 능력 `handoff_report` 를 선언한 connector 에는 배정된 `queued` 리포트 작업(original·tailored)을 기존 실행보다 먼저 `leased` 로 넘기고 `{"job_type": ...}` 형태(ARCHITECTURE)로 응답한다. 기존 실행은 원인 실행(`executions.predecessor_execution_id` 등 ARCHITECTURE 가 정한 연결)의 최신 original 이 `queued`·`leased` 면 건너뛴다(대기). 판단 단계 실행(접수·결과 뒤)은 대기하지 않는다(D15). 능력 미선언 connector 의 응답은 지금과 같다.
2. 받는 러너 전달(D14·D23): 능력을 선언한 connector 가 실행을 claim 하면 그 순간 원인 실행의 최신 `done` original 리포트 산출물 id 와 결정 답을 `ExecutionRequest` 의 선택 칸으로 붙인다(재claim 때도 최신). 미선언이면 칸 없음.
3. 제출 경로(ARCHITECTURE): 결과 제출 → 계약 검증·크기 → 실패면 `failed`(사유), 통과면 도메인 근거 확인 결과와 함께 `handoff_report` 산출물로 저장하고 `done`. 실패 보고 → `failed`. `leased` 가 아니면 409, 남의 작업 403. 받는 러너가 claim 때 붙은 리포트 산출물을 내려받을 수 있도록 `repo.download_allowed` 를 ARCHITECTURE 규칙대로 넓힌다(그 밖의 산출물 권한은 그대로).
4. tailored 작업은 B 가 고른 Agent 의 connector 에만 배정한다(step 8 이 만든다 — 여기서는 배정·제출이 job_type 무관하게 같은 길로 되게).
5. 새 러너 라우트를 ARCHITECTURE "### 주소 표" 에 행으로 더하고 `tests/workflow/server/test_app.py` 의 행 수 단정을 고친다.

## 테스트

- 능력 선언 러너: 리포트 작업이 먼저 나옴, `leased` 로 바뀜. 미선언 러너: 리포트 작업이 안 나오고 응답 바이트가 기존과 같음.
- 후속 실행: 원인 리포트 `queued` 동안 204(또는 다른 실행), `done`·`failed`·`timed_out`·`unavailable` 이 되면 claim 됨. 사람 [시작]으로 만든 실행도 같은 대기.
- 받는 러너 능력 선언 시 `ExecutionRequest` 에 리포트 id·결정 답, 결정 답을 바꾼 뒤 재claim 하면 최신.
- 제출: 정상 → `done` + 산출물, 크기 초과·계약 위반 → `failed`, 없는 근거 id → `done` + 그 주장 표시, 늦은 제출(`timed_out`) 409, 남의 connector 403, 원인 결과가 재작업으로 대체된 뒤의 제출도 `leased` 면 받아 그 실행에 붙음(D16).
- 받는 러너가 붙은 리포트 산출물을 내려받음(200), 관계없는 connector 는 403.
- 판단 단계 실행은 원인 리포트가 `queued` 여도 claim 됨.

## 금지사항 (이 step)

- `assemble_handoff`·`HandoffBundle` 을 바꾸지 마라. 이유: D23·D37.
- claim 에서 리포트 대기를 끝없이 하지 마라. 이유: D9 — 시간 초과(step 5)가 풀어 준다. 대기 조건은 상태(`queued`·`leased`)만 본다.

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
