#!/bin/bash
# Codex Stop — verify.sh 를 그대로 실행하고 결과만 codex 형식으로 바꾼다.
# verify.sh 는 실패 시 exit 2 + stderr 메시지 (Claude Code 규약). codex 는 stdout JSON 의
# {decision: "block", reason} 으로 턴을 이어 자가교정시킨다.
# 변경 없는 턴 스킵과 stop_hook_active 에 의한 무한루프 방지는 verify.sh 안에서 처리된다
# (codex Stop 페이로드도 같은 필드명을 쓴다).

INPUT=$(cat)
SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)

MSG=$(printf '%s' "$INPUT" | bash "$SCRIPT_DIR/verify.sh" 2>&1 >/dev/null)
if [ $? -ne 0 ]; then
  jq -cn --arg r "$MSG" '{decision: "block", reason: $r}'
fi

exit 0
