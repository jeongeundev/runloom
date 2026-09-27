"""GitHub App 인증 — manifest 교환·App JWT·설치 토큰·설치 저장소 (ADR-0017, ARCHITECTURE "GitHub App 연결 — phase 11").

- host 는 `https://api.github.com` 고정, 리다이렉트를 따라가지 않는다. 오류 분류는 `github_client.check_response` 그대로.
- App JWT: RS256, `iat` = 지금 − 60초, `exp` = 지금 + 9분, `iss` = client ID.
- 설치 토큰은 프로세스 메모리에만 설치별로 캐시하고 만료 5분 전이면 새로 받는다. 파일·DB 에 쓰지 않는다.
- 오류 메시지는 `메서드 경로: …` 뿐 — JWT·설치 토큰·개인 키·manifest code·응답 본문을 넣지 않는다.
"""

import json
import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import httpx
import jwt

from workflow.adapters import secret_store
from workflow.adapters.github_client import (
    API_HOST,
    API_VERSION,
    PER_PAGE,
    GitHubError,
    GitHubUnavailable,
    check_response,
    next_page,
)
from workflow.adapters.secret_store import SecretStore

logger = logging.getLogger(__name__)

JWT_BACKDATE_SECONDS = 60
JWT_LIFETIME_SECONDS = 9 * 60
TOKEN_REFRESH_MARGIN_SECONDS = 5 * 60

_MANIFEST_CODE = re.compile(r"[A-Za-z0-9_-]{1,200}")
_CONVERSION_PATH = "/app-manifests/…/conversions"  # 메시지용 — code 는 싣지 않는다


@dataclass(frozen=True)
class AppCredentials:
    """manifest 교환 결과. 호출자가 `save_credentials` 로 비밀 저장소에 둔다. repr 에 비밀이 없다."""

    app_id: int
    client_id: str
    slug: str
    name: str
    owner_login: str
    html_url: str
    client_secret: str = field(repr=False)
    webhook_secret: str | None = field(repr=False)  # manifest 에 웹훅이 없으면 GitHub 가 null 로 준다
    pem: str = field(repr=False)


@dataclass(frozen=True)
class Installation:
    installation_id: int
    account_login: str
    repository_selection: str


@dataclass(frozen=True)
class InstalledRepository:
    repository_id: int
    full_name: str


def _http(transport: httpx.BaseTransport | None) -> httpx.Client:
    return httpx.Client(
        base_url=f"https://{API_HOST}",
        headers={"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": API_VERSION, "User-Agent": "runloom"},
        transport=transport,
        timeout=10.0,
        follow_redirects=False,
    )


def _send(client: httpx.Client, method: str, path: str, label: str, **kwargs: Any) -> httpx.Response:
    try:
        return client.request(method, path, **kwargs)
    except httpx.HTTPError as exc:
        raise GitHubUnavailable(f"{method} {label}: {type(exc).__name__}") from None


def _json_object(response: httpx.Response, method: str, label: str) -> dict:
    try:
        data = response.json()
    except ValueError:
        data = None
    if not isinstance(data, dict):
        raise GitHubError(f"{method} {label}: 응답 형식 오류")
    return data


def build_manifest(base_url: str, name: str) -> dict:
    """App 만들기 화면에 넘길 manifest. 웹훅 항목은 넣지 않는다 — GitHub 는 `active: false` 여도 공개 인터넷에서 닿지 않는
    hook url(127.0.0.1)을 거부한다(2026-09-27 실제 확인). 권한은 이슈 쓰기·PR 쓰기(초안 PR, ADR-0018)·메타데이터만."""
    base = base_url.rstrip("/")
    return {
        "name": name,
        "url": base,
        "redirect_url": f"{base}/operator/github/app/callback",
        "setup_url": f"{base}/operator/github/app/setup",
        "setup_on_update": True,
        "public": False,
        "default_permissions": {"issues": "write", "pull_requests": "write", "metadata": "read"},
        "default_events": [],
    }


def exchange_manifest_code(code: str, *, transport: httpx.BaseTransport | None = None) -> AppCredentials:
    """`POST /app-manifests/{code}/conversions`(인증 없음). 저장하지 않는다 — 호출자가 `save_credentials` 한다."""
    if not isinstance(code, str) or not _MANIFEST_CODE.fullmatch(code):
        raise ValueError("manifest code 형식이 아닙니다")
    with _http(transport) as client:
        response = _send(client, "POST", f"/app-manifests/{code}/conversions", _CONVERSION_PATH)
        check_response("POST", _CONVERSION_PATH, response)
        data = _json_object(response, "POST", _CONVERSION_PATH)
    required = ("id", "client_id", "slug", "client_secret", "pem")
    missing = [key for key in required if not data.get(key)]
    if missing:
        # 이름만 남긴다 — 값(비밀)은 남기지 않는다
        logger.warning("GitHub App manifest 교환 응답에 없는 항목: %s", ", ".join(missing))
        raise GitHubError(f"POST {_CONVERSION_PATH}: 응답 형식 오류")
    try:
        creds = AppCredentials(
            app_id=int(data["id"]),
            client_id=data["client_id"],
            slug=data["slug"],
            name=data.get("name") or data["slug"],
            owner_login=(data.get("owner") or {}).get("login") or "",
            html_url=data.get("html_url") or "",
            client_secret=data["client_secret"],
            webhook_secret=data.get("webhook_secret") or None,
            pem=data["pem"],
        )
    except (TypeError, ValueError, AttributeError):
        raise GitHubError(f"POST {_CONVERSION_PATH}: 응답 형식 오류") from None
    values = (creds.client_id, creds.slug, creds.client_secret, creds.pem)
    if not all(isinstance(value, str) and value for value in values):
        raise GitHubError(f"POST {_CONVERSION_PATH}: 응답 형식 오류")
    return creds


def save_credentials(store: SecretStore, creds: AppCredentials, now: str) -> None:
    """비밀이 아닌 App 정보는 `github_app.json`, 비밀은 파일 하나씩. 개인 키를 마지막에 써 `load_app` 이 반쪽을 읽지 않게 한다."""
    store.write(secret_store.GITHUB_APP_CLIENT_SECRET, creds.client_secret)
    if creds.webhook_secret:
        store.write(secret_store.GITHUB_APP_WEBHOOK_SECRET, creds.webhook_secret)
    store.write(secret_store.GITHUB_APP_INFO, json.dumps({
        "app_id": creds.app_id, "client_id": creds.client_id, "slug": creds.slug, "name": creds.name,
        "owner_login": creds.owner_login, "html_url": creds.html_url, "created_at": now,
    }, ensure_ascii=False))
    store.write(secret_store.GITHUB_APP_PRIVATE_KEY, creds.pem)


def load_app(store: SecretStore, *, transport: httpx.BaseTransport | None = None) -> "GitHubAppAuth | None":
    """저장된 App 이 없거나(정보·개인 키 중 하나라도 없음) 정보 파일이 깨졌으면 None."""
    info_text = store.read(secret_store.GITHUB_APP_INFO)
    pem = store.read(secret_store.GITHUB_APP_PRIVATE_KEY)
    if not info_text or not pem:
        return None
    try:
        client_id = json.loads(info_text)["client_id"]
    except (ValueError, KeyError, TypeError):
        return None
    if not isinstance(client_id, str) or not client_id:
        return None
    return GitHubAppAuth(client_id, pem, transport=transport)


class GitHubAppAuth:
    def __init__(
        self,
        client_id: str,
        private_key_pem: str,
        *,
        transport: httpx.BaseTransport | None = None,
        clock: Callable[[], float] = time.time,
    ):
        self.client_id = client_id
        self._pem = private_key_pem
        self._clock = clock
        self._client = _http(transport)
        self._tokens: dict[int, tuple[str, float]] = {}  # installation_id → (토큰, 만료 epoch)

    def __repr__(self) -> str:
        return f"GitHubAppAuth(client_id={self.client_id!r})"

    def app_jwt(self) -> str:
        now = int(self._clock())
        claims = {"iat": now - JWT_BACKDATE_SECONDS, "exp": now + JWT_LIFETIME_SECONDS, "iss": self.client_id}
        try:
            return jwt.encode(claims, self._pem, algorithm="RS256")
        except (ValueError, TypeError, jwt.PyJWTError):
            raise GitHubError("App 개인 키로 JWT 를 서명할 수 없습니다") from None

    def installation_token(self, installation_id: int) -> str:
        installation_id = int(installation_id)
        cached = self._tokens.get(installation_id)
        if cached is not None and self._clock() < cached[1] - TOKEN_REFRESH_MARGIN_SECONDS:
            return cached[0]
        path = f"/app/installations/{installation_id}/access_tokens"
        response = self._app_call("POST", path)
        data = _json_object(response, "POST", path)
        token, expires_at = data.get("token"), data.get("expires_at")
        try:
            expires = datetime.fromisoformat(expires_at).timestamp()
        except (TypeError, ValueError):
            raise GitHubError(f"POST {path}: 응답 형식 오류") from None
        if not isinstance(token, str) or not token:
            raise GitHubError(f"POST {path}: 응답 형식 오류")
        self._tokens[installation_id] = (token, expires)
        return token

    def invalidate(self, installation_id: int) -> None:
        self._tokens.pop(int(installation_id), None)

    def get_installation(self, installation_id: int) -> Installation:
        """App JWT 로 확인한다 — 다른 App 의 설치면 GitHub 가 404(`GitHubNotFound`)를 준다."""
        path = f"/app/installations/{int(installation_id)}"
        data = _json_object(self._app_call("GET", path), "GET", path)
        try:
            return Installation(
                installation_id=int(data["id"]),
                account_login=data["account"]["login"],
                repository_selection=data["repository_selection"],
            )
        except (KeyError, TypeError, ValueError):
            raise GitHubError(f"GET {path}: 응답 형식 오류") from None

    def list_installation_repositories(self, installation_id: int) -> list[InstalledRepository]:
        path = "/installation/repositories"
        repos: list[InstalledRepository] = []
        page: int | None = 1
        while page is not None:
            response = self._installation_call(installation_id, "GET", path, params={"per_page": PER_PAGE, "page": page})
            data = _json_object(response, "GET", path)
            try:
                repos.extend(
                    InstalledRepository(repository_id=int(item["id"]), full_name=item["full_name"])
                    for item in data["repositories"]
                )
            except (KeyError, TypeError, ValueError):
                raise GitHubError(f"GET {path}: 응답 형식 오류") from None
            page = next_page(response, path)
        return repos

    # ── 내부 ──

    def _app_call(self, method: str, path: str) -> httpx.Response:
        """App JWT 로 부른다. 401 이면 JWT 를 새로 만들어 한 번 더."""
        response = self._jwt_send(method, path)
        if response.status_code == 401:
            response = self._jwt_send(method, path)
        return check_response(method, path, response)

    def _jwt_send(self, method: str, path: str) -> httpx.Response:
        return _send(self._client, method, path, path, headers={"Authorization": f"Bearer {self.app_jwt()}"})

    def _installation_call(self, installation_id: int, method: str, path: str, **kwargs: Any) -> httpx.Response:
        """설치 토큰으로 부른다. 401 이면 캐시를 버리고 새 토큰으로 한 번 더."""
        response = self._token_send(installation_id, method, path, kwargs)
        if response.status_code == 401:
            self.invalidate(installation_id)
            response = self._token_send(installation_id, method, path, kwargs)
        return check_response(method, path, response)

    def _token_send(self, installation_id: int, method: str, path: str, kwargs: dict[str, Any]) -> httpx.Response:
        headers = {"Authorization": f"Bearer {self.installation_token(installation_id)}"}
        return _send(self._client, method, path, path, headers=headers, **kwargs)
