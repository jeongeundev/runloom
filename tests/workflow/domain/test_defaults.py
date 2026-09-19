"""업무 등록 기본값 — PRD 2절 "기본값 — 2026-09-20 사용자 확정"."""

import pytest

from workflow.domain.defaults import (
    default_completion_mode,
    default_run_mode,
    default_selection_mode,
    kind_for_capability,
)


def test_run_mode_is_auto_only_with_predecessor():
    assert default_run_mode(has_predecessor=True) == "auto"
    assert default_run_mode(has_predecessor=False) == "manual"


def test_selection_mode_defaults_to_auto():
    assert default_selection_mode() == "auto"


def test_completion_mode_is_review_for_every_kind():
    assert default_completion_mode("diagnosis") == "review"
    assert default_completion_mode("code_change") == "review"


def test_kind_for_capability():
    assert kind_for_capability("operations.diagnose") == "diagnosis"
    assert kind_for_capability("code.modify") == "code_change"


def test_kind_for_unknown_capability_raises():
    with pytest.raises(KeyError):
        kind_for_capability("ops.unknown")
