# Step 0: MVP 계약과 현재 코드 간극 고정

## 읽어야 할 파일

- AGENTS.md
- phases/8-github-task-cycle/README.md
- docs/product/ROADMAP.md (MVP/v0.1·지표)
- docs/PRD.md (MVP·5개 업무 수용 기준)
- docs/ARCHITECTURE.md (실서비스 전환·현재 계약)
- docs/CONTRACT.md
- docs/GLOSSARY.md
- docs/adr/0013-existing-tasks-first-staged-rollout.md
- src/workflow/contracts/v1.py
- src/workflow/connector/local_tool.py
- src/workflow/server/worker.py

먼저 실제 파일과 이전 step 산출물을 읽는다. 미래 타입·함수명은 step 0에서 고정하며 이후 변경이 필요하면 같은 이름의 계약·테스트·문서를 함께 갱신한다. 대화 이력을 전제로 판단하지 않는다.

## 작업

docs/adr/0014-github-task-cycle.md에 이 phase의 구현 결정을 기록하고 PRD·ARCHITECTURE·CONTRACT·GLOSSARY를 구체화한다. README의 제안 기본값으로 로컬 검증을 진행할 수 있게 하고 실제 계정 정보는 마지막 step에서만 요구한다. 기존 code_change의 진단 인계·report_output 요구를 우회하지 말고, 등록된 검증 프로필을 사용하는 일반 버그 수정과 결과 커밋을 읽는 검토의 확장 계약을 정한다. 기존 v1 클라이언트 호환이 깨지면 명시적인 새 계약 버전과 구버전 거부/지원 전략을 결정한다. 확장 예시는 구현 전 json contract-pending으로 표기한다.

## 인터페이스와 책임

인터페이스 계획: GitHubIssueSnapshot, GitHubSourceConfig, AssigneeBinding, TaskReadiness, CodeReviewResult, FollowupDecision, SourceDelivery. 기존 타입과 중복이면 재사용하고 확정한 이름·필드·소유 범위·오류·전이 표를 남긴다. 새 명령/경로는 외부 입력에서 받지 않는다.

## 테스트 먼저

기존 데모와 일반 버그 검증 비교표, 담당자 없는/복수인 경우, 검토 approved/changes_requested/needs_information, 수정 재시도 상한, 사람 응답, 취소/소스 변경, 중복 키, 결과 반영 불확실 상태를 모두 명세한다. docs 링크 검사와 기존 계약 fixture 테스트를 통과시킨다.

소스 변경 전에 대응하는 tests/ 미러 경로에 실패 테스트를 작성하고 실패 원인을 확인한다. 구현 후 해당 테스트와 전체 회귀를 통과시킨다. 문서 전용 변경은 불필요한 제품 테스트를 만들지 않는다.

## Acceptance Criteria

```bash
python3 -m pytest -q
python3 -m ruff check .
```

위 커맨드와 이 step의 테스트 항목을 모두 확인한다.

## 검증 절차

1. 이 step의 책임 범위만 변경하고 도메인 외부 I/O 금지, server/connector 상호 import 금지, 비밀 환경변수 규칙을 확인한다.
2. 입력·결과·판정·완료·승인을 구분하고 기존 데모·n8n 계약 회귀를 확인한다.
3. 성공이면 phases/8-github-task-cycle/index.json의 step 0만 completed로 갱신하고 summary에 생성 파일·계약 결정·검증 결과·다음 step 주의점을 남긴다.
4. 3회 수정 후 실패하면 error/error_message, 외부 자격·사용자 선택 등 필수 정보가 없으면 blocked/blocked_reason을 기록한다. 미실행 검증을 completed로 표시하지 않는다.

## 금지사항

- main·공개 데모 VM을 변경하거나 배포하지 않는다. 이 phase는 service 기반 실서비스다.
- 사용자 변경을 삭제하거나 자동 stash/commit하지 않는다. 실행 전 작업 트리 정리는 README를 따른다.
- 임의 이슈의 명령·경로·URL로 실행 범위를 확장하지 않는다. 등록 대상과 고정 adapter 인자만 사용한다.
- 로그 분석·Jev·Jira·범용 그래프 편집기를 추가하지 않는다. 이 phase의 최소 GitHub 업무 순환 범위가 아니다.
- 실제 GitHub 쓰기·유료 호출은 step 16의 지정된 검증 범위 외에는 하지 않는다. 앞 step의 테스트는 대역을 사용한다.
