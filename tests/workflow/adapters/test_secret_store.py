"""secret_store.py — 0600 비밀 파일 (ADR-0017, ARCHITECTURE "비밀 파일"). 실제 임시 디렉터리로 돈다."""

import os
import stat

import pytest

from workflow.adapters import secret_store
from workflow.adapters.secret_store import (
    GITHUB_APP_CLIENT_SECRET,
    GITHUB_APP_INFO,
    GITHUB_APP_PRIVATE_KEY,
    GITHUB_APP_WEBHOOK_SECRET,
    GITHUB_TOKEN,
    SecretStore,
)

VALUE = "ghp_super-secret-value-123"


def _mode(path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_constants_are_the_architecture_file_names():
    assert (GITHUB_APP_INFO, GITHUB_APP_PRIVATE_KEY, GITHUB_APP_CLIENT_SECRET, GITHUB_APP_WEBHOOK_SECRET, GITHUB_TOKEN) == (
        "github_app.json",
        "github_app_private_key.pem",
        "github_app_client_secret",
        "github_app_webhook_secret",
        "github_token",
    )


def test_write_read_exists_delete_round_trip(tmp_path):
    store = SecretStore(tmp_path / "secrets")
    assert store.read(GITHUB_TOKEN) is None
    assert not store.exists(GITHUB_TOKEN)
    store.write(GITHUB_TOKEN, VALUE)
    assert store.read(GITHUB_TOKEN) == VALUE
    assert store.exists(GITHUB_TOKEN)
    store.write(GITHUB_TOKEN, "second")
    assert store.read(GITHUB_TOKEN) == "second"
    store.delete(GITHUB_TOKEN)
    assert store.read(GITHUB_TOKEN) is None
    assert not store.exists(GITHUB_TOKEN)
    store.delete(GITHUB_TOKEN)  # 없는 것 지우기는 멱등


def test_multiline_pem_round_trips(tmp_path):
    store = SecretStore(tmp_path / "secrets")
    pem = "-----BEGIN RSA PRIVATE KEY-----\nabc\ndef\n-----END RSA PRIVATE KEY-----\n"
    store.write(GITHUB_APP_PRIVATE_KEY, pem)
    assert store.read(GITHUB_APP_PRIVATE_KEY) == pem


def test_file_is_0600_and_directory_is_0700(tmp_path):
    root = tmp_path / "secrets"
    SecretStore(root).write(GITHUB_APP_CLIENT_SECRET, VALUE)
    assert _mode(root) == 0o700
    assert _mode(root / GITHUB_APP_CLIENT_SECRET) == 0o600


def test_existing_loose_directory_is_tightened_to_0700(tmp_path):
    root = tmp_path / "secrets"
    root.mkdir(mode=0o755)
    os.chmod(root, 0o755)
    SecretStore(root).write(GITHUB_TOKEN, VALUE)
    assert _mode(root) == 0o700


def test_no_temp_files_left_after_write(tmp_path):
    root = tmp_path / "secrets"
    store = SecretStore(root)
    store.write(GITHUB_TOKEN, VALUE)
    store.write(GITHUB_TOKEN, "again")
    assert [p.name for p in root.iterdir()] == [GITHUB_TOKEN]


def test_failed_write_keeps_previous_value_and_leaves_no_temp(tmp_path, monkeypatch):
    root = tmp_path / "secrets"
    store = SecretStore(root)
    store.write(GITHUB_TOKEN, "old")

    def boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(secret_store.os, "replace", boom)
    with pytest.raises(OSError):
        store.write(GITHUB_TOKEN, "new")
    monkeypatch.undo()
    assert store.read(GITHUB_TOKEN) == "old"
    assert [p.name for p in root.iterdir()] == [GITHUB_TOKEN]


@pytest.mark.parametrize(
    "name",
    ["../github_token", "github_token/../x", "/etc/passwd", "sub/github_token", "", ".", "..", "other_secret", "GITHUB_TOKEN"],
)
def test_names_outside_the_allow_list_are_rejected(tmp_path, name):
    root = tmp_path / "secrets"
    store = SecretStore(root)
    for call in (lambda: store.read(name), lambda: store.write(name, VALUE), lambda: store.delete(name), lambda: store.exists(name)):
        with pytest.raises(ValueError):
            call()
    assert not (tmp_path / "github_token").exists()
    assert not root.exists() or list(root.iterdir()) == []


def test_repr_and_errors_do_not_contain_values(tmp_path, monkeypatch):
    store = SecretStore(tmp_path / "secrets")
    store.write(GITHUB_TOKEN, VALUE)
    assert VALUE not in repr(store)
    with pytest.raises(ValueError) as exc:
        store.write("../x", VALUE)
    assert VALUE not in str(exc.value)

    def boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(secret_store.os, "replace", boom)
    with pytest.raises(OSError) as exc2:
        store.write(GITHUB_TOKEN, VALUE + "-new")
    assert VALUE not in str(exc2.value)


def test_from_env_reads_workflow_secret_dir_with_default(tmp_path):
    assert SecretStore.from_env({}).root == secret_store.Path("data/secrets")
    assert SecretStore.from_env({"WORKFLOW_SECRET_DIR": str(tmp_path / "s")}).root == tmp_path / "s"
