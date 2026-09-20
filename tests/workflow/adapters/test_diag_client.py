"""diag_client — 중앙 워커 → 진단 API HTTP 클라이언트 (CONTRACT 1절). 실제 서버 없이 MockTransport 로 검사한다."""

import json

import httpx
import pytest

from workflow.adapters.diag_client import (
    DiagConflict,
    DiagError,
    DiagLimit,
    DiagRejected,
    DiagUnavailable,
    HttpDiagClient,
)
from workflow.contracts.v1 import ExecutionRequest, RunStatus

TOKEN = "test-diag-token"
EXEC = "exec-diagnose-001"

REQUEST = ExecutionRequest.model_validate({
    "contract_version": 1,
    "execution_id": EXEC,
    "task_id": "diagnose-daily-0920",
    "kind": "diagnosis",
    "agent_id": "agent-ops-demo",
    "task_revision": 1,
    "request": "실패 원인과 수정에 필요한 근거를 조사해 주세요.",
    "input_artifact_ids": [],
    "target": {"run_id": "daily-0920-0900"},
})

ACCEPTED = {
    "execution_id": EXEC,
    "status": "accepted",
    "last_event_seq": 1,
    "result_artifact_id": None,
    "error": None,
}


def _client(handler) -> HttpDiagClient:
    return HttpDiagClient("http://diag.test", TOKEN, transport=httpx.MockTransport(handler))


def _recording(status_code: int, body=None, *, content: bytes | None = None, headers=None):
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if content is not None:
            return httpx.Response(status_code, content=content, headers=headers or {})
        return httpx.Response(status_code, json=body, headers=headers or {})

    return handler, calls


# --- submit -------------------------------------------------------------------------


def test_submit_posts_request_with_bearer_and_returns_run_status():
    handler, calls = _recording(202, ACCEPTED)

    status = _client(handler).submit(REQUEST)

    assert status == RunStatus.model_validate(ACCEPTED)
    request = calls[0]
    assert (request.method, request.url.path) == ("POST", "/runs")
    assert request.headers["authorization"] == f"Bearer {TOKEN}"
    assert json.loads(request.content) == REQUEST.model_dump(mode="json")


def test_submit_accepts_200_for_resubmission():
    handler, _ = _recording(200, {**ACCEPTED, "status": "running", "last_event_seq": 3})

    assert _client(handler).submit(REQUEST).status == "running"


def test_submit_409_is_conflict_with_error_body():
    body = {
        "code": "execution_conflict",
        "message": "execution_id exec-diagnose-001은 다른 내용으로 이미 접수되었습니다.",
        "field": "target.run_id",
        "details": None,
    }
    handler, _ = _recording(409, body)

    with pytest.raises(DiagConflict) as exc:
        _client(handler).submit(REQUEST)
    assert exc.value.body.code == "execution_conflict"
    assert exc.value.status == 409
    assert isinstance(exc.value, DiagRejected)


def test_submit_429_is_limit_with_error_body():
    body = {
        "code": "budget_exhausted",
        "message": "진단 총액 상한에 도달했습니다.",
        "field": None,
        "details": {"estimated_usd": 27.3, "limit_usd": 30},
    }
    handler, _ = _recording(429, body)

    with pytest.raises(DiagLimit) as exc:
        _client(handler).submit(REQUEST)
    assert exc.value.body.details == {"estimated_usd": 27.3, "limit_usd": 30}


def test_other_4xx_is_rejected_and_non_json_body_is_wrapped():
    handler, _ = _recording(422, content=b"<html>bad</html>")

    with pytest.raises(DiagRejected) as exc:
        _client(handler).submit(REQUEST)
    assert exc.value.status == 422
    assert exc.value.body.code == "http_422"
    assert "bad" in exc.value.body.message


@pytest.mark.parametrize("status_code", [500, 502, 503])
def test_5xx_is_unavailable(status_code):
    handler, _ = _recording(status_code, content=b"upstream error")

    with pytest.raises(DiagUnavailable):
        _client(handler).submit(REQUEST)


def test_transport_error_is_unavailable():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    with pytest.raises(DiagUnavailable) as exc:
        _client(handler).submit(REQUEST)
    assert isinstance(exc.value, DiagError)
    assert TOKEN not in str(exc.value)


# --- status ---------------------------------------------------------------------------


def test_status_sends_after_seq_and_parses_events():
    body = {
        **ACCEPTED,
        "status": "running",
        "last_event_seq": 2,
        "events": [
            {
                "contract_version": 1,
                "execution_id": EXEC,
                "seq": 2,
                "occurred_at": "2026-09-20T00:10:01Z",
                "type": "started",
                "data": {"runtime_ref": "diag-run-7f3a"},
            }
        ],
    }
    handler, calls = _recording(200, body)

    status = _client(handler).status(EXEC, after_seq=1)

    request = calls[0]
    assert (request.method, request.url.path) == ("GET", f"/runs/{EXEC}")
    assert request.url.params["after_seq"] == "1"
    assert [e.seq for e in status.events] == [2]
    assert status.events[0].data.runtime_ref == "diag-run-7f3a"


def test_status_404_is_rejected():
    handler, _ = _recording(
        404, {"code": "not_found", "message": "없음", "field": "execution_id", "details": None}
    )

    with pytest.raises(DiagRejected) as exc:
        _client(handler).status(EXEC, after_seq=0)
    assert exc.value.body.code == "not_found"


# --- download·capabilities -----------------------------------------------------------


def test_download_returns_bytes_and_content_type():
    handler, calls = _recording(
        200, content=b'{"a": 1}', headers={"content-type": "application/json"}
    )

    data, content_type = _client(handler).download(EXEC, "art-ev-001")

    assert (data, content_type) == (b'{"a": 1}', "application/json")
    assert calls[0].url.path == f"/runs/{EXEC}/artifacts/art-ev-001"


def test_download_quotes_ids_as_single_path_segments():
    handler, calls = _recording(200, content=b"x")

    _client(handler).download(EXEC, "../etc/passwd")

    # `url.path` 는 디코딩된 값이라 전송 원문(raw_path)으로 확인한다
    assert calls[0].url.raw_path == f"/runs/{EXEC}/artifacts/..%2Fetc%2Fpasswd".encode()


def test_download_403_is_rejected():
    handler, _ = _recording(
        403, {"code": "forbidden", "message": "다른 실행", "field": None, "details": None}
    )

    with pytest.raises(DiagRejected):
        _client(handler).download(EXEC, "art-other")


def test_capabilities_returns_dict():
    body = {"contract_version": 1, "role": "operations.diagnose", "workflow_ids": ["daily-report"]}
    handler, calls = _recording(200, body)

    assert _client(handler).capabilities() == body
    assert calls[0].url.path == "/capabilities"
