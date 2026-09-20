"""config — 연결 프로그램 경로와 토큰 파일. 토큰은 0600 파일에만 있고 repr 에 나오지 않는다."""

import os
import stat

from workflow.connector.config import connector_paths, read_token, write_token

from .conftest import CONNECTOR_ID, TOKEN


def test_connector_paths_default_and_override(tmp_path):
    default = connector_paths({"HOME": str(tmp_path)})
    assert default.home == tmp_path / "Library" / "Application Support" / "workflow-connector"
    assert default.state_db == default.home / "state.sqlite"

    custom = connector_paths({"WORKFLOW_CONNECTOR_HOME": str(tmp_path / "c")})
    assert (custom.home, custom.token_file, custom.log_dir) == (
        tmp_path / "c", tmp_path / "c" / "token.json", tmp_path / "c" / "logs"
    )


def test_write_token_creates_0600_file_in_0700_dir(tmp_path):
    paths = connector_paths({"WORKFLOW_CONNECTOR_HOME": str(tmp_path / "home")})
    assert read_token(paths) is None

    write_token(paths, CONNECTOR_ID, TOKEN, "https://central.example")

    assert stat.S_IMODE(os.stat(paths.token_file).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(paths.home).st_mode) == 0o700
    stored = read_token(paths)
    assert (stored.connector_id, stored.token, stored.server) == (CONNECTOR_ID, TOKEN, "https://central.example")


def test_write_token_overwrites_and_keeps_mode(tmp_path):
    paths = connector_paths({"WORKFLOW_CONNECTOR_HOME": str(tmp_path / "home")})
    write_token(paths, "conn-old", "wfc_old", "https://old.example")

    write_token(paths, CONNECTOR_ID, TOKEN, "https://central.example")

    assert read_token(paths).connector_id == CONNECTOR_ID
    assert stat.S_IMODE(os.stat(paths.token_file).st_mode) == 0o600


def test_stored_token_repr_hides_token(tmp_path):
    paths = connector_paths({"WORKFLOW_CONNECTOR_HOME": str(tmp_path / "home")})
    write_token(paths, CONNECTOR_ID, TOKEN, "https://central.example")

    stored = read_token(paths)

    assert TOKEN not in repr(stored) and TOKEN not in str(stored)
    assert CONNECTOR_ID in repr(stored)
