이 프로젝트는 Harness 프레임워크를 사용한다. 아래 워크플로우에 따라 작업을 진행하라.

---

## 워크플로우

### A. 탐색

`/docs/` 하위 문서(PRD, ARCHITECTURE, ADR, GLOSSARY 등)를 읽고 프로젝트의 기획·아키텍처·설계 의도를 파악한다. 필요시 Explore 에이전트를 병렬로 사용한다.

### B. 논의 — 정해야 할 것을 빠짐없이 정한다

목표는 하나다: **설계에 들어가는 중요한 결정 중 에이전트가 사용자 대신 추측으로 정한 것이 하나도 남지 않게 한다.** 그럴듯한 기본값을 골라 넘어가는 것이 나중에 가장 비싸게 터진다.

1. **결정 목록을 만든다.** 이번 작업이 다루는 대상(엔티티·상태·행위자·외부 연동)을 먼저 적고, 각 대상과 서로 부딪히는 대상 쌍에 네 관점을 적용해 "설계가 답해야 하는 결정"을 뽑는다. 관점은 빠뜨리지 않기 위한 도구이지 고정 목록이 아니다.
   - 구조 — 몇 개인가(1:1·1:N), 무엇으로 구별하나, 무엇에 속하나
   - 동작 — 상태 전이, 동시에 일어나면, 실패·거절·재시도, 경계 사례
   - 기술 — 저장, 인터페이스, 일관성
   - 계약 — 상태·값 집합, 중복 기준, 응답·산출물 키, 화면에 보이는 문구
2. **결정마다 셋 중 하나로 처리한다.**
   - 사용자가 말했거나 말한 것에서 바로 따라 나온다 → 그대로 쓴다 (묻지 않는다)
   - 이미 정한 방식 안의 조정값(시간 제한·개수 등) → 기본값을 정하고 "조정 가능"으로 적는다 (묻지 않는다)
   - 그 밖 → **묻는다.** 업무·동작 결정은 사용자에게 끌어내고(지어내지 않는다), 기술 결정은 선택지·장단점·추천을 제시해 사용자가 고른다
3. **질문은 걸러서 묻는다.** ① 구체적 위치(어느 화면·경로·규칙) ② 왜 아직 안 다뤄졌는지(문서·코드·관례가 이미 답하지 않는다는 근거) ③ 놔두면 생기는 결과 — 셋을 다 댈 수 있는 것만 묻는다. 걸러낸 것이 1번 목록의 중요한 결정이면 조용히 버리지 말고 결정 목록에 `N/A — {근거}` 로 남긴다(나중에 검증할 수 있게).
4. **목록을 만든다고 다 묻는 게 아니다.** 열거는 빠짐없이, 질문은 최소로 — 한 번에 4개 이하, 추천을 앞에 둔다. 물을 것이 더 남으면 다음 차례에 이어서 묻는다(4개를 넘었다고 건너뛰지 않는다).
5. **추측 찾기 (독립 검사 1).** 결정 목록의 분류를 마치면 **항상** C 로 가기 전에, 결정에 참여하지 않은 서브에이전트(general-purpose)를 1회 띄운다 — 분류가 끝났다는 판단 자체가 검사 대상이라 조건을 두지 않는다. 프롬프트에 "파일을 수정하지 말고 목록만 반환하라"를 적는다(general-purpose 는 쓰기 도구가 있다). 주는 것: 이번 논의의 사용자 발언 원문과 결정 목록(처리 결과 포함). 시키는 것: ① "사용자 발언에 근거 있음" 으로 처리한 결정 중 실제 발언으로 추적되지 않는 것 ② 목록에 아예 없는 결정. 조정값은 지적하지 않게 한다. 결과는 **문서를 고쳐 메우지 말고 사용자에게 다시 묻는다.** 답으로 바뀐 주변만 한 번 다시 보고, 그래도 남으면 사용자에게 넘긴다(반복 루프 금지).

결정 목록은 D 에서 `phases/{task-name}/README.md` 의 "결정" 절에 그대로 옮긴다(항목마다 결정과 처리 — 근거·조정값·사용자 답·`N/A — 근거`). D-4 검사와 PR 리뷰가 이것을 읽는다.

**완료 조건:** 결정 목록의 모든 항목이 근거 있음 / 조정값 / 묻고 답 받음 / `N/A — 근거` 중 하나다. 이 조건이 채워져야 C 로 간다. 뒤의 독립 검사가 잡아 주리라 믿고 여기를 얕게 하지 않는다 — 검사에서 무언가 나오면 그건 B 의 실패다.

### C. Step 설계

사용자가 구현 계획 작성을 지시하면 여러 step으로 나뉜 초안을 작성해 피드백을 요청한다.

설계 원칙:

1. **Scope 최소화** — 하나의 step에서 하나의 레이어 또는 모듈만 다룬다. 여러 모듈을 동시에 수정해야 하면 step을 쪼갠다.
2. **자기완결성** — 각 step 파일은 독립된 Codex 세션에서 실행된다. "이전 대화에서 논의한 바와 같이" 같은 외부 참조는 금지한다. 필요한 정보는 전부 파일 안에 적는다.
3. **사전 준비 강제** — 관련 문서 경로와 이전 step에서 생성/수정된 파일 경로를 명시한다. 세션이 코드를 읽고 맥락을 파악한 뒤 작업하도록 유도한다.
4. **시그니처 수준 지시** — 함수/클래스의 인터페이스만 제시하고 내부 구현은 에이전트 재량에 맡긴다. 단, 설계 의도에서 벗어나면 안 되는 핵심 규칙(멱등성, 보안, 데이터 무결성 등)은 반드시 명시한다.
5. **AC는 실행 가능한 커맨드** — "~가 동작해야 한다" 같은 추상적 서술이 아닌, 그대로 복사해 실행할 수 있는 커맨드를 포함한다. 커맨드는 `AGENTS.md`의 명령어 섹션에 정의된 것을 그대로 쓴다. PRD의 품질 기준에 해당 step과 관련된 항목이 있으면 그것도 실행 가능한 검증으로 AC에 넣는다.
6. **주의사항은 구체적으로** — "조심해라" 대신 "X를 하지 마라. 이유: Y" 형식으로 적는다.
7. **네이밍** — step name은 kebab-case slug로, 해당 step의 핵심 모듈/작업을 한두 단어로 표현한다 (예: `project-setup`, `api-layer`, `auth-flow`).

### D. 파일 생성

사용자가 C 의 step 초안을 승인하면 아래 파일들과 `phases/{task-name}/README.md`(왜·범위·B 의 "결정" 절)를 생성한다. 다 만들면 D-4 검사를 거친 뒤 E 로 실행한다.

#### D-1. `phases/index.json` (전체 현황)

여러 task를 관리하는 top-level 인덱스. 이미 존재하면 `phases` 배열에 새 항목을 추가한다.

```json
{
  "phases": [
    {
      "dir": "0-mvp",
      "status": "pending"
    }
  ]
}
```

- `dir`: task 디렉토리명.
- `status`: `"pending"` | `"completed"` | `"error"` | `"blocked"`. execute.py가 실행 중 자동으로 업데이트한다.
- 타임스탬프(`completed_at`, `failed_at`, `blocked_at`)는 execute.py가 상태 변경 시 자동 기록한다. 생성 시 넣지 않는다.

#### D-2. `phases/{task-name}/index.json` (task 상세)

```json
{
  "project": "<프로젝트명>",
  "phase": "<task-name>",
  "steps": [
    { "step": 0, "name": "project-setup", "status": "pending" },
    { "step": 1, "name": "core-types", "status": "pending" },
    { "step": 2, "name": "api-layer", "status": "pending" }
  ]
}
```

필드 규칙:

- `project`: 프로젝트명 (AGENTS.md 참조).
- `phase`: task 이름. 디렉토리명과 일치시킨다.
- `steps[].step`: 0부터 시작하는 순번.
- `steps[].name`: kebab-case slug.
- `steps[].status`: 초기값은 모두 `"pending"`.

상태 전이와 자동 기록 필드:

| 전이 | 기록되는 필드 | 기록 주체 |
|------|-------------|----------|
| → `completed` | `completed_at`, `summary`, `verification`, `concerns`(선택) | Codex 세션 (summary·verification·concerns), execute.py (timestamp) |
| → `error` | `failed_at`, `error_message` | Codex 세션 (message), execute.py (timestamp) |
| → `blocked` | `blocked_at`, `blocked_reason` | Codex 세션 (reason), execute.py (timestamp) |

**완료는 증거로 판정한다.** `verification`은 세션이 실제로 실행한 AC 명령과 종료 코드 목록(`[{"command": "...", "exit_code": 0}]`)이다. 비어 있거나 0 이 아닌 종료 코드가 있으면 execute.py는 `completed`로 인정하지 않고 재시도한다. 세션이 스스로 적는 값이라 위조를 막지는 못한다 — 최종 확인은 PR 의 CI 다. `concerns`는 끝냈지만 걸리는 점(정하지 않고 넘어간 것, 확인하지 못한 것)이고 `--pr` 이 PR 본문에 싣는다.

`summary`는 step 완료 시 산출물을 한 줄로 요약한 것으로, execute.py가 다음 step 프롬프트에 컨텍스트로 누적 전달한다. 따라서 다음 step에 유용한 정보(생성된 파일, 핵심 결정 등)를 담아야 한다.

`created_at`은 execute.py가 최초 실행 시 task 레벨에 한 번만 기록한다. step 레벨의 `started_at`도 execute.py가 각 step 시작 시 자동 기록한다. 생성 시 넣지 않는다.

#### D-3. `phases/{task-name}/step{N}.md` (각 step마다 1개)

```markdown
# Step {N}: {이름}

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/docs/ARCHITECTURE.md` ({이 step 에 필요한 절 이름})
- `/docs/adr/` (하위 파일 전부)
- `/docs/GLOSSARY.md`
- {이전 step에서 생성/수정된 파일 경로}

이전 step에서 만들어진 코드를 꼼꼼히 읽고, 설계 의도를 이해한 뒤 작업하라.

## 작업

{구체적인 구현 지시. 파일 경로, 클래스/함수 시그니처, 로직 설명을 포함.
코드 스니펫은 인터페이스/시그니처 수준만 제시하고, 구현체는 에이전트에게 맡겨라.
단, 설계 의도에서 벗어나면 안 되는 핵심 규칙은 명확히 박아넣어라.}

## Acceptance Criteria

```bash
{빌드}     # 컴파일/빌드 에러 없음 — AGENTS.md 명령어 섹션 값
{테스트}   # 테스트 통과 — AGENTS.md 명령어 섹션 값
{품질 검증} # PRD 품질 기준 중 이 step에 해당하는 항목 (없으면 생략)
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - ARCHITECTURE.md 디렉토리 구조를 따르는가?
   - ARCHITECTURE.md 외부 의존 표에 없는 외부 서비스를 끌어들이지 않았는가?
   - ADR 기술 스택을 벗어나지 않았는가?
   - AGENTS.md CRITICAL 규칙을 위반하지 않았는가?
   - GLOSSARY.md 용어를 그대로 썼는가? 금지 표현을 쓰지 않았는가?
3. 결과에 따라 `phases/{task-name}/index.json`의 해당 step을 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"`
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message": "구체적 에러 내용"`
   - 사용자 개입 필요 (API 키, 외부 인증, 수동 설정 등) → `"status": "blocked"`, `"blocked_reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- {이 step에서 하지 말아야 할 것. "X를 하지 마라. 이유: Y" 형식}
- 기존 테스트를 깨뜨리지 마라
```

#### D-4. 실행 가능성 검사 (독립 검사 2)

파일을 다 만든 뒤, 실행(E)하기 전에 **이번 대화를 보지 않은** 서브에이전트(general-purpose)를 1회 띄우고, 프롬프트에 "파일을 수정하지 말고 목록만 반환하라"를 적는다. 주는 것: `phases/{task-name}/` 파일과, 계약을 대조할 `AGENTS.md`·`docs/CONTRACT.md`·`docs/ARCHITECTURE.md`·`docs/GLOSSARY.md`(코드도 읽어도 된다). 이번 대화는 주지 않는다. 묻는 것: "각 step 파일만으로 독립 세션이 실행할 수 있나 — 막히는 곳, 물어봐야 할 곳은 어디인가". 특히 볼 것:

- 이전 대화·다른 step 을 전제한 문장 (설계 원칙 2 자기완결성)
- 계약(필드·상태 값 집합·응답 키·화면 문구)이 step 사이에 어긋나거나 한쪽에만 있는 것
- 목록에 항목을 더하거나 빼는 step 인데, 기존 테스트에 박힌 개수·이름 집합(스키마 버전, 종류 목록 등)을 고치는 일이 어느 step 에도 없는 것
- README "결정" 절의 결정·요구가 어느 step 에도 대응하지 않거나, 어떤 결정·요구에도 근거하지 않는 step

결과는 짧은 목록으로 받는다. 비었으면 실행으로 간다. 걸리는 게 있으면 사용자 결정이 필요한 것은 사용자에게 묻고, 문서 문제는 해당 step 만 고친 뒤 고친 부분만 한 번 다시 확인한다.

### E. 실행

```bash
python3 scripts/execute.py {task-name}                  # 순차 실행 (기본 엔진 codex)
python3 scripts/execute.py {task-name} --engine claude  # 처음부터 claude 로 실행
python3 scripts/execute.py {task-name} --push           # 실행 후 push
python3 scripts/execute.py {task-name} --pr             # 실행 후 push + main 행 PR (기본 경로)
```

execute.py가 자동으로 처리하는 것:

- `feat-{task-name}` 브랜치 생성/checkout
- 가드레일 주입 — AGENTS.md 만 매 step 프롬프트에 포함. docs/ 는 주입하지 않으며, 각 step.md 의 "읽어야 할 파일" 절이 필요한 문서·절을 가리킨다
- 컨텍스트 누적 — 완료된 step의 summary를 다음 step 프롬프트에 전달
- 자가 교정 — 실패 시 최대 3회 재시도하며, 이전 에러 메시지를 프롬프트에 피드백
- 엔진 폴백 — codex 가 사용량 한도로 실패하면 같은 step 부터 claude 독립 세션으로 전환
- 2단계 커밋 — 코드 변경(`feat`)과 메타데이터(`chore`)를 분리 커밋
- 타임스탬프 — started_at, completed_at, failed_at, blocked_at 자동 기록
- `--pr` — 끝에 `feat-{task-name}` 를 push 하고 `main` 행 PR 을 연다(제목 `feat: {phase}`, 본문은 step summary 목록). PR 이 이미 있으면 push 로 갱신만 한다

### F. PR 이후 — CI·리뷰·병합 (`/docs/adr/0030-pr-ci-review-flow.md`)

로컬에서 `main` 에 병합하지 않는다. PR 이 열리면 GitHub 이 나머지를 맡는다.

| 단계 | 누가 | 하는 일 |
|---|---|---|
| CI | `.github/workflows/ci.yml` | `ruff check` · `pytest -q`. 필수 검사 `test` |
| 리뷰 | `.github/workflows/claude-review.yml` | PR 이 열리거나 커밋이 올라올 때마다 Claude 가 인라인·요약 코멘트. 지적은 `fix`·`decision`·`follow-up`. 필수 검사 `review` |
| 수정 | 사람 | 리뷰를 읽고 직접 고쳐 push 하거나, PR 코멘트에 `@claude fix 지적 1·3 고쳐` 처럼 맡긴다(`claude.yml`). push 하면 CI·리뷰가 다시 돈다 |
| 병합 | 사람 | 두 필수 검사가 초록이면 GitHub 에서 **squash merge**. 브랜치는 자동 삭제 |
| 반영 | 사람 | 필요하면 셀프호스트에 `deploy/selfhost/install.sh` |

`follow-up` 지적은 이슈를 자동으로 만들지 않는다. 등록은 사람이 판단한다.

에러 복구:

- **error 발생 시**: `phases/{task-name}/index.json`에서 해당 step의 `status`를 `"pending"`으로 바꾸고 `error_message`를 삭제한 뒤 재실행한다.
- **blocked 발생 시**: `blocked_reason`에 적힌 사유를 해결한 뒤, `status`를 `"pending"`으로 바꾸고 `blocked_reason`을 삭제한 뒤 재실행한다.
