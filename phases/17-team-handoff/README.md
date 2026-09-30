# Phase 17 — 사람 사이 인계: 다른 멤버의 에이전트에게 맡기기 + 16 다듬기

작성일: 2026-09-30. 상태: 구현 계획 작성 완료, 모든 step pending. **`service`(16-work-ui 병합, 스키마 v12)에서 실행한다.** 근거: [재설계 계획](../../docs/product/REDESIGN_PLAN.md) 5.3·13절, [ADR-0021](../../docs/adr/0021-team-accounts-and-roles.md)(팀·러너 소유자·내 차례), [ADR-0022](../../docs/adr/0022-work-screen-and-direct-work.md)(업무 화면·담당 = 맡기기), [CURRENT_HANDOFF](../../docs/CURRENT_HANDOFF.md) "다음 작업".

## 왜

Runloom 의 핵심 가치는 내 에이전트 사이의 병목이 아니라 **사람 사이의 병목**을 줄이는 것이다(사용자 정리, 2026-09-30). 다른 멤버의 에이전트, 곧 그 멤버 Mac 에 있는 러너로 작업이 바로 가야 하고, 결과는 받아야 할 사람에게만 "내 차례"로 가야 한다. 혼자 쓰는 흐름은 Orca 와 Claude Code 로도 충분하다. phase 15 가 멤버 러너와 소유자를 만들었지만, 실사용은 러너 한 대로만 했다.

## 조사로 확인한 현재 (2026-09-30, `service` fb33816)

- 한 Mac 에서 러너 두 대: 러너 홈은 `WORKFLOW_CONNECTOR_HOME` 으로 나눌 수 있다(`connector/config.py`). 단일 실행 잠금은 없다. `deploy/selfhost/install-runner.sh` 는 launchd label `com.workflow.selfhost.connector`·plist·로그 경로가 고정이라 launchd 러너는 한 대뿐이다. 이 스크립트는 `python3 -m pip install -e <저장소>` 로 러너를 호스트 python3 에 편집 설치한다.
- 에이전트 → 러너 → 소유자: `agents.connector_id` → `connectors.owner_member_id`. `views.agent_owner_id`·`views.agent_online` 이 있다. **패널의 담당 후보(`_work_panel.html`, `work_actions` 후보)에는 이름만 나온다.** 소유자·켜짐 여부는 나오지 않고, 꺼진 에이전트도 후보에 남는다.
- 꺼진 러너의 에이전트에게 맡기기: 막지 않는다(`assign_work` → `start_stage(strict=False)`). 순환 종류(`bug_fix`·`code_review`)는 준비 판정 `executor_offline`("연결 끊김, 마지막 확인 …")으로 대기하고 워커가 매 tick 다시 본다. 순환이 아닌 종류는 온라인 검사 없이 실행을 만들어 `실행 요청됨 · 접수 대기` 로 남는다. 러너 소유자에게는 알림이 가지 않는다.
- 권한: 멤버도 `DELEGATE` 를 가진다. 에이전트 소유자를 비교하는 코드는 없다. 소유자의 제어는 해제·코드 취소·에이전트 삭제뿐이고 거절·승인은 없다.
- 인계 맥락(`connector/prompt.py`): 요청문 `task.request`(GitHub 이슈 본문)·인계 파일·이전 검토 findings. 순환 종류에만 "## 사람 응답" 을 붙인다(`task_cycle.request_text`). **업무 제목·양식 칸(`form_json`)은 프롬프트에 없다. 맡기는 사람이 지시를 적을 칸도 없다.**
- 내 차례 받는 사람(`team.turn_recipients`): 담당 멤버 → 맡긴 사람 → 활성 관리자 전원. 에이전트가 담당이면 맡긴 사람이 받는다. 에이전트 소유자는 쓰지 않는다. 알림(`Worker._notify`)도 같은 받는 사람을 쓴다.
- 러너 두 대 테스트는 없다. `tests/e2e/test_team.py` 가 멤버 두 명·러너 한 대(`WORKFLOW_CONNECTOR_HOME`, PATH 앞의 가짜 `codex`)로 돈다.
- 저장소별 보기: `work_list.GROUP_BYS = ("assignee","status")`. `work_items` 에 저장소 칸은 없다. GitHub 업무는 `source_id` → `github_sources.repository_full_name` 으로 알 수 있다. 직접 등록·n8n 업무는 저장소가 없다.
- 목록 키 칸은 `home.html` 의 `{{ row.source_key or row.work_key }}` — 원본 키가 먼저다. 브랜치·PR·패널은 `RUN-n` 을 쓰는데 목록에서는 찾을 수 없었다.
- [답하고 다시 판정](`views.RESPONSE_ACTIONS` 의 `resume`)은 워커 `_resume` 을 거쳐 **에이전트를 이전 결과 브랜치 위에서 다시 돌린다**. 이름과 동작이 다르다. "결과는 그대로 두고 검증만 다시" 하는 동작은 없다.
- 검증 명령: `local_tool._run_argv` 가 셸 없이 argv 를 worktree 에서 한 번, `result_commit` 의 깨끗한 임시 체크아웃에서 한 번 실행한다. 환경은 허용 목록(HOME·PATH·…)에 `--env` 를 더한 것이다. **상대값 `--env PYTHONPATH=src` 는 그대로 넘어가서**, 하위 프로세스가 cwd 를 바꾸면 호스트의 편집 설치(Runloom `service`)를 잡는다. sandbox RUN-24 의 검증 실패 6건이 이 때문이었다.
- 단계 상태 결함: 실행이 running 이 되는 이벤트(`machine_api` → `repo.append_event`)는 `executions` 만 갱신한다. 워커도 순환 종류의 활성 실행은 `_resume` 만 보고 넘어간다. 그래서 `tasks.status` 가 결과 판정 전까지 `실행 요청됨 · 접수 대기` 로 남는다. 업무 상태(`work_items.status`)는 맞다.

## 계획 기본값 (사용자 결정 2026-09-30, step 0 이 ADR-0023 으로 고정)

1. **맡기기 정책 — 에이전트마다.** `agents.delegation_policy` = `run`(바로 실행, 기본) | `owner_approval`(내 승인 뒤 실행).
   - 소유자가 에이전트 설정에서 고르고, 관리자도 바꿀 수 있다. 소유자 없는 러너(v11 이전)는 관리자가 소유한 것으로 본다.
   - 맡긴 사람 ≠ 소유자이면 소유자에게 항상 알림 "A 가 맡김 — RUN-n 제목"(정보 알림, 내 차례 아님)이 간다.
   - `owner_approval` 이고 맡긴 사람 ≠ 소유자이면, 승인 전에는 그 단계에 실행을 만들지 않는다. 패널 맡기기·옛 `/tasks/{id}/delegate|select|run`·워커 자동 착수·[다시 맡기기] 모두 같다.
   - 승인을 기다리는 동안 업무는 `내 차례`, 이유는 "A 가 맡김 · B 승인 대기"다. **받는 사람은 소유자다**(`turn_recipients` 에 우선 규칙 추가). 사람 요청 코드는 `owner_approval`, 버튼은 [승인]·[거절](메모 선택)이다.
   - 승인하면 곧 착수한다(러너가 꺼져 있으면 기본값 2의 대기).
   - 거절하면 그 단계의 선택을 지우고 업무 담당을 비운다(`담당 없음` 묶음으로 간다). 맡긴 사람에게 알림 "B 가 거절 — 메모"가 간다. 업무를 닫지는 않는다.
   - 자기 에이전트에게 맡기면 정책과 관계없이 바로 실행한다. 승인·거절은 소유자와 관리자가 한다.
2. **꺼진 러너에게 맡기기 — 허용하고 알린다.**
   - 담당 후보 한 줄: `이름 · <소유자 표시 이름>의 Mac · 켜짐|꺼짐`(소유자 없으면 `공용`).
   - 꺼진 에이전트에게 맡기면 담당은 저장한다. 업무 상태는 `대기`, 이유는 "B 의 러너 꺼짐 · 켜지면 시작"이다. 소유자에게 알림 "러너가 꺼져 있어 RUN-n 이 기다림"이 한 번 간다(업무 × 에이전트 중복 키).
   - 러너가 다시 붙으면 워커가 착수한다. **순환이 아닌 종류도 같다** — 러너가 꺼져 있으면 실행을 만들지 않고 기다린다. 이 판정은 종류 이름 분기 없이 에이전트 연결 상태로 한다.
3. **[검증만 다시]** — 검증 실패 사람 요청(`fix_verification_failed` 류, 코드는 step 0 이 확정)에 동작 `reverify` 를 더한다.
   - 에이전트를 돌리지 않는다. 같은 결과 커밋을 같은 러너(같은 저장소 등록)가 깨끗한 체크아웃에서 등록된 검증 명령으로 다시 검증하고, 평소처럼 판정한다.
   - 계약: `ExecutionRequest` 에 선택 칸(예: `verify_only_commit`)을 추가한다. `ClaimRequest` 에는 선택 칸 `capabilities: list[str] | None` 을 추가하고 러너가 `"verify_only"` 를 보고한다. 보고하지 않는 옛 러너에는 배정하지 않고 "연결 프로그램 업데이트 필요"로 기다린다. 옛 서버·옛 러너와의 호환은 기존 선택 칸 관례(null 이면 직렬화에서 뺌)를 따른다.
   - 기존 `resume` 버튼 이름은 [답하고 다시 맡기기]로 바꾼다(동작 그대로).
4. **인계 맥락.** 패널 담당 폼에 선택 칸 "지시 메모"(최대 2000자)를 넣는다. 에이전트 요청문 앞에 `# <RUN-n> <업무 제목>`·양식 칸(제목·값, 빈 칸 제외)·"## 맡긴 사람 지시 (이름)"을 붙인다. 계약 칸은 바꾸지 않고 서버가 `request` 문자열을 만든다. 메모는 업무 이벤트로 남아 패널 타임라인에 보인다.
5. **저장소별 보기.**
   - 묶기에 `repo`, 도구 막대에 저장소 필터 `repo=<owner/name>`(열거형 — 워크스페이스에 있는 저장소만, 모르는 값은 무시)를 넣는다.
   - 저장소가 없는 업무는 "저장소 없음" 묶음에 둔다. 기본 묶기는 담당자 그대로다.
   - 저장소는 조회 때 원본 소스에서 계산하고, 칸을 새로 두지 않는다(step 0 이 확인).
6. **목록 키 칸 = `RUN-n` 먼저.** 원본 키는 옆에 흐리게 짧게 쓴다(`sandbox#3` — 저장소 이름만 + 번호).
7. **상대 `PYTHONPATH` 풀기.** 러너가 실행할 때 등록된 `--env PYTHONPATH` 의 상대 항목(`:` 로 나눈 각 항목)을 그 실행의 cwd(worktree 또는 임시 체크아웃) 기준 절대 경로로 바꾼다. 다른 변수는 그대로 둔다. 등록 화면·SELFHOST 에 안내한다.
8. **단계 상태 결함 수정** — 실행 이벤트(accepted·running 등)를 받으면 그 단계의 `tasks.status` 를 다시 계산한다. 재현 테스트를 먼저 쓴다.
9. **한 Mac 에 러너 두 대** — `install-runner.sh --name <이름>`(영소문자·숫자·하이픈)이면 label `com.workflow.selfhost.connector.<이름>`·plist·로그·러너 홈 `…/workflow-connector-<이름>` 을 나눈다. `--name` 이 없으면 지금과 같다(업그레이드 호환). 실연동 1회는 이 Mac 에서 두 번째 멤버 계정과 `--name b` 러너로 한다(사용자, phase 뒤).
10. **스키마 v13**(ALTER + 필요하면 새 표): `agents.delegation_policy`(기본 `run`), 승인 요청을 담을 곳(사람 요청 코드 `owner_approval` — `human_requests` 에 CHECK 가 있으면 확장 방법을 step 0 이 정한다, `tasks` 재생성 금지), 지시 메모(업무 이벤트 종류 `delegated_with_note` 또는 칸 — step 0 확정), 알림 사건 종류 `delegated_to_you`·`runner_offline_waiting`·`delegation_declined`, 검증만 다시 실행 표시(`executions` 칸).

**하지 않는 것**: Jira(18-jira), 판단 제안·자동 시작(19-triage), 모니터링 확장(20-monitor), 다른 Mac 에서 하는 실연동, 에이전트 사용량 한도, 러너 전용 venv 설치, Claude Code 훅 상태 보고, 보드 끌기, 여러 행 일괄 변경, 에이전트 소유자별 권한 역할 추가(역할은 관리자·멤버 두 개 그대로).

## Step 목록

| Step | 이름 | 산출물 |
|---|---|---|
| 0 | handoff-design | ADR-0023, ARCHITECTURE "사람 사이 인계 — phase 17", CONTRACT(검증만 다시·`capabilities`), GLOSSARY, REDESIGN_PLAN 13절 번호 밀기 |
| 1 | schema-v13 | 맡기기 정책·승인 요청·지시 메모·알림 사건·검증만 다시 표시, v12 → v13 |
| 2 | contract-verify-only | `contracts/v1.py` 검증만 다시 칸·claim `capabilities`, 호환 테스트 |
| 3 | runner-verify | 러너: 검증만 다시 경로, 상대 `PYTHONPATH` 풀기, `capabilities` 보고 |
| 4 | task-status-refresh | 실행 이벤트 뒤 단계 상태 재계산(결함 수정) |
| 5 | delegation-rules | `domain`: 승인 필요 판정·준비 판정 막힘·받는 사람 우선 규칙·꺼진 러너 문구 |
| 6 | owner-approval | 맡기기 → 승인 요청 → 승인·거절, 소유자 알림, 꺼진 러너 대기(모든 종류) |
| 7 | verify-only-server | [검증만 다시] → 검증만 다시 실행 → 판정, 버튼 이름, 옛 러너 대기 |
| 8 | handoff-context | 지시 메모 칸, 요청문에 업무 제목·양식 칸·지시 |
| 9 | work-list-polish | 저장소 묶기·필터, 키 칸 `RUN-n`, 담당 후보 소유자·켜짐, 맡기기 정책 설정 화면 |
| 10 | multi-runner-install | `install-runner.sh --name`, SELFHOST "한 Mac 에 러너 두 대" |
| 11 | handoff-verify | e2e(멤버 두 명·러너 두 대), v12 사본 마이그레이션, 인계 문서·사용자 확인 목록 |

## 실행

```bash
git status --short            # 깨끗해야 한다
git branch --show-current     # service
python3 scripts/execute.py 17-team-handoff --engine claude
```

사용자가 띄워 둔 셀프호스트(compose 프로젝트 `runloom`)·러너(launchd)·`/Users/kje/demo/*` 는 건드리지 않는다. 병합, 셀프호스트 재설치(v13, 백업 먼저, **러너도 재설치** — 러너 프로토콜이 바뀐다), 두 번째 러너 실연동은 phase 뒤 사용자 지시로 한다.
