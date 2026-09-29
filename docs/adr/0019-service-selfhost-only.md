# ADR-0019: `service` 브랜치는 셀프호스트만 — demo 모드·진단 데모·대본 에이전트 제거

결정일: 2026-09-29 (phase 13 step 0). 근거: [재설계 계획](../product/REDESIGN_PLAN.md) 16절 결정 2("`service` 는 셀프호스트만"), 2026-09-29 사용자 결정 "demo 걷어내기는 별도 phase 로 먼저, 그 뒤 14-task-model". 기본값은 [phase 13 README](../../phases/13-selfhost-only/README.md) "계획 기본값"이며 이 문서로 구현 기준을 고정한다. 적용 범위는 `service` 브랜치(실서비스 통합)이며 `main`(공개 데모, 심사 ~2026-10-05 동결)은 바꾸지 않는다. 이 시점에는 구현이 없다 — step 1~5 가 만든다.

## 결정

1. **모드 개념을 없앤다.** 앱은 늘 [ADR-0016](0016-selfhost-docker-fixed-workspace.md) 결정 3 의 셀프호스트 동작이다 — 고정 워크스페이스 `sess-selfhost`(`SELFHOST_SESSION_ID`) + `OPERATOR_TOKEN` 로그인(`/login`·`/logout`). 익명 세션·`/operator` 토큰 입력으로 세션을 운영자로 올리는 경로·공개 랜딩은 없다. `Settings.mode` 는 사라진다.
   - `WORKFLOW_MODE` 는 동작을 정하지 않는다. 빈 값·미설정·`selfhost` 는 허용하되 읽고 버린다(기존 compose·`.env` 가 그대로 뜨게).
   - `WORKFLOW_MODE=demo`(그 밖의 값도)는 `load_settings` 가 시작 때 설정 오류로 멈춘다 — `ValueError` 계열 `SettingsError`, 메시지는 "WORKFLOW_MODE=demo 는 지원하지 않습니다 — service 브랜치는 셀프호스트 전용이며 공개 데모는 main 브랜치입니다" 취지. 조용히 셀프호스트로 뜨지 않는다(공개 데모로 착각한 배포가 로그인 화면으로 바뀌는 것을 막는다).
   - `DIAG_API_TOKEN` 은 요구하지 않는다(진단이 없다). `/healthz` 응답의 `mode` 칸은 호환을 위해 `"selfhost"` 고정값으로 둔다.
   - `/` 는 로그인 상태면 `/tasks`, 아니면 `/login` 으로 303 한다(랜딩 없음). 예전 `/operator/login` 은 없앤다(404) — 로그인 경로는 `/login` 하나다(step 2).
   - 카탈로그 등록 단계가 없으므로 `/operator/agents` 로 등록한 Agent 는 그 운영자 워크스페이스(`session_agents`)에 바로 붙는다. 러너 등록(`/connector/registrations`)은 지금처럼 새 Agent 만 워크스페이스에 붙인다(step 2).
2. **`service` 에서 지우는 것** (phase 13 README "조사로 확인한 현재" 전부, 원본은 `main` 에 남는다):
   - 모드 분기 — `server/auth.py`·`server/web.py`·`server/machine_api.py`·`adapters/repo.py`·`server/app.py` 의 `mode` 분기, 템플릿(`home.html`·`_sidebar.html`·`_live.html`·`operator.html`·`task_new.html`·`sources.html`·`login.html`)의 `mode` 분기, 공개 랜딩(`landing.html`), 익명 세션 발급.
   - 모드 가드 없이 열린 demo 전용 경로 — 카탈로그 등록(`/agents/register`·`/agents/{id}/unregister`, `agents.shared_to_all_sessions` 사용 코드), fixture 업무 가져오기(`/tasks/import`, `adapters/task_sources.py`·`task_source_fixtures/`), 시연 예시 폼(`web.EXAMPLES`·`with_successor`), 진단 기본 폼값(`web._EMPTY_FORM` 의 진단 값).
   - 진단 데모 — `src/diagnostic_demo/` 전체, `adapters/diag_client.py`, `server/worker.py` 진단 경로, `domain/verification.py` 의 데모 검사(`_Demo`), `diagnosis_usage` 사용 코드·진단 한도 설정.
   - 내장 종류 `diagnosis`·`code_change` 와 그 전용 코드 — 보고서 데모(`connector/local_tool.py` `DEMO_REPORT_KINDS`·`vp-report`, `domain/execution_policy.py` `report_code_change`), 병합 확인 대기열(`web._awaits_merge`, `tasks.merge_confirmed_at` 사용 코드).
   - 대본 에이전트와 VM 배포 — `src/workflow/scripted/`, `agents.demo_scripted` 사용 코드, `deploy/bin/{codex,claude}`, `deploy/systemd/*`, `deploy/env/*`, `deploy/install-vm.sh`·`update-vm.sh`·`backup.sh`·`Caddyfile`, `deploy/launchd/`(데모 러너), `scripts/seed_demo.py`·`local_stack.py`·`scaffold_demo_repo.py`·`diag_eval.py`·`make_handoff_dir.py` 와 각 `scripts/test_*`.
   - 위 코드만 검증하는 테스트. 셀프호스트 경로를 함께 보는 테스트는 지우지 않고 셀프호스트·`bug_fix` 기준으로 고친다(step 1).
3. **남기는 것** (지우면 셀프호스트 실사용이 깨진다):
   - `sessions` 와 `session_id` — 칸 이름은 그대로, 뜻은 "워크스페이스 키"(셀프호스트는 `sess-selfhost` 하나). `sessions.is_operator` — 셀프호스트 워크스페이스는 운영자다. `session_agents` — 러너가 만든 Agent 를 워크스페이스에 붙이며 GitHub 순환·후보 선택이 이 표를 쓴다.
   - 산출물 종류 `code_change_result`(`bug_fix` 의 결과), `diff`·`test_log_*`·`verification_log` 등 — 종류 이름 `code_change` 가 사라져도 산출물 종류는 계약 v1 그대로다.
   - n8n 입구(`POST /sources/n8n/chains`, `source_tokens`, `chains`, callback) — 실사용 입구다([ADR-0010](0010-n8n-inbox-and-callback.md)).
   - 직접 등록 폼(`/tasks/new`) — 종류 선택 기본값만 `bug_fix` 로 바꾼다.
   - 셀프호스트 배포 `deploy/selfhost/*`(러너 plist `com.workflow.selfhost.connector` — `deploy/launchd/` 와 무관).
   - phase 8·11·12 의 GitHub 순환(수집 → 수정 → 검토 → 초안 PR → 병합 추적) 전부.
4. **내장 종류는 `bug_fix`·`code_review` 두 개, 내장 규칙은 `bug_fix --[ready_for_review]--> code_review` 하나.** `BUILTIN_KIND_NAMES = ("bug_fix", "code_review")`. 스키마 v9 마이그레이션이 기존 워크스페이스의 `diagnosis`·`code_change` 종류 행과 그 둘을 잇는 내장 규칙 행(`from_kind=diagnosis`, `to_kind=code_change` — `succession_rules` 에 내장 표시 칸이 없어 이 쌍으로 식별한다, 워크스페이스마다 하나)을 지운다. 그 종류를 쓰는 Task 가 하나라도 있거나, 그 쌍 밖에서 두 종류 중 하나를 `from_kind`/`to_kind` 로 가진 후속 규칙(사용자가 등록한 규칙)이 있으면 마이그레이션을 중단한다(예외 메시지에 개수를 적고 DB 는 v8 그대로 — v4→v5 마이그레이션의 중단 방식과 같다). 셀프호스트 DB 에는 해당 행이 없다.
5. **칸·표 삭제는 하지 않는다.** `tasks.merge_confirmed_at`·`tasks.review_decision`, `agents.shared_to_all_sessions`·`agents.demo_scripted` 칸과 `diagnosis_usage` 표는 v9 에서 지우지 않는다. SQLite 칸 삭제는 테이블 재생성이 필요하므로 14-task-model 에서 `tasks` 를 다시 볼 때 함께 판단한다. 코드에서 읽고 쓰지 않게만 한다.

## 대안

- **모드 유지(`demo` 기본, 코드 두 벌).** `main` 과 코드를 맞추기 쉽지만, 인증·화면·워커가 두 모드를 계속 지원해야 하고 테스트도 두 벌로 돈다. 다음 phase(14-task-model)가 Task·후속·상태 코드를 넓게 고칠 때 진단·카탈로그·대본 경로와 그 테스트(약 250개 삭제 대상, 150~250개 수정 대상)까지 같이 고쳐야 한다. 기각.
- **`main` 에서도 제거.** 공개 데모가 심사(~2026-10-05) 중이라 `main`·공개 VM 은 동결이다. demo 코드의 원본도 거기 남아야 한다. 기각.
- **`WORKFLOW_MODE=demo` 를 조용히 무시.** 설정을 잘못 옮긴 배포가 경고 없이 로그인 화면으로 뜬다. 시작 때 멈추는 쪽이 원인을 바로 알려 준다. 기각.

## 결과

- `service` 코드에는 모드 분기가 없다. 인증은 한 경로(고정 워크스페이스 로그인), 러너(`wfc_`)·n8n(`wfs_`) 인증은 그대로다.
- [ADR-0008](0008-public-demo-scripted-agents.md)(대본 에이전트)·[ADR-0016](0016-selfhost-docker-fixed-workspace.md) 결정 4·5(모드 기본값 `demo`, 진단 선택)는 `service` 에서 이 ADR 이 대체한다. [ADR-0003](0003-diagnosis-model-openai-gpt41-mini.md)·[ADR-0005](0005-access-model-anonymous-session-operator-token.md)·[ADR-0006](0006-deployment-vm-caddy-mac-connector.md) 는 `main` 공개 데모의 기록으로 남는다.
- 문서의 진단 데모·대본 에이전트 절은 지우지 않고 "`main` 전용(ADR-0019)" 표시를 단다. GLOSSARY 의 진단 용어도 `main` 이 쓰므로 남긴다.
- `main` 의 demo 수정을 `service` 로 병합하면 지운 파일과 충돌한다. `main` 은 동결이며 demo 수정은 `service` 로 가져오지 않는다.
- 진단 삭제에 따른 세부(step 3): 설정에서 `DIAG_API_TOKEN`·`DIAG_API_URL`·진단 한도(`WORKFLOW_LIMIT_PER_SESSION_DAILY`·`_GLOBAL_DAILY`·`_ATTACHMENTS_MAX_BYTES`)를 뺀다 — 진단과 무관한 `ACTIVE_TASKS_PER_SESSION`·`UNKNOWN_AFTER_SECONDS`·`HEARTBEAT_OFFLINE_SECONDS` 는 남는다. AGENTS.md 비밀값 목록에서 `DIAG_API_TOKEN` 을 뺀다(`service` 가 읽지 않는다). `OPENAI_API_KEY` 는 운영자 환경에 있을 수 있어 목록에 남기고 러너는 여전히 도구 프로세스 환경에서 뺀다. 계약 v1 의 진단 모양(`DiagnosisTarget`·`DiagnosisResult`, `kind` `diagnosis`·`code_change` 요청)은 `kind_spec` 없는 옛 요청으로만 검증하고, `kind_spec` 이 있으면 같은 이름의 사용자 정의 종류로 본다. 자동 완료하는 종류가 없으므로 `completion_mode=auto` 는 늘 422 다. `bug_fix` 대상은 종류 이름이 아니라 실행 정책(`target == "code_change"`)으로 담당 Agent 의 등록 보고(등록·기준 커밋·첫 검증 프로필)에서 채운다 — n8n 입구로 들어온 `bug_fix` 첫 업무도 시작된다.
