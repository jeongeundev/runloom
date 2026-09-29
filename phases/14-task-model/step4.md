# Step 4: succession-placement — 후속을 같은 업무 단계/새 업무로, 원본 찾기를 업무에서

## 읽어야 할 파일

- AGENTS.md
- phases/14-task-model/README.md, phases/14-task-model/index.json (step 0~3 summary)
- docs/adr/0020-work-items-and-stages.md, docs/adr/0009-registered-kinds-and-succession-rules.md
- docs/ARCHITECTURE.md ("업무와 단계 — phase 14" 의 placement 표, "GitHub 업무 순환 — phase 8 계약" 의 후속 결정 표·중복 키)
- src/workflow/contracts/v1.py (`SuccessorRule`, `BUILTIN_RULES`), src/workflow/domain/succession.py, src/workflow/domain/task_followup.py, src/workflow/domain/kinds.py
- src/workflow/server/worker.py (`_advance_cycle`, `_create_followup_task`, `_spawn_successors`), src/workflow/server/task_cycle.py (`origin_source`, `source_match`), src/workflow/adapters/repo.py (`create_followup_once`)
- src/workflow/server/web.py (규칙 등록 화면 `/rules`·kinds 화면), templates kinds.html
- tests: tests/workflow/contracts/test_v1.py, tests/workflow/domain/test_succession.py, test_task_followup.py, tests/workflow/server/test_worker.py, test_task_cycle.py

## 작업

1. 계약: `SuccessorRule.placement: Literal["same_work", "new_work"] = "same_work"`. 저장된 옛 규칙 JSON(칸 없음)도 그대로 읽힌다. 내장 규칙은 `same_work`. 규칙 등록 화면·API 가 이 칸을 받고 보여 준다("같은 업무의 다음 단계" / "새 업무로 등록").
2. 워커 후속 생성: `same_work` → 새 단계 Task 를 원인 Task 의 업무에 붙인다. `new_work` → 새 업무를 만들고(`create_work_item`, 제목은 ARCHITECTURE 규칙, 원본은 `manual` 아닌 "원인 업무에서 이어짐" 표시 — step 0 이 정한 값) `work_item_links(type='spawned_from', cause_execution_id)` 로 잇고 그 업무의 첫 단계로 Task 를 만든다. `followup_links` 유일키(원인 실행·종류)는 그대로 — 두 경우 모두 중복 생성 없음. 비순환 종류 경로(`_spawn_successors`)도 같은 규칙.
3. 후속·재작업·검토 생성 뒤 해당 업무(들)에 `refresh_work_status` 를 같은 트랜잭션에서 부른다.
4. 원본 찾기: `task_cycle.origin_source` 가 선행 사슬 대신 `work_item_of_task` → 업무의 원본 칸으로 찾는다. 결과가 지금과 같아야 한다(검토 Task 도 fix 의 원본 이슈를 찾는다). 선행 사슬을 따라가는 다른 곳(views 선행/후속 칩 등)은 이 step 에서 건드리지 않는다 — step 9 가 화면을 본다.
5. 종류 이름으로 분기하지 않는다(ADR-0009, AGENTS.md).

## 테스트 먼저

- contracts: `placement` 기본값·옛 JSON 읽기·잘못된 값 422.
- worker: 내장 규칙으로 fix → review 가 같은 업무(업무 1, 단계 2), 사용자 정의 `new_work` 규칙으로 새 업무 + `spawned_from` 링크 1개, 같은 원인 재평가 시 중복 없음, 업무 상태 갱신.
- task_cycle: 검토 Task·재작업 뒤 `origin_source` 결과가 이전과 같음.

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
3. 성공이면 `phases/14-task-model/index.json` 의 step 4 만 `completed` 로 바꾸고 `summary` 에 생성·수정·삭제 파일, 결정, 검증 결과(테스트 수), 다음 step 이 주의할 점을 한 줄로 남긴다.
4. 3회 수정 후에도 실패하면 `error` + `error_message`, 사용자 결정·외부 자원이 필요하면 `blocked` + `blocked_reason` 을 기록한다. 실행하지 않은 검증을 `completed` 로 표시하지 않는다.

## 금지사항

- 실제 GitHub(api.github.com·github.com)·Discord·Jira 를 호출하지 않는다(공식 문서 읽기는 허용). 이유: 모든 step 은 httpx `MockTransport`·가짜 GitHub·임시 bare 저장소로 검증한다. 실연동은 phase 뒤 사용자와 함께 한다.
- 사용자가 띄워 둔 셀프호스트(compose 프로젝트 `runloom`, 포트 8000, 볼륨 `runloom_workflow-data`)·`deploy/selfhost/.env`·러너 홈(`~/Library/Application Support/workflow-connector/`)·launchd·`/Users/kje/demo/*`(OpenArchive·runloom-sandbox 클론)를 읽거나 바꾸지 않는다. 컨테이너를 띄우거나 멈추지 않는다. 이유: 실사용 중인 환경이다.
- 종류 이름(`bug_fix`·`code_review`)으로 새 분기를 만들지 않는다. 이유: 업무 종류·후속 규칙은 워크스페이스 등록 데이터다(ADR-0009, AGENTS.md).
- `tasks` 테이블을 재생성하거나 이름을 바꾸지 않는다. 이유: ADR-0020 결정 — 업무 상태는 `work_items` 에 두고 단계 상태는 그대로 둔다.
- 새 화면 구성(담당자별 묶음·보드·상세 패널·연결 화면)을 만들지 않는다. 이유: 16-work-ui 범위. 이 phase 의 화면 변경은 step 9 최소 변경뿐이다.
- 사용자 변경을 삭제하거나 stash·reset 하지 않는다. 커밋은 하네스가 한다.
- 이 step 범위 밖 모듈을 "개선"하지 않는다. 기존 테스트의 단정을 약하게 만들어 통과시키지 않는다.
