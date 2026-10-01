# Step 4: assignee-metrics — 담당자별 순수 계산

## 읽어야 할 파일

- AGENTS.md
- phases/20-monitor/README.md (사용자 결정 4가지·계획 기본값·조사 결과 — 이 phase 의 기준), phases/20-monitor/index.json (이전 step summary)
- docs/ARCHITECTURE.md "모니터링 — phase 20" (step 0 이 쓴 이름·시그니처·지표 정의·스키마 표 — README 와 다르면 ARCHITECTURE 가 기준)
- docs/adr/0026-*.md (step 0 이 쓴 ADR — step 0 에서는 아직 없다), docs/GLOSSARY.md
- docs/ARCHITECTURE.md "모니터링 — phase 20" 의 담당자별 지표 정의 표·이름 고정 표
- docs/ARCHITECTURE.md "팀 — phase 15" 의 "맡긴 사람과 내 차례 받는 사람", "업무와 단계 — phase 14" 의 "업무 상태"
- src/workflow/domain/metrics.py (`Stat`·`Ratio`·`_stat`·1회 통과율·재작업 정의 — 같은 정의를 쓴다), src/workflow/domain/team.py, src/workflow/domain/work_status.py

먼저 실제 파일을 읽는다. 대화 이력을 전제로 판단하지 않는다. 이전 step 에서 만든 코드를 꼼꼼히 읽고 설계 의도를 이해한 뒤 작업한다.

## 작업

`src/workflow/domain/assignee_metrics.py`(순수). `Stat`·`Ratio` 는 `domain/metrics.py` 것을 쓴다.

입력 값 객체(예): `AssigneeWorkFact`(work_item_id·담당 type/id·업무 상태·created_at·closed_at·병합 완료 여부·지금 내 차례면 내 차례가 된 시각·지금 받는 사람 member_id 튜플), `ResponseFact`(member_id·요청 created_at·응답 created_at), `AgentRunFact`(agent_id·work_item_id·kind·status·start_key·started_at·finished_at·cost_usd·첫 검토 outcome 등), `MemberLabel`/`AgentLabel`(id·표시 이름·활성/소유자) — 수집은 step 5. 판단 단계 실행은 입력에 오지 않는다(step 5 가 뺀다).

계산(예 이름) `compute_assignee_metrics(..., *, since, until, now) -> AssigneeReport`:
- 멤버 행(활성 멤버 전부 + 기록이 있는 비활성 멤버): 담당 업무 완료 n(기간 = `closed_at`)·진행 중 n(끝나지 않은 담당 업무, 기간 무관)·**내 차례 대기**(지금 받는 사람에 든 `내 차례` 업무 n, 가장 오래 기다린 시간 = `now` − 내 차례가 된 시각, 중앙값)·**응답 시간**(그 멤버가 응답한 사람 요청: 요청 → 응답 `Stat`, 기간 = 응답 시각).
- 에이전트 행: 담당 업무 완료·진행 n, 실행 n·실패율 `Ratio`·실패 코드 상위, 1회 통과율(phase 9 정의 — 그 에이전트가 담당한 업무의 첫 `code_review` 결과 `approved`), 재작업 실행 n, 실행 시간 `Stat`, 비용 `Stat`(NULL unknown).
- 담당 없음 행: 담당 없는 끝나지 않은 업무 n.
- 귀속 규칙(사용자 결정): 완료·진행 = 업무의 **지금** 담당, 응답 시간 = 응답한 멤버, 내 차례 대기 = **지금** 받는 사람. 과거 담당 이력으로 나누지 않는다.

## 테스트 먼저

`tests/workflow/domain/test_assignee_metrics.py`: 빈 입력, 완료·진행 귀속(지금 담당 기준), 내 차례 대기(받는 사람 둘이면 둘 다에 셈, `now` 로 대기 시간), 응답 시간(응답자 기준·기간 거르기), 에이전트 실패율·1회 통과·재작업·비용 NULL, 비활성 멤버는 기록이 있을 때만, 담당 없음 행.

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
   - `domain/` 은 FastAPI·sqlite3·HTTPX·subprocess·Git 을 import 하지 않는다. 지표 계산은 순수 함수이고 현재 시각을 스스로 읽지 않는다(필요하면 `now` 인자로 받는다).
   - `server/` 와 `connector/` 는 서로 import 하지 않고 `contracts/` 만 공유한다. 이 phase 는 `connector/` 를 바꾸지 않는다.
   - 외부 입력(쿼리 문자열·폼·업무 본문·판단 근거)에서 명령·경로를 받아 실행하지 않는다. 쿼리 값은 기존 파서처럼 검증하고 어긋나면 422.
   - 비밀값(연결 토큰, `OPERATOR_TOKEN`, `SESSION_SECRET`, GitHub App 비밀·설치 토큰, PAT, Jira API 토큰, 알림 웹훅 URL)은 DB·로그·응답·템플릿·백업·CSV 에 넣지 않는다. 설정 변경 기록에도 넣지 않는다(무엇을 바꿨는지 이름만, 값·본문은 넣지 않는다).
   - 템플릿은 외부 문자열(업무 제목·멤버 이름·에이전트 이름·종류 라벨)을 자동 이스케이프로만 출력한다(`|safe` 금지).
   - 종류 이름(`bug_fix`·`code_review`·`triage`)으로 새 분기를 만들지 않는다 — 판단 단계는 `is_triage_kind`·`repo._TRIAGE_STAGE`(결과 형태 `triage_result`)로 가른다.
   - 모르는 값은 0 이 아니라 "모름"(`unknown`)으로 센다. 모든 수치에 n 을 함께 낸다. 화면 문구는 인과를 단정하지 않는다("판단 덕분에 줄었다" 금지 — "기준 v1 n=12, 기준 v2 n=8" 처럼).
   - GLOSSARY 이름을 그대로 쓴다.
   - phase 8·9·11·12·14~19 동작(GitHub·Jira 순환, 기존 지표·기준선, 사람별 내 차례, 직접 작업·PR 신호, 소유자 승인·꺼진 러너 대기, 판단·자동 시작)이 그대로 동작한다. 기존 `/metrics.json`·`/metrics.csv` 의 키·열은 바꾸지 않는다(더하기만).
3. 성공이면 `phases/20-monitor/index.json` 의 이 step 만 `completed` 로 바꾸고, `summary` 에 한 줄로 남긴다: 생성·수정·삭제 파일, 결정, 검증 결과(테스트 수), 다음 step 이 주의할 점.
4. 3회 수정 후에도 실패하면 `error` + `error_message` 를 기록한다. 사용자 결정·외부 자원이 필요하면 `blocked` + `blocked_reason` 을 기록한다. 실행하지 않은 검증을 `completed` 로 표시하지 않는다.

## 금지사항

- 실제 Claude/Codex·GitHub(api.github.com·github.com)·Jira·Discord·외부 웹훅을 호출하지 않는다. 이유: 모든 step 은 가짜 러너·가짜 도구·httpx `MockTransport`·가짜 클라이언트로 검증한다.
- 사용자가 띄워 둔 환경을 읽거나 바꾸지 않는다: 셀프호스트(compose 프로젝트 `runloom`, 포트 8000, 볼륨 `runloom_workflow-data`)·`deploy/selfhost/.env`·러너 홈(`~/Library/Application Support/workflow-connector*`)·launchd(`launchctl` 실행 금지)·`~/Library/LaunchAgents`·`~/.claude/`·`/Users/kje/demo/*`. 컨테이너를 띄우거나 멈추지 않는다. 이유: 실사용 중인 환경이다.
- 새 의존성(Python 패키지, 차트 라이브러리, JS 프레임워크·번들러, 외부 CDN)을 추가하지 않는다. 이유: ADR-0002·UI_GUIDE. 화면은 표와 짧은 문장으로만 그린다.
- 러너 프로토콜·계약 v1(`contracts/v1.py` 의 실행 요청·이벤트·결과 모델)을 바꾸지 않는다. 이유: 이 phase 는 러너 재설치 없이 반영해야 한다.
- `tasks`·`work_items`·`triage_logs` 표를 재생성하거나 칸을 더하지 않는다. 이유: 지표는 기존 기록으로 계산한다(ADR-0025 결정 9 — "실제 결과" 칸을 두지 않는다). 새 표는 step 1 의 `config_changes` 하나뿐이다.
- 지표 계산 결과를 DB 에 저장하거나 캐시하지 않는다. 이유: 원천 기록에서 매번 계산한다(phase 9 원칙).
- 사용자 변경을 삭제하거나 stash·reset 하지 않는다. 커밋은 하네스가 한다.
- 이 step 범위 밖 모듈을 "개선"하지 않는다. 기존 테스트의 단정을 약하게 만들어 통과시키지 않는다.
