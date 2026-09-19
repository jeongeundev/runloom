"""중앙 웹/API 앱 팩토리.

- `create_app(settings)`: DB 스키마 초기화, 산출물 저장소, 오류 변환, 기계 API 라우터.
- 모듈 변수 `app` 은 `python3 -m uvicorn workflow.server.app:app` 진입점이며 import 시 환경변수를
  읽는다. 비밀값 없이는 뜨지 않는 것이 의도다. 테스트는 `WORKFLOW_SKIP_APP=1` 로 이 호출을 건너뛰고
  `create_app(settings)` 를 직접 쓴다 (tests/conftest.py).
"""

import os

from fastapi import FastAPI

from workflow.adapters.artifact_store import ArtifactStore
from workflow.adapters.db import connect, init_schema
from workflow.server import machine_api
from workflow.server.errors import install_error_handlers
from workflow.server.settings import Settings, load_settings


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
    install_error_handlers(app)
    app.include_router(machine_api.router)
    return app


app = None if os.environ.get("WORKFLOW_SKIP_APP") == "1" else create_app()
