"""외부 출처(GitHub·Jira) 이슈 → 이 제품의 능력 매핑 — 라벨의 명시적 비교만 (ADR-0004).

`Issue` 는 Task 가 되기 전의 외부 항목이다. `body` 는 그대로 `Task.request` 문자열이 될 뿐이며
여기서 명령·경로로 해석하지 않는다. 제목·본문에서 능력을 추론하지 않고 라벨만 본다.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from workflow.contracts.v1 import Capability

Source = Literal["github", "jira"]


@dataclass(frozen=True)
class Issue:
    source: Source
    key: str  # "#41" / "OPS-41" — 화면·Task.source_ref 에 그대로
    title: str
    body: str  # Task.request 가 된다
    labels: tuple[str, ...]
    blocked_by: tuple[str, ...]  # 같은 출처의 key 들
    url: str | None  # fixture 는 None (가짜 외부 링크를 만들지 않는다)


@dataclass(frozen=True)
class IssueMapping:
    capability: Capability | None  # None 이면 이 제품의 에이전트가 맡을 수 없는 이슈
    run_id: str | None  # 진단이면 필수
    reason: str  # 사람이 읽는 매핑 이유


def _label_value(labels: Sequence[str], prefix: str) -> str | None:
    # `prefix:<값>` 라벨의 값. 대소문자 정규화 없음. 값이 비어 있으면 없는 것으로 본다.
    for label in labels:
        if label.startswith(prefix) and len(label) > len(prefix):
            return label[len(prefix) :]
    return None


def _no_capability(labels: Sequence[str]) -> IssueMapping:
    shown = ", ".join(labels) if labels else "없음"
    return IssueMapping(capability=None, run_id=None, reason=f"맞는 능력 코드 없음 (라벨: {shown})")


def map_issue(issue: Issue) -> IssueMapping:
    """라벨 규칙으로 능력·run_id 를 정한다.

    - `incident` + `workflow:<id>` → `operations.diagnose {workflow_id}`, run_id 는 `run:<run_id>`.
      `run:` 라벨이 없으면 맡을 수 없다 (진단은 조사할 run 이 필요하다).
    - `bug` + `repo:<repository_id>` → `code.modify {repository_id}`.
    - 그 외 → None.
    """
    labels = issue.labels
    workflow_id = _label_value(labels, "workflow:")
    if "incident" in labels and workflow_id is not None:
        run_id = _label_value(labels, "run:")
        if run_id is None:
            return IssueMapping(capability=None, run_id=None, reason="run 라벨 없음")
        return IssueMapping(
            capability=Capability(code="operations.diagnose", scope={"workflow_id": workflow_id}),
            run_id=run_id,
            reason=f"라벨 incident·workflow:{workflow_id} → operations.diagnose",
        )

    repository_id = _label_value(labels, "repo:")
    if "bug" in labels and repository_id is not None:
        return IssueMapping(
            capability=Capability(code="code.modify", scope={"repository_id": repository_id}),
            run_id=None,
            reason=f"라벨 bug·repo:{repository_id} → code.modify",
        )

    return _no_capability(labels)
