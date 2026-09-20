"""모델 호출 비용 추정 — ARCHITECTURE "모델 호출 예산": usage 토큰을 실행마다 기록하고 설정된 단가로 누적한다.

단가(백만 토큰당 USD)는 설정값이다. 0 이면 추정값도 0 이며, 워커는 단가 미설정 시 usage 의 `estimated_usd` 를
NULL 로 남겨 "추정 불가" 를 구분한다.
"""

from sqlite3 import Connection


def estimate_usd(
    input_tokens: int, output_tokens: int, price_in_per_m: float, price_out_per_m: float
) -> float:
    return input_tokens / 1_000_000 * price_in_per_m + output_tokens / 1_000_000 * price_out_per_m


def total_estimated_usd(conn: Connection) -> float:
    """기록된 실행 전체의 추정 비용 합. 추정 불가(NULL)는 더하지 않는다."""
    return float(conn.execute("SELECT COALESCE(SUM(estimated_usd), 0) FROM usage").fetchone()[0])
