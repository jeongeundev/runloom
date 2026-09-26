# ADR-0015: 측정 — 업무 이벤트 보충·설정 번호·실행 사용량·GitHub 이력 기준선

결정일: 2026-09-27 (phase 9 step 0). 기본값은 [phase 9 README](../../phases/9-measure/README.md) "계획 기본값"(2026-09-27 사용자 결정)이며 이 문서로 구현 기준을 고정한다. 근거: [MVP 계획](../product/MVP_PLAN.md) 6절·10절, [ROADMAP](../product/ROADMAP.md) 9절. 이 시점에는 구현이 없다 — 아래 이름·칸은 step 1~10 이 만들고, 바꿀 때는 이 ADR·[ARCHITECTURE](../ARCHITECTURE.md) "측정 — phase 9"·[CONTRACT](../CONTRACT.md) 3절·[GLOSSARY](../GLOSSARY.md)·테스트를 같이 고친다.

## 결정

1. **원칙.** 지표는 사람이 보고한 숫자가 아니라 시스템이 남긴 기록에서 계산한다. 이미 있는 테이블(`tasks`·`executions`·`execution_events`·`task_verdicts`·`followup_links`·`human_requests`·`human_responses`·`source_issues`)에서 읽을 수 있는 시각은 다시 기록하지 않고, 빠진 것(준비 판정 변화·Task 상태 변화 이력·설정 버전·러너 폴더 커밋·비용과 토큰)만 보충한다.
2. **버전 = 워크스페이스 설정 번호 + 러너 폴더 커밋.**
   - 설정 번호 `sessions.config_revision`(1 부터). 종류 추가·삭제, 후속 규칙 추가·삭제, GitHub 소스 설정 생성·변경(중지 포함)이 같은 트랜잭션에서 1 올린다. 실행 생성 때 `executions.config_revision` 에 찍고, `followup_links.rules_revision` 에도 이 값을 쓴다(지금은 1 고정). 담당자 연결·에이전트 등록·토큰 발급 같은 작업은 올리지 않는다 — 결과를 만드는 규칙이 아니라 실행 대상·접근의 변화라서다.
   - 러너 폴더 커밋: 러너가 실행을 시작할 때 **로컬 등록 폴더(worktree 가 아닌 등록 경로)** 의 HEAD(`folder_commit`)와 미커밋 변경 여부(`folder_dirty`)를 읽어 `started` 이벤트에 싣는다. 그 폴더의 CLAUDE.md·AGENTS.md·에이전트 설정 변화를 가리키는 값이다. 읽지 못하면 null 이고 실행은 막지 않는다.
3. **비용·토큰을 중앙까지 가져온다.** `result_ready`·`failed` 이벤트에 선택 칸 `usage`(`ExecutionUsage`: `cost_usd`·`input_tokens`·`output_tokens`, 모두 선택)를 둔다. Claude 는 CLI 결과 JSON 의 `total_cost_usd`·`usage.input_tokens`·`usage.output_tokens`, Codex 는 확인되는 토큰만 — 비용은 보고하지 않으므로 null. 모르는 값은 null 이며 저장·화면·API 에서 0 이 아니라 "모름"이다. Claude 의 `total_cost_usd` 는 CLI 가 계산한 값이며 구독 사용 시 실제 청구액이 아니다 — 화면은 "CLI 보고 비용"으로 표시한다.
4. **계약은 `contract_version` 1 유지, 추가형 선택 칸.** `StartedData.folder_commit`·`folder_dirty`, `ResultReadyData.usage`·`FailedData.usage`. 기존 payload 는 그대로 유효하다. `_Contract` 가 `extra="forbid"` 이므로 구버전 서버는 새 칸을 422 로 거부한다 — ADR-0014 와 같이 업그레이드 순서는 서버 → 러너이고, 새 서버는 칸이 없는 구버전 러너 이벤트를 그대로 받는다(그 실행의 값은 null = 모름).
5. **업무 이벤트는 추가 전용 `task_events` 한 테이블.** `type` 은 `status_changed`·`blocked`·`ready` 셋뿐이다.
   - `status_changed`: Task 상태 값이 실제로 바뀔 때(또는 운영자 검토 결정 `review_decision` 이 주어질 때) 상태를 바꾸는 repo 함수 안, 같은 트랜잭션.
   - `blocked`: 준비 판정이 막혔고 대기 코드 집합(정렬된 `code` 와 각 `actor`)이 그 Task 의 직전 `blocked`·`ready` 기록과 다를 때만.
   - `ready`: 업무 순환 종류의 준비 판정이 통과해 실행을 만들 때, 실행 생성과 같은 트랜잭션. 그 Task 의 직전 기록이 `ready` 면 쓰지 않는다.
   - 각 행은 `task_id`·`session_id`·`type`·`task_revision`·`config_revision`(기록 시점 값)·`occurred_at`(서버 시계)·`data_json`. 수정·삭제하지 않는다.
6. **스키마 v5 → v6 는 데이터 보존·한 트랜잭션 마이그레이션**(ADR-0014 의 v4 → v5 와 같은 방식). 기존 실행의 새 칸은 NULL(= 모름), 기존 세션의 `config_revision` 은 1. 과거 이벤트를 추정해 채우지 않는다.
7. **기준선 = 도입 전 GitHub 이력 "이슈 열림 → 그 이슈를 닫은(연결된) 병합 PR".** GitHub GraphQL(이슈의 `closedByPullRequestsReferences` 또는 PR 의 `closingIssuesReferences`)로 가져온다. 도입 전 = 해당 GitHub 소스 연결 시각(`github_sources.created_at`) 이전에 열린 이슈. 가져오기는 운영자가 명시적으로 실행하고, 소스 단위 전체 교체라 멱등이다(`baseline_items`·`baseline_imports`). 화면·API 에 "하네스·Claude 사용 시기 이력 — 순수 수작업 기준 아님" 주석과 n 을 붙인다. OpenArchive 는 2026-09-27 조회 기준 약 19건이다. 이 phase 는 대역으로만 검증하고 실제 가져오기는 phase 뒤 사용자 지시로 한다.
8. **지표는 순수 도메인 계산.** `workflow.domain.metrics` 가 DB 행이 아닌 값 객체를 받아 `compute_metrics(...)`·`summarize_baseline(...)` 로 계산한다. 모든 수치는 중앙값·n·미완료 건수·"모름" 건수를 함께 낸다. 기간(from/to)과 묶음 기준(`config_revision` 또는 `folder_commit`)으로 나눌 수 있다. 관측한 차이를 인과적 효과로 단정하는 문구를 쓰지 않는다.
9. **도입 후 완료 = 그 이슈를 닫은 병합 PR 의 병합 시각**(2026-09-27 사용자 합의, step 11·12). 기준선이 "이슈 열림 → 병합"인데 도입 후 `bug_fix` 묶음의 완료가 운영자 검토 승인 시각이면, 승인 뒤 사람이 하는 push·PR·병합이 빠져 도입 후 수치가 짧게 나온다. 그래서 도입 후 완료도 GitHub 에서 원본 이슈를 닫은 병합 PR 중 가장 이른 병합 시각(`source_issues.pr_merged_at`, 기준선과 같은 해석)으로 맞춘다. 운영자 승인 시각은 버리지 않고 별도 지표로 둔다. 병합 칸은 v6 에 더하고(v6 는 아직 배포 전이라 새 버전을 만들지 않는다) NULL = 아직 모름/병합 없음, 한 번 기록한 병합은 덮지 않는다. step 11 은 조회·저장, 동기화와 지표 연결은 step 12.

## 대안

- **러너 폴더 무시(설정 번호만).** 에이전트 결과를 바꾸는 가장 큰 변수는 러너 폴더의 CLAUDE.md·설정인데 중앙 DB 에는 흔적이 없다. 설정 번호만으로는 "지시문을 고친 뒤 재작업률" 비교가 안 되어 기각. 폴더 내용 전체를 올리는 것은 비밀·용량 문제로 하지 않고 커밋 SHA·dirty 만 싣는다.
- **비용 제외.** 비용은 MVP 계획 6.2 의 지표이고 Claude CLI 가 이미 값을 주는데 러너가 버리고 있다. 제외하면 나중에 과거분을 복원할 수 없어 기각. 대신 모르는 값을 0 으로 채우지 않는다.
- **기준선을 "PR 열림 → 병합"으로.** 대부분 한 세션 안에서 끝나 대기·사람 차례를 보여주지 못하고, 도입 후 지표(이슈 열림 → 완료)와 구간이 다르다. 이슈와 연결된 병합 PR 만 쓰는 쪽이 건수는 적지만 같은 구간이라 기각.
- **별도 이벤트 저장소(모든 사건을 이벤트로 이중 기록, 이벤트 소싱).** 생성·판정·사람 요청/응답·실행 시각은 이미 테이블에 있어 이중 기록은 불일치만 만든다. 없는 세 가지만 추가 전용 테이블로 보충해 기각.
- **`contract_version: 2`.** 기존 필드의 의미가 바뀌지 않고 선택 칸만 더하므로 불필요하다(ADR-0014 4 와 같은 판단).

## 결과

- 이 phase 이전 데이터는 버전·커밋·비용·준비 판정 기록이 없다 — 지표에서 "모름"으로 따로 센다.
- 완료 시각의 비대칭: 기준선의 끝은 PR 병합이지만, 도입 후 `bug_fix` 는 자동 push·PR·merge 를 하지 않고(ADR-0014 6) 병합 확인(`merge_confirmed_at`)을 지금 `code_change` 에만 제공한다. 도입 후 "접수 → 완료"는 `merge_confirmed_at`, 없으면 묶음 시작 Task 가 `완료` 로 바뀐 `status_changed` 시각을 쓰고, 화면에 그 차이를 적는다. 결정 9 로 원본 이슈가 있는 묶음은 병합 PR 병합 시각으로 바꾼다(step 12).
- `GitHubSourceConfig.config_revision`(소스 설정의 낙관적 잠금 번호)과 `sessions.config_revision`(워크스페이스 설정 번호)은 이름이 같지만 다른 값이다. 소스 설정이 바뀔 때 둘 다 오른다.
- 공개 데모(`main`)·n8n 입구·callback 계약은 바뀌지 않는다.
