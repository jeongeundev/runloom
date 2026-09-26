# Step 8: 지표 API·기준선 가져오기 경로

## 읽어야 할 파일

- AGENTS.md
- phases/9-measure/README.md (계획 기본값·지표 정의 — 이 phase 의 기준)
- phases/9-measure/index.json (완료 step 의 summary)
- docs/adr/0015-measurement-events-and-baseline.md (step 0 산출물. step 0 에서는 새로 만든다)
- docs/ARCHITECTURE.md ("측정 — phase 9" 절. step 0 에서는 새로 만든다)
- docs/GLOSSARY.md
- src/workflow/domain/metrics.py (step 6), src/workflow/adapters/repo.py (step 4·7)
- src/workflow/server/web.py (`/operator/github` 인증·CSRF·세션 규칙), src/workflow/server/auth.py, src/workflow/server/app.py (라우터 등록)
- tests/workflow/server/

먼저 실제 파일과 이전 step 산출물을 읽는다. 이름·칸·함수 시그니처는 step 0 의 ADR-0015·ARCHITECTURE 가 고정한 것을 따르고, 바꿔야 하면 같은 이름의 문서·계약·테스트를 함께 갱신한다. 대화 이력을 전제로 판단하지 않는다.

## 작업

서버 계층. 새 모듈 `src/workflow/server/metrics_api.py`(라우터)를 만들고 `app.py` 에 등록한다.

1. `repo.list_metric_facts(conn, session_id)`: 세션의 Task·실행·판정·검토 결과·사람 요청·업무 이벤트를 도메인 입력 값 객체로 바꾸는 데 필요한 행을 읽는다(명시적 SQL). 코드 검토 outcome 은 저장된 결과(판정 JSON 또는 `code_review_result` 산출물)에서 읽는다 — 어디에 있는지 코드로 확인한다.
2. `GET /metrics.json?from=&to=&group_by=` — `compute_metrics` + 세션 GitHub 소스별 `summarize_baseline` 결과를 JSON 으로. 기준선 항목에는 "하네스·Claude 사용 시기 이력 — 순수 수작업 기준 아님" 주석 문자열을 함께 낸다.
3. `GET /metrics.csv` — 같은 지표를 행 단위로(지표, 그룹, n, 중앙값 초, 미완료, 분자, 분모). 모름은 빈 칸.
4. `POST /operator/github/sources/{source_id}/baseline` — 운영자만. 소스의 `created_at` 을 `opened_before` 로 `list_issue_pr_links` 호출 후 `replace_baseline`. GitHub 오류는 기존 GitHub 오류 응답 형식으로.
5. 인증·세션 소유 규칙은 기존 `/operator/github` 과 같게. 다른 세션의 데이터는 보이지 않는다.

## 테스트 먼저

서버 테스트(`tests/workflow/server/test_metrics_api.py`): 대역 데이터로 JSON 값·CSV 행, 기간·group_by 파라미터, 잘못된 파라미터 422/400, 비운영자·다른 세션 거부, 기준선 가져오기(가짜 GitHub 클라이언트) 성공·GitHub 오류·멱등, 응답에 토큰·저장소 경로가 없음.

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
3. 성공이면 `phases/9-measure/index.json` 의 step 8 만 `completed` 로 바꾸고 `summary` 에 생성·수정 파일, 결정, 검증 결과(테스트 수), 다음 step 이 주의할 점을 한 줄로 남긴다.
4. 3회 수정 후에도 실패하면 `error` + `error_message`, 사용자 결정·외부 자격이 필요하면 `blocked` + `blocked_reason` 을 기록한다. 실행하지 않은 검증을 `completed` 로 표시하지 않는다.

## 금지사항

- `main`·공개 데모 VM 을 변경·배포하지 않는다. 이유: 이 phase 는 `service` 기반 실서비스 작업이다.
- 실제 GitHub·유료 모델을 호출하지 않는다. 이유: 모든 step 은 대역(httpx `MockTransport`, fake 도구)으로 검증한다. 실제 기준선 가져오기는 phase 뒤 사용자 지시로 한다.
- 사용자 변경을 삭제하거나 stash·reset 하지 않는다. 커밋은 하네스가 한다.
- 이 step 범위 밖 모듈을 "개선"하지 않는다. 이유: step 마다 한 레이어만 바꿔 회귀 원인을 좁힌다.
- 모르는 비용·시각을 0 이나 현재 시각으로 채우지 않는다. 이유: "모름"과 0 은 다른 사실이다.
