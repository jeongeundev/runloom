"""GitHub 업무 순환 계약 — 소스 설정·담당 연결·이슈 스냅샷·원본 반영 (CONTRACT 13.6~13.8, ADR-0014), 기준선 (ADR-0015).

중앙 서버 안에서만 쓰는 모델이다(연결 프로그램은 모른다). 규칙은 v1 과 같다 — 알 수 없는 필드 거부, 자동 변환 없음.
토큰 필드는 없다: 값은 서버 환경변수 `WORKFLOW_GITHUB_TOKEN` 또는 비밀 저장소에만 있다(ADR-0017). 이슈 제목·본문·URL 은 표시·요청 재료일 뿐
명령·경로로 해석하지 않는다.
"""

import hashlib
import json
from typing import Annotated, Literal

from pydantic import AfterValidator, Field, model_validator

from workflow.contracts.v1 import NonEmptyStr, Rfc3339, Sha256, _Contract


def _validate_full_name(value: str) -> str:
    if value.split("/")[-1] in (".", ".."):
        raise ValueError("저장소 이름으로 . 이나 .. 은 쓸 수 없습니다")
    return value


# `owner/name` — GitHub 사용자·조직 이름(영숫자·하이픈)과 저장소 이름(영숫자·`.`·`_`·`-`). URL·경로 모양은 거부한다.
RepositoryFullName = Annotated[
    str,
    Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9._-]{1,100}$"),
    AfterValidator(_validate_full_name),
]
SourceId = Annotated[str, Field(pattern=r"^ghs-[0-9a-f]{8}$")]
PositiveInt = Annotated[int, Field(ge=1)]


def _unique(values: list, name: str) -> None:
    if len(set(values)) != len(values):
        raise ValueError(f"{name} 에 중복이 있습니다")


class GitHubSourceConfig(_Contract):
    """운영자가 연결한 저장소 하나. `repository_full_name ∈ WORKFLOW_GITHUB_REPOS` 는 서버가 검사한다.

    `intake: filtered`(기본) 는 phase 8 그대로 — 범위(라벨·고른 이슈)와 세 ID 가 필수다. `all_open` 은 열린 이슈 전부를
    가져오고 실행은 지시한 것만 한다(ADR-0017) — 범위는 비어도 되고, None 인 ID 는 자동 매칭이 정한다.
    새 칸은 모두 기본값이 있어 phase 8 에 저장된 config_json 이 그대로 유효하다.
    """

    source_id: SourceId
    repository_full_name: RepositoryFullName
    workflow_repository_id: NonEmptyStr | None = None  # 이 제품의 scope 값(`repository_id`)
    label_filter: list[NonEmptyStr]
    selected_issue_numbers: list[PositiveInt]
    start_at: Rfc3339  # all_open 에서는 연결 시각 기록일 뿐 범위에 쓰지 않는다
    fix_verification_profile_id: NonEmptyStr | None = None  # 로컬 등록의 검증 프로필 ID — 명령이 아니다
    review_agent_id: NonEmptyStr | None = None
    run_mode: Literal["auto", "manual"]
    max_rework_rounds: Annotated[int, Field(ge=0, le=3)] = 1
    intake: Literal["filtered", "all_open"] = "filtered"
    trigger_label: NonEmptyStr | None = None  # all_open 에서 이 라벨이 붙은 이슈는 지시된 것으로 본다
    default_fix_agent_id: NonEmptyStr | None = None  # 담당자 연결이 없을 때 쓸 수정 Agent
    installation_id: PositiveInt | None = None  # App 설치가 만든 소스 — 클라이언트가 설치 토큰을 쓴다
    enabled: bool
    config_revision: PositiveInt

    @model_validator(mode="after")
    def _check_scope(self) -> "GitHubSourceConfig":
        _unique(self.label_filter, "label_filter")
        _unique(self.selected_issue_numbers, "selected_issue_numbers")
        if self.intake == "filtered":
            if not self.label_filter and not self.selected_issue_numbers:
                raise ValueError("label_filter 와 selected_issue_numbers 가 둘 다 비었습니다 — 전체 백로그는 받지 않습니다")
            missing = [name for name in FILTERED_REQUIRED if getattr(self, name) is None]
            if missing:
                raise ValueError(f"intake filtered 는 {', '.join(missing)} 가 필요합니다")
        return self


FILTERED_REQUIRED = ("workflow_repository_id", "fix_verification_profile_id", "review_agent_id")


class AssigneeBinding(_Contract):
    """GitHub 사용자 숫자 ID → 수정 Agent. `github_login` 은 바뀔 수 있어 표시용이다."""

    source_id: SourceId
    github_user_id: PositiveInt
    github_login: NonEmptyStr
    agent_id: NonEmptyStr


class GitHubIssueSnapshot(_Contract):
    """GitHub REST 응답에서 필요한 값만. `is_pull_request: true` 는 업무로 받지 않는다(수집기 몫)."""

    repository_id: PositiveInt
    repository_full_name: RepositoryFullName
    issue_id: PositiveInt
    number: PositiveInt
    title: NonEmptyStr
    body: str
    state: Literal["open", "closed"]
    labels: list[str]
    assignee_ids: list[PositiveInt]
    assignee_logins: list[str]
    html_url: NonEmptyStr
    created_at: Rfc3339
    updated_at: Rfc3339
    is_pull_request: bool

    @model_validator(mode="after")
    def _check_assignees(self) -> "GitHubIssueSnapshot":
        if len(self.assignee_ids) != len(self.assignee_logins):
            raise ValueError("assignee_ids 와 assignee_logins 의 개수가 다릅니다")
        return self


def snapshot_digest(snapshot: GitHubIssueSnapshot) -> str:
    """같은 내용 판정용 sha256 — 키 정렬된 JSON."""
    data = json.dumps(snapshot.model_dump(mode="json"), sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(data.encode()).hexdigest()


class SourceDelivery(_Contract):
    """원본 이슈 댓글 반영 outbox 한 건. `unknown` 은 재POST 전에 marker 로 조정한다."""

    delivery_id: NonEmptyStr
    source_id: SourceId
    task_id: NonEmptyStr
    issue_number: PositiveInt
    body_revision: PositiveInt
    body_digest: Sha256
    state: Literal["pending", "sending", "delivered", "unknown", "failed"]
    comment_id: PositiveInt | None
    attempts: Annotated[int, Field(ge=0)]
    next_at: Rfc3339 | None
    last_error: str | None

    @model_validator(mode="after")
    def _check_delivered(self) -> "SourceDelivery":
        if self.state == "delivered" and self.comment_id is None:
            raise ValueError("delivered 는 comment_id 가 있어야 합니다")
        return self


class IssuePrLink(_Contract):
    """기준선 한 건 — 닫힌 이슈와 그 이슈를 닫은 **병합된** PR (ADR-0015). 병합 안 된 PR 은 만들지 않는다."""

    issue_number: PositiveInt
    issue_title: NonEmptyStr
    issue_opened_at: Rfc3339
    pr_number: PositiveInt
    pr_merged_at: Rfc3339


class PullRequestRef(_Contract):
    """GitHub PR 하나 (ADR-0018 결정 4). 병합 여부는 `merged_at` 으로만 본다 — 병합돼도 `state` 는 `closed` 다."""

    number: PositiveInt
    html_url: NonEmptyStr
    state: Literal["open", "closed"]
    draft: bool
    merged_at: Rfc3339 | None
