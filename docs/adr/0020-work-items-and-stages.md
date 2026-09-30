# ADR-0020: 업무(`WorkItem`)와 단계(`Task`) 분리 — 목록 한 줄 = 업무

결정일: 2026-09-30 (phase 14 step 0). 근거: [재설계 계획](../product/REDESIGN_PLAN.md) 3·5·10·16절(결정 1 "목록 한 줄 = 업무, 수정·검토·재작업·판단은 그 업무의 단계"), 벤치마킹 [가져오기·필드 매핑](../research/2026-09-29-benchmark-intake-mapping.md) "Runloom 에 가져올 점". 기본값은 [phase 14 README](../../phases/14-task-model/README.md) "계획 기본값" 11가지(사용자 결정 2026-09-29)이며 이 문서로 구현 기준을 고정한다. 적용 범위는 `service` 브랜치. 이 시점에는 구현이 없다 — 아래 이름·표는 step 1~10 이 만들고, 바꿀 때는 이 ADR·[ARCHITECTURE](../ARCHITECTURE.md) "업무와 단계 — phase 14"·[CONTRACT](../CONTRACT.md) 15절·[GLOSSARY](../GLOSSARY.md)·테스트를 같이 고친다.

계기(README "조사로 확인한 현재"): GitHub 이슈 하나가 `tasks` 2행 이상(`bug_fix` + 후속 `code_review`, 검토가 마감된 뒤 또 생기는 검토)이 되어 홈 목록이 이슈 하나를 여러 줄로 보인다. `tasks.predecessor_task_id` 가 같은 이슈의 다음 단계·서로 다른 이슈 사이 순서·폼 선행 세 뜻을 겹쳐 쓰고, 원본 찾기(`task_cycle.origin_source`)와 지표 묶음(`domain/metrics.py`)이 이 사슬을 거슬러 올라가 체인이면 여러 이슈가 한 묶음이 된다. 후속 규칙에는 "같은 업무의 다음 단계 / 새 업무" 를 가르는 칸이 없다. 실행 실패는 Task 를 `실패` 로 마감하고 알림만 보내 사람이 다시 맡길 길이 없다. 결과 브랜치 `task/<task_id>` 는 사람이 읽을 수 없다.

## 결정

1. **업무 = `WorkItem`(표 `work_items`), 단계 = `Task`.** 코드 식별자 `Task` 와 표 `tasks` 는 그대로 두고 뜻을 "업무의 한 단계" 로 좁힌다(화면 말 "단계"). 모든 Task 는 업무 하나에 속한다(`tasks.work_item_id`, ALTER 로 더함 — 칸은 NULL 허용이지만 v10 이후 repo 가 늘 채운다는 불변식). 홈 목록 한 줄 = 업무. 수정·검토·재작업(같은 Task 의 새 Execution)·다시 맡기기(새 Task)는 모두 그 업무 안에 있다. 판단(18-triage)도 새 업무가 아니라 그 업무의 단계다.
2. **업무 키 `RUN-<번호>`.** 워크스페이스마다 1 부터 매기고 재사용하지 않는다. 접두 `RUN` 은 코드 상수(`WORK_KEY_PREFIX`)다. DB 에는 번호(`work_items.key_number`)만 두고 문자열은 계산한다(`format_work_key(n) -> "RUN-n"`). `work_items` 에는 삭제 경로가 없으므로 번호는 같은 트랜잭션의 `MAX(key_number) + 1` 로 발급해도 재사용되지 않는다(`UNIQUE(session_id, key_number)` 가 경쟁을 막는다). 목록 "키" 칸은 원본 키(`source_key`)가 있으면 원본 키, 없으면 업무 키.
3. **업무 칸.** 키·제목·요청 본문·종류(대표 종류 = 첫 단계 종류)·우선순위(`high`|`normal`|`low`, 기본 `normal`)·담당(`member`|`agent` + id, 없으면 담당 없음)·업무 상태·상태 이유·원본(종류 `github`|`n8n`|`manual`, 소스 id, 원본 항목 id, 원본 키, URL, 원본 상태)·양식(`form_json`)·생성·갱신·마감 시각·revision. 데이터 등급은 18-triage 로 미룬다(판단이 쓰는 칸).
4. **업무 상태는 저장한다(`work_items.status`), 값은 순수 함수가 계산한다.** 값 8개: `새로 들어옴` · `대기` · `에이전트 작업 중` · `직접 작업 중` · `내 차례` · `PR · 검토` · `완료` · `종료`. 워커·응답 경로가 단계·사람 요청·PR·원본 신호를 `WorkItemFacts` 로 모아 `domain/work_status.work_status(facts)` 로 계산하고, 값·이유가 바뀔 때만 기록한다(업무 이벤트 한 행). 끝 상태는 `완료`·`종료` 둘. `직접 작업 중` 은 값만 예약한다 — 신호(브랜치 push·Claude Code 훅)는 16-work-ui 이후. 단계 상태(`tasks.status` — 사용자 상태 7개)와 그 판정(`domain/status.py`, 워커의 기존 쓰기)은 그대로 둔다 — `tasks` 재생성 없음.
5. **실패는 끝 상태가 아니라 `내 차례`.** 실행이 프로세스 종료 확인 실패로 끝나면 단계 Task 는 지금처럼 `실패` 로 마감하되, 워커가 같은 트랜잭션에서 그 Task 에 사람 요청 `stage_failed`(원인 키 `stage_failed:<execution_id>`)를 만든다. 업무 상태는 `내 차례`, 이유 `실패 — <사유>`. 응답 action 은 `retry`(다시 맡기기 — 같은 업무에 같은 종류의 새 단계 Task, 실패한 단계의 요청·대상·완료 기준·능력·담당 Agent 를 복사)와 `close`(닫기 — 업무 `종료`) 둘. 자동 재시도는 없다(ADR-0014 그대로). 사용자 결정 2026-09-29.
6. **후속 규칙 칸 `placement`.** `SuccessorRule.placement: Literal["same_work", "new_work"] = "same_work"`. 규칙이 만든 후속 Task 를 `same_work` 면 원인 Task 와 같은 업무의 다음 단계로, `new_work` 면 새 업무의 첫 단계로 두고 `work_item_links(type="spawned_from")` 로 잇는다. 내장 `bug_fix → code_review` 는 `same_work`. 결정은 규칙 행의 값으로만 하고 종류 이름으로 분기하지 않는다(ADR-0009). `contract_version` 은 1 그대로다(선택 칸, 기본값이 지금 동작).
7. **선행의 세 뜻을 나눈다.** `tasks.predecessor_task_id` 는 같은 업무 안 단계 순서만 뜻한다. 업무 사이 선행(n8n·체인 `blocked_by`, 직접 등록 폼의 선행)은 `work_item_links(type="blocks")` 다. 원본 찾기(`task_cycle.origin_source`)와 지표 묶음은 업무에서 읽고 선행 사슬을 거슬러 오르지 않는다. 체인(`chains`)은 n8n callback 단위라 남는다 — 체인 안 순서는 업무 사이 `blocks` 링크다. 지금 체인 선행이 하던 준비·인계 판정(`repo.tasks_with_ready_predecessor`·`predecessor_ready_execution`·`_spawn_successors`, 체인 정산의 `predecessor_status`)은 "선행 단계" 를 새로 정의해 같은 조건으로 한다: 같은 업무의 `predecessor_task_id`, 없으면 이 업무를 막는 앞 업무(`blocks` 링크가 하나일 때)의 가장 최근 단계. 체인은 한 줄이라 링크가 둘 이상인 업무는 이 phase 에 생기지 않는다.
8. **멤버.** 표 `members`(워크스페이스, 표시 이름, 역할 `admin`|`member`). 이 phase 는 워크스페이스가 생길 때(`create_session`)와 v10 마이그레이션 때 첫 관리자 1명(표시 이름 `관리자`)만 만든다. 업무 담당 자리에 멤버·Agent 를 같은 모양으로 둔다(`assignee_type`·`assignee_id`). 로그인·초대·역할 변경은 15-team. 알림·사람 요청은 지금처럼 운영자(워크스페이스) 하나에게 간다.
9. **매핑 표.** 표 `field_mappings`(워크스페이스, 원본 종류, 필드 `kind`|`priority`, 원본 값, Runloom 값, 순서). 같은 (원본 종류, 필드) 안에서 순서대로 보고 첫 일치를 쓴다. 원본 값 `*` 는 "나머지 전부". GitHub 은 라벨로 읽는다(대소문자 무시). 새 워크스페이스·마이그레이션은 `github · kind · * → bug_fix` 한 행을 seed 해 지금 동작을 유지한다. 매핑 행의 추가·변경·삭제·순서 변경은 같은 트랜잭션에서 `sessions.config_revision` +1(phase 9 설정 번호 — seed 는 올리지 않음). 이 phase 에서 매핑 표를 읽는 원본은 GitHub 뿐이다 — n8n 은 지금의 라벨 규칙(`kind:<kind>`)을 그대로 쓰고 우선순위는 `normal`.
10. **양식 칸.** GitHub 이슈 본문의 `### <제목>` 절(issue forms 결과 — `##` 도 받음)에서 목표·재현 절차·기대 동작·인수 조건 네 칸을 뽑는다. 제목 목록은 한국어·영어 동의어 표(코드 상수)로 정확히 비교(앞뒤 공백·끝 `:` 제거, 대소문자 무시)한다. 없으면 빈 칸이다. 본문은 지금처럼 요청(`request`)으로 그대로 넘긴다 — 양식은 참고 정보이며 실행 요청을 바꾸지 않는다. 칸마다 출처를 남긴다(`form_json` 의 `{value, source}`).
11. **결과 브랜치 `runloom/<업무 키>`.** 실행 요청에 선택 칸 `work_key`(패턴 `^[A-Z][A-Z0-9]{1,9}-[1-9][0-9]{0,8}$`)와 `branch_seq`(1 이상, 기본 1)를 더한다. 러너는 모델 검증으로 패턴을 확인한 뒤 브랜치를 짓는다: `branch_seq` 가 1 이면 `runloom/<work_key>`, 2 이상이면 `runloom/<work_key>-<branch_seq>`, `work_key` 가 없으면 옛 `task/<task_id>`. 이름 규칙은 `contracts/v1.result_branch` 한 곳에 두고 러너(`git_ops`)·서버(`domain/pull_request.head_branch`)가 같이 쓴다. 한 Task 의 브랜치는 그 Task 의 첫 수정 Execution 요청이 정한다 — 재작업(같은 Task 의 다음 Execution)은 첫 요청의 두 칸을 그대로 실어 같은 브랜치를 앞으로 옮긴다(force push 없음, 지금과 같다). 다시 맡기기(새 단계 Task)는 `branch_seq` 를 올려 기준 커밋에서 새 브랜치를 만든다. 이미 기록된 `task_pull_requests.head_branch` 는 바꾸지 않는다. PR 제목은 `<업무 키> <업무 제목>`(예: `RUN-23 할인 쿠폰이 두 번 적용됨`), 본문 첫 줄 `Fixes #N` 은 그대로.
12. **기존 데이터는 옮긴다(v9 → v10).** 한 트랜잭션: 업무로 묶기(`followup_links` 로 생긴 Task 는 원인 Execution 의 Task 업무로, 그 밖 Task 는 각자 새 업무) → 업무 사이 선행은 `blocks` 링크 → 키는 업무의 가장 이른 Task `created_at` 순 → 업무 상태 계산 → 첫 관리자·매핑 seed. 기준선·지표 기록(`baseline_*`·`task_events`·`executions` 측정 칸)은 그대로. 실패하면 v9 그대로 남는다(v4 → v5 방식). 사용자 결정 2026-09-29.

## 하지 않는 것

- `tasks` 테이블 재생성·이름 바꾸기, `tasks.status` CHECK 변경. ADR-0019 결정 5 가 미룬 칸 삭제(`tasks.merge_confirmed_at`·`review_decision`, `agents.shared_to_all_sessions`·`demo_scripted`, `diagnosis_usage`)도 하지 않는다 — 삭제는 `tasks` 재생성이 필요하고 이 phase 는 `tasks` 를 재생성하지 않기로 했다. 코드는 계속 읽고 쓰지 않는다.
- 되쓰기 기록 표·GitHub 새 이슈 등록(17-jira 에서 Jira 와 함께), 소스 표 이름 일반화(`github_sources` 유지 — Jira 는 전용 표를 나란히), 원본 상태 → Runloom 규칙 표(17-jira 전).
- 새 화면(담당자별 묶음·보드·상세 패널·연결 화면 — 16-work-ui). 이 phase 는 홈 목록을 업무 한 줄로 바꾸고 상세에 단계 묶음을 보이는 최소 변경(step 9)뿐이다. 매핑 표를 고치는 화면도 16 이후 — 이 phase 는 repo 함수와 seed 까지.
- 로그인·초대·멤버별 "내 차례"(15-team). 데이터 등급·판단(18-triage). `직접 작업 중` 신호(16 이후).
- 커스텀 필드 일반 매핑·양방향 필드 동기화·담당자 계정 자동 매칭(벤치마킹 권고).

## 대안

- **첫 Task 를 업무 머리로 쓰기(`tasks` 에 칸만 더함).** 표가 늘지 않지만 업무 칸(우선순위·담당·원본·양식·업무 상태)이 단계 칸과 한 행에 섞이고, 머리 Task 가 실패해 다시 맡기면(새 Task) 머리가 바뀌어야 한다. 후속 `new_work` 도 "머리" 표시를 옮겨야 한다. 기각.
- **`tasks` 를 `work_items` 로 이름 바꾸고 단계 표를 새로 만들기.** 뜻은 맞지만 `tasks` 를 참조하는 모든 표(`executions`·`followup_links`·`source_issues`·`human_requests`·`task_events`·`task_pull_requests`…)와 전 코드가 바뀐다. 기각.
- **업무 상태를 저장하지 않고 화면마다 계산.** 저장 칸이 없어 단순하지만 목록 필터·정렬·상태 변화 이벤트(모니터링)에 매번 단계·요청·PR 을 모두 읽어야 한다. 계산은 순수 함수로 두고 결과만 저장한다. 기각.
- **실패를 별도 끝 상태로(`실패`).** 목업 보드에 칸이 없고, 사람이 다시 맡기거나 닫을 때까지 끝난 것이 아니다. 사용자 결정으로 `내 차례 · 실패 — …`. 기각.
- **업무 키 문자열을 저장.** 접두가 코드 상수 하나라 번호만 저장하면 충분하고, 브랜치·PR 제목에서 키를 읽을 때 번호로 찾는다. 기각.

## 결과

- 목록·지표·원본 찾기의 단위가 업무 하나로 모인다. 체인은 여러 업무를 `blocks` 로 잇는 묶음이 되고 지표 묶음과 섞이지 않는다.
- 후속이 같은 업무인지 새 업무인지를 규칙 행으로 정할 수 있다(17·18 의 판단·새 업무 등록이 이 칸을 쓴다).
- 실패한 업무를 사람이 다시 맡길 수 있다. 다시 맡긴 단계는 새 Task·새 브랜치(`-2`)라 이전 시도의 브랜치·PR 과 섞이지 않는다.
- **업그레이드 순서**: 실행 요청(서버 → 러너)에 새 칸이 생기므로 구버전 러너는 `work_key` 가 든 요청을 `extra="forbid"` 로 거부한다. 셀프호스트는 중앙(`install.sh`)과 러너(`install-runner.sh`)를 같은 체크아웃으로 함께 올린다 — step 10 이 SELFHOST 문서에 순서를 적는다.
- v10 이전에 첫 수정 Execution 이 있던 Task(마이그레이션된 진행 중 업무)는 그 요청에 `work_key` 가 없으므로 끝까지 `task/<task_id>` 브랜치를 쓴다(결정 11 의 "첫 요청이 정한다").
- `predecessor_task_id` 로 미리 등록한 검토 Task(후속 결정 표의 `link_existing` 두 번째 경우)는 마이그레이션에서 `followup_links` 가 없으면 별도 업무 + `blocks` 링크가 된다.

16-work-ui 이후: 결정 4 의 `직접 작업 중` 신호는 [ADR-0022](0022-work-screen-and-direct-work.md) 결정 8(버튼 + 키 든 PR)이 채운다.
