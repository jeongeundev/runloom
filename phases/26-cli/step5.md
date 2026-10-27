# Step 5: cli-actions — `runloom decide`·`respond`·`start`·`stop`

## 읽어야 할 파일

- AGENTS.md, `phases/26-cli/README.md`("결정" D19~D21·D24·D25), `phases/26-cli/index.json`(이전 step summary)
- `docs/adr/0034-cli.md`, `docs/ARCHITECTURE.md` "CLI — phase 26"(**README 와 다르면 ARCHITECTURE 가 기준**)
- `src/workflow/cli/main.py`·`render.py`·`client.py`(step 3·4)
- `src/workflow/server/report_api.py`(`DecisionBody`·`put_decision`), `src/workflow/server/handoff_reports.py`(`answer_decision` 권한), `src/workflow/server/human_api.py`(`ResponseBody` — `response_id`·`expected_revision`·`action`·`text`·`agent_id`, `allowed_actions`, `list_requests`), `src/workflow/server/work_api.py`(`put_direct_work`·`delete_direct_work`·`_detail` 의 `direct`)
- `docs/CONTRACT.md` 18·19절

## 작업

1. `decide REPORT_ID ITEM_ID (--confirm | --change TEXT)`(둘 중 하나 필수 — argparse 상호 배타 그룹, 없으면 exit 2) → `PUT /reports/{id}/decisions/{item_id}` 본문 `{"answer": "confirmed"}` 또는 `{"answer": "changed", "note": TEXT}`. 성공하면 응답 리포트를 `report` 렌더로 출력.
2. `respond REQUEST_ID ACTION [--text TEXT] [--agent AGENT_ID]` → `GET /human-requests` 에서 `request_id` 를 찾아 `revision` 을 `expected_revision` 으로(없으면 `열린 사람 요청 <id> 가 없습니다.` exit 1) → `POST /human-requests/{id}/responses` 본문 `{"response_id": uuid4 hex, "expected_revision", "action", "text", "agent_id"(줄 때만)}`. `ResponseBody` 는 `strict=True` 라 `--text` 가 없으면 `"text": ""` 를 보낸다(null 금지). 행동 값 검사는 서버. 성공 출력은 ARCHITECTURE 문구(응답 저장·`created` 여부).
3. `start KEY` → `PUT /work-items/{key}/direct-work` → `직접 작업을 시작했습니다.` + `브랜치: <direct.branch>`. `stop KEY` → `DELETE …/direct-work` → 안내 문구. 로컬 git 실행 없음.
4. `--json` 은 응답 본문 그대로.

## 테스트

`tests/workflow/cli/test_main.py` 에 이어서(실제 앱 주입).
- decide: 받는 사람 `--confirm` 200 → 출력에 답, `--change "다르게"` → note 저장, 둘 다/둘 다 없음 → exit 2, 501자 → exit 1(서버 422), 보낸 사람·다른 팀원 → exit 1 + `403 forbidden`, 읽기 전용 토큰 → exit 1 + `token_read_only`.
- respond: 열린 요청에 허용 행동 → 저장(서버 기록 확인), 같은 명령 두 번 → 요청이 닫혀 `GET /human-requests` 목록에 없으므로 두 번째는 `열린 사람 요청 <id> 가 없습니다.` exit 1(서버가 요청을 닫지 않는 행동이면 실제 서버 응답을 단정), 없는 request_id → exit 1 안내, 허용되지 않은 행동 → exit 1(서버 422), `--agent` 전달.
- start: 브랜치 이름 출력·업무 상태 `직접 작업 중`, 에이전트 실행 중 업무 → exit 1(서버 오류 그대로), stop → 직접 작업 해제.
- 행동 명령 전부: 읽기 전용 토큰이면 exit 1 + `token_read_only`, 토큰 원문이 출력에 없음.
## 테스트 먼저

소스를 바꾸기 전에 `tests/` 미러 경로(`src/workflow/x/y.py` → `tests/workflow/x/test_y.py`)에 실패하는 테스트를 먼저 작성하고, 실패 원인이 의도한 것인지 확인한다(`tdd-guard.sh` 가 테스트 없는 소스 작성을 막는다). 구현한 뒤 해당 테스트와 전체 회귀를 통과시킨다. 이 step 의 변경으로 기존 테스트(개수·이름 집합·문구 단정)가 깨지면 새 동작 기준으로 고치되 단정을 약하게 만들지 않는다. 무엇을 왜 바꿨는지는 summary 에 남긴다.

## Acceptance Criteria

```bash
python3 -m pytest -q
python3 -m ruff check .
```

위 커맨드와 이 step 의 "테스트" 항목을 모두 확인한다.

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트: ARCHITECTURE.md 디렉토리 구조·외부 의존 표를 벗어나지 않았는가, ADR 기술 스택 안인가, AGENTS.md CRITICAL 규칙(도메인 순수성, `server`↔`connector`(↔`cli`) 상호 import 금지, 외부 입력으로 명령·경로 실행 금지, 비밀값)을 지켰는가, GLOSSARY 용어를 그대로 썼는가.
3. `phases/26-cli/index.json` 의 이 step 을 갱신한다: 성공 → `"status": "completed"`, `"summary"`(다음 step 에 필요한 파일·이름·결정), `"verification"`(실행한 AC 명령과 종료 코드 목록 `[{"command": "...", "exit_code": 0}]`), 걸리는 점이 있으면 `"concerns"`. 3회 수정해도 실패 → `"error"` + `"error_message"`. 사용자 개입 필요 → `"blocked"` + `"blocked_reason"` 후 즉시 중단.

## 금지사항

- `respond` 에서 `expected_revision` 을 사용자 인자로 받지 마라. 이유: D20 — CLI 가 서버 값을 찾아 쓴다.
- 실패한 행동을 자동 재시도하지 마라. 이유: D24.
- `start` 에서 git 명령(브랜치 생성·checkout)을 실행하지 마라. 이유: D21·AGENTS.md CRITICAL 외부 입력 실행 금지.
- 맡기기·우선순위·판단 제안·맞춤 풀이·다시 만들기 명령을 더하지 마라. 이유: D1 범위 밖.
- 기존 테스트를 깨뜨리지 마라.
