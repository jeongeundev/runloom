# Step 9: work-list-minimal — 홈 목록 = 업무 한 줄, 상세에 단계 묶음

## 읽어야 할 파일

- AGENTS.md
- phases/14-task-model/README.md, phases/14-task-model/index.json (step 0~8 summary)
- docs/adr/0020-work-items-and-stages.md, docs/product/REDESIGN_PLAN.md (4절 화면 — 이 step 은 그 일부만), docs/UI_GUIDE.md
- src/workflow/server/web.py (`_base.my_tasks`, `/`·`/tasks`·`/tasks/{id}`, `/tasks/{id}/delegate`), src/workflow/server/views.py (`task_summary`, `build_task_view`, 선행/후속 칩, cycle 카드), templates home.html·_sidebar.html·task_detail.html·_live.html·_cycle.html, static/style.css
- src/workflow/adapters/repo.py (`list_work_items`, `stages_of`, `work_item_of_task`)
- tests: tests/workflow/server/test_web.py, test_views.py, test_ui.py, test_web_cycle.py

## 작업

최소 변경이다. 새 화면 구성(담당자별 묶음·필터·보드·오른쪽 상세 패널)은 16-work-ui 가 한다.

1. 홈·사이드바 목록: tasks 행 대신 업무 한 줄 — 키(원본 키 있으면 원본 키, 없으면 업무 키), 제목, 담당(멤버 표시 이름 / 에이전트 이름 / "담당 없음"), 업무 상태·이유, 갱신 경과. 줄을 누르면 업무 상세로. "담당 없음"(`새로 들어옴`) 업무의 [에이전트에게 맡기기] 는 지금 위치에 유지.
2. 업무 상세 `/work/{key}`(예: `/work/RUN-23`): 머리(키·원본 링크·상태·담당·PR), 단계 목록(종류 라벨·단계 상태·실행 횟수, 누르면 기존 `/tasks/{id}` 상세), 양식 칸(있는 것만), 이어서 생긴 업무·선행 업무(`work_item_links`), 열린 사람 요청(기존 응답 폼 재사용 — `stage_failed` 의 [다시 맡기기]·[닫기] 포함). `/tasks/{id}` 는 브레드크럼에 업무 키 링크를 더한다.
3. 기존 선행/후속 칩: 같은 업무 안 단계는 "단계 N/M", 업무 사이는 업무 키 링크로.
4. 상태 이름은 사용자 말 그대로, 내부 코드는 상세 "자세히" 에만(REDESIGN_PLAN 4절 원칙).

## 테스트 먼저

- web: GitHub 이슈 1건(fix+review) → 홈에 한 줄, 키·상태·담당, `/work/RUN-1` 에 단계 2개, 없는 키 → 404, 다른 워크스페이스 키 접근 불가.
- 실패 업무 → 상세에 [다시 맡기기]·[닫기], 누르면 step 6 동작.
- 기존 화면 테스트 회귀(바뀐 문구는 기대값 갱신 — 단정을 약하게 하지 않는다).

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
3. 성공이면 `phases/14-task-model/index.json` 의 step 9 만 `completed` 로 바꾸고 `summary` 에 생성·수정·삭제 파일, 결정, 검증 결과(테스트 수), 다음 step 이 주의할 점을 한 줄로 남긴다.
4. 3회 수정 후에도 실패하면 `error` + `error_message`, 사용자 결정·외부 자원이 필요하면 `blocked` + `blocked_reason` 을 기록한다. 실행하지 않은 검증을 `completed` 로 표시하지 않는다.

## 금지사항

- 실제 GitHub(api.github.com·github.com)·Discord·Jira 를 호출하지 않는다(공식 문서 읽기는 허용). 이유: 모든 step 은 httpx `MockTransport`·가짜 GitHub·임시 bare 저장소로 검증한다. 실연동은 phase 뒤 사용자와 함께 한다.
- 사용자가 띄워 둔 셀프호스트(compose 프로젝트 `runloom`, 포트 8000, 볼륨 `runloom_workflow-data`)·`deploy/selfhost/.env`·러너 홈(`~/Library/Application Support/workflow-connector/`)·launchd·`/Users/kje/demo/*`(OpenArchive·runloom-sandbox 클론)를 읽거나 바꾸지 않는다. 컨테이너를 띄우거나 멈추지 않는다. 이유: 실사용 중인 환경이다.
- 종류 이름(`bug_fix`·`code_review`)으로 새 분기를 만들지 않는다. 이유: 업무 종류·후속 규칙은 워크스페이스 등록 데이터다(ADR-0009, AGENTS.md).
- `tasks` 테이블을 재생성하거나 이름을 바꾸지 않는다. 이유: ADR-0020 결정 — 업무 상태는 `work_items` 에 두고 단계 상태는 그대로 둔다.
- 새 화면 구성(담당자별 묶음·보드·상세 패널·연결 화면)을 만들지 않는다. 이유: 16-work-ui 범위. 이 phase 의 화면 변경은 step 9 최소 변경뿐이다.
- 사용자 변경을 삭제하거나 stash·reset 하지 않는다. 커밋은 하네스가 한다.
- 이 step 범위 밖 모듈을 "개선"하지 않는다. 기존 테스트의 단정을 약하게 만들어 통과시키지 않는다.
