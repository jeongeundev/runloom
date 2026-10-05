# ADR-0028: 설정 UX — 팀·저장소·설정을 메뉴로 꺼내고, 처음 온 관리자가 화면만으로 준비를 끝낸다

결정일: 2026-10-05 (phase 23 step 0). 근거: [사내 요청 실연동 1회차](../product/INTERNAL_REQUEST_LIVE_RUN_1.md) "결과"(화면 점검 15건), [UI_GUIDE](../UI_GUIDE.md), [ADR-0009](0009-registered-kinds-and-succession-rules.md)(종류·후속 규칙은 등록 데이터), [ADR-0021](0021-team-accounts-and-roles.md)(팀·역할·링크), [ADR-0022](0022-work-screen-and-direct-work.md)(업무 화면·연결 화면), [ADR-0023](0023-cross-member-delegation.md)(맡기기 정책), [ADR-0025](0025-triage.md)(판단), [ADR-0027](0027-next-step-triage.md)(결과 뒤 판단), [담당 범위 표](../product/RESPONSIBILITY_DIRECTORY.md). 기본값은 [phase 23 README](../../phases/23-setup-ux/README.md) "사용자 결정" D1~D4(2026-10-05)와 "계획 기본값" 11가지이며 이 문서로 구현 기준을 고정한다. 적용 범위는 `service` 브랜치. 이름·표·경로·시그니처·문구의 기준은 [ARCHITECTURE](../ARCHITECTURE.md) "설정 UX — phase 23" 이다(README 와 다르면 ARCHITECTURE).

## 계기

2026-10-05 공개 사례 K1 실연동을 셀프호스트(v24)에서 시작했지만 준비 1번(팀 초대)에서 멈췄다. 사용자 평: "보기가 너무 어려운데. 팀 초대하는것도 어렵고 그냥 하나부터 열까지 ux 개판". 관리자 계정으로 준비 화면을 돌며 찾은 문제 15건(1회차 문서 "화면 점검에서 나온 문제")은 네 묶음이다.

- **찾기**(1~3): 팀 초대가 "연결" 메뉴의 "팀·담당자" 탭 안에 있다. "연결" 첫 탭이 저장소 카드 + 이슈 목록 전체 + Jira + n8n 토큰·curl 로 4000px 를 넘는다. 판단 에이전트 칸이 저장소 카드 "고급 설정" 접힘 안에 있고 결과 뒤 판단도 이 칸이 켠다는 설명이 없다.
- **초대**(4~7): 링크는 만든 직후 한 번만 보이고 목록엔 `inv-…` 만 남는다. 누구에게 보낸 초대인지 모른다. 팀 탭 맨 위 빨간 `WORKFLOW_PUBLIC_URL` 경고가 늘 떠 있다.
- **내부 값 노출**(8~11): 에이전트 표의 `agt-…`·`code.fix · repository_id=…`, 저장소 카드 select 의 `(agt-…)`, 종류 폼의 kind 식별자·능력 코드·scope_key·산출물 체크박스 19개, 담당 범위 폼의 "시스템 식별자"·"요청 유형 식별자" 자유 입력, 저장소 고급 설정의 "GitHub 사용자 숫자 ID".
- **업무 목록**(12~15): 필터 네 묶음 + 저장소 select + [보기]. 빠른 필터 "담당 없음 20" 과 묶음 머리 "담당 없음 22" 가 다르다. 모두 같은 값인 우선순위·종류 칸. 상태 "새로 들어옴" 과 다음 할 일 "담당 없음" 의 반복. 에이전트 표 맡기기 칸이 좁아 버튼이 깨진다.

이 phase 가 끝나면 관리자가 **문서 없이 화면만 보고** 멤버 둘 초대 → 조사 종류 등록(맡을 에이전트 선택) → 담당 범위 추가 → 저장소에 판단 에이전트 지정까지 한다. K1 은 이 phase 뒤로 보류했다.

## 사용자 결정 (2026-10-05)

1. **D1 범위** — 설정 화면 + 업무 목록 정리. 점검 15건만 고친다. 저장소(프로젝트)별 판단 기준은 다음 phase.
2. **D2 메뉴** — 팀·저장소를 메뉴로 꺼낸다. 사이드바 `업무` · `받은·보낸 요청` · `모니터링` · `팀` · `저장소` · `설정` · `내 설정`(+ 조건부 `시작하기`). `팀` = 멤버·초대·에이전트·담당 범위. `저장소` = 저장소 카드(이슈 목록은 빼고 업무 화면으로). `설정` = 종류·규칙·판단·알림·n8n 입구·고급.
3. **D3 초대** — 초대할 때 받는 사람 이메일(필수)·이름(선택)을 적는다. 목록은 이메일로 보인다. 링크를 놓치면 그 줄에서 [링크 다시 만들기] — 옛 링크는 무효. 링크 원본은 저장하지 않는다(해시만).
4. **D4 종류의 맡을 에이전트** — 종류 등록 폼에서 "이 일을 맡을 에이전트" 를 체크박스로 고른다. 고른 에이전트에 그 종류의 능력을 붙인다. 능력 코드·scope_key·산출물 칸은 자동으로 채우고 "고급" 으로 접는다.

## 코드 조사로 README 와 달라진 사실 (2026-10-05, `feat-23-setup-ux` `6fe57fd` = `service` `058d9bc` + step 파일)

README "왜" 의 15건은 코드와 맞다(위치: `templates/_connect_team.html`·`_connect_github.html`·`_connect_kinds.html`·`home.html`, `web.py` `CONNECT_TABS`·`_tab_context`, `views.github_context`·`team_context`, `domain/work_list.py`). 아래는 더 들어가 확인한 사실이고 결정 몇 개를 바꾼 근거다.

1. **"담당 없음 20 vs 22" 의 원인은 빠른 필터와 묶기의 기준 차이다.** `work_list._matches` 의 `unassigned` 는 끝나지 않은 업무만 세지만(`_open`) `group_rows` 의 `담당 없음` 묶음은 끝난 업무 범위(`closed=recent` = 14일) 안의 `완료`·`종료` 담당 없음 업무까지 담는다. 끝난 업무를 어느 묶기에서든 맨 아래 한 묶음으로 빼면 두 수가 같은 기준이 된다(결정 9). 상태 묶기는 지금도 `완료`·`종료` 를 각각의 묶음으로 두므로, 그 둘도 "끝난 업무" 하나로 합친다. **보드는 바꾸지 않는다** — `BOARD_COLUMNS` 에 `완료` 칸이 있고 `종료` 는 보드에 없다(묶음 개념이 없다).
2. **상태 이유는 목록에 보이지 않는다.** 목록의 상태 칸은 배지(`st.badge(row.status)`)만 그리고, 이유(`status_reason`)는 `next_action` 의 마지막 대체값으로만 보인다(`work_list.next_action` — 질문 → 직접 작업 → PR → 상태 이유). README 의 "다음 할 일 문구가 상태 이유와 같으면 비운다" 를 그대로 쓰면 `버그 수정 실행 중`·`PR 확인 #12`·`선행 대기` 같은 이유가 목록에서 사라진다. 그래서 **같은 행의 다른 칸이 이미 말하는 것만 비운다**: 다음 할 일이 상태 이름과 같을 때(`대기` / `대기`), 그리고 담당 없음 행의 다음 할 일이 `담당 없음` 일 때(담당 칸 `없음` 과 같은 말). 나머지 이유는 그대로 둔다(결정 9).
3. **`members` 의 이메일 유일성은 비활성 멤버까지 덮는다.** `members_session_email` 은 `(session_id, email) WHERE email IS NOT NULL` 부분 UNIQUE 이고 `disabled_at` 을 보지 않는다. README 의 "활성 멤버 이메일과 겹치면 거부" 로 하면 비활성 멤버 이메일로 초대가 만들어지고 가입 때 `EmailTaken` 으로 막힌다. 그래서 **같은 워크스페이스의 멤버(활성·비활성) 이메일과 겹치면 초대를 거부**한다(결정 3).
4. **`member_invites` 에 ALTER 로 CHECK 를 더할 수 있다.** SQLite 3.37 이후 `ADD COLUMN … CHECK (…)` 는 기존 행으로 검사되고 다른 칸(`purpose`)을 참조할 수 있다(시스템 `python3` 의 SQLite 3.45.3 으로 확인). 그러나 "초대 행이면 이메일 필수" 는 기존 열린 초대(이메일 NULL)가 어기므로 CHECK 로 둘 수 없다. 그래서 **CHECK 는 "이메일·이름은 초대 행에만, 이메일은 정규화된 값" 만** 두고 "새 초대는 이메일 필수" 는 `repo.issue_invite` 가 검사한다(결정 3).
5. **초대 함수의 `session_id` 는 위치 인자다.** 지금 `issue_invite(conn, session_id, *, role, …)`·`revoke_invite(conn, session_id, invite_id, *, now)` 이다. README 의 `issue_invite(conn, *, session_id, …)`·`reissue_invite(conn, *, invite_id, now)` 대신 기존 모양을 따라 `session_id` 를 위치 인자로 두고, `reissue_invite` 도 `revoke_invite` 처럼 `session_id` 로 범위를 묶는다(다른 워크스페이스의 초대 id 로 링크를 만들지 못하게).
6. **트랜잭션이 겹치지 않는다.** `repo._tx` 는 `BEGIN IMMEDIATE` 를 그대로 열어 중첩되지 않는다. "종류 등록과 능력 붙이기를 한 트랜잭션" 은 서버가 두 함수를 이어 부르는 것으로는 안 된다. 그래서 **`insert_kind` 에 `agent_ids` 키워드를 더해** 같은 트랜잭션 안에서 능력을 붙이고, 능력 쓰기는 트랜잭션 없는 내부 함수(`_add_capability`·`_remove_capability`)를 공개 함수가 감싼다(결정 5).
7. **러너 에이전트의 범위 값은 내장 능력 셋이 같은 `repository_id` 를 가진다.** `register_local_agent` 가 `code.fix`·`code.review`·`code.triage` 를 `{"repository_id": <러너가 보고한 값>}` 으로 만들고, 재등록(`update_registration`)은 능력을 유지한다. 그래서 **범위 값 = 내장 종류 `capability_code`(`BUILTIN_KINDS`)에 해당하는 능력들의 `repository_id` 가 한 값일 때 그 값**, 없거나 둘 이상이면 고를 수 없다. 이 계산은 시각·DB 가 필요 없어 `domain/kinds.py` 에 둔다.
8. **사용자 정의 종류는 러너 선언 없이 돈다.** 러너는 claim 때 내장 종류만 `supported_kinds` 로 선언하고 사용자 정의 종류는 `LocalTarget` 으로 실행한다(`connector/adapter.py` `SUPPORTED_BUILTIN_KINDS` 주석). K1 1회차 준비가 조사 종류의 능력 코드를 `code.review` 로 "우회" 한 이유는 러너 에이전트에 새 능력을 붙일 화면이 없어서였다 — 결정 5 가 이 우회를 없앤다. 다만 지금 저장된 사용자 정의 종류가 내장 능력 코드(`code.review` 등)를 쓸 수 있으므로 능력 떼기는 내장 코드를 늘 보호한다.
9. **설정 번호를 올리는 영역에 "에이전트" 가 없다.** `config_changes.area` 의 CHECK 는 `kind`·`rule`·`source`·`mapping`·`triage_criteria`·`triage_autostart` 뿐이고 영역을 더하려면 표를 재생성해야 한다. 이 phase 는 `member_invites` 만 ALTER 한다는 기본값을 지키므로 **에이전트 능력 붙이기·떼기는 설정 번호를 올리지 않는다**(종류 등록은 지금처럼 `kind` `add` 로 오른다 — 함께 붙인 능력은 그 번호 안에 든다). 비용으로 남긴다.
10. **요청 유형 값은 코드에 고정 목록이 없다.** `Responsibility.request_kind` 는 `Identifier` 자유 값이고, 판단 요청문(`domain/next_step`)·`internal_request_store` 는 담당표 값을 그대로 옮긴다. 문서·테스트에 나오는 값은 `investigation`([담당 범위 표](../product/RESPONSIBILITY_DIRECTORY.md)·[사내 요청](../product/INTERNAL_REQUESTS.md)·K1 1회차), `data_check`(결과 뒤 판단 테스트의 사내 요청 예), 공개 사례의 "버그 보고"(`bug_report`)다. 고정 목록은 이 셋으로 정하고 **화면 폼에만** 건다 — JSON API(`PUT /responsibilities`)는 지금처럼 식별자 형식만 본다(결정 7).
11. **수집한 이슈에 GitHub 담당자 id·login 이 저장된다.** `source_issues.snapshot_json` 은 `GitHubIssueSnapshot` 이고 `assignee_ids`·`assignee_logins` 를 짝으로 담는다(개수가 다르면 계약 오류). 그래서 담당 연결은 **수집한 이슈에서 본 GitHub 사용자 줄마다 수정 에이전트를 고르는 폼**으로 바꿀 수 있다. 목록에 없는 사용자를 위한 숫자 ID 입력은 그 아래에 이름을 고쳐 남긴다(결정 8).
12. **저장소 카드 설정은 JSON 폼이라 중첩 폼을 둘 수 없다.** 카드 설정은 `data-json-action` PUT 폼(`base.html` 스크립트가 JSON 으로 보냄)이고 러너 다시 붙이기·담당 연결·기준선·수집 중지는 각각 다른 폼이다. HTML 은 폼 안에 폼을 둘 수 없으므로 README 의 "고급 접힘 하나" 대신 **카드 설정 폼 안의 "고급 설정" 접힘(설정 칸)** 과 **폼 밖의 "러너·담당 연결·기준선" 접힘(다른 폼들)** 둘로 나눈다(겹치지 않게 — 지금 템플릿 주석의 "`<details>` 는 겹치지 않는다" 규칙 유지).
13. **매칭 줄과 select 가 에이전트 id 를 그대로 보인다.** `views._match_rows` 의 `value` 는 `default_fix_agent_id`·`review_agent_id`·`triage_agent_id` 값(`agt-…`)이고, 카드 select 옵션은 `이름 (agt-…)` 이다. 이름으로 바꾸고 id 는 "자세히" 로 옮긴다(결정 8).
14. **노출 검사는 속성을 빼고 봐야 한다.** 폼 action(`/agents/agt-…/delegation-policy`)·href·`data-agent="agt-…"`·`<option value="agt-…">` 는 동작에 필요한 값이라 HTML 원문을 정규식으로 보면 늘 걸린다. 그래서 노출 단정은 **"자세히" 요소를 지운 뒤 태그를 지운 화면 글자** 를 본다(결정 11).
15. **연결 화면 밖 템플릿도 `/connect?tab=` 로 링크한다.** `_work_panel.html`·`internal_requests.html`·`metrics.html`·`_monitor_triage.html`·`task_new.html`·`agent_detail.html`·`operator_github_app_new.html`·`home.html`. 옛 주소는 303 으로 넘어가므로 깨지지는 않지만 step 1 이 href 만 새 주소로 바꾼다(그 화면들의 문구·배치는 범위 밖).

## 결정

1. **주소.** 새 GET 화면 `/team`(지금 303 라우트 자리를 화면이 대신한다)·`/repos`·`/settings?tab=kinds|triage|notify|inbound|advanced`. `/connect`·`/connect?tab=X`·옛 GET 주소는 새 주소로 303(표는 ARCHITECTURE — 판단 탭의 `version` 쿼리만 유지, 나머지 쿼리는 버린다). **POST 경로는 하나도 바꾸지 않는다**(GitHub App `new`·`callback`·`setup` 포함) — POST 뒤 303 대상과 POST 가 그리는 화면만 새 화면으로. 새 POST 경로는 둘뿐이다: `POST /team/invites/{invite_id}/reissue`(링크 다시 만들기), `POST /agents/{agent_id}/capabilities`(맡을 수 있는 일). 사이드바 활성 표시는 화면이 넘기는 `nav` 값(`team`·`repos`·`settings`), 없으면 지금처럼 경로 접두사.
2. **권한.** `CONNECT_TABS` 의 탭별 권한을 화면·탭·절 단위 표로 옮긴다(ARCHITECTURE 권한 표). `팀`·`저장소` 화면은 로그인 멤버 누구나 열고, 바꾸는 동작은 지금 권한 그대로(`manage_team`·`manage_connections`·`manage_rules`·`attach_runner`·`can_set_policy`). 새 동작 "맡을 수 있는 일" 편집은 종류 등록과 같은 `manage_rules`. `설정` 은 판단 탭 `manage_connections`, 알림 탭 `manage_shared_notify`, n8n 입구 탭 `manage_connections`(지금 그 절의 권한), 종류·고급 탭은 로그인(절별 권한은 그대로). 권한 없는 탭·절은 숨기고, 권한 없는 탭을 직접 열면 403 `forbidden`, POST 는 지금처럼 403.
3. **초대 스키마 v25.** `member_invites` 에 `invitee_email TEXT`·`invitee_name TEXT` 를 ALTER 로 더한다(다른 표는 건드리지 않는다). CHECK: 이메일은 NULL 이거나 `purpose = 'invite'` 이고 `lower(trim(…))` 과 같고 빈 값 아님, 이름은 NULL 이거나 `purpose = 'invite'` 이고 빈 값 아님. 새 초대의 이메일 필수는 애플리케이션 검증(사실 4). 기존 행은 NULL 그대로 — 열린 옛 초대는 목록에 "이메일 없음(옛 초대)" 로 보이고 다시 만들기·취소가 된다.
   - 정규화: 이메일은 `team.normalize_email`(앞뒤 공백 제거·소문자·형식 검사), 이름은 `team.clean_display_name`(앞뒤 공백 제거·1~40자), 빈 이름은 NULL.
   - 겹침: 같은 워크스페이스 멤버(활성·비활성) 이메일 → `EmailTaken`, 같은 워크스페이스의 **열린 초대**(미사용·미취소·미만료) 이메일 → 새 오류 `InviteEmailTaken`. 같은 트랜잭션에서 본다.
   - `reissue_invite(conn, session_id, invite_id, *, now) -> str` — 같은 행의 `token_sha256`·`expires_at`(지금 + `INVITE_TTL_DAYS`) 을 바꾸고 새 원본을 돌려준다. 이 워크스페이스의 열린 초대(`purpose = 'invite'`·미사용·미취소·미만료)가 아니면 `NotFound`(재설정 링크 행·다른 워크스페이스 포함 — `revoke_invite` 와 같은 오류). 옛 토큰은 더 찾히지 않는다.
   - `accept_invite` — 초대에 이메일이 있으면 입력 이메일의 정규화 값이 그 이메일과 같아야 한다(다르면 새 오류 `InviteEmailMismatch`). 이메일 없는 옛 초대는 지금처럼 입력 이메일.
4. **초대 화면.** 팀 화면 멤버 절 아래 "초대" 절: 이메일(필수)·이름(선택)·역할 + [초대 링크 만들기]. 만든 응답 화면(303 없음)에 링크 + [복사] + 안내 문구. `WORKFLOW_PUBLIC_URL` 미설정 안내는 빨간 `.alert` 가 아니라 링크 바로 아래 회색 한 줄 — 링크가 보일 때만(재설정 링크도 같은 규칙). 팀 화면 맨 위 빨간 경고는 없앤다. 대기 중 초대 줄 = 이메일 · 이름 · 역할 · 만료 + [링크 다시 만들기] + [취소], `inv-…` 는 줄의 "자세히" 안. 가입 화면은 초대 이메일을 `readonly` 로 채우고 이름이 있으면 표시 이름 기본값, 안내 한 줄 "<초대한 사람> 이 <역할> 로 초대했습니다". 초대 이메일 발송은 하지 않는다.
5. **에이전트 능력 하나씩.** `repo.add_agent_capability(conn, *, agent_id, capability) -> bool`(이미 같은 `code`·`scope` 가 있으면 False — 멱등), `repo.remove_agent_capability(conn, *, agent_id, capability) -> bool`(없으면 False). 둘 다 `capabilities_json` 을 읽어 하나만 더하거나 빼고 나머지 순서는 그대로 쓴다. **내장 능력 보호**: `capability.code` 가 `BUILTIN_KINDS` 의 `capability_code` 중 하나면 `remove` 는 `CapabilityProtected` — 종류 이름으로 가르지 않는다. 범위 값은 `domain.kinds.agent_repository_scope(capabilities) -> tuple[str | None, str | None]`(값, 이유 코드 `no_repository`·`many_repositories`) — 사실 7. 고를 수 없는 에이전트는 체크박스 비활성 + 이유(ARCHITECTURE 문구), 위조 POST 는 거부. 기존 `upsert_agent`·`POST /operator/agents`(전체 덮어쓰기)는 그대로 — 고급 탭 수동 등록은 붙인 능력을 덮어쓴다(비용).
   - **종류 삭제** `delete_kind` 는 같은 트랜잭션에서 그 종류의 `capability_code` 능력을 워크스페이스 에이전트 모두에서 뗀다. 단 그 코드가 내장 능력 코드이거나 남은 다른 종류가 같은 `capability_code` 를 쓰면 떼지 않는다.
6. **종류 폼.** 기본 칸 = 화면 이름(필수) · 지시문 · 결과값(기본 채움 `done, needs_information`) · 맡을 에이전트(체크박스, 줄 = `이름 · 저장소 · <소유자>의 Mac`, 소유자 없으면 `공용`). "고급" 접힘 = 종류 식별자(비우면 자동) · 능력 코드(비우면 종류 식별자) · scope_key(기본 `repository_id`) · 받는 산출물(라벨 먼저, 내부 이름은 흐리게). 자동 식별자 = `k_` + 소문자 hex 6자(`KIND_PATTERN` 안), 이미 있으면(내장 이름 포함) 다시 뽑기 최대 5번, 그래도 겹치면 409 `kind_exists`. 화면 이름이 워크스페이스의 다른 종류(내장 포함)와 같으면(앞뒤 공백 제거·대소문자 무시) 409 `kind_label_exists`. 맡을 에이전트 0명은 허용 — 안내 "맡을 에이전트는 나중에 팀 화면에서 붙일 수 있습니다". scope_key 가 `repository_id` 가 아니면 에이전트 선택을 막고(서버도 422) 고급 탭 수동 등록을 안내한다. 등록과 능력 붙이기는 `insert_kind(..., agent_ids=…)` 한 트랜잭션. 종류 카드는 화면 이름 · 받는/내는 산출물 라벨 · 결과값 · 맡을 수 있는 에이전트 이름들, `kind`·`capability_code`·`scope_key` 는 카드의 "자세히". 내장 종류 카드도 같다(정의·능력은 바꾸지 않는다).
7. **팀 화면.** 절 순서: 멤버 → 초대 → 에이전트 → 담당 범위. 에이전트 표 한 줄 = 이름(상세 링크) · `<소유자>의 Mac`(없으면 `공용`) · `켜짐`|`꺼짐` + 마지막 확인 · 맡을 수 있는 일(능력 코드에 해당하는 종류 화면 이름들) · 맡기기 정책(바꿀 수 있는 사람에게 select + 버튼, 줄바꿈 없는 너비) · 동작(러너 [해제]). **러너 표를 합친다**: 에이전트 줄의 러너 = `agents.connector_id`. 에이전트 없는 러너(연결됐지만 아직 등록 폴더 보고 전, 또는 에이전트를 지운 러너)는 같은 표에 `러너 · <소유자>의 Mac · 아직 에이전트 없음` 줄로, 러너 없는 에이전트(수동 등록·API)는 상태 칸 `러너 없음`. 해제된 러너는 에이전트 없는 러너 줄로 그리지 않고(그 러너의 에이전트 줄은 [해제] 없이 남고 해제 시각은 "자세히"), 여러 에이전트가 한 러너를 쓰면 [해제] 는 그 러너의 첫 줄에만. ID·능력 코드·connector id 는 줄의 "자세히". 관리자(`manage_rules`)에게 줄마다 "맡을 수 있는 일" 편집 — 사용자 정의 종류 중 `capability_code` 가 내장 능력 코드가 아니고 `scope_key == "repository_id"` 인 것만 체크박스, 저장 = `POST /agents/{agent_id}/capabilities`. 담당 범위 폼: 시스템(넓은 입력, placeholder `kube_proxy`, help "소문자·숫자·밑줄, 소문자로 시작") · 요청 유형(select — `investigation` 조사 · `bug_report` 버그 보고 · `data_check` 데이터 확인) · 받는 사람 · 판단 담당자 · 조사 에이전트(이름만, 선택). 표는 멤버·에이전트 이름과 요청 유형 라벨만. 목록 밖 기존 행은 값 그대로 + 흐린 `(목록 밖)` — 지울 수 있고 화면에서 새로 만들 수는 없다(API 는 그대로 받는다).
8. **저장소 화면.** 위쪽 = GitHub 연결 상태·[GitHub 연결]/[저장소 추가/변경], Jira 연결(지금 Jira 절 그대로), 접힌 "고급 — 토큰으로 연결". 카드 = 저장소 이름 · 수집 상태(켜짐|멈춤 · 마지막 동기화) · `업무 N건 보기`(→ `/tasks?repo=<owner/name>`, N = 그 저장소의 끝나지 않은 업무 수) · 카드 설정 폼(`manage_connections`): **판단 에이전트** select + 설명 "새로 들어온 업무의 담당·종류를 제안하고, 결과가 규칙 밖이거나 정보가 모자라거나 사내 요청이 돌아오면 다음 단계를 제안합니다" · 수정 에이전트·검토 에이전트(이름만) · 실행 방식 · 폼 안 "고급 설정" 접힘(로컬 저장소 ID · 수정 검증 프로필 · 트리거 라벨 또는 라벨·고른 이슈·시작 시각 · 재작업 상한 · 수집 켜기) · [미리보기]·[저장]. 폼 밖 "러너·담당 연결·기준선" 접힘(러너 다시 붙이기 · 담당 연결 · 기준선 · 수집 중지). 권한 없는 멤버에게는 같은 값을 글자로. 카드 안 이슈 목록·[에이전트에게 맡기기] 는 뺀다(업무 화면이 한다). 담당 연결 = 수집한 이슈에서 본 GitHub 사용자 줄(`login` · 지금 연결된 에이전트 이름 · 수정 에이전트 select · [연결]) + 그 아래 "목록에 없는 사용자" 숫자 ID 폼(라벨 `GitHub 사용자 번호`·help 로 찾는 법). `GitHubSourceConfig` 계약·PUT 경로·`expected_revision` 잠금은 그대로.
9. **업무 목록.** 끝난 업무(`TERMINAL_WORK_STATUSES`)는 어떤 묶기에서도 맨 아래 묶음 하나(키 `closed`, 이름 `끝난 업무`)로 모으고 기본은 접힘(`<tbody>` 에 `data-collapsed` — JS 없으면 펼친 채). 빠른 필터 건수와 끝나지 않은 묶음 머리 건수가 같은 기준이 된다(사실 1). 보이는 행(빠른 필터·저장소 필터 뒤 목록·보드에 그리는 행)이 모두 같은 값인 칸(`priority`·`kind`)은 목록 머리·행·보드 카드에서 숨긴다(행 0 이면 숨기지 않는다). 다음 할 일은 사실 2 의 두 경우만 `—`. 도구 막대 = 빠른 필터 · 저장소 select(바꾸면 바로 이동 — 소량 JS, `<noscript>` 안 [보기]) · [업무 등록] + `<details class="view-options">` "보기 옵션"(묶기 · 목록/보드 · 끝난 업무). 주소 쿼리(`q`·`group`·`repo`·`view`·`closed`·`open`)는 그대로. 업무 상태 계산·내 차례 규칙은 바꾸지 않는다.
10. **설정 화면.** 탭 순서·키·이름: `kinds` 업무 종류·규칙 · `triage` 판단 · `notify` 알림 · `inbound` n8n 입구 · `advanced` 고급. 모르는 탭·탭 없음은 볼 수 있는 첫 탭(늘 `kinds`). n8n 입구 탭 = 지금 가져올 곳 탭 아래의 입구 주소·토큰 목록·토큰 발급·요청 예시·callback 허용 목록. 규칙 표 글은 화면 이름·결과값으로(`버그 수정 — 결과 ready_for_review → 커밋 검토`), 넘기는 산출물은 라벨. 판단·알림·n8n 입구 탭의 내부 ID·코드(토큰 id 등)는 "자세히" 로. 고급 탭은 예외(수동 등록 폼·연결 코드 그대로, 맨 위 "개발·운영용" 한 줄).
11. **"자세히" 표식과 노출 단정.** 내부 값을 두는 곳은 `<details class="detail"><summary>자세히</summary>…</details>` 하나로 고정한다(안에 다른 `<details>` 를 두지 않는다). step 10 의 노출 단정은 응답 HTML 에서 ① `<script>`·`<style>` 블록 ② `<details class="detail"…>…</details>` 요소를 지우고 ③ 태그를 지운 글자에 `agt-[0-9a-f]|conn-[0-9a-f]|inv-[0-9a-f]|code\.(fix|review|triage)` 가 없음을 본다(사실 14). 대상: 팀·저장소·설정(고급 탭 제외)·업무 목록 본문. 표는 `.table-wrap` 안(390px 에서 페이지 가로 스크롤 없음 — CSS·마크업 단정).

## 하지 않는 것

- 저장소별 판단 기준(다음 phase), 관리자의 "멤버로 보기", 초대 이메일 발송(SMTP), 링크 원본 보관, 에이전트 능력 일괄 편집 API(JSON).
- 러너 프로토콜 변경 — 러너 재설치 불필요(`contracts/v1.py` claim·실행 요청·결과 계약, `connector/` 그대로).
- 업무 패널·단계 상세·`/requests`·모니터링 화면 재설계 — 그 화면의 `/connect?tab=` 링크 href 만 새 주소로(문구 그대로).
- 보드 칸 구성 변경, 업무 상태 계산·내 차례 규칙 변경, 맡기기 정책·소유자 승인 규칙 변경, 판단 기준·알림 저장 동작 변경.
- `config_changes` 영역 추가(사실 9), `Responsibility` 계약의 요청 유형 제한(사실 10 — 화면 폼만).
- 새 의존성(JS·CSS 프레임워크·아이콘 라이브러리·CDN), 실제 GitHub·Claude·Jira·Discord 호출, K1 실연동(phase 뒤 사용자 지시).

## 결과

**좋은 점**

- 메뉴 이름이 하는 일과 같다 — 초대는 `팀`, 판단 에이전트는 `저장소`, 종류는 `설정`. 4000px 탭이 사라지고 이슈 목록은 업무 화면 한 곳이다.
- 초대 목록이 이메일로 보이고 링크를 놓쳐도 그 줄에서 다시 만든다. 원본은 여전히 저장하지 않는다.
- 종류 등록 한 번으로 맡을 에이전트까지 정해진다 — K1 1회차의 `code.review` 우회가 필요 없다.
- 내부 값은 "자세히" 한 곳에만 있어 화면 단정 한 규칙으로 회귀를 막는다.
- 업무 목록의 건수가 한 기준이고 반복 칸이 줄어든다.

**비용**

- 옛 북마크 주소가 바뀐다(303 으로 넘어간다). 연결 화면을 단정하던 테스트를 새 주소 기준으로 옮긴다(옛 주소 단정은 303 대상 단정으로).
- 능력 붙이기·떼기는 설정 번호를 올리지 않는다 — 모니터링의 설정 번호 그룹에 "누가 어느 에이전트에 능력을 붙였는지" 는 남지 않는다(종류 등록과 함께 붙인 경우만 그 종류 번호 안).
- 고급 탭 수동 등록(`POST /operator/agents`)은 여전히 능력 전체를 덮어쓴다 — 화면으로 붙인 능력이 사라질 수 있다(고급 탭 안내 한 줄로 알린다).
- 요청 유형 고정 목록이 셋뿐이다 — 늘리려면 상수 한 줄과 라벨을 더한다. 목록 밖 기존 행은 화면에서 새로 만들 수 없다.
- 끝난 업무 묶음이 기본 접힘이라 상태 묶기의 `완료`·`종료` 를 한눈에 보려면 한 번 펼친다.

**셀프호스트 업그레이드 영향(v24 → v25)**

- 스키마: `member_invites` 칸 두 개 ALTER 만. 업무·멤버·초대 행 수 그대로, 열린 옛 초대는 이메일 없이 남는다. 백업 먼저(`backup create`) → `install.sh`. 러너 재설치 불필요(러너 프로토콜 변화 없음).
- 사용자가 11:20 에 만든 초대(`inv-93013b36ea37`, 링크 분실)는 업그레이드 뒤 그 줄의 [링크 다시 만들기] 로 살리거나 [취소] 한다.
- 북마크 `/connect?tab=…` 는 새 주소로 넘어간다. GitHub App 에 등록된 콜백·설치 주소는 그대로다.
- 절차 문서는 step 10 이 [SELFHOST](../SELFHOST.md) "업그레이드" v25 로 쓴다.
