# Step 0: report-design — 인계 리포트 설계 고정 (문서만)

## 읽어야 할 파일

- AGENTS.md
- phases/25-handoff-report/README.md — **"결정" 절 D1~D41 이 이 phase 의 기준이다.** [사용자] 표기 결정은 바꾸지 않는다.
- docs/adr/0031-handoff-understanding-and-clients.md(결정 2·6, 미결), docs/adr/0032-human-api-and-member-tokens.md, docs/adr/0027-next-step-triage.md·0025-triage.md(판단 실행 선례), docs/adr/0018-real-repo-cycle.md, docs/adr/0009-registered-kinds-and-succession-rules.md
- docs/ARCHITECTURE.md — "화면 계층과 인계 리포트 — ADR-0031 (설계 전)", "사람용 API — phase 24", "판단 — phase 19", "결과 뒤 판단 — phase 22", 러너 claim·handoff dir 를 설명하는 절
- docs/CONTRACT.md 4절(산출물 kind)·16.2(러너 capabilities)·17절(판단)·18절(사람용 API), docs/GLOSSARY.md(`handoff bundle`·`handoff dir`·`assemble_handoff`·`ExecutionAdapter`), docs/SELFHOST.md "업그레이드"
- 코드: `src/workflow/contracts/v1.py`(`ARTIFACT_KINDS`·`HandoffBundle`·`InputRef`·`ExecutionRequest`·`ClaimRequest`·`RunnerCapability`·`GenericResult`·`CodeChangeResult`·`CodeReviewResult`), `src/workflow/adapters/db.py`(`SCHEMA_VERSION`·마이그레이션 형식), `src/workflow/adapters/repo.py`(`claim_execution`·`record_verdict`·`record_runner_capabilities`), `src/workflow/server/machine_api.py`(claim·산출물 경로), `src/workflow/server/worker.py`(판정 `record_verdict(... "passed" ...)` 지점 전부·`assemble_handoff`·`_spawn_successors`·`_cycle_followups`·`_queue_after_result`·`_queue_pull_request`), `src/workflow/server/next_step_runs.py`·`triage_runs.py`(판단 실행 요청문·깨끗한 체크아웃 선례), `src/workflow/server/internal_investigation.py`, `src/workflow/server/stage_runs.py`, `src/workflow/server/work_api.py`·`work_actions.py`·`auth.py`(phase 24 API·권한), `src/workflow/server/web.py`(`INPUT_KIND_CHOICES`·업무 패널 라우트), `src/workflow/server/templates/_work_panel.html`, `src/workflow/adapters/github_client.py`(`get_pull_request`·`list_pulls`), `src/workflow/connector/runner.py`(`_claim_and_start`·`_download_handoff`·`_handoff_dir`·`_EXTENSIONS`), `connector/adapter.py`·`claude.py`·`codex.py`·`prompt.py`·`git_ops.py`, `src/workflow/domain/team.py`(`can`), `src/workflow/connector/local_tool.py`(`launch_readonly`·`_run_triage` — 읽기 전용 실행 선례), `TRIAGE_OUTPUT_SCHEMA`(Codex 엄격 출력 스키마 선례 — `grep -rn TRIAGE_OUTPUT_SCHEMA src`), `adapters/repo.py` 의 `download_allowed`·`record_triage_proposed`, `executions.predecessor_execution_id`, `tests/workflow/server/test_app.py`(ARCHITECTURE "### 주소 표" 대조·행 수 단정), `tests/workflow/contracts/test_v1.py`(CONTRACT 4절 kind 줄 대조·`_SIGNATURES`)

먼저 실제 파일을 읽는다. 대화 이력을 전제로 판단하지 않는다.

## 작업

이 step 은 **문서만** 쓴다. `src/`·`tests/` 는 바꾸지 않는다. 다음 step 들이 이 문서의 이름·표·경로를 그대로 쓴다.

1. 코드를 읽어 README 결정이 코드 사실과 맞는지 확인한다. 다르면 ADR 에 "코드 조사로 README 와 달라진 사실" 로 적고 [근거]·[조정값] 결정만 고친다. [사용자] 결정과 충돌하면 고치지 말고 `blocked` 로 멈춘다.
2. `docs/adr/0033-handoff-report.md` — 배경(ADR-0031 결정 2·미결 두 개), 결정(D1~D41 요약: 원본=그 단계 Agent 의 결과 뒤 별도 실행, 맞춤 풀이=받는 사람이 고른 자기 Agent, 대상 결과, claim 대기, 형식=모델 JSON+서버 템플릿, 근거 확인, 전용 표, 프로토콜), D37(ADR-0031 결정 2 "handoff_bundle 확장"의 구체화), 하지 않는 것(직접 작업·알림·켜기/끄기·CLI·앱), 결과(러너 재설치 — 보내는 쪽·받는 쪽 모두). ADR-0031 "미결" 의 "리포트를 누가 만드는가"·"리포트 형식과 보관" 끝에 "→ [ADR-0033] 에서 정함" 한 줄씩.
3. `docs/ARCHITECTURE.md`: "화면 계층과 인계 리포트 — ADR-0031 (설계 전)" 절의 "인계 리포트" 줄을 ADR-0033 을 가리키게 고치고, 끝에 `## 인계 리포트 — phase 25` 절을 쓴다. 최소한 아래를 **이름까지** 고정한다.
   - **모듈 배치**: 순수 규칙 `src/workflow/domain/handoff_report.py`(step 3), 서버 쪽 시작·수집·판정 `src/workflow/server/handoff_reports.py`(step 4·5·6 — PR 근거 수집도 여기), 사람용 API 라우터 `src/workflow/server/report_api.py`(step 8), 러너 쪽 `src/workflow/connector/report_job.py`(step 7). 이름을 바꾸면 이 절이 기준이다.
   - **표(스키마 v27)**: `handoff_reports` — `report_id`(`hrp-` + 12 hex)·`session_id`·`work_item_id`·`source_execution_id`(원인 실행)·`source_task_id`·`job_type CHECK IN ('original','tailored')`·`base_report_id`(tailored 만, 원본 report_id)·`requested_by_member_id`(tailored 만)·`agent_id`·`connector_id`·`status CHECK IN ('queued','leased','done','failed','timed_out','unavailable')`·`reason`(사람이 읽는 사유 — `unavailable`·`failed`·`timed_out` 일 때)·`artifact_id`(done 일 때 `handoff_report` 산출물)·`leased_at`·`created_at`·`updated_at`·`finished_at`. 제약: original 은 원인 실행당 "진행 중(queued·leased) 하나" (다시 만들기는 새 행), tailored 는 (base_report_id, requested_by_member_id) 의 진행 중 하나. `handoff_report_decisions` — `report_id`·`item_id`(리포트 JSON 의 결정 항목 id)·`answer CHECK IN ('confirmed','changed')`·`note`(changed 면 필수, 1~500자)·`member_id`·`answered_at`, (report_id, item_id) 유일 — 마지막 답이 유효(덮어쓰기), 이력은 업무 이벤트 `report_decision` 로 남긴다([조정값]). 인덱스와 마이그레이션 v26→v27 순서.
   - **상태 전이**: 판정 `passed` → original `queued`(또는 D10 사유로 `unavailable`) → claim 시 `leased` → 제출 판정 → `done`/`failed`; `queued`·`leased` 로 30분(설정 이름 예 `HANDOFF_REPORT_WAIT_SECONDS`=1800, [조정값]) 지나면 `timed_out`. 늦게 온 제출은 `timed_out` 이면 받지 않는다(409) — 원인 결과가 재작업으로 대체된 것은 상관없이 그 실행에 붙인다(D16). 다시 만들기는 `failed`·`timed_out`·`unavailable`·`done` 어디서든 새 original 행(D12).
   - **대상 판정**(D4·D5·D10): 어느 판정 지점(worker 의 `record_verdict` 를 `passed` 로 부르는 곳 전부 — 코드·범용·검토·사내 요청 조사 결과 — 와 접수 판단 통과를 기록하는 `repo.record_triage_proposed`. 결과 뒤 판단(`TRIAGE_MODE_NEXT_STEP`)은 제외 — D4)에서 리포트를 만드는지 표. 실행 실패·`failed` 판정 제외. 리포트 불가 사유 문구 표(`원인 에이전트 없음`·`API 연결형 에이전트`·`러너가 리포트를 지원하지 않음` 등 — 러너 지원은 그 connector 의 마지막 claim `capabilities` 에 `handoff_report` 가 있는지).
   - **claim 규칙**(D9·D11·D35·D36): `claim_execution` 순서 — (1) 이 connector 에 배정된 `queued` 리포트 작업(능력 선언 시) (2) 기존 실행. 기존 실행은 원인 실행(`executions.predecessor_execution_id` — 다른 경로가 있으면 코드로 확인해 함께 적는다)의 최신 original 리포트가 `queued`·`leased` 면 건너뛴다. **판단 단계 실행(접수·결과 뒤)은 대기하지 않는다**(D15). 응답 형태: 리포트 작업이면 `{"job_type": "handoff_report", ...ReportJob}`, 실행이면 기존 `ExecutionRequest` 그대로(능력 미선언 러너는 바뀌지 않음). `job_type` 판별을 러너가 어떻게 하는지(응답에 `job_type` 키가 있으면 리포트 작업).
   - **받는 러너 전달**(D14·D23): 능력을 선언한 러너가 실행을 claim 할 때, 원인 실행의 최신 `done` original 리포트 JSON 과 결정 답을 어떻게 붙이는지 — `ExecutionRequest` 에 선택 칸(예 `handoff_report_artifact_id: str | None`, `handoff_decisions: list[...] | None`, null 이면 직렬화에서 빠져 옛 러너 응답이 같다 — `_OmitUnknownMeasure` 선례 확인)을 두고, 러너가 handoff dir 에 `report.json`·`decisions.json` 을 쓴다. `HandoffBundle` 계약·`assemble_handoff` 는 바꾸지 않는다.
   - **표 추가 칸**: 리포트 작업 입력 자료(PR 근거·`pr_evidence_error`·업무 원문 요약 등 행을 만들 때 모은 것)를 claim 까지 보관할 칸(예 `inputs_json`).
   - **리포트 산출물의 소유 실행**: `artifacts.execution_id` 는 NOT NULL·FK·`UNIQUE(execution_id, kind, sha256)` 다 — original·tailored 리포트 산출물을 어느 실행에 매다는지(권장: 원인 실행)와, 받는 러너가 claim 때 붙은 리포트 id 를 내려받을 수 있도록 `download_allowed` 를 어떻게 넓히는지(예: 그 connector 가 claim 한 실행의 원인 실행에 매달린 `handoff_report`).
   - **repo 함수 시그니처**(step 2): 만들기·불가 기록·가져오기·완료·실패·시간 초과·최신 조회·목록·결정 답 쓰기/읽기·tailored 조회 — 이름과 인자.
   - **계약 칸 이름**(step 1): `HandoffReport` 의 절 키·주장 id·`evidence` 형식·'기록 없음' 표시·mermaid 칸, 맞춤 풀이·제출·실패·결정 답 본문 모델 이름과 칸, 크기 제한 값(주장 수·글자 수·Mermaid 길이)(D31), PR 근거 길이 상한(D22).
   - **서버 함수 이름**: 판정 지점에서 부를 함수(step 5 — 같은 트랜잭션인지 직후인지), 시간 초과 처리, PR 근거 수집(step 4), API·웹이 함께 쓸 행동 함수(결정 답·맞춤 풀이 요청·다시 만들기 — step 8·9), `Settings` 칸 이름(환경변수 `WORKFLOW_HANDOFF_REPORT_WAIT_SECONDS`, D39).
   - **러너 리포트 작업 실행**(step 7): 어댑터 경계 — `ExecutionAdapter.run` 은 `ExecutionRequest` 만 받으므로 리포트 작업을 어떻게 넘기는지(`local_tool.launch_readonly` 처럼 읽기 전용 실행 함수를 쓰는 방식 권장), Codex 엄격 출력 모드용 리포트 출력 스키마(이름, `TRIAGE_OUTPUT_SCHEMA` 선례), 러너가 체크아웃할 등록(`local_registration_id`)·커밋을 `ReportJob` 의 어느 칸에서 얻는지.
   - **러너 엔드포인트**: 리포트 산출물 내려받기·제출 경로(예 `POST /api/v1/report-jobs/{report_id}/result` 본문 = 리포트 JSON, `POST /api/v1/report-jobs/{report_id}/failure` 본문 = 사유), 연결 토큰 인증, 남의 작업 403, 상태가 `leased` 가 아니면 409. 리포트 작업 입력 자료(원인 결과 봉투·산출물 id·업무 원문·PR 근거·결정 대상)를 `ReportJob` 안에 싣는지 산출물로 내려받게 하는지.
   - **리포트 작업 실행 환경**(D18): 러너가 결과 커밋(없으면 base 커밋, 사용자 정의 종류는 `LocalTarget` 폴더 상태)을 깨끗한 읽기 전용 체크아웃으로 열고, 로컬 근거(`git log base..result`, `git diff --stat`, 커밋 메시지)를 모아 어댑터에 넘기는 방식. 쓰기·커밋 금지를 어떻게 강제하는지(판단 실행의 방식을 코드에서 확인해 같은 방식). 맞춤 풀이는 체크아웃 없이 원본 리포트 JSON 만 입력.
   - **PR 근거 수집**(D22): 중앙이 리포트 행을 만들 때(또는 claim 직전) GitHub App 으로 읽는 것 — 업무에 연결된 PR(`work_pull_requests`·`task_pull_requests`)의 본문·커밋 목록·리뷰 코멘트. 새 `GitHubClient` 메서드 이름·반환 타입, 읽기 실패 시 `pr_evidence_error` 문구. App 권한(pull_requests read 로 리뷰 코멘트를 읽을 수 있는지) 확인 결과.
   - **판정**(D20): 제출 JSON 검증 순서 — 계약 검증 실패·크기 초과 → `failed`(사유), 근거 ID 미확인 → 그 주장에 표시하고 `done`. 확인 가능한 근거 집합(D21)과 근거 ID 형식(예 `artifact:<id>`·`commit:<sha>`·`pr:<번호>`·`source:work`·`verification`)을 표로.
   - **사람용 API**(D29, step 8): 경로·메서드·권한·응답 표 — 리포트 HTML 을 `/api/v1` 로도 내는 경로를 포함한다(D29). 예: `GET /api/v1/work-items/{key}/reports`(단계별 최신·이전 목록), `GET /api/v1/reports/{report_id}`(JSON — 근거 확인 결과 포함), `GET /api/v1/reports/{report_id}.html` 또는 화면 경로, `PUT /api/v1/reports/{report_id}/decisions/{item_id}` `{"answer","note"}`(받는 사람·관리자, D13), `POST /api/v1/reports/{report_id}/tailor` `{"agent_id"}`(받는 사람이 자기 Agent 로, D8), `POST /api/v1/reports/{report_id}/regenerate`(보낸 사람·받는 사람·관리자, D12). 보낸 사람·받는 사람 판정 함수 이름과 정의(D7). 읽기 전용 토큰은 GET 만(ADR-0032).
   - **화면**(D32, step 9): 단계 줄 문구 세 가지, 리포트 페이지 경로·절 제목·"근거 확인 안 됨"·"기록 없음"·"PR 읽기 실패"·"옛 리포트 기준" 문구, 결정 답 폼, [내게 맞춰 풀기]·[리포트 다시 만들기] 버튼 노출 조건, mermaid 파일 위치(`server/static/vendor/mermaid.min.js`, 버전·라이선스 표기)와 `securityLevel: 'strict'`.
   - **step 경계**: README "step" 표 그대로 + 각 step 이 고칠 기존 테스트 단정 — step 1: `RUNNER_CAPABILITIES` 와 그 단정(`test_v1.py`·`tests/workflow/connector/test_client.py`), `_SIGNATURES` 대응. step 2: `ARTIFACT_KINDS` + CONTRACT 4절 kind 줄 + `artifacts` 재생성 + 스키마 버전 26→27 단정 + `test_db.py` "기존 표 재생성 안 함" 제외 목록 + `test_backup.py` 표별 행 수 dict. step 6·8·9: ARCHITECTURE "### 주소 표" 행 추가와 `test_app.py` 행 수 단정.
   - **새 라우트 위치**: 리포트 러너·사람용 API 경로는 이 phase 25 절에만 적는다. 기존 "### 주소 표"(`test_app.py` 가 대조)에는 이 step 에서 넣지 않는다 — 라우트를 만드는 step 6·8·9 가 그 표에 행을 더하고 행 수 단정을 고친다.
   - **mermaid**(D40): 정확한 버전과 내려받을 URL(cdn.jsdelivr.net 또는 unpkg 의 `mermaid@<버전>/dist/mermaid.min.js`)·sha256 을 적는다.
4. `docs/CONTRACT.md` 에 `## 19. 인계 리포트 — phase 25` 절: 리포트 JSON(`HandoffReport`) 예시, 리포트 작업(`ReportJob` original·tailored) 예시, claim 응답 예시, 결정 답 예시, 16.2 capabilities·4절 kind 목록 줄은 이 step 에서 고치지 않는다(16.2 는 step 1, 4절은 step 2 가 상수와 함께 — 테스트가 대조할 수 있다). **4절 산출물 kind 목록 줄은 이 step 에서 고치지 않는다** — `test_v1.py::test_artifact_kinds_match_contract_md_list` 가 그 줄을 `ARTIFACT_KINDS` 와 대조하므로 step 2 가 상수와 함께 고친다. **계약 모델이 있는 예시는 ` ```json ` 으로 쓰고 `tests/workflow/contracts/test_v1.py` 의 블록 개수·`_SIGNATURES` 대응을 step 1 이 맞춘다고 이 절에 적는다**(step 1 이 모델을 만든다). 모델이 없는 예시(API 응답)는 ` ```jsonc ` 로 쓰고 표·본문에 인라인 `` `{…}` `` JSON 을 쓰지 않는다(`_INLINE` 정규식). 쓴 뒤 `python3 -m pytest tests/workflow/contracts -q` — ` ```json ` 블록 때문에 실패하면 이 step 에서는 ` ```jsonc ` 로 두고 step 1 이 ` ```json ` 으로 바꾸게 ARCHITECTURE step 경계에 적는다(이 step 은 테스트를 고치지 않는다).
5. `docs/GLOSSARY.md` 에 phase 25 용어: 인계 리포트 / 원본 리포트(`original`)·맞춤 풀이(`tailored`)·리포트 작업·결정 항목·결정 답·보낸 사람·받는 사람·`handoff_report`(산출물 kind — 금지 표현: `report_output` 과 혼동, `요약`, `문서화`)·근거 확인 안 됨.

## 테스트

- 문서만이므로 테스트를 새로 쓰지 않는다. 기존 테스트 전체가 그대로 통과하는지 확인한다.

## 금지사항 (이 step)

- `src/`·`tests/` 를 바꾸지 마라. 이유: 문서만 쓰는 step 이다.
- README [사용자] 결정을 문서에서 다르게 적지 마라. 이유: 사용자가 정한 것이다 — 코드와 충돌하면 `blocked`.
- `docs/CURRENT_HANDOFF.md` 를 고치지 마라. 이유: 로컬 전용 파일이며 하네스 뒤 사람이 갱신한다(PR #10).

## Acceptance Criteria

```bash
python3 -m pytest -q
python3 -m ruff check .
```

위 커맨드와 이 step 의 "테스트" 항목을 모두 확인한다.

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트: ARCHITECTURE.md 디렉토리 구조·외부 의존 표를 벗어나지 않았는가, ADR 기술 스택 안인가, AGENTS.md CRITICAL 규칙(도메인 순수성, `server`↔`connector` 상호 import 금지, 외부 입력으로 명령·경로 실행 금지, 비밀값)을 지켰는가, GLOSSARY 용어를 그대로 썼는가.
3. `phases/25-handoff-report/index.json` 의 이 step 을 갱신한다: 성공 → `"status": "completed"`, `"summary"`(다음 step 에 필요한 파일·이름·결정), `"verification"`(실행한 AC 명령과 종료 코드). 3회 수정해도 실패 → `"error"` + `"error_message"`. 사용자 개입 필요 → `"blocked"` + `"blocked_reason"` 후 즉시 중단.
