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

**처음 접속(첫 설정)** — 계정이 아직 없으면 로그인 화면이 운영자 토큰 칸을 보인다.

1. 브라우저에서 `http://127.0.0.1:8000/login` 을 연다.
2. 운영자 토큰을 확인해 넣고, 관리자 계정(이메일·표시 이름·비밀번호 10~128자)을 만든다. 토큰 값은 `.env` 에만 있다 — 화면·로그·백업에는 없다.

   ```bash
   grep OPERATOR_TOKEN deploy/selfhost/.env
   ```

**그 뒤** — `/login` 은 이메일·비밀번호만 받는다. 운영자 토큰은 첫 설정과 비밀번호 복구(`/login/recover`, 아래 "팀")에만 쓴다. 워크스페이스는 하나이고 팀원은 초대 링크로 들어온다([ADR-0021](adr/0021-team-accounts-and-roles.md)).

로그인은 14일 유지되고 서버가 기억한다 — 로그아웃(왼쪽 목록 아래 버튼)·비밀번호 변경·비활성화가 그 로그인을 바로 끝낸다. 같은 이메일로 60초 안에 5번 틀리면 그 이메일은 잠시 막힌다(`잠시 후 다시 시도하세요`).

`OPERATOR_TOKEN`·`SESSION_SECRET` 은 계속 필수다. `SESSION_SECRET` 은 GitHub App 연결 중 확인값 서명에 쓰고, 바꿔도 로그인은 풀리지 않는다. `.env` 를 고친 뒤에는 `deploy/selfhost/install.sh` 를 다시 실행해 컨테이너에 반영한다.

## 팀

역할은 둘이다. **관리자** — GitHub 연결·소스·n8n 입구 토큰·매핑 표·종류·후속 규칙·공용 알림·팀 관리·모든 러너 해제. **멤버** — 업무 등록·에이전트에게 맡기기·사람 요청 응답·지표 보기·자기 러너 붙이기와 해제·내 설정. 권한 밖 화면은 403 이다.

**팀원 초대** — 관리자가 `/connect?tab=team`(연결 → 팀·담당자)에서 역할을 골라 [초대 링크 만들기]. 링크는 **그 화면에서 한 번만** 보인다 — 복사해 메신저 등으로 전한다(이메일은 보내지 않는다). 7일 동안 한 번 쓸 수 있고, 받은 사람이 이메일·표시 이름·비밀번호를 넣으면 가입·로그인된다. 쓰지 않은 초대는 목록에서 취소한다. 멤버는 삭제하지 않고 비활성화한다(로그인이 바로 끝나고 다시 활성화할 수 있다). 활성 관리자가 0 이 되는 강등·비활성화는 거부된다.

**비밀번호 분실**
- 팀원: 관리자가 `/connect?tab=team` 에서 그 멤버의 [재설정 링크] 를 만들어 전한다(24시간·한 번). 링크로 새 비밀번호를 정하면 그 멤버의 다른 로그인은 모두 끝난다.
- 관리자 본인(다른 관리자도 없을 때): `/login/recover` 에서 운영자 토큰 + 관리자 이메일 + 새 비밀번호.
- 자기 비밀번호 변경은 `/me`(왼쪽 목록 "내 설정").

**공개 주소와 원격 접속** — 기본은 이 Mac 에서만(`http://127.0.0.1:8000`) 연다. 팀원이 다른 기기에서 들어오려면 이 Mac 앞에 터널을 두고 그 주소를 `.env` 의 `WORKFLOW_PUBLIC_URL` 에 적은 뒤 `deploy/selfhost/install.sh` 를 다시 실행한다. 이 값이 초대·재설정 링크와 알림의 업무 링크 앞부분, 변경 요청의 출처(Origin) 검사 기준이 된다. `https://` 로 시작하면 로그인 쿠키에 `Secure` 가 붙는다.

- **Tailscale** — 팀원이 같은 tailnet 에 있을 때. 이 Mac 에서 `tailscale serve` 로 8000 포트를 tailnet 안 `https://<기기 이름>.<tailnet>.ts.net` 으로 연다(명령 형식은 설치한 Tailscale 버전 문서를 따른다). 공개 인터넷에는 열리지 않는다.
- **Cloudflare Tunnel** — 공개 인터넷에서 들어올 때. `cloudflared` 로 이름 있는 터널을 만들어 `http://127.0.0.1:8000` 으로 보낸다(임시 터널은 주소가 매번 바뀌어 초대 링크가 깨진다). Cloudflare Access 같은 앞단 인증을 함께 두기를 권한다.
- **공개 인터넷에 열 때는 `https://` 주소만 쓴다.** `http://` 로 열면 비밀번호·로그인 쿠키가 그대로 흐른다. 설치 스크립트는 터널을 만들지 않는다.

## 러너 연결

러너는 컨테이너가 아니라 호스트 Mac 에서 돈다 — `claude`·`codex` 로그인과 작업 폴더가 호스트에 있기 때문이다. 저장소를 연결한 뒤([GitHub 연결](#github-연결)) 저장소 카드에서 붙인다.

1. **[러너 붙이기]** — `/connect?tab=sources`(연결 → 가져올 곳) 의 저장소 카드에 `이 저장소를 등록한 러너 없음` 과 [러너 붙이기] 버튼이 보인다. 누르면 그 카드에 명령 한 줄이 나온다. 연결 코드가 들어 있고 1회용·10분 유효다(지나면 [다시 발급]).

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

버튼 없이 붙이거나 한 러너에 여러 폴더를 등록할 때. 연결 코드는 `/connect?tab=advanced`(연결 → 고급) → `연결 코드 발급`(1회용, 10분).

```bash
python3 -m pip install -e .
python3 -m workflow.connector connect --server http://127.0.0.1:8000 --code <연결 코드>
python3 -m workflow.connector register --repo <작업 폴더> --tool claude --verify "vp-pytest=python3 -m pytest -q"
deploy/selfhost/install-runner.sh
```

connect 는 연결 코드를 연결 토큰으로 바꿔 `~/Library/Application Support/workflow-connector/` 의 0600 파일에 둔다. register 는 폴더마다 한 번(`--id`·`--repository-id` 를 빼면 폴더 이름·GitHub owner/name). 인자 없는 `install-runner.sh` 는 plist 를 쓰고 연결 토큰 파일이 있을 때만 적재한다 — 없으면 connect 뒤 다시 실행한다.

`main` 데모 러너(`com.workflow.connector`)를 같은 Mac 에 둔 경우 연결 토큰 파일 위치가 겹치므로 한쪽에 `WORKFLOW_CONNECTOR_HOME` 을 따로 준다.

### 한 Mac 에 러너 두 대(시험용)

다른 사람에게 맡기는 흐름(소유자 알림·승인)을 Mac 한 대에서 시험할 때. 러너 소유자는 [러너 붙이기]를 누른 멤버이므로 두 번째 러너는 두 번째 멤버 계정으로 붙인다.

1. 관리자 계정으로 `/connect?tab=team` 에서 두 번째 멤버를 초대하고, 초대 링크로 계정을 만든다(다른 브라우저 프로필이나 시크릿 창).
2. 그 계정으로 로그인해 `/connect?tab=sources` 저장소 카드의 [러너 붙이기]를 누른다. 카드에 나온 명령 끝에 `--name b` 를 붙여 Runloom 설치 폴더에서 실행한다.

   ```bash
   deploy/selfhost/install-runner.sh --server http://127.0.0.1:8000 --code <연결 코드> --repo <이 저장소를 클론한 폴더> --name b
   ```

`--repo` 는 **두 번째 클론**을 준다 — 폴더 이름이 첫 러너가 등록한 폴더와 달라야 한다(예 `~/demo/runloom-sandbox-b`). 로컬 등록 이름 = 폴더 이름이고, 다른 러너가 이미 쓰는 이름이면 등록이 409 `registration_taken` 으로 실패한다. 한 클론을 두 러너가 함께 쓰면 결과 브랜치(`runloom/RUN-n`)도 겹친다.

두 러너가 같은 GitHub 저장소를 등록하면 저장소 카드의 수정·검토 에이전트 칸은 비워 둔다 — 업무를 맡긴 에이전트가 수정하고, 검토는 그 에이전트의 러너가 한다(검토는 수정 결과 커밋이 있는 러너에서만 돈다). 카드에 "수정 Agent 2개 — 설정에서 하나 고르세요" 가 보여도 패널에서 담당 에이전트를 고르면 그 에이전트로 시작한다.

`--name b` 러너는 label `com.workflow.selfhost.connector.b`, plist `~/Library/LaunchAgents/com.workflow.selfhost.connector.b.plist`, 로그 `~/Library/Logs/workflow-connector-selfhost-b/`, 러너 홈(연결 토큰·등록) `~/Library/Application Support/workflow-connector-b/` 를 쓴다(plist 의 `WORKFLOW_CONNECTOR_HOME`). 이름 없는 러너와 겹치지 않아 둘 다 돈다. 이름은 영소문자·숫자·하이픈 1~32자(하이픈으로 시작·끝 불가)이고, 틀리면 아무것도 하지 않고 종료 코드 2 로 끝난다. 같은 이름으로 다시 실행하면 그 러너만 다시 적재한다.

두 러너는 같은 Mac 사용자로 돌기 때문에 **같은 `claude`·`codex` 로그인(구독)과 같은 git 자격을 쓴다** — 소유자가 다르게 보이는 것은 Runloom 안에서뿐이다. 실제로 다른 사람이 쓰려면 그 사람의 Mac 에서 붙인다.

해제(이 러너만):

```bash
launchctl bootout gui/$(id -u)/com.workflow.selfhost.connector.b
rm ~/Library/LaunchAgents/com.workflow.selfhost.connector.b.plist
rm -r ~/Library/"Application Support"/workflow-connector-b ~/Library/Logs/workflow-connector-selfhost-b
```

## GitHub 연결

GitHub 이슈를 업무로 가져오고 결과를 이슈 댓글로 남긴다. 선택 기능이다. 기본은 **버튼 연결** — 내 GitHub 계정에 이 서버 전용 GitHub App 을 하나 만들어 설치한다([ADR-0017](adr/0017-github-app-connection.md)). 내부 ID·토큰을 입력하지 않는다. 자세한 동작·중지·복구는 [GitHub 런북](github/README.md).

상태: 버튼 연결은 가짜 GitHub(`tests/e2e/test_github_app.py`)에 이어 2026-09-27 실제 github.com 에서 App 만들기·설치·이슈 수집까지 했다([CURRENT_HANDOFF](CURRENT_HANDOFF.md)). 결과 브랜치 push → 초안 PR → 병합 추적 → 알림(phase 12)은 가짜 GitHub·로컬 bare 저장소로만 검증했다(`tests/e2e/test_real_repo.py`, [VERIFICATION_LOG](VERIFICATION_LOG.md) 2026-09-28 phase 12 절). 아래 GitHub 화면 이름은 GitHub 문서 기준이고 실제 문구가 조금 다를 수 있다.

### 버튼으로 연결 (기본)

준비: 로그인한 브라우저에 GitHub 도 로그인돼 있어야 한다. 서버 주소는 `http://127.0.0.1:<포트>` 그대로 둔다(`WORKFLOW_PUBLIC_URL` 이 있으면 그 주소로 돌아온다 — 브라우저에서 여는 주소와 같게 맞춘다).

1. `/connect?tab=sources` 에서 **[GitHub 연결]** 을 누른다. Runloom 이 App 설정(이름·권한)을 채워 GitHub 로 보낸다.
2. GitHub 의 **App 만들기 화면**에서 확인할 것:
   - App 이름 `runloom-xxxxxx`(무작위 6자 — GitHub 전역에서 겹치지 않게). 바꿔도 된다.
   - 권한: Issues 읽기·쓰기, Pull requests 읽기·쓰기(검토 승인 뒤 초안 PR — ADR-0018), Contents 읽기(PR 생성이 브랜치를 읽는다), Metadata 읽기. 이전에 만든 App 은 [권한 올리기](#app-권한-올리기--phase-12-전에-만든-app). 웹훅은 꺼져 있다(127.0.0.1 은 GitHub 가 부를 수 없다 — 새 이슈는 워커가 1분마다 조회한다).
   - 그대로 **[Create GitHub App]** 을 누른다. 조직 저장소면 `/operator/github/app/new?org=<조직 이름>` 으로 시작한다.
3. Runloom 이 App 개인 키·비밀을 받아 저장하고 GitHub 의 **설치 화면**으로 다시 보낸다. **Only select repositories** 로 대상 저장소(예: OpenArchive)를 고르고 **[Install]** 을 누른다.
4. `/connect?tab=sources` 로 돌아오면 고른 저장소마다 카드가 생긴다. 약 1분 안에 열린 이슈가 **전부** 업무 목록에 `대기 · 지시 전` 으로 들어온다(PR·닫힌 이슈 제외).
5. **러너 연결** — 저장소 카드의 [러너 붙이기] 로 그 저장소의 로컬 클론 폴더를 붙인다(위 "러너 연결"). 서버가 수정(`code.fix`)·검토(`code.review`) Agent 를 만든다. 폴더의 `origin` 이 `github.com/<owner>/<name>` 이면 서버가 알아서 짝을 짓는다 — 카드의 러너 매칭에 로컬 저장소·수정 Agent·검증 프로필·검토 Agent 가 `(자동)` 으로 보인다. `이 저장소를 등록한 러너 없음` 이면 register 가 안 됐거나 `origin` 이 다른 저장소다. 수정용 등록에는 `--verify` 가 있어야 한다.
6. **실행은 지시한 것만** — 업무 목록의 **[에이전트에게 맡기기]** 를 누르거나 GitHub 이슈에 `runloom` 라벨을 붙인다. 그 뒤는 자동이다: 수정(기준 = GitHub 기본 브랜치 최신) → 러너가 결과 브랜치 `task/<업무 id>` 를 `origin` 에 push → 검토(재작업 포함) → 검토 승인이면 서버가 초안 PR(`Fixes #<이슈>`)을 연다 → 업무는 `확인 필요 · PR 확인 — #<번호>`. **병합·이슈 종료는 사람이 GitHub 에서 한다** — 병합하면 다음 수집 주기에 업무가 `완료`(사유 `PR 병합`)가 되고 지표의 "이슈 열림 → 병합" 에 들어간다. 병합 없이 PR 을 닫으면 업무는 `실패`. push 가 안 됐거나(원격 자격) PR 을 못 열면(App 권한) 업무에 사람 요청이 남고 직접 push·PR 하는 안내가 붙는다.
7. **기준선 가져오기** — 카드의 [기준선 가져오기] 또는 모니터링(`/monitor`)의 `기준선 대 도입 후` 표에서. 소스 연결 전에 열린 이슈 → 병합 PR 시간을 기준선으로 쓴다. 다시 가져오면 전체를 바꾼다.

저장소를 더하거나 빼려면 카드 위 **[저장소 추가/변경]**(GitHub 의 App 설치 설정)에서 고르고 저장한다. 돌아오면 카드가 맞춰진다 — 설치에서 뺀 저장소는 수집이 멈춘다.

비밀값: App 개인 키·client secret·webhook secret 은 데이터 볼륨의 비밀 파일(`/data/secrets`, 디렉터리 0700·파일 0600)에만 있다. 설치 토큰은 파일에 쓰지 않고 메모리에만 둔다(1시간마다 새로 받음). DB·화면·로그·백업에는 없고, 러너가 띄우는 `claude`·`codex` 프로세스 환경에도 들어가지 않는다. **백업에 들어가지 않으므로** 볼륨을 지우면(`down -v`) App 을 다시 연결해야 한다 — GitHub 의 옛 App 은 GitHub 설정(Settings → Developer settings → GitHub Apps)에서 지운다.

### App 권한 올리기 — phase 12 전에 만든 App

phase 11 에서 만든 App 은 Pull requests 가 읽기뿐이라 초안 PR 을 열 때 GitHub 가 403 을 주고, 2026-09-29 이전에 만든 App 은 Contents 읽기가 없어 422 `not all refs are readable` 을 준다 — 둘 다 업무에 `GitHub App 권한(Pull requests 쓰기·Contents 읽기) 승인 필요` 사람 요청이 남는다. 새로 만드는 App 은 처음부터 두 권한이 있다. 올리는 순서([GitHub 문서](https://docs.github.com/en/apps/maintaining-github-apps/modifying-a-github-app-registration) 기준):

1. GitHub → **Settings → Developer settings → GitHub Apps** → 이 서버의 App(`runloom-xxxxxx`) → **Edit** → **Permissions & events**.
2. **Repository permissions → Pull requests** 를 **Read and write**, **Contents** 를 **Read-only** 로 바꾸고 맨 아래 **Save changes**. 바뀐 권한은 설치한 계정이 승인해야 적용된다.
3. 설치한 계정(개인이면 본인)의 **Settings → Applications → Installed GitHub Apps** → 이 App 의 **Configure** → 권한 변경 요청을 검토하고 **Accept new permissions**. 조직 설치면 조직 관리자가 조직 설정에서 승인한다.

승인 전에 이미 실패한 업무는 PR 을 다시 열지 않는다 — 사람 요청의 안내대로 `task/<업무 id>` 로 직접 PR 을 연다. 승인 뒤 새로 검토 승인되는 업무부터 자동으로 열린다.

### 고급 — 토큰으로 연결

App 을 만들 수 없을 때. `/connect?tab=sources` 의 접힌 **고급 — 토큰으로 연결** 에 fine-grained personal access token 과 저장소(`owner/name`)를 넣는다. 서버가 그 토큰으로 저장소를 읽을 수 있는지 확인한 뒤 비밀 파일(`github_token`)에 저장하고 그 저장소 카드를 만든다. 이후 흐름(맡기기·라벨·자동 매칭)은 같다.

- 토큰: *Only select repositories* 로 대상 저장소만, **Issues: Read and write**, **Metadata: Read-only**(자동). 기준선 가져오기를 쓰면 **Pull requests: Read-only** 도. Contents·Actions 등 그 밖의 권한, classic PAT 은 쓰지 않는다.
- 예전 방식(`.env` 의 `WORKFLOW_GITHUB_TOKEN`·`WORKFLOW_GITHUB_REPOS` + 라벨 범위 소스)도 그대로 동작한다 — [GitHub 런북](github/README.md) 1~4절. 화면에서 넣은 토큰이 환경변수보다 우선한다.

## Jira 연결

Jira Cloud 이슈를 업무로 가져오고, 업무가 진행되면 Jira 상태를 옮기고, 후속 업무를 Jira 이슈로 만든다. 선택 기능이다([ADR-0024](adr/0024-jira-source.md), 설계 [ARCHITECTURE "Jira 소스 — phase 18"](ARCHITECTURE.md)). 실행·PR 은 GitHub 저장소에서 하므로 **GitHub 연결과 러너가 먼저** 있어야 한다.

상태: 2026-10-01 가짜 Jira(httpx `MockTransport`)·가짜 GitHub·가짜 러너로만 검증했다(`tests/e2e/test_jira_cycle.py`, [VERIFICATION_LOG](VERIFICATION_LOG.md) phase 18 절). 실제 Jira Cloud 연동은 아직 하지 않았다 — 아래 Atlassian 화면 이름은 문서 기준이다.

1. **토큰 만들기** — Atlassian 계정의 [API 토큰 화면](https://id.atlassian.com/manage-profile/security/api-tokens)(연결 칸의 "토큰 만들기" 새 창)에서 만든다. 스코프를 고르는 토큰이면 `read:jira-work`·`write:jira-work`·`read:jira-user` 를 준다. 토큰은 만든 화면에서 한 번만 보인다.
2. **연결** — `/connect?tab=sources` 의 **Jira 연결** 에 사이트 주소(`https://<이름>.atlassian.net` 형식만 — 다른 호스트·경로·포트는 거부), 계정 이메일, 토큰을 넣는다. 서버가 사이트의 cloudId 와 내 계정(`myself`)을 확인한 뒤 저장한다 — 게이트웨이(`api.atlassian.com/ex/jira/<cloudId>`)로 되면 그것을, 안 되면 사이트 주소를 이후 호출 기준으로 쓴다. `이메일·토큰이 맞지 않습니다` 면 401, `권한(스코프)이 부족합니다` 면 403 이다. 성공하면 `연결됨 · 이름 · 사이트` 가 보인다.
3. **프로젝트 추가** — 프로젝트 찾기(키·이름) → 결과 줄에서 **연결 저장소**(이미 연결한 GitHub 저장소 하나 — 그 프로젝트 업무는 모두 이 저장소에서 실행·PR 된다)와 **시작점**(지금부터 / 열린 업무 전부)을 고르고 [추가]. 시작점은 추가할 때만 고른다.
4. **프로젝트 설정** — 프로젝트마다:
   - 가져올 이슈 유형(비우면 전부).
   - **세 상태** — 업무가 그 순간에 들어가면 Jira 이슈를 그 상태로 옮긴다. 후보는 그 프로젝트의 실제 상태 목록이고 비워 두면 그 순간은 옮기지 않는다.

     | 순간 | Runloom 업무 | 예(회사 형식) |
     |---|---|---|
     | 작업 시작 | `에이전트 작업 중`(맡긴 에이전트 착수) 또는 `직접 작업 중` | 진행 중 |
     | PR 열림 | `PR · 검토` | 리뷰중 |
     | 업무 완료 | `완료`(PR 병합) | 종료 |
   - 후속 이슈 유형(비우면 후속 업무를 Jira 에 만들지 않는다), 켜짐.
   - Jira 에서 이슈 유형·상태를 바꿨으면 **[목록 새로 고침]**.
5. **맡기기** — 약 1분 안에 이슈가 업무 목록에 `새로 들어옴` 으로 들어온다(종류는 매핑 표 — 기본 `bug_fix`). **[에이전트에게 맡기기]**(또는 [내 세션에서 작업]) 뒤에만 착수한다. 수정·검토는 GitHub 업무와 같고, 검토 승인 뒤 연결 저장소에 초안 PR(브랜치 `runloom/RUN-n`, 제목 `RUN-n 제목`, 본문 첫 줄 `원본: SHOP-12 — <이슈 주소>`, `Fixes` 없음)이 열린다. 병합은 사람이 GitHub 에서 한다 — 병합하면 업무 `완료`.

Jira 에서 이슈를 완료 범주(예: 종료)로 옮기면 GitHub 이슈를 닫은 것과 같다 — 도는 실행은 끊지 않고, 다음 단계(검토 등)를 시작하지 않고 기다린다. 다시 열면 이어간다. Jira 상태로 Runloom 업무를 완료·종료하지는 않는다(완료는 PR 병합).

상태 옮기기가 안 되면(목표 상태로 가는 전환이 없음, 전환 화면에 필수 칸, 권한) 재시도하지 않고 업무 패널의 "원본에 남긴 것" 에 `Jira 상태 → 리뷰중 · 반영 실패 · 이유` 가, 연결 화면에 `Jira 반영 실패 N건` 이 보인다. Jira 를 고친 뒤에는 Jira 에서 직접 옮긴다. 5xx·429·연결 오류는 물러났다가 다시 보낸다. 토큰이 만료·폐기되면(401) 연결 칸이 경고로 바뀌고 가져오기·옮기기가 멈춘다 — [다시 연결] 에 새 토큰을 넣으면 이어간다.

**후속 이슈** — 후속 규칙(연결 → 종류 탭)이 **새 업무** 로 후속을 만들고, 원인 업무가 Jira 원본이며, 그 프로젝트에 후속 이슈 유형이 있으면 같은 프로젝트에 이슈를 하나 만든다: 제목 = 새 업무 제목, 라벨 `runloom`·`runloom-RUN-n`, 원인 이슈와 `Relates` 링크(그 유형이 없으면 링크만 건너뜀). 다음 가져오기가 그 이슈를 받아도 같은 업무다. 응답을 잃으면 라벨로 찾아 한 번만 만든다.

**끊기** — [연결 끊기] 는 토큰 파일을 지우고 연결을 끊음으로 표시한다. 프로젝트 설정과 가져온 업무는 남는다(같은 사이트로 다시 연결하면 이어간다).

비밀값: Jira API 토큰은 데이터 볼륨의 비밀 파일(`/data/secrets/jira_api_token`, 0600)에만 있다. DB 에는 사이트 주소·cloudId·이메일·계정 id·표시 이름만 둔다. 토큰은 화면·로그·백업에 없고 러너 프로세스 환경에도 들어가지 않는다. **백업에 들어가지 않으므로** 볼륨을 지우면(`down -v`) 다시 연결한다.

## 알림

사람 차례가 되거나 업무가 실패하면 웹훅 URL 하나로 알린다(선택 기능, [ADR-0018](adr/0018-real-repo-cycle.md) 결정 5). Discord 채널 웹훅을 그대로 넣을 수 있다.

1. **URL 만들기** — Discord 면 채널 설정 → **연동(Integrations) → 웹후크 → 새 웹후크** → **웹후크 URL 복사**. 그 밖의 서비스는 JSON POST 를 받는 URL 이면 된다.
2. **등록** — `/connect?tab=notify`(연결 → 알림)에 붙여 넣고 저장. `https` 만 받는다(같은 Mac 의 수신기는 `http://127.0.0.1…` 도 허용). 저장 뒤 화면에는 `설정됨 · 호스트 <이름>` 만 보인다.
3. **[테스트 보내기]** — 한 번 보내 보고 결과(`보냄`·`HTTP 404`·`시간 초과` 등)를 바로 보여 준다.

언제 오나: 새 사람 요청(`[Runloom] 사람 차례 — 제목: 사유`), 초안 PR 이 열림(`[Runloom] PR 확인 — 제목 <PR 주소>`), 실행 실패로 업무가 끝남(`[Runloom] 실패 — 제목: 사유`). 각 줄 아래 업무 링크(`WORKFLOW_PUBLIC_URL` 기준 — 로그인 필요). 같은 사건은 한 번만 보낸다. Discord 호스트면 `{"content": …}`(2000자), 그 밖은 `content`·`event`·`task_id`·`task_url`·`title`·`pr_url` JSON.

전송 실패(수신 서버 오류·429·연결 실패)는 30초부터 두 배씩 물러나 5번까지 다시 보내고 그 뒤 포기한다 — 화면의 최근 알림 20건에 상태(보냄·대기·실패·건너뜀)와 오류 분류가 보인다. 알림 실패는 업무 상태를 바꾸지 않는다. URL 이 없으면 알림을 쌓지 않는다(나중에 등록해도 지난 사건은 오지 않는다).

URL 은 그 자체가 비밀(아는 사람은 채널에 글을 쓸 수 있다)이라 데이터 볼륨의 비밀 파일(`notify_webhook_url`, 0600)에만 둔다. DB·화면·로그·백업에 없다 — 볼륨을 지우면 다시 등록한다. 바꾸려면 새 URL 을 저장, 끄려면 [삭제].

**받는 사람** — 사람 차례 알림에는 받는 사람이 있다: 업무 담당 멤버 → 없으면 그 업무를 맡긴 사람(에이전트에게 맡기기·다시 맡기기·체인 시작·직접 등록·직접 실행을 누른 멤버) → 없으면 활성 관리자 전원. 공용 웹훅 메시지 첫 줄 끝에 `→ 이름` 이 붙고(여럿이면 한 번에 나열), 홈의 "내 차례" 필터도 같은 기준이다.

**개인 웹훅** — 멤버는 `/me`(내 설정)에 자기 웹훅 URL 을 저장할 수 있다. 저장하면 그 멤버가 받는 사람인 알림이 공용 웹훅과 별도로 개인 웹훅으로도 간다(`→ 이름` 없이). [테스트 보내기]·[삭제] 는 공용과 같다. 개인 웹훅 URL 도 비밀 파일(`notify_webhook_url.<멤버 id>`, 0600)에만 두고 DB·화면·로그·백업에 넣지 않는다. `/me` 에 내가 받는 최근 알림이 보인다.

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
- v9(phase 13) — 진단 데모 내장 종류 `diagnosis`·`code_change` 와 그 규칙을 지운다. 그 종류의 업무·실행·사용자 규칙이 있으면 올리지 않고 멈춘다. 백업(`backup create`)을 먼저 한다.
- v10(phase 14) — 업무 표(`work_items` 등)를 만들고 기존 Task 를 이슈마다 업무 하나로 묶는다(키 `RUN-n`, 첫 관리자·기본 매핑). 백업(`backup create`)을 먼저 한다. 진행 중인 실행이 끝난 뒤 업그레이드를 권장한다 — 러너도 함께 올린다(새 결과 브랜치 `runloom/<키>`).
- v11(phase 15) — 팀 계정 표(`login_sessions`·`member_invites`)와 칸(이메일·비밀번호 해시·맡긴 사람·응답자·알림 받는 사람·러너 소유자)을 더한다. 기존 행은 그대로다. 백업(`backup create`)을 먼저 한다. 재설치 뒤 **옛 로그인 쿠키는 무효라 다시 로그인**해야 하고, 첫 접속에서 `.env` 의 운영자 토큰으로 관리자 계정(이메일·비밀번호)을 만든다(위 "로그인"). 기존 러너는 소유자 없음 = 관리자만 해제할 수 있다 — 그대로 계속 돈다. 기존 알림 설정은 공용 웹훅으로 그대로 간다.
- v12(phase 16) — 업무 화면(담당자 묶음·보드·상세 패널)·직접 작업·PR 신호용 칸과 표를 더한다: `work_items` 직접 작업 칸 셋, `github_sources.pull_cursor`, 감지 PR 표 `work_pull_requests`, 업무 이벤트 종류 확장(`work_item_events` 재생성 — id 그대로). 기존 행과 업무 상태는 그대로다. 백업(`backup create`)을 먼저 하고, 진행 중인 실행이 끝난 뒤 `install.sh` 를 다시 돌린다. **러너 재기동은 필요 없다** — 러너 프로토콜(`connector`·`contracts`)은 바뀌지 않았다(재기동해도 된다). 재설치 뒤 첫 동기화는 저장소마다 최근 PR 100건까지만 읽어 업무 키(`RUN-n`)가 브랜치 이름·제목에 든 PR 을 업무에 붙인다.
- v13(phase 17) — 사람 사이 인계: 에이전트 맡기기 정책(`agents.delegation_policy`, 기존 에이전트는 모두 `run` = 바로 실행), 러너 능력(`connectors.capabilities_json`), 검증만 다시 표시(`executions.verify_only`), 착수 대기(`tasks.start_pending_at` — `tasks` 표는 재생성하지 않는다), 지시 메모(`work_items` 칸 둘), 알림 사건 3개·업무 이벤트 `handoff_note`(`notifications`·`work_item_events` 재생성 — id 그대로). 기존 행과 업무 상태는 그대로다. 순서:
  1. 백업 먼저(`backup create`).
  2. 진행 중인 실행이 끝난 뒤 `install.sh` → 스키마 13.
  3. **러너도 `install-runner.sh` 로 다시 설치한다** — 러너 프로토콜이 바뀌었다(claim 에 `capabilities`, 실행 요청에 `verify_only_commit`). 옛 러너는 v13 서버에 그대로 붙지만 [검증만 다시]를 받지 못해 그 실행은 `연결 프로그램 업데이트 필요 — 검증만 다시 미지원` 으로 기다린다. 새 러너는 v13 이전 서버에 붙지 못한다(422) — 서버를 먼저 올린다. 한 Mac 에 러너를 둘 이상 두었으면 이름마다(`--name b` 등) 다시 실행한다.
  - 러너 등록의 `--env PYTHONPATH=src` 같은 상대 항목은 이제 실행 폴더(worktree·임시 체크아웃) 기준 절대 경로로 풀린다 — `$PWD/src` 로 바꿔 둔 우회는 그대로 둬도 된다.
- v14(phase 18) — Jira 소스: Jira 표 4개(`jira_connections`·`jira_projects`·`jira_issues`·`jira_deliveries`)를 더하고, `work_items`·`field_mappings` 의 `source_type` 에 `jira` 를 넣고 `task_pull_requests.issue_number` 를 비울 수 있게 한다(세 표 재생성 — id·키 번호·행 그대로, `tasks` 는 재생성하지 않는다). `bug_fix` 종류가 있는 워크스페이스에 기본 매핑 `jira → bug_fix` 한 행을 더한다. 기존 행과 업무 상태는 그대로다. 외래키 검사에 걸리는 옛 행이 있으면 올리지 않고 멈춘다(그대로 v13). 순서:
  1. 백업 먼저(`backup create`).
  2. 진행 중인 실행이 끝난 뒤 `install.sh` → 스키마 14.
  3. **러너 재설치는 필요 없다** — 러너 프로토콜(`connector`·`contracts/v1`)은 이 phase 에서 바뀌지 않았다(재설치해도 된다).
  4. Jira 를 쓰려면 위 "Jira 연결". 쓰지 않으면 아무것도 하지 않아도 된다.
- v15(phase 19) — 판단: 표 3개(`triage_criteria` 판단 기준 버전·`triage_logs` 판단 로그·`triage_autostart` 종류별 자동 시작)를 더하고, 워크스페이스마다 내장 종류 `triage` 와 판단 기준 v1 을 넣고, `code.fix` 가 있는 로컬 에이전트에 같은 저장소의 `code.triage` 능력을 더한다(`tasks`·`github_sources` 는 재생성하지 않는다 — 판단 에이전트는 소스 설정 JSON 의 칸). 기존 행과 업무 상태는 그대로다. 사용자 정의 종류 이름이 `triage` 인 워크스페이스가 있거나 외래키 검사에 걸리는 옛 행이 있으면 올리지 않고 멈춘다(그대로 v14). 순서:
  1. 백업 먼저(`backup create`).
  2. 진행 중인 실행이 끝난 뒤 `install.sh` → 스키마 15.
  3. **러너도 `install-runner.sh` 로 다시 설치한다** — 러너 프로토콜이 바뀌었다(claim 의 `supported_kinds` 에 `triage`, 실행 요청 target `TriageTarget`, 결과 `triage_result`). 옛 러너는 v15 서버에 그대로 붙어 수정·검토는 계속 하지만 판단은 받지 못한다 — 업무 패널에 `러너 업데이트 필요 — 판단 미지원`. 한 Mac 에 러너를 둘 이상 두었으면 이름마다(`--name b` 등) 다시 실행한다.
  4. 판단을 쓰려면 연결 화면 저장소 카드에서 **판단 에이전트** 칸을 고른다(맡기기 정책이 '바로 실행'이고 그 저장소의 `code.triage` 능력이 있는 에이전트만). 비워 두면 그 저장소는 판단하지 않는다 — 지금과 같다. Jira 업무는 프로젝트의 연결 저장소 칸을 쓴다. 판단 기준·자동 시작은 연결 화면 "판단" 탭(자동 시작은 사람이 처리한 판단 20건부터 켤 수 있다).
- v16(phase 20) — 모니터링: 설정 변경 기록 표 `config_changes` 하나를 더한다(설정 번호를 올릴 때마다 영역·동작·이름·누가·언제 — 값·본문·비밀은 넣지 않는다). 다른 표는 재생성하지 않고 기존 행·업무 상태는 그대로다. v16 이전 설정 번호는 기록이 없어 모니터링 전후 탭 머리에 `설정 n — 기록 없음` 으로 보인다. 외래키 검사에 걸리는 옛 행이 있으면 올리지 않고 멈춘다(그대로 v15). 순서:
  1. 백업 먼저(`backup create`).
  2. 진행 중인 실행이 끝난 뒤 `install.sh` → 스키마 16.
  3. **러너 재설치는 필요 없다** — 러너 프로토콜(`connector`·`contracts/v1`)은 이 phase 에서 바뀌지 않았다(재설치해도 된다).
  4. `/monitor` 에 탭 셋(`전후`·`판단`·`담당자별`)이 생기고, 연결 화면 "판단" 탭의 종류별 자동 시작 행에 저장된 기준값 이상 판단의 사람 일치·병합 한 줄이 보인다. `/metrics.json` 은 키 `triage`·`assignees`·`config_changes` 를 더하고, `/metrics.csv` 는 열은 그대로 새 행만 더한다.
- 옛 주소는 넘어간다(303) — `/sources`·`/operator`·`/operator/github`·`/operator/notifications`·`/team`·`/agents`·`/kinds` → `/connect?tab=…`, `/metrics` → `/monitor`(`.json`·`.csv` 는 그대로), `/work/RUN-n` → `/tasks?open=RUN-n`. 북마크는 그대로 써도 된다. GitHub App 만들기·콜백·설치 경로와 POST 경로는 바뀌지 않아 GitHub 쪽 App 설정을 고칠 일은 없다. 알림·원본 댓글의 새 링크는 업무 주소(`/tasks?open=RUN-n`)다.
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
| 로그인이 계속 풀림 | 비밀번호 변경·비활성화·재설정 링크 사용 여부, 14일 경과 | 이 셋은 그 멤버의 로그인을 모두 끝낸다. 다시 로그인한다 |
| 다른 기기에서 저장·버튼이 403 `요청 출처를 확인할 수 없습니다` | 브라우저 주소와 `.env` 의 `WORKFLOW_PUBLIC_URL` | 터널 주소를 `WORKFLOW_PUBLIC_URL` 에 적고 `install.sh` 재실행(출처 검사 기준) |

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
