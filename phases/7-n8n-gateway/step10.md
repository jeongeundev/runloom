# Step 10: n8n-live-check

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/AGENTS.md`
- `/docs/adr/0010-n8n-inbox-and-callback.md`, `/docs/adr/0008-public-demo-scripted-agents.md`
- `/docs/n8n/README.md`·`/docs/n8n/runloom-handoff.json` (step 7 — 이 절차서를 그대로 따른다. 절차서가 틀리면 절차서를 고친다)
- `/docs/VERIFICATION_LOG.md` 전부 — 2026-09-22 "실제 Claude" 절의 기록 형식(무엇이 실제·무엇이 대본, 시각, 소요, 발견한 결함)
- `/docs/ARCHITECTURE.md` "검증 순서와 다음 결정" 표 7번 행, `/docs/CURRENT_HANDOFF.md` "지금 상태" 표
- `/scripts/local_stack.py` (`--callback-hosts`·`--public-url`, step 8), `/scripts/seed_demo.py`(카탈로그 에이전트 ID), `/tests/e2e/test_scenario.py` 의 n8n 절(어떤 HTTP 순서로 세션·등록·토큰을 만들었는지 — 같은 순서를 curl 로 한다)
- `/src/workflow/server/inbound_api.py`, `/src/workflow/server/worker.py` 의 `_deliver_callbacks`, `/src/workflow/adapters/callback_client.py`

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

**검증만 한다.** 제품 코드를 고치지 않는다(결함을 찾으면 기록하고 멈춘다 — 사용자가 다음 조치를 정한다). 실제 n8n(Docker)이 Runloom 을 부르고, Runloom 의 callback 이 실제 n8n Wait 노드를 깨우는 것을 1회 확인해 기록한다. 에이전트는 대본(`--scripted`)이다.

### 절차

1. **전제 확인**: `docker version`(데몬 응답), `docker pull docker.n8n.io/n8nio/n8n`(실패하면 blocked — 네트워크·이미지 이름을 `blocked_reason` 에), `python3 -m pytest -q` 통과.
2. **n8n 기동**: `docker run -d --name runloom-n8n -p 5678:5678 -v runloom_n8n_data:/home/node/.n8n docker.n8n.io/n8nio/n8n` → `docker exec runloom-n8n n8n --version` 기록 → `curl -sf http://localhost:5678/healthz` 가 200 이 될 때까지 대기(최대 60초).
3. **Runloom 로컬 스택**: 작업 디렉터리는 스크래치패드(또는 `data/live-n8n`). `python3 scripts/local_stack.py --scripted --workdir <dir> --callback-hosts localhost:5678 --public-url http://127.0.0.1:18000` 을 백그라운드로 띄우고 `logs/` 를 확인.
4. **세션·등록·토큰 (curl, 쿠키 jar)**: `GET /` 로 세션 → e2e 와 같은 순서로 카탈로그 에이전트 등록(`POST /agents/register`, ops·codex 순 — 수정 업무의 자동 선택이 한 후보로 정해지게) → `POST /sources/tokens` → 응답 HTML 에서 `wfs_…` 추출. 토큰은 셸 변수에만 두고 출력·로그·파일에 남기지 않는다(자격 증명 JSON 은 만들어 `docker cp` 한 뒤 즉시 삭제).
5. **n8n 에 워크플로우·자격 증명 넣기 (CLI 경로)**: `docs/n8n/runloom-handoff.json` 을 복사해 URL 포트를 18000 으로 바꾼 사본을 만든다(`sed`) → 자격 증명 JSON(README 의 decrypted 형식, `id` 는 워크플로우 JSON 의 `credentials.httpHeaderAuth.id` 와 같게) → `docker cp` 두 파일 → `docker exec runloom-n8n n8n import:credentials --input=/tmp/creds.json` → `docker exec runloom-n8n n8n import:workflow --input=/tmp/wf.json` → `docker exec runloom-n8n n8n list:workflow` 로 ID → `docker exec runloom-n8n n8n publish:workflow --id=<ID>`(없으면 `update:workflow --id=<ID> --active=true`) → `docker restart runloom-n8n` → healthz 대기. 어느 명령이 그 버전에 없거나 소유자 계정 설정을 요구하면 **blocked**: `blocked_reason` 에 n8n 버전·실패한 명령·출력 요지와 "n8n 화면(http://localhost:5678)에서 소유자 계정 생성 → Import from File → Header Auth 자격 증명 → Publish 를 사용자가 한 뒤 이 step 을 pending 으로 되돌린다" 를 적는다.
6. **트리거**: `curl -X POST http://localhost:5678/webhook/runloom-demo -H 'content-type: application/json' -d '{"run_id":"daily-0920-0900"}'` → 200. `docker logs runloom-n8n --since 1m` 에서 실행 시작 확인.
7. **Runloom 쪽 확인**: `sqlite3 <dir>/central/db.sqlite "SELECT chain_id, source, callback_attempts, callback_sent_at, callback_last_error FROM chains"` 로 `source='n8n'` 체인 생성 확인, `GET /chains/<id>` 로 A→B 진행(대본이라 1~2분). B 가 `확인 필요 · 검토 대기` 가 된 뒤 3~10초 안에 `callback_sent_at` 이 찬다. **2xx 로 기록됐다는 것이 n8n Wait 노드가 실제로 깨어났다는 증거다**(대기 중이 아닌 URL 은 n8n 이 404 를 준다). 중앙 워커 로그의 `tick` 줄(`callbacks_sent`)과 n8n 로그의 해당 실행 줄을 함께 확보한다. 가능하면 n8n 화면의 실행 목록(Executions)에서 Wait 뒤 노드가 실행됐는지 스크린샷 없이 텍스트로 기록(화면 로그인은 요구하지 않는다 — 안 되면 로그만).
8. **정리**: 로컬 스택 종료, `docker rm -f runloom-n8n`, `docker volume rm runloom_n8n_data`, 자격 증명 사본 삭제. `data/` 밑에 만들었으면 지운다(gitignore 라도).
9. **기록**: `docs/VERIFICATION_LOG.md` 에 `## 2026-09-22 — 실제 n8n 으로 입구·출구 1회 (phase 7 step 10)` 절: n8n 버전·Docker 버전, 무엇이 실제(n8n·HTTP·Wait 재개)이고 무엇이 대본(에이전트·진단)인지, 시각·소요, 요청·응답 요지(토큰 제외), `callback_sent_at`, 발견한 결함·절차서 오류(고친 README 줄). `docs/ARCHITECTURE.md` "검증 순서" 7번 행을 "실제 n8n 1회 통과" 로, `docs/CURRENT_HANDOFF.md` "지금 상태" 표의 phase 7 행에 결과 한 줄. 실패했으면 그대로 실패로 적는다 — 통과한 것처럼 쓰지 않는다.

## Acceptance Criteria

```bash
grep -n "실제 n8n" docs/VERIFICATION_LOG.md          # 새 절 1건 이상
docker ps -a --filter name=runloom-n8n --format '{{.Names}}' | wc -l    # 0 — 정리됨
git diff --stat HEAD -- src tests                     # 빈 출력 — 제품 코드 0줄 (README·문서 diff 만)
python3 -m pytest -q
python3 -m ruff check .
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - 토큰 원문이 로그·커밋·VERIFICATION_LOG 에 없는가? (`git diff` 와 `grep -rn "wfs_[A-Za-z0-9_-]\{20,\}" docs phases` 가 0건)
   - 실제 모델·실제 codex/claude 를 부르지 않았는가? (`--scripted` 스택, `OPENAI_API_KEY` 없음)
   - 기록이 실제와 대본을 구분하는가?
   - 공개 데모 VM 을 건드리지 않았는가?
3. 결과에 따라 `phases/7-n8n-gateway/index.json` 의 해당 step 을 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"` (n8n 버전·소요·`callback_sent_at`·고친 절차서 줄을 적는다)
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message": "구체적 에러 내용"` — 제품 결함이면 여기 적고 코드는 고치지 않는다
   - 사용자 개입 필요(Docker 데몬·이미지·n8n 화면 조작) → `"status": "blocked"`, `"blocked_reason": "구체적 사유 + 사용자가 할 일"` 후 즉시 중단

## 금지사항

- 제품 코드(`src/`)를 고치지 마라. 이유: 검증 step 이다. 결함은 기록하고 사용자가 정한다. 절차서(`docs/n8n/README.md`)의 오류만 고친다.
- `--dangerously…` 류의 n8n 보안 설정을 끄거나(`N8N_SECURE_COOKIE` 등) 외부 포트를 여는 설정을 더하지 마라. 이유: 로컬 확인에 필요 없다. 헬스체크·CLI 로 충분하지 않으면 blocked 로 사용자에게 넘긴다.
- 토큰·자격 증명 파일을 저장소 안이나 스크래치패드 밖에 남기지 마라. 이유: AGENTS.md 비밀값 규칙.
- 대본 대신 실제 codex/claude/모델을 켜지 마라. 이유: 이 step 의 목적은 n8n 연동이며 사용량·비용을 쓰지 않는다.
- 통과하지 못한 것을 통과한 것처럼 기록하지 마라.
- 기존 테스트를 깨뜨리지 마라.
