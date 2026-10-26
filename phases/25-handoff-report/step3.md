# Step 3: report-domain — 리포트 순수 규칙

## 읽어야 할 파일

- AGENTS.md, phases/25-handoff-report/README.md("결정" 절), phases/25-handoff-report/index.json(이전 step summary)
- docs/adr/0033-handoff-report.md, docs/ARCHITECTURE.md "인계 리포트 — phase 25"(**README 와 다르면 ARCHITECTURE 가 기준** — step 0 이 코드 조사로 고정한 이름·표·경로), docs/CONTRACT.md 19절, docs/GLOSSARY.md phase 25 용어
- `src/workflow/domain/` 의 기존 순수 모듈(`next_step.py`·`triage.py` — 요청문 조립·검사 함수 형식), `src/workflow/domain/execution_policy.py`
- step 1·2 결과: `contracts/v1.py` 새 모델, repo 함수

## 작업

`src/workflow/domain/handoff_report.py`(ARCHITECTURE 이름)에 FastAPI·sqlite3·HTTPX·subprocess·Git 을 import 하지 않는 순수 함수를 둔다.

1. 대상·불가 판정(D4·D5·D10): 판정 결과·실행 형태·원인 Agent 연결 방식·러너 능력을 받은 사실 묶음 → `만듦` / `불가(사유 문구)` / `대상 아님`. 사유 문구는 ARCHITECTURE 표 그대로.
2. 근거 확인(D18·D20·D21): 리포트 JSON 과 확인 가능한 근거 집합(산출물 id·커밋 범위·PR 번호·업무 원문·검증 로그 존재 여부)을 받아 주장별 `확인됨` / `근거 확인 안 됨` 을 돌려준다. 리포트를 거절하지 않는다.
3. 맞춤 풀이 인용 검사(D8): 맞춤 풀이의 주장이 원본 주장 id 를 인용하는지 — 없는 id 는 `근거 확인 안 됨`.
4. 리포트 작업 요청문 조립: 원인 결과 봉투 요약·outcome·산출물 목록·업무 원문·PR 근거(또는 읽기 실패 표시)·로컬 근거 자리를 받아 에이전트에게 줄 글을 만든다. "주장마다 근거 id 를 붙이고, 기록이 없는 이유는 '기록 없음' 으로 쓰고, 파일을 고치거나 커밋하지 말라" 를 포함. 맞춤 풀이용 요청문(받는 사람 이름·역할, 원본만 근거)도.
5. 보낸 사람·받는 사람 판정(D7)과 권한 판정(D8·D12·D13 — 결정 답=받는 사람·관리자, 다시 만들기=보낸 사람·받는 사람·관리자, 맞춤 풀이=받는 사람): 멤버 id·역할·원인 Agent 주인·다음 단계 담당·업무 담당을 받아 bool.

## 테스트

- `tests/workflow/domain/test_handoff_report.py`: 대상 판정(passed/failed, 판단 단계, needs_information, 직접 작업만 있는 업무), 불가 사유 각각, 근거 확인(존재·부재·형식 틀림), 인용 검사, 요청문에 금지 지시·근거 목록이 들어감, 권한 표(받는 사람·보낸 사람·관리자·다른 팀원·다음 단계 담당 없음 → 업무 담당).
- 이 모듈이 금지 모듈을 import 하지 않는다(기존 도메인 순수성 테스트가 있으면 그것으로).

## 금지사항 (이 step)

- 도메인 모듈에서 DB·HTTP·프로세스·Git 을 import 하지 마라. 이유: AGENTS.md CRITICAL 도메인 규칙.
- 외부 입력(업무 원문·PR 본문)을 명령·경로로 쓰지 마라. 이유: AGENTS.md CRITICAL — 글로만 요청문에 넣는다.

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
