"""러너·에이전트 소유자와 해제 권한 (phase 15 step 10, ADR-0021, ARCHITECTURE "팀 — phase 15" 러너 소유자).

연결 코드를 발급한 멤버가 교환 뒤 러너(연결 프로그램) 소유자가 되고, 에이전트 소유자는 그 러너에서 읽는다.
해제·에이전트 삭제·코드 취소는 소유자 본인 또는 관리자. 소유자 없는(v10 이전) 러너는 관리자만.
"""

import re

import pytest
from fastapi.testclient import TestClient

from workflow.adapters import repo

from .conftest import NOW, SESSION, log_in, log_in_member


@pytest.fixture
def admin(app):
    return log_in(TestClient(app))


@pytest.fixture
def kim(app, admin):
    return log_in_member(TestClient(app), display_name="김멤버")


@pytest.fixture
def lee(app, admin):
    return log_in_member(TestClient(app), display_name="이멤버")


def member_id(conn, name: str) -> str:
    return next(m["member_id"] for m in repo.list_members(conn, SESSION) if m["display_name"] == name)


def issue_and_exchange(app, owner: TestClient, conn) -> tuple[str, str]:
    """owner 가 운영자 화면에서 코드를 발급하고, 러너가 계약 v1 그대로 교환한다."""
    before = {c["code"] for c in repo.list_connect_codes(conn)}
    assert owner.post("/operator/connect-codes").status_code == 200
    (code,) = {c["code"] for c in repo.list_connect_codes(conn)} - before
    response = TestClient(app).post("/connector/exchange", json={"contract_version": 1, "connect_code": code})
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"connector_id", "token"}  # 교환 응답 모양 불변
    return body["connector_id"], body["token"]


def legacy_runner(conn) -> tuple[str, str]:
    """v10 에서 넘어온 러너 — 발급자 없는 코드로 교환(소유자 없음)."""
    return repo.exchange_connect_code(conn, repo.issue_connect_code(conn, NOW), NOW)


def seed_agent(conn, agent_id: str, connector_id: str | None) -> None:
    repo.upsert_agent(conn, {
        "agent_id": agent_id,
        "name": f"에이전트 {agent_id}",
        "owner_scope": "personal",
        "connection_type": "local",
        "local_registration_id": f"local-{agent_id}",
        "connector_id": connector_id,
        "capabilities": [{"code": "code.fix", "scope": {"repository_id": "repo-x"}}],
    })
    repo.register_session_agent(conn, SESSION, agent_id, NOW)


def runner_row(text: str, connector_id: str) -> str:
    start = text.index(f'data-runner="{connector_id}"')
    return text[start:text.index("</tr>", start)]


def revoke(client: TestClient, connector_id: str):
    return client.post(f"/operator/connectors/{connector_id}/revoke", follow_redirects=False)


# --- 소유자 기록·표시 -------------------------------------------------------------------------------


def test_issuer_becomes_runner_and_agent_owner(app, kim, conn):
    connector_id, _ = issue_and_exchange(app, kim, conn)
    seed_agent(conn, "agent-kim", connector_id)

    assert repo.connector_owner(conn, connector_id) == member_id(conn, "김멤버")
    assert "소유자 김멤버" in runner_row(kim.get("/connect?tab=team").text, connector_id)
    team_tab = kim.get("/connect?tab=team").text
    assert "소유자 김멤버" in team_tab[team_tab.index("<h2>에이전트</h2>"):team_tab.index("<h2>러너</h2>")]  # 에이전트 목록
    assert "소유자 김멤버" in kim.get("/agents/agent-kim").text


def test_runner_without_owner_is_admin_managed(admin, conn):
    connector_id, _ = legacy_runner(conn)
    seed_agent(conn, "agent-old", connector_id)
    seed_agent(conn, "agent-api", None)

    assert "관리자 관리" in runner_row(admin.get("/connect?tab=team").text, connector_id)
    assert admin.get("/agents/agent-old").text.count("관리자 관리") == 1
    assert "관리자 관리" in admin.get("/agents/agent-api").text


def test_disabled_owner_runner_keeps_running_and_says_so(app, admin, kim, conn):
    connector_id, token = issue_and_exchange(app, kim, conn)
    seed_agent(conn, "agent-kim", connector_id)

    assert admin.post(f"/team/members/{member_id(conn, '김멤버')}/disable", follow_redirects=False).status_code == 303

    assert repo.authenticate_connector(conn, token) == connector_id
    row = runner_row(admin.get("/connect?tab=team").text, connector_id)
    assert "소유자 김멤버" in row and "소유자 비활성" in row
    assert "소유자 비활성" in admin.get("/agents/agent-kim").text


# --- 러너 해제 ---------------------------------------------------------------------------------------


def test_owner_revokes_own_runner(app, kim, conn):
    connector_id, token = issue_and_exchange(app, kim, conn)
    assert f'action="/operator/connectors/{connector_id}/revoke"' in kim.get("/connect?tab=team").text

    response = revoke(kim, connector_id)

    assert (response.status_code, response.headers["location"]) == (303, "/connect?tab=team")
    assert repo.authenticate_connector(conn, token) is None


def test_other_member_cannot_revoke(app, kim, lee, conn):
    connector_id, token = issue_and_exchange(app, kim, conn)
    assert f"/operator/connectors/{connector_id}/revoke" not in lee.get("/connect?tab=team").text

    response = revoke(lee, connector_id)

    assert response.status_code == 403 and "<code>forbidden</code>" in response.text
    assert repo.authenticate_connector(conn, token) == connector_id


def test_admin_revokes_anyone_runner(app, admin, kim, conn):
    connector_id, token = issue_and_exchange(app, kim, conn)
    assert f'action="/operator/connectors/{connector_id}/revoke"' in admin.get("/connect?tab=team").text

    assert revoke(admin, connector_id).status_code == 303
    assert repo.authenticate_connector(conn, token) is None


def test_runner_without_owner_only_admin_revokes(admin, kim, conn):
    connector_id, token = legacy_runner(conn)
    assert f"/operator/connectors/{connector_id}/revoke" not in kim.get("/connect?tab=team").text

    assert revoke(kim, connector_id).status_code == 403
    assert repo.authenticate_connector(conn, token) == connector_id
    assert revoke(admin, connector_id).status_code == 303
    assert repo.authenticate_connector(conn, token) is None


def test_revoke_unknown_or_already_revoked_runner(admin, kim, conn):
    assert revoke(admin, "conn-none").status_code == 404
    assert revoke(kim, "conn-none").status_code == 403  # 소유자 없음 = 관리자만 — 존재를 알리지 않는다
    connector_id, _ = legacy_runner(conn)
    assert revoke(admin, connector_id).status_code == 303
    assert revoke(admin, connector_id).status_code == 404
    assert "해제됨" in runner_row(admin.get("/connect?tab=team").text, connector_id)


# --- 에이전트 삭제 ------------------------------------------------------------------------------------


def test_agent_delete_follows_runner_owner(app, admin, kim, lee, conn):
    connector_id, _ = issue_and_exchange(app, kim, conn)
    seed_agent(conn, "agent-kim", connector_id)
    seed_agent(conn, "agent-kim-2", connector_id)
    assert 'action="/operator/agents/agent-kim/delete"' in kim.get("/connect?tab=advanced").text
    assert "/operator/agents/agent-kim/delete" not in lee.get("/connect?tab=advanced").text

    assert lee.post("/operator/agents/agent-kim/delete", follow_redirects=False).status_code == 403
    assert repo.get_agent(conn, "agent-kim") is not None
    assert kim.post("/operator/agents/agent-kim/delete", follow_redirects=False).status_code == 303
    assert repo.get_agent(conn, "agent-kim") is None
    assert admin.post("/operator/agents/agent-kim-2/delete", follow_redirects=False).status_code == 303
    assert repo.get_agent(conn, "agent-kim-2") is None


def test_agent_without_owner_only_admin_deletes(admin, kim, conn):
    connector_id, _ = legacy_runner(conn)
    seed_agent(conn, "agent-old", connector_id)

    assert kim.post("/operator/agents/agent-old/delete", follow_redirects=False).status_code == 403
    assert repo.get_agent(conn, "agent-old") is not None
    assert admin.post("/operator/agents/agent-old/delete", follow_redirects=False).status_code == 303


# --- 연결 코드 목록·취소 --------------------------------------------------------------------------------


def test_member_sees_and_revokes_only_own_codes(app, admin, kim, lee, conn):
    kim.post("/operator/connect-codes")
    lee.post("/operator/connect-codes")
    admin.post("/operator/connect-codes")
    kim_code = repo.list_connect_codes(conn, issued_by_member_id=member_id(conn, "김멤버"))[0]["code"]
    lee_code = repo.list_connect_codes(conn, issued_by_member_id=member_id(conn, "이멤버"))[0]["code"]
    legacy = repo.issue_connect_code(conn, NOW)

    text = kim.get("/connect?tab=advanced").text
    assert kim_code in text and lee_code not in text and legacy not in text
    admin_text = admin.get("/connect?tab=advanced").text
    assert kim_code in admin_text and lee_code in admin_text and legacy in admin_text

    assert lee.post(f"/operator/connect-codes/{kim_code}/revoke", follow_redirects=False).status_code == 403
    assert kim.post(f"/operator/connect-codes/{legacy}/revoke", follow_redirects=False).status_code == 403
    assert kim.post(f"/operator/connect-codes/{kim_code}/revoke", follow_redirects=False).status_code == 303
    assert admin.post(f"/operator/connect-codes/{lee_code}/revoke", follow_redirects=False).status_code == 303
    assert admin.post(f"/operator/connect-codes/{legacy}/revoke", follow_redirects=False).status_code == 303
    revoked = {c["code"] for c in repo.list_connect_codes(conn) if c["revoked_at"] is not None}
    assert revoked == {kim_code, lee_code, legacy}


def test_attach_runner_card_records_issuer(app, kim, conn):
    from .test_web_roles import SOURCE, config

    repo.save_github_source(conn, SESSION, config(), NOW)
    assert kim.post(f"/operator/github/sources/{SOURCE}/runner").status_code == 200
    (row,) = repo.list_connect_codes(conn)
    assert row["issued_by_member_id"] == member_id(conn, "김멤버")


# --- 맡기기 정책 (phase 17 step 9, ARCHITECTURE "사람 사이 인계 — phase 17" 소유자와 맡기기 정책) ---------------


def set_policy(client: TestClient, agent_id: str, policy: str):
    return client.post(f"/agents/{agent_id}/delegation-policy", data={"policy": policy}, follow_redirects=False)


def policy_of(conn, agent_id: str) -> str:
    return repo.get_agent(conn, agent_id)["delegation_policy"]


def agent_row(text: str, agent_id: str) -> str:
    start = text.index(f'data-agent="{agent_id}"')
    return text[start:text.index("</tr>", start)]


def test_owner_sets_the_delegation_policy(app, kim, conn):
    connector_id, _ = issue_and_exchange(app, kim, conn)
    seed_agent(conn, "agent-kim", connector_id)
    row = agent_row(kim.get("/connect?tab=team").text, "agent-kim")
    assert 'action="/agents/agent-kim/delegation-policy"' in row
    assert re.findall(r'<option value="(\w+)"[^>]*>([^<]*)</option>', row) == [
        ("run", "바로 실행"), ("owner_approval", "내 승인 뒤 실행")]

    response = set_policy(kim, "agent-kim", "owner_approval")

    assert (response.status_code, response.headers["location"]) == (303, "/connect?tab=team")
    assert policy_of(conn, "agent-kim") == "owner_approval"
    row = agent_row(kim.get("/connect?tab=team").text, "agent-kim")
    assert '<option value="owner_approval" selected>' in row
    assert set_policy(kim, "agent-kim", "run").status_code == 303
    assert policy_of(conn, "agent-kim") == "run"


def test_admin_sets_anyone_policy_and_shared_agents(app, admin, kim, conn):
    connector_id, _ = issue_and_exchange(app, kim, conn)
    seed_agent(conn, "agent-kim", connector_id)
    seed_agent(conn, "agent-api", None)
    assert set_policy(admin, "agent-kim", "owner_approval").status_code == 303
    assert set_policy(admin, "agent-api", "owner_approval").status_code == 303
    assert (policy_of(conn, "agent-kim"), policy_of(conn, "agent-api")) == ("owner_approval", "owner_approval")


def test_other_member_sees_the_policy_read_only_and_is_refused(app, kim, lee, conn):
    connector_id, _ = issue_and_exchange(app, kim, conn)
    seed_agent(conn, "agent-kim", connector_id)
    seed_agent(conn, "agent-api", None)
    assert set_policy(kim, "agent-kim", "owner_approval").status_code == 303

    page = lee.get("/connect?tab=team").text
    row = agent_row(page, "agent-kim")
    assert "/delegation-policy" not in row and "<select" not in row and "내 승인 뒤 실행" in row
    assert "/delegation-policy" not in agent_row(page, "agent-api")  # 공용은 관리자만
    assert "바로 실행" in agent_row(page, "agent-api")

    for agent_id in ("agent-kim", "agent-api"):
        response = set_policy(lee, agent_id, "run")
        assert response.status_code == 403 and "<code>forbidden</code>" in response.text
        assert "러너 소유자나 관리자만 바꿀 수 있습니다." in response.text
    assert set_policy(kim, "agent-api", "owner_approval").status_code == 403
    assert (policy_of(conn, "agent-kim"), policy_of(conn, "agent-api")) == ("owner_approval", "run")


@pytest.mark.parametrize("value", ["", "always", "RUN", "owner_approval "])
def test_unknown_policy_value_is_422(app, kim, conn, value):
    connector_id, _ = issue_and_exchange(app, kim, conn)
    seed_agent(conn, "agent-kim", connector_id)
    response = set_policy(kim, "agent-kim", value)
    assert response.status_code == 422 and "<code>invalid_field</code>" in response.text
    assert policy_of(conn, "agent-kim") == "run"


def test_unknown_or_other_workspace_agent_is_404(admin, conn):
    response = set_policy(admin, "agent-nope", "run")
    assert response.status_code == 404 and "<code>not_found</code>" in response.text
    repo.upsert_agent(conn, {"agent_id": "agent-elsewhere", "name": "남의 것", "owner_scope": "personal",
                             "connection_type": "local", "local_registration_id": "local-elsewhere",
                             "capabilities": [{"code": "code.fix", "scope": {"repository_id": "repo-x"}}]})
    response = set_policy(admin, "agent-elsewhere", "owner_approval")
    assert response.status_code == 404 and "<code>not_found</code>" in response.text
    assert policy_of(conn, "agent-elsewhere") == "run"


def test_policy_change_needs_login_and_same_origin(app, kim, conn, client):
    connector_id, _ = issue_and_exchange(app, kim, conn)
    seed_agent(conn, "agent-kim", connector_id)
    response = client.post("/agents/agent-kim/delegation-policy", data={"policy": "owner_approval"},
                           follow_redirects=False)
    assert (response.status_code, response.headers["location"]) == (303, "/login")
    response = kim.post("/agents/agent-kim/delegation-policy", data={"policy": "owner_approval"},
                        headers={"Origin": "http://evil.example", "Accept": "text/html"}, follow_redirects=False)
    assert response.status_code == 403
    assert policy_of(conn, "agent-kim") == "run"
