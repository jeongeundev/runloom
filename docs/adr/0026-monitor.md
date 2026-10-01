# ADR-0026: 모니터링 — 판단 품질·담당자별·설정 변경 기록

결정일: 2026-10-02 (phase 20 step 0). 근거: [재설계 계획](../product/REDESIGN_PLAN.md) 4·6절 4·11·13절, [ADR-0015](0015-measurement-events-and-baseline.md)(측정 원칙·설정 번호), [ADR-0020](0020-work-items-and-stages.md)(업무·단계), [ADR-0021](0021-team-accounts-and-roles.md)(역할·`view_metrics`), [ADR-0025](0025-triage.md)(판단 — 결정 9·13 이 "실제 결과 집계·확신도 보정은 20-monitor" 로 넘김). 기본값은 [phase 20 README](../../phases/20-monitor/README.md) "사용자 결정" 4가지(2026-10-02)와 "계획 기본값" 8가지이며 이 문서로 구현 기준을 고정한다. 적용 범위는 `service` 브랜치. 이름·표·경로·시그니처의 기준은 [ARCHITECTURE](../ARCHITECTURE.md) "모니터링 — phase 20" 이다(README 와 다르면 ARCHITECTURE).

## 맥락

19 에서 판단이 제안·자동 시작까지 생겼다. 그런데 확신도는 모델이 말한 값이고(ADR-0025 결정 13), 자동 시작 기준값(기본 0.8)을 정할 근거가 화면에 없다. 19 는 판단 로그에 "실제 결과" 칸을 두지 않고(ADR-0025 결정 9) 기존 기록으로 계산하라고 넘겼다. 팀으로 쓰려면 누가(사람·에이전트) 얼마나 맡고 얼마나 기다리게 하는지도 보여야 하고, 설정 번호로 나눈 전후 비교는 "그 번호에서 무엇이 바뀌었는지" 기록이 없어 읽기 어렵다.

## 코드로 확인한 사실 (2026-10-02, `feat-20-monitor` = `service` `00a324d` + phase 20 step 파일)

README "조사로 확인한 현재" 는 맞았다. 더 들어가 확인한 것:

1. **설정 번호를 올리는 함수는 README 의 8개 그대로다.** `adapters/repo.py` 에서 `bump_config_revision` 을 부르는 곳: `replace_field_mappings`·`insert_kind`·`delete_kind`·`insert_rule`·`delete_rule`·`save_github_source`·`save_triage_criteria`·`save_triage_autostart`. 이 밖에 `sessions.config_revision` 을 바꾸는 SQL 은 없다(`github_connect`·`github_api` 의 `config_revision + 1` 은 소스 설정 하나의 낙관적 잠금 번호다). `create_session` 은 `_insert_kind_row`·`_insert_rule_row` 를 직접 써서 번호를 올리지 않는다. **`update_jira_project` 는 번호를 올리지 않는다** — 그래서 이번에도 기록하지 않는다(사용자 결정 3 "설정 번호를 올릴 때마다").
2. **번호를 올리는 경로의 호출자는 모두 로그인 멤버가 있는 웹 경로다.** `server/web.py` 종류·규칙 추가·삭제(`MANAGE_RULES`), 판단 기준·자동 시작 저장(`MANAGE_CONNECTIONS`, 이미 `member_id` 를 넘김), `mapping_api` 매핑 표(`MANAGE_RULES`), `github_api._save`(소스 생성·변경·중지, `MANAGE_CONNECTIONS`), `github_connect.sync_installation_sources`·`ensure_token_source`(웹 `GET /operator/github/app/setup`·`POST /operator/github/token`, `MANAGE_CONNECTIONS`). 워커·마이그레이션·시드는 번호를 올리지 않는다 — 기록의 `by_member_id` NULL 은 지금 코드에서는 생기지 않지만 칸은 NULL 을 허용한다(나중 경로·테스트 직접 호출).
3. **`save_github_source` 는 저장할 때마다 번호를 올린다** — 값이 같아도. `save_triage_criteria`·`save_triage_autostart` 는 같은 값이면 번호를 올리지 않는다. 기록은 번호를 따라간다(번호 한 번 = 한 행).
4. **판단 뒤 내용이 바뀐 업무는 셀 수 있다.** `triage_logs.work_revision` 은 판단 시작 때의 `work_items.revision`(`triage_runs.request_triage` 가 `work["revision"]` 을 넘김)이고, `work_items.revision` 은 원본(GitHub·Jira) 제목·본문·양식이 바뀔 때 +1 된다(`repo` 의 이슈·Jira 반영 경로).
5. **업무 `완료` 의 이유는 둘이다** — PR 병합(`work_status`: `task_pull_requests.state = 'merged'` 또는 감지 PR `work_pull_requests.state = 'merged'`)과 모든 단계 완료(`<종류> 완료`). 둘을 가르는 칸은 `work_items.status_reason` 문구뿐이고 문구는 표시용이다. 그래서 **병합 완료 = 업무 `status = '완료'` 이고 그 업무에 병합된 PR 이 하나라도 있음**(`task_pull_requests` 는 `tasks.work_item_id` 로, `work_pull_requests` 는 `work_item_id` 로)으로 센다. 끝난 것 = `status IN ('완료', '종료')`(= `closed_at IS NOT NULL`, 표 CHECK). 원본 이슈를 사람이 Runloom 밖 PR 로 닫은 것(`source_issues.pr_merged_at`)은 업무 상태를 바꾸지 않으므로 이 판정에 들지 않는다.
6. **재작업 = `executions.start_key` 가 `rework:` 로 시작하는 실행**(phase 9 `metrics._REWORK_PREFIX`, `worker`·`views` 도 같은 접두어). 판단 단계 실행은 `start_key` 가 `request_start_key(uuid)` 라 접두어가 없지만 규칙대로 판단 단계(`repo._TRIAGE_STAGE`)를 먼저 뺀다.
7. **"내 차례" 가 된 시각은 가장 최근 `to = '내 차례'` 행이 아니다.** `repo.set_work_status` 는 상태가 같고 이유만 바뀌어도 `status_changed` 한 행(`from = to = '내 차례'`)을 쓴다. 그래서 가장 최근 행은 이유가 바뀐 시각일 수 있다 → **`to = '내 차례'` 이고 `from != '내 차례'` 인 가장 최근 행의 `occurred_at`**(결정 6). 그런 행이 없으면(v9 → v10 마이그레이션이 만든 업무 등) 모름.
8. **"지금 받는 사람" 은 phase 17 이 넓혔다.** `repo.turn_recipients_of` = 열린 소유자 승인 요청이 있으면 그 승인자, 아니면 담당 멤버 → 맡긴 사람 → 활성 관리자 전원. 목록용 묶음 계산(`_member_facts`·`_approvers_by_work`·`_recipients`)이 이미 `list_work_rows` 에 있다 — 담당자 사실 읽기는 이것을 쓴다(업무마다 `turn_recipients_of` 를 부르지 않는다).
9. **응답자 칸은 v11 부터다.** `human_responses.member_id`(v11 ALTER, NULL 허용). v11 이전 응답은 NULL — 담당자별 응답 시간에서 빼고 "응답자 모름" 건수로 따로 센다.
10. **이름이 겹친다.** `domain/triage.py` 에 이미 `TriageFact`(업무 상태 판정용 최신 판단 사실)가 있다. 판단 품질 입력은 **`TriageLogFact`** 로 부른다(README·step 3 의 예시 `TriageFact` 를 바꿈).

## 결정

1. **화면 = `/monitor` 탭 셋**(계획 기본값 1 그대로): `전후`(지금 화면 그대로 + 설정 번호 머리) · `판단` · `담당자별`. 주소 `/monitor?tab=before_after|triage|assignees`(기본 `before_after`, 다른 값 422), 기간 `from`·`to` 공통. 표와 짧은 문장만 — 차트·JS 추가 없음.
2. **새 데이터를 모으지 않는다**(계획 기본값 2). 판단 품질·담당자별은 `triage_logs`·`work_items`·`work_item_events`·`tasks`·`executions`·`human_requests`·`human_responses`·`task_pull_requests`·`work_pull_requests`·`members`·`agents` 로 매번 계산한다. 계산 결과를 저장·캐시하지 않는다. 새 표는 `config_changes` 하나(결정 7). 러너 프로토콜·계약 v1 변화 없음 — 러너 재설치 없이 반영한다.
3. **판단 품질**(사용자 결정 1·2, 계획 기본값 3): 기간 = 판단 로그 `created_at`. 묶음 = 전체 · 제안 종류별(`proposed_kind`, NULL 은 `unknown`) · 기준 버전별(`criteria_version`). 지표는 ARCHITECTURE 지표 정의 표 그대로. **사람 일치율** = `accepted / (accepted + changed)` — 정답률이 아니다(사람이 고른 것과 같았는지일 뿐). **실제 결과** 대상 = `state = 'proposed'` · `proceed = 'ready'` · `handling IN ('accepted', 'auto_started')`. 그 업무가 끝났으면 분모, 병합 완료(사실 5)면 분자 ①, 병합 완료이고 재작업 실행(사실 6)이 0 이면 분자 ②. 끝나지 않은 것은 `incomplete`(진행 중)로 따로. `종료`·병합 없는 `완료` 는 분모에 든다.
4. **확신도 구간**(사용자 결정 2): `[0, 0.5)`·`[0.5, 0.7)`·`[0.7, 0.8)`·`[0.8, 0.9)`·`[0.9, 1.0]`. 대상은 `state = 'proposed'` 행(확신도가 있는 것). 구간마다 제안 n·사람 일치·실제 결과 둘. 0.5 미만을 한 칸으로 두는 이유: 자동 시작 기준값 범위가 0.50~1.00 이다.
5. **기준값 미리보기**(사용자 결정 2, 계획 기본값 8): 연결 "판단" 탭 자동 시작 행마다 저장된 기준값(행 없으면 0.8)으로 서버가 한 줄 — 그 종류(`proposed_kind`)·`confidence >= 기준값`·`state = 'proposed'` 판단 n, 사람 일치 x/(accepted+changed), 병합 y/(실제 결과 대상 중 끝난 것). 기간·기준 버전으로 거르지 않는다(전체 기록). 자동 시작 동작은 바꾸지 않는다.
6. **담당자별**(사용자 결정 4, 계획 기본값 4): 귀속 = 완료·진행은 업무의 **지금** 담당, 응답 시간은 응답한 멤버, 내 차례 대기는 **지금** 받는 사람(사실 8), 실행은 그 실행의 `agent_id`. 과거 담당 이력으로 나누지 않는다. 내 차례가 된 시각은 사실 7 의 규칙. 판단 단계·그 실행은 뺀다(19 그대로 — 판단은 판단 탭에서만). 권한은 `view_metrics`(관리자·멤버 둘 다) 그대로.
7. **`config_changes`(스키마 v16, 사용자 결정 3, 계획 기본값 6)**: 칸은 ARCHITECTURE 스키마 표. 바꾼 것 셋:
   - `area`·`action` 은 영문 코드(`kind`·`rule`·`source`·`mapping`·`triage_criteria`·`triage_autostart` / `add`·`delete`·`change`)로 저장하고 화면이 한글 이름으로 바꾼다 — 다른 표의 CHECK 값과 같은 관례.
   - 인덱스 대신 **`UNIQUE (session_id, revision)`** — "번호 한 번 = 한 행" 을 표가 지키고, 같은 칸 순서라 조회 인덱스도 된다.
   - `subject` 는 1~200자(넘으면 repo 가 199자 + `…`). 값·본문·비밀은 넣지 않는다 — 종류 이름, 규칙 `from → to`, 저장소 이름과 바뀐 칸 **이름**, 매핑 표는 원본 종류별 행 수, 판단 기준은 `v<버전>`, 자동 시작은 `<종류> 켬|끔 · 기준값 0.85`. README·step 2 의 매핑 `<소스 종류> 매핑 n행` 은 `replace_field_mappings` 가 원본 종류 전부를 한 번에 바꾸므로 `github 2행 · jira 1행` 꼴로 바꾼다.
8. **기록은 `bump_config_revision` 한 곳에서**(step 2 가 고를 모양을 고정): `bump_config_revision(conn, session_id, *, area, action, subject, member_id, now) -> int` 이 번호를 올리고 같은 트랜잭션에서 `record_config_change` 를 부른다. 호출자 8곳이 인자를 넘긴다 — 기록을 빠뜨린 번호 올리기가 생길 수 없다. 기록 지점 표와 각 함수의 새 키워드(`member_id`·`now`)는 ARCHITECTURE.
9. **v16 이전 번호는 "기록 없음"**(사용자 결정 3). 마이그레이션은 표만 만들고 과거 변경을 추정해 채우지 않는다.
10. **API 는 더하기만**(계획 기본값 7): `/metrics.json` 에 `triage`·`assignees`·`config_changes` 키, `/metrics.csv` 는 `CSV_COLUMNS` 그대로 새 행(`area` = `triage`·`assignee`). 기존 키·열·행은 바꾸지 않는다. CSV 는 id 만(표시 이름은 JSON 에만).
11. **원칙**(phase 9 그대로, 계획 기본값 5): 시스템이 남긴 기록으로만, 모든 값에 n, 모름은 0 이 아니라 "모름"(`unknown`), 인과 단정 문구 금지("기준 v1 n=12 · 기준 v2 n=8" 처럼 나란히).

## 하지 않는 것

Jira 기준선, v16 이전 설정 변경 복원, 과거 담당 이력으로 나눈 귀속, 알림·요청을 "그때 받는 사람" 으로 다시 계산, 차트·그래프 라이브러리·JS, 지표 저장·캐시, 자동 시작 기준값 자동 조정, 설정 번호를 올리지 않는 저장(Jira 프로젝트 설정·담당자 연결·에이전트 등록)의 기록, `tasks`·`work_items`·`triage_logs` 칸 추가, 데이터 등급·허용 명령·Claude Code 훅, 실제 Claude·GitHub·Jira 호출(모든 step 은 가짜 러너·가짜 도구·`MockTransport` 로 검증).

## 결과

- 판단 탭과 자동 시작 칸 옆 한 줄로 "이 기준값 이상 판단을 사람이 얼마나 그대로 받았고, 맡긴 일이 병합까지 갔는지" 를 n 과 함께 본다. 기준값은 여전히 사람이 정한다.
- 설정 번호 그룹에 무엇을 누가 바꿨는지 붙는다. v16 이전 번호는 비어 있다.
- 담당자별은 지금 담당·지금 받는 사람 기준이라, 담당을 넘긴 업무의 과거 몫은 보이지 않는다(의도한 단순화).
- 스키마 v16 은 표 하나 추가라 셀프호스트 업그레이드는 백업 → `install.sh` 뿐이고 러너 재설치가 필요 없다.
