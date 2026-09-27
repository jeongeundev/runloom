# Phase 9 — 측정: 업무 이벤트 보충·지표·기준선

작성일: 2026-09-27. 상태: step 0~13 완료(2026-09-27, `feat-9-measure`). step 11~13 은 같은 날 사용자 합의로 추가 — 완료 시각을 GitHub 병합 시각으로 맞추기, 재작업 상한 요청 중복 결함 수정. 근거: [MVP 계획](../../docs/product/MVP_PLAN.md) 6절(측정 설계)·10절·11절, [ROADMAP](../../docs/product/ROADMAP.md) 9절(지표 정의).

## 목표

시스템이 남긴 기록만으로 도입 후 지표를 계산하고, OpenArchive(`jeongeundev/OpenArchive`)의 도입 전 GitHub 이력 기준선과 나란히 보여준다. 사람이 보고한 숫자를 쓰지 않는다. 새 저장소를 만들기보다 기존 테이블(`tasks`·`executions`·`execution_events`·`task_verdicts`·`followup_links`·`human_requests`·`human_responses`)에서 계산하고, 빠진 시점·버전·비용만 보충한다.

## 현재 코드에서 확인한 간극 (2026-09-27)

| 필요한 것 | 지금 | 위치 |
|---|---|---|
| 착수 가능해진 시각, 대기 사유 변화 | 없음. 준비 판정은 매 주기 계산만 하고 저장하지 않는다 | `domain/task_readiness.py`, `server/task_cycle.py`, `server/worker.py` |
| Task 상태 변화 이력 | 없음. `tasks.status` 를 덮어쓴다 | `adapters/repo.py` `update_task_status`·`finish_task`·`record_verdict` 등 |
| 규칙/설정 버전 | 없음. `followup_links.rules_revision` 은 1 고정 | `server/worker.py` (`"rules_revision": 1`) |
| 러너 폴더 커밋 | 없음. 등록 때 HEAD 만 `agents.base_commit` 으로 보고 | `connector/cli.py` `_register` |
| 비용·토큰 | 없음. Claude CLI JSON 의 `total_cost_usd`·`usage` 를 러너가 버린다. 계약에 칸이 없다 | `connector/claude.py`, `contracts/v1.py` |
| 기준선 | 없음. GitHub 어댑터는 이슈·댓글 REST 만 있다 | `adapters/github_client.py` |

## 계획 기본값 (2026-09-27 사용자 결정 반영 — step 0 이 ADR-0015 로 고정)

1. **버전 = 워크스페이스 설정 번호 + 러너 폴더 커밋.**
   - 설정 번호: `sessions.config_revision`(1 부터). 종류 추가·삭제, 후속 규칙 추가·삭제, GitHub 소스 설정 생성·변경이 같은 트랜잭션에서 1 올린다. 실행 생성 때 `executions.config_revision` 에 찍고, `followup_links.rules_revision` 에도 이 값을 쓴다.
   - 러너 폴더 커밋: 러너가 실행을 시작할 때 **로컬 등록 저장소 폴더(worktree 가 아닌 등록 경로)** 의 HEAD 와 dirty 여부를 읽어 `started` 이벤트에 싣는다. 그 폴더의 CLAUDE.md·에이전트 설정 변화를 가리키는 값이다. 읽지 못하면 비운다(실행을 막지 않는다).
2. **비용을 중앙까지 가져온다.** `result_ready`·`failed` 이벤트에 선택 칸 `usage` 를 둔다. 값: `cost_usd`(Claude `total_cost_usd`), `input_tokens`, `output_tokens`. 모르는 값은 null 이며 화면·API 에서 0 이 아니라 "모름"이다. Codex 는 토큰만 알면 토큰만, 모르면 전부 null.
3. **계약은 `contract_version` 1 유지, 추가형 선택 칸.** `_Contract` 가 `extra="forbid"` 이므로 서버를 먼저(또는 함께) 올린다. 새 서버는 칸이 없는 구버전 러너 이벤트를 그대로 받는다.
4. **업무 이벤트는 추가 전용 `task_events` 한 테이블.** 기록 대상은 기존 테이블에 없는 것만:
   - `status_changed`: Task 상태가 실제로 바뀔 때마다(이전·다음 상태, 사유) — 상태를 바꾸는 repo 함수 안, 같은 트랜잭션.
   - `blocked`: 준비 판정이 막혔고 대기 코드 집합(정렬된 code 목록과 각 actor)이 직전 기록과 다를 때만.
   - `ready`: 준비 판정이 통과해 실행을 만들 때(직전 기록이 `ready` 가 아닐 때만).
   - 각 행: `task_id`, `session_id`, `type`, `task_revision`, `config_revision`, `occurred_at`(워커/요청 시각, 서버 시계), `data_json`.
   - 생성·판정·사람 요청/응답·후속 생성·실행 시각은 이미 있는 테이블에서 읽고 중복 기록하지 않는다.
5. **스키마 v5 → v6 마이그레이션은 데이터 보존·한 트랜잭션.** 기존 실행의 새 칸은 NULL(= 모름). 기존 세션 `config_revision` 은 1.
6. **기준선 = OpenArchive "이슈 열림 → 그 이슈를 닫은(연결된) 병합 PR"**(2026-09-27 조회 기준 약 19건). PR→병합만 있는 이력은 쓰지 않는다. GitHub GraphQL(`closingIssuesReferences` 또는 이슈의 `closedByPullRequestsReferences`)로 가져오고, 도입 전 = 해당 GitHub 소스 연결 시각(`github_sources.created_at`) 이전에 열린 이슈. 가져오기는 운영자가 실행하고 멱등이다. 화면·API 에 "하네스·Claude 사용 시기 이력 — 순수 수작업 기준 아님" 주석과 n 을 붙인다.
7. **지표는 순수 도메인 함수(`domain/metrics.py`)** 가 DB 행이 아닌 값 객체로 계산한다. 중앙값·n·미완료 건수를 함께 낸다. 인과적 효과로 단정하는 문구를 쓰지 않는다.

## 지표 정의 (step 0 이 ARCHITECTURE 에 표로 옮긴다)

"업무 묶음" = 원본 이슈에서 온 첫 Task(`source_issues.task_id`)와 그 후속들(`followup_links`, `predecessor_task_id`). 직접 등록 Task 는 자기 자신이 묶음의 시작.

| 영역 | 지표 | 정의 |
|---|---|---|
| 병목 | 인계 대기 | 후속 Task 생성(= 선행 판정으로 조건 충족) → 첫 실행 `started_at`. `blocked` 구간을 actor(operator/assignee/system)별로 나눠 입력 부족·승인 대기를 별도 집계 |
| 속도 | 접수 → 사람 차례 | 이슈 열림(스냅샷의 이슈 생성 시각, 없으면 Task `created_at`) → 묶음에서 처음 사람 차례가 된 시각(첫 `human_requests.created_at` 또는 첫 `status_changed` → `확인 필요` 중 이른 것) |
| 속도 | 접수 → 완료 | 이슈 열림 → 그 이슈를 닫은 병합 PR 의 병합 시각(`source_issues.pr_merged_at`, step 11~12). 기준선과 같은 구간. 직접 등록 Task 는 `merge_confirmed_at`/`완료` 전환 |
| 속도 | 접수 → 승인 | 이슈 열림 → 묶음에서 처음 운영자 승인(`완료` 전환) 시각 |
| 사람 부담 | 개입 횟수, 응답 시간 | 묶음당 `human_requests` 수 + 운영자 검토 결정 수, 요청 → 응답(`answered_at`) |
| 품질 | 1회 통과율, 재작업 횟수, 사람 거부 비율 | 첫 `code_review` 결과가 `approved` 인 묶음 비율, 묶음당 `changes_requested` 로 생긴 재작업 수, 운영자 검토 결정 중 `request_changes`·`close` 비율 |
| 비용 | 실행 시간, 비용, 토큰 | `started_at`→`finished_at`, `cost_usd`·토큰 합계와 "모름" 건수 분리 |
| 신뢰성 | 실패율, 실패 사유, 재실행 | `failed` 실행 비율, `failed_code` 분포, `attempt_no > 1` 건수 |

모든 지표는 기간(from/to)과 묶음 기준(`config_revision` 또는 `folder_commit`)으로 나눌 수 있다.

## Step 목록

| Step | 이름 | 산출물 |
|---|---|---|
| 0 | measure-design | ADR-0015, ARCHITECTURE "측정 — phase 9" 절, CONTRACT 예시, GLOSSARY |
| 1 | contract-usage | `contracts/v1.py` 선택 칸 (`folder_commit`·`folder_dirty`·`usage`) |
| 2 | connector-report | 러너가 폴더 커밋·비용·토큰을 보고 |
| 3 | schema-v6 | v5 → v6 마이그레이션 (`task_events`·설정 번호·실행 칸·기준선 테이블) |
| 4 | repo-events | repo: 상태 변경 이벤트, 설정 번호 증가, 실행 이벤트의 커밋·비용 저장 |
| 5 | worker-ready | 워커: `ready`/`blocked` 기록, 실행·후속에 설정 번호 |
| 6 | metrics-domain | `domain/metrics.py` 순수 계산 |
| 7 | baseline-import | GitHub GraphQL 이력 조회 + 운영자 가져오기 |
| 8 | metrics-api | 지표 API JSON·CSV |
| 9 | metrics-view | 지표 화면 |
| 10 | measure-verify | e2e, 문서·인계 갱신 |
| 11 | merge-link-storage | 이슈 하나의 병합 PR 조회, `source_issues` 병합 칸(v6 안) |
| 12 | done-from-github | 동기화가 병합 시각 저장, 접수 → 완료 = 병합 시각, 접수 → 승인 별도 |
| 13 | rework-request-dedup | 재작업을 이미 일으킨 검토가 상한 요청을 또 만드는 결함 수정 |

모든 step 은 실제 GitHub·유료 모델 없이 대역으로 구현·검증한다. 실제 OpenArchive 기준선 가져오기(읽기 전용 GitHub 호출)는 phase 뒤 사용자 지시로 한다.

## 실행

```bash
git status --short            # 깨끗해야 한다
git branch --show-current     # service
python3 scripts/execute.py 9-measure --engine claude
```

하네스가 `feat-9-measure` 를 만들고 step 마다 커밋한다. 완료 후 `service` 에 `--no-ff` 병합은 별도 단계. `main`·공개 데모 VM 은 건드리지 않는다.

## 범위 밖

- 새 업무의 기준 커밋이 등록 시점 HEAD 에 고정되는 문제 → `11-real-repo`.
- 알림 웹훅, 셀프호스트 패키징, n8n 주간 리포트 예시, 수동 처리 모드 기준선, 고급 ROI 대시보드.
