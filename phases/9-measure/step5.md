# Step 5: 워커 — 착수 가능·대기 기록과 설정 번호

## 읽어야 할 파일

- AGENTS.md
- phases/9-measure/README.md (계획 기본값·지표 정의 — 이 phase 의 기준)
- phases/9-measure/index.json (완료 step 의 summary)
- docs/adr/0015-measurement-events-and-baseline.md (step 0 산출물. step 0 에서는 새로 만든다)
- docs/ARCHITECTURE.md ("측정 — phase 9" 절. step 0 에서는 새로 만든다)
- docs/GLOSSARY.md
- src/workflow/server/worker.py (`_write_blocked`, `_create_cycle_execution`, 후속 생성의 `rules_revision`)
- src/workflow/server/task_cycle.py, src/workflow/domain/task_readiness.py (`Blocker.code`·`actor`)
- src/workflow/adapters/repo.py (step 4 산출물 `append_task_event`)
- tests/workflow/server/ (워커 테스트)

먼저 실제 파일과 이전 step 산출물을 읽는다. 이름·칸·함수 시그니처는 step 0 의 ADR-0015·ARCHITECTURE 가 고정한 것을 따르고, 바꿔야 하면 같은 이름의 문서·계약·테스트를 함께 갱신한다. 대화 이력을 전제로 판단하지 않는다.

## 작업

`server/worker.py` 만 바꾼다(필요하면 `task_cycle.py` 에 작은 조회 함수).

1. 준비 판정이 막혔을 때: 대기 코드 집합(정렬된 `(code, actor)` 목록)이 이 Task 의 직전 `blocked` 이벤트와 다르거나, 직전 이벤트가 `ready` 이면 `blocked` 이벤트를 추가한다. 같은 사유로 매 주기 반복 기록하지 않는다.
2. 준비 판정이 통과해 실행을 만들 때: 같은 트랜잭션에서 `ready` 이벤트를 추가한다(직전이 `ready` 이고 그 뒤 실행이 없으면 추가하지 않는다).
3. 후속 생성의 `"rules_revision": 1` 고정을 없애고 세션의 현재 `config_revision` 을 쓴다.
4. 기존 동작(착수 순서·중복 방지·사람 요청 생성)은 바꾸지 않는다. 이벤트 기록 실패가 판정 결과를 바꾸지 않도록 같은 트랜잭션 규칙을 지킨다.

## 테스트 먼저

워커 테스트: 막힘 → 같은 사유 반복 주기(이벤트 1개) → 사유 변경(이벤트 추가) → 통과(ready 1개 + 실행 생성), 재시작 후 같은 상태에서 중복 기록 없음, 후속 링크의 `rules_revision` 이 설정 변경 뒤 증가한 번호와 같음, 기존 phase 8 워커 테스트 회귀 통과.

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
3. 성공이면 `phases/9-measure/index.json` 의 step 5 만 `completed` 로 바꾸고 `summary` 에 생성·수정 파일, 결정, 검증 결과(테스트 수), 다음 step 이 주의할 점을 한 줄로 남긴다.
4. 3회 수정 후에도 실패하면 `error` + `error_message`, 사용자 결정·외부 자격이 필요하면 `blocked` + `blocked_reason` 을 기록한다. 실행하지 않은 검증을 `completed` 로 표시하지 않는다.

## 금지사항

- `main`·공개 데모 VM 을 변경·배포하지 않는다. 이유: 이 phase 는 `service` 기반 실서비스 작업이다.
- 실제 GitHub·유료 모델을 호출하지 않는다. 이유: 모든 step 은 대역(httpx `MockTransport`, fake 도구)으로 검증한다. 실제 기준선 가져오기는 phase 뒤 사용자 지시로 한다.
- 사용자 변경을 삭제하거나 stash·reset 하지 않는다. 커밋은 하네스가 한다.
- 이 step 범위 밖 모듈을 "개선"하지 않는다. 이유: step 마다 한 레이어만 바꿔 회귀 원인을 좁힌다.
- 모르는 비용·시각을 0 이나 현재 시각으로 채우지 않는다. 이유: "모름"과 0 은 다른 사실이다.
