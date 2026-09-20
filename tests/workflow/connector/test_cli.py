"""cli — `python3 -m workflow.connector connect|register|run`. 토큰 파일 0600, 등록 보고, 검증 명령의 로컬 보관."""

import json
import os
import stat
import subprocess
import sys

import pytest

from workflow.connector import state
from workflow.connector.claude import ClaudeAdapter
from workflow.connector.cli import ADAPTERS, main
from workflow.connector.config import connector_paths

from .conftest import CONNECTOR_ID, TOKEN, FakeCentral, make_request
from .test_codex import RESPONSE_AFTER, make_repo, write_fake_codex


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


# --- 어댑터 factory --------------------------------------------------------------------------


def test_adapters_factory_builds_claude_adapter_from_env(env, tmp_path):
    paths = connector_paths(env)
    paths.home.mkdir(parents=True)
    conn = state.connect(paths.state_db)
    try:
        adapter = ADAPTERS["claude"](conn, {**env, "ANTHROPIC_MODEL": "claude-x", "OPENAI_API_KEY": "sk-" + "x" * 20})
    finally:
        conn.close()

    assert isinstance(adapter, ClaudeAdapter) and adapter.tool_name == "claude"
    assert adapter.tool_env()["ANTHROPIC_MODEL"] == "claude-x" and "OPENAI_API_KEY" not in adapter.tool_env()
    assert sorted(ADAPTERS) == ["claude", "codex", "echo"]


# --- run · help ------------------------------------------------------------------------


def test_run_requires_connect_first(env, capsys):
    assert main(["run", "--adapter", "echo"], env=env) == 2
    assert "connect" in capsys.readouterr().err


def test_help_exits_zero(capsys):
    with pytest.raises(SystemExit) as info:
        main(["--help"])
    assert info.value.code == 0
    out = capsys.readouterr().out
    assert "connect" in out and "register" in out and "run" in out and "run-local" in out


def test_module_help_runs():
    result = subprocess.run(["python3", "-m", "workflow.connector", "--help"], capture_output=True, text=True)
    assert result.returncode == 0 and "register" in result.stdout


# --- run-local -------------------------------------------------------------------------


def test_run_local_runs_adapter_once_and_writes_artifacts_to_out(env, tmp_path, monkeypatch, capsys):
    bin_dir = tmp_path / "fakebin"
    write_fake_codex(bin_dir, "full")
    path_env = f"{bin_dir}{os.pathsep}{os.environ['PATH']}"
    monkeypatch.setenv("PATH", path_env)
    repo = make_repo(tmp_path)
    paths = connector_paths(env)
    paths.home.mkdir(parents=True)
    conn = state.connect(paths.state_db)
    state.init_schema(conn)
    state.save_registration(conn, {
        "local_registration_id": "local-demo-report", "repo_path": str(repo), "tool": "codex",
        "repository_id": "demo-report-repo", "base_commit": _head(repo),
        "verification_profiles": {
            "vp-pytest": [sys.executable, "-m", "pytest", "-q"],
            "vp-report": [sys.executable, "-m", "daily_report", "{response}"],
        },
    })
    conn.close()
    request = make_request()
    request = request.model_copy(update={
        "target": type(request.target).model_validate({**request.target.model_dump(), "base_commit": _head(repo)}),
    })
    request_file = tmp_path / "request.json"
    request_file.write_text(request.model_dump_json())
    handoff = tmp_path / "handoff"
    handoff.mkdir()
    (handoff / "response-after@1.json").write_text(json.dumps(RESPONSE_AFTER, ensure_ascii=False))
    out = tmp_path / "out"

    code = main(
        ["run-local", "--request", str(request_file), "--handoff-dir", str(handoff), "--out", str(out)],
        env={**env, "PATH": path_env},
    )

    assert code == 0
    names = sorted(p.name for p in out.iterdir())
    assert names == [
        "code_change_result.json", "codex_jsonl.jsonl", "codex_stderr.txt", "diff.diff", "report_output.txt",
        "test_log_after.txt", "test_log_before.txt", "verification_log.txt",
    ]
    result = json.loads((out / "code_change_result.json").read_text())
    assert result["outcome"] == "ready_for_review" and result["result_commit"] != _head(repo)
    assert (out / "test_log_before.txt").read_text().splitlines()[0] == "exit_code=1"
    assert "합계    20    5" in (out / "report_output.txt").read_text()
    printed = capsys.readouterr()
    assert "ready_for_review" in printed.out and str(out) in printed.out


def test_run_local_with_failed_adapter_exits_nonzero(env, tmp_path, capsys):
    bin_dir = tmp_path / "fakebin"
    write_fake_codex(bin_dir, "forbidden")
    path_env = f"{bin_dir}{os.pathsep}{os.environ['PATH']}"
    request_file = tmp_path / "request.json"
    request_file.write_text(make_request().model_dump_json())  # 등록 없음 → registration_missing
    (tmp_path / "handoff").mkdir()

    code = main(
        ["run-local", "--request", str(request_file), "--handoff-dir", str(tmp_path / "handoff"),
         "--out", str(tmp_path / "out")],
        env={**env, "PATH": path_env},
    )

    assert code == 1
    assert json.loads((tmp_path / "out" / "failed.json").read_text())["code"] == "registration_missing"
    assert "registration_missing" in capsys.readouterr().err
