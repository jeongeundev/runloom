"""화면 컨텍스트 조립 — DB 행을 `TaskView`(domain.status) 와 템플릿 컨텍스트로 옮긴다.

판정은 `domain.status.user_status` 가 한다. 여기서는 재료(선택 기록·선행 상태·연결 생존·활성
실행·판정)를 모으고, 비밀값(`credential_ref`·토큰) 은 컨텍스트에 넣지 않는다. 쓰기는 하지 않는다.
"""

import json
from datetime import UTC, datetime, timedelta
from sqlite3 import Connection, Row
from typing import Any

from workflow.adapters import repo
from workflow.adapters.artifact_store import ArtifactStore
from workflow.adapters.errors import ArtifactMissing
from workflow.domain.status import TaskView, UserStatus, user_status
from workflow.server.filters import KST, kst
from workflow.server.settings import Settings

# 결과 봉투로 화면이 파싱하는 산출물 종류 (CONTRACT 5·7절)
RESULT_KINDS = ("diagnosis_result", "code_change_result")

# 세션 화면에 넘기지 않는 agents 컬럼. `credential_ref` 는 참조명이지만 이름만으로도 환경 구성이 드러난다.
_AGENT_PRIVATE = ("credential_ref",)


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts)


def _utc(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def kst_day_bounds(now: str) -> tuple[str, str]:
    """(오늘 00:00 KST 의 UTC 시각, 내일 00:00 KST 의 RFC 3339). 일일 상한 계산과 CONTRACT 10절 `resets_at`."""
    start = _parse(now).astimezone(KST).replace(hour=0, minute=0, second=0, microsecond=0)
    return _utc(start), (start + timedelta(days=1)).isoformat()


def agent_online(agent: Row, *, now: str, settings: Settings) -> bool:
    """`online` 이고 마지막 heartbeat 가 `heartbeat_offline_seconds` 이내인지."""
    if agent["connection_state"] != "online" or not agent["last_seen_at"]:
        return False
    age = _parse(now) - _parse(agent["last_seen_at"])
    return age <= timedelta(seconds=settings.limits.heartbeat_offline_seconds)


def agent_public(agent: Row, *, now: str, settings: Settings) -> dict[str, Any]:
    """화면용 Agent. JSON 컬럼은 풀고 비밀 참조는 뺀다."""
    data = {k: agent[k] for k in agent.keys() if k not in _AGENT_PRIVATE}
    data["capabilities"] = json.loads(data.pop("capabilities_json"))
    data["verification_profile_ids"] = json.loads(data.pop("verification_profile_ids_json"))
    data["discovered"] = json.loads(data.pop("discovered_json"))
    data["shared_to_all_sessions"] = bool(data["shared_to_all_sessions"])
    data["online"] = agent_online(agent, now=now, settings=settings)
    return data


def summarize_verdict(verdict: dict[str, Any]) -> tuple[str, str]:
    """task_verdicts 의 JSON → (outcome, 화면 한 줄). 통과면 `판정 근거: n/m`, 아니면 미충족 항목 코드."""
    checks = verdict.get("checks", [])
    passed = [c["code"] for c in checks if c.get("passed")]
    failed = [c["code"] for c in checks if not c.get("passed")]
    outcome = verdict["outcome"]
    if outcome == "passed":
        return outcome, f"판정 근거: {len(passed)}/{len(checks)}"
    return outcome, ("미충족: " + ", ".join(failed)) if failed else "판정 불가"


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

    connector_online = connector_last_seen = None
    if task["kind"] == "code_change" and selected is not None:
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
    아니면 현재 실행·연결 상태로 실시간 판정한다."""
    if task["finished_at"] is not None:
        return UserStatus(task["status"], task["status_reason"])
    return user_status(view)


def task_summary(conn: Connection, task: Row, *, now: str, settings: Settings) -> dict[str, Any]:
    """목록·링크용 요약 (왼쪽 목록, 선행·후속 칩)."""
    return {
        "task_id": task["task_id"],
        "title": task["title"],
        "kind": task["kind"],
        "created_at": task["created_at"],
        "status": status_of(task, build_task_view(conn, task, now=now, settings=settings)),
    }


def _execution_context(conn: Connection, execution: Row) -> dict[str, Any]:
    data = dict(execution)
    data["events"] = [
        {**dict(e), "data": json.loads(e["data_json"])}
        for e in repo.list_events(conn, execution["execution_id"])
    ]
    data["artifacts"] = [dict(a) for a in repo.artifacts_of(conn, execution["execution_id"])]
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


def task_context(
    conn: Connection, store: ArtifactStore, task_row: Row, *, now: str, settings: Settings
) -> dict[str, Any]:
    """업무 상세 템플릿 컨텍스트 전부. 동작 가능 여부(`can_run`·`can_review`·`needs_selection`)도 여기서 정한다."""
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
    executions = [_execution_context(conn, e) for e in repo.list_executions(conn, task_row["task_id"])]
    active = next((e for e in executions if e["released_at"] is None), None)
    finished = task_row["finished_at"] is not None
    selected = selection is not None and selection.status == "selected"
    predecessor = (
        task_summary(conn, repo.get_task(conn, task_row["predecessor_task_id"]), now=now, settings=settings)
        if task_row["predecessor_task_id"] is not None
        else None
    )

    needs_selection = not finished and active is None and not selected
    return {
        "task": task,
        "view": view,
        "status": status,
        "selection": selection,
        "agent": agent_public(agent_row, now=now, settings=settings) if agent_row is not None else None,
        "executions": executions,
        "active_execution": active,
        "result": _result_context(conn, store, executions),
        "predecessor": predecessor,
        "successors": [
            task_summary(conn, s, now=now, settings=settings)
            for s in repo.successors_of(conn, task_row["task_id"])
        ],
        "can_run": (
            not finished
            and active is None
            and selected
            and (predecessor is None or predecessor["status"].label == "완료")
        ),
        "can_review": (
            not finished
            and active is not None
            and active["status"] == "result_ready"
            and status.label == "확인 필요"
        ),
        "needs_selection": needs_selection,
        "candidates": [
            agent_public(a, now=now, settings=settings)
            for a in repo.list_agents(conn)
            if a["shared_to_all_sessions"]
        ] if needs_selection else [],
    }
