# 현재 인계 — 업무 목록과 결과 기반 자동 실행

갱신일: 2026-09-27. 2026-09-26 이전 기록은 [보관 자료](archive/2026-09-27-contest-and-history/CURRENT_HANDOFF-until-2026-09-26.md).

## 다음 작업: 사용자 브라우저로 실제 GitHub App 생성·설치(OpenArchive) → 기준선 가져오기 → `12-real-repo`

[MVP 계획](product/MVP_PLAN.md)이 최신 기준이다(2026-09-25~26 사용자 합의). 목표: **2026-10-02 까지 Runloom 단독으로 OpenArchive(`jeongeundev/OpenArchive`) 실제 이슈를 순환 처리하고, 셀프호스트로 배포하며, 도입 전후를 지표로 보여준다.**

`11-github-app` 은 2026-09-27 step 0~9 를 마쳤다(`feat-11-github-app`, `service` 병합 전 — 사용자 지시 때 `--no-ff`). [ADR-0017](adr/0017-github-app-connection.md)·ARCHITECTURE "GitHub App 연결 — phase 11" 절이 기준이다. 들어간 것: 0600 비밀 저장소(`WORKFLOW_SECRET_DIR`, 백업 제외), manifest 로 사용자 자신의 GitHub App 만들기 → 설치 → 설치 저장소마다 소스 자동 생성(`intake: all_open`), App JWT·설치 토큰(메모리 캐시), 열린 이슈 전부 가져오기 + 실행은 [에이전트에게 맡기기]·`runloom` 라벨로 지시한 것만(`not_delegated`), 러너 `origin` 의 `owner/name` 으로 Agent·로컬 저장소·검증 프로필 자동 매칭, 고급 PAT 붙여 넣기, `/operator/github` 카드 화면. 예전 환경변수 토큰·라벨 범위 소스는 그대로 동작한다. 검증은 모두 대역이다 — 실제 github.com·api.github.com 호출 없음(`tests/e2e/test_github_app.py`, [VERIFICATION_LOG](VERIFICATION_LOG.md) 2026-09-27 phase 11 절).

다음 순서:
1. `feat-11-github-app` → `service` `--no-ff` 병합, `deploy/selfhost/install.sh` 재실행으로 셀프호스트에 반영(사용자 지시 때).
2. **사용자 브라우저로 실제 App 생성·설치** — [SELFHOST "GitHub 연결"](SELFHOST.md#github-연결) 순서: `/operator/github` [GitHub 연결] → GitHub 에서 App 이름 확인 → [Create GitHub App] → 설치 화면에서 *Only select repositories* 로 `jeongeundev/OpenArchive` 선택 → [Install] → 카드 확인 → 러너에서 OpenArchive 클론 폴더 register(`origin` 이 GitHub 인지) → 자동 매칭 `(자동)` 확인. 이때 확인할 미확인 항목: `setup_action` 실제 값, 설치 URL 의 `state` 가 setup 으로 돌아오는지, 실제 GitHub 화면 문구. 결과는 VERIFICATION_LOG 에 실연동으로 기록한다.
3. 실제 OpenArchive 기준선 가져오기 — **사용자 지시 후에만**. App 권한에 Pull requests 읽기가 들어 있다. 도입 전 경계는 소스 연결 시각(`github_sources.created_at`)이다.
4. `12-real-repo`(알림 웹훅·검증 환경변수 선언·worktree 에 없는 `.venv`/`node_modules` 처리·실연동 3건 이상). `12-real-repo` 에 넣을 것(2026-09-27 확인·합의): 새 업무의 기준 커밋은 러너 `register` 때 읽은 HEAD(`connector/cli.py:124` → `agents.base_commit`)로 고정돼, 등록 뒤 생긴 커밋(직접 작업·병합·pull)을 따라가지 않는다(`server/worker.py:1203`). 러너가 현재 HEAD 를 중앙에 다시 보고해 그 값을 기준으로 쓰게 고친다.

phase 11 에서 남긴 것: 수집 실패(rate limit·권한)는 DB 에 저장하지 않아 카드에 안 보인다(워커 로그만). 설치에서 빠졌다 다시 들어온 저장소의 소스는 멈춘 채다(고급 설정 `수집 켜기`). manifest code 는 callback URL 이라 접근 로그에 남는다(한 번 쓰면 무효). 브라우저 스크립트(자동 제출 폼·`data-json-action`)는 서버 렌더만 확인했다.

phase 9 e2e 에서 발견한 결함 — 수정됨(step 13): 재작업 상한 1 에서 수정 요청 검토가 재작업을 시작시킨 뒤, 재작업 결과가 판정되기 전 tick 이 같은 검토를 다시 평가하면 `domain/task_followup.py` `_after_review` 가 `rounds_used(1) >= 상한(1)` 으로 `rework_limit_reached` 사람 요청을 하나 더 만들던 문제. 이제 그 검토가 이미 재작업(`rework:{검토 실행}`, 워커가 수정 Task 실행의 `start_key` 로 `handled_cause_keys` 에 넣음)을 일으켰으면 상한 판단 전에 `none`("이미 재작업을 시작한 검토") 을 낸다. 회귀: `tests/workflow/domain/test_task_followup.py`·`tests/workflow/server/test_task_cycle.py`, e2e `tests/e2e/test_metrics.py` 는 사람 요청 0·개입 1 로 단정.

공모전 관련 작업은 더 하지 않는다(2026-09-27 사용자 결정). 공개 데모 VM 과 `main` 은 그대로 두고 손대지 않는다. 공모전 문서는 [보관 자료](archive/2026-09-27-contest-and-history/)로 옮겼다.

## 지금 상태

| 항목 | 상태 |
|---|---|
| 브랜치 | `service` 가 실서비스 통합 브랜치. phase 6·7·8 과 문서 정리 포함. 새 phase 는 `service` 에서 `feat-*` 로 분기하고 끝나면 `--no-ff` 병합. 원격 푸시는 사용자 지시 때만 |
| 완료 phase | 0-mvp, 1-diag-fix, 2-model-compare, 5-scripted-demo(공모전 데모), 6-typed-handoff([ADR-0009](adr/0009-registered-kinds-and-succession-rules.md)), 7-n8n-gateway([ADR-0010](adr/0010-n8n-inbox-and-callback.md)), 8-github-task-cycle([ADR-0014](adr/0014-github-task-cycle.md)), 9-measure([ADR-0015](adr/0015-measurement-events-and-baseline.md)), 10-selfhost([ADR-0016](adr/0016-selfhost-docker-fixed-workspace.md)) — 둘 다 `service` 병합됨, 11-github-app([ADR-0017](adr/0017-github-app-connection.md), `feat-11-github-app` — `service` 병합 전). 4-claude-issues 는 step 3 에서 종료 |
| 계획만 | `3-limit-wait`([ADR-0007](adr/0007-usage-limit-wait-policy.md), 사용량 한도 대기). 실사용에서 한도에 걸리는 빈도를 보고 당긴다 |
| 검증 | phase 11 기준(2026-09-27) `python3 -m pytest -q` 2685 passed/61 skipped, `ruff` 통과, `WORKFLOW_E2E=1` e2e 60 passed/1 skipped(새 `test_github_app.py` 6). phase 10 의 `WORKFLOW_DOCKER=1` 셀프호스트 e2e 1 passed(실제 Docker)는 phase 11 에서 다시 돌리지 않았다 |
| 실연동 | 2026-09-23 실제 GitHub·실제 Claude 로 `bug_fix` → `code_review` 1회 통과([VERIFICATION_LOG](VERIFICATION_LOG.md)). `changes_requested` 재작업은 실연동 미관찰. 측정·기준선·GitHub App 연결은 실연동 없음(대역만) |
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
