"""업무 목록 모델(ARCHITECTURE "업무 화면 — phase 16" 끝난 업무·빠른 필터·묶기 순서·보드 칸·다음 할 일)."""

from dataclasses import replace

import pytest

from workflow.domain.work_list import (
    BOARD_COLUMNS,
    CLOSED_GROUP_KEY,
    CLOSED_GROUP_LABEL,
    CLOSED_RECENT_DAYS,
    CLOSED_SCOPES,
    GROUP_BYS,
    HIDEABLE_COLUMNS,
    NO_NEXT_ACTION,
    PRIORITY_ORDER,
    QUICK_FILTERS,
    VIEWS,
    ListQuery,
    WorkRow,
    board_columns,
    filter_counts,
    filter_rows,
    group_rows,
    hidden_columns,
    next_action,
    parse_list_query,
    shown_next_action,
)
from workflow.domain.work_status import WORK_STATUSES

ME = "mem-me"


def row(n: int, *, status: str = "대기", assignee: tuple[str, str] | None = None, name: str | None = None,
        active: bool = True, priority: str = "normal", updated: str = "2026-09-30T00:00:00Z",
        recipients: tuple[str, ...] = (), closed_at: str | None = None, repository: str | None = None) -> WorkRow:
    return WorkRow(
        work_item_id=f"wi-{n}", key_number=n, work_key=f"RUN-{n}", source_type="manual", source_key=None,
        source_url=None, title=f"업무 {n}", assignee_type=assignee[0] if assignee else None,
        assignee_id=assignee[1] if assignee else None, assignee_name=name, assignee_active=active,
        priority=priority, kind="bug_fix", kind_label="버그 수정", status=status, status_reason="",
        next_action="", recipients=recipients, updated_at=updated, closed_at=closed_at, repository=repository,
    )


def keys(rows) -> list[int]:
    return [r.key_number for r in rows]


def test_constants_follow_the_architecture_table():
    assert QUICK_FILTERS == ("all", "my_turn", "unassigned", "agent_working")
    assert GROUP_BYS == ("assignee", "status", "repo")
    assert VIEWS == ("list", "board")
    assert CLOSED_SCOPES == ("recent", "all")
    assert CLOSED_RECENT_DAYS == 14
    assert PRIORITY_ORDER == ("high", "normal", "low")
    assert [c[0] for c in BOARD_COLUMNS] == ["waiting", "agent_working", "direct_working", "my_turn", "pr_review",
                                            "done"]


# --- 주소 쿼리 ---------------------------------------------------------------------------


def test_parse_list_query_defaults_and_unknown_values():
    assert parse_list_query() == ListQuery(q="all", group="assignee", view="list", closed="recent", open_key=None)
    assert parse_list_query(q="nope", group="x", view="grid", closed="old", open="zzz") == parse_list_query()
    assert parse_list_query(q="unassigned", group="status", view="board", closed="all", open="RUN-12") == ListQuery(
        q="unassigned", group="status", view="board", closed="all", open_key=12)


@pytest.mark.parametrize("value", ["RUN-0", "run-12", "OPS-12", "RUN-12x", "RUN-", " RUN-12", "RUN-1234567890"])
def test_parse_list_query_open_needs_our_key_format(value):
    assert parse_list_query(open=value).open_key is None


def test_parse_list_query_reads_the_old_my_turn_view_as_a_filter():
    query = parse_list_query(view="my_turn")
    assert (query.q, query.view) == ("my_turn", "list")


# --- 빠른 필터 ----------------------------------------------------------------------------


def test_filter_all_keeps_everything():
    rows = [row(1), row(2, status="완료", closed_at="2026-09-29T00:00:00Z")]
    assert filter_rows(rows, "all", member_id=ME) == rows


def test_filter_my_turn_needs_the_status_and_the_member_among_recipients():
    rows = [
        row(1, status="내 차례", recipients=(ME,)),
        row(2, status="내 차례", recipients=("mem-other",)),  # 다른 사람 차례
        row(3, status="대기", recipients=(ME,)),  # 상태가 내 차례가 아님
        row(4, status="내 차례", recipients=("mem-other", ME)),
    ]
    assert keys(filter_rows(rows, "my_turn", member_id=ME)) == [1, 4]


def test_filter_unassigned_and_agent_working_drop_closed_work():
    rows = [
        row(1),
        row(2, status="종료", closed_at="2026-09-29T00:00:00Z"),
        row(3, assignee=("agent", "agent-a"), name="코덱스"),
        row(4, assignee=("agent", "agent-a"), status="완료", closed_at="2026-09-29T00:00:00Z"),
        row(5, assignee=("member", ME), name="나"),
    ]
    assert keys(filter_rows(rows, "unassigned", member_id=ME)) == [1]
    assert keys(filter_rows(rows, "agent_working", member_id=ME)) == [3]


def test_unknown_filter_reads_as_all():
    rows = [row(1), row(2, assignee=("member", ME), name="나")]
    assert filter_rows(rows, "nope", member_id=ME) == rows


def test_filter_counts_count_every_quick_filter():
    rows = [
        row(1, status="내 차례", recipients=(ME,)),
        row(2, assignee=("agent", "agent-a"), name="코덱스"),
        row(3, status="완료", closed_at="2026-09-29T00:00:00Z"),
    ]
    assert filter_counts(rows, member_id=ME) == {"all": 3, "my_turn": 1, "unassigned": 1, "agent_working": 1}


def test_repo_filter_keeps_only_that_repository_and_counts_follow_it():
    rows = [
        row(1, repository="acme/web"),
        row(2, repository="acme/billing", assignee=("agent", "a"), name="에이"),
        row(3),
        row(4, repository="acme/billing"),
    ]
    assert keys(filter_rows(rows, "all", member_id=ME, repo="acme/billing")) == [2, 4]
    assert keys(filter_rows(rows, "unassigned", member_id=ME, repo="acme/billing")) == [4]
    assert keys(filter_rows(rows, "all", member_id=ME, repo=None)) == [1, 2, 3, 4]
    assert filter_counts(rows, member_id=ME, repo="acme/billing") == {
        "all": 2, "my_turn": 0, "unassigned": 1, "agent_working": 1}


def test_parse_list_query_repo_takes_only_a_workspace_repository():
    repos = ["acme/billing", "acme/web"]
    assert parse_list_query().repo is None
    assert parse_list_query(repo="acme/web", repos=repos).repo == "acme/web"
    assert parse_list_query(repo="ACME/Billing", repos=repos).repo == "acme/billing"  # 목록 표기로
    for value in ("acme/nope", "", "../etc", "acme/web "):
        assert parse_list_query(repo=value, repos=repos).repo is None
    assert parse_list_query(repo="acme/web").repo is None  # 목록 없음
    assert parse_list_query(group="repo").group == "repo"


# --- 묶기 ---------------------------------------------------------------------------------


def test_group_by_assignee_orders_none_me_members_agents_inactive():
    rows = [
        row(1, assignee=("member", "mem-z"), name="하늘", active=False),
        row(2, assignee=("agent", "agent-b"), name="코덱스"),
        row(3, assignee=("member", "mem-b"), name="나리"),
        row(4, assignee=("agent", "agent-a"), name="클로드"),
        row(5, assignee=("member", ME), name="가람"),
        row(6),
        row(7, assignee=("member", "mem-a"), name="나리"),  # 이름이 같으면 id 순
        row(8, assignee=("member", "mem-c"), name="다온"),
    ]
    groups = group_rows(rows, "assignee", member_id=ME)
    assert [(g.key, g.label) for g in groups] == [
        ("none", "담당 없음"),
        (f"member:{ME}", "가람 (나)"),
        ("member:mem-a", "나리"),
        ("member:mem-b", "나리"),
        ("member:mem-c", "다온"),
        ("agent:agent-b", "코덱스"),
        ("agent:agent-a", "클로드"),
        ("member:mem-z", "하늘 (비활성)"),
    ]
    assert [keys(g.rows) for g in groups] == [[6], [5], [7], [3], [8], [2], [4], [1]]


def test_rows_inside_a_group_order_by_priority_then_recent_then_key():
    rows = [
        row(1, priority="low", updated="2026-09-30T09:00:00Z"),
        row(2, priority="normal", updated="2026-09-29T00:00:00Z"),
        row(3, priority="high", updated="2026-09-01T00:00:00Z"),
        row(4, priority="normal", updated="2026-09-30T00:00:00Z"),
        row(5, priority="normal", updated="2026-09-30T00:00:00Z"),
    ]
    (group,) = group_rows(rows, "assignee", member_id=ME)
    assert keys(group.rows) == [3, 5, 4, 2, 1]


def test_empty_groups_are_not_made():
    assert group_rows([], "assignee", member_id=ME) == []
    assert [g.key for g in group_rows([row(1, assignee=("agent", "a"), name="에이")], "assignee", member_id=ME)] == [
        "agent:a"]


def test_group_by_status_follows_work_statuses_order():
    rows = [row(1, status="완료", closed_at="x"), row(2, status="새로 들어옴"), row(3, status="내 차례"),
            row(4, status="새로 들어옴")]
    groups = group_rows(rows, "status", member_id=ME)
    # 끝난 업무는 `완료`·`종료` 묶음 대신 맨 아래 "끝난 업무" 묶음 (phase 23 step 8)
    assert [(g.key, g.label) for g in groups] == [("status:새로 들어옴", "새로 들어옴"), ("status:내 차례", "내 차례"),
                                                  (CLOSED_GROUP_KEY, CLOSED_GROUP_LABEL)]
    assert [keys(g.rows) for g in groups] == [[4, 2], [3], [1]]
    assert [g.label for g in groups[:-1]] == [s for s in WORK_STATUSES if s in {r.status for r in rows[1:]}]


def test_group_by_repo_orders_by_name_ignoring_case_and_puts_no_repo_last():
    rows = [row(1), row(2, repository="acme/web"), row(3, repository="Acme/Billing"), row(4, repository="acme/web"),
            row(5, repository="zeta/app")]
    groups = group_rows(rows, "repo", member_id=ME)
    assert [(g.key, g.label) for g in groups] == [
        ("repo:Acme/Billing", "Acme/Billing"),
        ("repo:acme/web", "acme/web"),
        ("repo:zeta/app", "zeta/app"),
        ("repo:none", "저장소 없음"),
    ]
    assert [keys(g.rows) for g in groups] == [[3], [4, 2], [5], [1]]
    assert [g.key for g in group_rows([row(1, repository="a/b")], "repo", member_id=ME)] == ["repo:a/b"]


def test_unknown_group_reads_as_assignee():
    rows = [row(1), row(2, assignee=("member", ME), name="가람")]
    assert group_rows(rows, "nope", member_id=ME) == group_rows(rows, "assignee", member_id=ME)


# --- 끝난 업무 묶음 (phase 23 step 8) -------------------------------------------------------


def test_closed_group_constants():
    assert (CLOSED_GROUP_KEY, CLOSED_GROUP_LABEL) == ("closed", "끝난 업무")


def _mixed_rows() -> list[WorkRow]:
    return [
        row(1, status="완료", closed_at="2026-09-28T00:00:00Z", repository="acme/web"),
        row(2, status="새로 들어옴", repository="acme/web"),
        row(3, status="종료", assignee=("member", ME), name="가람", closed_at="2026-09-29T00:00:00Z"),
        row(4, status="대기", assignee=("agent", "agent-a"), name="클로드", repository="zeta/app"),
        row(5, status="완료", assignee=("agent", "agent-a"), name="클로드", closed_at="2026-09-29T00:00:00Z",
            priority="high"),
        row(6, status="새로 들어옴"),
    ]


@pytest.mark.parametrize("by", GROUP_BYS)
def test_closed_work_goes_to_one_closed_group_at_the_bottom_for_every_grouping(by):
    groups = group_rows(_mixed_rows(), by, member_id=ME)
    assert groups[-1].key == CLOSED_GROUP_KEY and groups[-1].label == CLOSED_GROUP_LABEL
    # 묶음 안 순서 = `closed_at` 최근순 → 키 번호 내림차순 (우선순위 무관)
    assert keys(groups[-1].rows) == [5, 3, 1]
    for group in groups[:-1]:
        assert all(r.status not in ("완료", "종료") for r in group.rows)
    assert sum(len(g.rows) for g in groups) == len(_mixed_rows())


def test_no_closed_group_without_closed_rows():
    assert all(g.key != CLOSED_GROUP_KEY for g in group_rows([row(1), row(2)], "status", member_id=ME))


def test_assignee_grouping_puts_closed_work_out_of_member_and_agent_groups():
    groups = group_rows(_mixed_rows(), "assignee", member_id=ME)
    assert [(g.key, keys(g.rows)) for g in groups] == [
        ("none", [6, 2]), ("agent:agent-a", [4]), (CLOSED_GROUP_KEY, [5, 3, 1])]


@pytest.mark.parametrize("by", GROUP_BYS)
@pytest.mark.parametrize("repo", [None, "acme/web"])
def test_group_head_counts_match_quick_filter_counts(by, repo):
    rows = _mixed_rows()
    counts = filter_counts(rows, member_id=ME, repo=repo)
    shown = filter_rows(rows, "all", member_id=ME, repo=repo)
    groups = {g.key: len(g.rows) for g in group_rows(shown, by, member_id=ME)}
    assert sum(groups.values()) == counts["all"]
    closed = sum(1 for r in shown if r.status in ("완료", "종료"))
    assert groups.get(CLOSED_GROUP_KEY, 0) == closed
    if by == "assignee":
        assert groups.get("none", 0) == counts["unassigned"]
    # 빠른 필터 `unassigned` 로 거른 목록의 묶음 머리 건수도 그 필터 건수와 같다
    unassigned = filter_rows(rows, "unassigned", member_id=ME, repo=repo)
    assert sum(len(g.rows) for g in group_rows(unassigned, by, member_id=ME)) == counts["unassigned"]


# --- 같은 값 칸 숨김 (phase 23 step 8) -------------------------------------------------------


def test_hideable_columns():
    assert HIDEABLE_COLUMNS == ("priority", "kind")


def test_hidden_columns_hide_columns_where_every_row_is_the_same():
    assert hidden_columns([row(1), row(2)]) == frozenset({"priority", "kind"})
    assert hidden_columns([row(1)]) == frozenset({"priority", "kind"})


def test_hidden_columns_keep_columns_that_differ():
    assert hidden_columns([row(1), row(2, priority="high")]) == frozenset({"kind"})
    other_kind = replace(row(2), kind="code_review", kind_label="커밋 검토")
    assert hidden_columns([row(1), other_kind]) == frozenset({"priority"})
    assert hidden_columns([row(1, priority="low"), replace(row(2), kind="code_review")]) == frozenset()


def test_hidden_columns_hide_nothing_without_rows():
    assert hidden_columns([]) == frozenset()


# --- 보드 ---------------------------------------------------------------------------------


def test_board_has_six_columns_new_goes_to_waiting_and_closed_is_left_out():
    rows = [
        row(1, status="새로 들어옴"), row(2, status="대기", priority="high"), row(3, status="에이전트 작업 중"),
        row(4, status="직접 작업 중"), row(5, status="내 차례"), row(6, status="PR · 검토"),
        row(7, status="완료", closed_at="x"), row(8, status="종료", closed_at="x"),
    ]
    columns = board_columns(rows)
    assert [(c.key, c.label) for c in columns] == [
        ("waiting", "대기"), ("agent_working", "에이전트 작업 중"), ("direct_working", "직접 작업 중"),
        ("my_turn", "내 차례"), ("pr_review", "PR · 검토"), ("done", "완료"),
    ]
    assert [keys(c.rows) for c in columns] == [[2, 1], [3], [4], [5], [6], [7]]


def test_board_keeps_empty_columns():
    assert [len(c.rows) for c in board_columns([])] == [0] * 6


# --- 다음 할 일 ---------------------------------------------------------------------------


def test_next_action_takes_the_first_match():
    kwargs = {"request_question": "어느 쪽으로 고칠까요?\n자세히", "direct_member_name": "가람", "pr_label": "PR #12",
              "status_reason": "대기"}
    assert next_action(**kwargs) == "어느 쪽으로 고칠까요?"
    assert next_action(**{**kwargs, "request_question": None}) == "직접 작업 중 · 가람"
    assert next_action(**{**kwargs, "request_question": None, "direct_member_name": None}) == "PR #12"
    assert next_action(request_question=None, direct_member_name=None, pr_label=None, status_reason="대기") == "대기"
    assert next_action(request_question=None, direct_member_name=None, pr_label=None, status_reason="") == ""


def test_next_action_cuts_the_question_to_80_characters():
    assert next_action(request_question="가" * 100, direct_member_name=None, pr_label=None,
                       status_reason="") == "가" * 80


def test_shown_next_action_blanks_text_that_repeats_the_status():
    assert NO_NEXT_ACTION == "—"
    assert shown_next_action(replace(row(1, status="대기"), next_action="대기")) == NO_NEXT_ACTION
    assert shown_next_action(replace(row(1, status="새로 들어옴"), next_action="담당 없음")) == NO_NEXT_ACTION


def test_shown_next_action_keeps_other_text():
    assert shown_next_action(replace(row(1, status="대기"), next_action="PR #12")) == "PR #12"
    # 담당이 있는데 `담당 없음` 이 남은 경우는 숨기지 않는다 — 담당 없음 행에서만
    assigned = replace(row(1, assignee=("member", ME), name="가람"), next_action="담당 없음")
    assert shown_next_action(assigned) == "담당 없음"
    assert shown_next_action(replace(row(1, status="내 차례"), next_action="어느 쪽으로 고칠까요?")) == "어느 쪽으로 고칠까요?"


def test_work_row_is_frozen():
    with pytest.raises(AttributeError):
        row(1).title = "x"  # type: ignore[misc]
    assert replace(row(1), title="x").title == "x"
