# Step 5: import-web

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/AGENTS.md`, `/docs/ARCHITECTURE.md`, `/docs/adr/` 전부, `/docs/GLOSSARY.md`, `/docs/UI_GUIDE.md`
- `/src/workflow/server/web.py` — `task_create`, `_insert_new_task`(Task 삽입·선택 기록·상태 확정), `_candidates`, `_session_agents`(step 2), `_target_for`, `_refresh_status`, `_redirect`, 활성 업무 한도(`settings.limits.active_tasks_per_session`)
- `/src/workflow/adapters/task_sources.py` — step 3 의 `load_issues`, `SOURCES`, `SOURCE_LABELS`
- `/src/workflow/domain/composition.py` — step 4 의 `compose`, `ChainPlan`, `PlanNode`, `Standalone`
- `/src/workflow/adapters/repo.py` — step 1 의 `insert_chain`, `list_session_agents`, `insert_task`(`chain_id`, `source_ref`)
- `/src/workflow/server/templates/task_new.html`, `home.html`, `_sidebar.html`, `/src/workflow/server/static/style.css`
- `/tests/workflow/server/test_web.py`(`create_task`, `diagnose_form`, `web` fixture), `/tests/workflow/server/test_ui.py`

이전 step 에서 만들어진 코드를 꼼꼼히 읽고, 설계 의도를 이해한 뒤 작업하라.

## 작업

심사자 흐름의 2단계 화면 "업무 가져오기". 출처 탭에서 이슈를 체크해 가져오면 step 4 규칙으로 체인이 구성되고 Task 들이 만들어진다. 이어지는 체인 화면은 step 6 이 만든다(이 step 에서는 `/chains/{chain_id}` 로 리다이렉트만 하고, 그 라우트가 아직 없으면 임시로 `/tasks` 로 보낸다 — step 6 이 바꾼다).

### `src/workflow/server/web.py`

- `GET /tasks/import?source=github|jira` — 탭 2개(`GitHub Issues`, `Jira`), 기본 `github`. 목록: key, 제목, 라벨 칩, `blocked by …`, 매핑 미리보기(`map_issue` 결과: `operations.diagnose · daily-report` / `code.modify · demo-report-repo` / `맡을 에이전트 없음 · 이유`). 체크박스는 전부 기본 체크. 상단 안내 한 줄: "시연 데이터입니다 — 실제 GitHub·Jira 에 연결하지 않습니다." 하단 버튼 `가져와서 워크플로우 구성`. 세션 등록 Agent 가 0개면 버튼 비활성 + "에이전트를 먼저 등록하세요"(링크 `/agents/register`).
- `POST /tasks/import` form `source`, `issue_keys`(다중) —
  1. `load_issues(source)` 에서 선택된 이슈만 고른다(모르는 key 무시, 0개면 422 `no_issues`).
  2. `compose(issues, _candidates(conn, session_id), prefer=[a.agent_id for a in list_session_agents(...)])`. `ValueError`(순환) → 422 `dependency_cycle`.
  3. 활성 업무 한도: 노드 수 + Standalone 중 능력 있는 것 수를 더해 `active_tasks_per_session` 을 넘으면 429(기존 문구).
  4. `repo.insert_chain({chain_id: f"chain-{token_hex(6)}", session_id, title: plan.title, source})`.
  5. 노드를 순서대로 `_insert_new_task(...)` 로 삽입한다 — `title=issue.title`, `request_text=issue.body`, `kind`, `capability_code`, `scope_value`, `selection_mode="auto"`, `chosen_agent_id=""`, `run_mode`, `completion_mode`, `criteria_extra=""`, `predecessor_task_id=<앞 노드의 task_id>`, `run_id`. `_insert_new_task` 에 `chain_id: str | None = None`, `source_ref: str | None = None`, `prefer: Sequence[str] | None = None` 인자를 추가해 `repo.insert_task` 와 `select_agent(prefer=…)` 에 넘긴다(동률 기본 선택이 실제 Task 의 `SelectionRecord` 에 남도록 — `PlanNode.selection` 을 그대로 저장하지 말고 실제 `task_id` 로 다시 계산한다).
  6. Standalone 중 `capability` 가 있는 것(예: 지원 안 되는 인계 쌍)은 선행 없는 단독 Task 로 삽입(`chain_id` 는 같은 체인, `run_mode="manual"`). 능력 없는 것(`#43`, `#44`)은 Task 를 만들지 않고 `insert_chain(..., skipped=[{key, title, reason}])` 에 남겨 체인 화면에서 "맡을 에이전트 없음" 으로만 보여 준다(step 1 의 `chains.skipped_json`).
  7. 303 → `/chains/{chain_id}`.
- `GET /tasks/new` 는 그대로(직접 등록). `?example=diagnose` 미리 채움과 `with_successor` 는 유지한다(직접 등록 경로의 예시).
- 홈 `/tasks` 업무 구역: 버튼 순서를 `업무 가져오기`(주, `/tasks/import`) · `직접 등록`(`/tasks/new`) 로 바꾸고 `시연 업무 만들기` 버튼은 제거한다(사이드바 `+` 는 `/tasks/import` 로). 빈 상태 문장 아래 한 줄: "GitHub·Jira 이슈를 가져오면 순서와 담당 에이전트가 자동으로 구성됩니다."

### `templates/tasks_import.html` (신규), `style.css`

- 탭은 링크 2개(`?source=…`), 활성 탭 밑줄. 표 형식(체크 · key · 제목 · 라벨 · 선행 · 배정 미리보기). 라벨 칩은 `.chip` 재사용, 색 없음.
- 안내 문장 클래스 `.small`. "시연 데이터" 라벨은 `.tag-scripted`(step 2) 재사용.

### 테스트 (먼저 작성)

- `tests/workflow/server/test_web.py`:
  - `/tasks/import` 기본 탭 github 4개, jira 탭 `OPS-41`…, 매핑 미리보기 문구, 시연 데이터 문장, 등록 Agent 0개면 버튼 비활성.
  - 카탈로그 2개 등록 후 `#41,#42,#43,#44` 가져오기 → 303 `/chains/chain-…`; Task 2개(`#41` 진단·직접·자동 완료, `#42` 코드 수정·자동·검토·선행 `#41`), `source_ref` 저장, `chain_id` 같음, chain 의 `skipped` 에 `#43`·`#44`; 홈 목록에 두 Task.
  - Codex·Claude 둘 다 등록(conftest 에 `agent-claude-mac` 시드 추가 — `local_registration_id=local-demo-report-claude`, 같은 `repository_id`·`base_commit`) 후 가져오기 → `#42` 의 선택 기록이 먼저 등록한 Agent 로 `selected`, reason 에 "먼저 등록한".
  - `#42` 만 가져오기(선행 없이) → 단독 코드 수정 Task, `run_mode=manual`.
  - 모르는 key 만 → 422; 한도 초과 → 429; 세션 격리.
- `tests/workflow/server/test_ui.py`: 셸·금지 표현 검사 경로에 `/tasks/import` 추가.
- 홈의 `시연 업무 만들기` 를 검사하던 기존 테스트는 새 버튼(`업무 가져오기`)으로 바꾼다.

## Acceptance Criteria

```bash
python3 -m pytest tests/workflow/server -q
python3 -m pytest -q
python3 -m ruff check .
grep -n "tasks/import\|insert_chain\|compose(" src/workflow/server/web.py | head
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - ARCHITECTURE.md 디렉토리 구조를 따르는가? (규칙은 `domain/composition.py`, 화면은 `server/`)
   - 외부 의존 표에 없는 외부 서비스를 끌어들이지 않았는가?
   - ADR 기술 스택을 벗어나지 않았는가?
   - AGENTS.md CRITICAL 규칙을 위반하지 않았는가? (이슈 본문은 `Task.request` 문자열일 뿐 실행하지 않는다)
   - GLOSSARY.md 용어·금지 표현.
   - UI_GUIDE: 모든 화면에 다음 행동 버튼 하나, 상태는 한글 텍스트 병기.
3. 결과에 따라 `phases/5-scripted-demo/index.json` 의 해당 step 을 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"`
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- 가져오기 화면에서 Task 를 실행하지 마라. 이유: 실행은 체인 화면(step 6)의 `워크플로우 시작` 한 곳에서만.
- `PlanNode.selection` 을 그대로 DB 에 저장하지 마라. 이유: `task_id` 가 임시 값(issue.key)이다 — 실제 `task_id` 로 다시 계산.
- fixture 이슈 외의 자유 입력 가져오기(URL 입력 등)를 만들지 마라. 이유: 이 phase 는 fixture 확정.
- 기존 테스트를 깨뜨리지 마라.
