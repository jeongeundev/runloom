# ARCHITECTURE — 공개 데모 구성과 오프라인 절

2026-09-27 `docs/ARCHITECTURE.md` 에서 원문 그대로 옮겼다. 공모전(원티드 AI Championship 2026) 공개 데모·이전 MVP 이력이며 현행 요구사항이 아니다.

### 공개 데모 구성 — VM 한 대, 대본 에이전트 (2026-09-21 확정)

심사 기간의 공개 데모는 [ADR-0008](adr/0008-public-demo-scripted-agents.md)을 따른다. 위 표의 Mac 두 행이 VM 으로 옮겨오고 실제 모델·실제 Codex/Claude 는 돌지 않는다. 절차는 [DEPLOY](DEPLOY.md), 파일은 `deploy/`.

| 구성 | 위치 | 실행 방식 | 데이터 |
|---|---|---|---|
| Caddy | VM | systemd, 도메인 인증서 자동 발급 | — |
| 중앙 웹/API · 중앙 워커 | VM, `127.0.0.1:8000` | systemd `workflow-central`·`workflow-worker`, env `/etc/workflow/central.env`(한도 200/5000 — 비용 0) | `/var/lib/workflow/central/` |
| 진단 API · 진단 워커 | VM, `127.0.0.1:8100`, 외부 비공개 | systemd `workflow-diag`·`workflow-diag-worker`, env `/etc/workflow/diag.env`(`DIAG_MODEL=fake`, `OPENAI_API_KEY` 비움) | `/var/lib/workflow/diag/` |
| 연결 프로그램 + 대본 에이전트 | VM | systemd `workflow-connector`, env `/etc/workflow/connector.env`(`WORKFLOW_CONNECTOR_HOME`, `WORKFLOW_SCRIPT_PACE_SECONDS=25`). PATH 앞의 `deploy/bin/{codex,claude}` 래퍼가 `workflow.scripted.*` 를 띄운다 | `/var/lib/workflow/connector/` (state.sqlite, 토큰 0600) |
| 데모 저장소 | VM | `scripts/scaffold_demo_repo.py`, 기준 커밋 `report-base` 고정 | `/var/lib/workflow/demo/demo-report-repo`, worktree 는 옆 `demo-report-repo-worktrees/`(결과 업로드 뒤 정리) |

`central.env` 의 n8n 키 두 개(`WORKFLOW_CALLBACK_HOSTS`·`WORKFLOW_PUBLIC_URL`, 비밀값 아님)는 공개 데모에서 허용 목록을 **비워** callback 을 받지 않고(`callback_url` 이 있는 접수는 422), 공개 주소는 배포 도메인으로 둔다(`deploy/env/central.env.example`). 카탈로그 세 Agent(`agent-ops-demo`·`agent-codex-mac`·`agent-claude-mac`)는 `seed_demo.py --scripted` 로 `demo_scripted=1` 이며 화면에 `시연용 · 대본 재생` 을 표시한다. 계약·검증기·worktree·실제 pytest·상태 규칙은 실제 어댑터와 같다. `deploy/launchd/`(운영자 Mac)는 셀프호스트 실사용용으로 남기고 공개 데모에서는 쓰지 않는다.

제안 — 사용자 확인 전: Mac 오프라인 동안에도 심사자가 B 결과를 볼 수 있도록, 운영자 세션에서 실제로 완료한 A → B 업무 한 쌍을 "예시 실행"으로 읽기 전용 공개한다. "운영자가 {날짜}에 실행한 기록"으로 표시하며 고정 답변 재생이 아니다. 구현 범위가 늘어나므로 채택 여부는 별도 확인한다.
