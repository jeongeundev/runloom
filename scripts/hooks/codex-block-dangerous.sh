#!/bin/bash
# Codex PreToolUse — 위험 명령 차단. .claude/settings.json 의 Bash 인라인 훅과 같은 규칙.
# 전역 ~/.codex/hooks.json 이 저장소의 이 파일을 찾아 실행한다. matcher 없이 모든 도구에
# 걸리므로 command 필드가 없는 도구(편집 등)는 그냥 통과시킨다.
# codex 의 command 는 문자열 또는 argv 배열 양쪽으로 온다.

INPUT=$(cat)
CMD=$(printf '%s' "$INPUT" | jq -r '
  (.tool_input.command // "") as $c
  | if ($c | type) == "array" then ($c | join(" ")) else ($c | tostring) end')

if printf '%s' "$CMD" | grep -qE 'rm[[:space:]]+-rf|git[[:space:]]+push[[:space:]]+--force|git[[:space:]]+reset[[:space:]]+--hard|DROP[[:space:]]+TABLE'; then
  jq -cn '{
    hookSpecificOutput: {
      hookEventName: "PreToolUse",
      permissionDecision: "deny",
      permissionDecisionReason: "BLOCKED: 위험한 명령어가 감지되었습니다 (rm -rf / git push --force / git reset --hard / DROP TABLE)."
    }
  }'
fi

exit 0
