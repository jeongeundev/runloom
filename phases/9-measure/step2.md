# Step 2: 러너 보고 — 폴더 커밋·비용·토큰

## 읽어야 할 파일

- AGENTS.md
- phases/9-measure/README.md (계획 기본값·지표 정의 — 이 phase 의 기준)
- phases/9-measure/index.json (완료 step 의 summary)
- docs/adr/0015-measurement-events-and-baseline.md (step 0 산출물. step 0 에서는 새로 만든다)
- docs/ARCHITECTURE.md ("측정 — phase 9" 절. step 0 에서는 새로 만든다)
- docs/GLOSSARY.md
- src/workflow/contracts/v1.py (step 1 산출물)
- src/workflow/connector/runner.py (이벤트 전송: started·result_ready·failed)
- src/workflow/connector/adapter.py (`AdapterOutput`, `progress(runtime_ref=...)`)
- src/workflow/connector/claude.py, src/workflow/connector/codex.py, src/workflow/connector/local_tool.py
- src/workflow/connector/git_ops.py (`head_sha`, `is_dirty`), src/workflow/connector/state.py (로컬 등록의 repo_path)
- tests/workflow/connector/ (test_runner.py, test_claude.py, test_codex.py, test_local_tool.py)

먼저 실제 파일과 이전 step 산출물을 읽는다. 이름·칸·함수 시그니처는 step 0 의 ADR-0015·ARCHITECTURE 가 고정한 것을 따르고, 바꿔야 하면 같은 이름의 문서·계약·테스트를 함께 갱신한다. 대화 이력을 전제로 판단하지 않는다.

## 작업

연결 프로그램만 바꾼다. 서버는 건드리지 않는다.

1. `AdapterOutput` 에 `usage: ExecutionUsage | None = None` 을 더한다. Claude 어댑터는 CLI JSON 결과의 `total_cost_usd`·`usage.input_tokens`·`usage.output_tokens`(step 0 이 ARCHITECTURE 에 적은 키)를 읽는다. 캐시 토큰 등 다른 키를 합칠지 여부는 ARCHITECTURE 에 적힌 대로 따른다. Codex 는 JSON 이벤트에서 토큰을 얻을 수 있으면 토큰만, 아니면 None. 실패 경로(한도·비정상 종료)에서도 결과 JSON 을 읽었다면 사용량을 채운다.
2. 러너가 `started` 이벤트를 보낼 때 로컬 등록 저장소 폴더(등록된 `repo_path`, worktree 아님)의 HEAD·dirty 여부를 `folder_commit`·`folder_dirty` 로 싣는다. 읽기 실패(Git 아님·권한 등)는 두 칸을 비우고 실행을 계속한다. 로컬 등록이 없는 실행(진단 등)은 비운다.
3. `result_ready`·`failed` 이벤트에 `usage` 를 싣는다. 모르면 칸을 생략(None)한다.

핵심 규칙:
- 저장소 경로·폴더 이름은 중앙에 보내지 않는다. 커밋 SHA 와 dirty 여부만 보낸다. 이유: 폴더·역할 이름은 러너 로컬 설정에만 둔다(MVP_PLAN 3절).
- 실행 인자·환경은 바꾸지 않는다(비용을 얻으려고 CLI 인자를 추가하지 않는다).
- `src/workflow/scripted/` 대본 에이전트는 바꾸지 않는다. 공개 데모 재생에 영향을 주지 않는다.

## 테스트 먼저

- `test_claude.py`: 비용·토큰이 있는 결과 JSON, 없는 결과 JSON, 한도 실패 JSON 에서 `usage` 추출.
- `test_codex.py`: 토큰 이벤트가 있을 때/없을 때.
- `test_runner.py`: `started` 이벤트에 등록 폴더 HEAD·dirty 가 실리는지(임시 git 저장소), 폴더가 Git 이 아니면 칸이 비고 실행은 계속되는지, `result_ready`·`failed` 에 `usage` 가 실리는지, 저장소 경로 문자열이 어떤 이벤트에도 없는지.

소스 변경 전에 `tests/` 미러 경로에 실패 테스트를 작성하고 실패 원인이 의도한 것인지 확인한다(`tdd-guard.sh` 가 테스트 없는 소스 작성을 막는다). 구현 후 해당 테스트와 전체 회귀를 통과시킨다.

## Acceptance Criteria

```bash
python3 -m pytest -q
python3 -m ruff check .
```

위 커맨드와 이 step 의 테스트 항목을 모두 확인한다.

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 규칙을 확인한다: `domain/` 은 FastAPI·sqlite3·HTTPX·subprocess·Git 을 import 하지 않는다 / `server/` 와 `connector/` 는 서로 import 하지 않고 `contracts/` 만 공유한다 / 비밀값(`WORKFLOW_GITHUB_TOKEN`, 연결 토큰 등)은 환경변수에서만 읽고 DB·로그·응답·템플릿에 넣지 않는다 / 이름은 GLOSSARY 식별자를 쓴다.
3. 성공이면 `phases/9-measure/index.json` 의 step 2 만 `completed` 로 바꾸고 `summary` 에 생성·수정 파일, 결정, 검증 결과(테스트 수), 다음 step 이 주의할 점을 한 줄로 남긴다.
4. 3회 수정 후에도 실패하면 `error` + `error_message`, 사용자 결정·외부 자격이 필요하면 `blocked` + `blocked_reason` 을 기록한다. 실행하지 않은 검증을 `completed` 로 표시하지 않는다.

## 금지사항

- `main`·공개 데모 VM 을 변경·배포하지 않는다. 이유: 이 phase 는 `service` 기반 실서비스 작업이다.
- 실제 GitHub·유료 모델을 호출하지 않는다. 이유: 모든 step 은 대역(httpx `MockTransport`, fake 도구)으로 검증한다. 실제 기준선 가져오기는 phase 뒤 사용자 지시로 한다.
- 사용자 변경을 삭제하거나 stash·reset 하지 않는다. 커밋은 하네스가 한다.
- 이 step 범위 밖 모듈을 "개선"하지 않는다. 이유: step 마다 한 레이어만 바꿔 회귀 원인을 좁힌다.
- 모르는 비용·시각을 0 이나 현재 시각으로 채우지 않는다. 이유: "모름"과 0 은 다른 사실이다.
