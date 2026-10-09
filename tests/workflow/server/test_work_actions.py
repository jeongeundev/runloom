# ruff: noqa: F811 — test_task_cycle 픽스처(cycle·settings)를 가져와 인자로 쓴다
"""담당·우선순위 바꾸기 — phase 16 step 3 (ADR-0022, ARCHITECTURE "업무 화면 — phase 16" 담당 바꾸기).

에이전트 담당 = 맡기기(선택 기록 → 지시 기록 → 착수), 멤버 = 배정만, `none` = 담당 해제. 실제 연결 프로그램·GitHub 은
부르지 않는다 — 원본 이슈는 `repo.upsert_source_issue` 로 넣는다(test_task_cycle 과 같은 시드).
"""

import json

import pytest

from workflow.adapters import repo
from workflow.contracts.v1 import Capability, KindSpec
from workflow.domain.work_status import WorkStatus
from workflow.server import work_actions
from workflow.server.work_actions import WorkActionError

from .test_owner_approval import TRIAGE, members, triage_task  # noqa: F401 — 픽스처
from .test_task_cycle import (  # noqa: F401 — 픽스처
    FIX,
    FIX_SHOP,
    NOW,
    REVIEW,
    SESSION,
    config,
    clock,
    cycle,
    direct_shop_task,
    executions,
    import_issue,
    make_worker,
    request_of,
    settings,
    worker,
    seen_at,
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
           session_id: str = SESSION, note: str = "") -> None:
    work_actions.assign_work(conn, store, settings, session_id=session_id,
                             work_item_id=work_of(conn, task_id)["work_item_id"], value=value, member_id=member_id,
                             now=now, note=note)


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
    seen_at(conn, NOW)  # 마지막 신호 NOW — OFFLINE 은 그 이틀 뒤
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



AUDIT = KindSpec(kind="audit", label="감사", capability_code="audit", scope_key="repository_id", input_kinds=[],
                 output_kind="generic_result", outcomes=["done", "needs_information"], instructions="", builtin=False)


def test_added_capability_makes_the_agent_a_candidate_for_a_user_kind(conn, admin):
    """phase 23 step 4 — 매칭은 등록부 + 능력이라 붙인 능력만으로 사용자 정의 종류의 맡기기 후보가 된다."""
    repo.insert_kind(conn, SESSION, AUDIT, NOW)
    repo.insert_work_item_task(conn, {
        "task_id": "task-audit", "session_id": SESSION, "title": "결제 로그 감사", "request": "로그를 살펴보세요.",
        "kind": "audit", "required_capability": {"code": "audit", "scope": {"repository_id": "billing"}},
        "selection_mode": "auto", "chosen_agent_id": None, "run_mode": "auto", "completion_mode": "review",
        "criteria": [], "predecessor_task_id": None, "revision": 1, "target": {},
        "status": "대기", "status_reason": "준비 판정 대기",
    }, NOW)
    work_item_id = work_of(conn, "task-audit")["work_item_id"]
    assert work_actions.agent_candidates(conn, SESSION, work_item_id) == []

    repo.add_agent_capability(conn, agent_id=REVIEW, capability=Capability(code="audit",
                                                                           scope={"repository_id": "billing"}))
    assert [a["agent_id"] for a in work_actions.agent_candidates(conn, SESSION, work_item_id)] == [REVIEW]
    repo.add_agent_capability(conn, agent_id=FIX_SHOP, capability=Capability(code="audit",
                                                                             scope={"repository_id": "shop"}))
    assert [a["agent_id"] for a in work_actions.agent_candidates(conn, SESSION, work_item_id)] == [REVIEW]

def test_no_open_stage_is_refused(conn, store, settings, admin, all_open):
    task_id = import_issue(conn, 1, labels=[])
    conn.execute("UPDATE tasks SET finished_at = ? WHERE task_id = ?", (NOW, task_id))
    refused(lambda: assign(conn, store, settings, task_id, f"agent:{FIX}", admin), 409, "no_open_stage")
    assert work_actions.agent_candidates(conn, SESSION, work_of(conn, task_id)["work_item_id"]) == []


# --- 지시 메모와 요청문 머리 (phase 17 step 8) ------------------------------------------


def test_cycle_handoff_request_carries_the_work_header_form_and_note(conn, store, settings, admin, all_open):
    task_id = import_issue(conn, 1, labels=[])
    form = {"goal": {"value": "쿠폰은 한 번만", "source": "github_body:### 목표"},
            "expected_behavior": {"value": "_No response_", "source": "github_body:### 기대 동작"}}
    conn.execute("UPDATE work_items SET form_json = ? WHERE work_item_id = ?",
                 (json.dumps(form, ensure_ascii=False), work_of(conn, task_id)["work_item_id"]))
    admin_name = repo.get_member(conn, SESSION, admin)["display_name"]

    assign(conn, store, settings, task_id, f"agent:{FIX}", admin, note="  결제 모듈만 보세요\n테스트 먼저  ")

    (execution,) = executions(conn, task_id)
    assert request_of(execution).request == (
        f"# RUN-1 버그 1\n\n## 업무 양식\n\n### 목표\n쿠폰은 한 번만\n\n"
        f"## 맡긴 사람 지시 ({admin_name})\n결제 모듈만 보세요\n테스트 먼저\n\n재현 절차 1"
    )
    assert repo.get_task(conn, task_id)["request"] == "재현 절차 1"  # 단계 원문은 그대로
    work = work_of(conn, task_id)
    assert (work["handoff_note"], work["handoff_note_by_member_id"]) == ("결제 모듈만 보세요\n테스트 먼저", admin)
    assert events(conn, task_id, "handoff_note") == [
        {"agent_id": FIX, "note": "결제 모듈만 보세요\n테스트 먼저", "by": admin}]


def test_non_cycle_handoff_request_carries_the_header_and_note(conn, store, settings, members, triage_task):
    assign(conn, store, settings, triage_task, f"agent:{TRIAGE}", members["a"], note="로그부터 보세요")

    (execution,) = executions(conn, triage_task)
    key = f"RUN-{work_of(conn, triage_task)['key_number']}"
    assert request_of(execution).request == (
        f"# {key} 보고서 분류\n\n## 맡긴 사람 지시 (김맡김)\n로그부터 보세요\n\n원인을 분류해 주세요.")


def test_blank_note_is_no_note(conn, store, settings, admin, all_open):
    task_id = import_issue(conn, 1, labels=[])
    assign(conn, store, settings, task_id, f"agent:{FIX}", admin, note="  \n ")
    (execution,) = executions(conn, task_id)
    assert request_of(execution).request == "# RUN-1 버그 1\n\n재현 절차 1"
    assert work_of(conn, task_id)["handoff_note"] is None
    assert events(conn, task_id, "handoff_note") == []


def test_note_over_the_limit_is_422(conn, store, settings, admin, all_open):
    task_id = import_issue(conn, 1, labels=[])
    error = refused(lambda: assign(conn, store, settings, task_id, f"agent:{FIX}", admin, note="가" * 2001),
                    422, "invalid_field")
    assert error.field == "note"
    assert executions(conn, task_id) == [] and work_of(conn, task_id)["assignee_type"] is None
    assign(conn, store, settings, task_id, f"agent:{FIX}", admin, note="가" * 2000)  # 경계는 받는다
    assert work_of(conn, task_id)["handoff_note"] == "가" * 2000


def test_member_assignment_ignores_the_note(conn, store, settings, admin, all_open):
    task_id = import_issue(conn, 1, labels=[])
    assign(conn, store, settings, task_id, f"member:{admin}", admin, note="가" * 2001)  # 길어도 무시
    work = work_of(conn, task_id)
    assert (work["assignee_type"], work["handoff_note"]) == ("member", None)
    assert events(conn, task_id, "handoff_note") == []
    assign(conn, store, settings, task_id, "none", admin, note="메모")
    assert work_of(conn, task_id)["handoff_note"] is None


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



# --- 직접 작업 (step 8) ---------------------------------------------------------------


def start_direct(conn, task_id: str, member_id: str, *, now: str = LATER) -> str:
    return work_actions.start_direct(conn, session_id=SESSION, work_item_id=work_of(conn, task_id)["work_item_id"],
                                     member_id=member_id, now=now)


def stop_direct(conn, task_id: str) -> None:
    work_actions.stop_direct(conn, session_id=SESSION, work_item_id=work_of(conn, task_id)["work_item_id"], now=LATER)


def test_start_direct_takes_the_assignee_and_gives_a_branch_name(conn, store, settings, admin, all_open):
    task_id = import_issue(conn, 1, labels=[], title="Login fails 로그인 실패")
    other = repo.add_member(conn, SESSION, display_name="이담당", now=NOW)
    assign(conn, store, settings, task_id, f"member:{other}", admin)
    repo.set_member_display_name(conn, SESSION, admin, "김개발", now=NOW)

    assert start_direct(conn, task_id, admin) == "RUN-1-login-fails"

    work = work_of(conn, task_id)
    assert (work["assignee_type"], work["assignee_id"]) == ("member", admin)  # 다른 멤버 담당이어도 누른 사람
    assert (work["direct_member_id"], work["direct_started_at"], work["direct_branch"]) == (
        admin, LATER, "RUN-1-login-fails")
    assert (work["status"], work["status_reason"]) == ("직접 작업 중", "김개발")
    assert events(conn, task_id, "direct_started") == [{"member_id": admin, "branch": "RUN-1-login-fails"}]
    assert events(conn, task_id, "assigned")[-1] == {
        "from": {"type": "member", "id": other}, "to": {"type": "member", "id": admin}, "by": admin}
    assert executions(conn, task_id) == []  # 직접 작업은 실행을 만들지 않는다
    # 같은 멤버가 다시 누르면 변화 없음
    assert start_direct(conn, task_id, admin) == "RUN-1-login-fails"
    assert len(events(conn, task_id, "direct_started")) == 1


def test_stop_direct_clears_the_columns_and_keeps_the_assignee(conn, admin, all_open):
    task_id = import_issue(conn, 1, labels=[])
    start_direct(conn, task_id, admin)
    stop_direct(conn, task_id)
    stop_direct(conn, task_id)  # 직접 작업 중이 아니면 변화 없음
    work = work_of(conn, task_id)
    assert (work["direct_member_id"], work["direct_started_at"], work["direct_branch"]) == (None, None, None)
    assert (work["assignee_type"], work["assignee_id"]) == ("member", admin)
    assert work["status"] != "직접 작업 중"
    assert events(conn, task_id, "direct_stopped") == [{"member_id": admin, "reason": "stopped"}]


def test_start_direct_is_refused_while_an_agent_runs(conn, store, settings, admin):
    task_id = direct_shop_task(conn)
    assign(conn, store, settings, task_id, f"agent:{FIX_SHOP}", admin)
    assert len(executions(conn, task_id)) == 1
    refused(lambda: start_direct(conn, task_id, admin), 409, "execution_conflict")
    assert work_of(conn, task_id)["direct_member_id"] is None


def test_start_direct_on_closed_work_is_409(conn, admin, all_open):
    task_id = import_issue(conn, 1, labels=[])
    repo.set_work_status(conn, work_of(conn, task_id)["work_item_id"], WorkStatus("종료", "원본 이슈 닫힘"), now=NOW)
    refused(lambda: start_direct(conn, task_id, admin), 409, "work_closed")
    assert work_of(conn, task_id)["direct_member_id"] is None


def test_hand_direct_work_to_an_agent_ends_it_and_starts(conn, store, settings, admin):
    task_id = direct_shop_task(conn)
    start_direct(conn, task_id, admin)

    assign(conn, store, settings, task_id, f"agent:{FIX_SHOP}", admin)

    work = work_of(conn, task_id)
    assert work["direct_member_id"] is None
    assert (work["assignee_type"], work["assignee_id"]) == ("agent", FIX_SHOP)
    assert events(conn, task_id, "direct_stopped") == [{"member_id": admin, "reason": "handed_to_agent"}]
    assert len(executions(conn, task_id)) == 1
    assert work["status"] == "에이전트 작업 중"


def test_assigning_another_member_ends_the_direct_work(conn, store, settings, admin, all_open):
    task_id = import_issue(conn, 1, labels=[])
    other = repo.add_member(conn, SESSION, display_name="이담당", now=NOW)
    start_direct(conn, task_id, admin)
    assign(conn, store, settings, task_id, f"member:{other}", admin)
    assert work_of(conn, task_id)["direct_member_id"] is None
    assert events(conn, task_id, "direct_stopped") == [{"member_id": admin, "reason": "reassigned"}]


def test_clearing_the_assignee_of_a_direct_work_is_409(conn, store, settings, admin, all_open):
    task_id = import_issue(conn, 1, labels=[])
    start_direct(conn, task_id, admin)
    refused(lambda: assign(conn, store, settings, task_id, "none", admin), 409, "direct_work_active")
    assert work_of(conn, task_id)["direct_member_id"] == admin


def test_worker_does_not_start_a_stage_under_direct_work(conn, admin, worker):
    task_id = direct_shop_task(conn)  # 자동 실행 — 선택된 Agent 가 있어 원래는 tick 이 착수한다
    start_direct(conn, task_id, admin, now=NOW)

    worker.tick()

    assert executions(conn, task_id) == []
    assert "직접 작업 중 — 에이전트에게 넘기면 시작" in repo.get_task(conn, task_id)["status_reason"]
    assert work_of(conn, task_id)["status"] == "직접 작업 중"
    assert repo.list_human_requests(conn, task_id) == []  # 대기 사유일 뿐 사람 요청이 아니다
