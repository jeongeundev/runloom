# 현재 인계 — 이종 에이전트 등록과 업무 자동 실행

갱신일: 2026-09-20 (구현 시작 후 중단)
상태: 문서 7항목 완료. 구현 계획 `phases/0-mvp/` step 0~17 작성·커밋. 하네스를 `--engine claude` 로 실행해 step 0 완료 후 step 1 진행 중 사용자 요청으로 중단. 브랜치 `feat-0-mvp`, 푸시 안 함.

## 지금 상태 — 새 세션이 먼저 볼 것

| 항목 | 상태 |
|---|---|
| 브랜치 | `feat-0-mvp` (main 에서 분기. main 은 그대로) |
| 커밋 | `docs:` 설계 문서 → `fix(harness)` 시간 초과·`__main__.py` 예외 → `chore(harness)` step 계획 → `feat(0-mvp): step 0` + `chore` |
| step 0 project-setup | 완료. `src/`·`tests/` 뼈대, editable 설치, `tests/test_packages.py` 의존 방향 회귀 테스트. pytest 98 passed |
| step 1 contracts | 시작했다가 중단. 미완성분은 `git stash list` 의 `stash@{0}` (v1.py 417줄, test_v1.py 393줄). 작업 트리는 HEAD 와 같다 |
| step 2~17 | pending. 파일은 `phases/0-mvp/step{N}.md` |
| 사용자 검토 | **step 파일 18개를 사용자가 아직 검토하지 않았다.** 이전 세션이 승인 범위를 넘어 실행을 시작했고 사용자가 멈췄다. 실행 재개 전에 사용자에게 step 파일 검토 여부를 묻는다 |

재개 방법:

```bash
# (선택) step 1 진행분을 이어서 쓰려면
git stash pop
# 실행 — 반드시 claude 엔진 (Codex 사용량 소진)
python3 scripts/execute.py 0-mvp --engine claude
```

`git stash pop` 없이 돌리면 step 1 을 처음부터 다시 만든다. pop 하면 그 파일 위에서 이어 간다 (execute.py 는 작업 트리를 초기화하지 않는다). 하네스는 step 15(Codex 실연동)·17(OpenAI 키) 에서 blocked 로 멈출 수 있다 — 사유 해결 후 `index.json` 의 해당 step 을 `pending` 으로 되돌리고 같은 명령으로 재개한다.

주의: 하네스가 도는 동안 같은 작업 트리에서 다른 Claude Code 세션의 Stop 훅(`verify.sh`)이 미완성 파일을 보고 검증 실패를 낸다. 하네스 실행 중에는 그 세션에서 소스를 만지지 않는다.

리스크 (사용자에게 알린 것): 제품의 B 실행은 연결 프로그램이 띄우는 Codex 이고 하네스와 같은 사용량을 쓴다. 사용량이 거의 없으면 심사 기간 B 가 `실패`로 표시된다. Claude Code 어댑터(`src/workflow/connector/` 경계에 추가)가 대안이며 step 15 결과를 보고 결정하기로 했다.

## 새 세션 시작

1. 루트 AGENTS.md(스택·규칙·명령어 채움)와 [ADR 목록](adr/0000-principles.md)을 읽는다. ADR-0000~0006이 확정 사항이며 0003만 작업 가정이다.
2. [PRD](PRD.md)·[ARCHITECTURE](ARCHITECTURE.md)·[CONTRACT](CONTRACT.md)·[GLOSSARY](GLOSSARY.md)를 읽는다. "2026-09-20 확정"으로 표시한 절은 재질문하지 않는다.
3. 구현 계획은 `phases/0-mvp/` 에 있다. 위 "지금 상태" 표를 보고 사용자에게 step 파일 검토 여부와 재개 여부를 확인한 뒤 실행한다. 사용자에게 제품 방향이나 시연 사례를 다시 고르도록 요구하지 않는다.

사용자는 문서 작성을 먼저 끝내고 구현으로 넘어가길 원한다. 별도 구현 요청 전에는 제품 코드·실연동·배포를 시작하지 않는다. 결정 질문은 압축 용어 대신 "누가 무엇을 하면 어떤 일이 생기는지" 장면으로 풀어 설명한 뒤 2~3개씩 묻는다.

## 제품의 중심 — 좁혀 해석하지 말 것

개인·팀·사내의 기능과 정보 접근 범위가 다른 기존 에이전트를 쉽게 등록하고, 업무에 맞게 연결해 자동 실행한다. 원래 문제는 A가 끝나 B를 시작할 수 있어도 사람이 전달·실행할 때까지 기다려 후속 업무가 밀리는 착수 대기다.

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
| 진단 모델 OpenAI gpt-4.1-mini — 작업 가정, 키·예산 확인 전 blocked | [ADR-0003](adr/0003-diagnosis-model-openai-gpt41-mini.md) |
| 중앙 서비스는 규칙 기반, LLM 미사용. 능력 코드 2개 명시 비교 | [ADR-0004](adr/0004-central-service-rule-based-no-llm.md), PRD 2절 |
| 기본값: 자동 선택 / 선행 있으면 자동 실행·없으면 직접 / 검토 후 완료. 후보 0·2+면 확인 필요. 검토 동작 승인·수정 요청·종료 | PRD 2·3·4절 |
| 익명 세션 워크스페이스, 운영자 토큰, 병합은 운영자 전용 | [ADR-0005](adr/0005-access-model-anonymous-session-operator-token.md), ARCHITECTURE 인증 절 |
| VM + 도메인 + Caddy, 연결 프로그램은 Mac(꺼질 수 있음), OpenAI 총액 US$30 | [ADR-0006](adr/0006-deployment-vm-caddy-mac-connector.md), ARCHITECTURE 배포·예산 절 |
| 공모전 제약: 무로그인 웹 데모, 심사 9/21~10/5 상시 접속 | [ADR-0000](adr/0000-principles.md) |
| 계약 v1 완전 예시(요청·이벤트·결과·오류·선택 기록) | [CONTRACT.md](CONTRACT.md) |
| 테스트 배치 `tests/`가 `src/` 미러, ruff 훅 추가 | AGENTS.md, `scripts/hooks/` (test_hooks.py 88 passed) |

미결·확인 필요:

- VM 제공자·도메인 이름은 아직 지정하지 않았다.
- OpenAI API 키·계정 사용 가능 여부·단가 확인 전. 진단 실연동 step은 blocked로 시작한다.
- Mac 오프라인 대비 "운영자 예시 실행 공개"는 ARCHITECTURE 배포 절에 제안으로만 적었다. 사용자 확인 전.
- 공모전 제출 마감이 2026-09-20으로 오늘이다. 사용자는 "적용됨, 일정 그대로"를 선택했다. 문서 완료 후 구현 일정은 사용자 판단이다.

## 다음 세션에서 할 일

1. 사용자에게 `phases/0-mvp/step*.md` 검토 여부를 묻는다. 고칠 점이 있으면 해당 step 파일을 고친다 (아직 시작 안 한 step 은 실행 시점에 파일을 읽는다).
2. 사용자가 재개를 지시하면 `python3 scripts/execute.py 0-mvp --engine claude` 로 실행한다. step 1 진행분을 쓸지(`git stash pop`) 먼저 정한다.
3. 미결 3건(VM·도메인, OpenAI 키·단가, 예시 실행 공개)은 해당 step(16·17·8)에 도달할 때 확인한다.

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

- 현행 문서: 제품 개요, PRD, ARCHITECTURE v0.3, CONTRACT, GLOSSARY, [UI_GUIDE](UI_GUIDE.md)(2026-09-20 채움, 심사자 첫 방문 흐름·화면 목록·상태 표시 규칙), ADR 0000~0006, 이 handoff, [문서 안내](README.md).
- [이전 원문 보관](archive/2026-09-19-before-agent-registration/README.md)은 이력이며 현행 요구사항이 아니다.
- 작업 트리에 기존 코드·설정 변경이 많다. 이 세션에서는 문서·ADR·AGENTS.md·`pyproject.toml`·`scripts/hooks/{tdd-guard,verify}.sh`·`scripts/test_hooks.py`를 바꿨고 ruff `--fix`로 `scripts/execute.py`·`test_execute.py`의 미사용 import·빈 f-string을 정리했다. 커밋·푸시하지 않았다.
- 검증은 문서 로컬 링크, CONTRACT.md JSON 파싱, `python3 -m pytest -q`(88 passed), `python3 -m ruff check .` 통과다. 제품 코드·서비스 연결 검증을 수행한 것으로 설명하지 않는다.

## 권장 스킬

- `example-skills:doc-coauthoring`: PRD·데이터 시나리오 공동 작성. 이미 정한 사항을 재질문하는 포괄 인터뷰로 되돌리지 않는다.
- `product-management:feature-spec`: 시나리오별 수용 기준 구체화 시.
- `openai-docs`: Codex 실행·설정 활용을 실제 확인할 때.
- `engineering:system-design`: 기술 설계 단계에서 필요할 때.
- `handoff`: 다음 세션 인계를 다시 갱신할 때. 사용자 지정 인계 파일은 docs/CURRENT_HANDOFF.md다.
