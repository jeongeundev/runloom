# Step 0: prompt-v3

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/docs/ARCHITECTURE.md` — "진단 모델과 평가 기준"
- `/docs/adr/` (하위 파일 전부)
- `/docs/GLOSSARY.md`
- `/docs/PRD.md` — "예상 조사 순서", "도구·인계 수용 기준"
- `/docs/DIAG_EVAL.md` — "관찰" 1번 (`list_runs.before` 연도 오기 7/15), "남은 문제의 분리" 표의 "프롬프트" 행
- `/src/diagnostic_demo/worker/prompt.py` — `PROMPT_VERSION`, `SYSTEM_PROMPT` "도구"·"조사 순서" 절
- `/src/diagnostic_demo/tools/api.py` — `list_runs` 스키마의 `before` 설명
- `/tests/diagnostic_demo/worker/test_prompt.py` (`test_prompt_version`, `test_system_prompt_has_no_fixed_answer` 의 금지 목록)
- `grep -rn "diag-prompt-v2" src tests scripts docs` 결과 — 버전 문자열을 검사하는 곳

이전 step에서 만들어진 코드를 꼼꼼히 읽고, 설계 의도를 이해한 뒤 작업하라.

## 배경

프롬프트 v2 재평가에서 15회 중 7회가 `list_runs` 의 `before` 에 실패 실행의 연도(2026) 대신 2023·2024 를 써서 빈 목록을 받고
"직전 정상 실행이 없다" 고 결론했다. 모델이 자료에 적힌 시각 대신 자기 학습 시점의 연도를 쓴 것이다. 이 step 은 `before` 인자를 어떻게
채우는지의 규칙 한 줄을 프롬프트에 넣는다. 이는 정답(어느 실행이 기준인지) 이 아니라 도구 인자 작성 규칙이다. 다음 step 의 모델 비교에서
이 잡음을 걷어내기 위한 것이다.

## 작업

### `src/diagnostic_demo/worker/prompt.py`

- `PROMPT_VERSION = "diag-prompt-v3"`.
- "조사 순서" 2번 또는 "도구" 절의 `list_runs` 설명에 한 줄을 추가한다. 내용은 다음 두 가지를 모두 담는다:
  - `before` 에는 실패 실행의 `get_run` 결과에 있는 `started_at` 값을 **그대로**(연도·시간대 포함, 다시 쓰지 않고) 넣는다.
  - 시각은 자료에서 읽은 값만 쓰고 현재 날짜나 기억으로 채우지 않는다.
- 그 외 문장은 바꾸지 않는다 (인용 규칙·결론 규칙은 v2 그대로). 문구에 fixture 의 실제 값(`daily-0920-0900`, `2026-09-20`, `daily-report` 등) 을 쓰지 않는다.

### 참조 갱신

- `PROMPT_VERSION` 문자열을 검사하는 테스트(`tests/diagnostic_demo/worker/test_prompt.py`, `test_runner.py`, `scripts/test_diag_eval.py` 등) 와
  `docs/CONTRACT.md` 의 `"prompt_version": "diag-prompt-v2"` 예시를 v3 로 맞춘다. `docs/DIAG_EVAL*.md` 는 과거 기록이므로 고치지 않는다.
- `src/diagnostic_demo/worker/fake_script.py` 대본은 그대로 유효해야 한다.

### 테스트 (먼저 작성)

- `tests/diagnostic_demo/worker/test_prompt.py`: 버전 `diag-prompt-v3`; `list_runs`·`before`·`started_at` 이 한 문장 안에서 언급된다;
  "현재 날짜" 류의 표현으로 기억 사용 금지가 명시된다; `test_system_prompt_has_no_fixed_answer` 금지 목록에 `2026`·`daily-0920` 을 추가해 통과한다.

## Acceptance Criteria

```bash
python3 -m pytest tests/diagnostic_demo -q
python3 -m pytest -q
python3 -m ruff check .
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - ARCHITECTURE.md 디렉토리 구조를 따르는가?
   - ARCHITECTURE.md 외부 의존 표에 없는 외부 서비스를 끌어들이지 않았는가?
   - ADR 기술 스택을 벗어나지 않았는가?
   - AGENTS.md CRITICAL 규칙을 위반하지 않았는가?
   - GLOSSARY.md 용어를 그대로 썼는가? 금지 표현을 쓰지 않았는가?
3. 결과에 따라 `phases/2-model-compare/index.json`의 해당 step을 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"` (추가한 문장을 그대로 적는다)
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- 유료 모델 호출을 하지 마라. 이유: 실호출은 Step 2 다.
- 프롬프트에 데모 정답(기준 실행 ID·경로 이름·오류 코드·문서 ID·적용 시각·연도) 을 넣지 마라. 이유: PRD "고정된 답변 재생으로 대체하지 않는다". "started_at 을 그대로 쓴다" 는 규칙이지 값이 아니다.
- `before` 없이 전체 목록을 보라고 바꾸지 마라. 이유: 도구 계약(`list_runs` 의 인자) 을 바꾸는 것이 아니라 모델이 인자를 자료에서 채우게 하는 것이 목적이다.
- `src/diagnostic_demo/tools/`·`src/workflow/contracts/` 를 고치지 마라. 이유: 이 step 은 프롬프트만 다룬다.
- 기존 테스트를 깨뜨리지 마라.
