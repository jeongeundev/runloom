# Multica: 한 업무가 사람과 에이전트 사이에서 진행되는 방식

- 조사일: 2026-10-04
- 대상: multica-ai/multica commit `b4ca5b4a23e68b26292a680dca7689a952bb1cd5`
- **코드 읽기 조사이며 실행해서 비교한 것은 아니다.** 서버나 데몬은 띄우지 않았다.
- 경로 약어: `h/` = `server/internal/handler/`, `s/` = `server/internal/service/`, `d/` = `server/internal/daemon/`, `m/` = `server/migrations/`, `docs/` = `apps/docs/content/docs/`
- 문서 내용과 코드 동작이 다른 곳은 **[불일치]**, 코드에서 확인하지 못한 것은 **미확인**으로 표시했다.

## 1. 핵심 객체와 관계

| 객체 | 정의 | 근거 |
|---|---|---|
| workspace | 최상위 격리 단위. 멤버는 owner·admin·member 역할을 갖는다 | `m/001_init.up.sql:15-33`, `docs/workspaces.mdx:8` |
| issue | 일의 단위. 담당자 종류는 `member·agent·squad`이고 `parent_issue_id`, `project_id`, `stage`(하위 업무 묶음 번호)를 가진다. 상태는 워크스페이스별 목록(`issue_status`)으로 관리한다 | `m/001_init.up.sql:52-70`, `m/084_squad.up.sql:31-33`, `m/123_issue_stage.up.sql:15`, `m/332_issue_status.up.sql:23` |
| agent | 계속 떠 있는 프로세스가 아니라 설정 묶음이다. 지시문·모델·스킬·러너·소유자·실행 권한을 담는다 | `m/001_init.up.sql:36-49`, `m/004_agent_runtime_loop.up.sql:18`, `docs/agents.mdx:10` |
| runtime | 데몬이 붙어 있는 컴퓨터와 그 위의 CLI 1종(provider). 소유자와 공개 범위가 있다 | `m/004_…`, `m/032_runtime_owner.up.sql:1`, `m/083_runtime_visibility.up.sql:2-3` |
| squad | 리더 에이전트 1명과 멤버(에이전트 또는 사람)로 이뤄진다. 지시문은 자유 텍스트 하나다 | `m/084_squad.up.sql:2-25`, `m/088_squad_instructions.up.sql:1` |
| skill | 재사용할 수 있는 작업 방법·자료 묶음으로, 여러 에이전트에 붙일 수 있다 | `m/008_structured_skills.up.sql:4,17,27` |
| project | 이슈 묶음. 저장소나 로컬 디렉터리를 `project_resource`로 붙인다 | `m/034_projects.up.sql:2-19`, `m/065_project_resources.up.sql:5-16` |
| run(`agent_task_queue`) | 실행 1회. 상태는 queued·dispatched·running·completed·failed·cancelled 등이다 | `m/001_init.up.sql:127-140`, `m/128_comment_routing_escalation.up.sql:5-17` |

관계를 한 줄로 요약하면 이렇다. 이슈에 배정·멘션·채팅·Autopilot 중 하나가 일어나면 run이 생기고, run은 에이전트에 묶인 runtime의 데몬이 실행하며, 결과는 이슈 댓글과 상태로 돌아온다(`docs/concepts.mdx:58-65`).

## 2. 업무 한 건의 생애

**생성**
- 사람, 에이전트(CLI 사용), Autopilot 중 하나가 이슈를 만든다.
- Autopilot은 두 방식이 있다. `create_issue`는 이슈를 만들고 배정까지 한다. `run_only`는 이슈 없이 run만 만든다(`s/autopilot.go:614-633,681,981`).

**배정과 실행 시작**

모든 경로는 하나의 판정 함수 `WillEnqueueRun`을 거친다(`s/issue_trigger.go:97`).
- 생성하거나 담당자를 바꾸면 run이 만들어진다. 단, 상태가 `backlog`이면 만들지 않는다(:128-133).
- `backlog`에서 다른 상태(done·cancelled 제외)로 옮기면 run이 만들어진다(:134-140).
- UI의 "Don't start yet"은 `suppress_run`으로 처리된다(`h/issue.go:3527-3532`).
- 같은 이슈·에이전트에 대기 중인 run이 이미 있으면 새로 만들지 않는다. DB 유니크 인덱스로 막는다(`m/452_agent_task_pending_thread_unique.up.sql:2-5`).

댓글로 시작하는 경우(`h/comment.go:2700-2792`)는 다음과 같다.
- 에이전트나 squad를 명시적으로 @멘션하면 그 대상이 실행된다.
- 에이전트의 댓글에 답글을 달면 그 에이전트가 다시 실행된다(`routeReplyToParentAuthor` :2763-2768).
- @all이나 @멤버만 있는 댓글은 아무 run도 만들지 않는다.

배정 경로는 수동 배정, 멘션, 답글, Autopilot(cron·webhook), 채팅이 전부다. 내용을 보고 담당 에이전트를 자동으로 골라 주는 기능은 squad 리더의 LLM 판단 외에는 **미확인**이다.

**실행**
- 데몬은 WebSocket RPC로 run을 가져가고, 안 되면 HTTP로 가져간다(`d/wsrpc.go:320-353`).
- 어떤 CLI를 쓸지는 run마다 정하지 않는다. runtime의 provider가 정한다(`d/daemon.go:5745`, `server/pkg/agent/agent.go:432-495`, 약 25종).
- Claude 실행 인자는 고정 배열이다: `-p --output-format stream-json --permission-mode bypassPermissions --disallowedTools AskUserQuestion` 외(`server/pkg/agent/claude.go:1078-1118`).
- Codex는 `danger-full-access`로 실행된다(`d/execenv/codex_sandbox.go:104-127`). 문서도 샌드박스가 없다고 명시한다(`docs/security-model.mdx:12-14,44`).
- 작업 디렉터리는 `~/multica_workspaces/<ws>/<issue>/{workdir,output,logs}`이다(`d/execenv/execenv.go:370-378`).
- 저장소는 미리 받아 두지 않는다. 에이전트가 `multica repo checkout <url>`을 실행하면 데몬이 bare clone 캐시에서 `agent/<agent>/<task>` 브랜치의 git worktree를 만들어 준다(`d/repocache/cache.go:823,865`).
- 로컬 디렉터리 자원은 `in_place` 또는 `worktree` 모드로 쓴다(`docs/project-resources.mdx:63-89`).
- 같은 이슈를 다시 실행하면 이전 세션을 `--resume`으로 이어 간다. 같은 runtime일 때만 가능하다(`h/daemon.go:2947-2969`).
- 지시문(brief)은 작업 디렉터리의 `CLAUDE.md` 또는 `AGENTS.md`에 쓰인다(`d/execenv/runtime_config.go:207-219`). 에이전트는 run 범위 토큰(`MULTICA_TOKEN`)을 받은 `multica` CLI로 이슈를 읽고, 댓글을 달고, 상태를 바꾼다.

**상태 전이**

정해진 전이 순서는 없다. 사람도 에이전트도 상태를 직접 바꾼다(`docs/issues.mdx:64`). 에이전트가 따르는 규칙은 brief의 문장일 뿐이다(`d/execenv/runtime_config_sections.go:771-791`).

| 상황 | 에이전트가 정하는 상태 |
|---|---|
| 일을 시작함 | `in_progress` |
| 결과를 전달함 | `in_review` ("`done` stays human") |
| 필요한 것이 없어 막힘 | `blocked` + 댓글 |
| 질문·논의만 함 | 상태를 바꾸지 않음 |

서버가 직접 바꾸는 경우는 두 가지뿐이다.
1. run이 실패하고, 재시도가 없고, 다른 활성 run도 없으면 `in_progress`를 `todo`로 되돌린다(`s/task.go:6076-6104`).
2. 이슈에 연결된 PR이 전부 병합되면 워크스페이스 설정 상태(기본 done)로 옮긴다(`h/pr_auto_complete.go:170-290`, `h/github.go:1621-1635`).

**완료 판정**
- run 완료(`CompleteTask`)는 이슈 상태를 쓰지 않는다(`s/task.go:4396-4475`). 출력이 비어 있어도 completed로 처리된다(`d/daemon.go:8904-8918`).
- 이슈가 끝났는지는 상태로 판단한다. `in_review`까지는 에이전트가 옮기고, `done`은 사람이 옮기거나 PR 병합 연동이 옮긴다.
- 자동 재시도는 runtime offline·timeout·네트워크 같은 인프라 실패에만 걸린다(`s/task.go:5241-5264`).

## 3. 에이전트 사이의 진행

**고정된 파이프라인이나 규칙 표는 없다.** 다음 담당자는 LLM이 매번 판단한다. "X가 끝나면 Y를 시작" 같은 규칙 표는 **미확인**이며, 마이그레이션에도 그런 테이블이 보이지 않는다.

- **Squad 리더**
  - 시스템이 고정 프롬프트 "Squad Operating Protocol"을 붙인다(`h/squad_briefing.go:33-94`).
  - 프롬프트 내용: "직접 하지 말고 조정하라(coordinate, NOT do the work)", 로스터의 역할·스킬을 보고 멤버를 @멘션하는 댓글 1개를 남기고 멈춰라, 다시 깨어나면 재평가해서 다음 위임·사람에게 에스컬레이션·종료 중 하나를 하라(:38-88).
  - 사람이 설정할 수 있는 것은 자유 텍스트 `squad.instructions` 하나다(:196-208). 예: "DB 일은 Alice에게".
  - 리더는 `multica squad activity`로 평가 결과(action·no_action·failed)와 이유를 활동 기록에 남긴다(`h/squad.go:976-1114`).
- **리더가 다시 깨어나는 조건**
  - 멤버가 멘션 없이 진행 댓글을 달면 리더가 깨어난다.
  - 누군가 명시적으로 @멘션하면 그 대상에게만 가고 리더는 깨어나지 않는다.
  - 리더 자신의 댓글로는 깨어나지 않는다(`h/comment.go:2739-2760,3070-3102`, `h/squad.go:1147-1159`).
- **멘션 체인**
  - 에이전트가 댓글로 다른 에이전트를 @멘션하면 그 에이전트의 run이 생긴다(`h/comment.go:2729`).
  - 깊이나 횟수 제한은 없고 대기 중복 제거만 있다(:3130-3137).
  - 문서도 "언제 끝낼지는 시스템이 정하지 않는다, 지시문에 종료 조건을 써라"라고 한다(`docs/mentioning-agents.mdx:73`).
  - 댓글 작성 시 특정 에이전트를 실행 대상에서 뺄 수 있다(`m/550_comment_suppressed_agents.up.sql:11`).
- **하위 업무(sub-issue)와 stage**
  - 에이전트가 `multica issue create --parent --stage N`으로 하위 업무를 만든다. 1단계는 `todo`로, 나머지는 `backlog`로 둔다(`s/builtin_skills/multica-platform/references/issues.md:313-342`).
  - 부모마다 시스템 wakeup 규칙 `child_done`이 있어서, 가장 이른 단계가 다 닫히면 부모 담당자를 깨운다. 담당자가 에이전트면 run, squad면 리더 run, 사람이면 inbox 알림이다(`s/issue_wakeup_system.go:332-341,551-562`).
- **[불일치]**
  - brief는 "하위 업무 전달은 항상 `in_review`, stage 장벽이 그 신호에 의존한다"고 한다(`runtime_config_sections.go:781`).
  - 그런데 하위 업무가 닫혔다는 이벤트는 done·closed 계열에서만 기록된다(`m/558_issue_child_event.up.sql:40-46`). `issue_wakeup_system.go`에도 `in_review` 처리가 없다.
  - 따라서 코드만 보면, 사람(또는 PR 병합)이 하위 업무를 done으로 옮겨야 부모가 깨어나는 것으로 보인다. 다른 경로는 **미확인**이다.
- **Wakeup**
  - 에이전트가 `multica issue wakeup`으로 이벤트 구독, 조건(`children_done`, `pull_request`, `issue_field`, `other_issue`), 만료를 등록할 수 있다(`docs/engineering/issue-wakeups.md:50-106,411-426,572-583`).
  - 폭주 방지 장치: 최대 발화 20회, 사람 개입 없이 3번째 순환하면 정지, 시간당 12회 제한(:596-605).

## 4. 사람의 개입

- **질문하는 방법**
  - Claude의 `AskUserQuestion` 도구는 꺼져 있고 "질문은 이슈 댓글로"라고 지시한다(`server/pkg/agent/claude.go:1085-1091`).
  - 에이전트는 `[@이름](mention://member/…)` 댓글로 사람을 부른다. 이 댓글은 inbox 알림이 된다(`runtime_config_sections.go:895`).
  - 막혔을 때는 `blocked` 상태와 댓글을 함께 쓴다(:783).
- **재개**
  - 사람이 에이전트 댓글에 답글을 달면 그 에이전트의 새 run이 생기고, 이전 세션을 이어 간다(`h/comment.go:2763-2768`, `h/daemon.go:2946-2958`).
  - 에이전트가 특정 멤버의 `comment.created`에 wakeup을 걸어 두고 기다릴 수도 있다(`issue-wakeups.md:136-143`).
  - 실행 중인 run에 메시지를 넣는 기능(supplement)도 있다. 사람만 쓸 수 있고 Claude·Codex·Grok만 지원한다(`h/task_supplement.go:223-274`).
- **검토와 승인**
  - 검토 전용 객체나 승인 게이트는 **미확인**이다. 승인 테이블은 플러그인 MCP 도구 관리자 승인 하나뿐이다(`m/369_plugin_mcp_approvals.up.sql:1`).
  - 실제 검토는 `in_review` 상태와 사람이 `done`으로 옮기는 관례로 이뤄진다.
  - README의 "Review gates"(`README.md:92`)는 이 관례를 가리키는 것으로 보인다. **[문서 표현 > 코드 구조]**

## 5. 다른 멤버의 에이전트 쓰기

- **실행 권한 범위**: `permission_mode private|public_to` 값과 대상 목록(`agent_invocation_target`, 대상 종류 workspace·member·team)으로 정한다(`m/130_agent_invocation_permission.up.sql:31-66`).
  - Only me = private
  - Entire workspace = public_to + workspace 대상
  - Specific people = public_to + member 대상
  - team 대상은 예약만 되어 있고 동작하지 않는다.
  - 새 에이전트의 기본값은 Only me다(`docs/agents.mdx:57`).
- **판정 함수**: `canInvokeAgent`(`h/agent_access.go:49-133`). private 에이전트는 admin도 실행할 수 없다(:86-90). 에이전트가 에이전트를 부를 때는 호출 사슬 맨 위의 사람 기준으로 판정한다(:74-79).
- **소유자 승인**: 없다. 허용 목록에 있으면 바로 실행되고, 없으면 403이다. 요청·승인 테이블은 **미확인**이다.
- **실행 위치**: run은 에이전트에 묶인 runtime을 그대로 따라가므로(`s/task.go:1294`) **에이전트 소유자가 등록한 runtime(보통 소유자의 머신)**에서 실행된다. 공개 runtime은 다른 사람이 자기 에이전트에 묶을 수 있다(`h/runtime.go:881-889`).

## 6. 재배정과 담당 변경

- 담당자를 바꾸면 새 담당자(에이전트)의 run이 생긴다. **기존 run은 취소되지 않고** 함께 돈다(`h/issue.go:4067-4078` 주석: "Ownership handoff no longer implies interruption").
- 담당자를 해제하거나 cancelled 상태로 바꿔도 run은 멈추지 않는다. 이슈를 삭제할 때만 run이 취소된다(`h/issue.go:4314`).
- 담당자를 사람으로 바꾸면 run 없이 담당만 바뀐다(`docs/assigning-issues.mdx:65`).
- "잘못 배정되었다"는 반송 동작이나 담당 범위 검사는 **미확인**이다. 담당 변경은 단순히 필드를 바꾸는 일이다.
- squad를 보관하면 그 squad의 업무는 전 리더 에이전트에게 넘어간다(`docs/squads.mdx:95`).

## 7. 워크스페이스 사이(팀 사이) 협업

**찾지 못했다.** 에이전트·squad 조회는 모두 이슈가 속한 워크스페이스 안에서만 이뤄진다(`server/pkg/db/queries/agent.sql:37-39`). run의 워크스페이스와 runtime의 워크스페이스가 다르면 run을 가져가는 시점에 취소된다(`h/daemon.go:1671-1676`). 문서도 워크스페이스는 "fully isolated"라고 한다(`docs/workspaces.mdx:8`). 한 계정이 여러 워크스페이스에 가입할 수는 있다.

## 8. 결과물과 검증

- **코드**
  - 에이전트가 직접 `gh pr create`를 실행하고, 호스트의 Git 자격으로 push한다(`runtime_config_sections.go:454`, `docs/vcs-integration.mdx:95`).
  - 스킬은 "PR을 먼저 만들고 최종 댓글은 그다음", PR 제목이나 브랜치에 이슈 키를 넣으라고 지시한다(`references/issues.md:35-55`).
  - GitHub 연동은 읽기 전용이다. PR 연결, CI 표시, 병합 시 상태 변경만 한다(`docs/github-integration.mdx:10`, `h/github.go:1060-1069`).
  - 예외: 로컬 디렉터리 `worktree` 모드에서는 데몬이 남은 변경을 에이전트 브랜치에 커밋한다(`docs/project-resources.mdx:82-86`).
- **코드가 아닌 결과**
  - 하나의 run마다 최종 결과 댓글 1개를 남긴다("Post exactly ONE comment per run", `runtime_config_sections.go:1012`).
  - 산출물 형식을 정하는 스키마는 **미확인**이다.
- **검증**
  - 테스트 실행, 계약 검사, 수락 기준 검사를 서버나 데몬이 하는 코드는 **미확인**이다.
  - brief는 "CI를 기다리지 말라, 'Local tests pass; CI running: <PR>'이면 인계 완료"라고 한다(:103).

## 9. VISION 방향

- 목표는 "사람과 에이전트를 한 팀으로", "사람-에이전트 작업의 system of record and action"이다(`VISION.md:11,89-90`).
- 앞으로 그리는 모습(`VISION.md:53-81`)은 다음과 같다.
  - Slack 대화나 고객 대화 같은 거친 의도를 에이전트가 구조화된 작업으로 만든다.
  - 불확실성을 드러낸다.
  - 위험하거나 절충이 필요한 지점에서 맞는 사람을 끌어들인다.
  - 합의가 되면 여러 에이전트가 병렬로 진행한다.
  - 사람은 상태 보고가 아니라 계획·diff·미리보기·테스트 결과를 검토하고, 다음 단계를 승인하거나 멈춘다.
  - 의도·결정·산출물 기록이 남아 다음 에이전트가 처음부터 시작하지 않는다.
  - 코드가 아닌 지식 노동(리서치, 고객 브리프, 지원 업무)으로도 넓힌다.
- 이 문서는 기능 목록이 아니라고 스스로 밝힌다(:96-98).
- 구체적인 로드맵이나 일정은 저장소에서 **미확인**이다.
- `docs/issue-status-lifecycle-rollout.md:39-41`은 "This is not a workflow engine; PR #7990 is subsequent work"라고 해서 후속 작업이 있음을 암시하지만, 그 내용은 **미확인**이다.
- VISION이 말하는 "다음 단계 승인"은 현재 코드에서 전용 객체로 찾지 못했다(4절). **[비전 > 현재 구현]**

## 정리 표

Runloom 쪽은 요청에 적힌 배경만 사용했다.

| 구분 | 항목 |
|---|---|
| **겹치는 것** | 로컬 러너(데몬)가 Claude Code·Codex CLI를 실행한다. 사람이 검토한다(`in_review`에서 사람이 done). 팀 안 다른 멤버의 에이전트에 일을 맡긴다(실행 권한 목록). 이슈 단위로 실행 기록이 남는다 |
| **Multica에만 있는 것** | Squad 리더 LLM이 매번 위임 대상을 판단한다. 멘션 체인으로 에이전트끼리 넘긴다. stage가 있는 하위 업무와 `child_done` wakeup. 에이전트가 직접 등록하는 이벤트·조건 wakeup. 실행 중 run에 메시지 넣기(supplement). 약 25종 CLI. Autopilot(cron·webhook). 채팅 채널 연동. PR 병합 시 done 자동 처리 |
| **Multica에 없어 보이는 것** (코드에서 못 찾음, 단정 아님) | 업무 종류별 고정 후속 규칙(규칙 표). 소유자 승인이나 요청 수락 단계. 담당 범위에 따른 반송·재배정 흐름. 워크스페이스(팀) 사이 요청. "조사→판단→결과 반환"을 구조화된 결과로 돌려주는 사내 요청 객체(결과는 댓글 1개). 서버·데몬의 결과 검증(테스트·계약). 승인 게이트 전용 객체 |
