# ADR-0030: phase 결과는 PR 로 — CI·Claude 리뷰를 필수 검사로, squash 병합

결정일: 2026-10-19. 사용자 결정 "실무에서 사용하고 있는 방식으로 진행해보고 싶어". OpenArchive 는 같은 흐름을 로컬 오케스트레이터(`scripts/ship.py`)로 강제했는데, 이 저장소는 그 관문을 GitHub 에 맡긴다.

## 결정

1. **로컬 병합을 그만두고 PR 로 올린다.** [ADR-0029](0029-demo-shutdown-main-single-branch.md) 3항의 "`main` 에 `--no-ff` 로 병합"을 대체한다. phase 는 `python3 scripts/execute.py {task} --pr` 로 돌리고, execute.py 가 끝에 `feat-{phase}` 를 push 하고 `main` 으로 가는 PR 을 연다(이미 있으면 push 로 갱신). PR 단위는 phase 하나다.
2. **CI** — `.github/workflows/ci.yml` 이 PR·`main` push 마다 `ruff check` 와 `pytest -q` 를 돈다. 로컬 `verify.sh` 훅과 같은 명령이다.
3. **리뷰** — `.github/workflows/claude-review.yml` 이 PR 이 열리거나 새 커밋이 올라올 때마다 Claude 리뷰 코멘트를 남긴다. 지적은 `fix`·`decision`·`follow-up` 으로 분류한다(ship.py 와 같은 분류). 리뷰는 고치지 않는다. 고칠지는 사람이 정하고, 맡길 때는 PR 코멘트에 `@claude …` 를 쓴다(`claude.yml`). 인증은 구독 OAuth 토큰(`CLAUDE_CODE_OAUTH_TOKEN` 저장소 secret)이다.
4. **관문** — `main` 브랜치 보호로 PR 필수, 필수 검사 `test`(CI)·`review`(Claude 리뷰), 관리자에게도 적용. 리뷰가 필수 검사라 PR head 마다 리뷰가 한 번은 돈다 — ship.py 의 `reviewed_head` 검사를 GitHub 이 대신한다. 1인 저장소라 승인 수는 0 이다.
5. **병합은 squash 만.** step 별 커밋은 PR 에 남고 `main` 에는 phase 단위 커밋 하나가 남는다. 병합한 브랜치는 자동 삭제한다.

## 결과

- 문서만 바꾸는 작업도 `main` 에 직접 push 하지 않고 PR 로 올린다.
- 리뷰 워크플로우는 `main` 에 있는 파일과 같아야 돈다(액션의 보안 검사). 워크플로우를 고치는 PR 에서는 `review` 검사가 실패할 수 있어, 그 PR 은 브랜치 보호를 잠시 풀고 병합한다.
- `@claude` 가 PR 브랜치에 push 하려면 Claude GitHub App 이 저장소에 설치돼 있어야 한다(App 토큰으로 push 해야 CI 가 다시 돈다).
- OAuth 토큰이 만료되면 `review` 가 실패해 병합이 막힌다. `claude setup-token` 으로 다시 만들어 secret 을 갱신한다.
- ship.py 의 변조 검사(테스트 삭제·`skip` 추가 감지)는 옮기지 않았다. 리뷰 프롬프트의 지적 항목으로만 남는다.
