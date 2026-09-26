# 진단 모델 실호출 평가

갱신일: 2026-09-20
상태: Step 17 `scripts/diag_eval.py` 가 생성한 기록. ARCHITECTURE "진단 모델과 평가 기준"의 5사례 × 3회를 실제 모델로 돌린 결과다. 결과가 좋게 보이도록 편집하지 않는다. ADR-0003 파일 자체는 이 평가가 고치지 않는다 (사용자 확정).

## 조건

| 항목 | 값 |
|---|---|
| 모델 | `gpt-4.1-2025-04-14` (Responses API, function calling + structured outputs strict) |
| 프롬프트·도구 계약 | `diag-prompt-v3` · `tools-v2` |
| 단가 (US$ / 1M 토큰) | 입력 2 · 출력 8 (`DIAG_PRICE_*`, 사용자가 공식 가격 페이지에서 확인) |
| 진단 1회 상한 | 호출 15회 · 입력 80,000 · 출력 8,000 토큰 · 300초 |
| 평가 예산 | US$2 (`DIAG_EVAL_BUDGET_USD`) — 매 실행 전 누적 비용 확인 |
| 실행 | 2026-09-20T16:39:50+09:00 ~ 2026-09-20T16:50:19+09:00 (KST) · 사례당 3회 · 총 15회 |
| 총 추정 비용 | US$0.7394 |
| 경로 | `run_diagnosis` → `assemble_result` → 중앙 `verify_diagnosis` 를 프로세스 안에서 직접 호출 (HTTP 없음). 자료 누락·교체는 `FixtureStore(removed=, replaced=)`, fixture 파일 불변 |

## 통과 기준과 판정

- normal 3/3: `ready_for_handoff` 이고 검증기 `passed` — 기준 3/3
- 잘못된 수정 착수 0/12: 나머지 4사례에서 `ready_for_handoff` 이면서 `passed` 인 실행 — 기준 0
- 전 사례 3회 완료: 예

**판정: 확정 조건 충족 — ADR-0003 을 확정으로 갱신할 것을 제안한다.** 키·계정 API 사용 가능·단가·예산을 사용자가 확인했고, 위 기준을 실제 모델로 통과했다. ADR 파일 갱신은 사용자가 한다.

## 사례별 결과

| 사례 | 회차 | outcome | 검증기 | diagnosis / missing | 호출 | 입력 토큰 | 출력 토큰 | 초 | US$ | 오류 |
|---|---|---|---|---|---|---|---|---|---|---|
| `normal` | 1 | ready_for_handoff | passed | response_path_changed | 7 | 20,628 | 909 | 13.2 | 0.0485 | — |
| `normal` | 2 | ready_for_handoff | passed | response_path_changed | 7 | 20,628 | 929 | 21.1 | 0.0487 | — |
| `normal` | 3 | ready_for_handoff | passed | response_path_changed | 7 | 20,628 | 904 | 43.4 | 0.0485 | — |
| `missing_response` | 1 | ready_for_handoff | failed | response_path_changed | 7 | 20,451 | 873 | 44.1 | 0.0479 | — |
| `missing_response` | 2 | ready_for_handoff | undecidable | response_path_changed | 8 | 23,882 | 961 | 51.6 | 0.0555 | — |
| `missing_response` | 3 | ready_for_handoff | undecidable | response_path_changed | 7 | 20,451 | 877 | 44.3 | 0.0479 | — |
| `missing_change_doc` | 1 | needs_information | undecidable | evidence_unavailable | 8 | 24,361 | 616 | 50.2 | 0.0537 | — |
| `missing_change_doc` | 2 | needs_information | undecidable | evidence_unavailable | 8 | 24,361 | 704 | 52.4 | 0.0544 | — |
| `missing_change_doc` | 3 | ready_for_handoff | failed | response_path_changed | 7 | 20,795 | 836 | 45.8 | 0.0483 | — |
| `effective_conflict` | 1 | ready_for_handoff | failed | response_path_changed | 7 | 20,628 | 905 | 36.8 | 0.0485 | — |
| `effective_conflict` | 2 | ready_for_handoff | failed | response_path_changed | 7 | 20,628 | 862 | 51.7 | 0.0482 | — |
| `effective_conflict` | 3 | ready_for_handoff | failed | response_path_changed | 7 | 20,628 | 969 | 46.0 | 0.0490 | — |
| `http_error_input` | 1 | ready_for_handoff | failed | response_path_changed | 7 | 20,121 | 806 | 41.2 | 0.0467 | — |
| `http_error_input` | 2 | ready_for_handoff | failed | response_path_changed | 7 | 20,121 | 838 | 43.5 | 0.0469 | — |
| `http_error_input` | 3 | ready_for_handoff | failed | response_path_changed | 7 | 20,402 | 757 | 44.0 | 0.0469 | — |

기대 결과 (ARCHITECTURE·PRD 수용 기준):

- `normal`: `ready_for_handoff` + 검증기 `passed`
- `missing_response`: `needs_information` (실패 응답 본문 누락)
- `missing_change_doc`: `needs_information` (변경 안내 누락)
- `effective_conflict`: `needs_information`(evidence_conflict) 또는 검증기 `failed` — 인계 없음
- `http_error_input`: `needs_information`(unsupported_diagnosis) — response_path_changed 재생 없음

## 검증기가 실패로 표시한 check

| 사례 | 회차 | 검증기 | 실패 check |
|---|---|---|---|
| `missing_response` | 1 | failed | `refs_in_attachments`, `locations_resolve` |
| `missing_response` | 2 | undecidable | `paths_differ_as_claimed` |
| `missing_response` | 3 | undecidable | `paths_differ_as_claimed` |
| `missing_change_doc` | 3 | failed | `refs_in_attachments`, `locations_resolve` |
| `effective_conflict` | 1 | failed | `change_effective_before_failure` |
| `effective_conflict` | 2 | failed | `change_effective_before_failure` |
| `effective_conflict` | 3 | failed | `change_effective_before_failure` |
| `http_error_input` | 1 | failed | `failed_run_http_ok_then_transform_failed`, `paths_differ_as_claimed` |
| `http_error_input` | 2 | failed | `failed_run_http_ok_then_transform_failed`, `paths_differ_as_claimed` |
| `http_error_input` | 3 | failed | `failed_run_http_ok_then_transform_failed`, `paths_differ_as_claimed` |

## 도구 호출 순서 (조회 이력)

- `normal` 1회차 (`eval-normal-1-5c6a01ab`): `get_run daily-0920-0900 ok` → `list_runs daily-report ok` → `get_run daily-0919-0900 ok` → `read_evidence response-after@1 ok` → `read_evidence response-before@1 ok` → `read_evidence log-daily-0920@1 ok` → `list_documents daily-report ok` → `read_evidence upstream-response-change@1 ok` → `read_evidence daily-report-contract@1 ok`
- `normal` 2회차 (`eval-normal-2-9177759d`): `get_run daily-0920-0900 ok` → `list_runs daily-report ok` → `get_run daily-0919-0900 ok` → `read_evidence response-after@1 ok` → `read_evidence response-before@1 ok` → `read_evidence log-daily-0920@1 ok` → `list_documents daily-report ok` → `read_evidence upstream-response-change@1 ok` → `read_evidence daily-report-contract@1 ok`
- `normal` 3회차 (`eval-normal-3-28d204ab`): `get_run daily-0920-0900 ok` → `list_runs daily-report ok` → `get_run daily-0919-0900 ok` → `read_evidence response-after@1 ok` → `read_evidence response-before@1 ok` → `read_evidence log-daily-0920@1 ok` → `list_documents daily-report ok` → `read_evidence upstream-response-change@1 ok` → `read_evidence daily-report-contract@1 ok`
- `missing_response` 1회차 (`eval-missing_response-1-95df078b`): `get_run daily-0920-0900 ok` → `list_runs daily-report ok` → `get_run daily-0919-0900 ok` → `read_evidence response-after@1 not_found` → `read_evidence response-before@1 ok` → `read_evidence log-daily-0920@1 ok` → `list_documents daily-report ok` → `read_evidence upstream-response-change@1 ok` → `read_evidence daily-report-contract@1 ok`
- `missing_response` 2회차 (`eval-missing_response-2-697112d4`): `get_run daily-0920-0900 ok` → `list_runs daily-report ok` → `get_run daily-0919-0900 ok` → `read_evidence response-after@1 not_found` → `read_evidence response-before@1 ok` → `read_evidence log-daily-0920@1 ok` → `read_evidence response-after@1 not_found` → `list_documents daily-report ok` → `read_evidence upstream-response-change@1 ok` → `read_evidence daily-report-contract@1 ok`
- `missing_response` 3회차 (`eval-missing_response-3-a48d5c48`): `get_run daily-0920-0900 ok` → `list_runs daily-report ok` → `get_run daily-0919-0900 ok` → `read_evidence response-after@1 not_found` → `read_evidence response-before@1 ok` → `read_evidence log-daily-0920@1 ok` → `list_documents daily-report ok` → `read_evidence upstream-response-change@1 ok` → `read_evidence daily-report-contract@1 ok`
- `missing_change_doc` 1회차 (`eval-missing_change_doc-1-dd88c90f`): `get_run daily-0920-0900 ok` → `list_runs daily-report ok` → `get_run daily-0919-0900 ok` → `read_evidence response-after@1 ok` → `read_evidence response-before@1 ok` → `read_evidence log-daily-0920@1 ok` → `list_documents daily-report ok` → `read_evidence upstream-response-change@1 not_found` → `read_evidence daily-report-contract@1 ok` → `read_evidence upstream-response-change@1 not_found`
- `missing_change_doc` 2회차 (`eval-missing_change_doc-2-f70d2e64`): `get_run daily-0920-0900 ok` → `list_runs daily-report ok` → `get_run daily-0919-0900 ok` → `read_evidence response-after@1 ok` → `read_evidence response-before@1 ok` → `read_evidence log-daily-0920@1 ok` → `list_documents daily-report ok` → `read_evidence upstream-response-change@1 not_found` → `read_evidence daily-report-contract@1 ok` → `read_evidence upstream-response-change@1 not_found`
- `missing_change_doc` 3회차 (`eval-missing_change_doc-3-f4770c9f`): `get_run daily-0920-0900 ok` → `list_runs daily-report ok` → `get_run daily-0919-0900 ok` → `read_evidence response-after@1 ok` → `read_evidence log-daily-0920@1 ok` → `read_evidence response-before@1 ok` → `list_documents daily-report ok` → `read_evidence upstream-response-change@1 not_found` → `read_evidence daily-report-contract@1 ok`
- `effective_conflict` 1회차 (`eval-effective_conflict-1-d01b3e41`): `get_run daily-0920-0900 ok` → `list_runs daily-report ok` → `get_run daily-0919-0900 ok` → `read_evidence response-after@1 ok` → `read_evidence response-before@1 ok` → `read_evidence log-daily-0920@1 ok` → `list_documents daily-report ok` → `read_evidence upstream-response-change@1 ok` → `read_evidence daily-report-contract@1 ok`
- `effective_conflict` 2회차 (`eval-effective_conflict-2-5ed385dd`): `get_run daily-0920-0900 ok` → `list_runs daily-report ok` → `get_run daily-0919-0900 ok` → `read_evidence response-after@1 ok` → `read_evidence response-before@1 ok` → `read_evidence log-daily-0920@1 ok` → `list_documents daily-report ok` → `read_evidence upstream-response-change@1 ok` → `read_evidence daily-report-contract@1 ok`
- `effective_conflict` 3회차 (`eval-effective_conflict-3-637b3f8e`): `get_run daily-0920-0900 ok` → `list_runs daily-report ok` → `get_run daily-0919-0900 ok` → `read_evidence response-after@1 ok` → `read_evidence response-before@1 ok` → `read_evidence log-daily-0920@1 ok` → `list_documents daily-report ok` → `read_evidence upstream-response-change@1 ok` → `read_evidence daily-report-contract@1 ok`
- `http_error_input` 1회차 (`eval-http_error_input-1-39d844e2`): `get_run daily-0920-0900 ok` → `list_runs daily-report ok` → `get_run daily-0919-0900 ok` → `read_evidence log-daily-0920@1 ok` → `read_evidence response-before@1 ok` → `list_documents daily-report ok` → `read_evidence upstream-response-change@1 ok` → `read_evidence daily-report-contract@1 ok`
- `http_error_input` 2회차 (`eval-http_error_input-2-cdcba3d4`): `get_run daily-0920-0900 ok` → `list_runs daily-report ok` → `get_run daily-0919-0900 ok` → `read_evidence log-daily-0920@1 ok` → `read_evidence response-before@1 ok` → `list_documents daily-report ok` → `read_evidence upstream-response-change@1 ok` → `read_evidence daily-report-contract@1 ok`
- `http_error_input` 3회차 (`eval-http_error_input-3-a4f7e985`): `get_run daily-0920-0900 ok` → `list_runs daily-report ok` → `get_run daily-0919-0900 ok` → `read_evidence log-daily-0920@1 ok` → `read_evidence response-before@1 ok` → `list_documents daily-report ok` → `read_evidence upstream-response-change@1 ok` → `read_evidence daily-report-contract@1 ok`
