# Step 14: local-e2e

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/docs/PRD.md` — "시연 흐름" 1~7, "수용 기준 제안" 표(순차 실행, 완료 방식, 중복 방지, 연결 끊김)
- `/docs/UI_GUIDE.md` — "심사자 첫 방문 흐름" (이 시나리오를 HTTP 로 재현한다)
- `/docs/ARCHITECTURE.md` — "검증 순서와 다음 결정" 표 5행, "배포와 실행 예산"(프로세스 5개)
- `/AGENTS.md` 명령어 절 — 기동 명령 5개
- Step 5~13 의 summary (index.json) 와 `src/workflow/server/app.py`, `server/worker.py`, `diagnostic_demo/api/app.py`, `diagnostic_demo/worker/__main__.py`, `workflow/connector/cli.py`, `scripts/scaffold_demo_repo.py`

## 작업

클라우드·API 키·실제 Codex 없이 로컬에서 전체 흐름을 띄우는 스크립트와 시나리오 테스트를 만든다. 진단은 `DIAG_MODEL=fake`, B 는 가짜 codex(Step 12 테스트의 정상 스크립트를 재사용)로 돈다. 이 step 이 통과하면 "파이프라인은 맞고, 남은 것은 실제 모델·실제 Codex" 라고 말할 수 있다.

### `scripts/seed_demo.py`

```python
def seed(db_path: Path, artifact_dir: Path, *, connector_id: str | None = None, base_commit: str, now: str) -> dict
# 운영자 에이전트 2개를 repo.upsert_agent 로 등록:
#  agent-ops-demo: api, company, capabilities [{operations.diagnose, {workflow_id: daily-report}}], api_url=DIAG_API_URL, credential_ref="env:DIAG_API_TOKEN", shared_to_all_sessions=1, connection_state=online
#  agent-codex-mac: local, personal, capabilities [{code.modify, {repository_id: demo-report-repo}}], local_registration_id=local-demo-report, repository_id=demo-report-repo, base_commit, verification_profile_ids=["vp-pytest","vp-report"], shared_to_all_sessions=1, connection_state=offline
# 연결 코드 1개 발급해 반환 {"connect_code": ..., "agents": [...]}
def main(argv=None)   # --db --artifacts --base-commit [--print-code]
```

Step 16 배포 런북도 이 스크립트를 쓴다.

### `scripts/local_stack.py`

```python
class LocalStack:
    def __init__(self, workdir: Path, *, central_port=18000, diag_port=18100, fake_codex: Path | None)
    def start(self) -> None      # 순서: 데모 저장소 scaffold → 진단 API → 진단 워커(fake) → 중앙 API → seed → 중앙 워커 → connector connect/register/run
    def stop(self) -> None       # 역순 terminate, 5초 후 kill. 로그는 workdir/logs/*.log
    def __enter__/__exit__
def main(argv=None)              # 사람이 직접 띄울 때: 시작 후 URL 을 출력하고 Ctrl-C 까지 대기
```

- 각 프로세스는 AGENTS.md 명령어 그대로 `subprocess.Popen([sys.executable, "-m", "uvicorn", "workflow.server.app:app", "--port", ...])` 등. 환경변수는 `workdir` 기준 경로와 `WORKFLOW_DEV=1`, `DIAG_DEV=1`, `DIAG_MODEL=fake`, `SESSION_SECRET`·`OPERATOR_TOKEN`·`DIAG_API_TOKEN` 은 무작위 생성 후 두 서비스에 같은 값, `WORKFLOW_CONNECTOR_HOME=workdir/connector`, `PATH` 앞에 `fake_codex` 디렉터리.
- 기동 대기: `/capabilities`(진단), `/`(중앙) 가 200 이 될 때까지 최대 30초.
- connector: `connect --code`(seed 가 준 코드) → `register --id local-demo-report --repo <scaffold 경로> --repository-id demo-report-repo --verify vp-pytest="python3 -m pytest -q" --verify vp-report="python3 -m daily_report {response}"` → `run` (백그라운드).

### 가짜 codex — `tests/e2e/fake_codex.py`

Step 12 테스트의 정상 스크립트를 파일로 옮긴다: stdin 프롬프트에서 인계 디렉터리 경로를 찾아 `response-after@1.json` 을 읽고, `-C` worktree 에 `tests/test_repro_records.py`(변경 응답 재현 + 두 경로 동등성 + 모호 사례) 를 추가하고 `daily_report/transformer.py` 를 두 경로 지원으로 수정한 뒤 JSONL 3줄과 last-message 파일을 쓴다. 실제 Codex 가 아니므로 파일 첫 줄 주석에 "e2e 전용 가짜 에이전트. 데모·심사에 쓰지 않는다" 를 적는다. `tests/e2e/__init__.py` 필요.

### 시나리오 테스트 — `tests/e2e/test_scenario.py`

`@pytest.mark.skipif(os.environ.get("WORKFLOW_E2E") != "1", reason="WORKFLOW_E2E=1 일 때만")` — verify.sh 가 매 턴 도는 pytest 에서 60초 넘게 걸리지 않도록. `pyproject.toml` 에 `markers = ["e2e"]` 추가.

`httpx.Client(base_url=central, cookies=...)` 로 심사자 세션을 흉내 낸다. 폼 POST 는 Step 6 의 필드명 그대로.

1. `GET /` → Set-Cookie, "아직 업무가 없습니다.", 에이전트 2개 중 `agent-codex-mac` 가 `연결됨`(connector run 이 heartbeat 를 보낸 뒤. 최대 20초 대기).
2. `POST /tasks` (diagnose 예시 값) → 303 → 상세에 `실행 가능`.
3. `POST /tasks` (fix 예시, predecessor=A) → `대기 · 선행 대기`.
4. `POST /tasks/{A}/run` → `실행 요청됨` → (폴링 `/tasks/{A}/live`, 최대 60초) `실행 중` 을 한 번 이상 관측 → `완료` 와 `판정 근거`.
5. B `/live` 폴링(최대 120초): `실행 요청됨` → `실행 중` → `확인 필요 · 검토 대기`. 결과 카드에 `검토 가능`, 산출물 칩 `diff`·`테스트 전`·`테스트 후`·`보고서`.
6. `GET /tasks/{B}/artifacts/{test_log_before}` 에 `exit_code=1`, `{report_output}` 에 `합계    20    5`, `{diff}` 에 `transformer.py`.
7. `POST /tasks/{B}/review decision=approve` → `완료`, `병합: 운영자 확인 대기`.
8. 중복 방지: 중앙 워커에 tick 을 더 돌리기 위해 10초 대기 후 DB 를 직접 열어 B 의 Execution 이 정확히 1개.
9. 다른 세션(새 Client) 에서 `GET /tasks/{A}` → 404.
10. 데모 저장소 원본의 `main` 이 여전히 `report-base` 커밋이고 `task/<B task_id>` 브랜치에 결과 커밋이 있다.
11. connector 를 `stop` 한 뒤(프로세스만) 새 fix 업무 C(predecessor=A) 를 등록 → 100초 안에 `대기 · 연결 끊김, 마지막 확인` (heartbeat 90초 규칙. 테스트 시간을 줄이려면 stack 이 `WORKFLOW_LIMIT_HEARTBEAT_OFFLINE_SECONDS=10` 을 주고 워커가 그 값을 읽게 한다).

실패 시 `workdir/logs/` 의 5개 로그를 pytest 출력에 덧붙인다.

### 테스트 — `scripts/test_seed_demo.py`, `scripts/test_local_stack.py`

- seed: 에이전트 2개 등록, 연결 코드 교환 가능, 두 번 실행해도 2개(멱등).
- local_stack: 단위 수준 — 명령 배열이 AGENTS.md 의 모듈 경로를 쓴다(`workflow.server.app:app` 등), 환경변수에 비밀값이 채워지고 두 서비스의 `DIAG_API_TOKEN` 이 같다. 실제 기동은 e2e 에서.

### GLOSSARY

`LocalStack`(로컬 5-프로세스 기동기. 심사 배포가 아니다), `fake codex`(e2e 전용 가짜 에이전트) 를 추가한다.

## Acceptance Criteria

```bash
python3 -m pytest scripts/test_seed_demo.py scripts/test_local_stack.py -q
python3 -m pytest -q                                   # e2e 는 건너뜀
WORKFLOW_E2E=1 python3 -m pytest tests/e2e -q -x       # 전체 시나리오 (수 분)
python3 -m ruff check .
```

## 검증 절차

1. 위 AC 커맨드를 실행한다. e2e 가 실패하면 로그를 읽고 해당 step 의 코드를 고친다 — 이 step 은 통합 결함을 잡는 자리다. 고친 파일이 어느 step 것인지 summary 에 적는다.
2. 체크리스트:
   - 실제 Codex·OpenAI 가 호출되지 않았는가? (`PATH` 의 fake_codex, `DIAG_MODEL=fake`)
   - A 완료가 검증기 판정으로, B 착수가 워커 스캔으로 일어났는가 (사람 조작 없이)?
   - 원본 저장소 `main` 불변.
3. `phases/0-mvp/index.json` 의 step 14 를 업데이트한다 (summary 에 e2e 실행 명령, 걸린 시간, 고친 통합 결함 목록).

## 금지사항

- 시나리오를 통과시키려고 검증기·상태 규칙을 완화하지 마라. 이유: 원칙. 완화가 필요하면 `error_message` 에 적고 멈춘다.
- fake codex 를 제품 코드(`src/`)에 두지 마라. 이유: 데모·심사에 섞이면 원칙 위반.
- e2e 를 기본 pytest 에 포함하지 마라. 이유: verify.sh 가 매 턴 돈다.
- 기존 테스트를 깨뜨리지 마라.
