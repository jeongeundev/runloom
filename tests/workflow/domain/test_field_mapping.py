"""field_mapping — 원본 값 → Runloom 값 매핑 표 (phase 14 step 5, ARCHITECTURE "매핑 표")."""

from workflow.domain.field_mapping import WILDCARD, MappingRow, map_value


def row(value: str, result: str, position: int, *, source_type="github", field="kind") -> MappingRow:
    return MappingRow(source_type=source_type, field=field, source_value=value, runloom_value=result,
                      position=position)


def test_first_match_by_position_wins():
    rows = [row(WILDCARD, "bug_fix", 3), row("docs", "write_docs", 2), row("Refactor", "refactor", 1)]
    assert map_value(rows, "github", "kind", ["docs", "refactor"]) == "refactor"  # 대소문자 무시
    assert map_value(rows, "github", "kind", ["docs"]) == "write_docs"
    assert map_value(rows, "github", "kind", ["ui"]) == "bug_fix"


def test_wildcard_matches_even_without_values():
    assert map_value([row(WILDCARD, "bug_fix", 1)], "github", "kind", []) == "bug_fix"


def test_no_match_is_none():
    rows = [row("docs", "write_docs", 1)]
    assert map_value(rows, "github", "kind", ["bug"]) is None
    assert map_value([], "github", "kind", ["bug"]) is None


def test_other_source_type_or_field_is_ignored():
    rows = [row(WILDCARD, "bug_fix", 1, source_type="n8n"), row("p1", "high", 1, field="priority")]
    assert map_value(rows, "github", "kind", ["p1"]) is None
    assert map_value(rows, "github", "priority", ["P1"]) == "high"


def test_equal_positions_keep_given_order():
    rows = [row("bug", "first", 1), row("bug", "second", 1)]
    assert map_value(rows, "github", "kind", ["bug"]) == "first"
