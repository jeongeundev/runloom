# Step 8: issues-import-web

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/AGENTS.md`
- `/docs/UI_GUIDE.md` — 셸·카드·칩·배지 규칙, 금지 표현
- `/docs/PRD.md` — 업무 등록 폼의 필드와 기본값
- `/src/workflow/server/web.py` — `task_new`(`?example=`·`predecessor=` 로 폼을 채우는 방식), `task_create`, `_insert_new_task`, `_form_context`
- `/src/workflow/server/templates/home.html`, `task_new.html`, `_live.html`, `_sidebar.html`, `task_detail.html`
- `/src/workflow/server/views.py` — step 6 의 `source`
- `/src/workflow/adapters/github_issues.py` — step 7
- `/src/workflow/server/app.py` — 앱 상태에 어댑터를 두는 방식(`app.state.store` 처럼)
- `/tests/workflow/server/test_web.py`, `test_ui.py`(Playwright 없이 렌더 검사), `conftest.py`(앱 fixture — `GitHubIssues` 를 가짜로 주입할 수 있어야 한다)

이전 step에서 만들어진 코드를 꼼꼼히 읽고, 설계 의도를 이해한 뒤 작업하라. 새 기능은 테스트를 먼저 작성한다 (AGENTS.md TDD, `tests/` 는 `src/` 미러 배치).

## 배경

심사자가 "티켓에서 업무가 시작된다" 를 보는 화면. 이슈 목록 → 하나 고름 → 제목·본문이 채워진 등록 폼 → 등록하면 카드에 `GitHub #12` 링크. 능력·대상은 이슈에 없으므로 폼에서 고른다. 다만 시연 이슈에는 라벨을 달아 두어 라벨로 미리 채운다.

## 작업

### `src/workflow/server/app.py`

- `settings.github` 가 있으면 `app.state.github = GitHubIssues(repo, token)`, 없으면 None. 테스트는 `app.state.github` 에 가짜를 넣는다.

### `src/workflow/server/web.py`

- `GET /tasks/import` — `app.state.github` 없으면 404(`feature_disabled`). 있으면 `list_open()` 을 보여 준다: 번호·제목·라벨·갱신 시각, 각 행에 "이 이슈로 업무 등록" 링크 `/tasks/new?issue=12`. `GitHubUnavailable` 이고 캐시 없음 → 같은 화면에 "GitHub 에 연결할 수 없습니다" 안내와 빈 목록 (500 이 아님). stale 이면 "캐시 · n분 전" 표시. 이 세션에서 이미 가져온 이슈(`source.number` 일치)는 "가져옴 → 업무 링크" 로 표시하고 다시 가져오기는 막지 않는다.
- `GET /tasks/new?issue=N` — `github.get(N)` 으로 폼을 채운다: `title` = 이슈 제목, `request` = 본문(비면 제목), 숨은 필드 `source_kind=github_issue`, `source_number=N`. 라벨 규칙: `runloom:diagnose` → `EXAMPLES["diagnose"]` 의 능력·범위·완료 방식·run_id 를 채움(제목·본문은 이슈 것), `runloom:code-change` → `EXAMPLES["fix"]` 의 능력·범위·실행/완료 방식. 라벨 없으면 능력 미선택(폼 기본). 이슈 없음(404) → 404 페이지.
- `POST /tasks` — `source_kind`·`source_number` 가 오면 서버가 **다시** `github.get(number)` 로 이슈를 확인해 `source` 를 만든다 (클라이언트가 보낸 URL·제목을 그대로 믿지 않는다). 없으면 422. `with_successor` 와 함께 오면 B 에는 source 를 붙이지 않는다.
- `_base` 컨텍스트에 `github_enabled: bool` 을 넣어 홈·사이드바가 버튼을 보일지 정한다.

### 템플릿

- `home.html`: 「시연 업무 만들기」 옆에 `github_enabled` 이면 「GitHub Issues 에서 가져오기」 버튼(`/tasks/import`). (step 9 가 "등록된 에이전트 0개면 숨김" 규칙을 덧붙인다.)
- `tasks_import.html` (신규): 표 형태, UI_GUIDE 의 카드·칩 규칙. 외부 자산 없음.
- `task_new.html`: `source_*` 숨은 필드와 상단에 "GitHub #12 · 제목 — 이슈 링크" 안내 줄.
- `task_detail.html`·`_live.html`·`_sidebar.html`·`home.html` 의 업무 카드: `source` 가 있으면 `GitHub #12` 칩(외부 링크, `rel="noopener"`, 새 탭).

### 테스트 (먼저 작성)

- `test_web.py`: 가짜 `GitHubIssues` 로 — `/tasks/import` 목록 렌더·라벨·가져온 표시, 기능 꺼짐 404, 연결 실패 시 200 + 안내, `?issue=N` 폼 채움(라벨 세 경우), POST 가 서버 재조회로 `source` 저장(조작된 `source_number` 는 422), 카드에 칩·링크, 다른 세션은 그 업무를 못 봄(기존 규칙 유지).
- `test_ui.py`: 가져오기 화면·칩이 UI_GUIDE 금지 CSS·외부 자산 규칙을 지킨다; 390px 가로 스크롤 없음(기존 반응형 검사에 화면 추가).

## Acceptance Criteria

```bash
python3 -m pytest tests/workflow/server -q
python3 -m pytest -q
python3 -m ruff check .
grep -n "rel=\"noopener\"" src/workflow/server/templates/*.html | head -1
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - ARCHITECTURE.md 디렉토리 구조를 따르는가?
   - ARCHITECTURE.md 외부 의존 표에 없는 외부 서비스를 끌어들이지 않았는가?
   - ADR 기술 스택을 벗어나지 않았는가?
   - AGENTS.md CRITICAL 규칙을 위반하지 않았는가? (도메인이 I/O 를 import 하지 않는가, server↔connector 가 서로 import 하지 않는가, 외부 입력에서 명령·경로를 실행하지 않는가, 비밀값이 DB·로그·응답에 없는가)
   - GLOSSARY.md 용어를 그대로 썼는가? 금지 표현을 쓰지 않았는가?
3. 결과에 따라 `phases/4-claude-issues/index.json`의 해당 step을 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"` (만든 파일·함수·결정 사항)
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- 이슈 본문을 그대로 HTML 로 렌더하지 마라(자동 이스케이프 유지, `|safe` 금지). 이유: 외부 입력이다.
- 이슈 본문·제목에서 run_id·저장소·명령을 뽑아 대상으로 쓰지 마라. 이유: AGENTS.md CRITICAL — 외부 입력에서 경로·명령을 받지 않는다. 대상은 라벨 규칙(우리가 정한 값) 또는 사용자가 폼에서 고른다.
- 클라이언트가 보낸 `source_url`·제목을 저장하지 마라. 이유: 서버 재조회로만 `source` 를 만든다.
- 시연용이라도 실제 GitHub 를 테스트에서 부르지 마라.
- 기존 테스트를 깨뜨리지 마라
