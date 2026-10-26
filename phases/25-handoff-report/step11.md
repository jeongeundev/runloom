# Step 11: report-verify — 끝에서 끝까지 확인

## 읽어야 할 파일

- AGENTS.md, phases/25-handoff-report/README.md("결정" 절), phases/25-handoff-report/index.json(이전 step summary)
- docs/adr/0033-handoff-report.md, docs/ARCHITECTURE.md "인계 리포트 — phase 25"(**README 와 다르면 ARCHITECTURE 가 기준** — step 0 이 코드 조사로 고정한 이름·표·경로), docs/CONTRACT.md 19절, docs/GLOSSARY.md phase 25 용어
- `tests/e2e/test_human_api.py`(서버는 `TestClient`, 러너는 연결 토큰으로 claim·산출물·결과 경로를 직접 부르는 가짜, 워커는 같은 DB 의 `Worker` 를 테스트가 tick — `WORKFLOW_E2E` 없이 기본 실행), `tests/workflow/server/conftest.py` 헬퍼, step 0~10 summary

## 작업

`tests/e2e/test_handoff_report.py` 에 위 e2e 와 같은 방식(가짜 러너가 HTTP 로 claim·제출)으로 쓴다 — 실제 `connector` 러너 코드는 step 7 단위 테스트가 본다. 소스 결함을 찾으면 최소 수정하고 summary 에 적는다.

1. `bug_fix` 결과 passed → 원본 리포트 작업 → 보내는 러너(능력 선언)가 리포트 제출 → `code_review` 실행이 그동안 claim 되지 않다가 `done` 뒤 claim → 받는 쪽 claim 응답에 리포트 산출물 id·결정 답 칸이 있고, 그 산출물 내려받기 200, 결정 답 내용이 남긴 답과 같다(handoff dir 파일 생성은 step 7 단위 테스트가 본다).
2. 리포트 실패·시간 초과 → 다음 단계가 리포트 없이 착수, 패널에 `리포트 없음(사유)` → 다시 만들기 → `done`.
3. 사용자 정의 종류 결과에도 리포트, 접수 판단 결과에는 리포트, 결과 뒤 판단 결과에는 없음, 직접 작업만 있는 업무는 리포트 없음.
4. 옛 러너(능력 미선언): 리포트 `unavailable`, 다음 단계 대기 없음, claim 응답이 기존과 같다.
5. 맞춤 풀이: 받는 사람이 자기 Agent 로 요청 → 그 러너가 tailored 제출 → API·페이지에서 보임.
6. 근거 없는 id 를 가진 리포트 → `done` + 주장에 `근거 확인 안 됨`.

7. 실제 러너 e2e 회귀: `WORKFLOW_E2E=1 python3 -m pytest tests/e2e -q` 를 돌린다. 러너가 리포트 작업을 받게 되어 가짜 codex/claude 가 리포트 요청문을 받거나 다음 단계 claim 이 늦어지면 가짜·대기 처리를 고친다(단정은 약하게 만들지 않는다). 이 환경에서 돌 수 없으면 이유를 summary·concerns 에 남긴다.

## 테스트

- 위 1~6 을 각각 테스트로. 전체 `pytest -q` 통과. 7 의 결과를 summary 에 적는다.

## 금지사항 (이 step)

- 실제 GitHub·실제 Claude/Codex 를 부르지 마라. 이유: CI 에서 돈다 — 가짜 클라이언트·가짜 HTTP 러너.
- 단정을 약하게 만들어 통과시키지 마라. 이유: 이 step 이 phase 의 검증이다.

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
