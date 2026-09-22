# Step 15: 문서 동기화와 실연동 준비

## 읽어야 할 파일

- AGENTS.md
- phases/8-github-task-cycle/README.md
- docs/product/ROADMAP.md (MVP/v0.1·지표)
- docs/PRD.md (MVP·5개 업무 수용 기준)
- docs/ARCHITECTURE.md (실서비스 전환·현재 계약)
- docs/CONTRACT.md
- docs/GLOSSARY.md
- docs/adr/0013-existing-tasks-first-staged-rollout.md
- docs/adr/0014-github-task-cycle.md (step 0 산출물)
- phases/8-github-task-cycle/index.json (완료 step summary)
- tests/e2e/test_scenario.py
- scripts/local_stack.py
- tests/workflow/server
- tests/workflow/connector
- docs/PRD.md
- docs/ARCHITECTURE.md
- docs/VERIFICATION_LOG.md
- docs/product/ROADMAP.md

먼저 실제 파일과 이전 step 산출물을 읽는다. 미래 타입·함수명은 step 0에서 고정하며 이후 변경이 필요하면 같은 이름의 계약·테스트·문서를 함께 갱신한다. 대화 이력을 전제로 판단하지 않는다.

## 작업

docs/github/README.md에 셀프호스트 운영자 설정·최소 GitHub 권한·환경변수 이름·저장소/이슈 scope·검증 프로필·동일 기기 수정/검토 등록·데이터 보존 업그레이드·중지·복구·결과 반영 unknown 조정을 작성한다. 테스트 결과와 실제 미검증을 구분한다. 기존 docs 계약을 구현 이름으로 검증하고 phase 계획의 차이를 기록한다.

## 인터페이스와 책임

실연동 체크리스트: 운영자가 지정한 테스트 repo/issue, 댓글 작성 범위, 사용 Agent/구독·비용 범위, 실제 baseline/review/rework 기대 결과. 실제 토큰값을 파일/채팅/로그에 쓰지 않는다.

## 테스트 먼저

전체 pytest·ruff 및 로컬 e2e 결과를 기록하고 문서 링크/명령/설정 이름을 확인한다. 이 단계에서 새 계약 미구현이나 핵심 회귀를 문서로 숨기지 않는다.

소스 변경 전에 대응하는 tests/ 미러 경로에 실패 테스트를 작성하고 실패 원인을 확인한다. 구현 후 해당 테스트와 전체 회귀를 통과시킨다. 문서 전용 변경은 불필요한 제품 테스트를 만들지 않는다.

## Acceptance Criteria

```bash
python3 -m pytest -q
python3 -m ruff check .
WORKFLOW_E2E=1 python3 -m pytest tests/e2e -q
```

위 커맨드와 이 step의 테스트 항목을 모두 확인한다.

## 검증 절차

1. 이 step의 책임 범위만 변경하고 도메인 외부 I/O 금지, server/connector 상호 import 금지, 비밀 환경변수 규칙을 확인한다.
2. 입력·결과·판정·완료·승인을 구분하고 기존 데모·n8n 계약 회귀를 확인한다.
3. 성공이면 phases/8-github-task-cycle/index.json의 step 15만 completed로 갱신하고 summary에 생성 파일·계약 결정·검증 결과·다음 step 주의점을 남긴다.
4. 3회 수정 후 실패하면 error/error_message, 외부 자격·사용자 선택 등 필수 정보가 없으면 blocked/blocked_reason을 기록한다. 미실행 검증을 completed로 표시하지 않는다.

## 금지사항

- main·공개 데모 VM을 변경하거나 배포하지 않는다. 이 phase는 service 기반 실서비스다.
- 사용자 변경을 삭제하거나 자동 stash/commit하지 않는다. 실행 전 작업 트리 정리는 README를 따른다.
- 임의 이슈의 명령·경로·URL로 실행 범위를 확장하지 않는다. 등록 대상과 고정 adapter 인자만 사용한다.
- 로그 분석·Jev·Jira·범용 그래프 편집기를 추가하지 않는다. 이 phase의 최소 GitHub 업무 순환 범위가 아니다.
- 실제 GitHub 쓰기·유료 호출은 step 16의 지정된 검증 범위 외에는 하지 않는다. 앞 step의 테스트는 대역을 사용한다.
