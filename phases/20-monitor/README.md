# Phase 20 — 모니터링: 판단 품질·담당자별·설정 버전 비교

작성일: 2026-10-02. 상태: 완료(2026-10-02, step 0~9, `feat-20-monitor` — `service` 병합·셀프호스트 v16 재설치는 사용자 지시 뒤). **`service`(19-triage 병합 `df82a46`, 스키마 v15)에서 실행한다.** 병합은 phase 뒤 사용자 지시로(`--no-ff`). 근거: [재설계 계획](../../docs/product/REDESIGN_PLAN.md) 4·6절 4·11·13절, [ADR-0015](../../docs/adr/0015-measurement-events-and-baseline.md)(측정 원칙), [ADR-0021](../../docs/adr/0021-team-accounts-and-roles.md)(역할), [ADR-0025](../../docs/adr/0025-triage.md)(판단 — 결정 9·13 이 "실제 결과 집계·확신도 보정은 20-monitor" 로 넘김).

## 왜

19 에서 판단이 제안·자동 시작까지 생겼지만 "판단을 믿어도 되나"를 볼 곳이 없다. 확신도는 모델이 말한 값이라 자동 시작 기준값(기본 0.8)을 정할 근거가 필요하다. 팀 단위로 쓰려면 누가(사람·에이전트) 얼마나 맡고 얼마나 기다리게 하는지도 보여야 한다. 설정(규칙·매핑·판단 기준·자동 시작)을 바꾼 전후를 비교하려면 무엇을 바꿨는지 기록이 있어야 한다.

## 사용자 결정 (2026-10-02)

1. **판단 "실제 결과" = 병합·재작업 둘 다.** 판단이 `맡겨도 됨` 이라 한 업무 중 끝난 것에서 ① PR 병합으로 완료된 비율 ② 재작업 없이 병합 완료된 비율을 따로 보인다. 아직 안 끝난 것은 진행 중으로 따로 센다.
2. **확신도 구간표 = 모니터링 판단 탭 + 자동 시작 칸 옆.** 판단 탭에 구간표, 연결 "판단" 탭의 종류별 자동 시작 행에 "지금 기준값 이상 판단 n건 — 사람 일치 x · 병합 y" 한 줄.
3. **설정 변경 기록 표를 더한다(스키마 v16).** 설정 번호(`config_revision`)를 올릴 때마다 무엇을(영역·대상)·누가·언제 바꿨는지 추가 전용 행. 모니터링의 설정 번호 그룹 머리에 보인다. v16 이전 번호는 "기록 없음".
4. **담당자별 화면은 모든 멤버가 본다.** 권한은 지금 `view_metrics`(관리자·멤버) 그대로.

## 계획 기본값 (step 0 이 ADR-0026 으로 고정 — 코드 근거가 있으면 바꾸고 이유를 남긴다)

1. **화면**: `/monitor` 탭 3개 — `전후`(지금 화면 그대로) · `판단` · `담당자별`. 주소 `/monitor?tab=before_after|triage|assignees`, 기간 `from`·`to` 공통. 표와 짧은 문장만(차트 라이브러리 없음).
2. **새 데이터를 모으지 않는다.** 판단 품질·담당자별은 기존 기록(`triage_logs`·`work_items`·`work_item_events`·`executions`·`human_requests`·`human_responses`·`work_pull_requests`/`task_pull_requests`)으로 매번 계산한다. 새 표는 설정 변경 기록 하나. 러너 프로토콜·계약 v1 변화 없음(러너 재설치 불필요).
3. **판단 품질**(종류별·기준 버전별, 기간 = 판단 시작 시각):
   - 제안 n(`state='proposed'`), 실패 n·실패 코드 분포, 대체됨 n
   - 사람 처리 건수(`accepted`·`changed`·`dismissed`·`auto_started`·미처리), **사람 일치율** = accepted / (accepted + changed)
   - 진행 여부 분포(`맡겨도 됨`·`확인 필요`·`부적합`)
   - **실제 결과**(대상 = `proceed='ready'` 이고 `accepted`·`auto_started`): 끝난 것 중 병합 완료, 끝난 것 중 재작업 없이 병합 완료, 진행 중 n
   - 확신도 구간(`[0,0.5)`·`[0.5,0.7)`·`[0.7,0.8)`·`[0.8,0.9)`·`[0.9,1.0]`)마다 제안 n·사람 일치·실제 결과
   - 판단 시간(판단 실행 시작 → 끝)·비용(`total_cost_usd` 는 CLI 계산값), 판단 뒤 내용이 바뀐 업무 수(`work_revision < 지금 revision`)
4. **담당자별**(귀속: 완료·진행 = 업무의 지금 담당, 응답 시간 = 응답한 멤버, 내 차례 대기 = 지금 받는 사람 `turn_recipients_of`):
   - 멤버: 완료 n(기간 = 끝난 시각)·진행 중 n·내 차례 대기 n·가장 오래 기다린 시간·응답 시간 중앙값
   - 에이전트: 완료·진행 n, 실행 n·실패율·실패 코드, 1회 통과율, 재작업 n, 실행 시간, 비용
   - 담당 없음: 끝나지 않은 업무 n
   - 판단 단계 실행은 담당자 지표·기존 지표에서 뺀다(19 그대로) — 판단 탭에서만
5. **원칙(phase 9 그대로)**: 시스템이 남긴 기록으로만, 모든 값에 n, 모름은 0 이 아니라 "모름", 인과 단정 문구 금지.
6. **`config_changes`**(v16): `id`·`session_id`·`revision`(바뀐 뒤 번호)·`area`(종류·후속 규칙·소스·매핑 표·판단 기준·자동 시작)·`action`(추가·삭제·변경)·`subject`(표시용 이름 — 값·본문·비밀 없음)·`by_member_id`(NULL 허용 — 마이그레이션·시드·워커)·`occurred_at`. 추가 전용. 기록 지점 = `bump_config_revision` 을 부르는 repo 함수 전부(같은 트랜잭션, 번호 한 번 = 한 행). 설정 번호를 올리지 않는 저장(예: Jira 프로젝트 설정)은 기록하지 않는다. (step 0: ADR-0026 결정 7 로 바꿈)
7. **API**: `/metrics.json` 에 `triage`·`assignees`·`config_changes` 키 추가, `/metrics.csv` 는 열 그대로 새 행만. 기존 키·열은 바꾸지 않는다.
8. **자동 시작 칸 미리보기**: 저장된 기준값 기준 서버 렌더 한 줄(JS 없음). 자동 시작 동작은 바꾸지 않는다.

## 조사로 확인한 현재 (2026-10-02, `service` `00a324d`)

- 지표: `domain/metrics.py`(`TaskFact`·`ExecutionFact`·`Stat`·`Ratio`·`compute_metrics`·`summarize_baseline`), `server/metrics_api.py`(`_report`·`_baselines`·`/metrics.json`·`/metrics.csv`·기준선 가져오기), `views.metrics_context`, `templates/metrics.html`(86줄), `web.metrics_page` = `GET /monitor`(`/metrics` 는 303). `repo.list_metric_facts` 는 판단 단계를 뺀다.
- 판단 로그 `triage_logs`: `proposed_kind`·`criteria_version`·`trigger`·`state`(running/proposed/failed/superseded)·`proceed`·`confidence`·`failed_code`·`handling`(accepted/changed/dismissed/auto_started)·`handled_at`·`final_assignee_*`·`final_kind`·`work_revision`·`execution_id`·`created_at`·`finished_at`. 기준 `triage_criteria(version)`, 자동 시작 `triage_autostart(kind, version, enabled, threshold)`, `repo.triage_handled_counts`, 화면 `_connect_triage.html`(자동 시작 행 `data-autostart`).
- 설정 번호: `repo.bump_config_revision` 을 부르는 곳 — `replace_field_mappings`·`insert_kind`·`delete_kind`·`insert_rule`·`delete_rule`·`save_github_source`·`save_triage_criteria`·`save_triage_autostart`. `update_jira_project` 는 부르지 않는다.
- 업무: `work_items`(assignee_type/id·status·closed_at·revision·requested_by_member_id), `work_item_events`(status_changed·assigned `{from,to,by}`·…), 상태 8개(`완료` = PR 병합 또는 모든 단계 완료, `종료`). 받는 사람 `repo.turn_recipients_of`(담당 멤버 → 맡긴 사람 → 관리자 전원). 응답자 `human_responses.member_id`. 재작업 = `executions.start_key` `rework:`.
- 권한 `view_metrics` = 관리자·멤버 둘 다(`domain/team.py`). 스키마 v15(`adapters/db.py`).

## 하지 않는 것

Jira 기준선, v16 이전 설정 변경 복원, 과거 담당 이력으로 나눈 귀속, 알림·요청을 "그때 받는 사람" 으로 다시 계산, 차트·그래프 라이브러리, 지표 저장·캐시, 자동 시작 기준값 자동 조정, 데이터 등급·허용 명령·Claude Code 훅, 실제 Claude·GitHub·Jira 호출.

## Step 목록

| Step | 이름 | 산출물 |
|---|---|---|
| 0 | monitor-design | ADR-0026, ARCHITECTURE "모니터링 — phase 20"(지표 정의·구간·스키마 v16·기록 지점·시그니처·경로·문구), GLOSSARY, REDESIGN_PLAN 13절, CURRENT_HANDOFF |
| 1 | schema-v16 | `config_changes` 표, v15 → v16, `record_config_change`·`list_config_changes`, v15 fixture |
| 2 | config-change-log | 설정 번호를 올리는 8곳이 같은 트랜잭션에서 변경 기록, 웹 경로가 멤버 id 를 넘김 |
| 3 | triage-metrics | `domain/triage_metrics.py` — 판단 품질·확신도 구간·기준값 미리보기 |
| 4 | assignee-metrics | `domain/assignee_metrics.py` — 멤버·에이전트·담당 없음 |
| 5 | metric-facts | repo 읽기: 판단 사실·결과 사실·담당자 사실·설정 변경 목록 |
| 6 | metrics-export | `/metrics.json` 새 키, `/metrics.csv` 새 행(열 그대로) |
| 7 | monitor-tabs | `/monitor` 탭 3개(전후 + 설정 번호 머리·판단·담당자별) |
| 8 | autostart-preview | 연결 "판단" 탭 자동 시작 행 미리보기 한 줄 |
| 9 | monitor-verify | e2e(가짜 러너·가짜 GitHub), v15 사본 마이그레이션, SELFHOST 업그레이드 v16(러너 재설치 없음), VERIFICATION_LOG·인계 문서 |

## 실행

```bash
git status --short            # 깨끗해야 한다
git branch --show-current     # service (execute.py 가 feat-20-monitor 를 만든다)
python3 scripts/execute.py 20-monitor --engine claude
```

사용자가 띄워 둔 셀프호스트(compose 프로젝트 `runloom`)·러너(launchd)·`/Users/kje/demo/*` 는 건드리지 않는다. 병합, 셀프호스트 v16 재설치(백업 먼저), 실연동은 phase 뒤 사용자 지시로 한다.
