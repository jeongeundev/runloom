# ruff: noqa: F811 — test_task_cycle·test_triage_runs·test_worker_next_step 픽스처(cycle·judge·capable·worker)를 인자로 쓴다
"""[제안대로]·[무시] — 다음 행동 4형 적용 (phase 22 step 7, ADR-0027 결정 7, ARCHITECTURE "결과 뒤 판단 — phase 22"
행동별 적용·경로).

원인은 수정 Agent 의 `needs_information` 결과(②). 워커가 결과 뒤 판단을 걸고, 테스트가 러너 대신 판단 결과를 올리고,
워커 판정으로 제안을 만든 뒤 `work_actions.accept_next_step`·`dismiss_next_step`·웹 경로를 부른다. 실제 러너·GitHub·모델을
부르지 않는다.
"""

import json

import pytest
from fastapi.testclient import TestClient

from workflow.adapters import repo, responsibility_store
from workflow.adapters.errors import NextStepHandled
from workflow.contracts.responsibility import Responsibility
from workflow.contracts.v1 import ExecutionRequest, ReviewComment
from workflow.domain.next_step import STAGE_DONE_REASON, WORK_DONE_REASON
from workflow.server import human_api, work_actions
from workflow.server.work_actions import NEXT_STEP_STALE, WorkActionError

from .conftest import log_in
from .test_task_cycle import (  # noqa: F401 — 픽스처
    FIX,
    NOW,
    SESSION,
    clock,
    config,
    cycle,
    executions,
    make_worker,
    settings,
    worker,
)
from .test_triage_dispatch import triage_logs
from .test_triage_judge import log_row, submit
from .test_triage_runs import judge, new_issue  # noqa: F401 — 픽스처
from .test_worker_next_step import DOCS, capable, fix_result, next_body, requests_of, work_of  # noqa: F401

REASONS = [{"criterion": "clarity", "note": "호스트 설정값이 필요"}]


@pytest.fixture
def admin(conn, capable) -> str:
    """판단 후보(멤버)에 들도록 판단 시작 전에 만든다."""
    return repo.ensure_first_admin(conn, SESSION, now=NOW)


@pytest.fixture
def docs(conn, capable) -> None:
    """사용자 정의 종류 docs 등록 + FIX 가 billing 의 docs 를 맡을 수 있다(판단 시작 전 — 후보에 들게)."""
    repo.insert_kind(conn, SESSION, DOCS, NOW)
    caps = json.loads(repo.get_agent(conn, FIX)["capabilities_json"])
    caps.append({"code": "code.docs", "scope": {"repository_id": "billing"}})
    conn.execute("UPDATE agents SET capabilities_json = ? WHERE agent_id = ?", (json.dumps(caps), FIX))
    conn.commit()


@pytest.fixture
def directory(conn, admin) -> str:
    """담당 범위 표 — kube_proxy/investigation → 박조사(판단 담당 admin). 반환은 받는 사람 member_id."""
    recipient = repo.add_member(conn, SESSION, display_name="박조사", now=NOW)
    responsibility_store.replace_entries(conn, SESSION, [
        Responsibility(system_id="kube_proxy", request_kind="investigation", recipient_member_id=recipient,
                       judgment_member_id=admin, agent_id=None),
    ], expected_revision=repo.get_config_revision(conn, SESSION), member_id=admin, now=NOW)
    return recipient


def propose(conn, store, worker, action: dict) -> tuple[str, str, str, object]:
    """RUN-1 수정 결과 `needs_information` → 결과 뒤 판단 → 결과 제출 → 판정(proposed).
    반환은 (원인 Task, 원인 실행, 업무, 판단 로그 행)."""
    task_id, execution_id = fix_result(conn, store, worker, outcome="needs_information")
    worker.tick()
    wid = work_of(conn, task_id)["work_item_id"]
    (log,) = triage_logs(conn, wid)
    submit(conn, store, log, next_body(log, action, reasons=REASONS))
    worker.tick()
    row = log_row(conn, log["triage_id"])
    assert row["state"] == "proposed", row["failed_message"]
    return task_id, execution_id, wid, row


def accept(conn, store, settings, wid: str, log, member_id: str, **kwargs) -> None:
    work_actions.accept_next_step(conn, store, settings, session_id=SESSION, work_item_id=wid,
                                  triage_id=log["triage_id"], member_id=member_id, now=NOW, **kwargs)


def raises(code: str, status: int, fn, *args, **kwargs) -> WorkActionError:
    with pytest.raises(WorkActionError) as caught:
        fn(*args, **kwargs)
    assert (caught.value.status, caught.value.code) == (status, code), caught.value.message
    return caught.value


def stages(conn, wid: str) -> list:
    return [t for t in repo.list_work_item_tasks(conn, wid) if not repo.is_triage_task(conn, t["task_id"])]


def work(conn, wid: str):
    return repo.get_work_item(conn, SESSION, wid)


def stage_action(kind: str, assignee: dict, *, rework: bool = False) -> dict:
    return {"type": "stage", "kind": kind, "assignee": assignee, "rework": rework}


# --- stage (다음 단계) ----------------------------------------------------------------


def test_stage_finishes_the_cause_and_assigns_the_new_stage_to_a_member(conn, store, settings, worker, admin, docs):
    task_id, execution_id, wid, log = propose(conn, store, worker,
                                              stage_action("docs", {"type": "member", "id": admin}))

    accept(conn, store, settings, wid, log, admin)

    cause = repo.get_task(conn, task_id)
    assert (cause["status"], cause["status_reason"], cause["finished_at"]) == ("완료", STAGE_DONE_REASON, NOW)
    assert repo.get_execution(conn, execution_id)["released_at"] == NOW
    (new,) = [t for t in stages(conn, wid) if t["task_id"] != task_id]
    assert (new["kind"], new["predecessor_task_id"], new["selection_mode"], new["chosen_agent_id"]) == (
        "docs", None, "manual", None)
    assert new["title"] == f"문서 정리: {work(conn, wid)['title']}"
    assert json.loads(new["required_capability_json"]) == {"code": "code.docs", "scope": {"repository_id": "billing"}}
    assert (new["run_mode"], new["completion_mode"], json.loads(new["target_json"])) == (
        cause["run_mode"], "review", {})
    # 입력은 선행·산출물 인계가 아니라 글로 넘긴다 — 원래 요청 + 이전 결과 요약 + 판단 근거
    assert work(conn, wid)["request"].strip() in new["request"]
    assert "## 이전 단계 결과 (버그 수정 · needs_information)" in new["request"]
    assert "## 다음 단계 판단 근거\n- 명확성 — 호스트 설정값이 필요" in new["request"]
    assert executions(conn, new["task_id"]) == []  # 멤버는 배정만
    assert (work(conn, wid)["assignee_type"], work(conn, wid)["assignee_id"]) == ("member", admin)
    row = log_row(conn, log["triage_id"])
    assert (row["handling"], row["handled_by_member_id"], row["handled_at"]) == ("accepted", admin, NOW)
    assert (row["final_assignee_type"], row["final_kind"]) == (None, None)
    # 원래 사람 요청은 열리지 않는다(처리 accepted → hold)
    worker.tick()
    assert requests_of(conn, task_id) == []


def test_stage_hands_the_new_stage_to_the_proposed_agent(conn, store, settings, worker, admin, docs):
    task_id, _, wid, log = propose(conn, store, worker, stage_action("docs", {"type": "agent", "id": FIX}))

    accept(conn, store, settings, wid, log, admin)

    (new,) = [t for t in stages(conn, wid) if t["task_id"] != task_id]
    assert repo.get_task(conn, new["task_id"])["chosen_agent_id"] == FIX
    assert repo.get_selection(conn, new["task_id"]).selected_agent_id == FIX
    assert (work(conn, wid)["assignee_type"], work(conn, wid)["assignee_id"]) == ("agent", FIX)
    assert work(conn, wid)["requested_by_member_id"] == admin
    (run,) = executions(conn, new["task_id"])  # 러너가 켜져 있어 맡기면 곧 착수(소유자 승인·대기 규칙 그대로)
    assert (run["agent_id"], run["status"], run["kind"]) == (FIX, "queued", "docs")
    assert ExecutionRequest.model_validate_json(run["request_json"]).input_artifact_ids == []
    assert log_row(conn, log["triage_id"])["handling"] == "accepted"


def test_removed_kind_is_refused_before_writing(conn, store, settings, worker, admin, docs):
    task_id, _, wid, log = propose(conn, store, worker, stage_action("docs", {"type": "member", "id": admin}))
    repo.delete_kind(conn, SESSION, "docs", now=NOW)

    raises("kind_unavailable", 409, accept, conn, store, settings, wid, log, admin)

    assert repo.get_task(conn, task_id)["finished_at"] is None
    assert len(stages(conn, wid)) == 1
    assert log_row(conn, log["triage_id"])["handling"] is None


# --- stage rework (재작업) -------------------------------------------------------------


def test_rework_starts_a_new_execution_of_the_cause_task(conn, store, settings, worker, admin):
    task_id, execution_id, wid, log = propose(conn, store, worker,
                                              stage_action("bug_fix", {"type": "agent", "id": FIX}, rework=True))
    before = ExecutionRequest.model_validate_json(repo.get_execution(conn, execution_id)["request_json"])

    accept(conn, store, settings, wid, log, admin)

    old, new = executions(conn, task_id)
    assert old["execution_id"] == execution_id and old["released_at"] == NOW
    assert (new["attempt_no"], new["agent_id"], new["released_at"]) == (2, FIX, None)
    request = ExecutionRequest.model_validate_json(new["request_json"])
    *carried, result_id, comment_id = request.input_artifact_ids
    assert carried == before.input_artifact_ids and result_id == old["result_artifact_id"]
    comment = ReviewComment.model_validate_json(repo.read_artifact(conn, store, comment_id))
    assert (comment.decision, comment.reviewed_execution_id) == ("request_changes", execution_id)
    assert comment.comment.startswith("다음 단계 판단: 재작업\n- 명확성 — 호스트 설정값이 필요")
    assert request.target == before.target  # needs_information 결과에는 결과 커밋이 없다 — 이전 대상 그대로
    cause = repo.get_task(conn, task_id)
    assert (cause["finished_at"], cause["review_decision"]) == (None, "request_changes")
    assert len(stages(conn, wid)) == 1  # 새 단계 없음
    assert log_row(conn, log["triage_id"])["handling"] == "accepted"


# --- new_work (새 업무) ----------------------------------------------------------------


def test_new_work_finishes_the_cause_and_spawns_a_linked_work(conn, store, settings, worker, admin, docs):
    action = {"type": "new_work", "kind": "docs", "title": "운영 문서 정리", "assignee": {"type": "member", "id": admin}}
    task_id, execution_id, wid, log = propose(conn, store, worker, action)

    accept(conn, store, settings, wid, log, admin)

    cause = repo.get_task(conn, task_id)
    assert (cause["status"], cause["status_reason"]) == ("완료", WORK_DONE_REASON)
    (link,) = [r for r in repo.list_work_item_links(conn, wid) if r["type"] == "spawned_from"]
    assert (link["from_work_item_id"], link["cause_execution_id"]) == (wid, execution_id)
    spawned = link["to_work_item_id"]
    (first,) = stages(conn, spawned)
    assert (first["kind"], first["title"], first["predecessor_task_id"]) == ("docs", "운영 문서 정리", None)
    assert "## 이전 단계 결과 (버그 수정 · needs_information)" in first["request"]
    assert repo.get_followup_link(conn, first["task_id"])["cause_execution_id"] == execution_id
    assert (work(conn, spawned)["assignee_type"], work(conn, spawned)["assignee_id"]) == ("member", admin)
    assert len(stages(conn, wid)) == 1
    assert log_row(conn, log["triage_id"])["handling"] == "accepted"


# --- internal_request (사내 요청) -------------------------------------------------------


def internal(recipient: str) -> dict:
    return {"type": "internal_request", "system_id": "kube_proxy", "request_kind": "investigation",
            "recipient_member_id": recipient, "purpose": "호스트 sysctl 값 확인"}


def internal_requests(conn) -> list:
    return conn.execute("SELECT * FROM internal_requests ORDER BY rowid").fetchall()


def test_internal_request_is_created_by_the_member_and_the_cause_waits(conn, store, settings, worker, admin,
                                                                      directory):
    task_id, execution_id, wid, log = propose(conn, store, worker, internal(directory))
    revision = repo.get_config_revision(conn, SESSION)

    accept(conn, store, settings, wid, log, admin)

    (request,) = internal_requests(conn)
    assert (request["work_item_id"], request["requester_member_id"], request["recipient_member_id"],
            request["judgment_member_id"], request["submission_key"], request["purpose"],
            request["created_by_triage_id"], request["directory_revision"], request["state"]) == (
        wid, admin, directory, admin, f"next_step:{log['triage_id']}", "호스트 sysctl 값 확인",
        log["triage_id"], revision, "pending")
    # 원인 Task 는 그대로 — 받는 사람이 수락하기 전에는 아무 실행도 없다
    cause = repo.get_task(conn, task_id)
    assert cause["finished_at"] is None and cause["status"] == "확인 필요"
    assert repo.get_execution(conn, execution_id)["released_at"] is None
    assert len(executions(conn, task_id)) == 1
    assert (work(conn, wid)["status"], work(conn, wid)["status_reason"]) == ("대기", "사내 요청 대기 · 박조사")
    assert log_row(conn, log["triage_id"])["handling"] == "accepted"
    worker.tick()
    assert requests_of(conn, task_id) == [] and len(internal_requests(conn)) == 1


def test_pressing_twice_makes_one_internal_request(conn, store, settings, worker, admin, directory):
    _, _, wid, log = propose(conn, store, worker, internal(directory))
    accept(conn, store, settings, wid, log, admin)

    error = raises("next_step_stale", 409, accept, conn, store, settings, wid, log, admin)

    assert error.message == NEXT_STEP_STALE
    assert len(internal_requests(conn)) == 1


def test_changed_directory_entry_is_stale(conn, store, settings, worker, admin, directory):
    _, _, wid, log = propose(conn, store, worker, internal(directory))
    responsibility_store.replace_entries(conn, SESSION, [
        Responsibility(system_id="kube_proxy", request_kind="investigation", recipient_member_id=directory,
                       judgment_member_id=directory, agent_id=None),
    ], expected_revision=repo.get_config_revision(conn, SESSION), member_id=admin, now=NOW)

    error = raises("stale_directory", 409, accept, conn, store, settings, wid, log, admin)

    assert "[무시]" in error.message
    assert internal_requests(conn) == []
    assert log_row(conn, log["triage_id"])["handling"] is None


def test_unrelated_directory_change_still_creates_the_request(conn, store, settings, worker, admin, directory):
    """담당표 revision 은 워크스페이스 설정 번호 전체 — 같은 항목이 그대로면 다른 설정이 바뀌어도 만든다."""
    _, _, wid, log = propose(conn, store, worker, internal(directory))
    other = repo.add_member(conn, SESSION, display_name="최다른", now=NOW)
    responsibility_store.replace_entries(conn, SESSION, [
        Responsibility(system_id="kube_proxy", request_kind="investigation", recipient_member_id=directory,
                       judgment_member_id=admin, agent_id=None),
        Responsibility(system_id="kubelet", request_kind="investigation", recipient_member_id=other,
                       judgment_member_id=admin, agent_id=None),
    ], expected_revision=repo.get_config_revision(conn, SESSION), member_id=admin, now=NOW)

    accept(conn, store, settings, wid, log, admin)

    (request,) = internal_requests(conn)
    assert request["directory_revision"] == repo.get_config_revision(conn, SESSION)


def test_disabled_recipient_is_an_invalid_recipient(conn, store, settings, worker, admin, directory):
    _, _, wid, log = propose(conn, store, worker, internal(directory))
    conn.execute("UPDATE members SET disabled_at = ? WHERE member_id = ?", (NOW, directory))
    conn.commit()

    raises("invalid_recipient", 422, accept, conn, store, settings, wid, log, admin)

    assert internal_requests(conn) == [] and log_row(conn, log["triage_id"])["handling"] is None


# --- human (사람 확인) -------------------------------------------------------------------


def test_human_opens_a_next_step_human_request(conn, store, settings, worker, admin):
    question = {"type": "human", "question": "재현 금액을 알려 주세요\n둘째 줄"}
    task_id, execution_id, wid, log = propose(conn, store, worker, question)

    accept(conn, store, settings, wid, log, admin)

    assert requests_of(conn, task_id) == [("next_step_human", f"next_step_human:{log['triage_id']}")]
    (request,) = repo.list_human_requests(conn, task_id)
    assert (request["question"], request["state"]) == ("재현 금액을 알려 주세요\n둘째 줄", "open")
    assert human_api.allowed_actions("next_step_human") == {"resume", "close"}
    assert human_api.asks_information("next_step_human")
    assert repo.get_task(conn, task_id)["finished_at"] is None
    assert repo.get_execution(conn, execution_id)["released_at"] is None
    assert (work(conn, wid)["status"], work(conn, wid)["status_reason"]) == (
        "내 차례", "사람 요청 — 재현 금액을 알려 주세요")
    assert log_row(conn, log["triage_id"])["handling"] == "accepted"
    worker.tick()  # 원래 사람 요청(fix_needs_information)은 열리지 않는다 — 같은 원인에 사람 요청 하나
    assert len(requests_of(conn, task_id)) == 1


# --- 공통 검사 ----------------------------------------------------------------------------


def test_stale_and_missing_proposals_are_refused(conn, store, settings, worker, admin):
    task_id, execution_id, wid, log = propose(conn, store, worker, {"type": "human", "question": "확인"})

    raises("not_found", 404, work_actions.accept_next_step, conn, store, settings, session_id=SESSION,
           work_item_id="wi-nope", triage_id=log["triage_id"], member_id=admin, now=NOW)
    raises("next_step_stale", 409, work_actions.accept_next_step, conn, store, settings, session_id=SESSION,
           work_item_id=wid, triage_id="tri-nope", member_id=admin, now=NOW)
    # 원인이 바뀜(원인 실행 해제) — 지난 제안
    conn.execute("UPDATE executions SET released_at = ? WHERE execution_id = ?", (NOW, execution_id))
    conn.commit()
    raises("next_step_stale", 409, accept, conn, store, settings, wid, log, admin)
    assert requests_of(conn, task_id) == [] and log_row(conn, log["triage_id"])["handling"] is None


def test_closed_work_is_refused(conn, store, settings, worker, admin):
    _, _, wid, log = propose(conn, store, worker, {"type": "human", "question": "확인"})
    conn.execute("UPDATE work_items SET status = '종료', closed_at = ? WHERE work_item_id = ?", (NOW, wid))
    conn.commit()
    raises("work_closed", 409, accept, conn, store, settings, wid, log, admin)


# --- [무시] ------------------------------------------------------------------------------


def test_dismiss_records_dismissed_and_the_original_request_opens_once(conn, store, settings, worker, admin):
    task_id, execution_id, wid, log = propose(conn, store, worker, {"type": "human", "question": "확인"})

    work_actions.dismiss_next_step(conn, session_id=SESSION, work_item_id=wid, triage_id=log["triage_id"],
                                   member_id=admin, now=NOW)

    row = log_row(conn, log["triage_id"])
    assert (row["handling"], row["handled_by_member_id"]) == ("dismissed", admin)
    raises("next_step_stale", 409, work_actions.dismiss_next_step, conn, session_id=SESSION, work_item_id=wid,
           triage_id=log["triage_id"], member_id=admin, now=NOW)
    raises("next_step_stale", 409, accept, conn, store, settings, wid, log, admin)
    raises("not_found", 404, work_actions.dismiss_next_step, conn, session_id=SESSION, work_item_id="wi-nope",
           triage_id=log["triage_id"], member_id=admin, now=NOW)
    # 대체 경로는 다음 tick 의 처분 — 원래 사람 요청 하나
    worker.tick()
    worker.tick()
    assert requests_of(conn, task_id) == [("fix_needs_information", f"fix_needs_information:{execution_id}")]


# --- 웹 경로 -----------------------------------------------------------------------------


@pytest.fixture
def web(client, capable) -> TestClient:
    """관리자 로그인 — 판단 후보(멤버)에 들도록 판단 시작 전에."""
    return log_in(client)


def admin_of(conn) -> str:
    return conn.execute("SELECT member_id FROM members ORDER BY created_at LIMIT 1").fetchone()["member_id"]


def test_web_accept_applies_the_action_and_redirects(web, conn, store, worker):
    task_id, _, wid, log = propose(conn, store, worker, {"type": "human", "question": "재현 금액?"})

    response = web.post("/work/RUN-1/next-step/accept", data={"triage_id": log["triage_id"]},
                        follow_redirects=False)

    assert response.status_code == 303 and response.headers["location"] == "/tasks?open=RUN-1"
    assert requests_of(conn, task_id) == [("next_step_human", f"next_step_human:{log['triage_id']}")]
    row = log_row(conn, log["triage_id"])
    assert (row["handling"], row["handled_by_member_id"]) == ("accepted", admin_of(conn))
    again = web.post("/work/RUN-1/next-step/accept", data={"triage_id": log["triage_id"]},
                     headers={"Accept": "text/html"}, follow_redirects=False)
    assert again.status_code == 409 and "next_step_stale" in again.text


def test_web_dismiss_records_dismissed(web, conn, store, worker):
    _, _, wid, log = propose(conn, store, worker, {"type": "human", "question": "재현 금액?"})

    response = web.post("/work/RUN-1/next-step/dismiss", data={"triage_id": log["triage_id"]},
                        follow_redirects=False)

    assert response.status_code == 303 and response.headers["location"] == "/tasks?open=RUN-1"
    assert log_row(conn, log["triage_id"])["handling"] == "dismissed"


def test_web_next_step_routes_need_login_and_same_origin(client, web, conn, store, worker):
    _, _, wid, log = propose(conn, store, worker, {"type": "human", "question": "재현 금액?"})
    for path in ("/work/RUN-1/next-step/accept", "/work/RUN-1/next-step/dismiss"):
        response = web.post(path, data={"triage_id": log["triage_id"]},
                            headers={"Origin": "http://evil.example", "Accept": "text/html"}, follow_redirects=False)
        assert response.status_code == 403 and "forbidden_origin" in response.text
        stranger = TestClient(client.app)
        response = stranger.post(path, data={"triage_id": log["triage_id"]}, follow_redirects=False)
        assert response.status_code == 303 and response.headers["location"] == "/login"
    assert log_row(conn, log["triage_id"])["handling"] is None
    missing = web.post("/work/RUN-9/next-step/accept", data={"triage_id": log["triage_id"]},
                       headers={"Accept": "text/html"}, follow_redirects=False)
    assert missing.status_code == 404


def test_handling_is_recorded_once_even_when_two_presses_race(conn, store, settings, worker, admin):
    """공통 검사를 둘 다 지난 두 번째 쓰기 — 처리 기록의 조건부 UPDATE 가 막고 사람 요청은 하나."""
    task_id, _, wid, log = propose(conn, store, worker, {"type": "human", "question": "확인"})
    repo.apply_next_human(conn, triage_id=log["triage_id"], member_id=admin, cause_task_id=task_id, question="확인",
                          now=NOW)
    with pytest.raises(NextStepHandled):
        repo.apply_next_human(conn, triage_id=log["triage_id"], member_id=admin, cause_task_id=task_id,
                              question="확인", now=NOW)
    with pytest.raises(NextStepHandled):
        repo.mark_next_step_accepted(conn, log["triage_id"], member_id=admin, now=NOW)
    assert len(requests_of(conn, task_id)) == 1
