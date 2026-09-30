# Step 0: handoff-design — 사람 사이 인계 결정과 이름·계약 고정

## 읽어야 할 파일

- AGENTS.md
- phases/17-team-handoff/README.md (조사 결과·계획 기본값 10가지 — 이 phase 의 기준)
- docs/product/REDESIGN_PLAN.md (5.3·13절)
- docs/adr/0020-work-items-and-stages.md, docs/adr/0021-team-accounts-and-roles.md, docs/adr/0022-work-screen-and-direct-work.md, docs/adr/0018-real-repo-cycle.md, docs/adr/0014-github-task-cycle.md
- docs/ARCHITECTURE.md ("팀 — phase 15" 역할 × 동작·내 차례·알림·러너 소유자, "업무 화면 — phase 16" 담당 바꾸기·주소 표·스키마 v12, "실제 저장소 순환 — phase 12" 러너 검증·`--env`)
- docs/CONTRACT.md (ExecutionRequest·ClaimRequest·호환 규칙 절), docs/GLOSSARY.md, docs/UI_GUIDE.md
- src/workflow/contracts/v1.py (`ExecutionRequest`·`ClaimRequest`·`_OmitUnknownMeasure` 선택 칸 관례)
- src/workflow/domain/team.py (`can`·`turn_recipients`), src/workflow/domain/task_readiness.py, src/workflow/domain/work_status.py, src/workflow/domain/status.py, src/workflow/domain/notification.py, src/workflow/domain/work_list.py
- src/workflow/adapters/db.py (`agents`·`connectors`·`human_requests`·`notifications`·`work_item_events`·`executions`·v11→v12 마이그레이션 방식), src/workflow/adapters/repo.py (`turn_recipients_of`·`list_work_rows`·`append_event`)
- src/workflow/server/work_actions.py (`assign_work`·`start_stage`·후보), src/workflow/server/human_api.py (종류별 허용 동작), src/workflow/server/views.py (`RESPONSE_ACTIONS`·`agent_owner_id`·`agent_online`), src/workflow/server/task_cycle.py (`request_text`·`READINESS_REQUEST_CODES`), src/workflow/server/worker.py (`_start_ready_tasks`·`_resume`·`_start_fix`·`_notify`·`_refresh_task`), src/workflow/server/machine_api.py
- src/workflow/connector/local_tool.py (`run`·`_run_argv`·`_in_clean_checkout`·`child_env`), src/workflow/connector/masking.py (`registered_env`), src/workflow/connector/runner.py·client.py (claim 보고)
- deploy/selfhost/install-runner.sh, docs/SELFHOST.md (러너 절)

먼저 실제 파일을 읽는다. 대화 이력을 전제로 판단하지 않는다.

## 작업

문서만 바꾼다(제품 코드·테스트 없음). README "계획 기본값" 10가지를 결정 기록으로 옮기고, 이후 step 이 쓸 이름·계약·스키마를 확정한다. 코드를 읽어 README 의 "조사로 확인한 현재"와 다른 사실이 나오면 ADR 에 적고 그 사실을 기준으로 정한다.

1. `docs/adr/0023-cross-member-delegation.md` 를 새로 만든다.
   - 결정: README 1~10.
   - 하지 않는 것.
   - 대안과 기각 이유:
     - 누구 것이든 바로 실행 — 소유자가 자기 Mac·구독 사용을 통제할 수 없다.
     - 항상 소유자 승인 — 없애려던 사람 사이 병목이 다시 생긴다.
     - 꺼진 에이전트 선택 막기 — 맡긴 사람이 기억했다가 다시 맡겨야 한다.
     - 버튼 이름만 바꾸기 — 환경 원인 검증 실패마다 에이전트 사용량이 들고 diff 가 바뀐다.
     - 계약 칸으로 제목·지시 전달 — 러너·계약 변경이 커진다. 요청문으로 충분하다.
     - `work_items` 에 저장소 칸 — 원본에서 계산할 수 있다.
     - 러너 전용 venv — 설치 흐름 변경이 크다. 상대 경로 풀기로 이번 원인을 막는다.
   - ADR-0021 결정 5(받는 사람 규칙)에 "승인 요청은 소유자" 우선 규칙이 붙는다고 적고, ADR-0021 끝에 한 줄 링크를 단다.
2. `docs/ARCHITECTURE.md` 에 "사람 사이 인계 — phase 17" 절을 쓴다. 반드시 아래를 **확정**해 적는다.
   - **소유자 결정 규칙**: 에이전트 소유자 = `connectors.owner_member_id`(없으면 "공용" = 활성 관리자 전원이 소유자 역할). 순수 함수 이름·위치(예: `domain/delegation.py` `needs_owner_approval(policy, requester_id, owner_id) -> bool`, `approval_deciders(owner_id, admins)`).
   - **승인 흐름 상태 전이**:
     - 맡기기 → (정책·소유자 비교) → 바로 착수 | 승인 요청 생성.
     - 승인 → 착수(꺼져 있으면 대기). 거절 → 단계 선택 해제 + 업무 담당 비움 + 맡긴 사람 알림.
     - 담당을 다른 것으로 바꾸면 열린 승인 요청을 닫는다.
     - 승인 요청이 열린 동안에는 어떤 경로(패널·옛 `delegate|select|run`·워커 자동 착수·[다시 맡기기])로도 실행을 만들지 않는다 — 막는 지점(준비 판정 막힘 코드 이름, 비순환 종류에서 막는 함수)을 적는다.
     - 사람 요청 코드 `owner_approval`, 허용 동작 `approve`·`decline`(버튼 [승인]·[거절]), 필요 동작(`team` 상수 — 소유자 비교를 함께 하는 규칙), 업무 상태 `내 차례` + 이유 문구.
   - **받는 사람 규칙 갱신**: 열린 `owner_approval` 요청이 있으면 받는 사람 = 소유자(공용이면 활성 관리자 전원). 그 밖은 ADR-0021 그대로. `turn_recipients` 의 새 인자 이름.
   - **꺼진 러너 대기**: "꺼짐" 판정(기존 `agent_online` 기준 그대로), 순환 종류 = 기존 `executor_offline` 문구를 "<소유자>의 러너 꺼짐 · 켜지면 시작"으로 바꿈, 비순환 종류 = 실행을 만들지 않고 기다리는 위치와 대기 이유 저장 위치, 워커가 다시 시도하는 시점, 소유자 알림 중복 키(`runner_offline:<work_item_id>:<agent_id>`).
   - **알림 사건 3개**: `delegated_to_you`(맡긴 사람 ≠ 소유자, 받는 사람 = 소유자), `runner_offline_waiting`(받는 사람 = 소유자), `delegation_declined`(받는 사람 = 맡긴 사람). 제목 문구, 중복 키, 공용·개인 웹훅 경로(ADR-0021 결정 6 그대로).
   - **검증만 다시**:
     - 사람 요청 동작 `reverify`(버튼 [검증만 다시]). 어떤 요청 코드에 붙는지 — 결과 커밋이 있는 검증 실패 요청. 코드 이름은 `task_followup.py` 에서 확인한다.
     - 워커가 만드는 실행의 모양: 같은 Task·같은 Agent·같은 `task_revision`? 아니면 올림? 이전 실행과의 관계, 결과 판정이 이전 결과를 어떻게 대체하는지.
     - 옛 러너 대기 문구, `resume` 버튼 새 이름 [답하고 다시 맡기기].
   - **계약 변경**(CONTRACT.md 에도 같은 절 번호로):
     - `ExecutionRequest.verify_only_commit: CommitSha | None = None` — 순환 수정 종류 target 에서만 허용할지 등 검증 규칙.
     - null 이면 직렬화에서 빼는 호환 방식.
     - `ClaimRequest.capabilities: list[str] | None = None`(알려진 값 `"verify_only"`, 중복 금지, 모르는 값은 서버가 무시).
     - 러너 결과 봉투는 기존 모양 그대로 쓴다(에이전트 산출물 없음 — 어떤 칸이 비는지 적는다).
   - **인계 맥락 요청문 모양**: 머리(`# <키> <제목>`), 양식 칸 절, "## 맡긴 사람 지시 (이름)" 절의 순서와 빈 경우 생략 규칙, 원래 `request` 는 그 뒤. 순환·비순환 모두 같은 함수를 지난다(함수 이름·위치). 요청문 크기 상한.
   - **저장소 보기**: 저장소 계산 규칙(GitHub 원본 = `github_sources.repository_full_name`, 그 밖 = 없음), `group=repo`·`repo=<owner/name>` 쿼리, 묶음 순서(저장소 이름순, "저장소 없음" 마지막), 짧은 원본 키 규칙(`owner/name#n` → `name#n`).
   - **상대 PYTHONPATH 풀기**: 러너 함수 이름·위치, 규칙(`:` 로 나눔, 빈 항목 유지 여부, `~` 는 풀지 않음, 절대 경로는 그대로).
   - **단계 상태 재계산**: 어느 이벤트에서 어느 함수를 부르는지.
   - **러너 두 대 설치**: `--name` 규칙, label·plist·로그·홈 경로 표, 이름 없는 설치와 공존.
   - **스키마 v13**: 칸·표·CHECK 변경. `human_requests`·`notifications` 등에 CHECK 가 있으면 재생성이 필요한지, 참조하는 표가 있는지 확인해 적는다. `tasks` 재생성은 금지다. v12 → v13 마이그레이션 규칙: 새 칸 기본값, 행 보존, 실패하면 v12 그대로.
   - **이름·시그니처 표**: 이후 step 이 만들 함수·모듈·라우트와 필요 동작.
3. `docs/CONTRACT.md` 에 계약 변경 절과 예시 JSON(검증만 다시 요청 1개, capabilities 가 든 claim 1개)을 넣는다.
4. `docs/GLOSSARY.md` 에 용어를 넣는다: `맡기기 정책`(`delegation_policy`: `run`·`owner_approval`), `소유자 승인`(`owner_approval`), `에이전트 소유자`, `검증만 다시`(`reverify`·`verify_only_commit`), `러너 능력 보고`(`capabilities`), `지시 메모`, `저장소 묶기`. 금지 표현이 있으면 적는다(예: "러너 주인" 대신 "소유자").
5. `docs/product/REDESIGN_PLAN.md` 13절 표: `17-team-handoff` 를 넣고 Jira·판단·모니터링을 18·19·20 으로 민다.
6. `docs/CURRENT_HANDOFF.md` "다음 작업" 맨 위에 "17-team-handoff 진행 중(phases/17-team-handoff)" 한 줄을 넣는다.

## 테스트 먼저

문서 전용 step 이라 제품 테스트를 새로 만들지 않는다. 기존 문서 검사(링크 등)는 통과해야 한다.

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
   - 외부 입력(요청 본문·폼·쿼리 문자열·지시 메모·이슈 본문·양식 칸·모델 응답)에서 명령·경로를 받아 실행하지 않는다. 검증 명령은 러너에 등록된 것만 쓴다.
   - 비밀값(연결 토큰, `OPERATOR_TOKEN`, `SESSION_SECRET`, 로그인 세션·초대·재설정 토큰 원문, 비밀번호, GitHub App 비밀·설치 토큰, PAT, 알림 웹훅 URL, `--env` 값)은 DB·로그·응답·템플릿·백업에 넣지 않는다.
   - 권한은 `domain/team.py` 역할 × 동작 표로만 판정한다. 소유자 비교는 그 표와 함께 쓰는 순수 함수로 한다.
   - 템플릿은 외부 문자열(업무 제목·지시 메모·멤버 이름)을 자동 이스케이프로만 출력한다(`|safe` 금지).
   - GLOSSARY 이름을 그대로 쓴다.
   - phase 8·11·12·14·15·16 의 GitHub 순환(수집 → 맡기기 → 수정 → 검토 → 초안 PR → 병합 추적 → 업무 완료), 사람별 내 차례, 직접 작업·PR 신호가 그대로 동작한다.
3. 성공이면 `phases/17-team-handoff/index.json` 의 이 step 만 `completed` 로 바꾸고, `summary` 에 한 줄로 남긴다: 생성·수정·삭제 파일, 결정, 검증 결과(테스트 수), 다음 step 이 주의할 점.
4. 3회 수정 후에도 실패하면 `error` + `error_message` 를 기록한다. 사용자 결정·외부 자원이 필요하면 `blocked` + `blocked_reason` 을 기록한다. 실행하지 않은 검증을 `completed` 로 표시하지 않는다.

## 금지사항

- 실제 GitHub(api.github.com·github.com)·Discord·외부 웹훅·실제 Claude/Codex 를 호출하지 않는다(공식 문서 읽기는 허용). 이유: 모든 step 은 httpx `MockTransport`·가짜 GitHub·가짜 도구(PATH 앞의 가짜 `codex`/`claude`)·가짜 알림 수신으로 검증한다. 실연동은 phase 뒤 사용자와 함께 한다.
- 사용자가 띄워 둔 환경을 읽거나 바꾸지 않는다: 셀프호스트(compose 프로젝트 `runloom`, 포트 8000, 볼륨 `runloom_workflow-data`)·`deploy/selfhost/.env`·러너 홈(`~/Library/Application Support/workflow-connector*`)·launchd(`launchctl` 실행 금지)·`~/Library/LaunchAgents`·`~/.claude/`·`/Users/kje/demo/*`. 컨테이너를 띄우거나 멈추지 않는다. 이유: 실사용 중인 환경이다. 러너가 필요한 테스트는 임시 폴더를 `WORKFLOW_CONNECTOR_HOME` 으로 쓴다.
- 새 의존성(Python 패키지, JS 프레임워크·번들러, 외부 CDN)을 추가하지 않는다. 이유: ADR-0002·UI_GUIDE.
- 종류 이름(`bug_fix`·`code_review`)으로 새 분기를 만들지 않는다(`composition.py`·`worker.py` 포함). 이유: 업무 종류·후속 규칙은 워크스페이스 등록 데이터다(ADR-0009, AGENTS.md). 순환 종류인지는 기존 `execution_policy` 판정을 쓴다.
- `tasks` 표를 재생성하거나 `tasks.status` CHECK 를 바꾸지 않는다. 이유: ADR-0020.
- 모델의 "완료했다" 응답이나 프로세스 종료 코드만으로 완료 처리하지 않는다. 검증만 다시도 평소 결과 판정을 거친다. 이유: 계약 v1.
- Jira(18-jira), 판단 제안·자동 시작(19-triage), 모니터링 확장(20-monitor), Claude Code 훅, 보드 끌기, 여러 행 일괄 변경, 새 역할을 만들지 않는다. 이유: 사용자 결정 2026-09-30 범위 밖.
- 사용자 변경을 삭제하거나 stash·reset 하지 않는다. 커밋은 하네스가 한다.
- 이 step 범위 밖 모듈을 "개선"하지 않는다. 기존 테스트의 단정을 약하게 만들어 통과시키지 않는다.
