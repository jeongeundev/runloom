# 현재 인계 — 업무 목록과 결과 기반 자동 실행

갱신일: 2026-09-27. 2026-09-26 이전 기록은 [보관 자료](archive/2026-09-27-contest-and-history/CURRENT_HANDOFF-until-2026-09-26.md).

## 다음 작업: 10/2 MVP phase 설계

[MVP 계획](product/MVP_PLAN.md)이 최신 기준이다(2026-09-25~26 사용자 합의). 목표: **2026-10-02 까지 Runloom 단독으로 OpenArchive(`jeongeundev/OpenArchive`) 실제 이슈를 순환 처리하고, 셀프호스트로 배포하며, 도입 전후를 지표로 보여준다.**

제안 phase 순서는 `9-measure`(이벤트 보충·지표 화면/API·GitHub 이력 기준선) → `10-selfhost`(한 명령 설치·데이터 보존·새 ADR) → `11-real-repo`(알림 웹훅·검증 환경변수 선언·worktree 에 없는 `.venv`/`node_modules` 처리·실연동 3건 이상). `11-real-repo` 에 넣을 것(2026-09-27 확인·합의): 새 업무의 기준 커밋은 러너 `register` 때 읽은 HEAD(`connector/cli.py:124` → `agents.base_commit`)로 고정돼, 등록 뒤 생긴 커밋(직접 작업·병합·pull)을 따라가지 않는다(`server/worker.py:1203`). 러너가 현재 HEAD 를 중앙에 다시 보고해 그 값을 기준으로 쓰게 고친다. 새 세션은 `9-measure` 설계부터 시작한다 — 먼저 `executions`·`execution_events`·`task_verdicts`·`human_requests`·`human_responses` 에 이미 있는 시각·버전과 빠진 것을 코드로 확인한다.

공모전 관련 작업은 더 하지 않는다(2026-09-27 사용자 결정). 공개 데모 VM 과 `main` 은 그대로 두고 손대지 않는다. 공모전 문서는 [보관 자료](archive/2026-09-27-contest-and-history/)로 옮겼다.

## 지금 상태

| 항목 | 상태 |
|---|---|
| 브랜치 | `service` 가 실서비스 통합 브랜치. phase 6·7·8 과 문서 정리 포함. 새 phase 는 `service` 에서 `feat-*` 로 분기하고 끝나면 `--no-ff` 병합. 원격 푸시는 사용자 지시 때만 |
| 완료 phase | 0-mvp, 1-diag-fix, 2-model-compare, 5-scripted-demo(공모전 데모), 6-typed-handoff([ADR-0009](adr/0009-registered-kinds-and-succession-rules.md)), 7-n8n-gateway([ADR-0010](adr/0010-n8n-inbox-and-callback.md)), 8-github-task-cycle([ADR-0014](adr/0014-github-task-cycle.md)). 4-claude-issues 는 step 3 에서 종료 |
| 계획만 | `3-limit-wait`([ADR-0007](adr/0007-usage-limit-wait-policy.md), 사용량 한도 대기). 실사용에서 한도에 걸리는 빈도를 보고 당긴다 |
| 검증 | phase 8 기준 `python3 -m pytest -q` 2164 passed/49 skipped, `ruff` 통과, `WORKFLOW_E2E=1` e2e 49 passed |
| 실연동 | 2026-09-23 실제 GitHub·실제 Claude 로 `bug_fix` → `code_review` 1회 통과([VERIFICATION_LOG](VERIFICATION_LOG.md)). `changes_requested` 재작업은 실연동 미관찰 |
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
