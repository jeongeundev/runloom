"""Execution 상태 전이(ARCHITECTURE "실행 이벤트")와 사용자 상태 대응표(PRD 3절)."""

import dataclasses

import pytest

from workflow.domain.status import (
    EXECUTION_STATUSES,
    TERMINAL_STATUSES,
    USER_STATUS_LABELS,
    InvalidTransition,
    TaskView,
    UserStatus,
    next_execution_status,
    user_status,
)

# --- 상태 전이 ---------------------------------------------------------------


def test_status_constants_match_glossary():
    assert EXECUTION_STATUSES == ("queued", "accepted", "running", "result_ready", "failed", "unknown")
    assert TERMINAL_STATUSES == ("result_ready", "failed")
    assert USER_STATUS_LABELS == ("대기", "실행 가능", "실행 요청됨", "실행 중", "확인 필요", "완료", "실패")


@pytest.mark.parametrize(
    ("current", "event_type", "expected"),
    [
        ("queued", "accepted", "accepted"),
        ("accepted", "started", "running"),
        ("running", "progress", "running"),
        ("running", "result_ready", "result_ready"),
        ("accepted", "failed", "failed"),
        ("running", "failed", "failed"),
    ],
)
def test_normal_transitions(current, event_type, expected):
    assert next_execution_status(current, event_type) == expected


@pytest.mark.parametrize("event_type", ["started", "result_ready", "failed"])
def test_unknown_restores_from_resent_events(event_type):
    expected = {"started": "running", "result_ready": "result_ready", "failed": "failed"}[event_type]
    assert next_execution_status("unknown", event_type) == expected


@pytest.mark.parametrize("terminal", TERMINAL_STATUSES)
@pytest.mark.parametrize("event_type", ["accepted", "started", "progress", "result_ready", "failed"])
def test_terminal_status_rejects_every_event(terminal, event_type):
    with pytest.raises(InvalidTransition) as exc:
        next_execution_status(terminal, event_type)
    assert exc.value.current == terminal
    assert exc.value.event_type == event_type


@pytest.mark.parametrize(
    ("current", "event_type"),
    [
        ("queued", "progress"),
        ("queued", "started"),
        ("queued", "result_ready"),
        ("queued", "failed"),
        ("accepted", "accepted"),
        ("accepted", "progress"),
        ("accepted", "result_ready"),
        ("running", "accepted"),
        ("running", "started"),
        ("unknown", "accepted"),
        ("unknown", "progress"),
    ],
)
def test_out_of_order_events_rejected(current, event_type):
    with pytest.raises(InvalidTransition):
        next_execution_status(current, event_type)


def test_unknown_is_never_an_event_result():
    for current in EXECUTION_STATUSES:
        for event_type in ("accepted", "started", "progress", "result_ready", "failed"):
            try:
                assert next_execution_status(current, event_type) != "unknown"
            except InvalidTransition:
                pass


def test_invalid_transition_message_names_both_sides():
    exc = InvalidTransition("result_ready", "started")
    assert "result_ready" in str(exc)
    assert "started" in str(exc)


# --- 사용자 상태 (PRD 3절 표, 행 순서) -----------------------------------------

BASE = TaskView(
    kind="diagnosis",
    run_mode="manual",
    completion_mode="review",
    selection_status="selected",
    selection_reason="operations.diagnose · workflow_id=daily-report 일치 후보 1개",
    selected_agent_id="agent-ops-demo",
    predecessor_status=None,
    connector_online=None,
    connector_last_seen=None,
    execution_status=None,
    last_progress=None,
    failed_code=None,
    failed_message=None,
    process_stopped=None,
    verdict=None,
    verdict_detail=None,
    review_decision=None,
    finished=False,
)


def view(**changes) -> TaskView:
    return dataclasses.replace(BASE, **changes)


def test_row1_waiting_for_predecessor():
    assert user_status(view(kind="code_change", run_mode="auto", predecessor_status="실행 중")) == UserStatus(
        "대기", "선행 대기"
    )


def test_row1_connector_offline_shows_last_seen():
    assert user_status(
        view(
            kind="code_change",
            run_mode="auto",
            predecessor_status="완료",
            connector_online=False,
            connector_last_seen="2026-09-20 10:12:03 KST",
        )
    ) == UserStatus("대기", "연결 끊김, 마지막 확인 2026-09-20 10:12:03 KST")


def test_row2_no_candidate_needs_attention():
    assert user_status(
        view(selection_status="needs_selection", selection_reason="후보 없음", selected_agent_id=None)
    ) == UserStatus("확인 필요", "후보 없음")


def test_row2_many_candidates_needs_attention():
    assert user_status(
        view(selection_status="needs_selection", selection_reason="후보 2개 — 선택 필요", selected_agent_id=None)
    ) == UserStatus("확인 필요", "후보 2개 — 선택 필요")


def test_row3_manual_run_ready():
    assert user_status(view()) == UserStatus("실행 가능", "agent-ops-demo 선택됨")


def test_row3_code_change_ready_when_connector_online():
    assert user_status(
        view(kind="code_change", predecessor_status="완료", connector_online=True, selected_agent_id="agent-codex-mac")
    ) == UserStatus("실행 가능", "agent-codex-mac 선택됨")


def test_auto_run_with_conditions_met_waits_for_worker():
    assert user_status(view(kind="code_change", run_mode="auto", predecessor_status="완료", connector_online=True)) == (
        UserStatus("대기", "자동 실행 대기")
    )


def test_row4_queued_and_accepted_are_requested():
    assert user_status(view(execution_status="queued")) == UserStatus("실행 요청됨", "접수 대기")
    assert user_status(view(execution_status="accepted")) == UserStatus("실행 요청됨", "접수 확인")


def test_row5_running_shows_last_progress():
    assert user_status(view(execution_status="running", last_progress="get_run daily-0920-0900 조회 완료")) == (
        UserStatus("실행 중", "get_run daily-0920-0900 조회 완료")
    )
    assert user_status(view(execution_status="running")) == UserStatus("실행 중", "시작 확인")


def test_row6_auto_completion_passed():
    assert user_status(
        view(
            completion_mode="auto",
            execution_status="result_ready",
            verdict="passed",
            verdict_detail="response_path_changed · 검증 6/6 통과",
            finished=True,
        )
    ) == UserStatus("완료", "response_path_changed · 검증 6/6 통과")


def test_row7_auto_completion_failed_or_undecidable():
    assert user_status(
        view(completion_mode="auto", execution_status="result_ready", verdict="failed", verdict_detail="첨부 누락: log-daily-0920@1")
    ) == UserStatus("확인 필요", "첨부 누락: log-daily-0920@1")
    assert user_status(
        view(completion_mode="auto", execution_status="result_ready", verdict="undecidable", verdict_detail="unsupported_diagnosis")
    ) == UserStatus("확인 필요", "unsupported_diagnosis")
    assert user_status(view(completion_mode="auto", execution_status="result_ready", verdict="undecidable")) == (
        UserStatus("확인 필요", "판정 불가")
    )
    assert user_status(view(completion_mode="auto", execution_status="result_ready")) == UserStatus(
        "확인 필요", "판정 대기"
    )


def test_row8_review_completion_waits_for_reviewer():
    assert user_status(view(completion_mode="review", execution_status="result_ready")) == UserStatus(
        "확인 필요", "검토 대기"
    )


def test_row9_failed_with_process_stopped():
    assert user_status(
        view(
            execution_status="failed",
            failed_code="timeout",
            failed_message="Codex 실행이 20분을 초과해 종료했습니다.",
            process_stopped=True,
            finished=True,
        )
    ) == UserStatus("실패", "timeout · Codex 실행이 20분을 초과해 종료했습니다.")


def test_row10_failed_without_stop_confirmation_or_unknown():
    assert user_status(
        view(execution_status="failed", failed_code="timeout", failed_message="종료 확인 실패", process_stopped=False)
    ) == UserStatus("확인 필요", "종료 미확인 — 재실행하지 않음")
    assert user_status(view(execution_status="unknown")) == UserStatus("확인 필요", "시작 여부 불명 — 재실행하지 않음")


def test_review_approve_finishes_as_completed():
    assert user_status(
        view(execution_status="result_ready", review_decision="approve", finished=True)
    ) == UserStatus("완료", "검토 승인")


def test_review_close_finishes_as_failed():
    assert user_status(
        view(execution_status="result_ready", review_decision="close", finished=True)
    ) == UserStatus("실패", "검토 거절")


def test_request_changes_falls_through_to_new_attempt():
    assert user_status(view(execution_status="queued", review_decision="request_changes")) == UserStatus(
        "실행 요청됨", "접수 대기"
    )


def test_row_order_predecessor_before_selection():
    assert user_status(
        view(
            kind="code_change",
            run_mode="auto",
            predecessor_status="실행 중",
            selection_status="needs_selection",
            selection_reason="후보 없음",
            selected_agent_id=None,
        )
    ) == UserStatus("대기", "선행 대기")


def test_every_label_is_in_glossary():
    views = [
        view(kind="code_change", run_mode="auto", predecessor_status="실행 중"),
        view(),
        view(execution_status="queued"),
        view(execution_status="running"),
        view(execution_status="result_ready"),
        view(execution_status="result_ready", review_decision="approve", finished=True),
        view(execution_status="failed", failed_code="c", failed_message="m", process_stopped=True),
    ]
    labels = {user_status(v).label for v in views}
    assert labels == set(USER_STATUS_LABELS)


def test_task_view_is_immutable():
    with pytest.raises(dataclasses.FrozenInstanceError):
        BASE.kind = "code_change"  # type: ignore[misc]
