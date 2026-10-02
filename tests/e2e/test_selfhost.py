"""셀프호스트 실제 Docker e2e — 설치·재시작 보존·백업 복원 (phase 10 step 8).

커밋된 작업 트리를 임시 디렉터리에 `git clone` 하고, 그 복사본의 `deploy/selfhost/install.sh` 를 격리된 compose 프로젝트
(`RUNLOOM_PROJECT=runloom-e2e-<랜덤>`)·빈 포트(`WORKFLOW_PORT`)로 실행한다. 흐름:

1. install.sh → `/healthz` ok → `.env` 의 `OPERATOR_TOKEN` 으로 첫 설정(`/login/setup`, 관리자 계정) → 종류 A 등록
2. `down`(볼륨 유지) → install.sh 재실행 → 종류 A 가 그대로
3. 컨테이너 안에서 `backup create`·`list` → 종류 B 등록 → `stop central worker` → `run --rm central … restore <이름> --force`
   → `up -d` → 종류 A 는 있고 B 는 없다 (백업 시점)

끝나면(finally) `down -v` 로 **이 테스트가 만든 프로젝트의 컨테이너·볼륨만** 지운다. 프로젝트별 이미지(`workflow-selfhost:runloom-e2e-…`)는
지우지 않는다. `WORKFLOW_DOCKER=1` 일 때만 돈다. 외부 호출 없음 — 네트워크는 127.0.0.1 뿐이다.
"""

import os
import secrets
import shutil
import socket
import subprocess
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import httpx
import pytest

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(os.environ.get("WORKFLOW_DOCKER") != "1", reason="WORKFLOW_DOCKER=1 일 때만"),
]

ROOT = Path(__file__).resolve().parents[2]
HEALTH_TIMEOUT = 180.0
ADMIN = {"email": "operator@example.com", "display_name": "운영자", "password": "e2e-operator-password"}


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _env_value(path: Path, key: str) -> str:
    for line in path.read_text().splitlines():
        if line.startswith(f"{key}="):
            return line.split("=", 1)[1]
    raise AssertionError(f"{key} 없음")


class Selfhost:
    """임시 clone 한 벌과 그 compose 프로젝트. 명령은 모두 `-p <프로젝트>` 로 이 프로젝트만 가리킨다."""

    def __init__(self, workdir: Path):
        self.repo = workdir / "runloom"
        self.project = f"runloom-e2e-{secrets.token_hex(4)}"
        self.port = _free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        self.selfhost = self.repo / "deploy" / "selfhost"
        self.env = {
            **{k: v for k, v in os.environ.items() if not k.startswith(("WORKFLOW_", "RUNLOOM_"))},
            "RUNLOOM_PROJECT": self.project,
            "WORKFLOW_PORT": str(self.port),
            "HEALTH_TIMEOUT": str(int(HEALTH_TIMEOUT)),
        }

    def run(self, *args: str, timeout: float = 600) -> subprocess.CompletedProcess:
        res = subprocess.run(
            list(args), cwd=self.repo, env=self.env, capture_output=True, text=True, timeout=timeout,
        )
        assert res.returncode == 0, f"{args}\nstdout:\n{res.stdout}\nstderr:\n{res.stderr}"
        return res

    def compose(self, *args: str, timeout: float = 600) -> subprocess.CompletedProcess:
        return self.run(
            "docker", "compose", "-p", self.project, "-f", str(self.selfhost / "compose.yaml"), *args, timeout=timeout,
        )

    def install(self) -> subprocess.CompletedProcess:
        return self.run(str(self.selfhost / "install.sh"), timeout=900)

    def wait_healthy(self) -> dict:
        deadline = time.monotonic() + HEALTH_TIMEOUT
        while time.monotonic() < deadline:
            try:
                response = httpx.get(f"{self.base}/healthz", timeout=3)
                if response.status_code == 200:
                    return response.json()
            except httpx.HTTPError:
                pass
            time.sleep(1)
        raise AssertionError(f"{self.base}/healthz 가 {HEALTH_TIMEOUT}초 안에 ok 가 아님")

    @contextmanager
    def login(self) -> Iterator[httpx.Client]:
        token = _env_value(self.selfhost / ".env", "OPERATOR_TOKEN")
        with httpx.Client(base_url=self.base, timeout=10, headers={"Origin": self.base}) as client:
            # 첫 설정 전이면 운영자 토큰으로 관리자 계정을 만들고, 이미 있으면(재설치·복원 뒤) 이메일·비밀번호로
            response = client.post("/login/setup", data={"token": token, **ADMIN}, follow_redirects=False)
            if response.status_code == 409:  # already_set_up
                response = client.post("/login", data={"email": ADMIN["email"], "password": ADMIN["password"]},
                                       follow_redirects=False)
            assert response.status_code == 303, response.text
            yield client

    def kinds_text(self) -> str:
        with self.login() as client:
            response = client.get("/connect?tab=kinds")
            assert response.status_code == 200, response.text
            return response.text


def _register_kind(client: httpx.Client, kind: str) -> None:
    response = client.post("/kinds", data={
        "kind": kind,
        "label": f"셀프호스트 확인 {kind}",
        "capability_code": kind,
        "scope_key": "repository_id",
        "input_kinds": ["diff"],
        "outcomes": "done",
        "instructions": "e2e 확인용 종류",
    }, follow_redirects=False)
    assert response.status_code == 303, response.text


@pytest.fixture
def selfhost(tmp_path):
    if shutil.which("docker") is None or subprocess.run(["docker", "info"], capture_output=True).returncode != 0:
        pytest.fail("WORKFLOW_DOCKER=1 인데 Docker 데몬에 연결할 수 없습니다")
    subprocess.run(
        ["git", "clone", "--quiet", "--no-local", str(ROOT), str(tmp_path / "runloom")], check=True, timeout=120,
    )
    host = Selfhost(tmp_path)
    try:
        yield host
    finally:
        subprocess.run(
            ["docker", "compose", "-p", host.project, "-f", str(host.selfhost / "compose.yaml"),
             "down", "-v", "--remove-orphans"],
            cwd=host.repo, env=host.env, capture_output=True, timeout=300,
        )


def test_install_restart_keeps_data_and_restore_returns_to_backup(selfhost):
    # 1. 설치 → healthz → 로그인 → 종류 A
    installed = selfhost.install()
    assert f"{selfhost.base}/login" in installed.stdout
    env_file = selfhost.selfhost / ".env"
    assert env_file.stat().st_mode & 0o777 == 0o600
    token = _env_value(env_file, "OPERATOR_TOKEN")
    assert len(token) == 64 and token not in installed.stdout + installed.stderr
    health = selfhost.wait_healthy()
    assert health["status"] == "ok" and health["mode"] == "selfhost"

    with selfhost.login() as client:
        assert client.get("/", follow_redirects=False).headers["location"] == "/tasks"
        _register_kind(client, "selfhost_before_restart")
    assert "selfhost_before_restart" in selfhost.kinds_text()

    # 2. down(볼륨 유지) → 재설치 → 그대로
    selfhost.compose("down")
    selfhost.install()
    selfhost.wait_healthy()
    assert "selfhost_before_restart" in selfhost.kinds_text()

    # 3. 백업 → 데이터 추가 → 정지·복원 → 백업 시점
    created = selfhost.compose("exec", "-T", "central", "python3", "-m", "workflow.server.backup", "create")
    listed = selfhost.compose("exec", "-T", "central", "python3", "-m", "workflow.server.backup", "list")
    name = listed.stdout.splitlines()[0].split("\t")[0]
    assert name in created.stdout

    with selfhost.login() as client:
        _register_kind(client, "selfhost_after_backup")
    assert "selfhost_after_backup" in selfhost.kinds_text()

    selfhost.compose("stop", "central", "worker")
    selfhost.compose(
        "run", "--rm", "-T", "central", "python3", "-m", "workflow.server.backup", "restore", name, "--force",
    )
    selfhost.compose("up", "-d")
    selfhost.wait_healthy()
    text = selfhost.kinds_text()
    assert "selfhost_before_restart" in text
    assert "selfhost_after_backup" not in text

    relisted = selfhost.compose("exec", "-T", "central", "python3", "-m", "workflow.server.backup", "list")
    assert any(line.startswith("pre-restore-") for line in relisted.stdout.splitlines())
