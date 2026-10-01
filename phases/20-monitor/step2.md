# Step 2: config-change-log — 설정 번호를 올리는 곳마다 변경 기록

## 읽어야 할 파일

- AGENTS.md
- phases/20-monitor/README.md (사용자 결정 4가지·계획 기본값·조사 결과 — 이 phase 의 기준), phases/20-monitor/index.json (이전 step summary)
- docs/ARCHITECTURE.md "모니터링 — phase 20" (step 0 이 쓴 이름·시그니처·지표 정의·스키마 표 — README 와 다르면 ARCHITECTURE 가 기준)
- docs/adr/0026-*.md (step 0 이 쓴 ADR — step 0 에서는 아직 없다), docs/GLOSSARY.md
- docs/ARCHITECTURE.md "모니터링 — phase 20" 의 "설정 변경 기록 지점 표"
- docs/ARCHITECTURE.md "측정 — phase 9" 의 "설정 번호 (`config_revision`)"
- src/workflow/adapters/repo.py: `bump_config_revision` 과 부르는 함수 전부(`replace_field_mappings`·`insert_kind`·`delete_kind`·`insert_rule`·`delete_rule`·`save_github_source`·`save_triage_criteria`·`save_triage_autostart` — `grep -n bump_config_revision` 로 다시 확인), step 1 의 `record_config_change`
- 그 repo 함수를 부르는 server 경로 전부(`grep -rn "insert_kind\|delete_kind\|insert_rule\|delete_rule\|save_github_source\|replace_field_mappings\|save_triage_criteria\|save_triage_autostart" src/workflow/server`) — 웹 화면·JSON API·GitHub App 연결·Jira 프로젝트 추가 등
- src/workflow/server/auth.py (로그인 멤버를 얻는 방식)

먼저 실제 파일을 읽는다. 대화 이력을 전제로 판단하지 않는다. 이전 step 에서 만든 코드를 꼼꼼히 읽고 설계 의도를 이해한 뒤 작업한다.

## 작업

1. `bump_config_revision` 을 부르는 repo 함수마다 같은 트랜잭션에서 `record_config_change` 를 부른다 — `revision` = bump 가 돌려준 새 번호, `area`·`action`·`subject` 는 ARCHITECTURE 지점 표 값. 설정 번호가 실제로 오르지 않으면(같은 값 저장 등 기존 "그대로" 경로) 기록도 없다 — 번호 한 번 = 기록 한 행.
   - 가장 단순한 모양을 고른다: 예) `bump_config_revision(conn, session_id, *, area, action, subject, member_id, now)` 로 넓혀 기록까지 한 곳에서 하거나, 각 함수가 bump 직후 `record_config_change` 를 부르거나. ARCHITECTURE 이름 고정 표가 정한 모양을 따른다.
2. 그 repo 함수들에 `member_id: str | None = None` 키워드(이미 있는 함수는 그대로)를 더하고, 사람이 누르는 server 경로는 로그인 멤버 id 를 넘긴다. 마이그레이션·시드·`create_session`·워커 경로는 None. 기존 호출이 깨지지 않게 기본값 None.
3. `delete_kind`·`delete_rule` 처럼 지금 `now` 가 없는 함수는 `now` 키워드를 더한다(호출자가 넘김). 기존 테스트 호출은 새 시그니처에 맞춘다.
4. `subject` 에 값·본문·토큰을 넣지 않는다 — 판단 기준은 `v<버전>`, 자동 시작은 `<종류> 켬|끔 · 기준값 0.85`, 매핑 표는 `<소스 종류> 매핑 n행`, 소스는 저장소 이름과 바뀐 칸 이름 정도(ARCHITECTURE 표 기준).

## 테스트 먼저

- `tests/workflow/adapters/test_repo.py`(미러 위치): 지점마다 — 바뀌면 설정 번호 +1 과 기록 한 행(같은 `revision`), 바뀌지 않으면 둘 다 없음, 트랜잭션 실패(예: 중복 종류)면 둘 다 없음, `member_id` 저장.
- server 경로 테스트(기존 테스트 파일에 더함): 종류 추가·규칙 추가·판단 기준 저장·자동 시작 변경을 로그인 멤버로 하면 `by_member_id` 가 그 멤버.
- 비밀값이 `subject` 에 들어가지 않음(소스 저장 테스트에서 토큰 문자열 부재 단정).

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
