# ruff: noqa: F811 — test_metrics_api 픽스처(operator·op·fake·settings)를 가져와 인자로 쓴다
"""지표 화면 `GET /monitor`(phase 16 step 7 — 옛 `GET /metrics` 는 303) (phase 9 step 9, ADR-0015, ARCHITECTURE "측정 — phase 9" API 표).

운영자만 연다(`/operator/github` 과 같은 규칙). 계산은 `metrics_api` 와 같은 것을 쓰고, 화면은 중앙값 옆에 n·미완료·모름을 함께 적는다.
모르는 값은 "모름" 이며 0 으로 보이지 않는다. 데이터·가짜 GitHub 는 `test_metrics_api` 의 fixture 를 그대로 쓴다.
"""

import re

from fastapi.testclient import TestClient

from workflow.domain.metrics import BASELINE_NOTE

from workflow.adapters import repo

from .conftest import log_in_member, log_in_other_workspace, seed_execution, task_row
from .test_github_api import login
from .test_metrics_api import (  # noqa: F401 — fixture
    MERGE_7,
    SOURCE,
    TOKEN,
    fake,
    op,
    operator,
    record_merge,
    seed_github_bundle,
    settings,
    triaged,
)

CAUSAL_NOTE = "관측값이며 인과 효과로 단정하지 않는다"
BASELINE_ACTION = f'data-json-action="/operator/github/sources/{SOURCE}/baseline"'


def page(client: TestClient, params: dict | None = None) -> str:
    response = client.get("/monitor", params=params or {})
    assert response.status_code == 200, response.text
    return response.text


def row(text: str, label: str) -> str:
    """지표 표에서 `label` 행(<tr>…</tr>) 하나."""
    match = re.search(rf"<tr[^>]*>\s*<th[^>]*>{re.escape(label)}</th>.*?</tr>", text, re.S)
    assert match, label
    return match.group(0)


# --- 권한 ---------------------------------------------------------------------------------------


def test_metrics_page_is_operator_only(client, conn):
    # 로그인 전, 그리고 고정 워크스페이스가 아닌 워크스페이스의 로그인 쿠키 — 셀프호스트에서는 둘 다 로그인 안 된 것
    stranger = log_in_other_workspace(TestClient(client.app))
    for anonymous in (client, stranger):
        response = anonymous.get("/monitor", follow_redirects=False)
        assert (response.status_code, response.headers["location"]) == (303, "/login")
        assert BASELINE_ACTION not in response.text
        tasks = anonymous.get("/tasks", follow_redirects=False)  # 사이드바 링크를 볼 화면도 없다
        assert (tasks.status_code, tasks.headers["location"]) == (303, "/login")


def test_sidebar_links_metrics_page_for_operator(op):
    assert 'href="/monitor"' in op.get("/tasks").text and 'href="/metrics"' not in op.get("/tasks").text


# --- 이름 변경 (phase 16 step 7) -----------------------------------------------------------------------


def test_monitor_page_is_named_monitoring_and_form_stays_on_monitor(op):
    text = page(op)
    assert "<title>모니터링 — Runloom</title>" in text
    assert '<form method="get" action="/monitor"' in text
    assert '<a href="/monitor" class="active">모니터링</a>' in text


def test_old_metrics_page_redirects_to_monitor_keeping_query(op):
    response = op.get("/metrics", params={"from": "2026-09-01T00:00:00Z", "group_by": "config_revision"},
                      follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/monitor?from=2026-09-01T00%3A00%3A00Z&group_by=config_revision"
    bare = op.get("/metrics", follow_redirects=False)
    assert (bare.status_code, bare.headers["location"]) == (303, "/monitor")
    # 쿼리 값은 넘어간 뒤 기존 파서가 검증한다
    assert op.get("/metrics", params={"from": "어제"}).status_code == 422


def test_metrics_json_and_csv_paths_stay(op):
    assert op.get("/metrics.json", follow_redirects=False).status_code == 200
    csv = op.get("/metrics.csv", follow_redirects=False)
    assert csv.status_code == 200 and "text/csv" in csv.headers["content-type"]


# --- 데이터 없음 / 있음 ----------------------------------------------------------------------------


def test_empty_session_renders_unknowns_not_zero(app):
    client = TestClient(app)
    login(client)  # 빈 워크스페이스 — 업무·소스 없음
    text = page(client)
    assert CAUSAL_NOTE in text
    for area in ("병목", "속도", "사람 부담", "품질", "비용", "신뢰성"):
        assert f">{area}<" in text
    cost = row(text, "CLI 보고 비용")
    assert "모름" in cost and "n 0" in cost and "$0" not in cost
    assert "모름" in row(text, "1회 통과율")
    assert "GitHub 소스가 없습니다" in text


def test_page_shows_values_with_n_and_unknown_counts(op):
    text = page(op)
    cost = row(text, "CLI 보고 비용")
    assert "$0.5" in cost and "n 1" in cost and "모름 1" in cost
    tokens = row(text, "출력 토큰")
    assert "모름" in tokens and "n 0" in tokens and "모름 2" in tokens
    failure = row(text, "실패율")
    assert "50%" in failure and "1/2" in failure and "n 2" in failure
    assert "timeout 1" in row(text, "실패 사유")
    assert "20분 0초" in row(text, "실행 시간")  # 중앙값 1200초
    assert "모름" in row(text, "1회 통과율")  # 검토 결과 없음 — 0% 가 아니다
    assert "0%" not in row(text, "1회 통과율")


# --- 기준선 대 도입 후 ---------------------------------------------------------------------------------


def test_top_compares_baseline_and_after_with_button_for_operator(op, fake):
    text = page(op)
    assert BASELINE_ACTION in text
    assert "가져온 적 없음" in text
    top = text.split('id="compare"', 1)[1].split("</section>", 1)[0]
    assert "이슈 열림 → 병합" in top and "도입 후" in top
    assert "n 0" in top and "미완료 2" not in top  # 직접 등록 업무 2개는 이슈 열림 → 병합 비교에 들어가지 않는다
    assert "승인" not in top  # 승인 지표는 아래 속도 표에

    assert op.post(f"/operator/github/sources/{SOURCE}/baseline").status_code == 200
    text = page(op)
    top = text.split('id="compare"', 1)[1].split("</section>", 1)[0]
    assert "acme/billing" in top
    assert "4시간 0분" in top and "n 2" in top  # 2시간·6시간의 중앙값
    assert BASELINE_NOTE in top
    assert "마지막 가져옴" in text and "KST" in text and "2건" in text
    assert BASELINE_ACTION in text


def test_top_compares_merge_only_and_approval_goes_to_speed_table(operator, conn):
    op, session_id = operator
    seed_github_bundle(conn, session_id)  # GitHub 묶음: 열림 00:00 → 승인 02:00 → 병합 04:00
    record_merge(conn, session_id, MERGE_7, "2026-09-26T06:00:00Z")
    text = page(op, {"from": "2026-09-26T00:00:00Z"})
    top = text.split('id="compare"', 1)[1].split("</section>", 1)[0]
    assert "4시간 0분" in top and "2시간 0분" not in top
    assert "병합 PR" in top
    assert "GitHub 이슈 업무만" in top and "묶음" not in top  # 지표 묶음 = 업무(phase 14 step 8)
    approval = row(text, "접수 → 승인")
    assert "2시간 0분" in approval and "n 1" in approval
    assert "4시간 0분" in row(text, "접수 → 완료")


# --- 기간·그룹 ---------------------------------------------------------------------------------------


def test_period_and_group_params_are_reflected(op):
    params = {"from": "2026-09-01T00:00:00Z", "to": "2026-10-01T00:00:00+09:00", "group_by": "config_revision"}
    text = page(op, params)
    assert 'name="from" value="2026-09-01T00:00:00Z"' in text
    assert 'name="to" value="2026-10-01T00:00:00+09:00"' in text
    assert '<option value="config_revision" selected>' in text
    assert "설정 번호 1" in text and "설정 번호 2" in text
    links = re.findall(r'href="(/metrics\.(?:json|csv)[^"]*)"', text)
    assert len(links) == 2
    assert all("group_by=config_revision" in link and "from=2026-09-01T00%3A00%3A00Z" in link for link in links)

    narrowed = page(op, {"from": "2026-09-21T00:00:00Z"})
    assert "n 1" in row(narrowed, "실패율")


def test_empty_form_values_mean_no_filter(op):
    text = page(op, {"from": "", "to": "", "group_by": ""})
    assert "전체" in text
    assert 'href="/metrics.json"' in text and 'href="/metrics.csv"' in text


def test_invalid_params_render_error_page(op):
    for params in ({"from": "어제"}, {"group_by": "agent"},
                   {"from": "2026-09-22T00:00:00Z", "to": "2026-09-21T00:00:00Z"}):
        response = op.get("/monitor", params=params)
        assert response.status_code == 422, params
        assert "text/html" in response.headers["content-type"]


# --- 비밀값 --------------------------------------------------------------------------------------------


def test_page_has_no_secrets_or_paths(op, fake, settings):
    op.post(f"/operator/github/sources/{SOURCE}/baseline")
    text = page(op, {"group_by": "folder_commit"})
    for secret in (TOKEN, settings.operator_token, settings.session_secret, str(settings.db_path.parent)):
        assert secret not in text


# --- 탭 3개 (phase 20 step 7, ARCHITECTURE "모니터링 — phase 20" 경로·화면 문구) ------------------------------------


def tabs(text: str) -> dict[str, str]:
    """탭 머리 — data-tab → 링크 원문."""
    nav = text.split("data-monitor-tabs", 1)[1].split("</nav>", 1)[0]
    return {key: link for link, key in re.findall(r'(<a href="[^"]*" data-tab="([^"]+)"[^>]*>[^<]*</a>)', nav)}


def named_row(text: str, name: str) -> str:
    """이름 칸 뒤에 표시(비활성·소유자)가 붙는 행."""
    match = re.search(rf"<tr[^>]*>\s*<th[^>]*>{re.escape(name)}.*?</tr>", text, re.S)
    assert match, name
    return match.group(0)


def body(text: str) -> str:
    return text.split("data-tab-body=", 1)[1]


def test_three_tabs_render_with_the_active_head_and_keep_the_period(op):
    params = {"from": "2026-09-01T00:00:00Z", "group_by": "config_revision"}
    for tab in ("before_after", "triage", "assignees"):
        text = page(op, {**params, "tab": tab})
        heads = tabs(text)
        assert list(heads) == ["before_after", "triage", "assignees"]
        assert [k for k, a in heads.items() if 'class="active"' in a] == [tab]
        assert ">전후</a>" in heads["before_after"] and ">판단</a>" in heads["triage"]
        assert ">담당자별</a>" in heads["assignees"]
        for key, link in heads.items():  # 링크는 기간·그룹을 유지한다
            assert f"tab={key}" in link and "from=2026-09-01T00%3A00%3A00Z" in link
            assert "group_by=config_revision" in link
        assert f'data-tab-body="{tab}"' in text
        assert f'<input type="hidden" name="tab" value="{tab}">' in text
        assert CAUSAL_NOTE in text
    assert 'data-tab-body="before_after"' in page(op)  # 기본 = 전후
    assert 'id="compare"' not in page(op, {"tab": "triage"})  # 전후 표는 전후 탭에만


def test_invalid_tab_is_422(op):
    response = op.get("/monitor", params={"tab": "charts"})
    assert response.status_code == 422
    assert "text/html" in response.headers["content-type"]
    assert "탭은 전후·판단·담당자별 중 하나입니다." in response.text


def test_member_role_opens_every_tab(app, triaged):
    member = log_in_member(TestClient(app))
    for tab in ("before_after", "triage", "assignees"):
        assert member.get("/monitor", params={"tab": tab}).status_code == 200, tab


# --- 전후 탭 — 설정 번호 머리 --------------------------------------------------------------------------


def test_config_revision_heads_show_the_change_or_no_record(triaged):
    op, _ = triaged
    text = page(op, {"group_by": "config_revision"})
    heads = re.findall(r"<div class=\"small\" data-config-head>([^<]*)</div>", text)
    assert "설정 1 — 기록 없음" in heads  # v16 전 번호
    assert "설정 2 — 저장소 연결 추가 acme/billing · 시스템 · 9/1" in heads  # 멤버 없는 경로 = 시스템
    assert not any(h.startswith("설정 3") for h in heads)  # 그 번호의 업무가 없으면 열도 없다
    assert "data-config-head" not in page(op)  # 나누지 않으면 머리 줄 없음
    assert "data-config-head" not in page(op, {"group_by": "folder_commit"})


def test_config_revision_head_names_the_member_escaped(triaged, conn):
    op, admin = triaged
    conn.execute("UPDATE members SET display_name = ? WHERE member_id = ?", ("<script>김</script>", admin))
    session_id = conn.execute("SELECT session_id FROM members WHERE member_id = ?", (admin,)).fetchone()[0]
    repo.insert_work_item_task(conn, {**task_row("task-3"), "session_id": session_id}, "2026-10-02T15:30:00Z")
    seed_execution(conn, "exec-3", "task-3")  # 설정 번호 그룹은 실행 때의 번호(3)
    text = page(op, {"group_by": "config_revision"})
    assert "설정 3 — 자동 시작 변경 bug_fix 끔 · 기준값 0.90 · &lt;script&gt;김&lt;/script&gt; · 9/22" in text
    assert "<script>김" not in text


# --- 판단 탭 ------------------------------------------------------------------------------------------


def test_triage_tab_matches_metrics_json(triaged):
    op, _ = triaged
    overall = op.get("/metrics.json").json()["triage"]["overall"]
    text = body(page(op, {"tab": "triage"}))
    summary = re.search(r"<p[^>]*data-triage-summary>([^<]*)</p>", text).group(1)
    merged = overall["merged"]
    assert summary == (
        f"제안 {overall['proposed']} · 사람 일치 {overall['agreement']['numerator']}/{overall['agreement']['denominator']}"
        f" · 병합 완료 {merged['numerator']}/{merged['denominator']}(진행 중 {merged['incomplete']})"
        f" · 재작업 없이 병합 {overall['merged_without_rework']['numerator']}/{merged['denominator']}"
    )
    assert summary == "제안 1 · 사람 일치 1/1 · 병합 완료 0/0(진행 중 1) · 재작업 없이 병합 0/0"

    kind = row(text, "bug_fix")
    assert "100% (1/1)" in kind  # 사람 일치
    assert "제안대로 1 · 다르게 0 · 무시 0 · 자동 시작 0 · 미처리 0" in kind
    assert "맡겨도 됨 1 · 확인 필요 0 · 부적합 0" in kind
    assert "2분 0초" in kind and "$0.1" in kind
    unknown_kind = row(text, "모름")  # 실패 판단은 제안 종류가 없다
    assert "timeout 1" in unknown_kind
    criteria = row(text, "v1")
    assert "n 1" in criteria
    bucket = row(text, "[0.9, 1.0]")
    assert "100% (1/1)" in bucket and "진행 중 1" in bucket
    low = row(text, "[0, 0.5)")
    assert "—" in low and "0%" not in low  # 분모 0 은 0% 가 아니다
    assert "실패 코드 timeout 1" in text
    assert "판단 뒤 내용이 바뀐 업무 0" in text
    assert "비용은 CLI 계산값이며 구독 청구액이 아닙니다." in text
    assert "아직 판단 기록이 없습니다." not in text


def test_triage_tab_follows_the_period(triaged):
    op, _ = triaged
    text = body(page(op, {"tab": "triage", "from": "2026-09-21T00:00:00Z"}))
    assert "제안 0 · 사람 일치 0/0" in text


def test_triage_tab_empty_state_links_connect_only_for_managers(app, op):
    text = body(page(op, {"tab": "triage"}))  # operator 픽스처 — 판단 기록 없음
    assert "아직 판단 기록이 없습니다." in text
    assert '<a href="/settings?tab=triage">연결 › 판단</a>' in text
    member = log_in_member(TestClient(app))
    member_text = body(page(member, {"tab": "triage"}))
    assert "아직 판단 기록이 없습니다." in member_text and "연결 › 판단" in member_text
    assert 'href="/settings?tab=triage"' not in member_text


# --- 담당자별 탭 -----------------------------------------------------------------------------------------


def test_assignees_tab_lists_members_and_agents_with_escaped_names(triaged, conn):
    op, admin = triaged
    conn.execute("UPDATE members SET display_name = ? WHERE member_id = ?", ("<script>관</script>", admin))
    conn.execute("UPDATE agents SET name = ? WHERE agent_id = ?", ("<b>코덱스</b>", "agent-codex-mac"))
    text = body(page(op, {"tab": "assignees"}))
    assert "<script>관" not in text and "<b>코덱스" not in text
    member = named_row(text, "&lt;script&gt;관&lt;/script&gt;")
    assert "(비활성)" not in member
    agent = named_row(text, "&lt;b&gt;코덱스&lt;/b&gt;")
    assert "관리자 관리" in agent  # 러너가 없는 Agent — 기존 소유자 표시
    assert "50% (1/2)" in agent and "timeout 1" in agent  # 실패율·실패 코드(판단 실행 제외)
    assert "<td>2</td>" in agent  # 진행 중 2
    assert "$0.5" in agent
    assert "담당 없는 진행 중 업무 0" in text
    assert "응답자를 모르는 응답" not in text
    assert "아직 맡은 업무가 없습니다." not in text


def test_assignees_tab_marks_inactive_members_with_records(triaged, conn):
    op, admin = triaged
    session_id = conn.execute("SELECT session_id FROM members WHERE member_id = ?", (admin,)).fetchone()[0]
    work = conn.execute("SELECT work_item_id FROM tasks WHERE task_id = 'task-1'").fetchone()[0]
    conn.execute("UPDATE work_items SET assignee_type = 'member' WHERE work_item_id = ?", (work,))
    log_in_member(TestClient(op.app), display_name="떠난 멤버")
    [gone] = [m["member_id"] for m in repo.list_members(conn, session_id) if m["display_name"] == "떠난 멤버"]
    conn.execute("UPDATE work_items SET assignee_id = ? WHERE work_item_id = ?", (gone, work))
    conn.execute("UPDATE members SET disabled_at = '2026-09-30T00:00:00Z' WHERE member_id = ?", (gone,))
    text = body(page(op, {"tab": "assignees"}))
    assert "(비활성)" in named_row(text, "떠난 멤버")


def test_assignees_tab_empty_state(app):
    client = TestClient(app)
    login(client)
    text = body(page(client, {"tab": "assignees"}))
    assert "아직 맡은 업무가 없습니다." in text
    assert "담당 없는 진행 중 업무 0" in text
