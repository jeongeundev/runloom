"""app.py — 앱 팩토리. import 시 create_app() 은 WORKFLOW_SKIP_APP=1 이면 건너뛴다 (tests/conftest.py)."""

import importlib

from fastapi import FastAPI

from workflow.adapters.db import SCHEMA_VERSION, connect
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
        assert conn.execute("SELECT version FROM schema_version").fetchone()[0] == SCHEMA_VERSION
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


def test_selfhost_without_diag_token_starts(settings):
    """phase 10 — 진단 토큰 없이도 앱이 만들어지고 스키마를 연다."""
    from dataclasses import replace

    app = create_app(replace(settings, diag_api_token=""))
    assert isinstance(app, FastAPI)
    assert settings.db_path.exists()


# --- GET /healthz (phase 10 step 5) ------------------------------------------------------------


def test_healthz_reports_schema_version_and_mode_without_auth(settings):
    """인증 없이 200. 비밀값·경로를 싣지 않는다 (ARCHITECTURE "헬스 확인")."""
    from fastapi.testclient import TestClient

    res = TestClient(create_app(settings)).get("/healthz")
    assert res.status_code == 200
    assert res.json() == {"status": "ok", "mode": "selfhost", "schema_version": SCHEMA_VERSION}
    for secret in (settings.session_secret, settings.operator_token, settings.diag_api_token):
        assert secret not in res.text
    assert str(settings.db_path) not in res.text
    assert "set-cookie" not in res.headers  # 세션 쿠키를 만들지 않는다


def test_healthz_errors_without_details_when_db_is_missing(settings):
    from fastapi.testclient import TestClient

    client = TestClient(create_app(settings))
    settings.db_path.unlink()
    for suffix in ("-wal", "-shm"):
        settings.db_path.with_name(settings.db_path.name + suffix).unlink(missing_ok=True)
    res = client.get("/healthz")
    assert res.status_code == 503
    assert res.json() == {"status": "error", "mode": "selfhost"}
    assert not settings.db_path.exists()  # 확인만 한다 — 빈 DB 를 만들지 않는다


def test_healthz_errors_on_unexpected_schema_version(settings):
    from fastapi.testclient import TestClient

    client = TestClient(create_app(settings))
    conn = connect(settings.db_path)
    try:
        conn.execute("UPDATE schema_version SET version = ?", (SCHEMA_VERSION + 1,))
    finally:
        conn.close()
    res = client.get("/healthz")
    assert res.status_code == 503
    assert res.json() == {"status": "error", "mode": "selfhost"}
    assert str(settings.db_path) not in res.text
