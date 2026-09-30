"""화면 컨텍스트 조립 — DB 행을 `TaskView`(domain.status) 와 템플릿 컨텍스트로 옮긴다.

판정은 `domain.status.user_status` 가 한다. 여기서는 재료(선택 기록·선행 상태·연결 생존·활성
실행·판정)를 모으고, 비밀값(`credential_ref`·토큰) 은 컨텍스트에 넣지 않는다. 쓰기는 하지 않는다.
"""

import json
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from sqlite3 import Connection, Row
from typing import Any
from urllib.parse import quote, urlsplit
from uuid import uuid4

from pydantic import ValidationError

from workflow.adapters import repo, secret_store
from workflow.adapters.artifact_store import ArtifactStore
from workflow.adapters.errors import ArtifactMissing, NotFound
from workflow.adapters.secret_store import SecretStore
from workflow.contracts.github import GitHubIssueSnapshot, GitHubSourceConfig
from workflow.contracts.v1 import CodeReviewResult, KindSpec, SuccessorRule, format_work_key
from workflow.domain.composition import compose, human_gate_label
from workflow.domain.execution_policy import policy_for
from workflow.domain.form_sections import FORM_HEADINGS
from workflow.domain.kinds import get_kind, kind_for_capability
from workflow.domain.metrics import (
    ACTORS,
    ALL_GROUP,
    UNKNOWN,
    BaselineItemFact,
    MetricsReport,
    Ratio,
    Stat,
    summarize_baseline,
)
from workflow.domain.notification import webhook_host
from workflow.domain import team
from workflow.domain.status import TaskView, UserStatus, user_status
from workflow.domain.task_sources import Issue
from workflow.domain.work_status import STAGE_FAILED
from workflow.server import github_clients, human_api, task_cycle
from workflow.server.filters import KIND_LABELS, duration, kind_label, kst
from workflow.server.settings import Settings

# 결과 봉투로 화면이 파싱하는 산출물 종류 (CONTRACT 5·7·11절)
RESULT_KINDS = ("code_change_result", "generic_result")

# 뷰어가 줄 번호를 붙여 보이는 산출물 종류
LOG_KINDS = (
    "test_log_before",
    "test_log_after",
    "verification_log",
    "codex_stderr",
    "codex_jsonl",
    "claude_stderr",
    "claude_jsonl",
)

# 검증 요약의 두 칸 비교에 보이는 로그 줄 수 (UI_GUIDE "오른쪽 열")
LOG_TAIL = 20

# 세션 화면에 넘기지 않는 agents 컬럼. `credential_ref` 는 참조명이지만 이름만으로도 환경 구성이 드러난다.
_AGENT_PRIVATE = ("credential_ref",)

# callback 중단 횟수 — worker.CALLBACK_MAX_ATTEMPTS 와 같은 값 (worker 가 views 를 import 하므로 여기서 가져오지 않는다)
_CALLBACK_MAX_ATTEMPTS = 5


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts)


def agent_online(agent: Row, *, now: str, settings: Settings) -> bool:
    """로컬: `online` 이고 마지막 heartbeat 가 `heartbeat_offline_seconds` 이내. API: heartbeat 가 없으므로 `online` 이면 연결됨."""
    if agent["connection_state"] != "online":
        return False
    if agent["connection_type"] == "api":
        return True
    if not agent["last_seen_at"]:
        return False
    age = _parse(now) - _parse(agent["last_seen_at"])
    return age <= timedelta(seconds=settings.limits.heartbeat_offline_seconds)


def _found_keys(found: Any, prefix: str = "") -> list[str]:
    """`discovered.found` 의 truthy 항목 키만 (중첩은 `git.head`). 파일 본문·커밋 같은 값은 넣지 않는다."""
    keys: list[str] = []
    if not isinstance(found, dict):
        return keys
    for key, value in found.items():
        path = f"{prefix}{key}"
        if isinstance(value, dict):
            keys.extend(_found_keys(value, f"{path}."))
        elif value:
            keys.append(path)
    return keys


def discovered_summary(agent: dict[str, Any]) -> list[str]:
    """에이전트 카드의 "발견된 정보" 요약. API 는 능력의 역할·자료 범위, 로컬은 발견된 설정 키와 검증 프로필."""
    if agent["connection_type"] == "api":
        return [
            c["code"] + "".join(f" · {k}={v}" for k, v in c["scope"].items()) for c in agent["capabilities"]
        ]
    found = agent["discovered"].get("found") if isinstance(agent["discovered"], dict) else None
    return [*_found_keys(found), *(f"검증 프로필 {p}" for p in agent["verification_profile_ids"])]


def agent_public(agent: Row, *, now: str, settings: Settings, kinds: Sequence[KindSpec] = ()) -> dict[str, Any]:
    """화면용 Agent. JSON 컬럼은 풀고 비밀 참조는 뺀다. 능력마다 `kind_label` — `kinds`(세션 등록부)에 그 코드의
    종류가 있으면 그 라벨, 없으면 None. 코드 값은 그대로 둔다."""
    data = {k: agent[k] for k in agent.keys() if k not in _AGENT_PRIVATE}
    data["capabilities"] = [
        {**c, "kind_label": spec.label if (spec := kind_for_capability(kinds, c["code"])) is not None else None}
        for c in json.loads(data.pop("capabilities_json"))
    ]
    data["verification_profile_ids"] = json.loads(data.pop("verification_profile_ids_json"))
    data["discovered"] = json.loads(data.pop("discovered_json"))
    data["online"] = agent_online(agent, now=now, settings=settings)
    data["discovered_summary"] = discovered_summary(data)
    return data


def kind_public(spec: KindSpec) -> dict[str, Any]:
    """종류·규칙 화면의 종류 카드. 산출물 kind 는 칩 라벨(`KIND_LABELS`)로, outcome 은 코드 그대로 (GLOSSARY `outcome 라벨`)."""
    return {
        "kind": spec.kind,
        "label": spec.label,
        "capability_code": spec.capability_code,
        "scope_key": spec.scope_key,
        "input_kinds": list(spec.input_kinds),
        "input_labels": [kind_label(k) for k in spec.input_kinds],
        "output_kind": spec.output_kind,
        "output_label": kind_label(spec.output_kind),
        "outcomes": list(spec.outcomes),
        "instructions": spec.instructions,
        "builtin": spec.builtin,
    }


PLACEMENT_LABELS = {"same_work": "같은 업무의 다음 단계", "new_work": "새 업무로 등록"}


def rule_public(rule_id: str, rule: SuccessorRule, kinds: Sequence[KindSpec]) -> dict[str, Any]:
    """규칙 한 줄 `{from label} --[outcome, …]--> {to label}` (ADR-0009 — 그래프를 그리지 않는다).
    등록부에 없는 종류는 코드 그대로 보인다."""

    def label(kind: str) -> str:
        spec = get_kind(kinds, kind)
        return spec.label if spec is not None else kind

    return {
        "rule_id": rule_id,
        "from_kind": rule.from_kind,
        "to_kind": rule.to_kind,
        "text": f"{label(rule.from_kind)} --[{', '.join(rule.on_outcomes)}]--> {label(rule.to_kind)}",
        "handoff_kinds": list(rule.handoff_kinds),
        "handoff_labels": [kind_label(k) for k in rule.handoff_kinds],
        "placement": rule.placement,
        "placement_label": PLACEMENT_LABELS[rule.placement],
    }


def summarize_verdict(verdict: dict[str, Any]) -> tuple[str, str]:
    """task_verdicts 의 JSON → (outcome, 화면 한 줄). 통과면 `판정 근거: n/m`, 아니면 미충족 항목 코드."""
    checks = verdict.get("checks", [])
    passed = [c["code"] for c in checks if c.get("passed")]
    failed = [c["code"] for c in checks if not c.get("passed")]
    outcome = verdict["outcome"]
    if outcome == "passed":
        return outcome, f"판정 근거: {len(passed)}/{len(checks)}"
    return outcome, ("미충족: " + ", ".join(failed)) if failed else "판정 불가"


def predecessor_handoff(conn: Connection, task: Row) -> tuple[list[str], str | None]:
    """선행의 준비된 실행(`repo.predecessor_ready_execution` — result_ready + 판정)이 만든 가장 최근 `handoff_bundle` 과
    그 실행 ID. 선행이 없거나, 실패로 마감됐거나, 결과·판정·묶음이 아직 없으면 ([], None) — 묶음은 워커가 조립한다.
    ADR-0009 (3): 선행 Task 의 `완료`(사람 승인)를 기다리지 않는다. 직접 실행(`web._run_task`)과 `can_run` 이 같이 쓴다."""
    if task["predecessor_task_id"] is None:
        return [], None
    predecessor = repo.get_task(conn, task["predecessor_task_id"])
    if predecessor is None or predecessor["status"] == "실패":
        return [], None
    execution = repo.predecessor_ready_execution(conn, predecessor["task_id"])
    if execution is None:
        return [], None
    for artifact in reversed(repo.artifacts_of(conn, execution["execution_id"])):
        if artifact["kind"] == "handoff_bundle":
            return [artifact["artifact_id"]], execution["execution_id"]
    return [], None


def build_task_view(conn: Connection, task: Row, *, now: str, settings: Settings) -> TaskView:
    selection = repo.get_selection(conn, task["task_id"])
    if selection is None:
        selection_status, selection_reason, selected = "needs_selection", "후보 없음", None
    else:
        selection_status, selection_reason, selected = (
            selection.status, selection.reason, selection.selected_agent_id
        )

    predecessor_status = None
    if task["predecessor_task_id"] is not None:
        predecessor = repo.get_task(conn, task["predecessor_task_id"])
        predecessor_status = predecessor["status"] if predecessor is not None else None
        # ADR-0009 (3): 선행 결과가 판정되고 인계 묶음이 준비됐으면 선행 조건이 충족된 것이다 — 선행 `완료` 를
        # 기다리지 않으므로 판정에는 "선행 없음" 으로 넘긴다 (domain.status 는 `완료` 만 통과시킨다).
        if predecessor_handoff(conn, task)[1] is not None:
            predecessor_status = None

    # 연결 상태는 종류 이름이 아니라 선택된 Agent 의 연결 유형으로 본다 — 사용자 정의 종류도 로컬 도구가 수행한다
    connector_online = connector_last_seen = None
    if selected is not None:
        agent = repo.get_agent(conn, selected)
        if agent is not None and agent["connection_type"] == "local":
            connector_online = agent_online(agent, now=now, settings=settings)
            connector_last_seen = kst(agent["last_seen_at"]) if agent["last_seen_at"] else "없음"

    execution = repo.active_execution(conn, task["task_id"])
    execution_status = last_progress = failed_code = failed_message = None
    process_stopped = verdict = verdict_detail = None
    if execution is not None:
        execution_status = execution["status"]
        for event in repo.list_events(conn, execution["execution_id"]):
            if event["type"] == "progress":
                last_progress = json.loads(event["data_json"])["message"]
        failed_code = execution["failed_code"]
        failed_message = execution["failed_message"]
        if execution["process_stopped"] is not None:
            process_stopped = bool(execution["process_stopped"])
        verdict_row = repo.get_verdict(conn, execution["execution_id"])
        if verdict_row is not None:
            verdict, verdict_detail = summarize_verdict(json.loads(verdict_row["verdict_json"]))

    return TaskView(
        kind=task["kind"],
        run_mode=task["run_mode"],
        completion_mode=task["completion_mode"],
        selection_status=selection_status,
        selection_reason=selection_reason,
        selected_agent_id=selected,
        predecessor_status=predecessor_status,
        connector_online=connector_online,
        connector_last_seen=connector_last_seen,
        execution_status=execution_status,
        last_progress=last_progress,
        failed_code=failed_code,
        failed_message=failed_message,
        process_stopped=process_stopped,
        verdict=verdict,
        verdict_detail=verdict_detail,
        review_decision=task["review_decision"],
        finished=task["finished_at"] is not None,
    )


def status_of(task: Row, view: TaskView) -> UserStatus:
    """마감된 Task(`finished_at`) 는 저장된 상태·이유가 기준이다 (검토 마감·워커 자동 완료).
    업무 순환 종류(`ExecutionPolicy.cycle`)도 저장값이다 — 준비 판정·후속 결정을 내린 워커가 쓴 상태라, 선택 기록 기반의
    `user_status` 로 다시 판정하면 대기 사유가 달라진다. 그 밖은 현재 실행·연결 상태로 실시간 판정한다."""
    if task["finished_at"] is not None or policy_for(task["kind"]).cycle:
        return UserStatus(task["status"], task["status_reason"])
    return user_status(view)


def undelegated(conn: Connection, task: Row) -> bool:
    """`intake: all_open` 소스 이슈의 (수정) Task 인데 실행 지시가 없다 — [에이전트에게 맡기기] 대상
    (ARCHITECTURE "실행 지시"). 마감된 Task·검토 Task·`filtered` 소스(수집 = 지시)는 아니다."""
    if task["finished_at"] is not None:
        return False
    issue = repo.get_source_issue_by_task(conn, task["session_id"], task["task_id"])
    if issue is None or issue["delegated_at"] is not None:
        return False
    config = repo.get_github_source(conn, task["session_id"], issue["source_id"])
    return config is not None and config.intake == "all_open"


def task_summary(conn: Connection, task: Row, *, now: str, settings: Settings) -> dict[str, Any]:
    """목록·링크용 요약 (왼쪽 목록, 선행·후속 칩). 지시 전 업무는 "대기 · 지시 전" 하나로 보인다 — 다른 대기 사유는
    상세에서만(ARCHITECTURE "실행 지시")."""
    delegatable = undelegated(conn, task)
    return {
        "task_id": task["task_id"],
        "title": task["title"],
        "kind": task["kind"],
        "created_at": task["created_at"],
        "status": (UserStatus("대기", "지시 전") if delegatable
                   else status_of(task, build_task_view(conn, task, now=now, settings=settings))),
        "delegatable": delegatable,
    }


def _execution_context(conn: Connection, execution: Row, now: str) -> dict[str, Any]:
    """실행 블록 재료. 경과 시간은 `started` 이벤트부터 종료 이벤트(없으면 now)까지다. 시작 전이면 None."""
    data = dict(execution)
    events = [
        {**dict(e), "data": json.loads(e["data_json"])}
        for e in repo.list_events(conn, execution["execution_id"])
    ]
    data["events"] = events
    # 산출물 칩 순서는 UI_GUIDE 의 칩 순서(KIND_LABELS 정의 순). 같은 kind 는 저장 순
    kinds = list(KIND_LABELS)
    data["artifacts"] = sorted(
        (dict(a) for a in repo.artifacts_of(conn, execution["execution_id"])),
        key=lambda a: (kinds.index(a["kind"]) if a["kind"] in kinds else len(kinds), a["created_at"]),
    )
    progress = [e["data"]["message"] for e in events if e["type"] == "progress"]
    data["progress_count"] = len(progress)
    data["last_progress"] = progress[-1] if progress else None
    started = next((e["occurred_at"] for e in events if e["type"] == "started"), None)
    ended = next((e["occurred_at"] for e in reversed(events) if e["type"] in ("result_ready", "failed")), None)
    data["duration_seconds"] = (
        max(0, int((_parse(ended or now) - _parse(started)).total_seconds())) if started else None
    )
    return data


def _result_context(
    conn: Connection, store: ArtifactStore, executions: list[dict[str, Any]]
) -> dict[str, Any] | None:
    """가장 최근 시도의 결과 산출물이 결과 봉투 종류면 파싱해 넘긴다. 깨진 JSON 은 원문 없이 종류만."""
    for execution in reversed(executions):
        artifact_id = execution["result_artifact_id"]
        if artifact_id is None:
            continue
        artifact = repo.get_artifact(conn, artifact_id)
        if artifact is None or artifact["kind"] not in RESULT_KINDS:
            return None
        try:
            data = json.loads(repo.read_artifact(conn, store, artifact_id))
        except (ValueError, ArtifactMissing):
            data = None
        return {
            "kind": artifact["kind"],
            "artifact_id": artifact_id,
            "execution_id": execution["execution_id"],
            "data": data,
        }
    return None


def _chip(conn: Connection, summary: dict[str, Any], stage_ids: list[str]) -> dict[str, Any]:
    """선행·후속 칩 — 같은 업무의 단계면 `단계 N/M`(단계 상세로), 다른 업무면 그 업무 키(업무 상세로)."""
    if summary["task_id"] in stage_ids:
        return {**summary, "stage_label": f"단계 {stage_ids.index(summary['task_id']) + 1}/{len(stage_ids)}"}
    work = repo.work_item_of_task(conn, summary["task_id"])
    return {**summary, "work_key": format_work_key(work["key_number"]) if work else None}


def task_context(
    conn: Connection, store: ArtifactStore, task_row: Row, *, now: str, settings: Settings,
    allowed: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    """업무 상세 템플릿 컨텍스트 전부. 동작 가능 여부(`can_run`·`can_review`·`needs_selection`)도 여기서 정한다.
    업무 순환 Task 는 `cycle`(`cycle_context`)의 준비 판정이 직접 실행 여부를 정하고, 담당은 선택 폼이 아니라
    GitHub 담당 연결·사람 요청 응답으로 바뀐다."""
    task = dict(task_row)
    task["required_capability"] = json.loads(task.pop("required_capability_json"))
    task["criteria"] = json.loads(task.pop("criteria_json"))
    task["target"] = json.loads(task.pop("target_json"))

    view = build_task_view(conn, task_row, now=now, settings=settings)
    status = status_of(task_row, view)
    selection = repo.get_selection(conn, task_row["task_id"])
    agent_row = (
        repo.get_agent(conn, selection.selected_agent_id)
        if selection is not None and selection.selected_agent_id is not None
        else None
    )
    executions = [_execution_context(conn, e, now) for e in repo.list_executions(conn, task_row["task_id"])]
    active = next((e for e in executions if e["released_at"] is None), None)
    result = _result_context(conn, store, executions)
    finished = task_row["finished_at"] is not None
    selected = selection is not None and selection.status == "selected"
    predecessor = (
        task_summary(conn, repo.get_task(conn, task_row["predecessor_task_id"]), now=now, settings=settings)
        if task_row["predecessor_task_id"] is not None
        else None
    )
    work = repo.work_item_of_task(conn, task_row["task_id"])
    stage_ids = [t["task_id"] for t in repo.list_work_item_tasks(conn, work["work_item_id"])] if work else []

    needs_selection = not finished and active is None and not selected
    chain = repo.get_chain(conn, task_row["chain_id"]) if task_row["chain_id"] is not None else None
    spec = repo.get_kind(conn, task_row["session_id"], task_row["kind"])
    cycle = cycle_context(conn, store, task_row, now=now, settings=settings, allowed=allowed)
    cycle_task = cycle is not None and cycle["cycle"]
    if cycle_task:
        needs_selection = False
    return {
        "task": task,
        "view": view,
        "status": status,
        "selection": selection,
        "kind_label": spec.label if spec is not None else task_row["kind"],
        # 입구로 만든 Task 면 브레드크럼에 워크플로우 칩 (phase 5 step 6)
        "chain": {"chain_id": chain["chain_id"], "title": chain["title"]} if chain is not None else None,
        "cycle": cycle,
        "agent": agent_public(agent_row, now=now, settings=settings) if agent_row is not None else None,
        "executions": executions,
        "active_execution": active,
        "result": result,
        "work": {"key": format_work_key(work["key_number"]), "title": work["title"]} if work else None,
        "predecessor": _chip(conn, predecessor, stage_ids) if predecessor is not None else None,
        "successors": [
            _chip(conn, task_summary(conn, s, now=now, settings=settings), stage_ids)
            for s in repo.followups_of(conn, task_row["task_id"])
        ],
        # 선행이 있으면 선행 결과 + 판정 + 인계 묶음이 조건이다 (ADR-0009 (3)) — `web._run_task` 와 같은 판단
        "can_run": cycle["can_start"] if cycle_task else (
            not finished
            and active is None
            and selected
            and (predecessor is None or predecessor_handoff(conn, task_row)[1] is not None)
        ),
        # 열린 사람 요청이 있으면 검토 폼 대신 요청에 답한다 — 같은 결과에 두 갈래 조작을 두지 않는다
        "can_review": (
            not finished
            and active is not None
            and active["status"] == "result_ready"
            and status.label == "확인 필요"
            and not (cycle is not None and cycle["open_requests"])
        ),
        "needs_selection": needs_selection,
        # 후보는 워크스페이스에 붙은 Agent 만. 등록 순서대로
        "candidates": [
            agent_public(a, now=now, settings=settings)
            for a in repo.list_session_agents(conn, task_row["session_id"])
        ] if needs_selection else [],
    }


# --- GitHub 업무 순환 화면 (phase 8 step 13) -------------------------------------------------
#
# 판정은 하지 않는다 — 대기 사유는 워커와 같은 `task_cycle.evaluate`, 응답 가능 동작은 `human_api.allowed_actions`,
# 반영 상태는 `SourceDelivery.state` 그대로다. 표시 라벨에서 실행 여부를 거꾸로 추정하지 않는다.

# 원본 반영 상태 라벨 (ARCHITECTURE "원본 반영 상태") — Task(Agent 작업) 상태와 따로 보인다
DELIVERY_LABELS = {
    "pending": "반영 대기", "sending": "반영 대기", "delivered": "반영됨", "unknown": "반영 불확실", "failed": "반영 실패",
}
# 대기 사유를 풀 주체 (`Blocker.actor`)
ACTOR_LABELS = {"operator": "운영자", "assignee": "GitHub 담당자", "system": "자동 해소 대기"}
# 사람 요청 응답 버튼 (`human_api.Action`) — 표시 순서
RESPONSE_ACTIONS = (
    ("resume", "답하고 다시 판정"), ("choose_agent", "이 Agent 로 지정"), ("retry", "다시 맡기기"), ("close", "업무 종료"),
)
# 실행 실패 요청의 [닫기] — 업무 `종료`(ARCHITECTURE "실패 단계")
_FAILED_CLOSE_LABEL = "닫기"


def issue_url(repository_full_name: str, number: int) -> str:
    """원본 링크는 저장소 이름(계약 패턴 검사)과 번호로 만든다 — 응답의 `html_url` 을 그대로 링크로 쓰지 않는다."""
    return f"https://github.com/{repository_full_name}/issues/{number}"


# 초안 PR 대기열 상태(`task_pull_requests.state`) → 화면 문구
PULL_REQUEST_LABELS = {
    "pending": "PR 여는 중", "open": "사람 차례 · PR 확인", "merged": "PR 병합됨", "closed": "PR 닫힘(병합 없음)",
    "failed": "PR 을 열지 못함",
}


def pull_request_public(row: Row) -> dict[str, Any]:
    """PR 링크는 저장소 이름과 번호로 만든다 — 응답의 `html_url` 을 그대로 링크로 쓰지 않는다(`issue_url` 과 같은 규칙)."""
    number = row["pr_number"]
    return {
        "state": row["state"],
        "label": PULL_REQUEST_LABELS[row["state"]],
        "number": number,
        "url": f"https://github.com/{row['repository_full_name']}/pull/{number}" if number is not None else None,
        "draft": row["draft"] == 1,
        "attempts": row["attempts"],
    }


def delivery_public(delivery: Any, repository_full_name: str) -> dict[str, Any]:
    return {
        "state": delivery.state,
        "label": DELIVERY_LABELS[delivery.state],
        "attempts": delivery.attempts,
        "last_error": delivery.last_error,
        "body_revision": delivery.body_revision,
        "comment_url": (
            f"{issue_url(repository_full_name, delivery.issue_number)}#issuecomment-{delivery.comment_id}"
            if delivery.comment_id is not None else None
        ),
    }


def _origin(conn: Connection, task: Row, issue: Row, config: GitHubSourceConfig | None) -> dict[str, Any]:
    snapshot = GitHubIssueSnapshot.model_validate_json(issue["snapshot_json"])
    bindings = (
        {b.github_user_id: b.agent_id for b in repo.list_assignee_bindings(conn, task["session_id"], issue["source_id"])}
        if config is not None else {}
    )
    return {
        "repository": snapshot.repository_full_name,
        "number": issue["issue_number"],
        "url": issue_url(snapshot.repository_full_name, issue["issue_number"]),
        "title": snapshot.title,
        "state": issue["state"],
        "source_revision": issue["source_revision"],
        "own": issue["task_id"] == task["task_id"],  # 검토 Task 는 수정 Task 의 원본을 따른다
        "assignees": [
            {"login": login, "github_user_id": user_id, "agent_id": bindings.get(user_id)}
            for user_id, login in zip(snapshot.assignee_ids, snapshot.assignee_logins, strict=True)
        ],
        "enabled": config.enabled if config is not None else False,
    }


def _review_result(conn: Connection, store: ArtifactStore, executions: list[Row]) -> dict[str, Any] | None:
    """가장 최근 검토 결과(`CodeReviewResult`)와 그 판정. 깨진 결과는 None — 판정 단계가 확인 필요로 둔다."""
    for execution in reversed(executions):
        if execution["result_artifact_id"] is None:
            continue
        try:
            result = CodeReviewResult.model_validate_json(repo.read_artifact(conn, store, execution["result_artifact_id"]))
        except (ValidationError, ValueError, NotFound, ArtifactMissing):
            return None
        verdict = repo.get_verdict(conn, execution["execution_id"])
        return {
            **result.model_dump(mode="json"),
            "blocking_count": sum(1 for f in result.findings if f.severity == "blocking"),
            "artifact_id": execution["result_artifact_id"],
            "verdict": json.loads(verdict["verdict_json"])["outcome"] if verdict is not None else None,
        }
    return None


def _request_public(
    conn: Connection, request: Row, *, can_respond: bool, agent_choices: list[dict[str, Any]]
) -> dict[str, Any]:
    allowed = human_api.allowed_actions(request["code"])
    data = {k: request[k] for k in ("request_id", "code", "question", "state", "revision", "created_at", "answered_at")}
    data["actions"] = [
        (value, _FAILED_CLOSE_LABEL if value == "close" and request["code"] == STAGE_FAILED else label)
        for value, label in RESPONSE_ACTIONS if value in allowed
    ]
    data["asks_information"] = human_api.asks_information(request["code"])
    data["agent_choices"] = agent_choices if "choose_agent" in allowed else []
    # 응답 폼마다 새 응답 ID — 같은 폼을 두 번 보내면 서버가 한 번만 반영한다(`response_id` 멱등)
    data["response_id"] = f"resp-{uuid4().hex}" if request["state"] == "open" and can_respond else None
    return data


def _agent_choices(conn: Connection, session_id: str, origin: dict[str, Any] | None) -> list[dict[str, Any]]:
    """사람 요청 `choose_agent` 의 후보 — 원본 이슈가 있으면 그 GitHub 담당에 연결된 Agent 만."""
    return [
        {"agent_id": a["agent_id"], "name": a["name"]}
        for a in repo.list_session_agents(conn, session_id)
        if origin is None or a["agent_id"] in {x["agent_id"] for x in origin["assignees"]}
    ]


def cycle_context(
    conn: Connection, store: ArtifactStore, task: Row, *, now: str, settings: Settings, allowed: frozenset[str]
) -> dict[str, Any] | None:
    """업무 상세의 업무 순환 영역 — 원본 링크·담당·대기 사유·사람 요청과 응답(입력 보충)·생성 근거·재시도 횟수·검토 결과·
    원본 반영 상태. 업무 순환 종류도 아니고 원본 이슈도 없으면 None."""
    policy = policy_for(task["kind"])
    issue, config = task_cycle.origin_source(conn, task)
    if not policy.cycle and issue is None:
        return None
    task_id = task["task_id"]
    finished = task["finished_at"] is not None
    active = repo.active_execution(conn, task_id)
    executions = repo.list_executions(conn, task_id)
    origin = _origin(conn, task, issue, config) if issue is not None else None

    blockers: list[dict[str, Any]] = []
    if policy.cycle and not finished and active is None:
        readiness = task_cycle.evaluate(conn, task, now=now, settings=settings)
        blockers = [
            {"code": b.code, "reason": b.reason, "actor": b.actor, "actor_label": ACTOR_LABELS[b.actor]}
            for b in readiness.blockers
        ]
    # 직접 실행 — 준비 판정에서 직접 실행 모드만 남았을 때, 또는 결과 뒤 다음 실행(재작업·응답 후 재개)을 워커가
    # 직접 실행 모드 때문에 멈춰 둔 때(`Worker._write_blocked` 가 그 판정으로 남긴 상태). 착수 여부는 다시 워커 규칙이 정한다
    can_start = policy.cycle and not finished and task["run_mode"] == "manual" and (
        (active is None and [b["code"] for b in blockers] == ["manual_mode"])
        or (active is not None and active["status"] == "result_ready" and task["status"] == "실행 가능")
    )

    agent_choices = _agent_choices(conn, task["session_id"], origin)
    requests = [
        _request_public(conn, r, can_respond=team.RESPOND in allowed, agent_choices=agent_choices)
        for r in repo.list_human_requests(conn, task_id)
    ]
    link = repo.get_followup_link(conn, task_id)
    cause = repo.get_execution(conn, link["cause_execution_id"]) if link is not None else None
    cause_task = repo.get_task(conn, cause["task_id"]) if cause is not None else None

    rework = None
    if policy.cycle and policy.target == "code_change":
        rework = {
            "used": sum(1 for e in executions if e["start_key"].startswith("rework:")),
            "max": task_cycle.max_rework_rounds(conn, task),
        }
    delivery = None
    if origin is not None and origin["own"]:
        deliveries = repo.list_source_deliveries(conn, task_id)
        delivery = delivery_public(deliveries[-1], origin["repository"]) if deliveries else None
    return {
        "cycle": policy.cycle,
        "origin": origin,
        "blockers": blockers,
        "can_start": can_start,
        "can_delegate": team.DELEGATE in allowed and undelegated(conn, task),
        "requests": requests,
        "open_requests": [r for r in requests if r["state"] == "open"],
        "responses": _responses_public(conn, task),
        "followup": {
            "cause_execution_id": link["cause_execution_id"],
            "cause_task_id": cause_task["task_id"] if cause_task is not None else None,
            "cause_task_title": cause_task["title"] if cause_task is not None else None,
            "created_at": link["created_at"],
        } if link is not None else None,
        "attempts": len(executions),
        "rework": rework,
        "review": _review_result(conn, store, executions) if policy.result_kind == "code_review_result" else None,
        "delivery": delivery,
        "pull_request": pull_request_public(pr) if (pr := repo.get_pull_request_row(conn, task_id)) else None,
    }


# --- 업무(WorkItem) 목록·상세 (phase 14 step 9, ADR-0020) ---------------------------------------------
#
# 업무 상태·이유는 워커·repo 가 저장한 값 그대로다(`domain/work_status`). 화면은 다시 판정하지 않는다.

# 양식 칸 키 → 화면 이름 (ARCHITECTURE "양식 칸")
FORM_LABELS = {
    "goal": "목표", "steps_to_reproduce": "재현 절차", "expected_behavior": "기대 동작", "acceptance_criteria": "인수 조건",
}
# 업무 사이 연결(앞 → 뒤)을 이 업무에서 본 이름 — (type, 이 업무가 앞인가)
_LINK_LABELS = {
    ("spawned_from", True): "이어서 생긴 업무", ("spawned_from", False): "원인 업무",
    ("blocks", True): "뒤따르는 업무", ("blocks", False): "선행 업무",
}


def assignee_label(conn: Connection, work: Row) -> str:
    """담당 표시 — 멤버 표시 이름 / 에이전트 이름 / `담당 없음`."""
    if work["assignee_type"] == "member":
        member = next((m for m in repo.list_members(conn, work["session_id"]) if m["member_id"] == work["assignee_id"]),
                      None)
        return member["display_name"] if member is not None else work["assignee_id"]
    if work["assignee_type"] == "agent":
        agent = repo.get_agent(conn, work["assignee_id"])
        return agent["name"] if agent is not None else work["assignee_id"]
    return "담당 없음"


def _member_names(conn: Connection, session_id: str) -> dict[str, str]:
    return {m["member_id"]: m["display_name"] for m in repo.list_members(conn, session_id)}


def _responses_public(conn: Connection, task: Row) -> list[dict[str, Any]]:
    """단계의 응답 기록 — 응답자는 표시 이름(v11 이전 응답은 None)."""
    names = _member_names(conn, task["session_id"])
    return [
        {**{k: r[k] for k in ("question", "action", "text", "agent_id", "created_at", "task_revision")},
         "responder": names.get(r["member_id"]) if r["member_id"] else None}
        for r in repo.list_human_responses(conn, task["task_id"])
    ]


def work_summary(conn: Connection, work: Row) -> dict[str, Any]:
    """목록 한 줄 — 키(원본 키가 있으면 원본 키)·제목·담당·업무 상태·이유·갱신 시각. 첫 단계가 지시 전이면
    [에이전트에게 맡기기] 대상(`delegate_task_id`). `내 차례` 면 받는 사람 표시 이름(`recipients`, 계산값)."""
    stages = repo.list_work_item_tasks(conn, work["work_item_id"])
    first = stages[0] if stages else None
    work_key = format_work_key(work["key_number"])
    recipients: list[str] = []
    if work["status"] == "내 차례":
        names = _member_names(conn, work["session_id"])
        recipients = [names[m] for m in repo.turn_recipients_of(conn, work["work_item_id"])]
    return {
        "work_key": work_key,
        "key": work["source_key"] or work_key,
        "title": work["title"],
        "assignee": assignee_label(conn, work),
        "recipients": recipients,
        "status": UserStatus(work["status"], work["status_reason"]),
        "updated_at": work["updated_at"],
        "delegate_task_id": first["task_id"] if first is not None and undelegated(conn, first) else None,
    }


def work_context(
    conn: Connection, work: Row, *, now: str, settings: Settings, allowed: frozenset[str]
) -> dict[str, Any]:
    """업무 상세 — 머리(키·원본·상태·담당·PR)·단계 목록·양식 칸·연결 업무·열린 사람 요청(응답 폼)."""
    session_id = work["session_id"]
    stages = repo.list_work_item_tasks(conn, work["work_item_id"])
    pull_request = None
    open_requests: list[dict[str, Any]] = []
    responses: list[dict[str, Any]] = []
    stage_views = []
    for stage in stages:
        summary = task_summary(conn, stage, now=now, settings=settings)
        spec = repo.get_kind(conn, session_id, stage["kind"])
        stage_views.append({
            **summary,
            "kind_label": spec.label if spec is not None else stage["kind"],
            "attempts": len(repo.list_executions(conn, stage["task_id"])),
        })
        if (pr := repo.get_pull_request_row(conn, stage["task_id"])) is not None:
            pull_request = pull_request_public(pr)
        responses.extend(_responses_public(conn, stage))
        opened = [r for r in repo.list_human_requests(conn, stage["task_id"]) if r["state"] == "open"]
        if opened:
            issue, config = task_cycle.origin_source(conn, stage)
            origin = _origin(conn, stage, issue, config) if issue is not None else None
            choices = _agent_choices(conn, session_id, origin)
            open_requests.extend(
                {**_request_public(conn, r, can_respond=team.RESPOND in allowed, agent_choices=choices),
                 "task_id": stage["task_id"]}
                for r in opened
            )
    form = json.loads(work["form_json"])
    links = []
    for link in repo.list_work_item_links(conn, work["work_item_id"]):
        ahead = link["from_work_item_id"] == work["work_item_id"]
        other = repo.get_work_item(conn, session_id, link["to_work_item_id" if ahead else "from_work_item_id"])
        if other is None:
            continue
        links.append({
            "label": _LINK_LABELS[(link["type"], ahead)],
            "type": link["type"],
            "key": format_work_key(other["key_number"]),
            "title": other["title"],
            "status": UserStatus(other["status"], other["status_reason"]),
        })
    return {
        "work": {
            **work_summary(conn, work),
            "work_item_id": work["work_item_id"],
            "request": work["request"],
            "source_type": work["source_type"],
            "source_key": work["source_key"],
            "source_url": work["source_url"],
            "created_at": work["created_at"],
            "closed_at": work["closed_at"],
        },
        "stages": stage_views,
        "pull_request": pull_request,
        "form_fields": [
            {"key": key, "label": FORM_LABELS[key], "value": form[key]["value"], "source": form[key]["source"]}
            for key in FORM_HEADINGS if key in form
        ],
        "links": links,
        "open_requests": open_requests,
        "responses": responses,
    }


# 연결 화면의 수집 자격 종류(`github_clients.credential_kind`) — 값은 보이지 않는다
CREDENTIAL_LABELS = {"app": "GitHub App 설치", "pat": "붙여 넣은 토큰", "env": "서버 환경변수 토큰"}


def _saved_app(secrets: SecretStore) -> dict[str, str] | None:
    """저장된 App 의 공개 정보(slug·owner·설치 설정 URL) — 개인 키와 정보가 모두 있을 때만. 비밀 파일은 읽지 않는다."""
    if not secrets.exists(secret_store.GITHUB_APP_PRIVATE_KEY):
        return None
    try:
        info = json.loads(secrets.read(secret_store.GITHUB_APP_INFO) or "")
    except ValueError:
        return None
    if not isinstance(info, dict) or not isinstance(info.get("slug"), str) or not info["slug"]:
        return None
    return {
        "slug": info["slug"],
        "owner_login": str(info.get("owner_login") or ""),
        # [저장소 추가/변경] — GitHub 설치 설정 화면. 돌아오면 state 없는 setup(허용)으로 소스를 맞춘다
        "install_url": f"https://github.com/apps/{quote(info['slug'], safe='')}/installations/new",
    }


def _match_rows(config: GitHubSourceConfig, match: Any) -> list[dict[str, Any]]:
    """저장소 카드의 러너 매칭 줄 — 설정값이 있으면 "설정", 자동 매칭이 정했으면 "자동", 아니면 정해지지 않음."""
    rows = []
    for label, configured, matched in (
        ("로컬 저장소", config.workflow_repository_id, match.workflow_repository_id),
        ("수정 에이전트", config.default_fix_agent_id, match.fix_agent_id),
        ("검증 프로필", config.fix_verification_profile_id, match.fix_verification_profile_id),
        ("검토 에이전트", config.review_agent_id, match.review_agent_id),
    ):
        how = "설정" if configured is not None else "자동" if matched is not None else None
        rows.append({"label": label, "value": configured or matched, "how": how})
    if config.intake == "filtered" and rows[1]["value"] is None:
        rows[1]["note"] = "이슈 GitHub 담당자에 연결한 Agent"  # filtered 는 담당 연결로 정한다(phase 8)
    return rows


def _baseline_summary(conn: Connection, session_id: str, source_id: str) -> dict[str, Any] | None:
    """저장소 카드의 기준선 한 줄 — 가져온 적이 없으면 None. 중앙값은 `/metrics` 와 같은 계산."""
    record, items = repo.list_baseline(conn, session_id, source_id)
    if record is None:
        return None
    summary = summarize_baseline(
        [BaselineItemFact(issue_number=i["issue_number"], issue_opened_at=i["issue_opened_at"],
                          pr_number=i["pr_number"], pr_merged_at=i["pr_merged_at"]) for i in items],
        opened_before=record["opened_before"], fetched_at=record["fetched_at"],
    )
    median = summary.intake_to_merge.median
    return {
        "n": summary.intake_to_merge.n,
        "median": duration(int(median)) if median is not None else "모름",
        "fetched_at": summary.fetched_at,
    }


def notifications_context(conn: Connection, session_id: str, *, secrets: SecretStore) -> dict[str, Any]:
    """운영자 알림 화면 — URL 은 설정됨/없음·호스트만, 최근 알림은 사건·상태·시각·오류 분류만(URL·본문 없음)."""
    url = secrets.read(secret_store.NOTIFY_WEBHOOK_URL)
    return {
        "notify_configured": url is not None,
        "notify_host": webhook_host(url) if url is not None else None,
        "notifications": [
            {k: row[k] for k in ("event", "state", "attempts", "last_error", "created_at", "sent_at")}
            for row in repo.list_notifications(conn, session_id)
        ],
    }


def team_context(conn: Connection, session_id: str, *, now: str) -> dict[str, Any]:
    """팀 화면 — 멤버(표시 이름·이메일·역할·상태·가입·마지막 접속)와 쓰지 않은 초대(역할·만료). 비밀번호 해시·링크 토큰은 싣지 않는다."""
    last_seen = repo.member_last_seen(conn, session_id)
    return {
        "members": [
            {**{k: row[k] for k in ("member_id", "display_name", "email", "role", "disabled_at", "created_at")},
             "last_seen_at": last_seen.get(row["member_id"])}
            for row in repo.list_members(conn, session_id)
        ],
        "invites": [
            {k: row[k] for k in ("invite_id", "role", "created_at", "expires_at")}
            for row in repo.list_open_invites(conn, session_id, now=now)
        ],
    }


def me_context(conn: Connection, session_id: str, member_id: str, *, secrets: SecretStore) -> dict[str, Any]:
    """내 설정 — 표시 이름·이메일·역할, 개인 웹훅은 설정됨/없음·호스트만(URL 은 다시 보이지 않는다),
    내가 받는 최근 알림(사건·상태·시각·오류 분류만 — 본문 없음)."""
    row = repo.get_member(conn, session_id, member_id)
    url = secrets.read(secret_store.personal_webhook_name(member_id))
    return {
        "account": {k: row[k] for k in ("display_name", "email", "role")},
        "webhook_configured": url is not None,
        "webhook_host": webhook_host(url) if url is not None else None,
        "notifications": [
            {k: n[k] for k in ("event", "channel", "state", "attempts", "last_error", "created_at", "sent_at")}
            for n in repo.list_notifications(conn, session_id, member_id=member_id)
        ],
    }


def github_context(
    conn: Connection, session_id: str, *, now: str, settings: Settings, secrets: SecretStore
) -> dict[str, Any]:
    """운영자 GitHub 화면 — 연결 상태(비밀은 연결됨/없음만)·저장소 카드(동기화·수집 자격·러너 매칭·트리거 라벨)·
    접힌 고급 설정(소스 설정·담당 연결)·실제 업무 목록·열린 사람 요청. 쓰기는 화면의 스크립트가 JSON API
    (`github_api`·`human_api`)로, 연결은 `/operator/github/app/new`·`/operator/github/token` 폼으로 한다."""
    agents = [agent_public(a, now=now, settings=settings) for a in repo.list_session_agents(conn, session_id)]

    def able(code: str) -> list[dict[str, Any]]:
        return [a for a in agents if any(c["code"] == code for c in a["capabilities"])]

    sources = []
    for config in repo.list_github_sources(conn, session_id):
        issues = []
        for row in repo.list_source_issues(conn, session_id, config.source_id):
            task = repo.get_task(conn, row["task_id"])
            snapshot = GitHubIssueSnapshot.model_validate_json(row["snapshot_json"])
            deliveries = repo.list_source_deliveries(conn, row["task_id"])
            issues.append({
                "number": row["issue_number"],
                "url": issue_url(config.repository_full_name, row["issue_number"]),
                "title": snapshot.title,
                "state": row["state"],
                "assignees": snapshot.assignee_logins,
                "task": task_summary(conn, task, now=now, settings=settings),
                "delivery": delivery_public(deliveries[-1], config.repository_full_name) if deliveries else None,
            })
        match = task_cycle.match_for_source(conn, session_id, config)
        credential = github_clients.credential_kind(config, settings, secrets)
        sources.append({
            "config": config.model_dump(mode="json"),
            "assignees": [b.model_dump(mode="json") for b in repo.list_assignee_bindings(conn, session_id, config.source_id)],
            "issues": issues,
            "synced_at": repo.get_source_synced_at(conn, session_id, config.source_id),
            "credential": CREDENTIAL_LABELS.get(credential) if credential else None,
            "match": _match_rows(config, match),
            "runner_missing": any(b.code == "repository_unmatched" for b in match.blockers),
            "match_blockers": [b.reason for b in match.blockers if b.code != "repository_unmatched"],
            "baseline": _baseline_summary(conn, session_id, config.source_id),
        })
    return {
        "token_configured": bool(settings.github_token),
        "pat_connected": secrets.exists(secret_store.GITHUB_TOKEN),
        "github_app": _saved_app(secrets),
        "allowed_repositories": list(settings.github_repos),
        "sources": sources,
        "fix_agents": able("code.fix"),
        "review_agents": able("code.review"),
        "open_requests": [
            {**{k: r[k] for k in ("request_id", "task_id", "code", "question", "created_at")},
             "task_title": repo.get_task(conn, r["task_id"])["title"]}
            for r in repo.list_open_human_requests(conn, session_id)
        ],
        "default_start_at": now,
    }


# --- 워크플로우 화면 (phase 5 step 6) --------------------------------------------------

# 이 상태의 노드가 하나라도 있으면 화면이 3초 폴링한다. 나머지(실행 가능·확인 필요·완료·실패)는 사람 조작 전에는 바뀌지 않는다
_LIVE_LABELS = ("실행 중", "실행 요청됨", "대기")


def _chain_issues(chain: Row, tasks: list[Row]) -> list[Issue]:
    """구성 이유를 다시 만들 재료. n8n 은 접수 때 저장한 항목 원문(`items_json`, ADR-0010). 그 밖의 출처·원문 없음은
    빈 목록."""
    if chain["source"] == "n8n":
        return [
            Issue(
                source="n8n", key=item["key"], title=item["title"], body=item["body"],
                labels=tuple(item["labels"]), blocked_by=tuple(item["blocked_by"]), url=None,
            )
            for item in json.loads(chain["items_json"] or "[]")
        ]
    return []


def _composition_reasons(conn: Connection, chain: Row, tasks: list[Row]) -> dict[str, tuple[str, ...]]:
    """접수 때의 구성 이유 문장(매핑·순서·체인·방식)을 같은 규칙(`compose`)과 세션 등록부로 다시 만든다 — 저장하지 않으므로.
    배정 이유(마지막 문장)는 저장된 `SelectionRecord.reason` 이 기준이라 뺀다. 후보 없이 돌려도 나머지 문장은 같다."""
    issues = _chain_issues(chain, tasks)
    if not issues:
        return {}
    plan = compose(
        issues, candidates=(), prefer=(),
        kinds=repo.list_kinds(conn, chain["session_id"]),
        rules=[rule for _, rule in repo.list_rules(conn, chain["session_id"])],
    )
    reasons = {node.issue.key: node.reasons[:-1] for node in plan.nodes}
    reasons.update({item.issue.key: (item.reason,) for item in plan.standalone})
    return reasons


def _chain_node(
    conn: Connection, task: Row, reasons: tuple[str, ...], kinds: Sequence[KindSpec], *, now: str, settings: Settings
) -> dict[str, Any]:
    """노드 하나 — `task_summary` 에 종류 라벨·담당·방식·선택 기록·이유를 얹는다. 상태는 task_summary 의 것 그대로."""
    selection = repo.get_selection(conn, task["task_id"])
    agent = (
        repo.get_agent(conn, selection.selected_agent_id)
        if selection is not None and selection.selected_agent_id is not None
        else None
    )
    spec = get_kind(kinds, task["kind"])
    return {
        **task_summary(conn, task, now=now, settings=settings),
        "kind_label": spec.label if spec is not None else task["kind"],
        "source_ref": task["source_ref"],
        "run_mode": task["run_mode"],
        "completion_mode": task["completion_mode"],
        "selection": selection,
        "agent": agent_public(agent, now=now, settings=settings) if agent is not None else None,
        "reasons": [*reasons, selection.reason if selection is not None else "후보 없음"],
    }


def _human_gate(conn: Connection, last: Row, node: dict[str, Any]) -> dict[str, Any]:
    """마지막 Task 에서 파생한 사람 단계. 상태 판정은 `node["status"]`(domain.status) 그대로이고 여기서는 문구만 고른다."""
    status: UserStatus = node["status"]
    if status.label == "완료":
        gate = ("완료", status.reason)
    elif status.label == "실패" and last["review_decision"] == "close":
        gate = ("실패", status.reason)
    elif status.label == "확인 필요" and (
        (execution := repo.active_execution(conn, last["task_id"])) is not None
        and execution["status"] == "result_ready"
    ):
        gate = ("확인 필요", status.reason)  # 검토 대기 / 판정 불가·미충족
    else:
        gate = ("대기", "선행 대기")
    return {
        "label": human_gate_label(last["completion_mode"]),
        "status_label": gate[0],
        "reason": gate[1],
        "task_id": last["task_id"],
    }


def _callback_state(chain: Row) -> dict[str, Any] | None:
    """체인 화면의 callback 한 줄 (ADR-0010 출구). `callback_url` 이 없으면 None. `state` 는 전송됨(`sent_at` 있음) ·
    실패(미전송이고 시도 횟수가 중단 횟수 이상) · 대기. 화면은 `host` 만 보인다 — n8n 내부 경로·실행 ID 는 필요 없다."""
    url = chain["callback_url"]
    if url is None:
        return None
    sent_at = chain["callback_sent_at"]
    attempts = chain["callback_attempts"]
    if sent_at is not None:
        state = "전송됨"
    elif attempts >= _CALLBACK_MAX_ATTEMPTS:
        state = "실패"
    else:
        state = "대기"
    return {
        "url": url,
        "host": urlsplit(url).netloc,
        "state": state,
        "sent_at": sent_at,
        "attempts": attempts,
        "last_error": chain["callback_last_error"],
    }


def _chain_progress(nodes: list[dict[str, Any]], started: bool) -> str:
    """동작 영역·홈의 진행 한 줄. 첫 미완료 노드의 사용자 상태를 그대로 쓴다."""
    if not started:
        return "시작 전"
    for index, node in enumerate(nodes, 1):
        if node["status"].label != "완료":
            return f"{len(nodes)}단계 중 {index}단계 {node['status'].label}"
    return f"{len(nodes)}단계 모두 완료"


def chain_summary(conn: Connection, chain: Row, *, now: str, settings: Settings) -> dict[str, Any]:
    """워크플로우 화면·홈 목록 컨텍스트. 노드는 `repo.tasks_of_chain` 순서, 상태는 `task_summary` 판정 그대로."""
    tasks = repo.tasks_of_chain(conn, chain["chain_id"])
    reasons = _composition_reasons(conn, chain, tasks)
    kinds = repo.list_kinds(conn, chain["session_id"])
    nodes = [
        _chain_node(conn, t, reasons.get(t["source_ref"], ()), kinds, now=now, settings=settings) for t in tasks
    ]
    started = chain["started_at"] is not None
    selected = [n["selection"] is not None and n["selection"].status == "selected" for n in nodes]
    return {
        "chain_id": chain["chain_id"],
        "title": chain["title"],
        "source": chain["source"],
        "source_label": chain["source"],
        "tasks": nodes,
        "done_count": sum(1 for n in nodes if n["status"].label == "완료"),
        "total": len(nodes),
        "started": started,
        "human_gate": _human_gate(conn, tasks[-1], nodes[-1]) if nodes else None,
        "skipped": json.loads(chain["skipped_json"]),
        "all_selected": all(selected),
        # 시작은 첫 노드만 본다 — 뒤 노드는 시작 후에도 확정할 수 있다 (워커가 선택 기록을 보고 착수)
        "can_start": not started and bool(nodes) and selected[0],
        "progress": _chain_progress(nodes, started),
        "polling": any(n["status"].label in _LIVE_LABELS for n in nodes),
        "callback": _callback_state(chain),
    }


# --- Step 7: 뷰어·결과 카드 재료 ------------------------------------------------------


def diff_stats(text: str) -> tuple[int, int, int]:
    """unified diff → (파일 수, 추가 줄, 삭제 줄). `+++`/`---` 헤더는 세지 않는다."""
    files = added = removed = 0
    for line in text.splitlines():
        if line.startswith("+++ "):
            files += 1
        elif line.startswith("+") and not line.startswith("+++"):
            added += 1
        elif line.startswith("-") and not line.startswith("---"):
            removed += 1
    return files, added, removed


def diff_lines(text: str) -> list[tuple[str, str]]:
    """unified diff 의 줄마다 (`meta`|`hunk`|`add`|`del`|`ctx`, 원문)."""
    result = []
    for line in text.splitlines():
        if line.startswith(("diff ", "index ", "--- ", "+++ ")):
            kind = "meta"
        elif line.startswith("@@"):
            kind = "hunk"
        elif line.startswith("+"):
            kind = "add"
        elif line.startswith("-"):
            kind = "del"
        else:
            kind = "ctx"
        result.append((kind, line))
    return result


def tail_lines(text: str, count: int) -> list[str]:
    lines = text.splitlines()
    return lines[-count:] if lines else []


def _read_text(conn: Connection, store: ArtifactStore, artifact_id: str | None) -> str | None:
    """산출물 본문. 없거나 파일이 사라졌으면 None (뷰어는 "첨부 없음" 으로 보인다)."""
    if not artifact_id:
        return None
    try:
        return repo.read_artifact(conn, store, artifact_id).decode("utf-8", errors="replace")
    except (NotFound, ArtifactMissing):
        return None


def viewer_context(
    conn: Connection, store: ArtifactStore, result: dict[str, Any] | None, *, session_id: str
) -> dict[str, Any] | None:
    """오른쪽 열(검증 요약 / 결과 봉투) 과 결과 카드 메타의 재료. `result` 는 `task_context()["result"]`."""
    if result is None:
        return None
    viewer: dict[str, Any] = {
        "kind": result["kind"],
        "artifact_id": result["artifact_id"],
        "execution_id": result["execution_id"],
        "data": result["data"],
        "raw_text": _read_text(conn, store, result["artifact_id"]) or "",
        "verdict": None,
        "verdict_passed": 0,
        "verdict_total": 0,
    }
    verdict_row = repo.get_verdict(conn, result["execution_id"])
    if verdict_row is not None:
        verdict = json.loads(verdict_row["verdict_json"])
        checks = verdict.get("checks", [])
        viewer["verdict"] = verdict
        viewer["verdict_passed"] = sum(1 for c in checks if c.get("passed"))
        viewer["verdict_total"] = len(checks)

    if result["kind"] == "generic_result":
        return viewer  # 결과 봉투는 outcome·summary·산출물 ID 뿐 — 검증 요약(diff·로그·보고서)이 없다

    latest: dict[str, Row] = {}
    for artifact in repo.artifacts_of(conn, result["execution_id"]):
        latest[artifact["kind"]] = artifact  # 같은 kind 가 여럿이면 마지막 것

    def text_of(kind: str) -> tuple[str | None, str | None]:
        artifact = latest.get(kind)
        return (artifact["artifact_id"], _read_text(conn, store, artifact["artifact_id"])) if artifact else (None, None)

    diff_id, diff_text = text_of("diff")
    before_id, before_text = text_of("test_log_before")
    after_id, after_text = text_of("test_log_after")
    viewer["diff"] = (
        {"artifact_id": diff_id, "lines": diff_lines(diff_text), "stats": diff_stats(diff_text)}
        if diff_text is not None else None
    )
    viewer["test_before"] = (
        {"artifact_id": before_id, "lines": tail_lines(before_text, LOG_TAIL)} if before_text is not None else None
    )
    viewer["test_after"] = (
        {"artifact_id": after_id, "lines": tail_lines(after_text, LOG_TAIL)} if after_text is not None else None
    )
    return viewer


def artifact_render(artifact: dict[str, Any] | Row, data: bytes) -> dict[str, Any]:
    """산출물 단독 페이지·칩 뷰어의 렌더 모드. diff 는 줄 색, 로그는 줄 번호, JSON 은 정렬, 나머지는 원문."""
    text = data.decode("utf-8", errors="replace")
    kind = artifact["kind"]
    content_type = (artifact["content_type"] or "").split(";")[0].strip()
    if kind == "diff":
        return {"mode": "diff", "text": text, "diff_lines": diff_lines(text)}
    if content_type == "application/json":
        try:
            return {"mode": "json", "text": json.dumps(json.loads(text), ensure_ascii=False, indent=2)}
        except ValueError:
            return {"mode": "text", "text": text}
    if kind in LOG_KINDS:
        return {"mode": "log", "text": text, "lines": text.splitlines()}
    return {"mode": "text", "text": text}


# --- 지표 화면 (phase 9 step 9, ADR-0015) -------------------------------------------------------------
# 중앙값(비율) 옆에 n·미완료·모름을 함께 적는다. 모르는 값은 "모름" 이며 0 으로 보이지 않는다.

UNKNOWN_TEXT = "모름"

# 영역 → (칸 이름, 이름표, 단위). 매핑 칸(handoff_blocked·failed_codes)은 아래에서 따로 펼친다.
METRIC_AREAS: tuple[tuple[str, tuple[tuple[str, str, str], ...]], ...] = (
    ("병목", (("handoff_wait", "인계 대기", "seconds"),)),
    ("속도", (("intake_to_human", "접수 → 사람 차례", "seconds"), ("intake_to_done", "접수 → 완료", "seconds"),
             ("intake_to_approval", "접수 → 승인", "seconds"))),
    ("사람 부담", (("interventions", "개입 횟수", "count"), ("response_time", "응답 시간", "seconds"))),
    ("품질", (("first_pass", "1회 통과율", "ratio"), ("rework", "재작업 횟수", "count"),
             ("human_rejection", "사람 거부 비율", "ratio"))),
    ("비용", (("execution_time", "실행 시간", "seconds"), ("cost_usd", "CLI 보고 비용", "usd"),
             ("input_tokens", "입력 토큰", "tokens"), ("output_tokens", "출력 토큰", "tokens"))),
    ("신뢰성", (("failure", "실패율", "ratio"), ("failed_codes", "실패 사유", "codes"),
               ("reruns", "재실행", "ratio"))),
)
ACTOR_LABELS = {"operator": "운영자", "assignee": "담당자", "system": "시스템"}


def _amount(value: float, unit: str) -> str:
    if unit == "seconds":
        return duration(round(value))
    if unit == "usd":
        return f"${value:.4g}"
    if unit == "tokens":
        return f"{value:,.0f}"
    return f"{value:g}"


def _stat_cell(stat: Stat, unit: str) -> dict[str, str]:
    parts = [f"n {stat.n}"]
    if stat.total is not None and unit != "seconds":
        parts.append(f"합계 {_amount(stat.total, unit)}")
    if stat.incomplete:
        parts.append(f"미완료 {stat.incomplete}")
    if stat.unknown:
        parts.append(f"{UNKNOWN_TEXT} {stat.unknown}")
    text = UNKNOWN_TEXT if stat.median is None else _amount(stat.median, unit)
    return {"text": text, "detail": " · ".join(parts)}


def _ratio_cell(ratio: Ratio) -> dict[str, str]:
    parts = [f"n {ratio.denominator}"]
    if ratio.incomplete:
        parts.append(f"미완료 {ratio.incomplete}")
    if ratio.unknown:
        parts.append(f"{UNKNOWN_TEXT} {ratio.unknown}")
    rate = ratio.rate
    text = UNKNOWN_TEXT if rate is None else f"{rate * 100:.0f}% ({ratio.numerator}/{ratio.denominator})"
    return {"text": text, "detail": " · ".join(parts)}


def _codes_cell(codes: Mapping[str, int]) -> dict[str, str]:
    text = ", ".join(f"{code} {count}" for code, count in codes.items()) or "없음"
    return {"text": text, "detail": f"n {sum(codes.values())}"}


def _cell(value: Any, unit: str) -> dict[str, str]:
    if unit == "ratio":
        return _ratio_cell(value)
    if unit == "codes":
        return _codes_cell(value)
    return _stat_cell(value, unit)


def group_label(key: str, group_by: str | None) -> str:
    if key == ALL_GROUP:
        return "전체"
    if key == UNKNOWN:
        return UNKNOWN_TEXT
    if group_by == "config_revision":
        return f"설정 번호 {key}"
    return f"커밋 {key[:7]}"


def metrics_context(report: MetricsReport, baselines: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """`metrics.html` 컨텍스트 — 맨 위 기준선 대 도입 후, 영역별 표(행 = 지표, 열 = 그룹)."""
    groups = report.groups
    areas = []
    for title, metrics in METRIC_AREAS:
        rows = [{"label": label, "cells": [_cell(getattr(g, name), unit) for g in groups]}
                for name, label, unit in metrics]
        if title == "병목":
            rows += [{"label": f"대기 — {ACTOR_LABELS[actor]}",
                      "cells": [_stat_cell(g.handoff_blocked[actor], "seconds") for g in groups]}
                     for actor in ACTORS]
        areas.append({"title": title, "rows": rows})
    after = [{"label": group_label(g.key, report.group_by), "cell": _stat_cell(g.intake_to_merge, "seconds"),
              "closed_unmerged": g.closed_unmerged} for g in groups]
    baseline_rows = [
        {**b, "cell": None if b["intake_to_merge"] is None else _stat_cell(Stat(**b["intake_to_merge"]), "seconds")}
        for b in baselines
    ]
    return {
        "group_columns": [{"key": g.key, "label": group_label(g.key, report.group_by)} for g in groups],
        "areas": areas, "after": after, "baselines": baseline_rows,
    }
