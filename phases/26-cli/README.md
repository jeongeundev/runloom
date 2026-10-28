# Phase 26 — CLI: 터미널의 에이전트가 내 다음 업무와 인계 내용을 직접 가져가고, 결정·응답·착수까지 한다

작성일: 2026-10-27. 상태: 설계 완료, 실행 전. **`main` 에서 실행하고 `--pr` 로 `main` 행 PR 을 연다**([ADR-0030](../../docs/adr/0030-pr-ci-review-flow.md)). 근거: [ADR-0031](../../docs/adr/0031-handoff-understanding-and-clients.md) 결정 3(API 우선)·6(순서 ③ "API 를 감싼 CLI — 에이전트가 '내 다음 업무와 인계 내용'을 직접 가져감"), [PRD](../../docs/PRD.md) 수용 항목 "웹 화면 없이 API(또는 CLI)로 업무 조회·리포트 조회·결정 응답·착수가 됨", [ADR-0032](../../docs/adr/0032-human-api-and-member-tokens.md)(사람용 API·멤버 토큰), [ADR-0033](../../docs/adr/0033-handoff-report.md)(인계 리포트).

## 왜

①(phase 24)로 사람용 기능이 `/api/v1` JSON API 와 멤버 토큰(`wfm_…`)으로 열렸고, ②(phase 25)로 단계마다 근거가 붙은 인계 리포트가 생겼다. 그러나 터미널에서 Claude Code·Codex 를 켜 둔 사람이 "내 차례 업무가 뭐고, 앞 사람이 무엇을 왜 했는지"를 에이전트에게 넘기려면 여전히 웹을 열어 복사해야 한다. curl 로 API 를 부를 수는 있지만 토큰 보관·여러 API 조합·리포트 읽기 형태를 매번 사람이 맞춰야 한다.

이 phase 가 끝나면 `runloom next` 한 줄로 내 차례 업무 하나와 그 업무의 최신 인계 리포트가 에이전트가 읽기 좋은 한국어 텍스트(또는 `--json`)로 나오고, 같은 CLI 로 리포트 결정 항목에 답하고, 사람 요청에 응답하고, 직접 작업을 시작·그만둔다.

## 범위

- 포함: `runloom` CLI(`login`·`logout`·`list`·`next`·`show`·`report`·`decide`·`respond`·`start`·`stop`, 공통 `--json`), 자격 파일, 서버 `GET /api/v1/me`·리포트 보기의 요청자 id, 러너의 `RUNLOOM_*` 예약 이름·`wfm_` 로그 마스킹, 사용 문서.
- 제외: 직접 작업 리포트(D2), 맡기기·우선순위·판단 제안 처리·맞춤 풀이 요청·리포트 다시 만들기(D1), Claude Code 스킬 등 에이전트 전용 파일(D26), Cloudflare Access 뒤 서버(D27), 별도 배포 패키지(D4), IDE 앱(ADR-0031 ④), 셀프호스트 업그레이드 적용(phase 뒤 사용자 지시).

## 결정

처리 표기: [사용자] 사용자 답 · [근거] 문서·코드에서 바로 따라 나옴 · [조정값] 기본값, 조정 가능 · [N/A — 근거] 묻지 않음과 근거. "(검사 후)" 는 독립 검사 1(2026-10-27) 지적으로 사용자에게 다시 물었거나 처리를 바꾼 항목.

### 구조
D1. 범위 = 조회(`list`·`next`·`show`·`report`) · 결정 답(`decide`) · 사람 요청 응답(`respond`) · 직접 작업 시작/그만(`start`·`stop`). 맡기기·우선순위·판단 제안 수락/무시·맞춤 풀이 요청·리포트 다시 만들기는 제외. [사용자]
D2. 직접 작업(에이전트 실행 없는 업무)의 리포트 → 이번엔 제외, 뒤로 미룸(ADR-0033 이 ③ 으로 미룬 항목을 다시 미룸). [사용자]
D3. 서버 변경은 `GET /api/v1/me` 추가와 리포트 보기(`report_view`)의 `requested_by_member_id` 칸 추가뿐(D28). 스키마 그대로(v27), 러너 프로토콜 그대로. 나머지 명령은 기존 `/api/v1` 를 쓴다. (검사 후) [사용자 + 근거: D1 의 다른 명령은 API 가 이미 있음]
D4. 같은 패키지(`workflow`) 안 모듈 `src/workflow/cli/`, pyproject console script `runloom`, `python3 -m workflow.cli` 도 같은 동작. 서버 의존성도 같이 설치된다. 패키지 분리는 ④ 앱 때 다시 본다. [사용자]
D5. `workflow.cli` 는 `workflow.contracts`·HTTPX·표준 라이브러리만 import 한다 — `server`·`connector`·`domain`·`adapters` 금지. AGENTS.md 아키텍처 규칙에 `cli` 를 더한다. 이 규칙은 `src/` 에만 적용되고 테스트는 서버 앱을 띄워도 된다. 경계를 확인하는 테스트(AST 로 `src/workflow/cli/*.py` import 검사)를 둔다. (검사 후) [근거: 기존 server↔connector 분리 규칙의 연장 + 조정값(검사 테스트)]
D6. 테스트는 `tests/workflow/cli/`. CLI 의 HTTP 클라이언트는 `httpx.Client` 주입을 받고, 테스트는 실제 앱의 `TestClient`(httpx.Client 하위 클래스)를 주입해 계약 어긋남을 잡는다. [조정값]

### 인증·설정
D7. `runloom login --server URL` 이 토큰을 표준 입력에서 읽는다(명령 인자로 받지 않는다 — 셸 기록). 저장 전에 `GET /api/v1/me` 로 확인해 실패하면 저장하지 않고, 성공하면 "<이름>(<역할>, 토큰 <read|full>)로 로그인했습니다." 를 출력한다. 저장 위치 `~/.config/runloom/credentials.json`(디렉터리 0700, 파일 0600, 임시 파일 → 원자 교체), 내용은 `server`·`token`. `RUNLOOM_TOKEN`·`RUNLOOM_SERVER` 환경변수가 있으면 파일보다 먼저 쓴다(각각 따로). [사용자 + 조정값(확인 경로·출력 문구)]
D8. `logout` 은 로컬 자격 파일만 지우고 "서버의 토큰은 웹 /me 에서 폐기하세요." 를 출력한다. 서버 토큰 폐기 API 는 부르지 않는다. (검사 후) [조정값]
D9. 자격 파일을 읽을 때 그룹·다른 사용자 권한 비트가 있으면 거절하고 `chmod 600 <경로>` 를 안내한다(exit 2). (검사 후) [조정값]
D10. 설정 홈 덮어쓰기 환경변수 `RUNLOOM_HOME`(테스트·여러 서버용) — 있으면 `$RUNLOOM_HOME/credentials.json`. `XDG_CONFIG_HOME` 은 따르지 않는다(경로 하나로 고정). (검사 후) [조정값]
D11. 서버 주소: `http://` 는 호스트가 `localhost`·`127.0.0.1`·`::1` 일 때만, 그 밖은 `https://` 만. `login` 에서 거절한다(사용자). 환경변수 `RUNLOOM_SERVER` 로 받은 주소에도 같은 규칙을 적용한다(exit 2). 끝의 `/` 는 지운다. 경로·쿼리가 붙은 주소는 거절한다([조정값]). (검사 후) [사용자 + 조정값(환경변수에도 적용)]
D12. 러너가 띄운 에이전트(등록 에이전트의 자동 실행)는 CLI 를 쓰지 않는다 — 사람이 켜 둔 에이전트만. 그래서 러너는 `RUNLOOM_TOKEN`·`RUNLOOM_SERVER` 를 등록 `--env` 예약 이름(`connector/masking.py` `RESERVED_ENV_NAMES`)에 넣어 거절하고, 로그 마스킹 패턴에 `wfm_` 를 더한다. 실행 환경은 이미 허용 목록(`codex_env`)이라 상속으로는 새지 않는다. (검사 후) [사용자 + 근거: AGENTS.md CRITICAL 비밀값은 Codex 프로세스 환경에 넣지 않는다]
D13. 러너 에이전트가 같은 Mac 의 자격 파일을 읽을 수 있는 문제 → N/A — 기존 러너 `token.json`(`wfc_`)과 같은 노출 수준이고, 러너 에이전트를 파일 시스템에서 격리하는 것은 이번 범위가 아니다.

### 동작
D14. `next` = `GET /api/v1/work-items?filter=my_turn` 결과를 우선순위(`high` > `normal` > `low`) → `updated_at` 오래된 순으로 정렬해 1건을 고르고 `show` 와 같은 출력을 낸 뒤, 나머지가 있으면 끝에 `내 차례 n건 더 — runloom list --filter my_turn`. 없으면 `내 차례 업무가 없습니다.`(exit 0). "오래 기다린 것" 을 `updated_at` 오래된 것으로 대신한다 — 웹 목록 순서(같은 우선순위에서 최근 변경 먼저)와 다를 수 있다. 내가 직접 작업 중인 업무는 후보가 아니다(웹 `내 차례` 필터와 같은 뜻). (검사 후) [사용자]
D15. `show KEY` = 업무 상세(키·제목·종류·우선순위·담당·상태·이유·다음 할 일·원본 링크·직접 작업 브랜치·열린 사람 요청과 그 `request_id`·허용 행동) + 리포트(D16). 내 차례가 아닌 업무도 볼 수 있다(API 권한 그대로). [사용자 + 근거 ADR-0032]
D16. 리포트 범위: 업무의 리포트 단계 중 **최신 원본 리포트의 `created_at` 이 가장 늦은 단계** 하나의 최신 원본을 펼치고, 그 단계에서 **내가 요청한** 맞춤 풀이(`requested_by_member_id` = `/me` 의 `member_id`, 기준 원본 `base_report_id` 가 그 단계의 원본 — 최신이든 이전이든) 중 가장 최근 것이 있으면 함께 펼친다. 기준이 최신 원본이 아니면(`stale`) `옛 리포트 기준` 을 붙인다(D-4 검사 후 [사용자]). 나머지 단계(와 이전 원본)는 `단계 이름 · report_id · 상태` 한 줄씩. 리포트가 하나도 없으면 `인계 리포트 없음`. (검사 후) [사용자]
D17. 리포트가 `done` 이 아니면 본문 대신 상태(`작성 중`·`리포트 없음(사유)`)를 출력한다. 맞춤 풀이가 `stale` 이면 `옛 리포트 기준` 표시를 붙인다. (검사 후) [조정값 — 웹 패널 문구와 맞춘다]
D18. `report REPORT_ID` = 리포트 한 건: 절(`changes`·`decisions`·`open_questions`·`to_confirm`·`constraints`·`remaining_work`)별 주장·주장 id·근거 id·근거 확인 안 됨 표시·`why` 없음은 `기록 없음`, 결정 항목(`to_confirm`)마다 `item_id` 와 현재 답. 절 제목은 웹 리포트 페이지와 같은 한국어. [사용자]
D19. `decide REPORT_ID ITEM_ID (--confirm | --change TEXT)` → `PUT /api/v1/reports/{id}/decisions/{item_id}`(`confirmed` / `changed`+`note`). 길이(1~500자)·답할 수 있는 사람(받는 사람·관리자) 판정은 서버 — 보낸 사람 등은 서버 403 을 그대로 보인다. 성공하면 바뀐 리포트를 `report` 와 같이 출력. (검사 후) [근거: ADR-0033 결정 10]
D20. `respond REQUEST_ID ACTION [--text TEXT] [--agent AGENT_ID]` → `POST /api/v1/human-requests/{id}/responses`. `expected_revision` 은 CLI 가 `GET /api/v1/human-requests` 에서 그 요청을 찾아 쓴다(없으면 `열린 사람 요청 <id> 가 없습니다.` exit 1). `response_id` 는 명령 1회마다 새 uuid. 응답할 수 있는 요청 범위·행동 검사는 서버(웹과 같음). [사용자 + 근거: 기존 API 계약]
D21. `start KEY` → `PUT /api/v1/work-items/{key}/direct-work`, 응답의 `direct.branch` 를 `브랜치: <이름>` 으로 출력. 로컬 git 은 실행하지 않는다. `stop KEY` → `DELETE …/direct-work`. [사용자]
D22. `list [--filter all|my_turn|unassigned|agent_working] [--repository OWNER/NAME] [--closed …]` — 기본 `all`, 값 집합은 API 와 같고 검사는 서버. 한 줄에 키·우선순위·상태·제목·담당. [조정값]
D23. 출력: 기본 한국어 텍스트(마크다운풍), `--json` 은 단일 API 호출 명령이면 응답 본문 그대로, 여러 호출을 합치는 `next`·`show` 는 `{"detail": <업무 상세 응답>, "reports": <업무 리포트 목록 응답>, "report": <펼친 원본 또는 null>, "tailored": <내 맞춤 풀이 또는 null>}`(+ `next` 는 `"more_my_turn": n`). `next` 에 업무가 없으면 같은 키에 `detail`·`reports`·`report`·`tailored` 모두 null, `more_my_turn` 0. `login --json` 은 `/me` 응답, `logout --json` 은 `{"deleted": bool}`. (D-4 검사 후 키 이름 `work` → `detail`) (검사 후) [사용자 + 조정값(합친 키)]
D24. 오류: 서버 오류 본문의 `code`·`message` 를 `오류 <status> <code>: <message>` 한 줄로 stderr, exit 1. 네트워크 실패·시간 초과(30초)도 exit 1. 사용법·설정 오류 exit 2. 401 이면 `runloom login 으로 다시 로그인하세요.` 를 덧붙인다. 자동 재시도 없음. 응답의 모르는 키는 무시한다(서버가 더 새 버전일 수 있음). (검사 후) [조정값]
D25. 읽기 전용 토큰으로 행동 명령 → 서버 403 `token_read_only` 를 D24 형식 그대로. 대화형 프롬프트 없음(`login` 의 표준 입력 제외). [근거: ADR-0032, ADR-0031 ③ — 에이전트가 쓰는 도구]
D26. 에이전트가 CLI 를 아는 방법 → `runloom --help`·명령별 `--help` 와 `docs/CLI.md` 만. Claude Code 스킬 등 도구 전용 파일은 만들지 않는다. [사용자]
D27. Cloudflare Access 뒤 서버 → 이번엔 미지원, `docs/CLI.md` 에 적는다. [사용자]
D28. `GET /api/v1/me` → `{"member_id", "display_name", "role", "token_scope"}`(`token_scope` 는 `read`·`full`, 쿠키 로그인은 `null`). `require_member_api`(쿠키·`wfm_` 모두, 읽기 전용 토큰 허용). `report_view` 에 `requested_by_member_id`(원본은 null) 추가 — 웹 템플릿은 쓰지 않는다. (검사 후) [사용자]

### 문서·운영
D29. 다른 Mac 의 팀원 설치 → `python3 -m pip install "git+https://github.com/jeongeundev/runloom@<태그 또는 커밋>"` 를 `docs/CLI.md` 첫 절에, 저장소 안에서는 `python3 -m pip install -e .`. (검사 후) [조정값]
D30. 문서: ADR-0034, ARCHITECTURE "CLI — phase 26" 절(명령·인자·출력·`--json` 형태·자격 파일·exit code·모듈 이름), CONTRACT 20절(`/api/v1/me` 예시), GLOSSARY phase 26 용어, `docs/CLI.md`, AGENTS.md 아키텍처 규칙·명령어 줄, SELFHOST 업그레이드 v27→(스키마 변화 없는) 서버 재설치 메모. [근거: 관례]
D31. 셀프호스트 영향: `/api/v1/me` 때문에 서버 재설치(`install.sh`)가 필요하고 스키마는 그대로. 러너 재설치는 D12 반영을 원할 때만(선택). [근거 D3·D12]

## step

| step | 이름 | 레이어 | 결정 |
|---|---|---|---|
| 0 | cli-design | 문서(ADR-0034·ARCHITECTURE·CONTRACT·GLOSSARY·AGENTS.md) | 전부 — 이름·인자·출력 형태 고정 |
| 1 | me-api | `server/` | D3·D28 |
| 2 | runner-secrets | `connector/masking.py` | D12 |
| 3 | cli-core | `cli/config.py`·`cli/client.py`·경계 테스트 | D5·D6·D7·D9·D10·D11·D24 |
| 4 | cli-read | `cli/` 명령(조회)·렌더·console script | D4·D7·D8·D14~D18·D22~D26 |
| 5 | cli-actions | `cli/` 명령(행동) | D19~D21·D24·D25 |
| 6 | usage-docs | `docs/CLI.md`·SELFHOST | D26·D27·D29~D31 |
