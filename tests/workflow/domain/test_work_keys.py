"""업무 키 주소 — phase 16 step 5 (ARCHITECTURE "업무 화면 — phase 16" 이름 표 `domain/work_keys.py`)."""

import pytest

from workflow.domain.work_keys import BRANCH_SUMMARY_MAX, branch_name, keys_in, work_path


def test_work_path_opens_the_panel_on_the_work_screen():
    assert work_path("RUN-12") == "/tasks?open=RUN-12"


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        pytest.param("URL filter 가 안 먹음", "RUN-15-url-filter", id="mixed"),
        pytest.param("쿠폰 오류", "RUN-15", id="korean-only-key-only"),
        pytest.param("", "RUN-15", id="empty"),
        pytest.param("  Fix: login/logout!!  (v2) ", "RUN-15-fix-login-logout-v2", id="special-chars"),
        pytest.param("Crème brûlée_bug", "RUN-15-cr-me-br-l-e-bug", id="non-ascii-letters"),
        pytest.param("---", "RUN-15", id="only-dashes"),
    ],
)
def test_branch_name(title, expected):
    assert branch_name("RUN-15", title) == expected


def test_branch_name_summary_is_cut_at_40_and_trailing_dash_removed():
    assert BRANCH_SUMMARY_MAX == 40
    title = "a" * 39 + " bcd"  # 40번째 글자가 '-' 가 되는 제목
    assert branch_name("RUN-7", title) == "RUN-7-" + "a" * 39
    long = branch_name("RUN-7", "word " * 30)
    summary = long.removeprefix("RUN-7-")
    assert len(summary) <= 40 and not summary.endswith("-") and summary.startswith("word-word")


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("RUN-15-url-filter", (15,)),
        ("fix: RUN-3 와 run-7 정리", (3, 7)),
        ("RUN-4 RUN-2 RUN-4", (4, 2)),
        ("feature/RUN-12_x", (12,)),
        ("XRUN-5", ()),
        ("run5", ()),
        ("RUN-0", ()),
        ("RUN-05", ()),
        ("RUN-1234567890", ()),
        ("RUN-123456789", (123456789,)),
        ("RUN-12a", (12,)),
        ("", ()),
        ("쿠폰 오류", ()),
    ],
)
def test_keys_in(text, expected):
    assert keys_in(text) == expected
