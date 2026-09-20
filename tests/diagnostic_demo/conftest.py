"""진단 데모 서비스 테스트의 공용 fixture — tmp_path 의 실제 DB·산출물 디렉터리와 패키지 fixture.

OpenAI 를 실제로 호출하는 테스트는 없다 (ADR-0003 확정 조건 전 유료 호출 금지). 모델은 `FakeModelClient`,
OpenAI SDK 파싱은 가짜 응답 객체로만 검사한다. `tests/diagnostic_demo/tools/conftest.py` 의 `store` 는 그대로 두고
여기서는 이름이 겹치지 않는 fixture 만 둔다.
"""

import json
import re
from pathlib import Path

import pytest

from diagnostic_demo.artifact_store import ArtifactStore
from diagnostic_demo.db import connect, init_schema
from diagnostic_demo.settings import FAKE_MODEL_ID, Settings
from diagnostic_demo.tools.store import FixtureStore

ROOT = Path(__file__).resolve().parents[2]
FIXTURE_ROOT = ROOT / "src" / "diagnostic_demo" / "fixtures"
CONTRACT_MD = ROOT / "docs" / "CONTRACT.md"
_FENCE = re.compile(r"```json\n(.*?)\n```", re.DOTALL)
_INLINE = re.compile(r"\|\s*`(\{.*?\})`\s*\|")  # 3절 오류표의 인라인 JSON

EXEC_A = "exec-diagnose-001"
TOKEN = "test-diag-token"


def contract_blocks() -> list[dict]:
    text = CONTRACT_MD.read_text(encoding="utf-8")
    return [json.loads(m) for m in _FENCE.findall(text)] + [json.loads(m) for m in _INLINE.findall(text)]


def contract_request() -> dict:
    """CONTRACT 1절 진단 실행 요청."""
    (block,) = [b for b in contract_blocks() if b.get("kind") == "diagnosis" and "execution_id" in b]
    return block


def contract_results() -> tuple[dict, dict]:
    """CONTRACT 5절(ready_for_handoff)·6절(needs_information) 결과."""
    results = [b for b in contract_blocks() if {"outcome", "findings"} <= set(b)]
    assert [r["outcome"] for r in results] == ["ready_for_handoff", "needs_information"]
    return results[0], results[1]


def diagnosis_request(execution_id: str = EXEC_A, run_id: str = "daily-0920-0900") -> dict:
    body = dict(contract_request())
    body["execution_id"] = execution_id
    body["target"] = {"run_id": run_id}
    return body


@pytest.fixture
def diag_settings(tmp_path) -> Settings:
    return Settings(
        db_path=tmp_path / "diag.sqlite",
        artifact_dir=tmp_path / "diag-artifacts",
        api_token=TOKEN,
        model="fake",
        model_id=FAKE_MODEL_ID,
    )


@pytest.fixture
def diag_conn(diag_settings):
    diag_settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(diag_settings.db_path)
    init_schema(conn)
    yield conn
    conn.close()


@pytest.fixture
def artifact_store(diag_settings) -> ArtifactStore:
    return ArtifactStore(diag_settings.artifact_dir)


@pytest.fixture
def fixture_store() -> FixtureStore:
    return FixtureStore(FIXTURE_ROOT, frozenset({"daily-report"}))
