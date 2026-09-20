# 진단 데모 가상 자료

가상 데모 자료. 미리디 공고에서 착안한 시나리오이며 실제 기업의 데이터·장애가 아니다.

일일 보고서 자동화(`daily-report`)에서 외부 집계 API 의 응답 형식이 바뀌어 변환 단계가 실패한 사건을 재현한다.
실행 기록·응답·로그·보고서·운영 문서는 모두 이 시나리오를 위해 만든 값이며 실제 운영 데이터로 표시하지 않는다.

## 구성

- `index.json` — 자동화 목록(`workflows`), 실행 요약(`runs`), 문서 목록(`documents`), 확장자별 `content_types`
- `evidence/<evidence_id>/<version>.<json|txt>` — 조회 도구 `read_evidence` 가 돌려주는 원문. 버전은 모두 `1`

| evidence_id | 형식 | 내용 |
|---|---|---|
| `run-daily-0919-0900` | json | 2026-09-19 09:00 정상 실행 기록 |
| `run-daily-0920-0900` | json | 2026-09-20 09:00 실패 실행 기록 (변환 단계 `MISSING_RECORDS_FIELD`) |
| `response-before` | json | 정상 실행이 받은 집계 응답 (`items`) |
| `response-after` | json | 실패 실행이 받은 집계 응답 (`data.records`) |
| `log-daily-0919` | txt | 정상 실행 로그 |
| `log-daily-0920` | txt | 실패 실행 로그 |
| `report-0918` | txt | 정상 실행이 생성한 2026-09-18 보고서 |
| `daily-report-runbook` | json | 운영 문서 `{markdown, machine}` — 실행 순서와 실패 시 동작 |
| `upstream-response-change` | json | 운영 문서 — 집계 API 제공자의 응답 형식 변경 안내 |
| `daily-report-contract` | json | 운영 문서 — 보고서 입력·출력 계약 |

실행 기록·문서 `machine` 의 필드는 `phases/0-mvp/step3.md` "실행 기록·문서 첨부의 형식" 을 따른다. 응답·로그·보고서는
`docs/PRD.md` "데모 데이터와 검증 상세" 의 값 그대로다.
