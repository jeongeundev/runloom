# ruff: noqa: F811 — test_task_cycle·test_triage_runs·test_worker_next_step 픽스처를 가져와 인자로 쓴다
"""업무 목록·상세 API(`/api/v1/work-items`) — phase 24 step 6 (ADR-0032, ARCHITECTURE "업무 목록 응답"·"업무 상세 응답",
CONTRACT 18.1·18.2).

목록은 웹 업무 화면(`/tasks`)과 같은 함수(`views.work_list_context`), 상세는 웹 패널과 같은 함수(`views.work_panel_context`)
로 만든다 — 테스트는 같은 데이터에서 웹 화면과 API 가 같은 키 집합·건수·후보·행동을 내는지 본다.
"""

import re

import pytest
from fastapi.testclient import TestClient

from workflow.adapters import repo
from workflow.domain.work_list import QUICK_FILTERS

from .conftest import ADMIN_EMAIL, NOW, SESSION, bearer, log_in, log_in_member, seed_agents
from .test_internal_request_api import setup_request, work_url
from .test_task_cycle import (  # noqa: F401 — 픽스처
    FIX,
    REVIEW,
    clock,
    config,
    cycle,
    executions,
    import_issue,
    make_worker,
    settings,
    worker,
)
from .test_triage_runs import judge, new_issue  # noqa: F401 — 픽스처
from .test_web_triage import propose as propose_triage
from .test_web_work_list import AGENT, add_source, assign, counts_in, keys_in, new_work, set_status
from .test_work_actions_next_step import propose as propose_next_step
from .test_worker_next_step import capable  # noqa: F401 — 픽스처

PATH = "/api/v1/work-items"
DETAIL_KEYS = {"work", "stages", "links", "direct", "open_requests", "responses", "triage", "next_step",
               "pull_requests", "internal_requests", "events", "assignee_candidates", "actions"}
ITEM_KEYS = {"key", "title", "kind", "kind_label", "priority", "assignee", "status", "status_reason", "my_turn",
             "next_action", "triage", "next_step", "repository", "source", "updated_at", "closed_at"}


def error(response, status: int, code: str, field: str | None = None) -> dict:
    assert response.status_code == status, response.text
    body = response.json()
    assert (body["code"], body["field"]) == (code, field), body
    return body


def member_named(conn, name: str) -> str:
    return next(m["member_id"] for m in repo.list_members(conn, SESSION) if m["display_name"] == name)


def admin_id(conn) -> str:
    return repo.find_member_by_email(conn, SESSION, ADMIN_EMAIL)["member_id"]


def token_for(conn, member_id: str, scope: str = "read") -> str:
    _, token = repo.issue_member_token(conn, session_id=SESSION, member_id=member_id, name="CLI", scope=scope, now=NOW)
    return token


# --- 목록 ------------------------------------------------------------------------------------------------


@pytest.fixture
def admin(client, conn) -> TestClient:
    from workflow.server.auth import ensure_workspace

    ensure_workspace(conn, NOW)
    seed_agents(conn)
    return log_in(client)


@pytest.fixture
def member(app, admin) -> TestClient:
    return log_in_member(TestClient(app), display_name="김멤버")


@pytest.fixture
def people(conn, admin, member) -> None:
    """담당 없음 2 · 관리자 1 · 김멤버 1(내 차례) · 에이전트 1 · 오래전 끝난 1(RUN-6)."""
    low, _ = new_work(conn, "담당 없음 낮음", priority="low", updated="2026-09-20T05:00:00Z")
    new_work(conn, "담당 없음 높음", priority="high", updated="2026-09-20T01:00:00Z")
    mine, _ = new_work(conn, "내 업무")
    theirs, _ = new_work(conn, "멤버 업무")
    agent, _ = new_work(conn, "에이전트 업무")
    old, _ = new_work(conn, "오래전 끝남")
    assign(conn, mine, "member", admin_id(conn))
    assign(conn, theirs, "member", member_named(conn, "김멤버"))
    assign(conn, agent, "agent", AGENT)
    set_status(conn, theirs, "내 차례", "사람 요청 — 어느 쪽으로 고칠까요?")
    set_status(conn, agent, "에이전트 작업 중", "실행 중")
    set_status(conn, old, "완료", "PR 병합 — #1", closed_at="2026-01-01T00:00:00Z")


def api_keys(client, query: str = "") -> list[str]:
    response = client.get(f"{PATH}{query}")
    assert response.status_code == 200, response.text
    return [item["key"] for item in response.json()["items"]]


def test_list_matches_the_web_list_for_every_filter_and_counts(admin, member, people):
    for client in (admin, member):
        web_html = client.get("/tasks").text
        assert client.get(PATH).json()["counts"] == counts_in(web_html)
        for name in QUICK_FILTERS:
            for closed in ("recent", "all"):
                web_keys = keys_in(client.get(f"/tasks?q={name}&closed={closed}").text)
                assert set(api_keys(client, f"?filter={name}&closed={closed}")) == set(web_keys), (name, closed)
    assert api_keys(member, "?filter=my_turn") == ["RUN-4"]
    assert "RUN-6" not in api_keys(admin) and "RUN-6" in api_keys(admin, "?closed=all")
    assert api_keys(admin) == ["RUN-5", "RUN-4", "RUN-3", "RUN-2", "RUN-1"]  # 키 번호 내림차순, 묶기 없음


def test_list_item_fields(admin, member, conn, people):
    body = member.get(PATH).json()
    assert set(body) == {"items", "counts"}
    items = {item["key"]: item for item in body["items"]}
    assert set(items["RUN-4"]) == ITEM_KEYS
    assert items["RUN-4"] == {
        "key": "RUN-4", "title": "멤버 업무", "kind": "bug_fix", "kind_label": "버그 수정", "priority": "normal",
        "assignee": {"type": "member", "id": member_named(conn, "김멤버"), "name": "김멤버", "active": True},
        "status": "내 차례", "status_reason": "사람 요청 — 어느 쪽으로 고칠까요?", "my_turn": True,
        "next_action": items["RUN-4"]["next_action"], "triage": None, "next_step": None, "repository": None,
        "source": {"type": "manual", "key": None, "url": None}, "updated_at": NOW, "closed_at": None,
    }
    assert items["RUN-1"]["assignee"] is None and items["RUN-1"]["priority"] == "low"
    assert items["RUN-5"]["assignee"] == {"type": "agent", "id": AGENT, "name": "개인 Codex", "active": True}
    # 내 차례는 요청 멤버 기준 — 관리자에게는 거짓
    assert {i["key"]: i["my_turn"] for i in admin.get(PATH).json()["items"]}["RUN-4"] is False


def test_repository_filter_matches_the_web_and_is_case_insensitive(admin, conn):
    add_source(conn, "ghs-0000000a", "acme/web")
    add_source(conn, "ghs-0000000b", "acme/billing")
    new_work(conn, "직접 업무")
    new_work(conn, "웹 이슈", source_type="github", source_id="ghs-0000000a", source_key="acme/web#7")
    new_work(conn, "결제 이슈", source_type="github", source_id="ghs-0000000b", source_key="acme/billing#41")
    body = admin.get(f"{PATH}?repository=ACME/WEB").json()
    assert [i["key"] for i in body["items"]] == ["RUN-2"] == keys_in(admin.get("/tasks?repo=acme/web").text)
    assert body["items"][0]["repository"] == "acme/web"
    assert body["items"][0]["source"] == {"type": "github", "key": "acme/web#7",
                                          "url": body["items"][0]["source"]["url"]}
    assert body["counts"] == counts_in(admin.get("/tasks?repo=acme/web").text)
    assert len(api_keys(admin, "?repository=")) == 3  # 빈 값 = 저장소 필터 없음


def test_unknown_query_values_are_422_with_the_query_name(admin):
    body = error(admin.get(f"{PATH}?filter=mine"), 422, "invalid_field", "filter")
    assert body["message"] == "filter 는 all·my_turn·unassigned·agent_working 중 하나입니다."
    body = error(admin.get(f"{PATH}?closed=old"), 422, "invalid_field", "closed")
    assert body["message"] == "closed 는 recent·all 중 하나입니다."
    body = error(admin.get(f"{PATH}?repository=acme/nope"), 422, "invalid_field", "repository")
    assert body["message"] == "저장소 acme/nope 는 이 워크스페이스 업무 저장소가 아닙니다."
    assert admin.get(f"{PATH}?group=nope&view=board&q=x").status_code == 200  # 화면 쿼리는 읽지 않는다


def test_list_with_a_read_token_and_without_login(app, admin, conn, people):
    token = token_for(conn, admin_id(conn))
    anonymous = TestClient(app)
    response = anonymous.get(PATH, headers=bearer(token))
    assert response.status_code == 200, response.text
    assert [i["key"] for i in response.json()["items"]] == api_keys(admin)
    error(anonymous.get(PATH), 401, "unauthenticated")
    error(anonymous.get(PATH, headers=bearer("wfm_nope")), 401, "unauthenticated")
    error(anonymous.get(f"{PATH}/RUN-1"), 401, "unauthenticated")


# --- 상세 ------------------------------------------------------------------------------------------------


@pytest.fixture
def web_admin(client, judge) -> TestClient:
    """관리자 로그인 — 판단 후보(멤버)에 들도록 판단 시작 전에."""
    return log_in(client)


@pytest.fixture
def issue_work(conn, web_admin) -> str:
    """담당 없는 새 GitHub 업무 RUN-1 — 반환은 업무 ID."""
    return new_issue(conn, 1)


def detail(client, key: str = "RUN-1") -> dict:
    response = client.get(f"{PATH}/{key}")
    assert response.status_code == 200, response.text
    return response.json()


def panel(client, key: str = "RUN-1") -> str:
    response = client.get(f"/work/{key}/panel")
    assert response.status_code == 200, response.text
    return response.text


def web_actions(html: str, key: str = "RUN-1") -> set[str]:
    """웹 패널에 보이는 행동 버튼(폼) → `panel_actions` 이름."""
    forms = {"assign": "assignee", "set_priority": "priority", "start_direct": "direct", "stop_direct": "direct/stop",
             "accept_triage": "triage/accept", "dismiss_triage": "triage/dismiss",
             "accept_next_step": "next-step/accept", "dismiss_next_step": "next-step/dismiss"}
    found = {name for name, path in forms.items() if f'action="/work/{key}/{path}"' in html}
    triage = re.search(rf'action="/work/{key}/triage" class="inline-form">.*?<button([^>]*)>', html, re.S)
    if triage and "disabled" not in triage.group(1):
        found.add("request_triage")
    return found


def candidate(body: dict, agent_id: str) -> dict:
    return next(c for c in body["assignee_candidates"] if c["type"] == "agent" and c["id"] == agent_id)


def agent_option(html: str, agent_id: str) -> str:
    return re.search(rf'<option value="agent:{agent_id}"[^>]*>(.*?)</option>', html, re.S).group(1)


def test_detail_has_the_panel_sections_without_screen_only_values(web_admin, conn, issue_work):
    body = detail(web_admin)
    assert set(body) == DETAIL_KEYS
    work = body["work"]
    assert set(work) == ITEM_KEYS | {"request", "created_at", "turn_recipients", "form_fields"}
    assert (work["key"], work["kind"], work["repository"], work["assignee"]) == ("RUN-1", "bug_fix", "acme/billing", None)
    assert work["turn_recipients"] == [] and work["source"]["type"] == "github"
    (stage,) = body["stages"]
    assert set(stage) == {"title", "kind", "kind_label", "status", "delegatable", "attempts", "created_at"}
    assert set(stage["status"]) == {"label", "reason"}
    assert body["direct"] is None and body["open_requests"] == [] and body["internal_requests"] == []
    # 화면 전용 값·내부 id 는 없다
    for name in ("work_item_id", "list_state", "close_href", "submission_key", "directory_choices",
                 "directory_revision", "member_id", "response_id", "judgment_submission_key"):
        assert f'"{name}"' not in str(body).replace("'", '"'), name
    assert issue_work not in str(body)


def test_detail_of_unknown_or_malformed_key_is_404(web_admin, issue_work):
    for key in ("RUN-99", "run-1", "RUN-0", "RUN-01", issue_work):
        body = error(web_admin.get(f"{PATH}/{key}"), 404, "not_found", "key")
        assert body["message"] == f"업무 {key}을 찾을 수 없습니다."


def test_candidates_carry_online_and_owner_approval_like_the_panel(app, web_admin, conn, cycle, issue_work):
    owner = log_in_member(TestClient(app), display_name="이소유")
    other = log_in_member(TestClient(app), display_name="김맡김")
    conn.execute("UPDATE connectors SET owner_member_id = ? WHERE connector_id = ?",
                 (member_named(conn, "이소유"), cycle["billing"]))
    conn.execute("UPDATE agents SET delegation_policy = 'owner_approval' WHERE agent_id = ?", (FIX,))
    for client, approval in ((other, True), (web_admin, True), (owner, False)):
        found = candidate(detail(client), FIX)
        assert found == {"type": "agent", "id": FIX, "name": FIX, "online": True, "needs_owner_approval": approval}
        assert ("승인 필요" in agent_option(panel(client), FIX)) is approval

    conn.execute("UPDATE agents SET connection_state = 'offline' WHERE agent_id = ?", (FIX,))
    assert candidate(detail(owner), FIX)["online"] is False
    assert "꺼짐" in agent_option(panel(owner), FIX)

    body = detail(web_admin)
    members = [c for c in body["assignee_candidates"] if c["type"] == "member"]
    assert {c["name"] for c in members} == {m["display_name"] for m in repo.list_members(conn, SESSION)
                                             if m["disabled_at"] is None}
    assert all(set(c) == {"type", "id", "name"} for c in members)
    assert body["assignee_candidates"][:len(members)] == members  # 멤버 먼저
    agents = [c["id"] for c in body["assignee_candidates"] if c["type"] == "agent"]
    assert FIX in agents and REVIEW not in agents  # 패널과 같은 후보 함수


def test_member_actions_match_the_panel_buttons(app, web_admin, conn, issue_work):
    member = log_in_member(TestClient(app), display_name="김멤버")
    for client in (member, web_admin):
        actions = detail(client)["actions"]
        assert set(actions) == web_actions(panel(client))
        assert {"assign", "set_priority", "start_direct"} <= set(actions)
    conn.execute("UPDATE work_items SET status = '종료', closed_at = ? WHERE work_item_id = ?", (NOW, issue_work))
    assert set(detail(member)["actions"]) == web_actions(panel(member))
    assert not {"assign", "set_priority", "start_direct"} & set(detail(member)["actions"])


def test_triage_proposal_is_in_the_detail(web_admin, conn, store, settings, worker, issue_work):
    log = propose_triage(conn, store, settings, worker, issue_work, confidence=0.62, proceed="needs_check",
                         missing_information=["재현 금액"])
    body = detail(web_admin)
    triage = body["triage"]
    assert (triage["state"], triage["triage_id"], triage["criteria_version"], triage["agent_name"]) == (
        "proposed", log["triage_id"], 1, FIX)
    proposal = triage["proposal"]
    assert (proposal["proceed"], proposal["confidence"], proposal["missing"]) == ("확인 필요", "0.62", ["재현 금액"])
    assert set(proposal) == {"proceed", "confidence", "kind_line", "assignee", "predecessors", "reasons", "missing",
                             "acceptable"}
    assert triage["unavailable_reason"] is None and triage["handled_by"] is None
    assert {"accept_triage", "dismiss_triage"} <= set(body["actions"])
    assert set(body["actions"]) == web_actions(panel(web_admin))
    assert body["work"]["triage"] == "판단 제안"


def test_next_step_proposal_is_in_the_detail(client, capable, conn, store, worker):
    admin = log_in(client)
    _, _, _, log = propose_next_step(conn, store, worker, {"type": "human", "question": "재현 금액은 얼마인가요?"})
    body = detail(admin)
    found = body["next_step"]
    assert (found["state"], found["triage_id"], found["agent_name"]) == ("proposed", log["triage_id"], FIX)
    assert found["cause_line"] == "버그 수정 결과 needs_information"
    assert found["proposal"]["question"] == "재현 금액은 얼마인가요?" and found["proposal"]["confidence"] == "0.82"
    assert found["handled_by"] is None and found["failed_label"] is None
    assert {"accept_next_step", "dismiss_next_step"} <= set(body["actions"])
    assert set(body["actions"]) == web_actions(panel(admin))


def test_internal_request_summary_is_in_the_detail(logged_in_client, conn):
    _, work_id, request_body = setup_request(logged_in_client, conn)
    created = logged_in_client.post(work_url(conn, work_id), json=request_body).json()
    key = f"RUN-{repo.get_work_item(conn, SESSION, work_id)['key_number']}"
    (summary,) = detail(logged_in_client, key)["internal_requests"]
    assert summary == {
        "request_id": created["request_id"], "system_id": "billing", "request_kind": "investigation",
        "purpose": "에러 원인을 확인해 주세요.", "phase_label": "수락 대기", "requester_name": summary["requester_name"],
        "recipient_name": "API 담당", "problem": None, "created_at": created["created_at"], "returned_at": None,
    }


def test_detail_with_a_read_token_has_no_secrets(app, web_admin, conn, issue_work):
    token = token_for(conn, admin_id(conn))
    response = TestClient(app).get(f"{PATH}/RUN-1", headers=bearer(token))
    assert response.status_code == 200, response.text
    assert response.json() == detail(web_admin)
    for text in (response.text, web_admin.get(PATH).text):
        assert "wfm_" not in text and "wfc_" not in text and "token_sha256" not in text
        for (sha,) in conn.execute("SELECT token_sha256 FROM member_tokens"):
            assert sha not in text
        for (ref,) in conn.execute("SELECT token_sha256 FROM connectors"):
            assert ref not in text
