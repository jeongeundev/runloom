#!/bin/bash
# Verify Hook — Stop
# 턴 종료 시 프로젝트를 검증한다. 존재하는 툴체인만 실행하므로
# 하네스 단독 상태에서도, 대상 프로젝트가 스캐폴딩된 뒤에도 동작한다.
# 현재 감지 대상: Node(package.json) / Python(pytest). 그 외 스택은 검증이 걸리지 않는다.
# 실패하면 exit 2 로 실패 내용을 Claude 에게 돌려주고 수정을 이어가게 한다.

INPUT=$(cat)

# stop hook 때문에 이미 재개된 턴이면 재검증하지 않는다 (무한 루프 방지)
if [ "$(echo "$INPUT" | jq -r '.stop_hook_active // false')" = "true" ]; then
  exit 0
fi

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
ROOT=$(git rev-parse --show-toplevel 2>/dev/null || pwd)
cd "$ROOT" || exit 0

# --- 이번 턴이 검증 대상인지 판정 ---
# 작업 트리만 보면, 세션이 커밋하고 턴을 끝냈을 때 트리가 깨끗해져서
# 검증이 통째로 빠진다 (execute.py 프리앰블이 세션에게 커밋을 지시한다).
# 마지막으로 검증을 통과한 커밋을 .git 에 기록해 두고, HEAD 가 그 뒤로
# 움직였으면 커밋된 변경도 검증 대상에 포함한다.
MARK="$(git rev-parse --git-dir 2>/dev/null || echo .)/harness-verified"
HEAD_SHA=$(git rev-parse HEAD 2>/dev/null || echo "")
PREV=$(cat "$MARK" 2>/dev/null || echo "")

WORKTREE=$( { git diff --name-only HEAD 2>/dev/null; \
              git ls-files --others --exclude-standard 2>/dev/null; } | sort -u )

COMMITTED=""
if [ -n "$HEAD_SHA" ] && [ "$HEAD_SHA" != "$PREV" ]; then
  if [ -n "$PREV" ] && git cat-file -e "${PREV}^{commit}" 2>/dev/null; then
    COMMITTED=$(git diff --name-only "$PREV" HEAD 2>/dev/null)
  else
    # 마커가 없거나 (rebase 등으로) 사라진 커밋이면 마지막 커밋만 본다
    COMMITTED=$(git show --name-only --pretty=format: HEAD 2>/dev/null)
  fi
fi

CHANGED=$(printf '%s\n%s\n' "$WORKTREE" "$COMMITTED" | sort -u | grep -v '^$')

# 변경이 없거나 문서(.md)만 바뀐 턴은 검증하지 않는다.
# 대화만 주고받은 턴에서 빌드/테스트가 도는 것을 막는다.
if [ -z "$(printf '%s\n' "$CHANGED" | grep -v '\.md$')" ]; then
  exit 0
fi

FAILED=""

# --- TDD 불변식 ---
# tdd-guard.sh 는 PreToolUse[Edit|Write] 라 Bash 리다이렉션(cat > f, tee, sed -i)으로
# 만든 파일을 막지 못한다. 여기서 작업 트리를 다시 확인해 우회를 잡는다.
# 판정은 tdd-guard.sh 를 그대로 호출해 재사용한다 — 규칙을 두 곳에서 관리하지 않기 위해.
MISSING=""
while IFS= read -r f; do
  [ -n "$f" ] && [ -f "$f" ] || continue
  if printf '{"tool_input":{"file_path":"%s"}}' "$ROOT/$f" \
     | bash "$SCRIPT_DIR/tdd-guard.sh" 2>/dev/null \
     | grep -q '"permissionDecision": "deny"'; then
    MISSING="${MISSING}
  - ${f}"
  fi
done < <(printf '%s\n' "$CHANGED" | grep -E '\.(ts|tsx|js|jsx|py)$')

if [ -n "$MISSING" ]; then
  FAILED="${FAILED}

===== TDD 위반 — 테스트 없는 소스 파일 =====
아래 파일에 대응하는 테스트가 없습니다. 구현보다 테스트를 먼저 작성하세요.${MISSING}"
fi

# --- 툴체인 검증 ---
run() {
  LABEL=$1
  shift
  if ! OUTPUT=$("$@" 2>&1); then
    FAILED="${FAILED}

===== ${LABEL} 실패 =====
${OUTPUT}"
  fi
}

# Node 프로젝트 — package.json 에 실제로 정의된 스크립트만 실행
if [ -f package.json ]; then
  for SCRIPT in lint build test; do
    if jq -e --arg s "$SCRIPT" '.scripts[$s] // empty' package.json >/dev/null 2>&1; then
      run "npm run $SCRIPT" npm run "$SCRIPT"
    fi
  done
fi

# Python 프로젝트 — 테스트 파일이 있을 때만 pytest
PY_TESTS=$(find . \( -name 'test_*.py' -o -name '*_test.py' \) \
  -not -path './node_modules/*' -not -path './.venv/*' -not -path './.git/*' | head -1)

# pytest 실행파일 대신 python3 -m 을 쓴다 — pyenv shim 은 있는데
# 활성 버전에는 pytest 가 없는 경우를 피하기 위해.
if [ -n "$PY_TESTS" ] && python3 -c 'import pytest' >/dev/null 2>&1; then
  run "pytest" python3 -m pytest -q
fi

if [ -n "$FAILED" ]; then
  # 실패 시에는 마커를 갱신하지 않는다 — 고칠 때까지 매 턴 다시 잡히도록.
  echo "검증 실패 — 아래 오류를 수정하세요.${FAILED}" >&2
  exit 2
fi

[ -n "$HEAD_SHA" ] && printf '%s' "$HEAD_SHA" > "$MARK" 2>/dev/null

exit 0
