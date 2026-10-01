"""판단 시작 — 판단 경로·이유, 후보·근거·기준으로 요청문을 만들어 판단 단계·실행·판단 로그를 한 번에 연다
(ADR-0025, ARCHITECTURE "판단 — phase 19" 판단 경로·시작 조건·요청문·후보·근거).

- 판단 Agent 는 업무의 연결 저장소 설정 칸(`GitHubSourceConfig.triage_agent_id`)이다 — GitHub 업무는 그 소스, Jira 업무는
  프로젝트의 연결 저장소(`task_cycle.origin`). 고정 매칭이라 다른 Agent 로 바꾸지 않는다.
- 판단 실행은 소유자 승인·꺼진 러너 대기(`owner_approval`·`start_pending_at`)를 타지 않는다. 승인 필요 정책·꺼진 러너·
  판단을 모르는 옛 러너면 이유를 돌려주고 아무것도 만들지 않는다.
- 판단 단계는 종류 이름이 아니라 결과 형태(`is_triage_kind`)로 찾는다.
- `work_actions`·`worker` 는 import 하지 않는다(그 둘이 이 모듈을 쓴다).
"""

import json
from dataclasses import dataclass
from secrets import token_hex
from sqlite3 import Connection, Row
from typing import Literal
from uuid import uuid4

from workflow.adapters import repo
from workflow.adapters.errors import TriageRunning
from workflow.contracts.github import GitHubSourceConfig
from workflow.contracts.v1 import (
    Capability,
    ExecutionRequest,
    KindSpec,
    TriageCandidates,
    TriageMemberCandidate,
    TriagePredecessorCandidate,
    format_work_key,
)
from workflow.domain import triage
from workflow.domain.execution_policy import is_triage_kind
from workflow.domain.form_sections import FORM_HEADINGS, FORM_LABELS
from workflow.domain.selection import Candidate, select_agent
from workflow.server import owner_approval, task_cycle, views
from workflow.server.settings import Settings

# 판단할 수 없는 이유 — 위에서부터 첫 해당(`triage_route`), 화면에 그대로
REASONS = {
    "not_new": "담당 없는 새 업무만 판단합니다",
    "no_repository": "연결 저장소가 없는 업무는 판단하지 않습니다",
    "no_stage": "맡길 단계가 없습니다",
    "origin_closed": "원본이 닫힌 업무는 판단하지 않습니다",
    "no_agent": "판단 에이전트 없음 — 저장소 카드에서 고르세요",
    "no_runner_repository": "이 저장소를 등록한 러너 없음",
    "agent_missing": "판단 에이전트가 이 워크스페이스에 없습니다",
    "no_capability": "판단 에이전트에 이 저장소 판단 능력(code.triage) 없음",
    "owner_approval": "판단 에이전트의 맡기기 정책이 승인 필요 — '바로 실행'으로 바꾸세요",
    "offline": "판단 에이전트의 러너 꺼짐",
    "runner_outdated": "러너 업데이트 필요 — 판단 미지원",
    "no_base_commit": "러너의 기준 커밋 보고 전 — 잠시 뒤 다시",
    "running": "판단 중",
}


@dataclass(frozen=True)
class TriageRoute:
    stage: Row | None  # 맡길 단계(판단 단계 제외 — `repo.open_stage`)
    config: GitHubSourceConfig | None  # GitHub 소스 또는 Jira 연결 저장소 사본
    agent_id: str | None  # config.triage_agent_id
    repository_id: str | None  # config.workflow_repository_id 또는 자동 매칭 결과
    reason: str | None  # 판단할 수 없는 이유. None 이면 시작 가능


@dataclass(frozen=True)
class TriageStart:
    started: bool
    triage_id: str | None
    reason: str | None  # 시작하지 않은 이유 — 버튼 응답·패널 문구


def _triage_spec(conn: Connection, session_id: str) -> KindSpec:
    return next(s for s in repo.list_kinds(conn, session_id) if is_triage_kind(s))


def _pool(conn: Connection, session_id: str) -> list[Candidate]:
    return [Candidate(agent_id=a["agent_id"],
                      capabilities=tuple(Capability.model_validate(c) for c in json.loads(a["capabilities_json"])))
            for a in repo.list_session_agents(conn, session_id)]


def _required(spec: KindSpec, repository_id: str) -> Capability:
    return Capability(code=spec.capability_code, scope={spec.scope_key: repository_id})


def triage_route(conn: Connection, work: Row, *, now: str, settings: Settings) -> TriageRoute:
    """업무 하나의 판단 경로와 판단할 수 없는 이유(`REASONS` 순서대로 첫 해당)."""
    session_id = work["session_id"]

    def stop(key: str, **found) -> TriageRoute:
        values = {"stage": None, "config": None, "agent_id": None, "repository_id": None} | found
        return TriageRoute(**values, reason=REASONS[key])

    if (work["status"] != "새로 들어옴" or work["assignee_type"] is not None or work["direct_member_id"] is not None
            or work["closed_at"] is not None):
        return stop("not_new")
    if work["source_type"] not in ("github", "jira"):
        return stop("no_repository")
    stage = repo.open_stage(conn, work["work_item_id"])
    if stage is None:
        return stop("no_stage")
    origin = task_cycle.origin(conn, stage)
    config = origin.config
    if config is None:
        return stop("no_repository", stage=stage)
    if origin.state == "closed":
        return stop("origin_closed", stage=stage, config=config)
    agent_id = config.triage_agent_id
    if agent_id is None:
        return stop("no_agent", stage=stage, config=config)
    repository_id = (config.workflow_repository_id
                     or task_cycle.match_for_source(conn, session_id, config).workflow_repository_id)
    found = {"stage": stage, "config": config, "agent_id": agent_id, "repository_id": repository_id}
    if repository_id is None:
        return stop("no_runner_repository", **found)
    agent = repo.get_agent(conn, agent_id)
    if (agent is None or not repo.is_session_agent(conn, session_id, agent_id) or agent["connection_type"] != "local"
            or agent["connector_id"] is None):
        return stop("agent_missing", **found)
    spec = _triage_spec(conn, session_id)
    record = select_agent(stage["task_id"], _required(spec, repository_id), _pool(conn, session_id), mode="manual",
                          chosen_agent_id=agent_id)
    if record.status != "selected":
        return stop("no_capability", **found)
    if agent["delegation_policy"] != "run":
        return stop("owner_approval", **found)
    if not views.agent_online(agent, now=now, settings=settings):
        return stop("offline", **found)
    connector = repo.get_connector(conn, agent["connector_id"])
    declared = json.loads(connector["supported_kinds_json"]) if connector["supported_kinds_json"] else []
    if spec.kind not in declared:
        return stop("runner_outdated", **found)
    if agent["base_commit"] is None:
        return stop("no_base_commit", **found)
    if conn.execute("SELECT 1 FROM triage_logs WHERE work_item_id = ? AND state = 'running'",
                    (work["work_item_id"],)).fetchone() is not None:
        return stop("running", **found)
    return TriageRoute(**found, reason=None)


def build_candidates(conn: Connection, work: Row, route: TriageRoute, *, now: str, settings: Settings) -> TriageCandidates:
    """후보 목록 — 시작할 수 있는 종류, 활성 멤버, 열린 단계를 맡을 수 있는 Agent(켜짐·진행 중 업무 수), 같은 저장소의
    끝나지 않은 업무. 지금 종류의 요구 능력은 열린 단계의 것 — 소스가 저장소를 자동 매칭하면 그 저장소로 본다
    (준비 판정 `_match_facts` 와 같다)."""
    session_id = work["session_id"]
    stage, config = route.stage, route.config
    required = Capability.model_validate_json(stage["required_capability_json"])
    if config.workflow_repository_id is None:
        (key,) = required.scope
        required = Capability(code=required.code, scope={key: route.repository_id})
    counts = repo.open_work_counts(conn, session_id)
    members = [TriageMemberCandidate(member_id=m["member_id"], display_name=m["display_name"],
                                     open_work=counts.get(("member", m["member_id"]), 0))
               for m in repo.list_members(conn, session_id) if m["disabled_at"] is None]
    agents = [triage.AgentInfo(
        agent_id=a["agent_id"], name=a["name"], owner_name=owner_approval.owner_name(conn, session_id, a["agent_id"]),
        online=views.agent_online(a, now=now, settings=settings), open_work=counts.get(("agent", a["agent_id"]), 0),
        capabilities=tuple(Capability.model_validate(c) for c in json.loads(a["capabilities_json"])),
    ) for a in repo.list_session_agents(conn, session_id)]
    predecessors = [TriagePredecessorCandidate(work_key=format_work_key(w["key_number"]), title=w["title"],
                                               status=w["status"])
                    for w in repo.open_works_in_repository(conn, session_id, config.source_id,
                                                           exclude_work_item_id=work["work_item_id"],
                                                           limit=triage.CANDIDATES_MAX)]
    return triage.assemble_candidates(
        specs=repo.list_kinds(conn, session_id), current_kind=stage["kind"], current_required=required,
        repository_id=route.repository_id, members=members, agents=agents, predecessors=predecessors,
        work_key=format_work_key(work["key_number"]),
    )


def request_triage(conn: Connection, settings: Settings, *, session_id: str, work_item_id: str,
                   trigger: Literal["auto", "manual"], member_id: str | None, now: str) -> TriageStart:
    """판단을 시작한다 — 이유가 있으면 아무것도 만들지 않고 이유를 돌려준다. 이미 도는 판단이 있으면 `판단 중`."""
    work = repo.get_work_item(conn, session_id, work_item_id)
    route = triage_route(conn, work, now=now, settings=settings)
    if route.reason is not None:
        return TriageStart(False, None, route.reason)
    spec = _triage_spec(conn, session_id)
    agent = repo.get_agent(conn, route.agent_id)
    candidates = build_candidates(conn, work, route, now=now, settings=settings)
    criteria = repo.current_triage_criteria(conn, session_id)
    kinds = [k.kind for k in candidates.kinds]
    evidence = triage.log_evidence(repo.triage_history(conn, session_id, kinds, limit=triage.HISTORY_LIMIT), kinds)
    form = json.loads(work["form_json"])
    text = triage.compose_triage_request(
        work_key=format_work_key(work["key_number"]), title=work["title"],
        origin_key=task_cycle.origin(conn, route.stage).origin_key, criteria_version=criteria["version"],
        criteria_body=criteria["body"],
        form_fields=[(FORM_LABELS[key], form[key]["value"]) for key in FORM_HEADINGS if key in form],
        request=work["request"], candidates=candidates, evidence=evidence,
    )
    task_id = f"task-{uuid4().hex[:12]}"
    required = _required(spec, route.repository_id)
    target = {"local_registration_id": agent["local_registration_id"], "base_commit": agent["base_commit"]}
    request = ExecutionRequest.model_validate({
        "contract_version": 1, "execution_id": f"exec-{token_hex(8)}", "task_id": task_id, "kind": spec.kind,
        "agent_id": agent["agent_id"], "task_revision": 1, "request": text, "input_artifact_ids": [],
        "target": target, "kind_spec": spec.model_dump(),
    })
    task = {
        "task_id": task_id, "session_id": session_id, "title": "판단", "request": text, "kind": spec.kind,
        "required_capability": required.model_dump(), "selection_mode": "manual", "chosen_agent_id": agent["agent_id"],
        "run_mode": "auto", "completion_mode": "review", "criteria": [], "predecessor_task_id": None, "revision": 1,
        "target": target, "status": "실행 요청됨", "status_reason": "판단 접수 대기",
    }
    selection = select_agent(task_id, required, _pool(conn, session_id), mode="manual",
                             chosen_agent_id=agent["agent_id"])
    try:
        triage_id = repo.start_triage(
            conn, session_id=session_id, work_item_id=work_item_id, work_revision=work["revision"], task=task,
            selection=selection, request=request, agent_id=agent["agent_id"], connector_id=agent["connector_id"],
            trigger=trigger, member_id=member_id, criteria_version=criteria["version"],
            input_sha256=triage.input_sha256(text), candidates=candidates, now=now,
        )
    except TriageRunning:
        return TriageStart(False, None, REASONS["running"])
    return TriageStart(True, triage_id, None)
