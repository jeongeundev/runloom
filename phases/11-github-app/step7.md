# Step 7: 연결 경로 — App 생성·설치·소스 자동 생성

## 읽어야 할 파일

- AGENTS.md
- phases/11-github-app/README.md (계획 기본값 — 이 phase 의 기준)
- phases/11-github-app/index.json (완료 step 의 summary)
- docs/adr/0017-github-app-connection.md (step 0 산출물. step 0 에서는 새로 만든다)
- docs/ARCHITECTURE.md ("GitHub App 연결 — phase 11" 절. step 0 에서는 새로 만든다)
- docs/GLOSSARY.md
- src/workflow/server/web.py (운영자 화면·CSRF·세션 규칙), src/workflow/server/github_api.py, src/workflow/server/auth.py
- src/workflow/adapters/github_app.py, src/workflow/adapters/secret_store.py
- tests/workflow/server/

먼저 실제 파일과 이전 step 산출물을 읽는다. 이름·경로·API 필드는 step 0 의 ADR-0017·ARCHITECTURE 가 고정한 것을 따르고, 바꿔야 하면 같은 이름의 문서·테스트를 함께 갱신한다. 대화 이력을 전제로 판단하지 않는다.

## 작업

ARCHITECTURE 가 정한 경로를 구현한다. 모두 운영자 전용.

1. `GET /operator/github/app/new`: manifest JSON(이름 예 `Runloom (<호스트>)`, url, `redirect_url`=`<요청 기준 주소>/operator/github/app/callback`, `setup_url`=`…/setup`, `hook_attributes.active=false`, `public=false`, 최소 권한)과 무작위 `state`(서버 측에 짧게 보관, 10분)를 담은 자동 제출 폼 HTML 을 돌려준다(폼 action `https://github.com/settings/apps/new?state=…`). 기준 주소는 `WORKFLOW_PUBLIC_URL` 이 있으면 그것, 없으면 요청 호스트(127.0.0.1/localhost 만 허용).
2. `GET /operator/github/app/callback?code&state`: state 검증(불일치·만료 거부) → `exchange_manifest_code` → 비밀 저장소에 개인 키·client secret·webhook secret, 비밀 아닌 app id·slug 는 비밀 저장소 또는 설정 행(ARCHITECTURE 대로) → `https://github.com/apps/{slug}/installations/new` 로 303.
3. `GET /operator/github/app/setup?installation_id&setup_action`: 설치 id 로 설치 저장소 목록 조회 → 저장소마다 소스가 없으면 `intake=all_open`, `trigger_label=runloom`, `run_mode=auto`(트리거 라벨 기준), 자동 결정 칸은 비워 생성(설정 번호 증가 규칙 따름), 이미 있으면 `installation_id` 만 갱신 → 설치에서 빠진 저장소의 소스는 수집 중지(삭제하지 않음) → `/operator/github` 로 303. 이 요청이 로그인한 운영자 세션에서 온 것인지 확인(state 또는 세션).
4. `POST /operator/github/token`(고급): PAT 를 비밀 저장소에 쓰고 그 토큰으로 볼 수 있는 저장소 목록을 보여 선택하게 한다(선택 → 같은 방식으로 소스 생성). 응답에 토큰 값 없음.
5. `POST /tasks/{task_id}/delegate` — [에이전트에게 맡기기]: 직접 실행 대기인 GitHub Task 를 자동 착수 대상으로 바꾼다(한 번만, 멱등). 기존 `/tasks/{id}/run` 과 관계를 ARCHITECTURE 대로 정리.

## 테스트 먼저

서버 테스트(가짜 GitHub transport): new 가 manifest·state 를 담은 폼, callback state 불일치·만료·재사용 거부, 교환 성공 시 비밀 저장·303 설치 URL, 교환 실패 오류 화면, setup 에서 저장소마다 소스 생성·재호출 멱등·빠진 저장소 중지, 비운영자 거부, PAT 경로, delegate 멱등·권한, 모든 응답·로그에 비밀 없음. demo 모드 회귀.

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
3. 성공이면 `phases/11-github-app/index.json` 의 step 7 만 `completed` 로 바꾸고 `summary` 에 생성·수정 파일, 결정, 검증 결과(테스트 수), 다음 step 이 주의할 점을 한 줄로 남긴다.
4. 3회 수정 후에도 실패하면 `error` + `error_message`, 사용자 결정·외부 자원이 필요하면 `blocked` + `blocked_reason` 을 기록한다. 실행하지 않은 검증을 `completed` 로 표시하지 않는다.

## 금지사항

- 실제 GitHub API(api.github.com)·github.com 앱 흐름을 호출하지 않는다(docs.github.com 문서 읽기는 허용). 이유: 모든 step 은 httpx `MockTransport`·가짜 GitHub 로 검증한다. 실제 App 생성·설치는 phase 뒤 사용자 브라우저에서 한다.
- 사용자가 띄워 둔 셀프호스트(compose 프로젝트 `runloom`, 포트 8000, 볼륨 `runloom_workflow-data`)와 `deploy/selfhost/.env` 를 건드리지 않는다. 컨테이너를 띄우거나 멈추지 않는다.
- `main`·공개 데모 VM·demo 모드 기본 동작을 바꾸지 않는다.
- 비밀값(App 개인 키·client secret·webhook secret·설치 토큰·PAT)을 DB·로그·응답·템플릿·예외 문구·백업에 넣지 않는다. 테스트 fixture 의 개인 키는 테스트 안에서 생성한다(저장소에 키 파일을 커밋하지 않는다).
- 사용자 변경을 삭제하거나 stash·reset 하지 않는다. 커밋은 하네스가 한다.
- 이 step 범위 밖 모듈을 "개선"하지 않는다.
