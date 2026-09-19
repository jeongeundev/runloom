# Step 11: connector-core

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/docs/ARCHITECTURE.md` — "등록·선택·권한"(연결 코드 교환, 능력 설명 제안, 설정 파일 업로드 금지), "인증·권한·비밀정보 규칙"(토큰 0600, 환경변수 허용 목록, `wfc_`·`sk-` 마스킹), "상태·재접속·완료"(로컬 기록 `accepted/launching/running/finished`, 미전송 이벤트 보존), "연결 프로그램 + Codex" 행(데이터 위치)
- `/docs/CONTRACT.md` — 2절(claim), 3절(이벤트 5종, 오류표의 `sequence_gap` 재전송), 4절(업로드·다운로드)
- `/src/workflow/contracts/v1.py` (Step 1), `/src/workflow/server/machine_api.py` (Step 5 — 호출 대상. import 하지 않는다)

## 작업

운영자 Mac 에서 도는 연결 프로그램의 코어를 `src/workflow/connector/` 에 만든다: 상태 저장, 중앙 API 클라이언트, 마스킹, 실행 루프. Codex·Git 은 Step 12 (`ExecutionAdapter` 프로토콜만 정의). `workflow.server` 를 import 하지 않는다.

### `src/workflow/connector/config.py`

```python
@dataclass(frozen=True)
class ConnectorPaths: home: Path; state_db: Path; token_file: Path; log_dir: Path
def connector_paths(env: Mapping = os.environ) -> ConnectorPaths   # WORKFLOW_CONNECTOR_HOME 또는 ~/Library/Application Support/workflow-connector/
def read_token(paths) -> tuple[str, str] | None                    # (connector_id, token). 파일 형식 JSON {"connector_id", "token", "server"}
def write_token(paths, connector_id, token, server) -> None        # 0600. 디렉터리 0700
```

### `src/workflow/connector/state.py`

```python
def connect(path) -> sqlite3.Connection; def init_schema(conn)
# registrations(local_registration_id PK, repo_path, tool, repository_id, base_commit, verification_profiles_json)
# executions(execution_id PK, phase CHECK IN ('accepted','launching','running','finished'), request_json, pid, process_start, runtime_ref, next_seq, result_json, worktree_path, claimed_at, finished_at)
# pending_events(execution_id, seq, event_json, PRIMARY KEY(execution_id, seq))
def save_registration(conn, reg: dict); def get_registration(conn, id) -> dict | None
def record_claim(conn, request: ExecutionRequest, now) -> None          # phase accepted, next_seq 1
def set_phase(conn, execution_id, phase, **fields)
def next_seq(conn, execution_id) -> int                                   # 발급 후 +1 저장 (재시작해도 1 로 안 돌아감)
def queue_event(conn, event: ExecutionEvent); def pop_pending(conn, execution_id) -> list[ExecutionEvent]; def ack_event(conn, execution_id, seq)
def active_execution(conn) -> dict | None
```

### `src/workflow/connector/client.py`

```python
class CentralClient:
    def __init__(self, server: str, token: str | None, transport: httpx.BaseTransport | None = None, timeout: float = 15.0)
    def exchange(self, connect_code: str) -> tuple[str, str]
    def claim(self, connector_id: str) -> ExecutionRequest | None                 # 204 → None
    def heartbeat(self, connector_id: str, current_execution_id: str | None) -> None
    def report_registration(self, connector_id: str, registration: dict) -> None  # POST /connector/registrations
    def post_event(self, event: ExecutionEvent) -> EventAck                       # 409 sequence_gap → SequenceGapError(expected_seq). 409 event_conflict → EventConflictError. 401 → Unauthenticated
    def download_artifact(self, execution_id: str, artifact_id: str) -> tuple[bytes, str]
    def upload_artifact(self, execution_id: str, meta: ArtifactMeta, data: bytes) -> ArtifactCreated
class Unreachable(Exception)     # 연결 오류. 호출자가 보류·재시도
```

### `src/workflow/connector/masking.py`

```python
SECRET_PATTERNS = (re.compile(r"wfc_[A-Za-z0-9_\-]{8,}"), re.compile(r"sk-[A-Za-z0-9_\-]{8,}"))
def mask_secrets(text: str) -> tuple[str, int]      # (마스킹된 텍스트, 발견 수). 대체는 "wfc_***" / "sk-***"
def codex_env(base: Mapping[str, str]) -> dict[str, str]   # 허용 목록만: HOME, PATH, LANG, LC_ALL, TERM, TMPDIR, USER, SHELL, CODEX_HOME, XDG_*. 그 외(특히 WORKFLOW_*, OPENAI_API_KEY, DIAG_*, SESSION_SECRET, OPERATOR_TOKEN) 제외
```

### `src/workflow/connector/adapter.py`

```python
@dataclass class AdapterOutput:
    result: CodeChangeResult | None          # None 이면 실패
    artifacts: list[tuple[ArtifactMeta, bytes]]
    failed: tuple[str, str, bool] | None     # (code, message, process_stopped)
    runtime_ref: str
class ExecutionAdapter(Protocol):
    def run(self, request: ExecutionRequest, handoff_dir: Path, progress: Callable[[str], None]) -> AdapterOutput
class EchoAdapter(ExecutionAdapter)         # 테스트·e2e 용: 인계 자료를 읽고 고정된 산출물을 돌려준다. 실제 Codex 는 Step 12
```

### `src/workflow/connector/runner.py`

```python
class Runner:
    def __init__(self, client: CentralClient, state_conn, paths: ConnectorPaths, adapter: ExecutionAdapter,
                 connector_id: str, clock: Callable[[], str], handoff_root: Path)
    def tick(self) -> None
    def run_forever(self, claim_interval=5.0, heartbeat_interval=30.0) -> None
```

`tick()` 규칙 (ARCHITECTURE "상태·재접속·완료"):

1. 미전송 이벤트(`pending_events`)를 seq 순으로 전송. `SequenceGapError(expected)` 면 expected 부터 다시. 같은 seq 200 은 ack. `Unreachable` 면 중단하고 다음 tick.
2. heartbeat (30초마다).
3. 활성 실행이 있으면: `phase` 가 `finished` 이고 미전송이 없으면 정리. `launching`/`running` 인데 프로세스 동일성을 확인할 수 없으면(PID 없음 또는 시작 시각 불일치) `failed` 이벤트 대신 **아무것도 보내지 않고** 로컬에 `unknown_local` 표시 후 사람 확인을 기다린다 (중앙이 2분 규칙으로 `unknown` 처리). 이 step 은 `launching`/`running` 을 어댑터 호출 전후로만 기록한다 (프로세스 감시는 Step 12).
4. 활성 실행이 없으면 `claim` → `record_claim`(로컬 먼저) → `accepted` 이벤트(seq 1) → 인계 산출물 다운로드(`input_artifact_ids` 의 `handoff_bundle` manifest 와 그 `attachments[].artifact_id` 만) → `handoff_root/<task_id>.handoff/` 에 저장(파일명 `{evidence_id}@{version}.{ext}`, `manifest.json`, 해시 검증 실패 시 `failed` 이벤트 code `handoff_hash_mismatch`) → `started` 이벤트(`runtime_ref` 는 어댑터가 준 값) → `adapter.run(...)` (progress 콜백은 `progress` 이벤트) → 산출물 업로드(마스킹 후) → `result_ready`(결과 산출물 `code_change_result` 업로드 후) 또는 `failed`.
5. 이벤트는 항상 `queue_event` 로 로컬에 먼저 쓰고 전송·ack 한다.

### `src/workflow/connector/discovery.py`

```python
def discover(repo_path: Path) -> dict
# 읽기 전용. 존재 여부와 요약만: AGENTS.md/CLAUDE.md 첫 200자, .codex/ 또는 codex.toml 존재, pyproject.toml 의 [project] name·pytest 설정 존재, 테스트 디렉터리 존재, git 원격 이름(URL 은 넣지 않음), 마지막 커밋 SHA. 파일 전체나 .env·auth 파일 내용은 절대 포함하지 않는다
# 반환 {"found": {...}, "not_read": [...], "verification_level": "설정 발견"}
```

### `src/workflow/connector/cli.py` + `__main__.py`

```text
python3 -m workflow.connector connect --server https://… --code <connect_code>
python3 -m workflow.connector register --id local-demo-report --repo /path/to/demo-report-repo --repository-id demo-report-repo --verify vp-pytest="python3 -m pytest -q"
python3 -m workflow.connector run
```

`register` 는 저장소의 HEAD 를 `base_commit` 으로 기록하고 `discover` 결과와 함께 `report_registration`. 검증 프로필은 `이름=명령` 을 인자 배열로 `shlex.split` 해 저장한다 — 서버나 요청에서 명령을 받지 않는다. `run` 은 Step 12 의 `CodexAdapter` 를 쓰지만 이 step 에서는 `--adapter echo` 옵션으로 `EchoAdapter` 를 선택할 수 있게 하고 기본값은 Step 12 에서 바꾼다.

### 테스트 — `tests/workflow/connector/test_state.py`, `test_client.py`, `test_masking.py`, `test_runner.py`, `test_discovery.py`, `test_cli.py`

중앙은 `httpx.MockTransport` 로 흉내 낸다 (`FakeCentral`: claim 큐, 이벤트 수신·seq 검증·gap 응답, 산출물 저장·다운로드).

- state: `next_seq` 가 재연결(새 connect) 뒤에도 이어진다. `pending_events` 순서.
- client: 각 엔드포인트 요청 형식(헤더 `Authorization: Bearer wfc_…`, 본문 `contract_version`), 204 → None, 409 gap → 예외 `expected_seq`, 연결 거부 → `Unreachable`.
- masking: `wfc_`·`sk-` 마스킹과 개수. `codex_env` 가 `OPENAI_API_KEY`·`WORKFLOW_*` 를 빼고 `PATH` 를 남긴다.
- runner: 정상 흐름 이벤트 5종이 seq 1..N 으로 중앙에 도착. claim 응답 유실(첫 claim 은 성공했지만 `record_claim` 후 크래시를 흉내: 새 Runner 가 로컬 기록을 보고 accepted 부터 재전송, 중앙에 실행이 2개 되지 않음). 순번 누락 → 재전송으로 복구. `Unreachable` 동안 이벤트가 로컬에 쌓이고 복구 후 순서대로 전송. 인계 해시 불일치 → `failed handoff_hash_mismatch`. 산출물에 `wfc_` 가 있으면 마스킹돼 업로드되고 `progress` 경고.
- discovery: tmp 저장소에서 AGENTS.md 발견, `.env` 내용 미포함.
- cli: `connect` 가 토큰 파일을 0600 으로 쓴다. `register` 가 `report_registration` 을 호출한다 (Mock).

### GLOSSARY

`ExecutionAdapter`(연결 프로그램이 실행 도구를 띄우는 경계. 첫 구현은 `CodexAdapter`), `handoff dir`(인계 묶음을 푼 로컬 디렉터리 `<repo>-worktrees/<task_id>.handoff/`. worktree 밖) 을 추가한다.

## Acceptance Criteria

```bash
python3 -m pytest tests/workflow/connector -q
python3 -m pytest -q
python3 -m ruff check .
python3 -m workflow.connector --help
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트:
   - `workflow.connector` 가 `workflow.server`·`workflow.adapters` 를 import 하지 않는가?
   - 토큰이 0600 파일에만 있고 로그·산출물·Codex 환경에 없는가?
   - 요청·산출물·근거에서 셸 명령·경로를 받아 실행하는 곳이 없는가? (검증 명령은 로컬 `register` 인자로만)
   - 시작 여부 불명을 재실행으로 처리하지 않는가?
3. `phases/0-mvp/index.json` 의 step 11 을 업데이트한다 (summary 에 모듈 목록, CLI 하위 명령, `ExecutionAdapter` 시그니처).

## 금지사항

- Codex 를 호출하지 마라. 이유: Step 12. 여기서는 `EchoAdapter` 까지만.
- `--dangerously-bypass-approvals-and-sandbox` 나 `claude` 폴백을 제품 코드에 넣지 마라. 이유: ARCHITECTURE "기존 하네스의 승인·샌드박스 우회와 Claude 자동 대체 정책을 복사하지 않는다".
- 설정 파일·인증 파일 내용을 서버에 올리지 마라. 이유: discovery 는 존재 여부와 요약만.
- 기존 테스트를 깨뜨리지 마라.
