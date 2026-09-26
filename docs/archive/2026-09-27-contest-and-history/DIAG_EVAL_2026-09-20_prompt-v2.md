# 진단 모델 실호출 평가

갱신일: 2026-09-20
상태: Step 17 `scripts/diag_eval.py` 가 생성한 기록. ARCHITECTURE "진단 모델과 평가 기준"의 5사례 × 3회를 실제 모델로 돌린 결과다. 결과가 좋게 보이도록 편집하지 않는다. ADR-0003 파일 자체는 이 평가가 고치지 않는다 (사용자 확정). phase `1-diag-fix` Step 3 의 재평가다 — Step 0(location 문법에 배열 인덱스), Step 1(텍스트 자료 줄 번호, `tools-v2`), Step 2(`diag-prompt-v2`) 를 적용한 뒤 같은 하네스로 한 번 돌렸다. 기준선은 [DIAG_EVAL_2026-09-20_prompt-v1.md](DIAG_EVAL_2026-09-20_prompt-v1.md)(프롬프트 v1·도구 v1, 2차 실행)이다. "실행 이력" 부터는 손으로 더했다(스크립트를 다시 돌리면 생성 절만 덮어쓴다).

## 조건

| 항목 | 값 |
|---|---|
| 모델 | `gpt-4.1-mini-2025-04-14` (Responses API, function calling + structured outputs strict) |
| 프롬프트·도구 계약 | `diag-prompt-v2` · `tools-v2` |
| 단가 (US$ / 1M 토큰) | 입력 0.4 · 출력 1.6 (`DIAG_PRICE_*`, 사용자가 공식 가격 페이지에서 확인) |
| 진단 1회 상한 | 호출 15회 · 입력 80,000 · 출력 8,000 토큰 · 300초 |
| 평가 예산 | US$2 (`DIAG_EVAL_BUDGET_USD`) — 매 실행 전 누적 비용 확인 |
| 실행 | 2026-09-20T15:48:51+09:00 ~ 2026-09-20T15:51:37+09:00 (KST) · 사례당 3회 · 총 15회 |
| 총 추정 비용 | US$0.0951 |
| 경로 | `run_diagnosis` → `assemble_result` → 중앙 `verify_diagnosis` 를 프로세스 안에서 직접 호출 (HTTP 없음). 자료 누락·교체는 `FixtureStore(removed=, replaced=)`, fixture 파일 불변 |

## 통과 기준과 판정

- normal 0/3: `ready_for_handoff` 이고 검증기 `passed` — 기준 3/3
- 잘못된 수정 착수 0/12: 나머지 4사례에서 `ready_for_handoff` 이면서 `passed` 인 실행 — 기준 0
- 전 사례 3회 완료: 예

**판정: 확정 보류.** 기준 미충족 실행:

- normal 1회차: outcome=needs_information 검증기=undecidable
- normal 2회차: outcome=ready_for_handoff 검증기=failed 실패 check `locations_resolve`
- normal 3회차: outcome=— 검증기=— 오류 `model_output_invalid: 초안이 DiagnosisDraft 형식이 아닙니다: 1개 오류 — diagnosis.baseline_run_id=''`

도구 반환·계약·프롬프트 중 어디가 문제인지의 분리 분석과 모델 교체 필요 여부는 아래 절에 적는다.

## 사례별 결과

| 사례 | 회차 | outcome | 검증기 | diagnosis / missing | 호출 | 입력 토큰 | 출력 토큰 | 초 | US$ | 오류 |
|---|---|---|---|---|---|---|---|---|---|---|
| `normal` | 1 | needs_information | undecidable | evidence_unavailable | 2 | 4,374 | 299 | 6.0 | 0.0022 | — |
| `normal` | 2 | ready_for_handoff | failed | response_path_changed | 6 | 16,967 | 1,010 | 14.7 | 0.0084 | — |
| `normal` | 3 | — | — | — | 2 | 4,610 | 210 | 11.2 | 0.0022 | model_output_invalid: 초안이 DiagnosisDraft 형식이 아닙니다: 1개 오류 — diagnosis.baseline_run_id='' |
| `missing_response` | 1 | ready_for_handoff | failed | response_path_changed | 7 | 20,951 | 1,043 | 13.6 | 0.0100 | — |
| `missing_response` | 2 | ready_for_handoff | failed | response_path_changed | 7 | 20,951 | 1,173 | 15.8 | 0.0103 | — |
| `missing_response` | 3 | needs_information | undecidable | evidence_unavailable | 2 | 4,374 | 362 | 4.8 | 0.0023 | — |
| `missing_change_doc` | 1 | needs_information | failed | evidence_unavailable | 2 | 4,374 | 353 | 5.5 | 0.0023 | — |
| `missing_change_doc` | 2 | ready_for_handoff | failed | response_path_changed | 6 | 17,542 | 1,150 | 14.0 | 0.0089 | — |
| `missing_change_doc` | 3 | ready_for_handoff | failed | response_path_changed | 6 | 16,710 | 981 | 12.3 | 0.0083 | — |
| `effective_conflict` | 1 | needs_information | undecidable | evidence_unavailable | 2 | 4,374 | 302 | 4.3 | 0.0022 | — |
| `effective_conflict` | 2 | ready_for_handoff | failed | response_path_changed | 5 | 14,041 | 890 | 13.5 | 0.0070 | — |
| `effective_conflict` | 3 | ready_for_handoff | failed | response_path_changed | 7 | 20,201 | 1,030 | 13.6 | 0.0097 | — |
| `http_error_input` | 1 | ready_for_handoff | failed | response_path_changed | 7 | 19,694 | 1,049 | 17.3 | 0.0096 | — |
| `http_error_input` | 2 | ready_for_handoff | failed | response_path_changed | 7 | 19,694 | 935 | 14.3 | 0.0094 | — |
| `http_error_input` | 3 | needs_information | failed | evidence_unavailable | 2 | 4,361 | 370 | 4.7 | 0.0023 | — |

기대 결과 (ARCHITECTURE·PRD 수용 기준):

- `normal`: `ready_for_handoff` + 검증기 `passed`
- `missing_response`: `needs_information` (실패 응답 본문 누락)
- `missing_change_doc`: `needs_information` (변경 안내 누락)
- `effective_conflict`: `needs_information`(evidence_conflict) 또는 검증기 `failed` — 인계 없음
- `http_error_input`: `needs_information`(unsupported_diagnosis) — response_path_changed 재생 없음

## 검증기가 실패로 표시한 check

| 사례 | 회차 | 검증기 | 실패 check |
|---|---|---|---|
| `normal` | 2 | failed | `locations_resolve` |
| `missing_response` | 1 | failed | `refs_in_attachments`, `locations_resolve` |
| `missing_response` | 2 | failed | `refs_in_attachments`, `locations_resolve` |
| `missing_change_doc` | 1 | failed | `locations_resolve` |
| `missing_change_doc` | 2 | failed | `refs_in_attachments`, `locations_resolve` |
| `missing_change_doc` | 3 | failed | `refs_in_attachments`, `locations_resolve` |
| `effective_conflict` | 2 | failed | `locations_resolve` |
| `effective_conflict` | 3 | failed | `change_effective_before_failure` |
| `http_error_input` | 1 | failed | `locations_resolve` |
| `http_error_input` | 2 | failed | `failed_run_http_ok_then_transform_failed`, `paths_differ_as_claimed` |
| `http_error_input` | 3 | failed | `refs_in_attachments`, `locations_resolve` |

## 도구 호출 순서 (조회 이력)

- `normal` 1회차 (`eval-normal-1-00291c53`): `get_run daily-0920-0900 ok` → `list_runs daily-report ok`
- `normal` 2회차 (`eval-normal-2-ef5b3e3e`): `get_run daily-0920-0900 ok` → `list_runs daily-report ok` → `get_run daily-0919-0900 ok` → `read_evidence response-before@1 ok` → `read_evidence response-after@1 ok` → `read_evidence log-daily-0920@1 ok` → `list_documents daily-report ok` → `read_evidence upstream-response-change@1 ok` → `read_evidence daily-report-contract@1 ok`
- `normal` 3회차 (`eval-normal-3-3e45cc4e`): `get_run daily-0920-0900 ok` → `list_runs daily-report ok` → `list_documents daily-report ok` → `read_evidence response-after@1 ok` → `read_evidence log-daily-0920@1 ok` → `read_evidence upstream-response-change@1 ok` → `read_evidence daily-report-contract@1 ok`
- `missing_response` 1회차 (`eval-missing_response-1-fa3d1a8e`): `get_run daily-0920-0900 ok` → `list_runs daily-report ok` → `get_run daily-0919-0900 ok` → `read_evidence response-after@1 not_found` → `read_evidence log-daily-0920@1 ok` → `read_evidence response-before@1 ok` → `read_evidence log-daily-0919@1 ok` → `list_documents daily-report ok` → `read_evidence upstream-response-change@1 ok` → `read_evidence daily-report-contract@1 ok`
- `missing_response` 2회차 (`eval-missing_response-2-0c86e778`): `get_run daily-0920-0900 ok` → `list_runs daily-report ok` → `get_run daily-0919-0900 ok` → `read_evidence response-before@1 ok` → `read_evidence response-after@1 not_found` → `read_evidence log-daily-0920@1 ok` → `read_evidence log-daily-0919@1 ok` → `list_documents daily-report ok` → `read_evidence upstream-response-change@1 ok` → `read_evidence daily-report-contract@1 ok`
- `missing_response` 3회차 (`eval-missing_response-3-10a0f316`): `get_run daily-0920-0900 ok` → `list_runs daily-report ok`
- `missing_change_doc` 1회차 (`eval-missing_change_doc-1-397b627d`): `get_run daily-0920-0900 ok` → `list_runs daily-report ok`
- `missing_change_doc` 2회차 (`eval-missing_change_doc-2-91e36ecb`): `get_run daily-0920-0900 ok` → `list_runs daily-report ok` → `list_documents daily-report ok` → `read_evidence upstream-response-change@1 not_found` → `read_evidence daily-report-contract@1 ok` → `read_evidence response-after@1 ok` → `read_evidence log-daily-0920@1 ok`
- `missing_change_doc` 3회차 (`eval-missing_change_doc-3-85533281`): `get_run daily-0920-0900 ok` → `list_runs daily-report ok` → `get_run daily-0919-0900 ok` → `read_evidence response-before@1 ok` → `read_evidence response-after@1 ok` → `read_evidence log-daily-0920@1 ok` → `list_documents daily-report ok` → `read_evidence upstream-response-change@1 not_found` → `read_evidence daily-report-contract@1 ok`
- `effective_conflict` 1회차 (`eval-effective_conflict-1-75b662e0`): `get_run daily-0920-0900 ok` → `list_runs daily-report ok`
- `effective_conflict` 2회차 (`eval-effective_conflict-2-c4fc1ad8`): `get_run daily-0920-0900 ok` → `list_runs daily-report ok` → `get_run daily-0919-0900 ok` → `read_evidence response-after@1 ok` → `read_evidence log-daily-0920@1 ok` → `list_documents daily-report ok` → `read_evidence upstream-response-change@1 ok` → `read_evidence daily-report-contract@1 ok`
- `effective_conflict` 3회차 (`eval-effective_conflict-3-9ba1592f`): `get_run daily-0920-0900 ok` → `list_runs daily-report ok` → `get_run daily-0919-0900 ok` → `read_evidence response-before@1 ok` → `read_evidence response-after@1 ok` → `read_evidence log-daily-0920@1 ok` → `list_documents daily-report ok` → `read_evidence upstream-response-change@1 ok` → `read_evidence daily-report-contract@1 ok`
- `http_error_input` 1회차 (`eval-http_error_input-1-26fd8424`): `get_run daily-0920-0900 ok` → `list_runs daily-report ok` → `get_run daily-0919-0900 ok` → `read_evidence response-before@1 ok` → `read_evidence log-daily-0920@1 ok` → `list_documents daily-report ok` → `read_evidence upstream-response-change@1 ok` → `read_evidence daily-report-contract@1 ok`
- `http_error_input` 2회차 (`eval-http_error_input-2-1d4691fa`): `get_run daily-0920-0900 ok` → `list_runs daily-report ok` → `get_run daily-0919-0900 ok` → `read_evidence response-before@1 ok` → `read_evidence log-daily-0920@1 ok` → `list_documents daily-report ok` → `read_evidence upstream-response-change@1 ok` → `read_evidence daily-report-contract@1 ok`
- `http_error_input` 3회차 (`eval-http_error_input-3-18939ef9`): `get_run daily-0920-0900 ok` → `list_runs daily-report ok`

## 실행 이력

이 step 의 실호출은 1회다. 프롬프트·도구·계약·fixture 는 실행 전후로 고치지 않았고, 스크립트 결함이 없어 재실행하지 않았다.

| 실행 | 명령 | 결과 | 비용 |
|---|---|---|---|
| 15:48~15:51 KST | `--cases all --repeat 3` | normal 0/3 · 잘못된 수정 착수 0/12 · 계약 거부 1/15 · `needs_information` 5/15 | US$0.0951 (표 기준) |

원본: `data/diag-eval/2026-09-20T154851+0900/` — `results.json`, 진단 서비스 DB·산출물(`diagnosis_result`·`tool_trace`·`evidence`), `invalid-drafts/`. `data/` 는 git 에 들어가지 않는다.

표의 총액은 실제보다 조금 적다. `worker/loop.py` 의 `call_model` 은 `turn_fn()` 이 돌아온 뒤에 `usage` 를 더하는데, 계약 거부는 `OpenAIModelClient._turn` 안에서 `DraftInvalid` 로 던져지므로 **마지막 턴의 호출 1회와 토큰이 `usage` 에 들어가지 않는다**. `normal` 3회차는 조회 이력이 7건(3턴)인데 호출 2·토큰 4,610/210 으로 기록됐다. 빠진 턴은 같은 궤적의 다른 실행 기준 입력 4~12k·출력 0.6~1k 토큰, 약 US$0.003~0.006 이며, 이 실행의 실제 총액은 약 US$0.10 이다. 이전 보고서의 계약 거부 12건(1차 7·2차 5)도 같은 방식으로 한 턴씩 빠져 있었다. 진단 서비스의 `db.record_usage` 도 같은 값을 쓰므로 총액 US$30 상한 추정이 계약 거부 1건당 한 턴만큼 적게 잡힌다. 제품 코드 결함이며 이 step 에서는 고치지 않는다(다음 phase 항목).

## 이전 평가와의 비교 — v1 2차 → 이번

### 바뀐 조건

| 항목 | 이전 (v1 2차) | 이번 |
|---|---|---|
| 프롬프트 | `diag-prompt-v1` | `diag-prompt-v2` — 인용 규칙에 배열 인덱스 `[N]` 허용·`$` 단독 등 금지 명시, `lines:N-M` 은 도구가 준 줄 번호를 그대로 쓰고 `line_count` 를 넘지 않음, 결론 규칙에 "ok 가 false 인 자료가 필요하면 `needs_information`" 한 줄 추가 |
| 도구 계약 | `tools-v1` — 텍스트 자료는 원문 문자열 | `tools-v2` — `text/plain` 은 `{line_count, lines: [{line, text}, …]}`. JSON 자료·조회 이력·첨부 sha256 은 불변 |
| 계약 v1 `Location` | 객체 경로(`$.a.b`) 또는 `lines:N-M`. 문법은 `AfterValidator` 라 strict 스키마에 없음 | `LOCATION_PATTERN` 이 배열 인덱스 `[N]` 을 허용하고 `Field(pattern=…)` 으로 strict 스키마의 `location` 에 `pattern` 이 드러남 |
| 모델·단가·상한·예산·fixture·사례 정의 | 같음 | 같음 |

### 지표

| 지표 | 이전 (v1 2차) | 이번 (v2) | 읽기 |
|---|---|---|---|
| `normal` 통과 (`ready_for_handoff` + `passed`) | 1/3 | **0/3** | 표본 3 에서 1 과 0 의 차이는 의미가 약하다. 실패 사유가 바뀐 것이 핵심(아래) |
| 잘못된 수정 착수 | 0/12 | 0/12 | 두 번 다 검증기가 막은 결과 |
| 계약 거부 (`model_output_invalid`) | 5 (전부 `location` 문법) | **1** (`diagnosis.baseline_run_id=''`, `location` 문법 0) | Step 0·2 의 목표 달성 |
| 배열 인덱스 인용 `$.stages[N]…` | 계약 거부 | 8건 인용, 전부 계약 통과·원문 해석 성공 | Step 0 의 목표 달성 |
| 실제 근거의 줄 범위 인용이 `line_count` 를 넘은 건 | 5 (`lines:2-5`·`2-6`·`1-6`·`1-7`) | **0** (계약 거부 초안 포함 12건 전부 안. ERROR 줄을 `lines:2-2` 로 짚음, 4줄 로그에 `lines:1-4`) | Step 1 의 목표 달성 |
| `locations_resolve` 실패 | 6 (줄 범위 초과 5, 부재 주장 인용 1, 미읽음 근거 파생 1 — 한 실행에 둘이 겹침) | 9 | 구성이 다르다 — 이번 9건은 미읽음·지어낸 근거 파생 5, 부재 주장 인용 2, 지어낸 경로 1, 중첩 오류 1 (관찰 4) |
| 읽지 않은 근거 인용 (`refs_in_attachments`) | 1 | **5** (실제 근거 4 + 지어낸 ID `-@-` 1) | 나빠짐 |
| `needs_information` | 0 | 5 | **전부 잘못된 이유**(`list_runs.before` 연도 오기 → "정상 실행 없음"). 의도한 사유(응답 누락·문서 누락·시각 충돌·503)로 보류한 실행 0 |
| 조사 순서 1~4 완주 (정상 실행 `get_run` 포함) | 15/15 | **8/15** | 연도 오기 7건이 정상 실행을 못 찾음 |
| 모델 호출 · 입력/출력 토큰 · 시간 | 93 · 245k/10.3k · 209초 | 70(+1 미집계) · 193k/11.2k · 166초 | 조기 종료 5건 때문에 줄었다 |
| 총 추정 비용 | US$0.1147 | US$0.0951 (실제 약 0.10) | |

### 사례별 outcome·검증기

| 사례 | 이전 1·2·3회차 | 이번 1·2·3회차 |
|---|---|---|
| `normal` | 거부 / ready·**passed** / ready·failed | needs·undecidable / ready·failed / 거부 |
| `missing_response` | 거부 / ready·failed / 거부 | ready·failed / ready·failed / needs·undecidable |
| `missing_change_doc` | ready·failed / 거부 / ready·failed | needs·failed / ready·failed / ready·failed |
| `effective_conflict` | ready·failed / ready·failed / ready·failed | needs·undecidable / ready·failed / ready·failed |
| `http_error_input` | ready·failed / 거부 / ready·failed | ready·failed / ready·failed / needs·failed |

## 관찰

1. **`list_runs` 의 `before` 에 연도를 잘못 쓴 실행 7/15 — 새로 나타난 문제.** `get_run daily-0920-0900` 이 `started_at: 2026-09-20T09:00:00+09:00` 을 돌려줬는데도 모델은 `before` 를 `2023-09-20T09:00:00+09:00`(4건) 또는 `2024-…`(2건) 로 썼다(조회 이력 확인; `normal` 3회차는 이력 산출물이 없으나 요약에 "정상 실행 기록이 없으므로" 라고 적혀 같은 경우로 본다). 도구는 정확히 빈 목록을 돌려줬고(2023 이전 실행 없음), 모델은 "직전 정상 실행이 없다"고 결론했다. 5건은 그 자리에서 `needs_information`(`evidence_unavailable`, `evidence_id` null)으로 멈췄고, 2건은 조사를 계속해 `baseline_run_id` 를 `""`(계약 거부) / `"N/A"`(`missing_change_doc` 2회차) 로 채운 `ready_for_handoff` 를 냈다. 이전 두 실행의 조회 이력 18건은 전부 `2026-…` 이었다. 프롬프트 v2·도구 v2 어디에도 시각 관련 변경은 없어, 표본 변동인지 프롬프트 길이·도구 설명 변화의 간접 효과인지 이 실행만으로 가를 수 없다.
2. **결론 규칙 미준수는 그대로다.** 조사를 끝까지 한 8건(정상 실행까지 읽은 실행) 전부 `ready_for_handoff` 였고, 그중 `normal` 이 아닌 7건에서 `needs_information` 은 0 이다. 모델은 자기 요약·claim 에 모순을 적고도 확정 진단을 냈다 — `effective_conflict` 2회차 요약 "2026-09-21부터 적용될 예정", 3회차 claim "2026-09-21 부터 적용되는 변경 사항"; `http_error_input` 1회차 claim "fetch 단계에서 HTTP 503 오류", 2회차 요약 "503 상태로 실패하여 데이터를 받아오지 못했습니다"; `missing_change_doc` 2회차 요약 "안내 문서가 적용된 이후 발생한 것으로 보인다"(그 문서는 `not_found`). v2 에 추가한 "ok 가 false 인 자료가 필요하면 `needs_information`" 문장은 해당하는 4건(`missing_response` 1·2회차, `missing_change_doc` 2·3회차)에서 0/4 준수였다. 이번의 `needs_information` 5건은 전부 관찰 1 의 잘못된 이유이며, PRD 가 요구하는 사유로 보류한 실행은 없다.
3. **읽지 않은 근거 인용 5건 (이전 1건).** `missing_response` 1·2회차는 `not_found` 였던 `response-after` 를 `$.__root__`·`$.data.records` 로 인용했고, `missing_change_doc` 2·3회차는 `not_found` 였던 `upstream-response-change` 를 `$.machine` 으로 인용하고 `change_document` 로 지정했다. `http_error_input` 3회차는 존재하지 않는 근거 `-@-`(evidence_id `-`, version `-`)를 `lines:1-10` 으로 인용했다. 다섯 건 모두 `refs_in_attachments` 가 막았다.
4. **인용 위치 — Step 0·1 의 목표 증상은 사라졌다.** 배열 인덱스 인용 8건(`$.stages[1].error_code`, `$.stages[1]`, `$.stages[0]`)이 모두 계약을 통과하고 원문에서 해석됐고, 실제 근거의 줄 범위 인용 12건이 모두 `line_count` 안이다. 남은 `locations_resolve` 9건은 (a) 관찰 3 의 미읽음·지어낸 근거에서 파생된 5건, (b) **부재를 주장하려고 없는 경로를 인용한 2건** — `response-after@1 $.items`(`normal` 2회차, `effective_conflict` 2회차; v1 2차 `effective_conflict` 1회차에도 같은 인용이 1건 있었다), (c) 지어낸 경로 1건 — `run-daily-0920-0900@1 $._meta.workflow_id`, (d) 중첩 오류 1건 — `upstream-response-change@1 $.old_path`(`$.machine.old_path` 여야 함) 이다. (b)~(d) 는 문법이 맞고 값이 없는 경우라 계약(`pattern`)으로는 막을 수 없고 검증기만 잡는다.
5. **`normal` 2회차는 인용 하나 차이로 미통과.** diagnosis 필드·첨부 7건·나머지 6개 위치가 전부 맞았고 `response-after@1 $.items`(부재 주장) 하나가 걸렸다. 프롬프트 v1·v2 모두 "값이 없으면 인용하지 않는다" 를 명시한다. CONTRACT 5절 예시처럼 부재는 `response-before@1 $.items` + `response-after@1 $.data.records` 로 표현해야 하는데, 이를 문법으로 강제할 수단은 없다.
6. **잘못된 수정 착수 0/12 — 이번에도 검증기의 결과다.** `http_error_input` 2회차는 위치가 전부 해석돼 데모 검사까지 갔고 `failed_run_http_ok_then_transform_failed`(`http_status=503`, transform `skipped`)·`paths_differ_as_claimed`(`response_ref` 없음) 가 막았다. `effective_conflict` 3회차는 `change_effective_before_failure`(effective_at 09-21 > failed 09-20) 가 막았다. 나머지는 공통 검사에서 걸려 데모 검사에 이르지 않았다. 검증기가 없으면 12건 중 8건이 B 로 인계됐다.
7. **도구·SDK 는 안정적이다.** 조회 결과는 `ok` 와 제거한 자료의 `not_found` 뿐, `access_denied`·`unavailable`·`invalid_arguments` 0. 상한(15회·80k·300초) 미도달. 새 도구 반환 형식(줄 목록)과 strict 스키마의 `location.pattern` 은 400 없이 동작했다.

## 통과 기준 판정

- `normal` 3/3 (`ready_for_handoff` + 검증기 `passed`): **0/3 — 미충족.** 1회차 연도 오기로 조기 `needs_information`, 2회차 부재 주장 인용 1건, 3회차 연도 오기 + 빈 `baseline_run_id` 로 계약 거부.
- 나머지 12회 중 잘못된 수정 착수 0회: **충족.** 모델 행동이 아니라 검증기 결과다(관찰 6).
- 전 사례 3회 완료: 충족.

**판정: 확정 조건 미충족 — 확정 보류.**

## 남은 문제의 분리 — 도구 반환·계약·프롬프트·모델

| 층 | 이번 실행의 관찰과 판단 | 고칠 수 있는 것 (이 step 에서 적용하지 않음) |
|---|---|---|
| 도구 반환 (`tools/api.py`, `tools/store.py`) | 줄 번호 반환은 목표대로 동작했다(줄 범위 초과 0). `list_runs` 는 `before` 가 자료 범위 밖이면 빈 목록만 돌려주므로, 모델이 연도를 잘못 써도 "잘못 썼다" 는 신호가 없다. 검증기 관점에서는 문제가 없다(빈 목록이 사실이다). | 빈 목록일 때 조회 범위의 사실 메타데이터(예: `total_runs`, 최초·최근 `started_at`)를 함께 돌려주는 안. 정답이 아니라 자료 범위 사실이다. 채택 여부는 프롬프트 층 수정과 함께 판단한다. |
| 계약 (`contracts/v1.py`, `worker/model.py`) | `location.pattern` 이 생성 시점에 문법을 강제해 문법 거부가 0 이 됐다. 남은 계약 거부 1건은 `baseline_run_id` 빈 문자열(`min_length=1`) — 조사가 불완전한데 `ready_for_handoff` 를 낸 결과라 계약이 정상 차단한 것이다. 부재 주장·지어낸 경로·중첩 오류는 문법이 맞아 계약으로 막을 수 없다. | 계약 층에는 남은 항목이 없다. 부재 주장을 표현할 문법(예: `absent:` 접두)은 CONTRACT·검증기·ARCHITECTURE 를 함께 바꾸는 설계 변경이라 이 phase 범위 밖이다. |
| 프롬프트 (`worker/prompt.py`) | 인용 규칙 보강은 효과가 있었다(관찰 4). 결론 규칙에 더한 한 줄은 0/4 준수(관찰 2). `list_runs` 인자 작성 규칙(`before` 에 실패 실행의 `started_at` 을 그대로 쓴다, 또는 `before` 없이 전체 목록을 본다)은 없다. | v3: `before` 값 작성 규칙 한 줄 — 정답이 아니라 인자 규칙이다. 이 한 줄로 관찰 1 의 7건이 줄면 결론 규칙 준수를 더 많은 표본에서 볼 수 있다. 결론 규칙 자체는 v1→v2 에서 문구를 더해도 0 준수였으므로 프롬프트만으로 해결된다고 기대하지 않는다. |
| 모델 (`gpt-4.1-mini-2025-04-14`) | (a) 결론 규칙 미준수 — 모순을 스스로 적고도 확정 진단(조사 완주 7/7). (b) 읽지 않은·존재하지 않는 근거 인용 5건. (c) 도구 결과에 있는 시각을 다른 연도로 옮겨 씀 7건. (d) 부재 주장에 없는 경로 인용 2건 — 프롬프트가 명시적으로 금지한 행동. (a)(b)(d) 는 이전 보고서가 "도구·계약 수정으로 사라지지 않는다" 고 예상한 항목이며, 그대로였다. | 이전 보고서의 조건("도구 반환·계약 스키마를 고친 뒤 재평가해도 `needs_information` 준수가 0 에 가깝고 `normal` 3/3 이 안 되면 상위 모델을 같은 하네스로 평가") 에 도달했다. 아래 절. |

## ADR-0003 확정 여부

**확정 조건 미충족 — 확정 보류.** 키·계정 API 사용 가능·단가·예산은 확인돼 있고 실호출은 동작하지만, ARCHITECTURE 의 통과 기준(`normal` 3/3)을 v1 1차 1/3, v1 2차 1/3, v2 0/3 으로 세 번 넘지 못했다. "잘못된 수정 착수 0회" 는 세 번 모두 검증기가 만든 결과다.

**상위 모델 비교 평가가 필요하다고 판단한다.** 근거:

- 값싸고 결정적인 수정(도구 반환·계약 문법·프롬프트 인용 규칙)은 적용했고 그 목표 증상(문법 거부·줄 범위 초과)은 0 이 됐다. 남은 실패는 모델이 자기가 읽은 사실과 어긋나는 결론을 내는 행동(관찰 1·2·3·5)이며, 같은 종류의 행동이 프롬프트 v1·v2 에서 모두 나타났다.
- 결론 규칙 준수는 v1 0/18, v2 0/7 이다. 이 값이 0 인 한 `normal` 이 3/3 이 되더라도 자료 누락·충돌 사례에서 A 는 매번 검증기 차단으로 `확인 필요` 에 머물고, PRD "입력 기록이 달라졌는데도 같은 확정 진단이면 수용 기준 실패" 를 통과하지 못한다.
- 표본이 사례당 3회라 `normal` 1/3 과 0/3 의 차이는 판단 근거로 약하다. 모델 비교는 같은 하네스(`python3 scripts/diag_eval.py --cases all --repeat 3`)·같은 프롬프트·같은 도구 계약으로 해야 층을 섞지 않는다.

제안 순서 — 사용자 결정 사항이며 이 step 은 실행하지 않았다:

1. 프롬프트 v3 에 `list_runs` 의 `before` 작성 규칙 한 줄을 넣는다(관찰 1 의 7건은 모델 비교에서 잡음이다). 정답·fixture 구조는 넣지 않는다.
2. 같은 제공자의 상위 모델 `gpt-4.1`(스냅샷은 공식 모델 문서에서 확인)을 `DIAG_MODEL_ID` 로 지정해 같은 하네스로 5사례 × 3회 돌린다. 단가는 공식 가격 페이지에서 확인해 `DIAG_PRICE_*` 를 바꿔야 한다 — mini 의 몇 배인지에 따라 다르지만 이번 실행이 US$0.10 이었으므로 5배라도 US$0.5 안팎으로 평가 예산 US$2 안이다. 실행 중 조용한 대체는 하지 않으며 `provenance.model_id` 에 그대로 남는다.
3. 그 결과로 `normal` 3/3 과 결론 규칙 준수(`needs_information` 이 의도한 사유로 나오는 비율)를 비교해 ADR-0003 확정 또는 대체 ADR 을 사용자가 결정한다. 이 문서는 ADR 파일을 고치지 않는다.

배포 관점은 이전 보고서와 같다: 현재 상태로 심사 데모를 돌리면 A 는 대부분 `확인 필요 · 미충족 항목`(결과 배지는 검증기 차단이면 `인계 가능`, 연도 오기 조기 종료면 `정보 필요`; 계약 거부는 `실패 · model_output_invalid`)으로 끝나고 B 는 자동 착수하지 않는다.
