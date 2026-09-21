"""e2e 공용 — `stack` fixture(대본 에이전트 로컬 스택)와, 실패한 테스트의 출력에 `LocalStack` 로그(workdir/logs/*.log)
꼬리를 덧붙이는 훅.

스택은 모듈당 한 번 뜬다. `LocalStack(scripted=True)`: `codex`·`claude` 는 `workflow.scripted.*` 래퍼, 진단은
`DIAG_MODEL=fake`, seed 는 `--scripted`(카탈로그 3개에 "시연용 · 대본 재생"). 대본 속도 `WORKFLOW_SCRIPT_PACE_SECONDS`
는 0 으로 고정한다 — 부모 환경의 값(배포 25초)이 e2e 를 느리게 하지 않게.
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from local_stack import LocalStack  # noqa: E402

from workflow.scripted._common import PACE_ENV  # noqa: E402


@pytest.fixture(scope="module")
def stack(tmp_path_factory) -> LocalStack:
    with pytest.MonkeyPatch.context() as env:
        env.setenv(PACE_ENV, "0")
        with LocalStack(tmp_path_factory.mktemp("stack"), fake_codex=None, scripted=True) as running:
            yield running


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    if not report.failed:
        return
    stack = item.funcargs.get("stack") if hasattr(item, "funcargs") else None
    if stack is None:
        return
    for name, tail in stack.log_tails().items():
        report.sections.append((f"stack log: {name}", tail))
