"""진단 데모 도구 테스트의 공용 fixture.

실제 패키지 fixture(`src/diagnostic_demo/fixtures/`)를 그대로 읽는다. 자료 누락·교체·일시 오류는
`FixtureStore` 의 `removed`·`replaced`·`unavailable` 로 재현하고 파일을 고치지 않는다.
"""

import json
from pathlib import Path

import pytest

from diagnostic_demo.tools.store import FixtureStore

SRC = Path(__file__).resolve().parents[3] / "src"
FIXTURE_ROOT = SRC / "diagnostic_demo" / "fixtures"
ALLOWED = frozenset({"daily-report"})


@pytest.fixture
def fixture_root() -> Path:
    return FIXTURE_ROOT


@pytest.fixture
def index() -> dict:
    return json.loads((FIXTURE_ROOT / "index.json").read_text(encoding="utf-8"))


@pytest.fixture
def store() -> FixtureStore:
    return FixtureStore(FIXTURE_ROOT, ALLOWED)


@pytest.fixture
def denied_store() -> FixtureStore:
    """daily-report 가 허용 범위 밖인 저장소 — 범위 검사용."""
    return FixtureStore(FIXTURE_ROOT, frozenset({"other-report"}))
