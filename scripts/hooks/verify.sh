#!/bin/bash
# Verify Hook — Stop
# 턴 종료 시 프로젝트를 검증한다. 존재하는 툴체인만 실행하므로
# Python 하네스 단독 상태에서도, 대상 프로젝트(Next.js)가 스캐폴딩된 뒤에도 동작한다.
# 실패하면 exit 2 로 실패 내용을 Claude 에게 돌려주고 수정을 이어가게 한다.

INPUT=$(cat)

# stop hook 때문에 이미 재개된 턴이면 재검증하지 않는다 (무한 루프 방지)
if [ "$(echo "$INPUT" | jq -r '.stop_hook_active // false')" = "true" ]; then
  exit 0
fi

ROOT=$(git rev-parse --show-toplevel 2>/dev/null || pwd)
cd "$ROOT" || exit 0

FAILED=""

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
  echo "검증 실패 — 아래 오류를 수정하세요.${FAILED}" >&2
  exit 2
fi

exit 0
