# ruff: noqa: F811 — test_task_cycle·test_triage_runs 픽스처(cycle·judge)를 가져와 인자로 쓴다
"""판단 설정 — phase 19 step 8 (ADR-0025, ARCHITECTURE "판단 — phase 19" 경로·자동 시작·화면).

연결 화면 판단 탭(`/connect?tab=triage`): 기준 본문 편집(저장 = 새 버전 행, 같은 본문이면 그대로)·버전 이력·이전 본문 보기,
종류별 자동 시작(사람 처리 20건 미만이면 서버도 켜기 거부, 기준값 0.50~1.00, 변경 = 설정 버전 행 + `config_revision` +1),
저장소 카드 판단 Agent 칸(`code.triage` 능력·맡기기 정책 `run` 만). 모두 `manage_connections`.
판단 Agent 소유자 알림은 하지 않는다 — ADR-0025 사실 10·결정 3(정책 `run` 인 Agent 만 고를 수 있다).
"""

import re

import pytest
from fastapi.testclient import TestClient

from workflow.adapters import repo
from workflow.domain.triage import AutostartSetting
from workflow.domain.triage_criteria import TRIAGE_CRITERIA_V1

from .conftest import ADMIN_EMAIL, log_in, log_in_member
from .test_task_cycle import (  # noqa: F401 — 픽스처
    FIX,
    NOW,
    REVIEW,
    SESSION,
    SOURCE,
    clock,
    config,
    cycle,
    settings,
)
from .test_triage_runs import give_triage, judge  # noqa: F401 — 픽스처


@pytest.fixture
def admin(client, judge) -> TestClient:
    return log_in(client)


def admin_id(conn) -> str:
    return repo.find_member_by_email(conn, SESSION, ADMIN_EMAIL)["member_id"]


def error(response, status: int, code: str) -> None:
    assert response.status_code == status, response.text
    assert code in response.text, response.text


def revision(conn) -> int:
    return repo.get_config_revision(conn, SESSION)


def tab(client, query: str = "") -> str:
    response = client.get(f"/connect?tab=triage{query}")
    assert response.status_code == 200, response.text
    return response.text


def save_criteria(client, body: str, expected_version: int):
    return client.post("/operator/triage/criteria", data={"body": body, "expected_version": str(expected_version)},
                       follow_redirects=False)


def save_autostart(client, kind: str, *, enabled: bool, threshold: str = "0.80"):
    data = {"threshold": threshold} | ({"enabled": "on"} if enabled else {})
    return client.post(f"/operator/triage/autostart/{kind}", data=data, follow_redirects=False)


def seed_handled(conn, kind: str, n: int, *, handling: str = "accepted", state: str = "proposed") -> None:
    """사람이 처리한 판단 로그 행 n 개 — 자격 건수 재료. 업무·단계·실행 행은 이 테스트의 관심이 아니라 외래키를 끄고 넣는다."""
    conn.execute("PRAGMA foreign_keys=OFF")
    for _ in range(n):
        number = conn.execute("SELECT COUNT(*) FROM triage_logs").fetchone()[0]
        handled = state == "proposed" and handling is not None
        conn.execute(
            "INSERT INTO triage_logs (triage_id, session_id, work_item_id, work_revision, task_id, execution_id,"
            " agent_id, trigger, criteria_version, input_sha256, candidates_json, state, result_json, proceed,"
            " confidence, proposed_kind, failed_code, handling, handled_at, final_assignee_type, final_assignee_id,"
            " final_kind, created_at, updated_at)"
            " VALUES (?, ?, ?, 1, ?, ?, ?, 'auto', 1, ?, '{}', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (f"trg-{number:08x}", SESSION, f"wi-{number}", f"task-t{number}", f"exec-t{number}", FIX, "0" * 64,
             state, "{}" if state == "proposed" else None, "ready" if state == "proposed" else None,
             0.9 if state == "proposed" else None, kind, "result_invalid" if state == "failed" else None,
             handling if handled else None, NOW if handled else None,
             "agent" if handled and handling != "dismissed" else None,
             FIX if handled and handling != "dismissed" else None, kind if handled else None, NOW, NOW),
        )
    conn.execute("PRAGMA foreign_keys=ON")


# --- 판단 기준 ------------------------------------------------------------------------------


def test_tab_shows_current_criteria_v1_and_history(admin):
    html = tab(admin)
    assert 'data-tab="triage" class="active"' in html
    assert "판단 기준 v1" in html
    assert '<textarea name="body"' in html
    assert 'name="expected_version" value="1"' in html
    assert "처음 기준" in html  # 시드 행은 쓴 사람이 없다
    assert 'href="/connect?tab=triage&amp;version=1"' in html


def test_saving_new_body_makes_v2_and_bumps_config_revision(admin, conn):
    before = revision(conn)
    response = save_criteria(admin, "새 판단 기준\n- 재현 절차가 있는가", 1)
    assert response.status_code == 303 and response.headers["location"] == "/connect?tab=triage"

    current = repo.current_triage_criteria(conn, SESSION)
    assert (current["version"], current["body"]) == (2, "새 판단 기준\n- 재현 절차가 있는가")
    assert revision(conn) == before + 1
    history = repo.list_triage_criteria(conn, SESSION)
    assert [h["version"] for h in history] == [2, 1]  # 최신순
    assert history[0]["created_by_member_id"] == admin_id(conn)
    assert history[1]["created_by_member_id"] is None

    html = tab(admin)
    assert "판단 기준 v2" in html and 'name="expected_version" value="2"' in html
    assert "관리자" in html  # v2 를 쓴 사람


def test_same_body_keeps_the_version(admin, conn):
    before = revision(conn)
    # 브라우저 textarea 는 줄바꿈을 CRLF 로 보낸다 — 같은 글이면 버전을 올리지 않는다
    response = save_criteria(admin, TRIAGE_CRITERIA_V1.replace("\n", "\r\n"), 1)
    assert response.status_code == 303
    assert repo.current_triage_criteria(conn, SESSION)["version"] == 1
    assert len(repo.list_triage_criteria(conn, SESSION)) == 1
    assert revision(conn) == before


def test_previous_version_body_is_shown_read_only(admin, conn):
    save_criteria(admin, "두 번째 기준", 1)
    html = tab(admin, "&version=1")
    view = re.search(r"<pre[^>]*data-criteria-version=\"1\"[^>]*>(.*?)</pre>", html, re.S)
    assert view is not None
    assert "너는 팀의 새 업무 하나를 보고" in view.group(1) and "두 번째 기준" not in view.group(1)  # v1 본문
    error(admin.get("/connect?tab=triage&version=9"), 404, "not_found")


def test_stale_expected_version_is_409(admin, conn):
    assert save_criteria(admin, "다른 사람 기준", 1).status_code == 303
    error(save_criteria(admin, "내 기준", 1), 409, "stale_criteria")
    assert repo.current_triage_criteria(conn, SESSION)["body"] == "다른 사람 기준"


@pytest.mark.parametrize("body", ["", "   \n", "가" * 8001])
def test_empty_or_too_long_body_is_422(admin, conn, body):
    error(save_criteria(admin, body, 1), 422, "invalid_field")
    assert repo.current_triage_criteria(conn, SESSION)["version"] == 1


def test_criteria_body_is_escaped(admin):
    save_criteria(admin, "<script>alert(1)</script> & 기준", 1)
    html = tab(admin)
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt; &amp; 기준" in html
    view = tab(admin, "&version=2")
    assert "<script>alert(1)</script>" not in view


# --- 자동 시작 -------------------------------------------------------------------------------


def test_handled_counts_only_accepted_and_changed_proposals(admin, conn):
    seed_handled(conn, "bug_fix", 3)
    seed_handled(conn, "bug_fix", 2, handling="changed")
    seed_handled(conn, "bug_fix", 4, handling="dismissed")
    seed_handled(conn, "bug_fix", 1, handling=None)
    seed_handled(conn, "bug_fix", 1, state="failed", handling=None)
    assert repo.triage_handled_counts(conn, SESSION) == {"bug_fix": 5}


def test_autostart_row_shows_count_and_locked_checkbox(admin, conn):
    seed_handled(conn, "bug_fix", 19)
    html = tab(admin)
    row = re.search(r'data-autostart="bug_fix".*?</form>', html, re.S)
    assert row is not None
    assert "판단 기록 19/20" in row.group(0)
    assert re.search(r'<input type="checkbox" name="enabled"[^>]*disabled', row.group(0))
    assert 'data-autostart="triage"' not in html and 'data-autostart="code_review"' not in html  # 시작 못 하는 종류


def test_enabling_with_19_handled_is_refused_by_the_server(admin, conn):
    seed_handled(conn, "bug_fix", 19)
    before = revision(conn)
    response = save_autostart(admin, "bug_fix", enabled=True, threshold="0.90")
    error(response, 409, "triage_autostart_locked")
    assert "판단 기록 19/20 — 20건이 되면 켤 수 있습니다" in response.text
    assert repo.triage_autostart_settings(conn, SESSION).get("bug_fix") is None
    assert revision(conn) == before


def test_enabling_with_20_handled_saves_a_version_and_bumps_revision(admin, conn):
    seed_handled(conn, "bug_fix", 20)
    before = revision(conn)
    response = save_autostart(admin, "bug_fix", enabled=True, threshold="0.90")
    assert response.status_code == 303 and response.headers["location"] == "/connect?tab=triage"
    assert repo.triage_autostart_settings(conn, SESSION)["bug_fix"] == AutostartSetting(
        kind="bug_fix", version=1, enabled=True, threshold=0.9)
    assert revision(conn) == before + 1

    # 같은 값 → 그대로
    save_autostart(admin, "bug_fix", enabled=True, threshold="0.90")
    assert repo.triage_autostart_settings(conn, SESSION)["bug_fix"].version == 1
    assert revision(conn) == before + 1

    # 끄기 → 새 버전
    save_autostart(admin, "bug_fix", enabled=False, threshold="0.90")
    assert repo.triage_autostart_settings(conn, SESSION)["bug_fix"] == AutostartSetting(
        kind="bug_fix", version=2, enabled=False, threshold=0.9)
    history = repo.list_triage_autostart(conn, SESSION, "bug_fix")
    assert [h["version"] for h in history] == [2, 1]
    assert history[0]["created_by_member_id"] == admin_id(conn)

    html = tab(admin)
    row = re.search(r'data-autostart="bug_fix".*?</form>', html, re.S).group(0)
    assert "판단 기록 20/20" in row and "disabled" not in row
    assert 'value="0.90"' in row and "v2" in row


def test_threshold_can_change_while_off_without_handled_records(admin, conn):
    assert save_autostart(admin, "bug_fix", enabled=False, threshold="0.70").status_code == 303
    assert repo.triage_autostart_settings(conn, SESSION)["bug_fix"] == AutostartSetting(
        kind="bug_fix", version=1, enabled=False, threshold=0.7)


@pytest.mark.parametrize("threshold", ["0.49", "1.01", "0.555", "abc", ""])
def test_threshold_out_of_range_is_422(admin, conn, threshold):
    seed_handled(conn, "bug_fix", 20)
    error(save_autostart(admin, "bug_fix", enabled=True, threshold=threshold), 422, "invalid_field")
    assert repo.triage_autostart_settings(conn, SESSION).get("bug_fix") is None


@pytest.mark.parametrize("kind", ["triage", "code_review", "nope"])
def test_autostart_of_triage_or_unstartable_or_unknown_kind_is_404(admin, kind):
    error(save_autostart(admin, kind, enabled=False), 404, "not_found")


# --- 저장소 카드 판단 Agent 칸 -----------------------------------------------------------------


def source_body(conn, **overrides) -> dict:
    current = repo.get_github_source(conn, SESSION, SOURCE).model_dump(mode="json")
    body = {k: v for k, v in current.items() if k not in ("source_id", "config_revision", "installation_id")}
    return {**body, "expected_revision": current["config_revision"], **overrides}


def triage_select(html: str) -> str:
    found = re.search(r'<select[^>]*name="triage_agent_id".*?</select>', html, re.S)
    assert found is not None
    return found.group(0)


def test_card_select_lists_run_policy_agents_with_triage_capability(admin, conn):
    give_triage(conn, REVIEW, "billing")
    repo.set_delegation_policy(conn, SESSION, REVIEW, "owner_approval", member_id=None, now=NOW)
    select = triage_select(admin.get("/connect?tab=sources").text)
    assert "없음 — 판단하지 않음" in select
    assert f'value="{FIX}" selected' in select  # judge 픽스처가 FIX 로 저장
    assert f'value="{REVIEW}"' not in select  # 정책 owner_approval 은 후보가 아니다


def test_card_match_rows_show_triage_agent(admin):
    html = admin.get("/connect?tab=sources").text
    assert re.search(rf"판단 에이전트</span><span><span class=\"mono\">{FIX}</span> \(설정\)", html)


def test_saving_triage_agent_bumps_revisions(admin, conn):
    repo.save_github_source(conn, SESSION, config(review_agent_id=REVIEW), NOW)  # 판단 Agent 없음
    before = revision(conn)
    response = admin.put(f"/github/sources/{SOURCE}", json=source_body(conn, triage_agent_id=FIX))
    assert response.status_code == 200, response.text
    saved = repo.get_github_source(conn, SESSION, SOURCE)
    assert saved.triage_agent_id == FIX and saved.config_revision == 2
    assert revision(conn) == before + 1

    # 비우면 자동 판단 안 함
    response = admin.put(f"/github/sources/{SOURCE}", json=source_body(conn, triage_agent_id=None))
    assert response.status_code == 200, response.text
    assert repo.get_github_source(conn, SESSION, SOURCE).triage_agent_id is None


def test_saving_other_fields_keeps_the_triage_agent(admin, conn):
    response = admin.put(f"/github/sources/{SOURCE}", json=source_body(conn, max_rework_rounds=2))
    assert response.status_code == 200, response.text
    assert repo.get_github_source(conn, SESSION, SOURCE).triage_agent_id == FIX


def test_owner_approval_agent_is_refused_as_triage_agent(admin, conn):
    repo.set_delegation_policy(conn, SESSION, FIX, "owner_approval", member_id=None, now=NOW)
    response = admin.put(f"/github/sources/{SOURCE}", json=source_body(conn, triage_agent_id=FIX))
    error(response, 422, "triage_agent_policy")
    assert response.json()["field"] == "triage_agent_id"


@pytest.mark.parametrize(("agent_id", "code"), [(REVIEW, "agent_capability_mismatch"),
                                                ("agent-nope", "agent_not_registered")])
def test_triage_agent_needs_registration_and_capability(admin, conn, agent_id, code):
    response = admin.put(f"/github/sources/{SOURCE}", json=source_body(conn, triage_agent_id=agent_id))
    error(response, 422, code)
    assert response.json()["field"] == "triage_agent_id"


# --- 권한 ----------------------------------------------------------------------------------


def test_member_has_no_triage_tab_and_gets_403(app, admin, conn):
    member = log_in_member(TestClient(app))
    assert 'data-tab="triage"' not in member.get("/connect").text
    error(member.get("/connect?tab=triage", follow_redirects=False), 403, "forbidden")
    error(save_criteria(member, "멤버 기준", 1), 403, "forbidden")
    error(save_autostart(member, "bug_fix", enabled=False), 403, "forbidden")
    assert member.put(f"/github/sources/{SOURCE}", json=source_body(conn, triage_agent_id=None)).status_code == 403
    assert repo.current_triage_criteria(conn, SESSION)["version"] == 1


def test_logged_out_is_redirected_to_login(app, judge):
    anonymous = TestClient(app)
    for response in (anonymous.get("/connect?tab=triage", follow_redirects=False),
                     save_criteria(anonymous, "기준", 1), save_autostart(anonymous, "bug_fix", enabled=False)):
        assert response.status_code == 303 and response.headers["location"] == "/login"


def test_cross_origin_posts_are_403(admin, conn):
    for path, data in (("/operator/triage/criteria", {"body": "기준", "expected_version": "1"}),
                       ("/operator/triage/autostart/bug_fix", {"threshold": "0.80"})):
        response = admin.post(path, data=data, headers={"Origin": "http://evil.example", "Accept": "text/html"},
                              follow_redirects=False)
        assert response.status_code == 403 and "forbidden_origin" in response.text
    assert repo.current_triage_criteria(conn, SESSION)["version"] == 1
