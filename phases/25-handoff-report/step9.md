# Step 9: report-web — 업무 패널·리포트 페이지

## 읽어야 할 파일

- AGENTS.md, phases/25-handoff-report/README.md("결정" 절), phases/25-handoff-report/index.json(이전 step summary)
- docs/adr/0033-handoff-report.md, docs/ARCHITECTURE.md "인계 리포트 — phase 25"(**README 와 다르면 ARCHITECTURE 가 기준** — step 0 이 코드 조사로 고정한 이름·표·경로), docs/CONTRACT.md 19절, docs/GLOSSARY.md phase 25 용어
- docs/UI_GUIDE.md, `src/workflow/server/web.py`(업무 패널 라우트·`_panel`·`INPUT_KIND_CHOICES`·폼 처리 패턴·`origin_guard`), `src/workflow/server/views.py`(`work_panel_context`), `templates/_work_panel.html`·`base.html`·`artifact.html`, `server/static/`, `tests/workflow/server/test_web_work_panel.py`
- step 8 결과: 공유 행동 함수와 API

## 작업

1. 단계 줄(D32): 원인 실행의 최신 original 상태에 따라 `리포트 작성 중` / [리포트 보기] / `리포트 없음(사유)` + [리포트 다시 만들기](권한 있을 때). 보드·목록·모니터링에는 넣지 않는다(D17·업무 상태 불변).
2. 리포트 페이지(ARCHITECTURE 경로): Jinja 템플릿으로 절별 주장과 근거 링크(산출물·커밋·PR·업무 원문), `근거 확인 안 됨`·`기록 없음`·`PR 읽기 실패` 표시, 결정 항목마다 답 폼(받는 사람·관리자만 — 아니면 답만 보임), [내게 맞춰 풀기] + 자기 Agent 선택(기본값: 다음 단계 Agent, 맞춤 풀이를 할 수 없는 Agent 는 빼고 남는 게 없으면 숨김 — D8), 맞춤 풀이 보기(만든 사람 표시, 원본을 다시 만들었으면 `옛 리포트 기준`), 이전 리포트는 '자세히'(D6).
3. 다이어그램(D24·D40): ARCHITECTURE 의 버전·URL·sha256 으로 `curl -fsSL` 로 받아 `shasum -a 256` 으로 확인한 뒤 `src/workflow/server/static/vendor/mermaid.min.js` 와 라이선스 파일(`mermaid.LICENSE`)로 둔다 — Write/Edit 도구로 `.js` 를 쓰지 않는다(`tdd-guard.sh` 가 테스트 없는 `.js` 작성을 막는다). 리포트 페이지에서만 `<script src="/static/vendor/mermaid.min.js">` 로 불러 초기화는 템플릿 인라인 `<script>`(`securityLevel: 'strict'`)로, 실패하면 텍스트 그대로. 외부 CDN 주소를 페이지에 쓰지 않는다. 네트워크가 막혀 받을 수 없으면 `blocked`.
4. 모든 모델 텍스트는 Jinja 자동 이스케이프(`|safe` 금지 — D25). Mermaid 텍스트는 `<pre class="mermaid">` 안 텍스트로만.
5. 폼 POST 는 step 8 의 공유 행동 함수를 부르고 기존 폼 패턴(Origin 검사·303)을 따른다.
6. ARCHITECTURE 의 리포트 HTML `/api/v1` 경로(D29)를 같은 렌더 함수로 만들고(쿠키·멤버 토큰 모두, 읽기 전용 토큰 허용), ARCHITECTURE "### 주소 표" 에 행을 더하고 `tests/workflow/server/test_app.py` 의 행 수 단정을 고친다. 화면 경로는 `web.py` 에 둔다(phase 24 라우트 분류 규칙 — 화면 라우트는 `web.py`).
7. 종류·규칙 폼 선택지(`INPUT_KIND_CHOICES` 등)에서 `handoff_report` 를 뺀다(D26).

## 테스트

- 패널: 리포트 상태별 단계 줄 문구, 다시 만들기 버튼 노출 권한.
- 리포트 페이지: 절·근거 링크, `<script>` 가 든 주장 텍스트가 이스케이프됨, Mermaid 텍스트가 `pre` 안 텍스트, 결정 폼은 받는 사람에게만, 맞춤 풀이 버튼 노출 조건, 이전 리포트 '자세히'.
- 결정 답·맞춤 풀이·다시 만들기 폼 POST → 303, 권한 없는 멤버 403.
- 종류 폼 선택지에 `handoff_report` 없음.
- `/api/v1` 리포트 HTML: 토큰(read)으로 200 `text/html`, 미인증 401.
- 리포트 페이지가 외부 스크립트 주소를 쓰지 않는다. `mermaid.min.js` 의 sha256 이 ARCHITECTURE 값과 같다.

## 금지사항 (이 step)

- 모델이 쓴 문자열에 `|safe`·`Markup` 을 쓰지 마라. 이유: 외부 입력 XSS(D25).
- 업무 상태·보드 칸 계산을 바꾸지 마라. 이유: 리포트는 단계 안 표시만(D17·README 범위).

## 테스트 먼저

소스·템플릿을 바꾸기 전에 `tests/` 미러 경로(`src/workflow/x/y.py` → `tests/workflow/x/test_y.py`)에 실패하는 테스트를 먼저 작성하고, 실패 원인이 의도한 것인지 확인한다(`tdd-guard.sh` 가 테스트 없는 소스 작성을 막는다). 구현한 뒤 해당 테스트와 전체 회귀를 통과시킨다. 이 step 의 변경으로 기존 테스트(개수·이름 집합·스키마 버전·문구 단정)가 깨지면 새 동작 기준으로 고치되 단정을 약하게 만들지 않는다. 무엇을 왜 바꿨는지는 summary 에 남긴다.

## Acceptance Criteria

```bash
python3 -m pytest -q
python3 -m ruff check .
```

위 커맨드와 이 step 의 "테스트" 항목을 모두 확인한다.

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트: ARCHITECTURE.md 디렉토리 구조·외부 의존 표를 벗어나지 않았는가, ADR 기술 스택 안인가, AGENTS.md CRITICAL 규칙(도메인 순수성, `server`↔`connector` 상호 import 금지, 외부 입력으로 명령·경로 실행 금지, 비밀값)을 지켰는가, GLOSSARY 용어를 그대로 썼는가.
3. `phases/25-handoff-report/index.json` 의 이 step 을 갱신한다: 성공 → `"status": "completed"`, `"summary"`(다음 step 에 필요한 파일·이름·결정), `"verification"`(실행한 AC 명령과 종료 코드). 3회 수정해도 실패 → `"error"` + `"error_message"`. 사용자 개입 필요 → `"blocked"` + `"blocked_reason"` 후 즉시 중단.
