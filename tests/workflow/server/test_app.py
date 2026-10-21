"""app.py — 앱 팩토리. import 시 create_app() 은 WORKFLOW_SKIP_APP=1 이면 건너뛴다 (tests/conftest.py)."""

import importlib
import re
from pathlib import Path

from fastapi import FastAPI
from fastapi.routing import APIRoute
from starlette.routing import Mount

from workflow.adapters.db import SCHEMA_VERSION, connect
from workflow.contracts.v1 import API_PREFIX
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


def test_app_starts_without_any_diagnosis_setting(settings):
    """phase 10 — 진단 설정 없이 앱이 만들어지고 스키마를 연다. 진단은 공개 데모 전용(종료)이라 설정 칸도 없다 (ADR-0019)."""
    app = create_app(settings)
    assert isinstance(app, FastAPI)
    assert settings.db_path.exists()
    assert not hasattr(settings, "diag_api_token")


# --- GET /healthz (phase 10 step 5) ------------------------------------------------------------


def test_healthz_reports_schema_version_and_mode_without_auth(settings):
    """인증 없이 200. 비밀값·경로를 싣지 않는다 (ARCHITECTURE "헬스 확인")."""
    from fastapi.testclient import TestClient

    res = TestClient(create_app(settings)).get("/healthz")
    assert res.status_code == 200
    assert res.json() == {"status": "ok", "mode": "selfhost", "schema_version": SCHEMA_VERSION}
    for secret in (settings.session_secret, settings.operator_token):
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


# --- 문서 ↔ 라우트 대조 (phase 24 step 9, ARCHITECTURE "라우트 분류 규칙") ---------------------------------

ARCHITECTURE = Path(__file__).resolve().parents[3] / "docs" / "ARCHITECTURE.md"
# 화면 라우트(endpoint 모듈 workflow.server.web) 밖에서 /api/v1 아래가 아니어도 되는 것 — /healthz·FastAPI 기본 문서
NON_API_PATHS = {"/healthz": "workflow.server.app", "/openapi.json": "fastapi.applications",
                 "/docs": "fastapi.applications", "/docs/oauth2-redirect": "fastapi.applications",
                 "/redoc": "fastapi.applications"}


def _documented_api_routes() -> set[tuple[str, str]]:
    """ARCHITECTURE "주소 표" 절의 새 경로 칸(옮기는 39 + 새로 여는 15) — (메서드, 경로)."""
    text = ARCHITECTURE.read_text(encoding="utf-8")
    section = text[text.index("### 주소 표 (step 3·4·5·6·7)"):text.index("### 라우트 분류 규칙")]
    rows = re.findall(r"`(/api/v1/[^`]+)` \| (GET|POST|PUT|PATCH|DELETE) \|", section)
    assert len(rows) == 39 + 15, rows  # 표가 바뀌면 이 수도 같이 본다
    return {(method, path) for path, method in rows}


def _app_routes(settings) -> list:
    return create_app(settings).routes


def test_every_non_screen_route_is_under_api_v1(settings):
    """endpoint 모듈이 `workflow.server.web` 인 화면 라우트·/healthz·FastAPI 기본 문서·/static 마운트를 뺀 모든 라우트는
    `/api/v1/` 로 시작한다 — JSON 라우트가 접두어 없이 새로 붙으면 여기서 걸린다."""
    seen_non_api = {}
    outside = []
    for route in _app_routes(settings):
        if isinstance(route, Mount):
            assert route.path == "/static", route.path
            continue
        module = route.endpoint.__module__
        if module == "workflow.server.web":
            assert not route.path.startswith(API_PREFIX), route.path  # 화면 라우트는 /api/v1 아래에 두지 않는다
            continue
        if route.path in NON_API_PATHS:
            seen_non_api[route.path] = module
            continue
        if not route.path.startswith(f"{API_PREFIX}/"):
            outside.append((route.path, module))
    assert outside == []
    assert seen_non_api == NON_API_PATHS


def test_every_documented_api_route_exists(settings):
    actual = {(method, route.path) for route in _app_routes(settings) if isinstance(route, APIRoute)
              for method in route.methods}
    assert _documented_api_routes() - actual == set()


def test_every_api_route_is_documented(settings):
    """반대쪽 — /api/v1 아래 라우트(HEAD 제외)는 모두 주소 표에 있다."""
    actual = {(method, route.path) for route in _app_routes(settings) if isinstance(route, APIRoute)
              for method in route.methods if route.path.startswith(f"{API_PREFIX}/") and method != "HEAD"}
    assert actual - _documented_api_routes() == set()
