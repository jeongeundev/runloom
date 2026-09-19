# Step 7: web-ui

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/docs/UI_GUIDE.md` — 전체. 이 step 의 명세다. "참고 화면과 대응", "앱 셸 — 3열", "업무 상세 — 가운데 열의 순서", "결과 카드", "오른쪽 열 — 산출물 뷰어", "색상", "상태 표시", "컴포넌트", "실시간 갱신"
- `/docs/GLOSSARY.md` — 사용자 상태·outcome 코드
- `/docs/CONTRACT.md` — 5·7·9절 (결과 카드와 뷰어가 표시할 필드)
- `/src/workflow/server/web.py`, `views.py`, `templates/` (Step 6) — 컨텍스트 키를 그대로 쓴다. 라우트 시그니처는 바꾸지 않는다
- `/scripts/hooks/tdd-guard.sh` — `.html`·`.css` 는 테스트 불필요. `.js` 파일은 테스트를 요구하므로 JS 는 `base.html` 안 `<script>` 에 둔다

## 작업

Step 6 의 최소 템플릿을 UI_GUIDE 대로 다시 쓰고 CSS 와 폴링 JS 를 추가한다. 서버 로직은 라이브 조각 라우트 하나만 추가한다.

### 라우트 추가 (`web.py`)

`GET /tasks/{task_id}/live` → `_live.html` 조각만 렌더 (상태 줄, 실행 블록, 결과 카드, 산출물 칩, 동작 영역, 후속 업무 칩). 세션 소유 확인. `Cache-Control: no-store`.

### 파일

```text
src/workflow/server/static/style.css        # UI_GUIDE "색상" 의 :root 변수 그대로. 컴포넌트는 변수만 참조
src/workflow/server/templates/base.html     # 3열 셸 + 인라인 <script> (폴링)
src/workflow/server/templates/_sidebar.html # 왼쪽 목록: 제품명, 탐색(업무·에이전트·운영자는 is_operator 일 때만), 최근, + 버튼, 내 업무 목록(제목·상대시각·상태 점)
src/workflow/server/templates/_status.html  # 배지 매크로: 점 + 한글 상태 + 이유. 점 채움 규칙은 UI_GUIDE 표
src/workflow/server/templates/_live.html    # 라이브 조각
src/workflow/server/templates/_result_card.html
src/workflow/server/templates/_viewer.html  # 오른쪽 열: 진단 결과 / 검증 요약 / 개별 산출물
src/workflow/server/templates/home.html, task_new.html, task_detail.html, artifact.html, agents.html, agent_detail.html, operator.html, error.html
```

정적 파일은 `create_app` 에서 `StaticFiles` 로 `/static` 에 마운트한다.

### 반드시 지킬 것 (UI_GUIDE 에서 테스트로 고정하는 항목)

- 배지: 상태 텍스트가 항상 보인다. 색만으로 표시하지 않는다. `data-status="{label}"` 속성을 둔다.
- outcome 라벨: `ready_for_handoff`→`인계 가능`, `ready_for_review`→`검토 가능`, `needs_information`→`정보 필요`. `filters.py` 에 `outcome_label` 필터.
- 결과 카드 셋째 줄 메타: 진단은 `{diagnosis.code} · 근거 N · 첨부 M · 검증 P/Q 통과`(verdict 가 있을 때), 코드 수정은 `{result_commit[:7]} ← {base_commit[:7]} · N files · +A -D · {profile_id} exit {code}` (diff 산출물에서 파일 수·증감을 세는 헬퍼 `diff_stats(text) -> (files, added, removed)` 를 `views.py` 에 둔다).
- 뷰어 "진단 결과" 순서: 검증 결과 → summary → findings(근거 칩) → diagnosis 표 → repair_request → missing_information. `needs_information` 이면 missing_information 이 맨 위.
- 근거 칩 `evidence_id@version · location` 을 누르면 첨부 원문의 해당 값이 펼쳐진다. 서버가 `resolve_location` 으로 잘라 컨텍스트에 넣는다 (`views.py` 의 `evidence_excerpts(...)`). 원문이 없으면 "첨부 없음".
- 두 칸 비교: 진단 `변경 전 응답 / 변경 후 응답`(old_path·new_path 값 강조), 코드 수정 `수정 전 테스트 / 수정 후 테스트`(각 로그 마지막 20줄) + 생성 보고서.
- diff 렌더: `<pre>` 안에서 `+` 줄은 `--bg-done`, `-` 줄은 `--bg-failed` 배경 (`views.py` 의 `diff_lines(text) -> list[(kind, line)]`).
- 동작 영역: 상태별로 하나만. 채팅 입력창 없음.
- 폴링: `data-live` 요소가 있으면 3초마다 `/tasks/{id}/live` 를 받아 교체. 입력 중(`document.activeElement` 가 조각 안의 textarea)이면 건너뜀. "마지막 갱신 {시각}" 표시, 실패 시 "갱신 실패, 재시도 중". 완료·실패면 10초. `prefers-reduced-motion` 이면 맥동 없음.
- 반응형: 1200px 미만 뷰어는 슬라이드 오버, 800px 미만 한 열. 390px 에서 가로 스크롤 없음 (`overflow-x: auto` 는 표·pre 만).
- 금지: `backdrop-filter`, `linear-gradient`, `box-shadow` 글로우, 보라 계열(`#7c3aed`, `#6366f1`, `#8b5cf6`, `indigo`, `violet`, `purple`), 외부 URL(`http://`, `https://`)이 CSS·템플릿의 `src`/`href`에 없음(링크 텍스트 제외), "Powered by".

### 테스트 — `tests/workflow/server/test_ui.py`, `test_views.py` 에 추가

Step 6 의 fixture 를 재사용해 실제 페이지를 렌더한다.

- 홈·상세·에이전트·운영자 페이지가 200 이고 `base.html` 의 3열 컨테이너(`class="shell"` 안에 `.sidebar`, `.main`, `.viewer`)가 있다.
- 상세 페이지 텍스트에 상태 한글 라벨과 이유가 같은 줄(같은 `.status-line` 요소)에 있다.
- `result_ready` + `diagnosis_result`(CONTRACT 5절) 를 넣은 업무의 뷰어에 `인계 가능`, `response_path_changed`, `근거 5`, 근거 칩 `response-after@1 · $.data.records` 와 펼침 내용에 `records` 가 있다.
- `code_change_result`(CONTRACT 7절) + diff 산출물을 넣으면 `검토 가능`, `9b7e4d2 ← 3f9c2e1`, `vp-pytest exit 0`.
- `/tasks/{id}/live` 가 조각만 돌려주고(`<html` 없음) `data-live` 를 포함한다. 다른 세션은 404.
- 렌더된 모든 페이지의 가시 텍스트에 `대기 중`, `Powered by` 가 없다.
- `style.css` 에 `backdrop-filter`, `gradient`, 보라 계열 hex 가 없다. `:root` 에 `--state-attention` 등 UI_GUIDE 변수가 있다.
- 템플릿 소스에 `<script src="http`, `<link href="http` 가 없다.

### GLOSSARY

`outcome 라벨`(화면 표시용 한글: 인계 가능·검토 가능·정보 필요. 코드 값은 그대로 유지) 을 추가한다.

## Acceptance Criteria

```bash
python3 -m pytest tests/workflow/server -q
python3 -m pytest -q
python3 -m ruff check .
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. UI_GUIDE "하지 마라" 표를 한 줄씩 대조한다.
3. `WORKFLOW_DEV=1 python3 -m uvicorn workflow.server.app:app --port 8000` 을 띄우고 `curl -s localhost:8000/ | head` 로 셸이 나오는지 확인한 뒤 종료한다 (실패해도 AC 는 pytest 기준).
4. `phases/0-mvp/index.json` 의 step 7 을 업데이트한다 (summary 에 템플릿 목록과 라이브 조각 경로).

## 금지사항

- Tailwind·웹폰트·아이콘 라이브러리·CDN 을 쓰지 마라. 이유: ADR-0002, 오프라인 VM 에서도 떠야 한다.
- `.js` 파일을 따로 만들지 마라. 이유: tdd-guard 가 테스트를 요구한다. `base.html` 인라인 `<script>`.
- 다크 테마를 만들지 마라. 이유: UI_GUIDE 첫 범위 밖.
- Step 6 의 라우트 시그니처·컨텍스트 키를 바꾸지 마라. 이유: Step 6 테스트가 그대로 통과해야 한다.
- 기존 테스트를 깨뜨리지 마라.
