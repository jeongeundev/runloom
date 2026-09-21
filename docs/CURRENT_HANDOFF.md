# 현재 인계 — 이종 에이전트 등록과 업무 자동 실행

갱신일: 2026-09-22 (phase 6-typed-handoff step 0~9 완료)
상태: 공개 데모(phase 5, VM 배포·심사 중)에 더해 실사용 첫 phase — 업무 종류(`KindSpec`)·후속 규칙(`SuccessorRule`)을 워크스페이스가 등록하는 구조([ADR-0009](adr/0009-registered-kinds-and-succession-rules.md)) — 를 구현했다. 진단 모델은 gpt-4.1 로 확정(ADR-0003), 공개 데모는 실제 모델·실제 Codex/Claude 를 돌리지 않는다(ADR-0008). 브랜치 `feat-6-typed-handoff`(main `0da3d21` 에서 분기), 푸시 안 함, **VM 에 배포 안 함 — 스키마 버전 3 이라 심사 이후 `WORKFLOW_RESET_DB=1` 로 배포한다**.

## 지금 상태 — 새 세션이 먼저 볼 것

| 항목 | 상태 |
|---|---|
| 브랜치 | `feat-6-typed-handoff` (main `0da3d21` = `origin/main` 에서 분기, 그 위 phase 6 커밋들. phase 5 는 main 에 병합·배포됨. 푸시 안 함) |
| `phases/0-mvp` step 0~17 | **완료.** contracts·domain·adapters·server(web/API/워커)·connector(Codex 어댑터·worktree)·diagnostic_demo(fixture·도구·워커)·deploy 설정·런북 |
| `phases/1-diag-fix` step 0~3 | **완료.** location 배열 인덱스 `[N]` + JSON Schema `pattern`, 도구 텍스트 반환에 줄 번호(`tools-v2`), 프롬프트 v2, 재평가 |
| `phases/2-model-compare` step 0~2 | **완료.** 프롬프트 v3, `DraftInvalid` 턴 사용량 집계, mini·gpt-4.1 5사례 × 3회 비교 → gpt-4.1 `normal` 3/3, [DIAG_EVAL](DIAG_EVAL.md). ADR-0003 을 gpt-4.1 로 확정 |
| `phases/3-limit-wait` step 0~7 | **계획만. 심사 이후 실행** ([ADR-0007](adr/0007-usage-limit-wait-policy.md)). 그 전까지 사용량 한도는 `실패` 로 기록 |
| `phases/4-claude-issues` step 0~3 | **완료·main 병합(`f6a3b43`).** 도구 계약·`LocalToolAdapter`·`ClaudeAdapter`·Runner 디스패치. step 4~11 은 5-scripted-demo 로 대체 |
| `phases/5-scripted-demo` step 0~11 | **완료·main 병합·VM 배포(2026-09-21, `https://runloom.duckdns.org`).** 대본 에이전트(`src/workflow/scripted/`)·스키마 v2(`session_agents`·`chains`)·카탈로그 등록(`/agents/register`)·이슈 fixture 와 라벨 매핑(`domain/task_sources.py`)·워크플로우 구성(`domain/composition.py`)·가져오기(`/tasks/import`)·체인 화면(`/chains/{chain_id}`)·seed 3개·connector worktree 정리·e2e 주 경로 test_12~21·VM 배포 설정([ADR-0008](adr/0008-public-demo-scripted-agents.md)) |
| `phases/6-typed-handoff` step 0~9 | **완료(브랜치, 미배포).** [ADR-0009](adr/0009-registered-kinds-and-succession-rules.md) — 계약 `KindSpec`·`SuccessorRule`·`GenericResult`·`LocalTarget`·`InputRef`·일반화된 `HandoffBundle`(`source_kind`·`source_result_artifact_id`·`inputs`), 도메인 `kinds.py`·`succession.py`(등록부는 인자), 스키마 v3(`kinds`·`succession_rules`, 세션 생성 시 내장 2종·규칙 1개 seed), 워커 후속 조건을 '선행 결과 + 판정 통과 + outcome ∈ 규칙'으로(사람 승인은 후속 착수를 막지 않음)·`_check_generic_results`, 연결 프로그램 읽기 전용 실행(`_run_generic`, Codex `--sandbox read-only`/Claude `Read Glob Grep`), 화면 `/kinds`(종류 카드·한 줄 규칙·등록 폼)와 업무 등록·가져오기·상세의 등록부 연동, e2e test_22~28(세 번째 종류 `review` 를 화면으로 등록하면 진단 → 수정 → 검토가 `composition.py`·`worker.py` 변경 없이 자동 착수), 문서 동기화(step 9). 실제 Claude·Codex 로는 미검증 |
| 공개 데모 구성 | [ADR-0008](adr/0008-public-demo-scripted-agents.md): VM 한 대, systemd 5개(중앙 2·진단 2·연결 프로그램) + Caddy, 카탈로그 3개 `demo_scripted=1`, `DIAG_MODEL=fake`, `deploy/bin/{codex,claude}` 래퍼 → `workflow.scripted.*`, 실제 codex/claude 바이너리·`OPENAI_API_KEY` 없음, 한도 200/5000(비용 0). 절차 [DEPLOY](DEPLOY.md). 2026-09-21 `https://runloom.duckdns.org` 에 main(phase 5, 스키마 2) 배포·심사자 흐름 완주([VERIFICATION_LOG](VERIFICATION_LOG.md)). **심사 기간(~10-05) 동결 — phase 6 는 올리지 않는다** |
| 검증 | `python3 -m pytest -q` 1545 passed + 29 skipped(e2e), `ruff` 통과. e2e 는 `WORKFLOW_E2E=1 python3 -m pytest tests/e2e -q` 로 29 passed(약 93초, 대본 스택 — 기존 22 + 세 번째 종류 절 test_22~28) — [VERIFICATION_LOG](VERIFICATION_LOG.md) 2026-09-22 절 |
| 실연동 증거 | 실제 Codex CLI 로 B 1회(2026-09-20, [VERIFICATION_LOG](VERIFICATION_LOG.md) Step 15)와 실제 gpt-4.1 진단 평가([DIAG_EVAL](DIAG_EVAL.md))가 따로 있다. 실제 모델 + 실제 Codex 로 A → B 를 한 번에 완료한 기록은 없다. 실제 Claude Code 실연동은 없다(phase 4 의 남은 step 이 5-scripted-demo 로 대체되며 빠짐). 세 번째 종류(`review`, 읽기 전용 `LocalTarget`)도 대본 e2e 뿐 — 실제 Codex `--sandbox read-only` 가 git 저장소가 아닌 인계 디렉터리에서 도는 동작은 미확인 |
| 남은 것 | 실제 Claude 로 `review` 종류 1회 실연동, n8n 입구 phase(업무 `callback_url` + `TaskSource` n8n — 별도 ADR), 완료 시 새 업무 생성 규칙(ADR-0009 트레이드오프), 범용 API 에이전트 계약(지금 API 는 `diagnosis` 만), 실제 GitHub/Jira API 연동(지금은 `adapters/task_source_fixtures/` fixture 뿐), 셀프호스트 1인용 패키징(ADR-0006 Mac 구성은 코드로 남아 있으나 설치 절차·문서 없음), A2A. 사용량 한도 대기는 phase 3(심사 이후) |
| 로컬 산출물(커밋 안 됨) | `.env`(비밀값, gitignore), `data/`(sqlite·산출물·평가 workdir), `../demo-report-repo`(B 가 수정하는 데모 저장소, `scripts/scaffold_demo_repo.py` 로 재생성 가능) |

### 재개 방법 — 하네스

```bash
cd /Users/kje/00_Workspace/01_Coding/project/workflow
python3 scripts/execute.py 6-typed-handoff --engine claude          # 완료된 step 은 건너뛴다. 새 step 을 추가하면 이어서 돈다
python3 scripts/execute.py 3-limit-wait --engine claude             # 심사 이후 (ADR-0007). 실행 전 step 파일을 사용자가 검토·승인
python3 scripts/local_stack.py --scripted                           # 로컬에서 공개 데모와 같은 대본 스택 5-프로세스
WORKFLOW_E2E=1 python3 -m pytest tests/e2e -q                       # 대본 e2e 29건 (약 1분 30초)
```

- 새 phase 는 `phases/{task-name}/index.json` + `step{N}.md` 를 만들고 같은 명령으로 돈다. 워크플로우 전체는 `.claude/commands/harness.md`.
- `--engine claude` 를 쓴다(2026-09-20 기준 Codex 사용량 소진). `claude -p` 세션 한도(429 "session limit")로 step 이 3회 실패하면 코드 문제가 아니다. `index.json` 의 그 step 을 `pending` 으로 되돌리고 `error_message` 를 지운 뒤 같은 명령으로 재개한다.
- `.env`(`OPENAI_API_KEY`·단가·`DIAG_EVAL_BUDGET_USD`)는 실제 모델 평가(`scripts/diag_eval.py`)에만 필요하다. 공개 데모·e2e·하네스 step 은 키 없이 돈다. 키 값은 채팅에 붙이지 않는다.
- step 파일은 사용자가 검토·승인했다 (0-mvp 는 실행 전 승인, 1·2·4·5 는 초안 제시 후 "진행해").

주의: 하네스가 도는 동안 같은 작업 트리에서 다른 Claude Code 세션의 Stop 훅(`verify.sh`)이 검증 실패를 낼 수 있다. 실패가 하네스의 미완성 파일 때문이면 기다리고, 커밋된 코드의 실제 결함이면(0-mvp step 7 의 시각 의존 테스트가 그 예) 테스트 파일만 따로 고쳐 커밋한다.

리스크 (사용자에게 알린 것): 공개 데모는 대본이라 모델·구독 사용량을 쓰지 않는다. 셀프호스트로 실제 Codex/Claude 를 돌리면 연결 프로그램이 띄우는 도구가 하네스 엔진과 같은 구독 사용량을 쓰고, 한도 도달은 심사 이후 phase 3 전까지 `실패` 로 기록된다.

## 진단 모델 — 지금까지의 판단 (2026-09-20)

사용자의 질문 "gpt-4.1-mini 는 사내 에이전트 역할인데 시나리오대로 돌게 하면 되지 않나" 에 대한 합의:

- **정해진 답을 재생하는 fake 로 시연하지 않는다.** PRD "소수의 자료로 실제 조회·진단을 수행하며 고정된 답변 재생으로 대체하지 않는다". `DIAG_MODEL=fake` 는 테스트 전용이며 provenance 에 `fake-fixture-script` 로 남는다.
- 대신 **우리 쪽(도구 반환·계약 문법·프롬프트)을 먼저 고치고 같은 하네스로 재평가**한다 → phase 1-diag-fix. 목표 증상(문법 거부 5→0, 줄 범위 초과 5→0)은 사라졌다.
- 남은 실패는 모델이 읽은 사실과 어긋나는 결론을 내는 행동이다: "읽지 못했으면 보류" 규칙 0회 준수, 읽지 않은 근거 인용, `list_runs.before` 에 자료의 2026 대신 2023/2024 를 씀(7/15). 사용자가 `gpt-4.1` 비교를 결정했다 → phase 2-model-compare.
- 비정상 사례에서 검증기가 막아 `확인 필요` 로 남는 것은 "검증 없이는 인계하지 않는다" 는 제품 주장이라 시연에 해가 되지 않는다. 문제는 정상 사례의 자동 인계가 안정적이지 않다는 점이다.

## 새 세션 시작

1. 루트 AGENTS.md(스택·규칙·명령어 채움)와 [ADR 목록](adr/0000-principles.md)을 읽는다. ADR-0000~0009 가 확정 사항이다(0003 은 gpt-4.1 로 확정, 0007 은 심사 이후 적용).
2. [PRD](PRD.md)·[ARCHITECTURE](ARCHITECTURE.md)·[CONTRACT](CONTRACT.md)·[GLOSSARY](GLOSSARY.md)를 읽는다. "2026-09-20 확정"으로 표시한 절은 재질문하지 않는다.
3. 위 "지금 상태" 표와 "재개 방법" 을 본다. 사용자가 재개를 지시하면 "재개 방법" 의 명령으로 하네스를 돌린다. 사용자에게 제품 방향·시연 사례·진단 모델·공개 데모 방식(대본)을 다시 고르도록 요구하지 않는다.

구현은 사용자가 "진행해" 로 지시했을 때만 하네스로 실행한다. 실배포·유료 호출·모델 교체는 별도 지시 없이 시작하지 않는다. 결정 질문은 압축 용어 대신 "누가 무엇을 하면 어떤 일이 생기는지" 장면으로 풀어 설명한 뒤 2~3개씩 묻는다.

## 제품의 중심 — 좁혀 해석하지 말 것

개인·팀·사내의 기능과 정보 접근 범위가 다른 기존 에이전트를 쉽게 등록하고, 업무에 맞게 연결해 자동 실행한다. 원래 문제는 A가 끝나 B를 시작할 수 있어도 사람이 전달·실행할 때까지 기다려 후속 업무가 밀리는 착수 대기다.

[ADR-0009](adr/0009-registered-kinds-and-succession-rules.md) 한 줄: 흐름을 그리지 않는다 — 종류·규칙을 등록하면 흐른다. 업무 종류는 입출력 봉투(받는 산출물·내는 산출물·결과값)이고 후속 규칙은 "이 결과값 다음에 무엇을 넘겨 어떤 종류를 시작하는가" 한 줄이며, 흐름은 규칙 표를 반복 적용한 결과다. 후속 착수는 선행 결과 + 판정 통과 + outcome 일치이지 사람 승인이 아니다. 새 단계를 붙일 때 `composition.py`·`worker.py` 에 종류 이름 분기를 늘리지 않고 규칙 행으로 되는지가 설계 기준이다.

개인 Codex·Claude Code 두 개 연결이나 코딩 자동화만으로 제품을 한정하지 않는다. 등록할 때 연결 가능성뿐 아니라 수행 가능한 업무·접근 가능한 정보·사용 권한을 파악하는 경험이 중요하다. 제품 방향과 공모전 시나리오는 둘 중 하나를 고르는 선택지가 아니다.

n8n·A2A는 구현 후보이며 자동 실행의 필수 조건이 아니다. Jira·Devin·Paperclip 등과 기능이 겹치므로 ‘외부 에이전트 연결이 시장에 없다’고 주장하지 않는다. 기존 환경의 등록·구성 부담 감소는 검증할 차별성 가설이다. 비교 근거는 제품 개요에 있다.

## 이미 합의한 내용의 위치

[PRD](PRD.md)에 아래 결정과 세부 한계를 기록했다. 다음 세션에서 같은 동의를 반복해서 묻지 않는다.

- 서비스 안의 간단한 업무 등록.
- 에이전트 직접 선택과 업무 내용 기반 자동 선택 모두 지원.
- 직접 실행·자동 실행, 자동 완료·검토 후 완료 모두 지원.
- 완료 기준은 시스템이 제안하고 사용자가 수정. 판단 불가면 확인 필요.
- 자동 선택 후 직접 실행은 사용자 조작을 기다리고, 자동 실행은 조건 충족 시 별도 승인 없이 시작. 대상 미결정이면 보류·질문.
- 등록 정보는 연결한 범위에서 자동 파악·제안하고 소유자가 확인·수정·보충.
- 로컬 에이전트는 현재 컴퓨터의 연결 프로그램 + 실행 도구·작업 폴더로 등록. 기존 설정을 활용한 새 실행이며 열린 대화 승계가 아니다.
- Git 코드 업무에 worktree 활용. 선행 코드 결과 버전을 후속 업무의 별도 worktree로 전달하고, 기준 브랜치 병합은 사용자 확인 후 진행. 정리·버전 보존 방식 등은 설계 전.

로컬 폴더 등록 규칙을 API 에이전트에도 강제하지 않는다. 첫 로컬 도구는 Codex CLI로 확정했다([ADR-0001](adr/0001-first-local-agent-codex.md)). Claude Code는 후속 어댑터다.

## 확정한 연결 대상과 시연

로컬 Codex CLI([ADR-0001](adr/0001-first-local-agent-codex.md)) + 사내 운영 진단 API 에이전트. 사용자는 실제 사내 에이전트가 없으므로 API 역할을 별도 데모 서비스로 재현하는 데 동의했다. 이전의 ‘사내 API는 첫 범위에서 제외’는 최신 결정이 아니다.

시연은 일일 보고서 자동화에서 외부 응답 형식이 바뀌어 변환 코드가 실패한 사건이다. 사내 에이전트가 실행 이력·로그·운영 문서로 진단하고, 로컬 개발 에이전트가 근거를 받아 실제 데모 저장소에서 재현·수정·테스트한다. A는 코드 수정이 아니므로 진단 결과를 B에 전달하고 worktree는 B의 코드 작업에 사용한다.

[미리디 AX Engineer – Infra 공고](https://www.miridih.com/ko/o/220034)의 업무를 참고한 사례다. 사용자는 이 직무에 지원하고 싶고 여기서 n8n을 알게 됐다고 설명했다. 공식 원문은 2026-09-20 curl로 읽었다(web 도구는 403). 공고에 내부 자동화 운영·로그 조사·Agent 서비스 개선·런북 정리가 있지만, 위 에이전트 구성이나 장애 사례를 해당 기업이 실제 사용한다고 확인한 것은 아니다.

진단 API의 범위 제안은 실행 ID·조사 요청 입력, 실행 이력·로그·문서 조회, 원인·근거·수정 대상·기대 동작 출력이다. 사용자는 이 범위가 괜찮다고 했으며 이후 MCP·RAG·OpenArchive를 논의했다. 상세 스키마·프로토콜은 미결이다.

## 마지막 논의 — OpenArchive와 범위 관리

사용자는 다른 대회에서 개발 중인 [OpenArchive](https://github.com/jeongeundev/OpenArchive)를 재사용하거나 사내 에이전트 생성 기능을 추가하는 아이디어를 제시했다. 이후 ‘필수가 아니면 사용하지 않아도 되고 후속 기능으로 두어도 된다’고 밝혔다.

Assistant는 첫 MVP에서는 OpenArchive 연동을 보류하고 작은 JSON 실행 기록·로그와 Markdown 운영 문서, 조회 도구로 진단 에이전트를 구성하자고 제안했다. 사용자는 그 뒤 컨텍스트 사용량 때문에 새 세션 인계를 요청했다. 미사용을 허용한 것은 사용자 발언이고, 최소 데이터 구성의 상세는 assistant 제안임을 구분한다.

MCP는 도구 연결, RAG는 검색 근거를 이용한 생성 방식이다. 개인 에이전트에 MCP로 사내 자료를 연결하는 대안도 인정한다. ‘문서가 사내에 있으니 반드시 별도 에이전트가 필요’, ‘사내 에이전트/RAG니까 외부 유출이 없음’이라고 설명하지 않는다. 이번 사내 역할은 문서 검색만이 아니라 로그·이력·절차를 종합하는 진단 업무다.

### OpenArchive 조사 결과 — 필요할 때만 참고

확인 기준: main의 `b544633ac1bf090ec07f8899eb1a7f2c493c6ff4`. GitHub API와 raw 소스를 읽었으며 설치·테스트·변경하지 않았다.

- README, 검색·권한·워커·문서 진단·MCP·인증 코드를 확인했다.
- 문서 임베딩·검색 기반과 발췌·출처·기준 버전 반환을 구현했다. 답변 생성은 연결한 에이전트 담당이다.
- [MCP 서버](https://github.com/jeongeundev/OpenArchive/blob/b544633ac1bf090ec07f8899eb1a7f2c493c6ff4/backend/mcp_server/server.py): stdio, search/get/list/create 문서 도구. MCP_USER_ID 환경으로 사용자 범위 설정.
- REST 위임 토큰이 있고 문서 열람 규칙은 public 또는 owner다. 세밀한 팀·부서 권한까지 갖춘 것으로 과장하지 않는다.
- diagnostics는 고아·중복·깨진 링크 등 문서 진단이며 운영 장애 진단이 아니다.
- 추후 활용한다면 독립 문서 기반으로 유지하고 운영 진단 에이전트가 이를 사용한다는 제안이다. 지금 범용 에이전트 생성 플랫폼으로 확대하지 않는다.

## 2026-09-20 확정 사항 — ADR과 문서 위치

| 결정 | 위치 |
|---|---|
| 첫 로컬 도구 Codex CLI, Claude Code는 후속 | [ADR-0001](adr/0001-first-local-agent-codex.md) |
| Python 3.13 + FastAPI + Jinja2 + SQLite + Pydantic v2 + HTTPX + pytest/ruff | [ADR-0002](adr/0002-server-stack-python-fastapi-sqlite.md), `pyproject.toml` |
| 진단 모델 OpenAI gpt-4.1-mini — 작업 가정. 키·예산 확인됨, 평가 3회 미달, gpt-4.1 비교 예정 | [ADR-0003](adr/0003-diagnosis-model-openai-gpt41-mini.md) |
| 중앙 서비스는 규칙 기반, LLM 미사용. 능력 코드 2개 명시 비교 | [ADR-0004](adr/0004-central-service-rule-based-no-llm.md), PRD 2절 |
| 기본값: 자동 선택 / 선행 있으면 자동 실행·없으면 직접 / 검토 후 완료. 후보 0·2+면 확인 필요. 검토 동작 승인·수정 요청·종료 | PRD 2·3·4절 |
| 익명 세션 워크스페이스, 운영자 토큰, 병합은 운영자 전용 | [ADR-0005](adr/0005-access-model-anonymous-session-operator-token.md), ARCHITECTURE 인증 절 |
| VM + 도메인 + Caddy, 연결 프로그램은 Mac(꺼질 수 있음), OpenAI 총액 US$30 | [ADR-0006](adr/0006-deployment-vm-caddy-mac-connector.md), ARCHITECTURE 배포·예산 절 |
| 공모전 제약: 무로그인 웹 데모, 심사 9/21~10/5 상시 접속 | [ADR-0000](adr/0000-principles.md) |
| 계약 v1 완전 예시(요청·이벤트·결과·오류·선택 기록) | [CONTRACT.md](CONTRACT.md) |
| 테스트 배치 `tests/`가 `src/` 미러, ruff 훅 추가 | AGENTS.md, `scripts/hooks/` (test_hooks.py 88 passed) |

미결·확인 필요:

- VM 제공자·도메인 이름은 아직 지정하지 않았다. 배포 설정·런북은 준비됐다([DEPLOY](DEPLOY.md)).
- OpenAI API 키·계정 사용 가능·단가·예산은 확인됐다(`.env`). ADR-0003 확정은 gpt-4.1 비교 결과를 보고 사용자가 정한다.
- Mac 오프라인 대비 "운영자 예시 실행 공개"는 ARCHITECTURE 배포 절에 제안으로만 적었다. 사용자 확인 전.
- 공모전 제출 마감 2026-09-20 은 지났다. 심사 9/21~10/5 상시 접속을 위해 배포가 필요하지만 사용자 판단 전이다.
- 제품 코드 결함 1건 발견·미수정: `worker/loop.py` 가 계약 거부된 턴의 호출·토큰을 사용량에 안 더함 → 2-model-compare step 1 에서 고친다.

## 다음 세션에서 할 일

1. 실제 Claude 로 `review` 종류 1회 실연동 — 사용량(구독 한도) 확인 후, 사람이 지시할 때만. 로컬 스택(`scripts/local_stack.py`, `--scripted` 없이)에서 `/kinds` 로 `review` 와 규칙 `code_change --[ready_for_review]--> review` 를 등록하고 A → B → C 를 돌려 `generic_result`·`readonly_violation` 없음·저장소 불변을 [VERIFICATION_LOG](VERIFICATION_LOG.md) 에 기록한다. ARCHITECTURE "검증 순서" 6번을 갱신한다.
2. n8n 입구 phase — 업무에 `callback_url`, `TaskSource` 에 n8n, 3~4 노드 워크플로우 예시. 별도 ADR 로 결정한 뒤 `phases/` 에 step 을 만든다. n8n 은 업무가 들어오는 입구·나가는 출구로만 쓴다(ADR-0009 참고 절).
3. 완료 시 새 업무를 **생성**하는 규칙(대상·범위를 선행 결과에서 파생) — ADR-0009 트레이드오프. 지금은 미리 등록된 업무 사이를 잇는 것만 한다.
4. 심사 이후(2026-10-05 뒤) VM 배포 — `feat-6-typed-handoff` 를 main 에 병합·푸시하고 [DEPLOY](DEPLOY.md) 7b 대로 `WORKFLOW_RESET_DB=1 update-vm.sh`(스키마 2 → 3) 후 seed·connect·register 를 다시 한다. 그 전에는 VM 에서 `update-vm.sh` 를 돌리지 않는다.
5. 심사 이후: `phases/3-limit-wait`(ADR-0007), 그 다음은 "지금 상태" 표의 "남은 것". ADR 파일은 사용자 확정 후에만 고친다.
6. 인계 문서를 갱신할 때 "지금 상태" 표를 먼저 고친다.

### 이전 세션의 기술 설계 진행 내용 — 확정 전 기록 (위 표가 우선)

- 확정: Codex 우선([ADR-0001](adr/0001-first-local-agent-codex.md)), Python/FastAPI/Jinja2/SQLite 스택([ADR-0002](adr/0002-server-stack-python-fastapi-sqlite.md)). 중앙 웹/API + 실행 조정 워커, 로컬 프로그램의 작업 조회, 별도 비동기 진단 API 구성은 그 안에 포함된다.
- 스택 제안: Python 3.13 계열, FastAPI/Uvicorn, Jinja2·CSS·소량의 JavaScript, Pydantic v2, HTTPX, sqlite3, pytest/Ruff. 실제 패키지 설치·버전 호환성 검증은 하지 않았다. AGENTS.md 기술 스택과 훅 명령은 아직 변경하지 않았다.
- 진단 제공자는 OpenAI Responses API, 첫 평가 후보는 `gpt-4.1-mini-2025-04-14`. 공식 문서에서 기능·스냅샷을 확인했으며 최신 모델이라는 주장은 아니다. API 자격 증명·계정 접근·예산은 미확인, 유료 호출 없음. 정상·누락·충돌·다른 실패 원인 5개 사례를 각각 3회 평가하는 안을 작성했다.
- 모델은 주장·근거 참조를 작성하고 진단 서비스가 조회 원문으로 첨부를 조립한다. 중앙 서비스의 업무 기반 자동 제안 방식, 인증·배포 환경과 구체적 실행 예산은 미결이다.
- 로컬 `codex-cli 0.155.1`과 `Claude Code 2.1.278`의 버전·도움말을 확인했다. Codex 공식 문서의 JSONL·결과 스키마·설정 탐색도 확인했다. 인증·모델 실행·worktree 설정 재사용은 미검증이다.
- 중복 전달은 같은 Execution ID로 처리하고, 로컬 시작 여부 불명은 재실행 대신 확인 필요로 둔다. B 결과는 전용 브랜치 로컬 커밋 보존을 제안하며 기준 브랜치 병합과 구분한다.
- 진단 자동 완료는 구조화된 변경 계약과 diagnosis 객체로 데모의 응답 경로 변경만 검증하는 안이다. PRD에 diagnosis·provenance·첨부 계약을 반영했다. 검증 실험은 남았으며 범용 자연어 진단 검증으로 소개하지 않는다.
- 중복 방지는 활성 실행 잠금과 `(task_id, start_key)` 유일성을 함께 사용한다. unknown·검토 대기는 잠금을 유지하고 명시적 재시도만 새 시작 키를 만든다. 완료된 뒤 늦게 온 선행 완료 이벤트도 새 실행을 만들지 않는다.

제품 설계 선택은 구체적인 제안과 이유를 제시해 한 번에 하나씩 논의한다. 사용자가 동의하면 반영하고 다음 결정까지 안내한다. 모든 사소한 구현 선택을 동의 질문으로 만들지 않는다. 이미 정한 시연·등록·선택·실행 방식을 반복 질문하지 않는다.

공모전 제약(무로그인 웹 데모, 설치형 불가, 마감 2026-09-20, 심사 2026-09-21~10-05)은 2026-09-20 사용자가 적용을 확인했고 [ADR-0000](adr/0000-principles.md)에 기록했다. 보관 초안의 GitHub 우선·되묻기 필수 등은 자동 적용하지 않는다.

## 문서와 작업 상태

- 현행 문서: 제품 개요, PRD, ARCHITECTURE, CONTRACT, GLOSSARY, [UI_GUIDE](UI_GUIDE.md), ADR 0000~0009, [VERIFICATION_LOG](VERIFICATION_LOG.md), [DEPLOY](DEPLOY.md), [DIAG_EVAL](DIAG_EVAL.md)(+ 이전 평가 `DIAG_EVAL_2026-09-20_prompt-v1.md`), 이 handoff, [문서 안내](README.md).
- [이전 원문 보관](archive/2026-09-19-before-agent-registration/README.md)은 이력이며 현행 요구사항이 아니다.
- 작업 트리는 HEAD 와 같다(`.env`·`data/` 는 gitignore). 이 세션에서 한 것: 0-mvp step 1~17 하네스 실행, step 7 시각 의존 테스트 수정(`d01773b`), 1-diag-fix 계획·실행, 2-model-compare 계획 커밋, 이 문서 갱신. 푸시하지 않았다.
- 검증은 `python3 -m pytest -q`(1018 passed, 11 skipped) 와 `python3 -m ruff check .` 통과. 실제 외부 호출 검증은 Codex 1회(step 15)·OpenAI 평가 3회(step 17, 1-diag-fix step 3) 뿐이며 배포·심사 환경 검증은 하지 않았다.

## 권장 스킬

- `example-skills:doc-coauthoring`: PRD·데이터 시나리오 공동 작성. 이미 정한 사항을 재질문하는 포괄 인터뷰로 되돌리지 않는다.
- `product-management:feature-spec`: 시나리오별 수용 기준 구체화 시.
- `openai-docs`: Codex 실행·설정 활용을 실제 확인할 때.
- `engineering:system-design`: 기술 설계 단계에서 필요할 때.
- `handoff`: 다음 세션 인계를 다시 갱신할 때. 사용자 지정 인계 파일은 docs/CURRENT_HANDOFF.md다.
