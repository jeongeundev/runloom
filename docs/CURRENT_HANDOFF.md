# 현재 인계 — 업무 목록과 결과 기반 자동 실행

갱신일: 2026-09-29. 2026-09-26 이전 기록은 [보관 자료](archive/2026-09-27-contest-and-history/CURRENT_HANDOFF-until-2026-09-26.md).

## 다음 작업: 14 완료 → service 병합 → 셀프호스트 재설치(사용자 지시) → 15-team 설계 (새 세션은 여기서 시작)

**14-task-model 완료**(2026-09-30, `feat-14-task-model`, [ADR-0020](adr/0020-work-items-and-stages.md), 검증은 [VERIFICATION_LOG](VERIFICATION_LOG.md) "phase 14 업무·단계"): 업무 `work_items`(키 `RUN-n`)·단계 Task 분리, 업무 상태 8개, 실패 → 내 차례·[다시 맡기기]·[닫기], `placement`, 매핑 표·양식 칸, 브랜치 `runloom/<키>`, 지표 묶음 = 업무, 홈 목록 = 업무 한 줄, 스키마 v10. 다음: `feat-14-task-model` 을 `service` 에 `--no-ff` 병합 → 셀프호스트 재설치(백업 먼저, 진행 중 실행이 끝난 뒤, 러너도 함께 — [SELFHOST](SELFHOST.md) 업그레이드 v10, 사용자 지시 뒤) → 15-team 설계(초대·로그인·역할). 셀프호스트 Docker e2e(`WORKFLOW_DOCKER=1`)는 이미지 태그 공유 때문에 미실행 — 재설치 때 함께 확인한다.

**13-selfhost-only 완료**(2026-09-29, `feat-13-selfhost-only`, [ADR-0019](adr/0019-service-selfhost-only.md), 검증은 [VERIFICATION_LOG](VERIFICATION_LOG.md) "2026-09-29 phase 13 셀프호스트 전용"). 다음: `feat-13-selfhost-only` 를 `service` 에 `--no-ff` 병합 → `python3 scripts/execute.py 14-task-model --engine claude`(`phases/14-task-model`). 셀프호스트는 아직 스키마 8 — 재설치(v9 로 올림)는 백업 뒤 사용자 지시로.

**step 설계 완료(2026-09-29)**: 13-task-model 범위를 두 phase 로 나눴다(사용자 결정 — demo 걷어내기를 먼저).
- `phases/13-selfhost-only/`(6 step): `service` 에서 demo 모드·진단 데모·대본 에이전트·카탈로그·fixture 가져오기·VM 배포 파일 삭제, 내장 종류 `bug_fix`·`code_review` 둘, 스키마 v9. README "남기는 것" 참고.
- `phases/14-task-model/`(11 step): 업무 `WorkItem`(`work_items`, 키 `RUN-n`) ↔ 단계 `Task` 분리, 업무 상태 8개(실패 = "내 차례 · 실패" + [다시 맡기기]·[닫기]), 후속 규칙 `placement`(same_work/new_work), `members`(첫 관리자), 매핑 표(`github · kind · * → bug_fix` 기본), 양식 칸 추출, 브랜치 `runloom/RUN-n`, 지표 묶음 = 업무, 홈 목록 = 업무 한 줄, 기존 데이터는 v10 마이그레이션으로 옮김.
- 이후 phase 번호 한 칸씩 밀림: 15-team, 16-work-ui, 17-jira, 18-triage, 19-monitor(REDESIGN_PLAN 13절은 13 의 step 5 가 고침).

**실행**: `service` 에서 `python3 scripts/execute.py 13-selfhost-only --engine claude` → `feat-13-selfhost-only` 를 `service` 에 `--no-ff` 병합 → `python3 scripts/execute.py 14-task-model --engine claude` → 병합. 셀프호스트 재설치는 사용자 지시 뒤.

**2026-09-29 확정 사항**: 목록 한 줄 = 업무(수정·검토·재작업·판단은 단계), `service` 는 셀프호스트만(demo 는 `main` 에만), 일정 무관·완성도 우선, 팀 계정 = 초대 링크 + 이메일·비밀번호, 판단 = 로컬 Claude Code 판단 에이전트가 제안만(자동 시작은 업무 종류별 설정), 직접 작업 상태 추적 = Git·GitHub 신호 + Claude Code 훅, 수신함 = "담당 없음" 묶음. Jira 는 사용자 계정의 새 Jira Cloud 사이트(지인 회사 Jira 는 형식만 참고).

**현재 환경(2026-09-29 저녁)**:
- `service` = `115f529`(실연동 1 결함 수정 병합), 원격 미푸시. 셀프호스트는 이 코드로 재설치(백업 `20260929T093748Z`, 스키마 8), 러너도 새 코드로 재기동.
- 실연동 대상은 비공개 `jeongeundev/runloom-sandbox`(클론 `/Users/kje/demo/runloom-sandbox`, 클론 로컬 `gh auth git-credential` 자격). App `runloom-gwufov` 설치는 sandbox 만 — 권한 Issues RW·Pull requests RW·Contents R·Metadata R. 워커·러너 **켜져 있음**, 알림(Discord) 설정됨.
- [실연동 1](VERIFICATION_LOG.md): sandbox #1 → 수정 → 검토 → 초안 PR #2 → 사람 병합 → 업무 완료. 결함 2건(러너 재시작 뒤 영구 정지, App Contents 권한)은 수정됨. 에이전트의 #1 수정도 `service` 로 가져옴(`d5c2568`).
- OpenArchive: 소스 중지, #112 운영자 종료, App 설치 없음. 쓰기 금지 그대로. 로컬 `/Users/kje/demo/OpenArchive-worktrees/task-e3df709051a2` 에 push 안 된 작업 폴더가 남아 있다(정리는 사용자 결정).

## 방향 전환 기록 (2026-09-29)

2026-09-29 실연동 중단. **OpenArchive 는 오픈소스 공모전 출품작이라 커밋·이슈·PR·댓글 하나하나가 심사 대상 — Runloom·에이전트가 쓰지 않는다**(읽기만). #112 를 맡긴 직후 봇 상태 댓글 1개가 달려 삭제했고, 러너(launchd bootout)·중앙 워커(`docker compose -p runloom … stop worker`)를 멈췄다. push·PR 은 없었다. GitHub App `runloom-gwufov` 의 OpenArchive 설치를 제거했다(App 자체는 남음, 설치 0). Runloom DB 의 OpenArchive 업무 22·기준선 24 는 남아 있다. **워커·러너를 다시 켜기 전에 실연동 대상을 정한다.**

**설계 계획: [재설계 계획](product/REDESIGN_PLAN.md)**(2026-09-29) — 목업 https://claude.ai/artifact/Jx6Pa7PmRZo1hmvFuiH66C, phase 제안 13-task-model → 14-team → 15-work-ui → 16-jira → 17-triage → 18-monitor. 실연동은 새 비공개 저장소(runloom 복사)·새 Jira Cloud 사이트에서.

사용자 판단(2026-09-29): 앱 사용 자체가 불편해 UX·UI 를 처음부터 다시 봐야 한다. 업무 가져오기가 가장 불편 — GitHub·**Jira 가져오기와 Jira 에 업무 등록**이 필수, 가져올 때 소스별 양식을 Runloom 업무 양식으로 맞춰 등록, 이슈 옆에 PR 도 보여야 한다. 같은 서비스는 없지만 비슷한 서비스를 벤치마킹한다. 조사 문서: `docs/research/2026-09-29-*.md`(로컬 에이전트 보드, 이슈→PR 에이전트, 가져오기·필드 매핑, Jira 연동).

에이전트의 ssh(OpenArchive HA 3노드 VM 실측) 질문: 러너가 사용자 계정으로 Claude Code 를 띄우므로 연결 자체는 사용자 터미널과 같은 조건이다. 막는 것은 `connector/claude.py` `ALLOWED_TOOLS` 고정(파일 편집·pytest·git diff/status)과 환경 허용 목록(`SSH_AUTH_SOCK` 없음)이다 — 등록별 허용 명령 선언으로 열 수 있다(미구현).

## 이전 작업: `12-real-repo` 실연동 (중단)

**12-real-repo 구현 완료** (2026-09-28, 브랜치 `feat-12-real-repo`, step 0~10 — `phases/12-real-repo/index.json`). 설계는 [ADR-0018](adr/0018-real-repo-cycle.md)·ARCHITECTURE "실제 저장소 순환 — phase 12"·CONTRACT 14절. 들어간 것: 저장소 카드 [러너 붙이기] → `install-runner.sh --server --code --repo` 한 명령(setup = connect + register, register 가 수정·검토 Agent 를 만듦), 러너가 60초마다 fetch 해 기본 브랜치 최신을 기준 커밋으로 보고, worktree 에 `--link` 심볼릭 링크·`--env` 환경(값은 러너에만), 결과 브랜치 `task/<id>` push, 검토 승인 뒤 App 으로 초안 PR(`Fixes #N`) → 병합 추적 → 완료·지표, 알림 웹훅(사람 차례·PR 확인·실패, `/operator/notifications`, 스키마 v8). 대역 e2e `tests/e2e/test_real_repo.py` 가 이 한 줄기를 가짜 GitHub·bare 저장소·가짜 알림 수신으로 돈다([VERIFICATION_LOG](VERIFICATION_LOG.md) 2026-09-28 절).

[MVP 계획](product/MVP_PLAN.md)의 목표: **2026-10-02 까지 Runloom 단독으로 OpenArchive(`jeongeundev/OpenArchive`) 실제 이슈 3건 이상을 이슈 → 수정 → 검토 → 사람 차례 알림까지 사람의 전달 없이 진행하고, 도입 전후를 지표로 보인다.** 남은 것은 사용자와 함께 하는 실연동이다.

실연동 준비 목록(순서대로 — 사용자 지시·확인 뒤에만 한다):
1. 완료(2026-09-29): `service` 병합(2e06218) → 백업 `20260929T012810Z` → `install.sh` 재실행, 스키마 8, 업무 22·기준선 24 보존.
2. **App 권한 승인** — 기존 App `runloom-gwufov` 의 Pull requests 를 Read and write 로 올리고 설치(jeongeundev)에서 새 권한 승인([SELFHOST "App 권한 올리기"](SELFHOST.md#app-권한-올리기--phase-12-전에-만든-app)).
3. **에이전트 전용 테스트 DB** — 2026-09-29 컨테이너 `runloom-agent-db`(pgvector pg17, vector 0.8.6, `127.0.0.1:5435`, 볼륨 `runloom-agent-db`)로 띄움 — 5433 은 `opensql-db-1`, 5434 는 `application-db-1` 이 사용 중. 러너 `--env DATABASE_URL=postgresql://openarchive:openarchive@localhost:5435/openarchive` 로 넘긴다.
4. 완료(2026-09-29) **OpenArchive 준비**: 원본 폴더 `/Users/kje/demo/OpenArchive` 를 `git pull --ff-only` 로 GitHub `main`(`2383ce9`)에 맞추고 `backend/.venv` 에 `pip install -e '.[dev]'`(pytest·ruff 가 없었다), `frontend` 에 `npm install`. check.sh 는 고치지 않았다 — 대신 러너 `--env PYTHONPATH=.` 로 링크된 편집 설치 venv 가 작업 복사본 코드를 먼저 잡게 한다(확인: 없으면 원본 `app`, 있으면 복사본 `app`). Next 16 Turbopack 이 링크된 `node_modules` 를 "points out of the filesystem root" 로 거부해 러너에 `--copy`(APFS 복제, 4.6초)를 더했다(`781ce2a`). GitHub `main` 클론 + 링크 venv + 복제 node_modules + `PYTHONPATH=.` + 5435 DB 로 `bash scripts/check.sh` 통과(exit 0, 약 3분, backend 880·frontend 242). **main 에 의존성이 늘면 원본 폴더를 다시 pull + pip/npm install 해야 한다**(venv·node_modules 는 원본 것을 쓴다).
5. **러너 붙이기** — 카드 [러너 붙이기] → 나온 명령 끝에 붙여 실행:
   `--repo /Users/kje/demo/OpenArchive --verify "check=bash scripts/check.sh" --link backend/.venv --copy frontend/node_modules --env PYTHONPATH=. --env DATABASE_URL=postgresql://openarchive:openarchive@localhost:5435/openarchive`
   `origin` 은 https(osxkeychain) — launchd 에서 push 가 되는지 첫 업무에서 확인.
6. **알림** — Discord 웹훅 URL 을 `/operator/notifications` 에 저장 → [테스트 보내기].
7. **이슈 3건 이상** — 사용자가 손대지 않은 이슈를 골라 [에이전트에게 맡기기] → PR 병합까지. [VERIFICATION_LOG](VERIFICATION_LOG.md) 의 "실연동 기록 틀" 에 이슈마다 기록(phase 11 미확인 `setup_action`·설치 URL `state` 복귀 포함). 끝나면 `/metrics` 의 기준선(24건, 중앙값 7시간 28분) 대 도입 후.

phase 12 에서 남긴 것: 실제 github.com 의 초안 PR 생성(초안 미지원 422 문구 포함)·권한 올리기 화면·실제 Discord 전송은 미확인. PR 을 못 연 업무는 다시 열지 않는다(사람 요청). 러너는 실행 중에는 fetch 하지 않는다. `--env` 값은 명령행 인자라 실행 중 `ps` 에 보인다. step 10 에서 고친 결함: 러너가 결과 봉투를 가릴 때 `sk-` 패턴이 `task-<12 hex>` 안의 `sk-…` 를 키로 보고 `task_id` 를 `task-***` 로 바꿔 모든 수정 결과가 판정 `result_ids_match` 에서 실패하던 문제(step 4 회귀, `connector/masking.py`), 원본 이슈 댓글·`/operator/github` 의 "자동으로 푸시하지 않음" 문구.

phase 11 에서 남긴 것: 수집 실패(rate limit·권한)는 DB 에 저장하지 않아 카드에 안 보인다(워커 로그만). 설치에서 빠졌다 다시 들어온 저장소의 소스는 멈춘 채다(고급 설정 `수집 켜기`). manifest code 는 callback URL 이라 접근 로그에 남는다(한 번 쓰면 무효). 브라우저 스크립트(자동 제출 폼·`data-json-action`)는 서버 렌더만 확인했다.

phase 9 e2e 에서 발견한 결함 — 수정됨(step 13): 재작업 상한 1 에서 수정 요청 검토가 재작업을 시작시킨 뒤, 재작업 결과가 판정되기 전 tick 이 같은 검토를 다시 평가하면 `domain/task_followup.py` `_after_review` 가 `rounds_used(1) >= 상한(1)` 으로 `rework_limit_reached` 사람 요청을 하나 더 만들던 문제. 이제 그 검토가 이미 재작업(`rework:{검토 실행}`, 워커가 수정 Task 실행의 `start_key` 로 `handled_cause_keys` 에 넣음)을 일으켰으면 상한 판단 전에 `none`("이미 재작업을 시작한 검토") 을 낸다. 회귀: `tests/workflow/domain/test_task_followup.py`·`tests/workflow/server/test_task_cycle.py`, e2e `tests/e2e/test_metrics.py` 는 사람 요청 0·개입 1 로 단정.

공모전 관련 작업은 더 하지 않는다(2026-09-27 사용자 결정). 공개 데모 VM 과 `main` 은 그대로 두고 손대지 않는다. 공모전 문서는 [보관 자료](archive/2026-09-27-contest-and-history/)로 옮겼다.

## 지금 상태

| 항목 | 상태 |
|---|---|
| 브랜치 | `service` 가 실서비스 통합 브랜치. phase 6·7·8 과 문서 정리 포함. 새 phase 는 `service` 에서 `feat-*` 로 분기하고 끝나면 `--no-ff` 병합. 원격 푸시는 사용자 지시 때만 |
| 완료 phase | 0-mvp, 1-diag-fix, 2-model-compare, 5-scripted-demo(공모전 데모), 6-typed-handoff([ADR-0009](adr/0009-registered-kinds-and-succession-rules.md)), 7-n8n-gateway([ADR-0010](adr/0010-n8n-inbox-and-callback.md)), 8-github-task-cycle([ADR-0014](adr/0014-github-task-cycle.md)), 9-measure([ADR-0015](adr/0015-measurement-events-and-baseline.md)), 10-selfhost([ADR-0016](adr/0016-selfhost-docker-fixed-workspace.md)), 11-github-app([ADR-0017](adr/0017-github-app-connection.md)) — 모두 `service` 병합됨(미푸시). 12-real-repo([ADR-0018](adr/0018-real-repo-cycle.md)) — `service` 병합됨(2e06218). 4-claude-issues 는 step 3 에서 종료 |
| 계획만 | `3-limit-wait`([ADR-0007](adr/0007-usage-limit-wait-policy.md), 사용량 한도 대기). 실사용에서 한도에 걸리는 빈도를 보고 당긴다 |
| 검증 | 2026-09-29 `service` 115f529 기준 `python3 -m pytest -q` 2932 passed/68 skipped, `ruff` 통과, `WORKFLOW_E2E=1` `test_github_app`·`test_real_repo` 13 passed. 이전: 2026-09-28 `feat-12-real-repo` step 10 기준 2918 passed/68 skipped. `WORKFLOW_E2E=1` e2e 67 passed/1 skipped(`test_real_repo.py` 7 포함), `WORKFLOW_DOCKER=1` 셀프호스트 e2e 는 phase 11 브랜치에서 1 passed(phase 12 에서 다시 돌리지 않음) |
| 실연동 | 2026-09-23 실제 GitHub·실제 Claude 로 `bug_fix` → `code_review` 1회 통과([VERIFICATION_LOG](VERIFICATION_LOG.md)). `changes_requested` 재작업은 실연동 미관찰. 2026-09-27 실제 GitHub App 생성·설치·이슈 수집(17건)·기준선(24건) 성공. 2026-09-29 실연동 1(sandbox): 실제 push·App 초안 PR·병합 추적·Discord 알림까지 통과 |
| 사용자 결정 대기 | 실연동 자원 정리(`jeongeundev/runloom-live-test`, `../runloom-live-test`, `../runloom-live-state/`, `~/.runloom-live.env`), 워커 httpx 로그의 callback URL `signature` 노출 처리 |

## 재개 방법 — 하네스

```bash
cd /Users/kje/00_Workspace/01_Coding/project/workflow
git checkout service
python3 scripts/execute.py {task-name} --engine claude   # phases/{task-name}/index.json + step{N}.md
```

- 워크플로우 전체는 `.claude/commands/harness.md`. `--engine claude` 를 쓴다(Codex 사용량 소진).
- 세션 한도(429)로 step 이 3회 실패하면 코드 문제가 아니다. `index.json` 의 그 step 을 `pending` 으로 되돌리고 `error_message` 를 지운 뒤 재개한다.
- 하네스가 도는 동안 같은 작업 트리에서 편집하지 않는다(필요하면 별도 worktree).
- 구현은 사용자가 "진행해" 로 지시했을 때만 한다. 실배포·유료 호출은 별도 지시 없이 시작하지 않는다. 결정 질문은 압축 용어 대신 장면으로 풀어 2~3개씩 묻는다.
