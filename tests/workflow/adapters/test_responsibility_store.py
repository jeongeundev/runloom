from workflow.adapters import db


def test_existing_v16_database_gets_empty_directory(tmp_path):
    conn = db.connect(tmp_path / 'db.sqlite')
    conn.executescript(db._SCHEMA.replace(db._V17_TABLES, '').replace(db._V18_TABLES, '').replace(db._V19_TABLES, '').replace(db._V20_TABLES, '').replace(db._V21_TABLES, '').replace(db._V22_TABLES, '').replace(db._V23_TABLES, ''))
    conn.execute('INSERT INTO schema_version VALUES (16)')
    conn.execute("INSERT INTO sessions (session_id, created_at) VALUES ('existing', '2026-10-03T00:00:00Z')")
    before = [tuple(row) for row in conn.execute('SELECT * FROM sessions')]
    db.init_schema(conn)
    assert conn.execute('SELECT COUNT(*) FROM responsibilities').fetchone()[0] == 0
    assert conn.execute('SELECT version FROM schema_version').fetchone()[0] == db.SCHEMA_VERSION
    assert [tuple(row) for row in conn.execute('SELECT * FROM sessions')] == before
    db.init_schema(conn)
    assert [tuple(row) for row in conn.execute('SELECT * FROM sessions')] == before
    conn.close()
