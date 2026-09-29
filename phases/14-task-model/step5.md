# Step 5: intake-work-items — GitHub·n8n·직접 등록이 업무 + 첫 단계, 양식 칸, 매핑 표

## 읽어야 할 파일

- AGENTS.md
- phases/14-task-model/README.md, phases/14-task-model/index.json (step 0~4 summary)
- docs/adr/0020-work-items-and-stages.md, docs/ARCHITECTURE.md ("업무와 단계 — phase 14" 의 매핑 표·양식 칸 규칙)
- docs/research/2026-09-29-benchmark-intake-mapping.md ("제안: 최소 정규화 Task 스키마", GitHub issue forms 결과 모양)
- src/workflow/domain/issue_intake.py (`snapshot_to_task_spec`, `intake_facts`, `ISSUE_KIND`), src/workflow/contracts/github.py (`GitHubIssueSnapshot`)
- src/workflow/server/github_sync.py, src/workflow/adapters/repo.py (`upsert_source_issue`, `mark_issue_delegated`)
- src/workflow/server/inbound_api.py, src/workflow/server/web.py (`create_chain`, `/tasks/new` 등록), src/workflow/domain/task_sources.py·composition.py (n8n 이 쓰는 부분)
- tests: tests/workflow/domain/test_issue_intake.py, tests/workflow/server/test_github_sync.py, test_inbound_api.py, test_web.py

## 작업

1. `domain/form_sections.py`(이름은 ARCHITECTURE 표): `extract_form(body) -> WorkForm` — `### <제목>` 절을 읽어 목표·재현 절차·기대 동작·인수 조건에 넣는다(동의어 표, 대소문자·공백 무시, 절 본문 strip, 값 `_No response_`(GitHub issue forms 빈 값)는 빈 칸). 칸마다 출처(`"github:section:<원문 제목>"`). 해석 못 하면 모두 빈 칸 — 예외 없음.
2. `domain/field_mapping.py`: `map_value(rows, *, source_type, field, values) -> str | None` — 순서대로 첫 일치, `*` 는 나머지. 순수 함수. `issue_intake` 의 `ISSUE_KIND` 고정을 매핑 결과로 바꾼다(라벨 목록을 `values` 로). 매핑 결과 종류가 워크스페이스에 없거나 None 이면 업무는 만들되 단계 Task 를 만들지 않고 업무 상태 `새로 들어옴`, 이유 "종류 매핑 없음 — 확인 필요". 우선순위도 라벨 매핑(없으면 `normal`).
3. GitHub 수집: `upsert_source_issue` 가 새 이슈면 업무(원본 칸·양식·종류·우선순위·담당 없음) + 첫 단계 Task 를 한 트랜잭션에서, 갱신이면 업무의 제목·요청·양식·원본 상태를 고치고 revision +1(마감 전만 — 지금 Task 규칙과 같게). 지시(`mark_issue_delegated`)는 업무 상태 갱신을 부른다.
4. n8n 입구·직접 등록 폼: 항목마다 업무 + 첫 단계. 체인 순서(`blocked_by`)는 `work_item_links(type='blocks')` 로도 남긴다(단계 `predecessor_task_id` 는 step 0 규칙대로). 직접 등록 원본은 `manual`, n8n 은 `n8n` + 항목 키.
5. 매핑 표 관리: repo `list_field_mappings`·`replace_field_mappings`(설정 번호 +1). 화면은 16-work-ui — 이 step 은 운영자 API 한 쌍(GET·PUT, 운영자 인증)만. 요청 본문 검증은 Pydantic.

## 테스트 먼저

- domain: 양식 절 추출(한국어·영어 제목, 빈 값, 절 없음, 중복 절), 매핑(첫 일치·`*`·일치 없음).
- github_sync: 새 이슈 → 업무 1 + 단계 1(종류 = 매핑), 라벨 매핑으로 다른 종류, 매핑 없음 → 업무만·`새로 들어옴`, 본문 수정 → 업무 revision +1·양식 갱신, 기존 phase 8·11·12 테스트 회귀.
- inbound·web: n8n 항목 N개 → 업무 N·`blocks` 링크, 직접 등록 → 업무 1.
- API: 매핑 PUT 뒤 설정 번호 +1, 운영자 아님 → 401/403.

소스·배포 파일 변경 전에 `tests/` 미러 경로에 실패 테스트를 작성하고 실패 원인이 의도한 것인지 확인한다(`tdd-guard.sh` 가 테스트 없는 소스 작성을 막는다). 구현 후 해당 테스트와 전체 회귀를 통과시킨다.

## Acceptance Criteria

```bash
python3 -m pytest -q
python3 -m ruff check .
```

위 커맨드와 이 step 의 테스트 항목을 모두 확인한다.

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 규칙을 확인한다: `domain/` 은 FastAPI·sqlite3·HTTPX·subprocess·Git 을 import 하지 않는다 / `server/` 와 `connector/` 는 서로 import 하지 않고 `contracts/` 만 공유한다 / 외부 입력(요청 본문·이슈 본문·모델 응답)에서 명령·경로·브랜치 이름을 받아 실행하지 않는다 / 비밀값(연결 토큰, `OPERATOR_TOKEN`, `SESSION_SECRET`, GitHub App 비밀·설치 토큰, PAT, 알림 웹훅 URL, 러너 로컬 등록의 환경변수 값)은 DB·로그·응답·템플릿·백업·중앙에 넣지 않는다 / GLOSSARY 이름을 그대로 쓴다 / phase 8·11·12 의 GitHub 순환(수집 → 수정 → 검토 → 초안 PR → 병합 추적)이 그대로 동작한다.
3. 성공이면 `phases/14-task-model/index.json` 의 step 5 만 `completed` 로 바꾸고 `summary` 에 생성·수정·삭제 파일, 결정, 검증 결과(테스트 수), 다음 step 이 주의할 점을 한 줄로 남긴다.
4. 3회 수정 후에도 실패하면 `error` + `error_message`, 사용자 결정·외부 자원이 필요하면 `blocked` + `blocked_reason` 을 기록한다. 실행하지 않은 검증을 `completed` 로 표시하지 않는다.

## 금지사항

- 실제 GitHub(api.github.com·github.com)·Discord·Jira 를 호출하지 않는다(공식 문서 읽기는 허용). 이유: 모든 step 은 httpx `MockTransport`·가짜 GitHub·임시 bare 저장소로 검증한다. 실연동은 phase 뒤 사용자와 함께 한다.
- 사용자가 띄워 둔 셀프호스트(compose 프로젝트 `runloom`, 포트 8000, 볼륨 `runloom_workflow-data`)·`deploy/selfhost/.env`·러너 홈(`~/Library/Application Support/workflow-connector/`)·launchd·`/Users/kje/demo/*`(OpenArchive·runloom-sandbox 클론)를 읽거나 바꾸지 않는다. 컨테이너를 띄우거나 멈추지 않는다. 이유: 실사용 중인 환경이다.
- 종류 이름(`bug_fix`·`code_review`)으로 새 분기를 만들지 않는다. 이유: 업무 종류·후속 규칙은 워크스페이스 등록 데이터다(ADR-0009, AGENTS.md).
- `tasks` 테이블을 재생성하거나 이름을 바꾸지 않는다. 이유: ADR-0020 결정 — 업무 상태는 `work_items` 에 두고 단계 상태는 그대로 둔다.
- 새 화면 구성(담당자별 묶음·보드·상세 패널·연결 화면)을 만들지 않는다. 이유: 16-work-ui 범위. 이 phase 의 화면 변경은 step 9 최소 변경뿐이다.
- 사용자 변경을 삭제하거나 stash·reset 하지 않는다. 커밋은 하네스가 한다.
- 이 step 범위 밖 모듈을 "개선"하지 않는다. 기존 테스트의 단정을 약하게 만들어 통과시키지 않는다.
