"""db.py — 연결 설정과 스키마 (ARCHITECTURE "DB 제약과 실행 잠금")."""

import sqlite3

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
}


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
