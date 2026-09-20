# Step 0: failed-retry-after

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/docs/ARCHITECTURE.md` — "계약 v1의 공통 규칙", "실행 이벤트", "상태·재접속·완료"
- `/docs/adr/` (하위 파일 전부) — 특히 `0007-usage-limit-wait-policy.md` (이 phase 의 근거)
- `/docs/GLOSSARY.md`
- `/docs/CONTRACT.md` — `failed` 이벤트 예시가 있는 절
- `/src/workflow/contracts/v1.py` — `_Contract`(알 수 없는 필드 거부), `Rfc3339`, `FailedData`, `_EVENT_DATA`, `ExecutionEvent`
- `/tests/workflow/contracts/test_v1.py` — 이벤트 예시를 검증하는 방식 (CONTRACT.md 예시를 fixture 로 쓰는지 확인)

이전 step에서 만들어진 코드를 꼼꼼히 읽고, 설계 의도를 이해한 뒤 작업하라.

## 배경

ADR-0007 에 따라 사용량 한도로 실패한 실행은 리셋 시각까지 기다렸다가 새 Execution 으로 재시도한다. 연결 프로그램이 리셋 시각을 서버에 알릴 통로가 없어 계약 v1 의 `failed` 이벤트에 선택 필드를 하나 더한다. 서버·연결 프로그램은 `src/workflow/contracts/` 만 공유하므로 이 step 은 계약만 바꾼다.

## 작업

### `src/workflow/contracts/v1.py`

- `FailedData` 에 선택 필드를 추가한다:
  ```python
  class FailedData(_Contract):
      code: str
      message: str
      process_stopped: bool
      retry_after: Rfc3339 | None = None  # 재시도 가능 시각. 사용량 한도(code="usage_limit") 에서만 쓴다
  ```
- 알 수 없는 필드 거부·자동 형 변환 금지 등 `_Contract` 규칙은 그대로다. `retry_after` 는 시간대가 있는 RFC 3339 만 받는다 (`Rfc3339` 재사용). `null` 허용.
- `code` 는 여전히 자유 문자열이다. `usage_limit` 값을 enum 으로 고정하지 마라 — 다른 실패 코드(`timeout`, `codex_unavailable` 등)도 문자열이다.

### `docs/CONTRACT.md`

- `failed` 이벤트 예시가 있는 절에 사용량 한도 예시를 하나 추가한다:
  `code: "usage_limit"`, `message` 는 Codex 문구를 마스킹한 한 줄, `process_stopped: true`, `retry_after: "2026-09-20T21:16:34+09:00"`.
- 기존 예시(`retry_after` 없음)는 그대로 둔다 — 하위 호환의 근거다.

### 테스트 (먼저 작성) — `tests/workflow/contracts/test_v1.py`

- `retry_after` 없는 기존 `failed` 예시가 그대로 통과한다 (`retry_after is None`).
- `retry_after` 가 시간대 있는 RFC 3339 면 통과, 시간대 없는 값(`"2026-09-20T21:16:34"`) 은 거부.
- `FailedData` 에 알 수 없는 필드(`resets_at`) 를 주면 거부.
- `ExecutionEvent(type="failed", data={... "retry_after": ...})` 가 파싱된다.

## Acceptance Criteria

```bash
python3 -m pytest tests/workflow/contracts -q
python3 -m pytest -q
python3 -m ruff check .
grep -n "retry_after" docs/CONTRACT.md      # 예시 1건 이상
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - ARCHITECTURE.md 디렉토리 구조를 따르는가?
   - ARCHITECTURE.md 외부 의존 표에 없는 외부 서비스를 끌어들이지 않았는가?
   - ADR 기술 스택을 벗어나지 않았는가?
   - AGENTS.md CRITICAL 규칙을 위반하지 않았는가?
   - GLOSSARY.md 용어를 그대로 썼는가? 금지 표현을 쓰지 않았는가?
3. 결과에 따라 `phases/3-limit-wait/index.json`의 해당 step을 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"` (추가한 필드·타입)
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- `contract_version` 을 올리지 마라. 이유: 선택 필드 추가는 v1 안에서 하위 호환이다. 버전을 올리면 배포된 연결 프로그램이 422 로 거부된다.
- `FailedData` 외의 계약 객체를 고치지 마라. 이유: 정책·재시도 정보는 서버 DB 와 등록 요청(step 2) 이 다루며 실행 이벤트 계약의 범위가 아니다.
- 연결 프로그램·서버 코드를 고치지 마라. 이유: 각각 step 1·step 4 다.
- 기존 테스트를 깨뜨리지 마라
