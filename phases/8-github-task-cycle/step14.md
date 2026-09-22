# Step 14: 전체 업무 순환과 장애 회귀

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
- src/workflow/server/web.py
- src/workflow/server/views.py
- src/workflow/server/templates
- src/workflow/server/filters.py
- tests/e2e/test_scenario.py
- scripts/local_stack.py
- tests/workflow/server
- tests/workflow/connector

먼저 실제 파일과 이전 step 산출물을 읽는다. 미래 타입·함수명은 step 0에서 고정하며 이후 변경이 필요하면 같은 이름의 계약·테스트·문서를 함께 갱신한다. 대화 이력을 전제로 판단하지 않는다.

## 작업

tests/e2e/test_github_cycle.py에 격리된 fake GitHub HTTP 서버와 임시 Git 저장소·fake 도구를 사용하는 새 시나리오를 추가한다. 기존 대본 스택과 분리하고 일반 버그 검증을 실제 subprocess 테스트로 확인한다. 필요시 scripts/local_stack.py의 명시적 테스트 설정만 추가하고 script 테스트를 먼저 작성한다.

## 인터페이스와 책임

5개 업무: A/B 준비됨, C는 A 산출물로 입력 해소, D는 위임 밖, E는 의견 결정 필요. B 결과는 새 F 검토를 생성한다. 수정 요청→한 번 수정→새 커밋 검토도 별도 검증한다.

## 테스트 먼저

등록/가져오기→실행→원본 댓글→기존 C/새 F→D/E 응답, 중복/재시작/단절/오래된 이벤트/상한/같은 repo lock 회귀. 기존 e2e와 함께 통과. 네트워크는 로컬 fake만 사용.

소스 변경 전에 대응하는 tests/ 미러 경로에 실패 테스트를 작성하고 실패 원인을 확인한다. 구현 후 해당 테스트와 전체 회귀를 통과시킨다. 문서 전용 변경은 불필요한 제품 테스트를 만들지 않는다.

## Acceptance Criteria

```bash
python3 -m pytest -q
python3 -m ruff check .
WORKFLOW_E2E=1 python3 -m pytest tests/e2e -q
python3 -m pytest scripts/ -q
```

새 GitHub e2e가 실제 실행됐는지 확인한다. skip을 통과로 세지 않는다.

## 검증 절차

1. 이 step의 책임 범위만 변경하고 도메인 외부 I/O 금지, server/connector 상호 import 금지, 비밀 환경변수 규칙을 확인한다.
2. 입력·결과·판정·완료·승인을 구분하고 기존 데모·n8n 계약 회귀를 확인한다.
3. 성공이면 phases/8-github-task-cycle/index.json의 step 14만 completed로 갱신하고 summary에 생성 파일·계약 결정·검증 결과·다음 step 주의점을 남긴다.
4. 3회 수정 후 실패하면 error/error_message, 외부 자격·사용자 선택 등 필수 정보가 없으면 blocked/blocked_reason을 기록한다. 미실행 검증을 completed로 표시하지 않는다.

## 금지사항

- main·공개 데모 VM을 변경하거나 배포하지 않는다. 이 phase는 service 기반 실서비스다.
- 사용자 변경을 삭제하거나 자동 stash/commit하지 않는다. 실행 전 작업 트리 정리는 README를 따른다.
- 임의 이슈의 명령·경로·URL로 실행 범위를 확장하지 않는다. 등록 대상과 고정 adapter 인자만 사용한다.
- 로그 분석·Jev·Jira·범용 그래프 편집기를 추가하지 않는다. 이 phase의 최소 GitHub 업무 순환 범위가 아니다.
- 실제 GitHub 쓰기·유료 호출은 step 16의 지정된 검증 범위 외에는 하지 않는다. 앞 step의 테스트는 대역을 사용한다.
