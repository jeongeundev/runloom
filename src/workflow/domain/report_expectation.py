"""기대 보고서 — PRD "기대 수정 결과와 보고서". 판정 수치는 입력 행에서 계산한다.

B 결과의 `report_output` 을 "정답 텍스트" 와 비교하지 않는다. 실패 응답(`response-after`)과 보고서 계약의
`supported_paths` 로 기대 행·합계를 계산해 두고(인계 묶음의 `expected_report.json`), B 가 낸 보고서에서
날짜·팀별 값·합계를 파싱해 비교한다. 20·5 같은 고정 수치는 어디에도 없다. I/O 를 하지 않는다.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from workflow.domain.evidence_location import ObjectPath, parse_location

_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_TOTAL_LABEL = "합계"


@dataclass(frozen=True)
class ExpectedReport:
    report_date: str
    rows: tuple[tuple[str, int, int], ...]  # (team, completed, pending) 입력 순서 그대로
    total_completed: int
    total_pending: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "report_date": self.report_date,
            "rows": [{"team": t, "completed": c, "pending": p} for t, c, p in self.rows],
            "total_completed": self.total_completed,
            "total_pending": self.total_pending,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ExpectedReport":
        return cls(
            report_date=data["report_date"],
            rows=tuple((r["team"], r["completed"], r["pending"]) for r in data["rows"]),
            total_completed=data["total_completed"],
            total_pending=data["total_pending"],
        )


def _count(value: Any) -> int | None:
    # bool 은 int 의 하위 타입이지만 건수가 아니다
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _row(item: Any) -> tuple[str, int, int] | None:
    if not isinstance(item, dict):
        return None
    team, completed, pending = item.get("team"), _count(item.get("completed")), _count(item.get("pending"))
    if not isinstance(team, str) or not team or completed is None or pending is None:
        return None
    return team, completed, pending


def _value_at(response: dict[str, Any], path: str) -> tuple[bool, Any]:
    """(경로가 있는지, 값). 줄 범위 같은 비객체 경로는 응답 JSON 에 적용할 수 없으므로 없는 것으로 본다."""
    parsed = parse_location(path)
    if not isinstance(parsed, ObjectPath):
        return False, None
    current: Any = response
    for key in parsed.keys:
        if not isinstance(current, dict) or key not in current:
            return False, None
        current = current[key]
    return True, current


def expected_report(response: dict[str, Any], supported_paths: Sequence[str]) -> ExpectedReport | None:
    """`supported_paths` 중 정확히 하나에 목록(list)이 있어야 한다. 0개·2개 이상, 배열이 아닌 값, 깨진 행,
    날짜 누락은 None (PRD 계약: 목록 누락·모호한 응답은 오류, 명시적 빈 배열만 0건)."""
    present = [value for found, value in (_value_at(response, p) for p in supported_paths) if found]
    if len(present) != 1 or not isinstance(present[0], list):
        return None
    report_date = response.get("report_date")
    if not isinstance(report_date, str) or not report_date:
        return None
    rows = []
    for item in present[0]:
        row = _row(item)
        if row is None:
            return None
        rows.append(row)
    return ExpectedReport(
        report_date=report_date,
        rows=tuple(rows),
        total_completed=sum(r[1] for r in rows),
        total_pending=sum(r[2] for r in rows),
    )


def _parse_report(text: str) -> tuple[str | None, list[tuple[str, int, int]], tuple[int, int] | None]:
    """(첫 날짜, 팀 행들, 합계). 끝 두 토큰이 정수인 줄을 행으로 본다. 공백 정렬은 무시한다."""
    date = None
    rows: list[tuple[str, int, int]] = []
    total = None
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if date is None:
            match = _DATE.search(line)
            if match:
                date = match.group(0)
        tokens = line.split()
        if len(tokens) < 3:
            continue
        try:
            completed, pending = int(tokens[-2]), int(tokens[-1])
        except ValueError:
            continue
        name = " ".join(tokens[:-2])
        if name == _TOTAL_LABEL:
            total = (completed, pending)
        else:
            rows.append((name, completed, pending))
    return date, rows, total


def report_text_matches(report_output: str, expected: ExpectedReport) -> bool:
    """날짜·팀별 값(순서 포함)·합계 줄이 모두 기대와 같아야 한다."""
    date, rows, total = _parse_report(report_output)
    return (
        date == expected.report_date
        and tuple(rows) == expected.rows
        and total == (expected.total_completed, expected.total_pending)
    )
