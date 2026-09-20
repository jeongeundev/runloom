"""report_expectation — 기대 보고서를 입력 행에서 계산하고 B 의 보고서 텍스트와 비교한다 (PRD "기대 수정 결과와 보고서").

20·5 같은 고정 수치는 여기서만 fixture 로 등장하고, 판정은 항상 입력에서 다시 계산한 값과 비교한다.
"""

import pytest

from workflow.domain.report_expectation import (
    ExpectedReport,
    expected_report,
    report_text_matches,
)

ROWS = [
    {"team": "운영", "completed": 12, "pending": 3},
    {"team": "개발", "completed": 8, "pending": 2},
]
PATHS = ["$.items", "$.data.records"]

PRD_REPORT = """일일 업무 보고서 — 2026-09-19

팀      완료  미완료
운영    12    3
개발    8     2
합계    20    5
"""


def _rows(rows):
    return [dict(r) for r in rows]


# --- expected_report ------------------------------------------------------------------


def test_old_path_response_gives_rows_and_totals():
    expected = expected_report({"report_date": "2026-09-18", "items": _rows(ROWS)}, PATHS)

    assert expected == ExpectedReport(
        report_date="2026-09-18",
        rows=(("운영", 12, 3), ("개발", 8, 2)),
        total_completed=20,
        total_pending=5,
    )


def test_new_path_response_gives_same_rows():
    expected = expected_report(
        {"report_date": "2026-09-19", "data": {"records": _rows(ROWS)}}, PATHS
    )

    assert expected is not None
    assert expected.report_date == "2026-09-19"
    assert expected.rows == (("운영", 12, 3), ("개발", 8, 2))


def test_totals_follow_input_rows_not_fixed_values():
    rows = _rows(ROWS)
    rows[0]["completed"] = 13
    expected = expected_report({"report_date": "2026-09-19", "data": {"records": rows}}, PATHS)

    assert expected is not None
    assert (expected.total_completed, expected.total_pending) == (21, 5)
    assert expected.rows[0] == ("운영", 13, 3)


def test_explicit_empty_list_is_zero_rows():
    expected = expected_report({"report_date": "2026-09-19", "items": []}, PATHS)

    assert expected == ExpectedReport("2026-09-19", (), 0, 0)


@pytest.mark.parametrize(
    "response",
    [
        {"report_date": "2026-09-19"},  # 목록 누락
        {"report_date": "2026-09-19", "items": _rows(ROWS), "data": {"records": _rows(ROWS)}},  # 두 경로
        {"report_date": "2026-09-19", "items": {"team": "운영"}},  # 배열이 아님
        {"report_date": "2026-09-19", "data": {"records": None}},
        {"items": _rows(ROWS)},  # 날짜 누락
        {"report_date": 20260919, "items": _rows(ROWS)},
    ],
)
def test_ambiguous_or_missing_list_is_none(response):
    assert expected_report(response, PATHS) is None


@pytest.mark.parametrize(
    "row",
    [
        {"team": "운영", "completed": 12},  # pending 누락
        {"team": "운영", "completed": "12", "pending": 3},  # 문자열
        {"team": "운영", "completed": True, "pending": 3},  # bool 은 정수가 아니다
        {"team": "운영", "completed": -1, "pending": 3},
        {"team": "", "completed": 1, "pending": 3},
        ["운영", 12, 3],
    ],
)
def test_invalid_row_is_none(row):
    assert expected_report({"report_date": "2026-09-19", "items": [row]}, PATHS) is None


def test_only_supported_paths_are_considered():
    response = {"report_date": "2026-09-19", "rows": _rows(ROWS)}

    assert expected_report(response, PATHS) is None
    assert expected_report(response, ["$.rows"]) is not None


def test_expected_report_round_trips_through_dict():
    expected = expected_report({"report_date": "2026-09-19", "items": _rows(ROWS)}, PATHS)

    data = expected.to_dict()
    assert data == {
        "report_date": "2026-09-19",
        "rows": [
            {"team": "운영", "completed": 12, "pending": 3},
            {"team": "개발", "completed": 8, "pending": 2},
        ],
        "total_completed": 20,
        "total_pending": 5,
    }
    assert ExpectedReport.from_dict(data) == expected


# --- report_text_matches ---------------------------------------------------------------


@pytest.fixture
def expected():
    return expected_report({"report_date": "2026-09-19", "data": {"records": _rows(ROWS)}}, PATHS)


def test_prd_report_matches(expected):
    assert report_text_matches(PRD_REPORT, expected)


def test_whitespace_alignment_is_ignored(expected):
    text = "일일 업무 보고서 — 2026-09-19\n팀 완료 미완료\n운영\t12 3\n  개발 8   2\n합계 20 5"

    assert report_text_matches(text, expected)


def test_report_with_stale_totals_is_rejected():
    rows = _rows(ROWS)
    rows[0]["completed"] = 13
    expected = expected_report({"report_date": "2026-09-19", "data": {"records": rows}}, PATHS)

    # 입력이 13·21 인데 고정 20 보고서를 내면 거부
    assert not report_text_matches(PRD_REPORT, expected)


@pytest.mark.parametrize(
    "text",
    [
        PRD_REPORT.replace("2026-09-19", "2026-09-18"),  # 날짜
        PRD_REPORT.replace("합계    20    5\n", ""),  # 합계 줄 없음
        PRD_REPORT.replace("운영    12    3\n개발    8     2", "개발    8     2\n운영    12    3"),  # 순서
        PRD_REPORT.replace("합계", "운영    12    3\n합계"),  # 행 추가
        PRD_REPORT.replace("개발    8     2\n", ""),  # 행 누락
        "",
    ],
)
def test_mismatching_report_is_rejected(expected, text):
    assert not report_text_matches(text, expected)


def test_empty_rows_report():
    expected = expected_report({"report_date": "2026-09-19", "items": []}, PATHS)
    text = "일일 업무 보고서 — 2026-09-19\n\n팀 완료 미완료\n합계 0 0\n"

    assert report_text_matches(text, expected)
