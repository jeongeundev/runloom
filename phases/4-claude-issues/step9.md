# Step 9: session-agent-registration

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/AGENTS.md`
- `/docs/PRD.md` — "에이전트 등록은 실행 환경 연결 → 정보 자동 파악·제안 → 사용자 확인·수정" (사용자 합의), 2절 자동 선택 기본값
- `/docs/adr/0005-access-model-anonymous-session-operator-token.md` — 익명 세션 워크스페이스·운영자 토큰
- `/docs/UI_GUIDE.md` — 홈 빈 상태, 카드·배지 규칙
- `/docs/GLOSSARY.md` — Agent·connector·등록(registration)·discovered
- `/src/workflow/adapters/db.py`, `repo.py` — step 6 의 `session_agents` 와 `register_session_agent`·`list_session_agents`
- `/src/workflow/server/web.py` — `_shared_agents`, `_candidates`(자동 선택 후보), `home`, `/agents`, `/agents/{id}`, `task_new`·`task_create`(`successor_agent_id` 목록), `/tasks/{id}/select`
- `/src/workflow/server/views.py` — `agent_public`, `task_context`(candidates), `discovered` 필드
- `/src/workflow/server/templates/home.html`, `agents.html`, `agent_detail.html`, `task_new.html`, `_sidebar.html`
- `/src/workflow/server/worker.py` — 실행 생성 시 agent 행을 어떻게 읽는지 (세션 등록과 무관해야 한다)
- `/tests/workflow/server/test_web.py`, `test_views.py`, `test_ui.py`, `/tests/e2e/test_scenario.py`
- `/phases/4-claude-issues/index.json` — step 4 의 summary (에이전트 3개·`successor_agent_id`)

이전 step에서 만들어진 코드를 꼼꼼히 읽고, 설계 의도를 이해한 뒤 작업하라. 새 기능은 테스트를 먼저 작성한다 (AGENTS.md TDD, `tests/` 는 `src/` 미러 배치).

## 배경

지금은 운영자가 등록한 에이전트가 모든 세션에 곧바로 보이고 선택 후보가 된다. 심사자(실서비스의 사용자)는 **빈 워크스페이스에서 시작해 에이전트를 직접 등록**하는 경험을 해야 한다. 물리적 **연결**(사용자 Mac 의 연결 프로그램, 진단 API 접속 정보)은 소유자·운영자가 한 번 하고, **등록**(발견된 정보를 확인하고 이름·범위를 정해 내 워크스페이스에 넣는 것)은 세션마다 한다. 두 개념을 나눈다:

- **연결 카탈로그** = `agents` 중 `shared_to_all_sessions=1` 인 행 (운영자가 연결해 둔 것). 운영자 화면은 그대로 이것을 관리한다.
- **세션 등록** = `session_agents`. 홈·에이전트 목록·자동 선택 후보·`successor_agent_id`·직접 선택 목록은 **세션이 등록한 것만** 쓴다.
- 워커·연결 프로그램·진단 API 호출은 `agents` 행(연결)을 그대로 쓴다 — 실행 경로는 바뀌지 않는다.

## 작업

### `src/workflow/server/web.py`

- `_session_agents(conn, session_id) -> list[Row]` = `repo.list_session_agents`. `_candidates`·홈 카드·`/agents`·`task_new` 의 에이전트 select·`successor_agent_id` 검증·`/tasks/{id}/select` 후보 검증을 모두 이것으로 바꾼다. 등록 안 한 에이전트를 `chosen_agent_id`·`successor_agent_id` 로 보내면 422.
- `GET /agents/register` — 카탈로그 화면. 카탈로그의 각 연결에 대해: 종류(local/api)·소유 구분·**발견된 정보**(`discovered`: 저장소·기준 커밋·검증 프로필·도구 설정 존재·마지막 확인; API 는 능력·workflow 범위)·연결 상태 배지·이미 등록했으면 "등록됨" 표시. 각 항목에 등록 폼: `display_name`(기본 `agents.name`), 능력·범위는 **읽기 전용으로 보여 주고**(연결이 보고한 값 — 세션이 바꾸면 실행이 깨진다) 확인 체크 "발견된 정보를 확인했습니다" 필수.
- `POST /agents/register` — `agent_id`·`display_name`·`confirmed=1`. 카탈로그에 없으면 404, 확인 없음 → 422, 이름은 1~40자. `repo.register_session_agent` 후 `/agents` 로 303. 멱등.
- `POST /agents/{id}/unregister` — 세션 등록 해제(그 에이전트를 쓰는 활성 업무가 있으면 409 `agent_in_use`).
- 홈: 등록 0개면 "등록된 에이전트가 없습니다" + 「에이전트 등록」 CTA 를 업무 영역보다 위에. 「시연 업무 만들기」·「가져오기」 는 등록 1개 이상일 때만 보인다 (0개면 안내 문장 "먼저 에이전트를 등록하세요").
- 시연 폼(`?example=diagnose`)의 `successor_agent_id` 기본값: 세션이 등록한 `code.modify` 에이전트 중 첫 번째(등록 순), 없으면 자동 선택.
- `/agents`·`/agents/{id}` 는 세션 등록 기준. 카드에 `display_name` 을 쓰고 원래 이름은 부제로.

### `src/workflow/server/views.py`

- `agent_public` 에 `display_name`(없으면 `name`) 을 넣는다. `discovered` 는 이미 `credential_ref` 를 빼고 내보낸다 — API URL 도 빼라 (`_AGENT_PRIVATE` 에 `api_url` 추가).

### 템플릿

- `agents_register.html` (신규): 카탈로그 카드 목록 + 각 카드의 등록 폼. UI_GUIDE 카드·배지·chip 규칙. 외부 자산 없음.
- `home.html`·`agents.html`·`agent_detail.html`·`_sidebar.html`·`task_new.html` 갱신.

### `scripts/local_stack.py`·`tests/e2e`

- e2e 시나리오의 첫 단계에 등록을 넣는다: test_01 첫 방문 → 에이전트 0개·CTA → test_01b `/agents/register` 카탈로그 3개(발견 정보에 `demo-report-repo`·기준 커밋·`vp-pytest`) → 3개 등록 → 홈에 3개 `연결됨`. 이후 테스트는 그대로 (등록 후 흐름은 동일). test_09 "다른 세션은 업무를 못 본다" 에 "다른 세션은 등록도 0개" 추가.
- `scripts/seed_demo.py` 는 바꾸지 않는다 (카탈로그를 만드는 역할 그대로).

### 테스트 (먼저 작성)

- `test_web.py`: 새 세션 홈은 에이전트 0개·CTA·시연 버튼 없음; `/agents/register` 카탈로그 3개와 발견 정보(`api_url`·`credential_ref`·토큰 없음); 등록 → 홈 카드·`/agents`; 확인 없음 422·없는 agent 404·이름 길이 422; 등록 안 한 에이전트로 `chosen_agent_id`·`successor_agent_id` → 422; 자동 선택 후보가 세션 등록만 센다(운영 진단 데모를 등록 안 하면 진단 업무는 `확인 필요 · 후보 0개`); 해제·사용 중 409; 다른 세션과 격리.
- `test_views.py`: `display_name`, `api_url` 비공개.
- `test_ui.py`: 등록 화면 렌더·390px·금지 문구.
- 기존 test_web/test_ui 의 fixture(`web`)는 **세션이 3개를 등록한 상태**를 기본으로 만들어 기존 검사가 그대로 통과하게 한다 (fixture 한 곳만 바꾼다). 등록 전 상태를 보는 테스트는 별도 fixture.

## Acceptance Criteria

```bash
python3 -m pytest tests/workflow/server -q
python3 -m pytest -q
python3 -m ruff check .
WORKFLOW_E2E=1 python3 -m pytest tests/e2e -q -x          # 등록 단계 포함 전부 통과 (가짜 도구)
grep -n "api_url" src/workflow/server/templates/agents_register.html ; test $? -eq 1   # 화면에 API 주소 없음
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - ARCHITECTURE.md 디렉토리 구조를 따르는가?
   - ARCHITECTURE.md 외부 의존 표에 없는 외부 서비스를 끌어들이지 않았는가?
   - ADR 기술 스택을 벗어나지 않았는가?
   - AGENTS.md CRITICAL 규칙을 위반하지 않았는가? (도메인이 I/O 를 import 하지 않는가, server↔connector 가 서로 import 하지 않는가, 외부 입력에서 명령·경로를 실행하지 않는가, 비밀값이 DB·로그·응답에 없는가)
   - GLOSSARY.md 용어를 그대로 썼는가? 금지 표현을 쓰지 않았는가?
3. 결과에 따라 `phases/4-claude-issues/index.json`의 해당 step을 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"` (만든 파일·함수·결정 사항)
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- `agents` 테이블을 세션별로 복제하거나 `session_id` 컬럼을 넣지 마라. 이유: 연결(물리)과 등록(워크스페이스)을 나누는 것이 설계다. 워커·연결 프로그램은 `agents` 를 그대로 쓴다.
- 세션 등록 폼에서 능력·범위·저장소·검증 프로필을 **편집**하게 하지 마라. 이유: 연결 프로그램이 보고한 값과 어긋나면 실행 요청 조립(`_target_for`)이 깨진다. 이번엔 확인만, 편집은 후속.
- 운영자 화면·seed·연결 프로그램 등록 보고 경로를 바꾸지 마라. 이유: 카탈로그를 만드는 쪽은 그대로다.
- 발견 정보에 `api_url`·`credential_ref`·토큰을 내보내지 마라.
- 기존 테스트를 깨뜨리지 마라 (fixture 갱신으로 통과시킨다)
