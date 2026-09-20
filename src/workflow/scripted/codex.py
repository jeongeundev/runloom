"""대본 Codex — `codex exec --json -C <worktree> … --output-last-message <file> -` 인자 형식을 그대로 받는다.

connector `CodexAdapter.build_argv` 는 바뀌지 않는다. 배포·로컬 스택이 이 모듈을 `codex` 이름의 래퍼로 PATH 앞에 둔다
(`scripts/local_stack.py --scripted`). 흐름: 프롬프트(stdin) → 대기 → 인계 응답 찾기 → 대기 → 수정 적용 →
JSONL 3줄(stdout) + 마지막 메시지 파일. 인계 응답을 못 찾으면 고치지 않고 `needs_information`.
인자에서 받는 경로는 `-C`(작업 디렉터리, 없으면 cwd)와 `--output-last-message`(쓰기) 뿐이다. 모델·네트워크 호출 없음.
"""

import json
import sys
from pathlib import Path

from workflow.scripted import SCRIPT_MODEL_ID
from workflow.scripted._common import apply_fix, fixed_result, missing_result, paced_sleep, read_handoff_response

THREAD_ID = "scripted-codex"
USAGE = "usage: codex exec --json -C <worktree> … --output-last-message <file> -   (프롬프트는 stdin)"


def _opt(argv: list[str], name: str) -> str | None:
    try:
        return argv[argv.index(name) + 1]
    except (ValueError, IndexError):
        return None


def _emit(obj: dict) -> None:
    print(json.dumps(obj, ensure_ascii=False), flush=True)


def main(argv: list[str]) -> int:
    last_message_opt = _opt(argv, "--output-last-message")
    if last_message_opt is None:
        print(USAGE, file=sys.stderr)
        return 2
    last_message = Path(last_message_opt)
    worktree = Path(_opt(argv, "-C") or Path.cwd())

    prompt = sys.stdin.read()
    _emit({"type": "thread.started", "thread_id": THREAD_ID, "model": SCRIPT_MODEL_ID})
    paced_sleep(0.4)
    response = read_handoff_response(prompt)
    if response is None:
        _emit({"type": "item", "text": "인계 목록에 response-after@1.json 이 없어 수정하지 않음"})
        last_message.write_text(json.dumps(missing_result(), ensure_ascii=False), encoding="utf-8")
        _emit({"type": "turn.completed", "note": "response-after@1.json not in handoff listing"})
        print("scripted codex: needs_information", file=sys.stderr)
        return 0

    paced_sleep(0.6)
    changed = apply_fix(worktree, response)
    _emit({"type": "item", "text": f"재현 테스트 {changed[0]} 작성 후 {changed[1]} 수정"})
    last_message.write_text(json.dumps(fixed_result(changed), ensure_ascii=False), encoding="utf-8")
    _emit({"type": "turn.completed"})
    print("scripted codex: done", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
