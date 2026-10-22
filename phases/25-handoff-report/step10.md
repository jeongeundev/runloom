# Step 10: ops-docs — 셀프호스트 업그레이드 v27

## 읽어야 할 파일

- AGENTS.md, phases/25-handoff-report/README.md("결정" 절), phases/25-handoff-report/index.json(이전 step summary)
- docs/adr/0033-handoff-report.md, docs/ARCHITECTURE.md "인계 리포트 — phase 25"(**README 와 다르면 ARCHITECTURE 가 기준** — step 0 이 코드 조사로 고정한 이름·표·경로), docs/CONTRACT.md 19절, docs/GLOSSARY.md phase 25 용어
- docs/SELFHOST.md "업그레이드"(그리고 `tests/` 의 SELFHOST 문서 검사 — `grep -rn SELFHOST tests/`)(v24·v26 절 형식), `deploy/selfhost/` 스크립트, AGENTS.md "명령어"·"아키텍처 규칙", docs/README.md(문서 목록이 있으면)
- step 0~9 summary

## 작업

1. SELFHOST "업그레이드" 에 v27 절: 백업 먼저 → 진행 중 실행이 끝난 뒤 `install.sh` → 스키마 27 → **보내는 쪽·받는 쪽 러너 모두 `install-runner.sh` 재실행**(능력 `handoff_report`) — 재설치 뒤 `connectors.capabilities_json` 에 `handoff_report` 가 있는지 확인하는 명령. 업그레이드 전에 끝난 결과에는 리포트가 없다(D27). 옛 러너는 리포트를 만들지도 받지도 못하고 다음 단계는 대기 없이 착수한다(D10).
2. 대기 시간 환경변수는 SELFHOST 에 적지 않는다(D39 — `test_selfhost_md_env_names_are_real`).
3. AGENTS.md 는 명령어·경계 규칙이 실제로 바뀐 경우에만 그 줄을 고친다(새 모듈 경계가 기존 규칙 안이면 고치지 않는다).

## 테스트

- 문서만이면 새 테스트 없음. 문서에 적은 명령의 경로·스크립트 이름이 실제 파일과 맞는지 `ls` 로 확인하고 summary 에 남긴다.

## 금지사항 (이 step)

- `docs/CURRENT_HANDOFF.md` 를 고치지 마라. 이유: 로컬 전용 파일(PR #10).
- 셀프호스트에 실제로 설치·재시작하지 마라. 이유: 적용은 phase 뒤 사용자 지시.

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
