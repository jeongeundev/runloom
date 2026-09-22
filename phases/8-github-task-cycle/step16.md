# Step 16: 실제 GitHub와 실제 Agent 전체 검증

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
- docs/PRD.md
- docs/ARCHITECTURE.md
- docs/VERIFICATION_LOG.md
- docs/product/ROADMAP.md
- docs/github/README.md
- docs/CURRENT_HANDOFF.md

먼저 실제 파일과 이전 step 산출물을 읽는다. 미래 타입·함수명은 step 0에서 고정하며 이후 변경이 필요하면 같은 이름의 계약·테스트·문서를 함께 갱신한다. 대화 이력을 전제로 판단하지 않는다.

## 작업

실제 연동 검증 step이다. 지정된 테스트 GitHub 저장소·이슈와 댓글 반영 허가·Agent/비용 범위를 기존 세션 또는 운영자 설정에서 확인한다. 없으면 이 step만 blocked로 기록하고 무엇이 필요한지 적는다. 이전 구현을 미완료로 되돌리거나 대본 성공으로 실연동 완료를 대체하지 않는다. 실제 토큰은 환경변수에만 둔다.

## 인터페이스와 책임

승인된 repo의 버그 이슈 접수→실제 수정 Agent→실제 검증→원본 댓글→새 검토 Task→실제 검토 Agent 결과를 확인한다. 검토 수정 요청이 없으면 해당 경로의 대역 검증과 실연동 미관찰을 구분한다. 종료 때 생성 자원 목록·정리 여부를 보고한다.

## 테스트 먼저

VERIFICATION_LOG에 source/task/execution 식별·커밋·검증·대기·사람 조작·비용(모르면 미확인)·원본 반영·후속 생성 근거와 날짜를 남긴다. 새 결함은 같은 검증 범위의 TDD로 수정 후 재검증. 실제 권한/비용 확대·main 배포·push/merge는 수행하지 않는다.

소스 변경 전에 대응하는 tests/ 미러 경로에 실패 테스트를 작성하고 실패 원인을 확인한다. 구현 후 해당 테스트와 전체 회귀를 통과시킨다. 문서 전용 변경은 불필요한 제품 테스트를 만들지 않는다.

## Acceptance Criteria

```bash
python3 -m pytest -q
python3 -m ruff check .
```

위 회귀 검사만으로 실연동 완료가 아니다. 작업 절의 실제 원본 반영·새 후속 생성·실제 검토 증거까지 필요하다.

## 검증 절차

1. 이 step의 책임 범위만 변경하고 도메인 외부 I/O 금지, server/connector 상호 import 금지, 비밀 환경변수 규칙을 확인한다.
2. 입력·결과·판정·완료·승인을 구분하고 기존 데모·n8n 계약 회귀를 확인한다.
3. 성공이면 phases/8-github-task-cycle/index.json의 step 16만 completed로 갱신하고 summary에 생성 파일·계약 결정·검증 결과·다음 step 주의점을 남긴다.
4. 3회 수정 후 실패하면 error/error_message, 외부 자격·사용자 선택 등 필수 정보가 없으면 blocked/blocked_reason을 기록한다. 미실행 검증을 completed로 표시하지 않는다.

## 금지사항

- main·공개 데모 VM을 변경하거나 배포하지 않는다. 이 phase는 service 기반 실서비스다.
- 사용자 변경을 삭제하거나 자동 stash/commit하지 않는다. 실행 전 작업 트리 정리는 README를 따른다.
- 임의 이슈의 명령·경로·URL로 실행 범위를 확장하지 않는다. 등록 대상과 고정 adapter 인자만 사용한다.
- 로그 분석·Jev·Jira·범용 그래프 편집기를 추가하지 않는다. 이 phase의 최소 GitHub 업무 순환 범위가 아니다.
- 실제 GitHub 쓰기·유료 호출은 step 16의 지정된 검증 범위 외에는 하지 않는다. 앞 step의 테스트는 대역을 사용한다.
