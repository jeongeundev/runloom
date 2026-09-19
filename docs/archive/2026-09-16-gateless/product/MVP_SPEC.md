# Gateless MVP Spec — Prepared Real Environment

> 상태: MVP 범위·prepared 실행 구성·결과 계약 freeze. 2026-09-16 실제 feasibility 검증 반영. Freeze는 GitHub 발행·제품 구현·통합 수용 검증의 완료를 뜻하지 않는다.
> 기준: [PRD](PRD.md), 확정된 [Domain Model](../domain/DOMAIN_MODEL.md), [Work Engine](../domain/WORK_ENGINE.md).
> 현재 검증: 실제 컨테이너 접근 차단, A2A HTTP task/status/artifact 및 Codex 실행 3회, shared JSON 기반 Backend compatibility 재개 성공. 임시 endpoint는 검증 후 종료했다. Demo GitHub Issue/PR/CI와 제품 통합은 아직 미구축이다. [검증 기록](../research/PREPARED_ENVIRONMENT_VERIFICATION.md) 참조.

## 1. 범위와 baseline 호환성

GitHub Issue를 external Source Work로 사용한다. Demo app의 Backend/Frontend responsibility와 실행 workspace를 실제로 분리하고, 각 Agent가 실제 코드를 수정·테스트한다. Cross-owner 요청은 Gateless가 수락·조율하고 A2A 연결부가 요청·상태·결과를 전달한다. GitHub draft PR과 지정 commit의 검사를 evidence로 사용한다. Jira와 n8n은 이번 MVP에서 제외한다.

| 변경 | Baseline에 대한 판단 |
|---|---|
| Jira → GitHub Issue | Source Work Reference의 연동 대상 교체. 목표·취소·최종 완료의 외부 소유권은 유지 |
| GitHub가 입력과 결과 출처를 함께 담당 | Issue/PR/check를 서로 다른 interaction과 식별자로 연결. 상태를 하나로 합치지 않음 |
| 신규 synthetic 업무 | 기존 고객 수요를 입증하지는 않지만 실제 handoff의 개입·시간 비교에는 사용 가능 |
| 새 A2A 연결부 준비 | 이미 합의한 실제 Agent 연결을 준비하는 일. CLI 설치를 endpoint 준비로 간주하지 않음 |
| Jira/n8n 제외 | 특정 제품이 아닌 책임 경계·결과·재개에 의존하므로 도메인/엔진 원칙 변경 없음 |

Original Work별 active Request 하나, 1-hop, owner당 슬롯 하나, READY 재진입 FIFO, non-preemption, 실행 불명 시 슬롯 유지, 실제 시작 전 인가, 자동 실패 retry·재작업 제외를 그대로 따른다.

## 2. 고정된 demo: 배송비 미정 주문 요약

준비할 app은 주문 요약 API와 이를 표시하는 작은 화면이다. 결제·로그인·DB·배포는 업무에 넣지 않는다. 시작 버전은 배송비가 항상 숫자라고 가정한다. 새 요구는 배송비가 아직 정해지지 않은 주문에서 0원으로 오표시하지 않는 것이다.

아래 이름·경로는 준비할 명세용 이름이며 실제 생성된 리소스가 아니다.

- Private demo 저장소 하나: `gateless-prepared-demo`(생성할 이름). `backend/`는 API·producer 테스트·계약 문서를, `frontend/`는 주문 화면·consumer 테스트를 담당한다.
- 실행 시 전체 clone/.git을 mount하지 않고 해당 Work에 필요한 owner subtree만 자기 workspace로 export한다. 상대 subtree는 접근할 수 없다.
- 검증된 Node 24 JavaScript ES modules와 node:test를 첫 실행 기준으로 고정한다. Backend는 작은 HTTP API, Frontend는 단일 화면으로 구성하며 별도 framework 도입은 첫 loop에 필요하지 않다. Probe에서는 핵심 producer/consumer 함수와 테스트를 실제 실행했으며 HTTP app·화면의 완성은 후속 준비 작업이다.
- 시작 commit에는 정상 동작하는 기존 app과 기준 테스트를 둔다. 정답 수정본·완성된 PR·성공 결과를 미리 넣어 재생하지 않는다.

### 2.1 최초 Work B1

Demo 저장소에 만들 실제 GitHub Issue의 제목:

> 배송비 미정 주문을 지원하고 Backend/Frontend 수정안의 계약 호환성 검증 자료를 준비한다.

목표는 **리뷰 가능한 변경안과 검증 근거**다. Merge나 배포 완료를 요구하지 않는다. Backend owner가 original Work를 맡고 Frontend의 자기 영역 수정은 한 단계 Request로 요청한다.

계약 v2에서 준비할 요구:

- `subtotal`은 음이 아닌 금액이다.
- 배송비 확정 시 `shippingFee`와 `total`은 숫자이며 `total = subtotal + shippingFee`다.
- 미정 시 `shippingFee = null`, `total = null`이고 UI는 배송비·합계를 ‘계산 중’으로 표시한다.
- 무료 배송의 숫자 0과 미정 null을 구분한다.

원래 Issue의 완료 기준은 Backend draft PR, Frontend draft PR, 각 대상 commit의 지정 검사, 공유된 consumer 사례로 실행한 Backend 호환성 검사와 보고서다. 최종 Issue 완료는 사람이 확인한다.

### 2.2 Backend Agent 첫 실행

1. 자신의 workspace에서 기존 API와 테스트를 읽는다.
2. 계약 v2에 맞춰 nullable 배송비·합계 처리와 producer 테스트를 실제 수정·실행한다.
3. 자신의 작업 branch/commit·남은 일을 보존한다.
4. 아래 R1을 구조화해 Gateless에 제안하고 실제 실행을 종료한다.

R1 결과가 필요한 이유는 Frontend가 구현·검증한 소비 사례를 Backend에서도 확인해야 원래 Issue의 호환성 검증 자료를 마칠 수 있기 때문이다. Gateless가 이를 독립적으로 발명하지 않는다. 준비된 업무 목표는 알려주되 handoff 성공·결과·재개를 script로 연출하지 않는다.

### 2.3 Cross-owner Request R1

> 계약 v2에 따라 주문 요약 화면의 확정/무료/미정 배송비 처리를 수정하고 consumer 테스트를 실행해 draft PR을 만든다. 실제 테스트에 사용한 허용된 JSON 사례와 검증 근거를 반환한다.

요청은 B1의 목표·책임 영역·계약 버전과 연결한다. 허용 행동은 Frontend의 지정 업무 branch 수정·테스트·draft PR이다. Merge, production deploy, 제3 owner 위임, 상대 저장소 접근은 허용하지 않는다.

### 2.4 Frontend Agent 실행

자기 workspace에서 응답 변환과 화면 렌더링을 수정한다. 양수 배송비, 무료 배송, 배송비 미정의 사례를 테스트한다. 계산 중 상태를 0원 또는 잘못된 합계로 표시하지 않는지 확인한다. 실제 테스트가 읽는 공유 가능한 JSON 사례를 저장하고 commit·draft PR을 만든다. 그 commit의 consumer 검사 결과와 함께 R1 결과를 반환한다.

### 2.5 Backend Agent 재개

새 Execution이 자신의 보존된 작업 상태와 허용된 consumer 사례를 받는다. Backend의 실제 API 출력이 그 사례의 응답 계약을 만족하는지 검증하는 테스트를 추가·실행한다. 양측 commit과 검증 결과를 연결한 `compatibility-report.md`를 작성하고 Backend draft PR을 마무리한다.

이는 수신 소스를 읽거나 Frontend 코드를 실행하는 단계가 아니다. 전달된 JSON 데이터는 실행 코드로 취급하지 않는다. 어떤 테스트를 작성하고 기술적으로 적합한지는 Agent가 판단하며, Gateless는 사전에 정한 증거 조건을 확인한다.

## 3. 자동 재개의 Result / Evidence / Shared context

| 구분 | R1에 필요한 내용 |
|---|---|
| Result | Frontend draft PR, 고정된 head SHA, 수정 요약, 실제 consumer 테스트가 사용하는 JSON 사례 |
| Evidence | 정확한 repo/PR/head SHA, 사전 지정한 consumer 검사 이름·출처·완료/성공 판정, 해당 사례 파일의 commit 참조 및 digest |
| 요청 shared context | 계약 v2 본문과 digest, 3가지 필수 동작, 허용 fixture 형식, B1/R1 연결, 결과 기준 |
| 반환 shared context | 허용 JSON 사례의 실제 내용, 사례별 기대 표시, 제한된 수정 요약, PR·검사 근거 참조 |
| Backend 내부 resume context | Backend branch/commit·남은 작업, 원래 Issue 목표, R1과 현재 결과 판정의 연결 |

PR URL만으로 재개하지 않는다. Backend Agent가 Frontend subtree나 private repo 전체를 읽을 수 없어도 반환된 내용으로 테스트와 보고서를 작성할 수 있어야 한다. 실제 사례 파일과 반환 내용의 digest·commit 연결을 확인해 무관한 자료를 통과시키지 않는다.

검사 이름은 준비할 기준으로 `frontend-consumer`, `backend-producer`, `backend-compatibility`를 제안한다. 기준 CI workflow·판정 설정은 Agent의 수정 허용 범위 밖에 둔다. 필수 사례를 없애거나 검사를 약화한 변경은 수용하지 않는다. GitHub 발행 adapter가 owner subtree 밖의 CI 설정 변경·경로 이탈·symlink를 거부해야 하며, 이 실제 발행 검증은 아직 완료하지 않았다. 특정 이름의 green check만으로 기술적 정답을 보증하지 않으며 결과 적합성은 수동/자동 비교에서도 별도 검토한다.

## 4. GitHub Issue 상태 계약

준비자가 만든 Issue 중 demo 대상에 명시적으로 등록한 것만 가져온다. Repo 식별과 Issue ID/번호를 함께 보존한다. PR을 Source Issue로 혼동하지 않는다.

- `open`: 명시된 조율 대상이고 관련 위임이 유효하면 실행 가능성을 평가한다.
- `closed`와 완료 사유: 외부 최종 완료로 관찰하고 후속 실행을 중단한다.
- `closed`와 미진행 사유: 이 demo에서는 취소로 해석하고 후속 실행을 중단한다.
- 사유가 없거나 다른 종료 사유: 닫힘을 존중해 새 실행은 막되 완료/취소를 추측하지 않고 확인 필요로 표시한다.
- 조회 실패는 취소가 아니다. 재개·dispatch 판단에 필요한 현재 정보를 확인할 수 없으면 보류한다.
- Agent 성공·PR/check 성공으로 Issue를 자동 close하지 않는다. Reopen도 종료한 Work를 자동 재실행하는 trigger로 쓰지 않는다.

GitHub API가 issue state와 state reason을 제공한다는 것은 [공식 문서](https://docs.github.com/en/rest/issues/issues)에서 확인했다. 위 의미 매핑은 demo 정책이며 실제 생성한 Issue에서 응답·변경 관찰을 검증해야 한다. 처음에는 명시된 Issue의 재조회 방식으로 범위를 제한하는 것을 권고한다. Webhook 등록을 첫 loop의 선행 조건으로 두지 않는다.

## 5. 단계별 실제 interaction

| 단계 | GitHub | A2A / owner 실행 | Gateless |
|---|---|---|---|
| B1 가져오기 | Issue 목표·상태, Backend 기준 commit 조회 | 아직 실행 없음 | Source reference·Work 연결, 위임·READY 판정 |
| Backend 실행 | Backend snapshot에서 실제 수정·검사, 연결부의 범위 제한 발행 | Backend endpoint에 실행 요청, 실제 CLI 실행 | 슬롯 예약·현재 인가·Execution 연결 |
| R1 제안·wait | Issue를 자동 종료하지 않음 | Backend의 구조화 요청·작업 상태 반환, CLI 종료 관찰 | Request 조건 확인, 수신 Work 생성, Backend 슬롯 반환 |
| Frontend 수행 | 신뢰된 실행 연결부가 Frontend subtree만 branch/PR로 발행, 실제 CI 검사 | Frontend endpoint로 새 실행 전달 | 수신 큐·실행 인가·상관관계 유지 |
| 결과 확인 | 허용된 조회 주체가 정확한 PR/SHA/check/fixture 증거 확인 | 구조화 Result/Evidence와 공유 자료 반환 | 요청 조건 충족 판정, 실행 종료 확인 |
| Backend resume | Backend compatibility 검사·보고서·draft PR | 보존 context와 결과로 Backend의 새 실제 실행 | 현재 Issue·위임·context 확인 후 READY 재진입·dispatch |
| 최종 검토 | 사람이 산출물 검토 후 Issue 완료 | Agent 성공이 외부 완료를 결정하지 않음 | 조율 종료와 외부 상태를 별도로 표시 |

원본 Issue, draft PR, check, A2A task, CLI session을 같은 상태나 ID로 취급하지 않는다. Agent가 상대 endpoint로 직접 우회 호출하지 않도록 연결·인증 경계도 검증한다. Jira와 n8n interaction은 없다.

## 6. Feasibility로 확정한 prepared 구성

### 6.1 한 저장소, 한 호스트, 분리된 두 실행 경계

한 private repo의 Backend/Frontend subtree를 Work별 snapshot으로 분리한다. 각 실제 Execution에는 자기 Work의 workspace 하나만 mount한다. B1이 대기 중 B2가 같은 owner 슬롯을 사용해도 B1의 미완료 파일을 덮어쓰지 않도록 Work별 작업 상태를 보존한다.

한 Docker 호스트와 공통 Node 24/Codex 0.154.0 이미지로 실행한다. 비루트 사용자, read-only root, capability 제거, no-new-privileges, 별도 ephemeral home과 제한된 mount를 적용한다. 사용자 홈·GitHub token·전체 Git 이력·Docker socket·상대 workspace는 worker에 제공하지 않는다. 모델 인증 파일만 read-only로 연결한다. Apps/plugins 등 외부 연결 기능을 worker에서 비활성화한다.

실제 probe에서 자기 파일 읽기/쓰기와 상대 파일·호스트 GitHub 설정·Docker socket 접근 실패, 무인증 private repo 조회 404를 확인했다. 이것은 실제 실행 경계이며 prompt 제약으로 대신하지 않는다. 다만 모델 인증정보 반출·컨테이너 탈출을 막는 적대적 multi-tenant sandbox를 입증한 것은 아니다. 한 운영자가 준비한 비민감 synthetic 실험으로 한정한다.

### 6.2 GitHub publication 책임

Agent는 자기 workspace의 수정 파일·결과를 반환한다. 신뢰된 owner 실행 연결부가 배정된 subtree 변경만 선택해 Work별 branch·draft PR을 발행한다. Worker에 GitHub credential을 주지 않는다. Publisher는 subtree 밖 변경, path traversal, symlink, CI 기준 변경을 거부한다. 이는 GitHub의 path별 token 권한이 아니라 실행 연결부의 집행 책임이다.

한 repo는 최소 external setup이며 두 private repo를 필수로 하지 않는다. 대신 publisher가 신뢰 경계에 들어간다는 비용을 명시한다. 실제 서로 다른 사람의 repo-level GitHub authority를 검증하는 것은 이 MVP의 성공 주장이 아니다. Publisher의 실제 GitHub 쓰기와 경로 거부 검증은 제품 통합 전에 완료해야 하며 이번 probe의 성공으로 대체하지 않는다.

### 6.3 하나의 A2A 서버, 두 owner 경로

공식 Python A2A SDK 0.3.25 / protocol 0.3.0 조합을 검증 버전으로 고정한다. 단일 서비스의 `/backend/`, `/frontend/` 경로, 분리된 task store, coordinator 인증과 owner 범위 확인을 사용한다. Worker에는 이 coordinator credential을 제공하지 않는다. Streaming/push는 첫 loop에 필요하지 않으며 HTTP 요청·polling·구조화 artifact로 충분하다.

실제 세 task에서 `submitted → working → completed`와 CLI 종료를 관찰했다. 각각 B1/R1/E1~E3 상관 식별자를 반환했고 Backend resume는 새 task/새 프로세스였다. 잘못된 인증은 401, 다른 owner 경로의 task 조회는 error 응답 존재를 확인했다. Summary의 not found는 스크립트 요약이며 원시 HTTP body는 보존하지 않았다. 검증용 메모리 task store는 재시작 복구를 보장하지 않는다. 실제 실행 직전 현재 위임 확인·중복/취소/복구는 Work Engine의 수용 기준으로 구현해야 한다.

### 6.4 실제 검증과 남은 준비의 구분

완료: 컨테이너 접근 경계, 모델 호출·코드 변경·테스트, A2A HTTP 왕복·상관관계, Frontend JSON만 전달한 Backend 새 실행과 compatibility 검사. 잘못된 shared total은 검사 실패, 원본 복원 후 6개 검사 통과로 실제 입력 사용도 확인했다.

미완료: 실제 demo GitHub repo/Issue/branch/PR/check 발행, publisher 경로 집행, 전체 app 화면·HTTP 경로, Gateless의 실제 accept·큐·위임 변경·중복·취소·재시작 복구·UI. 이번 coordinator는 고정 순서의 disposable 검증 드라이버이며 Gateless 제품 코드가 아니다. 검증 주장과 한계의 source of truth는 아래 검증 기록과 prepared-probe README다.

재현 스크립트·출력·Agent 산출물은 [prepared-probe](../research/prepared-probe/README.md), 검증 해석은 [검증 기록 5~6절](../research/PREPARED_ENVIRONMENT_VERIFICATION.md)에 보존했다. 서버·probe 컨테이너는 종료했고 이미지·임시 작업 자료는 남겼다.

## 7. 보조 업무와 검증 범위

B2는 Backend의 잘못된 주문 ID 응답 테스트 정비, F1은 Frontend 주문 목록의 빈 상태 표시·테스트를 준비하는 후보로 둔다. B1/R1과 수정 파일이 충돌하지 않게 만들고 실제 수행한다. B1 대기 중 B2가 슬롯을 사용하는지, F1과 R1이 Frontend 큐에서 경쟁하는지 관찰한다. Sleep으로 실행 시간을 부풀려 경쟁을 연출하지 않는다.

주 회차는 B1의 cross-owner 한 번으로 한정한다. 별도 회차에서 서로 다른 original Work의 요청 동시 수락, 같은 original Work의 두 번째 active 요청 차단, 무관한 commit 증거, 공유 자료 부족, Issue close·위임 철회, 중복 결과·응답 유실을 확인한다. 장애 주입 검증은 실제 정상 실행과 구분해 보고한다.

## 8. 수용 기준과 비교 실험

- 실제 GitHub Issue에서 시작하고 각 Agent가 자기 workspace에서 코드·테스트를 변경한다.
- 실제 draft PR과 대상 SHA의 지정 검사를 증거로 연결한다. 실행 실패나 test 실패를 성공 데이터로 대체하지 않는다.
- 실제 A2A 요청·상태·artifact와 CLI 실행·종료가 이어지고 Gateless의 handoff를 우회하지 않는다.
- 결과·context·현재 위임이 충족되면 Backend가 매번 사람 승인 없이 READY 복귀 후 실제 후속 작업을 수행한다.
- 슬롯·fan-out·1-hop·취소·철회·중복·불명 실행 처리는 Work Engine 기준을 지킨다.
- UI는 실제 관찰·판정을 보여 주며 mock 상태를 정상 실행처럼 재생하지 않는다.

수동/자동 조건에서 같은 repo 기준 commit, Issue 목표, Agent, contract, fixture·검사 기준을 사용한다. 수동은 사람이 요청 전달·실행 시작·결과 확인·재개를 연결한다. 두 조건의 산출물 적합성을 동일 기준으로 평가한다.

조율 개입·능동 조율 시간과 Agent/CI 대기 시간을 구분하고, demo 준비·정책 설정·실패 수정·재작업 비용을 함께 보고한다. 회차별 branch/Issue/context를 구분하고 활성 실행이 남은 환경을 초기화하지 않는다. Synthetic 업무와 1인 2-owner 역할이 실제 팀 수요·장기 신뢰의 증거가 아니라는 한계를 공개한다.

## 9. Freeze 및 다음 단계

다음은 고정한다: 배송비 미정 계약 업무, original Work B1과 단일 R1, 공유 JSON/증거 계약, Node/Codex 이미지, 한 private repo의 owner별 snapshot, 한 호스트의 분리 실행 경계, 단일 A2A 서비스의 두 경로, Jira/n8n 제외.

Freeze는 feasibility로 실행 가능한 최소 구성을 선택했다는 뜻이다. 미구축된 GitHub publication·UI·제품 lifecycle을 완료했다고 선언하지 않는다. 다음은 Architecture/구현 계획에서 현재 위임 확인, 안전한 GitHub publisher, 실제 CI, 지속 상태·복구와 웹 연결을 구체화하는 것이다. Gateless 제품 구현은 아직 시작하지 않는다.

계정의 실제 repo 이름·credential 주체, GitHub PR/check 왕복, 허용 예산·관찰 시간·보존 기간·실험 반복 수는 남은 준비 값이다. 범위를 추가하는 이유로 사용하지 않으며 실제 제약이 고정 구성과 충돌하면 그 증거를 남기고 변경한다.
