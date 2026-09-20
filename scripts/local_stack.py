#!/usr/bin/env python3
"""로컬 5-프로세스 기동기 — 클라우드·API 키·실제 Codex 없이 전체 흐름을 한 컴퓨터에서 띄운다.

    python3 scripts/local_stack.py [--workdir DIR] [--central-port 18000] [--diag-port 18100] [--fake-codex PATH] [--scripted]

기동 순서: 데모 저장소 scaffold → 진단 API → 진단 워커(`DIAG_MODEL=fake`) → 중앙 API → seed → 중앙 워커 →
connector connect / register / run. 프로세스는 AGENTS.md 명령어 절의 모듈 경로 그대로 `python3 -m …` 로 띄우고
로그는 `workdir/logs/{name}.log` 에 남긴다. 심사 배포가 아니다 (배포는 Step 16 의 systemd·launchd).

- 비밀값(`SESSION_SECRET`·`OPERATOR_TOKEN`·`DIAG_API_TOKEN`)은 시작마다 무작위로 만들어 두 서비스에 같은 값을 준다.
- 부모 환경의 `WORKFLOW_*`·`DIAG_*`·`OPENAI_*` 는 자식에 물려주지 않는다 (pytest 의 `WORKFLOW_SKIP_APP=1`, 실제 키).
- `fake_codex` 를 주면 그 스크립트를 `codex` 로 감싼 디렉터리를 connector 의 PATH 앞에 둔다. 없으면 PATH 의 실제 codex.
- `scripted` 면 `codex`·`claude` 둘 다 대본 에이전트(`python3 -m workflow.scripted.{codex,claude}`)로 감싼다. 둘 다 주면
  `scripted` 가 우선. 대본 속도 `WORKFLOW_SCRIPT_PACE_SECONDS` 는 부모 환경에 있으면 connector 자식에 그대로 넘긴다
  (기본 0 이라 e2e 속도는 그대로).
- heartbeat 오프라인 판정은 10초로 줄이고 connector heartbeat 는 3초 — e2e 가 연결 끊김을 100초 안에 보기 위해서다.
"""

import argparse
import os
import secrets
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import IO

import httpx

sys.path.insert(0, str(Path(__file__).parent))
from scaffold_demo_repo import scaffold  # noqa: E402
from seed_demo import seed  # noqa: E402

from workflow.scripted._common import PACE_ENV  # noqa: E402
from workflow.server.auth import utc_now  # noqa: E402

SERVICE_NAMES = ("diag_api", "diag_worker", "central_api", "central_worker", "connector")
SECRET_KEYS = ("SESSION_SECRET", "OPERATOR_TOKEN", "DIAG_API_TOKEN")
DROP_PREFIXES = ("WORKFLOW_", "DIAG_", "OPENAI_")
VERIFY_PROFILES = ("vp-pytest=python3 -m pytest -q", "vp-report=python3 -m daily_report {response}")
READY_TIMEOUT_SECONDS = 30.0
STOP_GRACE_SECONDS = 5.0
CONNECTOR_CLAIM_INTERVAL = 2.0
CONNECTOR_HEARTBEAT_INTERVAL = 3.0


@dataclass(frozen=True)
class Service:
    name: str
    argv: list[str]
    env: dict[str, str]


class StackError(RuntimeError):
    """기동 실패. 메시지에 해당 프로세스 로그 꼬리를 붙인다."""


class LocalStack:
    def __init__(
        self,
        workdir: Path,
        *,
        central_port: int = 18000,
        diag_port: int = 18100,
        fake_codex: Path | None,
        scripted: bool = False,
        heartbeat_offline_seconds: int = 10,
    ):
        self.workdir = Path(workdir).resolve()
        self.central_url = f"http://127.0.0.1:{central_port}"
        self.diag_url = f"http://127.0.0.1:{diag_port}"
        self.repo_path = self.workdir / "demo-report-repo"
        self.logs_dir = self.workdir / "logs"
        self.connector_home = self.workdir / "connector"
        self.central_db = self.workdir / "central" / "db.sqlite"
        self.central_artifacts = self.workdir / "central" / "artifacts"
        self.diag_db = self.workdir / "diag" / "db.sqlite"
        self.diag_artifacts = self.workdir / "diag" / "artifacts"
        self._secrets = {key: secrets.token_urlsafe(32) for key in SECRET_KEYS}
        if scripted:
            self.fake_bin: Path | None = self._install_scripted()
        else:
            self.fake_bin = self._install_fake_codex(Path(fake_codex)) if fake_codex else None
        self.services = self._plan(central_port, diag_port, heartbeat_offline_seconds)
        self.base_commit: str | None = None
        self.connect_code: str | None = None
        self._procs: dict[str, subprocess.Popen] = {}
        self._log_files: dict[str, IO[bytes]] = {}

    @property
    def operator_token(self) -> str:
        return self._secrets["OPERATOR_TOKEN"]

    @property
    def diag_api_token(self) -> str:
        return self._secrets["DIAG_API_TOKEN"]

    # --- 계획 (기동 없이도 검사 가능) --------------------------------------------------------

    def _install_fake_codex(self, script: Path) -> Path:
        """`codex` 라는 이름의 실행 파일로 감싼다. 실제 codex 는 connector 의 PATH 에서만 가려진다."""
        bin_dir = self.workdir / "bin"
        bin_dir.mkdir(parents=True, exist_ok=True)
        wrapper = bin_dir / "codex"
        wrapper.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script.resolve()}" "$@"\n')
        wrapper.chmod(0o755)
        return bin_dir

    def _install_scripted(self) -> Path:
        """`codex`·`claude` 를 대본 에이전트(`workflow.scripted.*`)로 감싼다. 실제 도구는 connector 의 PATH 에서만 가려진다."""
        bin_dir = self.workdir / "bin"
        bin_dir.mkdir(parents=True, exist_ok=True)
        for name in ("codex", "claude"):
            wrapper = bin_dir / name
            wrapper.write_text(f'#!/usr/bin/env bash\nexec "{sys.executable}" -m workflow.scripted.{name} "$@"\n')
            wrapper.chmod(0o755)
        return bin_dir

    @staticmethod
    def _base_env() -> dict[str, str]:
        return {
            k: v for k, v in os.environ.items()
            if not k.startswith(DROP_PREFIXES) and k not in SECRET_KEYS
        }

    def _plan(self, central_port: int, diag_port: int, heartbeat_offline_seconds: int) -> dict[str, Service]:
        base = self._base_env()
        py = sys.executable
        central_env = {
            **base,
            "WORKFLOW_DEV": "1",
            "WORKFLOW_DB_PATH": str(self.central_db),
            "WORKFLOW_ARTIFACT_DIR": str(self.central_artifacts),
            "DIAG_API_URL": self.diag_url,
            "WORKFLOW_LIMIT_HEARTBEAT_OFFLINE_SECONDS": str(heartbeat_offline_seconds),
            **self._secrets,
        }
        diag_env = {
            **base,
            "DIAG_DEV": "1",
            "DIAG_MODEL": "fake",
            "DIAG_DB_PATH": str(self.diag_db),
            "DIAG_ARTIFACT_DIR": str(self.diag_artifacts),
            "DIAG_API_TOKEN": self._secrets["DIAG_API_TOKEN"],
        }
        connector_env = {**base, "WORKFLOW_CONNECTOR_HOME": str(self.connector_home)}
        if os.environ.get(PACE_ENV):  # 대본 속도만 통과 — 나머지 WORKFLOW_* 는 위에서 빠졌다
            connector_env[PACE_ENV] = os.environ[PACE_ENV]
        if self.fake_bin is not None:
            connector_env["PATH"] = f"{self.fake_bin}{os.pathsep}{base.get('PATH', '')}"
        services = [
            Service("diag_api", [py, "-m", "uvicorn", "diagnostic_demo.api.app:app",
                                 "--host", "127.0.0.1", "--port", str(diag_port)], diag_env),
            Service("diag_worker", [py, "-m", "diagnostic_demo.worker"], diag_env),
            Service("central_api", [py, "-m", "uvicorn", "workflow.server.app:app",
                                    "--host", "127.0.0.1", "--port", str(central_port)], central_env),
            Service("central_worker", [py, "-m", "workflow.server.worker"], central_env),
            Service("connector", [py, "-m", "workflow.connector", "run", "--adapter", "codex",
                                  "--claim-interval", str(CONNECTOR_CLAIM_INTERVAL),
                                  "--heartbeat-interval", str(CONNECTOR_HEARTBEAT_INTERVAL)], connector_env),
        ]
        return {s.name: s for s in services}

    def connector_bootstrap(self, connect_code: str) -> tuple[list[str], list[str]]:
        """`run` 전에 한 번씩 도는 `connect`·`register` 명령."""
        py = sys.executable
        connect = [py, "-m", "workflow.connector", "connect", "--server", self.central_url, "--code", connect_code]
        register = [py, "-m", "workflow.connector", "register", "--id", "local-demo-report",
                    "--repo", str(self.repo_path), "--repository-id", "demo-report-repo"]
        for profile in VERIFY_PROFILES:
            register += ["--verify", profile]
        return connect, register

    # --- 기동·종료 ---------------------------------------------------------------------------

    def start(self) -> None:
        self.workdir.mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(exist_ok=True)
        self.base_commit = scaffold(self.repo_path, force=True)
        self._spawn("diag_api")
        self._wait_http("diag_api", f"{self.diag_url}/capabilities", {"Authorization": f"Bearer {self.diag_api_token}"})
        self._spawn("diag_worker")
        self._spawn("central_api")
        self._wait_http("central_api", f"{self.central_url}/", {})
        self.connect_code = seed(
            self.central_db, self.central_artifacts, base_commit=self.base_commit, now=utc_now(),
            diag_api_url=self.diag_url,
        )["connect_code"]
        self._spawn("central_worker")
        connect, register = self.connector_bootstrap(self.connect_code)
        self._run_once("connector-connect", connect)
        self._run_once("connector-register", register)
        self._spawn("connector")

    def stop(self) -> None:
        for name in reversed(SERVICE_NAMES):
            self.stop_service(name)

    def stop_service(self, name: str) -> None:
        """terminate → 5초 → kill. 로그 파일을 닫는다. 이미 끝났거나 안 띄운 것은 무시."""
        proc = self._procs.pop(name, None)
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(STOP_GRACE_SECONDS)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        handle = self._log_files.pop(name, None)
        if handle is not None:
            handle.close()

    def running(self, name: str) -> bool:
        proc = self._procs.get(name)
        return proc is not None and proc.poll() is None

    def exited(self) -> list[str]:
        """띄웠는데 스스로 끝난 프로세스 이름 (stop 으로 내린 것은 제외)."""
        return [name for name, proc in self._procs.items() if proc.poll() is not None]

    def __enter__(self) -> "LocalStack":
        try:
            self.start()
        except BaseException:
            self.stop()
            raise
        return self

    def __exit__(self, *exc) -> None:
        self.stop()

    def _spawn(self, name: str) -> None:
        service = self.services[name]
        handle = (self.logs_dir / f"{name}.log").open("ab")
        self._log_files[name] = handle
        self._procs[name] = subprocess.Popen(
            service.argv, env=service.env, cwd=self.workdir, stdin=subprocess.DEVNULL,
            stdout=handle, stderr=subprocess.STDOUT,
        )

    def _run_once(self, name: str, argv: list[str]) -> None:
        """connector 의 `connect`·`register` 같은 1회 명령. 실패하면 출력과 함께 StackError."""
        env = self.services["connector"].env
        completed = subprocess.run(argv, env=env, cwd=self.workdir, capture_output=True, text=True)
        (self.logs_dir / f"{name}.log").write_text(completed.stdout + completed.stderr)
        if completed.returncode != 0:
            raise StackError(f"{name} 실패 (exit {completed.returncode}):\n{completed.stdout}{completed.stderr}")

    def _wait_http(self, name: str, url: str, headers: dict[str, str]) -> None:
        deadline = time.monotonic() + READY_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            if not self.running(name):
                raise StackError(f"{name} 가 기동 중 종료됐습니다:\n{self.log_tails().get(name, '')}")
            try:
                if httpx.get(url, headers=headers, timeout=2.0).status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.25)
        raise StackError(f"{name} 가 {READY_TIMEOUT_SECONDS:.0f}초 안에 {url} 에 응답하지 않았습니다:\n"
                         f"{self.log_tails().get(name, '')}")

    def log_tails(self, lines: int = 60) -> dict[str, str]:
        """`logs/*.log` 의 마지막 줄들. e2e 실패 출력과 StackError 메시지에 쓴다."""
        if not self.logs_dir.is_dir():
            return {}
        tails = {}
        for path in sorted(self.logs_dir.glob("*.log")):
            text = path.read_text(encoding="utf-8", errors="replace")
            tails[path.stem] = "\n".join(text.splitlines()[-lines:])
        return tails


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="로컬에서 중앙·진단·연결 프로그램 5개 프로세스를 띄운다 (심사 배포 아님).")
    parser.add_argument("--workdir", type=Path, default=None, help="DB·산출물·로그·데모 저장소 위치. 기본은 임시 디렉터리")
    parser.add_argument("--central-port", type=int, default=18000)
    parser.add_argument("--diag-port", type=int, default=18100)
    parser.add_argument("--fake-codex", type=Path, default=None,
                        help="이 스크립트를 codex 로 쓴다 (예: tests/e2e/fake_codex.py). 없으면 PATH 의 실제 codex")
    parser.add_argument("--scripted", action="store_true",
                        help="codex·claude 를 대본 에이전트(workflow.scripted)로 쓴다. --fake-codex 보다 우선")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    workdir = args.workdir or Path(tempfile.mkdtemp(prefix="workflow-local-stack-"))
    stack = LocalStack(workdir, central_port=args.central_port, diag_port=args.diag_port, fake_codex=args.fake_codex,
                       scripted=args.scripted)
    try:
        with stack:
            print(f"중앙 웹: {stack.central_url}")
            print(f"진단 API: {stack.diag_url} (localhost 전용, Bearer 필요)")
            print(f"작업 디렉터리: {stack.workdir} (로그 {stack.logs_dir}, 데모 저장소 {stack.repo_path})")
            print(f"운영자 토큰: {stack.operator_token}")
            if args.scripted:
                print("codex·claude: 대본 에이전트 (workflow.scripted)")
            else:
                print(f"codex: {'가짜 ' + str(args.fake_codex) if args.fake_codex else 'PATH 의 실제 codex'}")
            print("Ctrl-C 로 종료", flush=True)
            while True:
                time.sleep(1)
                for name in stack.exited():
                    print(f"경고: {name} 가 종료됐습니다 — {stack.logs_dir / (name + '.log')}", flush=True)
                    stack.stop_service(name)
    except KeyboardInterrupt:
        pass
    except StackError as exc:
        print(f"기동 실패: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
