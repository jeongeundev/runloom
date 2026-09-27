"""notify_sender — 중앙 워커 → 알림 웹훅 HTTP 경계 (ADR-0018 결정 5). 실제 Discord 없이 MockTransport 로 검사한다.

URL 은 토큰을 담는다 — 예외 문구·로그(httpx 요청 로그 포함)에 들어가지 않아야 한다.
"""

import json
import logging

import httpx
import pytest

from workflow.adapters.notify_sender import NotifyFailed, NotifySender

URL = "https://discord.com/api/webhooks/123/tok-secret-path"
BODY = {"content": "[Runloom] PR 확인 — 버그 1"}


def _sender(handler) -> NotifySender:
    return NotifySender(transport=httpx.MockTransport(handler))


@pytest.mark.parametrize("status_code", [200, 204])
def test_post_sends_json_and_returns_on_2xx(status_code):
    calls: list[httpx.Request] = []

    def handler(request):
        calls.append(request)
        return httpx.Response(status_code)

    assert _sender(handler).post(URL, BODY) is None
    (request,) = calls
    assert (request.method, str(request.url)) == ("POST", URL)
    assert json.loads(request.content) == BODY


def test_non_2xx_is_failed_without_retry_after():
    with pytest.raises(NotifyFailed) as caught:
        _sender(lambda request: httpx.Response(500, text=URL)).post(URL, BODY)
    assert (str(caught.value), caught.value.retry_after) == ("HTTP 500", None)


def test_redirect_is_not_followed():
    with pytest.raises(NotifyFailed, match="HTTP 302"):
        _sender(lambda request: httpx.Response(302, headers={"location": "https://evil.example/"})).post(URL, BODY)


def test_429_reads_discord_retry_after_from_the_body():
    handler = lambda request: httpx.Response(429, json={"message": "rate limited", "retry_after": 1.5})  # noqa: E731
    with pytest.raises(NotifyFailed) as caught:
        _sender(handler).post(URL, BODY)
    assert (str(caught.value), caught.value.retry_after) == ("HTTP 429", 1.5)


def test_429_falls_back_to_the_retry_after_header():
    handler = lambda request: httpx.Response(429, headers={"retry-after": "7"}, text="slow down")  # noqa: E731
    with pytest.raises(NotifyFailed) as caught:
        _sender(handler).post(URL, BODY)
    assert caught.value.retry_after == 7.0


def test_connection_error_names_only_the_exception_class():
    def handler(request):
        raise httpx.ConnectError(f"cannot reach {request.url}", request=request)

    with pytest.raises(NotifyFailed) as caught:
        _sender(handler).post(URL, BODY)
    assert str(caught.value) == "연결 오류: ConnectError"
    assert "tok-secret" not in repr(caught.value)


def test_url_does_not_reach_the_logs(caplog):
    caplog.set_level(logging.DEBUG)
    _sender(lambda request: httpx.Response(204)).post(URL, BODY)
    with pytest.raises(NotifyFailed):
        _sender(lambda request: httpx.Response(500)).post(URL, BODY)
    assert "tok-secret" not in caplog.text
