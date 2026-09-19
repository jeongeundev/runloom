# Jira / n8n API feasibility research

확인일: 2026-09-15. [현재 PRD](../product/PRD.md)를 기준으로 공식 문서를 조사했다. 계정 인증, 실제 API 호출, webhook 등록 또는 workflow 실행은 하지 않았다. 아래의 **문서상 사실**, **설계 제안**, **환경 확인 필요**를 구분한다. 도메인 객체나 아키텍처를 확정하는 문서가 아니다.

## 1. Jira Cloud: 기존 업무 관찰과 조율 요약 기록

### 문서상 사실

| 필요한 동작 | 공식 API / 제약 |
|---|---|
| 프로젝트의 기존 업무 가져오기 | `GET` / `POST /rest/api/3/search/jql` enhanced search가 JQL 검색과 필드 선택, `nextPageToken` 페이지 처리를 제공한다. 기존 `/search` 계열은 제거 중 표시가 있으므로 새 연동이 구형 검색을 전제해서는 안 된다. 검색에는 Browse projects 및 해당하는 issue-level security 접근이 적용된다. [Issue search](https://developer.atlassian.com/cloud/jira/platform/rest/v3/api-group-issue-search/) |
| 변경 관찰 후 최신 정보 확인 | enhanced search는 최근 변경이 즉시 보이지 않을 수 있으며 `reconcileIssues`를 통한 더 강한 read-after-write 일관성을 안내한다. 검색 결과를 즉각적인 취소 확인의 확실한 증거로 가정할 수 없다. [Enhanced search](https://developer.atlassian.com/cloud/jira/platform/rest/v3/api-group-issue-search/#api-rest-api-3-search-jql-get) |
| 요약 기록 | `POST /rest/api/3/issue/{issueIdOrKey}/comment`로 comment를 추가할 수 있다. Browse projects, Add comments 및 필요한 issue visibility가 적용되고, 성공 응답은 201이다. 상태 전이와 별도 operation이다. [Issue comments](https://developer.atlassian.com/cloud/jira/platform/rest/v3/api-group-issue-comments/#api-rest-api-3-issue-issueidorkey-comment-post) |
| 외부 상태 식별 | status는 ID·이름·category를 가진다. 같은 이름이 여러 status에 사용될 수 있어 공식 문서는 ID 식별을 권장한다. [Workflow statuses](https://developer.atlassian.com/cloud/jira/platform/rest/v3/api-group-workflow-statuses/#api-rest-api-3-status-idorname-get) |

인증 방법은 별도로 선택해야 한다. Jira는 이메일과 API token의 Basic authentication을 문서화하지만 simple scripts/manual calls 용도로 안내하고 앱에는 다른 인증 방법 검토를 권장한다. API 인증이 성공했다고 프로젝트 권한까지 부여되는 것은 아니다. [Basic authentication](https://developer.atlassian.com/cloud/jira/platform/basic-auth-for-rest-apis/), [검색 권한](https://developer.atlassian.com/cloud/jira/platform/rest/v3/api-group-issue-search/)

### Webhook 등록 경로를 섞지 말 것

- Jira admin webhook과 OAuth/Connect dynamic webhook은 등록 경로가 다르다. issue created/updated/deleted 이벤트를 받을 수 있다. HTTPS와 유효한 TLS 인증서가 필요하다. [Webhook overview](https://developer.atlassian.com/cloud/jira/platform/webhooks/)
- `POST /rest/api/3/webhook` dynamic 등록은 Connect/OAuth 2.0 앱 전용이다. 비공개 OAuth 앱은 앱 owner와 등록 사용자가 일치해야 전달된다는 제한이 있다. 동적 webhook은 30일 만료·갱신 대상이다. Basic token만으로 이 경로를 사용할 수 있다고 가정하면 안 된다. [Dynamic webhook API](https://developer.atlassian.com/cloud/jira/platform/rest/v3/api-group-webhooks/)
- admin webhook의 secret 기반 전달 검증은 `X-Hub-Signature` HMAC으로 문서화되어 있다. 이 방법을 모든 등록 방식의 공통 인증으로 일반화하지 않는다. [Webhook signing](https://developer.atlassian.com/cloud/jira/platform/webhooks/#secure-admin-webhooks)
- Jira는 지정 오류·연결 실패 시 최대 5회 재전송한다. `X-Atlassian-Webhook-Identifier`는 tenant 안에서 유일하고 재전송 시 유지된다. 중복·지연 및 재시도 소진에 따른 누락이 가능하다. [Retry / reliability](https://developer.atlassian.com/cloud/jira/platform/webhooks/#retry-policy)
- failed-webhook 조회 API는 현재 문서의 permission 설명상 Connect 앱 전용이다. 모든 연동이 이 API로 누락을 복구한다고 가정할 수 없다. [Get failed webhooks](https://developer.atlassian.com/cloud/jira/platform/rest/v3/api-group-webhooks/#api-rest-api-3-webhook-failed-get)

### 설계 제안 — 미확정

1. 취소는 Gateless가 정한 글로벌 문자열이 아니라 준비된 Jira 프로젝트의 status/resolution 규칙과 명시적으로 연결한다. `Done` category 전체를 취소로 취급하지 않는다.
2. 외부 식별자는 tenant와 issue ID를 함께 보존한다. 취소 관찰 시점과 dispatch 결정 시점을 구분한다.
3. webhook은 변경을 인지하는 경로로 취급하고, 중복 억제와 최신 상태 재확인·재동기화 경로를 결정한다. 보이지 않는 issue를 삭제·취소로 단정하지 않는다.
4. Jira comment 반영은 자동 완료 transition과 분리한다. Gateless가 쓴 요약이 다시 업무 변경 이벤트로 들어올 때 재실행이 발생하지 않도록 출처·상관관계를 식별한다.

### 실제 환경에서 확인할 사항

- Jira Cloud인지, 프로젝트 종류와 취소/완료 status ID, 필요한 custom field, 긴급성의 신뢰 근거.
- 사용할 인증 주체와 실제 Browse/Add comments 권한, issue security 및 앱 접근 제한.
- admin webhook을 준비할 수 있는지 또는 OAuth 등록이 필요한지, 선택 방식의 전달 인증.
- 읽기 API의 실제 응답 필드와 취소 반영 지연. 이번 조사에서는 get-issue 상세 문서를 도구가 정상 반환하지 않아 정확한 단건 조회 옵션 검증은 남아 있다.

## 2. n8n: 기존 자동화 호출과 결과 관찰

### 문서상 사실

| 필요한 동작 | 공식 인터페이스 / 제약 |
|---|---|
| 기존 workflow에 입력을 보내 실행 | Webhook node는 외부 요청으로 workflow를 시작하고 데이터를 입력받는 공식 trigger이다. Production URL은 workflow publish 시 등록된다. Basic/Header/JWT 인증을 지원한다. [Webhook node](https://docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.webhook/) |
| 요청 응답 해석 | `Immediately`는 시작 응답이고, `When Last Node Finishes`는 마지막 노드 결과를 반환한다. Respond to Webhook으로 응답을 별도 구성할 수도 있다. 따라서 HTTP 2xx만으로 업무 결과 충족을 판정할 수 없다. [Webhook response modes](https://docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.webhook/#respond) |
| workflow 정의·상태 조회 | public API의 workflow resource에는 조회·생성·수정·publish 관련 operation이 있다. 조사한 현재 Workflow/Executions 목록에서는 workflow ID만으로 새 실행을 시작하는 generic REST run operation을 확인하지 못했다. `activate`를 단일 실행 요청으로 해석해서는 안 된다. 이는 확인한 목록에 대한 판단이며, n8n의 모든 다른 인터페이스에서 실행이 불가능하다는 주장은 아니다. [Workflow API](https://docs.n8n.io/connect/n8n-api/workflow), [Executions API](https://docs.n8n.io/connect/n8n-api/executions) |
| 실행 조회 | `GET /api/v1/executions`와 `GET /api/v1/executions/{executionId}`가 존재한다. `includeData`로 상세 데이터를 요청하며 execution ID, workflow ID, status를 구분한다. 데이터 저장·redaction·크기 제한에 따라 상세 결과가 제공되지 않을 수 있다. [Executions API](https://docs.n8n.io/connect/n8n-api/executions) |
| 비동기 결과 전송 구성 | HTTP Request node로 외부 REST endpoint를 호출할 수 있다. 이것은 callback을 구성할 수 있는 기능이지, 임의 workflow가 Gateless 결과 계약을 자동 제공한다는 뜻은 아니다. [HTTP Request node](https://docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.httprequest/) |

Public API는 `X-N8N-API-KEY` 인증을 안내한다. 무료 trial에서는 public API를 사용할 수 없다고 명시하며, Enterprise에서 API key scope 제한을 제공하고 비 Enterprise key는 해당 계정 자원·기능에 full access라고 설명한다. **Webhook trigger 인증과 public API key 인증은 별개**다. Public API 사용 불가를 Webhook trigger 사용 불가로 확대 해석하지 않는다. [API availability](https://docs.n8n.io/connect/n8n-api/), [API authentication](https://docs.n8n.io/connect/n8n-api/authentication/)

### 설계 제안 — 미확정

1. 선택한 기존 workflow의 Production Webhook을 명시적인 실행 entry point로 준비한다. 임의 workflow ID만 알면 실행할 수 있다고 설계하지 않는다.
2. Gateless request/attempt 식별자를 workflow 입력에 전달하고, 응답 또는 callback에서 돌려받는다. n8n execution ID와 별개로 연결한다. 가장 최근 execution을 이번 요청의 실행으로 추측하지 않는다.
3. 비동기 사용 시 접수와 terminal 결과를 별도로 전달한다. callback 인증, 결과 계약, 미도착 시 확인 경로를 정한다. 무조건 재요청하면 같은 자동화가 중복 수행될 수 있으므로 응답 유실을 실행 실패로 단정하지 않는다.
4. workflow의 성공은 그 workflow가 반환할 업무 증거의 적합성과 별개다. 성공 상태뿐 아니라 요청한 결과·검증 근거를 확인한다.
5. Gateless history에 필요한 최소 결과를 보존한다. n8n 상세 execution data가 영구 보존된다고 전제하지 않는다.

### 실제 환경에서 확인할 사항

- n8n Cloud/self-hosted, 배포 버전, trial 여부 및 public API 가용성.
- 실제 자동화의 목적, Production Webhook 제공 여부, 입력·출력 계약, workflow 내부 자격 증명과 허용 action.
- 동기 응답으로 끝낼 수 있는 시간인지, callback·오류 경로를 추가해야 하는지.
- execution 상세 저장·redaction·보존 설정, 조회 권한, timeout·중복 호출 시 실제 행동.

## 3. Domain Model 이전에 반영할 제약

다음은 위 API 사실에서 도출한 **설계 검토 항목**이며 새 객체 확정안이 아니다.

- 서비스 제품명과 읽기·쓰기·실행 capability를 1:1로 묶지 않는다.
- 외부 업무 상태, Gateless의 조율 상태, 외부 execution 상태를 각각 연결한다.
- 요청 접수, 실제 실행 시작, 실행 종료, 결과 충족을 구분한다.
- 원본 자원 ID·외부 execution ID·Gateless 요청/시도 ID·delivery ID를 혼동하지 않는다.
- webhook 등록 방식과 인증 주체, 전달 검증, 적용 범위를 integration별로 명시한다.
- 외부 시스템의 실제 사용 권한과 owner가 Gateless에 허용한 범위는 따로 검증한다.

이 조사로 문서상 실행 경로는 확인했지만, 계정별 실제 성공과 전체 왕복은 입증하지 않았다. [통합 검토 문서](INTEGRATION_FEASIBILITY.md)에서 다른 서비스·Agent 연결과 함께 검토한다.
