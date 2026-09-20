# Step 4: third-agent-seed

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/AGENTS.md`
- `/docs/PRD.md` — 2절 기본값: 자동 선택은 후보가 정확히 1개일 때, 0·2개 이상이면 확인 필요 → 직접 선택
- `/docs/UI_GUIDE.md` — 화면 흐름, "확인 필요" 상태
- `/docs/GLOSSARY.md` — LocalStack, fake codex, Candidate, example
- `/scripts/seed_demo.py` — `agent-codex-mac` 등록 블록, `LOCAL_REGISTRATION_ID`·`REPOSITORY_ID`·`VERIFICATION_PROFILE_IDS` 상수
- `/scripts/local_stack.py` — `fake_codex` 래핑, connector `register` 호출
- `/tests/e2e/fake_codex.py`, `/tests/e2e/test_scenario.py`, `/tests/e2e/conftest.py`
- `/src/workflow/server/web.py` — `EXAMPLES`, `task_create`(`with_successor` 로 fix 예시 B 를 함께 만든다), `_insert_new_task`, `/tasks/{task_id}/select`
- `/src/workflow/server/templates/task_new.html` — `with_successor` 체크박스
- `/tests/workflow/server/test_web.py` — `test_create_task_needs_selection_when_two_candidates_then_manual_select`, `test_create_diagnose_with_successor_registers_b_waiting_on_a`
- `/docs/DEPLOY.md` 5·6 절 — seed·register 명령이 코드 상수와 일치해야 한다 (`tests/test_deploy_files.py` 가 검사)

이전 step에서 만들어진 코드를 꼼꼼히 읽고, 설계 의도를 이해한 뒤 작업하라. 새 기능은 테스트를 먼저 작성한다 (AGENTS.md TDD, `tests/` 는 `src/` 미러 배치).

## 배경

세 번째 에이전트 `agent-claude-mac`(personal · local · `code.modify` · `demo-report-repo`)이 생기면 코드 수정 업무의 자동 선택은 후보 2개 → **확인 필요**가 된다 (PRD 기본값). 그러면 시연 폼이 함께 만드는 후속 B 가 A 완료 후 자동 착수하지 못한다. 그래서 시연 폼은 B 의 담당 에이전트를 **직접 지정**하게 하고(기본 개인 Codex), "후보 2개 → 확인 필요 → 직접 선택" 장면은 심사자가 빈 폼에서 자동 선택을 골랐을 때 나오게 둔다.

## 작업

### `scripts/seed_demo.py`

- 세 번째 에이전트 추가:
  ```python
  "agent_id": "agent-claude-mac", "name": "개인 Claude Code", "owner_scope": "personal",
  "connection_type": "local", "local_registration_id": "local-demo-report-claude",
  "repository_id": REPOSITORY_ID, "base_commit": base_commit,
  "verification_profile_ids": VERIFICATION_PROFILE_IDS,
  "capabilities": [{"code": "code.modify", "scope": {"repository_id": REPOSITORY_ID}}],
  "shared_to_all_sessions": True,
  ```
  연결 프로그램이 보고한 값 보존 규칙은 `agent-codex-mac` 과 같게 한다 (같은 헬퍼로 묶어라). 상수 `LOCAL_REGISTRATION_ID_CLAUDE = "local-demo-report-claude"`.
- `--print-code` 출력의 `agents=` 에 셋이 나온다.

### `scripts/local_stack.py`

- `--fake-claude PATH` 옵션. 주면 `claude` 이름으로 감싸 connector PATH 앞에 둔다 (`fake_codex` 와 같은 방식, 같은 bin 디렉터리).
- connector `register` 를 두 번 호출한다: 기존 codex 등록 + `--tool claude --id local-demo-report-claude --repo <같은 저장소> --repository-id demo-report-repo --verify …` (같은 검증 프로필). `run` 은 `--adapter` 없이(auto).

### `tests/e2e/fake_claude.py` (신규, 첫 줄 주석 "e2e 전용 가짜 에이전트")

- `claude -p --output-format json … --json-schema <schema>` 를 흉내 낸다: stdin 프롬프트에서 인계 응답 경로를 찾아 `fake_codex.py` 와 같은 수정을 worktree 에 적용하고(공통 부분은 `tests/e2e/_fake_fix.py` 로 빼서 둘이 import), step 2 에서 확인한 형태의 JSON 한 덩어리를 stdout 에 쓴다 (구조화 출력 `{summary, outcome:"ready_for_review"}`). 모델·네트워크 호출 없음.

### `src/workflow/server/web.py`·`templates/task_new.html`

- 시연 A 폼(`offer_successor`)에 `successor_agent_id` select 를 추가한다: 공유 에이전트 중 `code.modify` 능력이 있는 것들 + "자동 선택(후보가 둘이면 확인 필요)". 기본값 `agent-codex-mac`. 도움말: "후속 B 를 맡길 에이전트. 코드 수정 에이전트가 둘이라 자동 선택은 확인 필요가 됩니다."
- `task_create`: `with_successor` 이면 B 를 `selection_mode="manual", chosen_agent_id=successor_agent_id` 로 만든다. 값이 `""`(자동) 이면 지금처럼 auto. 지정한 에이전트가 공유 목록에 없거나 `code.modify` 가 없으면 422.
- `EXAMPLES["fix"]` 는 그대로 둔다 (`?example=fix` 직접 등록 경로는 여전히 auto → 후보 2개면 확인 필요).

### 테스트 (먼저 작성)

- `tests/test_seed_demo.py`(있으면 거기, 없으면 신규): seed 후 에이전트 3개, `agent-claude-mac` 의 `local_registration_id`·능력, 재실행 시 보고값 보존.
- `tests/workflow/server/test_web.py`: 시연 폼에 `successor_agent_id` 가 있고 기본이 `agent-codex-mac`; with_successor + 기본값 → B 가 `manual`·`agent-codex-mac`·`대기 · 선행 대기`; `successor_agent_id=""` + 후보 2개 → B 가 `확인 필요`; 없는 에이전트 → 422. `test_create_task_needs_selection_when_two_candidates_then_manual_select` 는 그대로 통과해야 한다.
- `tests/e2e/test_scenario.py` (WORKFLOW_E2E=1 에서만): 
  - test_01: 에이전트 3개 모두 `연결됨`.
  - test_03: B 를 `?example=fix` 로 등록하면 `확인 필요 · 후보 2개` → `/select` 로 `agent-codex-mac` 선택 → `대기 · 선행 대기`. (시연 폼 경로는 test_02 에서 `with_successor` 로 A+B 를 만들고 B 가 바로 `대기 · 선행 대기` 인지 본다 — 둘 중 하나로 정리하되 두 장면이 다 검사되게)
  - test_12 (신규): A 완료 뒤 코드 수정 업무 C 를 `agent-claude-mac` 직접 선택으로 등록 → 가짜 claude 로 `확인 필요 · 검토 대기` → 산출물 칩에 `Claude JSONL`·`Claude stderr`, 결과 커밋이 C 의 `task/` 브랜치에.
  - 기존 11건은 통과 유지 (B 의 Codex 경로).

## Acceptance Criteria

```bash
python3 -m pytest tests/workflow/server tests/test_seed_demo.py tests/test_deploy_files.py -q
python3 -m pytest -q
python3 -m ruff check .
WORKFLOW_E2E=1 python3 -m pytest tests/e2e -q -x          # 12 passed (가짜 codex·claude, 실제 호출 없음)
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - ARCHITECTURE.md 디렉토리 구조를 따르는가?
   - ARCHITECTURE.md 외부 의존 표에 없는 외부 서비스를 끌어들이지 않았는가?
   - ADR 기술 스택을 벗어나지 않았는가?
   - AGENTS.md CRITICAL 규칙을 위반하지 않았는가? (도메인이 I/O 를 import 하지 않는가, server↔connector 가 서로 import 하지 않는가, 외부 입력에서 명령·경로를 실행하지 않는가, 비밀값이 DB·로그·응답에 없는가)
   - GLOSSARY.md 용어를 그대로 썼는가? 금지 표현을 쓰지 않았는가?
3. 결과에 따라 `phases/4-claude-issues/index.json`의 해당 step을 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"` (만든 파일·함수·결정 사항)
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- 자동 선택 규칙(`domain/selection.py`)에 "둘 중 아무거나" 같은 동률 처리를 넣지 마라. 이유: PRD 기본값 "0·2개 이상이면 확인 필요" 는 사용자 확정이다. 시연은 직접 지정으로 푼다.
- 실제 `codex`·`claude` 를 e2e 에서 호출하지 마라. 이유: 구독 한도. 가짜 실행 파일만.
- `agent-codex-mac` 의 등록 ID·검증 프로필 상수를 바꾸지 마라. 이유: 배포된 Mac 연결 프로그램과 DEPLOY.md 가 그 값으로 등록돼 있다.
- 기존 테스트를 깨뜨리지 마라
