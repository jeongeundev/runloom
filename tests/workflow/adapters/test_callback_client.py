"""callback_client — 중앙 워커 → n8n Wait 노드 HTTP 클라이언트 (ADR-0010). 실제 서버 없이 MockTransport 로 검사한다."""

import json

import httpx
import pytest

from workflow.adapters.callback_client import CallbackFailed, HttpCallbackClient

URL = "http://localhost:5678/webhook-waiting/1234"
PAYLOAD = {"contract_version": 1, "chain_id": "chain-1", "title": "일일 보고서 복구"}


def _recording(status_code: int, headers=None):
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(status_code, headers=headers or {})

    return handler, calls


def _client(handler) -> HttpCallbackClient:
    return HttpCallbackClient(transport=httpx.MockTransport(handler))


def test_post_sends_json_body_and_returns_on_2xx():
    handler, calls = _recording(200)

    assert _client(handler).post(URL, PAYLOAD) is None

    assert len(calls) == 1
    request = calls[0]
    assert (request.method, str(request.url)) == ("POST", URL)
    assert request.headers["content-type"] == "application/json"
    assert json.loads(request.content) == PAYLOAD


@pytest.mark.parametrize("status_code", [404, 500])
def test_non_2xx_raises_callback_failed_with_status_only(status_code):
    handler, _ = _recording(status_code)

    with pytest.raises(CallbackFailed) as info:
        _client(handler).post(URL, PAYLOAD)

    assert str(info.value) == f"HTTP {status_code}"


def test_connection_error_message_has_no_url_or_body():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    with pytest.raises(CallbackFailed) as info:
        _client(handler).post(URL, PAYLOAD)

    message = str(info.value)
    assert message == "연결 오류: ConnectError"
    assert "localhost" not in message and "chain-1" not in message and "일일" not in message


def test_timeout_is_callback_failed():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    with pytest.raises(CallbackFailed, match="연결 오류: ReadTimeout"):
        _client(handler).post(URL, PAYLOAD)


def test_redirect_is_not_followed():
    handler, calls = _recording(302, headers={"location": "http://evil.example/steal"})

    with pytest.raises(CallbackFailed, match="HTTP 302"):
        _client(handler).post(URL, PAYLOAD)

    assert [str(c.url) for c in calls] == [URL]
