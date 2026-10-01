"""Jira 가져오기 규칙 — ARCHITECTURE "Jira 소스 — phase 18" 가져오기·원본 조회·세 순간, ADR-0024 결정 4~9·11."""

import pytest

from workflow.contracts.github import GitHubSourceConfig
from workflow.contracts.jira import JiraIssueSnapshot, JiraProjectConfig, JiraTransition
from workflow.contracts.v1 import BUILTIN_KINDS
from workflow.domain import jira_intake
from workflow.domain.field_mapping import MappingRow
from workflow.domain.work_status import WORK_STATUSES

BUG_FIX = next(spec for spec in BUILTIN_KINDS if spec.kind == "bug_fix")
SITE = "https://shop.atlassian.net"


def _project(**overrides) -> JiraProjectConfig:
    data = {
        "source_id": "jps-0000000a",
        "session_id": "s-1",
        "project_id": "10000",
        "project_key": "SHOP",
        "project_name": "쇼핑몰",
        "github_source_id": "ghs-00000001",
        "issue_types": [],
        "start_mode": "from_now",
        "start_at": "2026-09-29T00:00:00Z",
        "status_on_start": "진행 중",
        "status_on_review": "리뷰중",
        "status_on_done": None,
        "followup_issue_type": None,
        "enabled": True,
    }
    return JiraProjectConfig.model_validate({**data, **overrides})


def _snapshot(**overrides) -> JiraIssueSnapshot:
    data = {
        "issue_id": "10012",
        "key": "SHOP-12",
        "project_id": "10000",
        "summary": "쿠폰 적용 오류",
        "description_text": "  쿠폰이 안 먹습니다.\n\n## 재현 절차\n\n1. 쿠폰 입력\n",
        "status_name": "대기",
        "status_category": "new",
        "issue_type": "버그",
        "priority": "High",
        "labels": ["checkout"],
        "created": "2026-09-29T10:00:00.000+09:00",
        "updated": "2026-09-29T11:00:00.000+09:00",
    }
    return JiraIssueSnapshot.model_validate({**data, **overrides})


def _run(**overrides) -> GitHubSourceConfig:
    data = {
        "source_id": "ghs-00000001",
        "repository_full_name": "acme/shop",
        "workflow_repository_id": "shop",
        "label_filter": ["bug"],
        "selected_issue_numbers": [],
        "start_at": "2026-09-01T00:00:00Z",
        "fix_verification_profile_id": "vp-pytest",
        "review_agent_id": "agent-review",
        "run_mode": "manual",
        "max_rework_rounds": 2,
        "intake": "filtered",
        "trigger_label": "runloom",
        "enabled": True,
        "config_revision": 3,
    }
    return GitHubSourceConfig.model_validate({**data, **overrides})


# --- JQL ---

def test_search_jql_with_and_without_cursor():
    assert jira_intake.search_jql("10000", None) == \
        "project = 10000 AND statusCategory != Done ORDER BY updated ASC, key ASC"
    assert jira_intake.search_jql("10000", 1727571600000) == \
        "project = 10000 AND updated >= 1727571600000 ORDER BY updated ASC, key ASC"
    assert jira_intake.search_jql("10000", 0) == "project = 10000 AND updated >= 0 ORDER BY updated ASC, key ASC"


@pytest.mark.parametrize("project_id", ["SHOP", "0", "10000 OR project = 2", '1"', "1\\", "", "-1", "１"])
def test_search_jql_rejects_non_numeric_project(project_id):
    with pytest.raises(ValueError):
        jira_intake.search_jql(project_id, None)


@pytest.mark.parametrize("cursor", [-1, True, 1.5, "1"])
def test_search_jql_rejects_bad_cursor(cursor):
    with pytest.raises(ValueError):
        jira_intake.search_jql("10000", cursor)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("runloom-RUN-13", '"runloom-RUN-13"'),
        ('a"b', '"a\\"b"'),
        ("a\\b", '"a\\\\b"'),
        ('\\"', '"\\\\\\""'),
        ("줄\n바꿈", '"줄\\n바꿈"'),
    ],
)
def test_jql_string_escapes_quotes_and_backslashes(value, expected):
    assert jira_intake.jql_string(value) == expected


def test_jql_string_cannot_break_out():
    quoted = jira_intake.jql_string('x" OR project = 1 OR labels = "y')
    inner = quoted[1:-1]
    # 이스케이프되지 않은 따옴표가 안에 없다
    i = 0
    while i < len(inner):
        if inner[i] == "\\":
            i += 2
            continue
        assert inner[i] != '"'
        i += 1


# --- 커서 ---

def test_updated_ms_and_next_cursor():
    a = _snapshot(updated="2026-09-29T11:00:00.000+09:00")
    b = _snapshot(issue_id="10013", key="SHOP-13", updated="2026-09-29T02:00:00.123Z")
    assert jira_intake.updated_ms(a) == 1790647200000
    assert jira_intake.updated_ms(b) == 1790647200123
    assert jira_intake.next_cursor_ms([a, b], None) == 1790647200123
    assert jira_intake.next_cursor_ms([a], 1790647200500) == 1790647200500  # 뒤로 가지 않는다
    assert jira_intake.next_cursor_ms([], 5) == 5
    assert jira_intake.next_cursor_ms([], None) is None


def test_initial_cursor_by_start_mode():
    assert jira_intake.initial_cursor_ms("from_now", "2026-09-29T02:00:00Z") == 1790647200000
    assert jira_intake.initial_cursor_ms("all_open", "2026-09-29T02:00:00Z") is None


# --- 받기 ---

def test_accept_rules():
    project = _project()
    assert jira_intake.accept(project, _snapshot())
    assert not jira_intake.accept(project, _snapshot(project_id="10001"))
    assert not jira_intake.accept(project, _snapshot(status_category="done"))
    assert not jira_intake.accept(project, _snapshot(created="2026-09-28T23:59:59Z"))
    assert jira_intake.accept(project, _snapshot(created="2026-09-29T00:00:00Z"))
    assert jira_intake.accept(_project(start_mode="all_open"), _snapshot(created="2020-01-01T00:00:00Z"))
    typed = _project(issue_types=["Bug", "작업"])
    assert jira_intake.accept(typed, _snapshot(issue_type="bug"))
    assert jira_intake.accept(typed, _snapshot(issue_type="작업"))
    assert not jira_intake.accept(typed, _snapshot(issue_type="버그"))


def test_issue_state_by_category():
    assert jira_intake.issue_state(_snapshot(status_category="new")) == "open"
    assert jira_intake.issue_state(_snapshot(status_category="indeterminate")) == "open"
    assert jira_intake.issue_state(_snapshot(status_category="done", status_name="종료")) == "closed"


def test_mapping_values_and_kind_priority():
    snapshot = _snapshot(labels=["checkout", "urgent"], issue_type="버그", priority="High")
    assert jira_intake.mapping_values(snapshot) == ("checkout", "urgent", "버그", "High")
    assert jira_intake.mapping_values(_snapshot(labels=[], priority=None)) == ("버그",)
    rows = [
        MappingRow("github", "kind", "*", "code_review", 1),
        MappingRow("jira", "kind", "작업", "write_docs", 1),
        MappingRow("jira", "kind", "*", "bug_fix", 2),
        MappingRow("jira", "priority", "high", "high", 1),
    ]
    assert jira_intake.issue_kind(rows, _snapshot(issue_type="작업")) == "write_docs"
    assert jira_intake.issue_kind(rows, snapshot) == "bug_fix"
    assert jira_intake.issue_kind([r for r in rows if r.source_type == "github"], snapshot) is None
    assert jira_intake.issue_priority(rows, snapshot) == "high"
    assert jira_intake.issue_priority(rows, _snapshot(priority=None)) == "normal"


# --- 업무 칸 ---

def test_task_input_and_work_fields():
    snapshot = _snapshot()
    assert jira_intake.task_input(snapshot) == ("쿠폰 적용 오류", "쿠폰이 안 먹습니다.\n\n## 재현 절차\n\n1. 쿠폰 입력")
    fields = jira_intake.work_fields(snapshot, site_url=SITE)
    assert fields.title == "쿠폰 적용 오류"
    assert fields.request == "쿠폰이 안 먹습니다.\n\n## 재현 절차\n\n1. 쿠폰 입력"
    assert fields.form == {"steps_to_reproduce": {"value": "1. 쿠폰 입력", "source": "jira_description:## 재현 절차"}}
    assert fields.mapping_values == ("checkout", "버그", "High")
    assert fields.source_item_id == "10012"
    assert fields.source_key == "SHOP-12"
    assert fields.source_url == "https://shop.atlassian.net/browse/SHOP-12"
    assert fields.source_state == "대기"
    assert fields.state == "open"


def test_issue_url():
    assert jira_intake.issue_url(SITE, "SHOP-12") == "https://shop.atlassian.net/browse/SHOP-12"
    with pytest.raises(ValueError):
        jira_intake.issue_url("https://evil.com", "SHOP-12")
    with pytest.raises(ValueError):
        jira_intake.issue_url(SITE, "../x")


def test_snapshot_to_task_spec():
    spec = jira_intake.snapshot_to_task_spec(_project(), _run(), _snapshot(), kind=BUG_FIX, session_id="s-1",
                                             task_id="t-1")
    assert spec["title"] == "쿠폰 적용 오류"
    assert spec["request"].startswith("쿠폰이 안 먹습니다.")
    assert spec["kind"] == "bug_fix"
    assert spec["required_capability"] == {"code": BUG_FIX.capability_code, "scope": {BUG_FIX.scope_key: "shop"}}
    assert spec["run_mode"] == "manual"
    assert spec["source_ref"] == "SHOP-12"
    assert spec["status"] == "대기"
    assert spec["revision"] == 1
    assert spec["predecessor_task_id"] is None
    # App 소스처럼 로컬 저장소 id 가 없으면 저장소 이름 — 매칭이 채운다
    app = jira_intake.snapshot_to_task_spec(_project(), _run(intake="all_open", workflow_repository_id=None),
                                            _snapshot(), kind=BUG_FIX, session_id="s-1", task_id="t-1")
    assert app["required_capability"]["scope"] == {BUG_FIX.scope_key: "acme/shop"}


def test_run_config_is_all_open_copy():
    run = _run()
    copied = jira_intake.run_config(run)
    assert copied.intake == "all_open"
    assert copied.trigger_label is None
    assert copied.model_dump(exclude={"intake", "trigger_label"}) == run.model_dump(exclude={"intake", "trigger_label"})
    assert run.intake == "filtered"  # 원본은 그대로


def test_intake_facts():
    waiting = jira_intake.intake_facts(request="r", run_mode="manual", state="open", delegated_by=None,
                                       max_rework_rounds=2)
    assert waiting.assignee_ids == ()  # 수정 단계 — None 이 아니다
    assert waiting.bindings == {}
    assert waiting.request_required is True
    assert waiting.delegated is False
    assert waiting.run_mode == "manual"
    assert waiting.source_state == "open"
    assert waiting.max_rework_rounds == 2
    operator = jira_intake.intake_facts(request="r", run_mode="manual", state="closed", delegated_by="operator",
                                        max_rework_rounds=1)
    assert operator.delegated is True and operator.run_mode == "auto" and operator.source_state == "closed"
    followup = jira_intake.intake_facts(request="r", run_mode="manual", state="open", delegated_by="followup",
                                        max_rework_rounds=1)
    assert followup.delegated is True and followup.run_mode == "manual"


# --- 세 순간 ---

@pytest.mark.parametrize(
    ("status", "moment"),
    [
        ("새로 들어옴", None),
        ("대기", None),
        ("에이전트 작업 중", "start"),
        ("직접 작업 중", "start"),
        ("내 차례", None),
        ("PR · 검토", "review"),
        ("완료", "done"),
        ("종료", None),
        ("모르는 상태", None),
    ],
)
def test_moment_for(status, moment):
    assert jira_intake.moment_for(status) == moment


def test_moment_table_covers_all_work_statuses():
    assert set(jira_intake.MOMENT_OF_STATUS) <= set(WORK_STATUSES)
    assert set(jira_intake.MOMENT_OF_STATUS.values()) == set(jira_intake.JIRA_MOMENTS)


def test_moment_target_from_project():
    project = _project(status_on_start="진행 중", status_on_review="리뷰중", status_on_done=None)
    assert jira_intake.moment_target(project, "start") == "진행 중"
    assert jira_intake.moment_target(project, "review") == "리뷰중"
    assert jira_intake.moment_target(project, "done") is None
    assert jira_intake.MOMENT_COLUMNS == {"start": "status_on_start", "review": "status_on_review",
                                          "done": "status_on_done"}
    with pytest.raises(KeyError):
        jira_intake.moment_target(project, "other")


# --- 전환 고르기 ---

def _transition(tid: str, to_name: str, category: str = "indeterminate") -> JiraTransition:
    return JiraTransition(transition_id=tid, name=f"→ {to_name}", to_name=to_name, to_category=category)


def test_choose_transition():
    transitions = [_transition("11", "대기", "new"), _transition("21", "진행 중"), _transition("31", "In Review"),
                   _transition("41", "진행 중")]
    assert jira_intake.choose_transition(transitions, "진행 중") == "21"
    assert jira_intake.choose_transition(transitions, "  in review ") == "31"
    assert jira_intake.choose_transition(transitions, "IN REVIEW") == "31"
    assert jira_intake.choose_transition(transitions, "종료") is None
    assert jira_intake.choose_transition([], "진행 중") is None


def test_already_in_status():
    assert jira_intake.already_in("In Review", " in review")
    assert jira_intake.already_in("진행 중", "진행 중")
    assert not jira_intake.already_in("대기", "진행 중")


# --- 후속 이슈 등록 (step 8) ---

def test_followup_labels_and_key_number():
    assert jira_intake.followup_label(13) == "runloom-RUN-13"
    assert jira_intake.followup_labels(13) == ("runloom", "runloom-RUN-13")
    assert jira_intake.JIRA_LINK_TYPE == "Relates"
    for bad in (0, -1, True, "13", 1.5):
        with pytest.raises(ValueError):
            jira_intake.followup_label(bad)
    assert jira_intake.followup_key_number(["frontend", "runloom", "runloom-RUN-13"]) == 13
    assert jira_intake.followup_key_number(["runloom"]) is None
    assert jira_intake.followup_key_number([]) is None
    # 형식이 정확히 같을 때만 — 접두·접미·0 시작·소문자 키는 아니다
    for label in ("x-runloom-RUN-13", "runloom-RUN-13x", "runloom-RUN-013", "runloom-run-13", "runloom-RUN-"):
        assert jira_intake.followup_key_number([label]) is None


def test_followup_description_shape():
    text = jira_intake.followup_description(work_key="RUN-13", cause_key="SHOP-12", request="  검토 지적 고치기\n- 하나  ",
                                            work_url="https://runloom.example/work/RUN-13")
    assert text == (
        "Runloom 후속 업무 RUN-13 — SHOP-12 의 결과로 생겼습니다.\n\n"
        "검토 지적 고치기\n- 하나\n\n"
        "Runloom 업무: https://runloom.example/work/RUN-13"
    )
    # 공개 주소가 없으면 그 줄이 없고, 빈 요청이면 요청 절이 없다. 요청은 앞 2000자만
    assert jira_intake.followup_description(work_key="RUN-2", cause_key="SHOP-1", request="", work_url=None) == (
        "Runloom 후속 업무 RUN-2 — SHOP-1 의 결과로 생겼습니다.")
    long = jira_intake.followup_description(work_key="RUN-2", cause_key="SHOP-1", request="가" * 2500, work_url=None)
    assert long.endswith("\n\n" + "가" * 2000)


def test_followup_summary_is_cut_at_255():
    assert jira_intake.followup_summary("짧은 제목") == "짧은 제목"
    assert jira_intake.followup_summary("가" * 300) == "가" * 255
