# Step 5: login-web — 로그인·첫 설정·복구·초대 가입·로그아웃과 테스트 로그인

## 읽어야 할 파일

- AGENTS.md
- phases/15-team/README.md (조사 결과·계획 기본값 11가지 — 이 phase 의 기준), phases/15-team/index.json (이전 step summary)
- docs/ARCHITECTURE.md ("팀 — phase 15" 첫 설정·복구 흐름·쿠키)
- src/workflow/server/web.py (`/`·`/login`·`/logout`·`_workspace_login`·`_base`), src/workflow/server/auth.py (step 4), templates `login.html`·`base.html`·`_sidebar.html`
- tests/workflow/server/conftest.py (`log_in`·`logged_in_client`), tests/workflow/server/test_web.py (로그인 절, `login()` 헬퍼), tests/workflow/server/test_github_api.py (`login()` 과 `verify_session` 단정), `/login` 을 직접 부르는 테스트(test_web_github_connect·test_web_notifications·test_web_cycle·test_web_runner·test_web_metrics)
- tests/e2e/conftest.py 와 `OPERATOR_TOKEN` 으로 로그인하는 e2e 파일들(test_selfhost.py 등)

먼저 실제 파일을 읽는다. 대화 이력을 전제로 판단하지 않는다. 이전 step 이 정한 이름은 `docs/ARCHITECTURE.md` "팀 — phase 15" 를 따른다(README 와 다르면 ARCHITECTURE 가 기준).

## 작업

1. `/login` GET: 첫 설정이 필요하면(`needs_first_setup`) 토큰 칸 + "처음 설정" 안내, 아니면 이메일·비밀번호 칸과 "토큰으로 복구" 링크. POST: 첫 설정 단계에서는 토큰이 맞으면 짧게 유효한 서명 값(`SESSION_SECRET` HMAC, 10분)을 쿠키로 주고 `/setup` 으로. 이메일 로그인은 성공 시 로그인 세션 + 쿠키 → `/tasks`. 실패 문구는 이메일 존재 여부를 드러내지 않는다.
2. `/setup` GET·POST: 첫 설정 단계이고 위 서명 값이 있을 때만. 이메일·표시 이름·비밀번호(확인 칸) → 기존 첫 관리자 멤버에 계정 설정(없으면 관리자 새로) → 로그인. 첫 설정이 끝난 뒤에는 `/login` 으로 303.
3. `/login/recover` GET·POST: 토큰 + 관리자 이메일 + 새 비밀번호 → 그 관리자 비밀번호 바꾸고 기존 세션 폐기. 토큰 틀림·관리자 아님은 같은 문구. 실패 제한은 복구 경로 키.
4. `/invite/{token}` GET·POST: 유효하면 이메일·표시 이름·비밀번호 폼 → 가입·로그인. `purpose=reset` 이면 새 비밀번호 폼 → 사용 → `/login`. 무효·만료·사용됨은 같은 안내 화면(토큰은 로그·화면에 되찍지 않는다).
5. `/logout`: 로그인 세션 폐기 + 쿠키 삭제.
6. 사이드바 하단에 로그인한 멤버 표시 이름·역할과 로그아웃.
7. 테스트 헬퍼: `conftest.log_in(client)` 의 시그니처와 반환을 유지하고 안에서 "첫 설정 → 관리자 계정 → 로그인" 을 거치게 한다(기본 관리자 이메일·비밀번호 상수). 멤버로 로그인하는 헬퍼(`log_in_member(client, role=...)` 등)를 더한다. `/login` 에 토큰을 직접 POST 하거나 옛 쿠키를 단정하던 테스트를 새 흐름으로 고친다 — 단정의 강도는 유지한다. e2e 로그인도 같은 흐름으로 고친다.
8. 권한 판정 전환은 step 6 이다. 이 step 에서는 로그인한 사람이 기존 `is_operator` 판정을 통과하도록(워크스페이스 `is_operator=1` 유지) 두어 기존 화면이 그대로 열리게 한다.

## 테스트 먼저

`tests/workflow/server/test_web.py`(로그인 절) 또는 미러 경로 새 파일: 첫 설치 → 토큰 → 계정 만들기 → 로그인 한 줄기, 첫 설정 뒤 `/setup` 막힘, 서명 값 없이 `/setup` 거부, 이메일 로그인 성공·실패(문구 동일)·제한 429, 복구로 비밀번호 변경·기존 세션 무효, 초대 가입·재설정 링크 사용·무효 링크, 로그아웃 뒤 쿠키 재사용 거부, 응답 본문·로그에 토큰·비밀번호 원문 없음.

소스·배포 파일 변경 전에 `tests/` 미러 경로에 실패 테스트를 작성하고 실패 원인이 의도한 것인지 확인한다(`tdd-guard.sh` 가 테스트 없는 소스 작성을 막는다). 구현 후 해당 테스트와 전체 회귀를 통과시킨다.

## Acceptance Criteria

```bash
python3 -m pytest -q
python3 -m ruff check .
WORKFLOW_E2E=1 python3 -m pytest tests/e2e -q   # e2e 로그인 헬퍼를 고쳤으므로
```

위 커맨드와 이 step 의 테스트 항목을 모두 확인한다.

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 규칙을 확인한다: `domain/` 은 FastAPI·sqlite3·HTTPX·subprocess·Git 을 import 하지 않는다 / `server/` 와 `connector/` 는 서로 import 하지 않고 `contracts/` 만 공유한다 / 외부 입력(요청 본문·폼·초대 토큰·이슈 본문·모델 응답)에서 명령·경로를 받아 실행하지 않는다 / 비밀값(연결 토큰, `OPERATOR_TOKEN`, `SESSION_SECRET`, 로그인 세션·초대·재설정 토큰 원문, 비밀번호 원문, GitHub App 비밀·설치 토큰, PAT, 공용·개인 알림 웹훅 URL)은 DB·로그·응답·템플릿·백업에 넣지 않는다(토큰은 sha256, 비밀번호는 scrypt 해시만 DB 에) / GLOSSARY 이름을 그대로 쓴다 / phase 8·11·12·14 의 GitHub 순환(수집 → 수정 → 검토 → 초안 PR → 병합 추적 → 업무 완료)이 그대로 동작한다.
3. 성공이면 `phases/15-team/index.json` 의 step 5 만 `completed` 로 바꾸고 `summary` 에 생성·수정·삭제 파일, 결정, 검증 결과(테스트 수), 다음 step 이 주의할 점을 한 줄로 남긴다.
4. 3회 수정 후에도 실패하면 `error` + `error_message`, 사용자 결정·외부 자원이 필요하면 `blocked` + `blocked_reason` 을 기록한다. 실행하지 않은 검증을 `completed` 로 표시하지 않는다.

## 금지사항

- 실제 GitHub(api.github.com·github.com)·Discord·외부 웹훅을 호출하지 않는다(공식 문서 읽기는 허용). 이유: 모든 step 은 httpx `MockTransport`·가짜 GitHub·가짜 알림 수신으로 검증한다. 실연동은 phase 뒤 사용자와 함께 한다.
- 사용자가 띄워 둔 셀프호스트(compose 프로젝트 `runloom`, 포트 8000, 볼륨 `runloom_workflow-data`)·`deploy/selfhost/.env`·러너 홈(`~/Library/Application Support/workflow-connector/`)·launchd·`/Users/kje/demo/*` 를 읽거나 바꾸지 않는다. 컨테이너를 띄우거나 멈추지 않는다. 이유: 실사용 중인 환경이다.
- 새 의존성(argon2·bcrypt·passlib·itsdangerous·SMTP 라이브러리 등)을 추가하지 않는다. 이유: 표준 `hashlib.scrypt`·`secrets`·`hmac` 으로 충분하고 ADR-0002 가 의존성을 최소로 둔다.
- 이메일 발송·외부 로그인(OAuth)·여러 워크스페이스·2단계 인증을 만들지 않는다. 이유: ADR-0021 범위 밖(README "하지 않는 것").
- 종류 이름(`bug_fix`·`code_review`)으로 새 분기를 만들지 않는다. 이유: 업무 종류·후속 규칙은 워크스페이스 등록 데이터다(ADR-0009, AGENTS.md).
- `tasks`·`members`·`notifications` 등 기존 표를 재생성하지 않는다. 이유: v11 은 ALTER + 새 표로 충분하다(README 계획 기본값 11).
- 담당자별 묶음·보드·상세 패널 같은 새 업무 화면을 만들지 않는다. 이유: 16-work-ui 범위.
- 사용자 변경을 삭제하거나 stash·reset 하지 않는다. 커밋은 하네스가 한다.
- 이 step 범위 밖 모듈을 "개선"하지 않는다. 기존 테스트의 단정을 약하게 만들어 통과시키지 않는다.
