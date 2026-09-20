# Step 2: model-compare

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/docs/adr/0003-diagnosis-model-openai-gpt41-mini.md` — 확정 조건과 "확인에 실패하면 다른 제공자를 새 ADR 로 결정" 규칙
- `/docs/ARCHITECTURE.md` — "진단 모델과 평가 기준"(통과 기준, "실행 중 다른 모델로 조용히 대체하지 않으며 결과에 모델 ID 를 기록"), "모델 호출 예산"
- `/docs/PRD.md` — "도구·인계 수용 기준" 표
- `/docs/DIAG_EVAL.md` (프롬프트 v2·mini) 와 `/docs/DIAG_EVAL_2026-09-20_prompt-v1.md` — 이전 두 평가. 비교 기준선
- `/docs/README.md` — 문서 색인
- `/scripts/diag_eval.py`, `/scripts/test_diag_eval.py` — 평가 하네스. `DIAG_MODEL_ID`·`DIAG_PRICE_*` 를 env 에서 읽는다
- `/src/diagnostic_demo/settings.py` — `DIAG_MODEL_ID` 기본값, `DIAG_PRICE_INPUT_PER_M`·`DIAG_PRICE_OUTPUT_PER_M`
- `/phases/2-model-compare/index.json` 의 Step 0·1 `summary`

이전 step에서 만들어진 코드를 꼼꼼히 읽고, 설계 의도를 이해한 뒤 작업하라.

## 배경

gpt-4.1-mini 는 프롬프트 v1·v2 세 번의 평가에서 `normal` 3/3 을 넘지 못했고, 남은 실패는 모델이 읽은 사실과 어긋나는 결론을 내는 행동이었다.
사용자가 같은 제공자의 상위 모델 `gpt-4.1` 과 비교하기로 결정했다. 층을 섞지 않기 위해 **같은 하네스·같은 프롬프트(v3)·같은 도구 계약(tools-v2)** 으로
두 모델을 각각 5사례 × 3회 돌린다. mini 도 v3 로 다시 돌리는 이유는 v2 결과와 v3 결과 사이에 프롬프트 차이가 끼지 않게 하기 위해서다.

사용자가 확인한 값 (2026-09-20, 공식 가격·모델 문서):

| 모델 | 스냅샷 | 입력 US$/1M | 출력 US$/1M |
|---|---|---|---|
| gpt-4.1-mini | `gpt-4.1-mini-2025-04-14` | 0.40 | 1.60 |
| gpt-4.1 | `gpt-4.1-2025-04-14` | 2.00 | 8.00 |

## 작업

**시작 전 확인** — 하나라도 아니면 즉시 `blocked` 로 기록하고 멈춘다 (유료 호출 0회):

- `OPENAI_API_KEY`, `DIAG_PRICE_INPUT_PER_M`, `DIAG_PRICE_OUTPUT_PER_M`, `DIAG_EVAL_BUDGET_USD` 환경변수가 모두 있다.
- `DIAG_PRICE_INPUT_PER_M == 0.40`, `DIAG_PRICE_OUTPUT_PER_M == 1.60` (mini 단가). 다르면 blocked — 어느 모델 단가인지 알 수 없다.
- `DIAG_EVAL_BUDGET_USD >= 1` (gpt-4.1 15회 예상 US$0.5 안팎).

### 1. 이전 보고서 보존

- `git mv docs/DIAG_EVAL.md docs/DIAG_EVAL_2026-09-20_prompt-v2.md` (내용 불변).

### 2. 사전 점검 (네트워크 없음)

```bash
python3 -m pytest scripts/test_diag_eval.py -q
python3 -m pytest -q
python3 -m ruff check .
```

### 3. 실호출 — 두 모델, 각 1회

```bash
# (a) mini, 프롬프트 v3 — env 의 mini 단가 그대로
DIAG_MODEL_ID=gpt-4.1-mini-2025-04-14 \
  python3 scripts/diag_eval.py --cases all --repeat 3 --out docs/DIAG_EVAL_prompt-v3_gpt-4.1-mini.md

# (b) gpt-4.1, 프롬프트 v3 — 단가는 이 모델 값으로 명시
DIAG_MODEL_ID=gpt-4.1-2025-04-14 DIAG_PRICE_INPUT_PER_M=2.00 DIAG_PRICE_OUTPUT_PER_M=8.00 \
  python3 scripts/diag_eval.py --cases all --repeat 3 --out docs/DIAG_EVAL_prompt-v3_gpt-4.1.md
```

- 각각 1회만 실행한다. 스크립트가 예산 초과·오류로 멈추면 그 상태를 기록한다. 재실행은 스크립트 결함을 고친 경우에만 1회 더 허용하며 비용을 합산해 적는다.
- 두 보고서의 "조건" 표에 `model_id` 가 각각 맞게 찍혔는지 확인한다 (provenance 에도 같은 값).
- 프롬프트·도구·계약·fixture·사례 정의는 고치지 않는다. 결과가 나쁘더라도 그렇다.

### 4. `docs/DIAG_EVAL.md` — 종합 (새로 작성)

두 스크립트 산출물 위에 사람이 읽을 종합 보고서를 `docs/DIAG_EVAL.md` 로 새로 쓴다. 담을 것:

- **조건 표**: 네 번의 평가(v1 mini 2차 · v2 mini · v3 mini · v3 gpt-4.1) 의 프롬프트·도구 계약·모델·비용. 각 보고서 링크.
- **모델 비교표** (v3 mini vs v3 gpt-4.1, 사례 × 회차): outcome · 검증기 · 실패 check · 호출 · 토큰 · 비용.
- **지표 비교**: `normal` 통과 n/3, 잘못된 수정 착수 n/12, 의도한 사유(응답 누락·문서 누락·시각 충돌·503) 로 `needs_information` 을 낸 횟수 n/12,
  읽지 않은 근거 인용, 부재 주장 인용, `list_runs.before` 연도 오기, 계약 거부, 총비용. v2 mini 도 열로 같이 둔다.
- **통과 기준 판정** 모델별.
- **ADR-0003 판단**: gpt-4.1 이 통과면 "ADR-0003 을 gpt-4.1 로 갱신(또는 ADR-0007 로 대체) 할 것을 제안 — 진단 1회 비용 US$x, 하루 60회 상한 기준 월 예상 US$y"
  를 계산해 적는다. 둘 다 미달이면 무엇이 남았는지와 다음 선택지(추론 모델 평가 / 프롬프트 재설계 / 검증기 결과를 그대로 `확인 필요` 로 쓰는 데모 운영) 를
  장단점과 함께 적는다. **ADR 파일은 고치지 않는다** (사용자 확정).
- `docs/README.md` 색인: DIAG_EVAL 행을 종합으로 갱신하고 네 보고서 행을 둔다.

## Acceptance Criteria

```bash
python3 -m pytest scripts/test_diag_eval.py -q
python3 -m pytest -q
python3 -m ruff check .
# 실호출 (환경변수가 있을 때만, 각 1회):
DIAG_MODEL_ID=gpt-4.1-mini-2025-04-14 python3 scripts/diag_eval.py --cases all --repeat 3 --out docs/DIAG_EVAL_prompt-v3_gpt-4.1-mini.md
DIAG_MODEL_ID=gpt-4.1-2025-04-14 DIAG_PRICE_INPUT_PER_M=2.00 DIAG_PRICE_OUTPUT_PER_M=8.00 python3 scripts/diag_eval.py --cases all --repeat 3 --out docs/DIAG_EVAL_prompt-v3_gpt-4.1.md
test -f docs/DIAG_EVAL_2026-09-20_prompt-v2.md && grep -q "gpt-4.1-2025-04-14" docs/DIAG_EVAL_prompt-v3_gpt-4.1.md && grep -q "gpt-4.1-mini-2025-04-14" docs/DIAG_EVAL_prompt-v3_gpt-4.1-mini.md && grep -q "diag-prompt-v3" docs/DIAG_EVAL.md
```

## 검증 절차

1. 시작 전 확인. 하나라도 어긋나면 `blocked`.
2. 보존 → 사전 점검 → (a) → (b) → 종합 작성 순서로 진행한다. 두 모델의 `normal` n/3·잘못된 착수 n/12·의도한 보류 n/12·비용을 summary 에 적는다.
3. 아키텍처 체크리스트를 확인한다:
   - AGENTS.md CRITICAL 규칙을 위반하지 않았는가? (`OPENAI_API_KEY` 값이 보고서·로그·커밋에 없다 — `git diff --cached | grep -c "sk-"` 가 0)
   - GLOSSARY.md 용어를 그대로 썼는가?
4. 결과에 따라 `phases/2-model-compare/index.json`의 해당 step을 업데이트한다:
   - 성공 (두 평가가 끝나고 종합이 작성됨 — 통과 기준 미달이어도 평가 자체는 성공이다) → `"status": "completed"`, `"summary": "결과 요약"`
   - 스크립트가 3회 시도해도 돌지 않음 → `"status": "error"`, `"error_message": "구체적 에러 내용"`
   - 환경변수 누락·단가 불일치·예산 부족 → `"status": "blocked"`, `"blocked_reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- 키가 없을 때 `DIAG_MODEL=fake` 로 대신 돌려 "통과" 라고 쓰지 마라. 이유: 이 step 의 목적은 실제 모델 비교다.
- 위 두 모델 외의 모델 ID 로 돌리지 마라. 이유: 사용자가 gpt-4.1 을 골랐고 다른 모델은 단가·예산 확인이 없다.
- 실패한 사례를 통과시키려고 프롬프트·도구 설명·fixture·사례 정의를 고치거나 정답을 넣지 마라. 이유: 원칙.
- 한 모델의 결과를 보고 다른 모델의 실행을 건너뛰지 마라. 이유: 비교가 목적이며 같은 조건의 두 표가 있어야 한다.
- 예산을 넘겨 계속 돌리지 마라.
- `docs/adr/` 를 고치지 마라. 이유: 사용자 확정 사항. 제안만 종합 보고서에 적는다.
- 기존 테스트를 깨뜨리지 마라.
