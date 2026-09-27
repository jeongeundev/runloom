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
