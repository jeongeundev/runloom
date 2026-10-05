# ruff: noqa: F811 — test_task_cycle 픽스처(cycle·settings)를 가져와 인자로 쓴다
"""판단 경로·시작 — phase 19 step 5 (ADR-0025, ARCHITECTURE "판단 — phase 19" 판단 경로·시작 조건·요청문·후보·근거).

`triage_route` 이유 표, `request_triage` 가 한 트랜잭션에 만드는 판단 단계·선택·실행·판단 로그, 후보·근거 재료(repo).
실제 연결 프로그램·GitHub·모델을 부르지 않는다 — 원본 이슈는 `repo.upsert_source_issue` 로 넣는다(test_task_cycle 과 같다).
"""

import hashlib
import json

import pytest

from workflow.adapters import repo
from workflow.contracts.v1 import Capability, ExecutionRequest, KindSpec, TriageCandidates, TriageTarget
from workflow.domain.triage import PastWork
from workflow.server import triage_runs
from workflow.server.triage_runs import REASONS

from .test_task_cycle import (  # noqa: F401 — 픽스처
    ALL_KINDS,
    BASE,
    FIX,
    FIX_SHOP,
    NOW,
    REG_FIX,
    REVIEW,
    SESSION,
    SOURCE,
    clock,
    config,
    cycle,
    direct_shop_task,
    import_issue,
    make_worker,
    settings,
    worker,
)

TRIAGE_KINDS = [*ALL_KINDS, "triage"]
LATER = "2026-10-06T12:00:30Z"  # 러너 마지막 신호(NOW) 30초 뒤 — 켜짐
OFFLINE = "2026-10-08T12:00:00Z"


def give_triage(conn, agent_id: str, repository: str) -> None:
    """Agent 에 `code.triage` 능력을 더한다 — v15 마이그레이션·새 러너 등록과 같은 모양."""
    caps = json.loads(repo.get_agent(conn, agent_id)["capabilities_json"])
    caps.append({"code": "code.triage", "scope": {"repository_id": repository}})
    conn.execute("UPDATE agents SET capabilities_json = ? WHERE agent_id = ?", (json.dumps(caps), agent_id))
    conn.commit()


@pytest.fixture
def judge(conn, cycle) -> dict:
    """acme/billing 소스의 판단 Agent = FIX(같은 billing 러너), 러너가 triage 를 선언."""
    give_triage(conn, FIX, "billing")
    repo.record_supported_kinds(conn, cycle["billing"], TRIAGE_KINDS)
    repo.save_github_source(conn, SESSION, config(review_agent_id=REVIEW, triage_agent_id=FIX), NOW)
    return cycle


def new_issue(conn, number: int, **overrides) -> str:
    """담당 없는 새 GitHub 업무 하나 — 반환은 업무 ID."""
    task_id = import_issue(conn, number, assignee_ids=[], assignee_logins=[], **overrides)
    return repo.work_item_of_task(conn, task_id)["work_item_id"]


def work(conn, work_item_id: str):
    return repo.get_work_item(conn, SESSION, work_item_id)


def route(conn, settings, work_item_id: str, *, now: str = LATER):
    return triage_runs.triage_route(conn, work(conn, work_item_id), now=now, settings=settings)


def request(conn, settings, work_item_id: str, *, trigger="auto", member_id=None, now: str = LATER):
    return triage_runs.request_triage(conn, settings, session_id=SESSION, work_item_id=work_item_id,
                                      trigger=trigger, member_id=member_id, now=now)


def logs(conn, work_item_id: str) -> list:
    return conn.execute("SELECT * FROM triage_logs WHERE work_item_id = ? ORDER BY created_at, rowid",
                        (work_item_id,)).fetchall()


@pytest.fixture
def admin(conn, cycle) -> str:
    return repo.ensure_first_admin(conn, SESSION, now=NOW)


# --- 판단 경로 · 이유 ----------------------------------------------------------------


def test_route_of_a_new_unassigned_github_work_is_startable(conn, settings, judge):
    wid = new_issue(conn, 1)
    found = route(conn, settings, wid)
    assert found.reason is None
    assert (found.agent_id, found.repository_id, found.config.source_id) == (FIX, "billing", SOURCE)
    assert found.stage["kind"] == "bug_fix"


def test_reasons_are_the_architecture_texts():
    assert REASONS == {
        "not_new": "담당 없는 새 업무만 판단합니다",
        "no_repository": "연결 저장소가 없는 업무는 판단하지 않습니다",
        "no_stage": "맡길 단계가 없습니다",
        "origin_closed": "원본이 닫힌 업무는 판단하지 않습니다",
        "no_agent": "판단 에이전트 없음 — 저장소 카드에서 고르세요",
        "no_runner_repository": "이 저장소를 등록한 러너 없음",
        "agent_missing": "판단 에이전트가 이 워크스페이스에 없습니다",
        "no_capability": "판단 에이전트에 이 저장소 판단 능력(code.triage) 없음",
        "owner_approval": "판단 에이전트의 맡기기 정책이 승인 필요 — '바로 실행'으로 바꾸세요",
        "offline": "판단 에이전트의 러너 꺼짐",
        "runner_outdated": "러너 업데이트 필요 — 판단 미지원",
        "runner_no_next_step": "러너 업데이트 필요 — 결과 뒤 판단 미지원",
        "no_base_commit": "러너의 기준 커밋 보고 전 — 잠시 뒤 다시",
        "running": "판단 중",
    }


def test_assigned_work_is_not_triaged(conn, settings, judge, admin):
    wid = new_issue(conn, 1)
    repo.assign_work_item(conn, SESSION, wid, assignee_type="member", assignee_id=admin, now=NOW)
    assert route(conn, settings, wid).reason == REASONS["not_new"]


def test_direct_work_without_a_repository_is_not_triaged(conn, settings, judge):
    task_id = direct_shop_task(conn)
    conn.execute("UPDATE work_items SET assignee_type = NULL, assignee_id = NULL")
    conn.execute("UPDATE tasks SET chosen_agent_id = NULL WHERE task_id = ?", (task_id,))
    conn.commit()
    wid = repo.work_item_of_task(conn, task_id)["work_item_id"]
    repo.refresh_work_status(conn, wid, now=NOW)
    conn.commit()
    assert route(conn, settings, wid).reason == REASONS["no_repository"]


def test_closed_origin_is_not_triaged(conn, settings, judge):
    wid = new_issue(conn, 1)
    conn.execute("UPDATE source_issues SET state = 'closed'")
    conn.commit()
    assert route(conn, settings, wid).reason == REASONS["origin_closed"]


def test_no_triage_agent_on_the_source(conn, settings, cycle):
    wid = new_issue(conn, 1)
    found = route(conn, settings, wid)
    assert (found.reason, found.agent_id) == (REASONS["no_agent"], None)


def test_triage_agent_without_the_capability(conn, settings, cycle):
    repo.record_supported_kinds(conn, cycle["billing"], TRIAGE_KINDS)
    repo.save_github_source(conn, SESSION, config(review_agent_id=REVIEW, triage_agent_id=FIX), NOW)
    assert route(conn, settings, new_issue(conn, 1)).reason == REASONS["no_capability"]


def test_triage_agent_of_another_repository_lacks_the_capability(conn, settings, judge):
    give_triage(conn, FIX_SHOP, "shop")
    repo.save_github_source(conn, SESSION, config(review_agent_id=REVIEW, triage_agent_id=FIX_SHOP), NOW)
    assert route(conn, settings, new_issue(conn, 1)).reason == REASONS["no_capability"]


def test_unknown_triage_agent(conn, settings, judge):
    repo.save_github_source(conn, SESSION, config(review_agent_id=REVIEW, triage_agent_id="agent-nope"), NOW)
    assert route(conn, settings, new_issue(conn, 1)).reason == REASONS["agent_missing"]


def test_owner_approval_policy_is_refused(conn, settings, judge):
    conn.execute("UPDATE agents SET delegation_policy = 'owner_approval' WHERE agent_id = ?", (FIX,))
    conn.commit()
    assert route(conn, settings, new_issue(conn, 1)).reason == REASONS["owner_approval"]


def test_offline_runner(conn, settings, judge):
    assert route(conn, settings, new_issue(conn, 1), now=OFFLINE).reason == REASONS["offline"]


@pytest.mark.parametrize("kinds", [ALL_KINDS, None])
def test_old_runner_without_triage(conn, settings, judge, kinds):
    repo.record_supported_kinds(conn, judge["billing"], kinds)
    assert route(conn, settings, new_issue(conn, 1)).reason == REASONS["runner_outdated"]


def test_base_commit_not_reported(conn, settings, judge):
    conn.execute("UPDATE agents SET base_commit = NULL WHERE agent_id = ?", (FIX,))
    conn.commit()
    assert route(conn, settings, new_issue(conn, 1)).reason == REASONS["no_base_commit"]


# --- 판단 시작 -----------------------------------------------------------------------


def test_request_triage_creates_the_stage_selection_execution_and_log_in_one_go(conn, settings, judge, admin):
    wid = new_issue(conn, 1)
    first_stage = repo.list_work_item_tasks(conn, wid)[0]

    started = request(conn, settings, wid, trigger="manual", member_id=admin)

    assert started.started and started.reason is None and started.triage_id.startswith("trg-")
    stages = repo.list_work_item_tasks(conn, wid)
    assert [s["kind"] for s in stages] == ["bug_fix", "triage"]
    stage = stages[1]
    assert (stage["title"], stage["kind"], stage["selection_mode"], stage["chosen_agent_id"]) == (
        "판단", "triage", "manual", FIX)
    assert (stage["run_mode"], stage["completion_mode"], stage["revision"]) == ("auto", "review", 1)
    assert (stage["status"], stage["status_reason"], stage["work_item_id"]) == ("실행 요청됨", "판단 접수 대기", wid)
    assert json.loads(stage["required_capability_json"]) == {"code": "code.triage",
                                                             "scope": {"repository_id": "billing"}}
    assert json.loads(stage["criteria_json"]) == []
    assert stage["task_id"].startswith("task-") and len(stage["task_id"]) == len("task-") + 12
    selection = repo.get_selection(conn, stage["task_id"])
    assert (selection.mode, selection.status, selection.selected_agent_id) == ("manual", "selected", FIX)

    (execution,) = repo.list_executions(conn, stage["task_id"])
    assert (execution["status"], execution["agent_id"], execution["kind"], execution["attempt_no"]) == (
        "queued", FIX, "triage", 1)
    assert execution["assigned_connector_id"] == judge["billing"]
    assert execution["start_key"].startswith("req:")
    req = ExecutionRequest.model_validate_json(execution["request_json"])
    assert req.target == TriageTarget(local_registration_id=REG_FIX, base_commit=BASE)
    assert (req.input_artifact_ids, req.task_revision, req.work_key) == ([], 1, None)
    assert req.kind_spec.output_kind == "triage_result"
    assert req.request.startswith("# 판단: RUN-1 버그 1\n")
    assert req.request == stage["request"]
    assert "## 판단 기준 (v1)" in req.request and "재현 절차 1" in req.request

    (log,) = logs(conn, wid)
    assert log["triage_id"] == started.triage_id
    assert (log["state"], log["trigger"], log["requested_by_member_id"], log["agent_id"]) == (
        "running", "manual", admin, FIX)
    assert (log["task_id"], log["execution_id"], log["work_revision"], log["criteria_version"]) == (
        stage["task_id"], execution["execution_id"], 1, 1)
    assert log["input_sha256"] == hashlib.sha256(req.request.encode()).hexdigest()
    candidates = TriageCandidates.model_validate_json(log["candidates_json"])
    assert candidates.current_kind == "bug_fix"
    assert [a.agent_id for a in candidates.agents] == [FIX]
    assert candidates.agents[0].kinds == ["bug_fix"]

    # 업무: 담당 그대로 없음, 상태 = 새로 들어옴 · 판단 중. 맡길 단계는 원래 단계
    row = work(conn, wid)
    assert (row["assignee_type"], row["status"], row["status_reason"]) == (None, "새로 들어옴", "판단 중")
    assert repo.open_stage(conn, wid)["task_id"] == first_stage["task_id"]
    assert repo.is_triage_task(conn, stage["task_id"]) and not repo.is_triage_task(conn, first_stage["task_id"])
    assert repo.has_running_triage(conn, SESSION)


def test_running_triage_is_not_started_twice(conn, settings, judge):
    wid = new_issue(conn, 1)
    assert request(conn, settings, wid).started
    again = request(conn, settings, wid)
    assert (again.started, again.triage_id, again.reason) == (False, None, REASONS["running"])
    assert len(logs(conn, wid)) == 1
    assert len(repo.list_work_item_tasks(conn, wid)) == 2


def test_start_triage_refuses_a_second_running_row(conn, settings, judge, monkeypatch):
    """경합 — 이유 검사 뒤 다른 쪽이 먼저 시작했으면 repo 가 막는다(`TriageRunning`)."""
    wid = new_issue(conn, 1)
    before = route(conn, settings, wid)
    assert request(conn, settings, wid).started
    monkeypatch.setattr(triage_runs, "triage_route", lambda *args, **kwargs: before)  # 시작 전 경로를 본 쪽

    again = request(conn, settings, wid)

    assert (again.started, again.reason) == (False, REASONS["running"])
    assert len(logs(conn, wid)) == 1 and len(repo.list_work_item_tasks(conn, wid)) == 2


def test_request_with_a_reason_creates_nothing(conn, settings, cycle):
    wid = new_issue(conn, 1)
    result = request(conn, settings, wid)
    assert (result.started, result.reason) == (False, REASONS["no_agent"])
    assert logs(conn, wid) == [] and len(repo.list_work_item_tasks(conn, wid)) == 1


def test_new_triage_supersedes_the_unhandled_previous_row(conn, settings, judge):
    wid = new_issue(conn, 1)
    first = request(conn, settings, wid).triage_id
    conn.execute("UPDATE triage_logs SET state = 'failed', failed_code = 'timeout' WHERE triage_id = ?", (first,))
    conn.execute("UPDATE executions SET released_at = ? ", (LATER,))
    conn.commit()
    second = request(conn, settings, wid).triage_id
    assert [(r["triage_id"], r["state"]) for r in logs(conn, wid)] == [(first, "superseded"), (second, "running")]


def test_triage_stage_does_not_fill_the_work_assignee_nor_count_in_metrics(conn, settings, store, judge):
    wid = new_issue(conn, 1)
    request(conn, settings, wid)
    assert work(conn, wid)["assignee_type"] is None
    assert [e["type"] for e in repo.list_work_item_events(conn, wid) if e["type"] == "assigned"] == []
    facts = repo.list_metric_facts(conn, SESSION, store=store)
    assert [t.kind for t in facts.tasks] == ["bug_fix"]
    assert facts.executions == ()


def test_auto_request_has_no_requester(conn, settings, judge):
    wid = new_issue(conn, 1)
    request(conn, settings, wid)
    (log,) = logs(conn, wid)
    assert (log["trigger"], log["requested_by_member_id"]) == ("auto", None)


# --- 후보·근거 재료 ------------------------------------------------------------------


def test_candidates_list_members_agents_and_open_works_of_the_same_repository(conn, settings, judge, admin):
    a = new_issue(conn, 1)
    b = new_issue(conn, 2)
    repo.assign_work_item(conn, SESSION, b, assignee_type="member", assignee_id=admin, now=NOW)
    found = route(conn, settings, a)
    candidates = triage_runs.build_candidates(conn, work(conn, a), found, now=LATER, settings=settings)
    assert [(m.member_id, m.open_work) for m in candidates.members] == [(admin, 1)]
    (agent,) = candidates.agents
    assert (agent.agent_id, agent.online, agent.open_work, agent.kinds) == (FIX, True, 0, ["bug_fix"])
    assert [k.kind for k in candidates.kinds] == ["bug_fix"]
    assert [p.work_key for p in candidates.predecessors] == ["RUN-2"]  # 자신 제외, 같은 저장소의 끝나지 않은 업무



def test_added_capability_makes_the_agent_a_triage_candidate_for_a_user_kind(conn, settings, judge):
    """phase 23 step 4 — 붙인 능력으로 사용자 정의 종류의 판단 후보 Agent 가 된다(코드 변경 없음)."""
    audit = KindSpec(kind="audit", label="감사", capability_code="audit", scope_key="repository_id", input_kinds=[],
                     output_kind="generic_result", outcomes=["done"], instructions="", builtin=False)
    repo.insert_kind(conn, SESSION, audit, NOW)
    a = new_issue(conn, 1)

    def agents():
        found = route(conn, settings, a)
        candidates = triage_runs.build_candidates(conn, work(conn, a), found, now=LATER, settings=settings)
        assert [k.kind for k in candidates.kinds] == ["bug_fix", "audit"]
        return [(agent.agent_id, agent.kinds) for agent in candidates.agents]

    assert agents() == [(FIX, ["bug_fix"])]
    repo.add_agent_capability(conn, agent_id=REVIEW, capability=Capability(code="audit",
                                                                           scope={"repository_id": "billing"}))
    repo.add_agent_capability(conn, agent_id=FIX, capability=Capability(code="audit",
                                                                        scope={"repository_id": "billing"}))
    assert agents() == [(FIX, ["bug_fix", "audit"]), (REVIEW, ["audit"])]

def test_open_work_counts_and_runner_idle(conn, judge, admin):
    a = new_issue(conn, 1)
    repo.assign_work_item(conn, SESSION, a, assignee_type="agent", assignee_id=FIX, now=NOW)
    assert repo.open_work_counts(conn, SESSION) == {("agent", FIX): 1}
    assert repo.runner_idle(conn, judge["billing"])
    conn.execute("UPDATE connectors SET current_execution_id = 'exec-x' WHERE connector_id = ?", (judge["billing"],))
    conn.commit()
    assert not repo.runner_idle(conn, judge["billing"])


def test_triage_history_counts_runs_of_the_same_kind_without_verify_only(conn, judge):
    wid = new_issue(conn, 1)
    conn.execute("UPDATE work_items SET status = '완료', closed_at = ? WHERE work_item_id = ?",
                 ("2026-10-07T00:00:00Z", wid))
    conn.commit()
    (past,) = repo.triage_history(conn, SESSION, ["bug_fix"], limit=20)
    assert past == PastWork(kind="bug_fix", status="완료", created_at=work(conn, wid)["created_at"],
                            closed_at="2026-10-07T00:00:00Z", attempts=0)
    assert repo.triage_history(conn, SESSION, ["code_review"], limit=20) == []


def test_current_criteria_is_the_latest_version(conn, judge):
    row = repo.current_triage_criteria(conn, SESSION)
    assert row["version"] == 1 and row["body"].strip()
