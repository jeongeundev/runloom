"""중앙 기계 API 클라이언트 — 연결 프로그램 → 중앙 (CONTRACT 2절 claim, 3절 이벤트, 4절 산출물).

`workflow.server` 를 import 하지 않는다. 계약 모델만 공유한다.
- 연결 오류·시간 초과·5xx 는 `Unreachable` — 호출자가 보류하고 다음 tick 에 같은 ID·seq 로 재시도한다.
- 4xx 는 `CentralError`(status, body). 401 `Unauthenticated`, 409 `sequence_gap` → `SequenceGapError(expected_seq)`,
  409 `event_conflict` → `EventConflictError`.
- 토큰은 헤더에만 넣고 예외 메시지·repr 에 넣지 않는다.
"""

from typing import Any
from urllib.parse import quote

import httpx
from pydantic import ValidationError

from workflow.contracts.v1 import (
    CONTRACT_VERSION,
    ArtifactCreated,
    ArtifactMeta,
    ErrorBody,
    EventAck,
    ExecutionEvent,
    ExecutionRequest,
)


class Unreachable(Exception):
    """연결 오류·시간 초과·5xx. 호출자가 보류·재시도한다."""


class CentralError(Exception):
    """4xx. `body` 는 CONTRACT 오류 본문 (JSON 이 아니면 `http_{status}` 로 감싼다)."""

    def __init__(self, status: int, body: ErrorBody):
        super().__init__(f"HTTP {status} {body.code}: {body.message}")
        self.status = status
        self.body = body


class Unauthenticated(CentralError):
    """401 — 토큰 없음·만료·취소."""


class SequenceGapError(CentralError):
    """409 `sequence_gap` — `expected_seq` 부터 다시 보낸다."""

    def __init__(self, status: int, body: ErrorBody):
        super().__init__(status, body)
        self.expected_seq = int((body.details or {})["expected_seq"])


class EventConflictError(CentralError):
    """409 `event_conflict` — 같은 seq 에 다른 내용이 이미 있다."""


def _central_error(response: httpx.Response) -> CentralError:
    try:
        body = ErrorBody.model_validate(response.json())
    except (ValueError, ValidationError):
        body = ErrorBody(
            code=f"http_{response.status_code}", message=response.text[:200], field=None, details=None
        )
    if response.status_code == 401:
        return Unauthenticated(response.status_code, body)
    if response.status_code == 409 and body.code == "sequence_gap" and body.details:
        return SequenceGapError(response.status_code, body)
    if response.status_code == 409 and body.code == "event_conflict":
        return EventConflictError(response.status_code, body)
    return CentralError(response.status_code, body)


def _segment(value: str) -> str:
    # ID 는 불투명 문자열이다. 경로 구분자가 섞여 있어도 한 세그먼트로만 보낸다.
    return quote(value, safe="")


class CentralClient:
    def __init__(
        self,
        server: str,
        token: str | None,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 15.0,
    ):
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        self._server = server
        self._client = httpx.Client(base_url=server, headers=headers, transport=transport, timeout=timeout)

    def __repr__(self) -> str:
        return f"CentralClient(server={self._server!r})"

    def _call(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        try:
            response = self._client.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise Unreachable(f"{method} {path}: {type(exc).__name__}") from exc
        if response.status_code >= 500:
            raise Unreachable(f"{method} {path}: HTTP {response.status_code}")
        if response.status_code >= 400:
            raise _central_error(response)
        return response

    def exchange(self, connect_code: str) -> tuple[str, str]:
        """연결 코드 → (connector_id, 연결 토큰). 인증 없이 호출한다."""
        response = self._call(
            "POST", "/connector/exchange",
            json={"contract_version": CONTRACT_VERSION, "connect_code": connect_code},
        )
        data = response.json()
        return data["connector_id"], data["token"]

    def claim(self, connector_id: str) -> ExecutionRequest | None:
        response = self._call(
            "POST", "/connector/claim",
            json={"contract_version": CONTRACT_VERSION, "connector_id": connector_id},
        )
        if response.status_code == 204:
            return None
        return ExecutionRequest.model_validate(response.json())

    def heartbeat(self, connector_id: str, current_execution_id: str | None) -> None:
        self._call(
            "POST", "/connector/heartbeat",
            json={
                "contract_version": CONTRACT_VERSION, "connector_id": connector_id,
                "current_execution_id": current_execution_id,
            },
        )

    def report_registration(self, connector_id: str, registration: dict) -> dict:
        """서버 `RegistrationRequest` 필드만 보낸다. 저장소 경로·검증 명령은 로컬에만 있다."""
        body = {
            "contract_version": CONTRACT_VERSION,
            "connector_id": connector_id,
            "local_registration_id": registration["local_registration_id"],
            "tool": registration["tool"],
            "repository_id": registration["repository_id"],
            "base_commit": registration["base_commit"],
            "verification_profile_ids": list(registration["verification_profile_ids"]),
            "discovered": registration["discovered"],
        }
        return self._call("POST", "/connector/registrations", json=body).json()

    def post_event(self, event: ExecutionEvent) -> EventAck:
        response = self._call(
            "POST", f"/executions/{_segment(event.execution_id)}/events", json=event.model_dump(mode="json")
        )
        return EventAck.model_validate(response.json())

    def download_artifact(self, execution_id: str, artifact_id: str) -> tuple[bytes, str]:
        response = self._call(
            "GET", f"/executions/{_segment(execution_id)}/artifacts/{_segment(artifact_id)}"
        )
        return response.content, response.headers.get("content-type", "application/octet-stream")

    def upload_artifact(self, execution_id: str, meta: ArtifactMeta, data: bytes) -> ArtifactCreated:
        response = self._call(
            "POST", f"/executions/{_segment(execution_id)}/artifacts",
            data={"meta": meta.model_dump_json()},
            files={"file": (meta.name, data, meta.content_type)},
        )
        return ArtifactCreated.model_validate(response.json())
