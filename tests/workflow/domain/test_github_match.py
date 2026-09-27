"""자동 매칭 — ARCHITECTURE "GitHub App 연결 — phase 11 / 자동 매칭 (step 6)", ADR-0017."""

from workflow.contracts.github import GitHubSourceConfig
from workflow.contracts.v1 import Capability
from workflow.domain.github_match import MatchAgent, SourceMatch, match_source

REPO = "acme/billing"


def _source(**overrides) -> GitHubSourceConfig:
    data = {
        "source_id": "ghs-1a2b3c4d",
        "repository_full_name": REPO,
        "intake": "all_open",
        "label_filter": [],
        "selected_issue_numbers": [],
        "start_at": "2026-09-27T00:00:00Z",
        "run_mode": "auto",
        "max_rework_rounds": 1,
        "enabled": True,
        "config_revision": 1,
    }
    return GitHubSourceConfig.model_validate({**data, **overrides})


def _agent(agent_id: str, code: str, *, repository_id: str = "billing", github: str | None = REPO,
           profiles: tuple[str, ...] = ("vp-pytest",)) -> MatchAgent:
    return MatchAgent(
        agent_id=agent_id,
        github_repository=github,
        repository_id=repository_id,
        capabilities=(Capability(code=code, scope={"repository_id": repository_id}),),
        verification_profile_ids=profiles,
    )


FIX = _agent("agent-fix", "code.fix")
REVIEW = _agent("agent-review", "code.review", profiles=())


def _codes(match: SourceMatch) -> list[str]:
    return [b.code for b in match.blockers]


def test_one_runner_for_the_repository_fills_every_empty_field():
    match = match_source(_source(), [FIX, REVIEW])

    assert match == SourceMatch("billing", "vp-pytest", "agent-fix", "agent-review", ())


def test_github_repository_is_compared_case_insensitively():
    match = match_source(_source(repository_full_name="Acme/Billing"), [FIX, REVIEW])

    assert match.workflow_repository_id == "billing"


def test_no_runner_for_the_repository_is_repository_unmatched():
    other = _agent("agent-shop", "code.fix", repository_id="shop", github="acme/shop")
    legacy = _agent("agent-old", "code.fix", github=None)  # 옛 러너 — 보고 없음

    match = match_source(_source(), [other, legacy])

    assert _codes(match) == ["repository_unmatched"]
    assert match.blockers[0].actor == "operator"
    assert (match.workflow_repository_id, match.fix_agent_id, match.review_agent_id) == (None, None, None)


def test_two_local_repositories_for_the_same_github_repository_is_ambiguous():
    clone = _agent("agent-fix-2", "code.fix", repository_id="billing-clone")

    match = match_source(_source(), [FIX, clone, REVIEW])

    assert _codes(match) == ["repository_ambiguous"]
    assert match.workflow_repository_id is None


def test_configured_values_win_over_matching():
    match = match_source(
        _source(workflow_repository_id="billing", fix_verification_profile_id="vp-lint",
                review_agent_id="agent-review-2", default_fix_agent_id="agent-fix-2"),
        [FIX, REVIEW],
    )

    assert match == SourceMatch("billing", "vp-lint", "agent-fix-2", "agent-review-2", ())


def test_configured_repository_needs_no_github_report():
    legacy = _agent("agent-old", "code.fix", github=None)

    match = match_source(_source(workflow_repository_id="billing"), [legacy])

    assert match.fix_agent_id == "agent-old"


def test_two_fix_agents_wait_for_a_choice():
    second = _agent("agent-fix-2", "code.fix")

    match = match_source(_source(), [FIX, second, REVIEW])

    assert _codes(match) == ["fix_agent_ambiguous"]
    assert match.fix_agent_id is None
    assert match.review_agent_id == "agent-review"


def test_no_fix_agent_for_the_repository_is_fix_agent_unmatched():
    match = match_source(_source(), [REVIEW])

    assert _codes(match) == ["fix_agent_unmatched"]


def test_default_fix_agent_wins_over_candidates():
    second = _agent("agent-fix-2", "code.fix")

    match = match_source(_source(default_fix_agent_id="agent-fix-2"), [FIX, second, REVIEW])

    assert match.fix_agent_id == "agent-fix-2"
    assert match.blockers == ()


def test_bound_single_assignee_wins_over_default_and_candidates():
    second = _agent("agent-fix-2", "code.fix", profiles=("vp-2",))

    match = match_source(_source(default_fix_agent_id="agent-fix"), [FIX, second, REVIEW],
                         assignee_ids=(7,), bindings={7: "agent-fix-2"})

    assert match.fix_agent_id == "agent-fix-2"
    assert match.fix_verification_profile_id == "vp-2"


def test_zero_or_many_assignees_do_not_block_when_matching_decides():
    for assignees in ((), (7, 8)):
        match = match_source(_source(), [FIX, REVIEW], assignee_ids=assignees, bindings={7: "agent-x"})
        assert match.fix_agent_id == "agent-fix"
        assert match.blockers == ()


def test_unbound_single_assignee_falls_back_to_matching():
    match = match_source(_source(), [FIX, REVIEW], assignee_ids=(9,), bindings={})

    assert match.fix_agent_id == "agent-fix"


def test_filtered_source_does_not_pick_a_fix_agent_automatically():
    """filtered 는 ③ 을 하지 않는다 — 담당 대기(assignee_*)는 준비 판정이 기존대로 낸다."""
    source = _source(intake="filtered", label_filter=["bug"], workflow_repository_id="billing",
                     fix_verification_profile_id="vp-pytest", review_agent_id="agent-review")

    assert match_source(source, [FIX, REVIEW]) == SourceMatch("billing", "vp-pytest", None, "agent-review", ())
    assert match_source(source, [FIX, REVIEW], assignee_ids=(7,), bindings={7: "agent-fix"}).fix_agent_id == "agent-fix"
    assert match_source(source.model_copy(update={"default_fix_agent_id": "agent-fix"}), []).fix_agent_id == "agent-fix"


def test_profile_comes_from_the_fix_agent_registration():
    none = _agent("agent-fix", "code.fix", profiles=())
    many = _agent("agent-fix", "code.fix", profiles=("vp-pytest", "vp-lint"))

    assert _codes(match_source(_source(), [none, REVIEW])) == ["profile_unmatched"]
    assert _codes(match_source(_source(), [many, REVIEW])) == ["profile_ambiguous"]
    assert match_source(_source(fix_verification_profile_id="vp-lint"), [many, REVIEW]).blockers == ()


def test_review_agent_unmatched_and_ambiguous():
    second = _agent("agent-review-2", "code.review")

    assert _codes(match_source(_source(), [FIX])) == ["review_agent_unmatched"]
    assert _codes(match_source(_source(), [FIX, REVIEW, second])) == ["review_agent_ambiguous"]


def test_fix_and_review_blockers_are_split_for_their_tasks():
    match = match_source(_source(), [])
    assert [b.code for b in match.fix_blockers] == ["repository_unmatched"]
    assert [b.code for b in match.review_blockers] == ["repository_unmatched"]

    match = match_source(_source(), [_agent("agent-fix", "code.fix", profiles=())])
    assert [b.code for b in match.fix_blockers] == ["profile_unmatched"]
    assert [b.code for b in match.review_blockers] == ["review_agent_unmatched"]


def test_reasons_are_plain_sentences():
    reasons = {b.code: b.reason for b in match_source(_source(), []).blockers}

    assert reasons == {"repository_unmatched": "acme/billing 을 등록한 러너 없음 — 러너에서 이 저장소 폴더를 등록하세요"}


def test_one_agent_with_fix_and_review_takes_both_roles():
    """러너 한 명령으로 만든 Agent(ADR-0018 결정 1) — 능력 두 개, 수정·검토 모두 그 Agent 이고 대기 코드 없음."""
    both = MatchAgent(
        agent_id="agent-runner",
        github_repository=REPO,
        repository_id=REPO,
        capabilities=(
            Capability(code="code.fix", scope={"repository_id": REPO}),
            Capability(code="code.review", scope={"repository_id": REPO}),
        ),
        verification_profile_ids=("vp-check",),
    )

    match = match_source(_source(), [both])

    assert match == SourceMatch(REPO, "vp-check", "agent-runner", "agent-runner", ())
