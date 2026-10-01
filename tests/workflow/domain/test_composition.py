"""워크플로우 자동 구성 — 이슈 순서 배치와 Agent 배정. 규칙 기반이며 모든 결정에 이유 문장이 붙는다 (ADR-0004)."""

from dataclasses import fields

import pytest

from workflow.contracts.v1 import BUILTIN_KINDS, BUILTIN_RULES, Capability, KindSpec, SelectionRecord, SuccessorRule
from workflow.domain.completion import criteria_template
from workflow.domain.composition import ChainPlan, PlanNode, Standalone, compose
from workflow.domain.selection import Candidate
from workflow.domain.task_sources import Issue

BUG_FIX, CODE_REVIEW = BUILTIN_KINDS[:2]

FIX = Capability(code="code.fix", scope={"repository_id": "billing"})
REVIEW = Capability(code="code.review", scope={"repository_id": "billing"})

CODEX = Candidate(agent_id="agent-codex-mac", capabilities=(FIX,))
CLAUDE = Candidate(agent_id="agent-claude-mac", capabilities=(REVIEW,))
CLAUDE_MINI = Candidate(agent_id="agent-claude-mini", capabilities=(REVIEW,))
# 세션이 등록한 순서: Codex → Claude → Claude mini
PREFER = ["agent-codex-mac", "agent-claude-mac", "agent-claude-mini"]
ALL = [CODEX, CLAUDE, CLAUDE_MINI]


def _issue(key: str, title: str, labels: tuple[str, ...], blocked_by: tuple[str, ...] = ()) -> Issue:
    return Issue(source="github", key=key, title=title, body=f"{key} 본문", labels=labels, blocked_by=blocked_by, url=None)


ISSUE_41 = _issue("#41", "결제 합계 반올림 오류", ("kind:bug_fix", "repository_id:billing"))
ISSUE_42 = _issue("#42", "반올림 수정 검토", ("kind:code_review", "repository_id:billing"), blocked_by=("#41",))
ISSUE_43 = _issue("#43", "합계 모니터링 알림 추가", ("enhancement", "repository_id:billing"), blocked_by=("#42",))
ISSUE_44 = _issue("#44", "README 오타 수정", ("docs",))
FIXTURE = [ISSUE_41, ISSUE_42, ISSUE_43, ISSUE_44]


# --- fixture: #41 → #42 체인, #43·#44 Standalone -----------------------------------------


def test_fixture_composes_fix_then_review_chain():
    plan = compose(FIXTURE, ALL, PREFER, kinds=BUILTIN_KINDS, rules=BUILTIN_RULES)

    assert isinstance(plan, ChainPlan)
    assert [node.issue.key for node in plan.nodes] == ["#41", "#42"]
    assert [item.issue.key for item in plan.standalone] == ["#43", "#44"]
    assert plan.title == "결제 합계 반올림 오류 → 반올림 수정 검토"
    assert plan.human_gate == "검토 승인 (사람) · 병합은 운영자 확인"


def test_fixture_first_node_is_manual_review_bug_fix():
    plan = compose(FIXTURE, ALL, PREFER, kinds=BUILTIN_KINDS, rules=BUILTIN_RULES)
    node = plan.nodes[0]

    assert isinstance(node, PlanNode)
    assert node.issue is ISSUE_41
    assert node.capability == FIX
    assert node.kind == "bug_fix"
    assert node.predecessor_key is None
    assert node.run_mode == "manual"
    assert node.completion_mode == "review"
    assert node.criteria == tuple(criteria_template(BUG_FIX))
    assert isinstance(node.selection, SelectionRecord)
    assert node.selection.task_id == "#41"
    assert node.selection.status == "selected"
    assert node.selection.selected_agent_id == "agent-codex-mac"
    assert node.selection.candidate_count == 1
    assert node.selection.reason == "code.fix · repository_id=billing 일치 후보 1개"


def test_fixture_second_node_is_auto_review_code_review_with_prefer_default():
    plan = compose(FIXTURE, ALL, PREFER, kinds=BUILTIN_KINDS, rules=BUILTIN_RULES)
    node = plan.nodes[1]

    assert node.issue is ISSUE_42
    assert node.capability == REVIEW
    assert node.kind == "code_review"
    assert node.predecessor_key == "#41"
    assert node.run_mode == "auto"
    assert node.completion_mode == "review"
    assert node.criteria == tuple(criteria_template(CODE_REVIEW))
    assert node.selection.task_id == "#42"
    assert node.selection.status == "selected"
    assert node.selection.selected_agent_id == "agent-claude-mac"
    assert node.selection.candidate_count == 1
    assert "후보 2개" in node.selection.reason
    assert "먼저 등록한 agent-claude-mac" in node.selection.reason


def test_fixture_reasons_cover_mapping_order_chain_mode_and_assignment():
    plan = compose(FIXTURE, ALL, PREFER, kinds=BUILTIN_KINDS, rules=BUILTIN_RULES)
    first, second = plan.nodes

    assert first.reasons == (
        "라벨 kind:bug_fix + repository_id:billing → bug_fix",
        "blocked_by 없음 — 가져온 순서대로 배치",
        "체인의 첫 업무 — 선행 없음",
        "직접 실행 — 흐름의 첫 업무는 사람이 시작",
        "검토 후 완료 — 기본값",
        "code.fix · repository_id=billing 일치 후보 1개",
    )
    assert second.reasons == (
        "라벨 kind:code_review + repository_id:billing → code_review",
        "blocked_by #41 — #41 뒤에 배치",
        "선행 #41 (code.fix) → code.review 인계",
        "자동 실행 — 선행 결과가 규칙에 맞으면 별도 조작 없이 착수",
        "검토 후 완료 — 기본값",
        "code.review · repository_id=billing 일치 후보 2개 — 먼저 등록한 agent-claude-mac 를 기본 선택 (변경 가능)",
    )


def test_fixture_standalone_keep_mapping_reason():
    plan = compose(FIXTURE, ALL, PREFER, kinds=BUILTIN_KINDS, rules=BUILTIN_RULES)
    alert, readme = plan.standalone

    assert isinstance(alert, Standalone)
    assert alert.issue is ISSUE_43
    assert alert.mapping.capability is None
    assert alert.reason == "맞는 능력 코드 없음 (라벨: enhancement, repository_id:billing)"
    assert readme.issue is ISSUE_44
    assert readme.mapping.capability is None
    assert readme.reason == "맞는 능력 코드 없음 (라벨: docs)"


def test_every_node_completes_by_review():
    plan = compose(FIXTURE, ALL, PREFER, kinds=BUILTIN_KINDS, rules=BUILTIN_RULES)

    assert [node.completion_mode for node in plan.nodes] == ["review", "review"]


def test_plan_node_has_no_run_id():
    assert "run_id" not in {f.name for f in fields(PlanNode)}


def test_plan_is_immutable_value():
    plan = compose(FIXTURE, ALL, PREFER, kinds=BUILTIN_KINDS, rules=BUILTIN_RULES)

    with pytest.raises(AttributeError):
        plan.title = "x"  # type: ignore[misc]
    with pytest.raises(AttributeError):
        plan.nodes[0].run_mode = "auto"  # type: ignore[misc]


# --- 배정 ---------------------------------------------------------------------------------


def test_single_review_candidate_has_single_match_reason():
    plan = compose(FIXTURE, [CODEX, CLAUDE], PREFER, kinds=BUILTIN_KINDS, rules=BUILTIN_RULES)
    node = plan.nodes[1]

    assert node.selection.selected_agent_id == "agent-claude-mac"
    assert node.selection.reason == "code.review · repository_id=billing 일치 후보 1개"


def test_no_fix_candidate_leaves_needs_selection_with_hint():
    plan = compose(FIXTURE, [CLAUDE, CLAUDE_MINI], PREFER[1:], kinds=BUILTIN_KINDS, rules=BUILTIN_RULES)
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
    plan = compose(FIXTURE, ALL, [], kinds=BUILTIN_KINDS, rules=BUILTIN_RULES)
    node = plan.nodes[1]

    assert node.selection.status == "needs_selection"
    assert node.selection.candidate_count == 2
    assert node.selection.reason == "후보 2개 — 선택 필요"
    assert node.reasons[-1] == "후보 2개 — 선택 필요"


# --- 순서: blocked_by 위상 정렬 -------------------------------------------------------------


def test_input_order_does_not_override_blocked_by():
    plan = compose([ISSUE_42, ISSUE_41], [CODEX, CLAUDE], PREFER, kinds=BUILTIN_KINDS, rules=BUILTIN_RULES)

    assert [node.issue.key for node in plan.nodes] == ["#41", "#42"]
    assert plan.nodes[1].predecessor_key == "#41"


def test_dependency_cycle_raises():
    a = _issue("#1", "수정", ("kind:bug_fix", "repository_id:billing"), blocked_by=("#2",))
    b = _issue("#2", "검토", ("kind:code_review", "repository_id:billing"), blocked_by=("#1",))

    with pytest.raises(ValueError, match="의존 순환: #1, #2"):
        compose([a, b], [CODEX, CLAUDE], PREFER, kinds=BUILTIN_KINDS, rules=BUILTIN_RULES)


def test_self_dependency_is_a_cycle():
    a = _issue("#1", "수정", ("kind:bug_fix", "repository_id:billing"), blocked_by=("#1",))

    with pytest.raises(ValueError, match="의존 순환"):
        compose([a], [CODEX], PREFER, kinds=BUILTIN_KINDS, rules=BUILTIN_RULES)


def test_blocked_by_standalone_is_skipped_with_reason():
    review = _issue("#42", "검토", ("kind:code_review", "repository_id:billing"), blocked_by=("#44", "#41"))
    plan = compose([ISSUE_41, review, ISSUE_44], [CODEX, CLAUDE], PREFER, kinds=BUILTIN_KINDS, rules=BUILTIN_RULES)

    assert [node.issue.key for node in plan.nodes] == ["#41", "#42"]
    assert plan.nodes[1].predecessor_key == "#41"
    assert "선행 #44 은 이 제품의 에이전트가 맡지 않아 건너뜀" in plan.nodes[1].reasons
    assert "blocked_by #41 — #41 뒤에 배치" in plan.nodes[1].reasons
    assert [item.issue.key for item in plan.standalone] == ["#44"]


def test_blocked_by_only_standalone_becomes_first_without_dependency():
    fix = _issue("#41", "수정", ("kind:bug_fix", "repository_id:billing"), blocked_by=("#44",))
    plan = compose([fix, ISSUE_44], [CODEX], ["agent-codex-mac"], kinds=BUILTIN_KINDS, rules=BUILTIN_RULES)

    assert [node.issue.key for node in plan.nodes] == ["#41"]
    assert plan.nodes[0].predecessor_key is None
    assert plan.nodes[0].run_mode == "manual"
    assert "선행 #44 은 이 제품의 에이전트가 맡지 않아 건너뜀" in plan.nodes[0].reasons
    assert "blocked_by 없음 — 가져온 순서대로 배치" in plan.nodes[0].reasons


def test_blocked_by_unknown_key_is_skipped_with_reason():
    fix = _issue("#41", "수정", ("kind:bug_fix", "repository_id:billing"), blocked_by=("#99",))
    plan = compose([fix], [CODEX], ["agent-codex-mac"], kinds=BUILTIN_KINDS, rules=BUILTIN_RULES)

    assert [node.issue.key for node in plan.nodes] == ["#41"]
    assert "선행 #99 은 가져온 이슈에 없어 건너뜀" in plan.nodes[0].reasons


# --- 체인 구성: 인접 쌍은 등록된 후속 규칙으로만 잇는다 ----------------------------------------


def test_review_after_review_is_standalone_with_capability():
    second_review = _issue("#45", "두 번째 검토", ("kind:code_review", "repository_id:billing"), blocked_by=("#42",))
    plan = compose([ISSUE_41, ISSUE_42, second_review], [CODEX, CLAUDE], PREFER, kinds=BUILTIN_KINDS, rules=BUILTIN_RULES)

    assert [node.issue.key for node in plan.nodes] == ["#41", "#42"]
    (item,) = plan.standalone
    assert item.issue is second_review
    assert item.mapping.capability == REVIEW
    assert item.reason == "후속 규칙 없음: code_review → code_review"


def test_fix_after_fix_is_standalone():
    second_fix = _issue("#46", "다른 수정", ("kind:bug_fix", "repository_id:billing"))
    plan = compose([ISSUE_41, second_fix], [CODEX], PREFER, kinds=BUILTIN_KINDS, rules=BUILTIN_RULES)

    assert [node.issue.key for node in plan.nodes] == ["#41"]
    (item,) = plan.standalone
    assert item.issue is second_fix
    assert item.mapping.capability == FIX
    assert item.reason == "후속 규칙 없음: bug_fix → bug_fix"


def test_standalone_order_follows_input_order():
    second_review = _issue("#45", "두 번째 검토", ("kind:code_review", "repository_id:billing"), blocked_by=("#42",))
    plan = compose(
        [ISSUE_44, ISSUE_41, second_review, ISSUE_42], [CODEX, CLAUDE], PREFER, kinds=BUILTIN_KINDS, rules=BUILTIN_RULES
    )

    assert [node.issue.key for node in plan.nodes] == ["#41", "#42"]
    assert [item.issue.key for item in plan.standalone] == ["#44", "#45"]


# --- 규칙 한 줄 추가: 사용자 종류 + code_review → 그 종류 규칙이면 3단계 체인 --------------------

# 사용자가 등록한 세 번째 종류. 검토 결과를 받아 릴리스 노트를 쓴다 — composition.py 는 모른다
RELEASE_NOTE = KindSpec(
    kind="release_note", label="릴리스 노트", capability_code="docs.release_note", scope_key="repository_id",
    input_kinds=["code_review_result"], output_kind="generic_result",
    outcomes=["written", "needs_information"],
    instructions="검토 결과를 읽고 릴리스 노트를 쓰세요.", builtin=False,
)
REVIEW_TO_RELEASE_NOTE = SuccessorRule(
    from_kind="code_review", on_outcomes=["approved"], to_kind="release_note",
    handoff_kinds=["code_review_result"],
)
NOTE_CAP = Capability(code="docs.release_note", scope={"repository_id": "billing"})
WRITER = Candidate(agent_id="agent-writer-mac", capabilities=(NOTE_CAP,))
ISSUE_47 = _issue("#47", "릴리스 노트 작성", ("kind:release_note", "repository_id:billing"), blocked_by=("#42",))


def test_registered_rule_extends_chain_to_three_nodes():
    plan = compose(
        [ISSUE_41, ISSUE_42, ISSUE_47], [CODEX, CLAUDE, WRITER], [*PREFER, "agent-writer-mac"],
        kinds=[*BUILTIN_KINDS, RELEASE_NOTE], rules=[*BUILTIN_RULES, REVIEW_TO_RELEASE_NOTE],
    )

    assert [node.issue.key for node in plan.nodes] == ["#41", "#42", "#47"]
    assert plan.standalone == ()
    third = plan.nodes[2]
    assert third.kind == "release_note"
    assert third.capability == NOTE_CAP
    assert third.predecessor_key == "#42"
    assert third.run_mode == "auto"
    assert third.completion_mode == "review"
    assert third.criteria == tuple(criteria_template(RELEASE_NOTE))
    assert third.selection.selected_agent_id == "agent-writer-mac"
    assert third.reasons == (
        "라벨 kind:release_note + repository_id:billing → release_note",
        "blocked_by #42 — #42 뒤에 배치",
        "선행 #42 (code.review) → docs.release_note 인계",
        "자동 실행 — 선행 결과가 규칙에 맞으면 별도 조작 없이 착수",
        "검토 후 완료 — 기본값",
        "docs.release_note · repository_id=billing 일치 후보 1개",
    )
    assert plan.title == "결제 합계 반올림 오류 → 릴리스 노트 작성"
    assert plan.human_gate == "검토 승인 (사람) · 병합은 운영자 확인"


def test_registered_kind_without_rule_is_standalone():
    plan = compose(
        [ISSUE_41, ISSUE_42, ISSUE_47], [CODEX, CLAUDE, WRITER], [*PREFER, "agent-writer-mac"],
        kinds=[*BUILTIN_KINDS, RELEASE_NOTE], rules=BUILTIN_RULES,
    )

    assert [node.issue.key for node in plan.nodes] == ["#41", "#42"]
    (item,) = plan.standalone
    assert item.issue is ISSUE_47
    assert item.mapping.capability == NOTE_CAP
    assert item.reason == "후속 규칙 없음: code_review → release_note"


def test_kind_not_registered_is_standalone_without_capability():
    plan = compose(
        [ISSUE_41, ISSUE_42, ISSUE_47], [CODEX, CLAUDE, WRITER], PREFER,
        kinds=BUILTIN_KINDS, rules=[*BUILTIN_RULES, REVIEW_TO_RELEASE_NOTE],
    )

    assert [node.issue.key for node in plan.nodes] == ["#41", "#42"]
    (item,) = plan.standalone
    assert item.mapping.capability is None
    assert item.reason == "등록되지 않은 종류 kind:release_note"


def test_builtin_rules_apply_only_when_builtin_kinds_registered():
    # 종류가 하나도 등록되지 않은 워크스페이스 — kind:bug_fix 라벨도 맡지 않는다
    plan = compose(FIXTURE, ALL, PREFER, kinds=[], rules=[])

    assert plan.nodes == ()
    assert [item.issue.key for item in plan.standalone] == ["#41", "#42", "#43", "#44"]
    assert all(item.mapping.capability is None for item in plan.standalone)


# --- 노드 1개·0개 --------------------------------------------------------------------------


def test_single_review_node_title_and_human_gate():
    plan = compose([ISSUE_42], [CLAUDE], PREFER, kinds=BUILTIN_KINDS, rules=BUILTIN_RULES)
    node = plan.nodes[0]

    assert node.predecessor_key is None
    assert node.run_mode == "manual"
    assert node.completion_mode == "review"
    assert plan.title == "반올림 수정 검토"
    assert plan.human_gate == "검토 승인 (사람) · 병합은 운영자 확인"


def test_single_fix_node_is_manual_review():
    plan = compose([ISSUE_41], [CODEX], ["agent-codex-mac"], kinds=BUILTIN_KINDS, rules=BUILTIN_RULES)
    node = plan.nodes[0]

    assert node.predecessor_key is None
    assert node.run_mode == "manual"
    assert node.completion_mode == "review"
    assert plan.title == "결제 합계 반올림 오류"
    assert plan.human_gate == "검토 승인 (사람) · 병합은 운영자 확인"


def test_no_capable_issue_gives_empty_plan():
    plan = compose([ISSUE_43, ISSUE_44], ALL, PREFER, kinds=BUILTIN_KINDS, rules=BUILTIN_RULES)

    assert plan.nodes == ()
    assert [item.issue.key for item in plan.standalone] == ["#43", "#44"]
    assert plan.title == ""
    assert plan.human_gate == ""


def test_empty_input_gives_empty_plan():
    plan = compose([], [], [], kinds=BUILTIN_KINDS, rules=BUILTIN_RULES)

    assert plan == ChainPlan(nodes=(), standalone=(), human_gate="", title="")
