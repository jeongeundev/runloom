# Phase 15 — 팀: 초대 링크·이메일 로그인·역할·사람별 "내 차례"·알림·러너 소유자

작성일: 2026-09-30. 상태: 구현 계획 작성 완료, 모든 step pending. **`service`(14-task-model 병합, 스키마 v10)에서 실행한다.** 근거: [재설계 계획](../../docs/product/REDESIGN_PLAN.md) 5.3·12·13절, [ADR-0016](../../docs/adr/0016-selfhost-docker-fixed-workspace.md) 결정 3, [ADR-0020](../../docs/adr/0020-work-items-and-stages.md) 결정 8.

## 조사로 확인한 현재 (2026-09-30, `service` 32d7aba)

- 로그인은 `POST /login` 에 `OPERATOR_TOKEN` 하나(`server/web.py` `_workspace_login`). 쿠키 `wf_session` = 워크스페이스 id(`sess-selfhost`) + `SESSION_SECRET` HMAC(`server/auth.py`), 14일, `httponly`·`samesite=lax`, `secure` 없음. 서버 쪽 로그인 세션 표가 없어 로그아웃은 쿠키만 지운다. 로그인 실패 제한 `LoginThrottle` 은 프로세스 전역 하나.
- 권한은 `sessions.is_operator` 하나: `require_operator`(API 401/403)·`_require_operator_page`(화면 403)·`_base()["is_operator"]`·템플릿 `{% if is_operator %}`. `/kinds`·`/rules`·`/sources` 같은 설정 화면은 `require_session` 만 건다. CSRF 는 SameSite=Lax 에만 기댄다(GitHub App state 쿠키 예외).
- `members`(v10): `member_id`·`session_id`·`display_name`·`role IN ('admin','member')`·`created_at`. 이메일·비밀번호 칸 없음. `ensure_first_admin`(표시 이름 `관리자`)이 워크스페이스 생성·v10 마이그레이션 때 만든다. `add_member` 는 테스트만 부른다. `work_items.assignee_type/assignee_id` 는 멤버·Agent 다형 참조, `repo.assign_work_item` 은 라우트가 부르지 않는다.
- "내 차례" 는 업무 상태 값(`domain/work_status.py`)일 뿐 사람 구분이 없다. `human_requests`·`human_responses` 에 받는 사람·응답자 칸이 없다.
- 알림: `notifications` 큐(`session_id`, `dedupe_key` UNIQUE, 이벤트 `human_request`·`pr_opened`·`task_failed`)에 받는 사람 칸 없음. 웹훅 URL 은 `SecretStore` 고정 이름 `notify_webhook_url` 하나(설치 전체). 이름 목록 `NAMES` 가 고정이라 멤버별 파일이 없다.
- 러너: `connect_codes`(발급자 없음)·`connectors`(소유자 없음). `[러너 붙이기]`(`/operator/github/sources/{id}/runner`)·`/operator/connect-codes` 는 운영자 전용. `agents.owner_scope` 는 늘 `personal`.
- 비밀번호 해시 의존성 없음 — 표준 `hashlib.scrypt` 사용 가능(Python 3.13, 이미지 `python:3.13-slim`).
- 테스트: 서버 테스트 약 260개가 `tests/workflow/server/conftest.py` `log_in(client)`(토큰 POST)을 거친다. `/login` 을 직접 부르거나 문구를 단정하는 테스트가 수십 개, e2e 5개 파일이 `OPERATOR_TOKEN` 으로 로그인한다. `tests/test_selfhost_files.py` 가 `.env.example` 키와 `settings.ENV_KEYS` 일치를 검사한다.

## 계획 기본값 (사용자 결정 2026-09-30, step 0 이 ADR-0021 로 고정)

1. **운영자 토큰은 첫 설정·복구 전용.** 비밀번호가 있는 활성 멤버가 하나도 없으면 `/login` 은 토큰 칸을 보이고, 토큰이 맞으면 "관리자 계정 만들기"(이메일·표시 이름·비밀번호) — 기존 첫 관리자 멤버(`관리자`)에 계정을 채운다. 그 뒤 `/login` 은 이메일·비밀번호. 토큰은 `/login/recover`(토큰 + 관리자 이메일 + 새 비밀번호)에서만 쓴다. `OPERATOR_TOKEN`·`SESSION_SECRET` 은 `.env` 필수 그대로(업그레이드 호환). `sessions.is_operator` 칸은 남기되 권한 판정에 쓰지 않는다.
2. **로그인 세션은 서버 표.** 쿠키 값 = 무작위 토큰, DB 에는 sha256 만(연결 토큰과 같은 방식). 로그아웃·비활성화·비밀번호 변경이 즉시 세션을 폐기한다. 쿠키 이름을 바꿔(`wf_login` 제안 — step 0 확정) 옛 쿠키는 자연히 무효. 14일 유지.
3. **초대 링크.** 관리자가 역할(`admin`|`member`)을 정해 발급, 1회용·7일 만료, DB 에는 sha256 만. 이메일 발송 없음 — 링크를 복사해 전한다. 받는 사람이 이메일·표시 이름·비밀번호를 넣어 가입. 비밀번호 재설정 링크도 같은 표(`purpose` = `reset`, 대상 멤버 지정, 24시간). 관리자가 발급.
4. **역할.** 관리자: GitHub 연결·소스·n8n 입구 토큰·매핑 표·종류·후속 규칙·공용 알림·팀(초대·역할·비활성화·재설정 링크)·모든 러너 해제. 멤버: 업무 등록·맡기기·사람 요청 응답·병합 결정·지표 보기·**자기 러너 붙이기와 해제**·내 설정. 권한 판정은 `domain/team.py` 의 역할 × 동작 표 한 곳. 멤버는 삭제하지 않고 비활성화(`disabled_at`). 활성 관리자가 0 이 되는 강등·비활성화는 거부.
5. **사람별 "내 차례".** 업무 상태 계산(`work_status`)은 그대로. 받는 사람은 순수 함수로: 담당이 활성 멤버면 그 멤버, 아니면 **맡긴 사람**(`work_items.requested_by_member_id`, 활성일 때), 아니면 활성 관리자 전원. 맡긴 사람은 [에이전트에게 맡기기]·[다시 맡기기]·체인 시작을 누른 멤버(가장 최근). 자동 시작·수집은 기록하지 않는다. 응답은 로그인한 누구나 하고, 응답자를 `human_responses.member_id` 에 남긴다. 홈 빠른 필터 "내 차례" 는 로그인한 멤버 기준.
6. **알림.** 공용 웹훅은 그대로(관리자 설정). 알림 행마다 받는 사람(멤버, 여럿이면 행 여럿)을 두고 공용 메시지에 `→ 이름` 을 붙인다(받는 사람이 여럿인 공용 메시지는 한 번만). 멤버가 내 설정에 **개인 웹훅**을 저장하면 그 멤버가 받는 알림은 개인 웹훅으로도 간다. 개인 웹훅 URL 은 비밀 저장소(멤버 id 가 든 파일 이름, 0600)에 두고 DB·로그·응답·템플릿에 넣지 않는다. 중복 방지 키에 받는 사람·경로를 넣는다.
7. **공개 주소.** 선택 설정 `WORKFLOW_PUBLIC_URL`(`.env.example` 에 빈 값). 초대·재설정 링크의 주소와 Origin 검사 기준. `https://` 면 로그인 쿠키에 `secure`. 비면 요청 주소(`http://127.0.0.1:8000`)를 쓴다. 터널(Tailscale·Cloudflare Tunnel)은 SELFHOST 문서 안내만 — 설치 스크립트는 바꾸지 않는다.
8. **CSRF = Origin 검사.** 쿠키로 인증되는 변경 요청(POST 등)은 `Origin`(없으면 `Referer`)의 scheme+host+port 가 요청 주소 또는 `WORKFLOW_PUBLIC_URL` 과 같아야 한다. 둘 다 없으면 거부. Bearer 인증(러너 `wfc_`·입구 `wfs_`)과 `/connector/exchange` 는 제외. 폼 토큰은 넣지 않는다.
9. **로그인 실패 제한**은 이메일별(복구는 복구 경로 하나)로 나눈다. 60초에 5회 → 429. 프로세스 메모리 그대로.
10. **러너 소유자.** 연결 코드 발급 때 발급 멤버를 기록(`connect_codes.issued_by_member_id`)하고 교환 때 `connectors.owner_member_id` 로 옮긴다. 에이전트 소유자는 연결 프로그램에서 읽는다(에이전트 칸을 새로 두지 않는다). 해제·코드 취소는 소유자와 관리자. v10 이전 러너는 소유자 없음 = 관리자 관리. 러너 프로토콜(계약 v1)은 바뀌지 않는다.
11. **스키마 v11**(ALTER + 새 표, 재생성 없음): `members.email`(소문자 정규화, 부분 UNIQUE INDEX `(session_id, email) WHERE email IS NOT NULL`)·`password_hash`·`disabled_at`, 새 표 `login_sessions`·`member_invites`, `work_items.requested_by_member_id`, `human_responses.member_id`, `notifications.recipient_member_id`·`channel`, `connect_codes.issued_by_member_id`, `connectors.owner_member_id`. 기존 데이터는 그대로(새 칸 NULL).

**하지 않는 것**: 이메일(SMTP) 발송, 외부 로그인(Google·GitHub OAuth), 여러 워크스페이스, 2단계 인증, 담당자별 목록 화면·보드(16-work-ui), 터널 설치 자동화, 폼 CSRF 토큰, GitHub·Jira 사용자와 멤버 연결(담당 매핑 — 17 이후).

## Step 목록

| Step | 이름 | 산출물 |
|---|---|---|
| 0 | team-design | ADR-0021, ARCHITECTURE "팀 — phase 15", GLOSSARY, CURRENT_HANDOFF 한 줄 |
| 1 | team-domain | `domain/team.py`: 비밀번호 해시·검증·규칙, 역할 × 동작 표, 내 차례 받는 사람 |
| 2 | schema-v11 | v11 칸·표, v10 → v11 마이그레이션 |
| 3 | member-repo | 계정·초대·재설정·비활성화·역할·로그인 세션 repo 함수 |
| 4 | auth-sessions | 현재 멤버 의존성, 새 쿠키, Origin 검사, 이메일별 제한, `WORKFLOW_PUBLIC_URL` |
| 5 | login-web | 로그인·첫 설정·복구·초대 가입·로그아웃, 테스트 로그인 헬퍼·e2e 로그인 |
| 6 | role-gates | 라우트·템플릿 권한을 역할 표로, 러너 붙이기 멤버에게 |
| 7 | team-web | 팀 화면·내 설정(비밀번호·개인 웹훅) |
| 8 | my-turn | 맡긴 사람·응답자 기록, 사람별 내 차례 |
| 9 | notify-recipients | 받는 사람별 알림·개인 웹훅 |
| 10 | runner-owner | 러너·에이전트 소유자와 해제 권한 |
| 11 | team-verify | e2e 팀 한 줄기, v10 사본 마이그레이션, SELFHOST·인계 |

## 실행

```bash
git status --short            # 깨끗해야 한다
git branch --show-current     # service
python3 scripts/execute.py 15-team --engine claude
```

사용자가 띄워 둔 셀프호스트(compose 프로젝트 `runloom`)·러너(launchd)·`/Users/kje/demo/*` 는 건드리지 않는다. 병합·셀프호스트 재설치(v11 — 재설치 뒤 첫 접속에서 토큰으로 관리자 계정 만들기)는 phase 뒤 사용자 지시로.
