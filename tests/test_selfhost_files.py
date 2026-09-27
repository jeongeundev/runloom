"""deploy/selfhost/ 의 Docker 이미지·compose·.env.example 과 루트 .dockerignore (phase 10 step 5) — 파일 내용만 검사한다.

검사 기준: ADR-0016, ARCHITECTURE "셀프호스트 — phase 10" 절(이름 고정표·컨테이너 환경변수 고정값),
settings 모듈의 ENV_KEYS. 컨테이너는 띄우지 않는다 — 실제 기동은 step 8. 도커가 있으면 `docker compose config` 만 돌린다.
"""

import re
import shutil
import subprocess
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
