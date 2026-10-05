# Step 10: setup-ux-verify — 검증 — 화면만으로 준비 끝내기

## 읽어야 할 파일

- AGENTS.md
- phases/23-setup-ux/README.md (사용자 결정 D1~D4·계획 기본값 — 이 phase 의 기준), phases/23-setup-ux/index.json (이전 step summary)
- docs/ARCHITECTURE.md "설정 UX — phase 23" (step 0 이 쓴 이름·주소·시그니처·문구 — README 와 다르면 ARCHITECTURE 가 기준)
- docs/adr/0028-*.md (step 0 이 쓴 ADR), docs/UI_GUIDE.md, docs/GLOSSARY.md
- docs/ARCHITECTURE.md "설정 UX — phase 23" 전체, docs/product/INTERNAL_REQUEST_LIVE_RUN_1.md "준비 (관리자 R)"
- step 1~9 가 만든 화면·라우트·함수 전부, `tests/e2e/` 의 기존 서버 띄우기·로그인 helper(예: `test_team.py`, `test_next_step_cycle.py`)
- docs/SELFHOST.md "업그레이드", docs/CURRENT_HANDOFF.md, docs/VERIFICATION_LOG.md 형식

먼저 실제 파일을 읽는다. 대화 이력을 전제로 판단하지 않는다. 이전 step 에서 만든 코드를 꼼꼼히 읽고 설계 의도를 이해한 뒤 작업한다.

## 작업

1. `tests/e2e/test_setup_ux.py` 신규: 실제 서버 프로세스(기존 e2e 방식) + HTTP 클라이언트로, 관리자가 **화면이 주는 링크·폼만 따라** 아래를 끝낸다(주소를 손으로 만들지 않는다 — 사이드바·버튼 href 를 HTML 에서 읽어 따라간다):
   - 사이드바 `팀` → 이메일 초대 2건(N·J) → 링크 하나는 놓친 셈 치고 [링크 다시 만들기] → 옛 링크 가입 거부, 새 링크 가입 성공 → 둘 다 멤버 목록에.
   - `설정` → 종류 등록(화면 이름 `장애 조사`, 지시문, 결과값, 맡을 에이전트 = 가짜 러너 에이전트) → 그 에이전트가 새 종류 후보.
   - `팀` → 담당 범위 추가(시스템 `kube_proxy`, 요청 유형 select 값, 받는 사람 N, 판단 담당자 J, 에이전트).
   - `저장소` → 판단 에이전트 지정 → 저장소 설정에 반영.
   - 각 화면(팀·저장소·설정·업무 목록) 본문에서 ARCHITECTURE 노출 단정 규칙으로 `agt-`·`conn-`·`inv-`·`code.(fix|review|triage)` 가 "자세히" 밖에 없음.
   - 옛 주소 전부 303 → 새 주소, POST 경로 그대로 동작.
2. CSS·마크업 단정: 팀·저장소·설정·업무 목록의 표가 가로 스크롤 컨테이너 안(390px 가로 스크롤 없음 — UI_GUIDE).
3. v24 사본 → v25 업그레이드 e2e(열린 옛 초대가 "이메일 없음(옛 초대)" 로 보이고 다시 만들기·취소 가능).
4. 문서: SELFHOST "업그레이드" 에 v25(백업 먼저 · `install.sh` · 러너 재설치 불필요 · 북마크 주소가 바뀜 — 옛 주소는 넘어감 · 열린 옛 초대는 이메일 없이 남음), CURRENT_HANDOFF 맨 위(23 완료·남은 것·K1 재개 안내), VERIFICATION_LOG "phase 23 설정 UX", INTERNAL_REQUEST_LIVE_RUN_1 "준비 (관리자 R)" 를 새 화면 경로로 고친다(진행 표는 그대로).
5. 제품 코드에서 결함을 찾으면 고치고 summary 에 적는다(테스트 먼저).

## 테스트 먼저

- 위 e2e 와 업그레이드 e2e, CSS·마크업 단정.

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
- 셀프호스트에 업그레이드를 적용하지 마라. 이유: 실사용 환경 — 적용은 phase 뒤 사용자 지시.
