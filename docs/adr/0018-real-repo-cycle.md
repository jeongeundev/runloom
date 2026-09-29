# ADR-0018: 실제 저장소 순환 — 러너 한 명령·최신 기준 커밋·작업 복사본 준비물·초안 PR·알림 웹훅

결정일: 2026-09-27 (phase 12 step 0). 기본값은 [phase 12 README](../../phases/12-real-repo/README.md) "계획 기본값"(사용자 결정 2026-09-27)이며 이 문서로 구현 기준을 고정한다. 계기: 실제 저장소(OpenArchive)에 붙이려 하자 (1) 러너 등록이 운영자 에이전트 선등록 + `connect` + `register --id --repository-id --verify` 네 단계였고, (2) 새 업무 기준 커밋이 `register` 때 HEAD 로 고정돼 로컬 클론이 `origin/main` 보다 12 커밋 뒤였으며, (3) worktree 에 `backend/.venv`·`frontend/node_modules`·`DATABASE_URL` 이 없어 검증이 돌 수 없었고, (4) 결과 코드가 러너 Mac 의 `task/<task_id>` 브랜치에만 남아 사람이 폴더를 찾아가 push·PR 해야 했고, (5) 사람 차례를 알려 주는 범용 알림이 없었다. 이 시점에는 구현이 없다 — 아래 이름·경로는 step 1~10 이 만들고, 바꿀 때는 이 ADR·[ARCHITECTURE](../ARCHITECTURE.md) "실제 저장소 순환 — phase 12"·[CONTRACT](../CONTRACT.md) 14절·[GLOSSARY](../GLOSSARY.md)·테스트를 같이 고친다.

## 결정

1. **러너 한 명령 설정.** `python3 -m workflow.connector setup --server URL --code CODE --repo 폴더 [--tool claude|codex] [--verify 이름=명령 ...] [--link 경로 ...] [--env 이름=값 ...]` 은 `connect` + `register` 를 한 번에 한다(`--code` 를 빼면 저장된 토큰으로 `register` 만). `register` 의 `--id`·`--repository-id` 는 선택이 된다 — `--id` 기본 = 폴더 이름(파일 이름 안전 문자로), `--repository-id` 기본 = 러너가 찾은 GitHub `owner/name`(ADR-0017 결정 6 의 `found.github_repository`), 없으면 폴더 이름. `setup` 의 `--tool` 기본은 PATH 에 있는 `claude`, 없으면 `codex`, 둘 다 없으면 `claude`(구독 에이전트 실사용 기준, step 2), `register` 의 기본 `codex` 는 호환을 위해 그대로다. `setup` 은 같은 서버(끝 `/` 무시)의 `token.json` 이 있으면 `connect` 를 건너뛴다 — 코드는 1회용이고, 새 연결은 옛 등록과 409 `registration_taken` 으로 부딪힌다.
   - 서버는 `local_registration_id` 가 일치하는 Agent 가 있으면 지금처럼 연결 정보만 채운다(운영자가 미리 만든 Agent 경로 호환). 없고 서버가 `selfhost` 모드면 Agent 를 만든다: 이름 = 요청의 `agent_name`(러너가 폴더 이름을 보냄, 없으면 `local_registration_id`), `owner_scope` `personal`, `connection_type` `local`, 능력 `code.fix`·`code.review`(둘 다 scope `repository_id` = 보고값), 고정 워크스페이스(`SELFHOST_SESSION_ID`)에 등록. 같은 요청을 다시 보내면 같은 Agent 를 갱신한다(멱등 — 키는 `local_registration_id`). `demo` 모드는 지금처럼 404(공개 데모 동작을 바꾸지 않는다). selfhost 에서 그 이름을 취소되지 않은 다른 연결 프로그램이 쓰고 있으면 409 `registration_taken` 으로 거부한다(한 등록을 두 러너가 번갈아 덮어쓰지 않게, step 1).
   - 수정과 검토는 같은 Agent 가 맡을 수 있다. 실행은 따로(수정 worktree `task/<task_id>`, 검토는 결과 커밋의 읽기 전용 체크아웃)이며, ADR-0014 결정 5 의 "같은 연결 프로그램·같은 `repository_id`" 조건을 그대로 만족한다.
   - `deploy/selfhost/install-runner.sh --server URL --code CODE --repo 폴더 [--tool …]` 가 패키지 설치 → `setup` → launchd 적재를 한 번에 한다. 인자 없는 기존 호출은 지금 동작 그대로다.
2. **기준 커밋 = GitHub 기본 브랜치 최신.** 새 수정 업무는 등록 폴더의 `origin` 기본 브랜치 최신 커밋에서 출발한다. 러너는 claim 전에 등록마다(최소 간격 `BASE_FETCH_INTERVAL_SECONDS` = 60초) 등록 폴더에서 고정 인자로 `git fetch --quiet origin` 을 실행하고(`GIT_TERMINAL_PROMPT=0`, 제한 시간 있음), `refs/remotes/origin/HEAD` 가 가리키는 커밋을 읽는다(없으면 `git remote set-head origin --auto` 를 한 번 실행). 등록별 결과를 claim 요청의 선택 칸 `registration_heads`(`local_registration_id` → 커밋)로 보고하고, 서버는 그 연결 프로그램의 Agent 에 한해 `agents.base_commit` 을 갱신한다. 재작업은 지금처럼 검토한 결과 커밋에서, 같은 Task 의 재시도는 지금처럼 마지막 결과 커밋에서 출발한다(`_start_fix` 의 우선순위 그대로). fetch 실패·`origin` 없음은 그 등록을 보고하지 않아 이전 값이 유지되고, 러너 로그에만 남는다. 원격 이름·브랜치 이름을 외부 입력에서 받지 않는다 — `origin`·`refs/remotes/origin/HEAD` 는 코드 상수다.
   - 이유: 사용자는 다른 곳(다른 폴더·다른 기기)에서 개발해 GitHub 에 push 하고, 러너 폴더는 뒤처진다. 러너 폴더의 로컬 HEAD 는 사람이 체크아웃한 아무 브랜치일 수 있다.
3. **작업 복사본 준비물.** 러너 로컬 등록에 `--link 경로`(반복)와 `--env 이름=값`(반복)을 선언한다. 둘 다 러너 로컬 상태 DB 에만 두고 중앙에 보내지 않는다(이름도 보내지 않는다).
   - `link`: 등록 폴더 기준 상대 경로(절대 경로·`..`·`.git` 구성 요소 거부). worktree 를 **새로 만든 직후** 경로마다 원본 폴더의 같은 경로를 가리키는 심볼릭 링크를 건다. 원본에 대상이 없거나 worktree 에 이미 그 경로가 있으면(추적 파일) 건너뛰고 러너 로그에 남긴다. 링크 경로는 저장소 공용 git 디렉터리의 `info/exclude` 에 `/<경로>` 로 한 번 넣는다 — `.gitignore` 의 `node_modules/` 처럼 끝이 `/` 인 규칙은 심볼릭 링크(디렉터리가 아님)에 맞지 않아 커밋에 섞이기 때문이다. 링크된 대상은 원본 폴더와 공유되므로 에이전트가 그 안을 바꾸면 원본도 바뀐다(설치물 공유의 대가, 문서에 명시).
   - `env`: 검증 프로세스(`--verify` 명령)와 에이전트 도구 프로세스의 환경에 더한다(허용 목록 `codex_env` 위에 덧붙임). 등록 때 거부하는 이름: [AGENTS.md](../../AGENTS.md) 비밀값 목록의 환경변수(`OPERATOR_TOKEN`·`DIAG_API_TOKEN`·`OPENAI_API_KEY`·`SESSION_SECRET`·`WORKFLOW_GITHUB_TOKEN`), `WORKFLOW_` 로 시작하는 이름, `PATH`, `HOME`, 이름 형식(`[A-Za-z_][A-Za-z0-9_]*`) 위반. 값은 업로드 전 산출물·진행 메시지에서 가린다(`<env:이름>`).
   - `copy`(2026-09-29 실연동 준비에서 추가): `link` 와 같은 검사·건너뛰기·`info/exclude` 규칙으로 원본 폴더의 경로를 복사한다(macOS 는 APFS 복제 `cp -Rc`, 그 밖은 일반 복사). 이유: OpenArchive 검증의 `next build`(Turbopack)가 작업 복사본 밖을 가리키는 `node_modules` 링크를 "points out of the filesystem root" 로 거부했다. 실측: 570 패키지 `node_modules` 복제 4.6초, 빌드 통과. 러너 로컬 칸 `copies_json`.
   - 테스트 DB 는 에이전트 전용으로 따로 띄워 `--env DATABASE_URL=…` 로 넘긴다(README 계획 기본값 4 — 제품 코드 변경 없음, 실연동 준비물).
4. **결과 코드 전달 = 초안 PR.** [ADR-0014](0014-github-task-cycle.md) 결정 5·6 의 "자동 push/PR 은 하지 않는다"를 이 결정으로 대체한다.
   - push(러너): 수정 실행이 `ready_for_review` 결과 커밋을 내면, 등록 폴더에 `origin` 이 있으면 러너가 `git push origin refs/heads/task/<task_id>:refs/heads/task/<task_id>` 를 고정 인자로 실행한다(force 없음, 사용자 로컬 git 자격 사용, `GIT_TERMINAL_PROMPT=0`). 결과를 `result_ready` 이벤트의 선택 칸 `branch_pushed`(`true` 성공 / `false` 시도했으나 실패 / 칸 없음 = 시도 안 함·구버전)로 보고한다. push 실패는 실행을 실패시키지 않는다 — 결과 판정은 지금 규칙 그대로다. 러너는 기본 브랜치에 push 하지 않는다(대상 ref 가 `refs/heads/task/…` 로 고정).
   - PR(중앙): 검토가 `approved` 이고 그 검토가 본 수정 실행이 `branch_pushed = true` 이며 수정 Task 에 원본 GitHub 이슈가 있으면, 워커가 PR 대기열(`task_pull_requests`, 스키마 v8)에 한 행을 넣고 트랜잭션 밖에서 소스의 GitHub 자격(ADR-0017 클라이언트 선택 그대로)으로 연다: ① `GET /repos/{o}/{r}/pulls?head={o}:task/<task_id>&state=all` 에 있으면 그것을 쓴다(멱등) ② 없으면 `GET /repos/{o}/{r}` 의 `default_branch` 를 base 로 `POST /repos/{o}/{r}/pulls`(`head`=`task/<task_id>`, `base`, `draft: true`, 제목 = 이슈 제목, 본문 = `Fixes #N`·검토 요약·Runloom 업무 링크) ③ 422 가 draft 미지원이면 `draft: false` 로 한 번 더 ④ 그 밖의 422 는 ① 을 다시 조회해 있으면 쓰고 없으면 실패로 기록한다. 실패는 callback 과 같은 백오프로 최대 5회 재시도하고, 끝내 못 열면 수정 Task 를 지금 문구(`확인 필요 · 검토 승인 — 병합·이슈 종료는 사람`)에 사유를 더해 둔다. (step 6 구현: 끝내 못 열거나 권한 부족 403·자격 없음이면 바로, 또 `branch_pushed = false` 면 대기열 없이 사람 요청 `pr_unavailable` 을 만든다 — 사유에 직접 push·PR 안내 한 줄. 422 재시도·재조회는 클라이언트 `create_pull_request` 안에 있다.)
   - 추적: 기존 GitHub 주기 조회가 열린 PR 을 `GET /repos/{o}/{r}/pulls/{number}` 로 본다. 병합(`merged_at` 있음)되면 수정 Task 를 `완료`(병합 시각 기록 — 지표의 `pr_merged_at` 은 기존 이슈 병합 PR 조회가 채운다), 병합 없이 닫히면 `실패 · PR 이 병합 없이 닫힘` 으로 마감한다. PR 이 열려 있는 동안 수정 Task 는 `확인 필요 · 사람 차례 · PR 확인`.
   - 병합·이슈 닫기·리뷰 승인은 사람만 한다. `Fixes #N` 은 기본 브랜치로 병합될 때만 이슈를 닫으므로(GitHub 규칙) base 는 항상 기본 브랜치다.
   - App 권한: manifest `default_permissions` 의 `pull_requests` 를 `read` → `write` 로 올린다(PR 생성·조회에 필요한 최소 권한 — 공식 표에서 `POST /repos/{o}/{r}/pulls` 는 Pull requests 쓰기, `GET /repos/{o}/{r}` 는 Metadata 읽기. Contents 권한은 필요 없다 — 코드는 러너가 사용자 자격으로 push 한다). 이미 만든 App(`runloom-gwufov`)은 사용자가 GitHub App 설정에서 Pull requests 를 Read and write 로 올리고, 설치 화면에서 새 권한을 승인해야 적용된다(승인 전에는 PR 생성이 403 → 위 실패 경로).
5. **알림 웹훅.** 사람 차례와 업무 실패를 등록된 URL 하나로 POST 한다. 대상 사건: 새 사람 요청(`human_request`), PR 열림(`pr_opened`), 실행 실패 반영(`task_failed` — 사람이 닫은 PR·운영자 종료는 제외).
   - URL 은 토큰을 담으므로(Discord 웹훅 URL 자체가 비밀) 비밀 저장소 파일 `notify_webhook_url` 에 둔다. DB·로그·응답·템플릿·백업에 넣지 않고 화면은 "설정됨/없음"과 호스트 이름만 보인다.
   - 본문: 호스트가 `discord.com`·`discordapp.com`(하위 도메인 포함)이면 `{"content": 문구}`(2000자에서 자른다) 만, 그 밖은 `{"content", "event", "task_id", "task_url", "title", "pr_url"}` JSON(모르는 값은 null).
   - 전달: 알림 URL 이 설정돼 있으면 사건이 생기는 워커 트랜잭션 안에서 DB 대기열(`notifications`, 스키마 v8)에 넣고(중복 키로 한 번만 — URL 이 없으면 쌓지 않는다, step 7), 워커가 트랜잭션 밖에서 보낸다. 2xx 면 보냄, 실패는 callback 과 같은 백오프(30초 × 2^(n−1))로 최대 5회, Discord 429 는 `retry_after`(초) 와 백오프 중 큰 값 뒤에 다시. 보낼 때 URL 이 없으면 `skipped`(나중에 URL 을 넣어도 옛 알림을 몰아 보내지 않는다). 알림 실패는 업무 상태를 바꾸지 않는다.
   - [테스트 보내기] 는 대기열을 거치지 않고 즉시 한 번 보내 결과(성공/HTTP 상태)를 화면에 보인다.
6. **[러너 붙이기].** `/operator/github` 저장소 카드의 "이 저장소를 등록한 러너 없음" 안내를 [러너 붙이기] 버튼으로 바꾼다. 누르면 연결 코드(1회용·10분, 기존 `connect_codes`)를 발급하고 복사할 명령 한 줄 `deploy/selfhost/install-runner.sh --server <서버 주소> --code <코드> --repo <폴더>` 를 그 자리에서 보여 준다(리다이렉트 URL 에 코드를 싣지 않는다). 사용자는 `<폴더>` 만 채워 Runloom 설치 폴더에서 실행한다. 서버 주소는 `WORKFLOW_PUBLIC_URL` 이 있으면 그것, 없으면 요청의 base URL.

## 대안

- **로컬 HEAD 추적(러너가 등록 폴더 HEAD 를 매번 보고).** 사용자가 러너 폴더에서 다른 브랜치를 체크아웃하거나 push 전 커밋을 두면 그것이 기준이 된다. 사용자는 다른 곳에서 개발해 GitHub 에 push 하므로 GitHub 기본 브랜치가 진실이다. 기각.
- **업무마다 의존성 설치(`npm install`·`pip install` 을 worktree 에서).** 업무마다 수 분·네트워크·디스크가 들고, 설치 명령을 어디서 받을지(외부 입력에서 명령을 받지 않는다) 문제가 생긴다. 원본 폴더 설치물을 링크하는 쪽을 사용자가 골랐다. Next 가 링크된 `node_modules` 를 거부하면 그 경로만 검증 명령 안의 설치로 바꾼다(실연동 때 판단).
- **중앙이 push(코드를 중앙으로 보내거나 중앙이 저장소에 쓰기).** App 에 Contents 쓰기 권한과 코드 전송 경로가 필요하고, 셀프호스트 컨테이너에 저장소 사본이 생긴다. 러너가 이미 사용자 자격으로 저장소를 다루므로 러너가 push 한다. 기각.
- **자동 병합(검토 승인 = 병합).** 에이전트끼리의 승인으로 기본 브랜치가 바뀐다. 병합·이슈 닫기는 사람의 결정으로 남긴다(ADR-0014 결정 6 유지). 기각.
- **이메일 알림.** SMTP 자격·발신 도메인·스팸 처리가 필요하다. 웹훅 하나면 Discord·Slack·n8n 어느 쪽으로도 이어진다. 기각.
- **알림 URL 을 DB 에 저장.** 웹훅 URL 은 그 자체로 쓰기 권한이라 백업·로그로 퍼진다. ADR-0017 결정 4 의 비밀 저장소를 쓴다. 기각.

## 결과

- 러너 계약 `contract_version` 은 1 그대로다. 추가는 모두 선택 칸이다: `ClaimRequest.registration_heads`, `ResultReadyData.branch_pushed`, 등록 요청 `agent_name`, 등록 응답 `created`. 구버전 서버는 새 칸을 422 로 거부하므로 업그레이드 순서는 서버 → 러너(ADR-0014 결정 4 와 같다). 새 러너는 값이 없을 때 칸을 보내지 않는다.
- 스키마 v8(step 6·7): PR 대기열 `task_pull_requests`, 알림 대기열 `notifications`, `executions.branch_pushed`. v7 데이터는 보존 마이그레이션.
- 비밀 파일 하나 추가(`notify_webhook_url`). `SecretStore.NAMES` 가 여섯 개가 된다.
- 러너 로컬 상태 DB `registrations` 에 `links_json`·`env_json` 칸(기존 DB 는 `ALTER TABLE … ADD COLUMN` 기본값 `'[]'`·`'{}'`).
- 기본 모드(demo)·공개 데모·phase 8 `filtered` 소스·phase 11 순환은 그대로 동작한다: 구버전 러너는 `registration_heads`·`branch_pushed` 를 보내지 않아 기준 커밋·PR 경로가 지금 그대로이고, 알림 URL 이 없으면 알림은 쌓이지 않는다(step 7).
- 모든 step 은 가짜 GitHub(`MockTransport`)·임시 bare 저장소·가짜 알림 수신으로 검증한다. 실제 App 권한 올리기·PR·Discord 는 phase 뒤 사용자와 함께 한다.

## 사실 확인 (2026-09-27, 공식 문서)

확인함:
- `POST /repos/{owner}/{repo}/pulls` — 본문 `head`(필수, "같은 네트워크의 교차 저장소면 `username:branch`"), `base`(필수, 대상 저장소에 있는 브랜치), `title`, `body`, `draft`(boolean), `head_repo`. 응답 201·403·422("Validation failed, or the endpoint has been spammed"). [REST: Pulls](https://docs.github.com/en/rest/pulls/pulls?apiVersion=2022-11-28)
- 초안 PR 가용성: "public repositories with GitHub Free …, and in public and private repositories with GitHub Team and GitHub Enterprise Cloud" — Free 계정의 비공개 저장소는 초안을 못 만든다(결정 4 ③ 의 근거). 같은 문서.
- `GET /repos/{owner}/{repo}/pulls` — `head` = `user:ref-name`(또는 `organization:ref-name`), `state` = `open`·`closed`·`all`. 같은 문서.
- `GET /repos/{owner}/{repo}` 응답에 `default_branch`(필수 문자열). [REST: Repositories](https://docs.github.com/en/rest/repos/repos?apiVersion=2022-11-28#get-a-repository)
- App 권한: PR 생성은 "Pull requests" 쓰기, PR 목록은 Pull requests 읽기, 저장소 조회는 Metadata 읽기. PR 생성은 Contents 권한 목록에 없다. [Permissions required for GitHub Apps](https://docs.github.com/en/rest/authentication/permissions-required-for-github-apps?apiVersion=2022-11-28)
- 권한 추가 승인: "When you add new repository or organization permissions for an app, each account where the app is installed will need to approve the new permissions", "Updated permissions won't take effect … until the new permissions are approved". [Modifying a GitHub App registration](https://docs.github.com/en/apps/maintaining-github-apps/modifying-a-github-app-registration)
- 닫는 키워드: `close(s/d)`·`fix(es/ed)`·`resolve(s/d)`. 기본 브랜치로 병합될 때만 이슈를 닫고, 다른 브랜치를 대상으로 하면 무시된다. [Linking a pull request to an issue](https://docs.github.com/en/issues/tracking-your-work-with-issues/using-issues/linking-a-pull-request-to-an-issue)
- Discord 웹훅 실행: `content` "up to 2000 characters", `content`·`embeds`·`components`·`file`·`poll` 중 하나 필수, `wait` 기본 false 면 `204 No Content`. [Discord: Webhook](https://docs.discord.com/developers/resources/webhook)
- Discord 429: 본문 `retry_after`(초, 소수 가능)·`global`, `Retry-After` 헤더도 같은 초. [Discord: Rate Limits](https://docs.discord.com/developers/topics/rate-limits)

미확인(구현은 이에 기대지 않게 설계):
- 같은 head 로 이미 열린 PR 이 있을 때 422 본문의 정확한 모양(알려진 형태는 `errors[].message` 에 "A pull request already exists for …"). 공식 문서에 예시가 없다 — 결정 4 는 422 본문을 해석하지 않고 목록 재조회로 판단한다.
- 초안 미지원 저장소의 422 문구. 결정 4 ③ 은 422 메시지·`errors` 에 `draft`(대소문자 무시)가 들어 있으면 미지원으로 본다 — 실연동 때 실제 문구를 [VERIFICATION_LOG](../VERIFICATION_LOG.md)에 남긴다.
- Discord 가 모르는 필드를 무시하는지 거부하는지. 문서에 없다 — 그래서 Discord 호스트에는 `content` 만 보낸다.
