"""errors.py — 저장 계층 예외의 계층과 HTTP 변환(Step 5)에 필요한 속성."""

import pytest

from workflow.adapters.errors import (
    ActiveExecutionExists,
    AdapterError,
    ArtifactMissing,
    DuplicateStartKey,
    EmailTaken,
    EventConflict,
    Forbidden,
    HashMismatch,
    InvalidTransition,
    LastAdmin,
    NextStepExists,
    NextStepHandled,
    NotFound,
    ResponseConflict,
    SequenceGap,
    StaleRequest,
    TriageRunning,
)


@pytest.mark.parametrize(
    "exc_type",
    [ActiveExecutionExists, DuplicateStartKey, NotFound, Forbidden, HashMismatch, ArtifactMissing, EmailTaken,
     LastAdmin],
)
def test_simple_errors_inherit_adapter_error(exc_type):
    exc = exc_type("detail")
    assert isinstance(exc, AdapterError)
    assert str(exc) == "detail"


def test_sequence_gap_carries_expected_seq():
    exc = SequenceGap(4)
    assert isinstance(exc, AdapterError)
    assert exc.expected_seq == 4
    assert "4" in str(exc)


def test_event_conflict_carries_seq():
    exc = EventConflict(3)
    assert isinstance(exc, AdapterError)
    assert exc.seq == 3
    assert "3" in str(exc)


def test_invalid_transition_carries_current_status_event_type_and_optional_reason():
    exc = InvalidTransition("result_ready")
    assert isinstance(exc, AdapterError)
    assert (exc.current_status, exc.event_type, exc.reason) == ("result_ready", None, None)
    exc = InvalidTransition("result_ready", event_type="started")
    assert (exc.current_status, exc.event_type, exc.reason) == ("result_ready", "started", None)
    exc = InvalidTransition("running", event_type="result_ready", reason="result_artifact_missing")
    assert (exc.current_status, exc.event_type, exc.reason) == (
        "running", "result_ready", "result_artifact_missing")


def test_stale_request_carries_current_revision():
    exc = StaleRequest("hr-3c2b1a0f", 3)
    assert isinstance(exc, AdapterError)
    assert exc.current_revision == 3
    assert "hr-3c2b1a0f" in str(exc) and "3" in str(exc)


def test_response_conflict_is_adapter_error():
    assert isinstance(ResponseConflict("resp-1"), AdapterError)


def test_triage_running_is_adapter_error():
    assert isinstance(TriageRunning("work-1"), AdapterError)


def test_next_step_errors_carry_their_keys():
    exists = NextStepExists("after_result:exec-1")
    handled = NextStepHandled("trg-1")
    assert isinstance(exists, AdapterError) and isinstance(handled, AdapterError)
    assert exists.cause_key == "after_result:exec-1" and "after_result:exec-1" in str(exists)
    assert handled.triage_id == "trg-1" and "trg-1" in str(handled)
