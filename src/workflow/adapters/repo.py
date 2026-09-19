"""중앙 DB 함수형 저장소.

- 모든 함수는 `conn` 을 첫 인자로 받고, 여러 문장을 바꾸는 함수는 `BEGIN IMMEDIATE` … `COMMIT` 을 안에서 연다.
- 시각은 `now: str`(RFC 3339 UTC) 로 받는다. 비교는 같은 형식의 문자열 비교이므로 서버는 한 곳에서 만든다.
- 상태 전이는 `workflow.domain.status.next_execution_status` 에 묻고 여기서 다시 정하지 않는다.
- 발신자 신원은 인증 계층이 주는 `actor` 인자로만 온다. 이벤트 본문의 자칭 sender 는 없다.
- 트랜잭션 안에서 해시 계산·파일 쓰기 같은 긴 작업을 하지 않는다.
"""

import hashlib
import json
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from sqlite3 import Connection, Row

from workflow.adapters.artifact_store import ArtifactStore
from workflow.adapters.errors import (
    ActiveExecutionExists,
    ArtifactMissing,
    DuplicateStartKey,
    EventConflict,
    HashMismatch,
    InvalidTransition,
    NotFound,
    SequenceGap,
)
from workflow.contracts.v1 import (
    ArtifactCreated,
    ArtifactMeta,
    Capability,
    EventAck,
    ExecutionEvent,
    ExecutionRequest,
    HandoffBundle,
    SelectionRecord,
)
from workflow.domain import status as domain_status
from workflow.domain.status import TERMINAL_STATUSES, next_execution_status

TOKEN_PREFIX = "wfc_"


@contextmanager
def _tx(conn: Connection):
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts)


def _plus_seconds(ts: str, seconds: int) -> str:
    return (_parse(ts) + timedelta(seconds=seconds)).astimezone(UTC).isoformat().replace("+00:00", "Z")


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _one(conn: Connection, sql: str, params: tuple = ()) -> Row | None:
    return conn.execute(sql, params).fetchone()


def _require_rowcount(cursor: sqlite3.Cursor, what: str) -> None:
    if cursor.rowcount == 0:
        raise NotFound(what)


# --- 세션·운영자·에이전트 ---------------------------------------------------


def create_session(conn: Connection, session_id: str, now: str) -> None:
    conn.execute("INSERT INTO sessions (session_id, created_at) VALUES (?, ?)", (session_id, now))


def get_session(conn: Connection, session_id: str) -> Row | None:
    return _one(conn, "SELECT * FROM sessions WHERE session_id = ?", (session_id,))


def mark_operator(conn: Connection, session_id: str) -> None:
    cur = conn.execute("UPDATE sessions SET is_operator = 1 WHERE session_id = ?", (session_id,))
    _require_rowcount(cur, f"session {session_id}")


_AGENT_REQUIRED = ("agent_id", "name", "owner_scope", "connection_type", "capabilities")
_AGENT_OPTIONAL = {
    "connector_id": None,
    "local_registration_id": None,
    "repository_id": None,
    "base_commit": None,
    "verification_profile_ids": [],
    "api_url": None,
    "credential_ref": None,
    "discovered": {},
    "connection_state": "unknown",
    "last_seen_at": None,
    "shared_to_all_sessions": False,
}


def upsert_agent(conn: Connection, agent: dict) -> None:
    """키는 agents 컬럼. JSON 컬럼은 `capabilities`·`verification_profile_ids`·`discovered` 로 받아
    검증(Capability)한 뒤 `*_json` 에 저장한다. `credential_ref` 는 참조명(`env:…`)이며 값이 아니다."""
    unknown = set(agent) - set(_AGENT_REQUIRED) - set(_AGENT_OPTIONAL)
    if unknown:
        raise ValueError(f"agents 에 없는 키: {sorted(unknown)}")
    missing = [k for k in _AGENT_REQUIRED if k not in agent]
    if missing:
        raise ValueError(f"필수 키 누락: {missing}")
    a = {**_AGENT_OPTIONAL, **agent}
    caps = [Capability.model_validate(c).model_dump() for c in a["capabilities"]]
    conn.execute(
        """
        INSERT INTO agents (agent_id, name, owner_scope, connection_type, connector_id,
          local_registration_id, repository_id, base_commit, verification_profile_ids_json, api_url,
          credential_ref, capabilities_json, discovered_json, connection_state, last_seen_at,
          shared_to_all_sessions)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(agent_id) DO UPDATE SET
          name = excluded.name, owner_scope = excluded.owner_scope,
          connection_type = excluded.connection_type, connector_id = excluded.connector_id,
          local_registration_id = excluded.local_registration_id,
          repository_id = excluded.repository_id, base_commit = excluded.base_commit,
          verification_profile_ids_json = excluded.verification_profile_ids_json,
          api_url = excluded.api_url, credential_ref = excluded.credential_ref,
          capabilities_json = excluded.capabilities_json, discovered_json = excluded.discovered_json,
          connection_state = excluded.connection_state, last_seen_at = excluded.last_seen_at,
          shared_to_all_sessions = excluded.shared_to_all_sessions
        """,
        (
            a["agent_id"], a["name"], a["owner_scope"], a["connection_type"], a["connector_id"],
            a["local_registration_id"], a["repository_id"], a["base_commit"],
            json.dumps(list(a["verification_profile_ids"])), a["api_url"], a["credential_ref"],
            json.dumps(caps, ensure_ascii=False), json.dumps(a["discovered"], ensure_ascii=False),
            a["connection_state"], a["last_seen_at"], int(bool(a["shared_to_all_sessions"])),
        ),
    )


def list_agents(conn: Connection) -> list[Row]:
    return conn.execute("SELECT * FROM agents ORDER BY agent_id").fetchall()


def get_agent(conn: Connection, agent_id: str) -> Row | None:
    return _one(conn, "SELECT * FROM agents WHERE agent_id = ?", (agent_id,))


def delete_agent(conn: Connection, agent_id: str) -> None:
    cur = conn.execute("DELETE FROM agents WHERE agent_id = ?", (agent_id,))
    _require_rowcount(cur, f"agent {agent_id}")


def set_agent_connection(
    conn: Connection, agent_id: str, state: str, last_seen_at: str | None
) -> None:
    cur = conn.execute(
        "UPDATE agents SET connection_state = ?, last_seen_at = ? WHERE agent_id = ?",
        (state, last_seen_at, agent_id),
    )
    _require_rowcount(cur, f"agent {agent_id}")


def agents_for_connector(conn: Connection, connector_id: str) -> list[Row]:
    return conn.execute(
        "SELECT * FROM agents WHERE connector_id = ? ORDER BY agent_id", (connector_id,)
    ).fetchall()


def update_registration(
    conn: Connection,
    local_registration_id: str,
    *,
    connector_id: str,
    repository_id: str,
    base_commit: str,
    verification_profile_ids: list[str],
    discovered: dict,
    now: str,
) -> str:
    """연결 프로그램이 보고한 로컬 등록으로, 운영자가 미리 등록한 agent 의 연결 정보를 채운다.
    이름·소유 구분·능력은 운영자 값이라 건드리지 않는다. 없으면 NotFound. 반환은 agent_id."""
    with _tx(conn):
        row = _one(
            conn,
            "SELECT agent_id FROM agents WHERE local_registration_id = ? ORDER BY agent_id",
            (local_registration_id,),
        )
        if row is None:
            raise NotFound(f"local registration {local_registration_id}")
        conn.execute(
            """
            UPDATE agents SET connector_id = ?, repository_id = ?, base_commit = ?,
              verification_profile_ids_json = ?, discovered_json = ?,
              connection_state = 'online', last_seen_at = ?
            WHERE agent_id = ?
            """,
            (
                connector_id, repository_id, base_commit, json.dumps(list(verification_profile_ids)),
                json.dumps(discovered, ensure_ascii=False), now, row["agent_id"],
            ),
        )
    return row["agent_id"]


# --- 연결 코드·연결 프로그램 (ARCHITECTURE 인증 절) -------------------------


def issue_connect_code(conn: Connection, now: str, ttl_seconds: int = 600) -> str:
    code = secrets.token_urlsafe(24)
    conn.execute(
        "INSERT INTO connect_codes (code, issued_at, expires_at) VALUES (?, ?, ?)",
        (code, now, _plus_seconds(now, ttl_seconds)),
    )
    return code


def revoke_connect_code(conn: Connection, code: str, now: str) -> None:
    cur = conn.execute(
        "UPDATE connect_codes SET revoked_at = ? "
        "WHERE code = ? AND used_at IS NULL AND revoked_at IS NULL",
        (now, code),
    )
    _require_rowcount(cur, "connect code")


def list_connect_codes(conn: Connection) -> list[Row]:
    """운영자 화면용. 최근 발급 순. 코드 자체는 1회용·10분이라 화면에 보여도 된다."""
    return conn.execute("SELECT * FROM connect_codes ORDER BY issued_at DESC, code").fetchall()


def exchange_connect_code(conn: Connection, code: str, now: str) -> tuple[str, str]:
    """(connector_id, token_plain). 만료·사용·취소된 코드는 NotFound. DB 에는 토큰 sha256 만 남는다."""
    token_plain = TOKEN_PREFIX + secrets.token_urlsafe(32)
    token_hash = _sha256(token_plain)
    connector_id = f"conn-{secrets.token_hex(4)}"
    with _tx(conn):
        row = _one(conn, "SELECT * FROM connect_codes WHERE code = ?", (code,))
        if (
            row is None
            or row["used_at"] is not None
            or row["revoked_at"] is not None
            or _parse(now) >= _parse(row["expires_at"])
        ):
            raise NotFound("connect code")
        conn.execute("UPDATE connect_codes SET used_at = ? WHERE code = ?", (now, code))
        conn.execute(
            "INSERT INTO connectors (connector_id, token_sha256, created_at) VALUES (?, ?, ?)",
            (connector_id, token_hash, now),
        )
    return connector_id, token_plain


def authenticate_connector(conn: Connection, token_plain: str) -> str | None:
    row = _one(
        conn,
        "SELECT connector_id FROM connectors WHERE token_sha256 = ? AND revoked_at IS NULL",
        (_sha256(token_plain),),
    )
    return row["connector_id"] if row else None


def revoke_connector(conn: Connection, connector_id: str, now: str) -> None:
    cur = conn.execute(
        "UPDATE connectors SET revoked_at = ? WHERE connector_id = ? AND revoked_at IS NULL",
        (now, connector_id),
    )
    _require_rowcount(cur, f"connector {connector_id}")


def touch_connector(
    conn: Connection, connector_id: str, now: str, current_execution_id: str | None
) -> None:
    cur = conn.execute(
        "UPDATE connectors SET last_seen_at = ?, current_execution_id = ? WHERE connector_id = ?",
        (now, current_execution_id, connector_id),
    )
    _require_rowcount(cur, f"connector {connector_id}")


# --- 업무·선택 ---------------------------------------------------------------


def insert_task(conn: Connection, task: dict, now: str) -> None:
    """JSON 컬럼은 `required_capability`(Capability 로 검증)·`criteria`·`target` 로 받는다.
    선행 Task 는 같은 세션의 것만 허용한다 (ARCHITECTURE "선행 연결 변경 시 같은 소유 범위 검사")."""
    capability = Capability.model_validate(task["required_capability"]).model_dump()
    with _tx(conn):
        predecessor = task.get("predecessor_task_id")
        if predecessor is not None:
            prev = _one(conn, "SELECT session_id FROM tasks WHERE task_id = ?", (predecessor,))
            if prev is None or prev["session_id"] != task["session_id"]:
                raise NotFound(f"predecessor task {predecessor}")
        conn.execute(
            """
            INSERT INTO tasks (task_id, session_id, title, request, kind, required_capability_json,
              selection_mode, chosen_agent_id, run_mode, completion_mode, criteria_json,
              predecessor_task_id, revision, target_json, status, status_reason, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                task["task_id"], task["session_id"], task["title"], task["request"], task["kind"],
                json.dumps(capability, ensure_ascii=False), task["selection_mode"],
                task.get("chosen_agent_id"), task["run_mode"], task["completion_mode"],
                json.dumps(task["criteria"], ensure_ascii=False), predecessor, task["revision"],
                json.dumps(task["target"], ensure_ascii=False), task["status"],
                task["status_reason"], now,
            ),
        )


def get_task(conn: Connection, task_id: str) -> Row | None:
    return _one(conn, "SELECT * FROM tasks WHERE task_id = ?", (task_id,))


def list_tasks(conn: Connection, session_id: str | None) -> list[Row]:
    """`session_id` 가 None 이면 전체 (운영자)."""
    if session_id is None:
        return conn.execute("SELECT * FROM tasks ORDER BY created_at, task_id").fetchall()
    return conn.execute(
        "SELECT * FROM tasks WHERE session_id = ? ORDER BY created_at, task_id", (session_id,)
    ).fetchall()


def update_task_status(
    conn: Connection,
    task_id: str,
    status: str,
    reason: str,
    *,
    finished_at: str | None = None,
    review_decision: str | None = None,
) -> None:
    cur = conn.execute(
        "UPDATE tasks SET status = ?, status_reason = ?, "
        "finished_at = COALESCE(?, finished_at), review_decision = COALESCE(?, review_decision) "
        "WHERE task_id = ?",
        (status, reason, finished_at, review_decision, task_id),
    )
    _require_rowcount(cur, f"task {task_id}")


def update_task_choice(
    conn: Connection, task_id: str, *, chosen_agent_id: str, target: dict
) -> None:
    """직접 선택으로 전환. 선택된 agent 등록값에서 다시 만든 target 을 함께 고정한다."""
    cur = conn.execute(
        "UPDATE tasks SET selection_mode = 'manual', chosen_agent_id = ?, target_json = ? "
        "WHERE task_id = ?",
        (chosen_agent_id, json.dumps(target, ensure_ascii=False), task_id),
    )
    _require_rowcount(cur, f"task {task_id}")


def save_selection(conn: Connection, record: SelectionRecord) -> None:
    conn.execute(
        "INSERT INTO selection_records (task_id, record_json) VALUES (?, ?) "
        "ON CONFLICT(task_id) DO UPDATE SET record_json = excluded.record_json",
        (record.task_id, record.model_dump_json()),
    )


def get_selection(conn: Connection, task_id: str) -> SelectionRecord | None:
    row = _one(conn, "SELECT record_json FROM selection_records WHERE task_id = ?", (task_id,))
    return SelectionRecord.model_validate_json(row["record_json"]) if row else None


def successors_of(conn: Connection, task_id: str) -> list[Row]:
    return conn.execute(
        "SELECT * FROM tasks WHERE predecessor_task_id = ? ORDER BY created_at, task_id", (task_id,)
    ).fetchall()


def confirm_merge(conn: Connection, task_id: str, now: str) -> None:
    cur = conn.execute("UPDATE tasks SET merge_confirmed_at = ? WHERE task_id = ?", (now, task_id))
    _require_rowcount(cur, f"task {task_id}")


# --- 실행·이벤트 ---------------------------------------------------------------


def create_execution(
    conn: Connection,
    *,
    execution_id: str,
    task_id: str,
    attempt_no: int,
    start_key: str,
    agent_id: str,
    kind: str,
    request: ExecutionRequest,
    assigned_connector_id: str | None,
    predecessor_execution_id: str | None,
    now: str,
) -> None:
    """활성 잠금(`ux_executions_active`) 위반 → ActiveExecutionExists, `(task_id, start_key)` 중복 →
    DuplicateStartKey. 잠금이 우선한다 (sqlite 가 부분 인덱스를 먼저 검사한다)."""
    request_json = request.model_dump_json()
    with _tx(conn):
        try:
            conn.execute(
                """
                INSERT INTO executions (execution_id, task_id, attempt_no, start_key, agent_id, kind,
                  request_json, status, assigned_connector_id, predecessor_execution_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?, ?)
                """,
                (
                    execution_id, task_id, attempt_no, start_key, agent_id, kind, request_json,
                    assigned_connector_id, predecessor_execution_id, now,
                ),
            )
        except sqlite3.IntegrityError as exc:
            message = str(exc)
            if "start_key" in message:
                raise DuplicateStartKey(f"{task_id}/{start_key}") from exc
            if message.endswith("executions.task_id"):
                raise ActiveExecutionExists(task_id) from exc
            raise


def get_execution(conn: Connection, execution_id: str) -> Row | None:
    return _one(conn, "SELECT * FROM executions WHERE execution_id = ?", (execution_id,))


def active_execution(conn: Connection, task_id: str) -> Row | None:
    return _one(
        conn, "SELECT * FROM executions WHERE task_id = ? AND released_at IS NULL", (task_id,)
    )


def list_executions(conn: Connection, task_id: str) -> list[Row]:
    """Task 의 모든 시도. attempt_no 순 (해제된 것 포함)."""
    return conn.execute(
        "SELECT * FROM executions WHERE task_id = ? ORDER BY attempt_no", (task_id,)
    ).fetchall()


def claim_execution(conn: Connection, connector_id: str, now: str) -> Row | None:
    """이 connector 에 배정된 `queued` 실행 하나. 접수 확인(accepted) 전에는 같은 실행을 다시 준다.
    연결 프로그램당 실행 하나이므로 두 개가 queued 여도 먼저 내준 것을 유지한다. 상태는 바꾸지 않는다."""
    with _tx(conn):
        row = _one(
            conn,
            """
            SELECT e.* FROM executions e
            LEFT JOIN connectors c ON c.connector_id = e.assigned_connector_id
            WHERE e.assigned_connector_id = ? AND e.status = 'queued' AND e.released_at IS NULL
            ORDER BY (c.current_execution_id = e.execution_id) DESC, e.created_at, e.execution_id
            LIMIT 1
            """,
            (connector_id,),
        )
        if row is None:
            return None
        conn.execute(
            "UPDATE connectors SET current_execution_id = ?, last_seen_at = ? WHERE connector_id = ?",
            (row["execution_id"], now, connector_id),
        )
    return row


def _event_content(event: ExecutionEvent) -> dict:
    return {"type": event.type, "occurred_at": event.occurred_at, "data": event.data.model_dump()}


def append_event(
    conn: Connection, execution_id: str, event: ExecutionEvent, actor: str, now: str
) -> EventAck:
    """CONTRACT 3절 오류표. 저장·last_event_seq·상태 변경·시각 기록을 한 트랜잭션에서 처리한다.
    executions 의 `*_at` 은 서버 수신 시각(now), 발신 시각은 이벤트 행의 `occurred_at` 에 남는다."""
    if event.execution_id != execution_id:
        raise ValueError("event.execution_id 가 대상 실행과 다릅니다")
    data_json = json.dumps(event.data.model_dump(), ensure_ascii=False, sort_keys=True)
    with _tx(conn):
        row = get_execution(conn, execution_id)
        if row is None:
            raise NotFound(f"execution {execution_id}")
        existing = _one(
            conn,
            "SELECT type, occurred_at, data_json FROM execution_events "
            "WHERE execution_id = ? AND seq = ?",
            (execution_id, event.seq),
        )
        if existing is not None:
            same = (existing["type"], existing["occurred_at"], existing["data_json"]) == (
                event.type, event.occurred_at, data_json
            )
            if not same:
                raise EventConflict(event.seq)
            return EventAck(
                execution_id=execution_id, last_event_seq=row["last_event_seq"], status=row["status"]
            )
        expected = row["last_event_seq"] + 1
        if event.seq != expected:
            raise SequenceGap(expected)
        try:
            new_status = next_execution_status(row["status"], event.type)
        except domain_status.InvalidTransition as exc:
            raise InvalidTransition(row["status"], event_type=event.type) from exc

        updates: dict[str, object] = {"status": new_status, "last_event_seq": event.seq}
        if event.type == "accepted":
            updates["accepted_at"] = now
        elif event.type == "started":
            updates["started_at"] = now
        elif event.type == "result_ready":
            artifact_id = event.data.result_artifact_id
            owned = _one(
                conn,
                "SELECT 1 FROM artifacts WHERE artifact_id = ? AND execution_id = ?",
                (artifact_id, execution_id),
            )
            if owned is None:
                raise InvalidTransition(
                    row["status"], event_type=event.type, reason="result_artifact_missing"
                )
            updates["result_artifact_id"] = artifact_id
            updates["finished_at"] = now
        elif event.type == "failed":
            updates["failed_code"] = event.data.code
            updates["failed_message"] = event.data.message
            updates["process_stopped"] = int(event.data.process_stopped)
            updates["finished_at"] = now

        conn.execute(
            "INSERT INTO execution_events (execution_id, seq, type, occurred_at, received_at, "
            "data_json, actor) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (execution_id, event.seq, event.type, event.occurred_at, now, data_json, actor),
        )
        assignments = ", ".join(f"{col} = ?" for col in updates)
        conn.execute(
            f"UPDATE executions SET {assignments} WHERE execution_id = ?",
            (*updates.values(), execution_id),
        )
    return EventAck(execution_id=execution_id, last_event_seq=event.seq, status=new_status)


def list_events(conn: Connection, execution_id: str, after_seq: int = 0) -> list[Row]:
    return conn.execute(
        "SELECT * FROM execution_events WHERE execution_id = ? AND seq > ? ORDER BY seq",
        (execution_id, after_seq),
    ).fetchall()


def mark_unknown(conn: Connection, execution_id: str, kind: str, detail: str, now: str) -> None:
    """서버 관찰로 `unknown` 판정. 실행 주체의 seq 를 쓰지 않고 observation 행으로 남긴다."""
    with _tx(conn):
        row = get_execution(conn, execution_id)
        if row is None:
            raise NotFound(f"execution {execution_id}")
        if row["status"] in TERMINAL_STATUSES:
            raise InvalidTransition(row["status"])
        conn.execute(
            "UPDATE executions SET status = 'unknown' WHERE execution_id = ?", (execution_id,)
        )
        conn.execute(
            "INSERT INTO execution_observations (execution_id, observed_at, kind, detail) "
            "VALUES (?, ?, ?, ?)",
            (execution_id, now, kind, detail),
        )


def release_execution(conn: Connection, execution_id: str, now: str) -> None:
    """활성 잠금 해제. 이미 해제된 실행은 그대로 둔다. 시간 경과로는 호출하지 않는다."""
    with _tx(conn):
        if get_execution(conn, execution_id) is None:
            raise NotFound(f"execution {execution_id}")
        conn.execute(
            "UPDATE executions SET released_at = ? WHERE execution_id = ? AND released_at IS NULL",
            (now, execution_id),
        )


def executions_needing_attention(conn: Connection) -> list[Row]:
    """워커 스캔용: 활성이면서 최종 상태가 아닌 실행 (`unknown` 포함)."""
    placeholders = ", ".join("?" for _ in TERMINAL_STATUSES)
    return conn.execute(
        f"SELECT * FROM executions WHERE released_at IS NULL AND status NOT IN ({placeholders}) "
        "ORDER BY created_at, execution_id",
        TERMINAL_STATUSES,
    ).fetchall()


def get_verdict(conn: Connection, execution_id: str) -> Row | None:
    """실행에 대한 가장 최근 판정 (task_verdicts). 저장은 워커(Step 8)가 한다."""
    return _one(
        conn,
        "SELECT * FROM task_verdicts WHERE execution_id = ? ORDER BY decided_at DESC, rowid DESC LIMIT 1",
        (execution_id,),
    )


# --- 산출물 (CONTRACT 4절) ----------------------------------------------------


def _created(row: Row) -> ArtifactCreated:
    return ArtifactCreated(
        artifact_id=row["artifact_id"], kind=row["kind"], sha256=row["sha256"], size=row["size"]
    )


def store_artifact(
    conn: Connection,
    store: ArtifactStore,
    *,
    execution_id: str,
    session_id: str,
    meta: ArtifactMeta,
    data: bytes,
    now: str,
) -> tuple[ArtifactCreated, bool]:
    """해시·크기 불일치 → HashMismatch. 같은 `(execution_id, kind, sha256)` 은 (기존, False).
    해시 계산과 파일 쓰기는 트랜잭션 밖에서 한다. 파일은 내용 주소라 먼저 써도 안전하다."""
    sha256 = hashlib.sha256(data).hexdigest()
    if sha256 != meta.sha256 or len(data) != meta.size:
        raise HashMismatch(meta.name)
    _, store_ref = store.write(data)
    artifact_id = f"art-{secrets.token_hex(8)}"
    with _tx(conn):
        existing = _one(
            conn,
            "SELECT * FROM artifacts WHERE execution_id = ? AND kind = ? AND sha256 = ?",
            (execution_id, meta.kind, sha256),
        )
        if existing is not None:
            return _created(existing), False
        conn.execute(
            "INSERT INTO artifacts (artifact_id, execution_id, session_id, kind, name, content_type,"
            " sha256, size, store_ref, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                artifact_id, execution_id, session_id, meta.kind, meta.name, meta.content_type,
                sha256, meta.size, store_ref, now,
            ),
        )
    return (
        ArtifactCreated(artifact_id=artifact_id, kind=meta.kind, sha256=sha256, size=meta.size),
        True,
    )


def get_artifact(conn: Connection, artifact_id: str) -> Row | None:
    return _one(conn, "SELECT * FROM artifacts WHERE artifact_id = ?", (artifact_id,))


def read_artifact(conn: Connection, store: ArtifactStore, artifact_id: str) -> bytes:
    row = get_artifact(conn, artifact_id)
    if row is None:
        raise NotFound(f"artifact {artifact_id}")
    try:
        return store.read(row["store_ref"])
    except FileNotFoundError as exc:
        raise ArtifactMissing(artifact_id) from exc


def artifacts_of(conn: Connection, execution_id: str) -> list[Row]:
    return conn.execute(
        "SELECT * FROM artifacts WHERE execution_id = ? ORDER BY created_at, artifact_id",
        (execution_id,),
    ).fetchall()


def download_allowed(
    conn: Connection, store: ArtifactStore, execution_id: str, artifact_id: str
) -> bool:
    """실행의 `input_artifact_ids` 에 있거나, 그 입력 중 `handoff_bundle` manifest 의 attachments 에
    나열됐거나, 이 실행이 만든 산출물이면 True. manifest 를 읽어야 하므로 store 가 필요하다."""
    execution = get_execution(conn, execution_id)
    artifact = get_artifact(conn, artifact_id)
    if execution is None or artifact is None:
        return False
    if artifact["execution_id"] == execution_id:
        return True
    request = ExecutionRequest.model_validate_json(execution["request_json"])
    if artifact_id in request.input_artifact_ids:
        return True
    for input_id in request.input_artifact_ids:
        bundle_row = get_artifact(conn, input_id)
        if bundle_row is None or bundle_row["kind"] != "handoff_bundle":
            continue
        try:
            bundle = HandoffBundle.model_validate_json(store.read(bundle_row["store_ref"]))
        except (FileNotFoundError, ValueError):
            continue
        if any(a.artifact_id == artifact_id for a in bundle.attachments):
            return True
    return False


# --- 상한 (ARCHITECTURE 모델 호출 예산) ----------------------------------------


def count_diagnosis_started(conn: Connection, *, session_id: str | None, since: str) -> int:
    if session_id is None:
        row = _one(conn, "SELECT COUNT(*) FROM diagnosis_usage WHERE started_at >= ?", (since,))
    else:
        row = _one(
            conn,
            "SELECT COUNT(*) FROM diagnosis_usage WHERE started_at >= ? AND session_id = ?",
            (since, session_id),
        )
    return row[0]


def record_diagnosis_start(
    conn: Connection, session_id: str, execution_id: str, now: str
) -> None:
    conn.execute(
        "INSERT INTO diagnosis_usage (session_id, execution_id, started_at) VALUES (?, ?, ?)",
        (session_id, execution_id, now),
    )
