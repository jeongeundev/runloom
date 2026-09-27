# GitHub 업무 순환 — 셀프호스트 운영자 런북

작성일: 2026-09-23 (phase 8 step 15). 갱신: 2026-09-27 (phase 11 step 9 — 0절 GitHub App 연결). 계약은 [ADR-0014](../adr/0014-github-task-cycle.md), 이름·표는 [ARCHITECTURE](../ARCHITECTURE.md#github-업무-순환--phase-8-계약), 예시 payload 는 [CONTRACT](../CONTRACT.md) 13절, 용어는 [GLOSSARY](../GLOSSARY.md).

상태: `service` 에 병합되어 있다(미배포). 대역(MockTransport·127.0.0.1 가짜 GitHub·가짜 codex·임시 Git 저장소) 검증에 이어 **2026-09-23 실제 GitHub·실제 Claude 로 1회 통과했다**(step 16, `claude` 2.1.280, 비공개 테스트 저장소의 버그 이슈 2건 → 수정 → 검토 승인 → 원본 댓글, 사람 조작 0회) — [VERIFICATION_LOG 실연동 절](../VERIFICATION_LOG.md). **다만 `changes_requested` 재작업 경로는 실연동에서 관찰되지 않았다**(두 검토가 모두 승인). 다른 저장소로 시작할 때는 아래 [실연동 체크리스트](#실연동-체크리스트--step-16)의 값을 운영자가 먼저 정한다. 공개 데모(`main`·VM)는 이 기능을 쓰지 않는다 — 두 환경변수를 비워 둔다.

## 무엇을 하고 무엇을 하지 않나

| 한다 | 하지 않는다 |
|---|---|
| 허용한 저장소의 open Issue 를 REST 폴링으로 가져와 `bug_fix` Task 로 만든다 — App·붙여 넣은 토큰 연결은 열린 이슈 전부(실행은 지시한 것만), 환경변수 토큰의 라벨 범위 소스는 지정한 범위만 | webhook·OAuth 로그인·여러 워크스페이스 공유 |
| 담당자(GitHub 사용자 숫자 ID)에 연결된 로컬 Agent 가 같은 기기의 저장소에서 수정·검증한다 | 담당자를 자동 추정하거나 웹 사용자와 같은 사람으로 보기 |
| 판정 통과한 결과 커밋을 같은 로컬 저장소의 검토 Agent 가 읽기 전용으로 검토한다(`code_review`) | 커밋을 다른 기기로 옮기기, push·PR·merge·이슈 종료 |
| `changes_requested` 면 같은 수정 Task 를 `max_rework_rounds` 번까지 재작업하고, 넘으면 사람에게 묻는다 | 검토 승인만으로 업무를 끝내기 — 수정 Task 는 `확인 필요 · 검토 승인 — 병합·이슈 종료는 사람` 으로 남는다 |
| Task 마다 원본 이슈에 댓글 하나를 만들고 갱신한다(marker `<!-- runloom:task=<task_id> -->`) | 댓글 내용을 명령·응답·승인으로 읽기, 후속 Task 를 GitHub 이슈로 복제 |

GitHub 에 쓰는 요청은 댓글 생성(`POST …/issues/{n}/comments`)·수정(`PATCH …/issues/comments/{id}`) 두 가지뿐이다(`adapters/github_client.py` 에 다른 쓰기 메서드가 없다).

## 0. GitHub App 연결 — 기본 (phase 11)

2026-09-27 phase 11 부터 연결의 기본은 사용자 자신의 GitHub App 이다([ADR-0017](../adr/0017-github-app-connection.md), [ARCHITECTURE "GitHub App 연결 — phase 11"](../ARCHITECTURE.md#github-app-연결--phase-11)). 1~4절(환경변수 토큰·라벨 범위 소스·담당자 숫자 ID)은 그대로 동작하는 **예전·고급 방식**이다. 버튼 순서(사용자가 GitHub 화면에서 누르는 것)는 [SELFHOST "GitHub 연결"](../SELFHOST.md#github-연결).

| 무엇 | App 연결(`intake: all_open`) | 예전 방식(`intake: filtered`) |
|---|---|---|
| 자격 | App 개인 키 → App JWT → 설치 토큰(메모리, 만료 5분 전 갱신). 비밀은 `WORKFLOW_SECRET_DIR` 의 0600 파일 | `WORKFLOW_GITHUB_TOKEN` 또는 화면에서 붙여 넣은 PAT(비밀 파일 `github_token`, 환경변수보다 우선) |
| 허용 저장소 | App 설치에서 고른 저장소 | `WORKFLOW_GITHUB_REPOS`(붙여 넣은 PAT 는 확인한 저장소도) |
| 가져오는 이슈 | 열린 이슈 전부(PR·닫힘 제외, 연결 전 백로그 포함) | 라벨·시작 시각·고른 번호 |
| 실행 | 지시한 것만 — [에이전트에게 맡기기](`POST /tasks/{id}/delegate`) 또는 트리거 라벨 `runloom`(대소문자 무시). 지시 전은 `not_delegated` 대기 | 가져온 것 전부(수집 = 지시) |
| 수정·검토 Agent·로컬 저장소·검증 프로필 | 러너가 보고한 `origin` 의 `owner/name` 으로 자동 매칭(비워 둔 칸만). 못 정하면 `repository_*`·`fix_agent_*`·`profile_*`·`review_agent_*` 대기 — 카드의 고급 설정에서 하나 고른다 | 소스 설정의 세 ID + 담당자 연결 |

- 수집 주기·댓글 반영·후속(검토·재작업·사람 요청)은 5절 이후 그대로다. 소스별로 자기 자격의 클라이언트를 쓴다.
- 저장소 추가·제거는 GitHub 의 App 설치 설정에서 하고 돌아오면(`/operator/github/app/setup`) 카드가 맞춰진다. 설치에서 빠진 저장소는 수집이 멈추고, 다시 넣어도 멈춘 채다 — 카드의 고급 설정 `수집 켜기` 로 다시 켠다.
- 수집 실패(rate limit·권한)는 카드에 보이지 않는다 — 워커 로그를 본다.
- 검증: 가짜 GitHub 로만(`tests/e2e/test_github_app.py`). 실제 App 생성·설치·`setup_action` 값·설치 URL 의 `state` 복귀는 미확인 — 10절.

## 1. GitHub 토큰 — 최소 권한

- **fine-grained personal access token** 하나. Repository access 는 *Only select repositories* 로 대상 저장소만 고른다.
- Repository permissions: **Issues: Read and write**, **Metadata: Read-only**(자동 포함). 그 밖(Contents·Pull requests·Actions 등)은 주지 않는다 — 코드 읽기·쓰기는 로컬 저장소에서 하고 GitHub 로 보내지 않는다.
- 기준선 가져오기(phase 9, `POST /operator/github/sources/{source_id}/baseline`)를 쓸 때만 **Pull requests: Read-only** 를 더한다 — GraphQL 로 이슈를 닫은 병합 PR 의 번호·병합 시각을 읽는다(`list_issue_pr_links`). 쓰기 권한은 늘지 않는다.
- 만료일을 짧게 둔다. classic PAT(`repo` scope)은 권한이 넓어 쓰지 않는다.
- 권한 근거: [ARCHITECTURE "GitHub REST 경계"](../ARCHITECTURE.md#github-rest-경계-step-5) 의 요청별 권한 표와 공식 문서 링크(2026-09-23 확인).

토큰 값은 서버 환경변수에만 둔다. 이 저장소의 파일·채팅·이슈·로그·스크린샷에 쓰지 않는다. 운영자 API·화면은 값을 받지도 보이지도 않고 `token_configured`(`토큰 설정됨`/`토큰 없음`)만 알린다. 도구(Codex·Claude)·검증 명령 프로세스 환경에도 들어가지 않는다(`masking.codex_env` 허용 목록 — `WORKFLOW_GITHUB_TOKEN`·`GITHUB_TOKEN`·`GH_TOKEN` 이 없음을 테스트로 고정).

## 2. 중앙 서버 환경변수

중앙 웹/API 와 중앙 워커가 같은 값을 읽는다(`server/settings.py`, 예시 `deploy/env/central.env.example`).

| 이름 | 비밀 | 값 | 비었을 때 |
|---|---|---|---|
| `WORKFLOW_GITHUB_TOKEN` | 예(`OPTIONAL_SECRET_KEYS`) | 위 fine-grained PAT | GitHub 연결만 꺼진다 — 워커가 클라이언트를 만들지 않아 수집·댓글 반영이 없다. `WORKFLOW_DEV` 도 값을 만들지 않는다 |
| `WORKFLOW_GITHUB_REPOS` | 아니오 | 연결을 허용할 `owner/name` 콤마 구분(대소문자 무시) | 어떤 저장소도 연결할 수 없다(422 `repository_not_allowed`) |
| `WORKFLOW_PUBLIC_URL` | 아니오 | 댓글의 Task 링크 앞부분(끝에 `/` 없음). 링크는 운영자 로그인이 필요한 주소다 | 댓글에 Task ID 만 쓴다 |
| `OPERATOR_TOKEN` | 예 | 운영자 로그인(`/operator/login`) | 서버가 시작하지 않는다(기존 규칙) |

파일에 둔다면 `chmod 600`. 허용 목록은 "토큰이 닿는 저장소" 가 아니라 "이 서버가 연결을 받아들이는 저장소" 다 — 둘을 같게 맞춘다.

## 3. Agent 와 로컬 등록 — 같은 기기에서 수정·검토

수정 Agent 와 검토 Agent 는 **같은 연결 프로그램**에 등록된 **같은 로컬 저장소**를 봐야 한다. 검토는 수정이 만든 커밋을 그 저장소에서 직접 읽는다(없으면 `commit_missing`, 다른 연결 프로그램·`repository_id` 면 `review_repository_mismatch`).

1. 운영자 로그인 → `/operator/agents` 에서 Agent 를 만든다 — 연결 방식 `local`, 능력 `code.fix`(수정) 또는 `code.review`(검토), 범위 값 = 이 제품의 저장소 ID(예 `billing` — 소스의 `workflow_repository_id` 와 같게), 로컬 등록 ID. `/agents/register` 에서 이 워크스페이스(세션)에 등록한다.
2. 연결 코드 발급(`/operator/connect-codes`) → 운영자 Mac 에서
   ```bash
   python3 -m workflow.connector connect --server <중앙 URL> --code <연결 코드>
   python3 -m workflow.connector register --id local-billing-fix    --repo <저장소 폴더> --repository-id billing --tool codex \
       --verify "vp-pytest=python3 -m pytest -q"
   python3 -m workflow.connector register --id local-billing-review --repo <같은 저장소 폴더> --repository-id billing --tool claude
   python3 -m workflow.connector run
   ```
   - 검증 프로필(`--verify 이름=명령`)은 **수정 등록에만** 필요하다. 명령은 로컬 `state.sqlite` 에만 있고 서버에는 이름만 보고된다. 소스의 `fix_verification_profile_id` 는 같은 범위의 `code.fix` Agent 가 보고한 이름이어야 한다(아니면 422 `verification_profile_unknown`). 이슈 본문의 명령은 절대 실행하지 않는다.
   - 재현 테스트는 결과 커밋에서 새로 생긴 `tests/`·`test_*.py` 파일로 인식한다(`test_log_before` 는 그 테스트만 `base_commit` 체크아웃에서 실패해야 함). Python 이 아닌 저장소는 재현 테스트 없음으로 사람 요청이 될 수 있다.
   - 등록 폴더는 깨끗해야 한다. 착수 때 HEAD ≠ `base_commit` 이면 `base_commit_mismatch`, 미커밋 변경이면 `worktree_dirty`. 수정은 `task/<id>` 브랜치에서 하고 기준 브랜치는 건드리지 않는다.
3. 첫 claim 이 `supported_kinds` 에 `bug_fix`·`code_review` 를 선언해야 착수한다. 구버전 연결 프로그램은 `executor_outdated` 대기로 보인다.

연결 프로그램 하나는 한 번에 실행 하나만 돈다(기존 제약). 같은 로컬 등록에서 수정 실행은 하나씩(`repository_busy`)이고, 결과를 기다리는 실행(`result_ready`)은 다른 업무를 막지 않는다.

## 4. 소스 설정 — 저장소·이슈 범위

화면 `/operator/github` 또는 JSON API(CONTRACT 13.10). 운영자 세션만, 워크스페이스 하나만(다른 세션이 이미 GitHub 소스를 가지면 409 `github_workspace_taken`).

| 필드 | 뜻 |
|---|---|
| `repository_full_name` | `WORKFLOW_GITHUB_REPOS` 안의 저장소. 만든 뒤 바꿀 수 없다(새 소스로) |
| `workflow_repository_id` | Agent 능력 범위 값과 같은 저장소 ID |
| `label_filter` | 이 라벨을 **모두** 가진 open 이슈만(대소문자 무시) |
| `start_at` | 이 시각 이후 만든 이슈만 — 시작 전 백로그는 가져오지 않는다 |
| `selected_issue_numbers` | 명시적으로 고른 번호. 라벨·시작 시각·닫힘과 무관하게 가져온다(닫혀 있으면 `source_closed` 대기) |
| `fix_verification_profile_id` | 수정 결과를 검증할 등록된 프로필 이름 |
| `review_agent_id` | 검토 Agent(`code.review {repository_id}`) |
| `run_mode` | `auto` 면 준비되면 착수, `manual` 이면 업무 상세의 `실행` 버튼. **가져올 때의 값이 Task 에 고정**된다 |
| `max_rework_rounds` | 자동 재작업 상한 0~3(기본 1). 0 이면 수정 요청이 곧바로 사람 요청. 현재 설정을 매번 읽는다 |

`label_filter`·`selected_issue_numbers` 가 둘 다 비면 422(전체 백로그 금지). 저장 전 `POST /github/sources/preview`(화면의 미리보기)가 문제를 모두 보여 준다 — GitHub 는 호출하지 않는다. PR 항목은 언제나 제외한다.

담당 연결: `PUT /github/sources/{source_id}/assignees/{github_user_id}` `{github_login, agent_id}`. 로그인이 아니라 **숫자 ID** 로 잇는다(로그인은 바뀐다). 숫자 ID 는 GitHub 공개 API `https://api.github.com/users/<login>` 의 `id`. 담당자 0명(`assignee_missing`)·2명 이상(`assignee_multiple` — 사람 요청으로 Agent 선택)·연결 없음(`assignee_unbound`)은 자동 추정 없이 대기다. 연결 삭제 API 는 없다 — 다른 Agent 로 다시 연결해 교체한다.

## 5. 돌아가는 모습

워커(`python3 -m workflow.server.worker`)가 tick 마다: 켜진 소스를 60초 간격으로 수집 → 준비 판정 → 착수 → 결과 판정 → 후속(검토 연결·생성, 재작업, 사람 요청) → 마지막에 댓글 반영. 대기 사유(`Blocker.code` 15종, [표](../ARCHITECTURE.md#준비-판정--대기-코드-blockercode))와 사람 요청은 업무 상세와 `/operator/github` 에 보인다.

- 사람 요청 응답은 운영자 웹에서만(CONTRACT 13.11). 응답은 실행을 바로 만들지 않고 다음 tick 의 준비 판정이 새 revision 으로 다시 본다. 응답은 권한을 넓히지 않는다 — 위임 밖(`delegation_denied`)은 Agent 능력·소스 설정을 운영자가 따로 고쳐야 풀린다.
- 검토 승인 뒤: 결과 커밋은 로컬 `task/<id>` 브랜치에만 있다. 병합·push·이슈 종료는 사람이 직접 한다.
- 실행 중 이슈를 고치면 진행 중 실행 입력은 그대로이고 새 스냅샷이 다음 revision 이 된다. 이슈를 닫으면 새 착수·후속만 멈추고(`source_closed`), 다시 열면 재평가한다.

## 6. 데이터 보존 업그레이드 (v4 → v5)

phase 8 은 `SCHEMA_VERSION` 을 4 → 5 로 올리며 **처음으로 데이터 보존 마이그레이션**이 있다(`adapters/db.py`, [ARCHITECTURE "저장 — v5 마이그레이션"](../ARCHITECTURE.md#저장--v5-마이그레이션-step-4-구현됨)). `WORKFLOW_RESET_DB` 가 필요 없다.

1. 중앙·워커·연결 프로그램을 멈추고 DB 를 백업한다(VM 이면 `sudo systemctl start workflow-backup.service`, 로컬이면 `db.sqlite`·`-wal` 을 복사).
2. **서버를 먼저** 올린다. 중앙 또는 워커가 시작할 때 `init_schema` 가 `BEGIN IMMEDIATE` 한 트랜잭션으로 올린다 — 실패하면 v4 그대로 남고 시작하지 못한다. 기존 사용자 정의 종류 이름이 `bug_fix`·`code_review` 면 `세션:종류` 목록이 오류에 찍힌다(그 종류를 다른 이름으로 옮긴 뒤 다시 시작).
3. 그다음 연결 프로그램을 올린다. 구버전 연결 프로그램은 새 종류를 받지 않고 `executor_outdated` 대기가 된다(구버전 서버 + 신버전 연결 프로그램은 claim 이 422).
4. v3 이하·v6 이상은 여전히 시작을 거부한다. 버전 2 인 공개 데모 VM 은 이 절차 대상이 아니다([DEPLOY](../DEPLOY.md)).

개발 중 주의: `human_responses.agent_id` 를 배포 전 v5 DDL 에 더했다(step 11). 그 전 커밋으로 만든 로컬 v5 DB 는 다시 만들어야 한다.

## 7. 중지

| 멈추려는 것 | 방법 | 남는 것 |
|---|---|---|
| 새 이슈 수집·댓글 반영 | `POST /github/sources/{id}/stop`(화면 `중지`) — `enabled=false`, `config_revision`+1 | **이미 가져온 Task 의 준비 판정·착수·검토는 계속된다**(중지는 원본 연동만 끈다) |
| 모든 GitHub 호출 | `WORKFLOW_GITHUB_TOKEN` 을 비우고 워커 재시작 | 위와 같음 |
| 로컬 실행 | 연결 프로그램 `run` 을 끈다 | 대기 Task 는 `executor_offline`, 진행 중 실행은 기존 `unknown` 규칙(120초) |
| 특정 Task | 사람 요청에 `close` 응답, 또는 `확인 필요` 인 업무 상세 검토 폼의 종료 | `실패` 마감, 새 후속 없음 |
| 새 Task 의 자동 착수 | 소스 `run_mode=manual` 로 변경 | 이미 가져온 Task 의 `run_mode` 는 그대로 |

다시 켜기: `PUT /github/sources/{id}` 에 `enabled: true` 와 현재 `expected_revision`. 커서는 유지되어 멈춘 동안 바뀐 이슈를 이어서 받는다.

## 8. 복구

- **워커·중앙 재시작**: 중복 키(이슈 → Task, `start_key`, 후속 원인 키, 사람 요청 `cause_key`, 응답 `response_id`, 댓글 marker)로 같은 일을 다시 해도 Task·Execution·댓글이 늘지 않는다(e2e 로 확인). 수집 간격은 메모리 값이라 재시작하면 바로 한 번 수집한다.
- **수집 실패**: GitHub 오류는 `SyncReport.error` 로 남고 커서는 실패한 페이지에 머물러 다음 번에 다시 받는다. rate limit 은 알려준 시간(없으면 60초) 쉰다. 이미 도는 업무 순환에는 영향이 없다.
- **권한 오류(401·403)**: 토큰 만료·저장소 누락·권한 부족. 토큰을 바꾸고 워커를 재시작한다. `반영 실패` 처리는 9절.
- **이슈 이전·저장소 이름 변경**: 리다이렉트를 따르지 않으므로 오류로 드러난다. 새 저장소 이름을 허용 목록에 넣고 새 소스로 연결한다.
- **연결 프로그램 중단**: 도구가 남긴 미커밋 변경은 다음 착수에서 `worktree_dirty` 로 멈춘다. 운영자가 등록 폴더를 확인·정리한다(자동 삭제 없음).

## 9. 결과 반영과 `unknown` 조정

상태는 Task 상태와 따로 `반영 대기`(`pending`)·`반영됨`(`delivered`)·`반영 불확실`(`unknown`)·`반영 실패`(`failed`)로 보인다. 반영 실패는 Agent 작업·Task 를 바꾸지 않는다.

- 댓글 POST 의 응답을 잃으면(연결 오류·timeout·5xx) `unknown` 으로 두고 **다시 POST 하지 않는다**. 다음 tick 에 그 이슈 댓글을 전부(최대 30페이지) 읽어 첫 줄이 marker 인 댓글을 찾는다 — 있으면 `delivered`, 없음이 확인되면 `pending` 으로 돌려 다시 보내고, 조회가 실패하면 `unknown` 에 머문다(백오프).
- 이후 갱신은 같은 댓글을 PATCH 한다. 최신 본문(`body_revision`)만 보낸다.
- 사람이 지운 댓글은 다시 만들지 않는다(`failed`).
- 한계(원격 exactly-once 아님): marker 를 첫 줄에 쓴 다른 사람의 댓글을 우리 것으로 볼 수 있다. "없음" 확인 뒤 늦게 도착한 이전 POST 가 있으면 댓글이 둘이 될 수 있다. 전송 중 crash 는 claim 만료(120초) 뒤에야 조정된다. 댓글 3,000개 초과 이슈는 `unknown` 에 머문다. 결과 요약의 `@멘션` 은 실제 알림이 된다.
- 운영자 조치: `반영 불확실` 이 오래 남으면 이슈에서 marker 댓글을 직접 확인한다. 둘이면 먼저 만든 댓글을 남기고 나중 것을 지운다 — 조정은 가장 오래된 marker 댓글을 저장하고, 저장한 댓글을 지우면 다음 PATCH 가 404 → `반영 실패` 가 된다(다시 만들지 않음).
- `반영 실패` 는 자동으로 다시 보내지 않는다. 원인(토큰·권한)을 고친 뒤 Task 상태가 바뀌어 본문이 새 revision 이 되면 그 본문을 보낸다.

## 10. 검증 현황 — 테스트와 실제 미검증의 구분

2026-09-23, `feat-8-github-task-cycle` 에서 실행한 결과(step 15):

| 명령 | 결과 |
|---|---|
| `python3 -m pytest -q` | 2164 passed, 49 skipped(e2e 는 플래그 없어 건너뜀), 81초 |
| `python3 -m ruff check .` | All checks passed |
| `WORKFLOW_E2E=1 python3 -m pytest tests/e2e -q` | 49 passed(새 `test_github_cycle.py` 13 + 기존 대본·n8n e2e 36), 149초 |

대역으로 확인한 것: GitHub REST 요청·오류 분류·페이지·ETag(MockTransport), 수집 범위·중복·편집·닫힘, 준비 판정 15종, 후속 결정·재작업 상한, 사람 요청·응답 멱등, 댓글 outbox·marker 조정, 화면·API 권한, 그리고 `tests/e2e/test_github_cycle.py` 13개 — 127.0.0.1 가짜 GitHub + 실제 `HttpGitHubClient` + uvicorn 중앙 + 하위 프로세스 연결 프로그램 + 가짜 `codex` + 임시 Git 저장소 2개 + 실제 pytest 검증으로 A~G 전체 순환(수정 → 검토 수정 요청 → 재작업 → 승인, 상한 0 사람 요청, 담당 2명·위임 밖 응답 후 재개, POST 응답 유실 조정, 5xx 수집 실패, 재시작 멱등, 토큰 비노출).

2026-09-23 실제 연동에서 확인한 것(step 16 — [VERIFICATION_LOG 실연동 절](../VERIFICATION_LOG.md)):

- 실제 `api.github.com` — fine-grained PAT(Issues RW·Metadata R)로 수집·댓글 POST·PATCH 가 되고 그 밖의 쓰기는 필요하지 않았다. rate limit 5000/h 는 근처에도 가지 않았다(요청 10여 건).
- 실제 `claude` 2.1.280 이 `bug_fix` 에서 재현 테스트를 먼저 쓰고 고쳤다 — 그 테스트가 base 커밋에서만 실패함을 제품 판정과 별개로 직접 확인했다. 실제 검토 도구가 결과 커밋을 읽고 `REVIEW_RESULT_SCHEMA` 대로 `approved` 를 제출했다.
- 이슈마다 댓글 1개가 만들어져 `body_revision` 1→3 이 같은 댓글 PATCH 로 반영됐다. 기준 브랜치·원격은 변하지 않았고 이슈는 열린 채였다.
- 비용은 CLI 보고값으로 4회 합계 약 $1.19(수정 $0.34·$0.32, 검토 $0.32·$0.21).

여전히 확인하지 않은 것:

- **`changes_requested` → 재작업 → 재검토** — 실제 검토가 두 번 다 승인해서 관찰되지 않았다. 대역 e2e 에만 있다.
- 담당자 없음/복수, 위임 밖, 사람 요청·응답 후 재개, 이슈 편집·닫힘, POST 응답 유실 조정 — 모두 대역 e2e 에만 있다.
- 실제 rate limit·secondary rate limit 헤더, 실제 댓글 목록 페이지네이션(댓글이 1개뿐이었다).
- v4 → v5 데이터 보존 마이그레이션 — 실연동 DB 는 새로 만들어져 처음부터 v5 였다.
- 실제 Codex — 수정·검토 모두 Claude 로 돌렸다.
- 비 Python 저장소의 재현 테스트 인식.
- 브라우저에서 `data-json-action` 스크립트(서버 렌더만 확인).

실제 Link·ETag 동작은 수집 2회로는 페이지·304 경로를 밟지 않았으므로 대역 확인에 머문다.

2026-09-27 phase 11(GitHub App 연결) — **대역만**: `WORKFLOW_E2E=1 python3 -m pytest tests/e2e/test_github_app.py` 6개. 가짜 GitHub 가 manifest 교환·App JWT(공개 키로 서명 검증)·설치 토큰·설치 저장소를 흉내 내고, [GitHub 연결] → callback → setup → 소스 자동 생성 → 열린 이슈 3건 `지시 전` → 러너 register(`origin` = `git@github.com:acme/billing.git`) → 자동 매칭 → [맡기기] 한 건 수정·검토 승인·운영자 승인 → 라벨 붙인 다른 이슈 자동 착수·검토 승인까지 돌렸다. 비밀 파일 0700/0600, 비밀값이 DB·산출물·로그·화면·댓글·도구 환경에 없음. 실제 github.com 의 App 만들기·설치 화면은 밟지 않았다([VERIFICATION_LOG](../VERIFICATION_LOG.md) 2026-09-27 phase 11 절).

## 11. 계획과 구현의 차이

phase 8 README·ADR-0014 의 계획과 구현이 다른 곳. 코드 이름은 구현 기준이다.

| 계획 | 구현 | 이유 |
|---|---|---|
| ADR-0014 1: `WORKFLOW_GITHUB_TOKEN` 을 `SECRET_KEYS` 에 | `OPTIONAL_SECRET_KEYS` — 비어도 서버가 뜨고 GitHub 연결만 꺼진다 | 공개 데모·GitHub 를 쓰지 않는 셀프호스트가 값 없이 시작해야 함(step 6) |
| ADR-0014 3: `ExecutionPolicy` 에 `requires_report`·`followup_on_ready`·`rework_outcome` | `verifier`·`cycle`·`starts_from_result` 만. 보고서 요구는 `verifier=report_code_change`, 후속은 규칙 표·`decide_followup` | 같은 정보를 두 곳에 두지 않음(step 10) |
| ADR-0014 4·CONTRACT 13.5: `supported_kinds` null 이면 "내장 중 `code_change` 만" | `LEGACY_BUILTIN_KINDS = (diagnosis, code_change)` + 사용자 정의 종류 | 구버전도 진단은 실행해 왔음(step 2) |
| ADR-0014 2: 대상은 open Issue | `selected_issue_numbers` 는 닫혀 있어도 가져오고 `source_closed` 대기 | 명시적 선택을 조용히 버리지 않음(step 7) |
| 수집 이슈의 `run_mode` | 가져올 때 값 고정, 이후 소스 변경은 새 Task 에만 | 기존 Task·실행 입력 불변(step 6·7) |
| `followup_links.rules_revision` | 언제나 1 | 규칙 revision 개념이 아직 없음(step 10) |
| 소스 중지 | 수집·댓글만 멈추고 이미 가져온 Task 는 계속 | 중지는 원본 연동 스위치, 실행 중단은 연결 프로그램·Task 종료(위 7절) |
| 담당 연결 삭제 | API 없음(재연결로 교체) | MVP 범위 |
| 소스 설정 변경(라벨·시작 시각) | 커서를 초기화하지 않음 — 이미 지난 구간의 이슈는 다시 보지 않는다 | 필요하면 `selected_issue_numbers` 로 고른다(step 7) |
| README "GitHub 담당자 인증 매핑" | 없음 — 운영자가 숫자 ID → Agent 를 직접 연결, 응답도 운영자만 | ADR-0014 5·7 그대로 |
| `scripts/local_stack.py` | GitHub 순환을 띄우지 않음. e2e 는 자체 가짜 GitHub 를 쓴다 | 제품에 GitHub 주소 설정을 추가하지 않음(step 14) |

## 실연동 체크리스트 — step 16

아래 값을 운영자가 정해 주기 전에는 실제 GitHub 쓰기·유료 호출을 하지 않는다. 토큰 값은 이 표·채팅·로그에 쓰지 않는다 — "환경변수에 넣었음" 만 확인한다.

| 항목 | 운영자가 정할 값 |
|---|---|
| 테스트 저장소 | `owner/name` 하나(비공개 테스트 저장소 권장). `WORKFLOW_GITHUB_REPOS` 에 이것만 |
| 대상 이슈 | 번호(`selected_issue_numbers`) 또는 전용 라벨 + `start_at`. 재현 가능한 작은 버그 1건(가능하면 검토 수정 요청을 유도할 두 번째 1건) |
| 댓글 허가 | 그 이슈에 Runloom 댓글 생성·수정을 허가하는지. 다른 이슈·PR·저장소 쓰기 없음 |
| 토큰 | fine-grained PAT, 그 저장소만, Issues RW·Metadata R, 짧은 만료. 검증 뒤 폐기 여부 |
| 로컬 저장소 | 운영자 Mac 의 클론 경로, 깨끗한 기준 브랜치, 검증 명령(예 `python3 -m pytest -q`) |
| Agent·구독 | 수정 도구(Codex/Claude)와 검토 도구, 사용할 계정·구독, 허용 비용 상한(모르면 "미확인" 으로 기록) |
| 재작업 상한 | `max_rework_rounds`(기본 1) |
| 정리 | 끝난 뒤 댓글·`task/<id>` 브랜치·소스·토큰을 남길지 지울지 |

기대 결과(관찰할 것):

1. 수집: 지정 범위 이슈만 Task 가 되고 다른 이슈에는 댓글이 없다.
2. baseline: 수정 실행의 `test_log_before` 가 `base_commit` 에서 실패(0 아님), `test_log_after`·`verification_log` 는 결과 커밋의 깨끗한 체크아웃에서 통과, 판정 checks 전부 통과.
3. 원본 반영: 이슈에 marker 댓글 1개, 결과 커밋 SHA·"자동 푸시 없음" 문구, 이후 상태 변화는 같은 댓글 수정.
4. 검토: 새 `code_review` Task 1개(생성 근거 = 그 수정 실행), 실제 검토 도구가 결과 커밋을 읽고 `approved`·`changes_requested`·`needs_information` 중 하나를 스키마대로 제출.
5. rework: `changes_requested` 면 같은 수정 Task 의 다음 시도(`base_commit` = 이전 결과 커밋) → 새 커밋 재검토. 실제 검토가 수정 요청을 내지 않으면 이 경로는 "대역에서만 확인, 실연동 미관찰" 로 기록한다.
6. 끝: 승인이면 수정 Task `확인 필요 · 검토 승인 — 병합·이슈 종료는 사람`. push·PR·merge·이슈 종료 없음. 토큰이 DB·산출물·로그·댓글에 없음.

기록은 [VERIFICATION_LOG](../VERIFICATION_LOG.md) 에 source/task/execution ID·커밋·검증·대기·사람 조작·비용·원본 반영·후속 생성 근거·만든 자원과 정리 여부를 남긴다.
