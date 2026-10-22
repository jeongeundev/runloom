# ruff: noqa: F811 — test_task_cycle 픽스처(cycle·worker)를 가져와 인자로 쓴다
"""사람용 리포트 API(`/api/v1/work-items/{key}/reports`·`/api/v1/reports/…`) — phase 25 step 8 (ADR-0033, ARCHITECTURE
"인계 리포트 — phase 25" 의 "사람용 API"·"서버 함수", D7·D8·D12·D13·D29).

업무 순환(bug_fix → code_review)으로 본다: 수정 결과가 판정을 통과하면 수정 Agent(FIX, 러너 billing — 주인 '보낸이')가
원본 리포트를 쓰고, 다음 단계는 검토 Agent(REVIEW — 러너 shop 으로 옮겨 주인 '받는이')다. 행동은 라우트가 아니라
`server/handoff_reports.py` 의 행동 함수가 판정한다(웹 step 9 가 같이 쓴다).
"""

import json

import pytest
from fastapi.testclient import TestClient

from workflow.adapters import repo
from workflow.contracts.v1 import format_work_key
from workflow.server import handoff_reports

from .conftest import bearer, log_in, log_in_member
from .test_handoff_reports import FakeGitHub, _detect, capable, fix_passed
from .test_task_cycle import (  # noqa: F401 — 픽스처
    C1,
    FIX,
    FIX_SHOP,
    NOW,
    REG_REVIEW,
    REVIEW,
    SESSION,
    clock,
    cycle,
    executions,
    make_worker,
    settings,
    worker,
)
from .test_work_api import error, member_named, token_for

pytestmark = pytest.mark.usefixtures("cycle")

VIEW_KEYS = {"report_id", "job_type", "status", "reason", "stage", "source", "agent", "requested_by", "base_report_id",
             "stale", "created_at", "finished_at", "report", "evidence_check", "pull_requests", "pr_evidence_error",
             "decisions", "parties", "actions"}


def report_body(report_id: str, job_type: str = "original") -> dict:
    if job_type == "tailored":
        changes = [{"id": "t1", "text": "받는 사람 관점 요약", "why": None, "evidence": ["claim:c1"]}]
        return {"contract_version": 1, "report_id": report_id, "job_type": "tailored",
                "sections": {"changes": changes, "decisions": [], "open_questions": [], "to_confirm": [],
                             "constraints": [], "remaining_work": []}, "diagram": None}
    changes = [
        {"id": "c1", "text": "쿠폰 중복 적용을 막음", "why": "재현 테스트가 실패했다", "evidence": [f"commit:{C1[:7]}"]},
        {"id": "c2", "text": "없는 산출물을 인용", "why": None, "evidence": ["artifact:art-unknown"]},
    ]
    to_confirm = [{"id": "q1", "text": "할인 상한 기준 확인", "why": None, "evidence": ["source:work"]}]
    return {"contract_version": 1, "report_id": report_id, "job_type": "original",
            "sections": {"changes": changes, "decisions": [], "open_questions": [], "to_confirm": to_confirm,
                         "constraints": [], "remaining_work": []}, "diagram": "graph TD; A-->B"}


def finish_job(conn, store, connector_id: str) -> str:
    """그 러너의 가장 이른 리포트 작업을 claim·제출 — 반환은 report_id."""
    job = handoff_reports.lease_job(conn, connector_id, now=NOW)
    body = json.dumps(report_body(job.report_id, job.job_type)).encode()
    handoff_reports.submit_result(conn, store, connector_id=connector_id, report_id=job.report_id, body=body, now=NOW)
    return job.report_id


@pytest.fixture
def world(app, client, cycle, conn, store, worker) -> dict:
    """관리자(client)·보낸이·받는이·다른이 로그인, 수정 결과의 원본 리포트 done, 검토 단계 착수."""
    clients = {"관리자": log_in(client)}
    for name in ("보낸이", "받는이", "다른이"):
        clients[name] = log_in_member(TestClient(app), display_name=name)
    ids = {name: member_named(conn, name) for name in ("보낸이", "받는이", "다른이")}
    capable(conn, cycle["billing"])
    capable(conn, cycle["shop"])
    fix_task, fix_exec = fix_passed(conn, store, worker)
    conn.execute("UPDATE connectors SET owner_member_id = ? WHERE connector_id = ?", (ids["보낸이"], cycle["billing"]))
    conn.execute("UPDATE connectors SET owner_member_id = ? WHERE connector_id = ?", (ids["받는이"], cycle["shop"]))
    conn.execute("UPDATE agents SET connector_id = ? WHERE agent_id = ?", (cycle["shop"], REVIEW))
    conn.commit()
    report_id = finish_job(conn, store, cycle["billing"])
    work = repo.work_item_of_task(conn, fix_task)
    return {"clients": clients, "ids": ids, "report_id": report_id, "fix_task": fix_task, "fix_exec": fix_exec,
            "key": format_work_key(work["key_number"]), "work_item_id": work["work_item_id"]}


def view(client, report_id: str) -> dict:
    response = client.get(f"/api/v1/reports/{report_id}")
    assert response.status_code == 200, response.text
    return response.json()


def answer(client, report_id: str, item_id: str, body: dict):
    return client.put(f"/api/v1/reports/{report_id}/decisions/{item_id}", json=body)


# --- 목록·상세 ------------------------------------------------------------------------------------------


def test_report_view_has_the_report_evidence_check_parties_and_no_internal_inputs(world, conn):
    body = view(world["clients"]["다른이"], world["report_id"])

    assert set(body) == VIEW_KEYS
    assert (body["report_id"], body["job_type"], body["status"], body["reason"]) == (
        world["report_id"], "original", "done", None)
    assert body["stage"] == {"title": repo.get_task(conn, world["fix_task"])["title"], "kind": "bug_fix",
                             "kind_label": "버그 수정"}
    assert body["source"] == {"attempt_no": 1, "outcome": "ready_for_review"}
    assert body["agent"] == {"name": FIX}
    assert (body["requested_by"], body["base_report_id"], body["stale"]) == (None, None, False)
    assert body["report"] == report_body(world["report_id"])
    claims = body["evidence_check"]["claims"]
    assert claims["c1"] == {"verified": True, "unverified": []}
    assert claims["c2"] == {"verified": False, "unverified": ["artifact:art-unknown"]}  # 근거 확인 안 됨
    assert (body["pull_requests"], body["pr_evidence_error"], body["decisions"]) == ([], None, [])
    assert body["parties"] == {"sender": "보낸이", "recipient": "받는이"}
    assert body["actions"] == []  # 다른 팀원은 읽기만
    text = json.dumps(body, ensure_ascii=False)
    assert "# 인계 리포트" not in text and "wfc_" not in text and "wfm_" not in text  # 요청문·토큰 없음


def test_actions_follow_the_domain_permissions(world):
    clients, report_id = world["clients"], world["report_id"]
    assert view(clients["받는이"], report_id)["actions"] == ["answer", "tailor", "regenerate"]
    assert view(clients["관리자"], report_id)["actions"] == ["answer", "regenerate"]  # 관리자라도 맞춤 풀이는 받는 사람만
    assert view(clients["보낸이"], report_id)["actions"] == ["regenerate"]
    assert "tailor_choices" not in view(clients["보낸이"], report_id)
    assert view(clients["받는이"], report_id)["tailor_choices"] == [
        {"agent_id": REVIEW, "name": REVIEW, "default": True},  # 다음 단계 Agent 가 맨 앞
        {"agent_id": FIX_SHOP, "name": FIX_SHOP, "default": False},
    ]


def test_work_report_list_has_latest_and_previous_per_stage(world):
    clients, key = world["clients"], world["key"]
    first = clients["다른이"].get(f"/api/v1/work-items/{key}/reports")
    assert first.status_code == 200, first.text
    (stage,) = first.json()["stages"]
    assert set(stage) == {"title", "kind", "kind_label", "latest", "previous"}
    assert (stage["kind"], stage["latest"]["report_id"], stage["previous"]) == ("bug_fix", world["report_id"], [])
    assert first.json()["tailored"] == []

    again = clients["보낸이"].post(f"/api/v1/reports/{world['report_id']}/regenerate")
    assert again.status_code == 202, again.text

    (stage,) = clients["다른이"].get(f"/api/v1/work-items/{key}/reports").json()["stages"]
    assert (stage["latest"]["report_id"], stage["latest"]["status"]) == (again.json()["report_id"], "queued")
    assert [p["report_id"] for p in stage["previous"]] == [world["report_id"]]


def test_unknown_work_key_and_report_are_404(world):
    member = world["clients"]["다른이"]
    error(member.get("/api/v1/work-items/RUN-999/reports"), 404, "not_found", "key")
    error(member.get("/api/v1/reports/hrp-000000000000"), 404, "not_found")
    error(member.post("/api/v1/reports/hrp-000000000000/regenerate"), 404, "not_found")


# --- 결정 답 (D13) --------------------------------------------------------------------------------------


def test_recipient_and_admin_answer_and_the_last_answer_wins(world, conn):
    clients, report_id = world["clients"], world["report_id"]

    first = answer(clients["받는이"], report_id, "q1", {"answer": "confirmed"})
    assert first.status_code == 200, first.text
    assert [(d["item_id"], d["answer"], d["note"], d["answered_by"]) for d in first.json()["decisions"]] == [
        ("q1", "confirmed", None, "받는이")]

    second = answer(clients["관리자"], report_id, "q1", {"answer": "changed", "note": "  상한은 1만 원  "})
    assert second.status_code == 200, second.text
    (decision,) = second.json()["decisions"]
    assert (decision["answer"], decision["note"], decision["answered_by"]) == ("changed", "상한은 1만 원", "관리자")
    assert len(repo.list_report_decisions(conn, report_id)) == 1
    events = conn.execute("SELECT type FROM work_item_events WHERE work_item_id = ? AND type = 'report_decision'",
                          (world["work_item_id"],)).fetchall()
    assert len(events) == 2


def test_other_member_and_sender_cannot_answer(world):
    clients, report_id = world["clients"], world["report_id"]
    for name in ("다른이", "보낸이"):
        body = error(answer(clients[name], report_id, "q1", {"answer": "confirmed"}), 403, "forbidden")
        assert body["message"] == "받는 사람이나 관리자만 답할 수 있습니다."


def test_changed_needs_a_note_and_unknown_items_are_404(world):
    recipient, report_id = world["clients"]["받는이"], world["report_id"]
    body = error(answer(recipient, report_id, "q1", {"answer": "changed"}), 422, "invalid_field", "note")
    assert body["message"] == "한 줄 이유를 1~500자로 적어 주세요."
    error(answer(recipient, report_id, "q1", {"answer": "changed", "note": "가" * 501}), 422, "invalid_field", "note")
    error(answer(recipient, report_id, "c1", {"answer": "confirmed"}), 404, "not_found")  # to_confirm 이 아님
    error(answer(recipient, report_id, "q1", {"answer": "maybe"}), 422, "invalid_field", "answer")
    error(answer(recipient, report_id, "q1", {"answer": "confirmed", "extra": 1}), 422, "unknown_field", "extra")


def test_answer_needs_a_done_original(world, conn):
    clients, report_id = world["clients"], world["report_id"]
    again = clients["보낸이"].post(f"/api/v1/reports/{report_id}/regenerate").json()["report_id"]
    error(answer(clients["받는이"], again, "q1", {"answer": "confirmed"}), 409, "report_not_done")


def test_owner_of_the_next_stage_agent_is_the_recipient_and_shared_agent_falls_back_to_the_work_assignee(
        world, conn, cycle):
    clients, report_id, ids = world["clients"], world["report_id"], world["ids"]
    assert answer(clients["받는이"], report_id, "q1", {"answer": "confirmed"}).status_code == 200

    # 다음 단계 Agent 의 러너에 주인이 없으면(공유) 업무 담당 멤버가 받는 사람
    conn.execute("UPDATE connectors SET owner_member_id = NULL WHERE connector_id = ?", (cycle["shop"],))
    conn.execute("UPDATE work_items SET assignee_type = 'member', assignee_id = ? WHERE work_item_id = ?",
                 (ids["다른이"], world["work_item_id"]))
    conn.commit()
    assert view(clients["다른이"], report_id)["parties"] == {"sender": "보낸이", "recipient": "다른이"}
    assert answer(clients["다른이"], report_id, "q1", {"answer": "confirmed"}).status_code == 200
    error(answer(clients["받는이"], report_id, "q1", {"answer": "confirmed"}), 403, "forbidden")


# --- 맞춤 풀이 (D8) -------------------------------------------------------------------------------------


def tailor(client, report_id: str, agent_id: str):
    return client.post(f"/api/v1/reports/{report_id}/tailor", json={"agent_id": agent_id})


def test_recipient_tailors_with_own_agent_and_twice_is_the_same_job(world, conn, store, cycle):
    recipient, report_id = world["clients"]["받는이"], world["report_id"]

    first = tailor(recipient, report_id, REVIEW)

    assert first.status_code == 202, first.text
    body = first.json()
    assert (body["job_type"], body["status"], body["requested_by"], body["base_report_id"], body["stale"]) == (
        "tailored", "queued", "받는이", report_id, False)
    assert body["agent"] == {"name": REVIEW} and body["actions"] == []
    row = repo.get_handoff_report(conn, body["report_id"])
    assert (row["agent_id"], row["connector_id"], row["source_execution_id"]) == (
        REVIEW, cycle["shop"], world["fix_exec"])
    inputs = json.loads(row["inputs_json"])
    assert (inputs["local_registration_id"], inputs["checkout_commit"], inputs["base_commit"]) == (REG_REVIEW, None, None)
    assert inputs["evidence"]["claims"] == ["c1", "c2", "q1"]
    assert inputs["request"].startswith("# 맞춤 풀이: ") and "받는 사람: 받는이" in inputs["request"]

    assert tailor(recipient, report_id, FIX_SHOP).json()["report_id"] == body["report_id"]  # 진행 중이면 같은 행

    # 받는 러너가 쓰고 나면 원본 주장 인용이 확인된다 — 원본을 다시 만들면 옛 리포트 기준
    assert finish_job(conn, store, cycle["shop"]) == body["report_id"]
    done = view(world["clients"]["다른이"], body["report_id"])
    assert done["status"] == "done" and done["evidence_check"]["claims"]["t1"] == {"verified": True, "unverified": []}
    world["clients"]["보낸이"].post(f"/api/v1/reports/{report_id}/regenerate")
    assert view(recipient, body["report_id"])["stale"] is True
    listed = recipient.get(f"/api/v1/work-items/{world['key']}/reports").json()["tailored"]
    assert [t["report_id"] for t in listed] == [body["report_id"]]


def test_tailor_refuses_other_agents_non_recipients_and_unusable_agents(world, conn, cycle):
    clients, report_id = world["clients"], world["report_id"]
    body = error(tailor(clients["받는이"], report_id, FIX), 403, "forbidden")  # 보낸이의 Agent
    assert body["message"] == "내 에이전트만 고를 수 있습니다."
    error(tailor(clients["받는이"], report_id, "agent-none"), 403, "forbidden")
    for name in ("관리자", "보낸이", "다른이"):
        body = error(tailor(clients[name], report_id, REVIEW), 403, "forbidden")
        assert body["message"] == "받는 사람만 맞춤 풀이를 요청할 수 있습니다."

    conn.execute("UPDATE agents SET connection_type = 'api' WHERE agent_id = ?", (FIX_SHOP,))
    conn.commit()
    body = error(tailor(clients["받는이"], report_id, FIX_SHOP), 422, "tailor_unavailable")
    assert body["message"] == "API 연결형 에이전트"
    assert [c["agent_id"] for c in view(clients["받는이"], report_id)["tailor_choices"]] == [REVIEW]

    conn.execute("UPDATE connectors SET capabilities_json = NULL WHERE connector_id = ?", (cycle["shop"],))
    conn.commit()
    body = error(tailor(clients["받는이"], report_id, REVIEW), 422, "tailor_unavailable")
    assert body["message"] == "러너가 리포트를 지원하지 않음 — 러너 재설치 필요"
    assert "tailor" not in view(clients["받는이"], report_id)["actions"]  # 남는 Agent 가 없으면 버튼 없음
    assert conn.execute("SELECT COUNT(*) FROM handoff_reports WHERE job_type = 'tailored'").fetchone()[0] == 0


def test_tailor_needs_a_done_original(world):
    clients, report_id = world["clients"], world["report_id"]
    tailored = tailor(clients["받는이"], report_id, REVIEW).json()["report_id"]
    error(tailor(clients["받는이"], tailored, REVIEW), 409, "not_original")
    again = clients["보낸이"].post(f"/api/v1/reports/{report_id}/regenerate").json()["report_id"]
    error(tailor(clients["받는이"], again, REVIEW), 409, "report_not_done")


# --- 다시 만들기 (D12) ----------------------------------------------------------------------------------


def regenerate(client, report_id: str):
    return client.post(f"/api/v1/reports/{report_id}/regenerate")


def test_sender_recipient_and_admin_regenerate_into_one_active_original(world, conn, cycle):
    clients, report_id = world["clients"], world["report_id"]

    first = regenerate(clients["보낸이"], report_id)

    assert first.status_code == 202, first.text
    new_id = first.json()["report_id"]
    assert new_id != report_id
    row = repo.get_handoff_report(conn, new_id)
    assert (row["job_type"], row["status"], row["agent_id"], row["connector_id"], row["source_execution_id"]) == (
        "original", "queued", FIX, cycle["billing"], world["fix_exec"])
    assert json.loads(row["inputs_json"])["request"].startswith("# 인계 리포트: ")
    for name in ("받는이", "관리자"):  # 진행 중이면 그 행
        assert regenerate(clients[name], report_id).json()["report_id"] == new_id
    assert "regenerate" not in view(clients["보낸이"], new_id)["actions"]
    body = error(regenerate(clients["다른이"], report_id), 403, "forbidden")
    assert body["message"] == "보낸 사람·받는 사람·관리자만 다시 만들 수 있습니다."


def test_regenerate_without_the_cause_agent_is_unavailable_at_once(world, conn):
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute("DELETE FROM agents WHERE agent_id = ?", (FIX,))
    conn.commit()

    body = regenerate(world["clients"]["관리자"], world["report_id"])

    assert body.status_code == 202, body.text
    assert (body.json()["status"], body.json()["reason"], body.json()["agent"]) == (
        "unavailable", "원인 에이전트 없음", None)


def test_regenerate_collects_pr_evidence_again(app, world, conn):
    _detect(conn, world["work_item_id"], 77, NOW)
    conn.commit()
    app.state.github_client = FakeGitHub()

    body = regenerate(world["clients"]["보낸이"], world["report_id"]).json()

    assert body["pull_requests"] == [{"number": 77, "title": "PR 77", "url": "https://github.com/acme/billing/pull/77"}]
    assert body["pr_evidence_error"] is None
    assert json.loads(repo.get_handoff_report(conn, body["report_id"])["inputs_json"])["evidence"]["pulls"] == [77]


def test_regenerate_needs_an_original(world):
    clients, report_id = world["clients"], world["report_id"]
    tailored = tailor(clients["받는이"], report_id, REVIEW).json()["report_id"]
    error(regenerate(clients["관리자"], tailored), 409, "not_original")


# --- 인증 (ADR-0032) ------------------------------------------------------------------------------------


def test_read_token_reads_but_cannot_act_and_no_login_is_401(app, world, conn):
    report_id, recipient = world["report_id"], world["ids"]["받는이"]
    reader = TestClient(app, headers=bearer(token_for(conn, recipient, "read")))
    assert reader.get(f"/api/v1/work-items/{world['key']}/reports").status_code == 200
    assert reader.get(f"/api/v1/reports/{report_id}").json()["actions"] == ["answer", "tailor", "regenerate"]
    error(answer(reader, report_id, "q1", {"answer": "confirmed"}), 403, "token_read_only")
    error(tailor(reader, report_id, REVIEW), 403, "token_read_only")
    error(regenerate(reader, report_id), 403, "token_read_only")

    writer = TestClient(app, headers=bearer(token_for(conn, recipient, "full")))
    assert answer(writer, report_id, "q1", {"answer": "confirmed"}).status_code == 200  # Bearer 는 Origin 검사 없음

    anonymous = TestClient(app)
    error(anonymous.get(f"/api/v1/reports/{report_id}"), 401, "unauthenticated")
    error(anonymous.get(f"/api/v1/work-items/{world['key']}/reports"), 401, "unauthenticated")
    error(regenerate(anonymous, report_id), 401, "unauthenticated")
