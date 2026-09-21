"""대본 Claude — `claude -p --output-format json … --json-schema <schema>` 인자 형식, 프롬프트는 stdin, 작업 위치는 cwd.

connector `ClaudeAdapter.build_argv` 는 바뀌지 않는다. 배포·로컬 스택이 이 모듈을 `claude` 이름의 래퍼로 PATH 앞에 둔다.
같은 흐름(대기 → 인계 응답 → 수정 적용)으로 고친 뒤 stdout 에 `type=result` 봉투 **한 덩어리**를 쓴다 — 키는
`connector/claude.py` 의 `ClaudeResult`·`ClaudeStructuredOutput` 이 읽는 것과 같다. 인자에서 경로를 받지 않는다.
사용자 정의 종류(프롬프트 첫 줄 `# 업무 종류: …`, `build_readonly_argv` — cwd 는 인계 디렉터리)는 인계 응답 찾기·수정
적용을 건너뛰고 `--json-schema` 의 허용 outcome 첫 값을 `structured_output = {outcome, summary}` 로 낸다. 파일을 만들지 않는다.
"""

import json
import sys
from pathlib import Path

from workflow.scripted import SCRIPT_MODEL_ID
from workflow.scripted._common import (
    apply_fix,
    fixed_result,
    generic_kind_of,
    generic_outcomes,
    generic_result,
    handoff_listing,
    missing_result,
    paced_sleep,
    read_handoff_response,
)

SESSION_ID = "scripted-claude"
USAGE = "usage: claude -p --output-format json … --json-schema <schema>   (프롬프트는 stdin, 작업 위치는 cwd)"


def _schema(argv: list[str]) -> dict:
    """`--json-schema <json>` — 출력 스키마 문자열 그대로. 경로가 아니다."""
    try:
        return json.loads(argv[argv.index("--json-schema") + 1])
    except (ValueError, IndexError):
        return {}


def main(argv: list[str]) -> int:
    if "-p" not in argv:
        print(USAGE, file=sys.stderr)
        return 2
    worktree = Path.cwd()

    prompt = sys.stdin.read()
    paced_sleep(0.4)
    kind = generic_kind_of(prompt)
    if kind is not None:
        paced_sleep(0.6)
        structured = generic_result(kind, generic_outcomes(_schema(argv)), handoff_listing(prompt))
    elif (response := read_handoff_response(prompt)) is None:
        structured = missing_result()
    else:
        paced_sleep(0.6)
        structured = fixed_result(apply_fix(worktree, response))

    envelope = {
        "type": "result", "subtype": "success", "is_error": False,
        "result": structured["summary"], "structured_output": structured,
        "session_id": SESSION_ID, "model": SCRIPT_MODEL_ID,
        "usage": {"input_tokens": 0, "output_tokens": 0},
    }
    print(json.dumps(envelope, ensure_ascii=False), flush=True)
    print(f"scripted claude: {structured['outcome']}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
