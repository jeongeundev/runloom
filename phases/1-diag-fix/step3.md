# Step 3: diag-re-eval

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/docs/adr/0003-diagnosis-model-openai-gpt41-mini.md` — 확정 조건. 확인 전 유료 호출 금지
- `/docs/ARCHITECTURE.md` — "진단 모델과 평가 기준"(5사례 × 3회, 통과 기준), "모델 호출 예산"
- `/docs/PRD.md` — "도구·인계 수용 기준" 표
- `/docs/DIAG_EVAL.md` — 이전 평가(프롬프트 v1·도구 v1). 이번 결과와 비교할 기준선
- `/docs/README.md` — 문서 색인 (DIAG_EVAL 행)
- `/scripts/diag_eval.py`, `/scripts/test_diag_eval.py` — 평가 하네스. 이 step 은 이 스크립트를 실행하는 것이 주 작업이다
- `/phases/1-diag-fix/index.json` 의 Step 0·1·2 `summary` — 무엇이 바뀌었는지 (location 문법, 도구 반환, 프롬프트 v2)

이전 step에서 만들어진 코드를 꼼꼼히 읽고, 설계 의도를 이해한 뒤 작업하라.

## 작업

**시작 전 확인** — 다음 중 하나라도 아니면 즉시 `blocked` 로 기록하고 멈춘다 (유료 호출 0회):

- `OPENAI_API_KEY`, `DIAG_PRICE_INPUT_PER_M`, `DIAG_PRICE_OUTPUT_PER_M`, `DIAG_EVAL_BUDGET_USD` 환경변수가 모두 있다.
  (`python3 -c 'import os,sys; missing=[k for k in ("OPENAI_API_KEY","DIAG_PRICE_INPUT_PER_M","DIAG_PRICE_OUTPUT_PER_M","DIAG_EVAL_BUDGET_USD") if not os.environ.get(k)]; print(missing); sys.exit(bool(missing))'`)
- `DIAG_MODEL_ID` 가 비어 있거나 `gpt-4.1-mini-2025-04-14` 다. 다른 값이면 blocked — 모델 교체는 사용자 결정이며 단가도 달라진다.

### 1. 이전 보고서 보존

- `docs/DIAG_EVAL.md` 를 `docs/DIAG_EVAL_2026-09-20_prompt-v1.md` 로 `git mv` 한다 (내용 불변). 새 보고서가 같은 이름으로 생성된다.

### 2. 사전 점검 (네트워크 없음)

```bash
python3 -m pytest scripts/test_diag_eval.py -q
python3 -m pytest -q
python3 -m ruff check .
```

`scripts/diag_eval.py` 가 Step 0~2 의 변경(새 `TOOL_CONTRACT_VERSION`, `PROMPT_VERSION`, 배열 인덱스 location) 과 어긋나 테스트가 깨지면
스크립트·테스트만 고친다. 사례 정의(`CASES`)·기대 결과·통과 기준은 바꾸지 않는다.

### 3. 실호출

```bash
python3 scripts/diag_eval.py --cases all --repeat 3 --out docs/DIAG_EVAL.md
```

- 1회만 실행한다. 스크립트가 예산 초과·오류로 멈추면 그 상태를 그대로 기록한다. 재실행은 스크립트 결함(예: 새 도구 반환 형태를 처리 못 함) 을 고친 경우에만 1회 더 허용하며, 두 실행의 비용을 합산해 적는다.
- 프롬프트·도구·계약·fixture 는 이 step 에서 고치지 않는다. 결과가 나쁘더라도 그렇다.

### 4. `docs/DIAG_EVAL.md` 보완

스크립트가 쓴 표 아래에 다음을 적는다:

- **이전 평가와의 비교**: `docs/DIAG_EVAL_2026-09-20_prompt-v1.md` 의 2차 결과 대비 사례별 outcome·검증기·계약 거부 수·`locations_resolve` 실패 수·`needs_information` 횟수·읽지 않은 근거 인용 횟수·비용. 이번에 바뀐 것(location 문법·도구 줄 번호·프롬프트 v2) 을 조건 표에 적는다.
- **통과 기준 판정**: `normal` 3/3 (`ready_for_handoff` + 검증기 `passed`), 나머지 12회 중 잘못된 수정 착수 0회.
- **남은 문제의 분리**: 실패가 있으면 도구 반환·계약·프롬프트·모델 중 어디인지 이전 보고서와 같은 표 형식으로.
- **ADR-0003 확정 여부 판단**: 통과면 "확정 조건 충족 — ADR-0003 을 확정으로 갱신할 것을 제안". 미달이면 무엇이 남았는지와, 상위 모델(`gpt-4.1`) 비교 평가가 필요한지의 판단. **ADR 파일은 고치지 않는다** (사용자 확정).
- `docs/README.md` 색인의 DIAG_EVAL 행을 새 결과로 갱신하고 이전 보고서 행을 추가한다.

## Acceptance Criteria

```bash
python3 -m pytest scripts/test_diag_eval.py -q
python3 -m pytest -q
python3 -m ruff check .
# 실호출 (환경변수 4개가 있을 때만, 1회):
python3 scripts/diag_eval.py --cases all --repeat 3 --out docs/DIAG_EVAL.md
test -f docs/DIAG_EVAL_2026-09-20_prompt-v1.md && grep -q "diag-prompt-v2" docs/DIAG_EVAL.md
```

## 검증 절차

1. 시작 전 확인. 하나라도 없으면 `blocked`.
2. 사전 점검 → 실호출 → 보고서 보완 순서로 진행한다. 총비용(US$) 과 `normal` n/3·잘못된 수정 착수 n/12 를 summary 에 적는다.
3. 아키텍처 체크리스트를 확인한다:
   - AGENTS.md CRITICAL 규칙을 위반하지 않았는가? (`OPENAI_API_KEY` 값이 보고서·로그·커밋에 들어가지 않았는가 — `git diff --cached | grep -c "sk-"` 가 0)
   - GLOSSARY.md 용어를 그대로 썼는가?
4. 결과에 따라 `phases/1-diag-fix/index.json`의 해당 step을 업데이트한다:
   - 성공 (평가가 끝나고 보고서가 작성됨 — 통과 기준 미달이어도 평가 자체는 성공이다) → `"status": "completed"`, `"summary": "결과 요약 (normal n/3, 잘못된 착수 n/12, 비용, ADR 판단)"`
   - 스크립트가 3회 시도해도 돌지 않음 → `"status": "error"`, `"error_message": "구체적 에러 내용"`
   - 환경변수 누락·모델 ID 불일치 → `"status": "blocked"`, `"blocked_reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- 키가 없을 때 `DIAG_MODEL=fake` 로 대신 돌려 "통과" 라고 쓰지 마라. 이유: 이 step 의 목적은 실제 모델 재평가다.
- 실패한 사례를 통과시키려고 프롬프트·도구 설명·fixture 를 고치거나 정답을 넣지 마라. 이유: 원칙. 고칠 점은 보고서의 분리 분석에 적고 다음 phase 로 넘긴다.
- `DIAG_MODEL_ID` 를 바꿔 다른 모델로 돌리지 마라. 이유: 모델 교체는 사용자가 단가·예산을 확인하고 새 ADR 로 결정한다. 실행 중 조용한 대체 금지 (ARCHITECTURE).
- 예산(`DIAG_EVAL_BUDGET_USD`) 을 넘겨 계속 돌리지 마라.
- `docs/adr/0003-*.md` 를 고치지 마라. 이유: 사용자 확정 사항.
- 기존 테스트를 깨뜨리지 마라.
