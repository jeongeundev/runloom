# Step 1: tool-line-numbers

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/docs/ARCHITECTURE.md` — "진단 모델과 평가 기준"(도구 반환 → 모델), "진단 결과와 근거"(location 문법)
- `/docs/adr/` (하위 파일 전부)
- `/docs/GLOSSARY.md`
- `/docs/DIAG_EVAL.md` — "관찰" 3번, "분리 분석" 표의 "도구 반환" 행
- `/src/diagnostic_demo/tools/api.py` — `TOOL_SCHEMAS`(`read_evidence` 설명), `Tools.call` 의 반환 dict
- `/src/diagnostic_demo/tools/store.py` — `FixtureStore._read`, `raw_bytes`, `sha256_of` (첨부·해시의 원천. 건드리지 않는다)
- `/src/diagnostic_demo/tools/trace.py` — `TOOL_CONTRACT_VERSION`, `ToolTraceRecorder.record`
- `/src/diagnostic_demo/worker/loop.py` — `Tools.call` 결과가 모델에 어떻게 전달되는지
- `/src/workflow/domain/evidence_location.py` — `_resolve_line_range` 의 줄 세는 규칙 (Step 0 에서 수정됨)
- `/tests/diagnostic_demo/tools/test_api.py` (`test_read_evidence_text_call`, `test_schema_descriptions_state_facts_not_conclusions`)
- `/src/diagnostic_demo/fixtures/evidence/log-daily-0920/1.txt` — 4줄짜리 텍스트 자료

이전 step에서 만들어진 코드를 꼼꼼히 읽고, 설계 의도를 이해한 뒤 작업하라.

## 배경

평가에서 모델이 4줄짜리 로그에 `lines:2-6`, `lines:1-7` 처럼 원문 밖 범위를 인용해 `locations_resolve` 검사에 11회 걸렸다.
도구가 텍스트 자료를 줄 번호 없는 문자열 하나로 돌려주므로 모델이 줄을 직접 세어야 하기 때문이다. 이 step 은 모델에 보내는
텍스트 자료에 줄 번호를 붙인다. 첨부 원문·해시·조회 이력은 그대로다 (사실 자료를 바꾸는 것이 아니라 표시 형식만 바꾼다).

## 작업

### `src/diagnostic_demo/tools/api.py`

- `Tools.call` 이 돌려주는 dict 에서 `content_type == "text/plain"` 인 경우 `content` 를 줄 번호가 붙은 형태로 바꾼다.
  형태는 재량이나 다음을 만족해야 한다:
  - 줄 번호는 1부터, `workflow.domain.evidence_location._resolve_line_range` 와 같은 규칙으로 센다 (`\n` 으로 나누고,
    원문이 `\n` 으로 끝나면 마지막 빈 조각은 줄이 아니다). 그래야 모델이 본 번호와 검증기가 세는 번호가 같다.
  - 총 줄 수(`line_count`) 를 같이 준다. 모델이 `lines:N-M` 의 M 을 그 안에서 고를 수 있게.
  - 각 줄의 원문 텍스트는 바꾸지 않는다 (trim·정규화 금지).
- `read_evidence` 의 `description` 에 "텍스트 자료는 줄 번호가 붙은 줄 목록과 총 줄 수로 돌아오며 인용은 `lines:N-M` (1 ≤ N ≤ M ≤ 총 줄 수)" 를
  사실 서술로 추가한다. 진단 결론·힌트는 넣지 않는다 (`test_schema_descriptions_state_facts_not_conclusions` 가 확인한다).
- JSON 자료(`application/json`) 의 `content` 는 그대로 둔다.

### `src/diagnostic_demo/tools/trace.py`

- 도구가 모델에 돌려주는 형식이 바뀌었으므로 `TOOL_CONTRACT_VERSION` 을 `"tools-v2"` 로 올린다. ARCHITECTURE "결과에 모델 ID·프롬프트 버전·도구 계약 버전을 기록한다" 에 따라
  조회 이력·provenance 에 이 값이 남는다. `"tools-v1"` 을 문자열로 검사하는 테스트가 있으면 함께 고친다 (`grep -rn "tools-v1" tests/ scripts/ docs/`).

### 바꾸지 않는 것

- `FixtureStore._read`·`raw_bytes`·`sha256_of`: 첨부 조립(`worker/assemble.py`)과 조회 이력의 해시는 원문 바이트에서 나온다. 줄 번호는 모델용 표시일 뿐이다.
- `ToolTraceRecorder.record` 의 기록 내용 (`input`, `ok`, `returned`).

### 테스트 (먼저 작성)

- `tests/diagnostic_demo/tools/test_api.py`: `read_evidence` 텍스트 호출 결과가 줄 번호 1~4 와 `line_count == 4` 를 갖고 각 줄 텍스트가 원문과 같다;
  원문이 `\n` 으로 끝나도 빈 줄이 추가되지 않는다 (`FixtureStore(replaced=...)` 로 끝 개행 있는/없는 바이트 두 경우);
  JSON 호출 결과의 `content` 는 이전과 같다; 조회 이력의 `returned[].sha256` 이 원문 바이트의 sha256 과 같다 (줄 번호가 해시에 영향 없음);
  `read_evidence` 설명이 줄 번호·`lines:N-M` 을 언급한다.
- `tests/diagnostic_demo/tools/test_trace.py` 또는 `test_api.py`: `TOOL_CONTRACT_VERSION == "tools-v2"`.

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
   - AGENTS.md CRITICAL 규칙을 위반하지 않았는가? (`src/diagnostic_demo/` 는 중앙 DB 에 접근하지 않고 공개 계약만 공유한다)
   - GLOSSARY.md 용어를 그대로 썼는가? 금지 표현을 쓰지 않았는가?
3. 결과에 따라 `phases/1-diag-fix/index.json`의 해당 step을 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"` (모델에 보내는 텍스트 `content` 의 정확한 형태를 적는다 — Step 2 프롬프트가 참조한다)
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- `FixtureStore` 가 돌려주는 `ToolResult.content` 나 `raw_bytes` 를 바꾸지 마라. 이유: 첨부 원문·sha256·검증기 `attachments_loaded` 가 그 바이트를 기준으로 삼는다. 줄 번호가 첨부에 섞이면 해시가 어긋난다.
- fixture 파일에 줄 번호를 써 넣지 마라. 이유: 자료 자체를 바꾸는 것이며, 실제 운영 로그에는 줄 번호가 없다.
- `read_evidence` 설명에 정답(경로 이름·오류 코드·적용 시각) 이나 "이 줄을 인용하라" 류의 힌트를 넣지 마라. 이유: 원칙 "고정 답변 재생 금지".
- `src/diagnostic_demo/worker/prompt.py` 를 고치지 마라. 이유: 프롬프트 갱신은 Step 2 다.
- 기존 테스트를 깨뜨리지 마라. `test_read_evidence_text_call` 은 새 형태에 맞게 고치되, 그 외 도구 테스트는 그대로 통과해야 한다.
