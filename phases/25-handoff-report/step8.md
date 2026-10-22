# Step 8: report-api — 사람용 리포트 API

## 읽어야 할 파일

- AGENTS.md, phases/25-handoff-report/README.md("결정" 절), phases/25-handoff-report/index.json(이전 step summary)
- docs/adr/0033-handoff-report.md, docs/ARCHITECTURE.md "인계 리포트 — phase 25"(**README 와 다르면 ARCHITECTURE 가 기준** — step 0 이 코드 조사로 고정한 이름·표·경로), docs/CONTRACT.md 19절, docs/GLOSSARY.md phase 25 용어
- docs/adr/0032-human-api-and-member-tokens.md, docs/ARCHITECTURE.md "사람용 API — phase 24", docs/CONTRACT.md 18절
- `src/workflow/server/work_api.py`·`work_actions.py`·`work_lookup.py`·`auth.py`(`require_member_api`·`require_action`·읽기 전용 토큰)·`errors.py`·`app.py`, `tests/workflow/server/test_work_api.py`
- step 2·3·5·6 결과

## 작업

1. `server/report_api.py`(ARCHITECTURE 이름·경로 표 그대로), `app.py` 등록. 라우트: 업무의 리포트 목록, 리포트 JSON(근거 확인 결과·결정 답·이 멤버가 할 수 있는 행동 포함), 결정 답 PUT, 맞춤 풀이 요청 POST(받는 사람이 자기 Agent 로 — 진행 중이면 같은 행, 남의 Agent 403, 맞춤 풀이를 할 수 없는 Agent(API 연결형·러너 `handoff_report` 미선언) 422 + 사유 — D8), 맞춤 풀이 조회, 다시 만들기 POST(새 original — 불가 사유면 바로 `unavailable`).
2. 행동 로직은 웹(step 9)과 같이 쓸 함수 한 곳(ARCHITECTURE 이름 — 예 `server/handoff_reports.py` 의 행동 함수)에 두고 라우트는 호출만 한다(ADR-0031 결정 3).
3. 권한은 도메인 판정(D7 — 담당이 에이전트면 그 주인 멤버가 받는 사람·D8·D12·D13)과 기존 역할 검사, 읽기 전용 토큰은 GET 만(ADR-0032). 오류 본문은 기존 `code·message·field·details`.
4. 다시 만들기·맞춤 풀이로 만들어지는 작업도 PR 근거 수집(step 4)을 ARCHITECTURE 시점에 쓴다.
5. 새 라우트를 ARCHITECTURE "### 주소 표" 에 행으로 더하고 `tests/workflow/server/test_app.py` 의 행 수 단정을 고친다.

## 테스트

- 목록·상세: 단계별 최신과 이전, 근거 확인 안 됨 표시, 불가 사유.
- 결정 답: 받는 사람·관리자 200, 다른 팀원 403, 보낸 사람 403, changed 에 note 없음 422, 덮어쓰기.
- 받는 사람 판정: 다음 단계 담당이 에이전트면 그 주인이 결정 답 가능.
- 맞춤 풀이: 받는 사람 + 자기 Agent → 202 작업 생성, API 연결형 Agent 422, 두 번 → 같은 작업, 남의 Agent 403, 받는 사람 아님 403.
- 다시 만들기: 보낸 사람·받는 사람·관리자 허용, 다른 팀원 403, 원인 Agent 없음 → `unavailable`.
- 읽기 전용 토큰 GET 200·POST 403 `token_read_only`, 미인증 401. 응답에 비밀값 없음.

## 금지사항 (이 step)

- 리포트 HTML 라우트는 이 step 에서 만들지 마라. 이유: 템플릿이 step 9 에서 생긴다 — step 9 가 화면 경로와 `/api/v1` HTML 경로를 함께 만든다(D29).

- 웹 폼과 다른 규칙을 API 에만 두지 마라. 이유: ADR-0031 결정 3.
- 리포트 HTML 을 모델이 쓴 HTML 로 내보내지 마라. 이유: D19 — 서버 템플릿만.

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
