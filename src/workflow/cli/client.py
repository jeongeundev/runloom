"""HTTP 클라이언트 (ARCHITECTURE "CLI — phase 26" client.py).

요청 주소 = `API_PREFIX + path`, 헤더 `Authorization: Bearer <token>`. 자동 재시도 없음(D24).
토큰은 로그·예외 문구·`repr` 에 넣지 않는다.
"""

from collections.abc import Mapping
from typing import Any

import httpx

from workflow.contracts.v1 import API_PREFIX


class ApiError(Exception):
    """2xx 가 아닌 응답 — 서버 오류 본문의 `code`·`message`(없으면 `http_error`·상태 문구)."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(f"{status} {code}: {message}")
        self.status = status
        self.code = code
        self.message = message


class Unreachable(Exception):
    """연결 실패·시간 초과. `reason` 은 `연결 실패` | `시간 초과`."""

    def __init__(self, server: str, reason: str) -> None:
        super().__init__(f"{reason}: {server}")
        self.server = server
        self.reason = reason


class ApiClient:
    def __init__(self, server: str, token: str, *, http: httpx.Client | None = None,
                 timeout: float = 30.0) -> None:
        self._server = server
        self._headers = {"Authorization": f"Bearer {token}"}
        self._http = http if http is not None else httpx.Client(base_url=server, timeout=timeout)

    def __repr__(self) -> str:
        return f"ApiClient(server={self._server!r})"

    def get(self, path: str, params: Mapping[str, str] | None = None) -> Any:
        return self._request("GET", path, params=params)

    def put(self, path: str, json: Any = None) -> Any:
        return self._request("PUT", path, json=json)

    def post(self, path: str, json: Any = None) -> Any:
        return self._request("POST", path, json=json)

    def delete(self, path: str) -> Any:
        return self._request("DELETE", path)

    def _request(self, method: str, path: str, *, params: Mapping[str, str] | None = None,
                 json: Any = None) -> Any:
        try:
            response = self._http.request(method, API_PREFIX + path, params=params, json=json,
                                          headers=self._headers)
        except httpx.TimeoutException:
            raise Unreachable(self._server, "시간 초과") from None
        except httpx.TransportError:
            raise Unreachable(self._server, "연결 실패") from None
        if not response.is_success:
            raise _api_error(response)
        if response.status_code == 204 or not response.content:
            return None
        return response.json()


def _api_error(response: httpx.Response) -> ApiError:
    try:
        body = response.json()
    except ValueError:
        body = None
    if isinstance(body, dict) and isinstance(body.get("code"), str) and isinstance(body.get("message"), str):
        return ApiError(response.status_code, body["code"], body["message"])
    return ApiError(response.status_code, "http_error", response.reason_phrase)
