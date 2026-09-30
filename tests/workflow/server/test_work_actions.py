# ruff: noqa: F811 — test_task_cycle 픽스처(cycle·settings)를 가져와 인자로 쓴다
"""담당·우선순위 바꾸기 — phase 16 step 3 (ADR-0022, ARCHITECTURE "업무 화면 — phase 16" 담당 바꾸기).

에이전트 담당 = 맡기기(선택 기록 → 지시 기록 → 착수), 멤버 = 배정만, `none` = 담당 해제. 실제 연결 프로그램·GitHub 은
부르지 않는다 — 원본 이슈는 `repo.upsert_source_issue` 로 넣는다(test_task_cycle 과 같은 시드).
"""

import json

import pytest

from workflow.adapters import repo
from workflow.domain.work_status import WorkStatus
from workflow.server import work_actions
from workflow.server.work_actions import WorkActionError

from .test_task_cycle import (  # noqa: F401 — 픽스처
    FIX,
    FIX_SHOP,
    NOW,
    REVIEW,
    SESSION,
    config,
    cycle,
    direct_shop_task,
    executions,
    import_issue,
    settings,
)

LATER = "2026-10-06T12:00:30Z"  # 연결 프로그램 마지막 신호(NOW) 30초 뒤 — 온라인
OFFLINE = "2026-10-08T12:00:00Z"  # 연결 프로그램 마지막 신호(NOW)에서 이틀 뒤 — 오프라인


@pytest.fixture
def admin(conn, cycle) -> str:
    return repo.ensure_first_admin(conn, SESSION, now=NOW)


@pytest.fixture
def all_open(conn, cycle):
    """지시 전 이슈가 생기는 all_open 소스(test_web_github_connect 의 cycle_op 과 같은 설정)."""
    repo.save_github_source(conn, SESSION, config(review_agent_id=REVIEW, intake="all_open", label_filter=[],
                                                  trigger_label="runloom"), NOW)


def work_of(conn, task_id: str):
    return repo.work_item_of_task(conn, task_id)


def assign(conn, store, settings, task_id: str, value: str, member_id: str, *, now: str = LATER,
           session_id: str = SESSION) -> None:
    work_actions.assign_work(conn, store, settings, session_id=session_id,
                             work_item_id=work_of(conn, task_id)["work_item_id"], value=value, member_id=member_id,
                             now=now)


def refused(call, status: int, code: str) -> WorkActionError:
    with pytest.raises(WorkActionError) as caught:
        call()
    assert (caught.value.status, caught.value.code) == (status, code), caught.value.message
    return caught.value


def events(conn, task_id: str, type_: str) -> list[dict]:
    return [json.loads(e["data_json"]) for e in repo.list_work_item_events(conn, work_of(conn, task_id)["work_item_id"])
            if e["type"] == type_]


# --- 에이전트 = 맡기기 ---------------------------------------------------------------


def test_agent_on_an_undelegated_issue_delegates_and_starts(conn, store, settings, admin, all_open):
    task_id = import_issue(conn, 1, labels=[])
    assert repo.get_source_issue_by_task(conn, SESSION, task_id)["delegated_by"] is None

    assign(conn, store, settings, task_id, f"agent:{FIX}", admin)

    issue = repo.get_source_issue_by_task(conn, SESSION, task_id)
    assert (issue["delegated_by"], issue["delegated_at"]) == ("operator", LATER)
    assert len(executions(conn, task_id)) == 1
    selection = repo.get_selection(conn, task_id)
    assert (selection.mode, selection.status, selection.selected_agent_id) == ("manual", "selected", FIX)
    work = work_of(conn, task_id)
    assert (work["assignee_type"], work["assignee_id"]) == ("agent", FIX)
    assert work["requested_by_member_id"] == admin  # 맡긴 사람 = 누른 멤버
    assert work["status"] == "에이전트 작업 중"
    (assigned,) = events(conn, task_id, "assigned")
    assert (assigned["to"], assigned["by"]) == ({"type": "agent", "id": FIX}, admin)


def test_agent_on_a_direct_work_records_the_selection_and_starts(conn, store, settings, admin):
    task_id = direct_shop_task(conn)
    assert repo.get_selection(conn, task_id) is None

    assign(conn, store, settings, task_id, f"agent:{FIX_SHOP}", admin)

    assert repo.get_selection(conn, task_id).selected_agent_id == FIX_SHOP
    assert repo.get_task(conn, task_id)["chosen_agent_id"] == FIX_SHOP
    (execution,) = executions(conn, task_id)
    assert execution["agent_id"] == FIX_SHOP
    work = work_of(conn, task_id)
    assert (work["assignee_type"], work["assignee_id"], work["requested_by_member_id"]) == ("agent", FIX_SHOP, admin)
    assert work["status"] == "에이전트 작업 중"


@pytest.mark.parametrize("agent_id", [REVIEW, FIX_SHOP, "agent-nope"])
def test_agent_without_the_stage_capability_is_refused(conn, store, settings, admin, all_open, agent_id):
    task_id = import_issue(conn, 1, labels=[])  # code.fix · billing
    error = refused(lambda: assign(conn, store, settings, task_id, f"agent:{agent_id}", admin), 422, "invalid_field")
    assert error.field == "assignee" and error.message == "이 단계를 맡을 수 없는 에이전트입니다."
    assert executions(conn, task_id) == []
    assert repo.get_source_issue_by_task(conn, SESSION, task_id)["delegated_by"] is None
    assert work_of(conn, task_id)["assignee_type"] is None
    assert events(conn, task_id, "assigned") == []


def test_offline_runner_keeps_the_assignee_and_waits(conn, store, settings, admin, all_open):
    task_id = import_issue(conn, 1, labels=[])

    assign(conn, store, settings, task_id, f"agent:{FIX}", admin, now=OFFLINE)  # 오류로 돌려주지 않는다

    assert executions(conn, task_id) == []
    work = work_of(conn, task_id)
    assert (work["assignee_type"], work["assignee_id"]) == ("agent", FIX)
    assert repo.get_selection(conn, task_id).selected_agent_id == FIX
    assert repo.get_source_issue_by_task(conn, SESSION, task_id)["delegated_by"] == "operator"
    assert work["status"] == "대기" and work["status_reason"]  # 대기 사유는 단계 상태에서


def test_agent_candidates_are_the_agents_the_open_stage_accepts(conn, admin, all_open):
    task_id = import_issue(conn, 1, labels=[])
    work_item_id = work_of(conn, task_id)["work_item_id"]
    assert work_actions.open_stage(conn, work_item_id)["task_id"] == task_id
    assert [a["agent_id"] for a in work_actions.agent_candidates(conn, SESSION, work_item_id)] == [FIX]


def test_no_open_stage_is_refused(conn, store, settings, admin, all_open):
    task_id = import_issue(conn, 1, labels=[])
    conn.execute("UPDATE tasks SET finished_at = ? WHERE task_id = ?", (NOW, task_id))
    refused(lambda: assign(conn, store, settings, task_id, f"agent:{FIX}", admin), 409, "no_open_stage")
    assert work_actions.agent_candidates(conn, SESSION, work_of(conn, task_id)["work_item_id"]) == []


# --- 멤버 = 배정만 · none = 해제 ------------------------------------------------------


def test_member_is_assigned_and_none_clears(conn, store, settings, admin, all_open):
    task_id = import_issue(conn, 1, labels=[])
    other = repo.add_member(conn, SESSION, display_name="이담당", now=NOW)

    assign(conn, store, settings, task_id, f"member:{other}", admin)
    work = work_of(conn, task_id)
    assert (work["assignee_type"], work["assignee_id"]) == ("member", other)
    assert executions(conn, task_id) == [] and repo.get_selection(conn, task_id) is None  # 단계는 그대로
    assert repo.get_source_issue_by_task(conn, SESSION, task_id)["delegated_by"] is None

    assign(conn, store, settings, task_id, "none", admin)
    work = work_of(conn, task_id)
    assert (work["assignee_type"], work["assignee_id"]) == (None, None)
    assert work["status"] == "새로 들어옴" and work["status_reason"] == "담당 없음"  # 재계산
    assert events(conn, task_id, "assigned") == [
        {"from": None, "to": {"type": "member", "id": other}, "by": admin},
        {"from": {"type": "member", "id": other}, "to": None, "by": admin},
    ]


def test_same_assignee_is_no_change(conn, store, settings, admin, all_open):
    task_id = import_issue(conn, 1, labels=[])
    assign(conn, store, settings, task_id, f"member:{admin}", admin)
    assign(conn, store, settings, task_id, f"member:{admin}", admin)
    assert len(events(conn, task_id, "assigned")) == 1


def test_inactive_or_foreign_member_is_refused(conn, store, settings, admin, all_open):
    task_id = import_issue(conn, 1, labels=[])
    gone = repo.add_member(conn, SESSION, display_name="퇴사", now=NOW)
    repo.disable_member(conn, SESSION, gone, now=NOW)
    repo.create_session(conn, "sess-other", NOW)
    foreign = repo.ensure_first_admin(conn, "sess-other", now=NOW)
    for member_id in (gone, foreign, "mem-nope"):
        error = refused(lambda: assign(conn, store, settings, task_id, f"member:{member_id}", admin),
                        422, "invalid_field")
        assert error.field == "assignee"
    assert work_of(conn, task_id)["assignee_type"] is None


@pytest.mark.parametrize("value", ["", "agent", "agent:", "member:", "robot:x", "none:x", "NONE"])
def test_malformed_assignee_value_is_refused(value):
    error = refused(lambda: work_actions.parse_assignee(value), 422, "invalid_field")
    assert error.field == "assignee"


def test_parse_assignee():
    assert work_actions.parse_assignee("none") is None
    assert work_actions.parse_assignee("member:mem-1") == ("member", "mem-1")
    assert work_actions.parse_assignee("agent:agent-fix") == ("agent", "agent-fix")


# --- 막는 조건 --------------------------------------------------------------------


@pytest.mark.parametrize("value", [f"agent:{FIX_SHOP}", "member:ADMIN", "none"])
def test_active_execution_is_409(conn, store, settings, admin, value):
    task_id = direct_shop_task(conn)
    assign(conn, store, settings, task_id, f"agent:{FIX_SHOP}", admin)
    assert len(executions(conn, task_id)) == 1
    value = value.replace("ADMIN", admin)
    refused(lambda: assign(conn, store, settings, task_id, value, admin), 409, "execution_conflict")
    assert work_of(conn, task_id)["assignee_id"] == FIX_SHOP
    assert len(executions(conn, task_id)) == 1


@pytest.mark.parametrize("value", [f"agent:{FIX}", "member:ADMIN", "none"])
def test_closed_work_is_409(conn, store, settings, admin, all_open, value):
    task_id = import_issue(conn, 1, labels=[])
    repo.set_work_status(conn, work_of(conn, task_id)["work_item_id"], WorkStatus("종료", "원본 이슈 닫힘"), now=NOW)
    value = value.replace("ADMIN", admin)
    refused(lambda: assign(conn, store, settings, task_id, value, admin), 409, "work_closed")
    assert executions(conn, task_id) == []


def test_other_workspace_work_is_404(conn, store, settings, admin, all_open):
    task_id = import_issue(conn, 1, labels=[])
    repo.create_session(conn, "sess-other", NOW)
    refused(lambda: assign(conn, store, settings, task_id, "none", admin, session_id="sess-other"), 404, "not_found")
    refused(lambda: work_actions.set_priority(conn, session_id="sess-other",
                                              work_item_id=work_of(conn, task_id)["work_item_id"], priority="high",
                                              member_id=admin, now=LATER), 404, "not_found")


# --- 우선순위 --------------------------------------------------------------------


def set_priority(conn, task_id: str, priority: str, member_id: str) -> None:
    work_actions.set_priority(conn, session_id=SESSION, work_item_id=work_of(conn, task_id)["work_item_id"],
                              priority=priority, member_id=member_id, now=LATER)


def test_priority_change_records_an_event(conn, admin, all_open):
    task_id = import_issue(conn, 1, labels=[])
    before = work_of(conn, task_id)["status"], work_of(conn, task_id)["status_reason"]

    set_priority(conn, task_id, "high", admin)
    set_priority(conn, task_id, "high", admin)  # 같으면 그대로

    work = work_of(conn, task_id)
    assert (work["priority"], work["updated_at"]) == ("high", LATER)
    assert (work["status"], work["status_reason"]) == before  # 업무 상태에 영향 없음
    assert events(conn, task_id, "priority_changed") == [{"from": "normal", "to": "high", "by": admin}]


@pytest.mark.parametrize("priority", ["", "urgent", "HIGH"])
def test_unknown_priority_is_422(conn, admin, all_open, priority):
    task_id = import_issue(conn, 1, labels=[])
    error = refused(lambda: set_priority(conn, task_id, priority, admin), 422, "invalid_field")
    assert error.field == "priority"
    assert work_of(conn, task_id)["priority"] == "normal"


def test_priority_of_closed_work_is_409(conn, admin, all_open):
    task_id = import_issue(conn, 1, labels=[])
    repo.set_work_status(conn, work_of(conn, task_id)["work_item_id"], WorkStatus("완료", "PR 병합 — #3"), now=NOW)
    refused(lambda: set_priority(conn, task_id, "low", admin), 409, "work_closed")
