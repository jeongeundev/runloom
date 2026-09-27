# Step 13: 재작업 상한 요청 중복 결함 수정

## 읽어야 할 파일

- AGENTS.md
- phases/9-measure/README.md (계획 기본값·지표 정의 — 이 phase 의 기준)
- phases/9-measure/index.json (완료 step 의 summary)
- docs/adr/0015-measurement-events-and-baseline.md (step 0 산출물. step 0 에서는 새로 만든다)
- docs/ARCHITECTURE.md ("측정 — phase 9" 절. step 0 에서는 새로 만든다)
- docs/GLOSSARY.md
- src/workflow/domain/task_followup.py (`_after_review`, `decide_followup`, `handled_cause_keys`)
- src/workflow/server/worker.py (`rounds_used` 계산, `handled_cause_keys` 를 채우는 곳)
- tests/workflow/domain/test_task_followup.py, tests/e2e/test_metrics.py (요청 수 단정과 주석)
- docs/CURRENT_HANDOFF.md ("phase 9 e2e 에서 발견한 결함" 단락)

먼저 실제 파일과 이전 step 산출물을 읽는다. 이름·칸·함수 시그니처는 step 0 의 ADR-0015·ARCHITECTURE 가 고정한 것을 따르고, 바꿔야 하면 같은 이름의 문서·계약·테스트를 함께 갱신한다. 대화 이력을 전제로 판단하지 않는다.

## 작업

결함(phase 9 e2e 에서 발견): 재작업 상한 1 에서 수정 요청 검토가 재작업을 시작시킨 뒤, 재작업 결과가 판정되기 전 tick 이 같은 검토를 다시 평가하면 `_after_review` 가 `rounds_used(1) >= 상한(1)` 으로 `rework_limit_reached` 사람 요청을 하나 더 만든다. 이 검토가 이미 재작업(`rework:{검토 실행 id}`)을 일으켰는지 보지 않기 때문이다. 요청이 열린 채 남고 사람 개입 지표가 부풀려진다.

1. 먼저 도메인 테스트로 재현한다: `handled_cause_keys` 에 `rework:{ctx.execution_id}` 가 있고 `rounds_used == max_rework_rounds` 인 검토 결과 → 지금은 `rework_limit_reached` 요청이 나온다(실패 테스트).
2. 수정: 이 검토 실행이 이미 재작업을 일으켰다면(그 cause key 가 처리됨) 상한 판단보다 먼저 `none`("이미 재작업을 시작한 검토") 을 낸다. 상한 도달 판정은 아직 재작업을 일으키지 않은 새 `changes_requested` 검토에만 적용된다.
3. 워커가 `handled_cause_keys` 에 해당 Task 의 `rework:` 시작 키를 실제로 넣는지 확인하고, 안 넣으면 넣는다(워커 테스트 추가).
4. `tests/e2e/test_metrics.py` 의 요청 수 단정을 결함이 없는 값으로 고치고 결함 주석을 지운다. `docs/CURRENT_HANDOFF.md` 의 결함 단락을 "수정됨(step 13)" 으로 바꾼다.

## 테스트 먼저

- `test_task_followup.py`: 위 재현 사례, 상한 미만 재작업 정상, 재작업을 일으키지 않은 새 수정 요청 검토는 상한 도달 요청, 오래된 검토(stale_review) 기존 동작.
- 워커 테스트: 재작업 시작 뒤 판정 전 tick 반복에서 사람 요청이 늘지 않음.
- `WORKFLOW_E2E=1 python3 -m pytest tests/e2e -q` 통과.

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
3. 성공이면 `phases/9-measure/index.json` 의 step 13 만 `completed` 로 바꾸고 `summary` 에 생성·수정 파일, 결정, 검증 결과(테스트 수), 다음 step 이 주의할 점을 한 줄로 남긴다.
4. 3회 수정 후에도 실패하면 `error` + `error_message`, 사용자 결정·외부 자격이 필요하면 `blocked` + `blocked_reason` 을 기록한다. 실행하지 않은 검증을 `completed` 로 표시하지 않는다.

## 금지사항

- `main`·공개 데모 VM 을 변경·배포하지 않는다. 이유: 이 phase 는 `service` 기반 실서비스 작업이다.
- 실제 GitHub·유료 모델을 호출하지 않는다. 이유: 모든 step 은 대역(httpx `MockTransport`, fake 도구)으로 검증한다. 실제 기준선 가져오기는 phase 뒤 사용자 지시로 한다.
- 사용자 변경을 삭제하거나 stash·reset 하지 않는다. 커밋은 하네스가 한다.
- 이 step 범위 밖 모듈을 "개선"하지 않는다. 이유: step 마다 한 레이어만 바꿔 회귀 원인을 좁힌다.
- 모르는 비용·시각을 0 이나 현재 시각으로 채우지 않는다. 이유: "모름"과 0 은 다른 사실이다.
