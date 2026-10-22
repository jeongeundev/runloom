# Step 4: report-github — PR 근거 읽기

## 읽어야 할 파일

- AGENTS.md, phases/25-handoff-report/README.md("결정" 절), phases/25-handoff-report/index.json(이전 step summary)
- docs/adr/0033-handoff-report.md, docs/ARCHITECTURE.md "인계 리포트 — phase 25"(**README 와 다르면 ARCHITECTURE 가 기준** — step 0 이 코드 조사로 고정한 이름·표·경로), docs/CONTRACT.md 19절, docs/GLOSSARY.md phase 25 용어
- `src/workflow/adapters/github_client.py`(Protocol·실제 구현·`get_pull_request`·`list_pulls`·오류 타입·테스트용 가짜 클라이언트), `src/workflow/server/github_clients.py`(설치 토큰으로 클라이언트 얻는 법), `adapters/repo.py` 의 `work_pull_requests`·`task_pull_requests` 조회 함수
- docs/adr/0017-github-app-connection.md(설치 토큰은 프로세스 메모리에만), `tests/workflow/adapters/test_github_client.py`(httpx MockTransport 쓰는 법)

## 작업

1. `GitHubClient` Protocol 과 실제 구현에 ARCHITECTURE 의 읽기 메서드를 더한다 — PR 본문, PR 커밋 목록(sha·메시지 첫 줄), 리뷰 코멘트(작성자·본문·경로 표시 문자열). 읽기(GET)만. 공용 가짜 클라이언트는 없다 — Protocol 을 쓰는 테스트 파일들의 `FakeGitHub` 중 Protocol 준수를 단정하는 것이 있으면 그 가짜에만 메서드를 더하고, 이 step 테스트는 자기 가짜를 둔다.
2. `server/handoff_reports.py`(ARCHITECTURE 이름)에 PR 근거 수집 함수: 업무에 연결된 PR 이 있으면 위 메서드로 읽어 `ReportJob` 입력 자료 형태로, 없으면 빈 값, 읽기 실패(설치 토큰 실패·권한 부족·한도·연결 오류)면 PR 근거 없이 `pr_evidence_error`(ARCHITECTURE 문구)를 채운다(D22). 길이 상한은 ARCHITECTURE 값으로 자른다.
3. Jira 는 새 호출을 하지 않는다 — 업무 원문(`work_items.request`·`source_url`)만 쓴다(D22).

## 테스트

- MockTransport 로 세 메서드의 경로·응답 파싱, 4xx/5xx/연결 오류 → 클라이언트 오류 타입.
- 수집 함수: PR 없음 → 빈 값, PR 있음 → 본문·커밋·코멘트, 실패 → `pr_evidence_error` 만 있고 예외가 새지 않음.
- 반환값·로그에 설치 토큰·Authorization 헤더가 없다.

## 금지사항 (이 step)

- GitHub 에 쓰기(POST·PATCH·PUT·DELETE) 호출을 더하지 마라. 이유: D22 읽기만, 출품작 저장소 쓰기 금지 원칙.
- 설치 토큰을 반환값·DB·로그·요청문에 넣지 마라. 이유: AGENTS.md CRITICAL 비밀값.

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
