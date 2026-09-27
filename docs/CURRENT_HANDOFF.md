# 현재 인계 — 업무 목록과 결과 기반 자동 실행

갱신일: 2026-09-27. 2026-09-26 이전 기록은 [보관 자료](archive/2026-09-27-contest-and-history/CURRENT_HANDOFF-until-2026-09-26.md).

## 다음 작업: `11-github-app`(GitHub App 버튼 연결) → 실제 OpenArchive 기준선 가져오기(사용자 지시 후) → `12-real-repo`

[MVP 계획](product/MVP_PLAN.md)이 최신 기준이다(2026-09-25~26 사용자 합의). 목표: **2026-10-02 까지 Runloom 단독으로 OpenArchive(`jeongeundev/OpenArchive`) 실제 이슈를 순환 처리하고, 셀프호스트로 배포하며, 도입 전후를 지표로 보여준다.**

`9-measure` 는 2026-09-27 step 0~10 을 마쳤다(`feat-9-measure`, `service` 병합 전). [ADR-0015](adr/0015-measurement-events-and-baseline.md)·ARCHITECTURE "측정 — phase 9" 절이 기준이다. 들어간 것: `task_events`(상태 변화·막힘·착수 가능), 워크스페이스 설정 번호(`sessions.config_revision` → 실행·후속 링크), 러너 폴더 커밋·CLI 보고 비용/토큰(`started`·`result_ready`·`failed` 선택 칸, `contract_version` 1 유지), 스키마 v6, `domain/metrics.py`, GitHub GraphQL 기준선(`POST /operator/github/sources/{id}/baseline`), `/metrics.json`·`/metrics.csv`·`/metrics` 화면(운영자 전용). 검증은 모두 대역이다 — 실제 GitHub·모델 호출 없음([VERIFICATION_LOG](VERIFICATION_LOG.md) 2026-09-27 절).

다음 순서:
1. `feat-9-measure` → `service` `--no-ff` 병합(사용자 지시 때).
2. 실제 OpenArchive 기준선 가져오기 — **사용자 지시 후에만**. 토큰에 Issues·Pull requests 읽기 권한이 필요하다([GitHub 런북](github/README.md) 1절). 도입 전 경계는 소스 연결 시각(`github_sources.created_at`)이다.
3. `10-selfhost` 는 2026-09-27 step 0~8 완료(`feat-10-selfhost`, `service` 병합 전 — 사용자 지시 때 `--no-ff`). 한 명령 설치(`deploy/selfhost/install.sh`)·고정 워크스페이스 로그인(`WORKFLOW_MODE=selfhost`)·백업 CLI·러너 launchd, [ADR-0016](adr/0016-selfhost-docker-fixed-workspace.md)·[SELFHOST](SELFHOST.md). 실제 Docker 로 설치·재시작 보존·백업 복원 확인([VERIFICATION_LOG](VERIFICATION_LOG.md) 2026-09-27 phase 10 절). 러너 launchd 실제 적재는 미확인. 다음은 `11-github-app`(2026-09-27 계획 — GitHub 연결 화면의 내부 ID·`.env` 토큰 입력을 사용자 자신의 GitHub App 버튼 연결·열린 이슈 전부 가져오기·지시 실행·러너 원격 자동 매칭으로 바꾼다, [ADR-0017](adr/0017-github-app-connection.md)·ARCHITECTURE "GitHub App 연결 — phase 11", `feat-11-github-app`), 그 뒤 `12-real-repo`(알림 웹훅·검증 환경변수 선언·worktree 에 없는 `.venv`/`node_modules` 처리·실연동 3건 이상). `12-real-repo` 에 넣을 것(2026-09-27 확인·합의): 새 업무의 기준 커밋은 러너 `register` 때 읽은 HEAD(`connector/cli.py:124` → `agents.base_commit`)로 고정돼, 등록 뒤 생긴 커밋(직접 작업·병합·pull)을 따라가지 않는다(`server/worker.py:1203`). 러너가 현재 HEAD 를 중앙에 다시 보고해 그 값을 기준으로 쓰게 고친다.

phase 9 e2e 에서 발견한 결함 — 수정됨(step 13): 재작업 상한 1 에서 수정 요청 검토가 재작업을 시작시킨 뒤, 재작업 결과가 판정되기 전 tick 이 같은 검토를 다시 평가하면 `domain/task_followup.py` `_after_review` 가 `rounds_used(1) >= 상한(1)` 으로 `rework_limit_reached` 사람 요청을 하나 더 만들던 문제. 이제 그 검토가 이미 재작업(`rework:{검토 실행}`, 워커가 수정 Task 실행의 `start_key` 로 `handled_cause_keys` 에 넣음)을 일으켰으면 상한 판단 전에 `none`("이미 재작업을 시작한 검토") 을 낸다. 회귀: `tests/workflow/domain/test_task_followup.py`·`tests/workflow/server/test_task_cycle.py`, e2e `tests/e2e/test_metrics.py` 는 사람 요청 0·개입 1 로 단정.

공모전 관련 작업은 더 하지 않는다(2026-09-27 사용자 결정). 공개 데모 VM 과 `main` 은 그대로 두고 손대지 않는다. 공모전 문서는 [보관 자료](archive/2026-09-27-contest-and-history/)로 옮겼다.

## 지금 상태

| 항목 | 상태 |
|---|---|
| 브랜치 | `service` 가 실서비스 통합 브랜치. phase 6·7·8 과 문서 정리 포함. 새 phase 는 `service` 에서 `feat-*` 로 분기하고 끝나면 `--no-ff` 병합. 원격 푸시는 사용자 지시 때만 |
| 완료 phase | 0-mvp, 1-diag-fix, 2-model-compare, 5-scripted-demo(공모전 데모), 6-typed-handoff([ADR-0009](adr/0009-registered-kinds-and-succession-rules.md)), 7-n8n-gateway([ADR-0010](adr/0010-n8n-inbox-and-callback.md)), 8-github-task-cycle([ADR-0014](adr/0014-github-task-cycle.md)), 9-measure([ADR-0015](adr/0015-measurement-events-and-baseline.md), `feat-9-measure` — `service` 병합 전), 10-selfhost([ADR-0016](adr/0016-selfhost-docker-fixed-workspace.md), `feat-10-selfhost` — `service` 병합 전). 4-claude-issues 는 step 3 에서 종료 |
| 계획만 | `3-limit-wait`([ADR-0007](adr/0007-usage-limit-wait-policy.md), 사용량 한도 대기). 실사용에서 한도에 걸리는 빈도를 보고 당긴다 |
| 검증 | phase 10 기준(2026-09-27) `python3 -m pytest -q` 2464 passed/55 skipped, `ruff` 통과, `WORKFLOW_E2E=1` e2e 54 passed/1 skipped, `WORKFLOW_DOCKER=1` 셀프호스트 e2e 1 passed(실제 Docker) |
| 실연동 | 2026-09-23 실제 GitHub·실제 Claude 로 `bug_fix` → `code_review` 1회 통과([VERIFICATION_LOG](VERIFICATION_LOG.md)). `changes_requested` 재작업은 실연동 미관찰. 측정·기준선은 실연동 없음(대역만) |
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
