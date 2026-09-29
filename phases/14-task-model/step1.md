# Step 1: work-status-domain — 업무 상태 순수 함수

## 읽어야 할 파일

- AGENTS.md
- phases/14-task-model/README.md, phases/14-task-model/index.json (step 0 summary)
- docs/adr/0020-work-items-and-stages.md, docs/ARCHITECTURE.md ("업무와 단계 — phase 14" 의 업무 상태 표·이름 표)
- src/workflow/domain/status.py (`USER_STATUS_LABELS`, `user_status` — 같은 스타일), tests/workflow/domain/test_status.py
- src/workflow/domain/task_followup.py (`FollowupContext` 식의 사실 봉투 스타일)

## 작업

1. `src/workflow/domain/work_status.py` (이름은 step 0 ARCHITECTURE 이름 표를 따른다):
   - 상수 `WORK_STATUSES`(8개, ARCHITECTURE 순서), `WORK_TERMINAL = ("완료", "종료")`.
   - 사실 봉투(frozen dataclass): 업무 마감 여부·마감 종류, 담당 유무, 지시 여부(원본이 `all_open` 이고 지시 전이면 False), 단계 요약 목록(각 단계의 종류·단계 상태·마감·실패 여부·실행 중 여부·실패 이유), 열린 사람 요청 수와 첫 요청 문구, PR 상태(`pending|open|merged|closed|failed|None`), 원본 상태(`open|closed|None`), 직접 작업 신호(이 phase 에서는 늘 None).
   - `work_status(facts) -> WorkStatus(status, reason)`: ARCHITECTURE 우선순위를 그대로 구현. 실패한 단계가 있고 그 뒤 새 단계가 없으면 `내 차례` + 이유 `"실패 — <실패 이유>"`. PR merged → `완료`, PR 이 병합 없이 closed → `내 차례`("PR 이 병합 없이 닫힘") 인지 `종료` 인지는 ARCHITECTURE 표를 따른다.
2. 이 모듈은 DB·HTTP·FastAPI 를 import 하지 않는다. `domain/status.py` 의 단계 상태 라벨을 읽기 전용으로 참조해도 된다.

## 테스트 먼저

`tests/workflow/domain/test_work_status.py` — 상태 8개 각각이 나오는 사실 조합, 우선순위 충돌(열린 사람 요청 + 실행 중 → 내 차례, 열린 PR + 사람 요청 → 내 차례, 마감 → 다른 신호 무시), 실패 뒤 다시 맡겨 새 단계 실행 중 → 에이전트 작업 중, 이유 문구. 표 기반(parametrize)으로.

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
3. 성공이면 `phases/14-task-model/index.json` 의 step 1 만 `completed` 로 바꾸고 `summary` 에 생성·수정·삭제 파일, 결정, 검증 결과(테스트 수), 다음 step 이 주의할 점을 한 줄로 남긴다.
4. 3회 수정 후에도 실패하면 `error` + `error_message`, 사용자 결정·외부 자원이 필요하면 `blocked` + `blocked_reason` 을 기록한다. 실행하지 않은 검증을 `completed` 로 표시하지 않는다.

## 금지사항

- 실제 GitHub(api.github.com·github.com)·Discord·Jira 를 호출하지 않는다(공식 문서 읽기는 허용). 이유: 모든 step 은 httpx `MockTransport`·가짜 GitHub·임시 bare 저장소로 검증한다. 실연동은 phase 뒤 사용자와 함께 한다.
- 사용자가 띄워 둔 셀프호스트(compose 프로젝트 `runloom`, 포트 8000, 볼륨 `runloom_workflow-data`)·`deploy/selfhost/.env`·러너 홈(`~/Library/Application Support/workflow-connector/`)·launchd·`/Users/kje/demo/*`(OpenArchive·runloom-sandbox 클론)를 읽거나 바꾸지 않는다. 컨테이너를 띄우거나 멈추지 않는다. 이유: 실사용 중인 환경이다.
- 종류 이름(`bug_fix`·`code_review`)으로 새 분기를 만들지 않는다. 이유: 업무 종류·후속 규칙은 워크스페이스 등록 데이터다(ADR-0009, AGENTS.md).
- `tasks` 테이블을 재생성하거나 이름을 바꾸지 않는다. 이유: ADR-0020 결정 — 업무 상태는 `work_items` 에 두고 단계 상태는 그대로 둔다.
- 새 화면 구성(담당자별 묶음·보드·상세 패널·연결 화면)을 만들지 않는다. 이유: 16-work-ui 범위. 이 phase 의 화면 변경은 step 9 최소 변경뿐이다.
- 사용자 변경을 삭제하거나 stash·reset 하지 않는다. 커밋은 하네스가 한다.
- 이 step 범위 밖 모듈을 "개선"하지 않는다. 기존 테스트의 단정을 약하게 만들어 통과시키지 않는다.
