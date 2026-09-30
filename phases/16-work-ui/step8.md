# Step 8: direct-work — 직접 작업 — [내 세션에서 작업]·그만두기·넘기기와 업무 상태

## 읽어야 할 파일

- AGENTS.md
- phases/16-work-ui/README.md (조사 결과·계획 기본값 9가지 — 이 phase 의 기준), phases/16-work-ui/index.json (이전 step summary)
- docs/ARCHITECTURE.md ("업무 화면 — phase 16" 직접 작업·업무 상태 표·브랜치 이름 규칙), docs/adr/0022-work-screen-and-direct-work.md
- src/workflow/domain/work_status.py, tests/workflow/domain/test_work_status.py
- src/workflow/adapters/repo.py (업무 상태 재계산·`WorkItemFacts` 조립·업무 이벤트), src/workflow/server/work_actions.py (step 3), src/workflow/server/templates/_work_panel.html (step 5), src/workflow/server/views.py

먼저 실제 파일을 읽는다. 대화 이력을 전제로 판단하지 않는다. 이전 step 이 정한 이름·주소는 `docs/ARCHITECTURE.md` "업무 화면 — phase 16" 을 따른다(README 와 다르면 ARCHITECTURE 가 기준).

## 작업

1. 순수 함수: `branch_name(key, title)`(ARCHITECTURE 규칙 — ASCII 영숫자만, 최대 길이, 없으면 키만), `WorkItemFacts` 에 직접 작업 칸(누가·이름)을 더하고 `work_status` 규칙 표를 ARCHITECTURE 새 순서대로(사람 요청 > 열린 PR > 직접 작업 > 기존). 직접 작업이 없을 때 기존 결과가 바뀌지 않음을 기존 테스트로 확인.
2. repo `start_direct_work`·`stop_direct_work`(칸 3개 함께, 이벤트 `direct_started`·`direct_stopped`, 상태 재계산).
3. `work_actions`: 시작 — 담당 = 누른 멤버(다른 멤버 담당이어도), 활성 실행이 있거나 끝난 업무면 409. 그만두기 — 칸 비움, 담당은 그대로. 에이전트에게 넘기기 — 그만두기 + `assign_work(agent)`(한 트랜잭션, 착수 실패 규칙은 step 3 그대로). 라우트 `POST /work/{key}/direct`·`/direct/stop`(동작 `team.DELEGATE`). 담당 폼에서 에이전트를 고르면 직접 작업을 먼저 끝낸다.
4. 패널: 직접 작업이 없으면 [내 세션에서 작업], 있으면 "직접 작업 중 · 이름 · 시작 시각", 브랜치 이름 + [복사](JS `navigator.clipboard`, 실패하면 선택 가능한 텍스트), "PR 을 열면 PR · 검토, 병합되면 완료로 옮깁니다. Runloom 결과 판정은 거치지 않습니다." 안내, [직접 작업 그만두기]·담당 폼의 에이전트 선택. 목록 "다음 할 일" 칸에 `직접 작업 중 · 이름`.

## 테스트 먼저

`tests/workflow/domain/test_work_status.py`: 직접 작업 → `직접 작업 중`(이유에 이름), 사람 요청이 있으면 `내 차례` 가 앞, 직접 작업이 없으면 기존 결과 그대로. `branch_name` 테스트(한글 제목 → 키만, 특수문자·길이). `tests/workflow/server/test_work_actions.py`·`test_web_work_panel.py`: 시작·그만두기·넘기기 전이와 이벤트, 다른 멤버 담당 → 누른 사람, 실행 중 409, 끝난 업무 409, 패널 문구·브랜치 이름, 목록 다음 할 일.

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
3. 성공이면 `phases/16-work-ui/index.json` 의 step 8 만 `completed` 로 바꾸고 `summary` 에 생성·수정·삭제 파일, 결정, 검증 결과(테스트 수), 다음 step 이 주의할 점을 한 줄로 남긴다.
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
