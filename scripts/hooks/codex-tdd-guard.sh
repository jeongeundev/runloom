#!/bin/bash
# Codex PreToolUse — TDD 가드. 판정은 tdd-guard.sh 에 위임한다 (규칙을 두 곳에서 관리하지 않기 위해).
# codex 의 편집 도구는 apply_patch 라 file_path 대신 패치 텍스트(.tool_input.command)가 온다.
# 거기서 Add/Update File 경로를 뽑는다. Delete 는 구현 코드를 쓰는 게 아니므로 제외.
# 전역 ~/.codex/hooks.json 이 matcher 없이 모든 도구에 걸므로, 편집이 아니면 그냥 통과.

INPUT=$(cat)
SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
ROOT=$(git rev-parse --show-toplevel 2>/dev/null || pwd)

# 1) Edit/Write 가 file_path 를 직접 주면 그대로 쓴다.
FILES=$(printf '%s' "$INPUT" | jq -r '.tool_input.file_path // empty')

# 2) apply_patch 면 패치 텍스트에서 대상 파일을 추출한다.
if [ -z "$FILES" ]; then
  PATCH=$(printf '%s' "$INPUT" | jq -r '
    (.tool_input.command // "") as $c
    | if ($c | type) == "array" then ($c | join("\n")) else ($c | tostring) end')
  FILES=$(printf '%s\n' "$PATCH" | sed -nE 's/^\*\*\* (Add|Update) File: (.+)$/\2/p')
fi

[ -z "$FILES" ] && exit 0

# 패치 경로는 저장소 루트 기준 상대경로다. tdd-guard.sh 가 테스트 파일 존재를 확인할 수 있게
# 절대경로로 바꿔 넘긴다. 하나라도 deny 면 그 출력을 그대로 돌려준다 (codex 도 같은 형식을 쓴다).
while IFS= read -r f; do
  [ -z "$f" ] && continue
  case "$f" in /*) ;; *) f="$ROOT/$f" ;; esac
  OUT=$(jq -cn --arg p "$f" '{tool_input: {file_path: $p}}' | bash "$SCRIPT_DIR/tdd-guard.sh")
  if printf '%s' "$OUT" | grep -q '"permissionDecision": *"deny"'; then
    printf '%s\n' "$OUT"
    exit 0
  fi
done <<< "$FILES"

exit 0
