# 벤치마크: 외부 업무 가져오기(intake)·정규화·이슈↔PR 표시

- 작성: 2026-09-29 · 조사 범위: Linear, Jira(GitHub for Atlassian·Forms·REST), Unito, Exalate, Plane, GitHub Projects
- 근거: 각 제품 공식 문서만 사용했다(주장 옆에 URL). 공식 문서에서 찾지 못한 항목은 **확인 못 함** 으로 적었다.
- 목적: Runloom 이 GitHub Issues·Jira 를 가져와 자체 Task 형식으로 정규화하고, 이슈 옆에 PR 을 보이고, 결과를 이슈/티켓으로 되돌려 등록하는 설계의 참고.

## 요약

조사한 제품들은 공통적으로 **(1) 연결 = OAuth/앱 설치 또는 API 토큰 + 저장소/프로젝트 선택 + 필터(JQL·GitHub 검색·라벨)**, **(2) 매핑 = 제목·설명·상태·담당자·라벨·댓글 정도의 작은 공통 필드 집합 + 상태/우선순위는 "값 대 값" 표로 매핑, 사용자는 이메일로 자동 매칭, 맞지 않는 필드는 버리거나 라벨로 떨어뜨림**, **(3) 동기화 = 최초 1회 가져오기와 이후 webhook 기반 지속 동기화를 분리**하는 구조다. 커스텀 필드는 대부분 가져오지 않거나(Linear) 지원 타입만 가져온다(Plane). 충돌 처리는 "단방향이면 원본이 덮어쓴다(Plane, Unito 1-way)" 또는 "매핑 안 된 상태로는 옮기지 않는다(Linear-Jira)" 수준이며 필드 단위 병합 규칙을 문서화한 곳은 없다. **이슈↔PR 연결은 전부 "PR 제목·브랜치·커밋 메시지에 이슈 키를 넣는다"** 는 문자열 규약(Jira `JRA-123`, Linear `ENG-123`/매직 워드, GitHub `Fixes #10`, Plane `[WEB-344]`)이고, 표시는 "PR 상태(open/merged/declined) + 리뷰 상태 + 빌드 체크 요약" 한 줄이다. 정규화 "템플릿" 은 입력 측(Linear form template, Jira Forms, GitHub issue forms, Plane intake forms)에 있고, 가져온 항목은 **원본 식별자·링크를 보존한 공통 최소 필드 + 출처별 매핑 표**로 다룬다. Runloom 은 이 패턴(최소 공통 스키마 + 출처별 값 매핑 + 원본 링크/키 보존 + 키 문자열로 PR 연결)을 그대로 따르는 것이 가장 싸다.

## 비교표

| 항목 | Linear | Jira (GitHub for Atlassian) | Unito | Exalate | Plane | GitHub Projects |
|---|---|---|---|---|---|---|
| 연결 | GitHub 앱 설치(전체/선택 저장소) · Jira API 토큰/PAT(관리자 권한) | Jira 사이트 관리자 + GitHub 조직 소유자, 조직 선택 → Connect | 도구별 OAuth, "block of work"(저장소·프로젝트) 선택 | 양쪽 OAuth, 트리거 = JQL / GitHub 검색 문법 | GitHub App(조직↔워크스페이스 1:1) · Jira API 토큰+이메일+사이트 URL, JQL 필터 | 저장소 자체. auto-add 필터 |
| 매핑 모델 | 고정 매핑(Jira: 이슈 타입→라벨, Epic→Project, 커스텀 필드 미지원) | 매핑 없음(연결만) | UI 필드 쌍 + 값 매핑(상태 1:N 그룹), 사용자 이메일 매칭 | Groovy 스크립트(`replica`), `statusMap` | 가져오기 마법사: 상태→state, 우선순위 매핑, 나머지 상태 자동 생성 | 커스텀 필드(단일선택·반복·숫자·날짜·텍스트) |
| 방향·시점 | 가져오기 1회 + 선택적 단/양방향 동기화, Jira 는 webhook | 커밋/PR push 시 표시 | 1-way/2-way, webhook(GitHub·Jira 지원), 아니면 ~5분 폴링 | 트리거 + 큐 기반 순서 보장, 루프 방지 | Jira 1회(재실행으로 증분), GitHub 이슈 동기화는 webhook + 라벨 조건 | 실시간(동일 플랫폼) |
| 충돌 | Jira 상태가 Linear 에 없으면 갱신 안 함 | 해당 없음 | 1-way: 원본이 덮어씀. 동시 수정 규칙 확인 못 함 | 변경을 순서대로 큐 처리. 병합 규칙 확인 못 함 | 단방향이면 GitHub 가 덮어씀 | 해당 없음 |
| PR 연결 | 브랜치명·PR 제목에 ID, 매직 워드 | 브랜치·커밋·PR 제목에 대문자 키 | PR 을 작업 항목으로 동기화(코드 X, 생성 X) | `is:pr` 트리거로 PR 을 이슈로 동기화 | PR 제목/본문 `[WEB-344]`(상태 자동화) / `WEB-344`(링크만) | Closing keyword, Development 사이드바 수동 링크 |
| PR 표시 | 리뷰어·리뷰 상태·프리뷰 링크, PR 이벤트→상태 자동 전환 | 개발 패널: PR OPEN/MERGED/DECLINED, 리뷰, 빌드 ✓/✗, 배포 | 확인 못 함 | 확인 못 함 | 상태 자동 전환. 표시 형태 확인 못 함 | "Linked pull requests"·"Reviewers" 필드, 닫힘/병합 → Done |
| 역생성 | 양방향 시 Linear→Jira/GitHub 생성 | 해당 없음 | 2-way 흐름 + 기본 Jira 이슈 타입 지정, 필수 필드 기본값 | 스크립트로 타입 지정 | Plane 항목에 `github` 라벨 → GitHub 이슈 생성 | 드래프트 이슈 → 이슈 변환 |
| 템플릿 | 이슈 템플릿·폼 템플릿(필수 필드), Triage + Triage Intelligence | Forms(Jira 필드 링크, 필수 질문) | 확인 못 함 | 확인 못 함 | 작업 항목 타입 기반 Intake 폼·이메일 | Issue forms(YAML, `required`) |

## Linear

### 1. 연결
- GitHub: Settings > Integrations > GitHub 에서 Enable → GitHub 조직과 "All repositories" 또는 선택 저장소 → 설치·인증. 팀원은 개인 GitHub 계정을 따로 연결해 작성자 귀속을 맞춘다. https://linear.app/docs/github
- Jira 동기화: 전역 ADMINISTER 권한이 있는 Jira 계정(웹훅 설치용)의 API 토큰(Cloud)/PAT(Server), 또는 스코프(`read:jira-user`, `read:jira-work`, `write:jira-work`)를 가진 서비스 계정. Jira 스페이스 1개는 Linear 팀 1개에만 연결된다. https://linear.app/docs/jira
- Jira 가져오기: Atlassian 토큰·프로젝트 키·이메일·호스트명, Linear 관리자만. API 방식 또는 CSV 방식. https://linear.app/docs/jira-to-linear
- 모든 가져오기의 공통 단계: 인증 → 데이터 검토 → 가져올 이슈 선택(활성만/전체) → 사용자 매핑 → 확인. https://linear.app/docs/import-issues

### 2. 매핑
- Jira→Linear: Summary→Title, Assignee/Creator→같은 이름(최선 매칭), Priority→Priority, Due date, Comments, Estimate. 설명은 "markdown 으로 변환", 이슈 타입은 **라벨**(없으면 새로 생성), Epic→Project, 부모/자식→부모/하위 이슈, Components→팀 라벨, 상태는 "best effort conversion". 이미지 첨부는 가져오고 그 외 첨부는 Jira 원본 URL 로 남긴다. **커스텀 필드는 가져오지 않는다.** https://linear.app/docs/jira-to-linear
- Jira 동기화 필드: title, description, assignee, creator, priority, status, labels, due date. 이슈 타입은 기본 Task, 필수 필드가 있으면 동기화가 막힌다. https://linear.app/docs/jira
- GitHub Issues 동기화 필드: Title, Description, Status(GitHub Projects 커스텀 상태는 제외), Assignee, Labels, Sub-issues, Comments. https://linear.app/docs/github
- GitHub 가져오기: Title, Description, Labels, Projects, Comments, Sub-issue. 커스텀 필드·생성일 미이관. 사용자는 이메일·연결 계정으로 매칭, 없으면 외부 기여자로. https://linear.app/docs/github-to-linear
- 매핑 UI: 상태 값 매핑 화면의 구체 형태는 **확인 못 함**(문서는 "intelligent status mapping" 이라고만 씀).

### 3. 방향·시점·충돌
- GitHub Issues 동기화는 단방향(GitHub→Linear) 또는 양방향. 팀당 양방향 저장소는 1개. **새로 생성된 이슈만** 동기화하고 기존 이슈는 importer 로. https://linear.app/docs/github
- Jira: 단방향이면 Linear 에서 만든 이슈가 Jira 에 생기지 않는다. "Jira 에서 Linear 에 없는 상태로 옮기면 Linear 이슈 상태는 갱신되지 않는다." 삭제·이동은 전파되지 않는다. 웹훅 설치 권한이 필요하므로 webhook 기반. https://linear.app/docs/jira
- CSV 가져오기는 정적 스냅샷, API 가져오기는 이후 동기화 가능. https://linear.app/docs/jira-to-linear

### 4. PR 연결·표시
- 연결 방법: "Copy git branch name" 으로 만든 브랜치명, PR 제목의 이슈 ID, 매직 워드(`close/fix/resolve/complete/implement` 계열, 예 `Fixes ENG-123`). `{TEAM}-NEW` 로 PR 에서 이슈 생성. https://linear.app/docs/github
- 상태 자동화: 팀별로 PR drafted / opened(기본 In Progress) / review requested / ready for merge / merged(기본 Done) 에 상태 전환. 대상 브랜치 정규식별로 다른 규칙. https://linear.app/docs/github
- 표시: 리뷰어 아바타와 행동(댓글·변경 요청·승인), "Review requested", Vercel/Netlify 등 프리뷰 링크. CI 체크 표시 여부는 **확인 못 함**. https://linear.app/docs/github

### 5. 역생성
- 양방향 동기화면 Linear 에서 만든 이슈가 Jira/GitHub 에 생성된다. Jira 필수 필드가 있으면 동기화 실패 가능, 이슈 타입 기본 Task. https://linear.app/docs/jira

### 6. 템플릿·Triage
- 이슈 템플릿: team·status·priority·assignee·delegated agent·project·labels·estimate·sub-issues 를 미리 채우고 설명에 placeholder. 폼 템플릿: 텍스트·드롭다운·체크박스·날짜 + 속성 필드(priority, title, due date 등), **어떤 필드든 필수 지정 가능**. Slack·이메일·Intercom·Zendesk 입구가 템플릿으로 이슈를 만든다. https://linear.app/docs/issue-templates
- Triage: 통합(Slack·Sentry)이나 팀 외부 사용자가 만든 이슈가 들어오는 수신함. Accept(기본 상태로)·Decline·Duplicate(병합)·Snooze. Triage rules 는 조건→team/status/assignee/label/project/priority 설정. https://linear.app/docs/triage
- Triage Intelligence: 과거 데이터 기준으로 team·project·assignee·label 제안과 중복/관련 이슈 탐지. 속성별 "보여주기/숨기기/자동 적용", 1~4분 소요, Business/Enterprise. https://linear.app/docs/triage-intelligence

## Jira (GitHub for Atlassian · Forms · REST)

### 1. 연결
- 필요 권한: Jira 사이트 관리자 + GitHub 조직 소유자. Apps 에서 "GitHub for Atlassian" 설치 → GitHub 로그인 → 조직 선택 → Connect. "Only select repositories" 를 골랐다면 새 저장소는 수동 추가. https://support.atlassian.com/jira-cloud-administration/docs/integrate-with-github/

### 2. 매핑
- GitHub 이슈를 Jira 이슈로 매핑하는 기능은 없다 — 이 앱은 **개발 정보(브랜치·커밋·PR·빌드·배포) 연결** 용이다. https://support.atlassian.com/jira-cloud-administration/docs/integrate-with-github/
- Jira 쪽 형식: 소프트웨어 스페이스 기본 작업 유형은 Epic(상위)·Bug·Story·Task(표준)·Subtask(하위), 비즈니스 스페이스는 Task·Subtask. 서브태스크는 자식만 될 수 있다. https://support.atlassian.com/jira-cloud-administration/docs/what-are-issue-types/
- 생성 가능한 필드·필수 여부는 생성 화면과 같으며 `/rest/api/3/issue/createmeta/{projectIdOrKey}/issuetypes/{issueTypeId}` 의 field 객체에 `required` 로 나온다. v3 는 설명·댓글에 ADF(Atlassian Document Format)를 쓴다. https://developer.atlassian.com/cloud/jira/platform/rest/v3/api-group-issues/ · https://developer.atlassian.com/cloud/jira/platform/rest/v3/intro/

### 3. 방향·시점
- 브랜치·커밋·PR 을 push 하면 해당 이슈에 개발 정보가 나타난다. 백필 범위·웹훅 세부는 **확인 못 함**. https://support.atlassian.com/jira-cloud-administration/docs/integrate-with-github/

### 4. PR 연결·표시
- 키는 **대문자** (`JRA-123`, `jra-123` 아님). 브랜치명(`JRA-123-<name>`), 커밋 메시지(`JRA-123 <msg>`), PR 제목, 또는 PR 소스 브랜치명에 키. Smart commits 는 관리자 설정 시 추가 동작. https://support.atlassian.com/jira-software-cloud/docs/reference-issues-in-your-development-work/
- 개발 패널: PR 은 여러 개를 하나의 배지로 요약 — 열린 PR 이 하나라도 있으면 **OPEN**, 없고 병합된 게 있으면 **MERGED**, 둘 다 없고 거절된 게 있으면 **DECLINED**, 옆에 개수. 리뷰 배지(REVIEW/APPROVAL/REJECTED 등), 빌드는 모두 통과면 ✓·하나라도 실패면 실패 표시. 커밋은 최초 100개. https://support.atlassian.com/jira-software-cloud/docs/view-development-information-for-an-issue/

### 5. 역생성
- REST 로 생성할 때 createmeta 로 필수 필드를 조회해야 한다(위). createmeta 일부 엔드포인트는 폐기 공지가 있다. https://community.developer.atlassian.com/t/create-issue-meta-endpoint-deprecation/75413

### 6. 템플릿(Forms)
- Forms 의 필드를 Jira 필드에 링크(프로젝트 관리자). 같은 타입끼리만, Jira 필드 하나는 폼에서 한 번만 링크, 필수 필드는 조건부 섹션에 둘 수 없다(Summary 포함). 요청 유형(request type)에 폼을 붙이면 요청 폼에 나타난다. https://support.atlassian.com/jira-service-management-cloud/docs/link-a-form-field-to-a-jira-field/ · https://support.atlassian.com/jira-service-management-cloud/docs/add-a-form-to-the-portal-form-for-a-request-type/

## Unito

### 1. 연결
- app.unito.io → Create flow → 도구 추가·인증 → 동기화할 "block of work"(저장소·프로젝트 등) 선택 → 흐름 방향 → 규칙 → 필드 매핑 → 실행(Auto Sync 켜기/끄기). https://guide.unito.io/how-to-create-your-first-flow

### 2. 매핑
- 필드 매핑: 미리 만든 템플릿으로 자동 매핑하거나 직접 짝을 만든다. 필드 쌍마다 양방향(↔)/단방향(→). https://guide.unito.io/how-to-create-your-first-flow
- GitHub 지원 필드 16개(방향 포함): Assignee·Comment·Description·Label·Status·Title·Milestone due date 는 양방향, Created at·Issue ID·Issue number·Issue type·Link to issue·Milestone·Opened by·Repository name·Updated at 은 단방향. 규칙에 쓸 수 있는 필드는 Assignee·Issue type·Label·Milestone. https://guide.unito.io/a-guide-to-unitos-github-integration
- 상태 값 매핑: Field Mappings 의 상태 쌍 옆 톱니 → "+ Add more statuses". 한 상태를 여러 상태에 **그룹 매핑** 가능, 그룹이면 "항상 첫 번째 상태 선택". 매핑 안 된 상태는 흐름 규칙에 따라 설정. https://guide.unito.io/how-to-map-statuses-and-sections
- 사용자: 양쪽에 **같은 이메일**로 존재하면 자동 매핑. https://guide.unito.io/how-to-map-users-and-assignees
- 기본값: Jira 기본 이슈 타입을 지정해 GitHub 에서 온 항목의 타입을 정한다. 한쪽에만 필수인 필드는 기본값을 둬야 오류가 안 난다. https://guide.unito.io/jira-github-flow-guide
- Markdown↔ADF 변환 방식: **확인 못 함**.

### 3. 방향·시점·충돌
- 흐름 방향은 "항목 생성" 에만 영향, 필드 갱신 방향은 필드 매핑별. 기본 규칙은 **실행 이후 생성된 항목만** 동기화, 과거 항목은 규칙 수정으로. https://guide.unito.io/how-to-create-your-first-flow
- GitHub·Jira Cloud 모두 webhook 지원 → 실시간. 미지원 도구는 약 5분 간격 확인. https://guide.unito.io/unito-webhook-support
- 1-way 필드는 대상 쪽 수정이 원본에 반영되지 않고, 원본이 바뀌면 대상을 덮어쓴다. 양쪽 동시 수정 시 규칙은 **확인 못 함**. https://guide.unito.io/how-to-map-fields

### 4. PR 연결·표시
- PR 을 다른 도구의 작업 항목과 동기화할 수 있으나 코드는 건드리지 않고, 다른 도구에서 PR 을 만들 수는 없다. 표시 형태는 **확인 못 함**. https://guide.unito.io/a-guide-to-unitos-github-integration

### 5·6. 역생성·템플릿
- 2-way 흐름에서 반대편에 항목 생성, Jira 이슈 번호를 접두사로 동기화하는 옵션. https://guide.unito.io/jira-github-flow-guide
- 입력 정규화 템플릿 개념: **확인 못 함**(매핑 템플릿만 있음).

## Exalate

### 1. 연결
- 양쪽 OAuth(Jira 사이트 URL, GitHub 은 조직 소유자/저장소 관리자). "Quick sync"(기본 매핑) 또는 "Edit & Test"(Groovy 스크립트). https://exalate.com/blog/jira-github-issues-integration/
- 트리거: 엔티티 유형 선택 → 플랫폼 검색 문법으로 조건(Jira 는 JQL, GitHub 은 `label:bug is:open` 등) → 연결 선택 → 활성화. 기존 항목은 "Bulk Exalate" 로 시작. https://docs.exalate.com/docs/triggers-operation

### 2. 매핑
- 중간 객체 `replica` 에 보낼 필드를 담고(Outgoing), 받는 쪽이 자기 필드에 대입(Incoming). 상태 매핑 예: `def statusMap = ["open":"To Do","closed":"Done"]; issue.setStatus(statusMap[remoteStatusName] ?: remoteStatusName)` — **매핑 없으면 원래 이름 그대로** 시도. https://docs.exalate.com/docs/github-status-sync
- 서식: 형식이 다르면 변환 헬퍼로 바꾼다(예: Jira Cloud↔Azure DevOps 에서 `nodeHelper.getHtmlField`, `toMarkDownFromHtml`). https://docs.exalate.com/docs/html-markdown-conversion-guide
- 댓글: 기본은 `commentHelper.mergeComments` 로 원 작성자를 앞에 붙여 병합, 내부 댓글 필터 가능. https://docs.exalate.com/docs/github-comment-sync
- 사용자 매핑 규칙: **확인 못 함**(블로그는 개념만).

### 3. 방향·시점·충돌
- 트랜잭션 기반 큐: 변경을 원본 이벤트 순서대로 원자 단계로 적용·재시도. 연결이 꺼져도 변경을 큐에 모았다가 재개. https://docs.exalate.com/docs/sync-queue · https://docs.exalate.com/docs/triggers-operation
- 동기화 루프 방지는 "동기화로 생긴 변경과 사용자 변경을 구분" 한다고만 서술. https://exalate.com/blog/jira-github-issues-integration/

### 4. PR 연결·표시
- PR 을 이슈로 취급: GitHub 트리거 `is:pr`, Jira 쪽에서 `replica.type.name == "Pull Request"` 이면 별도 이슈 타입으로 생성. PR 링크 표시는 Atlassian 기본 앱 몫이라고 구분. https://docs.exalate.com/docs/github-pull-request-sync · https://exalate.com/blog/jira-github-issues-integration/

### 5·6. 역생성·템플릿
- 역생성은 스크립트(`issue.typeName = ...`)로 타입·필드 지정. 템플릿 개념은 **확인 못 함**.

## Plane (오픈소스)

### 1. 연결
- Jira 가져오기: API 토큰 + 이메일 + 사이트 URL → 대상 Plane 프로젝트 선택 → JQL 로 선택적 필터 → 매핑 단계. 사용자 가져오기는 CSV 또는 건너뛰기(건너뛰면 작성자가 가져온 사람으로 표시). https://docs.plane.so/importers/jira
- GitHub: GitHub App, GitHub 조직 1개 ↔ Plane 워크스페이스 1개, 전체/선택 저장소, 개인 계정 연결(선택). https://docs.plane.so/integrations/github
- GitHub **가져오기 도구는 없다**(Jira·Linear·Asana·ClickUp·Notion·Confluence·Flatfile·CSV 만). https://docs.plane.so/importers/github

### 2. 매핑
- Jira: "각 Jira 상태를 Plane state 로" 매핑 화면, 우선순위 매핑, "나머지 상태 자동 생성·매핑" 옵션. 커스텀 필드→속성(지원 안 되는 타입은 건너뜀), 이슈 타입→작업 항목 타입, 스프린트→cycle, 컴포넌트→module, 댓글·첨부·서브태스크 이관. **모든 가져온 항목에 `JIRA IMPORTED` 라벨.** https://docs.plane.so/importers/jira
- GitHub 동기화: title, description, assignees, labels, states, comments. 상태는 "Issue Open → Plane state 선택", "Issue Closed → Plane state 선택" 두 값 매핑. https://docs.plane.so/integrations/github

### 3. 방향·시점·충돌
- Jira: 1회 가져오기 + 재실행으로 증분, Server/DC 는 약 7일 내 중단 재개. https://docs.plane.so/importers/jira
- GitHub: webhook, **라벨 조건** — GitHub 이슈에 `plane` 라벨이면 Plane 으로, Plane 항목에 `github` 라벨이면 GitHub 으로. 단방향이면 GitHub 데이터가 Plane 을 덮어씀. https://docs.plane.so/integrations/github

### 4. PR 연결·표시
- PR 제목/설명에 `[WEB-344]`(대괄호) → 링크 + PR 상태(draft·opened·review-requested·approved·merged·closed)에 따라 작업 항목 상태 자동 전환. `WEB-344`(대괄호 없음) → 링크만. 작업 항목 화면의 PR 표시 형태는 **확인 못 함**. https://docs.plane.so/integrations/github

### 5. 역생성
- Plane 항목에 `github` 라벨을 붙이면 연결 저장소에 GitHub 이슈가 생성된다. 넘어가는 필드 목록은 동기화 필드와 같다고만 서술. https://docs.plane.so/integrations/github

### 6. 템플릿(Intake)
- Intake: 게스트가 만든 항목을 Accept/Decline/Snooze/Duplicate 로 분류. https://docs.plane.so/core-concepts/intake
- Intake 폼: **작업 항목 타입을 골라** 그 타입의 커스텀 속성 중 폼에 넣을 것을 선택, 폼마다 URL. Intake 이메일: 제목→title, 본문→description. https://docs.plane.so/intake/intake-forms · https://docs.plane.so/intake/intake-email

## GitHub Projects (+ Issues 기본 기능)

### 1. 연결
- 같은 플랫폼이라 인증 없음. 프로젝트에 이슈·PR·드래프트 이슈를 담고, auto-add 워크플로우로 "필터에 맞는 저장소 항목 자동 추가". https://docs.github.com/en/issues/planning-and-tracking-with-projects/learning-about-projects/about-projects · https://docs.github.com/en/issues/planning-and-tracking-with-projects/automating-your-project/using-the-built-in-automations

### 2. 매핑(필드)
- 최대 50개 필드. 커스텀: 날짜·숫자·단일 선택·텍스트·반복(iteration). 기본: assignee·milestone·labels. 뷰: 표·보드·로드맵. https://docs.github.com/en/issues/planning-and-tracking-with-projects/learning-about-projects/about-projects

### 3. 시점
- 기본 워크플로우: 이슈/PR 닫힘 → Status Done, PR 병합 → Done(기본 켜짐), 추가 시 Todo, 자동 보관. https://docs.github.com/en/issues/planning-and-tracking-with-projects/automating-your-project/using-the-built-in-automations

### 4. PR 연결·표시
- Closing keyword 9개(`close(s/d)`, `fix(es/ed)`, `resolve(s/d)`), 콜론·대문자 허용, 다른 저장소는 `Fixes owner/repo#100`. **PR 이 기본 브랜치를 향할 때만** 링크·자동 닫힘. Development 사이드바로 PR 당 최대 10개 이슈 수동 링크. https://docs.github.com/en/issues/tracking-your-work-with-issues/using-issues/linking-a-pull-request-to-an-issue
- 프로젝트 표에서 숨김 필드 "Linked pull requests"(PR 상태 보임)·"Reviewers" 를 켜서 이슈 행 옆에 PR 을 표시. 이 필드로 그룹·슬라이스는 불가, 정렬은 가능. https://docs.github.com/en/issues/planning-and-tracking-with-projects/understanding-fields/about-pull-request-fields

### 5·6. 역생성·템플릿
- Issue forms(YAML): 필수 `name`·`description`·`body`, 선택 `title`·`labels`·`assignees`·`projects`·`type`. 요소 markdown·input·textarea·dropdown·checkboxes·upload, `validations.required`. **제출 결과는 필드별 markdown 섹션으로 이슈 본문에 합쳐진다**(구조화 필드가 아님). https://docs.github.com/en/communities/using-templates-to-encourage-useful-issues-and-pull-requests/syntax-for-issue-forms

## Runloom 에 가져올 점

### 관찰에서 나온 원칙
1. **공통 최소 필드 + 원본 보존.** 모든 제품이 title/description/status/assignee/labels/comments 수준에서 멈추고, 나머지(커스텀 필드)는 버리거나(Linear) 지원 타입만 가져온다(Plane). Runloom 도 공통 필드는 작게 두고, 원본은 `raw`(스냅샷)와 링크로 보존해 필요할 때 다시 읽는다. 현재 `GitHubIssueSnapshot` + `snapshot_digest` 방식이 이 원칙과 맞다.
2. **이슈 타입은 라벨/종류로 떨어뜨린다.** Linear 는 Jira 이슈 타입을 라벨로, Plane 은 작업 항목 타입으로 옮긴다. Runloom 은 이미 `kind` 가 있으므로 "출처 타입·라벨 → `kind`" 매핑 표(워크스페이스 등록 데이터, ADR-0009 와 같은 방식)로 푼다.
3. **상태는 값 대 값 표, 모르는 값은 건드리지 않는다.** GitHub 은 open/closed 두 값(Plane), Jira 는 워크플로우 상태 이름. Linear 처럼 "매핑 없는 상태로는 옮기지 않는다" 가 가장 안전하다. Exalate 의 "없으면 원래 이름" 은 Runloom 의 고정 상태 모델과 맞지 않는다.
4. **가져오기 후 첫 관문은 수신함(Triage/Intake).** Linear·Plane 모두 외부 유입을 곧바로 실행하지 않고 Accept/Decline/Duplicate 를 거친다. Runloom 에서는 "가져옴 → 종류 매핑 확인 → 실행 대기" 로 두면 된다(모델의 완료 주장 금지 원칙과 같은 결).
5. **PR 연결은 키 문자열 규약 + 한 줄 요약 표시.** 모든 제품이 브랜치명·PR 제목·본문의 키로 연결한다. Runloom 은 자기가 PR 을 만드므로 브랜치명·PR 본문에 원본 키(`#41` / `OPS-41`)와 Runloom Task ID 를 **둘 다** 넣으면 GitHub(closing keyword)·Jira(개발 패널)·Runloom 셋 모두에서 연결된다. 표시는 Jira 개발 패널처럼 PR 상태 배지(open/merged/closed) + 리뷰 상태 + 체크 ✓/✗ 요약 한 줄. 단, GitHub closing keyword 는 기본 브랜치 대상 PR 에서만 동작한다.
6. **역생성은 출처별 필수 필드를 미리 알아야 한다.** Jira 는 project·issuetype·summary + createmeta 의 required 필드, 설명은 ADF. Unito 처럼 "기본 이슈 타입·기본값" 을 연결 설정에 둔다. GitHub 은 title 만 필수.
7. **동기화는 1회 가져오기와 지속 수집을 분리.** Linear·Unito 모두 기본은 "연결 이후 새 항목" 이고 과거 항목은 별도 가져오기. Runloom 은 셀프호스트라 webhook 수신이 어려울 수 있으므로 현행 폴링(`sync_source`)을 유지하고, Unito 가 문서화한 "webhook 없으면 약 5분" 이 기준선으로 쓸 만하다.
8. **입력 템플릿은 출처 쪽에 두고, Runloom 은 결과만 읽는다.** GitHub issue forms 는 결과가 본문 markdown 섹션이고, Jira Forms 는 Jira 필드로 링크된다. Runloom 은 본문 섹션 파싱을 강제하지 말고 `body` 를 그대로 Task 요청으로 넘기되(현 `Issue.body → Task.request` 와 같음), 종류 판정에 필요한 값만 라벨/타입에서 읽는다.

### 제안: 최소 정규화 Task 스키마 (`SourceItem` → `Task`)

가져온 외부 항목의 정규형. 기존 `Issue`(domain/task_sources.py)·`GitHubIssueSnapshot` 을 확장하는 형태로, 새 필드는 표에서 ★.

| 필드 | 뜻 | GitHub Issue 에서 | Jira Issue 에서 | 근거 |
|---|---|---|---|---|
| `source` | 출처 종류 | `"github"` | `"jira"` | 전 제품 공통 |
| `source_instance` ★ | 연결 단위 | `repository_full_name` | 사이트 URL + 프로젝트 키 | Linear·Plane 의 저장소/스페이스 선택 단위 |
| `external_id` ★ | 불변 ID(중복 방지) | `issue_id` | `id` | 번호·키는 이동 시 바뀔 수 있음(Linear: 이동 미전파) |
| `key` | 사람이 읽는 키, PR 연결 키 | `#41` (`owner/repo#41`) | `OPS-41` (대문자) | Jira 대문자 규약, GitHub 교차 저장소 문법 |
| `url` | 원본 링크 | `html_url` | `https://<site>/browse/OPS-41` | Linear 비이미지 첨부를 원본 URL 로 남기는 방식 |
| `title` | 제목 | `title` | `fields.summary` | 전 제품 공통 |
| `body` | 요청 본문(markdown) | `body` 그대로 | `fields.description`(ADF) → markdown 변환 | Linear "converted into markdown", Exalate 변환 헬퍼 |
| `source_type` ★ | 출처 타입 원문 | 이슈 타입(없으면 null) | `fields.issuetype.name` (Bug/Story/Task…) | Linear 는 라벨로, Plane 은 타입으로 보존 |
| `labels` | 라벨 원문 | `labels[].name` | `fields.labels` + components | Linear Components→라벨 |
| `source_status` ★ | 출처 상태 원문 | `open`/`closed` | `fields.status.name` (+`statusCategory`) | Plane open/closed 매핑, Jira 워크플로우 상태 |
| `assignees` | 담당자 표시용 | `assignee_logins` | `fields.assignee.displayName`/`accountId` | 매칭은 표시만(이메일 매칭은 Unito·Linear 방식, Runloom 1인용이면 불필요) |
| `blocked_by` | 선행 키 | 본문/링크 규약 | `issuelinks`(blocks) | 현 `Issue.blocked_by` |
| `parent_key` ★ | 상위 항목 | sub-issue 부모 | `fields.parent.key` (Epic/Subtask) | Linear 부모/하위 이관 |
| `updated_at` | 변경 감지 | `updated_at` | `fields.updated` | 폴링 커서 |
| `raw_digest` | 원본 스냅샷 해시 | `snapshot_digest` | 같은 방식 | 입력 변경 재평가 |

Runloom 쪽에서 정해지는 값(출처 원문이 아니라 **매핑 표**로 결정):

| Task 필드 | 결정 방법 | 기본값 |
|---|---|---|
| `kind` | 워크스페이스 등록 매핑: (`source`, `source_type` 또는 `labels`) → `kind`. 위에서부터 첫 일치 | 매핑 없음 → 수신함 대기(실행 안 함) |
| 수신 여부 | 출처 상태 매핑: GitHub `open`, Jira 지정 상태/카테고리(예: To Do) 만 가져옴 | 매핑 없는 상태는 가져오지 않음/갱신 안 함 |
| `source_ref` | `key` | — |

PR·결과 역등록:

| 동작 | GitHub | Jira |
|---|---|---|
| PR 연결 | 브랜치 `runloom/<task_id>-41`, PR 본문 `Fixes #41` (기본 브랜치 대상일 때만 자동 닫힘) | 브랜치·PR 제목에 `OPS-41` → 개발 패널 표시(GitHub for Atlassian 설치 전제) |
| PR 표시(Runloom 화면) | Task 행 옆 한 줄: PR 상태(open/merged/closed) · 리뷰 상태 · 체크 ✓/✗ — Jira 개발 패널 요약 방식 | 같음(PR 은 GitHub 에서 읽음) |
| 결과 댓글 | issue comment(markdown) | comment(ADF 로 변환) |
| 새 항목 등록 | `title` 필수, `labels`·`assignees` 선택 | `project`·`issuetype`·`summary` + createmeta `required` 필드. 연결 설정에 기본 이슈 타입·기본값(Unito 방식) |

### 하지 않을 것 (조사로 확인된 비용)
- 양방향 필드 동기화: 문서화된 충돌 규칙이 있는 제품이 없고, Exalate 는 트랜잭션 큐까지 필요했다. Runloom 은 **원본 → Runloom 단방향 수집 + 결과는 댓글/새 항목/PR 로 추가만** 한다.
- 커스텀 필드 일반 매핑: Linear 도 하지 않는다. 필요한 값은 라벨·타입으로 받는다.
- 사용자 계정 매칭: 셀프호스트 1인용 전제에서는 표시 문자열만 보존한다.
