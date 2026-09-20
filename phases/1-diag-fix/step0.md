# Step 0: location-grammar

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/docs/ARCHITECTURE.md` — "진단 결과와 근거" 절(location 문법), "진단 모델과 평가 기준"
- `/docs/adr/` (하위 파일 전부)
- `/docs/GLOSSARY.md`
- `/docs/DIAG_EVAL.md` — "관찰" 3번과 "분리 분석" 표. 이 phase 가 왜 필요한지의 근거
- `/src/workflow/contracts/v1.py` — `_OBJECT_PATH`, `_LINE_RANGE`, `_validate_location`, `Location`, `Sha256`(`Field(pattern=)` 관용구)
- `/src/workflow/domain/evidence_location.py` — `ObjectPath`, `LineRange`, `parse_location`, `resolve_location`
- `/src/workflow/domain/verification.py` — `_check_locations_resolve`, `is_list_at` (resolve_location 의 사용처)
- `/tests/workflow/contracts/test_v1.py` (`test_rejects_bad_location`, `test_accepts_good_location`)
- `/tests/workflow/domain/test_evidence_location.py`
- `/src/diagnostic_demo/fixtures/evidence/run-daily-0920-0900/1.json` — `stages` 가 배열인 실행 기록

이전 step에서 만들어진 코드를 꼼꼼히 읽고, 설계 의도를 이해한 뒤 작업하라.

## 배경

진단 모델 실호출 평가(`docs/DIAG_EVAL.md`)에서 30회 중 12회가 계약의 `location` 검증에 거부됐다. 모델은 실행 기록의
`stages` 배열을 `$.stages[1].error_code` 처럼 인용하는데 계약 v1 이 배열 인덱스를 받지 않는다. 또 `Location` 의 문법이
`AfterValidator` 에만 있어 모델에 보내는 JSON Schema 에는 `location: string` 만 나타나므로 API 가 생성 시점에 문법을 강제할
수 없다. 이 step 은 그 두 가지를 계약·도메인 층에서 고친다. 정답을 주입하는 것이 아니라 문법을 넓히고 스키마에 드러내는 것이다.

## 작업

### 1. `src/workflow/contracts/v1.py` — 배열 인덱스 허용 + 스키마 pattern

- 객체 경로에 `[N]` 배열 인덱스(N 은 0 이상 정수, 앞자리 0 없음) 를 허용한다. `$.stages[1].status`, `$.data.records[0].team`,
  `$.items[0]` 이 유효하다. 와일드카드(`[*]`)·필터·`$` 단독·`$.keys()` 는 여전히 거부한다.
- 문법 정규식을 모듈 상수 하나(예: `LOCATION_PATTERN: str`) 로 두고, `Location` 을
  `Annotated[str, Field(pattern=LOCATION_PATTERN), AfterValidator(_validate_location)]` 형태로 바꿔
  JSON Schema 에 `pattern` 이 나타나게 한다 (`Sha256`·`CommitSha` 와 같은 관용구). `AfterValidator` 는 `lines:N-M` 의 M ≥ N 검사를
  계속 맡는다.
- 정규식은 Pydantic v2 가 쓰는 Rust `regex` 크레이트와 Python `re` 둘 다에서 동작해야 한다 (lookaround·backreference 금지).

### 2. `src/workflow/domain/evidence_location.py` — 같은 문법으로 해석

- `ObjectPath.keys` 가 문자열 키와 정수 인덱스를 섞어 담도록 바꾼다 (예: `tuple[str | int, ...]`).
  `"$.stages[1].status"` → `("stages", 1, "status")`.
- `_resolve_object_path` 는 정수 인덱스에서는 `list` 이고 범위 안일 때만 진행하고, 문자열 키에서는 `dict` 이고 키가 있을 때만 진행한다.
  자료형 불일치·범위 밖은 지금처럼 `None`.
- 문법 정규식은 `contracts/v1.py` 의 것과 반드시 같아야 한다. 도메인이 contracts 를 import 해도 된다면 상수를 가져다 쓰고,
  그렇지 않으면 두 곳의 정규식이 같은지 확인하는 테스트를 둔다 (`tests/workflow/domain/test_evidence_location.py`).
- 모듈 docstring 의 "배열 인덱스·와일드카드·필터는 v1 에 없다" 를 실제 문법에 맞게 고친다.

### 3. 문서

- `/docs/ARCHITECTURE.md` "진단 결과와 근거" 절의 location 설명에 배열 인덱스 `[N]`(0부터) 을 추가한다. 와일드카드·필터 미지원 문장은 유지한다.
- `/docs/CONTRACT.md` 에 location 문법 설명이 있으면 같은 내용으로 맞춘다. 예시 JSON 은 바꾸지 않는다.

### 테스트 (먼저 작성)

- `tests/workflow/contracts/test_v1.py`: `$.stages[1].status`·`$.items[0]` 수용, `$.items[*]`·`$.a[01]`·`$.a[-1]`·`$` 거부,
  `EvidenceRef.model_json_schema()["properties"]["location"]` 에 `pattern` 이 있고 그 pattern 이 수용 예를 통과·거부 예를 거절한다.
- `tests/workflow/domain/test_evidence_location.py`: `parse_location("$.stages[1].status")` 결과, 실행 기록 fixture 바이트로
  `resolve_location(..., "$.stages[1].error_code")` 가 `Resolved("MISSING_RECORDS_FIELD")`, 범위 밖 `[3]` 은 `None`,
  dict 에 인덱스·list 에 키는 `None`.
- `tests/workflow/domain/test_verification.py`: `locations_resolve` 가 `$.stages[1].status` 인용을 통과시키는 사례 1건.

## Acceptance Criteria

```bash
python3 -m pytest tests/workflow/contracts tests/workflow/domain -q
python3 -m pytest -q
python3 -m ruff check .
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - ARCHITECTURE.md 디렉토리 구조를 따르는가?
   - ARCHITECTURE.md 외부 의존 표에 없는 외부 서비스를 끌어들이지 않았는가?
   - ADR 기술 스택을 벗어나지 않았는가?
   - AGENTS.md CRITICAL 규칙을 위반하지 않았는가? (`domain/` 은 FastAPI·sqlite3·HTTPX·subprocess·Git 을 import 하지 않는다)
   - GLOSSARY.md 용어를 그대로 썼는가? 금지 표현을 쓰지 않았는가?
3. 결과에 따라 `phases/1-diag-fix/index.json`의 해당 step을 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"` (상수 이름, 정규식, 바뀐 시그니처를 적는다)
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- `src/diagnostic_demo/` 를 고치지 마라. 이유: 이 step 은 계약·도메인 층만 다룬다. 도구 반환은 Step 1, 프롬프트·스키마 확인은 Step 2 다.
- fixture 파일(`src/diagnostic_demo/fixtures/`) 을 고치지 마라. 이유: 문법을 넓혀 배열을 인용할 수 있게 하는 것이 목적이지 자료를 바꾸는 것이 아니다.
- 와일드카드·필터·음수 인덱스를 받지 마라. 이유: 검증기가 "원문에 실제로 존재하는 값 하나" 를 확인해야 하므로 위치는 결정적이어야 한다.
- 스키마의 `pattern` 과 `AfterValidator` 의 정규식을 따로 관리하지 마라. 이유: 두 곳이 어긋나면 API 는 통과시키고 서비스는 거부하는 상태로 돌아간다.
- 기존 테스트를 깨뜨리지 마라. 기존 수용 예(`$.items`, `$.data.records`, `lines:2-3`, `lines:4-4`) 는 그대로 유효해야 한다.
