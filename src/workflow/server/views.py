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
from workflow.adapters.errors import ArtifactMissing, NotFound
from workflow.domain.evidence_location import resolve_location
from workflow.domain.status import TaskView, UserStatus, user_status
from workflow.server.filters import KIND_LABELS, KST, kst
from workflow.server.settings import Settings

# 결과 봉투로 화면이 파싱하는 산출물 종류 (CONTRACT 5·7절)
RESULT_KINDS = ("diagnosis_result", "code_change_result")

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


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts)


def _utc(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def kst_day_bounds(now: str) -> tuple[str, str]:
    """(오늘 00:00 KST 의 UTC 시각, 내일 00:00 KST 의 RFC 3339). 일일 상한 계산과 CONTRACT 10절 `resets_at`."""
    start = _parse(now).astimezone(KST).replace(hour=0, minute=0, second=0, microsecond=0)
    return _utc(start), (start + timedelta(days=1)).isoformat()


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
    """등록 카탈로그 카드의 "발견된 정보" 요약. API 는 능력의 역할·자료 범위, 로컬은 발견된 설정 키와 검증 프로필."""
    if agent["connection_type"] == "api":
        return [
            c["code"] + "".join(f" · {k}={v}" for k, v in c["scope"].items()) for c in agent["capabilities"]
        ]
    found = agent["discovered"].get("found") if isinstance(agent["discovered"], dict) else None
    return [*_found_keys(found), *(f"검증 프로필 {p}" for p in agent["verification_profile_ids"])]


def agent_public(agent: Row, *, now: str, settings: Settings) -> dict[str, Any]:
    """화면용 Agent. JSON 컬럼은 풀고 비밀 참조는 뺀다."""
    data = {k: agent[k] for k in agent.keys() if k not in _AGENT_PRIVATE}
    data["capabilities"] = json.loads(data.pop("capabilities_json"))
    data["verification_profile_ids"] = json.loads(data.pop("verification_profile_ids_json"))
    data["discovered"] = json.loads(data.pop("discovered_json"))
    data["shared_to_all_sessions"] = bool(data["shared_to_all_sessions"])
    data["demo_scripted"] = bool(data["demo_scripted"])
    data["online"] = agent_online(agent, now=now, settings=settings)
    data["discovered_summary"] = discovered_summary(data)
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
    executions = [_execution_context(conn, e, now) for e in repo.list_executions(conn, task_row["task_id"])]
    active = next((e for e in executions if e["released_at"] is None), None)
    result = _result_context(conn, store, executions)
    # 결과 카드의 "대본 재생" 표시는 결과를 만든 실행의 Agent 기준. 결과가 없으면 선택된 Agent
    producer = next((e for e in executions if result is not None and e["execution_id"] == result["execution_id"]), None)
    result_agent = repo.get_agent(conn, producer["agent_id"]) if producer is not None else agent_row
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
        "result": result,
        "agent_scripted": bool(result_agent["demo_scripted"]) if result_agent is not None else False,
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
        # 후보는 이 세션이 카탈로그에서 등록한 Agent 만 (phase 5 step 2). 등록 순서대로
        "candidates": [
            agent_public(a, now=now, settings=settings)
            for a in repo.list_session_agents(conn, task_row["session_id"])
        ] if needs_selection else [],
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


def _pretty_json(text: str) -> str:
    try:
        return json.dumps(json.loads(text), ensure_ascii=False, indent=2)
    except ValueError:
        return text


class _AttachmentReader:
    """결과 봉투의 `attachments` 를 (evidence_id, version) 로 찾아 원문을 읽는다.

    같은 세션의 산출물만 읽는다 — 결과 봉투는 외부(진단 서비스)가 쓴 자료라 다른 세션의 artifact_id 를
    가리켜도 원문을 보이지 않는다. 읽은 바이트는 artifact_id 별로 한 번만 읽는다."""

    def __init__(self, conn: Connection, store: ArtifactStore, data: dict[str, Any], session_id: str):
        self._conn, self._store, self._session_id = conn, store, session_id
        self._by_key = {(a["evidence_id"], a["version"]): a for a in data.get("attachments", [])}
        self._cache: dict[str, bytes | None] = {}

    def find(self, evidence_id: str, version: str | None = None) -> dict[str, Any] | None:
        if version is not None:
            return self._by_key.get((evidence_id, version))
        return next((a for (e, _), a in self._by_key.items() if e == evidence_id), None)

    def read(self, attachment: dict[str, Any] | None) -> bytes | None:
        if attachment is None:
            return None
        artifact_id = attachment["artifact_id"]
        if artifact_id not in self._cache:
            row = repo.get_artifact(self._conn, artifact_id)
            if row is None or row["session_id"] != self._session_id:
                self._cache[artifact_id] = None
            else:
                try:
                    self._cache[artifact_id] = self._store.read(row["store_ref"])
                except FileNotFoundError:
                    self._cache[artifact_id] = None
        return self._cache[artifact_id]


def evidence_excerpts(
    conn: Connection, store: ArtifactStore, data: dict[str, Any], *, session_id: str
) -> dict[str, dict[str, Any]]:
    """findings 의 evidence_refs 마다 첨부 원문에서 location 을 잘라 낸다 (UI_GUIDE 근거 칩).

    키는 `evidence_id@version · location`. 값은 `{found, text}` — 첨부·산출물이 없으면 "첨부 없음",
    위치가 원문에 없으면 "원문에 없음". 모델 문장을 원문 대신 넣지 않는다."""
    reader = _AttachmentReader(conn, store, data, session_id)
    excerpts: dict[str, dict[str, Any]] = {}
    for finding in data.get("findings", []):
        for ref in finding.get("evidence_refs", []):
            key = f"{ref['evidence_id']}@{ref['version']} · {ref['location']}"
            if key in excerpts:
                continue
            attachment = reader.find(ref["evidence_id"], ref["version"])
            content = reader.read(attachment)
            if content is None:
                excerpts[key] = {"found": False, "text": "첨부 없음"}
                continue
            try:
                resolved = resolve_location(content, attachment["content_type"], ref["location"])
            except ValueError:
                resolved = None
            if resolved is None:
                excerpts[key] = {"found": False, "text": "원문에 없음"}
            elif ref["location"].startswith("lines:"):
                excerpts[key] = {"found": True, "text": "\n".join(resolved.value)}
            else:
                excerpts[key] = {"found": True, "text": json.dumps(resolved.value, ensure_ascii=False, indent=2)}
    return excerpts


def _response_pair(
    conn: Connection, store: ArtifactStore, data: dict[str, Any], *, session_id: str
) -> dict[str, Any] | None:
    """두 칸 비교 `변경 전 응답 / 변경 후 응답`. diagnosis 의 baseline/failed 실행 기록(`run-{run_id}`) 의
    `response_ref` 가 가리키는 첨부 원문이다. 없으면 text None."""
    diagnosis = data.get("diagnosis")
    if not diagnosis:
        return None
    reader = _AttachmentReader(conn, store, data, session_id)

    def side(run_id: str | None, path: str | None) -> dict[str, Any]:
        record = reader.read(reader.find(f"run-{run_id}")) if run_id else None
        ref = None
        if record is not None:
            try:
                ref = json.loads(record).get("response_ref")
            except (ValueError, AttributeError):
                ref = None
        attachment = reader.find(ref["evidence_id"], ref.get("version")) if isinstance(ref, dict) else None
        content = reader.read(attachment)
        return {
            "run_id": run_id,
            "path": path,
            "evidence": f"{attachment['evidence_id']}@{attachment['version']}" if attachment else None,
            "text": _pretty_json(content.decode("utf-8", errors="replace")) if content is not None else None,
        }

    return {
        "before": side(diagnosis.get("baseline_run_id"), diagnosis.get("old_path")),
        "after": side(diagnosis.get("failed_run_id"), diagnosis.get("new_path")),
    }


def viewer_context(
    conn: Connection, store: ArtifactStore, result: dict[str, Any] | None, *, session_id: str
) -> dict[str, Any] | None:
    """오른쪽 열(진단 결과 / 검증 요약) 과 결과 카드 메타의 재료. `result` 는 `task_context()["result"]`."""
    if result is None:
        return None
    data = result["data"] or {}
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

    if result["kind"] == "diagnosis_result":
        viewer["excerpts"] = evidence_excerpts(conn, store, data, session_id=session_id)
        viewer["compare"] = _response_pair(conn, store, data, session_id=session_id)
        return viewer

    latest: dict[str, Row] = {}
    for artifact in repo.artifacts_of(conn, result["execution_id"]):
        latest[artifact["kind"]] = artifact  # 같은 kind 가 여럿이면 마지막 것

    def text_of(kind: str) -> tuple[str | None, str | None]:
        artifact = latest.get(kind)
        return (artifact["artifact_id"], _read_text(conn, store, artifact["artifact_id"])) if artifact else (None, None)

    diff_id, diff_text = text_of("diff")
    before_id, before_text = text_of("test_log_before")
    after_id, after_text = text_of("test_log_after")
    report_id, report_text = text_of("report_output")
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
    viewer["report"] = {"artifact_id": report_id, "text": report_text} if report_text is not None else None
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
