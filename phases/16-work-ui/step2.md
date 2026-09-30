# Step 2: work-list-model — 목록 모델 — 필터·묶기·보드 칸(순수)과 목록 행 조회

## 읽어야 할 파일

- AGENTS.md
- phases/16-work-ui/README.md (조사 결과·계획 기본값 9가지 — 이 phase 의 기준), phases/16-work-ui/index.json (이전 step summary)
- docs/ARCHITECTURE.md ("업무 화면 — phase 16" 끝난 업무·빠른 필터·묶기 순서·보드 칸·다음 할 일 규칙·이름 표)
- src/workflow/domain/work_status.py, src/workflow/domain/team.py (`turn_recipients`)
- src/workflow/adapters/repo.py (`list_work_items`·`_member_facts`·`_recipients`·`list_members`·업무 관련 조회), src/workflow/server/views.py (`work_summary`·`assignee_label`·`_member_names`·`work_context`)
- tests/workflow/domain/test_work_status.py, tests/workflow/server/test_views.py, tests/workflow/adapters/test_repo.py

먼저 실제 파일을 읽는다. 대화 이력을 전제로 판단하지 않는다. 이전 step 이 정한 이름·주소는 `docs/ARCHITECTURE.md` "업무 화면 — phase 16" 을 따른다(README 와 다르면 ARCHITECTURE 가 기준).

## 작업

화면 없이 목록의 데이터 모양만 만든다.

1. `src/workflow/domain/work_list.py`(시각·DB·HTTP 없음): `WorkRow`(키·원본 키·원본 종류·제목·담당(종류·id·이름·활성 여부)·우선순위·종류 라벨·상태·상태 이유·다음 할 일·받는 사람 member_id 들·갱신·마감 시각), `filter_rows(rows, q, *, member_id)`, `group_rows(rows, by, *, member_id, ...)` → 묶음 목록(묶음 키·표시 이름·행), `board_columns(rows)` → 6칸. 규칙은 ARCHITECTURE 표 그대로. 모르는 `q`·`by` 는 기본값.
2. repo `list_work_rows(conn, session_id, *, closed_since)`: 목록에 필요한 것을 업무 수와 무관한 적은 쿼리로 모은다(업무 N 개에 쿼리 N 번 금지 — 담당 이름·열린 사람 요청 첫 질문·업무 PR 번호를 JOIN 또는 묶음 조회). 받는 사람 계산은 기존 `_recipients` 를 재사용한다. 업무 PR 표(v12)는 이 step 에서 비어 있어도 읽는다.
3. `server/views.py` 에 목록 화면 문맥 함수(이름은 ARCHITECTURE): 쿼리 값 정규화 → 행 → 필터 → 묶기/보드 → 템플릿에 넘길 dict, 빠른 필터별 건수(내 차례 수 포함).

## 테스트 먼저

`tests/workflow/domain/test_work_list.py`: 필터 4개(내 차례는 받는 사람일 때만, 끝난 업무 제외 규칙), 묶기 순서(담당 없음 → 나 → 다른 멤버 → 에이전트 → 비활성, 묶음 안 우선순위·최근순), 빈 묶음 숨김, 상태 묶음 순서, 보드 6칸(`새로 들어옴` → 대기 칸, `종료` 제외), 모르는 쿼리 값 → 기본값, 다음 할 일 우선순위. `tests/workflow/adapters/test_repo.py`: `list_work_rows` 가 최근 14일 밖 끝난 업무를 빼고 `closed=all` 에서 넣음, 다른 워크스페이스 업무 없음, 업무 30개에서 쿼리 수가 업무 수에 비례하지 않음(`conn.set_trace_callback` 으로 셈). `tests/workflow/server/test_views.py`: 문맥 함수의 건수·정규화.

소스·템플릿 변경 전에 `tests/` 미러 경로에 실패 테스트를 작성하고 실패 원인이 의도한 것인지 확인한다(`tdd-guard.sh` 가 테스트 없는 소스 작성을 막는다). 구현 후 해당 테스트와 전체 회귀를 통과시킨다. 이 step 의 변경으로 기존 테스트가 깨지면 새 동작 기준으로 고치되 단정을 약하게 만들지 않는다(무엇을 왜 바꿨는지 summary 에 남긴다).

## Acceptance Criteria

```bash
python3 -m pytest -q
python3 -m ruff check .
```

위 커맨드와 이 step 의 테스트 항목을 모두 확인한다.

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 규칙을 확인한다: `domain/` 은 FastAPI·sqlite3·HTTPX·subprocess·Git 을 import 하지 않는다 / `server/` 와 `connector/` 는 서로 import 하지 않고 `contracts/` 만 공유한다 / 외부 입력(요청 본문·폼·쿼리 문자열·PR 제목·브랜치 이름·이슈 본문·모델 응답)에서 명령·경로를 받아 실행하지 않는다 — 주소 쿼리 값은 열거형으로만 받고 되돌아갈 URL 을 받지 않는다 / 비밀값(연결 토큰, `OPERATOR_TOKEN`, `SESSION_SECRET`, 로그인 세션·초대·재설정 토큰 원문, 비밀번호, GitHub App 비밀·설치 토큰, PAT, 알림 웹훅 URL)은 DB·로그·응답·템플릿·백업에 넣지 않는다 / 권한은 `domain/team.py` 역할 × 동작 표로만 판정한다 / 템플릿은 PR 제목·이슈 제목 같은 외부 문자열을 자동 이스케이프로만 출력한다(`|safe` 금지) / GLOSSARY 이름을 그대로 쓴다 / phase 8·11·12·14·15 의 GitHub 순환(수집 → 맡기기 → 수정 → 검토 → 초안 PR → 병합 추적 → 업무 완료)과 사람별 내 차례가 그대로 동작한다.
3. 성공이면 `phases/16-work-ui/index.json` 의 step 2 만 `completed` 로 바꾸고 `summary` 에 생성·수정·삭제 파일, 결정, 검증 결과(테스트 수), 다음 step 이 주의할 점을 한 줄로 남긴다.
4. 3회 수정 후에도 실패하면 `error` + `error_message`, 사용자 결정·외부 자원이 필요하면 `blocked` + `blocked_reason` 을 기록한다. 실행하지 않은 검증을 `completed` 로 표시하지 않는다.

## 금지사항

- 실제 GitHub(api.github.com·github.com)·Discord·외부 웹훅을 호출하지 않는다(공식 문서 읽기는 허용). 이유: 모든 step 은 httpx `MockTransport`·가짜 GitHub·가짜 알림 수신으로 검증한다. 실연동은 phase 뒤 사용자와 함께 한다.
- 사용자가 띄워 둔 셀프호스트(compose 프로젝트 `runloom`, 포트 8000, 볼륨 `runloom_workflow-data`)·`deploy/selfhost/.env`·러너 홈(`~/Library/Application Support/workflow-connector/`)·launchd·`~/.claude/`·`/Users/kje/demo/*` 를 읽거나 바꾸지 않는다. 컨테이너를 띄우거나 멈추지 않는다. 이유: 실사용 중인 환경이다.
- 새 의존성(Python 패키지, JS 프레임워크·번들러, 외부 CDN·웹폰트·아이콘 라이브러리)을 추가하지 않는다. 이유: ADR-0002·UI_GUIDE — Jinja2 서버 렌더 + CSS + 소량 인라인 JS.
- 종류 이름(`bug_fix`·`code_review`)으로 새 분기를 만들지 않는다. 이유: 업무 종류·후속 규칙은 워크스페이스 등록 데이터다(ADR-0009, AGENTS.md).
- `tasks` 표를 재생성하거나 `tasks.status` CHECK 를 바꾸지 않는다. 이유: ADR-0020 — 업무 상태는 `work_items` 에, 단계 상태는 그대로.
- 판단 제안·확신도·자동 시작(18-triage), Jira(17-jira), 모니터링 지표 확장(19-monitor), Claude Code 훅(다음 phase), 보드 끌어 옮기기, 브랜치 push 감지, 여러 행 일괄 변경을 만들지 않는다. 이유: 사용자 결정 2026-09-30 범위 밖.
- 직접 작업을 Runloom 결과 판정·완료 판정으로 처리하지 않는다. 이유: 완료는 PR 병합 같은 원본 신호로만(REDESIGN_PLAN 10절, 계약 v1).
- 사용자 변경을 삭제하거나 stash·reset 하지 않는다. 커밋은 하네스가 한다.
- 이 step 범위 밖 모듈을 "개선"하지 않는다. 기존 테스트의 단정을 약하게 만들어 통과시키지 않는다.
