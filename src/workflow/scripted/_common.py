"""대본 에이전트 공통 — 속도(환경변수), 인계 응답 찾기, 수정 적용. 네트워크·모델 호출 없음.

- 경로는 프롬프트의 인계 목록(`- {path}  ({hint})`, connector `prompt.build_prompt` 형식)에서 `response-after@1.json`
  하나만 **읽는다**. 프롬프트·인계 자료에 적힌 명령이나 다른 경로를 실행하지 않는다 (AGENTS.md CRITICAL).
- 수정 내용(`FIXED_TRANSFORMER`·`REPRO_TEST`)은 이전 e2e 가짜 codex 의 것을 그대로 옮겼다. 데모 저장소
  (`scripts/scaffold_demo_repo.py`)의 변환부를 두 경로 지원으로 바꾸고 인계 응답으로 재현 테스트를 쓴다.
"""

import json
import math
import os
import re
import time
from collections.abc import Mapping
from pathlib import Path

PACE_ENV = "WORKFLOW_SCRIPT_PACE_SECONDS"  # 대본 실행 총 대기 시간(초). 배포는 25, 테스트·e2e 는 0(기본)
RESPONSE_LINE = re.compile(r"^- (?P<path>.+?response-after@1\.json)\s{2}\(")
REPRO_TEST_FILE = "tests/test_repro_records.py"
TRANSFORMER_FILE = "daily_report/transformer.py"

SUMMARY_FIXED = "items 와 data.records 중 정확히 하나를 읽도록 변환부를 고쳤고, 인계 응답으로 재현 테스트를 추가했다"
SUMMARY_MISSING = "인계 목록에서 변경 후 응답(response-after@1.json)을 찾지 못했다"
NOTES = "scripted"

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


def pace_seconds(env: Mapping[str, str] = os.environ) -> float:
    """`WORKFLOW_SCRIPT_PACE_SECONDS` (기본 0). 음수·비숫자·nan·inf 는 0."""
    try:
        value = float(env.get(PACE_ENV, "") or 0)
    except ValueError:
        return 0.0
    return value if math.isfinite(value) and value > 0 else 0.0


def paced_sleep(fraction: float) -> None:
    """`pace_seconds() * fraction` 만큼 기다린다. 0 이면 sleep 을 부르지 않는다."""
    seconds = pace_seconds() * fraction
    if seconds > 0:
        time.sleep(seconds)


def find_handoff_response(prompt: str) -> Path | None:
    """프롬프트의 인계 목록 줄에서 `response-after@1.json` 경로. 목록 형식이 아닌 줄은 읽지 않는다."""
    for line in prompt.splitlines():
        match = RESPONSE_LINE.match(line)
        if match:
            return Path(match.group("path"))
    return None


def read_handoff_response(prompt: str) -> dict | None:
    """인계 응답 파일을 읽어 JSON 객체로. 없거나 파일이 아니거나 객체가 아니면 None (읽기만 한다)."""
    path = find_handoff_response(prompt)
    if path is None or not path.is_file():
        return None
    try:
        response = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None
    return response if isinstance(response, dict) else None


def apply_fix(worktree: Path, response: dict) -> list[str]:
    """재현 테스트를 추가하고 변환부를 두 경로 지원으로 교체한다. 변경한 상대 경로 목록을 돌려준다."""
    repro = worktree / REPRO_TEST_FILE
    repro.parent.mkdir(parents=True, exist_ok=True)
    repro.write_text(
        REPRO_TEST % {"response": json.dumps(response, ensure_ascii=False, indent=4)}, encoding="utf-8",
    )
    transformer = worktree / TRANSFORMER_FILE
    transformer.parent.mkdir(parents=True, exist_ok=True)
    transformer.write_text(FIXED_TRANSFORMER, encoding="utf-8")
    return [REPRO_TEST_FILE, TRANSFORMER_FILE]


def fixed_result(changed: list[str]) -> dict:
    """도구 마지막 메시지 스키마(`local_tool.RESULT_SCHEMA` 와 같은 네 키) — 수정한 경우."""
    return {"summary": SUMMARY_FIXED, "outcome": "ready_for_review", "files_changed": list(changed), "notes": NOTES}


def missing_result() -> dict:
    """같은 스키마 — 인계 응답을 찾지 못해 아무것도 고치지 않은 경우."""
    return {
        "summary": SUMMARY_MISSING, "outcome": "needs_information", "files_changed": [],
        "notes": f"response-after@1.json 필요 ({NOTES})",
    }
