# Step 5: triage-dispatch — 자동 판단 대상·판단 단계 만들기·물러남

## 읽어야 할 파일

- AGENTS.md
- phases/19-triage/README.md (사용자 결정 3가지·계획 기본값 14가지·조사 결과 — 이 phase 의 기준), phases/19-triage/index.json (이전 step summary)
- docs/ARCHITECTURE.md "판단 — phase 19" (step 0 이 쓴 이름·시그니처·스키마 표 — README 와 다르면 ARCHITECTURE 가 기준)
- docs/adr/0025-*.md (step 0 이 쓴 ADR), docs/GLOSSARY.md
- src/workflow/server/worker.py (`tick` 순서·`_start_waiting_stages`·러너 상태), stage_runs.py (`start_execution`·`run_task`), work_actions.py (`open_stage`·`agent_candidates`·`assign_work`), task_cycle.py (`origin`·`execution_request_text`), owner_approval.py
- src/workflow/adapters/repo.py (`work_item_facts`·`set_work_status`·`list_work_rows`·단계 만들기·`claim_execution`), src/workflow/domain/work_status.py, github_match.py
- step 1~3 산출물

먼저 실제 파일을 읽는다. 대화 이력을 전제로 판단하지 않는다.

## 작업

1. repo: 판단 대상 쿼리(README 기본값 10 — ARCHITECTURE 이름), 판단 단계 만들기(한 트랜잭션: 업무에 판단 단계 + 판단 로그 행은 결과 때(step 6) — 단계·실행 생성 규칙은 ARCHITECTURE), 로그 근거 사실 수집, 판단 Agent 읽기(GitHub 소스 칸 / Jira 프로젝트 → 연결 저장소 칸).
2. 서버 함수 `request_triage(conn, session_id, work_item_id, *, by_member_id | None)`(이름은 ARCHITECTURE): 후보·근거·기준 현재 버전으로 요청문(step 3) → 판단 단계·실행 → 판단 Agent 로 고정 매칭. 이미 도는 판단이 있으면 아무것도 안 함. 판단 Agent 없음·러너가 판단 미지원(옛 러너)·꺼짐은 이유를 돌려준다(화면 문구는 ARCHITECTURE).
3. 워커 tick: ARCHITECTURE 가 정한 자리에 `_triage_new_work` — 워크스페이스마다 최대 1건, 러너 빔 조건, `usage_limit` 실패한 Agent 는 1시간 쉼(워커 메모리, 시각 주입 가능하게), 자동 판단이 실패한 업무에 다시 걸지 않음(단 `usage_limit` 실패는 쉬는 시간 뒤 다시 대상).
4. 판단 단계 빼기: `open_stage`·`agent_candidates`·`work_item_facts`/`work_status`(판단 단계가 돌면 `새로 들어옴 · 판단 중`, 에이전트 작업 중이 아님)·목록 단계 수·후속 규칙(`_spawn_successors` 가 판단 단계 결과로 후속을 만들지 않음). 모두 step 2 의 판정 함수로.
5. 판단 실행은 소유자 승인·꺼진 러너 대기 경로(`owner_approval`)를 타지 않는다 — 판단 Agent 는 `run` 정책만 고를 수 있다(ADR-0025). 정책이 나중에 `owner_approval` 로 바뀌면 자동 판단을 건너뛴다.

## 테스트 먼저

`tests/workflow/server/test_triage_dispatch.py`(가짜 러너·DB): 새로 들어온 담당 없는 업무 2건 → tick 1회에 1건만, 다음 tick(앞 판단 끝난 뒤) 1건, 러너 실행 중이면 0건, 판단 Agent 없음 → 0건, 옛 러너(supported_kinds 에 triage 없음) → 0건, 원본 닫힘 → 0건, `usage_limit` 실패 → 1시간 동안 그 Agent 0건·1시간 뒤 같은 업무 다시, 그 밖 실패 → 같은 업무 다시 안 걸림, 판단 중 업무 상태 `새로 들어옴 · 판단 중`, 판단 단계가 있어도 패널 담당 select·`assign_work` 는 원래 단계에 맡김, Jira 업무는 연결 저장소의 판단 Agent, 판단 단계가 후속을 만들지 않음. GitHub·Jira 순환 회귀.

소스·템플릿을 바꾸기 전에 `tests/` 미러 경로에 실패하는 테스트를 먼저 작성하고, 실패 원인이 의도한 것인지 확인한다(`tdd-guard.sh` 가 테스트 없는 소스 작성을 막는다). 구현한 뒤 해당 테스트와 전체 회귀를 통과시킨다. 이 step 의 변경으로 기존 테스트가 깨지면 새 동작 기준으로 고치되 단정을 약하게 만들지 않는다. 무엇을 왜 바꿨는지는 summary 에 남긴다.

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
