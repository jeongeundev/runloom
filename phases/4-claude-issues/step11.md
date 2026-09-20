# Step 11: deploy-docs

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/AGENTS.md`
- `/docs/README.md` — 문서 색인
- `/docs/DEPLOY.md` 전체 — 5·6 절(seed·register)·7 절(예시 실행)·7b(코드 갱신)·9 절(점검)·10 절(한계)
- `/deploy/env/central.env.example`, `/deploy/launchd/com.workflow.connector.plist`, `/deploy/update-vm.sh`
- `/docs/adr/0001-first-local-agent-codex.md`, `/docs/adr/0000-principles.md`(ADR 목록)
- `/docs/PRD.md` — "업무 입력" 행(23행)·74행·460행, 실행 대상 행(22행)
- `/docs/ARCHITECTURE.md` — 첫 로컬 도구 표(12행), 외부 의존 표
- `/docs/GLOSSARY.md`, `/docs/UI_GUIDE.md`, `/docs/CURRENT_HANDOFF.md`
- `/phases/4-claude-issues/index.json` — step 0~10 summary (무엇이 실제로 만들어졌는지 여기서 읽는다)
- `/tests/test_deploy_files.py` — 런북 문자열이 스크립트 상수와 일치하는지 검사한다

이전 step에서 만들어진 코드를 꼼꼼히 읽고, 설계 의도를 이해한 뒤 작업하라. 새 기능은 테스트를 먼저 작성한다 (AGENTS.md TDD, `tests/` 는 `src/` 미러 배치).

## 배경

phase 4 의 결과를 문서·배포 설정에 반영한다. 코드는 고치지 않는다 (문서·설정·테스트만). 실제 VM 갱신과 Mac 재등록은 사용자가 지시할 때 `update-vm.sh` 로 한다.

## 작업

### ADR

- `docs/adr/0001-first-local-agent-codex.md` 끝에 "**후속(2026-09-2x)**: phase 4 에서 Claude Code 어댑터를 추가했다 (`connector/claude.py`, 공통 흐름 `local_tool.py`). 결정 자체(첫 도구 Codex)는 유효하다." 한 문단.
- `docs/adr/0009-session-agent-registration.md` (신규): 연결(소유자가 한 번: 연결 프로그램·API 접속 정보)과 등록(워크스페이스마다: 발견 정보 확인·이름·범위)을 분리한 결정, 이유(심사자가 빈 상태에서 등록을 겪어야 하고 실서비스의 로그인 사용자 워크스페이스와 같은 화면), 트레이드오프(세션이 지워지면 등록도 사라짐, 카탈로그는 운영자가 연결한 것뿐). ADR 목록에 추가.
- `docs/adr/0008-task-source-github-issues.md` (신규): 업무 입력의 첫 외부 출처를 GitHub Issues(읽기 전용, 운영자 저장소 하나, 라벨로 능력 미리 채움)로 정한 결정·이유(Jira 보다 먼저인 이유: 토큰만 있으면 즉시, 저장소 자체로 시연)·트레이드오프(되쓰기 없음, 저장소 하나, 한도). `0000-principles.md` 의 ADR 목록에 추가.

### PRD / ARCHITECTURE / GLOSSARY / UI_GUIDE

- PRD 22행 실행 대상: "로컬 Codex CLI · Claude Code CLI + 사내 진단 API". 23·74행 업무 입력: "서비스 내 등록 + GitHub Issues 가져오기(읽기). Jira 는 후속". 460행 보류 목록에서 Jira 문구 정리.
- ARCHITECTURE 12행 표: Claude Code 를 "후속 후보" 에서 "phase 4 추가" 로. 외부 의존 표에 GitHub 행(step 7 에서 넣었으면 확인만).
- GLOSSARY: `source`, `가져오기(import)`, `연결(connection) vs 등록(registration)`, `local_tool` 공통 흐름, `fake claude` 추가. 금지 표현 확인.
- UI_GUIDE: 에이전트 등록 화면(카탈로그·발견 정보·확인)·가져오기 화면·안내 블록·`successor_agent_id` 설명 추가. 홈 빈 상태 순서(에이전트 0개 → 등록 CTA).

### DEPLOY.md·deploy/

- 5 절 seed 출력 `agents=agent-claude-mac,agent-codex-mac,agent-ops-demo`. 6 절에 두 번째 `register --tool claude --id local-demo-report-claude …` 명령과 `claude --version` 확인. launchd 재시작 절차(코드 갱신 뒤 `launchctl unload/load`).
- 3 절 env 표에 `GITHUB_ISSUES_REPO=jeongeundev/runloom`, `GITHUB_TOKEN`(읽기 전용 fine-grained, 이슈 read) 추가. 서비스 시작 시 DB 마이그레이션(v1→v2) 이 자동으로 되며 시작 전에 8 절 백업을 한 번 돌리라는 문장.
- 7 절 예시 실행을 새 흐름으로 다시 쓴다: 빈 세션 → 「에이전트 등록」 3개 → 「GitHub Issues 에서 가져오기」 1건 → 시연 A+B(담당 에이전트 기본 Codex) → A 실행 → B 자동 착수 → 승인. 10 절 한계에 Claude 구독 한도(심사자가 Claude 를 고르면 사용자 구독을 씀), GitHub 한도(토큰 없으면 60회/시).
- `deploy/update-vm.sh` 는 그대로 (마이그레이션은 서비스가 한다). 단 스크립트 주석에 "스키마가 바뀌는 갱신은 먼저 `systemctl start workflow-backup.service`" 를 추가.

### 시연 이슈

- `jeongeundev/runloom` 에 이슈 3개를 `gh issue create` 로 만든다 (제목·본문은 가상 시나리오, 실제 장애 아님을 본문 첫 줄에 명시):
  1. `runloom:diagnose` 라벨 — "일일 보고서 09:00 실행 실패 조사"
  2. `runloom:code-change` 라벨 — "보고서 변환기가 두 응답 형식을 모두 처리하도록 수정"
  3. 라벨 없음 — "실패 알림에 근거 링크 포함" (능력 미선택 → 심사자가 폼에서 고르는 사례)
  라벨 2개도 `gh label create` 로 만든다. 만든 이슈 번호를 DEPLOY 7 절과 handoff 에 적는다.

### `docs/CURRENT_HANDOFF.md`

- "지금 상태" 표를 phase 4 완료 기준으로 다시 쓴다: 배포 URL·저장소·에이전트 3개·가져오기·다음 할 일(VM 갱신 명령, Mac 재등록 명령, phase 5 후보: A2A·계정·알림·Jira·셀프호스트 패키징). 이전 세션 기술 설계 절은 줄인다.
- `docs/README.md` 색인에 ADR-0008·0009.

### 테스트

- `tests/test_deploy_files.py`: 런북의 두 번째 register 명령이 `seed_demo.LOCAL_REGISTRATION_ID_CLAUDE` 와 일치, env 예시 키 = `ENV_KEYS`(step 7 에서 이미), README 색인에 ADR-0008·0009 링크 존재, ADR 목록에 둘 다.

## Acceptance Criteria

```bash
python3 -m pytest tests/test_deploy_files.py -q
python3 -m pytest -q
python3 -m ruff check .
gh issue list --repo jeongeundev/runloom --state open --json number,labels -q '.[] | "\(.number) \([.labels[].name] | join(","))"'   # 3건, 라벨 2종
grep -n "0008\|0009" docs/adr/0000-principles.md docs/README.md
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

- `src/` 를 고치지 마라. 이유: 이 step 은 문서·설정·시연 데이터만. 결함을 발견하면 index.json summary 에 적고 멈춘다.
- VM 에 접속하거나 서비스를 재시작하지 마라. 이유: 배포 시점은 사용자가 정한다 (Mac 연결 프로그램 재등록과 같이 해야 한다).
- 이슈 본문에 실제 회사·실제 장애를 암시하는 표현을 쓰지 마라. 이유: PRD — 가상 데모 자료임을 명시한다.
- 기존 테스트를 깨뜨리지 마라
