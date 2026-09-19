"""근거 위치 문법 — ARCHITECTURE "진단 결과와 근거" 의 location 규칙.

JSON 자료는 `$.a.b` 객체 경로, 텍스트 자료는 `lines:N-M` (1부터, 양끝 포함).
와일드카드·배열 인덱스·필터는 v1 에서 지원하지 않는다.
"""

import json

import pytest

from workflow.domain.evidence_location import (
    LineRange,
    ObjectPath,
    Resolved,
    parse_location,
    resolve_location,
)

RESPONSE_AFTER = json.dumps(
    {
        "report_date": "2026-09-19",
        "data": {"records": [{"team": "운영", "completed": 12, "pending": 3}]},
        "note": None,
    }
).encode()

LOG = (
    "2026-09-20T09:00:00+09:00 INFO  stage=fetch http_status=200\n"
    "2026-09-20T09:00:01+09:00 ERROR stage=transform code=MISSING_RECORDS_FIELD\n"
    "2026-09-20T09:00:01+09:00 INFO  stage=render status=skipped\n"
    "2026-09-20T09:00:01+09:00 ERROR event=run_finished status=failed\n"
).encode()


# --- parse_location ---------------------------------------------------------


def test_parse_object_path():
    assert parse_location("$.data.records") == ObjectPath(keys=("data", "records"))
    assert parse_location("$.items") == ObjectPath(keys=("items",))


def test_parse_line_range():
    assert parse_location("lines:2-3") == LineRange(start=2, end=3)
    assert parse_location("lines:4-4") == LineRange(start=4, end=4)


@pytest.mark.parametrize(
    "location",
    [
        "",
        "data.records",
        "$",
        "$.",
        "$.items[0]",
        "$.items.*",
        "$.data..records",
        "lines:3-2",
        "lines:0-1",
        "lines:2",
        "lines:a-b",
        "line:1-2",
    ],
)
def test_parse_rejects_syntax_errors(location):
    with pytest.raises(ValueError):
        parse_location(location)


# --- resolve_location: JSON --------------------------------------------------


def test_json_path_found_returns_value():
    resolved = resolve_location(RESPONSE_AFTER, "application/json", "$.data.records")

    assert resolved == Resolved(value=[{"team": "운영", "completed": 12, "pending": 3}], found=True)


def test_json_top_level_key():
    assert resolve_location(RESPONSE_AFTER, "application/json", "$.report_date") == Resolved(
        value="2026-09-19"
    )


def test_json_missing_key_is_none():
    assert resolve_location(RESPONSE_AFTER, "application/json", "$.items") is None
    assert resolve_location(RESPONSE_AFTER, "application/json", "$.data.record") is None


def test_json_null_value_is_found():
    assert resolve_location(RESPONSE_AFTER, "application/json", "$.note") == Resolved(
        value=None, found=True
    )


def test_json_path_through_non_object_is_none():
    # records 는 배열이므로 그 아래 키를 따라갈 수 없다 (인덱스 미지원)
    assert resolve_location(RESPONSE_AFTER, "application/json", "$.data.records.team") is None
    # report_date 는 문자열
    assert resolve_location(RESPONSE_AFTER, "application/json", "$.report_date.x") is None


def test_json_invalid_document_is_none():
    assert resolve_location(b"{not json", "application/json", "$.items") is None


def test_json_root_must_be_object():
    assert resolve_location(b"[1, 2]", "application/json", "$.items") is None


# --- resolve_location: text --------------------------------------------------


def test_text_line_range_inside():
    assert resolve_location(LOG, "text/plain", "lines:1-2") == Resolved(
        value=[
            "2026-09-20T09:00:00+09:00 INFO  stage=fetch http_status=200",
            "2026-09-20T09:00:01+09:00 ERROR stage=transform code=MISSING_RECORDS_FIELD",
        ]
    )


def test_text_last_line():
    assert resolve_location(LOG, "text/plain", "lines:4-4") == Resolved(
        value=["2026-09-20T09:00:01+09:00 ERROR event=run_finished status=failed"]
    )


def test_text_trailing_newline_is_not_a_line():
    assert resolve_location(LOG, "text/plain", "lines:5-5") is None


def test_text_range_beyond_end_is_none():
    assert resolve_location(LOG, "text/plain", "lines:3-9") is None


def test_text_without_trailing_newline():
    assert resolve_location(b"a\nb", "text/plain", "lines:2-2") == Resolved(value=["b"])
    assert resolve_location(b"a\nb", "text/plain", "lines:3-3") is None


def test_text_invalid_utf8_is_none():
    assert resolve_location(b"\xff\xfe", "text/plain", "lines:1-1") is None


# --- resolve_location: 자료형·문법 --------------------------------------------


def test_type_mismatch_is_none():
    assert resolve_location(RESPONSE_AFTER, "application/json", "lines:1-1") is None
    assert resolve_location(LOG, "text/plain", "$.items") is None


def test_unknown_content_type_is_none():
    assert resolve_location(LOG, "application/octet-stream", "lines:1-1") is None


def test_content_type_parameters_are_ignored():
    assert resolve_location(LOG, "text/plain; charset=utf-8", "lines:1-1") is not None
    assert resolve_location(RESPONSE_AFTER, "application/json; charset=utf-8", "$.data") is not None


def test_syntax_error_raises_even_in_resolve():
    with pytest.raises(ValueError):
        resolve_location(RESPONSE_AFTER, "application/json", "$.items[0]")
