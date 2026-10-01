"""착수 코드 — phase 17 step 6 에서 `work_actions` 에서 옮김(ARCHITECTURE "꺼진 러너 대기" 착수 코드 이동).

동작은 test_work_actions·test_owner_approval 이 본다. 여기서는 옮긴 이름이 같은 객체인지와 v13 의 꺼진 러너 오류만 본다.
"""

import pytest

from workflow.adapters import repo
from workflow.contracts.v1 import SelectionRecord
from workflow.server import stage_runs, work_actions

from .test_owner_approval import OFFLINE, TRIAGE, members, triage_task  # noqa: F401 — 픽스처
from .test_task_cycle import SESSION, cycle, settings  # noqa: F401 — 픽스처


def test_work_actions_keeps_the_moved_names():
    assert work_actions.WorkActionError is stage_runs.WorkActionError
    assert work_actions.start_execution is stage_runs.start_execution
    assert work_actions.run_task is stage_runs.run_task


def test_run_task_refuses_an_offline_runner(conn, settings, triage_task):  # noqa: F811
    repo.save_selection(conn, _selected(triage_task))
    stage = repo.get_task(conn, triage_task)

    with pytest.raises(stage_runs.WorkActionError) as caught:
        stage_runs.run_task(conn, stage, session_id=SESSION, now=OFFLINE, settings=settings)

    assert (caught.value.status, caught.value.code, caught.value.message) == (
        409, "runner_offline", "이소유의 러너 꺼짐 · 켜지면 시작")
    assert repo.list_executions(conn, triage_task) == []


def _selected(task_id: str) -> SelectionRecord:
    capability = {"code": "classify", "scope": {"repository_id": "billing"}}
    return SelectionRecord.model_validate({
        "task_id": task_id, "mode": "manual", "required_capability": capability, "candidate_count": 1,
        "selected_agent_id": TRIAGE, "matched": capability, "status": "selected", "reason": "직접 지정",
    })
