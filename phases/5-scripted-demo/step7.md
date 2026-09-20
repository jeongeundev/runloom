# Step 7: seed-three-agents

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/AGENTS.md`(비밀값은 환경변수에서만), `/docs/ARCHITECTURE.md`(인증 절·배포 절), `/docs/adr/` 전부(ADR-0003 하루 상한, ADR-0005), `/docs/GLOSSARY.md`(`local_registration_id`, `connector`, `demo-report-repo`, `LocalStack`)
- `/scripts/seed_demo.py` — `seed(...)`: 에이전트 2개 upsert(`agent-ops-demo`, `agent-codex-mac`), 연결 코드 발급, 멱등 규칙
- `/scripts/local_stack.py` — seed 호출과 `connector connect/register/run` 순서, step 0 의 `--scripted`
- `/src/workflow/connector/cli.py` — `register --id --repo --repository-id --tool codex|claude --verify …`, `run --adapter auto`
- `/src/workflow/connector/runner.py` — `select_adapter`(등록의 `tool` 로 어댑터 선택)
- `/src/workflow/server/settings.py` — `Limits`, `ENV_KEYS`(`WORKFLOW_LIMIT_PER_SESSION_DAILY`, `WORKFLOW_LIMIT_GLOBAL_DAILY`)
- `/src/workflow/server/web.py` — `_check_diagnosis_limits`
- `/deploy/env/central.env.example`, `/deploy/env/diag.env.example`, `/tests/test_deploy_files.py`(env 예시 키가 settings 와 일치해야 한다)
- `/scripts/test_seed_demo.py`, `/scripts/test_local_stack.py`, `/tests/e2e/conftest.py`
- `/phases/5-scripted-demo/step1.md`(`demo_scripted`), `/phases/5-scripted-demo/step2.md`(카탈로그)

이전 step 에서 만들어진 코드를 꼼꼼히 읽고, 설계 의도를 이해한 뒤 작업하라.

## 작업

카탈로그를 3개로: `운영 진단 데모`(API) · `개인 Codex`(local, tool codex) · `Claude Code`(local, tool claude). 공개 데모에서는 셋 다 대본 에이전트(`demo_scripted=True`)다. 진단 한도는 대본이라 비용이 0 이므로 배포에서 크게 올린다(코드 기본값은 그대로).

### `scripts/seed_demo.py`

- `seed(..., scripted: bool = False)` 인자 추가. `--scripted` CLI 플래그. True 면 세 Agent 의 `demo_scripted=True` 로 upsert(멱등 재실행 시 값 유지).
- 세 번째 Agent:
  ```python
  {"agent_id": "agent-claude-mac", "name": "Claude Code", "owner_scope": "personal", "connection_type": "local",
   "local_registration_id": "local-demo-report-claude", "repository_id": REPOSITORY_ID, "base_commit": base_commit,
   "verification_profile_ids": VERIFICATION_PROFILE_IDS,
   "capabilities": [{"code": "code.modify", "scope": {"repository_id": REPOSITORY_ID}}],
   "shared_to_all_sessions": True}
  ```
  `agent-codex-mac` 과 같은 저장소·기준 커밋·검증 프로필. 연결 프로그램이 보고한 값(`connector_id`·연결 상태·`discovered`)은 기존 규칙대로 지우지 않는다. 출력 `agents=` 에 3개.
- 상수 `LOCAL_REGISTRATION_IDS = {"codex": "local-demo-report", "claude": "local-demo-report-claude"}` 를 두고 `local_stack.py`·배포 스크립트가 같은 값을 쓴다.

### `scripts/local_stack.py`

- connector 등록을 두 번 한다: `register --id local-demo-report --tool codex …` 와 `register --id local-demo-report-claude --tool claude …`(같은 `--repo`·`--repository-id`·`--verify`). `run --adapter auto` 하나가 두 등록을 모두 실행한다(`select_adapter` 가 등록의 tool 로 고른다 — phase 4 step 3). `--scripted` 면 seed 도 `--scripted` 로.
- 로컬 스택 기동 뒤 카탈로그가 3개인지 확인하는 대기 조건을 e2e conftest 가 쓰는 헬퍼에 반영한다(`_wait_agent` 등이 `agent-claude-mac` 도 기다릴 수 있게).

### 한도

- `deploy/env/central.env.example`: `WORKFLOW_LIMIT_PER_SESSION_DAILY=200`, `WORKFLOW_LIMIT_GLOBAL_DAILY=5000` 로 예시 값을 바꾸고 주석에 "대본 진단(DIAG_MODEL=fake)은 비용 0 — 실제 모델을 켜면 ADR-0003 값(10/36)으로 되돌린다". `Limits` 기본값(코드)은 바꾸지 않는다.
- `deploy/env/diag.env.example`: `DIAG_MODEL=fake`, `DIAG_FAKE_TURN_SECONDS=2.5`, `OPENAI_API_KEY=`(비움) + 주석 "공개 데모는 fake. 실제 모델은 키·예산 확인 후". `DIAG_GLOBAL_DAILY`·예산 키는 그대로 두되 주석에 fake 에서는 무의미함을 적는다. (`tests/test_deploy_files.py` 의 "secrets empty"·"keys match" 검사가 통과해야 한다 — `DIAG_FAKE_TURN_SECONDS` 는 step 0 이 `ENV_KEYS` 에 넣었다.)

### `tests/workflow/server/conftest.py`

- `seed_agents` 에 `agent-claude-mac` 을 추가하되 **기본 fixture 의 기존 테스트가 깨지지 않게** 별도 fixture `agents_three`(또는 `seed_agents(conn, with_claude=True)`) 로 둔다. step 5·6 이 넣은 테스트가 이미 시드를 추가했다면 그것을 이 형태로 정리한다.

### 테스트 (먼저 작성)

- `scripts/test_seed_demo.py`: 3개 upsert·멱등·`--scripted` 왕복·보고값 보존·`agent-claude-mac` 의 `local_registration_id`.
- `scripts/test_local_stack.py`: 등록 명령 2개가 만들어지는지(인자 배열 검사 수준), `--scripted` 가 seed 인자에 반영.
- `tests/test_deploy_files.py`: env 예시의 새 값·키 검사 통과.
- `tests/e2e/test_scenario.py`: 첫 방문 뒤 카탈로그 3개 등록 → 기존 시나리오 통과(Codex 가 먼저 등록되면 B 는 Codex).

## Acceptance Criteria

```bash
python3 -m pytest scripts/ tests/test_deploy_files.py -q
python3 -m pytest -q
python3 -m ruff check .
grep -n "agent-claude-mac\|local-demo-report-claude\|demo_scripted" scripts/seed_demo.py | head
grep -n "DIAG_MODEL=fake\|DIAG_FAKE_TURN_SECONDS" deploy/env/diag.env.example
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - ARCHITECTURE.md 디렉토리 구조·외부 의존 표(OpenAI 는 "켜면"으로만).
   - ADR-0003: 실제 모델 키·예산 확인 전 유료 호출 금지 — 예시 env 에 키 비움.
   - AGENTS.md CRITICAL: 비밀값을 파일·DB 에 넣지 않는다(seed 의 `credential_ref` 는 참조명만).
   - GLOSSARY.md 용어.
3. 결과에 따라 `phases/5-scripted-demo/index.json` 의 해당 step 을 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"`
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- `agent-codex-mac`·`local-demo-report` 의 ID 를 바꾸지 마라. 이유: 테스트·문서 67곳이 참조한다. 새 Agent 만 추가한다.
- `Limits` 의 코드 기본값을 올리지 마라. 이유: 실제 모델을 켠 배포가 기본값으로 보호되어야 한다 — 배포 env 예시에서만 올린다.
- 실제 `claude`·`codex` 를 호출하는 테스트를 쓰지 마라.
- 기존 테스트를 깨뜨리지 마라.
