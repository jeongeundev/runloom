"""GitHub 업무 순환의 준비 판정 재료 — ARCHITECTURE "준비 판정 — 대기 코드", ADR-0014 결정 5·10.

DB 행을 `domain.task_readiness.TaskFacts` 값으로 모아 `evaluate_readiness` 에 넘긴다. 업무 하나씩 따로 평가하고
다른 Task 에 관한 사실(같은 로컬 등록에서 도는 수정 실행)은 값으로만 넘긴다 — 독립 업무는 서로의 대기에 묶이지 않는다.
가져온 Task 와 직접 등록 Task 는 같은 `github_sync.task_intake_facts` 를 거친다. 검토 Task 는 원본 이슈가 없으므로
수정 Task(선행)의 원본 상태·허용 저장소·재작업 상한을 따른다. 착수·후속 저장은 워커가 한다.
"""

import json
from dataclasses import replace
from sqlite3 import Connection, Row

from workflow.adapters import repo
from workflow.contracts.github import GitHubSourceConfig
from workflow.contracts.v1 import Capability
from workflow.domain.execution_policy import BUILTIN_POLICIES, policy_for
from workflow.domain.selection import Candidate
from workflow.domain.task_readiness import ExecutorFacts, TaskFacts, TaskReadiness, evaluate_readiness
from workflow.server import github_sync
from workflow.server.settings import Settings

# 원본 이슈가 없는(직접 등록) 수정 Task 의 자동 재작업 상한 — 소스 설정의 기본값과 같다
DEFAULT_MAX_REWORK_ROUNDS: int = GitHubSourceConfig.model_fields["max_rework_rounds"].default
# 저장소 작업 트리를 쓰는 종류 — 같은 로컬 등록에서 하나씩만 돈다(`repository_busy`)
_WORKTREE_KINDS = tuple(kind for kind, policy in BUILTIN_POLICIES.items() if policy.target == "code_change")


def origin_source(conn: Connection, task: Row) -> tuple[Row | None, GitHubSourceConfig | None]:
    """Task 또는 그 선행을 따라 올라가 처음 만나는 원본 이슈 매핑과 소스 설정. 없으면 (None, None)."""
    seen: set[str] = set()
    current: Row | None = task
    while current is not None and current["task_id"] not in seen:
        seen.add(current["task_id"])
        issue = repo.get_source_issue_by_task(conn, current["session_id"], current["task_id"])
        if issue is not None:
            return issue, repo.get_github_source(conn, current["session_id"], issue["source_id"])
        predecessor = current["predecessor_task_id"]
        current = repo.get_task(conn, predecessor) if predecessor else None
    return None, None


def max_rework_rounds(conn: Connection, task: Row) -> int:
    _, config = origin_source(conn, task)
    return config.max_rework_rounds if config is not None else DEFAULT_MAX_REWORK_ROUNDS


def _executor(conn: Connection, agent: Row) -> ExecutorFacts:
    connector = repo.get_connector(conn, agent["connector_id"]) if agent["connector_id"] else None
    declared = connector["supported_kinds_json"] if connector is not None else None
    return ExecutorFacts(
        agent_id=agent["agent_id"],
        connector_id=agent["connector_id"],
        repository_id=agent["repository_id"],
        connection_type=agent["connection_type"],
        connection_state=agent["connection_state"],
        last_seen_at=agent["last_seen_at"],
        supported_kinds=tuple(json.loads(declared)) if declared is not None else None,
    )


def task_facts(conn: Connection, task: Row, *, now: str, settings: Settings, **overrides) -> TaskFacts:
    """Task 하나의 준비 판정 입력. `overrides` 는 호출 문맥의 값(검토 짝 Agent·선행 결과·재작업 요청 등)."""
    session_id = task["session_id"]
    intake = github_sync.task_intake_facts(conn, session_id, task["task_id"])
    issue, config = origin_source(conn, task)
    agents = repo.list_session_agents(conn, session_id)
    requests = repo.list_human_requests(conn, task["task_id"])
    asked = [r["task_revision"] for r in requests if r["code"].endswith("_needs_information")]
    allowed = {name.lower() for name in settings.github_repos}
    values = {
        **intake.as_kwargs(),
        "task_id": task["task_id"],
        "kind": task["kind"],
        "now": now,
        "offline_after_seconds": settings.limits.heartbeat_offline_seconds,
        "required": Capability.model_validate_json(task["required_capability_json"]),
        "candidates": [
            Candidate(a["agent_id"], tuple(Capability.model_validate(c) for c in json.loads(a["capabilities_json"])))
            for a in agents
        ],
        "executors": {a["agent_id"]: _executor(conn, a) for a in agents},
        "chosen_agent_id": task["chosen_agent_id"],
        "repository_allowed": config is None or config.repository_full_name.lower() in allowed,
        "task_revision": task["revision"],
        "information_requested_at_revision": max(asked, default=None),
        "open_request_ids": tuple(r["request_id"] for r in requests if r["state"] == "open"),
        "source_state": issue["state"] if issue is not None else None,
        "max_rework_rounds": config.max_rework_rounds if config is not None else DEFAULT_MAX_REWORK_ROUNDS,
    }
    values.update(overrides)
    return TaskFacts(**values)


def evaluate(conn: Connection, task: Row, *, now: str, settings: Settings, **overrides) -> TaskReadiness:
    """준비 판정. 실행 Agent 가 정해지면 그 로컬 등록에서 도는 다른 수정 실행을 넣어 한 번 더 본다 —
    어느 등록을 쓸지는 담당·능력 검사가 끝나야 알 수 있다."""
    facts = task_facts(conn, task, now=now, settings=settings, **overrides)
    readiness = evaluate_readiness(facts)
    agent = repo.get_agent(conn, readiness.agent_id) if readiness.agent_id is not None else None
    if agent is None or not agent["local_registration_id"] or policy_for(task["kind"]).target != "code_change":
        return readiness
    busy = repo.busy_executions(conn, agent["local_registration_id"], _WORKTREE_KINDS, exclude_task_id=task["task_id"])
    return evaluate_readiness(replace(facts, busy_execution_ids=tuple(busy))) if busy else readiness
