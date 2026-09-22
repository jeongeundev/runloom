# ADR-0010: n8n 은 업무가 들어오는 입구와 나가는 출구다 — 판단은 Runloom 이 한다

결정일: 2026-09-22. 사용자 확정. 적용 범위는 제품(`src/workflow/`)의 셀프호스트 실사용이며, 공개 데모(VM, [ADR-0008](0008-public-demo-scripted-agents.md))는 심사 기간 동안 건드리지 않는다. [ADR-0009](0009-registered-kinds-and-succession-rules.md) 참고 절의 "n8n 은 업무가 들어오는 입구·나가는 출구로만 쓴다"를 구체화한 것이다. 이 제품은 "n8n 옆의 에이전트 인계 계층"이며 n8n 을 대체하지 않는다.

장면: n8n 쪽은 노드 4개다 — Webhook(또는 Error Trigger) → HTTP Request(Runloom 에 POST) → Wait(On Webhook Call — `$execution.resumeUrl` 로 깨어남) → Slack. Runloom 은 그 사이에서 종류·규칙·판정·인계·사람 게이트를 맡는다.

**결정**:

1. n8n 은 `TaskSource` 하나(`n8n`)다. `chains.source` 값 `'n8n'`, `domain/task_sources.Source` 에 `"n8n"`. 입구 본문의 항목(`InboundItem`)은 GitHub·Jira fixture 의 `Issue` 와 같은 모양(`key`·`title`·`body`·`labels`·`blocked_by`, `url` 은 None)이고 라벨 규칙(`incident`+`workflow:<id>`+`run:<run_id>`, `bug`+`repo:<id>`, `kind:<kind>`+`<scope_key>:<value>`)도 그대로다 — `map_issue`·`compose` 를 재사용하며 매핑·구성·워커 후속 코드에 n8n 분기를 두지 않는다. fixture 출처 목록 `adapters/task_sources.SOURCES`(`github`·`jira`)는 그대로다 — n8n 은 파일이 없으므로 가져오기 화면(`/tasks/import`)에 나오지 않고 입구 API 로만 들어온다.
2. 입구 인증은 워크스페이스(세션)가 `/sources` 화면에서 발급하는 입구 토큰(`source token`)이다. 원문은 `wfs_` + `secrets.token_urlsafe(32)`, 발급 응답에서 한 번만 보이고 서버에는 sha256 만 남는다(연결 토큰 `wfc_` 와 같은 방식, `repo.exchange_connect_code` 참고). 취소하면 다음 요청부터 401. `Authorization: Bearer wfs_…` → `session_id` + `source`. 팀 전제(ADR-0009)라 워크스페이스별로 구분한다.
3. 입구 API `POST /sources/n8n/chains`(JSON 본문 `InboundChainRequest`, 응답 201 `InboundChainResponse`)는 항목으로 체인 + Task 들을 가져오기(`/tasks/import`)와 같은 규칙으로 만들고, **접수 즉시 첫 업무 시작을 시도한다** — n8n 트리거가 곧 사람의 "워크플로우 시작" 조작이다. 첫 업무가 후보 없음(409 `selection_required`)·상한(429 `daily_limit_reached` — 진단 상한)·조건 미충족(409)이면 체인은 남기고 `started=false` + `start_error`(오류 본문)로 201 을 돌려준다. 세션에 등록된 에이전트가 없으면 422 `agent_not_registered`, 세션 활성 업무 상한은 가져오기와 같이 체인을 만들기 전에 검사해 429 `active_task_limit_reached`(체인 없음). 토큰 없음·취소는 401 `unauthenticated`, 토큰의 `source` 가 경로와 다르면 403 `forbidden`.
4. 출구는 callback 이다. 체인이 **사람 차례**(`chain_settled`)가 되면 워커가 `callback_url` 로 `ChainCallback` 을 **체인당 1회** POST 한다(n8n Wait 노드는 한 번만 깨어난다). `chain_settled(nodes)` 는 도메인 순수 함수(`domain/settlement.py`)다: (a) 어떤 업무도 `실행 요청됨`·`실행 중` 이 아니고, (b) `대기` 인 업무는 모두 선행 업무의 상태가 `확인 필요` 또는 `실패` 이면(= 사람에게 막힘) 참. 그 밖의 `대기`(자동 실행 대기·연결 끊김·선행 진행 중)와 실행 중은 아직 워커 몫이라 거짓. 빈 목록은 거짓. 남는 상태(`확인 필요`·`완료`·`실패`·`실행 가능`)는 사람 조작 전엔 바뀌지 않는다 — 화면 폴링 규칙(`views._LIVE_LABELS`)과 같은 관찰이다. 워커 tick 의 **마지막 단계**(후속 스캔·실패 반영 뒤)에서 판정하므로, A 판정 → B 착수가 같은 tick 에 일어나면 그 사이에 보내지 않는다. 전송은 트랜잭션 밖 HTTPX POST(10초). 2xx 면 `callback_sent_at`, 아니면 `callback_attempts`+1 과 `callback_next_at = now + 30·2^(n-1)초`(30·60·120·240초), 5회 실패 후 중단(`callback_last_error` 를 화면에). 사람이 그 뒤 승인·종료해도 다시 보내지 않는다. `callback_url` 은 선택이며, 직접 등록·가져오기 화면으로 만든 체인은 `callback_url` 이 없으므로 아무것도 보내지 않는다.
5. callback 대상은 허용 목록 `WORKFLOW_CALLBACK_HOSTS` 안에서만이다. 콤마 구분 `host` 또는 `host:port`(예 `localhost:5678,127.0.0.1`), `host` 만 쓰면 그 호스트의 모든 포트. 비어 있으면 callback 을 받지 않는다 — `callback_url` 이 있는 접수는 422 `callback_host_not_allowed`. `callback_url` 은 http/https 만. 판정 함수는 `domain/callback_policy.py` 의 `host_allowed(url, allowed)`·`parse_hosts(raw)` 다. `WORKFLOW_PUBLIC_URL`(선택, 예 `http://127.0.0.1:8000`, 끝 `/` 없음)은 응답·callback 의 `chain_url`·`task_url` 앞에 붙고, 비면 두 필드는 null. 둘 다 비밀값이 아니다.
6. 중앙은 여전히 LLM 을 부르지 않는다([ADR-0004](0004-central-service-rule-based-no-llm.md) 유지). 등록부 → n8n JSON 컴파일, LLM 워크플로우 생성, 업무마다 callback, 완료 시 새 업무 생성 규칙은 하지 않는다.

**이유**: 사용자의 원래 문제는 착수 대기이고, n8n 은 회사에 이미 있는 트리거·알림 도구다. n8n 이 트리거·알림을, Runloom 이 에이전트 단계(계약 판정·근거 인계·사람 게이트)를 맡으면 서로를 대체하지 않는다. 흐름을 n8n 노드로 다시 그리면(등록부 → n8n JSON) 판단이 미리 그린 정적 배치로 넘어가 ADR-0009 의 차별점("안 그려도 흐른다 + 넘어갈 때 검증한다")이 사라진다. 입구 본문을 `Issue` 와 같은 모양으로 두는 이유: 매핑·구성 경로가 하나면 가져오기 화면과 입구 API 가 같은 규칙·같은 이유 문장을 내고, 테스트도 하나다. callback 을 "사람 차례" 1회로 두는 이유: n8n Wait 노드는 한 번만 깨어나고, 사람이 필요한 순간이 곧 알림이 필요한 순간이다("검토해 주세요"). 마지막 업무 `완료` 만 기다리면 알림이 사람 승인 뒤에 와서 쓸모가 없다. 허용 목록 이유: 공개 데모 VM 은 누구나 세션을 만들 수 있으므로 외부가 준 주소로 서버가 POST 하게 두면 내부 주소(`127.0.0.1:8100` 등)를 찌를 수 있다. 셀프호스트는 `localhost:5678` 한 줄이면 된다.

**트레이드오프**:
- 업무마다 알림은 없다. 업무별 진행이 필요하면 n8n 쪽에서 체인 화면(`chain_url`)을 폴링하거나 다음 phase 다.
- `대기 · 연결 끊김` 은 선행이 `확인 필요` 면 사람 차례로 본다(판정은 선행의 상태만 본다). 연결이 돌아와도 다시 보내지 않는다.
- 진단 API 후속(API 에이전트가 두 번째 업무)은 워커가 시작하지 않으므로 `대기` 에 머물러 callback 이 안 온다 — ADR-0009 한계 그대로.
- 입구 본문이 라벨 규칙이라 n8n 쪽 표현식에 라벨을 써야 한다. `kind`·`scope` 필드를 따로 두는 형식은 만들지 않았다 — 매핑 경로를 하나로 유지.
- 스키마 `SCHEMA_VERSION` 3 → 4, 마이그레이션 없음(`WORKFLOW_RESET_DB=1` 재생성). 공개 데모 VM 은 손대지 않는다.

**참고**: n8n AI Workflow Builder(LLM 이 워크플로우 JSON 생성)와 겹치지 않는다 — 이 제품은 워크플로우를 만들지 않는다.
