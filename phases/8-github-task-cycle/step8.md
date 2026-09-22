# Step 8: 보고서 데모와 분리된 일반 버그 실행

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
- src/workflow/server/worker.py
- src/workflow/server/web.py
- src/workflow/domain/task_readiness.py
- src/workflow/adapters/github_client.py
- src/workflow/connector/local_tool.py
- src/workflow/connector/prompt.py
- src/workflow/connector/codex.py
- src/workflow/connector/claude.py
- src/workflow/connector/runner.py

먼저 실제 파일과 이전 step 산출물을 읽는다. 미래 타입·함수명은 step 0에서 고정하며 이후 변경이 필요하면 같은 이름의 계약·테스트·문서를 함께 갱신한다. 대화 이력을 전제로 판단하지 않는다.

## 작업

일반 버그 실행 경로를 등록된 검증 프로필과 고정 커밋에 연결한다. 공통 local_tool 중심으로 재사용하며 도구별 launch 계약은 유지한다. 보고서 데모의 expected-report·응답 fixture 요구는 데모 경로에만 남긴다. 재현 테스트와 수정 후 검사, 실제 검증 로그·diff·result_commit을 보존한다. 이전 검토의 지적은 다음 시도 입력으로 전달한다.

## 인터페이스와 책임

일반 버그용 prepare/run/finalize의 인터페이스는 기존 LocalToolAdapter.run과 AdapterOutput을 확장해 유지한다. verification_profile_id는 사전 등록 명령만 해석하고 프로세스 환경에서 GitHub 토큰을 제거한다.

## 테스트 먼저

임시 실제 Git 저장소와 fake 도구로 baseline fail→result pass, 테스트 미추가/무변경/검증 실패·커밋 불일치·프로세스 중단·heartbeat·재시도 baseline·데모 회귀. 실제 모델 호출 금지.

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
3. 성공이면 phases/8-github-task-cycle/index.json의 step 8만 completed로 갱신하고 summary에 생성 파일·계약 결정·검증 결과·다음 step 주의점을 남긴다.
4. 3회 수정 후 실패하면 error/error_message, 외부 자격·사용자 선택 등 필수 정보가 없으면 blocked/blocked_reason을 기록한다. 미실행 검증을 completed로 표시하지 않는다.

## 금지사항

- main·공개 데모 VM을 변경하거나 배포하지 않는다. 이 phase는 service 기반 실서비스다.
- 사용자 변경을 삭제하거나 자동 stash/commit하지 않는다. 실행 전 작업 트리 정리는 README를 따른다.
- 임의 이슈의 명령·경로·URL로 실행 범위를 확장하지 않는다. 등록 대상과 고정 adapter 인자만 사용한다.
- 로그 분석·Jev·Jira·범용 그래프 편집기를 추가하지 않는다. 이 phase의 최소 GitHub 업무 순환 범위가 아니다.
- 실제 GitHub 쓰기·유료 호출은 step 16의 지정된 검증 범위 외에는 하지 않는다. 앞 step의 테스트는 대역을 사용한다.
