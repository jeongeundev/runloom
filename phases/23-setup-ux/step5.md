# Step 5: kind-form — 종류 등록 폼 — 이름·지시·결과값·맡을 에이전트

## 읽어야 할 파일

- AGENTS.md
- phases/23-setup-ux/README.md (사용자 결정 D1~D4·계획 기본값 — 이 phase 의 기준), phases/23-setup-ux/index.json (이전 step summary)
- docs/ARCHITECTURE.md "설정 UX — phase 23" (step 0 이 쓴 이름·주소·시그니처·문구 — README 와 다르면 ARCHITECTURE 가 기준)
- docs/adr/0028-*.md (step 0 이 쓴 ADR), docs/UI_GUIDE.md, docs/GLOSSARY.md
- docs/ARCHITECTURE.md "설정 UX — phase 23" 의 종류 폼 절·문구 표·step 5 시그니처
- step 1·4 가 만든 것: `/settings?tab=kinds` 화면, `add_agent_capability`·범위 helper·`delete_kind` 능력 떼기
- `src/workflow/server/web.py` `kinds_create`·`_kinds_context`·`/kinds/{kind}/delete`·`_validation_page_error`·`INPUT_KIND_CHOICES`, `_connect_kinds.html`(또는 step 1 이 바꾼 이름), `src/workflow/contracts/v1.py` `KindSpec`·`KIND_PATTERN`
- 테스트: `tests/workflow/server/test_web_connect.py`·`test_web.py` 의 종류 등록 테스트

먼저 실제 파일을 읽는다. 대화 이력을 전제로 판단하지 않는다. 이전 step 에서 만든 코드를 꼼꼼히 읽고 설계 의도를 이해한 뒤 작업한다.

## 작업

1. 기본 칸: 화면 이름(필수) · 지시문 · 결과값(기본 채움 값은 ARCHITECTURE) · 맡을 에이전트 체크박스(줄 = 에이전트 이름 · 저장소 · `<소유자>의 Mac`, 고를 수 없는 에이전트는 비활성 + 이유).
2. "고급" `<details>`: 종류 식별자(비우면 자동 — ARCHITECTURE 규칙) · 능력 코드(비우면 종류 식별자) · scope_key(기본 `repository_id`) · 받는 산출물 체크박스(라벨 먼저, 내부 이름은 흐리게).
3. 등록 = `insert_kind` + 고른 에이전트마다 `add_agent_capability(Capability(code=capability_code, scope={scope_key: 범위 값}))` 를 한 트랜잭션. scope_key 가 `repository_id` 가 아니면 에이전트 선택을 막고 안내(ARCHITECTURE).
4. 종류 카드: 화면 이름 · 받는/내는 산출물 라벨 · 결과값 · 맡을 수 있는 에이전트 이름들. kind·capability_code·scope_key 는 카드의 "자세히" 안으로. 사용자 정의 종류 [삭제] 는 그대로(능력 떼기는 step 4).
5. 오류 문구는 칸 옆 인라인(지금 `_validation_page_error` 형식 유지).

## 테스트 먼저

- 화면 이름·지시문·결과값·에이전트 2명만 넣고 등록 → 종류 생성(자동 식별자가 `KIND_PATTERN` 안), 두 에이전트에 능력, 후보에 나옴.
- 고급에서 식별자·능력 코드 직접 지정, 식별자 충돌 오류.
- 고를 수 없는 에이전트는 체크박스 비활성 + 이유, 위조 POST 로 보내도 거부.
- 카드 본문에 내부 코드 없음("자세히" 안에만).
- 권한: `manage_rules` 없으면 폼 없음·POST 403.

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
- 내장 종류의 정의·능력을 바꾸지 마라. 이유: ADR-0009·러너 프로토콜.
