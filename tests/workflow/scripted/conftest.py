"""scripted 테스트 공용 — 데모 저장소 worktree(scaffold), 인계 디렉터리, connector 가 stdin 으로 주는 실제 프롬프트.

대본 속도 `WORKFLOW_SCRIPT_PACE_SECONDS` 는 모든 테스트에서 0 이다 (테스트 시간). 0 이 아닌 값을 넣지 않는다.
실제 `codex`·`claude`·OpenAI 는 호출하지 않는다.
"""

import json
import sys
from pathlib import Path

import pytest

from tests.workflow.connector.conftest import make_request
from workflow.connector.prompt import build_prompt
from workflow.scripted._common import PACE_ENV

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))
from scaffold_demo_repo import scaffold  # noqa: E402

RESPONSE_AFTER = {
    "report_date": "2026-09-19",
    "data": {"records": [
        {"team": "운영", "completed": 12, "pending": 3},
        {"team": "개발", "completed": 8, "pending": 2},
    ]},
}


@pytest.fixture(autouse=True)
def no_pace(monkeypatch):
    monkeypatch.setenv(PACE_ENV, "0")


@pytest.fixture
def worktree(tmp_path) -> Path:
    """수정 전 데모 저장소 — scripts/scaffold_demo_repo.py 가 만드는 것과 같다."""
    repo = tmp_path / "demo-report-repo"
    scaffold(repo)
    return repo


@pytest.fixture
def handoff(tmp_path) -> Path:
    handoff = tmp_path / "fix-daily-0920.handoff"
    handoff.mkdir()
    (handoff / "manifest.json").write_text("{}")
    (handoff / "response-after@1.json").write_text(json.dumps(RESPONSE_AFTER, ensure_ascii=False))
    (handoff / "log-daily-0920@1.txt").write_text("ERROR code=MISSING_RECORDS_FIELD\n")
    return handoff


def prompt_for(handoff: Path, worktree: Path) -> str:
    """connector 가 실제로 stdin 에 넣는 프롬프트 (`prompt.build_prompt`)."""
    return build_prompt(make_request(), handoff, worktree)
