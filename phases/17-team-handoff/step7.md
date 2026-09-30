# Step 7: verify-only-server — [검증만 다시] → 검증만 다시 실행 → 판정

## 읽어야 할 파일

- AGENTS.md
- phases/17-team-handoff/README.md (조사 결과·계획 기본값 10가지 — 이 phase 의 기준), phases/17-team-handoff/index.json (이전 step summary)
- docs/ARCHITECTURE.md "사람 사이 인계 — phase 17" (step 0 이 쓴 이름·시그니처 표 — README 와 다르면 ARCHITECTURE 가 기준)
- docs/adr/0023-*.md (step 0 이 쓴 ADR), docs/GLOSSARY.md
- docs/CONTRACT.md (검증만 다시·`capabilities`), src/workflow/contracts/v1.py
- src/workflow/server/human_api.py (종류별 허용 동작·`respond_to_request`), src/workflow/server/views.py (`RESPONSE_ACTIONS`), src/workflow/server/worker.py (`_resume`·`_start_fix`·결과 판정·기준 커밋 계산), src/workflow/server/machine_api.py (claim — 러너 `capabilities` 저장·배정 조건), src/workflow/domain/task_followup.py (검증 실패 요청 코드), src/workflow/domain/task_readiness.py (`executor_outdated`), src/workflow/domain/settlement.py·completion.py (판정)
- src/workflow/adapters/repo.py (step 1 이 더한 검증만 다시 표시)
- tests/workflow/server/test_human_api.py·test_worker*.py·test_machine_api.py·test_task_cycle.py

먼저 실제 파일을 읽는다. 대화 이력을 전제로 판단하지 않는다.

## 작업

1. **claim**: 러너가 보고한 `capabilities` 를 연결 프로그램 기준으로 기억한다(저장 위치는 ARCHITECTURE).
   - 검증만 다시 실행은 `verify_only` 를 보고한 러너에게만 배정한다.
   - 보고하지 않은 러너면 준비 판정 `executor_outdated` 류("연결 프로그램 업데이트 필요 — 검증만 다시 미지원")로 기다린다.
2. **동작 `reverify`**: ARCHITECTURE 가 정한 검증 실패 요청 코드에만 허용한다.
   - 누르면 요청을 닫고(응답자 기록), 워커가 같은 단계에 검증만 다시 실행을 만든다.
   - 요청 `verify_only_commit` = 그 단계의 마지막 결과 커밋이다. 결과 커밋이 없으면 허용하지 않는다.
   - Agent·target 은 이전과 같다.
3. **판정**: 검증만 다시 결과도 기존 결과 판정을 지난다.
   - 통과하면 그 단계가 평소처럼 다음(후속 규칙 — 예: 검토 단계)으로 간다.
   - 다시 실패하면 같은 종류의 검증 실패 사람 요청이 새로 열린다(중복 키 규칙 확인).
   - 에이전트 사용량·변경 파일은 새로 생기지 않는다. 지표 이벤트는 ARCHITECTURE 규칙을 따른다.
4. **버튼 이름**: `resume` = [답하고 다시 맡기기], `reverify` = [검증만 다시]. 패널·단계 상세·알림 문구를 함께 바꾼다.

## 테스트 먼저

미러 테스트에 넣는다.
- 검증 실패 요청 → `reverify` → 워커 tick → 실행 요청에 `verify_only_commit` = 마지막 결과 커밋.
- `verify_only` 를 보고하지 않은 러너의 claim → 배정 안 됨 + 대기 문구. 보고한 러너 → 배정.
- 가짜 러너 결과 통과 → 후속 단계 생성. 실패 → 새 검증 실패 요청.
- 결과 커밋 없는 요청에 `reverify` → 거부.
- 다른 요청 코드에 `reverify` → 422/409.
- `resume` 동작은 그대로(에이전트 다시 실행).
- 버튼 문구.

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
