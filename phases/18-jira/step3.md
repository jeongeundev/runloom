# Step 3: jira-client — HTTP 경계와 비밀 이름

## 읽어야 할 파일

- AGENTS.md
- phases/18-jira/README.md (사용자 결정 6가지·계획 기본값 12가지·조사 결과 — 이 phase 의 기준), phases/18-jira/index.json (이전 step summary)
- docs/ARCHITECTURE.md "Jira 소스 — phase 18" (step 0 이 쓴 이름·시그니처·스키마 표 — README 와 다르면 ARCHITECTURE 가 기준)
- docs/adr/0024-*.md (step 0 이 쓴 ADR), docs/GLOSSARY.md
- src/workflow/adapters/github_client.py (Protocol·HTTPX·오류 계층·401 재시도·rate limit), src/workflow/adapters/secret_store.py, src/workflow/server/github_clients.py
- src/workflow/contracts/jira.py·domain/jira_intake.py (step 2)
- docs/research/2026-09-29-jira-integration.md 2.1·2.3·2.5절 (엔드포인트·페이지·429)

먼저 실제 파일을 읽는다. 대화 이력을 전제로 판단하지 않는다.

## 작업

1. `adapters/jira_client.py`: `JiraClient` Protocol + `HttpJiraClient(base_url, email, token_provider, transport=None)`. 메서드(이름은 ARCHITECTURE): tenant_info(cloudId), myself, 프로젝트 찾기, 프로젝트 이슈 유형, 프로젝트 상태 목록, `search_jql`(fields 명시·`nextPageToken`·`maxResults`), 이슈 한 건, 전환 목록·전환 실행, 이슈 생성, 이슈 링크 유형 목록·링크 만들기.
   - Basic 인증(`email:token`), `follow_redirects=False`, 타임아웃, 응답 크기 상한.
   - 오류 계층(GitHub 과 같은 모양): 401 인증, 403 권한, 404 없음, 400 요청 거부(본문의 `errorMessages` 요약만 — 토큰·헤더 넣지 않음), 429·503 `Retry-After`, 5xx·네트워크 = 일시.
   - 요청 URL 은 설정된 사이트(또는 ARCHITECTURE 의 게이트웨이) 밖으로 나가지 않는다. 이슈 키·id 는 경로에 넣기 전에 형식 검사.
2. `secret_store.NAMES` 에 Jira 토큰 이름(ARCHITECTURE). 읽기 도우미는 ARCHITECTURE 위치에.
3. 로그·예외 메시지에 토큰·Authorization 헤더가 나오지 않는다.

## 테스트 먼저

`tests/workflow/adapters/test_jira_client.py`(httpx `MockTransport`): 각 메서드 요청 모양(경로·쿼리·JSON·Authorization 이 Basic), search 페이지 두 쪽, 오류 분류 표, 429 `Retry-After`, 사이트 밖 리디렉트 안 따름, 잘못된 키 거부, 예외 문자열에 토큰 없음. secret_store 새 이름 쓰기·권한 0600.

소스·템플릿을 바꾸기 전에 `tests/` 미러 경로에 실패하는 테스트를 먼저 작성하고, 실패 원인이 의도한 것인지 확인한다(`tdd-guard.sh` 가 테스트 없는 소스 작성을 막는다). 구현한 뒤 해당 테스트와 전체 회귀를 통과시킨다. 이 step 의 변경으로 기존 테스트가 깨지면 새 동작 기준으로 고치되 단정을 약하게 만들지 않는다. 무엇을 왜 바꿨는지는 summary 에 남긴다.

## Acceptance Criteria

```bash
python3 -m pytest -q
python3 -m ruff check .
```

위 커맨드와 이 step 의 테스트 항목을 모두 확인한다.

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 규칙을 확인한다.
   - `domain/` 은 FastAPI·sqlite3·HTTPX·subprocess·Git 을 import 하지 않는다.
   - `server/` 와 `connector/` 는 서로 import 하지 않고 `contracts/` 만 공유한다.
   - 외부 입력(Jira 응답·이슈 본문·요청 본문·폼·쿼리 문자열·모델 응답)에서 명령·경로를 받아 실행하지 않는다. Jira 사이트 주소는 `https://<이름>.atlassian.net` 형식만 받는다(다른 호스트·경로·포트 거부 — SSRF 방지).
   - 비밀값(Jira API 토큰, 연결 토큰, `OPERATOR_TOKEN`, `SESSION_SECRET`, GitHub App 비밀·설치 토큰, PAT, 알림 웹훅 URL)은 DB·로그·응답·템플릿·백업에 넣지 않는다. Jira 토큰은 `adapters/secret_store.py` 로만 읽고 쓴다.
   - 템플릿은 외부 문자열(Jira 제목·상태 이름·프로젝트 이름)을 자동 이스케이프로만 출력한다(`|safe` 금지).
   - GLOSSARY 이름을 그대로 쓴다.
   - phase 8·11·12·14·15·16·17 의 GitHub 순환(수집 → 맡기기 → 수정 → 검토 → 초안 PR → 병합 추적 → 업무 완료), 사람별 내 차례, 직접 작업·PR 신호, 소유자 승인·꺼진 러너 대기·검증만 다시가 그대로 동작한다.
3. 성공이면 `phases/18-jira/index.json` 의 이 step 만 `completed` 로 바꾸고, `summary` 에 한 줄로 남긴다: 생성·수정·삭제 파일, 결정, 검증 결과(테스트 수), 다음 step 이 주의할 점.
4. 3회 수정 후에도 실패하면 `error` + `error_message` 를 기록한다. 사용자 결정·외부 자원이 필요하면 `blocked` + `blocked_reason` 을 기록한다. 실행하지 않은 검증을 `completed` 로 표시하지 않는다.

## 금지사항

- 실제 Jira(`*.atlassian.net`·`api.atlassian.com`)·GitHub(api.github.com·github.com)·Discord·외부 웹훅·실제 Claude/Codex 를 호출하지 않는다(공식 문서 읽기는 허용). 이유: 모든 step 은 httpx `MockTransport`·가짜 Jira·가짜 GitHub·가짜 도구(PATH 앞의 가짜 `codex`/`claude`)로 검증한다. 실연동은 phase 뒤 사용자가 Jira 사이트를 만든 뒤 한다.
- 사용자가 띄워 둔 환경을 읽거나 바꾸지 않는다: 셀프호스트(compose 프로젝트 `runloom`, 포트 8000, 볼륨 `runloom_workflow-data`)·`deploy/selfhost/.env`·러너 홈(`~/Library/Application Support/workflow-connector*`)·launchd(`launchctl` 실행 금지)·`~/Library/LaunchAgents`·`~/.claude/`·`/Users/kje/demo/*`. 컨테이너를 띄우거나 멈추지 않는다. 이유: 실사용 중인 환경이다.
- 새 의존성(Python 패키지 — Jira SDK·ADF 라이브러리 포함, JS 프레임워크·번들러, 외부 CDN)을 추가하지 않는다. 이유: ADR-0002·UI_GUIDE. HTTP 는 HTTPX, ADF 는 직접 작은 변환기.
- 종류 이름(`bug_fix`·`code_review`)으로 새 분기를 만들지 않는다(`composition.py`·`worker.py` 포함). 원본 종류(`source_type`)로 가르는 것은 소스 경계 모듈(`jira_sync`·`github_sync`·되쓰기 모듈)과 원본 조회 한 곳에만 둔다. 이유: ADR-0009, AGENTS.md.
- `tasks` 표를 재생성하거나 `tasks.status` CHECK 를 바꾸지 않는다. 이유: ADR-0020.
- 모델의 "완료했다" 응답이나 프로세스 종료 코드만으로 완료 처리하지 않는다. Jira 상태도 완료 판정 근거가 아니다(업무 완료는 기존 PR 병합 신호). 이유: 계약 v1.
- 진행 댓글·PR 원격 링크·OAuth·웹훅·Jira 담당자 매핑·Jira 기준선·매핑 표 편집 화면·판단 제안(19-triage)·모니터링 확장(20-monitor)을 만들지 않는다. 이유: 사용자 결정 2026-09-30 범위 밖.
- 사용자 변경을 삭제하거나 stash·reset 하지 않는다. 커밋은 하네스가 한다.
- 이 step 범위 밖 모듈을 "개선"하지 않는다. 기존 테스트의 단정을 약하게 만들어 통과시키지 않는다.
