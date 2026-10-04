"""결과 뒤 판단 순수 규칙 — ARCHITECTURE "결과 뒤 판단 — phase 22" 의 "요청문·후보·판정 순수 규칙", ADR-0027.

DB·HTTP·프로세스·시각을 보지 않는다. 호출자(`server/next_step_runs.py`·워커·`work_actions`)가 재료를 모아 넘긴다.
업무 글·결과 요약·사람 응답·요청 목적·반환 요약은 외부 입력이지만 펜스 안 문자열로만 이어 붙인다 — 명령·경로로
해석하지 않는다. 판단 결과의 종류·담당·담당 범위는 여기의 `validate_next_step` 으로 시작할 때 고정한 후보 안에서만
받는다. 판단은 제안만 한다 — 업무 완료·종료 행동은 없다.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from workflow.contracts.v1 import (
    RESPONSIBILITY_CANDIDATES_MAX,
    Capability,
    KindSpec,
    NextAction,
    TriageCandidates,
    TriageCause,
    TriageKindCandidate,
    TriageMemberCandidate,
    TriageResponsibilityCandidate,
    TriageResult,
)
from workflow.domain.execution_policy import is_triage_kind
from workflow.domain.triage import (
    CRITERION_LABELS,
    FAILED_LABELS,
    AgentInfo,
    KindEvidence,
    TriageVerdict,
    agent_candidates,
    clip,
    evidence_section,
    fenced,
    join_sections,
    one_line,
    people_lines,
    work_lines,
)

NEXT_STEP_WAIT_SECONDS = 3600  # 원인 나이 상한 — 넘으면 대체 경로
NEEDS_INFORMATION_CODES = ("fix_needs_information", "review_needs_information")
NEXT_STEP_HUMAN_CODE = "next_step_human"
PRIOR_TEXT_MAX = 2000
RESPONSE_TEXT_MAX = 1000
RESPONSES_MAX = 3
ACTION_LABELS = {"stage": "다음 단계", "rework": "재작업", "new_work": "새 업무", "internal_request": "사내 요청",
                 "human": "사람 확인"}
STAGE_DONE_REASON = "다음 단계로 넘김"
WORK_DONE_REASON = "새 업무로 넘김"

_VERDICT_LABELS = {"passed": "통과", "failed": "실패"}
_ACTION_LINE_QUESTION_MAX = 80


# ── 처분 ───────────────────────────────────────────────────────────────


def disposition(*, state: str | None, handling: str | None, route_reason: str | None,
                cause_age_seconds: float) -> Literal["queue", "hold", "fallback"]:
    """원인 하나를 어떻게 할지 — 그 원인의 판단 행(`state`·`handling`), 시작 조건 이유, 원인 나이로."""
    if state == "running":
        return "hold"
    if state == "proposed" and handling in (None, "accepted"):
        return "hold"
    if state is not None:
        return "fallback"  # 무시·실패·대체됨
    if route_reason is not None or cause_age_seconds > NEXT_STEP_WAIT_SECONDS:
        return "fallback"
    return "queue"


# ── 후보 ───────────────────────────────────────────────────────────────


def _startable(spec: KindSpec) -> bool:
    return not is_triage_kind(spec) and not spec.input_kinds and spec.scope_key == "repository_id"


def next_step_kinds(specs: Sequence[KindSpec], *, current_kind: str) -> list[TriageKindCandidate]:
    """등록부 순서로 지금 종류(재작업용) + 새 단계·새 업무로 시작할 수 있는 종류."""
    return [TriageKindCandidate(kind=s.kind, label=s.label, startable=_startable(s))
            for s in specs if not is_triage_kind(s) and (s.kind == current_kind or _startable(s))]


def assemble_next_step_candidates(*, specs: Sequence[KindSpec], cause: TriageCause, current_required: Capability,
                                  repository_id: str, members: Sequence[TriageMemberCandidate],
                                  agents: Sequence[AgentInfo],
                                  responsibilities: Sequence[TriageResponsibilityCandidate]) -> TriageCandidates:
    """후보 목록 — 지금 종류 = 원인 종류. Agent 규칙은 접수 판단과 같다. 선행 없음, 담당 범위는 상한까지."""
    kinds = next_step_kinds(specs, current_kind=cause.kind)
    names = {k.kind for k in kinds}
    return TriageCandidates(
        current_kind=cause.kind,
        kinds=kinds,
        members=list(members),
        agents=agent_candidates([s for s in specs if s.kind in names], current_kind=cause.kind,
                                current_required=current_required, repository_id=repository_id, agents=agents),
        predecessors=[],
        responsibilities=list(responsibilities)[:RESPONSIBILITY_CANDIDATES_MAX],
        cause=cause,
    )


# ── 요청문 ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class PriorResult:
    """원인 실행의 결과 — 요청문 `## 이전 결과`·새 단계 글에 쓴다."""

    kind: str
    kind_label: str
    outcome: str
    verdict: Literal["passed", "failed"]
    summary: str
    missing_information: tuple[str, ...] = ()


@dataclass(frozen=True)
class ResponseNote:
    at: str
    member_name: str | None
    text: str


@dataclass(frozen=True)
class ReturnedRequest:
    system_id: str
    request_kind: str
    recipient_name: str
    purpose: str
    summary: str
    returned_at: str


def _require_cause(candidates: TriageCandidates) -> TriageCause:
    if candidates.cause is None:
        raise ValueError("결과 뒤 판단 후보에는 cause 가 있어야 합니다")
    return candidates.cause


def compose_next_step_request(*, work_key: str, title: str, origin_key: str | None, criteria_version: int,
                              criteria_body: str, form_fields: Sequence[tuple[str, str]], request: str,
                              candidates: TriageCandidates, evidence: Sequence[KindEvidence], prior: PriorResult,
                              responses: Sequence[ResponseNote], returned: ReturnedRequest | None) -> str:
    """`# 다음 단계 판단: <키> <제목>` → 판단 기준 → 업무 → 이전 결과 → 사람 응답 → 반환된 사내 요청 → 후보 →
    로그 근거 → 답하는 법. 빈 절은 쓰지 않는다. `responses` 는 오래된 순 — 마지막 `RESPONSES_MAX` 개만."""
    cause = _require_cause(candidates)
    head = f"# 다음 단계 판단: {work_key} {one_line(title)}" + (f"\n원본: {one_line(origin_key)}" if origin_key else "")
    sections = [head, f"## 판단 기준 (v{criteria_version})\n{criteria_body.strip()}"]

    labels = {k.kind: k.label for k in candidates.kinds}
    current = f"{candidates.current_kind} ({labels[candidates.current_kind]})"
    sections.append("\n".join([f"## 업무\n지금 단계: {current}", *work_lines(form_fields, request)]))

    verdict = _VERDICT_LABELS[prior.verdict]
    before = ["## 이전 결과", f"- 단계: {current} · 결과 {prior.outcome} · 판정 {verdict}",
              f"### 결과 요약\n{fenced(clip(prior.summary, PRIOR_TEXT_MAX))}"]
    if prior.missing_information:
        before += ["### 모자란 정보", *[f"- {one_line(m)}" for m in prior.missing_information]]
    sections.append("\n".join(before))

    if responses:
        notes = ["## 사람 응답"]
        for r in responses[-RESPONSES_MAX:]:
            notes.append(f"- {r.at}" + (f" {one_line(r.member_name)}" if r.member_name else ""))
            notes.append(fenced(clip(r.text, RESPONSE_TEXT_MAX)))
        sections.append("\n".join(notes))

    if returned is not None:
        sections.append("\n".join([
            "## 반환된 사내 요청",
            f"- {returned.system_id}/{returned.request_kind} → {one_line(returned.recipient_name)}"
            f" · 반환 {returned.returned_at}",
            f"### 요청 목적\n{fenced(returned.purpose)}",
            f"### 반환 요약\n{fenced(returned.summary)}",
        ]))

    cand = ["## 후보", "### 다음 단계 종류"]
    for k in candidates.kinds:
        if k.kind == candidates.current_kind:
            cand.append(f"- {k.kind} — {one_line(k.label)} (지금 단계 — 재작업만)")
        elif k.startable:
            cand.append(f"- {k.kind} — {one_line(k.label)}")
    if people := people_lines(candidates):
        cand += ["### 담당", *people]
    if candidates.responsibilities:
        cand.append("### 사내 요청 담당 범위")
        for r in candidates.responsibilities:
            agent = "조사 에이전트 있음" if r.agent_id else "조사 에이전트 없음"
            cand.append(f"- {r.system_id}/{r.request_kind} → member:{r.recipient_member_id} {one_line(r.recipient_name)}"
                        f" (판단 담당 member:{r.judgment_member_id} · {agent})")
    sections.append("\n".join(cand))

    if (logs := evidence_section(evidence)) is not None:
        sections.append(logs)

    sections.append("\n".join([
        "## 답하는 법",
        "- next_action 하나만 고른다: stage(같은 업무의 다음 단계 — rework=true 면 지금 단계 재작업) · new_work(새 업무)"
        " · internal_request(위 담당 범위의 사람에게 사내 요청) · human(사람에게 확인).",
        "- 업무를 완료·종료하는 행동은 없다. 끝났다고 보면 human 으로 사람에게 확인을 묻는다.",
        "- kind·assignee·system_id·request_kind·recipient_member_id 는 위 후보 안의 값만 쓴다. 후보 밖 값은 판단 실패로 기록된다.",
        f"- 재작업(rework=true)은 지금 단계 종류와 지금 단계의 에이전트(agent:{cause.agent_id})로만 쓴다."
        " 새 단계·새 업무에는 '지금 단계 — 재작업만' 종류를 쓰지 않는다.",
        "- 담당은 type(member|agent)과 id 로 쓴다.",
        "- 저장소 코드는 현재 폴더에서 읽기만 한다.",
    ]))
    return join_sections(sections)


# ── 결과 판정 ──────────────────────────────────────────────────────────


def validate_next_step(result: TriageResult, candidates: TriageCandidates, *, execution_id: str, task_id: str,
                       base_commit: str) -> TriageVerdict:
    """위에서부터 첫 실패. 통과면 `TriageVerdict(True, None, "")`. `human` 은 내용 검사가 없다(길이·공백은 계약)."""
    if result.execution_id != execution_id or result.task_id != task_id:
        return TriageVerdict(False, "ids_mismatch",
                             f"요청 {execution_id}/{task_id} ≠ 결과 {result.execution_id}/{result.task_id}")
    if result.inspected_commit != base_commit:
        return TriageVerdict(False, "commit_mismatch", f"기준 {base_commit} ≠ 읽은 커밋 {result.inspected_commit}")
    action = result.next_action
    if action is None:
        return TriageVerdict(False, "next_action_missing", "결과 뒤 판단 결과에 next_action 이 없음")
    if action.type == "internal_request":
        key = (action.system_id, action.request_kind, action.recipient_member_id)
        if key not in {(r.system_id, r.request_kind, r.recipient_member_id) for r in candidates.responsibilities}:
            return TriageVerdict(False, "responsibility_not_candidate",
                                 f"후보 밖 담당 범위 {action.system_id}/{action.request_kind} → "
                                 f"member:{action.recipient_member_id}")
        return TriageVerdict(True, None, "")
    if action.type == "human":
        return TriageVerdict(True, None, "")

    kinds = {k.kind: k for k in candidates.kinds}
    if action.kind not in kinds:
        return TriageVerdict(False, "kind_not_candidate", f"후보 밖 종류 {action.kind}")
    who = f"{action.assignee.type}:{action.assignee.id}"
    if action.type == "stage" and action.rework:
        cause_agent = candidates.cause.agent_id if candidates.cause is not None else None
        if action.kind != candidates.current_kind or (action.assignee.type, action.assignee.id) != (
                "agent", cause_agent):
            return TriageVerdict(False, "rework_mismatch",
                                 f"재작업은 {candidates.current_kind}·agent:{cause_agent} 만 — 제안 {action.kind}·{who}")
    elif (action.type == "stage" and action.kind == candidates.current_kind) or not kinds[action.kind].startable:
        return TriageVerdict(False, "kind_not_startable", f"새로 시작할 수 없는 종류 {action.kind}")
    if action.assignee.type == "member":
        if action.assignee.id not in {m.member_id for m in candidates.members}:
            return TriageVerdict(False, "assignee_not_candidate", f"후보 밖 담당 {who}")
    else:
        agent = next((a for a in candidates.agents if a.agent_id == action.assignee.id), None)
        if agent is None:
            return TriageVerdict(False, "assignee_not_candidate", f"후보 밖 담당 {who}")
        if action.kind not in agent.kinds:
            return TriageVerdict(False, "assignee_cannot_take_kind", f"{who} 는 {action.kind} 를 맡을 수 없음")
    return TriageVerdict(True, None, "")


# ── 행동·문구 ──────────────────────────────────────────────────────────


def action_label(action: NextAction) -> str:
    if action.type == "stage" and action.rework:
        return ACTION_LABELS["rework"]
    return ACTION_LABELS[action.type]


def action_line(action: NextAction, *, kind_labels: Mapping[str, str], names: Mapping[str, str]) -> str:
    """패널·알림 공용 한 줄. `names` 키는 `member:<id>`·`agent:<id>`, 없으면 id 그대로."""
    if action.type == "internal_request":
        recipient = names.get(f"member:{action.recipient_member_id}", action.recipient_member_id)
        return f"사내 요청 · {action.system_id}/{action.request_kind} → {one_line(recipient)}"
    if action.type == "human":
        first = action.question.split("\n", 1)[0][:_ACTION_LINE_QUESTION_MAX]
        return f"사람 확인 — {first}"
    who = one_line(names.get(f"{action.assignee.type}:{action.assignee.id}", action.assignee.id))
    label = one_line(kind_labels.get(action.kind, action.kind))
    if action.type == "new_work":
        return f"새 업무 「{one_line(action.title)}」 · {label} → {who}"
    if action.rework:
        return f"재작업 → {who}"
    return f"{label} → {who}"


def _reason_lines(result: TriageResult) -> list[str]:
    return [f"- {CRITERION_LABELS[r.criterion]} — {r.note}" for r in result.reasons]


def stage_request_text(*, work_request: str, prior: PriorResult, result: TriageResult) -> str:
    """새 단계·새 업무 Task 의 요청 글 — 원래 요청 + 이전 단계 결과 + 판단 근거."""
    parts = [work_request.strip()] if work_request.strip() else []
    parts.append(f"## 이전 단계 결과 ({prior.kind_label} · {prior.outcome})\n{clip(prior.summary, PRIOR_TEXT_MAX)}")
    parts.append("\n".join(["## 다음 단계 판단 근거", *_reason_lines(result)]))
    return "\n\n".join(parts)


def rework_note(result: TriageResult, *, returned: ReturnedRequest | None) -> str:
    """재작업 지적(`review_comment.comment`) — 판단 근거 + 모자란 정보 + (③) 반환 요약."""
    lines = ["다음 단계 판단: 재작업", *_reason_lines(result),
             *[f"- 모자란 정보: {m}" for m in result.missing_information]]
    text = "\n".join(lines)
    if returned is not None:
        text += (f"\n\n반환된 사내 요청 ({returned.system_id}/{returned.request_kind}, {returned.recipient_name}):\n"
                 f"{returned.summary}")
    return text


# ── 업무 상태 ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class NextStepFact:
    state: Literal["running", "proposed", "request_waiting"]
    action_label: str | None  # proposed 일 때
    recipient_name: str | None  # request_waiting 일 때


def next_step_status(fact: NextStepFact) -> tuple[str, str]:
    """업무 (상태, 이유)."""
    if fact.state == "running":
        return ("에이전트 작업 중", "다음 단계 판단 중")
    if fact.state == "proposed":
        return ("내 차례", f"다음 단계 제안 · {fact.action_label}")
    return ("대기", f"사내 요청 대기 · {fact.recipient_name}")


def next_step_reason(*, state: str, action_label: str | None, failed_code: str | None) -> str:
    """판단 단계 마감 이유 — `proposed` 또는 `failed`."""
    if state == "proposed":
        return f"다음 단계 제안 · {action_label}"
    return f"다음 단계 판단 실패 · {FAILED_LABELS.get(failed_code, '실행 실패')}"
