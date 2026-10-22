# ruff: noqa: F811 — test_task_cycle·test_report_api 픽스처(cycle·worker·world)를 가져와 인자로 쓴다
"""인계 리포트 화면 — phase 25 step 9 (ADR-0033, ARCHITECTURE "인계 리포트 — phase 25" 의 "화면"·"mermaid", D6·D8·D12·D13·
D24·D25·D26·D32).

업무 패널 단계 줄(`data-stage-report`), 리포트 페이지 `GET /work/{key}/reports/{report_id}`, 폼 셋(결정 답·맞춤 풀이·
다시 만들기), 같은 렌더의 `/api/v1/reports/{report_id}/html`. 픽스처는 사람용 API 테스트의 `world` 다(수정 결과의 원본
리포트 done — 보낸이·받는이·다른이·관리자).
"""

import hashlib
import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from workflow.adapters import repo
from workflow.contracts.v1 import ReportJobFailure
from workflow.server import handoff_reports, web

from .conftest import bearer
from .test_report_api import finish_job, report_body, world  # noqa: F401 — 픽스처
from .test_task_cycle import (  # noqa: F401 — 픽스처
    C1,
    FIX,
    NOW,
    REVIEW,
    SESSION,
    clock,
    cycle,
    executions,
    make_worker,
    settings,
    worker,
)
from .test_work_api import token_for

pytestmark = pytest.mark.usefixtures("cycle")

VENDOR = Path(web.__file__).parent / "static" / "vendor"
MERMAID_SHA256 = "581ed7d74bd9048d0e3a91363927d72ef22942d7722546b27f7cc29e35390eb8"
MERMAID_LICENSE_SHA256 = "ec9fb67dcb25eccc416ed56e1aab819222c805a2a4bfe4cb19e7556bf2ffde80"


def page(client, key: str, report_id: str) -> str:
    response = client.get(f"/work/{key}/reports/{report_id}")
    assert response.status_code == 200, response.text
    return response.text


def stage_line(client, key: str) -> str:
    response = client.get(f"/work/{key}/panel")
    assert response.status_code == 200, response.text
    found = re.search(r'<ol class="stage-list" data-stages>.*?</ol>', response.text, re.S)
    return found.group(0)


def claim_block(text: str, claim_id: str) -> str:
    return re.search(rf'<li id="claim-{claim_id}".*?</li>', text, re.S).group(0)


def resubmit(conn, store, cycle, world, body: dict) -> str:
    """원본을 다시 만들어 주어진 본문으로 제출 — 반환은 새 report_id."""
    row = handoff_reports.regenerate(conn, store, None, lambda config: None, session_id=SESSION,
                                     member=_member(conn, world["ids"]["보낸이"]), report_id=world["report_id"], now=NOW)
    job = handoff_reports.lease_job(conn, cycle["billing"], now=NOW)
    assert job.report_id == row["report_id"]
    data = {**body, "report_id": job.report_id}
    handoff_reports.submit_result(conn, store, connector_id=cycle["billing"], report_id=job.report_id,
                                  body=json.dumps(data).encode(), now=NOW)
    return job.report_id


def _member(conn, member_id: str):
    from workflow.server.auth import LoggedIn

    row = repo.get_member(conn, SESSION, member_id)
    return LoggedIn(session_id=SESSION, member_id=member_id, role=row["role"], display_name=row["display_name"],
                    login_id="test")


# --- 패널 단계 줄 (D32) ---------------------------------------------------------------------------------


def test_stage_line_links_to_a_done_report(world):
    key, report_id = world["key"], world["report_id"]
    line = stage_line(world["clients"]["다른이"], key)
    assert 'data-stage-report="done"' in line
    assert f'href="/work/{key}/reports/{report_id}"' in line and "리포트 보기" in line
    assert line.count("data-stage-report") == 1  # 리포트 행이 없는 단계(검토)에는 아무것도 없다


def test_stage_line_shows_writing_while_queued(world):
    clients = world["clients"]
    assert clients["보낸이"].post(f"/api/v1/reports/{world['report_id']}/regenerate").status_code == 202
    line = stage_line(clients["다른이"], world["key"])
    assert 'data-stage-report="queued"' in line and "리포트 작성 중" in line
    assert "리포트 보기" not in line and "/regenerate" not in line


def test_stage_line_shows_no_report_with_reason_and_regenerate_only_for_parties(world, conn, cycle):
    clients = world["clients"]
    clients["보낸이"].post(f"/api/v1/reports/{world['report_id']}/regenerate")
    job = handoff_reports.lease_job(conn, cycle["billing"], now=NOW)
    handoff_reports.submit_failure(conn, connector_id=cycle["billing"], report_id=job.report_id,
                                   failure=ReportJobFailure(contract_version=1, failure_code="timeout", message="멈춤"),
                                   now=NOW)

    line = stage_line(clients["보낸이"], world["key"])
    assert 'data-stage-report="failed"' in line and "리포트 없음(리포트 작성 실패 — 멈춤)" in line
    assert f'action="/work/{world["key"]}/reports/{job.report_id}/regenerate"' in line
    assert "리포트 다시 만들기" in line
    assert "/regenerate" in stage_line(clients["관리자"], world["key"])
    other = stage_line(clients["다른이"], world["key"])
    assert "리포트 없음(리포트 작성 실패 — 멈춤)" in other and "/regenerate" not in other


def test_report_decision_event_line_in_panel(world):
    clients = world["clients"]
    clients["받는이"].put(f"/api/v1/reports/{world['report_id']}/decisions/q1", json={"answer": "confirmed"})
    clients["관리자"].put(f"/api/v1/reports/{world['report_id']}/decisions/q1",
                         json={"answer": "changed", "note": "상한은 1만 원"})
    text = clients["다른이"].get(f"/work/{world['key']}/panel").text
    assert "리포트 결정 답 · 받는이 · 확인함" in text
    assert "리포트 결정 답 · 관리자 · 다르게 정함 — 상한은 1만 원" in text


# --- 리포트 페이지 -------------------------------------------------------------------------------------


def test_report_page_has_sections_claims_and_evidence_links(world, conn):
    key, report_id = world["key"], world["report_id"]
    text = page(world["clients"]["다른이"], key, report_id)

    assert "인계 리포트 · 버그 수정 · 시도 1" in text
    assert "보낸이" in text and "받는이" in text
    for title in ("바뀐 것", "결정과 근거", "정하지 않고 남긴 것", "받는 사람이 결정·확인할 것", "지킬 제약", "남은 일",
                  "다이어그램"):
        assert title in text
    c1 = claim_block(text, "c1")
    assert "쿠폰 중복 적용을 막음" in c1 and "재현 테스트가 실패했다" in c1
    assert f'href="https://github.com/acme/billing/commit/{C1[:7]}"' in c1
    assert "근거 확인 안 됨" not in c1
    c2 = claim_block(text, "c2")
    assert "기록 없음" in c2  # why null
    assert "data-unverified" in c2 and "근거 확인 안 됨" in c2
    q1 = claim_block(text, "q1")
    work = repo.get_work_item(conn, SESSION, world["work_item_id"])
    assert f'href="{work["source_url"]}"' in q1  # source:work → 원본 주소


def test_artifact_and_pr_evidence_link_and_pr_read_failure_is_shown(world, conn, store, cycle):
    fix_exec = repo.get_execution(conn, world["fix_exec"])
    artifact_id = fix_exec["result_artifact_id"]
    inputs = json.loads(repo.get_handoff_report(conn, world["report_id"])["inputs_json"])
    assert artifact_id in inputs["evidence"]["artifacts"]
    body = report_body("hrp-000000000000")
    body["sections"]["changes"] = [{"id": "c1", "text": "산출물 인용", "why": None,
                                    "evidence": [f"artifact:{artifact_id}", "pr:77"]}]
    new_id = resubmit(conn, store, cycle, world, body)
    row = repo.get_handoff_report(conn, new_id)
    data = json.loads(row["inputs_json"])
    data["pull_requests"] = [{"number": 77, "title": "PR 77", "url": "https://github.com/acme/billing/pull/77"}]
    data["pr_evidence_error"] = "PR 읽기 실패 — GitHub 요청 한도"
    conn.execute("UPDATE handoff_reports SET inputs_json = ? WHERE report_id = ?", (json.dumps(data), new_id))
    conn.commit()

    text = page(world["clients"]["다른이"], world["key"], new_id)

    c1 = claim_block(text, "c1")
    assert f'href="/tasks/{world["fix_task"]}/artifacts/{artifact_id}"' in c1
    assert 'href="https://github.com/acme/billing/pull/77"' in c1
    assert "PR 읽기 실패 — GitHub 요청 한도" in text


def test_model_text_is_escaped_and_diagram_is_pre_text(world, conn, store, cycle):
    body = report_body("hrp-000000000000")
    body["sections"]["changes"][0]["text"] = "<script>alert(1)</script>"
    body["diagram"] = "graph TD; A-->B; C[\"<img src=x onerror=alert(2)>\"]"
    new_id = resubmit(conn, store, cycle, world, body)

    text = page(world["clients"]["다른이"], world["key"], new_id)

    assert "<script>alert(1)</script>" not in text and "&lt;script&gt;alert(1)&lt;/script&gt;" in text
    assert "<img src=x" not in text
    diagram = re.search(r'<pre class="mermaid" data-report-diagram>(.*?)</pre>', text, re.S).group(1)
    assert "<" not in diagram and "A--&gt;B" in diagram


def test_page_loads_only_the_vendored_mermaid_with_strict_security(world):
    text = page(world["clients"]["다른이"], world["key"], world["report_id"])
    sources = re.findall(r'<script[^>]*\bsrc="([^"]*)"', text)
    assert sources == ["/static/vendor/mermaid.min.js"]
    assert "securityLevel: 'strict'" in text
    assert not re.search(r"(src|href)=\"(https?:)?//[^\"]*(jsdelivr|unpkg|cdn)", text)
    panel = world["clients"]["다른이"].get(f"/work/{world['key']}/panel").text
    assert "mermaid" not in panel  # 리포트 페이지에서만 불러온다


def test_vendored_mermaid_matches_architecture_sha256(client):
    assert hashlib.sha256((VENDOR / "mermaid.min.js").read_bytes()).hexdigest() == MERMAID_SHA256
    assert hashlib.sha256((VENDOR / "mermaid.LICENSE").read_bytes()).hexdigest() == MERMAID_LICENSE_SHA256
    assert client.get("/static/vendor/mermaid.min.js").status_code == 200


def test_unknown_or_other_work_report_is_404(world):
    member = world["clients"]["다른이"]
    assert member.get(f"/work/{world['key']}/reports/hrp-000000000000").status_code == 404
    assert member.get(f"/work/RUN-999/reports/{world['report_id']}").status_code == 404


def test_writing_and_unavailable_pages(world, conn):
    clients = world["clients"]
    queued = clients["보낸이"].post(f"/api/v1/reports/{world['report_id']}/regenerate").json()["report_id"]
    assert "리포트 작성 중" in page(clients["다른이"], world["key"], queued)
    conn.execute("UPDATE handoff_reports SET status = 'unavailable', reason = '러너 연결 없음', finished_at = ?"
                 " WHERE report_id = ?", (NOW, queued))
    conn.commit()
    assert "리포트 없음(러너 연결 없음)" in page(clients["다른이"], world["key"], queued)


# --- 결정 답 폼 (D13) ----------------------------------------------------------------------------------


def decision_action(key: str, report_id: str, item_id: str) -> str:
    return f'action="/work/{key}/reports/{report_id}/decisions/{item_id}"'


def test_decision_form_only_for_recipient_and_admin(world):
    clients, key, report_id = world["clients"], world["key"], world["report_id"]
    assert decision_action(key, report_id, "q1") in page(clients["받는이"], key, report_id)
    assert decision_action(key, report_id, "q1") in page(clients["관리자"], key, report_id)
    for name in ("보낸이", "다른이"):
        assert "/decisions/" not in page(clients[name], key, report_id)


def test_decision_post_redirects_and_answer_is_visible_to_all(world, conn):
    clients, key, report_id = world["clients"], world["key"], world["report_id"]

    response = clients["받는이"].post(f"/work/{key}/reports/{report_id}/decisions/q1",
                                     data={"answer": "changed", "note": "상한은 1만 원"}, follow_redirects=False)

    assert response.status_code == 303, response.text
    assert response.headers["location"] == f"/work/{key}/reports/{report_id}"
    (decision,) = repo.list_report_decisions(conn, report_id)
    assert (decision["answer"], decision["note"]) == ("changed", "상한은 1만 원")
    q1 = claim_block(page(clients["다른이"], key, report_id), "q1")
    assert "다르게 정함 — 상한은 1만 원 · 받는이" in q1

    clients["받는이"].post(f"/work/{key}/reports/{report_id}/decisions/q1", data={"answer": "confirmed"})
    assert "확인함 · 받는이" in claim_block(page(clients["다른이"], key, report_id), "q1")


def test_decision_post_by_other_member_is_403(world, conn):
    response = world["clients"]["다른이"].post(f"/work/{world['key']}/reports/{world['report_id']}/decisions/q1",
                                              data={"answer": "confirmed"}, follow_redirects=False)
    assert response.status_code == 403
    assert "받는 사람이나 관리자만 답할 수 있습니다." in response.text
    assert repo.list_report_decisions(conn, world["report_id"]) == []


def test_decision_post_from_foreign_origin_is_refused(world):
    response = world["clients"]["받는이"].post(
        f"/work/{world['key']}/reports/{world['report_id']}/decisions/q1", data={"answer": "confirmed"},
        headers={"Origin": "https://evil.example", "Accept": "text/html"}, follow_redirects=False)
    assert response.status_code == 403


# --- 맞춤 풀이 (D8) -------------------------------------------------------------------------------------


def test_tailor_button_only_for_recipient_with_usable_agents(world, conn, cycle):
    clients, key, report_id = world["clients"], world["key"], world["report_id"]
    text = page(clients["받는이"], key, report_id)
    form = re.search(r'<form[^>]*action="/work/[^"]*/tailor".*?</form>', text, re.S).group(0)
    assert "내게 맞춰 풀기" in form
    options = re.findall(r'<option value="([^"]+)"', form)
    assert options[0] == REVIEW  # 기본값: 다음 단계 Agent 가 맨 앞
    for name in ("관리자", "보낸이", "다른이"):
        assert "/tailor" not in page(clients[name], key, report_id)

    conn.execute("UPDATE connectors SET capabilities_json = NULL WHERE connector_id = ?", (cycle["shop"],))
    conn.commit()
    assert "/tailor" not in page(clients["받는이"], key, report_id)  # 남는 Agent 가 없으면 숨김


def test_tailor_post_redirects_to_the_tailored_page_and_shows_it(world, conn, store, cycle):
    clients, key, report_id = world["clients"], world["key"], world["report_id"]

    response = clients["받는이"].post(f"/work/{key}/reports/{report_id}/tailor", data={"agent_id": REVIEW},
                                     follow_redirects=False)

    assert response.status_code == 303, response.text
    tailored_id = repo.list_work_reports(conn, world["work_item_id"])[-1]["report_id"]
    assert response.headers["location"] == f"/work/{key}/reports/{tailored_id}"
    assert finish_job(conn, store, cycle["shop"]) == tailored_id

    text = page(clients["다른이"], key, tailored_id)
    assert f"맞춤 풀이 · 받는이 · {REVIEW}" in text
    assert f'href="/work/{key}/reports/{report_id}#claim-c1"' in claim_block(text, "t1")

    original = page(clients["다른이"], key, report_id)
    listing = re.search(r"data-tailored-list.*?</ul>", original, re.S).group(0)
    assert f"/work/{key}/reports/{tailored_id}" in listing and "받는이" in listing
    assert "옛 리포트 기준" not in listing
    clients["보낸이"].post(f"/api/v1/reports/{report_id}/regenerate")
    listing = re.search(r"data-tailored-list.*?</ul>", page(clients["다른이"], key, report_id), re.S).group(0)
    assert "옛 리포트 기준" in listing


def test_tailor_post_by_non_recipient_is_403(world, conn):
    response = world["clients"]["관리자"].post(f"/work/{world['key']}/reports/{world['report_id']}/tailor",
                                              data={"agent_id": REVIEW}, follow_redirects=False)
    assert response.status_code == 403
    assert conn.execute("SELECT COUNT(*) FROM handoff_reports WHERE job_type = 'tailored'").fetchone()[0] == 0


# --- 다시 만들기·이전 리포트 (D6·D12) -----------------------------------------------------------------


def test_regenerate_post_redirects_to_the_new_report_and_previous_is_in_details(world, conn):
    clients, key, report_id = world["clients"], world["key"], world["report_id"]
    assert f"/reports/{report_id}/regenerate" in page(clients["보낸이"], key, report_id)
    assert "/regenerate" not in page(clients["다른이"], key, report_id)

    response = clients["보낸이"].post(f"/work/{key}/reports/{report_id}/regenerate", follow_redirects=False)

    assert response.status_code == 303, response.text
    new_id = repo.latest_original_report(conn, world["fix_exec"])["report_id"]
    assert new_id != report_id
    assert response.headers["location"] == f"/work/{key}/reports/{new_id}"
    text = page(clients["다른이"], key, new_id)
    history = re.search(r'<details class="report-history">.*?</details>', text, re.S).group(0)
    assert "<summary>자세히</summary>" in history and f"/work/{key}/reports/{report_id}" in history
    assert "/regenerate" not in page(clients["보낸이"], key, new_id)  # 진행 중이면 버튼 없음


def test_regenerate_post_by_other_member_is_403(world, conn):
    response = world["clients"]["다른이"].post(f"/work/{world['key']}/reports/{world['report_id']}/regenerate",
                                              follow_redirects=False)
    assert response.status_code == 403
    assert repo.latest_original_report(conn, world["fix_exec"])["report_id"] == world["report_id"]


# --- /api/v1 HTML (D29) --------------------------------------------------------------------------------


def test_api_html_with_read_token_and_unauthenticated(app, world, conn):
    report_id = world["report_id"]
    reader = TestClient(app, headers=bearer(token_for(conn, world["ids"]["받는이"], "read")))

    response = reader.get(f"/api/v1/reports/{report_id}/html")

    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/html")
    assert "인계 리포트 · 버그 수정 · 시도 1" in response.text and "쿠폰 중복 적용을 막음" in response.text
    cookie = world["clients"]["다른이"].get(f"/api/v1/reports/{report_id}/html")
    assert cookie.status_code == 200
    anonymous = TestClient(app).get(f"/api/v1/reports/{report_id}/html")
    assert anonymous.status_code == 401
    assert world["clients"]["다른이"].get("/api/v1/reports/hrp-000000000000/html").status_code == 404


# --- 종류·규칙 폼 선택지 (D26) ------------------------------------------------------------------------


def test_kind_form_choices_hide_handoff_report(world):
    assert "handoff_report" not in web.INPUT_KIND_CHOICES and "handoff_bundle" not in web.INPUT_KIND_CHOICES
    text = world["clients"]["관리자"].get("/settings?tab=kinds").text
    assert 'value="handoff_report"' not in text
