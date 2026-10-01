# Step 0: triage-design — 결정·이름·스키마 v15·계약 고정 (문서만)

## 읽어야 할 파일

- AGENTS.md
- phases/19-triage/README.md (사용자 결정 3가지·계획 기본값 14가지·조사 결과 — 이 phase 의 기준)
- docs/product/REDESIGN_PLAN.md 3·5.4·6·11·13·15·16절
- docs/ARCHITECTURE.md 의 "업무와 단계 — phase 14"·"업무 화면 — phase 16"·"사람 사이 인계 — phase 17"·"Jira 소스 — phase 18" 절, phase 9 측정(설정 번호) 절, 계약 v1 절
- docs/adr/0009·0020·0021·0022·0023·0024, docs/GLOSSARY.md, docs/CONTRACT.md
- src/workflow/contracts/v1.py (`KindSpec`·내장 종류·`CodeReviewResult`·`GenericResult`·`SuccessorRule`·claim `supported_kinds`·러너 capabilities)
- src/workflow/domain/execution_policy.py, work_status.py, delegation.py, handoff_context.py, github_match.py, metrics.py, field_mapping.py
- src/workflow/adapters/db.py (v14 표·재생성 선례·`kinds`·`github_sources`·`agents`·`connectors`), src/workflow/adapters/repo.py (`register_local_agent`·`work_item_facts`·`set_work_status`·`hand_work_to_agent`·`create_followup_once`·`claim_execution`·config_revision 올리는 함수·`list_work_rows`)
- src/workflow/server/work_actions.py (`assign_work`·`open_stage`·`agent_candidates`·`start_stage`), stage_runs.py, owner_approval.py, task_cycle.py (`execution_request_text`·`origin`), worker.py (`tick`·판정·`_spawn_successors`·`_start_waiting_stages`), views.py (`work_panel_context`), web.py (`/connect`·`/work/{key}/…`), templates/_work_panel.html
- src/workflow/connector/adapter.py·client.py·local_tool.py (`run` 분기·`_run_commit_review`·`_run_generic`)·claude.py (`ALLOWED_TOOLS`·`READONLY_TOOLS`·`usage_limit`)·runner.py

먼저 실제 파일을 읽는다. 대화 이력을 전제로 판단하지 않는다. README "조사로 확인한 현재" 와 코드가 다르면 코드가 기준이고, 다른 점을 ADR 에 적는다.

## 작업 (문서만 — 코드·테스트를 바꾸지 않는다)

1. **ADR-0025 `docs/adr/0025-triage.md`**: README 사용자 결정 3가지와 계획 기본값 14가지를 결정으로 고정한다. 코드를 보고 기본값을 바꿔야 하면 바꾸고 이유를 적는다. 반드시 정할 것:
   - 판단 단계를 가르는 방법: `output_kind == "triage_result"` 를 읽는 곳을 한 함수(예: `domain/execution_policy.is_triage_kind(spec)` 또는 같은 수준)로 모으고, `open_stage`·`agent_candidates`·`work_item_facts`/`work_status`·`_spawn_successors`·목록 단계 수가 그것을 쓰는 방법. 판단 단계가 업무의 "첫 단계"(대표 종류 `work_items.kind`)가 되지 않게 하는 방법 — 판단 단계는 기존 첫 단계 **뒤에** 만들어지므로 `open_stage`(최신 열린 단계)가 판단 단계를 고르지 않게 해야 한다.
   - 판단 단계·실행의 요구 능력 `code.triage {repository_id}` 와 매칭: 판단 Agent(`triage_agent_id`)를 `chosen_agent_id` 처럼 고정해 그 Agent 에만 가게 하는 방법(기존 `select_agent` 재사용), 연결 저장소 `repository_id` 를 채우는 방법(App 소스는 `workflow_repository_id` 가 None 일 수 있음 — 17 매칭 결과로 채우는 선례).
   - 판단 실행 claim: 러너 claim 의 `supported_kinds` 에 `triage` 가 없으면 판단 실행을 주지 않는 방법(`repo.claim_execution` 조건) — 옛 러너 호환.
   - 체크아웃 대상: 기본 브랜치 끝(`origin/<기본 브랜치>`) — phase 12 `base` 계산과 같은 것을 러너가 쓰는지, 요청에 커밋을 넣을지(중앙은 커밋을 모름) 정한다. 판단 결과에 본 커밋(`inspected_commit`)을 담을지.
   - `TriageResult` 칸·제약(README 기본값 3)과 요청에 넣는 후보 목록 형태(`TriageCandidates` — 종류·담당·선행), 검증 실패 코드.
   - 판단 로그 표·기준 표·자동 시작 표의 칸, 사람 처리 기록을 쓰는 곳(맡기기 트랜잭션 — `hand_work_to_agent` 와 멤버 배정 경로), `superseded` 규칙.
   - 자동 판단 대상 쿼리(README 기본값 10)와 워커 tick 에서의 자리(`_start_waiting_stages` 뒤가 기본 — 이유), 자동 시작의 자리.
   - 자동 시작 자격 20건의 정확한 계산(제안 종류 기준·`accepted`+`changed` 행, `superseded` 제외).
   - 업무 상태 이유 문구: `판단 중`·`판단 제안 · 맡겨도 됨 0.86`·`판단 실패` 등 — `새로 들어옴` 이유 칸에 어떻게 보일지.
   - 직접 등록·n8n 업무: 연결 저장소를 정할 수 있는지 코드로 확인하고 판단 대상 여부를 정한다.
   - 선행 업무 링크(`work_item_links(blocks)`)가 착수를 막는지 코드로 확인한 결과.
2. **ARCHITECTURE "판단 — phase 19" 절**(끝에 추가): 흐름(대상 고르기 → 판단 단계 → 러너 → 판정 → 로그 → 제안/자동 시작), 스키마 v15 표(표·칸·CHECK·FK·인덱스, Agent 능력·내장 종류 시드 방법, v14 → v15 마이그레이션 — `tasks` 재생성 금지), 모듈·함수 이름·시그니처 표(step 번호 표시), 경로 표(패널 버튼·설정 화면·저장소 카드), 업무 상태 이유 문구, 판단 로그 상태·사람 처리 값, 러너 프로토콜 변화.
3. **CONTRACT.md**: `triage_result` 예시 블록 1개(정상 `ready`)와 요청 후보 목록 예시. 기존 블록 수를 세는 테스트가 있으면 이 step 에서 기대값을 같이 고친다(그 테스트만).
4. **`docs/product/triage-criteria-v1.md`**: 판단 기준 v1(README 기본값 12) — 에이전트가 읽는 지시문. 각 항목(명확성·검증 가능성·범위·위험·권한·과거 유사 결과·선행 의존)이 `proceed`·`confidence` 에 어떻게 반영되는지, `unsuitable` 예시(운영 접근·비밀값·되돌리기 어려움), 근거는 항목 이름 + 한 줄. 600단어 이내.
5. **GLOSSARY**: phase 19 용어(판단·판단 단계·판단 Agent·판단 기준·기준 버전·판단 로그·판단 제안·진행 여부(`ready`/`needs_check`/`unsuitable`)·확신도·자동 시작·사람 처리) — 코드 식별자와 쓰지 말 말.
6. **REDESIGN_PLAN**: 13절 19-triage 행에 이번 범위와 ADR-0025 링크, 15절 미결의 "자동 시작 최소 건수·확신도 기준 초기값" 을 결정됨(20건·0.8)으로.
7. **docs/CURRENT_HANDOFF.md** 맨 위 "다음 작업"에 한 줄: 19-triage 진행 중(`feat-19-triage`, `service` 에서 갈라짐 — 끝나면 `--no-ff` 병합, Jira·17 실연동·브라우저 확인은 사용자가 뒤로 미룸).

## 테스트

문서만 바꾼다. 기존 테스트가 문서 형식(CONTRACT 블록 수 등)을 검사하므로 전체 회귀를 돌린다.

## Acceptance Criteria

```bash
python3 -m pytest -q
python3 -m ruff check .
```

위 커맨드와 이 step 의 테스트 항목을 모두 확인한다.

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 규칙을 확인한다.
   - `domain/` 은 FastAPI·sqlite3·HTTPX·subprocess·Git 을 import 하지 않는다.
   - `server/` 와 `connector/` 는 서로 import 하지 않고 `contracts/` 만 공유한다.
   - 외부 입력(업무 본문·요청 본문·폼·쿼리 문자열·기준 문서·모델 응답)에서 명령·경로를 받아 실행하지 않는다. 판단 결과의 담당·종류·선행 업무는 중앙이 넘긴 후보 목록 안에서만 받는다.
   - 비밀값(연결 토큰, `OPERATOR_TOKEN`, `SESSION_SECRET`, GitHub App 비밀·설치 토큰, PAT, Jira API 토큰, 알림 웹훅 URL)은 DB·로그·응답·템플릿·백업·요청문에 넣지 않는다.
   - 템플릿은 외부 문자열(업무 제목·판단 근거·기준 문서)을 자동 이스케이프로만 출력한다(`|safe` 금지).
   - 종류 이름(`bug_fix`·`code_review`·`triage`)으로 새 분기를 만들지 않는다 — 판단 단계는 결과 형태(`output_kind == "triage_result"`)로 가른다.
   - GLOSSARY 이름을 그대로 쓴다.
   - phase 8·11·12·14~18 의 GitHub·Jira 순환(수집 → 맡기기 → 수정 → 검토 → 초안 PR → 병합 추적 → 업무 완료), 사람별 내 차례, 직접 작업·PR 신호, 소유자 승인·꺼진 러너 대기·검증만 다시, Jira 세 순간·후속 이슈가 그대로 동작한다.
3. 성공이면 `phases/19-triage/index.json` 의 이 step 만 `completed` 로 바꾸고, `summary` 에 한 줄로 남긴다: 생성·수정·삭제 파일, 결정, 검증 결과(테스트 수), 다음 step 이 주의할 점.
4. 3회 수정 후에도 실패하면 `error` + `error_message` 를 기록한다. 사용자 결정·외부 자원이 필요하면 `blocked` + `blocked_reason` 을 기록한다. 실행하지 않은 검증을 `completed` 로 표시하지 않는다.

## 금지사항

- 실제 Claude/Codex·GitHub(api.github.com·github.com)·Jira·Discord·외부 웹훅을 호출하지 않는다. 이유: 모든 step 은 가짜 도구(PATH 앞의 가짜 `claude`/`codex`)·httpx `MockTransport`·가짜 클라이언트로 검증한다. 판단 실연동은 phase 뒤 사용자 지시로 한다.
- 외부 판정 서비스·LLM API 를 새로 붙이지 않는다. 이유: 업무 내용이 외부로 나가면 안 되는 경우가 있다(REDESIGN_PLAN 6절 2). 판단은 러너의 로컬 에이전트만 한다.
- 사용자가 띄워 둔 환경을 읽거나 바꾸지 않는다: 셀프호스트(compose 프로젝트 `runloom`, 포트 8000, 볼륨 `runloom_workflow-data`)·`deploy/selfhost/.env`·러너 홈(`~/Library/Application Support/workflow-connector*`)·launchd(`launchctl` 실행 금지)·`~/Library/LaunchAgents`·`~/.claude/`·`/Users/kje/demo/*`. 컨테이너를 띄우거나 멈추지 않는다. 이유: 실사용 중인 환경이다.
- 새 의존성(Python 패키지, JS 프레임워크·번들러, 외부 CDN)을 추가하지 않는다. 이유: ADR-0002·UI_GUIDE.
- `tasks` 표를 재생성하거나 `tasks.status` CHECK 를 바꾸지 않는다. 이유: ADR-0020.
- 판단 결과로 업무를 완료·종료 처리하거나 `내 차례` 를 만들지 않는다. 모델의 "완료했다" 응답이나 프로세스 종료 코드만으로 완료 처리하지 않는다. 이유: 판단은 제안이다(사용자 결정), 계약 v1.
- 데이터 등급·에이전트 허용 명령·판단 품질 화면(20-monitor)·판단 실제 결과 집계·로컬 모델·A2A·자동 재판단을 만들지 않는다. 이유: 이번 범위 밖(README "하지 않는 것").
- 사용자 변경을 삭제하거나 stash·reset 하지 않는다. 커밋은 하네스가 한다.
- 이 step 범위 밖 모듈을 "개선"하지 않는다. 기존 테스트의 단정을 약하게 만들어 통과시키지 않는다.
