"""백업·복원 CLI (ADR-0016 결정 7, ARCHITECTURE "백업·복원 CLI"). 실제 임시 DB·산출물 디렉터리로 돈다."""

import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path

import pytest

from workflow.adapters.db import SCHEMA_VERSION, connect, init_schema
from workflow.server import backup

T1 = datetime(2026, 9, 27, 1, 0, 0, tzinfo=UTC)
T2 = datetime(2026, 9, 27, 2, 0, 0, tzinfo=UTC)
T3 = datetime(2026, 9, 27, 3, 0, 0, tzinfo=UTC)


def _seed(db_path: Path, artifact_dir: Path, sessions=("sess-a", "sess-b")) -> None:
    conn = connect(db_path)
    init_schema(conn)
    for sid in sessions:
        conn.execute("INSERT INTO sessions (session_id, created_at) VALUES (?, ?)", (sid, "2026-09-27T00:00:00Z"))
    conn.close()
    (artifact_dir / "sess-a").mkdir(parents=True, exist_ok=True)
    (artifact_dir / "sess-a" / "report.md").write_text("보고서 본문\n", encoding="utf-8")
    (artifact_dir / "top.json").write_text('{"a": 1}', encoding="utf-8")


def _sessions(db_path: Path) -> list[str]:
    conn = connect(db_path)
    try:
        return [r[0] for r in conn.execute("SELECT session_id FROM sessions ORDER BY session_id")]
    finally:
        conn.close()


def _files(root: Path) -> dict[str, bytes]:
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def _env(base: Path) -> dict[str, str]:
    return {
        "WORKFLOW_DB_PATH": str(base / "central.sqlite"),
        "WORKFLOW_ARTIFACT_DIR": str(base / "artifacts"),
        "WORKFLOW_BACKUP_DIR": str(base / "backups"),
    }


@pytest.fixture
def src(tmp_path) -> Path:
    base = tmp_path / "src"
    base.mkdir()
    _seed(base / "central.sqlite", base / "artifacts")
    return base


def test_create_list_restore_to_other_location_round_trips(src, tmp_path, capsys):
    env = _env(src)
    assert backup.main(["create"], env=env, now=lambda: T1) == 0
    out = capsys.readouterr().out.strip().splitlines()
    assert out[-1] == "20260927T010000Z"
    made = src / "backups" / "20260927T010000Z"
    assert sorted(p.name for p in made.iterdir()) == ["artifacts.tar.gz", "central.sqlite"]

    assert backup.main(["list"], env=env) == 0
    line = capsys.readouterr().out.strip().splitlines()[0]
    assert line.startswith("20260927T010000Z")
    assert "2026-09-27T01:00:00Z" in line
    assert f"schema {SCHEMA_VERSION}" in line

    other = tmp_path / "other"
    other_env = {**_env(other), "WORKFLOW_BACKUP_DIR": env["WORKFLOW_BACKUP_DIR"]}
    assert backup.main(["restore", "20260927T010000Z"], env=other_env) == 0
    assert _sessions(other / "central.sqlite") == ["sess-a", "sess-b"]
    assert _files(other / "artifacts") == _files(src / "artifacts")


def test_backup_does_not_include_env_or_token_files(src, capsys):
    (src / ".env").write_text("OPERATOR_TOKEN=secret-value\n")
    backup.main(["create"], env=_env(src), now=lambda: T1)
    made = src / "backups" / "20260927T010000Z"
    import tarfile

    with tarfile.open(made / "artifacts.tar.gz") as tar:
        names = tar.getnames()
    assert all(".env" not in n for n in names)
    assert not any(b"secret-value" in p.read_bytes() for p in made.iterdir())


def test_backup_does_not_include_the_secret_directory(src, capsys):
    """ADR-0017 — 비밀 저장소(WORKFLOW_SECRET_DIR)는 백업에 들어가지 않는다. 볼륨 안 artifacts 옆에 있어도."""
    from workflow.adapters.secret_store import GITHUB_APP_PRIVATE_KEY, SecretStore

    SecretStore(src / "secrets").write(GITHUB_APP_PRIVATE_KEY, "PEM-SECRET-VALUE")
    backup.main(["create"], env={**_env(src), "WORKFLOW_SECRET_DIR": str(src / "secrets")}, now=lambda: T1)
    made = src / "backups" / "20260927T010000Z"
    import tarfile

    with tarfile.open(made / "artifacts.tar.gz") as tar:
        names = tar.getnames()
        blobs = [tar.extractfile(m).read() for m in tar.getmembers() if m.isfile()]
    assert all("secrets" not in n and GITHUB_APP_PRIVATE_KEY not in n for n in names)
    assert not any(b"PEM-SECRET-VALUE" in b for b in blobs)
    assert not any(b"PEM-SECRET-VALUE" in p.read_bytes() for p in made.iterdir())


def test_create_includes_rows_still_in_wal_and_survives_concurrent_writes(src):
    db_path = src / "central.sqlite"
    holder = connect(db_path)
    holder.execute("PRAGMA wal_autocheckpoint=0")
    holder.execute("INSERT INTO sessions (session_id, created_at) VALUES ('sess-wal', 'x')")
    assert (src / "central.sqlite-wal").stat().st_size > 0

    stop = threading.Event()
    written = []

    def writer():
        c = connect(db_path)
        i = 0
        while not stop.is_set():
            c.execute("INSERT INTO sessions (session_id, created_at) VALUES (?, 'x')", (f"sess-w{i}",))
            written.append(i)
            i += 1
        c.close()

    thread = threading.Thread(target=writer)
    thread.start()
    try:
        name = backup.create_backup(backup.Paths.from_env(_env(src)), now=T1)
    finally:
        stop.set()
        thread.join()
        holder.close()

    copied = sqlite3.connect(src / "backups" / name / "central.sqlite")
    try:
        assert copied.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        ids = {r[0] for r in copied.execute("SELECT session_id FROM sessions")}
    finally:
        copied.close()
    assert {"sess-a", "sess-b", "sess-wal"} <= ids
    assert written  # 백업 중에도 쓰기가 막히지 않았다


def test_keep_removes_oldest_backups(src, capsys):
    env = _env(src)
    for t in (T1, T2, T3):
        assert backup.main(["create", "--keep", "2"], env=env, now=lambda t=t: t) == 0
    names = sorted(p.name for p in (src / "backups").iterdir())
    assert names == ["20260927T020000Z", "20260927T030000Z"]


def test_dest_overrides_backup_dir(src, tmp_path):
    dest = tmp_path / "elsewhere"
    assert backup.main(["create", "--dest", str(dest)], env=_env(src), now=lambda: T1) == 0
    assert (dest / "20260927T010000Z" / "central.sqlite").is_file()
    assert not (src / "backups").exists()


def test_restore_refuses_existing_db_without_force(src, capsys):
    env = _env(src)
    backup.main(["create"], env=env, now=lambda: T1)
    conn = connect(src / "central.sqlite")
    conn.execute("INSERT INTO sessions (session_id, created_at) VALUES ('sess-new', 'x')")
    conn.close()

    assert backup.main(["restore", "20260927T010000Z"], env=env) == 1
    assert "--force" in capsys.readouterr().err
    assert "sess-new" in _sessions(src / "central.sqlite")
    assert [p.name for p in (src / "backups").iterdir()] == ["20260927T010000Z"]


def test_restore_with_force_backs_up_current_state_first(src, capsys):
    env = _env(src)
    backup.main(["create"], env=env, now=lambda: T1)
    conn = connect(src / "central.sqlite")
    conn.execute("INSERT INTO sessions (session_id, created_at) VALUES ('sess-new', 'x')")
    conn.close()
    (src / "artifacts" / "later.txt").write_text("나중")

    assert backup.main(["restore", "20260927T010000Z", "--force"], env=env, now=lambda: T2) == 0
    assert _sessions(src / "central.sqlite") == ["sess-a", "sess-b"]
    assert not (src / "artifacts" / "later.txt").exists()

    pre = src / "backups" / "pre-restore-20260927T020000Z"
    assert "sess-new" in _sessions(pre / "central.sqlite")
    # 복원 전 백업도 list 에 나오고 다시 복원할 수 있다
    capsys.readouterr()
    backup.main(["list"], env=env)
    assert capsys.readouterr().out.splitlines()[0].startswith("pre-restore-20260927T020000Z")
    assert backup.main(["restore", "pre-restore-20260927T020000Z", "--force"], env=env, now=lambda: T3) == 0
    assert "sess-new" in _sessions(src / "central.sqlite")
    assert (src / "artifacts" / "later.txt").read_text() == "나중"


def test_restore_unknown_name_exits_2_and_changes_nothing(src, capsys):
    env = _env(src)
    assert backup.main(["restore", "20990101T000000Z", "--force"], env=env) == 2
    assert backup.main(["restore", "../src", "--force"], env=env) == 2
    assert _sessions(src / "central.sqlite") == ["sess-a", "sess-b"]
    assert not (src / "backups").exists()


def test_restore_rejects_corrupt_db_backup(src, capsys):
    env = _env(src)
    backup.main(["create"], env=env, now=lambda: T1)
    (src / "backups" / "20260927T010000Z" / "central.sqlite").write_bytes(b"not a database" * 100)

    assert backup.main(["restore", "20260927T010000Z", "--force"], env=env, now=lambda: T2) == 1
    assert "손상" in capsys.readouterr().err
    assert _sessions(src / "central.sqlite") == ["sess-a", "sess-b"]
    assert not (src / "backups" / "pre-restore-20260927T020000Z").exists()


def test_restore_rejects_corrupt_artifact_archive(src, capsys):
    env = _env(src)
    backup.main(["create"], env=env, now=lambda: T1)
    (src / "backups" / "20260927T010000Z" / "artifacts.tar.gz").write_bytes(b"broken")

    assert backup.main(["restore", "20260927T010000Z", "--force"], env=env, now=lambda: T2) == 1
    assert "손상" in capsys.readouterr().err
    assert (src / "artifacts" / "top.json").exists()


def test_restored_db_passes_init_schema(src, tmp_path):
    env = _env(src)
    backup.main(["create"], env=env, now=lambda: T1)
    other = tmp_path / "other"
    backup.main(["restore", "20260927T010000Z"], env={**_env(other), "WORKFLOW_BACKUP_DIR": env["WORKFLOW_BACKUP_DIR"]})
    conn = connect(other / "central.sqlite")
    try:
        init_schema(conn)
        assert conn.execute("SELECT version FROM schema_version").fetchone()[0] == SCHEMA_VERSION
    finally:
        conn.close()


def test_create_without_db_fails(tmp_path, capsys):
    assert backup.main(["create"], env=_env(tmp_path), now=lambda: T1) == 1
    assert not (tmp_path / "central.sqlite").exists()


def test_help_says_stop_services_before_restore(capsys):
    with pytest.raises(SystemExit):
        backup.main(["restore", "--help"], env={})
    assert "멈춘" in capsys.readouterr().out


# --- phase 13·14·15: 셀프호스트 모양 v8 DB 사본 → v11 + 백업 왕복 (ADR-0019·0020, SELFHOST "업그레이드") -----------

V8_NOW = "2026-09-28T00:00:00Z"
LEGACY_KINDS = ("diagnosis", "code_change")
V8_SESSION = "sess-selfhost"
V9_SCHEMA = (Path(__file__).parents[2] / "workflow" / "adapters" / "fixtures" / "schema_v9.sql").read_text()


def _v8_selfhost(db_path: Path, artifact_dir: Path) -> None:
    """phase 12 셀프호스트가 남긴 모양 — 워크스페이스 하나(운영자), 옛 내장 4종류·규칙 2개, GitHub 순환 행.
    v8 과 v9 는 표 구조가 같아 고정한 v9 스키마 원문에 옛 행을 넣고 버전을 8 로 둔다."""
    from workflow.contracts.v1 import BUILTIN_KINDS, BUILTIN_RULES

    conn = connect(db_path)
    conn.executescript(V9_SCHEMA)
    conn.execute("INSERT INTO schema_version (version) VALUES (8)")
    conn.execute("INSERT INTO sessions (session_id, created_at, is_operator) VALUES (?, ?, 1)", (V8_SESSION, V8_NOW))
    for spec in BUILTIN_KINDS[:2]:  # v14 까지는 bug_fix·code_review 만 (triage 는 v15)
        conn.execute("INSERT INTO kinds (session_id, kind, spec_json, created_at) VALUES (?, ?, ?, ?)",
                     (V8_SESSION, spec.kind, spec.model_dump_json(), V8_NOW))
    conn.execute("INSERT INTO succession_rules (rule_id, session_id, from_kind, to_kind, rule_json, created_at)"
                 " VALUES ('rule-builtin', ?, 'bug_fix', 'code_review', ?, ?)",
                 (V8_SESSION, BUILTIN_RULES[0].model_dump_json(), V8_NOW))
    for kind in LEGACY_KINDS:
        conn.execute("INSERT INTO kinds (session_id, kind, spec_json, created_at) VALUES (?, ?, '{}', ?)",
                     (V8_SESSION, kind, V8_NOW))
    conn.execute("INSERT INTO succession_rules (rule_id, session_id, from_kind, to_kind, rule_json, created_at)"
                 " VALUES ('rule-legacy', ?, 'diagnosis', 'code_change', '{}', ?)", (V8_SESSION, V8_NOW))
    conn.execute("INSERT INTO agents (agent_id, name, owner_scope, connection_type, capabilities_json,"
                 " connection_state) VALUES ('agent-codex-mac', 'n', 'personal', 'local', '[]', 'online')")
    conn.execute("INSERT INTO github_sources (source_id, session_id, repository_full_name, config_json,"
                 " created_at, updated_at) VALUES ('ghs-00000001', ?, 'acme/billing', '{}', ?, ?)",
                 (V8_SESSION, V8_NOW, V8_NOW))
    for n in range(1, 4):
        for task_id, kind in ((f"t-fix-{n}", "bug_fix"), (f"t-review-{n}", "code_review")):
            conn.execute(
                "INSERT INTO tasks (task_id, session_id, title, request, kind, required_capability_json,"
                " selection_mode, run_mode, completion_mode, criteria_json, revision, target_json,"
                " status, status_reason, created_at) VALUES (?, ?, 't', 'r', ?, '{}', 'auto', 'manual',"
                " 'review', '[]', 1, '{}', '완료', '완료', ?)", (task_id, V8_SESSION, kind, V8_NOW))
        conn.execute(
            "INSERT INTO source_issues (source_id, github_issue_id, issue_number, task_id, source_revision,"
            " snapshot_json, snapshot_digest, issue_updated_at, state, created_at, updated_at)"
            " VALUES ('ghs-00000001', ?, ?, ?, 1, '{}', 'd', ?, 'open', ?, ?)",
            (1000 + n, n, f"t-fix-{n}", V8_NOW, V8_NOW, V8_NOW))
        conn.execute(
            "INSERT INTO baseline_items (source_id, issue_number, issue_title, issue_opened_at, pr_number,"
            " pr_merged_at, fetched_at) VALUES ('ghs-00000001', ?, 'i', ?, ?, ?, ?)",
            (100 + n, V8_NOW, 200 + n, V8_NOW, V8_NOW))
        conn.execute(
            "INSERT INTO task_pull_requests (task_id, session_id, source_id, repository_full_name, issue_number,"
            " head_branch, fix_execution_id, review_execution_id, state, pr_number, created_at, updated_at)"
            " VALUES (?, ?, 'ghs-00000001', 'acme/billing', ?, ?, 'e-f', 'e-r', 'open', ?, ?, ?)",
            (f"t-fix-{n}", V8_SESSION, n, f"task/t-fix-{n}", 10 + n, V8_NOW, V8_NOW))
        conn.execute(
            "INSERT INTO notifications (notification_id, session_id, event, task_id, dedupe_key, content,"
            " payload_json, state, created_at) VALUES (?, ?, 'pr_opened', ?, ?, 'c', '{}', 'sent', ?)",
            (f"ntf-0000000{n}", V8_SESSION, f"t-fix-{n}", f"pr_opened:t-fix-{n}", V8_NOW))
    conn.close()
    (artifact_dir / V8_SESSION).mkdir(parents=True, exist_ok=True)
    (artifact_dir / V8_SESSION / "diff.patch").write_text("--- a\n+++ b\n", encoding="utf-8")


def _counts(db_path: Path) -> dict[str, int]:
    conn = sqlite3.connect(db_path)
    try:
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name != 'schema_version' ORDER BY name")]
        return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables}
    finally:
        conn.close()


# v14 가 더하는 빈 Jira 표 4개 (phase 18)
JIRA_EMPTY = {"jira_connections": 0, "jira_projects": 0, "jira_issues": 0, "jira_deliveries": 0}
# v15 가 더하는 판단 표 3개 — 기준은 워크스페이스마다 v1 한 행 (phase 19)
TRIAGE_ONE_WORKSPACE = {"triage_criteria": 1, "triage_logs": 0, "triage_autostart": 0}
# v16 이 더하는 빈 설정 변경 기록 표 (phase 20) — 과거 변경은 채우지 않는다
CONFIG_CHANGES_EMPTY = {"config_changes": 0}


def _version_and_kinds(db_path: Path) -> tuple[int, list[str]]:
    conn = sqlite3.connect(db_path)
    try:
        version = conn.execute("SELECT version FROM schema_version").fetchone()[0]
        kinds = [r[0] for r in conn.execute("SELECT kind FROM kinds WHERE session_id = ? ORDER BY kind", (V8_SESSION,))]
        return version, kinds
    finally:
        conn.close()


def test_selfhost_v8_copy_upgrades_to_v11_and_backups_round_trip(tmp_path, capsys):
    src = tmp_path / "src"
    src.mkdir()
    env = _env(src)
    _v8_selfhost(src / "central.sqlite", src / "artifacts")
    v8_counts = _counts(src / "central.sqlite")
    assert v8_counts["tasks"] == 6 and v8_counts["kinds"] == 4 and v8_counts["succession_rules"] == 2

    # 1) 업그레이드 전에 백업한다 — 목록에 스키마 8 로 나온다
    assert backup.main(["create"], env=env, now=lambda: T1) == 0
    v8_backup = capsys.readouterr().out.strip()
    assert backup.main(["list"], env=env) == 0
    assert capsys.readouterr().out.strip().endswith("schema 8")

    # 2) v11 로 올린다 — 진단 두 종류·그 규칙만 사라지고 나머지 행 수는 그대로, 이슈마다 수정·검토가 각자 업무
    #    (후속 연결 없음), 첫 관리자·기본 매핑 둘(github·jira)
    conn = connect(src / "central.sqlite")
    init_schema(conn)
    conn.close()
    v9_counts = _counts(src / "central.sqlite")
    assert v9_counts == {
        **v8_counts, "kinds": 3, "succession_rules": 1,
        "work_items": 6, "work_item_links": 0, "members": 1, "field_mappings": 2, "work_item_events": 0,
        "login_sessions": 0, "member_invites": 0, "work_pull_requests": 0, **JIRA_EMPTY, **TRIAGE_ONE_WORKSPACE, **CONFIG_CHANGES_EMPTY, "responsibilities": 0, "internal_requests": 0, "internal_request_investigations": 0, "internal_request_rejections": 0, "internal_request_questions": 0, "internal_request_judgments": 0, "internal_request_resumptions": 0,
    }
    assert _version_and_kinds(src / "central.sqlite") == (SCHEMA_VERSION, ["bug_fix", "code_review", "triage"])

    # 3) v9 백업 → 다른 위치로 복원: 행·산출물이 그대로
    assert backup.main(["create"], env=env, now=lambda: T2) == 0
    v9_backup = capsys.readouterr().out.strip()
    dst = tmp_path / "dst"
    dst_env = {**_env(dst), "WORKFLOW_BACKUP_DIR": env["WORKFLOW_BACKUP_DIR"]}
    assert backup.main(["restore", v9_backup], env=dst_env) == 0
    assert _counts(dst / "central.sqlite") == v9_counts
    assert _files(dst / "artifacts") == _files(src / "artifacts")

    # 4) 업그레이드 전 v8 백업으로 되돌려도 복원이 v9 로 올린다 — 같은 결과
    old = tmp_path / "old"
    old_env = {**_env(old), "WORKFLOW_BACKUP_DIR": env["WORKFLOW_BACKUP_DIR"]}
    assert backup.main(["restore", v8_backup], env=old_env) == 0
    assert _counts(old / "central.sqlite") == v9_counts
    assert _version_and_kinds(old / "central.sqlite") == (SCHEMA_VERSION, ["bug_fix", "code_review", "triage"])


V9_NOW = "2026-09-29T00:00:00Z"
V9_SESSION = "sess-selfhost"
ARCHIVE, SANDBOX = "ghs-00000001", "ghs-00000002"
UNDELEGATED = range(1, 18)  # 수집만 되고 지시 전인 이슈 17건
MERGED = (18, 19, 20)       # 수정 + 검토 + PR 병합


def _t9(n: int) -> str:
    return f"2026-09-29T{n // 60:02d}:{n % 60:02d}:00Z"


def _v9_selfhost(db_path: Path, artifact_dir: Path) -> None:
    """phase 13 셀프호스트가 남긴 모양의 v9 — 워크스페이스 하나(운영자), 내장 2종류·규칙 1개, GitHub App 소스 둘
    (`all_open`). OpenArchive 류 소스: 이슈 20건(17건 지시 전, 3건 수정 → 검토 → PR 병합) + 기준선 24행.
    sandbox 류 소스: 이슈 1건 수정 → 검토 → PR 병합·이슈 닫힘. 실제 셀프호스트 볼륨·백업은 읽지 않는다."""
    import json

    from workflow.contracts.v1 import BUILTIN_KINDS, BUILTIN_RULES

    conn = connect(db_path)
    conn.executescript(V9_SCHEMA)
    conn.execute("INSERT INTO schema_version (version) VALUES (9)")
    conn.execute("INSERT INTO sessions (session_id, created_at, is_operator) VALUES (?, ?, 1)", (V9_SESSION, V9_NOW))
    for spec in BUILTIN_KINDS[:2]:  # v14 까지는 bug_fix·code_review 만 (triage 는 v15)
        conn.execute("INSERT INTO kinds (session_id, kind, spec_json, created_at) VALUES (?, ?, ?, ?)",
                     (V9_SESSION, spec.kind, spec.model_dump_json(), V9_NOW))
    conn.execute("INSERT INTO succession_rules (rule_id, session_id, from_kind, to_kind, rule_json, created_at)"
                 " VALUES ('rule-builtin', ?, 'bug_fix', 'code_review', ?, ?)",
                 (V9_SESSION, BUILTIN_RULES[0].model_dump_json(), V9_NOW))
    conn.execute("INSERT INTO agents (agent_id, name, owner_scope, connection_type, capabilities_json,"
                 " connection_state) VALUES ('agent-runner', 'runner', 'personal', 'local', '[]', 'online')")
    config = json.dumps({"intake": "all_open"})
    for source_id, name in ((ARCHIVE, "acme/archive"), (SANDBOX, "acme/sandbox")):
        conn.execute("INSERT INTO github_sources (source_id, session_id, repository_full_name, config_json,"
                     " created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)", (source_id, V9_SESSION, name, config,
                                                                            V9_NOW, V9_NOW))

    def task(task_id: str, kind: str, n: int, status: str, reason: str, agent: str | None,
             predecessor: str | None = None, source_ref: str | None = None) -> None:
        conn.execute(
            "INSERT INTO tasks (task_id, session_id, title, request, kind, required_capability_json, selection_mode,"
            " chosen_agent_id, run_mode, completion_mode, criteria_json, predecessor_task_id, revision, target_json,"
            " status, status_reason, created_at, finished_at, source_ref) VALUES (?, ?, ?, ?, ?, '{}', 'auto', ?,"
            " 'auto', 'review', '[]', ?, 1, '{}', ?, ?, ?, ?, ?)",
            (task_id, V9_SESSION, f"제목 {task_id}", f"본문 {task_id}", kind, agent, predecessor, status, reason,
             _t9(n), _t9(n + 30) if status == "완료" else None, source_ref))

    def execution(execution_id: str, task_id: str, kind: str, n: int) -> None:
        conn.execute(
            "INSERT INTO executions (execution_id, task_id, attempt_no, start_key, agent_id, kind, request_json,"
            " status, created_at, released_at) VALUES (?, ?, 1, 'start', 'agent-runner', ?, '{}', 'result_ready',"
            " ?, ?)", (execution_id, task_id, kind, _t9(n), _t9(n + 1)))

    def issue(source_id: str, repo_name: str, number: int, task_id: str, n: int, *, state: str = "open",
              merged: int | None = None) -> None:
        conn.execute(
            "INSERT INTO source_issues (source_id, github_issue_id, issue_number, task_id, source_revision,"
            " snapshot_json, snapshot_digest, issue_updated_at, state, created_at, updated_at, merged_pr_number,"
            " pr_merged_at, delegated_at, delegated_by) VALUES (?, ?, ?, ?, 1, '{}', 'd', ?, ?, ?, ?, ?, ?, ?, ?)",
            (source_id, 5000 + n, number, task_id, _t9(n), state, _t9(n), _t9(n), merged,
             _t9(n + 40) if merged else None, _t9(n) if merged else None, "operator" if merged else None))

    def merged_cycle(source_id: str, repo_name: str, number: int, n: int, state: str) -> None:
        fix, review = f"t-fix-{source_id[-1]}-{number}", f"t-rev-{source_id[-1]}-{number}"
        task(fix, "bug_fix", n, "완료", "PR 병합", "agent-runner", source_ref=f"{repo_name}#{number}")
        issue(source_id, repo_name, number, fix, n, state=state, merged=300 + number)
        execution(f"e-f-{fix}", fix, "bug_fix", n)
        task(review, "code_review", n + 5, "완료", "검토 승인", "agent-runner", predecessor=fix)
        execution(f"e-r-{fix}", review, "code_review", n + 5)
        conn.execute("INSERT INTO followup_links (session_id, cause_execution_id, to_kind, task_id, rules_revision,"
                     " created_at) VALUES (?, ?, 'code_review', ?, 1, ?)", (V9_SESSION, f"e-f-{fix}", review, _t9(n)))
        conn.execute(
            "INSERT INTO task_pull_requests (task_id, session_id, source_id, repository_full_name, issue_number,"
            " head_branch, fix_execution_id, review_execution_id, state, pr_number, created_at, updated_at,"
            " merged_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'merged', ?, ?, ?, ?)",
            (fix, V9_SESSION, source_id, repo_name, number, f"task/{fix}", f"e-f-{fix}", f"e-r-{fix}",
             300 + number, _t9(n + 10), _t9(n + 40), _t9(n + 40)))

    for number in UNDELEGATED:
        task_id = f"t-fix-1-{number}"
        task(task_id, "bug_fix", number, "대기", "실행 지시 전", None, source_ref=f"acme/archive#{number}")
        issue(ARCHIVE, "acme/archive", number, task_id, number)
    for number in MERGED:
        merged_cycle(ARCHIVE, "acme/archive", number, number * 3, "open")
    merged_cycle(SANDBOX, "acme/sandbox", 1, 100, "closed")
    for n in range(24):
        conn.execute(
            "INSERT INTO baseline_items (source_id, issue_number, issue_title, issue_opened_at, pr_number,"
            " pr_merged_at, fetched_at) VALUES (?, ?, 'i', ?, ?, ?, ?)",
            (ARCHIVE, 500 + n, "2026-08-01T00:00:00Z", 900 + n, "2026-08-02T00:00:00Z", V9_NOW))
    conn.execute("INSERT INTO baseline_imports (source_id, opened_before, fetched_at, item_count)"
                 " VALUES (?, ?, ?, 24)", (ARCHIVE, V9_NOW, V9_NOW))
    conn.close()
    (artifact_dir / V9_SESSION).mkdir(parents=True, exist_ok=True)
    (artifact_dir / V9_SESSION / "diff.patch").write_text("--- a\n+++ b\n", encoding="utf-8")


def _works(db_path: Path) -> list[tuple]:
    conn = sqlite3.connect(db_path)
    try:
        return [tuple(r) for r in conn.execute(
            "SELECT w.key_number, w.source_key, w.status, w.status_reason, w.closed_at IS NOT NULL,"
            " (SELECT COUNT(*) FROM tasks t WHERE t.work_item_id = w.work_item_id)"
            " FROM work_items w ORDER BY w.key_number")]
    finally:
        conn.close()


def test_selfhost_v9_copy_upgrades_to_v11_work_items_and_backups_round_trip(tmp_path, capsys):
    src = tmp_path / "src"
    src.mkdir()
    env = _env(src)
    _v9_selfhost(src / "central.sqlite", src / "artifacts")
    v9_counts = _counts(src / "central.sqlite")
    assert (v9_counts["tasks"], v9_counts["source_issues"], v9_counts["baseline_items"]) == (25, 21, 24)

    # 1) 업그레이드 전 백업 — 스키마 9
    assert backup.main(["create"], env=env, now=lambda: T1) == 0
    v9_backup = capsys.readouterr().out.strip()
    assert backup.main(["list"], env=env) == 0
    assert capsys.readouterr().out.strip().endswith("schema 9")

    # 2) v11 — 이슈 하나 = 업무 하나(검토 단계는 수정 업무에), 키는 생성 순, 기존 표 행 수·기준선 그대로
    conn = connect(src / "central.sqlite")
    init_schema(conn)
    conn.close()
    v10_counts = _counts(src / "central.sqlite")
    assert v10_counts == {
        **v9_counts, "work_items": 21, "work_item_links": 0, "members": 1, "field_mappings": 2,
        "work_item_events": 0, "login_sessions": 0, "member_invites": 0, "work_pull_requests": 0, **JIRA_EMPTY,
        "kinds": v9_counts["kinds"] + 1, **TRIAGE_ONE_WORKSPACE, **CONFIG_CHANGES_EMPTY, "responsibilities": 0, "internal_requests": 0, "internal_request_investigations": 0, "internal_request_rejections": 0, "internal_request_questions": 0, "internal_request_judgments": 0, "internal_request_resumptions": 0,  # + 내장 triage 종류·판단 기준 v1
    }
    works = _works(src / "central.sqlite")
    assert [w[0] for w in works] == list(range(1, 22))
    assert [w[1] for w in works] == [f"acme/archive#{n}" for n in range(1, 21)] + ["acme/sandbox#1"]
    assert works[:17] == [(n, f"acme/archive#{n}", "새로 들어옴", "담당 없음", 0, 1) for n in UNDELEGATED]
    assert works[17:] == [(k, key, "완료", f"PR 병합 — #{pr}", 1, 2) for k, key, pr in (
        (18, "acme/archive#18", 318), (19, "acme/archive#19", 319), (20, "acme/archive#20", 320),
        (21, "acme/sandbox#1", 301))]
    conn = sqlite3.connect(src / "central.sqlite")
    try:
        heads = [r[0] for r in conn.execute("SELECT head_branch FROM task_pull_requests ORDER BY task_id")]
        assert all(h.startswith("task/") for h in heads)  # 이미 기록된 브랜치 이름은 바꾸지 않는다
        (imported,) = conn.execute("SELECT item_count FROM baseline_imports").fetchone()
        assert imported == 24
    finally:
        conn.close()

    # 3) v10 백업 → 다른 위치 복원: 업무까지 그대로
    assert backup.main(["create"], env=env, now=lambda: T2) == 0
    v10_backup = capsys.readouterr().out.strip()
    assert backup.main(["list"], env=env) == 0
    assert capsys.readouterr().out.strip().splitlines()[0].endswith(f"schema {SCHEMA_VERSION}")
    dst = tmp_path / "dst"
    dst_env = {**_env(dst), "WORKFLOW_BACKUP_DIR": env["WORKFLOW_BACKUP_DIR"]}
    assert backup.main(["restore", v10_backup], env=dst_env) == 0
    assert _counts(dst / "central.sqlite") == v10_counts
    assert _works(dst / "central.sqlite") == works
    assert _files(dst / "artifacts") == _files(src / "artifacts")

    # 4) 업그레이드 전 v9 백업으로 되돌려도 복원이 v10 으로 올린다 — 같은 업무
    old = tmp_path / "old"
    old_env = {**_env(old), "WORKFLOW_BACKUP_DIR": env["WORKFLOW_BACKUP_DIR"]}
    assert backup.main(["restore", v9_backup], env=old_env) == 0
    assert _counts(old / "central.sqlite") == v10_counts
    assert _works(old / "central.sqlite") == works


V10_SCHEMA = (Path(__file__).parents[2] / "workflow" / "adapters" / "fixtures" / "schema_v10.sql").read_text()
V10_NOW = "2026-09-30T00:00:00Z"
V10_SESSION = "sess-selfhost"
V10_ADMIN = "mem-00000001"
V10_WORKS = 22  # 수집만 된 업무 17 + 완료 4 + 사람 요청이 열린 업무 1


def _v10_selfhost(db_path: Path, artifact_dir: Path) -> None:
    """phase 14 셀프호스트가 남긴 모양의 v10 — 워크스페이스 하나, 첫 관리자(`관리자`, 이메일·비밀번호 없음), 업무 22건
    (단계 Task 하나씩, 마지막 업무는 사람 요청이 열려 `내 차례`), 알림 행(보낸 것·대기), 연결 코드로 붙은 러너 1개와
    그 러너의 Agent. 실제 셀프호스트 볼륨·백업은 읽지 않는다."""
    from workflow.contracts.v1 import BUILTIN_KINDS, BUILTIN_RULES

    conn = connect(db_path)
    conn.executescript(V10_SCHEMA)
    conn.execute("INSERT INTO schema_version (version) VALUES (10)")
    conn.execute("INSERT INTO sessions (session_id, created_at, is_operator) VALUES (?, ?, 1)",
                 (V10_SESSION, V10_NOW))
    for spec in BUILTIN_KINDS[:2]:  # v14 까지는 bug_fix·code_review 만 (triage 는 v15)
        conn.execute("INSERT INTO kinds (session_id, kind, spec_json, created_at) VALUES (?, ?, ?, ?)",
                     (V10_SESSION, spec.kind, spec.model_dump_json(), V10_NOW))
    conn.execute("INSERT INTO succession_rules (rule_id, session_id, from_kind, to_kind, rule_json, created_at)"
                 " VALUES ('rule-builtin', ?, 'bug_fix', 'code_review', ?, ?)",
                 (V10_SESSION, BUILTIN_RULES[0].model_dump_json(), V10_NOW))
    conn.execute("INSERT INTO members (member_id, session_id, display_name, role, created_at)"
                 " VALUES (?, ?, '관리자', 'admin', ?)", (V10_ADMIN, V10_SESSION, V10_NOW))
    conn.execute("INSERT INTO connect_codes (code, issued_at, expires_at, used_at) VALUES ('code-1', ?, ?, ?)",
                 (V10_NOW, V10_NOW, V10_NOW))
    conn.execute("INSERT INTO connectors (connector_id, token_sha256, created_at) VALUES ('conn-00000001', 'h', ?)",
                 (V10_NOW,))
    conn.execute("INSERT INTO agents (agent_id, name, owner_scope, connection_type, connector_id,"
                 " local_registration_id, capabilities_json, connection_state) VALUES ('agt-00000001', 'billing',"
                 " 'personal', 'local', 'conn-00000001', 'billing', '[]', 'online')")
    for n in range(1, V10_WORKS + 1):
        work_id, task_id = f"wi-{n:012x}", f"t-{n}"
        status, reason, task_status = (
            ("새로 들어옴", "지시 전 — [에이전트에게 맡기기]", "대기") if n <= 17
            else ("완료", "PR 병합", "완료") if n <= 21 else ("내 차례", "사람 요청 — 확인", "확인 필요"))
        conn.execute(
            "INSERT INTO work_items (work_item_id, session_id, key_number, title, request, kind, status,"
            " status_reason, source_type, source_key, created_at, updated_at, closed_at) VALUES (?, ?, ?, ?, 'r',"
            " 'bug_fix', ?, ?, 'github', ?, ?, ?, ?)",
            (work_id, V10_SESSION, n, f"이슈 {n}", status, reason, f"acme/archive#{n}", V10_NOW, V10_NOW,
             V10_NOW if status == "완료" else None))
        conn.execute(
            "INSERT INTO tasks (task_id, session_id, title, request, kind, required_capability_json, selection_mode,"
            " run_mode, completion_mode, criteria_json, revision, target_json, status, status_reason, created_at,"
            " work_item_id) VALUES (?, ?, ?, 'r', 'bug_fix', '{}', 'auto', 'auto', 'review', '[]', 1, '{}', ?, ?,"
            " ?, ?)", (task_id, V10_SESSION, f"이슈 {n}", task_status, reason, V10_NOW, work_id))
    conn.execute("INSERT INTO human_requests (request_id, task_id, code, question, cause_key, task_revision, revision,"
                 " state, created_at) VALUES ('hr-00000001', ?, 'rework_limit_reached', '확인', 'k', 1, 1, 'open', ?)",
                 (f"t-{V10_WORKS}", V10_NOW))
    for n, (event, state) in enumerate((("pr_opened", "sent"), ("task_failed", "sent"), ("human_request", "pending")),
                                       start=1):
        conn.execute("INSERT INTO notifications (notification_id, session_id, event, task_id, dedupe_key, content,"
                     " payload_json, state, created_at) VALUES (?, ?, ?, ?, ?, 'c', '{}', ?, ?)",
                     (f"ntf-0000000{n}", V10_SESSION, event, f"t-{17 + n}", f"{event}:t-{17 + n}", state, V10_NOW))
    conn.close()
    (artifact_dir / V10_SESSION).mkdir(parents=True, exist_ok=True)
    (artifact_dir / V10_SESSION / "diff.patch").write_text("--- a\n+++ b\n", encoding="utf-8")


def _v11_facts(db_path: Path) -> dict:
    from workflow.adapters import repo

    conn = connect(db_path)
    try:
        (admin,) = repo.list_members(conn, V10_SESSION)
        return {
            "version": conn.execute("SELECT version FROM schema_version").fetchone()[0],
            "needs_first_setup": repo.needs_first_setup(conn, V10_SESSION),
            "admin": (admin["member_id"], admin["display_name"], admin["role"], admin["email"],
                      admin["password_hash"], admin["disabled_at"]),
            "runner_owner": repo.connector_owner(conn, "conn-00000001"),
            "code_issuer": conn.execute("SELECT issued_by_member_id FROM connect_codes").fetchone()[0],
            "notifications": [tuple(r) for r in conn.execute(
                "SELECT dedupe_key, channel, recipient_member_id, state FROM notifications ORDER BY notification_id")],
            "requesters": conn.execute(
                "SELECT COUNT(*) FROM work_items WHERE requested_by_member_id IS NOT NULL").fetchone()[0],
            "works": [tuple(r) for r in conn.execute(
                "SELECT key_number, status, status_reason FROM work_items ORDER BY key_number")],
            # 사람 요청이 열린 업무의 받는 사람 = 활성 관리자 전원(담당·맡긴 사람 없음)
            "turn": repo.turn_recipients_of(conn, f"wi-{V10_WORKS:012x}"),
        }
    finally:
        conn.close()


def test_selfhost_v10_copy_upgrades_to_v11_needs_first_setup_and_backups_round_trip(tmp_path, capsys):
    src = tmp_path / "src"
    src.mkdir()
    env = _env(src)
    _v10_selfhost(src / "central.sqlite", src / "artifacts")
    v10_counts = _counts(src / "central.sqlite")
    assert (v10_counts["work_items"], v10_counts["tasks"], v10_counts["notifications"], v10_counts["connectors"]) == (
        V10_WORKS, V10_WORKS, 3, 1)

    # 1) 업그레이드 전 백업 — 스키마 10
    assert backup.main(["create"], env=env, now=lambda: T1) == 0
    v10_backup = capsys.readouterr().out.strip()
    assert backup.main(["list"], env=env) == 0
    assert capsys.readouterr().out.strip().endswith("schema 10")

    # 2) v11 — 기존 행 수 그대로 + 새 표 둘(비어 있음), 새 칸은 비어 있고 알림은 공용
    conn = connect(src / "central.sqlite")
    init_schema(conn)
    conn.close()
    v11_counts = _counts(src / "central.sqlite")
    assert v11_counts == {**v10_counts, "login_sessions": 0, "member_invites": 0, "work_pull_requests": 0,
                          "field_mappings": v10_counts["field_mappings"] + 1, **JIRA_EMPTY,  # + jira 기본 매핑
                          "kinds": v10_counts["kinds"] + 1, **TRIAGE_ONE_WORKSPACE, **CONFIG_CHANGES_EMPTY, "responsibilities": 0, "internal_requests": 0, "internal_request_investigations": 0, "internal_request_rejections": 0, "internal_request_questions": 0, "internal_request_judgments": 0, "internal_request_resumptions": 0}  # + triage 종류·기준 v1
    facts = _v11_facts(src / "central.sqlite")
    assert facts["version"] == SCHEMA_VERSION == 25
    assert facts["needs_first_setup"] is True  # 첫 접속에서 .env 토큰으로 관리자 계정을 만든다
    assert facts["admin"] == (V10_ADMIN, "관리자", "admin", None, None, None)
    assert (facts["runner_owner"], facts["code_issuer"]) == (None, None)  # 기존 러너 = 관리자 관리
    assert facts["notifications"] == [
        ("pr_opened:t-18", "shared", None, "sent"), ("task_failed:t-19", "shared", None, "sent"),
        ("human_request:t-20", "shared", None, "pending")]
    assert facts["requesters"] == 0
    assert facts["works"][:17] == [(n, "새로 들어옴", "지시 전 — [에이전트에게 맡기기]") for n in range(1, 18)]
    assert facts["works"][-1] == (V10_WORKS, "내 차례", "사람 요청 — 확인")
    assert facts["turn"] == (V10_ADMIN,)

    # 3) v11 백업 → 다른 위치 복원: 같은 행·판정·산출물
    assert backup.main(["create"], env=env, now=lambda: T2) == 0
    v11_backup = capsys.readouterr().out.strip()
    dst = tmp_path / "dst"
    dst_env = {**_env(dst), "WORKFLOW_BACKUP_DIR": env["WORKFLOW_BACKUP_DIR"]}
    assert backup.main(["restore", v11_backup], env=dst_env) == 0
    assert _counts(dst / "central.sqlite") == v11_counts
    assert _v11_facts(dst / "central.sqlite") == facts
    assert _files(dst / "artifacts") == _files(src / "artifacts")

    # 4) 업그레이드 전 v10 백업으로 되돌려도 복원이 v11 로 올린다 — 같은 결과
    old = tmp_path / "old"
    old_env = {**_env(old), "WORKFLOW_BACKUP_DIR": env["WORKFLOW_BACKUP_DIR"]}
    assert backup.main(["restore", v10_backup], env=old_env) == 0
    assert _counts(old / "central.sqlite") == v11_counts
    assert _v11_facts(old / "central.sqlite") == facts


# --- phase 16: 셀프호스트 모양 v11 DB 사본 → v12 + 백업 왕복 (ADR-0022, SELFHOST "업그레이드" v12) -----------------

V11_SCHEMA = (Path(__file__).parents[2] / "workflow" / "adapters" / "fixtures" / "schema_v11.sql").read_text()
V11_SESSION = "sess-selfhost"
V11_SOURCE = "ghs-00000001"
# (키, 업무 상태, 이유, 단계 상태, 실행 상태, Runloom PR (상태, 번호), 열린 사람 요청 질문)
V11_WORKS = (
    (1, "새로 들어옴", "담당 없음", "대기", None, None, None),
    (2, "에이전트 작업 중", "버그 수정 실행 중", "실행 중", "running", None, None),
    (3, "PR · 검토", "PR 확인 — #101", "확인 필요", "result_ready", ("open", 101), None),
    (4, "완료", "PR 병합 — #102", "완료", "result_ready", ("merged", 102), None),
    (5, "내 차례", "사람 요청 — 범위를 정해 주세요", "확인 필요", "result_ready", None, "범위를 정해 주세요"),
)


def _t11(n: int) -> str:
    return f"2026-09-30T00:{n:02d}:00Z"


def _v11_selfhost(db_path: Path, artifact_dir: Path) -> None:
    """phase 15 셀프호스트가 남긴 모양의 v11 — 관리자(계정 있음)·멤버, GitHub 소스·러너·Agent 하나, 이슈마다 업무 하나
    (새로 들어옴·에이전트 작업 중·PR · 검토·완료·내 차례 — 단계·실행·Runloom PR·사람 요청이 그 상태를 만든다),
    업무 이벤트. 실제 셀프호스트 볼륨·백업은 읽지 않는다."""
    from workflow.contracts.v1 import BUILTIN_KINDS, BUILTIN_RULES

    conn = connect(db_path)
    conn.executescript(V11_SCHEMA)
    conn.execute("INSERT INTO schema_version (version) VALUES (11)")
    conn.execute("INSERT INTO sessions (session_id, created_at, is_operator) VALUES (?, ?, 1)", (V11_SESSION, _t11(0)))
    for spec in BUILTIN_KINDS[:2]:  # v14 까지는 bug_fix·code_review 만 (triage 는 v15)
        conn.execute("INSERT INTO kinds (session_id, kind, spec_json, created_at) VALUES (?, ?, ?, ?)",
                     (V11_SESSION, spec.kind, spec.model_dump_json(), _t11(0)))
    conn.execute("INSERT INTO succession_rules (rule_id, session_id, from_kind, to_kind, rule_json, created_at)"
                 " VALUES ('rule-builtin', ?, 'bug_fix', 'code_review', ?, ?)",
                 (V11_SESSION, BUILTIN_RULES[0].model_dump_json(), _t11(0)))
    conn.execute("INSERT INTO members (member_id, session_id, display_name, role, created_at, email, password_hash)"
                 " VALUES ('mem-00000001', ?, '관리자', 'admin', ?, 'a@example.com', 'scrypt$h')",
                 (V11_SESSION, _t11(0)))
    conn.execute("INSERT INTO members (member_id, session_id, display_name, role, created_at)"
                 " VALUES ('mem-0000000a', ?, '김OO', 'member', ?)", (V11_SESSION, _t11(0)))
    conn.execute("INSERT INTO connectors (connector_id, token_sha256, created_at, owner_member_id)"
                 " VALUES ('conn-00000001', 'h', ?, 'mem-0000000a')", (_t11(0),))
    conn.execute("INSERT INTO agents (agent_id, name, owner_scope, connection_type, connector_id,"
                 " local_registration_id, capabilities_json, connection_state) VALUES ('agt-00000001', 'billing',"
                 " 'personal', 'local', 'conn-00000001', 'billing', '[]', 'online')")
    conn.execute("INSERT INTO session_agents (session_id, agent_id, registered_at) VALUES (?, 'agt-00000001', ?)",
                 (V11_SESSION, _t11(0)))
    conn.execute("INSERT INTO github_sources (source_id, session_id, repository_full_name, config_json, cursor,"
                 " created_at, updated_at) VALUES (?, ?, 'acme/billing', '{}', ?, ?, ?)",
                 (V11_SOURCE, V11_SESSION, _t11(1), _t11(0), _t11(0)))
    for n, status, reason, task_status, run, pr, question in V11_WORKS:
        work_id, task_id = f"wi-{n:012x}", f"t-{n}"
        closed = _t11(10 + n) if status == "완료" else None
        conn.execute(
            "INSERT INTO work_items (work_item_id, session_id, key_number, title, request, kind, status, status_reason,"
            " source_type, source_id, source_key, created_at, updated_at, closed_at, requested_by_member_id)"
            " VALUES (?, ?, ?, ?, 'r', 'bug_fix', ?, ?, 'github', ?, ?, ?, ?, ?, ?)",
            (work_id, V11_SESSION, n, f"이슈 {n}", status, reason, V11_SOURCE, f"acme/billing#{n}", _t11(n),
             _t11(10 + n), closed, None if run is None else "mem-0000000a"))
        conn.execute(
            "INSERT INTO tasks (task_id, session_id, title, request, kind, required_capability_json, selection_mode,"
            " chosen_agent_id, run_mode, completion_mode, criteria_json, revision, target_json, status, status_reason,"
            " finished_at, created_at, work_item_id) VALUES (?, ?, ?, 'r', 'bug_fix', '{}', 'auto', ?, 'auto',"
            " 'review', '[]', 1, '{}', ?, ?, ?, ?, ?)",
            (task_id, V11_SESSION, f"이슈 {n}", None if run is None else "agt-00000001", task_status, reason,
             closed, _t11(n), work_id))
        conn.execute("INSERT INTO source_issues (source_id, github_issue_id, issue_number, task_id, source_revision,"
                     " snapshot_json, snapshot_digest, issue_updated_at, state, created_at, updated_at, delegated_at,"
                     " delegated_by) VALUES (?, ?, ?, ?, 1, '{}', 'd', ?, 'open', ?, ?, ?, ?)",
                     (V11_SOURCE, 9000 + n, n, task_id, _t11(n), _t11(n), _t11(n),
                      None if run is None else _t11(n), None if run is None else "operator"))
        if run is not None:
            conn.execute("INSERT INTO executions (execution_id, task_id, attempt_no, start_key, agent_id, kind,"
                         " request_json, status, created_at, released_at) VALUES (?, ?, 1, ?, 'agt-00000001',"
                         " 'bug_fix', '{}', ?, ?, ?)",
                         (f"exe-{n}", task_id, f"auto:{task_id}:r1", run, _t11(n),
                          None if run == "running" else _t11(n)))
        if pr is not None:
            state, number = pr
            conn.execute("INSERT INTO task_pull_requests (task_id, session_id, source_id, repository_full_name,"
                         " issue_number, head_branch, fix_execution_id, review_execution_id, state, pr_number, pr_url,"
                         " draft, created_at, updated_at, merged_at) VALUES (?, ?, ?, 'acme/billing', ?, ?, ?, ?, ?,"
                         " ?, ?, 1, ?, ?, ?)",
                         (task_id, V11_SESSION, V11_SOURCE, n, f"runloom/RUN-{n}", f"exe-{n}", f"exe-{n}", state,
                          number, f"https://github.com/acme/billing/pull/{number}", _t11(n), _t11(n),
                          _t11(10 + n) if state == "merged" else None))
        if question is not None:
            conn.execute("INSERT INTO human_requests (request_id, task_id, code, question, cause_key, task_revision,"
                         " revision, state, created_at) VALUES (?, ?, 'rework_limit_reached', ?, 'k', 1, 1, 'open', ?)",
                         (f"hr-0000000{n}", task_id, question, _t11(n)))
        conn.execute("INSERT INTO work_item_events (work_item_id, session_id, type, config_revision, occurred_at,"
                     " data_json) VALUES (?, ?, 'status_changed', 1, ?, ?)",
                     (work_id, V11_SESSION, _t11(10 + n), f'{{"from": "새로 들어옴", "to": "{status}"}}'))
    conn.close()
    (artifact_dir / V11_SESSION).mkdir(parents=True, exist_ok=True)
    (artifact_dir / V11_SESSION / "diff.patch").write_text("--- a\n+++ b\n", encoding="utf-8")


def _v12_facts(db_path: Path) -> dict:
    """저장된 업무 상태와, v12 규칙(직접 작업·감지 PR 포함)으로 다시 계산한 상태 — 둘이 같아야 한다."""
    from workflow.adapters import repo
    from workflow.domain.work_status import work_status

    conn = connect(db_path)
    try:
        works = conn.execute("SELECT work_item_id, key_number, status, status_reason, direct_member_id,"
                             " direct_started_at, direct_branch FROM work_items ORDER BY key_number").fetchall()
        return {
            "version": conn.execute("SELECT version FROM schema_version").fetchone()[0],
            "stored": [(w["key_number"], w["status"], w["status_reason"]) for w in works],
            "recomputed": [(w["key_number"], s.status, s.reason) for w in works
                           for s in [work_status(repo.work_item_facts(conn, w["work_item_id"]))]],
            "direct": {(w["direct_member_id"], w["direct_started_at"], w["direct_branch"]) for w in works},
            "pull_cursor": conn.execute("SELECT cursor, pull_cursor FROM github_sources").fetchone()[:],
            "event_ids": [r[0] for r in conn.execute("SELECT id FROM work_item_events ORDER BY id")],
            "runloom_pulls": [tuple(r) for r in conn.execute(
                "SELECT task_id, state, pr_number FROM task_pull_requests ORDER BY task_id")],
        }
    finally:
        conn.close()


def test_selfhost_v11_copy_upgrades_to_v12_with_unchanged_work_status_and_backups_round_trip(tmp_path, capsys):
    src = tmp_path / "src"
    src.mkdir()
    env = _env(src)
    _v11_selfhost(src / "central.sqlite", src / "artifacts")
    v11_counts = _counts(src / "central.sqlite")
    assert (v11_counts["work_items"], v11_counts["tasks"], v11_counts["executions"], v11_counts["task_pull_requests"],
            v11_counts["work_item_events"], v11_counts["human_requests"]) == (5, 5, 4, 2, 5, 1)

    # 1) 업그레이드 전 백업 — 스키마 11
    assert backup.main(["create"], env=env, now=lambda: T1) == 0
    v11_backup = capsys.readouterr().out.strip()
    assert backup.main(["list"], env=env) == 0
    assert capsys.readouterr().out.strip().endswith("schema 11")

    # 2) v12 — 기존 행 수 그대로 + 감지 PR 표(비어 있음), 직접 작업 칸·PR 커서는 비어 있고, 업무 상태 재계산 결과가 같다
    conn = connect(src / "central.sqlite")
    init_schema(conn)
    conn.close()
    v12_counts = _counts(src / "central.sqlite")
    assert v12_counts == {**v11_counts, "work_pull_requests": 0,
                          "field_mappings": v11_counts["field_mappings"] + 1, **JIRA_EMPTY,  # + jira 기본 매핑
                          "kinds": v11_counts["kinds"] + 1, **TRIAGE_ONE_WORKSPACE, **CONFIG_CHANGES_EMPTY, "responsibilities": 0, "internal_requests": 0, "internal_request_investigations": 0, "internal_request_rejections": 0, "internal_request_questions": 0, "internal_request_judgments": 0, "internal_request_resumptions": 0}  # + triage 종류·기준 v1
    facts = _v12_facts(src / "central.sqlite")
    assert facts["version"] == SCHEMA_VERSION == 25
    assert facts["stored"] == [(n, status, reason) for n, status, reason, *_ in V11_WORKS]
    assert facts["recomputed"] == facts["stored"]
    assert facts["direct"] == {(None, None, None)}
    assert facts["pull_cursor"] == (_t11(1), None)  # 이슈 커서 그대로, PR 커서는 처음(NULL)
    assert facts["event_ids"] == [1, 2, 3, 4, 5]
    assert facts["runloom_pulls"] == [("t-3", "open", 101), ("t-4", "merged", 102)]

    # 3) v12 백업 → 다른 위치 복원: 같은 행·상태·산출물
    assert backup.main(["create"], env=env, now=lambda: T2) == 0
    v12_backup = capsys.readouterr().out.strip()
    assert backup.main(["list"], env=env) == 0
    listed = dict(line.split("\t", 1) for line in capsys.readouterr().out.strip().splitlines())
    assert listed[v12_backup].endswith(f"schema {SCHEMA_VERSION}") and listed[v11_backup].endswith("schema 11")
    dst = tmp_path / "dst"
    dst_env = {**_env(dst), "WORKFLOW_BACKUP_DIR": env["WORKFLOW_BACKUP_DIR"]}
    assert backup.main(["restore", v12_backup], env=dst_env) == 0
    assert _counts(dst / "central.sqlite") == v12_counts
    assert _v12_facts(dst / "central.sqlite") == facts
    assert _files(dst / "artifacts") == _files(src / "artifacts")

    # 4) 업그레이드 전 v11 백업을 v12 코드로 복원해도 v12 로 올린다 — 같은 결과
    old = tmp_path / "old"
    old_env = {**_env(old), "WORKFLOW_BACKUP_DIR": env["WORKFLOW_BACKUP_DIR"]}
    assert backup.main(["restore", v11_backup], env=old_env) == 0
    assert _counts(old / "central.sqlite") == v12_counts
    assert _v12_facts(old / "central.sqlite") == facts


# --- phase 18: v13 사본 → v14 Jira 표 + 백업 왕복 (ADR-0024, ARCHITECTURE "스키마 v14") -----------------------------

V13_SCHEMA = (Path(__file__).parents[2] / "workflow" / "adapters" / "fixtures" / "schema_v13.sql").read_text()


def test_v13_copy_upgrades_to_v14_and_jira_rows_round_trip_without_token(tmp_path, capsys):
    """v13 백업을 복원하면 14 로 올라가 Jira 표가 생기고, Jira 행은 백업에 실리되 토큰(비밀 파일)은 실리지 않는다."""
    from workflow.adapters.secret_store import SecretStore

    src = tmp_path / "src"
    (src / "artifacts").mkdir(parents=True)
    conn = connect(src / "central.sqlite")
    conn.executescript(V13_SCHEMA)
    conn.execute("INSERT INTO schema_version (version) VALUES (13)")
    conn.execute("INSERT INTO sessions (session_id, created_at, is_operator) VALUES ('s1', ?, 1)", (V10_NOW,))
    conn.execute("INSERT INTO kinds (session_id, kind, spec_json, created_at) VALUES ('s1', 'bug_fix', '{}', ?)",
                 (V10_NOW,))
    conn.close()
    env = _env(src)
    assert backup.main(["create"], env=env, now=lambda: T1) == 0
    v13_backup = capsys.readouterr().out.strip()

    old = tmp_path / "old"
    assert backup.main(["restore", v13_backup], env={**_env(old), "WORKFLOW_BACKUP_DIR": env["WORKFLOW_BACKUP_DIR"]}) == 0
    capsys.readouterr()
    conn = connect(old / "central.sqlite")
    init_schema(conn)
    assert conn.execute("SELECT version FROM schema_version").fetchone()[0] == SCHEMA_VERSION == 25
    assert [tuple(r) for r in conn.execute("SELECT source_type, source_value, runloom_value FROM field_mappings")] == [
        ("jira", "*", "bug_fix")]
    conn.execute("INSERT INTO jira_connections (session_id, site_url, cloud_id, api_base, email, account_id,"
                 " display_name, connected_at, updated_at) VALUES ('s1', 'https://acme.atlassian.net', 'c', 'gateway',"
                 " 'a@example.com', 'acc', '김OO', ?, ?)", (V10_NOW, V10_NOW))
    conn.close()
    SecretStore(old / "secrets").write("github_token", "TOKEN-SECRET-VALUE")  # 비밀 저장소 파일은 백업 밖

    old_env = {**_env(old), "WORKFLOW_BACKUP_DIR": env["WORKFLOW_BACKUP_DIR"], "WORKFLOW_SECRET_DIR": str(old / "secrets")}
    assert backup.main(["create"], env=old_env, now=lambda: T2) == 0
    v14_backup = capsys.readouterr().out.strip()
    made = Path(env["WORKFLOW_BACKUP_DIR"]) / v14_backup
    assert not any(b"TOKEN-SECRET-VALUE" in p.read_bytes() for p in made.iterdir())
    dst = tmp_path / "dst"
    assert backup.main(["restore", v14_backup], env={**_env(dst), "WORKFLOW_BACKUP_DIR": env["WORKFLOW_BACKUP_DIR"]}) == 0
    conn = connect(dst / "central.sqlite")
    try:
        assert [tuple(r) for r in conn.execute("SELECT session_id, site_url, api_base FROM jira_connections")] == [
            ("s1", "https://acme.atlassian.net", "gateway")]
    finally:
        conn.close()


# --- phase 19: v14 사본 → v15 판단 표·내장 triage 종류·기준 v1 + 백업 왕복 (ADR-0025, ARCHITECTURE "스키마 v15") -------

V14_SCHEMA = (Path(__file__).parents[2] / "workflow" / "adapters" / "fixtures" / "schema_v14.sql").read_text()


def test_v14_backup_restores_and_upgrades_to_v15(tmp_path, capsys):
    """v14 백업을 복원하면 15 로 올라가 판단 표가 생기고 워크스페이스에 triage 종류·기준 v1 이 생긴다. 다시 백업·복원해도
    판단 기준 행이 그대로다."""
    from workflow.domain.triage_criteria import TRIAGE_CRITERIA_V1

    src = tmp_path / "src"
    (src / "artifacts").mkdir(parents=True)
    conn = connect(src / "central.sqlite")
    conn.executescript(V14_SCHEMA)
    conn.execute("INSERT INTO schema_version (version) VALUES (14)")
    conn.execute("INSERT INTO sessions (session_id, created_at, is_operator) VALUES ('s1', ?, 1)", (V10_NOW,))
    conn.execute("INSERT INTO kinds (session_id, kind, spec_json, created_at) VALUES ('s1', 'bug_fix', '{}', ?)",
                 (V10_NOW,))
    conn.close()
    env = _env(src)
    assert backup.main(["create"], env=env, now=lambda: T1) == 0
    v14_backup = capsys.readouterr().out.strip()
    assert backup.main(["list"], env=env) == 0
    assert capsys.readouterr().out.strip().endswith("schema 14")

    old = tmp_path / "old"
    assert backup.main(["restore", v14_backup], env={**_env(old), "WORKFLOW_BACKUP_DIR": env["WORKFLOW_BACKUP_DIR"]}) == 0
    capsys.readouterr()
    conn = connect(old / "central.sqlite")
    init_schema(conn)
    assert conn.execute("SELECT version FROM schema_version").fetchone()[0] == SCHEMA_VERSION == 25
    assert [r[0] for r in conn.execute("SELECT kind FROM kinds WHERE session_id = 's1' ORDER BY kind")] == [
        "bug_fix", "triage"]
    assert [tuple(r) for r in conn.execute("SELECT session_id, version, body FROM triage_criteria")] == [
        ("s1", 1, TRIAGE_CRITERIA_V1)]
    conn.close()

    old_env = {**_env(old), "WORKFLOW_BACKUP_DIR": env["WORKFLOW_BACKUP_DIR"]}
    assert backup.main(["create"], env=old_env, now=lambda: T2) == 0
    v15_backup = capsys.readouterr().out.strip()
    dst = tmp_path / "dst"
    assert backup.main(["restore", v15_backup], env={**_env(dst), "WORKFLOW_BACKUP_DIR": env["WORKFLOW_BACKUP_DIR"]}) == 0
    assert _counts(dst / "central.sqlite") == _counts(old / "central.sqlite")
    conn = connect(dst / "central.sqlite")
    try:
        assert conn.execute("SELECT version FROM schema_version").fetchone()[0] == SCHEMA_VERSION
        assert [tuple(r) for r in conn.execute("SELECT session_id, version FROM triage_criteria")] == [("s1", 1)]
    finally:
        conn.close()


# --- phase 20: v15 사본 → v16 설정 변경 기록 표 + 백업 왕복 (ADR-0026, ARCHITECTURE "스키마 v16") -----------------------

V15_SCHEMA = (Path(__file__).parents[2] / "workflow" / "adapters" / "fixtures" / "schema_v15.sql").read_text()


def test_v15_backup_restores_and_upgrades_to_v16(tmp_path, capsys):
    """v15 백업을 복원하면 16 으로 올라가 빈 설정 변경 기록 표가 생긴다(과거 변경은 채우지 않는다). 설정 번호는 그대로이고,
    다시 백업·복원해도 기록 행이 그대로다."""
    src = tmp_path / "src"
    (src / "artifacts").mkdir(parents=True)
    conn = connect(src / "central.sqlite")
    conn.executescript(V15_SCHEMA)
    conn.execute("INSERT INTO schema_version (version) VALUES (15)")
    conn.execute("INSERT INTO sessions (session_id, created_at, is_operator, config_revision) VALUES ('s1', ?, 1, 4)",
                 (V10_NOW,))
    conn.close()
    env = _env(src)
    assert backup.main(["create"], env=env, now=lambda: T1) == 0
    v15_backup = capsys.readouterr().out.strip()
    assert backup.main(["list"], env=env) == 0
    assert capsys.readouterr().out.strip().endswith("schema 15")

    old = tmp_path / "old"
    assert backup.main(["restore", v15_backup], env={**_env(old), "WORKFLOW_BACKUP_DIR": env["WORKFLOW_BACKUP_DIR"]}) == 0
    capsys.readouterr()
    conn = connect(old / "central.sqlite")
    init_schema(conn)
    assert conn.execute("SELECT version FROM schema_version").fetchone()[0] == SCHEMA_VERSION == 25
    assert conn.execute("SELECT config_revision FROM sessions WHERE session_id = 's1'").fetchone()[0] == 4
    assert conn.execute("SELECT COUNT(*) FROM config_changes").fetchone()[0] == 0
    conn.execute("INSERT INTO config_changes (session_id, revision, area, action, subject, by_member_id, occurred_at)"
                 " VALUES ('s1', 5, 'kind', 'add', 'classify', NULL, ?)", (V10_NOW,))
    conn.close()

    old_env = {**_env(old), "WORKFLOW_BACKUP_DIR": env["WORKFLOW_BACKUP_DIR"]}
    assert backup.main(["create"], env=old_env, now=lambda: T2) == 0
    v16_backup = capsys.readouterr().out.strip()
    dst = tmp_path / "dst"
    assert backup.main(["restore", v16_backup], env={**_env(dst), "WORKFLOW_BACKUP_DIR": env["WORKFLOW_BACKUP_DIR"]}) == 0
    assert _counts(dst / "central.sqlite") == _counts(old / "central.sqlite")
    conn = connect(dst / "central.sqlite")
    try:
        assert conn.execute("SELECT version FROM schema_version").fetchone()[0] == SCHEMA_VERSION
        assert [tuple(r) for r in conn.execute("SELECT session_id, revision, area, action, subject FROM config_changes")
                ] == [("s1", 5, "kind", "add", "classify")]
    finally:
        conn.close()


# --- phase 22: v23 백업 → v24 판단 원인·알림 사건·사내 요청 출처 (ADR-0027, ARCHITECTURE "스키마 v24") -----------------

V23_SCHEMA = (Path(__file__).parents[2] / "workflow" / "adapters" / "fixtures" / "schema_v23.sql").read_text()


def test_v23_backup_restores_and_upgrades_to_v24(tmp_path, capsys):
    """v23 백업을 복원하면 24 로 올라간다 — 접수 판단 행은 cause = 'intake', 알림·사내 요청 행은 그대로이고 사내 요청
    출처는 NULL. 올린 DB 를 다시 백업·복원해도 행이 그대로다."""
    src = tmp_path / "src"
    (src / "artifacts").mkdir(parents=True)
    conn = connect(src / "central.sqlite")
    conn.executescript(V23_SCHEMA)
    conn.execute("INSERT INTO schema_version (version) VALUES (23)")
    conn.execute("INSERT INTO sessions (session_id, created_at, is_operator) VALUES ('s1', ?, 1)", (V10_NOW,))
    conn.execute("INSERT INTO kinds (session_id, kind, spec_json, created_at) VALUES ('s1', 'triage', '{}', ?)",
                 (V10_NOW,))
    member = "INSERT INTO members (member_id, session_id, display_name, role, created_at) VALUES (?, 's1', 'n', ?, ?)"
    conn.execute(member, ("mem-00000001", "admin", V10_NOW))
    conn.execute(member, ("mem-00000002", "member", V10_NOW))
    conn.execute("INSERT INTO agents (agent_id, name, owner_scope, connection_type, capabilities_json,"
                 " connection_state) VALUES ('a1', 'n', 'personal', 'local', '[]', 'online')")
    conn.execute("INSERT INTO work_items (work_item_id, session_id, key_number, title, request, kind, status,"
                 " status_reason, source_type, created_at, updated_at) VALUES ('wi-000000000001', 's1', 1, 't', 'r',"
                 " 'triage', '대기', '', 'manual', ?, ?)", (V10_NOW, V10_NOW))
    conn.execute("INSERT INTO tasks (task_id, session_id, title, request, kind, required_capability_json,"
                 " selection_mode, run_mode, completion_mode, criteria_json, revision, target_json, status,"
                 " status_reason, created_at, work_item_id) VALUES ('t1', 's1', 't', 'r', 'triage', '{}', 'auto',"
                 " 'manual', 'review', '[]', 1, '{}', '대기', '', ?, 'wi-000000000001')", (V10_NOW,))
    conn.execute("INSERT INTO executions (execution_id, task_id, attempt_no, start_key, agent_id, kind, request_json,"
                 " status, created_at) VALUES ('e1', 't1', 1, 'k', 'a1', 'triage', '{}', 'failed', ?)", (V10_NOW,))
    conn.execute("INSERT INTO triage_criteria (session_id, version, body, created_at) VALUES ('s1', 1, '기준', ?)",
                 (V10_NOW,))
    conn.execute("INSERT INTO triage_logs (triage_id, session_id, work_item_id, work_revision, task_id, execution_id,"
                 " agent_id, trigger, criteria_version, input_sha256, candidates_json, state, failed_code, created_at,"
                 " updated_at) VALUES ('trg-00000001', 's1', 'wi-000000000001', 1, 't1', 'e1', 'a1', 'auto', 1, ?,"
                 " '{}', 'failed', 'result_invalid', ?, ?)", ("a" * 64, V10_NOW, V10_NOW))
    conn.execute("INSERT INTO notifications (notification_id, session_id, event, task_id, dedupe_key, content,"
                 " payload_json, state, created_at) VALUES ('ntf-00000001', 's1', 'delegated_to_you', 't1', 'k1', 'c',"
                 " '{}', 'pending', ?)", (V10_NOW,))
    conn.execute("INSERT INTO internal_requests (request_id, session_id, work_item_id, requester_member_id,"
                 " recipient_member_id, judgment_member_id, system_id, request_kind, directory_revision,"
                 " submission_key, purpose, state, revision, created_at) VALUES ('req-00000001', 's1',"
                 " 'wi-000000000001', 'mem-00000001', 'mem-00000002', 'mem-00000001', 'billing', 'investigate', 1,"
                 " 'key', '목적', 'pending', 1, ?)", (V10_NOW,))
    conn.close()
    env = _env(src)
    assert backup.main(["create"], env=env, now=lambda: T1) == 0
    v23_backup = capsys.readouterr().out.strip()
    assert backup.main(["list"], env=env) == 0
    assert capsys.readouterr().out.strip().endswith("schema 23")

    old = tmp_path / "old"
    assert backup.main(["restore", v23_backup], env={**_env(old), "WORKFLOW_BACKUP_DIR": env["WORKFLOW_BACKUP_DIR"]}) == 0
    capsys.readouterr()
    assert _counts(old / "central.sqlite") == _counts(src / "central.sqlite")
    conn = connect(old / "central.sqlite")
    init_schema(conn)
    assert conn.execute("SELECT version FROM schema_version").fetchone()[0] == SCHEMA_VERSION == 25
    assert [tuple(r) for r in conn.execute("SELECT triage_id, cause, cause_execution_id, cause_request_id"
                                           " FROM triage_logs")] == [("trg-00000001", "intake", None, None)]
    assert [tuple(r) for r in conn.execute("SELECT notification_id, event FROM notifications")] == [
        ("ntf-00000001", "delegated_to_you")]
    assert [tuple(r) for r in conn.execute("SELECT request_id, created_by_triage_id FROM internal_requests")] == [
        ("req-00000001", None)]
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    conn.execute("INSERT INTO notifications (notification_id, session_id, event, task_id, dedupe_key, content,"
                 " payload_json, state, created_at) VALUES ('ntf-00000002', 's1', 'internal_request_received', 't1',"
                 " 'k2', 'c', '{}', 'pending', ?)", (V10_NOW,))
    conn.close()

    old_env = {**_env(old), "WORKFLOW_BACKUP_DIR": env["WORKFLOW_BACKUP_DIR"]}
    assert backup.main(["create"], env=old_env, now=lambda: T2) == 0
    v24_backup = capsys.readouterr().out.strip()
    dst = tmp_path / "dst"
    assert backup.main(["restore", v24_backup], env={**_env(dst), "WORKFLOW_BACKUP_DIR": env["WORKFLOW_BACKUP_DIR"]}) == 0
    assert _counts(dst / "central.sqlite") == _counts(old / "central.sqlite")
    conn = connect(dst / "central.sqlite")
    try:
        assert conn.execute("SELECT version FROM schema_version").fetchone()[0] == SCHEMA_VERSION
        assert [tuple(r) for r in conn.execute("SELECT notification_id, event FROM notifications"
                                               " ORDER BY notification_id")] == [
            ("ntf-00000001", "delegated_to_you"), ("ntf-00000002", "internal_request_received")]
    finally:
        conn.close()
