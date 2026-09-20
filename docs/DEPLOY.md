# 배포 런북 — VM + 운영자 Mac

갱신일: 2026-09-20
상태: [ADR-0006](adr/0006-deployment-vm-caddy-mac-connector.md)의 구성을 올리는 절차. 설정 파일은 `deploy/` 에 있고 `tests/test_deploy_files.py` 가 AGENTS.md 명령어·settings 환경변수·seed 인자와의 일치를 검사한다. VM 제공자·도메인은 아직 지정하지 않았으므로 아래 `{domain}`·`{vm-ip}` 는 사용자가 정한 값으로 바꾼다. 이 문서를 만든 세션은 VM 에 접속하거나 도메인을 등록하지 않았다.

| 위치 | 프로세스 | 파일 |
|---|---|---|
| VM | Caddy(443 → 127.0.0.1:8000) | `deploy/Caddyfile` → `/etc/caddy/Caddyfile` |
| VM | `workflow-central`·`workflow-worker` (env `/etc/workflow/central.env`) | `deploy/systemd/*.service`, `deploy/env/central.env.example` |
| VM | `workflow-diag`(127.0.0.1:8100, 비노출)·`workflow-diag-worker` (env `/etc/workflow/diag.env`) | `deploy/env/diag.env.example` |
| VM | `workflow-backup.timer` 03:00 → `deploy/backup.sh` → `/var/backups/workflow/{날짜}/`, 7일 보관 | |
| Mac | `com.workflow.connector` (launchd KeepAlive) → `python3 -m workflow.connector run` → Codex | `deploy/launchd/com.workflow.connector.plist` |
| Mac | 데모 저장소 `~/demo-report-repo` (기준 커밋 `report-base` 고정), worktree 는 `~/demo-report-repo-worktrees/` | `scripts/scaffold_demo_repo.py` |

## 1. VM 준비

- Ubuntu 24.04, 1 vCPU / 1 GB 면 충분하다. 인바운드 22·80·443 만 연다. 8000·8100 은 열지 않는다(localhost 전용).
- 도메인의 A 레코드를 VM 공인 IP 로 둔다. Caddy 가 이 도메인으로 인증서를 자동 발급한다.

확인:

```bash
dig +short {domain}      # {vm-ip} 하나가 나와야 한다. 전파 전이면 비어 있다
```

## 2. 설치 스크립트

VM 에서 root 로. 저장소를 임시로 받아 스크립트를 실행하면 `/opt/workflow` 에 다시 clone 하고 venv·systemd 유닛까지 만든다. 멱등이라 다시 실행해도 된다.

```bash
sudo apt-get install -y git
git clone https://github.com/jeongeundev/runloom.git /tmp/workflow-src
sudo bash /tmp/workflow-src/deploy/install-vm.sh
# 다른 저장소·브랜치면: sudo WORKFLOW_REPO_URL=… WORKFLOW_REPO_REF=… bash /tmp/workflow-src/deploy/install-vm.sh
```

스크립트는 서비스를 **start 하지 않는다**. env 파일이 비어 있으면 시작 즉시 실패해 `Restart=always` 가 반복되기 때문이다. 3 단계에서 값을 채운 뒤 시작한다.

확인:

```bash
systemctl status workflow-central workflow-worker workflow-diag workflow-diag-worker   # 모두 enabled, inactive
systemctl list-timers workflow-backup.timer                                            # 다음 03:00 예정
ls -l /etc/workflow/            # central.env, diag.env 가 root 0600
sudo -u workflow -H /opt/workflow/venv/bin/python -c "import workflow, diagnostic_demo; print('ok')"
```

## 3. 환경변수 파일 채우기

`/etc/workflow/central.env` 와 `/etc/workflow/diag.env` 는 예시를 복사한 상태다. 키 목록은 `src/workflow/server/settings.py`·`src/diagnostic_demo/settings.py` 의 `ENV_KEYS` 와 같다.

```bash
openssl rand -hex 32   # SESSION_SECRET
openssl rand -hex 32   # OPERATOR_TOKEN
openssl rand -hex 32   # DIAG_API_TOKEN — central.env 와 diag.env 에 같은 값
sudoedit /etc/workflow/central.env
sudoedit /etc/workflow/diag.env
```

| 파일 | 채울 값 |
|---|---|
| `central.env` | `SESSION_SECRET`, `OPERATOR_TOKEN`, `DIAG_API_TOKEN`. 나머지는 예시 값 그대로 |
| `diag.env` | `DIAG_API_TOKEN`(위와 같은 값), `OPENAI_API_KEY`, `DIAG_PRICE_INPUT_PER_M`·`DIAG_PRICE_OUTPUT_PER_M`(백만 토큰당 USD — gpt-4.1 은 입력 2.00 / 출력 8.00 — 공식 가격 페이지에서 다시 확인해 적는다. 비우면 비용을 추정하지 못해 US$30 총액 상한이 동작하지 않는다) |

모델은 [ADR-0003](adr/0003-diagnosis-model-openai-gpt41-mini.md) 확정대로 `gpt-4.1-2025-04-14`, 하루 상한 `DIAG_GLOBAL_DAILY=36` (예시 파일 값 그대로). 키가 없으면 진단 워커는 exit 2 로 멈추고 유료 호출을 하지 않는다.

```bash
sudo chmod 600 /etc/workflow/central.env /etc/workflow/diag.env
sudo systemctl start workflow-diag workflow-diag-worker workflow-central workflow-worker
```

확인:

```bash
systemctl status workflow-central workflow-worker workflow-diag workflow-diag-worker   # 모두 active (running)
curl -sI http://127.0.0.1:8000/ | head -1                                              # HTTP/1.1 200
journalctl -u workflow-diag-worker -n 3        # "진단 워커 시작: model=openai model_id=gpt-4.1-2025-…"
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
curl -I https://{domain}          # HTTP/2 200. 첫 요청은 인증서 발급으로 몇 초 걸릴 수 있다
curl -I http://{domain}           # 308 → https 리다이렉트
curl -m 5 http://{vm-ip}:8100/capabilities || echo "외부에서 닫힘 (정상)"
```

## 5. 데모 저장소와 seed

seed 는 `agent-codex-mac` 의 기준 커밋(`--base-commit`)을 요구하고, 그 커밋은 Mac 에서 데모 저장소를 만들 때 정해진다(실행마다 SHA 가 다르다). 그래서 Mac 을 먼저 준비한다.

Mac:

```bash
git clone https://github.com/jeongeundev/runloom.git ~/workflow
cd ~/workflow && python3 -m pip install -e ".[dev]"
python3 scripts/scaffold_demo_repo.py ~/demo-report-repo     # 마지막 줄 base_commit={sha} 를 적어 둔다
codex --version && codex login status                        # Step 15 와 같은 ChatGPT 로그인 재사용
```

VM (DB 소유자 `workflow` 로 실행. 멱등이며 다시 돌리면 새 연결 코드만 발급한다):

```bash
cd /opt/workflow
sudo -u workflow -H venv/bin/python scripts/seed_demo.py \
  --db /var/lib/workflow/central/db.sqlite \
  --artifacts /var/lib/workflow/central/artifacts \
  --base-commit {sha} --print-code
# agents=agent-codex-mac,agent-ops-demo
# connect_code=…            ← 1회용, 10분. 6 단계 직전에 실행한다. 만료되면 seed 를 다시 돌리거나 /operator 에서 발급
```

확인: 브라우저에서 `https://{domain}/agents` 에 2개 — `agent-ops-demo` 연결됨, `agent-codex-mac` 연결 끊김(아직 Mac 미연결).

## 6. Mac — 연결 프로그램

```bash
cd ~/workflow
python3 -m workflow.connector connect --code {connect_code} --server https://{domain}
#   연결됨: connector_id=… (토큰은 ~/Library/Application Support/workflow-connector/token.json 에 0600 으로 저장)
python3 -m workflow.connector register --id local-demo-report --repo ~/demo-report-repo --repository-id demo-report-repo \
  --verify "vp-pytest=python3 -m pytest -q" --verify "vp-report=python3 -m daily_report {response}"
#   등록됨: local-demo-report → agent agent-codex-mac (base_commit …, 검증 프로필 vp-pytest, vp-report)
```

`register` 의 `--id`·`--repository-id`·`--verify` 는 `scripts/seed_demo.py` 와 `scripts/local_stack.py` 의 값과 같아야 한다(Step 14 e2e 와 동일). `base_commit` 은 저장소 HEAD 에서 읽으므로 5 단계의 `{sha}` 와 일치한다.

launchd 로 상시 실행. plist 는 `~` 를 풀지 않으므로 홈 경로를 치환해 넣는다. `WorkingDirectory` 는 `~/workflow`, `PATH` 는 `which codex`·`which python3` 가 있는 경로여야 한다(기본값은 pyenv shim + Homebrew).

```bash
mkdir -p ~/Library/Logs/workflow-connector ~/Library/LaunchAgents
sed "s#/Users/USERNAME#$HOME#g" deploy/launchd/com.workflow.connector.plist > ~/Library/LaunchAgents/com.workflow.connector.plist
launchctl load ~/Library/LaunchAgents/com.workflow.connector.plist
```

확인:

```bash
launchctl list | grep workflow                                      # PID 와 상태 0
tail -3 ~/Library/Application\ Support/workflow-connector/logs/connector.log   # "연결 프로그램 시작: connector_id=… adapter=codex"
```

`https://{domain}/agents` 에서 `agent-codex-mac` 이 **연결됨**(heartbeat 30초 안에 바뀐다). 중지·재시작은 `launchctl unload` / `load`. 토큰을 취소하면(운영자 화면) 다음 요청부터 401 이며 `connect` 부터 다시 한다.

## 7. 운영자 예시 실행 — A → B 한 번 끝까지

Mac 오프라인 대비로 결과가 남아 있도록, 그리고 실제 OpenAI·Codex 경로가 배포 환경에서 도는지 확인하기 위해 한 번 돌린다. 진단 1회는 수 센트가 든다.

1. `https://{domain}/operator` 에 `OPERATOR_TOKEN` 을 입력한다.
2. 홈 → `시연 업무 만들기` → 폼의 `후속 업무 B(코드 수정)도 함께 등록` 이 체크된 채 `등록` 하면 A(진단, 자동 완료)와 B(코드 수정, 자동 실행, 검토 후 완료, 선행 A)가 함께 만들어진다. A 에서 `실행`. `실행 요청됨 → 실행 중 → 완료 · 판정 근거: n/m` 을 본다.
3. A 완료 후 워커 스캔(3초) 안에 B 가 별도 조작 없이 자동 착수한다. Codex 실행은 2~3분(Step 15 기준). (체크를 풀고 등록했으면 A 상세의 `후속 업무 B 등록` 으로 나중에 만들 수 있다.)
4. B 가 `확인 필요 · 검토 대기` 가 되면 산출물(diff·테스트 전·후·보고서)을 열어 보고 `완료 승인` → `완료 · 검토 승인 · 병합: 운영자 확인 대기`.

이 A·B 는 운영자 세션의 업무로 남는다. 다른 세션(심사자)에게 보여 주는 "예시 실행 공개" 화면은 [ARCHITECTURE](ARCHITECTURE.md) 배포 절의 제안이며 구현되지 않았다.

확인: Mac 의 `~/demo-report-repo` 는 `main` 이 `report-base` 그대로이고 결과는 `task/{B task_id}` 브랜치에만 있다(`git -C ~/demo-report-repo branch`).

## 7b. 코드 갱신 — 이후 배포

GitHub `main` 에 푸시한 뒤 VM 에서 한 줄. env·데이터·Caddy 는 건드리지 않고 코드만 받아 서비스 4개를 재시작한다 (몇 초 502).

```bash
sudo bash /opt/workflow/deploy/update-vm.sh        # "갱신 끝: {sha}, 중앙 웹 200" 이 나와야 한다
```

Mac 연결 프로그램도 같은 저장소를 쓰므로 `git pull` 뒤 `launchctl unload` / `load` 로 재시작한다.

## 8. 백업 타이머

```bash
systemctl list-timers workflow-backup.timer        # NEXT 가 다음 03:00
sudo systemctl start workflow-backup.service       # 한 번 수동 실행
ls -l /var/backups/workflow/$(date +%F)/           # central.sqlite, diag.sqlite, central-artifacts.tar.gz, diag-artifacts.tar.gz
```

복원은 서비스를 멈춘 뒤 `.sqlite` 를 `/var/lib/workflow/{central,diag}/db.sqlite` 로, tar 를 같은 디렉터리에 풀고 다시 시작한다. 진단 fixture 는 저장소에 있으므로 백업 대상이 아니다.

## 9. 심사 기간 점검 목록 — 매일 (2026-09-21 ~ 10-05)

```bash
# VM
curl -I https://{domain} | head -1                                  # 200
systemctl is-active workflow-central workflow-worker workflow-diag workflow-diag-worker   # 4줄 active
curl -sS -H "Authorization: Bearer $(sudo grep '^DIAG_API_TOKEN=' /etc/workflow/diag.env | cut -d= -f2-)" \
  http://127.0.0.1:8100/budget                                       # estimated_usd / limit_usd 30 / runs_today
journalctl -u workflow-worker --since -1d | grep -c ERROR            # 0 이 정상. 늘면 journalctl -u workflow-worker --since -1d 로 본다
df -h /var/lib/workflow /var/backups                                 # 여유 공간
# Mac
launchctl list | grep workflow                                       # 살아 있는지
tail -1 ~/Library/Application\ Support/workflow-connector/logs/connector.log
```

`estimated_usd` 가 27 (90%) 에 닿으면 새 진단은 `429 budget_exhausted` 다. 남은 심사 일정에 비해 소진이 빠르면 `central.env` 의 `WORKFLOW_LIMIT_PER_SESSION_DAILY`·`WORKFLOW_LIMIT_GLOBAL_DAILY` 를 낮추고 `systemctl restart workflow-central workflow-worker`.

## 10. 알려진 한계

- Mac 이 꺼지거나 오프라인이면 B 는 `대기 · 연결 끊김, 마지막 확인 {시각}` 으로 남고 시작했다고 표시하지 않는다. 재접속하면 claim 한 실행부터 이어간다. 진단(A)은 Mac 과 무관하게 동작한다.
- Codex 사용량 한도에 걸리면 B 는 `실패` 다. 다른 엔진으로 자동 대체하지 않는다(ARCHITECTURE 상태·재접속·완료 절).
- 진단 총액 상한은 US$30 이며 90% 에서 새 접수를 멈춘다. 단가(`DIAG_PRICE_*`)를 비워 두면 이 상한이 동작하지 않는다.
- `OPENAI_API_KEY` 가 없으면 진단 워커가 멈춰 A 는 접수(`accepted`)에서 2분 뒤 `확인 필요 · 시작 여부 불명` 이 된다. 재실행하지 않는다.
- 연결 코드는 10분, 세션 쿠키는 14일이다. 심사 기간 중 데이터 리셋은 없고 worktree 는 자동 삭제하지 않는다.
- 데모 저장소의 결과 커밋은 `task/{task_id}` 브랜치에만 남는다. 기준 브랜치 병합은 운영자 확인 대기로 표시될 뿐 자동으로 하지 않는다.
