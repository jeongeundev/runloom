"""deploy/ 설정 파일과 docs/DEPLOY.md 런북 (Step 16, phase 5 step 10) — VM·도메인 없이 파일 내용만 검사한다.

검사 기준: AGENTS.md 명령어 절의 모듈 경로, settings 모듈의 ENV_KEYS, connector/config.py 가 읽는 키,
seed_demo·local_stack 의 등록 인자, ADR-0006(VM + Caddy, 진단 API 비노출, 컨테이너 없음),
ADR-0008(공개 데모는 VM 한 대에서 대본 에이전트 — systemd 5개, deploy/bin 래퍼, 실제 Codex/Claude·OpenAI 키 없음),
ARCHITECTURE 재시작·백업·보존.
"""

import plistlib
import re
import subprocess
import sys
from pathlib import Path

import pytest

from diagnostic_demo.settings import ENV_KEYS as DIAG_ENV_KEYS
from workflow.connector.config import connector_paths
from workflow.connector.masking import ENV_ALLOWLIST
from workflow.scripted._common import PACE_ENV
from workflow.server.settings import ENV_KEYS as CENTRAL_ENV_KEYS
from workflow.server.settings import SECRET_KEYS, Limits

ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "deploy"
DEPLOY_MD = ROOT / "docs" / "DEPLOY.md"
AGENTS_MD = ROOT / "AGENTS.md"
ARCHITECTURE_MD = ROOT / "docs" / "ARCHITECTURE.md"
ADR_0008 = ROOT / "docs" / "adr" / "0008-public-demo-scripted-agents.md"

sys.path.insert(0, str(ROOT / "scripts"))
import local_stack  # noqa: E402
import seed_demo  # noqa: E402

# 유닛 파일 → AGENTS.md 명령어 절의 모듈 경로, 쓰는 env 파일
SERVICES = {
    "workflow-central.service": ("workflow.server.app:app", "central.env"),
    "workflow-worker.service": ("workflow.server.worker", "central.env"),
    "workflow-diag.service": ("diagnostic_demo.api.app:app", "diag.env"),
    "workflow-diag-worker.service": ("diagnostic_demo.worker", "diag.env"),
    "workflow-connector.service": ("workflow.connector", "connector.env"),
}
CONNECTOR_HOME = "/var/lib/workflow/connector"
SCRIPTED_WRAPPERS = ("codex", "claude")  # deploy/bin/ — PATH 앞에서 실제 도구 이름을 가로챈다 (ADR-0008)


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


def test_service_modules_are_the_ones_in_agents_md_commands():
    """AGENTS.md 명령어 절이 유일한 실행 방법 목록이다 — 유닛 5개의 모듈 경로가 거기 있어야 한다."""
    text = AGENTS_MD.read_text(encoding="utf-8")
    block = text[text.index("## 명령어"):]
    block = block[:block.index("## ", 5)]
    for module, _ in SERVICES.values():
        assert f"-m {module}" in block or f"-m uvicorn {module}" in block, module


def test_connector_service_runs_scripted_wrappers_first_on_path_after_central():
    """공개 데모의 연결 프로그램은 VM 에서 systemd 로 돈다 (ADR-0008). PATH 앞의 deploy/bin 이 codex·claude 를 가로채고,
    그다음 venv 가 와야 검증 프로필(`vp-pytest=python3 -m pytest -q`)의 python3 가 pytest 가 있는 venv 를 찾는다."""
    unit = _unit("workflow-connector.service")
    assert unit["ExecStart"] == ["/opt/workflow/venv/bin/python -m workflow.connector run"]
    paths = [v for v in unit["Environment"] if v.startswith("PATH=")]
    assert len(paths) == 1
    entries = paths[0].removeprefix("PATH=").split(":")
    assert entries[0] == "/opt/workflow/deploy/bin"
    assert entries[1] == "/opt/workflow/venv/bin"
    assert "/usr/bin" in entries
    assert "workflow-central.service" in unit["After"][0]
    # 토큰·속도 외의 값은 env 파일에서 온다. 비밀값을 유닛에 직접 적지 않는다
    assert not [v for v in unit["Environment"] if v.startswith(("WORKFLOW_TOKEN", "OPENAI", "wfc_"))]


@pytest.mark.parametrize("name", SCRIPTED_WRAPPERS)
def test_scripted_wrapper_execs_the_scripted_module_with_fixed_interpreter(name):
    """2줄 래퍼: 어댑터가 만든 인자 배열을 그대로 대본 에이전트에 넘긴다. 실제 도구를 부르지 않는다."""
    path = DEPLOY / "bin" / name
    subprocess.run(["bash", "-n", str(path)], check=True)
    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines[0].startswith("#!")
    assert lines[1] == f'exec /opt/workflow/venv/bin/python -m workflow.scripted.{name} "$@"'
    assert len(lines) == 2
    assert path.stat().st_mode & 0o111, f"deploy/bin/{name} 는 실행 권한이 있어야 한다"


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


def test_connector_env_example_keys_are_what_the_connector_reads():
    """connector/config.py 는 WORKFLOW_CONNECTOR_HOME 만 읽고, 대본 속도는 masking 허용 목록을 거쳐 래퍼 프로세스에 닿는다.
    연결 토큰은 그 디렉터리의 token.json (0600) 이지 env 값이 아니다."""
    env = _env_example("connector.env.example")
    assert set(env) == {"WORKFLOW_CONNECTOR_HOME", PACE_ENV}
    paths = connector_paths(env)
    assert paths.home == Path(CONNECTOR_HOME)
    assert paths.token_file == Path(CONNECTOR_HOME) / "token.json"
    assert env[PACE_ENV] == "25"
    assert PACE_ENV in ENV_ALLOWLIST
    text = (DEPLOY / "env" / "connector.env.example").read_text(encoding="utf-8")
    assert "대본" in text and "실제 Codex" in text
    for key in (*SECRET_KEYS, "DIAG_API_TOKEN", "OPENAI_API_KEY", "WORKFLOW_TOKEN"):
        assert key not in env


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


def test_backup_script_covers_three_dbs_and_artifacts_and_keeps_7_days():
    text = (DEPLOY / "backup.sh").read_text(encoding="utf-8")
    assert ".backup" in text and text.count('backup_db "$') == 3
    assert "/var/lib/workflow/central/db.sqlite" in text
    assert "/var/lib/workflow/diag/db.sqlite" in text
    assert f"${{WORKFLOW_CONNECTOR_HOME:-{CONNECTOR_HOME}}}/state.sqlite" in text
    assert 'backup_db "$CONNECTOR_DB"' in text
    # 연결 토큰 파일은 백업하지 않는다 — 재연결로 대체 (주석에서만 언급)
    code_lines = [line for line in text.splitlines() if not line.lstrip().startswith("#")]
    assert not [line for line in code_lines if "token.json" in line]
    assert "/var/backups/workflow" in text
    assert "tar" in text
    assert "-mtime +7" in text


def test_update_script_pulls_restarts_and_checks_without_touching_env():
    text = (DEPLOY / "update-vm.sh").read_text(encoding="utf-8")
    assert "pull -q --ff-only origin" in text and "/venv/bin/pip\" install -q -e" in text
    assert ("systemctl restart workflow-diag workflow-diag-worker workflow-central workflow-worker "
            "workflow-connector") in text
    assert "curl" in text and "127.0.0.1:8000" in text
    assert "/etc/workflow" not in text and "openssl" not in text


def test_update_script_moves_data_only_behind_explicit_reset_flag():
    """스키마가 바뀐 배포만 WORKFLOW_RESET_DB=1 로 DB 를 초기화한다. 플래그 없이는 데이터 디렉터리를 건드리지 않고,
    초기화도 삭제가 아니라 /var/backups/workflow/reset-… 로 옮기는 것이다 (심사 중 세션 데이터 보호)."""
    text = (DEPLOY / "update-vm.sh").read_text(encoding="utf-8")
    assert "rm -rf" not in text and "rm -r" not in text
    assert 'if [ "${WORKFLOW_RESET_DB:-}" = "1" ]; then' in text
    start = text.index("reset_data() {")
    end = text.index("\n}\n", start) + 3
    body, outside = text[start:end], text[:start] + text[end:]
    # 옮기는 명령과 데이터 경로 사용은 함수 안에만
    assert "BACKUP_DIR=/var/backups/workflow\n" in text
    assert "mv " in body and '"$BACKUP_DIR/reset-$(date' in body
    for needle in ("central/db.sqlite", "central/artifacts", "diag/", "$RESET_DIR"):
        assert needle in body, needle
    code_outside = [line for line in outside.splitlines() if not line.lstrip().startswith("#")]
    assert not [line for line in code_outside if "mv " in line or "$DATA_DIR/" in line], code_outside
    # 함수 호출은 플래그 분기 안 한 곳뿐
    calls = [i for i, line in enumerate(text.splitlines()) if line.strip() == "reset_data"]
    assert len(calls) == 1
    lines = text.splitlines()
    assert lines[calls[0] - 1].strip() == 'if [ "${WORKFLOW_RESET_DB:-}" = "1" ]; then'
    assert lines[calls[0] + 1].strip() == "fi"
    assert "DB 초기화됨(백업:" in text


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
    assert "systemctl start workflow-connector" not in text


def test_install_script_prepares_the_scripted_connector_on_the_vm():
    """ADR-0008: 연결 프로그램 유닛 enable, 토큰·데모 저장소 디렉터리(workflow 소유), 래퍼 실행 권한, connector.env,
    검증 프로필의 pytest 가 있는 venv([dev]). 실제 codex·claude 설치 절차는 없다."""
    text = (DEPLOY / "install-vm.sh").read_text(encoding="utf-8")
    enable_lines = [line for line in text.splitlines() if "systemctl enable" in line]
    assert any("workflow-connector.service" in line for line in enable_lines)
    assert "DATA_DIR=/var/lib/workflow\n" in text and CONNECTOR_HOME.startswith("/var/lib/workflow/")
    assert '-o workflow -g workflow "$DATA_DIR/connector" "$DATA_DIR/demo"' in text
    assert "deploy/bin" in text and "chmod 755" in text
    assert "for name in central diag connector" in text
    assert 'install -q -e "$APP_DIR[dev]"' in text
    for forbidden in ("npm", "@openai/codex", "claude-code", "codex login"):
        assert forbidden not in text.lower(), forbidden


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
    assert "seed_demo.py" in text and "--print-code" in text and "--scripted" in text
    assert "scaffold_demo_repo.py /var/lib/workflow/demo/demo-report-repo" in text
    assert "install-vm.sh" in text and "update-vm.sh" in text and "WORKFLOW_RESET_DB=1" in text
    # 연결 프로그램은 VM 에서: 토큰 위치는 env 예시와 같은 값, 그다음 systemd
    env = _env_example("connector.env.example")
    assert f"WORKFLOW_CONNECTOR_HOME={env['WORKFLOW_CONNECTOR_HOME']}" in text
    assert "connect --code" in text
    assert "systemctl start workflow-connector" in text
    # register 는 tool 마다 하나 — 인자는 seed_demo 의 등록 ID·저장소 ID, local_stack 의 검증 프로필과 같아야 한다
    for tool, registration_id in seed_demo.LOCAL_REGISTRATION_IDS.items():
        assert f"--id {registration_id} --tool {tool}" in text, tool
    assert f"--repository-id {seed_demo.REPOSITORY_ID}" in text
    for profile in local_stack.VERIFY_PROFILES:
        assert f'--verify "{profile}"' in text
    # 공개 데모는 대본만 — 실제 codex·claude 를 VM 에 설치·로그인하는 절차가 없다 (ADR-0008)
    for forbidden in ("codex login", "codex --version", "npm install", "brew install"):
        assert forbidden not in text, forbidden


def test_runbook_covers_every_step_and_known_limits():
    text = DEPLOY_MD.read_text(encoding="utf-8")
    for needle in (
        "## 5. 데모 저장소와 seed",
        "## 6. VM — 연결 프로그램",
        "## 7. 운영자 예시 실행",
        "## 9. 심사 기간 점검 목록",
        "## 10. 알려진 한계",
        "0008-public-demo-scripted-agents.md",
        "dig +short",
        "systemctl status workflow-",
        "openssl rand -hex 32",
        "chmod 600",
        "WORKFLOW_DOMAIN",
        "curl -I https://",
        "/agents/register",
        "systemctl is-active workflow-central workflow-worker workflow-diag workflow-diag-worker workflow-connector",
        "systemctl list-timers",
        "/budget",
        "journalctl -u workflow-worker",
        "du -sh /var/lib/workflow/demo",
        "DIAG_MODEL=fake",
        "ADR-0006",
    ):
        assert needle in text, needle
    # 미구현 기능을 구현된 것처럼 적지 않는다 (ARCHITECTURE 의 "예시 실행 공개" 는 제안)
    assert "운영자 세션의 업무로 남는다" in text


def test_adr_0008_and_architecture_describe_the_scripted_public_demo():
    adr = ADR_0008.read_text(encoding="utf-8")
    for needle in ("workflow.scripted", "DIAG_MODEL=fake", "ADR-0006", "VERIFICATION_LOG", "시연용 · 대본 재생"):
        assert needle in adr, needle
    arch = ARCHITECTURE_MD.read_text(encoding="utf-8")
    assert "0008-public-demo-scripted-agents.md" in arch
    assert "공개 데모에서는 호출하지 않음" in arch
