# Step 0: setup-ux-design — 설정 UX 설계 고정 (문서만)

## 읽어야 할 파일

- AGENTS.md
- phases/23-setup-ux/README.md (사용자 결정 D1~D4·계획 기본값 — 이 phase 의 기준), phases/23-setup-ux/index.json (이전 step summary)
- docs/ARCHITECTURE.md "설정 UX — phase 23" (step 0 이 쓴 이름·주소·시그니처·문구 — README 와 다르면 ARCHITECTURE 가 기준)
- docs/adr/0028-*.md (step 0 이 쓴 ADR), docs/UI_GUIDE.md, docs/GLOSSARY.md
- docs/product/INTERNAL_REQUEST_LIVE_RUN_1.md "결과" (화면 점검 15건 — 이 phase 의 계기)
- docs/ARCHITECTURE.md "업무 화면 — phase 16" 의 "주소 (step 3~8)"·"화면 배치 (step 4·5·6·7)"(연결 화면 탭 표·시작하기 표), "팀 — phase 15", "판단 — phase 19" 의 판단 탭, "결과 뒤 판단 — phase 22"
- docs/adr/0022-work-screen-and-direct-work.md, docs/adr/0025-triage.md, docs/adr/0026-monitor.md (ADR 형식), docs/adr/0009-registered-kinds-and-succession-rules.md, docs/adr/0021-*.md·0024-*.md 등 팀·담당 관련 ADR 이 있으면 그것도
- docs/product/RESPONSIBILITY_DIRECTORY.md, docs/SELFHOST.md "업그레이드"
- 코드: `src/workflow/server/templates/_sidebar.html`, `connect.html`, `_connect_*.html`, `home.html`, `start.html`, `login.html`(초대 가입 화면 템플릿 위치 확인); `src/workflow/server/web.py` 의 `_base`(200 근처)·`CONNECT_TABS`(1720 근처)·`_visible_tabs`·`_tab_context`·`connect_page`·`_connect_page`·`_to_connect` 와 옛 주소 라우트(`/agents`·`/kinds`·`/sources`·`/operator`·`/operator/github`·`/operator/notifications`·`/team`·`/metrics`)·`team_invite`·`/invite/{token}`·`_link_url`·`kinds_create`·`/responsibilities/add`·`operator_register_agent`; `src/workflow/server/views.py` 의 `github_context`·`team_context`·`triage_settings_context`·`notifications_context`·`agent_public`·`work_list_context`; `src/workflow/domain/work_list.py`(`filter_counts`·`_matches`·`group_rows`·`_assignee_group`·`next_action`); `src/workflow/domain/start_checklist.py`; `src/workflow/domain/team.py`(`_ROLE_ACTIONS`); `src/workflow/adapters/db.py` `member_invites`·`agents`·`kinds`·`responsibilities` DDL 과 마이그레이션 형식; `src/workflow/adapters/repo.py` 초대 함수(`_insert_link`·`issue_invite`·`invite_for_token`·`accept_invite`·`revoke_invite`·`list_open_invites`)·`upsert_agent`·`register_local_agent`·`insert_kind`·`delete_kind`·`list_source_issues`; `src/workflow/adapters/responsibility_store.py`; `src/workflow/contracts/v1.py` `KindSpec`·`KIND_PATTERN`·`Capability`; `src/workflow/contracts/github.py` `GitHubSourceConfig`·`AssigneeBinding`; `src/workflow/server/github_api.py` `update_source`·`bind_assignee`
- 테스트: `tests/workflow/server/test_web_connect.py`, `test_web.py`, `test_web_team.py`, `test_web_work_list.py`, `test_web_start.py`, `tests/workflow/domain/test_work_list.py` (주소·문구 단정 형식)

먼저 실제 파일을 읽는다. 대화 이력을 전제로 판단하지 않는다. 이전 step 에서 만든 코드를 꼼꼼히 읽고 설계 의도를 이해한 뒤 작업한다.

## 작업

이 step 은 **문서만** 쓴다. 코드·테스트는 바꾸지 않는다. 다음 step 들이 이 문서를 기준으로 구현한다.

1. 코드를 직접 읽어 README "왜" 의 15건과 "계획 기본값" 이 코드와 맞는지 확인한다. 다르면 ADR 에 "코드 조사로 README 와 달라진 사실" 로 적고 결정을 고친다(ADR-0025·0027 의 같은 절 형식).
2. `docs/adr/0028-setup-ux.md` 를 쓴다. 계기(K1 준비 단계 중단·사용자 평·15건), 사용자 결정 D1~D4, 결정(README 계획 기본값 1~11 을 코드 근거로 확정·수정), 하지 않는 것, 결과(좋은 점·비용·셀프호스트 업그레이드 영향). 최소한 아래를 확정한다:
   - **주소 표**: 새 GET 주소, `/connect?tab=X` → 새 주소 표(모르는 탭·탭 없음 포함), 옛 주소 303 대상, POST 뒤 303 대상(지금 `/connect?tab=…` 로 보내는 모든 POST 를 찾아 표로), `/start` 체크리스트 항목의 새 링크. `/team` 은 지금 303 라우트 — 실제 화면으로 바뀐다.
   - **권한 표**: 화면·탭·절별 필요 동작(`_ROLE_ACTIONS` 기준), 권한 없을 때(숨김·403).
   - **스키마 v25**: `member_invites` 에 더하는 칸·제약(초대 행만 이메일 필수인 CHECK 를 둘지 — ALTER 로 CHECK 를 못 더하면 애플리케이션 검증 + 이유), 기존 행 처리, 이메일 정규화(소문자·앞뒤 공백), 겹침 규칙, `reissue_invite` 거부 조건과 오류 이름, `accept_invite` 의 이메일 고정 규칙(옛 초대는 이메일 입력 그대로).
   - **에이전트 능력**: `add_agent_capability`·`remove_agent_capability` 시그니처와 반환, 내장 능력 보호 규칙(어떤 code 를 보호하는지 — `BUILTIN_KINDS` 의 `capability_code` 로 판별, 종류 이름 분기 금지), 범위 값을 어디서 가져오는지, 고를 수 없는 에이전트와 이유 문구, 종류 삭제 시 능력 떼기의 트랜잭션.
   - **종류 폼**: 기본 칸·고급 칸, 자동 식별자 규칙(충돌 시 재시도 횟수), 결과값 기본 채움(추천 `done, needs_information`), 화면 이름 중복 처리, 맡을 에이전트 0명 허용 여부(추천: 허용 — 나중에 팀 화면에서 붙인다, 안내 문구).
   - **팀 화면**: 절 순서·에이전트 표 칸·러너 합치기(에이전트 없는 러너·러너 없는 에이전트 처리)·"자세히" 표식(클래스 이름 하나로 고정 — step 10 의 노출 단정이 쓴다)·담당 범위 요청 유형 고정 목록(RESPONSIBILITY_DIRECTORY·`internal_request_store`·판단 요청문이 쓰는 값에서 뽑는다 — 목록 밖 기존 행 표시 방법).
   - **저장소 화면**: 카드 칸·고급 칸·업무 링크 주소, 판단 에이전트 설명 문구, 담당 연결 GitHub 사용자 고르기 가능 여부(`source_issues` 에 담당자 id·login 이 저장되는지 코드로 확인 — 안 되면 라벨만 정리하고 이유), GitHub App·Jira 연결 위치.
   - **업무 목록**: 끝난 업무 묶음 이름·위치·접힘 기본값, 건수 규칙(빠른 필터·묶음 머리), 칸 숨김 규칙(보이는 행 기준·보드 포함), 다음 할 일 중복 판정, 도구 막대 마크업("보기 옵션" `<details>`, 저장소 select 자동 이동 JS·`<noscript>`).
   - **설정 화면**: 탭 목록·순서·키, n8n 입구 탭의 내용, 각 탭에서 "자세히" 로 옮길 값.
   - **노출 단정 규칙**: step 10 이 쓸 정규식과 "자세히" 밖 판정 방법(HTML 에서 그 표식 요소를 지운 뒤 검사 등).
3. `docs/ARCHITECTURE.md` 끝에 "## 설정 UX — phase 23" 절: 이름 고정 표(라우트·함수·상수·템플릿·CSS 클래스·문구 — 다음 step 이 그대로 쓴다), 주소 표, 권한 표, 스키마 v25 표, 화면별 절·칸 표, step 별 시그니처 목록(1~9). "업무 화면 — phase 16" 의 연결 화면 탭 표와 주소 표에는 "phase 23 에서 바뀜 → 설정 UX 절" 한 줄만 단다(옛 표는 지우지 않는다).
4. `docs/UI_GUIDE.md` 의 앱 셸 사이드바·화면 목록·업무 화면 도구 막대를 새 구성으로 갱신(갱신일·phase 표기).
5. `docs/GLOSSARY.md` 에 phase 23 용어(팀 화면, 저장소 화면, 설정 화면, 맡을 수 있는 일, 링크 다시 만들기, 끝난 업무 묶음 — 코드 식별자와 금지 표현).
6. `docs/CURRENT_HANDOFF.md` 맨 위 "다음 작업" 을 "23-setup-ux 진행 중" 으로.

## 테스트 먼저

- 문서만이므로 테스트를 새로 쓰지 않는다. AC 로 기존 테스트 전체가 그대로 통과하는지 확인한다.

소스·템플릿을 바꾸기 전에 `tests/` 미러 경로에 실패하는 테스트를 먼저 작성하고, 실패 원인이 의도한 것인지 확인한다(`tdd-guard.sh` 가 테스트 없는 소스 작성을 막는다). 구현한 뒤 해당 테스트와 전체 회귀를 통과시킨다. 이 step 의 변경으로 기존 테스트(주소·문구 단정)가 깨지면 새 동작 기준으로 고치되 단정을 약하게 만들지 않는다 — 옛 주소 단정은 지우지 말고 303 대상 단정으로 바꾼다. 무엇을 왜 바꿨는지는 summary 에 남긴다.

## Acceptance Criteria

```bash
python3 -m pytest -q
python3 -m ruff check .
```

위 커맨드와 이 step 의 테스트 항목을 모두 확인한다.

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 규칙을 확인한다.
   - `domain/` 은 FastAPI·sqlite3·HTTPX·subprocess·Git 을 import 하지 않는다. 순수 함수이고 현재 시각을 스스로 읽지 않는다(`now` 인자).
   - `server/` 와 `connector/` 는 서로 import 하지 않고 `contracts/` 만 공유한다.
   - 외부 입력(폼 값·업무 제목·멤버 이름·이메일·종류 이름·지시문)에서 명령·경로를 받아 실행하지 않는다.
   - 비밀값(연결 토큰 `wfc_…`, `OPERATOR_TOKEN`, `SESSION_SECRET`, GitHub App 비밀·설치 토큰, PAT, Jira API 토큰, 알림 웹훅 URL)과 **초대·재설정 링크 원본 토큰**을 DB·로그·응답 JSON·백업에 넣지 않는다. 링크 원본은 만든 그 응답의 화면에만 한 번 보인다(DB 에는 `token_sha256` 만).
   - 템플릿은 외부 문자열을 자동 이스케이프로만 출력한다(`|safe` 금지). `alert()`·`confirm()` 을 쓰지 않는다. JS 없이도 모든 이동·폼이 된다(UI_GUIDE).
   - 종류 이름(`bug_fix`·`code_review`·`triage`)으로 새 분기를 만들지 않는다(ADR-0009). 내장 종류 판별은 `KindSpec.builtin`.
   - GLOSSARY 이름을 그대로 쓴다(`Execution` ≠ run, `Agent` ≠ connector, `outcome` ≠ 상태). 화면 문구 금지 표현을 쓰지 않는다.
   - POST 경로를 바꾸지 않는다(GitHub App 콜백·설치·폼 action). 바꾸는 것은 GET 화면 주소와 POST 뒤 303 대상뿐이다.
   - phase 8·9·11·12·14~22 동작(GitHub·Jira 순환, 지표, 내 차례, 직접 작업·PR 신호, 소유자 승인·꺼진 러너 대기, 접수 판단·결과 뒤 판단, 모니터링, 사내 요청)이 그대로 동작한다.
3. 성공이면 `phases/23-setup-ux/index.json` 의 이 step 만 `completed` 로 바꾸고, `summary` 에 한 줄로 남긴다: 생성·수정·삭제 파일, 결정, 검증 결과(테스트 수), 다음 step 이 주의할 점.
4. 3회 수정 후에도 실패하면 `error` + `error_message` 를 기록한다. 사용자 결정·외부 자원이 필요하면 `blocked` + `blocked_reason` 을 기록한다. 실행하지 않은 검증을 `completed` 로 표시하지 않는다.

## 금지사항

- 실제 Claude/Codex·GitHub(api.github.com·github.com)·Jira·Discord·외부 웹훅을 호출하지 않는다. 이유: 모든 step 은 가짜 러너·가짜 도구·httpx `MockTransport`·가짜 클라이언트로 검증한다.
- 사용자가 띄워 둔 환경을 읽거나 바꾸지 않는다: 셀프호스트(compose 프로젝트 `runloom`, 포트 8000, 볼륨 `runloom_workflow-data`)·`deploy/selfhost/.env`·러너 홈(`~/Library/Application Support/workflow-connector*`)·launchd(`launchctl` 실행 금지)·`~/Library/LaunchAgents`·`~/.claude/`·`/Users/kje/demo/*`·GitHub 저장소 `jeongeundev/*`. 컨테이너를 띄우거나 멈추지 않는다. 이유: 실사용 중인 환경이다.
- 새 의존성(Python 패키지, JS·CSS 프레임워크, 번들러, 아이콘 라이브러리, 외부 CDN·웹폰트)을 추가하지 않는다. 이유: ADR-0002·UI_GUIDE.
- 러너 프로토콜(`contracts/v1.py` 의 claim·실행 요청·결과 계약, `connector/`)을 바꾸지 않는다. 이유: 이 phase 는 러너 재설치 없이 올라가야 한다.
- 사용자 변경을 삭제하거나 stash·reset 하지 않는다. 커밋은 하네스가 한다.
- 이 step 범위 밖 화면·모듈을 "개선"하지 않는다(업무 패널·단계 상세·`/requests`·모니터링 화면은 이 phase 범위 밖). 기존 테스트의 단정을 약하게 만들어 통과시키지 않는다.
- 코드·테스트·스키마·템플릿을 바꾸지 마라. 이유: 이 step 은 설계 고정만 한다. 구현은 step 1~9.
