# Step 0: team-design — 팀 모델 결정과 이름 고정

## 읽어야 할 파일

- AGENTS.md
- phases/15-team/README.md (조사 결과·계획 기본값 11가지 — 이 phase 의 기준), phases/15-team/index.json (이전 step summary)
- docs/product/REDESIGN_PLAN.md (5.3·12·13절)
- docs/adr/0016-selfhost-docker-fixed-workspace.md (결정 3), docs/adr/0019-service-selfhost-only.md, docs/adr/0020-work-items-and-stages.md (결정 8), docs/adr/0017-github-app-connection.md (비밀 저장소)
- docs/ARCHITECTURE.md ("셀프호스트 — phase 10", "셀프호스트 전용 — phase 13", "업무와 단계 — phase 14" 의 스키마 v10·이름 표, 알림 웹훅 절)
- docs/GLOSSARY.md (`session`, `operator`, `connect code`, 고정 워크스페이스·워크스페이스 로그인, 러너 붙이기, 알림 웹훅, `Member`)
- src/workflow/server/auth.py, src/workflow/server/web.py (`_workspace_login`·`/login`·`/logout`·`_base`·`_require_operator_page`), src/workflow/server/settings.py, src/workflow/adapters/secret_store.py, src/workflow/adapters/db.py (`members`·`notifications`·`connect_codes`·`connectors`·`human_responses`), src/workflow/server/worker.py (`_notify`·`_deliver_notifications`·`_request_human`)

먼저 실제 파일을 읽는다. 대화 이력을 전제로 판단하지 않는다. 이전 step 이 정한 이름은 `docs/ARCHITECTURE.md` "팀 — phase 15" 를 따른다(README 와 다르면 ARCHITECTURE 가 기준).

## 작업

문서만 바꾼다(제품 코드·테스트 없음). README "계획 기본값" 11가지를 결정 기록으로 옮기고 이후 step 이 쓸 이름을 고정한다.

1. `docs/adr/0021-team-accounts-and-roles.md` 신설: 결정 1~11, 하지 않는 것, 대안·기각 이유(토큰 로그인 완전 제거 + CLI 로 관리자 생성 — 첫 설치가 웹에서 끝나지 않음 / 토큰·이메일 로그인 병존 — 로그인 방법이 둘로 남고 누가 했는지 모름 / 받는 사람 = 담당 멤버, 없으면 관리자 — 멤버가 에이전트에게 맡긴 일의 후속을 관리자가 받음 / 폼 CSRF 토큰 — 모든 템플릿 수정, Origin 검사로 같은 위협을 막음 / 서명 쿠키만(서버 표 없음) — 로그아웃·비활성화가 즉시 반영되지 않음 / 이메일 발송 — SMTP 설정이 1인 셀프호스트에 부담). ADR-0016 결정 3("로그인한 사람은 곧 운영자")을 이 ADR 이 대체한다고 적고 ADR-0016 끝에 한 줄 링크를 단다.
2. `docs/ARCHITECTURE.md` 에 "팀 — phase 15" 절. 반드시 **이름을 확정**해 적는다:
   - 스키마 v11: `members` 새 칸(`email` 소문자, `password_hash`, `disabled_at`) + 부분 UNIQUE INDEX, `login_sessions`(`login_id`, `session_id`, `member_id`, `token_sha256` UNIQUE, `created_at`, `expires_at`, `last_seen_at`, `revoked_at`), `member_invites`(`invite_id`, `session_id`, `purpose IN ('invite','reset')`, `role`(invite 만), `member_id`(reset 만), `token_sha256` UNIQUE, `created_by_member_id`, `created_at`, `expires_at`, `used_at`, `used_by_member_id`, `revoked_at`), `work_items.requested_by_member_id`, `human_responses.member_id`, `notifications.recipient_member_id`·`channel IN ('shared','personal')`, `connect_codes.issued_by_member_id`, `connectors.owner_member_id`. 칸 이름을 바꾸면 README 대신 여기가 기준이다.
   - 비밀번호 해시 저장 형식(예: `scrypt$n$r$p$<salt b64>$<hash b64>`)과 파라미터(n=2^14, r=8, p=1, dklen=32 — 테스트는 낮은 n 을 주입할 수 있게), 비밀번호 규칙(최소 길이 10, 최대 길이 — 긴 입력으로 CPU 를 쓰지 않게).
   - 쿠키 이름(`wf_login` 제안)·수명·`secure` 조건, 옛 `wf_session` 처리(무시), 로그인 세션 폐기 경로(로그아웃·비활성화·비밀번호 변경·재설정).
   - 첫 설정·복구 흐름 표: "비밀번호 있는 활성 멤버 0" 판정, 토큰 → 계정 만들기(기존 첫 관리자 행 재사용, 없으면 새 관리자), 복구(토큰 + 관리자 이메일 + 새 비밀번호).
   - 역할 × 동작 표(동작 이름은 `domain/team.py` 상수와 같게 — 예 `manage_connections`, `manage_rules`, `manage_team`, `manage_shared_notify`, `attach_runner`, `remove_any_runner`, `create_work`, `delegate`, `respond`, `view_metrics`, `edit_own_settings`). 라우트별 필요 동작 표(현재 `require_operator`·`_require_operator_page`·`require_session` 만 건 라우트를 모두 나열해 동작 하나씩 배정).
   - 내 차례 받는 사람 규칙(담당 멤버 → 맡긴 사람 → 활성 관리자 전원), 맡긴 사람을 기록하는 경로 목록.
   - 알림: 받는 사람별 행, 공용 메시지 `→ 이름`(여럿이면 이름 나열, 공용 전송은 한 번), 개인 웹훅 비밀 저장소 이름 규칙(예 `notify_webhook_url.mem-xxxxxxxx`), 중복 방지 키 모양.
   - Origin 검사 규칙과 제외 경로, 이메일별 실패 제한.
   - 러너 소유자: 코드 발급자 → 연결 프로그램 소유자 → 에이전트 소유자 표시, 해제 권한.
   - v10 → v11 마이그레이션(새 칸 NULL, 기존 첫 관리자는 이메일 없음 → 첫 설정 대상).
   - 이름·시그니처 표(이후 step 이 만들 함수 — 최종 이름은 여기서 정한다): `domain/team.py` `hash_password(pw, *, n=...) -> str`·`verify_password(pw, stored) -> bool`·`password_problem(pw) -> str | None`·`normalize_email(s) -> str | None`·`can(role, action) -> bool`·`turn_recipients(...) -> tuple[str, ...]`; repo `set_member_credentials`·`find_member_by_email`·`needs_first_setup`·`create_login_session`·`member_for_login_token`·`revoke_login_sessions`·`issue_invite`·`accept_invite`·`issue_reset_link`·`use_reset_link`·`revoke_invite`·`set_member_role`·`disable_member`; auth `current_member`·`require_member`·`require_action(action)`·`check_origin`.
3. `docs/GLOSSARY.md`: `operator`·워크스페이스 로그인·고정 워크스페이스 정의를 "첫 설정·복구에만 쓰는 토큰" 으로 고침, `Member` 에 계정 칸·비활성화, 새 용어 `로그인 세션`(`login_sessions` — `session` 과 다름), `초대 링크`(`member_invites`, 연결 코드와 다름 — 연결 코드 금지 표현의 `invite` 를 설명과 함께 유지), `재설정 링크`, `맡긴 사람`, `받는 사람`, `개인 웹훅`, `러너 소유자`, `공개 주소`(`WORKFLOW_PUBLIC_URL`). 역할 `admin` 은 화면 말 "관리자".
4. `docs/CURRENT_HANDOFF.md` "다음 작업" 에 "15-team 진행 중(phases/15-team)" 한 줄.

## 테스트 먼저

문서 전용 step 이라 제품 테스트를 새로 만들지 않는다. 기존 문서 검사(링크 등)가 통과해야 한다.

## Acceptance Criteria

```bash
python3 -m pytest -q
python3 -m ruff check .
```

위 커맨드와 이 step 의 테스트 항목을 모두 확인한다.

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 규칙을 확인한다: `domain/` 은 FastAPI·sqlite3·HTTPX·subprocess·Git 을 import 하지 않는다 / `server/` 와 `connector/` 는 서로 import 하지 않고 `contracts/` 만 공유한다 / 외부 입력(요청 본문·폼·초대 토큰·이슈 본문·모델 응답)에서 명령·경로를 받아 실행하지 않는다 / 비밀값(연결 토큰, `OPERATOR_TOKEN`, `SESSION_SECRET`, 로그인 세션·초대·재설정 토큰 원문, 비밀번호 원문, GitHub App 비밀·설치 토큰, PAT, 공용·개인 알림 웹훅 URL)은 DB·로그·응답·템플릿·백업에 넣지 않는다(토큰은 sha256, 비밀번호는 scrypt 해시만 DB 에) / GLOSSARY 이름을 그대로 쓴다 / phase 8·11·12·14 의 GitHub 순환(수집 → 수정 → 검토 → 초안 PR → 병합 추적 → 업무 완료)이 그대로 동작한다.
3. 성공이면 `phases/15-team/index.json` 의 step 0 만 `completed` 로 바꾸고 `summary` 에 생성·수정·삭제 파일, 결정, 검증 결과(테스트 수), 다음 step 이 주의할 점을 한 줄로 남긴다.
4. 3회 수정 후에도 실패하면 `error` + `error_message`, 사용자 결정·외부 자원이 필요하면 `blocked` + `blocked_reason` 을 기록한다. 실행하지 않은 검증을 `completed` 로 표시하지 않는다.

## 금지사항

- 실제 GitHub(api.github.com·github.com)·Discord·외부 웹훅을 호출하지 않는다(공식 문서 읽기는 허용). 이유: 모든 step 은 httpx `MockTransport`·가짜 GitHub·가짜 알림 수신으로 검증한다. 실연동은 phase 뒤 사용자와 함께 한다.
- 사용자가 띄워 둔 셀프호스트(compose 프로젝트 `runloom`, 포트 8000, 볼륨 `runloom_workflow-data`)·`deploy/selfhost/.env`·러너 홈(`~/Library/Application Support/workflow-connector/`)·launchd·`/Users/kje/demo/*` 를 읽거나 바꾸지 않는다. 컨테이너를 띄우거나 멈추지 않는다. 이유: 실사용 중인 환경이다.
- 새 의존성(argon2·bcrypt·passlib·itsdangerous·SMTP 라이브러리 등)을 추가하지 않는다. 이유: 표준 `hashlib.scrypt`·`secrets`·`hmac` 으로 충분하고 ADR-0002 가 의존성을 최소로 둔다.
- 이메일 발송·외부 로그인(OAuth)·여러 워크스페이스·2단계 인증을 만들지 않는다. 이유: ADR-0021 범위 밖(README "하지 않는 것").
- 종류 이름(`bug_fix`·`code_review`)으로 새 분기를 만들지 않는다. 이유: 업무 종류·후속 규칙은 워크스페이스 등록 데이터다(ADR-0009, AGENTS.md).
- `tasks`·`members`·`notifications` 등 기존 표를 재생성하지 않는다. 이유: v11 은 ALTER + 새 표로 충분하다(README 계획 기본값 11).
- 담당자별 묶음·보드·상세 패널 같은 새 업무 화면을 만들지 않는다. 이유: 16-work-ui 범위.
- 사용자 변경을 삭제하거나 stash·reset 하지 않는다. 커밋은 하네스가 한다.
- 이 step 범위 밖 모듈을 "개선"하지 않는다. 기존 테스트의 단정을 약하게 만들어 통과시키지 않는다.
