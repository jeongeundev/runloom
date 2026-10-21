# Step 1: member-token-store — 스키마 v26 멤버 토큰 저장

## 읽어야 할 파일

- AGENTS.md, phases/24-human-api/README.md("결정" D12~D23·D32), phases/24-human-api/index.json(이전 step summary)
- docs/ARCHITECTURE.md "사람용 API — phase 24"(step 0 이 고정한 `member_tokens` 표·repo 시그니처 — **README 와 다르면 ARCHITECTURE 가 기준**), docs/adr/0032-*.md, docs/GLOSSARY.md "멤버 토큰"
- `src/workflow/adapters/db.py`(DDL·`SCHEMA_VERSION = 25`·`_migrate_24_to_25` 형식·마이그레이션 목록), `src/workflow/adapters/repo.py`(`_sha256`·`create_login_session`·`member_for_login_token`·`revoke_login_sessions`·`disable_member`·`enable_member`)
- 테스트(새 표·버전 때문에 함께 고칠 것):
  - `tests/workflow/adapters/test_db.py` — 표 집합(70 근처 `V15_TABLES = TABLES - PHASE20_TABLES` 등 집합 정의에 새 표 반영), 3160 근처 `sql_after` 비교 필터에 `member_tokens`, 스키마 버전 단정
  - `tests/workflow/server/test_backup.py` — 표 이름·행 수 dict(360·510·660·836 근처 — `member_tokens: 0` 추가), `SCHEMA_VERSION == 25` 단정(664·840·896·950·998·1077 근처)
  - `tests/workflow/adapters/test_internal_request_store.py:84` 근처 `== 25`
  - 그 밖: `grep -rn "SCHEMA_VERSION\|user_version" tests` 와 `grep -rn "login_sessions" tests` 로 더 찾는다
  - fixture: `tests/workflow/adapters/fixtures/` 에 v25 SQL 은 없다(가장 최신이 v24). v25 → v26 마이그레이션 테스트는 v24 fixture 를 열어 마이그레이션 체인(24→25→26)으로 올린 뒤 단정한다(v25 fixture 를 새로 만들지 않는다).

## 작업

1. `adapters/db.py`: 새 DB DDL 과 마이그레이션 `_migrate_25_to_26` 에 `member_tokens` 표(ARCHITECTURE 표 그대로)를 더하고 `SCHEMA_VERSION = 26`. 다른 표는 건드리지 않는다. 마이그레이션 끝에 기존 형식대로 `PRAGMA foreign_key_check`.
2. `adapters/repo.py`: ARCHITECTURE 의 시그니처대로 `issue_member_token`·`list_member_tokens`·`revoke_member_token`·`member_for_api_token`·`touch_member_token`·`revoke_member_tokens`. 핵심 규칙:
   - DB 에는 `token_sha256` 만. 원문은 `issue_member_token` 반환값으로 한 번만 나간다. `list_member_tokens` 결과에 해시·원문이 없다.
   - `member_for_api_token` 은 원문 형식(`wfm_` 접두어)이 아니면 DB 를 보지 않고 None. 폐기된 토큰·비활성 멤버면 None. 결과에 `session_id` 를 싣는다(워크스페이스 일치 검사는 step 2 의 auth 가 한다 — `SELFHOST_SESSION_ID` 는 `server/auth.py` 에 있어 adapters 에서 쓰지 않는다).
   - `revoke_member_token` 은 그 멤버의 토큰만 폐기(남의 token_id 면 False, 이미 폐기면 False). 행을 지우지 않고 `revoked_at`.
   - `disable_member` 가 같은 트랜잭션에서 그 멤버의 열린 토큰을 모두 폐기한다. `enable_member` 는 토큰을 되살리지 않는다(D14).
   - 비밀번호 변경·재설정 함수는 토큰을 건드리지 않는다(D14).
   - `touch_member_token` 은 `last_used_at` 이 없거나 60초 이상 지났을 때만 UPDATE(D22).
3. 스키마 버전을 박은 기존 테스트·fixture 를 26 기준으로 고친다(v25 DB 에서 v26 으로 올라가는 마이그레이션 테스트 포함).

## 테스트

- v25 DB → v26 마이그레이션: 표 생성, 기존 행 그대로, `foreign_key_check` 통과.
- 발급 → 원문이 `wfm_` 로 시작, DB 의 모든 칸에 원문이 없음(SELECT * 문자열 검사) → `member_for_api_token(원문)` 이 멤버·scope 를 돌려줌.
- 폐기 → None. 남의 토큰 폐기 → False. 비활성화 → 모든 토큰 None, 다시 활성화해도 None. 비밀번호 변경 뒤에도 토큰 유효.
- `touch_member_token` 60초 규칙, `scope` 가 `read`·`full` 밖이면 거부.

## 금지사항 (이 step)

- 서버 라우트·auth·화면을 만들지 마라. 이유: step 2·3 범위.
- 원문 토큰을 로그·예외 메시지에 넣지 마라. 이유: AGENTS.md 비밀값 규칙.

## 테스트 먼저

소스·템플릿을 바꾸기 전에 `tests/` 미러 경로(`src/workflow/x/y.py` → `tests/workflow/x/test_y.py`)에 실패하는 테스트를 먼저 작성하고, 실패 원인이 의도한 것인지 확인한다(`tdd-guard.sh` 가 테스트 없는 소스 작성을 막는다). 구현한 뒤 해당 테스트와 전체 회귀를 통과시킨다. 이 step 의 변경으로 기존 테스트(주소·문구 단정)가 깨지면 새 동작 기준으로 고치되 단정을 약하게 만들지 않는다. 무엇을 왜 바꿨는지는 summary 에 남긴다.

## Acceptance Criteria

```bash
python3 -m pytest -q
python3 -m ruff check .
```

위 커맨드와 이 step 의 "테스트" 항목을 모두 확인한다.

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 규칙을 확인한다.
   - `domain/` 은 FastAPI·sqlite3·HTTPX·subprocess·Git 을 import 하지 않는다.
   - `server/` 와 `connector/` 는 서로 import 하지 않고 `contracts/` 만 공유한다.
   - 외부 입력(요청 본문·쿼리·토큰 이름)에서 명령·경로를 받아 실행하지 않는다.
   - 비밀값(연결 토큰 `wfc_…`, **멤버 토큰 `wfm_…` 원문**, `OPERATOR_TOKEN`, `SESSION_SECRET`, GitHub App 비밀·설치 토큰, PAT, 로그인·초대 링크 원본)을 DB·로그·응답(발급 응답 1회 제외)·템플릿·백업에 넣지 않는다. DB 에는 `token_sha256` 만.
   - 웹 폼과 API 는 같은 도메인·`work_actions`·`views` 함수를 쓴다. API 전용 규칙(권한·상태 전이·중복 방지)을 따로 만들지 않는다.
   - GLOSSARY 이름을 그대로 쓴다(`WorkItem`·업무 키, `Execution` ≠ run, `Agent` ≠ connector, `outcome` ≠ 상태).
   - 종류 이름(`bug_fix`·`code_review`·`triage`)으로 새 분기를 만들지 않는다(ADR-0009).
3. 성공이면 `phases/24-human-api/index.json` 의 이 step 만 `completed` 로 바꾸고 `summary`(생성·수정 파일, 결정, 테스트 수, 다음 step 주의점 한 줄)와 `verification`(실제 실행한 AC 명령과 종료 코드 목록 `[{"command": "...", "exit_code": 0}]`)을 남긴다. 끝냈지만 걸리는 점(정하지 않고 넘어간 것, 확인 못 한 것)은 `concerns` 에.
4. 3회 수정 후에도 실패하면 `error` + `error_message`. 사용자 결정·외부 자원이 필요하면 `blocked` + `blocked_reason` 후 즉시 중단. 실행하지 않은 검증을 `completed` 로 표시하지 않는다.

## 금지사항 (공통)

- 실제 Claude/Codex·GitHub·Jira·Discord·외부 웹훅을 호출하지 않는다. 이유: 가짜 클라이언트·`MockTransport`·FastAPI `TestClient` 로 검증한다.
- 사용자가 띄워 둔 환경을 읽거나 바꾸지 않는다: 셀프호스트(compose 프로젝트 `runloom`, 포트 8000, 볼륨 `runloom_workflow-data`)·`deploy/selfhost/.env`·러너 홈(`~/Library/Application Support/workflow-connector*`)·launchd(`launchctl` 실행 금지)·`~/.claude/`·GitHub 저장소 `jeongeundev/*`. 컨테이너를 띄우거나 멈추지 않는다. 셀프호스트에 업그레이드를 적용하지 않는다. 이유: 실사용 환경 — 적용은 phase 뒤 사용자 지시.
- 새 의존성(Python 패키지, JS·CSS 프레임워크, CDN)을 추가하지 않는다. 이유: ADR-0002.
- 화면(HTML) 라우트·화면 폼 POST 경로(`/tasks…`, `/work/{key}/…`, `/me…`, `/responsibilities/add`·`/remove`, `/sources/tokens…`, `/operator/…`, `/operator/github/app/callback`, `/requests/…`, `/team…` 등)와 `/healthz` 를 옮기지 않는다. 이유: README 결정 D10 — JSON API 만 `/api/v1` 로 옮긴다. 경로 문자열 일괄 치환을 하지 마라 — 같은 접두어의 화면 폼이 깨진다.
- 사용자 변경을 삭제하거나 stash·reset 하지 않는다. 커밋은 하네스가 한다.
- 이 step 범위 밖 모듈·화면을 "개선"하지 않는다. 기존 테스트의 단정을 약하게 만들어 통과시키지 않는다.
- 기존 테스트를 깨뜨리지 마라.
