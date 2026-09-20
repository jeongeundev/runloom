# Step 1: usage-on-invalid-draft

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/docs/ARCHITECTURE.md` — "모델 호출 예산" (호출 수·토큰 상한, 사용 토큰 기록)
- `/docs/adr/` (하위 파일 전부)
- `/docs/GLOSSARY.md`
- `/docs/DIAG_EVAL.md` — Step 3 summary 가 적은 "발견한 제품 코드 결함": 계약 거부 턴의 호출·토큰이 사용량에 빠짐
- `/src/diagnostic_demo/worker/model.py` — `DraftInvalid`, `OpenAIModelClient._turn` (usage 를 읽은 뒤 `DraftInvalid` 를 던지는 지점)
- `/src/diagnostic_demo/worker/loop.py` — `run_diagnosis.call_model` (`turn_fn()` 이 돌아온 뒤에만 `usage` 를 더한다)
- `/src/diagnostic_demo/worker/runner.py` — `DraftInvalid` 를 받아 `failed`(code `model_output_invalid`) 로 기록하는 곳과 usage 기록
- `/scripts/diag_eval.py` — `run_case` 의 `usage = Usage()` 와 `DraftInvalid` 처리, 비용 계산
- `/tests/diagnostic_demo/worker/test_loop.py`, `test_model.py`, `test_runner.py`, `/scripts/test_diag_eval.py`

이전 step에서 만들어진 코드를 꼼꼼히 읽고, 설계 의도를 이해한 뒤 작업하라.

## 배경

모델의 최종 출력이 `DiagnosisDraft` 형식이 아니면 `OpenAIModelClient._turn` 이 `DraftInvalid` 를 던지는데, 그 턴의 `input_tokens`·`output_tokens` 는
이미 응답에 있음에도 `loop.call_model` 이 `turn_fn()` 반환 뒤에만 `usage` 를 더하므로 그 턴의 호출 1회와 토큰이 사용량·비용·예산 판정에서 빠진다.
재평가에서 계약 거부 1건당 한 턴 비용이 표에서 누락됐다. 다음 step 의 모델 비교에서 비용을 정확히 적기 위해 고친다.

## 작업

### `src/diagnostic_demo/worker/model.py`

- `DraftInvalid` 가 그 턴의 사용량을 함께 갖도록 한다. 예: `DraftInvalid(raw, message, *, input_tokens: int = 0, output_tokens: int = 0)`.
  `_turn` 에서 던질 때 응답 usage 값을 넣는다. `loop.run_diagnosis` 가 도구도 초안도 없을 때 던지는 `DraftInvalid("", …)` 는 0 으로 둔다.

### `src/diagnostic_demo/worker/loop.py`

- `call_model` 이 `DraftInvalid` 를 받으면 `usage.calls += 1` 과 그 예외의 토큰을 `usage` 에 더한 뒤 그대로 다시 던진다. 상한 초과 판정은 하지 않는다
  (이미 실패한 실행이다). 상한 검사 순서(호출 전 `max_calls`·timeout 확인) 는 바꾸지 않는다.
- `usage` 를 호출자가 넘긴 객체에 누적하는 현재 방식이 유지돼야 한다 — `runner.py`·`diag_eval.py` 가 예외 뒤에도 그 객체를 읽는다.

### `src/diagnostic_demo/worker/runner.py`, `scripts/diag_eval.py`

- 둘 다 `run_diagnosis(..., usage=usage)` 로 자기 `Usage` 객체를 넘기고 예외 뒤에 그 값을 기록하는지 확인한다. 넘기지 않는 곳이 있으면 넘기게 고친다.
  기록 형식·컬럼은 바꾸지 않는다.

### 테스트 (먼저 작성)

- `tests/diagnostic_demo/worker/test_model.py`: 형식 위반 출력에서 발생한 `DraftInvalid` 가 응답 usage 의 토큰을 갖는다.
- `tests/diagnostic_demo/worker/test_loop.py`: `FakeModelClient` 대본에서 마지막 턴이 `DraftInvalid` 를 던지도록 하고(대본 항목 대신 예외를 내는 가짜 클라이언트를 테스트 안에서 만든다),
  `run_diagnosis` 가 `DraftInvalid` 를 다시 던지되 넘긴 `usage` 의 `calls`·토큰에 그 턴이 포함된다.
- `tests/diagnostic_demo/worker/test_runner.py` 또는 `scripts/test_diag_eval.py`: `model_output_invalid` 로 끝난 실행의 기록된 usage 가 0 이 아니다.

## Acceptance Criteria

```bash
python3 -m pytest tests/diagnostic_demo scripts/test_diag_eval.py -q
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
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"` (바뀐 시그니처)
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- 유료 모델 호출을 하지 마라. 이유: 실호출은 Step 2 다. 이 step 은 가짜 클라이언트로 끝난다.
- `DraftInvalid` 를 잡아서 모델에 두 번째 기회를 주지 마라. 이유: loop.py 의 설계("초안이 잘못돼도 다시 묻지 않고 판정은 검증기가 한다") 를 유지한다. 사용량만 더하고 그대로 던진다.
- 예산 상한 값·기록 컬럼·이벤트 형식을 바꾸지 마라. 이유: 계약 v1 과 ARCHITECTURE 의 상한 표는 이 step 의 범위가 아니다.
- 프롬프트·도구·계약을 고치지 마라.
- 기존 테스트를 깨뜨리지 마라.
