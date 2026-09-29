# Step 0: task-model-design — 업무·단계 모델 결정과 이름 고정

## 읽어야 할 파일

- AGENTS.md
- phases/14-task-model/README.md (조사 결과·계획 기본값 11가지 — 이 phase 의 기준), phases/14-task-model/index.json
- docs/product/REDESIGN_PLAN.md (3·5·10·16절)
- docs/research/2026-09-29-benchmark-intake-mapping.md ("Runloom 에 가져올 점")
- docs/adr/0009-registered-kinds-and-succession-rules.md, 0011-task-driven-work-cycle.md, 0014-github-task-cycle.md, 0015-measurement-events-and-baseline.md, 0018-real-repo-cycle.md, 0019-service-selfhost-only.md
- docs/ARCHITECTURE.md ("GitHub 업무 순환 — phase 8 계약" 의 후속 결정 표·중복 키, "측정 — phase 9", "실제 저장소 순환 — phase 12", "셀프호스트 전용 — phase 13")
- docs/CONTRACT.md (ExecutionRequest·SuccessorRule 예시), docs/GLOSSARY.md
- src/workflow/contracts/v1.py (`SuccessorRule`, `ExecutionRequest`), src/workflow/adapters/db.py, src/workflow/domain/status.py, src/workflow/domain/task_followup.py

먼저 실제 파일을 읽는다. 대화 이력을 전제로 판단하지 않는다.

## 작업

문서만 바꾼다(제품 코드·테스트 없음). README "계획 기본값" 11가지를 결정 기록으로 옮기고 이후 step 이 쓸 이름을 고정한다.

1. `docs/adr/0020-work-items-and-stages.md` 신설: 결정 1~11, 하지 않는 것, 대안·기각 이유(첫 Task 를 업무 머리로 쓰기 — 칸이 Task 에 섞이고 후속이 "머리" 를 바꿀 수 없음 / `tasks` 표 이름 바꾸기 — 전 코드 변경 / 업무 상태를 저장하지 않고 화면마다 계산 — 목록 필터·정렬·이벤트에 비쌈 / 실패를 별도 끝 상태로 — 사용자 결정).
2. `docs/ARCHITECTURE.md` 에 "업무와 단계 — phase 14" 절. 반드시 **이름을 확정**해 적는다:
   - 스키마 v10 표: `work_items`(칸 이름·타입·CHECK — README 3·4, `UNIQUE(session_id, key_number)`, 키 문자열은 `RUN-` + 번호로 저장 또는 계산 중 하나로 정해 적는다), `work_item_links`(`from_work_item_id`, `to_work_item_id`, `type IN ('blocks','spawned_from')`, `cause_execution_id` NULL 허용, 같은 쌍·유형 UNIQUE, 자기 자신 금지), `members`(`member_id` `mem-`+8 hex, `session_id`, `display_name`, `role IN ('admin','member')`, `created_at`), `field_mappings`(`mapping_id`, `session_id`, `source_type`, `field IN ('kind','priority')`, `source_value`(`*` = 나머지), `runloom_value`, `position`, `created_at`), `tasks.work_item_id`(ALTER 로 더함 — NULL 허용이지만 repo 가 늘 채운다는 불변식), 업무 이벤트를 `task_events` 에 섞을지 새 표 `work_item_events` 로 둘지(추가 전용, 상태 전환 기록).
   - 업무 상태 표: 값 8개(README 4)와 **판정 우선순위**(예: 마감 → 완료/종료, 열린 사람 요청·실패·검토 대기 → 내 차례, 열린 PR → PR · 검토, 실행 중 단계 → 에이전트 작업 중, 지시 전·담당 없음 → 새로 들어옴, 그 밖 → 대기), 각 값의 이유 문구 예.
   - 후속 규칙 `placement` 동작 표(same_work: 새 단계 Task 를 같은 업무에 / new_work: 새 업무 + `spawned_from`), 기존 `followup_links` 유일키는 그대로.
   - 실패 처리: 사람 요청 코드 `stage_failed`, 응답 action `retry`(같은 업무에 같은 종류 새 단계 Task, 실패한 단계의 입력·대상을 복사) · `close`(업무 종료). 중복 방지 키.
   - 매핑 표 적용 순서, 기본 seed 행, 설정 번호 증가 조건.
   - 양식 칸 추출 규칙(절 제목 동의어 표), `form_json` 모양(칸마다 `{value, source}`).
   - 실행 요청 선택 칸 `work_key`(패턴)·`branch_seq`(기본 1), 브랜치 규칙(`runloom/<work_key>`, 순번 2 이상은 `-<순번>`, 키 없으면 `task/<task_id>`), 재작업·검토가 같은 브랜치를 쓰는 규칙이 지금과 같은지, PR 제목 규칙.
   - v9 → v10 마이그레이션 규칙(README 11 — 같은 업무로 묶는 기준: `followup_links` 로 생긴 Task 는 원인 Execution 의 Task 업무로, 그 밖 Task 는 각자 새 업무; 체인 선행은 `blocks` 링크로; 키는 업무의 가장 이른 Task `created_at` 순).
   - 이름·시그니처 표(이후 step 이 만들 모듈·함수: `domain/work_status.py` `work_status(facts) -> WorkStatus`, `domain/form_sections.py` `extract_form(body) -> WorkForm`, `domain/field_mapping.py` `map_value(rows, source_type, field, values) -> str | None`, repo `create_work_item`·`get_work_item`·`list_work_items`·`set_work_status`·`assign_work_item`, `WorkItemFacts` 등 — 최종 이름은 여기서 정한다).
3. `docs/CONTRACT.md`: `SuccessorRule` 예시에 `placement`, `ExecutionRequest` 예시에 `work_key`·`branch_seq`(모두 선택 칸 — `contract_version` 1 그대로). 예시 블록 수를 세는 테스트가 있으면 함께 갱신.
4. `docs/GLOSSARY.md`: `WorkItem`(업무 — 목록 한 줄, 금지 표현 `Ticket`·`Issue`·`Job`), `Task` 정의를 "업무의 한 단계" 로 수정(가져온 업무 칸 설명을 WorkItem 으로 옮김), `업무 키`, `업무 상태`, `placement`, `멤버`(`Member` — `User`·`Account` 금지), `매핑 표`, `양식 칸`.
5. `docs/CURRENT_HANDOFF.md` "다음 작업" 에 "14-task-model 진행 중" 한 줄.

## 테스트 먼저

문서 전용 step 이라 제품 테스트를 새로 만들지 않는다. 기존 문서 검사(링크·CONTRACT 예시 파싱 등)가 통과해야 한다.

## Acceptance Criteria

```bash
python3 -m pytest -q
python3 -m ruff check .
```

위 커맨드와 이 step 의 테스트 항목을 모두 확인한다.

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 규칙을 확인한다: `domain/` 은 FastAPI·sqlite3·HTTPX·subprocess·Git 을 import 하지 않는다 / `server/` 와 `connector/` 는 서로 import 하지 않고 `contracts/` 만 공유한다 / 외부 입력(요청 본문·이슈 본문·모델 응답)에서 명령·경로·브랜치 이름을 받아 실행하지 않는다 / 비밀값(연결 토큰, `OPERATOR_TOKEN`, `SESSION_SECRET`, GitHub App 비밀·설치 토큰, PAT, 알림 웹훅 URL, 러너 로컬 등록의 환경변수 값)은 DB·로그·응답·템플릿·백업·중앙에 넣지 않는다 / GLOSSARY 이름을 그대로 쓴다 / phase 8·11·12 의 GitHub 순환(수집 → 수정 → 검토 → 초안 PR → 병합 추적)이 그대로 동작한다.
3. 성공이면 `phases/14-task-model/index.json` 의 step 0 만 `completed` 로 바꾸고 `summary` 에 생성·수정·삭제 파일, 결정, 검증 결과(테스트 수), 다음 step 이 주의할 점을 한 줄로 남긴다.
4. 3회 수정 후에도 실패하면 `error` + `error_message`, 사용자 결정·외부 자원이 필요하면 `blocked` + `blocked_reason` 을 기록한다. 실행하지 않은 검증을 `completed` 로 표시하지 않는다.

## 금지사항

- 실제 GitHub(api.github.com·github.com)·Discord·Jira 를 호출하지 않는다(공식 문서 읽기는 허용). 이유: 모든 step 은 httpx `MockTransport`·가짜 GitHub·임시 bare 저장소로 검증한다. 실연동은 phase 뒤 사용자와 함께 한다.
- 사용자가 띄워 둔 셀프호스트(compose 프로젝트 `runloom`, 포트 8000, 볼륨 `runloom_workflow-data`)·`deploy/selfhost/.env`·러너 홈(`~/Library/Application Support/workflow-connector/`)·launchd·`/Users/kje/demo/*`(OpenArchive·runloom-sandbox 클론)를 읽거나 바꾸지 않는다. 컨테이너를 띄우거나 멈추지 않는다. 이유: 실사용 중인 환경이다.
- 종류 이름(`bug_fix`·`code_review`)으로 새 분기를 만들지 않는다. 이유: 업무 종류·후속 규칙은 워크스페이스 등록 데이터다(ADR-0009, AGENTS.md).
- `tasks` 테이블을 재생성하거나 이름을 바꾸지 않는다. 이유: ADR-0020 결정 — 업무 상태는 `work_items` 에 두고 단계 상태는 그대로 둔다.
- 새 화면 구성(담당자별 묶음·보드·상세 패널·연결 화면)을 만들지 않는다. 이유: 16-work-ui 범위. 이 phase 의 화면 변경은 step 9 최소 변경뿐이다.
- 사용자 변경을 삭제하거나 stash·reset 하지 않는다. 커밋은 하네스가 한다.
- 이 step 범위 밖 모듈을 "개선"하지 않는다. 기존 테스트의 단정을 약하게 만들어 통과시키지 않는다.
