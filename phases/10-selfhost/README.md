# Phase 10 — 셀프호스트: 한 명령 설치·고정 워크스페이스·데이터 보존

작성일: 2026-09-27. 상태: 구현 계획 작성 완료, 모든 step pending. 근거: [MVP 계획](../../docs/product/MVP_PLAN.md) 8절(배포 경로)·10절·11절.

## 목표

빈 디렉터리에서 설치 문서대로 명령 하나로 중앙 서버·워커를 띄우고, 러너는 호스트 Mac 에서 네이티브로 붙인다. 재시작·업그레이드 뒤에도 데이터가 남고, 백업·복원이 된다. 브라우저·쿠키가 바뀌어도 같은 워크스페이스로 돌아온다.

## 현재 코드에서 확인한 간극 (2026-09-27)

| 필요한 것 | 지금 | 위치 |
|---|---|---|
| 한 명령 설치 | 없음. 공모전 VM 설치(Ubuntu·systemd·Caddy·대본 에이전트)만 있다 | `deploy/install-vm.sh`, `deploy/systemd/` |
| 워크스페이스 유지 | 브라우저 쿠키 = 워크스페이스. 쿠키를 잃으면 화면으로 다시 찾을 수 없다. 운영자 로그인도 "이 쿠키 세션이 운영자" 표시 | `server/auth.py` `require_session`, ADR-0005 |
| 진단 데모 분리 | `DIAG_API_TOKEN` 이 필수 비밀값 | `server/settings.py` `SECRET_KEYS` |
| 헬스 확인 | 없음 | — |
| 백업 | VM 용 셸 스크립트(systemd 타이머) | `deploy/backup.sh` |

## 계획 기본값 (2026-09-27 사용자 결정 — step 0 이 ADR-0016 으로 고정)

1. **Docker compose**: `central`(웹/API)·`worker` 두 서비스, 같은 named volume(`/data`), 호스트 `127.0.0.1` 에만 포트 공개. Mac bind mount 는 SQLite WAL 잠금 위험으로 쓰지 않는다. ADR-0006("컨테이너 없음")은 공개 데모 한정으로 범위를 좁힌다.
2. **러너는 호스트 네이티브**(launchd): 구독 CLI 로그인이 호스트에 있으므로. `http://127.0.0.1:<포트>` 로 붙는다.
3. **`WORKFLOW_MODE=selfhost`**: 고정 워크스페이스 하나, `OPERATOR_TOKEN` 로그인 = 운영자, 익명 세션 생성 없음, 화면 미로그인 → `/login`, API → 401. 러너·n8n 토큰 인증은 그대로. 기본값(미설정)은 `demo` 로 기존 동작 그대로 — 기존 테스트와 `main` 공개 데모 경로를 지킨다. ADR-0005 는 공개 데모 한정.
4. **진단 데모 제외**: selfhost 에서 `DIAG_API_TOKEN` 선택, 비면 진단 기능 꺼짐.
5. **설치 스크립트**가 `.env`(0600)를 만들고 비밀값을 생성한다. 이미 있으면 덮어쓰지 않는다. 재실행 = 업그레이드. 스키마 마이그레이션은 서버 시작 시 기존 `init_schema`.
6. **백업·복원 CLI**: `python3 -m workflow.server.backup create|list|restore`. 비밀값은 백업하지 않는다.
7. **새 설치는 빈 DB**: 기존 `data/central.sqlite`·9/23 실연동 DB 는 옮기지 않는다.

## Step 목록

| Step | 이름 | 산출물 |
|---|---|---|
| 0 | selfhost-design | ADR-0016, ARCHITECTURE "셀프호스트 — phase 10" 절, GLOSSARY |
| 1 | settings-mode | `WORKFLOW_MODE`, selfhost 에서 진단 토큰 선택 |
| 2 | workspace-login | 고정 워크스페이스, `/login`·`/logout`, 익명 세션 차단 |
| 3 | selfhost-ui | 로그인 화면·내비게이션, 데모 전용 요소 숨김 |
| 4 | backup-cli | 백업·목록·복원 CLI |
| 5 | docker-image | `/healthz`, Dockerfile, compose, `.env.example` |
| 6 | install-script | `install.sh`(한 명령), `install-runner.sh`(launchd) |
| 7 | selfhost-docs | `docs/SELFHOST.md`, AGENTS.md 명령어 |
| 8 | selfhost-verify | 실제 Docker 로 설치·재시작 보존·백업 복원 e2e (`WORKFLOW_DOCKER=1`) |

step 0~7 은 컨테이너를 띄우지 않는다. step 8 만 이 Mac 의 Docker 로 격리된 프로젝트 이름·포트에서 실제 검증한다(외부 호출·비용 없음).

## 실행

```bash
git status --short            # 깨끗해야 한다
git branch --show-current     # service
python3 scripts/execute.py 10-selfhost --engine claude
```

완료 후 `service` 에 `--no-ff` 병합은 별도 단계. `main`·공개 데모 VM 은 건드리지 않는다.

## 범위 밖

- 원격 접속(Cloudflare Tunnel), 팀 계정·권한, 호스팅 제어면.
- 기존 DB 이전 도구.
- 새 업무 기준 커밋 추적·알림 웹훅·검증 환경 → `11-real-repo`.
