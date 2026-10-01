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
    "work_pull_requests",
    "jira_connections",
    "jira_projects",
    "jira_issues",
    "jira_deliveries",
    "triage_criteria",
    "triage_logs",
    "triage_autostart",
    "config_changes",
}
PHASE9_TABLES = {"task_events", "baseline_items", "baseline_imports"}
PHASE12_TABLES = {"task_pull_requests", "notifications"}
PHASE14_TABLES = {"work_items", "work_item_links", "members", "field_mappings", "work_item_events"}
PHASE15_TABLES = {"login_sessions", "member_invites"}
PHASE16_TABLES = {"work_pull_requests"}
PHASE18_TABLES = {"jira_connections", "jira_projects", "jira_issues", "jira_deliveries"}
PHASE19_TABLES = {"triage_criteria", "triage_logs", "triage_autostart"}
PHASE20_TABLES = {"config_changes"}
V15_TABLES = TABLES - PHASE20_TABLES
V14_TABLES = V15_TABLES - PHASE19_TABLES
V13_TABLES = V14_TABLES - PHASE18_TABLES  # v12 도 같은 표 집합(v13 은 칸·CHECK 만 바꿨다)
V11_TABLES = V13_TABLES - PHASE16_TABLES
V10_TABLES = V11_TABLES - PHASE15_TABLES
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
# v14 까지의 DB 에 있던 내장 종류 — 판단(`triage`)은 v15 가 넣는다(ADR-0025)
PHASE8_BUILTIN_KINDS = tuple(spec for spec in BUILTIN_KINDS if spec.kind in ("bug_fix", "code_review"))
V8_BUILTIN_KINDS = (*LEGACY_KINDS, *PHASE8_BUILTIN_KINDS)


def _without_legacy(dump: dict[str, list[tuple]]) -> dict[str, list[tuple]]:
    """v9 가 지우는 행(진단·코드 수정 종류와 그 둘의 규칙)을 뺀 덤프 — 나머지 행은 그대로여야 한다."""
    return {
        table: [row for row in rows if not (table in ("kinds", "succession_rules") and set(row) & set(LEGACY_KIND_NAMES))]
        for table, rows in dump.items()
    }


def _without_v15_seeds(dump: dict[str, list[tuple]]) -> dict[str, list[tuple]]:
    """v15 가 넣는 내장 triage 종류 행과 Agent 의 code.triage 능력을 뺀 덤프 — 나머지 행·칸은 그대로여야 한다."""
    def agent_row(row: tuple) -> tuple:
        out = []
        for value in row:
            if isinstance(value, str) and value.startswith("[") and '"code.triage"' in value:
                value = json.dumps([cap for cap in json.loads(value) if cap["code"] != "code.triage"])
            out.append(value)
        return tuple(out)

    return {
        table: [agent_row(row) for row in rows] if table == "agents"
        else [row for row in rows if not (table == "kinds" and "triage" in row)]
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


def test_schema_version_is_16():
    assert SCHEMA_VERSION == 16


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
    assert _without_v15_seeds(_dump(c, V5_TABLES, columns)) == _without_legacy(before)  # 기존 행·열 값은 하나도 바뀌지 않는다
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
    assert _without_v15_seeds(_dump(c, v6_tables, columns)) == _without_legacy(before)  # 기존 행·열 값은 그대로
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
    assert _without_v15_seeds(_dump(c, v7_tables, columns)) == _without_legacy(before)  # 기존 행·열 값은 그대로
    pushed = dict(c.execute("SELECT execution_id, branch_pushed FROM executions").fetchall())
    assert pushed == {"e1": None, "e2": 1}  # 이벤트에 남은 보고만 옮긴다 — 없으면 모름
    for table in PHASE12_TABLES:
        assert c.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
    assert c.execute("PRAGMA foreign_key_check").fetchall() == []
    after = _dump(c, TABLES)
    init_schema(c)
    assert _dump(c, TABLES) == after
    c.close()


def test_migrates_v4_all_the_way_to_v16(db_path):
    c = _v4_db(db_path)
    c.close()
    c = connect(db_path)
    init_schema(c)
    assert c.execute("SELECT version FROM schema_version").fetchone()[0] == 16
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


def test_fresh_db_seeds_three_builtin_kinds_and_one_rule(conn):
    from workflow.adapters import repo

    assert conn.execute("SELECT version FROM schema_version").fetchone()[0] == SCHEMA_VERSION
    repo.create_session(conn, "s1", NOW)
    kinds = [r["kind"] for r in conn.execute("SELECT kind FROM kinds WHERE session_id = 's1' ORDER BY kind")]
    assert kinds == ["bug_fix", "code_review", "triage"]
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
    assert _without_v15_seeds(_dump(c, V9_TABLES, columns)) == _without_legacy(before)  # 두 종류·그 규칙만 사라지고 나머지는 그대로
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
        for spec in PHASE8_BUILTIN_KINDS:
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
    assert _columns(conn, "work_items") == (WORK_ITEM_COLUMNS | {"requested_by_member_id"} | V12_COLUMNS["work_items"]
                                             | V13_COLUMNS["work_items"])  # v11·v12·v13
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
        ("wi-x", 20, "normal", None, None, "대기", "linear", NOW, NOW, None),  # 원본 종류 허용 값 밖
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
        ("map-4", "linear", "kind", "x", 1, NOW),  # 원본 종류 허용 값 밖
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
    assert mappings == [("s1", "github", "kind", "*", "bug_fix", 1), ("s1", "jira", "kind", "*", "bug_fix", 2)]
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
    assert _without_v15_seeds(_dump(c, V9_TABLES, columns)) == before  # 기존 행·열 값은 그대로
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
        " ORDER BY session_id, source_type")]
    assert mappings == [("s1", "github", "kind", "*", "bug_fix", 1), ("s1", "jira", "kind", "*", "bug_fix", 1),
                        ("s2", "github", "kind", "*", "bug_fix", 1), ("s2", "jira", "kind", "*", "bug_fix", 1)]
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
        for spec in PHASE8_BUILTIN_KINDS:
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
    assert _without_v15_seeds(_without_jira_mappings(_dump(c, V10_TABLES, columns))) == before  # 기존 행 수·열 값은 그대로
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


@pytest.mark.parametrize("make", [_v4_db, _v5_db, _v6_db, _v7_db, _v8_db, _v9_db, _v10_db])
def test_migrates_v4_to_v10_all_the_way_to_v16(db_path, make):
    c = make(db_path)
    c.close()
    c = connect(db_path)
    init_schema(c)
    assert c.execute("SELECT version FROM schema_version").fetchone()[0] == 16
    assert TABLES <= _table_names(c)
    for table, columns in V11_COLUMNS.items():
        assert columns <= _columns(c, table), table
    for table, columns in V12_COLUMNS.items():
        assert columns <= _columns(c, table), table
    for table, columns in V13_COLUMNS.items():
        assert columns <= _columns(c, table), table
    assert c.execute("SELECT COUNT(*) FROM notifications WHERE channel != 'shared'").fetchone()[0] == 0
    assert c.execute("SELECT COUNT(*) FROM work_items WHERE direct_member_id IS NOT NULL").fetchone()[0] == 0
    assert c.execute("PRAGMA foreign_key_check").fetchall() == []
    c.close()


# --- phase 16: v11 → v12 직접 작업 칸·업무 PR 표·PR 커서·이벤트 종류 (ADR-0022) -----------------------------

V11_SCHEMA = (Path(__file__).parent / "fixtures" / "schema_v11.sql").read_text()
V12_COLUMNS = {
    "work_items": {"direct_member_id", "direct_started_at", "direct_branch"},
    "github_sources": {"pull_cursor"},
}
WORK_PULL_REQUEST_COLUMNS = [
    "id", "session_id", "work_item_id", "source_id", "repository_full_name", "pr_number", "title", "pr_url",
    "head_branch", "state", "draft", "author_login", "merged_at", "pr_updated_at", "created_at", "updated_at",
]
_WORK_EVENT_INSERT = ("INSERT INTO work_item_events (work_item_id, session_id, type, config_revision, occurred_at,"
                      " data_json) VALUES ('wi-000000000001', 's1', ?, 1, ?, '{}')")


def _v11_db(db_path):
    """phase 15 서버가 남긴 모양의 v11 DB. 워크스페이스 s1 — 관리자(계정 있음)·멤버, GitHub 소스 1개, 업무 1건
    (단계 Task 1), 업무 이벤트 2건(id 5·9 — 빈 번호가 있어도 id 가 그대로인지 본다)."""
    c = connect(db_path)
    c.executescript(V11_SCHEMA)
    c.execute("INSERT INTO schema_version (version) VALUES (11)")
    c.execute("INSERT INTO sessions (session_id, created_at, is_operator) VALUES ('s1', ?, 1)", (NOW,))
    for spec in PHASE8_BUILTIN_KINDS:
        c.execute("INSERT INTO kinds (session_id, kind, spec_json, created_at) VALUES ('s1', ?, ?, ?)",
                  (spec.kind, spec.model_dump_json(), NOW))
    c.execute("INSERT INTO members (member_id, session_id, display_name, role, created_at, email, password_hash)"
              " VALUES ('mem-00000001', 's1', '관리자', 'admin', ?, 'a@example.com', 'scrypt$h')", (NOW,))
    c.execute("INSERT INTO members (member_id, session_id, display_name, role, created_at)"
              " VALUES ('mem-0000000a', 's1', '김OO', 'member', ?)", (NOW,))
    c.execute("INSERT INTO github_sources (source_id, session_id, repository_full_name, config_json, cursor,"
              " created_at, updated_at) VALUES ('ghs-00000001', 's1', 'acme/billing', '{}', '2026-09-29T00:00:00Z',"
              " ?, ?)", (NOW, NOW))
    c.execute("INSERT INTO work_items (work_item_id, session_id, key_number, title, request, kind, assignee_type,"
              " assignee_id, status, status_reason, source_type, created_at, updated_at, requested_by_member_id)"
              " VALUES ('wi-000000000001', 's1', 1, '쿠폰 오류', '고쳐 주세요', 'bug_fix', 'member', 'mem-0000000a',"
              " '내 차례', '검토 승인', 'manual', ?, ?, 'mem-00000001')", (NOW, NOW))
    _insert_task(c, "t1", "s1", "bug_fix")
    c.execute("UPDATE tasks SET work_item_id = 'wi-000000000001' WHERE task_id = 't1'")
    event = ("INSERT INTO work_item_events (id, work_item_id, session_id, type, config_revision, occurred_at,"
             " data_json) VALUES (?, 'wi-000000000001', 's1', ?, 1, ?, ?)")
    c.execute(event, (5, "status_changed", NOW, '{"from": "대기", "to": "내 차례", "reason": "검토 승인"}'))
    c.execute(event, (9, "assigned", NOW, '{"from": null, "to": {"type": "member", "id": "mem-0000000a"}}'))
    return c


def _v12_base(conn) -> None:
    """새 칸·표 제약 확인용 최소 행 — 멤버 둘, GitHub 소스, 업무 하나."""
    _v11_base(conn)
    conn.execute("INSERT INTO work_items (work_item_id, session_id, key_number, title, request, kind, status,"
                 " status_reason, source_type, created_at, updated_at) VALUES ('wi-000000000001', 's1', 1, 't', 'r',"
                 " 'bug_fix', '대기', '', 'manual', ?, ?)", (NOW, NOW))


def _referencing(conn, table: str) -> list[str]:
    """`table` 을 외래키로 참조하는 표 이름."""
    return [
        name for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
        if any(r["table"] == table for r in conn.execute(f"PRAGMA foreign_key_list({name})"))
    ]


def test_v12_constants():
    from workflow.adapters import db

    assert db._V12_WORK_ITEM_EVENT_TYPES == (
        "status_changed", "assigned", "priority_changed", "direct_started", "direct_stopped", "pull_request_linked",
    )
    assert db.WORK_PULL_REQUEST_STATES == ("open", "merged", "closed")


def test_fresh_db_has_v12_columns_table_and_indexes(conn):
    assert conn.execute("SELECT version FROM schema_version").fetchone()[0] == SCHEMA_VERSION
    for table, columns in V12_COLUMNS.items():
        assert columns <= _columns(conn, table), table
    assert ("members", "direct_member_id", "member_id") in _foreign_keys(conn, "work_items")
    assert _column_lists(conn, ["work_pull_requests"])["work_pull_requests"] == WORK_PULL_REQUEST_COLUMNS
    assert _foreign_keys(conn, "work_pull_requests") == {
        ("sessions", "session_id", "session_id"), ("work_items", "work_item_id", "work_item_id"),
        ("github_sources", "source_id", "source_id"),
    }
    indexed = {
        (i["unique"], tuple(r["name"] for r in conn.execute(f"PRAGMA index_info({i['name']})")))
        for i in conn.execute("PRAGMA index_list(work_pull_requests)")
    }
    assert {(1, ("session_id", "repository_full_name", "pr_number")), (0, ("work_item_id", "pr_updated_at"))} <= indexed
    event_indexes = {r["name"] for r in conn.execute("PRAGMA index_list(work_item_events)")}
    assert {"ix_work_item_events_item", "ix_work_item_events_session"} <= event_indexes
    assert _referencing(conn, "work_item_events") == []  # 재생성해도 끊길 외래키가 없다


def test_v12_work_pull_request_checks(conn):
    _v12_base(conn)
    insert = ("INSERT INTO work_pull_requests (session_id, work_item_id, source_id, repository_full_name, pr_number,"
              " title, pr_url, head_branch, state, draft, author_login, merged_at, pr_updated_at, created_at,"
              " updated_at) VALUES ('s1', ?, ?, 'acme/billing', ?, 'RUN-1 고침', 'https://github.com/acme/billing/pull/7',"
              " 'run-1-fix', ?, ?, 'kim', ?, ?, ?, ?)")
    conn.execute(insert, ("wi-000000000001", "ghs-00000001", 7, "open", 1, None, NOW, NOW, NOW))
    conn.execute(insert, ("wi-000000000001", "ghs-00000001", 8, "merged", 0, NOW, NOW, NOW, NOW))
    conn.execute(insert, ("wi-000000000001", "ghs-00000001", 9, "closed", 0, None, NOW, NOW, NOW))
    for params in (
        ("wi-000000000001", "ghs-00000001", 10, "draft", 0, None, NOW, NOW, NOW),  # 상태 허용 값 밖
        ("wi-000000000001", "ghs-00000001", 11, "merged", 0, None, NOW, NOW, NOW),  # 병합이면 병합 시각이 있어야
        ("wi-000000000001", "ghs-00000001", 12, "open", 0, NOW, NOW, NOW, NOW),  # 병합이 아니면 병합 시각이 없어야
        ("wi-000000000001", "ghs-00000001", 13, "open", 2, None, NOW, NOW, NOW),  # draft 는 0·1
        ("wi-000000000001", "ghs-00000001", 0, "open", 0, None, NOW, NOW, NOW),  # PR 번호는 1 이상
        ("wi-000000000001", "ghs-00000001", 7, "closed", 0, None, NOW, NOW, NOW),  # 같은 저장소 같은 PR 번호
        ("wi-nope", "ghs-00000001", 14, "open", 0, None, NOW, NOW, NOW),  # 없는 업무
        ("wi-000000000001", "ghs-nope", 15, "open", 0, None, NOW, NOW, NOW),  # 없는 소스
        ("wi-000000000001", "ghs-00000001", 16, "open", 0, None, None, NOW, NOW),  # PR 갱신 시각 필수
    ):
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(insert, params)
    assert conn.execute("SELECT COUNT(*) FROM work_pull_requests").fetchone()[0] == 3


def test_v12_work_item_direct_columns_and_event_types(conn):
    _v12_base(conn)
    conn.execute("UPDATE work_items SET direct_member_id = 'mem-00000002', direct_started_at = ?,"
                 " direct_branch = 'run-1-fix'", (NOW,))
    with pytest.raises(sqlite3.IntegrityError):  # 없는 멤버
        conn.execute("UPDATE work_items SET direct_member_id = 'mem-nope'")
    conn.execute("UPDATE github_sources SET pull_cursor = ?", (NOW,))
    from workflow.adapters.db import WORK_ITEM_EVENT_TYPES

    for event_type in WORK_ITEM_EVENT_TYPES:
        conn.execute(_WORK_EVENT_INSERT, (event_type, NOW))
    with pytest.raises(sqlite3.IntegrityError):  # 모르는 종류
        conn.execute(_WORK_EVENT_INSERT, ("commented", NOW))


def test_v11_fixture_is_the_phase15_schema(db_path):
    c = _v11_db(db_path)
    assert c.execute("SELECT version FROM schema_version").fetchone()[0] == 11
    assert _table_names(c) - {"sqlite_sequence"} == V11_TABLES | {"schema_version"}
    for table, columns in V12_COLUMNS.items():
        assert not columns & _columns(c, table), table
    with pytest.raises(sqlite3.IntegrityError):  # v11 은 새 이벤트 종류를 모른다
        c.execute(_WORK_EVENT_INSERT, ("priority_changed", NOW))
    c.close()


def test_migrates_v11_to_v12_preserving_rows_and_event_ids(db_path):
    c = _v11_db(db_path)
    columns = _column_lists(c, V11_TABLES)
    before = _dump(c, V11_TABLES)
    c.close()

    c = connect(db_path)
    init_schema(c)
    assert [tuple(r) for r in c.execute("SELECT version FROM schema_version")] == [(SCHEMA_VERSION,)]
    assert _without_v15_seeds(_without_jira_mappings(_dump(c, V11_TABLES, columns))) == before  # 기존 행 수·열 값은 그대로
    assert [r[0] for r in c.execute("SELECT id FROM work_item_events ORDER BY id")] == [5, 9]
    for table, new in V12_COLUMNS.items():
        values = {tuple(r) for r in c.execute(f"SELECT {', '.join(sorted(new))} FROM {table}")}
        assert values == {(None,) * len(new)}, table  # 새 칸은 NULL
    assert c.execute("SELECT cursor FROM github_sources").fetchone()[0] == "2026-09-29T00:00:00Z"  # 이슈 커서 그대로
    assert c.execute("SELECT COUNT(*) FROM work_pull_requests").fetchone()[0] == 0
    event_indexes = {r["name"] for r in c.execute("PRAGMA index_list(work_item_events)")}
    assert {"ix_work_item_events_item", "ix_work_item_events_session"} <= event_indexes
    assert _referencing(c, "work_item_events") == []
    c.execute(_WORK_EVENT_INSERT, ("pull_request_linked", NOW))  # 새 종류 허용
    assert c.execute("SELECT MAX(id) FROM work_item_events").fetchone()[0] == 10  # id 는 이어서
    with pytest.raises(sqlite3.IntegrityError):
        c.execute(_WORK_EVENT_INSERT, ("commented", NOW))
    assert c.execute("PRAGMA foreign_key_check").fetchall() == []
    assert c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"

    after = _dump(c, TABLES)
    init_schema(c)  # 재실행은 아무것도 바꾸지 않는다
    assert _dump(c, TABLES) == after
    c.close()


def test_v12_migration_rolls_back_on_foreign_key_violation(db_path):
    c = _v11_db(db_path)
    c.execute("PRAGMA foreign_keys=OFF")
    c.execute("UPDATE tasks SET work_item_id = 'wi-nope' WHERE task_id = 't1'")  # 외래키 검사가 잡을 옛 결함
    c.execute("PRAGMA foreign_keys=ON")
    before = _dump(c, V11_TABLES)
    events_sql = c.execute("SELECT sql FROM sqlite_master WHERE name = 'work_item_events'").fetchone()[0]
    with pytest.raises(RuntimeError, match="외래키"):
        init_schema(c)
    assert c.execute("SELECT version FROM schema_version").fetchone()[0] == 11
    assert _table_names(c) - {"sqlite_sequence"} == V11_TABLES | {"schema_version"}
    for table, columns in V12_COLUMNS.items():
        assert not columns & _columns(c, table), table
    assert c.execute("SELECT sql FROM sqlite_master WHERE name = 'work_item_events'").fetchone()[0] == events_sql
    assert _dump(c, V11_TABLES) == before
    assert not c.in_transaction
    c.execute("UPDATE tasks SET work_item_id = 'wi-000000000001' WHERE task_id = 't1'")
    init_schema(c)  # 원인이 사라지면 다시 돌릴 수 있다
    assert c.execute("SELECT version FROM schema_version").fetchone()[0] == SCHEMA_VERSION
    assert [r[0] for r in c.execute("SELECT id FROM work_item_events ORDER BY id")] == [5, 9]
    c.close()


@pytest.mark.parametrize("old", ["v10", "v11"])
def test_fresh_schema_matches_v11_migrated_schema(tmp_path, old):
    fresh = connect(tmp_path / "fresh.sqlite")
    init_schema(fresh)
    migrated = (_v10_db if old == "v10" else _v11_db)(tmp_path / "old.sqlite")
    init_schema(migrated)
    assert _table_names(fresh) == _table_names(migrated)
    for table in TABLES:
        cols = "SELECT name, type, \"notnull\", dflt_value, pk FROM pragma_table_info(?) ORDER BY cid"
        assert fresh.execute(cols, (table,)).fetchall() == migrated.execute(cols, (table,)).fetchall(), table
        assert _foreign_keys(fresh, table) == _foreign_keys(migrated, table), table
    assert _indexes(fresh) == _indexes(migrated)
    sql = "SELECT sql FROM sqlite_master WHERE name IN ('work_item_events', 'work_pull_requests') ORDER BY name"
    assert fresh.execute(sql).fetchall() == migrated.execute(sql).fetchall()  # CHECK 까지 같은 원문
    fresh.close()
    migrated.close()


# --- phase 17: v12 → v13 맡기기 정책·러너 능력·검증만 다시·맡김 대기·지시 메모·알림 사건 (ADR-0023) -------------------

V12_SCHEMA = (Path(__file__).parent / "fixtures" / "schema_v12.sql").read_text()
V13_COLUMNS = {
    "agents": {"delegation_policy"},
    "connectors": {"capabilities_json"},
    "executions": {"verify_only"},
    "tasks": {"start_pending_at"},
    "work_items": {"handoff_note", "handoff_note_by_member_id"},
}
_NOTIFICATION_INSERT = ("INSERT INTO notifications (notification_id, session_id, event, task_id, dedupe_key, content,"
                        " payload_json, state, created_at) VALUES (?, 's1', ?, 't1', ?, 'c', '{}', 'pending', ?)")


def _v12_db(db_path):
    """phase 16 서버가 남긴 모양의 v12 DB. 워크스페이스 s1 — 관리자·멤버, 러너 1(소유자 = 멤버)·로컬 에이전트 1,
    업무 1건(단계 Task 1, 실행 1), 열린 사람 요청 1, 알림 2건(공용·개인), 업무 이벤트 2건(id 5·9)."""
    c = connect(db_path)
    c.executescript(V12_SCHEMA)
    c.execute("INSERT INTO schema_version (version) VALUES (12)")
    c.execute("INSERT INTO sessions (session_id, created_at, is_operator) VALUES ('s1', ?, 1)", (NOW,))
    for spec in PHASE8_BUILTIN_KINDS:
        c.execute("INSERT INTO kinds (session_id, kind, spec_json, created_at) VALUES ('s1', ?, ?, ?)",
                  (spec.kind, spec.model_dump_json(), NOW))
    c.execute("INSERT INTO members (member_id, session_id, display_name, role, created_at, email, password_hash)"
              " VALUES ('mem-00000001', 's1', '관리자', 'admin', ?, 'a@example.com', 'scrypt$h')", (NOW,))
    c.execute("INSERT INTO members (member_id, session_id, display_name, role, created_at)"
              " VALUES ('mem-0000000a', 's1', '김OO', 'member', ?)", (NOW,))
    c.execute("INSERT INTO connectors (connector_id, token_sha256, created_at, supported_kinds_json, owner_member_id)"
              " VALUES ('conn-1', 'h', ?, '[\"bug_fix\"]', 'mem-0000000a')", (NOW,))
    c.execute("INSERT INTO agents (agent_id, name, owner_scope, connection_type, connector_id, capabilities_json,"
              " connection_state) VALUES ('agent-1', 'opensql', 'personal', 'local', 'conn-1', '[]', 'online')")
    c.execute("INSERT INTO work_items (work_item_id, session_id, key_number, title, request, kind, assignee_type,"
              " assignee_id, status, status_reason, source_type, created_at, updated_at, requested_by_member_id)"
              " VALUES ('wi-000000000001', 's1', 1, '쿠폰 오류', '고쳐 주세요', 'bug_fix', 'agent', 'agent-1',"
              " '내 차례', '사람 요청 — 금액?', 'manual', ?, ?, 'mem-00000001')", (NOW, NOW))
    _insert_task(c, "t1", "s1", "bug_fix")
    c.execute("UPDATE tasks SET work_item_id = 'wi-000000000001', chosen_agent_id = 'agent-1' WHERE task_id = 't1'")
    c.execute("INSERT INTO executions (execution_id, task_id, attempt_no, start_key, agent_id, kind, request_json,"
              " status, created_at) VALUES ('e1', 't1', 1, 'k1', 'agent-1', 'bug_fix', '{}', 'result_ready', ?)", (NOW,))
    c.execute("INSERT INTO human_requests (request_id, task_id, code, question, cause_key, task_revision, revision,"
              " state, created_at) VALUES ('hr-00000001', 't1', 'fix_needs_information', '금액?', 'a:1', 1, 1, 'open', ?)",
              (NOW,))
    c.execute(_NOTIFICATION_INSERT, ("ntf-00000001", "human_request", "human_request:hr-00000001", NOW))
    c.execute("INSERT INTO notifications (notification_id, session_id, event, task_id, dedupe_key, content,"
              " payload_json, state, attempts, created_at, sent_at, recipient_member_id, channel)"
              " VALUES ('ntf-00000002', 's1', 'task_failed', 't1', 'task_failed:e1', 'c', '{}', 'sent', 1, ?, ?,"
              " 'mem-0000000a', 'personal')", (NOW, NOW))
    event = ("INSERT INTO work_item_events (id, work_item_id, session_id, type, config_revision, occurred_at,"
             " data_json) VALUES (?, 'wi-000000000001', 's1', ?, 1, ?, '{}')")
    c.execute(event, (5, "status_changed", NOW))
    c.execute(event, (9, "direct_started", NOW))
    return c


def test_v13_constants():
    from workflow.adapters import db
    from workflow.domain.delegation import DELEGATION_POLICIES

    assert db.NOTIFICATION_EVENTS == (
        "human_request", "pr_opened", "task_failed", "delegated_to_you", "runner_offline_waiting",
        "delegation_declined",
    )
    assert db.WORK_ITEM_EVENT_TYPES == (
        "status_changed", "assigned", "priority_changed", "direct_started", "direct_stopped", "pull_request_linked",
        "handoff_note",
    )
    assert DELEGATION_POLICIES == ("run", "owner_approval")


def test_fresh_db_has_v13_columns_and_checks(conn):
    _v12_base(conn)
    for table, columns in V13_COLUMNS.items():
        assert columns <= _columns(conn, table), table
    assert ("members", "handoff_note_by_member_id", "member_id") in _foreign_keys(conn, "work_items")
    # 정책 — 기본 run, 두 값만
    conn.execute("INSERT INTO agents (agent_id, name, owner_scope, connection_type, capabilities_json,"
                 " connection_state) VALUES ('agent-1', 'a', 'team', 'local', '[]', 'online')")
    assert conn.execute("SELECT delegation_policy FROM agents").fetchone()[0] == "run"
    conn.execute("UPDATE agents SET delegation_policy = 'owner_approval'")
    for bad in ("approve", "", None):
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE agents SET delegation_policy = ?", (bad,))
    # 검증만 다시 표시 — 기본 0, 0·1 만
    assert {r[0] for r in conn.execute("SELECT verify_only FROM executions")} == {0}
    conn.execute("UPDATE executions SET verify_only = 1")
    for bad in (2, None):
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE executions SET verify_only = ?", (bad,))
    # 지시 메모 쓴 멤버는 있는 멤버
    conn.execute("UPDATE work_items SET handoff_note = '결제 모듈만', handoff_note_by_member_id = 'mem-00000002'")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE work_items SET handoff_note_by_member_id = 'mem-nope'")
    conn.execute("UPDATE tasks SET start_pending_at = ?", (NOW,))
    conn.execute("UPDATE connectors SET capabilities_json = '[\"verify_only\"]'")


def test_v13_notification_events_and_work_item_event_types(conn):
    _v12_base(conn)
    from workflow.adapters.db import NOTIFICATION_EVENTS, WORK_ITEM_EVENT_TYPES

    for n, event in enumerate(NOTIFICATION_EVENTS):
        conn.execute(_NOTIFICATION_INSERT, (f"ntf-{n}", event, f"k{n}", NOW))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(_NOTIFICATION_INSERT, ("ntf-x", "approved", "kx", NOW))
    for event_type in WORK_ITEM_EVENT_TYPES:
        conn.execute(_WORK_EVENT_INSERT, (event_type, NOW))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(_WORK_EVENT_INSERT, ("commented", NOW))
    notification_indexes = {r["name"] for r in conn.execute("PRAGMA index_list(notifications)")}
    assert "ix_notifications_recipient" in notification_indexes
    # 재생성하는 두 표를 참조하는 표가 없다 — 재생성해도 끊길 외래키가 없다
    assert _referencing(conn, "notifications") == []
    assert _referencing(conn, "work_item_events") == []
    # 알림 경로·받는 사람 제약은 재생성 뒤에도 그대로
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE notifications SET channel = 'dm'")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE notifications SET recipient_member_id = 'mem-nope'")


def test_v12_fixture_is_the_phase16_schema(db_path):
    c = _v12_db(db_path)
    assert c.execute("SELECT version FROM schema_version").fetchone()[0] == 12
    assert _table_names(c) - {"sqlite_sequence"} == V13_TABLES | {"schema_version"}
    for table, columns in V13_COLUMNS.items():
        assert not columns & _columns(c, table), table
    with pytest.raises(sqlite3.IntegrityError):  # v12 는 새 알림 사건을 모른다
        c.execute(_NOTIFICATION_INSERT, ("ntf-x", "delegated_to_you", "kx", NOW))
    with pytest.raises(sqlite3.IntegrityError):  # v12 는 새 이벤트 종류를 모른다
        c.execute(_WORK_EVENT_INSERT, ("handoff_note", NOW))
    c.close()


def test_migrates_v12_to_v13_preserving_rows_with_defaults(db_path):
    c = _v12_db(db_path)
    columns = _column_lists(c, V13_TABLES)
    before = _dump(c, V13_TABLES)
    tasks_page = c.execute("SELECT rootpage FROM sqlite_master WHERE name = 'tasks'").fetchone()[0]
    c.close()

    c = connect(db_path)
    init_schema(c)
    assert [tuple(r) for r in c.execute("SELECT version FROM schema_version")] == [(SCHEMA_VERSION,)]
    assert _without_v15_seeds(_without_jira_mappings(_dump(c, V13_TABLES, columns))) == before  # 기존 행 수·열 값은 그대로
    assert [r[0] for r in c.execute("SELECT id FROM work_item_events ORDER BY id")] == [5, 9]
    assert [r[0] for r in c.execute("SELECT notification_id FROM notifications ORDER BY notification_id")] == [
        "ntf-00000001", "ntf-00000002"]
    assert [tuple(r) for r in c.execute("SELECT delegation_policy FROM agents")] == [("run",)]
    assert [tuple(r) for r in c.execute("SELECT verify_only FROM executions")] == [(0,)]
    assert [tuple(r) for r in c.execute("SELECT capabilities_json FROM connectors")] == [(None,)]
    assert [tuple(r) for r in c.execute("SELECT start_pending_at FROM tasks")] == [(None,)]
    assert [tuple(r) for r in c.execute("SELECT handoff_note, handoff_note_by_member_id FROM work_items")] == [(None, None)]
    # tasks 는 재생성하지 않는다 — 칸만 붙는다(같은 b-tree)
    assert c.execute("SELECT rootpage FROM sqlite_master WHERE name = 'tasks'").fetchone()[0] == tasks_page
    assert {"ix_notifications_recipient"} <= {r["name"] for r in c.execute("PRAGMA index_list(notifications)")}
    assert {"ix_work_item_events_item", "ix_work_item_events_session"} <= {
        r["name"] for r in c.execute("PRAGMA index_list(work_item_events)")}
    c.execute(_NOTIFICATION_INSERT, ("ntf-00000003", "delegation_declined", "delegation_declined:hr-1", NOW))
    c.execute(_WORK_EVENT_INSERT, ("handoff_note", NOW))
    assert c.execute("SELECT MAX(id) FROM work_item_events").fetchone()[0] == 10  # id 는 이어서
    with pytest.raises(sqlite3.IntegrityError):
        c.execute("UPDATE agents SET delegation_policy = 'ask'")
    assert c.execute("PRAGMA foreign_key_check").fetchall() == []
    assert c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"

    after = _dump(c, TABLES)
    init_schema(c)  # 재실행은 아무것도 바꾸지 않는다
    assert _dump(c, TABLES) == after
    c.close()


def test_v13_migration_rolls_back_on_foreign_key_violation(db_path):
    c = _v12_db(db_path)
    c.execute("PRAGMA foreign_keys=OFF")
    c.execute("UPDATE tasks SET work_item_id = 'wi-nope' WHERE task_id = 't1'")  # 외래키 검사가 잡을 옛 결함
    c.execute("PRAGMA foreign_keys=ON")
    before = _dump(c, V13_TABLES)
    recreated = "SELECT name, sql FROM sqlite_master WHERE name IN ('notifications', 'work_item_events') ORDER BY name"
    sql_before = c.execute(recreated).fetchall()
    with pytest.raises(RuntimeError, match="외래키"):
        init_schema(c)
    assert c.execute("SELECT version FROM schema_version").fetchone()[0] == 12
    for table, columns in V13_COLUMNS.items():
        assert not columns & _columns(c, table), table
    assert c.execute(recreated).fetchall() == sql_before
    assert _dump(c, V13_TABLES) == before
    assert not c.in_transaction
    c.execute("UPDATE tasks SET work_item_id = 'wi-000000000001' WHERE task_id = 't1'")
    init_schema(c)  # 원인이 사라지면 다시 돌릴 수 있다
    assert c.execute("SELECT version FROM schema_version").fetchone()[0] == SCHEMA_VERSION
    c.close()


@pytest.mark.parametrize("old", ["v11", "v12"])
def test_fresh_schema_matches_v12_migrated_schema(tmp_path, old):
    fresh = connect(tmp_path / "fresh.sqlite")
    init_schema(fresh)
    migrated = (_v11_db if old == "v11" else _v12_db)(tmp_path / "old.sqlite")
    init_schema(migrated)
    assert _table_names(fresh) == _table_names(migrated)
    for table in TABLES:
        cols = "SELECT name, type, \"notnull\", dflt_value, pk FROM pragma_table_info(?) ORDER BY cid"
        assert fresh.execute(cols, (table,)).fetchall() == migrated.execute(cols, (table,)).fetchall(), table
        assert _foreign_keys(fresh, table) == _foreign_keys(migrated, table), table
    assert _indexes(fresh) == _indexes(migrated)
    sql = "SELECT sql FROM sqlite_master WHERE name IN ('notifications', 'work_item_events') ORDER BY name"
    assert fresh.execute(sql).fetchall() == migrated.execute(sql).fetchall()  # CHECK 까지 같은 원문
    fresh.close()
    migrated.close()


# --- phase 18: v13 → v14 Jira 표·source_type CHECK·Runloom PR 이슈 번호 NULL (ADR-0024) ----------------------------

V13_SCHEMA = (Path(__file__).parent / "fixtures" / "schema_v13.sql").read_text()
JIRA_CONNECTION_COLUMNS = [
    "session_id", "site_url", "cloud_id", "api_base", "email", "account_id", "display_name", "connected_at",
    "disconnected_at", "auth_failed_at", "updated_at",
]
JIRA_PROJECT_COLUMNS = [
    "source_id", "session_id", "project_id", "project_key", "project_name", "github_source_id", "issue_types_json",
    "start_mode", "start_at", "status_on_start", "status_on_review", "status_on_done", "followup_issue_type",
    "choices_json", "cursor_ms", "cursor_updated_at", "enabled", "created_at", "updated_at",
]
JIRA_ISSUE_COLUMNS = [
    "source_id", "issue_id", "issue_key", "task_id", "source_revision", "snapshot_json", "snapshot_digest",
    "issue_updated_at", "state", "status_name", "delegated_at", "delegated_by", "created_at", "updated_at",
]
JIRA_DELIVERY_COLUMNS = [
    "delivery_id", "session_id", "source_id", "work_item_id", "action", "moment", "target", "cause_issue_id",
    "dedupe_key", "state", "result_issue_id", "result_issue_key", "attempts", "next_at", "last_error", "note",
    "created_at", "updated_at", "delivered_at",
]
_WORK_INSERT = ("INSERT INTO work_items (work_item_id, session_id, key_number, title, request, kind, status,"
                " status_reason, source_type, source_id, source_item_id, created_at, updated_at)"
                " VALUES (?, 's1', ?, 't', 'r', 'bug_fix', '대기', '', ?, ?, ?, ?, ?)")
_MAPPING_INSERT = ("INSERT INTO field_mappings (mapping_id, session_id, source_type, field, source_value, runloom_value,"
                   " position, created_at) VALUES (?, 's1', ?, 'kind', ?, 'bug_fix', 1, ?)")
_JIRA_PROJECT_INSERT = (
    "INSERT INTO jira_projects (source_id, session_id, project_id, project_key, project_name, github_source_id,"
    " start_mode, start_at, enabled, created_at, updated_at) VALUES (?, 's1', ?, 'SHOP', '쇼핑', ?, ?, ?, ?, ?, ?)"
)
_JIRA_ISSUE_INSERT = (
    "INSERT INTO jira_issues (source_id, issue_id, issue_key, task_id, source_revision, snapshot_json, snapshot_digest,"
    " issue_updated_at, state, status_name, delegated_at, delegated_by, created_at, updated_at)"
    " VALUES ('jps-00000001', ?, 'SHOP-12', ?, ?, '{}', 'd', ?, ?, 'To Do', ?, ?, ?, ?)"
)
_JIRA_DELIVERY_INSERT = (
    "INSERT INTO jira_deliveries (delivery_id, session_id, source_id, work_item_id, action, moment, target,"
    " cause_issue_id, dedupe_key, state, result_issue_id, attempts, created_at, updated_at)"
    " VALUES (?, 's1', 'jps-00000001', 'wi-000000000001', ?, ?, '리뷰중', ?, ?, ?, ?, ?, ?, ?)"
)


def _without_jira_mappings(dump: dict[str, list[tuple]]) -> dict[str, list[tuple]]:
    """v14 가 워크스페이스마다 넣는 jira 기본 매핑 행을 뺀 덤프 — 나머지 행은 그대로여야 한다."""
    return {
        table: [row for row in rows if not (table == "field_mappings" and "jira" in row)]
        for table, rows in dump.items()
    }


def _jira_base(conn) -> None:
    """Jira 표 제약 확인용 최소 행 — v12 기본(멤버 둘·GitHub 소스·업무 하나) + 단계 하나 + Jira 프로젝트 하나."""
    _v12_base(conn)
    conn.execute(_JIRA_PROJECT_INSERT, ("jps-00000001", "10000", "ghs-00000001", "from_now", NOW, 1, NOW, NOW))


def test_v14_constants():
    from workflow.adapters import db

    assert db.JIRA_DELIVERY_STATES == ("pending", "sending", "delivered", "unknown", "failed", "skipped")


def test_fresh_db_has_v14_jira_tables_keys_and_indexes(conn):
    assert PHASE18_TABLES <= _table_names(conn)
    assert _column_lists(conn, ["jira_connections", "jira_projects", "jira_issues", "jira_deliveries"]) == {
        "jira_connections": JIRA_CONNECTION_COLUMNS, "jira_projects": JIRA_PROJECT_COLUMNS,
        "jira_issues": JIRA_ISSUE_COLUMNS, "jira_deliveries": JIRA_DELIVERY_COLUMNS,
    }
    assert "token" not in " ".join(JIRA_CONNECTION_COLUMNS)  # 토큰은 비밀 저장소에만
    assert _foreign_keys(conn, "jira_connections") == {("sessions", "session_id", "session_id")}
    assert _foreign_keys(conn, "jira_projects") == {
        ("sessions", "session_id", "session_id"), ("github_sources", "github_source_id", "source_id")}
    assert _foreign_keys(conn, "jira_issues") == {
        ("jira_projects", "source_id", "source_id"), ("tasks", "task_id", "task_id")}
    assert _foreign_keys(conn, "jira_deliveries") == {
        ("sessions", "session_id", "session_id"), ("jira_projects", "source_id", "source_id"),
        ("work_items", "work_item_id", "work_item_id")}
    indexed = {
        table: {(i["unique"], tuple(r["name"] for r in conn.execute(f"PRAGMA index_info({i['name']})")))
                for i in conn.execute(f"PRAGMA index_list({table})")}
        for table in ("jira_projects", "jira_issues", "jira_deliveries", "work_items")
    }
    assert (1, ("session_id", "project_id")) in indexed["jira_projects"]
    assert (1, ("task_id",)) in indexed["jira_issues"]
    assert {(1, ("dedupe_key",)), (0, ("state", "next_at")), (0, ("work_item_id", "created_at"))} <= indexed[
        "jira_deliveries"]
    assert {(0, ("session_id", "status")), (1, ("source_id", "source_item_id"))} <= indexed["work_items"]
    names = {r["name"] for r in conn.execute("PRAGMA index_list(jira_deliveries)")}
    assert {"ix_jira_deliveries_due", "ix_jira_deliveries_work"} <= names
    assert {"ix_work_items_status", "ux_work_items_jira_issue"} <= {
        r["name"] for r in conn.execute("PRAGMA index_list(work_items)")}


def test_v14_source_type_accepts_jira_and_rejects_unknown(conn):
    _v12_base(conn)
    conn.execute(_WORK_INSERT, ("wi-000000000002", 2, "jira", "jps-00000001", "10001", NOW, NOW))
    with pytest.raises(sqlite3.IntegrityError):  # 모르는 원본 종류
        conn.execute(_WORK_INSERT, ("wi-000000000003", 3, "linear", None, None, NOW, NOW))
    with pytest.raises(sqlite3.IntegrityError):  # 같은 Jira 이슈의 업무는 하나
        conn.execute(_WORK_INSERT, ("wi-000000000004", 4, "jira", "jps-00000001", "10001", NOW, NOW))
    conn.execute(_WORK_INSERT, ("wi-000000000005", 5, "jira", "jps-00000001", None, NOW, NOW))  # 후속 이슈 전
    conn.execute(_WORK_INSERT, ("wi-000000000006", 6, "jira", "jps-00000001", None, NOW, NOW))
    conn.execute(_WORK_INSERT, ("wi-000000000007", 7, "jira", "jps-00000002", "10001", NOW, NOW))  # 다른 프로젝트
    conn.execute(_WORK_INSERT, ("wi-000000000008", 8, "github", "jps-00000001", "10001", NOW, NOW))  # Jira 만 유일
    conn.execute(_MAPPING_INSERT, ("map-00000001", "jira", "Bug", NOW))
    conn.execute(_MAPPING_INSERT, ("map-00000002", "n8n", "Bug", NOW))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(_MAPPING_INSERT, ("map-00000003", "linear", "Bug", NOW))
    with pytest.raises(sqlite3.IntegrityError):  # 같은 원본 종류·칸·값은 하나
        conn.execute(_MAPPING_INSERT, ("map-00000004", "jira", "Bug", NOW))


def test_v14_task_pull_request_issue_number_may_be_null(conn):
    _cycle_base(conn)
    conn.execute(_PR_INSERT.replace("41", "?"), ("t1", "ghs-00000001", None, "pending", None, NOW, NOW))
    with pytest.raises(sqlite3.IntegrityError):  # 값이 있으면 1 이상
        conn.execute("UPDATE task_pull_requests SET issue_number = 0")
    conn.execute("UPDATE task_pull_requests SET issue_number = 41")


def test_v14_jira_connection_and_project_checks(conn):
    _jira_base(conn)
    insert = ("INSERT INTO jira_connections (session_id, site_url, cloud_id, api_base, email, account_id, display_name,"
              " connected_at, updated_at) VALUES (?, 'https://acme.atlassian.net', 'c', ?, 'a@example.com', 'acc',"
              " '김OO', ?, ?)")
    conn.execute(insert, ("s1", "gateway", NOW, NOW))
    for params in (("s1", "site", NOW, NOW),  # 워크스페이스당 1행
                   ("s-nope", "site", NOW, NOW),  # 없는 워크스페이스
                   ("s1", "proxy", NOW, NOW)):  # 호출 기준은 두 값
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(insert, params)
    conn.execute("UPDATE jira_connections SET api_base = 'site'")
    project = conn.execute("SELECT * FROM jira_projects").fetchone()
    assert (project["issue_types_json"], project["choices_json"], project["cursor_ms"]) == ("[]", "{}", None)
    for params in (
        ("jps-00000002", "10000", "ghs-00000001", "all_open", NOW, 1, NOW, NOW),  # 같은 워크스페이스 같은 프로젝트
        ("jps-00000003", "10001", "ghs-nope", "all_open", NOW, 1, NOW, NOW),  # 연결 저장소는 있는 GitHub 소스
        ("jps-00000004", "10002", "ghs-00000001", "later", NOW, 1, NOW, NOW),  # 시작점은 두 값
        ("jps-00000005", "10003", "ghs-00000001", "all_open", NOW, 2, NOW, NOW),  # 켜짐은 0·1
        ("jps-00000006", "10004", "ghs-00000001", "all_open", None, 1, NOW, NOW),  # 시작 시각 필수
    ):
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(_JIRA_PROJECT_INSERT, params)
    conn.execute("UPDATE jira_projects SET cursor_ms = 0")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE jira_projects SET cursor_ms = -1")


def test_v14_jira_issue_checks(conn):
    _jira_base(conn)
    _insert_task(conn, "t-j2", "s1", "bug_fix")
    conn.execute(_JIRA_ISSUE_INSERT, ("10001", "t1", 1, NOW, "open", None, None, NOW, NOW))
    conn.execute(_JIRA_ISSUE_INSERT, ("10002", "t-j2", 1, NOW, "closed", NOW, "followup", NOW, NOW))
    conn.execute("UPDATE jira_issues SET delegated_at = ?, delegated_by = 'operator' WHERE issue_id = '10001'", (NOW,))
    _insert_task(conn, "t-j3", "s1", "bug_fix")
    for params in (
        ("10001", "t-j3", 1, NOW, "open", None, None, NOW, NOW),  # 같은 프로젝트 같은 이슈
        ("10003", "t1", 1, NOW, "open", None, None, NOW, NOW),  # 단계 하나에 이슈 하나
        ("10004", "t-nope", 1, NOW, "open", None, None, NOW, NOW),  # 없는 단계
        ("10005", "t-j3", 0, NOW, "open", None, None, NOW, NOW),  # revision 1 이상
        ("10006", "t-j3", 1, NOW, "done", None, None, NOW, NOW),  # 열림·닫힘만
        ("10007", "t-j3", 1, NOW, "open", NOW, "label", NOW, NOW),  # 지시는 operator·followup
        ("10008", "t-j3", 1, NOW, "open", NOW, None, NOW, NOW),  # 지시 시각과 지시자는 함께
        ("10009", "t-j3", 1, NOW, "open", None, "operator", NOW, NOW),
    ):
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(_JIRA_ISSUE_INSERT, params)


def test_v14_jira_delivery_checks_and_dedupe_key(conn):
    _jira_base(conn)
    conn.execute(_JIRA_DELIVERY_INSERT, ("jdl-00000001", "transition", "review", None,
                                         "transition:wi-000000000001:review", "pending", None, 0, NOW, NOW))
    conn.execute(_JIRA_DELIVERY_INSERT, ("jdl-00000002", "create_issue", None, "10001",
                                         "create_issue:wi-000000000001", "delivered", "10002", 1, NOW, NOW))
    from workflow.adapters.db import JIRA_DELIVERY_STATES

    for n, state in enumerate(JIRA_DELIVERY_STATES):
        conn.execute(_JIRA_DELIVERY_INSERT, (f"jdl-1000000{n}", "transition", "start", None, f"k{n}", state, None, 0,
                                             NOW, NOW))
    for params in (
        ("jdl-00000003", "transition", "review", None, "transition:wi-000000000001:review", "pending", None, 0, NOW,
         NOW),  # 같은 중복 키는 한 번
        ("jdl-00000004", "comment", None, None, "x1", "pending", None, 0, NOW, NOW),  # 동작은 둘
        ("jdl-00000005", "transition", None, None, "x2", "pending", None, 0, NOW, NOW),  # 전환은 순간이 있어야
        ("jdl-00000006", "transition", "merge", None, "x3", "pending", None, 0, NOW, NOW),  # 순간은 셋
        ("jdl-00000007", "create_issue", "done", "10001", "x4", "pending", None, 0, NOW, NOW),  # 생성은 순간 없음
        ("jdl-00000008", "create_issue", None, None, "x5", "pending", None, 0, NOW, NOW),  # 생성은 원인 이슈가 있어야
        ("jdl-00000009", "transition", "start", "10001", "x6", "pending", None, 0, NOW, NOW),  # 전환은 원인 이슈 없음
        ("jdl-0000000a", "create_issue", None, "10001", "x7", "delivered", None, 1, NOW, NOW),  # 생성 완료는 결과 이슈
        ("jdl-0000000b", "transition", "start", None, "x8", "sent", None, 0, NOW, NOW),  # 상태 허용 값 밖
        ("jdl-0000000c", "transition", "start", None, "x9", "pending", None, -1, NOW, NOW),  # 시도 수 0 이상
    ):
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(_JIRA_DELIVERY_INSERT, params)
    with pytest.raises(sqlite3.IntegrityError):  # 없는 업무
        conn.execute(_JIRA_DELIVERY_INSERT.replace("'wi-000000000001'", "'wi-nope'"),
                     ("jdl-0000000d", "transition", "done", None, "x10", "pending", None, 0, NOW, NOW))
    with pytest.raises(sqlite3.IntegrityError):  # 없는 프로젝트
        conn.execute(_JIRA_DELIVERY_INSERT.replace("'jps-00000001'", "'jps-nope'"),
                     ("jdl-0000000e", "transition", "done", None, "x11", "pending", None, 0, NOW, NOW))


def test_create_session_seeds_jira_default_mapping(conn):
    from workflow.adapters import repo

    assert ("jira", "kind", "*", "bug_fix") in repo.DEFAULT_FIELD_MAPPINGS
    repo.create_session(conn, "s1", NOW)
    jira = repo.list_field_mappings(conn, "s1", "jira")
    assert [(r.source_type, r.field, r.source_value, r.runloom_value) for r in jira] == [
        ("jira", "kind", "*", "bug_fix")]


def _v13_db(db_path):
    """phase 17 서버가 남긴 모양의 v13 DB. 워크스페이스 s1 — 관리자·멤버, GitHub 소스 1, 업무 2건(key 3·7, 사이 링크·
    지시 메모·직접 작업), 단계 Task 2(감지 PR·Runloom PR 각 1), 업무 이벤트 2건(id 5·9), 매핑 2행(github·n8n).
    워크스페이스 s2 — bug_fix 종류가 없다(jira 기본 매핑을 넣지 않는다)."""
    c = connect(db_path)
    c.executescript(V13_SCHEMA)
    c.execute("INSERT INTO schema_version (version) VALUES (13)")
    for session_id in ("s1", "s2"):
        c.execute("INSERT INTO sessions (session_id, created_at, is_operator) VALUES (?, ?, 1)", (session_id, NOW))
    for spec in PHASE8_BUILTIN_KINDS:
        c.execute("INSERT INTO kinds (session_id, kind, spec_json, created_at) VALUES ('s1', ?, ?, ?)",
                  (spec.kind, spec.model_dump_json(), NOW))
    _seed_kind(c, "s2", "classify")
    c.execute("INSERT INTO members (member_id, session_id, display_name, role, created_at, email, password_hash)"
              " VALUES ('mem-00000001', 's1', '관리자', 'admin', ?, 'a@example.com', 'scrypt$h')", (NOW,))
    c.execute("INSERT INTO members (member_id, session_id, display_name, role, created_at)"
              " VALUES ('mem-0000000a', 's1', '김OO', 'member', ?)", (NOW,))
    c.execute("INSERT INTO github_sources (source_id, session_id, repository_full_name, config_json, cursor,"
              " created_at, updated_at) VALUES ('ghs-00000001', 's1', 'acme/billing', '{}', ?, ?, ?)", (NOW, NOW, NOW))
    work = ("INSERT INTO work_items (work_item_id, session_id, key_number, title, request, kind, assignee_type,"
            " assignee_id, status, status_reason, source_type, source_id, source_item_id, source_key, source_url,"
            " source_state, created_at, updated_at, requested_by_member_id, direct_member_id, direct_started_at,"
            " direct_branch, handoff_note, handoff_note_by_member_id)"
            " VALUES (?, 's1', ?, ?, 'r', 'bug_fix', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)")
    c.execute(work, ("wi-000000000003", 3, "쿠폰 오류", "member", "mem-0000000a", "직접 작업 중", "김OO 작업", "github",
                     "ghs-00000001", "123456", "acme/billing#41", "https://github.com/acme/billing/issues/41", "open",
                     NOW, NOW, "mem-00000001", "mem-0000000a", NOW, "run-3-fix", "결제만", "mem-00000001"))
    c.execute(work, ("wi-000000000007", 7, "리뷰", None, None, "대기", "선행 대기", "manual", None, None, None, None,
                     None, NOW, NOW, None, None, None, None, None, None))
    c.execute("INSERT INTO work_item_links (from_work_item_id, to_work_item_id, type, created_at)"
              " VALUES ('wi-000000000003', 'wi-000000000007', 'blocks', ?)", (NOW,))
    _insert_task(c, "t1", "s1", "bug_fix")
    _insert_task(c, "t2", "s1", "code_review")
    c.execute("UPDATE tasks SET work_item_id = 'wi-000000000003' WHERE task_id = 't1'")
    c.execute("UPDATE tasks SET work_item_id = 'wi-000000000007', predecessor_task_id = 't1' WHERE task_id = 't2'")
    event = ("INSERT INTO work_item_events (id, work_item_id, session_id, type, config_revision, occurred_at,"
             " data_json) VALUES (?, 'wi-000000000003', 's1', ?, 1, ?, '{}')")
    c.execute(event, (5, "status_changed", NOW))
    c.execute(event, (9, "handoff_note", NOW))
    c.execute("INSERT INTO work_pull_requests (session_id, work_item_id, source_id, repository_full_name, pr_number,"
              " title, pr_url, head_branch, state, draft, pr_updated_at, created_at, updated_at) VALUES ('s1',"
              " 'wi-000000000003', 'ghs-00000001', 'acme/billing', 8, 'RUN-3 고침',"
              " 'https://github.com/acme/billing/pull/8', 'run-3-fix', 'open', 0, ?, ?, ?)", (NOW, NOW, NOW))
    c.execute("INSERT INTO task_pull_requests (task_id, session_id, source_id, repository_full_name, issue_number,"
              " head_branch, fix_execution_id, review_execution_id, state, pr_number, pr_url, draft, attempts,"
              " created_at, updated_at) VALUES ('t1', 's1', 'ghs-00000001', 'acme/billing', 41, 'runloom/RUN-3',"
              " 'e1', 'e2', 'open', 9, 'https://github.com/acme/billing/pull/9', 1, 1, ?, ?)", (NOW, NOW))
    c.execute(_MAPPING_INSERT.replace("'kind'", "'priority'").replace("'bug_fix'", "'high'"),
              ("map-00000001", "github", "P1", NOW))
    c.execute(_MAPPING_INSERT, ("map-00000002", "n8n", "*", NOW))
    return c


def test_v13_fixture_is_the_phase17_schema(db_path):
    c = _v13_db(db_path)
    assert c.execute("SELECT version FROM schema_version").fetchone()[0] == 13
    assert _table_names(c) - {"sqlite_sequence"} == V13_TABLES | {"schema_version"}
    with pytest.raises(sqlite3.IntegrityError):  # v13 은 jira 원본을 모른다
        c.execute(_WORK_INSERT, ("wi-000000000009", 9, "jira", None, None, NOW, NOW))
    with pytest.raises(sqlite3.IntegrityError):
        c.execute(_MAPPING_INSERT, ("map-00000009", "jira", "Bug", NOW))
    with pytest.raises(sqlite3.IntegrityError):  # v13 Runloom PR 은 이슈 번호가 있어야
        c.execute("UPDATE task_pull_requests SET issue_number = NULL")
    c.close()


def test_migrates_v13_to_v14_preserving_rows_and_foreign_keys(db_path):
    c = _v13_db(db_path)
    columns = _column_lists(c, V13_TABLES)
    before = _dump(c, V13_TABLES)
    tasks_sql, tasks_page = c.execute("SELECT sql, rootpage FROM sqlite_master WHERE name = 'tasks'").fetchone()
    c.close()

    c = connect(db_path)
    init_schema(c)
    assert [tuple(r) for r in c.execute("SELECT version FROM schema_version")] == [(SCHEMA_VERSION,)]
    assert _column_lists(c, V13_TABLES) == columns  # 칸·순서 그대로
    assert _without_v15_seeds(_without_jira_mappings(_dump(c, V13_TABLES))) == before  # 행 수·id·key_number·칸 값 그대로
    assert [tuple(r) for r in c.execute("SELECT work_item_id, key_number FROM work_items ORDER BY key_number")] == [
        ("wi-000000000003", 3), ("wi-000000000007", 7)]
    # tasks 는 재생성하지 않는다
    assert tuple(c.execute("SELECT sql, rootpage FROM sqlite_master WHERE name = 'tasks'").fetchone()) == (
        tasks_sql, tasks_page)
    # jira 기본 매핑 — bug_fix 종류가 있는 워크스페이스에만 한 행
    jira = c.execute("SELECT session_id, field, source_value, runloom_value, position, created_at, mapping_id"
                     " FROM field_mappings WHERE source_type = 'jira'").fetchall()
    assert [tuple(r)[:5] for r in jira] == [("s1", "kind", "*", "bug_fix", 1)]
    assert jira[0]["mapping_id"].startswith("map-") and len(jira[0]["mapping_id"]) == 12
    for table in PHASE18_TABLES:
        assert c.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
    # 외래키는 다시 켜져 있고, 자식 표는 새 work_items 를 가리킨다
    assert c.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert c.execute("PRAGMA foreign_key_check").fetchall() == []
    assert c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    with pytest.raises(sqlite3.IntegrityError):
        c.execute(_WORK_EVENT_INSERT.replace("'wi-000000000001'", "'wi-nope'"), ("status_changed", NOW))
    with pytest.raises(sqlite3.IntegrityError):
        c.execute("INSERT INTO work_item_links (from_work_item_id, to_work_item_id, type, created_at)"
                  " VALUES ('wi-000000000003', 'wi-nope', 'blocks', ?)", (NOW,))
    with pytest.raises(sqlite3.IntegrityError):
        c.execute("UPDATE tasks SET work_item_id = 'wi-nope' WHERE task_id = 't1'")
    with pytest.raises(sqlite3.IntegrityError):  # 자식이 있는 업무는 지울 수 없다
        c.execute("DELETE FROM work_items WHERE work_item_id = 'wi-000000000003'")
    for table in ("work_item_events", "work_item_links", "tasks", "work_pull_requests", "jira_deliveries"):
        assert "work_items" in {r["table"] for r in c.execute(f"PRAGMA foreign_key_list({table})")}, table
    # 새 CHECK·유일 색인
    c.execute(_WORK_INSERT, ("wi-000000000010", 10, "jira", "jps-00000001", "10001", NOW, NOW))
    with pytest.raises(sqlite3.IntegrityError):
        c.execute(_WORK_INSERT, ("wi-000000000011", 11, "jira", "jps-00000001", "10001", NOW, NOW))
    with pytest.raises(sqlite3.IntegrityError):
        c.execute(_WORK_INSERT, ("wi-000000000012", 12, "linear", None, None, NOW, NOW))
    c.execute("UPDATE task_pull_requests SET issue_number = NULL")
    c.execute(_WORK_EVENT_INSERT.replace("'wi-000000000001'", "'wi-000000000003'"), ("handoff_note", NOW))
    assert c.execute("SELECT MAX(id) FROM work_item_events").fetchone()[0] == 10  # id 는 이어서

    after = _dump(c, TABLES)
    init_schema(c)  # 재실행은 아무것도 바꾸지 않는다
    assert _dump(c, TABLES) == after
    assert c.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    c.close()


def test_v14_migration_rolls_back_on_foreign_key_violation(db_path):
    c = _v13_db(db_path)
    c.execute("PRAGMA foreign_keys=OFF")
    c.execute("UPDATE tasks SET work_item_id = 'wi-nope' WHERE task_id = 't1'")  # 외래키 검사가 잡을 옛 결함
    c.execute("PRAGMA foreign_keys=ON")
    before = _dump(c, V13_TABLES)
    recreated = ("SELECT name, sql FROM sqlite_master WHERE name IN ('work_items', 'field_mappings',"
                 " 'task_pull_requests') ORDER BY name")
    sql_before = c.execute(recreated).fetchall()
    with pytest.raises(RuntimeError, match="외래키"):
        init_schema(c)
    assert c.execute("SELECT version FROM schema_version").fetchone()[0] == 13
    assert not PHASE18_TABLES & _table_names(c)
    assert c.execute(recreated).fetchall() == sql_before
    assert _dump(c, V13_TABLES) == before
    assert not c.in_transaction
    assert c.execute("PRAGMA foreign_keys").fetchone()[0] == 1  # 실패해도 다시 켠다
    c.execute("UPDATE tasks SET work_item_id = 'wi-000000000003' WHERE task_id = 't1'")
    init_schema(c)  # 원인이 사라지면 다시 돌릴 수 있다
    assert c.execute("SELECT version FROM schema_version").fetchone()[0] == SCHEMA_VERSION
    c.close()


@pytest.mark.parametrize("old", ["v12", "v13"])
def test_fresh_schema_matches_v13_migrated_schema(tmp_path, old):
    fresh = connect(tmp_path / "fresh.sqlite")
    init_schema(fresh)
    migrated = (_v12_db if old == "v12" else _v13_db)(tmp_path / "old.sqlite")
    init_schema(migrated)
    assert _table_names(fresh) == _table_names(migrated)
    for table in TABLES:
        cols = "SELECT name, type, \"notnull\", dflt_value, pk FROM pragma_table_info(?) ORDER BY cid"
        assert fresh.execute(cols, (table,)).fetchall() == migrated.execute(cols, (table,)).fetchall(), table
        assert _foreign_keys(fresh, table) == _foreign_keys(migrated, table), table
    assert _indexes(fresh) == _indexes(migrated)
    sql = ("SELECT name, sql FROM sqlite_master WHERE name IN ('work_items', 'field_mappings', 'task_pull_requests')"
           " OR name LIKE 'jira_%' ORDER BY name")
    assert fresh.execute(sql).fetchall() == migrated.execute(sql).fetchall()  # CHECK 까지 같은 원문
    fresh.close()
    migrated.close()


# --- phase 19: v14 → v15 판단 기준·판단 로그·자동 시작 설정, 내장 triage 종류, code.triage 능력 (ADR-0025) ------

V14_SCHEMA = (Path(__file__).parent / "fixtures" / "schema_v14.sql").read_text()
TRIAGE_CRITERIA_COLUMNS = ["session_id", "version", "body", "created_by_member_id", "created_at"]
TRIAGE_LOG_COLUMNS = [
    "triage_id", "session_id", "work_item_id", "work_revision", "task_id", "execution_id", "agent_id", "trigger",
    "requested_by_member_id", "criteria_version", "input_sha256", "candidates_json", "state", "result_json",
    "proceed", "confidence", "proposed_kind", "failed_code", "failed_message", "handling", "handled_by_member_id",
    "handled_at", "final_assignee_type", "final_assignee_id", "final_kind", "created_at", "finished_at", "updated_at",
]
TRIAGE_AUTOSTART_COLUMNS = ["session_id", "kind", "version", "enabled", "threshold", "created_by_member_id",
                            "created_at"]
_CRITERIA_INSERT = ("INSERT INTO triage_criteria (session_id, version, body, created_by_member_id, created_at)"
                    " VALUES (?, ?, ?, ?, ?)")
_AUTOSTART_INSERT = ("INSERT INTO triage_autostart (session_id, kind, version, enabled, threshold, created_by_member_id,"
                     " created_at) VALUES (?, ?, ?, ?, ?, ?, ?)")
SHA = "a" * 64


def _triage_log(**overrides):
    row = {
        "triage_id": "trg-00000001", "session_id": "s1", "work_item_id": "wi-000000000001", "work_revision": 1,
        "task_id": "t1", "execution_id": "e1", "agent_id": "a1", "trigger": "auto", "requested_by_member_id": None,
        "criteria_version": 1, "input_sha256": SHA, "candidates_json": "{}", "state": "running",
        "result_json": None, "proceed": None, "confidence": None, "proposed_kind": None, "failed_code": None,
        "failed_message": None, "handling": None, "handled_by_member_id": None, "handled_at": None,
        "final_assignee_type": None, "final_assignee_id": None, "final_kind": None, "created_at": NOW,
        "finished_at": None, "updated_at": NOW,
    }
    row.update(overrides)
    return row


def _insert_triage_log(conn, **overrides) -> None:
    row = _triage_log(**overrides)
    conn.execute(f"INSERT INTO triage_logs ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})",
                 tuple(row.values()))


def _triage_base(conn) -> None:
    """판단 표 제약 확인용 최소 행 — v12 기본(멤버 둘·GitHub 소스·업무 하나·단계 t1·t2·실행 e1) + 실행 e2 + 기준 v1."""
    _v12_base(conn)
    conn.execute("INSERT INTO executions (execution_id, task_id, attempt_no, start_key, agent_id, kind,"
                 " request_json, status, created_at) VALUES ('e2', 't2', 1, 'k2', 'a1', 'code_review', '{}',"
                 " 'queued', ?)", (NOW,))
    conn.execute(_CRITERIA_INSERT, ("s1", 1, "기준", None, NOW))


def test_fresh_db_has_v15_triage_tables_keys_and_indexes(conn):
    assert PHASE19_TABLES <= _table_names(conn)
    assert _column_lists(conn, ["triage_criteria", "triage_logs", "triage_autostart"]) == {
        "triage_criteria": TRIAGE_CRITERIA_COLUMNS, "triage_logs": TRIAGE_LOG_COLUMNS,
        "triage_autostart": TRIAGE_AUTOSTART_COLUMNS,
    }
    assert _foreign_keys(conn, "triage_criteria") == {
        ("sessions", "session_id", "session_id"), ("members", "created_by_member_id", "member_id")}
    assert _foreign_keys(conn, "triage_logs") == {
        ("sessions", "session_id", "session_id"), ("work_items", "work_item_id", "work_item_id"),
        ("tasks", "task_id", "task_id"), ("executions", "execution_id", "execution_id"),
        ("agents", "agent_id", "agent_id"), ("members", "requested_by_member_id", "member_id"),
        ("members", "handled_by_member_id", "member_id"),
        ("triage_criteria", "session_id", "session_id"), ("triage_criteria", "criteria_version", "version")}
    assert _foreign_keys(conn, "triage_autostart") == {
        ("sessions", "session_id", "session_id"), ("members", "created_by_member_id", "member_id")}
    # github_sources·tasks 는 그대로 — 판단 Agent 는 config_json 의 칸이다(ADR-0025 결정 3)
    assert "triage_agent_id" not in _columns(conn, "github_sources")
    indexes = {r["name"]: r["unique"] for r in conn.execute("PRAGMA index_list(triage_logs)")}
    assert indexes["ux_triage_logs_running"] == 1
    assert indexes["ix_triage_logs_work"] == 0 and indexes["ix_triage_logs_session"] == 0
    cols = {name: [r["name"] for r in conn.execute(f"PRAGMA index_info({name})")] for name in indexes}
    assert cols["ux_triage_logs_running"] == ["work_item_id"]
    assert cols["ix_triage_logs_work"] == ["work_item_id", "created_at"]
    assert cols["ix_triage_logs_session"] == ["session_id", "state"]


def test_v15_triage_criteria_checks(conn):
    _triage_base(conn)
    conn.execute(_CRITERIA_INSERT, ("s1", 2, "x" * 8000, "mem-00000001", NOW))
    for params in (
        ("s1", 2, "다시", None, NOW),  # 같은 워크스페이스 같은 버전
        ("s1", 0, "기준", None, NOW),  # 버전 1 이상
        ("s1", 3, "", None, NOW),  # 빈 본문
        ("s1", 3, "x" * 8001, None, NOW),  # 8000자 넘음
        ("s-nope", 1, "기준", None, NOW),  # 없는 워크스페이스
        ("s1", 3, "기준", "mem-nope", NOW),  # 없는 멤버
        ("s1", 3, "기준", None, None),  # 시각 필수
    ):
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(_CRITERIA_INSERT, params)


def test_v15_triage_log_checks(conn):
    _triage_base(conn)
    _insert_triage_log(conn)
    proposed = {"result_json": "{}", "proceed": "ready", "confidence": 0.9, "proposed_kind": "bug_fix"}
    for overrides in (
        {"triage_id": "trg-00000002"},  # 업무마다 도는 판단 하나
        {"triage_id": "trg-00000002", "task_id": "t2", "execution_id": "e1", "state": "failed", "failed_code": "x"},
        {"triage_id": "trg-00000002", "task_id": "t1", "execution_id": "e2", "state": "failed", "failed_code": "x"},
        {"triage_id": "trg-00000002", "task_id": "t2", "execution_id": "e2", "state": "done"},
        {"triage_id": "trg-00000002", "task_id": "t2", "execution_id": "e2", "trigger": "button"},
        {"triage_id": "trg-00000002", "task_id": "t2", "execution_id": "e2", "trigger": "manual"},  # 누가 눌렀는지
        {"triage_id": "trg-00000002", "task_id": "t2", "execution_id": "e2", "work_revision": 0},
        {"triage_id": "trg-00000002", "task_id": "t2", "execution_id": "e2", "input_sha256": "a" * 63},
        {"triage_id": "trg-00000002", "task_id": "t2", "execution_id": "e2", "criteria_version": 2},  # 없는 기준
        {"triage_id": "trg-00000002", "task_id": "t2", "execution_id": "e2", "agent_id": "a-nope"},
        {"triage_id": "trg-00000002", "task_id": "t2", "execution_id": "e2", "work_item_id": "wi-nope"},
        {"triage_id": "trg-00000002", "task_id": "t2", "execution_id": "e2", "state": "proposed"},  # 결과 없음
        {"triage_id": "trg-00000002", "task_id": "t2", "execution_id": "e2", "state": "failed"},  # 실패 코드 없음
        {"triage_id": "trg-00000002", "task_id": "t2", "execution_id": "e2", "state": "superseded",
         **proposed, "proceed": "maybe"},
        {"triage_id": "trg-00000002", "task_id": "t2", "execution_id": "e2", "state": "superseded",
         **proposed, "confidence": 1.5},
        {"triage_id": "trg-00000002", "task_id": "t2", "execution_id": "e2", "state": "superseded",
         **proposed, "confidence": -0.1},
        {"triage_id": "trg-00000002", "task_id": "t2", "execution_id": "e2", "state": "failed", "failed_code": "x",
         "handling": "dismissed", "handled_at": NOW},  # 처리는 제안에만
    ):
        with pytest.raises(sqlite3.IntegrityError):
            _insert_triage_log(conn, **overrides)
    conn.execute("UPDATE triage_logs SET state = 'proposed', result_json = '{}', proceed = 'ready', confidence = 0.9,"
                 " proposed_kind = 'bug_fix', finished_at = ?", (NOW,))
    for update in (
        "handling = 'accepted'",  # 처리 시각과 함께
        "handling = 'ignored', handled_at = 'x'",
        "handling = 'accepted', handled_at = 'x'",  # 담당을 정한 처리는 최종 담당이 있어야
        "handling = 'dismissed', handled_at = 'x', final_assignee_type = 'agent'",  # 종류·id 는 함께
        "handling = 'changed', handled_at = 'x', final_assignee_type = 'robot', final_assignee_id = 'a1'",
        "handled_by_member_id = 'mem-nope'",
        "state = 'running', handling = 'dismissed', handled_at = 'x'",
    ):
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(f"UPDATE triage_logs SET {update}")
    conn.execute("UPDATE triage_logs SET handling = 'dismissed', handled_at = ?, handled_by_member_id = 'mem-00000001'",
                 (NOW,))
    conn.execute("UPDATE triage_logs SET handling = 'auto_started', handled_by_member_id = NULL,"
                 " final_assignee_type = 'agent', final_assignee_id = 'a1', final_kind = 'bug_fix'")
    # 업무 하나에 끝난 판단은 여러 행, 도는 판단은 다시 하나 더
    _insert_triage_log(conn, triage_id="trg-00000002", task_id="t2", execution_id="e2", trigger="manual",
                       requested_by_member_id="mem-00000001")
    assert conn.execute("SELECT COUNT(*) FROM triage_logs").fetchone()[0] == 2


def test_v15_triage_autostart_checks(conn):
    _triage_base(conn)
    conn.execute(_AUTOSTART_INSERT, ("s1", "bug_fix", 1, 0, 0.8, None, NOW))
    conn.execute(_AUTOSTART_INSERT, ("s1", "bug_fix", 2, 1, 0.5, "mem-00000001", NOW))
    conn.execute(_AUTOSTART_INSERT, ("s1", "gone_kind", 1, 1, 1.0, None, NOW))  # 종류 FK 없음 — 이력만 남는다
    for params in (
        ("s1", "bug_fix", 2, 0, 0.8, None, NOW),  # 같은 종류 같은 버전
        ("s1", "bug_fix", 0, 0, 0.8, None, NOW),
        ("s1", "bug_fix", 3, 2, 0.8, None, NOW),  # 켜짐은 0·1
        ("s1", "bug_fix", 3, 1, 0.49, None, NOW),  # 기준값 0.5~1
        ("s1", "bug_fix", 3, 1, 1.01, None, NOW),
        ("s-nope", "bug_fix", 1, 1, 0.8, None, NOW),
        ("s1", "bug_fix", 3, 1, 0.8, "mem-nope", NOW),
    ):
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(_AUTOSTART_INSERT, params)


def test_create_session_seeds_triage_kind_and_criteria_v1(conn):
    from workflow.adapters import repo
    from workflow.domain.triage_criteria import TRIAGE_CRITERIA_V1

    repo.create_session(conn, "s1", NOW)
    spec = KindSpec.model_validate_json(
        conn.execute("SELECT spec_json FROM kinds WHERE session_id = 's1' AND kind = 'triage'").fetchone()[0])
    assert spec == next(k for k in BUILTIN_KINDS if k.kind == "triage")
    assert (spec.output_kind, spec.capability_code, spec.builtin) == ("triage_result", "code.triage", True)
    assert spec.outcomes == ["ready", "needs_check", "unsuitable"]
    rows = [tuple(r) for r in conn.execute("SELECT session_id, version, body, created_by_member_id, created_at"
                                           " FROM triage_criteria")]
    assert rows == [("s1", 1, TRIAGE_CRITERIA_V1, None, NOW)]
    assert conn.execute("SELECT COUNT(*) FROM triage_autostart").fetchone()[0] == 0  # 행 없음 = 꺼짐


def _v14_db(db_path):
    """phase 18 서버가 남긴 모양의 v14 DB. 워크스페이스 s1 — 관리자·멤버, GitHub 소스 1, 업무 1(단계 Task 1·실행 1),
    사용자 정의 종류 classify, 로컬 Agent 셋(수정·검토 능력 / 다른 저장소 수정 능력만 / 사용자 정의 능력만)과
    API Agent 하나(code.fix). 워크스페이스 s2 — 종류 없음."""
    c = connect(db_path)
    c.executescript(V14_SCHEMA)
    c.execute("INSERT INTO schema_version (version) VALUES (14)")
    for session_id in ("s1", "s2"):
        c.execute("INSERT INTO sessions (session_id, created_at, is_operator) VALUES (?, ?, 1)", (session_id, NOW))
    for spec in PHASE8_BUILTIN_KINDS:
        c.execute("INSERT INTO kinds (session_id, kind, spec_json, created_at) VALUES ('s1', ?, ?, ?)",
                  (spec.kind, spec.model_dump_json(), NOW))
    _seed_kind(c, "s1", "classify")
    c.execute("INSERT INTO members (member_id, session_id, display_name, role, created_at)"
              " VALUES ('mem-00000001', 's1', '관리자', 'admin', ?)", (NOW,))
    c.execute("INSERT INTO github_sources (source_id, session_id, repository_full_name, config_json, cursor,"
              " created_at, updated_at) VALUES ('ghs-00000001', 's1', 'acme/billing',"
              " '{\"review_agent_id\": \"agt-00000001\"}', ?, ?, ?)", (NOW, NOW, NOW))
    agent = ("INSERT INTO agents (agent_id, name, owner_scope, connection_type, capabilities_json, connection_state)"
             " VALUES (?, ?, 'personal', ?, ?, 'online')")
    c.execute(agent, ("agt-00000001", "billing", "local", json.dumps([
        {"code": "code.fix", "scope": {"repository_id": "billing"}},
        {"code": "code.review", "scope": {"repository_id": "billing"}}])))
    c.execute(agent, ("agt-00000002", "shop", "local", json.dumps([
        {"code": "code.fix", "scope": {"repository_id": "shop"}}])))
    c.execute(agent, ("agt-00000003", "ops", "local", json.dumps([
        {"code": "ops.classify", "scope": {"workflow_id": "w"}}])))
    c.execute(agent, ("agt-00000004", "cloud", "api", json.dumps([
        {"code": "code.fix", "scope": {"repository_id": "billing"}}])))
    c.execute("INSERT INTO work_items (work_item_id, session_id, key_number, title, request, kind, status,"
              " status_reason, source_type, source_id, source_item_id, created_at, updated_at)"
              " VALUES ('wi-000000000003', 's1', 3, '쿠폰 오류', 'r', 'bug_fix', '새로 들어옴', '담당 없음', 'github',"
              " 'ghs-00000001', '123456', ?, ?)", (NOW, NOW))
    _insert_task(c, "t1", "s1", "bug_fix")
    c.execute("UPDATE tasks SET work_item_id = 'wi-000000000003' WHERE task_id = 't1'")
    c.execute("INSERT INTO executions (execution_id, task_id, attempt_no, start_key, agent_id, kind, request_json,"
              " status, created_at) VALUES ('e1', 't1', 1, 'k', 'agt-00000001', 'bug_fix', '{}', 'queued', ?)", (NOW,))
    return c


def _capabilities(c, agent_id: str) -> list[dict]:
    return json.loads(c.execute("SELECT capabilities_json FROM agents WHERE agent_id = ?", (agent_id,)).fetchone()[0])


def test_v14_fixture_is_the_phase18_schema(db_path):
    c = _v14_db(db_path)
    assert c.execute("SELECT version FROM schema_version").fetchone()[0] == 14
    assert _table_names(c) - {"sqlite_sequence"} == V14_TABLES | {"schema_version"}
    assert not PHASE19_TABLES & _table_names(c)
    c.close()


def test_migrates_v14_to_v15_seeding_triage_and_preserving_rows(db_path):
    from workflow.domain.triage_criteria import TRIAGE_CRITERIA_V1

    c = _v14_db(db_path)
    columns = _column_lists(c, V14_TABLES)
    before = _dump(c, V14_TABLES)
    recreated = "SELECT name, sql, rootpage FROM sqlite_master WHERE name IN ('tasks', 'github_sources') ORDER BY name"
    sql_before = [tuple(r) for r in c.execute(recreated)]
    c.close()

    c = connect(db_path)
    init_schema(c)
    assert [tuple(r) for r in c.execute("SELECT version FROM schema_version")] == [(SCHEMA_VERSION,)]
    assert _column_lists(c, V14_TABLES) == columns  # 칸·순서 그대로
    assert _without_v15_seeds(_dump(c, V14_TABLES)) == before  # 업무·단계·실행·Agent 그대로
    assert [tuple(r) for r in c.execute(recreated)] == sql_before  # tasks·github_sources 는 재생성하지 않는다
    # 내장 triage 종류 — 워크스페이스마다(종류가 없던 s2 도)
    triage = next(k for k in BUILTIN_KINDS if k.kind == "triage")
    rows = c.execute("SELECT session_id, spec_json FROM kinds WHERE kind = 'triage' ORDER BY session_id").fetchall()
    assert [(r[0], KindSpec.model_validate_json(r[1])) for r in rows] == [("s1", triage), ("s2", triage)]
    # 판단 기준 v1 — 워크스페이스마다, 시드는 멤버 없음
    assert [tuple(r)[:4] for r in c.execute(
        "SELECT session_id, version, body, created_by_member_id FROM triage_criteria ORDER BY session_id")] == [
        ("s1", 1, TRIAGE_CRITERIA_V1, None), ("s2", 1, TRIAGE_CRITERIA_V1, None)]
    assert c.execute("SELECT COUNT(*) FROM triage_logs").fetchone()[0] == 0
    assert c.execute("SELECT COUNT(*) FROM triage_autostart").fetchone()[0] == 0
    # code.fix 가 있는 로컬 Agent 에만, 그 범위 그대로 끝에
    assert _capabilities(c, "agt-00000001") == [
        {"code": "code.fix", "scope": {"repository_id": "billing"}},
        {"code": "code.review", "scope": {"repository_id": "billing"}},
        {"code": "code.triage", "scope": {"repository_id": "billing"}}]
    assert _capabilities(c, "agt-00000002") == [
        {"code": "code.fix", "scope": {"repository_id": "shop"}},
        {"code": "code.triage", "scope": {"repository_id": "shop"}}]
    assert _capabilities(c, "agt-00000003") == [{"code": "ops.classify", "scope": {"workflow_id": "w"}}]
    assert _capabilities(c, "agt-00000004") == [{"code": "code.fix", "scope": {"repository_id": "billing"}}]
    assert c.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert c.execute("PRAGMA foreign_key_check").fetchall() == []
    assert c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"

    after = _dump(c, TABLES)
    init_schema(c)  # 재실행은 아무것도 바꾸지 않는다
    assert _dump(c, TABLES) == after
    c.close()


def test_v15_capability_seed_is_not_duplicated(db_path):
    c = _v14_db(db_path)
    c.execute("UPDATE agents SET capabilities_json = ? WHERE agent_id = 'agt-00000002'", (json.dumps([
        {"code": "code.fix", "scope": {"repository_id": "shop"}},
        {"code": "code.triage", "scope": {"repository_id": "shop"}}]),))
    init_schema(c)
    assert [cap["code"] for cap in _capabilities(c, "agt-00000002")] == ["code.fix", "code.triage"]
    c.close()


def test_v15_migration_aborts_when_a_user_kind_is_named_triage(db_path):
    c = _v14_db(db_path)
    _seed_kind(c, "s2", "triage")
    before = _dump(c, V14_TABLES)
    with pytest.raises(RuntimeError, match="s2:triage"):
        init_schema(c)
    assert c.execute("SELECT version FROM schema_version").fetchone()[0] == 14
    assert not PHASE19_TABLES & _table_names(c)
    assert _dump(c, V14_TABLES) == before
    assert c.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    c.close()


def test_v15_migration_rolls_back_on_foreign_key_violation(db_path):
    c = _v14_db(db_path)
    c.execute("PRAGMA foreign_keys=OFF")
    c.execute("UPDATE tasks SET work_item_id = 'wi-nope' WHERE task_id = 't1'")  # 외래키 검사가 잡을 옛 결함
    c.execute("PRAGMA foreign_keys=ON")
    before = _dump(c, V14_TABLES)
    with pytest.raises(RuntimeError, match="외래키"):
        init_schema(c)
    assert c.execute("SELECT version FROM schema_version").fetchone()[0] == 14
    assert not PHASE19_TABLES & _table_names(c)
    assert _dump(c, V14_TABLES) == before
    assert c.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    c.close()


@pytest.mark.parametrize("old", ["v13", "v14"])
def test_fresh_schema_matches_v14_migrated_schema(tmp_path, old):
    fresh = connect(tmp_path / "fresh.sqlite")
    init_schema(fresh)
    migrated = (_v13_db if old == "v13" else _v14_db)(tmp_path / "old.sqlite")
    init_schema(migrated)
    assert _table_names(fresh) == _table_names(migrated)
    for table in TABLES:
        cols = "SELECT name, type, \"notnull\", dflt_value, pk FROM pragma_table_info(?) ORDER BY cid"
        assert fresh.execute(cols, (table,)).fetchall() == migrated.execute(cols, (table,)).fetchall(), table
        assert _foreign_keys(fresh, table) == _foreign_keys(migrated, table), table
    assert _indexes(fresh) == _indexes(migrated)
    sql = "SELECT name, sql FROM sqlite_master WHERE name LIKE '%triage%' ORDER BY name"
    assert fresh.execute(sql).fetchall() == migrated.execute(sql).fetchall()  # CHECK 까지 같은 원문
    fresh.close()
    migrated.close()


@pytest.mark.parametrize("make", [_v4_db, _v9_db, _v13_db])
def test_migrates_old_versions_to_v15_with_triage_seeds(db_path, make):
    c = make(db_path)
    c.close()
    c = connect(db_path)
    init_schema(c)
    assert c.execute("SELECT version FROM schema_version").fetchone()[0] == 16
    sessions = [r[0] for r in c.execute("SELECT session_id FROM sessions ORDER BY session_id")]
    assert sessions
    assert [r[0] for r in c.execute("SELECT session_id FROM kinds WHERE kind = 'triage' ORDER BY session_id")] == sessions
    assert [tuple(r) for r in c.execute("SELECT session_id, version FROM triage_criteria ORDER BY session_id")] == [
        (s, 1) for s in sessions]
    for (raw,) in c.execute("SELECT capabilities_json FROM agents WHERE connection_type = 'local'"):
        caps = json.loads(raw)
        fixes = [cap["scope"] for cap in caps if cap["code"] == "code.fix"]
        assert [cap["scope"] for cap in caps if cap["code"] == "code.triage"] == fixes
    assert c.execute("PRAGMA foreign_key_check").fetchall() == []
    assert c.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    c.close()


# --- phase 20: v15 → v16 설정 변경 기록 표 (ADR-0026, ARCHITECTURE "스키마 v16") ---------------------------------------

V15_SCHEMA = (Path(__file__).parent / "fixtures" / "schema_v15.sql").read_text()
CONFIG_CHANGE_COLUMNS = ["id", "session_id", "revision", "area", "action", "subject", "by_member_id", "occurred_at"]
_CHANGE_INSERT = ("INSERT INTO config_changes (session_id, revision, area, action, subject, by_member_id, occurred_at)"
                  " VALUES (?, ?, ?, ?, ?, ?, ?)")


def test_v16_constants():
    from workflow.adapters import db

    assert db.CONFIG_CHANGE_AREAS == ("kind", "rule", "source", "mapping", "triage_criteria", "triage_autostart")
    assert db.CONFIG_CHANGE_ACTIONS == ("add", "delete", "change")


def test_fresh_db_has_v16_config_changes_table_keys_and_index(conn):
    assert PHASE20_TABLES <= _table_names(conn)
    assert _column_lists(conn, ["config_changes"]) == {"config_changes": CONFIG_CHANGE_COLUMNS}
    assert _foreign_keys(conn, "config_changes") == {
        ("sessions", "session_id", "session_id"), ("members", "by_member_id", "member_id")}
    unique = [r for r in conn.execute("PRAGMA index_list(config_changes)") if r["unique"]]
    assert [[c["name"] for c in conn.execute(f"PRAGMA index_info({r['name']})")] for r in unique] == [
        ["session_id", "revision"]]


def test_v16_config_change_checks(conn):
    _v11_base(conn)
    conn.execute(_CHANGE_INSERT, ("s1", 2, "kind", "add", "classify", "mem-00000001", NOW))
    conn.execute(_CHANGE_INSERT, ("s1", 3, "triage_criteria", "change", "v2", None, NOW))  # 멤버 없는 경로
    conn.execute(_CHANGE_INSERT, ("s2", 2, "rule", "delete", "bug_fix→code_review", None, NOW))  # 세션마다 번호
    for params in (
        ("s1", 4, "agent", "add", "x", None, NOW),  # 모르는 영역
        ("s1", 4, "kind", "rename", "x", None, NOW),  # 모르는 동작
        ("s1", 4, "kind", "add", "", None, NOW),  # 빈 이름
        ("s1", 4, "kind", "add", "x" * 201, None, NOW),  # 200자 초과
        ("s1", 0, "kind", "add", "x", None, NOW),  # 번호는 1 이상
        ("s1", 2, "kind", "delete", "classify", None, NOW),  # 같은 번호 두 행
        ("s1", 4, "kind", "add", "x", "mem-nope", NOW),  # 없는 멤버
        ("s-nope", 2, "kind", "add", "x", None, NOW),  # 없는 워크스페이스
        ("s1", 4, None, "add", "x", None, NOW),
        ("s1", 4, "kind", "add", "x", None, None),
    ):
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(_CHANGE_INSERT, params)
    conn.execute(_CHANGE_INSERT, ("s1", 4, "kind", "add", "x" * 200, None, NOW))


def _v15_db(db_path):
    """phase 19 서버가 남긴 모양의 v15 DB — v14 모양 행(워크스페이스 s1·s2, 업무·단계·실행·Agent)에 내장 triage 종류·
    기준 v1·판단 로그 하나·자동 시작 한 행과 설정 번호 3."""
    from workflow.domain.triage_criteria import TRIAGE_CRITERIA_V1

    c = connect(db_path)
    c.executescript(V15_SCHEMA)
    c.execute("INSERT INTO schema_version (version) VALUES (15)")
    for session_id in ("s1", "s2"):
        c.execute("INSERT INTO sessions (session_id, created_at, is_operator) VALUES (?, ?, 1)", (session_id, NOW))
        for spec in BUILTIN_KINDS:
            c.execute("INSERT INTO kinds (session_id, kind, spec_json, created_at) VALUES (?, ?, ?, ?)",
                      (session_id, spec.kind, spec.model_dump_json(), NOW))
        c.execute(_CRITERIA_INSERT, (session_id, 1, TRIAGE_CRITERIA_V1, None, NOW))
    c.execute("UPDATE sessions SET config_revision = 3 WHERE session_id = 's1'")
    c.execute("INSERT INTO members (member_id, session_id, display_name, role, created_at)"
              " VALUES ('mem-00000001', 's1', '관리자', 'admin', ?)", (NOW,))
    c.execute("INSERT INTO agents (agent_id, name, owner_scope, connection_type, capabilities_json, connection_state)"
              " VALUES ('agt-00000001', 'billing', 'personal', 'local', ?, 'online')", (json.dumps([
                  {"code": "code.fix", "scope": {"repository_id": "billing"}},
                  {"code": "code.triage", "scope": {"repository_id": "billing"}}]),))
    c.execute("INSERT INTO work_items (work_item_id, session_id, key_number, title, request, kind, status,"
              " status_reason, source_type, created_at, updated_at) VALUES ('wi-000000000001', 's1', 1, '쿠폰 오류',"
              " 'r', 'bug_fix', '새로 들어옴', '판단 중', 'manual', ?, ?)", (NOW, NOW))
    _insert_task(c, "t1", "s1", "triage")
    c.execute("UPDATE tasks SET work_item_id = 'wi-000000000001' WHERE task_id = 't1'")
    c.execute("INSERT INTO executions (execution_id, task_id, attempt_no, start_key, agent_id, kind, request_json,"
              " status, created_at) VALUES ('e1', 't1', 1, 'k', 'agt-00000001', 'triage', '{}', 'queued', ?)", (NOW,))
    _insert_triage_log(c, agent_id="agt-00000001")
    c.execute(_AUTOSTART_INSERT, ("s1", "bug_fix", 1, 1, 0.8, "mem-00000001", NOW))
    return c


def test_v15_fixture_is_the_phase19_schema(db_path):
    c = _v15_db(db_path)
    assert c.execute("SELECT version FROM schema_version").fetchone()[0] == 15
    assert _table_names(c) - {"sqlite_sequence"} == V15_TABLES | {"schema_version"}
    assert not PHASE20_TABLES & _table_names(c)
    c.close()


def test_migrates_v15_to_v16_adding_an_empty_config_changes_table(db_path):
    c = _v15_db(db_path)
    columns = _column_lists(c, V15_TABLES)
    before = _dump(c, V15_TABLES)
    sql_before = [tuple(r) for r in c.execute("SELECT type, name, sql, rootpage FROM sqlite_master ORDER BY name")]
    c.close()

    c = connect(db_path)
    init_schema(c)
    assert [tuple(r) for r in c.execute("SELECT version FROM schema_version")] == [(SCHEMA_VERSION,)] == [(16,)]
    assert _column_lists(c, V15_TABLES) == columns
    assert _dump(c, V15_TABLES) == before  # 행 그대로 — 설정 번호도
    sql_after = [tuple(r) for r in c.execute("SELECT type, name, sql, rootpage FROM sqlite_master ORDER BY name")]
    assert [r for r in sql_after if "config_changes" not in r[1]] == sql_before  # 기존 표는 재생성하지 않는다
    assert c.execute("SELECT COUNT(*) FROM config_changes").fetchone()[0] == 0  # 과거 변경을 추정해 채우지 않는다
    assert c.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert c.execute("PRAGMA foreign_key_check").fetchall() == []
    assert c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"

    c.execute(_CHANGE_INSERT, ("s1", 4, "kind", "add", "classify", "mem-00000001", NOW))
    after = _dump(c, TABLES)
    init_schema(c)  # 재실행은 아무것도 바꾸지 않는다
    assert _dump(c, TABLES) == after
    c.close()


def test_v16_migration_rolls_back_on_foreign_key_violation(db_path):
    c = _v15_db(db_path)
    c.execute("PRAGMA foreign_keys=OFF")
    c.execute("UPDATE tasks SET work_item_id = 'wi-nope' WHERE task_id = 't1'")  # 외래키 검사가 잡을 옛 결함
    c.execute("PRAGMA foreign_keys=ON")
    before = _dump(c, V15_TABLES)
    with pytest.raises(RuntimeError, match="외래키"):
        init_schema(c)
    assert c.execute("SELECT version FROM schema_version").fetchone()[0] == 15
    assert not PHASE20_TABLES & _table_names(c)
    assert _dump(c, V15_TABLES) == before
    assert c.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    c.close()


@pytest.mark.parametrize("old", ["v14", "v15"])
def test_fresh_schema_matches_v15_migrated_schema(tmp_path, old):
    fresh = connect(tmp_path / "fresh.sqlite")
    init_schema(fresh)
    migrated = (_v14_db if old == "v14" else _v15_db)(tmp_path / "old.sqlite")
    init_schema(migrated)
    assert _table_names(fresh) == _table_names(migrated)
    for table in TABLES:
        cols = "SELECT name, type, \"notnull\", dflt_value, pk FROM pragma_table_info(?) ORDER BY cid"
        assert fresh.execute(cols, (table,)).fetchall() == migrated.execute(cols, (table,)).fetchall(), table
        assert _foreign_keys(fresh, table) == _foreign_keys(migrated, table), table
    assert _indexes(fresh) == _indexes(migrated)
    sql = "SELECT name, sql FROM sqlite_master WHERE name LIKE '%config_changes%' ORDER BY name"
    assert fresh.execute(sql).fetchall() == migrated.execute(sql).fetchall()  # CHECK 까지 같은 원문
    fresh.close()
    migrated.close()


@pytest.mark.parametrize("make", [_v4_db, _v9_db, _v14_db])
def test_migrates_old_versions_to_v16_with_empty_config_changes(db_path, make):
    c = make(db_path)
    c.close()
    c = connect(db_path)
    init_schema(c)
    assert c.execute("SELECT version FROM schema_version").fetchone()[0] == 16
    assert TABLES <= _table_names(c)
    assert c.execute("SELECT COUNT(*) FROM config_changes").fetchone()[0] == 0
    assert c.execute("PRAGMA foreign_key_check").fetchall() == []
    assert c.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    c.close()
