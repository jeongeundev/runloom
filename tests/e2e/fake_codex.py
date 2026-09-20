#!/usr/bin/env python3
# e2e 전용 가짜 에이전트. 데모·심사에 쓰지 않는다.
"""`codex exec --json -C <worktree> … --output-last-message <file> -` 를 흉내 낸다 (Step 12 테스트의 정상 대본을 파일로).

`scripts/local_stack.py` 가 `fake_codex=` 로 받아 `codex` 이름으로 감싸 connector 의 PATH 앞에 둔다.
하는 일: stdin 프롬프트의 인계 목록에서 `response-after@1.json` 경로를 찾아 읽고, worktree 에
`tests/test_repro_records.py`(변경 응답 재현 + 두 경로 동등성 + 모호 사례)를 추가한 뒤 `daily_report/transformer.py` 를
두 경로 지원으로 고친다. JSONL 3줄을 stdout 에, 마지막 메시지를 파일에 쓴다. 모델·네트워크 호출은 없다.
인계 응답을 찾지 못하면 아무것도 고치지 않고 `needs_information` 으로 끝난다.
"""

import json
import re
import sys
from pathlib import Path

THREAD_ID = "fake-codex-e2e"
RESPONSE_LINE = re.compile(r"^- (?P<path>.+?response-after@1\.json)\s{2}\(")

FIXED_TRANSFORMER = '''"""응답 변환부 — items 또는 data.records 중 정확히 하나를 읽는다 (docs/contract.md)."""

from dataclasses import dataclass


class TransformError(Exception):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code} {detail}".strip())
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class Row:
    team: str
    completed: int
    pending: int


@dataclass(frozen=True)
class Report:
    report_date: str
    rows: tuple[Row, ...]


def _row(item) -> Row:
    if not isinstance(item, dict) or not isinstance(item.get("team"), str):
        raise TransformError("INVALID_ROW", f"item={item!r}")
    completed, pending = item.get("completed"), item.get("pending")
    for value in (completed, pending):
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise TransformError("INVALID_ROW", f"item={item!r}")
    return Row(item["team"], completed, pending)


def _records(response: dict) -> list:
    has_items = "items" in response
    data = response.get("data")
    has_records = isinstance(data, dict) and "records" in data
    if has_items and has_records:
        raise TransformError("AMBIGUOUS_RECORDS_FIELD", "both $.items and $.data.records present")
    records = response.get("items") if has_items else (data or {}).get("records")
    if not isinstance(records, list):
        raise TransformError(
            "MISSING_RECORDS_FIELD",
            f"expected_path=$.items|$.data.records observed_root_keys={sorted(response)}",
        )
    return records


def transform(response: dict) -> Report:
    if not isinstance(response.get("report_date"), str):
        raise TransformError("MISSING_REPORT_DATE")
    return Report(response["report_date"], tuple(_row(item) for item in _records(response)))
'''

REPRO_TEST = '''"""변경 응답(data.records) 재현 — 인계된 response-after 원문을 그대로 입력으로 쓴다."""

import pytest

from daily_report.transformer import TransformError, transform

RESPONSE_AFTER = %(response)s


def test_changed_response_is_transformed_in_order():
    report = transform(RESPONSE_AFTER)
    assert report.report_date == RESPONSE_AFTER["report_date"]
    assert [(r.team, r.completed, r.pending) for r in report.rows] == [
        (item["team"], item["completed"], item["pending"]) for item in RESPONSE_AFTER["data"]["records"]
    ]


def test_items_and_data_records_are_equivalent():
    rows = RESPONSE_AFTER["data"]["records"]
    old = transform({"report_date": RESPONSE_AFTER["report_date"], "items": rows})
    new = transform({"report_date": RESPONSE_AFTER["report_date"], "data": {"records": rows}})
    assert old == new


def test_both_paths_present_is_rejected():
    with pytest.raises(TransformError) as info:
        transform({"report_date": "2026-09-19", "items": [], "data": {"records": []}})
    assert info.value.code == "AMBIGUOUS_RECORDS_FIELD"
'''


def _opt(args: list[str], name: str) -> str:
    return args[args.index(name) + 1]


def _emit(obj: dict) -> None:
    print(json.dumps(obj, ensure_ascii=False), flush=True)


def _find_response(prompt: str) -> Path | None:
    for line in prompt.splitlines():
        match = RESPONSE_LINE.match(line)
        if match:
            return Path(match.group("path"))
    return None


def main(argv: list[str]) -> int:
    worktree = Path(_opt(argv, "-C"))
    last_message = Path(_opt(argv, "--output-last-message"))
    prompt = sys.stdin.read()
    _emit({"type": "thread.started", "thread_id": THREAD_ID})

    response_path = _find_response(prompt)
    if response_path is None or not response_path.is_file():
        _emit({"type": "turn.completed", "note": "response-after@1.json not in handoff listing"})
        last_message.write_text(json.dumps({
            "summary": "인계 목록에서 변경 후 응답(response-after@1.json)을 찾지 못했다",
            "outcome": "needs_information", "files_changed": [], "notes": "response-after 필요",
        }, ensure_ascii=False))
        return 0

    response = json.loads(response_path.read_text(encoding="utf-8"))
    (worktree / "tests" / "test_repro_records.py").write_text(
        REPRO_TEST % {"response": json.dumps(response, ensure_ascii=False, indent=4)}, encoding="utf-8",
    )
    _emit({"type": "item", "text": "재현 테스트 tests/test_repro_records.py 작성 후 daily_report/transformer.py 수정"})
    (worktree / "daily_report" / "transformer.py").write_text(FIXED_TRANSFORMER, encoding="utf-8")
    last_message.write_text(json.dumps({
        "summary": "items 와 data.records 중 정확히 하나를 읽도록 변환부를 고쳤고, 인계 응답으로 재현 테스트를 추가했다",
        "outcome": "ready_for_review",
        "files_changed": ["tests/test_repro_records.py", "daily_report/transformer.py"],
        "notes": "",
    }, ensure_ascii=False))
    _emit({"type": "turn.completed"})
    print("fake codex (e2e): done", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
