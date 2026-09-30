# Step 11: team-verify — e2e 팀 한 줄기, v10 사본 마이그레이션, 문서·인계

## 읽어야 할 파일

- AGENTS.md
- phases/15-team/README.md (조사 결과·계획 기본값 11가지 — 이 phase 의 기준), phases/15-team/index.json (이전 step summary)
- docs/adr/0021-team-accounts-and-roles.md, docs/ARCHITECTURE.md ("팀 — phase 15")
- tests/e2e/ (conftest.py, test_real_repo.py, test_github_cycle.py, test_selfhost.py)
- src/workflow/server/backup.py, tests/workflow/server/test_backup.py
- docs/VERIFICATION_LOG.md, docs/CURRENT_HANDOFF.md, docs/SELFHOST.md, docs/product/REDESIGN_PLAN.md, deploy/selfhost/install.sh (설치 뒤 안내 문구)

먼저 실제 파일을 읽는다. 대화 이력을 전제로 판단하지 않는다. 이전 step 이 정한 이름은 `docs/ARCHITECTURE.md` "팀 — phase 15" 를 따른다(README 와 다르면 ARCHITECTURE 가 기준).

## 작업

1. e2e 팀 한 줄기(`tests/e2e/` 새 파일 또는 기존 파일에): 첫 설정(토큰 → 관리자 계정) → 관리자가 멤버 초대 → 멤버 가입·로그인 → 멤버가 [러너 붙이기](가짜 러너, 소유자 = 멤버) → 멤버가 GitHub 업무를 에이전트에게 맡김 → 검토에서 사람 요청 → 멤버의 내 차례에만 보임 + 가짜 수신에 공용(`→ 멤버`)·개인 알림 → 멤버 응답(응답자 기록) → 계속 진행. 관리자 전용 경로를 멤버가 못 여는 것 하나. 다른 Origin 의 POST 403 하나.
2. v10 → v11 사본 확인: 현재 셀프호스트와 같은 모양의 v10 DB(업무 20여 건·첫 관리자 이메일 없음·알림 행·연결 프로그램 1개)를 테스트 안에서 만들어 올리고, 행 보존·첫 설정 필요 판정·기존 러너 소유자 없음·백업 왕복을 단정한다. **사용자의 실제 셀프호스트 볼륨·백업 파일을 읽지 않는다.**
3. 셀프호스트 Docker e2e: `WORKFLOW_DOCKER=1 python3 -m pytest tests/e2e/test_selfhost.py -q` — 먼저 테스트 코드를 읽어 사용자의 compose 프로젝트 `runloom`·포트 8000·볼륨·이미지 태그(`workflow-selfhost:local` 공유 문제 — CURRENT_HANDOFF)와 겹치지 않는지 확인하고, 겹치거나 Docker 가 없으면 실행하지 않고 "미실행(이유)" 로 summary 에 적는다.
4. 문서: `docs/SELFHOST.md` — 업그레이드 v11(백업 먼저, 재설치 뒤 첫 접속에서 `.env` 토큰 → 관리자 계정 만들기, 옛 쿠키 무효라 다시 로그인), 팀원 초대, 비밀번호 분실(재설정 링크·토큰 복구), `WORKFLOW_PUBLIC_URL` 과 원격 접속(Tailscale·Cloudflare Tunnel 안내 — 공개 인터넷에 열 때 https 필수), 개인 웹훅. `install.sh` 설치 뒤 안내 문구를 "처음 접속 때 토큰으로 관리자 계정을 만든다" 로(`tests/test_selfhost_files.py` 단정 함께). `docs/VERIFICATION_LOG.md` "phase 15 팀" 절(명령·결과 수·미실행 항목). `docs/CURRENT_HANDOFF.md` "다음 작업" 을 "15 완료 → service 병합 → 셀프호스트 재설치(사용자 지시) → 16-work-ui 설계" 로. `docs/product/REDESIGN_PLAN.md` 13절 15-team 에 완료·ADR-0021 링크.

## 테스트 먼저

1·2 의 테스트를 먼저 작성해 실패(또는 이미 통과)를 확인한 뒤 진행한다. 제품 코드를 고쳐야 하면 그 결함을 재현하는 테스트를 먼저 쓴다.

소스·배포 파일 변경 전에 `tests/` 미러 경로에 실패 테스트를 작성하고 실패 원인이 의도한 것인지 확인한다(`tdd-guard.sh` 가 테스트 없는 소스 작성을 막는다). 구현 후 해당 테스트와 전체 회귀를 통과시킨다.

## Acceptance Criteria

```bash
python3 -m pytest -q
python3 -m ruff check .
WORKFLOW_E2E=1 python3 -m pytest tests/e2e -q
```

위 커맨드와 이 step 의 테스트 항목을 모두 확인한다.

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 규칙을 확인한다: `domain/` 은 FastAPI·sqlite3·HTTPX·subprocess·Git 을 import 하지 않는다 / `server/` 와 `connector/` 는 서로 import 하지 않고 `contracts/` 만 공유한다 / 외부 입력(요청 본문·폼·초대 토큰·이슈 본문·모델 응답)에서 명령·경로를 받아 실행하지 않는다 / 비밀값(연결 토큰, `OPERATOR_TOKEN`, `SESSION_SECRET`, 로그인 세션·초대·재설정 토큰 원문, 비밀번호 원문, GitHub App 비밀·설치 토큰, PAT, 공용·개인 알림 웹훅 URL)은 DB·로그·응답·템플릿·백업에 넣지 않는다(토큰은 sha256, 비밀번호는 scrypt 해시만 DB 에) / GLOSSARY 이름을 그대로 쓴다 / phase 8·11·12·14 의 GitHub 순환(수집 → 수정 → 검토 → 초안 PR → 병합 추적 → 업무 완료)이 그대로 동작한다.
3. 성공이면 `phases/15-team/index.json` 의 step 11 만 `completed` 로 바꾸고 `summary` 에 생성·수정·삭제 파일, 결정, 검증 결과(테스트 수), 다음 step 이 주의할 점을 한 줄로 남긴다.
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
