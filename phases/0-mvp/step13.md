# Step 13: demo-report-repo

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/docs/PRD.md` — "사건과 입력 계약", "기대 수정 결과와 보고서"(보고서 형식, 검증 표), "실행 이력과 실패 로그"(`MISSING_RECORDS_FIELD`)
- `/docs/ARCHITECTURE.md` — "보고서 데모 | 별도 Python Git 저장소, pytest", "데모 저장소 | 운영자 Mac | 기준 커밋 고정 clone"
- `/AGENTS.md` — "B 가 수정하는 보고서 데모 저장소는 이 저장소 밖에 별도 Git 저장소로 둔다"
- `/src/workflow/connector/codex.py` (Step 12) — 어댑터가 기대하는 저장소 구조: `python3 -m pytest -q`, `python3 -m daily_report <response.json>`
- `/tests/workflow/connector/test_codex.py` 의 `make_repo` — 같은 구조여야 한다

## 작업

B 가 수정할 데모 저장소를 **생성하는 스크립트** `scripts/scaffold_demo_repo.py` 를 만든다. 저장소 자체는 이 트리 밖(기본 `../demo-report-repo`)에 만들어지며 커밋되지 않는다. 파일 내용은 스크립트 안의 문자열 상수로 둔다 (템플릿 디렉터리를 두면 이 저장소의 pytest·tdd-guard 가 잡는다).

### 생성되는 저장소

```text
demo-report-repo/
  README.md                  # "가상 데모 저장소. 일일 보고서 자동화의 변환부. 실제 서비스가 아니다." + 실행 방법
  AGENTS.md                  # 저장소 규칙: TDD(테스트 먼저), `python3 -m pytest -q`, 변환 계약은 docs/contract.md 참조, 커밋하지 말 것(연결 프로그램이 한다)
  docs/contract.md           # PRD "입력 계약": 두 경로 지원, 정확히 하나, 빈 배열 0건, 누락·비배열·동시 존재 오류. **수정 전 코드는 이 계약을 아직 구현하지 않았다** 고 명시
  pyproject.toml             # name daily-report-demo, pytest testpaths=["tests"], pythonpath=["."]
  .gitignore                 # __pycache__, .pytest_cache, *.txt 출력
  daily_report/__init__.py
  daily_report/transformer.py
  daily_report/report.py
  daily_report/__main__.py
  tests/__init__.py
  tests/test_transformer.py
  tests/test_report.py
```

`transformer.py` (수정 전, 의도적으로 `items` 만 지원):

```python
class TransformError(Exception):
    def __init__(self, code: str, detail: str = ""): ...
@dataclass(frozen=True)
class Row: team: str; completed: int; pending: int
@dataclass(frozen=True)
class Report: report_date: str; rows: tuple[Row, ...]
def transform(response: dict) -> Report
    # response["items"] 가 list 가 아니면 TransformError("MISSING_RECORDS_FIELD", f"expected_path=$.items observed_root_keys={sorted(response)}")
    # 각 행 team(str), completed(int>=0), pending(int>=0) 검증 → TransformError("INVALID_ROW")
    # report_date 누락 → TransformError("MISSING_REPORT_DATE")
```

`report.py`: `render(report: Report) -> str` — PRD 형식 정확히:

```text
일일 업무 보고서 — 2026-09-19

팀      완료  미완료
운영    12    3
개발    8     2
합계    20    5
```

열 정렬은 공백이며 판정은 값 기준이다. 행이 없으면 팀 행 없이 `합계    0     0`.

`__main__.py`: `python3 -m daily_report <response.json>` → stdout 에 보고서, exit 0. `TransformError` 면 stderr 에 `ERROR stage=transform component=report_transformer code={code} {detail}` 한 줄, exit 1. 파일 없음·JSON 오류 exit 2.

`tests/test_transformer.py`: `items` 형식 정상, 행 검증 오류, `report_date` 누락. **`data.records` 케이스는 없다** (B 가 추가한다). `tests/test_report.py`: 렌더 형식·합계.

### 스크립트

```python
def scaffold(path: Path, *, force: bool = False) -> str      # 디렉터리 생성, 파일 쓰기, git init -b main, user 설정(로컬), 커밋 "chore: report-base (수정 전 기준)", 태그 report-base. 반환 HEAD SHA. 이미 있고 force 아니면 FileExistsError
def main(argv=None)      # scaffold_demo_repo.py [PATH] [--force]. 기본 PATH 는 이 저장소의 부모/demo-report-repo. 마지막 줄에 "base_commit={sha}" 출력
```

### 테스트 — `scripts/test_scaffold_demo_repo.py`

- `scaffold(tmp_path/"r")` 후: git 저장소이고 커밋 1개, 태그 `report-base`, 반환 SHA == HEAD.
- 생성된 저장소에서 `subprocess.run([sys.executable, "-m", "pytest", "-q"], cwd=repo)` 가 exit 0.
- `python3 -m daily_report` 에 PRD 의 `response-before` 파일 → exit 0, stdout 에 `합계    20    5` 와 `2026-09-18`.
- `response-after` 파일 → exit 1, stderr 에 `MISSING_RECORDS_FIELD` 와 `expected_path=$.items`.
- 빈 `items: []` → 합계 0 0. `report_date` 없음 → exit 1 `MISSING_REPORT_DATE`.
- 두 번째 `scaffold` 는 `FileExistsError`, `force=True` 면 다시 만든다.
- README·AGENTS.md 에 "가상"·"실제 서비스가 아니다" 문구.
- (Step 12 정합) `tests/workflow/connector/test_codex.py` 의 `make_repo` 를 이 스크립트의 `scaffold` 로 바꾸고 그 테스트가 여전히 통과한다.

### GLOSSARY

`demo-report-repo`(B 가 수정하는 별도 저장소. `repository_id` 값과 같다. 기준 커밋 태그 `report-base`) 를 추가한다.

## Acceptance Criteria

```bash
python3 -m pytest scripts/test_scaffold_demo_repo.py -q
python3 -m pytest -q
python3 -m ruff check .
python3 scripts/scaffold_demo_repo.py /tmp/demo-report-repo-check --force && rm -rf /tmp/demo-report-repo-check
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 체크리스트:
   - 저장소가 이 트리 밖에 생성되는가? 이 트리에 `demo-report-repo/` 가 생기지 않았는가?
   - 수정 전 코드가 `data.records` 를 처리하지 않는가? (B 의 일이 남아 있어야 한다)
   - 보고서 형식이 PRD 와 같은가?
3. `phases/0-mvp/index.json` 의 step 13 을 업데이트한다 (summary 에 스크립트 경로, 기본 생성 위치, base_commit 얻는 법).

## 금지사항

- 수정 후 코드(두 경로 지원)를 미리 넣지 마라. 이유: B 가 재현·수정할 결함이 있어야 한다.
- 생성된 저장소를 이 저장소에 커밋하지 마라. 이유: AGENTS.md 경계.
- 실제 기업명·URL 을 넣지 마라.
- 기존 테스트를 깨뜨리지 마라.
