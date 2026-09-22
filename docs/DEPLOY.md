# 배포 런북 — VM 한 대, 대본 에이전트 (공개 데모)

갱신일: 2026-09-22 (phase 7-n8n-gateway docs-sync — 3절 n8n 환경변수 2개, 7b·10 에 스키마 버전 4 와 공개 데모 callback 없음 반영)
상태: [ADR-0008](adr/0008-public-demo-scripted-agents.md)의 공개 데모 구성을 올리는 절차. [ADR-0006](adr/0006-deployment-vm-caddy-mac-connector.md)의 VM + Caddy 는 그대로이고, 운영자 Mac 의 연결 프로그램 대신 **같은 VM 의 systemd 유닛**이 대본 에이전트(`workflow.scripted.*`)를 돌린다. 실제 Codex/Claude·OpenAI 키는 이 VM 에 없다. 설정 파일은 `deploy/` 에 있고 `tests/test_deploy_files.py` 가 AGENTS.md 명령어·settings 환경변수·seed 인자·아래 명령과의 일치를 검사한다. `{domain}`·`{vm-ip}` 는 사용자가 정한 값으로 바꾼다. 이 문서를 만든 세션은 VM 에 접속하지 않았다.

| 위치 | 프로세스 | 파일 |
|---|---|---|
| VM | Caddy(443 → 127.0.0.1:8000) | `deploy/Caddyfile` → `/etc/caddy/Caddyfile` |
| VM | `workflow-central`·`workflow-worker` (env `/etc/workflow/central.env`) | `deploy/systemd/*.service`, `deploy/env/central.env.example` |
| VM | `workflow-diag`(127.0.0.1:8100, 비노출)·`workflow-diag-worker` (env `/etc/workflow/diag.env`, `DIAG_MODEL=fake`) | `deploy/env/diag.env.example` |
| VM | `workflow-connector` (env `/etc/workflow/connector.env`) → PATH 앞의 `deploy/bin/{codex,claude}` → `python -m workflow.scripted.{codex,claude}` | `deploy/systemd/workflow-connector.service`, `deploy/env/connector.env.example`, `deploy/bin/` |
| VM | 연결 프로그램 토큰·상태 `/var/lib/workflow/connector/`, 데모 저장소 `/var/lib/workflow/demo/demo-report-repo` (기준 커밋 `report-base` 고정), worktree 는 옆 `demo-report-repo-worktrees/` | `scripts/scaffold_demo_repo.py` |
| VM | `workflow-backup.timer` 03:00 → `deploy/backup.sh` → `/var/backups/workflow/{날짜}/`, 7일 보관 | |
| Mac | `com.workflow.connector` (launchd) — **공개 데모는 사용 안 함**. 셀프호스트 실사용용(ADR-0006)으로 남긴다 | `deploy/launchd/com.workflow.connector.plist` |

## 1. VM 준비

- Ubuntu 24.04, 1 vCPU / 1 GB 면 충분하다. 인바운드 22·80·443 만 연다. 8000·8100 은 열지 않는다(localhost 전용).
- 도메인의 A 레코드를 VM 공인 IP 로 둔다. Caddy 가 이 도메인으로 인증서를 자동 발급한다.

확인:

```bash
dig +short {domain}      # {vm-ip} 하나가 나와야 한다. 전파 전이면 비어 있다
```

## 2. 설치 스크립트 — 유닛 5개

VM 에서 root 로. 저장소를 임시로 받아 스크립트를 실행하면 `/opt/workflow` 에 다시 clone 하고 venv(`[dev]` — 데모 저장소 검증에 쓰는 pytest 포함)·데이터 디렉터리·systemd 유닛 5개(중앙 2 + 진단 2 + 연결 프로그램)·백업 타이머까지 만든다. 멱등이라 다시 실행해도 된다. 실제 `codex`·`claude` 는 설치하지 않는다 — `deploy/bin/` 래퍼가 그 이름을 맡는다.

```bash
sudo apt-get install -y git
git clone https://github.com/jeongeundev/runloom.git /tmp/workflow-src
sudo bash /tmp/workflow-src/deploy/install-vm.sh
# 다른 저장소·브랜치면: sudo WORKFLOW_REPO_URL=… WORKFLOW_REPO_REF=… bash /tmp/workflow-src/deploy/install-vm.sh
```

스크립트는 서비스를 **start 하지 않는다**. env 파일이 비어 있으면 시작 즉시 실패해 `Restart=always` 가 반복되기 때문이다. 3 단계에서 값을 채운 뒤 4개를 시작하고, 연결 프로그램은 6 단계에서 시작한다.

확인:

```bash
systemctl status workflow-central workflow-worker workflow-diag workflow-diag-worker workflow-connector   # 모두 enabled, inactive
systemctl list-timers workflow-backup.timer                                            # 다음 03:00 예정
ls -l /etc/workflow/            # central.env, diag.env, connector.env 가 root 0600
ls -l /var/lib/workflow/        # central, diag, connector, demo — workflow 소유 0700
ls -l /opt/workflow/deploy/bin/ # codex, claude 실행 권한
sudo -u workflow -H /opt/workflow/venv/bin/python -c "import workflow, workflow.scripted, diagnostic_demo, pytest; print('ok')"
```

## 3. 환경변수 파일 채우기

`/etc/workflow/central.env`·`diag.env`·`connector.env` 는 예시를 복사한 상태다. **이전 배포(실제 모델 시절)의 파일이 남아 있으면** 예시와 키·값을 대조한다 — 특히 `DIAG_MODEL=fake`, `OPENAI_API_KEY` 비움, `DIAG_PRICE_*` 비움(단가가 있으면 대본의 기록된 토큰 수에 곱해져 예산 추정이 쌓인다), `DIAG_GLOBAL_DAILY`·`WORKFLOW_LIMIT_*_DAILY` 를 예시 값(5000·200)으로. 2026-09-21 배포에서 36·10 이 남아 있었다. 키 목록은 `src/workflow/server/settings.py`·`src/diagnostic_demo/settings.py` 의 `ENV_KEYS`, `src/workflow/connector/config.py` 와 같다.

```bash
openssl rand -hex 32   # SESSION_SECRET
openssl rand -hex 32   # OPERATOR_TOKEN
openssl rand -hex 32   # DIAG_API_TOKEN — central.env 와 diag.env 에 같은 값
sudoedit /etc/workflow/central.env
sudoedit /etc/workflow/diag.env
```

| 파일 | 채울 값 |
|---|---|
| `central.env` | 비밀값 2개 `SESSION_SECRET`, `OPERATOR_TOKEN` + `DIAG_API_TOKEN`. 한도는 예시 값 그대로(`WORKFLOW_LIMIT_PER_SESSION_DAILY=200`, `WORKFLOW_LIMIT_GLOBAL_DAILY=5000` — 대본이라 비용 0). n8n 입구·출구([ADR-0010](adr/0010-n8n-inbox-and-callback.md), phase 7 — 심사 이후 배포) 키 2개는 비밀값이 아니다: `WORKFLOW_CALLBACK_HOSTS` 는 **공개 데모에서 비워 둔다**(누구나 세션을 만들 수 있어 외부가 준 주소로 서버가 POST 하게 두지 않는다 — `callback_url` 이 있는 접수는 422 `callback_host_not_allowed`, 없는 접수는 된다), `WORKFLOW_PUBLIC_URL` 은 배포 도메인(예시 파일의 `https://runloom.duckdns.org`, 끝 `/` 없음 — 입구 응답·callback 의 `chain_url`·`task_url` 앞에 붙는다). 셀프호스트에서 n8n 을 붙일 때만 `WORKFLOW_CALLBACK_HOSTS=localhost:5678` 처럼 채운다([docs/n8n/README.md](n8n/README.md)) |
| `diag.env` | `DIAG_API_TOKEN`(위와 같은 값)뿐. `DIAG_MODEL=fake`, `OPENAI_API_KEY` 비움, 단가 비움 — 예시 그대로 둔다. fake 는 키를 읽지 않고 유료 호출을 하지 않는다([ADR-0003](adr/0003-diagnosis-model-openai-gpt41-mini.md) 키·예산 확인 전 호출 금지와 같은 결과) |
| `connector.env` | 고칠 값 없음. `WORKFLOW_CONNECTOR_HOME=/var/lib/workflow/connector`(토큰·상태 DB 위치), `WORKFLOW_SCRIPT_PACE_SECONDS=25`(대본 실행 시간 — 심사자가 `실행 중` 을 보게) |

```bash
sudo chmod 600 /etc/workflow/central.env /etc/workflow/diag.env /etc/workflow/connector.env
sudo systemctl start workflow-diag workflow-diag-worker workflow-central workflow-worker
```

확인:

```bash
systemctl status workflow-central workflow-worker workflow-diag workflow-diag-worker   # 모두 active (running)
curl -sI http://127.0.0.1:8000/ | head -1                                              # HTTP/1.1 200
journalctl -u workflow-diag-worker -n 3        # "진단 워커 시작: model=fake …" — openai 가 아니어야 한다
sudo ss -ltnp | grep -E ':(8000|8100) '        # 둘 다 127.0.0.1 에만 묶여 있다
```

## 4. Caddy

`/etc/caddy/Caddyfile` 의 `{$WORKFLOW_DOMAIN}` 을 실제 도메인으로 바꾼다. 진단 API 는 이 파일에 없어야 한다(8100 비노출).

```bash
sudo sed -i 's/{\$WORKFLOW_DOMAIN}/{domain}/' /etc/caddy/Caddyfile
sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl reload caddy
```

확인:

```bash
curl -s -o /dev/null -w '%{http_code}\n' https://{domain}/   # 200 (HEAD 는 405). 첫 요청은 인증서 발급으로 몇 초 걸릴 수 있다
curl -I http://{domain}           # 308 → https 리다이렉트
curl -m 5 http://{vm-ip}:8100/capabilities || echo "외부에서 닫힘 (정상)"
```

## 5. 데모 저장소와 seed — VM 에서

seed 는 두 로컬 Agent 의 기준 커밋(`--base-commit`)을 요구하고, 그 커밋은 데모 저장소를 만들 때 정해진다(실행마다 SHA 가 다르다). 둘 다 VM 에서 `workflow` 사용자로 한다.

```bash
cd /opt/workflow
sudo -u workflow -H venv/bin/python scripts/scaffold_demo_repo.py /var/lib/workflow/demo/demo-report-repo
#   마지막 줄 base_commit={sha} 를 적어 둔다 (= git -C /var/lib/workflow/demo/demo-report-repo rev-parse HEAD)
sudo -u workflow -H venv/bin/python scripts/seed_demo.py \
  --db /var/lib/workflow/central/db.sqlite \
  --artifacts /var/lib/workflow/central/artifacts \
  --scripted --base-commit {sha} --print-code
# agents=agent-claude-mac,agent-codex-mac,agent-ops-demo
# connect_code=…            ← 1회용, 10분. 6 단계 직전에 실행한다. 만료되면 seed 를 다시 돌리거나 /operator 에서 발급
```

`--scripted` 는 재실행마다 붙인다 — 없으면 세 Agent 의 `시연용 · 대본 재생` 표시가 내려간다. seed 는 멱등이며 연결 프로그램이 보고한 연결 정보는 지우지 않는다.

확인: 브라우저에서 `https://{domain}/agents/register` 에 카탈로그 3개 — `agent-ops-demo` 연결됨, `agent-codex-mac`·`agent-claude-mac` 연결 끊김(아직 연결 프로그램 미연결), 셋 다 `시연용 · 대본 재생`.

## 6. VM — 연결 프로그램

`connect`·`register` 는 유닛과 같은 사용자·같은 `WORKFLOW_CONNECTOR_HOME` 으로 한 번씩 실행한다. 토큰은 `/var/lib/workflow/connector/token.json` 에 0600 으로 저장되며 env 파일에 넣지 않는다. `register` 는 tool 마다 하나(codex·claude), 같은 저장소·같은 검증 프로필이다.

```bash
cd /opt/workflow
sudo -u workflow -H env WORKFLOW_CONNECTOR_HOME=/var/lib/workflow/connector \
  venv/bin/python -m workflow.connector connect --code {connect_code} --server https://{domain}
#   연결됨: connector_id=… (토큰은 /var/lib/workflow/connector/token.json 에 0600 으로 저장)
sudo -u workflow -H env WORKFLOW_CONNECTOR_HOME=/var/lib/workflow/connector \
  venv/bin/python -m workflow.connector register --id local-demo-report --tool codex \
  --repo /var/lib/workflow/demo/demo-report-repo --repository-id demo-report-repo \
  --verify "vp-pytest=python3 -m pytest -q" --verify "vp-report=python3 -m daily_report {response}"
#   등록됨: local-demo-report → agent agent-codex-mac (base_commit …, 검증 프로필 vp-pytest, vp-report)
sudo -u workflow -H env WORKFLOW_CONNECTOR_HOME=/var/lib/workflow/connector \
  venv/bin/python -m workflow.connector register --id local-demo-report-claude --tool claude \
  --repo /var/lib/workflow/demo/demo-report-repo --repository-id demo-report-repo \
  --verify "vp-pytest=python3 -m pytest -q" --verify "vp-report=python3 -m daily_report {response}"
#   등록됨: local-demo-report-claude → agent agent-claude-mac (…)
sudo systemctl start workflow-connector
```

`register` 의 `--id`·`--tool`·`--repository-id`·`--verify` 는 `scripts/seed_demo.py`(`LOCAL_REGISTRATION_IDS`)·`scripts/local_stack.py`(`VERIFY_PROFILES`)의 값과 같아야 한다(e2e 와 동일). `base_commit` 은 저장소 HEAD 에서 읽으므로 5 단계의 `{sha}` 와 일치한다. 검증 프로필의 `python3` 는 유닛 PATH 의 venv(`/opt/workflow/venv/bin`)에서 찾는다 — 그래서 2 단계가 `[dev]` 를 설치했다.

확인:

```bash
systemctl status workflow-connector                                   # active (running)
journalctl -u workflow-connector -n 3                                 # "연결 프로그램 시작: connector_id=… adapter=codex,claude"
sudo -u workflow -H ls -l /var/lib/workflow/connector/                # token.json 0600, state.sqlite
sudo -u workflow -H env PATH=/opt/workflow/deploy/bin:$PATH which codex claude   # /opt/workflow/deploy/bin/{codex,claude}
```

`https://{domain}/agents/register` 에서 카탈로그 3개 모두 **연결됨**(heartbeat 30초 안에 바뀐다). 중지·재시작은 `systemctl stop/restart workflow-connector`. 토큰을 취소하면(운영자 화면) 다음 요청부터 401 이며 `connect` 부터 다시 한 뒤 재시작한다.

## 7. 운영자 예시 실행 — 등록 → 가져오기 → 시작 → 승인

배포 환경에서 심사자 흐름이 끝까지 도는지 한 번 확인한다. 대본 에이전트라 비용은 0 이다.

1. `https://{domain}/operator` 에 `OPERATOR_TOKEN` 을 입력한다 (운영자 화면 확인용 — 아래 흐름 자체는 심사자 세션과 같다).
2. `/agents/register` 에서 카탈로그 3개를 `등록` 한다 (순서: 운영 진단 데모 → 개인 Codex → Claude Code. 먼저 등록한 로컬 Agent 가 코드 수정의 기본 담당이 된다).
3. 홈 → `업무 가져오기` → GitHub 탭, 4개가 체크된 채 `가져와서 워크플로우 구성` → 워크플로우 화면. #41 진단(직접 실행·자동 완료) → #42 코드 수정(선행 완료 시 자동·검토 후 완료, 담당 `개인 Codex · 시연용 · 대본 재생`) → 사람 단계. #43·#44 는 "워크플로우에 넣지 않은 이슈".
4. `워크플로우 시작` → #41 `실행 요청됨 → 완료 · 판정 근거: n/n` (fake 대본 9턴 × `DIAG_FAKE_TURN_SECONDS` 2.5초 ≈ 23초).
5. #41 완료 후 워커 스캔(3초) 안에 #42 가 별도 조작 없이 착수한다. `대기 → 실행 요청됨 → 실행 중 → 확인 필요 · 검토 대기` (대본 25초 + worktree·pytest·보고서 수 초). 산출물에 diff·테스트 전/후·보고서·`Codex JSONL`(thread_id `scripted-codex`)이 있고 결과 카드 끝에 `대본 재생 (실제 모델 호출 없음)`.
6. `검토하기` → 산출물을 열어 보고 `완료 승인` → 사람 단계 `완료 · 병합: 운영자 확인 대기`, 홈 워크플로우 구역 `2/2 완료`.

이 워크플로우는 운영자 세션의 업무로 남는다. 다른 세션(심사자)에게 보여 주는 "예시 실행 공개" 화면은 [ARCHITECTURE](ARCHITECTURE.md) 배포 절의 제안이며 구현되지 않았다.

확인:

```bash
sudo -u workflow -H git -C /var/lib/workflow/demo/demo-report-repo branch      # main 과 task/{#42 task_id}. main 은 report-base 그대로
sudo -u workflow -H git -C /var/lib/workflow/demo/demo-report-repo worktree list   # main 하나 — 결과 업로드 뒤 worktree 는 정리된다
sudo ls /var/lib/workflow/demo/demo-report-repo-worktrees/                     # 비어 있음 (결과 업로드 뒤 worktree 는 지운다 — 디렉터리 자체는 남는다)
```

## 7b. 코드 갱신 — 이후 배포

GitHub `main` 에 푸시한 뒤 VM 에서 한 줄. env·데이터·Caddy 는 건드리지 않고 코드만 받아 서비스 5개를 재시작한다 (몇 초 502). 연결 프로그램도 같은 저장소를 쓰므로 함께 재시작된다.

```bash
sudo bash /opt/workflow/deploy/update-vm.sh        # "갱신 끝: {sha}, 중앙 웹 200" 이 나와야 한다
```

**스키마 버전이 바뀐 배포**(`src/workflow/adapters/db.py` `SCHEMA_VERSION` 변경 — 마이그레이션이 없어 기존 DB 로는 시작하지 못한다)만 명시적 플래그로 DB 를 초기화한다. 서비스를 멈추고 `central/db.sqlite`·`central/artifacts`·`diag/*` 를 `/var/backups/workflow/reset-{시각}/` 로 **옮긴** 뒤(삭제 아님) 빈 상태로 재시작한다. 출력에 `DB 초기화됨(백업: …)` 이 남는다. 플래그 없이는 데이터를 절대 건드리지 않는다.

```bash
sudo WORKFLOW_RESET_DB=1 bash /opt/workflow/deploy/update-vm.sh
```

초기화 뒤에는 카탈로그·연결 프로그램 등록이 중앙 DB 에서 사라졌으므로 5 단계의 seed(`--base-commit` 은 기존 저장소 HEAD)와 6 단계의 `connect`·`register` ×2 를 다시 하고 `sudo systemctl restart workflow-connector` 한다. 데모 저장소와 연결 프로그램 상태 디렉터리는 그대로 둔다. 심사자 세션 쿠키는 남아 있지만 그 세션의 업무는 사라진다.

**phase 6-typed-handoff(브랜치 `feat-6-typed-handoff`, [ADR-0009](adr/0009-registered-kinds-and-succession-rules.md))는 `SCHEMA_VERSION` 을 2 → 3 으로 올렸다**(`kinds`·`succession_rules` 테이블, `tasks.kind` FK). VM 의 DB 는 버전 2 이므로 이 phase 를 배포할 때는 반드시 `WORKFLOW_RESET_DB=1` 이 필요하고(백업 절차는 위와 같다), 심사 기간(2026-09-21 ~ 10-05)에는 공개 데모를 동결하므로 **심사 이후**에 배포한다. 그 전까지 `main` 에 병합·푸시하더라도 VM 에서 `update-vm.sh` 를 돌리지 않는다 — 플래그 없이 돌리면 버전 불일치로 중앙·워커가 시작하지 못한다.

**phase 7-n8n-gateway(브랜치 `feat-7-n8n-gateway`, [ADR-0010](adr/0010-n8n-inbox-and-callback.md))는 `SCHEMA_VERSION` 을 3 → 4 로 올렸다**(`source_tokens` 테이블, `chains` 에 `items_json`·`callback_*` 열, `source` CHECK 에 `n8n`). 마이그레이션은 없으므로 phase 6·7 을 함께 배포하는 심사 이후 첫 갱신 한 번에 `WORKFLOW_RESET_DB=1`(스키마 2 → 4)이 필요하다. 같은 갱신에서 `central.env` 에 3절의 `WORKFLOW_CALLBACK_HOSTS`(비움)·`WORKFLOW_PUBLIC_URL` 두 줄을 추가한다 — 없으면 기본값(빈 목록·빈 공개 주소)으로 뜨지만 `tests/test_deploy_files.py` 가 예시 파일과 `ENV_KEYS` 의 일치를 검사하므로 예시 파일과 같게 맞춘다.

## 8. 백업 타이머

```bash
systemctl list-timers workflow-backup.timer        # NEXT 가 다음 03:00
sudo systemctl start workflow-backup.service       # 한 번 수동 실행
ls -l /var/backups/workflow/$(date +%F)/           # central.sqlite, diag.sqlite, connector-state.sqlite, central-artifacts.tar.gz, diag-artifacts.tar.gz
```

복원은 서비스를 멈춘 뒤 `.sqlite` 를 `/var/lib/workflow/{central,diag}/db.sqlite`·`/var/lib/workflow/connector/state.sqlite` 로, tar 를 같은 디렉터리에 풀고 다시 시작한다. 진단 fixture 는 저장소에 있으므로 백업 대상이 아니다. 연결 토큰 파일은 백업하지 않는다 — 없으면 6 단계의 `connect` 부터 다시 한다.

## 9. 심사 기간 점검 목록 — 매일 (2026-09-21 ~ 10-05)

```bash
curl -s -o /dev/null -w '%{http_code}\n' https://{domain}/          # 200 (HEAD 는 405)
systemctl is-active workflow-central workflow-worker workflow-diag workflow-diag-worker workflow-connector   # 5줄 active
curl -s https://{domain}/agents/register | grep -o 'data-status="연결됨"' | wc -l   # 3 — 카탈로그 3개 모두 연결됨
journalctl -u workflow-worker --since -1d | grep -c ERROR            # 0 이 정상. 늘면 journalctl -u workflow-worker --since -1d 로 본다
journalctl -u workflow-connector --since -1d | grep -c ERROR         # 0 이 정상 (401 이면 토큰 취소 — 6 단계 connect 부터)
df -h /var/lib/workflow /var/backups                                 # 여유 공간
sudo du -sh /var/lib/workflow/demo                                   # 결과 업로드 뒤 worktree·인계 디렉터리를 지우므로 커지지 않아야 한다 (브랜치만 늘어난다)
curl -sS -H "Authorization: Bearer $(sudo grep '^DIAG_API_TOKEN=' /etc/workflow/diag.env | cut -d= -f2-)" \
  http://127.0.0.1:8100/budget                                       # runs_today 만 는다. estimated_usd 는 DIAG_PRICE_* 가 비어 있으면 늘지 않는다 (fake 도 기록된 토큰 수를 보고한다)
```

`runs_today` 가 `DIAG_GLOBAL_DAILY=5000` 에 닿으면 새 진단은 `429 daily_limit_reached` 다 — 대본이라 비용은 없으니 필요하면 `diag.env`·`central.env` 의 값을 같이 올리고 `systemctl restart workflow-diag workflow-central workflow-worker`.

## 10. 알려진 한계

- 대본이라 진단 내용·수정 diff 가 매번 같다. 실제 모델의 오류·보류 사례는 공개 데모에 없고 [DIAG_EVAL](DIAG_EVAL.md)·[VERIFICATION_LOG](VERIFICATION_LOG.md) 2026-09-20 기록으로 대신한다([ADR-0008](adr/0008-public-demo-scripted-agents.md) 트레이드오프).
- 실제 모델·실제 Codex 로 바꾸려면 env 3개를 고치고 Mac 을 연결한다: `diag.env` 의 `DIAG_MODEL=openai`·`OPENAI_API_KEY`·`DIAG_PRICE_*`·`DIAG_GLOBAL_DAILY=36`, `central.env` 의 한도 `10/36`, `connector.env` 는 쓰지 않고 `systemctl disable --now workflow-connector` 한 뒤 `seed_demo.py` 를 `--scripted` 없이 다시 돌리고 운영자 Mac 에서 `deploy/launchd/com.workflow.connector.plist` 로 연결 프로그램을 띄운다([ADR-0006](adr/0006-deployment-vm-caddy-mac-connector.md), 총액 US$30 상한). 키·예산 확인 전에는 하지 않는다(ADR-0003).
- DB 초기화는 `WORKFLOW_RESET_DB=1` 로만 한다(7b). 그 외 경로로 데이터를 지우는 스크립트는 없다.
- 연결 프로그램이 멈추면 코드 수정 노드는 `대기 · 연결 끊김, 마지막 확인 {시각}` 으로 남고 시작했다고 표시하지 않는다. `systemctl restart workflow-connector` 로 재접속하면 claim 한 실행부터 이어간다. 진단(A)은 연결 프로그램과 무관하게 동작한다.
- 연결 코드는 10분, 세션 쿠키는 14일이다. 결과 업로드 뒤 worktree·인계 디렉터리는 지우고 `task/{task_id}` 브랜치만 남긴다(`run --keep-workdirs` 로 보존).
- 데모 저장소의 결과 커밋은 `task/{task_id}` 브랜치에만 남는다. 기준 브랜치 병합은 운영자 확인 대기로 표시될 뿐 자동으로 하지 않는다.
- 업무 종류·후속 규칙([ADR-0009](adr/0009-registered-kinds-and-succession-rules.md), 심사 이후 배포): API 에이전트(진단 API)는 `diagnosis` 종류만 받는다 — 사용자 정의 종류는 로컬 도구(Codex·Claude)가 읽기 전용으로만 수행한다. 완료 시 새 업무를 생성하는 규칙은 없다 — 미리 등록된 업무 사이를 규칙으로 이을 뿐이다. 사람이 선행 업무를 종료해도 이미 시작한 후속은 계속된다(멈추려면 후속 업무를 따로 종료). 사용자 정의 종류는 자동 완료 검증기가 없어 항상 사람 검토다.
- n8n 입구·출구([ADR-0010](adr/0010-n8n-inbox-and-callback.md), 심사 이후 배포): **공개 데모는 callback 이 없다** — `WORKFLOW_CALLBACK_HOSTS` 를 비워 두므로 `callback_url` 이 있는 접수는 422 이고, `callback_url` 없이 접수한 체인은 화면에서만 진행을 본다. 입구 토큰(`/sources`)은 세션당 활성 5개까지이며 취소는 그 세션의 화면에서만 할 수 있다 — 세션 쿠키(14일)가 만료되면 그 토큰을 취소할 화면이 없어진다(토큰 자체는 DB 에 남아 계속 통한다. 세션 데이터를 지우는 절차는 `WORKFLOW_RESET_DB=1` 뿐). 실제 n8n 과의 실연동은 phase 7 step 10 에서 로컬(Docker n8n + `scripts/local_stack.py --callback-hosts localhost:5678`)로만 한다 — VM 에 n8n 을 두지 않는다.
