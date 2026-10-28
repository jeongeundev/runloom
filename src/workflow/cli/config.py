"""자격 파일·환경변수·서버 주소 규칙 (ARCHITECTURE "CLI — phase 26" config.py).

자격 파일 `~/.config/runloom/credentials.json`(`RUNLOOM_HOME` 이 있으면 `$RUNLOOM_HOME/credentials.json`),
디렉터리 0700·파일 0600. `RUNLOOM_TOKEN`·`RUNLOOM_SERVER` 가 있으면 각각 파일 값보다 먼저다.
"""

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
_LOGIN_HINT = "runloom login --server URL 로 먼저 로그인하세요."


class ConfigError(Exception):
    """메시지는 그대로 stderr 에 쓰는 한국어 한 줄 — `main` 이 exit 2 로 옮긴다."""


@dataclass(frozen=True)
class Credentials:
    server: str
    token: str = field(repr=False)


def credentials_path(env: Mapping[str, str]) -> Path:
    if env.get("RUNLOOM_HOME"):
        return Path(env["RUNLOOM_HOME"]) / "credentials.json"
    home = Path(env["HOME"]) if env.get("HOME") else Path.home()
    return home / ".config" / "runloom" / "credentials.json"


def normalize_server(url: str) -> str:
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if not host or not (parts.scheme == "https" or (parts.scheme == "http" and host in _LOCAL_HOSTS)):
        raise ConfigError(f"서버 주소는 https:// 여야 합니다(http:// 는 localhost·127.0.0.1·::1 만): {url}")
    if parts.path not in ("", "/") or parts.query or parts.fragment or url.endswith(("?", "#")):
        raise ConfigError(f"서버 주소에 경로·쿼리를 붙이지 마세요: {url}")
    return f"{parts.scheme}://{parts.netloc}"


def load(env: Mapping[str, str]) -> Credentials:
    token = env.get("RUNLOOM_TOKEN") or None
    server = env.get("RUNLOOM_SERVER") or None
    if token is None or server is None:
        stored = _read_file(credentials_path(env))
        token = token or stored["token"]
        server = server or stored["server"]
    return Credentials(normalize_server(server), token)


def _read_file(path: Path) -> dict:
    try:
        mode = path.stat().st_mode
    except FileNotFoundError:
        raise ConfigError(_LOGIN_HINT) from None
    if mode & 0o077:
        raise ConfigError(f"자격 파일 권한이 너무 넓습니다: {path} — chmod 600 {path}")
    broken = ConfigError(f"자격 파일을 읽을 수 없습니다: {path} — runloom login --server URL 로 다시 로그인하세요.")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise broken from None
    if not isinstance(data, dict) or not all(isinstance(data.get(key), str) for key in ("server", "token")):
        raise broken
    return data


def save(env: Mapping[str, str], creds: Credentials) -> Path:
    path = credentials_path(env)
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps({"server": creds.server, "token": creds.token}, indent=2) + "\n")
        os.replace(temp, path)
    except BaseException:
        temp.unlink(missing_ok=True)
        raise
    return path


def delete(env: Mapping[str, str]) -> bool:
    try:
        credentials_path(env).unlink()
    except FileNotFoundError:
        return False
    return True
