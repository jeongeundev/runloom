# Step 2: 인증 — 고정 워크스페이스와 로그인

## 읽어야 할 파일

- AGENTS.md
- phases/10-selfhost/README.md (계획 기본값 — 이 phase 의 기준)
- phases/10-selfhost/index.json (완료 step 의 summary)
- docs/adr/0016-selfhost-docker-fixed-workspace.md (step 0 산출물. step 0 에서는 새로 만든다)
- docs/ARCHITECTURE.md ("셀프호스트 — phase 10" 절. step 0 에서는 새로 만든다)
- docs/GLOSSARY.md
- src/workflow/server/auth.py (`require_session`, `require_operator`, `_session_from_cookie`)
- src/workflow/server/web.py (`/operator/login`, `_base`, `is_operator` 사용처), src/workflow/adapters/repo.py (sessions)
- src/workflow/server/human_api.py, src/workflow/server/metrics_api.py, src/workflow/server/inbound_api.py, src/workflow/server/machine_api.py (각 API 가 거는 의존성)
- tests/workflow/server/ (auth·web 테스트)

먼저 실제 파일과 이전 step 산출물을 읽는다. 이름·환경변수·경로는 step 0 의 ADR-0016·ARCHITECTURE 가 고정한 것을 따르고, 바꿔야 하면 같은 이름의 문서·테스트를 함께 갱신한다. 대화 이력을 전제로 판단하지 않는다.

## 작업

selfhost 모드만 바뀐다. demo 모드 동작은 한 줄도 바뀌지 않아야 한다.

1. 고정 워크스페이스: step 0 이 정한 방식으로 워크스페이스 세션 id 하나를 찾거나 만든다(`is_operator=1`, 내장 종류 seed 는 기존 `create_session` 경로 그대로).
2. `POST /login`(폼: token) — `OPERATOR_TOKEN` 과 `hmac.compare_digest` 로 비교. 맞으면 고정 워크스페이스 id 를 서명한 쿠키(HttpOnly, SameSite=Lax, 기간은 step 0 값)를 심고 `/` 로. 틀리면 로그인 화면에 오류(토큰 값은 어디에도 남기지 않음). `POST /logout` — 쿠키 삭제.
3. `require_session`(selfhost): 유효한 워크스페이스 쿠키가 아니면 새 세션을 만들지 않는다. HTML 요청은 `/login` 으로 303, JSON API 는 401 `unauthenticated`. `require_operator`(selfhost): 로그인 = 운영자.
4. 기존 `/operator/login` 은 selfhost 에서 `/login` 과 같은 동작(또는 리다이렉트)으로 둔다.
5. 연결 토큰(러너)·입구 토큰(n8n) 인증은 바꾸지 않는다. 입구 토큰이 가리키는 세션이 고정 워크스페이스인지는 기존 규칙대로 토큰 행이 정한다.
6. 로그인 시도 제한: 같은 프로세스에서 짧은 시간 연속 실패 시 지연 또는 429 — 단순한 메모리 카운터로 충분하다(step 0 에 적은 값).

## 테스트 먼저

auth·web 테스트(selfhost 설정으로 만든 앱): 쿠키 없이 화면 → /login, API → 401, 새 세션 행이 생기지 않음; 로그인 성공 → 같은 워크스페이스, 두 번째 "브라우저"(새 클라이언트)로 로그인해도 같은 워크스페이스 데이터가 보임; 틀린 토큰 거부·토큰 값이 응답·로그에 없음; 로그아웃; 연속 실패 제한; 러너·n8n 토큰 경로 회귀. demo 모드 기존 테스트 전부 통과.

소스·배포 파일 변경 전에 `tests/` 미러 경로(배포 파일은 `tests/test_deploy_files.py` 또는 새 `tests/test_selfhost_files.py`)에 실패 테스트를 작성하고 실패 원인이 의도한 것인지 확인한다(`tdd-guard.sh` 가 테스트 없는 소스 작성을 막는다). 구현 후 해당 테스트와 전체 회귀를 통과시킨다.

## Acceptance Criteria

```bash
python3 -m pytest -q
python3 -m ruff check .
```

위 커맨드와 이 step 의 테스트 항목을 모두 확인한다.

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 규칙을 확인한다: `domain/` 은 FastAPI·sqlite3·HTTPX·subprocess·Git 을 import 하지 않는다 / `server/` 와 `connector/` 는 서로 import 하지 않는다 / 비밀값(`OPERATOR_TOKEN`, `SESSION_SECRET`, `WORKFLOW_GITHUB_TOKEN`, 연결 토큰 등)은 환경변수·0600 파일에서만 읽고 DB·로그·응답·템플릿·이미지 레이어에 넣지 않는다 / 기본 모드(demo)의 기존 동작과 테스트가 그대로다.
3. 성공이면 `phases/10-selfhost/index.json` 의 step 2 만 `completed` 로 바꾸고 `summary` 에 생성·수정 파일, 결정, 검증 결과(테스트 수), 다음 step 이 주의할 점을 한 줄로 남긴다.
4. 3회 수정 후에도 실패하면 `error` + `error_message`, 사용자 결정·외부 자원이 필요하면 `blocked` + `blocked_reason` 을 기록한다. 실행하지 않은 검증을 `completed` 로 표시하지 않는다.

## 금지사항

- `main`·공개 데모 VM·`deploy/install-vm.sh`·`deploy/systemd/` 를 바꾸지 않는다. 이유: 공개 데모는 기존 경로 그대로 유지한다(ADR-0006·0008).
- 기본 모드(`WORKFLOW_MODE` 미설정 = demo)의 동작을 바꾸지 않는다. 이유: 기존 테스트와 공개 데모가 익명 세션을 전제로 한다.
- 실제 GitHub·유료 모델을 호출하지 않는다.
- step 8 이 아니면 `docker compose up`·`docker run` 으로 컨테이너를 띄우지 않는다(`docker compose config` 같은 정적 검사는 가능). 이유: 실제 기동 검증은 step 8 한 곳에서 격리된 프로젝트 이름·포트로 한다.
- 사용자 변경을 삭제하거나 stash·reset 하지 않는다. `data/` 아래 기존 DB 를 건드리지 않는다. 커밋은 하네스가 한다.
- 이 step 범위 밖 모듈을 "개선"하지 않는다.
