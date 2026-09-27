# Step 0: 실제 저장소 순환 설계 고정 — ADR·ARCHITECTURE·계약 예시

## 읽어야 할 파일

- AGENTS.md
- phases/12-real-repo/README.md (계획 기본값 — 이 phase 의 기준)
- phases/12-real-repo/index.json (완료 step 의 summary)
- docs/adr/0018-real-repo-cycle.md (step 0 산출물. step 0 에서는 새로 만든다)
- docs/ARCHITECTURE.md ("실제 저장소 순환 — phase 12" 절. step 0 에서는 새로 만든다)
- docs/GLOSSARY.md
- docs/adr/0014-github-task-cycle.md, docs/adr/0015-measurement-events-and-baseline.md, docs/adr/0017-github-app-connection.md
- docs/ARCHITECTURE.md ("GitHub 업무 순환 — phase 8 계약", "GitHub App 연결 — phase 11", "Codex와 worktree" 절)
- docs/CONTRACT.md (연결 프로그램 등록·claim·결과 이벤트 예시)
- docs/SELFHOST.md (러너 절), docs/product/MVP_PLAN.md (10·11절)
- src/workflow/connector/cli.py, src/workflow/connector/git_ops.py, src/workflow/connector/local_tool.py, src/workflow/connector/masking.py, src/workflow/connector/state.py
- src/workflow/server/machine_api.py (`/connector/registrations`), src/workflow/server/worker.py (`_start_fix`, 검토 `approved` 처리, `_deliver_callbacks`, `create_human_request_once` 호출부), src/workflow/adapters/github_app.py (manifest `default_permissions`)
- src/workflow/contracts/v1.py (`RegistrationRequest`, `ClaimRequest`, `ResultReadyData`)

먼저 실제 파일과 이전 step 산출물을 읽는다. 이름·경로·API 필드는 step 0 의 ADR-0018·ARCHITECTURE 가 고정한 것을 따르고, 바꿔야 하면 같은 이름의 문서·테스트를 함께 갱신한다. 대화 이력을 전제로 판단하지 않는다.

## 작업

문서만 바꾼다(제품 코드 없음). README "계획 기본값" 여섯 가지를 결정 기록으로 옮기고 이후 step 이 쓸 이름을 고정한다.

1. **공식 문서로 사실 확인** (WebFetch 가능하면 사용, 불가하면 "미확인"으로 표시): GitHub REST `POST /repos/{owner}/{repo}/pulls`(`head`·`base`·`draft`·`title`·`body`, 같은 head 로 이미 열린 PR 이 있을 때 422 본문), `GET /repos/{owner}/{repo}/pulls?head=owner:branch&state=all`, `GET /repos/{owner}/{repo}`(`default_branch`), PR 생성에 필요한 App 권한(Pull requests write, Contents read 필요 여부), 설치된 App 의 권한을 늘릴 때 설치 소유자 승인 흐름, 닫는 키워드(`Fixes #N`)가 기본 브랜치 병합 때 이슈를 닫는 조건. Discord 웹훅 실행 본문(`content` 2000자 제한, 알 수 없는 필드 처리, 429 `retry_after`).
2. `docs/adr/0018-real-repo-cycle.md` 신설. 결정:
   - **러너 한 명령 설정**: `python3 -m workflow.connector setup --server URL --code CODE --repo 폴더 [--verify 이름=명령 ...]` = connect + register. 운영자가 에이전트를 미리 만들지 않아도 register 가 에이전트를 만든다(이름=폴더 이름, 능력=`code.fix`+`code.review`, `repository_id`=러너가 보고한 GitHub owner/name, 없으면 폴더 이름). 미리 만든 에이전트(`local_registration_id` 일치) 경로는 그대로 호환.
   - **기준 커밋 추적**: 새 수정 업무는 GitHub 기본 브랜치 최신 커밋에서 출발한다. 러너가 claim 전에 등록 폴더에서 `git fetch origin` 을 고정 인자로 실행하고, 등록별 `origin/<기본 브랜치>` 커밋을 claim 요청의 선택 칸으로 보고한다. 서버는 그 값을 `agents.base_commit` 에 반영한다. 재작업은 지금처럼 검토한 결과 커밋에서 출발. fetch 실패는 이전 값을 유지하고 러너 로그에만 남긴다. 이유: 사용자는 다른 곳에서 개발해 GitHub 에 push 하고 러너 폴더는 뒤처진다.
   - **작업 복사본 준비물**: 러너 로컬 등록에 `--link 경로`(반복)와 `--env 이름=값`(반복)을 선언한다. worktree 를 새로 만든 직후 `link` 경로마다 원본 폴더의 같은 경로를 가리키는 심볼릭 링크를 걸고(대상이 원본에 없으면 건너뛰고 로그), 그 경로를 git 제외 목록(`info/exclude`)에 넣어 커밋에 섞이지 않게 한다. `env` 는 검증 프로세스와 에이전트 도구 프로세스의 환경에 더한다. 값과 이름은 러너 로컬에만 있고 중앙에 보내지 않는다. Runloom 비밀값 이름(AGENTS.md 목록)·`WORKFLOW_*`·`PATH`·`HOME` 은 등록 때 거부한다.
   - **결과 코드 전달 = 초안 PR**: 수정 실행이 `ready_for_review` 를 내면 러너가 `task/<task_id>` 브랜치를 등록 폴더의 `origin` 에 push 한다(고정 인자, force 없음, 사용자의 로컬 git 자격 사용, 결과를 `ResultReadyData` 선택 칸으로 보고). 검토가 `approved` 면 중앙이 소스의 GitHub 자격으로 초안 PR(head=`task/<task_id>`, base=기본 브랜치, 본문에 `Fixes #N`·검토 요약·Runloom 업무 링크)을 연다. 같은 head 의 PR 이 이미 있으면 그것을 쓴다(멱등). 초안 PR 이 지원되지 않는 저장소면 일반 PR 로 한 번 더 시도. 동기화가 PR 상태를 조회해 병합되면 수정 Task 를 완료(병합 시각 기록), 병합 없이 닫히면 종료한다. 병합·이슈 닫기는 사람만. ADR-0014 의 "자동 push/PR 은 하지 않는다"를 이 결정으로 대체한다고 적고 ADR-0014 에 한 줄 덧붙인다. manifest `default_permissions` 에 `pull_requests: write`(및 1 에서 확인한 최소 권한)를 넣고, 이미 만든 App 은 사용자가 GitHub App 설정에서 권한을 올리고 설치에서 승인해야 한다고 적는다.
   - **알림 웹훅**: 사람 차례(새 사람 요청, PR 열림)와 업무 실패 때 등록된 URL 하나로 POST. URL 은 토큰을 담으므로 비밀 저장소 파일에 둔다. 호스트가 `discord.com`/`discordapp.com` 이면 `{"content": 문구}`, 그 밖은 `{"content", "event", "task_id", "task_url", "title", "pr_url"}` JSON. 워커가 DB 대기열(스키마 v8)로 보내고 재시도(callback 과 같은 백오프, 상한 5회). 알림 실패가 업무 상태를 바꾸지 않는다.
   - **[러너 붙이기]**: GitHub 저장소 카드의 "러너 없음" 안내를 버튼으로 바꾼다. 누르면 연결 코드를 발급하고 복사할 명령 한 줄(`deploy/selfhost/install-runner.sh --server … --code … --repo <폴더>`)을 보여 준다. 폴더 경로만 사용자가 채운다.
   대안·기각 이유(로컬 HEAD 추적, 업무마다 의존성 설치, 중앙이 push, 자동 병합, 이메일 알림)를 적는다.
3. `docs/ARCHITECTURE.md` 에 "실제 저장소 순환 — phase 12" 절: 흐름(러너 붙이기 → setup → 자동 매칭 → fetch·기준 커밋 보고 → 수정 → push → 검토 → 초안 PR → 사람 병합 → 완료), 계약 변경 표(모두 선택 칸 추가 — 이름 확정: 예 `ClaimRequest.registration_heads: dict[str, CommitSha]`, `ResultReadyData.branch_pushed: bool | None`), 러너 로컬 등록 새 칸(`links`, `env` — 로컬 DB 에 칸을 더하는 방식 포함), 스키마 v8 표(PR 기록·알림 대기열 — 테이블·칸 이름 확정), 비밀 파일 이름(`SecretStore.NAMES` 추가분), 새 경로 표(알림 설정·테스트 보내기·러너 붙이기), 업무 상태 문구(PR 열림 → "사람 차례 · PR 확인", 병합 → 완료), 이름·시그니처 표.
4. `docs/CONTRACT.md` 에 새 선택 칸 예시 블록(claim 의 기준 커밋 보고, result_ready 의 push 결과, 에이전트를 새로 만드는 register 응답). 예시 블록 수를 세는 기존 테스트가 있으면 함께 갱신한다.
5. `docs/GLOSSARY.md` 용어(러너 붙이기, 기준 커밋 보고, 작업 복사본 준비물, 초안 PR, 알림 웹훅), `docs/CURRENT_HANDOFF.md` 에 "12-real-repo 진행 중" 한 줄.

## 테스트 먼저

문서 전용 step 이라 제품 테스트를 새로 만들지 않는다. 기존 문서 검사(링크·CONTRACT 예시 파싱 등)가 통과해야 한다.

소스·배포 파일 변경 전에 `tests/` 미러 경로에 실패 테스트를 작성하고 실패 원인이 의도한 것인지 확인한다(`tdd-guard.sh` 가 테스트 없는 소스 작성을 막는다). 구현 후 해당 테스트와 전체 회귀를 통과시킨다.

## Acceptance Criteria

```bash
python3 -m pytest -q
python3 -m ruff check .
```

위 커맨드와 이 step 의 테스트 항목을 모두 확인한다.

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 규칙을 확인한다: `domain/` 은 FastAPI·sqlite3·HTTPX·subprocess·Git 을 import 하지 않는다 / `server/` 와 `connector/` 는 서로 import 하지 않고 `contracts/` 만 공유한다 / 외부 입력(요청 본문·이슈 본문·모델 응답)에서 명령·경로·브랜치 이름을 받아 실행하지 않는다 — git 인자 배열은 러너 어댑터가 고정한다 / 비밀값(연결 토큰, GitHub App 비밀·설치 토큰, PAT, 알림 웹훅 URL, 러너 로컬 등록의 환경변수 값)은 DB·로그·응답·템플릿·백업·중앙에 넣지 않는다 / 기본 모드(demo)와 phase 8·11 의 기존 GitHub 순환이 그대로 동작한다.
3. 성공이면 `phases/12-real-repo/index.json` 의 step 0 만 `completed` 로 바꾸고 `summary` 에 생성·수정 파일, 결정, 검증 결과(테스트 수), 다음 step 이 주의할 점을 한 줄로 남긴다.
4. 3회 수정 후에도 실패하면 `error` + `error_message`, 사용자 결정·외부 자원이 필요하면 `blocked` + `blocked_reason` 을 기록한다. 실행하지 않은 검증을 `completed` 로 표시하지 않는다.

## 금지사항

- 실제 GitHub(api.github.com·github.com)와 실제 Discord 를 호출하지 않는다(docs.github.com·discord.com/developers 문서 읽기는 허용). 이유: 모든 step 은 httpx `MockTransport`·가짜 GitHub·로컬 bare 저장소로 검증한다. 실연동은 phase 뒤 사용자와 함께 한다.
- `/Users/kje/demo/OpenArchive` 와 그 worktree 를 읽거나 바꾸지 않는다. 이유: 사용자가 개발 중인 실제 저장소다. 테스트는 임시 디렉터리의 가짜 저장소로 한다.
- 사용자가 띄워 둔 셀프호스트(compose 프로젝트 `runloom`, 포트 8000, 볼륨 `runloom_workflow-data`)·`deploy/selfhost/.env`·러너 홈(`~/Library/Application Support/workflow-connector/`)·launchd 를 건드리지 않는다. 컨테이너를 띄우거나 멈추지 않는다.
- 러너가 기본 브랜치에 push 하거나 force push 하지 않는다. 병합·이슈 닫기는 하지 않는다 — 사람만 한다.
- `main`·공개 데모 VM·demo 모드 기본 동작을 바꾸지 않는다.
- 사용자 변경을 삭제하거나 stash·reset 하지 않는다. 커밋은 하네스가 한다.
- 이 step 범위 밖 모듈을 "개선"하지 않는다.
