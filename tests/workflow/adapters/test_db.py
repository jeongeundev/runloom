"""db.py — 연결 설정과 스키마 (ARCHITECTURE "DB 제약과 실행 잠금")."""

import json
import sqlite3
import threading
from pathlib import Path

import pytest

from workflow.adapters.db import SCHEMA_VERSION, connect, init_schema
from workflow.contracts.v1 import BUILTIN_KINDS, BUILTIN_RULES

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
    "source_tokens",
    "github_sources",
    "github_assignee_bindings",
    "source_issues",
    "followup_links",
    "human_requests",
    "human_responses",
    "source_deliveries",
    "task_events",
    "baseline_items",
    "baseline_imports",
    "task_pull_requests",
    "notifications",
}
PHASE9_TABLES = {"task_events", "baseline_items", "baseline_imports"}
PHASE12_TABLES = {"task_pull_requests", "notifications"}


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
    assert {
        "chain_id", "session_id", "title", "source", "skipped_json", "created_at", "started_at",
    } <= _columns(conn, "chains")  # phase 7 이 callback 열을 더한다


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


def test_schema_version_is_8():
    assert SCHEMA_VERSION == 8


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


# --- phase 7: 입구 토큰·Chain callback (ADR-0010, ARCHITECTURE "n8n 입구와 출구" 저장) ------


def _insert_chain(conn, chain_id: str, source: str, **columns) -> None:
    names = ["chain_id", "session_id", "title", "source", "created_at", *columns]
    conn.execute(
        f"INSERT INTO chains ({', '.join(names)}) VALUES ({', '.join('?' * len(names))})",
        (chain_id, "s1", "t", source, NOW, *columns.values()),
    )


def test_init_schema_rejects_versions_without_migration(db_path):
    """마이그레이션은 4 → 5 하나뿐이다. 3 이하가 기록된 DB 는 재생성(WORKFLOW_RESET_DB=1) 대상이다."""
    c = connect(db_path)
    init_schema(c)
    c.execute("UPDATE schema_version SET version = 3")
    with pytest.raises(RuntimeError, match="3"):
        init_schema(c)
    c.close()


def test_phase7_source_tokens_table(conn):
    assert _columns(conn, "source_tokens") == {
        "token_id", "session_id", "source", "token_sha256", "label", "created_at", "last_used_at",
        "revoked_at",
    }
    assert {("sessions", "session_id", "session_id")} <= _foreign_keys(conn, "source_tokens")
    conn.execute("INSERT INTO sessions (session_id, created_at) VALUES ('s1', ?)", (NOW,))
    conn.execute(
        "INSERT INTO source_tokens (token_id, session_id, source, token_sha256, label, created_at)"
        " VALUES ('src-1', 's1', 'n8n', 'hash-1', '', ?)",
        (NOW,),
    )
    row = conn.execute("SELECT * FROM source_tokens WHERE token_id = 'src-1'").fetchone()
    assert row["last_used_at"] is None and row["revoked_at"] is None
    with pytest.raises(sqlite3.IntegrityError):  # source 허용 값 밖
        conn.execute(
            "INSERT INTO source_tokens (token_id, session_id, source, token_sha256, label, created_at)"
            " VALUES ('src-2', 's1', 'slack', 'hash-2', '', ?)",
            (NOW,),
        )
    with pytest.raises(sqlite3.IntegrityError):  # token_sha256 유일
        conn.execute(
            "INSERT INTO source_tokens (token_id, session_id, source, token_sha256, label, created_at)"
            " VALUES ('src-3', 's1', 'n8n', 'hash-1', '', ?)",
            (NOW,),
        )
    with pytest.raises(sqlite3.IntegrityError):  # 없는 세션
        conn.execute(
            "INSERT INTO source_tokens (token_id, session_id, source, token_sha256, label, created_at)"
            " VALUES ('src-4', 'no-such-session', 'n8n', 'hash-4', '', ?)",
            (NOW,),
        )


def test_phase7_chains_callback_columns_defaults_and_source(conn):
    assert _columns(conn, "chains") == {
        "chain_id", "session_id", "title", "source", "skipped_json", "created_at", "started_at",
        "items_json", "callback_url", "callback_sent_at", "callback_attempts", "callback_next_at",
        "callback_last_error",
    }
    conn.execute("INSERT INTO sessions (session_id, created_at) VALUES ('s1', ?)", (NOW,))
    _insert_chain(conn, "c1", "n8n")
    row = conn.execute("SELECT * FROM chains WHERE chain_id = 'c1'").fetchone()
    assert row["source"] == "n8n"
    assert row["callback_attempts"] == 0
    assert all(
        row[c] is None
        for c in ("items_json", "callback_url", "callback_sent_at", "callback_next_at",
                  "callback_last_error")
    )
    for source in ("github", "jira", "manual"):
        _insert_chain(conn, f"c-{source}", source)
    with pytest.raises(sqlite3.IntegrityError):  # source 허용 값 밖
        _insert_chain(conn, "c-slack", "slack")
    with pytest.raises(sqlite3.IntegrityError):  # callback_attempts >= 0
        _insert_chain(conn, "c-neg", "n8n", callback_attempts=-1)


# --- phase 8: v4 → v5 데이터 보존 마이그레이션 (ADR-0014 결과, ARCHITECTURE "GitHub 업무 순환" 저장) ------

V4_SCHEMA = (Path(__file__).parent / "fixtures" / "schema_v4.sql").read_text()
V4_TABLES = TABLES - PHASE9_TABLES - PHASE12_TABLES - {
    "github_sources", "github_assignee_bindings", "source_issues", "followup_links", "human_requests",
    "human_responses", "source_deliveries",
}
PHASE8_KINDS = {spec.kind: spec for spec in BUILTIN_KINDS if spec.kind in ("bug_fix", "code_review")}


def _v4_db(db_path, *, extra_kind: str | None = None):
    """phase 7 서버가 남긴 모양의 v4 DB — 세션 2개, 내장·사용자 정의 종류, 규칙, 업무·실행·이벤트·산출물·판정."""
    c = connect(db_path)
    c.executescript(V4_SCHEMA)
    c.execute("INSERT INTO schema_version (version) VALUES (4)")
    for sid in ("s1", "s2"):
        c.execute("INSERT INTO sessions (session_id, created_at, is_operator) VALUES (?, ?, ?)",
                  (sid, NOW, int(sid == "s1")))
        for spec in BUILTIN_KINDS[:2]:
            c.execute("INSERT INTO kinds (session_id, kind, spec_json, created_at) VALUES (?, ?, ?, ?)",
                      (sid, spec.kind, spec.model_dump_json(), NOW))
        c.execute(
            "INSERT INTO succession_rules (rule_id, session_id, from_kind, to_kind, rule_json, created_at)"
            " VALUES (?, ?, 'diagnosis', 'code_change', ?, ?)",
            (f"rule-{sid}", sid, BUILTIN_RULES[0].model_dump_json(), NOW),
        )
    c.execute("INSERT INTO kinds (session_id, kind, spec_json, created_at) VALUES ('s1', 'review', ?, ?)",
              (json.dumps({"kind": "review", "builtin": False}), NOW))
    if extra_kind is not None:  # phase 8 이전에 사용자가 만든 같은 이름 종류
        c.execute("INSERT INTO kinds (session_id, kind, spec_json, created_at) VALUES ('s2', ?, ?, ?)",
                  (extra_kind, json.dumps({"kind": extra_kind, "builtin": False}), NOW))
    c.execute(
        "INSERT INTO agents (agent_id, name, owner_scope, connection_type, connector_id, capabilities_json,"
        " connection_state) VALUES ('a1', 'codex', 'personal', 'local', 'conn-1', '[]', 'online')"
    )
    c.execute("INSERT INTO connectors (connector_id, token_sha256, created_at) VALUES ('conn-1', 'h', ?)", (NOW,))
    c.execute("INSERT INTO source_tokens (token_id, session_id, source, token_sha256, label, created_at)"
              " VALUES ('src-1', 's1', 'n8n', 'th', '', ?)", (NOW,))
    c.execute("INSERT INTO chains (chain_id, session_id, title, source, created_at, callback_url)"
              " VALUES ('c1', 's1', 't', 'n8n', ?, 'http://localhost:5678/cb')", (NOW,))
    c.execute(
        "INSERT INTO tasks (task_id, session_id, title, request, kind, required_capability_json,"
        " selection_mode, run_mode, completion_mode, criteria_json, revision, target_json,"
        " status, status_reason, created_at, chain_id) VALUES ('t1', 's1', 't', 'r', 'review', '{}',"
        " 'auto', 'manual', 'review', '[]', 2, '{}', '확인 필요', '검토 대기', ?, 'c1')",
        (NOW,),
    )
    c.execute(
        "INSERT INTO executions (execution_id, task_id, attempt_no, start_key, agent_id, kind, request_json,"
        " status, created_at, result_artifact_id) VALUES ('e1', 't1', 1, 'k', 'a1', 'review', '{}',"
        " 'result_ready', ?, 'art-1')",
        (NOW,),
    )
    c.execute("INSERT INTO execution_events (execution_id, seq, type, occurred_at, received_at, data_json,"
              " actor) VALUES ('e1', 1, 'accepted', ?, ?, '{}', 'conn-1')", (NOW, NOW))
    c.execute(
        "INSERT INTO artifacts (artifact_id, execution_id, session_id, kind, name, content_type, sha256,"
        " size, store_ref, created_at) VALUES ('art-1', 'e1', 's1', 'generic_result', 'r.json',"
        " 'application/json', 'abc', 3, 'ab/abc', ?)",
        (NOW,),
    )
    c.execute("INSERT INTO task_verdicts (task_id, execution_id, verdict_json, decided_at)"
              " VALUES ('t1', 'e1', '{}', ?)", (NOW,))
    return c


def _dump(conn, tables, columns: dict[str, list[str]] | None = None) -> dict[str, list[tuple]]:
    """`columns` 를 주면 그 열만 — 마이그레이션이 열을 더해도 기존 열 값이 그대로인지 비교한다."""
    def select(t: str) -> str:
        return ", ".join(columns[t]) if columns else "*"
    return {
        t: [tuple(r) for r in conn.execute(f"SELECT {select(t)} FROM {t} ORDER BY rowid")] for t in sorted(tables)
    }


def _column_lists(conn, tables) -> dict[str, list[str]]:
    return {t: [r["name"] for r in conn.execute(f"PRAGMA table_info({t})")] for t in tables}


def _table_names(conn) -> set[str]:
    return {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _insert_artifact(conn, artifact_id: str, kind: str) -> None:
    conn.execute(
        "INSERT INTO artifacts (artifact_id, execution_id, session_id, kind, name, content_type, sha256,"
        " size, store_ref, created_at) VALUES (?, 'e1', 's1', ?, 'n', 'c', ?, 0, 'r', ?)",
        (artifact_id, kind, artifact_id, NOW),
    )


def test_v4_fixture_is_the_phase7_schema(db_path):
    """고정한 v4 원문이 실제 phase 7 모양인지 — code_review_result 산출물을 모른다."""
    c = _v4_db(db_path)
    assert _table_names(c) - {"sqlite_sequence"} == V4_TABLES | {"schema_version"}
    with pytest.raises(sqlite3.IntegrityError):
        _insert_artifact(c, "art-review", "code_review_result")
    c.close()


def test_migrates_v4_to_v5_preserving_data(db_path):
    c = _v4_db(db_path)
    kept = V4_TABLES - {"kinds", "succession_rules", "connectors"}
    kept_columns = _column_lists(c, kept)
    before = _dump(c, kept)
    kinds_before = _dump(c, {"kinds", "succession_rules"})
    c.close()

    c = connect(db_path)
    init_schema(c)
    assert [tuple(r) for r in c.execute("SELECT version FROM schema_version")] == [(SCHEMA_VERSION,)]
    assert TABLES <= _table_names(c)
    assert _dump(c, kept, kept_columns) == before
    for table, rows in kinds_before.items():  # 기존 종류·규칙 행은 그대로, 새 내장만 더해진다
        assert set(rows) <= set(_dump(c, {table})[table])
    row = c.execute("SELECT connector_id, token_sha256, supported_kinds_json FROM connectors").fetchone()
    assert tuple(row) == ("conn-1", "h", None)  # null = 구버전 연결 프로그램
    for sid in ("s1", "s2"):
        seeded = {
            r["kind"]: r["spec_json"]
            for r in c.execute("SELECT kind, spec_json FROM kinds WHERE session_id = ?", (sid,))
            if r["kind"] in PHASE8_KINDS
        }
        assert seeded == {k: spec.model_dump_json() for k, spec in PHASE8_KINDS.items()}
        rules = c.execute(
            "SELECT rule_id, rule_json FROM succession_rules WHERE session_id = ? AND from_kind = 'bug_fix'",
            (sid,),
        ).fetchall()
        assert [r["rule_json"] for r in rules] == [BUILTIN_RULES[1].model_dump_json()]
        assert rules[0]["rule_id"].startswith("rule-")
    assert c.execute("PRAGMA foreign_key_check").fetchall() == []
    assert c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    _insert_artifact(c, "art-review", "code_review_result")  # 재생성한 artifacts CHECK
    with pytest.raises(sqlite3.IntegrityError):
        _insert_artifact(c, "art-bad", "nope")
    with pytest.raises(sqlite3.IntegrityError):  # UNIQUE (execution_id, kind, sha256) 유지
        c.execute(
            "INSERT INTO artifacts (artifact_id, execution_id, session_id, kind, name, content_type, sha256,"
            " size, store_ref, created_at) VALUES ('art-dup', 'e1', 's1', 'generic_result', 'n', 'c', 'abc',"
            " 0, 'r', ?)",
            (NOW,),
        )
    with pytest.raises(sqlite3.IntegrityError):  # artifacts.execution_id FK 유지
        c.execute(
            "INSERT INTO artifacts (artifact_id, execution_id, session_id, kind, name, content_type, sha256,"
            " size, store_ref, created_at) VALUES ('art-x', 'no-exec', 's1', 'diff', 'n', 'c', 'x', 0, 'r', ?)",
            (NOW,),
        )

    after = _dump(c, TABLES)
    init_schema(c)  # 재실행은 아무것도 바꾸지 않는다
    assert _dump(c, TABLES) == after
    c.close()


def test_migration_rolls_back_on_builtin_name_conflict(db_path):
    """phase 8 이전에 사용자가 만든 bug_fix 가 있으면 전체를 되돌리고 충돌 목록을 알린다."""
    c = _v4_db(db_path, extra_kind="bug_fix")
    before = _dump(c, V4_TABLES)
    c.close()

    c = connect(db_path)
    with pytest.raises(RuntimeError, match=r"s2.*bug_fix"):
        init_schema(c)
    assert c.execute("SELECT version FROM schema_version").fetchone()[0] == 4
    assert _table_names(c) - {"sqlite_sequence"} == V4_TABLES | {"schema_version"}
    assert _dump(c, V4_TABLES) == before
    assert "supported_kinds_json" not in _columns(c, "connectors")
    with pytest.raises(sqlite3.IntegrityError):  # artifacts 도 v4 그대로
        _insert_artifact(c, "art-review", "code_review_result")
    assert not c.in_transaction
    c.close()


def test_migration_rolls_back_when_a_later_statement_fails(db_path, monkeypatch):
    from workflow.adapters import db

    c = _v4_db(db_path)
    before = _dump(c, V4_TABLES)

    def boom(conn, now):
        raise sqlite3.OperationalError("중간 실패")

    monkeypatch.setattr(db, "_seed_phase8_kinds", boom)
    with pytest.raises(sqlite3.OperationalError, match="중간 실패"):
        init_schema(c)
    assert c.execute("SELECT version FROM schema_version").fetchone()[0] == 4
    assert _table_names(c) - {"sqlite_sequence"} == V4_TABLES | {"schema_version"}
    assert _dump(c, V4_TABLES) == before
    assert not c.in_transaction
    monkeypatch.undo()
    init_schema(c)  # 원인이 사라지면 다시 돌릴 수 있다
    assert c.execute("SELECT version FROM schema_version").fetchone()[0] == SCHEMA_VERSION
    c.close()


def _indexes(conn) -> set[tuple[str, str]]:
    return {
        (r["name"], r["sql"])
        for r in conn.execute("SELECT name, sql FROM sqlite_master WHERE type = 'index' AND sql IS NOT NULL")
    }


@pytest.mark.parametrize("old", ["v4", "v5"])
def test_fresh_schema_matches_migrated_schema(tmp_path, old):
    """새로 만든 DB 와 v4·v5 에서 올린 DB 의 테이블·열·외래키·인덱스 정의가 같다(순서 무관)."""
    fresh = connect(tmp_path / "fresh.sqlite")
    init_schema(fresh)
    migrated = (_v4_db if old == "v4" else _v5_db)(tmp_path / "old.sqlite")
    init_schema(migrated)
    assert _table_names(fresh) == _table_names(migrated)
    for table in TABLES:
        cols = "SELECT name, type, \"notnull\", dflt_value, pk FROM pragma_table_info(?) ORDER BY name"
        assert fresh.execute(cols, (table,)).fetchall() == migrated.execute(cols, (table,)).fetchall(), table
        assert _foreign_keys(fresh, table) == _foreign_keys(migrated, table), table
    assert _indexes(fresh) == _indexes(migrated)
    fresh.close()
    migrated.close()


def _cycle_base(conn) -> None:
    """새 테이블 제약 확인용 최소 행 — 세션·종류·에이전트·소스·업무·실행."""
    conn.execute("INSERT INTO sessions (session_id, created_at) VALUES ('s1', ?)", (NOW,))
    _seed_kind(conn, "s1", "bug_fix")
    _seed_kind(conn, "s1", "code_review")
    conn.execute("INSERT INTO agents (agent_id, name, owner_scope, connection_type, capabilities_json,"
                 " connection_state) VALUES ('a1', 'n', 'personal', 'local', '[]', 'online')")
    conn.execute("INSERT INTO github_sources (source_id, session_id, repository_full_name, config_json,"
                 " created_at, updated_at) VALUES ('ghs-00000001', 's1', 'acme/billing', '{}', ?, ?)", (NOW, NOW))
    _insert_task(conn, "t1", "s1", "bug_fix")
    _insert_task(conn, "t2", "s1", "code_review")
    conn.execute("INSERT INTO executions (execution_id, task_id, attempt_no, start_key, agent_id, kind,"
                 " request_json, status, created_at) VALUES ('e1', 't1', 1, 'k', 'a1', 'bug_fix', '{}',"
                 " 'result_ready', ?)", (NOW,))


def test_phase8_table_keys_and_checks(conn):
    _cycle_base(conn)
    with pytest.raises(sqlite3.IntegrityError):  # 세션당 저장소 하나
        conn.execute("INSERT INTO github_sources (source_id, session_id, repository_full_name, config_json,"
                     " created_at, updated_at) VALUES ('ghs-00000002', 's1', 'acme/billing', '{}', ?, ?)",
                     (NOW, NOW))
    conn.execute("INSERT INTO github_assignee_bindings (source_id, github_user_id, github_login, agent_id,"
                 " updated_at) VALUES ('ghs-00000001', 7, 'kim', 'a1', ?)", (NOW,))
    with pytest.raises(sqlite3.IntegrityError):  # (source_id, github_user_id) 하나
        conn.execute("INSERT INTO github_assignee_bindings (source_id, github_user_id, github_login, agent_id,"
                     " updated_at) VALUES ('ghs-00000001', 7, 'lee', 'a1', ?)", (NOW,))
    with pytest.raises(sqlite3.IntegrityError):  # 없는 Agent
        conn.execute("INSERT INTO github_assignee_bindings (source_id, github_user_id, github_login, agent_id,"
                     " updated_at) VALUES ('ghs-00000001', 8, 'lee', 'nope', ?)", (NOW,))

    issue = ("INSERT INTO source_issues (source_id, github_issue_id, issue_number, task_id, source_revision,"
             " snapshot_json, snapshot_digest, issue_updated_at, state, created_at, updated_at)"
             " VALUES ('ghs-00000001', ?, ?, ?, ?, '{}', 'd', ?, ?, ?, ?)")
    conn.execute(issue, (100, 41, "t1", 1, NOW, "open", NOW, NOW))
    for params in (
        (100, 42, "t2", 1, NOW, "open", NOW, NOW),  # (source_id, github_issue_id) 유일
        (101, 43, "t1", 1, NOW, "open", NOW, NOW),  # Task 하나에 이슈 하나
        (102, 44, "t2", 0, NOW, "open", NOW, NOW),  # source_revision >= 1
        (103, 45, "t2", 1, NOW, "merged", NOW, NOW),  # state 허용 값 밖
    ):
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(issue, params)

    link = ("INSERT INTO followup_links (session_id, cause_execution_id, to_kind, task_id, rules_revision,"
            " created_at) VALUES ('s1', 'e1', ?, ?, 1, ?)")
    conn.execute(link, ("code_review", "t2", NOW))
    with pytest.raises(sqlite3.IntegrityError):  # (session_id, cause_execution_id, to_kind) 유일
        conn.execute(link, ("code_review", "t1", NOW))
    with pytest.raises(sqlite3.IntegrityError):  # to_kind 는 이 세션에 등록된 종류
        conn.execute(link, ("diagnosis", "t1", NOW))

    request = ("INSERT INTO human_requests (request_id, task_id, code, question, cause_key, task_revision,"
               " revision, state, created_at) VALUES (?, 't1', 'fix_needs_information', 'q', ?, 1, 1, ?, ?)")
    conn.execute(request, ("hr-00000001", "fix_needs_information:e1", "open", NOW))
    with pytest.raises(sqlite3.IntegrityError):  # (task_id, cause_key) 유일
        conn.execute(request, ("hr-00000002", "fix_needs_information:e1", "open", NOW))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(request, ("hr-00000003", "other", "pending", NOW))
    response = ("INSERT INTO human_responses (request_id, response_id, action, text, expected_revision,"
                " task_revision, created_at) VALUES ('hr-00000001', 'resp-1', 'answer', ?, 1, 2, ?)")
    conn.execute(response, ("재현 절차", NOW))
    with pytest.raises(sqlite3.IntegrityError):  # (request_id, response_id) 유일
        conn.execute(response, ("다른 내용", NOW))

    delivery = ("INSERT INTO source_deliveries (delivery_id, source_id, task_id, issue_number, body_revision,"
                " body_digest, body, state, comment_id, created_at, updated_at)"
                " VALUES (?, 'ghs-00000001', 't1', 41, ?, 'd', 'b', ?, ?, ?, ?)")
    conn.execute(delivery, ("dlv-00000001", 1, "pending", None, NOW, NOW))
    for params in (
        ("dlv-00000002", 1, "pending", None, NOW, NOW),  # (task_id, body_revision) 유일
        ("dlv-00000003", 2, "delivered", None, NOW, NOW),  # delivered 는 comment_id 필수
        ("dlv-00000004", 3, "lost", None, NOW, NOW),  # state 허용 값 밖
    ):
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(delivery, params)
    row = conn.execute("SELECT attempts, next_at, last_error FROM source_deliveries").fetchone()
    assert tuple(row) == (0, None, None)


# --- phase 9: v5 → v6 측정 스키마 (ADR-0015, ARCHITECTURE "측정 — phase 9" 저장) -----------------------

V5_SCHEMA = (Path(__file__).parent / "fixtures" / "schema_v5.sql").read_text()
V5_TABLES = TABLES - PHASE9_TABLES - PHASE12_TABLES
EXECUTION_MEASURE_COLUMNS = (
    "config_revision", "folder_commit", "folder_dirty", "cost_usd", "input_tokens", "output_tokens",
)
SOURCE_ISSUE_MERGE_COLUMNS = ("merged_pr_number", "pr_merged_at", "merge_checked_at")


def _v5_db(db_path):
    """phase 8 서버가 남긴 모양의 v5 DB — v4 데이터에 GitHub 순환 행을 더한다."""
    c = connect(db_path)
    c.executescript(V5_SCHEMA)
    c.execute("INSERT INTO schema_version (version) VALUES (5)")
    for sid in ("s1", "s2"):
        c.execute("INSERT INTO sessions (session_id, created_at, is_operator) VALUES (?, ?, ?)",
                  (sid, NOW, int(sid == "s1")))
        for spec in BUILTIN_KINDS:
            c.execute("INSERT INTO kinds (session_id, kind, spec_json, created_at) VALUES (?, ?, ?, ?)",
                      (sid, spec.kind, spec.model_dump_json(), NOW))
    c.execute("INSERT INTO kinds (session_id, kind, spec_json, created_at) VALUES ('s1', 'review', ?, ?)",
              (json.dumps({"kind": "review", "builtin": False}), NOW))
    c.execute(
        "INSERT INTO succession_rules (rule_id, session_id, from_kind, to_kind, rule_json, created_at)"
        " VALUES ('rule-1', 's1', 'bug_fix', 'code_review', ?, ?)",
        (BUILTIN_RULES[1].model_dump_json(), NOW),
    )
    c.execute(
        "INSERT INTO agents (agent_id, name, owner_scope, connection_type, connector_id, capabilities_json,"
        " connection_state) VALUES ('a1', 'claude', 'personal', 'local', 'conn-1', '[]', 'online')"
    )
    c.execute("INSERT INTO connectors (connector_id, token_sha256, created_at, supported_kinds_json)"
              " VALUES ('conn-1', 'h', ?, '[\"bug_fix\"]')", (NOW,))
    c.execute("INSERT INTO github_sources (source_id, session_id, repository_full_name, config_json,"
              " created_at, updated_at) VALUES ('ghs-00000001', 's1', 'acme/billing', '{}', ?, ?)", (NOW, NOW))
    task = (
        "INSERT INTO tasks (task_id, session_id, title, request, kind, required_capability_json,"
        " selection_mode, run_mode, completion_mode, criteria_json, revision, target_json,"
        " status, status_reason, created_at, predecessor_task_id, review_decision) VALUES (?, 's1', 't', 'r', ?,"
        " '{}', 'auto', 'auto', 'review', '[]', 2, '{}', ?, '검토 대기', ?, ?, ?)"
    )
    c.execute(task, ("t1", "bug_fix", "확인 필요", NOW, None, "approve"))
    c.execute(task, ("t2", "code_review", "대기", NOW, "t1", None))
    c.execute(task, ("t3", "review", "대기", NOW, None, None))
    c.execute(
        "INSERT INTO executions (execution_id, task_id, attempt_no, start_key, agent_id, kind, request_json,"
        " status, created_at, started_at, finished_at, released_at, result_artifact_id)"
        " VALUES ('e1', 't1', 1, 'k', 'a1', 'bug_fix', '{}', 'result_ready', ?, ?, ?, ?, 'art-1')",
        (NOW, NOW, NOW, NOW),
    )
    c.execute("INSERT INTO execution_events (execution_id, seq, type, occurred_at, received_at, data_json,"
              " actor) VALUES ('e1', 1, 'started', ?, ?, '{}', 'conn-1')", (NOW, NOW))
    c.execute(
        "INSERT INTO artifacts (artifact_id, execution_id, session_id, kind, name, content_type, sha256,"
        " size, store_ref, created_at) VALUES ('art-1', 'e1', 's1', 'code_review_result', 'r.json',"
        " 'application/json', 'abc', 3, 'ab/abc', ?)",
        (NOW,),
    )
    c.execute("INSERT INTO task_verdicts (task_id, execution_id, verdict_json, decided_at)"
              " VALUES ('t1', 'e1', '{}', ?)", (NOW,))
    c.execute("INSERT INTO source_issues (source_id, github_issue_id, issue_number, task_id, source_revision,"
              " snapshot_json, snapshot_digest, issue_updated_at, state, created_at, updated_at)"
              " VALUES ('ghs-00000001', 100, 41, 't1', 1, '{}', 'd', ?, 'open', ?, ?)", (NOW, NOW, NOW))
    c.execute("INSERT INTO followup_links (session_id, cause_execution_id, to_kind, task_id, rules_revision,"
              " created_at) VALUES ('s1', 'e1', 'code_review', 't2', 1, ?)", (NOW,))
    return c


def test_v5_fixture_is_the_phase8_schema(db_path):
    c = _v5_db(db_path)
    assert _table_names(c) - {"sqlite_sequence"} == V5_TABLES | {"schema_version"}
    assert "config_revision" not in _columns(c, "sessions")
    assert not set(EXECUTION_MEASURE_COLUMNS) & _columns(c, "executions")
    assert not set(SOURCE_ISSUE_MERGE_COLUMNS) & _columns(c, "source_issues")
    c.close()


def test_fresh_db_is_v6_with_measure_tables(conn):
    assert conn.execute("SELECT version FROM schema_version").fetchone()[0] == SCHEMA_VERSION
    assert TABLES <= _table_names(conn)
    assert "config_revision" in _columns(conn, "sessions")
    assert set(EXECUTION_MEASURE_COLUMNS) <= _columns(conn, "executions")
    assert set(SOURCE_ISSUE_MERGE_COLUMNS) <= _columns(conn, "source_issues")
    assert _columns(conn, "task_events") == {
        "id", "task_id", "session_id", "type", "task_revision", "config_revision", "occurred_at", "data_json",
    }
    assert _columns(conn, "baseline_items") == {
        "source_id", "issue_number", "issue_title", "issue_opened_at", "pr_number", "pr_merged_at", "fetched_at",
    }
    assert _columns(conn, "baseline_imports") == {"source_id", "opened_before", "fetched_at", "item_count"}
    assert {("tasks", "task_id", "task_id"), ("sessions", "session_id", "session_id")} <= _foreign_keys(
        conn, "task_events")
    assert {("github_sources", "source_id", "source_id")} <= _foreign_keys(conn, "baseline_items")
    assert {("github_sources", "source_id", "source_id")} <= _foreign_keys(conn, "baseline_imports")
    indexed = {
        tuple(r["name"] for r in conn.execute(f"PRAGMA index_info({i['name']})"))
        for i in conn.execute("PRAGMA index_list(task_events)")
    }
    assert {("task_id", "id"), ("session_id", "occurred_at")} <= indexed


def test_task_event_types_constant():
    from workflow.adapters.db import TASK_EVENT_TYPES

    assert TASK_EVENT_TYPES == ("status_changed", "blocked", "ready")


def test_phase9_checks(conn):
    _cycle_base(conn)
    assert conn.execute("SELECT config_revision FROM sessions WHERE session_id = 's1'").fetchone()[0] == 1
    with pytest.raises(sqlite3.IntegrityError):  # config_revision >= 1
        conn.execute("UPDATE sessions SET config_revision = 0 WHERE session_id = 's1'")
    row = conn.execute(f"SELECT {', '.join(EXECUTION_MEASURE_COLUMNS)} FROM executions").fetchone()
    assert tuple(row) == (None,) * len(EXECUTION_MEASURE_COLUMNS)  # NULL = 모름
    for column, value in (("folder_dirty", 2), ("cost_usd", -0.01), ("input_tokens", -1), ("output_tokens", -1)):
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(f"UPDATE executions SET {column} = ? WHERE execution_id = 'e1'", (value,))
    conn.execute("UPDATE executions SET folder_dirty = 1, cost_usd = 0, input_tokens = 0, output_tokens = 5,"
                 " folder_commit = ?, config_revision = 1 WHERE execution_id = 'e1'", ("a" * 40,))

    event = ("INSERT INTO task_events (task_id, session_id, type, task_revision, config_revision, occurred_at,"
             " data_json) VALUES (?, 's1', ?, 1, 1, ?, '{}')")
    for event_type in ("status_changed", "blocked", "ready"):
        conn.execute(event, ("t1", event_type, NOW))
    for params in (("t1", "started", NOW), ("nope", "ready", NOW), ("t1", "ready", None)):
        with pytest.raises(sqlite3.IntegrityError):  # type 허용 값 밖 / 없는 Task / NOT NULL
            conn.execute(event, params)

    item = ("INSERT INTO baseline_items (source_id, issue_number, issue_title, issue_opened_at, pr_number,"
            " pr_merged_at, fetched_at) VALUES (?, 41, 't', ?, ?, ?, ?)")
    conn.execute(item, ("ghs-00000001", NOW, 7, NOW, NOW))
    conn.execute(item, ("ghs-00000001", NOW, 8, NOW, NOW))  # 이슈 하나에 병합 PR 여럿
    for params in (("ghs-00000001", NOW, 7, NOW, NOW), ("ghs-nope", NOW, 9, NOW, NOW)):
        with pytest.raises(sqlite3.IntegrityError):  # (source_id, issue_number, pr_number) 유일 / 없는 소스
            conn.execute(item, params)
    imports = ("INSERT INTO baseline_imports (source_id, opened_before, fetched_at, item_count)"
               " VALUES (?, ?, ?, 2)")
    conn.execute(imports, ("ghs-00000001", NOW, NOW))
    for source_id in ("ghs-00000001", "ghs-nope"):
        with pytest.raises(sqlite3.IntegrityError):  # 소스당 하나 / 없는 소스
            conn.execute(imports, (source_id, NOW, NOW))


def test_migrates_v5_to_v6_preserving_data(db_path):
    c = _v5_db(db_path)
    columns = _column_lists(c, V5_TABLES)
    before = _dump(c, V5_TABLES)
    c.close()

    c = connect(db_path)
    init_schema(c)
    assert [tuple(r) for r in c.execute("SELECT version FROM schema_version")] == [(SCHEMA_VERSION,)]
    assert TABLES <= _table_names(c)
    assert _dump(c, V5_TABLES, columns) == before  # 기존 행·열 값은 하나도 바뀌지 않는다
    assert [tuple(r) for r in c.execute("SELECT session_id, config_revision FROM sessions ORDER BY 1")] == [
        ("s1", 1), ("s2", 1),
    ]
    row = c.execute(f"SELECT {', '.join(EXECUTION_MEASURE_COLUMNS)} FROM executions").fetchone()
    assert tuple(row) == (None,) * len(EXECUTION_MEASURE_COLUMNS)
    row = c.execute(f"SELECT {', '.join(SOURCE_ISSUE_MERGE_COLUMNS)} FROM source_issues").fetchone()
    assert tuple(row) == (None,) * len(SOURCE_ISSUE_MERGE_COLUMNS)  # 병합은 아직 모름 — 추정해 채우지 않는다
    for table in PHASE9_TABLES:  # 과거 이벤트를 추정해 채우지 않는다
        assert c.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
    assert c.execute("PRAGMA foreign_key_check").fetchall() == []
    assert c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"

    after = _dump(c, TABLES)
    init_schema(c)  # 재실행은 아무것도 바꾸지 않는다
    assert _dump(c, TABLES) == after
    c.close()


def test_migrates_v4_to_v6_in_one_go(db_path):
    c = _v4_db(db_path)
    c.close()
    c = connect(db_path)
    init_schema(c)
    assert c.execute("SELECT version FROM schema_version").fetchone()[0] == SCHEMA_VERSION
    assert TABLES <= _table_names(c)
    assert "supported_kinds_json" in _columns(c, "connectors")
    assert [r[0] for r in c.execute("SELECT DISTINCT config_revision FROM sessions")] == [1]
    assert [tuple(r) for r in c.execute("SELECT config_revision, cost_usd FROM executions")] == [(None, None)]
    c.close()


def test_v4_migration_rolls_back_entirely_when_5_to_6_fails(db_path, monkeypatch):
    """4 → 5 → 6 은 한 트랜잭션 — 뒤쪽이 실패하면 5 로도 남지 않고 4 그대로다."""
    from workflow.adapters import db

    c = _v4_db(db_path)
    before = _dump(c, V4_TABLES)

    def boom(conn):
        raise sqlite3.OperationalError("5→6 실패")

    monkeypatch.setattr(db, "_migrate_5_to_6", boom)
    with pytest.raises(sqlite3.OperationalError, match="5→6 실패"):
        init_schema(c)
    assert c.execute("SELECT version FROM schema_version").fetchone()[0] == 4
    assert _table_names(c) - {"sqlite_sequence"} == V4_TABLES | {"schema_version"}
    assert _dump(c, V4_TABLES) == before
    assert not c.in_transaction
    c.close()


def test_v5_migration_rolls_back_when_a_later_statement_fails(db_path, monkeypatch):
    from workflow.adapters import db

    c = _v5_db(db_path)
    before = _dump(c, V5_TABLES)
    # 새 칸을 더한 뒤 마지막 새 테이블 문장에서 실패하게 한다(이미 있는 이름).
    monkeypatch.setattr(db, "_V6_TABLES", db._V6_TABLES + "\nCREATE TABLE sessions (x TEXT);\n")
    with pytest.raises(sqlite3.OperationalError):
        init_schema(c)
    assert c.execute("SELECT version FROM schema_version").fetchone()[0] == 5
    assert _table_names(c) - {"sqlite_sequence"} == V5_TABLES | {"schema_version"}
    assert _dump(c, V5_TABLES) == before
    assert "config_revision" not in _columns(c, "sessions")
    assert not set(EXECUTION_MEASURE_COLUMNS) & _columns(c, "executions")
    assert not set(SOURCE_ISSUE_MERGE_COLUMNS) & _columns(c, "source_issues")
    assert not c.in_transaction
    monkeypatch.undo()
    init_schema(c)  # 원인이 사라지면 다시 돌릴 수 있다
    assert c.execute("SELECT version FROM schema_version").fetchone()[0] == SCHEMA_VERSION
    c.close()


# --- phase 11: v6 → v7 실행 지시 (ADR-0017, ARCHITECTURE "실행 지시") --------------------------------------

SOURCE_ISSUE_DELEGATION_COLUMNS = ("delegated_at", "delegated_by")


def _v6_db(db_path):
    """phase 9·10 서버가 남긴 모양의 v6 DB — v5 fixture 를 그때의 5 → 6 마이그레이션으로 올린다."""
    from workflow.adapters import db

    c = _v5_db(db_path)
    c.execute("BEGIN IMMEDIATE")
    db._migrate_5_to_6(c)
    c.execute("COMMIT")
    return c


def test_v6_fixture_has_no_delegation_columns(db_path):
    c = _v6_db(db_path)
    assert c.execute("SELECT version FROM schema_version").fetchone()[0] == 6
    assert not set(SOURCE_ISSUE_DELEGATION_COLUMNS) & _columns(c, "source_issues")
    c.close()


def test_fresh_db_has_delegation_columns_with_check(conn):
    _cycle_base(conn)
    assert set(SOURCE_ISSUE_DELEGATION_COLUMNS) <= _columns(conn, "source_issues")
    conn.execute("INSERT INTO source_issues (source_id, github_issue_id, issue_number, task_id, source_revision,"
                 " snapshot_json, snapshot_digest, issue_updated_at, state, created_at, updated_at)"
                 " VALUES ('ghs-00000001', 100, 41, 't1', 1, '{}', 'd', ?, 'open', ?, ?)", (NOW, NOW, NOW))
    row = conn.execute("SELECT delegated_at, delegated_by FROM source_issues").fetchone()
    assert tuple(row) == (None, None)  # 지시 전
    for by in ("operator", "label"):
        conn.execute("UPDATE source_issues SET delegated_at = ?, delegated_by = ?", (NOW, by))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE source_issues SET delegated_by = 'agent'")


def test_migrates_v6_to_v7_preserving_data(db_path):
    c = _v6_db(db_path)
    v6_tables = TABLES - PHASE12_TABLES
    before = _dump(c, v6_tables)
    c.close()

    c = connect(db_path)
    init_schema(c)
    assert [tuple(r) for r in c.execute("SELECT version FROM schema_version")] == [(SCHEMA_VERSION,)]
    columns = _column_lists(c, v6_tables)
    for table, names in columns.items():
        columns[table] = [n for n in names if n not in SOURCE_ISSUE_DELEGATION_COLUMNS and n != "branch_pushed"]
    assert _dump(c, v6_tables, columns) == before  # 기존 행·열 값은 그대로
    row = c.execute("SELECT delegated_at, delegated_by FROM source_issues").fetchone()
    assert tuple(row) == (None, None)  # 옛 소스는 filtered 라 지시 칸을 보지 않는다 — 추정해 채우지 않는다
    assert c.execute("PRAGMA foreign_key_check").fetchall() == []
    after = _dump(c, TABLES)
    init_schema(c)
    assert _dump(c, TABLES) == after
    c.close()


# --- phase 12: v7 → v8 초안 PR·알림 대기열 (ADR-0018, ARCHITECTURE "스키마 v8") ---------------------------


def _v7_db(db_path):
    """phase 11 서버가 남긴 모양의 v7 DB — v6 fixture 를 그때의 6 → 7 로 올리고, step 5 러너가 보낸 push 결과를
    이벤트에만 남긴 실행을 하나 더한다(v7 에는 `executions.branch_pushed` 칸이 없다)."""
    from workflow.adapters import db

    c = _v6_db(db_path)
    c.execute("BEGIN IMMEDIATE")
    db._migrate_6_to_7(c)
    c.execute("COMMIT")
    c.execute(
        "INSERT INTO executions (execution_id, task_id, attempt_no, start_key, agent_id, kind, request_json,"
        " status, created_at) VALUES ('e2', 't1', 2, 'k2', 'a1', 'bug_fix', '{}', 'result_ready', ?)", (NOW,)
    )
    c.execute("INSERT INTO execution_events (execution_id, seq, type, occurred_at, received_at, data_json, actor)"
              " VALUES ('e2', 1, 'result_ready', ?, ?, ?, 'conn-1')",
              (NOW, NOW, json.dumps({"result_artifact_id": "art-2", "branch_pushed": True})))
    return c


def test_v7_fixture_has_no_phase12_tables(db_path):
    c = _v7_db(db_path)
    assert c.execute("SELECT version FROM schema_version").fetchone()[0] == 7
    assert not PHASE12_TABLES & _table_names(c)
    assert "branch_pushed" not in _columns(c, "executions")
    c.close()


def test_migrates_v7_to_v8_preserving_data_and_copies_pushed_results(db_path):
    c = _v7_db(db_path)
    v7_tables = TABLES - PHASE12_TABLES
    columns = _column_lists(c, v7_tables)
    before = _dump(c, v7_tables)
    c.close()

    c = connect(db_path)
    init_schema(c)
    assert [tuple(r) for r in c.execute("SELECT version FROM schema_version")] == [(8,)]
    assert TABLES <= _table_names(c)
    assert _dump(c, v7_tables, columns) == before  # 기존 행·열 값은 그대로
    pushed = dict(c.execute("SELECT execution_id, branch_pushed FROM executions").fetchall())
    assert pushed == {"e1": None, "e2": 1}  # 이벤트에 남은 보고만 옮긴다 — 없으면 모름
    for table in PHASE12_TABLES:
        assert c.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
    assert c.execute("PRAGMA foreign_key_check").fetchall() == []
    after = _dump(c, TABLES)
    init_schema(c)
    assert _dump(c, TABLES) == after
    c.close()


def test_migrates_v4_all_the_way_to_v8(db_path):
    c = _v4_db(db_path)
    c.close()
    c = connect(db_path)
    init_schema(c)
    assert c.execute("SELECT version FROM schema_version").fetchone()[0] == 8
    assert TABLES <= _table_names(c)
    assert "branch_pushed" in _columns(c, "executions")
    c.close()


_PR_INSERT = (
    "INSERT INTO task_pull_requests (task_id, session_id, source_id, repository_full_name, issue_number, head_branch,"
    " fix_execution_id, review_execution_id, state, pr_number, created_at, updated_at)"
    " VALUES (?, 's1', ?, 'acme/billing', 41, 'task/t1', 'e1', 'e1', ?, ?, ?, ?)"
)


def test_fresh_db_task_pull_requests_checks(conn):
    _cycle_base(conn)
    assert "branch_pushed" in _columns(conn, "executions")
    for value in (None, 0, 1):
        conn.execute("UPDATE executions SET branch_pushed = ? WHERE execution_id = 'e1'", (value,))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE executions SET branch_pushed = 2 WHERE execution_id = 'e1'")

    conn.execute(_PR_INSERT, ("t1", "ghs-00000001", "pending", None, NOW, NOW))
    with pytest.raises(sqlite3.IntegrityError):  # 수정 Task 하나에 PR 하나
        conn.execute(_PR_INSERT, ("t1", "ghs-00000001", "pending", None, NOW, NOW))
    conn.execute("DELETE FROM task_pull_requests")
    for params in (
        ("t1", "ghs-00000001", "open", None, NOW, NOW),  # 열림·병합·닫힘은 번호가 있어야 한다
        ("t1", "ghs-00000001", "draft", 3, NOW, NOW),  # 상태 허용 값 밖
        ("nope", "ghs-00000001", "pending", None, NOW, NOW),  # 없는 Task
        ("t1", "ghs-nope", "pending", None, NOW, NOW),  # 없는 소스
    ):
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(_PR_INSERT, params)
    conn.execute(_PR_INSERT, ("t1", "ghs-00000001", "merged", 7, NOW, NOW))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE task_pull_requests SET draft = 2")


def test_fresh_db_notifications_checks(conn):
    _cycle_base(conn)
    insert = ("INSERT INTO notifications (notification_id, session_id, event, task_id, dedupe_key, content,"
              " payload_json, state, created_at) VALUES (?, 's1', ?, 't1', ?, 'c', '{}', ?, ?)")
    conn.execute(insert, ("ntf-1", "human_request", "human_request:hr-1", "pending", NOW))
    for params in (
        ("ntf-2", "human_request", "human_request:hr-1", "pending", NOW),  # 중복 키
        ("ntf-3", "done", "k3", "pending", NOW),  # 사건 허용 값 밖
        ("ntf-4", "pr_opened", "k4", "queued", NOW),  # 상태 허용 값 밖
    ):
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(insert, params)
    assert "url" not in " ".join(_columns(conn, "notifications"))  # URL 은 비밀 파일에만
