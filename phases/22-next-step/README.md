# Phase 22 — 결과 뒤 판단: 상주 판단 에이전트가 사이클을 닫는다

작성일: 2026-10-04. 상태: 설계 완료, 실행 전. **`service`(21 사내 요청 병합 `332da59`, 스키마 v23)에서 실행한다.** 병합은 phase 뒤 사용자 지시로(`--no-ff`). 근거: [ADR-0009](../../docs/adr/0009-registered-kinds-and-succession-rules.md)(종류·후속 규칙은 등록 데이터), [ADR-0025](../../docs/adr/0025-triage.md)(판단), [ADR-0023](../../docs/adr/0023-cross-member-delegation.md)(맡기기 정책), [사내 요청](../../docs/product/INTERNAL_REQUESTS.md)·[조사·반환](../../docs/product/INTERNAL_REQUEST_INVESTIGATION.md)·[담당 범위 표](../../docs/product/RESPONSIBILITY_DIRECTORY.md), [Multica 진행 방식 조사](../../docs/research/2026-10-04-multica-how-work-flows.md).

## 왜

Runloom 의 사이클은 **업무 등록 → 배정 → 에이전트 작업 → 결과에 따라 업무 등록** 이다. 이 사이클이 사람 없이 이어지면 업무 사이 병목이 없다. 지금은 두 곳에서 끊긴다.

1. **배정 판단이 접수 때 한 번뿐이다.** 판단 에이전트(phase 19)는 `새로 들어옴`·담당 없음 업무만 본다. 결과가 나온 뒤 후속 규칙에 맞는 것이 없으면 아무 일도 일어나지 않고(`worker._apply_followup` 의 `none`), `needs_information` 은 사람 요청으로 멈춘다.
2. **바깥 담당을 모른다.** 판단 후보는 워크스페이스 멤버·에이전트뿐이다. 21 의 담당 범위 표와 사내 요청은 사람이 손으로만 쓴다. 요청이 만들어져도 받는 사람에게 알림이 없고, 반환된 결과는 원래 업무로 이어지지 않는다.

Multica 는 이 자리를 squad 리더 LLM(자유 텍스트 지시, 권한 전부 열림, 댓글로 인계)이 메운다. Runloom 은 **프로젝트에 상주하는 판단 에이전트가 등록된 후보(종류·멤버·에이전트·담당 범위) 안에서만 다음 행동을 제안**하고, 사람이 한 번 눌러 확정한다. 이 phase 는 판단을 결과 뒤로 넓히고 담당 범위 표를 후보에 넣는다. 프로젝트별 판단 기준·한 화면 등록은 phase 23.

장면(공개 사례 K1 각색, RUN-26): 수정 에이전트가 `needs_information`(LXC 호스트 sysctl 값 필요) → 결과 뒤 판단이 "kube_proxy 담당 N 에게 사내 요청: 호스트 설정값 확인" 제안 → 사람이 [제안대로] → 요청 생성, N 에게 알림 → N 수락·조사·검토·반환 → 원래 업무에서 결과 뒤 판단이 다시 → "반환 결과를 넣어 수정 에이전트에게 재작업" 제안.

## 사용자 결정 (2026-10-04)

1. **D1 언제** — 결과 뒤 판단은 세 경우에만 돈다: ① 후속 규칙에 맞는 것이 없는 결과(판정 통과) ② `needs_information` ③ 사내 요청 반환. 판정 실패·실행 실패는 지금처럼 사람에게 간다.
2. **D2 무엇을** — 다음 행동은 네 가지: 같은 업무의 다음 단계·재작업 / 새 업무 / 외부 담당에게 사내 요청 / 사람에게 돌려보내기(확인 필요). "완료"는 제안하지 않는다(PR 병합·사람이 정한다).
3. **D3 어떻게** — 판단은 제안까지. 사람이 [제안대로] 한 번 누른다. 결과 뒤 판단의 자동 시작은 이번에 없다. 접수 판단의 자동 시작 자격 건수(`triage_handled_counts`)에 섞지 않는다. 판단이 만든 사내 요청의 요청자는 [제안대로] 를 누른 멤버다.
4. **D4 받는 쪽** — 받는 사람의 수락·검토는 그대로(동의). 대신 요청 생성 시 받는 사람에게 알림, 반환되면 요청자의 수동 재개 없이 원래 업무의 결과 뒤 판단이 자동으로 이어진다(재개 기록 기능은 그대로 둔다).

## 계획 기본값 (step 0 이 ADR-0027 로 고정 — 코드 근거가 있으면 바꾸고 이유를 남긴다)

1. **같은 판단, 다른 원인.** 내장 종류 `triage`·표 `triage_logs`·판단 Agent(저장소 카드 `triage_agent_id`)·판단 기준·읽기 전용 체크아웃을 그대로 쓴다. 판단 로그에 원인 `cause` ∈ `intake`(기존)·`after_result`·`request_returned` 와 원인 참조 `cause_execution_id`(executions, NULL 허용)·`cause_request_id`(internal_requests, NULL 허용)를 둔다. 같은 원인에 판단은 하나(부분 UNIQUE). `trigger`(auto/manual)는 그대로.
2. **계약 확장(러너 재설치 필요).** `TriageTarget` 에 `mode: "intake" | "next_step"`(기본 `intake`, 직렬화 시 기본값이면 빠지게 해 옛 러너 호환을 지킨다 — 방법은 step 1 이 정한다). `TriageResult` 에 `next_action: NextAction | None` — `mode=next_step` 이면 필수, `intake` 면 금지. `NextAction` 은 `type` 으로 가르는 4형:
   - `stage` `{kind, assignee{type,id}, rework: bool}` — 같은 업무에 다음 단계(또는 `rework=true` 면 원인 단계 재작업)
   - `new_work` `{kind, title(1~120자), assignee}` — `spawned_from` 으로 잇는 새 업무
   - `internal_request` `{system_id, request_kind, recipient_member_id, purpose(1~2000자)}` — 담당 범위 표 항목 중 하나
   - `human` `{question(1~500자)}` — 사람에게 돌려보내기
   `next_step` 모드에서는 `proposed_kind`·`assignee`·`predecessors` 를 비운다(접수 판단 전용). `proceed`·`confidence`·`reasons`·`missing_information` 은 공통.
3. **러너 능력** `after_result_triage` 를 `RUNNER_CAPABILITIES` 에 더한다. 중앙은 판단 Agent 러너의 마지막 claim `capabilities` 에 이 값이 있을 때만 결과 뒤 판단을 시작한다. 없으면 지금 동작(사람 요청 또는 조용히 `확인 필요`)으로 간다 — 옛 러너는 결과 뒤 판단만 못 한다.
4. **후보 확장.** `TriageCandidates` 에 `responsibilities: [{system_id, request_kind, recipient_member_id, recipient_name, judgment_member_id, has_agent}]`(최대 50, 활성 항목만 — `entry_problem` 이 None 인 것). `next_step` 모드의 `stage` 종류 후보 = 입력이 없는 종류 + 입력 종류가 원인 실행의 산출물 종류로 채워지는 종류(ADR-0009 인계 규칙). 지금 단계 종류가 `input_kinds` 를 가져도 `labels[current_kind]` KeyError 가 나지 않게 고친다(`domain/triage.py:180` 부근, 조사 C3).
5. **요청문.** 기존 절에 더해 `## 이전 결과`(원인 실행의 종류·outcome·판정·결과 요약), `## 사람 응답`(원인 Task 의 최근 응답), `## 반환된 사내 요청`(요청 목적·반환 요약). 외부 문자열은 기존처럼 펜스 안에. `## 답하는 법` 은 모드별 고정 문구.
6. **시작 지점(워커).**
   - ① 순환 종류: `_apply_followup` 에서 결정이 `none` 이고 `hold_code` 가 없을 때(규칙 없음·후속 대상 아님). 사용자 정의 종류: `_check_generic_results` 판정 통과 뒤 그 Task 에 후속 Task(`predecessor_task_id`)가 없을 때.
   - ② `needs_information`(`fix_needs_information`·`review_needs_information`): 결과 뒤 판단을 시작할 수 있으면 사람 요청 대신 판단. 시작할 수 없거나 판단이 실패·무시되면 **원래 사람 요청을 그때 연다**(사이클이 조용히 멈추지 않게).
   - ③ 반환: 반환 기록(`internal_request_investigations.returned_at`)이 있고 그 요청에 `request_returned` 판단이 없으면 원래 업무에서 시작.
   - 시작 조건: 원래 업무의 원본 설정(`task_cycle.origin`)에 판단 Agent 가 있음, 판단 Agent 시작 가능(ADR-0025 결정 5) + 능력 `after_result_triage`. 직접 등록·n8n·조사 업무는 판단 Agent 가 없으므로 지금 동작.
   - 동시성: 워크스페이스에 `running` 판단은 1건(접수·결과 뒤 합쳐서), 러너가 빌 때만. 결과 뒤 판단을 접수 판단보다 먼저 고른다(진행 중 업무가 막혀 있으므로). tick 순서는 step 0 이 정한다.
7. **[제안대로] (`accept_next_step`).** 최신 `next_step` 판단이 `proposed`·처리 없음·원인이 그대로일 때만(아니면 409 `next_step_stale`). 행동별:
   - `stage`: 원인 Task 를 마감(판정 통과면 `완료`, `needs_information` 이면 `완료` 가 아니라 기존 사람 응답 `resume` 과 같은 재착수 — step 0 이 확정)하고 같은 업무에 다음 단계(`predecessor_task_id` = 원인 Task, 입력 산출물은 ADR-0009 인계)를 만들어 담당에게 맡긴다. `rework=true` 면 원인 Task 에 판단 근거를 지적 산출물로 넣어 새 실행.
   - `new_work`: `create_followup_once(placement=new_work)` 와 같은 모양으로 새 업무 + `spawned_from`, 원인 Task 는 `완료`.
   - `internal_request`: `internal_request_store` 의 생성 경로를 그대로(요청자 = 누른 멤버, `submission_key` = 판단 id 기반, 담당표 revision = 판단 시점 값 — 바뀌었으면 409 `stale_directory`), 요청에 `created_by_triage_id`. 원인 Task 는 그대로 두고 업무 이유를 `사내 요청 대기`.
   - `human`: 원인 Task 에 사람 요청(새 코드 `next_step_human`, 질문 = 제안 문구)을 연다.
   - [무시] = `dismissed` + ②의 원래 사람 요청(없으면 `next_step_human` 과 같은 일반 확인 요청)을 연다.
   - 처리 기록은 `triage_logs.handling`(`accepted`·`dismissed`). 담당이 이미 있는 업무에서도 남긴다.
8. **알림(v24).** 새 사건 `internal_request_received`(받는 사람 = 요청 수신자, 판단이 만든 것·사람이 만든 것 모두) · `next_step_proposed`(받는 사람 = `turn_recipients_of`). 같은 요청·판단에 한 번(`dedupe_key`).
9. **스키마 v24.** `triage_logs` 재생성(원인 칸·CHECK·부분 UNIQUE 둘, 기존 행은 `cause='intake'`), `notifications` 재생성(사건 CHECK), `internal_requests.created_by_triage_id`(NULL 허용). `human_requests.code` 는 CHECK 가 없다(`db.py` human_requests DDL) — `next_step_human` 은 표 변경 없이 코드 값과 `human_api._ACTIONS` 만 더한다. v23 fixture.
10. **화면.** 업무 패널에 "다음 단계 제안" 절(원인·행동·근거·확신도·[제안대로]·[무시]·실패 이유), 업무 이유 `다음 단계 판단 중`·`다음 단계 제안 · <행동>`·`사내 요청 대기`, 목록 배지. `/requests` 에 "판단 제안으로 생성" 표시.

## 조사로 확인한 현재 (2026-10-04, `service` `aae864a`)

- tick 순서 `worker.py:398-417`: 판정 3개(:402-404) → `_judge_triage`(:405) → `_autostart_triaged`(:406) → `_advance_cycle`(:407) → `_spawn_successors`(:408) → `_start_waiting_stages`(:409) → `_triage_new_work`(:410) → `_reflect_failures`(:411) … `_refresh_work_statuses`(:417).
- 판정: `_check_code_results`(:570)·`_check_review_results`(:649)·`_check_generic_results`(:692) 모두 `record_verdict(status="확인 필요", finish=False)` — 실행 잠금 유지, 업무 `내 차례`·`검토 대기`.
- 순환 후속: `_cycle_followups`(:766) → `decide_followup`(`domain/task_followup.py:158`) → `_apply_followup`(:850). 규칙 없음 = `FollowupDecision("none", …)`(task_followup.py:87-88)·검토 outcome 후속 대상 아님(:124) → `_apply_followup` 에서 어느 가지에도 안 걸림(:886-888). `needs_information` → `request_human`(:880-885, `_request_human` :1667, `create_human_request_once` repo.py:3709, 코드 `fix_needs_information`·`review_needs_information`, cause_key `"{code}:{execution_id}"`).
- 사용자 정의 종류 후속: `_spawn_successors`(:1187)는 미리 만든 후속 Task 만 착수. 후속 Task 가 없으면 결과를 보지 않는다.
- 사람 응답: `human_api.respond_to_request`(human_api.py:123) → `record_human_response_once`(repo.py:3851) → 다음 tick `_resume`(worker.py:1017).
- 판단: 대상 `repo.auto_triage_works`(repo.py:4263, 판단 로그 행이 하나라도 있으면 제외), 경로 판정 `triage_runs.triage_route`(triage_runs.py:86, `not_new` :94), 시작 `request_triage`(:172) → `repo.start_triage`(repo.py:4343, 처리 없는 proposed·failed 를 superseded :4359), 자동 `_triage_new_work`(worker.py:1398, 워크스페이스 1건 :1404, 러너 빔 :1413), 판정 `triage.validate`(triage.py:238), 기록 `record_triage_proposed`/`failed`(repo.py:4399/4419), 수락 `work_actions.accept_triage`(work_actions.py:256), 처리 기록 `_assign_work_item`(repo.py:687, 담당 없음→정함일 때만), 자동 시작 `_autostart_triaged`(worker.py:1368)·`triage_handled_counts`(repo.py:4560). 요청문 `compose_triage_request`(triage.py:171)에 이전 결과·사람 응답 절 없음. 후보 `assemble_candidates`(triage.py:74), `startable_kinds`(:55, 입력 있는 종류 제외). 판단 단계 제외 `is_triage_kind`(execution_policy.py:56)·`_TRIAGE_STAGE`(repo.py:134).
- 계약: `TriageTarget`(contracts/v1.py:169), `TriageResult`(:743), `TriageCandidates`(:806), `RUNNER_CAPABILITIES`(:351, `verify_only` 하나). 러너 판단 스키마 `TRIAGE_OUTPUT_SCHEMA`(connector/local_tool.py:140), 실행 `_run_triage`(:609), claim 능력 보고 `connector/client.py:117-123`.
- 판단 로그 DDL `adapters/db.py:789-827`: `trigger` CHECK(auto,manual), `state`(running,proposed,failed,superseded), `handling`(accepted,changed,dismissed,auto_started), 부분 UNIQUE `ux_triage_logs_running`. 알림 `NOTIFICATION_EVENTS`(db.py:516) — `notifications.event` CHECK 라 사건을 더하려면 표 재생성(v13 선례).
- 사내 요청: 생성 `internal_request_store.create`/`_create`(:62-108, 요청자 = 로그인 멤버, `UNIQUE(session, requester, submission_key)`, 담당표 revision = `config_revision`), 반환 `record_return`(:380-402), 조사 업무 `start_investigation`(:267-324, `source_type='manual'`). 알림 없음. 판단(`triage_runs.py`·`domain/triage.py`)은 사내 요청·담당 범위를 참조하지 않는다. 담당 범위 `responsibility_store.list_entries`·`entry_problem`(:11-23).
- e2e: `tests/e2e/test_triage_cycle.py`(가짜 claude `install_fake_claude` :82-157 — `--json-schema` properties 로 분기, 이슈 본문 표식 `[scenario:]`·`[ask]`·`[confidence:]`, 러너 `start_runner` :282, 같은 프로세스 uvicorn 스레드 + `World`·`drive`(`test_github_cycle.py:435-579`)). 사용자 정의 종류(generic)·사내 요청을 가짜 CLI 로 돈 e2e 는 없다(`test_internal_investigation.py:406` 은 결과를 직접 저장).

## 하지 않는 것

결과 뒤 판단의 자동 시작, 프로젝트(저장소)별 판단 기준·한 화면 등록(phase 23), 받는 사람 수락·검토 생략, 조사 업무 안에서의 판단, 판단이 업무 완료·종료를 정하기, 판정 실패·실행 실패의 판단, 워크스페이스 사이 요청, A2A, 조사 에이전트 자료 공유(앞 단계 산출물 인계로 다룬다), Claude Code 훅, 실제 Claude·GitHub·Jira 호출(모든 step 은 가짜 러너·가짜 도구·`MockTransport` 로 검증, 실연동은 phase 뒤 사용자 지시 — 준비 문서 `docs/product/INTERNAL_REQUEST_LIVE_RUN_1.md`).

## Step 목록

| Step | 이름 | 산출물 |
|---|---|---|
| 0 | next-step-design | ADR-0027, ARCHITECTURE "결과 뒤 판단 — phase 22"(이름·계약·스키마 v24·시작 지점·tick 순서·행동별 적용·문구), GLOSSARY, CURRENT_HANDOFF |
| 1 | next-action-contract | `contracts/v1.py`: `TriageTarget.mode`, `NextAction` 4형, `TriageResult.next_action`, `TriageCandidates.responsibilities`, 능력 `after_result_triage` |
| 2 | connector-next-action | 러너: claim 능력 보고, `mode=next_step` 판단 스키마·결과 조립(읽기 전용 경로 그대로) |
| 3 | schema-v24 | `triage_logs`·`notifications` 재생성, `internal_requests.created_by_triage_id`, v23 fixture, 23→24 |
| 4 | next-step-domain | `domain/triage.py`(또는 새 `domain/next_step.py`): 시작 판정·후보·요청문·제안 검증 순수 함수 |
| 5 | next-step-repo | 결과 뒤 판단 시작·기록·원인별 대체·처리 기록·대상 조회 |
| 6 | worker-after-result | 워커: 규칙 밖 결과·`needs_information`·사용자 정의 종류 결과에서 시작, 대체 경로(사람 요청), 판단 판정 |
| 7 | accept-next-step | `work_actions.accept_next_step`·`dismiss_next_step`, 웹·API 경로, 행동 4형 적용 |
| 8 | request-notify-return | 요청 생성 알림, 반환 → `request_returned` 판단, `next_step_proposed` 알림 |
| 9 | next-step-panel | 업무 패널 "다음 단계 제안" 절·업무 이유·목록 배지·`/requests` 표시 |
| 10 | next-step-verify | e2e(실제 러너 프로세스 + 가짜 claude, RUN-26 장면), v23 사본 마이그레이션, SELFHOST v24(러너 재설치), VERIFICATION_LOG·인계 문서 |

## 실행

```bash
git status --short            # 깨끗해야 한다
git branch --show-current     # service (execute.py 가 feat-22-next-step 을 만든다)
python3 scripts/execute.py 22-next-step --engine claude
```

사용자가 띄워 둔 셀프호스트(compose 프로젝트 `runloom`)·러너(launchd)·`/Users/kje/demo/*` 는 건드리지 않는다. 병합, 셀프호스트 v24 재설치(백업 먼저, 러너 재설치 포함), 실연동은 phase 뒤 사용자 지시로 한다.
