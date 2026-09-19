"""adapters 테스트 공용 fixture — 실제 파일 DB 를 쓴다 (`:memory:` 는 WAL·연결 분리를 검증하지 못한다)."""

import pytest

from workflow.adapters.artifact_store import ArtifactStore
from workflow.adapters.db import connect, init_schema

NOW = "2026-09-20T00:00:00Z"


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "central.sqlite"


@pytest.fixture
def conn(db_path):
    c = connect(db_path)
    init_schema(c)
    yield c
    c.close()


@pytest.fixture
def store(tmp_path):
    return ArtifactStore(tmp_path / "artifacts")
