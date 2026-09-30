"""업무 화면 `/tasks` — phase 16 step 4 (ARCHITECTURE "업무 화면 — phase 16" 주소 표·화면 배치, UI_GUIDE "업무 화면").

한 줄 표(칸 8개, 묶음 머리 행), 담당자·상태 묶기, 빠른 필터 4개와 건수, 보드 6칸, 행 링크 `open=<key>`(목록 상태 유지),
모르는 쿼리 값 → 기본값, 외부 제목 이스케이프, 새 사이드바(내 차례 배지·"최근" 없음·권한별 숨김), 빈 상태.
"""

import re

import pytest
from fastapi.testclient import TestClient

from workflow.adapters import repo
from workflow.server.auth import LOGIN_COOKIE, ensure_workspace, utc_now

from .conftest import NOW, SESSION, log_in, log_in_member, seed_agents

AGENT = "agent-codex-mac"  # 이름 "개인 Codex"
HEADERS = ["키", "제목", "담당", "우선순위", "종류", "상태", "다음 할 일", "업데이트"]


@pytest.fixture
def admin(client, conn) -> TestClient:
    ensure_workspace(conn, NOW)
    seed_agents(conn)
    return log_in(client)


@pytest.fixture
def member(app, admin) -> TestClient:
    return log_in_member(TestClient(app), display_name="김멤버")


def member_id(conn, client) -> str:
    return repo.member_for_login_token(conn, client.cookies[LOGIN_COOKIE], now=utc_now())["member_id"]


def new_work(conn, title: str, *, updated: str = NOW, **overrides) -> tuple[str, str]:
    """(work_item_id, 업무 키). 직접 등록 모양, 상태는 저장값을 그대로 보인다."""
    conn.execute("BEGIN IMMEDIATE")
    work_item_id, key = repo.create_work_item(conn, SESSION, title=title, request="요청", kind="bug_fix",
                                              **{"source_type": "manual", "now": NOW, **overrides})
    conn.execute("COMMIT")
    conn.execute("UPDATE work_items SET updated_at = ? WHERE work_item_id = ?", (updated, work_item_id))
    return work_item_id, f"RUN-{key}"


def assign(conn, work_item_id: str, assignee_type: str, assignee_id: str) -> None:
    repo.assign_work_item(conn, SESSION, work_item_id, assignee_type=assignee_type, assignee_id=assignee_id, now=NOW)


def set_status(conn, work_item_id: str, status: str, reason: str = "", closed_at: str | None = None) -> None:
    conn.execute("UPDATE work_items SET status = ?, status_reason = ?, closed_at = ? WHERE work_item_id = ?",
                 (status, reason, closed_at, work_item_id))


def main_of(html: str) -> str:
    return html[html.index('class="main'):html.index("<script>")]


def sidebar_of(html: str) -> str:
    return html[html.index('class="sidebar'):html.index('class="main')]


def keys_in(html: str) -> list[str]:
    return re.findall(r'data-work-key="(RUN-\d+)"', main_of(html))


def groups_in(html: str) -> list[tuple[str, str, int]]:
    """(묶음 키, 이름, 건수) — 머리 행 순서."""
    return [(k, label.strip(), int(n)) for k, label, n in re.findall(
        r'<tr class="group-head" data-group="([^"]+)">.*?<span class="group-label">(.*?)</span>'
        r'\s*<span class="count">(\d+)</span>', main_of(html), re.S)]


def counts_in(html: str) -> dict[str, int]:
    return {q: int(n) for q, n in re.findall(r'data-q="(\w+)"[^>]*>[^<]*<span class="count">(\d+)</span>', html)}


@pytest.fixture
def people(conn, admin, member) -> dict[str, str]:
    """담당 없음 2개 · 나(관리자) 1개 · 김멤버 1개(내 차례) · 에이전트 1개. 우선순위·시각으로 묶음 안 순서를 본다."""
    me, other = member_id(conn, admin), member_id(conn, member)
    low, _ = new_work(conn, "담당 없음 낮음", priority="low", updated="2026-09-20T05:00:00Z")      # RUN-1
    high, _ = new_work(conn, "담당 없음 높음", priority="high", updated="2026-09-20T01:00:00Z")    # RUN-2
    mine, _ = new_work(conn, "내 업무")                                                         # RUN-3
    theirs, _ = new_work(conn, "멤버 업무")                                                     # RUN-4
    agent, _ = new_work(conn, "에이전트 업무")                                                  # RUN-5
    assign(conn, mine, "member", me)
    assign(conn, theirs, "member", other)
    assign(conn, agent, "agent", AGENT)
    set_status(conn, theirs, "내 차례", "사람 요청 — 어느 쪽으로 고칠까요?")
    set_status(conn, agent, "에이전트 작업 중", "실행 중")
    return {"me": me, "other": other}


# --- 표 ----------------------------------------------------------------------------------------------------


def test_list_table_has_eight_column_headers(admin, people):
    html = admin.get("/tasks").text
    table = main_of(html)[main_of(html).index("<table"):]
    assert re.findall(r"<th scope=\"col\"[^>]*>(.*?)</th>", table) == HEADERS


def test_assignee_groups_in_order_with_counts_and_order_inside(admin, people):
    html = admin.get("/tasks").text
    assert groups_in(html) == [
        ("none", "담당 없음", 2),
        (f"member:{people['me']}", "관리자 (나)", 1),
        (f"member:{people['other']}", "김멤버", 1),
        (f"agent:{AGENT}", "개인 Codex", 1),
    ]
    assert keys_in(html) == ["RUN-2", "RUN-1", "RUN-3", "RUN-4", "RUN-5"]  # 묶음 안: 우선순위 먼저


def test_status_groups_follow_work_status_order(admin, people):
    html = admin.get("/tasks?group=status").text
    assert [(k, n) for k, _, n in groups_in(html)] == [
        ("status:새로 들어옴", 2), ("status:대기", 1), ("status:에이전트 작업 중", 1), ("status:내 차례", 1)]
    # 담당만 있고 단계가 없는 RUN-3 은 계산된 `대기`


def test_row_cells_show_source_badge_priority_symbol_status_badge_and_next_action(admin, conn, people):
    new_work(conn, "GitHub 이슈", source_type="github", source_key="acme/billing#41",
             source_url="https://github.com/acme/billing/issues/41", priority="high")  # RUN-6
    html = admin.get("/tasks").text
    row = re.search(r'<tr class="work-row" data-work-key="RUN-6".*?</tr>', html, re.S).group(0)
    assert "acme/billing#41" in row and '<span class="source-badge" data-source="github">GitHub</span>' in row
    assert "↑ 높음" in row
    assert 'data-status="새로 들어옴"' in row
    theirs = re.search(r'<tr class="work-row" data-work-key="RUN-4".*?</tr>', html, re.S).group(0)
    assert "사람 요청 — 어느 쪽으로 고칠까요?" in theirs and 'data-source="manual">직접</span>' in theirs
    assert "– 보통" in theirs and "김멤버" in theirs
    assert "↓ 낮음" in re.search(r'data-work-key="RUN-1".*?</tr>', html, re.S).group(0)


def test_row_links_open_the_work_and_keep_the_list_state(admin, people):
    html = admin.get("/tasks").text
    assert 'href="/tasks?open=RUN-3"' in main_of(html)
    html = admin.get("/tasks?q=all&group=status&closed=all").text
    assert 'href="/tasks?group=status&amp;closed=all&amp;open=RUN-3"' in main_of(html)
    board = admin.get("/tasks?view=board&q=agent_working").text
    assert 'href="/tasks?q=agent_working&amp;view=board&amp;open=RUN-5"' in main_of(board)


def test_unknown_query_values_fall_back_to_defaults(admin, people):
    html = admin.get("/tasks?q=nope&group=x&view=<script>&closed=zz").text
    assert groups_in(html) == groups_in(admin.get("/tasks").text)
    assert "<table" in main_of(html)
    assert 'data-q="all" aria-current="true"' in html
    assert "<script>" not in main_of(html)


def test_external_title_is_escaped(admin, conn):
    new_work(conn, "<script>alert(1)</script>", source_type="github", source_key="<b>k</b>")
    html = admin.get("/tasks").text
    assert "<script>alert(1)</script>" not in html and "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "<b>k</b>" not in html
    board = admin.get("/tasks?view=board").text
    assert "<script>alert(1)</script>" not in board


# --- 빠른 필터 ---------------------------------------------------------------------------------------------


def test_quick_filters_and_counts(admin, member, conn, people):
    assert counts_in(admin.get("/tasks").text) == {"all": 5, "my_turn": 0, "unassigned": 2, "agent_working": 1}
    assert counts_in(member.get("/tasks").text) == {"all": 5, "my_turn": 1, "unassigned": 2, "agent_working": 1}
    assert keys_in(admin.get("/tasks?q=unassigned").text) == ["RUN-2", "RUN-1"]
    assert keys_in(admin.get("/tasks?q=agent_working").text) == ["RUN-5"]
    assert keys_in(member.get("/tasks?q=my_turn").text) == ["RUN-4"]
    assert keys_in(member.get("/tasks?view=my_turn").text) == ["RUN-4"]  # 15 의 옛 링크
    html = admin.get("/tasks?q=unassigned&group=status").text
    assert 'href="/tasks?q=agent_working&amp;group=status"' in html  # 필터 링크도 나머지 상태를 유지


def test_closed_scope_hides_old_closed_work_unless_all(admin, conn, people):
    old, _ = new_work(conn, "오래전 끝남")  # RUN-6
    set_status(conn, old, "완료", "PR 병합 — #1", closed_at="2026-01-01T00:00:00Z")
    assert "RUN-6" not in keys_in(admin.get("/tasks").text)
    assert "RUN-6" in keys_in(admin.get("/tasks?closed=all").text)


# --- 보드 --------------------------------------------------------------------------------------------------


def test_board_has_six_columns_and_cards(admin, conn, people):
    done, _ = new_work(conn, "끝난 업무")  # RUN-6
    set_status(conn, done, "완료", "PR 병합 — #1", closed_at=utc_now())
    html = main_of(admin.get("/tasks?view=board").text)
    columns = re.findall(r'<section class="board-col" data-column="(\w+)">.*?<h3>(.*?)\s*<span class="count">(\d+)</span>',
                         html, re.S)
    assert columns == [("waiting", "대기", "3"), ("agent_working", "에이전트 작업 중", "1"),
                       ("direct_working", "직접 작업 중", "0"), ("my_turn", "내 차례", "1"),
                       ("pr_review", "PR · 검토", "0"), ("done", "완료", "1")]
    waiting = html[html.index('data-column="waiting"'):html.index('data-column="agent_working"')]
    assert re.findall(r'data-work-key="(RUN-\d+)"', waiting) == ["RUN-2", "RUN-3", "RUN-1"]
    card = re.search(r'<a class="board-card" data-work-key="RUN-4".*?</a>', html, re.S).group(0)
    for text in ("RUN-4", "– 보통", "멤버 업무", "김멤버", "사람 요청 — 어느 쪽으로 고칠까요?"):
        assert text in card, text
    assert "<table" not in html


# --- 도구 막대·빈 상태·에이전트 없음 ---------------------------------------------------------------------------


def test_toolbar_links_and_register_button(admin, member, people):
    html = main_of(admin.get("/tasks").text)
    for href in ('href="/tasks?group=status"', 'href="/tasks?view=board"', 'href="/tasks?closed=all"',
                 'href="/tasks/new"'):
        assert href in html, href
    assert 'data-group-toggle="none"' in html  # 묶음 접기 버튼


def test_empty_list_and_empty_filter_have_a_sentence_and_next_action(admin, conn):
    html = main_of(admin.get("/tasks").text)
    assert "아직 업무가 없습니다." in html and 'href="/tasks/new"' in html
    new_work(conn, "하나")
    assign(conn, conn.execute("SELECT work_item_id FROM work_items").fetchone()[0], "agent", AGENT)
    html = main_of(admin.get("/tasks?q=unassigned").text)
    assert "담당 없는 업무가 없습니다." in html and ">전체 보기</a>" in html
    assert "내 차례인 업무가 없습니다." in main_of(admin.get("/tasks?q=my_turn").text)


def test_agents_zero_shows_runner_hint(client, conn):
    ensure_workspace(conn, NOW)
    admin = log_in(client)
    html = main_of(admin.get("/tasks").text)
    assert "러너를 붙이면 에이전트가 생깁니다" in html and 'href="/operator/github"' in html


def test_home_has_no_agent_or_chain_cards(admin, people):
    html = main_of(admin.get("/tasks").text)
    assert "agent-card" not in html and "러너를 붙이면" not in html
    assert 'href="/chains/' not in html


# --- 사이드바 ----------------------------------------------------------------------------------------------


def test_sidebar_has_new_items_and_no_recent_list(admin, people):
    sidebar = sidebar_of(admin.get("/tasks").text)
    nav = re.findall(r'<a href="([^"]+)"[^>]*>([^<]+)', sidebar[sidebar.index('class="nav"'):])
    assert [label.strip() for _, label in nav[:4]] == ["업무", "모니터링", "연결", "내 설정"]
    assert [href for href, _ in nav[:4]] == ["/tasks", "/metrics", "/operator/github", "/me"]
    assert "최근" not in sidebar and 'href="/tasks/new"' not in sidebar and "data-work-key" not in sidebar
    assert "시작하기" not in sidebar  # step 7 전까지 숨김
    assert "관리자 · 관리자" in sidebar and 'action="/logout"' in sidebar


def test_sidebar_turn_badge_counts_my_turn(admin, member, people):
    assert re.search(r'data-turn-count>(\d+)<', sidebar_of(member.get("/tasks").text)).group(1) == "1"
    assert re.search(r'data-turn-count>(\d+)<', sidebar_of(member.get("/me").text)).group(1) == "1"
    assert "data-turn-count" not in sidebar_of(admin.get("/tasks").text)  # 0 이면 배지 없음


def test_sidebar_marks_active_by_path_prefix(admin, people):
    nav = sidebar_of(admin.get("/operator").text)
    assert '<a href="/operator/github" class="active">' in nav
    assert 'href="/tasks" class="active"' not in nav
    assert '<a href="/tasks" class="active">' in sidebar_of(admin.get("/tasks").text)


def test_sidebar_hides_items_without_permission(admin, member, people, monkeypatch):
    from workflow.domain import team

    real = team.allowed_actions
    monkeypatch.setattr(team, "allowed_actions",
                        lambda role: real(role) - {team.VIEW_METRICS, team.EDIT_OWN_SETTINGS})
    sidebar = sidebar_of(member.get("/tasks").text)
    assert 'href="/metrics"' not in sidebar and 'href="/me"' not in sidebar
    assert 'href="/tasks"' in sidebar and 'href="/operator/github"' in sidebar
