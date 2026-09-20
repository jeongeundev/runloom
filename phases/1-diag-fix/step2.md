# Step 2: prompt-v2

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/docs/ARCHITECTURE.md` — "진단 모델과 평가 기준", "진단 결과와 근거"(Step 0 에서 갱신된 location 문법)
- `/docs/adr/` (하위 파일 전부)
- `/docs/GLOSSARY.md`
- `/docs/PRD.md` — "예상 조사 순서", "진단 결과 형식", "도구·인계 수용 기준"
- `/docs/DIAG_EVAL.md` — "관찰" 1·2·3번, "분리 분석" 표의 "계약"·"프롬프트" 행
- `/src/diagnostic_demo/worker/prompt.py` — `PROMPT_VERSION`, `SYSTEM_PROMPT` "인용 규칙"·"결론 규칙"
- `/src/diagnostic_demo/worker/model.py` — `DiagnosisDraft`, `_strict`, `draft_json_schema`
- `/src/workflow/contracts/v1.py` — Step 0 이 추가한 `Location` 의 `Field(pattern=)`
- `/src/diagnostic_demo/tools/api.py` — Step 1 이 바꾼 텍스트 자료 반환 형태 (`read_evidence` 설명)
- `/tests/diagnostic_demo/worker/test_prompt.py`, `/tests/diagnostic_demo/worker/test_model.py`
  (`test_draft_json_schema_is_openai_strict_compatible`)
- `/src/diagnostic_demo/worker/fake_script.py` — 대본의 인용이 새 문법에서도 유효한지 확인용

이전 step에서 만들어진 코드를 꼼꼼히 읽고, 설계 의도를 이해한 뒤 작업하라. 특히 `phases/1-diag-fix/index.json` 의 Step 0·1 `summary` 에 적힌
정규식 상수 이름과 텍스트 자료 반환 형태를 그대로 반영하라.

## 배경

Step 0 이 계약 `Location` 에 배열 인덱스와 JSON Schema `pattern` 을 넣었고, Step 1 이 텍스트 자료를 줄 번호와 함께 돌려주게 했다.
이 step 은 (1) 그 두 변화가 모델에 보내는 strict 스키마와 프롬프트에 실제로 반영됐는지 확인하고, (2) 프롬프트의 인용 규칙을 새 문법에 맞게
고친다. 프롬프트를 바꾸므로 `PROMPT_VERSION` 을 올린다. 평가 보고서대로 결론 규칙 미준수(`needs_information` 0회) 는 프롬프트만으로
해결된다고 기대하지 않는다 — 문구를 보강하되 정답을 넣지 않는다.

## 작업

### 1. 스키마 확인 — `src/diagnostic_demo/worker/model.py` 와 테스트

- `draft_json_schema()["$defs"]["EvidenceRef"]["properties"]["location"]` 에 Step 0 의 `pattern` 이 그대로 있어야 한다.
  `_strict` 의 `_UNSUPPORTED_KEYWORDS` 가 `pattern` 을 지우지 않는지 확인한다 (OpenAI structured outputs strict 모드는 문자열 `pattern` 을
  지원한다 — 공식 가이드 "Supported properties" 에 명시). 지워진다면 예외로 둔다. 코드 변경이 필요 없으면 테스트만 추가한다.
- `tests/diagnostic_demo/worker/test_model.py`: `location` 의 `pattern` 이 존재하고, 그 pattern 으로 `$.stages[1].status`·`lines:1-4` 는
  매치되고 `$.stages[1]` 는 매치되며(경로 자체는 유효), `$.items[*]`·`$`·`$.keys()` 는 매치되지 않는다. 기존
  `test_draft_json_schema_is_openai_strict_compatible` 이 계속 통과한다.

### 2. 프롬프트 — `src/diagnostic_demo/worker/prompt.py`

- `PROMPT_VERSION = "diag-prompt-v2"`.
- "인용 규칙" 을 새 문법에 맞게 고친다:
  - JSON 자료: `$.a.b` 객체 경로에 `[N]` 배열 인덱스(0부터) 를 쓸 수 있다 (예시는 문법 예시로만: `$.a[0].b`). 와일드카드·필터·`$` 단독은 안 된다.
  - 텍스트 자료: 도구가 줄 번호와 총 줄 수를 함께 돌려주므로, `lines:N-M` 은 도구가 보여준 번호를 그대로 쓰고 M 은 총 줄 수를 넘지 않는다.
  - 문서 `$.machine.` 규칙과 "읽지 않은 근거 인용 금지" 는 유지한다.
- "결론 규칙" 은 유지하되, 도구 결과 `ok=false`(not_found·access_denied·unavailable) 를 받은 자료가 진단에 필요하면 `needs_information` 으로
  두라는 문장을 명시적으로 한 줄 추가한다. 이는 기존 규칙의 재서술이며 정답(경로·코드·시각) 을 넣는 것이 아니다.
- 프롬프트에 fixture 의 실제 값(`$.items`, `$.data.records`, `MISSING_RECORDS_FIELD`, `stages`, 날짜 등) 을 쓰지 않는다.
  `test_system_prompt_has_no_fixed_answer` 가 확인한다 — 필요하면 그 테스트의 금지 목록에 `stages`·`MISSING_RECORDS_FIELD` 를 추가한다.

### 3. 이어지는 참조 갱신

- `PROMPT_VERSION` 문자열을 검사하는 테스트(`tests/diagnostic_demo/worker/test_prompt.py::test_prompt_version` 등) 와
  `docs/CONTRACT.md` 의 `"prompt_version": "diag-prompt-v1"` 예시를 `diag-prompt-v2` 로 맞춘다 (`grep -rn "diag-prompt-v1" src tests scripts docs`).
  `docs/DIAG_EVAL.md` 는 과거 기록이므로 고치지 않는다.
- `src/diagnostic_demo/worker/fake_script.py` 의 대본은 그대로 유효해야 한다 (`tests/diagnostic_demo/worker/test_fake_script.py` 통과).

### 테스트 (먼저 작성)

- `tests/diagnostic_demo/worker/test_prompt.py`: 버전 `diag-prompt-v2`; 인용 규칙에 배열 인덱스와 총 줄 수 언급; 결론 규칙에 `ok` 가 false 인 자료 → `needs_information` 언급;
  고정 답변 없음.
- `tests/diagnostic_demo/worker/test_model.py`: 위 1번.

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
3. 결과에 따라 `phases/1-diag-fix/index.json`의 해당 step을 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"` (프롬프트 버전, 바뀐 규칙 문장, 스키마 pattern 확인 결과)
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- 유료 모델 호출을 하지 마라. 이유: 실호출 재평가는 Step 3 이며 예산·키 확인이 거기 있다. 이 step 은 `FakeModelClient` 와 스키마 검사만으로 끝난다.
- 프롬프트에 데모 정답(경로 이름·오류 코드·문서 ID·적용 시각·기준 실행 ID) 을 넣지 마라. 이유: PRD "고정된 답변 재생으로 대체하지 않는다".
- 사례를 통과시키려고 특정 사례의 조건("응답 본문이 없으면 needs_information") 처럼 fixture 구조를 전제한 문장을 쓰지 마라. 이유: 위와 같다. 규칙은 일반 형태(읽지 못한 자료가 필요하면 보류) 로만 적는다.
- `src/workflow/contracts/v1.py`·`src/diagnostic_demo/tools/` 를 고치지 마라. 이유: Step 0·1 의 범위다. 어긋난 점을 찾으면 summary 에 적고 멈춘다.
- 기존 테스트를 깨뜨리지 마라.
