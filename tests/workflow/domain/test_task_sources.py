"""외부 출처 이슈 → 능력 매핑 규칙 — 라벨의 명시적 비교만 (ADR-0004). 자유 문장에서 추론하지 않는다."""

from dataclasses import fields
from typing import get_args

import pytest

from workflow.contracts.v1 import BUILTIN_KINDS, Capability, KindSpec
from workflow.domain.task_sources import Issue, IssueMapping, Source, map_issue

FIX = Capability(code="code.fix", scope={"repository_id": "billing"})


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


# --- kind:bug_fix / kind:code_review + repository_id:<id> → 내장 종류의 능력 -----------------


def test_kind_bug_fix_label_maps_to_code_fix():
    mapping = map_issue(_issue(("kind:bug_fix", "repository_id:billing")), BUILTIN_KINDS)

    assert isinstance(mapping, IssueMapping)
    assert mapping.capability == FIX
    assert mapping.reason == "라벨 kind:bug_fix + repository_id:billing → bug_fix"


def test_kind_code_review_label_maps_to_code_review():
    mapping = map_issue(_issue(("kind:code_review", "repository_id:billing")), BUILTIN_KINDS)

    assert mapping.capability == Capability(code="code.review", scope={"repository_id": "billing"})
    assert mapping.reason == "라벨 kind:code_review + repository_id:billing → code_review"


def test_label_order_does_not_matter():
    mapping = map_issue(_issue(("repository_id:billing", "kind:bug_fix")), BUILTIN_KINDS)

    assert mapping.capability == FIX


# --- 데모 전용 라벨 규칙(incident·bug)은 없다 — 능력 없음 ---------------------------------------


@pytest.mark.parametrize(
    "labels",
    [
        ("incident", "workflow:daily-report", "run:daily-0920-0900"),
        ("bug", "repo:demo-report-repo"),
        ("bug", "repository_id:billing"),
    ],
)
def test_incident_and_bug_labels_have_no_capability(labels):
    mapping = map_issue(_issue(labels), BUILTIN_KINDS)

    assert mapping.capability is None
    assert mapping.reason == f"맞는 능력 코드 없음 (라벨: {', '.join(labels)})"


def test_incident_and_bug_labels_stay_unmapped_even_with_same_named_kinds():
    # 예전 내장 라벨 규칙은 종류 이름(diagnosis·code_change)만 보고 켜졌다 — 이제 그런 규칙 자체가 없다
    kinds = [
        KindSpec(
            kind=name, label=name, capability_code=code, scope_key=scope_key,
            input_kinds=[], output_kind="generic_result", outcomes=["done"], instructions="", builtin=False,
        )
        for name, code, scope_key in (
            ("diagnosis", "operations.diagnose", "workflow_id"),
            ("code_change", "code.modify", "repository_id"),
        )
    ]

    assert map_issue(_issue(("incident", "workflow:daily-report", "run:x")), kinds).capability is None
    assert map_issue(_issue(("bug", "repo:demo-report-repo")), kinds).capability is None


def test_mapping_has_no_run_id():
    assert [f.name for f in fields(IssueMapping)] == ["capability", "reason"]


# --- 그 외 → None ----------------------------------------------------------------------


def test_docs_label_has_no_capability():
    mapping = map_issue(_issue(("docs",)), BUILTIN_KINDS)

    assert mapping.capability is None
    assert mapping.reason == "맞는 능력 코드 없음 (라벨: docs)"


def test_scope_label_without_kind_label_has_no_capability():
    # repository_id 라벨이 있어도 kind: 라벨이 없으면 능력을 정하지 않는다.
    mapping = map_issue(_issue(("enhancement", "repository_id:billing")), BUILTIN_KINDS)

    assert mapping.capability is None
    assert mapping.reason == "맞는 능력 코드 없음 (라벨: enhancement, repository_id:billing)"


def test_no_labels_has_no_capability():
    mapping = map_issue(_issue(()), BUILTIN_KINDS)

    assert mapping.capability is None
    assert mapping.reason == "맞는 능력 코드 없음 (라벨: 없음)"


def test_title_and_body_are_not_used_for_mapping():
    # ADR-0004: 자유 문장에서 능력을 추론하지 않는다.
    mapping = map_issue(
        _issue((), title="결제 합계 반올림 오류", body="kind:bug_fix repository_id:billing"),
        BUILTIN_KINDS,
    )

    assert mapping.capability is None


def test_labels_are_compared_case_sensitively():
    assert map_issue(_issue(("KIND:bug_fix", "repository_id:billing")), BUILTIN_KINDS).capability is None
    assert map_issue(_issue(("kind:bug_fix", "Repository_id:billing")), BUILTIN_KINDS).capability is None


def test_empty_label_value_is_treated_as_missing():
    assert map_issue(_issue(("kind:", "repository_id:billing")), BUILTIN_KINDS).reason == (
        "맞는 능력 코드 없음 (라벨: kind:, repository_id:billing)"
    )
    assert map_issue(_issue(("kind:bug_fix", "repository_id:")), BUILTIN_KINDS).reason == "repository_id 라벨 없음"


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
    assert mapping.reason == "라벨 kind:review + repository_id:demo-report-repo → review"


def test_kind_label_of_unregistered_kind_is_unassignable():
    mapping = map_issue(_issue(("kind:review", "repository_id:demo-report-repo")), BUILTIN_KINDS)

    assert mapping.capability is None
    assert mapping.reason == "등록되지 않은 종류 kind:review"


def test_kind_label_without_scope_label_is_unassignable():
    mapping = map_issue(_issue(("kind:review",)), KINDS)

    assert mapping.capability is None
    assert mapping.reason == "repository_id 라벨 없음"


def test_kind_label_uses_spec_capability_code_and_scope_key():
    spec = KindSpec(
        kind="lint", label="린트", capability_code="code.lint", scope_key="repository_id",
        input_kinds=[], output_kind="generic_result", outcomes=["clean", "dirty"], instructions="", builtin=False,
    )
    mapping = map_issue(_issue(("kind:lint", "repository_id:demo-report-repo")), [spec])

    assert mapping.capability == Capability(code="code.lint", scope={"repository_id": "demo-report-repo"})
    assert mapping.reason == "라벨 kind:lint + repository_id:demo-report-repo → lint"


def test_builtin_kind_label_needs_builtin_kinds_registered():
    # 내장 종류도 등록부에 없으면 kind:bug_fix 를 맡을 수 없다
    assert map_issue(_issue(("kind:bug_fix", "repository_id:billing")), [REVIEW]).reason == (
        "등록되지 않은 종류 kind:bug_fix"
    )
    assert map_issue(_issue(("kind:bug_fix", "repository_id:billing")), []).capability is None


# --- 계약 ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "labels, code, scope_key",
    [
        (("kind:bug_fix", "repository_id:other-repo"), "code.fix", "repository_id"),
        (("kind:code_review", "repository_id:other-repo"), "code.review", "repository_id"),
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
        ("kind:bug_fix", "repository_id:billing"),
        ("incident", "workflow:daily-report", "run:daily-0920-0900"),
        ("kind:review", "repository_id:demo-report-repo"),
        ("docs",),
    ],
)
def test_n8n_issue_maps_exactly_like_github(labels):
    n8n = Issue(
        source="n8n", key="run-daily-0920", title="제목", body="본문", labels=labels, blocked_by=(), url=None
    )

    assert map_issue(n8n, KINDS) == map_issue(_issue(labels), KINDS)
