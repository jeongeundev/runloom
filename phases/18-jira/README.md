# Phase 18 — Jira 소스: 연결·가져오기·상태 옮기기·후속 업무 등록

작성일: 2026-09-30. 상태: 구현 계획 작성 완료, 모든 step pending. **`service`(17-team-handoff 병합 `3392159`, 스키마 v13 — 소유자 승인 범위 = 업무)에서 실행한다.** 병합은 phase 뒤 사용자 지시로(`--no-ff`). 근거: [재설계 계획](../../docs/product/REDESIGN_PLAN.md) 7·13·14·16절, [Jira 연동 조사](../../docs/research/2026-09-29-jira-integration.md), [ADR-0020](../../docs/adr/0020-work-items-and-stages.md)(업무·단계·매핑 표·"Jira 는 전용 표를 나란히"), [ADR-0017](../../docs/adr/0017-github-app-connection.md)(비밀 저장소).

## 왜

팀은 업무를 Jira 로 관리한다(REDESIGN_PLAN 14절 — 회사 Jira 형식: 이슈 유형 버그·작업, 상태 대기·진행 중·리뷰중·종료). Jira 업무가 Runloom 업무 목록에 들어와 에이전트(또는 다른 멤버의 에이전트 — phase 17)에게 맡겨지고, 팀원이 Jira 만 보고 있어도 상태가 따라와야 한다.

## 사용자 결정 (2026-09-30)

1. **18 = Jira**(계획 순서 그대로, 19-triage·20-monitor 는 뒤).
2. **되쓰기 범위 = 상태 옮기기 + 후속 업무를 Jira 에 새로 만들기.** 진행 댓글·PR 원격 링크는 **하지 않는다**.
3. **저장소 = Jira 프로젝트마다 하나.** 연결 설정에서 "SHOP 프로젝트 → 워크스페이스에 등록된 GitHub 저장소(`github_sources`) 하나"를 고른다. 그 프로젝트 업무는 전부 그 저장소에서 실행·PR 된다. 컴포넌트별 덮어쓰기 없음.
4. **상태 옮기기 = 세 순간.** 작업 시작(에이전트 실행 착수 또는 [내 세션에서 작업]) → "진행 중", PR 열림(업무 상태 `PR · 검토`) → "리뷰중", 업무 완료(PR 병합) → "종료". 각 순간의 Jira 상태 **이름**을 프로젝트마다 연결 설정에서 고른다(후보는 그 프로젝트의 실제 상태 목록). 비워 두면 그 순간은 옮기지 않는다.
5. **Jira → Runloom = 완료 범주면 멈춤.** Jira 상태의 `statusCategory` 가 `done` 이면 GitHub 이슈 닫힘과 같이 취급한다(`source_closed` 대기 — 다음 단계를 시작하지 않음, 다시 열리면 이어감). 이미 도는 실행은 끊지 않는다. Runloom 업무를 닫지는 않는다.
6. **실연동은 phase 뒤.** 사용자가 무료 Jira Cloud 사이트와 API 토큰을 만든 뒤 1회. 모든 step 은 가짜 Jira(httpx `MockTransport`)로 검증한다.

## 계획 기본값 (사용자 확인 전 — step 0 이 ADR-0024 로 고정, 근거가 있으면 바꾸고 이유를 남긴다)

1. **인증 = API 토큰 붙여 넣기**(사이트 주소 `https://<이름>.atlassian.net` + 이메일 + 토큰). OAuth 3LO 없음. 토큰은 `adapters/secret_store.py`(0600)에 새 고정 이름으로, DB 에는 사이트 주소·cloudId·accountId·표시 이름만. 연결 확인 = `GET /_edge/tenant_info`(cloudId) + `GET /rest/api/3/myself`. 401 = "이메일·토큰 불일치", 403 = "권한(스코프) 부족". scoped 토큰은 `api.atlassian.com/ex/jira/{cloudId}` 로만 된다 — 호출 기준 주소를 어떻게 정할지는 step 0 이 조사 문서로 확정.
2. **연결은 워크스페이스에 하나**(사이트 하나). 프로젝트는 여러 개 고를 수 있다.
3. **프로젝트 설정 한 행** = 프로젝트 키·id, 연결 저장소(`github_sources.source_id`, 필수), 가져올 이슈 유형(비우면 전부), 가져오기 시작점(GitHub `intake` 와 같은 뜻 — "지금부터"/"열린 업무 전부"), 세 순간의 상태 이름, 후속 업무 이슈 유형(비우면 후속을 Jira 에 만들지 않음).
4. **가져오기 = 폴링.** `GET /rest/api/3/search/jql`(옛 `/search` 금지), `fields` 명시, `nextPageToken` 페이지, JQL `project = X AND updated >= <epoch ms> ORDER BY updated ASC, key ASC`(따옴표 없는 epoch ms — 조사 문서), 커서는 본 가장 늦은 `updated`(포함 경계, upsert 로 흡수). 페이지를 다 저장한 뒤에만 커서 전진(GitHub 과 같음). 주기·429 `Retry-After` 는 GitHub 소스와 같은 모양.
5. **업무로**: `work_items.source_type='jira'`, `source_item_id` = issue id(문자열), `source_key` = `SHOP-12`, `source_url` = `{site}/browse/SHOP-12`, `source_state` = Jira 상태 이름. 본문(ADF) → 텍스트(문단·제목·목록·코드·링크만)로 바꿔 `request`·양식 칸 읽기에 넘긴다. 종류·우선순위는 기존 매핑 표(`field_mappings`, `source_type='jira'`)로 — 입력값은 라벨 + 이슈 유형 이름 + 우선순위 이름. 기본 행 `('jira','kind','*','bug_fix')`.
6. **원본 열림/닫힘** = `statusCategory.key == 'done'` 이면 닫힘. 기존 `open`/`closed` 두 값 판정(`task_readiness`·`task_followup` 의 `source_closed`)에 그대로 넣는다 — 새 대기 코드 없음.
7. **실행·PR**: Jira 업무의 실행 대상 저장소 = 프로젝트 연결 저장소. 브랜치 `runloom/<RUN-n>`·PR 제목 `<RUN-n> <제목>` 그대로(Jira 키를 PR 제목에 넣지 않는다 — PR 원격 링크·개발 패널은 범위 밖). PR 본문의 `Fixes #N` 은 GitHub 원본일 때만. PR 감지(`RUN-n` 키)·병합 → 업무 완료는 기존 GitHub 소스 PR 동기화가 그대로 한다(연결 저장소가 `github_sources` 이므로).
8. **상태 옮기기 전송** = 되쓰기 outbox 표(Jira 전용). 업무 상태가 세 순간에 들어가면 한 행(업무 × 순간, 중복 키로 한 번만). 전송 때 `GET /issue/{key}/transitions` 에서 `to.name` 이 설정 이름과 같은(대소문자 무시) 전환을 찾아 `POST`. 이미 그 상태면 보내지 않고 `delivered`. 목표로 가는 전환이 없으면 `failed`(이유 "전환 없음 — 현재 상태 X") — 재시도하지 않는다. 화면 필수 칸이 있는 전환(400)도 `failed`. 5xx·429·네트워크는 재시도.
9. **후속 업무 등록**: 후속 규칙이 **새 업무**(`placement == "new_work"`)를 만들고 원인 업무가 Jira 원본이며 그 프로젝트에 후속 이슈 유형이 설정돼 있으면, `POST /rest/api/3/issue`(같은 프로젝트, 설정 이슈 유형, summary = 새 업무 제목, description = 짧은 Markdown → ADF, labels `runloom` + `runloom-<RUN-n>`) → 원인 이슈와 링크 `POST /rest/api/3/issueLink`(유형 "Relates" — 이름이 없으면 링크 생략·기록). 성공하면 새 업무의 `source_item_id`·`source_key`·`source_url` 을 채운다(이후 동기화가 그 이슈를 같은 업무로 받는다 — 중복 업무 금지). 응답을 잃으면 JQL `labels = "runloom-<RUN-n>"` 로 찾아 조정(멱등).
10. **스키마 v14**: Jira 전용 표를 나란히(ADR-0020 결정) — 연결 1행, 프로젝트 설정, 이슈 스냅숏(업무 연결), outbox. `work_items.source_type`·`field_mappings.source_type` CHECK 에 `'jira'` 추가(재생성 방법은 step 0 이 v13 재생성 선례와 FK 를 보고 확정 — `tasks` 재생성 금지).
11. **연결 화면**: `/connect?tab=sources` 에 Jira 칸(GitHub 칸 옆). 붙여 넣기 → 확인 → 프로젝트 고르기 → 프로젝트별 설정(저장소·이슈 유형·시작점·세 상태 이름·후속 이슈 유형) → 저장. 끊기(토큰 지움, 업무는 남김). 상태 이름 후보는 `GET /rest/api/3/project/{key}/statuses`.
12. **Markdown → ADF** 는 후속 이슈 본문에만 쓴다(문단·목록·코드·링크). 새 의존성 없음.

## 조사로 확인한 현재 (2026-09-30, `feat-17-team-handoff` step 6 시점 — step 0 이 `service` 의 17 완료 코드로 다시 확인)

- `adapters/db.py` `SCHEMA_VERSION = 13`. `github_sources`(UNIQUE session·repo, `cursor`·`pull_cursor`), GitHub 부속 표 `source_issues`(PK `(source_id, github_issue_id)`, `issue_number INTEGER`, `state IN ('open','closed')`)·`source_deliveries`(`comment_id`·`issue_number INTEGER`)·`github_assignee_bindings`·`task_pull_requests`·`work_pull_requests`·기준선 표 — 모두 `github_sources` FK.
- CHECK: `work_items.source_type IN ('github','n8n','manual')`, `field_mappings.source_type IN ('github','n8n')` — `'jira'` 없음. `chains.source` 에는 이미 `'jira'`. `work_items` 는 여러 표가 FK 로 참조한다(재생성 주의).
- 동기화: `worker.tick` → `_sync_github` → `github_sync.sync_source`(`_poll` → `_selected` → `_check_merges` → `_link_pulls`). 소스별 간격·rate-limit 대기는 워커 메모리 dict. 업무 upsert 는 `repo.upsert_source_issue` 한 트랜잭션(`source_type="github"`·`github.com` URL·`repo#N` 하드코딩). `issue_intake.py` 가 매핑 키 `"github"` 하드코딩.
- 매핑: `field_mappings (session, source_type, field∈{kind,priority}, source_value, runloom_value, position)`, 첫 일치·`*`. `domain/field_mapping.map_value(rows, source_type, field, values)` 는 범용. `mapping_api.py` `source_type: Literal["github","n8n"]`.
- 되쓰기: `github_delivery.py` 는 댓글만. 원본 상태 변경·새 이슈 생성 없음. 후속 `placement == "new_work"`(`repo.py`) 는 원인 업무의 `source_type`·`source_id`·`source_key`·`source_url` 을 복사하고 `source_item_id` 는 비운다.
- 원본 닫힘: `source_issues.state`·`work_items.source_state` → `task_readiness`(`source_closed`)·`task_followup`(`hold_code="source_closed"`), 입력은 `github_sync.task_intake_facts`·`task_cycle.py`·`worker.py`. `task_cycle.origin_source` 가 `source_type != "github"` 이면 `(None, None)` — 그래서 `source_closed`·재작업 상한·담당 바인딩이 GitHub 에만 묶여 있다.
- PR 감지: `domain/work_keys.keys_in` 은 `RUN-` 만, 정수 번호. `github_sync._pull_target` → `get_work_item_by_key`(워크스페이스 범위). PR 본문 첫 줄 `Fixes #N`(`domain/pull_request.pr_body`).
- 비밀: `secret_store.NAMES` 고정 allowlist(없는 이름은 ValueError). HTTP: `github_client.GitHubClient` Protocol + `HttpGitHubClient(transport=)`, 오류 계층 `GitHubError`/`RateLimited`/`Forbidden`/`NotFound`/`Unavailable`/`Unprocessable`. 테스트는 `httpx.MockTransport` 또는 Protocol 가짜.
- 화면: `web.py` `GET /connect?tab=sources` → `connect.html` 이 `_connect_github.html`·`_connect_inbound.html` include.
- `domain/form_sections.py` 는 Markdown `##`/`###` 절(GitHub issue forms, `_No response_`) 전제.

## 하지 않는 것

진행 댓글, PR 원격 링크·개발 패널·PR 제목의 Jira 키, OAuth 3LO, 웹훅, Jira Data Center, Jira 담당자 ↔ 멤버 매핑, Jira 기준선(생성 → 해결 지표 — 19·20 에서), 매핑 표 편집 화면(API 만), 컴포넌트별 저장소, 삭제·권한 상실 감지(bulkfetch), 판단 제안(19-triage), 모니터링 확장(20-monitor), 실제 Jira·GitHub 호출.

## Step 목록

| Step | 이름 | 산출물 |
|---|---|---|
| 0 | jira-design | ADR-0024, ARCHITECTURE "Jira 소스 — phase 18"(표·경로·시그니처·스키마 v14·상태 표), GLOSSARY, REDESIGN_PLAN 13절 |
| 1 | schema-v14 | Jira 표 4종, `source_type` CHECK 에 `jira`, v13 → v14 |
| 2 | jira-domain | `contracts/jira.py`, `domain/adf.py`(ADF → 텍스트, Markdown → ADF), `domain/jira_intake.py`(JQL·스냅숏 → 업무 칸·열림/닫힘·세 순간 판정) |
| 3 | jira-client | `adapters/jira_client.py`(Protocol + HTTPX, 오류 계층), 비밀 이름 |
| 4 | jira-connect | 연결 화면·경로: 붙여 넣기 → 확인 → 프로젝트 설정 → 저장·끊기 |
| 5 | jira-sync | `server/jira_sync.py` + 워커 tick, 업무 upsert, 원본 닫힘 → `source_closed` |
| 6 | jira-run | Jira 업무 → 연결 저장소에서 실행·초안 PR·PR 감지·완료 |
| 7 | jira-transitions | 세 순간 → outbox → 전환 전송 |
| 8 | jira-followup | 후속 새 업무 → Jira 이슈 생성·링크·조정 |
| 9 | jira-verify | e2e(가짜 Jira + 가짜 GitHub + 가짜 러너), v13 사본 마이그레이션, SELFHOST·인계 문서·실연동 확인 목록 |

## 실행

```bash
git status --short            # 깨끗해야 한다
git branch --show-current     # service (execute.py 가 feat-18-jira 를 만든다)
python3 scripts/execute.py 18-jira --engine claude
```

사용자가 띄워 둔 셀프호스트(compose 프로젝트 `runloom`)·러너(launchd)·`/Users/kje/demo/*` 는 건드리지 않는다. 병합, 셀프호스트 재설치(v14, 백업 먼저), Jira 실연동은 phase 뒤 사용자 지시로 한다.
