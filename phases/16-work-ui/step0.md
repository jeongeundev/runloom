# Step 0: work-ui-design — 업무 화면 결정과 이름·주소 고정

## 읽어야 할 파일

- AGENTS.md
- phases/16-work-ui/README.md (조사 결과·계획 기본값 9가지 — 이 phase 의 기준), phases/16-work-ui/index.json (이전 step summary)
- docs/product/REDESIGN_PLAN.md (4·10·13·16절)
- docs/adr/0020-work-items-and-stages.md, docs/adr/0021-team-accounts-and-roles.md, docs/adr/0017-github-app-connection.md (App 경로·권한), docs/adr/0014-github-task-cycle.md
- docs/ARCHITECTURE.md ("업무와 단계 — phase 14" 업무 상태 표·스키마 v10, "팀 — phase 15" 역할 × 동작·라우트 표·스키마 v11, "실제 저장소 순환 — phase 12" PR 절)
- docs/UI_GUIDE.md (전체), docs/GLOSSARY.md
- src/workflow/domain/work_status.py, src/workflow/domain/team.py
- src/workflow/adapters/db.py (`work_items`·`work_item_events`·`task_pull_requests`·`github_sources`·`source_issues`), src/workflow/adapters/github_client.py (`GitHubClient`·`list_issues`·`get_issue_pr_link`)
- src/workflow/server/web.py (`home`·`work_detail`·`task_delegate`·`task_select`·`task_run`·`_base`), src/workflow/server/github_sync.py, src/workflow/server/worker.py·github_delivery.py (알림·댓글의 `/tasks/{task_id}` 링크)
- src/workflow/server/templates/_sidebar.html·home.html·work_detail.html·base.html

먼저 실제 파일을 읽는다. 대화 이력을 전제로 판단하지 않는다. 이전 step 이 정한 이름·주소는 `docs/ARCHITECTURE.md` "업무 화면 — phase 16" 을 따른다(README 와 다르면 ARCHITECTURE 가 기준).

## 작업

문서만 바꾼다(제품 코드·테스트 없음). README "계획 기본값" 9가지를 결정 기록으로 옮기고 이후 step 이 쓸 이름·주소를 고정한다.

1. `docs/adr/0022-work-screen-and-direct-work.md` 신설: 결정(README 1~9), 하지 않는 것, 대안·기각 이유(상세 전체 페이지 — 목록으로 돌아갈 때 맥락을 잃음 / 보드 끌기 — 상태가 계산값이라 끌기가 행동과 1:1 이 아님 / 에이전트 담당 = 배정만 — 한 번 더 눌러야 하고 "담당 에이전트인데 안 움직임" 상태가 생김 / 옛 화면 유지 — 화면이 두 벌 / 브랜치 push 감지 — 폴링마다 브랜치 목록, 누가 push 했는지 모름 / Claude Code 훅 포함 — 사용자 전역 설정을 고치는 일이라 따로 검증). ADR-0020 결정 4 의 "`직접 작업 중` 은 값만 예약" 을 이 ADR 이 채운다고 적고 ADR-0020 끝에 한 줄 링크.
2. `docs/ARCHITECTURE.md` 에 "업무 화면 — phase 16" 절. 반드시 **이름·주소를 확정**해 적는다:
   - **주소 표**: `GET /tasks`(쿼리 `q=all|my_turn|unassigned|agent_working`, `group=assignee|status`, `view=list|board`, `open=<업무 키>`, `closed=recent|all` — 모르는 값은 기본값, 되돌아갈 URL 을 받지 않음), `GET /work/{key}/panel`(패널 조각), `GET /work/{key}` → 303 `/tasks?open=<key>`, `POST /work/{key}/assignee`(값 `member:<id>`|`agent:<id>`|`none`), `POST /work/{key}/priority`, `POST /work/{key}/direct`·`/direct/stop`, `GET /connect?tab=sources|team|kinds|notify|advanced`, `GET /monitor`, `GET /start`. 옛 GET 주소 → 새 주소 대응 표(`/sources`·`/operator`·`/operator/github`·`/operator/notifications`·`/team`·`/agents`·`/kinds`·`/metrics`). **바꾸지 않는 경로** 목록(모든 POST, `/operator/github/app/*`·콜백·설치 setup, `/sources/{id}`, `/agents/{id}`, `/chains/{id}`, `/tasks/{task_id}` 단계 상세·`/live`·산출물, `/metrics.json`·`/metrics.csv`, 러너·입구 API). 각 새 경로의 필요 동작(`domain/team.py` 상수)도 표에.
   - **끝난 업무**: 기본 목록은 끝나지 않은 업무 + 최근 14일 안에 끝난 업무(`closed=recent`), `closed=all` 은 전부. 빠른 필터 정의(`my_turn` = 상태 `내 차례` 이고 로그인 멤버가 받는 사람, `unassigned` = 담당 없음이고 끝나지 않음, `agent_working` = 담당이 에이전트이고 끝나지 않음).
   - **묶기 순서**: 담당자 묶음은 "담당 없음" → 로그인한 나 → 다른 활성 멤버(이름순) → 에이전트(이름순) → 비활성 멤버. 묶음 안은 우선순위(높음 → 낮음) → 갱신 시각 최근순. 상태 묶음은 `WORK_STATUSES` 순. 빈 묶음은 숨김. **보드 칸**: README 3 의 6칸(`새로 들어옴` 은 `대기` 칸에, `종료` 는 보드에서 뺌).
   - **"다음 할 일" 칸 규칙**: 열린 사람 요청 질문 → 직접 작업이면 `직접 작업 중 · 이름` → PR 이면 `PR #n` → 상태 이유 → 빈 값.
   - **담당 바꾸기 규칙**: 에이전트 = 맡기기(시작할 단계 고르는 규칙 — 업무의 가장 최근 미마감 단계, 없으면 거부; 후보 에이전트 = 그 단계 `required_capability` 를 가진 워크스페이스 에이전트; GitHub 지시 전 이슈면 지시 기록; `Worker.start_manually` 로 착수하고 지금 못 시작하면 대기 사유를 남김), 사람 = 배정만, `none` = 담당 해제(실행·직접 작업 없을 때). 활성 실행이 있으면 409. 맡긴 사람 기록(ADR-0021). 기존 `/tasks/{id}/delegate`·`/select`·`/run` 은 같은 내부 함수를 부르도록 남긴다.
   - **직접 작업**: 칸 이름, 브랜치 이름 규칙(`<키>-<제목에서 ASCII 영숫자만 소문자·하이픈, 최대 40자>`, 남는 게 없으면 키만), 시작·그만두기·넘기기의 상태 전이, 에이전트 실행 중 거부.
   - **업무 상태 표 갱신**: `domain/work_status.py` 규칙 표에 직접 작업·업무 PR(감지 PR 포함)을 넣은 새 순서(열린 사람 요청이 가장 앞, 그다음 열린 PR, 그다음 직접 작업, 그다음 기존 규칙 — 병합된 감지 PR 은 `완료`, 병합 없이 닫힌 감지 PR 은 무시). `WorkItemFacts` 새 칸 이름.
   - **PR 신호**: 읽는 API(`GET /repos/{owner}/{repo}/pulls?state=all&sort=updated&direction=desc&per_page=50`, 소스마다 tick 당 최대 1~2 페이지, 커서 = 마지막으로 본 `updated_at`), 키 매칭 규칙(head 브랜치 이름 또는 제목에서 `RUN-<숫자>` 를 단어 경계로, 대소문자 무시, 같은 워크스페이스에 있는 키만, 여럿이면 처음 것), Runloom 이 연 PR(`task_pull_requests` 에 있는 번호)과 중복 저장하지 않는 규칙, 저장하는 칸(제목·URL·head·상태·draft·작성자 로그인·병합 시각). GitHub App 권한은 지금과 같음(Pull requests RW 이미 있음).
   - **스키마 v12**: `work_items.direct_member_id`·`direct_started_at`·`direct_branch`(ALTER), 새 표 `work_pull_requests`(이름·칸·UNIQUE `(session_id, repository_full_name, pr_number)`), `github_sources.pull_cursor`(ALTER), `work_item_events` 재생성으로 type 에 `priority_changed`·`direct_started`·`direct_stopped`·`pull_request_linked` 추가(다른 표가 참조하지 않음을 확인하고 적는다). v11 → v12 마이그레이션(새 칸 NULL, 행 보존, 실패하면 v11 그대로).
   - **화면 배치**: 업무 화면은 본문 폭 제한(760px) 없이 넓은 표, 패널은 오른쪽 560px 겹침(800px 미만은 전체 화면), 기존 3열 셸 산출물 뷰어는 단계 상세에서만. 사이드바 항목(README 6), 연결 탭 5개에 옛 화면 내용이 어디로 가는지 표.
   - **이름·시그니처 표**(이후 step 이 만들 함수 — 최종 이름은 여기서 정한다): `domain/work_list.py` `WorkRow`·`filter_rows(rows, q, *, member_id)`·`group_rows(rows, by, *, member_id, ...)`·`board_columns(rows)`, `domain/work_status.py` 새 facts 칸, `branch_name(key, title)`·`keys_in(text)` 의 위치, repo `list_work_rows`·`set_work_priority`·`start_direct_work`·`stop_direct_work`·`upsert_work_pull_request`·PR 커서 읽기·쓰기, `server/work_actions.py` `assign_work(...)`·`set_priority(...)`, `GitHubClient.list_pulls(repo, cursor)`, 시작하기 계산 함수.
3. `docs/UI_GUIDE.md`: "앱 셸" 을 새 사이드바·업무 화면 넓은 표·오른쪽 패널로 고치고, "화면 목록" 에 업무·패널·연결·모니터링·시작하기를 넣는다(옛 항목은 지우거나 "단계 상세" 로). 상태 배지 규칙(색 + 텍스트) 그대로.
4. `docs/GLOSSARY.md`: `업무 화면`, `상세 패널`, `묶기`, `빠른 필터`, `보드`, `직접 작업`(`direct work` — Runloom 러너 밖 사람 세션), `업무 PR`(`work_pull_requests`, 감지 PR·Runloom PR), `연결 화면`, `시작하기`, `모니터링`(= 옛 지표 화면). 금지 표현이 있으면 적는다.
5. `docs/CURRENT_HANDOFF.md` "다음 작업" 에 "16-work-ui 진행 중(phases/16-work-ui)" 한 줄.

## 테스트 먼저

문서 전용 step 이라 제품 테스트를 새로 만들지 않는다. 기존 문서 검사(링크 등)가 통과해야 한다.

## Acceptance Criteria

```bash
python3 -m pytest -q
python3 -m ruff check .
```

위 커맨드와 이 step 의 테스트 항목을 모두 확인한다.

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 규칙을 확인한다: `domain/` 은 FastAPI·sqlite3·HTTPX·subprocess·Git 을 import 하지 않는다 / `server/` 와 `connector/` 는 서로 import 하지 않고 `contracts/` 만 공유한다 / 외부 입력(요청 본문·폼·쿼리 문자열·PR 제목·브랜치 이름·이슈 본문·모델 응답)에서 명령·경로를 받아 실행하지 않는다 — 주소 쿼리 값은 열거형으로만 받고 되돌아갈 URL 을 받지 않는다 / 비밀값(연결 토큰, `OPERATOR_TOKEN`, `SESSION_SECRET`, 로그인 세션·초대·재설정 토큰 원문, 비밀번호, GitHub App 비밀·설치 토큰, PAT, 알림 웹훅 URL)은 DB·로그·응답·템플릿·백업에 넣지 않는다 / 권한은 `domain/team.py` 역할 × 동작 표로만 판정한다 / 템플릿은 PR 제목·이슈 제목 같은 외부 문자열을 자동 이스케이프로만 출력한다(`|safe` 금지) / GLOSSARY 이름을 그대로 쓴다 / phase 8·11·12·14·15 의 GitHub 순환(수집 → 맡기기 → 수정 → 검토 → 초안 PR → 병합 추적 → 업무 완료)과 사람별 내 차례가 그대로 동작한다.
3. 성공이면 `phases/16-work-ui/index.json` 의 step 0 만 `completed` 로 바꾸고 `summary` 에 생성·수정·삭제 파일, 결정, 검증 결과(테스트 수), 다음 step 이 주의할 점을 한 줄로 남긴다.
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
