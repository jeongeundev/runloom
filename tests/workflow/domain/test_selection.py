"""자동·직접 선택 규칙 — PRD 2절 "첫 구현의 판단 규칙", CONTRACT 9절 선택 기록."""

import pytest

from workflow.contracts.v1 import Capability, SelectionRecord
from workflow.domain.selection import Candidate, select_agent

DIAGNOSE = Capability(code="operations.diagnose", scope={"workflow_id": "daily-report"})
DIAGNOSE_OTHER = Capability(code="operations.diagnose", scope={"workflow_id": "weekly-report"})
MODIFY = Capability(code="code.modify", scope={"repository_id": "demo-report-repo"})

OPS = Candidate(agent_id="agent-ops-demo", capabilities=(DIAGNOSE,))
OPS_TWIN = Candidate(agent_id="agent-ops-twin", capabilities=(DIAGNOSE,))
CODEX = Candidate(agent_id="agent-codex-mac", capabilities=(MODIFY,))


def test_auto_single_match_is_selected_with_contract_reason():
    record = select_agent("diagnose-daily-0920", DIAGNOSE, [OPS, CODEX])

    assert isinstance(record, SelectionRecord)
    assert record.status == "selected"
    assert record.selected_agent_id == "agent-ops-demo"
    assert record.matched == DIAGNOSE
    assert record.candidate_count == 1
    assert record.mode == "auto"
    assert record.task_id == "diagnose-daily-0920"
    assert record.required_capability == DIAGNOSE
    assert record.reason == "operations.diagnose · workflow_id=daily-report 일치 후보 1개"


def test_auto_no_match_needs_selection():
    record = select_agent("t", DIAGNOSE, [CODEX])

    assert record.status == "needs_selection"
    assert record.selected_agent_id is None
    assert record.matched is None
    assert record.candidate_count == 0
    assert record.reason == "후보 없음"


def test_auto_two_matches_needs_selection():
    record = select_agent("t", DIAGNOSE, [OPS, OPS_TWIN, CODEX])

    assert record.status == "needs_selection"
    assert record.selected_agent_id is None
    assert record.candidate_count == 2
    assert record.reason == "후보 2개 — 선택 필요"


def test_auto_excludes_not_allowed_candidates():
    blocked = Candidate(agent_id="agent-ops-twin", capabilities=(DIAGNOSE,), allowed=False)

    record = select_agent("t", DIAGNOSE, [OPS, blocked])

    assert record.status == "selected"
    assert record.selected_agent_id == "agent-ops-demo"
    assert record.candidate_count == 1


def test_auto_scope_must_match_exactly():
    other_scope = Candidate(agent_id="agent-ops-other", capabilities=(DIAGNOSE_OTHER,))

    record = select_agent("t", DIAGNOSE, [other_scope])

    assert record.status == "needs_selection"
    assert record.candidate_count == 0
    assert record.reason == "후보 없음"


def test_auto_ignores_chosen_agent_id():
    record = select_agent("t", DIAGNOSE, [OPS, OPS_TWIN], mode="auto", chosen_agent_id="agent-ops-twin")

    assert record.status == "needs_selection"
    assert record.candidate_count == 2


def test_manual_allowed_and_matching_is_selected():
    record = select_agent("t", DIAGNOSE, [OPS, OPS_TWIN], mode="manual", chosen_agent_id="agent-ops-twin")

    assert record.status == "selected"
    assert record.mode == "manual"
    assert record.selected_agent_id == "agent-ops-twin"
    assert record.matched == DIAGNOSE
    assert record.candidate_count == 1
    assert record.reason == "직접 선택"


def test_manual_not_allowed_needs_selection():
    blocked = Candidate(agent_id="agent-ops-twin", capabilities=(DIAGNOSE,), allowed=False)

    record = select_agent("t", DIAGNOSE, [OPS, blocked], mode="manual", chosen_agent_id="agent-ops-twin")

    assert record.status == "needs_selection"
    assert record.selected_agent_id is None
    assert record.reason == "선택한 에이전트는 사용 허용되지 않음"


def test_manual_unknown_agent_is_not_allowed():
    record = select_agent("t", DIAGNOSE, [OPS], mode="manual", chosen_agent_id="agent-none")

    assert record.status == "needs_selection"
    assert record.reason == "선택한 에이전트는 사용 허용되지 않음"


def test_manual_without_capability_needs_selection():
    record = select_agent("t", DIAGNOSE, [OPS, CODEX], mode="manual", chosen_agent_id="agent-codex-mac")

    assert record.status == "needs_selection"
    assert record.selected_agent_id is None
    assert record.reason == "선택한 에이전트에 operations.diagnose 능력 없음"


def test_manual_requires_chosen_agent_id():
    with pytest.raises(ValueError):
        select_agent("t", DIAGNOSE, [OPS], mode="manual")


def test_candidate_is_immutable_value():
    with pytest.raises(AttributeError):
        OPS.agent_id = "other"  # type: ignore[misc]
