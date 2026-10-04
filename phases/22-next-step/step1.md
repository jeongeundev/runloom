# Step 1: next-action-contract — 계약: 다음 행동·모드·후보·러너 능력

## 읽어야 할 파일

- AGENTS.md
- phases/22-next-step/README.md (사용자 결정 D1~D4·계획 기본값·조사 결과 — 이 phase 의 기준), phases/22-next-step/index.json (이전 step summary)
- docs/ARCHITECTURE.md "결과 뒤 판단 — phase 22" (step 0 이 쓴 이름·시그니처·스키마·문구 — README 와 다르면 ARCHITECTURE 가 기준. step 0 에서는 아직 없다)
- docs/adr/0027-*.md (step 0 이 쓴 ADR), docs/adr/0025-triage.md, docs/adr/0009-registered-kinds-and-succession-rules.md, docs/GLOSSARY.md
- src/workflow/contracts/v1.py (`TriageTarget`, `TriageResult`, `TriageCandidates`, `RUNNER_CAPABILITIES`, `ClaimRequest`, `_OmitUnknownMeasure`·직렬화 관례, `KindId`·`Identifier`·`NonEmptyStr`)
- src/workflow/contracts/internal_request.py, src/workflow/contracts/responsibility.py (요청·담당 범위 필드 제약)
- tests/workflow/contracts/test_v1.py (기존 판단 계약 테스트 위치 — 미러 경로 확인)

먼저 실제 파일을 읽는다. 대화 이력을 전제로 판단하지 않는다. 이전 step 에서 만든 코드를 꼼꼼히 읽고 설계 의도를 이해한 뒤 작업한다.

## 작업

ARCHITECTURE "결과 뒤 판단 — phase 22" 의 계약 필드 표를 그대로 구현한다.

1. `NextAction` — `type` 으로 가르는 4형(`stage`·`new_work`·`internal_request`·`human`), `extra="forbid"`. 필드 제약은 ADR-0027(예: `title` 1~120자, `purpose` 1~2000자, `question` 1~500자, 공백만 거부, `assignee{type: member|agent, id}`).
2. `TriageTarget.mode: Literal["intake", "next_step"]` — ADR 이 정한 직렬화(기본값일 때 옛 러너가 받는 JSON 이 지금과 같아야 한다).
3. `TriageResult.next_action: NextAction | None` — 모드 일관성은 결과만으로 알 수 없으면 중앙 판정(step 4)이 본다. 결과 모델 자체의 제약(예: `next_action` 이 있으면 `proposed_kind`·`assignee`·`predecessors` 는 비어야 함)은 여기서.
4. `TriageCandidates.responsibilities` — 항목 모델·최대 개수.
5. `RUNNER_CAPABILITIES` 에 `after_result_triage` 상수.

시그니처는 ARCHITECTURE 이름 고정 표를 따른다.

## 테스트 먼저

- `tests/workflow/contracts/` 미러 위치: 4형 각각 정상·경계·추가 필드 거부, 모드 기본값 직렬화가 기존 `TriageTarget` JSON 과 바이트 단위로 같음, `next_action` 과 접수 판단 칸 동시 사용 거부, 기존 접수 `TriageResult` 예시가 그대로 통과, 후보 `responsibilities` 최대 개수, 능력 상수.

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
- `connector/` 와 `server/` 를 바꾸지 마라. 이유: 계약만 이 step 의 범위다(러너는 step 2, 중앙은 step 4~). 단, 계약 변경으로 기존 테스트가 깨지면 그 테스트만 새 계약 기준으로 고친다.
