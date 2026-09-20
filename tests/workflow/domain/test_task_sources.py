"""외부 출처 이슈 → 능력 매핑 규칙 — 라벨의 명시적 비교만 (ADR-0004). 자유 문장에서 추론하지 않는다."""

import pytest

from workflow.contracts.v1 import Capability
from workflow.domain.task_sources import Issue, IssueMapping, map_issue

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
    mapping = map_issue(_issue(("incident", "workflow:daily-report", "run:daily-0920-0900")))

    assert isinstance(mapping, IssueMapping)
    assert mapping.capability == DIAGNOSE
    assert mapping.run_id == "daily-0920-0900"
    assert mapping.reason == "라벨 incident·workflow:daily-report → operations.diagnose"


def test_incident_without_run_label_is_unassignable():
    mapping = map_issue(_issue(("incident", "workflow:daily-report")))

    assert mapping.capability is None
    assert mapping.run_id is None
    assert mapping.reason == "run 라벨 없음"


def test_incident_without_workflow_label_has_no_capability():
    mapping = map_issue(_issue(("incident", "run:daily-0920-0900")))

    assert mapping.capability is None
    assert mapping.run_id is None
    assert mapping.reason == "맞는 능력 코드 없음 (라벨: incident, run:daily-0920-0900)"


def test_label_order_does_not_matter():
    mapping = map_issue(_issue(("run:daily-0920-0900", "workflow:daily-report", "incident")))

    assert mapping.capability == DIAGNOSE
    assert mapping.run_id == "daily-0920-0900"


# --- bug + repo:<repository_id> → code.modify ------------------------------------------


def test_bug_with_repo_maps_to_modify():
    mapping = map_issue(_issue(("bug", "repo:demo-report-repo")))

    assert mapping.capability == MODIFY
    assert mapping.run_id is None
    assert mapping.reason == "라벨 bug·repo:demo-report-repo → code.modify"


def test_bug_without_repo_label_has_no_capability():
    mapping = map_issue(_issue(("bug",)))

    assert mapping.capability is None
    assert mapping.reason == "맞는 능력 코드 없음 (라벨: bug)"


# --- 그 외 → None ----------------------------------------------------------------------


def test_docs_label_has_no_capability():
    mapping = map_issue(_issue(("docs",)))

    assert mapping.capability is None
    assert mapping.run_id is None
    assert mapping.reason == "맞는 능력 코드 없음 (라벨: docs)"


def test_enhancement_with_repo_has_no_capability():
    # repo 라벨이 있어도 bug 가 아니면 code.modify 가 아니다.
    mapping = map_issue(_issue(("enhancement", "repo:demo-report-repo")))

    assert mapping.capability is None
    assert mapping.reason == "맞는 능력 코드 없음 (라벨: enhancement, repo:demo-report-repo)"


def test_no_labels_has_no_capability():
    mapping = map_issue(_issue(()))

    assert mapping.capability is None
    assert mapping.reason == "맞는 능력 코드 없음 (라벨: 없음)"


def test_title_and_body_are_not_used_for_mapping():
    # ADR-0004: 자유 문장에서 능력을 추론하지 않는다.
    mapping = map_issue(
        _issue((), title="일일 보고서 생성 실패", body="incident workflow:daily-report run:daily-0920-0900")
    )

    assert mapping.capability is None


def test_labels_are_compared_case_sensitively():
    assert map_issue(_issue(("Incident", "workflow:daily-report", "run:x"))).capability is None
    assert map_issue(_issue(("BUG", "repo:demo-report-repo"))).capability is None


def test_empty_label_value_is_treated_as_missing():
    assert map_issue(_issue(("incident", "workflow:", "run:x"))).capability is None
    assert map_issue(_issue(("incident", "workflow:daily-report", "run:"))).reason == "run 라벨 없음"
    assert map_issue(_issue(("bug", "repo:"))).capability is None


# --- 계약 ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "labels, code, scope_key",
    [
        (("incident", "workflow:weekly-report", "run:weekly-0921"), "operations.diagnose", "workflow_id"),
        (("bug", "repo:other-repo"), "code.modify", "repository_id"),
    ],
)
def test_mapped_capability_passes_contract_validation(labels, code, scope_key):
    capability = map_issue(_issue(labels)).capability

    assert capability is not None
    assert capability.code == code
    assert set(capability.scope) == {scope_key}
    # 계약 v1 의 scope 키 검사를 통과하는 값이어야 한다 (재검증해도 같다).
    assert Capability.model_validate(capability.model_dump()) == capability


def test_issue_and_mapping_are_immutable():
    issue = _issue(("docs",))
    mapping = map_issue(issue)

    with pytest.raises(AttributeError):
        issue.key = "#2"  # type: ignore[misc]
    with pytest.raises(AttributeError):
        mapping.reason = "x"  # type: ignore[misc]
