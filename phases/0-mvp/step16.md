# Step 16: deploy-prep

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/docs/adr/0006-deployment-vm-caddy-mac-connector.md`
- `/docs/ARCHITECTURE.md` — "배포와 실행 예산" 절 전체 (실행 위치·프로세스 표, 재시작·백업·보존, 환경변수 파일 0600)
- `/docs/adr/0000-principles.md` — 공모전 제약 (심사 9/21~10/5 상시 접속, 무로그인)
- `/AGENTS.md` 명령어 절, `/scripts/seed_demo.py` (Step 14), `/src/workflow/server/settings.py`·`/src/diagnostic_demo/settings.py` (환경변수 이름)

## 작업

VM 과 운영자 Mac 에 올릴 설정 파일과 런북을 `deploy/` 와 `docs/DEPLOY.md` 에 만든다. 실제 VM 작업(제공자 선택, 도메인 구매, DNS)은 사용자가 런북대로 수동으로 한다 — ARCHITECTURE 에 "VM 제공자·도메인 이름은 아직 지정하지 않았다".

### 파일

```text
deploy/systemd/workflow-central.service      # ExecStart=/opt/workflow/venv/bin/python -m uvicorn workflow.server.app:app --host 127.0.0.1 --port 8000, EnvironmentFile=/etc/workflow/central.env, WorkingDirectory=/opt/workflow, Restart=always, User=workflow
deploy/systemd/workflow-worker.service       # python -m workflow.server.worker, 같은 env
deploy/systemd/workflow-diag.service         # python -m uvicorn diagnostic_demo.api.app:app --host 127.0.0.1 --port 8100, EnvironmentFile=/etc/workflow/diag.env
deploy/systemd/workflow-diag-worker.service  # python -m diagnostic_demo.worker
deploy/systemd/workflow-backup.service + workflow-backup.timer   # 매일 03:00 deploy/backup.sh
deploy/Caddyfile                             # {$WORKFLOW_DOMAIN} { reverse_proxy 127.0.0.1:8000 }  — 진단 API 는 노출하지 않음
deploy/env/central.env.example               # WORKFLOW_DB_PATH=/var/lib/workflow/central/db.sqlite, WORKFLOW_ARTIFACT_DIR, SESSION_SECRET=, OPERATOR_TOKEN=, DIAG_API_URL=http://127.0.0.1:8100, DIAG_API_TOKEN=, WORKFLOW_LIMIT_*
deploy/env/diag.env.example                  # DIAG_DB_PATH=/var/lib/workflow/diag/db.sqlite, DIAG_ARTIFACT_DIR, DIAG_API_TOKEN=, OPENAI_API_KEY=, DIAG_MODEL_ID, DIAG_BUDGET_USD=30, DIAG_PRICE_*
deploy/backup.sh                             # sqlite3 .backup 두 DB + tar artifacts/traces → /var/backups/workflow/{date}/, 7일 지난 것 삭제. set -euo pipefail
deploy/launchd/com.workflow.connector.plist  # ProgramArguments: /usr/bin/env python3 -m workflow.connector run, KeepAlive true, WorkingDirectory, StandardOut/ErrPath ~/Library/Logs/workflow-connector/, EnvironmentVariables PATH(codex 가 있는 경로 포함)
deploy/install-vm.sh                         # Ubuntu 기준: apt python3.13 venv sqlite3 caddy, useradd workflow, /opt/workflow clone + venv + pip install ., /var/lib/workflow/{central,diag} 생성(0700), systemd 유닛 복사·enable. 멱등. 실제 실행은 사용자
docs/DEPLOY.md                               # 런북
```

### `docs/DEPLOY.md` 런북 (순서대로, 각 단계에 확인 명령)

1. VM 준비 (Ubuntu 24.04, 1 vCPU/1GB 면 충분, 포트 80·443). 도메인의 A 레코드 → VM IP. 확인: `dig +short {domain}`.
2. `deploy/install-vm.sh` 실행. 확인: `systemctl status workflow-*`.
3. 환경변수 파일 채우기: `openssl rand -hex 32` 로 `SESSION_SECRET`·`OPERATOR_TOKEN`·`DIAG_API_TOKEN` (중앙·진단에 같은 값), `OPENAI_API_KEY`, 단가. `chmod 600`.
4. Caddy: `WORKFLOW_DOMAIN` 을 `/etc/caddy/Caddyfile` 에. 확인: `curl -I https://{domain}` 200.
5. seed: `python -m scripts.seed_demo --db ... --base-commit {report-base sha} --print-code` → 연결 코드 출력. 확인: 브라우저에서 `/agents` 에 2개.
6. Mac: `git clone` 이 저장소, `pip install -e .`, `python3 scripts/scaffold_demo_repo.py ~/demo-report-repo`, `python3 -m workflow.connector connect --server https://{domain} --code {code}`, `register ...`(Step 14 와 같은 인자), plist 를 `~/Library/LaunchAgents/` 에 복사 후 `launchctl load`. 확인: `/agents` 에서 `agent-codex-mac` 연결됨.
7. 운영자 화면에서 시연 A → B 를 한 번 끝까지 돌려 "운영자 예시 실행" 이 생기게 한다 (Mac 오프라인 대비. ARCHITECTURE 제안이며 화면 공개 기능은 미구현 — 이 문서에는 "운영자 세션의 업무로 남는다" 고만 적는다).
8. 백업 타이머 확인: `systemctl list-timers workflow-backup`.
9. 심사 기간 점검 목록: 매일 `/budget`, `journalctl -u workflow-worker --since -1d | grep -c ERROR`, Mac 의 `launchctl list | grep workflow`.
10. 알려진 한계: Mac 오프라인 시 B 는 `대기`, Codex 사용량 한도 시 B 는 `실패`. 진단 총액 상한 US$30.

### 테스트 — `tests/test_deploy_files.py`

- 4개 `.service` 의 `ExecStart` 가 AGENTS.md 명령어의 모듈 경로(`workflow.server.app:app`, `workflow.server.worker`, `diagnostic_demo.api.app:app`, `diagnostic_demo.worker`)를 포함한다. `Restart=always`, `EnvironmentFile` 있음.
- plist 가 `plistlib` 로 파싱되고 `KeepAlive` true, `ProgramArguments` 에 `workflow.connector`.
- `Caddyfile` 에 `8100` 이 없다 (진단 API 비노출).
- `env.example` 두 파일의 키가 `load_settings` 가 읽는 환경변수 이름과 일치한다 (settings 모듈에서 이름 목록을 export 해 비교).
- `backup.sh` 가 `bash -n` 을 통과하고 `set -euo pipefail` 로 시작한다.
- `DEPLOY.md` 에 `seed_demo`, `connect --code`, `launchctl` 문자열이 있다.

## Acceptance Criteria

```bash
python3 -m pytest tests/test_deploy_files.py -q
python3 -m pytest -q
python3 -m ruff check .
bash -n deploy/backup.sh deploy/install-vm.sh
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 체크리스트:
   - 비밀값이 예시 파일에서 비어 있는가? 저장소에 실제 값이 없는가?
   - 진단 API 가 외부에 노출되지 않는가?
   - 런북의 명령이 AGENTS.md·Step 11·14 의 CLI 와 일치하는가?
3. `phases/0-mvp/index.json` 의 step 16 을 업데이트한다.

## 금지사항

- VM 에 실제로 접속하거나 도메인을 등록하지 마라. 이유: 사용자 결정·비용.
- Docker·Kubernetes 를 추가하지 마라. 이유: ADR-0006 은 systemd 4개.
- 기존 테스트를 깨뜨리지 마라.
