# Step 1: team-domain — 비밀번호·역할·받는 사람 순수 함수

## 읽어야 할 파일

- AGENTS.md
- phases/15-team/README.md (조사 결과·계획 기본값 11가지 — 이 phase 의 기준), phases/15-team/index.json (이전 step summary)
- docs/adr/0021-team-accounts-and-roles.md, docs/ARCHITECTURE.md ("팀 — phase 15" 의 해시 형식·역할 × 동작 표·받는 사람 규칙·이름 표)
- src/workflow/domain/work_status.py (순수 함수 모양 참고), src/workflow/domain/notification.py
- tests/workflow/domain/ (기존 도메인 테스트 형식)

먼저 실제 파일을 읽는다. 대화 이력을 전제로 판단하지 않는다. 이전 step 이 정한 이름은 `docs/ARCHITECTURE.md` "팀 — phase 15" 를 따른다(README 와 다르면 ARCHITECTURE 가 기준).

## 작업

`src/workflow/domain/team.py` 를 만든다(외부 I/O 없음 — `hashlib`·`hmac`·`secrets`·`base64` 만).

- `hash_password(password: str, *, n: int = 2**14) -> str` — 무작위 salt 16바이트, ARCHITECTURE 형식 문자열. `verify_password(password: str, stored: str) -> bool` — 저장 문자열의 파라미터로 다시 계산해 `hmac.compare_digest`. 형식이 깨졌거나 모르는 알고리즘이면 예외 대신 `False`.
- `password_problem(password: str) -> str | None` — 규칙 위반이면 사용자에게 보일 한국어 문구, 아니면 `None`(최소 10자, 최대 길이 초과, 공백만).
- `normalize_email(value: str) -> str | None` — 앞뒤 공백 제거·소문자, `@` 하나에 양쪽이 비지 않았을 때만. 복잡한 RFC 검증은 하지 않는다.
- 역할·동작 상수와 `can(role: str, action: str) -> bool` — ARCHITECTURE 표 그대로. 모르는 역할·동작은 `False`.
- `turn_recipients(*, assignee_type: str | None, assignee_id: str | None, requested_by: str | None, active_members: Mapping[str, str]) -> tuple[str, ...]` — `active_members` 는 활성 멤버 id → 역할. 담당이 활성 멤버면 `(그 id,)`, 아니면 맡긴 사람이 활성이면 `(그 id,)`, 아니면 활성 관리자 전원(id 정렬). 결과가 비는 경우(관리자 0)는 빈 튜플.

## 테스트 먼저

`tests/workflow/domain/test_team.py`: 해시 왕복(테스트는 `n=2**4` 로 빠르게)·같은 비밀번호도 해시가 다름·틀린 비밀번호·깨진 저장 문자열 `False`·파라미터가 저장 문자열에서 읽히는지, 비밀번호 규칙 경계값, 이메일 정규화 표, 역할 × 동작 표 전체(관리자는 전부, 멤버는 README 4 목록만), 받는 사람 규칙 경우별(담당 멤버·담당 비활성·담당 Agent + 맡긴 사람·맡긴 사람 비활성·아무도 없음 → 관리자 전원·관리자 0).

소스·배포 파일 변경 전에 `tests/` 미러 경로에 실패 테스트를 작성하고 실패 원인이 의도한 것인지 확인한다(`tdd-guard.sh` 가 테스트 없는 소스 작성을 막는다). 구현 후 해당 테스트와 전체 회귀를 통과시킨다.

## Acceptance Criteria

```bash
python3 -m pytest -q
python3 -m ruff check .
```

위 커맨드와 이 step 의 테스트 항목을 모두 확인한다.

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 규칙을 확인한다: `domain/` 은 FastAPI·sqlite3·HTTPX·subprocess·Git 을 import 하지 않는다 / `server/` 와 `connector/` 는 서로 import 하지 않고 `contracts/` 만 공유한다 / 외부 입력(요청 본문·폼·초대 토큰·이슈 본문·모델 응답)에서 명령·경로를 받아 실행하지 않는다 / 비밀값(연결 토큰, `OPERATOR_TOKEN`, `SESSION_SECRET`, 로그인 세션·초대·재설정 토큰 원문, 비밀번호 원문, GitHub App 비밀·설치 토큰, PAT, 공용·개인 알림 웹훅 URL)은 DB·로그·응답·템플릿·백업에 넣지 않는다(토큰은 sha256, 비밀번호는 scrypt 해시만 DB 에) / GLOSSARY 이름을 그대로 쓴다 / phase 8·11·12·14 의 GitHub 순환(수집 → 수정 → 검토 → 초안 PR → 병합 추적 → 업무 완료)이 그대로 동작한다.
3. 성공이면 `phases/15-team/index.json` 의 step 1 만 `completed` 로 바꾸고 `summary` 에 생성·수정·삭제 파일, 결정, 검증 결과(테스트 수), 다음 step 이 주의할 점을 한 줄로 남긴다.
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
