# Step 7: 기준선 — GitHub 이슈↔병합 PR 이력 조회·저장

## 읽어야 할 파일

- AGENTS.md
- phases/9-measure/README.md (계획 기본값·지표 정의 — 이 phase 의 기준)
- phases/9-measure/index.json (완료 step 의 summary)
- docs/adr/0015-measurement-events-and-baseline.md (step 0 산출물. step 0 에서는 새로 만든다)
- docs/ARCHITECTURE.md ("측정 — phase 9" 절. step 0 에서는 새로 만든다)
- docs/GLOSSARY.md
- src/workflow/adapters/github_client.py (토큰·재시도·한도 처리, `_call`)
- src/workflow/contracts/github.py
- src/workflow/adapters/repo.py, src/workflow/adapters/db.py (`baseline_items`·`baseline_imports`)
- tests/workflow/adapters/test_github_client.py (MockTransport 사용 방식), tests/workflow/contracts/test_github.py

먼저 실제 파일과 이전 step 산출물을 읽는다. 이름·칸·함수 시그니처는 step 0 의 ADR-0015·ARCHITECTURE 가 고정한 것을 따르고, 바꿔야 하면 같은 이름의 문서·계약·테스트를 함께 갱신한다. 대화 이력을 전제로 판단하지 않는다.

## 작업

어댑터 계층만: GitHub 클라이언트 메서드와 repo 저장. 서버 경로는 step 8.

1. `contracts/github.py` 에 `IssuePrLink`(issue_number, issue_title, issue_opened_at, pr_number, pr_merged_at) 추가.
2. `GitHubClient.list_issue_pr_links(repo, *, opened_before) -> list[IssuePrLink]`: GitHub GraphQL(`POST /graphql`, 같은 토큰·헤더·한도 처리)로 저장소의 닫힌 이슈 중 `opened_before` 이전에 열린 것과, 그 이슈를 닫은 **병합된** PR(`closedByPullRequestsReferences` 등, step 0 이 적은 필드)을 페이지를 끝까지 돌며 모은다. 병합 PR 이 없는 이슈는 제외한다. 이슈 하나에 병합 PR 이 여럿이면 가장 이른 병합을 쓴다.
3. 허용 저장소 검사(`WORKFLOW_GITHUB_REPOS`)는 기존 REST 메서드와 같게 적용한다. 토큰은 `WORKFLOW_GITHUB_TOKEN` 에서만 읽는다(기존 `from_env`).
4. repo: `replace_baseline(conn, *, source_id, opened_before, links, now)` — 한 트랜잭션에서 그 소스의 `baseline_items` 를 교체하고 `baseline_imports` 를 갱신한다(재실행 멱등). `list_baseline(conn, session_id, source_id)` — 세션 소유 확인.

## 테스트 먼저

- `test_github_client.py`: GraphQL 응답 두 페이지 연결, 병합 안 된 PR 무시, 여러 PR 중 이른 병합 선택, `opened_before` 경계, GraphQL `errors` 응답·한도 응답 처리, 허용 밖 저장소 거부, 요청 본문·로그에 토큰이 없음.
- `test_repo.py`: 교체 멱등, 다른 세션 소스 접근 거부.
- `test_github.py`: `IssuePrLink` 검증.

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
3. 성공이면 `phases/9-measure/index.json` 의 step 7 만 `completed` 로 바꾸고 `summary` 에 생성·수정 파일, 결정, 검증 결과(테스트 수), 다음 step 이 주의할 점을 한 줄로 남긴다.
4. 3회 수정 후에도 실패하면 `error` + `error_message`, 사용자 결정·외부 자격이 필요하면 `blocked` + `blocked_reason` 을 기록한다. 실행하지 않은 검증을 `completed` 로 표시하지 않는다.

## 금지사항

- `main`·공개 데모 VM 을 변경·배포하지 않는다. 이유: 이 phase 는 `service` 기반 실서비스 작업이다.
- 실제 GitHub·유료 모델을 호출하지 않는다. 이유: 모든 step 은 대역(httpx `MockTransport`, fake 도구)으로 검증한다. 실제 기준선 가져오기는 phase 뒤 사용자 지시로 한다.
- 사용자 변경을 삭제하거나 stash·reset 하지 않는다. 커밋은 하네스가 한다.
- 이 step 범위 밖 모듈을 "개선"하지 않는다. 이유: step 마다 한 레이어만 바꿔 회귀 원인을 좁힌다.
- 모르는 비용·시각을 0 이나 현재 시각으로 채우지 않는다. 이유: "모름"과 0 은 다른 사실이다.
