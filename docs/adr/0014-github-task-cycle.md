# ADR-0014: GitHub 업무 순환(버그 수정 → 커밋 검토)의 MVP 계약

결정일: 2026-09-23 (phase 8 step 0). [ADR-0011](0011-task-driven-work-cycle.md)·[ADR-0013](0013-existing-tasks-first-staged-rollout.md)의 MVP를 GitHub Issues 버그 수정 → 검토 한 유형으로 구현하기 위한 계약이다. 기본값은 [phase 8 README](../../phases/8-github-task-cycle/README.md)의 제안이며 이 문서로 구현 기준을 고정한다. 이 시점에는 구현이 없다 — 아래 이름·필드는 step 1~13이 만들고, 바꿀 때는 이 ADR·[ARCHITECTURE](../ARCHITECTURE.md)·[CONTRACT](../CONTRACT.md) 13절·[GLOSSARY](../GLOSSARY.md)·테스트를 같이 고친다.

## 결정

1. **범위와 인증.** 셀프호스트 운영자 워크스페이스 하나가 GitHub 저장소를 연결한다. 자격 증명은 서버 환경변수 `WORKFLOW_GITHUB_TOKEN`(비밀값 — 구현은 선택 비밀값 `OPTIONAL_SECRET_KEYS`: 비면 GitHub 연결만 꺼진다(step 6). DB·로그·응답·템플릿·도구 프로세스 환경에 넣지 않음)과 허용 저장소 목록 `WORKFLOW_GITHUB_REPOS`(`owner/name` 콤마 구분, 비밀 아님)뿐이다. 설정 화면·API는 토큰 값을 받지 않고 "환경에 있음/없음"만 보인다. OAuth·GitHub App·공개 세션의 전역 토큰 사용은 범위 밖이다. 설정·응답은 운영자(`OPERATOR_TOKEN`)만 한다.
2. **수집.** `api.github.com` REST 폴링만 쓴다(webhook 없음). 대상은 설정된 저장소의 open Issue 중 (a) `label_filter` 라벨을 모두 가지고 `created_at >= start_at` 인 것, 또는 (b) 운영자가 번호로 고른 것(`selected_issue_numbers`)이다. Pull request 항목·범위 밖 백로그는 자동 착수하지 않는다. 같은 이슈는 `(source_id, github_issue_id)` 로 Task 하나다.
3. **새 내장 종류 2개.** 기존 `code_change`(진단 인계·`report_output`·`expected-report` 필수)는 그대로 두고 우회하지 않는다. 일반 버그 수정은 `bug_fix`, 결과 커밋 검토는 `code_review` 로 분리한다. 둘 다 `builtin: true` 이며 `BUILTIN_KIND_NAMES` 가 네 개가 된다. 내장 규칙 `bug_fix --[ready_for_review]--> code_review` 를 추가한다.
   - `bug_fix`: `capability_code` `code.fix`, `scope_key` `repository_id`, `input_kinds` `[]`, `output_kind` `code_change_result`, outcomes `ready_for_review`·`needs_information`. target 은 기존 `CodeChangeTarget`(`local_registration_id`·`base_commit`·`verification_profile_id`)을 재사용하고 결과 봉투도 `CodeChangeResult` 를 재사용한다. 검증 프로필은 로컬 등록에 사전 등록된 ID만 쓴다. 필수 산출물은 `diff`·`test_log_before`(0 아님)·`test_log_after`·`verification_log` 이며 `report_output`·expected-report 는 요구하지 않는다. 첫 시도의 `input_artifact_ids` 는 빈 배열을 허용한다.
   - `code_review`: `capability_code` `code.review`, `scope_key` `repository_id`, `input_kinds` `["code_change_result"]`, `output_kind` `code_review_result`(새 산출물 kind), outcomes `approved`·`changes_requested`·`needs_information`. target 은 새 `CommitReviewTarget`(`local_registration_id`·`source_execution_id`·`base_commit`·`result_commit`), 결과는 새 `CodeReviewResult`(`reviewed_commit` 이 target 의 `result_commit` 과 같아야 함). 검토는 같은 로컬 저장소에서 결과 커밋의 깨끗한 읽기 전용 체크아웃을 읽는다 — 인계 디렉터리만 읽는 `_run_generic` 으로 대체하지 않는다.
   - 종류별 target·결과·필수 산출물·판정기·후속 동작은 도메인의 데이터 표 `BUILTIN_POLICIES`(`ExecutionPolicy`)에 둔다. worker·connector·composition 에 종류 이름 `if` 를 늘리지 않고 이 표를 조회한다(ADR-0009 유지 — 내장 종류만 코드 판정기를 가진다).
4. **계약 버전.** `contract_version` 은 1 그대로다. 기존 v1 payload(CONTRACT 1~12절)는 한 글자도 바뀌지 않고 계속 유효하다. 확장은 추가형이다: 새 target·결과 모델, 산출물 kind `code_review_result`, `KindSpec.output_kind` 값 추가, `ClaimRequest.supported_kinds`(선택, 기본 null) 추가.
   - 구버전 연결 프로그램: `supported_kinds` 를 보내지 않으면 서버는 내장 중 `diagnosis`·`code_change`(`LEGACY_BUILTIN_KINDS`)와 사용자 정의 종류만 가능하다고 본다. `bug_fix`·`code_review` 실행은 만들지 않고 Task 를 `대기`(사유 코드 `executor_outdated`, "연결 프로그램 업데이트 필요")로 둔다 — 구버전이 모르는 요청을 받아 파싱에 실패하는 경로를 만들지 않는다. 서버는 마지막 claim 의 선언을 `connectors` 에 저장해 준비 판정에 쓴다.
   - 구버전 서버 + 신버전 연결 프로그램은 지원하지 않는다(서버가 `supported_kinds` 를 422 로 거부). 업그레이드 순서는 서버 → 연결 프로그램.
   - 기존 필드의 의미를 바꾸거나 필수 필드를 추가해야 하는 변경이 생기면 그때 `contract_version: 2` 를 만들고 서버가 한 릴리스 동안 1·2를 함께 받는다. 이번 phase 에는 없다.
5. **담당 연결.** `AssigneeBinding` 이 GitHub 사용자 숫자 ID → 수정 Agent 를 잇는다. 검토 Agent 는 소스 설정의 `review_agent_id` 하나다. GitHub 담당자와 이 제품의 인증 사용자·Agent 소유자를 같은 사람으로 보지 않는다. 담당자 0명(`assignee_missing`)·2명 이상(`assignee_multiple`)·연결 없음(`assignee_unbound`)은 자동 추정 없이 대기다. 수정·검토 Agent 는 같은 연결 프로그램·같은 `repository_id` 여야 하며(서버 검사), 커밋 존재는 연결 프로그램이 검토 시작 때 확인한다(`commit_missing`). 다른 기기로 커밋 전송·자동 push/PR/merge 는 하지 않는다.
6. **후속과 재작업.** 판정 통과한 `bug_fix` 결과(`ready_for_review`)마다 검토를 한 번 연결한다: 그 수정 Task 의 검토 Task 가 없으면 만들고(원인 키 `(session_id, cause_execution_id, "code_review")` 유일), 있으면 기존 검토 Task 에 새 Execution 을 연결한다. `changes_requested` 는 같은 수정 Task 의 다음 Execution(`attempt_no + 1`, `base_commit` = 이전 `result_commit`, 입력에 이전 결과·검토 결과)으로 이어지며, 자동 재작업은 `max_rework_rounds`(기본 1, 0~3) 번까지다. 넘으면 사람 요청. `approved` 는 검토 Task 완료일 뿐 이슈 close·merge·push 권한이 아니다 — 수정 Task 는 `확인 필요 · 검토 승인 — 병합·이슈 종료는 사람` 으로 남는다. 제목 유사도 자동 병합·후속 Task 의 GitHub 이슈 복제는 하지 않는다.
7. **사람 요청.** 입력 부족·위임 밖·결정 필요·재작업 상한·에이전트의 `needs_information`·검증 실패는 `HumanRequest` 로 남고 운영자가 웹에서 응답한다(`response_id` 멱등, `expected_revision` 낙관적 잠금). 응답은 다음 실행 입력(새 `task_revision`)이 되며 원본 스냅샷은 바꾸지 않는다. 응답했다고 바로 실행하지 않고 준비 판정을 다시 한다. GitHub 댓글 내용은 명령으로 해석하지 않는다 — 댓글에는 응답 링크 안내만 쓴다.
8. **원본 반영.** Task 마다 원본 이슈에 댓글 하나를 만들고 갱신한다. 본문 첫 줄에 marker `<!-- runloom:task=<task_id> -->` 를 넣고 `comment_id` 를 저장한다. 전달은 outbox(`SourceDelivery`)가 워커 트랜잭션 밖에서 하며, 최신 `body_revision` 만 보낸다. POST 응답을 잃으면 `unknown` 으로 두고 재POST 전에 댓글 목록에서 marker 를 찾아 조정한다(찾으면 `delivered`, 없음이 확인되면 다시 `pending`, 조회 실패면 `unknown` 유지). 원격 exactly-once 는 주장하지 않는다. 전달 실패는 Agent 작업 실패·Task 상태와 따로 표시한다.
9. **원본 변경·취소.** 실행 중 이슈 편집은 실행 입력을 바꾸지 않는다 — 새 스냅샷은 다음 `task_revision` 이 되고 진행 중인 결과·후속은 원래 revision 기준으로 기록한다. `updated_at` 이 저장값보다 오래된 스냅샷은 버리고, 같은 시각·다른 digest 는 새 revision 으로 받는다. 이슈가 닫히면 새 착수·후속 생성을 멈추고(`source_closed` 대기, 진행 중 실행은 계속) 다시 열리면 재평가한다. 운영자 종료(close)는 Task 를 `실패`(사유 `운영자 종료`)로 마감하고 새 후속을 만들지 않는다.
10. **동시성.** 독립 업무는 서로 기다리지 않는다. 같은 로컬 등록(같은 저장소)에서 수정 Execution 은 하나씩만 돈다(`repository_busy` 대기). 기존 "연결 프로그램당 실행 하나" 제약은 그대로라서 실제 병렬은 연결 프로그램 수만큼이다.

## 대안

- `code_change` 의 `report_output` 요구를 조건부로 끄기: 진단 데모의 완료 검증을 약하게 만들고 데모 회귀 위험이 있어 기각.
- 검토를 사용자 정의 `review`(인계 디렉터리 읽기)로 처리: 실제 커밋을 읽지 않은 검토가 완료를 대신할 수 있어 기각.
- `contract_version: 2` 로 전면 교체: 기존 payload 가 바뀌지 않으므로 불필요하다. 구버전 호환은 claim 시 선언으로 막는다.
- GitHub webhook: 공개 수신 주소·서명 검증이 필요하다. 셀프호스트 MVP 는 폴링으로 시작한다.

## 결과

- step 1~13 은 대역(MockTransport·임시 Git 저장소·fake 도구)으로 검증하고, 실제 GitHub 쓰기·실제 Agent 는 step 16 에서만 한다. 실제 저장소·이슈·댓글 허가·비용 범위가 없으면 step 16 만 blocked 이다.
- DB 는 phase 7(`SCHEMA_VERSION` 4) 데이터를 보존하는 트랜잭션 마이그레이션으로 바꾼다(step 4). 기존 세션에 새 내장 종류·규칙을 seed 하며, 같은 이름의 사용자 정의 종류가 있으면 마이그레이션 전체를 되돌리고 충돌 목록을 알린다.
- 공개 데모(`main`)·n8n 입구·callback 계약은 바뀌지 않는다.

> 범위 갱신(2026-09-27, [ADR-0017](0017-github-app-connection.md)): GitHub App 으로 연결한 소스(`intake: all_open`)는 설치 저장소의 열린 이슈를 **전부 목록에 가져오되 실행은 지시한 것만**([에이전트에게 맡기기] 또는 트리거 라벨) 한다. 결정 1 의 "OAuth·GitHub App 은 범위 밖"과 결정 2 의 "범위 밖 백로그는 받지 않는다"는 이 소스에 한해 바뀌고, 기존 `filtered` 소스·환경변수 토큰은 이 문서 그대로다.
