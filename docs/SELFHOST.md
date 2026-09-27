# 셀프호스트 설치 — 내 Mac 에서 Runloom 운영하기

작성일: 2026-09-27 (phase 10 step 7). 결정은 [ADR-0016](adr/0016-selfhost-docker-fixed-workspace.md), 구성·이름은 [ARCHITECTURE "셀프호스트 — phase 10"](ARCHITECTURE.md#셀프호스트--phase-10), 용어는 [GLOSSARY](GLOSSARY.md). 공개 데모 VM 은 이 문서가 아니라 [DEPLOY](DEPLOY.md)다.

상태: 파일(`deploy/selfhost/`)과 스크립트는 가짜 `docker`·`curl`·`launchctl` 로 테스트했다(`tests/test_selfhost_files.py`). 2026-09-27 phase 10 step 8 에서 실제 Docker(Docker Desktop 24.0.2, compose v2.19.1)로 설치 → 로그인 → `down`·재설치 뒤 데이터 보존 → 컨테이너 안 백업 → 정지·복원 → 백업 시점으로 돌아옴을 확인했다(`tests/e2e/test_selfhost.py`, [VERIFICATION_LOG](VERIFICATION_LOG.md)). 러너(`install-runner.sh`·launchd)의 실제 적재는 아직 확인하지 않았다.

구성 한눈에:

| 어디서 | 무엇 | 파일 |
|---|---|---|
| Docker (compose 프로젝트 `runloom`) | `central`(웹/API, `127.0.0.1:8000`)·`worker`(중앙 워커), 데이터 볼륨 `runloom_workflow-data` → `/data` | `deploy/selfhost/compose.yaml`, `deploy/selfhost/Dockerfile` |
| 호스트 Mac | 설정·비밀값 `deploy/selfhost/.env`(0600, 설치 스크립트가 생성) | `deploy/selfhost/.env.example` |
| 호스트 Mac (launchd) | 러너 `python3 -m workflow.connector run` — 로그인된 `claude`·`codex` 로 등록 폴더에서 작업 | `deploy/selfhost/install-runner.sh` |

아래 명령은 모두 **저장소 루트**에서 친다. compose 명령은 설치 스크립트와 같은 프로젝트 이름·파일(`-p runloom -f deploy/selfhost/compose.yaml`)을 쓴다 — 빠뜨리면 다른 프로젝트로 보고 새 빈 볼륨을 만든다.

## 요구 사항

- macOS (러너가 launchd 로 돈다).
- Docker Desktop(또는 `docker compose` 가 되는 호환 런타임)이 설치되어 실행 중.
- Python 3.13 계열 `python3` — 러너용. 중앙은 컨테이너 안의 Python 을 쓴다.
- 러너가 쓸 도구 CLI 가 **호스트에 로그인된 상태**: `claude`(Claude Code) 또는 `codex`(Codex CLI). 둘 중 쓰는 것만 있으면 된다.
- Git, 작업할 저장소의 로컬 클론.

확인:

```bash
docker compose version
python3 --version
claude --version
codex --version
```

## 설치

한 명령이다.

```bash
deploy/selfhost/install.sh
```

하는 일:

1. `deploy/selfhost/.env` 가 없으면 `deploy/selfhost/.env.example` 에서 만들고 `SESSION_SECRET`·`OPERATOR_TOKEN` 을 무작위로 채운다(0600). **이미 있으면 건드리지 않는다.**
2. 이미지 빌드 후 `central`·`worker` 기동(`docker compose -p runloom … up -d --build`).
3. `http://127.0.0.1:8000/healthz` 가 `{"status": "ok", …}` 가 될 때까지 최대 120초 기다린다.
4. 접속 주소와 다음 할 일을 출력한다. 토큰 값은 출력하지 않는다.

바꿀 수 있는 값(환경변수): `WORKFLOW_PORT`(호스트 포트, 기본은 `.env` 의 값 → 8000), `RUNLOOM_PROJECT`(compose 프로젝트 이름, 기본 `runloom` — 바꾸면 이 문서의 `-p runloom` 도 바꿔 친다), `HEALTH_TIMEOUT`(대기 초), `DRY_RUN=1`(할 일만 출력). 포트를 계속 바꿔 쓰려면 `.env` 의 `WORKFLOW_PORT` 를 고친다.

새 설치는 빈 DB 로 시작한다. 기존 `data/central.sqlite`(개발용)는 옮기지 않는다.

## 로그인

1. 브라우저에서 `http://127.0.0.1:8000/login` 을 연다.
2. 로그인 토큰을 확인해 넣는다. 값은 `.env` 에만 있다 — 화면·로그·백업에는 없다.

   ```bash
   grep OPERATOR_TOKEN deploy/selfhost/.env
   ```

워크스페이스는 하나이고 로그인한 사람이 곧 운영자다. 브라우저·쿠키가 바뀌어도 다시 로그인하면 같은 워크스페이스다. 로그아웃은 왼쪽 목록 아래 버튼. 60초 안에 5번 틀리면 잠시 로그인이 막힌다(`잠시 후 다시 시도하세요`).

`SESSION_SECRET` 을 바꾸면 모든 로그인이 풀린다(데이터는 그대로). `OPERATOR_TOKEN` 을 바꾸면 새 값으로 로그인한다. `.env` 를 고친 뒤에는 `deploy/selfhost/install.sh` 를 다시 실행해 컨테이너에 반영한다.

## 러너 연결

러너는 컨테이너가 아니라 호스트 Mac 에서 돈다 — `claude`·`codex` 로그인과 작업 폴더가 호스트에 있기 때문이다. 순서는 연결 코드 → connect → register → install-runner.

1. **연결 코드** — 로그인한 화면에서 `/operator` → `연결 코드 발급`. 1회용, 10분 유효.
2. **패키지 설치** — 호스트 `python3` 에 이 저장소를 설치한다(install-runner.sh 도 같은 설치를 한다).

   ```bash
   python3 -m pip install -e .
   ```

3. **connect** — 연결 코드를 연결 토큰으로 바꿔 `~/Library/Application Support/workflow-connector/` 의 0600 파일에 둔다.

   ```bash
   python3 -m workflow.connector connect --server http://127.0.0.1:8000 --code <연결 코드>
   ```

4. **register** — 작업 폴더와 도구를 이 Mac 에 등록한다. 폴더마다, 도구마다 한 번. 검증 명령(`--verify 이름=명령`)은 이 Mac 의 로컬 상태에만 저장되고 서버에는 이름만 보고된다.

   ```bash
   python3 -m workflow.connector register --id <등록 id> --repo <작업 폴더> --repository-id <저장소 id> --tool claude
   python3 -m workflow.connector register --id <등록 id> --repo <작업 폴더> --repository-id <저장소 id> --tool codex --verify "vp-pytest=python3 -m pytest -q"
   ```

5. **install-runner** — launchd 에 러너를 올린다. 로그인 때 자동 시작되고 꺼지면 다시 뜬다(라벨 `com.workflow.selfhost.connector`, 로그 `~/Library/Logs/workflow-connector-selfhost/`).

   ```bash
   deploy/selfhost/install-runner.sh
   ```

   연결 토큰 파일이 없으면 plist 만 쓰고 적재하지 않는다 — 3 을 한 뒤 다시 실행한다. `DRY_RUN=1` 은 할 일과 plist 내용만 보여 준다. plist 에 토큰·API 키는 들어가지 않는다.

그다음 화면에서 Agent 를 만들고(`/operator/agents`, 연결 방식 `local`, 로컬 등록 ID = 4 의 `--id`) 워크스페이스에 등록한다(`/agents/register`). Agent·능력 범위를 고르는 방법은 [GitHub 런북 3절](github/README.md#3-agent-와-로컬-등록--같은-기기에서-수정검토)과 같다.

공개 데모용 러너(`com.workflow.connector`)를 같은 Mac 에서 같이 쓰면 연결 토큰 파일 위치가 겹친다. 그때는 한쪽에 `WORKFLOW_CONNECTOR_HOME` 을 따로 준다.

## GitHub 연결

GitHub 이슈를 업무로 가져오고 결과를 이슈 댓글로 남긴다. 선택 기능이다 — 토큰이 비면 꺼진다. 자세한 동작·중지·복구는 [GitHub 런북](github/README.md).

1. **토큰** — fine-grained personal access token, *Only select repositories* 로 대상 저장소만.
   - **Issues: Read and write**, **Metadata: Read-only**(자동 포함).
   - 기준선 가져오기를 쓸 때만 **Pull requests: Read-only** 를 더한다(GraphQL 로 이슈를 닫은 병합 PR 을 읽는다).
   - Contents·Actions 등 그 밖의 권한, classic PAT 은 쓰지 않는다.
2. **`.env` 에 적는다** — `WORKFLOW_GITHUB_TOKEN`(토큰), `WORKFLOW_GITHUB_REPOS`(허용할 `owner/name`, 콤마 구분), `WORKFLOW_PUBLIC_URL`(댓글의 업무 링크 앞부분, 예 `http://127.0.0.1:8000`). 그리고 반영:

   ```bash
   deploy/selfhost/install.sh
   ```

3. **소스 설정** — `/operator/github` 에서 저장소·이슈 범위(라벨·시작 시각·번호)·검증 프로필·검토 Agent 를 정하고, 담당자(GitHub 숫자 ID)를 Agent 에 잇는다.
4. **기준선 가져오기** — `/metrics` 의 `기준선 대 도입 후` 표에서 소스마다 가져온다(운영자 API `POST /operator/github/sources/{source_id}/baseline`). 소스 연결 전에 열린 이슈 → 병합 PR 시간을 기준선으로 쓴다. 다시 가져오면 전체를 바꾼다.

토큰 값은 `.env` 에만 둔다. 화면·API 는 `토큰 설정됨`/`토큰 없음`만 보이고, 러너가 띄우는 `claude`·`codex` 프로세스 환경에도 들어가지 않는다.

## 백업·복원

데이터는 볼륨 `runloom_workflow-data` 의 `/data/central.sqlite`(DB)와 `/data/artifacts/`(산출물)다. 백업은 `/data/backups/{이름}/` 에 쌓인다(이름 = UTC 시각 `YYYYMMDDTHHMMSSZ`). `.env`·연결 토큰 파일은 백업하지 않는다 — 따로 보관한다.

백업 만들기(서버·워커가 돌아도 된다 — SQLite 온라인 백업):

```bash
docker compose -p runloom -f deploy/selfhost/compose.yaml exec central python3 -m workflow.server.backup create
docker compose -p runloom -f deploy/selfhost/compose.yaml exec central python3 -m workflow.server.backup create --keep 7   # 최근 7개만 남김
docker compose -p runloom -f deploy/selfhost/compose.yaml exec central python3 -m workflow.server.backup list
```

호스트로 꺼내기(볼륨이 사라져도 남게):

```bash
docker compose -p runloom -f deploy/selfhost/compose.yaml cp central:/data/backups/<이름> ./runloom-backups/<이름>
```

복원 — **반드시 central·worker 를 멈춘 뒤** 한다. 현재 DB 는 `pre-restore-{시각}` 백업으로 먼저 남는다.

```bash
docker compose -p runloom -f deploy/selfhost/compose.yaml stop central worker
docker compose -p runloom -f deploy/selfhost/compose.yaml run --rm central python3 -m workflow.server.backup restore <이름> --force
docker compose -p runloom -f deploy/selfhost/compose.yaml up -d
```

`--force` 는 대상 DB 가 이미 있을 때 필요하다(없으면 거부, 종료 코드 1). 백업이 손상됐으면 아무것도 바꾸지 않고 종료 코드 1, 없는 이름이면 2. 복원 뒤 러너는 그대로 붙는다 — 연결 토큰은 DB 가 아니라 호스트 파일이지만, 복원한 DB 에 그 러너 연결이 없던 시점이면 connect 를 다시 한다.

## 업그레이드

```bash
docker compose -p runloom -f deploy/selfhost/compose.yaml exec central python3 -m workflow.server.backup create
git pull
deploy/selfhost/install.sh
deploy/selfhost/install-runner.sh
```

- `install.sh` 재실행 = 이미지 재빌드·재기동. `.env` 와 볼륨은 그대로다. 스키마가 바뀌었으면 서버·워커가 시작할 때 `init_schema` 가 올린다. 올리지 못하면 `/healthz` 가 503 이 되고 설치 스크립트가 실패로 끝난다 — 그때는 로그를 보고 위 백업으로 복원한다.
- 러너는 저장소를 `pip install -e` 로 쓰므로 `git pull` 로 코드가 바뀐다. `install-runner.sh` 재실행이 러너를 다시 띄운다. 서버를 먼저, 러너를 나중에 올린다.

## 제거

멈추기만(데이터·설정 유지, 다시 `install.sh` 로 올림):

```bash
docker compose -p runloom -f deploy/selfhost/compose.yaml down
```

러너 내리기:

```bash
launchctl bootout gui/$(id -u)/com.workflow.selfhost.connector
rm ~/Library/LaunchAgents/com.workflow.selfhost.connector.plist
```

**데이터까지 삭제** — 아래 명령은 볼륨 `runloom_workflow-data` 를 지운다. DB·산출물·볼륨 안의 백업(`/data/backups`)이 **모두 삭제되고 되돌릴 수 없다**. 먼저 백업을 만들어 위 `cp` 로 호스트에 꺼내 둔다.

```bash
docker compose -p runloom -f deploy/selfhost/compose.yaml down -v
```

그 밖에 남는 것: `deploy/selfhost/.env`(비밀값), 연결 토큰·러너 상태 `~/Library/Application Support/workflow-connector/`, 이미지 `workflow-selfhost:local`(`docker image rm workflow-selfhost:local`). 필요 없으면 직접 지운다.

## 문제 해결

| 증상 | 확인 | 조치 |
|---|---|---|
| 설치가 `/healthz` 대기에서 실패 | `docker compose -p runloom -f deploy/selfhost/compose.yaml ps`, `… logs central worker` | `.env` 비밀값이 비었는지(`SESSION_SECRET`·`OPERATOR_TOKEN` 은 필수), 스키마 오류인지 로그로 본다. 느린 첫 빌드면 `HEALTH_TIMEOUT=300 deploy/selfhost/install.sh` |
| `/healthz` 503 `{"status": "error"}` | 로그의 `init_schema` 오류 | DB 가 없거나 스키마 버전이 코드와 다르다. 업그레이드 직후면 백업으로 복원하거나 코드를 이전 커밋으로 되돌린다 |
| 포트 충돌(`port is already allocated`, `address already in use`) | `lsof -nP -iTCP:8000 -sTCP:LISTEN` | `.env` 의 `WORKFLOW_PORT` 를 바꾸고 `install.sh` 재실행. `WORKFLOW_PUBLIC_URL` 과 러너 `connect --server` 주소도 같은 포트로 맞춘다(러너는 connect 를 다시) |
| 러너 오프라인(업무가 `executor_offline` 대기) | `launchctl print gui/$(id -u)/com.workflow.selfhost.connector`, `~/Library/Logs/workflow-connector-selfhost/stderr.log` | 연결 토큰 파일이 없으면 connect 후 `install-runner.sh` 재실행. 도구를 못 찾으면 `claude`·`codex` 가 설치된 셸에서 `install-runner.sh` 재실행(PATH 를 다시 적는다). 서버 주소·포트가 맞는지 본다 |
| 러너가 도구 로그인 오류 | 로그의 도구 출력 | 호스트 터미널에서 `claude`·`codex` 로 다시 로그인. 러너는 호스트 로그인을 그대로 쓴다 |
| SQLite `database is locked` | 로그 | central·worker 는 WAL·5초 대기로 같이 쓴다. 호스트에서 DB 파일을 직접 열거나 복사하지 않는다(백업 CLI 를 쓴다). 볼륨을 bind mount(`./data:/data`)로 바꾸지 않는다 — Docker Desktop 파일 공유 계층에서는 잠금이 보장되지 않는다. 복원은 서비스를 멈춘 뒤에만 |
| 로그인이 계속 풀림 | `.env` 의 `SESSION_SECRET` 변경 여부 | 바꿨다면 다시 로그인. 컨테이너를 다시 만들 때마다 값이 바뀌지는 않는다(`.env` 가 원본) |

## 알려진 한계

- **원격 접속 없음.** 포트는 `127.0.0.1` 에만 열린다. 다른 기기·휴대폰에서 접속하려면 터널(Cloudflare Tunnel 등)이 필요하고 이 phase 범위 밖이다.
- **워크스페이스 하나, 로그인 한 종류.** 팀 계정·권한 없음.
- **등록 뒤 새 커밋을 따라가지 않는다.** 업무의 기준 커밋은 등록 때 정해진다. 저장소에 새 커밋이 생겨도 기준을 옮기지 않아 러너가 `base_commit_mismatch` 로 멈출 수 있다 — 새 기준 커밋 추적·알림 웹훅·검증 환경은 `12-real-repo`.
- **진단 데모 없음.** compose 에 진단 API 가 없어 진단 기능은 꺼져 있다.
- **자동 백업 일정 없음.** 백업은 위 명령으로 직접 만든다.
- **러너는 macOS 전용**(launchd). 연결 프로그램 하나는 한 번에 실행 하나만 돈다.
- **기존 DB 이전 도구 없음.** 새 설치는 빈 DB.
