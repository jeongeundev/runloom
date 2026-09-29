# Step 10: task-model-verify — e2e 업무 기준, v9 사본 마이그레이션, 문서·인계

## 읽어야 할 파일

- AGENTS.md
- phases/14-task-model/README.md, phases/14-task-model/index.json (step 0~9 summary)
- docs/adr/0020-work-items-and-stages.md, docs/ARCHITECTURE.md ("업무와 단계 — phase 14")
- tests/e2e/ (conftest.py, test_github_cycle.py, test_github_app.py, test_real_repo.py, test_metrics.py, test_selfhost.py)
- src/workflow/server/backup.py, tests/workflow/server/test_backup.py
- docs/VERIFICATION_LOG.md, docs/CURRENT_HANDOFF.md, docs/SELFHOST.md, docs/product/REDESIGN_PLAN.md

## 작업

1. e2e 에 업무 단정을 더한다: `test_real_repo.py`·`test_github_cycle.py` 한 줄기가 끝났을 때 업무 1개·단계 2개·업무 상태 흐름(`새로 들어옴` → 지시 → `에이전트 작업 중` → `PR · 검토` → `완료`)·브랜치 `runloom/RUN-1`·PR 제목 키. 실패 한 줄기(검증 실패 → `내 차례` → [다시 맡기기] → 새 단계) 하나 추가. `WORKFLOW_E2E=1 python3 -m pytest tests/e2e -q`.
2. v9 → v10 사본 확인: 현재 셀프호스트와 같은 모양의 v9 DB(OpenArchive 류 GitHub 업무 20여 건 — 대부분 지시 전, 몇 건은 fix+review+PR 병합, sandbox 류 1건 완료, 기준선 행)를 테스트 안에서 만들어 올리고, 업무 수·키·상태 분포·기준선 보존·백업 왕복을 단정한다. **사용자의 실제 셀프호스트 볼륨·백업 파일을 읽지 않는다.**
3. 셀프호스트 Docker e2e: `WORKFLOW_DOCKER=1 python3 -m pytest tests/e2e/test_selfhost.py -q` — 먼저 테스트 코드를 읽어 사용자의 compose 프로젝트 `runloom`·포트 8000·볼륨 `runloom_workflow-data` 와 겹치지 않는지 확인하고, 겹치거나 Docker 가 없으면 실행하지 않고 "미실행(이유)" 로 summary 에 적는다.
4. 문서: `docs/VERIFICATION_LOG.md` "phase 14 업무·단계" 절(명령·결과 수·미실행 항목), `docs/SELFHOST.md` 업그레이드 절에 "v10 — 업무 표 생성, 백업 먼저, 진행 중 실행은 끝난 뒤 업그레이드 권장" 한 줄, `docs/CURRENT_HANDOFF.md` "다음 작업" 을 "14 완료 → service 병합 → 셀프호스트 재설치(사용자 지시) → 15-team 설계" 로, `docs/product/REDESIGN_PLAN.md` 16절 제안 중 14 에서 확정된 것 표시.

## 테스트 먼저

1·2 의 테스트를 먼저 작성해 실패(또는 이미 통과)를 확인한 뒤 진행한다. 제품 코드를 고쳐야 하면 그 결함을 재현하는 테스트를 먼저 쓴다.

소스·배포 파일 변경 전에 `tests/` 미러 경로에 실패 테스트를 작성하고 실패 원인이 의도한 것인지 확인한다(`tdd-guard.sh` 가 테스트 없는 소스 작성을 막는다). 구현 후 해당 테스트와 전체 회귀를 통과시킨다.

## Acceptance Criteria

```bash
python3 -m pytest -q
python3 -m ruff check .
WORKFLOW_E2E=1 python3 -m pytest tests/e2e -q   # e2e 를 건드린 step
```

위 커맨드와 이 step 의 테스트 항목을 모두 확인한다.

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 규칙을 확인한다: `domain/` 은 FastAPI·sqlite3·HTTPX·subprocess·Git 을 import 하지 않는다 / `server/` 와 `connector/` 는 서로 import 하지 않고 `contracts/` 만 공유한다 / 외부 입력(요청 본문·이슈 본문·모델 응답)에서 명령·경로·브랜치 이름을 받아 실행하지 않는다 / 비밀값(연결 토큰, `OPERATOR_TOKEN`, `SESSION_SECRET`, GitHub App 비밀·설치 토큰, PAT, 알림 웹훅 URL, 러너 로컬 등록의 환경변수 값)은 DB·로그·응답·템플릿·백업·중앙에 넣지 않는다 / GLOSSARY 이름을 그대로 쓴다 / phase 8·11·12 의 GitHub 순환(수집 → 수정 → 검토 → 초안 PR → 병합 추적)이 그대로 동작한다.
3. 성공이면 `phases/14-task-model/index.json` 의 step 10 만 `completed` 로 바꾸고 `summary` 에 생성·수정·삭제 파일, 결정, 검증 결과(테스트 수), 다음 step 이 주의할 점을 한 줄로 남긴다.
4. 3회 수정 후에도 실패하면 `error` + `error_message`, 사용자 결정·외부 자원이 필요하면 `blocked` + `blocked_reason` 을 기록한다. 실행하지 않은 검증을 `completed` 로 표시하지 않는다.

## 금지사항

- 실제 GitHub(api.github.com·github.com)·Discord·Jira 를 호출하지 않는다(공식 문서 읽기는 허용). 이유: 모든 step 은 httpx `MockTransport`·가짜 GitHub·임시 bare 저장소로 검증한다. 실연동은 phase 뒤 사용자와 함께 한다.
- 사용자가 띄워 둔 셀프호스트(compose 프로젝트 `runloom`, 포트 8000, 볼륨 `runloom_workflow-data`)·`deploy/selfhost/.env`·러너 홈(`~/Library/Application Support/workflow-connector/`)·launchd·`/Users/kje/demo/*`(OpenArchive·runloom-sandbox 클론)를 읽거나 바꾸지 않는다. 컨테이너를 띄우거나 멈추지 않는다. 이유: 실사용 중인 환경이다.
- 종류 이름(`bug_fix`·`code_review`)으로 새 분기를 만들지 않는다. 이유: 업무 종류·후속 규칙은 워크스페이스 등록 데이터다(ADR-0009, AGENTS.md).
- `tasks` 테이블을 재생성하거나 이름을 바꾸지 않는다. 이유: ADR-0020 결정 — 업무 상태는 `work_items` 에 두고 단계 상태는 그대로 둔다.
- 새 화면 구성(담당자별 묶음·보드·상세 패널·연결 화면)을 만들지 않는다. 이유: 16-work-ui 범위. 이 phase 의 화면 변경은 step 9 최소 변경뿐이다.
- 사용자 변경을 삭제하거나 stash·reset 하지 않는다. 커밋은 하네스가 한다.
- 이 step 범위 밖 모듈을 "개선"하지 않는다. 기존 테스트의 단정을 약하게 만들어 통과시키지 않는다.
