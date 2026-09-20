# Step 5: claude-live-check

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/AGENTS.md`
- `/docs/VERIFICATION_LOG.md` — phase 0 step 15 의 Codex 실연동 기록 형식 (무엇을 확인했고 무엇은 안 했는지)
- `/docs/DEPLOY.md` 5·6 절
- `/scripts/local_stack.py` — `--fake-codex`·`--fake-claude` 없이 돌리면 PATH 의 실제 도구를 쓴다
- `/src/workflow/connector/claude.py`, `/src/workflow/connector/runner.py`
- `/phases/0-mvp/step15.md` 와 `phases/0-mvp/index.json` 의 step 15 summary — 같은 절차를 Claude 로 반복한다

이전 step에서 만들어진 코드를 꼼꼼히 읽고, 설계 의도를 이해한 뒤 작업하라. 새 기능은 테스트를 먼저 작성한다 (AGENTS.md TDD, `tests/` 는 `src/` 미러 배치).

## 배경

Claude 어댑터가 실제 `claude` CLI(사용자 Mac 의 로그인 재사용)로 B 를 끝까지 수행하는지 한 번 확인한다. 구독 사용량을 쓰므로 **1회만** 돌리고, 실패해도 재시도는 최대 1회다. 유료 API 키는 쓰지 않는다 (`ANTHROPIC_API_KEY` 가 환경에 있으면 제거하고 시작한다 — 구독 로그인 경로만).

## 작업

1. 시작 전 확인: `claude --version`, `claude auth status` (또는 로그인 상태를 보여 주는 명령) 가 로그인됨을 보인다. 아니면 `blocked` 로 멈춘다.
2. `python3 scripts/local_stack.py --workdir data/claude-live --fake-codex tests/e2e/fake_codex.py` (Codex 는 가짜, Claude 는 실제) 를 백그라운드로 띄운다.
3. 브라우저 없이 HTTP 로: 세션 쿠키 발급 → A 등록·실행(진단은 `DIAG_MODEL=fake` 라 무료) → 완료 확인 → 코드 수정 업무를 `agent-claude-mac` 직접 선택·자동 실행·검토 후 완료로 등록 → `확인 필요 · 검토 대기` 까지 대기 (최대 15분).
4. 기록할 것: 소요 시간, `claude_jsonl` 산출물의 최상위 키와 usage, 결과 커밋·diff 요약(변경 파일·+/-), 수정 전/후 테스트 exit code, 검증 프로필 exit code, 승인 요청 횟수(0 이어야 함), 데모 저장소 `main` 불변·`task/` 브랜치 존재.
5. `docs/VERIFICATION_LOG.md` 에 "Claude Code 실연동 (phase 4 step 5)" 절을 step 15 형식으로 추가한다. 실패했으면 실패 그대로 기록하고 원인 분류(권한 거부 / 출력 형식 / 한도 / 어댑터 결함)를 적는다.
6. 어댑터 결함이 드러나면 이 step 안에서 고친다 (테스트 먼저) — 단 `bypassPermissions` 로 우회하지 않는다. 권한 거부로 실패하면 `--allowedTools` 목록을 필요한 최소로 넓히고 그 이유를 기록한다.
7. 스택을 내리고 `data/claude-live` 는 남긴다 (gitignore).

## Acceptance Criteria

```bash
python3 -m pytest -q
python3 -m ruff check .
grep -n "Claude Code 실연동" docs/VERIFICATION_LOG.md
git -C data/claude-live/demo-report-repo log --oneline -1 main    # report-base 그대로
git -C data/claude-live/demo-report-repo branch | grep -c "task/"  # 1 이상
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - ARCHITECTURE.md 디렉토리 구조를 따르는가?
   - ARCHITECTURE.md 외부 의존 표에 없는 외부 서비스를 끌어들이지 않았는가?
   - ADR 기술 스택을 벗어나지 않았는가?
   - AGENTS.md CRITICAL 규칙을 위반하지 않았는가? (도메인이 I/O 를 import 하지 않는가, server↔connector 가 서로 import 하지 않는가, 외부 입력에서 명령·경로를 실행하지 않는가, 비밀값이 DB·로그·응답에 없는가)
   - GLOSSARY.md 용어를 그대로 썼는가? 금지 표현을 쓰지 않았는가?
3. 결과에 따라 `phases/4-claude-issues/index.json`의 해당 step을 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"` (만든 파일·함수·결정 사항)
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- 실제 Claude 실행을 2회 넘게 하지 마라. 이유: 이 세션·하네스와 같은 구독 한도를 쓴다.
- `ANTHROPIC_API_KEY` 로 돌리지 마라. 이유: 유료 호출이며 ADR-0001 원칙(로컬 로그인 재사용)과 다르다.
- 결과가 나쁘다고 판정 규칙이나 검증 프로필을 느슨하게 하지 마라. 이유: 검증 없이 인계하지 않는다는 제품 주장.
- 기존 테스트를 깨뜨리지 마라
