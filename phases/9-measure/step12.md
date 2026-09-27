# Step 12: 완료 = GitHub 병합 시각 — 동기화·지표·화면 연결

## 읽어야 할 파일

- AGENTS.md
- phases/9-measure/README.md (계획 기본값·지표 정의 — 이 phase 의 기준)
- phases/9-measure/index.json (완료 step 의 summary)
- docs/adr/0015-measurement-events-and-baseline.md (step 0 산출물. step 0 에서는 새로 만든다)
- docs/ARCHITECTURE.md ("측정 — phase 9" 절. step 0 에서는 새로 만든다)
- docs/GLOSSARY.md
- index.json 의 step 11 summary
- src/workflow/server/github_sync.py (`sync_source`, `_poll`, `_Intake.take`)
- src/workflow/server/metrics_api.py, src/workflow/adapters/repo.py (`list_metric_facts`)
- src/workflow/domain/metrics.py (`TaskFact.merge_confirmed_at`, 접수 → 완료 계산)
- src/workflow/server/views.py, src/workflow/server/templates/ (지표 화면)
- tests/e2e/test_metrics.py, tests/e2e/test_github_cycle.py (FakeGitHub)

먼저 실제 파일과 이전 step 산출물을 읽는다. 이름·칸·함수 시그니처는 step 0 의 ADR-0015·ARCHITECTURE 가 고정한 것을 따르고, 바꿔야 하면 같은 이름의 문서·계약·테스트를 함께 갱신한다. 대화 이력을 전제로 판단하지 않는다.

## 작업

1. 동기화: `sync_source` 가 이슈 수집 뒤 `list_issues_needing_merge_check` 행마다 `get_issue_pr_link` 를 불러 `record_issue_merge` 로 저장한다. 한 번의 동기화에서 조회 수에 상한(예: 20)을 두고, 실패는 `SyncReport` 에 남기되 이슈 수집 결과를 되돌리지 않는다. 병합 PR 이 없다고 확인된 행은 `merge_checked_at` 을 남겨 매 주기 다시 조회하지 않게 하되, 이슈가 다시 바뀌면(`issue_updated_at` 이 `merge_checked_at` 보다 나중) 다시 조회한다.
2. 지표 원천: 묶음 시작 Task 의 완료 시각 = `source_issues.pr_merged_at`. 순서는 병합 시각 → (GitHub 이슈가 아닌 직접 등록 Task 만) 기존 규칙(`merge_confirmed_at`, `완료` 전환, v6 이전 `finished_at`). **GitHub 이슈 묶음에는 Runloom 승인 시각을 완료로 쓰지 않는다** — 병합 전이면 미완료로 센다. 이슈가 병합 없이 닫히면 "병합 없이 닫힘" 건수로 따로 센다.
3. 기존 "승인 시각"은 버리지 않고 별도 지표 "접수 → 승인"(묶음에서 처음 운영자 승인 또는 `완료` 로 바뀐 시각)으로 낸다. JSON·CSV·화면에 추가한다.
4. 화면 맨 위 비교(기준선 대 도입 후)는 "이슈 열림 → 병합" 끼리만 나란히 놓는다. 승인 지표는 아래 속도 표에.
5. e2e(`tests/e2e/test_metrics.py`): FakeGitHub 에 이슈 닫힘 + 병합 PR 을 추가해, 운영자 승인 직후에는 접수 → 완료가 미완료 1 이고 GitHub 에서 병합·닫힌 뒤 동기화하면 완료 n 1(병합 시각 기준)이 되는지 확인한다. 기존의 "status_changed → 완료 로 잰다" 단정은 새 규칙에 맞게 바꾼다.

## 테스트 먼저

- `tests/workflow/server/` github_sync 테스트: 닫힌 이슈 병합 조회·저장, 상한, 조회 실패가 수집을 되돌리지 않음, 병합 없음 재조회 조건.
- `tests/workflow/domain/test_metrics.py`: GitHub 묶음은 승인만으로 완료 아님, 병합 시각으로 완료, 병합 없이 닫힘 별도 집계, 직접 등록 Task 는 기존 규칙, 접수 → 승인 지표.
- 지표 API·화면 테스트 갱신, e2e 위 5번.

소스 변경 전에 `tests/` 미러 경로에 실패 테스트를 작성하고 실패 원인이 의도한 것인지 확인한다(`tdd-guard.sh` 가 테스트 없는 소스 작성을 막는다). 구현 후 해당 테스트와 전체 회귀를 통과시킨다.

## Acceptance Criteria

```bash
python3 -m pytest -q
python3 -m ruff check .
WORKFLOW_E2E=1 python3 -m pytest tests/e2e -q
```

위 커맨드와 이 step 의 테스트 항목을 모두 확인한다.

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 규칙을 확인한다: `domain/` 은 FastAPI·sqlite3·HTTPX·subprocess·Git 을 import 하지 않는다 / `server/` 와 `connector/` 는 서로 import 하지 않고 `contracts/` 만 공유한다 / 비밀값(`WORKFLOW_GITHUB_TOKEN`, 연결 토큰 등)은 환경변수에서만 읽고 DB·로그·응답·템플릿에 넣지 않는다 / 이름은 GLOSSARY 식별자를 쓴다.
3. 성공이면 `phases/9-measure/index.json` 의 step 12 만 `completed` 로 바꾸고 `summary` 에 생성·수정 파일, 결정, 검증 결과(테스트 수), 다음 step 이 주의할 점을 한 줄로 남긴다.
4. 3회 수정 후에도 실패하면 `error` + `error_message`, 사용자 결정·외부 자격이 필요하면 `blocked` + `blocked_reason` 을 기록한다. 실행하지 않은 검증을 `completed` 로 표시하지 않는다.

## 금지사항

- `main`·공개 데모 VM 을 변경·배포하지 않는다. 이유: 이 phase 는 `service` 기반 실서비스 작업이다.
- 실제 GitHub·유료 모델을 호출하지 않는다. 이유: 모든 step 은 대역(httpx `MockTransport`, fake 도구)으로 검증한다. 실제 기준선 가져오기는 phase 뒤 사용자 지시로 한다.
- 사용자 변경을 삭제하거나 stash·reset 하지 않는다. 커밋은 하네스가 한다.
- 이 step 범위 밖 모듈을 "개선"하지 않는다. 이유: step 마다 한 레이어만 바꿔 회귀 원인을 좁힌다.
- 모르는 비용·시각을 0 이나 현재 시각으로 채우지 않는다. 이유: "모름"과 0 은 다른 사실이다.
- 실제 GitHub 를 호출하지 않는다. FakeGitHub·MockTransport 만.
