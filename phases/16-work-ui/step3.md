# Step 3: assign-priority — 담당·우선순위 바꾸기 — 에이전트 담당 = 맡기기

## 읽어야 할 파일

- AGENTS.md
- phases/16-work-ui/README.md (조사 결과·계획 기본값 9가지 — 이 phase 의 기준), phases/16-work-ui/index.json (이전 step summary)
- docs/ARCHITECTURE.md ("업무 화면 — phase 16" 담당 바꾸기 규칙·주소 표, "팀 — phase 15" 맡긴 사람·역할 표)
- src/workflow/server/web.py (`task_delegate`·`task_select`·`task_run`·`_run_cycle_task`·`_run_task`·`_candidates`·`_target_for`·`_refresh_status`·`_redirect`), src/workflow/server/worker.py (`Worker.start_manually`), src/workflow/server/task_cycle.py
- src/workflow/adapters/repo.py (`assign_work_item`·`set_work_requester`·`mark_issue_delegated`·`save_selection`·`update_task_choice`·업무 이벤트 기록·업무 상태 재계산 함수)
- src/workflow/domain/selection.py, src/workflow/domain/work_status.py, src/workflow/domain/team.py
- tests/workflow/server/test_web.py·test_web_cycle.py·test_web_my_turn.py·conftest.py

먼저 실제 파일을 읽는다. 대화 이력을 전제로 판단하지 않는다. 이전 step 이 정한 이름·주소는 `docs/ARCHITECTURE.md` "업무 화면 — phase 16" 을 따른다(README 와 다르면 ARCHITECTURE 가 기준).

## 작업

1. `src/workflow/server/work_actions.py`(새 모듈 — web.py 가 더 커지지 않게): `assign_work(...)` 와 `set_priority(...)`(인자는 ARCHITECTURE 시그니처). 규칙은 ARCHITECTURE 그대로:
   - 에이전트: 시작할 단계를 고르고, 그 에이전트가 단계 능력을 가졌는지 검사(`select_agent(mode="manual")` 기록), GitHub 지시 전 이슈면 `mark_issue_delegated`, `Worker.start_manually` 로 착수, 업무 담당 = 그 에이전트, 맡긴 사람 = 누른 멤버, 업무 이벤트 `assigned`, 업무 상태 재계산. 착수하지 못하면(러너 오프라인 등) 담당은 저장하고 대기 사유는 단계에 남는다.
   - 멤버: 배정만(활성 멤버만), 이벤트 `assigned`. `none`: 담당 해제. 셋 모두 활성 실행이 있으면 409, 끝난 업무면 409.
   - 우선순위: `high|normal|low` 만, 이벤트 `priority_changed`(이전·이후 값).
2. 라우트 `POST /work/{key}/assignee`·`POST /work/{key}/priority`(동작 `team.DELEGATE`, Origin 검사는 기존 미들웨어). 폼 값은 열거형·id 로만 받고 존재·워크스페이스를 검사한다. 성공하면 `/tasks?open=<key>` 로 303. 오류는 기존 `PageError` 형식.
3. 기존 `/tasks/{id}/delegate`·`/select`·`/run` 은 동작을 바꾸지 않되, 겹치는 착수 코드는 `work_actions` 의 내부 함수를 함께 쓰도록 정리해도 된다(동작 변화 없음을 기존 테스트로 확인).

## 테스트 먼저

`tests/workflow/server/test_work_actions.py`: GitHub 지시 전 이슈 업무에 에이전트 → 지시 기록·실행 생성·상태 `에이전트 작업 중`·맡긴 사람, 직접 등록 업무에 에이전트 → 선택 기록·실행, 능력 없는 에이전트 거부, 러너 오프라인이면 담당 저장 + 대기, 멤버 배정·해제, 비활성 멤버 거부, 활성 실행 중 409, 끝난 업무 409, 다른 워크스페이스 키 404, 우선순위 이벤트. 라우트 테스트(미러 새 파일 `tests/workflow/server/test_web_work.py`): 로그인 필수·멤버 허용, 다른 Origin 거부, 303 주소, 잘못된 값 422.

소스·템플릿 변경 전에 `tests/` 미러 경로에 실패 테스트를 작성하고 실패 원인이 의도한 것인지 확인한다(`tdd-guard.sh` 가 테스트 없는 소스 작성을 막는다). 구현 후 해당 테스트와 전체 회귀를 통과시킨다. 이 step 의 변경으로 기존 테스트가 깨지면 새 동작 기준으로 고치되 단정을 약하게 만들지 않는다(무엇을 왜 바꿨는지 summary 에 남긴다).

## Acceptance Criteria

```bash
python3 -m pytest -q
python3 -m ruff check .
```

위 커맨드와 이 step 의 테스트 항목을 모두 확인한다.

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 규칙을 확인한다: `domain/` 은 FastAPI·sqlite3·HTTPX·subprocess·Git 을 import 하지 않는다 / `server/` 와 `connector/` 는 서로 import 하지 않고 `contracts/` 만 공유한다 / 외부 입력(요청 본문·폼·쿼리 문자열·PR 제목·브랜치 이름·이슈 본문·모델 응답)에서 명령·경로를 받아 실행하지 않는다 — 주소 쿼리 값은 열거형으로만 받고 되돌아갈 URL 을 받지 않는다 / 비밀값(연결 토큰, `OPERATOR_TOKEN`, `SESSION_SECRET`, 로그인 세션·초대·재설정 토큰 원문, 비밀번호, GitHub App 비밀·설치 토큰, PAT, 알림 웹훅 URL)은 DB·로그·응답·템플릿·백업에 넣지 않는다 / 권한은 `domain/team.py` 역할 × 동작 표로만 판정한다 / 템플릿은 PR 제목·이슈 제목 같은 외부 문자열을 자동 이스케이프로만 출력한다(`|safe` 금지) / GLOSSARY 이름을 그대로 쓴다 / phase 8·11·12·14·15 의 GitHub 순환(수집 → 맡기기 → 수정 → 검토 → 초안 PR → 병합 추적 → 업무 완료)과 사람별 내 차례가 그대로 동작한다.
3. 성공이면 `phases/16-work-ui/index.json` 의 step 3 만 `completed` 로 바꾸고 `summary` 에 생성·수정·삭제 파일, 결정, 검증 결과(테스트 수), 다음 step 이 주의할 점을 한 줄로 남긴다.
4. 3회 수정 후에도 실패하면 `error` + `error_message`, 사용자 결정·외부 자원이 필요하면 `blocked` + `blocked_reason` 을 기록한다. 실행하지 않은 검증을 `completed` 로 표시하지 않는다.

## 금지사항

- 실제 GitHub(api.github.com·github.com)·Discord·외부 웹훅을 호출하지 않는다(공식 문서 읽기는 허용). 이유: 모든 step 은 httpx `MockTransport`·가짜 GitHub·가짜 알림 수신으로 검증한다. 실연동은 phase 뒤 사용자와 함께 한다.
- 사용자가 띄워 둔 셀프호스트(compose 프로젝트 `runloom`, 포트 8000, 볼륨 `runloom_workflow-data`)·`deploy/selfhost/.env`·러너 홈(`~/Library/Application Support/workflow-connector/`)·launchd·`~/.claude/`·`/Users/kje/demo/*` 를 읽거나 바꾸지 않는다. 컨테이너를 띄우거나 멈추지 않는다. 이유: 실사용 중인 환경이다.
- 새 의존성(Python 패키지, JS 프레임워크·번들러, 외부 CDN·웹폰트·아이콘 라이브러리)을 추가하지 않는다. 이유: ADR-0002·UI_GUIDE — Jinja2 서버 렌더 + CSS + 소량 인라인 JS.
- 종류 이름(`bug_fix`·`code_review`)으로 새 분기를 만들지 않는다. 이유: 업무 종류·후속 규칙은 워크스페이스 등록 데이터다(ADR-0009, AGENTS.md).
- `tasks` 표를 재생성하거나 `tasks.status` CHECK 를 바꾸지 않는다. 이유: ADR-0020 — 업무 상태는 `work_items` 에, 단계 상태는 그대로.
- 판단 제안·확신도·자동 시작(18-triage), Jira(17-jira), 모니터링 지표 확장(19-monitor), Claude Code 훅(다음 phase), 보드 끌어 옮기기, 브랜치 push 감지, 여러 행 일괄 변경을 만들지 않는다. 이유: 사용자 결정 2026-09-30 범위 밖.
- 직접 작업을 Runloom 결과 판정·완료 판정으로 처리하지 않는다. 이유: 완료는 PR 병합 같은 원본 신호로만(REDESIGN_PLAN 10절, 계약 v1).
- 사용자 변경을 삭제하거나 stash·reset 하지 않는다. 커밋은 하네스가 한다.
- 이 step 범위 밖 모듈을 "개선"하지 않는다. 기존 테스트의 단정을 약하게 만들어 통과시키지 않는다.
