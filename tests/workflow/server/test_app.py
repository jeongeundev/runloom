"""app.py — 앱 팩토리. import 시 create_app() 은 WORKFLOW_SKIP_APP=1 이면 건너뛴다 (tests/conftest.py)."""

import importlib

from fastapi import FastAPI

from workflow.adapters.db import connect
from workflow.server import app as app_module
from workflow.server.app import create_app
from workflow.server.settings import Settings


def test_module_level_app_is_skipped_under_test_env():
    assert app_module.app is None


def test_create_app_initialises_schema_and_directories(settings):
    app = create_app(settings)
    assert isinstance(app, FastAPI)
    assert app.state.settings is settings
    assert settings.artifact_dir.is_dir()
    conn = connect(settings.db_path)
    try:
        assert conn.execute("SELECT version FROM schema_version").fetchone()[0] == 1
    finally:
        conn.close()


def test_conn_factory_opens_a_new_connection_each_time(settings):
    app = create_app(settings)
    a = app.state.conn_factory()
    b = app.state.conn_factory()
    try:
        assert a is not b
    finally:
        a.close()
        b.close()


def test_create_app_without_settings_reads_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKFLOW_DEV", "1")
    monkeypatch.setenv("WORKFLOW_DB_PATH", str(tmp_path / "db.sqlite"))
    monkeypatch.setenv("WORKFLOW_ARTIFACT_DIR", str(tmp_path / "art"))
    app = create_app()
    assert isinstance(app.state.settings, Settings)
    assert (tmp_path / "db.sqlite").exists()


def test_module_import_creates_app_when_not_skipped(monkeypatch, tmp_path):
    monkeypatch.delenv("WORKFLOW_SKIP_APP", raising=False)
    monkeypatch.setenv("WORKFLOW_DEV", "1")
    monkeypatch.setenv("WORKFLOW_DB_PATH", str(tmp_path / "db.sqlite"))
    monkeypatch.setenv("WORKFLOW_ARTIFACT_DIR", str(tmp_path / "art"))
    reloaded = importlib.reload(app_module)
    try:
        assert isinstance(reloaded.app, FastAPI)
    finally:
        monkeypatch.setenv("WORKFLOW_SKIP_APP", "1")
        importlib.reload(app_module)
