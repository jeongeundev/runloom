# Step 6: 운영자 소스 설정과 담당 연결 API

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
- src/workflow/adapters/task_sources.py
- src/workflow/server/settings.py
- src/workflow/connector/masking.py
- src/workflow/server/auth.py
- src/workflow/server/app.py
- src/workflow/adapters/repo.py

먼저 실제 파일과 이전 step 산출물을 읽는다. 미래 타입·함수명은 step 0에서 고정하며 이후 변경이 필요하면 같은 이름의 계약·테스트·문서를 함께 갱신한다. 대화 이력을 전제로 판단하지 않는다.

## 작업

운영자 전용 GitHub 연결 설정 API를 추가한다. 초기 셀프호스트 1개 운영자 워크스페이스로 범위를 제한한다. 서버 환경에서 허용한 저장소/토큰 참조만 연결하며 임의 공개 세션이 전역 GitHub 자격 증명을 사용할 수 없게 한다. 설정은 필수 입력·담당 GitHub ID↔Agent·검토 Agent·검증 프로필·자동실행·시작시각/선택 이슈·재시도 상한을 포함한다.

## 인터페이스와 책임

server/github_api.py: 소스 미리보기·저장/중지·담당 연결 endpoints. 설정 읽기/쓰기 모두 운영자 인증+소유 범위. token 값은 폼에 받지 않고 환경설정된 연결 가능 여부만 응답한다.

## 테스트 먼저

미인증·다른 세션 거부, 저장소 허용목록, Agent 소유/능력 범위, 미등록 검증 프로필, 비밀 미노출, 설정 변경은 활성 Execution 입력을 바꾸지 않음. ENV_KEYS 테스트와 example env 동기화.

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
3. 성공이면 phases/8-github-task-cycle/index.json의 step 6만 completed로 갱신하고 summary에 생성 파일·계약 결정·검증 결과·다음 step 주의점을 남긴다.
4. 3회 수정 후 실패하면 error/error_message, 외부 자격·사용자 선택 등 필수 정보가 없으면 blocked/blocked_reason을 기록한다. 미실행 검증을 completed로 표시하지 않는다.

## 금지사항

- main·공개 데모 VM을 변경하거나 배포하지 않는다. 이 phase는 service 기반 실서비스다.
- 사용자 변경을 삭제하거나 자동 stash/commit하지 않는다. 실행 전 작업 트리 정리는 README를 따른다.
- 임의 이슈의 명령·경로·URL로 실행 범위를 확장하지 않는다. 등록 대상과 고정 adapter 인자만 사용한다.
- 로그 분석·Jev·Jira·범용 그래프 편집기를 추가하지 않는다. 이 phase의 최소 GitHub 업무 순환 범위가 아니다.
- 실제 GitHub 쓰기·유료 호출은 step 16의 지정된 검증 범위 외에는 하지 않는다. 앞 step의 테스트는 대역을 사용한다.
