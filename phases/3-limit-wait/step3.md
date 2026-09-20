# Step 3: connector-policy-flag

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/docs/ARCHITECTURE.md` — "등록·선택·권한"
- `/docs/adr/` (하위 파일 전부) — 특히 `0007-usage-limit-wait-policy.md`
- `/docs/GLOSSARY.md`
- `/docs/DEPLOY.md` — 연결 프로그램 `register` 명령 예시가 있는 절
- `/src/workflow/connector/cli.py` — `register` 서브커맨드 인자(`--id`, `--repo`, `--repository-id`, `--tool`, `--verify`), `_register`
- `/src/workflow/connector/client.py` — `report_registration`("서버 `RegistrationRequest` 필드만 보낸다")
- `/src/workflow/connector/state.py` — `save_registration`(로컬에 남기는 필드)
- `/src/workflow/server/machine_api.py` — Step 2 가 추가한 `RegistrationRequest.on_usage_limit`
- `/tests/workflow/connector/test_cli.py`, `test_client.py`

이전 step에서 만들어진 코드를 꼼꼼히 읽고, 설계 의도를 이해한 뒤 작업하라.

## 배경

Step 2 로 서버가 Agent 의 `on_usage_limit` 을 받을 수 있다. 운영자가 연결 프로그램에서 등록할 때 이 값을 정하도록 `register` 에 플래그를 더한다. 기본은 `wait` 라 플래그 없이 등록해도 ADR-0007 동작이 된다.

## 작업

### `src/workflow/connector/cli.py`

- `register` 에 `--on-usage-limit {wait,fail}` (기본 `wait`) 을 추가한다.
- `_register` 가 `client.report_registration(...)` 페이로드에 `"on_usage_limit": args.on_usage_limit` 을 넣는다.
- 등록 완료 출력 한 줄에 정책을 함께 보여준다 (예: `사용량 한도 시: 리셋까지 대기`).

### `src/workflow/connector/client.py`

- `report_registration` 이 `on_usage_limit` 을 본문에 실는다. 값이 없으면 넣지 않는다(서버 기본값 `wait`).

### `src/workflow/connector/state.py`

- 로컬 등록 저장 형식은 **바꾸지 않는다**. 정책은 서버가 보관한다. 이유: 연결 프로그램은 등록을 재보고하지 않으며, 로컬 사본을 두면 두 곳이 어긋날 수 있다.

### `docs/DEPLOY.md`

- `register` 명령 예시에 `--on-usage-limit wait` 를 보이고 값 설명 한 줄.

### 테스트 (먼저 작성)

- `tests/workflow/connector/test_cli.py`: 플래그 없이 → 전송 본문 `on_usage_limit == "wait"`; `--on-usage-limit fail` → `"fail"`; 잘못된 값 → argparse 오류(종료 코드 2).
- `tests/workflow/connector/test_client.py`: 본문에 필드가 실리고, 없으면 생략된다.

## Acceptance Criteria

```bash
python3 -m pytest tests/workflow/connector -q
python3 -m pytest -q
python3 -m ruff check .
python3 -m workflow.connector register --help | grep -- --on-usage-limit
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - ARCHITECTURE.md 디렉토리 구조를 따르는가?
   - ARCHITECTURE.md 외부 의존 표에 없는 외부 서비스를 끌어들이지 않았는가?
   - ADR 기술 스택을 벗어나지 않았는가?
   - AGENTS.md CRITICAL 규칙을 위반하지 않았는가? (`server/` 와 `connector/` 는 서로 import 하지 않는다)
   - GLOSSARY.md 용어를 그대로 썼는가? 금지 표현을 쓰지 않았는가?
3. 결과에 따라 `phases/3-limit-wait/index.json`의 해당 step을 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"`
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- 실제 서버에 등록하지 마라. 이유: 테스트는 `httpx.MockTransport`(`tests/workflow/connector/conftest.py` 의 `FakeCentral`) 로 한다.
- `connector/` 에서 `server/` 모듈을 import 하지 마라. 이유: AGENTS.md CRITICAL. 필드 이름은 문자열로 맞춘다.
- `--tool` 선택지에 `claude` 를 추가하지 마라. 이유: ADR-0001 — 어댑터는 Codex 1종. `fallback` 도 이번 범위 밖이다.
- 기존 테스트를 깨뜨리지 마라
