# Step 7: docs-sync

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/docs/ARCHITECTURE.md` — "상태·재접속·완료" (문장 "모델·CLI 실패나 사용량 한도는 실패로 기록하고 다른 엔진으로 자동 대체하지 않는다. … 무제한 자동 재시도는 첫 범위 밖이다."), "타임아웃과 폴링" 표, "DB 제약과 실행 잠금", "등록·선택·권한"
- `/docs/adr/0007-usage-limit-wait-policy.md` — 이 phase 의 결정. "적용 시점" 문단은 이제 지난 일이다
- `/docs/PRD.md` — 3절 사용자 상태 표
- `/docs/GLOSSARY.md`
- `/docs/CONTRACT.md` — Step 0 이 추가한 `usage_limit` 예시
- `/docs/UI_GUIDE.md`, `/docs/DEPLOY.md` — Step 2·3 이 고친 부분
- `/docs/CURRENT_HANDOFF.md`
- `/docs/README.md` — 문서 색인
- `/phases/3-limit-wait/index.json` — Step 0~6 의 `summary` (실제로 구현된 이름·값)
- `grep -rn "사용량 한도\|usage_limit\|on_usage_limit\|retry_after" docs src` 결과

이전 step에서 만들어진 코드를 꼼꼼히 읽고, 설계 의도를 이해한 뒤 작업하라.

## 배경

코드는 Step 0~6 으로 ADR-0007 을 구현했다. 문서가 아직 첫 범위 결정("한도는 실패로 기록, 재실행 없음")을 말하고 있어 다음 phase 의 step 프롬프트에 낡은 규칙이 주입된다(`scripts/execute.py` 는 `docs/*.md` 와 `docs/adr/*.md` 를 매 step 에 넣는다). 문서를 코드와 같게 맞춘다.

## 작업

### `docs/ARCHITECTURE.md`

- "상태·재접속·완료" 의 해당 문장을 고친다: 모델·CLI 실패는 실패로 기록하고 다른 엔진으로 자동 대체하지 않는다(유지). **사용량 한도**(`failed_code = usage_limit`, 종료 확인)는 Agent 의 `on_usage_limit` 정책이 `wait` 이면 `대기` 로 두고 리셋 시각(`retry_after`) 이후 새 Execution(`start_key = retry:{실패 ID}`)으로 재시도한다. 상한(재시도 횟수·기본 대기·최대 대기)은 `Limits` 설정. ADR-0007 링크.
- "타임아웃과 폴링" 표에 세 값을 추가한다 (초기값 6회 · 3600초 · 21600초, 환경변수 이름).
- "등록·선택·권한" 에 등록 요청의 `on_usage_limit` 한 줄.
- "DB 제약과 실행 잠금" 에 `retry:` start_key 한 줄과 `schema_version` 마이그레이션(현재 3) 한 줄.

### `docs/adr/0007-usage-limit-wait-policy.md`

- 첫 문단의 "적용 시점 … 그 전까지는 현재 문장이 유효" 를 "적용됨: phases/3-limit-wait (YYYY-MM-DD)" 로 바꾼다. 결정·규칙 본문은 구현과 다른 값이 있으면 구현에 맞춘다(예: 상한 값).

### `docs/PRD.md`

- 3절 사용자 상태 표의 `대기` 행 설명에 "사용량 한도 리셋 대기" 를 예로 추가한다. 표 구조는 바꾸지 않는다.

### `docs/GLOSSARY.md`

- `on_usage_limit`(Agent 정책, `wait`/`fail`, `fallback` 예약), `retry_after`(계약 `FailedData` 필드) 항목 추가. 금지 표현 열에 `backoff`, `cooldown` 등 쓰지 않는 말을 적는다.

### `docs/CURRENT_HANDOFF.md`, `docs/README.md`

- 인계 문서를 "3-limit-wait 완료" 상태로 갱신하고, README 색인에 ADR-0007 이 빠져 있으면 추가한다.

### 검증

- `grep -n "다른 엔진으로 자동 대체하지 않는다" docs/ARCHITECTURE.md` 가 새 문장(사용량 한도 예외 포함) 1건만 찾는다.
- `grep -rn "무제한 자동 재시도" docs/` 의 문장이 여전히 참이다(상한이 있다).

## Acceptance Criteria

```bash
python3 -m pytest -q
python3 -m ruff check .
grep -n "ADR-0007\|0007-usage-limit" docs/ARCHITECTURE.md docs/README.md
grep -n "on_usage_limit" docs/GLOSSARY.md docs/ARCHITECTURE.md
grep -n "retry_after" docs/GLOSSARY.md docs/CONTRACT.md
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - 문서가 서로 모순되지 않는가? (ARCHITECTURE ↔ ADR-0007 ↔ PRD 3절 ↔ GLOSSARY)
   - GLOSSARY.md 용어를 그대로 썼는가? 금지 표현을 쓰지 않았는가?
3. 결과에 따라 `phases/3-limit-wait/index.json`의 해당 step을 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"`
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- `src/`·`tests/` 를 고치지 마라. 이유: 이 step 은 문서만. 문서와 코드가 다르면 문서를 코드에 맞추고, 코드가 ADR 을 어긴 것 같으면 `summary` 에 적어 두어라(다음 phase 에서 다룬다).
- 과거 평가 보고서(`docs/DIAG_EVAL*.md`)·`VERIFICATION_LOG.md` 의 기록을 고치지 마라. 이유: 기록이다. 새 검증은 새 항목으로 덧붙인다.
- ADR-0001·ADR-0004 를 고치지 마라. 이유: 어댑터 1종·규칙 기반 선택은 그대로다.
- 기존 테스트를 깨뜨리지 마라
