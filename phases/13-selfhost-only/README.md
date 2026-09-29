# Phase 13 — `service` 는 셀프호스트만: demo 모드 걷어내기

작성일: 2026-09-29. 상태: 구현 계획 작성 완료, 모든 step pending. 근거: [재설계 계획](../../docs/product/REDESIGN_PLAN.md) 16절 결정 2("`service` 는 셀프호스트만 — demo 모드는 `main` 과 공개 VM 에만"), 2026-09-29 사용자 결정 "demo 걷어내기는 별도 phase 로 먼저, 그 뒤 14-task-model".

## 왜 먼저 하나

다음 phase(14-task-model)는 업무·단계 모델을 바꾸며 Task·후속·상태 코드를 넓게 고친다. demo 코드(진단·카탈로그·대본 에이전트·fixture 가져오기)가 남아 있으면 그 코드와 테스트(약 250개 삭제 대상, 150~250개 수정 대상)까지 같이 고쳐야 한다. 먼저 걷어내 고칠 범위를 줄인다.

## 조사로 확인한 현재 (2026-09-29, `service` d7bd9ae)

- 모드 스위치는 `WORKFLOW_MODE`(→ `Settings.mode`, `demo` 기본 | `selfhost`) 하나. 분기는 `server/auth.py:100-181`, `server/web.py`(212·426-482·939·1982-1990), `server/machine_api.py:154-156`, `adapters/repo.py:312-330`, `server/app.py:72-73`, 템플릿 `home.html`·`_sidebar.html`·`_live.html`·`operator.html`·`task_new.html`·`sources.html`·`login.html`.
- demo 전용인데 모드 가드 없이 열린 것: 카탈로그 등록(`/agents/register`·`/agents/{id}/unregister`, `agents.shared_to_all_sessions`), fixture 업무 가져오기(`/tasks/import`, `adapters/task_sources.py`·`task_source_fixtures/`), 시연 예시 폼(`web.EXAMPLES`·`with_successor`), 진단 기본 폼값(`web._EMPTY_FORM`).
- 진단: `src/diagnostic_demo/` 전체, `adapters/diag_client.py`, `server/worker.py` 진단 경로(59-63·249-343·546-750·1799), `domain/verification.py` `_Demo` 검사(172-430), `diagnosis_usage` 표·한도 설정. 셀프호스트는 `DIAG_API_TOKEN` 이 비어 진단이 꺼져 있다.
- 내장 종류 `diagnosis`·`code_change`: 모든 세션에 seed 되지만 셀프호스트 실사용은 `bug_fix`·`code_review` 뿐. `code_change` 는 보고서 데모(`connector/local_tool.py` `DEMO_REPORT_KINDS`·`vp-report`, `domain/execution_policy.py` `report_code_change`)와 병합 확인 대기열(`web._awaits_merge`, `tasks.merge_confirmed_at`) 전용.
- 대본 에이전트: `src/workflow/scripted/`, `agents.demo_scripted`, `deploy/bin/{codex,claude}`, `deploy/systemd/*`, `deploy/env/*`, `deploy/install-vm.sh`·`update-vm.sh`·`backup.sh`·`Caddyfile`·`deploy/launchd/`(데모 러너), `scripts/seed_demo.py`·`local_stack.py`·`scaffold_demo_repo.py`·`diag_eval.py`·`make_handoff_dir.py` 와 각 `scripts/test_*`.

## 남기는 것 (지우면 셀프호스트가 깨진다)

- `sessions` 와 `session_id`(뜻은 "워크스페이스"), `sessions.is_operator`(셀프호스트 워크스페이스는 운영자), `session_agents`(러너가 만든 Agent 를 워크스페이스에 붙인다 — GitHub 순환·후보 선택이 이 표를 쓴다).
- 산출물 종류 `code_change_result`(`bug_fix` 가 쓴다), `diff`·`test_log_*`·`verification_log` 등.
- n8n 입구(`/inbound/…`, `source_tokens`, `chains`, callback) — 실사용 입구다(ADR-0010).
- 직접 등록 폼(`/tasks/new`) — 종류 선택 기본값만 `bug_fix` 계열로 바꾼다.
- 셀프호스트 러너 `deploy/selfhost/*`(자체 plist `com.workflow.selfhost.connector` 를 쓴다 — `deploy/launchd/` 와 무관).

## 계획 기본값 (step 0 이 ADR-0019 로 고정)

1. 모드 개념을 없앤다. 앱은 늘 셀프호스트 동작(고정 워크스페이스 `sess-selfhost` + 운영자 토큰 로그인)이다. `WORKFLOW_MODE` 는 읽지 않는다 — 값이 `demo` 로 들어오면 시작 때 설정 오류로 멈춘다(조용히 무시하지 않는다).
2. 진단 데모·대본 에이전트·카탈로그·fixture 가져오기·VM 배포 파일은 `service` 에서 지운다. 원본은 `main` 에 그대로 있다.
3. 내장 종류는 `bug_fix`·`code_review` 두 개. 스키마 v9 마이그레이션이 기존 세션의 `diagnosis`·`code_change` 종류 행과 그 둘을 잇는 내장 규칙을 지운다. 그 종류를 쓰는 Task 가 하나라도 있으면 마이그레이션을 중단한다(데이터 보존 — 셀프호스트 DB 에는 없다).
4. `tasks.merge_confirmed_at`·`review_decision` 칸, `agents.shared_to_all_sessions`·`demo_scripted` 칸, `diagnosis_usage` 표는 v9 에서 지우지 않는다(SQLite 칸 삭제는 테이블 재생성 — 14 에서 `tasks` 를 다시 볼 때 함께 판단). 코드에서 쓰지 않게만 한다.

## Step 목록

| Step | 이름 | 산출물 |
|---|---|---|
| 0 | selfhost-only-design | ADR-0019, ARCHITECTURE·GLOSSARY·README 정리 |
| 1 | selfhost-fixtures | 테스트 기반(conftest·seed)을 셀프호스트·`bug_fix` 기준으로 — 코드 삭제 전, 전부 green |
| 2 | remove-demo-web | 모드 분기·랜딩·익명 세션·카탈로그·fixture 가져오기·시연 폼 삭제 |
| 3 | remove-diagnosis | 진단 데모·내장 `diagnosis`·`code_change`·보고서 데모·병합 확인 대기열 삭제, 스키마 v9 |
| 4 | remove-demo-deploy | `scripted/`·VM 배포 파일·데모 스크립트 삭제, AGENTS.md·DEPLOY 정리 |
| 5 | selfhost-only-verify | 전체 회귀·e2e·셀프호스트 e2e·v8 DB 사본 마이그레이션, 문서·인계 |

## 실행

```bash
git status --short            # 깨끗해야 한다
git branch --show-current     # service
python3 scripts/execute.py 13-selfhost-only --engine claude
```

끝나면 `feat-13-selfhost-only` 를 `service` 에 `--no-ff` 병합한 뒤 14-task-model 을 돌린다. 사용자가 띄워 둔 셀프호스트(compose 프로젝트 `runloom`)·러너(launchd)·`/Users/kje/demo/*` 는 건드리지 않는다. 셀프호스트 재설치는 사용자 지시로 phase 뒤에 한다.
