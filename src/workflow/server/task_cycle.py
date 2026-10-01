"""GitHub 업무 순환의 준비 판정 재료 — ARCHITECTURE "준비 판정 — 대기 코드", ADR-0014 결정 5·10.

DB 행을 `domain.task_readiness.TaskFacts` 값으로 모아 `evaluate_readiness` 에 넘긴다. 업무 하나씩 따로 평가하고
다른 Task 에 관한 사실(같은 로컬 등록에서 도는 수정 실행)은 값으로만 넘긴다 — 독립 업무는 서로의 대기에 묶이지 않는다.
가져온 Task 와 직접 등록 Task 는 같은 `github_sync.task_intake_facts` 를 거친다. 검토 Task 는 원본 이슈가 없으므로
수정 Task(선행)의 원본 상태·허용 저장소·재작업 상한을 따른다. 착수·후속 저장은 워커가 한다.

자동 매칭(phase 11 step 6, ADR-0017): 소스 설정의 빈 칸은 판정 때마다 `github_match.match_source` 로 계산한다(저장하지
않음). 로컬 저장소를 매칭하는 소스의 Task 는 저장 scope 대신 매칭한 저장소로 능력을 본다. 수정 Task 는 수정 Agent·검증
프로필, 검토 Task(설정·Task 에 검토 Agent 없음)는 검토 Agent 를 매칭 결과로 정한다.

사람 요청(step 11): 운영자가 정해야 풀리는 대기(`READINESS_REQUEST_CODES`)는 워커가 revision 마다 한 번 요청으로 남긴다
(`readiness_cause_key`). 이런 요청은 그 대기 사유가 이미 막고 있으므로 `decision_pending` 에 세지 않는다 — 담당자를
한 명으로 줄이는 식으로 사유가 사라지면 응답 없이도 착수한다. 응답 내용은 `request_text` 로 다음 실행 요청에 붙는다.
"""

import json
from dataclasses import replace
from sqlite3 import Connection, Row

from workflow.adapters import repo, secret_store
from workflow.adapters.secret_store import SecretStore
from workflow.contracts.github import GitHubSourceConfig
from workflow.contracts.v1 import Capability, format_work_key
from workflow.domain.execution_policy import BUILTIN_POLICIES, policy_for
from workflow.domain.form_sections import FORM_HEADINGS, FORM_LABELS
from workflow.domain.github_match import MatchAgent, SourceMatch, match_source
from workflow.domain.issue_intake import IntakeFacts
from workflow.domain.selection import Candidate
from workflow.domain.delegation import OWNER_APPROVAL_PREFIX
from workflow.domain.handoff_context import compose_request
from workflow.domain.task_readiness import ExecutorFacts, TaskFacts, TaskReadiness, evaluate_readiness
from workflow.server import github_sync, owner_approval
from workflow.server.settings import Settings

# 원본 이슈가 없는(직접 등록) 수정 Task 의 자동 재작업 상한 — 소스 설정의 기본값과 같다
DEFAULT_MAX_REWORK_ROUNDS: int = GitHubSourceConfig.model_fields["max_rework_rounds"].default
# 저장소 작업 트리를 쓰는 종류 — 같은 로컬 등록에서 하나씩만 돈다(`repository_busy`)
_WORKTREE_KINDS = tuple(kind for kind, policy in BUILTIN_POLICIES.items() if policy.target == "code_change")


# 운영자가 정해야 풀리는 대기 — 요청으로 남긴다. 그 밖의 대기(연결 끊김·저장소 사용 중 등)는 시스템이 풀린다
READINESS_REQUEST_CODES = ("assignee_multiple", "delegation_denied", "input_missing")
READINESS_REQUEST_PREFIX = "ready:"


def readiness_cause_key(code: str, task_revision: int) -> str:
    return f"{READINESS_REQUEST_PREFIX}{code}:r{task_revision}"


def request_text(conn: Connection, task: Row) -> str:
    """다음 실행의 요청 문구 — Task 요청 원문 뒤에 운영자 응답(글이 있는 것, 응답 순)을 붙인다. 원문은 바꾸지 않는다."""
    answers = [r for r in repo.list_human_responses(conn, task["task_id"]) if r["text"].strip()]
    if not answers:
        return task["request"]
    lines = [task["request"].strip(), ""] if task["request"].strip() else []
    lines.append("## 사람 응답 (운영자)")
    for answer in answers:
        lines += [f"- 질문: {answer['question']}", f"  답: {answer['text'].strip()}"]
    return "\n".join(lines)


def execution_request_text(conn: Connection, task: Row, *, with_answers: bool) -> str:
    """실행 요청의 `request` — 업무 키·제목·양식 칸·맡긴 사람 지시를 원래 요청문 앞에 붙인다(`compose_request`).
    원래 요청문은 `with_answers` 면 `request_text`(사람 응답 포함), 아니면 단계 원문. 준비 판정은 `request_text` 를 쓴다."""
    body = request_text(conn, task) if with_answers else task["request"]
    work = repo.work_item_of_task(conn, task["task_id"])
    if work is None:
        return body
    form = json.loads(work["form_json"])
    note_by = None
    if work["handoff_note_by_member_id"] is not None:
        member = repo.get_member(conn, work["session_id"], work["handoff_note_by_member_id"])
        note_by = member["display_name"] if member is not None else None
    return compose_request(
        work_key=format_work_key(work["key_number"]), title=work["title"],
        form_fields=[(FORM_LABELS[key], form[key]["value"]) for key in FORM_HEADINGS if key in form],
        note=work["handoff_note"], note_by=note_by, body=body,
    )


def origin_source(conn: Connection, task: Row) -> tuple[Row | None, GitHubSourceConfig | None]:
    """Task 가 속한 업무의 원본 칸으로 찾은 원본 이슈 매핑과 소스 설정(ADR-0020 — 선행 사슬을 거슬러 오르지 않는다).
    GitHub 원본이 아니면 (None, None). 새 업무로 이어진 후속(원본 이슈 칸 없음)은 (None, 설정)."""
    work = repo.work_item_of_task(conn, task["task_id"])
    if work is None or work["source_type"] != "github" or work["source_id"] is None:
        return None, None
    issue = (
        repo.get_source_issue(conn, task["session_id"], work["source_id"], int(work["source_item_id"]))
        if work["source_item_id"] is not None else None
    )
    return issue, repo.get_github_source(conn, task["session_id"], work["source_id"])


def max_rework_rounds(conn: Connection, task: Row) -> int:
    _, config = origin_source(conn, task)
    return config.max_rework_rounds if config is not None else DEFAULT_MAX_REWORK_ROUNDS


def _executor(conn: Connection, agent: Row, session_id: str) -> ExecutorFacts:
    connector = repo.get_connector(conn, agent["connector_id"]) if agent["connector_id"] else None
    declared = connector["supported_kinds_json"] if connector is not None else None
    reported = connector["capabilities_json"] if connector is not None else None
    return ExecutorFacts(
        owner_name=owner_approval.owner_name(conn, session_id, agent["agent_id"]),
        agent_id=agent["agent_id"],
        connector_id=agent["connector_id"],
        repository_id=agent["repository_id"],
        connection_type=agent["connection_type"],
        connection_state=agent["connection_state"],
        last_seen_at=agent["last_seen_at"],
        supported_kinds=tuple(json.loads(declared)) if declared is not None else None,
        runner_capabilities=tuple(json.loads(reported)) if reported is not None else None,
    )


def _match_agent(agent: Row) -> MatchAgent:
    discovered = json.loads(agent["discovered_json"] or "{}")
    found = discovered.get("found") if isinstance(discovered, dict) else None
    github = found.get("github_repository") if isinstance(found, dict) else None
    return MatchAgent(
        agent_id=agent["agent_id"],
        github_repository=github if isinstance(github, str) else None,
        repository_id=agent["repository_id"],
        capabilities=tuple(Capability.model_validate(c) for c in json.loads(agent["capabilities_json"])),
        verification_profile_ids=tuple(json.loads(agent["verification_profile_ids_json"])),
    )


def source_match(conn: Connection, task: Row) -> SourceMatch | None:
    """Task 의 원본 소스 자동 매칭. 원본이 없으면 None. 담당 연결은 원본 이슈가 이 Task 에 있을 때(수정 Task)만 넘긴다."""
    _, config = origin_source(conn, task)
    if config is None:
        return None
    intake = github_sync.task_intake_facts(conn, task["session_id"], task["task_id"])
    return _match(config, repo.list_session_agents(conn, task["session_id"]), intake, task["chosen_agent_id"])


def match_for_source(conn: Connection, session_id: str, config: GitHubSourceConfig) -> SourceMatch:
    """소스 단위 자동 매칭 — 연결 화면의 저장소 카드. 이슈가 없으니 담당 연결 없이(담당자 없는 이슈 기준) 계산한다."""
    local = [_match_agent(a) for a in repo.list_session_agents(conn, session_id) if a["connection_type"] == "local"]
    return match_source(config, local)


def _match(config: GitHubSourceConfig, agents: list[Row], intake: IntakeFacts, chosen_agent_id: str | None,
           pair_agent_id: str | None = None) -> SourceMatch:
    """사람이 이 Task 에 맡긴 Agent(`chosen_agent_id`)가 있으면 수정 Agent 는 그것, 검토는 수정 Agent(`pair_agent_id`)가
    검토할 수 있으면 그것이다(phase 17 — 같은 저장소의 러너 여럿)."""
    local = [_match_agent(a) for a in agents if a["connection_type"] == "local"]
    return match_source(config, local, assignee_ids=intake.assignee_ids or (), bindings=intake.bindings,
                        chosen_agent_id=chosen_agent_id, pair_agent_id=pair_agent_id)


def _match_facts(task: Row, config: GitHubSourceConfig, match: SourceMatch, required: Capability, *, fix: bool) -> dict:
    values: dict = {}
    if config.workflow_repository_id is None and match.workflow_repository_id is not None:
        (key,) = required.scope
        values["required"] = Capability(code=required.code, scope={key: match.workflow_repository_id})
    if fix:
        values.update(matched_agent_id=match.fix_agent_id, match_blockers=match.fix_blockers,
                      auto_match=config.intake == "all_open")
    elif task["chosen_agent_id"] is None:
        values.update(matched_agent_id=match.review_agent_id, match_blockers=match.review_blockers, auto_match=True)
    return values


def task_facts(conn: Connection, task: Row, *, now: str, settings: Settings, **overrides) -> TaskFacts:
    """Task 하나의 준비 판정 입력. `overrides` 는 호출 문맥의 값(검토 짝 Agent·선행 결과·재작업 요청 등)."""
    session_id = task["session_id"]
    intake = github_sync.task_intake_facts(conn, session_id, task["task_id"])
    issue, config = origin_source(conn, task)
    agents = repo.list_session_agents(conn, session_id)
    requests = repo.list_human_requests(conn, task["task_id"])
    asked = [r["task_revision"] for r in requests if r["code"].endswith("_needs_information")]
    allowed = {name.lower() for name in settings.github_repos}
    required = Capability.model_validate_json(task["required_capability_json"])
    values = {
        **intake.as_kwargs(),
        "task_id": task["task_id"],
        "kind": task["kind"],
        "now": now,
        "offline_after_seconds": settings.limits.heartbeat_offline_seconds,
        "required": required,
        "candidates": [
            Candidate(a["agent_id"], tuple(Capability.model_validate(c) for c in json.loads(a["capabilities_json"])))
            for a in agents
        ],
        "executors": {a["agent_id"]: _executor(conn, a, session_id) for a in agents},
        "chosen_agent_id": task["chosen_agent_id"],
        # App 설치 소스는 설치 저장소 자체가, 화면에서 붙여 넣은 PAT 가 있으면 소스 저장소가 허용 범위다(ADR-0017,
        # `github_clients` 와 같은 규칙) — 환경변수 허용 목록은 환경변수 토큰 연결에만
        "repository_allowed": (
            config is None or config.installation_id is not None or config.repository_full_name.lower() in allowed
            or SecretStore(settings.secret_dir).exists(secret_store.GITHUB_TOKEN)
        ),
        "task_revision": task["revision"],
        "request_text": request_text(conn, task),
        "information_requested_at_revision": max(asked, default=None),
        # 준비 판정 대기 요청(`ready:`)과 소유자 승인 요청은 그 대기 사유가 이미 막는다 — 사유가 두 번 보이지 않게
        "open_request_ids": tuple(
            r["request_id"] for r in requests
            if r["state"] == "open" and not r["cause_key"].startswith((READINESS_REQUEST_PREFIX, OWNER_APPROVAL_PREFIX))
        ),
        "owner_approvals": owner_approval.approval_facts(conn, task),
        "source_state": issue["state"] if issue is not None else None,
        "max_rework_rounds": config.max_rework_rounds if config is not None else DEFAULT_MAX_REWORK_ROUNDS,
        "direct_work": repo.is_direct_working(conn, task["task_id"]),
    }
    if config is not None:
        match = _match(config, agents, intake, task["chosen_agent_id"], overrides.get("pair_agent_id"))
        values.update(_match_facts(task, config, match, required, fix=intake.assignee_ids is not None))
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
