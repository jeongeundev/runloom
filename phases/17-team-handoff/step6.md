# Step 6: owner-approval — 맡기기 → 승인 요청 → 승인·거절, 소유자 알림, 꺼진 러너 대기

## 읽어야 할 파일

- AGENTS.md
- phases/17-team-handoff/README.md (조사 결과·계획 기본값 10가지 — 이 phase 의 기준), phases/17-team-handoff/index.json (이전 step summary)
- docs/ARCHITECTURE.md "사람 사이 인계 — phase 17" (step 0 이 쓴 이름·시그니처 표 — README 와 다르면 ARCHITECTURE 가 기준)
- docs/adr/0023-*.md (step 0 이 쓴 ADR), docs/GLOSSARY.md
- src/workflow/domain/ (step 5 가 더한 함수), src/workflow/adapters/repo.py (step 1 이 더한 함수)
- src/workflow/server/work_actions.py (`assign_work`·`start_stage`·후보), src/workflow/server/web.py (`/work/{key}/assignee`·옛 `/tasks/{id}/delegate|select|run`·사람 요청 응답 라우트), src/workflow/server/human_api.py (`respond_to_request`·종류별 허용 동작), src/workflow/server/task_cycle.py, src/workflow/server/worker.py (`_start_ready_tasks`·[다시 맡기기] 경로·`_notify`), src/workflow/server/views.py (`RESPONSE_ACTIONS`)
- tests/workflow/server/test_work_actions.py·test_web_work.py·test_human_api.py·test_worker*.py·conftest.py

먼저 실제 파일을 읽는다. 대화 이력을 전제로 판단하지 않는다.

## 작업

`src/workflow/server/` 에서 ARCHITECTURE 의 승인 흐름을 구현한다.

1. **맡기기**: `assign_work` 에서 에이전트를 담당으로 고르면, step 5 의 판정으로 승인이 필요한지 본다.
   - 필요하면 착수하지 않고 승인 요청을 연다(받는 사람 = 소유자).
   - 맡긴 사람 ≠ 소유자이면, 정책과 관계없이 소유자에게 `delegated_to_you` 알림을 보낸다.
   - 옛 `/tasks/{id}/delegate|select|run` 과 [다시 맡기기]도 같은 내부 함수를 지난다.
2. **막기**: 승인 요청이 열린 단계에는 어떤 경로로도 실행을 만들지 않는다.
   - 순환 종류는 준비 판정 막힘으로 막는다.
   - 비순환 종류는 실행을 만드는 함수 입구에서 막는다(ARCHITECTURE 위치).
   - 종류 이름으로 나누지 않는다.
3. **승인·거절**: 사람 요청 동작 `approve`·`decline`. 권한은 step 5 판정과 `team` 동작이다. 소유자·관리자가 아니면 403 이다.
   - 승인 → 요청 닫기 → 곧 착수(꺼져 있으면 대기).
   - 거절(메모 선택) → 요청 닫기 → 단계 선택 해제 → 업무 담당 비움 → 맡긴 사람에게 `delegation_declined`(메모 포함, 이스케이프) → 업무 이벤트.
   - 담당을 다른 것으로 바꾸면 열린 승인 요청을 닫는다.
4. **꺼진 러너 대기(모든 종류)**: 담당 에이전트의 러너가 꺼져 있으면 실행을 만들지 않고 대기 이유를 남긴다.
   - 소유자에게 `runner_offline_waiting` 을 한 번 보낸다(중복 키).
   - 워커 tick 이 러너가 켜진 것을 보면 착수한다.
   - 이미 만들어진 `queued` 실행은 기존 동작 그대로 둔다.
5. 업무 상태(`내 차례`·`대기` 이유)와 받는 사람별 "내 차례" 필터가 step 5 규칙으로 계산되게 연결한다.

## 테스트 먼저

미러 테스트에 넣는다. 멤버 A·B, B 소유 러너·에이전트를 쓴다.
- 정책 `run`: A 가 맡김 → 바로 실행 + B 에게 `delegated_to_you`.
- 정책 `owner_approval`:
  - A 가 맡김 → 실행 없음, 업무 `내 차례`, B 의 내 차례 필터에 있고 A 에게는 없음.
  - 옛 run 라우트·워커 tick 으로도 실행이 생기지 않음.
  - B 승인 → 실행.
  - B 거절 → 담당 없음 + A 에게 알림.
  - A 가 승인 시도 → 403. 관리자는 승인 가능.
- B 가 자기 에이전트에게 맡김 → 정책과 관계없이 바로 실행, 알림 없음.
- 꺼진 러너:
  - 비순환 종류 → 실행 없음, `대기` 이유, 알림 1회(tick 두 번에도 1회).
  - 러너 heartbeat 뒤 tick → 실행.
- 담당을 바꾸면 승인 요청이 닫힘.

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
