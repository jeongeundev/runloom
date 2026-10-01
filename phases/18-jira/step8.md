# Step 8: jira-followup — 후속 새 업무 → Jira 이슈 생성·링크·조정

## 읽어야 할 파일

- AGENTS.md
- phases/18-jira/README.md (사용자 결정 6가지·계획 기본값 14가지·조사 결과 — 이 phase 의 기준), phases/18-jira/index.json (이전 step summary)
- docs/ARCHITECTURE.md "Jira 소스 — phase 18" (step 0 이 쓴 이름·시그니처·스키마 표 — README 와 다르면 ARCHITECTURE 가 기준)
- docs/adr/0024-*.md (step 0 이 쓴 ADR), docs/GLOSSARY.md
- src/workflow/adapters/repo.py (후속 `placement == "new_work"` — 원인 업무 원본 칸 복사·`spawned_from` 링크), src/workflow/domain/succession.py·task_followup.py (후속 규칙), src/workflow/domain/adf.py (`markdown_to_adf` — step 2)
- step 1·3·5·7 산출물 (outbox·전송 모양)

먼저 실제 파일을 읽는다. 대화 이력을 전제로 판단하지 않는다.

## 작업

1. 후속 규칙이 새 업무를 만들 때 원인 업무가 Jira 원본이고 그 프로젝트에 후속 이슈 유형이 설정돼 있으면 outbox 에 "이슈 생성" 한 행(새 업무당 한 번). 새 업무의 `source_key`·`source_url` 은 원인 것을 복사하지 않는다(생성 전엔 비움 — 목록 키 칸은 `RUN-n` 만).
2. 전송: `POST /rest/api/3/issue`(같은 프로젝트·설정 이슈 유형·summary = 새 업무 제목·description = 짧은 Markdown(원인 `SHOP-12`·Runloom `RUN-n`·요약) → ADF·labels `runloom`,`runloom-<RUN-n>`) → 성공하면 새 업무 `source_item_id`·`source_key`·`source_url` 채움 → 원인 이슈와 링크(유형 이름 "Relates", 없으면 링크 생략하고 기록).
3. 조정: 응답 유실·`unknown` 이면 JQL `labels = "runloom-<RUN-n>"` 로 찾아 있으면 그 이슈로 채움(두 번 만들지 않음). 400(필수 칸 등)은 `failed` + 이유("필수 칸 X — Jira 프로젝트 설정 확인"). 재시도 규칙은 step 7 과 같다.
4. 만든 이슈가 다음 동기화로 들어오면 같은 업무(step 5 규칙) — 테스트로 확인.

## 테스트 먼저

미러 테스트: Jira 원인 + 후속 유형 설정 → 이슈 생성 요청 모양(ADF·라벨) → 새 업무 원본 칸 채움 → 링크, 후속 유형 비면 생성 안 함, GitHub 원인은 생성 안 함, 같은 단계 후속(`placement` 가 새 업무 아님)은 생성 안 함, 유실 → 라벨 JQL 조정 → 한 번만, 400 → failed, 링크 유형 없음 → 이슈는 성공. 동기화 뒤 업무 수 불변.

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
