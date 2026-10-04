from workflow.adapters import db


def test_v17_migration_preserves_directory_and_existing_data(tmp_path):
    conn = db.connect(tmp_path / 'old.sqlite')
    schema = db._SCHEMA.replace(db._V18_TABLES, '').replace(db._V19_TABLES, '').replace(db._V20_TABLES, '').replace(db._V21_TABLES, '').replace(db._V22_TABLES, '').replace(db._V23_TABLES, '')
    conn.executescript(schema)
    conn.execute('INSERT INTO schema_version VALUES (17)')
    conn.execute("INSERT INTO sessions (session_id, created_at) VALUES ('existing', '2026-10-03T00:00:00Z')")
    conn.execute("INSERT INTO responsibilities VALUES ('existing', 0, '{}')")
    before = [tuple(r) for r in conn.execute('SELECT * FROM responsibilities')]
    db.init_schema(conn)
    assert conn.execute('SELECT version FROM schema_version').fetchone()[0] == db.SCHEMA_VERSION
    assert conn.execute('SELECT COUNT(*) FROM internal_requests').fetchone()[0] == 0
    assert [tuple(r) for r in conn.execute('SELECT * FROM responsibilities')] == before
    db.init_schema(conn)
    assert [tuple(r) for r in conn.execute('SELECT * FROM responsibilities')] == before
    conn.close()


def test_v18_migration_adds_empty_investigations(tmp_path):
    conn = db.connect(tmp_path / 'v18.sqlite')
    conn.executescript(db._SCHEMA.replace(getattr(db, '_V19_TABLES', ''), '').replace(db._V20_TABLES, '').replace(db._V21_TABLES, '').replace(db._V22_TABLES, '').replace(db._V23_TABLES, ''))
    conn.execute('INSERT INTO schema_version VALUES (18)')
    db.init_schema(conn)
    assert conn.execute('SELECT version FROM schema_version').fetchone()[0] == db.SCHEMA_VERSION
    assert conn.execute('SELECT COUNT(*) FROM internal_request_investigations').fetchone()[0] == 0
    assert conn.execute('PRAGMA foreign_key_check').fetchall() == []
    conn.close()


def test_v19_migration_preserves_workspace_and_adds_rejections(tmp_path):
    conn = db.connect(tmp_path / 'v19.sqlite')
    conn.executescript(db._SCHEMA.replace(db._V20_TABLES, '').replace(db._V21_TABLES, '').replace(db._V22_TABLES, '').replace(db._V23_TABLES, ''))
    conn.execute('INSERT INTO schema_version VALUES (19)')
    conn.execute("INSERT INTO sessions (session_id, created_at) VALUES ('existing', '2026-10-04T00:00:00Z')")
    before = [tuple(r) for r in conn.execute('SELECT * FROM sessions')]
    db.init_schema(conn)
    db.init_schema(conn)
    assert conn.execute('SELECT version FROM schema_version').fetchone()[0] == db.SCHEMA_VERSION
    assert [tuple(r) for r in conn.execute('SELECT * FROM sessions')] == before
    assert conn.execute('SELECT COUNT(*) FROM internal_request_rejections').fetchone()[0] == 0
    assert conn.execute('PRAGMA foreign_key_check').fetchall() == []
    conn.close()


def test_v20_migration_adds_question_history_and_preserves_workspace(tmp_path):
    conn = db.connect(tmp_path / 'v20.sqlite')
    conn.executescript(db._SCHEMA.replace(db._V21_TABLES, '').replace(db._V22_TABLES, '').replace(db._V23_TABLES, ''))
    conn.execute('INSERT INTO schema_version VALUES (20)')
    conn.execute("INSERT INTO sessions (session_id, created_at) VALUES ('existing', '2026-10-04T00:00:00Z')")
    before = [tuple(r) for r in conn.execute('SELECT * FROM sessions')]
    db.init_schema(conn)
    db.init_schema(conn)
    assert conn.execute('SELECT version FROM schema_version').fetchone()[0] == db.SCHEMA_VERSION
    assert [tuple(r) for r in conn.execute('SELECT * FROM sessions')] == before
    assert conn.execute('SELECT COUNT(*) FROM internal_request_questions').fetchone()[0] == 0
    assert conn.execute('PRAGMA foreign_key_check').fetchall() == []
    conn.close()


def test_v21_migration_adds_judgments_and_preserves_workspace(tmp_path):
    conn = db.connect(tmp_path / 'v21.sqlite')
    conn.executescript(db._SCHEMA.replace(db._V22_TABLES, '').replace(db._V23_TABLES, ''))
    conn.execute('INSERT INTO schema_version VALUES (21)')
    conn.execute("INSERT INTO sessions (session_id, created_at) VALUES ('existing', '2026-10-04T00:00:00Z')")
    before = [tuple(r) for r in conn.execute('SELECT * FROM sessions')]
    db.init_schema(conn)
    db.init_schema(conn)
    assert conn.execute('SELECT version FROM schema_version').fetchone()[0] == db.SCHEMA_VERSION
    assert [tuple(r) for r in conn.execute('SELECT * FROM sessions')] == before
    assert conn.execute('SELECT COUNT(*) FROM internal_request_judgments').fetchone()[0] == 0
    assert conn.execute('PRAGMA foreign_key_check').fetchall() == []
    conn.close()


def test_v22_migration_adds_resumptions_and_preserves_workspace(tmp_path):
    conn = db.connect(tmp_path / 'v22.sqlite')
    conn.executescript(db._SCHEMA.replace(db._V23_TABLES, ''))
    conn.execute('INSERT INTO schema_version VALUES (22)')
    conn.execute("INSERT INTO sessions (session_id, created_at) VALUES ('existing', '2026-10-04T00:00:00Z')")
    db.init_schema(conn)
    db.init_schema(conn)
    assert conn.execute('SELECT version FROM schema_version').fetchone()[0] == 23
    assert conn.execute('SELECT session_id FROM sessions').fetchone()[0] == 'existing'
    assert conn.execute('SELECT COUNT(*) FROM internal_request_resumptions').fetchone()[0] == 0
    assert conn.execute('PRAGMA foreign_key_check').fetchall() == []
    conn.close()
