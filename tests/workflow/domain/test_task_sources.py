"""외부 출처 이슈 → 능력 매핑 규칙 — 라벨의 명시적 비교만 (ADR-0004). 자유 문장에서 추론하지 않는다."""

from typing import get_args

import pytest

from workflow.contracts.v1 import BUILTIN_KINDS, Capability, KindSpec
from workflow.domain.task_sources import Issue, IssueMapping, Source, map_issue

DIAGNOSE = Capability(code="operations.diagnose", scope={"workflow_id": "daily-report"})
MODIFY = Capability(code="code.modify", scope={"repository_id": "demo-report-repo"})


def _issue(labels: tuple[str, ...], *, title: str = "제목", body: str = "본문") -> Issue:
    return Issue(
        source="github",
        key="#1",
        title=title,
        body=body,
        labels=labels,
        blocked_by=(),
        url=None,
    )


# --- incident + workflow:<id> + run:<run_id> → operations.diagnose --------------------


def test_incident_with_workflow_and_run_maps_to_diagnose():
    mapping = map_issue(_issue(("incident", "workflow:daily-report", "run:daily-0920-0900")), BUILTIN_KINDS)

    assert isinstance(mapping, IssueMapping)
    assert mapping.capability == DIAGNOSE
    assert mapping.run_id == "daily-0920-0900"
    assert mapping.reason == "라벨 incident·workflow:daily-report → operations.diagnose"


def test_incident_without_run_label_is_unassignable():
    mapping = map_issue(_issue(("incident", "workflow:daily-report")), BUILTIN_KINDS)

    assert mapping.capability is None
    assert mapping.run_id is None
    assert mapping.reason == "run 라벨 없음"


def test_incident_without_workflow_label_has_no_capability():
    mapping = map_issue(_issue(("incident", "run:daily-0920-0900")), BUILTIN_KINDS)

    assert mapping.capability is None
    assert mapping.run_id is None
    assert mapping.reason == "맞는 능력 코드 없음 (라벨: incident, run:daily-0920-0900)"


def test_label_order_does_not_matter():
    mapping = map_issue(_issue(("run:daily-0920-0900", "workflow:daily-report", "incident")), BUILTIN_KINDS)

    assert mapping.capability == DIAGNOSE
    assert mapping.run_id == "daily-0920-0900"


# --- bug + repo:<repository_id> → code.modify ------------------------------------------


def test_bug_with_repo_maps_to_modify():
    mapping = map_issue(_issue(("bug", "repo:demo-report-repo")), BUILTIN_KINDS)

    assert mapping.capability == MODIFY
    assert mapping.run_id is None
    assert mapping.reason == "라벨 bug·repo:demo-report-repo → code.modify"


def test_bug_without_repo_label_has_no_capability():
    mapping = map_issue(_issue(("bug",)), BUILTIN_KINDS)

    assert mapping.capability is None
    assert mapping.reason == "맞는 능력 코드 없음 (라벨: bug)"


# --- 그 외 → None ----------------------------------------------------------------------


def test_docs_label_has_no_capability():
    mapping = map_issue(_issue(("docs",)), BUILTIN_KINDS)

    assert mapping.capability is None
    assert mapping.run_id is None
    assert mapping.reason == "맞는 능력 코드 없음 (라벨: docs)"


def test_enhancement_with_repo_has_no_capability():
    # repo 라벨이 있어도 bug 가 아니면 code.modify 가 아니다.
    mapping = map_issue(_issue(("enhancement", "repo:demo-report-repo")), BUILTIN_KINDS)

    assert mapping.capability is None
    assert mapping.reason == "맞는 능력 코드 없음 (라벨: enhancement, repo:demo-report-repo)"


def test_no_labels_has_no_capability():
    mapping = map_issue(_issue(()), BUILTIN_KINDS)

    assert mapping.capability is None
    assert mapping.reason == "맞는 능력 코드 없음 (라벨: 없음)"


def test_title_and_body_are_not_used_for_mapping():
    # ADR-0004: 자유 문장에서 능력을 추론하지 않는다.
    mapping = map_issue(
        _issue((), title="일일 보고서 생성 실패", body="incident workflow:daily-report run:daily-0920-0900"),
        BUILTIN_KINDS,
    )

    assert mapping.capability is None


def test_labels_are_compared_case_sensitively():
    assert map_issue(_issue(("Incident", "workflow:daily-report", "run:x")), BUILTIN_KINDS).capability is None
    assert map_issue(_issue(("BUG", "repo:demo-report-repo")), BUILTIN_KINDS).capability is None


def test_empty_label_value_is_treated_as_missing():
    assert map_issue(_issue(("incident", "workflow:", "run:x")), BUILTIN_KINDS).capability is None
    assert map_issue(_issue(("incident", "workflow:daily-report", "run:")), BUILTIN_KINDS).reason == "run 라벨 없음"
    assert map_issue(_issue(("bug", "repo:")), BUILTIN_KINDS).capability is None


# --- kind:<kind> + <scope_key>:<value> → 등록된 종류의 capability_code (일반 규칙) --------------

REVIEW = KindSpec(
    kind="review", label="검토", capability_code="review", scope_key="repository_id",
    input_kinds=["diff", "code_change_result"], output_kind="generic_result",
    outcomes=["approved", "changes_requested", "needs_information"], instructions="", builtin=False,
)
KINDS = [*BUILTIN_KINDS, REVIEW]


def test_kind_label_maps_to_registered_kind():
    mapping = map_issue(_issue(("kind:review", "repository_id:demo-report-repo")), KINDS)

    assert mapping.capability == Capability(code="review", scope={"repository_id": "demo-report-repo"})
    assert mapping.run_id is None
    assert mapping.reason == "라벨 kind:review + repository_id:demo-report-repo → review"


def test_kind_label_of_unregistered_kind_is_unassignable():
    mapping = map_issue(_issue(("kind:review", "repository_id:demo-report-repo")), BUILTIN_KINDS)

    assert mapping.capability is None
    assert mapping.reason == "등록되지 않은 종류 kind:review"


def test_kind_label_without_scope_label_is_unassignable():
    mapping = map_issue(_issue(("kind:review",)), KINDS)

    assert mapping.capability is None
    assert mapping.reason == "repository_id 라벨 없음"


def test_kind_label_takes_priority_over_builtin_label_rules():
    # kind: 라벨이 있으면 일반 규칙만 본다 — bug·repo: 가 같이 있어도 code.modify 로 가지 않는다
    mapping = map_issue(_issue(("bug", "repo:demo-report-repo", "kind:review", "repository_id:other")), KINDS)

    assert mapping.capability == Capability(code="review", scope={"repository_id": "other"})


def test_kind_label_uses_spec_capability_code_and_scope_key():
    spec = KindSpec(
        kind="lint", label="린트", capability_code="code.lint", scope_key="repository_id",
        input_kinds=[], output_kind="generic_result", outcomes=["clean", "dirty"], instructions="", builtin=False,
    )
    mapping = map_issue(_issue(("kind:lint", "repository_id:demo-report-repo")), [spec])

    assert mapping.capability == Capability(code="code.lint", scope={"repository_id": "demo-report-repo"})
    assert mapping.reason == "라벨 kind:lint + repository_id:demo-report-repo → lint"


def test_builtin_label_rules_need_builtin_kinds_registered():
    # 내장 종류가 등록부에 없으면 incident·bug 규칙도 적용하지 않는다
    assert map_issue(_issue(("incident", "workflow:daily-report", "run:x")), [REVIEW]).capability is None
    assert map_issue(_issue(("bug", "repo:demo-report-repo")), [REVIEW]).capability is None
    assert map_issue(_issue(("bug", "repo:demo-report-repo")), []).reason == (
        "맞는 능력 코드 없음 (라벨: bug, repo:demo-report-repo)"
    )


# --- 계약 ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "labels, code, scope_key",
    [
        (("incident", "workflow:weekly-report", "run:weekly-0921"), "operations.diagnose", "workflow_id"),
        (("bug", "repo:other-repo"), "code.modify", "repository_id"),
    ],
)
def test_mapped_capability_passes_contract_validation(labels, code, scope_key):
    capability = map_issue(_issue(labels), BUILTIN_KINDS).capability

    assert capability is not None
    assert capability.code == code
    assert set(capability.scope) == {scope_key}
    # 계약 v1 의 scope 키 검사를 통과하는 값이어야 한다 (재검증해도 같다).
    assert Capability.model_validate(capability.model_dump()) == capability


def test_issue_and_mapping_are_immutable():
    issue = _issue(("docs",))
    mapping = map_issue(issue, BUILTIN_KINDS)

    with pytest.raises(AttributeError):
        issue.key = "#2"  # type: ignore[misc]
    with pytest.raises(AttributeError):
        mapping.reason = "x"  # type: ignore[misc]


# --- n8n 은 TaskSource 하나 — 라벨 규칙·매핑이 github 과 같다 (ADR-0010) ------------------


def test_source_literal_includes_n8n():
    assert get_args(Source) == ("github", "jira", "n8n")


@pytest.mark.parametrize(
    "labels",
    [
        ("incident", "workflow:daily-report", "run:daily-0920-0900"),
        ("bug", "repo:demo-report-repo"),
        ("kind:review", "repository_id:demo-report-repo"),
        ("docs",),
    ],
)
def test_n8n_issue_maps_exactly_like_github(labels):
    n8n = Issue(
        source="n8n", key="run-daily-0920", title="제목", body="본문", labels=labels, blocked_by=(), url=None
    )

    assert map_issue(n8n, KINDS) == map_issue(_issue(labels), KINDS)


def test_n8n_incident_maps_to_diagnose_with_run_id():
    n8n = Issue(
        source="n8n", key="run-daily-0920", title="제목", body="본문",
        labels=("incident", "workflow:daily-report", "run:daily-0920-0900"), blocked_by=(), url=None,
    )
    mapping = map_issue(n8n, BUILTIN_KINDS)

    assert mapping.capability == DIAGNOSE
    assert mapping.run_id == "daily-0920-0900"
    assert mapping.reason == "라벨 incident·workflow:daily-report → operations.diagnose"
