# Prepared environment feasibility — 검증 기록

검증일·정리일: 2026-09-16. 이 문서는 **검증 목적·절차·관찰·결론·한계의 source of truth**다. [prepared-probe/README.md](prepared-probe/README.md)는 evidence 위치와 재현 절차의 source of truth다. [MVP Spec](../product/MVP_SPEC.md)은 이 결과로 선택한 제품 범위와 준비 구성을 표현한다. 요약이 서로 다르면 이 문서의 검증 한계를 우선한다.

이번 세션은 기존 검증의 독립 검토 자료를 정리하며 종료한다. 새 모델 실행이나 Docker 실험을 반복하지 않았고 Architecture·Gateless 제품 구현을 시작하지 않았다.

## 1. 결론의 정확한 범위

| 검증 | Evidence가 지지하는 결론 | 지지하지 않는 결론 |
|---|---|---|
| Permission boundary | 동일한 제한 컨테이너 구성에서 자기 workspace 읽기/쓰기와 지정된 상대·호스트 경로 접근 실패를 관찰했다. | 모든 경로·네트워크·credential 접근 차단, repo별 GitHub 권한 분리, 적대적 multi-tenant 보안 완성 |
| A2A 실제 왕복 | 공식 SDK의 loopback HTTP 요청·task polling·artifact가 실제 Codex 프로세스 3회와 연결되고 주어진 correlation ID가 반환됐다. | Gateless의 accept/인가/큐/취소/복구 엔진, 분산 배포, 서로 다른 owner identity의 인증을 검증함 |
| Shared context 기반 resume | 새 Backend 프로세스가 Frontend 사례를 전달받아 실제 compatibility 테스트를 작성·실행했다. 입력 변조에 검사가 반응했다. | Gateless가 결과를 판단해 자동 재개함, CLI 세션 복원, 실제 업무의 필수 dependency 발견·조율 비용 감소를 입증함 |

세 결론은 **한 번의 synthetic 시나리오에 대한 실행 가능성 관찰**이다. 동일한 호스트에서 세 Agent 실행을 순차 수행했다. 병렬 실행·슬롯 경쟁은 이번 probe에서 검증하지 않았다.

## 2. 실제 환경과 구성

| 항목 | 사용한 구성 |
|---|---|
| 호스트 | macOS arm64, 현재 사용자 UID/GID 501:20. 로컬 인증을 사용하는 한 운영자 |
| Docker | Desktop 4.21.1, Engine 24.0.2, Linux/arm64. 다른 기존 컨테이너는 변경하지 않음 |
| 이미지 | `node:24-bookworm-slim` + CA certificates + Codex 0.154.0. Tag: `gateless-feasibility:codex-0.154.0` |
| 실행 root | `/private/tmp/gateless-feasibility`, 하위 `backend/`, `frontend/`. Git repo clone이 아닌 직접 준비한 파일 디렉터리 |
| 컨테이너 | 비루트 501:20, read-only root, cap-drop ALL, no-new-privileges, 별도 tmpfs home/tmp, 자기 workspace 하나만 mount |
| 모델 인증 | 호스트 Codex auth 파일 하나를 read-only mount. 사용자 홈 전체·GitHub credential·Docker socket은 mount하지 않음 |
| CLI | `exec`, `--ignore-user-config`, `--ephemeral`, `--skip-git-repo-check`, JSONL. Apps/plugins/remote plugin/browser/computer 및 무한 연결 retry 비활성화 |
| CLI shell 권한 | `--sandbox danger-full-access`로 **컨테이너 내부** shell을 실행. 파일 격리 근거는 CLI sandbox가 아니라 외부 Docker 설정 |
| Python/HTTP | Python 3.12.11, a2a-sdk 0.3.25 / protocol 0.3.0, uvicorn 0.53.0, starlette 1.6.0, httpx 0.28.1 |
| A2A 구성 | 한 Python 프로세스의 `127.0.0.1:18765/backend/`, `/frontend/`. Owner별 InMemoryTaskStore, coordinator 공통 bearer token 하나 |
| 실행 수명 | Agent 호출마다 새 컨테이너·CLI 프로세스. Backend E1과 E3는 같은 Backend 파일 상태를 다시 mount. 한 운영자의 같은 모델 인증 사용 |

인증 token은 worker에 전달하지 않았지만 서버·coordinator는 같은 신뢰 프로세스다. 별도 서버·VM·GitHub 계정 두 개를 사용하지 않았다. 정확한 Docker/CLI 명령은 [당시 실행 스크립트](prepared-probe/raw/executed-probe.py)에 있다.

최초 컨테이너 probe는 CLI 홈 권한 오류, 다음 시도는 TLS 신뢰 오류로 실패했다. 비루트 writable ephemeral home과 CA 설치 후 본 검증이 성공했다. TLS 검증은 끄지 않았다. 실패 원인 진단은 당시 도구 출력에 근거하며, 전체 실패 로그·Docker build 출력은 repo에 보존하지 않았다.

## 3. Synthetic 입력과 실제 integration의 구분

| 요소 | 성격 |
|---|---|
| 배송비 paid/free/pending 업무, B1/R1/E1~E3 | 검증자가 지정한 synthetic 목표·식별자. 실제 GitHub Issue ID가 아님 |
| `api.mjs`, `view.mjs`, producer 기준 테스트 | 검증자가 seed로 제공. Producer의 pending 처리는 의도적으로 실패 |
| Backend/Frontend/Backend 순서와 프롬프트 | 드라이버에 고정. Agent가 dependency 필요성을 자유롭게 발견하거나 실행 순서를 결정한 실험이 아님 |
| Consumer 사례의 세 값·기대 표시 | 프롬프트에서 구체적으로 지정. Agent가 실제 파일·테스트를 작성했지만 새로운 외부 사실을 발견한 것은 아님 |
| Agent 코드 수정·테스트 | 실제 외부 모델 호출과 컨테이너 shell 실행. 사전 녹화 결과 재생이 아님 |
| A2A | 공식 SDK의 실제 로컬 HTTP JSON-RPC `message/send`, `tasks/get`, task/artifact 처리. 외부 호스팅 A2A 서비스가 아님 |
| GitHub | 이전 인증된 계정/비공개 repo 조회와 이번 컨테이너의 무인증 조회만 실제 수행 |
| GitHub Issue/branch/PR/check | 생성·발행·CI 실행하지 않음. 테스트 출력은 GitHub check가 아니라 컨테이너의 `node:test` 결과 |
| UI·HTTP demo app·Gateless | 미구현. `render`는 문자열 객체를 반환하는 함수로, 브라우저 UI 검증이 아님 |
| Jira/n8n | 사용하지 않음. 실제 기존 프로젝트가 없다는 사용자 확인 후 MVP에서 제외 |

## 4. 검증 A — 지정된 파일/credential 접근 경계

**목적·가설:** 단순 prompt 지시 없이 동일 호스트의 두 workspace를 실행 관점에서 분리할 수 있는가?

**실제 절차:**

1. Backend/Frontend 각각의 디렉터리에 owner marker를 만들었다.
2. 각 디렉터리 하나만 `/workspace`로 mount한 별도 컨테이너에서 Node 검사 프로그램을 순차 실행했다. 이는 Agent의 자발적 공격 시도가 아니라 **Agent 실행과 같은 Docker 설정의 결정적 검사**다.
3. 자기 marker를 읽고 자기 workspace에 파일을 썼다.
4. `probe.py`의 `BOUNDARY`에 나열된 상대 디렉터리·호스트 경로·GitHub 설정·Docker socket·`.git/config`를 읽고 `/other-owner.txt` 쓰기를 시도했다.
5. GH_TOKEN/GITHUB_TOKEN의 존재 여부를 확인하고, 인증된 이전 조회에서 private임을 확인한 repo에 무인증 GitHub API 요청을 보냈다.

**관찰:** 양쪽 모두 자기 읽기/쓰기 성공, 검사한 읽기 경로는 ENOENT, `/other-owner.txt` 쓰기는 EROFS, GH_TOKEN/GITHUB_TOKEN 없음, private repo 무인증 조회는 HTTP 404. [원시 검사 JSON](prepared-probe/boundary-results.json)

**해석·한계:** ENOENT는 해당 경로가 컨테이너에 없다는 관찰이며 모든 접근의 인가 거부를 뜻하지 않는다. EROFS는 검사한 root 위치의 쓰기 실패다. `/tmp`, ephemeral home 등 workspace 밖에도 허용된 쓰기 공간이 있으므로 ‘workspace 밖 모든 쓰기 차단’으로 표현하지 않는다. 실제 Git clone/export·publisher 경로 집행·symlink 탈출·컨테이너 탈출·동시 실행·host network 접근은 검증하지 않았다.

Model auth는 worker에 읽기 가능하고 outbound network는 열려 있다. 연결 기능 비활성화는 임의 shell의 네트워크 접근 차단을 뜻하지 않는다. 404만으로 repo 존재·비공개 여부를 입증하지 않으며, 존재 여부는 이전 인증 조회 관찰에 의존한다. 두 사람의 GitHub 권한이나 서로 다른 모델 계정을 분리했다는 결론은 내리지 않는다.

## 5. 검증 B — A2A와 실제 실행·correlation

**목적·가설:** 하나의 HTTP 서비스 안에서 owner 경로를 구분하고 A2A 요청을 실제 CLI 실행·상태·결과와 연결할 수 있는가?

**실제 절차:**

1. `/backend/`, `/frontend/` 각각에 SDK AgentCard·handler·task store를 연결했다.
2. 잘못된 bearer로 card를 조회해 401을 확인하고 올바른 bearer로 두 card의 이름을 확인했다.
3. Driver가 prepare/consumer/resume 메시지를 순서대로 전송했다. Application payload에 B1/R1/E1~E3을 넣었다.
4. Adapter는 task를 만들고 `working`을 알린 후 owner 컨테이너에서 Codex를 실행했다. CLI 종료 뒤 별도 컨테이너에서 지정 테스트를 다시 실행했다.
5. Adapter가 workspace 파일·exit code·주어진 correlation ID로 DataPart artifact를 구성했다. 모델 자체가 A2A server이거나 HTTP artifact를 직접 발행한 것은 아니다.
6. Driver가 `tasks/get`으로 상태를 polling하고 반환 artifact의 ID가 보낸 ID와 같은지 assert했다.
7. Frontend endpoint에 Backend task ID를 조회해 error 응답 존재를 assert했다.

**관찰:**

| 실행 | Task ID | 관찰 상태 | CLI/test exit |
|---|---|---|---|
| B1 prepare / E1 | `565c6e81-565a-423d-973e-2f16e87023d5` | submitted → working → completed | 0 / 0 |
| R1 consumer / E2 | `09fd93b6-3845-4455-96a2-989fe0b5c342` | submitted → working → completed | 0 / 0 |
| B1 resume / E3 | `be7ef94c-e9da-42f5-b4a3-8936339c8b86` | submitted → working → completed | 0 / 0 |

[관찰 요약](prepared-probe/summary.json), [CLI 원시 로그·재현 자료 안내](prepared-probe/README.md)

**해석·한계:** `working`은 adapter가 subprocess 생성 전에 보낸 상태이므로 그 시점에 CLI가 이미 시작됐다는 독립 증거는 아니다. 실제 실행 근거는 CLI 이벤트·파일 변경·테스트·프로세스 종료다. Correlation은 주어진 ID 전달·반환을 확인했으며 business identity 검증·중복 방지·지속성·위조 방지를 검증하지 않았다.

`summary.json`의 `cross_owner_task_lookup: "not found"`는 스크립트가 넣은 요약 문자열이다. 실제 assertion은 응답의 `error` 존재만 검사했다. 원시 응답 body를 저장하지 않았으므로 정확한 error code나 ‘not found’ 메시지를 독립 확인한 증거로 사용하지 않는다. 공통 bearer 하나와 분리된 task store는 두 owner identity의 별도 인증·인가를 입증하지 않는다.

원시 HTTP request/response 전체, packet capture, 전체 polling 기록은 보존하지 않았다. `summary.json`은 응답에서 추출한 상태 변화와 artifact 데이터 및 일부 assertion 결과다. `cancel`은 NotImplemented이며 retry·취소·철회·큐·슬롯·재시작 복구·Gateless contract enforcement는 검증하지 않았다.

## 6. 검증 C — 공유 JSON으로 새 Backend 실행 이어가기

**목적·가설:** Frontend 소스를 제공하지 않고 허용된 데이터 결과만 전달해, 새 Backend 프로세스가 실제 compatibility 테스트 입력으로 사용할 수 있는가?

**실제 절차:**

1. Seed producer 테스트를 실행해 pending의 실패를 확인했다.
2. Backend Agent가 API를 수정하고 producer 검사를 수행하며 `request.json`을 썼다.
3. Frontend Agent가 renderer·`cases.json`·그 파일을 읽는 consumer 테스트를 작성·실행했다.
4. Adapter가 Frontend 사례 파일을 읽어 JSON과 원본 bytes의 SHA-256을 artifact에 넣었다. Driver가 HTTP 응답의 이 artifact를 받아 Backend 요청으로 전달했고 Backend adapter가 `shared-context.json`에 썼다.
5. 새 Backend CLI가 기존 Backend 파일과 shared JSON을 읽어 compatibility 테스트·보고서를 작성했다. Frontend workspace는 mount하지 않았다.
6. 검증자가 `verify_shared.py`로 전달된 cases의 의미상 일치와 Frontend 원본 파일 digest 일치를 확인했다.
7. 전달 JSON 첫 사례의 total을 999로 바꾸고 compatibility 검사 실패를 확인했다. finally에서 원본을 복원하고 producer+compatibility 6개 통과를 확인했다.

**관찰:** Producer 3개, consumer 3개, 새 Backend의 producer 3 + compatibility 3개 통과. 잘못된 total에서는 실패, 원본 복원 후 6개 통과. 실제 Frontend digest는 `f6f080dbc5bd4f7793487f64c86fd7e7186443cb0943c341e2e339ce58c276e7`이다.

[Producer 전후](prepared-probe/producer-before.txt), [수정 후](prepared-probe/prepare-tests.txt), [consumer](prepared-probe/consumer-tests.txt), [resume](prepared-probe/resume-tests.txt), [변조 검사](prepared-probe/shared-negative-tests.txt), [복원 검사](prepared-probe/shared-restored-tests.txt), [공유 검증 요약](prepared-probe/shared-verification.json), [Backend 보고서](prepared-probe/observed/backend/compatibility-report.md)

**해석·한계:** 새 프로세스의 continuation과 실제 데이터 의존성은 확인했다. CLI session resume·대화 복원은 사용하지 않았다. Backend는 전달된 digest를 보고서에 인용했고 원본 Frontend 파일 대조는 양 workspace를 읽을 수 있는 신뢰된 검증자가 수행했다. 이 hash 일치는 서명·신뢰 출처·GitHub commit 증거 검증이 아니다. JSON은 재직렬화됐으므로 envelope 전체 bytes의 동일성을 주장하지 않는다.

‘Frontend 소스가 없다’는 직접 assertion은 `backend/view.mjs`의 부재이며, 다른 소스 경로 전체의 부재를 전수 검사한 것은 아니다. Mount 구성·원시 CLI 파일 조회·실제 산출물을 함께 근거로 삼는다. 테스트는 알려진 세 synthetic 사례를 사용하고 Agent가 consumer/compatibility 테스트를 작성했다. 임의 업무의 기술적 정답·독립 QA·조율 비용 감소를 검증하지 않았다. 정상 loop의 재개는 고정 driver가 시작했으며 Gateless의 자동 판정/재개 성공이라고 부르지 않는다.

## 7. Evidence 보존과 재현 수준

[README](prepared-probe/README.md)에 파일별 역할, 명령, 기대 결과, 재현 제약을 정리했다. 이번 문서 정리에서 임시 디렉터리의 세 CLI JSONL·stderr와 당시 실행 스크립트를 확인해 `prepared-probe/raw/`로 원본 bytes 그대로 보존했다. Credential 내용은 포함하지 않는다. [SHA-256 목록](prepared-probe/evidence-sha256.json)은 파일 일관성 확인용이며 서명·신뢰 출처 인증이 아니다.

기존 raw 위치는 `/private/tmp/gateless-feasibility/{prepare,consumer,resume}-cli.jsonl`와 같은 디렉터리의 stderr·스크립트다. 임시 디렉터리는 사라질 수 있으므로 repo의 보존본을 우선한다. 원시 HTTP 전체 응답, Docker inspect·네트워크 capture, 처음 인증된 GitHub 응답 전체는 보존하지 않았다. 해당 항목의 근거는 당시 도구 관찰과 요약이며 raw evidence처럼 표시하지 않는다.

재현은 새 workspace에서 실제 모델을 다시 호출한다. 출력·task ID·소요 시간은 달라질 수 있다. 같은 결과를 보장하는 deterministic replay가 아니다. README의 오프라인 검토는 저장된 결과만 검사하며 새로운 실행 성공을 뜻하지 않는다.

## 8. MVP Spec에 반영한 결정과 미검증 조건

| Freeze한 결정 | 근거와 조건 |
|---|---|
| 배송비 미정 synthetic 업무·공유 사례 계약 | 실제 producer/consumer 수정과 새 Backend compatibility 입력 사용 확인. 고객 수요 검증은 아님 |
| 한 호스트·공통 이미지·owner별 실행 경계 | 순차 컨테이너 실행에서 지정한 파일 접근 경계 확인. 두 VM/서버는 이 probe에 필요하지 않았음 |
| 한 A2A 서비스·두 경로·polling | 실제 HTTP task/artifact 왕복 성공. 상시 서비스·분산 인증·복구는 미검증 |
| Node 24/Codex 0.154.0, SDK 0.3.25/protocol 0.3.0 | 실제 검증한 조합으로 고정. 최신 버전·모든 환경 호환성 주장이 아님 |
| Private repo 하나의 owner별 Work snapshot | 파일 디렉터리 분리 성공에서 도출한 **구성 선택**. 실제 Git export·PR 발행을 검증한 사실은 아님 |
| GitHub credential 없는 worker + 신뢰된 publisher | Worker에 repo credential이 필요 없었던 관찰에 기반. Publisher의 subtree/path traversal/symlink/CI 변경 차단은 아직 미구현·미검증 |
| Jira/n8n 제외 | Synthetic GitHub/Agent/A2A 경로에 집중한다는 제품 결정. 이번 probe는 GitHub Issue/PR/check 왕복도 아직 수행하지 않음 |

Freeze는 **MVP 범위·준비 구성·결과 계약을 선택한 것**이다. Prepared environment 전체·보안·GitHub publication·제품 수용 검증의 완료 선언이 아니다. 특히 repo 하나가 두 repo보다 전체 구현·권한 집행 비용까지 작다는 비교 실험은 하지 않았다. 하나로 줄인 대신 신뢰된 publisher가 필요한 trade-off를 기록한다. 이 publisher가 집행 불가능하면 구성 결정을 다시 검토해야 한다.

현재 미검증: 실제 demo repo/Issue/branch/PR/check, publisher 경로 집행, 현재 위임 확인·철회, 동시 실행·슬롯/FIFO·중복/유실/취소/재시작 복구, 전체 app·웹 UI, 비용·효과 비교 실험. 원본 업무 상태와 GitHub check에 대한 수용 기준은 여전히 후속 검증 대상이다.

## 9. 세션 종료와 다음 독립 검토

Probe 실행 종료 시 임시 HTTP 서버와 probe 컨테이너가 종료된 것을 확인했다. 이번 문서 정리에서는 다시 실행하거나 현재 Docker 상태를 재검증하지 않았다. 이미지·임시 자료는 당시 남겼고 인증 파일은 이미지/repo로 복사하지 않았다.

다음 세션은 이 문서 → README의 오프라인 evidence 검토 → `summary.json`/raw CLI/테스트·fixture 대조 순서로 시작한다. ‘파일 경계’, ‘A2A 왕복’, ‘continuation’의 한계를 검토한 뒤 사용자와 다음 작업을 결정한다. Architecture·제품 구현으로 자동 진입하지 않는다.
