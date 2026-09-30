# Step 9: pr-signal — PR 신호 — 소스 저장소 PR 읽기·키 매칭·업무 PR·상태

## 읽어야 할 파일

- AGENTS.md
- phases/16-work-ui/README.md (조사 결과·계획 기본값 9가지 — 이 phase 의 기준), phases/16-work-ui/index.json (이전 step summary)
- docs/ARCHITECTURE.md ("업무 화면 — phase 16" PR 신호·업무 상태 표·스키마 v12)
- src/workflow/adapters/github_client.py (`GitHubClient` 프로토콜·`HttpGitHubClient.list_issues`·ETag·rate limit 처리·`get_issue_pr_link`), tests/workflow/adapters/test_github_client.py
- src/workflow/server/github_sync.py (`sync_source`·`_poll`·`_check_merges`·`SyncReport`), tests/workflow/server/test_github_sync.py
- src/workflow/server/worker.py (동기화 호출·업무 상태 재계산), src/workflow/adapters/repo.py (`task_pull_requests` 조회·업무 상태 재계산), src/workflow/domain/work_status.py
- tests/ 의 가짜 GitHub(대역) — `GitHubClient` 를 흉내 내는 테스트 클래스들(`grep -rn "def get_issue_pr_link" tests` 로 찾는다, 새 메서드가 필요하다)

먼저 실제 파일을 읽는다. 대화 이력을 전제로 판단하지 않는다. 이전 step 이 정한 이름·주소는 `docs/ARCHITECTURE.md` "업무 화면 — phase 16" 을 따른다(README 와 다르면 ARCHITECTURE 가 기준).

## 작업

1. 순수 함수 `keys_in(text)`(ARCHITECTURE 규칙: `RUN-<숫자>` 단어 경계·대소문자 무시·순서 유지·중복 제거).
2. `GitHubClient.list_pulls(repo, cursor)` + `HttpGitHubClient` 구현(ARCHITECTURE API·필드만 파싱하는 Pydantic 모델, 기존 rate limit·오류 예외 그대로). 프로토콜을 흉내 내는 테스트 대역 모두에 메서드 추가.
3. `github_sync`: 이슈 수집 뒤 소스마다 PR 읽기(tick 당 페이지 상한, 커서 = 마지막 `updated_at`, 실패는 `report` 에 남기고 이슈 수집 결과는 유지). PR 마다 head 브랜치 → 제목 순으로 키를 찾고, 같은 워크스페이스 업무가 있으면 `upsert_work_pull_request`(이미 있으면 상태·제목·병합 시각 갱신), 처음 붙으면 이벤트 `pull_request_linked`. Runloom 이 연 PR(`task_pull_requests` 에 같은 저장소·번호)은 저장하지 않는다. 바뀐 업무는 업무 상태 재계산.
4. 업무 상태: `WorkItemFacts` 에 업무 PR 을 넣어 ARCHITECTURE 표대로 — 열림 → `PR · 검토`, 병합 → `완료`(끝 상태 기록 경로는 기존 완료와 같게, 원본 이슈 닫힘과 겹쳐도 한 번), 병합 없이 닫힘 → 무시(직접 작업이면 `직접 작업 중` 으로 돌아감). 담당이 에이전트이거나 없는 업무에 사람이 연 PR 도 같은 규칙.
5. 패널·목록: 업무 PR 을 머리·타임라인·다음 할 일(`PR #n`)에 보인다(Runloom PR 과 같은 자리, 감지 PR 은 "감지" 표시). 외부 제목은 이스케이프.

## 테스트 먼저

`tests/workflow/domain/`(키 추출 순수 함수 테스트 — 위치는 ARCHITECTURE), `tests/workflow/adapters/test_github_client.py`(MockTransport 로 PR 목록 파싱·페이지·304·rate limit), `tests/workflow/server/test_github_sync.py`(브랜치 키·제목 키·키 없음·다른 워크스페이스 키 무시, Runloom PR 제외, 재수집 멱등, 열림 → 병합 → 완료 한 번, 닫힘 → 직접 작업 복귀, PR 읽기 실패해도 이슈 결과 유지, 커서 전진), `tests/workflow/domain/test_work_status.py`(업무 PR 규칙), 패널·목록 표시 테스트.

소스·템플릿 변경 전에 `tests/` 미러 경로에 실패 테스트를 작성하고 실패 원인이 의도한 것인지 확인한다(`tdd-guard.sh` 가 테스트 없는 소스 작성을 막는다). 구현 후 해당 테스트와 전체 회귀를 통과시킨다. 이 step 의 변경으로 기존 테스트가 깨지면 새 동작 기준으로 고치되 단정을 약하게 만들지 않는다(무엇을 왜 바꿨는지 summary 에 남긴다).

## Acceptance Criteria

```bash
python3 -m pytest -q
python3 -m ruff check .
```

위 커맨드와 이 step 의 테스트 항목을 모두 확인한다.

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 규칙을 확인한다: `domain/` 은 FastAPI·sqlite3·HTTPX·subprocess·Git 을 import 하지 않는다 / `server/` 와 `connector/` 는 서로 import 하지 않고 `contracts/` 만 공유한다 / 외부 입력(요청 본문·폼·쿼리 문자열·PR 제목·브랜치 이름·이슈 본문·모델 응답)에서 명령·경로를 받아 실행하지 않는다 — 주소 쿼리 값은 열거형으로만 받고 되돌아갈 URL 을 받지 않는다 / 비밀값(연결 토큰, `OPERATOR_TOKEN`, `SESSION_SECRET`, 로그인 세션·초대·재설정 토큰 원문, 비밀번호, GitHub App 비밀·설치 토큰, PAT, 알림 웹훅 URL)은 DB·로그·응답·템플릿·백업에 넣지 않는다 / 권한은 `domain/team.py` 역할 × 동작 표로만 판정한다 / 템플릿은 PR 제목·이슈 제목 같은 외부 문자열을 자동 이스케이프로만 출력한다(`|safe` 금지) / GLOSSARY 이름을 그대로 쓴다 / phase 8·11·12·14·15 의 GitHub 순환(수집 → 맡기기 → 수정 → 검토 → 초안 PR → 병합 추적 → 업무 완료)과 사람별 내 차례가 그대로 동작한다.
3. 성공이면 `phases/16-work-ui/index.json` 의 step 9 만 `completed` 로 바꾸고 `summary` 에 생성·수정·삭제 파일, 결정, 검증 결과(테스트 수), 다음 step 이 주의할 점을 한 줄로 남긴다.
4. 3회 수정 후에도 실패하면 `error` + `error_message`, 사용자 결정·외부 자원이 필요하면 `blocked` + `blocked_reason` 을 기록한다. 실행하지 않은 검증을 `completed` 로 표시하지 않는다.

## 금지사항

- 실제 GitHub(api.github.com·github.com)·Discord·외부 웹훅을 호출하지 않는다(공식 문서 읽기는 허용). 이유: 모든 step 은 httpx `MockTransport`·가짜 GitHub·가짜 알림 수신으로 검증한다. 실연동은 phase 뒤 사용자와 함께 한다.
- 사용자가 띄워 둔 셀프호스트(compose 프로젝트 `runloom`, 포트 8000, 볼륨 `runloom_workflow-data`)·`deploy/selfhost/.env`·러너 홈(`~/Library/Application Support/workflow-connector/`)·launchd·`~/.claude/`·`/Users/kje/demo/*` 를 읽거나 바꾸지 않는다. 컨테이너를 띄우거나 멈추지 않는다. 이유: 실사용 중인 환경이다.
- 새 의존성(Python 패키지, JS 프레임워크·번들러, 외부 CDN·웹폰트·아이콘 라이브러리)을 추가하지 않는다. 이유: ADR-0002·UI_GUIDE — Jinja2 서버 렌더 + CSS + 소량 인라인 JS.
- 종류 이름(`bug_fix`·`code_review`)으로 새 분기를 만들지 않는다. 이유: 업무 종류·후속 규칙은 워크스페이스 등록 데이터다(ADR-0009, AGENTS.md).
- `tasks` 표를 재생성하거나 `tasks.status` CHECK 를 바꾸지 않는다. 이유: ADR-0020 — 업무 상태는 `work_items` 에, 단계 상태는 그대로.
- 판단 제안·확신도·자동 시작(18-triage), Jira(17-jira), 모니터링 지표 확장(19-monitor), Claude Code 훅(다음 phase), 보드 끌어 옮기기, 브랜치 push 감지, 여러 행 일괄 변경을 만들지 않는다. 이유: 사용자 결정 2026-09-30 범위 밖.
- 직접 작업을 Runloom 결과 판정·완료 판정으로 처리하지 않는다. 이유: 완료는 PR 병합 같은 원본 신호로만(REDESIGN_PLAN 10절, 계약 v1).
- 사용자 변경을 삭제하거나 stash·reset 하지 않는다. 커밋은 하네스가 한다.
- 이 step 범위 밖 모듈을 "개선"하지 않는다. 기존 테스트의 단정을 약하게 만들어 통과시키지 않는다.
