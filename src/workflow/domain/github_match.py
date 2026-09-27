"""자동 매칭 — ARCHITECTURE "GitHub App 연결 — phase 11 / 자동 매칭 (step 6)", ADR-0017.

소스 설정의 빈 칸(로컬 저장소·검증 프로필·수정·검토 Agent)을 러너가 보고한 GitHub 저장소(`found.github_repository`)와
등록 능력으로 판정 때마다 계산한다. 저장하지 않는다 — 설정에 값이 있으면 그 값이 우선이고 매칭은 `None` 칸만 채운다.
DB·HTTP 를 보지 않는다. Agent 행은 호출자가 `MatchAgent` 로 넘긴다.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from workflow.contracts.github import GitHubSourceConfig
from workflow.contracts.v1 import BUILTIN_KINDS, Capability
from workflow.domain.task_readiness import Blocker

_FIX_KIND = next(spec for spec in BUILTIN_KINDS if spec.kind == "bug_fix")
_REVIEW_KIND = next(spec for spec in BUILTIN_KINDS if spec.kind == "code_review")

_REPOSITORY_CODES = ("repository_unmatched", "repository_ambiguous")
_FIX_CODES = (*_REPOSITORY_CODES, "fix_agent_unmatched", "fix_agent_ambiguous", "profile_unmatched", "profile_ambiguous")
_REVIEW_CODES = (*_REPOSITORY_CODES, "review_agent_unmatched", "review_agent_ambiguous")


@dataclass(frozen=True)
class MatchAgent:
    agent_id: str
    github_repository: str | None  # 러너 보고 `found.github_repository`. 옛 러너는 None
    repository_id: str | None  # 로컬 등록의 scope 값
    capabilities: tuple[Capability, ...]
    verification_profile_ids: tuple[str, ...]


@dataclass(frozen=True)
class SourceMatch:
    workflow_repository_id: str | None
    fix_verification_profile_id: str | None
    fix_agent_id: str | None
    review_agent_id: str | None
    blockers: tuple[Blocker, ...]

    @property
    def fix_blockers(self) -> tuple[Blocker, ...]:
        """수정 Task 를 막는 사유 — 검토 Agent 가 없어도 수정은 먼저 돈다(검토 Task 가 따로 기다린다)."""
        return tuple(b for b in self.blockers if b.code in _FIX_CODES)

    @property
    def review_blockers(self) -> tuple[Blocker, ...]:
        return tuple(b for b in self.blockers if b.code in _REVIEW_CODES)


def _one(values: Sequence[str], unmatched: Blocker, ambiguous: Blocker, blockers: list[Blocker]) -> str | None:
    distinct = sorted(set(values))
    if len(distinct) == 1:
        return distinct[0]
    blockers.append(unmatched if not distinct else ambiguous)
    return None


def _capable(agents: Sequence[MatchAgent], code: str, repository_id: str) -> list[str]:
    wanted = Capability(code=code, scope={"repository_id": repository_id})
    return [a.agent_id for a in agents if wanted in a.capabilities]


def _choice(what: str, count: int) -> str:
    return f"{what} {count}개 — 설정에서 하나 고르세요"


def match_source(
    source: GitHubSourceConfig,
    agents: Sequence[MatchAgent],
    *,
    assignee_ids: Sequence[int] = (),
    bindings: Mapping[int, str] | None = None,
) -> SourceMatch:
    """`agents` 는 이 워크스페이스의 로컬 Agent. 수정 Agent 는 ① 담당자 1명의 연결 ② `default_fix_agent_id`
    ③ `code.fix {repository_id}` 후보가 하나(`all_open` 만) 순서. 검증 프로필은 정해진 수정 Agent 의 등록이 보고한
    프로필이 하나일 때. 로컬 저장소가 정해지지 않으면 Agent·프로필도 정하지 않는다(그 사유 하나만 낸다)."""
    blockers: list[Blocker] = []
    full_name = source.repository_full_name
    repository_id = source.workflow_repository_id
    if repository_id is None:
        reported = [
            a.repository_id for a in agents
            if a.repository_id and a.github_repository and a.github_repository.casefold() == full_name.casefold()
        ]
        repository_id = _one(
            reported,
            Blocker("repository_unmatched", f"{full_name} 을 등록한 러너 없음 — 러너에서 이 저장소 폴더를 등록하세요",
                    "operator"),
            Blocker("repository_ambiguous", _choice(f"{full_name} 을 등록한 로컬 저장소", len(set(reported))),
                    "operator"),
            blockers,
        )
    if repository_id is None:
        return SourceMatch(None, source.fix_verification_profile_id, None, source.review_agent_id, tuple(blockers))

    fix_agent_id = _fix_agent(source, agents, repository_id, assignee_ids, bindings or {}, blockers)
    profile_id = source.fix_verification_profile_id
    if profile_id is None and fix_agent_id is not None:
        profiles = next((a.verification_profile_ids for a in agents if a.agent_id == fix_agent_id), ())
        profile_id = _one(
            profiles,
            Blocker("profile_unmatched", f"수정 Agent {fix_agent_id} 의 등록에 검증 프로필 없음", "operator"),
            Blocker("profile_ambiguous", _choice(f"수정 Agent {fix_agent_id} 의 검증 프로필", len(set(profiles))),
                    "operator"),
            blockers,
        )
    review_agent_id = source.review_agent_id
    if review_agent_id is None:
        reviewers = _capable(agents, _REVIEW_KIND.capability_code, repository_id)
        review_agent_id = _one(
            reviewers,
            Blocker("review_agent_unmatched", f"{repository_id} 을 검토할 Agent 없음", "operator"),
            Blocker("review_agent_ambiguous", _choice(f"{repository_id} 검토 Agent", len(reviewers)), "operator"),
            blockers,
        )
    return SourceMatch(repository_id, profile_id, fix_agent_id, review_agent_id, tuple(blockers))


def _fix_agent(
    source: GitHubSourceConfig, agents: Sequence[MatchAgent], repository_id: str, assignee_ids: Sequence[int],
    bindings: Mapping[int, str], blockers: list[Blocker],
) -> str | None:
    if len(assignee_ids) == 1 and assignee_ids[0] in bindings:
        return bindings[assignee_ids[0]]
    if source.default_fix_agent_id is not None:
        return source.default_fix_agent_id
    if source.intake != "all_open":
        return None  # filtered — 담당 대기(assignee_*)는 준비 판정이 기존대로 낸다
    fixers = _capable(agents, _FIX_KIND.capability_code, repository_id)
    return _one(
        fixers,
        Blocker("fix_agent_unmatched", f"{repository_id} 을 수정할 Agent 없음", "operator"),
        Blocker("fix_agent_ambiguous", _choice(f"{repository_id} 수정 Agent", len(fixers)), "operator"),
        blockers,
    )
