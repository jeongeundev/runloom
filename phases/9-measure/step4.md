# Step 4: 저장 — 상태 이벤트·설정 번호·실행 사용량

## 읽어야 할 파일

- AGENTS.md
- phases/9-measure/README.md (계획 기본값·지표 정의 — 이 phase 의 기준)
- phases/9-measure/index.json (완료 step 의 summary)
- docs/adr/0015-measurement-events-and-baseline.md (step 0 산출물. step 0 에서는 새로 만든다)
- docs/ARCHITECTURE.md ("측정 — phase 9" 절. step 0 에서는 새로 만든다)
- docs/GLOSSARY.md
- src/workflow/adapters/db.py (step 3 산출물)
- src/workflow/adapters/repo.py (상태 변경 함수 전부: `update_task_status`·`finish_task`·`record_verdict`·사람 응답으로 `실패` 로 바꾸는 곳 등, 종류·규칙·GitHub 소스 저장, 실행 생성, 실행 이벤트 반영)
- src/workflow/server/machine_api.py (실행 이벤트 수신 → repo 호출 경로)
- tests/workflow/adapters/test_repo.py, tests/workflow/server/ (machine API 테스트)

먼저 실제 파일과 이전 step 산출물을 읽는다. 이름·칸·함수 시그니처는 step 0 의 ADR-0015·ARCHITECTURE 가 고정한 것을 따르고, 바꿔야 하면 같은 이름의 문서·계약·테스트를 함께 갱신한다. 대화 이력을 전제로 판단하지 않는다.

## 작업

`adapters/repo.py` 를 중심으로, 실행 이벤트 수신 경로(`server/machine_api.py`)는 새 칸을 repo 에 넘기는 데까지만 바꾼다.

1. `append_task_event(conn, *, task_id, type, data, now)` — `session_id`·`task_revision`·현재 `config_revision` 을 DB 에서 읽어 채운다. 호출자의 트랜잭션 안에서 쓴다.
2. Task 상태를 바꾸는 모든 repo 함수: 이전 상태를 읽고, 실제로 달라졌을 때만 같은 트랜잭션에서 `status_changed`(from, to, reason) 를 추가한다. `UPDATE tasks SET status` 가 있는 곳을 `grep` 으로 모두 찾아 빠짐없이 적용한다.
3. `bump_config_revision(conn, session_id)` — 종류 추가·삭제, 후속 규칙 추가·삭제, GitHub 소스 설정 생성·변경을 저장하는 repo 함수가 같은 트랜잭션에서 호출한다. ARCHITECTURE 의 "올리지 않는 작업"은 호출하지 않는다.
4. 실행 생성 repo 함수는 그 순간의 `sessions.config_revision` 을 `executions.config_revision` 에 같은 트랜잭션으로 찍는다(생성 경로가 여러 개면 모두).
5. 실행 이벤트 반영: `started` 의 `folder_commit`·`folder_dirty`, `result_ready`·`failed` 의 `usage` 를 `executions` 새 칸에 저장한다. 같은 seq 재전송(멱등)은 값을 바꾸지 않는다. 칸이 없으면 NULL 유지.
6. `followup_links` 를 만드는 repo 함수가 `rules_revision` 을 받는 대신 세션의 현재 `config_revision` 을 쓸 수 있게 한다(워커 쪽 1 고정 제거는 step 5).

## 테스트 먼저

`tests/workflow/adapters/test_repo.py`: 상태 변경마다 `status_changed` 1행(같은 상태로 다시 쓰면 0행), 트랜잭션 실패 시 이벤트도 없음, 종류·규칙·소스 변경마다 `config_revision` +1 과 담당자 연결은 불변, 실행 생성 시 번호 기록, 이벤트의 커밋·사용량 저장과 재전송 멱등, 다른 세션 행에 영향 없음.
서버 테스트: 새 칸이 있는/없는 이벤트 POST 가 모두 200 이고 저장값이 맞는지.

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
3. 성공이면 `phases/9-measure/index.json` 의 step 4 만 `completed` 로 바꾸고 `summary` 에 생성·수정 파일, 결정, 검증 결과(테스트 수), 다음 step 이 주의할 점을 한 줄로 남긴다.
4. 3회 수정 후에도 실패하면 `error` + `error_message`, 사용자 결정·외부 자격이 필요하면 `blocked` + `blocked_reason` 을 기록한다. 실행하지 않은 검증을 `completed` 로 표시하지 않는다.

## 금지사항

- `main`·공개 데모 VM 을 변경·배포하지 않는다. 이유: 이 phase 는 `service` 기반 실서비스 작업이다.
- 실제 GitHub·유료 모델을 호출하지 않는다. 이유: 모든 step 은 대역(httpx `MockTransport`, fake 도구)으로 검증한다. 실제 기준선 가져오기는 phase 뒤 사용자 지시로 한다.
- 사용자 변경을 삭제하거나 stash·reset 하지 않는다. 커밋은 하네스가 한다.
- 이 step 범위 밖 모듈을 "개선"하지 않는다. 이유: step 마다 한 레이어만 바꿔 회귀 원인을 좁힌다.
- 모르는 비용·시각을 0 이나 현재 시각으로 채우지 않는다. 이유: "모름"과 0 은 다른 사실이다.
