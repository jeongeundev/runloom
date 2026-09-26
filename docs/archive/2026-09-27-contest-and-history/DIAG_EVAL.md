# 진단 모델 비교 평가 — 종합

갱신일: 2026-09-20
상태: phase `2-model-compare` Step 2 의 종합 보고서. 같은 하네스(`scripts/diag_eval.py`)·같은 프롬프트(`diag-prompt-v3`)·같은 도구 계약(`tools-v2`)으로 `gpt-4.1-mini` 와 `gpt-4.1` 을 각각 5사례 × 3회 돌린 결과를 이전 두 평가(프롬프트 v1·v2, mini)와 나란히 놓는다. 스크립트가 생성한 원본 표는 모델별 보고서에 있고 이 문서는 손으로 썼다. 결과가 좋게 보이도록 편집하지 않는다. **ADR-0003 파일은 이 평가가 고치지 않는다** (사용자 확정) — 마지막 절은 제안이다.

| 보고서 | 내용 |
|---|---|
| [DIAG_EVAL_2026-09-20_prompt-v1.md](DIAG_EVAL_2026-09-20_prompt-v1.md) | mini · 프롬프트 v1 · 도구 v1. 5사례 × 3회 두 번(1차·2차) + 사전 확인 1회 |
| [DIAG_EVAL_2026-09-20_prompt-v2.md](DIAG_EVAL_2026-09-20_prompt-v2.md) | mini · 프롬프트 v2 · 도구 v2. 1회. location 배열 인덱스·줄 번호 반환 적용 후 |
| [DIAG_EVAL_prompt-v3_gpt-4.1-mini.md](DIAG_EVAL_prompt-v3_gpt-4.1-mini.md) | mini · 프롬프트 v3 · 도구 v2. 1회 — 이번 비교의 한쪽 |
| [DIAG_EVAL_prompt-v3_gpt-4.1.md](DIAG_EVAL_prompt-v3_gpt-4.1.md) | gpt-4.1 · 프롬프트 v3 · 도구 v2. 1회 — 이번 비교의 다른 쪽 |

## 조건

| 평가 | 모델 (`provenance.model_id`) | 프롬프트 · 도구 계약 | 단가 입력/출력 (US$/1M) | 실행 (KST) | 호출 · 입력/출력 토큰 · 시간 | 총 추정 비용 |
|---|---|---|---|---|---|---|
| v1 mini 2차 | `gpt-4.1-mini-2025-04-14` | `diag-prompt-v1` · `tools-v1` | 0.40 / 1.60 | 15:01~15:04 | 93 · 245k/10.3k · 209초 | US$0.1147 (사전 확인·1차 포함 US$0.2263) |
| v2 mini | `gpt-4.1-mini-2025-04-14` | `diag-prompt-v2` · `tools-v2` | 0.40 / 1.60 | 15:48~15:51 | 70(+1 미집계) · 193k/11.2k · 166초 | US$0.0951 (실제 약 0.10) |
| **v3 mini** | `gpt-4.1-mini-2025-04-14` | `diag-prompt-v3` · `tools-v2` | 0.40 / 1.60 | 16:36~16:39 | 90 · 267k/14.8k · 214초 | **US$0.1305** |
| **v3 gpt-4.1** | `gpt-4.1-2025-04-14` | `diag-prompt-v3` · `tools-v2` | 2.00 / 8.00 | 16:39~16:50 | 108 · 319k/12.7k · 629초 | **US$0.7394** |

- 공통: 진단 1회 상한 호출 15 · 입력 80k · 출력 8k 토큰 · 300초. 사례 정의·fixture·검증기 동일. 경로는 `run_diagnosis` → `assemble_result` → 중앙 `verify_diagnosis` (HTTP 없음). 평가 예산 `DIAG_EVAL_BUDGET_USD=2` 는 스크립트 실행 1회마다 적용된다.
- v3 두 실행은 Step 1(계약 거부 턴의 호출·토큰을 `usage` 에 더함) 이후라 호출·토큰이 실제와 같다. v1·v2 는 계약 거부 1건당 한 턴이 빠져 있다.
- 단가는 사용자가 2026-09-20 공식 가격 페이지에서 확인한 값이다. gpt-4.1 단가는 실행 명령의 환경변수로 덮어썼고 `.env` 는 mini 단가 그대로다.
- 두 보고서의 "조건" 표와 산출물 `diagnosis_result` 15개의 `provenance.model_id` 가 각각 `gpt-4.1-mini-2025-04-14`, `gpt-4.1-2025-04-14` 로 일치함을 확인했다. 실행 중 모델 대체는 없다.
- 이 step 의 실호출은 두 번(모델당 1회)이며 스크립트 결함·재실행은 없었다. 이 step 비용 US$0.8699. 프로젝트 누적(사전 확인 포함) 약 US$1.19.
- 원본: `data/diag-eval/2026-09-20T163610+0900/`(mini), `data/diag-eval/2026-09-20T163950+0900/`(gpt-4.1) — `results.json`, 진단 서비스 DB·산출물, `invalid-drafts/`. `data/` 는 git 에 들어가지 않는다.

## 모델 비교표 — v3 mini vs v3 gpt-4.1

outcome · 검증기 · 실패 check · 호출 · 입력/출력 토큰 · US$. `거부` 는 계약 검증기가 초안을 거부한 실행(`model_output_invalid`).

| 사례 | 회차 | mini outcome | mini 검증기 | mini 실패 check | mini 호출·토큰·US$ | gpt-4.1 outcome | gpt-4.1 검증기 | gpt-4.1 실패 check | gpt-4.1 호출·토큰·US$ |
|---|---|---|---|---|---|---|---|---|---|
| `normal` | 1 | ready_for_handoff | failed | `refs_in_attachments`, `locations_resolve` | 6 · 17,519/895 · 0.0084 | ready_for_handoff | **passed** | — | 7 · 20,628/909 · 0.0485 |
| `normal` | 2 | 거부 (`baseline_run_id=''`) | — | — | 4 · 11,048/1,149 · 0.0063 | ready_for_handoff | **passed** | — | 7 · 20,628/929 · 0.0487 |
| `normal` | 3 | ready_for_handoff | failed | `locations_resolve` | 8 · 25,724/1,028 · 0.0119 | ready_for_handoff | **passed** | — | 7 · 20,628/904 · 0.0485 |
| `missing_response` | 1 | 거부 (`baseline_run_id=''`) | — | — | 3 · 8,405/928 · 0.0048 | ready_for_handoff | failed | `refs_in_attachments`, `locations_resolve` | 7 · 20,451/873 · 0.0479 |
| `missing_response` | 2 | ready_for_handoff | failed | `refs_in_attachments`, `locations_resolve` | 7 · 21,013/1,062 · 0.0101 | ready_for_handoff | undecidable | `paths_differ_as_claimed` (첨부 없음) | 8 · 23,882/961 · 0.0555 |
| `missing_response` | 3 | ready_for_handoff | failed | `refs_in_attachments`, `locations_resolve` | 7 · 21,013/1,092 · 0.0102 | ready_for_handoff | undecidable | `paths_differ_as_claimed` (첨부 없음) | 7 · 20,451/877 · 0.0479 |
| `missing_change_doc` | 1 | ready_for_handoff | failed | `refs_in_attachments`, `locations_resolve` | 6 · 17,757/848 · 0.0085 | **needs_information** (`evidence_unavailable`) | undecidable | — | 8 · 24,361/616 · 0.0537 |
| `missing_change_doc` | 2 | ready_for_handoff | failed | `refs_in_attachments`, `locations_resolve` | 5 · 14,089/1,011 · 0.0073 | **needs_information** (`evidence_unavailable`) | undecidable | — | 8 · 24,361/704 · 0.0544 |
| `missing_change_doc` | 3 | ready_for_handoff | failed | `refs_in_attachments`, `locations_resolve` | 7 · 21,475/848 · 0.0099 | ready_for_handoff | failed | `refs_in_attachments`, `locations_resolve` | 7 · 20,795/836 · 0.0483 |
| `effective_conflict` | 1 | ready_for_handoff | failed | `change_effective_before_failure` | 7 · 21,052/1,024 · 0.0101 | ready_for_handoff | failed | `change_effective_before_failure` | 7 · 20,628/905 · 0.0485 |
| `effective_conflict` | 2 | ready_for_handoff | failed | `change_effective_before_failure` | 7 · 21,052/1,178 · 0.0103 | ready_for_handoff | failed | `change_effective_before_failure` | 7 · 20,628/862 · 0.0482 |
| `effective_conflict` | 3 | 거부 (`baseline_run_id=''`) | — | — | 3 · 8,464/887 · 0.0048 | ready_for_handoff | failed | `change_effective_before_failure` | 7 · 20,628/969 · 0.0490 |
| `http_error_input` | 1 | ready_for_handoff | failed | `failed_run_http_ok_then_transform_failed`, `paths_differ_as_claimed` | 7 · 20,121/956 · 0.0096 | ready_for_handoff | failed | `failed_run_http_ok_then_transform_failed`, `paths_differ_as_claimed` | 7 · 20,121/806 · 0.0467 |
| `http_error_input` | 2 | ready_for_handoff | failed | 위와 같음 | 7 · 21,048/961 · 0.0100 | ready_for_handoff | failed | 위와 같음 | 7 · 20,121/838 · 0.0469 |
| `http_error_input` | 3 | ready_for_handoff | failed | 위와 같음 | 6 · 17,257/924 · 0.0084 | ready_for_handoff | failed | 위와 같음 | 7 · 20,402/757 · 0.0469 |

기대 결과(ARCHITECTURE·PRD): `normal` 은 `ready_for_handoff` + `passed`; `missing_response`·`missing_change_doc` 은 `needs_information`; `effective_conflict` 는 `needs_information`(evidence_conflict) 또는 검증기 `failed`; `http_error_input` 은 `needs_information`(unsupported_diagnosis) — `response_path_changed` 재생 없음.

## 지표 비교

| 지표 | v2 mini | v3 mini | v3 gpt-4.1 | 읽기 |
|---|---|---|---|---|
| `normal` 통과 (`ready_for_handoff` + `passed`) | 0/3 | 0/3 | **3/3** | mini 는 네 번(v1 1/3·1/3, v2 0/3, v3 0/3) 모두 미달 |
| 잘못된 수정 착수 (normal 외 `ready_for_handoff` + `passed`) | 0/12 | 0/12 | 0/12 | 세 번 모두 검증기가 만든 결과 (아래 판정 절) |
| 의도한 사유로 `needs_information` (응답 누락·문서 누락·시각 충돌·503) | 0/12 | 0/12 | **2/12** | gpt-4.1 은 문서 누락 2건에서 `evidence_unavailable` + `evidence_id` 를 정확히 적음 |
| `needs_information` 전체 (잘못된 사유 포함) | 5/15 | 0/15 | 2/15 | v2 의 5건은 전부 연도 오기로 "정상 실행 없음" |
| 조사 완주 (정상 실행 `get_run` 도달) | 8/15 | 12/15 | 15/15 | |
| `list_runs.before` 연도 오기 | 7/15 | 0/12 (3건 확인 불가) | 0/15 | v3 의 인자 규칙이 효과. mini 의 3건은 계약 거부 실행이라 조회 이력 산출물이 없다 (아래 관찰) |
| 계약 거부 (`model_output_invalid`) | 1 | 3 | 0 | mini 3건 전부 `diagnosis.baseline_run_id=''` — 정상 실행 없이 `ready_for_handoff` |
| 읽지 않은 근거 인용 (`refs_in_attachments` 실패 실행) | 5 | 6 (+거부 초안 1) | 2 | gpt-4.1 은 `not_found` 자료 인용 2건(`missing_response` 1, `missing_change_doc` 3) |
| 부재 주장 인용 (읽은 `response-after` 에 `$.items`) | 2 | 1 | 0 | gpt-4.1 은 부재를 로그 2행(`observed_root_keys`)으로 표현 |
| 지어낸 경로 | 1 (`$._meta.workflow_id`) | 2 (`$._keys` ×2) | 0 | |
| 사실이 뒤집힌 진단 | 0 | 1 (`missing_change_doc` 2: old=`$.data.records` new=`$.items`) | 0 | |
| `locations_resolve` 실패 실행 | 9 | 7 | 2 | gpt-4.1 의 2건은 읽지 않은 자료에서 파생 |
| 검증기 `undecidable` | 3 | 0 | 4 | v2 의 3건은 연도 오기로 조기 종료한 결과. gpt-4.1 의 4건은 읽지 않은 자료를 인용하지 않아 첨부가 부족한 경우 — "차단(`failed`)" 보다 "보류" 로 끝난다 |
| 진단 1회 평균 호출 · 토큰 · 시간 | 4.7 · 12.9k/0.7k · 11초 | 6.0 · 17.8k/1.0k · 14초 | 7.2 · 21.2k/0.8k · **42초** | gpt-4.1 은 13~52초. 상한 300초 안 |
| 진단 1회 평균 비용 | US$0.0063 (실제 ~0.0067) | US$0.0087 | **US$0.0493** | `normal` 은 0.0485·0.0487·0.0485 |
| 총비용 | US$0.0951 (실제 ~0.10) | US$0.1305 | US$0.7394 | |

## 관찰 — v3 gpt-4.1

1. **`normal` 3/3 — 세 회차가 같은 궤적.** 조회 순서 9회가 동일하고 입력 토큰이 셋 다 20,628 이다. diagnosis 필드(경로·실행·문서)·첨부 7개·인용 위치 전부 원문에서 해석됐고 데모 검사 7개도 통과. 3회차의 `$.machine.empty_list_policy.missing` 처럼 깊은 경로도 실제 값이다.
2. **문서 누락 2/3 에서 스스로 보류.** `missing_change_doc` 1·2회차는 `read_evidence upstream-response-change@1` 이 `not_found` 로 두 번 돌아오자 `needs_information` · `evidence_unavailable` · `evidence_id: upstream-response-change` 를 냈다 — CONTRACT 6절 예시와 같은 구조이며 PRD "부족한 자료·이유 표시" 그대로다. 3회차는 같은 `not_found` 문서를 `$.machine` 으로 인용하고 `change_document` 로 지정해 `refs_in_attachments` 가 막았다.
3. **응답 누락 0/3 보류 — 그러나 지어내지 않음.** `missing_response` 세 회차 모두 `ready_for_handoff` 였다. 2·3회차는 `not_found` 인 `response-after` 를 인용하지 않고 로그 2행(`observed_root_keys=[report_date,data]`)으로 부재를 표현했다. 검증기는 `paths_differ_as_claimed` 에서 "첨부 없음" 으로 `undecidable`(보류) 을 냈고, 화면에는 `확인 필요 · 미충족 항목` 으로 남는다. 1회차만 `not_found` 자료를 `$.data.records` 로 인용해 `failed`.
4. **시각 충돌 0/3, HTTP 503 0/3 — 읽고도 판단에 반영하지 않음.** `effective_conflict` 는 세 회차 모두 인용 위치가 전부 해석됐는데(`$.machine.effective_at` 을 읽었다) `ready_for_handoff` 였다. 1회차 요약 "변경 적용 직후"(사실은 적용 전), 2회차 claim "2026-09-21 00시 이전에 미리 변경되었음이 변경 안내 문서에 명시되었다"(문서에 없는 내용), 3회차는 적용 시각을 언급하지 않았다. `http_error_input` 은 세 회차 모두 claim 에 "fetch 단계 HTTP 503" 을 적고도 diagnosis 는 `response_path_changed` 였고, 2회차 요약은 "응답 구조 변경으로 인해 503" 이라는 인과를 만들었다. 여섯 건 모두 데모 검사(`change_effective_before_failure`, `failed_run_http_ok_then_transform_failed`·`paths_differ_as_claimed`)가 막았다.
5. **도구 사용은 정확하다.** `list_runs.before` 15/15 가 실패 실행의 `started_at` 그대로, 정상 실행 `get_run` 15/15, 계약 거부 0, 지어낸 경로 0, 부재 주장 인용 0. `not_found` 자료를 한 번 더 읽어 보는 재시도가 3건(`missing_response` 2, `missing_change_doc` 1·2) 있었고 그중 2건이 보류로 이어졌다.
6. **느리고 비싸다.** 진단 1회 평균 42초(13~52초), mini 의 3배. 비용 US$0.049 로 mini 의 5.7배. 상한(15회·80k·300초)에는 닿지 않았다. 웹 화면은 3초 폴링이라 `실행 중` 표시가 30~50초 이어진다.

## 관찰 — v3 mini (v2 대비)

1. **연도 오기는 사라졌다.** 조회 이력이 남은 12건 전부 `before=2026-09-20T09:00:00+09:00`. v2 의 7/15 → 0/12. 계약 거부 3건(`normal` 2, `missing_response` 1, `effective_conflict` 3)은 조회 이력 산출물이 저장되지 않아 인자를 확인할 수 없다 — 셋 다 `list_runs` 직후 정상 실행 `get_run` 없이 `list_documents` 로 넘어갔고 요약에 "직전 정상 실행 기록이 없어" 라고 적었으므로 빈 목록을 받은 것으로 보이나, `before`·`status` 값이 원인인지는 알 수 없다. 조사 완주는 8 → 12.
2. **완주가 늘자 확정 진단이 늘었다.** 유효 결과 12건 전부 `ready_for_handoff`, `needs_information` 0. 결론 규칙 준수 0/12 — v1 0/18, v2 0/7 에 이어 같다. v2 의 `needs_information` 5건은 전부 잘못된 사유였으므로, 의도한 사유의 보류는 세 평가 통틀어 0 이다.
3. **읽지 않은 근거 인용 6건(+거부 초안 1).** `not_found` 자료 인용 5(`missing_response` 2·3, `missing_change_doc` 1·2·3), 한 번도 읽지 않은 `response-before` 인용 2(`normal` 1, `missing_change_doc` 2). v2 5건과 같은 수준이다.
4. **지어낸 경로·뒤집힌 주장.** `response-after@1 $._keys` 2건(`normal` 3, `missing_response` 2). `missing_change_doc` 2회차는 old/new 경로를 반대로 적고(`old=$.data.records`, `new=$.items`) 읽지 않은 `response-before` 에 `$.data.records` 가 있다고 주장했다.
5. **`normal` 은 인용 하나 차이로 두 번 미통과, 한 번 계약 거부.** 1회차 `response-after@1 $.items`(부재 주장) + 읽지 않은 `response-before` 인용, 3회차 `$._keys` 하나. v2 `normal` 2회차와 같은 양상이다. 2회차는 정상 실행을 못 찾고 `baseline_run_id=''` 로 거부됐다.
6. **계약 거부 3건 전부 `baseline_run_id=''`.** 정상 실행을 찾지 못했는데도 `ready_for_handoff` 를 냈고 계약(`min_length=1`)이 막았다. 조회 이력이 없는 것은 하네스가 계약 거부 실행의 trace 를 저장하지 않기 때문이며 이 step 에서 고치지 않았다.

## 통과 기준 판정 — 모델별

ARCHITECTURE "진단 모델과 평가 기준": 정상 사례 3회 모두 정확한 근거와 결과(`normal` 3/3 `ready_for_handoff` + `passed`), 나머지 12회에서 잘못된 수정 착수 0.

| 기준 | v3 mini | v3 gpt-4.1 |
|---|---|---|
| `normal` 3/3 | **미충족** 0/3 | **충족** 3/3 |
| 잘못된 수정 착수 0/12 | 충족 — 검증기 12/12 차단 | 충족 — 모델 보류 2 + 검증기 차단 10 |
| 전 사례 3회 완료 | 충족 | 충족 |
| **판정** | **확정 보류 (네 번째)** | **통과 기준 충족** |

PRD "도구·인계 수용 기준" 표에 대한 gpt-4.1 의 위치 — 제품 수준(A `확인 필요`·B 미실행·이유 표시)과 모델 수준(스스로 보류)을 나눠 본다:

| PRD 사례 | 제품 수준 | 모델 수준 |
|---|---|---|
| 모든 데모 근거 조회 가능 | 3/3 — 조회 이력·인용 일치, 첨부만으로 재현 가능 | 3/3 |
| 실패 응답 본문 누락 | 3/3 — 검증기 `undecidable`/`failed`, 미충족 항목에 "첨부 없음: response-after@1" | 0/3 |
| 변경 안내 누락 | 3/3 | **2/3** |
| 적용 시각 충돌 | 3/3 — `change_effective_before_failure` 가 `evidence_conflict` 를 표시 | 0/3 |
| HTTP 오류 입력 | 3/3 — 인계 차단. 다만 PRD "기존 형식 변경 진단을 재생하지 않고" 는 위반 | 0/3 |

즉 "잘못된 수정 착수 0" 은 gpt-4.1 에서도 대부분 검증기의 결과다(10/12). 모델 교체가 바꾼 것은 (a) `normal` 이 안정적으로 통과하고, (b) 도구 인자·인용 위치·계약 형식 오류가 사라지고, (c) 문서 누락에서 2/3 스스로 보류한다는 점이다. 시각 충돌·HTTP 오류·응답 누락에서 스스로 보류하는 행동은 여전히 없다.

## ADR-0003 판단 — 제안 (2026-09-20 사용자가 gpt-4.1 로 확정, ADR 갱신됨)

**gpt-4.1(`gpt-4.1-2025-04-14`) 로 ADR-0003 을 갱신하거나 ADR-0007 로 대체할 것을 제안한다.** 근거: 통과 기준을 같은 하네스에서 충족했고, mini 는 네 번의 평가(도구·계약·프롬프트 층을 세 차례 고친 뒤에도)에서 `normal` 을 한 번도 3/3 으로 넘기지 못했다. 시연의 핵심인 "A 자동 완료 → B 자동 착수" 는 `normal` 이 안정적이어야 성립한다.

비용 (단가 2.00/8.00, 이번 실행 기준):

| 항목 | gpt-4.1 | mini (참고) |
|---|---|---|
| 진단 1회 | US$0.049 (범위 0.047~0.056, `normal` 0.0485) | US$0.0087 |
| 하루 60회 상한(전체 일일 상한)을 채울 때 | US$2.96/일 | US$0.52/일 |
| 월 예상 (30일 × 60회) | **US$88.7** | US$15.7 |
| 심사 기간 15일 (9/21~10/5) × 60회 | US$44.4 | US$7.8 |
| 총액 US$30 · 90% 정지(US$27) 도달 | 약 547회 — 하루 60회면 **10일째** 정지 | 약 3,100회 — 도달 안 함 |

하루 60회·15일을 US$27 안에 두려면 `DIAG_GLOBAL_DAILY` 를 **36회 이하**로 낮추거나 총액 상한을 US$45 이상으로 올려야 한다. 세션당 10회 상한은 그대로 둬도 된다. 실제 심사 트래픽은 알 수 없으므로 DEPLOY 9절의 `/budget` 점검으로 소진 속도를 보고 조정한다.

시간: 진단 1회 42초(mini 14초). 상한 300초 안이지만 `accepted` 후 `started` 미확인 → `unknown` 2분 규칙과는 무관하고(진단 워커가 `started` 를 먼저 보낸다), 화면의 `실행 중` 이 길어질 뿐이다.

채택 시 함께 바뀔 것 (사용자가 확정한 뒤에만, 이 step 은 어느 것도 고치지 않았다): ADR-0003 본문 또는 ADR-0007 신설, `src/diagnostic_demo/settings.py` 의 `DEFAULT_MODEL_ID`, `deploy/env/diag.env.example` 의 `DIAG_MODEL_ID`·`DIAG_PRICE_*`, DEPLOY.md 3절의 단가 문구, ARCHITECTURE "진단 모델과 평가 기준"·"모델 호출 예산" 절, 로컬 `.env` 의 단가.

다른 선택지 — 장단점:

| 선택지 | 장점 | 단점 |
|---|---|---|
| **gpt-4.1 채택 (제안)** | `normal` 3/3, 도구 인자·인용·계약 오류 0, 문서 누락 2/3 스스로 보류. 프롬프트·도구 재작업 없이 지금 배포 가능 | 비용 5.7배·시간 3배. 하루 상한을 낮추거나 총액을 올려야 15일을 버틴다. 시각 충돌·503·응답 누락은 여전히 검증기 의존 |
| mini 유지 + 검증기 결과를 `확인 필요` 로 쓰는 데모 운영 | 비용 최소. 검증기가 잘못된 착수는 막는다 | 정상 사례가 4번의 평가에서 3/3 이 안 됐다 — 시연의 자동 인계가 성립하지 않고, 심사자가 보는 A 는 대부분 `확인 필요 · 미충족 항목` 또는 `실패 · model_output_invalid` |
| 추론 모델 평가 | 결론 규칙(스스로 보류) 준수가 오를 가능성 | 단가·예산·스냅샷 확인이 없어 이 step 범위 밖. 응답 시간이 더 길 수 있음. 같은 하네스로 5사례 × 3회 한 번 더 필요 |
| 프롬프트 재설계 (v4) 후 재평가 | 결론 규칙 문장을 첫 줄로 올리고 "ok 가 false 인 자료가 있으면 `needs_information`" 을 강조하면 gpt-4.1 의 2/12 가 오를 수 있음 | mini 에서는 v1→v2→v3 문구 추가가 결론 규칙에 효과가 없었다(0/18·0/7·0/12). 재평가 비용 US$0.75(gpt-4.1) 추가 |

남은 문제 — 모델 교체로 해결되지 않는 것: 자료 누락·충돌·다른 원인에서 모델이 스스로 보류하는 비율은 gpt-4.1 에서도 2/12 다. PRD "입력 기록이 달라졌는데도 같은 확정 진단이면 수용 기준 실패" 를 모델 수준에서 통과한 것은 아니며, 제품은 검증기(`verify_diagnosis`)로 이를 막는다. ARCHITECTURE 의 "구조화 출력 성공과 사실 관계 검증 통과는 별개" 가 gpt-4.1 에서도 필요한 방어선이다. 이 검증기가 `failed` 대신 `undecidable` 로 끝나는 비율(gpt-4.1 4/12)이 높아진 것은 모델이 읽지 않은 자료를 지어내지 않기 때문이며, 화면 표시는 둘 다 `확인 필요` 다.

배포 관점: gpt-4.1 로 심사 데모를 돌리면 정상 사례 A 는 `완료 · 판정 근거` 로 끝나고 B 가 자동 착수한다(이 평가 기준 3/3). 자료가 빠지거나 충돌하는 비교 사례는 `확인 필요` 에 머문다 — 이는 "검증 없이는 인계하지 않는다" 는 제품 주장과 일치한다.
