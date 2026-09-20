"""cli — `python3 -m workflow.connector connect|register|run`. 토큰 파일 0600, 등록 보고, 검증 명령의 로컬 보관."""

import json
import os
import stat
import subprocess

import pytest

from workflow.connector import state
from workflow.connector.cli import main
from workflow.connector.config import connector_paths

from .conftest import CONNECTOR_ID, TOKEN, FakeCentral


@pytest.fixture
def env(tmp_path):
    return {"WORKFLOW_CONNECTOR_HOME": str(tmp_path / "home"), "HOME": str(tmp_path)}


@pytest.fixture
def repo(tmp_path):
    repo = tmp_path / "demo"
    repo.mkdir()
    (repo / "README.md").write_text("demo")
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "init"],
                   cwd=repo, check=True)
    return repo


def _head(repo) -> str:
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True,
                          text=True).stdout.strip()


# --- connect ---------------------------------------------------------------------------


def test_connect_exchanges_code_and_writes_token_file(env, capsys):
    fake = FakeCentral()
    fake.connect_codes["code-1"] = CONNECTOR_ID

    code = main(["connect", "--server", "http://central.test", "--code", "code-1"], env=env, transport=fake.transport())

    assert code == 0
    paths = connector_paths(env)
    assert stat.S_IMODE(os.stat(paths.token_file).st_mode) == 0o600
    assert json.loads(paths.token_file.read_text()) == {
        "connector_id": CONNECTOR_ID, "token": TOKEN, "server": "http://central.test"
    }
    out = capsys.readouterr()
    assert CONNECTOR_ID in out.out and TOKEN not in out.out and TOKEN not in out.err


def test_connect_with_bad_code_fails_without_writing(env, capsys):
    fake = FakeCentral()

    code = main(["connect", "--server", "http://central.test", "--code", "nope"], env=env, transport=fake.transport())

    assert code == 1
    assert not connector_paths(env).token_file.exists()
    assert "not_found" in capsys.readouterr().err


# --- register --------------------------------------------------------------------------


def test_register_reports_registration_and_stores_commands_locally(env, repo, capsys):
    fake = FakeCentral()
    fake.connect_codes["code-1"] = CONNECTOR_ID
    main(["connect", "--server", "http://central.test", "--code", "code-1"], env=env, transport=fake.transport())

    code = main([
        "register", "--id", "local-demo-report", "--repo", str(repo), "--repository-id", "demo-report-repo",
        "--verify", 'vp-pytest=python3 -m pytest -q', "--verify", "vp-report=python3 -m daily_report {response}",
    ], env=env, transport=fake.transport())

    assert code == 0
    assert len(fake.registrations) == 1
    body = fake.registrations[0]
    assert body["connector_id"] == CONNECTOR_ID
    assert body["local_registration_id"] == "local-demo-report"
    assert body["base_commit"] == _head(repo)
    assert body["verification_profile_ids"] == ["vp-pytest", "vp-report"]
    assert body["discovered"]["verification_level"] == "설정 발견"
    assert "pytest -q" not in json.dumps(body) and str(repo) not in json.dumps(body)

    conn = state.connect(connector_paths(env).state_db)
    try:
        reg = state.get_registration(conn, "local-demo-report")
    finally:
        conn.close()
    assert reg["repo_path"] == str(repo.resolve())
    assert reg["verification_profiles"] == {
        "vp-pytest": ["python3", "-m", "pytest", "-q"],
        "vp-report": ["python3", "-m", "daily_report", "{response}"],
    }
    assert reg["base_commit"] == _head(repo)
    assert "agent-codex-mac" in capsys.readouterr().out


def test_register_requires_connect_first(env, repo, capsys):
    code = main(["register", "--id", "x", "--repo", str(repo), "--repository-id", "r"], env=env)

    assert code == 2
    assert "connect" in capsys.readouterr().err


def test_register_rejects_bad_verify_format(env, repo):
    fake = FakeCentral()
    fake.connect_codes["code-1"] = CONNECTOR_ID
    main(["connect", "--server", "http://central.test", "--code", "code-1"], env=env, transport=fake.transport())

    with pytest.raises(SystemExit):
        main(["register", "--id", "x", "--repo", str(repo), "--repository-id", "r", "--verify", "no-equals"],
             env=env, transport=fake.transport())
    assert fake.registrations == []


# --- run · help ------------------------------------------------------------------------


def test_run_requires_connect_first(env, capsys):
    assert main(["run", "--adapter", "echo"], env=env) == 2
    assert "connect" in capsys.readouterr().err


def test_help_exits_zero(capsys):
    with pytest.raises(SystemExit) as info:
        main(["--help"])
    assert info.value.code == 0
    out = capsys.readouterr().out
    assert "connect" in out and "register" in out and "run" in out


def test_module_help_runs():
    result = subprocess.run(["python3", "-m", "workflow.connector", "--help"], capture_output=True, text=True)
    assert result.returncode == 0 and "register" in result.stdout
