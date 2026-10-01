"""중앙 웹/API 앱 팩토리.

- `create_app(settings)`: DB 스키마 초기화, 산출물 저장소, 오류 변환, 기계 API 라우터, 입구 API 라우터, GitHub 설정 API 라우터, 사람 요청 응답 API 라우터, 지표 API 라우터, 웹 라우터, `GET /healthz`.
- 모듈 변수 `app` 은 `python3 -m uvicorn workflow.server.app:app` 진입점이며 import 시 환경변수를
  읽는다. 비밀값 없이는 뜨지 않는 것이 의도다. 테스트는 `WORKFLOW_SKIP_APP=1` 로 이 호출을 건너뛰고
  `create_app(settings)` 를 직접 쓴다 (tests/conftest.py).
"""

import os
import sqlite3
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from workflow.adapters.artifact_store import ArtifactStore
from workflow.adapters.db import SCHEMA_VERSION, connect, init_schema
from workflow.adapters.secret_store import SecretStore
from workflow.server import github_api, human_api, inbound_api, machine_api, mapping_api, metrics_api, web
from workflow.server.auth import LoginThrottle
from workflow.server.errors import install_error_handlers
from workflow.server.settings import Settings, load_settings

STATIC_DIR = Path(__file__).parent / "static"


def create_app(settings: Settings | None = None) -> FastAPI:
    if settings is None:
        settings = load_settings()
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    settings.artifact_dir.mkdir(parents=True, exist_ok=True)
    conn = connect(settings.db_path)
    try:
        init_schema(conn)
    finally:
        conn.close()

    app = FastAPI(title="workflow central")
    app.state.settings = settings
    # 요청마다 새 연결 (auth.get_conn). sqlite3 연결을 스레드 간 공유하지 않는다.
    app.state.conn_factory = lambda: connect(settings.db_path)
    app.state.store = ArtifactStore(settings.artifact_dir)
    app.state.login_throttle = LoginThrottle()  # 로그인 연속 실패 제한 — 키(이메일·setup·recover)별, 프로세스 메모리
    app.state.github_client = None  # 기준선 가져오기 — None 이면 요청 때 Settings 로 만든다. 테스트는 가짜로 바꾼다
    app.state.secrets = SecretStore(settings.secret_dir)  # GitHub App·PAT 비밀 파일 (ADR-0017)
    app.state.github_transport = None  # GitHub 연결 경로의 httpx transport — 테스트는 가짜 GitHub 로 바꾼다
    app.state.jira_transport = None  # Jira 연결 경로의 httpx transport — 테스트는 가짜 Jira 로 바꾼다
    app.state.notify_transport = None  # 알림 [테스트 보내기] 의 httpx transport — 테스트는 가짜 수신으로 바꾼다
    install_error_handlers(app)
    app.include_router(machine_api.router)
    app.include_router(inbound_api.router)  # n8n 입구 (ADR-0010) — 입구 토큰만, 세션 쿠키 없음
    app.include_router(github_api.router)  # GitHub 소스 설정 (ADR-0014) — 운영자 세션만
    app.include_router(human_api.router)  # 사람 요청 응답 (ADR-0014) — 운영자 세션만
    app.include_router(metrics_api.router)  # 지표·기준선 가져오기 (ADR-0015) — 운영자 세션만
    app.include_router(mapping_api.router)  # 매핑 표 (ADR-0020) — 운영자 세션만
    web.install(app)  # 라우터 + PageError → error.html
    app.middleware("http")(web.origin_guard)  # 쿠키 인증 변경 요청의 Origin 검사 (phase 15) — Bearer 경로 제외
    app.add_api_route("/healthz", lambda: _healthz(settings), methods=["GET"])  # 인증 없음 (ADR-0016)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")  # style.css 만. CDN 없음
    return app


def _healthz(settings: Settings) -> JSONResponse:
    """DB 를 읽기 전용으로 열어 schema_version 을 확인한다. 없는 DB 를 만들지 않고, 오류 내용·경로는 싣지 않는다.
    `mode` 는 호환을 위한 고정값 `selfhost` (ADR-0019)."""
    try:
        conn = sqlite3.connect(f"{settings.db_path.resolve().as_uri()}?mode=ro", uri=True)
        try:
            version = conn.execute("SELECT version FROM schema_version").fetchone()[0]
        finally:
            conn.close()
    except (sqlite3.Error, TypeError):
        version = None
    if version != SCHEMA_VERSION:
        return JSONResponse({"status": "error", "mode": "selfhost"}, status_code=503)
    return JSONResponse({"status": "ok", "mode": "selfhost", "schema_version": version})


app = None if os.environ.get("WORKFLOW_SKIP_APP") == "1" else create_app()
