# Step 4: 백업·복원 CLI

## 읽어야 할 파일

- AGENTS.md
- phases/10-selfhost/README.md (계획 기본값 — 이 phase 의 기준)
- phases/10-selfhost/index.json (완료 step 의 summary)
- docs/adr/0016-selfhost-docker-fixed-workspace.md (step 0 산출물. step 0 에서는 새로 만든다)
- docs/ARCHITECTURE.md ("셀프호스트 — phase 10" 절. step 0 에서는 새로 만든다)
- docs/GLOSSARY.md
- deploy/backup.sh (공개 데모용 — 참고만, 바꾸지 않는다)
- src/workflow/adapters/db.py (`connect`, `init_schema`), src/workflow/server/settings.py
- tests/workflow/server/

먼저 실제 파일과 이전 step 산출물을 읽는다. 이름·환경변수·경로는 step 0 의 ADR-0016·ARCHITECTURE 가 고정한 것을 따르고, 바꿔야 하면 같은 이름의 문서·테스트를 함께 갱신한다. 대화 이력을 전제로 판단하지 않는다.

## 작업

`src/workflow/server/backup.py` 신설, `python3 -m workflow.server.backup` 으로 실행.

- `create [--dest DIR] [--keep N]`: 설정의 DB 를 `sqlite3.Connection.backup` 으로 온라인 복사하고 산출물 디렉터리를 tar.gz 로 묶어 `DIR/<UTC 타임스탬프>/` 에 둔다. 무결성 확인(`PRAGMA integrity_check`)을 복사본에 돌린다. `--keep` 이 있으면 오래된 것부터 지운다. 기본 DIR 은 step 0 이 정한 `/data/backups`(설정으로 바꿀 수 있게).
- `list`: 백업 목록(시각·크기·스키마 버전).
- `restore <백업 이름> [--force]`: 대상 DB 가 있으면 `--force` 없이 거부. 복원 전 현재 DB 를 한 번 더 백업. 복원 뒤 `init_schema` 가 통과하는지 확인. 서비스가 멈춘 상태에서 쓴다고 도움말·문서에 적는다.
- 비밀값·env 파일은 백업하지 않는다.

## 테스트 먼저

`tests/workflow/server/test_backup.py`: 데이터가 있는 임시 DB·산출물로 create → list → 다른 위치로 restore 후 행·파일 동일, WAL 쓰기 중 백업, `--keep` 정리, `--force` 없는 덮어쓰기 거부, 복원 전 자동 백업, 손상 백업 거부.

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
3. 성공이면 `phases/10-selfhost/index.json` 의 step 4 만 `completed` 로 바꾸고 `summary` 에 생성·수정 파일, 결정, 검증 결과(테스트 수), 다음 step 이 주의할 점을 한 줄로 남긴다.
4. 3회 수정 후에도 실패하면 `error` + `error_message`, 사용자 결정·외부 자원이 필요하면 `blocked` + `blocked_reason` 을 기록한다. 실행하지 않은 검증을 `completed` 로 표시하지 않는다.

## 금지사항

- `main`·공개 데모 VM·`deploy/install-vm.sh`·`deploy/systemd/` 를 바꾸지 않는다. 이유: 공개 데모는 기존 경로 그대로 유지한다(ADR-0006·0008).
- 기본 모드(`WORKFLOW_MODE` 미설정 = demo)의 동작을 바꾸지 않는다. 이유: 기존 테스트와 공개 데모가 익명 세션을 전제로 한다.
- 실제 GitHub·유료 모델을 호출하지 않는다.
- step 8 이 아니면 `docker compose up`·`docker run` 으로 컨테이너를 띄우지 않는다(`docker compose config` 같은 정적 검사는 가능). 이유: 실제 기동 검증은 step 8 한 곳에서 격리된 프로젝트 이름·포트로 한다.
- 사용자 변경을 삭제하거나 stash·reset 하지 않는다. `data/` 아래 기존 DB 를 건드리지 않는다. 커밋은 하네스가 한다.
- 이 step 범위 밖 모듈을 "개선"하지 않는다.
