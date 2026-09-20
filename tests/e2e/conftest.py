"""e2e 공용 — 실패한 테스트의 출력에 `LocalStack` 로그(workdir/logs/*.log) 꼬리를 덧붙인다."""

import pytest


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
