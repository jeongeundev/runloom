# Step 11: docs-glossary

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/AGENTS.md`, `/docs/README.md`(문서 구분), `/docs/ARCHITECTURE.md`, `/docs/adr/` 전부(step 10 의 ADR-0008 포함), `/docs/GLOSSARY.md`, `/docs/UI_GUIDE.md`, `/docs/PRD.md`, `/docs/CURRENT_HANDOFF.md`, `/docs/CONTRACT.md`
- 이 phase 의 산출물: `/src/workflow/scripted/`, `/src/workflow/domain/task_sources.py`, `/src/workflow/domain/composition.py`, `/src/workflow/adapters/task_sources.py`, `/src/workflow/adapters/db.py`(`session_agents`·`chains`), `/src/workflow/server/web.py`(`/agents/register`, `/tasks/import`, `/chains/…`), `/src/workflow/server/templates/landing.html`, `tasks_import.html`, `chain_detail.html`, `agents_register.html`
- `/phases/5-scripted-demo/index.json` 의 step 0~10 `summary`

이전 step 에서 만들어진 코드를 꼼꼼히 읽고, 설계 의도를 이해한 뒤 작업하라.

## 작업

코드가 앞서 갔으니 문서를 코드에 맞춘다. 새 개념을 GLOSSARY 에 등록하고, 심사자 흐름·화면 목록·제품 문서의 낡은 문장을 고친다. 문서만 고치고 코드는 건드리지 않는다.

### `docs/GLOSSARY.md` — 용어 표에 추가·수정

| 용어 | 뜻 | 쓰지 말 것 |
|---|---|---|
| `Chain` / `chain_id` | 세션이 가져오기로 만든 Task 묶음. 화면 라벨은 "워크플로우". 순서는 Task 의 `predecessor_task_id` 체인. `workflow_id`(진단 대상 자동화 ID)와 다르다 (`adapters/db.py` `chains`, `domain/composition.py`) | `workflow_id`, `pipeline`, `flow` |
| `Issue` | 외부 출처(GitHub·Jira fixture)의 업무 항목. `key`·`labels`·`blocked_by`. Task 가 되기 전의 것 (`domain/task_sources.py`) | `Task`(이미 등록된 것), `ticket` |
| `TaskSource` / `source` | Issue 의 출처 `github`/`jira`. 이 phase 는 fixture (`adapters/task_sources.py`) | `provider`, `integration` |
| `IssueMapping` | 라벨 규칙으로 정한 Issue 의 능력·run_id·이유 (`map_issue`) | `classification`, `intent` |
| `ChainPlan` / `PlanNode` / `Standalone` | `compose` 의 결과 — 순서·방식·배정·이유 / 체인에 못 들어간 이슈와 이유 (`domain/composition.py`) | `Graph`, `DAG`, `schedule` |
| `session agent` | 세션이 카탈로그에서 등록한 Agent (`session_agents`). 후보·홈·선택은 세션 등록만 센다 | `favorite`, `subscription` |
| `catalog` | 운영자가 미리 넣은 `shared_to_all_sessions=1` Agent 목록 (`/agents/register`) | `marketplace`, `directory` |
| `scripted agent` / `demo_scripted` | 대본 에이전트 (`workflow.scripted.codex`/`.claude`, `DIAG_MODEL=fake`). 모델 호출 없이 정해진 변경·진단을 내며 계약·검증기·worktree·pytest 는 실제와 같다. `agents.demo_scripted=1` 이면 화면에 `시연용 · 대본 재생`. 공개 데모 전용(ADR-0008) | `fake codex`(이전 이름 — 항목을 이 항목으로 대체), `mock`, `simulation`(화면 문구로 쓰지 않음) |
| `human gate` | 체인 마지막의 사람 단계 노드 문구 "검토 승인 (사람) · 병합은 운영자 확인". Task 가 아니다 | `approval step`, `manual task` |
| `prefer` | `select_agent` 동률 규칙의 우선순위(세션이 먼저 등록한 순서) | `priority`, `score` |

- 기존 `fake codex` 항목을 `scripted agent` 로 바꾸고, `LocalStack` 항목에 `--scripted` 를 적는다. `example` 항목에 "직접 등록 경로의 미리 채움. 시연 주 경로는 가져오기(`/tasks/import`)" 를 덧붙인다.
- "경계가 헷갈리는 개념" 절에 `workflow_id` vs `Chain` 한 줄.

### `docs/UI_GUIDE.md`

- 화면 목록에 `/agents/register`, `/tasks/import`, `/chains/{chain_id}` 행 추가; 홈(`/tasks`) 행을 워크플로우 구역 포함으로.
- "심사자 첫 방문 흐름" 을 e2e(step 9) 순서로 다시 쓴다: 랜딩 → 서비스 바로 가기 → 에이전트 등록(3개) → 업무 가져오기 → 워크플로우 확인(순서·담당·이유) → 시작 → A 완료 → B 자동 착수 → 검토 승인 → 병합은 운영자 확인. 각 단계에 화면이 보여 주는 상태 문구.
- 상태 표시 절에 `시연용 · 대본 재생` 라벨 규칙(작은 회색 텍스트, 배지 아님).

### `docs/PRD.md` · `docs/product/PRODUCT_BRIEF.md`

- "공모전 시나리오" 절 앞에 상태 줄: "2026-09-21 이후 공개 데모는 대본 에이전트(ADR-0008). 아래 시연 흐름의 등록·가져오기·구성·자동 실행은 구현됨, 실제 모델·실제 Codex 실행 증거는 VERIFICATION_LOG 2026-09-20." 본문은 고치지 않는다(합의 기록).
- 시연 흐름 1·2 항목에 "카탈로그 등록", "GitHub·Jira fixture 가져오기" 를 괄호로 덧붙인다.

### `docs/CURRENT_HANDOFF.md`

- "지금 상태" 절을 phase 5 완료 기준으로 갱신: 공개 데모 구성, 남은 것(실제 GitHub/Jira API 연동, 새 업무 종류(리뷰 단계), 셀프호스트 1인용 패키징, A2A), 재개 방법(`python3 scripts/execute.py …`).

### `docs/CONTRACT.md`

- 바뀐 계약이 없음을 확인한다(이 phase 는 계약 v1 을 바꾸지 않았다). 바뀌었다면 그 step 이 이미 고쳤어야 하므로 여기서는 확인만 하고 불일치가 있으면 summary 에 적는다.

### `AGENTS.md`

- "제품 코드와 진단 데모의 경계" 절에 한 줄: "`src/workflow/scripted/` 는 공개 데모 전용 대본 에이전트다(ADR-0008). 제품 런타임 경로(`connector/`)에서 import 하지 않는다."

## Acceptance Criteria

```bash
python3 -m pytest -q                       # 문서 검사 테스트(test_deploy_files 의 런북 검사 등) 포함 통과
python3 -m ruff check .
grep -n "Chain\b\|scripted agent\|session agent\|Issue\b" docs/GLOSSARY.md | head
grep -n "fake codex" docs/GLOSSARY.md && exit 1 || echo "옛 용어 제거됨"
grep -n "/agents/register\|/tasks/import\|/chains/" docs/UI_GUIDE.md | head
grep -n "ADR-0008" docs/PRD.md docs/CURRENT_HANDOFF.md AGENTS.md
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - 문서가 코드와 일치하는가? (경로·식별자·문구를 실제 템플릿·라우트에서 확인)
   - GLOSSARY 규칙(코드 식별자 = 문서 용어)을 지키는가?
   - ADR 간 모순이 없는가?
3. 결과에 따라 `phases/5-scripted-demo/index.json` 의 해당 step 을 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"`
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- 코드를 고치지 마라. 이유: 이 step 은 문서 정합성만. 코드 결함을 발견하면 summary 에 적는다.
- PRD 의 합의 기록 본문을 지우거나 다시 쓰지 마라. 이유: 결정 이력.
- 기존 테스트를 깨뜨리지 마라.
