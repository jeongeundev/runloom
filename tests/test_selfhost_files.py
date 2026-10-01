"""deploy/selfhost/ 의 Docker 이미지·compose·.env.example 과 루트 .dockerignore (phase 10 step 5) — 파일 내용만 검사한다.

검사 기준: ADR-0016, ARCHITECTURE "셀프호스트 — phase 10" 절(이름 고정표·컨테이너 환경변수 고정값),
settings 모듈의 ENV_KEYS. 컨테이너는 띄우지 않는다 — 실제 기동은 step 8. 도커가 있으면 `docker compose config` 만 돌린다.
"""

import os
import plistlib
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from workflow.server.settings import ENV_KEYS, OPTIONAL_SECRET_KEYS, SECRET_KEYS

ROOT = Path(__file__).resolve().parents[1]
SELFHOST = ROOT / "deploy" / "selfhost"
COMPOSE = SELFHOST / "compose.yaml"
DOCKERFILE = SELFHOST / "Dockerfile"
ENV_EXAMPLE = SELFHOST / ".env.example"
DOCKERIGNORE = ROOT / ".dockerignore"

# compose `environment` 가 고정하는 값 (ARCHITECTURE "구성") — .env 로 바꾸지 않는다
FIXED_ENV = {
    "WORKFLOW_MODE": "selfhost",
    "WORKFLOW_DB_PATH": "/data/central.sqlite",
    "WORKFLOW_ARTIFACT_DIR": "/data/artifacts",
    "WORKFLOW_BACKUP_DIR": "/data/backups",
    # 비밀 파일(ADR-0017) — 볼륨 안, artifacts 밖이라 백업에 들어가지 않는다
    "WORKFLOW_SECRET_DIR": "/data/secrets",
}
SERVICES = ("central", "worker")


def _compose() -> dict:
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))


def _env_example() -> dict[str, str]:
    out: dict[str, str] = {}
    for line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        assert "=" in line, f".env.example: KEY=VALUE 형식이 아님: {line!r}"
        key, _, value = line.partition("=")
        out[key.strip()] = value.strip().strip('"')
    return out


def _dockerfile_instructions() -> list[tuple[str, str]]:
    out = []
    for line in DOCKERFILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        op, _, rest = line.partition(" ")
        out.append((op.upper(), rest))
    return out


# --- compose -----------------------------------------------------------------------------------


def test_compose_has_central_and_worker_only():
    """진단 API·진단 워커·Caddy 는 compose 에 없다 (ADR-0016 결정 5)."""
    assert set(_compose()["services"]) == set(SERVICES)


def test_central_publishes_port_on_host_loopback_only():
    services = _compose()["services"]
    assert services["central"]["ports"] == ["127.0.0.1:${WORKFLOW_PORT:-8000}:8000"]
    assert "ports" not in services["worker"]
    command = services["central"]["command"]
    assert command[:2] == ["uvicorn", "workflow.server.app:app"]
    assert command[2:] == ["--host", "0.0.0.0", "--port", "8000"]
    assert "--reload" not in command


def test_worker_runs_the_agents_md_worker_module():
    assert _compose()["services"]["worker"]["command"] == ["python3", "-m", "workflow.server.worker"]


def test_both_services_share_one_named_volume_at_data():
    """bind mount 는 SQLite WAL 잠금이 보장되지 않아 쓰지 않는다 (ADR-0016 결정 1)."""
    compose = _compose()
    assert set(compose["volumes"]) == {"workflow-data"}
    for name in SERVICES:
        volumes = compose["services"][name]["volumes"]
        assert volumes == ["workflow-data:/data"], name
        for volume in volumes:
            source = volume.split(":", 1)[0]
            assert not source.startswith((".", "/", "~")), f"{name}: bind mount 금지 {volume}"


def test_both_services_fix_selfhost_mode_and_data_paths():
    for name in SERVICES:
        service = _compose()["services"][name]
        assert service["environment"] == FIXED_ENV, name
        assert service["env_file"] == [".env"] or service["env_file"] == ".env", name
        assert service["restart"] == "unless-stopped", name


def test_both_services_use_the_same_image_built_from_repo_root():
    services = _compose()["services"]
    for name in SERVICES:
        build = services[name]["build"]
        assert build == {"context": "../..", "dockerfile": "deploy/selfhost/Dockerfile"}, name
    assert services["central"]["image"] == services["worker"]["image"]


def test_central_healthcheck_hits_healthz_and_worker_waits_for_it():
    services = _compose()["services"]
    check = services["central"]["healthcheck"]
    assert "http://127.0.0.1:8000/healthz" in " ".join(check["test"])
    assert services["worker"]["depends_on"] == {"central": {"condition": "service_healthy"}}
    assert "healthcheck" not in services["worker"]


def test_compose_holds_no_secret_values():
    text = COMPOSE.read_text(encoding="utf-8")
    for key in (*SECRET_KEYS, *OPTIONAL_SECRET_KEYS):
        assert key not in text, key


def test_repo_holds_no_secret_looking_values_under_deploy():
    """저장소에 실제 값이 없어야 한다. 접두사 검사는 connector.masking 과 같은 규칙 (tests/test_deploy_files.py 에서 옮김).
    사용자의 실제 `deploy/selfhost/.env` 는 저장소 밖(.gitignore) 값이라 읽지 않는다."""
    for path in (ROOT / "deploy").rglob("*"):
        if path.is_file() and path != SELFHOST / ".env":
            text = path.read_text(encoding="utf-8", errors="replace")
            assert not re.search(r"\bwfc_[A-Za-z0-9_-]{8,}", text), path
            assert not re.search(r"\bsk-[A-Za-z0-9_-]{8,}", text), path


# --- .env.example ------------------------------------------------------------------------------


def test_env_example_keys_are_settings_keys_minus_compose_fixed_plus_port():
    """`load_settings` 가 읽는 키 중 compose 가 고정하지 않는 것 + compose 치환용 WORKFLOW_PORT."""
    expected = (set(ENV_KEYS) - set(FIXED_ENV)) | {"WORKFLOW_PORT"}
    assert set(_env_example()) == expected


def test_env_example_leaves_secrets_empty_and_defaults_port():
    env = _env_example()
    for key in (*SECRET_KEYS, *OPTIONAL_SECRET_KEYS):
        assert env[key] == "", f"{key} 는 비어 있어야 한다"
    assert env["WORKFLOW_PORT"] == "8000"


def test_env_example_public_url_is_empty_and_explains_login_use():
    """phase 15 step 4 — 공개 주소는 선택. 링크·Origin 검사·로그인 쿠키 Secure 에 쓰인다는 설명이 붙는다."""
    assert _env_example()["WORKFLOW_PUBLIC_URL"] == ""
    lines = ENV_EXAMPLE.read_text(encoding="utf-8").splitlines()
    comment = lines[lines.index("WORKFLOW_PUBLIC_URL=") - 1]
    assert "https" in comment and "Secure" in comment


def test_env_example_explains_every_key():
    """키마다 바로 위에 설명 주석이 있다."""
    lines = ENV_EXAMPLE.read_text(encoding="utf-8").splitlines()
    for i, line in enumerate(lines):
        if line and not line.startswith("#"):
            assert i > 0 and lines[i - 1].startswith("#"), f"설명 없는 키: {line}"


# --- Dockerfile --------------------------------------------------------------------------------


def test_dockerfile_uses_python_313_slim_and_runs_as_non_root():
    ins = _dockerfile_instructions()
    froms = [rest for op, rest in ins if op == "FROM"]
    assert froms and all(rest.startswith("python:3.13-slim") for rest in froms)
    users = [rest for op, rest in ins if op == "USER"]
    assert users, "USER 가 있어야 한다"
    assert users[-1] not in ("root", "0")


def test_dockerfile_installs_package_without_dev_extras():
    runs = " ".join(rest for op, rest in _dockerfile_instructions() if op == "RUN")
    assert re.search(r"pip install [^&]*--no-cache-dir", runs)
    assert "[dev]" not in runs and "-e " not in runs


def test_dockerfile_copies_only_sources():
    copies = [rest for op, rest in _dockerfile_instructions() if op == "COPY"]
    sources = {part for rest in copies for part in rest.split()[:-1] if not part.startswith("--")}
    assert sources == {"pyproject.toml", "src"}


def test_dockerfile_has_no_secret_args_or_envs():
    for op, rest in _dockerfile_instructions():
        if op in ("ARG", "ENV"):
            for key in (*SECRET_KEYS, *OPTIONAL_SECRET_KEYS, "TOKEN", "SECRET", "KEY"):
                assert key not in rest.upper(), f"{op} {rest}"


# --- .dockerignore -----------------------------------------------------------------------------


def test_dockerignore_excludes_data_env_git_and_test_outputs():
    entries = {line.strip() for line in DOCKERIGNORE.read_text(encoding="utf-8").splitlines()}
    for entry in ("data/", ".env", "**/.env", ".git", "phases/", ".pytest_cache", "**/__pycache__"):
        assert entry in entries, entry


# --- docker compose config (정적 검사 — 컨테이너를 띄우지 않는다) --------------------------------


@pytest.mark.skipif(shutil.which("docker") is None, reason="docker 없음")
def test_docker_compose_config_accepts_the_file(tmp_path):
    """env_file 이 있어야 config 가 통과하므로 저장소 밖 임시 디렉터리에 compose 와 .env(= .env.example)를 둔다."""
    version = subprocess.run(["docker", "compose", "version"], capture_output=True, text=True)
    if version.returncode != 0:
        pytest.skip("docker compose 없음")
    shutil.copy(COMPOSE, tmp_path / "compose.yaml")
    shutil.copy(ENV_EXAMPLE, tmp_path / ".env")
    res = subprocess.run(
        ["docker", "compose", "-f", str(tmp_path / "compose.yaml"), "config", "--format", "json"],
        capture_output=True, text=True, cwd=tmp_path,
    )
    assert res.returncode == 0, res.stderr
    assert '"published": "8000"' in res.stdout and '"host_ip": "127.0.0.1"' in res.stdout


# --- install.sh (step 6) — 가짜 docker·curl 을 PATH 앞에 두고 임시 디렉터리에서 실행한다 -----------

INSTALL = SELFHOST / "install.sh"
INSTALL_RUNNER = SELFHOST / "install-runner.sh"
BASE_PATH = "/usr/bin:/bin:/usr/sbin:/sbin"

FAKE_DOCKER = """#!/bin/bash
echo "$*" >> "$FAKE_LOG"
exit 0
"""
# FAKE_CURL_FAIL=1 이면 연결 실패, 아니면 healthz ok
FAKE_CURL = """#!/bin/bash
echo "curl $*" >> "$FAKE_LOG"
if [ "${FAKE_CURL_FAIL:-0}" = 1 ]; then exit 7; fi
echo '{"status":"ok","mode":"selfhost","schema_version":6}'
"""
# print: FAKE_PRINT_LOADED 번까지 "아직 적재됨"(0), 그 뒤 "없음"(113).
# bootstrap: FAKE_BOOTSTRAP_FAILS 번까지 5(Input/output error), 그 뒤 성공.
FAKE_LAUNCHCTL = """#!/bin/bash
echo "launchctl $*" >> "$FAKE_LOG"
count() { local f="$FAKE_LOG.$1"; local n=$(( $(cat "$f" 2>/dev/null || echo 0) + 1 )); echo "$n" > "$f"; echo "$n"; }
case "$1" in
  print) [ "$(count print)" -le "${FAKE_PRINT_LOADED:-0}" ] && exit 0; exit 113 ;;
  bootstrap) [ "$(count bootstrap)" -le "${FAKE_BOOTSTRAP_FAILS:-0}" ] && { echo "Bootstrap failed: 5: Input/output error" >&2; exit 5; } ;;
esac
exit 0
"""


def _fake_bin(tmp_path: Path, **scripts: str) -> Path:
    bin_dir = tmp_path / "fakebin"
    bin_dir.mkdir(exist_ok=True)
    for name, body in scripts.items():
        path = bin_dir / name
        path.write_text(body, encoding="utf-8")
        path.chmod(0o755)
    return bin_dir


def _install_copy(tmp_path: Path) -> Path:
    """저장소의 deploy/selfhost/.env 를 건드리지 않게 install.sh·.env.example·compose.yaml 을 복사한다."""
    target = tmp_path / "repo" / "deploy" / "selfhost"
    target.mkdir(parents=True)
    for src in (INSTALL, ENV_EXAMPLE, COMPOSE):
        shutil.copy(src, target / src.name)
    return target


def _run_install(tmp_path: Path, selfhost: Path, *args: str, with_docker: bool = True, **env: str):
    scripts = {"curl": FAKE_CURL}
    if with_docker:
        scripts["docker"] = FAKE_DOCKER
    bin_dir = _fake_bin(tmp_path, **scripts)
    full_env = {
        "PATH": f"{bin_dir}:{BASE_PATH}",
        "HOME": str(tmp_path / "home"),
        "FAKE_LOG": str(tmp_path / "calls.log"),
        "HEALTH_TIMEOUT": "2",
        **env,
    }
    return subprocess.run(
        ["bash", str(selfhost / "install.sh"), *args], capture_output=True, text=True, env=full_env,
    )


def _env_values(path: Path) -> dict[str, str]:
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            out[key] = value
    return out


def _calls(tmp_path: Path) -> list[str]:
    log = tmp_path / "calls.log"
    return log.read_text(encoding="utf-8").splitlines() if log.exists() else []


def test_install_scripts_are_executable_bash_with_strict_mode():
    for script in (INSTALL, INSTALL_RUNNER):
        assert script.is_file(), script
        assert script.stat().st_mode & 0o111, f"{script.name} 에 실행 권한이 없다"
        text = script.read_text(encoding="utf-8")
        assert text.startswith("#!/usr/bin/env bash") or text.startswith("#!/bin/bash")
        assert "set -euo pipefail" in text


def test_install_creates_env_0600_with_generated_secrets(tmp_path):
    selfhost = _install_copy(tmp_path)
    res = _run_install(tmp_path, selfhost)
    assert res.returncode == 0, res.stdout + res.stderr
    env_file = selfhost / ".env"
    assert env_file.stat().st_mode & 0o777 == 0o600
    values = _env_values(env_file)
    assert re.fullmatch(r"[0-9a-f]{64}", values["SESSION_SECRET"])
    assert re.fullmatch(r"[0-9a-f]{64}", values["OPERATOR_TOKEN"])
    assert values["SESSION_SECRET"] != values["OPERATOR_TOKEN"]
    # 나머지 키는 .env.example 그대로
    example = _env_example()
    assert set(values) == set(example)
    assert values["WORKFLOW_PORT"] == "8000"
    assert not {"DIAG_API_TOKEN", "DIAG_API_URL"} & set(values)  # 진단 데모는 `main` 전용 (ADR-0019)
    # 출력에 비밀값이 없고 토큰 파일 위치·접속 주소·다음 할 일이 있다
    out = res.stdout + res.stderr
    assert values["OPERATOR_TOKEN"] not in out and values["SESSION_SECRET"] not in out
    assert str(env_file) in out
    assert "http://127.0.0.1:8000/login" in out
    assert "install-runner.sh" in out
    # phase 15 — 운영자 토큰은 첫 설정용: 처음 접속 때 토큰으로 관리자 계정을 만든다
    assert "처음 접속 때 토큰으로 관리자 계정을 만든다" in out


def test_install_runs_compose_with_project_name_and_file_then_waits_for_healthz(tmp_path):
    selfhost = _install_copy(tmp_path)
    res = _run_install(tmp_path, selfhost)
    assert res.returncode == 0, res.stdout + res.stderr
    calls = _calls(tmp_path)
    up = [c for c in calls if " up " in f" {c} "]
    assert up == [f"compose -p runloom -f {selfhost / 'compose.yaml'} up -d --build"]
    assert any(c.startswith("curl ") and "http://127.0.0.1:8000/healthz" in c for c in calls)
    assert calls.index(up[0]) < next(i for i, c in enumerate(calls) if c.startswith("curl "))


def test_install_honours_project_name_and_port_from_env_file(tmp_path):
    selfhost = _install_copy(tmp_path)
    (selfhost / ".env").write_text("WORKFLOW_PORT=8123\nSESSION_SECRET=a\nOPERATOR_TOKEN=b\n", encoding="utf-8")
    (selfhost / ".env").chmod(0o600)
    res = _run_install(tmp_path, selfhost, RUNLOOM_PROJECT="rl-test")
    assert res.returncode == 0, res.stdout + res.stderr
    calls = _calls(tmp_path)
    assert f"compose -p rl-test -f {selfhost / 'compose.yaml'} up -d --build" in calls
    assert any("http://127.0.0.1:8123/healthz" in c for c in calls)
    assert "http://127.0.0.1:8123/login" in res.stdout


def test_install_rerun_keeps_existing_env_and_rebuilds(tmp_path):
    selfhost = _install_copy(tmp_path)
    assert _run_install(tmp_path, selfhost).returncode == 0
    env_file = selfhost / ".env"
    before = env_file.read_bytes()
    res = _run_install(tmp_path, selfhost)
    assert res.returncode == 0, res.stdout + res.stderr
    assert env_file.read_bytes() == before
    ups = [c for c in _calls(tmp_path) if " up -d --build" in c]
    assert len(ups) == 2
    # 볼륨을 지우는 명령은 없다
    assert not any(" down" in c or "-v" in c.split() or "volume rm" in c for c in _calls(tmp_path))


def test_install_fails_when_healthz_never_ok(tmp_path):
    selfhost = _install_copy(tmp_path)
    res = _run_install(tmp_path, selfhost, FAKE_CURL_FAIL="1", HEALTH_TIMEOUT="1")
    assert res.returncode != 0
    assert "logs" in res.stdout + res.stderr
    values = _env_values(selfhost / ".env")
    assert values["OPERATOR_TOKEN"] not in res.stdout + res.stderr


def test_install_without_docker_explains_and_exits(tmp_path):
    selfhost = _install_copy(tmp_path)
    res = _run_install(tmp_path, selfhost, with_docker=False)
    assert res.returncode != 0
    assert "Docker" in res.stdout + res.stderr
    assert not (selfhost / ".env").exists()


def test_install_dry_run_changes_nothing(tmp_path):
    selfhost = _install_copy(tmp_path)
    res = _run_install(tmp_path, selfhost, DRY_RUN="1")
    assert res.returncode == 0, res.stdout + res.stderr
    assert not (selfhost / ".env").exists()
    assert not any(" up " in f" {c} " or c.startswith("curl ") for c in _calls(tmp_path))
    assert "up -d --build" in res.stdout and "-p runloom" in res.stdout


def test_install_help(tmp_path):
    selfhost = _install_copy(tmp_path)
    res = _run_install(tmp_path, selfhost, "--help")
    assert res.returncode == 0
    assert "DRY_RUN" in res.stdout and "RUNLOOM_PROJECT" in res.stdout
    assert not (selfhost / ".env").exists() and _calls(tmp_path) == []


# --- install-runner.sh (step 6) ---------------------------------------------------------------


def _run_runner(tmp_path: Path, *args: str, **env: str):
    bin_dir = _fake_bin(tmp_path, launchctl=FAKE_LAUNCHCTL, claude="#!/bin/bash\n", codex="#!/bin/bash\n")
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    full_env = {
        "PATH": f"{bin_dir}:{BASE_PATH}",
        "HOME": str(home),
        "FAKE_LOG": str(tmp_path / "calls.log"),
        "PYTHON": sys.executable,
        **env,
    }
    return subprocess.run(["bash", str(INSTALL_RUNNER), *args], capture_output=True, text=True, env=full_env)


def test_install_runner_dry_run_prints_plist_with_real_home_and_no_token(tmp_path):
    res = _run_runner(tmp_path, DRY_RUN="1")
    assert res.returncode == 0, res.stdout + res.stderr
    home = tmp_path / "home"
    plist_path = home / "Library" / "LaunchAgents" / "com.workflow.selfhost.connector.plist"
    out = res.stdout
    assert str(plist_path) in out
    assert not plist_path.exists() and _calls(tmp_path) == []
    start, end = out.index("<?xml"), out.index("</plist>") + len("</plist>")
    plist = plistlib.loads(out[start:end].encode())
    assert plist["Label"] == "com.workflow.selfhost.connector"
    assert plist["ProgramArguments"][1:] == ["-m", "workflow.connector", "run"]
    assert Path(plist["ProgramArguments"][0]).is_absolute()
    assert plist["KeepAlive"] is True and plist["RunAtLoad"] is True
    assert plist["WorkingDirectory"] == str(ROOT)
    assert plist["StandardOutPath"].startswith(str(home))
    path_dirs = plist["EnvironmentVariables"]["PATH"].split(":")
    assert str(tmp_path / "fakebin") in path_dirs  # claude·codex 위치
    assert "USERNAME" not in out[start:end] and "~" not in out[start:end]
    assert "TOKEN" not in out[start:end] and "wfc_" not in out[start:end]
    # 사용자가 할 명령: 연결 코드 교환·저장소 등록 (서버 주소는 127.0.0.1:포트)
    assert "workflow.connector connect --server http://127.0.0.1:8000" in out
    assert "workflow.connector register" in out
    assert "pip install -e" in out


def test_install_runner_uses_workflow_port(tmp_path):
    res = _run_runner(tmp_path, DRY_RUN="1", WORKFLOW_PORT="8123")
    assert res.returncode == 0, res.stdout + res.stderr
    assert "--server http://127.0.0.1:8123" in res.stdout


def test_install_runner_writes_plist_and_loads_it_when_connected(tmp_path):
    connector_home = tmp_path / "connector"
    connector_home.mkdir()
    (connector_home / "token.json").write_text("{}", encoding="utf-8")
    res = _run_runner(tmp_path, WORKFLOW_CONNECTOR_HOME=str(connector_home), SKIP_PIP_INSTALL="1")
    assert res.returncode == 0, res.stdout + res.stderr
    plist_path = tmp_path / "home" / "Library" / "LaunchAgents" / "com.workflow.selfhost.connector.plist"
    plist = plistlib.loads(plist_path.read_bytes())
    assert plist["Label"] == "com.workflow.selfhost.connector"
    calls = _calls(tmp_path)
    assert any(c.startswith("launchctl bootstrap") and str(plist_path) in c for c in calls)


def _run_connected_runner(tmp_path: Path, **env: str):
    connector_home = tmp_path / "connector"
    connector_home.mkdir()
    (connector_home / "token.json").write_text("{}", encoding="utf-8")
    return _run_runner(tmp_path, WORKFLOW_CONNECTOR_HOME=str(connector_home), SKIP_PIP_INSTALL="1", **env)


def test_install_runner_waits_for_bootout_before_bootstrap(tmp_path):
    # bootout 은 서비스가 내려가기 전에 돌아온다 — 사라질 때까지 기다린 뒤 bootstrap (2026-09-30 재설치 때 5 실패)
    res = _run_connected_runner(tmp_path, FAKE_PRINT_LOADED="2")
    assert res.returncode == 0, res.stdout + res.stderr
    verbs = [c.split()[1] for c in _calls(tmp_path) if c.startswith("launchctl")]
    assert verbs == ["bootout", "print", "print", "print", "bootstrap"]


def test_install_runner_retries_bootstrap_after_io_error(tmp_path):
    res = _run_connected_runner(tmp_path, FAKE_BOOTSTRAP_FAILS="1")
    assert res.returncode == 0, res.stdout + res.stderr
    assert sum(c.startswith("launchctl bootstrap") for c in _calls(tmp_path)) == 2
    assert "러너 적재" in res.stdout


def test_install_runner_fails_when_bootstrap_keeps_failing(tmp_path):
    res = _run_connected_runner(tmp_path, FAKE_BOOTSTRAP_FAILS="9")
    assert res.returncode != 0
    assert sum(c.startswith("launchctl bootstrap") for c in _calls(tmp_path)) == 3
    assert "러너 적재" not in res.stdout
    assert "launchctl bootstrap" in res.stderr  # 사용자가 직접 칠 명령 안내


def test_install_runner_does_not_load_before_connect(tmp_path):
    res = _run_runner(tmp_path, WORKFLOW_CONNECTOR_HOME=str(tmp_path / "none"), SKIP_PIP_INSTALL="1")
    assert res.returncode == 0, res.stdout + res.stderr
    assert (tmp_path / "home" / "Library" / "LaunchAgents" / "com.workflow.selfhost.connector.plist").exists()
    assert not any(c.startswith("launchctl bootstrap") for c in _calls(tmp_path))
    assert "connect --server" in res.stdout


def test_install_runner_help(tmp_path):
    res = _run_runner(tmp_path, "--help")
    assert res.returncode == 0
    assert "DRY_RUN" in res.stdout and _calls(tmp_path) == []


# --- install-runner.sh --server --code --repo (phase 12 step 9, ADR-0018) ------------------------------

RUNNER_CODE = "AbCdEfGhIjKlMnOpQrStUvWx0123"
ENV_VALUE = "postgresql://agent:s3cretPW@127.0.0.1:5434/oa"


def _plist_in(out: str) -> tuple[dict, str]:
    start, end = out.index("<?xml"), out.index("</plist>") + len("</plist>")
    return plistlib.loads(out[start:end].encode()), out[start:end]


def test_install_runner_with_code_dry_run_prints_setup_then_launchd(tmp_path):
    folder = tmp_path / "OpenArchive"
    res = _run_runner(
        tmp_path, "--server", "http://127.0.0.1:8000", "--code", RUNNER_CODE, "--repo", str(folder),
        "--tool", "claude", "--verify", "check=scripts/check.sh", "--link", "backend/.venv",
        "--copy", "frontend/node_modules", "--env", f"DATABASE_URL={ENV_VALUE}", DRY_RUN="1",
    )
    assert res.returncode == 0, res.stdout + res.stderr
    out = res.stdout
    assert _calls(tmp_path) == []
    lines = out.splitlines()
    pip = next(i for i, line in enumerate(lines) if "pip install -e" in line)
    setup = next(i for i, line in enumerate(lines) if "-m workflow.connector setup" in line)
    boot = next(i for i, line in enumerate(lines) if "launchctl bootstrap" in line)
    assert pip < setup < boot
    setup_line = lines[setup]
    for part in ("--server http://127.0.0.1:8000", f"--repo {folder}", "--tool claude",
                 "--verify check=scripts/check.sh", "--link backend/.venv", "--copy frontend/node_modules",
                 "--env DATABASE_URL="):
        assert part in setup_line, part
    # 코드·env 값은 출력하지 않는다
    assert RUNNER_CODE not in out and ENV_VALUE not in out and "s3cretPW" not in out
    plist, raw = _plist_in(out)
    assert plist["ProgramArguments"][1:] == ["-m", "workflow.connector", "run"]
    assert plist["EnvironmentVariables"]["HOME"] == str(tmp_path / "home")  # git push·fetch 자격 위치
    assert "--code" not in raw and "DATABASE_URL" not in raw
    # 인자로 붙였으면 수동 connect/register 안내는 없다
    assert "workflow.connector connect" not in out


def test_install_runner_plist_has_home_without_args_too(tmp_path):
    res = _run_runner(tmp_path, DRY_RUN="1")
    assert res.returncode == 0, res.stdout + res.stderr
    plist, _ = _plist_in(res.stdout)
    assert plist["EnvironmentVariables"]["HOME"] == str(tmp_path / "home")
    assert "workflow.connector setup" not in res.stdout


@pytest.mark.parametrize("args", [
    ("--server", "http://127.0.0.1:8000"),
    ("--server", "http://127.0.0.1:8000", "--code", RUNNER_CODE),
    ("--repo", "/tmp/x", "--code", RUNNER_CODE),
    ("--tool", "claude"),
    ("--bogus",),
])
def test_install_runner_needs_server_code_repo_together(tmp_path, args):
    res = _run_runner(tmp_path, *args, DRY_RUN="1")
    assert res.returncode == 2
    assert "--server" in res.stderr and _calls(tmp_path) == []
    assert RUNNER_CODE not in res.stdout + res.stderr


def test_install_runner_stops_before_launchd_when_setup_fails(tmp_path):
    folder = tmp_path / "repo"
    folder.mkdir()
    subprocess.run(["git", "init", "-q", str(folder)], check=True)
    res = _run_runner(
        tmp_path, "--server", "http://127.0.0.1:1", "--code", RUNNER_CODE, "--repo", str(folder),
        SKIP_PIP_INSTALL="1", WORKFLOW_CONNECTOR_HOME=str(tmp_path / "connector"),
    )
    assert res.returncode != 0
    assert not (tmp_path / "home" / "Library" / "LaunchAgents" / "com.workflow.selfhost.connector.plist").exists()
    assert not any(c.startswith("launchctl") for c in _calls(tmp_path))
    assert RUNNER_CODE not in res.stdout + res.stderr


# --- install-runner.sh --name (phase 17 step 10, ARCHITECTURE "러너 두 대 설치") --------------------------------


def test_install_runner_name_splits_label_plist_logs_and_home(tmp_path):
    folder = tmp_path / "repo"
    res = _run_runner(
        tmp_path, "--name", "b", "--server", "http://127.0.0.1:8000", "--code", RUNNER_CODE, "--repo", str(folder),
        "--env", f"DATABASE_URL={ENV_VALUE}", DRY_RUN="1",
    )
    assert res.returncode == 0, res.stdout + res.stderr
    out = res.stdout
    home = tmp_path / "home"
    label = "com.workflow.selfhost.connector.b"
    plist_path = home / "Library" / "LaunchAgents" / f"{label}.plist"
    runner_home = home / "Library" / "Application Support" / "workflow-connector-b"
    log_dir = home / "Library" / "Logs" / "workflow-connector-selfhost-b"
    plist, raw = _plist_in(out)
    assert plist["Label"] == label
    assert plist["StandardOutPath"] == str(log_dir / "stdout.log")
    assert plist["EnvironmentVariables"]["WORKFLOW_CONNECTOR_HOME"] == str(runner_home)
    assert str(plist_path) in out and str(log_dir) in out and str(runner_home) in out
    assert f"launchctl bootout gui/{os.getuid()}/{label};" in out
    assert f"launchctl bootstrap gui/{os.getuid()} {plist_path}" in out
    # setup 도 같은 러너 홈으로 돈다
    setup_line = next(line for line in out.splitlines() if "-m workflow.connector setup" in line)
    assert f'WORKFLOW_CONNECTOR_HOME="{runner_home}"' in setup_line
    # 코드·--env 값은 출력하지 않는다
    assert RUNNER_CODE not in out and ENV_VALUE not in out and "s3cretPW" not in out
    assert "DATABASE_URL" not in raw
    assert not plist_path.exists() and _calls(tmp_path) == []


def test_install_runner_without_name_keeps_old_label_and_home(tmp_path):
    res = _run_runner(tmp_path, DRY_RUN="1")
    assert res.returncode == 0, res.stdout + res.stderr
    plist, _ = _plist_in(res.stdout)
    home = tmp_path / "home"
    assert plist["Label"] == "com.workflow.selfhost.connector"
    assert plist["StandardOutPath"] == str(home / "Library" / "Logs" / "workflow-connector-selfhost" / "stdout.log")
    assert "WORKFLOW_CONNECTOR_HOME" not in plist["EnvironmentVariables"]
    assert str(home / "Library" / "LaunchAgents" / "com.workflow.selfhost.connector.plist") in res.stdout
    assert str(home / "Library" / "Application Support" / "workflow-connector") in res.stdout
    assert "workflow-connector-" not in res.stdout.replace("workflow-connector-selfhost", "")


def test_install_runner_name_respects_explicit_connector_home(tmp_path):
    explicit = tmp_path / "explicit-home"
    res = _run_runner(tmp_path, "--name", "b", DRY_RUN="1", WORKFLOW_CONNECTOR_HOME=str(explicit))
    assert res.returncode == 0, res.stdout + res.stderr
    plist, _ = _plist_in(res.stdout)
    assert plist["EnvironmentVariables"]["WORKFLOW_CONNECTOR_HOME"] == str(explicit)


def test_install_runner_name_loads_only_its_label(tmp_path):
    connector_home = tmp_path / "connector"
    connector_home.mkdir()
    (connector_home / "token.json").write_text("{}", encoding="utf-8")
    res = _run_runner(tmp_path, "--name", "b", WORKFLOW_CONNECTOR_HOME=str(connector_home), SKIP_PIP_INSTALL="1")
    assert res.returncode == 0, res.stdout + res.stderr
    label = "com.workflow.selfhost.connector.b"
    plist_path = tmp_path / "home" / "Library" / "LaunchAgents" / f"{label}.plist"
    assert plistlib.loads(plist_path.read_bytes())["Label"] == label
    assert not (tmp_path / "home" / "Library" / "LaunchAgents" / "com.workflow.selfhost.connector.plist").exists()
    calls = [c for c in _calls(tmp_path) if c.startswith("launchctl")]
    assert calls and all(label in c for c in calls)


@pytest.mark.parametrize("name", ["B", "a_b", "-b", "b-", "a" * 33, ""])
def test_install_runner_rejects_bad_name(tmp_path, name):
    res = _run_runner(tmp_path, "--name", name, DRY_RUN="1")
    assert res.returncode == 2, res.stdout + res.stderr
    assert "--name" in res.stderr and res.stdout == "" and _calls(tmp_path) == []


def test_install_runner_accepts_32_char_name(tmp_path):
    name = "a" + "-b" * 15 + "c"  # 32자 — ARCHITECTURE 규칙 상한
    assert len(name) == 32
    res = _run_runner(tmp_path, "--name", name, DRY_RUN="1")
    assert res.returncode == 0, res.stdout + res.stderr
    assert _plist_in(res.stdout)[0]["Label"] == f"com.workflow.selfhost.connector.{name}"


def test_install_runner_name_needs_value(tmp_path):
    res = _run_runner(tmp_path, "--name", DRY_RUN="1")
    assert res.returncode == 2 and _calls(tmp_path) == []


def test_install_runner_help_mentions_name(tmp_path):
    res = _run_runner(tmp_path, "--help")
    assert "--name" in res.stdout


# --- docs/SELFHOST.md (step 7) ------------------------------------------------------------------
# 문서의 명령이 실제 파일·모듈·CLI 인자와 맞는지 본다. 명령을 실행하지는 않는다 — argparse 로 인자만 확인한다.

SELFHOST_MD = ROOT / "docs" / "SELFHOST.md"
AGENTS_MD = ROOT / "AGENTS.md"
COMPOSE_PREFIX = "docker compose -p runloom -f deploy/selfhost/compose.yaml"


def _selfhost_md() -> str:
    return SELFHOST_MD.read_text(encoding="utf-8")


def _code_lines(text: str) -> list[str]:
    """```bash 블록의 명령 줄 (주석·빈 줄 제외, 줄 끝 주석 제거)."""
    lines, inside = [], False
    for raw in text.splitlines():
        if raw.lstrip().startswith("```"):  # 목록 안 들여쓴 블록 포함
            inside = not inside
            continue
        line = raw.split(" # ")[0].strip()
        if inside and line and not line.startswith("#"):
            lines.append(line)
    return lines


def _args_after(line: str, marker: str) -> list[str]:
    """`marker` 뒤 인자. `<자리 표시>` 는 값 하나로 바꾼다."""
    import shlex

    rest = line.split(marker, 1)[1]
    return shlex.split(re.sub(r"<[^>]+>", "X", rest))


def test_selfhost_md_covers_every_section():
    text = _selfhost_md()
    for heading in (
        "## 요구 사항", "## 설치", "## 로그인", "## 러너 연결", "## GitHub 연결", "## 백업·복원",
        "## 업그레이드", "## 제거", "## 문제 해결", "## 알려진 한계", "## 알림", "### App 권한 올리기",
    ):
        assert heading in text, heading
    for needle in ("Docker Desktop", "Python 3.13", "claude", "codex", "WORKFLOW_GITHUB_REPOS", "초안 PR",
                   "Accept new permissions", "/connect?tab=notify"):
        assert needle in text, needle


def test_selfhost_md_covers_team_upgrade_and_remote_access():
    """phase 15 step 11 — v11 업그레이드(백업 먼저·토큰으로 관리자 계정·다시 로그인), 팀원 초대, 비밀번호 분실,
    공개 주소와 원격 접속(https 필수), 개인 웹훅."""
    text = _selfhost_md()
    assert "## 팀" in text
    upgrade = text[text.index("## 업그레이드"):text.index("## 제거")]
    for needle in ("v11", "backup create", "관리자 계정", "다시 로그인"):
        assert needle in upgrade, needle
    for needle in ("/connect?tab=team", "초대 링크", "재설정 링크", "/login/recover", "WORKFLOW_PUBLIC_URL", "Tailscale",
                   "Cloudflare Tunnel", "https://", "개인 웹훅", "/me"):
        assert needle in text, needle


def test_selfhost_md_covers_two_runners_on_one_mac():
    """phase 17 step 10 — 두 번째 멤버 계정으로 [러너 붙이기] → --name b, 같은 Claude 로그인, 해제 방법."""
    text = _selfhost_md()
    section = text[text.index("### 한 Mac 에 러너 두 대"):text.index("## GitHub 연결")]
    for needle in ("/connect?tab=team", "러너 붙이기", "--name b", "같은 `claude`", "WORKFLOW_CONNECTOR_HOME",
                   "launchctl bootout gui/$(id -u)/com.workflow.selfhost.connector.b",
                   "com.workflow.selfhost.connector.b.plist", "workflow-connector-b"):
        assert needle in section, needle


def test_selfhost_md_paths_exist():
    text = _selfhost_md()
    paths = set(re.findall(r"(?<![\w/.])((?:deploy|src|docs|tests)/[\w./-]+[\w])", text))
    assert "deploy/selfhost/install.sh" in paths and "deploy/selfhost/install-runner.sh" in paths
    generated = {"deploy/selfhost/.env"}  # install.sh 가 만드는 파일
    missing = [p for p in sorted(paths - generated) if not (ROOT / p).exists()]
    assert missing == []


def test_selfhost_md_modules_exist():
    import importlib.util

    modules = set(re.findall(r"-m ([a-z_][\w.]*)", "\n".join(_code_lines(_selfhost_md()))))
    assert {"workflow.connector", "workflow.server.backup"} <= modules
    assert [m for m in sorted(modules) if importlib.util.find_spec(m) is None] == []


def test_selfhost_md_cli_arguments_parse():
    from workflow.connector.cli import build_parser as connector_parser
    from workflow.server.backup import _parser as backup_parser

    seen = set()
    for line in _code_lines(_selfhost_md()):
        for marker, parser in (("-m workflow.connector", connector_parser), ("-m workflow.server.backup", backup_parser)):
            if marker in line:
                args = _args_after(line, marker)
                parser().parse_args(args)  # 인자가 틀리면 SystemExit
                seen.add((marker, args[0]))
    for command in ("connect", "register"):
        assert ("-m workflow.connector", command) in seen, command
    for command in ("create", "list", "restore"):
        assert ("-m workflow.server.backup", command) in seen, command


def test_selfhost_md_compose_commands_use_install_project_and_services():
    compose = _compose()
    lines = [
        line for line in _code_lines(_selfhost_md())
        if line.startswith("docker compose") and line != "docker compose version"  # 요구 사항 확인
    ]
    assert lines
    for line in lines:
        assert line.startswith(COMPOSE_PREFIX), line
        args = line[len(COMPOSE_PREFIX):].split()
        if args and args[0] in ("exec", "run", "logs", "stop", "restart", "cp"):
            services = [a.split(":")[0] for a in args[1:] if not a.startswith("-")]
            assert services and services[0] in compose["services"], line
    # 복원은 서비스를 멈춘 뒤
    text = "\n".join(lines)
    assert text.index(f"{COMPOSE_PREFIX} stop central worker") < text.index("backup restore")


def test_selfhost_md_env_names_are_real():
    known = set(ENV_KEYS) | set(FIXED_ENV) | set(_env_example())
    for script in (SELFHOST / "install.sh", SELFHOST / "install-runner.sh"):
        known |= set(re.findall(r"\b[A-Z][A-Z0-9_]+\b", script.read_text(encoding="utf-8")))
    known |= {"WORKFLOW_CONNECTOR_HOME"}  # connector/config.py
    names = set(re.findall(r"\b(?:WORKFLOW|RUNLOOM|DIAG|OPERATOR|SESSION|HEALTH|SKIP|DRY)_[A-Z0-9_]+\b", _selfhost_md()))
    assert names and sorted(names - known) == []


def test_selfhost_md_warns_that_volume_removal_deletes_data():
    text = _selfhost_md()
    assert f"{COMPOSE_PREFIX} down -v" in text
    assert "runloom_workflow-data" in text
    section = text[text.index("## 제거"):text.index("## 문제 해결")]
    assert "데이터" in section and "삭제" in section


def test_selfhost_md_never_prints_secret_values():
    text = _selfhost_md()
    assert "grep OPERATOR_TOKEN deploy/selfhost/.env" in text
    assert not re.search(r"\b[0-9a-f]{32,}\b", text)
    assert "wfc_" not in text.replace("wfc_…", "")


@pytest.mark.parametrize("doc", ["docs/SELFHOST.md", "docs/README.md", "docs/DEPLOY.md"])
def test_doc_relative_links_resolve(doc):
    path = ROOT / doc
    text = path.read_text(encoding="utf-8")
    targets = re.findall(r"\]\(([^)\s]+)\)", text)
    broken = []
    for target in targets:
        if re.match(r"[a-z]+://|mailto:|#", target):
            continue
        if not (path.parent / target.split("#")[0]).exists():
            broken.append(target)
    assert broken == []


def test_agents_md_commands_include_selfhost_install_and_backup():
    text = AGENTS_MD.read_text(encoding="utf-8")
    section = text[text.index("## 명령어"):text.index("## 하네스")]
    assert "deploy/selfhost/install.sh" in section
    assert "deploy/selfhost/install-runner.sh" in section
    assert f"{COMPOSE_PREFIX} exec central python3 -m workflow.server.backup create" in section


def test_docs_index_and_public_demo_runbook_point_at_selfhost():
    assert "](SELFHOST.md)" in (ROOT / "docs" / "README.md").read_text(encoding="utf-8")
    head = (ROOT / "docs" / "DEPLOY.md").read_text(encoding="utf-8").splitlines()[:3]
    assert any("공개 데모 VM 런북" in line and "SELFHOST.md" in line for line in head)
