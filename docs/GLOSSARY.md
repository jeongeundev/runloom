# 용어 사전

## 왜 필요한가

`scripts/execute.py` 는 각 step 을 **독립된 Codex 세션**에서 실행한다. 세션 간에는
대화 맥락이 이어지지 않으므로, 도메인 용어를 매 세션이 새로 해석한다. step 1 이
만든 `Order` 를 step 4 가 `Purchase` 로 부르는 사고가 여기서 나온다.

이 파일은 `docs/*.md` 와 함께 매 step 프롬프트에 주입되므로, 여기에 한 번 적으면
모든 세션이 같은 이름을 쓴다.

## 규칙

- **코드 식별자와 정확히 일치**시킨다. 한글 설명은 붙이되, 이름 자체는 코드에 쓰는 그대로 적는다.
- 같은 것을 가리키는 **금지 표현**을 함께 적는다. 이게 실제로 드리프트를 막는다.
- 새 도메인 개념이 생기면 그 step 에서 여기에 추가한다.

## 용어

갱신일: 2026-09-21. 필드 구조는 [ARCHITECTURE](ARCHITECTURE.md), 예시는 [CONTRACT](CONTRACT.md)를 따른다.

| 용어 | 정의 | 금지 표현 |
|------|------|-----------|
| `Agent` | 등록된 실행 대상 하나. 로컬은 실행 도구 + 작업 폴더, API는 주소 + 자격 증명. `capabilities`를 가진다 | `Bot`, `Worker`, `Runner` |
| `Task` | 사용자가 등록했거나 가져오기로 만든 업무 하나. 선행 Task 하나를 가질 수 있고 `required_capability` 하나를 가진다. 가져온 Task 는 `chain_id`·`source_ref` 를 가진다 | `Job`, `Ticket`, `Issue`(가져오기 전의 외부 항목 — 별도 용어) |
| `Execution` | Task의 한 번의 시도. `attempt_no`로 구분. 상태는 `queued`, `accepted`, `running`, `result_ready`, `failed`, `unknown` | `Run`, `Job`, `Attempt` |
| `ExecutionEvent` | 실행 주체가 보내는 이벤트. `seq` 연속 정수, `type`은 `accepted`, `started`, `progress`, `result_ready`, `failed` | `Log`, `Message`, `Notification` |
| `Artifact` | 실행이 만든 불변 파일. `kind`, `sha256`, `artifact_id`. kind 목록은 CONTRACT 4절 | `File`, `Upload`, `Output` |
| `run` / `run_id` | 진단 대상인 보고서 자동화의 실행 (예: `daily-0920-0900`). 이 제품의 Execution이 아니다 | `execution`, `job` |
| `workflow_id` | 진단 대상 자동화의 ID (예: `daily-report`). 이 제품의 업무 흐름이 아니다 — 화면의 "워크플로우"(`Chain`)와 다르다 | `pipeline`, `flow` |
| `evidence` | 진단 서비스가 조회하는 원문 자료 하나. `evidence_id@version`으로 참조 | `document`(문서는 evidence의 한 종류), `source` |
| `capability` | Agent의 등록 능력 `{ code, scope }`. code는 `operations.diagnose`, `code.modify` | `skill`, `role`, `permission` |
| `required_capability` | Task가 요구하는 능력 하나. 같은 구조 | `requirement`, `needs` |
| `connector` / `connector_id` | 운영자 Mac에서 도는 로컬 연결 프로그램. Agent 여러 개(`local_registration_id`)를 대신 실행할 수 있다 | `agent`, `daemon`, `client` |
| `connect code` | 운영자가 발급하는 1회용 10분 연결 코드. `POST /connector/exchange` 로 교환하면 `connector_id` 와 연결 토큰 `wfc_…` 이 된다. 코드는 교환 즉시 무효, 토큰은 서버에 sha256 만 남는다 | `pairing code`, `invite`, `api key` |
| `example` | 등록 폼 미리 채움 키 `diagnose` / `fix` (`GET /tasks/new?example=…`, `server/web.py` 의 `EXAMPLES`). 시연 업무 A·B 의 제목·요청·능력·기본 방식을 채울 뿐이며 목표 자동 분해 기능이 아니다. 직접 등록 경로의 미리 채움이며 시연 주 경로는 가져오기(`/tasks/import`) | `template`(완료 기준 템플릿과 혼동), `preset`, `scenario` |
| `local_registration_id` | connector 안에서 등록된 폴더 + 도구 하나 (`tool` 은 `codex`/`claude`). 실행 요청의 `target.local_registration_id` 로 찾은 등록의 `tool` 이 어댑터를 정한다 (`runner.select_adapter`) — 요청 본문에서 도구를 받지 않는다. 등록 없음 → 실패 코드 `registration_missing`, 그 도구의 어댑터 없음 → `adapter_missing` | `folder_id`, `workspace` |
| `handoff bundle` | A 결과와 근거 원문을 묶은 B 입력 manifest. kind `handoff_bundle` | `payload`, `context`, `package` |
| `verification profile` / `verification_profile_id` | 소유자가 사전 등록한 검증 명령 (예: `vp-pytest`). 요청에 셸 명령을 넣지 않는다 | `test command`, `check` |
| `start_key` | Task당 실행 중복 방지 키. 웹 재전송·이벤트 중복에 같은 키 사용 | `idempotency_key`, `dedupe_key` |
| `outcome` | 에이전트 결과 봉투의 결론. 진단은 `ready_for_handoff` / `needs_information`, 코드 수정은 `ready_for_review` / `needs_information`. 시스템 상태가 아니다 | `status`, `result_status` |
| `outcome 라벨` | 화면 표시용 한글: `ready_for_handoff`→`인계 가능`, `ready_for_review`→`검토 가능`, `needs_information`→`정보 필요` (`server/filters.py` 의 `outcome_label`). 코드 값은 그대로 유지하며 라벨은 배지에만 쓴다 | `완료`(Task 상태와 혼동), `성공`, `승인됨` |
| `session` | 심사자의 익명 워크스페이스. 서명 쿠키로 식별 | `user`, `account`, `visitor` |
| `operator` | `OPERATOR_TOKEN`으로 인증한 운영자 | `admin`, `owner`, `superuser` |
| `base_commit` / `result_commit` | 코드 수정 실행의 시작 커밋과 보존된 결과 커밋(전체 SHA) | `head`, `ref`, `branch` |
| `Candidate` | 선택 후보. Agent 의 ID·능력·허용 여부만 가진 도메인 값 (`domain/selection.py`) | `Option`, `Choice`, `Applicant` |
| `TaskView` | 사용자 상태 판정에 필요한 Task·Execution·연결 스냅샷 (`domain/status.py`). DB 행·Pydantic 모델이 아니다 | `Snapshot`, `State`, `Context` |
| `Criterion` | 완료 기준 항목 하나. `code`·`text`·`structured` (`domain/completion.py`) | `Rule`, `Condition` |
| `Verdict` | 진단 결과 검증기의 판정. `passed`/`failed`/`undecidable` 와 `checks` (`domain/verification.py`). Task 상태·`outcome` 과 구분 | `Result`, `Decision`, `Status` |
| `Check` | 검증기 판정 항목 하나. `code`·`passed`·`detail` (`domain/verification.py`) | `Rule`, `Assertion`, `Test` |
| `LoadedEvidence` | 중앙이 내려받아 해시를 확인한 첨부 원문. `(evidence_id, version)` 키로 검증기에 넘긴다 (`domain/verification.py`) | `Attachment`(결과 봉투의 참조), `Document`, `Blob` |
| `ExecutionObservation` | 서버가 기록한 관찰 (`unknown_no_start`, `heartbeat_lost`, `timeout`). `unknown` 판정 근거이며 실행 주체가 보내는 `ExecutionEvent` 와 구분한다 — seq 를 소비하지 않는다 (`execution_observations` 테이블) | `Event`, `Log`, `Alert` |
| `TickReport` | 중앙 워커 한 바퀴(`Worker.tick`)의 처리 건수 요약 — 접수·반영 이벤트·판정·후속 생성·실패 반영 등 정수 카운터 (`server/worker.py`). 로그·테스트용이며 Task·Execution 상태가 아니다 | `Status`, `Result`, `Summary` |
| `FixtureStore` | 진단 데모의 가상 자료 저장소 (`diagnostic_demo/tools/store.py`). `allowed_workflow_ids` 로 조회 범위를 두고 범위 밖은 `access_denied`, 없는 자료는 `not_found`. `removed`·`replaced`·`unavailable` 로 자료 누락·교체·일시 오류를 재현한다 — fixture 파일은 고치지 않는다 | `Database`, `Repository`(중앙 `adapters/repo.py` 와 혼동), `Index` |
| `ToolResult` | 조회 도구 호출 하나의 결과. `ok`·`content`·`content_type`·`error`(`not_found`/`access_denied`/`unavailable`)·`returned`(실제로 반환한 근거) (`diagnostic_demo/tools/store.py`). 오류를 빈 본문으로 바꾸지 않는다 | `Response`, `Output`, `Payload` |
| `TraceRecord` | 진단 서비스가 기록한 조회 한 건. `call_id`·`tool`·`input`·`ok`·`error`·`returned[{evidence_id, version, sha256}]` (`diagnostic_demo/tools/trace.py`). `ToolTraceRecorder.to_json()` 의 항목이며 중앙은 이를 `TraceEntry` 로 읽는다 | `Log`, `Call`, `Event` |
| `LocalToolAdapter` | 로컬 도구 공통 흐름 (`connector/local_tool.py`). 등록·worktree 확인 → `launch` → 원시 로그 보존(마스킹) → 변경 확인 → 결과 커밋 → 수정 전 재현 테스트 → 수정 후 테스트 → 깨끗한 체크아웃에서 검증·보고서 → diff → outcome 판정. 하위 클래스(`CodexAdapter`, `ClaudeAdapter`)는 `tool_name`·`raw_kinds`·`launch`·`parse_last_message` 만 구현하고, 도구 출력에서 실패 사유(사용량 한도 → 실패 코드 `usage_limit`)를 읽어야 하면 `classify_failure` 를 덮어쓴다(기본 None) | `BaseAdapter`, `Runner`(`connector/runner.py` 와 혼동), `Driver` |
| `ToolRun` | 도구 프로세스 한 번의 원시 결과 — `pid`·`started_at`·`exit_code`·`stdout`(마스킹 전 원문)·`stderr`·`timed_out`·`stopped`·`last_message`(마지막 구조화 메시지 JSON 문자열 또는 None) (`connector/local_tool.py`). 어댑터가 커밋·검증·판정을 하기 전의 재료이며 `CodeChangeResult` 가 아니다. 이전 이름 `CodexRun` 은 쓰지 않는다 | `CodexRun`, `Result`, `Output`, `Session` |
| `ToolResult` | `parse_last_message` 가 도구의 마지막 메시지에서 읽은 주장 — `outcome`·`summary`·`parse_note`(못 읽은 사유, 정상이면 None) (`connector/local_tool.py`). 공통 흐름이 변경·재현 테스트 유무로 다시 판정하므로 그대로 `CodeChangeResult.outcome` 이 되지 않는다 | `LastMessage`, `Verdict`(검증기 판정과 혼동), `Answer` |
| `vp-report` | 보고서 생성 검증 프로필의 고정 이름. `register --verify "vp-report=python3 -m daily_report {response}"` 로 등록하고 어댑터는 `{response}` 토큰 하나만 인계 자료의 `response-after@1.json` 경로로 치환해 결과 커밋의 깨끗한 체크아웃에서 실행한다 → `report_output`. 요청의 `verification_profile_id`(`vp-pytest`)와 별개 | `report command`, `render script` |
| `ExecutionAdapter` | 연결 프로그램이 실행 도구를 띄우는 경계 (`connector/adapter.py`). `run(request, handoff_dir, progress) -> AdapterOutput`. 첫 구현은 `CodexAdapter`(Step 12), 두 번째는 `ClaudeAdapter`(`connector/claude.py`, `claude -p --output-format json`, 고정 `--allowedTools`·`--permission-mode acceptEdits`, 우회 정책 없음), 테스트·e2e 는 `EchoAdapter`. `progress(message, *, runtime_ref=...)` 의 첫 `runtime_ref` 가 `started` 이벤트가 된다 | `Executor`, `Driver`, `Backend` |
| `handoff dir` | 인계 묶음(`handoff_bundle`)을 푼 로컬 디렉터리 `<repo>-worktrees/<task_id>.handoff/` (`runner._handoff_dir`). `manifest.json` 과 `{evidence_id}@{version}.{ext}`. worktree 밖이며 어댑터는 읽기만 한다 | `workspace`, `inbox`, `context dir` |
| `DiagnosisDraft` | 모델이 작성하는 결과 초안 — `outcome`·`summary`·`findings`·`diagnosis`·`repair_request`·`missing_information` (`diagnostic_demo/worker/model.py`). 첨부·provenance·`execution_id` 가 없다. 서비스가 조회 이력에서 첨부를 조립해 `DiagnosisResult` 로 완성한다 (`worker/assemble.py`) | `Result`(완성 봉투와 혼동), `Answer`, `Output` |
| `ModelClient` | 진단 워커가 모델을 부르는 인터페이스 — `start(system, user, tools, schema)`·`continue_with_tool_results(results)` → `ModelTurn`(`tool_calls`, `draft`, 토큰 수) (`diagnostic_demo/worker/model.py`). 구현은 `OpenAIModelClient`(Responses API)와 `FakeModelClient`(대본, `DIAG_MODEL=fake` 전용) | `LLM`, `Agent`(등록 레코드와 혼동), `Provider` |
| `Budget` | 진단 1회의 상한 — `max_calls`·`max_input_tokens`·`max_output_tokens`·`timeout_seconds` (`diagnostic_demo/worker/loop.py`). 넘으면 `BudgetExceeded`(code `budget_exceeded`/`timeout`) 로 실행이 `failed`. 총액 US$30·일일 건수는 접수 시점의 429 이며 이 객체가 아니다 | `Limit`, `Quota`, `Cost` |
| `demo-report-repo` | B 가 수정하는 별도 저장소. 이 트리 밖에 `scripts/scaffold_demo_repo.py` 로 생성한다 (기본 위치 부모/`demo-report-repo`). `repository_id` 값과 같다. 기준 커밋은 태그 `report-base`(수정 전, `items` 만 지원)이며 스크립트 마지막 줄 `base_commit=…` 이 그 SHA 다. 가상 데모 자료이며 이 저장소에 커밋하지 않는다 | `demo repo`, `target repo`, `sample project` |
| `LocalStack` | 로컬 5-프로세스 기동기 (`scripts/local_stack.py`). 데모 저장소 scaffold → 진단 API → 진단 워커(`DIAG_MODEL=fake`) → 중앙 API → seed → 중앙 워커 → connector 순으로 `python3 -m …` 를 띄우고 `workdir/logs/*.log` 에 로그를 남긴다. 비밀값은 시작마다 생성. `--scripted`(`LocalStack(scripted=True)`)면 `codex`·`claude` 를 대본 에이전트(`workflow.scripted.*`)로 감싸 connector 의 PATH 앞에 두고 seed 도 `--scripted` 로 돈다 — e2e 는 이 구성으로 고정. 개발·e2e 용이며 심사 배포(Step 16 systemd·launchd)가 아니다 | `deploy`, `production stack`, `docker-compose` |
| `scripted agent` / `demo_scripted` | 대본 에이전트 (`workflow.scripted.codex`/`.claude`, 진단은 `DIAG_MODEL=fake`). 모델 호출 없이 정해진 변경·진단을 내며 계약·검증기·worktree·pytest 는 실제와 같다. 실제 어댑터의 argv 형식을 그대로 받으므로 `connector/`·`server/` 코드는 바뀌지 않는다. `agents.demo_scripted=1` 이면 화면에 `시연용 · 대본 재생`. 공개 데모 전용([ADR-0008](adr/0008-public-demo-scripted-agents.md)) — 제품 런타임(`connector/`)에서 import 하지 않는다 | `가짜 codex`·`fake_codex`(이전 이름 — 이 항목으로 대체. `tests/e2e/fake_codex.py` 는 `--fake-codex` 호환용 shim 만 남음), `mock`, `simulation`(화면 문구로 쓰지 않음) |
| `Chain` / `chain_id` | 세션이 가져오기로 만든 Task 묶음. 화면 라벨은 "워크플로우". 순서는 Task 의 `predecessor_task_id` 체인으로만 표현한다. `workflow_id`(진단 대상 자동화 ID)와 다르다 (`adapters/db.py` `chains`, `domain/composition.py`) | `workflow_id`, `pipeline`, `flow` |
| `Issue` | 외부 출처(GitHub·Jira fixture)의 업무 항목. `key`·`labels`·`blocked_by`. Task 가 되기 전의 것이며 `body` 는 그대로 `Task.request` 가 된다 (`domain/task_sources.py`) | `Task`(이미 등록된 것), `ticket` |
| `TaskSource` / `source` | Issue 의 출처 `github`/`jira`. 이 phase 는 fixture (`adapters/task_sources.py` `SOURCES`, `task_source_fixtures/`) — 실제 GitHub·Jira API 를 부르지 않는다. 화면에는 "시연 데이터" | `provider`, `integration` |
| `IssueMapping` | 라벨 규칙으로 정한 Issue 의 능력·`run_id`·이유 (`map_issue`). `incident`+`workflow:<id>`+`run:<run_id>` → `operations.diagnose`, `bug`+`repo:<id>` → `code.modify`, 그 외 None | `classification`, `intent` |
| `ChainPlan` / `PlanNode` / `Standalone` | `compose` 의 결과 — 순서·방식·배정·이유가 붙은 노드 열 / 체인에 못 들어간 이슈와 이유 (`domain/composition.py`). `PlanNode.selection` 은 임시 task_id 라 저장하지 않고 server 가 실제 task_id 로 다시 계산한다 | `Graph`, `DAG`, `schedule` |
| `session agent` | 세션이 카탈로그에서 등록한 Agent (`session_agents`, `repo.list_session_agents`). 후보·홈·선택 목록은 세션 등록만 센다 | `favorite`, `subscription` |
| `catalog` | 운영자가 미리 넣은 `shared_to_all_sessions=1` Agent 목록 (`/agents/register`, `seed_demo.py` 의 3개). 세션은 여기서 고르기만 하며 새 Agent 를 만들지 않는다 | `marketplace`, `directory` |
| `human gate` | 체인 마지막의 사람 단계 노드. 문구는 `human_gate_label` — "검토 승인 (사람) · 병합은 운영자 확인"(검토 후 완료) / "완료 확인 (사람)"(자동 완료). Task 가 아니며 상태는 마지막 Task 에서 파생한다 (`views._human_gate`) | `approval step`, `manual task` |
| `prefer` | `select_agent` 동률 규칙의 우선순위 — 세션이 먼저 등록한 순서의 agent_id 목록. 자동 선택에서 일치 후보가 2개 이상이면 `prefer` 에서 가장 앞인 후보를 기본 선택(이유 "먼저 등록한 …", 변경 가능). 가져오기만 넘기고 직접 등록은 넘기지 않는다 | `priority`, `score` |
| 사용자 상태 | `대기`, `실행 가능`, `실행 요청됨`, `실행 중`, `확인 필요`, `완료`, `실패`. 화면 문구로 그대로 쓴다 | `pending`, `done`, `success`, `error`, `대기 중` |

## 경계가 헷갈리는 개념

- `Execution`과 `run`: Execution은 이 제품이 만든 Task의 시도이고, run은 진단 대상 자동화(일일 보고서)의 실행이다. `run_id`는 진단 요청의 `target`에만 나온다.
- `workflow_id`와 `Chain`: `workflow_id`는 진단 대상 자동화(일일 보고서)의 ID로 능력 scope·진단 요청에만 나온다. `Chain`은 이 제품이 가져오기로 만든 Task 묶음이며 화면에서만 "워크플로우"라 부른다. 코드·경로는 `chain`이다.
- `Agent`와 `connector`: Agent는 등록 레코드, connector는 그 Agent를 대신해 실제 프로세스를 띄우는 프로그램이다. connector 하나가 폴더별 Agent 여러 개를 가질 수 있다.
- `outcome`과 상태: outcome은 에이전트의 주장이다. Execution `result_ready`는 결과가 저장됐다는 뜻이고, Task `완료`는 시스템이 완료 기준을 검증했거나 사람이 승인했다는 뜻이다. 셋을 서로 대체하지 않는다.
- `attachments`와 `Artifact`: attachments는 결과 봉투 안의 근거 참조 배열이고, 각 항목의 `artifact_id`가 실제 Artifact를 가리킨다.
- `Task.status`와 `Execution.status`: Task는 사용자 상태(한글), Execution은 내부 상태(영문). 대응표는 PRD 3절.
- `Criterion`, `Check`, `Verdict`: Criterion은 사용자가 등록 화면에서 보고 고치는 완료 기준 항목이고, Check는 검증기가 첨부 원문을 읽어 낸 판정 항목, Verdict는 그 묶음의 결론이다. `Verdict.passed`는 A의 `완료` 근거일 뿐 Task 상태가 아니며, `undecidable`은 실패가 아니라 보류(확인 필요)다.
