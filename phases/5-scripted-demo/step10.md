# Step 10: deploy-vm-scripted

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/AGENTS.md`(비밀값·명령어 절), `/docs/ARCHITECTURE.md`(배포 절·외부 의존 표), `/docs/adr/0006-deployment-vm-caddy-mac-connector.md`, `/docs/adr/0003-diagnosis-model-openai-gpt41-mini.md`, `/docs/adr/0000-principles.md`, `/docs/GLOSSARY.md`, `/docs/DEPLOY.md` 전체
- `/deploy/install-vm.sh`, `/deploy/update-vm.sh`, `/deploy/systemd/*.service`, `/deploy/env/*.example`, `/deploy/launchd/com.workflow.connector.plist`, `/deploy/backup.sh`
- `/tests/test_deploy_files.py` — 배포 파일과 AGENTS.md 명령어·settings 키·seed 인자·런북 문장의 일치를 검사한다 (이 step 의 문서·스크립트 변경은 이 테스트를 함께 고쳐야 한다)
- `/scripts/scaffold_demo_repo.py`, `/scripts/seed_demo.py`(step 7), `/scripts/local_stack.py`(step 0·7 — VM 배포는 이 스크립트를 쓰지 않지만 같은 인자를 쓴다)
- `/src/workflow/connector/cli.py`, `/src/workflow/connector/config.py`(토큰 파일 위치 — Linux 에서의 기본 경로 확인)
- `/src/workflow/scripted/` (step 0)

이전 step 에서 만들어진 코드를 꼼꼼히 읽고, 설계 의도를 이해한 뒤 작업하라.

## 작업

공개 데모를 **VM 한 대**에서 대본 에이전트로 돌린다. Mac 연결 프로그램·OpenAI 키는 쓰지 않는다. 이 step 은 배포 **파일·문서·테스트**를 만든다. 실제 VM 반영(ssh)은 사용자가 런북대로 한다 — 이 step 에서 VM 에 접속하지 않는다.

### ADR

- `docs/adr/0008-public-demo-scripted-agents.md` — 결정: 심사 기간 공개 데모는 대본 에이전트(`workflow.scripted.*`, `DIAG_MODEL=fake`)로 VM 에서만 돈다. 이유: 불특정 방문자 트래픽에 실제 API·구독 에이전트를 노출하면 비용·한도 문제(2026-09-21 사용자 확정). 유지되는 것: 계약·검증기·worktree·실제 pytest·상태 규칙은 실제와 동일, 화면에 `시연용 · 대본 재생` 표시. 트레이드오프: 진단 내용·수정 diff 가 매번 같다, 실제 모델의 오류·보류 사례를 보여 주지 못한다(DIAG_EVAL 링크). 실제 연동 증거: 2026-09-20 실제 gpt-4.1·Codex 로 A→B 를 완료한 기록(VERIFICATION_LOG). ADR-0006 의 "운영자 Mac 연결 프로그램" 구성은 셀프호스트 실사용용으로 유지하고 공개 데모에서는 쓰지 않는다.

### 배포 파일

- `deploy/bin/codex`, `deploy/bin/claude` — 2줄 셸 래퍼: `exec /opt/workflow/venv/bin/python -m workflow.scripted.codex "$@"` / `…claude "$@"`. `install-vm.sh` 가 `chmod 755`.
- `deploy/systemd/workflow-connector.service` — `User=workflow`, `WorkingDirectory=/opt/workflow`, `EnvironmentFile=/etc/workflow/connector.env`, `Environment=PATH=/opt/workflow/deploy/bin:/usr/local/bin:/usr/bin:/bin`(래퍼가 앞), `ExecStart=/opt/workflow/venv/bin/python -m workflow.connector run`, `Restart=always`, `After=workflow-central.service`. 다른 유닛과 같은 형식.
- `deploy/env/connector.env.example` — `WORKFLOW_SCRIPT_PACE_SECONDS=25`, `WORKFLOW_CONNECTOR_HOME=/var/lib/workflow/connector`(토큰·상태 파일 위치 — `connector/config.py` 가 이 환경변수를 읽지 않으면 이 step 에서 추가한다: 기본은 기존 경로, 값이 있으면 그 디렉터리), 주석에 "대본 에이전트 전용. 실제 Codex/Claude 는 이 VM 에 없다".
- `deploy/env/central.env.example`·`diag.env.example` 은 step 7 값 그대로(한도·fake).
- `deploy/install-vm.sh` — 추가: `install -d /var/lib/workflow/connector /var/lib/workflow/demo`(소유 workflow), `deploy/bin/*` 실행 권한, 유닛 5개 enable(연결 프로그램 포함). 서비스 start 는 여전히 하지 않는다.
- `deploy/update-vm.sh` — 재시작 목록에 `workflow-connector` 추가. **스키마 버전이 바뀐 배포**를 위한 명시적 플래그 `WORKFLOW_RESET_DB=1`: 서비스 중지 → `central/db.sqlite`·`central/artifacts`·`diag/*` 를 `/var/backups/workflow/reset-{날짜}/` 로 옮김 → 재시작. 플래그 없으면 지금처럼 코드만 갱신. 출력에 "DB 초기화됨(백업: …)" 을 남긴다.
- `deploy/backup.sh` — 연결 프로그램 상태 디렉터리도 백업 대상에 넣는다.
- `deploy/launchd/com.workflow.connector.plist` 는 남긴다(셀프호스트용). 문서에서만 "공개 데모는 사용 안 함".

### `docs/DEPLOY.md` 다시 쓰기 (구조 유지, 내용 교체)

1. VM 준비 (그대로)
2. 설치 스크립트 (유닛 5개)
3. 환경변수 파일 채우기 — `central.env`(비밀값 2개 + 한도), `diag.env`(`DIAG_MODEL=fake`, 키 없음), `connector.env`
4. Caddy (그대로)
5. **데모 저장소와 seed — VM 에서**: `sudo -u workflow -H venv/bin/python scripts/scaffold_demo_repo.py /var/lib/workflow/demo/demo-report-repo` → `base_commit` → `seed_demo.py --scripted --base-commit {sha} --print-code`. 카탈로그 3개.
6. **VM — 연결 프로그램**: `sudo -u workflow -H env WORKFLOW_CONNECTOR_HOME=/var/lib/workflow/connector venv/bin/python -m workflow.connector connect --code … --server https://{domain}` → `register --id local-demo-report --tool codex --repo /var/lib/workflow/demo/demo-report-repo --repository-id demo-report-repo --verify "vp-pytest=…" --verify "vp-report=…"` → 같은 저장소로 `register --id local-demo-report-claude --tool claude …` → `systemctl start workflow-connector`. 확인: `/agents/register` 카탈로그 3개 모두 연결됨.
7. 운영자 예시 실행 — 새 흐름(등록 → 가져오기 → 시작 → 승인)으로. 비용 0.
7b. 코드 갱신 — `update-vm.sh`, 스키마가 바뀌면 `WORKFLOW_RESET_DB=1`.
8. 백업 타이머
9. 심사 기간 점검 목록 — 매일: 5개 서비스 active, `/agents/register` 3개 연결됨, 디스크(`/var/lib/workflow/demo` 크기 — step 8 정리로 커지지 않아야 함), `/budget` 은 fake 라 0.
10. 알려진 한계 — 대본이라 결과가 매번 같음; 실제 모델·실제 Codex 로 바꾸려면 env 3개 변경 + Mac 연결(ADR-0006); DB 초기화는 `WORKFLOW_RESET_DB=1` 로만.

### `docs/ARCHITECTURE.md`

- 배포 절: 공개 데모 구성(VM 5 프로세스, 대본 에이전트) 표를 추가하고 ADR-0008 링크. 외부 의존 표의 OpenAI 행에 "공개 데모에서는 호출하지 않음(fake)" 을 적는다.

### 테스트 (먼저 작성) — `tests/test_deploy_files.py`

- 유닛 5개가 AGENTS.md 명령어 절의 모듈 경로를 쓰는지(`workflow.connector` 추가 — AGENTS.md 명령어 절에 `python3 -m workflow.connector run` 이 이미 있다), 연결 프로그램 유닛의 PATH 가 `deploy/bin` 으로 시작하는지, 래퍼 2개가 `workflow.scripted.*` 를 부르는지, `connector.env.example` 키가 `connector/config.py` 가 읽는 키와 같은지, `update-vm.sh` 가 `WORKFLOW_RESET_DB` 없이는 데이터 디렉터리를 건드리지 않는지(문자열 검사), 런북이 스크립트·CLI 와 같은 명령을 쓰는지(기존 `test_runbook_uses_the_same_commands_as_scripts_and_cli` 갱신), 런북이 5·6·7·9·10절과 ADR-0008 을 언급하는지.

## Acceptance Criteria

```bash
python3 -m pytest tests/test_deploy_files.py -q
python3 -m pytest -q
python3 -m ruff check .
bash -n deploy/install-vm.sh deploy/update-vm.sh deploy/backup.sh deploy/bin/codex deploy/bin/claude
ls deploy/systemd/workflow-connector.service deploy/env/connector.env.example docs/adr/0008-public-demo-scripted-agents.md
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - ARCHITECTURE.md 배포 절·외부 의존 표가 새 구성을 반영하는가?
   - ADR-0006(VM+Caddy, 컨테이너 없음)·ADR-0003(키 없으면 유료 호출 없음)·ADR-0008 과 모순이 없는가?
   - AGENTS.md CRITICAL: env 예시에 비밀값 없음, 래퍼는 고정 인자만.
   - GLOSSARY.md 용어.
3. 결과에 따라 `phases/5-scripted-demo/index.json` 의 해당 step 을 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"`
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- VM 에 ssh 로 접속하거나 실제 배포를 하지 마라. 이유: 배포는 사용자가 런북대로 한다 — 이 step 은 파일·문서·테스트만.
- `update-vm.sh` 가 플래그 없이 DB 를 지우게 만들지 마라. 이유: 심사 중 세션 데이터를 실수로 날리지 않기 위해 명시적 `WORKFLOW_RESET_DB=1` 만.
- 실제 `codex`·`claude` 바이너리를 VM 에 설치하는 절차를 넣지 마라. 이유: 공개 데모는 대본만(ADR-0008).
- 기존 테스트를 깨뜨리지 마라.
