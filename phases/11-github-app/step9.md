# Step 9: e2e 와 문서

## 읽어야 할 파일

- AGENTS.md
- phases/11-github-app/README.md (계획 기본값 — 이 phase 의 기준)
- phases/11-github-app/index.json (완료 step 의 summary)
- docs/adr/0017-github-app-connection.md (step 0 산출물. step 0 에서는 새로 만든다)
- docs/ARCHITECTURE.md ("GitHub App 연결 — phase 11" 절. step 0 에서는 새로 만든다)
- docs/GLOSSARY.md
- tests/e2e/test_github_cycle.py, tests/e2e/test_metrics.py (FakeGitHub)
- docs/SELFHOST.md, docs/github/README.md, docs/CURRENT_HANDOFF.md, docs/VERIFICATION_LOG.md

먼저 실제 파일과 이전 step 산출물을 읽는다. 이름·경로·API 필드는 step 0 의 ADR-0017·ARCHITECTURE 가 고정한 것을 따르고, 바꿔야 하면 같은 이름의 문서·테스트를 함께 갱신한다. 대화 이력을 전제로 판단하지 않는다.

## 작업

1. `tests/e2e/test_github_app.py`(`WORKFLOW_E2E=1`): FakeGitHub 에 manifest 교환·설치 토큰·설치 저장소·이슈 목록을 추가하고, 운영자 로그인 → [GitHub 연결] → (GitHub 쪽은 테스트가 callback·setup 을 직접 호출) → 소스 자동 생성 → 동기화로 열린 이슈 전부 대기 → 러너 register(가짜 도구, remote 가 그 저장소) → 매칭 자동 → 이슈 하나 [맡기기] → 수정 → 검토 → 완료, 다른 이슈에 트리거 라벨 → 자동 착수. 비밀 파일 권한·DB/로그에 비밀 없음.
2. `docs/SELFHOST.md` GitHub 절을 버튼 흐름으로 다시 쓴다(실제 GitHub 화면에서 사용자가 누를 것: App 이름 확인 → Create → 설치 대상 저장소 선택 → Install). 토큰 방식은 고급으로. `docs/github/README.md` 갱신.
3. CURRENT_HANDOFF: phase 11 상태, 다음 = 사용자 브라우저로 실제 App 생성·설치(OpenArchive 선택)·기준선 가져오기 → `12-real-repo`. VERIFICATION_LOG 에 대역 e2e 기록(실연동 아님 명시).

## 테스트 먼저

e2e 자체가 이 step 의 테스트다.

소스·배포 파일 변경 전에 `tests/` 미러 경로에 실패 테스트를 작성하고 실패 원인이 의도한 것인지 확인한다(`tdd-guard.sh` 가 테스트 없는 소스 작성을 막는다). 구현 후 해당 테스트와 전체 회귀를 통과시킨다.

## Acceptance Criteria

```bash
python3 -m pytest -q
python3 -m ruff check .
WORKFLOW_E2E=1 python3 -m pytest tests/e2e -q
```

위 커맨드와 이 step 의 테스트 항목을 모두 확인한다.

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 규칙을 확인한다: `domain/` 은 FastAPI·sqlite3·HTTPX·subprocess·Git 을 import 하지 않는다 / `server/` 와 `connector/` 는 서로 import 하지 않는다 / 비밀값(`OPERATOR_TOKEN`, `SESSION_SECRET`, `WORKFLOW_GITHUB_TOKEN`, 연결 토큰 등)은 환경변수·0600 파일에서만 읽고 DB·로그·응답·템플릿·이미지 레이어에 넣지 않는다 / 기본 모드(demo)의 기존 동작과 기존 GitHub 소스 설정(라벨·고른 이슈·환경변수 토큰)이 그대로 동작한다.
3. 성공이면 `phases/11-github-app/index.json` 의 step 9 만 `completed` 로 바꾸고 `summary` 에 생성·수정 파일, 결정, 검증 결과(테스트 수), 다음 step 이 주의할 점을 한 줄로 남긴다.
4. 3회 수정 후에도 실패하면 `error` + `error_message`, 사용자 결정·외부 자원이 필요하면 `blocked` + `blocked_reason` 을 기록한다. 실행하지 않은 검증을 `completed` 로 표시하지 않는다.

## 금지사항

- 실제 GitHub API(api.github.com)·github.com 앱 흐름을 호출하지 않는다(docs.github.com 문서 읽기는 허용). 이유: 모든 step 은 httpx `MockTransport`·가짜 GitHub 로 검증한다. 실제 App 생성·설치는 phase 뒤 사용자 브라우저에서 한다.
- 사용자가 띄워 둔 셀프호스트(compose 프로젝트 `runloom`, 포트 8000, 볼륨 `runloom_workflow-data`)와 `deploy/selfhost/.env` 를 건드리지 않는다. 컨테이너를 띄우거나 멈추지 않는다.
- `main`·공개 데모 VM·demo 모드 기본 동작을 바꾸지 않는다.
- 비밀값(App 개인 키·client secret·webhook secret·설치 토큰·PAT)을 DB·로그·응답·템플릿·예외 문구·백업에 넣지 않는다. 테스트 fixture 의 개인 키는 테스트 안에서 생성한다(저장소에 키 파일을 커밋하지 않는다).
- 사용자 변경을 삭제하거나 stash·reset 하지 않는다. 커밋은 하네스가 한다.
- 이 step 범위 밖 모듈을 "개선"하지 않는다.
