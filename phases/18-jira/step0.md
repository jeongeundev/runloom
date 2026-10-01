# Step 0: jira-design — 결정·이름·스키마 v14 고정 (문서만)

## 읽어야 할 파일

- AGENTS.md
- phases/18-jira/README.md (사용자 결정 6가지·계획 기본값 14가지·조사 결과 — 이 phase 의 기준)
- docs/research/2026-09-29-jira-integration.md (1절 권장 설계·2절 사실·3절 열린 질문 — 1차 출처 조사)
- docs/product/REDESIGN_PLAN.md 7·13·14·16절
- docs/ARCHITECTURE.md 의 "GitHub 업무 순환 — phase 8 계약"·"GitHub App 연결 — phase 11"·"실제 저장소 순환 — phase 12"·"업무와 단계 — phase 14"·"업무 화면 — phase 16"·"사람 사이 인계 — phase 17" 절
- docs/adr/0014·0017·0018·0020·0022·0023, docs/GLOSSARY.md, docs/CONTRACT.md
- src/workflow/adapters/db.py (v13 재생성 선례·FK), src/workflow/adapters/repo.py (`upsert_source_issue`·후속 `placement`·`field_mappings`), src/workflow/adapters/secret_store.py, src/workflow/adapters/github_client.py
- src/workflow/server/github_sync.py (`task_intake_facts`·`_pull_target`)·github_delivery.py·task_cycle.py (`origin_source`·`task_facts`·`_match`·`_match_facts`·`execution_request_text`)·worker.py (`tick`·`_sync_github`·`_queue_pull_request`·`_deliver_pull_requests`·`_deliver_github`·`_followup_context`·`_create_followup_task`)·stage_runs.py·owner_approval.py·views.py (`cycle_context`·`_origin`)·mapping_api.py·web.py (`/connect`)
- src/workflow/domain/issue_intake.py (`intake_facts`)·field_mapping.py·form_sections.py·task_readiness.py·task_followup.py·work_status.py·pull_request.py·work_keys.py·github_match.py (`match_source`)·handoff_context.py (`compose_request`)·work_list.py
- src/workflow/adapters/repo.py 의 `set_work_status`·`work_item_facts`(`delegated`)·`hand_work_to_agent`·`list_work_rows`(`repository` 칸)·`enqueue_pull_request`·`create_followup_once`

먼저 실제 파일을 읽는다. 대화 이력을 전제로 판단하지 않는다. phase 17 은 끝나 `service` 에 병합됐다 — 17 이 바꾼 코드(스키마 v13·`owner_approval`·`stage_runs`·매칭 `chosen_agent_id`/`pair_agent_id`·요청문 머리·목록 저장소 칸)를 기준으로 본다. README "조사로 확인한 현재" 는 2026-10-01 에 17 완료 코드로 다시 맞췄다 — 다르면 코드가 기준이고, 다른 점을 ADR 에 적는다.

## 작업 (문서만 — 코드·테스트를 바꾸지 않는다)

1. **ADR-0024 `docs/adr/0024-jira-source.md`**: README 사용자 결정 6가지와 계획 기본값 14가지를 결정으로 고정한다. 코드·조사 문서를 보고 기본값을 바꿔야 하면 바꾸고 이유를 적는다. 반드시 정할 것:
   - 호출 기준 주소: 사이트 주소 직접 vs `api.atlassian.com/ex/jira/{cloudId}`(scoped 토큰). 조사 문서 근거로 하나 또는 "확인 뒤 저장" 규칙.
   - 토큰 보관 = `secret_store` 새 고정 이름(ADR-0017 과 같은 방식) — AGENTS.md 비밀값 규칙과 맞는지 한 줄.
   - Jira 상태 → Runloom `open`/`closed` 접기(`statusCategory.key == 'done'` → closed).
   - 세 순간의 정의를 **업무 상태 전이**로: 어떤 `work_items.status` 로 들어갈 때 어느 순간인지(ARCHITECTURE "업무 상태" 8개 이름으로). 한 업무에서 순간이 되풀이될 때(PR 닫힘 뒤 다시 열림 등) 다시 보낼지.
   - 전송 실패 분류(재시도 vs `failed`)와 화면 표시.
   - 후속 이슈 생성 조건·본문·라벨·링크 유형·조정 방법, 만든 이슈를 다음 동기화가 같은 업무로 받는 규칙(중복 업무 금지).
   - 원본 조회 일반화: `task_cycle.origin_source` 가 Jira 업무도 원본 열림/닫힘을 돌려주게 하는 방법(종류 이름 분기 없이, `source_type` 분기는 이 한 곳).
   - Jira 업무의 실행 대상 저장소를 어디서 읽는지(프로젝트 설정 → `github_sources`), 초안 PR·PR 감지가 Jira 업무에도 되는지 코드로 확인한 결과와 필요한 변경. 특히:
     - `origin_source` 를 부르는 곳 전부(README 조사 목록)가 Jira 업무에서 무엇을 받는지 — `views._origin` 은 GitHub 이슈 행 모양을 전제한다. 돌려주는 모양(원본 사실 묶음으로 바꿀지, Jira 는 따로 둘지)을 정한다.
     - 에이전트 매칭: `task_facts` 가 `config` 가 있으면 `_match` 를 부르고 `fix=intake.assignee_ids is not None` 으로 가른다 — Jira 업무(`source_issues` 없음 → `assignee_ids=None`)가 수정 단계로 매칭되게 하는 방법과 요구 능력 `repository_id` 범위를 채우는 방법.
     - 맡기기 전 대기(README 기본값 13): 지시 기록 위치, `work_item_facts.delegated`·`intake_facts(needs_delegation, delegated_by)`·`hand_work_to_agent` 를 Jira 에도 맞추는 방법.
     - 초안 PR: `_queue_pull_request` 가 GitHub 이슈 행 없이도 연결 저장소로 PR 행을 만드는 방법, `task_pull_requests.issue_number`·`enqueue_pull_request` 시그니처를 어떻게 할지(스키마 v14 에 포함되면 표에 적는다), `pr_body` 의 `Fixes #N` 을 원본이 GitHub 일 때만 넣는 방법과 Jira 원본 줄 문구.
     - 목록 `list_work_rows.repository` 가 Jira 업무의 연결 저장소를 보이게 하는 방법.
     - 요청문 머리의 원본 키(README 기본값 14).
   - 세 순간 훅 위치: 업무 상태 쓰기는 `repo.set_work_status` 한 곳이다(`status_changed` 이벤트) — 여기서 outbox 를 넣을지, 다른 곳인지와 이유.
2. **ARCHITECTURE "Jira 소스 — phase 18" 절**(끝에 추가): 흐름, 스키마 v14 표(표·칸·CHECK·FK·인덱스, `work_items`·`field_mappings` CHECK 확장 방법 — v13 재생성 선례를 따르되 `work_items` 를 참조하는 FK 가 깨지지 않는 방법을 실제 SQL 로 확인해 적는다, `tasks` 재생성 금지), 모듈·함수 이름·시그니처 표(step 번호 표시), 경로 표(연결 화면·저장·끊기), 비밀 이름, outbox 상태(`pending`/`sending`/`delivered`/`unknown`/`failed` — 기존 `SourceDelivery.state` 와 맞출지), 세 순간 표, 오류 분류 표, 화면 문구.
3. **GLOSSARY**: phase 18 용어(Jira 연결·Jira 프로젝트 설정·세 순간(상태 옮기기)·후속 이슈 등록·Jira outbox 등) — 코드 식별자와 쓰지 말 말.
4. **REDESIGN_PLAN 13절**: 18-jira 행에 이번 범위(되쓰기 = 상태 옮기기·후속 등록, 댓글·PR 링크 제외)와 ADR-0024 링크.
5. **docs/CURRENT_HANDOFF.md** 맨 위 "다음 작업"을 한 줄: 18-jira 진행 중(`feat-18-jira`, 17 병합 뒤 `service` 에서 갈라짐 — 끝나면 18 → service `--no-ff` 병합).

## 테스트

문서만 바꾼다. 기존 테스트가 문서 형식(CONTRACT 블록 수 등)을 검사하므로 전체 회귀를 돌린다. CONTRACT.md 에 블록을 더하지 않는다(Jira 는 러너 계약을 바꾸지 않는다).

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
   - 외부 입력(Jira 응답·이슈 본문·요청 본문·폼·쿼리 문자열·모델 응답)에서 명령·경로를 받아 실행하지 않는다. Jira 사이트 주소는 `https://<이름>.atlassian.net` 형식만 받는다(다른 호스트·경로·포트 거부 — SSRF 방지).
   - 비밀값(Jira API 토큰, 연결 토큰, `OPERATOR_TOKEN`, `SESSION_SECRET`, GitHub App 비밀·설치 토큰, PAT, 알림 웹훅 URL)은 DB·로그·응답·템플릿·백업에 넣지 않는다. Jira 토큰은 `adapters/secret_store.py` 로만 읽고 쓴다.
   - 템플릿은 외부 문자열(Jira 제목·상태 이름·프로젝트 이름)을 자동 이스케이프로만 출력한다(`|safe` 금지).
   - GLOSSARY 이름을 그대로 쓴다.
   - phase 8·11·12·14·15·16·17 의 GitHub 순환(수집 → 맡기기 → 수정 → 검토 → 초안 PR → 병합 추적 → 업무 완료), 사람별 내 차례, 직접 작업·PR 신호, 소유자 승인·꺼진 러너 대기·검증만 다시가 그대로 동작한다.
3. 성공이면 `phases/18-jira/index.json` 의 이 step 만 `completed` 로 바꾸고, `summary` 에 한 줄로 남긴다: 생성·수정·삭제 파일, 결정, 검증 결과(테스트 수), 다음 step 이 주의할 점.
4. 3회 수정 후에도 실패하면 `error` + `error_message` 를 기록한다. 사용자 결정·외부 자원이 필요하면 `blocked` + `blocked_reason` 을 기록한다. 실행하지 않은 검증을 `completed` 로 표시하지 않는다.

## 금지사항

- 실제 Jira(`*.atlassian.net`·`api.atlassian.com`)·GitHub(api.github.com·github.com)·Discord·외부 웹훅·실제 Claude/Codex 를 호출하지 않는다(공식 문서 읽기는 허용). 이유: 모든 step 은 httpx `MockTransport`·가짜 Jira·가짜 GitHub·가짜 도구(PATH 앞의 가짜 `codex`/`claude`)로 검증한다. 실연동은 phase 뒤 사용자가 Jira 사이트를 만든 뒤 한다.
- 사용자가 띄워 둔 환경을 읽거나 바꾸지 않는다: 셀프호스트(compose 프로젝트 `runloom`, 포트 8000, 볼륨 `runloom_workflow-data`)·`deploy/selfhost/.env`·러너 홈(`~/Library/Application Support/workflow-connector*`)·launchd(`launchctl` 실행 금지)·`~/Library/LaunchAgents`·`~/.claude/`·`/Users/kje/demo/*`. 컨테이너를 띄우거나 멈추지 않는다. 이유: 실사용 중인 환경이다.
- 새 의존성(Python 패키지 — Jira SDK·ADF 라이브러리 포함, JS 프레임워크·번들러, 외부 CDN)을 추가하지 않는다. 이유: ADR-0002·UI_GUIDE. HTTP 는 HTTPX, ADF 는 직접 작은 변환기.
- 종류 이름(`bug_fix`·`code_review`)으로 새 분기를 만들지 않는다(`composition.py`·`worker.py` 포함). 원본 종류(`source_type`)로 가르는 것은 소스 경계 모듈(`jira_sync`·`github_sync`·되쓰기 모듈)과 원본 조회 한 곳에만 둔다. 이유: ADR-0009, AGENTS.md.
- `tasks` 표를 재생성하거나 `tasks.status` CHECK 를 바꾸지 않는다. 이유: ADR-0020.
- 모델의 "완료했다" 응답이나 프로세스 종료 코드만으로 완료 처리하지 않는다. Jira 상태도 완료 판정 근거가 아니다(업무 완료는 기존 PR 병합 신호). 이유: 계약 v1.
- 진행 댓글·PR 원격 링크·OAuth·웹훅·Jira 담당자 매핑·Jira 기준선·매핑 표 편집 화면·판단 제안(19-triage)·모니터링 확장(20-monitor)을 만들지 않는다. 이유: 사용자 결정 2026-09-30 범위 밖.
- 사용자 변경을 삭제하거나 stash·reset 하지 않는다. 커밋은 하네스가 한다.
- 이 step 범위 밖 모듈을 "개선"하지 않는다. 기존 테스트의 단정을 약하게 만들어 통과시키지 않는다.
