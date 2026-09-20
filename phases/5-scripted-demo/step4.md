# Step 4: composition-rules

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/AGENTS.md`, `/docs/ARCHITECTURE.md`, `/docs/adr/` 전부(특히 ADR-0004 중앙 규칙 기반), `/docs/GLOSSARY.md`(`Candidate`, `SelectionRecord`, `Criterion`), `/docs/PRD.md` 2절 "업무 등록과 연결"·4절 "완료 처리와 후속 업무 시작"
- `/src/workflow/domain/selection.py` — `select_agent`, `Candidate`
- `/src/workflow/domain/defaults.py` — `default_run_mode`, `default_completion_mode`, `kind_for_capability`
- `/src/workflow/domain/completion.py` — `criteria_template`, `can_auto_complete`
- `/src/workflow/domain/task_sources.py` — step 3 의 `Issue`, `map_issue`, `IssueMapping`
- `/src/workflow/contracts/v1.py` — `Capability`, `SelectionRecord`
- `/tests/workflow/domain/test_selection.py`, `/tests/workflow/domain/test_defaults.py`

이전 step 에서 만들어진 코드를 꼼꼼히 읽고, 설계 의도를 이해한 뒤 작업하라.

## 작업

심사자 흐름의 3단계 "워크플로우 자동 구성". 가져온 이슈(또는 직접 등록한 Task)의 순서를 배치하고 각 단계에 Agent 를 배정한다. **규칙 기반이며 LLM 을 쓰지 않는다**(ADR-0004). 그래서 모든 결정에 사람이 읽는 `reason` 이 붙는다.

### `src/workflow/domain/selection.py` — 동률 규칙 추가

```python
def select_agent(task_id, required, candidates, mode="auto", chosen_agent_id=None,
                 prefer: Sequence[str] | None = None) -> SelectionRecord
```
- `mode="auto"` 에서 일치 후보가 2개 이상이고 `prefer`(agent_id 우선순위 — 세션이 먼저 등록한 순서)가 주어지면 `prefer` 에서 가장 앞에 오는 일치 후보를 `status="selected"`, `candidate_count=1`, `reason=f"{code} · {scope} 일치 후보 {n}개 — 먼저 등록한 {agent_id} 를 기본 선택 (변경 가능)"` 으로 돌려준다. `prefer` 가 없거나 일치 후보가 `prefer` 에 없으면 지금처럼 `needs_selection`.
- 기존 호출(`prefer` 없음)의 동작은 바뀌지 않는다.

### `src/workflow/domain/composition.py` (신규, 순수)

```python
@dataclass(frozen=True)
class PlanNode:
    issue: Issue
    capability: Capability
    kind: Literal["diagnosis", "code_change"]
    run_id: str | None
    predecessor_key: str | None      # 체인 안의 바로 앞 노드 key
    run_mode: Literal["manual", "auto"]
    completion_mode: Literal["auto", "review"]
    criteria: tuple[Criterion, ...]
    selection: SelectionRecord       # task_id 는 아직 없으므로 issue.key 를 임시 task_id 로 넣는다 (server 가 삽입 시 다시 계산한다)
    reasons: tuple[str, ...]         # 순서·방식·배정의 이유 문장들

@dataclass(frozen=True)
class Standalone:
    issue: Issue
    mapping: IssueMapping            # capability None 이거나 체인에 못 들어간 이유
    reason: str

@dataclass(frozen=True)
class ChainPlan:
    nodes: tuple[PlanNode, ...]      # 실행 순서
    standalone: tuple[Standalone, ...]
    human_gate: str                  # 마지막 노드 뒤 사람 단계 문구, 예 "검토 승인 · 병합은 운영자 확인"
    title: str                       # 예 "일일 보고서 실패 → 수정" (첫·끝 노드 제목에서)

def compose(issues: Sequence[Issue], candidates: Sequence[Candidate], prefer: Sequence[str]) -> ChainPlan
```

규칙(이 순서대로, 각 단계가 `reasons` 문장을 남긴다):
1. **매핑**: `map_issue` 로 능력을 정한다. `capability=None` 이면 `Standalone`(reason = mapping.reason).
2. **순서**: `blocked_by` 로 위상 정렬. 순환이 있으면 `ValueError("의존 순환: …")`. `blocked_by` 가 Standalone(능력 없음)을 가리키면 그 의존은 무시하고 reason 에 "선행 #43 은 이 제품의 에이전트가 맡지 않아 건너뜀" 을 남긴다.
3. **체인 구성**: 능력 순서가 `operations.diagnose → code.modify` 인 인접 쌍만 선행-후속으로 잇는다. 첫 노드는 선행 없음. `code.modify → code.modify` 처럼 인계 자료가 정의되지 않은 쌍은 잇지 않고 뒤 노드를 Standalone 으로 보낸다(reason "진단 → 코드 수정 인계만 지원"). 결과 체인은 이 phase 에서 최대 2노드다 — 그래도 함수는 일반화된 순서 규칙으로 쓴다.
4. **방식**: `run_mode = default_run_mode(has_predecessor)`(첫 노드 직접, 후속 자동). `completion_mode`: 진단이고 `can_auto_complete(kind)` 면 `auto`(후속이 사람 조작 없이 착수하도록), 그 외 `review`. `criteria = criteria_template(kind)`.
5. **배정**: `select_agent(issue.key, capability, candidates, prefer=prefer)`. 후보 0개면 `needs_selection` 그대로 두고 reason "후보 없음 — 에이전트를 등록하거나 직접 지정".
6. `human_gate`: 마지막 노드가 `review` 면 "검토 승인 (사람) · 병합은 운영자 확인", 아니면 "완료 확인 (사람)".
7. `title`: `f"{첫 노드 제목} → {끝 노드 제목}"`, 노드 1개면 그 제목.

### 테스트 (먼저 작성) — `tests/workflow/domain/test_composition.py`, `test_selection.py` 추가분

- step 3 fixture 와 같은 4개 이슈 + 후보 3개(진단 API, Codex, Claude — Codex 가 먼저 등록) → 노드 2개 `#41 → #42`, Standalone 2개(`#43` 능력 없음, `#44` 능력 없음), `#41` 직접·자동 완료·운영 진단 배정(후보 1개), `#42` 자동 실행·검토 후 완료·Codex 기본 선택(reason 에 "후보 2개"·"먼저 등록한"), human_gate 문구, title.
- 후보에 Codex 만 있으면 `#42` reason 은 "일치 후보 1개".
- 진단 후보 없음 → `#41` needs_selection, reason "후보 없음".
- 순환 의존 → ValueError. `blocked_by` 가 Standalone 을 가리키면 건너뜀 reason.
- `select_agent(prefer=…)` 동률 규칙과 기존 동작 불변.

## Acceptance Criteria

```bash
python3 -m pytest tests/workflow/domain -q
python3 -m pytest -q
python3 -m ruff check .
grep -rn "import fastapi\|import sqlite3\|import httpx\|import subprocess" src/workflow/domain/ && exit 1 || echo "도메인 경계 OK"
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - ARCHITECTURE.md 디렉토리 구조를 따르는가? (순수 도메인)
   - ADR-0004 를 지키는가? (점수·모델 판단 없음, 명시적 규칙과 이유 문장만)
   - AGENTS.md CRITICAL 규칙을 위반하지 않았는가?
   - GLOSSARY.md 용어를 그대로 썼는가? (`Chain`, `PlanNode` 는 새 용어 — step 11 등록)
3. 결과에 따라 `phases/5-scripted-demo/index.json` 의 해당 step 을 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"`
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- 후보를 점수·최근 사용·모델로 줄이지 마라. 이유: ADR-0004 와 `selection.py` 의 원칙 — 동률은 "먼저 등록한 순서"라는 명시적 규칙 하나만 추가한다.
- 새 업무 종류(`kind`)를 만들지 마라. 이유: 이 phase 범위 밖. 체인은 진단 → 코드 수정 인계만 잇는다.
- DB·HTTP 를 import 하지 마라. 이유: AGENTS.md CRITICAL.
- 기존 테스트를 깨뜨리지 마라.
