# 진단 모델 실호출 평가

갱신일: 2026-09-20
상태: Step 17 `scripts/diag_eval.py` 가 생성한 기록에 "실행 이력"·"관찰"·"분리 분석"·"ADR-0003 확정 여부" 절을 손으로 더했다(스크립트를 다시 돌리면 생성 절만 덮어쓴다). ARCHITECTURE "진단 모델과 평가 기준"의 5사례 × 3회를 실제 모델로 돌린 결과다. 결과가 좋게 보이도록 편집하지 않는다. ADR-0003 파일 자체는 이 평가가 고치지 않는다 (사용자 확정).

## 조건

| 항목 | 값 |
|---|---|
| 모델 | `gpt-4.1-mini-2025-04-14` (Responses API, function calling + structured outputs strict) |
| 프롬프트·도구 계약 | `diag-prompt-v1` · `tools-v1` |
| 단가 (US$ / 1M 토큰) | 입력 0.4 · 출력 1.6 (`DIAG_PRICE_*`, 사용자가 공식 가격 페이지에서 확인) |
| 진단 1회 상한 | 호출 15회 · 입력 80,000 · 출력 8,000 토큰 · 300초 |
| 평가 예산 | US$2 (`DIAG_EVAL_BUDGET_USD`) — 매 실행 전 누적 비용 확인 |
| 실행 | 2026-09-20T15:01:15+09:00 ~ 2026-09-20T15:04:44+09:00 (KST) · 사례당 3회 · 총 15회 |
| 총 추정 비용 | US$0.1147 |
| 경로 | `run_diagnosis` → `assemble_result` → 중앙 `verify_diagnosis` 를 프로세스 안에서 직접 호출 (HTTP 없음). 자료 누락·교체는 `FixtureStore(removed=, replaced=)`, fixture 파일 불변 |

## 통과 기준과 판정

- normal 1/3: `ready_for_handoff` 이고 검증기 `passed` — 기준 3/3
- 잘못된 수정 착수 0/12: 나머지 4사례에서 `ready_for_handoff` 이면서 `passed` 인 실행 — 기준 0
- 전 사례 3회 완료: 예

**판정: 확정 보류.** 기준 미충족 실행:

- normal 1회차: outcome=— 검증기=— 오류 `model_output_invalid: 초안이 DiagnosisDraft 형식이 아닙니다: 2개 오류 — findings.0.evidence_refs.0.location='$.stages[1].error_code'; findings.1.evidence_refs.0.location='$.stages[1].status'`
- normal 3회차: outcome=ready_for_handoff 검증기=failed 실패 check `locations_resolve`

도구 반환·계약·프롬프트 중 어디가 문제인지의 분리 분석과 모델 교체 필요 여부는 아래 절에 적는다.

## 실행 이력

같은 스크립트·같은 프롬프트(`diag-prompt-v1`)·같은 도구 계약(`tools-v1`)으로 세 번 호출했다. 위 표는 **2차** 결과다. 프롬프트·도구·fixture 는 세 실행 사이에 바꾸지 않았다. 바꾼 것은 스크립트의 기록 기능 하나뿐이다(1차에서 `model_output_invalid` 의 원문이 남지 않아 원인을 가를 수 없었으므로, 2차 전에 계약 검증기가 거부한 초안의 원문·오류 위치를 `workdir/invalid-drafts/` 에 보존하도록 했다 — `scripts/test_diag_eval.py::test_run_case_preserves_invalid_draft_for_analysis`).

| 실행 | 명령 | 결과 | 비용 |
|---|---|---|---|
| 사전 확인 (14:56 KST) | `--cases normal --repeat 1` | SDK·strict 스키마 호환 확인(400 없음). `ready_for_handoff`, 검증기 `failed`(`locations_resolve`: `log-daily-0920 lines:2-5`, 로그는 4줄) | US$0.0093 |
| 1차 (14:57~15:00 KST) | `--cases all --repeat 3` | normal 1/3 · 잘못된 수정 착수 0/12 · `model_output_invalid` 7/15 (원문 미보존) | US$0.1023 |
| 2차 (15:01~15:04 KST) | `--cases all --repeat 3` | normal 1/3 · 잘못된 수정 착수 0/12 · `model_output_invalid` 5/15 (원문 보존) — 위 표 | US$0.1147 |
| **합계** | 31회 | | **US$0.2263** (평가 예산 US$2 의 11%) |

원본: `data/diag-eval/2026-09-20T145751+0900/`(1차), `data/diag-eval/2026-09-20T150115+0900/`(2차) — 각각 `results.json`, 진단 서비스 DB·산출물(`diagnosis_result`·`tool_trace`·`evidence`), 2차는 `invalid-drafts/`. `data/` 는 git 에 들어가지 않는다.

### 1차 실행 결과 (원문 보존 전)

| 사례 | 회차 | outcome | 검증기 | 실패 check / diagnosis | 호출 | 입력 토큰 | 출력 토큰 | 초 | US$ | 오류 |
|---|---|---|---|---|---|---|---|---|---|---|
| `normal` | 1 | — | — | — | 6 | 15,197 | 257 | 15.3 | 0.0065 | model_output_invalid: 4개 오류 |
| `normal` | 2 | ready_for_handoff | failed | `locations_resolve` | 7 | 19,071 | 991 | 12.4 | 0.0092 | — |
| `normal` | 3 | ready_for_handoff | passed | response_path_changed | 7 | 19,809 | 719 | 15.5 | 0.0091 | — |
| `missing_response` | 1 | ready_for_handoff | failed | `locations_resolve` | 6 | 15,848 | 1,002 | 12.0 | 0.0079 | — |
| `missing_response` | 2 | ready_for_handoff | undecidable | `paths_differ_as_claimed`(첨부 없음) | 8 | 21,962 | 1,029 | 15.6 | 0.0104 | — |
| `missing_response` | 3 | — | — | — | 6 | 15,573 | 257 | 13.7 | 0.0066 | model_output_invalid: 1개 오류 |
| `missing_change_doc` | 1 | — | — | — | 1 | 1,822 | 82 | 4.1 | 0.0009 | model_output_invalid: 1개 오류 (도구 호출 없이 첫 턴에 초안) |
| `missing_change_doc` | 2 | ready_for_handoff | failed | `refs_in_attachments`, `locations_resolve` | 7 | 18,814 | 942 | 12.2 | 0.0090 | — |
| `missing_change_doc` | 3 | — | — | — | 6 | 15,197 | 257 | 13.2 | 0.0065 | model_output_invalid: 1개 오류 |
| `effective_conflict` | 1 | ready_for_handoff | failed | `change_effective_before_failure` | 7 | 19,258 | 901 | 14.9 | 0.0091 | — |
| `effective_conflict` | 2 | — | — | — | 6 | 15,197 | 257 | 14.2 | 0.0065 | model_output_invalid: 2개 오류 |
| `effective_conflict` | 3 | ready_for_handoff | failed | `refs_in_attachments`, `locations_resolve` | 5 | 12,973 | 1,051 | 12.8 | 0.0069 | — |
| `http_error_input` | 1 | — | — | — | 5 | 11,868 | 250 | 13.4 | 0.0051 | model_output_invalid: 1개 오류 |
| `http_error_input` | 2 | — | — | — | 1 | 1,822 | 82 | 4.9 | 0.0009 | model_output_invalid: 1개 오류 (첫 턴에 초안) |
| `http_error_input` | 3 | ready_for_handoff | failed | `locations_resolve` | 6 | 15,580 | 875 | 13.1 | 0.0076 | — |

1차의 `model_output_invalid` 는 원문이 없어 오류 위치를 확정할 수 없다. 2차에서 같은 오류 5건이 모두 `location` 문법(배열 인덱스 경로)이었고 1차의 오류 수(1~4개)·토큰 수(같은 궤적에서 257 출력 토큰이 3회 반복)가 2차와 일치하므로 같은 원인으로 본다. 확정은 아니다.


## 사례별 결과 — 2차

| 사례 | 회차 | outcome | 검증기 | diagnosis / missing | 호출 | 입력 토큰 | 출력 토큰 | 초 | US$ | 오류 |
|---|---|---|---|---|---|---|---|---|---|---|
| `normal` | 1 | — | — | — | 6 | 15,197 | 257 | 12.6 | 0.0065 | model_output_invalid: 초안이 DiagnosisDraft 형식이 아닙니다: 2개 오류 — findings.0.evidence_refs.0.location='$.stages[1].error_code'; findings.1.evidence_refs.0.location='$.stages[1].status' |
| `normal` | 2 | ready_for_handoff | passed | response_path_changed | 7 | 19,258 | 936 | 13.7 | 0.0092 | — |
| `normal` | 3 | ready_for_handoff | failed | response_path_changed | 7 | 18,696 | 877 | 13.9 | 0.0089 | — |
| `missing_response` | 1 | — | — | — | 5 | 11,972 | 234 | 14.2 | 0.0052 | model_output_invalid: 초안이 DiagnosisDraft 형식이 아닙니다: 2개 오류 — findings.0.evidence_refs.0.location='$.stages[1]'; findings.1.evidence_refs.2.location='$.keys()' |
| `missing_response` | 2 | ready_for_handoff | failed | response_path_changed | 6 | 15,665 | 1,027 | 13.4 | 0.0079 | — |
| `missing_response` | 3 | — | — | — | 5 | 12,088 | 272 | 13.7 | 0.0053 | model_output_invalid: 초안이 DiagnosisDraft 형식이 아닙니다: 2개 오류 — findings.0.evidence_refs.0.location='$.stages[1]'; findings.0.evidence_refs.2.location='$' |
| `missing_change_doc` | 1 | ready_for_handoff | failed | response_path_changed | 7 | 18,744 | 804 | 11.5 | 0.0088 | — |
| `missing_change_doc` | 2 | — | — | — | 3 | 6,358 | 232 | 10.5 | 0.0029 | model_output_invalid: 초안이 DiagnosisDraft 형식이 아닙니다: 1개 오류 — findings.1.evidence_refs.1.location='$.stages[1].status' |
| `missing_change_doc` | 3 | ready_for_handoff | failed | response_path_changed | 7 | 18,814 | 976 | 12.7 | 0.0091 | — |
| `effective_conflict` | 1 | ready_for_handoff | failed | response_path_changed | 7 | 19,071 | 911 | 12.7 | 0.0091 | — |
| `effective_conflict` | 2 | ready_for_handoff | failed | response_path_changed | 7 | 19,998 | 979 | 21.3 | 0.0096 | — |
| `effective_conflict` | 3 | ready_for_handoff | failed | response_path_changed | 7 | 19,971 | 895 | 18.6 | 0.0094 | — |
| `http_error_input` | 1 | ready_for_handoff | failed | response_path_changed | 6 | 15,354 | 723 | 11.5 | 0.0073 | — |
| `http_error_input` | 2 | — | — | — | 6 | 15,256 | 235 | 15.2 | 0.0065 | model_output_invalid: 초안이 DiagnosisDraft 형식이 아닙니다: 1개 오류 — findings.0.evidence_refs.0.location='$.stages[0]' |
| `http_error_input` | 3 | ready_for_handoff | failed | response_path_changed | 7 | 18,974 | 981 | 13.1 | 0.0092 | — |

기대 결과 (ARCHITECTURE·PRD 수용 기준):

- `normal`: `ready_for_handoff` + 검증기 `passed`
- `missing_response`: `needs_information` (실패 응답 본문 누락)
- `missing_change_doc`: `needs_information` (변경 안내 누락)
- `effective_conflict`: `needs_information`(evidence_conflict) 또는 검증기 `failed` — 인계 없음
- `http_error_input`: `needs_information`(unsupported_diagnosis) — response_path_changed 재생 없음

## 검증기가 실패로 표시한 check

| 사례 | 회차 | 검증기 | 실패 check |
|---|---|---|---|
| `normal` | 3 | failed | `locations_resolve` |
| `missing_response` | 2 | failed | `locations_resolve` |
| `missing_change_doc` | 1 | failed | `change_document_matches`, `change_effective_before_failure` |
| `missing_change_doc` | 3 | failed | `refs_in_attachments`, `locations_resolve` |
| `effective_conflict` | 1 | failed | `locations_resolve` |
| `effective_conflict` | 2 | failed | `change_effective_before_failure` |
| `effective_conflict` | 3 | failed | `change_effective_before_failure` |
| `http_error_input` | 1 | failed | `locations_resolve` |
| `http_error_input` | 3 | failed | `locations_resolve` |

## 도구 호출 순서 (조회 이력)

- `normal` 1회차 (`eval-normal-1-2219be34`): `get_run daily-0920-0900 ok` → `list_runs daily-report ok` → `get_run daily-0919-0900 ok` → `read_evidence response-after@1 ok` → `read_evidence log-daily-0920@1 ok` → `read_evidence response-before@1 ok` → `list_documents daily-report ok` → `read_evidence upstream-response-change@1 ok` → `read_evidence daily-report-contract@1 ok`
- `normal` 2회차 (`eval-normal-2-3760c473`): `get_run daily-0920-0900 ok` → `list_runs daily-report ok` → `get_run daily-0919-0900 ok` → `read_evidence response-before@1 ok` → `read_evidence response-after@1 ok` → `read_evidence log-daily-0920@1 ok` → `list_documents daily-report ok` → `read_evidence upstream-response-change@1 ok` → `read_evidence daily-report-contract@1 ok`
- `normal` 3회차 (`eval-normal-3-b2018302`): `get_run daily-0920-0900 ok` → `list_runs daily-report ok` → `get_run daily-0919-0900 ok` → `read_evidence response-before@1 ok` → `read_evidence response-after@1 ok` → `read_evidence log-daily-0920@1 ok` → `list_documents daily-report ok` → `read_evidence upstream-response-change@1 ok` → `read_evidence daily-report-contract@1 ok`
- `missing_response` 1회차 (`eval-missing_response-1-5e4c0928`): `get_run daily-0920-0900 ok` → `list_runs daily-report ok` → `get_run daily-0919-0900 ok` → `read_evidence response-after@1 not_found` → `read_evidence log-daily-0920@1 ok` → `list_documents daily-report ok` → `read_evidence upstream-response-change@1 ok` → `read_evidence daily-report-contract@1 ok`
- `missing_response` 2회차 (`eval-missing_response-2-a40c2e7f`): `get_run daily-0920-0900 ok` → `list_runs daily-report ok` → `get_run daily-0919-0900 ok` → `read_evidence response-after@1 not_found` → `read_evidence log-daily-0920@1 ok` → `list_documents daily-report ok` → `read_evidence upstream-response-change@1 ok` → `read_evidence daily-report-contract@1 ok`
- `missing_response` 3회차 (`eval-missing_response-3-c94a240c`): `get_run daily-0920-0900 ok` → `list_runs daily-report ok` → `get_run daily-0919-0900 ok` → `read_evidence response-after@1 not_found` → `read_evidence log-daily-0920@1 ok` → `read_evidence response-before@1 ok` → `list_documents daily-report ok` → `read_evidence upstream-response-change@1 ok` → `read_evidence daily-report-contract@1 ok`
- `missing_change_doc` 1회차 (`eval-missing_change_doc-1-58279970`): `get_run daily-0920-0900 ok` → `list_runs daily-report ok` → `get_run daily-0919-0900 ok` → `read_evidence response-before@1 ok` → `read_evidence response-after@1 ok` → `read_evidence log-daily-0920@1 ok` → `list_documents daily-report ok` → `read_evidence upstream-response-change@1 not_found` → `read_evidence daily-report-contract@1 ok`
- `missing_change_doc` 2회차 (`eval-missing_change_doc-2-00fc702b`): `get_run daily-0920-0900 ok` → `list_runs daily-report ok` → `list_documents daily-report ok` → `get_run daily-0919-0900 ok` → `read_evidence response-after@1 ok` → `read_evidence log-daily-0920@1 ok` → `read_evidence upstream-response-change@1 not_found` → `read_evidence daily-report-contract@1 ok`
- `missing_change_doc` 3회차 (`eval-missing_change_doc-3-7c1f5a57`): `get_run daily-0920-0900 ok` → `list_runs daily-report ok` → `get_run daily-0919-0900 ok` → `read_evidence response-after@1 ok` → `read_evidence log-daily-0920@1 ok` → `read_evidence response-before@1 ok` → `list_documents daily-report ok` → `read_evidence upstream-response-change@1 not_found` → `read_evidence daily-report-contract@1 ok`
- `effective_conflict` 1회차 (`eval-effective_conflict-1-e1b50e8f`): `get_run daily-0920-0900 ok` → `list_runs daily-report ok` → `get_run daily-0919-0900 ok` → `read_evidence response-after@1 ok` → `read_evidence log-daily-0920@1 ok` → `read_evidence response-before@1 ok` → `list_documents daily-report ok` → `read_evidence upstream-response-change@1 ok` → `read_evidence daily-report-contract@1 ok`
- `effective_conflict` 2회차 (`eval-effective_conflict-2-b384e8b2`): `get_run daily-0920-0900 ok` → `list_runs daily-report ok` → `get_run daily-0919-0900 ok` → `read_evidence response-before@1 ok` → `read_evidence response-after@1 ok` → `read_evidence log-daily-0920@1 ok` → `list_documents daily-report ok` → `read_evidence upstream-response-change@1 ok` → `read_evidence daily-report-contract@1 ok`
- `effective_conflict` 3회차 (`eval-effective_conflict-3-b80225f8`): `get_run daily-0920-0900 ok` → `list_runs daily-report ok` → `get_run daily-0919-0900 ok` → `read_evidence response-before@1 ok` → `read_evidence response-after@1 ok` → `read_evidence log-daily-0920@1 ok` → `list_documents daily-report ok` → `read_evidence upstream-response-change@1 ok` → `read_evidence daily-report-contract@1 ok`
- `http_error_input` 1회차 (`eval-http_error_input-1-3379cb28`): `get_run daily-0920-0900 ok` → `list_runs daily-report ok` → `get_run daily-0919-0900 ok` → `read_evidence response-before@1 ok` → `read_evidence log-daily-0920@1 ok` → `list_documents daily-report ok` → `read_evidence upstream-response-change@1 ok` → `read_evidence daily-report-contract@1 ok`
- `http_error_input` 2회차 (`eval-http_error_input-2-cbe8006e`): `get_run daily-0920-0900 ok` → `list_runs daily-report ok` → `get_run daily-0919-0900 ok` → `read_evidence log-daily-0920@1 ok` → `read_evidence response-before@1 ok` → `list_documents daily-report ok` → `read_evidence upstream-response-change@1 ok` → `read_evidence daily-report-contract@1 ok`
- `http_error_input` 3회차 (`eval-http_error_input-3-ed3115f8`): `get_run daily-0920-0900 ok` → `list_runs daily-report ok` → `get_run daily-0919-0900 ok` → `read_evidence log-daily-0920@1 ok` → `read_evidence response-before@1 ok` → `list_documents daily-report ok` → `read_evidence upstream-response-change@1 ok` → `read_evidence daily-report-contract@1 ok`

## 관찰 — 두 실행 30회에서 반복된 것

1. **모델은 `needs_information` 을 한 번도 내지 않았다.** outcome 을 확인할 수 있는 23회(유효 초안 18 + 원문을 보존한 2차 계약 거부 5)가 전부 `ready_for_handoff` 였고, 그중 자료를 빼거나 바꾼 4사례 18회도 예외 없이 `response_path_changed` · `$.items` → `$.data.records` · baseline `daily-0919-0900` 을 냈다(1차 계약 거부 7회는 원문이 없어 outcome 미확인). 실패 응답 본문을 읽지 못해도(`missing_response`), 변경 안내가 `not_found` 여도(`missing_change_doc`), 안내의 적용 시각이 실패 실행 뒤여도(`effective_conflict`), 실패 실행이 HTTP 503 으로 조회 단계에서 끝나 응답이 없어도(`http_error_input`) 같은 확정 진단이다. PRD "입력 기록이 달라졌는데도 같은 확정 진단을 내놓으면 수용 기준 실패" 에 해당한다. 프롬프트의 결론 규칙(읽지 못했거나 시각·경로가 맞지 않으면 `needs_information`)을 모델이 따르지 않았다.
2. **읽지 않은 근거를 인용했다 (3회).** `missing_change_doc` 1차 2회차·2차 3회차는 `read_evidence upstream-response-change@1` 이 `not_found` 였는데 그 문서를 인용하고 `change_document` 로 지정했다. `effective_conflict` 1차 3회차는 한 번도 읽지 않은 `response-before` 를 인용했다. 셋 다 검증기 `refs_in_attachments` 가 막았다. `missing_change_doc` 2차 1회차는 대신 `daily-report-contract` 를 `change_document` 로 지정했다(`change_document_matches`·`change_effective_before_failure` 차단).
3. **인용 위치가 자주 틀린다.** 4줄짜리 로그에 `lines:2-5`, `lines:2-6`, `lines:1-6`, `lines:1-7`(원문 밖). 실행 기록의 `stages` 배열에 `$.stages[1].error_code`, `$.stages[1]`, `$.stages[0]`, 그리고 `$.keys()`, `$`(계약 v1 미지원 문법). 두 실행 30회 중 계약 거부 12회(2차 5회는 전부 `location` 문법으로 확인, 1차 7회는 추정) + `locations_resolve` 실패 11회 = 23회가 인용 위치 문제다. 진단 객체(경로·실행·문서)는 확인 가능한 회차에서 정답 사례 기준으로 맞았다 — 자료를 뺀 사례에서도 같은 값이었다는 점이 문제다(1번).
4. **잘못된 수정 착수는 0/24 였다 — 모델이 아니라 검증기 덕분이다.** 4사례 24회 모두 계약 검증기(`Location`, `DiagnosisResult`)나 중앙 검증기(`refs_in_attachments`, `attachments_loaded`, `locations_resolve`, `change_effective_before_failure`, `change_document_matches`, 첨부 부족 `undecidable`)가 막았다. ARCHITECTURE 의 "구조화 출력 성공과 사실 관계 검증 통과는 별개" 가 실제로 필요한 방어선이었다.
5. **도구 호출 자체는 안정적이다.** 조회 이력 30회분에서 결과는 `ok` 와 (제거한 자료의) `not_found` 뿐이고 `access_denied`·`unavailable` 은 없었다. 조사 순서(실패 실행 → `list_runs` → 정상 실행 → 응답·로그 → 문서)는 프롬프트대로였고 5~8회 호출·12k~22k 입력 토큰·11~21초·회당 US$0.005~0.010 이었다. 상한(15회·80k·300초)에는 한 번도 닿지 않았다. strict 스키마·`previous_response_id` 연결·function calling 은 SDK 3.16.2 에서 400 없이 동작했다.

## 분리 분석 — 도구 반환·계약·프롬프트·모델

| 층 | 관찰과 판단 | 고칠 수 있는 것 (이 step 에서 적용하지 않음) |
|---|---|---|
| 도구 반환 (`tools/api.py`, `tools/store.py`, fixture 형식) | 텍스트 자료는 줄 번호 없는 원문 문자열로 돌아가므로 모델이 줄을 세어야 한다 → 4줄 로그에 `lines:2-5`~`1-7`. 실행 기록의 `stages` 는 배열이라 자연스러운 인용이 `$.stages[1]…` 인데 계약이 배열 인덱스를 지원하지 않는다. **인용 위치 문제 23회 중 대부분이 이 층에서 만들어진다.** | 텍스트 자료 반환에 줄 번호(또는 `line_count`)를 붙인다. 실행 기록의 `stages` 를 단계 이름을 키로 하는 객체로 바꾸거나(fixture·Step 3 형식·검증기 함께 변경), 계약 v1 `Location` 에 배열 인덱스를 추가한다(ARCHITECTURE·CONTRACT 갱신 필요). |
| 계약 (`contracts/v1.py`, `worker/model.py`) | `Location` 문법은 Pydantic `AfterValidator` 라 JSON Schema 에 나타나지 않는다. 모델에 보내는 strict 스키마에는 `location: string` 뿐이라 API 가 `$.stages[1]` 을 막을 수 없고, 서비스가 받은 뒤에야 거부한다(12회). OpenAI 공식 문서(structured-outputs 가이드, 2026-09-20 확인)는 strict 스키마의 문자열 `pattern` 을 지원한다. | `draft_json_schema()` 에서 `location` 에 `pattern`(`^\$(\.[A-Za-z_][A-Za-z0-9_-]*)+$|^lines:[1-9][0-9]*-[1-9][0-9]*$` 류)을 넣으면 생성 시점에 문법이 강제된다. 정답이 아니라 문법이므로 원칙 위반이 아니다. `contracts/v1.py` 의 정규식과 한 곳에서 관리해야 한다. |
| 프롬프트 (`worker/prompt.py`) | "객체 경로(와일드카드·필터 없음)" 라고만 적어 배열 인덱스 금지가 명시되지 않았다. "인용한 줄은 원문에 실제로 존재해야 한다" 는 있지만 줄 수 확인 절차가 없다. 결론 규칙은 명시돼 있는데 outcome 을 확인한 4사례 18회 중 0회 준수 — 문구 문제라기보다 모델이 "읽지 못함 → 보류" 를 실행하지 않는 것이다. | 배열 인덱스 금지·`lines` 범위 확인을 문구로 보강할 수 있으나, 결론 규칙 미준수는 프롬프트만으로 해결된다고 기대하기 어렵다. 프롬프트를 바꾸면 `PROMPT_VERSION` 을 올린다. |
| 모델 (`gpt-4.1-mini-2025-04-14`) | 도구 사용·조사 순서·정답 사례의 진단 내용은 안정적이나, (a) 자료 누락·충돌·다른 실패 원인에서 보류하지 않고 같은 확정 진단을 재생하고, (b) 읽지 못한 근거를 인용하며, (c) 줄 범위를 원문 밖으로 잡는다. (a)(b)는 도구·계약 층 수정으로 사라지지 않는다 — 검증기가 계속 막겠지만 A 는 매번 `확인 필요` 로 남아 자동 인계가 성립하지 않는다. | 도구 반환·계약 스키마를 먼저 고친 뒤 같은 5사례로 재평가한다. 그래도 `needs_information` 준수가 0 에 가깝고 `normal` 3/3 이 안 되면 상위 모델(같은 제공자의 `gpt-4.1` 또는 추론 모델)을 같은 하네스로 평가하고 새 ADR 로 결정한다. 실행 중 조용한 대체는 하지 않는다. |

1차 `missing_change_doc` 1회차·`http_error_input` 2회차는 모델 호출 1회 — 도구를 한 번도 부르지 않고 첫 턴에 초안을 냈다(82 출력 토큰, 계약 거부). 도구 호출 결과가 없었으므로 도구 반환·계약 층의 문제가 아니라 모델이 조사 없이 결론을 낸 사례다.

## ADR-0003 확정 여부

**확정 조건 미충족 — 확정 보류.** 키·계정 API 사용 가능·단가·예산은 확인됐고 실호출은 동작하지만, ARCHITECTURE 의 통과 기준(`normal` 3/3 정확한 근거·결과) 을 1차 1/3, 2차 1/3 으로 넘지 못했다. "잘못된 수정 착수 0회" 는 두 실행 모두 충족했으나 이는 검증기의 결과이지 모델의 결과가 아니다.

모델 교체는 아직 결정하지 않는다. 위 표의 도구 반환·계약 스키마 수정은 결정적이고 값싸며 정답을 주입하지 않으므로 먼저 적용하고 같은 하네스(`python3 scripts/diag_eval.py --cases all --repeat 3`)로 재평가한 뒤, 결론 규칙 준수(`needs_information`)와 `normal` 3/3 을 다시 본다. 그 결과로 ADR-0003 확정 또는 대체 ADR 을 사용자가 결정한다. 이 문서는 ADR 파일을 고치지 않는다.

배포 관점: 현재 상태로 심사 데모를 돌리면 A 는 대부분 `확인 필요 · 미충족 항목`(계약 거부는 `실패 · model_output_invalid`)으로 끝나고 B 는 자동 착수하지 않는다. 시연의 자동 인계는 위 수정·재평가 뒤에 기대할 수 있다.
