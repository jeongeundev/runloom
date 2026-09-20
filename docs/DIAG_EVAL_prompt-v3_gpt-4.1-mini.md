# 진단 모델 실호출 평가

갱신일: 2026-09-20
상태: Step 17 `scripts/diag_eval.py` 가 생성한 기록. ARCHITECTURE "진단 모델과 평가 기준"의 5사례 × 3회를 실제 모델로 돌린 결과다. 결과가 좋게 보이도록 편집하지 않는다. ADR-0003 파일 자체는 이 평가가 고치지 않는다 (사용자 확정).

## 조건

| 항목 | 값 |
|---|---|
| 모델 | `gpt-4.1-mini-2025-04-14` (Responses API, function calling + structured outputs strict) |
| 프롬프트·도구 계약 | `diag-prompt-v3` · `tools-v2` |
| 단가 (US$ / 1M 토큰) | 입력 0.4 · 출력 1.6 (`DIAG_PRICE_*`, 사용자가 공식 가격 페이지에서 확인) |
| 진단 1회 상한 | 호출 15회 · 입력 80,000 · 출력 8,000 토큰 · 300초 |
| 평가 예산 | US$2 (`DIAG_EVAL_BUDGET_USD`) — 매 실행 전 누적 비용 확인 |
| 실행 | 2026-09-20T16:36:10+09:00 ~ 2026-09-20T16:39:44+09:00 (KST) · 사례당 3회 · 총 15회 |
| 총 추정 비용 | US$0.1305 |
| 경로 | `run_diagnosis` → `assemble_result` → 중앙 `verify_diagnosis` 를 프로세스 안에서 직접 호출 (HTTP 없음). 자료 누락·교체는 `FixtureStore(removed=, replaced=)`, fixture 파일 불변 |

## 통과 기준과 판정

- normal 0/3: `ready_for_handoff` 이고 검증기 `passed` — 기준 3/3
- 잘못된 수정 착수 0/12: 나머지 4사례에서 `ready_for_handoff` 이면서 `passed` 인 실행 — 기준 0
- 전 사례 3회 완료: 예

**판정: 확정 보류.** 기준 미충족 실행:

- normal 1회차: outcome=ready_for_handoff 검증기=failed 실패 check `refs_in_attachments`, `locations_resolve`
- normal 2회차: outcome=— 검증기=— 오류 `model_output_invalid: 초안이 DiagnosisDraft 형식이 아닙니다: 1개 오류 — diagnosis.baseline_run_id=''`
- normal 3회차: outcome=ready_for_handoff 검증기=failed 실패 check `locations_resolve`

도구 반환·계약·프롬프트 중 어디가 문제인지의 분리 분석과 모델 교체 필요 여부는 아래 절에 적는다.

## 사례별 결과

| 사례 | 회차 | outcome | 검증기 | diagnosis / missing | 호출 | 입력 토큰 | 출력 토큰 | 초 | US$ | 오류 |
|---|---|---|---|---|---|---|---|---|---|---|
| `normal` | 1 | ready_for_handoff | failed | response_path_changed | 6 | 17,519 | 895 | 15.2 | 0.0084 | — |
| `normal` | 2 | — | — | — | 4 | 11,048 | 1,149 | 13.3 | 0.0063 | model_output_invalid: 초안이 DiagnosisDraft 형식이 아닙니다: 1개 오류 — diagnosis.baseline_run_id='' |
| `normal` | 3 | ready_for_handoff | failed | response_path_changed | 8 | 25,724 | 1,028 | 16.7 | 0.0119 | — |
| `missing_response` | 1 | — | — | — | 3 | 8,405 | 928 | 10.5 | 0.0048 | model_output_invalid: 초안이 DiagnosisDraft 형식이 아닙니다: 1개 오류 — diagnosis.baseline_run_id='' |
| `missing_response` | 2 | ready_for_handoff | failed | response_path_changed | 7 | 21,013 | 1,062 | 14.7 | 0.0101 | — |
| `missing_response` | 3 | ready_for_handoff | failed | response_path_changed | 7 | 21,013 | 1,092 | 16.1 | 0.0102 | — |
| `missing_change_doc` | 1 | ready_for_handoff | failed | response_path_changed | 6 | 17,757 | 848 | 17.9 | 0.0085 | — |
| `missing_change_doc` | 2 | ready_for_handoff | failed | response_path_changed | 5 | 14,089 | 1,011 | 12.2 | 0.0073 | — |
| `missing_change_doc` | 3 | ready_for_handoff | failed | response_path_changed | 7 | 21,475 | 848 | 17.7 | 0.0099 | — |
| `effective_conflict` | 1 | ready_for_handoff | failed | response_path_changed | 7 | 21,052 | 1,024 | 16.1 | 0.0101 | — |
| `effective_conflict` | 2 | ready_for_handoff | failed | response_path_changed | 7 | 21,052 | 1,178 | 14.9 | 0.0103 | — |
| `effective_conflict` | 3 | — | — | — | 3 | 8,464 | 887 | 9.2 | 0.0048 | model_output_invalid: 초안이 DiagnosisDraft 형식이 아닙니다: 1개 오류 — diagnosis.baseline_run_id='' |
| `http_error_input` | 1 | ready_for_handoff | failed | response_path_changed | 7 | 20,121 | 956 | 13.9 | 0.0096 | — |
| `http_error_input` | 2 | ready_for_handoff | failed | response_path_changed | 7 | 21,048 | 961 | 13.2 | 0.0100 | — |
| `http_error_input` | 3 | ready_for_handoff | failed | response_path_changed | 6 | 17,257 | 924 | 12.1 | 0.0084 | — |

기대 결과 (ARCHITECTURE·PRD 수용 기준):

- `normal`: `ready_for_handoff` + 검증기 `passed`
- `missing_response`: `needs_information` (실패 응답 본문 누락)
- `missing_change_doc`: `needs_information` (변경 안내 누락)
- `effective_conflict`: `needs_information`(evidence_conflict) 또는 검증기 `failed` — 인계 없음
- `http_error_input`: `needs_information`(unsupported_diagnosis) — response_path_changed 재생 없음

## 검증기가 실패로 표시한 check

| 사례 | 회차 | 검증기 | 실패 check |
|---|---|---|---|
| `normal` | 1 | failed | `refs_in_attachments`, `locations_resolve` |
| `normal` | 3 | failed | `locations_resolve` |
| `missing_response` | 2 | failed | `refs_in_attachments`, `locations_resolve` |
| `missing_response` | 3 | failed | `refs_in_attachments`, `locations_resolve` |
| `missing_change_doc` | 1 | failed | `refs_in_attachments`, `locations_resolve` |
| `missing_change_doc` | 2 | failed | `refs_in_attachments`, `locations_resolve` |
| `missing_change_doc` | 3 | failed | `refs_in_attachments`, `locations_resolve` |
| `effective_conflict` | 1 | failed | `change_effective_before_failure` |
| `effective_conflict` | 2 | failed | `change_effective_before_failure` |
| `http_error_input` | 1 | failed | `failed_run_http_ok_then_transform_failed`, `paths_differ_as_claimed` |
| `http_error_input` | 2 | failed | `failed_run_http_ok_then_transform_failed`, `paths_differ_as_claimed` |
| `http_error_input` | 3 | failed | `failed_run_http_ok_then_transform_failed`, `paths_differ_as_claimed` |

## 도구 호출 순서 (조회 이력)

- `normal` 1회차 (`eval-normal-1-a17307b6`): `get_run daily-0920-0900 ok` → `list_runs daily-report ok` → `get_run daily-0919-0900 ok` → `read_evidence response-after@1 ok` → `read_evidence log-daily-0920@1 ok` → `list_documents daily-report ok` → `read_evidence upstream-response-change@1 ok` → `read_evidence daily-report-contract@1 ok`
- `normal` 2회차 (`eval-normal-2-cdff91c2`): `get_run daily-0920-0900 ok` → `list_runs daily-report ok` → `list_documents daily-report ok` → `read_evidence upstream-response-change@1 ok` → `read_evidence daily-report-contract@1 ok`
- `normal` 3회차 (`eval-normal-3-93c3a59a`): `get_run daily-0920-0900 ok` → `list_runs daily-report ok` → `get_run daily-0919-0900 ok` → `read_evidence response-after@1 ok` → `read_evidence response-before@1 ok` → `read_evidence log-daily-0920@1 ok` → `read_evidence log-daily-0919@1 ok` → `list_documents daily-report ok` → `read_evidence upstream-response-change@1 ok` → `read_evidence daily-report-contract@1 ok`
- `missing_response` 1회차 (`eval-missing_response-1-768c2e8f`): `get_run daily-0920-0900 ok` → `list_runs daily-report ok` → `list_documents daily-report ok` → `read_evidence response-after@1 not_found` → `read_evidence log-daily-0920@1 ok` → `read_evidence upstream-response-change@1 ok` → `read_evidence daily-report-contract@1 ok`
- `missing_response` 2회차 (`eval-missing_response-2-5bf12900`): `get_run daily-0920-0900 ok` → `list_runs daily-report ok` → `get_run daily-0919-0900 ok` → `read_evidence response-before@1 ok` → `read_evidence response-after@1 not_found` → `read_evidence log-daily-0920@1 ok` → `list_documents daily-report ok` → `read_evidence upstream-response-change@1 ok` → `read_evidence daily-report-contract@1 ok`
- `missing_response` 3회차 (`eval-missing_response-3-d5a57792`): `get_run daily-0920-0900 ok` → `list_runs daily-report ok` → `get_run daily-0919-0900 ok` → `read_evidence response-after@1 not_found` → `read_evidence response-before@1 ok` → `read_evidence log-daily-0920@1 ok` → `list_documents daily-report ok` → `read_evidence upstream-response-change@1 ok` → `read_evidence daily-report-contract@1 ok`
- `missing_change_doc` 1회차 (`eval-missing_change_doc-1-b3dc3ce6`): `get_run daily-0920-0900 ok` → `list_runs daily-report ok` → `get_run daily-0919-0900 ok` → `read_evidence response-before@1 ok` → `read_evidence response-after@1 ok` → `read_evidence log-daily-0920@1 ok` → `list_documents daily-report ok` → `read_evidence upstream-response-change@1 not_found` → `read_evidence daily-report-contract@1 ok`
- `missing_change_doc` 2회차 (`eval-missing_change_doc-2-a488dcdc`): `get_run daily-0920-0900 ok` → `list_runs daily-report ok` → `get_run daily-0919-0900 ok` → `read_evidence response-after@1 ok` → `read_evidence log-daily-0920@1 ok` → `list_documents daily-report ok` → `read_evidence upstream-response-change@1 not_found` → `read_evidence daily-report-contract@1 ok`
- `missing_change_doc` 3회차 (`eval-missing_change_doc-3-6fb90f29`): `get_run daily-0920-0900 ok` → `list_runs daily-report ok` → `get_run daily-0919-0900 ok` → `read_evidence response-after@1 ok` → `read_evidence log-daily-0920@1 ok` → `read_evidence response-before@1 ok` → `list_documents daily-report ok` → `read_evidence upstream-response-change@1 not_found` → `read_evidence daily-report-contract@1 ok`
- `effective_conflict` 1회차 (`eval-effective_conflict-1-6d31f153`): `get_run daily-0920-0900 ok` → `list_runs daily-report ok` → `get_run daily-0919-0900 ok` → `read_evidence response-after@1 ok` → `read_evidence log-daily-0920@1 ok` → `read_evidence response-before@1 ok` → `list_documents daily-report ok` → `read_evidence upstream-response-change@1 ok` → `read_evidence daily-report-contract@1 ok`
- `effective_conflict` 2회차 (`eval-effective_conflict-2-bfc5bca6`): `get_run daily-0920-0900 ok` → `list_runs daily-report ok` → `get_run daily-0919-0900 ok` → `read_evidence response-after@1 ok` → `read_evidence log-daily-0920@1 ok` → `read_evidence response-before@1 ok` → `list_documents daily-report ok` → `read_evidence upstream-response-change@1 ok` → `read_evidence daily-report-contract@1 ok`
- `effective_conflict` 3회차 (`eval-effective_conflict-3-ac5c1127`): `get_run daily-0920-0900 ok` → `list_runs daily-report ok` → `list_documents daily-report ok` → `read_evidence response-after@1 ok` → `read_evidence log-daily-0920@1 ok` → `read_evidence upstream-response-change@1 ok` → `read_evidence daily-report-contract@1 ok`
- `http_error_input` 1회차 (`eval-http_error_input-1-d70f28ee`): `get_run daily-0920-0900 ok` → `list_runs daily-report ok` → `get_run daily-0919-0900 ok` → `read_evidence log-daily-0920@1 ok` → `read_evidence response-before@1 ok` → `list_documents daily-report ok` → `read_evidence upstream-response-change@1 ok` → `read_evidence daily-report-contract@1 ok`
- `http_error_input` 2회차 (`eval-http_error_input-2-e19816ca`): `get_run daily-0920-0900 ok` → `list_runs daily-report ok` → `get_run daily-0919-0900 ok` → `read_evidence log-daily-0920@1 ok` → `read_evidence log-daily-0919@1 ok` → `read_evidence response-before@1 ok` → `list_documents daily-report ok` → `read_evidence upstream-response-change@1 ok` → `read_evidence daily-report-contract@1 ok`
- `http_error_input` 3회차 (`eval-http_error_input-3-d3495dac`): `get_run daily-0920-0900 ok` → `list_runs daily-report ok` → `get_run daily-0919-0900 ok` → `read_evidence log-daily-0920@1 ok` → `read_evidence response-before@1 ok` → `list_documents daily-report ok` → `read_evidence upstream-response-change@1 ok` → `read_evidence daily-report-contract@1 ok`
