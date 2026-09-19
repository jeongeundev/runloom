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

갱신일: 2026-09-20. 필드 구조는 [ARCHITECTURE](ARCHITECTURE.md), 예시는 [CONTRACT](CONTRACT.md)를 따른다.

| 용어 | 정의 | 금지 표현 |
|------|------|-----------|
| `Agent` | 등록된 실행 대상 하나. 로컬은 실행 도구 + 작업 폴더, API는 주소 + 자격 증명. `capabilities`를 가진다 | `Bot`, `Worker`, `Runner` |
| `Task` | 사용자가 등록한 업무 하나. 선행 Task 하나를 가질 수 있고 `required_capability` 하나를 가진다 | `Job`, `Ticket`, `Issue` |
| `Execution` | Task의 한 번의 시도. `attempt_no`로 구분. 상태는 `queued`, `accepted`, `running`, `result_ready`, `failed`, `unknown` | `Run`, `Job`, `Attempt` |
| `ExecutionEvent` | 실행 주체가 보내는 이벤트. `seq` 연속 정수, `type`은 `accepted`, `started`, `progress`, `result_ready`, `failed` | `Log`, `Message`, `Notification` |
| `Artifact` | 실행이 만든 불변 파일. `kind`, `sha256`, `artifact_id`. kind 목록은 CONTRACT 4절 | `File`, `Upload`, `Output` |
| `run` / `run_id` | 진단 대상인 보고서 자동화의 실행 (예: `daily-0920-0900`). 이 제품의 Execution이 아니다 | `execution`, `job` |
| `workflow_id` | 진단 대상 자동화의 ID (예: `daily-report`). 이 제품의 업무 흐름이 아니다 | `pipeline`, `flow` |
| `evidence` | 진단 서비스가 조회하는 원문 자료 하나. `evidence_id@version`으로 참조 | `document`(문서는 evidence의 한 종류), `source` |
| `capability` | Agent의 등록 능력 `{ code, scope }`. code는 `operations.diagnose`, `code.modify` | `skill`, `role`, `permission` |
| `required_capability` | Task가 요구하는 능력 하나. 같은 구조 | `requirement`, `needs` |
| `connector` / `connector_id` | 운영자 Mac에서 도는 로컬 연결 프로그램. Agent 여러 개(`local_registration_id`)를 대신 실행할 수 있다 | `agent`, `daemon`, `client` |
| `local_registration_id` | connector 안에서 등록된 폴더 + 도구 하나 | `folder_id`, `workspace` |
| `handoff bundle` | A 결과와 근거 원문을 묶은 B 입력 manifest. kind `handoff_bundle` | `payload`, `context`, `package` |
| `verification profile` / `verification_profile_id` | 소유자가 사전 등록한 검증 명령 (예: `vp-pytest`). 요청에 셸 명령을 넣지 않는다 | `test command`, `check` |
| `start_key` | Task당 실행 중복 방지 키. 웹 재전송·이벤트 중복에 같은 키 사용 | `idempotency_key`, `dedupe_key` |
| `outcome` | 에이전트 결과 봉투의 결론. 진단은 `ready_for_handoff` / `needs_information`, 코드 수정은 `ready_for_review` / `needs_information`. 시스템 상태가 아니다 | `status`, `result_status` |
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
| 사용자 상태 | `대기`, `실행 가능`, `실행 요청됨`, `실행 중`, `확인 필요`, `완료`, `실패`. 화면 문구로 그대로 쓴다 | `pending`, `done`, `success`, `error`, `대기 중` |

## 경계가 헷갈리는 개념

- `Execution`과 `run`: Execution은 이 제품이 만든 Task의 시도이고, run은 진단 대상 자동화(일일 보고서)의 실행이다. `run_id`는 진단 요청의 `target`에만 나온다.
- `Agent`와 `connector`: Agent는 등록 레코드, connector는 그 Agent를 대신해 실제 프로세스를 띄우는 프로그램이다. connector 하나가 폴더별 Agent 여러 개를 가질 수 있다.
- `outcome`과 상태: outcome은 에이전트의 주장이다. Execution `result_ready`는 결과가 저장됐다는 뜻이고, Task `완료`는 시스템이 완료 기준을 검증했거나 사람이 승인했다는 뜻이다. 셋을 서로 대체하지 않는다.
- `attachments`와 `Artifact`: attachments는 결과 봉투 안의 근거 참조 배열이고, 각 항목의 `artifact_id`가 실제 Artifact를 가리킨다.
- `Task.status`와 `Execution.status`: Task는 사용자 상태(한글), Execution은 내부 상태(영문). 대응표는 PRD 3절.
- `Criterion`, `Check`, `Verdict`: Criterion은 사용자가 등록 화면에서 보고 고치는 완료 기준 항목이고, Check는 검증기가 첨부 원문을 읽어 낸 판정 항목, Verdict는 그 묶음의 결론이다. `Verdict.passed`는 A의 `완료` 근거일 뿐 Task 상태가 아니며, `undecidable`은 실패가 아니라 보류(확인 필요)다.
