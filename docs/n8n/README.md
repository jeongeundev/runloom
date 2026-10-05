# n8n 예시 — Runloom handoff

갱신일: 2026-10-02. 결정은 [ADR-0010](../adr/0010-n8n-inbox-and-callback.md), 계약 예시는 [CONTRACT](../CONTRACT.md) 12절, 설계는 [ARCHITECTURE](../ARCHITECTURE.md) "n8n 입구와 출구" 절. 이 디렉터리의 `runloom-handoff.json` 은 n8n 에 그대로 import 하는 예시 워크플로우 하나이며, 제품은 워크플로우 JSON 을 만들지 않는다.

이 절차는 **로컬(운영자 컴퓨터)** 에서 따라 한다. 공개 데모 VM(`runloom.duckdns.org`)은 callback 허용 목록이 비어 있어 callback 이 오지 않는다 — 심사 기간 동결 상태이며 이 절차의 대상이 아니다.

## 1. 무엇을 하는가

n8n 이 Runloom 에 코드 수정과 검토 두 항목을 넣는다. 등록된 로컬 에이전트가 수정 결과를 만들고 검토 단계에 넘긴다. 체인이 **사람 차례**가 되면 워커가 callback 을 보내 Wait 노드를 깨운다. 실제 모델과 저장소를 사용하므로 구독 사용량을 소비하고 결과 브랜치를 만들 수 있다.

n8n 쪽 노드는 4개다:

| 노드 | 하는 일 | Runloom 쪽 대응 |
|---|---|---|
| Webhook (`POST /webhook/runloom-demo`) | 테스트용 트리거. 본문 `{"run_id": "daily-0920-0900", "repository_id": "billing"}` 을 받고 즉시 응답한다 | — (실제 운영에서는 Error Trigger 나 Schedule 로 바꾼다) |
| HTTP Request (`POST …/sources/n8n/chains`) | 항목 2개(수정·검토)와 `callback_url: $execution.resumeUrl` 을 `InboundChainRequest` 로 보낸다. 인증은 Header Auth 자격 증명(입구 토큰) | 입구 API — 라벨 규칙으로 체인 + Task 2개를 만들고 첫 업무를 즉시 시작한다. 응답 `InboundChainResponse` 의 `chain_url` 이 체인 화면 |
| Wait (On Webhook Call) | `$execution.resumeUrl` 로 POST 가 올 때까지 잠든다 | 워커가 체인이 사람 차례(`chain_settled`)가 된 tick 의 마지막에 `ChainCallback` 을 체인당 1회 POST 한다 |
| Slack (비활성) | callback 본문의 `title`·`human_gate`·`chain_url` 로 메시지를 만든다. 자격 증명을 넣기 전엔 비활성이라 데이터를 그대로 통과시킨다 | — |

라벨은 `kind:bug_fix` 또는 `kind:code_review` 와 `repository_id:<등록 ID>` 를 쓴다. Webhook 본문의 `repository_id` 는 러너 등록 ID 와 같아야 한다. 검토의 `blocked_by` 는 수정 항목의 키다. 별도 `kind`·`scope` 필드는 없다.

## 2. 준비

### n8n — Docker

```bash
docker run -d --name runloom-n8n -p 5678:5678 -v runloom_n8n_data:/home/node/.n8n docker.n8n.io/n8nio/n8n
```

화면으로 import 하려면(4절 "화면으로") 브라우저에서 `http://localhost:5678` 을 열고 첫 실행이면 소유자 계정을 만든다 — CLI 로 넣는 경로(4절 "CLI 로")는 소유자 계정 없이 된다. 컨테이너 안에서 호스트(Runloom)는 `host.docker.internal` 로 보인다 — Docker Desktop(Mac/Windows) 이름이며 Linux 는 7절.

### Runloom — 셀프호스트

[SELFHOST](../SELFHOST.md)의 설치·로그인·러너 등록 절차를 따른다. `deploy/selfhost/.env` 에 설정을 저장한 뒤 재설치한다:

```bash
# deploy/selfhost/.env 에 저장할 두 줄
WORKFLOW_CALLBACK_HOSTS=localhost:5678
WORKFLOW_PUBLIC_URL=http://127.0.0.1:8000
# 설정 저장 뒤 실행
deploy/selfhost/install.sh
```

Docker 워커에서 호스트의 n8n 에 접속하려면 n8n 의 `WEBHOOK_URL` 을 `http://host.docker.internal:5678/` 로 설정하고 허용 목록도 `host.docker.internal:5678` 로 맞춘다. `localhost` 는 각 컨테이너 자신이므로 Docker 워커에서는 호스트 n8n 주소로 쓸 수 없다. 개발 서버를 호스트에서 직접 실행하는 경우에는 위 `localhost:5678` 값을 쓴다. `WORKFLOW_PUBLIC_URL` 은 사람이 여는 링크 주소다.

HTTP Request 기본 URL 은 `http://host.docker.internal:8000/sources/n8n/chains` 다. 중앙 포트를 바꿨으면 노드 URL 도 바꾼다.

## 3. Runloom 에서

1. 관리자 로그인 뒤 `/team` 에서 러너가 켜져 있는지 확인한다. 대상 저장소에 `code.fix`·`code.review` 능력이 필요하다. 에이전트가 없으면 입구 API 는 422 `agent_not_registered` 로 거부한다.
2. `/settings?tab=inbound`(설정 → n8n 입구) 에서 입구 토큰을 발급한다. 한 번만 보이는 `wfs_…` 원문을 복사하고 callback 허용 목록을 확인한다.
3. 러너에 등록한 `repository_id` 를 확인한다. 실행 예시의 `billing` 을 자신의 ID 로 바꾼다.

## 4. n8n 에서

### 화면으로

1. **Workflows → Import from File** 로 `docs/n8n/runloom-handoff.json` 을 올린다.
2. **Credentials → Create credential → Header Auth**: Name `Authorization`, Value `Bearer wfs_…`(3절에서 복사한 원문). 자격 증명 이름은 `Runloom source token`. 값은 n8n 의 자격 증명 저장소에만 있고 워크플로우 JSON 에는 참조(`credentials.httpHeaderAuth`)만 있다.
3. **HTTP Request** 노드를 열어 Credential 에서 위 자격 증명을 고르고, URL 의 포트를 중앙 포트에 맞춘다.
4. 저장 후 활성화(**Publish** / 구버전은 우측 상단 **Active** 토글). 활성화해야 `POST /webhook/runloom-demo` 가 production URL 로 열린다.

### CLI 로 (대안)

```bash
cp docs/n8n/runloom-handoff.json runloom-handoff.json   # 중앙 포트가 다르면 사본 URL 을 고친다
docker cp runloom-handoff.json runloom-n8n:/tmp/runloom-handoff.json
docker exec -u root runloom-n8n chown node:node /tmp/runloom-handoff.json   # docker cp 는 호스트 사용자 소유·권한 그대로 옮긴다 — node 가 읽으려면 필요
docker exec -u node runloom-n8n n8n import:workflow --input=/tmp/runloom-handoff.json
```

`import:workflow` 는 JSON 최상위의 `id` 를 요구한다(2.39.10 에서 없으면 `NOT NULL constraint failed: workflow_entity.id` 로 거부). 예시 파일의 `"id": "runloomHandoff001"` 이 그 값이며 아래 `list:workflow`·`publish:workflow` 에 그대로 쓴다.

자격 증명은 decrypted 형식 파일로 넣는다. `id` 는 JSON 의 참조(`runloom-source-token`)와 같게 둔다:

```json
[
  {
    "id": "runloom-source-token",
    "name": "Runloom source token",
    "type": "httpHeaderAuth",
    "data": { "name": "Authorization", "value": "Bearer wfs_…" }
  }
]
```

```bash
docker cp runloom-credentials.json runloom-n8n:/tmp/runloom-credentials.json
docker exec -u root runloom-n8n chown node:node /tmp/runloom-credentials.json   # 위와 같은 이유 (0600 파일은 이 줄이 없으면 EACCES)
docker exec -u node runloom-n8n n8n import:credentials --input=/tmp/runloom-credentials.json
docker exec -u node runloom-n8n rm /tmp/runloom-credentials.json && rm runloom-credentials.json   # 원문이 든 파일은 지운다
docker exec -u node runloom-n8n n8n list:workflow                    # "runloomHandoff001|Runloom handoff"
docker exec -u node runloom-n8n n8n publish:workflow --id=runloomHandoff001   # 구버전: n8n update:workflow --id=<id> --active=true
docker restart runloom-n8n                                           # CLI 로 바꾼 활성 상태는 재시작 뒤 반영된다 — 로그에 `Activated workflow "Runloom handoff"`
```

노드 파라미터 이름·`typeVersion` 은 n8n 버전에 따라 다를 수 있다. 예시 파일의 값은 2026-09-22 n8n 저장소(master)에서 확인한 범위 안이다 — Webhook `2`(1~2.1), HTTP Request `4.2`(1~4.5), Wait `1.1`(1~1.1), Slack `2.2`(1~2.7). import 가 거부되면 화면에서 같은 노드 4개를 손으로 만들어도 된다(파라미터는 1절 표와 JSON 참조). 2026-09-22 n8n **2.39.10**(Docker) 에서 이전 진단 데모 JSON 을 CLI 로 import·publish 하고 Webhook → HTTP Request 201 → Wait → callback → Slack 통과를 1회 확인했다 — [VERIFICATION_LOG](../VERIFICATION_LOG.md) "실제 n8n" 절.

## 5. 실행

```bash
curl -X POST http://localhost:5678/webhook/runloom-demo -H 'content-type: application/json' -d '{"run_id":"daily-0920-0900","repository_id":"billing"}'
```

1. HTTP Request 응답의 `started`·`start_error`·`tasks` 를 확인한다. `tasks` 가 비었으면 종류·저장소 라벨을 확인한다.
2. `chain_url` 에서 수정·검토 진행을 확인한다. `started: false` 면 시작 오류를 해결하고 체인 화면에서 시작한다.
3. 사람 차례에 callback 이 Wait 를 깨운다. Slack 은 비활성이라 메시지를 보내지 않는다. callback 의 `human_gate`·`tasks` 를 확인한다.
4. Runloom 에서 결과를 처리한다. callback 은 다시 보내지 않는다.

2026-09-22 실제 n8n 검증은 이전 진단 데모 예시에 대한 기록이다. 현재 수정·검토 예시는 파일 계약 검증만 했으며 실제 n8n 실행은 별도 확인 대상이다.

## 6. 동작 규칙

- **callback 은 체인당 1회, 사람 차례에.** 워커는 매 tick 의 마지막에 체인의 업무 상태로 `chain_settled` 를 판정한다 — 실행 요청됨·실행 중인 업무가 없고, `대기` 인 업무는 모두 선행의 `확인 필요`·`실패` 에 막혀 있으면 사람 차례다. 그 순간 `ChainCallback` 을 `callback_url` 로 POST 하고 `callback_sent_at` 을 남긴다. 사람이 그 뒤 승인·수정 요청·종료해도 다시 보내지 않는다(n8n Wait 노드는 한 번만 깨어난다). 마지막 업무의 `완료` 를 기다리지 않는 이유: 그러면 알림이 사람 승인 뒤에 와서 쓸모가 없다.
- **전송 실패는 30·60·120·240초 뒤 재시도, 5회 실패 후 중단.** 체인 화면의 callback 줄에 `대기 · 재시도 n회` 또는 `실패 n회 · <사유>` 로 보인다. n8n 이 꺼져 있었으면 다시 켜고 그 창 안에 있으면 된다.
- **허용 목록.** `callback_url` 의 host 가 `WORKFLOW_CALLBACK_HOSTS` 안이어야 접수된다(밖이면 422 `callback_host_not_allowed`, 목록이 비어 있어도 422). `host` 만 쓰면 그 host 의 모든 포트, `host:port` 는 그 포트만. 워커도 보내기 직전에 한 번 더 검사한다. Docker 워커는 `host.docker.internal:5678` 처럼 도달 가능한 주소를 쓴다.
- **`started: false` 응답의 뜻.** 체인과 Task 는 만들어졌지만 첫 업무를 시작하지 못했다 — `start_error.code` 가 `selection_required`(담당 후보 없음) 등이다. 201 이므로 n8n 실행은 Wait 로 넘어가 잠들고, 사람이 `chain_url` 에서 담당을 확정해 `워크플로우 시작` 을 누르면 그 뒤 흐름은 같다. 체인을 만들기 전의 거부(401·403·422·429)는 HTTP Request 노드가 오류로 멈춘다.
- **입구 토큰.** `/settings?tab=inbound` 에서 취소하면 다음 요청부터 401 이다. 활성 토큰은 워크스페이스당 5개.

## 7. 한계

- 업무마다 알림은 없다. 업무별 진행이 필요하면 n8n 쪽에서 `chain_url` 을 폴링한다.
- Slack 노드는 자격 증명(Slack API 토큰 또는 OAuth2)을 넣고 `disabled` 를 풀어야 실제로 보낸다. 채널 `#runloom` 은 플레이스홀더다.
- Wait 노드는 사람 차례가 올 때까지 며칠 잠들 수 있다. 필요하면 노드의 **Limit Wait Time** 을 켜 상한을 둔다(예시 JSON 은 끄지 않은 기본 상태).
- `host.docker.internal` 은 Docker Desktop(Mac/Windows) 이름이다. Linux 는 `docker run` 에 `--add-host=host.docker.internal:host-gateway` 를 붙인다.
- Webhook 트리거는 테스트용이다. 실제 운영에서는 Error Trigger(실패한 n8n 워크플로우) 나 Schedule 로 바꾸고 `run_id` 를 그 이벤트에서 뽑는다.
- 셀프호스트는 진단 데모 종류를 제공하지 않는다.
- 공개 데모 VM 은 허용 목록이 비어 있어 `callback_url` 이 있는 접수를 422 로 거부한다. 이 절차는 로컬 전용이다.
