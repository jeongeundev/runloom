"""알림 웹훅 HTTP 경계 — 중앙 워커 → 등록된 URL 하나 (ADR-0018 결정 5). 본문·응답·URL 을 로그·예외 메시지에 넣지 않는다.

URL 은 토큰을 담는다(Discord 웹훅 URL 자체가 비밀). httpx 는 요청마다 URL 을 INFO 로그로 남기므로 보내는 동안 그 로그를 막는다.
리다이렉트를 따라가지 않는다. 실패는 모두 `NotifyFailed` 하나 — 재시도 여부·간격은 워커(`worker._deliver_notifications`)가 정한다.
"""

import contextvars
import logging
from typing import Any

import httpx

_sending = contextvars.ContextVar("notify_sending", default=False)


class _HideWhileSending(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return not _sending.get()


logging.getLogger("httpx").addFilter(_HideWhileSending())


class NotifyFailed(Exception):
    """연결 오류·시간 초과·2xx 아님. 메시지는 `HTTP 429` / `연결 오류: <예외 클래스 이름>` 처럼 짧다 — `last_error` 에 그대로 남는다.
    `retry_after` 는 429 가 알려 준 대기 초(본문 `retry_after` — Discord — 또는 `Retry-After` 헤더), 모르면 None."""

    def __init__(self, message: str, retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after


def _retry_after(response: httpx.Response) -> float | None:
    try:
        value = response.json().get("retry_after")
    except (ValueError, AttributeError):
        value = None
    if value is None:
        value = response.headers.get("retry-after")
    try:
        return float(value) if value is not None else None
    except ValueError:
        return None


class NotifySender:
    def __init__(self, *, timeout_seconds: float = 10.0, transport: httpx.BaseTransport | None = None):
        self._client = httpx.Client(timeout=timeout_seconds, transport=transport, follow_redirects=False)

    def post(self, url: str, body: dict[str, Any]) -> None:
        token = _sending.set(True)
        try:
            response = self._client.post(url, json=body)
        except (httpx.HTTPError, httpx.InvalidURL) as exc:
            raise NotifyFailed(f"연결 오류: {type(exc).__name__}") from None
        finally:
            _sending.reset(token)
        if not 200 <= response.status_code < 300:
            retry_after = _retry_after(response) if response.status_code == 429 else None
            raise NotifyFailed(f"HTTP {response.status_code}", retry_after)
