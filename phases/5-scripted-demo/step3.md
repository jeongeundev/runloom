# Step 3: task-sources

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/AGENTS.md`(CRITICAL: 도메인은 FastAPI·sqlite3·HTTPX 를 import 하지 않는다), `/docs/ARCHITECTURE.md`, `/docs/adr/` 전부, `/docs/GLOSSARY.md`(`Task`, `capability`, `required_capability`, `run`/`run_id`, `workflow_id`, `example`)
- `/src/workflow/contracts/v1.py` — `Capability`(code `operations.diagnose` scope `workflow_id` / `code.modify` scope `repository_id`)
- `/src/workflow/domain/defaults.py` — `kind_for_capability`, `/src/workflow/domain/selection.py`
- `/src/workflow/server/web.py` — `EXAMPLES`(진단·수정 예시의 제목·요청 문구 — 가져오기 이슈의 요청문은 이걸 재사용한다), `SCOPE_KEYS`
- `/src/diagnostic_demo/fixtures/index.json`, `/src/diagnostic_demo/fixtures/README.md` — 실패 run `daily-0920-0900`, 정상 run `daily-0919-0900`
- `/tests/workflow/domain/test_selection.py`, `/tests/workflow/adapters/test_diag_client.py`(어댑터 테스트 스타일)
- `/phases/5-scripted-demo/step0.md` 의 "배경" 절

이전 step 에서 만들어진 코드를 꼼꼼히 읽고, 설계 의도를 이해한 뒤 작업하라.

## 작업

심사자 흐름의 2단계 "업무 가져오기"의 재료. 출처(GitHub·Jira)는 **fixture** 다 — 실제 API 를 부르지 않는다(2026-09-21 사용자 확정: 대본 에이전트가 낼 수 있는 결과는 정해져 있으므로 가져올 이슈도 그 시나리오의 것이어야 한다). 화면에는 "시연 데이터"로 표시한다(step 5).

### `src/workflow/domain/task_sources.py` (신규, 순수)

```python
@dataclass(frozen=True)
class Issue:
    source: Literal["github", "jira"]
    key: str            # "#41" / "OPS-41" — 화면·Task.source_ref 에 그대로
    title: str
    body: str           # Task.request 가 된다
    labels: tuple[str, ...]
    blocked_by: tuple[str, ...]   # 같은 출처의 key 들
    url: str | None     # fixture 는 None (가짜 외부 링크를 만들지 않는다)

@dataclass(frozen=True)
class IssueMapping:
    capability: Capability | None   # None 이면 이 제품의 에이전트가 맡을 수 없는 이슈
    run_id: str | None              # 진단이면 필수
    reason: str                     # 사람이 읽는 매핑 이유 (예: "라벨 incident·workflow:daily-report → operations.diagnose")

def map_issue(issue: Issue) -> IssueMapping
```

매핑 규칙(라벨 기반, 명시적 비교만 — ADR-0004):
- `incident` + `workflow:<id>` → `operations.diagnose {workflow_id: <id>}`, `run_id` 는 라벨 `run:<run_id>` 에서. `run:` 라벨이 없으면 `capability=None`, reason "run 라벨 없음".
- `bug` + `repo:<repository_id>` → `code.modify {repository_id: <repository_id>}`.
- 그 외 → `capability=None`, reason "맞는 능력 코드 없음 (라벨: …)".
- 라벨 대소문자는 그대로 비교한다(정규화 없음).

### `src/workflow/adapters/task_sources.py` (신규) + fixture

- `src/workflow/adapters/task_source_fixtures/github.json`, `jira.json` — 각 4개 이슈. 두 파일의 이슈는 같은 사건을 두 도구 형식으로 표현한 것이라 내용은 같고 key 형식만 다르다(`#41`… / `OPS-41`…):
  1. `#41` "일일 보고서 생성 실패 (09-20 09:00)" — labels `incident`, `workflow:daily-report`, `run:daily-0920-0900`. body 는 `EXAMPLES["diagnose"]["request"]` 와 같은 문장.
  2. `#42` "집계 API 응답 형식 변경 대응" — labels `bug`, `repo:demo-report-repo`, blocked_by `#41`. body 는 `EXAMPLES["fix"]["request"]`.
  3. `#43` "변경 응답 형식 모니터링 알림 추가" — labels `enhancement`, `repo:demo-report-repo`, blocked_by `#42`. (능력 없음 → 체인에 못 들어가는 예)
  4. `#44` "README 오타 수정" — labels `docs`. (관련 없는 이슈)
- `load_issues(source: str) -> list[Issue]` — 패키지 안 fixture 파일을 읽는다(`importlib.resources` 또는 `Path(__file__).parent`). 모르는 source → `ValueError`. 파일 내용은 시작 시 한 번 검증(필수 키, blocked_by 가 같은 파일의 key 를 가리킴).
- `SOURCES = ("github", "jira")`, `SOURCE_LABELS = {"github": "GitHub Issues", "jira": "Jira"}`.

### 테스트 (먼저 작성)

- `tests/workflow/domain/test_task_sources.py`: 규칙 표 — incident+workflow+run → diagnose; run 없음 → None; bug+repo → modify; docs → None; 이유 문구; `Capability` 검증 통과(scope 키).
- `tests/workflow/adapters/test_task_sources.py`: 두 출처 각 4개; key 형식; `blocked_by` 참조 무결; `#41→diagnose`, `#42→modify`, `#43`·`#44→None`; 모르는 source ValueError; `url` 은 전부 None.

## Acceptance Criteria

```bash
python3 -m pytest tests/workflow/domain/test_task_sources.py tests/workflow/adapters/test_task_sources.py -q
python3 -m pytest -q
python3 -m ruff check .
grep -c "\"key\"" src/workflow/adapters/task_source_fixtures/github.json   # 4
grep -rn "import fastapi\|import sqlite3\|import httpx" src/workflow/domain/ && exit 1 || echo "도메인 경계 OK"
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - ARCHITECTURE.md 디렉토리 구조를 따르는가? (`domain/` 순수, fixture 읽기는 `adapters/`)
   - 외부 의존 표에 없는 외부 서비스를 끌어들이지 않았는가? (GitHub·Jira API 호출 없음)
   - ADR 기술 스택을 벗어나지 않았는가?
   - AGENTS.md CRITICAL 규칙을 위반하지 않았는가? (이슈 본문에서 명령·경로를 실행하지 않는다 — 본문은 `Task.request` 문자열일 뿐)
   - GLOSSARY.md 용어를 그대로 썼는가? (`Issue` 는 외부 출처 항목, `Task` 와 구분 — step 11 이 GLOSSARY 에 넣는다)
3. 결과에 따라 `phases/5-scripted-demo/index.json` 의 해당 step 을 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"`
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- GitHub·Jira API 를 호출하는 코드(HTTPX)를 넣지 마라. 이유: fixture 확정, 공개 트래픽의 한도·비용.
- 자유 문장(제목·본문)에서 능력을 추론하지 마라. 이유: ADR-0004 — 라벨의 명시적 비교만.
- fixture 이슈에 실제 저장소·회사·사람 이름을 넣지 마라. 이유: 가상 데모 자료.
- `server/` 를 건드리지 마라. 이유: 화면은 step 5.
- 기존 테스트를 깨뜨리지 마라.
