# ruff: noqa: F811 — test_task_cycle·test_triage_runs·test_web_triage 픽스처(cycle·judge·worker·admin)를 가져와 인자로 쓴다
"""자동 맡기기 — 워커 `_autostart_triaged` (phase 19 step 9, ADR-0025 결정 13, ARCHITECTURE "판단 — phase 19" 자동 시작).

켜진 종류·`ready`·제안 담당 에이전트·확신도 ≥ 기준값인 처리 없는 최신 제안을 [제안대로 맡기기]와 같은 함수
(`work_actions.accept_triage(member_id=None)`)로 맡긴다 — 맡긴 사람 없음, 판단 로그 `auto_started`, 타임라인
`자동 시작 · 판단 v<n>`. 소유자 승인·꺼진 러너 대기·실패 뒤 내 차례는 기존 경로 그대로. 자격 건수(사람 처리 20건)는
`repo.triage_handled_counts` 를 바꿔 끼우고, 설정 행은 직접 넣는다(설정 화면 검증은 test_web_triage_settings).
"""

import inspect
import json
import re

import pytest
from fastapi.testclient import TestClient

from workflow.adapters import repo
from workflow.server.worker import TickReport, Worker

from .conftest import log_in_member
from .test_task_cycle import (  # noqa: F401 — 픽스처
    FIX,
    NOW,
    REVIEW,
    SESSION,
    clock,
    config,
    cycle,
    executions,
    make_worker,
    settings,
    worker,
    seen_at,
)
from .test_triage_dispatch import triage_logs
from .test_triage_judge import fail, log_row, result_body, submit
from .test_triage_runs import OFFLINE, judge, new_issue  # noqa: F401 — 픽스처
from .test_web_triage import admin, admin_id, member_named, panel, section  # noqa: F401 — 픽스처


@pytest.fixture
def source(conn, judge) -> None:
    """지시 전 이슈가 생기는 all_open 소스 — 맡기면 운영자 지시로 곧 착수한다(test_web_triage 와 같다)."""
    repo.save_github_source(conn, SESSION, config(review_agent_id=REVIEW, triage_agent_id=FIX, intake="all_open",
                                                  label_filter=[], trigger_label="runloom"), NOW)


@pytest.fixture
def eligible(monkeypatch) -> None:
    """버그 수정 제안을 사람이 20건 처리했다 — 자동 시작 자격."""
    monkeypatch.setattr(repo, "triage_handled_counts", lambda conn, session_id: {"bug_fix": 20})


def autostart(conn, *, enabled: bool = True, threshold: float = 0.8, version: int = 1, kind: str = "bug_fix") -> None:
    conn.execute("INSERT INTO triage_autostart (session_id, kind, version, enabled, threshold, created_by_member_id,"
                 " created_at) VALUES (?, ?, ?, ?, ?, NULL, ?)", (SESSION, kind, version, int(enabled), threshold, NOW))
    conn.commit()


def proposed(conn, store, worker, wid: str, **overrides):
    """자동 판단 시작(tick) → 결과 제출. 판정·자동 맡기기는 다음 tick 이 한다. 반환은 판단 로그 행(running)."""
    assert worker.tick().triage_started == 1
    (log,) = triage_logs(conn, wid)
    submit(conn, store, log, result_body(log, **overrides))
    return log


def fix_stage(conn, wid: str):
    (stage,) = [t for t in repo.list_work_item_tasks(conn, wid) if t["kind"] == "bug_fix"]
    return stage


def work(conn, wid: str):
    return repo.get_work_item(conn, SESSION, wid)


def assigned_events(conn, wid: str) -> list[dict]:
    return [json.loads(r["data_json"]) for r in conn.execute(
        "SELECT data_json FROM work_item_events WHERE work_item_id = ? AND type = 'assigned' ORDER BY rowid", (wid,))]


def assert_not_started(conn, wid: str, triage_id: str) -> None:
    assert (work(conn, wid)["assignee_type"], work(conn, wid)["assignee_id"]) == (None, None)
    assert executions(conn, fix_stage(conn, wid)["task_id"]) == []
    assert log_row(conn, triage_id)["handling"] is None


# --- 맡긴다 -----------------------------------------------------------------------------


def test_enabled_ready_agent_proposal_above_threshold_is_handed_and_started(admin, conn, store, worker, source,
                                                                           eligible):
    autostart(conn, threshold=0.8)
    wid = new_issue(conn, 1, labels=[])
    log = proposed(conn, store, worker, wid, confidence=0.86)

    report = worker.tick()

    assert (report.triage_judged, report.triage_autostarted) == (1, 1)
    assert (work(conn, wid)["assignee_type"], work(conn, wid)["assignee_id"]) == ("agent", FIX)
    assert work(conn, wid)["requested_by_member_id"] is None  # 맡긴 사람 없음
    assert len(executions(conn, fix_stage(conn, wid)["task_id"])) == 1  # 같은 tick 에 착수
    row = log_row(conn, log["triage_id"])
    assert (row["handling"], row["handled_by_member_id"], row["final_assignee_type"], row["final_assignee_id"],
            row["final_kind"], row["handled_at"]) == ("auto_started", None, "agent", FIX, "bug_fix", NOW)
    (event,) = assigned_events(conn, wid)
    assert event["triage"] == {"triage_id": log["triage_id"], "criteria_version": 1}
    assert event["by"] is None
    text = panel(admin)
    assert "자동 시작 · 판단 v1" in section(text)  # 판단 절 처리됨 한 줄
    assert "자동 시작 · 판단 v1" in section(text, "timeline")  # 타임라인 assigned 줄


def test_autostart_runs_after_judging_and_before_the_cycle_advances():
    calls = re.findall(r"self\.(_\w+)\(conn, report\)", inspect.getsource(Worker.tick))
    i = calls.index("_autostart_triaged")
    assert calls[i - 1:i + 2] == ["_judge_triage", "_autostart_triaged", "_advance_cycle"]


def test_two_ticks_and_a_restarted_worker_hand_only_once(admin, conn, store, worker, make_worker, source, eligible):
    autostart(conn)
    wid = new_issue(conn, 1, labels=[])
    log = proposed(conn, store, worker, wid)
    assert worker.tick().triage_autostarted == 1

    assert worker.tick().triage_autostarted == 0
    assert make_worker().tick().triage_autostarted == 0
    assert len(executions(conn, fix_stage(conn, wid)["task_id"])) == 1
    assert len(assigned_events(conn, wid)) == 1
    assert log_row(conn, log["triage_id"])["handling"] == "auto_started"


# --- 맡기지 않는다 ------------------------------------------------------------------------


def test_confidence_below_threshold_is_not_handed(admin, conn, store, worker, source, eligible):
    autostart(conn, threshold=0.9)
    wid = new_issue(conn, 1, labels=[])
    log = proposed(conn, store, worker, wid, confidence=0.86)
    assert worker.tick().triage_autostarted == 0
    assert_not_started(conn, wid, log["triage_id"])


def test_needs_check_is_not_handed(admin, conn, store, worker, source, eligible):
    autostart(conn)
    wid = new_issue(conn, 1, labels=[])
    log = proposed(conn, store, worker, wid, proceed="needs_check", missing_information=["재현 금액"], confidence=0.95)
    assert worker.tick().triage_autostarted == 0
    assert_not_started(conn, wid, log["triage_id"])


def test_member_assignee_is_not_handed(admin, conn, store, worker, source, eligible):
    autostart(conn)
    wid = new_issue(conn, 1, labels=[])
    log = proposed(conn, store, worker, wid, assignee={"type": "member", "id": admin_id(conn)}, confidence=0.95)
    assert worker.tick().triage_autostarted == 0
    assert_not_started(conn, wid, log["triage_id"])


def test_disabled_or_missing_setting_is_not_handed(admin, conn, store, worker, source, eligible):
    wid = new_issue(conn, 1, labels=[])
    log = proposed(conn, store, worker, wid, confidence=0.95)
    assert worker.tick().triage_autostarted == 0  # 설정 행 없음 = 꺼짐
    assert_not_started(conn, wid, log["triage_id"])
    autostart(conn, enabled=False)
    assert worker.tick().triage_autostarted == 0
    assert_not_started(conn, wid, log["triage_id"])


def test_enabled_without_twenty_handled_triages_is_not_handed(admin, conn, store, worker, source):
    autostart(conn)  # 자격 없이 켜진 행 — 화면은 막지만 워커도 다시 본다
    wid = new_issue(conn, 1, labels=[])
    log = proposed(conn, store, worker, wid, confidence=0.95)
    assert worker.tick().triage_autostarted == 0
    assert_not_started(conn, wid, log["triage_id"])


def test_setting_turned_off_or_raised_after_the_proposal_wins(admin, conn, store, worker, source, eligible):
    """맡기는 순간의 설정 기준 — 판단 결과 뒤 꺼지거나 기준값이 오르면 맡기지 않는다."""
    autostart(conn, threshold=0.8)
    wid = new_issue(conn, 1, labels=[])
    log = proposed(conn, store, worker, wid, confidence=0.86)
    autostart(conn, enabled=False, version=2)
    assert worker.tick().triage_autostarted == 0
    assert_not_started(conn, wid, log["triage_id"])
    autostart(conn, threshold=0.9, version=3)
    assert worker.tick().triage_autostarted == 0
    assert_not_started(conn, wid, log["triage_id"])
    autostart(conn, threshold=0.85, version=4)
    assert worker.tick().triage_autostarted == 1


def test_a_person_who_assigned_first_wins(admin, conn, store, worker, source, eligible):
    autostart(conn)
    wid = new_issue(conn, 1, labels=[])
    log = proposed(conn, store, worker, wid)
    worker._judge_triage(conn, TickReport())  # 판정만 — 자동 맡기기 전에 사람이 정한다
    me = admin_id(conn)
    repo.assign_work_item(conn, SESSION, wid, assignee_type="member", assignee_id=me, by_member_id=me, now=NOW)

    assert worker.tick().triage_autostarted == 0
    assert (work(conn, wid)["assignee_type"], work(conn, wid)["assignee_id"]) == ("member", me)
    assert executions(conn, fix_stage(conn, wid)["task_id"]) == []
    assert log_row(conn, log["triage_id"])["handling"] == "changed"


# --- 기존 경로 그대로 -------------------------------------------------------------------------


def test_owner_approval_agent_waits_for_the_owner(app, admin, conn, store, worker, source, eligible, cycle):
    log_in_member(TestClient(app), display_name="이소유")
    owner = member_named(conn, "이소유")
    conn.execute("UPDATE connectors SET owner_member_id = ? WHERE connector_id = ?", (owner, cycle["billing"]))
    conn.commit()
    autostart(conn)
    wid = new_issue(conn, 1, labels=[])
    log = proposed(conn, store, worker, wid)
    repo.set_delegation_policy(conn, SESSION, FIX, "owner_approval", member_id=None, now=NOW)

    assert worker.tick().triage_autostarted == 1

    stage = fix_stage(conn, wid)
    assert executions(conn, stage["task_id"]) == []
    (approval,) = repo.list_owner_approvals(conn, stage["task_id"])
    assert approval["state"] == "open"
    assert (work(conn, wid)["status"], work(conn, wid)["status_reason"]) == ("내 차례", "자동으로 맡김 · 이소유 승인 대기")
    assert repo.turn_recipients_of(conn, wid) == (owner,)
    assert log_row(conn, log["triage_id"])["handling"] == "auto_started"


def test_offline_runner_waits(admin, conn, store, worker, clock, source, eligible):
    autostart(conn)
    wid = new_issue(conn, 1, labels=[])
    log = proposed(conn, store, worker, wid)
    seen_at(conn, NOW)
    clock.now = OFFLINE  # 러너 마지막 신호(NOW) 이틀 뒤

    assert worker.tick().triage_autostarted == 1

    stage = fix_stage(conn, wid)
    assert executions(conn, stage["task_id"]) == []
    assert (stage["status"], stage["status_reason"]) == ("대기", "공용 러너 꺼짐 · 켜지면 시작")
    assert (work(conn, wid)["assignee_type"], work(conn, wid)["assignee_id"]) == ("agent", FIX)
    assert log_row(conn, log["triage_id"])["handling"] == "auto_started"


def test_failure_of_an_autostarted_work_is_the_admins_turn(app, admin, conn, store, worker, source, eligible):
    log_in_member(TestClient(app), display_name="김멤버")
    autostart(conn)
    wid = new_issue(conn, 1, labels=[])
    proposed(conn, store, worker, wid)
    assert worker.tick().triage_autostarted == 1
    (execution,) = executions(conn, fix_stage(conn, wid)["task_id"])

    fail(conn, execution, "tool_failed", "테스트 실패")
    worker.tick()

    assert work(conn, wid)["status"] == "내 차례"
    assert repo.turn_recipients_of(conn, wid) == (admin_id(conn),)  # 맡긴 사람 없음 → 관리자 전원
