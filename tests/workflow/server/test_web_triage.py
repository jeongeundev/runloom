# ruff: noqa: F811 — test_task_cycle·test_triage_runs 픽스처(cycle·judge·worker)를 가져와 인자로 쓴다
"""판단 제안 절·버튼·목록 배지·사람 처리 기록 — phase 19 step 7 (ADR-0025, ARCHITECTURE "판단 — phase 19" 사람 처리·경로·화면).

패널 절 `data-panel-section="triage"`(판단 중·제안·무시함·처리됨·실패), [판단 받기]/[다시 판단]·[제안대로 맡기기]·[무시]
경로(`delegate` 동작·Origin 검사), 담당이 정해질 때 판단 로그 `accepted`/`changed`, [무시] → `dismissed`. 판단은 연결 프로그램
대신 테스트가 결과 산출물을 올리고 워커 판정(`_judge_triage`)으로 제안을 만든다.
"""

import re

import pytest
from fastapi.testclient import TestClient

from workflow.adapters import repo
from workflow.contracts.v1 import KindSpec
from workflow.server.worker import TickReport

from .conftest import ADMIN_EMAIL, log_in, log_in_member
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
)
from .test_triage_judge import fail, log_row, result_body, submit
from .test_triage_runs import judge, logs, new_issue, request  # noqa: F401 — 픽스처

DOCS = KindSpec(kind="write_docs", label="문서 작성", capability_code="docs.write", scope_key="repository_id",
                input_kinds=[], output_kind="generic_result", outcomes=["done"], instructions="", builtin=False)


@pytest.fixture
def admin(client, judge) -> TestClient:
    """관리자 로그인 — 판단 후보(멤버)에 들도록 판단 시작 전에."""
    return log_in(client)


def admin_id(conn) -> str:
    return repo.find_member_by_email(conn, SESSION, ADMIN_EMAIL)["member_id"]


def member_named(conn, name: str) -> str:
    return next(m["member_id"] for m in repo.list_members(conn, SESSION) if m["display_name"] == name)


def start(conn, settings, wid: str):
    started = request(conn, settings, wid)
    assert started.started, started.reason
    (log,) = [r for r in logs(conn, wid) if r["state"] == "running"]
    return log


def propose(conn, store, settings, worker, wid: str, **overrides):
    """판단 시작 → 결과 제출 → 워커 판정. 반환은 판단 로그 행(proposed)."""
    log = start(conn, settings, wid)
    submit(conn, store, log, result_body(log, **overrides))
    worker._judge_triage(conn, TickReport())
    row = log_row(conn, log["triage_id"])
    assert row["state"] == "proposed", row["failed_message"]
    return row


def panel(client, key: str = "RUN-1") -> str:
    response = client.get(f"/work/{key}/panel")
    assert response.status_code == 200, response.text
    return response.text


def section(text: str, name: str = "triage") -> str | None:
    found = re.search(rf'data-panel-section="{name}".*?</section>', text, re.S)
    return found.group(0) if found else None


def error(response, status: int, code: str) -> None:
    assert response.status_code == status, response.text
    assert f"<code>{code}</code>" in response.text, response.text


def work(conn, wid: str):
    return repo.get_work_item(conn, SESSION, wid)


def fix_stage(conn, wid: str):
    return repo.open_stage(conn, wid)


# --- 절 그리기 -----------------------------------------------------------------------


def test_proposal_section_shows_proceed_confidence_assignee_kind_reasons_and_buttons(admin, conn, store, settings,
                                                                                      worker):
    wid = new_issue(conn, 1)
    new_issue(conn, 2)
    log = propose(conn, store, settings, worker, wid, predecessors=["RUN-2"],
                  reasons=[{"criterion": "clarity", "note": "재현 절차가 분명함"},
                           {"criterion": "dependency", "note": "RUN-2 먼저"}],
                  proceed="needs_check", missing_information=["재현 금액"], confidence=0.62)

    text = panel(admin)
    triage = section(text)
    assert triage is not None
    # 절 순서 — 속성 다음, 지금 할 일·진행 앞 (ARCHITECTURE 화면)
    assert text.index('data-panel-section="props"') < text.index('data-panel-section="triage"') < text.index(
        'data-panel-section="timeline"')
    assert "판단 제안 · 확인 필요 · 확신도 0.62 · 기준 v1" in triage
    assert "버그 수정" in triage  # 종류 라벨
    assert f"{FIX} · 공용" in triage  # 담당 = 에이전트 이름 · 소유자
    assert "RUN-2" in triage
    assert "명확성 — 재현 절차가 분명함" in triage and "선행 의존 — RUN-2 먼저" in triage
    assert "재현 금액" in triage
    assert 'action="/work/RUN-1/triage/accept"' in triage and 'action="/work/RUN-1/triage/dismiss"' in triage
    assert f'name="triage_id" value="{log["triage_id"]}"' in triage
    assert 'action="/work/RUN-1/triage"' in triage and "다시 판단" in triage
    assert "triage_invalid" not in triage and "needs_check" not in triage  # 내부 코드는 보이지 않는다


def test_section_is_not_drawn_without_a_triage_route_or_log(app, client, conn, cycle):
    """판단 Agent 없는 저장소의 업무 — 판단할 수 있는 경로가 아니면 절이 없다(다른 원본·담당 있는 업무)."""
    admin = log_in(client)
    new_issue(conn, 1)
    repo.assign_work_item(conn, SESSION, repo.get_work_item_by_key(conn, SESSION, 1)["work_item_id"],
                          assignee_type="member", assignee_id=admin_id(conn), now=NOW)
    assert section(panel(admin)) is None


def test_without_a_triage_agent_there_is_no_button_only_the_phrase(admin, conn):
    repo.save_github_source(conn, SESSION, config(review_agent_id=REVIEW), NOW)  # triage_agent_id 없음
    new_issue(conn, 1)
    triage = section(panel(admin))
    assert triage is not None and "판단 에이전트 없음 — 저장소 카드에서 고르세요" in triage
    assert "<button" not in triage
    error(admin.post("/work/RUN-1/triage"), 409, "triage_unavailable")


def test_new_work_shows_the_get_triage_button(admin, conn):
    new_issue(conn, 1)
    triage = section(panel(admin))
    assert 'action="/work/RUN-1/triage"' in triage and "판단 받기" in triage and "다시 판단" not in triage


def test_disabled_reason_shows_a_disabled_button(admin, conn, cycle):
    repo.record_supported_kinds(conn, cycle["billing"], ["bug_fix", "code_review"])  # 옛 러너
    new_issue(conn, 1)
    triage = section(panel(admin))
    assert re.search(r"<button[^>]*disabled[^>]*>판단 받기</button>", triage)
    assert "러너 업데이트 필요 — 판단 미지원" in triage


def test_running_triage_shows_agent_and_version(admin, conn, settings):
    wid = new_issue(conn, 1)
    start(conn, settings, wid)
    triage = section(panel(admin))
    assert f"판단 중 · {FIX} · 기준 v1" in triage
    assert "판단 받기" not in triage and "다시 판단" not in triage


def test_failed_triage_shows_the_failure_name_and_retry(admin, conn, settings, worker):
    wid = new_issue(conn, 1)
    log = start(conn, settings, wid)
    fail(conn, log, "timeout", "30분 안에 끝나지 않음")
    worker._judge_triage(conn, TickReport())
    triage = section(panel(admin))
    assert "판단 실패 · 시간 초과 · 30분 안에 끝나지 않음" in triage
    assert "다시 판단" in triage and "제안대로 맡기기" not in triage


def test_reasons_and_missing_information_are_escaped(admin, conn, store, settings, worker):
    wid = new_issue(conn, 1)
    propose(conn, store, settings, worker, wid, proceed="needs_check",
            reasons=[{"criterion": "risk", "note": "<script>alert(1)</script>"}],
            missing_information=["<img src=x onerror=alert(2)>"])
    triage = section(panel(admin))
    assert "<script>alert(1)" not in triage and "&lt;script&gt;alert(1)&lt;/script&gt;" in triage
    assert "<img src=x" not in triage and "&lt;img src=x onerror=alert(2)&gt;" in triage


def test_unsuitable_proposal_has_no_accept_button(admin, conn, store, settings, worker):
    wid = new_issue(conn, 1)
    propose(conn, store, settings, worker, wid, proceed="unsuitable", confidence=0.3)
    triage = section(panel(admin))
    assert "부적합" in triage and "triage/accept" not in triage and "triage/dismiss" in triage


# --- [제안대로 맡기기] ------------------------------------------------------------------


def test_accept_hands_to_the_proposed_agent_and_records_accepted(admin, conn, store, settings, worker):
    # 지시 전 이슈가 생기는 all_open 소스 — 맡기면 운영자 지시로 곧 착수한다(test_web_work 와 같다)
    repo.save_github_source(conn, SESSION, config(review_agent_id=REVIEW, triage_agent_id=FIX, intake="all_open",
                                                  label_filter=[], trigger_label="runloom"), NOW)
    wid = new_issue(conn, 1, labels=[])
    other = new_issue(conn, 2, labels=[])
    log = propose(conn, store, settings, worker, wid, predecessors=["RUN-2"])

    response = admin.post("/work/RUN-1/triage/accept", data={"triage_id": log["triage_id"]}, follow_redirects=False)

    assert response.status_code == 303 and response.headers["location"] == "/tasks?open=RUN-1"
    assert (work(conn, wid)["assignee_type"], work(conn, wid)["assignee_id"]) == ("agent", FIX)
    assert len(executions(conn, fix_stage(conn, wid)["task_id"])) == 1
    row = log_row(conn, log["triage_id"])
    assert (row["handling"], row["handled_by_member_id"], row["final_assignee_type"], row["final_assignee_id"],
            row["final_kind"]) == ("accepted", admin_id(conn), "agent", FIX, "bug_fix")
    links = repo.list_work_item_links(conn, wid)
    assert [(link["from_work_item_id"], link["to_work_item_id"], link["type"]) for link in links] == [
        (other, wid, "blocks")]
    triage = section(panel(admin))
    assert "판단 제안대로 맡김" in triage and "triage/accept" not in triage


def test_accept_changes_the_unstarted_stage_kind_and_assigns_a_member(admin, conn, store, settings, worker):
    repo.insert_kind(conn, SESSION, DOCS, NOW)
    wid = new_issue(conn, 1)
    me = admin_id(conn)
    log = propose(conn, store, settings, worker, wid, proposed_kind="write_docs",
                  assignee={"type": "member", "id": me})
    stage = fix_stage(conn, wid)
    assert "지금 버그 수정 → 제안 문서 작성" in section(panel(admin))

    response = admin.post("/work/RUN-1/triage/accept", data={"triage_id": log["triage_id"]}, follow_redirects=False)

    assert response.status_code == 303
    changed = repo.get_task(conn, stage["task_id"])
    assert changed["kind"] == "write_docs"
    assert changed["required_capability_json"].replace(" ", "") == (
        '{"code":"docs.write","scope":{"repository_id":"billing"}}')
    assert work(conn, wid)["kind"] == "write_docs"  # 첫 단계면 업무 종류도
    assert (work(conn, wid)["assignee_type"], work(conn, wid)["assignee_id"]) == ("member", me)
    assert executions(conn, stage["task_id"]) == []  # 멤버는 배정만
    row = log_row(conn, log["triage_id"])
    assert (row["handling"], row["final_assignee_type"], row["final_kind"]) == ("accepted", "member", "write_docs")


def test_accept_with_owner_approval_policy_waits_for_the_owner(app, admin, conn, store, settings, worker, cycle):
    wid = new_issue(conn, 1)
    log = propose(conn, store, settings, worker, wid)
    log_in_member(TestClient(app), display_name="이소유")
    conn.execute("UPDATE connectors SET owner_member_id = ? WHERE connector_id = ?",
                 (member_named(conn, "이소유"), cycle["billing"]))
    repo.set_delegation_policy(conn, SESSION, FIX, "owner_approval", member_id=None, now=NOW)

    response = admin.post("/work/RUN-1/triage/accept", data={"triage_id": log["triage_id"]}, follow_redirects=False)

    assert response.status_code == 303
    stage = fix_stage(conn, wid)
    assert executions(conn, stage["task_id"]) == []
    (approval,) = repo.list_owner_approvals(conn, stage["task_id"])
    assert approval["state"] == "open"
    assert work(conn, wid)["status"] == "내 차례"
    assert log_row(conn, log["triage_id"])["handling"] == "accepted"


def test_accept_twice_or_a_stale_triage_is_409(admin, conn, store, settings, worker):
    wid = new_issue(conn, 1)
    log = propose(conn, store, settings, worker, wid)
    error(admin.post("/work/RUN-1/triage/accept", data={"triage_id": "trg-nope0000"}), 409, "triage_stale")
    assert admin.post("/work/RUN-1/triage/accept", data={"triage_id": log["triage_id"]},
                      follow_redirects=False).status_code == 303
    error(admin.post("/work/RUN-1/triage/accept", data={"triage_id": log["triage_id"]}), 409, "triage_stale")


# --- 사람 처리 기록 -----------------------------------------------------------------------


def test_choosing_a_different_assignee_records_changed(admin, conn, store, settings, worker):
    wid = new_issue(conn, 1)
    log = propose(conn, store, settings, worker, wid)
    me = admin_id(conn)

    response = admin.post("/work/RUN-1/assignee", data={"assignee": f"member:{me}"}, follow_redirects=False)

    assert response.status_code == 303
    row = log_row(conn, log["triage_id"])
    assert (row["handling"], row["handled_by_member_id"], row["final_assignee_type"], row["final_assignee_id"],
            row["final_kind"], row["handled_at"] is not None) == ("changed", me, "member", me, "bug_fix", True)
    assert "판단과 다르게 정함" in section(panel(admin))


def test_choosing_the_proposed_agent_in_the_select_records_accepted(admin, conn, store, settings, worker):
    wid = new_issue(conn, 1)
    log = propose(conn, store, settings, worker, wid)
    admin.post("/work/RUN-1/assignee", data={"assignee": f"agent:{FIX}"}, follow_redirects=False)
    assert log_row(conn, log["triage_id"])["handling"] == "accepted"


def test_working_in_my_session_records_changed(admin, conn, store, settings, worker):
    wid = new_issue(conn, 1)
    log = propose(conn, store, settings, worker, wid)
    assert admin.post("/work/RUN-1/direct", follow_redirects=False).status_code == 303
    row = log_row(conn, log["triage_id"])
    assert (row["handling"], row["final_assignee_type"], row["final_assignee_id"]) == ("changed", "member",
                                                                                       admin_id(conn))


def test_later_reassignment_does_not_rewrite_the_handling(admin, conn, store, settings, worker):
    wid = new_issue(conn, 1)
    log = propose(conn, store, settings, worker, wid)
    me = admin_id(conn)
    admin.post("/work/RUN-1/assignee", data={"assignee": f"member:{me}"}, follow_redirects=False)
    admin.post("/work/RUN-1/assignee", data={"assignee": "none"}, follow_redirects=False)
    admin.post("/work/RUN-1/assignee", data={"assignee": f"agent:{FIX}"}, follow_redirects=False)
    row = log_row(conn, log["triage_id"])
    assert (row["handling"], row["final_assignee_type"]) == ("changed", "member")


# --- [무시] ------------------------------------------------------------------------------


def test_dismiss_records_dismissed_and_folds_the_section(admin, conn, store, settings, worker):
    wid = new_issue(conn, 1)
    log = propose(conn, store, settings, worker, wid)

    response = admin.post("/work/RUN-1/triage/dismiss", data={"triage_id": log["triage_id"]}, follow_redirects=False)

    assert response.status_code == 303 and response.headers["location"] == "/tasks?open=RUN-1"
    row = log_row(conn, log["triage_id"])
    assert (row["handling"], row["handled_by_member_id"], row["final_assignee_type"], row["final_kind"]) == (
        "dismissed", admin_id(conn), None, None)
    assert (work(conn, wid)["status"], work(conn, wid)["status_reason"]) == ("새로 들어옴", "담당 없음")
    triage = section(panel(admin))
    assert re.search(r"<details[^>]*>\s*<summary>판단 제안\(무시함\) · ", triage)
    assert "triage/accept" not in triage and "triage/dismiss" not in triage
    error(admin.post("/work/RUN-1/triage/dismiss", data={"triage_id": log["triage_id"]}), 409, "triage_stale")


# --- [판단 받기] 권한 ------------------------------------------------------------------------


def test_member_can_request_a_triage(app, admin, conn):
    wid = new_issue(conn, 1)
    member = log_in_member(TestClient(app), display_name="김맡김")

    response = member.post("/work/RUN-1/triage", follow_redirects=False)

    assert response.status_code == 303 and response.headers["location"] == "/tasks?open=RUN-1"
    (row,) = logs(conn, wid)
    assert (row["state"], row["trigger"], row["requested_by_member_id"]) == ("running", "manual",
                                                                             member_named(conn, "김맡김"))
    error(member.post("/work/RUN-1/triage"), 409, "triage_unavailable")  # 이미 판단 중


def test_triage_routes_need_login_and_same_origin(client, admin, conn, store, settings, worker):
    wid = new_issue(conn, 1)
    log = propose(conn, store, settings, worker, wid)
    for path in ("/work/RUN-1/triage", "/work/RUN-1/triage/accept", "/work/RUN-1/triage/dismiss"):
        response = admin.post(path, data={"triage_id": log["triage_id"]},
                              headers={"Origin": "http://evil.example", "Accept": "text/html"}, follow_redirects=False)
        assert response.status_code == 403 and "forbidden_origin" in response.text
        stranger = TestClient(client.app)
        response = stranger.post(path, data={"triage_id": log["triage_id"]}, follow_redirects=False)
        assert response.status_code == 303 and response.headers["location"] == "/login"
    assert log_row(conn, log["triage_id"])["handling"] is None
    assert len(logs(conn, wid)) == 1


def test_unknown_work_key_is_404(admin):
    error(admin.post("/work/RUN-99/triage"), 404, "not_found")
    error(admin.post("/work/RUN-99/triage/accept", data={"triage_id": "trg-00000000"}), 404, "not_found")
    error(admin.post("/work/RUN-99/triage/dismiss", data={"triage_id": "trg-00000000"}), 404, "not_found")


# --- 목록 배지 ---------------------------------------------------------------------------


def list_row(client, key: str = "RUN-1") -> str:
    response = client.get("/tasks")
    assert response.status_code == 200, response.text
    return re.search(rf'<tr class="work-row" data-work-key="{key}">.*?</tr>', response.text, re.S).group(0)


def test_list_badge_follows_the_latest_triage(admin, conn, store, settings, worker):
    wid = new_issue(conn, 1)
    assert "data-triage" not in list_row(admin)
    log = start(conn, settings, wid)
    assert 'data-triage="판단 중"' in list_row(admin)
    submit(conn, store, log, result_body(log))
    worker._judge_triage(conn, TickReport())
    assert 'data-triage="판단 제안"' in list_row(admin)
    rows = {r.work_key: r for r in repo.list_work_rows(conn, SESSION, closed_since=None)}
    assert rows["RUN-1"].triage == "판단 제안"
    admin.post("/work/RUN-1/triage/dismiss", data={"triage_id": log["triage_id"]}, follow_redirects=False)
    assert "data-triage" not in list_row(admin)


def test_list_badge_for_a_failed_triage(admin, conn, settings, worker):
    wid = new_issue(conn, 1)
    log = start(conn, settings, wid)
    fail(conn, log, "usage_limit")
    worker._judge_triage(conn, TickReport())
    assert 'data-triage="판단 실패"' in list_row(admin)

