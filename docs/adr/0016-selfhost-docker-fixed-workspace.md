# ADR-0016: 셀프호스트 — Docker compose·호스트 네이티브 러너·고정 워크스페이스

결정일: 2026-09-27 (phase 10 step 0). 기본값은 [phase 10 README](../../phases/10-selfhost/README.md) "계획 기본값"(2026-09-27 사용자 결정)이며 이 문서로 구현 기준을 고정한다. 근거: [MVP 계획](../product/MVP_PLAN.md) 8절(배포 경로)·10절·11절. 이 시점에는 구현이 없다 — 아래 이름·경로는 step 1~8 이 만들고, 바꿀 때는 이 ADR·[ARCHITECTURE](../ARCHITECTURE.md) "셀프호스트 — phase 10"·[GLOSSARY](../GLOSSARY.md)·테스트를 같이 고친다.

## 결정

1. **중앙은 Docker compose.** `deploy/selfhost/compose.yaml` 에 `central`(웹/API, Uvicorn)·`worker`(중앙 워커) 두 서비스를 둔다. 이미지는 `deploy/selfhost/Dockerfile` 하나로 만들고 두 서비스가 명령만 달리 쓴다. 데이터는 named volume `workflow-data` 하나를 두 서비스가 `/data` 에 붙인다(`/data/central.sqlite`·`/data/artifacts`·`/data/backups`). 포트는 호스트 `127.0.0.1` 에만 공개한다(기본 8000, `WORKFLOW_PORT` 로 바꿈). Mac bind mount 는 쓰지 않는다 — SQLite WAL 의 공유 메모리·잠금이 호스트 파일 공유 계층을 거치면 보장되지 않는다.
2. **러너는 호스트 네이티브(launchd).** 구독 CLI(`claude`·`codex`)의 로그인과 작업 폴더가 호스트에 있으므로 연결 프로그램(`python3 -m workflow.connector`)은 컨테이너에 넣지 않고 호스트 Mac 에서 launchd 로 돈다. `http://127.0.0.1:<포트>` 로 붙는다. 러너 프로토콜(계약 v1)은 바뀌지 않는다.
3. **`WORKFLOW_MODE=selfhost` 에서 고정 워크스페이스 하나 + `OPERATOR_TOKEN` 로그인.**
   - 워크스페이스는 DB 에 하나다(세션 id 고정값 `sess-selfhost`). 첫 로그인 성공 때 `is_operator=1` 로 만들고 이후 재사용한다.
   - `/login` 에 `OPERATOR_TOKEN` 을 넣으면 그 id 를 `SESSION_SECRET` 으로 서명한 쿠키를 받는다. 로그인한 사람은 곧 운영자다.
   - 익명 세션을 만들지 않는다. 화면 미로그인 → `/login` 303, API 미로그인 → 401 `unauthenticated`. 러너(연결 토큰 `wfc_`)·n8n(입구 토큰 `wfs_`) 인증은 그대로다.
   - 브라우저·쿠키가 바뀌어도 다시 로그인하면 같은 워크스페이스다.
4. **기본 모드는 `demo` — 기존 그대로.** `WORKFLOW_MODE` 미설정(또는 `demo`)은 익명 세션 발급·`/operator` 토큰 입력·데모 화면 전부 지금 동작 그대로다. 기존 테스트와 `main` 공개 데모 경로를 지킨다. 그 밖의 값은 설정 오류(`ValueError`)다.
5. **진단 데모 제외.** selfhost 에서 `DIAG_API_TOKEN` 은 선택이다. 비면 진단 기능(진단 API 호출)이 꺼진다. compose 에 진단 API·진단 워커 서비스를 넣지 않는다. demo 모드에서는 지금처럼 필수다.
6. **설치 = 한 명령, 재실행 = 업그레이드.** `deploy/selfhost/install.sh` 가 `deploy/selfhost/.env`(0600)를 `.env.example` 에서 만들고 `SESSION_SECRET`·`OPERATOR_TOKEN` 을 무작위로 생성한다. 이미 있으면 덮어쓰지 않는다. 이미지 빌드 → `docker compose up -d` → `/healthz` 확인. 스키마 마이그레이션은 서버·워커 시작 때 기존 `init_schema` 가 한다. 러너는 `deploy/selfhost/install-runner.sh` 가 연결·등록·launchd 설치를 한다.
7. **백업·복원 CLI.** `python3 -m workflow.server.backup create|list|restore`. DB 는 SQLite 온라인 백업 API 로, 산출물은 tar 로 `/data/backups/{이름}/` 에 둔다. 비밀값(`.env`·연결 토큰 파일)은 백업하지 않는다.
8. **새 설치는 빈 DB.** 기존 `data/central.sqlite`·2026-09-23 실연동 DB 는 옮기지 않는다. 이전 도구는 범위 밖이다.
9. **비밀값은 이미지 레이어에 넣지 않는다.** `.env` 는 compose `env_file` 로 실행 때만 주입하고 빌드 컨텍스트에서 제외한다(`.dockerignore`). AGENTS.md 비밀값 규칙(DB·로그·응답·템플릿 금지)은 그대로다.

## 범위 조정

- [ADR-0005](0005-access-model-anonymous-session-operator-token.md)(익명 세션·운영자 토큰)는 **공개 데모(demo 모드)에 한정**한다. 셀프호스트는 이 ADR 결정 3 을 따른다.
- [ADR-0006](0006-deployment-vm-caddy-mac-connector.md)(VM + Caddy, 컨테이너 없음)은 **공개 데모에 한정**한다. 공개 데모 VM·`deploy/install-vm.sh`·`deploy/systemd/`·[DEPLOY](../DEPLOY.md) 런북은 바꾸지 않는다([ADR-0008](0008-public-demo-scripted-agents.md)).

## 대안

- **macOS 네이티브 전용(launchd 로 중앙·워커도).** Docker 가 필요 없지만 Python 버전·의존성 설치가 사용자 Mac 환경에 따라 갈리고, "빈 디렉터리에서 한 명령" 재현이 어렵다. 업그레이드·제거도 흩어진다. 러너만 네이티브로 두고 중앙은 컨테이너로 기각.
- **별도 상시 서버(VM)에 중앙.** 공모전 구성과 같은 형태지만 1인 셀프호스트 사용자에게 VM·도메인·HTTPS 준비를 요구하고, 러너가 인터넷을 거쳐 붙는다. 원격 접속이 필요하면 나중에 Cloudflare Tunnel 을 붙인다(범위 밖). 기각.
- **로그인 없음(127.0.0.1 이니 누구나 운영자).** 같은 Mac 의 다른 프로세스·브라우저 탭(다른 사이트의 요청)이 운영자 동작을 할 수 있고, 쿠키를 잃으면 워크스페이스를 다시 찾는 규칙이 여전히 필요하다. 토큰 로그인이 비용이 거의 없어 기각.
- **bind mount(`./data:/data`).** 호스트에서 파일이 보여 편하지만 Docker Desktop 의 파일 공유(virtiofs 등)를 거친 SQLite WAL 은 두 컨테이너 사이 잠금·공유 메모리 일관성이 보장되지 않고 느리다. 파일은 백업 CLI 로 꺼낸다. 기각.

## 결과

- 셀프호스트 사용자는 Docker Desktop(또는 호환 런타임)과 호스트 `python3`(러너용)가 필요하다.
- 팀 계정·권한은 여전히 없다 — 워크스페이스 하나, 로그인 한 종류. 팀 로그인·원격 접속·호스팅 제어면은 후속.
- demo 와 selfhost 가 같은 코드에서 분기하므로 인증 경로 테스트는 두 모드 모두 돈다. 분기는 인증·화면 노출·진단 켜짐에만 두고, 업무·실행·후속 규칙 코드에는 두지 않는다.
