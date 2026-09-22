"""callback HTTP 클라이언트 — 중앙 워커 → n8n Wait 노드 (ADR-0010). 본문·응답·헤더를 로그·예외 메시지에 넣지 않는다.

리다이렉트를 따라가지 않는다 (`follow_redirects=False`) — 허용 목록(`WORKFLOW_CALLBACK_HOSTS`)이 리다이렉트로 우회되지 않게.
실패는 모두 `CallbackFailed` 하나다. 재시도 여부·간격은 워커(`worker._deliver_callbacks`)가 정한다.
"""

from typing import Any, Protocol

import httpx


class CallbackFailed(Exception):
    """연결 오류·시간 초과·2xx 아님. 메시지는 `HTTP 404` / `연결 오류: <예외 클래스 이름>` 처럼 짧다 — `callback_last_error` 에 그대로 남는다."""


class CallbackClient(Protocol):
    def post(self, url: str, payload: dict[str, Any]) -> None: ...


class HttpCallbackClient:
    def __init__(self, timeout_seconds: float = 10.0, transport: httpx.BaseTransport | None = None):
        self._client = httpx.Client(timeout=timeout_seconds, transport=transport, follow_redirects=False)

    def post(self, url: str, payload: dict[str, Any]) -> None:
        try:
            response = self._client.post(url, json=payload)
        except httpx.HTTPError as exc:
            raise CallbackFailed(f"연결 오류: {type(exc).__name__}") from exc
        if not 200 <= response.status_code < 300:
            raise CallbackFailed(f"HTTP {response.status_code}")
