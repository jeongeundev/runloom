# Step 5: 러너 — 수정 결과 브랜치 push

## 읽어야 할 파일

- AGENTS.md
- phases/12-real-repo/README.md (계획 기본값 — 이 phase 의 기준)
- phases/12-real-repo/index.json (완료 step 의 summary)
- docs/adr/0018-real-repo-cycle.md (step 0 산출물. step 0 에서는 새로 만든다)
- docs/ARCHITECTURE.md ("실제 저장소 순환 — phase 12" 절. step 0 에서는 새로 만든다)
- docs/GLOSSARY.md
- src/workflow/connector/git_ops.py, src/workflow/connector/local_tool.py (수정 실행의 `ready_for_review` 결과 생성부), src/workflow/connector/runner.py
- src/workflow/contracts/v1.py (`ResultReadyData`, `_OmitUnknownMeasure`)
- src/workflow/server/machine_api.py·src/workflow/adapters/repo.py (result_ready 이벤트 저장)
- tests/workflow/connector/test_git_ops.py, tests/workflow/connector/test_local_tool.py, tests/workflow/contracts/test_v1.py

먼저 실제 파일과 이전 step 산출물을 읽는다. 이름·경로·API 필드는 step 0 의 ADR-0018·ARCHITECTURE 가 고정한 것을 따르고, 바꿔야 하면 같은 이름의 문서·테스트를 함께 갱신한다. 대화 이력을 전제로 판단하지 않는다.

## 작업

- `git_ops.push_task_branch(repo, task_id) -> bool`: 고정 인자 `git push origin task/<id>:refs/heads/task/<id>`(force 없음). 브랜치 이름은 `task/` + 서버가 준 task_id 에 기존 `_safe` 규칙을 적용한 값만 — `ensure_worktree` 가 만든 브랜치 이름과 같은 함수로 만든다. 실패(자격·거부·네트워크)는 False + 로그(원격 URL·자격 문자열은 로그에서 가린다). 시간 제한. 사용자 git 훅은 그대로 둔다.
- 수정 실행이 `ready_for_review` 를 낼 때(검증 통과·결과 커밋 있음) 등록 폴더에서 push 하고 결과를 `ResultReadyData` 새 선택 칸(ADR-0018 이름, 예 `branch_pushed`)으로 보고한다. push 실패해도 실행 결과는 그대로 `ready_for_review`(검토는 로컬 커밋으로 진행된다). 재작업은 같은 브랜치에 fast-forward 로 다시 push.
- 검토(`code_review`) 실행은 push 하지 않는다.
- 서버는 받은 값을 실행 기록에 저장한다(측정 칸처럼 모르면 None). 계약 호환: 칸 없는 옛 러너 이벤트도 받는다.

## 테스트 먼저

- 임시 bare origin 클론에서 수정 결과 후 bare 에 `task/<id>` 가 생김, 재작업 뒤 fast-forward 로 갱신, force 인자 없음(인자 배열 단정).
- origin 쓰기 불가(권한 없는 경로) → False, 결과는 여전히 ready_for_review, 보고 칸 False.
- 검토 실행은 push 호출 없음.
- 계약: 칸 있음/없음 모두 파싱, 서버 저장값 확인.

소스·배포 파일 변경 전에 `tests/` 미러 경로에 실패 테스트를 작성하고 실패 원인이 의도한 것인지 확인한다(`tdd-guard.sh` 가 테스트 없는 소스 작성을 막는다). 구현 후 해당 테스트와 전체 회귀를 통과시킨다.

## Acceptance Criteria

```bash
python3 -m pytest -q
python3 -m ruff check .
```

위 커맨드와 이 step 의 테스트 항목을 모두 확인한다.

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 규칙을 확인한다: `domain/` 은 FastAPI·sqlite3·HTTPX·subprocess·Git 을 import 하지 않는다 / `server/` 와 `connector/` 는 서로 import 하지 않고 `contracts/` 만 공유한다 / 외부 입력(요청 본문·이슈 본문·모델 응답)에서 명령·경로·브랜치 이름을 받아 실행하지 않는다 — git 인자 배열은 러너 어댑터가 고정한다 / 비밀값(연결 토큰, GitHub App 비밀·설치 토큰, PAT, 알림 웹훅 URL, 러너 로컬 등록의 환경변수 값)은 DB·로그·응답·템플릿·백업·중앙에 넣지 않는다 / 기본 모드(demo)와 phase 8·11 의 기존 GitHub 순환이 그대로 동작한다.
3. 성공이면 `phases/12-real-repo/index.json` 의 step 5 만 `completed` 로 바꾸고 `summary` 에 생성·수정 파일, 결정, 검증 결과(테스트 수), 다음 step 이 주의할 점을 한 줄로 남긴다.
4. 3회 수정 후에도 실패하면 `error` + `error_message`, 사용자 결정·외부 자원이 필요하면 `blocked` + `blocked_reason` 을 기록한다. 실행하지 않은 검증을 `completed` 로 표시하지 않는다.

## 금지사항

- 실제 GitHub(api.github.com·github.com)와 실제 Discord 를 호출하지 않는다(docs.github.com·discord.com/developers 문서 읽기는 허용). 이유: 모든 step 은 httpx `MockTransport`·가짜 GitHub·로컬 bare 저장소로 검증한다. 실연동은 phase 뒤 사용자와 함께 한다.
- `/Users/kje/demo/OpenArchive` 와 그 worktree 를 읽거나 바꾸지 않는다. 이유: 사용자가 개발 중인 실제 저장소다. 테스트는 임시 디렉터리의 가짜 저장소로 한다.
- 사용자가 띄워 둔 셀프호스트(compose 프로젝트 `runloom`, 포트 8000, 볼륨 `runloom_workflow-data`)·`deploy/selfhost/.env`·러너 홈(`~/Library/Application Support/workflow-connector/`)·launchd 를 건드리지 않는다. 컨테이너를 띄우거나 멈추지 않는다.
- 러너가 기본 브랜치에 push 하거나 force push 하지 않는다. 병합·이슈 닫기는 하지 않는다 — 사람만 한다.
- `main`·공개 데모 VM·demo 모드 기본 동작을 바꾸지 않는다.
- 사용자 변경을 삭제하거나 stash·reset 하지 않는다. 커밋은 하네스가 한다.
- 이 step 범위 밖 모듈을 "개선"하지 않는다.
