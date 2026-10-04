# Step 6: worker-after-result — 워커: 결과 뒤 판단 시작·대체 경로·판정

## 읽어야 할 파일

- AGENTS.md
- phases/22-next-step/README.md (사용자 결정 D1~D4·계획 기본값·조사 결과 — 이 phase 의 기준), phases/22-next-step/index.json (이전 step summary)
- docs/ARCHITECTURE.md "결과 뒤 판단 — phase 22" (step 0 이 쓴 이름·시그니처·스키마·문구 — README 와 다르면 ARCHITECTURE 가 기준. step 0 에서는 아직 없다)
- docs/adr/0027-*.md (step 0 이 쓴 ADR), docs/adr/0025-triage.md, docs/adr/0009-registered-kinds-and-succession-rules.md, docs/GLOSSARY.md
- src/workflow/server/worker.py (tick :398-417, `_check_*_results` :570/:649/:692, `_cycle_followups` :766, `_apply_followup` :850, `_spawn_successors` :1187, `_request_human` :1667, `_judge_triage`, `_triage_new_work` :1398, `_reflect_failures` :1423, `TRIAGE_DEADLINE_SECONDS`)
- tests/workflow/server/test_worker*.py (가짜 러너·`seed_*` 헬퍼)
- step 4·5 산출물

먼저 실제 파일을 읽는다. 대화 이력을 전제로 판단하지 않는다. 이전 step 에서 만든 코드를 꼼꼼히 읽고 설계 의도를 이해한 뒤 작업한다.

## 작업

ARCHITECTURE 의 시작 지점·tick 순서를 그대로.

1. ① 순환 종류 규칙 없음(`none`·hold 없음), 사용자 정의 종류 판정 통과 + 후속 Task 없음 → 결과 뒤 판단 대기 원인으로 기록·시작.
2. ② `needs_information` → 결과 뒤 판단을 시작할 수 있으면 사람 요청 대신 판단, 시작할 수 없으면 지금처럼 사람 요청.
3. 결과 뒤 판단 시작은 워크스페이스 running 1건·러너 빔 규칙을 따르고 접수 판단보다 먼저 고른다.
4. 판정: 기존 `_judge_triage` 가 모드에 맞춰 step 4 검증을 쓴다. 판단 실패·기한 초과 → 원인이 `needs_information` 이면 원래 사람 요청을 연다(같은 원인 키 — 둘 생기지 않게), 규칙 없음 원인이면 지금처럼 `확인 필요`(알림 없음 동작 유지).
5. 제안이 기록되면 업무 상태 재계산(이유 문구는 step 9 가 화면에, 여기서는 `work_status` 사실만).

## 테스트 먼저

- 워커 테스트(가짜 러너): 원인별 시작 4경우, 판정 실패·실행 실패는 시작 안 함, `needs_information` 에서 판단 시작 시 사람 요청 없음 → 판단 실패 시 사람 요청 하나, 능력 없는 러너·판단 Agent 없음 → 기존 동작 그대로, 접수 판단과의 우선순위·running 1건, 재시작 멱등(같은 원인 두 번 시작 안 함), 기존 순환·후속 테스트 회귀 없음.

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
   - `domain/` 은 FastAPI·sqlite3·HTTPX·subprocess·Git 을 import 하지 않는다. 순수 함수이고 현재 시각을 스스로 읽지 않는다(`now` 인자).
   - `server/` 와 `connector/` 는 서로 import 하지 않고 `contracts/` 만 공유한다.
   - 외부 입력(업무 본문·판단 결과·사내 요청 목적·사람 응답·산출물)에서 명령·경로를 받아 실행하지 않는다. 판단 결과의 값은 판단 시점에 저장한 후보 안에서만 받는다(밖이면 `triage_invalid`).
   - 비밀값(연결 토큰 `wfc_…`, `OPERATOR_TOKEN`, `SESSION_SECRET`, GitHub App 비밀·설치 토큰, PAT, Jira API 토큰, 알림 웹훅 URL)을 DB·로그·응답·템플릿·백업·판단 요청문·러너 프로세스 환경에 넣지 않는다.
   - 템플릿은 외부 문자열(업무 제목·판단 근거·요청 목적·멤버 이름)을 자동 이스케이프로만 출력한다(`|safe` 금지).
   - 종류 이름(`bug_fix`·`code_review`·`triage`)으로 새 분기를 만들지 않는다 — 판단 단계는 `is_triage_kind`·`repo._TRIAGE_STAGE` 로, 순환 종류는 `execution_policy.policy_for` 로 가른다(ADR-0009·0025).
   - 판단은 제안만 한다. 업무 완료·종료, 받는 사람의 수락·검토, 원래 업무 담당 변경을 판단이 직접 하지 않는다(D3·D4). 결과 뒤 판단을 자동 시작하지 않는다.
   - GLOSSARY 이름을 그대로 쓴다(`Execution` ≠ run, `Agent` ≠ connector, `outcome` ≠ 상태).
   - phase 8·9·11·12·14~21 동작(GitHub·Jira 순환, 지표, 내 차례, 직접 작업·PR 신호, 소유자 승인·꺼진 러너 대기, 접수 판단·자동 시작, 모니터링, 사내 요청 사람 흐름)이 그대로 동작한다. 접수 판단(`cause='intake'`)의 동작·자동 시작 자격 건수는 바뀌지 않는다.
3. 성공이면 `phases/22-next-step/index.json` 의 이 step 만 `completed` 로 바꾸고, `summary` 에 한 줄로 남긴다: 생성·수정·삭제 파일, 결정, 검증 결과(테스트 수), 다음 step 이 주의할 점.
4. 3회 수정 후에도 실패하면 `error` + `error_message` 를 기록한다. 사용자 결정·외부 자원이 필요하면 `blocked` + `blocked_reason` 을 기록한다. 실행하지 않은 검증을 `completed` 로 표시하지 않는다.

## 금지사항

- 실제 Claude/Codex·GitHub(api.github.com·github.com)·Jira·Discord·외부 웹훅을 호출하지 않는다. 이유: 모든 step 은 가짜 러너·가짜 도구·httpx `MockTransport`·가짜 클라이언트로 검증한다.
- 사용자가 띄워 둔 환경을 읽거나 바꾸지 않는다: 셀프호스트(compose 프로젝트 `runloom`, 포트 8000, 볼륨 `runloom_workflow-data`)·`deploy/selfhost/.env`·러너 홈(`~/Library/Application Support/workflow-connector*`)·launchd(`launchctl` 실행 금지)·`~/Library/LaunchAgents`·`~/.claude/`·`/Users/kje/demo/*`·GitHub 저장소 `jeongeundev/*`. 컨테이너를 띄우거나 멈추지 않는다. 이유: 실사용 중인 환경이다.
- 새 의존성(Python 패키지, JS 프레임워크·번들러, 외부 CDN)을 추가하지 않는다. 이유: ADR-0002·UI_GUIDE.
- 결과 뒤 판단 전용 새 실행 엔진·새 내장 종류·DAG/파이프라인 개념을 만들지 않는다. 이유: 판단은 기존 `triage` 종류·단계·실행·러너 위에 얹는다(ADR-0025), 흐름은 규칙과 제안의 반복이지 그래프가 아니다.
- 사내 요청의 사람 단계(수락·조사 시작·검토·판단 응답·반환)를 시스템이 대신하지 않는다. 이유: 받는 사람의 동의를 남긴다(D4).
- 사용자 변경을 삭제하거나 stash·reset 하지 않는다. 커밋은 하네스가 한다.
- 이 step 범위 밖 모듈을 "개선"하지 않는다. 기존 테스트의 단정을 약하게 만들어 통과시키지 않는다.
- [제안대로] 적용·알림·화면을 만들지 마라. 이유: step 7·8·9 의 범위다.
