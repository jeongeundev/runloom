# 사람용 CLI `runloom`

터미널에서 내 차례 업무와 인계 리포트를 읽고, 결정 항목에 답하고, 사람 요청에 응답하고, 직접 작업을 시작·그만두는 명령이다. 서버의 `/api/v1` 을 멤버 토큰(`wfm_…`)으로 부르는 얇은 클라이언트다. 결정 기록은 [ADR-0034](adr/0034-cli.md), 명령·출력·exit code 의 기준은 [ARCHITECTURE](ARCHITECTURE.md) "CLI — phase 26".

## 설치

Python 3.13 이상이 필요하다.

```bash
# 저장소를 받아 둔 Mac (서버·러너를 돌리는 곳)
python3 -m pip install -e .

# 다른 Mac 의 팀원 — 저장소를 받지 않고 태그나 커밋을 골라 설치
python3 -m pip install "git+https://github.com/jeongeundev/runloom@<태그 또는 커밋>"
```

- CLI 는 서버와 같은 패키지(`workflow`)에 들어 있어 서버 의존성(FastAPI 등)도 함께 깔린다. 별도 배포 패키지는 없다.
- 설치하면 `runloom` 명령이 생긴다. `python3 -m workflow.cli` 도 같다.
- 서버 쪽은 `GET /api/v1/me` 가 있는 버전이어야 한다 — 셀프호스트는 [SELFHOST](SELFHOST.md) "업그레이드" 의 phase 26 항목.

## 로그인

1. 웹에서 로그인한 뒤 `/me` 의 "API 토큰" 절에서 토큰을 만든다. 원문(`wfm_…`)은 그 화면에 한 번만 보인다.
   - `읽기 전용` — `list`·`next`·`show`·`report` 만. `decide`·`respond`·`start`·`stop` 은 `오류 403 token_read_only: …` 로 거절된다.
   - `내 권한 전부` — 내 역할이 웹에서 할 수 있는 것까지.
2. 토큰을 복사해 표준 입력으로 넘긴다. 명령 인자로는 받지 않는다(셸 기록에 남지 않게).

```bash
pbpaste | runloom login --server http://localhost:8000
# 김개발(member, 토큰 full)로 로그인했습니다.
```

- `login` 은 토큰으로 `GET /api/v1/me` 를 불러 확인된 뒤에만 저장한다. 실패(401 등)면 아무것도 쓰지 않는다.
- 서버 주소는 `https://…` 여야 한다. `http://` 는 `localhost`·`127.0.0.1`·`::1` 만 받는다. 끝 `/` 는 지우고, 경로·쿼리가 붙으면 거절한다(exit 2).
- `runloom logout` 은 로컬 자격 파일만 지운다. 서버의 토큰은 웹 `/me` 의 [폐기] 로 지운다.

### 자격 파일·환경변수

| 항목 | 값 |
|---|---|
| 자격 파일 | `~/.config/runloom/credentials.json` — 내용 `{"server": …, "token": …}`. `XDG_CONFIG_HOME` 은 보지 않는다 |
| 권한 | 디렉터리 0700, 파일 0600. 그룹·다른 사용자 권한이 있으면 읽지 않고 `chmod 600 <경로>` 를 안내한다(exit 2) |
| `RUNLOOM_TOKEN` | 있으면 파일의 토큰보다 먼저 |
| `RUNLOOM_SERVER` | 있으면 파일의 서버 주소보다 먼저. 위 주소 규칙을 똑같이 따른다 |
| `RUNLOOM_HOME` | 자격 파일 디렉터리를 바꾼다(`$RUNLOOM_HOME/credentials.json`) — 서버를 여럿 쓸 때 |

`RUNLOOM_TOKEN`·`RUNLOOM_SERVER` 는 각각 따로 적용된다. 둘 다 있으면 자격 파일을 열지 않는다.

## 명령

모든 명령은 `--json` 을 받는다(`runloom next --json` — 하위 명령 뒤에 붙인다). 명령별 도움말은 `runloom <명령> --help`.

| 명령 | 하는 일 |
|---|---|
| `runloom login --server URL` | 표준 입력의 토큰을 확인하고 저장 |
| `runloom logout` | 자격 파일 지우기 |
| `runloom list [--filter F] [--repository OWNER/NAME] [--closed C]` | 업무 목록 |
| `runloom next` | 내 차례 업무 하나와 그 인계 리포트 |
| `runloom show KEY` | 업무 상세와 최신 인계 리포트 |
| `runloom report REPORT_ID` | 인계 리포트 한 건 |
| `runloom decide REPORT_ID ITEM_ID (--confirm \| --change TEXT)` | 리포트의 결정·확인 항목에 답하기 |
| `runloom respond REQUEST_ID ACTION [--text TEXT] [--agent AGENT_ID]` | 열린 사람 요청에 응답하기 |
| `runloom start KEY` · `runloom stop KEY` | 직접 작업 시작 · 그만두기 |

### `next` — 내 다음 업무

웹 `내 차례` 필터와 같은 후보에서 우선순위(`high` → `normal` → `low`), 그다음 오래 바뀌지 않은 것(`updated_at`)부터 하나를 골라 `show` 와 같은 꼴로 낸다. 내가 직접 작업 중인 업무는 후보가 아니다. 순서는 웹 목록(같은 우선순위에서 최근 변경 먼저)과 다를 수 있다.

```text
$ runloom next
# RUN-12 로그인 만료 처리
- 종류: 버그 수정 (bug_fix)
- 우선순위: high
- 담당: 김개발 (member)
- 상태: 내 차례 — 검토 결과를 확인해 주세요
- 다음 할 일: 검토 결과 확인
- 원본: https://github.com/acme/api/issues/12
- 할 수 있는 행동: assign, set_priority, start_direct

## 열린 사람 요청
없음

## 인계 리포트 · 버그 수정 · 시도 1 · hrp-0a1b2c3d4e5f
보낸 사람 김개발 · 받는 사람 박검토

### 바뀐 것
- [c1] 로그인 만료 시 토큰 갱신을 한 번만 시도하도록 바꿨다
  왜: 갱신 실패가 무한 재시도로 이어졌다는 이슈 본문
  근거: commit:3f2a9c1, source:work

### 결정과 근거
없음

### 정하지 않고 남긴 것
없음

### 받는 사람이 결정·확인할 것
- [t1] 로그아웃 대신 로그인 화면으로 보내는 동작이 맞는지 확인
  왜: PR 리뷰 코멘트에서 의견이 갈렸다
  근거: pr:42
  답: 아직 없음

### 지킬 제약
없음

### 남은 일
없음

내 차례 2건 더 — runloom list --filter my_turn
```

- 내 차례 업무가 없으면 `내 차례 업무가 없습니다.`(exit 0).
- 리포트는 가장 늦게 만들어진 단계의 최신 원본 하나를 펼친다. 그 단계 원본을 기준으로 **내가** 요청한 맞춤 풀이가 있으면 가장 최근 것을 이어서 펼치고(`## 맞춤 풀이 · …`), 기준이 펼친 원본이 아니면 머리 줄 끝에 `· 옛 리포트 기준` 이 붙는다. 나머지 원본은 `## 다른 리포트` 아래 `- <단계> · <report_id> · <작성 중|완성|리포트 없음(사유)>` 한 줄씩 — 펼쳐 보려면 `runloom report <report_id>`.
- 리포트가 하나도 없으면 `## 인계 리포트` 아래 `인계 리포트 없음`. 아직 쓰는 중이면 `리포트 작성 중`.
- `근거 확인 안 됨` 은 근거 id 를 서버가 확인하지 못한 주장, `왜: 기록 없음` 은 이유가 남지 않은 주장이다.

### `show`·`report`·`list`

```text
$ runloom show RUN-12          # next 와 같은 꼴 (다음 업무 건수 줄만 없음)
$ runloom report hrp-0a1b2c3d4e5f
$ runloom list --filter my_turn
RUN-12 · high · 내 차례 · 로그인 만료 처리 · 담당 김개발
RUN-9 · normal · 내 차례 · 결제 실패 알림 · 담당 김개발
```

- `list --filter` 는 `all`(기본)·`my_turn`·`unassigned`·`agent_working`, `--closed` 는 끝난 업무 범위(서버 기본 `recent`, `all`). 값 검사는 서버가 한다(틀리면 `오류 422 …`, exit 1). 목록이 비면 `업무가 없습니다.`
- `show` 는 내 차례가 아닌 업무도 내 권한으로 볼 수 있는 만큼 보인다.

### `decide` — 결정·확인 항목에 답하기

리포트의 `받는 사람이 결정·확인할 것` 절 항목(`[t1]` 의 `t1`)에 답한다. 답할 수 있는 사람(받는 사람·관리자)과 길이(1~500자)는 서버가 검사한다.

```text
$ runloom decide hrp-0a1b2c3d4e5f t1 --confirm
$ runloom decide hrp-0a1b2c3d4e5f t1 --change "로그인 화면이 아니라 홈으로 보낸다"
답을 저장했습니다.

## 인계 리포트 · 버그 수정 · 시도 1 · hrp-0a1b2c3d4e5f
…
  답: 다르게 정함 — 로그인 화면이 아니라 홈으로 보낸다 · 박검토 · 2026-10-22T09:30:00.000000Z
…
```

`--confirm` 과 `--change` 중 하나만 준다(없거나 둘 다면 exit 2).

### `respond` — 사람 요청에 응답하기

`show` 의 `## 열린 사람 요청` 에 나온 `request_id` 와 그 아래 `행동:` 중 하나로 응답한다.

```text
$ runloom respond hrq-1a2b3c4d5e6f resume --text "스테이징에서만 재현됩니다"
응답을 저장했습니다: hrq-1a2b3c4d5e6f resume
$ runloom respond hrq-1a2b3c4d5e6f choose_agent --agent <에이전트 id>
```

- 요청이 이미 닫혔거나 없으면 `열린 사람 요청 <id> 가 없습니다.`(exit 1). 그 사이 요청이 바뀌었으면 서버의 `409 stale_request` 를 그대로 보인다.
- 행동마다 필요한 값(`resume` 의 글, `choose_agent` 의 에이전트 등)은 서버가 검사한다.

### `start`·`stop` — 직접 작업

```text
$ runloom start RUN-12
직접 작업을 시작했습니다.
브랜치: run-12-login-expiry
$ runloom stop RUN-12
직접 작업을 그만두었습니다.
```

`start` 는 서버가 정한 브랜치 이름을 보이기만 한다 — 로컬에서 git 은 실행하지 않는다. 같은 사람이 다시 `start` 하면 처음 브랜치를 그대로 돌려준다. 에이전트가 실행 중인 업무는 `오류 409 execution_conflict: …`.

### `--json`

- `list`·`report`·`decide`·`respond`·`start`·`stop` 은 API 응답 본문 그대로, `login` 은 `GET /api/v1/me` 응답(토큰 없음), `logout` 은 `{"deleted": true|false}`.
- `show`·`next` 는 여러 응답을 합친 객체다 — `detail`(업무 상세), `reports`(그 업무의 리포트 목록), `report`(펼친 원본, 없으면 null), `tailored`(내 맞춤 풀이, 없으면 null). `next` 는 `more_my_turn`(고른 것 밖의 내 차례 건수)을 더한다. 내 차례 업무가 없으면 `{"detail": null, "reports": null, "report": null, "tailored": null, "more_my_turn": 0}`. 예시는 [ARCHITECTURE](ARCHITECTURE.md) "CLI — phase 26" "`--json`".

### exit code

| exit | 언제 | stderr |
|---|---|---|
| 0 | 성공(내 차례 없음·지울 자격 파일 없음 포함), `--help` | — |
| 1 | 서버 오류 응답 | `오류 <status> <code>: <message>` — 401 이면 다음 줄 `runloom login 으로 다시 로그인하세요.` |
| 1 | 서버에 닿지 못함(30초 시간 초과 포함) | `서버에 연결할 수 없습니다(연결 실패\|시간 초과): <server>` |
| 1 | `respond` 의 요청이 열린 목록에 없음 | `열린 사람 요청 <id> 가 없습니다.` |
| 2 | 사용법 오류 | argparse 사용법 + `runloom: error: …` |
| 2 | 설정 오류 — 로그인 안 함, 자격 파일 권한·형식, 서버 주소, `login` 표준 입력이 빔 | 무엇을 하면 되는지 한 줄 |

자동 재시도와 대화형 프롬프트는 없다(`login` 의 표준 입력만 예외). 토큰 원문은 어떤 출력에도 나오지 않는다.

## 에이전트에게 쓰게 하기

터미널에 켜 둔 Claude Code·Codex 에게 그대로 지시한다. 에이전트는 `runloom --help`·`runloom <명령> --help` 와 이 문서로 사용법을 안다 — 도구 전용 스킬 파일은 따로 없다. 먼저 사람이 `runloom login` 을 해 둔다.

```text
runloom next 로 내 다음 업무와 인계 리포트를 읽어. "받는 사람이 결정·확인할 것" 항목은 나에게 물어본 뒤
runloom decide <report_id> <item_id> --confirm 또는 --change "…" 로 답하고, runloom start <업무 키> 로 직접 작업을
시작해서 보여 준 브랜치 이름으로 작업해.
```

- 기계가 읽기 좋은 꼴이 필요하면 `--json` 을 붙이게 한다.
- 에이전트에게는 `읽기 전용` 토큰으로 읽기만 시키고, 답·시작은 사람이 하게 나눌 수도 있다.
- **러너가 띄운 에이전트(등록 에이전트의 자동 실행)는 CLI 를 쓰지 않는다.** 러너는 등록 `--env` 로 `RUNLOOM_TOKEN`·`RUNLOOM_SERVER` 를 넘기는 것을 거절하고, 실행 로그에서 `wfm_…` 를 가린다.

## 하지 않는 것

- 업무 맡기기·우선순위 바꾸기·판단 제안 수락/무시·맞춤 풀이 요청·리포트 다시 만들기 — 웹에서 한다.
- 직접 작업(에이전트 실행 없는 업무)의 인계 리포트 — 아직 없다.
- 서버 토큰 폐기 — 웹 `/me` 에서 한다.
- **Cloudflare Access 뒤에 있는 서버는 아직 지원하지 않는다.** CLI 는 `Authorization: Bearer wfm_…` 헤더만 보내므로 Access 로그인 화면에 막힌다. 같은 Mac 의 `http://localhost:…` 이나 Tailscale 처럼 Access 없이 닿는 https 주소로 쓴다.
