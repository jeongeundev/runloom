"""github_app — manifest 교환·App JWT·설치 토큰·설치 저장소 (ADR-0017, phase 11 step 2).

실제 GitHub 없이 MockTransport 로 검사한다. 개인 키는 테스트 안에서 만든다(저장소에 키 파일 없음)."""

import json
import logging
from urllib.parse import parse_qs

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from workflow.adapters import secret_store
from workflow.adapters.github_app import (
    AppCredentials,
    GitHubAppAuth,
    Installation,
    InstalledRepository,
    build_manifest,
    exchange_manifest_code,
    load_app,
    save_credentials,
)
from workflow.adapters.github_client import (
    GitHubError,
    GitHubForbidden,
    GitHubNotFound,
    GitHubUnavailable,
)
from workflow.adapters.secret_store import SecretStore

CLIENT_ID = "Iv23liTESTCLIENT"
NOW = 1_790_000_000.0


@pytest.fixture(scope="module")
def key_pair() -> tuple[str, rsa.RSAPublicKey]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL, serialization.NoEncryption()
    ).decode()
    return pem, key.public_key()


class Clock:
    def __init__(self, now: float = NOW):
        self.now = now

    def __call__(self) -> float:
        return self.now


class Recorder:
    def __init__(self, routes: dict):
        self.routes = routes
        self.calls: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        response = self.routes[(request.method, request.url.path)]
        return response(request) if callable(response) else response

    def paths(self) -> list[str]:
        return [f"{r.method} {r.url.path}" for r in self.calls]


def _iso(epoch: float) -> str:
    from datetime import UTC, datetime

    return datetime.fromtimestamp(epoch, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _token_response(token: str, expires_at: float) -> httpx.Response:
    return httpx.Response(201, json={"token": token, "expires_at": _iso(expires_at), "repository_selection": "all"})


def _auth(pem: str, handler, clock=None) -> GitHubAppAuth:
    return GitHubAppAuth(CLIENT_ID, pem, transport=httpx.MockTransport(handler), clock=clock or Clock())


CONVERSION = {
    "id": 123456,
    "slug": "runloom-a1b2c3",
    "name": "runloom-a1b2c3",
    "client_id": CLIENT_ID,
    "client_secret": "CLIENTSECRET0123456789abcdef",
    "webhook_secret": "WEBHOOKSECRET0123456789",
    "pem": "-----BEGIN RSA PRIVATE KEY-----\nFAKEPEMBODY\n-----END RSA PRIVATE KEY-----\n",
    "owner": {"login": "acme", "id": 1},
    "html_url": "https://github.com/apps/runloom-a1b2c3",
}


# ── manifest 교환 ─────────────────────────────────────────────────────────


def test_exchange_manifest_code_posts_without_auth_and_parses():
    rec = Recorder({("POST", "/app-manifests/abc123/conversions"): httpx.Response(201, json=CONVERSION)})

    creds = exchange_manifest_code("abc123", transport=httpx.MockTransport(rec))

    request = rec.calls[0]
    assert request.url.host == "api.github.com" and request.url.scheme == "https"
    assert "authorization" not in request.headers
    assert request.headers["accept"] == "application/vnd.github+json"
    assert creds == AppCredentials(
        app_id=123456, client_id=CLIENT_ID, slug="runloom-a1b2c3", name="runloom-a1b2c3", owner_login="acme",
        html_url="https://github.com/apps/runloom-a1b2c3", client_secret=CONVERSION["client_secret"],
        webhook_secret=CONVERSION["webhook_secret"], pem=CONVERSION["pem"],
    )


def test_app_credentials_repr_has_no_secrets():
    creds = exchange_manifest_code(
        "abc123",
        transport=httpx.MockTransport(lambda r: httpx.Response(201, json=CONVERSION)),
    )
    text = repr(creds)
    for secret in (CONVERSION["client_secret"], CONVERSION["webhook_secret"], "FAKEPEMBODY"):
        assert secret not in text
    assert "runloom-a1b2c3" in text


@pytest.mark.parametrize(("status", "error"), [(404, GitHubNotFound), (422, GitHubError), (500, GitHubUnavailable)])
def test_exchange_manifest_code_errors_are_classified_without_code_or_body(status, error):
    body = {"message": "Not Found", "documentation_url": "https://docs.github.com"}
    rec = Recorder({("POST", "/app-manifests/abc123/conversions"): httpx.Response(status, json=body)})

    with pytest.raises(error) as info:
        exchange_manifest_code("abc123", transport=httpx.MockTransport(rec))

    assert "abc123" not in str(info.value) and "Not Found" not in str(info.value)
    assert f"HTTP {status}" in str(info.value)


@pytest.mark.parametrize("webhook_secret", [None, ""])
def test_exchange_manifest_code_accepts_missing_webhook_secret(webhook_secret):
    """manifest 에 웹훅이 없으면 GitHub 는 webhook_secret 을 null 로 준다(2026-09-27 실제 확인)."""
    body = {**CONVERSION, "webhook_secret": webhook_secret}
    creds = exchange_manifest_code("abc123", transport=httpx.MockTransport(lambda r: httpx.Response(201, json=body)))
    assert creds.webhook_secret is None and creds.pem == CONVERSION["pem"]


def test_exchange_manifest_code_bad_shape_names_missing_fields_only(caplog):
    body = {k: v for k, v in CONVERSION.items() if k not in ("client_secret", "pem")}
    with caplog.at_level("WARNING"), pytest.raises(GitHubError):
        exchange_manifest_code("abc123", transport=httpx.MockTransport(lambda r: httpx.Response(201, json=body)))
    assert "client_secret" in caplog.text and "pem" in caplog.text
    assert CONVERSION["webhook_secret"] not in caplog.text


def test_save_credentials_without_webhook_secret_writes_no_file(tmp_path, key_pair):
    pem, _ = key_pair
    store = SecretStore(tmp_path / "secrets")
    creds = AppCredentials(
        app_id=1, client_id=CLIENT_ID, slug="runloom-a1b2c3", name="runloom-a1b2c3", owner_login="acme",
        html_url="", client_secret="CS", webhook_secret=None, pem=pem,
    )
    save_credentials(store, creds, "2026-09-27T09:00:00Z")
    assert not store.exists(secret_store.GITHUB_APP_WEBHOOK_SECRET)
    assert store.read(secret_store.GITHUB_APP_PRIVATE_KEY) == pem


@pytest.mark.parametrize("missing", ["id", "slug", "client_id", "client_secret", "pem"])
def test_exchange_manifest_code_bad_shape_is_github_error(missing):
    body = {k: v for k, v in CONVERSION.items() if k != missing}
    with pytest.raises(GitHubError) as info:
        exchange_manifest_code("abc123", transport=httpx.MockTransport(lambda r: httpx.Response(201, json=body)))
    assert type(info.value) is GitHubError
    for secret in (CONVERSION["client_secret"], "FAKEPEMBODY"):
        assert secret not in str(info.value)


@pytest.mark.parametrize("code", ["", "../x", "a/b", "a b", "abc?x=1"])
def test_exchange_manifest_code_refuses_odd_code_before_request(code):
    rec = Recorder({})
    with pytest.raises(ValueError):
        exchange_manifest_code(code, transport=httpx.MockTransport(rec))
    assert rec.calls == []


# ── 비밀 저장·읽기 ────────────────────────────────────────────────────────


def test_save_credentials_splits_info_and_secrets(tmp_path, key_pair):
    pem, _ = key_pair
    store = SecretStore(tmp_path / "secrets")
    creds = AppCredentials(
        app_id=123456, client_id=CLIENT_ID, slug="runloom-a1b2c3", name="runloom-a1b2c3", owner_login="acme",
        html_url="https://github.com/apps/runloom-a1b2c3", client_secret="CS", webhook_secret="WS", pem=pem,
    )

    save_credentials(store, creds, "2026-09-27T09:00:00Z")

    info = json.loads(store.read(secret_store.GITHUB_APP_INFO))
    assert info == {
        "app_id": 123456, "client_id": CLIENT_ID, "slug": "runloom-a1b2c3", "name": "runloom-a1b2c3",
        "owner_login": "acme", "html_url": "https://github.com/apps/runloom-a1b2c3",
        "created_at": "2026-09-27T09:00:00Z",
    }
    assert store.read(secret_store.GITHUB_APP_PRIVATE_KEY) == pem
    assert store.read(secret_store.GITHUB_APP_CLIENT_SECRET) == "CS"
    assert store.read(secret_store.GITHUB_APP_WEBHOOK_SECRET) == "WS"

    auth = load_app(store)
    assert isinstance(auth, GitHubAppAuth)
    assert jwt.decode(auth.app_jwt(), options={"verify_signature": False})["iss"] == CLIENT_ID


def test_load_app_is_none_without_saved_app(tmp_path, key_pair):
    store = SecretStore(tmp_path / "secrets")
    assert load_app(store) is None
    store.write(secret_store.GITHUB_APP_PRIVATE_KEY, key_pair[0])
    assert load_app(store) is None  # 정보 파일 없이 키만 있으면 연결 안 됨


# ── App JWT ───────────────────────────────────────────────────────────────


def test_app_jwt_is_rs256_signed_with_iss_iat_exp(key_pair):
    pem, public_key = key_pair
    auth = _auth(pem, Recorder({}))

    token = auth.app_jwt()

    assert jwt.get_unverified_header(token)["alg"] == "RS256"
    claims = jwt.decode(token, public_key, algorithms=["RS256"], options={"verify_exp": False, "verify_iat": False})
    assert claims["iss"] == CLIENT_ID
    assert claims["iat"] == int(NOW) - 60
    assert int(NOW) < claims["exp"] <= int(NOW) + 600


def test_app_jwt_signature_fails_with_other_key(key_pair):
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048).public_key()
    token = _auth(key_pair[0], Recorder({})).app_jwt()
    with pytest.raises(jwt.InvalidSignatureError):
        jwt.decode(token, other, algorithms=["RS256"], options={"verify_exp": False, "verify_iat": False})


def test_bad_private_key_error_has_no_key_text():
    auth = _auth("-----BEGIN RSA PRIVATE KEY-----\nNOTAKEYSECRETBODY\n-----END RSA PRIVATE KEY-----\n", Recorder({}))
    with pytest.raises(GitHubError) as info:
        auth.app_jwt()
    assert "NOTAKEYSECRETBODY" not in str(info.value) and "NOTAKEYSECRETBODY" not in repr(info.value)
    assert info.value.__cause__ is None and info.value.__suppress_context__


# ── 설치 토큰 ─────────────────────────────────────────────────────────────


def test_installation_token_uses_jwt_and_is_cached(key_pair):
    pem, public_key = key_pair
    rec = Recorder({("POST", "/app/installations/77/access_tokens"): _token_response("ghs_ONE", NOW + 3600)})
    auth = _auth(pem, rec)

    assert auth.installation_token(77) == "ghs_ONE"
    assert auth.installation_token(77) == "ghs_ONE"

    assert rec.paths() == ["POST /app/installations/77/access_tokens"]
    bearer = rec.calls[0].headers["authorization"].removeprefix("Bearer ")
    claims = jwt.decode(bearer, public_key, algorithms=["RS256"], options={"verify_exp": False, "verify_iat": False})
    assert claims["iss"] == CLIENT_ID


def test_installation_token_refreshes_five_minutes_before_expiry(key_pair):
    tokens = iter(["ghs_ONE", "ghs_TWO"])
    rec = Recorder({
        ("POST", "/app/installations/77/access_tokens"): lambda r: _token_response(next(tokens), NOW + 3600),
    })
    clock = Clock()
    auth = _auth(key_pair[0], rec, clock)

    assert auth.installation_token(77) == "ghs_ONE"
    clock.now = NOW + 3600 - 301
    assert auth.installation_token(77) == "ghs_ONE"
    clock.now = NOW + 3600 - 300
    assert auth.installation_token(77) == "ghs_TWO"
    assert len(rec.calls) == 2


def test_installation_tokens_are_cached_per_installation(key_pair):
    rec = Recorder({
        ("POST", "/app/installations/1/access_tokens"): _token_response("ghs_A", NOW + 3600),
        ("POST", "/app/installations/2/access_tokens"): _token_response("ghs_B", NOW + 3600),
    })
    auth = _auth(key_pair[0], rec)

    assert (auth.installation_token(1), auth.installation_token(2), auth.installation_token(1)) == (
        "ghs_A", "ghs_B", "ghs_A",
    )
    assert len(rec.calls) == 2


def test_invalidate_drops_cached_token(key_pair):
    tokens = iter(["ghs_ONE", "ghs_TWO"])
    rec = Recorder({
        ("POST", "/app/installations/77/access_tokens"): lambda r: _token_response(next(tokens), NOW + 3600),
    })
    auth = _auth(key_pair[0], rec)

    assert auth.installation_token(77) == "ghs_ONE"
    auth.invalidate(77)
    assert auth.installation_token(77) == "ghs_TWO"


def test_installation_token_401_retries_once_with_new_jwt(key_pair):
    statuses = iter([401, 201])

    def handler(request):
        status = next(statuses)
        return _token_response("ghs_OK", NOW + 3600) if status == 201 else httpx.Response(401, json={})

    rec = Recorder({("POST", "/app/installations/77/access_tokens"): handler})

    assert _auth(key_pair[0], rec).installation_token(77) == "ghs_OK"
    assert len(rec.calls) == 2


def test_installation_token_401_twice_is_forbidden(key_pair):
    rec = Recorder({("POST", "/app/installations/77/access_tokens"): httpx.Response(401, json={})})
    with pytest.raises(GitHubForbidden) as info:
        _auth(key_pair[0], rec).installation_token(77)
    assert len(rec.calls) == 2
    assert str(info.value) == "POST /app/installations/77/access_tokens: HTTP 401"


@pytest.mark.parametrize("body", [{}, {"token": "ghs_X"}, {"token": "", "expires_at": _iso(NOW)},
                                  {"token": "ghs_X", "expires_at": "not a date"}])
def test_installation_token_bad_shape_is_github_error(key_pair, body):
    rec = Recorder({("POST", "/app/installations/77/access_tokens"): httpx.Response(201, json=body)})
    with pytest.raises(GitHubError) as info:
        _auth(key_pair[0], rec).installation_token(77)
    assert type(info.value) is GitHubError
    assert "ghs_X" not in str(info.value)


# ── 설치 확인·설치 저장소 ───────────────────────────────────────────────


def test_get_installation_uses_app_jwt(key_pair):
    pem, public_key = key_pair
    body = {"id": 77, "account": {"login": "acme", "id": 1}, "repository_selection": "selected"}
    rec = Recorder({("GET", "/app/installations/77"): httpx.Response(200, json=body)})

    installation = _auth(pem, rec).get_installation(77)

    assert installation == Installation(installation_id=77, account_login="acme", repository_selection="selected")
    bearer = rec.calls[0].headers["authorization"].removeprefix("Bearer ")
    assert jwt.decode(bearer, public_key, algorithms=["RS256"],
                      options={"verify_exp": False, "verify_iat": False})["iss"] == CLIENT_ID


def test_get_installation_of_other_app_is_not_found(key_pair):
    rec = Recorder({("GET", "/app/installations/99"): httpx.Response(404, json={"message": "Not Found"})})
    with pytest.raises(GitHubNotFound):
        _auth(key_pair[0], rec).get_installation(99)


def _repos_page(repos, next_page=None, host="api.github.com"):
    headers = {}
    if next_page is not None:
        headers["link"] = f'<https://{host}/installation/repositories?per_page=100&page={next_page}>; rel="next"'
    return httpx.Response(200, json={"total_count": 3, "repositories": repos}, headers=headers)


def test_list_installation_repositories_follows_pages_with_installation_token(key_pair):
    pages = {
        "1": _repos_page([{"id": 1, "full_name": "acme/app"}, {"id": 2, "full_name": "acme/lib"}], next_page=2),
        "2": _repos_page([{"id": 3, "full_name": "acme/docs"}]),
    }
    rec = Recorder({
        ("POST", "/app/installations/77/access_tokens"): _token_response("ghs_INST", NOW + 3600),
        ("GET", "/installation/repositories"): lambda r: pages[parse_qs(r.url.query.decode())["page"][0]],
    })

    repos = _auth(key_pair[0], rec).list_installation_repositories(77)

    assert repos == [
        InstalledRepository(repository_id=1, full_name="acme/app"),
        InstalledRepository(repository_id=2, full_name="acme/lib"),
        InstalledRepository(repository_id=3, full_name="acme/docs"),
    ]
    listing = [r for r in rec.calls if r.url.path == "/installation/repositories"]
    assert [parse_qs(r.url.query.decode())["per_page"][0] for r in listing] == ["100", "100"]
    assert all(r.headers["authorization"] == "Bearer ghs_INST" for r in listing)
    assert rec.paths().count("POST /app/installations/77/access_tokens") == 1


def test_list_installation_repositories_401_refreshes_token_once(key_pair):
    tokens = iter(["ghs_OLD", "ghs_NEW"])

    def listing(request):
        if request.headers["authorization"] == "Bearer ghs_OLD":
            return httpx.Response(401, json={})
        return _repos_page([{"id": 1, "full_name": "acme/app"}])

    rec = Recorder({
        ("POST", "/app/installations/77/access_tokens"): lambda r: _token_response(next(tokens), NOW + 3600),
        ("GET", "/installation/repositories"): listing,
    })

    repos = _auth(key_pair[0], rec).list_installation_repositories(77)

    assert [r.full_name for r in repos] == ["acme/app"]


def test_list_installation_repositories_refuses_next_link_to_other_host(key_pair):
    rec = Recorder({
        ("POST", "/app/installations/77/access_tokens"): _token_response("ghs_INST", NOW + 3600),
        ("GET", "/installation/repositories"): _repos_page([{"id": 1, "full_name": "acme/app"}], 2, "evil.example"),
    })
    with pytest.raises(GitHubError, match="api.github.com"):
        _auth(key_pair[0], rec).list_installation_repositories(77)


# ── manifest (step 7) ──────────────────────────────────────────────────


def test_build_manifest_has_minimal_permissions_and_no_webhook():
    """GitHub 는 active=false 여도 공개 인터넷에서 닿지 않는 hook url(127.0.0.1)을 거부한다(2026-09-27 실제 확인) — 웹훅 항목을 뺀다."""
    manifest = build_manifest("http://127.0.0.1:8000", "runloom-a1b2c3")

    assert manifest == {
        "name": "runloom-a1b2c3",
        "url": "http://127.0.0.1:8000",
        "redirect_url": "http://127.0.0.1:8000/operator/github/app/callback",
        "setup_url": "http://127.0.0.1:8000/operator/github/app/setup",
        "setup_on_update": True,
        "public": False,
        "default_permissions": {"issues": "write", "pull_requests": "write", "contents": "read", "metadata": "read"},
        "default_events": [],
    }
    # Contents 는 읽기만 — PR 생성이 head·base ref 를 읽는다(없으면 422 `not all refs are readable`, 실연동 1). push 는 러너가
    assert manifest["default_permissions"]["contents"] == "read"


def test_build_manifest_strips_trailing_slash():
    manifest = build_manifest("https://runloom.example/", "runloom-zz9999")

    assert manifest["redirect_url"] == "https://runloom.example/operator/github/app/callback"
    assert "hook_attributes" not in manifest


# ── 비밀이 새지 않음 ─────────────────────────────────────────────────────


def test_secrets_never_appear_in_repr_errors_or_logs(key_pair, caplog):
    caplog.set_level(logging.DEBUG)
    pem, _ = key_pair
    rec = Recorder({
        ("POST", "/app/installations/77/access_tokens"): _token_response("ghs_SECRETINSTALLTOKEN", NOW + 3600),
        ("GET", "/installation/repositories"): httpx.Response(403, json={"message": "ghs_SECRETINSTALLTOKEN"}),
    })
    auth = _auth(pem, rec)

    with pytest.raises(GitHubForbidden) as info:
        auth.list_installation_repositories(77)

    app_jwt = rec.calls[0].headers["authorization"].removeprefix("Bearer ")
    key_body = pem.splitlines()[1]
    for text in (str(info.value), repr(info.value), caplog.text, repr(auth)):
        for secret in ("ghs_SECRETINSTALLTOKEN", app_jwt, key_body):
            assert secret not in text
    assert str(info.value) == "GET /installation/repositories: HTTP 403"
