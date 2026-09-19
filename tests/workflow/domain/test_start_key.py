"""start_key — ARCHITECTURE "DB 제약과 실행 잠금"."""

from workflow.domain.start_key import auto_start_key, request_start_key


def test_auto_start_key_is_deterministic_per_revision():
    assert auto_start_key("fix-daily-0920", 1) == auto_start_key("fix-daily-0920", 1)
    assert auto_start_key("fix-daily-0920", 1) == "auto:fix-daily-0920:r1"


def test_auto_start_key_changes_with_revision_or_task():
    assert auto_start_key("fix-daily-0920", 1) != auto_start_key("fix-daily-0920", 2)
    assert auto_start_key("fix-daily-0920", 1) != auto_start_key("fix-daily-0921", 1)


def test_request_start_key_uses_request_id():
    assert request_start_key("req-abc") == "req:req-abc"
    assert request_start_key("req-abc") == request_start_key("req-abc")


def test_prefixes_differ_between_auto_and_request():
    assert not auto_start_key("x", 1).startswith("req:")
    assert not request_start_key("x").startswith("auto:")
