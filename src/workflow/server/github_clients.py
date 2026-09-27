"""소스별 GitHub 클라이언트 선택 — ADR-0017, ARCHITECTURE "클라이언트 선택".

순서: ① 소스에 `installation_id` 가 있고 비밀 저장소에 App 자격이 있으면 설치 토큰(허용 목록 = 그 소스 저장소)
② 비밀 저장소의 붙여 넣은 PAT(`WORKFLOW_GITHUB_REPOS` + 그 소스 저장소) ③ `WORKFLOW_GITHUB_TOKEN`(지금 동작)
④ 없으면 None — 그 소스는 수집·반영하지 않는다. 토큰 값은 클라이언트 안에만 있고 로그·repr 에 싣지 않는다.
"""

import httpx

from workflow.adapters import secret_store
from workflow.adapters.github_app import GitHubAppAuth, load_app
from workflow.adapters.github_client import HttpGitHubClient, InstallationTokenProvider, TokenProvider
from workflow.adapters.secret_store import SecretStore
from workflow.contracts.github import GitHubSourceConfig
from workflow.server.settings import Settings


def _choose(
    source: GitHubSourceConfig, settings: Settings, secrets: SecretStore, app: GitHubAppAuth | None
) -> tuple[str | TokenProvider, tuple[str, ...]] | None:
    if source.installation_id is not None and app is not None:
        return InstallationTokenProvider(app, source.installation_id), (source.repository_full_name,)
    pat = (secrets.read(secret_store.GITHUB_TOKEN) or "").strip()
    if pat:
        return pat, (*settings.github_repos, source.repository_full_name)
    if settings.github_token:
        return settings.github_token, settings.github_repos
    return None


def credential_kind(source: GitHubSourceConfig, settings: Settings, secrets: SecretStore) -> str | None:
    """`client_for` 가 이 소스에 쓸 자격의 종류 — `app`·`pat`·`env`·None(수집 안 함). 연결 화면 표시용이라 값은 돌려주지
    않고 App 은 파일이 있는지만 본다."""
    if source.installation_id is not None and all(
        secrets.exists(name) for name in (secret_store.GITHUB_APP_INFO, secret_store.GITHUB_APP_PRIVATE_KEY)
    ):
        return "app"
    if (secrets.read(secret_store.GITHUB_TOKEN) or "").strip():
        return "pat"
    return "env" if settings.github_token else None


def client_for(
    source: GitHubSourceConfig,
    settings: Settings,
    secrets: SecretStore,
    *,
    app: GitHubAppAuth | None = None,
    transport: httpx.BaseTransport | None = None,
) -> HttpGitHubClient | None:
    """`app` 을 주면 그 App 인증(설치 토큰 캐시)을 쓰고, 없으면 비밀 저장소에서 읽는다."""
    if app is None and source.installation_id is not None:
        app = load_app(secrets, transport=transport)
    chosen = _choose(source, settings, secrets, app)
    if chosen is None:
        return None
    token, allowed = chosen
    return HttpGitHubClient(token, allowed, transport=transport)


class SourceClients:
    """워커 프로세스당 하나. App 인증을 한 번 읽어 설치 토큰 캐시를 소스끼리 같이 쓰고, 소스별 클라이언트는 자격이
    같으면 재사용한다. App 이 아직 없으면 부를 때마다 다시 읽어 central 이 나중에 저장한 App·PAT 을 따라간다."""

    def __init__(self, settings: Settings, secrets: SecretStore, *, transport: httpx.BaseTransport | None = None):
        self._settings = settings
        self._secrets = secrets
        self._transport = transport
        self._app: GitHubAppAuth | None = None
        self._clients: dict[str, tuple[tuple, HttpGitHubClient]] = {}  # source_id → (자격 열쇠, 클라이언트)

    def __repr__(self) -> str:
        return f"SourceClients(sources={sorted(self._clients)})"

    def __call__(self, source: GitHubSourceConfig) -> HttpGitHubClient | None:
        if self._app is None and source.installation_id is not None:
            self._app = load_app(self._secrets, transport=self._transport)
        chosen = _choose(source, self._settings, self._secrets, self._app)
        if chosen is None:
            self._clients.pop(source.source_id, None)
            return None
        token, allowed = chosen
        key = (("installation", source.installation_id) if not isinstance(token, str) else ("token", token), allowed)
        cached = self._clients.get(source.source_id)
        if cached is not None and cached[0] == key:
            return cached[1]
        client = HttpGitHubClient(token, allowed, transport=self._transport)
        self._clients[source.source_id] = (key, client)
        return client
