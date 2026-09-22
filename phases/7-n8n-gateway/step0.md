# Step 0: adr-n8n-gateway

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/AGENTS.md`
- `/docs/ARCHITECTURE.md` 전부 — 특히 "첫 선택과 전제"(외부 의존 표), "인증·권한·비밀정보 규칙", "업무 종류와 후속 규칙", "최소 데이터 모델과 영속성", "상태·재접속·완료", "검증 순서와 다음 결정"
- `/docs/adr/` 전부 — 특히 `0000-principles.md`, `0004-central-service-rule-based-no-llm.md`, `0005-access-model-anonymous-session-operator-token.md`, `0009-registered-kinds-and-succession-rules.md` (형식과 어조를 따른다. 0009 참고 절의 "n8n 은 입구·출구" 한 줄이 이 phase 의 출발점이다)
- `/docs/GLOSSARY.md` 전부 (표 형식·"금지 표현" 열·"경계가 헷갈리는 개념" 절)
- `/docs/CONTRACT.md` 전부 (절 번호·JSON 예시·오류표 형식)
- `/docs/PRD.md` 의 n8n 언급 줄(`grep -n n8n docs/PRD.md`) — 고치지 않는다, 모순만 피한다
- `/src/workflow/domain/task_sources.py`, `/src/workflow/domain/composition.py`, `/src/workflow/domain/status.py`(`USER_STATUS_LABELS`·`user_status`), `/src/workflow/server/worker.py` 의 모듈 docstring·`tick`·`_spawn_successors`, `/src/workflow/server/views.py` 의 `_LIVE_LABELS`·`chain_summary`·`_human_gate`, `/src/workflow/adapters/db.py` 의 `chains`·`connectors` 테이블, `/src/workflow/adapters/repo.py` 의 `exchange_connect_code`·`authenticate_connector` — 지금 무엇이 있는지 확인용. **이 step 은 코드를 고치지 않는다.**

이전 step 은 없다. 이 step 이 이 phase 의 첫 문서를 만들고, 뒤의 모든 step 이 이 문서를 읽는다.

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

문서만 쓴다. 코드·테스트를 만들지 않는다. 위 "이 phase 의 개념" 절이 정본이며, 아래 문서는 그것을 각 문서의 형식으로 옮긴 것이다. 개념 절과 다르게 쓰지 마라.

### 1. `docs/adr/0010-n8n-inbox-and-callback.md` (신규)

제목: `ADR-0010: n8n 은 업무가 들어오는 입구와 나가는 출구다 — 판단은 Runloom 이 한다`. 결정일 2026-09-22, 사용자 확정. 기존 ADR 형식(**결정** / **이유** / **트레이드오프**)을 따르고 아래를 담는다:

- **결정**: (1) n8n 은 `TaskSource` 하나(`n8n`)다. 본문 항목은 `Issue` 와 같은 모양이고 라벨 규칙·`compose` 를 그대로 쓴다 — 매핑·구성·후속 코드에 n8n 분기를 두지 않는다. (2) 입구 인증은 워크스페이스가 화면에서 발급하는 입구 토큰(`wfs_…`, 서버엔 sha256, 취소 가능)이다 — 팀 전제(ADR-0009)라 워크스페이스별로 구분한다. (3) 입구 API 는 접수 즉시 첫 업무를 시작한다(n8n 트리거 = 사람의 시작 조작). 시작이 거부돼도 체인은 남긴다. (4) 출구는 callback 이며 체인이 "사람 차례"(`chain_settled` — 실행 중인 업무가 없고 `대기` 는 모두 선행의 `확인 필요`/`실패` 에 막힌 상태)가 될 때 **체인당 1회** 보낸다. 사람 승인 뒤에 다시 보내지 않는다. (5) callback 대상은 허용 목록 `WORKFLOW_CALLBACK_HOSTS` 안에서만 — 비어 있으면 거부. (6) 중앙은 여전히 LLM 을 부르지 않는다(ADR-0004 유지).
- **이유**: 사용자의 원래 문제는 착수 대기이고 n8n 은 회사에 이미 있는 트리거·알림 도구다. n8n 이 트리거·알림을, Runloom 이 에이전트 단계(계약 판정·근거 인계·사람 게이트)를 맡으면 서로를 대체하지 않는다. 흐름을 n8n 노드로 다시 그리면(등록부 → n8n JSON) 판단이 정적 그래프로 넘어가 ADR-0009 의 차별점("안 그려도 흐른다 + 넘어갈 때 검증한다")이 사라진다. callback 을 "사람 차례" 1회로 두는 이유: n8n Wait 노드는 한 번만 깨어나고, 사람이 필요한 순간이 곧 알림이 필요한 순간이다("검토해 주세요"). 마지막 업무 `완료` 만 기다리면 알림이 사람 승인 뒤에 와서 쓸모가 없다. 허용 목록 이유: 공개 데모 VM 은 누구나 세션을 만들 수 있으므로 외부가 준 주소로 서버가 POST 하게 두면 내부 주소를 찌를 수 있다.
- **트레이드오프**: 업무마다 알림은 없다(n8n 쪽에서 체인 화면을 폴링하거나 다음 phase). `대기 · 연결 끊김` 은 선행이 `확인 필요` 면 사람 차례로 본다(연결이 돌아와도 다시 보내지 않는다). 진단 API 후속(API 에이전트가 두 번째 업무)은 워커가 시작하지 않으므로 `대기` 에 머물러 callback 이 안 온다 — ADR-0009 한계 그대로. 입구 본문이 라벨 규칙이라 n8n 쪽 표현식에 라벨을 써야 한다(`kind`·`scope` 필드를 따로 두는 형식은 만들지 않았다 — 매핑 경로를 하나로 유지). 스키마 v3 → v4 재생성.
- 참고 한 줄: n8n AI Workflow Builder(LLM 이 워크플로우 JSON 생성)와 겹치지 않는다 — 이 제품은 워크플로우를 만들지 않는다.

### 2. `docs/GLOSSARY.md`

"용어" 표에 아래 행을 추가한다(코드 식별자 그대로, 금지 표현 포함). 기존 `TaskSource` / `source` 행은 `n8n` 을 포함하도록 고친다(`github`/`jira`/`n8n`. `n8n` 은 fixture 가 없고 입구 API 로만 들어온다).

| 용어 | 정의 요지 | 금지 표현 |
|---|---|---|
| `source token` / `wfs_` | 워크스페이스가 `/sources` 에서 발급하는 입구 토큰. `Authorization: Bearer wfs_…` → `session_id`·`source`. 서버엔 sha256(`source_tokens`), 취소하면 401. 연결 토큰 `wfc_` 와 구분(그건 연결 프로그램) | `api key`, `webhook secret`, `inbound token`(코드에 없는 이름) |
| `InboundChainRequest` / `InboundItem` | 입구 API 본문. 항목은 `Issue` 와 같은 모양(`key`·`title`·`body`·`labels`·`blocked_by`), 1~10개, `callback_url` 선택 | `payload`, `trigger` |
| `InboundChainResponse` | 입구 API 응답 — `chain_id`·`chain_url`·`started`·`start_error`·`tasks`·`skipped` | — |
| `ChainCallback` | 체인이 사람 차례가 될 때 워커가 `callback_url` 로 보내는 본문 — `human_gate`·업무별 `status`·`outcome`·`summary`·링크. 체인당 1회 | `webhook event`, `notification`, `status update` |
| `chain_settled` / 사람 차례 | 도메인 순수 함수(`domain/settlement.py`). 실행 요청됨·실행 중이 없고 `대기` 는 모두 선행의 `확인 필요`/`실패` 에 막혔을 때 참 — callback 시점 | `done`, `finished`(Task 마감과 혼동), `idle` |
| `WORKFLOW_CALLBACK_HOSTS` / `host_allowed` | callback 대상 허용 목록(`host` 또는 `host:port`, 콤마)과 그 판정(`domain/callback_policy.py`). 비면 거부 | `whitelist`(문구로 쓰지 않음), `CORS` |

"경계가 헷갈리는 개념" 절에 두 항목을 추가한다: (a) `source token` 과 `connect code`/`wfc_`: 전자는 워크스페이스가 발급해 외부(n8n)가 업무를 넣을 때, 후자는 운영자가 발급해 연결 프로그램이 실행을 가져갈 때. (b) `callback_url` 과 `ExecutionEvent`: 전자는 체인 단위로 밖(n8n)에 1회 보내는 것, 후자는 실행 주체가 안으로 보내는 것.

### 3. `docs/ARCHITECTURE.md`

"업무 종류와 후속 규칙" 절 다음에 새 절 `## n8n 입구와 출구 — 2026-09-22 확정` 을 추가한다. 내용: (1) 장면 — n8n 4노드와 Runloom 의 역할 분담, (2) 입구 — 토큰 발급·인증·`POST /sources/n8n/chains`·즉시 시작·거부 시 `started=false`, (3) 출구 — `chain_settled` 규칙과 워커 마지막 단계·1회·재시도(30·60·120·240초, 5회)·허용 목록, (4) 저장 — `source_tokens` 와 `chains` 의 새 컬럼, (5) 화면 — `/sources`·체인 화면의 callback 상태, (6) 한계 — 업무별 알림 없음, API 에이전트 후속은 안 옴, 공개 데모 VM 은 허용 목록이 비어 있어 callback 없음.

"인증·권한·비밀정보 규칙" 표에 행 `| n8n(입구) | 입구 토큰 wfs_… (Bearer) | 자기 워크스페이스에 체인 등록·첫 업무 시작 | 웹 동작·다른 워크스페이스·연결 프로그램 API |` 를 추가하고, 표 아래에 "입구 토큰: 세션이 `/sources` 에서 발급한다. 원문은 발급 응답에 한 번, 서버에는 sha256 만. 취소는 같은 세션만." 한 문단을 붙인다. "첫 선택과 전제" 표의 `n8n·A2A·MCP·OpenArchive` 행은 `n8n 은 phase 7 에서 입구·출구로 연결(ADR-0010). A2A·MCP·OpenArchive 는 그대로 제외` 로 고친다. "최소 데이터 모델과 영속성" 표에 `source token`·`Chain.callback_*` 행을 추가한다. "검증 순서와 다음 결정" 표에 7번 행 `| 7 | n8n 입구·출구 — 미검증 | 실제 n8n(Docker)이 POST 한 항목으로 체인이 생겨 A → B 가 사람 조작 없이 돌고, B 검토 대기 시점에 callback 이 n8n Wait 노드를 깨운다(2xx) |` 를 추가한다. 다른 절은 손대지 않는다.

### 4. `docs/CONTRACT.md`

`## 12. n8n 입구·callback` 절을 추가한다. JSON 예시: (a) `InboundChainRequest` — 항목 2개(진단: `run-daily-0920` · 라벨 `incident`·`workflow:daily-report`·`run:daily-0920-0900` / 수정: `fix-format` · 라벨 `bug`·`repo:demo-report-repo` · `blocked_by: ["run-daily-0920"]`), `callback_url: "http://localhost:5678/webhook-waiting/1234"`, (b) `InboundChainResponse` — `started: true`, tasks 2개(`status` 는 `실행 요청됨`·`대기`), `skipped: []`, (c) `InboundChainResponse` 의 `started: false` 예시 — `start_error: {code: "selection_required", …}`, (d) `ChainCallback` — B 가 `확인 필요 · 검토 대기`, `outcome: "ready_for_review"`, `human_gate: {label: "검토 승인 (사람) · 병합은 운영자 확인", status_label: "확인 필요", reason: "검토 대기"}`, (e) 오류 본문 — 401 `unauthenticated`, 403 `forbidden`(토큰 source 불일치), 422 `callback_host_not_allowed`, 422 `agent_not_registered`, 422 `dependency_cycle`, 429 `active_task_limit_reached`. 3절 오류표에 새 코드 두 개(`callback_host_not_allowed`, `selection_required`(있으면 유지))를 추가한다. 인증 절(있으면)에 `Authorization: Bearer wfs_…` 한 줄.

## Acceptance Criteria

```bash
ls docs/adr/0010-n8n-inbox-and-callback.md
grep -n "source token\|InboundChainRequest\|ChainCallback\|chain_settled\|WORKFLOW_CALLBACK_HOSTS" docs/GLOSSARY.md docs/ARCHITECTURE.md docs/CONTRACT.md | wc -l   # 0 이 아니어야 한다
grep -n "## 12\." docs/CONTRACT.md                     # 1건
grep -n "n8n 입구와 출구" docs/ARCHITECTURE.md          # 1건
grep -rn "DAG\|pipeline" docs/adr/0010-n8n-inbox-and-callback.md   # 0건
python3 -m pytest -q          # 문서만 바꿨으므로 그대로 통과
python3 -m ruff check .
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - 문서 사이에 모순이 없는가? (ADR·GLOSSARY·ARCHITECTURE·CONTRACT 가 같은 필드명·같은 오류 코드·같은 환경변수 이름을 쓴다)
   - GLOSSARY 의 금지 표현(`edge`, `transition`, `pipeline`, `DAG`, `graph`, `workflow`(제품 흐름 뜻))을 새 문장에 쓰지 않았는가?
   - AGENTS.md CRITICAL 규칙과 충돌하는 문장이 없는가? (중앙 LLM 미사용, 외부 입력의 경로·명령 미실행, 비밀값은 환경변수·해시만)
   - n8n 을 비판하는 문구가 없는가?
3. 결과에 따라 `phases/7-n8n-gateway/index.json` 의 해당 step 을 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"` (만든 파일·절 이름을 적어 뒤 step 이 찾게 한다)
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- 코드·테스트·템플릿을 만들거나 고치지 마라. 이유: 이 step 은 문서 정본을 만드는 단계이고, 코드는 step 1 부터 이 문서를 읽고 만든다.
- 개념 절과 다른 필드명·값·오류 코드를 문서에 쓰지 마라. 이유: 뒤 step 10개가 이 문서를 시그니처처럼 읽는다.
- `docs/PRD.md`·`docs/UI_GUIDE.md`·`docs/DEPLOY.md`·`docs/CURRENT_HANDOFF.md` 를 고치지 마라. 이유: step 9 가 구현 결과에 맞춰 한꺼번에 맞춘다.
- ADR-0009 등 기존 ADR 본문을 고치지 마라. 이유: ADR 은 사용자 확정 기록이며 이번 결정은 0010 에 새로 적는다.
- 기존 테스트를 깨뜨리지 마라.
