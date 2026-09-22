# Step 2: domain-settlement

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/AGENTS.md`
- `/docs/adr/0010-n8n-inbox-and-callback.md`, `/docs/adr/0004-central-service-rule-based-no-llm.md`
- `/docs/GLOSSARY.md` (`chain_settled`·`WORKFLOW_CALLBACK_HOSTS` 행)
- `/docs/ARCHITECTURE.md` "n8n 입구와 출구" 절과 "상태·재접속·완료" 절
- `/docs/PRD.md` 3절 (사용자 상태 대응표)
- `/src/workflow/domain/status.py` — `USER_STATUS_LABELS`, `user_status` 의 각 상태가 언제 나오는지(특히 `대기` 의 네 가지 이유: 선행 대기·연결 끊김·자동 실행 대기, 그리고 워커가 쓰는 "후속 규칙 없음")
- `/src/workflow/domain/succession.py`, `/src/workflow/domain/kinds.py` — 도메인 모듈의 작성 방식(순수 함수, 등록부는 인자, docstring 한국어)
- `/src/workflow/server/views.py` 의 `_LIVE_LABELS` 주석 — 같은 관찰을 도메인 규칙으로 옮긴다
- `/src/workflow/server/worker.py` 의 `_spawn_successors` — `대기` 로 남는 경우와 `확인 필요` 로 바뀌는 경우
- `/tests/workflow/domain/test_status.py`, `/tests/workflow/domain/test_succession.py` — 테스트 작성 방식
- step 1 산출물: `/src/workflow/contracts/v1.py` 의 새 모델(이 step 은 쓰지 않지만 이름을 맞춘다)

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

TDD: `tests/workflow/domain/test_settlement.py`·`tests/workflow/domain/test_callback_policy.py` 를 먼저 쓰고 실패를 확인한 뒤 구현한다. 도메인 층만 만든다 — DB·HTTP·시각을 보지 않는다.

### 1. `src/workflow/domain/settlement.py` (신규)

```python
"""체인이 "사람 차례"인가 — callback 시점 판정 (ADR-0010). 시각·DB·HTTP 를 보지 않는다."""

LIVE_LABELS = ("실행 요청됨", "실행 중")          # 워커·실행 주체가 진행 중
BLOCKED_BY_HUMAN = ("확인 필요", "실패")          # 이 상태의 선행은 사람이 봐야 풀린다

@dataclass(frozen=True)
class NodeState:
    status: str                    # USER_STATUS_LABELS 중 하나
    predecessor_status: str | None # 선행 Task 의 사용자 상태. 선행이 없으면 None

def chain_settled(nodes: Sequence[NodeState]) -> bool:
    """(a) LIVE_LABELS 인 업무가 없고 (b) `대기` 인 업무는 모두 predecessor_status ∈ BLOCKED_BY_HUMAN 이면 참. 빈 목록은 거짓."""
```

규칙의 뜻(docstring 에 적는다): `대기` 의 선행이 `확인 필요`/`실패` 면 워커가 더 할 수 없다(판정 불가·규칙 밖 outcome·실패한 선행은 사람이 본다 — `_spawn_successors` 가 그렇게 둔다). 선행이 `완료`·`실행 중`·`대기`·None 인 `대기` 는 자동 실행 대기·연결 끊김·선행 진행 중이라 워커 몫이다. `실행 가능`·`확인 필요`·`완료`·`실패` 는 사람 조작 전엔 바뀌지 않는다.

### 2. `src/workflow/domain/callback_policy.py` (신규)

```python
"""callback 대상 허용 목록 — `WORKFLOW_CALLBACK_HOSTS` 의 판정 (ADR-0010). 표준 urllib.parse 만 쓴다."""

def parse_hosts(raw: str) -> tuple[str, ...]:
    """콤마 구분 문자열 → 항목 튜플. 공백 제거, 빈 항목 제거, 소문자화. 빈 문자열 → 빈 튜플."""

def host_allowed(url: str, allowed: Sequence[str]) -> bool:
    """`url` 의 스킴이 http/https 이고 host(소문자)가 `allowed` 의 어느 항목과 맞으면 참.
    항목이 `host` 면 그 호스트의 모든 포트, `host:port` 면 포트까지 같아야 한다(스킴 기본 포트 80/443 을 채워 비교).
    `allowed` 가 비어 있으면 항상 거짓. userinfo(`user@host`)·빈 host·파싱 실패 → 거짓."""
```

### 3. 테스트

`test_settlement.py` 는 `pytest.mark.parametrize` 로 아래 표를 그대로 검증한다(각 행은 `(status, predecessor_status)` 튜플의 목록):

| 장면 | nodes | 기대 |
|---|---|---|
| 빈 체인 | `[]` | False |
| 첫 업무 후보 없음 | `[("확인 필요", None)]` | True |
| A 완료 → B 검토 대기 (주 경로) | `[("완료", None), ("확인 필요", "완료")]` | True |
| A 완료 → B 실행 중 | `[("완료", None), ("실행 중", "완료")]` | False |
| A 완료 → B 실행 요청됨 | `[("완료", None), ("실행 요청됨", "완료")]` | False |
| A 판정 불가 → B 선행 대기 | `[("확인 필요", None), ("대기", "확인 필요")]` | True |
| A 실패 → B 선행 대기 | `[("실패", None), ("대기", "실패")]` | True |
| A 완료 → B 자동 실행 대기·연결 끊김 | `[("완료", None), ("대기", "완료")]` | False |
| A 실행 중 → B 선행 대기 | `[("실행 중", None), ("대기", "실행 중")]` | False |
| 시작 전 (실행 가능) | `[("실행 가능", None)]` | True |
| 단독 진단 완료 | `[("완료", None)]` | True |
| 첫 업무 대기 (연결 끊김) | `[("대기", None)]` | False |
| A→B→C, C 실행 중 | `[("완료", None), ("확인 필요", "완료"), ("실행 중", "확인 필요")]` | False |
| A→B→C, C 검토 대기 | `[("완료", None), ("확인 필요", "완료"), ("확인 필요", "확인 필요")]` | True |

`test_callback_policy.py`: `parse_hosts("")→()`, `parse_hosts(" localhost:5678, 127.0.0.1 ")→("localhost:5678","127.0.0.1")`, `host_allowed("http://localhost:5678/webhook-waiting/12", ("localhost:5678",))→True`, 포트 다름 → False, `("localhost",)` 이면 어느 포트든 True, `https://example.com/x` 에 `("example.com:443",)`→True, 빈 allowed → False, `ftp://localhost:5678/` → False, `http://user@localhost:5678/` → False, 대소문자(`LOCALHOST`) → True, `http://127.0.0.1:8100/runs` 에 `("localhost:5678",)` → False.

## Acceptance Criteria

```bash
python3 -m pytest tests/workflow/domain/test_settlement.py tests/workflow/domain/test_callback_policy.py -q
python3 -m pytest -q
python3 -m ruff check .
grep -n "^import\|^from" src/workflow/domain/settlement.py src/workflow/domain/callback_policy.py   # fastapi·sqlite3·httpx·subprocess 가 없어야 한다
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - `domain/` 이 FastAPI·sqlite3·HTTPX·subprocess·Git·`server/`·`adapters/` 를 import 하지 않는가? (AGENTS.md CRITICAL)
   - 상태 문구가 `USER_STATUS_LABELS` 의 것과 글자까지 같은가? (`실행 요청됨`, `실행 중`, `확인 필요`, `실패`, `대기`)
   - GLOSSARY 의 이름(`chain_settled`, `host_allowed`, `parse_hosts`, `NodeState`)과 같은가?
   - 테스트 파일 위치가 `tests/workflow/domain/` 미러 배치인가?
3. 결과에 따라 `phases/7-n8n-gateway/index.json` 의 해당 step 을 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"` (모듈 경로·함수 시그니처를 적는다)
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- `status.py`·`succession.py`·`composition.py` 를 고치지 마라. 이유: 기존 상태·후속 규칙은 그대로이고 이 step 은 그 위의 관찰 함수만 더한다.
- `NodeState` 에 이유 문자열(`status_reason`)을 넣고 그것을 파싱해 판정하지 마라. 이유: 이유 문구는 화면용이며 바뀔 수 있다. 판정은 상태 라벨과 선행 상태로만 한다.
- `host_allowed` 에서 DNS 조회나 IP 대역 검사를 하지 마라. 이유: 허용 목록은 명시 비교다(ADR-0004 의 태도). 네트워크를 건드리는 판정은 도메인에 두지 않는다.
- 기존 테스트를 깨뜨리지 마라.
