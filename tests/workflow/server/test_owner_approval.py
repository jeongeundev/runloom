# ruff: noqa: F811 — test_task_cycle 픽스처(cycle·settings·clock)를 가져와 인자로 쓴다
"""소유자 승인·꺼진 러너 대기 — phase 17 step 6 (ADR-0023 결정 1·2, ARCHITECTURE "사람 사이 인계 — phase 17").

멤버 A(김맡김)·B(이소유), billing 러너(수정 FIX·검토 REVIEW·분류 TRIAGE 에이전트)는 B 소유. 맡기기 → 승인 요청 →
승인·거절, 소유자 알림, 꺼진 러너 대기(모든 종류)를 본다. 실제 러너·GitHub·웹훅은 부르지 않는다 — 알림은 대기열 행만 본다.
"""

import pytest
from fastapi.testclient import TestClient

from workflow.adapters import repo
from workflow.adapters.db import connect
from workflow.adapters.secret_store import NOTIFY_WEBHOOK_URL, SecretStore
from workflow.contracts.v1 import KindSpec
from workflow.domain.work_list import filter_rows
from workflow.server import human_api, owner_approval, views, work_actions
from workflow.server.errors import ApiError
from workflow.server.work_actions import WorkActionError
from workflow.server.worker import Worker

from .conftest import log_in_member
from .test_task_cycle import (  # noqa: F401 — 픽스처
    FIX,
    NOW,
    REVIEW,
    SESSION,
    NoCallbacks,
    clock,
    config,
    cycle,
    executions,
    import_issue,
    settings,
)

LATER = "2026-10-06T12:00:30Z"  # 러너 마지막 신호(NOW) 30초 뒤 — 켜짐
OFFLINE = "2026-10-08T12:00:00Z"  # 이틀 뒤 — 꺼짐
BACK = "2026-10-08T12:00:10Z"  # 꺼진 뒤 러너가 다시 붙은 시각
WEBHOOK = "https://discord.com/api/webhooks/123/tok-secret-path"
TRIAGE = "agent-triage"
TRIAGE_KIND = KindSpec(
    kind="triage", label="분류", capability_code="triage", scope_key="repository_id", input_kinds=[],
    output_kind="generic_result", outcomes=["ready_for_handoff"], instructions="원인을 분류하세요.", builtin=False,
)


@pytest.fixture
def members(conn, cycle) -> dict:
    """관리자·A·B. billing 러너(FIX·REVIEW·TRIAGE)의 소유자 = B."""
    admin = repo.ensure_first_admin(conn, SESSION, now=NOW)
    a = repo.add_member(conn, SESSION, display_name="김맡김", now=NOW)
    b = repo.add_member(conn, SESSION, display_name="이소유", now=NOW)
    conn.execute("UPDATE connectors SET owner_member_id = ? WHERE connector_id = ?", (b, cycle["billing"]))
    return {"admin": admin, "a": a, "b": b}


@pytest.fixture
def all_open(conn, cycle):
    """지시 전 이슈(워커가 스스로 시작하지 않음)가 생기는 소스."""
    repo.save_github_source(conn, SESSION, config(review_agent_id=REVIEW, intake="all_open", label_filter=[],
                                                  trigger_label="runloom"), NOW)


@pytest.fixture
def secrets(settings) -> SecretStore:
    """공용 알림 웹훅이 있어야 알림 행이 쌓인다(보내지는 않는다 — notifier 없음)."""
    store = SecretStore(settings.secret_dir)
    store.write(NOTIFY_WEBHOOK_URL, WEBHOOK)
    return store


@pytest.fixture
def worker(app, settings, store, clock, secrets) -> Worker:
    return Worker(lambda: connect(settings.db_path), store, NoCallbacks(), settings, clock, secrets=secrets)


@pytest.fixture
def triage_task(conn, cycle, members) -> str:
    """순환이 아닌 사용자 정의 종류 `triage` 의 단계 하나 — 에이전트 TRIAGE(billing 러너, B 소유)가 맡을 수 있다."""
    repo.insert_kind(conn, SESSION, TRIAGE_KIND, NOW)
    repo.upsert_agent(conn, {
        "agent_id": TRIAGE, "name": "분류기", "owner_scope": "personal", "connection_type": "local",
        "local_registration_id": "local-triage",
        "capabilities": [{"code": "triage", "scope": {"repository_id": "billing"}}],
    })
    repo.register_session_agent(conn, SESSION, TRIAGE, NOW)
    repo.update_registration(conn, "local-triage", connector_id=cycle["billing"], repository_id="billing",
                             base_commit="a" * 40, verification_profile_ids=[], discovered={}, now=NOW)
    repo.insert_work_item_task(conn, {
        "task_id": "task-triage", "session_id": SESSION, "title": "보고서 분류", "request": "원인을 분류해 주세요.",
        "kind": "triage", "required_capability": {"code": "triage", "scope": {"repository_id": "billing"}},
        "selection_mode": "auto", "chosen_agent_id": None, "run_mode": "auto", "completion_mode": "review",
        "criteria": [], "predecessor_task_id": None, "revision": 1, "target": {},
        "status": "대기", "status_reason": "준비 판정 대기",
    }, NOW)
    return "task-triage"


def work_of(conn, task_id: str):
    return repo.work_item_of_task(conn, task_id)


def set_policy(conn, agent_id: str, policy: str) -> None:
    repo.set_delegation_policy(conn, SESSION, agent_id, policy, member_id=None, now=NOW)


def assign(conn, store, settings, secrets, task_id: str, value: str, member_id: str, *, now: str = LATER) -> None:
    work_actions.assign_work(conn, store, settings, session_id=SESSION,
                             work_item_id=work_of(conn, task_id)["work_item_id"], value=value, member_id=member_id,
                             now=now, secrets=secrets)


def notes(conn, event: str | None = None) -> list[tuple[str, str | None, str]]:
    """(사건, 받는 사람, 중복 키) — 쌓인 순."""
    return [(r["event"], r["recipient_member_id"], r["dedupe_key"]) for r in reversed(repo.list_notifications(conn, SESSION))
            if event is None or r["event"] == event]


def note_content(conn, event: str) -> str:
    (row,) = [r for r in repo.list_notifications(conn, SESSION) if r["event"] == event]
    return row["content"]


def approvals(conn, task_id: str) -> list:
    return repo.list_owner_approvals(conn, task_id)


def respond(conn, settings, secrets, request_id: str, action: str, member_id: str, *, text: str = "",
            response_id: str = "resp-1") -> dict:
    request = repo.get_human_request(conn, SESSION, request_id)
    body = human_api.ResponseBody(response_id=response_id, expected_revision=request["revision"], action=action,
                                  text=text)
    return human_api.respond_to_request(conn, SESSION, request_id, body, LATER, member_id=member_id,
                                        settings=settings, secret_store=secrets)


def heartbeat(conn, connector_id: str, now: str) -> None:
    """러너 heartbeat 와 같은 쓰기 — 연결 프로그램·그 에이전트를 켜짐으로."""
    repo.touch_connector(conn, connector_id, now, None)
    for agent in repo.agents_for_connector(conn, connector_id):
        repo.set_agent_connection(conn, agent["agent_id"], "online", now)


# --- 정책 run ------------------------------------------------------------------------


def test_run_policy_starts_at_once_and_tells_the_owner(conn, store, settings, secrets, members, all_open):
    task_id = import_issue(conn, 1, labels=[])

    assign(conn, store, settings, secrets, task_id, f"agent:{FIX}", members["a"])

    assert len(executions(conn, task_id)) == 1
    assert approvals(conn, task_id) == []
    assert notes(conn) == [("delegated_to_you", members["b"], f"delegated_to_you:{task_id}:{FIX}:shared")]
    assert "김맡김 가 agent-fix 에게 맡김" in note_content(conn, "delegated_to_you")


def test_owner_hands_to_own_agent_runs_without_asking_or_telling(conn, store, settings, secrets, members, all_open):
    set_policy(conn, FIX, "owner_approval")
    task_id = import_issue(conn, 1, labels=[])

    assign(conn, store, settings, secrets, task_id, f"agent:{FIX}", members["b"])

    assert len(executions(conn, task_id)) == 1
    assert approvals(conn, task_id) == [] and notes(conn) == []


# --- 정책 owner_approval ------------------------------------------------------------


@pytest.fixture
def pending(conn, store, settings, secrets, members, all_open) -> tuple[str, str]:
    """A 가 B 의 FIX(`owner_approval`)에게 맡긴 상태 — (task_id, request_id)."""
    set_policy(conn, FIX, "owner_approval")
    task_id = import_issue(conn, 1, labels=[])
    assign(conn, store, settings, secrets, task_id, f"agent:{FIX}", members["a"])
    (request,) = approvals(conn, task_id)
    return task_id, request["request_id"]


def test_owner_approval_waits_for_the_owner(conn, members, pending):
    task_id, request_id = pending
    a, b = members["a"], members["b"]

    assert executions(conn, task_id) == []
    (request,) = approvals(conn, task_id)
    assert (request["code"], request["state"], request["cause_key"]) == (
        "owner_approval", "open", f"owner_approval:{FIX}:{a}:1")
    assert request["question"].splitlines()[0] == "김맡김 가 맡김 · 이소유 승인 대기"
    work = work_of(conn, task_id)
    assert (work["status"], work["status_reason"]) == ("내 차례", "김맡김 가 맡김 · 이소유 승인 대기")
    assert repo.turn_recipients_of(conn, work["work_item_id"]) == (b,)
    rows = repo.list_work_rows(conn, SESSION, closed_since=None)
    assert [r.work_item_id for r in filter_rows(rows, "my_turn", member_id=b)] == [work["work_item_id"]]
    assert filter_rows(rows, "my_turn", member_id=a) == []  # B 의 내 차례에 있고 A 에게는 없다
    # 승인 요청 알림이 정보 알림을 대신한다 — 같은 맡기기로 두 번 보내지 않는다
    assert notes(conn) == [("human_request", b, f"human_request:{request_id}:shared")]
    assert repo.get_task(conn, task_id)["start_pending_at"] == LATER


def test_no_path_starts_the_stage_before_approval(conn, store, settings, secrets, members, pending, worker):
    task_id, _ = pending
    stage = repo.get_task(conn, task_id)

    with pytest.raises(WorkActionError) as caught:  # 옛 `/tasks/{id}/run` 과 같은 착수
        work_actions.start_stage(conn, store, settings, stage, session_id=SESSION, now=LATER, strict=True,
                                 secrets=secrets)
    assert caught.value.status == 409 and "이소유 승인 대기" in caught.value.message
    worker.tick()
    worker.tick()

    assert executions(conn, task_id) == []
    assert len(approvals(conn, task_id)) == 1  # 같은 범위는 같은 요청
    assert repo.get_task(conn, task_id)["status_reason"] == "김맡김 가 맡김 · 이소유 승인 대기"


def test_owner_approves_then_the_worker_starts(conn, settings, secrets, members, pending, worker):
    task_id, request_id = pending

    respond(conn, settings, secrets, request_id, "approve", members["b"])
    assert executions(conn, task_id) == []  # 응답은 실행을 만들지 않는다
    worker.tick()

    (execution,) = executions(conn, task_id)
    assert execution["agent_id"] == FIX
    assert repo.get_task(conn, task_id)["start_pending_at"] is None
    assert work_of(conn, task_id)["status"] == "에이전트 작업 중"


def test_owner_declines_and_the_requester_hears_it(conn, settings, secrets, members, pending, worker):
    task_id, request_id = pending
    a, b = members["a"], members["b"]

    respond(conn, settings, secrets, request_id, "decline", b, text="오늘은 Mac 을 못 씁니다\n다음 주에")

    work = work_of(conn, task_id)
    assert (work["assignee_type"], work["assignee_id"]) == (None, None)
    assigned = [e for e in repo.list_work_item_events(conn, work["work_item_id"]) if e["type"] == "assigned"]
    assert '"by": "%s"' % b in assigned[-1]["data_json"] and '"to": null' in assigned[-1]["data_json"]
    selection = repo.get_selection(conn, task_id)
    assert (selection.status, selection.reason) == ("needs_selection", "이소유 가 거절 — 오늘은 Mac 을 못 씁니다")
    stage = repo.get_task(conn, task_id)
    assert (stage["chosen_agent_id"], stage["start_pending_at"]) == (None, None)
    assert work["status"] != "내 차례"
    assert notes(conn, "delegation_declined") == [("delegation_declined", a, f"delegation_declined:{request_id}:shared")]
    assert "이소유 가 거절 — 오늘은 Mac 을 못 씁니다" in note_content(conn, "delegation_declined")

    worker.tick()  # 워커는 거절된 범위를 다시 묻거나 시작하지 않는다
    assert executions(conn, task_id) == [] and len(approvals(conn, task_id)) == 1


def test_only_the_owner_or_an_admin_decides(conn, settings, secrets, members, pending):
    _, request_id = pending

    with pytest.raises(ApiError) as caught:
        respond(conn, settings, secrets, request_id, "approve", members["a"])
    assert (caught.value.status, caught.value.code) == (403, "forbidden")
    assert repo.get_human_request(conn, SESSION, request_id)["state"] == "open"

    respond(conn, settings, secrets, request_id, "approve", members["admin"])
    assert repo.get_human_request(conn, SESSION, request_id)["state"] == "answered"


def test_decline_note_over_2000_is_refused(conn, settings, secrets, members, pending):
    _, request_id = pending
    with pytest.raises(ApiError) as caught:
        respond(conn, settings, secrets, request_id, "decline", members["b"], text="x" * 2001)
    assert (caught.value.status, caught.value.code, caught.value.field) == (422, "invalid_field", "text")


def test_approval_request_allows_only_approve_and_decline(conn, settings, secrets, members, pending):
    _, request_id = pending
    assert human_api.allowed_actions("owner_approval") == frozenset({"approve", "decline"})
    with pytest.raises(ApiError) as caught:
        respond(conn, settings, secrets, request_id, "resume", members["b"])
    assert (caught.value.status, caught.value.code) == (422, "invalid_field")


def test_changing_the_assignee_withdraws_the_request(conn, store, settings, secrets, members, pending):
    task_id, request_id = pending

    assign(conn, store, settings, secrets, task_id, f"member:{members['a']}", members["a"])

    (request,) = approvals(conn, task_id)
    assert (request["state"], request["action"]) == ("answered", "withdraw")
    work = work_of(conn, task_id)
    assert work["status"] != "내 차례"
    assert repo.turn_recipients_of(conn, work["work_item_id"]) == (members["a"],)
    assert repo.get_task(conn, task_id)["start_pending_at"] is None


def test_approval_facts_follow_the_latest_request(conn, members, pending):
    task_id, _ = pending
    facts = owner_approval.approval_facts(conn, repo.get_task(conn, task_id))
    assert facts[FIX].state == "pending"
    assert facts[FIX].reason == "김맡김 가 맡김 · 이소유 승인 대기"
    assert facts[REVIEW].state == "not_needed"  # 정책 run


def _next_stage(conn, task_id: str, new_task_id: str) -> dict:
    """같은 업무에 이어지는 단계 하나(분류 `triage`, TRIAGE 에이전트 — 같은 소유자 B)."""
    repo.insert_task(conn, {
        "task_id": new_task_id, "session_id": SESSION, "title": "이어지는 단계", "request": "이어서 해 주세요.",
        "kind": "triage", "required_capability": {"code": "triage", "scope": {"repository_id": "billing"}},
        "selection_mode": "auto", "chosen_agent_id": None, "run_mode": "auto", "completion_mode": "review",
        "criteria": [], "predecessor_task_id": None, "revision": 1, "target": {},
        "status": "대기", "status_reason": "준비 판정 대기",
    }, NOW, work_item_id=work_of(conn, task_id)["work_item_id"])
    return repo.get_task(conn, new_task_id)


def test_approval_covers_later_stages_of_the_same_work(conn, settings, secrets, members, pending, triage_task):
    """승인 범위 = 업무 × 소유자 × 맡긴 사람(사용자 결정 2026-10-01) — 같은 업무의 다음 단계는 같은 소유자의
    다른 에이전트라도 다시 묻지 않는다."""
    task_id, request_id = pending
    set_policy(conn, TRIAGE, "owner_approval")
    respond(conn, settings, secrets, request_id, "approve", members["b"])

    stage = _next_stage(conn, task_id, "task-next")
    triage = repo.get_agent(conn, TRIAGE)

    assert owner_approval.approval_fact(conn, stage, triage).state == "approved"
    assert owner_approval.ensure_request(conn, stage, TRIAGE, now=LATER, explicit=False, settings=settings,
                                         secrets=secrets) == "approved"
    assert approvals(conn, "task-next") == []
    # 다른 업무는 새로 묻는다
    assert owner_approval.approval_fact(conn, repo.get_task(conn, triage_task), triage).state == "missing"


def test_pending_approval_on_one_stage_holds_the_next_stage(conn, members, pending, triage_task):
    task_id, _ = pending
    set_policy(conn, TRIAGE, "owner_approval")

    stage = _next_stage(conn, task_id, "task-next")

    assert owner_approval.approval_fact(conn, stage, repo.get_agent(conn, TRIAGE)).state == "pending"


def test_a_new_requester_is_asked_again(conn, store, settings, secrets, members, pending, triage_task):
    task_id, request_id = pending
    set_policy(conn, TRIAGE, "owner_approval")
    respond(conn, settings, secrets, request_id, "approve", members["b"])
    repo.set_work_requester(conn, work_of(conn, task_id)["work_item_id"], members["admin"])

    stage = _next_stage(conn, task_id, "task-next")

    assert owner_approval.approval_fact(conn, stage, repo.get_agent(conn, TRIAGE)).state == "missing"


# --- 순환이 아닌 종류 ----------------------------------------------------------------


def test_non_cycle_owner_approval_waits_then_starts(conn, store, settings, secrets, members, triage_task, worker):
    set_policy(conn, TRIAGE, "owner_approval")

    assign(conn, store, settings, secrets, triage_task, f"agent:{TRIAGE}", members["a"])
    worker.tick()

    assert executions(conn, triage_task) == []
    stage = repo.get_task(conn, triage_task)
    view = views.build_task_view(conn, stage, now=NOW, settings=settings)
    assert (views.status_of(stage, view).label, views.status_of(stage, view).reason) == (
        "대기", "김맡김 가 맡김 · 이소유 승인 대기")
    with pytest.raises(WorkActionError) as caught:
        work_actions.run_task(conn, stage, session_id=SESSION, now=NOW, settings=settings)
    assert (caught.value.status, caught.value.code) == (409, "owner_approval_pending")

    (request,) = approvals(conn, triage_task)
    respond(conn, settings, secrets, request["request_id"], "approve", members["b"])
    worker.tick()

    (execution,) = executions(conn, triage_task)
    assert execution["agent_id"] == TRIAGE


def test_offline_runner_waits_tells_the_owner_once_and_starts_when_back(
    conn, cycle, store, settings, secrets, members, triage_task, worker, clock,
):
    clock.now = OFFLINE
    assign(conn, store, settings, secrets, triage_task, f"agent:{TRIAGE}", members["a"], now=OFFLINE)

    assert executions(conn, triage_task) == []
    stage = repo.get_task(conn, triage_task)
    assert (stage["status"], stage["status_reason"]) == ("대기", "이소유의 러너 꺼짐 · 켜지면 시작")
    work = work_of(conn, triage_task)
    assert (work["status"], work["status_reason"]) == ("대기", "이소유의 러너 꺼짐 · 켜지면 시작")

    worker.tick()
    worker.tick()
    assert executions(conn, triage_task) == []
    assert notes(conn, "runner_offline_waiting") == [
        ("runner_offline_waiting", members["b"], f"runner_offline:{work['work_item_id']}:{TRIAGE}:shared")]
    assert "러너가 꺼져 있어 RUN-1 이 기다림" in note_content(conn, "runner_offline_waiting")

    heartbeat(conn, cycle["billing"], BACK)
    clock.now = BACK
    worker.tick()

    (execution,) = executions(conn, triage_task)
    assert execution["agent_id"] == TRIAGE
    assert repo.get_task(conn, triage_task)["start_pending_at"] is None


# --- 웹 경로 -------------------------------------------------------------------------


def test_old_run_route_and_api_respect_the_owner(app, conn, cycle, all_open):
    task_id = import_issue(conn, 1, labels=[])
    client = log_in_member(TestClient(app), display_name="김맡김")
    a = next(m["member_id"] for m in repo.list_members(conn, SESSION) if m["display_name"] == "김맡김")
    b = repo.add_member(conn, SESSION, display_name="이소유", now=NOW)
    conn.execute("UPDATE connectors SET owner_member_id = ? WHERE connector_id = ?", (b, cycle["billing"]))
    set_policy(conn, FIX, "owner_approval")
    conn.execute("UPDATE tasks SET chosen_agent_id = ?, selection_mode = 'manual' WHERE task_id = ?", (FIX, task_id))

    response = client.post(f"/tasks/{task_id}/run", follow_redirects=False)

    assert response.status_code == 409, response.text
    assert executions(conn, task_id) == []
    (request,) = approvals(conn, task_id)
    assert request["cause_key"] == f"owner_approval:{FIX}:{a}:1"
    answer = client.post(f"/human-requests/{request['request_id']}/responses",
                         json={"response_id": "resp-a", "expected_revision": 1, "action": "approve"})
    assert answer.status_code == 403 and answer.json()["code"] == "forbidden"
