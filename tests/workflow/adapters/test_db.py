"""db.py — 연결 설정과 스키마 (ARCHITECTURE "DB 제약과 실행 잠금")."""

import json
import sqlite3
import threading
from pathlib import Path

import pytest

from workflow.adapters.db import SCHEMA_VERSION, connect, init_schema
from workflow.contracts.v1 import BUILTIN_KINDS, BUILTIN_RULES, KindSpec, SuccessorRule

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
    "work_items",
    "work_item_links",
    "members",
    "field_mappings",
    "work_item_events",
    "login_sessions",
    "member_invites",
}
PHASE9_TABLES = {"task_events", "baseline_items", "baseline_imports"}
PHASE12_TABLES = {"task_pull_requests", "notifications"}
PHASE14_TABLES = {"work_items", "work_item_links", "members", "field_mappings", "work_item_events"}
PHASE15_TABLES = {"login_sessions", "member_invites"}
V10_TABLES = TABLES - PHASE15_TABLES
V9_TABLES = V10_TABLES - PHASE14_TABLES

# phase 13 이전(v4~v8) 서버가 모든 세션에 seed 하던 진단 데모 내장 종류·규칙 (ADR-0019). 지금 계약은 이 이름을
# 내장으로 받지 않으므로 검증 없이 만든다 — 옛 DB 행 모양 그대로다.
LEGACY_KIND_NAMES = ("diagnosis", "code_change")
LEGACY_KINDS = (
    KindSpec.model_construct(
        kind="diagnosis", label="진단", capability_code="operations.diagnose", scope_key="workflow_id",
        input_kinds=[], output_kind="diagnosis_result",
        outcomes=["ready_for_handoff", "needs_information"], instructions="", builtin=True,
    ),
    KindSpec.model_construct(
        kind="code_change", label="코드 수정", capability_code="code.modify", scope_key="repository_id",
        input_kinds=["diagnosis_result", "evidence"], output_kind="code_change_result",
        outcomes=["ready_for_review", "needs_information"], instructions="", builtin=True,
    ),
)
LEGACY_RULE = SuccessorRule(
    from_kind="diagnosis", on_outcomes=["ready_for_handoff"], to_kind="code_change",
    handoff_kinds=["diagnosis_result", "evidence"],
)
V8_BUILTIN_KINDS = (*LEGACY_KINDS, *BUILTIN_KINDS)


def _without_legacy(dump: dict[str, list[tuple]]) -> dict[str, list[tuple]]:
    """v9 가 지우는 행(진단·코드 수정 종류와 그 둘의 규칙)을 뺀 덤프 — 나머지 행은 그대로여야 한다."""
    return {
        table: [row for row in rows if not (table in ("kinds", "succession_rules") and set(row) & set(LEGACY_KIND_NAMES))]
        for table, rows in dump.items()
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


def test_schema_version_is_11():
    assert SCHEMA_VERSION == 11


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
V4_TABLES = V10_TABLES - PHASE9_TABLES - PHASE12_TABLES - PHASE14_TABLES - {
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
        for spec in LEGACY_KINDS:
            c.execute("INSERT INTO kinds (session_id, kind, spec_json, created_at) VALUES (?, ?, ?, ?)",
                      (sid, spec.kind, spec.model_dump_json(), NOW))
        c.execute(
            "INSERT INTO succession_rules (rule_id, session_id, from_kind, to_kind, rule_json, created_at)"
            " VALUES (?, ?, 'diagnosis', 'code_change', ?, ?)",
            (f"rule-{sid}", sid, LEGACY_RULE.model_dump_json(), NOW),
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
    for table, rows in _without_legacy(kinds_before).items():  # 기존 종류·규칙 행은 그대로(v9 가 진단 둘만 지움), 새 내장만 더해진다
        assert set(rows) <= set(_dump(c, {table})[table])
    assert not set(LEGACY_KIND_NAMES) & {r[0] for r in c.execute("SELECT kind FROM kinds")}
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
        assert [r["rule_json"] for r in rules] == [BUILTIN_RULES[0].model_dump_json()]
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
V5_TABLES = V10_TABLES - PHASE9_TABLES - PHASE12_TABLES - PHASE14_TABLES
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
        for spec in V8_BUILTIN_KINDS:
            c.execute("INSERT INTO kinds (session_id, kind, spec_json, created_at) VALUES (?, ?, ?, ?)",
                      (sid, spec.kind, spec.model_dump_json(), NOW))
    c.execute("INSERT INTO kinds (session_id, kind, spec_json, created_at) VALUES ('s1', 'review', ?, ?)",
              (json.dumps({"kind": "review", "builtin": False}), NOW))
    c.execute(
        "INSERT INTO succession_rules (rule_id, session_id, from_kind, to_kind, rule_json, created_at)"
        " VALUES ('rule-1', 's1', 'bug_fix', 'code_review', ?, ?)",
        (BUILTIN_RULES[0].model_dump_json(), NOW),
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
    assert _dump(c, V5_TABLES, columns) == _without_legacy(before)  # 기존 행·열 값은 하나도 바뀌지 않는다
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
    v6_tables = V9_TABLES - PHASE12_TABLES
    columns = _column_lists(c, v6_tables)
    before = _dump(c, v6_tables)
    c.close()

    c = connect(db_path)
    init_schema(c)
    assert [tuple(r) for r in c.execute("SELECT version FROM schema_version")] == [(SCHEMA_VERSION,)]
    assert _dump(c, v6_tables, columns) == _without_legacy(before)  # 기존 행·열 값은 그대로
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
    v7_tables = V9_TABLES - PHASE12_TABLES
    columns = _column_lists(c, v7_tables)
    before = _dump(c, v7_tables)
    c.close()

    c = connect(db_path)
    init_schema(c)
    assert [tuple(r) for r in c.execute("SELECT version FROM schema_version")] == [(SCHEMA_VERSION,)]
    assert TABLES <= _table_names(c)
    assert _dump(c, v7_tables, columns) == _without_legacy(before)  # 기존 행·열 값은 그대로
    pushed = dict(c.execute("SELECT execution_id, branch_pushed FROM executions").fetchall())
    assert pushed == {"e1": None, "e2": 1}  # 이벤트에 남은 보고만 옮긴다 — 없으면 모름
    for table in PHASE12_TABLES:
        assert c.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
    assert c.execute("PRAGMA foreign_key_check").fetchall() == []
    after = _dump(c, TABLES)
    init_schema(c)
    assert _dump(c, TABLES) == after
    c.close()


def test_migrates_v4_all_the_way_to_v11(db_path):
    c = _v4_db(db_path)
    c.close()
    c = connect(db_path)
    init_schema(c)
    assert c.execute("SELECT version FROM schema_version").fetchone()[0] == 11
    assert TABLES <= _table_names(c)
    assert "branch_pushed" in _columns(c, "executions")
    assert c.execute("SELECT COUNT(*) FROM tasks WHERE work_item_id IS NULL").fetchone()[0] == 0
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


# --- phase 13: v8 → v9 진단 데모 내장 종류 삭제 (ADR-0019 결정 4) ------------------------------------------


def _v8_db(db_path):
    """phase 12 서버가 남긴 모양의 v8 DB — 두 세션 모두 옛 내장 4종류와 옛 내장 규칙 둘을 가진다."""
    from workflow.adapters import db

    c = _v7_db(db_path)
    c.execute("BEGIN IMMEDIATE")
    db._migrate_7_to_8(c)
    c.execute("COMMIT")
    for sid in ("s1", "s2"):
        c.execute(
            "INSERT INTO succession_rules (rule_id, session_id, from_kind, to_kind, rule_json, created_at)"
            " VALUES (?, ?, 'diagnosis', 'code_change', ?, ?)",
            (f"rule-legacy-{sid}", sid, LEGACY_RULE.model_dump_json(), NOW),
        )
    return c


def test_v8_fixture_has_legacy_kinds(db_path):
    c = _v8_db(db_path)
    assert c.execute("SELECT version FROM schema_version").fetchone()[0] == 8
    kinds = {tuple(r) for r in c.execute("SELECT session_id, kind FROM kinds WHERE kind IN ('diagnosis', 'code_change')")}
    assert kinds == {(s, k) for s in ("s1", "s2") for k in LEGACY_KIND_NAMES}
    c.close()


def test_fresh_db_seeds_two_builtin_kinds_and_one_rule(conn):
    from workflow.adapters import repo

    assert conn.execute("SELECT version FROM schema_version").fetchone()[0] == SCHEMA_VERSION
    repo.create_session(conn, "s1", NOW)
    kinds = [r["kind"] for r in conn.execute("SELECT kind FROM kinds WHERE session_id = 's1' ORDER BY kind")]
    assert kinds == ["bug_fix", "code_review"]
    rules = [tuple(r) for r in conn.execute("SELECT from_kind, to_kind FROM succession_rules WHERE session_id = 's1'")]
    assert rules == [("bug_fix", "code_review")]


def test_migrates_v8_to_v9_dropping_legacy_kinds_and_rules(db_path):
    c = _v8_db(db_path)
    columns = _column_lists(c, V9_TABLES)
    before = _dump(c, V9_TABLES)
    c.close()

    c = connect(db_path)
    init_schema(c)
    assert [tuple(r) for r in c.execute("SELECT version FROM schema_version")] == [(SCHEMA_VERSION,)]
    assert _dump(c, V9_TABLES, columns) == _without_legacy(before)  # 두 종류·그 규칙만 사라지고 나머지는 그대로
    for sid in ("s1", "s2"):
        kinds = {r["kind"] for r in c.execute("SELECT kind FROM kinds WHERE session_id = ?", (sid,))}
        assert not set(LEGACY_KIND_NAMES) & kinds and {"bug_fix", "code_review"} <= kinds
    rules = {tuple(r) for r in c.execute("SELECT session_id, from_kind, to_kind FROM succession_rules")}
    assert rules == {("s1", "bug_fix", "code_review")}
    assert c.execute("PRAGMA foreign_key_check").fetchall() == []
    after = _dump(c, TABLES)
    init_schema(c)
    assert _dump(c, TABLES) == after
    c.close()


def _assert_v9_aborted(db_path, c, match: str) -> None:
    before = _dump(c, V9_TABLES)
    c.close()
    c = connect(db_path)
    with pytest.raises(RuntimeError, match=match):
        init_schema(c)
    assert c.execute("SELECT version FROM schema_version").fetchone()[0] == 8
    assert _dump(c, V9_TABLES) == before
    assert not PHASE14_TABLES & _table_names(c)
    assert not c.in_transaction
    c.close()


def test_v9_migration_aborts_when_a_legacy_task_exists(db_path):
    c = _v8_db(db_path)
    _insert_task(c, "t-diag", "s2", "diagnosis")
    c.execute(
        "INSERT INTO executions (execution_id, task_id, attempt_no, start_key, agent_id, kind, request_json,"
        " status, created_at) VALUES ('e-diag', 't-diag', 1, 'kd', 'a1', 'diagnosis', '{}', 'failed', ?)", (NOW,)
    )
    _assert_v9_aborted(db_path, c, r"s2:diagnosis")


def test_v9_migration_aborts_when_a_user_rule_uses_a_legacy_kind(db_path):
    c = _v8_db(db_path)
    c.execute(
        "INSERT INTO succession_rules (rule_id, session_id, from_kind, to_kind, rule_json, created_at)"
        " VALUES ('rule-user', 's1', 'code_change', 'review', '{}', ?)", (NOW,)
    )
    _assert_v9_aborted(db_path, c, r"s1:code_change")


# --- phase 14: v9 → v10 업무·링크·멤버·매핑 (ADR-0020, ARCHITECTURE "업무와 단계 — phase 14") ------------------

V9_SCHEMA = (Path(__file__).parent / "fixtures" / "schema_v9.sql").read_text()
WORK_ITEM_COLUMNS = {
    "work_item_id", "session_id", "key_number", "title", "request", "kind", "priority", "assignee_type",
    "assignee_id", "status", "status_reason", "source_type", "source_id", "source_item_id", "source_key",
    "source_url", "source_state", "form_json", "revision", "created_at", "updated_at", "closed_at",
}


def _t(n: int) -> str:
    return f"2026-09-28T00:00:{n:02d}Z"


def _v9_task(c, task_id: str, session_id: str, kind: str, n: int, *, status: str, reason: str,
             predecessor: str | None = None, agent: str | None = None, chain: str | None = None,
             source_ref: str | None = None) -> None:
    c.execute(
        "INSERT INTO tasks (task_id, session_id, title, request, kind, required_capability_json, selection_mode,"
        " chosen_agent_id, run_mode, completion_mode, criteria_json, predecessor_task_id, revision, target_json,"
        " status, status_reason, created_at, chain_id, source_ref) VALUES (?, ?, ?, ?, ?, '{}', 'auto', ?, 'auto',"
        " 'review', '[]', ?, 1, '{}', ?, ?, ?, ?, ?)",
        (task_id, session_id, f"제목 {task_id}", f"요청 {task_id}", kind, agent, predecessor, status, reason,
         _t(n), chain, source_ref),
    )


def _v9_execution(c, execution_id: str, task_id: str, attempt: int, kind: str, status: str, n: int,
                  start_key: str = "start") -> None:
    released = None if status in ("queued", "accepted", "running") else _t(n)
    c.execute(
        "INSERT INTO executions (execution_id, task_id, attempt_no, start_key, agent_id, kind, request_json, status,"
        " created_at, released_at) VALUES (?, ?, ?, ?, 'a1', ?, '{}', ?, ?, ?)",
        (execution_id, task_id, attempt, start_key, kind, status, _t(n), released),
    )


def _v9_issue(c, number: int, task_id: str, state: str = "open") -> None:
    snapshot = {"number": number, "html_url": f"https://example.invalid/{number}", "state": state}
    c.execute(
        "INSERT INTO source_issues (source_id, github_issue_id, issue_number, task_id, source_revision, snapshot_json,"
        " snapshot_digest, issue_updated_at, state, created_at, updated_at)"
        " VALUES ('ghs-00000001', ?, ?, ?, 1, ?, 'd', ?, ?, ?, ?)",
        (1000 + number, number, task_id, json.dumps(snapshot), NOW, state, NOW, NOW),
    )


def _followup(c, cause: str, task_id: str) -> None:
    c.execute("INSERT INTO followup_links (session_id, cause_execution_id, to_kind, task_id, rules_revision,"
              " created_at) VALUES ('s1', ?, 'code_review', ?, 1, ?)", (cause, task_id, NOW))


def _v9_db(db_path):
    """phase 13 서버가 남긴 모양의 v9 DB. 워크스페이스 s1 — GitHub 이슈 41(수정 + 검토 + 재작업·재검토, PR 열림),
    이슈 42(검토 마감 뒤 두 번째 검토 Task 실행 중), n8n 체인 2업무(선행 있음), 직접 등록 1건(실패).
    워크스페이스 s2 — 직접 등록 1건(키가 워크스페이스마다 1 부터인지 본다)."""
    c = connect(db_path)
    c.executescript(V9_SCHEMA)
    c.execute("INSERT INTO schema_version (version) VALUES (9)")
    for sid in ("s1", "s2"):
        c.execute("INSERT INTO sessions (session_id, created_at, is_operator) VALUES (?, ?, ?)",
                  (sid, NOW, int(sid == "s1")))
        for spec in BUILTIN_KINDS:
            c.execute("INSERT INTO kinds (session_id, kind, spec_json, created_at) VALUES (?, ?, ?, ?)",
                      (sid, spec.kind, spec.model_dump_json(), NOW))
        c.execute("INSERT INTO succession_rules (rule_id, session_id, from_kind, to_kind, rule_json, created_at)"
                  " VALUES (?, ?, 'bug_fix', 'code_review', ?, ?)",
                  (f"rule-{sid}", sid, BUILTIN_RULES[0].model_dump_json(), NOW))
    c.execute("INSERT INTO agents (agent_id, name, owner_scope, connection_type, connector_id, capabilities_json,"
              " connection_state) VALUES ('a1', 'claude', 'personal', 'local', 'conn-1', '[]', 'online')")
    c.execute("INSERT INTO github_sources (source_id, session_id, repository_full_name, config_json,"
              " created_at, updated_at) VALUES ('ghs-00000001', 's1', 'acme/billing', '{}', ?, ?)", (NOW, NOW))
    c.execute("INSERT INTO chains (chain_id, session_id, title, source, created_at) VALUES ('c1', 's1', 'n8n', 'n8n', ?)",
              (NOW,))

    # 이슈 41: 수정 → 검토(변경 요청) → 재작업 → 재검토 → PR 열림
    _v9_task(c, "t-fix-41", "s1", "bug_fix", 1, status="완료", reason="완료", agent="a1",
             source_ref="acme/billing#41")
    _v9_issue(c, 41, "t-fix-41")
    _v9_execution(c, "e-f1", "t-fix-41", 1, "bug_fix", "result_ready", 1)
    _v9_task(c, "t-rev-41", "s1", "code_review", 2, status="완료", reason="완료", predecessor="t-fix-41", agent="a1")
    _followup(c, "e-f1", "t-rev-41")
    _v9_execution(c, "e-r1", "t-rev-41", 1, "code_review", "result_ready", 2)
    _v9_execution(c, "e-f2", "t-fix-41", 2, "bug_fix", "result_ready", 3, start_key="rework:e-r1")
    _v9_execution(c, "e-r2", "t-rev-41", 2, "code_review", "result_ready", 4, start_key="rereview")
    c.execute("INSERT INTO task_pull_requests (task_id, session_id, source_id, repository_full_name, issue_number,"
              " head_branch, fix_execution_id, review_execution_id, state, pr_number, created_at, updated_at)"
              " VALUES ('t-fix-41', 's1', 'ghs-00000001', 'acme/billing', 41, 'task/t-fix-41', 'e-f2', 'e-r2',"
              " 'open', 12, ?, ?)", (NOW, NOW))

    # 이슈 42: 첫 검토 마감 뒤 수정이 다시 결과를 내 두 번째 검토 Task 가 생겼다
    _v9_task(c, "t-fix-42", "s1", "bug_fix", 5, status="완료", reason="완료", agent="a1",
             source_ref="acme/billing#42")
    _v9_issue(c, 42, "t-fix-42")
    _v9_execution(c, "e-f3", "t-fix-42", 1, "bug_fix", "result_ready", 5)
    _v9_task(c, "t-rev-42a", "s1", "code_review", 6, status="완료", reason="완료", predecessor="t-fix-42", agent="a1")
    _followup(c, "e-f3", "t-rev-42a")
    _v9_execution(c, "e-r3", "t-rev-42a", 1, "code_review", "result_ready", 6)
    _v9_execution(c, "e-f4", "t-fix-42", 2, "bug_fix", "result_ready", 7, start_key="rerun")
    _v9_task(c, "t-rev-42b", "s1", "code_review", 8, status="실행 중", reason="실행 중", predecessor="t-fix-42",
             agent="a1")
    _followup(c, "e-f4", "t-rev-42b")
    _v9_execution(c, "e-r4", "t-rev-42b", 1, "code_review", "running", 8)

    # n8n 체인: OPS-2 가 OPS-1 뒤
    _v9_task(c, "t-n1", "s1", "bug_fix", 10, status="대기", reason="담당 에이전트 없음", chain="c1", source_ref="OPS-1")
    _v9_task(c, "t-n2", "s1", "bug_fix", 11, status="대기", reason="선행 대기", predecessor="t-n1", agent="a1",
             chain="c1", source_ref="OPS-2")

    # 직접 등록: 실행 실패로 마감
    _v9_task(c, "t-m1", "s1", "bug_fix", 12, status="실패", reason="실행 실패 — timeout", agent="a1")
    _v9_execution(c, "e-m1", "t-m1", 1, "bug_fix", "failed", 12)

    _v9_task(c, "t-s2", "s2", "bug_fix", 13, status="대기", reason="담당 에이전트 없음")
    return c


def _work_of(c, task_id: str):
    return c.execute("SELECT w.* FROM work_items w JOIN tasks t ON t.work_item_id = w.work_item_id"
                     " WHERE t.task_id = ?", (task_id,)).fetchone()


def test_fresh_db_has_v10_tables_columns_and_checks(conn):
    from workflow.domain.work_status import WORK_STATUSES

    assert conn.execute("SELECT version FROM schema_version").fetchone()[0] == SCHEMA_VERSION
    assert PHASE14_TABLES <= _table_names(conn)
    assert _columns(conn, "work_items") == WORK_ITEM_COLUMNS | {"requested_by_member_id"}  # v11
    assert _columns(conn, "work_item_links") == {
        "from_work_item_id", "to_work_item_id", "type", "cause_execution_id", "created_at",
    }
    assert _columns(conn, "members") == {
        "member_id", "session_id", "display_name", "role", "created_at", "email", "password_hash", "disabled_at",
    }
    assert _columns(conn, "field_mappings") == {
        "mapping_id", "session_id", "source_type", "field", "source_value", "runloom_value", "position", "created_at",
    }
    assert _columns(conn, "work_item_events") == {
        "id", "work_item_id", "session_id", "type", "config_revision", "occurred_at", "data_json",
    }
    assert "work_item_id" in _columns(conn, "tasks")
    assert ("work_items", "work_item_id", "work_item_id") in _foreign_keys(conn, "tasks")
    assert {("kinds", "session_id", "session_id"), ("kinds", "kind", "kind")} <= _foreign_keys(conn, "work_items")
    indexed = {
        (table, tuple(r["name"] for r in conn.execute(f"PRAGMA index_info({i['name']})")))
        for table in ("tasks", "work_items", "work_item_events")
        for i in conn.execute(f"PRAGMA index_list({table})")
    }
    assert {
        ("tasks", ("work_item_id", "created_at")), ("work_items", ("session_id", "status")),
        ("work_item_events", ("work_item_id", "id")), ("work_item_events", ("session_id", "occurred_at")),
    } <= indexed

    _cycle_base(conn)
    item = ("INSERT INTO work_items (work_item_id, session_id, key_number, title, request, kind, priority,"
            " assignee_type, assignee_id, status, status_reason, source_type, created_at, updated_at, closed_at)"
            " VALUES (?, 's1', ?, 't', 'r', 'bug_fix', ?, ?, ?, ?, '', ?, ?, ?, ?)")
    for n, status in enumerate(WORK_STATUSES, start=1):  # 업무 상태 8개는 모두 받는다
        closed = NOW if status in ("완료", "종료") else None
        conn.execute(item, (f"wi-{n}", n, "normal", None, None, status, "manual", NOW, NOW, closed))
    row = conn.execute("SELECT priority, form_json, revision FROM work_items WHERE work_item_id = 'wi-1'").fetchone()
    assert tuple(row) == ("normal", "{}", 1)
    for params in (
        ("wi-x", 1, "normal", None, None, "대기", "manual", NOW, NOW, None),  # 키 중복
        ("wi-x", 0, "normal", None, None, "대기", "manual", NOW, NOW, None),  # 키 >= 1
        ("wi-x", 20, "urgent", None, None, "대기", "manual", NOW, NOW, None),  # 우선순위 허용 값 밖
        ("wi-x", 20, "normal", "agent", None, "대기", "manual", NOW, NOW, None),  # 담당 종류·id 는 함께
        ("wi-x", 20, "normal", "team", "x", "대기", "manual", NOW, NOW, None),  # 담당 종류 허용 값 밖
        ("wi-x", 20, "normal", None, None, "실패", "manual", NOW, NOW, None),  # 업무 상태 허용 값 밖
        ("wi-x", 20, "normal", None, None, "완료", "manual", NOW, NOW, None),  # 끝 상태는 마감 시각이 있어야
        ("wi-x", 20, "normal", None, None, "대기", "manual", NOW, NOW, NOW),  # 열린 상태는 마감 시각이 없어야
        ("wi-x", 20, "normal", None, None, "대기", "jira", NOW, NOW, None),  # 원본 종류 허용 값 밖
    ):
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(item, params)
    with pytest.raises(sqlite3.IntegrityError):  # 등록되지 않은 종류
        conn.execute("INSERT INTO work_items (work_item_id, session_id, key_number, title, request, kind, status,"
                     " status_reason, source_type, created_at, updated_at) VALUES ('wi-y', 's1', 30, 't', 'r',"
                     " 'nope', '대기', '', 'manual', ?, ?)", (NOW, NOW))

    link = ("INSERT INTO work_item_links (from_work_item_id, to_work_item_id, type, cause_execution_id, created_at)"
            " VALUES (?, ?, ?, ?, ?)")
    conn.execute(link, ("wi-1", "wi-2", "blocks", None, NOW))
    conn.execute(link, ("wi-1", "wi-2", "spawned_from", "e1", NOW))
    for params in (
        ("wi-1", "wi-2", "blocks", None, NOW),  # 같은 링크 중복
        ("wi-1", "wi-1", "blocks", None, NOW),  # 자기 자신
        ("wi-1", "wi-3", "relates", None, NOW),  # 링크 종류 허용 값 밖
        ("wi-1", "wi-nope", "blocks", None, NOW),  # 없는 업무
        ("wi-1", "wi-3", "spawned_from", "e-nope", NOW),  # 없는 실행
    ):
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(link, params)

    member = "INSERT INTO members (member_id, session_id, display_name, role, created_at) VALUES (?, ?, 'n', ?, ?)"
    conn.execute(member, ("mem-1", "s1", "admin", NOW))
    conn.execute(member, ("mem-2", "s1", "member", NOW))
    for params in (("mem-3", "s1", "owner", NOW), ("mem-4", "s-nope", "member", NOW)):
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(member, params)

    mapping = ("INSERT INTO field_mappings (mapping_id, session_id, source_type, field, source_value, runloom_value,"
               " position, created_at) VALUES (?, 's1', ?, ?, ?, 'bug_fix', ?, ?)")
    conn.execute(mapping, ("map-1", "github", "kind", "*", 1, NOW))
    conn.execute(mapping, ("map-2", "github", "kind", "bug", 1, NOW))  # position 은 유일하지 않다
    for params in (
        ("map-3", "github", "kind", "*", 2, NOW),  # (세션, 원본, 필드, 원본 값) 중복
        ("map-4", "jira", "kind", "x", 1, NOW),  # 원본 종류 허용 값 밖
        ("map-5", "github", "assignee", "x", 1, NOW),  # 필드 허용 값 밖
        ("map-6", "github", "kind", "y", 0, NOW),  # position >= 1
    ):
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(mapping, params)

    event = ("INSERT INTO work_item_events (work_item_id, session_id, type, config_revision, occurred_at, data_json)"
             " VALUES (?, 's1', ?, 1, ?, '{}')")
    conn.execute(event, ("wi-1", "status_changed", NOW))
    conn.execute(event, ("wi-1", "assigned", NOW))
    for params in (("wi-1", "created", NOW), ("wi-nope", "assigned", NOW)):
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(event, params)
    with pytest.raises(sqlite3.IntegrityError):  # 없는 업무를 가리키는 단계
        conn.execute("UPDATE tasks SET work_item_id = 'wi-nope' WHERE task_id = 't1'")


def test_create_session_seeds_first_admin_and_default_mapping(conn):
    from workflow.adapters import repo

    repo.create_session(conn, "s1", NOW)
    members = [tuple(r) for r in conn.execute("SELECT session_id, display_name, role, created_at FROM members")]
    assert members == [("s1", "관리자", "admin", NOW)]
    assert conn.execute("SELECT member_id FROM members").fetchone()[0].startswith("mem-")
    mappings = [tuple(r) for r in conn.execute(
        "SELECT session_id, source_type, field, source_value, runloom_value, position FROM field_mappings")]
    assert mappings == [("s1", "github", "kind", "*", "bug_fix", 1)]
    assert conn.execute("SELECT mapping_id FROM field_mappings").fetchone()[0].startswith("map-")
    assert repo.get_config_revision(conn, "s1") == 1  # seed 는 설정 번호를 올리지 않는다
    assert repo.ensure_first_admin(conn, "s1", now=NOW) == conn.execute("SELECT member_id FROM members").fetchone()[0]
    assert conn.execute("SELECT COUNT(*) FROM members").fetchone()[0] == 1  # 있으면 그 관리자


def test_v9_fixture_is_the_phase13_schema(db_path):
    c = _v9_db(db_path)
    assert c.execute("SELECT version FROM schema_version").fetchone()[0] == 9
    assert _table_names(c) - {"sqlite_sequence"} == V9_TABLES | {"schema_version"}
    assert "work_item_id" not in _columns(c, "tasks")
    c.close()


def test_migrates_v9_to_v10_grouping_stages_into_work_items(db_path):
    c = _v9_db(db_path)
    columns = _column_lists(c, V9_TABLES)
    before = _dump(c, V9_TABLES)
    c.close()

    c = connect(db_path)
    init_schema(c)
    assert [tuple(r) for r in c.execute("SELECT version FROM schema_version")] == [(SCHEMA_VERSION,)]
    assert _dump(c, V9_TABLES, columns) == before  # 기존 행·열 값은 그대로
    assert c.execute("SELECT COUNT(*) FROM tasks WHERE work_item_id IS NULL").fetchone()[0] == 0
    assert c.execute("SELECT COUNT(*) FROM work_items").fetchone()[0] == 6

    # 검토·재검토 Task 는 원인 수정 Task 의 업무에 붙는다(사슬 끝까지)
    fix41, fix42 = _work_of(c, "t-fix-41"), _work_of(c, "t-fix-42")
    assert _work_of(c, "t-rev-41")["work_item_id"] == fix41["work_item_id"]
    assert {_work_of(c, t)["work_item_id"] for t in ("t-rev-42a", "t-rev-42b")} == {fix42["work_item_id"]}

    keys = {t: (_work_of(c, t)["session_id"], _work_of(c, t)["key_number"])
            for t in ("t-fix-41", "t-fix-42", "t-n1", "t-n2", "t-m1", "t-s2")}
    assert keys == {"t-fix-41": ("s1", 1), "t-fix-42": ("s1", 2), "t-n1": ("s1", 3), "t-n2": ("s1", 4),
                    "t-m1": ("s1", 5), "t-s2": ("s2", 1)}

    assert fix41["work_item_id"].startswith("wi-") and len(fix41["work_item_id"]) == 15
    assert (fix41["title"], fix41["request"], fix41["kind"], fix41["priority"]) == (
        "제목 t-fix-41", "요청 t-fix-41", "bug_fix", "normal")
    assert (fix41["assignee_type"], fix41["assignee_id"]) == ("agent", "a1")
    assert (fix41["source_type"], fix41["source_id"], fix41["source_item_id"], fix41["source_key"],
            fix41["source_url"], fix41["source_state"]) == (
        "github", "ghs-00000001", "1041", "acme/billing#41", "https://github.com/acme/billing/issues/41", "open")
    assert fix41["form_json"] == "{}" and fix41["revision"] == 1
    assert fix41["created_at"] == _t(1)
    n1, n2, m1 = _work_of(c, "t-n1"), _work_of(c, "t-n2"), _work_of(c, "t-m1")
    assert (n1["source_type"], n1["source_id"], n1["source_key"], n1["source_url"], n1["source_state"]) == (
        "n8n", "c1", "OPS-1", None, None)
    assert (n1["assignee_type"], n1["assignee_id"]) == (None, None)
    assert (m1["source_type"], m1["source_id"], m1["source_item_id"], m1["source_key"], m1["source_url"]) == (
        "manual", None, None, None, None)

    statuses = {t: (_work_of(c, t)["status"], _work_of(c, t)["status_reason"])
                for t in ("t-fix-41", "t-fix-42", "t-n1", "t-n2", "t-m1", "t-s2")}
    assert statuses == {
        "t-fix-41": ("PR · 검토", "PR 확인 — #12"),
        "t-fix-42": ("에이전트 작업 중", "커밋 검토 실행 중"),
        "t-n1": ("새로 들어옴", "담당 없음"),
        "t-n2": ("대기", "선행 대기"),
        "t-m1": ("종료", "실행 실패 — timeout"),
        "t-s2": ("새로 들어옴", "담당 없음"),
    }
    assert m1["closed_at"] is not None and fix41["closed_at"] is None
    assert c.execute("SELECT COUNT(*) FROM work_item_events").fetchone()[0] == 0  # 계산값만 넣고 이벤트는 없다

    links = [tuple(r) for r in c.execute(
        "SELECT from_work_item_id, to_work_item_id, type, cause_execution_id FROM work_item_links")]
    assert links == [(n1["work_item_id"], n2["work_item_id"], "blocks", None)]  # 같은 업무 안 선행은 링크가 아니다

    members = [tuple(r) for r in c.execute("SELECT session_id, display_name, role FROM members ORDER BY session_id")]
    assert members == [("s1", "관리자", "admin"), ("s2", "관리자", "admin")]
    mappings = [tuple(r) for r in c.execute(
        "SELECT session_id, source_type, field, source_value, runloom_value, position FROM field_mappings"
        " ORDER BY session_id")]
    assert mappings == [("s1", "github", "kind", "*", "bug_fix", 1), ("s2", "github", "kind", "*", "bug_fix", 1)]
    assert [r[0] for r in c.execute("SELECT DISTINCT config_revision FROM sessions")] == [1]
    assert c.execute("PRAGMA foreign_key_check").fetchall() == []
    assert c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"

    after = _dump(c, TABLES)
    init_schema(c)  # 재실행은 아무것도 바꾸지 않는다
    assert _dump(c, TABLES) == after
    c.close()


def test_v10_migration_rolls_back_when_a_task_is_left_without_work_item(db_path, monkeypatch):
    from workflow.adapters import db

    c = _v9_db(db_path)
    before = _dump(c, V9_TABLES)
    real = db._work_roots

    def drop_one(conn):
        roots = real(conn)
        roots.pop("t-m1")
        return roots

    monkeypatch.setattr(db, "_work_roots", drop_one)
    with pytest.raises(RuntimeError, match="work_item_id"):
        init_schema(c)
    assert c.execute("SELECT version FROM schema_version").fetchone()[0] == 9
    assert _table_names(c) - {"sqlite_sequence"} == V9_TABLES | {"schema_version"}
    assert "work_item_id" not in _columns(c, "tasks")
    assert _dump(c, V9_TABLES) == before
    assert not c.in_transaction
    monkeypatch.undo()
    init_schema(c)  # 원인이 사라지면 다시 돌릴 수 있다
    assert c.execute("SELECT version FROM schema_version").fetchone()[0] == SCHEMA_VERSION
    c.close()


def test_fresh_schema_matches_v9_migrated_schema(tmp_path):
    fresh = connect(tmp_path / "fresh.sqlite")
    init_schema(fresh)
    migrated = _v9_db(tmp_path / "old.sqlite")
    init_schema(migrated)
    assert _table_names(fresh) == _table_names(migrated)
    for table in TABLES:
        cols = "SELECT name, type, \"notnull\", dflt_value, pk FROM pragma_table_info(?) ORDER BY name"
        assert fresh.execute(cols, (table,)).fetchall() == migrated.execute(cols, (table,)).fetchall(), table
        assert _foreign_keys(fresh, table) == _foreign_keys(migrated, table), table
    assert _indexes(fresh) == _indexes(migrated)
    fresh.close()
    migrated.close()


# --- phase 15: v10 → v11 계정·로그인 세션·초대 칸 (ADR-0021) ------------------------------------------

V10_SCHEMA = (Path(__file__).parent / "fixtures" / "schema_v10.sql").read_text()
V11_COLUMNS = {
    "members": {"email", "password_hash", "disabled_at"},
    "work_items": {"requested_by_member_id"},
    "human_responses": {"member_id"},
    "notifications": {"recipient_member_id", "channel"},
    "connect_codes": {"issued_by_member_id"},
    "connectors": {"owner_member_id"},
}


def _v10_db(db_path):
    """phase 14 서버가 남긴 모양의 v10 DB. 워크스페이스 s1 — 첫 관리자·멤버, 업무 1건(단계 Task 1, 사람 요청·응답),
    알림 1건, 연결 코드·연결 프로그램 1개. 워크스페이스 s2 — 첫 관리자만."""
    c = connect(db_path)
    c.executescript(V10_SCHEMA)
    c.execute("INSERT INTO schema_version (version) VALUES (10)")
    for sid in ("s1", "s2"):
        c.execute("INSERT INTO sessions (session_id, created_at, is_operator) VALUES (?, ?, 1)", (sid, NOW))
        for spec in BUILTIN_KINDS:
            c.execute("INSERT INTO kinds (session_id, kind, spec_json, created_at) VALUES (?, ?, ?, ?)",
                      (sid, spec.kind, spec.model_dump_json(), NOW))
        c.execute("INSERT INTO members (member_id, session_id, display_name, role, created_at)"
                  " VALUES (?, ?, '관리자', 'admin', ?)", (f"mem-0000000{sid[-1]}", sid, NOW))
    c.execute("INSERT INTO members (member_id, session_id, display_name, role, created_at)"
              " VALUES ('mem-0000000a', 's1', '김OO', 'member', ?)", (NOW,))
    c.execute("INSERT INTO work_items (work_item_id, session_id, key_number, title, request, kind, assignee_type,"
              " assignee_id, status, status_reason, source_type, created_at, updated_at) VALUES ('wi-000000000001',"
              " 's1', 1, '쿠폰 오류', '고쳐 주세요', 'bug_fix', 'member', 'mem-0000000a', '내 차례', '검토 승인',"
              " 'manual', ?, ?)", (NOW, NOW))
    _insert_task(c, "t1", "s1", "bug_fix")
    c.execute("UPDATE tasks SET work_item_id = 'wi-000000000001' WHERE task_id = 't1'")
    c.execute("INSERT INTO human_requests (request_id, task_id, code, question, cause_key, task_revision, revision,"
              " state, created_at, answered_at) VALUES ('hr-00000001', 't1', 'approve', '승인?', 'k', 1, 2,"
              " 'answered', ?, ?)", (NOW, NOW))
    c.execute("INSERT INTO human_responses (request_id, response_id, action, text, expected_revision, task_revision,"
              " created_at) VALUES ('hr-00000001', 'rsp-1', 'approve', '좋아요', 1, 2, ?)", (NOW,))
    c.execute("INSERT INTO notifications (notification_id, session_id, event, task_id, dedupe_key, content,"
              " payload_json, state, created_at) VALUES ('ntf-00000001', 's1', 'human_request', 't1',"
              " 'human_request:hr-00000001', '[Runloom] 사람 차례', '{}', 'sent', ?)", (NOW,))
    c.execute("INSERT INTO connect_codes (code, issued_at, expires_at, used_at) VALUES ('code-1', ?, ?, ?)",
              (NOW, NOW, NOW))
    c.execute("INSERT INTO connectors (connector_id, token_sha256, created_at) VALUES ('conn-1', 'h', ?)", (NOW,))
    return c


def _v11_base(conn) -> None:
    """새 칸·표 제약 확인용 최소 행 — 두 워크스페이스, 멤버 둘."""
    _cycle_base(conn)
    conn.execute("INSERT INTO sessions (session_id, created_at) VALUES ('s2', ?)", (NOW,))
    member = "INSERT INTO members (member_id, session_id, display_name, role, created_at) VALUES (?, ?, 'n', ?, ?)"
    conn.execute(member, ("mem-00000001", "s1", "admin", NOW))
    conn.execute(member, ("mem-00000002", "s1", "member", NOW))


def test_v11_constants_come_from_domain_and_contract():
    from workflow.adapters import db

    assert db.NOTIFICATION_CHANNELS == ("shared", "personal")
    assert db.INVITE_PURPOSES == ("invite", "reset")


def test_fresh_db_has_v11_tables_columns_and_indexes(conn):
    assert conn.execute("SELECT version FROM schema_version").fetchone()[0] == SCHEMA_VERSION
    assert PHASE15_TABLES <= _table_names(conn)
    for table, columns in V11_COLUMNS.items():
        assert columns <= _columns(conn, table), table
    assert _columns(conn, "login_sessions") == {
        "login_id", "session_id", "member_id", "token_sha256", "created_at", "expires_at", "last_seen_at", "revoked_at",
    }
    assert _columns(conn, "member_invites") == {
        "invite_id", "session_id", "purpose", "role", "member_id", "token_sha256", "created_by_member_id", "created_at",
        "expires_at", "used_at", "used_by_member_id", "revoked_at",
    }
    assert {("sessions", "session_id", "session_id"), ("members", "member_id", "member_id")} <= _foreign_keys(
        conn, "login_sessions")
    assert {("sessions", "session_id", "session_id"), ("members", "member_id", "member_id"),
            ("members", "created_by_member_id", "member_id"), ("members", "used_by_member_id", "member_id")} <= (
        _foreign_keys(conn, "member_invites"))
    assert ("members", "requested_by_member_id", "member_id") in _foreign_keys(conn, "work_items")
    assert ("members", "member_id", "member_id") in _foreign_keys(conn, "human_responses")
    assert ("members", "recipient_member_id", "member_id") in _foreign_keys(conn, "notifications")
    assert ("members", "issued_by_member_id", "member_id") in _foreign_keys(conn, "connect_codes")
    assert ("members", "owner_member_id", "member_id") in _foreign_keys(conn, "connectors")
    assert "password" not in " ".join(_columns(conn, "login_sessions") | _columns(conn, "member_invites"))

    unique = conn.execute("SELECT sql FROM sqlite_master WHERE name = 'members_session_email'").fetchone()[0]
    assert "UNIQUE" in unique and "WHERE email IS NOT NULL" in unique
    indexed = {
        (table, tuple(r["name"] for r in conn.execute(f"PRAGMA index_info({i['name']})")))
        for table in ("members", "login_sessions", "notifications")
        for i in conn.execute(f"PRAGMA index_list({table})")
    }
    assert {("members", ("session_id", "email")), ("login_sessions", ("member_id",)),
            ("notifications", ("recipient_member_id",))} <= indexed


def test_v11_member_email_is_unique_per_workspace_only_when_set(conn):
    _v11_base(conn)
    conn.execute("UPDATE members SET email = 'a@example.com', password_hash = 'h' WHERE member_id = 'mem-00000001'")
    with pytest.raises(sqlite3.IntegrityError):  # 같은 워크스페이스 같은 이메일
        conn.execute("UPDATE members SET email = 'a@example.com' WHERE member_id = 'mem-00000002'")
    member = ("INSERT INTO members (member_id, session_id, display_name, role, created_at, email)"
              " VALUES (?, ?, 'n', 'member', ?, ?)")
    conn.execute(member, ("mem-00000003", "s1", NOW, None))
    conn.execute(member, ("mem-00000004", "s1", NOW, None))  # 이메일 없는 멤버는 여럿
    conn.execute(member, ("mem-00000005", "s2", NOW, "a@example.com"))  # 다른 워크스페이스는 같은 이메일 허용
    assert conn.execute("SELECT COUNT(*) FROM members WHERE email IS NULL").fetchone()[0] == 3
    with pytest.raises(sqlite3.IntegrityError):  # 역할 CHECK 는 그대로
        conn.execute("UPDATE members SET role = 'owner' WHERE member_id = 'mem-00000003'")


def test_v11_login_sessions_checks(conn):
    _v11_base(conn)
    insert = ("INSERT INTO login_sessions (login_id, session_id, member_id, token_sha256, created_at, expires_at,"
              " last_seen_at) VALUES (?, ?, ?, ?, ?, ?, ?)")
    conn.execute(insert, ("lgn-000000000001", "s1", "mem-00000001", "sha-1", NOW, NOW, NOW))
    row = conn.execute("SELECT revoked_at FROM login_sessions").fetchone()
    assert row[0] is None
    for params in (
        ("lgn-000000000001", "s1", "mem-00000001", "sha-2", NOW, NOW, NOW),  # id 중복
        ("lgn-000000000002", "s1", "mem-00000001", "sha-1", NOW, NOW, NOW),  # 토큰 해시 중복
        ("lgn-000000000003", "s1", "mem-nope", "sha-3", NOW, NOW, NOW),  # 없는 멤버
        ("lgn-000000000004", "s-nope", "mem-00000001", "sha-4", NOW, NOW, NOW),  # 없는 워크스페이스
        ("lgn-000000000005", "s1", "mem-00000001", None, NOW, NOW, NOW),  # 토큰 해시 필수
        ("lgn-000000000006", "s1", "mem-00000001", "sha-6", NOW, None, NOW),  # 만료 시각 필수
    ):
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(insert, params)


def test_v11_member_invites_checks(conn):
    _v11_base(conn)
    insert = ("INSERT INTO member_invites (invite_id, session_id, purpose, role, member_id, token_sha256,"
              " created_by_member_id, created_at, expires_at, used_at, used_by_member_id)"
              " VALUES (?, 's1', ?, ?, ?, ?, 'mem-00000001', ?, ?, ?, ?)")
    conn.execute(insert, ("inv-1", "invite", "member", None, "sha-1", NOW, NOW, None, None))
    conn.execute(insert, ("inv-2", "invite", "admin", None, "sha-2", NOW, NOW, NOW, "mem-00000002"))
    conn.execute(insert, ("inv-3", "reset", None, "mem-00000002", "sha-3", NOW, NOW, None, None))
    for params in (
        ("inv-4", "login", "member", None, "sha-4", NOW, NOW, None, None),  # 목적 허용 값 밖
        ("inv-5", "invite", "owner", None, "sha-5", NOW, NOW, None, None),  # 역할 허용 값 밖
        ("inv-6", "invite", None, None, "sha-6", NOW, NOW, None, None),  # 초대는 역할이 있어야
        ("inv-7", "invite", "member", "mem-00000002", "sha-7", NOW, NOW, None, None),  # 초대는 멤버가 없어야
        ("inv-8", "reset", None, None, "sha-8", NOW, NOW, None, None),  # 재설정은 멤버가 있어야
        ("inv-9", "reset", "member", "mem-00000002", "sha-9", NOW, NOW, None, None),  # 재설정은 역할이 없어야
        ("inv-10", "invite", "member", None, "sha-1", NOW, NOW, None, None),  # 토큰 해시 중복
        ("inv-11", "invite", "member", None, "sha-11", NOW, NOW, NOW, None),  # 사용 시각·사용자는 함께
        ("inv-12", "invite", "member", None, "sha-12", NOW, NOW, None, "mem-00000002"),  # 사용 시각·사용자는 함께
        ("inv-13", "reset", None, "mem-nope", "sha-13", NOW, NOW, None, None),  # 없는 멤버
    ):
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(insert, params)


def test_v11_notification_channel_and_member_references(conn):
    _v11_base(conn)
    insert = ("INSERT INTO notifications (notification_id, session_id, event, task_id, dedupe_key, content,"
              " payload_json, state, created_at, channel, recipient_member_id)"
              " VALUES (?, 's1', 'human_request', 't1', ?, 'c', '{}', 'pending', ?, ?, ?)")
    conn.execute("INSERT INTO notifications (notification_id, session_id, event, task_id, dedupe_key, content,"
                 " payload_json, state, created_at) VALUES ('ntf-1', 's1', 'human_request', 't1', 'k1', 'c', '{}',"
                 " 'pending', ?)", (NOW,))
    assert tuple(conn.execute("SELECT channel, recipient_member_id FROM notifications").fetchone()) == ("shared", None)
    conn.execute(insert, ("ntf-2", "k2", NOW, "personal", "mem-00000002"))
    for params in (
        ("ntf-3", "k3", NOW, "email", None),  # 경로 허용 값 밖
        ("ntf-4", "k4", NOW, None, None),  # 경로 필수
        ("ntf-5", "k5", NOW, "shared", "mem-nope"),  # 없는 멤버
    ):
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(insert, params)

    conn.execute("INSERT INTO connect_codes (code, issued_at, expires_at, issued_by_member_id) VALUES"
                 " ('code-1', ?, ?, 'mem-00000001')", (NOW, NOW))
    conn.execute("INSERT INTO connectors (connector_id, token_sha256, created_at, owner_member_id) VALUES"
                 " ('conn-1', 'h', ?, 'mem-00000001')", (NOW,))
    conn.execute("INSERT INTO work_items (work_item_id, session_id, key_number, title, request, kind, status,"
                 " status_reason, source_type, created_at, updated_at, requested_by_member_id) VALUES ('wi-1', 's1', 1,"
                 " 't', 'r', 'bug_fix', '대기', '', 'manual', ?, ?, 'mem-00000002')", (NOW, NOW))
    for sql in (
        "UPDATE work_items SET requested_by_member_id = 'mem-nope'",
        "UPDATE connect_codes SET issued_by_member_id = 'mem-nope'",
        "UPDATE connectors SET owner_member_id = 'mem-nope'",
    ):
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(sql)


def test_v10_fixture_is_the_phase14_schema(db_path):
    c = _v10_db(db_path)
    assert c.execute("SELECT version FROM schema_version").fetchone()[0] == 10
    assert _table_names(c) - {"sqlite_sequence"} == V10_TABLES | {"schema_version"}
    for table, columns in V11_COLUMNS.items():
        assert not columns & _columns(c, table), table
    c.close()


def test_migrates_v10_to_v11_preserving_rows(db_path):
    c = _v10_db(db_path)
    columns = _column_lists(c, V10_TABLES)
    before = _dump(c, V10_TABLES)
    c.close()

    c = connect(db_path)
    init_schema(c)
    assert [tuple(r) for r in c.execute("SELECT version FROM schema_version")] == [(SCHEMA_VERSION,)]
    assert _dump(c, V10_TABLES, columns) == before  # 기존 행 수·열 값은 그대로
    for table, new in V11_COLUMNS.items():
        values = {tuple(r) for r in c.execute(f"SELECT {', '.join(sorted(new - {'channel'}))} FROM {table}")}
        assert values <= {(None,) * len(new - {"channel"})}, table  # 새 칸은 NULL
    assert [r[0] for r in c.execute("SELECT channel FROM notifications")] == ["shared"]
    assert c.execute("SELECT COUNT(*) FROM login_sessions").fetchone()[0] == 0
    assert c.execute("SELECT COUNT(*) FROM member_invites").fetchone()[0] == 0
    assert c.execute("PRAGMA foreign_key_check").fetchall() == []
    assert c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"

    after = _dump(c, TABLES)
    init_schema(c)  # 재실행은 아무것도 바꾸지 않는다
    assert _dump(c, TABLES) == after
    c.close()


def test_v11_migration_rolls_back_on_foreign_key_violation(db_path):
    c = _v10_db(db_path)
    c.execute("PRAGMA foreign_keys=OFF")
    c.execute("UPDATE tasks SET work_item_id = 'wi-nope' WHERE task_id = 't1'")  # 외래키 검사가 잡을 옛 결함
    c.execute("PRAGMA foreign_keys=ON")
    before = _dump(c, V10_TABLES)
    with pytest.raises(RuntimeError, match="외래키"):
        init_schema(c)
    assert c.execute("SELECT version FROM schema_version").fetchone()[0] == 10
    assert _table_names(c) - {"sqlite_sequence"} == V10_TABLES | {"schema_version"}
    for table, columns in V11_COLUMNS.items():
        assert not columns & _columns(c, table), table
    assert c.execute("SELECT name FROM sqlite_master WHERE name = 'members_session_email'").fetchone() is None
    assert _dump(c, V10_TABLES) == before
    assert not c.in_transaction
    c.execute("UPDATE tasks SET work_item_id = 'wi-000000000001' WHERE task_id = 't1'")
    init_schema(c)  # 원인이 사라지면 다시 돌릴 수 있다
    assert c.execute("SELECT version FROM schema_version").fetchone()[0] == SCHEMA_VERSION
    c.close()


def test_fresh_schema_matches_v10_migrated_schema(tmp_path):
    fresh = connect(tmp_path / "fresh.sqlite")
    init_schema(fresh)
    migrated = _v10_db(tmp_path / "old.sqlite")
    init_schema(migrated)
    assert _table_names(fresh) == _table_names(migrated)
    for table in TABLES:
        cols = "SELECT name, type, \"notnull\", dflt_value, pk FROM pragma_table_info(?) ORDER BY cid"
        assert fresh.execute(cols, (table,)).fetchall() == migrated.execute(cols, (table,)).fetchall(), table
        assert _foreign_keys(fresh, table) == _foreign_keys(migrated, table), table
    assert _indexes(fresh) == _indexes(migrated)
    fresh.close()
    migrated.close()


@pytest.mark.parametrize("make", [_v4_db, _v5_db, _v6_db, _v7_db, _v8_db, _v9_db])
def test_migrates_v4_to_v9_all_the_way_to_v11(db_path, make):
    c = make(db_path)
    c.close()
    c = connect(db_path)
    init_schema(c)
    assert c.execute("SELECT version FROM schema_version").fetchone()[0] == 11
    assert TABLES <= _table_names(c)
    for table, columns in V11_COLUMNS.items():
        assert columns <= _columns(c, table), table
    assert c.execute("SELECT COUNT(*) FROM notifications WHERE channel != 'shared'").fetchone()[0] == 0
    assert c.execute("PRAGMA foreign_key_check").fetchall() == []
    c.close()
