# Step 17: diag-live-eval

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/docs/adr/0003-diagnosis-model-openai-gpt41-mini.md` — 확정 조건 (API 키, 계정 API 사용 가능, 예산 상한). 확인 전 유료 호출 금지
- `/docs/ARCHITECTURE.md` — "진단 모델과 평가 기준"(5사례 × 3회, 통과 기준), "모델 호출 예산"
- `/docs/PRD.md` — "도구·인계 수용 기준" 표
- `/src/diagnostic_demo/` (Step 9·10) — `FixtureStore(removed=, replaced=, unavailable=)`, `run_diagnosis`, `OpenAIModelClient`, `assemble_result`
- `/src/workflow/domain/verification.py` (Step 3)

## 작업

**시작 전 확인** — 다음 중 하나라도 아니면 즉시 `blocked` 로 기록하고 멈춘다 (유료 호출 0회):

- `OPENAI_API_KEY` 환경변수가 있다.
- `DIAG_PRICE_INPUT_PER_M`, `DIAG_PRICE_OUTPUT_PER_M` 가 있다 (사용자가 공식 가격 페이지에서 확인해 넣는다).
- `DIAG_EVAL_BUDGET_USD` 가 있다 (이 평가에 쓸 상한. 기본 없음).

`blocked_reason` 예: "OPENAI_API_KEY 미설정. ADR-0003 확정 조건: 키·계정 API 사용 가능 여부·단가·평가 예산을 사용자가 확인해 환경변수로 넣은 뒤 재실행".

### `scripts/diag_eval.py`

```python
CASES = {
  "normal":            dict(),                                                          # 기대: ready_for_handoff, 검증기 passed
  "missing_response":  dict(removed={("response-after", "1")}),                        # 기대: needs_information
  "missing_change_doc": dict(removed={("upstream-response-change", "1")}),             # 기대: needs_information
  "effective_conflict": dict(replaced={("upstream-response-change","1"): <effective_at 를 2026-09-21 로 바꾼 바이트>}),   # 기대: needs_information(evidence_conflict) 또는 검증기 failed — 어느 쪽이든 인계 없음
  "http_error_input":  dict(replaced={("run-daily-0920-0900","1"): <http_status 503, stages fetch failed 로 바꾼 바이트>, ("log-daily-0920","1"): <fetch 실패 로그>}),   # 기대: needs_information(unsupported_diagnosis) — response_path_changed 를 재생하지 않음
}
def run_case(name, repeat=3, *, model, settings) -> list[CaseResult]   # CaseResult: outcome, verdict, calls, input_tokens, output_tokens, seconds, estimated_usd, error
def main(argv=None)   # --cases all|이름 --repeat 3 --out docs/DIAG_EVAL.md. 매 실행 전 누적 비용이 DIAG_EVAL_BUDGET_USD 를 넘으면 중단
```

각 실행은 진단 서비스의 실제 경로(`run_diagnosis` → `assemble_result` → 중앙 `verify_diagnosis`)를 프로세스 안에서 직접 호출한다 (HTTP 없이). 모델은 `OpenAIModelClient(model_id=DIAG_MODEL_ID)`.

### 통과 기준 (ARCHITECTURE)

- `normal` 3/3: `ready_for_handoff` 이고 검증기 `passed`.
- 나머지 4사례 × 3회 = 12회 중 "잘못된 수정 착수"(= `ready_for_handoff` 이면서 검증기 `passed`) 가 **0회**.
- 기록: 사례별 outcome·verdict·호출 수·토큰·시간·비용 표, 실패 시 어떤 check 가 실패했는지, 총비용.

### `docs/DIAG_EVAL.md`

결과 표와 함께 ADR-0003 확정 여부 판단을 적는다: 통과면 "확정 조건 충족 — ADR-0003 을 확정으로 갱신할 것을 제안", 실패면 도구 반환·계약·프롬프트 중 어디가 문제인지 분리한 결과와 모델 교체 필요 여부. **ADR 파일 자체는 이 step 이 고치지 않는다** (사용자 확정).

### 테스트 — `scripts/test_diag_eval.py`

네트워크 없이: `CASES` 의 `replaced` 바이트가 유효한 JSON 이고 의도한 필드가 바뀌었는지, `run_case` 가 `FakeModelClient` 로도 돌아가는지(대본 주입), 예산 초과 시 중단하는지, 결과 표 렌더.

## Acceptance Criteria

```bash
python3 -m pytest scripts/test_diag_eval.py -q
python3 -m pytest -q
python3 -m ruff check .
# 실호출 (환경변수 3개가 있을 때만):
python3 scripts/diag_eval.py --cases all --repeat 3 --out docs/DIAG_EVAL.md
```

## 검증 절차

1. 시작 전 확인 3개. 하나라도 없으면 `blocked`.
2. 있으면 pytest 먼저, 그 다음 실호출. 총비용을 summary 에 적는다.
3. `phases/0-mvp/index.json` 의 step 17 을 업데이트한다.

## 금지사항

- 키가 없을 때 fake 로 대신 돌려 "통과" 라고 쓰지 마라. 이유: 이 step 의 목적은 실제 모델 평가다.
- 실패한 사례를 통과시키려고 정답을 프롬프트에 넣지 마라. 이유: 원칙.
- 예산을 넘겨 계속 돌리지 마라.
- 기존 테스트를 깨뜨리지 마라.
