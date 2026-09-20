"""근거 위치 문법 — ARCHITECTURE "진단 결과와 근거" 의 location 규칙.

JSON 자료는 `$.a.b[0].c` 객체 경로(배열 인덱스 `[N]` 은 0부터), 텍스트 자료는 `lines:N-M`
(1부터, 양끝 포함). 와일드카드·필터·음수 인덱스는 v1 에서 지원하지 않는다.
문법은 계약 v1 의 `Location` 과 같아야 한다 — `test_grammar_matches_contract`.
"""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from workflow.contracts.v1 import EvidenceRef
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

# 실행 기록 fixture — `stages` 가 배열이라 배열 인덱스 인용의 실제 사례다
RUN_RECORD_0920 = (
    Path(__file__).resolve().parents[3]
    / "src" / "diagnostic_demo" / "fixtures" / "evidence" / "run-daily-0920-0900" / "1.json"
)

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


def test_parse_object_path_with_array_index():
    assert parse_location("$.stages[1].status") == ObjectPath(keys=("stages", 1, "status"))
    assert parse_location("$.items[0]") == ObjectPath(keys=("items", 0))
    assert parse_location("$.a[0][1].b") == ObjectPath(keys=("a", 0, 1, "b"))


def test_parse_line_range():
    assert parse_location("lines:2-3") == LineRange(start=2, end=3)
    assert parse_location("lines:4-4") == LineRange(start=4, end=4)


BAD_LOCATIONS = [
    "",
    "data.records",
    "$",
    "$.",
    "$[0]",
    "$.items[*]",
    "$.items.*",
    "$.a[01]",
    "$.a[-1]",
    "$.a[]",
    "$.a[1",
    "$.keys()",
    "$.data..records",
    "lines:3-2",
    "lines:0-1",
    "lines:2",
    "lines:a-b",
    "line:1-2",
]
GOOD_LOCATIONS = [
    "$.items",
    "$.data.records",
    "$.stages[1].status",
    "$.items[0]",
    "$.data.records[0].team",
    "lines:2-3",
    "lines:4-4",
]


@pytest.mark.parametrize("location", BAD_LOCATIONS)
def test_parse_rejects_syntax_errors(location):
    with pytest.raises(ValueError):
        parse_location(location)


@pytest.mark.parametrize("location", GOOD_LOCATIONS + BAD_LOCATIONS)
def test_grammar_matches_contract(location):
    """도메인 해석기와 계약 v1 `Location` 은 같은 문자열을 받고 같은 문자열을 거부한다."""
    try:
        EvidenceRef.model_validate({"evidence_id": "e", "version": "1", "location": location})
        contract_accepts = True
    except ValidationError:
        contract_accepts = False
    try:
        parse_location(location)
        domain_accepts = True
    except ValueError:
        domain_accepts = False

    assert contract_accepts == domain_accepts


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
    # records 는 배열이므로 문자열 키로는 따라갈 수 없다 — 인덱스가 필요하다
    assert resolve_location(RESPONSE_AFTER, "application/json", "$.data.records.team") is None
    # report_date 는 문자열
    assert resolve_location(RESPONSE_AFTER, "application/json", "$.report_date.x") is None


def test_json_array_index_resolves_in_run_record_fixture():
    content = RUN_RECORD_0920.read_bytes()

    assert resolve_location(content, "application/json", "$.stages[1].error_code") == Resolved(
        value="MISSING_RECORDS_FIELD"
    )
    assert resolve_location(content, "application/json", "$.stages[1].status") == Resolved(
        value="failed"
    )
    assert resolve_location(content, "application/json", "$.stages[0]") == Resolved(
        value={"stage": "fetch", "status": "succeeded"}
    )


def test_json_array_index_out_of_range_is_none():
    content = RUN_RECORD_0920.read_bytes()  # stages 는 3개

    assert resolve_location(content, "application/json", "$.stages[3]") is None
    assert resolve_location(content, "application/json", "$.stages[3].status") is None


def test_json_index_on_object_is_none():
    content = RUN_RECORD_0920.read_bytes()  # response_ref 는 객체

    assert resolve_location(content, "application/json", "$.response_ref[0]") is None


def test_json_key_on_array_is_none():
    content = RUN_RECORD_0920.read_bytes()  # stages 는 배열

    assert resolve_location(content, "application/json", "$.stages.stage") is None


def test_json_index_then_key_in_response():
    assert resolve_location(RESPONSE_AFTER, "application/json", "$.data.records[0].team") == Resolved(
        value="운영"
    )
    assert resolve_location(RESPONSE_AFTER, "application/json", "$.data.records[1]") is None


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
        resolve_location(RESPONSE_AFTER, "application/json", "$.items[*]")
