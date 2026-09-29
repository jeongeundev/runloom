"""외부 출처(GitHub·Jira·n8n) 이슈 → 이 제품의 능력 매핑 — 라벨의 명시적 비교만 (ADR-0004).

`Issue` 는 Task 가 되기 전의 외부 항목이다. `body` 는 그대로 `Task.request` 문자열이 될 뿐이며
여기서 명령·경로로 해석하지 않는다. 제목·본문에서 능력을 추론하지 않고 라벨만 본다.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from workflow.contracts.v1 import Capability, KindSpec
from workflow.domain.kinds import get_kind

Source = Literal["github", "jira", "n8n"]


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
    reason: str  # 사람이 읽는 매핑 이유


def _label_value(labels: Sequence[str], prefix: str) -> str | None:
    # `prefix:<값>` 라벨의 값. 대소문자 정규화 없음. 값이 비어 있으면 없는 것으로 본다.
    for label in labels:
        if label.startswith(prefix) and len(label) > len(prefix):
            return label[len(prefix) :]
    return None


def _no_capability(labels: Sequence[str]) -> IssueMapping:
    shown = ", ".join(labels) if labels else "없음"
    return IssueMapping(capability=None, reason=f"맞는 능력 코드 없음 (라벨: {shown})")


def map_issue(issue: Issue, kinds: Sequence[KindSpec]) -> IssueMapping:
    """라벨 규칙으로 능력을 정한다. `kinds` 는 워크스페이스의 종류 등록부다.

    - `kind:<kind>` 가 있으면: 그 종류가 `kinds` 에 있고 `<scope_key>:<value>` 라벨이 있으면
      `Capability(code=spec.capability_code, scope={scope_key: value})`. 종류가 없거나 scope 라벨이 없으면 맡을 수 없다.
    - 그 외 → None.
    """
    labels = issue.labels
    kind = _label_value(labels, "kind:")
    if kind is not None:
        spec = get_kind(kinds, kind)
        if spec is None:
            return IssueMapping(capability=None, reason=f"등록되지 않은 종류 kind:{kind}")
        value = _label_value(labels, f"{spec.scope_key}:")
        if value is None:
            return IssueMapping(capability=None, reason=f"{spec.scope_key} 라벨 없음")
        return IssueMapping(
            capability=Capability(code=spec.capability_code, scope={spec.scope_key: value}),
            reason=f"라벨 kind:{kind} + {spec.scope_key}:{value} → {kind}",
        )

    return _no_capability(labels)
