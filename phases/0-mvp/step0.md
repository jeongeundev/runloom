# Step 0: project-setup

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/AGENTS.md` — 기술 스택, CRITICAL 규칙, 명령어, 테스트 배치 규칙
- `/docs/ARCHITECTURE.md` — "디렉터리와 의존 방향 제안" 절
- `/docs/adr/` (하위 파일 전부)
- `/docs/GLOSSARY.md`
- `/pyproject.toml` — 의존성과 pytest·ruff 설정. `packages = ["src/workflow", "src/diagnostic_demo"]` 이므로 두 패키지가 있어야 설치된다
- `/scripts/hooks/tdd-guard.sh` — 어떤 파일에 테스트를 요구하는지 (`__init__.py`, `__main__.py`, `conftest.py`, `.json/.css/.md/.html` 은 예외)

## 작업

제품 코드는 아직 한 줄도 없다. 이 step 은 패키지 뼈대와 테스트 배치를 만들고 설치·테스트·린트가 도는 상태를 만든다. 기능 코드는 쓰지 않는다.

### 1. 패키지 뼈대

아래 디렉터리와 빈 `__init__.py` 를 만든다. 모듈 파일은 만들지 않는다 (해당 step 에서 테스트와 함께 만든다).

```text
src/workflow/__init__.py
src/workflow/contracts/__init__.py
src/workflow/domain/__init__.py
src/workflow/adapters/__init__.py
src/workflow/server/__init__.py
src/workflow/connector/__init__.py
src/diagnostic_demo/__init__.py
src/diagnostic_demo/api/__init__.py
src/diagnostic_demo/worker/__init__.py
src/diagnostic_demo/tools/__init__.py
src/diagnostic_demo/fixtures/README.md      # "가상 데모 자료. 실제 운영 데이터가 아님" 한 줄
```

### 2. 테스트 배치

`tests/` 는 `src/` 를 미러링한다. 같은 파일명(`test_app.py`)이 여러 패키지에 생기므로 모든 tests 디렉터리에 `__init__.py` 를 둔다 (pytest 모듈 이름 충돌 방지).

```text
tests/__init__.py
tests/conftest.py                 # 비워 두거나 공용 fixture 자리만
tests/test_packages.py            # 아래 스모크 테스트
tests/workflow/__init__.py
tests/workflow/contracts/__init__.py
tests/workflow/domain/__init__.py
tests/workflow/adapters/__init__.py
tests/workflow/server/__init__.py
tests/workflow/connector/__init__.py
tests/diagnostic_demo/__init__.py
tests/diagnostic_demo/api/__init__.py
tests/diagnostic_demo/worker/__init__.py
tests/diagnostic_demo/tools/__init__.py
```

`tests/test_packages.py` 는 다음을 확인한다:

- `import workflow`, `import diagnostic_demo` 가 된다.
- `workflow.domain` 패키지 소스에 `fastapi`, `sqlite3`, `httpx`, `subprocess` 를 import 하는 줄이 없다 (AGENTS.md CRITICAL 규칙의 회귀 테스트. 지금은 빈 패키지라 자명하게 통과하지만 이후 step 을 지키게 한다). `src/workflow/domain/**/*.py` 를 읽어 정규식으로 검사하면 된다.
- `workflow.server` 소스가 `workflow.connector` 를, `workflow.connector` 소스가 `workflow.server` 를 import 하지 않는다. `diagnostic_demo` 소스가 `workflow.adapters`·`workflow.server` 를 import 하지 않는다 (`workflow.contracts` 만 허용).

### 3. 설치와 무시 목록

- `python3 -m pip install -e ".[dev]"` 를 실행해 editable 설치한다. 실패하면 원인을 `error_message` 에 적는다.
- `.gitignore` 에 `*.sqlite`, `*.sqlite-wal`, `*.sqlite-shm`, `data/` 를 추가한다.
- `pyproject.toml` 은 건드리지 않는다. 설치가 실패하는 경우에만 최소 수정하고 이유를 summary 에 남긴다.

### 4. GLOSSARY

새 용어가 없으므로 수정하지 않는다.

## Acceptance Criteria

```bash
python3 -m pip install -e ".[dev]"                         # 설치 성공
python3 -c "import workflow, diagnostic_demo"              # 두 패키지 import
python3 -m pytest -q                                       # scripts/ + tests/ 모두 통과
python3 -m ruff check .                                    # 린트 통과
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - ARCHITECTURE.md 디렉토리 구조를 따르는가?
   - ADR 기술 스택을 벗어나지 않았는가? (새 의존성을 추가하지 않았는가)
   - AGENTS.md CRITICAL 규칙을 위반하지 않았는가?
3. 결과에 따라 `phases/0-mvp/index.json` 의 step 0 을 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary"`: 만든 디렉터리 목록과 설치 결과 한 줄
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason"` 후 즉시 중단

## 금지사항

- 기능 모듈(`app.py`, `models.py` 등)을 미리 만들지 마라. 이유: 테스트 없는 소스는 tdd-guard 에 막히고, 각 step 이 테스트와 함께 만든다.
- `scripts/execute.py`, `scripts/hooks/` 를 수정하지 마라. 이유: 하네스는 이 실행의 대상이 아니다.
- `docs/` 를 수정하지 마라. 이유: 이 step 에서 바뀌는 설계 결정이 없다.
- 기존 테스트(`scripts/test_*.py`)를 깨뜨리지 마라.
