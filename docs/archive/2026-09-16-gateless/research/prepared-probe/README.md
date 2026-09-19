# Prepared probe — evidence와 재현 안내

이 문서는 **자료 위치·독립 검토·재현 방법의 source of truth**다. 검증 목적·환경·관찰·결론의 한계는 [PREPARED_ENVIRONMENT_VERIFICATION.md](../PREPARED_ENVIRONMENT_VERIFICATION.md)를 따른다. 제품 코드가 아니라 한 번의 synthetic feasibility 실행 자료다. 지금 단계에서 Architecture·Gateless 구현으로 진행하지 않는다.

## 1. 먼저 확인할 결과

- 파일 경계: 같은 Docker 구성에서 자기 workspace는 사용 가능하고 **검사한** 상대/호스트 경로는 읽을 수 없었다. 모든 외부 접근을 차단했다는 뜻은 아니다.
- A2A: 실제 loopback HTTP → SDK task → 실제 Codex → artifact/polling 세 회차가 연결됐다. Coordinator는 고정 순서 driver다.
- Continuation: Frontend의 실제 JSON 파일 내용이 A2A artifact를 거쳐 새 Backend 실행의 테스트 입력이 됐다. 잘못된 total에서는 실패했다. Gateless 자동 재개 정책·CLI 세션 복원을 검증하지 않았다.

## 2. Evidence 지도

아래 파일은 이번 문서 정리 전 수행한 **한 성공 회차**의 기록이다. 복수 실행의 통계나 성능 benchmark가 아니다.

| 자료 | 생성 주체·역할 | 읽을 때 주의할 점 |
|---|---|---|
| [seed.py](seed.py) | 초기 함수·producer 테스트·owner marker 생성 | Synthetic 입력이며 실제 외부 repo checkout이 아님 |
| [raw/executed-probe.py](raw/executed-probe.py) | 당시 실제 실행한 driver/adapter 원본 | 호스트 auth 경로·UID 501:20 고정. 그대로 재실행하기보다 재현본 사용 |
| [probe.py](probe.py) | 재현용 driver/adapter | 원본과의 차이는 auth 경로 환경 변수/홈 기본값 및 실행 사용자 UID/GID 적용 두 부분. 이번 정리에서 실행 로직은 수정하지 않음 |
| [Dockerfile](Dockerfile), [requirements.txt](requirements.txt) | 이미지·SDK/서버 의존성 | Docker base tag와 apt/전이 의존성·모델은 완전 고정하지 않았음 |
| [boundary-results.json](boundary-results.json) | 결정적 Node 검사 stdout을 파싱해 보존 | ENOENT/EROFS/404와 확인한 경로 목록. Agent의 적대적 탈출 시도 아님 |
| [summary.json](summary.json) | Driver가 HTTP 응답·exit code에서 구성한 요약 | 원시 HTTP dump가 아님. `not found`는 error 존재 검사 뒤 넣은 고정 요약 |
| [raw/prepare-cli.jsonl](raw/prepare-cli.jsonl) | Backend CLI 원시 이벤트 | 파일 조회·변경·실제 producer test 명령과 출력 |
| [raw/consumer-cli.jsonl](raw/consumer-cli.jsonl) | Frontend CLI 원시 이벤트 | renderer·fixture·consumer 테스트 작성과 명령 출력 |
| [raw/resume-cli.jsonl](raw/resume-cli.jsonl) | 새 Backend CLI 원시 이벤트 | shared JSON 조회·compatibility 테스트·보고서 작성 |
| [raw/prepare-stderr.txt](raw/prepare-stderr.txt), [raw/consumer-stderr.txt](raw/consumer-stderr.txt), [raw/resume-stderr.txt](raw/resume-stderr.txt) | 세 CLI의 stderr 원본 | 성공 회차의 출력이며 초기 실패 회차 stderr가 아님 |
| [producer-before.txt](producer-before.txt) | Driver가 Agent 실행 전 수행한 기준 검사 | Pending 테스트의 의도된 실패 |
| [prepare-tests.txt](prepare-tests.txt), [consumer-tests.txt](consumer-tests.txt), [resume-tests.txt](resume-tests.txt) | Driver가 Agent 종료 후 별도 컨테이너에서 재실행한 검사 출력 | CLI 내부 테스트 출력과 별도다. 각각 3/3/6개 통과 |
| [verify_shared.py](verify_shared.py) | 별도로 수행한 내용·digest·변조 감도 검사 | 새 모델 호출 없이 입력 변조·검사·복원 |
| [shared-verification.json](shared-verification.json) | 위 검사의 assertion 결과 요약 | Frontend 소스 부재 직접 검사는 Backend의 `view.mjs` 하나에 한정 |
| [shared-negative-tests.txt](shared-negative-tests.txt), [shared-restored-tests.txt](shared-restored-tests.txt) | Total 변조/복원 후 실제 테스트 출력 | 실패는 정상적인 감도 검사 결과다. 복원 후 6개 통과 |
| [evidence-sha256.json](evidence-sha256.json) | 정리 시 생성한 파일 해시 목록 | 파일 일관성 확인용. 외부 서명·출처 인증 아님 |

최종 Agent 산출물:

- Backend: [api.mjs](observed/backend/api.mjs), [producer.test.mjs](observed/backend/producer.test.mjs), [request.json](observed/backend/request.json), [shared-context.json](observed/backend/shared-context.json), [compatibility.test.mjs](observed/backend/compatibility.test.mjs), [compatibility-report.md](observed/backend/compatibility-report.md).
- Frontend: [contract.json](observed/frontend/contract.json), [view.mjs](observed/frontend/view.mjs), [cases.json](observed/frontend/cases.json), [consumer.test.mjs](observed/frontend/consumer.test.mjs), [result.json](observed/frontend/result.json).

`contract.json`과 `shared-context.json`은 adapter가 쓴 전달 입력이다. `producer.test.mjs`는 검증자가 만든 seed다. 나머지 코드·사례·요청·보고서는 CLI가 실제 작성했다. A2A artifact는 adapter가 이 파일과 실행 결과로 구성했으며 `result.json`을 그대로 전송하는 방식은 아니었다.

## 3. Raw 보존 수준

이번 정리에서 임시 디렉터리의 CLI 로그·stderr·실행 스크립트 내용을 확인하고 `raw/`에 bytes 그대로 복사했다. Credential 값은 포함하지 않는다. 이전 원본 위치는 `/private/tmp/gateless-feasibility/`이며 사라질 수 있으므로 repo 보존본을 우선한다.

저장하지 않은 자료: 원시 HTTP request/response 전체·packet capture·전체 polling 목록, Docker inspect/build 원시 출력, 이전 인증된 GitHub 조회 전체 응답, 초기 실패 probe의 전체 로그. 없는 raw 자료를 `summary.json`으로 대체했다고 주장하지 않는다. 모델 이름/버전의 별도 pin·완전한 dependency lock도 기록하지 않았다.

## 4. 외부 실행 없이 독립 검토

저장소 루트에서 아래 명령으로 보존본의 일관성과 주요 관찰을 검사할 수 있다. 모델·Docker·GitHub를 호출하지 않으며 과거 결과의 정합성 검사다.

```sh
python3 - <<'PY'
from pathlib import Path
import hashlib, json
p = Path('docs/research/prepared-probe')
for name, expected in json.loads((p / 'evidence-sha256.json').read_text()).items():
    assert hashlib.sha256((p / name).read_bytes()).hexdigest() == expected, name
summary = json.loads((p / 'summary.json').read_text())
assert len(summary['runs']) == 3
assert len({r['task_id'] for r in summary['observations']}) == 3
assert all(r['cli_exit'] == 0 and r['test_exit'] == 0 for r in summary['runs'])
for r in summary['observations']:
    assert r['states'] == ['submitted', 'working', 'completed']
for r in summary['boundaries'].values():
    assert all(v == 'ENOENT' for v in r['reads'].values())
    assert r['crossWrite'] == 'EROFS'
    assert r['githubPrivateWithoutCredential'] == 404
    assert r['githubCredentialPresent'] is False
raw = (p / 'observed/frontend/cases.json').read_bytes()
shared = json.loads((p / 'observed/backend/shared-context.json').read_text())
assert shared['cases'] == json.loads(raw)
assert shared['sha256'] == hashlib.sha256(raw).hexdigest()
v = json.loads((p / 'shared-verification.json').read_text())
for key in ['fixture_digest_matches', 'cases_match_frontend_bytes',
            'backend_does_not_contain_frontend_source', 'tampered_total_rejected']:
    assert v[key] is True
assert v['restored_tests_exit'] == 0
print('Stored evidence consistency: PASS')
PY
```

이어 raw CLI의 실제 command/file change와 `observed/` 산출물, 전후·변조 테스트 출력을 읽는다. `summary.json`만 읽고 보안·제품 lifecycle을 통과했다고 판단하지 않는다.

## 5. 실제 재현 전제

- 당시 환경은 macOS arm64 + Docker Desktop Linux/arm64 + 비루트 UID/GID 501:20이었다. 다른 OS에서 동일 결과는 아직 검증하지 않았다.
- Docker 사용 권한, Python 3.12/uv, 패키지 다운로드와 모델·GitHub 네트워크, 컨테이너에 읽기 가능한 Codex 인증 파일이 필요하다. 실행은 실제 모델 사용량과 Docker 리소스를 소비한다.
- `GATELESS_PROBE_AUTH` 미지정 시 현재 사용자 `.codex/auth.json`을 read-only mount한다. GitHub credential이나 전체 사용자 홈을 대신 mount하지 않는다. CLI shell은 컨테이너 안에서 danger-full-access로 실행되며 격리는 Docker 구성에 의존한다.
- 재현본의 UID는 현재 사용자에서 얻으므로 **root로 실행하면 당시 비루트 조건을 재현하지 못한다.** UID 0으로 실행하지 않는다.
- `probe.py`의 `BOUNDARY`는 당시 호스트 경로와 `jeongeundev/harness-framework`를 포함한다. 다른 환경에서는 실제 존재하는 자기 marker/상대 marker/호스트 파일과, 자신이 인증 조회로 private임을 확인한 repo로 검사 대상을 바꾸고 변경 내용을 기록한다. 존재하지 않는 임의 경로의 ENOENT/404를 접근 차단 증거로 과장하지 않는다.
- Loopback 18765와 `gateless-probe-prepare/consumer/resume` 이름이 비어 있어야 한다. 사용 중인 기존 프로세스를 임의 종료하지 않는다.

## 6. 새 임시 디렉터리에서 재현

저장소 루트 기준으로 다음과 같이 복사한다. 완료된 `observed/`를 seed로 복사하지 않는다.

```sh
probe_dir=$(mktemp -d /private/tmp/gateless-replay.XXXXXX)
cp docs/research/prepared-probe/{probe.py,seed.py,verify_shared.py,Dockerfile,requirements.txt} "$probe_dir/"
cd "$probe_dir"
python3 seed.py
uv venv --python 3.12 venv
uv pip install --python venv/bin/python -r requirements.txt
docker build -t gateless-feasibility:codex-0.154.0 .
venv/bin/python probe.py
venv/bin/python verify_shared.py
```

필요한 검사 경로 수정은 `probe.py` 실행 전에 한다. `seed.py`는 기존 owner 디렉터리가 있으면 거부한다. 실패한 회차를 원본 evidence 위에서 덮어쓰지 말고 새 임시 디렉터리를 사용한다.

기대 출력은 `BOUNDARY PASS both owners`, 세 phase의 `A2A SENT`/`A2A COMPLETED`, `PROBE PASS`, 그리고 shared 내용·digest 일치/변조 실패/복원 성공이다. 초기 producer pending은 실패해야 하고 최종 producer/consumer/resume는 3/3/6개 통과해야 한다. Task ID·모델 문장·코드 모양·소요 시간·polling 중간 상태의 포착은 달라질 수 있다.

Node base image는 mutable tag, apt/전이 의존성은 완전 lock되지 않았고 모델은 CLI 기본 선택이다. 따라서 bit-for-bit 재현이나 매회 성공을 보장하지 않는다. 현재 이미지/패키지 조합이 달라졌다면 그 차이도 실행 기록에 남긴다.

## 7. 실패·종료와 검증 한계

정상 완료 시 서버 종료, `--rm` 컨테이너 종료를 기대한다. 중단·timeout·예외 경로의 강제 정리까지 검증한 runner는 아니다. 오류가 나면 자동 성공으로 처리하지 말고 자기 probe 컨테이너·18765 listener가 남았는지 확인한다. 모델 작업이 남았다면 증거를 보존하고 해당 검증 실행만 정리한다. 재시도·cancel 자체의 제품 동작을 시험한 것은 아니다.

SDK import 오류는 `a2a-sdk[http-server]` extra 설치 여부, CLI permission 오류는 ephemeral home의 소유권, TLS 오류는 CA 설치·신뢰 구성을 확인한다. 문제를 피하려고 root/privileged·host 전체 mount·TLS 비활성화로 재현 조건을 바꾸지 않는다.

MVP freeze의 의미와 아직 검증하지 못한 GitHub publisher·현재 위임·동시성·복구·UI·효과 측정은 상위 검증 기록 8절을 따른다. 이 자료를 검토하는 것만으로 Architecture나 제품 구현을 시작하지 않는다.
