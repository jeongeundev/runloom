# Step 2: catalog-registration

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/AGENTS.md`, `/docs/ARCHITECTURE.md`, `/docs/adr/` 전부(특히 ADR-0005 익명 세션·운영자 토큰), `/docs/GLOSSARY.md`, `/docs/UI_GUIDE.md`(앱 셸·화면 목록·하지 마라·상태 표시)
- `/src/workflow/server/web.py` — `_base`, `_shared_agents`, `_candidates`, `home`(`/tasks`), `agents_list`, `agent_detail`, `task_create`/`_insert_new_task`, `task_select`, 운영자 라우트
- `/src/workflow/server/views.py` — `agent_public`, `agent_online`
- `/src/workflow/server/templates/home.html`, `agents.html`, `agent_detail.html`, `_sidebar.html`, `task_new.html`, `_live.html`(선택 목록 `/tasks/{id}/select`), `_result_card.html`
- `/src/workflow/server/static/style.css`
- `/src/workflow/adapters/repo.py` — step 1 이 추가한 `register_session_agent`, `unregister_session_agent`, `list_session_agents`, `is_session_agent`
- `/tests/workflow/server/conftest.py`(`seed_agents`, `client`, `agents`, `web` fixture), `/tests/workflow/server/test_web.py`, `/tests/workflow/server/test_ui.py`, `/tests/e2e/test_scenario.py`
- `/phases/5-scripted-demo/step0.md` 의 "배경" 절, `/phases/5-scripted-demo/step1.md` 의 "이 step 이 만드는 개념"

이전 step 에서 만들어진 코드를 꼼꼼히 읽고, 설계 의도를 이해한 뒤 작업하라.

## 작업

심사자 흐름의 1단계 "에이전트 등록". 운영자가 미리 넣어 둔 카탈로그(`shared_to_all_sessions=1` 인 Agent)에서 세션이 골라 등록한다. 등록 전에는 홈에 에이전트가 0개이고 업무를 실행할 후보도 없다.

### `src/workflow/server/web.py`

- `_shared_agents(conn)` → 이름을 `_catalog_agents(conn)` 로 바꾸고(운영자 카탈로그), 새로 `_session_agents(conn, session_id) -> list[Row]` 를 만든다. 홈·업무 등록 폼·선택 목록·후보(`_candidates(conn, session_id)`)는 **세션 등록 Agent 만** 쓴다. 운영자 화면은 카탈로그 전체를 그대로 본다.
- 라우트:
  - `GET /agents/register` — 카탈로그 목록. 카드마다: 이름, `agent_id · owner_scope · connection_type`, 능력, 연결 상태, **발견된 정보 요약**(`discovered_json` 의 `found` 키 목록: 예 `AGENTS.md`, `git.head`, `codex_config`, 검증 프로필 — API 에이전트는 capabilities 의 역할·자료 범위), 이미 등록됐으면 `등록됨` 배지와 `해제` 버튼, 아니면 `등록` 버튼. `demo_scripted` 면 작은 회색 라벨 `시연용 · 대본 재생`.
  - `POST /agents/register` form `agent_id` — 카탈로그에 없으면 404, 있으면 `repo.register_session_agent` 후 303 → `/tasks`. 멱등.
  - `POST /agents/{agent_id}/unregister` — 세션 등록 해제. 그 Agent 가 선택된 미완료 Task 가 이 세션에 있으면 409 `agent_in_use`("진행 중인 업무가 있어 해제할 수 없습니다").
  - `GET /agents` — 세션이 등록한 Agent 만 나열 + 상단 링크 `에이전트 등록`(`/agents/register`). 0개면 빈 상태 문장 "등록한 에이전트가 없습니다." 와 버튼 `에이전트 등록`.
  - `GET /agents/{agent_id}` — 세션 등록 Agent 또는 카탈로그 Agent 면 열린다. 그 외 404.
- `task_create`·`task_select`: `chosen_agent_id`·후보가 세션 등록 Agent 가 아니면 422 `agent_not_registered`("등록하지 않은 에이전트입니다").
- 홈 `/tasks`: 등록 Agent 카드 + 버튼 `에이전트 등록`(→ `/agents/register`). 등록 0개면 "에이전트" 구역이 안내로 바뀐다: "먼저 에이전트를 등록하세요." + 버튼. 업무 구역의 `시연 업무 만들기` 는 이 step 에서 유지하되, 등록 Agent 가 0개면 버튼을 비활성(`disabled` + 이유 "에이전트를 먼저 등록하세요")으로 둔다 — step 5·6 이 가져오기 흐름으로 바꾼다.
- 사이드바 `에이전트` 링크는 그대로 `/agents`.

### `src/workflow/server/views.py`

- `agent_public` 에 `demo_scripted: bool` 과 `discovered_summary: list[str]`(발견된 정보 키 요약, 없으면 빈 목록) 을 추가한다.

### `templates/` · `style.css`

- `agents_register.html`(신규) — 카탈로그 카드 그리드(`agent-grid` 재사용). 상단 한 줄: "이미 쓰는 에이전트를 골라 등록합니다. 등록한 에이전트만 이 세션의 업무에 배정됩니다."
- `agents.html`·`home.html`·`agent_detail.html` 갱신. 카드의 `시연용 · 대본 재생` 라벨은 `.tag-scripted`(작은 회색 텍스트, 배지 색 아님) 로.
- 결과 카드(`_result_card.html`): 실행한 Agent 가 `demo_scripted` 면 셋째 줄 메타 끝에 `· 대본 재생 (실제 모델 호출 없음)` 을 붙인다. Task 상세 컨텍스트에 Agent 행이 이미 있는지 `views.task_context` 를 보고 필요하면 `agent_scripted: bool` 을 넣는다.

### 테스트 (먼저 작성)

- `tests/workflow/server/test_web.py`:
  - 새 세션 홈은 에이전트 0개·안내 문장·`에이전트 등록` 링크, `시연 업무 만들기` 비활성.
  - `/agents/register` 에 카탈로그 2개(conftest `seed_agents`)가 보이고 발견 정보 요약이 있으며 `api_url`·`credential_ref`·토큰이 HTML 에 없다.
  - 등록 → 홈 카드·`/agents` 에 나타남; 다시 등록해도 1개; 없는 agent 404.
  - 등록 안 한 Agent 로 `chosen_agent_id`(직접 선택) → 422; 자동 선택 후보가 세션 등록만 센다(운영 진단을 등록 안 하면 진단 업무는 `확인 필요 · 후보 없음`).
  - 해제; 진행 중 Task 가 있으면 409; 다른 세션과 격리(한 세션의 등록이 다른 세션 홈에 안 보임).
  - `demo_scripted=True` 인 Agent 는 카드에 `시연용 · 대본 재생`, 결과 카드에 `대본 재생`.
- `tests/workflow/server/test_ui.py`: 3열 셸 검사 경로에 `/agents/register` 추가; 금지 표현 검사 경로에도 추가.
- `tests/e2e/test_scenario.py`·`conftest.py`: 첫 방문 뒤 카탈로그 2개를 등록하는 단계를 시나리오 앞에 넣는다(HTTP POST). 나머지 시나리오는 그대로 통과해야 한다.
- 운영자 테스트(`/operator`)는 카탈로그 전체가 그대로 보이므로 바뀌지 않아야 한다.

## Acceptance Criteria

```bash
python3 -m pytest tests/workflow/server -q
python3 -m pytest -q
python3 -m ruff check .
grep -n "agents/register\|unregister\|_session_agents\|_catalog_agents" src/workflow/server/web.py | head
grep -rn "api_url\|credential_ref" src/workflow/server/templates/agents_register.html && exit 1 || echo "발견 정보에 비밀 참조 없음"
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - ARCHITECTURE.md 디렉토리 구조를 따르는가?
   - 외부 의존 표에 없는 외부 서비스를 끌어들이지 않았는가?
   - ADR 기술 스택을 벗어나지 않았는가? (Jinja2 + CSS + 소량 JS, CDN 없음)
   - AGENTS.md CRITICAL 규칙을 위반하지 않았는가? (`api_url`·`credential_ref`·토큰을 화면에 내보내지 않는다)
   - GLOSSARY.md 용어를 그대로 썼는가? 금지 표현(`대기 중`, `Powered by`, `pending`…)을 쓰지 않았는가?
   - UI_GUIDE "하지 마라"(보라 강조색, 그라데이션, 상태를 색으로만) 를 지켰는가?
3. 결과에 따라 `phases/5-scripted-demo/index.json` 의 해당 step 을 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"`
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- 세션이 새 Agent 를 만들게(운영자 등록 폼 노출) 하지 마라. 이유: ADR-0005 — 등록은 운영자, 세션은 카탈로그에서 고르기만.
- 세션 등록을 `agents` 테이블의 컬럼으로 표현하지 마라. 이유: 세션마다 다르다(`session_agents`, step 1).
- 워커(`server/worker.py`)를 고치지 마라. 이유: 워커는 저장된 `SelectionRecord` 만 보므로 바꿀 필요가 없다.
- 기존 테스트를 깨뜨리지 마라.
