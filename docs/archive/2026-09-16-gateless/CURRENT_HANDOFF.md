# Gateless — Current Handoff

갱신일: 2026-09-16

## 현재 단계

이번 세션은 세 가지 prepared-environment feasibility의 독립 검토 자료를 정리하고 종료했다. Architecture·Gateless 제품 구현은 시작하지 않는다. 새로운 모델/Docker 실험도 반복하지 않았다.

확정된 Domain Model·Work Engine과 MVP 범위/구성 freeze는 유지한다. 다만 freeze는 전체 준비 환경·권한 집행·GitHub publication·제품 수용 완료를 뜻하지 않는다. Private repo 하나와 신뢰된 publisher는 구성 선택이며 실제 Git export/발행 경계는 아직 검증 전이다.

검증한 것은 지정 경로의 컨테이너 접근 제한, loopback A2A task/artifact와 실제 Codex 3회 실행의 연결, Frontend 사례 JSON을 받은 새 Backend의 compatibility 검사다. 정상 흐름은 고정 coordinator가 순차 실행했으며 실제 Gateless의 accept·큐·인가·자동 재개·복구를 검증하지 않았다.

원시 CLI JSONL·stderr·당시 실행 스크립트를 검토해 repo에 보존했다. 원시 HTTP 전체 응답은 당시 저장하지 않았다. 다음 세션에서 임시 디렉터리가 없어도 보존된 evidence를 검토할 수 있다.

## 현재 source of truth

1. [research/PREPARED_ENVIRONMENT_VERIFICATION.md](research/PREPARED_ENVIRONMENT_VERIFICATION.md): 세 검증의 목적·가설·환경·절차·관찰·결론·한계·MVP 결정. 실제 검증 주장에 대한 기준 문서.
2. [research/prepared-probe/README.md](research/prepared-probe/README.md): evidence 지도·오프라인 검토·실제 재현·실패 시 주의점. 실행 스크립트·원시 로그·테스트 출력·산출물과 연결한다.
3. [product/PRD.md](product/PRD.md), [domain/DOMAIN_MODEL.md](domain/DOMAIN_MODEL.md), [domain/WORK_ENGINE.md](domain/WORK_ENGINE.md), [product/MVP_SPEC.md](product/MVP_SPEC.md): 제품·도메인·runtime 기준과 선택한 MVP 범위. 검증 사실과 아직 구현할 요구를 혼동하지 않는다.
4. [research/INTEGRATION_FEASIBILITY.md](research/INTEGRATION_FEASIBILITY.md), [research/EXTERNAL_WORK_API_RESEARCH.md](research/EXTERNAL_WORK_API_RESEARCH.md): 이전 공식 API 조사. 현재 검증 결과는 1·2번을 우선한다.
5. [GATELESS_CONTEXT.md](GATELESS_CONTEXT.md): 과거 Discovery 이력. 현재 제품 합의나 실검증을 대체하지 않는다.

저장소 `docs/PRD.md`는 빈 템플릿이다. 이 인계 문서는 위 source of truth 전체를 대체하지 않는다.

## 최근 확정된 결정

- 최소 prepared 구성: private repo 하나, owner별 Work snapshot, 같은 호스트/이미지의 격리된 실행 컨테이너, A2A 서버 하나의 두 owner 경로. Agent에는 GitHub token·전체 clone/.git을 주지 않는다. 신뢰된 publisher가 owner subtree만 발행하는 경계를 집행해야 하며 이 발행 경로는 아직 실제 검증 전이다.
- Node 24/Codex 0.154.0, A2A SDK 0.3.25/protocol 0.3.0 조합으로 실제 왕복했다. 최신 버전이라는 주장이 아니다. Model auth 반출·적대적 컨테이너 탈출까지 검증한 multi-tenant sandbox가 아니라 비민감 synthetic 실험 범위다.

- Active cross-owner Request는 original Work당 하나로 제한한다. 1-hop과 별개의 fan-out 제한이며 서로 다른 original Work는 owner 슬롯에 따라 독립 진행한다.
- BLOCKED는 실제 첫 실행 전 조건 부족, WAITING은 적어도 한 번 실행된 Work의 continuation 조건 대기다. 원인 종류는 별도 사유로 남긴다.
- Resume는 새 READY 순번을 받는다. 실행 불명 시 슬롯을 유지한다. 실제 시작 전 owner 연결부도 현재 허가를 확인하며 실행 실패·재작업의 자동 retry는 MVP에서 제외한다.

- Request는 handoff 관계·조건을 표현하며 독립 실행 상태 머신을 갖지 않는다. Work는 실행 조율 lifecycle, Execution은 실제 시도, 결과 판정은 Request 조건과 Result/Evidence의 관계에서 관리한다.
- Contract 변경·철회는 과거 사실을 다시 쓰지 않는다. 향후 행동에는 그 행동과 관련된 현재 위임 경계를 적용하며 Accepted를 영구 실행권으로 보지 않는다.

- **Owner defines the boundary → Agent proposes the work/result → Gateless enforces the contract → Agent performs the technical judgment.** Owner가 자동 위임 가능한 요청·행동, 최소 결과·evidence, 공유 context 범위를 정한다. Agent 제안만으로 자동 재개 기준을 약화하지 않는다. Gateless는 사전 contract에 대한 요청·결과 적합성과 재개 조건을 확인하며 기술적 정답을 다시 판단하는 super-agent가 되지 않는다. 조건 충족 시 매번 사람의 재개 승인을 요구하지 않는다.
- Execution Delegation, Scheduling Delegation, Result Condition, Context Sharing을 “반복적인 handoff에서 사람이 빠져도 되는 사전 경계”의 의미적 집합인 Coordination Contract로 묶는 방향을 채택했다. 하나의 permission boolean으로 축약하지 않는다.
- GitHub Issue를 external Source Work로 사용한다. 새 demo의 Backend/Frontend 책임·workspace를 실제 분리하고 실제 코드 작업과 A2A 왕복을 수행한다. Jira·n8n은 이번 MVP에서 제외한다. Synthetic 업무를 기존 고객 사례라고 주장하지 않는다.
- 외부 업무 상태와 Gateless coordination state를 분리한다. 상태 소유권은 integration별로 정한다.
- MVP에서도 구조화된 coordination event history를 보존한다. 반복 handoff·human intervention 분석과 automation opportunity 발견·제안은 폐기하지 않고 **post-MVP**로 남긴다. 현재 패턴 분석이나 process mining을 구현하지 않는다.
- 임시 A2A endpoint 두 경로의 왕복은 검증했고 서버는 당시 종료했다. 상시 배포·현재 위임 집행은 아직 미구축이다. GitHub가 Issue와 PR/check 출처를 동시에 맡아도 상태·식별자·판정을 혼합하지 않는다.

## 남은 준비·구현 검증

- 실제 private demo repo/Issue 생성과 owner별 subtree의 branch/PR/check 발행.
- Publisher의 경로 이탈·symlink·CI 기준 변경 거부와 필요한 GitHub 권한/merge 제한의 실제 집행.
- Work Engine의 현재 위임 확인·큐·취소·중복·재시작 복구, 웹 연결과 통합 loop.
- 예산·관찰 시간·보존 기간·신뢰 긴급성 출처·실험 반복 수.

## 다음 세션에서 가장 먼저 할 작업

1. 검증 기록 1절의 세 결론과 한계, 4~6절의 절차·관찰을 읽는다.
2. README의 오프라인 검사로 해시·summary·fixture 정합성을 확인하고 실제 CLI 명령·산출물·전후/변조 테스트를 대조한다.
3. 한 repo + publisher 선택의 미검증 조건과 세 검증의 한계를 사용자와 검토한다. 다음 작업은 그 뒤 결정한다. Architecture/제품 구현으로 자동 진입하지 않는다.

표현 주의:

- ‘Permission boundary’는 **검사한 경로**의 접근 제한이다. `/tmp`와 model auth 접근은 허용됐으며 GitHub repo별 권한·적대적 보안은 미검증이다.
- ‘A2A 왕복’은 한 호스트의 실제 HTTP/SDK/CLI 연결이다. 공통 bearer 하나, owner별 task store이며 서로 다른 identity의 인증을 입증하지 않는다. `summary.json`의 `not found`는 error 존재 assertion 뒤 넣은 문자열이고 원시 HTTP body는 없다.
- ‘Resume’는 고정 driver가 새 CLI를 시작해 shared JSON을 테스트에 쓰게 한 것이다. Gateless 자동 판정/재개나 CLI 세션 복원은 아니다. Fixture의 세 사례는 프롬프트에 미리 지정됐다.

재현이 필요한 근거가 생기면 README에 따라 새 임시 디렉터리에서 수행한다. 원시 CLI 로그는 이제 repo의 `prepared-probe/raw/`에 있으며 credential은 복사하지 않았다. 당시 Docker 이미지·임시 자료는 남겼지만 이번 정리에서 현재 실행 상태를 다시 확인한 것은 아니다.

문서 순서는 `PRD → DOMAIN_MODEL → WORK_ENGINE → MVP_SPEC → ARCHITECTURE → 필요한 ADR → PLAN`을 유지하되, 이번 마무리는 evidence 정리와 검토 handoff에 한정한다.

## 근거 없이 다시 열지 말아야 할 결정

- 첫 persona는 multi-owner 개발팀이다. 개인·비개발자를 같은 첫 제품으로 억지로 합치지 않는다.
- Provider permission, execution delegation, scheduling delegation을 구분한다. 권한을 요청자에게 옮기지 않고 권한 보유자에게 일을 전달한다.
- owner별 제공 슬롯 하나, 확인된 긴급 우선·일반 READY FIFO, non-preemption, 불확실하면 escalation. Dependency는 순위 점수가 아니라 실행 조건이다.
- 기존 Work에서 명시적으로 파생된 cross-owner 요청 한 단계까지만 자동 허용한다. Gateless의 원시 정보 기반 Work discovery와 연쇄 위임은 제외한다.
- 명시된 수정안·테스트 결과·draft PR로 요청 충족을 검증한다. 실행 성공·결과 충족·원래 Work 재개를 구분하며 실제 merge 의존성은 대표 시나리오에서 제외한다.
- 외부 결과 대기 시 슬롯을 반환하고, 결과 충족 후 READY로 복귀한다. 동일 Agent 프로세스 유지나 즉시 재실행은 요구하지 않는다.
- 성공은 동일 시나리오에서 결과 적합성을 유지하며 수동 대비 조율 개입·시간이 줄어드는지로 측정한다. 설정·정책 유지·재작업 비용도 공개하며, 임의 감소율이나 조직 전체 수요 입증을 주장하지 않는다.

기존 반론을 설명 없이 반복하지 말되, 새로운 근거로 변경이 필요하면 어떤 가정이 깨졌는지 명시한다. 저장소에 기존 미커밋 변경이 다수 있으므로 관련 없는 변경을 정리하거나 되돌리지 않는다.
