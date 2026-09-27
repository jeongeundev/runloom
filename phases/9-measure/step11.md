# Step 11: 병합 시각 저장 — 이슈 하나의 병합 PR 조회와 v6 칸

## 읽어야 할 파일

- AGENTS.md
- phases/9-measure/README.md (계획 기본값·지표 정의 — 이 phase 의 기준)
- phases/9-measure/index.json (완료 step 의 summary)
- docs/adr/0015-measurement-events-and-baseline.md (step 0 산출물. step 0 에서는 새로 만든다)
- docs/ARCHITECTURE.md ("측정 — phase 9" 절. step 0 에서는 새로 만든다)
- docs/GLOSSARY.md
- phases/9-measure/step10-output.json 은 읽지 않아도 된다. 대신 index.json 의 step 7·10 summary 를 읽는다
- src/workflow/adapters/github_client.py (`list_issue_pr_links`, `_graphql`)
- src/workflow/contracts/github.py (`IssuePrLink`)
- src/workflow/adapters/db.py (v6 정의·`_migrate_5_to_6`), src/workflow/adapters/repo.py (`source_issues` 저장 함수)
- tests/workflow/adapters/test_github_client.py, tests/workflow/adapters/test_db.py, tests/workflow/adapters/test_repo.py

먼저 실제 파일과 이전 step 산출물을 읽는다. 이름·칸·함수 시그니처는 step 0 의 ADR-0015·ARCHITECTURE 가 고정한 것을 따르고, 바꿔야 하면 같은 이름의 문서·계약·테스트를 함께 갱신한다. 대화 이력을 전제로 판단하지 않는다.

## 작업

배경(2026-09-27 사용자 합의): 기준선은 "이슈 열림 → 그 이슈를 닫은 PR 병합" 인데, 도입 후 `bug_fix` 묶음의 완료는 지금 "운영자가 Runloom 에서 검토를 승인한 시각"(병합 확인 단계는 `code_change` 에만 있다)이라 같은 구간이 아니다. 사람이 승인 뒤 push·PR·병합을 하므로 도입 후 수치가 짧게 나온다. 도입 후 완료도 **GitHub 에서 그 이슈를 닫은 병합 PR 의 병합 시각**으로 맞춘다. 이 step 은 조회와 저장만, 동기화·지표 연결은 step 12.

1. `HttpGitHubClient.get_issue_pr_link(repo, number) -> IssuePrLink | None` (+ `GitHubClient` Protocol): GraphQL `repository.issue(number)` 의 `closedByPullRequestsReferences` 중 병합된 PR 의 가장 이른 병합 하나. 이슈가 열려 있거나 병합 PR 이 없으면 None. `list_issue_pr_links` 와 같은 허용 저장소 검사·오류 분류·가장 이른 병합 규칙을 공유한다(중복 코드를 만들지 말고 노드 해석을 함께 쓴다).
2. 스키마: `source_issues` 에 `merged_pr_number INTEGER`, `pr_merged_at TEXT`(둘 다 NULL 허용, NULL = 아직 모름/병합 없음), `merge_checked_at TEXT`(마지막 조회 시각) 을 더한다. **v6 는 아직 어디에도 배포되지 않았으므로 새 버전을 만들지 않고 v6 정의(`_migrate_5_to_6` 와 빈 DB 생성)에 넣는다.** `SCHEMA_VERSION` 은 6 그대로.
3. repo: `record_issue_merge(conn, *, session_id, source_id, github_issue_id, link: IssuePrLink | None, now)` — 세션 소유 확인, 한 번 기록된 병합 값은 다른 값으로 덮지 않는다(같은 값 재기록은 멱등). `list_issues_needing_merge_check(conn, session_id, source_id)` — `state='closed'` 이고 `pr_merged_at IS NULL` 인 행.
4. ARCHITECTURE "측정 — phase 9" 절·ADR-0015 에 이 결정(완료 = 병합 PR 병합 시각, 승인 시각은 별도 지표)을 한 단락 추가한다.

## 테스트 먼저

- `test_github_client.py`: 병합 PR 하나·여럿(이른 것)·미병합만·없음·열린 이슈, GraphQL 오류, 허용 밖 저장소, 토큰이 요청 본문·예외 문구에 없음.
- `test_db.py`: 빈 DB·v5→v6 에 새 칸이 있고 기존 행은 NULL.
- `test_repo.py`: 기록·멱등·다른 값으로 덮지 않음·다른 세션 거부·조회 대상 목록.

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
3. 성공이면 `phases/9-measure/index.json` 의 step 11 만 `completed` 로 바꾸고 `summary` 에 생성·수정 파일, 결정, 검증 결과(테스트 수), 다음 step 이 주의할 점을 한 줄로 남긴다.
4. 3회 수정 후에도 실패하면 `error` + `error_message`, 사용자 결정·외부 자격이 필요하면 `blocked` + `blocked_reason` 을 기록한다. 실행하지 않은 검증을 `completed` 로 표시하지 않는다.

## 금지사항

- `main`·공개 데모 VM 을 변경·배포하지 않는다. 이유: 이 phase 는 `service` 기반 실서비스 작업이다.
- 실제 GitHub·유료 모델을 호출하지 않는다. 이유: 모든 step 은 대역(httpx `MockTransport`, fake 도구)으로 검증한다. 실제 기준선 가져오기는 phase 뒤 사용자 지시로 한다.
- 사용자 변경을 삭제하거나 stash·reset 하지 않는다. 커밋은 하네스가 한다.
- 이 step 범위 밖 모듈을 "개선"하지 않는다. 이유: step 마다 한 레이어만 바꿔 회귀 원인을 좁힌다.
- 모르는 비용·시각을 0 이나 현재 시각으로 채우지 않는다. 이유: "모름"과 0 은 다른 사실이다.
