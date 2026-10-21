# Phase 24 — 사람용 JSON API: 업무 읽기·행동을 `/api/v1` 과 멤버 토큰으로 연다

작성일: 2026-10-21. 상태: 설계 완료, 실행 전. **`main` 에서 실행하고 `--pr` 로 `main` 행 PR 을 연다**([ADR-0030](../../docs/adr/0030-pr-ci-review-flow.md)). 근거: [ADR-0031](../../docs/adr/0031-handoff-understanding-and-clients.md) 결정 3(API 우선)·6(순서 ① 사람용 JSON API)·미결 "API 인증", [CURRENT_HANDOFF](../../docs/CURRENT_HANDOFF.md) "방향 갱신 — 2026-10-21".

## 왜

ADR-0031 은 Runloom 의 역할을 "이해한 상태로 이어받는 인계"로 넓히고 순서를 ① 사람용 JSON API → ② 인계 리포트 → ③ CLI → ④ IDE 앱으로 정했다. 지금 사람용 기능은 대부분 `server/web.py` 의 HTML 폼·303 응답뿐이다. 특히 **업무 목록·업무 상태·업무 행동(맡기기·우선순위·직접 작업·판단 제안 처리)에는 JSON 이 없다.** 이미 있는 사람용 JSON API(사람 요청·사내 요청·담당 범위 등)도 로그인 쿠키만 받아서, 터미널의 에이전트·스크립트·n8n 이 "내 이름으로" 부를 방법이 없다.

이 phase 가 끝나면 멤버가 `/me` 에서 토큰을 만들어 `curl -H "Authorization: Bearer wfm_…" /api/v1/work-items?filter=my_turn` 으로 내 차례 업무를 보고, `PUT /api/v1/work-items/RUN-12/assignee` 로 맡길 수 있다. 웹 화면은 그대로다 — 웹과 API 가 같은 함수를 쓴다.

## 결정

처리 표기: [사용자] 사용자 답·발언 · [근거] 문서·코드·관례에서 따라 나옴 · [조정값] 기본값, 조정 가능 · [N/A] 묻지 않음과 근거. 독립 검사 1(2026-10-21) 지적 반영 — D8·D14 처리 표기 정정, D10·D11·D14·D19·D24·D28·D35 보강. 독립 검사 2(실행 가능성) 지적 반영 — D11·D28·D33 수정, D36·D37 추가.


## 범위
D1. 이번 phase 는 업무 읽기(목록·한 건 상태) + 업무 행동(담당 정하기/해제=맡기기, 우선순위, 직접 작업 시작/그만, 판단 받기·다시 판단, 판단 제안 수락/무시, 다음 단계 제안 수락/무시)을 JSON API 로 연다. [사용자: "읽기 + 행동", 판단 받기 "넣음"]
D2. 새 업무 직접 등록(/tasks/new)은 범위 밖. [사용자: "읽기 + 행동" 선택 — "+ 새 업무 등록" 옵션을 고르지 않음]
D3. 인계 리포트·CLI·앱은 범위 밖 (ADR-0031 순서 ②③④). [근거: ADR-0031 결정 6]
D4. 웹 화면은 그대로 HTML 폼, 새 API 와 같은 `work_actions`·`views`/`work_list` 함수를 공유한다 — 화면마다 규칙을 따로 두지 않는다. [사용자: "웹은 그대로, 같은 함수 공유"]

## 주소 체계
D5. 모든 JSON API 는 `/api/v1` 아래, 복수 명사 리소스, 메서드 의미 GET 읽기 · PATCH 칸 바꾸기 · PUT/DELETE 붙이고 떼기 · POST 생성 · CRUD 밖 행동은 `POST …/{id}/accept`. [사용자: "실무에서 사용하는 형식", "둘다 사용"(v1 유지)]
D6. 업무는 업무 키 `RUN-12` 로 가리킨다(내부 work_item_id 아님). [사용자: "둘다 사용"(RUN-12 키)]
D7. 주소표(세부 철자는 step 문서에서 확정):
  GET /api/v1/work-items?filter=&repository=&closed= · GET /api/v1/work-items/RUN-12 · PATCH /api/v1/work-items/RUN-12 {priority}
  PUT/DELETE …/RUN-12/assignee · PUT/DELETE …/RUN-12/direct-work · POST …/RUN-12/triage-runs
  POST …/RUN-12/triage-proposals/{id}/accept|dismiss · POST …/RUN-12/next-step-proposals/{id}/accept|dismiss
  GET/POST …/RUN-12/internal-requests · /api/v1/human-requests… · /api/v1/internal-requests/{id}/… · /api/v1/responsibilities…
  GET/POST /api/v1/me/tokens · DELETE /api/v1/me/tokens/{id}
  /api/v1/github/sources… · /api/v1/field-mappings · GET /api/v1/metrics · /api/v1/metrics.csv · POST /api/v1/github/sources/{id}/baseline
  [사용자: v1·RUN-12 승인, 이름 바꾼 두 곳(/metrics.json → /api/v1/metrics, baseline → /api/v1/github/sources/{id}/baseline) "이대로 확정". 그 밖 철자는 조정값]
D8. 러너(`/connector/*`, `/executions/*`)와 n8n 입구(`/sources/n8n/chains`)도 `/api/v1` 아래로 옮긴다. [사용자: "같이 옮김"] 뒷부분은 그대로 두고 앞에 `/api/v1` 만 붙인다. [근거: 기계 프로토콜 이름 변경은 요청 밖 — 바꿀 이유 없음]
D9. 옛 주소는 바로 없앤다(별칭·리다이렉트 없음). 업그레이드 시 러너 재설치·n8n 워크플로우 주소 수정이 필수 — SELFHOST 업그레이드 절에 적는다. [사용자: "바로 없앰"]
D10. 화면 주소와 화면 폼 POST 경로(HTML·303: /tasks, /work/{key}/…, /me…, /responsibilities/add·/remove, /sources/tokens…, /operator/…, /operator/github/app/callback 등)와 `/healthz` 는 옮기지 않는다. 접두어 일괄 치환 금지 — JSON 라우터만 옮긴다. [근거: D5 는 JSON API 대상. GitHub App callback 은 GitHub 에 등록된 주소, `/healthz` 는 compose healthcheck·install.sh 가 부름]
D11. 웹 스크립트(`data-json-action`, 10곳)·HTML 링크(`metrics.html` 의 JSON 내려받기)·설정 n8n 입구 탭이 보여 주는 주소(`web.py` `INBOUND_PATH`)와 러너 클라이언트(`connector/client.py`)·n8n 문서/예시의 주소를 같은 변경에서 고친다. [근거: D9]

## 멤버 토큰
D12. 멤버 토큰을 만든다 — 한 멤버가 여러 개, 이름을 붙여 발급, 하나씩 폐기, 자동 만료 없음, 마지막 사용 시각 표시. [사용자: "여러 개·이름·만료 없음"(옵션 설명에 마지막 사용 시각·/me 발급 포함)]
D13. 발급·목록·폐기 화면은 `/me`. [사용자: 선택 옵션 설명 "/me 에서 이름 붙여 여러 개 발급"]
D14. 멤버를 비활성화하면 그 멤버 토큰은 모두 무효. [사용자: 옵션 설명 "멤버를 비활성화하면 전부 무효"] 비활성화는 토큰을 실제로 폐기(`revoked_at`)한다 — 다시 활성화해도 되살아나지 않는다(로그인 세션과 같은 동작). 비밀번호 변경·재설정은 토큰을 건드리지 않는다. [사용자: "비활성화 때만 폐기"] 관리자가 남의 토큰을 개별 폐기하는 화면은 만들지 않는다. [N/A — 비활성화로 대응 가능, 요청 없음]
D15. 토큰마다 발급 때 `읽기 전용`(GET 만, 그 밖 403) 또는 `내 권한 전부` 를 고른다. 항목별 세부 권한은 없다. [사용자: "발급 때 '읽기 전용' 선택"]
D16. 토큰 요청의 권한은 토큰 주인 멤버의 역할(`team.can`)로 판정 — 웹과 같다. [근거: D4·ADR-0031 결정 3]
D17. 저장은 sha256 해시만, 원문은 발급 응답에 한 번만. DB·로그·응답(그 뒤)·템플릿·백업에 원문 없음. AGENTS.md 비밀값 목록에 멤버 토큰을 더한다. [근거: AGENTS.md CRITICAL 비밀값 규칙, login_sessions·member_invites 의 token_sha256 패턴]
D18. 원문 형식 접두어 `wfm_` + 무작위(연결 토큰 `wfc_` 와 구별). [조정값]
D19. 인증: 사람용 JSON API 는 `Authorization: Bearer <멤버 토큰>` 또는 로그인 쿠키를 받는다. Bearer 헤더가 있으면 토큰만 보고 쿠키는 무시, 토큰이 틀리면 401(쿠키로 넘어가지 않음). Origin 검사 예외는 (a) `Authorization: Bearer` 헤더가 있는 요청, (b) 러너·n8n 입구의 정확한 새 경로(`/api/v1/connector/`·`/api/v1/executions/`·`/api/v1/sources/{s}/chains`)뿐이다. `/api/v1/` 접두어 전체를 예외로 넓히지 않는다 — 쿠키 API 가 CSRF 검사에서 빠진다. [근거: auth.check_origin 규칙·CSRF 원리, 검사 1 지적]
D20. 멤버 로그인을 요구하는 기존 JSON API(사람 요청·사내 요청·담당 범위)는 모두 토큰도 받는다. 관리자 설정 API(GitHub 소스·매핑·지표)도 같은 의존성을 쓰므로 토큰을 받되 역할 검사는 그대로. [사용자: "멤버용 기존 API 도 토큰"]
D21. 토큰 발급(`POST /api/v1/me/tokens`)은 로그인 쿠키로만 — 토큰으로 토큰을 만들지 않는다. [조정값 — 새면 확산되지 않게]
D22. 마지막 사용 시각 기록은 분 단위 이상 차이 날 때만 갱신. [조정값 — 매 요청 쓰기 방지]
D23. 토큰 실패 횟수 제한 없음. [N/A — 원문이 무작위 32바이트 이상이라 추측 공격 불가, 로그인 제한(LoginThrottle)은 비밀번호용]

## 응답·오류 계약
D24. 읽기 전용 토큰의 GET 밖 요청은 403 `token_read_only`("읽기 전용 토큰입니다") — 역할 부족 `forbidden` 과 구별. 기준은 메서드(GET·HEAD 만 허용), POST 인 미리보기도 막힌다. [사용자: "따로 구분"]
D24b. 오류 본문은 기존 `code·message·field·details`, 미인증 401 `unauthenticated`, 권한 403 `forbidden`, 없는 업무 404 `not_found`, `WorkActionError` 는 같은 status·code 로 옮긴다. [근거: errors.py·CONTRACT 공통 규칙]
D25. 행동 요청은 JSON 본문만(폼 본문 422). [근거: ARCHITECTURE "이 API 들은 JSON 본문만 받는다"]
D26. 목록 응답 = `WorkRow` 칸(키·제목·종류·우선순위·담당·상태·상태 이유·다음 할 일·판단·다음 단계·저장소·원본·갱신 시각) + 빠른 필터 건수. 상태 값은 화면과 같은 사용자 상태 라벨(GLOSSARY). [근거: D4 같은 함수 — `repo.list_work_rows`·`filter_rows`·`filter_counts`]
D27. 목록 쿼리 `filter`(웹 빠른 필터 값)·`repository`·`closed` 는 웹과 같은 값 집합. 모르는 값은 422 `invalid_field`(웹은 기본값으로 바꾸지만 API 는 엄격). 묶기·보드(group·view)는 화면 배치라 받지 않는다. 페이지 나누기 없음(웹과 같음). [조정값]
D28. 상세 응답 = 업무 패널이 보여 주는 정보(업무 칸·단계·링크·열린 사람 요청과 응답·판단 제안·다음 단계 제안·PR·직접 작업·사내 요청 요약·이벤트·할 수 있는 행동)에서 화면 전용(폼 숨은 입력)을 뺀 것. 담당 후보(멤버·에이전트 id·이름, 에이전트는 러너 켜짐 `online`·소유자 승인 필요 `needs_owner_approval` — 패널 선택 목록과 같은 함수·같은 값, 새 '맡길 수 있음' 판정 없음)를 포함한다 [사용자: "업무 상세에 후보 포함", 검사 2 뒤 "웹과 같은 값"]. 그 밖 내부 ID 는 행동에 필요한 것(제안 id 등)만. [근거: D4 + 사용자 "업무 하나의 상태(단계·다음 할 일·제안)"]
D29. 행동 성공 응답은 바뀐 업무 상세(D28). [조정값]
D30. 중복·동시 요청은 도메인 동작 그대로(판단 제안은 triage_id 로 stale 409 등) — API 전용 멱등 키를 새로 두지 않는다. [근거: ADR-0031 결정 3 "규칙을 따로 두지 않는다"]
D31. 사내 요청 생성·조회 경로가 work_item_id 에서 RUN 키로 바뀐다(`/api/v1/work-items/RUN-12/internal-requests`). [근거: D6·D9]

## 저장·문서
D32. 새 표 `member_tokens`(token_id·member_id·name·scope(read|full)·token_sha256·created_at·last_used_at·revoked_at) → 스키마 v26, 마이그레이션·스키마 버전을 박은 테스트 갱신. [근거: D12·D15·D17]
D33. 문서: 새 ADR-0032(주소 체계·멤버 토큰 — ADR-0031 미결 "API 인증" 해소), ARCHITECTURE·CONTRACT(주소·예시)·GLOSSARY(멤버 토큰 용어)·SELFHOST(v26 업그레이드: 러너 재설치·n8n 주소 수정)·n8n 문서·AGENTS.md 비밀값 목록. [근거: 프로젝트 관례] 옛 주소는 문서 **전부**에서 새 주소로 고친다 — ADR·VERIFICATION_LOG·ARCHITECTURE 지난 phase 절·CONTRACT·제품 문서 포함. 제외는 `docs/archive/`(보관물)와 `phases/*/`(하네스 실행 기록)뿐. [사용자: "전부 고침"]
D35. 업그레이드 순서: 진행 중 실행 0건 확인 → 백업 → install.sh(v26) → 러너 재설치(`install-runner.sh`) → n8n 워크플로우 주소 수정. 그 사이 옛 러너의 요청은 404(옛 주소 없음). [근거: SELFHOST 기존 업그레이드 절차 "진행 중 실행이 끝난 뒤" + D9]
D36. 판정 순서: 읽기 전용 토큰의 GET·HEAD 밖 요청은 역할 검사보다 먼저 403 `token_read_only`. 쿠키 전용 API(토큰 발급)에 Bearer 가 오면 403 `cookie_required`. [조정값]
D37. 멤버당 토큰 개수 상한 없음. [조정값 — 검사 2: 근거 없는 상한 제거]
D34. FastAPI 기본 `/docs`·`/openapi.json` 은 지금처럼 둔다. [N/A — 이번 결정과 무관, 기존 동작]

## 하지 않는 것

새 업무 직접 등록 API(D2), 인계 리포트·CLI·IDE 앱(D3), 웹 화면을 API 클라이언트로 바꾸기(D4), 옛 주소 별칭·리다이렉트(D9), 관리자의 남의 토큰 개별 폐기 화면(D14), 토큰 항목별 세부 권한(D15), 토큰 만료(D12), API 전용 멱등 키(D30), 페이지 나누기(D27), 러너·n8n 경로 뒷부분 이름 변경(D8), 실제 GitHub·Claude·Jira 호출, 셀프호스트 업그레이드 적용(phase 뒤 사용자 지시).

## Step

| # | 이름 | 하는 일 |
|---|---|---|
| 0 | `api-design` | 문서만: ADR-0032, ARCHITECTURE "사람용 API — phase 24"(옛→새 주소 전체 표·응답 칸·토큰·오류 코드·step 별 시그니처), CONTRACT 예시, GLOSSARY, AGENTS.md 비밀값 목록, CURRENT_HANDOFF |
| 1 | `member-token-store` | 스키마 v26 `member_tokens`, repo 발급·목록·폐기·조회·마지막 사용 갱신, 비활성화 시 폐기 |
| 2 | `token-auth` | `auth.py` Bearer 인증·읽기 전용 403 `token_read_only`·Origin 예외 좁히기 |
| 3 | `me-tokens` | `/api/v1/me/tokens` API(발급은 쿠키만)·`/me` 화면 토큰 절 |
| 4 | `api-prefix` | 사람용 JSON 라우터(GitHub 소스·사람 요청·지표·담당 범위·사내 요청·매핑) `/api/v1` 로, 옛 주소 제거, 사내 요청 RUN 키, 웹 스크립트·지표 링크, 테스트 주소 |
| 5 | `connector-paths` | 러너·n8n 프로토콜(서버 `machine_api`·`inbound_api` + `connector/client.py` 함께 — 한쪽만 옮기면 e2e 가 깨짐), Origin 예외 새 경로 |
| 6 | `work-read-api` | `GET /api/v1/work-items`·`GET /api/v1/work-items/{key}`(담당 후보 포함) |
| 7 | `work-action-api` | 우선순위·담당·직접 작업·판단 받기·제안 수락/무시 API |
| 8 | `ops-docs` | SELFHOST v26 업그레이드 절, n8n 문서·예시 주소, CURRENT_HANDOFF |
| 9 | `human-api-verify` | e2e: 토큰 발급 → 조회 → 맡기기 → 읽기 전용 403 → 폐기 401 → 옛 주소 404, 문서 표 ↔ 라우트 대조 |
