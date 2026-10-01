# ruff: noqa: F811 — test_task_cycle·test_triage_runs 픽스처(cycle·judge·worker)를 가져와 인자로 쓴다
"""자동 판단 — 워커 `_triage_new_work` (phase 19 step 5, ADR-0025, ARCHITECTURE "판단 — phase 19" 자동 판단 대상).

워크스페이스에서 한 번에 1건, 판단 Agent 러너가 비었을 때만, `usage_limit` 실패한 Agent 는 1시간 쉼(워커 메모리),
판단이 실패한 업무에 자동으로 다시 걸지 않음(사용량 한도는 예외). 판단 단계는 맡기기·업무 상태·후속 규칙에서 빠진다.
결과 판정(`_judge_triage`)은 step 6 — 여기서는 판단 끝을 DB 에 직접 적는다(`end_triage`).
"""

import hashlib
import json

from workflow.adapters import repo
from workflow.contracts.v1 import ArtifactMeta
from workflow.domain import team
from workflow.server import views, work_actions
from workflow.server.jira_sync import sync_project
from workflow.server.worker import TRIAGE_USAGE_PAUSE_SECONDS, _plus_seconds

from .test_jira_sync import FakeJira, add_project, jissue
from .test_task_cycle import (  # noqa: F401 — 픽스처
    BASE,
    FIX,
    NOW,
    REG_FIX,
    SESSION,
    _append,
    clock,
    cycle,
    executions,
    import_issue,
    make_worker,
    request_of,
    settings,
    worker,
)
from .test_triage_runs import TRIAGE_KINDS, judge, new_issue  # noqa: F401 — 픽스처


def triage_logs(conn, work_item_id: str | None = None) -> list:
    if work_item_id is None:
        return conn.execute("SELECT * FROM triage_logs ORDER BY created_at, rowid").fetchall()
    return conn.execute("SELECT * FROM triage_logs WHERE work_item_id = ? ORDER BY created_at, rowid",
                        (work_item_id,)).fetchall()


def end_triage(conn, triage_id: str, *, now: str, failed_code: str | None = None) -> None:
    """판단 끝(step 6 `_judge_triage` 자리) — 로그 proposed/failed, 실행 잠금 해제, 단계 마감."""
    log = conn.execute("SELECT * FROM triage_logs WHERE triage_id = ?", (triage_id,)).fetchone()
    if failed_code is None:
        conn.execute("UPDATE triage_logs SET state = 'proposed', result_json = '{}', proceed = 'ready',"
                     " confidence = 0.9, finished_at = ?, updated_at = ? WHERE triage_id = ?", (now, now, triage_id))
    else:
        conn.execute("UPDATE triage_logs SET state = 'failed', failed_code = ?, failed_message = '실패',"
                     " finished_at = ?, updated_at = ? WHERE triage_id = ?", (failed_code, now, now, triage_id))
    conn.execute("UPDATE executions SET released_at = ? WHERE execution_id = ?", (now, log["execution_id"]))
    conn.execute("UPDATE tasks SET finished_at = ?, status = ? WHERE task_id = ?",
                 (now, "완료" if failed_code is None else "실패", log["task_id"]))
    conn.commit()


def touch(conn, cycle, now: str) -> None:
    """러너 신호 — 시각을 옮긴 뒤에도 켜짐."""
    for connector in (cycle["billing"], cycle["shop"]):
        repo.touch_connector(conn, connector, now, None)
        for agent in repo.agents_for_connector(conn, connector):
            repo.set_agent_connection(conn, agent["agent_id"], "online", now)


# --- 1건 상한 · 러너 빔 --------------------------------------------------------------


def test_two_new_works_are_triaged_one_per_tick(conn, worker, judge):
    a = new_issue(conn, 1)
    b = new_issue(conn, 2)

    report = worker.tick()

    assert report.triage_started == 1
    (log,) = triage_logs(conn)
    assert (log["work_item_id"], log["trigger"], log["requested_by_member_id"], log["state"]) == (
        a, "auto", None, "running")
    assert worker.tick().triage_started == 0  # 판단이 도는 동안은 0건

    end_triage(conn, log["triage_id"], now=NOW)
    assert worker.tick().triage_started == 1
    assert [r["work_item_id"] for r in triage_logs(conn)] == [a, b]
    assert worker.tick().triage_started == 0  # 대상 없음 — 판단 로그 행이 있는 업무는 다시 걸지 않는다


def test_triage_waits_while_the_runner_runs_a_fix(conn, worker, judge):
    assigned = import_issue(conn, 1)  # 담당 연결 → FIX — 같은 tick 에 수정이 러너를 먼저 차지한다
    new_issue(conn, 2)

    report = worker.tick()

    assert (report.tasks_started, report.triage_started) == (1, 0)
    assert len(executions(conn, assigned)) == 1
    assert triage_logs(conn) == []


def test_no_triage_agent_means_no_auto_triage(conn, worker, cycle):
    repo.record_supported_kinds(conn, cycle["billing"], TRIAGE_KINDS)
    new_issue(conn, 1)
    assert worker.tick().triage_started == 0
    assert triage_logs(conn) == []


def test_old_runner_without_triage_is_skipped(conn, worker, judge):
    repo.record_supported_kinds(conn, judge["billing"], ["code_change", "bug_fix", "code_review"])
    new_issue(conn, 1)
    assert worker.tick().triage_started == 0


def test_closed_origin_is_skipped(conn, worker, judge):
    new_issue(conn, 1)
    conn.execute("UPDATE source_issues SET state = 'closed'")
    conn.commit()
    assert worker.tick().triage_started == 0


def test_owner_approval_policy_skips_auto_triage(conn, worker, judge):
    conn.execute("UPDATE agents SET delegation_policy = 'owner_approval' WHERE agent_id = ?", (FIX,))
    conn.commit()
    new_issue(conn, 1)
    assert worker.tick().triage_started == 0
    assert conn.execute("SELECT COUNT(*) FROM human_requests").fetchone()[0] == 0  # 승인 요청을 쌓지 않는다


# --- 실패 · 사용량 한도 --------------------------------------------------------------


def test_usage_limit_pauses_the_agent_for_an_hour_then_retries_the_same_work(conn, worker, judge, clock):
    a = new_issue(conn, 1)
    worker.tick()
    (log,) = triage_logs(conn)
    end_triage(conn, log["triage_id"], now=NOW, failed_code="usage_limit")
    # step 6 `_judge_triage` 가 하는 일 — 그 Agent 를 한 시간 쉬게 한다(워커 메모리)
    worker._triage_paused_until[FIX] = _plus_seconds(NOW, TRIAGE_USAGE_PAUSE_SECONDS)
    new_issue(conn, 2)

    clock.now = _plus_seconds(NOW, TRIAGE_USAGE_PAUSE_SECONDS - 60)
    touch(conn, judge, clock.now)
    assert worker.tick().triage_started == 0  # 쉬는 동안은 그 Agent 의 판단 0건

    clock.now = _plus_seconds(NOW, TRIAGE_USAGE_PAUSE_SECONDS)
    touch(conn, judge, clock.now)
    assert worker.tick().triage_started == 1
    rows = triage_logs(conn, a)
    assert [(r["state"], r["failed_code"]) for r in rows] == [("superseded", "usage_limit"), ("running", None)]


def test_other_failure_is_not_retried_automatically(conn, worker, judge):
    a = new_issue(conn, 1)
    worker.tick()
    (log,) = triage_logs(conn)
    end_triage(conn, log["triage_id"], now=NOW, failed_code="readonly_violation")

    assert worker.tick().triage_started == 0
    assert repo.auto_triage_works(conn, SESSION) == []
    assert [r["state"] for r in triage_logs(conn, a)] == ["failed"]


# --- 판단 단계 빼기 ------------------------------------------------------------------


def test_work_under_triage_is_new_and_triaging_not_agent_working(conn, worker, judge):
    a = new_issue(conn, 1)
    worker.tick()
    row = repo.get_work_item(conn, SESSION, a)
    assert (row["status"], row["status_reason"], row["assignee_type"]) == ("새로 들어옴", "판단 중", None)
    # 러너가 받아 실행 중이어도 판단 단계는 업무 상태를 바꾸지 않는다
    (log,) = triage_logs(conn)
    _append(conn, log["execution_id"], 1, "accepted", {})
    _append(conn, log["execution_id"], 2, "started", {"runtime_ref": "pid:9"})
    worker.tick()
    row = repo.get_work_item(conn, SESSION, a)
    assert (row["status"], row["status_reason"]) == ("새로 들어옴", "판단 중")


def test_assigning_during_triage_hands_the_original_stage(conn, store, settings, worker, judge, clock):
    a = new_issue(conn, 1)
    worker.tick()
    (log,) = triage_logs(conn)
    (fix_stage,) = [t for t in repo.list_work_item_tasks(conn, a) if t["kind"] == "bug_fix"]

    assert work_actions.open_stage(conn, a)["task_id"] == fix_stage["task_id"]
    assert [r["agent_id"] for r in work_actions.agent_candidates(conn, SESSION, a)] == [FIX]
    panel = views.work_panel_context(conn, SESSION, a, member_id="mem-x", allowed=frozenset({team.DELEGATE}),
                                     now=NOW, settings=settings)
    assert panel["can_edit"] and panel["can_start_direct"]  # 판단 실행은 에이전트 실행으로 세지 않는다
    assert [c["agent_id"] for c in panel["agents"]] == [FIX]
    admin = repo.ensure_first_admin(conn, SESSION, now=NOW)
    work_actions.assign_work(conn, store, settings, session_id=SESSION, work_item_id=a, value=f"agent:{FIX}",
                             member_id=admin, now=NOW)

    assert repo.get_selection(conn, fix_stage["task_id"]).selected_agent_id == FIX
    assert repo.get_task(conn, fix_stage["task_id"])["chosen_agent_id"] == FIX
    row = repo.get_work_item(conn, SESSION, a)
    assert (row["assignee_type"], row["assignee_id"]) == ("agent", FIX)
    assert repo.get_task(conn, log["task_id"])["finished_at"] is None  # 판단 단계는 그대로 돈다


def test_triage_stage_creates_no_successor(conn, store, worker, judge):
    a = new_issue(conn, 1)
    worker.tick()
    (log,) = triage_logs(conn)
    request = request_of(repo.get_execution(conn, log["execution_id"]))
    body = {
        "contract_version": 1, "execution_id": log["execution_id"], "task_id": log["task_id"],
        "inspected_commit": BASE, "proceed": "ready", "confidence": 0.9, "proposed_kind": "bug_fix",
        "assignee": {"type": "agent", "id": FIX}, "predecessors": [],
        "reasons": [{"criterion": "clarity", "note": "재현 절차가 있다"}], "missing_information": [],
    }
    data = json.dumps(body).encode()
    meta = ArtifactMeta(contract_version=1, kind="triage_result", name="triage.json", content_type="application/json",
                        sha256=hashlib.sha256(data).hexdigest(), size=len(data))
    created, _ = repo.store_artifact(conn, store, execution_id=log["execution_id"], session_id=SESSION, meta=meta,
                                     data=data, now=NOW)
    _append(conn, log["execution_id"], 1, "accepted", {})
    _append(conn, log["execution_id"], 2, "started", {"runtime_ref": "pid:9"})
    _append(conn, log["execution_id"], 3, "result_ready", {"result_artifact_id": created.artifact_id})
    assert request.target.base_commit == BASE

    for _ in range(2):
        report = worker.tick()
        assert (report.successors_created, report.followup_tasks_created, report.followups_started) == (0, 0, 0)
    assert sorted(t["kind"] for t in repo.list_work_item_tasks(conn, a)) == ["bug_fix", "triage"]
    assert repo.successors_of(conn, log["task_id"]) == []
    assert repo.get_work_item(conn, SESSION, a)["assignee_type"] is None


# --- Jira ---------------------------------------------------------------------------


def test_jira_work_uses_the_triage_agent_of_the_linked_repository(conn, worker, judge):
    project = add_project(conn)
    jira = FakeJira()
    jira.put(jissue(1))
    sync_project(conn, jira, project, NOW)
    (jira_work,) = conn.execute("SELECT * FROM work_items WHERE source_type = 'jira'").fetchall()

    assert worker.tick().triage_started == 1
    (log,) = triage_logs(conn)
    assert (log["work_item_id"], log["agent_id"]) == (jira_work["work_item_id"], FIX)
    request = request_of(repo.get_execution(conn, log["execution_id"]))
    assert (request.target.local_registration_id, request.target.base_commit) == (REG_FIX, BASE)
    assert request.request.startswith(f"# 판단: RUN-{jira_work['key_number']} 쿠폰 오류 1\n원본: SHOP-1\n")


def test_jira_project_without_a_linked_triage_agent_is_skipped(conn, worker, cycle):
    repo.record_supported_kinds(conn, cycle["billing"], TRIAGE_KINDS)
    project = add_project(conn)
    jira = FakeJira()
    jira.put(jissue(1))
    sync_project(conn, jira, project, NOW)
    assert worker.tick().triage_started == 0


# --- 순환 회귀 — 판단 Agent 가 있어도 담당 있는 업무는 그대로 ------------------------------


def test_assigned_github_issue_still_runs_the_cycle(conn, worker, judge):
    task_id = import_issue(conn, 1)
    report = worker.tick()
    assert report.tasks_started == 1
    (execution,) = executions(conn, task_id)
    assert (execution["kind"], execution["agent_id"]) == ("bug_fix", FIX)
    assert triage_logs(conn) == []
    assert repo.claim_execution(conn, judge["billing"], NOW)["execution_id"] == execution["execution_id"]
