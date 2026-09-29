# Jira 연동 조사 — Runloom 셀프호스트 기준 (2026-09-29)

> 범위: Jira Cloud 우선, Data Center 는 차이만. 1차 출처(developer.atlassian.com, support.atlassian.com, confluence.atlassian.com, Jira Cloud v3 OpenAPI 스펙)만 근거로 쓴다. 커뮤니티 글은 "(커뮤니티)" 로 표시하고 결정 근거로 쓰지 않는다. 문서에 없으면 **확인 못 함**.
> OpenAPI 스펙 원본: https://developer.atlassian.com/cloud/jira/platform/swagger-v3.v3.json (2026-09-29 내려받아 엔드포인트·스코프·파라미터를 직접 확인했다. 아래 "[스펙]" 표시는 이 파일이 출처다.)

## 0. 요약

- **인증은 두 갈래가 현실적이다.** (A) API 토큰(이메일:토큰 Basic) — 등록 절차 없이 붙여넣기 한 번, 최대 365일 만료, 토큰 트래픽은 새 포인트 기반 한도 대상이 아님. (B) OAuth 2.0 (3LO) — "Atlassian 로 연결" 버튼과 사이트 선택 화면이 나오지만, **GitHub App manifest 같은 자동 앱 생성 흐름이 없다**. 셀프호스트 사용자마다 developer console 에서 앱을 직접 만들고 콜백 URL·스코프·client id/secret 를 옮겨야 한다(확인한 문서 안에서 자동 생성 API 는 확인 못 함).
- **가져오기는 폴링으로 충분하고, 폴링이 기본이어야 한다.** 웹훅은 HTTPS 공개 URL 만 받는다(127.0.0.1 불가). 검색은 `/rest/api/3/search/jql`(nextPageToken 방식)만 쓴다 — 옛 `/rest/api/3/search` 는 "Currently being removed" [스펙].
- **증분 동기화 키는 `updated`.** JQL `updated` 는 분 단위 문자열 또는 **따옴표 없는 epoch 밀리초**를 받는다 → 시간대 문제 없이 `updated >= <ms>` 커서를 쓸 수 있다.
- **쓰기(write-back)는 전부 `write:jira-work` 한 스코프로 된다**: 이슈 생성, 댓글, 전환, 원격 링크(GitHub PR), 이슈 링크, 라벨 편집.
- **본문 형식**: v3 는 description·댓글·여러 줄 커스텀 필드에 ADF(JSON) 필수. v2 는 같은 작업을 문자열로 받는다. Markdown→ADF 공식 Python 변환기는 확인 못 함.
- **개발 패널에 PR 표시**: Runloom 이 브랜치명·PR 제목에 이슈 키(`PROJ-123`)를 넣고, 사용자의 Jira 에 "GitHub for Atlassian" 앱이 설치돼 있으면 된다. Runloom 쪽 추가 API 호출은 필요 없다. 그 앱이 없으면 원격 링크로 PR URL 을 붙이는 방식으로 대신한다.

## 1. Runloom 권장 설계

### 1.1 인증 선택 — 권장: "API 토큰 기본 + OAuth 는 나중(선택)"

| | API 토큰 (Basic `email:token`) | OAuth 2.0 (3LO) |
|---|---|---|
| 사용자 절차 | id.atlassian.com 에서 토큰 생성 → 사이트 주소·이메일·토큰 붙여넣기 | developer console 에서 앱 생성 → 콜백 URL 입력 → 스코프 추가 → client id/secret 을 Runloom 에 입력 → "연결" 버튼 → 사이트 동의 |
| "버튼으로 연결" 느낌 | 없음 (붙여넣기) | 있음. 단 앱 생성은 사용자가 손으로 해야 함 |
| 만료 | 생성 시 1~365일, 기본 1년 | 액세스 토큰 `expires_in` 만큼, 리프레시 토큰은 회전식·90일 비활성 만료 |
| 권한 범위 | scoped 토큰이면 스코프 제한 가능, unscoped 는 사용자 권한 전체 | 앱 스코프 ∩ 사용자 권한 |
| 호출 주소 | unscoped: `https://{site}.atlassian.net/rest/api/3/...` / scoped: `https://api.atlassian.com/ex/jira/{cloudId}/...` | `https://api.atlassian.com/ex/jira/{cloudId}/...` + Bearer |
| 속도 제한 | 기존 버스트 한도만 (포인트 한도 비대상) | 2026-03-02부터 포인트 기반 시간당 한도 + 버스트 |
| 웹훅 API 등록 | 불가 (동적 웹훅은 Connect·OAuth 앱 전용) | 가능 (단 HTTPS 공개 URL 필요) |
| 저장할 비밀값 | 토큰 1개 | client secret + 회전하는 refresh token |

- **근거와 트레이드오프**
  - 3LO 는 콜백 URL 이 앱마다 1개이고 사용자가 console 에서 직접 설정한다. Runloom 은 공개 URL 이 없으니 콜백은 `http://127.0.0.1:8000/...` 이 되어야 하는데, **공식 문서는 localhost·http 허용 여부를 말하지 않는다**(확인 못 함). 커뮤니티 답변은 "localhost 면 http 가능, 그 외는 https" 라고 한다(커뮤니티). 채택 전에 실제 console 에서 1회 확인이 필요하다.
  - 3LO 의 이점(버튼·사이트 선택)은 "앱을 한 번 만들고 여러 사람에게 공유하는" 모델에서 크다. 셀프호스트 1인 사용자에게는 앱 생성 단계가 오히려 토큰 붙여넣기보다 길다. 반면 Runloom 운영자가 **공용 3LO 앱을 하나 등록해 client secret 을 배포물에 넣는 방식은** 비밀값을 배포물에 넣는 것이 되어 AGENTS.md 의 비밀값 규칙과 충돌한다 — 채택하지 않는다.
  - 결론: 1단계는 **API 토큰(scoped 권장, unscoped 도 허용)**. 붙여넣은 뒤의 "프로젝트 고르기" 화면은 OAuth 와 똑같이 만들 수 있으므로 표준 온보딩 체감의 대부분은 확보된다. 3LO 는 "Cloudflare Tunnel 로 HTTPS 가 있고 웹훅까지 원하는 사용자"용 고급 경로로 미룬다.
  - 참고: 공식 문서는 Basic 인증을 "simple scripts and manual calls" 용으로 권장하고 앱에는 OAuth 를 권한다 (https://developer.atlassian.com/cloud/jira/platform/basic-auth-for-rest-apis/). 셀프호스트 1인 도구라는 성격상 이 권고를 알고 선택하는 것으로 기록해 둔다.
- Forge / Connect 는 제외: Connect 는 2027-01-31 지원 종료, Forge 는 Atlassian 인프라에서 돌고 Forge Remote 백엔드는 인터넷에서 접근 가능해야 한다(§2.4).
- Data Center 사용자는 PAT(Bearer) 한 경로만 두면 된다(§2.5).

### 1.2 온보딩 화면 (API 토큰 경로)

1. **Jira 연결** — 입력: 사이트 주소(`https://xxx.atlassian.net`), 이메일, API 토큰. "토큰 만들기" 링크로 `https://id.atlassian.com/manage-profile/security/api-tokens` 를 새 창으로 연다. 안내 문구: scoped 토큰 권장 스코프 = `read:jira-work`, `write:jira-work`, `read:jira-user`, 만료일 입력 권장.
2. **확인** — 서버가 `GET https://{site}/_edge/tenant_info` 로 cloudId 를 얻고(scoped 토큰은 `api.atlassian.com/ex/jira/{cloudId}` 로만 동작하므로 필수), `GET /rest/api/3/myself` 로 계정 이름·accountId 를 보여준다. 실패 시 401/403 을 "토큰·이메일 불일치" / "스코프 부족" 으로 구분 표시.
3. **프로젝트 고르기** — `GET /rest/api/3/project/search?query=&maxResults=50` 결과를 검색 가능한 목록으로. 여러 개 선택.
4. **가져올 범위** — 프로젝트별로: 이슈 유형 체크박스(`GET /rest/api/3/issue/createmeta/{project}/issuetypes`), 상태 범위("완료 아님" 기본 = `statusCategory != Done`), 시작 시점("지금부터 / 열린 이슈 전부" — GitHub 소스의 `intake` 와 같은 뜻), 선택적 추가 JQL(고급).
5. **필드 대응** — 인수 조건(acceptance criteria) 같은 커스텀 필드를 이름으로 고르게 한다. 후보는 createmeta 필드 목록(`.../issuetypes/{issueTypeId}`)에서 `schema.custom` 이 있는 여러 줄 텍스트 필드. 선택 결과는 `customfield_10xxx` ID 로 저장.
6. **쓰기 설정** — 결과 반영 방식 토글: 진행 댓글, 완료 시 전환할 상태(전환 목록은 실제 이슈마다 다르므로 "상태 이름"으로 저장하고 실행 시 전환 ID 를 찾는다), 후속 이슈를 만들 프로젝트·이슈 유형, PR 연결 방식(개발 패널 / 원격 링크).

토큰은 기존 `secret_store`(파일, 0600)에 두고 DB 에는 사이트 URL·cloudId·accountId·설정만 둔다 — AGENTS.md 비밀값 규칙과 맞춘다. (주의: AGENTS.md 는 비밀값을 "환경변수에서만" 읽으라고 하는데, GitHub App 비밀은 이미 `secret_store` 파일을 쓰고 있다. Jira 토큰 보관 위치는 같은 방식을 따를지 설계 단계에서 확정 필요.)

### 1.3 가져오기·동기화 루프 (기존 `github_sync.py` 와 같은 모양)

```
jql = project in (A,B) AND issuetype in (...) AND <사용자 추가 조건>
      AND updated >= <cursor_ms>            # 따옴표 없이 epoch ms
      ORDER BY updated ASC, key ASC
GET /rest/api/3/search/jql?jql=...&fields=<목록>&maxResults=100[&nextPageToken=...]
  → isLast / nextPageToken 이 없을 때까지
  → 각 이슈 upsert (키: cloudId + issue.id, 표시: issue.key)
  → 새 cursor = 이번에 본 가장 늦은 fields.updated (포함 경계 — 같은 이슈 재수신은 upsert 로 흡수)
```
- 최초 가져오기는 커서 없이 "열린 이슈 전부" 또는 "지금부터". `updated` 조건이 없으면 반드시 프로젝트 조건 등으로 **bounded** JQL 이어야 한다 (unbounded 는 거부) [스펙].
- `fields` 는 기본값이 `id` 뿐이므로 반드시 명시: `summary,description,status,issuetype,priority,labels,assignee,reporter,updated,created,parent,issuelinks,<선택한 customfield>` [스펙].
- 주기: 1~5분. 429 는 `Retry-After` 존중, 지수 백오프 + 지터(§2.3 속도 제한).
- 삭제·권한 상실 감지: 폴링으로는 삭제 이벤트가 오지 않는다. 주기적으로(예: 하루 1회) Runloom 이 들고 있는 열린 업무의 id 를 `POST /rest/api/3/issue/bulkfetch`(최대 100, 조건 맞으면 1000)로 확인해 빠진 것을 "원본 없음" 으로 표시.
- 쓰기 직후 재조회: 검색은 결과 반영이 늦을 수 있으니 방금 쓴 이슈는 `reconcileIssues`(최대 50 id)로 넘긴다.
- 상태 변화 이력이 필요하면 `POST /rest/api/3/changelog/bulkfetch`(최대 1000 이슈, 필드 10개 필터)로 받는다 — 1단계에는 필요 없음.

### 1.4 쓰기 작업 (전부 `write:jira-work`)

| Runloom 결과 | Jira 호출 | 주의 |
|---|---|---|
| 후속 이슈 등록 | `POST /rest/api/3/issue` `{fields:{project:{key}, issuetype:{id}, summary, description:<ADF>, labels:[...]}}` | 필수 필드는 createmeta 로 사전 확인. 등록 후 원본과 이슈 링크 |
| 진행 댓글 | `POST /rest/api/3/issue/{key}/comment` `{body:<ADF>}` | 같은 댓글 반복 방지는 Runloom 쪽 기록으로 (Jira 는 멱등 키 없음 — 확인 못 함) |
| 상태 전환 | `GET .../transitions` 로 목표 상태(`to.name` 또는 `to.statusCategory`)에 가는 전환 id 찾기 → `POST .../transitions {transition:{id}}` | 전환은 현재 상태에 따라 다름. 화면 있는 전환은 필수 필드 요구 가능(`expand=transitions.fields`) |
| GitHub PR 연결 | `POST /rest/api/3/issue/{key}/remotelink` `{globalId:"github-pr:<owner>/<repo>#<n>", object:{url,title,icon,status:{resolved}}}` | `globalId` 가 같으면 갱신 → 멱등. GitHub for Atlassian 앱이 있으면 이 호출 없이 개발 패널로 대체 가능 |
| 이슈 간 관계 | `POST /rest/api/3/issueLink` `{type:{name:"Blocks"}, inwardIssue, outwardIssue}` | 링크 유형 이름은 사이트마다 다를 수 있어 `GET /rest/api/3/issueLinkType` 로 확인. 중복 요청은 "생성됨" 으로 응답 |
| 라벨 | `PUT /rest/api/3/issue/{key}` `{update:{labels:[{add:"runloom"}]}}` | 전환은 여기서 안 됨 |

ADF 는 Runloom 이 만드는 문장이 단순(문단·목록·코드·링크)하므로 **작은 Markdown→ADF 변환기를 직접 쓰는 것**이 현실적이다. 대안은 같은 작업을 v2 엔드포인트(`/rest/api/2/...`, 문자열 본문)로 호출하는 것 — v2 와 v3 는 같은 작업 집합이다. 다만 v2 문자열이 어떤 문법(wiki markup)으로 렌더링되는지는 v3 intro 에 명시가 없어 확인 못 함.

### 1.5 정규화된 업무로의 필드 대응

| 정규화 필드 (현 `domain.task_sources.Issue` 기준 + 추가) | Jira 원천 |
|---|---|
| `source` | `"jira"` |
| `key` | `issue.key` (표시·PR 제목용). 내부 고유키는 `cloudId:issue.id` — 키는 이슈 이동 시 바뀔 수 있음 (get issue 가 이동된 이슈를 찾아준다는 스펙 문구로 추정, 키 변경 규칙 자체는 확인 못 함) |
| `title` | `fields.summary` |
| `body` | `fields.description`(ADF) → Markdown 으로 변환해 저장. 원본 ADF 도 보관 권장 |
| `acceptance_criteria` | 사용자가 고른 `customfield_xxxxx` (없으면 비움) |
| `labels` | `fields.labels` (+ 선택: `issuetype.name`, `priority.name` 를 라벨처럼) |
| `blocked_by` | `fields.issuelinks` 중 "is blocked by" 방향 링크의 키 (유형 이름은 사이트별) |
| `status` / 완료 판정 | `fields.status.name` + `status.statusCategory.key` (`new`/`indeterminate`/`done`) — Runloom 완료 판정은 계약 v1 규칙을 따르고 Jira 상태는 참고 신호로만 |
| `assignee` | `fields.assignee.accountId` (이메일·username 은 쓰지 않음) |
| `url` | `{site}/browse/{key}` |
| `updated_at` | `fields.updated` (동기화 커서) |

## 2. 주제별 사실

### 2.1 인증

**API 토큰 + Basic**
- 형식: `useremail:api_token` 을 Base64 로 `Authorization: Basic`. 비밀번호 인증은 폐기됨. 문서는 Basic 을 스크립트·수동 호출용으로 권함 — https://developer.atlassian.com/cloud/jira/platform/basic-auth-for-rest-apis/
- 토큰 종류: scoped(권장)와 unscoped. scoped 토큰은 `https://api.atlassian.com/ex/jira/{cloudId}` 로 호출해야 함 — https://support.atlassian.com/atlassian-account/docs/manage-api-tokens-for-your-atlassian-account/
- 만료: 2024-12-15 이후 생성 토큰은 기본 1년, 생성 시 1~365일 지정. 그 이전 토큰도 2025-03-13 이후 1년 만료로 바뀜 — 같은 출처.
- unscoped 토큰의 폐지 일정: 확인 못 함.
- cloudId 얻기: `GET https://{site}.atlassian.net/_edge/tenant_info` → `{"cloudId": ...}` — https://support.atlassian.com/jira/kb/retrieve-my-atlassian-sites-cloud-id/

**OAuth 2.0 (3LO)** — https://developer.atlassian.com/cloud/jira/platform/oauth-2-3lo-apps/
- 등록: developer console → Create → "OAuth 2.0 integration". 권한 부여 방식 두 가지: account-level(한 번의 동의로 여러 사이트), resource-level(선택한 사이트만).
- 인가 URL 파라미터: `audience=api.atlassian.com`, `client_id`, `scope`(공백 구분), `redirect_uri`, `state`, `response_type=code`, `prompt=consent`. 암시적 흐름은 지원 안 함.
- 토큰 교환: `POST https://auth.atlassian.com/oauth/token` (`grant_type=authorization_code`, client_id, client_secret, code, redirect_uri).
- 리프레시: 스코프에 `offline_access` 필요. 회전식 — 쓸 때마다 새 리프레시 토큰, 이전 것은 무효. 비활성 만료 90일, 재사용 유예 10분. 만료되면 처음부터 재인가.
- 액세스 토큰 수명: 응답의 `expires_in` 로만 명시, 고정값은 문서에서 확인 못 함 (커뮤니티: 3600초). 리프레시 토큰 절대 만료: 확인 못 함.
- 사이트 찾기: `GET https://api.atlassian.com/oauth/token/accessible-resources` → `id`(cloudId), `name`, `url`, `scopes`, `avatarUrl`. 이후 `https://api.atlassian.com/ex/jira/{cloudId}/rest/api/3/...` 에 Bearer.
- 콜백 URL: "accessible by the app" 인 URL, `redirect_uri` 와 일치해야 함. **localhost/127.0.0.1·http 허용 여부는 공식 문서에서 확인 못 함.** 커뮤니티: localhost 면 http 가능 (https://community.developer.atlassian.com/t/oauth-2-0-callback-url-using-http-not-https/58722, 커뮤니티). 앱당 콜백 URL 1개라는 불만 글도 있음(커뮤니티).
- 다른 사용자가 쓰려면 Distribution 에서 sharing 켜기. 마켓플레이스 정식 앱이 되는 것은 아님.
- 사용자의 권한이 앱 스코프보다 항상 우선 제약. 관리자가 Connected Apps 화면에서 사용자 접근을 회수할 수 없음.
- 앱을 API 로 생성하는 방법(GitHub App manifest 에 해당): 확인 못 함.

**스코프** — https://developer.atlassian.com/cloud/jira/platform/scopes-for-oauth-2-3LO-and-forge-apps/
- classic 6종: `read:jira-user`, `read:jira-work`, `write:jira-work`, `manage:jira-project`, `manage:jira-configuration`, `manage:jira-webhook`. 가능하면 classic 을 쓰고 granular 는 부족할 때만. 앱당 50개 미만 권장.
- Runloom 필요 스코프: `read:jira-work`(검색·조회·createmeta·전환 조회·링크 유형), `write:jira-work`(생성·댓글·전환·원격 링크·이슈 링크·편집), `read:jira-user`(`/myself`), `offline_access`, 웹훅 쓸 때만 `manage:jira-webhook` [스펙의 `x-atlassian-oauth2-scopes`].

### 2.2 사이트·프로젝트 찾기
- OAuth: accessible-resources (위).
- 프로젝트: `GET /rest/api/3/project/search` — `startAt`/`maxResults`(≤100), `query`(키·이름 부분 일치, 대소문자 무시), `keys`/`id`(최대 50), `action`(view/browse/edit), `status`(실험적), `expand=description`. Browse/Administer 권한 있는 프로젝트만 [스펙].
- 현재 사용자: `GET /rest/api/3/myself` (스코프 `read:jira-user`) [스펙].

### 2.3 검색·가져오기
- `GET|POST /rest/api/3/search/jql` [스펙]
  - `jql` 은 bounded 여야 함. 예: `order by key desc` 는 거부, `assignee = currentUser() order by key` 는 허용. ORDER BY 필드 최대 7개.
  - 페이징: `nextPageToken`(첫 페이지는 없음). 마지막 페이지에는 토큰이 없고 `isLast`. 토큰은 7일 후 만료.
  - `maxResults`: 필드를 많이 요청하면 페이지당 적게 올 수 있음. id/key 만 요청할 때 최대, "max 5000".
  - `fields`: **기본값 `id`**. `*all`, `*navigable`, `-필드` 제외 문법.
  - `expand`: `renderedFields`, `names`, `schema`, `changelog` 등. `properties` 최대 5개, `fieldsByKeys`, `failFast`, `reconcileIssues`(최대 50 id), `includeArchivedProjects`.
  - 응답에 `total` 없음. 개수가 필요하면 `POST /rest/api/3/search/approximate-count`(bounded JQL, 추정치).
  - 응답의 `warnings` 는 실험적(피처 플래그).
- 옛 `GET|POST /rest/api/3/search`: `deprecated: true`, "Currently being removed" — 변경 공지 https://developer.atlassian.com/changelog/#CHANGE-2046 (공지 본문의 날짜는 페이지가 JS 렌더링이라 확인 못 함).
- 대량 재조회: `POST /rest/api/3/issue/bulkfetch` — 기본 100개, 필드를 명시적으로 ≤100개 지정·다중값 필드 없음·무거운 expand 없음 조건이면 1000개. id 오름차순 [스펙].
- 이력: `GET /rest/api/3/issue/{key}/changelog`(오래된 순 페이지), `POST /rest/api/3/changelog/bulkfetch`(최대 1000 이슈, 필드 ID 10개 필터) [스펙].
- 일관성: 검색은 쓰기 직후 오래된 값을 줄 수 있음(대부분 수 초, 일괄 작업은 수 분). `reconcileIssues` 로 지정 이슈만 강한 일관성 — https://developer.atlassian.com/cloud/jira/platform/search-and-reconcile/
- JQL `updated`: 형식 `"yyyy/MM/dd HH:mm"`, `"yyyy-MM-dd HH:mm"`, 날짜만, 상대값(`-5m` 등). 결과는 사용자 설정 시간대 기준. **따옴표 없이 숫자를 주면 epoch 이후 밀리초로 해석**. 연산자 `>=` 등 지원 — https://support.atlassian.com/jira-software-cloud/docs/jql-fields/ (Updated 절). → 문자열은 분 단위이므로 커서는 epoch ms 숫자로 쓰는 편이 안전.
- 속도 제한 — https://developer.atlassian.com/cloud/jira/platform/rate-limiting/
  - 세 가지 동시 적용: 시간당 포인트 한도, 초당 버스트 한도, 이슈별 쓰기 한도.
  - 포인트 한도 시행: 2026-03-02부터(Jira·Confluence Cloud 앱). 기본 Tier 1 은 앱 전체 공유 65,000 포인트/시간. GET 은 1 + 객체당 1점(사용자·그룹 등은 2점), 쓰기 1점.
  - **"API token-based traffic is not affected by this change"** — 토큰 트래픽은 기존 버스트 한도만. OAuth 3LO·Forge 는 포인트 한도 대상.
  - 버스트: GET·POST 100 rps, PUT·DELETE 50 rps. 이슈별 쓰기: 2초에 20회, 30초에 100회.
  - 429 + `Retry-After`, `X-RateLimit-*`, `RateLimit-Reason`(`jira-quota-global-based`, `jira-quota-tenant-based`, `jira-burst-based`, `jira-per-issue-on-write`). 권장: 2초에서 시작하는 지수 백오프, 지터 0.7~1.3, 최대 4회.
  - 셀프호스트 사용자가 자기 3LO 앱을 쓰면 "앱 전체 공유 65,000" 은 그 사용자 한 명의 몫이 된다(추론).

### 2.4 이슈 데이터 모델
- 생성 메타 (현행): `GET /rest/api/3/issue/createmeta/{projectIdOrKey}/issuetypes`(이슈 유형 페이지), `GET .../issuetypes/{issueTypeId}`(필드 페이지: `fieldId`, `name`, `required`, `schema`, `allowedValues`, `defaultValue`, `hasDefaultValue`, `operations`, `autoCompleteUrl`) [스펙].
- 옛 `GET /rest/api/3/issue/createmeta` 는 deprecated — 공지 https://developer.atlassian.com/cloud/jira/platform/changelog/#CHANGE-1304 [스펙].
- 본문 형식: v3 는 댓글 `body`, 워크로그 `comment`, 이슈 `description`·`environment`, `textarea` 커스텀 필드에 ADF. 한 줄 `textfield` 는 문자열. v2·v3 는 같은 작업 집합 — https://developer.atlassian.com/cloud/jira/platform/rest/v3/intro/ (Version 절). v2 스펙의 댓글 `body` 는 `string` [v2 스펙 https://developer.atlassian.com/cloud/jira/platform/swagger.v3.json].
- ADF 구조: 루트 `{"type":"doc","version":1,"content":[...]}`, 블록 노드(paragraph, heading, bulletList, codeBlock, table, panel …)와 인라인(text + marks: strong, link 등) — https://developer.atlassian.com/cloud/jira/platform/apis/document/structure/ . Markdown 변환이나 공식 Python 라이브러리 언급은 이 문서에 없음 → 확인 못 함.
- 커스텀 필드: 인수 조건은 표준 필드가 아니므로 사이트마다 `customfield_NNNNN` 이 다름. createmeta 의 `schema.custom` 로 식별, 여러 줄이면 ADF (v3 intro).
- 상태·전환: `GET /rest/api/3/issue/{key}/transitions` — 현재 상태에서 가능한 전환만, Transition issues 권한 없으면 빈 목록. `expand=transitions.fields` 로 전환 화면 필드. 응답 항목: `id`, `name`, `to`(상태), `hasScreen`, `isGlobal`, `isConditional`, `isAvailable`. `POST` 로 수행, 화면 필드는 `fields`/`update` 로 [스펙].
- 편집 `PUT /rest/api/3/issue/{key}` 로는 전환 불가(무시됨) [스펙].
- 담당자: accountId. `username`/`userkey` 는 JQL 등에서 개인정보 이유로 사용 불가 [스펙 — `/search` jql 파라미터 설명].
- 우선순위: 이슈 `fields.priority` 로 받음. 우선순위 목록 엔드포인트 세부는 조사하지 않음(1단계 불필요).

### 2.5 쓰기
- 이슈 생성 `POST /rest/api/3/issue`: `fields`/`update` 로 내용, 설정 가능한 필드는 생성 화면 필드와 같음(createmeta). 하위 작업은 subtask 유형 + `parent`. 생성 시 전환도 적용 가능. 스코프 `write:jira-work` [스펙].
- 댓글 `POST /rest/api/3/issue/{key}/comment` — Browse + Add comments 권한 [스펙].
- 원격 링크 `POST /rest/api/3/issue/{key}/remotelink` — `globalId` 가 같은 링크가 있으면 **갱신**(빈 필드는 null 로). 본문: `globalId`, `application{name,type}`, `relationship`(기본 "links to"), `object{url,title,summary,icon,status}`. 이슈 링크 기능이 켜져 있어야 함, Link issues 권한 [스펙].
- 이슈 링크 `POST /rest/api/3/issueLink` — `type`, `inwardIssue`, `outwardIssue`, 선택 `comment`. 응답 본문 없음(ID 는 이슈의 `issuelinks` 로 조회). 중복 요청도 "생성됨" [스펙]. 링크 유형 목록 `GET /rest/api/3/issueLinkType` [스펙].
- 라벨·필드 편집 `PUT /rest/api/3/issue/{key}` — `notifyUsers`, `returnIssue` 파라미터. 화면 설정으로 편집 가능 여부를 검사하지 않음 [스펙].
- 쓰기 멱등 키(요청 ID 헤더 등): 확인 못 함 → Runloom 은 자기 DB 에 "이미 쓴 작업" 을 기록해 중복을 막아야 함(원격 링크만 `globalId` 로 자연 멱등).

### 2.6 웹훅 vs 폴링 — https://developer.atlassian.com/cloud/jira/platform/webhooks/
- **HTTPS URL 만 허용**, 신뢰된 인증서 필요, 허용 포트 제한(443 등). → 127.0.0.1 기본 구성에서는 웹훅 불가. Cloudflare Tunnel(HTTPS 공개 호스트) 이 있을 때만 선택지.
- 동적 웹훅(REST 등록): Connect·OAuth 2.0 앱만. OAuth 앱은 **사용자·앱·사이트당 5개**. 등록 후 **30일 만료**, `PUT /rest/api/3/webhook/refresh` 로 30일씩 연장 [스펙에도 동일 문구].
- 스펙 추가 조건: "for non-public OAuth apps, webhooks are delivered only if there is a match between the app owner and the user who registered a dynamic webhook" — 셀프호스트 사용자가 자기 앱을 만들고 자기가 등록하는 경우 충족(추론) [스펙 POST /rest/api/3/webhook].
- 이벤트: `jira:issue_created|updated|deleted`, `comment_*`, `issue_property_*`, sprint·version 이벤트. `jqlFilter` 필수이며 부분 JQL 만(`issueKey`, `project`, `issuetype`, `status`, `assignee`, `reporter`, `issue.property`, epic `cf[id]`; 연산자 `=`, `!=`, `IN`, `NOT IN`). `fieldIdsFilter` 로 특정 필드 변경만 [스펙].
- 재시도: 408·409·425·429·5xx 에 최대 5회, 5~15분 무작위 간격. `X-Atlassian-Webhook-Identifier` 로 중복 제거.
- 서명: **관리자 등록 웹훅**은 secret 설정 시 `X-Hub-Signature` HMAC. 동적(OAuth) 웹훅의 서명 방식은 이 페이지 요약에서 확인 못 함.
- 실패 조회 `GET /rest/api/3/webhook/failed` 는 Connect 앱 전용 [스펙].
- 결론: GitHub 연동과 같은 이유로 **폴링 기본**. 웹훅은 "Tunnel 있음 + 3LO 앱" 조건에서 폴링 주기를 늘리는 보조 신호로만(웹훅 수신 → 해당 이슈만 즉시 재조회, 폴링은 그대로 유지해 누락 보정).

### 2.7 Forge / Connect
- Connect: 2025-09-17 이후 마켓플레이스 신규 앱은 Forge 만. 2026-03-31 이후 Connect 앱 업데이트 불가. 지원 종료 2027-01-31 — https://www.atlassian.com/blog/development/announcing-connect-end-of-support-timeline-and-next-steps , https://developer.atlassian.com/platform/adopting-forge-from-connect/connect-end-of-support-private-apps/
- Forge: Atlassian 클라우드 인프라에 배포. 자기 사이트에는 Forge CLI 로 직접 설치 가능. 외부 백엔드를 부르는 Forge Remote 는 백엔드가 인터넷에서 접근 가능해야 함 — https://developer.atlassian.com/platform/forge/remote/ , https://developer.atlassian.com/platform/forge/distribute-your-apps/
- 판단: 셀프호스트 127.0.0.1 제품에는 맞지 않음(사용자가 Forge 개발 도구 설치·배포를 해야 하고, Jira→Runloom 방향 호출엔 공개 URL 필요).

### 2.8 GitHub PR 이 Jira 에 붙는 방식
- 사용자의 Jira 에 "GitHub for Atlassian"(GitHub for Jira) 앱이 설치·연결되어 있으면, 이슈 키를 **브랜치명**(`JRA-123-...`), **커밋 메시지**, **PR 제목**(또는 소스 브랜치명)에 넣은 개발 항목이 이슈의 개발 패널·보드에 나타남. 커밋은 이슈당 100개 한도 — https://support.atlassian.com/jira-cloud-administration/docs/integrate-with-github/ , https://support.atlassian.com/jira-software-cloud/docs/reference-issues-in-your-development-work/
- GitHub Enterprise Server 도 별도 절차로 지원 (위 integrate-with-github).
- Runloom 적용: 브랜치 `runloom/PROJ-123-<slug>` 처럼 키를 포함, PR 제목 앞에 `PROJ-123`. 이것만으로 개발 패널 표시(앱 설치가 전제). Runloom 이 그 앱 설치 여부를 API 로 알 수 있는지는 확인 못 함 → 설정 화면 토글로 "원격 링크도 붙이기" 를 두는 것이 안전.
- 개발 패널 데이터를 직접 넣는 Development Information API 는 Connect/Forge·OAuth 조건이 있을 것으로 보이나 이번에 조사하지 않음 → 확인 못 함.

### 2.9 Data Center 차이 (간단히)
- 인증: PAT(Jira 8.14+). 프로필 → Personal Access Tokens 또는 `POST {base}/rest/pat/latest/tokens`. `Authorization: Bearer <token>`. 최대 만료 기본 365일, 사용자당 기본 10개(관리자 시스템 속성으로 변경) — https://confluence.atlassian.com/enterprise/using-personal-access-tokens-1026032365.html
- API: `/rest/api/2/...` (ADF 없음, 문자열 본문). 일반 `createmeta` 는 Jira 9.0 에서 제거, `createmeta/{projectIdOrKey}/issuetypes[/{issueTypeId}]` 사용(8.4 도입) — https://confluence.atlassian.com/jiracore/createmeta-rest-endpoint-to-be-removed-975040986.html
- 검색: DC 의 검색 엔드포인트·페이징 방식(`startAt` 여부)은 이번에 DC 레퍼런스를 직접 확인하지 않음 → 확인 못 함 (Cloud 의 `search/jql` 변경은 Cloud 한정 공지).
- cloudId·accessible-resources·동적 웹훅·포인트 한도는 Cloud 개념 — DC 에 해당 없음(추론).

## 3. 열린 질문 (설계 전 확인)
1. developer console 에 `http://127.0.0.1:8000/...` 콜백이 저장되는지 실제 1회 확인 (3LO 경로를 넣을 경우).
2. Jira 토큰 보관: 기존 `secret_store` 파일 방식을 따를지, AGENTS.md 의 "환경변수만" 문구를 어떻게 적용할지.
3. 사용자 사이트에 GitHub for Atlassian 앱이 이미 있는지 → 원격 링크 기본값 결정.
4. Markdown→ADF 변환 범위(문단·목록·코드블록·링크·굵게 정도로 제한할지).
5. DC 지원을 1단계에 넣을지 (넣으면 v2 문자열 본문 경로가 필요).
