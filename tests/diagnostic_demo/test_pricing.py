"""비용 추정 — usage 토큰 × 설정 단가. 단가는 실연동 전 공식 가격에서 확인해 설정값으로 넣는다."""

import pytest

from diagnostic_demo import db
from diagnostic_demo.pricing import estimate_usd, total_estimated_usd
from tests.diagnostic_demo.conftest import diagnosis_request
from workflow.contracts.v1 import ExecutionRequest

NOW = "2026-09-20T00:00:00.000000Z"


def test_estimate_usd_is_per_million_tokens():
    assert estimate_usd(1_000_000, 0, 0.4, 1.6) == pytest.approx(0.4)
    assert estimate_usd(0, 1_000_000, 0.4, 1.6) == pytest.approx(1.6)
    assert estimate_usd(50_000, 5_000, 0.4, 1.6) == pytest.approx(0.02 + 0.008)


def test_zero_prices_estimate_zero():
    assert estimate_usd(80_000, 8_000, 0.0, 0.0) == 0.0


def test_total_sums_recorded_usage_and_ignores_unpriced(diag_conn):
    assert total_estimated_usd(diag_conn) == 0.0
    for n, usd in ((1, 1.25), (2, None), (3, 0.75)):
        execution_id = f"exec-{n}"
        db.insert_run(diag_conn, ExecutionRequest.model_validate(diagnosis_request(execution_id)), NOW)
        db.record_usage(diag_conn, execution_id, "gpt-test", 10, 1, 1, usd, NOW)
    assert total_estimated_usd(diag_conn) == pytest.approx(2.0)
