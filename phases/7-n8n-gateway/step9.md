# Step 9: docs-sync

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/AGENTS.md`
- `/docs/adr/0010-n8n-inbox-and-callback.md` (정본 — 구현이 여기서 벗어났으면 문서가 아니라 `summary` 에 적고 사용자 확정 대상으로 남긴다)
- `/docs/CURRENT_HANDOFF.md` 전부 — "지금 상태" 표·"재개 방법"·"다음 세션에서 할 일" 형식
- `/docs/GLOSSARY.md`, `/docs/ARCHITECTURE.md`, `/docs/CONTRACT.md` (step 0 이 쓴 절), `/docs/UI_GUIDE.md`, `/docs/DEPLOY.md` 3절(환경변수)·10절(한계), `/docs/README.md`, `/docs/PRD.md` 의 n8n 언급 줄, `/docs/VERIFICATION_LOG.md`
- `/phases/7-n8n-gateway/index.json` 의 step 0~8 `summary` — 실제로 만들어진 이름·경로
- 구현 전부: `/src/workflow/contracts/v1.py`(새 모델), `/src/workflow/domain/settlement.py`·`callback_policy.py`, `/src/workflow/adapters/db.py`·`repo.py`(새 테이블·함수)·`callback_client.py`·`task_sources.py`, `/src/workflow/server/inbound_api.py`·`auth.py`·`web.py`(`create_chain`·`start_chain`·`/sources`)·`worker.py`(`_deliver_callbacks`)·`views.py`·`settings.py`·`templates/sources.html`·`chain_detail.html`, `/scripts/local_stack.py`, `/deploy/env/central.env.example`, `/docs/n8n/`
- `/phases/6-typed-handoff/step9.md` 와 그 `summary` — 이전 phase 의 문서 동기화 방식

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

문서만 구현에 맞춘다. 코드·테스트 0줄. 각 문서에서 실제 코드와 다른 문장을 찾아 고치고, 없는 것은 더한다. "2026-09-22 확정" 표시는 유지한다.

1. **`docs/GLOSSARY.md`**: step 0 이 넣은 행을 실제 이름·시그니처로 검증(`chain_settled(nodes)`·`NodeState`·`host_allowed`·`parse_hosts`·`create_chain`·`start_chain`·`ChainCreated`·`CallbackClient`·`HttpCallbackClient`·`CallbackFailed`·`_deliver_callbacks`·`CALLBACK_MAX_ATTEMPTS`·`chains_awaiting_callback`·`record_callback_attempt`·`issue_source_token`·`require_source_token`). `TickReport` 행에 `callbacks_sent`·`callbacks_failed`. `TaskSource` 행에 `n8n`. `LocalStack` 행에 `callback_hosts`·`public_url`. 갱신일을 2026-09-22 로.
2. **`docs/ARCHITECTURE.md`**: "n8n 입구와 출구" 절의 경로·컬럼·재시도 수치·설정 키를 코드와 대조. "검증 순서와 다음 결정" 7번 행을 step 8 결과(대본 e2e 통과, 실제 n8n 은 step 10 뒤 갱신)로. "배포와 실행 예산" 의 환경변수 표(있으면)에 두 키.
3. **`docs/CONTRACT.md`** 12절: 실제 응답 JSON(테스트에서 `model_dump(mode="json")` 한 값)으로 예시를 검증. 오류 코드 목록이 `inbound_api.py` 와 같은지.
4. **`docs/UI_GUIDE.md`**: `/sources` 페이지 절(구성·문구·1회 표시 규칙), 사이드바 항목 "입구", 체인 화면의 callback 한 줄.
5. **`docs/DEPLOY.md`**: 3절 환경변수 표에 `WORKFLOW_CALLBACK_HOSTS`(공개 데모는 비움)·`WORKFLOW_PUBLIC_URL`, 7b 에 "스키마 3 → 4 이므로 `WORKFLOW_RESET_DB=1`", 10절 한계에 "공개 데모는 callback 없음(허용 목록 비움)".
6. **`docs/PRD.md`**: n8n 언급 줄(191행 부근·472행 부근)을 "n8n 입구·출구는 phase 7 에서 구현(ADR-0010), 공개 데모 필수 아님" 으로 최소 수정. 다른 절은 손대지 않는다.
7. **`docs/README.md`**: `n8n/`·ADR-0010 등록 확인.
8. **`docs/CURRENT_HANDOFF.md`**: 갱신일·상태 줄, "지금 상태" 표에 `phases/7-n8n-gateway` 행(완료 step 범위·산출물 한 줄·"실제 n8n 미검증(step 10)"), "남은 것" 에서 n8n 입구 항목을 빼고 "실제 n8n 실연동(step 10)"·"업무별 알림"·"완료 시 새 업무 생성" 을 남긴다, "재개 방법" 에 `python3 scripts/execute.py 7-n8n-gateway --engine claude`(step 10 남음)와 로컬 n8n 명령 한 줄, "다음 세션에서 할 일" 2번을 완료 표시하고 step 10 을 적는다. 스키마 v4 배포 주의(심사 이후 `WORKFLOW_RESET_DB=1`).
9. **`docs/VERIFICATION_LOG.md`**: step 8 절이 있는지 확인만.

## Acceptance Criteria

```bash
git diff --stat HEAD -- src tests scripts deploy        # 빈 출력 — 코드 0줄
grep -n "7-n8n-gateway" docs/CURRENT_HANDOFF.md          # 1건 이상
grep -n "WORKFLOW_CALLBACK_HOSTS" docs/DEPLOY.md docs/ARCHITECTURE.md | wc -l    # 2 이상
grep -n "/sources" docs/UI_GUIDE.md                       # 1건 이상
python3 -m pytest -q
python3 -m ruff check .
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - 문서의 이름·경로·오류 코드·수치가 코드와 같은가? (`grep` 으로 대조한 것만 적는다)
   - GLOSSARY 금지 표현·n8n 비판 문구가 없는가?
   - ADR-0010 본문을 고치지 않았는가? (구현이 ADR 과 다르면 `summary` 에 적는다)
   - CURRENT_HANDOFF 의 "지금 상태" 표를 먼저 고쳤는가? (handoff 규칙)
3. 결과에 따라 `phases/7-n8n-gateway/index.json` 의 해당 step 을 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"` (고친 문서·절 이름을 적는다)
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- 코드·테스트·스크립트·배포 파일을 고치지 마라. 이유: 문서 동기화 step 이다. 결함을 찾으면 `summary` 에 적는다.
- ADR 파일을 고치지 마라. 이유: 사용자 확정 기록이다.
- `docs/archive/` 를 고치지 마라. 이유: 이력이다.
- 공개 데모 VM 배포 절차(DEPLOY 7b)를 "지금 실행하라" 로 쓰지 마라. 이유: 심사 기간(~2026-10-05) 동결.
- 기존 테스트를 깨뜨리지 마라.
