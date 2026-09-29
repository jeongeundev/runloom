# Step 8: metrics-by-work — 지표 묶음 = 업무

## 읽어야 할 파일

- AGENTS.md
- phases/14-task-model/README.md, phases/14-task-model/index.json (step 0~7 summary)
- docs/adr/0015-measurement-events-and-baseline.md, docs/adr/0020-work-items-and-stages.md, docs/ARCHITECTURE.md ("측정 — phase 9" 의 지표 정의, "업무와 단계 — phase 14")
- src/workflow/domain/metrics.py (루트 찾기·묶음, `TaskFact`, 각 지표 함수), src/workflow/adapters/repo.py (`list_metric_facts`), src/workflow/server/metrics_api.py, templates metrics.html
- tests: tests/workflow/domain/test_metrics.py, tests/workflow/server/test_metrics_api.py, test_web_metrics.py, tests/e2e/test_metrics.py

## 작업

1. 묶음 단위를 `predecessor_task_id` 루트에서 **업무**로 바꾼다: 사실 봉투에 `work_item_id` 를 싣고(`list_metric_facts` 가 `tasks.work_item_id` 를 함께 읽음), 묶음 = 같은 업무의 단계들. 체인에서 여러 이슈가 한 묶음으로 합쳐지던 문제가 사라진다 — 기대값이 바뀌는 테스트는 이유를 주석 한 줄로.
2. `intake_to_*` 시작 시각 = 업무 생성 시각(원본이 GitHub 이면 지금처럼 이슈 열린 시각 규칙 유지 — ARCHITECTURE 지표 정의를 그대로 따른다), 끝 = 업무의 `pr_merged_at`/완료 시각.
3. `handoff_wait`(후속 Task 생성 → 첫 시작)는 "같은 업무의 다음 단계 생성 → 그 단계 첫 시작" 으로 정의를 유지한다(단계는 여전히 Task 라 계산식은 거의 같다) — 업무 사이 연결(`blocks`)은 인계로 세지 않는다. ARCHITECTURE 지표 정의 표를 이 뜻으로 고친다.
4. 기준선(`baseline_items`)·실행 지표·`group_by` 는 바꾸지 않는다.
5. `/metrics` 화면의 "묶음" 문구를 "업무" 로.

## 테스트 먼저

- domain: 체인 2업무(선행 있음) → 묶음 2, fix+review+재작업 업무 → 묶음 1·재작업 1·1회 통과 False, 새 업무(`spawned_from`)는 별도 묶음.
- metrics_api·e2e: 기존 기대값 회귀(업무 = 이전 묶음인 경우 같은 값).

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
3. 성공이면 `phases/14-task-model/index.json` 의 step 8 만 `completed` 로 바꾸고 `summary` 에 생성·수정·삭제 파일, 결정, 검증 결과(테스트 수), 다음 step 이 주의할 점을 한 줄로 남긴다.
4. 3회 수정 후에도 실패하면 `error` + `error_message`, 사용자 결정·외부 자원이 필요하면 `blocked` + `blocked_reason` 을 기록한다. 실행하지 않은 검증을 `completed` 로 표시하지 않는다.

## 금지사항

- 실제 GitHub(api.github.com·github.com)·Discord·Jira 를 호출하지 않는다(공식 문서 읽기는 허용). 이유: 모든 step 은 httpx `MockTransport`·가짜 GitHub·임시 bare 저장소로 검증한다. 실연동은 phase 뒤 사용자와 함께 한다.
- 사용자가 띄워 둔 셀프호스트(compose 프로젝트 `runloom`, 포트 8000, 볼륨 `runloom_workflow-data`)·`deploy/selfhost/.env`·러너 홈(`~/Library/Application Support/workflow-connector/`)·launchd·`/Users/kje/demo/*`(OpenArchive·runloom-sandbox 클론)를 읽거나 바꾸지 않는다. 컨테이너를 띄우거나 멈추지 않는다. 이유: 실사용 중인 환경이다.
- 종류 이름(`bug_fix`·`code_review`)으로 새 분기를 만들지 않는다. 이유: 업무 종류·후속 규칙은 워크스페이스 등록 데이터다(ADR-0009, AGENTS.md).
- `tasks` 테이블을 재생성하거나 이름을 바꾸지 않는다. 이유: ADR-0020 결정 — 업무 상태는 `work_items` 에 두고 단계 상태는 그대로 둔다.
- 새 화면 구성(담당자별 묶음·보드·상세 패널·연결 화면)을 만들지 않는다. 이유: 16-work-ui 범위. 이 phase 의 화면 변경은 step 9 최소 변경뿐이다.
- 사용자 변경을 삭제하거나 stash·reset 하지 않는다. 커밋은 하네스가 한다.
- 이 step 범위 밖 모듈을 "개선"하지 않는다. 기존 테스트의 단정을 약하게 만들어 통과시키지 않는다.
