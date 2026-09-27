# Phase 12 — 실제 저장소 순환: 러너 한 명령, 최신 기준 커밋, 초안 PR, 알림

작성일: 2026-09-27. 상태: 구현 계획 작성 완료, 모든 step pending. 목표([MVP 계획](../../docs/product/MVP_PLAN.md) 10절): 2026-10-02 까지 Runloom 단독으로 OpenArchive(`jeongeundev/OpenArchive`, 공개, 기본 브랜치 `main`) 실제 이슈 3건 이상을 이슈 → 수정 → 검토 → 사람 차례 알림까지 사람의 전달 없이 진행하고, 도입 전후를 지표로 보인다.

## 조사로 드러난 빈칸 (2026-09-27)

- 러너 등록이 운영자의 에이전트 선등록(`local_registration_id`·능력 코드 입력) + `connect` + `register --id --repository-id --verify` 네 단계다. 사용자는 내부 ID·형식값 입력을 병목으로 본다.
- 새 업무 기준 커밋이 `register` 때 HEAD 로 고정된다(`worker._start_fix`). 러너는 fetch 하지 않는다. 실제 OpenArchive 로컬 클론은 `origin/main` 보다 12 커밋 뒤였다 — 사용자는 다른 곳에서 개발해 push 한다.
- 검증·도구 프로세스 환경은 허용 목록만 받는다. worktree 에는 `backend/.venv`·`frontend/node_modules` 가 없다(git 무시 대상).
- 결과 코드는 러너 Mac 의 `task/<task_id>` 브랜치에만 남는다(ADR-0014: 자동 push/PR 안 함). 사람이 폴더를 찾아가 push·PR 해야 한다.
- 범용 알림 웹훅이 없다(n8n callback 만).

## 계획 기본값 (사용자 결정 2026-09-27, step 0 이 ADR-0018 로 고정)

1. **러너 한 명령**: 저장소 카드 [러너 붙이기] → 코드가 들어간 명령 한 줄(`install-runner.sh --server --code --repo <폴더>`) → setup(connect + register) → launchd. register 가 에이전트를 만든다(이름=폴더, 능력 `code.fix`+`code.review`, 저장소=GitHub owner/name). 수정과 검토는 같은 에이전트, 별도 실행·worktree.
2. **기준 커밋 = GitHub 기본 브랜치 최신**: 러너가 fetch 해 등록별 `origin/<기본>` 커밋을 claim 때 보고, 서버가 `agents.base_commit` 갱신. 재작업은 검토한 결과 커밋에서.
3. **작업 복사본 준비물 = 원본 폴더 링크**(사용자 선택): 러너 로컬 등록의 `--link backend/.venv --link frontend/node_modules` 를 worktree 에 심볼릭 링크(git 제외). `--env` 로 검증·도구 환경 추가(값은 러너 로컬만).
4. **테스트 DB = 에이전트 전용**(사용자 선택): 실연동 때 pgvector 를 5434 에 따로 띄우고 `--env DATABASE_URL=…:5434/…` 로 넘긴다. 이 Mac 의 5433 은 `opensql-db-1` 이 쓰고 있다.
5. **결과 코드 = GitHub 초안 PR**(사용자 선택): 수정 결과마다 러너가 `task/<id>` 를 origin 에 push(사용자 로컬 git 자격, force 없음) → 검토 승인 뒤 중앙이 App 으로 초안 PR(`Fixes #N`) → 사람이 병합 → 동기화가 병합을 보고 Task 완료(지표 `pr_merged_at`). 병합·이슈 닫기는 사람만. ADR-0014 의 "자동 push/PR 안 함"을 대체한다. App 권한에 Pull requests 쓰기 추가 — 이미 만든 App(`runloom-gwufov`)은 사용자가 권한을 올리고 설치에서 승인.
6. **알림 웹훅**: 사람 차례(새 사람 요청·PR 열림)·업무 실패 → 등록 URL 하나(비밀 저장소). Discord 호스트면 `{"content"}`, 그 밖 JSON. 대기열·재시도.

지시만 한 이슈를 처리한다(phase 11 그대로: [에이전트에게 맡기기] 또는 `runloom` 라벨). 사용자가 직접 고치고 있는 이슈는 맡기지 않으면 겹치지 않고, 이슈가 닫히면 업무는 `source_closed` 로 멈춘다.

## Step 목록

| Step | 이름 | 산출물 |
|---|---|---|
| 0 | real-repo-design | ADR-0018, ADR-0014 대체 줄, ARCHITECTURE 절, CONTRACT 예시, GLOSSARY |
| 1 | self-register-api | register 가 에이전트 생성(멱등), 수정·검토 같은 에이전트 매칭 |
| 2 | connector-setup | `connector setup`, register 기본값, 로컬 등록 `links`·`env` |
| 3 | base-tracking | fetch·기준 커밋 보고(claim 선택 칸)·서버 반영 |
| 4 | worktree-env | worktree 링크(git 제외)·환경변수 전달·값 가림 |
| 5 | branch-push | 수정 결과 `task/<id>` push, `ResultReadyData` 보고 |
| 6 | draft-pr | 스키마 v8, 검토 승인 → 초안 PR, 병합 추적 → 완료, App 권한 |
| 7 | notify-core | 알림 대기열(v8)·전송·재시도 |
| 8 | notify-ui | 알림 URL 설정·테스트 보내기 |
| 9 | runner-ui | [러너 붙이기] 버튼, `install-runner.sh --server --code --repo` |
| 10 | real-repo-verify | 가짜 GitHub·bare 저장소 e2e, SELFHOST·인계·VERIFICATION_LOG 틀 |

모든 step 은 가짜 GitHub·임시 bare 저장소·가짜 알림 수신으로 검증한다. 늦어지면 step 8(화면)을 환경변수 설정으로 줄이고, 그다음 step 9 의 install-runner 통합을 뺀다(setup 명령은 유지).

## phase 뒤 실연동 (사용자와 함께, step 아님)

1. `service` 병합 → `deploy/selfhost/install.sh` 재실행(스키마 v8).
2. GitHub App 권한 올리기(Pull requests 쓰기) → 설치에서 승인.
3. 에이전트 전용 pgvector 5434, OpenArchive `scripts/check.sh` 의 pytest 를 `python -m pytest` 로 한 줄 수정(링크된 편집 설치 venv 가 원본 코드를 가리키는 문제), 원본 폴더 `frontend` 에서 `npm install`. Next 가 링크된 `node_modules` 를 거부하면 frontend 만 업무마다 설치로 바꾼다.
4. [러너 붙이기] → 명령 실행, Discord 웹훅 URL 등록 → [테스트 보내기].
5. 사용자가 손대지 않은 이슈 3건 이상 [맡기기] → PR 병합까지, VERIFICATION_LOG 기록(phase 11 미확인 `setup_action`·설치 URL `state` 포함).

## 실행

```bash
git status --short            # 깨끗해야 한다
git branch --show-current     # service
python3 scripts/execute.py 12-real-repo --engine claude
```

사용자가 띄워 둔 셀프호스트(compose 프로젝트 `runloom`)와 `/Users/kje/demo/OpenArchive` 는 건드리지 않는다.
