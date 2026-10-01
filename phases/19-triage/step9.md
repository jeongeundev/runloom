# Step 9: triage-autostart — 자동 맡기기

## 읽어야 할 파일

- AGENTS.md
- phases/19-triage/README.md (사용자 결정 3가지·계획 기본값 14가지·조사 결과 — 이 phase 의 기준), phases/19-triage/index.json (이전 step summary)
- docs/ARCHITECTURE.md "판단 — phase 19" (step 0 이 쓴 이름·시그니처·스키마 표 — README 와 다르면 ARCHITECTURE 가 기준)
- docs/adr/0025-*.md (step 0 이 쓴 ADR), docs/GLOSSARY.md
- src/workflow/server/worker.py (`tick`·`_triage_new_work`(step 5)·`_start_waiting_stages`), work_actions.py (`assign_work`·`start_stage`), owner_approval.py, src/workflow/adapters/repo.py (`hand_work_to_agent`·판단 로그·자동 시작 설정)
- src/workflow/domain/triage.py (`should_autostart`)
- step 5~8 산출물

먼저 실제 파일을 읽는다. 대화 이력을 전제로 판단하지 않는다.

## 작업

1. 워커(ARCHITECTURE 자리): `proposed` 판단 중 사람 처리 없음·업무 여전히 담당 없음·`should_autostart` 참 → 기존 맡기기 함수로 맡김(맡긴 사람 없음, 표시 `자동 시작 · 판단 v<n>` — ARCHITECTURE), 판단 로그 `auto_started`. 판단 1건당 한 번만(멱등 — 두 tick·워커 재시작에도 두 번 맡기지 않음).
2. 소유자 승인 정책·꺼진 러너는 기존 경로 그대로(승인 대기·대기 표시·소유자 알림). 맡긴 사람이 없을 때 승인 요청·알림 문구가 깨지지 않게 한다("자동 시작" 으로 표시).
3. 판단 결과를 낸 뒤 자동 시작 설정이 꺼졌거나 기준값이 올랐으면 맡기지 않는다(맡기는 순간의 설정 기준).
4. 자동 시작이 맡긴 업무의 `내 차례`(실패 등)는 기존 규칙대로 — 맡긴 사람이 없으면 관리자 전원(15 결정).

## 테스트 먼저

`tests/workflow/server/test_triage_autostart.py`: 켜짐·`ready`·Agent·0.86 ≥ 0.8 → 맡김·실행 시작·로그 `auto_started`·표시 문구, 기준값 미만·`needs_check`·멤버 담당·꺼짐 → 안 맡김, 사람이 먼저 맡김 → 안 맡김, 두 tick 멱등, 소유자 승인 정책 Agent → 승인 대기·소유자 내 차례, 꺼진 러너 → 대기, 실패 시 내 차례 = 관리자.

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
