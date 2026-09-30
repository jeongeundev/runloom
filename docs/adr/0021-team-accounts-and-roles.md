# ADR-0021: 팀 — 초대 링크·이메일 로그인·역할·사람별 "내 차례"·알림·러너 소유자

결정일: 2026-09-30 (phase 15 step 0). 근거: [재설계 계획](../product/REDESIGN_PLAN.md) 5.3절(팀·담당자)·12절(접근 모델)·13절(phase 제안 3 `15-team`), [ADR-0020](0020-work-items-and-stages.md) 결정 8(멤버 — "로그인·초대·역할 변경은 15-team"). 기본값은 [phase 15 README](../../phases/15-team/README.md) "계획 기본값" 11가지(사용자 결정 2026-09-30)이며 이 문서로 구현 기준을 고정한다. 적용 범위는 `service` 브랜치. 이 시점에는 구현이 없다 — 아래 이름·표·경로는 step 1~11 이 만들고, 바꿀 때는 이 ADR·[ARCHITECTURE](../ARCHITECTURE.md) "팀 — phase 15"·[GLOSSARY](../GLOSSARY.md)·테스트를 같이 고친다. 이름·시그니처의 기준은 ARCHITECTURE 쪽이다(README 와 다르면 ARCHITECTURE).

**이 ADR 은 [ADR-0016](0016-selfhost-docker-fixed-workspace.md) 결정 3 의 "`/login` 에 `OPERATOR_TOKEN` 을 넣으면 … 로그인한 사람은 곧 운영자다" 를 대체한다.** 고정 워크스페이스 하나(`sess-selfhost`)·익명 세션 없음·러너(`wfc_`)·n8n(`wfs_`) 인증은 그대로다.

계기(README "조사로 확인한 현재"): 로그인은 `OPERATOR_TOKEN` 하나이고 쿠키는 워크스페이스 id 의 서명뿐이라 누가 했는지 모르고, 로그아웃은 쿠키만 지운다. 권한은 `sessions.is_operator` 하나이고 설정 화면 일부(`/kinds`·`/rules`·`/sources`)는 로그인만 본다. "내 차례" 는 업무 상태 값일 뿐 사람 구분이 없고, 알림은 설치 전체에 웹훅 하나다. 러너(연결 코드·연결 프로그램)에는 소유자가 없다. 팀이 한 설치를 같이 쓰려면 사람 계정·역할·사람별 차례가 필요하다.

## 결정

1. **운영자 토큰은 첫 설정·복구 전용.** 비밀번호가 있는 활성 멤버가 하나도 없으면(`needs_first_setup`) `/login` 은 토큰 칸을 보이고, 토큰이 맞으면 "관리자 계정 만들기"(이메일·표시 이름·비밀번호)로 간다. 기존 첫 관리자 멤버(`ensure_first_admin` 이 만든 `관리자`)가 있으면 그 행에 계정을 채우고, 없으면 새 관리자를 만든다. 그 뒤 `/login` 은 이메일·비밀번호만 받는다. 토큰은 `/login/recover`(토큰 + 활성 관리자 이메일 + 새 비밀번호)에서만 쓴다. `OPERATOR_TOKEN`·`SESSION_SECRET` 은 `.env` 필수 그대로다(업그레이드 호환 — `SESSION_SECRET` 은 GitHub App state 쿠키 서명에 계속 쓴다). `sessions.is_operator` 칸은 남기되 권한 판정에 쓰지 않는다.
2. **로그인 세션은 서버 표(`login_sessions`).** 쿠키 값 = 무작위 토큰, DB 에는 sha256 만(연결 토큰과 같은 방식). 로그아웃·비활성화·비밀번호 변경·재설정·복구가 즉시 세션을 폐기한다. 쿠키 이름을 `wf_login` 으로 바꿔 옛 `wf_session` 쿠키는 읽지 않는다(자연히 무효, 다시 로그인). 14일 유지(`session_cookie_days`).
3. **초대 링크와 재설정 링크(`member_invites`).** 관리자가 역할(`admin`|`member`)을 정해 초대 링크를 발급한다. 1회용·7일 만료, DB 에는 sha256 만. 이메일 발송 없음 — 발급 화면에 한 번 보이는 링크를 복사해 전한다. 받는 사람이 이메일·표시 이름·비밀번호를 넣어 가입한다. 비밀번호 재설정 링크도 같은 표(`purpose = 'reset'`, 대상 멤버 지정, 24시간)이며 관리자가 발급한다.
4. **역할 두 개.** 관리자(`admin`, 화면 말 "관리자"): GitHub 연결·소스·n8n 입구 토큰·매핑 표·종류·후속 규칙·공용 알림·팀(초대·역할·비활성화·재설정 링크)·모든 러너 해제. 멤버(`member`): 업무 등록·맡기기·사람 요청 응답·검토·병합 결정·지표 보기·자기 러너 붙이기와 해제·내 설정. 관리자는 멤버의 동작을 모두 한다. 권한 판정은 `domain/team.py` 의 역할 × 동작 표(`can(role, action)`) 한 곳이다. 멤버는 삭제하지 않고 비활성화(`disabled_at`)하며 다시 활성화할 수 있다. 활성 관리자가 0 이 되는 강등·비활성화는 거부한다.
5. **사람별 "내 차례".** 업무 상태 계산(`domain/work_status`)은 그대로다. 그 업무가 `내 차례` 일 때 받는 사람은 순수 함수 `turn_recipients` 가 정한다: 담당이 활성 멤버면 그 멤버, 아니면 **맡긴 사람**(`work_items.requested_by_member_id`, 활성일 때), 아니면 활성 관리자 전원. 맡긴 사람은 그 업무에서 에이전트 작업을 시작시킨 멤버(가장 최근)다 — [에이전트에게 맡기기]·[다시 맡기기]·체인 시작·직접 등록·직접 실행. 자동 시작·수집·트리거 라벨은 기록하지 않는다(누가 했는지 모른다). 응답은 로그인한 누구나 하고, 응답자를 `human_responses.member_id` 에 남긴다. 홈 빠른 필터 "내 차례" 는 로그인한 멤버가 받는 사람인 업무다.
6. **알림은 받는 사람별.** 공용 웹훅은 그대로(관리자 설정, 비밀 파일 `notify_webhook_url`). 알림 행마다 받는 사람 한 명(`notifications.recipient_member_id`)과 경로(`channel`: `shared`|`personal`)를 둔다. 공용 메시지는 사건마다 한 번 보내고 끝에 `→ 이름`(여럿이면 이름을 나열)을 붙인다. 멤버가 내 설정에 **개인 웹훅**을 저장하면 그 멤버가 받는 알림은 개인 웹훅으로도 간다. 개인 웹훅 URL 은 비밀 저장소 파일(`notify_webhook_url.<member_id>`, 0600)에만 두고 DB·로그·응답·템플릿에 넣지 않는다. 중복 방지 키에 경로·받는 사람을 넣는다.
7. **공개 주소 `WORKFLOW_PUBLIC_URL`.** 이미 있는 선택 설정(`Settings.public_url`)을 초대·재설정 링크의 앞부분과 Origin 검사 기준으로도 쓴다. `https://` 로 시작하면 로그인 쿠키에 `Secure`. 비면 요청 주소(`http://127.0.0.1:8000`)를 쓴다. 원격 접속 터널(Tailscale·Cloudflare Tunnel)은 SELFHOST 문서 안내만 — 설치 스크립트는 바꾸지 않는다.
8. **CSRF = Origin 검사.** 쿠키로 인증되는 변경 요청(GET·HEAD·OPTIONS 밖)은 `Origin`(없으면 `Referer`)의 scheme+host+port 가 요청 주소 또는 `WORKFLOW_PUBLIC_URL` 과 같아야 한다. 둘 다 없거나 다르면 403 `forbidden_origin`. Bearer 인증 경로(러너 `wfc_` — `/connector/*`·`/executions/*`, 입구 `wfs_` — `/sources/{source}/chains`)와 `/connector/exchange`(연결 코드 교환)는 제외한다. 폼 토큰은 넣지 않는다.
9. **로그인 실패 제한은 이메일별.** 60초에 5회 실패 → 그 이메일(정규화한 값)은 창이 지날 때까지 429. 첫 설정·복구는 각각 한 칸(`setup`·`recover`)을 쓴다. 프로세스 메모리 그대로(127.0.0.1·소규모 팀 전제).
10. **러너 소유자.** 연결 코드 발급 때 발급 멤버를 기록(`connect_codes.issued_by_member_id`)하고 교환 때 `connectors.owner_member_id` 로 옮긴다. 에이전트 소유자는 연결 프로그램(`agents.connector_id`)에서 읽는다 — 에이전트에 칸을 새로 두지 않는다. 러너 해제·코드 취소는 소유자와 관리자. v11 이전 러너·코드는 소유자 없음 = 관리자 관리. 러너 프로토콜(계약 v1)은 바뀌지 않는다.
11. **스키마 v11 은 ALTER + 새 표.** `members.email`(소문자 정규화, 부분 UNIQUE INDEX `(session_id, email) WHERE email IS NOT NULL`)·`password_hash`·`disabled_at`, 새 표 `login_sessions`·`member_invites`, `work_items.requested_by_member_id`, `human_responses.member_id`, `notifications.recipient_member_id`·`channel`, `connect_codes.issued_by_member_id`, `connectors.owner_member_id`. 기존 표를 재생성하지 않고 기존 데이터는 그대로다(새 칸 NULL — `channel` 만 기본값 `shared`). 기존 첫 관리자는 이메일·비밀번호가 없으므로 업그레이드 뒤 첫 접속이 첫 설정이다.

비밀번호는 표준 라이브러리 `hashlib.scrypt`(n=2^14, r=8, p=1, 32바이트, 16바이트 무작위 salt)로 해시하고 파라미터를 저장 문자열에 함께 둔다. 토큰(로그인 세션·초대·재설정)은 `secrets.token_urlsafe(32)`, DB 에는 sha256 hex 만. 새 의존성은 없다([ADR-0002](0002-server-stack-python-fastapi-sqlite.md)).

## 하지 않는 것

- 이메일(SMTP) 발송 — 초대·재설정 링크는 사람이 복사해 전한다.
- 외부 로그인(Google·GitHub OAuth), 2단계 인증, 여러 워크스페이스(워크스페이스는 `sess-selfhost` 하나 그대로).
- 담당자별 목록 화면·보드·상세 패널(16-work-ui). 이 phase 의 "내 차례" 화면 변화는 홈 빠른 필터 하나다.
- 터널 설치 자동화, 폼 CSRF 토큰.
- GitHub·Jira 사용자와 멤버 연결(담당 매핑 — 17 이후). GitHub 담당자 연결(`AssigneeBinding`)은 지금처럼 Agent 를 고르는 재료다.
- 기존 표 재생성, 멤버 삭제, 멤버 스스로 가입(초대 없이), 멤버가 스스로 이메일 바꾸기.

## 대안

- **토큰 로그인 완전 제거 + CLI 로 관리자 생성.** 로그인 경로가 하나라 단순하지만, 첫 설치가 웹에서 끝나지 않고 컨테이너 안 CLI(`docker compose exec`)를 알아야 한다. 비밀번호를 잊은 유일한 관리자의 복구도 CLI 가 된다. `.env` 에 이미 있는 토큰을 첫 설정·복구에만 쓰는 편이 설치 흐름을 지킨다. 기각.
- **토큰 로그인과 이메일 로그인 병존.** 업그레이드가 가장 부드럽지만 로그인 방법이 둘로 남고, 토큰으로 들어온 사람은 누구인지 모른다(응답자·맡긴 사람·러너 소유자를 기록할 수 없다). 기각.
- **받는 사람 = 담당 멤버, 없으면 관리자.** 단순하지만 업무 담당은 대개 Agent 라서(ADR-0020 결정 — `create_execution` 이 담당을 실행 Agent 로 채운다) 멤버가 에이전트에게 맡긴 일의 후속(검토 승인·실패)을 관리자가 받게 된다. 맡긴 사람을 가운데 둔다. 기각.
- **폼 CSRF 토큰.** 표준 방식이지만 모든 폼 템플릿과 화면 스크립트의 JSON 요청을 고쳐야 한다. 같은 위협(다른 사이트가 쿠키를 실어 보내는 변경 요청)을 Origin 검사가 막고, 요즘 브라우저는 변경 요청에 `Origin` 을 싣는다. 기각.
- **서명 쿠키만(서버 표 없음).** 표가 필요 없지만 로그아웃·비활성화·비밀번호 변경이 쿠키 만료(14일)까지 반영되지 않는다. 팀에서는 비활성화가 즉시 먹어야 한다. 기각.
- **이메일 발송.** 초대·재설정이 자연스럽지만 SMTP 서버·발신 주소·스팸 처리 설정이 1인·소규모 셀프호스트에 부담이다. 링크 복사로 충분하다. 기각.
- **argon2·bcrypt 라이브러리.** 권장 알고리즘이지만 새 의존성(네이티브 빌드)이 생긴다. `hashlib.scrypt` 는 표준 라이브러리이고 메모리 강도가 있는 KDF 다. 기각.

## 결과

- 업그레이드(v10 → v11) 뒤 모든 기존 로그인이 풀리고, 첫 접속에서 `.env` 의 `OPERATOR_TOKEN` 으로 관리자 계정을 만든다(SELFHOST 업그레이드 절, step 11).
- 서버 테스트의 로그인 헬퍼(`tests/workflow/server/conftest.py` `log_in`)와 e2e 로그인이 이메일·비밀번호로 바뀐다(step 5). 테스트는 낮은 scrypt n 을 주입해 빠르게 돈다.
- 모든 쿠키 인증 변경 요청에 Origin 검사가 걸리므로 테스트 클라이언트는 `Origin` 헤더를 기본으로 싣는다(step 4·5).
- 권한은 역할 × 동작 표 한 곳이라 새 경로는 동작 하나를 고르면 된다. 템플릿은 `is_operator` 대신 `can` 결과(`allowed` 집합)를 본다(step 6).
- 알림 행이 받는 사람마다 생기므로 알림 표가 커진다. 공용 경로는 사건마다 한 번만 보낸다.
- phase 8·11·12·14 의 GitHub 순환(수집 → 수정 → 검토 → 초안 PR → 병합 추적 → 업무 완료)은 사람 쪽 기록(맡긴 사람·응답자·받는 사람)만 더해지고 흐름은 그대로다.
