# Step 2: runner-secrets — 러너의 `RUNLOOM_*` 예약 이름과 `wfm_` 로그 마스킹

## 읽어야 할 파일

- AGENTS.md, `phases/26-cli/README.md`("결정" D12·D13), `phases/26-cli/index.json`(이전 step summary)
- `docs/ARCHITECTURE.md` "CLI — phase 26"(러너 변경 문단)
- `src/workflow/connector/masking.py`(`SECRET_PATTERNS`·`_REPLACEMENTS`·`RESERVED_ENV_NAMES`·`registered_env`·`codex_env`), `src/workflow/connector/cli.py`(`--env` 처리·`RESERVED_ENV_NAMES` 사용처)
- `tests/workflow/connector/test_masking.py`, `tests/workflow/connector/test_cli.py`

## 작업

1. `RESERVED_ENV_NAMES` 에 `RUNLOOM_TOKEN`·`RUNLOOM_SERVER` 를 더한다 — 러너 등록 `--env RUNLOOM_TOKEN=…` 이 기존 예약 이름과 같은 방식으로 거절되고, 이미 저장된 등록에 그 이름이 있어도 `registered_env` 가 실행 환경에 넣지 않는다.
2. `SECRET_PATTERNS` 에 `wfm_` 패턴(기존 `wfc_` 와 같은 꼴), 대체 문자열 `wfm_***`.
3. `RUNLOOM_HOME` 은 비밀값이 아니므로 예약하지 않는다.
4. `docs/ARCHITECTURE.md` 에서 `RESERVED_ENV_NAMES` 목록·마스킹 패턴을 나열하는 줄(`grep -n RESERVED_ENV_NAMES docs/ARCHITECTURE.md`, `grep -n 'wfc_\*\*\*' docs/ARCHITECTURE.md` 로 찾는다 — 러너 CLI·마스킹 설명)을 새 이름·패턴으로 고친다.

## 테스트

- `mask_secrets` 가 `wfm_` 원문을 `wfm_***` 로 바꾸고 개수를 센다.
- 러너 CLI `register`/`setup` 의 `--env RUNLOOM_TOKEN=x`·`--env RUNLOOM_SERVER=x` 거절(기존 예약 이름 테스트와 같은 꼴, 같은 exit code).
- `registered_env` 가 저장된 `RUNLOOM_TOKEN` 을 빼는지.
- `codex_env` 가 기본 환경의 `RUNLOOM_TOKEN` 을 넘기지 않는지(이미 허용 목록이라 통과해야 함 — 회귀 고정).
## 테스트 먼저

소스를 바꾸기 전에 `tests/` 미러 경로(`src/workflow/x/y.py` → `tests/workflow/x/test_y.py`)에 실패하는 테스트를 먼저 작성하고, 실패 원인이 의도한 것인지 확인한다(`tdd-guard.sh` 가 테스트 없는 소스 작성을 막는다). 구현한 뒤 해당 테스트와 전체 회귀를 통과시킨다. 이 step 의 변경으로 기존 테스트(개수·이름 집합·문구 단정)가 깨지면 새 동작 기준으로 고치되 단정을 약하게 만들지 않는다. 무엇을 왜 바꿨는지는 summary 에 남긴다.

## Acceptance Criteria

```bash
python3 -m pytest -q
python3 -m ruff check .
```

위 커맨드와 이 step 의 "테스트" 항목을 모두 확인한다.

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트: ARCHITECTURE.md 디렉토리 구조·외부 의존 표를 벗어나지 않았는가, ADR 기술 스택 안인가, AGENTS.md CRITICAL 규칙(도메인 순수성, `server`↔`connector`(↔`cli`) 상호 import 금지, 외부 입력으로 명령·경로 실행 금지, 비밀값)을 지켰는가, GLOSSARY 용어를 그대로 썼는가.
3. `phases/26-cli/index.json` 의 이 step 을 갱신한다: 성공 → `"status": "completed"`, `"summary"`(다음 step 에 필요한 파일·이름·결정), `"verification"`(실행한 AC 명령과 종료 코드 목록 `[{"command": "...", "exit_code": 0}]`), 걸리는 점이 있으면 `"concerns"`. 3회 수정해도 실패 → `"error"` + `"error_message"`. 사용자 개입 필요 → `"blocked"` + `"blocked_reason"` 후 즉시 중단.

## 금지사항

- `codex_env` 허용 목록을 넓히지 마라. 이유: AGENTS.md CRITICAL — 비밀값은 Codex 프로세스 환경에 넣지 않는다.
- `server/`·`cli/` 를 import 하지 마라. 이유: AGENTS.md 경계 규칙.
- 러너 프로토콜(claim·결과 제출)을 바꾸지 마라. 이유: D3 — 러너 재설치가 필수가 되면 안 된다.
- 기존 테스트를 깨뜨리지 마라.
