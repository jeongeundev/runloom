# Step 3: remove-diagnosis — 진단 데모·내장 `diagnosis`·`code_change` 삭제, 스키마 v9

## 읽어야 할 파일

- AGENTS.md
- phases/13-selfhost-only/README.md, phases/13-selfhost-only/index.json (step 0~2 summary)
- docs/adr/0019-service-selfhost-only.md, docs/ARCHITECTURE.md ("셀프호스트 전용 — phase 13" 절, "업무 종류와 후속 규칙" 절)
- src/diagnostic_demo/ (지울 대상 — 무엇이 있는지만 확인), pyproject.toml (`packages`)
- src/workflow/adapters/diag_client.py, src/workflow/adapters/db.py (`init_schema`, `_migrate_*`, `PHASE8_KIND_NAMES`), src/workflow/adapters/repo.py (`create_session`, `diagnosis_usage` 함수, `confirm_merge`)
- src/workflow/contracts/v1.py (`BUILTIN_KINDS`, `BUILTIN_RULES`, `BUILTIN_KIND_NAMES` 류, `ARTIFACT_KINDS`)
- src/workflow/server/worker.py (진단 경로·진단 클라이언트), src/workflow/server/web.py (`_check_diagnosis_limits`, `_target_for`, `_awaits_merge`, 병합 확인 경로), src/workflow/server/views.py, src/workflow/server/settings.py (`DIAG_*`, `Limits`), src/workflow/server/templates/operator.html·task_new.html·_live.html
- src/workflow/domain/verification.py (`_Demo`), execution_policy.py (`report_code_change`), task_readiness.py (`LEGACY_BUILTIN_KINDS`), completion.py, kinds.py (`can_auto_complete`), evidence_location.py, report_expectation.py, defaults.py
- src/workflow/connector/local_tool.py (`DEMO_REPORT_KINDS`, `vp-report`), src/workflow/connector/adapter.py (`SUPPORTED_BUILTIN_KINDS`)
- tests/workflow/adapters/test_db.py (마이그레이션 테스트 형식)

## 작업

1. **진단 데모 패키지**: `src/diagnostic_demo/`·`tests/diagnostic_demo/` 삭제, `pyproject.toml` `packages` 에서 제거. `adapters/diag_client.py`·그 테스트 삭제. 워커의 진단 실행·검증·클라이언트 경로 삭제. 설정의 `DIAG_API_TOKEN`·`DIAG_API_URL`·진단 한도(`WORKFLOW_LIMIT_*`) 삭제 — 셀프호스트 `.env.example`·compose 에 있으면 함께 뺀다(`tests/test_selfhost_files.py` 갱신). `diagnosis_usage` 를 읽고 쓰는 코드 삭제(표는 남김).
2. **내장 종류**: `BUILTIN_KINDS` 는 `bug_fix`·`code_review`, `BUILTIN_RULES` 는 `bug_fix → code_review` 하나. 진단·보고서 데모 전용 도메인 코드(`verification._Demo`, `execution_policy.report_code_change`, `evidence_location`·`report_expectation` 이 진단 전용이면 모듈째, `task_readiness.LEGACY_BUILTIN_KINDS`, `completion`·`kinds.can_auto_complete` 의 진단 분기)를 지운다. 산출물 종류 `diagnosis_result`·`evidence`·`handoff_bundle` 등이 더는 쓰이지 않으면 `ARTIFACT_KINDS` 에서 빼지 **않는다** — 기존 DB `artifacts.kind` CHECK 와 계약 v1 호환 때문(ADR-0019 에 적힌 대로).
3. **러너**: `connector/local_tool.py` 의 보고서 데모(`DEMO_REPORT_KINDS`, `vp-report`), `connector/adapter.py` `SUPPORTED_BUILTIN_KINDS` 에서 두 종류 제거. 러너가 지원 종류로 `diagnosis`·`code_change` 를 더 보고하지 않는다.
4. **병합 확인 대기열**: `web._awaits_merge`·`confirm_merge` 경로·`operator.html` "병합 확인 대기" 삭제. `merge_confirmed_at`·`review_decision` 을 읽는 지표(`domain/metrics.py`)는 bug_fix 경로(`pr_merged_at`)가 이미 있으므로 진단 전용 분기만 지운다 — 지표 테스트 기대값이 바뀌면 이유를 summary 에.
5. **스키마 v9** (`db.py`): `SCHEMA_VERSION = 9`, `_migrate_8_to_9` 를 `init_schema` 사슬에 추가(4~8 → 9). 하는 일: 모든 세션에서 `kinds` 의 `diagnosis`·`code_change` 행과 그 둘이 from/to 인 `succession_rules` 행을 지운다. **먼저 검사**: 그 종류의 `tasks`·`executions` 가 있거나 사용자 정의 규칙이 그 종류를 가리키면 `RuntimeError`(세션:종류 목록 포함)로 중단 → 호출자 트랜잭션 ROLLBACK. `PRAGMA foreign_key_check` 통과 확인. 새 DB 는 두 종류를 seed 하지 않는다. `_seed_phase8_kinds`(4 → 5) 는 그대로 둔다(옛 DB 경로).
6. **AGENTS.md**: 기술 스택의 "진단 데모" 줄, 아키텍처 규칙의 `src/diagnostic_demo/` 문장, "제품 코드와 진단 데모의 경계" 절, 명령어의 진단 API·진단 워커 줄을 지우거나 "`main` 전용" 으로 줄인다. `OPENAI_API_KEY`·`DIAG_API_TOKEN` 은 비밀값 목록에서 빼도 되는지 ADR-0019 에 맞춘다.

## 테스트 먼저

- db: 빈 DB → v9, 내장 종류 2개·규칙 1개. v8 DB(두 종류 행 있음, 해당 Task 없음) → v9 에서 행 삭제·다른 데이터 보존. v8 DB 에 `diagnosis` Task 가 있으면 마이그레이션 실패·버전 8 그대로·데이터 불변. v4~v7 DB 가 v9 까지 오르는지(기존 마이그레이션 테스트 기대 버전 갱신).
- contracts: `BUILTIN_KINDS` 두 개, `KindSpec(builtin=True)` 이름 검사.
- worker·web: 진단 경로 없이 bug_fix 순환 회귀 통과. `/operator` 에 병합 확인 대기열 없음.
- connector: 지원 종류 보고에 두 종류 없음.

소스·배포 파일 변경 전에 `tests/` 미러 경로에 실패 테스트를 작성하고 실패 원인이 의도한 것인지 확인한다(`tdd-guard.sh` 가 테스트 없는 소스 작성을 막는다). 구현 후 해당 테스트와 전체 회귀를 통과시킨다.

## Acceptance Criteria

```bash
python3 -m pytest -q
python3 -m ruff check .
```

위 커맨드와 이 step 의 테스트 항목을 모두 확인한다.

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 규칙을 확인한다: `domain/` 은 FastAPI·sqlite3·HTTPX·subprocess·Git 을 import 하지 않는다 / `server/` 와 `connector/` 는 서로 import 하지 않고 `contracts/` 만 공유한다 / 외부 입력(요청 본문·이슈 본문·모델 응답)에서 명령·경로·브랜치 이름을 받아 실행하지 않는다 / 비밀값(연결 토큰, `OPERATOR_TOKEN`, `SESSION_SECRET`, GitHub App 비밀·설치 토큰, PAT, 알림 웹훅 URL, 러너 로컬 등록의 환경변수 값)은 DB·로그·응답·템플릿·백업·중앙에 넣지 않는다 / GLOSSARY 이름을 그대로 쓴다 / phase 8·11·12 의 GitHub 순환(수집 → 수정 → 검토 → 초안 PR → 병합 추적)이 그대로 동작한다.
3. 성공이면 `phases/13-selfhost-only/index.json` 의 step 3 만 `completed` 로 바꾸고 `summary` 에 생성·수정·삭제 파일, 결정, 검증 결과(테스트 수), 다음 step 이 주의할 점을 한 줄로 남긴다.
4. 3회 수정 후에도 실패하면 `error` + `error_message`, 사용자 결정·외부 자원이 필요하면 `blocked` + `blocked_reason` 을 기록한다. 실행하지 않은 검증을 `completed` 로 표시하지 않는다.

## 금지사항

- 실제 GitHub(api.github.com·github.com)·Discord·Jira 를 호출하지 않는다(공식 문서 읽기는 허용). 이유: 모든 step 은 httpx `MockTransport`·가짜 GitHub·임시 bare 저장소로 검증한다. 실연동은 phase 뒤 사용자와 함께 한다.
- 사용자가 띄워 둔 셀프호스트(compose 프로젝트 `runloom`, 포트 8000, 볼륨 `runloom_workflow-data`)·`deploy/selfhost/.env`·러너 홈(`~/Library/Application Support/workflow-connector/`)·launchd·`/Users/kje/demo/*`(OpenArchive·runloom-sandbox 클론)를 읽거나 바꾸지 않는다. 컨테이너를 띄우거나 멈추지 않는다. 이유: 실사용 중인 환경이다.
- `main` 브랜치·공개 데모 VM 을 바꾸지 않는다. 이유: 공개 데모 원본(심사 ~2026-10-05 동결)이며 demo 코드의 원본이 거기 남아야 한다.
- 셀프호스트가 쓰는 것(`session_agents`, `sessions.is_operator`, n8n 입구·`chains`, `code_change_result` 산출물 종류, `deploy/selfhost/*`)을 지우지 않는다. 이유: README "남기는 것" — 지우면 실사용이 깨진다.
- 사용자 변경을 삭제하거나 stash·reset 하지 않는다. 커밋은 하네스가 한다.
- 이 step 범위 밖 모듈을 "개선"하지 않는다. 기존 테스트의 단정을 약하게 만들어 통과시키지 않는다.
