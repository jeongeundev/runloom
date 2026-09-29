# Phase 14 — 업무(목록 한 줄)와 단계(Task) 분리

작성일: 2026-09-29. 상태: 구현 계획 작성 완료, 모든 step pending. **13-selfhost-only 가 `service` 에 병합된 뒤 실행한다**(스키마 v9·내장 종류 2개·demo 코드 없음을 전제). 근거: [재설계 계획](../../docs/product/REDESIGN_PLAN.md) 5절·16절, 목업 https://claude.ai/artifact/Jx6Pa7PmRZo1hmvFuiH66C.

## 조사로 확인한 현재 (2026-09-29, `service` d7bd9ae)

- GitHub 이슈 하나 = `tasks` 보통 2행: `bug_fix` Task(원본 이슈·PR·댓글·재작업 횟수가 붙음) + 후속 `code_review` Task("커밋 검토: <제목>", `followup_links` 로 생김). 재작업은 같은 `bug_fix` Task 의 새 Execution(`start_key` `rework:<검토 exec>`), 재검토는 같은 `code_review` Task 의 새 Execution. 검토 Task 가 마감된 뒤 수정이 다시 결과를 내면 검토 Task 가 하나 더 생긴다. 홈 목록(`web._base.my_tasks`)은 tasks 행을 한 줄씩 보여 이슈 하나가 두 줄 이상이 된다.
- `tasks.predecessor_task_id` 가 세 뜻으로 겹친다: 같은 이슈의 다음 단계(worker `_create_followup_task`), 서로 다른 이슈 사이 순서(체인 `compose` `blocked_by`), 폼에서 지정한 선행. `task_cycle.origin_source` 는 선행을 거슬러 올라가 원본 이슈를 찾는다. 지표 묶음(`domain/metrics.py` 루트 찾기)도 이 사슬이라 체인이면 여러 이슈가 한 묶음이 된다.
- GitHub 이슈 → Task: `domain/issue_intake.snapshot_to_task_spec` — 종류 `bug_fix` 고정, `source_ref = owner/repo#N`, 제목·본문만. 담당·라벨은 `source_issues.snapshot_json` 에만.
- `SuccessorRule` 에는 "같은 업무의 다음 단계 / 새 업무" 를 가르는 칸이 없다. 재작업은 `domain/task_followup._after_review` 에 고정.
- 결과 브랜치 `task/<task_id>`: 러너 `connector/git_ops._task_branch`, 서버 `domain/pull_request.head_branch`, 댓글 문구 `server/github_delivery.py`. PR 제목 = 원본 이슈 제목.
- 실행이 프로세스 종료 확인 실패면 `worker._reflect_failures` 가 Task 를 `실패` 로 마감하고 알림만 보낸다 — 사람이 다시 맡길 길이 없다.

## 계획 기본값 (사용자 결정 2026-09-29, step 0 이 ADR-0020 으로 고정)

1. **업무 = `WorkItem`(표 `work_items`), 단계 = `Task`.** 코드 식별자 `Task` 는 그대로 두고 뜻을 "업무의 한 단계" 로 좁힌다(화면 말 "단계"). 모든 Task 는 업무 하나에 속한다(`tasks.work_item_id`). 목록 한 줄 = 업무.
2. **업무 키**: 워크스페이스마다 `RUN-<번호>`(1부터, 재사용 없음). 접두 `RUN` 고정. 목록 "키" 칸은 원본 키가 있으면 원본 키, 없으면 업무 키.
3. **업무 칸**: 키·제목·요청 본문·종류(대표 종류 = 첫 단계 종류)·우선순위(`high`|`normal`|`low`, 기본 `normal`)·담당(`member`|`agent` + id, 없으면 담당 없음)·업무 상태·상태 이유·원본(종류 `github`|`n8n`|`manual`, 소스 id, 원본 키, URL, 원본 상태)·양식(`form_json`: 목표·재현 절차·기대 동작·인수 조건, 칸마다 출처)·생성·갱신·마감 시각·revision. 데이터 등급은 18-triage 로 미룬다.
4. **업무 상태**(저장, 단계·사람 요청·PR·원본 신호에서 순수 함수로 계산): `새로 들어옴`(담당 없음·지시 전) · `대기`(맡겼지만 시작 전 — 준비 대기·연결 끊김) · `에이전트 작업 중` · `직접 작업 중`(값만 예약 — 신호는 16-work-ui) · `내 차례`(열린 사람 요청·검토 대기·**실패**) · `PR · 검토`(PR 열림) · `완료` · `종료`. 끝 상태는 완료·종료 둘. 실패는 `내 차례` + 이유 "실패 — …" 로 보이고 사람이 [다시 맡기기]·[닫기] 를 고른다(사용자 결정 2026-09-29). 단계 상태(`tasks.status`)는 그대로 둔다 — `tasks` 재생성 없음.
5. **후속 규칙 칸 `placement`**: `SuccessorRule.placement: "same_work" | "new_work"`, 기본 `same_work`. 내장 `bug_fix → code_review` 는 `same_work`. `new_work` 면 새 업무를 만들고 `work_item_links(type="spawned_from")` 로 잇는다. 종류 이름 분기 없음(ADR-0009).
6. **선행의 세 뜻 분리**: `tasks.predecessor_task_id` 는 같은 업무 안 단계 순서만. 업무 사이 선행(체인 `blocked_by`, 폼 선행)은 `work_item_links(type="blocks")`. 원본 찾기는 업무에서(`work_items.source_*`), 선행 사슬을 거슬러 오르지 않는다.
7. **멤버**: 표 `members`(워크스페이스, 표시 이름, 역할 `admin`|`member`). 이 phase 는 첫 관리자 1명(워크스페이스가 생길 때·마이그레이션 때)만 만든다. 로그인·초대는 15-team.
8. **매핑 표**: 표 `field_mappings`(워크스페이스, 원본 종류, 필드 `kind`|`priority`, 원본 값, Runloom 값, 순서). 위에서부터 첫 일치. GitHub 는 라벨로 읽는다. 기본 행 `github · kind · * → bug_fix` 를 seed 해 지금 동작을 유지한다. 매핑이 바뀌면 워크스페이스 `config_revision` +1(phase 9 설정 번호).
9. **양식 칸**: GitHub 이슈 본문의 `### <제목>` 절(issue forms 결과)에서 목표·재현 절차·기대 동작·인수 조건을 뽑는다. 제목 목록은 한국어·영어 동의어 표(코드 상수)로. 없으면 빈 칸 — 본문은 지금처럼 요청으로 그대로 넘긴다.
10. **결과 브랜치 `runloom/<업무 키>`**: 실행 요청에 선택 칸 `work_key` 를 더하고, 러너가 패턴(`^[A-Z][A-Z0-9]{1,9}-[1-9][0-9]{0,8}$`)을 검사한 뒤 브랜치를 짓는다. 칸이 없으면 옛 `task/<task_id>`. 다시 맡기기(새 단계)는 선택 칸 `branch_seq` 로 `runloom/<키>-<순번>`(기준 커밋에서 새로, force push 없음). 이미 기록된 `task_pull_requests.head_branch` 는 바꾸지 않는다. PR 제목 `RUN-23 <제목>`.
11. **기존 데이터는 옮긴다**(사용자 결정): v9 → v10 이 선행 사슬로 같은 업무 단계를 묶어 업무 행·키(생성 시각 순)를 만들고, 업무 상태를 계산해 넣고, 첫 관리자를 만든다. 기준선·지표 기록은 그대로.

**하지 않는 것**: 되쓰기 기록 표·GitHub 새 이슈 등록(17-jira 에서 Jira 와 함께), 소스 표 이름 일반화(`github_sources` 유지 — Jira 는 전용 표를 나란히), 원본 상태 → Runloom 규칙 표(17-jira 전), 새 화면(16-work-ui — 이 phase 는 목록을 업무 한 줄로 바꾸는 최소 변경만).

## Step 목록

| Step | 이름 | 산출물 |
|---|---|---|
| 0 | task-model-design | ADR-0020, ARCHITECTURE 절, CONTRACT 예시, GLOSSARY(`WorkItem`, `Task` 뜻 변경) |
| 1 | work-status-domain | `domain/work_status.py` 업무 상태 순수 함수 |
| 2 | schema-v10 | `work_items`·`work_item_links`·`members`·`field_mappings`·`tasks.work_item_id`, v9 → v10 마이그레이션 |
| 3 | work-item-repo | 업무 생성(키 발급)·조회·목록·담당·상태 기록 |
| 4 | succession-placement | `SuccessorRule.placement`, 후속을 같은 업무/새 업무로, 원본 찾기를 업무에서 |
| 5 | intake-work-items | GitHub·n8n·직접 등록이 업무 + 첫 단계, 양식 칸 추출, 매핑 표 |
| 6 | work-status-worker | 워커가 업무 상태 기록, 실패 → 내 차례 · [다시 맡기기]·[닫기] |
| 7 | branch-key | `work_key` 실행 요청 칸, 브랜치 `runloom/<키>`, PR 제목 |
| 8 | metrics-by-work | 지표 묶음 = 업무 |
| 9 | work-list-minimal | 홈 목록 = 업무 한 줄, 상세에 단계 묶음 |
| 10 | task-model-verify | e2e 업무 기준, v9 사본 마이그레이션, 문서·인계 |

## 실행

```bash
git status --short            # 깨끗해야 한다
git branch --show-current     # service (13-selfhost-only 병합 뒤)
python3 scripts/execute.py 14-task-model --engine claude
```

사용자가 띄워 둔 셀프호스트(compose 프로젝트 `runloom`)·러너(launchd)·`/Users/kje/demo/*` 는 건드리지 않는다. 셀프호스트 재설치·sandbox 실연동은 phase 뒤 사용자 지시로.
