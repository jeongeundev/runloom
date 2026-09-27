# 현재 인계 — 업무 목록과 결과 기반 자동 실행

갱신일: 2026-09-27. 2026-09-26 이전 기록은 [보관 자료](archive/2026-09-27-contest-and-history/CURRENT_HANDOFF-until-2026-09-26.md).

## 다음 작업: `12-real-repo` 설계 (새 세션은 여기서 시작)

**12-real-repo 진행 중** (2026-09-27, 브랜치 `feat-12-real-repo`): step 0 에서 [ADR-0018](adr/0018-real-repo-cycle.md)·ARCHITECTURE "실제 저장소 순환 — phase 12"·CONTRACT 14절로 설계 고정. 진행 상태는 `phases/12-real-repo/index.json`.

[MVP 계획](product/MVP_PLAN.md)이 최신 기준이다(2026-09-25~26 사용자 합의). 목표: **2026-10-02 까지 Runloom 단독으로 OpenArchive(`jeongeundev/OpenArchive`) 실제 이슈를 순환 처리하고, 셀프호스트로 배포하며, 도입 전후를 지표로 보여준다.**

2026-09-27 에 한 일: `9-measure`·`10-selfhost`·`11-github-app` 완료·`service` 병합. 이 Mac 에 셀프호스트 설치(`deploy/selfhost/install.sh`, compose 프로젝트 `runloom`, http://127.0.0.1:8000, 스키마 7). 사용자가 브라우저로 실제 GitHub App(`runloom-gwufov`, jeongeundev)을 만들고 OpenArchive 에 설치 → 열린 이슈 17건이 "실행 지시 전"으로 들어옴(에이전트 미시작). 실제 기준선 가져오기 완료: **24건, 이슈 열림 → 병합 중앙값 7시간 28분**(연결 시각 2026-09-27T11:05:13Z 이전). 실제 사용 중 고친 것(모두 `service` 직접 커밋, 재현 테스트 먼저):
- `7ab6427` manifest 에서 `hook_attributes` 제거 — GitHub 가 `active:false` 여도 127.0.0.1 hook url 을 거부.
- `5c62cc0` manifest 교환에서 `webhook_secret` 선택값 — 웹훅 없는 App 은 null 로 온다. 이 실패로 만들어진 첫 App 은 개인 키를 잃어 사용자가 GitHub 에서 지웠다.
- `99ee3d7` 기준선 가져오기가 소스의 GitHub 자격(App·PAT·환경변수)을 씀 — 전에는 `WORKFLOW_GITHUB_TOKEN` 만 봤다.
- `00c3f01` 저장소 카드에 기준선 결과 한 줄(건수·중앙값·가져온 시각·지표 링크), 가져오기 전 설명, 카드 제목 내부 ID 숨김.

`12-real-repo` 에 넣을 것:
1. **러너 붙이기(사용자 흐름)** — 아직 러너가 없다(카드: "이 저장소를 등록한 러너 없음"). 연결 코드 → `connect` → OpenArchive 폴더 `register` → `install-runner.sh`. 카드의 러너 안내가 명령어만 보여 준다 — 사용자는 내부 ID·형식값 입력을 병목으로 본다(2026-09-27 피드백: "실제 서비스 흐름으로"). 러너 등록도 같은 기준으로 다듬는다.
2. **새 업무 기준 커밋 추적** — 기준 커밋이 러너 `register` 때 HEAD(`connector/cli.py:124` → `agents.base_commit`)로 고정돼 등록 뒤 커밋(사용자가 OpenArchive 를 계속 개발 중)을 따라가지 않는다(`server/worker.py:1203`). 러너가 현재 HEAD 를 다시 보고해 그 값을 쓰게 고친다.
3. **OpenArchive 검증 환경** — `scripts/check.sh` 는 `DATABASE_URL`(pgvector)·`backend/.venv`·`node_modules` 가 필요한데 worktree 에 없다. 검증 프로필이 넘길 환경변수를 러너 로컬 등록에서 선언, worktree 에서 실행 환경을 쓰는 방법.
4. **알림 웹훅**(사람 차례·실패 → 등록 URL, Discord 웹훅 호환).
5. **실연동 3건 이상**: [에이전트에게 맡기기] 또는 `runloom` 라벨로 이슈 → 수정 → 검토 → 사람 차례, VERIFICATION_LOG 기록. 이때 phase 11 미확인 항목(`setup_action` 값, 설치 URL `state` 가 setup 으로 돌아오는지)도 기록.

phase 11 에서 남긴 것: 수집 실패(rate limit·권한)는 DB 에 저장하지 않아 카드에 안 보인다(워커 로그만). 설치에서 빠졌다 다시 들어온 저장소의 소스는 멈춘 채다(고급 설정 `수집 켜기`). manifest code 는 callback URL 이라 접근 로그에 남는다(한 번 쓰면 무효). 브라우저 스크립트(자동 제출 폼·`data-json-action`)는 서버 렌더만 확인했다.

phase 9 e2e 에서 발견한 결함 — 수정됨(step 13): 재작업 상한 1 에서 수정 요청 검토가 재작업을 시작시킨 뒤, 재작업 결과가 판정되기 전 tick 이 같은 검토를 다시 평가하면 `domain/task_followup.py` `_after_review` 가 `rounds_used(1) >= 상한(1)` 으로 `rework_limit_reached` 사람 요청을 하나 더 만들던 문제. 이제 그 검토가 이미 재작업(`rework:{검토 실행}`, 워커가 수정 Task 실행의 `start_key` 로 `handled_cause_keys` 에 넣음)을 일으켰으면 상한 판단 전에 `none`("이미 재작업을 시작한 검토") 을 낸다. 회귀: `tests/workflow/domain/test_task_followup.py`·`tests/workflow/server/test_task_cycle.py`, e2e `tests/e2e/test_metrics.py` 는 사람 요청 0·개입 1 로 단정.

공모전 관련 작업은 더 하지 않는다(2026-09-27 사용자 결정). 공개 데모 VM 과 `main` 은 그대로 두고 손대지 않는다. 공모전 문서는 [보관 자료](archive/2026-09-27-contest-and-history/)로 옮겼다.

## 지금 상태

| 항목 | 상태 |
|---|---|
| 브랜치 | `service` 가 실서비스 통합 브랜치. phase 6·7·8 과 문서 정리 포함. 새 phase 는 `service` 에서 `feat-*` 로 분기하고 끝나면 `--no-ff` 병합. 원격 푸시는 사용자 지시 때만 |
| 완료 phase | 0-mvp, 1-diag-fix, 2-model-compare, 5-scripted-demo(공모전 데모), 6-typed-handoff([ADR-0009](adr/0009-registered-kinds-and-succession-rules.md)), 7-n8n-gateway([ADR-0010](adr/0010-n8n-inbox-and-callback.md)), 8-github-task-cycle([ADR-0014](adr/0014-github-task-cycle.md)), 9-measure([ADR-0015](adr/0015-measurement-events-and-baseline.md)), 10-selfhost([ADR-0016](adr/0016-selfhost-docker-fixed-workspace.md)), 11-github-app([ADR-0017](adr/0017-github-app-connection.md)) — 모두 `service` 병합됨(미푸시). 4-claude-issues 는 step 3 에서 종료 |
| 계획만 | `3-limit-wait`([ADR-0007](adr/0007-usage-limit-wait-policy.md), 사용량 한도 대기). 실사용에서 한도에 걸리는 빈도를 보고 당긴다 |
| 검증 | 2026-09-27 마지막 수정(`00c3f01`) 기준 `python3 -m pytest -q` 2692 passed/61 skipped, `ruff` 통과. `WORKFLOW_E2E=1` e2e 60 passed/1 skipped(phase 11 기준), `WORKFLOW_DOCKER=1` 셀프호스트 e2e 1 passed(phase 11 브랜치에서 다시 확인) |
| 실연동 | 2026-09-23 실제 GitHub·실제 Claude 로 `bug_fix` → `code_review` 1회 통과([VERIFICATION_LOG](VERIFICATION_LOG.md)). `changes_requested` 재작업은 실연동 미관찰. 2026-09-27 실제 GitHub App 생성·설치·이슈 수집(17건)·기준선(24건) 성공 — 에이전트 실행은 아직 없음 |
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
