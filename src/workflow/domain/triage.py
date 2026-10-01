"""판단 순수 규칙 — ARCHITECTURE "판단 — phase 19" 의 "요청문·후보·근거"·"결과 판정"·"업무 상태"·"자동 시작", ADR-0025.

DB·HTTP·프로세스를 보지 않는다. 호출자(`server/triage_runs.py`·워커)가 재료를 모아 넘긴다.
업무 글·기준 본문·후보 이름은 외부 입력이지만 문자열로만 이어 붙인다 — 명령·경로로 해석하지 않는다.
판단 결과의 종류·담당·선행은 여기의 `validate` 로 시작할 때 고정한 후보 안에서만 받는다.
"""

import hashlib
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from statistics import median
from typing import Literal

from workflow.contracts.v1 import (
    TRIAGE_CANDIDATES_MAX,
    Capability,
    KindSpec,
    TriageAgentCandidate,
    TriageCandidates,
    TriageKindCandidate,
    TriageMemberCandidate,
    TriagePredecessorCandidate,
    TriageResult,
)
from workflow.domain.execution_policy import is_triage_kind
from workflow.domain.form_sections import NO_RESPONSE
from workflow.domain.handoff_context import FORM_VALUE_MAX

HISTORY_LIMIT = 20  # 로그 근거 — 종류마다 최근 끝난 업무 수
CANDIDATES_MAX = TRIAGE_CANDIDATES_MAX

PROCEED_LABELS = {"ready": "맡겨도 됨", "needs_check": "확인 필요", "unsuitable": "부적합"}
CRITERION_LABELS = {
    "clarity": "명확성", "verifiability": "검증 가능성", "scope": "범위", "risk": "위험", "permission": "권한",
    "history": "과거 유사 결과", "dependency": "선행 의존",
}
FAILED_LABELS = {
    "triage_invalid": "후보 밖 제안", "usage_limit": "사용량 한도", "timeout": "시간 초과",
    "triage_deadline": "시간 초과", "readonly_violation": "읽기 전용 위반", "result_invalid": "결과 형식 오류",
    "commit_missing": "커밋 없음", "unknown": "시작 여부 불명",
}  # 그 밖 = "실행 실패"

AUTOSTART_MIN_HANDLED = 20
AUTOSTART_DEFAULT_THRESHOLD = 0.8
AUTOSTART_THRESHOLD_RANGE = (0.5, 1.0)

_CUT = "…(생략)"


# ── 후보 ───────────────────────────────────────────────────────────────


def startable_kinds(specs: Sequence[KindSpec], *, current_kind: str) -> list[KindSpec]:
    """업무를 시작할 수 있는 종류 — 판단 종류·입력이 필요한 종류 제외, 저장소 범위가 아니면 지금 종류일 때만. 등록부 순서."""
    return [s for s in specs
            if not is_triage_kind(s) and not s.input_kinds
            and (s.scope_key == "repository_id" or s.kind == current_kind)]


@dataclass(frozen=True)
class AgentInfo:
    """후보 Agent 재료 — 맡을 수 있는 종류는 `assemble_candidates` 가 능력으로 계산한다."""

    agent_id: str
    name: str
    owner_name: str | None
    online: bool
    open_work: int
    capabilities: tuple[Capability, ...]


def assemble_candidates(*, specs: Sequence[KindSpec], current_kind: str, current_required: Capability,
                        repository_id: str, members: Sequence[TriageMemberCandidate], agents: Sequence[AgentInfo],
                        predecessors: Sequence[TriagePredecessorCandidate], work_key: str) -> TriageCandidates:
    """후보 목록. 종류 = `startable_kinds`. Agent = 후보 종류 중 하나라도 요구 능력(지금 종류는 열린 단계의 요구 능력,
    나머지는 `{scope_key: repository_id}`)과 똑같은 능력이 있는 것. 선행 = 자신 제외. Agent·선행은 `CANDIDATES_MAX` 건까지."""
    kinds = startable_kinds(specs, current_kind=current_kind)

    def required(spec: KindSpec) -> Capability:
        if spec.kind == current_kind:
            return current_required
        return Capability(code=spec.capability_code, scope={spec.scope_key: repository_id})

    needs = [(spec.kind, required(spec)) for spec in kinds]
    agent_rows = []
    for agent in agents:
        able = [kind for kind, need in needs if need in agent.capabilities]
        if able:
            agent_rows.append(TriageAgentCandidate(
                agent_id=agent.agent_id, name=agent.name, owner_name=agent.owner_name, online=agent.online,
                open_work=agent.open_work, kinds=able,
            ))
    return TriageCandidates(
        current_kind=current_kind,
        kinds=[TriageKindCandidate(kind=s.kind, label=s.label) for s in kinds],
        members=list(members),
        agents=agent_rows[:CANDIDATES_MAX],
        predecessors=[p for p in predecessors if p.work_key != work_key][:CANDIDATES_MAX],
    )


# ── 로그 근거 ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class PastWork:
    kind: str
    status: str  # 업무 상태
    created_at: str
    closed_at: str | None
    attempts: int  # 그 업무에서 `kind` 단계(판단 제외)의 실행 수 — 검증만 다시 제외


@dataclass(frozen=True)
class KindEvidence:
    kind: str
    n: int
    first_pass: int  # 완료 · 실행 1회
    rework: int  # 실행 2회 이상
    median_seconds: int | None  # 완료 업무 접수 → 끝 중앙값


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def log_evidence(history: Sequence[PastWork], kinds: Sequence[str], *,
                 limit: int = HISTORY_LIMIT) -> tuple[KindEvidence, ...]:
    """종류마다 끝난(`closed_at` 있음) 업무를 최신순 `limit` 건 보고 센다."""
    out = []
    for kind in kinds:
        done = sorted((w for w in history if w.kind == kind and w.closed_at is not None),
                      key=lambda w: w.closed_at, reverse=True)[:limit]
        seconds = [(_parse(w.closed_at) - _parse(w.created_at)).total_seconds() for w in done if w.status == "완료"]
        out.append(KindEvidence(
            kind=kind, n=len(done),
            first_pass=sum(1 for w in done if w.status == "완료" and w.attempts == 1),
            rework=sum(1 for w in done if w.attempts >= 2),
            median_seconds=round(median(seconds)) if seconds else None,
        ))
    return tuple(out)


def _duration(seconds: int) -> str:
    minutes = seconds // 60
    hours, minutes = divmod(minutes, 60)
    if hours and minutes:
        return f"{hours}시간 {minutes}분"
    if hours:
        return f"{hours}시간"
    return f"{minutes}분"


# ── 요청문·해시 ─────────────────────────────────────────────────────────


def _line(text: str) -> str:
    """한 줄로 — 제목·이름의 줄바꿈이 절 머리를 만들지 않게."""
    return " ".join(text.split())


def _fenced(text: str) -> str:
    """외부 글을 그대로 담는 펜스 — 글 안의 가장 긴 backtick 줄보다 길게 해서 글이 절을 닫거나 열지 못한다."""
    longest = max((len(m) for m in re.findall(r"`+", text)), default=0)
    fence = "`" * max(3, longest + 1)
    return f"{fence}\n{text}\n{fence}"


def compose_triage_request(*, work_key: str, title: str, origin_key: str | None, criteria_version: int,
                           criteria_body: str, form_fields: Sequence[tuple[str, str]], request: str,
                           candidates: TriageCandidates, evidence: Sequence[KindEvidence]) -> str:
    """`# 판단: <키> <제목>` → 판단 기준 → 업무 → 후보 → 로그 근거 → 답하는 법. 빈 절은 쓰지 않는다.
    업무 양식·요청 원문은 펜스 안에 그대로 넣는다(그 안의 `## 후보` 같은 줄이 절 경계를 흐리지 않게)."""
    head = f"# 판단: {work_key} {_line(title)}" + (f"\n원본: {_line(origin_key)}" if origin_key else "")
    sections = [head, f"## 판단 기준 (v{criteria_version})\n{criteria_body.strip()}"]

    labels = {k.kind: k.label for k in candidates.kinds}
    work = [f"## 업무\n지금 종류: {candidates.current_kind} ({labels[candidates.current_kind]})"]
    for label, value in form_fields:
        value = value.strip()
        if value and value != NO_RESPONSE:
            value = value[:FORM_VALUE_MAX] + _CUT if len(value) > FORM_VALUE_MAX else value
            work.append(f"### {label}\n{_fenced(value)}")
    if request.strip():
        work.append(f"### 요청\n{_fenced(request.strip())}")
    sections.append("\n".join(work))

    cand = ["## 후보", "### 종류", *[f"- {k.kind} — {_line(k.label)}" for k in candidates.kinds]]
    people = [f"- member:{m.member_id} {_line(m.display_name)} — 진행 중 {m.open_work}" for m in candidates.members]
    for a in candidates.agents:
        parts = ([f"소유 {_line(a.owner_name)}"] if a.owner_name else []) + [
            "켜짐" if a.online else "꺼짐", f"진행 중 {a.open_work}", "맡을 수 있는 종류 " + ", ".join(a.kinds)]
        people.append(f"- agent:{a.agent_id} {_line(a.name)} — " + " · ".join(parts))
    if people:
        cand += ["### 담당", *people]
    if candidates.predecessors:
        cand += ["### 선행 후보",
                 *[f"- {p.work_key} {_line(p.title)} ({_line(p.status)})" for p in candidates.predecessors]]
    sections.append("\n".join(cand))

    if evidence:
        lines = ["## 로그 근거 (Runloom 계산)"]
        for ev in evidence:
            if ev.n == 0:
                lines.append(f"- {ev.kind} 기록 없음")
                continue
            line = f"- {ev.kind} 최근 {ev.n}건: 1회 통과 {ev.first_pass}건 · 재작업 {ev.rework}건"
            if ev.median_seconds is not None:
                line += f" · 완료까지 중앙 {_duration(ev.median_seconds)}"
            lines.append(line)
        sections.append("\n".join(lines))

    sections.append("\n".join([
        "## 답하는 법",
        "- proposed_kind·assignee·predecessors 는 위 후보 안의 값만 쓴다. 후보 밖 값은 판단 실패로 기록된다.",
        "- 담당은 type(member|agent)과 id 로 쓴다.",
        "- 저장소 코드는 현재 폴더에서 읽기만 한다.",
    ]))
    return "\n\n".join("\n".join(line.rstrip() for line in s.split("\n")) for s in sections)


def input_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ── 결과 판정 ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class TriageVerdict:
    ok: bool
    code: str | None  # ARCHITECTURE "결과 판정" 하위 사유
    reason: str


def validate(result: TriageResult, candidates: TriageCandidates, *, execution_id: str, task_id: str,
             base_commit: str) -> TriageVerdict:
    """위에서부터 첫 실패. 통과면 `TriageVerdict(True, None, "")`."""
    if result.execution_id != execution_id or result.task_id != task_id:
        return TriageVerdict(False, "ids_mismatch",
                             f"요청 {execution_id}/{task_id} ≠ 결과 {result.execution_id}/{result.task_id}")
    if result.inspected_commit != base_commit:
        return TriageVerdict(False, "commit_mismatch", f"기준 {base_commit} ≠ 읽은 커밋 {result.inspected_commit}")
    kinds = {k.kind for k in candidates.kinds}
    if result.proposed_kind is not None and result.proposed_kind not in kinds:
        return TriageVerdict(False, "kind_not_candidate", f"후보 밖 종류 {result.proposed_kind}")
    if result.assignee is not None:
        who = f"{result.assignee.type}:{result.assignee.id}"
        if result.assignee.type == "member":
            if result.assignee.id not in {m.member_id for m in candidates.members}:
                return TriageVerdict(False, "assignee_not_candidate", f"후보 밖 담당 {who}")
        else:
            agent = next((a for a in candidates.agents if a.agent_id == result.assignee.id), None)
            if agent is None:
                return TriageVerdict(False, "assignee_not_candidate", f"후보 밖 담당 {who}")
            kind = result.proposed_kind or candidates.current_kind
            if kind not in agent.kinds:
                return TriageVerdict(False, "assignee_cannot_take_kind", f"{who} 는 {kind} 를 맡을 수 없음")
    outside = [key for key in result.predecessors if key not in {p.work_key for p in candidates.predecessors}]
    if outside:
        return TriageVerdict(False, "predecessor_not_candidate", "후보 밖 선행 " + ", ".join(outside))
    return TriageVerdict(True, None, "")


def handling_for(result: TriageResult, *, assignee_type: str, assignee_id: str,
                 kind: str) -> Literal["accepted", "changed"]:
    """사람이 정한 담당·종류가 제안과 같으면 `accepted`. 제안 종류가 없으면 종류는 지금 그대로가 제안이다."""
    same_assignee = result.assignee is not None and (result.assignee.type, result.assignee.id) == (
        assignee_type, assignee_id)
    same_kind = result.proposed_kind is None or result.proposed_kind == kind
    return "accepted" if same_assignee and same_kind else "changed"


# ── 업무 상태 ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class TriageFact:
    state: Literal["running", "proposed", "failed"]
    proceed: str | None  # proposed 일 때
    confidence: float | None  # proposed 일 때
    failed_code: str | None  # failed 일 때


def triage_reason(fact: TriageFact) -> str:
    """`새로 들어옴` 의 이유 — `판단 중`·`판단 제안 · 맡겨도 됨 0.86`·`판단 실패 · 사용량 한도`."""
    if fact.state == "running":
        return "판단 중"
    if fact.state == "proposed":
        return f"판단 제안 · {PROCEED_LABELS[fact.proceed]} {fact.confidence:.2f}"
    return f"판단 실패 · {FAILED_LABELS.get(fact.failed_code, '실행 실패')}"


# ── 자동 시작 ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class AutostartSetting:
    kind: str
    version: int  # 0 = 행 없음(기본 꺼짐)
    enabled: bool
    threshold: float


def can_enable_autostart(handled_count: int) -> bool:
    return handled_count >= AUTOSTART_MIN_HANDLED


def should_autostart(*, proceed: str, assignee_type: str | None, confidence: float,
                     setting: AutostartSetting | None, handled_count: int) -> bool:
    return (setting is not None and setting.enabled and can_enable_autostart(handled_count)
            and proceed == "ready" and assignee_type == "agent" and confidence >= setting.threshold)


_THRESHOLD = re.compile(r"\d(\.\d{1,2})?")


def parse_threshold(value: str) -> float:
    """`0.50`~`1.00`, 소수 둘째 자리까지. 아니면 ValueError."""
    if not _THRESHOLD.fullmatch(value):
        raise ValueError("기준값은 0.50~1.00, 소수 둘째 자리까지입니다")
    number = float(value)
    low, high = AUTOSTART_THRESHOLD_RANGE
    if not low <= number <= high:
        raise ValueError("기준값은 0.50~1.00, 소수 둘째 자리까지입니다")
    return number
