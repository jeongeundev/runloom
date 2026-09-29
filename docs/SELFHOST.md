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

러너는 컨테이너가 아니라 호스트 Mac 에서 돈다 — `claude`·`codex` 로그인과 작업 폴더가 호스트에 있기 때문이다. 저장소를 연결한 뒤([GitHub 연결](#github-연결)) 저장소 카드에서 붙인다.

1. **[러너 붙이기]** — `/operator/github` 의 저장소 카드에 `이 저장소를 등록한 러너 없음` 과 [러너 붙이기] 버튼이 보인다. 누르면 그 카드에 명령 한 줄이 나온다. 연결 코드가 들어 있고 1회용·10분 유효다(지나면 [다시 발급]).

   ```bash
   deploy/selfhost/install-runner.sh --server http://127.0.0.1:8000 --code <연결 코드> --repo <이 저장소를 클론한 폴더>
   ```

2. **실행** — Runloom 을 설치한 폴더(이 저장소)에서 `<이 저장소를 클론한 폴더>` 만 바꿔 실행한다. 스크립트가 순서대로: 패키지 설치(`pip install -e`) → `python3 -m workflow.connector setup`(연결 + 저장소 등록 — 등록 이름=폴더 이름, 저장소=`origin` 의 GitHub owner/name, 도구=PATH 의 `claude`, 없으면 `codex`) → launchd 적재(라벨 `com.workflow.selfhost.connector`, 로그 `~/Library/Logs/workflow-connector-selfhost/`). 로그인 때 자동 시작되고 꺼지면 다시 뜬다. 서버가 수정·검토 Agent 를 알아서 만든다.
3. **확인** — 카드를 새로고침하면 러너 매칭에 로컬 저장소·수정 Agent·검토 Agent 가 `(자동)` 으로 보인다.

명령 끝에 등록 옵션을 더할 수 있다 — 스크립트가 setup 에 그대로 넘긴다:

- `--tool claude|codex` — 도구를 고른다.
- `--verify 이름=명령` — 검증 프로필. 수정 업무에는 하나 있어야 한다. 명령은 이 Mac 에만 저장되고 서버에는 이름만 보고된다.
- `--link 경로` — worktree 에 원본 폴더로 심볼릭 링크할 git 무시 대상(예: `--link backend/.venv`).
- `--copy 경로` — 링크를 거부하는 도구용으로 원본에서 복사할 git 무시 대상(예: `--copy frontend/node_modules` — Next 16 Turbopack 은 작업 복사본 밖을 가리키는 `node_modules` 링크를 "points out of the filesystem root" 로 거부한다). macOS 는 APFS 복제(`cp -Rc`)라 디스크를 거의 쓰지 않고 수 초에 끝난다.
- `--env 이름=값` — 검증·도구 프로세스 환경에 더할 값(예: 에이전트 전용 테스트 DB 주소). 값은 이 Mac 의 러너 상태에만 저장된다.

연결 코드와 `--env` 값은 plist·스크립트 출력에 쓰지 않는다(`DRY_RUN=1` 도 `***` 로 가린다). 명령행 인자라 실행하는 동안 같은 Mac 의 `ps` 에는 보인다. `DRY_RUN=1` 은 할 일과 plist 내용만 보여 준다. setup 이 실패하면(코드 만료·서버 주소 틀림) launchd 에 적재하지 않고 종료 코드 1 — 카드에서 [다시 발급] 뒤 다시 실행한다. 이미 붙은 저장소를 다른 폴더·Mac 으로 옮길 때는 카드의 고급 설정 → [러너 다시 붙이기].

**git 자격.** 러너는 결과 브랜치 `task/<업무 id>` 를 `origin` 에 push 하고 기준 커밋을 fetch 한다. launchd 는 로그인 셸 설정을 읽지 않으므로 plist 에 `HOME` 을 넣어 `~/.gitconfig`·자격 도우미(`osxkeychain`)·`~/.ssh` 를 찾게 한다. `SSH_AUTH_SOCK`(ssh-agent)은 들어가지 않아 **ssh 원격(`git@github.com:…`)은 암호 걸린 키면 실패할 수 있다** — `origin` 을 https 로 쓰고 `osxkeychain` 에 자격을 두거나, 암호 없는 배포 키를 `~/.ssh/config` 에 지정한다. push 실패는 업무를 막지 않고 사람 요청(`pr_unavailable`)으로 안내된다.

### 고급 — 손으로 connect·register

버튼 없이 붙이거나 한 러너에 여러 폴더를 등록할 때. 연결 코드는 `/operator` → `연결 코드 발급`(1회용, 10분).

```bash
python3 -m pip install -e .
python3 -m workflow.connector connect --server http://127.0.0.1:8000 --code <연결 코드>
python3 -m workflow.connector register --repo <작업 폴더> --tool claude --verify "vp-pytest=python3 -m pytest -q"
deploy/selfhost/install-runner.sh
```

connect 는 연결 코드를 연결 토큰으로 바꿔 `~/Library/Application Support/workflow-connector/` 의 0600 파일에 둔다. register 는 폴더마다 한 번(`--id`·`--repository-id` 를 빼면 폴더 이름·GitHub owner/name). 인자 없는 `install-runner.sh` 는 plist 를 쓰고 연결 토큰 파일이 있을 때만 적재한다 — 없으면 connect 뒤 다시 실행한다.

공개 데모용 러너(`com.workflow.connector`)를 같은 Mac 에서 같이 쓰면 연결 토큰 파일 위치가 겹친다. 그때는 한쪽에 `WORKFLOW_CONNECTOR_HOME` 을 따로 준다.

## GitHub 연결

GitHub 이슈를 업무로 가져오고 결과를 이슈 댓글로 남긴다. 선택 기능이다. 기본은 **버튼 연결** — 내 GitHub 계정에 이 서버 전용 GitHub App 을 하나 만들어 설치한다([ADR-0017](adr/0017-github-app-connection.md)). 내부 ID·토큰을 입력하지 않는다. 자세한 동작·중지·복구는 [GitHub 런북](github/README.md).

상태: 버튼 연결은 가짜 GitHub(`tests/e2e/test_github_app.py`)에 이어 2026-09-27 실제 github.com 에서 App 만들기·설치·이슈 수집까지 했다([CURRENT_HANDOFF](CURRENT_HANDOFF.md)). 결과 브랜치 push → 초안 PR → 병합 추적 → 알림(phase 12)은 가짜 GitHub·로컬 bare 저장소로만 검증했다(`tests/e2e/test_real_repo.py`, [VERIFICATION_LOG](VERIFICATION_LOG.md) 2026-09-28 phase 12 절). 아래 GitHub 화면 이름은 GitHub 문서 기준이고 실제 문구가 조금 다를 수 있다.

### 버튼으로 연결 (기본)

준비: 로그인한 브라우저에 GitHub 도 로그인돼 있어야 한다. 서버 주소는 `http://127.0.0.1:<포트>` 그대로 둔다(`WORKFLOW_PUBLIC_URL` 이 있으면 그 주소로 돌아온다 — 브라우저에서 여는 주소와 같게 맞춘다).

1. `/operator/github` 에서 **[GitHub 연결]** 을 누른다. Runloom 이 App 설정(이름·권한)을 채워 GitHub 로 보낸다.
2. GitHub 의 **App 만들기 화면**에서 확인할 것:
   - App 이름 `runloom-xxxxxx`(무작위 6자 — GitHub 전역에서 겹치지 않게). 바꿔도 된다.
   - 권한: Issues 읽기·쓰기, Pull requests 읽기·쓰기(검토 승인 뒤 초안 PR — ADR-0018), Contents 읽기(PR 생성이 브랜치를 읽는다), Metadata 읽기. 이전에 만든 App 은 [권한 올리기](#app-권한-올리기--phase-12-전에-만든-app). 웹훅은 꺼져 있다(127.0.0.1 은 GitHub 가 부를 수 없다 — 새 이슈는 워커가 1분마다 조회한다).
   - 그대로 **[Create GitHub App]** 을 누른다. 조직 저장소면 `/operator/github/app/new?org=<조직 이름>` 으로 시작한다.
3. Runloom 이 App 개인 키·비밀을 받아 저장하고 GitHub 의 **설치 화면**으로 다시 보낸다. **Only select repositories** 로 대상 저장소(예: OpenArchive)를 고르고 **[Install]** 을 누른다.
4. `/operator/github` 로 돌아오면 고른 저장소마다 카드가 생긴다. 약 1분 안에 열린 이슈가 **전부** 업무 목록에 `대기 · 지시 전` 으로 들어온다(PR·닫힌 이슈 제외).
5. **러너 연결** — 저장소 카드의 [러너 붙이기] 로 그 저장소의 로컬 클론 폴더를 붙인다(위 "러너 연결"). 서버가 수정(`code.fix`)·검토(`code.review`) Agent 를 만든다. 폴더의 `origin` 이 `github.com/<owner>/<name>` 이면 서버가 알아서 짝을 짓는다 — 카드의 러너 매칭에 로컬 저장소·수정 Agent·검증 프로필·검토 Agent 가 `(자동)` 으로 보인다. `이 저장소를 등록한 러너 없음` 이면 register 가 안 됐거나 `origin` 이 다른 저장소다. 수정용 등록에는 `--verify` 가 있어야 한다.
6. **실행은 지시한 것만** — 업무 목록의 **[에이전트에게 맡기기]** 를 누르거나 GitHub 이슈에 `runloom` 라벨을 붙인다. 그 뒤는 자동이다: 수정(기준 = GitHub 기본 브랜치 최신) → 러너가 결과 브랜치 `task/<업무 id>` 를 `origin` 에 push → 검토(재작업 포함) → 검토 승인이면 서버가 초안 PR(`Fixes #<이슈>`)을 연다 → 업무는 `확인 필요 · PR 확인 — #<번호>`. **병합·이슈 종료는 사람이 GitHub 에서 한다** — 병합하면 다음 수집 주기에 업무가 `완료`(사유 `PR 병합`)가 되고 지표의 "이슈 열림 → 병합" 에 들어간다. 병합 없이 PR 을 닫으면 업무는 `실패`. push 가 안 됐거나(원격 자격) PR 을 못 열면(App 권한) 업무에 사람 요청이 남고 직접 push·PR 하는 안내가 붙는다.
7. **기준선 가져오기** — 카드의 [기준선 가져오기] 또는 `/metrics` 의 `기준선 대 도입 후` 표에서. 소스 연결 전에 열린 이슈 → 병합 PR 시간을 기준선으로 쓴다. 다시 가져오면 전체를 바꾼다.

저장소를 더하거나 빼려면 카드 위 **[저장소 추가/변경]**(GitHub 의 App 설치 설정)에서 고르고 저장한다. 돌아오면 카드가 맞춰진다 — 설치에서 뺀 저장소는 수집이 멈춘다.

비밀값: App 개인 키·client secret·webhook secret 은 데이터 볼륨의 비밀 파일(`/data/secrets`, 디렉터리 0700·파일 0600)에만 있다. 설치 토큰은 파일에 쓰지 않고 메모리에만 둔다(1시간마다 새로 받음). DB·화면·로그·백업에는 없고, 러너가 띄우는 `claude`·`codex` 프로세스 환경에도 들어가지 않는다. **백업에 들어가지 않으므로** 볼륨을 지우면(`down -v`) App 을 다시 연결해야 한다 — GitHub 의 옛 App 은 GitHub 설정(Settings → Developer settings → GitHub Apps)에서 지운다.

### App 권한 올리기 — phase 12 전에 만든 App

phase 11 에서 만든 App 은 Pull requests 가 읽기뿐이라 초안 PR 을 열 때 GitHub 가 403 을 주고, 2026-09-29 이전에 만든 App 은 Contents 읽기가 없어 422 `not all refs are readable` 을 준다 — 둘 다 업무에 `GitHub App 권한(Pull requests 쓰기·Contents 읽기) 승인 필요` 사람 요청이 남는다. 새로 만드는 App 은 처음부터 두 권한이 있다. 올리는 순서([GitHub 문서](https://docs.github.com/en/apps/maintaining-github-apps/modifying-a-github-app-registration) 기준):

1. GitHub → **Settings → Developer settings → GitHub Apps** → 이 서버의 App(`runloom-xxxxxx`) → **Edit** → **Permissions & events**.
2. **Repository permissions → Pull requests** 를 **Read and write**, **Contents** 를 **Read-only** 로 바꾸고 맨 아래 **Save changes**. 바뀐 권한은 설치한 계정이 승인해야 적용된다.
3. 설치한 계정(개인이면 본인)의 **Settings → Applications → Installed GitHub Apps** → 이 App 의 **Configure** → 권한 변경 요청을 검토하고 **Accept new permissions**. 조직 설치면 조직 관리자가 조직 설정에서 승인한다.

승인 전에 이미 실패한 업무는 PR 을 다시 열지 않는다 — 사람 요청의 안내대로 `task/<업무 id>` 로 직접 PR 을 연다. 승인 뒤 새로 검토 승인되는 업무부터 자동으로 열린다.

### 고급 — 토큰으로 연결

App 을 만들 수 없을 때. `/operator/github` 의 접힌 **고급 — 토큰으로 연결** 에 fine-grained personal access token 과 저장소(`owner/name`)를 넣는다. 서버가 그 토큰으로 저장소를 읽을 수 있는지 확인한 뒤 비밀 파일(`github_token`)에 저장하고 그 저장소 카드를 만든다. 이후 흐름(맡기기·라벨·자동 매칭)은 같다.

- 토큰: *Only select repositories* 로 대상 저장소만, **Issues: Read and write**, **Metadata: Read-only**(자동). 기준선 가져오기를 쓰면 **Pull requests: Read-only** 도. Contents·Actions 등 그 밖의 권한, classic PAT 은 쓰지 않는다.
- 예전 방식(`.env` 의 `WORKFLOW_GITHUB_TOKEN`·`WORKFLOW_GITHUB_REPOS` + 라벨 범위 소스)도 그대로 동작한다 — [GitHub 런북](github/README.md) 1~4절. 화면에서 넣은 토큰이 환경변수보다 우선한다.

## 알림

사람 차례가 되거나 업무가 실패하면 웹훅 URL 하나로 알린다(선택 기능, [ADR-0018](adr/0018-real-repo-cycle.md) 결정 5). Discord 채널 웹훅을 그대로 넣을 수 있다.

1. **URL 만들기** — Discord 면 채널 설정 → **연동(Integrations) → 웹후크 → 새 웹후크** → **웹후크 URL 복사**. 그 밖의 서비스는 JSON POST 를 받는 URL 이면 된다.
2. **등록** — `/operator/notifications`(왼쪽 목록 "알림")에 붙여 넣고 저장. `https` 만 받는다(같은 Mac 의 수신기는 `http://127.0.0.1…` 도 허용). 저장 뒤 화면에는 `설정됨 · 호스트 <이름>` 만 보인다.
3. **[테스트 보내기]** — 한 번 보내 보고 결과(`보냄`·`HTTP 404`·`시간 초과` 등)를 바로 보여 준다.

언제 오나: 새 사람 요청(`[Runloom] 사람 차례 — 제목: 사유`), 초안 PR 이 열림(`[Runloom] PR 확인 — 제목 <PR 주소>`), 실행 실패로 업무가 끝남(`[Runloom] 실패 — 제목: 사유`). 각 줄 아래 업무 링크(`WORKFLOW_PUBLIC_URL` 기준 — 로그인 필요). 같은 사건은 한 번만 보낸다. Discord 호스트면 `{"content": …}`(2000자), 그 밖은 `content`·`event`·`task_id`·`task_url`·`title`·`pr_url` JSON.

전송 실패(수신 서버 오류·429·연결 실패)는 30초부터 두 배씩 물러나 5번까지 다시 보내고 그 뒤 포기한다 — 화면의 최근 알림 20건에 상태(보냄·대기·실패·건너뜀)와 오류 분류가 보인다. 알림 실패는 업무 상태를 바꾸지 않는다. URL 이 없으면 알림을 쌓지 않는다(나중에 등록해도 지난 사건은 오지 않는다).

URL 은 그 자체가 비밀(아는 사람은 채널에 글을 쓸 수 있다)이라 데이터 볼륨의 비밀 파일(`notify_webhook_url`, 0600)에만 둔다. DB·화면·로그·백업에 없다 — 볼륨을 지우면 다시 등록한다. 바꾸려면 새 URL 을 저장, 끄려면 [삭제].

## 백업·복원

데이터는 볼륨 `runloom_workflow-data` 의 `/data/central.sqlite`(DB)와 `/data/artifacts/`(산출물)다. 백업은 `/data/backups/{이름}/` 에 쌓인다(이름 = UTC 시각 `YYYYMMDDTHHMMSSZ`). `.env`·연결 토큰 파일·GitHub 비밀 파일(`/data/secrets`)은 백업하지 않는다 — 따로 보관하거나 다시 연결한다.

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
- **기준 커밋은 러너가 볼 때만 따라간다.** 러너가 60초마다 `git fetch origin` 해 기본 브랜치 최신을 보고한다. 러너가 꺼져 있거나 실행 중인 동안은 fetch 하지 않으므로, 그 사이 push 된 커밋은 다음 보고 뒤의 새 업무부터 기준이 된다. 재작업은 검토한 결과 커밋에서 이어 간다.
- **`--link` 는 원본 폴더와 공유한다.** worktree 의 `backend/.venv` 등은 원본 폴더로 향하는 심볼릭 링크라 에이전트가 설치물을 바꾸면 원본도 바뀐다. 링크된 편집 설치(`pip install -e`)는 원본 코드를 가리키므로 검증은 `python -m pytest` 처럼 작업 폴더를 경로 앞에 두는 명령으로 한다.
- **PR 을 한 번 못 열면 다시 열지 않는다.** App 권한·push 실패로 사람 요청이 된 업무는 사람이 직접 PR 을 연다. 그 PR 의 병합은 업무에 자동으로 반영되지 않는다 — 업무 상세의 운영자 검토 승인으로 마감한다(이슈가 병합 PR 로 닫히면 지표의 병합 시각은 채워진다).
- **알림 URL 은 하나.** 사건 종류·저장소별로 나눠 보내지 않는다.
- **진단 데모 없음.** compose 에 진단 API 가 없어 진단 기능은 꺼져 있다.
- **자동 백업 일정 없음.** 백업은 위 명령으로 직접 만든다.
- **러너는 macOS 전용**(launchd). 연결 프로그램 하나는 한 번에 실행 하나만 돈다.
- **기존 DB 이전 도구 없음.** 새 설치는 빈 DB.
