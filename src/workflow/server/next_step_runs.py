"""결과 뒤 판단 시작 — 원인(규칙 밖 결과·`needs_information`·사내 요청 반환)의 판단 경로·이유, 후보·이전 결과·사람
응답·반환으로 요청문을 만들어 판단 단계·실행·판단 로그를 한 번에 연다(ADR-0027, ARCHITECTURE "결과 뒤 판단 — phase 22"
시작 조건).

- 판단 Agent 검사는 접수 판단과 같다(`triage_runs._agent_route`) — 더해 러너 능력 `after_result_triage` 를 본다. 업무
  조건(`not_new`)·맡길 단계·업무의 `running` 은 보지 않는다(워크스페이스의 `running` 은 워커가, 업무의 `running` 은
  `repo.start_triage` 가 막는다).
- 체크아웃 커밋은 판단 Agent 의 `base_commit`. 이전 결과는 요청문의 글로만 넘긴다.
- 판단은 제안만 한다 — 원인 Task·업무 담당을 바꾸지 않는다. `work_actions`·`worker` 는 import 하지 않는다.
"""

import json
from dataclasses import dataclass
from sqlite3 import Connection
from typing import Literal

from workflow.adapters import repo, responsibility_store
from workflow.adapters.artifact_store import ArtifactStore
from workflow.adapters.errors import NextStepExists, TriageRunning
from workflow.contracts.v1 import (
    TRIAGE_MODE_NEXT_STEP,
    TriageCandidates,
    TriageCause,
    TriageResponsibilityCandidate,
    format_work_key,
)
from workflow.domain import next_step, triage
from workflow.domain.form_sections import FORM_HEADINGS, FORM_LABELS
from workflow.server import task_cycle, triage_runs
from workflow.server.settings import Settings
from workflow.server.triage_runs import REASONS, TriageRoute, TriageStart


@dataclass(frozen=True)
class NextStepCause:
    cause: Literal["after_result", "request_returned"]
    session_id: str
    work_item_id: str
    task_id: str  # 원인 Task
    execution_id: str  # 원인 실행
    request_id: str | None  # request_returned 만
    at: str  # 원인 시각(판정 decided_at 또는 returned_at)
    fallback: Literal["none", "original_request", "next_step_human"]  # 대체 경로 종류


def next_step_route(conn: Connection, cause: NextStepCause, *, now: str, settings: Settings) -> TriageRoute:
    """원인 Task 원본 설정의 판단 Agent 경로와 판단할 수 없는 이유 — `stage` 는 원인 Task."""
    return triage_runs._agent_route(conn, cause.session_id, repo.get_task(conn, cause.task_id), now=now,
                                    settings=settings, need_after_result=True)


def _responsibilities(conn: Connection, session_id: str) -> list[TriageResponsibilityCandidate]:
    """담당표의 활성 항목(`entry_problem` 이 None)만, 표 순서로."""
    out = []
    for entry in responsibility_store.list_entries(conn, session_id):
        if responsibility_store.entry_problem(conn, session_id, entry) is not None:
            continue
        recipient = repo.get_member(conn, session_id, entry.recipient_member_id)
        out.append(TriageResponsibilityCandidate(
            system_id=entry.system_id, request_kind=entry.request_kind, recipient_member_id=entry.recipient_member_id,
            recipient_name=recipient["display_name"], judgment_member_id=entry.judgment_member_id,
            agent_id=entry.agent_id,
        ))
    return out


def _store(settings: Settings) -> ArtifactStore:
    return ArtifactStore(settings.artifact_dir)


def build_next_step_candidates(conn: Connection, cause: NextStepCause, route: TriageRoute, *, now: str,
                               settings: Settings) -> TriageCandidates:
    """후보 — 원인(종류·Agent·outcome), 다음 단계 종류, 활성 멤버, Agent(접수 판단과 같은 규칙), 담당 범위."""
    prior = repo.cause_prior(conn, _store(settings), cause.execution_id)
    members, agents = triage_runs._people(conn, cause.session_id, now=now, settings=settings)
    return next_step.assemble_next_step_candidates(
        specs=repo.list_kinds(conn, cause.session_id),
        cause=TriageCause(cause=cause.cause, execution_id=cause.execution_id, task_id=cause.task_id, kind=prior.kind,
                          agent_id=repo.get_execution(conn, cause.execution_id)["agent_id"], outcome=prior.outcome,
                          request_id=cause.request_id),
        current_required=triage_runs._required_of(route.stage, route), repository_id=route.repository_id,
        members=members, agents=agents, responsibilities=_responsibilities(conn, cause.session_id),
    )


def _responses(conn: Connection, session_id: str, task_id: str) -> list[next_step.ResponseNote]:
    """원인 Task 의 사람 응답 중 글이 있는 것(오래된 순)."""
    notes = []
    for r in repo.list_human_responses(conn, task_id):
        if not r["text"].strip():
            continue
        member = repo.get_member(conn, session_id, r["member_id"]) if r["member_id"] else None
        notes.append(next_step.ResponseNote(at=r["created_at"], member_name=member["display_name"] if member else None,
                                            text=r["text"]))
    return notes


def _returned(conn: Connection, session_id: str, request_id: str) -> next_step.ReturnedRequest:
    row = conn.execute(
        "SELECT r.system_id, r.request_kind, r.purpose, m.display_name AS recipient_name, i.returned_summary,"
        " i.returned_at FROM internal_requests r JOIN members m ON m.member_id = r.recipient_member_id"
        " JOIN internal_request_investigations i ON i.request_id = r.request_id"
        " WHERE r.session_id = ? AND r.request_id = ?", (session_id, request_id),
    ).fetchone()
    return next_step.ReturnedRequest(system_id=row["system_id"], request_kind=row["request_kind"],
                                     recipient_name=row["recipient_name"], purpose=row["purpose"],
                                     summary=row["returned_summary"], returned_at=row["returned_at"])


def request_next_step(conn: Connection, settings: Settings, *, cause: NextStepCause, now: str) -> TriageStart:
    """결과 뒤 판단을 시작한다 — 이유가 있으면 아무것도 만들지 않고 이유를 돌려준다. 업무에 도는 판단이 있거나 같은
    원인의 판단이 이미 있으면 `판단 중`(멱등)."""
    route = next_step_route(conn, cause, now=now, settings=settings)
    if route.reason is not None:
        return TriageStart(False, None, route.reason)
    work = repo.get_work_item(conn, cause.session_id, cause.work_item_id)
    candidates = build_next_step_candidates(conn, cause, route, now=now, settings=settings)
    criteria = repo.current_triage_criteria(conn, cause.session_id)
    kinds = [k.kind for k in candidates.kinds]
    evidence = triage.log_evidence(repo.triage_history(conn, cause.session_id, kinds, limit=triage.HISTORY_LIMIT),
                                   kinds)
    form = json.loads(work["form_json"])
    text = next_step.compose_next_step_request(
        work_key=format_work_key(work["key_number"]), title=work["title"],
        origin_key=task_cycle.origin(conn, route.stage).origin_key, criteria_version=criteria["version"],
        criteria_body=criteria["body"],
        form_fields=[(FORM_LABELS[key], form[key]["value"]) for key in FORM_HEADINGS if key in form],
        request=work["request"], candidates=candidates, evidence=evidence,
        prior=repo.cause_prior(conn, _store(settings), cause.execution_id),
        responses=_responses(conn, cause.session_id, cause.task_id),
        returned=_returned(conn, cause.session_id, cause.request_id) if cause.request_id is not None else None,
    )
    try:
        triage_id = triage_runs._launch(
            conn, work=work, route=route, text=text, candidates=candidates, criteria_version=criteria["version"],
            trigger="auto", member_id=None, status_reason="다음 단계 판단 접수 대기", now=now,
            mode=TRIAGE_MODE_NEXT_STEP, cause=cause.cause, cause_execution_id=cause.execution_id,
            cause_request_id=cause.request_id,
        )
    except (TriageRunning, NextStepExists):
        return TriageStart(False, None, REASONS["running"])
    return TriageStart(True, triage_id, None)
