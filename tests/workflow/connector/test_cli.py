"""cli — `python3 -m workflow.connector connect|register|run`. 토큰 파일 0600, 등록 보고, 검증 명령의 로컬 보관."""

import json
import logging
import os
import stat
import subprocess
import sys

import pytest

from workflow.connector import state
from workflow.connector.claude import ClaudeAdapter
from workflow.connector.cli import ADAPTERS, main
from workflow.connector.config import connector_paths
from workflow.connector.runner import Runner

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


SECRET_VALUE = "postgresql://agent:s3cret-value@localhost:5434/openarchive"


def _git_repo(path, *, origin: str | None = None):
    path.mkdir(parents=True)
    (path / "README.md").write_text("demo")
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=path, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "add", "."], cwd=path, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "init"],
                   cwd=path, check=True)
    if origin is not None:
        subprocess.run(["git", "remote", "add", "origin", origin], cwd=path, check=True)
    return path


def _fake_tools(tmp_path, *names):
    """PATH 에 둘 가짜 도구 실행 파일. setup 의 --tool 기본값 판단용."""
    bin_dir = tmp_path / "tools"
    bin_dir.mkdir(exist_ok=True)
    for name in names:
        tool = bin_dir / name
        tool.write_text("#!/bin/sh\nexit 0\n")
        tool.chmod(0o755)
    return bin_dir


def _registration(env, local_registration_id):
    conn = state.connect(connector_paths(env).state_db)
    try:
        return state.get_registration(conn, local_registration_id)
    finally:
        conn.close()


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


def test_register_tool_claude_is_stored_locally_and_reported(env, repo):
    fake = FakeCentral()
    fake.connect_codes["code-1"] = CONNECTOR_ID
    main(["connect", "--server", "http://central.test", "--code", "code-1"], env=env, transport=fake.transport())

    code = main([
        "register", "--id", "local-demo-claude", "--repo", str(repo), "--repository-id", "demo-report-repo",
        "--tool", "claude",
    ], env=env, transport=fake.transport())

    assert code == 0
    assert fake.registrations[0]["tool"] == "claude"
    conn = state.connect(connector_paths(env).state_db)
    try:
        assert state.get_registration(conn, "local-demo-claude")["tool"] == "claude"
    finally:
        conn.close()


def test_register_rejects_unknown_tool(env, repo):
    with pytest.raises(SystemExit):
        main(["register", "--id", "x", "--repo", str(repo), "--repository-id", "r", "--tool", "gemini"], env=env)


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


def test_register_defaults_id_and_repository_id_from_folder_and_github_remote(env, tmp_path, connected):
    repo = _git_repo(tmp_path / "My Repo", origin="git@github.com:acme/Demo-Repo.git")

    code = main(["register", "--repo", str(repo)], env=env, transport=connected.transport())

    assert code == 0
    body = connected.registrations[0]
    assert body["local_registration_id"] == "my-repo"
    assert body["repository_id"] == "acme/Demo-Repo"
    assert body["agent_name"] == "My Repo"
    assert body["tool"] == "codex"  # register 의 기본은 호환을 위해 codex 그대로


def test_register_stores_links_and_env_locally_and_rerun_replaces_them(env, repo, connected):
    code = main([
        "register", "--id", "local-demo", "--repo", str(repo), "--repository-id", "r",
        "--link", "backend/.venv/", "--link", "./backend/.venv", "--link", "frontend/node_modules",
        "--env", f"DATABASE_URL={SECRET_VALUE}",
    ], env=env, transport=connected.transport())

    assert code == 0
    reg = _registration(env, "local-demo")
    assert reg["links"] == ["backend/.venv", "frontend/node_modules"]  # 정규화 · 중복 제거 · 선언 순서
    assert reg["env"] == {"DATABASE_URL": SECRET_VALUE}
    sent = json.dumps(connected.registrations[0])
    assert "DATABASE_URL" not in sent and SECRET_VALUE not in sent and "node_modules" not in sent

    assert main(["register", "--id", "local-demo", "--repo", str(repo), "--repository-id", "r"],
                env=env, transport=connected.transport()) == 0
    reg = _registration(env, "local-demo")
    assert reg["links"] == [] and reg["env"] == {}  # 등록은 선언 전체를 다시 쓴다


@pytest.mark.parametrize("link", ["../x", "/abs/path", ".git/hooks", "a/.git/config", "", "."])
def test_register_rejects_unsafe_link(env, repo, connected, link):
    with pytest.raises(SystemExit) as info:
        main(["register", "--id", "x", "--repo", str(repo), "--repository-id", "r", "--link", link],
             env=env, transport=connected.transport())
    assert info.value.code == 2
    assert connected.registrations == []


@pytest.mark.parametrize("name", [
    "OPENAI_API_KEY", "OPERATOR_TOKEN", "DIAG_API_TOKEN", "SESSION_SECRET", "WORKFLOW_GITHUB_TOKEN",
    "WORKFLOW_CONNECTOR_HOME", "PATH", "HOME", "1BAD", "BAD-NAME",
])
def test_register_rejects_reserved_or_malformed_env_name_without_echoing_value(env, repo, connected, capsys, name):
    with pytest.raises(SystemExit) as info:
        main(["register", "--id", "x", "--repo", str(repo), "--repository-id", "r",
              "--env", f"{name}={SECRET_VALUE}"], env=env, transport=connected.transport())
    assert info.value.code == 2
    assert connected.registrations == []
    printed = capsys.readouterr()
    assert SECRET_VALUE not in printed.err and SECRET_VALUE not in printed.out


def test_register_rejects_same_env_name_twice(env, repo, connected, capsys):
    with pytest.raises(SystemExit) as info:
        main(["register", "--id", "x", "--repo", str(repo), "--repository-id", "r",
              "--env", "DATABASE_URL=a", "--env", f"DATABASE_URL={SECRET_VALUE}"],
             env=env, transport=connected.transport())
    assert info.value.code == 2
    assert connected.registrations == [] and SECRET_VALUE not in capsys.readouterr().err


# --- setup -----------------------------------------------------------------------------


def test_setup_connects_and_registers_with_defaults(env, tmp_path, capsys):
    repo = _git_repo(tmp_path / "OpenArchive", origin="https://github.com/jeongeundev/OpenArchive.git")
    fake = FakeCentral()
    fake.connect_codes["code-1"] = CONNECTOR_ID
    run_env = {**env, "PATH": str(_fake_tools(tmp_path, "claude", "codex"))}

    code = main([
        "setup", "--server", "http://central.test", "--code", "code-1", "--repo", str(repo),
        "--verify", "vp-check=scripts/check.sh", "--link", "backend/.venv", "--link", "frontend/node_modules",
        "--env", f"DATABASE_URL={SECRET_VALUE}", "--env", "PYTHONDONTWRITEBYTECODE=1",
    ], env=run_env, transport=fake.transport())

    assert code == 0
    assert json.loads(connector_paths(env).token_file.read_text())["connector_id"] == CONNECTOR_ID
    body = fake.registrations[0]
    assert body["connector_id"] == CONNECTOR_ID
    assert body["local_registration_id"] == "openarchive"
    assert body["repository_id"] == "jeongeundev/OpenArchive"
    assert body["agent_name"] == "OpenArchive"
    assert body["tool"] == "claude"  # PATH 에 claude 가 있으면 claude 우선
    assert body["verification_profile_ids"] == ["vp-check"]
    sent = json.dumps(body)
    assert SECRET_VALUE not in sent and "DATABASE_URL" not in sent and "PYTHONDONTWRITEBYTECODE" not in sent
    reg = _registration(env, "openarchive")
    assert reg["tool"] == "claude" and reg["env"]["DATABASE_URL"] == SECRET_VALUE
    assert reg["links"] == ["backend/.venv", "frontend/node_modules"]

    printed = capsys.readouterr()
    summary = printed.out.strip().splitlines()[-1]
    assert "openarchive" in summary and "jeongeundev/OpenArchive" in summary and "vp-check" in summary
    assert "링크 2개" in summary and "DATABASE_URL" in summary and "PYTHONDONTWRITEBYTECODE" in summary
    assert "run" in summary and "install-runner" in summary
    assert SECRET_VALUE not in printed.out and SECRET_VALUE not in printed.err and TOKEN not in printed.out


def test_setup_without_github_remote_uses_folder_name_as_repository_id(env, tmp_path):
    repo = _git_repo(tmp_path / "local-only")
    fake = FakeCentral()
    fake.connect_codes["code-1"] = CONNECTOR_ID

    code = main(["setup", "--server", "http://central.test", "--code", "code-1", "--repo", str(repo)],
                env={**env, "PATH": str(_fake_tools(tmp_path, "codex"))}, transport=fake.transport())

    assert code == 0
    body = fake.registrations[0]
    assert body["repository_id"] == "local-only" and body["local_registration_id"] == "local-only"
    assert body["tool"] == "codex"  # claude 가 없으면 codex


def test_setup_skips_connect_when_token_for_same_server_exists(env, repo, connected):
    token_before = connector_paths(env).token_file.read_text()

    # 코드는 1회용이라 두 번째 교환은 실패한다 — 건너뛰었으면 성공
    code = main(["setup", "--server", "http://central.test/", "--code", "code-1", "--repo", str(repo),
                 "--tool", "codex"], env=env, transport=connected.transport())
    assert code == 0
    assert main(["setup", "--server", "http://central.test", "--repo", str(repo), "--tool", "codex"],
                env=env, transport=connected.transport()) == 0

    assert connector_paths(env).token_file.read_text() == token_before
    assert len(connected.registrations) == 2


def test_setup_without_token_requires_code(env, repo, capsys):
    code = main(["setup", "--server", "http://central.test", "--repo", str(repo), "--tool", "codex"], env=env)

    assert code == 2
    assert "--code" in capsys.readouterr().err
    assert not connector_paths(env).token_file.exists()


def test_setup_with_bad_code_does_not_register(env, repo):
    fake = FakeCentral()

    code = main(["setup", "--server", "http://central.test", "--code", "nope", "--repo", str(repo),
                 "--tool", "codex"], env=env, transport=fake.transport())

    assert code == 1
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


@pytest.fixture
def connected(env) -> FakeCentral:
    fake = FakeCentral()
    fake.connect_codes["code-1"] = CONNECTOR_ID
    main(["connect", "--server", "http://central.test", "--code", "code-1"], env=env, transport=fake.transport())
    return fake


def test_run_default_builds_codex_and_claude_adapters(env, connected, monkeypatch, caplog):
    monkeypatch.setattr(Runner, "run_forever", lambda self, *args, **kwargs: None)
    caplog.set_level(logging.INFO, logger="workflow.connector.cli")

    assert main(["run"], env=env, transport=connected.transport()) == 0

    assert "adapter=codex,claude" in caplog.text


def test_run_with_explicit_adapter_builds_only_that_one(env, connected, monkeypatch, caplog):
    monkeypatch.setattr(Runner, "run_forever", lambda self, *args, **kwargs: None)
    caplog.set_level(logging.INFO, logger="workflow.connector.cli")

    assert main(["run", "--adapter", "echo"], env=env, transport=connected.transport()) == 0

    assert "adapter=echo" in caplog.text and "codex" not in caplog.text


def test_run_passes_keep_workdirs_to_runner(env, connected, monkeypatch):
    built: list[dict] = []

    class FakeRunner:
        def __init__(self, **kwargs):
            built.append(kwargs)

        def run_forever(self, *args, **kwargs):
            pass

    monkeypatch.setattr("workflow.connector.cli.Runner", FakeRunner)

    assert main(["run", "--adapter", "echo"], env=env, transport=connected.transport()) == 0
    assert main(["run", "--adapter", "echo", "--keep-workdirs"], env=env, transport=connected.transport()) == 0

    assert [kwargs["keep_workdirs"] for kwargs in built] == [False, True]  # 기본은 정리, 플래그는 디버깅용 보존


def test_run_requires_connect_first(env, capsys):
    assert main(["run", "--adapter", "echo"], env=env) == 2
    assert "connect" in capsys.readouterr().err


def test_help_exits_zero(capsys):
    with pytest.raises(SystemExit) as info:
        main(["--help"])
    assert info.value.code == 0
    out = capsys.readouterr().out
    assert "connect" in out and "register" in out and "run" in out and "run-local" in out and "setup" in out


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
