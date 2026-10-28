"""CLI HTTP 클라이언트 — phase 26 step 3 (ARCHITECTURE "CLI — phase 26" client.py).

실제 앱(`TestClient`)을 `http` 로 넣어 `/api/v1` 을 그대로 부른다. 연결 실패는 httpx `MockTransport` 로 만든다.
"""

import httpx
import pytest
from fastapi.testclient import TestClient

from workflow.cli.client import ApiClient, ApiError, Unreachable

from .conftest import MEMBER_NAME

SERVER = "https://runloom.example.com"


def test_get_me_with_member_token(app, member_token):
    api = ApiClient(SERVER, member_token, http=TestClient(app))
    me = api.get("/me")
    assert (me["display_name"], me["role"], me["token_scope"]) == (MEMBER_NAME, "member", "full")


def test_wrong_token_is_api_error_401(app, member_token):
    api = ApiClient(SERVER, "wfm_wrongTOKEN0123456789", http=TestClient(app))
    with pytest.raises(ApiError) as caught:
        api.get("/me")
    assert (caught.value.status, caught.value.code) == (401, "unauthenticated")
    assert caught.value.message
    assert "wfm_wrongTOKEN0123456789" not in str(caught.value) + repr(caught.value)


def test_unknown_work_item_is_404_not_found(app, member_token):
    api = ApiClient(SERVER, member_token, http=TestClient(app))
    with pytest.raises(ApiError) as caught:
        api.get("/work-items/RUN-999")
    assert (caught.value.status, caught.value.code) == (404, "not_found")
    assert caught.value.message == "업무 RUN-999을 찾을 수 없습니다."


def _mock(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler), base_url=SERVER)


def test_requests_prefix_bearer_params_and_json_body():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.method == "DELETE":
            return httpx.Response(204)
        return httpx.Response(200, json={"ok": True, "extra": "무시해도 됨"})

    api = ApiClient(SERVER, "wfm_tokenVALUE0123456789", http=_mock(handler))
    assert api.get("/work-items", params={"filter": "my_turn"}) == {"ok": True, "extra": "무시해도 됨"}
    assert api.put("/reports/r1/decisions/t1", json={"answer": "confirmed"}) == {"ok": True, "extra": "무시해도 됨"}
    assert api.post("/work-items/RUN-1/direct-work") == {"ok": True, "extra": "무시해도 됨"}
    assert api.delete("/work-items/RUN-1/direct-work") is None

    assert [(r.method, r.url.path) for r in seen] == [
        ("GET", "/api/v1/work-items"), ("PUT", "/api/v1/reports/r1/decisions/t1"),
        ("POST", "/api/v1/work-items/RUN-1/direct-work"), ("DELETE", "/api/v1/work-items/RUN-1/direct-work")]
    assert all(r.headers["Authorization"] == "Bearer wfm_tokenVALUE0123456789" for r in seen)
    assert seen[0].url.params["filter"] == "my_turn"
    assert seen[1].headers["content-type"] == "application/json"
    assert seen[1].read() == b'{"answer":"confirmed"}'
    assert seen[2].read() == b""


def test_non_json_error_body_is_http_error():
    api = ApiClient(SERVER, "wfm_tokenVALUE0123456789",
                    http=_mock(lambda request: httpx.Response(502, text="<html>bad gateway</html>")))
    with pytest.raises(ApiError) as caught:
        api.get("/me")
    assert (caught.value.status, caught.value.code, caught.value.message) == (502, "http_error", "Bad Gateway")


def test_empty_2xx_body_is_none():
    api = ApiClient(SERVER, "wfm_tokenVALUE0123456789", http=_mock(lambda request: httpx.Response(200)))
    assert api.get("/me") is None


@pytest.mark.parametrize("exc, reason", [
    (httpx.ConnectError("refused"), "연결 실패"),
    (httpx.ReadTimeout("slow"), "시간 초과"),
    (httpx.ConnectTimeout("slow"), "시간 초과"),
])
def test_transport_failure_is_unreachable(exc, reason):
    calls = []

    def handler(request):
        calls.append(request)
        raise exc

    api = ApiClient(SERVER, "wfm_tokenVALUE0123456789", http=_mock(handler))
    with pytest.raises(Unreachable) as caught:
        api.get("/me")
    assert (caught.value.server, caught.value.reason) == (SERVER, reason)
    assert len(calls) == 1  # 자동 재시도 없음
    assert "wfm_tokenVALUE0123456789" not in str(caught.value) + repr(caught.value)


def test_real_unreachable_server_without_injected_client():
    api = ApiClient("http://127.0.0.1:9", "wfm_tokenVALUE0123456789", timeout=2.0)
    with pytest.raises(Unreachable) as caught:
        api.get("/me")
    assert caught.value.server == "http://127.0.0.1:9"


def test_repr_hides_token():
    api = ApiClient(SERVER, "wfm_tokenVALUE0123456789", http=_mock(lambda request: httpx.Response(200)))
    assert "wfm_tokenVALUE0123456789" not in repr(api)
