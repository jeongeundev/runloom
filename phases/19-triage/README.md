# Phase 19 — 판단: 담당 없는 업무에 종류·담당·순서 제안, 판단 로그, 자동 시작

작성일: 2026-10-01. 상태: **step 0~10 완료**(2026-10-02, `feat-19-triage` — 검증은 [VERIFICATION_LOG](../../docs/VERIFICATION_LOG.md) "phase 19 판단"). `service` 병합·셀프호스트 v15 재설치(러너 재설치 포함)·판단 실연동은 사용자 지시 대기. **`service`(18-jira 병합 `c5f1d31`, 스키마 v14)에서 실행한다.** 병합은 phase 뒤 사용자 지시로(`--no-ff`). 근거: [재설계 계획](../../docs/product/REDESIGN_PLAN.md) 3·5.4·6·13·15·16절, [ADR-0009](../../docs/adr/)(종류·후속 규칙은 등록 데이터), [ADR-0020](../../docs/adr/0020-work-items-and-stages.md)(업무·단계), [ADR-0023](../../docs/adr/0023-cross-member-delegation.md)(맡기기 정책·소유자 승인), [ADR-0024](../../docs/adr/0024-jira-source.md)(Jira 업무 → 연결 저장소).

## 왜

업무가 들어오면(GitHub·Jira·직접 등록) 지금은 사람이 하나씩 열어 "누가 맡을지·어떤 작업인지·먼저 할 것이 있는지"를 정한다. 판단 단계는 이 일을 러너의 로컬 Claude Code(구독)에게 **제안**하게 하고, 판단 기록이 쌓여 믿을 만해지면 업무 종류별로 **자동 시작**을 켤 수 있게 한다. 업무 내용은 외부 판정 서비스로 보내지 않는다(REDESIGN_PLAN 6절 2).

## 사용자 결정 (2026-10-01)

1. **판단 시점 = 자동 + 상한.** 새로 들어온 담당 없는 업무마다 자동으로 판단을 건다. 단 워크스페이스에서 **한 번에 1건**, 판단 Agent 의 러너가 **비어 있을 때만**(수정·검토 실행이 우선). 업무 패널에 [판단 받기](판단이 없을 때)·[다시 판단](있을 때) 버튼도 둔다.
2. **판단 입력 = 저장소 읽기 + 기준 문서는 중앙.** 판단은 **내장 종류 `triage`** 다(사용자 정의 종류는 결과가 `{outcome, summary}` 고정이고 인계 폴더에서 돌아 저장소를 못 읽는다). 업무의 연결 저장소 기본 브랜치(`origin` 기본 브랜치 — phase 12 기준과 같음)를 **읽기 전용 체크아웃**으로 꺼내 그 저장소를 등록한 러너 Agent 가 판단한다(`code_review` 의 읽기 전용 경로 재사용). **판단 기준 문서는 Runloom 이 저장**하고 설정 화면에서 고치며, 고치면 **기준 버전**(v1, v2…)이 오른다. 러너에 판단 전용 폴더를 따로 등록하지 않는다.
3. **자동 시작 설정을 이번에 넣는다.** 패널 판단 제안 + [제안대로 맡기기], 그리고 업무 종류별 자동 시작(끔 기본). 판단 기록이 기준 건수(20건) 미만이면 켤 수 없다(잠김).

## 계획 기본값 (step 0 이 ADR-0025 로 고정 — 코드 근거가 있으면 바꾸고 이유를 남긴다)

1. **판단 Agent = 저장소 카드의 새 칸**(GitHub 소스 설정 — 기존 `default_fix_agent_id`·`review_agent_id` 옆 `triage_agent_id`). Jira 업무는 프로젝트의 연결 저장소 칸을 쓴다. 직접 등록·n8n 업무는 연결 저장소가 없으므로 자동 판단하지 않는다(버튼도 숨김 — step 0 이 확인: 직접 등록 업무에 저장소 칸이 있으면 그것을 쓴다). 비워 두면 그 저장소는 자동 판단 안 함, 버튼만(버튼은 판단 Agent 가 정해져 있어야 보인다). 후보 = 그 저장소를 등록한 Agent 중 맡기기 정책이 `run` 인 것(`owner_approval` 은 후보에서 뺀다 — 자동 판단마다 승인 요청이 쌓이지 않게). 칸을 바꿀 권한 = 소스 연결 권한(`MANAGE_CONNECTIONS`). 다른 멤버의 Agent 를 고르면 소유자에게 알림 1회.
2. **판단은 그 업무의 한 단계(Task)이지만 업무 상태·맡기기 대상이 아니다.** 판단 단계가 도는 동안 업무 상태는 `새로 들어옴`, 이유 `판단 중`. `work_actions.open_stage`·담당 후보·`work_status` 는 판단 단계를 빼고 본다. 판단 단계인지는 **종류 이름이 아니라 종류의 결과 형태 `output_kind == "triage_result"`** 로 가른다(ADR-0009 — `execution_policy` 가 결과 형태로 정책을 고르는 것과 같은 수준). 판단 단계는 후속 규칙을 만들지 않는다.
3. **결과 형태(계약 v1 확장 `triage_result`)**: `{kind, assignee: {type: member|agent, id} | null, predecessors: [RUN-n…], proceed: ready|needs_check|unsuitable, confidence: 0.0~1.0, reasons: [{criterion, note}…], missing_information: […]}`. `kind`·`assignee`·`predecessors` 는 **요청문에 넣어 준 후보 목록 안에서만** 받는다 — 밖이면 판단 실패(`triage_invalid`). `proceed == needs_check` 이면 `missing_information` 또는 `reasons` 1개 이상. `ready` 인데 `assignee` 없음은 실패.
4. **요청문**: `# 판단: RUN-n 제목` → 기준 문서(현재 버전 본문) → 업무 양식·요청·원본 키 → 후보 목록(종류: 워크스페이스 등록 종류 중 업무를 시작할 수 있는 것, 담당: 멤버 + 그 업무 열린 단계를 맡을 수 있는 Agent(이름·소유자·켜짐·진행 중 업무 수), 선행 후보: 같은 저장소의 열린 업무 키·제목) → 로그 근거(중앙 계산). 저장소 코드는 체크아웃에서 에이전트가 직접 읽는다.
5. **로그 근거(중앙 계산, 순수 함수)**: 같은 종류의 최근 N=20 건 단계 결과 — 1회 통과율(재작업 없이 병합)·재작업률·중앙 소요 시간, 후보 Agent·멤버별 진행 중 업무 수, 같은 저장소 열린 업무. 건수(n)를 같이 적는다. 인과 단정 문구 없음.
6. **판단 로그 표**: 판단마다 한 행 — 업무·단계·실행, 기준 버전, 입력 해시(요청문 원문 해시 — 원문은 저장하지 않음, 실행 기록에 이미 있음), 결과 JSON, `proceed`·`confidence`, 상태(`proposed`/`failed`/`superseded`), 사람 처리(`accepted`/`changed`/`dismissed`/`auto_started` + 누가·언제·실제로 정한 담당·종류). [다시 판단]은 이전 행을 `superseded` 로. "실제 결과"(병합·재작업) 집계는 20-monitor 가 기존 이벤트로 계산한다 — 이번에는 칸을 만들지 않는다.
7. **사람 처리 기록**: 판단 제안이 떠 있는 업무에서 담당이 정해지면(패널 select·[제안대로 맡기기]·[내 세션에서 작업]) 같은 담당·종류면 `accepted`, 다르면 `changed`. [무시]는 `dismissed`(제안 접힘). 기록은 맡기기 트랜잭션과 같이 쓴다.
8. **[제안대로 맡기기]**: 제안 종류가 열린 단계 종류와 다르고 그 단계가 아직 시작 전이면 단계 종류를 바꾼 뒤(요구 능력 다시 계산) 맡긴다. 선행 업무는 `work_item_links(blocks)` 로 남긴다(기존 대기 규칙이 선행이 끝날 때까지 착수를 막는지 step 0 이 확인 — 안 막으면 링크만, 막는 것은 범위 밖). 담당이 멤버면 배정만(16 결정 — 사람은 배정만).
9. **자동 시작**: 종류별 설정 `{enabled, threshold}`(기본 꺼짐, 기준값 0.8). 켤 수 있는 조건 = 그 종류(제안 종류 기준)로 사람이 처리한(`accepted`·`changed`) 판단 20건 이상. 켜져 있으면 판단이 `proceed == ready` · 제안 담당이 **에이전트** · `confidence >= threshold` 일 때 워커가 기존 맡기기 경로(`assign_work` 와 같은 함수 — 소유자 승인·꺼진 러너 대기 그대로)로 맡긴다. 맡긴 사람 = 없음, 표시는 `자동 시작 · 판단 v<기준 버전>`, 판단 로그 `auto_started`. 켜고 끔·기준값 변경은 설정 버전 행으로 남기고 `sessions.config_revision` 도 올린다(phase 9 와 같은 방식).
10. **자동 판단 대상**: 업무 상태 `새로 들어옴`·담당 없음·판단 로그에 그 업무 `revision` 의 행이 없음·원본 열림·연결 저장소에 판단 Agent 가 있음·그 Agent 러너 켜짐이고 실행 중이 아님. tick 마다 워크스페이스에서 최대 1건 시작(이미 도는 판단이 있으면 0건). 오래된 업무부터. 업무 내용이 바뀌어(`revision` 증가) 다시 판단할지는 자동으로 하지 않는다 — [다시 판단]만.
11. **사용량 한도**: 판단 실행이 `usage_limit` 로 실패하면 그 Agent 의 자동 판단을 1시간 쉰다(워커 메모리 — 재시작하면 풀림, 3-limit-wait 는 범위 밖). 그 밖 실패는 판단 로그 `failed` + 패널 "판단 실패 · 이유 · [다시 판단]" — **`내 차례` 를 만들지 않는다**(판단은 제안일 뿐). 같은 업무에서 자동 판단은 실패하면 다시 걸지 않는다 — 단 `usage_limit` 실패는 판단 실패가 아니므로 쉬는 시간이 끝나면 그 업무를 다시 대상에 넣는다.
12. **기준 문서 v1**: REDESIGN_PLAN 6절 5 항목(명확성·검증 가능성·범위·위험·권한·과거 유사 결과·선행 의존)을 판단 지시문으로 쓴 한국어 문서. step 0 이 `docs/product/triage-criteria-v1.md` 로 쓰고, 스키마 v15 가 워크스페이스마다 v1 행으로 시드한다(본문은 코드 상수 — 문서와 같은 글, 테스트가 같음을 확인).
13. **러너 프로토콜 변화**: 러너 `SUPPORTED_BUILTIN_KINDS` 에 `triage`, 결과 형태 `triage_result`, 체크아웃 대상 = 기본 브랜치 끝. 서버는 claim 의 `supported_kinds` 에 `triage` 가 없는 러너에 판단 실행을 주지 않는다(옛 러너는 판단만 못 하고 나머지는 그대로) — 화면에 "러너 업데이트 필요" 표시. 셀프호스트 업그레이드 v15 에 **러너 재설치** 포함.
14. **스키마 v15**: 판단 기준(버전) 표, 판단 로그 표, 자동 시작 설정(버전) 표, `github_sources.triage_agent_id`(FK agents, NULL), 기존 로컬 Agent 에 `code.triage {repository_id}` 능력, 워크스페이스마다 내장 종류 `triage` 행. `tasks` 재생성 금지.

## 조사로 확인한 현재 (2026-10-01, `service` `c5f1d31`)

step 0 은 이 목록을 출발점으로 쓰되 실제 코드로 다시 확인한다. 다르면 코드가 기준이고 다른 점을 ADR 에 적는다.

- 종류: `kinds(session_id, kind, spec_json)`(워크스페이스별, `adapters/db.py`), `KindSpec`(`contracts/v1.py` — `kind, label, capability_code, scope_key, input_kinds, output_kind, outcomes, instructions, builtin`). `output_kind ∈ diagnosis_result|code_change_result|code_review_result|generic_result`, 사용자 정의 종류는 `generic_result` 만. 내장 `bug_fix`(code.fix)·`code_review`(code.review). 결과 형태는 종류마다 pydantic 모델(`CodeReviewResult` 등). 종류 → target·결과 모양은 `domain/execution_policy.py`(내장은 `cycle=True`).
- 후속 규칙 `succession_rules`·`SuccessorRule{from_kind, on_outcomes, to_kind, handoff_kinds, placement: same_work|new_work}`, 처리 `server/worker.py` `_spawn_successors`·`repo.create_followup_once`.
- 업무 `work_items`(v14): `kind`(첫 단계 종류), `assignee_type ∈ {member, agent}`+`assignee_id`(둘 다 NULL 가능), `priority`, `form_json`(goal·steps_to_reproduce·expected_behavior·acceptance_criteria), `handoff_note`, `requested_by_member_id`, `revision`. 단계 = `tasks.work_item_id`. 상태 8개는 `domain/work_status.py` `work_status(facts)` 순수 함수(`새로 들어옴` 이유 `담당 없음`/`지시 전 — …`), 재료 `repo.work_item_facts`, 쓰기 `repo.set_work_status` 한 곳, tick 끝 `_refresh_work_statuses`.
- 맡기기: `server/work_actions.py` `assign_work` → `open_stage`(마감 전 단계 중 최신) → `select_agent(mode="manual")` → `repo.hand_work_to_agent`(한 트랜잭션) → `start_stage`(`mark_start_pending` → `owner_approval.ensure_request` → 실행: cycle 종류 `run_cycle_task`, 나머지 `stage_runs.run_task`). 담당 후보 `agent_candidates`. 맡기기 정책 `agents.delegation_policy ∈ run|owner_approval`(`domain/delegation.py`). 꺼진 러너 = `runner_offline` → 워커 `_start_waiting_stages`.
- 요청문 머리: `server/task_cycle.py` `execution_request_text` → `domain/handoff_context.compose_request`(`# RUN-n 제목`·양식·지시 메모·원본 키).
- 러너: 등록 `connector/cli.py`(`--repo`·`--tool`·`--verify`…), 서버 Agent 생성 `repo.register_local_agent` — 능력 `code.fix`·`code.review` 고정, scope `{repository_id}`. claim 에 `supported_kinds = SUPPORTED_BUILTIN_KINDS = ("bug_fix","code_review")`(`connector/adapter.py:38`, `client.py:121`). 러너 capabilities 는 `verify_only` 하나(`connectors.capabilities_json`).
- 읽기 전용 실행: `connector/local_tool.py` `LocalToolAdapter.run` 분기 — `code_review` 는 `_run_commit_review`(결과 커밋의 깨끗한 임시 체크아웃 + `READONLY_TOOLS`(Read·Glob·Grep) + `--json-schema` structured_output → `CodeReviewResult`, HEAD·파일 바뀌면 `readonly_violation`, 끝나면 지움). 사용자 정의 종류는 `_run_generic`(cwd = 인계 폴더, `{outcome, summary}` 만). `connector/claude.py` `ALLOWED_TOOLS`·`READONLY_TOOLS`, 실패 분류 `usage_limit`.
- 설정 버전: 워크스페이스 하나 `sessions.config_revision`(종류·규칙·소스·매핑 표 바꿀 때 +1, `repo.py` `_bump_config_revision` 류). 실행에 `config_revision`·`folder_commit`·비용·토큰. `task_events`·`work_item_events` 추가 전용. 지표 `domain/metrics.py` `compute_metrics`, `/metrics`.
- 워커 `tick`: `_sync_github` → `_sync_jira` → offline/observe → 판정 셋 → `_advance_cycle` → `_spawn_successors` → `_start_waiting_stages` → 실패 반영 → 전달들 → 알림 → `_refresh_work_statuses`. 새 업무를 따로 훑는 단계는 없다. 동시 실행 = 러너당 1(`connectors.current_execution_id`)·Task 당 활성 1. 워크스페이스 상한·사용량 대기 없음.
- 매핑 표 `field_mappings` — 기본 `('github','kind','*','bug_fix')`·`('jira','kind','*','bug_fix')`. 매핑이 없으면 그 이슈는 가져오지 않는다(판단이 종류를 정하는 것은 "가져온 뒤 다른 종류 제안" 뿐).
- 소스 설정: GitHub 소스 config 에 `default_fix_agent_id`·`review_agent_id`(`domain/github_match.py` `SourceMatch`), Jira 프로젝트 → 연결 저장소(`github_sources`) 하나.
- 패널 `server/templates/_work_panel.html`(`views.work_panel_context`): 머리 → `props`(담당 select·우선순위·PR) → `now` → `timeline` → `left` → `links` → `origin` → `form` → `detail`. 비어 있는 절은 그리지 않는다.

## 하지 않는 것

데이터 등급, 에이전트 허용 명령(REDESIGN_PLAN 9절), 판단 품질·담당자별·버전 비교 화면(20-monitor), 판단 "실제 결과" 집계 칸, 로컬 모델(Ollama 등), 외부 판정 API, A2A(판단 에이전트가 수정 에이전트에게 묻기), 업무 내용 변경 시 자동 재판단, 선행 업무 완료까지 착수 막기(기존 규칙이 없으면), 사용량 한도 대기열(3-limit-wait), Claude Code 훅, 실제 Claude·GitHub·Jira 호출.

## Step 목록

| Step | 이름 | 산출물 |
|---|---|---|
| 0 | triage-design | ADR-0025, ARCHITECTURE "판단 — phase 19"(흐름·스키마 v15 표·시그니처 표·경로 표·문구), CONTRACT `triage_result` 예시, GLOSSARY, `docs/product/triage-criteria-v1.md`, REDESIGN_PLAN 13·15절 |
| 1 | schema-v15 | 판단 기준·판단 로그·자동 시작 설정 표, `github_sources.triage_agent_id`, Agent `code.triage` 능력, 내장 종류 `triage` 시드, v14 → v15 |
| 2 | triage-contract | `contracts/`: `TriageResult`·내장 `triage` KindSpec·`output_kind`·러너 claim `supported_kinds`, `execution_policy`, `register_local_agent` 능력 |
| 3 | triage-domain | `domain/triage.py`: 로그 근거·후보 목록·결과 검증(후보 밖 거부)·자동 시작 자격·입력 해시·요청문 |
| 4 | triage-runner | 러너: `triage` 지원, 기본 브랜치 읽기 전용 체크아웃, json-schema → `TriageResult` |
| 5 | triage-dispatch | 워커: 자동 판단 대상 고르기·판단 단계·실행 만들기(1건·러너 빔·옛 러너 제외), `usage_limit` 물러남, `open_stage`·담당 후보·`work_status` 에서 판단 단계 제외 |
| 6 | triage-judge | 결과 판정 → 판단 로그(`proposed`/`failed`), 단계 마감, 후속 규칙 없음 |
| 7 | triage-panel | 패널 "판단 제안" 절·[제안대로 맡기기]·[무시]·[판단 받기]/[다시 판단], 목록 배지, 사람 처리 기록(`accepted`/`changed`/`dismissed`) |
| 8 | triage-settings | 설정 화면: 판단 기준 편집·버전 이력, 종류별 자동 시작(잠김·기준값·버전), 저장소 카드 판단 Agent 칸 |
| 9 | triage-autostart | 워커 자동 맡기기(기존 맡기기·승인·꺼진 러너 경로 그대로), 판단 로그 `auto_started` |
| 10 | triage-verify | e2e(가짜 러너·가짜 GitHub·가짜 Jira), v14 사본 마이그레이션, SELFHOST 업그레이드 v15(러너 재설치), 인계 문서·실연동 확인 목록 |

## 실행

```bash
git status --short            # 깨끗해야 한다
git branch --show-current     # service (execute.py 가 feat-19-triage 를 만든다)
python3 scripts/execute.py 19-triage --engine claude
```

사용자가 띄워 둔 셀프호스트(compose 프로젝트 `runloom`)·러너(launchd)·`/Users/kje/demo/*` 는 건드리지 않는다. 병합, 셀프호스트 재설치(v15, 백업 먼저, 러너 재설치), 판단 실연동은 phase 뒤 사용자 지시로 한다.
