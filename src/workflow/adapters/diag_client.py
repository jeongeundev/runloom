"""진단 API HTTP 클라이언트 — 중앙 워커 → 진단 데모 서비스 (CONTRACT 1절, ARCHITECTURE 인증 절).

`Authorization: Bearer <DIAG_API_TOKEN>` 은 생성자로 받은 값만 쓰고 로그·예외 메시지에 넣지 않는다.
연결 오류·5xx 는 `DiagUnavailable` — 워커가 같은 execution_id 로 다음 tick 에 재시도한다.
4xx 는 `DiagRejected`(409 `DiagConflict`, 429 `DiagLimit`) — 재시도해도 같으므로 워커가 실행을 failed 로 확정한다.
"""

from typing import Any, Protocol
from urllib.parse import quote

import httpx
from pydantic import ValidationError

from workflow.contracts.v1 import ErrorBody, ExecutionRequest, RunStatus


class DiagError(Exception):
    pass


class DiagUnavailable(DiagError):
    """연결 오류·시간 초과·5xx. 다음 tick 에 같은 실행 ID 로 재시도한다."""


class DiagRejected(DiagError):
    """4xx. `body` 는 CONTRACT 오류 본문 (JSON 이 아니면 `http_{status}` 로 감싼다)."""

    def __init__(self, status: int, body: ErrorBody):
        super().__init__(f"HTTP {status} {body.code}: {body.message}")
        self.status = status
        self.body = body


class DiagConflict(DiagRejected):
    """409 — 같은 execution_id 가 다른 내용으로 접수돼 있다."""


class DiagLimit(DiagRejected):
    """429 — 일일·총액 상한 (CONTRACT 10절)."""


class DiagClient(Protocol):
    def submit(self, request: ExecutionRequest) -> RunStatus: ...

    def status(self, execution_id: str, after_seq: int) -> RunStatus: ...

    def download(self, execution_id: str, artifact_id: str) -> tuple[bytes, str]: ...

    def capabilities(self) -> dict: ...


def _rejected(response: httpx.Response) -> DiagRejected:
    try:
        body = ErrorBody.model_validate(response.json())
    except (ValueError, ValidationError):
        body = ErrorBody(
            code=f"http_{response.status_code}", message=response.text[:200], field=None, details=None
        )
    if response.status_code == 409:
        return DiagConflict(response.status_code, body)
    if response.status_code == 429:
        return DiagLimit(response.status_code, body)
    return DiagRejected(response.status_code, body)


class HttpDiagClient:
    def __init__(
        self,
        base_url: str,
        token: str,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 10.0,
    ):
        self._client = httpx.Client(
            base_url=base_url,
            headers={"Authorization": f"Bearer {token}"},
            transport=transport,
            timeout=timeout,
        )

    def _call(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        try:
            response = self._client.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise DiagUnavailable(f"{method} {path}: {type(exc).__name__}") from exc
        if response.status_code >= 500:
            raise DiagUnavailable(f"{method} {path}: HTTP {response.status_code}")
        if response.status_code >= 400:
            raise _rejected(response)
        return response

    def submit(self, request: ExecutionRequest) -> RunStatus:
        response = self._call("POST", "/runs", json=request.model_dump(mode="json"))
        return RunStatus.model_validate(response.json())

    def status(self, execution_id: str, after_seq: int = 0) -> RunStatus:
        response = self._call(
            "GET", f"/runs/{quote(execution_id, safe='')}", params={"after_seq": after_seq}
        )
        return RunStatus.model_validate(response.json())

    def download(self, execution_id: str, artifact_id: str) -> tuple[bytes, str]:
        # ID 는 불투명 문자열이다. 경로 구분자가 섞여 있어도 한 세그먼트로만 보낸다.
        response = self._call(
            "GET",
            f"/runs/{quote(execution_id, safe='')}/artifacts/{quote(artifact_id, safe='')}",
        )
        return response.content, response.headers.get("content-type", "application/octet-stream")

    def capabilities(self) -> dict:
        return self._call("GET", "/capabilities").json()
