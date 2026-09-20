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


# --- Step 7 ---------------------------------------------------------------------

from workflow.server.filters import ago, duration, kind_label, outcome_label  # noqa: E402


@pytest.mark.parametrize(
    ("outcome", "label"),
    [
        ("ready_for_handoff", "인계 가능"),
        ("ready_for_review", "검토 가능"),
        ("needs_information", "정보 필요"),
        ("something_else", "something_else"),
        (None, ""),
    ],
)
def test_outcome_label(outcome, label):
    assert outcome_label(outcome) == label


@pytest.mark.parametrize(
    ("value", "now", "expected"),
    [
        ("2026-09-20T01:12:03Z", "2026-09-20T01:12:33Z", "방금 전"),
        ("2026-09-20T01:00:03Z", "2026-09-20T01:12:03Z", "12분 전"),
        ("2026-09-19T23:12:03Z", "2026-09-20T01:12:03Z", "2시간 전"),
        ("2026-09-17T01:12:03Z", "2026-09-20T01:12:03Z", "3일 전"),
        ("2026-09-20T02:00:00Z", "2026-09-20T01:12:03Z", "방금 전"),  # 시계 차이는 미래로 보이지 않는다
        (None, "2026-09-20T01:12:03Z", ""),
    ],
)
def test_ago(value, now, expected):
    assert ago(value, now) == expected


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [(0, "0초"), (45, "45초"), (60, "1분 0초"), (253, "4분 13초"), (3725, "1시간 2분"), (None, "")],
)
def test_duration(seconds, expected):
    assert duration(seconds) == expected


def test_kind_label_maps_artifact_kinds_and_passes_unknown_through():
    assert kind_label("diff") == "diff"
    assert kind_label("test_log_before") == "테스트 전"
    assert kind_label("test_log_after") == "테스트 후"
    assert kind_label("report_output") == "보고서"
    assert kind_label("diagnosis_result") == "진단 결과"
    assert kind_label("code_change_result") == "수정 결과"
    assert kind_label("handoff_bundle") == "인계 묶음"
    assert kind_label("codex_jsonl") == "Codex JSONL"
    assert kind_label("codex_stderr") == "Codex stderr"
    assert kind_label("claude_jsonl") == "Claude JSONL"
    assert kind_label("claude_stderr") == "Claude stderr"
    assert kind_label("new_kind") == "new_kind"
