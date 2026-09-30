# Phase 16 — 업무 화면: 한 줄 목록·담당자별 묶음·보드·상세 패널·연결 화면·직접 작업

작성일: 2026-09-30. 상태: 구현 계획 작성 완료, 모든 step pending. **`service`(15-team 병합, 스키마 v11)에서 실행한다.** 근거: [재설계 계획](../../docs/product/REDESIGN_PLAN.md) 4·10·13절, 목업 3판 https://claude.ai/artifact/Jx6Pa7PmRZo1hmvFuiH66C (비공개), [ADR-0020](../../docs/adr/0020-work-items-and-stages.md), [ADR-0021](../../docs/adr/0021-team-accounts-and-roles.md).

## 조사로 확인한 현재 (2026-09-30, `service` fb4d0bc)

- 사이드바(`templates/_sidebar.html`)에 탐색 10개(업무·에이전트·종류·규칙·입구·운영자·GitHub·지표·알림·팀·내 설정)와 "최근" 업무 목록(`_base()["my_work"]` = 전체 업무)이 있다. 기능 단위로 흩어져 사용자가 할 일이 안 보인다(REDESIGN_PLAN 1절).
- 홈 `/tasks`(`web.home`, `home.html`)는 에이전트 카드 · 워크플로우(체인) 카드 · 업무 카드 목록(한 줄 = 업무, phase 14)과 빠른 필터 `내 차례`/`전체` 두 개. 담당자별 묶음·보드·표 형태 없음.
- 업무 상세 `/work/{key}`(`work_detail.html`)는 전체 페이지. 단계 상세 `/tasks/{task_id}` 는 3열 셸(오른쪽 산출물 뷰어)과 라이브 조각(`/tasks/{id}/live`).
- **담당·우선순위를 바꾸는 화면·경로가 없다.** `repo.assign_work_item` 은 라우트가 부르지 않는다. 맡기기는 두 갈래: GitHub 이슈 단계는 `POST /tasks/{id}/delegate`(지시 기록 + `Worker.start_manually`), 직접 등록 단계는 `POST /tasks/{id}/select` → `POST /tasks/{id}/run`.
- 업무 상태(`domain/work_status.py`)는 8개 값 중 `직접 작업 중` 을 예약만 했다. PR 은 단계 단위 표 `task_pull_requests`(Runloom 이 연 초안 PR)뿐이라 **사람이 연 PR 을 업무에 붙일 곳이 없다**. GitHub 동기화(`server/github_sync.py`)는 이슈 목록·선택 이슈·닫힌 이슈의 병합 PR(`get_issue_pr_link`)만 읽는다.
- `work_item_events.type` CHECK 는 `status_changed`·`assigned` 둘. 이 표를 참조하는 다른 표는 없다.
- 알림·원본 댓글의 링크는 단계 주소 `/tasks/{task_id}` 다(`worker.py`·`github_delivery.py`).
- 지표 `/metrics`(+`.json`·`.csv`), 연결 설정은 `/sources`·`/operator`·`/operator/github`(+App 만들기·콜백·설치 경로)·`/operator/notifications`·`/team`·`/agents`·`/kinds`.
- 브라우저 JS 는 `base.html` 안의 인라인 스크립트(뷰어·라이브 갱신) 정도. 브라우저 자동 테스트는 없다(pytest 가 렌더된 HTML 을 단정).

## 계획 기본값 (사용자 결정 2026-09-30, step 0 이 ADR-0022 로 고정)

1. **업무 화면 = `/tasks` 의 한 줄 표.** 칸: 키(원본 키가 있으면 원본 키 + 원본 표시, 없으면 `RUN-n`) · 제목 · 담당 · 우선순위 · 종류 · 상태 · 다음 할 일 · 업데이트. 묶기: 담당자(맨 위 "담당 없음", 그다음 사람, 그다음 에이전트) / 상태. 빠른 필터: 전체 · 내 차례(로그인 멤버가 받는 사람) · 담당 없음 · 에이전트 작업 중. 보기: 목록 / 보드. 필터·묶기·보기·열린 업무는 주소에 남는다(`?q=&group=&view=&open=`). "다음 할 일" 칸은 열린 사람 요청 질문 또는 상태 이유(판단 제안 칸은 18-triage).
2. **상세는 오른쪽 패널.** 목록은 그대로 두고 오른쪽에 패널이 열린다. 주소 `/tasks?open=RUN-12` — 새로 고침·링크 공유도 같은 상태. 서버가 `open` 을 보고 패널을 함께 렌더하므로 JS 없이도 열린다. JS 는 행을 누를 때 패널 조각을 불러와 끼우고 주소만 바꾼다. `/work/RUN-12` 는 `/tasks?open=RUN-12` 로 넘긴다. 알림·원본 댓글 링크도 업무 주소로.
3. **보드는 보기 전용.** 칸: 대기·새로 들어옴 / 에이전트 작업 중 / 직접 작업 중 / 내 차례 / PR · 검토 / 완료. 끌어 옮기기 없음 — 업무 상태는 계산값(ADR-0020). 카드를 누르면 패널.
4. **담당을 에이전트로 고르면 곧 맡기기.** 패널의 담당 선택에서 에이전트를 고르면 그 업무의 다음 단계에 그 에이전트를 지정하고 바로 시작한다(GitHub 이슈면 지시 기록 포함 — 지금의 delegate·select·run 을 한 경로로). 사람을 고르면 배정만. 실행 중인 업무는 담당을 바꿀 수 없다. 우선순위도 패널에서 바꾼다. 관리자·멤버 모두(`delegate` 동작).
5. **연결 화면으로 모은다.** `/connect` 탭: 가져올 곳(GitHub 저장소·n8n 입구) / 팀·담당자(멤버·초대·에이전트·러너) / 업무 종류·규칙 / 알림 / 고급(연결 코드·입구 토큰 등 운영자 화면의 나머지). 옛 GET 주소(`/sources`·`/operator`·`/operator/github`·`/operator/notifications`·`/team`·`/agents`·`/kinds`)는 새 탭으로 303. GitHub App 만들기·콜백·설치 경로와 모든 POST 경로는 주소를 바꾸지 않는다(GitHub 에 등록된 URL·기존 폼). 지표는 이름만 **모니터링**(`/monitor`, `/metrics` → 303, `.json`·`.csv` 그대로).
6. **사이드바**: 업무(내 차례 수) · 모니터링 · 연결 · 시작하기 · 내 설정 + 로그인 멤버·로그아웃. "최근" 목록은 없앤다(업무 화면이 목록). 업무 등록은 업무 화면 도구 막대의 버튼.
7. **시작하기** `/start`: 가져올 곳 연결 → 러너 붙이기 → 팀원 초대(선택) → 첫 업무 맡기기. 각 항목의 완료는 DB 에서 계산한다. 필수 항목이 다 끝나면 사이드바에서 숨긴다(주소는 남음).
8. **직접 작업 = 버튼 + PR 신호.** 패널 [내 세션에서 작업] → 담당 = 누른 멤버, 상태 `직접 작업 중 · 이름`, 키가 든 브랜치 이름(`RUN-15-<영문 요약>`)을 복사해 준다. 다른 멤버가 담당이어도 누른 사람으로 바뀐다. 에이전트 실행 중이면 누를 수 없다. [직접 작업 그만두기]·[에이전트에게 넘기기]. GitHub 동기화가 소스 저장소의 PR 을 읽어 **브랜치 이름이나 제목에 업무 키가 든 PR** 을 업무에 붙인다: 열림 → `PR · 검토`, 병합 → `완료`, 병합 없이 닫힘 → 직접 작업이면 `직접 작업 중` 으로. 브랜치 push 만으로는 감지하지 않는다. 직접 작업은 Runloom 결과 판정을 거치지 않는다 — 완료는 PR 병합으로만.
9. **스키마 v12**(ALTER + 새 표): `work_items` 직접 작업 칸(누가·언제·브랜치), 업무에 붙은 PR 표(Runloom PR·감지 PR 공용 보기 또는 감지 PR 전용 — step 0 확정), 소스별 PR 읽기 커서, 업무 이벤트 종류 확장(`work_item_events` 는 다른 표가 참조하지 않아 CHECK 를 넓히려 재생성해도 된다 — `tasks` 재생성 금지는 그대로).

**하지 않는 것**: 판단 제안·확신도·자동 시작(18-triage), Jira(17-jira), 모니터링 확장(19-monitor), Claude Code 훅 상태 보고(다음 phase 로 미룸 — 사용자 결정 2026-09-30), 보드 끌기, 브랜치 push 감지, 여러 행 선택·일괄 변경, 새 JS 프레임워크·번들러·CDN.

## Step 목록

| Step | 이름 | 산출물 |
|---|---|---|
| 0 | work-ui-design | ADR-0022, ARCHITECTURE "업무 화면 — phase 16"(주소·이름·스키마 v12·상태 규칙), UI_GUIDE 셸 갱신, GLOSSARY |
| 1 | schema-v12 | 직접 작업 칸·업무 PR 표·PR 커서·이벤트 종류, v11 → v12 |
| 2 | work-list-model | `domain/work_list.py`(필터·묶기·보드 칸, 순수), 목록 행 조회·보기 모델 |
| 3 | assign-priority | 담당(사람 배정 / 에이전트 = 맡기기)·우선순위 바꾸기 경로 |
| 4 | work-list-ui | `/tasks` 표·묶음·필터·목록/보드·주소 상태, 새 사이드바 |
| 5 | detail-panel | 패널 조각·`?open=`·타임라인·원본에 남긴 것·이어서 생긴 업무·양식, `/work/{key}` 넘김, 알림 링크 |
| 6 | connect-tabs | `/connect` 탭 5개, 옛 주소 넘김 |
| 7 | monitor-start | `/monitor` 이름 변경, `/start` 체크리스트 |
| 8 | direct-work | [내 세션에서 작업]·그만두기·넘기기, 업무 상태 `직접 작업 중` |
| 9 | pr-signal | 소스 저장소 PR 읽기 → 키 매칭 → 업무 PR·상태 |
| 10 | work-ui-verify | e2e 한 줄기, v11 사본 마이그레이션, SELFHOST·UI_GUIDE·인계 |

## 실행

```bash
git status --short            # 깨끗해야 한다
git branch --show-current     # service
python3 scripts/execute.py 16-work-ui --engine claude
```

사용자가 띄워 둔 셀프호스트(compose 프로젝트 `runloom`)·러너(launchd)·`/Users/kje/demo/*` 는 건드리지 않는다. 병합·셀프호스트 재설치(v12, 백업 먼저)는 phase 뒤 사용자 지시로.
