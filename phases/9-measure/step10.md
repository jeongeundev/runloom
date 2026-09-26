# Step 10: e2e 검증과 문서 갱신

## 읽어야 할 파일

- AGENTS.md
- phases/9-measure/README.md (계획 기본값·지표 정의 — 이 phase 의 기준)
- phases/9-measure/index.json (완료 step 의 summary)
- docs/adr/0015-measurement-events-and-baseline.md (step 0 산출물. step 0 에서는 새로 만든다)
- docs/ARCHITECTURE.md ("측정 — phase 9" 절. step 0 에서는 새로 만든다)
- docs/GLOSSARY.md
- tests/e2e/test_github_cycle.py (fake GitHub·임시 저장소·fake 도구 스택)
- phases/9-measure/index.json (전체 summary)
- docs/CURRENT_HANDOFF.md, docs/product/MVP_PLAN.md (7절 측정 행), docs/VERIFICATION_LOG.md

먼저 실제 파일과 이전 step 산출물을 읽는다. 이름·칸·함수 시그니처는 step 0 의 ADR-0015·ARCHITECTURE 가 고정한 것을 따르고, 바꿔야 하면 같은 이름의 문서·계약·테스트를 함께 갱신한다. 대화 이력을 전제로 판단하지 않는다.

## 작업

1. `tests/e2e/test_metrics.py`(기존 e2e 와 같은 `WORKFLOW_E2E=1` 게이트)를 추가한다. fake GitHub·fake 도구로 이슈 1건이 수정 → 검토(수정 요청) → 재작업 → 검토 승인 → 병합 확인까지 돈 뒤 `/metrics.json` 에서: 재작업 1, 1회 통과 아님, 인계 대기 n≥1, 설정 번호 기록, fake 도구가 보고한 비용 합계, 비용 모름 건수, `folder_commit` 그룹이 보이는지 확인한다. fake GitHub 에 GraphQL 응답을 추가해 기준선 가져오기 → 화면에 기준선 n 이 보이는지도 확인한다.
2. 문서: `docs/CURRENT_HANDOFF.md` 에 phase 9 완료 상태·검증 수치·다음(실제 OpenArchive 기준선 가져오기는 사용자 지시 후, 그다음 `10-selfhost`), `MVP_PLAN.md` 7절 측정 행 갱신, `VERIFICATION_LOG.md` 에 대역 e2e 기록(실연동 아님을 명시).

## 테스트 먼저

e2e 자체가 이 step 의 테스트다. 추가로 `WORKFLOW_E2E=1 python3 -m pytest tests/e2e -q` 가 기존 e2e 와 함께 통과해야 한다.

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
3. 성공이면 `phases/9-measure/index.json` 의 step 10 만 `completed` 로 바꾸고 `summary` 에 생성·수정 파일, 결정, 검증 결과(테스트 수), 다음 step 이 주의할 점을 한 줄로 남긴다.
4. 3회 수정 후에도 실패하면 `error` + `error_message`, 사용자 결정·외부 자격이 필요하면 `blocked` + `blocked_reason` 을 기록한다. 실행하지 않은 검증을 `completed` 로 표시하지 않는다.

## 금지사항

- `main`·공개 데모 VM 을 변경·배포하지 않는다. 이유: 이 phase 는 `service` 기반 실서비스 작업이다.
- 실제 GitHub·유료 모델을 호출하지 않는다. 이유: 모든 step 은 대역(httpx `MockTransport`, fake 도구)으로 검증한다. 실제 기준선 가져오기는 phase 뒤 사용자 지시로 한다.
- 사용자 변경을 삭제하거나 stash·reset 하지 않는다. 커밋은 하네스가 한다.
- 이 step 범위 밖 모듈을 "개선"하지 않는다. 이유: step 마다 한 레이어만 바꿔 회귀 원인을 좁힌다.
- 모르는 비용·시각을 0 이나 현재 시각으로 채우지 않는다. 이유: "모름"과 0 은 다른 사실이다.
- 실제 OpenArchive 기준선을 가져오지 않는다. 이유: 실제 GitHub 호출은 phase 뒤 사용자 지시로 한다.
