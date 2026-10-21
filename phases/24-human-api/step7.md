# Step 7: work-action-api — 업무 행동 API

## 읽어야 할 파일

- AGENTS.md, phases/24-human-api/README.md("결정" D1·D4·D24·D24b·D25·D29·D30), phases/24-human-api/index.json(이전 step summary)
- docs/ARCHITECTURE.md "사람용 API — phase 24" 의 업무 행동 표(메서드·경로·본문·성공 상태·권한·오류 — **README 와 다르면 ARCHITECTURE 가 기준**), docs/CONTRACT.md 18절 담당 PUT 예시
- `src/workflow/server/web.py` 의 `work_assignee`·`work_priority`·`work_direct`·`work_direct_stop`·`work_triage`·`work_triage_accept`·`work_triage_dismiss`·`work_next_step_accept`·`work_next_step_dismiss`(권한 의존성·호출 인자 그대로 따라 한다)
- `src/workflow/server/work_actions.py`(`parse_assignee`·`assign_work`·`set_priority`·`start_direct`·`stop_direct`·`accept_triage`·`dismiss_triage`·`accept_next_step`·`dismiss_next_step`), `src/workflow/server/triage_runs.py`(`request_triage`), `src/workflow/server/stage_runs.py`(`WorkActionError`)
- step 6 의 `server/work_api.py` 와 상세 변환 함수
- 테스트: `tests/workflow/server/test_web_work.py`·`test_work_actions.py`·`test_web_triage.py`·`test_web_next_step.py`(fixture·가짜 러너)

## 작업

1. `server/work_api.py` 에 ARCHITECTURE 표의 행동 라우트를 더한다. 각 라우트는 **웹 라우트와 같은 `work_actions`·`triage_runs` 함수를 같은 인자로** 부른다(`store`·`settings`·`secrets` 는 `request.app.state` 에서, `member_id` = 요청 멤버).
   - `PATCH /api/v1/work-items/{key}` `{"priority"}` — 다른 칸이 오면 422.
   - `PUT …/assignee` `{"type","id","note"?}` → `parse_assignee` 값으로 바꿔 `assign_work`. `DELETE …/assignee` → `none`.
   - `PUT`/`DELETE …/direct-work` → `start_direct`/`stop_direct`.
   - `POST …/triage-runs` → `request_triage(trigger="manual")`, 시작 못 하면 409 `triage_unavailable`(메시지 = 이유).
   - `POST …/triage-proposals/{triage_id}/accept|dismiss`, `POST …/next-step-proposals/{triage_id}/accept|dismiss`.
2. 권한 = 웹 라우트와 같은 동작(`require_action(team.X, api=True)`), 읽기 전용 토큰은 step 2 의존성이 역할 검사보다 먼저 403 `token_read_only`. 업무 키 조회는 step 4 의 공용 함수(없으면 404 `not_found`).
3. 본문은 JSON 만(Pydantic 모델, `extra="forbid"`) — 폼 본문 422(D25).
4. `WorkActionError` → 같은 status·code·message·field 의 `ApiError`. 없는 키 404.
5. 성공 응답 = step 6 상세 변환 함수로 만든 바뀐 업무 상세(D29), 상태 코드는 ARCHITECTURE 표대로.

## 테스트

- 행동마다: 성공 시 DB 상태가 같은 웹 폼 POST 와 같다(같은 fixture 에서 웹 경로·API 경로 각각 실행해 비교하거나 같은 단정), 응답이 상세 모양.
- 맡기기(에이전트) → 실행 생성 또는 소유자 승인·꺼진 러너 대기(웹과 같음), 멤버 배정, 해제, 직접 작업 중 해제 409 `direct_work_active`.
- 판단 제안 accept 두 번째는 409 `triage_stale`(D30), 다음 단계 stale 409 `next_step_stale`.
- read 토큰 → 403 `token_read_only`, member 역할에 없는 동작 → 403 `forbidden`, 폼 본문 422, 모르는 본문 칸 422, Bearer 요청은 Origin 없이 통과.

## 금지사항 (이 step)

- `work_actions` 의 규칙을 API 라우트에서 다시 쓰거나 우회하지 마라. 이유: README D4·ADR-0031 결정 3.
- 새 업무 등록 API 를 만들지 마라. 이유: README D2 범위 밖.

## 테스트 먼저

소스·템플릿을 바꾸기 전에 `tests/` 미러 경로(`src/workflow/x/y.py` → `tests/workflow/x/test_y.py`)에 실패하는 테스트를 먼저 작성하고, 실패 원인이 의도한 것인지 확인한다(`tdd-guard.sh` 가 테스트 없는 소스 작성을 막는다). 구현한 뒤 해당 테스트와 전체 회귀를 통과시킨다. 이 step 의 변경으로 기존 테스트(주소·문구 단정)가 깨지면 새 동작 기준으로 고치되 단정을 약하게 만들지 않는다. 무엇을 왜 바꿨는지는 summary 에 남긴다.

## Acceptance Criteria

```bash
python3 -m pytest -q
python3 -m ruff check .
```

위 커맨드와 이 step 의 "테스트" 항목을 모두 확인한다.

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 규칙을 확인한다.
   - `domain/` 은 FastAPI·sqlite3·HTTPX·subprocess·Git 을 import 하지 않는다.
   - `server/` 와 `connector/` 는 서로 import 하지 않고 `contracts/` 만 공유한다.
   - 외부 입력(요청 본문·쿼리·토큰 이름)에서 명령·경로를 받아 실행하지 않는다.
   - 비밀값(연결 토큰 `wfc_…`, **멤버 토큰 `wfm_…` 원문**, `OPERATOR_TOKEN`, `SESSION_SECRET`, GitHub App 비밀·설치 토큰, PAT, 로그인·초대 링크 원본)을 DB·로그·응답(발급 응답 1회 제외)·템플릿·백업에 넣지 않는다. DB 에는 `token_sha256` 만.
   - 웹 폼과 API 는 같은 도메인·`work_actions`·`views` 함수를 쓴다. API 전용 규칙(권한·상태 전이·중복 방지)을 따로 만들지 않는다.
   - GLOSSARY 이름을 그대로 쓴다(`WorkItem`·업무 키, `Execution` ≠ run, `Agent` ≠ connector, `outcome` ≠ 상태).
   - 종류 이름(`bug_fix`·`code_review`·`triage`)으로 새 분기를 만들지 않는다(ADR-0009).
3. 성공이면 `phases/24-human-api/index.json` 의 이 step 만 `completed` 로 바꾸고 `summary`(생성·수정 파일, 결정, 테스트 수, 다음 step 주의점 한 줄)와 `verification`(실제 실행한 AC 명령과 종료 코드 목록 `[{"command": "...", "exit_code": 0}]`)을 남긴다. 끝냈지만 걸리는 점(정하지 않고 넘어간 것, 확인 못 한 것)은 `concerns` 에.
4. 3회 수정 후에도 실패하면 `error` + `error_message`. 사용자 결정·외부 자원이 필요하면 `blocked` + `blocked_reason` 후 즉시 중단. 실행하지 않은 검증을 `completed` 로 표시하지 않는다.

## 금지사항 (공통)

- 실제 Claude/Codex·GitHub·Jira·Discord·외부 웹훅을 호출하지 않는다. 이유: 가짜 클라이언트·`MockTransport`·FastAPI `TestClient` 로 검증한다.
- 사용자가 띄워 둔 환경을 읽거나 바꾸지 않는다: 셀프호스트(compose 프로젝트 `runloom`, 포트 8000, 볼륨 `runloom_workflow-data`)·`deploy/selfhost/.env`·러너 홈(`~/Library/Application Support/workflow-connector*`)·launchd(`launchctl` 실행 금지)·`~/.claude/`·GitHub 저장소 `jeongeundev/*`. 컨테이너를 띄우거나 멈추지 않는다. 셀프호스트에 업그레이드를 적용하지 않는다. 이유: 실사용 환경 — 적용은 phase 뒤 사용자 지시.
- 새 의존성(Python 패키지, JS·CSS 프레임워크, CDN)을 추가하지 않는다. 이유: ADR-0002.
- 화면(HTML) 라우트·화면 폼 POST 경로(`/tasks…`, `/work/{key}/…`, `/me…`, `/responsibilities/add`·`/remove`, `/sources/tokens…`, `/operator/…`, `/operator/github/app/callback`, `/requests/…`, `/team…` 등)와 `/healthz` 를 옮기지 않는다. 이유: README 결정 D10 — JSON API 만 `/api/v1` 로 옮긴다. 경로 문자열 일괄 치환을 하지 마라 — 같은 접두어의 화면 폼이 깨진다.
- 사용자 변경을 삭제하거나 stash·reset 하지 않는다. 커밋은 하네스가 한다.
- 이 step 범위 밖 모듈·화면을 "개선"하지 않는다. 기존 테스트의 단정을 약하게 만들어 통과시키지 않는다.
- 기존 테스트를 깨뜨리지 마라.
