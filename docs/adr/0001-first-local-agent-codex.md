# ADR-0001: 첫 로컬 에이전트는 Codex CLI 1종

결정일: 2026-09-20. 사용자 확정.

**결정**: 첫 구현의 로컬 연결 어댑터는 Codex CLI 하나만 지원한다. Claude Code는 후속 어댑터이며 첫 데모 범위가 아니다.

**이유**: 로컬 `codex-cli 0.155.1`에서 비대화형 실행에 필요한 `exec --json`, `-C`, `--output-schema`, `--output-last-message`, `--sandbox` 옵션을 확인했다(2026-09-20, 도움말만 실행). 기존 하네스 `scripts/execute.py`가 `codex exec --json` 출력을 이미 다루고 있어 파싱·실패 구분 경험을 재사용할 수 있다.

**트레이드오프**: Claude Code만 쓰는 사용자는 첫 데모에서 연결할 수 없다. 어댑터 경계(`src/workflow/connector/`)를 두어 후속 추가가 가능하게 하되, 두 도구 공용 추상화를 미리 만들지 않는다. Codex의 저장된 인증 재사용·모델 실행·worktree에서의 프로젝트 설정 재사용은 아직 검증하지 않았으므로 구현 계획의 첫 로컬 step에서 확인한다.
