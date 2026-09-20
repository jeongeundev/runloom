"""워크플로우 자동 구성 — 이슈 순서 배치와 Agent 배정. 규칙 기반이며 모든 결정에 이유 문장이 붙는다 (ADR-0004)."""

import pytest

from workflow.contracts.v1 import Capability, SelectionRecord
from workflow.domain.completion import criteria_template
from workflow.domain.composition import ChainPlan, PlanNode, Standalone, compose
from workflow.domain.selection import Candidate
from workflow.domain.task_sources import Issue

DIAGNOSE = Capability(code="operations.diagnose", scope={"workflow_id": "daily-report"})
MODIFY = Capability(code="code.modify", scope={"repository_id": "demo-report-repo"})

OPS = Candidate(agent_id="agent-ops-demo", capabilities=(DIAGNOSE,))
CODEX = Candidate(agent_id="agent-codex-mac", capabilities=(MODIFY,))
CLAUDE = Candidate(agent_id="agent-claude-mac", capabilities=(MODIFY,))
# 세션이 등록한 순서: 진단 API → Codex → Claude
PREFER = ["agent-ops-demo", "agent-codex-mac", "agent-claude-mac"]


def _issue(key: str, title: str, labels: tuple[str, ...], blocked_by: tuple[str, ...] = ()) -> Issue:
    return Issue(source="github", key=key, title=title, body=f"{key} 본문", labels=labels, blocked_by=blocked_by, url=None)


# step 3 fixture(github.json)와 같은 4개
ISSUE_41 = _issue("#41", "일일 보고서 생성 실패 (09-20 09:00)", ("incident", "workflow:daily-report", "run:daily-0920-0900"))
ISSUE_42 = _issue("#42", "집계 API 응답 형식 변경 대응", ("bug", "repo:demo-report-repo"), blocked_by=("#41",))
ISSUE_43 = _issue("#43", "변경 응답 형식 모니터링 알림 추가", ("enhancement", "repo:demo-report-repo"), blocked_by=("#42",))
ISSUE_44 = _issue("#44", "README 오타 수정", ("docs",))
FIXTURE = [ISSUE_41, ISSUE_42, ISSUE_43, ISSUE_44]


# --- 시연 fixture: #41 → #42 체인, #43·#44 Standalone -----------------------------------


def test_fixture_composes_diagnose_then_modify_chain():
    plan = compose(FIXTURE, [OPS, CODEX, CLAUDE], PREFER)

    assert isinstance(plan, ChainPlan)
    assert [node.issue.key for node in plan.nodes] == ["#41", "#42"]
    assert [item.issue.key for item in plan.standalone] == ["#43", "#44"]
    assert plan.title == "일일 보고서 생성 실패 (09-20 09:00) → 집계 API 응답 형식 변경 대응"
    assert plan.human_gate == "검토 승인 (사람) · 병합은 운영자 확인"


def test_fixture_first_node_is_manual_auto_complete_diagnosis():
    plan = compose(FIXTURE, [OPS, CODEX, CLAUDE], PREFER)
    node = plan.nodes[0]

    assert isinstance(node, PlanNode)
    assert node.issue is ISSUE_41
    assert node.capability == DIAGNOSE
    assert node.kind == "diagnosis"
    assert node.run_id == "daily-0920-0900"
    assert node.predecessor_key is None
    assert node.run_mode == "manual"
    assert node.completion_mode == "auto"
    assert node.criteria == tuple(criteria_template("diagnosis"))
    assert isinstance(node.selection, SelectionRecord)
    assert node.selection.task_id == "#41"
    assert node.selection.status == "selected"
    assert node.selection.selected_agent_id == "agent-ops-demo"
    assert node.selection.candidate_count == 1
    assert node.selection.reason == "operations.diagnose · workflow_id=daily-report 일치 후보 1개"


def test_fixture_second_node_is_auto_review_code_change_with_codex_default():
    plan = compose(FIXTURE, [OPS, CODEX, CLAUDE], PREFER)
    node = plan.nodes[1]

    assert node.issue is ISSUE_42
    assert node.capability == MODIFY
    assert node.kind == "code_change"
    assert node.run_id is None
    assert node.predecessor_key == "#41"
    assert node.run_mode == "auto"
    assert node.completion_mode == "review"
    assert node.criteria == tuple(criteria_template("code_change"))
    assert node.selection.task_id == "#42"
    assert node.selection.status == "selected"
    assert node.selection.selected_agent_id == "agent-codex-mac"
    assert node.selection.candidate_count == 1
    assert "후보 2개" in node.selection.reason
    assert "먼저 등록한 agent-codex-mac" in node.selection.reason


def test_fixture_reasons_cover_mapping_order_chain_mode_and_assignment():
    plan = compose(FIXTURE, [OPS, CODEX, CLAUDE], PREFER)
    first, second = plan.nodes

    assert first.reasons == (
        "라벨 incident·workflow:daily-report → operations.diagnose",
        "blocked_by 없음 — 가져온 순서대로 배치",
        "체인의 첫 업무 — 선행 없음",
        "직접 실행 — 흐름의 첫 업무는 사람이 시작",
        "자동 완료 — 진단 자동 판정기 있음",
        "operations.diagnose · workflow_id=daily-report 일치 후보 1개",
    )
    assert second.reasons == (
        "라벨 bug·repo:demo-report-repo → code.modify",
        "blocked_by #41 — #41 뒤에 배치",
        "선행 #41 (operations.diagnose) → code.modify 인계",
        "자동 실행 — 선행 완료 후 별도 조작 없이 착수",
        "검토 후 완료 — 기본값",
        "code.modify · repository_id=demo-report-repo 일치 후보 2개 — 먼저 등록한 agent-codex-mac 를 기본 선택 (변경 가능)",
    )


def test_fixture_standalone_keep_mapping_reason():
    plan = compose(FIXTURE, [OPS, CODEX, CLAUDE], PREFER)
    alert, readme = plan.standalone

    assert isinstance(alert, Standalone)
    assert alert.issue is ISSUE_43
    assert alert.mapping.capability is None
    assert alert.reason == "맞는 능력 코드 없음 (라벨: enhancement, repo:demo-report-repo)"
    assert readme.issue is ISSUE_44
    assert readme.mapping.capability is None
    assert readme.reason == "맞는 능력 코드 없음 (라벨: docs)"


def test_plan_is_immutable_value():
    plan = compose(FIXTURE, [OPS, CODEX, CLAUDE], PREFER)

    with pytest.raises(AttributeError):
        plan.title = "x"  # type: ignore[misc]
    with pytest.raises(AttributeError):
        plan.nodes[0].run_mode = "auto"  # type: ignore[misc]


# --- 배정 ---------------------------------------------------------------------------------


def test_single_modify_candidate_has_single_match_reason():
    plan = compose(FIXTURE, [OPS, CODEX], PREFER)
    node = plan.nodes[1]

    assert node.selection.selected_agent_id == "agent-codex-mac"
    assert node.selection.reason == "code.modify · repository_id=demo-report-repo 일치 후보 1개"


def test_no_diagnose_candidate_leaves_needs_selection_with_hint():
    plan = compose(FIXTURE, [CODEX, CLAUDE], ["agent-codex-mac", "agent-claude-mac"])
    node = plan.nodes[0]

    assert node.selection.status == "needs_selection"
    assert node.selection.selected_agent_id is None
    assert node.selection.candidate_count == 0
    assert node.selection.reason == "후보 없음"
    assert node.reasons[-1] == "후보 없음 — 에이전트를 등록하거나 직접 지정"
    # 배정 실패는 체인 구성·방식에 영향을 주지 않는다
    assert [n.issue.key for n in plan.nodes] == ["#41", "#42"]
    assert plan.nodes[1].predecessor_key == "#41"


def test_tie_without_prefer_leaves_needs_selection():
    plan = compose(FIXTURE, [OPS, CODEX, CLAUDE], [])
    node = plan.nodes[1]

    assert node.selection.status == "needs_selection"
    assert node.selection.candidate_count == 2
    assert node.selection.reason == "후보 2개 — 선택 필요"
    assert node.reasons[-1] == "후보 2개 — 선택 필요"


# --- 순서: blocked_by 위상 정렬 -------------------------------------------------------------


def test_input_order_does_not_override_blocked_by():
    plan = compose([ISSUE_42, ISSUE_41], [OPS, CODEX], PREFER)

    assert [node.issue.key for node in plan.nodes] == ["#41", "#42"]
    assert plan.nodes[1].predecessor_key == "#41"


def test_dependency_cycle_raises():
    a = _issue("#1", "진단", ("incident", "workflow:daily-report", "run:daily-0920-0900"), blocked_by=("#2",))
    b = _issue("#2", "수정", ("bug", "repo:demo-report-repo"), blocked_by=("#1",))

    with pytest.raises(ValueError, match="의존 순환: #1, #2"):
        compose([a, b], [OPS, CODEX], PREFER)


def test_self_dependency_is_a_cycle():
    a = _issue("#1", "진단", ("incident", "workflow:daily-report", "run:daily-0920-0900"), blocked_by=("#1",))

    with pytest.raises(ValueError, match="의존 순환"):
        compose([a], [OPS], PREFER)


def test_blocked_by_standalone_is_skipped_with_reason():
    fix = _issue("#42", "수정", ("bug", "repo:demo-report-repo"), blocked_by=("#44", "#41"))
    plan = compose([ISSUE_41, fix, ISSUE_44], [OPS, CODEX], PREFER)

    assert [node.issue.key for node in plan.nodes] == ["#41", "#42"]
    assert plan.nodes[1].predecessor_key == "#41"
    assert "선행 #44 은 이 제품의 에이전트가 맡지 않아 건너뜀" in plan.nodes[1].reasons
    assert "blocked_by #41 — #41 뒤에 배치" in plan.nodes[1].reasons
    assert [item.issue.key for item in plan.standalone] == ["#44"]


def test_blocked_by_only_standalone_becomes_first_without_dependency():
    fix = _issue("#42", "수정", ("bug", "repo:demo-report-repo"), blocked_by=("#44",))
    plan = compose([fix, ISSUE_44], [CODEX], ["agent-codex-mac"])

    assert [node.issue.key for node in plan.nodes] == ["#42"]
    assert plan.nodes[0].predecessor_key is None
    assert plan.nodes[0].run_mode == "manual"
    assert "선행 #44 은 이 제품의 에이전트가 맡지 않아 건너뜀" in plan.nodes[0].reasons
    assert "blocked_by 없음 — 가져온 순서대로 배치" in plan.nodes[0].reasons


def test_blocked_by_unknown_key_is_skipped_with_reason():
    fix = _issue("#42", "수정", ("bug", "repo:demo-report-repo"), blocked_by=("#99",))
    plan = compose([fix], [CODEX], ["agent-codex-mac"])

    assert [node.issue.key for node in plan.nodes] == ["#42"]
    assert "선행 #99 은 가져온 이슈에 없어 건너뜀" in plan.nodes[0].reasons


# --- 체인 구성: 진단 → 코드 수정 인계만 ------------------------------------------------------


def test_modify_after_modify_is_standalone_with_capability():
    second_fix = _issue("#45", "두 번째 수정", ("bug", "repo:demo-report-repo"), blocked_by=("#42",))
    plan = compose([ISSUE_41, ISSUE_42, second_fix], [OPS, CODEX], PREFER)

    assert [node.issue.key for node in plan.nodes] == ["#41", "#42"]
    (item,) = plan.standalone
    assert item.issue is second_fix
    assert item.mapping.capability == MODIFY
    assert item.reason == "진단 → 코드 수정 인계만 지원"


def test_diagnose_after_diagnose_is_standalone():
    second_diag = _issue("#46", "다른 진단", ("incident", "workflow:daily-report", "run:daily-0921-0900"))
    plan = compose([ISSUE_41, second_diag], [OPS], PREFER)

    assert [node.issue.key for node in plan.nodes] == ["#41"]
    (item,) = plan.standalone
    assert item.issue is second_diag
    assert item.mapping.capability == DIAGNOSE
    assert item.reason == "진단 → 코드 수정 인계만 지원"


def test_standalone_order_follows_input_order():
    second_fix = _issue("#45", "두 번째 수정", ("bug", "repo:demo-report-repo"), blocked_by=("#42",))
    plan = compose([ISSUE_44, ISSUE_41, second_fix, ISSUE_42], [OPS, CODEX], PREFER)

    assert [node.issue.key for node in plan.nodes] == ["#41", "#42"]
    assert [item.issue.key for item in plan.standalone] == ["#44", "#45"]


# --- 노드 1개·0개 --------------------------------------------------------------------------


def test_single_diagnosis_node_title_and_human_gate():
    plan = compose([ISSUE_41], [OPS], PREFER)

    assert plan.title == "일일 보고서 생성 실패 (09-20 09:00)"
    assert plan.human_gate == "완료 확인 (사람)"
    assert plan.nodes[0].completion_mode == "auto"


def test_single_modify_node_is_manual_review():
    plan = compose([ISSUE_42], [CODEX], ["agent-codex-mac"])
    node = plan.nodes[0]

    assert node.predecessor_key is None
    assert node.run_mode == "manual"
    assert node.completion_mode == "review"
    assert plan.title == "집계 API 응답 형식 변경 대응"
    assert plan.human_gate == "검토 승인 (사람) · 병합은 운영자 확인"


def test_no_capable_issue_gives_empty_plan():
    plan = compose([ISSUE_43, ISSUE_44], [OPS, CODEX], PREFER)

    assert plan.nodes == ()
    assert [item.issue.key for item in plan.standalone] == ["#43", "#44"]
    assert plan.title == ""
    assert plan.human_gate == ""


def test_empty_input_gives_empty_plan():
    plan = compose([], [], [])

    assert plan == ChainPlan(nodes=(), standalone=(), human_gate="", title="")
