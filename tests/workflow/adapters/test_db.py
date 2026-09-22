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


def test_schema_version_is_5():
    assert SCHEMA_VERSION == 5


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
V4_TABLES = TABLES - {
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


def _dump(conn, tables) -> dict[str, list[tuple]]:
    return {t: [tuple(r) for r in conn.execute(f"SELECT * FROM {t} ORDER BY rowid")] for t in sorted(tables)}


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
    before = _dump(c, V4_TABLES - {"kinds", "succession_rules", "connectors"})
    kinds_before = _dump(c, {"kinds", "succession_rules"})
    c.close()

    c = connect(db_path)
    init_schema(c)
    assert [tuple(r) for r in c.execute("SELECT version FROM schema_version")] == [(SCHEMA_VERSION,)]
    assert TABLES <= _table_names(c)
    assert _dump(c, V4_TABLES - {"kinds", "succession_rules", "connectors"}) == before
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


def test_fresh_schema_matches_migrated_schema(tmp_path):
    """새로 만든 DB 와 v4 에서 올린 DB 의 테이블·열 정의가 같다(순서 무관)."""
    fresh = connect(tmp_path / "fresh.sqlite")
    init_schema(fresh)
    migrated = _v4_db(tmp_path / "old.sqlite")
    init_schema(migrated)
    for table in TABLES:
        cols = "SELECT name, type, \"notnull\", dflt_value, pk FROM pragma_table_info(?) ORDER BY name"
        assert fresh.execute(cols, (table,)).fetchall() == migrated.execute(cols, (table,)).fetchall(), table
        assert _foreign_keys(fresh, table) == _foreign_keys(migrated, table), table
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
