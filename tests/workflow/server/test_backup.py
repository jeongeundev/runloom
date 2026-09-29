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


# --- phase 13·14: 셀프호스트 모양 v8 DB 사본 → v10 + 백업 왕복 (ADR-0019·0020, SELFHOST "업그레이드") -----------

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
    for spec in BUILTIN_KINDS:
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


def _version_and_kinds(db_path: Path) -> tuple[int, list[str]]:
    conn = sqlite3.connect(db_path)
    try:
        version = conn.execute("SELECT version FROM schema_version").fetchone()[0]
        kinds = [r[0] for r in conn.execute("SELECT kind FROM kinds WHERE session_id = ? ORDER BY kind", (V8_SESSION,))]
        return version, kinds
    finally:
        conn.close()


def test_selfhost_v8_copy_upgrades_to_v10_and_backups_round_trip(tmp_path, capsys):
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

    # 2) v10 으로 올린다 — 진단 두 종류·그 규칙만 사라지고 나머지 행 수는 그대로, 이슈마다 수정·검토가 각자 업무
    #    (후속 연결 없음), 첫 관리자·기본 매핑 하나
    conn = connect(src / "central.sqlite")
    init_schema(conn)
    conn.close()
    v9_counts = _counts(src / "central.sqlite")
    assert v9_counts == {
        **v8_counts, "kinds": 2, "succession_rules": 1,
        "work_items": 6, "work_item_links": 0, "members": 1, "field_mappings": 1, "work_item_events": 0,
    }
    assert _version_and_kinds(src / "central.sqlite") == (10, ["bug_fix", "code_review"])

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
    assert _version_and_kinds(old / "central.sqlite") == (SCHEMA_VERSION, ["bug_fix", "code_review"])
