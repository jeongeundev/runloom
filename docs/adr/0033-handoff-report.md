# ADR-0033: 인계 리포트 — 그 단계 Agent 가 결과 뒤 별도 실행으로 근거 붙은 리포트를 만들고, 받는 사람이 이해·판단한 뒤 이어받는다

결정일: 2026-10-22 (phase 25 step 0). 근거: [ADR-0031](0031-handoff-understanding-and-clients.md) 결정 2(인계 리포트)·6(순서 ②)·미결 "리포트를 누가 만드는가"·"리포트 형식과 보관", [ADR-0032](0032-human-api-and-member-tokens.md)(사람용 API·멤버 토큰), [ADR-0025](0025-triage.md)·[ADR-0027](0027-next-step-triage.md)(판단 — 중앙이 만든 요청문·읽기 전용 체크아웃 선례), [ADR-0018](0018-real-repo-cycle.md)(실제 저장소 순환), [ADR-0009](0009-registered-kinds-and-succession-rules.md)(종류·후속 규칙은 등록 데이터). 결정 원본은 [phase 25 README](../../phases/25-handoff-report/README.md) "결정" D1~D41 이고 이 문서가 구현 기준으로 고정한다. 이름·표·경로·시그니처·문구의 기준은 [ARCHITECTURE](../ARCHITECTURE.md) "인계 리포트 — phase 25" 다(README 와 다르면 ARCHITECTURE). 예시는 [CONTRACT](../CONTRACT.md) 19절.

## 배경

- ADR-0031 결정 2 는 단계가 끝나 다른 사람에게 넘어갈 때 사람용(무엇이 왜 바뀌었는지·결정과 근거·정하지 않고 남긴 것·받는 사람이 결정·확인할 것)과 에이전트용 인계 자료를 함께 내고, 주장마다 근거를 붙이라고 정했다. 순서 ② 다.
- 지금 인계 자료는 에이전트용 `handoff_bundle`(`assemble_handoff` — 선행 결과·규칙 `handoff_kinds` 산출물·근거 원문)과 결과 봉투의 `summary` 한 줄뿐이다. 사람이 읽고 판단할 형태가 없다.
- ADR-0031 미결 둘: **리포트를 누가 만드는가**(보내는 쪽 러너 / 받는 쪽 에이전트), **리포트 형식과 보관**(HTML 생성 방식·다이어그램·산출물 kind 이름·결정 기록 위치). 이 ADR 이 둘을 정한다.

## 코드 조사로 README 와 달라진 사실 (2026-10-22, `feat-25-handoff-report` = `main` `2c188b7` + phase 파일)

[사용자] 결정과 충돌하는 코드 사실은 없다. 아래는 결정을 구현 이름으로 옮기며 확인한 것이고, 바꾼 것은 [근거]·[조정값] 의 구현 방식뿐이다.

1. **"원인 실행" 은 `executions.predecessor_execution_id` 만으로 찾을 수 없다.** 그 칸이 채워지는 경로는 규칙 후속(`_spawn_successors`)·검토 착수(`_start_review`)·사람 [시작]의 선행 묶음(`views.predecessor_handoff`)·검증만 다시(`_start_verify_only`)·사람 [수정 요청](`stage_runs.request_changes` — 이전 시도의 값을 이어받음)이다. 검토 뒤 재작업(`_start_fix`, `start_key = rework:<검토 실행>`)·결과 뒤 판단 [제안대로] 의 새 단계(`apply_next_stage` — 선행 없음)·접수 판단 뒤 첫 단계는 비어 있다. 그래서 원인 규칙을 넷으로 정한다(ARCHITECTURE "원인 실행 규칙") — D9 의 "모든 착수 경로가 claim 한 곳을 지난다" 를 지키는 방법이다.
2. **사내 요청 조사 결과는 별도 판정 지점이 없다.** 조사 단계는 사용자 정의 종류 실행이라 `_check_generic_results` 가 판정한다. 판정 지점은 넷이다 — `_check_code_results`·`_check_review_results`·`_check_generic_results` 의 `record_verdict(passed)` 와 `_triage_verdict` 의 `record_triage_proposed`(접수 판단만, D4).
3. **`artifacts` 를 참조하는 외래키가 셋 있다**(`internal_request_judgments`·`internal_request_resumptions`·`internal_request_investigations.returned_artifact_id`). `_migrate_4_to_5` 주석의 "참조하는 FK 없다" 는 v5 당시 사실이다. v27 재생성(D38)은 `init_schema` 가 이미 외래키를 끈 채 돌리므로 같은 방식(새 표 → 복사 → 지우기 → 이름 바꾸기)이 되고, 끝의 `foreign_key_check` 가 확인한다.
4. **업무 이벤트 `type` CHECK 는 `handoff_note` 까지다.** 결정 답 이력 이벤트 `report_decision` 을 넣으려면 `work_item_events` 를 v13 방식으로 한 번 더 재생성한다.
5. **D39 를 지키려면 설정 키 목록을 둘로 나눠야 한다.** `tests/workflow/server/test_settings.py` 는 `load_settings` 가 읽는 키 = `ENV_KEYS` 를, `tests/test_selfhost_files.py` 는 `ENV_KEYS` = `.env.example` 키를 단정한다. 대기 시간 키를 `.env.example`·SELFHOST 에 넣지 않으려고 `settings.HIDDEN_ENV_KEYS` 를 두고 첫 단정을 `ENV_KEYS ∪ HIDDEN_ENV_KEYS` 로 고친다(step 5).
6. **러너는 `domain/` 을 import 하지 않는다**(지금 `connector/` 의 import 는 `connector.*`·`contracts.v1` 뿐). 판단과 같이 **요청문은 중앙이 만들고**(`ReportJob.request`) 러너는 로컬 근거(커밋 목록·변경 통계)와 고정 꼬리만 붙인다. 산출물 발췌(검증 로그·테스트 로그·diff 앞부분)도 중앙이 요청문에 넣으므로 러너용 새 내려받기 경로가 없다.
7. **`_OmitUnknownMeasure` 로 null 칸을 직렬화에서 뺄 수 있다**(`verify_only_commit`·`TriageTarget.mode` 선례). 받는 러너 전달 칸(`ExecutionRequest.handoff_report`)이 null 이면 옛 러너 응답 바이트가 그대로다(D35).
8. **계약 예시 대조 테스트의 키 서명이 겹친다.** 최상위 `source_execution_id` 는 `HandoffBundle`, `connector_id` 는 `ClaimRequest`, `code`+`message` 는 `ErrorBody` 로 잡힌다 — 새 계약 모델은 이 키를 최상위에 두지 않는다(`ReportJob` 에 원인 실행 id 없음, 실패 본문은 `failure_code`).
9. **러너는 실행이 없을 때만 claim 하고 한 번에 하나만 돈다**(`Runner.tick` — `state.active_execution` 이 없을 때 `_claim_and_start`). 리포트 작업도 그 tick 안에서 claim → 실행 → 제출을 끝낸다. 러너가 리포트 작업 중 재시작하면 그 작업은 다시 주지 않고(`leased` 는 한 번) 대기 시간 뒤 `timed_out` 이다.
10. **Mermaid 최신은 12.2.0 이다**(2026-10-22 jsDelivr). D40 [사용자] 대로 11.x 의 마지막 11.17.2 를 고른다.
11. **"자세히"(`details.detail`)는 내부 ID 접힘 전용이다**(GLOSSARY). D6 의 이전 리포트 접힘은 같은 글자 "자세히" 를 쓰되 클래스를 `report-history` 로 둔다(노출 단정이 지우는 요소와 겹치지 않게).
12. **CONTRACT 16.2 는 `capabilities` 의 알려진 값을 `verify_only` 하나로 적고 있다** — 코드는 이미 `after_result_triage` 까지 둘이다. step 1 이 `handoff_report` 를 더하며 이 줄을 함께 고친다.

## 결정

### 만드는 쪽과 대상 (D1~D8)

1. **원본 리포트**(`job_type = original`)는 판정을 통과한 실행 결과마다 **그 실행의 Agent 가 같은 러너에서 별도 실행으로** 만든다(D1~D3). Runloom 의 실행(`Execution`)이 아니라 리포트 작업이다 — Task·종류·업무 상태·보드·측정·모니터링을 건드리지 않는다(D17).
2. **대상**은 판정 `passed` 인 결과 전부다 — 코드 수정·커밋 검토·사용자 정의 종류(사내 요청 조사 포함)·접수 판단. **결과 뒤 판단 결과는 제외**(원인 단계 리포트와 중복, D4). 실행 실패·판정 `failed` 는 제외. 직접 작업은 실행이 없어 리포트도 없다(D5).
3. 리포트 1개 = 원인 실행 1개. 재작업 결과는 새 리포트이고 화면은 최신을, 이전은 접힘으로 보인다(D6).
4. **보낸 사람** = 원인 실행 Agent 의 주인 멤버(러너 소유자). **받는 사람** = 다음 단계 담당 멤버(없으면 업무 담당) — 담당이 에이전트면 그 주인 멤버, 주인 없는 공유 에이전트면 업무 담당 멤버, 그도 없으면 관리자만(D7). 판정 함수는 순수 규칙(`domain/handoff_report.py`)이다.
5. **맞춤 풀이**(`job_type = tailored`)는 받는 사람이 [내게 맞춰 풀기] 를 누를 때만, 자기 Agent 중 하나(기본 다음 단계 Agent)가 원본 리포트만 근거로(원본 주장 id 인용) 만든다. 맞춤 풀이를 할 수 없는 Agent(API 연결형·러너 능력 미선언)는 목록에서 빠지고, 남는 게 없으면 버튼이 없다(API 는 422 + 사유). 팀에 공개, 만든 사람 표시, (원본 리포트, 요청 멤버)당 최신 하나. 원본을 다시 만들면 옛 풀이에 "옛 리포트 기준"(D8). 원본과 같은 표·같은 claim·같은 제출 경로로 받는 사람 Agent 의 러너에 배정한다(D36).

### 동작 (D9~D16)

6. **착수 대기**: 다음 단계 실행은 claim 순간 원인 실행의 최신 원본 리포트가 작성 중(`queued`·`leased`)이면 가져갈 수 없다(최대 30분, `WORKFLOW_HANDOFF_REPORT_WAIT_SECONDS`=1800). 자동·재작업·판단 제안 수락·사람 [시작] 모두 `repo.claim_execution` 한 곳을 지난다. 실패·시간 초과·리포트 불가면 리포트 없이 착수(D9). 대기는 받는 러너의 능력과 무관하다 — D9 은 조건이 없다. 받는 러너에게 리포트를 **붙이는 것**만 능력 선언 러너에 한한다(D23).
7. **리포트 불가**: 원인 Agent 삭제·API 연결형·러너 연결 없음·러너 능력 미선언이면 대기 없이 `unavailable`(사유)(D10). 러너가 능력 선언을 그만두면 그 러너에 배정된 대기 작업도 `unavailable` 이 된다.
8. 같은 러너에서 리포트 작업이 그 러너의 다음 실행보다 먼저다(D11). 판단 단계 실행(접수·결과 뒤)은 리포트를 기다리지 않는다(D15).
9. **실패**: 자동 재시도 없음. `리포트 없음(사유)` + [리포트 다시 만들기](보낸 사람·받는 사람·관리자) — 같은 Agent 가 새 행으로 다시 쓴다. 이미 착수한 다음 단계에는 사람용으로만 남는다(D12).
10. **결정 항목**: 리포트의 "받는 사람이 결정·확인할 것" 항목마다 [확인함] / [다르게 정함 + 한 줄(1~500자)]. 답할 수 있는 사람 = 받는 사람·관리자. 착수를 막지 않는다(D13). 마지막 답이 유효하고 이력은 업무 이벤트 `report_decision` 으로 남는다.
11. **전달**: 리포트·결정 답은 인계 묶음에 굳히지 않고 claim 순간의 최신을 붙인다. 자동 즉시 착수면 그 뒤의 답은 사람 기록으로만 남고, 사람 [시작]·재작업 때 반영된다(D14).
12. 원인 결과가 재작업으로 대체된 뒤 도착한 리포트도 `leased` 면 받아 그 실행에 붙여 보관한다. `timed_out` 뒤에 온 제출은 409 로 받지 않는다(D16).

### 기술 (D17~D29, D35~D41)

13. **실행 모델**: 전용 표 `handoff_reports`(원인 실행·작업 종류·상태·임대·결과 산출물·사유·입력 자료)와 `handoff_report_decisions`(결정 답). 스키마 v27(D17·D27). 기존 결과에 리포트를 채워 넣지 않는다(D41).
14. **배정 프로토콜**: 같은 `POST /api/v1/connector/claim` 이 능력 `handoff_report` 를 선언한 러너에게 `ExecutionRequest` 또는 리포트 작업(`ReportJob`) 중 하나를 준다. 러너는 응답에 `job_type` 키가 있으면 리포트 작업으로 읽는다(`ExecutionRequest` 에는 그 칸이 없다). 선언하지 않은 러너의 응답은 지금과 같은 바이트다(D35). 제출은 `POST /api/v1/report-jobs/{report_id}/result`·`/failure`(연결 토큰).
15. **실행 환경**: 원본은 결과 커밋(없으면 기준 커밋, 사용자 정의 종류는 실행 시작 때 등록 폴더 HEAD `folder_commit`)의 깨끗한 임시 체크아웃에서 읽기 전용으로 돈다 — 판단과 같은 `_in_clean_checkout` + `launch_readonly` + HEAD·파일 불변 확인. 원인 단계 실행 승인을 그대로 쓰고 능력 검사·소유자 재승인은 없다(D18). 맞춤 풀이는 체크아웃 없이 원본 리포트 JSON 만 입력이다.
16. **형식**: 모델은 절별 주장 + 근거 ID 의 JSON(`HandoffReport`)을 내고, 서버가 근거를 확인한 뒤 Jinja 템플릿으로 HTML 을 그린다. 에이전트용도 같은 JSON 이다(D19). 판정: 계약 위반·크기 초과 = `failed`, 확인 안 되는 근거 ID = 그 주장에 "근거 확인 안 됨" 을 붙이고 `done`(D20).
17. **근거 종류**(D21): 이 실행·입력 묶음의 산출물(`artifact:<id>`), 결과·기준 커밋과 PR 커밋(`commit:<sha>`), 업무에 연결된 PR(`pr:<번호>` — 있을 때만, `bug_fix → code_review` 시점엔 보통 없다), 업무 원문(`source:work` — 이슈·Jira·사내 요청), 검증 기록(`verification`). 맞춤 풀이는 원본 주장(`claim:<id>`)만.
18. **PR 근거**: 중앙이 리포트 행을 만들 때 GitHub App(또는 소스의 자격)으로 업무에 연결된 PR 의 본문·커밋·리뷰 코멘트를 읽는다(GET 만). 러너는 로컬 git 만 본다. 읽기 실패면 PR 근거 없이 "PR 읽기 실패 — 사유". Jira 는 이미 가진 원문만(D22). GitHub 토큰은 중앙 프로세스에만 있고 요청문·러너·DB 에 들어가지 않는다(D28).
19. **받는 러너 호환**: 리포트·결정 답은 `handoff_bundle` 계약(`HandoffBundle`, `extra=forbid`)을 바꾸지 않고, `ExecutionRequest` 의 선택 칸 `handoff_report`(null 이면 직렬화에서 빠짐)로 리포트 산출물 id·해시·결정 답을 알리고 받는 러너가 handoff dir 에 `report.json`·`decisions.json` 을 쓴다. 능력을 선언한 받는 러너에게만(D23).
20. **ADR-0031 결정 2 의 구체화(D37)**: 결정 2 는 "에이전트용은 기존 `handoff_bundle` 을 확장하며 새 묶음을 따로 만들지 않는다" 였다. 이 ADR 은 **묶음 계약은 그대로 두고 같은 인계 디렉터리(handoff dir)에 별도 파일 두 개를 함께 놓는 것**으로 구체화한다. 새 묶음 개념은 없다. 이유: 옛 받는 러너의 `HandoffBundle` 은 strict(`extra=forbid`)라 계약 칸을 늘리면 옛 러너가 inputs 를 내려받지 못한다. 묶음은 착수 때 굳고 리포트·답은 그 뒤에도 바뀐다(D14).
21. **다이어그램**: Mermaid 텍스트(`diagram`). 고정 버전 11.17.2 의 `mermaid.min.js` 를 sha256 확인 뒤 `server/static/vendor/` 에 라이선스와 함께 커밋하고(CDN 없음), `securityLevel: 'strict'`, 해석 실패면 텍스트 그대로(D24·D40). 모든 모델 텍스트는 Jinja 자동 이스케이프(D25).
22. **저장**: 리포트 JSON 은 산출물 kind `handoff_report` 로 **원인 실행에 매단다**(`artifacts.execution_id` NOT NULL·FK). 맞춤 풀이도 같은 kind·같은 원인 실행이고 `handoff_reports` 행(`job_type=tailored`)이 가리킨다. HTML 은 조회 때 그린다. kind 는 종류·규칙 폼 선택지에서 숨긴다. 결정 답은 DB 행이다(D26). v27 은 `ARTIFACT_KINDS` CHECK 를 넓히려고 `artifacts` 를 다시 만들고, 이때 v15 이후 빠져 있던 `triage_result` 도 들어가 v14 이전 DB 가 판단 결과 산출물을 거절하던 결함이 함께 고쳐진다(D38).
23. **API**: 리포트 목록·조회(JSON·HTML)·결정 답·맞춤 풀이 요청·다시 만들기를 `/api/v1` 에 연다. 웹 폼과 같은 행동 함수(`server/handoff_reports.py`)를 쓴다. 읽기 전용 토큰은 조회만(D29, ADR-0032).
24. **대기 시간 설정**: 환경변수 `WORKFLOW_HANDOFF_REPORT_WAIT_SECONDS`(기본 1800). `.env.example`·compose·SELFHOST 에 넣지 않고 ARCHITECTURE 에만 적는다(D39).

### 계약과 화면 (D30~D34)

25. 리포트 JSON 의 절: 바뀐 것(무엇·왜) `changes` · 결정과 근거 `decisions` · 정하지 않고 남긴 것 `open_questions` · 받는 사람이 결정·확인할 것 `to_confirm` · 지킬 제약 `constraints` · 남은 일 `remaining_work`. 주장마다 `evidence` 목록, '왜' 기록이 없으면 `why: null`(화면 "기록 없음")(D30). 크기 상한은 ARCHITECTURE 값(D31).
26. 업무 패널 단계 줄: `리포트 작성 중` / [리포트 보기] / `리포트 없음(사유)`(D32). 새 알림 종류는 없다(D33). 리포트 켜기/끄기 설정은 없다(D34).

## 하지 않는 것

직접 작업(에이전트 실행 없는 업무)의 리포트(③ CLI), CLI·IDE 앱(ADR-0031 ③④), 새 알림 종류, 리포트 켜기/끄기 설정, 리포트 자동 재시도, 업그레이드 전 결과의 리포트 백필, 결과 뒤 판단 결과의 리포트, 리포트 작업 원시 로그(stdout·stderr) 보존, `HandoffBundle` 계약 칸 추가, 받는 러너가 아닌 곳(웹·API)으로의 리포트 파일 내려받기 경로, 리포트 작업의 사용량·비용 측정, 셀프호스트 업그레이드 적용(phase 뒤 사용자 지시).

## 결과

- **셀프호스트 업그레이드 영향(v27)**: claim 응답·제출 경로가 늘어 **보내는 쪽·받는 쪽 러너 모두 재설치**(`install-runner.sh`)해야 리포트를 만들고 받는다. 옛 러너가 실행한 결과는 `리포트 없음(러너가 리포트를 지원하지 않음 …)` 이고 다음 단계는 대기 없이 착수한다. 업그레이드 전에 끝난 결과에는 리포트가 없다. 절차는 [SELFHOST](../SELFHOST.md) "업그레이드" v27(step 10).
- 받는 사람은 업무 패널에서 단계마다 리포트를 열어 근거를 따라가고, 결정 항목에 답하고, 원하면 자기 에이전트에게 맞춤 풀이를 시킨다. 다음 단계 에이전트는 착수할 때 그 순간의 최신 리포트(`report.json`)와 결정 답(`decisions.json`)을 handoff dir 에서 먼저 읽는다.
- 리포트 작업이 원인 러너의 다음 실행 앞에 끼므로 같은 러너의 다음 실행이 리포트 한 번만큼 늦어진다. 다른 러너의 다음 단계는 최대 30분 기다린다.
- 판정 트랜잭션과 리포트 행 만들기는 따로다(판정 직후 같은 바퀴). 그 사이 워커가 죽으면 그 결과는 리포트 없이 남는다(업그레이드 전 결과와 같다) — 같은 판정을 다시 보지 않는다.
- ③ CLI·④ 앱은 리포트 JSON API(`GET /api/v1/reports/{report_id}`)와 HTML(`…/html`) 위에 선다.
