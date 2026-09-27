# Step 4: 작업 복사본 준비물 — 링크와 환경변수

## 읽어야 할 파일

- AGENTS.md
- phases/12-real-repo/README.md (계획 기본값 — 이 phase 의 기준)
- phases/12-real-repo/index.json (완료 step 의 summary)
- docs/adr/0018-real-repo-cycle.md (step 0 산출물. step 0 에서는 새로 만든다)
- docs/ARCHITECTURE.md ("실제 저장소 순환 — phase 12" 절. step 0 에서는 새로 만든다)
- docs/GLOSSARY.md
- src/workflow/connector/local_tool.py (`ensure_worktree` 호출부, `child_env`, `_run_argv`), src/workflow/connector/git_ops.py, src/workflow/connector/masking.py (`ENV_ALLOWLIST`, `codex_env`)
- step 2 가 만든 registrations `links_json`·`env_json`
- tests/workflow/connector/test_local_tool.py, tests/workflow/connector/test_masking.py

먼저 실제 파일과 이전 step 산출물을 읽는다. 이름·경로·API 필드는 step 0 의 ADR-0018·ARCHITECTURE 가 고정한 것을 따르고, 바꿔야 하면 같은 이름의 문서·테스트를 함께 갱신한다. 대화 이력을 전제로 판단하지 않는다.

## 작업

- worktree 를 **새로 만든 직후**(이어 쓰는 경우는 링크가 없을 때만) 등록의 `links` 경로마다 `worktree/경로` → `원본 폴더/경로` 심볼릭 링크를 만든다. 원본에 대상이 없으면 건너뛰고 로그. worktree 에 그 경로가 이미 파일로 있으면(추적 파일) 덮지 않고 로그. 링크 경로를 git 제외 목록(`git rev-parse --git-path info/exclude` 로 찾은 파일)에 넣어 `commit_all`·`is_dirty` 에 잡히지 않게 한다 — 추가 후 `is_dirty` 가 False 인지 테스트로 고정.
- 검증 프로세스(`_run_argv`)와 에이전트 도구 프로세스 환경 = 기존 허용 목록 환경 + 등록 `env`. 등록 `env` 가 허용 목록 이름을 덮지 못한다(step 2 의 거부 규칙을 실행 때도 다시 확인).
- `env` 값은 러너 로그·실행 이벤트·산출물에 나오지 않게 한다: 도구·검증 출력에 값이 나오면 기존 마스킹처럼 가린다(값 길이 8 이상만).
- 원본 폴더의 파일을 쓰거나 지우지 않는다(링크 대상은 읽기만).

## 테스트 먼저

- 임시 저장소에 `backend/.venv/bin/x`·`frontend/node_modules/y` 를 원본에만 두고, 새 worktree 에 링크가 생기고 `is_dirty` False, `commit_all` 결과에 링크가 없음.
- 원본에 없는 링크 대상 → 건너뜀, 추적 파일과 겹치는 경로 → 덮지 않음.
- 검증 명령(`env` 를 출력하는 작은 스크립트)이 등록 env 를 받음, 에이전트 도구 환경도 받음, 허용 목록 이름 덮기 불가.
- 출력에 env 값이 들어가면 산출물·이벤트에서 가려짐.

소스·배포 파일 변경 전에 `tests/` 미러 경로에 실패 테스트를 작성하고 실패 원인이 의도한 것인지 확인한다(`tdd-guard.sh` 가 테스트 없는 소스 작성을 막는다). 구현 후 해당 테스트와 전체 회귀를 통과시킨다.

## Acceptance Criteria

```bash
python3 -m pytest -q
python3 -m ruff check .
```

위 커맨드와 이 step 의 테스트 항목을 모두 확인한다.

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 규칙을 확인한다: `domain/` 은 FastAPI·sqlite3·HTTPX·subprocess·Git 을 import 하지 않는다 / `server/` 와 `connector/` 는 서로 import 하지 않고 `contracts/` 만 공유한다 / 외부 입력(요청 본문·이슈 본문·모델 응답)에서 명령·경로·브랜치 이름을 받아 실행하지 않는다 — git 인자 배열은 러너 어댑터가 고정한다 / 비밀값(연결 토큰, GitHub App 비밀·설치 토큰, PAT, 알림 웹훅 URL, 러너 로컬 등록의 환경변수 값)은 DB·로그·응답·템플릿·백업·중앙에 넣지 않는다 / 기본 모드(demo)와 phase 8·11 의 기존 GitHub 순환이 그대로 동작한다.
3. 성공이면 `phases/12-real-repo/index.json` 의 step 4 만 `completed` 로 바꾸고 `summary` 에 생성·수정 파일, 결정, 검증 결과(테스트 수), 다음 step 이 주의할 점을 한 줄로 남긴다.
4. 3회 수정 후에도 실패하면 `error` + `error_message`, 사용자 결정·외부 자원이 필요하면 `blocked` + `blocked_reason` 을 기록한다. 실행하지 않은 검증을 `completed` 로 표시하지 않는다.

## 금지사항

- 실제 GitHub(api.github.com·github.com)와 실제 Discord 를 호출하지 않는다(docs.github.com·discord.com/developers 문서 읽기는 허용). 이유: 모든 step 은 httpx `MockTransport`·가짜 GitHub·로컬 bare 저장소로 검증한다. 실연동은 phase 뒤 사용자와 함께 한다.
- `/Users/kje/demo/OpenArchive` 와 그 worktree 를 읽거나 바꾸지 않는다. 이유: 사용자가 개발 중인 실제 저장소다. 테스트는 임시 디렉터리의 가짜 저장소로 한다.
- 사용자가 띄워 둔 셀프호스트(compose 프로젝트 `runloom`, 포트 8000, 볼륨 `runloom_workflow-data`)·`deploy/selfhost/.env`·러너 홈(`~/Library/Application Support/workflow-connector/`)·launchd 를 건드리지 않는다. 컨테이너를 띄우거나 멈추지 않는다.
- 러너가 기본 브랜치에 push 하거나 force push 하지 않는다. 병합·이슈 닫기는 하지 않는다 — 사람만 한다.
- `main`·공개 데모 VM·demo 모드 기본 동작을 바꾸지 않는다.
- 사용자 변경을 삭제하거나 stash·reset 하지 않는다. 커밋은 하네스가 한다.
- 이 step 범위 밖 모듈을 "개선"하지 않는다.
