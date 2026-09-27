# Step 8: 연결 화면 다시 설계와 [에이전트에게 맡기기]

## 읽어야 할 파일

- AGENTS.md
- phases/11-github-app/README.md (계획 기본값 — 이 phase 의 기준)
- phases/11-github-app/index.json (완료 step 의 summary)
- docs/adr/0017-github-app-connection.md (step 0 산출물. step 0 에서는 새로 만든다)
- docs/ARCHITECTURE.md ("GitHub App 연결 — phase 11" 절. step 0 에서는 새로 만든다)
- docs/GLOSSARY.md
- src/workflow/server/templates/operator_github.html, src/workflow/server/views.py, src/workflow/server/web.py
- src/workflow/server/templates/ (업무 목록·상세), docs/UI_GUIDE.md
- tests/workflow/server/ (화면 테스트)

먼저 실제 파일과 이전 step 산출물을 읽는다. 이름·경로·API 필드는 step 0 의 ADR-0017·ARCHITECTURE 가 고정한 것을 따르고, 바꿔야 하면 같은 이름의 문서·테스트를 함께 갱신한다. 대화 이력을 전제로 판단하지 않는다.

## 작업

1. `/operator/github`:
   - 연결 전: 설명 한 줄 + **[GitHub 연결]** 버튼(→ `/operator/github/app/new`). 그 아래 접힌 "고급 — 토큰으로 연결".
   - 연결 후: 저장소 카드 목록 — 저장소 이름, 동기화 상태(마지막 동기화·가져온 이슈 수·오류), 러너 매칭 상태(자동으로 정해진 수정·검토 에이전트와 검증 프로필, 또는 "이 저장소를 등록한 러너 없음 — 러너에서 register" 안내), 트리거 라벨, 기준선 가져오기 버튼(기존). **[저장소 추가/변경]** 은 GitHub 설치 설정 페이지 링크.
   - 기존 입력 폼(로컬 저장소 ID·검증 프로필 ID·시작 시각·GitHub 숫자 사용자 ID 등)은 저장소 카드의 접힌 "고급 설정"으로 옮기고, 자동 결정 칸은 비워 두면 자동이라고 표시한다. 내부 ID 를 필수 입력으로 요구하지 않는다.
2. 업무 목록·상세: 직접 실행 대기인 GitHub Task 에 **[에이전트에게 맡기기]** 버튼, 대기 사유(새 대기 코드 포함)를 사람 말로.
3. 비밀값은 "연결됨/없음"만. 기존 CSS·컴포넌트를 쓴다.

## 테스트 먼저

화면 테스트: 연결 전 버튼만(내부 ID 입력 칸이 기본 화면에 없음), 연결 후 저장소 카드·매칭 상태·러너 없음 안내, 고급 설정 접힘, 맡기기 버튼 노출 조건, 비밀 값 미노출, demo 모드 회귀.

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
3. 성공이면 `phases/11-github-app/index.json` 의 step 8 만 `completed` 로 바꾸고 `summary` 에 생성·수정 파일, 결정, 검증 결과(테스트 수), 다음 step 이 주의할 점을 한 줄로 남긴다.
4. 3회 수정 후에도 실패하면 `error` + `error_message`, 사용자 결정·외부 자원이 필요하면 `blocked` + `blocked_reason` 을 기록한다. 실행하지 않은 검증을 `completed` 로 표시하지 않는다.

## 금지사항

- 실제 GitHub API(api.github.com)·github.com 앱 흐름을 호출하지 않는다(docs.github.com 문서 읽기는 허용). 이유: 모든 step 은 httpx `MockTransport`·가짜 GitHub 로 검증한다. 실제 App 생성·설치는 phase 뒤 사용자 브라우저에서 한다.
- 사용자가 띄워 둔 셀프호스트(compose 프로젝트 `runloom`, 포트 8000, 볼륨 `runloom_workflow-data`)와 `deploy/selfhost/.env` 를 건드리지 않는다. 컨테이너를 띄우거나 멈추지 않는다.
- `main`·공개 데모 VM·demo 모드 기본 동작을 바꾸지 않는다.
- 비밀값(App 개인 키·client secret·webhook secret·설치 토큰·PAT)을 DB·로그·응답·템플릿·예외 문구·백업에 넣지 않는다. 테스트 fixture 의 개인 키는 테스트 안에서 생성한다(저장소에 키 파일을 커밋하지 않는다).
- 사용자 변경을 삭제하거나 stash·reset 하지 않는다. 커밋은 하네스가 한다.
- 이 step 범위 밖 모듈을 "개선"하지 않는다.
