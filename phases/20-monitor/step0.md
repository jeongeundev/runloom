# Step 0: monitor-design — ADR-0026·ARCHITECTURE·GLOSSARY (문서만)

## 읽어야 할 파일

- AGENTS.md
- phases/20-monitor/README.md (사용자 결정 4가지·계획 기본값·조사 결과 — 이 phase 의 기준), phases/20-monitor/index.json (이전 step summary)
- docs/ARCHITECTURE.md "모니터링 — phase 20" (step 0 이 쓴 이름·시그니처·지표 정의·스키마 표 — README 와 다르면 ARCHITECTURE 가 기준)
- docs/adr/0026-*.md (step 0 이 쓴 ADR — step 0 에서는 아직 없다), docs/GLOSSARY.md
- docs/product/REDESIGN_PLAN.md 3·4·6·11·13절
- docs/ARCHITECTURE.md "측정 — phase 9"(지표 정의·이벤트 기록 규칙·설정 번호·API·화면), "업무와 단계 — phase 14"(업무 상태·지표 묶음), "팀 — phase 15"(역할 × 동작·맡긴 사람과 내 차례 받는 사람), "업무 화면 — phase 16"(주소·화면 배치), "판단 — phase 19"(사람 처리·자동 시작·스키마 v15·화면)
- docs/adr/0015-measurement-events-and-baseline.md, 0020, 0021, 0025-triage.md
- src/workflow/domain/metrics.py, src/workflow/server/metrics_api.py, src/workflow/server/views.py `metrics_context`, src/workflow/server/templates/metrics.html, src/workflow/server/templates/_connect_triage.html
- src/workflow/adapters/repo.py: `bump_config_revision` 과 그것을 부르는 함수 전부(`grep -n bump_config_revision`), `list_metric_facts`, `turn_recipients_of`, `triage_handled_counts`, `save_triage_criteria`, `save_triage_autostart`
- src/workflow/adapters/db.py (`SCHEMA_VERSION`, `_migrate_14_to_15` 의 모양)

먼저 실제 파일을 읽는다. 대화 이력을 전제로 판단하지 않는다. 이전 step 에서 만든 코드를 꼼꼼히 읽고 설계 의도를 이해한 뒤 작업한다.

## 작업

문서만 쓴다(소스·테스트 변경 없음). README 의 사용자 결정·계획 기본값을 실제 코드로 확인하고, 다르면 코드가 기준이며 다른 점을 ADR 에 적는다.

1. `docs/adr/0026-monitor.md` — 맥락(19 가 넘긴 것: 확신도 보정·판단 실제 결과), 사실(코드로 확인한 원천 표·칸), 결정(README 사용자 결정 4 + 계획 기본값, 바꾼 것은 이유와 함께), 하지 않는 것.
2. `docs/ARCHITECTURE.md` 에 새 절 "모니터링 — phase 20"(위치: "판단 — phase 19" 절 바로 뒤, "기존 구현과 초기 설계 기록" 앞). 다음을 모두 담는다:
   - 한 줄 요약, 흐름.
   - **지표 정의 표** 둘(판단 품질·담당자별) — 열: 지표·정의·원천 칸·미완료·모름. README 기본값 3·4 를 그대로 옮기되 칸 이름은 실제 스키마로.
   - **확신도 구간**: 구간 경계(`[0.5,0.7)`, `[0.7,0.8)`, `[0.8,0.9)`, `[0.9,1.0]`, 0.5 미만은 `[0,0.5)` 한 칸), 구간마다 제안 n·사람 일치·실제 결과.
   - **기준값 미리보기**: 종류·기준값 → "그 기준값 이상이었던 판단 n건, 사람 일치 x/(accepted+changed), 병합 완료 y/(끝난 것)".
   - **스키마 v16 표**: `config_changes`(README 기본값 6 의 칸·CHECK·인덱스), v15 → v16 마이그레이션 순서. 원본 v15 스키마 fixture 경로 `tests/workflow/adapters/fixtures/schema_v15.sql`.
   - **설정 변경 기록 지점 표**: `bump_config_revision` 을 부르는 repo 함수마다 `area`·`action`·`subject` 값과, 그 함수를 부르는 server 경로가 멤버 id 를 어떻게 넘기는지(넘길 수 없는 경로 — 마이그레이션·시드·워커 — 는 NULL).
   - **이름·시그니처 고정 표**: step 1~8 이 만드는 함수·값 객체·경로 이름(아래 각 step 의 예시 이름을 출발점으로 하되 코드 관례에 맞게 확정).
   - **경로 표**: `GET /monitor?tab=before_after|triage|assignees`(기본 `before_after`, 다른 값 422), 기간 `from`·`to` 공통, 권한 `view_metrics`(관리자·멤버 둘 다 — 사용자 결정 4). `/metrics.json`·`/metrics.csv` 의 새 키·행(기존 키는 그대로, 더하기만).
   - **화면 문구**: 탭 이름(`전후`·`판단`·`담당자별`), 표 머리, 빈 상태 문구("아직 판단 기록이 없습니다" 등), 설정 번호 그룹 머리("설정 4 — 판단 기준 v2 · 김OO · 10/3", v16 전 번호는 "기록 없음").
3. `docs/GLOSSARY.md` 에 phase 20 용어: 판단 품질, 사람 일치율, 실제 결과(병합 완료 비율·재작업 없이 완료 비율), 확신도 구간, 기준값 미리보기, 설정 변경 기록(`config_changes`), 담당자별. 금지 표현(`정확도` — 사람 일치율은 정답률이 아니다, `대시보드`, `analytics`).
4. `docs/product/REDESIGN_PLAN.md` 13절 표의 8행(`20-monitor`)에 이번 범위 한 줄, `docs/CURRENT_HANDOFF.md` 맨 위 "다음 작업" 에 "20-monitor 진행 중" 한 줄.
5. `phases/20-monitor/README.md` 의 "계획 기본값" 중 바꾼 것이 있으면 그 줄 끝에 `(step 0: ADR-0026 결정 n 으로 바꿈)` 을 단다.

확인할 것(코드로): ① `bump_config_revision` 을 부르는 함수 목록이 README 조사와 같은지 ② Jira 프로젝트 설정 저장(`update_jira_project`)이 설정 번호를 올리지 않는 것 — 올리지 않으면 이번에도 기록하지 않는다(설정 번호를 올리는 곳만 기록) ③ `triage_logs` 의 `work_revision` 과 `work_items.revision` 으로 "판단 뒤 내용이 바뀐 업무" 를 셀 수 있는지 ④ 업무 `완료` 가 PR 병합인지 다른 이유(모든 단계 완료)인지 가를 수 있는 칸(`work_items.status_reason`·`work_pull_requests.state`·`task_pull_requests`) — 병합 완료 비율의 분자를 무엇으로 셀지 확정 ⑤ 재작업 = 업무의 단계 실행 중 `start_key` 가 `rework:` 로 시작하는 것(phase 9 정의) — 판단 단계 실행은 뺀다 ⑥ "내 차례" 가 된 시각 = 업무 `work_item_events(status_changed).to = '내 차례'` 의 가장 최근 행.

## 테스트 먼저

문서 step 이라 새 테스트는 없다. 기존 테스트(`tests/` 중 문서 내용을 검사하는 것 — 예: CONTRACT·GLOSSARY 검사)가 통과해야 한다.

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
