# Step 8: central-worker

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/docs/ARCHITECTURE.md` — "구성과 책임"(실행 조정 워커의 책임·경계), "실행 인터페이스 초안", "DB 제약과 실행 잠금"(A 완료 트랜잭션, B 생성 스캔), "계약 수용 기준" 표(이 step 의 테스트 목록), "상태·재접속·완료", "배포와 실행 예산"(unknown 2분, 첨부 1MB, 타임아웃·폴링 초기값), "연결 끊김과 Mac 오프라인"
- `/docs/PRD.md` — "완료 판정과 실제 인계" 1~5, "A의 진단 결과와 B 착수 조건", "기대 수정 결과와 보고서"(기대 보고서는 입력 행에서 계산)
- `/docs/CONTRACT.md` — 1절(진단 API 호출·응답), 2절(handoff manifest), 7절(B 결과 필수 산출물)
- `/src/workflow/adapters/repo.py` (Step 4), `/src/workflow/domain/verification.py` (Step 3), `/src/workflow/domain/status.py`·`start_key.py` (Step 2), `/src/workflow/server/settings.py`·`views.py` (Step 5·6)

## 작업

중앙 워커 `src/workflow/server/worker.py` (`python3 -m workflow.server.worker`) 와 진단 API 클라이언트 `src/workflow/adapters/diag_client.py` 를 만든다. 워커는 DB 를 기준으로 상태를 전진시키고, 진단 API 를 호출하며, 검증기로 A 완료를 판정하고, B 의 입력을 고정해 실행을 만든다. 모델을 호출하지 않는다.

### `src/workflow/adapters/diag_client.py`

```python
class DiagClient(Protocol):
    def submit(self, request: ExecutionRequest) -> RunStatus            # POST /runs. 202/200 → RunStatus. 409 → DiagConflict. 429 → DiagLimit(ErrorBody)
    def status(self, execution_id: str, after_seq: int) -> RunStatus    # GET /runs/{id}?after_seq=
    def download(self, execution_id: str, artifact_id: str) -> tuple[bytes, str]   # GET /runs/{id}/artifacts/{artifact_id} → (bytes, content_type)
    def capabilities(self) -> dict

class HttpDiagClient(DiagClient):
    def __init__(self, base_url: str, token: str, transport: httpx.BaseTransport | None = None, timeout: float = 10.0)
```

`Authorization: Bearer {DIAG_API_TOKEN}`. 연결 오류는 `DiagUnavailable` 로 감싸 워커가 다음 tick 에 재시도한다 (같은 execution_id 로).

### `src/workflow/domain/report_expectation.py` (순수)

```python
@dataclass(frozen=True)
class ExpectedReport: report_date: str; rows: tuple[tuple[str, int, int], ...]; total_completed: int; total_pending: int
def expected_report(response: dict, supported_paths: Sequence[str]) -> ExpectedReport | None
    # supported_paths 중 정확히 하나에 list 가 있어야 한다. 0개·2개 → None. 각 행 team/completed/pending 검증. 합계는 계산
def report_text_matches(report_output: str, expected: ExpectedReport) -> bool
    # 보고서 텍스트에서 날짜·팀별 값·합계 줄을 파싱해 비교. 공백 정렬은 무시
```

### `src/workflow/server/worker.py`

```python
class Worker:
    def __init__(self, conn_factory: Callable[[], sqlite3.Connection], store: ArtifactStore,
                 diag: DiagClient, settings: Settings, clock: Callable[[], str])
    def tick(self) -> TickReport          # 한 바퀴. 아래 단계를 순서대로. 각 단계는 자기 트랜잭션
    def run_forever(self, interval_seconds: float = 3.0) -> None
def main() -> None                        # settings 로드, HttpDiagClient, 시작 시 tick 한 번(복구), run_forever
```

`tick()` 의 단계:

1. **연결 상태**: `last_seen_at` 이 `heartbeat_offline_seconds` 보다 오래된 local agent 를 `offline` 으로.
2. **관찰**: `accepted` 후 `unknown_after_seconds` 지나도 `started` 없으면 `mark_unknown(kind="unknown_no_start")`. `running` 인데 배정 connector 의 heartbeat 가 끊기면 관찰 `heartbeat_lost` 만 기록 (상태는 유지, 재실행 없음).
3. **진단 전달**: `kind="diagnosis"` 이고 `queued` 인 실행 → `diag.submit(request)`. 응답의 `status` 가 `accepted` 면 actor `"diag"` 로 `accepted` 이벤트를 **워커가 대신** `append_event` (진단 API 는 이벤트 push 가 없다. 워커가 `status()` 로 받은 `events` 를 순서대로 `append_event` 한다. seq 는 진단 API 가 발급한 값 그대로). `DiagLimit` → 실행 `failed`(code 그대로) + Task `확인 필요`, reason 은 ErrorBody.message.
4. **진단 폴링**: `accepted`/`running` 진단 실행 → `diag.status(id, after_seq=last_event_seq)` → 새 이벤트를 `append_event`. `result_ready` 가 오면 먼저 결과 산출물·`provenance.tool_trace_artifact_id`·`attachments[].artifact_id` 를 `diag.download` 로 받아 각각 `store_artifact`(kind `diagnosis_result` / `tool_trace` / `evidence`) 한 뒤 이벤트를 적용한다 (이벤트가 먼저 오면 `result_artifact_missing` 이므로 순서 중요). 첨부 총 크기 > `attachments_max_bytes` → 저장하지 않고 실행 `failed` code `attachments_too_large`, Task `확인 필요`.
5. **A 판정**: `result_ready` 진단 실행 중 판정 기록 없는 것 → `DiagnosisResult` 파싱, `LoadedEvidence` 조립(중앙이 저장한 바이트로 해시 재계산), `tool_trace` 산출물을 `TraceEntry` 로 파싱, `verify_diagnosis`. 저장은 `task_verdicts`. 그 뒤 한 트랜잭션에서: `completion_mode="auto"` 이고 `passed` → Task `완료`(reason `"판정 근거: {통과 check 수}/{전체}"`), `release_execution`. 그 외(`failed`/`undecidable`/`review`) → Task `확인 필요`(reason: `"검토 대기"` 또는 미충족 항목 목록). 잠금 유지.
6. **B 생성 스캔**: `완료`된 Task 의 후속 Task 중 활성 실행이 없고 `run_mode="auto"` 이고 선택 `selected` 인 것 → 인계 묶음 조립 → `create_execution(start_key=auto_start_key(task_id, revision), attempt_no=1 또는 다음, assigned_connector_id=agent.connector_id, predecessor_execution_id=A 실행)`. `DuplicateStartKey`/`ActiveExecutionExists` 는 정상(이미 만들었음)으로 삼켜 로그만 남긴다. connector `offline` 이면 실행을 만들지 않고 Task `대기`(reason `"연결 끊김, 마지막 확인 {시각}"`). `run_mode="manual"` 이면 입력만 준비하고 Task `실행 가능`.
7. **B 결과 확인**: `kind="code_change"` 이고 `result_ready` 인 실행 중 미확인 → `CodeChangeResult` 파싱. 필수 산출물(`diff`, `test_log_before`, `test_log_after`, `report_output`, `verification_log`) 존재, `test_log_before` 의 첫 줄 `exit_code=` 가 0 이 아님, `verification.exit_code == 0`, `report_output` 이 `report_text_matches(expected)` 인지 확인 (기대 보고서는 인계 묶음의 `expected_report.json`). 모두 만족 → Task `확인 필요` reason `"검토 대기"`. 하나라도 아니면 `확인 필요` reason `"필수 산출물 누락: …"` 또는 `"보고서 수치 불일치"`. 어느 쪽이든 사람 검토 전 완료하지 않는다.
8. **실패 반영**: `failed` 이벤트가 온 실행 → Task `실패`(process_stopped 이면) 또는 `확인 필요`(`"시작 여부 불명 — 재실행하지 않음"`). `unknown` → `확인 필요`.

**인계 묶음 조립** (`assemble_handoff(conn, store, a_execution, b_task) -> str`): `HandoffBundle` manifest (kind `handoff_bundle`) 를 B 의 세션 소유 산출물로 저장하되 `execution_id` 는 A 실행. `attachments` 는 A 결과의 `attachments` 그대로(artifact_id 는 중앙에 저장된 evidence 산출물 ID 로 치환). 추가로 `expected_report.json`(kind `evidence`, evidence_id `expected-report`, version `1`)을 `response-after` 첨부와 보고서 계약의 `supported_paths` 로 계산해 넣는다. B 의 `ExecutionRequest.input_artifact_ids = [handoff_artifact_id]`, `target` 은 B Task 의 `target_json`. `request` 는 B Task 의 request.

### 테스트 — `tests/workflow/server/test_worker.py`, `tests/workflow/adapters/test_diag_client.py`, `tests/workflow/domain/test_report_expectation.py`

진단 API 는 `httpx.MockTransport` 로 흉내 낸다 (`FakeDiagServer`: 접수 → 이벤트 순서대로 방출 → CONTRACT 5절 결과와 첨부 8개 + trace 제공). Step 3 테스트의 `make_demo_attachments()` 를 재사용해 fixture 를 만든다. 시계는 고정 문자열을 돌려주는 함수.

ARCHITECTURE "계약 수용 기준" 표를 한 행씩:

- 같은 요청·ID 두 번(키 순서만 다름) → submit 1회, 실행 1개.
- claim 응답 유실 후 재조회 → 같은 배정 (Step 4·5 에서 검증했으면 여기서는 B 실행이 `queued` 로 하나만 생기는지).
- A 완료 저장 직후 재시작 → 새 Worker 인스턴스의 첫 tick 이 B 를 한 번 만든다.
- B 종료 뒤 과거 A 완료 이벤트 재처리(tick 을 여러 번) → B 실행 추가 없음 (`start_key`).
- B 가 `unknown` 또는 검토 대기인 동안 → 새 실행 없음.
- 판정 대상 건수가 바뀜 → `expected_report` 가 13·21 을 계산하고 `report_text_matches` 가 20 짜리 보고서를 거부.
- 정상 흐름: A `queued` → tick → `accepted`/`running` → 결과 → `완료` → B `queued` 배정 → (Step 5 API 로 claim 가능 여부는 여기서 DB 로 확인).
- `needs_information` 결과 → A `확인 필요`, B 없음. 첨부 누락(trace 에 없음) → A `확인 필요`, B 없음.
- connector offline → B Task `대기` + 이유. 온라인이 되면 다음 tick 에 생성.
- `DiagLimit` 429 → A `확인 필요`, reason 에 한도 문구.
- 첨부 1MB 초과 → `failed attachments_too_large`.
- `accepted` 후 2분 무소식 → `unknown` → `확인 필요` `"시작 여부 불명 — 재실행하지 않음"`.
- B `result_ready` 필수 산출물 누락 → `확인 필요` `"필수 산출물 누락"`.
- `DiagUnavailable` → 실행 상태 불변, 다음 tick 재시도.

### GLOSSARY

`TickReport`(워커 한 바퀴의 처리 건수 요약. 로그용) 을 추가한다.

## Acceptance Criteria

```bash
python3 -m pytest tests/workflow/server/test_worker.py tests/workflow/adapters/test_diag_client.py tests/workflow/domain/test_report_expectation.py -q
python3 -m pytest -q
python3 -m ruff check .
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트:
   - 워커가 OpenAI·모델을 호출하지 않는가? (ADR-0004)
   - A 완료가 `verify_diagnosis` 의 `passed` 로만 결정되는가? `outcome` 문자열이나 `summary` 로 완료하는 곳이 없는가?
   - 트랜잭션 안에서 HTTP 를 기다리지 않는가? (다운로드는 트랜잭션 밖, 저장·상태 변경은 안)
   - 자동 재시도가 "같은 execution_id 재전송"뿐인가? 새 실행을 자동으로 만드는 경로가 B 생성 스캔뿐인가?
3. `phases/0-mvp/index.json` 의 step 8 을 업데이트한다 (summary 에 tick 단계 목록과 `assemble_handoff` 위치).

## 금지사항

- 모델의 `outcome`·`summary` 로 완료 처리하지 마라. 이유: 원칙 "상태는 증거로만".
- 시간 초과·heartbeat 상실로 실행을 자동 재시작하지 마라. 이유: 중복 실행 방지. `unknown` 과 확인 필요로 둔다.
- 워커에 FastAPI 라우트를 두지 마라. 이유: 별도 프로세스다.
- 20·5 같은 고정 수치를 판정에 쓰지 마라. 이유: `expected_report` 가 입력에서 계산한다.
- 기존 테스트를 깨뜨리지 마라.
