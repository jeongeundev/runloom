"""근거 위치(location) 문법과 첨부 원문에서의 해석 — ARCHITECTURE "진단 결과와 근거".

JSON 자료(`application/json`)는 `$.a.b[0].c` 객체 경로(문자열 키와 0부터 시작하는 배열 인덱스 `[N]`),
텍스트 자료(`text/plain`)는 `lines:N-M` 줄 범위(1부터, 양끝 포함)만 지원한다.
와일드카드·필터·음수 인덱스는 v1 에 없다 — 위치는 원문의 값 하나를 결정적으로 가리켜야 한다.
문법은 `contracts/v1.LOCATION_PATTERN` 하나를 그대로 쓴다. 여기서는 첨부 바이트만 보고 I/O 를 하지 않는다.
"""

import json
import re
from dataclasses import dataclass
from typing import Any

from workflow.contracts.v1 import LOCATION_PATTERN

_LOCATION = re.compile(LOCATION_PATTERN)
# 문법 검사를 통과한 객체 경로를 토큰으로 자른다: `.key` 또는 `[index]`
_SEGMENT = re.compile(r"\.([A-Za-z_][A-Za-z0-9_]*)|\[([0-9]+)\]")


@dataclass(frozen=True)
class ObjectPath:
    keys: tuple[str | int, ...]  # "$.stages[1].status" → ("stages", 1, "status")


@dataclass(frozen=True)
class LineRange:
    start: int  # 1-based
    end: int  # 양끝 포함


@dataclass(frozen=True)
class Resolved:
    """위치가 원문에 실제로 존재함. JSON 의 `null` 값도 키가 있으면 `Resolved(None)` 이다."""

    value: Any
    found: bool = True


def parse_location(location: str) -> ObjectPath | LineRange:
    """location 문자열을 구조로 바꾼다. 문법 오류는 ValueError."""
    if not _LOCATION.match(location):
        raise ValueError(f"location 문법 오류: {location!r} — `$.a.b[0]` 또는 `lines:N-M` (M ≥ N)")
    if location.startswith("lines:"):
        start, end = (int(n) for n in location[len("lines:") :].split("-"))
        if start > end:
            raise ValueError(f"location 문법 오류: {location!r} — lines:N-M 은 M ≥ N")
        return LineRange(start=start, end=end)
    return ObjectPath(
        keys=tuple(key if key else int(index) for key, index in _SEGMENT.findall(location))
    )


def resolve_location(content: bytes, content_type: str, location: str) -> Resolved | None:
    """첨부 원문에서 location 이 가리키는 값·줄을 찾는다.

    없거나 자료형이 맞지 않으면 None. 문법 오류는 `parse_location` 의 ValueError 그대로.
    """
    parsed = parse_location(location)
    media_type = content_type.split(";")[0].strip()

    if media_type == "application/json" and isinstance(parsed, ObjectPath):
        return _resolve_object_path(content, parsed)
    if media_type == "text/plain" and isinstance(parsed, LineRange):
        return _resolve_line_range(content, parsed)
    return None


def _resolve_object_path(content: bytes, path: ObjectPath) -> Resolved | None:
    try:
        current = json.loads(content)
    except (UnicodeDecodeError, ValueError):
        return None
    for key in path.keys:
        if isinstance(key, int):
            if not isinstance(current, list) or key >= len(current):
                return None
        elif not isinstance(current, dict) or key not in current:
            return None
        current = current[key]
    return Resolved(value=current)


def _resolve_line_range(content: bytes, line_range: LineRange) -> Resolved | None:
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        return None
    lines = text.split("\n")
    if text.endswith("\n"):
        lines.pop()  # 마지막 개행 뒤의 빈 조각은 줄이 아니다
    if line_range.end > len(lines):
        return None
    return Resolved(value=lines[line_range.start - 1 : line_range.end])
