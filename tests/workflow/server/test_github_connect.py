"""github_connect — App 설치·PAT 연결이 소스를 맞추는 규칙 (phase 11 step 7, ADR-0017, ARCHITECTURE "경로").

설치 저장소마다 `intake: all_open` 소스를 만들고(빈 칸은 자동 매칭 몫), 이미 있으면 `installation_id` 만 바꾸며,
설치에서 빠진 저장소는 수집만 멈춘다(삭제 없음). 다시 불러도 같은 결과다.
"""

import pytest

from workflow.adapters import repo
from workflow.adapters.github_app import InstalledRepository
from workflow.contracts.github import GitHubSourceConfig
from workflow.server.github_connect import ensure_token_source, sync_installation_sources

SESSION = "sess-op"
NOW = "2026-09-27T10:00:00Z"
LATER = "2026-09-27T11:00:00Z"


@pytest.fixture
def db(conn):
    repo.create_session(conn, SESSION, NOW)
    return conn


def repos(*names: str) -> list[InstalledRepository]:
    return [InstalledRepository(repository_id=100 + i, full_name=name) for i, name in enumerate(names)]


def sources(conn) -> dict[str, GitHubSourceConfig]:
    return {s.repository_full_name: s for s in repo.list_github_sources(conn, SESSION)}


def test_each_installed_repository_gets_an_all_open_source(db):
    changed = sync_installation_sources(db, SESSION, 42, repos("acme/billing", "acme/shop"), NOW)

    found = sources(db)
    assert sorted(found) == ["acme/billing", "acme/shop"]
    assert sorted(changed) == sorted(s.source_id for s in found.values())
    billing = found["acme/billing"]
    assert billing.intake == "all_open"
    assert billing.trigger_label == "runloom"
    assert billing.run_mode == "auto"
    assert billing.installation_id == 42
    assert billing.enabled is True
    assert billing.config_revision == 1
    assert billing.start_at == NOW
    assert billing.label_filter == [] and billing.selected_issue_numbers == []
    assert (billing.workflow_repository_id, billing.fix_verification_profile_id, billing.review_agent_id,
            billing.default_fix_agent_id) == (None, None, None, None)
    assert billing.source_id.startswith("ghs-")


def test_calling_again_changes_nothing(db):
    sync_installation_sources(db, SESSION, 42, repos("acme/billing"), NOW)
    before = sources(db)

    assert sync_installation_sources(db, SESSION, 42, repos("acme/billing"), LATER) == []
    assert sources(db) == before


def test_existing_source_only_gets_the_installation_id(db):
    filtered = GitHubSourceConfig(
        source_id="ghs-1a2b3c4d", repository_full_name="Acme/Billing", workflow_repository_id="billing",
        label_filter=["bug"], selected_issue_numbers=[], start_at="2026-09-01T00:00:00Z",
        fix_verification_profile_id="vp-pytest", review_agent_id="agent-review", run_mode="manual",
        enabled=True, config_revision=3,
    )
    repo.save_github_source(db, SESSION, filtered, NOW)

    assert sync_installation_sources(db, SESSION, 42, repos("acme/billing"), NOW) == ["ghs-1a2b3c4d"]

    (saved,) = repo.list_github_sources(db, SESSION)
    assert saved == filtered.model_copy(update={"installation_id": 42, "config_revision": 4})


def test_repository_removed_from_the_installation_stops_but_is_kept(db):
    sync_installation_sources(db, SESSION, 42, repos("acme/billing", "acme/shop"), NOW)
    shop = sources(db)["acme/shop"]
    token_only = GitHubSourceConfig(
        source_id="ghs-00000001", repository_full_name="acme/lib", label_filter=[], selected_issue_numbers=[],
        start_at=NOW, run_mode="auto", intake="all_open", enabled=True, config_revision=1,
    )
    repo.save_github_source(db, SESSION, token_only, NOW)

    changed = sync_installation_sources(db, SESSION, 42, repos("acme/billing"), LATER)

    after = sources(db)
    assert changed == [shop.source_id]
    assert after["acme/shop"] == shop.model_copy(update={"enabled": False, "config_revision": 2})
    assert after["acme/billing"].enabled is True
    assert after["acme/lib"] == token_only  # 다른 설치·토큰 소스는 건드리지 않는다
    assert sync_installation_sources(db, SESSION, 42, repos("acme/billing"), LATER) == []


def test_token_source_is_created_once(db):
    source_id = ensure_token_source(db, SESSION, "acme/lib", NOW)

    (saved,) = repo.list_github_sources(db, SESSION)
    assert saved.source_id == source_id
    assert (saved.intake, saved.trigger_label, saved.run_mode, saved.installation_id) == (
        "all_open", "runloom", "auto", None)
    assert ensure_token_source(db, SESSION, "ACME/lib", LATER) is None
    assert repo.list_github_sources(db, SESSION) == [saved]
