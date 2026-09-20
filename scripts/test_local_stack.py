"""local_stack.py — 로컬 5-프로세스 기동기의 계획(명령 배열·환경변수)만 검사한다. 실제 기동은 tests/e2e."""

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import local_stack
from local_stack import SERVICE_NAMES, LocalStack

FAKE_CODEX = Path(__file__).resolve().parents[1] / "tests" / "e2e" / "fake_codex.py"


@pytest.fixture
def stack(tmp_path) -> LocalStack:
    return LocalStack(tmp_path / "stack", fake_codex=FAKE_CODEX)


def test_service_names_and_order(stack):
    assert SERVICE_NAMES == ("diag_api", "diag_worker", "central_api", "central_worker", "connector")
    assert tuple(stack.services) == SERVICE_NAMES


def test_commands_use_agents_md_module_paths(stack):
    argv = {name: service.argv for name, service in stack.services.items()}
    assert all(a[0] == sys.executable for a in argv.values())
    assert argv["central_api"][1:4] == ["-m", "uvicorn", "workflow.server.app:app"]
    assert argv["central_worker"][1:] == ["-m", "workflow.server.worker"]
    assert argv["diag_api"][1:4] == ["-m", "uvicorn", "diagnostic_demo.api.app:app"]
    assert argv["diag_worker"][1:] == ["-m", "diagnostic_demo.worker"]
    assert argv["connector"][1:4] == ["-m", "workflow.connector", "run"]
    assert "--reload" not in argv["central_api"]  # 감시 프로세스가 생기면 stop 이 자식을 놓친다


def test_ports_are_in_uvicorn_args_and_urls(tmp_path):
    stack = LocalStack(tmp_path, central_port=18001, diag_port=18101, fake_codex=FAKE_CODEX)
    central = stack.services["central_api"].argv
    diag = stack.services["diag_api"].argv
    assert central[central.index("--port") + 1] == "18001"
    assert diag[diag.index("--port") + 1] == "18101"
    assert stack.central_url == "http://127.0.0.1:18001"
    assert stack.diag_url == "http://127.0.0.1:18101"
    assert stack.services["central_worker"].env["DIAG_API_URL"] == "http://127.0.0.1:18101"
    assert stack.services["central_api"].env["DIAG_API_URL"] == "http://127.0.0.1:18101"


def test_secrets_are_generated_and_shared_between_services(stack):
    central = stack.services["central_api"].env
    worker = stack.services["central_worker"].env
    diag_api = stack.services["diag_api"].env
    diag_worker = stack.services["diag_worker"].env

    for key in ("SESSION_SECRET", "OPERATOR_TOKEN", "DIAG_API_TOKEN"):
        assert len(central[key]) >= 32
        assert worker[key] == central[key]
    assert diag_api["DIAG_API_TOKEN"] == central["DIAG_API_TOKEN"]
    assert diag_worker["DIAG_API_TOKEN"] == central["DIAG_API_TOKEN"]
    assert central["WORKFLOW_DEV"] == "1" and diag_api["DIAG_DEV"] == "1"
    assert diag_api["DIAG_MODEL"] == "fake" and diag_worker["DIAG_MODEL"] == "fake"
    assert stack.operator_token == central["OPERATOR_TOKEN"]


def test_two_stacks_do_not_share_secrets(tmp_path):
    a = LocalStack(tmp_path / "a", fake_codex=FAKE_CODEX)
    b = LocalStack(tmp_path / "b", fake_codex=FAKE_CODEX)
    assert a.services["central_api"].env["DIAG_API_TOKEN"] != b.services["central_api"].env["DIAG_API_TOKEN"]


def test_data_paths_are_under_workdir(stack):
    workdir = stack.workdir
    central = stack.services["central_api"].env
    diag = stack.services["diag_api"].env
    connector = stack.services["connector"].env
    assert Path(central["WORKFLOW_DB_PATH"]).is_relative_to(workdir)
    assert Path(central["WORKFLOW_ARTIFACT_DIR"]).is_relative_to(workdir)
    assert Path(diag["DIAG_DB_PATH"]).is_relative_to(workdir)
    assert Path(diag["DIAG_ARTIFACT_DIR"]).is_relative_to(workdir)
    assert Path(connector["WORKFLOW_CONNECTOR_HOME"]) == workdir / "connector"
    assert stack.repo_path == workdir / "demo-report-repo"
    assert stack.logs_dir == workdir / "logs"
    assert Path(central["WORKFLOW_DB_PATH"]) != Path(diag["DIAG_DB_PATH"])


def test_heartbeat_offline_seconds_is_passed_to_central_and_matched_by_connector(stack):
    assert stack.services["central_api"].env["WORKFLOW_LIMIT_HEARTBEAT_OFFLINE_SECONDS"] == "10"
    assert stack.services["central_worker"].env["WORKFLOW_LIMIT_HEARTBEAT_OFFLINE_SECONDS"] == "10"
    argv = stack.services["connector"].argv
    assert float(argv[argv.index("--heartbeat-interval") + 1]) < 10


def test_fake_codex_shadows_real_codex_on_connector_path_only(stack):
    connector_path = stack.services["connector"].env["PATH"].split(os.pathsep)
    assert connector_path[0] == str(stack.fake_bin)
    assert (stack.fake_bin / "codex").exists() and os.access(stack.fake_bin / "codex", os.X_OK)
    assert str(stack.fake_bin) not in stack.services["central_worker"].env["PATH"].split(os.pathsep)


def test_without_fake_codex_path_is_untouched(tmp_path):
    stack = LocalStack(tmp_path, fake_codex=None)
    assert stack.fake_bin is None
    assert stack.services["connector"].env["PATH"] == os.environ["PATH"]


def test_inherited_workflow_and_openai_env_is_dropped(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKFLOW_SKIP_APP", "1")
    monkeypatch.setenv("DIAG_SKIP_APP", "1")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-should-not-leak")
    monkeypatch.setenv("WORKFLOW_DB_PATH", "/elsewhere.sqlite")
    stack = LocalStack(tmp_path, fake_codex=FAKE_CODEX)
    for service in stack.services.values():
        assert "WORKFLOW_SKIP_APP" not in service.env
        assert "DIAG_SKIP_APP" not in service.env
        assert "OPENAI_API_KEY" not in service.env
    assert stack.services["central_api"].env["WORKFLOW_DB_PATH"] != "/elsewhere.sqlite"


def test_connector_bootstrap_commands(stack):
    connect, register = stack.connector_bootstrap("CODE-1")
    assert connect[1:4] == ["-m", "workflow.connector", "connect"]
    assert connect[connect.index("--server") + 1] == stack.central_url
    assert connect[connect.index("--code") + 1] == "CODE-1"
    assert register[1:4] == ["-m", "workflow.connector", "register"]
    assert register[register.index("--id") + 1] == "local-demo-report"
    assert register[register.index("--repo") + 1] == str(stack.repo_path)
    assert register[register.index("--repository-id") + 1] == "demo-report-repo"
    verifies = [register[i + 1] for i, a in enumerate(register) if a == "--verify"]
    assert verifies == ["vp-pytest=python3 -m pytest -q", "vp-report=python3 -m daily_report {response}"]


def test_log_tails_is_empty_before_start(stack):
    assert stack.log_tails() == {}


def test_main_parser_defaults():
    args = local_stack.build_parser().parse_args([])
    assert args.workdir is None and args.central_port == 18000 and args.diag_port == 18100
    assert args.fake_codex is None
