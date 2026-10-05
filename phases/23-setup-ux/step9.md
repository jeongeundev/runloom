# Step 9: settings-page — 설정 화면 — 탭 정리·내부 값은 자세히로

## 읽어야 할 파일

- AGENTS.md
- phases/23-setup-ux/README.md (사용자 결정 D1~D4·계획 기본값 — 이 phase 의 기준), phases/23-setup-ux/index.json (이전 step summary)
- docs/ARCHITECTURE.md "설정 UX — phase 23" (step 0 이 쓴 이름·주소·시그니처·문구 — README 와 다르면 ARCHITECTURE 가 기준)
- docs/adr/0028-*.md (step 0 이 쓴 ADR), docs/UI_GUIDE.md, docs/GLOSSARY.md
- docs/ARCHITECTURE.md "설정 UX — phase 23" 의 설정 화면 절·노출 단정 표식·step 9 시그니처
- step 1·5 가 만든 `/settings` 화면과 탭 템플릿(종류·규칙·판단·알림·n8n 입구·고급), `src/workflow/server/web.py` `_inbound_context`·`_advanced_context`, `src/workflow/server/views.py` `triage_settings_context`·`notifications_context`
- 테스트: `test_web_triage_settings.py`, `test_web_notifications.py`, `test_web_connect.py`, `tests/e2e/test_monitor.py`(판단 기준 링크가 있으면)

먼저 실제 파일을 읽는다. 대화 이력을 전제로 판단하지 않는다. 이전 step 에서 만든 코드를 꼼꼼히 읽고 설계 의도를 이해한 뒤 작업한다.

## 작업

1. 탭 순서·이름은 ARCHITECTURE. n8n 입구 탭 = 입구 주소·토큰 목록·토큰 발급·요청 예시·callback 허용 목록(지금 sources 탭 아래 내용).
2. 규칙 표: `버그 수정 --[ready_for_review]--> 커밋 검토` 같은 화살표 문구를 화면 이름·결과값 라벨로(ARCHITECTURE 문구), 넘기는 산출물은 라벨. 규칙 등록 폼의 산출물 체크박스는 라벨 먼저.
3. 판단·알림 탭: 내부 ID·코드는 "자세히" 안으로.
4. 고급 탭은 수동 등록 폼·연결 코드 그대로(예외 — 상단에 "개발·운영용" 안내 한 줄).

## 테스트 먼저

- 탭 순서·권한, n8n 입구 탭 내용과 토큰 발급 POST 뒤 303.
- 규칙 표 문구.
- 고급 탭 외 설정 화면 본문(자세히 제외) 내부 ID 노출 없음.

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
- 판단 기준·알림 저장 동작을 바꾸지 마라. 이유: 표시만 바꾼다.
