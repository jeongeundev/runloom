# Step 0: GitHub App 연결 설계 고정 — ADR·규칙·ARCHITECTURE

## 읽어야 할 파일

- AGENTS.md
- phases/11-github-app/README.md (계획 기본값 — 이 phase 의 기준)
- phases/11-github-app/index.json (완료 step 의 summary)
- docs/adr/0017-github-app-connection.md (step 0 산출물. step 0 에서는 새로 만든다)
- docs/ARCHITECTURE.md ("GitHub App 연결 — phase 11" 절. step 0 에서는 새로 만든다)
- docs/GLOSSARY.md
- docs/adr/0014-github-task-cycle.md, docs/adr/0016-selfhost-docker-fixed-workspace.md
- docs/ARCHITECTURE.md ("GitHub 업무 순환 — phase 8 계약" 절, "셀프호스트 — phase 10" 절)
- docs/github/README.md, docs/SELFHOST.md (GitHub 연결 절)
- src/workflow/contracts/github.py (`GitHubSourceConfig`·`AssigneeBinding`), src/workflow/adapters/github_client.py (`from_env`·헤더·오류 분류)
- src/workflow/server/github_api.py, src/workflow/server/github_sync.py, src/workflow/server/task_cycle.py, src/workflow/domain/task_readiness.py (담당 판정)
- src/workflow/server/web.py (`/operator/github`), src/workflow/server/templates/operator_github.html
- src/workflow/connector/discovery.py (`discovered` 에 보내는 값), src/workflow/server/machine_api.py (`RegistrationRequest.discovered`)
- src/workflow/server/backup.py (백업 대상), src/workflow/server/settings.py

먼저 실제 파일과 이전 step 산출물을 읽는다. 이름·경로·API 필드는 step 0 의 ADR-0017·ARCHITECTURE 가 고정한 것을 따르고, 바꿔야 하면 같은 이름의 문서·테스트를 함께 갱신한다. 대화 이력을 전제로 판단하지 않는다.

## 작업

문서만 바꾼다(제품 코드 없음). README "계획 기본값"을 결정 기록으로 옮기고 이후 step 이 쓸 이름을 고정한다.

1. **GitHub 공식 문서로 사실 확인** (WebFetch 가능하면 사용, 불가하면 확인 못 한 항목을 "미확인"으로 표시): App manifest 흐름(`https://github.com/settings/apps/new` 에 manifest 폼 POST, `state`, `redirect_url`·`setup_url`·`hook_attributes.active=false`·`default_permissions`·`public=false`), `POST /app-manifests/{code}/conversions` 응답 필드(id, slug, client_id, client_secret, webhook_secret, pem)와 1시간 제한, 설치 URL(`https://github.com/apps/{slug}/installations/new`)과 setup_url 로 돌아오는 파라미터(`installation_id`, `setup_action`), App JWT(RS256, iat/exp/iss 제약), `POST /app/installations/{id}/access_tokens`(만료), `GET /installation/repositories`(페이지). 필요한 최소 권한: Issues RW, Pull requests R, Metadata R, Contents R 필요 여부.
2. `docs/adr/0017-github-app-connection.md` 신설. 결정: 표준 연결 = 사용자 자신의 GitHub App(manifest 생성 → 설치 → 저장소 선택) / 인증 = App JWT → 설치 토큰(캐시, 만료 전 갱신) / 비밀 보관 = 데이터 볼륨의 0600 비밀 파일(`WORKFLOW_SECRET_DIR`, 기본 `/data/secrets`, demo·개발 기본 `data/secrets`) — DB·백업·로그·응답 제외, 화면은 연결 여부만 / 가져오기 = 설치 저장소의 열린 이슈 전부(`intake: all_open`), 실행은 지시한 것만([에이전트에게 맡기기] 또는 `trigger_label` 기본 `runloom`) / 자동 매칭 = 러너가 보고한 GitHub remote owner/name 과 같은 저장소의 에이전트·검증 프로필, 담당자 매핑 없으면 기본 담당 에이전트 / 웹훅 없음(127.0.0.1, 주기 조회 유지) / 대체 경로 = 고급 설정의 PAT 붙여 넣기(같은 비밀 파일) / 기존 환경변수 토큰·라벨 설정은 그대로 호환. 대안·기각 이유(환경변수만, OAuth App, 공용 App, 웹훅, 전부 자동 실행)를 적는다. ADR-0014 의 "전체 백로그는 받지 않는다"를 "전체를 목록에 가져오되 실행은 지시한 것만"으로 바꾼다고 적고 ADR-0014 파일에 한 줄 덧붙인다.
3. `AGENTS.md` 아키텍처 규칙의 비밀값 CRITICAL 줄을 "환경변수 또는 비밀 저장소(`adapters/secret_store.py`, 0600 파일)에서만 읽는다. DB·로그·응답·템플릿·백업·Codex 프로세스 환경에 넣지 않는다" 로 고치고 비밀값 목록에 App 개인 키·client secret·webhook secret·설치 토큰을 추가한다.
4. `docs/ARCHITECTURE.md` 에 "GitHub App 연결 — phase 11" 절: 흐름도(버튼 → GitHub App 생성 → callback 교환 → 설치 → setup → 소스 자동 생성 → 동기화), 경로 표(예: `GET /operator/github/app/new`, `GET /operator/github/app/callback`, `GET /operator/github/app/setup`, `POST /operator/github/token`, `POST /tasks/{id}/delegate` — 이름을 확정), state(CSRF) 규칙, 비밀 파일 이름 표, 소스 설정 새 칸(`intake`, `trigger_label`, `default_fix_agent_id`, 선택이 된 `workflow_repository_id`·`fix_verification_profile_id`·`review_agent_id`, `installation_id`)과 기존 설정 호환 규칙, 자동 매칭 규칙(러너 discovered 의 `github_repository` 키, 여러 후보·없음일 때 대기 코드), 실행 지시 규칙, 이름·시그니처 표(`SecretStore`, `GitHubAppAuth`, 토큰 공급자 인터페이스).
5. `docs/GLOSSARY.md` 용어, `docs/CURRENT_HANDOFF.md`·`docs/product/MVP_PLAN.md` 11절의 phase 순서를 `11-github-app` → `12-real-repo` 로 갱신(기존 `11-real-repo` 항목은 12 로 이름만 바꾼다).

## 테스트 먼저

문서 전용 step 이라 제품 테스트를 새로 만들지 않는다. 기존 문서 검사(링크 등)가 통과해야 한다.

소스·배포 파일 변경 전에 `tests/` 미러 경로에 실패 테스트를 작성하고 실패 원인이 의도한 것인지 확인한다(`tdd-guard.sh` 가 테스트 없는 소스 작성을 막는다). 구현 후 해당 테스트와 전체 회귀를 통과시킨다.

## Acceptance Criteria

```bash
python3 -m pytest -q
python3 -m ruff check .
```

위 커맨드와 이 step 의 테스트 항목을 모두 확인한다.

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 규칙을 확인한다: `domain/` 은 FastAPI·sqlite3·HTTPX·subprocess·Git 을 import 하지 않는다 / `server/` 와 `connector/` 는 서로 import 하지 않는다 / 비밀값(`OPERATOR_TOKEN`, `SESSION_SECRET`, `WORKFLOW_GITHUB_TOKEN`, 연결 토큰 등)은 환경변수·0600 파일에서만 읽고 DB·로그·응답·템플릿·이미지 레이어에 넣지 않는다 / 기본 모드(demo)의 기존 동작과 기존 GitHub 소스 설정(라벨·고른 이슈·환경변수 토큰)이 그대로 동작한다.
3. 성공이면 `phases/11-github-app/index.json` 의 step 0 만 `completed` 로 바꾸고 `summary` 에 생성·수정 파일, 결정, 검증 결과(테스트 수), 다음 step 이 주의할 점을 한 줄로 남긴다.
4. 3회 수정 후에도 실패하면 `error` + `error_message`, 사용자 결정·외부 자원이 필요하면 `blocked` + `blocked_reason` 을 기록한다. 실행하지 않은 검증을 `completed` 로 표시하지 않는다.

## 금지사항

- 실제 GitHub API(api.github.com)·github.com 앱 흐름을 호출하지 않는다(docs.github.com 문서 읽기는 허용). 이유: 모든 step 은 httpx `MockTransport`·가짜 GitHub 로 검증한다. 실제 App 생성·설치는 phase 뒤 사용자 브라우저에서 한다.
- 사용자가 띄워 둔 셀프호스트(compose 프로젝트 `runloom`, 포트 8000, 볼륨 `runloom_workflow-data`)와 `deploy/selfhost/.env` 를 건드리지 않는다. 컨테이너를 띄우거나 멈추지 않는다.
- `main`·공개 데모 VM·demo 모드 기본 동작을 바꾸지 않는다.
- 비밀값(App 개인 키·client secret·webhook secret·설치 토큰·PAT)을 DB·로그·응답·템플릿·예외 문구·백업에 넣지 않는다. 테스트 fixture 의 개인 키는 테스트 안에서 생성한다(저장소에 키 파일을 커밋하지 않는다).
- 사용자 변경을 삭제하거나 stash·reset 하지 않는다. 커밋은 하네스가 한다.
- 이 step 범위 밖 모듈을 "개선"하지 않는다.
