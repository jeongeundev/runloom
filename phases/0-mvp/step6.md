# Step 6: session-web

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/docs/PRD.md` — "2. 업무 등록과 연결"(입력 항목, 기본값), "4. 완료 처리와 후속 업무 시작"(검토 동작), "공모전 시나리오"(시연 흐름 1~7)
- `/docs/UI_GUIDE.md` — "화면 목록", "심사자 첫 방문 흐름", "업무 상세 — 가운데 열의 순서". 이 step 은 라우트·데이터 흐름이고 마크업·CSS 는 Step 7
- `/docs/ARCHITECTURE.md` — "인증·권한·비밀정보 규칙" 표(세션·운영자가 할 수 있는 것), "배포와 실행 예산"(세션당 활성 업무 5, 진단 상한 429)
- `/docs/CONTRACT.md` — 1·2절(예시 업무의 request 문구), 9절(선택 기록), 10절(429 본문)
- `/docs/adr/0005-access-model-anonymous-session-operator-token.md`
- `/src/workflow/server/` (Step 5) — `app.py`, `auth.py`, `errors.py`, `settings.py`
- `/src/workflow/domain/` (Step 2) — `selection`, `status`, `completion`, `defaults`, `start_key`
- `/src/workflow/adapters/repo.py` (Step 4)

## 작업

심사자(익명 세션)와 운영자가 쓰는 웹 라우트를 `src/workflow/server/web.py` 에 만든다. 템플릿은 이 step 에서 **최소 마크업**(제목·본문 텍스트·폼·링크만, CSS 없음)으로 만들고 Step 7 이 UI_GUIDE 대로 다시 쓴다. 테스트는 마크업이 아니라 렌더된 텍스트와 리다이렉트·상태 코드를 본다.

### 세션 처리

`auth.require_session` 을 모든 웹 라우트의 의존성으로 건다. 쿠키가 없으면 `sessions` 행을 만들고 서명 쿠키를 심는다. 세션은 자기 `session_id` 의 Task 만 본다. 다른 세션의 task_id 로 접근하면 404 (존재를 알리지 않는다).

### 라우트 (`web.py` 의 `router`, 모두 HTML)

| 경로 | 동작 |
|---|---|
| `GET /` | 홈. 운영자 소유(`shared_to_all_sessions=1`) 에이전트 목록의 연결 상태 + 내 업무 목록 + 빈 상태 문구 "아직 업무가 없습니다." + `시연 업무 만들기` 링크(`/tasks/new?example=diagnose`) |
| `GET /tasks/new` | 등록 폼. `?example=diagnose` 또는 `?example=fix&predecessor={task_id}` 면 아래 "예시 미리 채움" 값으로 채운다. 폼 필드: `title`, `request`, `capability_code`(select: operations.diagnose / code.modify), `scope_value`, `selection_mode`(auto/manual), `chosen_agent_id`(select), `run_mode`(manual/auto), `completion_mode`(auto/review — `can_auto_complete` 가 False 인 종류면 auto 비활성), `criteria_extra`(textarea, 줄마다 항목), `predecessor_task_id`(select: 내 업무), 진단이면 `run_id`, 코드 수정이면 대상 정보는 선택된 agent 등록값에서 가져오므로 입력받지 않는다. 완료 기준 템플릿(`criteria_template`)의 문구를 폼에 표시한다 |
| `POST /tasks` | 폼 → Task 생성. `kind = kind_for_capability(code)`, `required_capability = Capability(code, {scope_key: scope_value})`, `criteria = merge_criteria(template, extra)`. 세션당 활성 업무(finished 아님) 5개 초과면 429 화면. 선택: `select_agent(...)` 후보는 `shared_to_all_sessions` 에이전트. 결과를 `save_selection`. 상태: `user_status(TaskView)` 로 계산해 저장. `target_json`: 진단 `{"run_id": ...}`, 코드 수정 `{"local_registration_id", "base_commit", "verification_profile_id"}` 는 선택된 agent 행에서(verification_profile_ids 의 첫 값). 완료 후 303 → `/tasks/{task_id}` |
| `GET /tasks/{task_id}` | 상세. 컨텍스트: task, 선택 기록, 활성/과거 Execution 과 이벤트, 결과 산출물 목록, 사용자 상태·이유, 선행·후속 링크, 검토 가능 여부. 결과 산출물이 `diagnosis_result`/`code_change_result` 면 JSON 을 파싱해 넘긴다 |
| `POST /tasks/{task_id}/run` | 직접 실행. 조건: 활성 실행 없음, 선택 `selected`, 선행이 있으면 선행 `완료`. 아니면 409 화면과 이유. 진단이면 상한 검사(세션 10/일, 전체 60/일 → 429, CONTRACT 10절 본문을 화면 문구로). `create_execution(start_key=request_start_key(uuid), attempt_no=다음 번호, assigned_connector_id=agent.connector_id)` + `record_diagnosis_start`. `ExecutionRequest` 를 여기서 조립해 `request_json` 에 고정한다 (CONTRACT 1·2절 형식). 상태 `실행 요청됨`. 303 → 상세 |
| `POST /tasks/{task_id}/select` | `needs_selection` 인 업무에 `agent_id` 를 직접 지정. `select_agent(mode="manual")` 로 다시 기록. 303 |
| `POST /tasks/{task_id}/review` | `decision`(approve/request_changes/close) + `comment`. 조건: 활성 실행이 `result_ready` 이고 상태 `확인 필요`. approve → Task `완료`, `finished_at`, `review_decision`, `release_execution`; 코드 수정이면 `status_reason="검토 승인 · 병합: 운영자 확인 대기"`. request_changes → `ReviewComment` 를 `review_comment` 산출물로 저장, 이전 실행 `release`, 새 실행 `attempt_no+1`, `start_key=request_start_key(uuid)`, `input_artifact_ids` = 원래 입력 + 이전 결과 artifact + review artifact, 코드 수정이면 `base_commit` = 이전 `result_commit`(없으면 원래). close → Task `실패`, reason `"검토 거절"`, release. 303 |
| `GET /tasks/{task_id}/artifacts/{artifact_id}` | 세션 소유 확인. `?raw=1` 이면 바이트 다운로드, 아니면 뷰어 페이지(Step 7 이 꾸민다. 지금은 `<pre>`) |
| `GET /agents`, `GET /agents/{agent_id}` | 읽기 전용. `credential_ref`·토큰 관련 값은 컨텍스트에 넣지 않는다. `discovered_json` 은 표시 |
| `GET /operator` | 운영자 아니면 토큰 입력 폼. 운영자면 운영자 화면: 에이전트 목록·등록 폼·삭제, 연결 코드 발급·취소, 병합 확인 대기 업무(`완료` + 코드 수정 + `merge_confirmed_at IS NULL`), 모든 세션 업무, 진단 사용량(오늘 건수) |
| `POST /operator/login` | `token` 이 `settings.operator_token` 과 `hmac.compare_digest` 로 같으면 `mark_operator`. 아니면 403 |
| `POST /operator/agents` | 등록 폼: `agent_id`, `name`, `owner_scope`, `connection_type`, `capabilities`(코드+범위 1개, 첫 구현), local 이면 `local_registration_id`, api 면 `api_url`·`credential_ref`(`env:` 접두사만 허용). `shared_to_all_sessions=1`. 자동 파악값은 연결 프로그램의 registrations 가 채우므로 폼에는 "연결 프로그램이 등록을 보고하면 채워짐" 안내만 |
| `POST /operator/agents/{agent_id}/delete` | 삭제 |
| `POST /operator/connect-codes`, `POST /operator/connect-codes/{code}/revoke` | 발급(화면에 코드와 만료 시각 표시)·취소 |
| `POST /operator/merges/{task_id}/confirm` | `confirm_merge`. 실제 git 병합은 운영자가 Mac 에서 수동으로 한다는 안내 문구 |

### 예시 미리 채움 (UI_GUIDE "심사자 첫 방문 흐름")

`web.py` 의 상수 `EXAMPLES`:

- `diagnose`: title `"일일 보고서 실패 진단"`, request = CONTRACT 1절의 `request` 문구, capability `operations.diagnose` / `workflow_id=daily-report`, selection auto, run manual, completion **auto**(A 만 기본값과 다르다. 이유를 폼에 한 줄 표시: "자동 판정기가 있는 진단 업무라 자동 완료로 미리 채움"), run_id `daily-0920-0900`.
- `fix`: title `"보고서 변환기 수정"`, request = CONTRACT 2절의 `request` 문구, capability `code.modify` / `repository_id=demo-report-repo`, selection auto, run auto, completion review, predecessor = 쿼리의 task_id.

### `TaskView` 조립 — `src/workflow/server/views.py`

```python
def build_task_view(conn, task_row, *, now: str, settings) -> TaskView
def task_context(conn, store, task_row, *, now: str, settings) -> dict     # 템플릿 컨텍스트 전부
```

`connector_online` 은 agent 의 `connection_state == "online"` 이고 `last_seen_at` 이 `now - heartbeat_offline_seconds` 이내인지로 계산한다. `predecessor_status` 는 선행 Task 행의 `status`.

### 템플릿 (`src/workflow/server/templates/`)

`base.html`, `home.html`, `task_new.html`, `task_detail.html`, `artifact.html`, `agents.html`, `agent_detail.html`, `operator.html`, `error.html`. 최소 마크업. Jinja2 `autoescape=True`. 시각은 `filters.py` 의 `kst(value)` 필터로 `2026-09-20 10:12:03 KST` 형식.

### 테스트 — `tests/workflow/server/test_web.py`, `test_views.py`

`TestClient(create_app(settings))`, fixture 로 운영자 에이전트 2개(`agent-ops-demo` api, `agent-codex-mac` local, 둘 다 `shared_to_all_sessions=1`)를 DB 에 넣는다.

- 첫 `GET /` 에 `Set-Cookie` 가 있고 "아직 업무가 없습니다." 가 보인다. 두 번째 요청은 새 쿠키를 심지 않는다.
- `GET /tasks/new?example=diagnose` 폼에 CONTRACT 1절 request 문구와 완료 기준 템플릿 문구가 있다.
- `POST /tasks`(diagnose 예시) → 303 → 상세에 `실행 가능`, `operations.diagnose · workflow_id=daily-report 일치 후보 1개`, 실행 버튼.
- `POST /tasks`(fix 예시, predecessor=A) → 상세에 `대기`, `선행 대기`.
- 후보 2개가 되도록 에이전트를 하나 더 넣으면 → `확인 필요`, `후보 2개 — 선택 필요`; `POST /tasks/{id}/select` 후 `실행 가능`.
- 다른 세션(쿠키 없는 새 클라이언트)에서 그 task_id → 404.
- `POST /tasks/{A}/run` → 303, 상세 `실행 요청됨`, DB 에 Execution `queued` 와 CONTRACT 1절 형식의 `request_json`, `diagnosis_usage` 1행. 두 번 누르면 409 (활성 실행 존재).
- 세션 상한: 같은 세션에서 11번째 진단 실행 → 429, 화면에 "오늘 이 세션의 진단 실행 한도(10회)에 도달했습니다."
- 검토: DB 에 `result_ready` 실행과 `code_change_result` 산출물을 넣고 `POST review approve` → `완료`, `검토 승인 · 병합: 운영자 확인 대기`. `request_changes` → 새 실행 `attempt_no=2`, `input_artifact_ids` 에 review 산출물, `base_commit` 이 이전 `result_commit`. `close` → `실패`, `검토 거절`.
- 운영자: 잘못된 토큰 403. 올바른 토큰 후 `/operator` 에 등록 폼과 연결 코드 발급. 발급된 코드로 `POST /connector/exchange` 성공. 세션 클라이언트는 `/operator` 의 등록 POST 에 403.
- `/agents/{id}` 응답 본문에 `credential_ref` 값·`wfc_` 가 없다.

### GLOSSARY

`example`(등록 폼 미리 채움 키 `diagnose`/`fix`. 자동 분해 기능이 아니다) 를 추가한다.

## Acceptance Criteria

```bash
python3 -m pytest tests/workflow/server -q
python3 -m pytest -q
python3 -m ruff check .
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트:
   - 화면 문구가 GLOSSARY 사용자 상태 7개와 PRD 3절 이유 문구인가? `pending`, `done`, `대기 중` 없음.
   - 세션이 다른 세션의 업무·산출물에 접근하지 못하는가? 운영자 전용 동작이 세션에 403 인가?
   - 실행 생성이 `start_key`·`attempt_no` 규칙을 따르고, 실행 요청을 조립할 때 셸 명령·경로를 폼에서 받지 않는가?
   - 완료 기준 제안·자동 선택에 LLM 이 없는가?
3. `phases/0-mvp/index.json` 의 step 6 을 업데이트한다 (summary 에 라우트 목록, `EXAMPLES` 위치, `views.py` 함수).

## 금지사항

- CSS·JavaScript·3열 레이아웃을 만들지 마라. 이유: Step 7 이 UI_GUIDE 대로 만든다. 여기서 만들면 두 번 작업한다.
- 실행 요청을 진단 API 나 연결 프로그램에 직접 보내지 마라. 이유: DB 에 `queued` 로만 남기고 워커(Step 8)와 claim(Step 5)이 가져간다.
- `alert`/`confirm` 이나 JS 로 검토를 처리하지 마라. 이유: UI_GUIDE 금지 목록. 폼 POST 만.
- 운영자 토큰을 쿠키·HTML 에 넣지 마라. 이유: 세션 행의 `is_operator` 표시만 쓴다.
- 기존 테스트를 깨뜨리지 마라.
