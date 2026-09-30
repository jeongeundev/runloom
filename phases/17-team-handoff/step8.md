# Step 8: handoff-context — 지시 메모와 요청문의 업무 제목·양식 칸

## 읽어야 할 파일

- AGENTS.md
- phases/17-team-handoff/README.md (조사 결과·계획 기본값 10가지 — 이 phase 의 기준), phases/17-team-handoff/index.json (이전 step summary)
- docs/ARCHITECTURE.md "사람 사이 인계 — phase 17" (step 0 이 쓴 이름·시그니처 표 — README 와 다르면 ARCHITECTURE 가 기준)
- docs/adr/0023-*.md (step 0 이 쓴 ADR), docs/GLOSSARY.md
- src/workflow/server/task_cycle.py (`request_text`), src/workflow/server/work_actions.py (`assign_work`·비순환 요청문 `task["request"]`), src/workflow/server/worker.py (실행 요청을 만드는 곳), src/workflow/domain/form_sections.py·field_mapping.py (양식 칸), src/workflow/adapters/repo.py (`form_json`·step 1 지시 메모 함수)
- src/workflow/server/templates/_work_panel.html (담당 폼), src/workflow/server/web.py (`POST /work/{key}/assignee`)
- src/workflow/connector/prompt.py (러너가 요청문을 쓰는 방식 — 바꾸지 않는다, 읽기만)
- tests/workflow/server/test_task_cycle.py·test_work_actions.py·test_web_work.py

먼저 실제 파일을 읽는다. 대화 이력을 전제로 판단하지 않는다.

## 작업

1. 패널 담당 폼에 선택 칸 "지시 메모"(textarea, 최대 2000자 — 넘으면 422)를 넣는다.
   - 에이전트를 담당으로 고를 때만 쓰고, 사람 배정·해제에서는 무시한다.
   - 저장은 step 1 위치다. 패널 타임라인에 "<이름> 이 맡김 — 메모"로 보인다(이스케이프).
2. 요청문 조립 함수(ARCHITECTURE 이름·위치)를 하나 둔다. 순환·비순환 종류 모두 실행 요청의 `request` 를 이 함수로 만든다.
   - 순서는 `# <RUN-n> <업무 제목>` → 양식 칸 절(제목·값, 빈 칸 생략) → "## 맡긴 사람 지시 (이름)"(가장 최근 메모, 없으면 생략) → 원래 요청문 → 기존 "## 사람 응답" 절(있으면).
   - 크기 상한을 넘으면 ARCHITECTURE 규칙대로 자른다.
   - 순수 조립 부분은 domain 에 두어도 된다(ARCHITECTURE 를 따른다).
3. 계약 칸·러너 프롬프트 코드는 바꾸지 않는다.

## 테스트 먼저

- 조립 함수: 제목·양식·메모·응답 조합, 빈 칸 생략, 상한.
- 맡기기 경로(순환 1·비순환 1)에서 만들어진 실행 요청 `request` 에 머리·메모가 들어감.
- 메모 2001자 → 422.
- 사람 배정에서는 메모 무시.
- 타임라인 표시·이스케이프(`<script>` 메모).

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
