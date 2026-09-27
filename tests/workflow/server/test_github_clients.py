"""github_clients — 소스별 GitHub 클라이언트 선택 (phase 11 step 5, ADR-0017, ARCHITECTURE "클라이언트 선택").

App 설치 토큰 → 비밀 저장소 PAT → 환경변수 토큰 → 없음. 실제 GitHub 없이 MockTransport 로 Authorization 을 확인한다.
개인 키는 테스트 안에서 만든다."""

import json
from pathlib import Path

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from workflow.adapters import secret_store
from workflow.adapters.github_client import GitHubRepositoryNotAllowed, IssueCursor
from workflow.adapters.secret_store import SecretStore
from workflow.contracts.github import GitHubSourceConfig
from workflow.server.github_clients import SourceClients, client_for, credential_kind
from workflow.server.settings import Settings

REPO = "acme/billing"
ENV_TOKEN = "ghp_env_token_value"
PAT = "github_pat_pasted_value"
INSTALLATION_TOKEN = "ghs_installation_token"


@pytest.fixture(scope="module")
def pem() -> str:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL, serialization.NoEncryption()
    ).decode()


class FakeGitHub:
    """설치 토큰 발급과 이슈 목록만. 목록 요청의 Authorization 을 모은다."""

    def __init__(self):
        self.token_requests: list[str] = []
        self.list_auth: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "POST" and path.startswith("/app/installations/"):
            self.token_requests.append(path)
            return httpx.Response(201, json={"token": INSTALLATION_TOKEN, "expires_at": "2099-01-01T00:00:00Z"})
        if request.method == "GET" and path.endswith("/issues"):
            self.list_auth.append(request.headers["Authorization"])
            return httpx.Response(200, json=[])
        return httpx.Response(404, json={})


def settings(tmp_path: Path, **overrides) -> Settings:
    data = {
        "db_path": tmp_path / "central.sqlite", "artifact_dir": tmp_path / "artifacts",
        "session_secret": "s", "operator_token": "o", "diag_api_url": "http://127.0.0.1:8100", "diag_api_token": "d",
        "github_token": ENV_TOKEN, "github_repos": (REPO,),
    }
    return Settings(**{**data, **overrides})


def source(**overrides) -> GitHubSourceConfig:
    data = {
        "source_id": "ghs-1a2b3c4d", "repository_full_name": REPO, "intake": "all_open",
        "start_at": "2026-10-06T00:00:00Z", "trigger_label": "runloom", "label_filter": [],
        "selected_issue_numbers": [], "run_mode": "auto", "enabled": True, "config_revision": 1,
    }
    return GitHubSourceConfig.model_validate({**data, **overrides})


def with_app(store: SecretStore, pem: str) -> None:
    store.write(secret_store.GITHUB_APP_INFO, json.dumps({"app_id": 1, "client_id": "Iv23liTEST"}))
    store.write(secret_store.GITHUB_APP_PRIVATE_KEY, pem)


def list_auth(client, fake: FakeGitHub, repo: str = REPO) -> str:
    client.list_issues(repo, IssueCursor())
    return fake.list_auth[-1]


def test_installation_source_uses_app_installation_token_before_pat_and_env(tmp_path, pem):
    store = SecretStore(tmp_path / "secrets")
    with_app(store, pem)
    store.write(secret_store.GITHUB_TOKEN, PAT)
    fake = FakeGitHub()
    client = client_for(source(installation_id=42), settings(tmp_path), store, transport=httpx.MockTransport(fake))
    assert list_auth(client, fake) == f"Bearer {INSTALLATION_TOKEN}"
    assert fake.token_requests == ["/app/installations/42/access_tokens"]


def test_installation_client_allows_only_the_installed_repository(tmp_path, pem):
    store = SecretStore(tmp_path / "secrets")
    with_app(store, pem)
    fake = FakeGitHub()
    client = client_for(source(repository_full_name="acme/shop", installation_id=42), settings(tmp_path), store,
                        transport=httpx.MockTransport(fake))
    assert list_auth(client, fake, "acme/shop") == f"Bearer {INSTALLATION_TOKEN}"  # 환경변수 목록 밖이어도
    with pytest.raises(GitHubRepositoryNotAllowed):
        client.list_issues(REPO, IssueCursor())  # 다른 저장소는 이 소스의 클라이언트로 부르지 않는다


def test_installation_source_without_saved_app_falls_back_to_pat(tmp_path):
    store = SecretStore(tmp_path / "secrets")
    store.write(secret_store.GITHUB_TOKEN, PAT)
    fake = FakeGitHub()
    client = client_for(source(installation_id=42), settings(tmp_path), store, transport=httpx.MockTransport(fake))
    assert list_auth(client, fake) == f"Bearer {PAT}"
    assert fake.token_requests == []


def test_pasted_pat_wins_over_env_token_and_allows_its_source_repository(tmp_path):
    store = SecretStore(tmp_path / "secrets")
    store.write(secret_store.GITHUB_TOKEN, PAT)
    fake = FakeGitHub()
    client = client_for(source(repository_full_name="acme/shop"), settings(tmp_path), store,
                        transport=httpx.MockTransport(fake))
    assert list_auth(client, fake, "acme/shop") == f"Bearer {PAT}"


def test_env_token_is_the_existing_behaviour(tmp_path):
    store = SecretStore(tmp_path / "secrets")
    fake = FakeGitHub()
    filtered = source(intake="filtered", label_filter=["bug"], workflow_repository_id="billing",
                      fix_verification_profile_id="vp-pytest", review_agent_id="agent-review")
    client = client_for(filtered, settings(tmp_path), store, transport=httpx.MockTransport(fake))
    assert list_auth(client, fake) == f"Bearer {ENV_TOKEN}"
    other = client_for(source(repository_full_name="acme/shop"), settings(tmp_path), store,
                       transport=httpx.MockTransport(fake))
    with pytest.raises(GitHubRepositoryNotAllowed):  # 환경변수 토큰은 WORKFLOW_GITHUB_REPOS 안만
        other.list_issues("acme/shop", IssueCursor())


def test_no_credentials_means_no_client(tmp_path):
    store = SecretStore(tmp_path / "secrets")
    assert client_for(source(), settings(tmp_path, github_token=""), store) is None
    assert client_for(source(installation_id=42), settings(tmp_path, github_token=""), store) is None


def test_source_clients_reuse_app_token_cache_and_follow_secret_changes(tmp_path, pem):
    store = SecretStore(tmp_path / "secrets")
    fake = FakeGitHub()
    clients = SourceClients(settings(tmp_path, github_token=""), store, transport=httpx.MockTransport(fake))
    installed = source(installation_id=42)
    assert clients(installed) is None  # 아직 아무 자격도 없다

    with_app(store, pem)  # central 이 App 을 저장한 뒤 — 워커 재시작 없이 따라온다
    first = clients(installed)
    assert clients(installed) is first  # 같은 자격이면 같은 클라이언트
    list_auth(first, fake)
    list_auth(clients(source(source_id="ghs-00000002", repository_full_name="acme/shop", installation_id=42)),
              fake, "acme/shop")
    assert fake.token_requests == ["/app/installations/42/access_tokens"]  # 설치 토큰 캐시를 소스끼리 같이 쓴다

    plain = source(source_id="ghs-00000003", repository_full_name="acme/web")
    assert clients(plain) is None
    store.write(secret_store.GITHUB_TOKEN, PAT)
    assert list_auth(clients(plain), fake, "acme/web") == f"Bearer {PAT}"
    store.write(secret_store.GITHUB_TOKEN, PAT + "_new")
    assert list_auth(clients(plain), fake, "acme/web") == f"Bearer {PAT}_new"  # 바꾼 PAT 을 바로 쓴다


def test_client_repr_and_errors_hold_no_secret(tmp_path, pem):
    store = SecretStore(tmp_path / "secrets")
    with_app(store, pem)
    store.write(secret_store.GITHUB_TOKEN, PAT)
    clients = SourceClients(settings(tmp_path), store, transport=httpx.MockTransport(FakeGitHub()))
    for client in (clients(source(installation_id=42)), clients(source(source_id="ghs-00000002"))):
        assert PAT not in repr(client) and ENV_TOKEN not in repr(client) and "PRIVATE" not in repr(client)
    assert PAT not in repr(clients) and "PRIVATE" not in repr(clients)


def test_credential_kind_follows_the_client_order_without_reading_values_into_the_answer(tmp_path, pem):
    """phase 11 step 8 — 연결 화면이 소스마다 "어떤 자격으로 수집하는지"(값 없이 종류만)를 보인다. 순서는 client_for 와 같다."""
    store = SecretStore(tmp_path / "secrets")
    bare = settings(tmp_path, github_token="")
    assert credential_kind(source(installation_id=42), bare, store) is None
    assert credential_kind(source(), settings(tmp_path), store) == "env"
    store.write(secret_store.GITHUB_TOKEN, PAT)
    assert credential_kind(source(installation_id=42), bare, store) == "pat"  # App 없으면 PAT 로 내려간다
    with_app(store, pem)
    assert credential_kind(source(installation_id=42), bare, store) == "app"
    assert credential_kind(source(), bare, store) == "pat"  # 설치 소스가 아니면 App 을 쓰지 않는다
