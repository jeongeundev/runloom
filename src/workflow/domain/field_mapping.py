"""매핑 표 — 원본 값(GitHub 라벨 등) → Runloom 값(종류·우선순위) (ADR-0020, ARCHITECTURE "매핑 표").

행은 워크스페이스 등록 데이터다(`field_mappings`). 위에서부터(`position` 오름차순, 같으면 받은 순서) 첫 일치.
"""

from collections.abc import Sequence
from dataclasses import dataclass

WILDCARD = "*"  # 나머지 전부 — 값이 없어도 일치
PRIORITIES = ("high", "normal", "low")  # `priority` 행의 Runloom 값 = `work_items.priority` CHECK


@dataclass(frozen=True)
class MappingRow:
    source_type: str  # github | n8n
    field: str  # kind | priority
    source_value: str
    runloom_value: str
    position: int


def map_value(rows: Sequence[MappingRow], source_type: str, field: str, values: Sequence[str]) -> str | None:
    """첫 일치 행의 `runloom_value`. 원본 값 비교는 대소문자 무시. 일치가 없으면 None."""
    wanted = {value.casefold() for value in values}
    for row in sorted((r for r in rows if r.source_type == source_type and r.field == field),
                      key=lambda r: r.position):
        if row.source_value == WILDCARD or row.source_value.casefold() in wanted:
            return row.runloom_value
    return None
