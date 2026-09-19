"""filters.py — 템플릿 필터. 시각은 UTC 저장·Asia/Seoul 표시 (UI_GUIDE 타이포그래피 절)."""

import pytest

from workflow.server.filters import kst


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2026-09-20T01:12:03Z", "2026-09-20 10:12:03 KST"),
        ("2026-09-20T01:12:03.123456Z", "2026-09-20 10:12:03 KST"),
        ("2026-09-20T10:12:03+09:00", "2026-09-20 10:12:03 KST"),
        ("2026-09-19T23:30:00-05:00", "2026-09-20 13:30:00 KST"),
    ],
)
def test_kst_formats_any_timezone_as_seoul(value, expected):
    assert kst(value) == expected


def test_kst_empty_values_render_empty():
    assert kst(None) == ""
    assert kst("") == ""
