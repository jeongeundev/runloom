"""deploy/ 설정 파일과 docs/DEPLOY.md 런북 (Step 16) — VM·도메인 없이 파일 내용만 검사한다.

검사 기준: AGENTS.md 명령어 절의 모듈 경로, settings 모듈의 ENV_KEYS, seed_demo·local_stack 의 등록 인자,
ADR-0006(systemd 4개 + Caddy, 진단 API 비노출, Mac 은 launchd KeepAlive), ARCHITECTURE 재시작·백업·보존.
"""

import plistlib
import re
import subprocess
import sys
from pathlib import Path

import pytest

from diagnostic_demo.settings import ENV_KEYS as DIAG_ENV_KEYS
from workflow.server.settings import ENV_KEYS as CENTRAL_ENV_KEYS
from workflow.server.settings import SECRET_KEYS, Limits

ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "deploy"
DEPLOY_MD = ROOT / "docs" / "DEPLOY.md"

sys.path.insert(0, str(ROOT / "scripts"))
import local_stack  # noqa: E402
import seed_demo  # noqa: E402

# 유닛 파일 → AGENTS.md 명령어 절의 모듈 경로, 쓰는 env 파일
SERVICES = {
    "workflow-central.service": ("workflow.server.app:app", "central.env"),
    "workflow-worker.service": ("workflow.server.worker", "central.env"),
    "workflow-diag.service": ("diagnostic_demo.api.app:app", "diag.env"),
    "workflow-diag-worker.service": ("diagnostic_demo.worker", "diag.env"),
}


def _unit(name: str) -> dict[str, list[str]]:
    """systemd 유닛의 `Key=Value` 를 모은다. 같은 키가 여러 번이면 순서대로."""
    out: dict[str, list[str]] = {}
    for line in (DEPLOY / "systemd" / name).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith(("#", "[")):
            continue
        key, _, value = line.partition("=")
        out.setdefault(key.strip(), []).append(value.strip())
    return out


def _env_example(name: str) -> dict[str, str]:
    """EnvironmentFile 형식 (`KEY=VALUE`, `#` 주석). 따옴표는 벗긴다."""
    out: dict[str, str] = {}
    for line in (DEPLOY / "env" / name).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        assert "=" in line, f"{name}: KEY=VALUE 형식이 아님: {line!r}"
        key, _, value = line.partition("=")
        out[key.strip()] = value.strip().strip('"')
    return out


# --- systemd ---------------------------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(SERVICES))
def test_service_runs_agents_md_module_with_restart_and_env_file(name):
    module, env_file = SERVICES[name]
    unit = _unit(name)
    exec_start = unit["ExecStart"][0]
    assert module in exec_start
    assert exec_start.startswith("/opt/workflow/venv/bin/python -m ")
    assert unit["Restart"] == ["always"]
    assert unit["EnvironmentFile"] == [f"/etc/workflow/{env_file}"]
    assert unit["User"] == ["workflow"]
    assert unit["WorkingDirectory"] == ["/opt/workflow"]
    assert unit["WantedBy"] == ["multi-user.target"]


def test_http_services_bind_localhost_only():
    """외부 노출은 Caddy 만. 중앙 8000·진단 8100 모두 127.0.0.1 에 묶는다 (ARCHITECTURE 실행 위치 표)."""
    central = _unit("workflow-central.service")["ExecStart"][0]
    diag = _unit("workflow-diag.service")["ExecStart"][0]
    assert "--host 127.0.0.1 --port 8000" in central
    assert "--host 127.0.0.1 --port 8100" in diag
    assert "--reload" not in central and "--reload" not in diag


def test_backup_timer_runs_daily_at_0300_as_workflow_user():
    timer = _unit("workflow-backup.timer")
    assert timer["OnCalendar"] == ["*-*-* 03:00:00"]
    assert timer["Persistent"] == ["true"]
    assert timer["WantedBy"] == ["timers.target"]
    service = _unit("workflow-backup.service")
    assert service["Type"] == ["oneshot"]
    assert service["ExecStart"] == ["/opt/workflow/deploy/backup.sh"]
    assert service["User"] == ["workflow"]
    # 백업은 경로만 필요하다 — 비밀값이 든 env 파일을 읽지 않는다
    assert "EnvironmentFile" not in service


# --- Caddy ---------------------------------------------------------------------------------


def test_caddyfile_proxies_central_only_and_never_exposes_diag_api():
    text = (DEPLOY / "Caddyfile").read_text(encoding="utf-8")
    assert "8100" not in text
    assert "reverse_proxy 127.0.0.1:8000" in text
    assert "{$WORKFLOW_DOMAIN}" in text
    assert len(re.findall(r"reverse_proxy", text)) == 1


# --- 환경변수 예시 -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name, expected_keys",
    [("central.env.example", CENTRAL_ENV_KEYS), ("diag.env.example", DIAG_ENV_KEYS)],
    ids=["central", "diag"],
)
def test_env_example_keys_match_what_load_settings_reads(name, expected_keys):
    assert set(_env_example(name)) == set(expected_keys)


def test_env_examples_leave_secrets_empty():
    central = _env_example("central.env.example")
    diag = _env_example("diag.env.example")
    for key in SECRET_KEYS:
        assert central[key] == "", f"central {key} 는 비어 있어야 한다"
    assert diag["DIAG_API_TOKEN"] == ""
    assert diag["OPENAI_API_KEY"] == ""


def test_env_examples_point_at_architecture_paths():
    central = _env_example("central.env.example")
    diag = _env_example("diag.env.example")
    assert central["WORKFLOW_DB_PATH"] == "/var/lib/workflow/central/db.sqlite"
    assert central["WORKFLOW_ARTIFACT_DIR"].startswith("/var/lib/workflow/central/")
    assert central["DIAG_API_URL"] == "http://127.0.0.1:8100"
    assert diag["DIAG_DB_PATH"] == "/var/lib/workflow/diag/db.sqlite"
    assert diag["DIAG_ARTIFACT_DIR"].startswith("/var/lib/workflow/diag/")
    assert diag["DIAG_BUDGET_USD"] == "30"


def test_env_examples_run_the_public_demo_on_scripted_diagnosis():
    """공개 데모는 대본 진단(fake) — 비용 0 이라 한도를 올린다 (phase 5 step 7). 코드 기본값(Limits)은 그대로."""
    central = _env_example("central.env.example")
    diag = _env_example("diag.env.example")
    assert diag["DIAG_MODEL"] == "fake"
    assert diag["DIAG_FAKE_TURN_SECONDS"] == "2.5"
    assert diag["OPENAI_API_KEY"] == ""  # ADR-0003: 키·예산 확인 전 유료 호출 금지
    assert central["WORKFLOW_LIMIT_PER_SESSION_DAILY"] == "200"
    assert central["WORKFLOW_LIMIT_GLOBAL_DAILY"] == "5000"
    # 진단 API 의 하루 접수 상한은 횟수로 세므로 fake 에서도 걸린다 — 중앙과 같은 값이어야 중앙 한도가 의미 있다
    assert diag["DIAG_GLOBAL_DAILY"] == central["WORKFLOW_LIMIT_GLOBAL_DAILY"]
    assert (Limits.per_session_daily, Limits.global_daily) == (10, 60)
    text = (DEPLOY / "env" / "central.env.example").read_text(encoding="utf-8")
    assert "DIAG_MODEL=fake" in text and "10/36" in text  # 실제 모델을 켜면 되돌릴 ADR-0003 값


def test_repo_holds_no_secret_looking_values_under_deploy():
    """저장소에 실제 값이 없어야 한다. 접두사 검사는 connector.masking 과 같은 규칙."""
    for path in DEPLOY.rglob("*"):
        if path.is_file():
            text = path.read_text(encoding="utf-8", errors="replace")
            assert not re.search(r"\bwfc_[A-Za-z0-9_-]{8,}", text), path
            assert not re.search(r"\bsk-[A-Za-z0-9_-]{8,}", text), path


# --- 셸 스크립트 -------------------------------------------------------------------------------


@pytest.mark.parametrize("script", ["backup.sh", "install-vm.sh", "update-vm.sh"])
def test_shell_scripts_parse_and_start_strict(script):
    path = DEPLOY / script
    subprocess.run(["bash", "-n", str(path)], check=True)
    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines[0].startswith("#!")
    assert lines[1] == "set -euo pipefail"
    assert path.stat().st_mode & 0o111, f"{script} 는 실행 권한이 있어야 한다"


def test_backup_script_covers_both_dbs_and_artifacts_and_keeps_7_days():
    text = (DEPLOY / "backup.sh").read_text(encoding="utf-8")
    assert text.count(".backup") == 2
    assert "/var/lib/workflow/central/db.sqlite" in text
    assert "/var/lib/workflow/diag/db.sqlite" in text
    assert "/var/backups/workflow" in text
    assert "tar" in text
    assert "-mtime +7" in text


def test_update_script_pulls_restarts_and_checks_without_touching_env():
    text = (DEPLOY / "update-vm.sh").read_text(encoding="utf-8")
    assert "pull -q --ff-only origin" in text and "/venv/bin/pip\" install -q -e" in text
    assert "systemctl restart workflow-diag workflow-diag-worker workflow-central workflow-worker" in text
    assert "curl" in text and "127.0.0.1:8000" in text
    assert "/etc/workflow" not in text and "openssl" not in text


def test_install_script_is_systemd_only_and_never_starts_services_before_env_is_filled():
    text = (DEPLOY / "install-vm.sh").read_text(encoding="utf-8")
    for forbidden in ("docker", "kubectl", "kubernetes", "helm"):
        assert forbidden not in text.lower()
    for unit in (*SERVICES, "workflow-backup.timer"):
        assert unit in text
    assert "useradd" in text and "python3.13" in text and "caddy" in text
    # 데이터·env 디렉터리 0700, env 파일 0600 (ARCHITECTURE 실행 위치 표)
    assert re.search(r"(chmod 700|install -d -m 700)", text) and "chmod 600" in text
    # 유닛은 enable 만 한다. env 파일이 비어 있으면 시작 즉시 실패해 Restart=always 가 반복되기 때문
    assert "enable --now workflow-central" not in text
    assert "systemctl start workflow-central" not in text


# --- launchd ---------------------------------------------------------------------------------


def test_launchd_plist_keeps_connector_alive():
    with (DEPLOY / "launchd" / "com.workflow.connector.plist").open("rb") as handle:
        plist = plistlib.load(handle)
    assert plist["Label"] == "com.workflow.connector"
    assert plist["KeepAlive"] is True
    args = plist["ProgramArguments"]
    assert args[:2] == ["/usr/bin/env", "python3"]
    assert "workflow.connector" in args and args[-1] == "run"
    assert "Library/Logs/workflow-connector" in plist["StandardOutPath"]
    assert "Library/Logs/workflow-connector" in plist["StandardErrorPath"]
    assert "PATH" in plist["EnvironmentVariables"]
    assert "/opt/homebrew/bin" in plist["EnvironmentVariables"]["PATH"]
    assert plist["WorkingDirectory"]
    # 토큰·API 키는 파일·환경변수 어디에도 넣지 않는다
    assert not {k for k in plist["EnvironmentVariables"] if k in ("WORKFLOW_TOKEN", "OPENAI_API_KEY")}


# --- 런북 ------------------------------------------------------------------------------------


def test_runbook_uses_the_same_commands_as_scripts_and_cli():
    text = DEPLOY_MD.read_text(encoding="utf-8")
    assert "seed_demo" in text and "--print-code" in text
    assert "connect --code" in text
    assert "launchctl" in text
    assert "scaffold_demo_repo.py" in text
    assert "install-vm.sh" in text
    # register 인자는 seed_demo 의 등록 ID·저장소 ID·검증 프로필과 같아야 한다 (Step 14 와 동일)
    assert f"--id {seed_demo.LOCAL_REGISTRATION_IDS['codex']}" in text
    assert f"--repository-id {seed_demo.REPOSITORY_ID}" in text
    for profile in local_stack.VERIFY_PROFILES:
        assert f'--verify "{profile}"' in text


def test_runbook_covers_every_step_and_known_limits():
    text = DEPLOY_MD.read_text(encoding="utf-8")
    for needle in (
        "dig +short",
        "systemctl status workflow-",
        "openssl rand -hex 32",
        "chmod 600",
        "WORKFLOW_DOMAIN",
        "curl -I https://",
        "/agents",
        "systemctl list-timers",
        "/budget",
        "journalctl -u workflow-worker",
        "launchctl list",
        "US$30",
    ):
        assert needle in text, needle
    # 미구현 기능을 구현된 것처럼 적지 않는다 (ARCHITECTURE 의 "예시 실행 공개" 는 제안)
    assert "운영자 세션의 업무로 남는다" in text
