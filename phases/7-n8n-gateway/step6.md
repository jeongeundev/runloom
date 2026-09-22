# Step 6: web-sources-page

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/AGENTS.md`
- `/docs/adr/0010-n8n-inbox-and-callback.md`, `/docs/adr/0005-access-model-anonymous-session-operator-token.md`
- `/docs/ARCHITECTURE.md` "n8n 입구와 출구" 절 (화면), "인증·권한·비밀정보 규칙"
- `/docs/UI_GUIDE.md` 전부 — 앱 셸·목록·폼·상태 점·문구 규칙 (새 페이지는 이 규칙을 따른다)
- `/docs/GLOSSARY.md` (`source token`·`Chain`·`TaskSource`·`outcome 라벨`)
- `/src/workflow/server/web.py` — `kinds_page`·`kinds_create`·`rules_create`·`kind_delete`(등록 폼·삭제 폼·PageError·303 패턴), `operator` 절의 연결 코드 발급·취소(`/operator/connect-codes`)와 그 템플릿 `operator.html`(발급값을 한 번 보여주는 방식), `chain_detail`·`_chain_context`, `_render`
- `/src/workflow/server/views.py` — `chain_summary`·`_composition_reasons`·`_chain_node`, `agent_public`
- `/src/workflow/server/templates/kinds.html`, `chain_detail.html`, `_sidebar.html`, `base.html`, `operator.html`, `/src/workflow/server/static/style.css`
- `/src/workflow/adapters/task_sources.py` (`SOURCE_LABELS`), `/src/workflow/adapters/repo.py` 의 `issue_source_token`·`revoke_source_token`·`list_source_tokens` (step 3)
- `/src/workflow/server/settings.py` 의 `public_url` (step 4), `/src/workflow/server/inbound_api.py` (step 4 — 경로 문자열을 화면에 그대로 쓴다)
- `/tests/workflow/server/test_web.py`(kinds·operator 절), `test_views.py`, `test_ui.py` — 화면 테스트 방식(HTML 문자열 검사)

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

TDD: `tests/workflow/server/test_web.py`·`test_views.py`·`test_ui.py` 에 테스트를 먼저 쓰고 실패를 확인한 뒤 구현한다. 웹 층(라우트·뷰·템플릿·CSS)만 바꾼다.

### 1. `/sources` 페이지 — `src/workflow/server/web.py` + `templates/sources.html` (신규)

- `GET /sources` (세션 필요, `require_session`): 제목 "입구". 내용 순서:
  1. 안내 한 문단 — "n8n 같은 외부 도구가 이 워크스페이스에 업무를 넣는 곳. 토큰을 발급해 HTTP 요청의 `Authorization: Bearer` 에 넣는다." 
  2. 입구 주소: `{public_url 또는 request.base_url 의 스킴+호스트}/sources/n8n/chains` 를 `<code>` 로.
  3. 토큰 표: `token_id`·`label`·발급 시각·마지막 사용·상태(활성/취소)·취소 버튼(활성만, `POST /sources/tokens/{token_id}/revoke`). 없으면 "아직 발급한 토큰이 없습니다."
  4. 발급 폼: `label`(선택) → `POST /sources/tokens`.
  5. 요청 예시 `<pre>`: `curl -X POST … -H 'Authorization: Bearer <토큰>' -H 'content-type: application/json' -d '{ … CONTRACT 12절 (a) 와 같은 본문 … }'` — 토큰 자리는 `wfs_…` 플레이스홀더. `docs/n8n/README.md` 링크 한 줄(step 7 이 만든다 — 파일이 아직 없어도 링크 문구만).
  6. 허용 목록 상태 한 줄: `settings.callback_hosts` 가 비면 "callback 허용 목록이 비어 있어 `callback_url` 은 거부됩니다 (`WORKFLOW_CALLBACK_HOSTS`)", 있으면 항목 나열.
- `POST /sources/tokens`: `label` Form(기본 ""). `repo.issue_source_token(conn, session_id, "n8n", label, now)` → **303 하지 않고** 같은 페이지를 200 으로 렌더하며 `issued_token`(원문)을 한 번 보여준다("이 값은 다시 볼 수 없습니다"). 세션당 활성 토큰 상한 5개(초과 422 `invalid_field`, 문구 "활성 토큰은 5개까지입니다. 하나를 취소하세요.").
- `POST /sources/tokens/{token_id}/revoke`: 같은 세션만(다른 세션·없음 → 404). 303 `/sources`.
- 사이드바 `_sidebar.html` 의 nav 에 `종류·규칙` 다음 `<a href="/sources">입구</a>`(활성 표시 규칙 동일).

### 2. 체인 화면 — `views.chain_summary` + `templates/chain_detail.html`

- `adapters/task_sources.SOURCE_LABELS["n8n"] = "n8n"` 추가(`SOURCES` 는 그대로).
- `chain_summary` 반환에 `callback` 키 추가: `None`(callback_url 없음) 또는 `{"url": str, "state": "대기" | "전송됨" | "실패", "sent_at": str|None, "attempts": int, "last_error": str|None}` — `state` 는 `sent_at` 있으면 전송됨, `attempts >= 5` 이고 미전송이면 실패, 아니면 대기.
- `chain_detail.html` 의 출처 표시(지금 `source_label` 을 쓰는 곳)에 그대로 `n8n` 이 보이게 하고, 그 옆 또는 아래에 callback 한 줄: "callback · 대기(사람 차례가 되면 보냄)" / "callback · 전송됨 {sent_at|ago}" / "callback · 실패 {attempts}회 · {last_error}". URL 은 host 만 보여준다(`urllib.parse.urlsplit(url).netloc`).
- `_composition_reasons`: `chain["source"] == "n8n"` 이면 `items_json` 을 `Issue(source="n8n", …, url=None)` 목록으로 만들어 같은 `compose` 로 이유를 다시 만든다(`items_json` 이 NULL 이면 `{}`). 기존 fixture 출처 분기는 그대로.
- 홈·목록의 체인 카드(있으면)도 `source_label` 을 쓰므로 자동으로 `n8n` 이 보인다 — 확인만.

### 3. 테스트

`test_web.py`: `GET /sources` 200·입구 주소·빈 상태 문구·사이드바 링크; 발급 POST → 200, 응답 HTML 에 `wfs_` 원문 1회, 이후 `GET /sources` 에는 원문 없음(`wfs_` 문자열이 플레이스홀더 외에 없음 — 플레이스홀더는 `wfs_…` 그대로 검색에서 제외); 6번째 발급 422; 취소 → 303, 표에 "취소"; 다른 세션의 토큰 취소 404; 취소된 토큰으로 입구 API 401(step 4 의 라우트 사용). `test_views.py`: `chain_summary(...)["callback"]` 세 상태; n8n 체인의 `_composition_reasons` 가 `items_json` 으로 이유를 만든다(라벨 매핑 문장 포함). `test_ui.py`: 체인 화면 HTML 에 `n8n` 출처와 callback 한 줄, `/sources` 가 UI_GUIDE 의 셸(사이드바·제목)을 쓴다.

## Acceptance Criteria

```bash
python3 -m pytest tests/workflow/server -q
python3 -m pytest -q
python3 -m ruff check .
grep -n "sources" src/workflow/server/templates/_sidebar.html     # 1건 이상
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - 토큰 원문이 발급 응답 1회 외에 어디에도 없는가? (목록·로그·DB — AGENTS.md CRITICAL)
   - 취소·목록이 세션 범위인가? (다른 세션 404, ADR-0005)
   - 화면 문구가 UI_GUIDE 규칙과 GLOSSARY 용어를 따르는가? (`입구`·`토큰`·`callback` — `webhook secret`·`API key` 같은 금지 표현 없음)
   - n8n 비판 문구가 없는가?
   - CDN·외부 스크립트를 끌어오지 않는가? (`style.css` 만)
3. 결과에 따라 `phases/7-n8n-gateway/index.json` 의 해당 step 을 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"` (경로·템플릿 이름·`chain_summary["callback"]` 형태를 적는다)
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- 발급한 토큰을 세션 쿠키·flash·쿼리 문자열로 넘겨 다음 페이지에서 보여주지 마라. 이유: 원문이 로그·히스토리에 남는다. POST 응답을 바로 렌더한다.
- 운영자 화면(`/operator`)에 토큰 발급을 두지 마라. 이유: 입구 토큰은 워크스페이스(세션) 소유다(ADR-0010 결정 2). 운영자는 연결 코드만.
- 체인 화면에 callback 본문 전체나 URL 전체를 찍지 마라. 이유: n8n 내부 주소·실행 ID 는 화면에 필요 없다. host 와 상태만.
- `SOURCES`(fixture 목록)에 `n8n` 을 넣지 마라. 이유: 가져오기 화면이 fixture 파일을 찾는다.
- 워커·계약·DB 코드를 건드리지 마라. 이유: step 3~5 에서 끝났다.
- 기존 테스트를 깨뜨리지 마라.
