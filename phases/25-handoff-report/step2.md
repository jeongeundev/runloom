# Step 2: report-store — 스키마 v27 과 repo 함수

## 읽어야 할 파일

- AGENTS.md, phases/25-handoff-report/README.md("결정" 절), phases/25-handoff-report/index.json(이전 step summary)
- docs/adr/0033-handoff-report.md, docs/ARCHITECTURE.md "인계 리포트 — phase 25"(**README 와 다르면 ARCHITECTURE 가 기준** — step 0 이 코드 조사로 고정한 이름·표·경로), docs/CONTRACT.md 19절, docs/GLOSSARY.md phase 25 용어
- `src/workflow/adapters/db.py`(`SCHEMA_VERSION`·마이그레이션 형식·`_in`), `src/workflow/adapters/repo.py`(`_tx`·`claim_execution`·업무 이벤트 기록 함수·`record_runner_capabilities`), `tests/workflow/adapters/test_db.py`·fixtures(`schema_v24.sql` 등 옛 스키마 픽스처 쓰는 법), `tests/workflow/server/test_backup.py`(스키마 버전 단정)
- step 1 결과: `src/workflow/contracts/v1.py` 의 새 모델

## 작업

0. **`ARTIFACT_KINDS`·`db.py` 를 바꾸기 전에** 지금 코드로 만든 v26 스키마를 `tests/workflow/adapters/fixtures/schema_v26.sql` 로 덤프한다(기존 `schema_v24.sql` 이 만들어진 방식과 같게 — 그 파일 머리와 쓰는 테스트를 보고 따른다).
1. `contracts/v1.py` 의 `ARTIFACT_KINDS` 에 `handoff_report` 를 더하고 CONTRACT 4절 kind 목록 줄을 함께 고친다(`test_v1.py::test_artifact_kinds_match_contract_md_list`). `test_v1.py` 의 `ARTIFACT_KINDS` 튜플 전체 단정과 `len(ARTIFACT_KINDS) == 18` 단정도 새 값으로 고친다.
2. `db.py`: `SCHEMA_VERSION = 27`, ARCHITECTURE 표대로 `handoff_reports`·`handoff_report_decisions` 와 인덱스, v26→v27 마이그레이션. **`artifacts` 표를 다시 만든다**(SQLite 는 CHECK 를 바꿀 수 없다 — `_migrate_4_to_5` 방식: 새 표 생성·행 복사·교체, 인덱스·FK 그대로) — 이로써 v15 이후 마이그레이션 DB 에 빠져 있던 `triage_result` 도 들어간다(README D38). 기존 결과에 리포트를 채워 넣지 않는다(D41). 업무 이벤트 type CHECK 에 `report_decision` 등 ARCHITECTURE 가 정한 값이 있으면 넓힌다(기존 재생성 방식 선례 그대로).
3. `repo.py`(또는 ARCHITECTURE 가 정한 `adapters/` 모듈)에 리포트 작업 함수: 만들기(original·tailored, 진행 중 중복 방지 — 같은 키면 기존 행 반환), 불가 기록(`unavailable`+사유), connector 의 queued 작업 하나 가져오기·`leased` 로, 완료(`done`+artifact_id)·실패·시간 초과 일괄 처리(기준 시각 인자), 원인 실행의 최신 original 조회, 업무의 단계별 리포트 목록, 결정 답 쓰기(덮어쓰기 + 이벤트)·읽기, tailored 최신 조회(D8). 시그니처는 ARCHITECTURE 이름 그대로.
4. 모든 시각은 인자 `now` 로 받는다(테스트 가능).

## 테스트

- 마이그레이션: v26 DB(기존 픽스처 방식)를 v27 로 올려 표·제약 확인. 새 DB 생성도.
- 진행 중 original 중복 생성 → 같은 행. 다시 만들기(이전 행 `failed`/`done`) → 새 행.
- `leased` 전이는 한 번만(두 번째 가져오기는 없음), 시간 초과 처리 경계(정확히 30분).
- 결정 답 덮어쓰기와 이벤트 기록, `changed` 인데 note 없음 거절.
- 스키마 버전 26 을 단정하는 기존 테스트(`test_db.py`·`test_backup.py`·`test_app.py` 등)를 27 로 고친다.
- `test_db.py` 의 "기존 표는 재생성하지 않는다" 류 테스트(제외 목록)에 `artifacts` 재생성을 반영하고, 새 DB 와 마이그레이션 DB 스키마 일치 테스트가 통과하는지 본다.
- `test_backup.py` 의 표별 행 수 dict 들(여러 곳 — `grep -n "work_items" tests/workflow/server/test_backup.py` 로 찾는다)에 새 두 표를 더한다.
- `schema_v14.sql` 픽스처를 v27 로 올린 뒤 `triage_result`·`handoff_report` 산출물 행이 들어간다(D38 결함 회귀).
- `schema_v26.sql` 을 v27 로 올려 기존 산출물 행이 그대로다.

## 금지사항 (이 step)

- 비밀값·토큰을 새 표에 넣지 마라. 이유: AGENTS.md CRITICAL.
- 서버 라우트·워커를 건드리지 마라. 이유: 저장 레이어 step 이다.

## 테스트 먼저

소스·템플릿을 바꾸기 전에 `tests/` 미러 경로(`src/workflow/x/y.py` → `tests/workflow/x/test_y.py`)에 실패하는 테스트를 먼저 작성하고, 실패 원인이 의도한 것인지 확인한다(`tdd-guard.sh` 가 테스트 없는 소스 작성을 막는다). 구현한 뒤 해당 테스트와 전체 회귀를 통과시킨다. 이 step 의 변경으로 기존 테스트(개수·이름 집합·스키마 버전·문구 단정)가 깨지면 새 동작 기준으로 고치되 단정을 약하게 만들지 않는다. 무엇을 왜 바꿨는지는 summary 에 남긴다.

## Acceptance Criteria

```bash
python3 -m pytest -q
python3 -m ruff check .
```

위 커맨드와 이 step 의 "테스트" 항목을 모두 확인한다.

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트: ARCHITECTURE.md 디렉토리 구조·외부 의존 표를 벗어나지 않았는가, ADR 기술 스택 안인가, AGENTS.md CRITICAL 규칙(도메인 순수성, `server`↔`connector` 상호 import 금지, 외부 입력으로 명령·경로 실행 금지, 비밀값)을 지켰는가, GLOSSARY 용어를 그대로 썼는가.
3. `phases/25-handoff-report/index.json` 의 이 step 을 갱신한다: 성공 → `"status": "completed"`, `"summary"`(다음 step 에 필요한 파일·이름·결정), `"verification"`(실행한 AC 명령과 종료 코드). 3회 수정해도 실패 → `"error"` + `"error_message"`. 사용자 개입 필요 → `"blocked"` + `"blocked_reason"` 후 즉시 중단.
