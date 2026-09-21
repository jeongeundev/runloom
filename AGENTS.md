# 프로젝트: workflow — 기존 에이전트 등록과 업무 자동 실행

> Codex(`scripts/execute.py` 실행 엔진)와 Claude Code(`CLAUDE.md` 가 이 파일을 import)가 함께 읽는 프로젝트 규칙.
> execute.py 가 매 step 프롬프트에 주입하므로, 여기 적은 내용은 모든 step 세션에 전달된다. 이 파일 외의 문서는 주입하지 않는다 — 필요한 문서·절은 각 step.md 의 "읽어야 할 파일" 절이 가리키고, 세션이 그때 읽는다.
> 결정 기록은 `docs/adr/`, 설계는 `docs/ARCHITECTURE.md`, 계약 예시는 `docs/CONTRACT.md`, 용어는 `docs/GLOSSARY.md`.

## 기술 스택
- Python 3.13 계열, 시스템 `python3` 사용 (가상환경 없음). [ADR-0002](docs/adr/0002-server-stack-python-fastapi-sqlite.md)
- FastAPI + Uvicorn (웹/API), Jinja2 + CSS + 소량 브라우저 JavaScript (화면), Pydantic v2 (계약 검증), 표준 `sqlite3` + 명시적 SQL (저장), HTTPX (HTTP 클라이언트)
- 진단 데모: OpenAI Python SDK, Responses API — [ADR-0003](docs/adr/0003-diagnosis-model-openai-gpt41-mini.md) 확정 (gpt-4.1). 키·예산 확인 전 유료 호출 금지
- 로컬 에이전트 어댑터: Codex CLI 1종 [ADR-0001](docs/adr/0001-first-local-agent-codex.md)
- pytest, ruff. 의존성은 `pyproject.toml`

## 아키텍처 규칙
- CRITICAL: 도메인 규칙(`src/workflow/domain/`)은 FastAPI·sqlite3·HTTPX·subprocess·Git 을 import 하지 않는다. DB·HTTP·프로세스·Git 은 `adapters/`·`server/`·`connector/` 경계 모듈에서만 다룬다.
- CRITICAL: `src/workflow/server/` 와 `src/workflow/connector/` 는 서로 import 하지 않고 `src/workflow/contracts/` 만 공유한다. `src/diagnostic_demo/` 는 중앙 DB 에 접근하지 않고 공개 계약만 공유한다.
- CRITICAL: 외부 입력(요청 본문·산출물·근거 문서·모델 응답)에서 셸 명령이나 파일 경로를 받아 실행하지 않는다. 실행 파일과 인자 배열은 어댑터가 고정한다.
- CRITICAL: 비밀값(연결 토큰 `wfc_…`, `OPERATOR_TOKEN`, `DIAG_API_TOKEN`, `OPENAI_API_KEY`, `SESSION_SECRET`)은 환경변수에서만 읽는다. DB·로그·응답·템플릿·Codex 프로세스 환경에 넣지 않는다.
- 상태 전환·중복 방지·완료 판정은 `docs/ARCHITECTURE.md` 계약 v1 과 `docs/CONTRACT.md` 예시를 따른다. 모델의 "완료했다" 응답이나 프로세스 종료 코드만으로 완료 처리하지 않는다.
- 이름은 `docs/GLOSSARY.md` 의 코드 식별자를 그대로 쓴다 (`Execution` ≠ `run`, `Agent` ≠ `connector`, `outcome` ≠ 상태).
- 테스트 배치: `tests/` 가 `src/` 구조를 따라간다. `src/workflow/domain/selection.py` → `tests/workflow/domain/test_selection.py`. `tdd-guard.sh` 가 이 배치를 인식한다.

## 제품 코드와 진단 데모의 경계
- `src/workflow/` 는 제품(중앙 웹/API·워커·연결 프로그램·계약). `src/diagnostic_demo/` 는 "사내 운영 진단 API" 역할을 재현하는 데모 서비스이며 제품의 일부가 아니다.
- B 가 수정하는 보고서 데모 저장소는 이 저장소 밖에 별도 Git 저장소로 둔다.
- 진단 fixture(가상 실행 기록·로그·운영 문서)는 `src/diagnostic_demo/fixtures/` 에 두고 실제 운영 데이터로 표시하지 않는다.
- `src/workflow/scripted/` 는 공개 데모 전용 대본 에이전트다([ADR-0008](docs/adr/0008-public-demo-scripted-agents.md)). 제품 런타임 경로(`connector/`)에서 import 하지 않는다 — 배포·로컬 스택이 `codex`/`claude` 이름의 PATH 래퍼로 앞에 둘 뿐이다.

## 개발 프로세스
- CRITICAL: 새 기능 구현 시 반드시 테스트를 먼저 작성하고, 테스트가 통과하는 구현을 작성할 것 (TDD)
- 커밋 메시지는 conventional commits 형식을 따를 것 (feat:, fix:, docs:, refactor:)

## 명령어
```bash
python3 -m pip install -e ".[dev]"                               # 의존성 설치 — src/workflow, src/diagnostic_demo 패키지가 있어야 함
python3 -m uvicorn workflow.server.app:app --reload --port 8000  # 개발 서버: 중앙 웹/API
python3 -m workflow.server.worker                                 # 중앙 워커
python3 -m uvicorn diagnostic_demo.api.app:app --port 8100       # 진단 API (localhost 전용)
python3 -m diagnostic_demo.worker                                 # 진단 워커
python3 -m workflow.connector                                     # 로컬 연결 프로그램 (운영자 Mac)
                                                                  # 빌드: 없음
python3 -m ruff check .                                           # 린트
python3 -m pytest -q                                              # 테스트 — scripts/ + tests/ (pyproject testpaths)
python3 -m pytest scripts/ -q                                     # 하네스 테스트만
```

> `scripts/hooks/verify.sh` 는 소스가 바뀐 턴에 `python3 -m pytest -q` 와 `python3 -m ruff check .` 를 실행한다. 2026-09-20 `scripts/test_hooks.py` 로 감지를 확인했다.

## 하네스 (scripts/)
`scripts/execute.py` 는 `phases/{task-name}/` 의 step 을 순차 실행하는 자가 교정 하네스다. 각 step 을 독립된 codex 세션에 위임한다:

```
codex exec --json --dangerously-bypass-approvals-and-sandbox <prompt>
```

- `python3 scripts/execute.py {task-name} [--push] [--engine codex|claude]` 로 실행한다. 워크플로우 전체는 `.claude/commands/harness.md` 참고. 2026-09-20 기준 Codex 사용량이 거의 남지 않아 `--engine claude` 로 실행한다.
- `--dangerously-bypass-approvals-and-sandbox` 는 승인 프롬프트·샌드박스를 건너뛴다 (자동화 전용). 외부에서 통제된 환경에서만 쓴다.
- codex 가 사용량 한도로 실패하면 같은 step 을 `claude -p --dangerously-skip-permissions --strict-mcp-config` 로 재실행하고, 그 실행의 남은 step 도 claude 로 돌린다. `--strict-mcp-config` 는 전역 MCP 를 물지 않게 한다 (step 세션은 내장 도구만 쓴다). 어느 엔진이 돌았는지는 `step{N}-output.json` 의 `engine` 에 남는다.
- step 산출물은 `phases/{task-name}/step{N}-output.json`, 진행 상태는 `index.json` 에 기록된다.
- 하네스 스크립트 자체를 수정할 때는 `python3 -m pytest scripts/` 를 green 으로 유지한다.

## 훅 (scripts/hooks/)
판정 규칙은 `tdd-guard.sh`(테스트 없는 소스 작성 차단, `tests/` 미러 배치 인식)와 `verify.sh`(변경된 턴에 TDD 불변식 + pytest + ruff) 두 곳에만 있다.

- Claude Code: `.claude/settings.json` 이 위 둘을 직접 건다.
- Codex: `codex-block-dangerous.sh` / `codex-tdd-guard.sh` / `codex-verify-gate.sh` 가 codex 입출력 형식(apply_patch 패치 텍스트, `decision: block`)을 위 둘로 변환한다. 이 파일들은 `~/.codex/hooks.json`(전역)이 저장소 루트에서 찾아 실행하므로 프로젝트에 `.codex/hooks.json` 을 두지 않는다 — 두면 이중 실행된다. 실행 권한(`chmod +x`)이 없으면 전역 훅이 건너뛴다.
