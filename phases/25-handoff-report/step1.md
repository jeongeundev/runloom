# Step 1: report-contract — 리포트 계약 모델

## 읽어야 할 파일

- AGENTS.md, phases/25-handoff-report/README.md("결정" 절), phases/25-handoff-report/index.json(이전 step summary)
- docs/adr/0033-handoff-report.md, docs/ARCHITECTURE.md "인계 리포트 — phase 25"(**README 와 다르면 ARCHITECTURE 가 기준** — step 0 이 코드 조사로 고정한 이름·표·경로), docs/CONTRACT.md 19절, docs/GLOSSARY.md phase 25 용어
- `src/workflow/contracts/v1.py` 전체(특히 `ARTIFACT_KINDS`·`ArtifactKind`·`_Contract`·`_OmitUnknownMeasure`·`ExecutionRequest`·`ClaimRequest`·`RunnerCapability`·`HandoffBundle`), `tests/workflow/contracts/test_v1.py`(CONTRACT 예시 블록 개수·`_SIGNATURES`·`_INLINE`)

## 작업

1. `contracts/v1.py` 에 ARCHITECTURE·CONTRACT 19절의 이름 그대로 모델을 더한다: `HandoffReport`(절 — 바뀐 것(무엇·왜)·결정과 근거·정하지 않고 남긴 것·받는 사람이 결정·확인할 것(항목 id 포함)·지킬 제약·남은 일, 주장마다 `evidence` 목록, '왜' 기록 없음 표시, 선택 Mermaid 텍스트 — D30), 맞춤 풀이 형태(원본 주장 id 인용 — D8), `ReportJob`(`job_type`·original/tailored 와 입력 자료 — D35·D36), 제출·실패 본문, 결정 답 본문.
2. 크기 제한(D31 — 주장 수·글자 수·Mermaid 길이)은 ARCHITECTURE 값으로 모델 검증에 넣는다. 위반은 ValidationError(서버가 `failed` 로 판정 — step 6).
3. `ARTIFACT_KINDS` 는 **이 step 에서 바꾸지 않는다**(DB CHECK 가 함께 바뀌어 step 2 가 맡는다). `ExecutionRequest` 에 받는 러너용 선택 칸(ARCHITECTURE 이름 — 리포트 산출물 id·결정 답)을 더하되 null 이면 직렬화에서 빠지게 한다(옛 러너 응답 불변 — D35, `_OmitUnknownMeasure` 선례).
4. `RUNNER_CAPABILITIES`(`v1.py`)에 `handoff_report` 를 더한다. 서버가 이 튜플 값만 저장하고(`repo.record_runner_capabilities`) 러너가 기본으로 이 튜플을 보내므로(`connector/client.py`) 이 step 이 그 단정 테스트(`tests/workflow/contracts/test_v1.py`·`tests/workflow/connector/test_client.py` 의 capabilities 값 단정)를 함께 고치고, CONTRACT 16.2 의 capabilities 목록도 같이 고친다. 러너가 리포트 작업을 실제로 처리하는 것은 step 7 이다(같은 PR 안이라 중간 상태는 배포되지 않는다).
5. CONTRACT 19절 예시 중 계약 모델이 있는 것을 ```json 블록으로 두고 `test_v1.py` 의 블록 개수·`_SIGNATURES` 를 맞춘다(step 0 이 `jsonc` 로 둔 것이 있으면 여기서 바꾼다). `_SIGNATURES` 는 최상위 키로 모델을 고르므로 새 예시가 기존 매처와 겹치지 않는지 확인한다 — 최상위 `source_execution_id` 는 `HandoffBundle`, `connector_id` 는 `ClaimRequest`, `outcome`+`kind` 는 `GenericResult`, `decision` 은 `ReviewComment` 로 잡힌다. 겹치면 새 매처를 더 구체적으로 앞에 두거나 기존 매처를 좁힌다.

## 테스트

- `HandoffReport`: CONTRACT 19절 예시 왕복, 크기 초과·필수 절 누락·중복 항목 id 거절, evidence 형식 검사.
- `ReportJob` original/tailored 각각, `job_type` 판별.
- `ExecutionRequest` 의 새 칸이 None 이면 `model_dump_json` 에 키가 없다(옛 응답과 같은 바이트).
- `RUNNER_CAPABILITIES` 값 집합을 단정하는 기존 테스트를 새 값으로 고친다.

## 금지사항 (이 step)

- `HandoffBundle`·`InputRef` 칸을 바꾸지 마라. 이유: 옛 받는 러너가 strict 검증에 실패해 inputs 를 못 내려받는다(D37).
- DB·서버·러너 코드를 건드리지 마라(테스트 단정 고치기는 예외). 이유: 계약 레이어 step 이다.
- `ARTIFACT_KINDS` 를 바꾸지 마라. 이유: 새 DB 의 `artifacts.kind` CHECK 가 바로 바뀌어 마이그레이션 일치 테스트가 깨진다 — step 2 가 재생성과 함께 한다.

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
