# Step 6: work-status-worker — 워커가 업무 상태를 기록, 실패 → 내 차례 · 다시 맡기기/닫기

## 읽어야 할 파일

- AGENTS.md
- phases/14-task-model/README.md, phases/14-task-model/index.json (step 0~5 summary)
- docs/adr/0020-work-items-and-stages.md, docs/ARCHITECTURE.md ("업무와 단계 — phase 14" 의 업무 상태 표·실패 처리)
- src/workflow/server/worker.py (`_write_status`, `_refresh_task`, `record_verdict` 호출부, `_reflect_failures`, `_apply_pull_request_state`, `_resume`), src/workflow/server/human_api.py (`_ACTIONS`, action 목록), src/workflow/adapters/repo.py (`create_human_request_once`, 응답 처리 `close`, `refresh_work_status`)
- src/workflow/domain/work_status.py, src/workflow/domain/notification.py
- tests: tests/workflow/server/test_worker.py, test_human_api.py, test_task_cycle.py

## 작업

1. 단계 상태를 쓰는 모든 워커·웹 경로(상태 기록, 판정, 마감, PR 상태 반영, 사람 응답, 원본 닫힘 대기)가 같은 트랜잭션에서 그 단계의 업무에 `refresh_work_status` 를 부른다. 호출을 흩뿌리지 말고 repo 의 단계 상태 기록 함수 한 곳(예: `_write_status`/`finish_task`/`record_verdict` 가 공유하는 내부 함수)에서 부르게 한다.
2. 실패: `_reflect_failures` 가 단계를 `실패` 로 마감한 뒤 사람 요청 `stage_failed`(대상 = 실패한 단계, `cause_key` = `failed:<execution_id>` — 중복 없음)를 만든다. 업무 상태는 `내 차례` · "실패 — <코드> · <메시지>". 알림은 지금처럼 `task_failed` 한 번(사람 요청 알림과 겹쳐 두 번 보내지 않는다 — 어느 쪽 하나로 정해 ARCHITECTURE 에 적는다).
3. 응답 action: `stage_failed` 의 허용 action 은 `retry`·`close`. `retry` → 같은 업무에 같은 종류의 새 단계 Task(실패 단계의 요청·대상·능력·선택 복사, `predecessor_task_id` = 실패 단계), 워커가 평소처럼 준비 판정·시작. `close` → 업무 `종료`(업무 마감 시각). 같은 요청에 두 번 응답하면 지금 규칙(낙관적 잠금)대로 거부. action 이름은 contracts/human_api 목록과 GLOSSARY 에 맞춘다.
4. 업무 담당이 비어 있고 에이전트가 단계를 맡으면(자동 매칭·지시) 업무 담당을 그 에이전트로 채운다 — 이미 담당이 있으면 바꾸지 않는다.
5. GitHub 원본 댓글(`github_delivery`) 문구의 업무 표시는 이 step 에서 바꾸지 않는다(step 7 이 키를 넣는다).

## 테스트 먼저

- worker: 실행 시작 → 업무 `에이전트 작업 중`, 검토 대기·사람 요청 → `내 차례`, PR 열림 → `PR · 검토`, 병합 → `완료`, 실패 → `내 차례`·요청 1개(재평가해도 1개), `retry` → 새 단계·업무 `대기`/`에이전트 작업 중`, `close` → `종료`.
- human_api: `stage_failed` 에 `resume` 은 거부, `retry`·`close` 허용.
- 업무 이벤트: 전환마다 1개, 같은 상태 재기록 없음.

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
3. 성공이면 `phases/14-task-model/index.json` 의 step 6 만 `completed` 로 바꾸고 `summary` 에 생성·수정·삭제 파일, 결정, 검증 결과(테스트 수), 다음 step 이 주의할 점을 한 줄로 남긴다.
4. 3회 수정 후에도 실패하면 `error` + `error_message`, 사용자 결정·외부 자원이 필요하면 `blocked` + `blocked_reason` 을 기록한다. 실행하지 않은 검증을 `completed` 로 표시하지 않는다.

## 금지사항

- 실제 GitHub(api.github.com·github.com)·Discord·Jira 를 호출하지 않는다(공식 문서 읽기는 허용). 이유: 모든 step 은 httpx `MockTransport`·가짜 GitHub·임시 bare 저장소로 검증한다. 실연동은 phase 뒤 사용자와 함께 한다.
- 사용자가 띄워 둔 셀프호스트(compose 프로젝트 `runloom`, 포트 8000, 볼륨 `runloom_workflow-data`)·`deploy/selfhost/.env`·러너 홈(`~/Library/Application Support/workflow-connector/`)·launchd·`/Users/kje/demo/*`(OpenArchive·runloom-sandbox 클론)를 읽거나 바꾸지 않는다. 컨테이너를 띄우거나 멈추지 않는다. 이유: 실사용 중인 환경이다.
- 종류 이름(`bug_fix`·`code_review`)으로 새 분기를 만들지 않는다. 이유: 업무 종류·후속 규칙은 워크스페이스 등록 데이터다(ADR-0009, AGENTS.md).
- `tasks` 테이블을 재생성하거나 이름을 바꾸지 않는다. 이유: ADR-0020 결정 — 업무 상태는 `work_items` 에 두고 단계 상태는 그대로 둔다.
- 새 화면 구성(담당자별 묶음·보드·상세 패널·연결 화면)을 만들지 않는다. 이유: 16-work-ui 범위. 이 phase 의 화면 변경은 step 9 최소 변경뿐이다.
- 사용자 변경을 삭제하거나 stash·reset 하지 않는다. 커밋은 하네스가 한다.
- 이 step 범위 밖 모듈을 "개선"하지 않는다. 기존 테스트의 단정을 약하게 만들어 통과시키지 않는다.
