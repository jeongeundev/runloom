"""GitHub 업무 순환 계약 모델 — CONTRACT 13.6~13.8, ADR-0014.

fixture 는 `docs/CONTRACT.md` 13절 블록이다. 모델 대응·전체 검증은 `test_v1.py` 가 하고,
여기서는 왕복·거부 규칙·`snapshot_digest` 를 본다.
"""

import json
import re
from pathlib import Path

import pytest
from pydantic import BaseModel, ValidationError

from workflow.contracts import github
from workflow.contracts.github import (
    AssigneeBinding,
    GitHubIssueSnapshot,
    GitHubSourceConfig,
    IssuePrLink,
    PullRequestRef,
    SourceDelivery,
    snapshot_digest,
)

CONTRACT_MD = Path(__file__).resolve().parents[3] / "docs" / "CONTRACT.md"
_FENCE = re.compile(r"```json\n(.*?)\n```", re.DOTALL)


def _block(key: str) -> dict:
    """`key` 를 가진 첫 json 블록의 복사본."""
    text = CONTRACT_MD.read_text(encoding="utf-8")
    return next(b for b in (json.loads(m) for m in _FENCE.findall(text)) if key in b)


def _config(**overrides) -> dict:
    return {**_block("label_filter"), **overrides}


def _snapshot(**overrides) -> dict:
    return {**_block("is_pull_request"), **overrides}


def _delivery(**overrides) -> dict:
    return {**_block("delivery_id"), **overrides}


def _binding(**overrides) -> dict:
    return {**_block("github_user_id"), **overrides}


def test_all_models_forbid_extra_and_are_strict():
    models = [
        obj for obj in vars(github).values()
        if isinstance(obj, type) and issubclass(obj, BaseModel) and obj is not BaseModel
    ]
    assert {m.__name__ for m in models} >= {"GitHubSourceConfig", "AssigneeBinding", "GitHubIssueSnapshot", "SourceDelivery"}
    for model in models:
        assert model.model_config.get("extra") == "forbid", model.__name__
        assert model.model_config.get("strict") is True, model.__name__


@pytest.mark.parametrize(
    ("model", "key"),
    [
        (GitHubSourceConfig, "label_filter"),
        (AssigneeBinding, "github_user_id"),
        (GitHubIssueSnapshot, "is_pull_request"),
        (SourceDelivery, "delivery_id"),
    ],
)
def test_roundtrip_in_contract_md_field_order(model, key):
    block = _block(key)
    parsed = model.model_validate(block)
    dumped = parsed.model_dump(mode="json")
    assert dumped == block
    assert list(dumped) == list(block)
    assert model.model_validate(dumped) == parsed


# --- GitHubSourceConfig ---------------------------------------------------------


@pytest.mark.parametrize("field", ["token", "github_token", "WORKFLOW_GITHUB_TOKEN", "authorization"])
def test_config_has_no_token_field(field):
    with pytest.raises(ValidationError):
        GitHubSourceConfig.model_validate(_config(**{field: "ghp_x"}))


def test_config_requires_label_filter_or_selected_issues():
    """둘 다 비면 전체 백로그 자동 착수가 된다 — 거부."""
    with pytest.raises(ValidationError):
        GitHubSourceConfig.model_validate(_config(label_filter=[], selected_issue_numbers=[]))
    GitHubSourceConfig.model_validate(_config(label_filter=[], selected_issue_numbers=[41]))
    GitHubSourceConfig.model_validate(_config(label_filter=["bug"], selected_issue_numbers=[]))


def test_config_max_rework_rounds_default_and_range():
    block = _config()
    del block["max_rework_rounds"]
    assert GitHubSourceConfig.model_validate(block).max_rework_rounds == 1
    for rounds in (0, 3):
        assert GitHubSourceConfig.model_validate(_config(max_rework_rounds=rounds)).max_rework_rounds == rounds
    for rounds in (-1, 4, "1"):
        with pytest.raises(ValidationError):
            GitHubSourceConfig.model_validate(_config(max_rework_rounds=rounds))


@pytest.mark.parametrize(
    "overrides",
    [
        {"source_id": "ghs-XYZ"},
        {"source_id": "src-1a2b3c4d"},
        {"repository_full_name": "acme"},
        {"repository_full_name": "acme/billing/extra"},
        {"repository_full_name": "acme/.."},
        {"repository_full_name": "../acme/billing"},
        {"repository_full_name": "https://github.com/acme/billing"},
        {"repository_full_name": "acme/bill ing"},
        {"workflow_repository_id": ""},
        {"label_filter": ["bug", "bug"]},
        {"label_filter": [""]},
        {"selected_issue_numbers": [41, 41]},
        {"selected_issue_numbers": [0]},
        {"selected_issue_numbers": ["41"]},
        {"start_at": "2026-10-06T09:00:00"},
        {"fix_verification_profile_id": ""},
        {"review_agent_id": ""},
        {"run_mode": "always"},
        {"enabled": "true"},
        {"config_revision": 0},
    ],
)
def test_config_rejects_bad_fields(overrides):
    with pytest.raises(ValidationError):
        GitHubSourceConfig.model_validate(_config(**overrides))


# --- GitHubSourceConfig 새 칸 (phase 11 step 3, ARCHITECTURE "소스 설정 새 칸") -----------

# phase 8 에 저장된 config_json 모양 그대로 — 새 칸이 없다.
PHASE8_CONFIG = {
    "source_id": "ghs-1a2b3c4d",
    "repository_full_name": "acme/billing",
    "workflow_repository_id": "billing",
    "label_filter": ["bug", "runloom"],
    "selected_issue_numbers": [],
    "start_at": "2026-10-06T09:00:00+09:00",
    "fix_verification_profile_id": "vp-pytest",
    "review_agent_id": "agent-claude-mac",
    "run_mode": "auto",
    "max_rework_rounds": 1,
    "enabled": True,
    "config_revision": 3,
}


def _all_open(**overrides) -> dict:
    return {**_config(
        intake="all_open", label_filter=[], selected_issue_numbers=[], trigger_label="runloom",
        workflow_repository_id=None, fix_verification_profile_id=None, review_agent_id=None,
        default_fix_agent_id=None, installation_id=12345678,
    ), **overrides}


def test_phase8_config_json_still_validates_with_defaults():
    config = GitHubSourceConfig.model_validate_json(json.dumps(PHASE8_CONFIG))
    assert config.intake == "filtered"
    assert (config.trigger_label, config.default_fix_agent_id, config.installation_id) == (None, None, None)
    assert config.workflow_repository_id == "billing"
    dumped = config.model_dump(mode="json")
    assert {k: dumped[k] for k in PHASE8_CONFIG} == PHASE8_CONFIG


def test_all_open_accepts_empty_scope_and_undecided_ids():
    config = GitHubSourceConfig.model_validate(_all_open())
    assert config.intake == "all_open"
    assert (config.label_filter, config.selected_issue_numbers) == ([], [])
    assert (config.workflow_repository_id, config.fix_verification_profile_id, config.review_agent_id) == (
        None, None, None)
    assert (config.trigger_label, config.installation_id) == ("runloom", 12345678)
    # 결정된 값·기본 수정 Agent 도 받는다
    config = GitHubSourceConfig.model_validate(_all_open(
        workflow_repository_id="billing", default_fix_agent_id="agent-codex-mac", trigger_label=None))
    assert (config.workflow_repository_id, config.default_fix_agent_id, config.trigger_label) == (
        "billing", "agent-codex-mac", None)


def test_filtered_keeps_scope_and_id_requirements():
    with pytest.raises(ValidationError):
        GitHubSourceConfig.model_validate(_config(intake="filtered", label_filter=[], selected_issue_numbers=[]))
    for field in ("workflow_repository_id", "fix_verification_profile_id", "review_agent_id"):
        with pytest.raises(ValidationError):
            GitHubSourceConfig.model_validate(_config(**{field: None}))
        block = _config()
        del block[field]
        with pytest.raises(ValidationError):
            GitHubSourceConfig.model_validate(block)


@pytest.mark.parametrize(
    "overrides",
    [
        {"intake": "all"},
        {"intake": None},
        {"trigger_label": ""},
        {"trigger_label": 1},
        {"default_fix_agent_id": ""},
        {"installation_id": 0},
        {"installation_id": "12345678"},
        {"workflow_repository_id": ""},
        {"review_agent_id": ""},
        {"fix_verification_profile_id": ""},
        {"label_filter": ["bug", "bug"]},
    ],
)
def test_new_fields_reject_bad_values(overrides):
    with pytest.raises(ValidationError):
        GitHubSourceConfig.model_validate(_all_open(**overrides))


def test_contract_md_has_an_all_open_example():
    text = CONTRACT_MD.read_text(encoding="utf-8")
    blocks = [json.loads(m) for m in _FENCE.findall(text)]
    examples = [b for b in blocks if b.get("intake") == "all_open"]
    assert len(examples) == 1
    config = GitHubSourceConfig.model_validate(examples[0])
    assert config.model_dump(mode="json") == examples[0]


@pytest.mark.parametrize("name", ["acme/billing", "a-b/c.d_e", "Acme-2/Repo.Name"])
def test_config_accepts_repository_full_names(name):
    assert GitHubSourceConfig.model_validate(_config(repository_full_name=name)).repository_full_name == name


# --- AssigneeBinding ------------------------------------------------------------


@pytest.mark.parametrize(
    "overrides",
    [
        {"github_user_id": "5812345"},
        {"github_user_id": 0},
        {"github_login": ""},
        {"agent_id": ""},
        {"source_id": "ghs-1"},
    ],
)
def test_binding_rejects_bad_fields(overrides):
    with pytest.raises(ValidationError):
        AssigneeBinding.model_validate(_binding(**overrides))


# --- GitHubIssueSnapshot --------------------------------------------------------


@pytest.mark.parametrize(
    "overrides",
    [
        {"repository_id": "700112233"},
        {"issue_id": 0},
        {"number": 0},
        {"repository_full_name": "acme"},
        {"state": "merged"},
        {"title": ""},
        {"body": None},
        {"assignee_ids": ["5812345"]},
        {"assignee_ids": [5812345, 5812346]},  # login 수와 다름
        {"html_url": ""},
        {"created_at": "2026-10-06 10:12:00"},
        {"updated_at": "2026-10-06T10:15:30"},
        {"is_pull_request": "false"},
    ],
)
def test_snapshot_rejects_bad_fields(overrides):
    with pytest.raises(ValidationError):
        GitHubIssueSnapshot.model_validate(_snapshot(**overrides))


def test_snapshot_accepts_unassigned_and_closed():
    parsed = GitHubIssueSnapshot.model_validate(
        _snapshot(assignee_ids=[], assignee_logins=[], state="closed", body="")
    )
    assert parsed.assignee_ids == [] and parsed.state == "closed"


def test_snapshot_digest_is_stable_sha256():
    snapshot = GitHubIssueSnapshot.model_validate(_snapshot())
    digest = snapshot_digest(snapshot)
    assert re.fullmatch(r"[0-9a-f]{64}", digest)
    assert snapshot_digest(GitHubIssueSnapshot.model_validate(_snapshot())) == digest
    reordered = dict(reversed(list(_snapshot().items())))
    assert snapshot_digest(GitHubIssueSnapshot.model_validate(reordered)) == digest


@pytest.mark.parametrize(
    "overrides",
    [
        {"body": "재현: 다른 조건"},
        {"labels": ["bug"]},
        {"assignee_ids": [1], "assignee_logins": ["other"]},
        {"state": "closed"},
        {"updated_at": "2026-10-06T10:16:00Z"},
    ],
)
def test_snapshot_digest_changes_with_content(overrides):
    base = snapshot_digest(GitHubIssueSnapshot.model_validate(_snapshot()))
    assert snapshot_digest(GitHubIssueSnapshot.model_validate(_snapshot(**overrides))) != base


# --- SourceDelivery -------------------------------------------------------------


def test_delivery_delivered_requires_comment_id():
    with pytest.raises(ValidationError):
        SourceDelivery.model_validate(_delivery(state="delivered", comment_id=None))
    parsed = SourceDelivery.model_validate(
        _delivery(state="delivered", comment_id=1234567890, next_at=None, last_error=None)
    )
    assert parsed.comment_id == 1234567890


@pytest.mark.parametrize("state", ["pending", "sending", "unknown", "failed"])
def test_delivery_accepts_states(state):
    assert SourceDelivery.model_validate(_delivery(state=state)).state == state


@pytest.mark.parametrize(
    "overrides",
    [
        {"state": "sent"},
        {"body_digest": "7C" * 32},
        {"body_revision": 0},
        {"attempts": -1},
        {"issue_number": 0},
        {"comment_id": "123"},
        {"next_at": "2026-10-06T10:31:00"},
        {"delivery_id": ""},
        {"task_id": ""},
    ],
)
def test_delivery_rejects_bad_fields(overrides):
    with pytest.raises(ValidationError):
        SourceDelivery.model_validate(_delivery(**overrides))


# --- IssuePrLink (phase 9 기준선) ---------------------------------------------------------

_LINK = {
    "issue_number": 12,
    "issue_title": "목록 정렬 오류",
    "issue_opened_at": "2026-08-01T09:00:00Z",
    "pr_number": 15,
    "pr_merged_at": "2026-08-02T10:30:00Z",
}


def test_issue_pr_link_roundtrip():
    parsed = IssuePrLink.model_validate(_LINK)
    assert parsed.model_dump(mode="json") == _LINK


@pytest.mark.parametrize(
    "overrides",
    [
        {"issue_number": 0},
        {"issue_number": "12"},
        {"issue_title": ""},
        {"issue_opened_at": "2026-08-01T09:00:00"},
        {"pr_number": 0},
        {"pr_merged_at": None},
        {"pr_merged_at": "어제"},
        {"extra": 1},
    ],
)
def test_issue_pr_link_rejects_bad_fields(overrides):
    with pytest.raises(ValidationError):
        IssuePrLink.model_validate({**_LINK, **overrides})


# --- PullRequestRef (phase 12 초안 PR) ------------------------------------------------------

_PR = {
    "number": 31,
    "html_url": "https://github.com/acme/billing/pull/31",
    "state": "open",
    "draft": True,
    "merged_at": None,
}


def test_pull_request_ref_roundtrip():
    assert PullRequestRef.model_validate(_PR).model_dump(mode="json") == _PR
    merged = {**_PR, "state": "closed", "draft": False, "merged_at": "2026-10-06T12:00:00Z"}
    assert PullRequestRef.model_validate(merged).merged_at == "2026-10-06T12:00:00Z"


@pytest.mark.parametrize(
    "overrides",
    [{"number": 0}, {"state": "merged"}, {"html_url": ""}, {"draft": "true"}, {"merged_at": "2026-10-06"}, {"x": 1}],
)
def test_pull_request_ref_rejects(overrides):
    with pytest.raises(ValidationError):
        PullRequestRef.model_validate({**_PR, **overrides})
