# Step 0: 측정 설계 고정 — ADR·ARCHITECTURE·CONTRACT·GLOSSARY

## 읽어야 할 파일

- AGENTS.md
- phases/9-measure/README.md (계획 기본값·지표 정의 — 이 phase 의 기준)
- phases/9-measure/index.json (완료 step 의 summary)
- docs/adr/0015-measurement-events-and-baseline.md (step 0 산출물. step 0 에서는 새로 만든다)
- docs/ARCHITECTURE.md ("측정 — phase 9" 절. step 0 에서는 새로 만든다)
- docs/GLOSSARY.md
- docs/product/MVP_PLAN.md (6절 측정 설계, 10절 범위)
- docs/product/ROADMAP.md (9절 지표)
- docs/CONTRACT.md (실행 이벤트 절, 13절 GitHub — `json` 과 `json contract-pending` 펜스 규칙)
- docs/adr/0009-registered-kinds-and-succession-rules.md, docs/adr/0014-github-task-cycle.md
- src/workflow/adapters/db.py, src/workflow/adapters/repo.py (상태 변경 함수들)
- src/workflow/contracts/v1.py (`StartedData`·`ResultReadyData`·`FailedData`·`_Contract`)
- src/workflow/server/worker.py (`rules_revision` 1 고정 위치, `_write_blocked`, `_create_cycle_execution`)
- src/workflow/connector/claude.py, src/workflow/connector/codex.py (CLI 결과에서 비용·토큰을 얻을 수 있는지)
- tests/workflow/contracts/test_v1.py (CONTRACT.md 블록 검사 방식)

먼저 실제 파일과 이전 step 산출물을 읽는다. 이름·칸·함수 시그니처는 step 0 의 ADR-0015·ARCHITECTURE 가 고정한 것을 따르고, 바꿔야 하면 같은 이름의 문서·계약·테스트를 함께 갱신한다. 대화 이력을 전제로 판단하지 않는다.

## 작업

문서만 바꾼다. 제품 코드는 바꾸지 않는다. README "계획 기본값"과 "지표 정의"를 결정 기록으로 옮기고, 이후 step 이 쓸 이름을 고정한다.

1. `docs/adr/0015-measurement-events-and-baseline.md` 신설 (기존 ADR 형식을 따른다). 결정: 버전 = `sessions.config_revision` + 러너 폴더 커밋 / 비용·토큰 선택 칸 `usage` / 계약 v1 유지·추가형 / 추가 전용 `task_events`(`status_changed`·`blocked`·`ready` 만) / v5→v6 데이터 보존 마이그레이션 / 기준선 = 이슈 → 연결된 병합 PR, 도입 전 = 소스 연결 시각 이전 / 지표는 순수 도메인 계산, 중앙값·n·미완료. 대안과 기각 이유(러너 폴더 무시, 비용 제외, PR→병합 기준선, 별도 이벤트 저장소)를 적는다.
2. `docs/ARCHITECTURE.md` 에 "측정 — phase 9" 절 추가:
   - 간극 표 (README 표를 코드 위치와 함께).
   - v6 스키마 표: `task_events`(id, task_id, session_id, type CHECK('status_changed','blocked','ready'), task_revision, config_revision, occurred_at, data_json), `sessions.config_revision`, `executions` 새 칸(`config_revision`, `folder_commit`, `folder_dirty`, `cost_usd`, `input_tokens`, `output_tokens` — 모두 NULL 허용, NULL = 모름), 기준선 테이블 `baseline_items`(source_id, issue_number, issue_title, issue_opened_at, pr_number, pr_merged_at, fetched_at; PK source_id+issue_number+pr_number)와 `baseline_imports`(source_id PK, opened_before, fetched_at, item_count).
   - 이벤트 기록 규칙 표: 무엇을 어디서(어느 repo 함수·워커 지점) 같은 트랜잭션으로 쓰는가, 중복 방지 조건(`blocked` 는 대기 코드 집합이 바뀔 때만, `ready` 는 직전이 `ready` 가 아닐 때만, `status_changed` 는 값이 바뀔 때만).
   - 설정 번호를 올리는 작업 목록(종류 추가·삭제, 규칙 추가·삭제, GitHub 소스 설정 생성·변경)과 올리지 않는 작업(담당자 연결, 에이전트 등록 등).
   - 지표 정의 표 (README 표 + 각 지표의 원천 칸·미완료 처리·"모름" 처리).
   - 이름 고정: 계약 `ExecutionUsage`(cost_usd, input_tokens, output_tokens — 모두 선택), `StartedData.folder_commit`·`folder_dirty`, `ResultReadyData.usage`·`FailedData.usage`; 도메인 `workflow.domain.metrics` 의 입력 값 객체와 `compute_metrics(...)`·`summarize_baseline(...)`; GitHub 어댑터 `list_issue_pr_links(...)`; 계약 `contracts/github.py` 의 `IssuePrLink`; repo `append_task_event`·`bump_config_revision`·`replace_baseline`·`list_metric_facts` 등. 시그니처 수준만 적는다.
   - API·화면: `GET /metrics`(화면), `GET /metrics.json`, `GET /metrics.csv`, `POST /operator/github/sources/{source_id}/baseline` — 인증은 기존 운영자 GitHub 화면(`/operator/github`)과 같은 규칙을 따른다고 적고, 코드에서 그 규칙을 확인해 구체화한다.
3. `docs/CONTRACT.md` 실행 이벤트 절에 `started`(folder_commit 포함)·`result_ready`(usage 포함)·`failed`(usage 일부 null) 예시를 `json contract-pending` 펜스로 추가한다(step 1 이 `json` 으로 바꾼다). 기존 블록은 건드리지 않는다.
4. `docs/GLOSSARY.md`: 업무 이벤트(`TaskEvent`/`task_events`), 설정 번호(`config_revision`), 러너 폴더 커밋(`folder_commit`), 실행 사용량(`ExecutionUsage`), 업무 묶음, 기준선(`baseline_items`) — 화면 라벨과 코드 식별자를 함께.
5. Claude·Codex CLI 결과에서 비용·토큰을 어떤 키로 얻는지 코드와 테스트 fixture 로 확인해 ARCHITECTURE 에 적는다. 확인할 수 없는 값은 "모름(null)"으로 둔다고 적는다.

## 테스트 먼저

문서 전용 step 이라 제품 테스트를 새로 만들지 않는다. 기존 문서·계약 검사(CONTRACT.md 블록 수 검사 포함)가 그대로 통과해야 한다. `json contract-pending` 블록이 기존 검사에서 제외되는지 확인한다.

소스 변경 전에 `tests/` 미러 경로에 실패 테스트를 작성하고 실패 원인이 의도한 것인지 확인한다(`tdd-guard.sh` 가 테스트 없는 소스 작성을 막는다). 구현 후 해당 테스트와 전체 회귀를 통과시킨다.

## Acceptance Criteria

```bash
python3 -m pytest -q
python3 -m ruff check .
```

위 커맨드와 이 step 의 테스트 항목을 모두 확인한다.

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 규칙을 확인한다: `domain/` 은 FastAPI·sqlite3·HTTPX·subprocess·Git 을 import 하지 않는다 / `server/` 와 `connector/` 는 서로 import 하지 않고 `contracts/` 만 공유한다 / 비밀값(`WORKFLOW_GITHUB_TOKEN`, 연결 토큰 등)은 환경변수에서만 읽고 DB·로그·응답·템플릿에 넣지 않는다 / 이름은 GLOSSARY 식별자를 쓴다.
3. 성공이면 `phases/9-measure/index.json` 의 step 0 만 `completed` 로 바꾸고 `summary` 에 생성·수정 파일, 결정, 검증 결과(테스트 수), 다음 step 이 주의할 점을 한 줄로 남긴다.
4. 3회 수정 후에도 실패하면 `error` + `error_message`, 사용자 결정·외부 자격이 필요하면 `blocked` + `blocked_reason` 을 기록한다. 실행하지 않은 검증을 `completed` 로 표시하지 않는다.

## 금지사항

- `main`·공개 데모 VM 을 변경·배포하지 않는다. 이유: 이 phase 는 `service` 기반 실서비스 작업이다.
- 실제 GitHub·유료 모델을 호출하지 않는다. 이유: 모든 step 은 대역(httpx `MockTransport`, fake 도구)으로 검증한다. 실제 기준선 가져오기는 phase 뒤 사용자 지시로 한다.
- 사용자 변경을 삭제하거나 stash·reset 하지 않는다. 커밋은 하네스가 한다.
- 이 step 범위 밖 모듈을 "개선"하지 않는다. 이유: step 마다 한 레이어만 바꿔 회귀 원인을 좁힌다.
- 모르는 비용·시각을 0 이나 현재 시각으로 채우지 않는다. 이유: "모름"과 0 은 다른 사실이다.
