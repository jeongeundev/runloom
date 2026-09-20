# Step 10: demo-guide

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/AGENTS.md`
- `/docs/UI_GUIDE.md` — 홈 화면 구성, 문구 규칙(과장·AI 템플릿 문구 금지)
- `/docs/adr/0000-principles.md`, `/docs/adr/0005-access-model-anonymous-session-operator-token.md` — 왜 운영자가 미리 등록하고 심사자는 익명 세션인지
- `/docs/product/PRODUCT_BRIEF.md` — 차별성 가설 (과장하지 않는 표현)
- `/src/workflow/server/templates/home.html`, `agents.html`, `agents_register.html`(step 9)
- `/src/workflow/server/web.py` — `/agents`, `/agents/register`(step 9), `/operator`(토큰 필요)
- `/tests/workflow/server/test_ui.py` — `test_visible_text_has_no_forbidden_phrases`

이전 step에서 만들어진 코드를 꼼꼼히 읽고, 설계 의도를 이해한 뒤 작업하라. 새 기능은 테스트를 먼저 작성한다 (AGENTS.md TDD, `tests/` 는 `src/` 미러 배치).

## 배경

step 9 이후 심사자는 빈 워크스페이스에서 시작해 에이전트를 직접 등록한다. 그래도 "연결 프로그램·진단 API 는 누가 띄웠나, 실제 서비스에서는 어떻게 되나" 를 화면이 설명해야 데모가 제품의 한계처럼 보이지 않는다. 홈 상단에 짧은 안내 블록을 둔다 (한 번 접으면 기억).

## 작업

### `templates/home.html` — 안내 블록 (접을 수 있는 `<details open>`)

제목 "이 데모는 이렇게 준비돼 있습니다". 내용(사실만, 4줄 이내):
1. 운영자가 자기 Mac 에서 연결 프로그램(Codex·Claude Code)을 띄우고 사내 진단 API 의 접속 정보를 넣어 두었다. 실제 서비스에서도 이 "연결" 은 소유자가 한 번 한다.
2. 그 위에서 **등록**(발견된 저장소·검증 프로필·능력을 확인하고 이름·범위를 정하는 것)은 이 워크스페이스에서 직접 한다 → 「에이전트 등록」. 실제 서비스에서는 로그인한 사용자의 워크스페이스다.
3. 업무는 「GitHub Issues 에서 가져오기」(`github_enabled` 일 때), 「시연 업무 만들기」(미리 채워진 A+B), 빈 등록 폼 중 하나로 만든다.
4. A 가 끝나면 B 는 사람 조작 없이 시작한다. 코드 수정 에이전트를 둘 등록하면 자동 선택은 "확인 필요" 가 되고 그때 직접 고른다.
`localStorage` 로 접힘 상태를 기억한다 (try/catch, 실패해도 렌더). 새 외부 자산 없음. 등록된 에이전트가 0개면 블록을 항상 펼친다.

### 테스트 (먼저 작성)

- `test_ui.py`: 홈에 안내 블록 4줄과 「에이전트 등록」 링크, 등록 0개면 `open`, 금지 문구 검사 통과, 390px 가로 스크롤 없음.

## Acceptance Criteria

```bash
python3 -m pytest tests/workflow/server -q
python3 -m pytest -q
python3 -m ruff check .
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

- 안내 문구에 "시장에 없다", "완전 자동", "어떤 에이전트든" 같은 과장을 쓰지 마라. 이유: PRODUCT_BRIEF 의 표현 원칙·UI_GUIDE 금지 문구.
- 안내 블록에 운영자 토큰·연결 코드·API URL 을 보이지 마라. 이유: 비밀값·내부 주소.
- 기존 테스트를 깨뜨리지 마라
