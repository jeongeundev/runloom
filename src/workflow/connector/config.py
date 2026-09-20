"""연결 프로그램의 로컬 경로와 연결 토큰 파일 (ARCHITECTURE 배포 절 "연결 프로그램 + Codex" 행).

토큰 `wfc_…` 은 사용자 홈의 0600 파일에만 둔다. 환경변수·로그·산출물·Codex 프로세스 환경에 넣지 않는다.
"""

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ConnectorPaths:
    home: Path
    state_db: Path
    token_file: Path
    log_dir: Path


@dataclass(frozen=True)
class StoredToken:
    connector_id: str
    token: str
    server: str

    def __repr__(self) -> str:  # 토큰 값이 로그·예외에 섞이지 않게
        return f"StoredToken(connector_id={self.connector_id!r}, server={self.server!r})"


def connector_paths(env: Mapping[str, str] = os.environ) -> ConnectorPaths:
    """`WORKFLOW_CONNECTOR_HOME` 또는 `~/Library/Application Support/workflow-connector/`."""
    override = env.get("WORKFLOW_CONNECTOR_HOME")
    if override:
        home = Path(override)
    else:
        base = Path(env["HOME"]) if env.get("HOME") else Path.home()
        home = base / "Library" / "Application Support" / "workflow-connector"
    return ConnectorPaths(
        home=home,
        state_db=home / "state.sqlite",
        token_file=home / "token.json",
        log_dir=home / "logs",
    )


def read_token(paths: ConnectorPaths) -> StoredToken | None:
    if not paths.token_file.is_file():
        return None
    data = json.loads(paths.token_file.read_text(encoding="utf-8"))
    return StoredToken(connector_id=data["connector_id"], token=data["token"], server=data["server"])


def write_token(paths: ConnectorPaths, connector_id: str, token: str, server: str) -> None:
    """디렉터리 0700, 파일 0600. 기존 파일은 덮어쓴다."""
    paths.home.mkdir(parents=True, exist_ok=True)
    os.chmod(paths.home, 0o700)
    payload = json.dumps({"connector_id": connector_id, "token": token, "server": server}, indent=2)
    fd = os.open(paths.token_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(payload + "\n")
    os.chmod(paths.token_file, 0o600)
