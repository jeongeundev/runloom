# Step 9: e2e-chain

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/AGENTS.md`, `/docs/ARCHITECTURE.md`, `/docs/adr/` 전부, `/docs/GLOSSARY.md`(`LocalStack`), `/docs/UI_GUIDE.md` "심사자 첫 방문 흐름"
- `/tests/e2e/conftest.py`, `/tests/e2e/test_scenario.py` — `stack` fixture, `_wait_agent`, 시나리오 순서(첫 방문 → 시연 업무 → A 실행 → B 자동 착수 → 검토 승인 → 저장소 검사)
- `/scripts/local_stack.py` — step 0 `--scripted`, step 7 등록 2개
- `/src/workflow/server/web.py` — step 2 `/agents/register`, step 5 `/tasks/import`, step 6 `/chains/{id}`·`/start`·`/live`
- `/src/workflow/scripted/` — step 0
- `/docs/VERIFICATION_LOG.md` — 기존 검증 기록 형식

이전 step 에서 만들어진 코드를 꼼꼼히 읽고, 설계 의도를 이해한 뒤 작업하라.

## 작업

심사자 흐름 전체를 로컬 스택(대본 에이전트, `DIAG_MODEL=fake`)으로 끝까지 돌리는 e2e. 기존 시나리오(시연 업무 만들기 경로)는 직접 등록 경로로 남기고, 새 시나리오를 **주 경로**로 추가한다.

### `tests/e2e/conftest.py`

- `stack` fixture 가 `LocalStack(..., scripted=True)` 로 뜬다(`--fake-codex` 대신). `WORKFLOW_SCRIPT_PACE_SECONDS` 는 0.

### `tests/e2e/test_scenario.py` — 새 시나리오 `test_1x_chain_*` (번호는 기존 순서 뒤에)

1. 첫 방문: `/` 는 랜딩(쿠키 없음, `서비스 바로 가기`), `/tasks` 에서 쿠키 발급·에이전트 0개·`에이전트 등록` 안내.
2. `/agents/register` 카탈로그 3개(`agent-ops-demo`, `agent-codex-mac`, `agent-claude-mac`) → 셋 다 등록(순서: ops, codex, claude). 홈 카드 3개, `시연용 · 대본 재생` 라벨.
3. `/tasks/import?source=github` 4개 → 전부 체크 가져오기 → 303 `/chains/…`.
4. 체인 화면: 노드 `#41`(운영 진단, 실행 가능) → `#42`(개인 Codex 기본 선택, "후보 2개"·"먼저 등록한", 대기 · 선행 대기), 사람 단계, skipped `#43`·`#44`.
5. `POST /chains/{id}/start` → `#41` `실행 요청됨` → `실행 중` → `완료 · 판정 근거: n/n`(최대 60초 대기, `/chains/{id}/live` 폴링).
6. `#42` 가 사람 조작 없이 `실행 요청됨` → `실행 중` → `확인 필요 · 검토 대기`(최대 120초). 결과 카드 `검토 가능`, `대본 재생` 표시, 산출물(diff·테스트 전후·보고서).
7. `#42` 검토 승인 → 사람 단계 `완료 · 병합: 운영자 확인 대기`. 홈 워크플로우 구역 `2/2 완료`.
8. 저장소: 데모 저장소 `main` 은 `report-base` 그대로, `task/{#42 task_id}` 브랜치 존재, worktree 디렉터리 없음(step 8).
9. Jira 탭으로 한 번 더 가져오기(다른 세션) → 같은 구조, 세션 격리.
10. 추가: 다른 세션에서 Claude 를 먼저 등록하고 가져오면 `#42` 가 Claude Code 기본 선택이고 실행 결과 산출물에 `claude_jsonl` 이 있다(대본 Claude 경로 검증). 시간이 걸리므로 이 케이스는 `@pytest.mark.slow` 로 두고 기본 실행에 포함한다(전체 e2e 가 5분을 넘지 않게).

### 검증 기록

- `docs/VERIFICATION_LOG.md` 에 "대본 데모 e2e (phase 5 step 9)" 절 — 실행 시각, 소요 시간, A·B 각 소요, 산출물 종류, 저장소 확인 결과. 실제 모델·실제 Codex 는 돌지 않았음을 명시.

## Acceptance Criteria

```bash
python3 -m pytest tests/e2e -q -x          # 로컬 5-프로세스 e2e 통과 (대본 에이전트)
python3 -m pytest -q
python3 -m ruff check .
grep -n "phase 5 step 9" docs/VERIFICATION_LOG.md
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - 시나리오가 UI_GUIDE "심사자 첫 방문 흐름" 과 같은 순서인가? (다르면 UI_GUIDE 를 이 순서로 고친다 — 문서가 코드를 따른다)
   - 외부 서비스 호출이 없는가? (OpenAI·GitHub·Jira·실제 Codex/Claude)
   - AGENTS.md CRITICAL 규칙을 위반하지 않았는가?
   - GLOSSARY.md 용어.
3. 결과에 따라 `phases/5-scripted-demo/index.json` 의 해당 step 을 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"`
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- e2e 를 통과시키려고 대기 시간을 늘리는 것 외에 제품 코드의 상태 규칙을 바꾸지 마라. 이유: 규칙 변경은 앞 step 의 범위이며 여기서 바뀌면 문서와 어긋난다. 결함이 드러나면 원인 step 의 파일을 고치고 그 테스트를 함께 고친다(그 사실을 summary 에 적는다).
- 실제 `codex`·`claude`·OpenAI 를 쓰지 마라.
- 기존 테스트를 깨뜨리지 마라.
