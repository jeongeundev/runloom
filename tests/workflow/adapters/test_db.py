"""db.py — 연결 설정과 스키마 (ARCHITECTURE "DB 제약과 실행 잠금")."""

import sqlite3
import threading

import pytest

from workflow.adapters.db import SCHEMA_VERSION, connect, init_schema

from .conftest import NOW

TABLES = {
    "sessions",
    "agents",
    "connect_codes",
    "connectors",
    "tasks",
    "selection_records",
    "executions",
    "execution_events",
    "execution_observations",
    "artifacts",
    "task_verdicts",
    "diagnosis_usage",
    "session_agents",
    "chains",
    "kinds",
    "succession_rules",
}


def _seed_kind(conn, session_id: str, kind: str = "diagnosis") -> None:
    """tasks.kind 가 kinds 를 참조하므로 원시 SQL 로 Task 를 넣는 테스트는 종류 행을 먼저 둔다."""
    conn.execute(
        "INSERT INTO kinds (session_id, kind, spec_json, created_at) VALUES (?, ?, '{}', ?)",
        (session_id, kind, NOW),
    )


def test_connect_applies_pragmas(db_path):
    c = connect(db_path)
    assert c.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert c.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert c.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
    assert c.row_factory is sqlite3.Row
    assert c.isolation_level is None
    c.close()


def test_init_schema_is_idempotent(conn):
    init_schema(conn)
    names = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert TABLES <= names
    indexes = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    assert "ux_executions_active" in indexes
    assert conn.execute("SELECT version FROM schema_version").fetchone()[0] == SCHEMA_VERSION


def test_foreign_key_violation_raises(conn):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO tasks (task_id, session_id, title, request, kind, required_capability_json,"
            " selection_mode, run_mode, completion_mode, criteria_json, revision, target_json,"
            " status, status_reason, created_at) VALUES"
            " ('t1','no-such-session','t','r','diagnosis','{}','auto','manual','review','[]',1,'{}',"
            " '대기','선행 대기',?)",
            (NOW,),
        )


def test_check_constraints(conn):
    conn.execute("INSERT INTO sessions (session_id, created_at) VALUES ('s1', ?)", (NOW,))
    _seed_kind(conn, "s1")
    with pytest.raises(sqlite3.IntegrityError):  # 자기 자신을 선행 업무로 지정 금지
        conn.execute(
            "INSERT INTO tasks (task_id, session_id, title, request, kind, required_capability_json,"
            " selection_mode, run_mode, completion_mode, criteria_json, predecessor_task_id, revision,"
            " target_json, status, status_reason, created_at) VALUES"
            " ('t1','s1','t','r','diagnosis','{}','auto','manual','review','[]','t1',1,'{}',"
            " '대기','선행 대기',?)",
            (NOW,),
        )
    with pytest.raises(sqlite3.IntegrityError):  # owner_scope 허용 값 밖
        conn.execute(
            "INSERT INTO agents (agent_id, name, owner_scope, connection_type, capabilities_json,"
            " connection_state) VALUES ('a1','n','nobody','api','[]','online')"
        )
    with pytest.raises(sqlite3.IntegrityError):  # execution_events.seq >= 1
        conn.execute(
            "INSERT INTO execution_events (execution_id, seq, type, occurred_at, received_at,"
            " data_json, actor) VALUES ('e1', 0, 'accepted', ?, ?, '{}', 'x')",
            (NOW, NOW),
        )


def test_connection_is_usable_from_another_thread_sequentially(db_path):
    """FastAPI 는 의존성 준비·핸들러·정리를 서로 다른 threadpool 스레드에서 돌린다.
    요청당 연결 하나를 순차적으로 쓸 수 있어야 한다 (동시 공유는 하지 않는다)."""
    c = connect(db_path)
    init_schema(c)
    result: dict[str, object] = {}

    def use():
        try:
            result["value"] = c.execute("SELECT version FROM schema_version").fetchone()[0]
        except Exception as exc:  # noqa: BLE001 — 스레드 예외를 본 스레드로 옮긴다
            result["error"] = exc

    t = threading.Thread(target=use)
    t.start()
    t.join()
    assert "error" not in result, result.get("error")
    assert result["value"] == SCHEMA_VERSION
    c.close()


# --- phase 5: 세션 등록·Chain·대본 플래그 -----------------------------------


def _columns(conn, table: str) -> set[str]:
    return {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}


def test_phase5_tables_and_columns(conn):
    assert "demo_scripted" in _columns(conn, "agents")
    assert {"chain_id", "source_ref"} <= _columns(conn, "tasks")
    assert _columns(conn, "session_agents") == {"session_id", "agent_id", "registered_at"}
    assert _columns(conn, "chains") == {
        "chain_id", "session_id", "title", "source", "skipped_json", "created_at", "started_at",
    }


def test_schema_version_mismatch_raises(db_path):
    c = connect(db_path)
    init_schema(c)
    c.execute("UPDATE schema_version SET version = ?", (SCHEMA_VERSION + 1,))
    with pytest.raises(RuntimeError):
        init_schema(c)
    c.close()


def test_phase5_check_and_key_constraints(conn):
    conn.execute("INSERT INTO sessions (session_id, created_at) VALUES ('s1', ?)", (NOW,))
    _seed_kind(conn, "s1")
    conn.execute(
        "INSERT INTO agents (agent_id, name, owner_scope, connection_type, capabilities_json,"
        " connection_state) VALUES ('a1','n','company','api','[]','online')"
    )
    with pytest.raises(sqlite3.IntegrityError):  # demo_scripted 는 0/1
        conn.execute(
            "INSERT INTO agents (agent_id, name, owner_scope, connection_type, capabilities_json,"
            " connection_state, demo_scripted) VALUES ('a2','n','company','api','[]','online', 2)"
        )
    with pytest.raises(sqlite3.IntegrityError):  # chains.source 허용 값 밖
        conn.execute(
            "INSERT INTO chains (chain_id, session_id, title, source, created_at)"
            " VALUES ('c1','s1','t','email',?)",
            (NOW,),
        )
    conn.execute(
        "INSERT INTO session_agents (session_id, agent_id, registered_at) VALUES ('s1','a1',?)",
        (NOW,),
    )
    with pytest.raises(sqlite3.IntegrityError):  # (session_id, agent_id) 기본키
        conn.execute(
            "INSERT INTO session_agents (session_id, agent_id, registered_at) VALUES ('s1','a1',?)",
            (NOW,),
        )
    with pytest.raises(sqlite3.IntegrityError):  # 없는 agent 로 등록 불가
        conn.execute(
            "INSERT INTO session_agents (session_id, agent_id, registered_at) VALUES ('s1','nope',?)",
            (NOW,),
        )
    with pytest.raises(sqlite3.IntegrityError):  # tasks.chain_id 는 chains 를 참조
        conn.execute(
            "INSERT INTO tasks (task_id, session_id, title, request, kind, required_capability_json,"
            " selection_mode, run_mode, completion_mode, criteria_json, revision, target_json,"
            " status, status_reason, created_at, chain_id) VALUES"
            " ('t1','s1','t','r','diagnosis','{}','auto','manual','review','[]',1,'{}',"
            " '대기','선행 대기',?, 'no-such-chain')",
            (NOW,),
        )


# --- phase 6: 업무 종류·후속 규칙 (ADR-0009, ARCHITECTURE "저장") ---------------


def _foreign_keys(conn, table: str) -> set[tuple[str, str, str]]:
    """(참조 테이블, 자식 컬럼, 부모 컬럼) 집합."""
    return {(r["table"], r["from"], r["to"]) for r in conn.execute(f"PRAGMA foreign_key_list({table})")}


def test_schema_version_is_3():
    assert SCHEMA_VERSION == 3


def test_phase6_tables_and_foreign_keys(conn):
    assert _columns(conn, "kinds") == {"session_id", "kind", "spec_json", "created_at"}
    assert _columns(conn, "succession_rules") == {
        "rule_id", "session_id", "from_kind", "to_kind", "rule_json", "created_at",
    }
    assert {("sessions", "session_id", "session_id")} <= _foreign_keys(conn, "kinds")
    assert {
        ("sessions", "session_id", "session_id"),
        ("kinds", "session_id", "session_id"), ("kinds", "from_kind", "kind"),
        ("kinds", "to_kind", "kind"),
    } <= _foreign_keys(conn, "succession_rules")
    assert {("kinds", "session_id", "session_id"), ("kinds", "kind", "kind")} <= _foreign_keys(conn, "tasks")


def _insert_task(conn, task_id: str, session_id: str, kind: str) -> None:
    conn.execute(
        "INSERT INTO tasks (task_id, session_id, title, request, kind, required_capability_json,"
        " selection_mode, run_mode, completion_mode, criteria_json, revision, target_json,"
        " status, status_reason, created_at) VALUES (?, ?, 't', 'r', ?, '{}', 'auto', 'manual',"
        " 'review', '[]', 1, '{}', '대기', '선행 대기', ?)",
        (task_id, session_id, kind, NOW),
    )


def test_task_kind_must_be_registered_in_the_same_session(conn):
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    for session_id in ("s1", "s2"):
        conn.execute("INSERT INTO sessions (session_id, created_at) VALUES (?, ?)", (session_id, NOW))
    _seed_kind(conn, "s1", "review")
    _insert_task(conn, "t1", "s1", "review")
    with pytest.raises(sqlite3.IntegrityError):  # 이전 CHECK 목록에 있던 이름이어도 등록이 없으면 거부
        _insert_task(conn, "t2", "s1", "diagnosis")
    with pytest.raises(sqlite3.IntegrityError):  # 다른 세션의 등록은 세지 않는다
        _insert_task(conn, "t3", "s2", "review")
    with pytest.raises(sqlite3.IntegrityError):  # 참조되는 종류는 지울 수 없다
        conn.execute("DELETE FROM kinds WHERE session_id = 's1' AND kind = 'review'")


def test_execution_kind_accepts_any_registered_name(conn):
    conn.execute("INSERT INTO sessions (session_id, created_at) VALUES ('s1', ?)", (NOW,))
    _seed_kind(conn, "s1", "review")
    _insert_task(conn, "t1", "s1", "review")
    conn.execute(
        "INSERT INTO executions (execution_id, task_id, attempt_no, start_key, agent_id, kind,"
        " request_json, status, created_at) VALUES ('e1', 't1', 1, 'k', 'a', 'review', '{}', 'queued', ?)",
        (NOW,),
    )
    assert conn.execute("SELECT kind FROM executions WHERE execution_id = 'e1'").fetchone()[0] == "review"


def test_succession_rule_constraints(conn):
    conn.execute("INSERT INTO sessions (session_id, created_at) VALUES ('s1', ?)", (NOW,))
    _seed_kind(conn, "s1", "diagnosis")
    _seed_kind(conn, "s1", "code_change")
    conn.execute(
        "INSERT INTO succession_rules (rule_id, session_id, from_kind, to_kind, rule_json, created_at)"
        " VALUES ('r1', 's1', 'diagnosis', 'code_change', '{}', ?)",
        (NOW,),
    )
    with pytest.raises(sqlite3.IntegrityError):  # (session_id, from_kind, to_kind) 유일
        conn.execute(
            "INSERT INTO succession_rules (rule_id, session_id, from_kind, to_kind, rule_json, created_at)"
            " VALUES ('r2', 's1', 'diagnosis', 'code_change', '{}', ?)",
            (NOW,),
        )
    with pytest.raises(sqlite3.IntegrityError):  # to_kind 가 이 세션에 없다
        conn.execute(
            "INSERT INTO succession_rules (rule_id, session_id, from_kind, to_kind, rule_json, created_at)"
            " VALUES ('r3', 's1', 'code_change', 'review', '{}', ?)",
            (NOW,),
        )
    with pytest.raises(sqlite3.IntegrityError):  # 규칙이 참조하는 종류는 지울 수 없다
        conn.execute("DELETE FROM kinds WHERE session_id = 's1' AND kind = 'diagnosis'")


def test_artifact_kind_accepts_generic_result(conn):
    conn.execute("INSERT INTO sessions (session_id, created_at) VALUES ('s1', ?)", (NOW,))
    _seed_kind(conn, "s1", "review")
    _insert_task(conn, "t1", "s1", "review")
    conn.execute(
        "INSERT INTO executions (execution_id, task_id, attempt_no, start_key, agent_id, kind,"
        " request_json, status, created_at) VALUES ('e1', 't1', 1, 'k', 'a', 'review', '{}', 'queued', ?)",
        (NOW,),
    )
    conn.execute(
        "INSERT INTO artifacts (artifact_id, execution_id, session_id, kind, name, content_type, sha256,"
        " size, store_ref, created_at) VALUES ('a1', 'e1', 's1', 'generic_result', 'n', 'c', 'h', 0, 'r', ?)",
        (NOW,),
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO artifacts (artifact_id, execution_id, session_id, kind, name, content_type,"
            " sha256, size, store_ref, created_at) VALUES ('a2', 'e1', 's1', 'nope', 'n', 'c', 'h', 0, 'r', ?)",
            (NOW,),
        )
