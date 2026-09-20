# Step 6: chain-web

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/AGENTS.md`, `/docs/ARCHITECTURE.md`(상태·재접속·완료 절), `/docs/adr/` 전부(ADR-0005 병합은 운영자 확인), `/docs/GLOSSARY.md`, `/docs/UI_GUIDE.md`(상태 표시·실시간 갱신·업무 상세 순서·하지 마라), `/docs/PRD.md` 3절 상태 대응표
- `/src/workflow/server/web.py` — `task_detail`, `task_live`(`/tasks/{id}/live` 라이브 조각), `task_run`(`_start_execution`), `task_select`, `task_review`, `_refresh_status`, step 5 의 `/tasks/import`
- `/src/workflow/server/views.py` — `task_summary`, `build_task_view`, `status_of`, `task_context`
- `/src/workflow/domain/status.py` — 사용자 상태 라벨(`USER_STATUS_LABELS`)과 이유
- `/src/workflow/server/templates/task_detail.html`, `_live.html`, `_status.html`(배지·상태 점 매크로), `_sidebar.html`, `home.html`, `base.html`(인라인 스크립트의 폴링 방식)
- `/src/workflow/adapters/repo.py` — step 1 의 `get_chain`, `list_chains`, `tasks_of_chain`, `mark_chain_started`
- `/tests/workflow/server/test_web.py`(`seed_diagnosis_result`, `seed_reviewable_fix` 같은 결과 시드 헬퍼), `/tests/workflow/server/test_ui.py`, `/tests/workflow/server/test_views.py`

이전 step 에서 만들어진 코드를 꼼꼼히 읽고, 설계 의도를 이해한 뒤 작업하라.

## 작업

심사자 흐름의 3·4단계 화면 "워크플로우". 가져오기 직후 이 화면이 열리고, 구성 결과(순서·담당·이유)를 보여 주며 `워크플로우 시작` 한 번으로 첫 Task 가 실행되고 나머지는 워커가 자동으로 잇는다. 화면 라벨은 "워크플로우", 경로·코드는 `chain`.

### `src/workflow/server/web.py`

- `GET /chains/{chain_id}` — 세션 소유가 아니면 404. 가운데 열:
  1. 브레드크럼 `워크플로우 / {title}` · 출처 라벨(`GitHub Issues` / `Jira`, `시연 데이터` 라벨).
  2. **노드 목록**(세로, 순서대로). 노드마다: 순번, `source_ref` + 제목(→ `/tasks/{task_id}` 링크), 종류(`진단`/`코드 수정`), 담당 Agent 이름(+ `시연용 · 대본 재생` 라벨), 실행 방식(`직접`/`선행 완료 시 자동`), 완료 방식(`자동 완료`/`검토 후 완료`), 상태 배지 + 이유(`views.task_summary` 의 status·reason 그대로), 그리고 접이식 **이유**(`SelectionRecord.reason` + 구성 이유 문장). 선택 기록이 `needs_selection` 이면 그 자리에 `/tasks/{task_id}/select` 폼(등록 Agent 셀렉트 + `확정`)을 인라인으로 둔다. `selected` 여도 시작 전(`chains.started_at IS NULL`)이면 `담당 변경` 링크로 같은 폼을 펼칠 수 있다(`task_select` 는 실행 중이 아닐 때만 허용 — 기존 규칙 확인).
  3. 마지막에 **사람 단계 노드**: "검토 승인 (사람) · 병합은 운영자 확인". 상태는 마지막 Task 에서 파생: 검토 대기면 `확인 필요 · 검토 대기`(→ Task 상세의 검토 폼 링크), 승인되면 `완료 · 병합: 운영자 확인 대기`, 병합 확인되면 `완료 · 병합 확인됨`.
  4. `skipped` 목록: "워크플로우에 넣지 않은 이슈" — key·제목·이유(예: `#43 변경 응답 형식 모니터링 알림 추가 · 맞는 능력 코드 없음 (라벨: enhancement, repo:demo-report-repo)`).
  5. 동작 영역: 시작 전이면 버튼 `워크플로우 시작`(첫 노드가 `needs_selection` 이면 비활성 + "담당 에이전트를 먼저 확정하세요"). 시작 후에는 진행 요약 한 줄(`2단계 중 1단계 실행 중`)만.
- `POST /chains/{chain_id}/start` — 첫 노드 Task 에 대해 `task_run` 과 같은 경로(`_start_execution`, 진단 한도 검사 포함)로 실행을 만들고 `repo.mark_chain_started`. 이미 시작했으면 303 그대로(멱등). 첫 노드가 `needs_selection` 이면 409 `selection_required`. 진단 한도 429 는 기존 문구 그대로.
- `GET /chains/{chain_id}/live` — 노드 목록 + 사람 단계 + 동작 영역을 담은 조각(`_chain_live.html`). `base.html` 의 폴링 스크립트가 `/tasks/{id}/live` 를 다루는 방식과 같게(`data-live-url` 같은 기존 속성 규약을 따른다) 3초마다 갱신. 어느 노드든 `실행 중`·`실행 요청됨`·`대기` 가 있는 동안만 폴링.
- 홈 `/tasks`: 업무 구역 위에 **워크플로우** 구역 — 세션의 chain 목록(제목, 출처, 진행 `n/m 완료`, 상태 요약, → `/chains/{id}`). 사이드바 "최근" 목록에는 Task 그대로.
- Task 상세(`_live.html`) 브레드크럼에 chain 이 있으면 칩 `워크플로우 {title}`(→ `/chains/{id}`)을 추가한다.

### `views.py`

- `chain_summary(conn, chain: Row, *, now, settings) -> dict` — `title`, `source`, `tasks: [task_summary…]`(순서), `done_count`, `total`, `started`, `human_gate: {label, status_label, reason, task_id}`, `skipped: list[dict]`, `all_selected: bool`.

### `templates/chain_detail.html`, `_chain_live.html`, `style.css`

- 노드는 세로 목록 카드(`.chain-node`), 왼쪽에 순번 원(단색 테두리, 상태 점은 `_status.html` 매크로), 노드 사이 세로 연결선(`border-left`). 색으로만 상태를 표시하지 않는다 — 배지 + 한글 이유 병기.
- 접이식 이유는 `<details>` (`자동 선택 ·` 행과 같은 스타일).

### 테스트 (먼저 작성)

- `tests/workflow/server/test_web.py`:
  - 가져오기 후 `/chains/{id}`: 노드 2개 순서·담당·이유·상태(`#41 실행 가능`, `#42 대기 · 선행 대기`), 사람 단계 문구, skipped 2개, `워크플로우 시작` 버튼.
  - 시작 → 303, `#41` 에 실행 생성(`실행 요청됨`), `started_at` 기록, 다시 시작해도 실행 1개.
  - 첫 노드 후보 없음(진단 Agent 미등록)이면 버튼 비활성·start 409.
  - `#42` 동률(Codex·Claude)이라도 기본 선택돼 시작 가능; 시작 전 `담당 변경`으로 Claude 로 바꾸면 선택 기록 `manual`.
  - `/chains/{id}/live` 가 노드 상태를 담고, 다른 세션은 404.
  - 결과 시드 헬퍼로 `#41` 완료·`#42` 검토 대기 상태를 만들면 사람 단계가 `확인 필요 · 검토 대기`; 승인 후 `완료 · 병합: 운영자 확인 대기`.
  - 홈에 워크플로우 구역과 진행 `1/2 완료`.
- `tests/workflow/server/test_ui.py`: 셸·금지 표현 경로에 `/chains/{id}` 추가; 상태를 색으로만 표시하지 않는지(배지 텍스트 존재).
- `tests/workflow/server/test_views.py`: `chain_summary`.

## Acceptance Criteria

```bash
python3 -m pytest tests/workflow/server -q
python3 -m pytest -q
python3 -m ruff check .
grep -n "chains/{chain_id}\|chains/{chain_id}/start\|chains/{chain_id}/live" src/workflow/server/web.py
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - ARCHITECTURE.md 디렉토리 구조를 따르는가?
   - 상태 전환 규칙(ARCHITECTURE "상태·재접속·완료", PRD 3절)을 화면이 새로 만들지 않고 `views`·`domain/status` 의 판정을 그대로 쓰는가?
   - ADR-0005: 병합 확인은 운영자 전용 — 세션 화면에는 "운영자 확인 대기"만.
   - AGENTS.md CRITICAL 규칙을 위반하지 않았는가?
   - GLOSSARY.md 용어·금지 표현(`pending`, `done`, `대기 중` 금지). UI_GUIDE "하지 마라"(스피너로 접수·시작 뭉개기 금지).
3. 결과에 따라 `phases/5-scripted-demo/index.json` 의 해당 step 을 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"`
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- 체인 화면에서 두 번째 노드를 직접 실행하는 버튼을 만들지 마라. 이유: 후속은 워커가 선행 완료를 보고 자동 착수한다 — 그 장면이 제품 가치다.
- 워커(`server/worker.py`)의 후속 착수 로직을 바꾸지 마라. 이유: 이미 `predecessor_task_id` + `SelectionRecord` 로 동작한다.
- 상태를 화면에서 새로 계산하지 마라. 이유: `domain/status.py` 가 유일한 판정 위치.
- 외부 스크립트·CDN·드래그 라이브러리를 넣지 마라. 이유: ADR-0002, 인라인 소량 JS 만.
- 기존 테스트를 깨뜨리지 마라.
