# Step 7: 셀프호스트 설치 문서

## 읽어야 할 파일

- AGENTS.md
- phases/10-selfhost/README.md (계획 기본값 — 이 phase 의 기준)
- phases/10-selfhost/index.json (완료 step 의 summary)
- docs/adr/0016-selfhost-docker-fixed-workspace.md (step 0 산출물. step 0 에서는 새로 만든다)
- docs/ARCHITECTURE.md ("셀프호스트 — phase 10" 절. step 0 에서는 새로 만든다)
- docs/GLOSSARY.md
- deploy/selfhost/ 전부, src/workflow/server/backup.py
- docs/github/README.md (GitHub 토큰 권한 — 기준선 GraphQL 에 필요한 읽기 권한 포함)
- docs/README.md (문서 안내), docs/DEPLOY.md (공개 데모 런북 — 구분만 적는다)

먼저 실제 파일과 이전 step 산출물을 읽는다. 이름·환경변수·경로는 step 0 의 ADR-0016·ARCHITECTURE 가 고정한 것을 따르고, 바꿔야 하면 같은 이름의 문서·테스트를 함께 갱신한다. 대화 이력을 전제로 판단하지 않는다.

## 작업

1. `docs/SELFHOST.md` 신설: 요구 사항(macOS·Docker Desktop·Python 3.13·로그인된 claude/codex CLI), 설치(한 명령), 로그인, 러너 연결(연결 코드 → connect → register → install-runner), GitHub 연결(토큰 권한·`WORKFLOW_GITHUB_REPOS`·기준선 가져오기), 백업·복원(`docker compose exec central python -m workflow.server.backup …`, 복원은 서비스 정지 후), 업그레이드(git pull → install.sh 재실행), 제거(볼륨 삭제는 데이터 삭제임을 경고), 문제 해결(헬스 실패·포트 충돌·러너 오프라인·SQLite 잠금), 알려진 한계(원격 접속 없음, 등록 뒤 새 커밋 미추적 → 11-real-repo).
2. `AGENTS.md` 명령어 절에 셀프호스트 설치·백업 명령을 추가하고, `docs/README.md` 에 링크. `docs/DEPLOY.md` 맨 위에 "공개 데모 VM 런북 — 셀프호스트는 SELFHOST.md" 한 줄.
3. 문서의 명령이 실제 파일·인자와 맞는지 테스트로 검사한다(예: SELFHOST.md 코드 블록의 스크립트 경로·모듈 경로가 존재).

## 테스트 먼저

문서 검사 테스트(`tests/test_selfhost_files.py`): SELFHOST.md 가 참조하는 경로·모듈·환경변수가 실제로 존재, 링크 검사 통과, AGENTS.md 명령어 절에 셀프호스트 명령.

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
3. 성공이면 `phases/10-selfhost/index.json` 의 step 7 만 `completed` 로 바꾸고 `summary` 에 생성·수정 파일, 결정, 검증 결과(테스트 수), 다음 step 이 주의할 점을 한 줄로 남긴다.
4. 3회 수정 후에도 실패하면 `error` + `error_message`, 사용자 결정·외부 자원이 필요하면 `blocked` + `blocked_reason` 을 기록한다. 실행하지 않은 검증을 `completed` 로 표시하지 않는다.

## 금지사항

- `main`·공개 데모 VM·`deploy/install-vm.sh`·`deploy/systemd/` 를 바꾸지 않는다. 이유: 공개 데모는 기존 경로 그대로 유지한다(ADR-0006·0008).
- 기본 모드(`WORKFLOW_MODE` 미설정 = demo)의 동작을 바꾸지 않는다. 이유: 기존 테스트와 공개 데모가 익명 세션을 전제로 한다.
- 실제 GitHub·유료 모델을 호출하지 않는다.
- step 8 이 아니면 `docker compose up`·`docker run` 으로 컨테이너를 띄우지 않는다(`docker compose config` 같은 정적 검사는 가능). 이유: 실제 기동 검증은 step 8 한 곳에서 격리된 프로젝트 이름·포트로 한다.
- 사용자 변경을 삭제하거나 stash·reset 하지 않는다. `data/` 아래 기존 DB 를 건드리지 않는다. 커밋은 하네스가 한다.
- 이 step 범위 밖 모듈을 "개선"하지 않는다.
