# Step 13: 연결·업무 목록·결과·대기 화면

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
- src/workflow/adapters/github_client.py
- src/workflow/server/worker.py
- src/workflow/adapters/repo.py
- src/workflow/server/web.py
- src/workflow/server/views.py
- src/workflow/server/templates
- src/workflow/server/filters.py

먼저 실제 파일과 이전 step 산출물을 읽는다. 미래 타입·함수명은 step 0에서 고정하며 이후 변경이 필요하면 같은 이름의 계약·테스트·문서를 함께 갱신한다. 대화 이력을 전제로 판단하지 않는다.

## 작업

기존 화면에 운영자 소스 설정/담당 연결/미리보기와 실제 업무 목록을 연결한다. 원본 링크·담당 Agent·준비/대기 사유·입력 보충·검토 결과·생성 근거·재시도 횟수·GitHub 반영 상태를 표시한다. 실제 데이터와 fixture를 구분한다. 고급 규칙 JSON·자연어 workflow builder를 필수 입력으로 만들지 않는다.

## 인터페이스와 책임

기존 render/view 패턴 유지, 필수 소스 API와 사람 응답 API를 사용한다. GitHub 작업 실패/Agent 실패/사람 대기를 서로 다른 상태로 표시하고 표시용 라벨에서 실행 결정을 역산하지 않는다.

## 테스트 먼저

서버 렌더 테스트로 auth/CSRF 정책·escaping·실제/데모 표시·대기 이유 일치·응답 후 재개·수동모드·중복 제출·비밀 노출 없음·결과 반영 재시도 표시. 도메인 판단을 template에서 구현하지 않음.

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
3. 성공이면 phases/8-github-task-cycle/index.json의 step 13만 completed로 갱신하고 summary에 생성 파일·계약 결정·검증 결과·다음 step 주의점을 남긴다.
4. 3회 수정 후 실패하면 error/error_message, 외부 자격·사용자 선택 등 필수 정보가 없으면 blocked/blocked_reason을 기록한다. 미실행 검증을 completed로 표시하지 않는다.

## 금지사항

- main·공개 데모 VM을 변경하거나 배포하지 않는다. 이 phase는 service 기반 실서비스다.
- 사용자 변경을 삭제하거나 자동 stash/commit하지 않는다. 실행 전 작업 트리 정리는 README를 따른다.
- 임의 이슈의 명령·경로·URL로 실행 범위를 확장하지 않는다. 등록 대상과 고정 adapter 인자만 사용한다.
- 로그 분석·Jev·Jira·범용 그래프 편집기를 추가하지 않는다. 이 phase의 최소 GitHub 업무 순환 범위가 아니다.
- 실제 GitHub 쓰기·유료 호출은 step 16의 지정된 검증 범위 외에는 하지 않는다. 앞 step의 테스트는 대역을 사용한다.
