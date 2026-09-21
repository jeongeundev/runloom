"""local_stack.py — 로컬 5-프로세스 기동기의 계획(명령 배열·환경변수)만 검사한다. 실제 기동은 tests/e2e."""

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import local_stack
import seed_demo
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
    """connect 1번 + register 2번(codex·claude, 같은 폴더·저장소 ID·검증 프로필). 등록 ID 는 seed_demo 와 같은 값."""
    connect, registers = stack.connector_bootstrap("CODE-1")
    assert connect[1:4] == ["-m", "workflow.connector", "connect"]
    assert connect[connect.index("--server") + 1] == stack.central_url
    assert connect[connect.index("--code") + 1] == "CODE-1"

    assert len(registers) == 2
    by_tool = {r[r.index("--tool") + 1]: r for r in registers}
    assert list(by_tool) == ["codex", "claude"]  # codex 먼저 — 카탈로그 등록 순서와 무관하지만 로그 순서를 고정한다
    assert by_tool["codex"][by_tool["codex"].index("--id") + 1] == "local-demo-report"
    assert by_tool["claude"][by_tool["claude"].index("--id") + 1] == "local-demo-report-claude"
    for tool, register in by_tool.items():
        assert register[1:4] == ["-m", "workflow.connector", "register"]
        assert register[register.index("--id") + 1] == seed_demo.LOCAL_REGISTRATION_IDS[tool]
        assert register[register.index("--repo") + 1] == str(stack.repo_path)
        assert register[register.index("--repository-id") + 1] == "demo-report-repo"
        verifies = [register[i + 1] for i, a in enumerate(register) if a == "--verify"]
        assert verifies == ["vp-pytest=python3 -m pytest -q", "vp-report=python3 -m daily_report {response}"]


def test_connector_runs_auto_adapter_so_both_registrations_are_served(stack):
    """`run --adapter auto` 하나가 codex·claude 등록을 모두 실행한다 (runner.select_adapter 가 등록의 tool 로 고른다)."""
    argv = stack.services["connector"].argv
    assert argv[argv.index("--adapter") + 1] == "auto"


def _start_without_processes(monkeypatch, stack) -> tuple[list[dict], list[tuple[str, list[str]]]]:
    """start() 의 순서(seed 인자·1회 명령)만 기록한다. 프로세스는 띄우지 않는다."""
    seeds: list[dict] = []
    once: list[tuple[str, list[str]]] = []

    def fake_seed(db, artifacts, **kwargs):
        seeds.append({"db": db, "artifacts": artifacts, **kwargs})
        return {"connect_code": "CODE-X", "agents": []}

    monkeypatch.setattr(local_stack, "scaffold", lambda path, force: "a" * 40)
    monkeypatch.setattr(local_stack, "seed", fake_seed)
    monkeypatch.setattr(LocalStack, "_spawn", lambda self, name: None)
    monkeypatch.setattr(LocalStack, "_wait_http", lambda self, name, url, headers: None)
    monkeypatch.setattr(LocalStack, "_run_once", lambda self, name, argv: once.append((name, argv)))
    stack.start()
    return seeds, once


def test_start_seeds_plain_by_default_and_registers_both_tools(tmp_path, monkeypatch):
    stack = LocalStack(tmp_path, fake_codex=FAKE_CODEX)
    seeds, once = _start_without_processes(monkeypatch, stack)
    [call] = seeds
    assert call["scripted"] is False
    assert call["base_commit"] == "a" * 40 and call["diag_api_url"] == stack.diag_url
    assert [name for name, _ in once] == ["connector-connect", "connector-register-codex", "connector-register-claude"]
    assert once[0][1][once[0][1].index("--code") + 1] == "CODE-X"


def test_start_with_scripted_seeds_scripted(tmp_path, monkeypatch):
    stack = LocalStack(tmp_path, fake_codex=None, scripted=True)
    seeds, _ = _start_without_processes(monkeypatch, stack)
    assert seeds[0]["scripted"] is True


def test_log_tails_is_empty_before_start(stack):
    assert stack.log_tails() == {}


def test_start_service_respawns_a_stopped_service_only(stack, monkeypatch):
    """e2e 가 connector 를 내렸다가(`stop_service`) 다음 시나리오에서 다시 띄운다. 이미 도는 것은 건드리지 않는다."""
    spawned: list[str] = []
    monkeypatch.setattr(LocalStack, "_spawn", lambda self, name: spawned.append(name))
    stack.start_service("connector")
    assert spawned == ["connector"]

    class Alive:
        def poll(self):
            return None

    stack._procs["connector"] = Alive()
    stack.start_service("connector")
    assert spawned == ["connector"]  # 도는 중이면 no-op


def test_main_parser_defaults():
    args = local_stack.build_parser().parse_args([])
    assert args.workdir is None and args.central_port == 18000 and args.diag_port == 18100
    assert args.fake_codex is None


# --- --scripted: 대본 에이전트 래퍼 (workflow.scripted) ------------------------------------

PACE_ENV = "WORKFLOW_SCRIPT_PACE_SECONDS"


def test_scripted_installs_codex_and_claude_wrappers_ahead_on_connector_path_only(tmp_path):
    stack = LocalStack(tmp_path, fake_codex=None, scripted=True)

    bin_dir = stack.fake_bin
    assert bin_dir is not None
    for name in ("codex", "claude"):
        wrapper = bin_dir / name
        assert wrapper.exists() and os.access(wrapper, os.X_OK)
        lines = wrapper.read_text().splitlines()
        assert lines[0] == "#!/usr/bin/env bash" and len(lines) == 2
        assert f"-m workflow.scripted.{name}" in lines[1] and '"$@"' in lines[1]
    connector_path = stack.services["connector"].env["PATH"].split(os.pathsep)
    assert connector_path[0] == str(bin_dir)
    assert str(bin_dir) not in stack.services["central_worker"].env["PATH"].split(os.pathsep)


def test_scripted_wins_over_fake_codex(tmp_path):
    stack = LocalStack(tmp_path, fake_codex=FAKE_CODEX, scripted=True)
    assert "workflow.scripted.codex" in (stack.fake_bin / "codex").read_text()
    assert (stack.fake_bin / "claude").exists()


def test_fake_codex_alone_installs_no_claude_wrapper(stack):
    assert "workflow.scripted" not in (stack.fake_bin / "codex").read_text()
    assert not (stack.fake_bin / "claude").exists()


def test_script_pace_env_is_passed_to_connector_only(tmp_path, monkeypatch):
    monkeypatch.setenv(PACE_ENV, "25")
    stack = LocalStack(tmp_path, fake_codex=None, scripted=True)
    assert stack.services["connector"].env[PACE_ENV] == "25"
    for name in ("central_api", "central_worker", "diag_api", "diag_worker"):
        assert PACE_ENV not in stack.services[name].env


def test_script_pace_env_is_absent_when_not_set(tmp_path, monkeypatch):
    monkeypatch.delenv(PACE_ENV, raising=False)
    stack = LocalStack(tmp_path, fake_codex=None, scripted=True)
    assert PACE_ENV not in stack.services["connector"].env


def test_main_parser_scripted_flag():
    assert local_stack.build_parser().parse_args([]).scripted is False
    assert local_stack.build_parser().parse_args(["--scripted"]).scripted is True
