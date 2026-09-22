# Step 8: e2e-n8n

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/AGENTS.md`
- `/docs/adr/0010-n8n-inbox-and-callback.md`, `/docs/adr/0008-public-demo-scripted-agents.md`
- `/docs/ARCHITECTURE.md` "n8n 입구와 출구" 절, "공개 데모 구성"
- `/docs/CONTRACT.md` 12절
- `/docs/VERIFICATION_LOG.md` 2026-09-22 절 두 개 — 기록 형식(무엇이 실제였고 무엇이 대본이었는지)
- `/scripts/local_stack.py` 전부 — `LocalStack.__init__`·`_plan`(central_env)·`main` 의 argparse, `/tests/e2e/conftest.py`
- `/tests/e2e/test_scenario.py` 전부 — 특히 체인 절(test_12~21: `chain_stack`·`judge`·`_register_all`·`_import`·`_watch_chain`·`_nodes`·`_gate`)과 세 번째 종류 절(test_22~28: `reviewer`·`_stored_status`·`_executions`·`_wait_verdict`)의 헬퍼와 대기 방식
- `/src/workflow/server/inbound_api.py`(step 4), `/src/workflow/server/web.py` 의 `/sources` 라우트(step 6), `/src/workflow/server/worker.py` 의 `_deliver_callbacks`(step 5), `/src/workflow/contracts/v1.py` 의 `ChainCallback`(step 1)
- `/docs/n8n/README.md` (step 7 — 로컬 스택 인자 이름을 여기와 맞춘다)

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

이 phase 의 증명이다. e2e 를 먼저 쓰고(실패 확인) `local_stack.py` 의 작은 변경으로 통과시킨다. 제품 코드는 고치지 않는다 — e2e 가 제품 결함을 드러내면 그 결함만 최소로 고치고 `summary` 에 적는다.

### 1. `scripts/local_stack.py`

`LocalStack.__init__` 에 `callback_hosts: str = "127.0.0.1"`, `public_url: str | None = None`(None 이면 `central_url`) 인자를 더하고 `_plan` 의 `central_env` 에 `WORKFLOW_CALLBACK_HOSTS`·`WORKFLOW_PUBLIC_URL` 을 넣는다(중앙 API·중앙 워커 둘 다 같은 env 를 쓰는지 확인). `main` 에 `--callback-hosts`(기본 `127.0.0.1`)·`--public-url`(기본 없음) 인자를 더한다. 모듈 docstring 의 사용법 줄에 반영. `tests/e2e/conftest.py` 는 기본값으로 충분하면 손대지 않는다.

### 2. `tests/e2e/test_scenario.py` — 새 절 `# --- n8n 입구·출구 (phase 7 step 8) ---`

모듈 fixture: `callback_receiver` — `http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)` 를 데몬 스레드로 띄우고 받은 `(path, headers, json body)` 를 목록에 쌓으며 200 `{"ok": true}` 로 답한다. 실패 모드는 필요 없다(워커 단위 테스트가 했다). `n8n_client` — `chain_stack` 위의 새 세션 `httpx.Client`. `n8n_ctx` — dict.

테스트(번호는 기존 마지막 다음부터, 이름은 `test_NN_n8n_…`):
1. **입구 화면과 토큰 발급**: `_register_all` 로 카탈로그 3개 등록 → `GET /sources` 200, 입구 주소에 `/sources/n8n/chains` → `POST /sources/tokens` (label "e2e") → 응답 HTML 에서 `wfs_[A-Za-z0-9_-]+` 를 정규식으로 뽑아 `n8n_ctx["token"]` → 다시 `GET /sources` 에는 원문 없음.
2. **POST 로 체인이 생기고 첫 업무가 바로 시작된다**: 쿠키 없는 `httpx.post(f"{stack.central_url}/sources/n8n/chains", headers={"Authorization": f"Bearer {token}"}, json={...})` — 항목은 CONTRACT 12절 (a)(진단 `run-daily-0920` + 수정 `fix-format`, 담당은 자동 선택이므로 수정 후보가 codex·claude 둘이면 `확인 필요` 가 된다 → 첫 테스트에서 등록을 `ops`·`codex` 두 개만 하거나, 기존 체인 테스트가 쓰는 방식(`prefer` 로 먼저 등록한 것)을 따른다 — `_register_all` 의 순서를 `("ops", "codex", "claude")` 로 하면 `prefer` 규칙으로 codex 가 기본 선택된다, 확인), `callback_url=f"http://127.0.0.1:{receiver.port}/webhook-waiting/e2e"` → 201, `started is True`, `tasks[0].status == "실행 요청됨"`, `tasks[1].status == "대기"`, `chain_url` 이 `central_url` 로 시작. `n8n_ctx["chain_id"]`.
3. **A → B 가 사람 조작 없이 돌고 B 가 검토 대기가 된다**: `_watch_chain` 으로 B 노드가 `("확인 필요", "검토 대기")` 가 될 때까지(기존 test_17 의 시간 한도와 같게).
4. **callback 이 정확히 1건 오고 본문이 계약대로다**: 수신기 목록이 1건이 될 때까지 최대 30초 폴링 → `ChainCallback.model_validate(body)` 통과, `chain_id` 일치, `source == "n8n"`, `tasks[0].kind == "diagnosis"`·`outcome == "ready_for_handoff"`, `tasks[1].kind == "code_change"`·`status == "확인 필요"`·`outcome == "ready_for_review"`·`summary` 비어 있지 않음, `human_gate.status_label == "확인 필요"`, `chain_url`·`task_url` 이 `central_url` 로 시작, 요청 헤더 `content-type` 이 `application/json`. 체인 화면에 "callback · 전송됨".
5. **승인 뒤에도 두 번째 callback 은 없다**: B 승인(`POST /tasks/{id}/review` approve) → 워커 tick 3번 이상 지나도록 10초 대기 → 수신기 목록 여전히 1건. DB(`_stored_status` 방식으로 sqlite 직접)에서 `callback_attempts == 0`·`callback_sent_at` 있음.
6. **거부 경로**: (a) 허용 밖 `callback_url="http://example.com/x"` → 422 `callback_host_not_allowed`, 체인 안 생김(홈/목록 개수 비교); (b) 토큰 취소(`POST /sources/tokens/{id}/revoke`) 뒤 같은 POST → 401; (c) Bearer 없음 → 401; (d) `callback_url` 없이 정상 POST → 201, 그 체인은 callback 없이 진행(화면에 callback 줄 없음).
7. **다른 세션은 이 체인을 못 본다**: 기존 test_09/20 방식.

대기 시간·폴링 간격은 기존 체인 테스트와 같은 상수를 쓴다. 활성 업무 한도(5)·진단 일일 한도는 새 세션이므로 여유가 있지만, 6-(d) 까지 합쳐 활성 업무가 5개를 넘지 않게 앞 체인의 B 를 승인해 마감한 뒤 진행한다.

### 3. `docs/VERIFICATION_LOG.md`

절 `## 2026-09-22 — n8n 입구·출구 e2e (phase 7 step 8)` 을 추가한다: 무엇이 대본이고 무엇이 실제였는지(에이전트 대본, n8n 은 테스트 수신기), 시간, 통과한 테스트 번호, 발견한 결함(있으면). 실제 n8n 은 step 10.

## Acceptance Criteria

```bash
WORKFLOW_E2E=1 python3 -m pytest tests/e2e -q -k "n8n"        # 새 절만
WORKFLOW_E2E=1 python3 -m pytest tests/e2e -q                 # 전체 (기존 29 + 새 것) — 약 2분
python3 -m pytest -q
python3 -m ruff check .
python3 scripts/local_stack.py --help | grep -c "callback-hosts\|public-url"    # 2
```

## 검증 절차

1. 위 AC 커맨드를 실행한다. e2e 실패 시 `stack log:` 섹션(중앙 워커 로그의 `tick` 줄·`callback` 관련 줄)을 먼저 본다.
2. 아키텍처 체크리스트를 확인한다:
   - 제품 코드 diff 가 없는가? 있다면 e2e 가 드러낸 결함의 최소 수정인가? (`git diff --stat HEAD -- src`)
   - e2e 가 대본 스택(`scripted=True`) 그대로인가? 실제 codex/claude/모델을 부르지 않는가?
   - 테스트가 시간에 의존한 flaky 대기(`sleep` 고정)를 쓰지 않고 상태 폴링을 쓰는가?
   - 수신기가 `127.0.0.1` 에만 바인드되는가?
3. 결과에 따라 `phases/7-n8n-gateway/index.json` 의 해당 step 을 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"` (테스트 번호·소요 시간·local_stack 인자·고친 결함을 적는다)
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- 통과시키려고 `chain_settled`·워커 단계 순서·허용 목록 검사를 느슨하게 만들지 마라. 이유: 그것이 이 phase 의 계약이다. e2e 가 실패하면 원인을 `summary` 에 적고 최소 수정만 한다.
- 수신기를 `0.0.0.0` 에 바인드하지 마라. 이유: 테스트 중 외부 접속을 열 이유가 없다.
- 기존 e2e(test_01~28)의 순서·헬퍼를 바꾸지 마라. 이유: 모듈 fixture 하나에 여러 절이 순서대로 얹혀 있다. 새 절은 뒤에만 붙인다.
- 실제 n8n·Docker 를 이 step 에서 쓰지 마라. 이유: step 10 의 몫이며 e2e 는 CI 에서 Docker 없이 돌아야 한다.
- 기존 테스트를 깨뜨리지 마라.
