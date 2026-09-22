# Step 7: n8n-example

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/AGENTS.md`
- `/docs/adr/0010-n8n-inbox-and-callback.md`, `/docs/adr/0008-public-demo-scripted-agents.md`
- `/docs/CONTRACT.md` 12절 — 요청 본문 예시(라벨·key)와 `ChainCallback` 필드(Slack 문구가 읽는 것)
- `/docs/ARCHITECTURE.md` "n8n 입구와 출구" 절
- `/docs/DEPLOY.md` 5·7절 — 로컬에서 데모 저장소·seed·연결 프로그램을 띄우는 절차(README 가 참조한다), `/scripts/local_stack.py` 모듈 docstring
- `/docs/README.md` — 문서 안내 형식(새 디렉터리를 여기 한 줄로 등록한다)
- `/src/workflow/server/inbound_api.py` (step 4 — 경로·오류 코드), `/src/workflow/server/settings.py` 의 두 키, `/deploy/env/central.env.example`
- `/src/workflow/adapters/task_source_fixtures/github.json` — 라벨 값(`workflow:daily-report`·`run:daily-0920-0900`·`repo:demo-report-repo`)을 그대로 쓴다
- `/tests/test_deploy_files.py` — 저장소 루트 파일을 검사하는 테스트 작성 방식(새 테스트도 같은 자리)
- 공개 문서(웹 조회 가능하면): n8n Webhook·HTTP Request·Wait(On Webhook Call)·Slack 노드 파라미터, 워크플로우 JSON 형식(`nodes`·`connections`·`settings`), `$execution.resumeUrl`, Docker 실행(`docker run -it --rm -p 5678:5678 -v n8n_data:/home/node/.n8n docker.n8n.io/n8nio/n8n`). 조회가 안 되면 아래 골격을 그대로 쓰고 README 에 "노드 파라미터 이름은 n8n 버전에 따라 다를 수 있다" 를 적는다.

## 이 phase 의 개념 (모든 step 공통 — 이 절은 step 파일마다 같다)

이 phase 는 n8n 을 업무가 **들어오는 입구**와 결과가 **나가는 출구**로 붙인다. 판단(종류·규칙·판정·인계)은 그대로 Runloom 이 한다
(ADR-0009 참고 절 "n8n 은 업무가 들어오는 입구·나가는 출구로만 쓴다"). n8n 쪽은 노드 4개 — Webhook(또는 Error Trigger) → HTTP Request(Runloom 에 POST) →
Wait(On Webhook Call — `$execution.resumeUrl` 로 깨어남) → Slack. ADR-0010(step 0 이 쓴다)이 정본이다. 사용자는 n8n 을 써 본 적이 없고,
이 제품을 "n8n 옆의 에이전트 인계 계층"으로 소개한다 — 문서·화면·주석에 n8n 을 비판하는 문구를 쓰지 않는다.

- **`n8n` 은 `TaskSource` 하나**: `chains.source` 값 `'n8n'`, `domain/task_sources.Source` 에 `"n8n"`. 본문 항목은 GitHub·Jira fixture 의 `Issue` 와
  같은 모양(`key`·`title`·`body`·`labels`·`blocked_by`, `url` 은 None)이고 라벨 규칙(`incident`+`workflow:<id>`+`run:<run_id>`, `bug`+`repo:<id>`,
  `kind:<kind>`+`<scope_key>:<value>`)도 그대로다 — `map_issue`·`compose` 를 재사용하며 **매핑·구성·워커 후속 코드에 n8n 분기를 넣지 않는다**.
  fixture 출처 목록 `adapters/task_sources.SOURCES`(`github`·`jira`)는 그대로다 — n8n 은 파일이 없으므로 가져오기 화면(`/tasks/import`)에 나오지 않는다.
- **입구 토큰 (`source token`)**: 워크스페이스(세션)가 `/sources` 화면에서 발급한다. 원문은 `wfs_` + `secrets.token_urlsafe(32)`, 발급 응답에서 한 번만 보이고
  서버에는 sha256 만 남는다(연결 토큰 `wfc_` 와 같은 방식, `repo.exchange_connect_code` 참고). 취소하면 다음 요청부터 401. `Authorization: Bearer wfs_…` → `session_id` + `source`.
- **입구 API** `POST /sources/n8n/chains` (JSON 본문 `InboundChainRequest`, 응답 201 `InboundChainResponse`): 항목으로 체인 + Task 들을 가져오기(`/tasks/import`)와
  같은 규칙으로 만들고, **접수 즉시 첫 업무 시작을 시도한다**(n8n 트리거가 곧 사람의 "워크플로우 시작"). 첫 업무가 후보 없음(409 `selection_required`)·상한(429)·
  조건 미충족(409)이면 체인은 남기고 `started=false` + `start_error`(오류 본문)로 201 을 돌려준다. 세션에 등록된 에이전트가 없으면 422 `agent_not_registered`.
  `callback_url` 은 선택이며 http/https 만, 허용 목록 밖이면 422 `callback_host_not_allowed`. 토큰 없음·취소 401 `unauthenticated`, 토큰의 `source` 가 경로와 다르면 403.
- **callback (출구)**: 체인이 **사람 차례**(`chain_settled`)가 되면 워커가 `callback_url` 로 `ChainCallback` 을 **체인당 1회** POST 한다
  (n8n Wait 노드는 한 번만 깨어난다). `chain_settled(nodes)` 는 도메인 순수 함수(`domain/settlement.py`): (a) 어떤 업무도 `실행 요청됨`·`실행 중` 이 아니고,
  (b) `대기` 인 업무는 모두 선행 업무의 상태가 `확인 필요` 또는 `실패` 이면(= 사람에게 막힘) 참. 그 밖의 `대기`(자동 실행 대기·연결 끊김·선행 진행 중)와
  실행 중은 아직 워커 몫이라 거짓. 빈 목록은 거짓. 남는 상태(`확인 필요`·`완료`·`실패`·`실행 가능`)는 사람 조작 전엔 바뀌지 않는다 — 화면 폴링 규칙(`views._LIVE_LABELS`)과 같은 관찰이다.
  워커 tick 의 **마지막 단계**(후속 스캔·실패 반영 뒤)에서 판정하므로, A 판정 → B 착수가 같은 tick 에 일어나면 그 사이에 보내지 않는다.
  전송은 트랜잭션 밖 HTTPX POST(10초). 2xx 면 `callback_sent_at`, 아니면 `callback_attempts`+1 과 `callback_next_at = now + 30·2^(n-1)초`(30·60·120·240초), 5회 실패 후 중단(`callback_last_error` 를 화면에).
  사람이 그 뒤 승인·종료해도 다시 보내지 않는다. 직접 등록·가져오기 화면으로 만든 체인은 `callback_url` 이 없으므로 아무것도 보내지 않는다.
- **허용 목록** `WORKFLOW_CALLBACK_HOSTS`: 콤마 구분 `host` 또는 `host:port`(예 `localhost:5678,127.0.0.1`). `host` 만 쓰면 그 호스트의 모든 포트. 비어 있으면 callback 을 받지 않는다(접수 시 422).
  이유: 공개 데모 VM 은 누구나 세션을 만들 수 있어, 외부가 준 주소로 서버가 POST 하게 두면 내부 주소(`127.0.0.1:8100` 등)를 찌를 수 있다. 셀프호스트는 `localhost:5678` 한 줄.
  판정 함수는 `domain/callback_policy.py` 의 `host_allowed(url, allowed)`·`parse_hosts(raw)` 다. `WORKFLOW_PUBLIC_URL`(선택, 예 `http://127.0.0.1:8000`, 끝 `/` 없음)은
  응답·callback 의 `chain_url`·`task_url` 앞에 붙는다. 비면 두 필드는 null. 둘 다 비밀값이 아니다.
- **계약** (`contracts/v1.py`, step 1):
  `InboundItem(key, title, body, labels: list[str], blocked_by: list[str])` ·
  `InboundChainRequest(contract_version, items: list[InboundItem] 1~10개, key 유일, blocked_by 는 같은 요청의 key 만, callback_url: str | None)` ·
  `InboundChainResponse(contract_version, chain_id, chain_url: str | None, started: bool, start_error: ErrorBody | None, tasks: list[InboundTaskRef(task_id, key, kind, status)], skipped: list[InboundSkipped(key, reason)])` ·
  `ChainCallback(contract_version, chain_id, title, source: Literal["n8n"], chain_url: str | None, settled_at, human_gate: CallbackGate(label, status_label, reason), tasks: list[CallbackTask(task_id, key, kind, title, status, status_reason, outcome: str | None, summary: str | None, task_url: str | None)])`.
  `outcome`·`summary` 는 그 Task 의 최신 결과 봉투(`diagnosis_result`·`code_change_result`·`generic_result` 산출물)에서 읽고, 결과가 없으면 null. `status` 는 `USER_STATUS_LABELS` 의 문구다.
- **저장** (step 3): `SCHEMA_VERSION` 3 → 4, 마이그레이션 없음(`WORKFLOW_RESET_DB=1` 재생성 — 공개 데모 VM 은 손대지 않는다).
  신규 `source_tokens(token_id PK 'src-'+8hex, session_id, source, token_sha256 UNIQUE, label, created_at, last_used_at, revoked_at)`.
  `chains` 에 `items_json`(n8n 이 보낸 항목 원문 — 체인 화면의 구성 이유 재계산용)·`callback_url`·`callback_sent_at`·`callback_attempts`(기본 0)·`callback_next_at`·`callback_last_error` 추가, `source` CHECK 에 `'n8n'`.
- **화면** (step 6): `/sources`(입구 URL·토큰 목록·발급·취소·curl 예시), 사이드바 "입구" 링크, 체인 화면에 출처 `n8n` 과 callback 상태 한 줄.
- **증명** (step 8·10): e2e 는 테스트 안 HTTP 수신기가 n8n 역할 — 토큰 발급 → POST → 대본 A→B → 수신기가 `ChainCallback` 1건(B `확인 필요 · 검토 대기`) 받고 두 번째는 안 온다.
  step 10 은 Docker 의 실제 n8n 으로 같은 흐름을 1회 돌리고 VERIFICATION_LOG 에 남긴다.
- **하지 않는 것**: 등록부 → n8n JSON 컴파일, LLM 워크플로우 생성, 업무마다 callback, 완료 시 새 업무 생성 규칙(ADR-0009 트레이드오프 — 다음), n8n 비판 문구, `graph`·`DAG`·`pipeline`·`workflow`(제품 흐름 뜻) 단어.

## 작업

문서와 예시 파일을 만든다. 제품 코드는 고치지 않는다. TDD: `tests/test_n8n_example.py` 를 먼저 쓰고(파일 없음으로 실패) 파일을 만든다.

### 1. `docs/n8n/runloom-handoff.json` — import 가능한 n8n 워크플로우

n8n 워크플로우 JSON(`{"name": "Runloom handoff", "nodes": [...], "connections": {...}, "settings": {"executionOrder": "v1"}}`). 노드 4개, 이 순서로 연결:

1. **Webhook** (`n8n-nodes-base.webhook`): `httpMethod: "POST"`, `path: "runloom-demo"`, 응답은 즉시. 테스트용 트리거다 — README 에 "실제 운영에서는 Error Trigger 나 Schedule 로 바꾼다" 를 적는다. 입력 본문 예: `{"run_id": "daily-0920-0900"}`.
2. **HTTP Request** (`n8n-nodes-base.httpRequest`): `method: "POST"`, `url: "http://host.docker.internal:8000/sources/n8n/chains"`, 인증은 Generic → Header Auth 자격 증명(`httpHeaderAuth`, 이름 "Runloom source token" — 헤더 `Authorization`, 값 `Bearer wfs_…`; 자격 증명 값은 JSON 에 넣지 않는다, `credentials` 참조만), 본문 JSON(표현식):
   ```
   {{ JSON.stringify({
     contract_version: 1,
     callback_url: $execution.resumeUrl,
     items: [
       { key: "run-" + $json.body.run_id, title: "일일 보고서 생성 실패 (" + $json.body.run_id + ")", body: "실행 " + $json.body.run_id + " 의 실패 원인을 진단해 주세요.",
         labels: ["incident", "workflow:daily-report", "run:" + $json.body.run_id], blocked_by: [] },
       { key: "fix-" + $json.body.run_id, title: "집계 API 응답 형식 변경 대응", body: "진단 결과를 근거로 demo-report-repo 를 수정하고 테스트를 통과시켜 주세요.",
         labels: ["bug", "repo:demo-report-repo"], blocked_by: ["run-" + $json.body.run_id] }
     ]
   }) }}
   ```
3. **Wait** (`n8n-nodes-base.wait`): `resume: "webhook"`, `httpMethod: "POST"`. Limit Wait Time 은 켜지 않되 README 에 "며칠 잠들 수 있으니 필요하면 켠다" 를 적는다.
4. **Slack** (`n8n-nodes-base.slack`): `"disabled": true`(자격 증명 전엔 데이터를 그대로 통과), 메시지 텍스트 표현식은 callback 본문에서 — `{{ $json.body.title }} — {{ $json.body.human_gate.status_label }} · {{ $json.body.human_gate.reason }}` 과 `{{ $json.body.chain_url }}` 두 줄. 채널은 플레이스홀더 `#runloom`.

JSON 안에 실제 토큰·비밀값·개인 정보를 넣지 않는다. 노드 `id`·`position`·`typeVersion` 은 n8n 이 받아들이는 형식으로 채운다(공개 문서에서 확인한 값, 못 확인하면 최근 안정 버전 값을 쓰고 README 에 명시).

### 2. `docs/n8n/README.md`

절 순서: (1) 무엇을 하는가 — 장면 한 문단과 노드 4개 표(노드 · 하는 일 · Runloom 쪽 대응), (2) 준비 — Docker 로 n8n(`docker run -d --name runloom-n8n -p 5678:5678 -v runloom_n8n_data:/home/node/.n8n docker.n8n.io/n8nio/n8n`), Runloom 로컬(`python3 scripts/local_stack.py --scripted --callback-hosts localhost:5678 --public-url http://127.0.0.1:18000` — step 8 이 인자를 만든다; 또는 개발 서버 + 환경변수 `WORKFLOW_CALLBACK_HOSTS=localhost:5678`·`WORKFLOW_PUBLIC_URL=http://127.0.0.1:8000`), 포트 표(로컬 스택 18000 / 개발 서버 8000 — HTTP Request 노드 URL 을 맞춘다), (3) Runloom 에서 — 에이전트 등록·`/sources` 토큰 발급, (4) n8n 에서 — 화면 import(Workflows → Import from File), Header Auth 자격 증명 만들기, HTTP Request 노드에 연결, 활성화(publish); CLI 대안(`docker cp` + `docker exec runloom-n8n n8n import:workflow --input=…`, `n8n import:credentials --input=…`(decrypted 형식 `[{"id","name","type":"httpHeaderAuth","data":{"name":"Authorization","value":"Bearer wfs_…"}}]`), `n8n publish:workflow --id=…`(구버전 `update:workflow --id=… --active=true`), 컨테이너 재시작), (5) 실행 — `curl -X POST http://localhost:5678/webhook/runloom-demo -H 'content-type: application/json' -d '{"run_id":"daily-0920-0900"}'` → Runloom 체인 화면 링크 → 1~2분 뒤 callback → n8n 실행 목록에서 Wait 가 깨어난 것 확인, (6) 동작 규칙 — callback 은 체인당 1회·"사람 차례"에, 허용 목록, `started=false` 응답의 뜻, (7) 한계 — 업무별 알림 없음, Slack 자격 증명, `host.docker.internal` 은 Docker Desktop(Mac/Windows) 이름이며 Linux 는 `--add-host` 필요.
어조: 절차서. n8n 을 비판하는 문장·"대체" 표현 금지. 공개 데모 VM(`runloom.duckdns.org`)은 허용 목록이 비어 있어 callback 이 오지 않는다고 적는다.

### 3. `docs/README.md` 에 `n8n/` 디렉터리 한 줄 추가.

### 4. `tests/test_n8n_example.py`

`docs/n8n/runloom-handoff.json` 을 읽어: JSON 파싱, `nodes` 4개, type 이 순서대로 webhook·httpRequest·wait·slack, `connections` 가 1→2→3→4 로 이어짐, HTTP Request 의 url 에 `/sources/n8n/chains` 포함, 본문 표현식에 `$execution.resumeUrl`·`contract_version`·`callback_url`·`incident`·`workflow:daily-report`·`repo:demo-report-repo` 포함, Wait 의 `resume == "webhook"`, Slack 노드 `disabled == True`, 파일 전체에 `wfs_` 뒤에 플레이스홀더(`…` 또는 `<`)가 아닌 실제 토큰 모양(`wfs_[A-Za-z0-9_-]{20,}`)이 없음, `sk-` 없음. `docs/n8n/README.md` 에 `docker run`·`/sources`·`WORKFLOW_CALLBACK_HOSTS`·`webhook/runloom-demo` 가 있음.

## Acceptance Criteria

```bash
python3 -m pytest tests/test_n8n_example.py -q
python3 -c "import json; d=json.load(open('docs/n8n/runloom-handoff.json')); print(len(d['nodes']), [n['type'] for n in d['nodes']])"
grep -c "n8n/" docs/README.md      # 1 이상
python3 -m pytest -q
python3 -m ruff check .
git diff --stat HEAD -- src        # 빈 출력 — 제품 코드는 손대지 않는다
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - 예시 파일에 비밀값·실제 토큰이 없는가?
   - 본문의 라벨·key 가 CONTRACT 12절·fixture 값과 같은가?
   - README 가 GLOSSARY 용어(`입구 토큰`·`callback`·`체인`)를 쓰고 n8n 비판 문구가 없는가?
   - 제품 코드를 건드리지 않았는가?
3. 결과에 따라 `phases/7-n8n-gateway/index.json` 의 해당 step 을 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"` (파일 경로·노드 구성·README 절 이름·확인한 n8n 버전을 적는다)
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- 이 step 에서 Docker·n8n 을 실제로 띄우지 마라. 이유: 실연동은 step 10 의 몫이며 여기서는 파일과 절차서만 만든다.
- 워크플로우 JSON 을 생성하는 코드·버튼을 제품에 넣지 마라. 이유: "등록부 → n8n JSON 컴파일" 은 하지 않기로 했다(ADR-0010). 예시는 정적 파일 하나다.
- `items` 에 라벨 대신 `kind`·`scope` 필드를 쓰지 마라. 이유: 입구 계약은 라벨 규칙 하나다.
- README 에 공개 데모 VM 에서 이 절차를 따라 하라고 쓰지 마라. 이유: 심사 기간 동결이며 허용 목록이 비어 있다.
- 기존 테스트를 깨뜨리지 마라.
