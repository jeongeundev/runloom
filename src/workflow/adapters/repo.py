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
from collections.abc import Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from sqlite3 import Connection, Row
from typing import Literal

from workflow.adapters.artifact_store import ArtifactStore
from workflow.adapters.errors import (
    ActiveExecutionExists,
    ArtifactMissing,
    DuplicateKind,
    DuplicateRule,
    DuplicateStartKey,
    EventConflict,
    HashMismatch,
    InvalidTransition,
    KindInUse,
    KindProtected,
    NotFound,
    RegistrationTaken,
    ResponseConflict,
    SequenceGap,
    StaleConfig,
    StaleRequest,
    TaskClosed,
)
from workflow.contracts.github import (
    AssigneeBinding,
    GitHubIssueSnapshot,
    GitHubSourceConfig,
    IssuePrLink,
    PullRequestRef,
    SourceDelivery,
    snapshot_digest,
)
from workflow.contracts.v1 import (
    BUILTIN_KIND_NAMES,
    BUILTIN_KINDS,
    BUILTIN_RULES,
    ArtifactCreated,
    ArtifactMeta,
    Capability,
    EventAck,
    ExecutionEvent,
    ExecutionRequest,
    ExecutionUsage,
    HandoffBundle,
    KindSpec,
    SelectionRecord,
    SuccessorRule,
)
from workflow.domain import status as domain_status
from workflow.domain.metrics import ExecutionFact, HumanRequestFact, MetricFacts, TaskEventFact, TaskFact
from workflow.domain.pull_request import head_branch
from workflow.domain.status import TERMINAL_STATUSES, next_execution_status
from workflow.domain.task_followup import FollowupTaskSpec
from workflow.domain.work_status import (
    TERMINAL_WORK_STATUSES,
    WORK_STATUSES,
    PullRequestFact,
    RequestFact,
    StageFact,
    WorkItemFacts,
    WorkStatus,
    work_status,
)

TOKEN_PREFIX = "wfc_"
SOURCE_TOKEN_PREFIX = "wfs_"


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
    """세션 생성과 함께 내장 종류(`BUILTIN_KINDS`)·내장 규칙(`BUILTIN_RULES`)을 이 세션에 seed 한다 (ADR-0009).
    이미 있는 세션에는 v4 → v5 마이그레이션(`db._seed_phase8_kinds`)이 phase 8 종류를 넣는다.
    첫 관리자·기본 매핑도 같은 트랜잭션에서 만든다(ADR-0020) — 기존 세션에는 v9 → v10 이 넣는다."""
    with _tx(conn):
        conn.execute("INSERT INTO sessions (session_id, created_at) VALUES (?, ?)", (session_id, now))
        for spec in BUILTIN_KINDS:
            _insert_kind_row(conn, session_id, spec, now)
        for rule in BUILTIN_RULES:
            _insert_rule_row(conn, session_id, rule, now)
        ensure_first_admin(conn, session_id, now=now)
        seed_default_field_mappings(conn, session_id, now=now)


# 새 워크스페이스의 기본 매핑 — GitHub 이슈는 라벨과 무관하게 지금처럼 bug_fix (ARCHITECTURE "매핑 표").
DEFAULT_FIELD_MAPPINGS = (("github", "kind", "*", "bug_fix"),)


def ensure_first_admin(conn: Connection, session_id: str, *, now: str) -> str:
    """워크스페이스의 첫 관리자 id. 없으면 만든다. 자체 BEGIN 이 없다 — `create_session`·마이그레이션이 부른다."""
    row = _one(conn, "SELECT member_id FROM members WHERE session_id = ? AND role = 'admin'"
                     " ORDER BY created_at, member_id LIMIT 1", (session_id,))
    if row is not None:
        return row["member_id"]
    member_id = f"mem-{secrets.token_hex(4)}"
    conn.execute("INSERT INTO members (member_id, session_id, display_name, role, created_at)"
                 " VALUES (?, ?, '관리자', 'admin', ?)", (member_id, session_id, now))
    return member_id


def seed_default_field_mappings(conn: Connection, session_id: str, *, now: str) -> None:
    """기본 매핑 행을 넣는다. 설정 번호는 올리지 않는다. 자체 BEGIN 이 없다."""
    for position, (source_type, field, source_value, runloom_value) in enumerate(DEFAULT_FIELD_MAPPINGS, start=1):
        conn.execute(
            "INSERT INTO field_mappings (mapping_id, session_id, source_type, field, source_value, runloom_value,"
            " position, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (f"map-{secrets.token_hex(4)}", session_id, source_type, field, source_value, runloom_value, position,
             now),
        )


def work_item_facts(conn: Connection, work_item_id: str) -> WorkItemFacts:
    """업무 상태 판정(`domain/work_status.work_status`)의 재료를 DB 에서 모은다. 사실을 모으는 SQL 은 여기 한 곳."""
    item = _one(conn, "SELECT status, status_reason, assignee_type FROM work_items WHERE work_item_id = ?",
                (work_item_id,))
    if item is None:
        raise NotFound(f"work item {work_item_id}")
    stages = conn.execute(
        "SELECT t.task_id, t.kind, COALESCE(json_extract(k.spec_json, '$.label'), t.kind) AS kind_label, t.status,"
        " t.status_reason, t.created_at, t.chosen_agent_id,"
        " EXISTS (SELECT 1 FROM executions e WHERE e.task_id = t.task_id) AS executed"
        " FROM tasks t JOIN kinds k ON k.session_id = t.session_id AND k.kind = t.kind"
        " WHERE t.work_item_id = ? ORDER BY t.created_at, t.task_id",
        (work_item_id,),
    ).fetchall()
    requests = conn.execute(
        "SELECT h.code, h.question FROM human_requests h JOIN tasks t ON t.task_id = h.task_id"
        " WHERE t.work_item_id = ? AND h.state = 'open' ORDER BY h.created_at, h.request_id",
        (work_item_id,),
    ).fetchall()
    pr = _one(conn, "SELECT p.state, p.pr_number FROM task_pull_requests p JOIN tasks t ON t.task_id = p.task_id"
                    " WHERE t.work_item_id = ? ORDER BY p.created_at DESC, p.task_id DESC LIMIT 1", (work_item_id,))
    # 지시 전 = 원본 소스가 all_open 이고 원본 이슈에 지시 기록이 없음
    undelegated = _one(
        conn,
        "SELECT 1 FROM source_issues si JOIN tasks t ON t.task_id = si.task_id"
        " JOIN github_sources gs ON gs.source_id = si.source_id"
        " WHERE t.work_item_id = ? AND json_extract(gs.config_json, '$.intake') = 'all_open'"
        " AND si.delegated_by IS NULL",
        (work_item_id,),
    )
    return WorkItemFacts(
        stored_status=item["status"],
        stored_reason=item["status_reason"],
        assigned=item["assignee_type"] is not None or any(s["chosen_agent_id"] for s in stages),
        delegated=undelegated is None,
        stages=tuple(
            StageFact(task_id=s["task_id"], kind=s["kind"], kind_label=s["kind_label"], status=s["status"],
                      status_reason=s["status_reason"], created_at=s["created_at"], executed=bool(s["executed"]))
            for s in stages
        ),
        open_requests=tuple(RequestFact(code=r["code"], question=r["question"]) for r in requests),
        pull_request=PullRequestFact(state=pr["state"], number=pr["pr_number"]) if pr else None,
    )


# --- 업무(WorkItem)·멤버 — ADR-0020 ---------------------------------------


def create_work_item(
    conn: Connection, session_id: str, *, title: str, request: str, kind: str, source_type: str,
    source_id: str | None = None, source_item_id: str | None = None, source_key: str | None = None,
    source_url: str | None = None, source_state: str | None = None, priority: str = "normal",
    assignee_type: str | None = None, assignee_id: str | None = None, form: dict | None = None, now: str,
) -> tuple[str, int]:
    """(work_item_id, key_number). 자체 BEGIN 이 없다 — 호출자가 연 `BEGIN IMMEDIATE` 트랜잭션 안에서 첫 단계와
    함께 부른다(밖이면 RuntimeError). 키 번호는 같은 트랜잭션에서 워크스페이스별 `MAX + 1` 이라 쓰기 잠금이
    동시 생성을 직렬화한다. 상태는 `새로 들어옴` 으로 시작하고 `refresh_work_status` 가 계산한다."""
    if not conn.in_transaction:
        raise RuntimeError("create_work_item 은 BEGIN IMMEDIATE 트랜잭션 안에서만 부른다")
    if get_kind(conn, session_id, kind) is None:
        raise NotFound(f"종류 {kind} 이 등록되지 않음")
    key_number = conn.execute(
        "SELECT COALESCE(MAX(key_number), 0) + 1 FROM work_items WHERE session_id = ?", (session_id,)
    ).fetchone()[0]
    work_item_id = f"wi-{secrets.token_hex(6)}"
    conn.execute(
        "INSERT INTO work_items (work_item_id, session_id, key_number, title, request, kind, priority, assignee_type,"
        " assignee_id, status, status_reason, source_type, source_id, source_item_id, source_key, source_url,"
        " source_state, form_json, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '', ?, ?, ?, ?, ?, ?,"
        " ?, ?, ?)",
        (work_item_id, session_id, key_number, title, request, kind, priority, assignee_type, assignee_id,
         WORK_STATUSES[0], source_type, source_id, source_item_id, source_key, source_url, source_state,
         json.dumps(form or {}, ensure_ascii=False), now, now),
    )
    return work_item_id, key_number


def get_work_item(conn: Connection, session_id: str, work_item_id: str) -> Row | None:
    return _one(conn, "SELECT * FROM work_items WHERE work_item_id = ? AND session_id = ?", (work_item_id, session_id))


def get_work_item_by_key(conn: Connection, session_id: str, key_number: int) -> Row | None:
    return _one(conn, "SELECT * FROM work_items WHERE session_id = ? AND key_number = ?", (session_id, key_number))


def work_item_of_task(conn: Connection, task_id: str) -> Row | None:
    """이 단계(Task)가 속한 업무."""
    return _one(conn, "SELECT w.* FROM work_items w JOIN tasks t ON t.work_item_id = w.work_item_id"
                      " WHERE t.task_id = ?", (task_id,))


def list_work_items(
    conn: Connection, session_id: str, *, include_closed: bool = True, status: str | None = None,
    assignee: tuple[str, str] | None = None,
) -> list[Row]:
    """워크스페이스의 업무(키 번호 내림차순 — 새 업무가 위). `assignee` 는 `(assignee_type, assignee_id)`."""
    where, params = ["session_id = ?"], [session_id]
    if not include_closed:
        where.append("closed_at IS NULL")
    if status is not None:
        where.append("status = ?")
        params.append(status)
    if assignee is not None:
        where.append("assignee_type = ? AND assignee_id = ?")
        params.extend(assignee)
    return conn.execute(
        f"SELECT * FROM work_items WHERE {' AND '.join(where)} ORDER BY key_number DESC", params
    ).fetchall()


def list_work_item_tasks(conn: Connection, work_item_id: str) -> list[Row]:
    """업무의 단계(Task) — 생성 순."""
    return conn.execute(
        "SELECT * FROM tasks WHERE work_item_id = ? ORDER BY created_at, task_id", (work_item_id,)
    ).fetchall()


def _work_item_event(conn: Connection, work_item_id: str, session_id: str, type: str, data: dict, now: str) -> None:
    conn.execute(
        "INSERT INTO work_item_events (work_item_id, session_id, type, config_revision, occurred_at, data_json)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (work_item_id, session_id, type, get_config_revision(conn, session_id), now,
         json.dumps(data, ensure_ascii=False)),
    )


def set_work_status(conn: Connection, work_item_id: str, status: WorkStatus, *, now: str) -> bool:
    """값 또는 이유가 저장값과 다를 때만 쓰고 `status_changed` 이벤트 한 행을 남긴다. 끝 상태가 되면 `closed_at`.
    자체 BEGIN 이 없다 — 그 변경과 같은 트랜잭션에서 부른다."""
    row = _one(conn, "SELECT session_id, status, status_reason FROM work_items WHERE work_item_id = ?",
               (work_item_id,))
    if row is None:
        raise NotFound(f"work item {work_item_id}")
    if (row["status"], row["status_reason"]) == (status.status, status.reason):
        return False
    closed_at = now if status.status in TERMINAL_WORK_STATUSES else None
    conn.execute(
        "UPDATE work_items SET status = ?, status_reason = ?, closed_at = ?, updated_at = ? WHERE work_item_id = ?",
        (status.status, status.reason, closed_at, now, work_item_id),
    )
    _work_item_event(conn, work_item_id, row["session_id"], "status_changed",
                     {"from": row["status"], "to": status.status, "reason": status.reason}, now)
    return True


def refresh_work_status(conn: Connection, work_item_id: str, *, now: str) -> bool:
    """사실을 모아(`work_item_facts`) 업무 상태를 다시 계산해 바뀌었을 때만 기록한다. 멱등, 자체 BEGIN 없음."""
    return set_work_status(conn, work_item_id, work_status(work_item_facts(conn, work_item_id)), now=now)


def assign_work_item(
    conn: Connection, session_id: str, work_item_id: str, *, assignee_type: str | None, assignee_id: str | None,
    now: str,
) -> bool:
    """담당을 바꾼다(None = 담당 없음). 멤버는 이 워크스페이스의 멤버, Agent 는 이 워크스페이스에 등록된 Agent 여야
    한다(아니면 NotFound). 바뀌면 `assigned` 이벤트, 같으면 False."""
    if (assignee_type is None) != (assignee_id is None) or assignee_type not in (None, "member", "agent"):
        raise ValueError(f"담당 {assignee_type!r}/{assignee_id!r}")
    with _tx(conn):
        row = get_work_item(conn, session_id, work_item_id)
        if row is None:
            raise NotFound(f"work item {work_item_id}")
        if assignee_type == "member" and _one(
            conn, "SELECT 1 FROM members WHERE member_id = ? AND session_id = ?", (assignee_id, session_id)
        ) is None:
            raise NotFound(f"member {assignee_id}")
        if assignee_type == "agent" and not is_session_agent(conn, session_id, assignee_id):
            raise NotFound(f"agent {assignee_id}")
        if (row["assignee_type"], row["assignee_id"]) == (assignee_type, assignee_id):
            return False
        conn.execute(
            "UPDATE work_items SET assignee_type = ?, assignee_id = ?, updated_at = ? WHERE work_item_id = ?",
            (assignee_type, assignee_id, now, work_item_id),
        )

        def who(type_: str | None, id_: str | None) -> dict | None:
            return None if type_ is None else {"type": type_, "id": id_}

        _work_item_event(conn, work_item_id, session_id, "assigned",
                         {"from": who(row["assignee_type"], row["assignee_id"]),
                          "to": who(assignee_type, assignee_id)}, now)
        return True


def link_work_items(
    conn: Connection, *, from_work_item_id: str, to_work_item_id: str, type: str,
    cause_execution_id: str | None = None, now: str,
) -> bool:
    """업무 사이 연결(앞 → 뒤). 이미 있으면 False. 두 업무가 같은 워크스페이스가 아니면 NotFound.
    자체 BEGIN 이 없다 — 연결을 만드는 변경과 같은 트랜잭션에서 부른다."""
    ends = [_one(conn, "SELECT session_id FROM work_items WHERE work_item_id = ?", (work_item_id,))
            for work_item_id in (from_work_item_id, to_work_item_id)]
    if None in ends or ends[0]["session_id"] != ends[1]["session_id"]:
        raise NotFound(f"work item {from_work_item_id} → {to_work_item_id}")
    cur = conn.execute(
        "INSERT OR IGNORE INTO work_item_links (from_work_item_id, to_work_item_id, type, cause_execution_id,"
        " created_at) VALUES (?, ?, ?, ?, ?)",
        (from_work_item_id, to_work_item_id, type, cause_execution_id, now),
    )
    return cur.rowcount == 1


def list_work_item_links(conn: Connection, work_item_id: str) -> list[Row]:
    """이 업무가 앞이든 뒤든 걸린 연결(생성 순)."""
    return conn.execute(
        "SELECT * FROM work_item_links WHERE from_work_item_id = ? OR to_work_item_id = ?"
        " ORDER BY created_at, from_work_item_id, to_work_item_id",
        (work_item_id, work_item_id),
    ).fetchall()


def list_work_item_events(conn: Connection, work_item_id: str) -> list[Row]:
    return conn.execute(
        "SELECT * FROM work_item_events WHERE work_item_id = ? ORDER BY id", (work_item_id,)
    ).fetchall()


def add_member(conn: Connection, session_id: str, *, display_name: str, role: str = "member", now: str) -> str:
    """멤버 추가(초대·로그인은 15-team). 없는 세션은 FK 오류."""
    member_id = f"mem-{secrets.token_hex(4)}"
    conn.execute("INSERT INTO members (member_id, session_id, display_name, role, created_at) VALUES (?, ?, ?, ?, ?)",
                 (member_id, session_id, display_name, role, now))
    return member_id


def list_members(conn: Connection, session_id: str) -> list[Row]:
    return conn.execute(
        "SELECT * FROM members WHERE session_id = ? ORDER BY created_at, member_id", (session_id,)
    ).fetchall()


def get_session(conn: Connection, session_id: str) -> Row | None:
    return _one(conn, "SELECT * FROM sessions WHERE session_id = ?", (session_id,))


def get_config_revision(conn: Connection, session_id: str) -> int:
    """워크스페이스 설정 번호 (ADR-0015). 실행·후속 연결에 찍는 값."""
    row = _one(conn, "SELECT config_revision FROM sessions WHERE session_id = ?", (session_id,))
    if row is None:
        raise NotFound(f"session {session_id}")
    return row["config_revision"]


def bump_config_revision(conn: Connection, session_id: str) -> int:
    """설정 번호 +1. 자체 BEGIN 이 없다 — 종류·규칙·GitHub 소스를 저장하는 호출자 트랜잭션 안에서만 부른다."""
    cur = conn.execute(
        "UPDATE sessions SET config_revision = config_revision + 1 WHERE session_id = ?", (session_id,)
    )
    _require_rowcount(cur, f"session {session_id}")
    return get_config_revision(conn, session_id)


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
    "demo_scripted": False,
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
          shared_to_all_sessions, demo_scripted)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(agent_id) DO UPDATE SET
          name = excluded.name, owner_scope = excluded.owner_scope,
          connection_type = excluded.connection_type, connector_id = excluded.connector_id,
          local_registration_id = excluded.local_registration_id,
          repository_id = excluded.repository_id, base_commit = excluded.base_commit,
          verification_profile_ids_json = excluded.verification_profile_ids_json,
          api_url = excluded.api_url, credential_ref = excluded.credential_ref,
          capabilities_json = excluded.capabilities_json, discovered_json = excluded.discovered_json,
          connection_state = excluded.connection_state, last_seen_at = excluded.last_seen_at,
          shared_to_all_sessions = excluded.shared_to_all_sessions,
          demo_scripted = excluded.demo_scripted
        """,
        (
            a["agent_id"], a["name"], a["owner_scope"], a["connection_type"], a["connector_id"],
            a["local_registration_id"], a["repository_id"], a["base_commit"],
            json.dumps(list(a["verification_profile_ids"])), a["api_url"], a["credential_ref"],
            json.dumps(caps, ensure_ascii=False), json.dumps(a["discovered"], ensure_ascii=False),
            a["connection_state"], a["last_seen_at"], int(bool(a["shared_to_all_sessions"])),
            int(bool(a["demo_scripted"])),
        ),
    )


def list_agents(conn: Connection) -> list[Row]:
    return conn.execute("SELECT * FROM agents ORDER BY agent_id").fetchall()


def get_agent(conn: Connection, agent_id: str) -> Row | None:
    return _one(conn, "SELECT * FROM agents WHERE agent_id = ?", (agent_id,))


def delete_agent(conn: Connection, agent_id: str) -> None:
    """세션 등록(`session_agents`)도 함께 지운다."""
    with _tx(conn):
        conn.execute("DELETE FROM session_agents WHERE agent_id = ?", (agent_id,))
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
        _fill_registration(conn, row["agent_id"], connector_id, repository_id, base_commit,
                           verification_profile_ids, discovered, now)
    return row["agent_id"]


def _fill_registration(
    conn: Connection, agent_id: str, connector_id: str, repository_id: str, base_commit: str,
    verification_profile_ids: list[str], discovered: dict, now: str,
) -> None:
    conn.execute(
        """
        UPDATE agents SET connector_id = ?, repository_id = ?, base_commit = ?,
          verification_profile_ids_json = ?, discovered_json = ?,
          connection_state = 'online', last_seen_at = ?
        WHERE agent_id = ?
        """,
        (
            connector_id, repository_id, base_commit, json.dumps(list(verification_profile_ids)),
            json.dumps(discovered, ensure_ascii=False), now, agent_id,
        ),
    )



def update_registration_heads(conn: Connection, connector_id: str, heads: Mapping[str, str]) -> int:
    """claim 때 보고된 기준 커밋(ADR-0018 결정 2)으로 이 연결 프로그램 Agent 의 `base_commit` 을 바꾼다.
    다른 연결 프로그램의 Agent·모르는 등록은 건드리지 않는다. 반환은 바뀐 Agent 수."""
    changed = 0
    with _tx(conn):
        for local_registration_id, commit in heads.items():
            changed += conn.execute(
                "UPDATE agents SET base_commit = ? WHERE connector_id = ? AND local_registration_id = ?",
                (commit, connector_id, local_registration_id),
            ).rowcount
    return changed


def register_local_agent(
    conn: Connection,
    *,
    connector_id: str,
    local_registration_id: str,
    agent_name: str | None,
    repository_id: str,
    base_commit: str,
    verification_profile_ids: list[str],
    discovered: dict,
    session_id: str,
    now: str,
) -> tuple[str, bool]:
    """러너 등록 (ADR-0018 결정 1). 반환 (agent_id, created).

    같은 `local_registration_id` 의 Agent 가 있으면 `update_registration` 과 같은 갱신(이름·소유 구분·능력 유지).
    없으면 `code.fix`·`code.review` 능력의 Agent 를 만들어 `session_id`(고정 워크스페이스)에 등록한다 — 한 트랜잭션.
    취소되지 않은 다른 연결 프로그램이 이미 쓰는 이름이면 RegistrationTaken."""
    with _tx(conn):
        row = _one(
            conn,
            "SELECT agent_id, connector_id FROM agents WHERE local_registration_id = ? ORDER BY agent_id",
            (local_registration_id,),
        )
        if row is not None and row["connector_id"] not in (None, connector_id):
            owner = _one(
                conn, "SELECT 1 FROM connectors WHERE connector_id = ? AND revoked_at IS NULL", (row["connector_id"],)
            )
            if owner is not None:
                raise RegistrationTaken(local_registration_id)
        if row is None:
            agent_id = f"agt-{secrets.token_hex(4)}"
            upsert_agent(conn, {
                "agent_id": agent_id,
                "name": agent_name or local_registration_id,
                "owner_scope": "personal",
                "connection_type": "local",
                "capabilities": [
                    {"code": "code.fix", "scope": {"repository_id": repository_id}},
                    {"code": "code.review", "scope": {"repository_id": repository_id}},
                ],
                "local_registration_id": local_registration_id,
            })
            register_session_agent(conn, session_id, agent_id, now)
        else:
            agent_id = row["agent_id"]
        _fill_registration(conn, agent_id, connector_id, repository_id, base_commit,
                           verification_profile_ids, discovered, now)
    return agent_id, row is None


# --- 업무 종류·후속 규칙 (phase 6, ADR-0009: 워크스페이스별 등록부) ---------------


def _insert_kind_row(conn: Connection, session_id: str, spec: KindSpec, now: str) -> None:
    conn.execute(
        "INSERT INTO kinds (session_id, kind, spec_json, created_at) VALUES (?, ?, ?, ?)",
        (session_id, spec.kind, spec.model_dump_json(), now),
    )


def _insert_rule_row(conn: Connection, session_id: str, rule: SuccessorRule, now: str) -> str:
    rule_id = f"rule-{secrets.token_hex(6)}"
    conn.execute(
        "INSERT INTO succession_rules (rule_id, session_id, from_kind, to_kind, rule_json, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (rule_id, session_id, rule.from_kind, rule.to_kind, rule.model_dump_json(), now),
    )
    return rule_id


def list_kinds(conn: Connection, session_id: str) -> list[KindSpec]:
    """내장 먼저(`BUILTIN_KINDS` 순), 그 다음 created_at·kind 순."""
    specs = [
        KindSpec.model_validate_json(r["spec_json"])
        for r in conn.execute(
            "SELECT spec_json FROM kinds WHERE session_id = ? ORDER BY created_at, kind", (session_id,)
        )
    ]
    builtin = sorted((s for s in specs if s.builtin), key=lambda s: BUILTIN_KIND_NAMES.index(s.kind))
    return builtin + [s for s in specs if not s.builtin]


def get_kind(conn: Connection, session_id: str, kind: str) -> KindSpec | None:
    row = _one(conn, "SELECT spec_json FROM kinds WHERE session_id = ? AND kind = ?", (session_id, kind))
    return KindSpec.model_validate_json(row["spec_json"]) if row else None


def insert_kind(conn: Connection, session_id: str, spec: KindSpec, now: str) -> None:
    """같은 kind 가 있으면 DuplicateKind. `validate_*` 검증은 서버 몫 — 여기서는 저장만 한다."""
    with _tx(conn):
        if get_kind(conn, session_id, spec.kind) is not None:
            raise DuplicateKind(spec.kind)
        _insert_kind_row(conn, session_id, spec, now)
        bump_config_revision(conn, session_id)


def delete_kind(conn: Connection, session_id: str, kind: str) -> None:
    """내장 → KindProtected. Task 가 쓰거나 규칙이 참조 → KindInUse. 없으면 NotFound."""
    with _tx(conn):
        spec = get_kind(conn, session_id, kind)
        if spec is None:
            raise NotFound(f"kind {kind}")
        if spec.builtin:
            raise KindProtected(kind)
        used_by_task = _one(
            conn, "SELECT 1 FROM tasks WHERE session_id = ? AND kind = ? LIMIT 1", (session_id, kind)
        )
        used_by_rule = _one(
            conn,
            "SELECT 1 FROM succession_rules WHERE session_id = ? AND (from_kind = ? OR to_kind = ?) LIMIT 1",
            (session_id, kind, kind),
        )
        if used_by_task is not None or used_by_rule is not None:
            raise KindInUse(kind)
        conn.execute("DELETE FROM kinds WHERE session_id = ? AND kind = ?", (session_id, kind))
        bump_config_revision(conn, session_id)


def list_rules(conn: Connection, session_id: str) -> list[tuple[str, SuccessorRule]]:
    """(rule_id, rule) 목록. created_at·rule_id 순."""
    return [
        (r["rule_id"], SuccessorRule.model_validate_json(r["rule_json"]))
        for r in conn.execute(
            "SELECT rule_id, rule_json FROM succession_rules WHERE session_id = ? "
            "ORDER BY created_at, rule_id",
            (session_id,),
        )
    ]


def get_rule(conn: Connection, session_id: str, from_kind: str, to_kind: str) -> SuccessorRule | None:
    row = _one(
        conn,
        "SELECT rule_json FROM succession_rules WHERE session_id = ? AND from_kind = ? AND to_kind = ?",
        (session_id, from_kind, to_kind),
    )
    return SuccessorRule.model_validate_json(row["rule_json"]) if row else None


def insert_rule(conn: Connection, session_id: str, rule: SuccessorRule, now: str) -> str:
    """rule_id 를 돌려준다. 같은 (from_kind, to_kind) → DuplicateRule. 종류가 세션에 없으면 NotFound."""
    with _tx(conn):
        for kind in (rule.from_kind, rule.to_kind):
            if get_kind(conn, session_id, kind) is None:
                raise NotFound(f"kind {kind}")
        if get_rule(conn, session_id, rule.from_kind, rule.to_kind) is not None:
            raise DuplicateRule(f"{rule.from_kind} → {rule.to_kind}")
        rule_id = _insert_rule_row(conn, session_id, rule, now)
        bump_config_revision(conn, session_id)
        return rule_id


def delete_rule(conn: Connection, session_id: str, rule_id: str) -> None:
    """내장 규칙도 삭제할 수 있다 — 팀이 버그 수정 → 커밋 검토를 잇지 않을 수 있다."""
    with _tx(conn):
        cur = conn.execute(
            "DELETE FROM succession_rules WHERE session_id = ? AND rule_id = ?", (session_id, rule_id)
        )
        _require_rowcount(cur, f"rule {rule_id}")
        bump_config_revision(conn, session_id)


# --- 세션 등록 (phase 5: 심사자 세션이 카탈로그에서 고른 Agent) ---------------


def register_session_agent(conn: Connection, session_id: str, agent_id: str, now: str) -> None:
    """멱등. 이미 등록돼 있으면 처음 `registered_at` 을 유지한다."""
    conn.execute(
        "INSERT OR IGNORE INTO session_agents (session_id, agent_id, registered_at) VALUES (?, ?, ?)",
        (session_id, agent_id, now),
    )


def unregister_session_agent(conn: Connection, session_id: str, agent_id: str) -> None:
    conn.execute(
        "DELETE FROM session_agents WHERE session_id = ? AND agent_id = ?", (session_id, agent_id)
    )


def list_session_agents(conn: Connection, session_id: str) -> list[Row]:
    """agents 행 + `registered_at`. 먼저 등록한 순서(같은 시각이면 삽입 순서)가 뒤 step 의 동률 규칙에 쓰인다."""
    return conn.execute(
        """
        SELECT a.*, sa.registered_at FROM session_agents sa JOIN agents a ON a.agent_id = sa.agent_id
        WHERE sa.session_id = ? ORDER BY sa.registered_at, sa.rowid
        """,
        (session_id,),
    ).fetchall()


def is_session_agent(conn: Connection, session_id: str, agent_id: str) -> bool:
    row = _one(
        conn,
        "SELECT 1 FROM session_agents WHERE session_id = ? AND agent_id = ?", (session_id, agent_id)
    )
    return row is not None


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


def record_supported_kinds(conn: Connection, connector_id: str, kinds: Sequence[str] | None) -> None:
    """마지막 claim 의 `supported_kinds` 선언. None(구버전 claim)이면 NULL 로 되돌린다. 없는 connector 는 NotFound."""
    cur = conn.execute(
        "UPDATE connectors SET supported_kinds_json = ? WHERE connector_id = ?",
        (None if kinds is None else json.dumps(list(kinds)), connector_id),
    )
    _require_rowcount(cur, f"connector {connector_id}")


def get_connector(conn: Connection, connector_id: str) -> Row | None:
    return _one(conn, "SELECT * FROM connectors WHERE connector_id = ?", (connector_id,))


# --- 입구 토큰 (phase 7, ADR-0010: 세션이 발급해 n8n 이 쓴다) ------------------------


def issue_source_token(
    conn: Connection, session_id: str, source: str, label: str, now: str
) -> tuple[str, str]:
    """(token_id, token_plain). 원문은 여기서만 만들어 돌려주고 DB 에는 sha256 만 남는다. 세션이 없으면 NotFound."""
    token_plain = SOURCE_TOKEN_PREFIX + secrets.token_urlsafe(32)
    token_id = f"src-{secrets.token_hex(4)}"
    with _tx(conn):
        if get_session(conn, session_id) is None:
            raise NotFound(f"session {session_id}")
        conn.execute(
            "INSERT INTO source_tokens (token_id, session_id, source, token_sha256, label, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (token_id, session_id, source, _sha256(token_plain), label, now),
        )
    return token_id, token_plain


def authenticate_source_token(conn: Connection, token_plain: str) -> Row | None:
    """취소되지 않은 토큰이면 그 행(token_id·session_id·source). 없으면 None. 원문을 로그·예외에 넣지 않는다."""
    return _one(
        conn,
        "SELECT token_id, session_id, source FROM source_tokens"
        " WHERE token_sha256 = ? AND revoked_at IS NULL",
        (_sha256(token_plain),),
    )


def touch_source_token(conn: Connection, token_id: str, now: str) -> None:
    cur = conn.execute(
        "UPDATE source_tokens SET last_used_at = ? WHERE token_id = ?", (now, token_id)
    )
    _require_rowcount(cur, f"source token {token_id}")


def revoke_source_token(conn: Connection, session_id: str, token_id: str, now: str) -> None:
    """같은 세션의 토큰만. 다른 세션이거나 없으면 NotFound. 이미 취소됐으면 그대로(멱등)."""
    with _tx(conn):
        row = _one(
            conn,
            "SELECT 1 FROM source_tokens WHERE token_id = ? AND session_id = ?",
            (token_id, session_id),
        )
        if row is None:
            raise NotFound(f"source token {token_id}")
        conn.execute(
            "UPDATE source_tokens SET revoked_at = ? WHERE token_id = ? AND revoked_at IS NULL",
            (now, token_id),
        )


def list_source_tokens(conn: Connection, session_id: str) -> list[Row]:
    """발급 순(같은 시각이면 삽입 순서), 취소된 것 포함. `/sources` 화면용."""
    return conn.execute(
        "SELECT * FROM source_tokens WHERE session_id = ? ORDER BY created_at, rowid", (session_id,)
    ).fetchall()


# --- 업무·선택 ---------------------------------------------------------------


def insert_task(conn: Connection, task: dict, now: str, *, work_item_id: str) -> None:
    """JSON 컬럼은 `required_capability`(Capability 로 검증)·`criteria`·`target` 로 받는다.
    종류는 이 세션의 등록부(`kinds`)에 있어야 한다 (없으면 NotFound — FK 오류를 기다리지 않는다).
    선행 Task 는 같은 세션의 것만 허용한다 (ARCHITECTURE "선행 연결 변경 시 같은 소유 범위 검사").
    `work_item_id` 는 이 Task 가 단계로 들어갈 같은 세션의 업무다(ADR-0020, 없으면 NotFound)."""
    with _tx(conn):
        _insert_task_row(conn, task, now, work_item_id=work_item_id)


def insert_work_item_task(
    conn: Connection, task: dict, now: str, *, source_type: str = "manual", source_id: str | None = None,
    source_item_id: str | None = None, source_key: str | None = None,
) -> str:
    """새 업무와 그 첫 단계 Task 를 한 트랜잭션에 만들고 업무 id 를 돌려준다. 제목·요청·종류는 Task 의 것,
    담당은 Task 에 고른 Agent 가 있으면 그 Agent(v9 → v10 마이그레이션과 같다). Task 가 실패하면 업무도 남지 않는다."""
    with _tx(conn):
        work_item_id = _work_item_for_task(conn, task, now, source_type=source_type, source_id=source_id,
                                           source_item_id=source_item_id, source_key=source_key)
        _insert_task_row(conn, task, now, work_item_id=work_item_id)
    return work_item_id


def _work_item_for_task(conn: Connection, task: dict, now: str, **source) -> str:
    agent = task.get("chosen_agent_id")
    work_item_id, _ = create_work_item(
        conn, task["session_id"], title=task["title"], request=task["request"], kind=task["kind"],
        assignee_type="agent" if agent else None, assignee_id=agent, now=now, **source,
    )
    return work_item_id


def _insert_task_row(conn: Connection, task: dict, now: str, *, work_item_id: str) -> None:
    """`insert_task` 의 트랜잭션 안쪽 — 원본 이슈·후속 Task 를 매핑 행과 한 트랜잭션에 넣을 때 쓴다."""
    work = _one(conn, "SELECT session_id FROM work_items WHERE work_item_id = ?", (work_item_id,))
    if work is None or work["session_id"] != task["session_id"]:
        raise NotFound(f"work item {work_item_id}")
    capability = Capability.model_validate(task["required_capability"]).model_dump()
    if get_kind(conn, task["session_id"], task["kind"]) is None:
        raise NotFound(f"종류 {task['kind']} 이 등록되지 않음")
    predecessor = task.get("predecessor_task_id")
    if predecessor is not None:
        prev = _one(conn, "SELECT session_id FROM tasks WHERE task_id = ?", (predecessor,))
        if prev is None or prev["session_id"] != task["session_id"]:
            raise NotFound(f"predecessor task {predecessor}")
    conn.execute(
        """
        INSERT INTO tasks (task_id, session_id, title, request, kind, required_capability_json,
          selection_mode, chosen_agent_id, run_mode, completion_mode, criteria_json,
          predecessor_task_id, revision, target_json, status, status_reason, created_at,
          chain_id, source_ref, work_item_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            task["task_id"], task["session_id"], task["title"], task["request"], task["kind"],
            json.dumps(capability, ensure_ascii=False), task["selection_mode"],
            task.get("chosen_agent_id"), task["run_mode"], task["completion_mode"],
            json.dumps(task["criteria"], ensure_ascii=False), predecessor, task["revision"],
            json.dumps(task["target"], ensure_ascii=False), task["status"],
            task["status_reason"], now, task.get("chain_id"), task.get("source_ref"), work_item_id,
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
    now: str,
) -> None:
    """상태가 실제로 바뀌거나 운영자 검토 결정(`review_decision`)이 주어지면 같은 트랜잭션에 `status_changed`."""
    with _tx(conn):
        previous = _status_of(conn, task_id)
        conn.execute(
            "UPDATE tasks SET status = ?, status_reason = ?, "
            "finished_at = COALESCE(?, finished_at), review_decision = COALESCE(?, review_decision) "
            "WHERE task_id = ?",
            (status, reason, finished_at, review_decision, task_id),
        )
        _status_changed(conn, task_id, previous, status, reason, now, review_decision=review_decision)


# --- 업무 이벤트 (phase 9, ADR-0015 — 추가 전용 task_events) -----------------------


def _status_of(conn: Connection, task_id: str) -> str:
    row = _one(conn, "SELECT status FROM tasks WHERE task_id = ?", (task_id,))
    if row is None:
        raise NotFound(f"task {task_id}")
    return row["status"]


def _status_changed(
    conn: Connection, task_id: str, previous: str, status: str, reason: str, now: str,
    *, review_decision: str | None = None,
) -> None:
    """상태 UPDATE 와 같은 트랜잭션에서. 상태가 같고 검토 결정도 없으면(사유 문구만 바뀜) 쓰지 않는다."""
    if previous == status and review_decision is None:
        return
    data = {"from": previous, "to": status, "reason": reason, "review_decision": review_decision}
    append_task_event(conn, task_id=task_id, type="status_changed", data=data, now=now)


def append_task_event(conn: Connection, *, task_id: str, type: str, data: dict, now: str) -> bool:
    """세션·`task_revision`·현재 `config_revision` 은 DB 에서 읽는다. 자체 BEGIN 이 없다 — 호출자 트랜잭션 안에서 쓴다.
    `blocked` 는 그 Task 의 최근 `blocked`·`ready` 가 같은 목록의 `blocked` 면, `ready` 는 최근 행이 `ready` 면
    쓰지 않는다 (ARCHITECTURE "이벤트 기록 규칙"). 썼으면 True."""
    task = _one(
        conn,
        "SELECT t.session_id, t.revision, s.config_revision FROM tasks t"
        " JOIN sessions s ON s.session_id = t.session_id WHERE t.task_id = ?",
        (task_id,),
    )
    if task is None:
        raise NotFound(f"task {task_id}")
    data_json = json.dumps(data, ensure_ascii=False, sort_keys=True)
    if type == "blocked":
        last = _one(
            conn,
            "SELECT type, data_json FROM task_events WHERE task_id = ? AND type IN ('blocked', 'ready')"
            " ORDER BY id DESC LIMIT 1",
            (task_id,),
        )
        if last is not None and (last["type"], last["data_json"]) == ("blocked", data_json):
            return False
    elif type == "ready":
        last = _one(conn, "SELECT type FROM task_events WHERE task_id = ? ORDER BY id DESC LIMIT 1", (task_id,))
        if last is not None and last["type"] == "ready":
            return False
    conn.execute(
        "INSERT INTO task_events (task_id, session_id, type, task_revision, config_revision, occurred_at, data_json)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (task_id, task["session_id"], type, task["revision"], task["config_revision"], now, data_json),
    )
    return True


def record_blocked(conn: Connection, task_id: str, blockers: list[dict], *, now: str) -> bool:
    """워커의 준비 판정 대기 기록 — `append_task_event` 를 자체 트랜잭션으로 감싼다. `blockers` 는 `{"code", "actor"}`."""
    with _tx(conn):
        return append_task_event(conn, task_id=task_id, type="blocked", data={"blockers": blockers}, now=now)


def list_task_events(conn: Connection, task_id: str) -> list[Row]:
    return conn.execute("SELECT * FROM task_events WHERE task_id = ? ORDER BY id", (task_id,)).fetchall()


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


def tasks_with_ready_predecessor(conn: Connection) -> list[Row]:
    """워커 후속 스캔용. 마감되지 않은 Task 중 선행 Task 가 (a) `완료` 로 마감됐거나 (b) 활성 실행이
    `result_ready` 이고 task_verdicts 에 그 실행의 판정이 있는 것. 선행이 `실패` 로 마감된 Task 는 제외한다.
    판정 통과·outcome 일치는 워커가 `predecessor_ready_execution` 으로 읽어 판단한다 (ADR-0009)."""
    return conn.execute(
        """
        SELECT t.* FROM tasks t JOIN tasks p ON p.task_id = t.predecessor_task_id
        WHERE t.finished_at IS NULL AND p.status != '실패'
          AND (
            (p.status = '완료' AND p.finished_at IS NOT NULL)
            OR EXISTS (
              SELECT 1 FROM executions e
              WHERE e.task_id = p.task_id AND e.released_at IS NULL AND e.status = 'result_ready'
                AND EXISTS (SELECT 1 FROM task_verdicts v WHERE v.execution_id = e.execution_id)
            )
          )
        ORDER BY t.created_at, t.task_id
        """
    ).fetchall()


def predecessor_ready_execution(conn: Connection, task_id: str) -> Row | None:
    """`task_id`(선행 Task) 를 '준비'시킨 실행 — `result_ready` 이고 `result_artifact_id` 가 있으며
    판정이 기록된 가장 최근 시도. 해제된 시도도 포함한다(선행이 이미 `완료` 된 경우). 없으면 None."""
    return _one(
        conn,
        """
        SELECT e.* FROM executions e
        WHERE e.task_id = ? AND e.status = 'result_ready' AND e.result_artifact_id IS NOT NULL
          AND EXISTS (SELECT 1 FROM task_verdicts v WHERE v.execution_id = e.execution_id)
        ORDER BY e.attempt_no DESC LIMIT 1
        """,
        (task_id,),
    )


# --- Chain (phase 5: "업무 가져오기" 로 만든 Task 묶음. phase 7: 입구 API 의 callback) -----


def insert_chain(conn: Connection, chain: dict, now: str) -> None:
    """키: chain_id, session_id, title, source, skipped(list[dict], 기본 []).
    선택 키 `callback_url`(str|None)·`items`(list|None → items_json, n8n 이 보낸 항목 원문). 없으면 NULL."""
    items = chain.get("items")
    conn.execute(
        "INSERT INTO chains (chain_id, session_id, title, source, skipped_json, created_at,"
        " callback_url, items_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            chain["chain_id"], chain["session_id"], chain["title"], chain["source"],
            json.dumps(list(chain.get("skipped", [])), ensure_ascii=False), now,
            chain.get("callback_url"),
            None if items is None else json.dumps(list(items), ensure_ascii=False),
        ),
    )


def get_chain(conn: Connection, chain_id: str) -> Row | None:
    return _one(conn, "SELECT * FROM chains WHERE chain_id = ?", (chain_id,))


def list_chains(conn: Connection, session_id: str) -> list[Row]:
    return conn.execute(
        "SELECT * FROM chains WHERE session_id = ? ORDER BY created_at, chain_id", (session_id,)
    ).fetchall()


def mark_chain_started(conn: Connection, chain_id: str, now: str) -> None:
    """`started_at` 이 NULL 일 때만 기록한다. 없는 chain 은 NotFound."""
    with _tx(conn):
        if get_chain(conn, chain_id) is None:
            raise NotFound(f"chain {chain_id}")
        conn.execute(
            "UPDATE chains SET started_at = ? WHERE chain_id = ? AND started_at IS NULL", (now, chain_id)
        )


def tasks_of_chain(conn: Connection, chain_id: str) -> list[Row]:
    """predecessor 체인 순서: 선행이 없거나 체인 밖인 Task 부터, 그 Task 를 선행으로 갖는 Task 순.
    created_at·task_id 는 동률(같은 선행을 가진 Task 들)에서만 순서를 정한다."""
    rows = conn.execute(
        "SELECT * FROM tasks WHERE chain_id = ? ORDER BY created_at, task_id", (chain_id,)
    ).fetchall()
    in_chain = {r["task_id"] for r in rows}
    successors: dict[str | None, list[Row]] = {}
    for r in rows:
        pred = r["predecessor_task_id"]
        successors.setdefault(pred if pred in in_chain else None, []).append(r)
    ordered: list[Row] = []
    stack = list(reversed(successors.get(None, [])))
    while stack:
        r = stack.pop()
        ordered.append(r)
        stack.extend(reversed(successors.get(r["task_id"], [])))
    return ordered


def chains_awaiting_callback(conn: Connection, now: str, *, max_attempts: int) -> list[Row]:
    """callback_url 이 있고 callback_sent_at 이 NULL 이고 attempts < max_attempts 이고
    (next_at IS NULL OR next_at <= now) 인 체인. created_at 순. 세션을 가리지 않는다 — 워커가 전체를 본다."""
    return conn.execute(
        """
        SELECT * FROM chains
        WHERE callback_url IS NOT NULL AND callback_sent_at IS NULL AND callback_attempts < ?
          AND (callback_next_at IS NULL OR callback_next_at <= ?)
        ORDER BY created_at, chain_id
        """,
        (max_attempts, now),
    ).fetchall()


def record_callback_attempt(
    conn: Connection, chain_id: str, *, ok: bool, error: str | None, now: str, next_at: str | None
) -> None:
    """ok 면 callback_sent_at = now, last_error = NULL. 아니면 attempts + 1, last_error = error,
    next_at = next_at. 없는 체인은 NotFound."""
    if ok:
        cur = conn.execute(
            "UPDATE chains SET callback_sent_at = ?, callback_last_error = NULL WHERE chain_id = ?",
            (now, chain_id),
        )
    else:
        cur = conn.execute(
            "UPDATE chains SET callback_attempts = callback_attempts + 1, callback_last_error = ?,"
            " callback_next_at = ? WHERE chain_id = ?",
            (error, next_at, chain_id),
        )
    _require_rowcount(cur, f"chain {chain_id}")


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
    release_execution_id: str | None = None,
    ready: bool = False,
) -> None:
    """마감된 Task → TaskClosed, 활성 잠금(`ux_executions_active`) 위반 → ActiveExecutionExists, `(task_id, start_key)` 중복 →
    DuplicateStartKey. 잠금이 우선한다 (sqlite 가 부분 인덱스를 먼저 검사한다).
    `release_execution_id` 를 주면 그 이전 시도의 잠금 해제와 새 시도 생성을 한 트랜잭션에서 한다 — 새 시도가
    거부되면 해제도 되돌린다(워커의 재작업·검토 재연결). `ready` 면 같은 트랜잭션에 `ready` 이벤트(업무 순환 착수)."""
    request_json = request.model_dump_json()
    with _tx(conn):
        closed = _one(conn, "SELECT finished_at FROM tasks WHERE task_id = ?", (task_id,))
        if closed is not None and closed["finished_at"] is not None:
            raise TaskClosed(task_id)  # 종료와 착수가 겹쳐도 마감된 Task 에 실행을 붙이지 않는다
        if release_execution_id is not None:
            conn.execute(
                "UPDATE executions SET released_at = ? WHERE execution_id = ? AND task_id = ? AND released_at IS NULL",
                (now, release_execution_id, task_id),
            )
        try:
            conn.execute(
                """
                INSERT INTO executions (execution_id, task_id, attempt_no, start_key, agent_id, kind,
                  request_json, status, assigned_connector_id, predecessor_execution_id, created_at,
                  config_revision)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?, ?,
                  (SELECT s.config_revision FROM sessions s JOIN tasks t ON t.session_id = s.session_id
                   WHERE t.task_id = ?))
                """,
                (
                    execution_id, task_id, attempt_no, start_key, agent_id, kind, request_json,
                    assigned_connector_id, predecessor_execution_id, now, task_id,
                ),
            )
        except sqlite3.IntegrityError as exc:
            message = str(exc)
            if "start_key" in message:
                raise DuplicateStartKey(f"{task_id}/{start_key}") from exc
            if message.endswith("executions.task_id"):
                raise ActiveExecutionExists(task_id) from exc
            raise
        if ready:
            data = {"execution_id": execution_id, "agent_id": agent_id, "start_key": start_key}
            append_task_event(conn, task_id=task_id, type="ready", data=data, now=now)


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


def _usage_columns(usage: ExecutionUsage | None) -> dict[str, object]:
    """모르는 값은 NULL 그대로 — 0 으로 채우지 않는다 (ADR-0015)."""
    if usage is None:
        return {"cost_usd": None, "input_tokens": None, "output_tokens": None}
    return {"cost_usd": usage.cost_usd, "input_tokens": usage.input_tokens, "output_tokens": usage.output_tokens}


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
            updates["folder_commit"] = event.data.folder_commit
            updates["folder_dirty"] = None if event.data.folder_dirty is None else int(event.data.folder_dirty)
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
            updates.update(_usage_columns(event.data.usage))
            pushed = event.data.branch_pushed
            updates["branch_pushed"] = None if pushed is None else int(pushed)
        elif event.type == "failed":
            updates["failed_code"] = event.data.code
            updates["failed_message"] = event.data.message
            updates["process_stopped"] = int(event.data.process_stopped)
            updates["finished_at"] = now
            updates.update(_usage_columns(event.data.usage))

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


def executions_by(
    conn: Connection, *, statuses: tuple[str, ...], kind: str | None = None
) -> list[Row]:
    """워커 스캔용: 활성(`released_at IS NULL`)이면서 주어진 상태인 실행. `kind` 로 좁힐 수 있다."""
    placeholders = ", ".join("?" for _ in statuses)
    sql = f"SELECT * FROM executions WHERE released_at IS NULL AND status IN ({placeholders})"
    params: tuple = tuple(statuses)
    if kind is not None:
        sql += " AND kind = ?"
        params += (kind,)
    return conn.execute(sql + " ORDER BY created_at, execution_id", params).fetchall()


def busy_executions(
    conn: Connection, local_registration_id: str, kinds: Sequence[str], *, exclude_task_id: str
) -> list[str]:
    """같은 로컬 등록(같은 저장소 작업 트리)에서 아직 도는 — 결과·실패 전인 — `kinds` 실행 ID. 준비 판정의
    `repository_busy` 재료다. `result_ready`(검토·사람 대기)는 저장소를 쓰지 않으므로 세지 않는다."""
    kind_marks = ", ".join("?" for _ in kinds)
    rows = conn.execute(
        f"""
        SELECT e.execution_id FROM executions e JOIN agents a ON a.agent_id = e.agent_id
        WHERE e.released_at IS NULL AND e.status IN ('queued', 'accepted', 'running', 'unknown')
          AND a.local_registration_id = ? AND e.kind IN ({kind_marks}) AND e.task_id != ?
        ORDER BY e.created_at, e.execution_id
        """,
        (local_registration_id, *kinds, exclude_task_id),
    ).fetchall()
    return [r["execution_id"] for r in rows]


def results_awaiting_verdict(conn: Connection, kind: str | None = None) -> list[Row]:
    """`result_ready` 인 활성 실행 중 판정(task_verdicts) 기록이 없는 것. 워커가 한 번씩만 판정한다.
    `kind` 가 None 이면 모든 종류."""
    sql = """
        SELECT e.* FROM executions e
        WHERE e.released_at IS NULL AND e.status = 'result_ready'
          AND NOT EXISTS (SELECT 1 FROM task_verdicts v WHERE v.execution_id = e.execution_id)
    """
    params: tuple = ()
    if kind is not None:
        sql += " AND e.kind = ?"
        params = (kind,)
    return conn.execute(sql + " ORDER BY e.created_at, e.execution_id", params).fetchall()


def fail_execution(conn: Connection, execution_id: str, *, code: str, message: str, now: str) -> None:
    """실행 주체의 이벤트 없이 서버가 `failed` 로 확정 — 접수 거부(4xx)·첨부 상한 초과.
    `mark_unknown` 처럼 seq 를 소비하지 않는다. 프로세스가 남아 있지 않으므로 `process_stopped=1`."""
    with _tx(conn):
        row = get_execution(conn, execution_id)
        if row is None:
            raise NotFound(f"execution {execution_id}")
        if row["status"] in TERMINAL_STATUSES:
            raise InvalidTransition(row["status"])
        conn.execute(
            "UPDATE executions SET status = 'failed', failed_code = ?, failed_message = ?, "
            "process_stopped = 1, finished_at = ? WHERE execution_id = ?",
            (code, message, now, execution_id),
        )


def record_observation(
    conn: Connection, execution_id: str, kind: str, detail: str, now: str
) -> None:
    """상태를 바꾸지 않는 서버 관찰 (예: 실행 중 heartbeat 상실). 재실행 근거가 아니다."""
    if get_execution(conn, execution_id) is None:
        raise NotFound(f"execution {execution_id}")
    conn.execute(
        "INSERT INTO execution_observations (execution_id, observed_at, kind, detail) "
        "VALUES (?, ?, ?, ?)",
        (execution_id, now, kind, detail),
    )


def observations_of(conn: Connection, execution_id: str) -> list[Row]:
    return conn.execute(
        "SELECT * FROM execution_observations WHERE execution_id = ? ORDER BY observed_at, id",
        (execution_id,),
    ).fetchall()


def finish_task(
    conn: Connection, *, task_id: str, execution_id: str, status: str, reason: str, now: str
) -> None:
    """Task 마감(`finished_at`)과 활성 잠금 해제를 한 트랜잭션에서. 워커의 `완료`(자동 판정)·`실패`(종료 확인) 용."""
    with _tx(conn):
        previous = _status_of(conn, task_id)
        conn.execute(
            "UPDATE tasks SET status = ?, status_reason = ?, finished_at = ? WHERE task_id = ?",
            (status, reason, now, task_id),
        )
        _status_changed(conn, task_id, previous, status, reason, now)
        conn.execute(
            "UPDATE executions SET released_at = ? WHERE execution_id = ? AND released_at IS NULL",
            (now, execution_id),
        )


def record_verdict(
    conn: Connection,
    *,
    task_id: str,
    execution_id: str,
    verdict: dict,
    status: str,
    reason: str,
    finish: bool,
    now: str,
) -> None:
    """판정 저장 + Task 상태를 한 트랜잭션에서. `finish` 면 마감·잠금 해제까지 (A 자동 완료)."""
    with _tx(conn):
        conn.execute(
            "INSERT INTO task_verdicts (task_id, execution_id, verdict_json, decided_at) "
            "VALUES (?, ?, ?, ?)",
            (task_id, execution_id, json.dumps(verdict, ensure_ascii=False), now),
        )
        previous = _status_of(conn, task_id)
        conn.execute(
            "UPDATE tasks SET status = ?, status_reason = ?, "
            "finished_at = CASE WHEN ? THEN ? ELSE finished_at END WHERE task_id = ?",
            (status, reason, int(finish), now, task_id),
        )
        _status_changed(conn, task_id, previous, status, reason, now)
        if finish:
            conn.execute(
                "UPDATE executions SET released_at = ? WHERE execution_id = ? AND released_at IS NULL",
                (now, execution_id),
            )


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


def artifacts_of_kinds(conn: Connection, execution_id: str, kinds: Sequence[str]) -> list[Row]:
    """실행의 산출물 중 `kinds` 에 든 것. 인계 조립이 규칙 `handoff_kinds` 로 모을 때 쓴다."""
    if not kinds:
        return []
    placeholders = ", ".join("?" for _ in kinds)
    return conn.execute(
        f"SELECT * FROM artifacts WHERE execution_id = ? AND kind IN ({placeholders}) "
        "ORDER BY created_at, artifact_id",
        (execution_id, *kinds),
    ).fetchall()


def download_allowed(
    conn: Connection, store: ArtifactStore, execution_id: str, artifact_id: str
) -> bool:
    """실행의 `input_artifact_ids` 에 있거나, 그 입력 중 `handoff_bundle` manifest 의
    `source_result_artifact_id`·`inputs`·`attachments` 에 나열됐거나, 이 실행이 만든 산출물이면 True.
    manifest 를 읽어야 하므로 store 가 필요하다."""
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
        if artifact_id == bundle.source_result_artifact_id:
            return True
        if any(i.artifact_id == artifact_id for i in bundle.inputs):
            return True
        if any(a.artifact_id == artifact_id for a in bundle.attachments):
            return True
    return False


# --- GitHub 업무 순환 (phase 8, ADR-0014: 소스·담당 연결·원본 매핑·후속 원인·사람 요청·반영 outbox) ------------
# 세션 소유: source → github_sources.session_id, Task → tasks.session_id. 다른 세션이면 NotFound(또는 None).


def _source_row(conn: Connection, session_id: str, source_id: str) -> Row:
    row = _one(
        conn, "SELECT * FROM github_sources WHERE source_id = ? AND session_id = ?", (source_id, session_id)
    )
    if row is None:
        raise NotFound(f"source {source_id}")
    return row


def save_github_source(
    conn: Connection, session_id: str, config: GitHubSourceConfig, now: str, *, expected_revision: int | None = None
) -> None:
    """저장 또는 교체. 수집 커서는 유지한다. 다른 세션의 source_id → NotFound, 같은 세션에 같은 저장소 → IntegrityError.
    `expected_revision` 을 주면 기존 소스만 갱신하고, 저장된 `config_revision` 이 다르면 같은 트랜잭션에서 StaleConfig.
    `WORKFLOW_GITHUB_REPOS` 허용은 서버 몫."""
    with _tx(conn):
        owner = _one(
            conn, "SELECT session_id, config_json FROM github_sources WHERE source_id = ?", (config.source_id,)
        )
        if owner is not None and owner["session_id"] != session_id:
            raise NotFound(f"source {config.source_id}")
        if expected_revision is not None:
            if owner is None:
                raise NotFound(f"source {config.source_id}")
            current = GitHubSourceConfig.model_validate_json(owner["config_json"]).config_revision
            if current != expected_revision:
                raise StaleConfig(config.source_id, current)
        conn.execute(
            "INSERT INTO github_sources (source_id, session_id, repository_full_name, config_json, created_at,"
            " updated_at) VALUES (?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(source_id) DO UPDATE SET repository_full_name = excluded.repository_full_name,"
            " config_json = excluded.config_json, updated_at = excluded.updated_at",
            (config.source_id, session_id, config.repository_full_name, config.model_dump_json(), now, now),
        )
        bump_config_revision(conn, session_id)


def get_github_source(conn: Connection, session_id: str, source_id: str) -> GitHubSourceConfig | None:
    row = _one(
        conn, "SELECT config_json FROM github_sources WHERE source_id = ? AND session_id = ?",
        (source_id, session_id),
    )
    return GitHubSourceConfig.model_validate_json(row["config_json"]) if row else None


def list_github_sources(conn: Connection, session_id: str) -> list[GitHubSourceConfig]:
    return [
        GitHubSourceConfig.model_validate_json(r["config_json"])
        for r in conn.execute(
            "SELECT config_json FROM github_sources WHERE session_id = ? ORDER BY created_at, rowid", (session_id,)
        )
    ]


def github_source_session(conn: Connection, source_id: str) -> str | None:
    """소스를 가진 세션. 수집기(워커)가 source_id 로 소유 세션을 찾을 때 쓴다."""
    row = _one(conn, "SELECT session_id FROM github_sources WHERE source_id = ?", (source_id,))
    return row["session_id"] if row else None


def github_source_sessions(conn: Connection) -> list[str]:
    """GitHub 소스를 가진 세션들. 운영자 API 는 이것이 한 세션뿐이도록 지킨다(셀프호스트 1개 워크스페이스)."""
    return [r[0] for r in conn.execute("SELECT DISTINCT session_id FROM github_sources ORDER BY session_id")]


def replace_baseline(
    conn: Connection, session_id: str, source_id: str, items: Sequence[IssuePrLink], *, opened_before: str, now: str
) -> int:
    """소스의 기준선 전체 교체 + 가져오기 기록, 한 트랜잭션 (ADR-0015). 다시 불러도 같은 결과 — 멱등.
    설정이 아니라 관측 이력이므로 `config_revision` 은 올리지 않는다. 다른 세션 소스 → NotFound."""
    with _tx(conn):
        _source_row(conn, session_id, source_id)
        conn.execute("DELETE FROM baseline_items WHERE source_id = ?", (source_id,))
        conn.executemany(
            "INSERT INTO baseline_items (source_id, issue_number, issue_title, issue_opened_at, pr_number,"
            " pr_merged_at, fetched_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                (source_id, i.issue_number, i.issue_title, i.issue_opened_at, i.pr_number, i.pr_merged_at, now)
                for i in items
            ],
        )
        conn.execute(
            "INSERT INTO baseline_imports (source_id, opened_before, fetched_at, item_count) VALUES (?, ?, ?, ?)"
            " ON CONFLICT(source_id) DO UPDATE SET opened_before = excluded.opened_before,"
            " fetched_at = excluded.fetched_at, item_count = excluded.item_count",
            (source_id, opened_before, now, len(items)),
        )
    return len(items)


def list_baseline(conn: Connection, session_id: str, source_id: str) -> tuple[Row | None, list[Row]]:
    """(마지막 가져오기 기록 또는 None, 항목 — 이슈·PR 번호순). 다른 세션 소스 → NotFound."""
    _source_row(conn, session_id, source_id)
    record = _one(conn, "SELECT * FROM baseline_imports WHERE source_id = ?", (source_id,))
    items = conn.execute(
        "SELECT * FROM baseline_items WHERE source_id = ? ORDER BY issue_number, pr_number", (source_id,)
    ).fetchall()
    return record, items


def github_source_created_at(conn: Connection, session_id: str, source_id: str) -> str:
    """소스를 처음 연결한 시각(`github_sources.created_at` — 설정 변경에도 그대로). 기준선의 `opened_before`.
    다른 세션 소스 → NotFound."""
    return _source_row(conn, session_id, source_id)["created_at"]


# --- 지표 입력 (phase 9 step 8, ADR-0015 8항) -----------------------------------------------------------------


def _json_outcome(conn: Connection, store: ArtifactStore, artifact_id: str | None) -> str | None:
    """결과 산출물 JSON 의 최상위 `outcome` 문자열. 읽지 못하면 None."""
    if artifact_id is None:
        return None
    try:
        data = json.loads(read_artifact(conn, store, artifact_id))
    except (ValueError, NotFound, ArtifactMissing):
        return None
    outcome = data.get("outcome") if isinstance(data, dict) else None
    return outcome if isinstance(outcome, str) else None


def _execution_outcome(conn: Connection, store: ArtifactStore, row: Row) -> str | None:
    """가장 최근 판정의 outcome. 검토(`code_review`)는 판정이 `passed` 일 때만 결과 산출물(`CodeReviewResult`)의
    outcome — 판정 JSON 에는 통과 여부만 있고 approved·changes_requested 는 산출물에만 있다."""
    if row["verdict_json"] is None:
        return None
    verdict = json.loads(row["verdict_json"]).get("outcome")
    if row["kind"] != "code_review":
        return verdict
    return _json_outcome(conn, store, row["result_artifact_id"]) if verdict == "passed" else None


def list_metric_facts(conn: Connection, session_id: str, *, store: ArtifactStore) -> MetricFacts:
    """세션의 Task·실행·업무 이벤트·사람 요청을 도메인 값 객체로 옮긴다(계산 없음). NULL 은 None 그대로(모름).
    원본 이슈에서 온 Task 는 이슈 상태·병합 시각·마지막 조회 시각(`source_issues`)도 싣는다.
    `store` 는 검토 결과 산출물의 outcome 을 읽는 데만 쓴다."""
    tasks = tuple(
        TaskFact(
            task_id=r["task_id"], kind=r["kind"], created_at=r["created_at"], status=r["status"],
            predecessor_task_id=r["predecessor_task_id"],
            issue_opened_at=json.loads(r["snapshot_json"])["created_at"] if r["snapshot_json"] else None,
            issue_state=r["issue_state"], pr_merged_at=r["pr_merged_at"], merge_checked_at=r["merge_checked_at"],
            finished_at=r["finished_at"],
            review_decision=r["review_decision"],
        )
        for r in conn.execute(
            "SELECT t.*, si.snapshot_json, si.state AS issue_state, si.pr_merged_at, si.merge_checked_at"
            " FROM tasks t LEFT JOIN source_issues si ON si.task_id = t.task_id"
            " WHERE t.session_id = ? ORDER BY t.created_at, t.rowid",
            (session_id,),
        )
    )
    executions = tuple(
        ExecutionFact(
            execution_id=r["execution_id"], task_id=r["task_id"], kind=r["kind"], attempt_no=r["attempt_no"],
            status=r["status"], start_key=r["start_key"], created_at=r["created_at"], started_at=r["started_at"],
            finished_at=r["finished_at"], failed_code=r["failed_code"], outcome=_execution_outcome(conn, store, r),
            config_revision=r["config_revision"], folder_commit=r["folder_commit"], cost_usd=r["cost_usd"],
            input_tokens=r["input_tokens"], output_tokens=r["output_tokens"],
        )
        for r in conn.execute(
            "SELECT e.*, (SELECT v.verdict_json FROM task_verdicts v WHERE v.execution_id = e.execution_id"
            "  ORDER BY v.decided_at DESC, v.rowid DESC LIMIT 1) AS verdict_json"
            " FROM executions e JOIN tasks t ON t.task_id = e.task_id WHERE t.session_id = ?"
            " ORDER BY e.created_at, e.rowid",
            (session_id,),
        ).fetchall()
    )
    events = tuple(
        TaskEventFact(task_id=r["task_id"], type=r["type"], occurred_at=r["occurred_at"],
                      data=json.loads(r["data_json"]))
        for r in conn.execute(
            "SELECT task_id, type, occurred_at, data_json FROM task_events WHERE session_id = ? ORDER BY id",
            (session_id,),
        )
    )
    requests = tuple(
        HumanRequestFact(request_id=r["request_id"], task_id=r["task_id"], created_at=r["created_at"],
                         answered_at=r["answered_at"])
        for r in conn.execute(
            "SELECT h.request_id, h.task_id, h.created_at, h.answered_at FROM human_requests h"
            " JOIN tasks t ON t.task_id = h.task_id WHERE t.session_id = ? ORDER BY h.created_at, h.rowid",
            (session_id,),
        )
    )
    return MetricFacts(tasks=tasks, executions=executions, events=events, human_requests=requests)


def save_source_cursor(conn: Connection, session_id: str, source_id: str, cursor: str, now: str) -> None:
    cur = conn.execute(
        "UPDATE github_sources SET cursor = ?, cursor_updated_at = ? WHERE source_id = ? AND session_id = ?",
        (cursor, now, source_id, session_id),
    )
    _require_rowcount(cur, f"source {source_id}")


def get_source_cursor(conn: Connection, session_id: str, source_id: str) -> str | None:
    return _source_row(conn, session_id, source_id)["cursor"]


def get_source_synced_at(conn: Connection, session_id: str, source_id: str) -> str | None:
    """마지막으로 커서를 저장한 시각 — 수집이 끝난(또는 페이지를 넘긴) 때. 실패한 수집은 바꾸지 않는다."""
    return _source_row(conn, session_id, source_id)["cursor_updated_at"]


def bind_assignee(conn: Connection, session_id: str, binding: AssigneeBinding, now: str) -> None:
    """`(source_id, github_user_id)` 당 하나 — 다시 부르면 Agent·login 을 바꾼다. 소스·Agent 가 없으면 NotFound.
    Agent 능력(`code.fix {repository_id}`) 검사는 서버 몫."""
    with _tx(conn):
        _source_row(conn, session_id, binding.source_id)
        if get_agent(conn, binding.agent_id) is None:
            raise NotFound(f"agent {binding.agent_id}")
        conn.execute(
            "INSERT INTO github_assignee_bindings (source_id, github_user_id, github_login, agent_id, updated_at)"
            " VALUES (?, ?, ?, ?, ?) ON CONFLICT(source_id, github_user_id) DO UPDATE SET"
            " github_login = excluded.github_login, agent_id = excluded.agent_id, updated_at = excluded.updated_at",
            (binding.source_id, binding.github_user_id, binding.github_login, binding.agent_id, now),
        )


def list_assignee_bindings(conn: Connection, session_id: str, source_id: str) -> list[AssigneeBinding]:
    _source_row(conn, session_id, source_id)
    return [
        AssigneeBinding(source_id=r["source_id"], github_user_id=r["github_user_id"],
                        github_login=r["github_login"], agent_id=r["agent_id"])
        for r in conn.execute(
            "SELECT * FROM github_assignee_bindings WHERE source_id = ? ORDER BY github_user_id", (source_id,)
        )
    ]


@dataclass(frozen=True)
class SourceIssueUpsert:
    """`created` 새 Task · `updated` 새 source_revision · `unchanged` 같은 digest · `stale` 저장값보다 오래된 스냅샷."""

    action: Literal["created", "updated", "unchanged", "stale"]
    task_id: str
    source_revision: int
    input_changed: bool = False  # Task 제목·요청이 바뀌어 Task revision 이 올랐다


def upsert_source_issue(
    conn: Connection, session_id: str, source_id: str, snapshot: GitHubIssueSnapshot, *, task: dict, now: str
) -> SourceIssueUpsert:
    """원본 이슈 하나를 `(source_id, github_issue_id)` 로 Task 하나에 잇는다. 처음 보면 원본 칸을 채운 업무(`github`)와
    그 첫 단계 `task`(insert_task 와 같은 dict)를 같은 트랜잭션에 만든다. `updated_at` 이 저장값보다 이르면 버리고, 같은 시각이라도 digest 가 다르면 새 revision 이다
    (ADR-0014 결정 9). 이미 있는 Task 는 `task` 의 제목·요청만 본다 — 마감 전이고 둘 중 하나가 다르면 같은 트랜잭션에서
    바꾸고 Task revision+1(다음 실행 입력, 진행 중 실행의 요청은 그대로). 담당·라벨·상태는 원본 스냅샷에만 남는다."""
    digest = snapshot_digest(snapshot)
    with _tx(conn):
        source = _source_row(conn, session_id, source_id)
        if snapshot.repository_full_name != source["repository_full_name"]:
            raise ValueError(f"{snapshot.repository_full_name} 은 source {source_id} 의 저장소가 아닙니다")
        row = _one(
            conn, "SELECT * FROM source_issues WHERE source_id = ? AND github_issue_id = ?",
            (source_id, snapshot.issue_id),
        )
        if row is None:
            if task["session_id"] != session_id:
                raise ValueError(f"task {task['task_id']} 의 세션이 source {source_id} 의 세션과 다릅니다")
            key = f"{snapshot.repository_full_name}#{snapshot.number}"
            work_item_id = _work_item_for_task(
                conn, task, now, source_type="github", source_id=source_id, source_item_id=str(snapshot.issue_id),
                source_key=key, source_url=f"https://github.com/{snapshot.repository_full_name}/issues/{snapshot.number}",
                source_state=snapshot.state,
            )
            _insert_task_row(conn, task, now, work_item_id=work_item_id)
            conn.execute(
                "INSERT INTO source_issues (source_id, github_issue_id, issue_number, task_id, source_revision,"
                " snapshot_json, snapshot_digest, issue_updated_at, state, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?)",
                (source_id, snapshot.issue_id, snapshot.number, task["task_id"], snapshot.model_dump_json(),
                 digest, snapshot.updated_at, snapshot.state, now, now),
            )
            return SourceIssueUpsert("created", task["task_id"], 1)
        stored_at, incoming_at = _parse(row["issue_updated_at"]), _parse(snapshot.updated_at)
        if incoming_at < stored_at:
            return SourceIssueUpsert("stale", row["task_id"], row["source_revision"])
        if digest == row["snapshot_digest"]:
            return SourceIssueUpsert("unchanged", row["task_id"], row["source_revision"])
        revision = row["source_revision"] + 1
        conn.execute(
            "UPDATE source_issues SET issue_number = ?, source_revision = ?, snapshot_json = ?, snapshot_digest = ?,"
            " issue_updated_at = ?, state = ?, updated_at = ? WHERE source_id = ? AND github_issue_id = ?",
            (snapshot.number, revision, snapshot.model_dump_json(), digest, snapshot.updated_at, snapshot.state,
             now, source_id, snapshot.issue_id),
        )
        cur = conn.execute(
            "UPDATE tasks SET title = ?, request = ?, revision = revision + 1"
            " WHERE task_id = ? AND finished_at IS NULL AND (title != ? OR request != ?)",
            (task["title"], task["request"], row["task_id"], task["title"], task["request"]),
        )
        return SourceIssueUpsert("updated", row["task_id"], revision, input_changed=cur.rowcount == 1)


def mark_issue_delegated(
    conn: Connection, *, session_id: str, source_id: str, github_issue_id: int, by: str, now: str
) -> bool:
    """원본 이슈 Task 에 실행 지시를 기록한다(ADR-0017 — `all_open` 소스). 처음 지시만 남기고 이미 있으면 그대로(False).
    라벨을 떼거나 이슈가 바뀌어도 지우지 않는다. 다른 세션 소스·없는 이슈 → NotFound, `by` 가 operator·label 밖 → ValueError."""
    if by not in ("operator", "label"):
        raise ValueError(f"지시 주체 {by!r} 는 operator·label 이 아닙니다")
    with _tx(conn):
        _source_row(conn, session_id, source_id)
        row = _one(
            conn, "SELECT delegated_at FROM source_issues WHERE source_id = ? AND github_issue_id = ?",
            (source_id, github_issue_id),
        )
        if row is None:
            raise NotFound(f"source issue {source_id}/{github_issue_id}")
        cur = conn.execute(
            "UPDATE source_issues SET delegated_at = ?, delegated_by = ?"
            " WHERE source_id = ? AND github_issue_id = ? AND delegated_at IS NULL",
            (now, by, source_id, github_issue_id),
        )
        return cur.rowcount == 1


def list_source_issues(conn: Connection, session_id: str, source_id: str) -> list[Row]:
    """이 소스가 받은 이슈 전부(번호 순). 다른 세션의 소스면 NotFound."""
    _source_row(conn, session_id, source_id)
    return conn.execute(
        "SELECT * FROM source_issues WHERE source_id = ? ORDER BY issue_number", (source_id,)
    ).fetchall()


def record_issue_merge(
    conn: Connection, *, session_id: str, source_id: str, github_issue_id: int, link: IssuePrLink | None, now: str
) -> None:
    """원본 이슈를 닫은 병합 PR 을 기록하고 `merge_checked_at` 을 `now` 로 (ADR-0015 — 도입 후 완료 시각).
    `link` None = 조회했지만 병합 없음 — 병합 칸은 그대로 둔다. 한 번 기록된 병합은 다른 값·None 으로 덮지 않는다
    (같은 값 재기록은 멱등). 다른 세션 소스·없는 이슈 → NotFound, 다른 번호의 링크 → ValueError."""
    with _tx(conn):
        _source_row(conn, session_id, source_id)
        row = _one(
            conn, "SELECT issue_number FROM source_issues WHERE source_id = ? AND github_issue_id = ?",
            (source_id, github_issue_id),
        )
        if row is None:
            raise NotFound(f"source issue {source_id}/{github_issue_id}")
        if link is not None and link.issue_number != row["issue_number"]:
            raise ValueError(f"링크 이슈 #{link.issue_number} 는 #{row['issue_number']} 가 아닙니다")
        conn.execute(
            "UPDATE source_issues SET merge_checked_at = ?,"
            " merged_pr_number = COALESCE(merged_pr_number, ?), pr_merged_at = COALESCE(pr_merged_at, ?)"
            " WHERE source_id = ? AND github_issue_id = ?",
            (now, link and link.pr_number, link and link.pr_merged_at, source_id, github_issue_id),
        )


def list_issues_needing_merge_check(conn: Connection, session_id: str, source_id: str) -> list[Row]:
    """닫혔지만 병합 시각을 아직 모르는 원본 이슈(번호 순). 다른 세션 소스 → NotFound."""
    _source_row(conn, session_id, source_id)
    return conn.execute(
        "SELECT * FROM source_issues WHERE source_id = ? AND state = 'closed' AND pr_merged_at IS NULL"
        " ORDER BY issue_number",
        (source_id,),
    ).fetchall()


def get_source_issue_by_task(conn: Connection, session_id: str, task_id: str) -> Row | None:
    return _one(
        conn,
        "SELECT si.* FROM source_issues si JOIN github_sources gs ON gs.source_id = si.source_id"
        " WHERE si.task_id = ? AND gs.session_id = ?",
        (task_id, session_id),
    )


def create_followup_once(
    conn: Connection, spec: FollowupTaskSpec, task: dict, now: str, *, work_item_id: str
) -> tuple[str, bool]:
    """(task_id, created). 같은 `(session_id, cause_execution_id, kind)` 가 있으면 그 Task 를 돌려주고 만들지 않는다.
    `task` 는 spec 과 세션·종류·선행이 같아야 하고(ValueError), 원인 실행은 같은 세션 것이어야 한다(NotFound).
    새 Task 는 `work_item_id` 업무의 단계가 된다(같은 세션 업무가 아니면 NotFound)."""
    if (task["session_id"], task["kind"], task.get("predecessor_task_id")) != (
        spec.session_id, spec.kind, spec.predecessor_task_id,
    ):
        raise ValueError(f"task {task['task_id']} 가 후속 spec 과 다릅니다")
    with _tx(conn):
        cause = _one(
            conn,
            "SELECT 1 FROM executions e JOIN tasks t ON t.task_id = e.task_id"
            " WHERE e.execution_id = ? AND t.session_id = ?",
            (spec.cause_execution_id, spec.session_id),
        )
        if cause is None:
            raise NotFound(f"execution {spec.cause_execution_id}")
        existing = _one(
            conn,
            "SELECT task_id FROM followup_links WHERE session_id = ? AND cause_execution_id = ? AND to_kind = ?",
            (spec.session_id, spec.cause_execution_id, spec.kind),
        )
        if existing is not None:
            return existing["task_id"], False
        _insert_task_row(conn, task, now, work_item_id=work_item_id)
        conn.execute(
            "INSERT INTO followup_links (session_id, cause_execution_id, to_kind, task_id, rules_revision, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (spec.session_id, spec.cause_execution_id, spec.kind, task["task_id"], spec.rules_revision, now),
        )
        return task["task_id"], True


def get_followup_link(conn: Connection, task_id: str) -> Row | None:
    """결과가 만든 후속 Task 의 원인(`cause_execution_id`·`to_kind`). 결과로 만들지 않은 Task 면 None."""
    return _one(conn, "SELECT * FROM followup_links WHERE task_id = ?", (task_id,))


def create_human_request_once(
    conn: Connection, task_id: str, code: str, question: str, cause_key: str, now: str
) -> tuple[str, bool]:
    """(request_id, created). 같은 `(task_id, cause_key)` 는 기존 요청을 돌려준다. 요청 당시 Task revision 을 남긴다."""
    with _tx(conn):
        task = get_task(conn, task_id)
        if task is None:
            raise NotFound(f"task {task_id}")
        existing = _one(
            conn, "SELECT request_id FROM human_requests WHERE task_id = ? AND cause_key = ?", (task_id, cause_key)
        )
        if existing is not None:
            return existing["request_id"], False
        request_id = f"hr-{secrets.token_hex(4)}"
        conn.execute(
            "INSERT INTO human_requests (request_id, task_id, code, question, cause_key, task_revision, revision,"
            " state, created_at) VALUES (?, ?, ?, ?, ?, ?, 1, 'open', ?)",
            (request_id, task_id, code, question, cause_key, task["revision"], now),
        )
        return request_id, True


def get_human_request(conn: Connection, session_id: str, request_id: str) -> Row | None:
    return _one(
        conn,
        "SELECT hr.* FROM human_requests hr JOIN tasks t ON t.task_id = hr.task_id"
        " WHERE hr.request_id = ? AND t.session_id = ?",
        (request_id, session_id),
    )


def list_human_requests(conn: Connection, task_id: str) -> list[Row]:
    """Task 의 사람 요청 전부(만든 순). 준비 판정(열린 요청·정보 요청 revision)과 후속 원인 키 재료."""
    return conn.execute(
        "SELECT * FROM human_requests WHERE task_id = ? ORDER BY created_at, rowid", (task_id,)
    ).fetchall()


def list_open_human_requests(conn: Connection, session_id: str) -> list[Row]:
    """세션의 열린 요청(만든 순) — 운영자 응답 목록."""
    return conn.execute(
        "SELECT hr.* FROM human_requests hr JOIN tasks t ON t.task_id = hr.task_id"
        " WHERE t.session_id = ? AND hr.state = 'open' ORDER BY hr.created_at, hr.rowid",
        (session_id,),
    ).fetchall()


def list_human_responses(conn: Connection, task_id: str) -> list[Row]:
    """Task 의 응답(응답 순)과 그 요청의 code·question·cause_key·asked_revision(요청 당시 Task revision).
    다음 실행 입력과 응답 후 재개의 재료."""
    return conn.execute(
        "SELECT r.*, hr.code, hr.question, hr.cause_key, hr.task_revision AS asked_revision"
        " FROM human_responses r JOIN human_requests hr ON hr.request_id = r.request_id"
        " WHERE hr.task_id = ? ORDER BY r.created_at, r.rowid",
        (task_id,),
    ).fetchall()


def record_human_response_once(
    conn: Connection, session_id: str, request_id: str, *, response_id: str, expected_revision: int,
    action: str, text: str, now: str, agent_id: str | None = None, close_reason: str | None = None,
) -> tuple[int, bool]:
    """(task_revision, created). 응답은 요청을 `answered` 로, Task revision 을 +1 한다(다음 실행 입력).
    `agent_id` 는 같은 트랜잭션에서 Task 의 실행 Agent 로 지정하고, `close_reason` 은 Task 를 `실패` 로 마감하고
    활성 실행을 해제한다(운영자 종료). 같은 response_id·같은 내용 재전송은 처음 결과, 다른 내용은 ResponseConflict,
    revision 불일치·이미 응답됨은 StaleRequest, 마감된 Task 는 TaskClosed, 다른 세션이면 NotFound.
    `action` 의 허용 값 검사는 서버 몫."""
    with _tx(conn):
        request = get_human_request(conn, session_id, request_id)
        if request is None:
            raise NotFound(f"human request {request_id}")
        previous = _one(
            conn, "SELECT * FROM human_responses WHERE request_id = ? AND response_id = ?", (request_id, response_id)
        )
        if previous is not None:
            if (previous["action"], previous["text"], previous["agent_id"]) != (action, text, agent_id):
                raise ResponseConflict(response_id)
            return previous["task_revision"], False
        if request["state"] != "open" or request["revision"] != expected_revision:
            raise StaleRequest(request_id, request["revision"])
        task_id = request["task_id"]
        if get_task(conn, task_id)["finished_at"] is not None:
            raise TaskClosed(task_id)
        conn.execute("UPDATE tasks SET revision = revision + 1 WHERE task_id = ?", (task_id,))
        if agent_id is not None:
            conn.execute(
                "UPDATE tasks SET chosen_agent_id = ?, selection_mode = 'manual' WHERE task_id = ?", (agent_id, task_id)
            )
        if close_reason is not None:
            previous = _status_of(conn, task_id)
            conn.execute(
                "UPDATE tasks SET status = '실패', status_reason = ?, finished_at = ? WHERE task_id = ?",
                (close_reason, now, task_id),
            )
            _status_changed(conn, task_id, previous, "실패", close_reason, now)
            conn.execute("UPDATE executions SET released_at = ? WHERE task_id = ? AND released_at IS NULL", (now, task_id))
        task_revision = get_task(conn, task_id)["revision"]
        conn.execute(
            "UPDATE human_requests SET state = 'answered', revision = revision + 1, answered_at = ?"
            " WHERE request_id = ?",
            (now, request_id),
        )
        conn.execute(
            "INSERT INTO human_responses (request_id, response_id, action, text, agent_id, expected_revision,"
            " task_revision, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (request_id, response_id, action, text, agent_id, expected_revision, task_revision, now),
        )
        return task_revision, True


def _delivery(row: Row) -> SourceDelivery:
    return SourceDelivery.model_validate(
        {k: row[k] for k in SourceDelivery.model_fields}
    )


def enqueue_source_delivery_once(conn: Connection, task_id: str, body: str, now: str) -> tuple[SourceDelivery, bool]:
    """(delivery, created). 원본 이슈에 연결된 Task 만(없으면 NotFound). 최신 revision 과 본문이 같으면 그것을 돌려주고,
    다르면 `body_revision + 1` 의 pending 을 만든다. 전송·조정은 step 12 의 워커 몫."""
    digest = _sha256(body)
    with _tx(conn):
        issue = _one(conn, "SELECT source_id, issue_number FROM source_issues WHERE task_id = ?", (task_id,))
        if issue is None:
            raise NotFound(f"source issue of task {task_id}")
        latest = _one(
            conn, "SELECT * FROM source_deliveries WHERE task_id = ? ORDER BY body_revision DESC LIMIT 1", (task_id,)
        )
        if latest is not None and latest["body_digest"] == digest:
            return _delivery(latest), False
        delivery_id = f"dlv-{secrets.token_hex(4)}"
        conn.execute(
            "INSERT INTO source_deliveries (delivery_id, source_id, task_id, issue_number, body_revision, body_digest,"
            " body, state, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)",
            (delivery_id, issue["source_id"], task_id, issue["issue_number"],
             (latest["body_revision"] if latest else 0) + 1, digest, body, now, now),
        )
        return _delivery(_one(conn, "SELECT * FROM source_deliveries WHERE delivery_id = ?", (delivery_id,))), True


def list_source_deliveries(conn: Connection, task_id: str) -> list[SourceDelivery]:
    """Task 의 반영 기록 전부(body_revision 순). 마지막이 화면에 보이는 반영 상태다."""
    return [
        _delivery(r) for r in conn.execute(
            "SELECT * FROM source_deliveries WHERE task_id = ? ORDER BY body_revision", (task_id,)
        )
    ]


def delivery_targets(conn: Connection) -> list[Row]:
    """아직 끝나지 않은 반영(pending·sending·unknown)이 있는 Task 와 그 원본 — (task_id, source_id, issue_number,
    repository_full_name, config_json). 세션을 가리지 않는다(워커 전용). 소스 켜짐 여부는 호출자가 본다."""
    return conn.execute(
        "SELECT DISTINCT d.task_id, d.source_id, d.issue_number, gs.repository_full_name, gs.config_json"
        " FROM source_deliveries d JOIN github_sources gs ON gs.source_id = d.source_id"
        " WHERE d.state IN ('pending', 'sending', 'unknown') ORDER BY d.task_id"
    ).fetchall()


def claim_source_delivery(
    conn: Connection, delivery_id: str, *, expected_state: str, now: str, claim_until: str,
    comment_id: int | None, for_send: bool,
) -> int | None:
    """`expected_state` 이고 `next_at` 이 지난(또는 없는) 행을 `sending` 으로 가져간다 — 여러 소비자 중 하나만 성공한다.
    `next_at` 은 claim 만료 시각, `attempts + 1` 은 이 claim 의 fence(반환값). 실패면 None.
    `for_send` 면 그 Task 의 최신 revision 이고 다른 revision 이 전송 중·불확실하지 않을 때만 가져간다(중복 POST 방지)."""
    guard = (
        " AND NOT EXISTS (SELECT 1 FROM source_deliveries o WHERE o.task_id = d.task_id AND ("
        "o.body_revision > d.body_revision OR (o.delivery_id != d.delivery_id AND o.state IN ('sending', 'unknown'))))"
        if for_send else ""
    )
    with _tx(conn):
        cur = conn.execute(
            "UPDATE source_deliveries AS d SET state = 'sending', attempts = attempts + 1, next_at = ?,"
            " comment_id = ?, updated_at = ?"
            " WHERE delivery_id = ? AND state = ? AND (next_at IS NULL OR next_at <= ?)" + guard,
            (claim_until, comment_id, now, delivery_id, expected_state, now),
        )
        if cur.rowcount != 1:
            return None
        return _one(conn, "SELECT attempts FROM source_deliveries WHERE delivery_id = ?", (delivery_id,))["attempts"]


def record_source_delivery(
    conn: Connection, delivery_id: str, *, attempts: int, state: str, comment_id: int | None,
    next_at: str | None, last_error: str | None, now: str,
) -> bool:
    """claim(`attempts` fence)의 결과를 남긴다. 그 사이 claim 이 만료돼 다른 소비자가 가져갔으면 False — 늦은 기록은 버린다."""
    cur = conn.execute(
        "UPDATE source_deliveries SET state = ?, comment_id = ?, next_at = ?, last_error = ?, updated_at = ?"
        " WHERE delivery_id = ? AND state = 'sending' AND attempts = ?",
        (state, comment_id, next_at, last_error, now, delivery_id, attempts),
    )
    return cur.rowcount == 1


# --- 초안 PR 대기열 (phase 12 step 6, ADR-0018 결정 4) ---------------------------------------------------


def enqueue_pull_request(
    conn: Connection, *, task_id: str, session_id: str, source_id: str, repository_full_name: str, issue_number: int,
    fix_execution_id: str, review_execution_id: str, now: str,
) -> bool:
    """수정 Task 하나에 한 행(`pending`). 이미 있으면 그대로 두고 False — 검토 재평가·재시작에도 PR 은 하나다."""
    cur = conn.execute(
        "INSERT INTO task_pull_requests (task_id, session_id, source_id, repository_full_name, issue_number,"
        " head_branch, fix_execution_id, review_execution_id, state, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?) ON CONFLICT (task_id) DO NOTHING",
        (task_id, session_id, source_id, repository_full_name, issue_number, head_branch(task_id),
         fix_execution_id, review_execution_id, now, now),
    )
    return cur.rowcount == 1


def get_pull_request_row(conn: Connection, task_id: str) -> Row | None:
    return _one(conn, "SELECT * FROM task_pull_requests WHERE task_id = ?", (task_id,))


def pull_requests_due(conn: Connection, now: str, *, max_attempts: int) -> list[Row]:
    """열 차례인 행 — `pending` 이고 attempts < max_attempts 이고 (next_at IS NULL OR next_at <= now). 만든 순."""
    return conn.execute(
        "SELECT * FROM task_pull_requests WHERE state = 'pending' AND attempts < ?"
        " AND (next_at IS NULL OR next_at <= ?) ORDER BY created_at, task_id",
        (max_attempts, now),
    ).fetchall()


def open_pull_requests(conn: Connection) -> list[Row]:
    """병합·닫힘을 지켜볼 행(`open`). 만든 순."""
    return conn.execute(
        "SELECT * FROM task_pull_requests WHERE state = 'open' ORDER BY created_at, task_id"
    ).fetchall()


def record_pull_request(
    conn: Connection, task_id: str, *, state: str, now: str, pr: PullRequestRef | None = None,
    error: str | None = None, next_at: str | None = None,
) -> None:
    """상태 기록. `error` 가 있으면 시도 실패 — attempts + 1, last_error·next_at. `pr` 이 있으면 번호·URL·초안 여부,
    `merged` 는 PR 의 병합 시각, `closed` 는 `now` 를 닫힌 시각으로. 없는 행은 NotFound."""
    columns: dict[str, object] = {"state": state, "updated_at": now, "next_at": next_at}
    if error is not None:
        columns["last_error"] = error
    if pr is not None:
        columns.update(pr_number=pr.number, pr_url=pr.html_url, draft=int(pr.draft))
    if state == "open":
        columns["last_error"] = None
    elif state == "merged":
        columns["merged_at"] = pr.merged_at if pr is not None else now
    elif state == "closed":
        columns["closed_at"] = now
    assignments = ", ".join(f"{name} = ?" for name in columns)
    attempts = ", attempts = attempts + 1" if error is not None else ""
    cur = conn.execute(
        f"UPDATE task_pull_requests SET {assignments}{attempts} WHERE task_id = ?", (*columns.values(), task_id)
    )
    _require_rowcount(cur, f"pull request of {task_id}")


# --- 알림 대기열 (ADR-0018 결정 5, ARCHITECTURE "알림 (step 7·8)") ---------------------------------


def enqueue_notification(
    conn: Connection, *, session_id: str, event: str, task_id: str | None, dedupe_key: str, content: str,
    payload: dict, now: str,
) -> bool:
    """`pending` 한 행. 같은 `dedupe_key` 가 이미 있으면 그대로 두고 False — 재평가·재시작에도 사건당 한 번.
    URL 은 넣지 않는다(보낼 때 비밀 파일에서 읽는다)."""
    cur = conn.execute(
        "INSERT INTO notifications (notification_id, session_id, event, task_id, dedupe_key, content, payload_json,"
        " state, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?) ON CONFLICT (dedupe_key) DO NOTHING",
        (f"ntf-{secrets.token_hex(4)}", session_id, event, task_id, dedupe_key, content,
         json.dumps(payload, ensure_ascii=False), now),
    )
    return cur.rowcount == 1


def notifications_due(conn: Connection, now: str, *, max_attempts: int) -> list[Row]:
    """보낼 차례인 행 — `pending` 이고 attempts < max_attempts 이고 (next_at IS NULL OR next_at <= now). 만든 순."""
    return conn.execute(
        "SELECT * FROM notifications WHERE state = 'pending' AND attempts < ?"
        " AND (next_at IS NULL OR next_at <= ?) ORDER BY created_at, rowid",
        (max_attempts, now),
    ).fetchall()


def record_notification_attempt(
    conn: Connection, notification_id: str, *, state: str, error: str | None, now: str, next_at: str | None
) -> None:
    """전송 결과. `sent`·`failed`·(재시도할) `pending` 은 시도 한 번(attempts + 1), `skipped` 는 보내지 않았으니 그대로.
    `error` 는 분류 문구만(URL·응답 본문 없음). 없는 행은 NotFound."""
    attempts = ", attempts = attempts + 1" if state != "skipped" else ""
    cur = conn.execute(
        f"UPDATE notifications SET state = ?, last_error = ?, next_at = ?, sent_at = ?{attempts}"
        " WHERE notification_id = ?",
        (state, error, next_at, now if state == "sent" else None, notification_id),
    )
    _require_rowcount(cur, f"notification {notification_id}")


def list_notifications(conn: Connection, session_id: str, limit: int = 20) -> list[Row]:
    """세션의 최근 알림(새것 먼저)."""
    return conn.execute(
        "SELECT * FROM notifications WHERE session_id = ? ORDER BY created_at DESC, rowid DESC LIMIT ?",
        (session_id, limit),
    ).fetchall()
