# Gateless: Domain Model 작성 전 API 활용 가능성 검토

> 조사일: 2026-09-15
> 기준: [PRD](../product/PRD.md). 제품 합의를 대체하는 Architecture/ADR가 아닌 사전 조사 자료다.
> 조사 환경 가정: Jira Cloud, GitHub.com, n8n(배포 형태 미정), owner별 Codex 또는 Claude Code. 실제 서비스 종류·버전·계정 조건은 사용자 확인 전이다.
> 검증 수준: 공식 문서 조회와 설치된 CLI의 help/version 확인. 실제 계정 인증·API 호출·Agent 실행·외부 쓰기는 수행하지 않았다.

## 1. 결론

문서에서 확인되는 기능은 기존 업무 조회, 외부 실행 요청, 산출물 확인, Agent 비대화형 실행이라는 핵심 경로를 지원한다. 그러나 단순히 API를 연결하면 Gateless의 coordination loop가 완성되는 것은 아니다.

**설계 판단:** Gateless가 제공할 핵심은 각 외부 기능을 쉽게 호출하는 UI에 더해, 요청의 책임·위임·식별자·결과 조건·대기·재개를 이어 주는 조율이다. 외부 서비스가 제공하는 상태와 Gateless의 업무 진행 조건을 혼동하지 않도록 integration별 계약을 먼저 정해야 한다.

이번 조사에서 구분하는 세 수준:

- **확인된 기능:** 아래 공식 출처 또는 설치된 도구 help에서 확인한 인터페이스.
- **설계 제안:** 그 인터페이스를 PRD에 맞게 사용하는 방법. 아직 확정된 도메인·아키텍처가 아니다.
- **실환경 미검증:** 계정·플랜·네트워크·자격 증명·실제 응답과 오류 조건. 문서상 가능성과 실행 성공을 구분한다.

Jira·n8n의 endpoint, 권한 및 이벤트 제약은 [외부 업무·자동화 API 조사](EXTERNAL_WORK_API_RESEARCH.md)에 상세히 기록한다.

요약하면 Jira Cloud의 기존 업무 검색에는 enhanced JQL search를 사용할 수 있고, 취소 의미는 프로젝트별 상태와 연결해야 한다. Webhook 등록 방법은 인증 방식에 따라 달라진다. n8n은 production webhook을 실행 진입점으로 사용할 수 있으나 즉시 응답과 최종 결과를 구분해야 하며, public API 접근 조건과 webhook 인증은 별개다. [Jira search](https://developer.atlassian.com/cloud/jira/platform/rest/v3/api-group-issue-search/), [Jira webhooks](https://developer.atlassian.com/cloud/jira/platform/rest/v3/api-group-webhooks/), [n8n Webhook](https://docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.webhook/), [n8n API authentication](https://docs.n8n.io/connect/n8n-api/authentication/)

## 2. 제품이 아니라 상호작용별 계약을 정의한다

다음은 **설계 제안**이다. 한 서비스가 읽기·쓰기·실행·이벤트 출처를 동시에 맡을 수 있다.

| 상호작용 | 입력 | 관찰하거나 요청하는 것 | Gateless에서 추가로 결정할 것 |
|---|---|---|---|
| Jira 업무 가져오기 | 지정 프로젝트/Issue | 목표, 현재 상태, 업무 관련 필드 | 어떤 Issue가 조율 대상인지, 책임 영역과 허용 프로젝트 |
| Jira 변경 관찰 | webhook 또는 재조회 | 원본 상태의 변경 | 취소/완료 의미, 오래된 관찰 처리, 후속 실행 금지 |
| Jira 진행 요약 반영 | 관련 Issue와 조율 요약 | comment 등 선택한 기록 동작 | 자체 기록으로 다시 발생한 이벤트의 처리, 최종 완료와의 구분 |
| GitHub 산출물 조회 | repository, PR, commit | draft PR 및 검사 증거 | 어떤 버전의 어떤 검사가 결과 조건을 충족하는지 |
| GitHub 산출물 생성 | owner별 변경 branch | draft PR 생성 | 쓰기 주체가 해당 owner 측임을 유지 |
| n8n 실행 요청 | 허용된 기존 workflow와 입력 | production webhook 등 지원 진입점 호출 | 접수와 완료 구분, 실행 식별자 및 결과 회수 계약 |
| n8n 결과 관찰 | 외부 실행 ID/상관 식별자 | 선택한 결과 API 또는 callback | 기대 결과 충족 여부, 실패/응답 유실 처리 |
| Agent 실행 요청 | 목표, scope, 필요한 context | A2A 연결부가 로컬 Agent 실행 | 슬롯, depth, 책임·양측 위임 확인 |
| Agent 후속 요청과 결과 | 구조화된 요청/산출물 | A2A status/artifact 또는 정해진 응답 | 새로운 cross-owner 요청인지 결과인지, 대기·재개 전이 |

각 행에는 사용할 인증 주체, 읽기/쓰기 범위, 원본 상태 소유자, 외부 ID, 접수 응답, 완료 확인 방법, 재시도 가능 여부, 취소 의미, 남길 history가 필요하다. 처음부터 범용 integration 프레임워크를 만들 필요는 없지만, 이 질문에 대한 답은 선택한 각 동작에 있어야 한다.

## 3. GitHub: 산출물 생성과 증거 확인

### 3.1 문서에서 확인한 기능

| 용도 | 인터페이스와 확인한 사항 | 공식 출처 |
|---|---|---|
| draft PR 생성 | `POST /repos/{owner}/{repo}/pulls`; head/base와 `draft` 입력. fine-grained token의 Pull requests write 필요. | [Pull requests API](https://docs.github.com/en/rest/pulls/pulls#create-a-pull-request) |
| PR 조회 | `GET /repos/{owner}/{repo}/pulls/{pull_number}`. PR과 head commit의 연결 확인에 사용 가능. | [Pull requests API](https://docs.github.com/en/rest/pulls/pulls#get-a-pull-request) |
| commit별 검사 조회 | `GET /repos/{owner}/{repo}/commits/{ref}/check-runs`; SHA 지정 가능, Checks read 필요. 검사 `status`와 `conclusion`이 구분됨. | [Check runs API](https://docs.github.com/en/rest/checks/runs#list-check-runs-for-a-git-reference) |
| 선택적 Actions 실행 | `POST /repos/{owner}/{repo}/actions/workflows/{workflow_id}/dispatches`; workflow_dispatch를 설정한 workflow, ref와 정해진 inputs 필요. Actions write 권한. | [Workflow dispatch API](https://docs.github.com/en/rest/actions/workflows#create-a-workflow-dispatch-event) |
| Actions 결과 조회 | `GET /repos/{owner}/{repo}/actions/runs/{run_id}`로 실행 상태와 결과 조회. | [Workflow runs API](https://docs.github.com/en/rest/actions/workflow-runs#get-a-workflow-run) |

조회한 최신 workflow dispatch 문서는 성공 응답을 run ID와 URL을 포함한 HTTP 200으로 설명한다. 과거 응답 형태를 기억에 의존해 고정하지 말고 채택할 API 버전과 실제 응답을 확인해야 한다. Actions를 실제 MVP 실행 Provider로 추가한다는 결정은 아니다. [공식 응답 정의](https://docs.github.com/en/rest/actions/workflows#create-a-workflow-dispatch-event)

### 3.2 제품 합의에 영향을 주는 제약

**확인된 사실:** PR merge endpoint는 Contents write 권한을 사용한다. 따라서 branch에 코드를 올리는 권한과 merge 금지를 단순한 토큰 permission 이름만으로 구분했다고 주장할 수 없다. [Merge API 권한](https://docs.github.com/en/rest/pulls/pulls#merge-a-pull-request)

**설계 제안:** 실제 owner 환경에서 보호 대상 branch의 규칙과 bypass 여부, 실행 도구 제약을 함께 검토해야 한다. Gateless에서 dispatch 시 scope를 확인하는 것만으로 Agent 내부의 모든 행동이 통제되는 것은 아니다. branch 규칙은 GitHub가 제공하지만 실제 사용할 계정·저장소에서 적용 가능하고 우회되지 않는지 확인해야 한다. [Repository rules](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/available-rules-for-rulesets)

**설계 제안:** 결과 증거는 PR URL 하나가 아니라 repository + PR + 검증 대상 commit SHA + 선택한 검사 식별자/판정으로 연결한다. 이전 commit의 성공이나 무관한 검사가 현재 요청을 충족시키지 않도록 한다. 검사 완료와 성공도 구분한다. 결과 기준은 제품 시나리오에서 정하며 API가 대신 정하지 않는다.

**미결인 context 경계:** 요청하는 Backend Agent가 Frontend 저장소를 읽을 수 없는 경우, PR URL만 반환하면 자동 재개에 필요한 내용을 읽지 못할 수 있다. 공유가 허용된 계약·수정 요약·검증 결과를 전달할지, 별도로 허용된 읽기 경로를 사용할지 정해야 한다. 결과 URL을 받았다는 이유로 상대 저장소 접근 권한이 생긴다고 가정하지 않는다. 이 선택은 기존 권한 경계를 유지하면서 대표 시나리오가 실제로 이어지는지에 직접 영향을 준다.

### 3.3 이벤트와 재조회

GitHub는 webhook secret 검증, 신속한 2xx 응답, `X-GitHub-Delivery` 활용을 문서화한다. 재전송 시 delivery ID가 유지된다. 실패 webhook은 자동 재전송되지 않는다. [Webhook best practices](https://docs.github.com/en/webhooks/using-webhooks/best-practices-for-using-webhooks), [Failed deliveries](https://docs.github.com/en/webhooks/using-webhooks/handling-failed-webhook-deliveries)

**설계 제안:** webhook 도착만을 유일한 진실로 삼지 않는다. 필요한 원본을 재조회하는 경로와 중복 처리 기준을 둔다. 늦게 도착한 성공 이벤트가 취소되거나 다른 commit으로 바뀐 작업을 재개하지 않도록 결과의 관련성과 현재 업무 상태를 함께 확인한다.

## 4. Agent: 기존 CLI와 A2A 사이의 연결부

### 4.1 실제 설치 환경에서 확인한 인터페이스

모델 실행이나 인증정보 열람 없이 다음 명령의 출력만 확인했다.

| 도구 | 로컬 확인 결과 | 다시 확인할 명령 |
|---|---|---|
| Codex | `codex-cli 0.154.0`. 비대화형 exec, JSONL 이벤트 출력, 최종 응답 JSON Schema, 작업 디렉터리 및 session resume 옵션 제공. | `codex --version`, `codex exec --help` |
| Claude Code | `2.1.272`. print 모드, json/stream-json 출력, JSON Schema, resume 및 허용 도구 옵션 제공. | `claude --version`, `claude --help` |

이는 **설치된 CLI help의 증거**이며 실작업 성공이나 무인 서버에서의 인증 가능성을 검증한 것은 아니다. Claude의 비대화형·구조화 출력 경로는 공식 문서에서도 확인했다. [Run Claude Code programmatically](https://code.claude.com/docs/en/headless)

### 4.2 설계상 필요한 연결 동작

다음은 도구의 기본 기능이라는 주장이 아닌 **Gateless 연결부의 설계 후보**다.

1. owner별 환경에서 허용된 입력만 받아 CLI 실행을 시작한다.
2. Agent가 `추가 요청 필요` 또는 `결과 준비`를 구조화된 응답으로 반환하게 한다.
3. 추가 요청이면 cross-owner 요청과 재개 context를 Gateless에 전달한다.
4. 실제 CLI 실행 종료/정지를 확인한 뒤 슬롯을 반환한다. 대기라는 텍스트만 출력하고 프로세스가 계속 실행 중인 경우를 슬롯 반환으로 간주하지 않는다.
5. 결과가 준비되고 슬롯이 다시 배정되면, 보존된 context와 결과로 새 CLI 실행을 시작한다.

기존 session resume는 사용 가능한 선택지지만 필수 요구사항은 아니다. 새 프로세스로 시작하려면 대화 요약 외에 작업 branch/commit, 남은 변경, 관련 산출물, 다음 행동에 필요한 입력을 어떻게 보존할지도 확인해야 한다. 실제 보존 범위는 아직 미결이다.

로컬 CLI는 owner 측 실행 수단이고 A2A는 외부 계약이다. CLI를 설치했다는 사실만으로 Gateless가 호출 가능한 A2A 서비스가 마련됐다고 가정하지 않는다. Hosted 웹에서 owner 환경에 요청을 전달하는 네트워크 경로와 실행 수명 관리는 Architecture 결정으로 남긴다.

## 5. A2A: 전달 가능한 것과 Gateless가 소유할 것

A2A 명세는 메시지 전송, task 조회, 취소 요청, 상태·artifact 교환을 정의한다. streaming/push 지원은 상대 기능에 따라 달라지고, 취소 요청의 성공은 보장되지 않는다. SendMessage 중복 방지는 선택적이므로 모든 서버가 자동으로 중복 실행을 막는다고 가정할 수 없다. [A2A specification](https://a2a-protocol.org/latest/specification/)

종료 상태의 A2A task는 다시 시작할 수 없다. 후속 작업은 같은 context와 연결된 새 task로 표현할 수 있다. 반면 입력 대기 상태의 task는 후속 입력을 받는 관계가 가능하다. [Life of a Task](https://a2a-protocol.org/latest/topics/life-of-a-task/)

**설계 결론:** Gateless Work, 외부 A2A task, CLI 실행을 하나의 생명주기로 동일시하면 안 된다. PRD의 `RUNNING → WAITING → READY → RUNNING`을 A2A 상태명에 그대로 복사하지 않는다. 어느 실행 시도에 어떤 task/프로세스가 연결되고, 슬롯이 언제 비는지는 별도로 정의한다.

**설계 제안:** 한 단계 cross-owner 요청과 원래 Work 연결, delegated scope와 결과 조건은 Gateless가 정할 애플리케이션 데이터다. A2A Agent Card의 skill을 책임자나 위임 권한의 확정 증거로 취급하지 않는다. 수신 서버의 인가와 Gateless의 사전 위임 확인은 함께 필요하다. [A2A authorization](https://a2a-protocol.org/latest/specification/#75-server-authorization-responsibilities)

선택할 A2A 버전과 SDK를 고정한 뒤 실제 메시지 형태를 검증해야 한다. 본 문서의 개념 설명을 서로 다른 버전의 method 이름·state 문자열이 호환된다는 의미로 사용하지 않는다.

## 6. Domain Model에 반영해야 할 관찰

다음은 확정 객체 목록이 아니라 **실제 외부 기능 때문에 구분해야 하는 의미**다.

| 구분 | 필요한 이유 |
|---|---|
| 원본 업무 참조와 Gateless Work | Jira 상태 소유권과 내부 대기·재개가 다름 |
| 논리적 요청과 외부 호출/실행 시도 | 전송 실패, 접수 성공, 실제 실행 성공이 다름 |
| 외부 시스템별 식별자와 상관관계 | Issue, PR, commit, n8n execution, A2A task, CLI session을 이어야 함 |
| 결과 보고와 검증 증거 | HTTP 성공/Provider 성공만으로 요청 조건을 충족하지 않음 |
| 작업 재개 context와 외부 session | 같은 프로세스가 없어도 정확한 작업 맥락이 필요함 |
| 권한과 위임 및 집행 지점 | API permission과 허용 행동 범위가 일치하지 않음 |
| 관찰 이벤트와 현재 확인 상태 | 중복·지연·누락된 외부 이벤트가 있을 수 있음 |

구조화된 history에는 연결 ID, 이벤트 종류, 관찰 시각과 제공되는 외부 발생 시각, 관련 상태·근거 참조를 보존하는 방향을 권한다. 모든 원시 payload나 credential을 저장할 필요는 없다. 이는 PRD P-13을 구체화하기 위한 제안이며 event sourcing 또는 process mining 구현을 요구하지 않는다.

## 7. 실제 검증 전에는 확정할 수 없는 것

아래는 **후속 기술 검증 항목**이며 이번 조사에서 실행하지 않았다.

1. Jira 배포 형태·프로젝트 상태/필드·인증 주체·webhook 등록 방식. 취소를 의미하는 상태와 그 변경을 읽을 권한.
2. GitHub 저장소·플랜에서 draft PR, branch 제한과 검사 증거 조회가 가능한지. owner별 자격 증명과 Gateless의 조회 권한 범위.
3. n8n 배포 형태·버전·기존 workflow의 production 진입점, 입력/결과 형태, 공개 API 접근 및 결과 보존 설정.
4. Agent의 실제 비대화형 실행, 구조화된 후속 요청 반환, 프로세스 종료 확인, 새 프로세스로의 재개.
5. 선택한 A2A 연결부의 요청 중복 처리, 인증, task 상태/CLI 종료/슬롯 반환의 대응.
6. 실제 webhook 수신 네트워크와 응답 유실 때의 복구 경로. 외부 효과가 발생했는지 모르는 요청을 무조건 재시도하지 않을 방법.

**다음 설계 순서 제안:** 환경 종류와 각 integration의 실제 동작을 확인 → 해당 제약을 반영해 Domain Model 작성 → Work Engine/MVP Spec에서 구체적인 메시지·상태·수용 기준 결정 → Architecture에서 인증·연결부·배포 구현 선택. 전체 개발 착수 전에 최소 왕복의 기술 검증이 필요하지만, 지금 제품 구현을 시작한 것은 아니다.
