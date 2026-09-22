# Step 9: 결과 커밋의 읽기 전용 검토

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
- src/workflow/connector/local_tool.py
- src/workflow/connector/prompt.py
- src/workflow/connector/codex.py
- src/workflow/connector/claude.py
- src/workflow/connector/runner.py
- src/workflow/connector/git_ops.py
- src/workflow/connector/adapter.py

먼저 실제 파일과 이전 step 산출물을 읽는다. 미래 타입·함수명은 step 0에서 고정하며 이후 변경이 필요하면 같은 이름의 계약·테스트·문서를 함께 갱신한다. 대화 이력을 전제로 판단하지 않는다.

## 작업

등록 저장소 안에서 보존된 결과 커밋의 별도 읽기 전용 검토 환경을 만든다. 현재 generic 인계 디렉터리만 읽는 방식으로 검토 완료를 대체하지 않는다. 수정 Agent와 검토 Agent는 MVP에서 같은 로컬 저장소의 커밋에 접근 가능한 등록이어야 한다. 다른 기기 커밋 전송은 지원하지 않고 착수 전 이유를 표시한다.

## 인터페이스와 책임

review target에는 local_registration_id·result_commit·source_execution_id를 사용한다. launch_readonly와 결과 파서를 재사용하고 구조화한 CodeReviewResult를 업로드한다. 결과에 검토 대상 커밋 일치 필수.

## 테스트 먼저

실제 커밋 내용을 읽는 fake reviewer, 원본/검토 tree 변경 금지·커밋 없음·다른 등록 저장소·잘못된 source 참조·해시 불일치·cleanup 후 결과 보존. approved/changes_requested/needs_information 파싱.

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
3. 성공이면 phases/8-github-task-cycle/index.json의 step 9만 completed로 갱신하고 summary에 생성 파일·계약 결정·검증 결과·다음 step 주의점을 남긴다.
4. 3회 수정 후 실패하면 error/error_message, 외부 자격·사용자 선택 등 필수 정보가 없으면 blocked/blocked_reason을 기록한다. 미실행 검증을 completed로 표시하지 않는다.

## 금지사항

- main·공개 데모 VM을 변경하거나 배포하지 않는다. 이 phase는 service 기반 실서비스다.
- 사용자 변경을 삭제하거나 자동 stash/commit하지 않는다. 실행 전 작업 트리 정리는 README를 따른다.
- 임의 이슈의 명령·경로·URL로 실행 범위를 확장하지 않는다. 등록 대상과 고정 adapter 인자만 사용한다.
- 로그 분석·Jev·Jira·범용 그래프 편집기를 추가하지 않는다. 이 phase의 최소 GitHub 업무 순환 범위가 아니다.
- 실제 GitHub 쓰기·유료 호출은 step 16의 지정된 검증 범위 외에는 하지 않는다. 앞 step의 테스트는 대역을 사용한다.
